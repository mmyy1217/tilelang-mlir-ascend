#!/usr/bin/env python3
"""Run bounded operator jobs on a local or remote execution target.

The controller stays on the machine that holds the project `.workspace/`; the
execution target is described by a runner config (see
`references/runner-contract.md`). Only the standard library is used so the
script can be copied next to any project without extra dependencies.

Exit codes are part of the interface:

    0  job completed and the workload exited 0
    1  workload exited non-zero (candidate/build/test failure)
    2  usage or configuration error
    3  transport failure (connection, container, upload/fetch)
    4  timeout
    5  job state unknown (must not be resubmitted blindly)
"""

import argparse
import datetime
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import time
import uuid

CONFIG_CONTRACT = 'operator-harness/runner@1'
RECEIPT_CONTRACT = 'operator-harness/runner-receipt@1'

EXIT_OK = 0
EXIT_WORKLOAD = 1
EXIT_USAGE = 2
EXIT_TRANSPORT = 3
EXIT_TIMEOUT = 4
EXIT_UNKNOWN = 5
EXIT_LOCK = 6

STATUS_COMPLETED = 'completed'
STATUS_FAILED = 'failed'
STATUS_TRANSPORT_ERROR = 'transport_error'
STATUS_TIMEOUT = 'timeout'
STATUS_UNKNOWN = 'unknown'
STATUS_LOCKED = 'lock_timeout'


class RunnerError(Exception):
    """Configuration or usage problem detected before touching the target."""

    exit_code = EXIT_USAGE


class TransportError(Exception):
    """The connection, container, or file transfer failed."""

    exit_code = EXIT_TRANSPORT


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec='seconds')


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

def _require(mapping, key, where):
    if not isinstance(mapping, dict) or key not in mapping:
        raise RunnerError(f'missing {where}.{key} in runner config')
    return mapping[key]


def load_config(path):
    try:
        raw = Path(path).read_text()
    except OSError as error:
        raise RunnerError(f'cannot read config {path}: {error}')
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RunnerError(f'config {path} is not valid JSON: {error}')
    if not isinstance(config, dict):
        raise RunnerError('runner config must be a JSON object')
    contract = config.get('contract')
    if contract != CONFIG_CONTRACT:
        raise RunnerError(
            f'unsupported config contract {contract!r}; expected {CONFIG_CONTRACT}')

    transport = _require(config, 'transport', 'config')
    kind = _require(transport, 'kind', 'transport')
    if kind not in ('local', 'ssh'):
        raise RunnerError(f'unsupported transport.kind {kind!r}')
    if kind == 'ssh':
        _require(transport, 'host', 'transport')

    execution = _require(config, 'execution', 'config')
    exec_kind = _require(execution, 'kind', 'execution')
    if exec_kind not in ('host', 'docker'):
        raise RunnerError(f'unsupported execution.kind {exec_kind!r}')
    if exec_kind == 'docker':
        _require(execution, 'container', 'execution')
    _require(execution, 'workdir', 'execution')

    paths = _require(config, 'paths', 'config')
    _require(paths, 'remote_root', 'paths')

    limits = config.setdefault('limits', {})
    limits.setdefault('timeout_seconds', 600)
    limits.setdefault('max_upload_mb', 256)

    lock = config.setdefault('lock', {})
    lock.setdefault('wait_seconds', 0)

    return config


def local_data_dir(config):
    raw = config['paths'].get('local_data_dir') or '.harness-runner'
    return Path(raw).expanduser()


def job_dir(config, job_id):
    return local_data_dir(config) / 'jobs' / job_id


def remote_job_dir(config, job_id):
    root = config['paths']['remote_root'].rstrip('/')
    return f'{root}/{job_id}'


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------

def _ssh_argv(config, remote_command):
    transport = config['transport']
    argv = ['ssh', '-o', f"ConnectTimeout={int(transport.get('connect_timeout', 20))}",
            '-o', 'BatchMode=yes']
    for option in transport.get('ssh_options', []):
        argv.extend(['-o', str(option)])
    argv.append(transport['host'])
    argv.append(remote_command)
    return argv


def _target_command(config, inner):
    """Wrap `inner` so it runs inside the configured host/docker target."""
    execution = config['execution']
    if execution['kind'] == 'docker':
        container = execution['container']
        argv = ['docker', 'exec', '-i']
        for name, value in execution.get('env', {}).items():
            argv.extend(['-e', f'{name}={value}'])
        argv.extend([container, 'bash', '-c', inner])
        return ' '.join(shlex.quote(part) for part in argv)
    return f'bash -c {shlex.quote(inner)}'


def run_transport(config, inner, *, stdin=None, timeout=None, binary_stdin=False):
    """Execute `inner` on the target; return (returncode, stdout, stderr)."""
    transport = config['transport']
    timeout = timeout or config['limits']['timeout_seconds']
    if transport['kind'] == 'ssh':
        argv = _ssh_argv(config, _target_command(config, inner))
        payload = stdin
        if binary_stdin and stdin is not None:
            payload = stdin
        try:
            result = subprocess.run(
                argv, input=payload, capture_output=True, timeout=timeout,
                text=not binary_stdin)
        except subprocess.TimeoutExpired:
            raise TimeoutError('transport command timed out')
        except OSError as error:
            raise TransportError(f'cannot start ssh: {error}')
        return result.returncode, result.stdout, result.stderr

    # local transport: run the same wrapper through the local shell
    command = _target_command(config, inner)
    try:
        result = subprocess.run(
            ['bash', '-c', command], input=stdin, capture_output=True,
            timeout=timeout, text=not binary_stdin)
    except subprocess.TimeoutExpired:
        raise TimeoutError('local command timed out')
    except OSError as error:
        raise TransportError(f'cannot start local shell: {error}')
    return result.returncode, result.stdout, result.stderr


def check_transport(config):
    """Fail fast when the target is unreachable or the container is absent."""
    inner = 'printf ok'
    code, out, err = run_transport(config, inner, timeout=60)
    if code != 0 or 'ok' not in (out or ''):
        detail = (err or out or '').strip()[:400]
        raise TransportError(f'target unreachable (rc={code}): {detail}')


def _as_text(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', 'replace')
    return value or ''


# --------------------------------------------------------------------------
# upload / fetch
# --------------------------------------------------------------------------

def _tar_bytes(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, path in entries:
            archive.add(str(path), arcname=name)
    return buffer.getvalue()


def upload_tree(config, job_id, sources):
    """Upload {relative_destination: local_path} and return the manifest."""
    manifest = []
    entries = []
    for destination, source in sources.items():
        source = Path(source)
        if not source.exists():
            raise RunnerError(f'upload source missing: {source}')
        size = source.stat().st_size
        if size > config['limits']['max_upload_mb'] * 1024 * 1024:
            raise RunnerError(
                f'upload source exceeds max_upload_mb: {source} ({size} bytes)')
        manifest.append({'path': destination, 'sha256': sha256_file(source),
                         'bytes': size})
        entries.append((destination, source))

    payload = _tar_bytes(entries)
    destination = shlex.quote(remote_job_dir(config, job_id))
    inner = f'mkdir -p {destination} && tar -x -C {destination} -f -'
    code, out, err = run_transport(
        config, inner, stdin=payload, binary_stdin=True,
        timeout=max(120, config['limits']['timeout_seconds']))
    if code != 0:
        raise TransportError(
            f'upload failed (rc={code}): {_as_text(err).strip()[:400]}')

    # write the manifest next to the uploaded files and re-check the digests
    manifest_bytes = json.dumps(manifest, indent=2).encode()
    inner = f'cat > {destination}/upload-manifest.json'
    code, out, err = run_transport(
        config, inner, stdin=manifest_bytes, binary_stdin=True)
    if code != 0:
        raise TransportError(
            f'manifest upload failed (rc={code}): {_as_text(err).strip()[:400]}')
    # Prefer python3 (parses the manifest); fall back to sha256sum so a target
    # without python3 can still verify an upload.
    checks = ' && '.join(
        f'echo "{item["sha256"]}  {item["path"]}"' for item in manifest)
    verify = (f'cd {destination} && if command -v python3 >/dev/null 2>&1; then '
              'python3 -c "import json,hashlib;'
              'm=json.load(open(\'upload-manifest.json\'));'
              'bad=[e[\'path\'] for e in m if '
              'hashlib.sha256(open(e[\'path\'],\'rb\').read()).hexdigest()!=e[\'sha256\']];'
              'print(\'ok\' if not bad else \'mismatch:\'+\',\'.join(bad))"; '
              f'else printf \'%s\\n\' {shlex.quote(checks)} | sha256sum -c -; fi')
    code, out, err = run_transport(config, verify)
    if code != 0 or 'ok' not in _as_text(out).lower():
        raise TransportError(
            f'upload digest check failed (rc={code}): {_as_text(out).strip()[:400]}')
    return manifest


def fetch_tree(config, job_id, destination):
    """Fetch the whole remote job directory into `destination`."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    job = shlex.quote(remote_job_dir(config, job_id))
    inner = f'tar -c -C {job} -f - .'
    code, out, err = run_transport(
        config, inner, binary_stdin=True,
        timeout=max(120, config['limits']['timeout_seconds']))
    if code != 0:
        raise TransportError(
            f'fetch failed (rc={code}): {_as_text(err).strip()[:400]}')
    payload = out if isinstance(out, bytes) else (out or '').encode()
    if not payload:
        raise TransportError('fetch returned no data')
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r') as archive:
        try:
            archive.extractall(destination, filter='data')
        except TypeError:  # Python < 3.12
            archive.extractall(destination)
    return destination


# --------------------------------------------------------------------------
# job execution
# --------------------------------------------------------------------------

def _interpreter(config, script):
    """Pick the interpreter for the uploaded script.

    `execution.interpreter` in the config wins; otherwise the suffix decides.
    Unknown suffixes run through bash, matching the shell-oriented workflow.
    """
    configured = config['execution'].get('interpreter')
    if configured:
        return configured
    suffix = Path(script).suffix.lower()
    if suffix == '.py':
        return 'python3'
    return 'bash'


def _wrapper_script(config, job_id, command_name, *, use_lock, interpreter='bash'):
    execution = config['execution']
    workdir = execution['workdir']
    job = remote_job_dir(config, job_id)
    lock = config.get('lock') or {}
    lock_dir = lock.get('dir')
    lines = [
        'set -u',
        f'JOB_DIR={shlex.quote(job)}',
        f'WORKDIR={shlex.quote(workdir)}',
        'mkdir -p "$JOB_DIR" || exit 96',
        'printf "%s\\n" "$$" > "$JOB_DIR/pid"',
        'printf "%s\\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$JOB_DIR/started_at"',
    ]
    if use_lock and lock_dir:
        lock_path = f'{lock_dir.rstrip("/")}/{job_id}.lock'
        lines.extend([
            f'LOCK={shlex.quote(lock_path)}',
            'mkdir -p "$(dirname "$LOCK")" || exit 96',
            'waited=0',
            f'while ! mkdir "$LOCK" 2>/dev/null; do',
            '  if [ "$waited" -ge ' + str(int(lock.get('wait_seconds', 0))) + ' ]; then',
            '    printf "%s\\n" "lock_timeout" > "$JOB_DIR/lock_state"',
            '    exit 95',
            '  fi',
            '  sleep 1; waited=$((waited+1))',
            'done',
            'printf "%s\\n" "acquired" > "$JOB_DIR/lock_state"',
            'trap \'rmdir "$LOCK" 2>/dev/null || true\' EXIT',
        ])
    lines.extend([
        'cd "$WORKDIR" || { printf "%s\\n" "97" > "$JOB_DIR/exit_code"; exit 97; }',
        f'{interpreter} "$JOB_DIR/{command_name}" > "$JOB_DIR/stdout.log" 2> "$JOB_DIR/stderr.log"',
        'rc=$?',
        'printf "%s\\n" "$rc" > "$JOB_DIR/exit_code"',
        'printf "%s\\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$JOB_DIR/finished_at"',
        'exit "$rc"',
    ])
    return '\n'.join(lines) + '\n'


def read_remote_exit(config, job_id):
    """Return (workload_exit, stdout, stderr) or None when unavailable."""
    job = remote_job_dir(config, job_id)
    inner = (f'cd {shlex.quote(job)} 2>/dev/null || exit 98; '
             'cat exit_code 2>/dev/null; echo "---"; '
             'tail -c 400000 stdout.log 2>/dev/null; echo "---"; '
             'tail -c 400000 stderr.log 2>/dev/null')
    code, out, err = run_transport(config, inner, timeout=120)
    text = _as_text(out)
    if code != 0:
        return None
    parts = text.split('---\n', 2)
    if len(parts) < 3:
        return None
    exit_text = parts[0].strip()
    if not exit_text.isdigit():
        return None
    return int(exit_text), parts[1], parts[2]


def write_receipt(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + '\n')


def read_receipt(config, job_id):
    path = job_dir(config, job_id) / 'receipt.json'
    if not path.exists():
        raise RunnerError(f'no receipt for job {job_id} at {path}')
    return json.loads(path.read_text())


def cmd_probe(args):
    config = load_config(args.config)
    checks = []
    overall = EXIT_OK

    def record(name, ok, detail):
        nonlocal overall
        checks.append({'check': name, 'status': 'verified' if ok else 'blocked',
                       'detail': detail})
        if not ok:
            overall = EXIT_TRANSPORT

    try:
        check_transport(config)
        record('transport', True, f"{config['transport']['kind']} reachable")
    except (TransportError, TimeoutError) as error:
        record('transport', False, str(error))
        return _emit(args, {'contract': CONFIG_CONTRACT, 'checks': checks},
                     overall)

    execution = config['execution']
    workdir = execution['workdir']
    inner = ('printf "arch=%s\\n" "$(uname -m)"; '
             'printf "python=%s\\n" "$(command -v python3 || echo missing)"; '
             f'cd {shlex.quote(workdir)} 2>/dev/null && '
             'printf "workdir=writable\\n" && touch .runner-probe && '
             'rm -f .runner-probe || printf "workdir=missing\\n"')
    code, out, err = run_transport(config, inner)
    text = _as_text(out)
    record('workdir', 'workdir=writable' in text,
           f'{workdir}: ' + ('writable' if 'workdir=writable' in text
                             else _as_text(err).strip()[:200] or 'missing'))
    record('python3', 'python=missing' not in text,
           next((line for line in text.splitlines()
                 if line.startswith('python=')), 'python=unknown'))
    record('arch', True,
           next((line for line in text.splitlines()
                 if line.startswith('arch=')), 'arch=unknown'))

    expected_arch = (config.get('identity') or {}).get('expected_arch')
    if expected_arch:
        observed = next((line.split('=', 1)[1] for line in text.splitlines()
                         if line.startswith('arch=')), '')
        record('arch_expected', observed == expected_arch,
               f'expected {expected_arch}, observed {observed or "unknown"}')

    if execution['kind'] == 'docker':
        record('container', True, f"docker exec {execution['container']}")

    return _emit(args, {'contract': CONFIG_CONTRACT, 'checks': checks}, overall)


def cmd_upload(args):
    config = load_config(args.config)
    sources = {}
    for item in args.src:
        if ':' not in item:
            raise RunnerError(f'upload source must be LOCAL:DEST, got {item!r}')
        local, destination = item.split(':', 1)
        sources[destination] = local
    check_transport(config)
    manifest = upload_tree(config, args.job, sources)
    return _emit(args, {'job': args.job, 'upload_manifest': manifest}, EXIT_OK)


def cmd_exec(args):
    config = load_config(args.config)
    script = Path(args.script)
    if not script.is_file():
        raise RunnerError(f'script not found: {script}')
    job = args.job
    directory = job_dir(config, job)
    directory.mkdir(parents=True, exist_ok=True)

    receipt = {
        'contract': RECEIPT_CONTRACT,
        'job_id': job,
        'environment_id': config.get('environment_id'),
        'created_at': now(),
        'request': {'script': str(script), 'sha256': sha256_file(script),
                    'timeout_seconds': args.timeout or
                    config['limits']['timeout_seconds']},
        'transport': {'kind': config['transport']['kind'],
                      'host': config['transport'].get('host')},
        'execution': {key: config['execution'].get(key)
                      for key in ('kind', 'container', 'workdir')},
        'status': STATUS_UNKNOWN,
        'ssh_exit': None,
        'workload_exit': None,
    }
    receipt_path = directory / 'receipt.json'
    write_receipt(receipt_path, receipt)

    try:
        check_transport(config)
        interpreter = _interpreter(config, script)
        command_name = 'command.py' if interpreter.startswith('python') else 'command.sh'
        upload_tree(config, job,
                    {command_name: script,
                     'request.json': _write_request(directory, receipt)})
        wrapper = _wrapper_script(config, job, command_name,
                                  use_lock=not args.no_lock,
                                  interpreter=interpreter)
        timeout = args.timeout or config['limits']['timeout_seconds']
        try:
            code, out, err = run_transport(config, wrapper, timeout=timeout)
        except TimeoutError:
            # The controller gave up; the workload may still be running.
            # Record what the target reports instead of assuming it stopped.
            observed = _observe_state(config, job)
            receipt.update(status=STATUS_TIMEOUT, finished_at=now(),
                           timeout_seconds=timeout, observed_state=observed)
            if observed in ('running', 'unknown'):
                receipt['detail'] = ('workload may still be running; '
                                     'check with `status` before resubmitting')
            write_receipt(receipt_path, receipt)
            _save_logs(directory, '', f'timeout after {timeout}s')
            return _emit(args, receipt, EXIT_TIMEOUT)

        receipt['ssh_exit'] = code
        if code == 95:
            # The wrapper exits 95 when the shared lock was not acquired; the
            # workload never started, so this is not a workload failure.
            receipt.update(status=STATUS_LOCKED, finished_at=now(),
                           detail='shared lock not acquired; workload not started')
            write_receipt(receipt_path, receipt)
            return _emit(args, receipt, EXIT_LOCK)
        remote = read_remote_exit(config, job)
        if remote is None:
            receipt.update(status=STATUS_TRANSPORT_ERROR, finished_at=now(),
                           detail=(_as_text(err) or _as_text(out)).strip()[:400])
            write_receipt(receipt_path, receipt)
            _save_logs(directory, _as_text(out), _as_text(err))
            return _emit(args, receipt, EXIT_TRANSPORT)

        workload_exit, stdout, stderr = remote
        receipt['workload_exit'] = workload_exit
        receipt['finished_at'] = now()
        receipt['status'] = (STATUS_COMPLETED if workload_exit == 0
                             else STATUS_FAILED)
        write_receipt(receipt_path, receipt)
        _save_logs(directory, stdout, stderr)
        try:
            fetch_tree(config, job, directory / 'remote')
            receipt['fetched'] = True
            write_receipt(receipt_path, receipt)
        except TransportError as error:
            receipt['fetched'] = False
            receipt['fetch_error'] = str(error)
            write_receipt(receipt_path, receipt)
        return _emit(args, receipt,
                     EXIT_OK if workload_exit == 0 else EXIT_WORKLOAD)
    except (TransportError, TimeoutError) as error:
        receipt.update(status=STATUS_TRANSPORT_ERROR, finished_at=now(),
                       detail=str(error))
        write_receipt(receipt_path, receipt)
        return _emit(args, receipt, EXIT_TRANSPORT)


def _write_request(directory, receipt):
    path = directory / 'request.json'
    path.write_text(json.dumps(receipt, indent=2) + '\n')
    return path


def _save_logs(directory, stdout, stderr):
    (directory / 'stdout.log').write_text(_as_text(stdout))
    (directory / 'stderr.log').write_text(_as_text(stderr))


def _observe_state(config, job_id):
    """Return running / finished / absent / unknown without resubmitting."""
    job = shlex.quote(remote_job_dir(config, job_id))
    inner = (f'cd {job} 2>/dev/null || {{ echo absent; exit 0; }}; '
             'if [ -f exit_code ]; then echo "finished:$(cat exit_code)"; '
             'elif [ -f pid ] && kill -0 "$(cat pid)" 2>/dev/null; then '
             'echo running; else echo unknown; fi')
    try:
        code, out, err = run_transport(config, inner, timeout=60)
    except (TransportError, TimeoutError):
        return 'unknown'
    if code != 0:
        return 'unknown'
    return _as_text(out).strip() or 'unknown'


def cmd_fetch(args):
    config = load_config(args.config)
    destination = Path(args.dst) if args.dst else job_dir(config, args.job) / 'remote'
    check_transport(config)
    fetch_tree(config, args.job, destination)
    return _emit(args, {'job': args.job, 'destination': str(destination)}, EXIT_OK)


def cmd_status(args):
    config = load_config(args.config)
    receipt = read_receipt(config, args.job)
    # A timeout only says the controller stopped waiting; re-observe the target.
    if receipt.get('status') in (STATUS_COMPLETED, STATUS_FAILED,
                                 STATUS_TRANSPORT_ERROR, STATUS_LOCKED):
        return _emit(args, receipt, EXIT_OK)
    # still running (or unknown): probe the target without resubmitting
    state = _observe_state(config, args.job)
    if state.startswith('finished:'):
        receipt['status'] = (STATUS_COMPLETED
                             if state.split(':', 1)[1] == '0' else STATUS_FAILED)
        receipt['workload_exit'] = int(state.split(':', 1)[1])
        receipt.pop('detail', None)
        directory = job_dir(config, args.job)
        remote = read_remote_exit(config, args.job)
        if remote is not None:
            _save_logs(directory, remote[1], remote[2])
        try:
            fetch_tree(config, args.job, directory / 'remote')
            receipt['fetched'] = True
        except TransportError as error:
            receipt['fetched'] = False
            receipt['fetch_error'] = str(error)
    elif state == 'running':
        receipt['status'] = 'running'
    else:
        receipt['status'] = STATUS_UNKNOWN
    receipt['observed_state'] = state
    write_receipt(job_dir(config, args.job) / 'receipt.json', receipt)
    return _emit(args, receipt,
                 EXIT_UNKNOWN if receipt['status'] == STATUS_UNKNOWN else EXIT_OK)


def cmd_receipt(args):
    config = load_config(args.config)
    return _emit(args, read_receipt(config, args.job), EXIT_OK)


def _emit(args, payload, exit_code):
    if getattr(args, 'json', False):
        print(json.dumps(payload, indent=2))
    else:
        for key in ('status', 'job_id', 'workload_exit', 'ssh_exit', 'detail'):
            if key in payload:
                print(f'{key}: {payload[key]}')
        for check in payload.get('checks', []):
            print(f"{check['status']:>9}  {check['check']}: {check['detail']}")
        for item in payload.get('upload_manifest', []):
            print(f"uploaded {item['path']} ({item['bytes']} bytes)")
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    def common(name, help_text):
        item = sub.add_parser(name, help=help_text)
        item.add_argument('--config', required=True, help='runner config JSON')
        item.add_argument('--json', action='store_true', help='machine-readable output')
        return item

    probe = common('probe', 'check transport, workdir, and toolchain identity')
    probe.set_defaults(func=cmd_probe)

    upload = common('upload', 'copy files to the remote job directory')
    upload.add_argument('--job', required=True)
    upload.add_argument('--src', action='append', required=True,
                        metavar='LOCAL:DEST')
    upload.set_defaults(func=cmd_upload)

    execute = common('exec', 'upload a script and run it on the target')
    execute.add_argument('--job', default=None)
    execute.add_argument('--script', required=True)
    execute.add_argument('--timeout', type=int, default=None)
    execute.add_argument('--no-lock', action='store_true',
                         help='skip the configured shared lock')
    execute.set_defaults(func=cmd_exec)

    fetch = common('fetch', 'copy the remote job directory back')
    fetch.add_argument('--job', required=True)
    fetch.add_argument('--dst', default=None)
    fetch.set_defaults(func=cmd_fetch)

    status = common('status', 'report job state without resubmitting')
    status.add_argument('--job', required=True)
    status.set_defaults(func=cmd_status)

    receipt = common('receipt', 'print the stored receipt')
    receipt.add_argument('--job', required=True)
    receipt.set_defaults(func=cmd_receipt)

    args = parser.parse_args(argv)
    if getattr(args, 'job', None) is None:
        args.job = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6]
    try:
        return args.func(args)
    except RunnerError as error:
        print(f'error: {error}', file=sys.stderr)
        return error.exit_code
    except (TransportError, TimeoutError) as error:
        print(f'transport error: {error}', file=sys.stderr)
        return EXIT_TRANSPORT
    except KeyboardInterrupt:
        print('interrupted; job state unknown, check with `status`',
              file=sys.stderr)
        return EXIT_UNKNOWN


if __name__ == '__main__':
    sys.exit(main())
