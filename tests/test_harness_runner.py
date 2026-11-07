"""Synthetic checks for the runner contract using the local transport."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
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
        self.config.write_text(json.dumps({
            'contract': 'operator-harness/runner@1',
            'environment_id': 'test-local',
            'transport': {'kind': 'local'},
            'execution': {'kind': 'host', 'workdir': str(self.remote)},
            'paths': {'remote_root': str(self.root / 'jobs'),
                      'local_data_dir': str(self.root / 'data')},
            'limits': {'timeout_seconds': 60},
        }))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=120)

    def test_probe_reports_verified_workdir(self):
        result = self.run_cli('probe', '--config', str(self.config), '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        statuses = {check['check']: check['status'] for check in payload['checks']}
        self.assertEqual(statuses['transport'], 'verified')
        self.assertEqual(statuses['workdir'], 'verified')

    def test_exec_success_writes_receipt_and_logs(self):
        script = self.root / 'ok.sh'
        script.write_text('echo hello-from-target\n')
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-ok', '--script', str(script), '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(receipt['workload_exit'], 0)
        self.assertEqual(receipt['ssh_exit'], 0)
        data = self.root / 'data' / 'jobs' / 'job-ok'
        self.assertIn('hello-from-target', (data / 'stdout.log').read_text())
        self.assertTrue((data / 'remote' / 'exit_code').exists())

    def test_exec_propagates_workload_failure(self):
        script = self.root / 'fail.sh'
        script.write_text('echo boom >&2\nexit 7\n')
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-fail', '--script', str(script), '--json')
        self.assertEqual(result.returncode, 1)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['workload_exit'], 7)
        data = self.root / 'data' / 'jobs' / 'job-fail'
        self.assertIn('boom', (data / 'stderr.log').read_text())

    def test_status_does_not_resubmit(self):
        script = self.root / 'ok.sh'
        script.write_text('echo once\n')
        self.run_cli('exec', '--config', str(self.config),
                     '--job', 'job-once', '--script', str(script))
        result = self.run_cli('status', '--config', str(self.config),
                              '--job', 'job-once', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'completed')

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

    def test_lock_contention_is_reported_separately(self):
        lock_dir = self.root / 'locks'
        lock_dir.mkdir()
        (lock_dir / 'job-locked.lock').mkdir()
        config = json.loads(self.config.read_text())
        config['lock'] = {'dir': str(lock_dir), 'wait_seconds': 0}
        self.config.write_text(json.dumps(config))
        script = self.root / 'ok.sh'
        script.write_text('echo should-not-run\n')
        result = self.run_cli('exec', '--config', str(self.config),
                              '--job', 'job-locked', '--script', str(script),
                              '--json')
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'lock_timeout')
        self.assertIsNone(receipt['workload_exit'])

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
