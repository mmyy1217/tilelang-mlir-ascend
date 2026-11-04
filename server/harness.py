"""Read-only HTTP projection of completed workspace captures; no source workspace access."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
from fastapi import APIRouter, HTTPException, Query

ID = re.compile(r'^[A-Za-z0-9_-]+$')

def router(default_dir):
    api = APIRouter(prefix='/api/harness')
    root = Path(os.environ.get('HARNESS_VIS_DATA_DIR', str(Path(default_dir) / 'harness'))).resolve()

    def snapshot_path(project_id, snapshot_id):
        if not ID.fullmatch(project_id) or not ID.fullmatch(snapshot_id):
            raise HTTPException(400, 'Invalid snapshot identity')
        path = root / project_id / 'snapshots' / snapshot_id
        if not path.resolve().is_relative_to(root) or not (path / 'manifest.json').is_file():
            raise HTTPException(404, 'Snapshot not found')
        return path

    def read_json(path):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise HTTPException(409, 'Snapshot record unavailable or invalid')

    @api.get('/snapshots')
    def snapshots():
        items, errors = [], []
        for file in sorted(root.glob('*/snapshots/*/manifest.json'), reverse=True):
            if file.parent.name.startswith('.') or not file.resolve().is_relative_to(root):
                continue
            try:
                manifest = read_json(file)
                if manifest.get('schema') != 'harness-vis/snapshot@1':
                    raise ValueError('Unknown schema')
                items.append({k: manifest[k] for k in ('id', 'projectId', 'projectName', 'capturedAt', 'consistency', 'totalBytes')})
            except (HTTPException, KeyError, ValueError):
                errors.append(file.parent.name)
        items.sort(key=lambda x: x['capturedAt'], reverse=True)
        return {'items': items, 'unavailable': errors, 'mode': 'manual-snapshot'}

    @api.get('/projects/{project_id}/snapshots/{snapshot_id}')
    def model(project_id: str, snapshot_id: str):
        snapshot = snapshot_path(project_id, snapshot_id)
        versions = sorted((root / project_id / 'views' / snapshot_id).glob('*.json'))
        source = versions[-1] if versions else snapshot / 'model.json'
        if not source.resolve().is_relative_to(root):
            raise HTTPException(400, 'Invalid view path')
        data = read_json(source)
        data['manifest'] = read_json(snapshot / 'manifest.json')
        return data

    @api.get('/projects/{project_id}/snapshots/{snapshot_id}/file')
    def content(project_id: str, snapshot_id: str, path: str = Query(...)):
        snapshot = snapshot_path(project_id, snapshot_id)
        manifest = read_json(snapshot / 'manifest.json')
        entry = next((f for f in manifest['files'] if f['path'] == path and f['status'] == 'copied'), None)
        if not entry:
            raise HTTPException(404, 'File was not copied into this snapshot')
        if not entry.get('text'):
            raise HTTPException(415, 'Binary artifact: metadata only')
        target = snapshot / 'workspace' / path
        if Path(path).is_absolute() or '..' in Path(path).parts or not target.resolve().is_relative_to(snapshot / 'workspace'):
            raise HTTPException(400, 'Invalid file path')
        try:
            data = target.read_bytes()
        except OSError:
            raise HTTPException(409, 'Captured file is missing')
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise HTTPException(409, 'Captured file no longer matches its digest')
        return {'path': path, 'sha256': entry['sha256'], 'content': data.decode('utf-8')}
    return api
