import importlib.util
import json
from pathlib import Path
import shutil
import sys
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('harness_capture', ROOT / 'skills/harness-vis/scripts/harness_vis.py')
vis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vis)

@pytest.fixture
def source(tmp_path):
    project = tmp_path / 'operator'
    workspace = project / '.workspace'
    (workspace / 'runs/R1').mkdir(parents=True)
    (workspace / 'STATE.md').write_text('# 当前任务\n候选尚未上板。\n')
    (workspace / 'runs/R1/TASK.md').write_text('# 分析任务\n- node: analysis\n- operation: 分析标杆\n- created_at: 2026-11-03T12:00:00Z\n')
    (workspace / 'runs/R1/RESULT.md').write_text('# 分析结果\n- status: completed\n精度：not_run\n')
    return project

def test_capture_isolated_identity_and_append_only(source, tmp_path):
    data = tmp_path / 'vis'
    first = vis.capture(source, data)
    snap = Path(first['snapshotPath'])
    source_file = source / '.workspace/STATE.md'
    old = source_file.read_text()
    assert (snap / 'workspace/STATE.md').read_text() == old
    assert source_file.read_text() == old
    source_file.write_text('new state')
    second = vis.capture(source, data)
    assert first['snapshotId'] != second['snapshotId']
    assert first['projectId'] == second['projectId']
    assert (snap / 'workspace/STATE.md').read_text() == old
    model = json.loads((snap / 'model.json').read_text())
    assert model['runs'][0]['node'] == 'analysis'
    assert model['runs'][0]['status'] == 'completed'
    assert 'correctnessPassed' not in model['runs'][0]
    assert source_file.read_text() == 'new state'

def test_capture_limits_links_and_private_files(source, tmp_path):
    workspace = source / '.workspace'
    secret = tmp_path / 'outside.txt'
    secret.write_text('external secret')
    (workspace / 'link').symlink_to(secret)
    (workspace / 'dirlink').symlink_to(tmp_path, target_is_directory=True)
    (workspace / '.env').write_text('TOKEN=secret')
    (workspace / 'large.bin').write_bytes(b'x'*1001)
    result = vis.capture(source, tmp_path / 'vis', max_file_bytes=1000)
    snap = Path(result['snapshotPath'])
    entries = {e['path']:e for e in vis.validate_snapshot(snap)['files']}
    assert entries['link']['status'] == 'excluded'
    assert entries['dirlink']['status'] == 'excluded'
    assert entries['.env']['status'] == 'excluded'
    assert entries['large.bin']['status'] == 'omitted'
    assert not (snap/'workspace/link').exists()
    with pytest.raises(ValueError, match='outside'):
        vis.capture(source, workspace/'vis')

def test_source_change_is_partial(source, tmp_path, monkeypatch):
    original = vis.inventory
    calls = 0
    def changing(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root/'STATE.md').write_text('changed while capturing')
        return original(root)
    monkeypatch.setattr(vis, 'inventory', changing)
    result = vis.capture(source, tmp_path/'vis')
    manifest = json.loads((Path(result['snapshotPath'])/'manifest.json').read_text())
    assert result['consistency'] == 'partial'
    assert manifest['warnings'][0]['kind'] == 'source_changed'

def test_publish_references_integrity_and_immutable_views(source, tmp_path):
    result = vis.capture(source, tmp_path/'vis')
    snap = Path(result['snapshotPath'])
    base = (snap/'model.json').read_bytes()
    notes = tmp_path/'annotations.json'
    notes.write_text(json.dumps([{'title':'观察', 'body':'尚未上板', 'sources':['STATE.md']}]))
    first = vis.publish(snap, notes)
    second = vis.publish(snap, notes)
    assert first['path'] != second['path']
    assert Path(first['path']).exists()
    assert (snap/'model.json').read_bytes() == base
    notes.write_text(json.dumps([{'title':'bad', 'body':'claim', 'sources':['../outside']}]))
    with pytest.raises(ValueError, match='sources'):
        vis.publish(snap, notes)
    (snap/'workspace/STATE.md').write_text('tampered')
    with pytest.raises(ValueError, match='digest'):
        vis.publish(snap, notes)

def test_read_only_api_paths_history_and_integrity(source, tmp_path, monkeypatch):
    data = tmp_path/'vis'
    captured = vis.capture(source, data)
    monkeypatch.setenv('HARNESS_VIS_DATA_DIR', str(data))
    from server.app import create_app
    with TestClient(create_app(tmp_path/'service', auto_seed=False)) as client:
        listing = client.get('/api/harness/snapshots').json()
        assert len(listing['items']) == 1
        base = f"/api/harness/projects/{captured['projectId']}/snapshots/{captured['snapshotId']}"
        assert client.get(base).json()['runs'][0]['node'] == 'analysis'
        response = client.get(base+'/file', params={'path':'STATE.md'})
        assert response.status_code == 200
        assert response.json()['content'].startswith('# 当前任务')
        assert client.get(base+'/file', params={'path':'../../etc/passwd'}).status_code == 404
        assert client.post('/api/harness/snapshots', json={}).status_code == 405
        (Path(captured['snapshotPath'])/'workspace/STATE.md').write_text('tampered')
        assert client.get(base+'/file', params={'path':'STATE.md'}).status_code == 409

def test_install_portable_bundle(source, tmp_path):
    build = tmp_path/'build'
    (build/'web/dist').mkdir(parents=True)
    (build/'web/dist/index.html').write_text('<html>harness</html>')
    shutil.copytree(ROOT/'skills/harness-vis', build/'skills/harness-vis', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'server', build/'server', ignore=shutil.ignore_patterns('__pycache__'))
    installed = vis.install(source, build)
    root = Path(installed['skillPath'])
    assert (root/'runtime/web/dist/index.html').is_file()
    assert (root/'runtime/server/harness.py').is_file()
    assert not (root/'runtime/server/test_harness.py').exists()
    assert '专用可视化 Agent' in (root/'SKILL.md').read_text()
    with pytest.raises(ValueError, match='already exists'):
        vis.install(source, build)

def test_watch_debounce_content_change_and_restart(source, tmp_path):
    import os
    data = tmp_path/'vis'
    watcher = vis.SnapshotWatcher(source, data, settle=2)
    assert watcher.tick(0) is None
    assert watcher.tick(1) is None
    first = watcher.tick(2)
    assert first['consistency'] == 'checked'
    assert watcher.tick(3) is None
    path = source/'.workspace/STATE.md'
    old = path.read_bytes()
    metadata = path.stat()
    path.write_bytes(b'x'*len(old))
    os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    assert watcher.tick(4) is None  # Same size and timestamp, changed bytes.
    path.write_bytes(b'y'*len(old))
    assert watcher.tick(5) is None  # Burst resets quiet window.
    assert watcher.tick(6) is None
    second = watcher.tick(7)
    assert second['snapshotId'] != first['snapshotId']
    assert (Path(first['snapshotPath'])/'workspace/STATE.md').read_bytes() == old
    assert (Path(second['snapshotPath'])/'workspace/STATE.md').read_bytes() == b'y'*len(old)
    restarted = vis.SnapshotWatcher(source, data, settle=0)
    assert restarted.tick(8) is None
    assert restarted.last_snapshot == second['snapshotId']
    path.unlink()
    assert restarted.tick(9)['files'] == 2  # File deletion creates a new observation.


def test_watch_exclusions_limits_and_missing_workspace(source, tmp_path):
    workspace = source/'.workspace'
    data = tmp_path/'vis'
    watcher = vis.SnapshotWatcher(source, data, settle=0, max_file_bytes=1000)
    watcher.tick(0)
    (workspace/'.env').write_text('secret')
    (workspace/'external').symlink_to(tmp_path/'missing')
    assert watcher.tick(1) is None
    (workspace/'large.bin').write_bytes(b'x'*1001)
    result = watcher.tick(2)
    assert result
    assert not (Path(result['snapshotPath'])/'workspace/large.bin').exists()
    workspace.rename(source/'moved-workspace')
    with pytest.raises(ValueError, match='real .workspace'):
        watcher.tick(3)


def test_watch_partial_retries_are_bounded(source, tmp_path, monkeypatch):
    original = vis.capture
    def partial(*args):
        result = original(*args)
        result['consistency'] = 'partial'
        return result
    monkeypatch.setattr(vis, 'capture', partial)
    watcher = vis.SnapshotWatcher(source, tmp_path/'vis', settle=0)
    assert watcher.tick(0)
    assert watcher.tick(1)
    assert watcher.tick(2) is None
    assert watcher.state == 'partial'
    (source/'.workspace/STATE.md').write_text('changed')
    assert watcher.tick(3)  # A new source revision permits fresh attempts.


def test_watch_lock_and_terminal_status(source, tmp_path):
    import fcntl
    data = tmp_path/'vis'
    project_id = vis.digest(str(source.resolve()).encode())[:16]
    directory = data/project_id
    directory.mkdir(parents=True)
    with (directory/'.watch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='already owns'):
            vis.watch(source, data, max_captures=1)
    events = []
    result = vis.watch(source, data, interval=.01, settle=0, max_captures=1, emit=events.append)
    assert result['captures'] == 1
    status = json.loads((directory/'watch.json').read_text())
    assert status['state'] == 'stopped'
    assert status['lastSnapshotId']
    assert [e['event'] for e in events] == ['watch_started', 'snapshot_captured', 'watch_stopped']
    for kwargs in [{'interval':0}, {'interval':float('nan')}, {'settle':-1}, {'max_duration':-1}, {'max_captures':-1}]:
        with pytest.raises(ValueError):
            vis.watch(source, data, **kwargs)
    with pytest.raises(ValueError, match='outside'):
        vis.watch(source, source/'.workspace/vis')


def test_watch_cli_captures_live_changes_and_stops(source, tmp_path):
    import subprocess
    data = tmp_path/'vis'
    command = [sys.executable, str(ROOT/'skills/harness-vis/scripts/harness_vis.py'), 'watch',
        '--project', str(source), '--data-dir', str(data), '--interval', '.05', '--settle', '.1',
        '--max-captures', '2', '--max-duration', '5']
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    events = []
    try:
        for line in process.stdout:
            event = json.loads(line)
            events.append(event)
            if event['event'] == 'snapshot_captured':
                (source/'.workspace/STATE.md').write_text('updated from running producer')
                break
        output, error = process.communicate(timeout=8)
        events.extend(json.loads(line) for line in output.splitlines())
        assert process.returncode == 0, error
        captures = [e for e in events if e['event'] == 'snapshot_captured']
        assert len(captures) == 2
        assert (Path(captures[-1]['snapshotPath'])/'workspace/STATE.md').read_text() == 'updated from running producer'
        assert events[-1]['event'] == 'watch_stopped'
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_api_watch_state_expires_without_claiming_liveness(source, tmp_path, monkeypatch):
    data = tmp_path/'vis'
    captured = vis.capture(source, data)
    path = data/captured['projectId']/'watch.json'
    status = {'schema':'harness-vis/watch@1','projectId':captured['projectId'],'state':'watching',
        'updatedAt':'2000-01-01T00:00:00+00:00','staleAfterSeconds':15}
    vis.write_json(path, status)
    monkeypatch.setenv('HARNESS_VIS_DATA_DIR', str(data))
    from server.app import create_app
    with TestClient(create_app(tmp_path/'service', auto_seed=False)) as client:
        assert client.get('/api/harness/snapshots').json()['watchers'][0]['state'] == 'stale'
        status['updatedAt'] = vis.now()
        vis.write_json(path, status)
        assert client.get('/api/harness/snapshots').json()['watchers'][0]['state'] == 'watching'
        status['state'] = 'stopped'
        vis.write_json(path, status)
        assert client.get('/api/harness/snapshots').json()['watchers'][0]['state'] == 'stopped'
