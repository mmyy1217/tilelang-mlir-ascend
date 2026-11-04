#!/usr/bin/env python3
"""Read-only workspace capture and portable Harness Vis entry point (stdlib only)."""
from __future__ import annotations
import argparse
import hashlib
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
