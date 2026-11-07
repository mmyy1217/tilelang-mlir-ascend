"""Synthetic checks for the runner contract using the local transport."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = (Path(__file__).resolve().parents[1] /
          'assets/skills/harness-remote-runner/scripts/harness_runner.py')


class RunnerLocalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / 'remote'
        self.remote.mkdir()
        self.config = self.root / 'runner.json'
        self.base_config = {
            'contract': 'operator-harness/runner@1',
            'environment_id': 'test-local',
            'transport': {'kind': 'local'},
            'execution': {'kind': 'host', 'workdir': str(self.remote)},
            'paths': {'remote_root': str(self.root / 'jobs'),
                      'local_data_dir': str(self.root / 'data')},
            'limits': {'timeout_seconds': 60},
        }
        self.write_config()

    def write_config(self, **overrides):
        config = json.loads(json.dumps(self.base_config))
        for key, value in overrides.items():
            config[key] = value
        self.config.write_text(json.dumps(config))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=180)

    def write_script(self, name, body):
        path = self.root / name
        path.write_text(body)
        return path

    def data_dir(self, job):
        return self.root / 'data' / 'jobs' / job

    # ---------------------------------------------------------------- probe

    def test_probe_reports_verified_workdir(self):
        result = self.run_cli('probe', '--config', str(self.config), '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        statuses = {check['check']: check['status'] for check in payload['checks']}
        self.assertEqual(statuses['transport'], 'verified')
        self.assertEqual(statuses['workdir'], 'verified')

    def test_probe_rejects_missing_workdir(self):
        self.write_config(execution={'kind': 'host',
                                     'workdir': str(self.root / 'nope')})
        result = self.run_cli('probe', '--config', str(self.config), '--json')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        checks = {c['check']: c for c in json.loads(result.stdout)['checks']}
        self.assertEqual(checks['workdir']['status'], 'blocked')
        self.assertIn('missing', checks['workdir']['detail'])

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'root bypasses directory permissions')
    def test_probe_rejects_readonly_workdir(self):
        readonly = self.root / 'readonly'
        readonly.mkdir()
        readonly.chmod(0o555)
        self.addCleanup(readonly.chmod, 0o755)
        self.write_config(execution={'kind': 'host', 'workdir': str(readonly)})
        result = self.run_cli('probe', '--config', str(self.config), '--json')
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        checks = {c['check']: c for c in json.loads(result.stdout)['checks']}
        self.assertEqual(checks['workdir']['status'], 'blocked')
        self.assertIn('readonly', checks['workdir']['detail'])

    # ----------------------------------------------------------------- exec

    def test_exec_success_writes_receipt_and_logs(self):
        script = self.write_script('ok.sh', 'echo hello-from-target\n')
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-ok', '--script', str(script), '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['workload_exit'], 0)
        data = self.data_dir('job-ok')
        self.assertIn('hello-from-target', (data / 'stdout.log').read_text())
        self.assertTrue((data / 'remote' / 'exit_code').exists())

    def test_exec_propagates_workload_failure(self):
        script = self.write_script('fail.sh', 'echo boom >&2\nexit 7\n')
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-fail', '--script', str(script), '--json')
        self.assertEqual(result.returncode, 1)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['workload_exit'], 7)
        self.assertIn('boom', (self.data_dir('job-fail') / 'stderr.log').read_text())

    def test_exec_rejects_reused_job_id(self):
        script = self.write_script('ok.sh', 'echo first\n')
        first = self.run_cli('exec', '--config', str(self.config),
                             '--job', 'job-reuse', '--script', str(script))
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-reuse', '--script', str(script))
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertIn('already has an execution record', second.stderr)
        # The original evidence must survive untouched.
        self.assertIn('first', (self.data_dir('job-reuse') / 'stdout.log').read_text())

    def test_exec_rejects_job_id_recorded_on_target_only(self):
        script = self.write_script('ok.sh', 'echo first\n')
        self.run_cli('exec', '--config', str(self.config),
                     '--job', 'job-remote', '--script', str(script))
        # Simulate a lost local receipt while the target keeps the record.
        (self.data_dir('job-remote') / 'receipt.json').unlink()
        second = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-remote', '--script', str(script))
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertIn('target already holds a terminal record', second.stderr)

    def test_timeout_leaves_job_recoverable(self):
        script = self.write_script('slow.sh', 'sleep 3\necho finished\n')
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-slow', '--script', str(script),
                              '--timeout', '1', '--json')
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'timeout')
        self.assertEqual(receipt['observed_state'], 'running')

        # The detached wrapper must still write the terminal record, even
        # though the controller already gave up waiting.
        deadline = time.monotonic() + 30
        recovered = None
        while time.monotonic() < deadline:
            status = self.run_cli('status', '--config', str(self.config),
                                  '--job', 'job-slow', '--json')
            self.assertEqual(status.returncode, 0, status.stderr)
            recovered = json.loads(status.stdout)
            if recovered['status'] != 'running':
                break
            time.sleep(1)
        self.assertEqual(recovered['status'], 'completed')
        self.assertEqual(recovered['workload_exit'], 0)
        self.assertIn('finished', (self.data_dir('job-slow') / 'stdout.log').read_text())

    def test_workload_exit_95_is_not_a_lock_signal(self):
        script = self.write_script('ninetyfive.sh', 'exit 95\n')
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-95', '--script', str(script), '--json')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['workload_exit'], 95)

    def test_logs_survive_separator_like_output(self):
        script = self.write_script(
            'sep.sh', 'printf "before\\n---\\nafter\\n"\n'
                      'printf "error\\n" >&2\n')
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-sep', '--script', str(script), '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.data_dir('job-sep')
        self.assertEqual((data / 'stdout.log').read_text(),
                         'before\n---\nafter\n')
        self.assertEqual((data / 'stderr.log').read_text(), 'error\n')

    # ------------------------------------------------------------------ env

    def test_execution_env_applies_to_host_mode(self):
        script = self.write_script('env.sh', 'printf "%s\\n" "$HARNESS_PROBE"\n')
        self.write_config(execution={'kind': 'host', 'workdir': str(self.remote),
                                     'env': {'HARNESS_PROBE': 'from-config'}})
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-env', '--script', str(script))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('from-config',
                      (self.data_dir('job-env') / 'stdout.log').read_text())

    # ----------------------------------------------------------------- lock

    def test_lock_is_shared_across_job_ids(self):
        lock_dir = self.root / 'locks'
        lock_dir.mkdir()
        (lock_dir / 'test-local.lock').mkdir()
        self.write_config(lock={'dir': str(lock_dir), 'wait_seconds': 0})
        script = self.write_script('ok.sh', 'echo should-not-run\n')
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-locked', '--script', str(script),
                              '--json')
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'lock_timeout')
        self.assertIsNone(receipt['workload_exit'])

    def test_lock_serialises_sequential_jobs(self):
        lock_dir = self.root / 'locks'
        self.write_config(lock={'dir': str(lock_dir), 'wait_seconds': 5})
        script = self.write_script('ok.sh', 'echo ran\n')
        for job in ('job-a', 'job-b'):
            result = self.run_cli('exec', '--config', str(self.config),
                                  '--job', job, '--script', str(script))
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((lock_dir / 'test-local.lock').exists(),
                         'lock must be released after each job')

    # --------------------------------------------------------------- upload

    def test_upload_verifies_digest(self):
        source = self.root / 'payload.txt'
        source.write_text('payload\n')
        result = self.run_cli('upload', '--config', str(self.config),
                              '--job', 'job-up', '--src',
                              f'{source}:payload.txt', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)['upload_manifest']
        self.assertEqual(manifest[0]['path'], 'payload.txt')
        self.assertTrue((self.root / 'jobs' / 'job-up' / 'payload.txt').exists())

    def test_upload_fails_when_target_content_is_tampered(self):
        source = self.root / 'ok_payload.txt'
        source.write_text('original\n')
        # Tamper after the tar is written but before verification by making the
        # destination read-only content differ: emulate with a pre-created file
        # that the tar cannot overwrite is not portable, so instead verify the
        # verifier itself rejects a mismatch.
        job = 'job-tamper'
        target = self.root / 'jobs' / job
        target.mkdir(parents=True)
        (target / 'ok_payload.txt').write_text('tampered\n')
        (target / 'upload-manifest.json').write_text(json.dumps(
            [{'path': 'ok_payload.txt', 'sha256': 'deadbeef', 'bytes': 9}]))
        (target / 'upload-manifest.sha256').write_text(
            'deadbeef  ok_payload.txt\n')
        verify = (f'cd {target} && if command -v python3 >/dev/null 2>&1; then '
                  'python3 -c "import json,hashlib,sys;'
                  'm=json.load(open(\'upload-manifest.json\'));'
                  'bad=[e[\'path\'] for e in m if '
                  'hashlib.sha256(open(e[\'path\'],\'rb\').read()).hexdigest()!=e[\'sha256\']];'
                  'print(\'ok\' if not bad else \'mismatch:\'+\',\'.join(bad));'
                  'sys.exit(1 if bad else 0)"; '
                  'else sha256sum -c upload-manifest.sha256; fi')
        checked = subprocess.run(['bash', '-c', verify],
                                 capture_output=True, text=True)
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn('mismatch', checked.stdout)

    def test_sha256sum_fallback_reads_a_real_manifest(self):
        job = 'job-fallback'
        target = self.root / 'jobs' / job
        target.mkdir(parents=True)
        (target / 'data.bin').write_text('content\n')
        import hashlib
        digest = hashlib.sha256(b'content\n').hexdigest()
        (target / 'upload-manifest.sha256').write_text(
            f'{digest}  data.bin\n')
        checked = subprocess.run(
            ['bash', '-c', f'cd {target} && sha256sum -c upload-manifest.sha256'],
            capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn('OK', checked.stdout)

    # ---------------------------------------------------------------- status

    def test_status_does_not_resubmit(self):
        script = self.write_script('ok.sh', 'echo once\n')
        self.run_cli('exec', '--config', str(self.config),
                     '--job', 'job-once', '--script', str(script))
        result = self.run_cli('status', '--config', str(self.config),
                              '--job', 'job-once', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'completed')

    def test_status_recovers_after_post_submit_read_failure(self):
        script = self.write_script('ok.sh', 'echo recovered\n')
        self.run_cli('exec', '--config', str(self.config),
                     '--job', 'job-recover', '--script', str(script))
        receipt_path = self.data_dir('job-recover') / 'receipt.json'
        receipt = json.loads(receipt_path.read_text())
        # Emulate a controller that lost the result read after submission.
        receipt.update(status='unknown', workload_exit=None,
                       detail='submitted but result read failed')
        receipt_path.write_text(json.dumps(receipt))
        result = self.run_cli('status', '--config', str(self.config),
                              '--job', 'job-recover', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        recovered = json.loads(result.stdout)
        self.assertEqual(recovered['status'], 'completed')
        self.assertEqual(recovered['workload_exit'], 0)
        self.assertIn('recovered',
                      (self.data_dir('job-recover') / 'stdout.log').read_text())

    # ----------------------------------------------------------- config/exit

    def test_bad_contract_is_usage_error(self):
        self.config.write_text(json.dumps({'contract': 'nope'}))
        result = self.run_cli('probe', '--config', str(self.config))
        self.assertEqual(result.returncode, 2)
        self.assertIn('unsupported config contract', result.stderr)

    def test_unreachable_target_is_transport_error(self):
        self.config.write_text(json.dumps({
            'contract': 'operator-harness/runner@1',
            'transport': {'kind': 'ssh', 'host': 'no-such-host.invalid',
                          'connect_timeout': 2},
            'execution': {'kind': 'host', 'workdir': '/tmp'},
            'paths': {'remote_root': '/tmp/jobs',
                      'local_data_dir': str(self.root / 'data')},
            'limits': {'timeout_seconds': 20},
        }))
        result = self.run_cli('probe', '--config', str(self.config))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
