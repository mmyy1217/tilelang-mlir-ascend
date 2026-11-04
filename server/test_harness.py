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
