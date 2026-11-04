#!/usr/bin/env python3
"""Read-only workspace capture and portable Harness Vis entry point (stdlib only)."""
from __future__ import annotations
import argparse
import hashlib
import fcntl
import math
import signal
import threading
import time
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone

SCHEMA = 'harness-vis/snapshot@1'
TEXT_EXTENSIONS = {'.md', '.txt', '.json', '.yaml', '.yml', '.py', '.csv', '.log', '.toml', '.sh', '.mlir'}
EXCLUDED = {'.git', '.venv', 'node_modules', '__pycache__', '.env', '.aws', '.ssh'}

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(data):
    return hashlib.sha256(data).hexdigest()

def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def safe_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Path must be relative and contained in the snapshot')
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Path escapes snapshot')
    return target

def inventory(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith('.env'))
        # Record symlink directories, but never traverse them.
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink():
                files.append(name)
                dirs.remove(name)
        for name in sorted(files):
            path = Path(directory) / name
            rel = path.relative_to(root).as_posix()
            try:
                info = path.lstat()
                result[rel] = (info.st_size, info.st_mtime_ns, info.st_mode)
            except FileNotFoundError:
                continue
    return result

def read_regular(root, relative, limit):
    # Reject symlink ancestors as well as final components, including during capture races.
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError('Not a regular file')
            data = source.read(limit + 1)
            if len(data) > limit:
                raise ValueError('File exceeds capture limit')
            return data
    finally:
        os.close(fd)

def fields(text):
    values = {}
    for line in text.splitlines():
        match = re.match(r'^\s*(?:-\s*)?(?:\*\*)?([\w\- /\u4e00-\u9fff]+)(?:\*\*)?\s*[:：]\s*(.+?)\s*$', line)
        if match:
            values.setdefault(match[1].strip().lower(), match[2].strip(' `'))
    return values

def make_model(snapshot, manifest):
    records = []
    for file in manifest['files']:
        if file['status'] != 'copied' or not file.get('text'):
            continue
        path = file['path']
        if not path.lower().endswith('.md'):
            continue
        content = (snapshot / 'workspace' / path).read_text(encoding='utf-8')
        meta = fields(content)
        name = Path(path).stem.upper()
        parts = Path(path).parts
        run_id = parts[1] if len(parts) > 2 and parts[0] == 'runs' else None
        kind = name.lower() if name in {'TASK', 'RESULT', 'CANDIDATE', 'BASELINE', 'STATE', 'ENVIRONMENT', 'AUTOMATIC', 'REQUIREMENTS', 'PROJECT', 'DECISIONS', 'RESOURCE_USAGE'} else 'document'
        if parts[0] == 'environments':
            kind = 'environment'
        title = next((line.lstrip('# ').strip() for line in content.splitlines() if line.startswith('# ')), Path(path).name)
        records.append({'id': path, 'path': path, 'kind': kind, 'runId': run_id, 'title': title, 'fields': meta, 'sha256': file['sha256']})
    runs = []
    for run_id in sorted({r['runId'] for r in records if r['runId']}):
        group = [r for r in records if r['runId'] == run_id]
        task = next((r for r in group if r['kind'] == 'task' and len(Path(r['path']).parts) == 3), None)
        result = next((r for r in group if r['kind'] == 'result' and len(Path(r['path']).parts) == 3), None)
        tm, rm = (task or {}).get('fields', {}), (result or {}).get('fields', {})
        runs.append({'id': run_id, 'node': tm.get('node', 'unknown'), 'operation': tm.get('operation', ''),
                     'status': rm.get('status', rm.get('状态', 'unknown')), 'createdAt': tm.get('created_at'),
                     'parentRun': tm.get('parent_run'), 'records': [r['id'] for r in group],
                     'taskPath': (task or {}).get('path'), 'resultPath': (result or {}).get('path')})
    return {'schema': 'harness-vis/model@1', 'snapshotId': manifest['id'], 'projectId': manifest['projectId'],
            'projectName': manifest['projectName'], 'capturedAt': manifest['capturedAt'],
            'consistency': manifest['consistency'], 'records': records, 'runs': runs,
            'warnings': manifest['warnings'], 'annotations': []}

def capture(project, data_dir, max_file_bytes=2_000_000, max_total_bytes=50_000_000):
    project = Path(project).expanduser().resolve()
    workspace = project / '.workspace'
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError('Project must contain a real .workspace directory')
    data_dir = Path(data_dir).expanduser().resolve()
    if data_dir.is_relative_to(workspace):
        raise ValueError('Snapshot storage must be outside the source .workspace')
    if max_file_bytes <= 0 or max_total_bytes <= 0:
        raise ValueError('Capture limits must be positive')
    project_id = digest(str(project).encode())[:16]
    snapshot_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    target_root = data_dir / project_id / 'snapshots'
    target_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.capture-', dir=target_root))
    files, warnings, total = [], [], 0
    try:
        before = inventory(workspace)
        for rel, (size, mtime, mode) in sorted(before.items()):
            entry = {'path': rel, 'sizeBytes': size, 'modifiedNs': mtime}
            files.append(entry)
            if not stat.S_ISREG(mode) or Path(rel).name in EXCLUDED or Path(rel).name.startswith('.env'):
                entry.update(status='excluded', reason='symlink, special file or private configuration')
                continue
            if size > max_file_bytes or total + size > max_total_bytes:
                entry.update(status='omitted', reason='capture size limit')
                continue
            try:
                data = read_regular(workspace, rel, min(max_file_bytes, max_total_bytes - total))
                destination = staging / 'workspace' / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
                total += len(data)
                try:
                    data.decode('utf-8')
                    is_text = b'\0' not in data and Path(rel).suffix.lower() in TEXT_EXTENSIONS
                except UnicodeDecodeError:
                    is_text = False
                entry.update(status='copied', sizeBytes=len(data), sha256=digest(data), text=is_text)
            except (OSError, ValueError) as exc:
                entry.update(status='missing', reason=str(exc))
        after = inventory(workspace)
        changed = set(before) ^ set(after)
        changed.update(path for path in before.keys() & after.keys() if before[path] != after[path])
        # Verify bytes too: timestamps alone cannot identify in-place edits reliably.
        for entry in files:
            if entry['status'] == 'copied':
                try:
                    if digest(read_regular(workspace, entry['path'], max_file_bytes)) != entry['sha256']:
                        changed.add(entry['path'])
                except (OSError, ValueError):
                    changed.add(entry['path'])
        if changed:
            warnings.append({'kind': 'source_changed', 'paths': sorted(changed), 'message': 'Source changed during capture; this is a partial observation, not an atomic filesystem snapshot.'})
        omitted = [f['path'] for f in files if f['status'] != 'copied']
        if omitted:
            warnings.append({'kind': 'files_not_copied', 'paths': omitted, 'message': 'Some files were excluded, unavailable or exceeded the capture limits.'})
        manifest = {'schema': SCHEMA, 'id': snapshot_id, 'projectId': project_id, 'projectName': project.name,
                    'sourceProject': str(project), 'capturedAt': now(), 'consistency': 'partial' if changed else 'checked',
                    'consistencyMeaning': 'Two-pass file and digest comparison; not an atomic filesystem snapshot.',
                    'externalReferences': 'not_captured', 'totalBytes': total, 'files': files, 'warnings': warnings}
        write_json(staging / 'manifest.json', manifest)
        write_json(staging / 'model.json', make_model(staging, manifest))
        (staging / 'AGENT_TASK.md').write_text(
            '# Harness Vis snapshot task\n\nRead SKILL.md from the installed harness-vis skill. '
            'Read manifest.json and model.json here, then only the copied workspace files needed. '
            'Do not read or modify the live source workspace. Do not execute copied scripts. '
            'Write an annotation JSON file outside this snapshot; each annotation requires title, body and sources '
            '(exact copied relative paths). Return the annotation path to the coordinator; only the coordinator publishes it. '
            'Missing references remain unknown. Recorded running is not a live job observation.\n', encoding='utf-8')
        final = target_root / snapshot_id
        staging.rename(final)
        return {'projectId': project_id, 'snapshotId': snapshot_id, 'snapshotPath': str(final), 'consistency': manifest['consistency'], 'files': len(files)}
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

def watch_fingerprint(workspace, max_file_bytes, max_total_bytes):
    """Hash bounded capture inputs; private files and symlinks never trigger sync."""
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError('Project must contain a real .workspace directory')
    entries, total = [], 0
    for rel, (size, mtime, mode) in sorted(inventory(workspace).items()):
        if not stat.S_ISREG(mode) or Path(rel).name in EXCLUDED or Path(rel).name.startswith('.env'):
            continue
        if size > max_file_bytes or total + size > max_total_bytes:
            entries.append((rel, 'omitted', size, mtime))
            continue
        data = read_regular(workspace, rel, min(max_file_bytes, max_total_bytes-total))
        entries.append((rel, 'copied', len(data), digest(data)))
        total += len(data)
    return digest(json.dumps(entries, sort_keys=True).encode())

def manifest_fingerprint(manifest):
    entries = []
    for entry in sorted(manifest['files'], key=lambda e: e['path']):
        if entry['status'] == 'copied':
            entries.append((entry['path'], 'copied', entry['sizeBytes'], entry['sha256']))
        elif entry['status'] == 'omitted':
            entries.append((entry['path'], 'omitted', entry['sizeBytes'], entry['modifiedNs']))
        elif entry['status'] == 'missing':
            entries.append((entry['path'], 'missing'))
    return digest(json.dumps(entries, sort_keys=True).encode())

class SnapshotWatcher:
    """A deterministic debounce state machine; capture retains all isolation checks."""
    def __init__(self, project, data_dir, settle=2, max_file_bytes=2_000_000, max_total_bytes=50_000_000):
        self.project = Path(project).expanduser().resolve()
        self.workspace = self.project / '.workspace'
        self.data_dir = Path(data_dir).expanduser().resolve()
        if not self.workspace.is_dir() or self.workspace.is_symlink():
            raise ValueError('Project must contain a real .workspace directory')
        if self.data_dir.is_relative_to(self.workspace):
            raise ValueError('Snapshot storage must be outside the source .workspace')
        if not math.isfinite(settle) or settle < 0 or min(max_file_bytes, max_total_bytes) <= 0:
            raise ValueError('Invalid watch settle time or capture limits')
        self.settle, self.max_file_bytes, self.max_total_bytes = settle, max_file_bytes, max_total_bytes
        self.project_id = digest(str(self.project).encode())[:16]
        self.pending = self.published = None
        self.changed_at, self.attempts = 0, 0
        self.last_snapshot = None
        self.state = 'watching'
        # Resume from a checked snapshot without creating an identical copy on every restart.
        snapshots = []
        for path in (self.data_dir/self.project_id/'snapshots').glob('*/manifest.json'):
            if path.parent.name.startswith('.'):
                continue
            try:
                manifest = json.loads(path.read_text())
                if manifest.get('schema') == SCHEMA:
                    snapshots.append((manifest['capturedAt'], path, manifest))
            except (OSError, ValueError, KeyError):
                continue
        if snapshots:
            _, path, manifest = max(snapshots, key=lambda item: item[0])
            try:
                if manifest.get('consistency') == 'checked':
                    validate_snapshot(path.parent)
                    self.published = manifest_fingerprint(manifest)
                    self.last_snapshot = manifest['id']
            except (OSError, ValueError, KeyError):
                pass

    def tick(self, timestamp, before_capture=None):
        fingerprint = watch_fingerprint(self.workspace, self.max_file_bytes, self.max_total_bytes)
        if fingerprint == self.published:
            self.pending, self.attempts, self.state = None, 0, 'watching'
            return None
        if fingerprint != self.pending:
            self.pending, self.changed_at, self.attempts = fingerprint, timestamp, 0
        self.state = 'settling'
        if timestamp-self.changed_at < self.settle:
            return None
        if self.attempts >= 2:
            self.state = 'partial'
            return None
        self.state = 'capturing'
        if before_capture:
            before_capture()
        result = capture(self.project, self.data_dir, self.max_file_bytes, self.max_total_bytes)
        self.attempts += 1
        self.changed_at = timestamp
        self.last_snapshot = result['snapshotId']
        manifest = json.loads((Path(result['snapshotPath'])/'manifest.json').read_text())
        if result['consistency'] == 'checked' and manifest_fingerprint(manifest) == fingerprint:
            self.published, self.state = fingerprint, 'watching'
        else:
            self.state = 'partial'
        return result

def watch(project, data_dir, interval=2, settle=2, max_captures=0, max_duration=0,
          max_file_bytes=2_000_000, max_total_bytes=50_000_000, emit=None):
    if not math.isfinite(interval) or interval <= 0 or not math.isfinite(max_duration) or max_duration < 0 or max_captures < 0:
        raise ValueError('Watch interval must be positive and limits nonnegative')
    watcher = SnapshotWatcher(project, data_dir, settle, max_file_bytes, max_total_bytes)
    directory = watcher.data_dir/watcher.project_id
    directory.mkdir(parents=True, exist_ok=True)
    emit = emit or (lambda value: print(json.dumps(value, ensure_ascii=False), flush=True))
    stopped = threading.Event()
    started, count = time.monotonic(), 0
    error = None
    with (directory/'.watch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('A watcher already owns this project and data directory')
        # Re-read the latest capture after taking ownership (another watcher may just have exited).
        watcher = SnapshotWatcher(project, data_dir, settle, max_file_bytes, max_total_bytes)
        def heartbeat(state):
            temporary = directory/('.watch-'+uuid.uuid4().hex+'.tmp')
            write_json(temporary, {'schema':'harness-vis/watch@1', 'projectId':watcher.project_id,
                'state':state, 'updatedAt':now(), 'staleAfterSeconds':max(15, interval*3+settle),
                'intervalSeconds':interval, 'settleSeconds':settle,
                'lastSnapshotId':watcher.last_snapshot, 'captures':count, 'error':error})
            temporary.replace(directory/'watch.json')
        old_handlers = {}
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                old_handlers[signum] = signal.signal(signum, lambda *_: stopped.set())
        try:
            heartbeat('watching')
            emit({'event':'watch_started', 'projectId':watcher.project_id, 'intervalSeconds':interval, 'settleSeconds':settle})
            while not stopped.is_set():
                if max_duration and time.monotonic()-started >= max_duration:
                    break
                try:
                    result = watcher.tick(time.monotonic(), lambda: heartbeat('capturing'))
                    error = None
                    if result:
                        count += 1
                        emit({'event':'snapshot_captured', **result})
                    heartbeat(watcher.state)
                    if max_captures and count >= max_captures:
                        break
                except (OSError, ValueError) as exc:
                    # A disappeared workspace is not an empty workspace snapshot.
                    message = str(exc)
                    if message != error:
                        emit({'event':'watch_error', 'message':message})
                    error = message
                    watcher.pending = None  # Require a fresh quiet period after recovery.
                    heartbeat('error')
                remaining = max(0, max_duration-(time.monotonic()-started)) if max_duration else interval
                stopped.wait(min(interval, remaining))
        finally:
            heartbeat('stopped')
            for signum, handler in old_handlers.items():
                signal.signal(signum, handler)
            emit({'event':'watch_stopped', 'projectId':watcher.project_id, 'captures':count})
    return {'projectId':watcher.project_id, 'captures':count}

def validate_snapshot(snapshot):
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    if manifest.get('schema') != SCHEMA:
        raise ValueError('Unsupported snapshot schema')
    for entry in manifest['files']:
        if entry['status'] == 'copied':
            path = safe_path(snapshot / 'workspace', entry['path'])
            if not path.is_file() or digest(path.read_bytes()) != entry['sha256']:
                raise ValueError('Snapshot digest mismatch: ' + entry['path'])
    return manifest

def publish(snapshot, annotations):
    snapshot = Path(snapshot).resolve()
    manifest = validate_snapshot(snapshot)
    entries = json.loads(Path(annotations).read_text(encoding='utf-8'))
    if not isinstance(entries, list) or len(entries) > 100:
        raise ValueError('Annotations must be a list of at most 100 items')
    copied = {f['path'] for f in manifest['files'] if f['status'] == 'copied'}
    for item in entries:
        if not isinstance(item, dict) or set(item) != {'title', 'body', 'sources'}:
            raise ValueError('Each annotation requires exactly title, body and sources')
        if not isinstance(item['title'], str) or not isinstance(item['body'], str) or len(item['title']) > 200 or len(item['body']) > 20000:
            raise ValueError('Invalid annotation text')
        if not isinstance(item['sources'], list) or not item['sources'] or any(not isinstance(s, str) or s not in copied for s in item['sources']):
            raise ValueError('Annotation sources must refer to copied files')
    # Immutable captures, append-only derived views. Publish through an atomic rename.
    views = snapshot.parent.parent / 'views' / manifest['id']
    views.mkdir(parents=True, exist_ok=True)
    revision = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + uuid.uuid4().hex[:6]
    model = make_model(snapshot, manifest)
    model.update(annotations=entries, annotatedAt=now(), viewRevision=revision)
    temporary = views / ('.' + revision + '.tmp')
    write_json(temporary, model)
    target = views / (revision + '.json')
    temporary.rename(target)
    return {'snapshotId': manifest['id'], 'viewRevision': revision, 'path': str(target)}

def install(project, source=None):
    source = Path(source or Path(__file__).resolve().parents[3])
    project = Path(project).expanduser().resolve()
    target = project / '.agents' / 'skills' / 'harness-vis'
    if target.exists() or target.is_symlink():
        raise ValueError('harness-vis already exists; preserve it and choose a clean installation target')
    if not (source / 'web/dist/index.html').is_file():
        raise ValueError('Build web first: npm --prefix web ci && npm --prefix web run build')
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.harness-vis-', dir=target.parent))
    try:
        shutil.copytree(source / 'skills/harness-vis', staging, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', 'runtime'))
        shutil.copytree(source / 'server', staging / 'runtime/server', ignore=shutil.ignore_patterns('__pycache__', 'test_*.py', 'conftest.py'))
        shutil.copytree(source / 'web/dist', staging / 'runtime/web/dist')
        write_json(staging / 'INSTALLATION.json', {'schema': 'harness-vis/install@1', 'installedAt': now()})
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {'skillPath': str(target), 'entrypoint': str(target / 'scripts/harness_vis.py')}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    cap = sub.add_parser('capture')
    cap.add_argument('--project', required=True)
    cap.add_argument('--data-dir', required=True)
    cap.add_argument('--max-file-bytes', type=int, default=2_000_000)
    cap.add_argument('--max-total-bytes', type=int, default=50_000_000)
    watching = sub.add_parser('watch', help='Capture changed workspace files after a quiet period')
    watching.add_argument('--project', required=True)
    watching.add_argument('--data-dir', required=True)
    watching.add_argument('--interval', type=float, default=2)
    watching.add_argument('--settle', type=float, default=2)
    watching.add_argument('--max-captures', type=int, default=0, help='Stop after this many new captures; 0 is unlimited')
    watching.add_argument('--max-duration', type=float, default=0, help='Stop after this many seconds; 0 is unlimited')
    watching.add_argument('--max-file-bytes', type=int, default=2_000_000)
    watching.add_argument('--max-total-bytes', type=int, default=50_000_000)
    pub = sub.add_parser('publish')
    pub.add_argument('--snapshot', required=True)
    pub.add_argument('--annotations', required=True)
    ins = sub.add_parser('install')
    ins.add_argument('--project', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--data-dir', required=True)
    serve.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == 'capture':
            result = capture(args.project, args.data_dir, args.max_file_bytes, args.max_total_bytes)
        elif args.command == 'watch':
            watch(args.project, args.data_dir, args.interval, args.settle, args.max_captures,
                  args.max_duration, args.max_file_bytes, args.max_total_bytes)
            return
        elif args.command == 'publish':
            result = publish(args.snapshot, args.annotations)
        elif args.command == 'install':
            result = install(args.project)
        else:
            runtime = Path(__file__).resolve().parents[1] / 'runtime'
            root = runtime if runtime.is_dir() else Path(__file__).resolve().parents[3]
            sys.path.insert(0, str(root))
            os.environ['HARNESS_VIS_DATA_DIR'] = str(Path(args.data_dir).expanduser().resolve())
            os.environ['WORKBENCH_DATA_DIR'] = str(Path(args.data_dir).expanduser().resolve() / '.service')
            import uvicorn
            from server.app import create_app
            print(f'Harness Vis: http://127.0.0.1:{args.port}/', flush=True)
            uvicorn.run(create_app(auto_seed=False), host='127.0.0.1', port=args.port)
            return
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, f'harness-vis: {exc}\n')

if __name__ == '__main__':
    main()
