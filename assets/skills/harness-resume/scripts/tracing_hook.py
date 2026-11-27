"""Optional thin post-commit hook and read-only status reader for external `agent-trace`.

Design boundaries:
- Zero hard dependency: projects without explicit opt-in (`project.json["tracing"]["enabled"] == True`)
  never spawn any external CLI or write tracing metadata.
- Narrow single-command interface: invokes `agent-trace [--dir ...] [--trace ...] --json sync-harness
  --project-dir <workspace>/.workspace/<project_id>` ONLY after `accept` and `promote-candidate`
  have durably committed `search/tree.json`.
- Never masks sync failures behind `|| true` and never rolls back committed Harness state:
  failures are recorded in `search/tracing-status.json` and surfaced on stderr + `reconcile`/`tracing-status`
  so syncing can be retried later without re-running NPU jobs.
- `read_status()` is strictly read-only and safe for `harness-status` / `searchctl reconcile`.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

import projectctl

HOOK_STATUS_SCHEMA = "operator-harness/tracing-hook-status@1"
SYNC_STATE_SCHEMA = "agent-tracing/harness-sync@1"
TRIGGER_OPS = ("accept", "promote-candidate")
SYNC_TIMEOUT_SECONDS = 60


def _now():
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json_safe(path):
    if not path.exists() or path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@contextmanager
def _hook_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".tracing-hook.lock"
    if lock_path.is_symlink():
        raise projectctl.Invalid("tracing hook lock must not be a symlink")
    with lock_path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def load_config(store):
    if store.legacy or not store.project_id:
        return None
    data = store.workspace.load(store.project_id)
    raw = data.get("tracing")
    if not raw:
        return None
    cfg = projectctl.validate_tracing_schema(store.project_id, raw)
    if not cfg["enabled"]:
        return cfg
    projectctl.validate_tracing_store_dir(store.root, store.project_id, cfg["store_dir"])
    return cfg


def build_command(store, cfg):
    exe, _ = projectctl.resolve_tracing_command(store.root, cfg["command"])
    store_dir_path = (store.root / cfg["store_dir"]).resolve()
    return [
        exe,
        "--dir", str(store_dir_path),
        "--trace", cfg["trace_id"],
        "--json",
        "sync-harness",
        "--project-dir", str(store.records.resolve()),
    ]


def build_retry_command(store, cfg):
    exe, _ = projectctl.resolve_tracing_command(store.root, cfg["command"])
    store_dir_path = (store.root / cfg["store_dir"]).resolve()
    return shlex.join([
        exe,
        "--dir", str(store_dir_path),
        "--trace", cfg["trace_id"],
        "sync-harness",
        "--project-dir", str(store.records.resolve()),
    ])


def _tree_watermark(tree):
    if not isinstance(tree, dict):
        return 0, None, None
    events = tree.get("events") or []
    count = len(events)
    last_id = None
    if events and isinstance(events[-1], dict):
        op = events[-1].get("operation")
        if isinstance(op, dict) and isinstance(op.get("event_id"), str):
            last_id = op["event_id"]
    best = tree.get("best")
    promoted = best.get("candidate_id") if isinstance(best, dict) and isinstance(best.get("candidate_id"), str) else None
    return count, last_id, promoted


def _trace_dir(store, cfg):
    base = projectctl.validate_tracing_store_dir(store.root, store.project_id, cfg["store_dir"])
    traces_root = base / "traces"
    projectctl.require(not traces_root.is_symlink(), "tracing traces directory must not be a symlink")
    raw_tdir = traces_root / cfg["trace_id"]
    projectctl.require(not raw_tdir.is_symlink(), "trace directory must not be a symlink")
    tdir = raw_tdir.resolve()
    if not tdir.is_relative_to(base):
        raise projectctl.Invalid("trace directory escapes tracing store_dir")
    return tdir


def _status_file(store):
    return store.workspace.local(store.project_id, "search/tracing-status.json")


def read_status(store, tree=None):
    """Strictly read-only inspection of project tracing config, sync watermark, and viewer path."""
    if store.legacy or not store.project_id:
        return {"enabled": False, "state": "disabled", "reason": "legacy search layout"}
    if tree is None and store.path.exists():
        try:
            tree = store.load()
        except (projectctl.Invalid, OSError, ValueError, KeyError, TypeError):
            tree = None
    upstream_count, last_event_id, promoted_id = _tree_watermark(tree)

    try:
        cfg = load_config(store)
    except (projectctl.Invalid, OSError, ValueError, KeyError, TypeError) as exc:
        return {
            "enabled": True,
            "state": "error",
            "upstream_event_count": upstream_count,
            "synced_event_count": None,
            "lag_events": upstream_count,
            "last_upstream_event_id": last_event_id,
            "synced_last_event_id": None,
            "promoted_candidate_id": promoted_id,
            "best_attempt_id": None,
            "viewer_path": None,
            "viewer_exists": False,
            "synced_at": None,
            "last_error": f"invalid tracing config: {exc}",
            "retry_command": "",
        }
    if not cfg or not cfg["enabled"]:
        return {"enabled": False, "state": "disabled"}

    try:
        tdir = _trace_dir(store, cfg)
        retry_cmd = build_retry_command(store, cfg)
    except (projectctl.Invalid, OSError, RuntimeError, ValueError) as exc:
        return {
            "enabled": True,
            "state": "error",
            "command": cfg["command"],
            "trace_id": cfg["trace_id"],
            "store_dir": cfg["store_dir"],
            "upstream_event_count": upstream_count,
            "synced_event_count": None,
            "lag_events": upstream_count,
            "last_upstream_event_id": last_event_id,
            "synced_last_event_id": None,
            "promoted_candidate_id": promoted_id,
            "best_attempt_id": None,
            "viewer_path": None,
            "viewer_exists": False,
            "synced_at": None,
            "last_error": f"invalid tracing path: {exc}",
            "retry_command": "",
        }
    sync_path = tdir / "harness_sync_state.json"
    sync_state = _read_json_safe(sync_path)
    if sync_state is not None:
        raw_pdir = sync_state.get("project_dir")
        resolved_pdir = None
        if isinstance(raw_pdir, str) and "\x00" not in raw_pdir:
            try:
                resolved_pdir = Path(raw_pdir).resolve()
            except (OSError, RuntimeError, ValueError):
                resolved_pdir = None
        valid_binding = (
            sync_state.get("schema") == SYNC_STATE_SCHEMA
            and sync_state.get("project_id") == store.project_id
            and sync_state.get("trace_id") == cfg["trace_id"]
            and resolved_pdir == store.records.resolve()
        )
        if not valid_binding:
            sync_state = None

    hook_status = None
    try:
        hook_status = _read_json_safe(_status_file(store))
    except (projectctl.Invalid, OSError):
        hook_status = None
    if hook_status is not None and (
        hook_status.get("schema") != HOOK_STATUS_SCHEMA
        or hook_status.get("project_id") != store.project_id
        or hook_status.get("trace_id") != cfg["trace_id"]
    ):
        hook_status = None

    default_viewer = tdir / "index.html"
    viewer_path = default_viewer
    raw_viewer_str = sync_state.get("viewer_path") if sync_state else None
    if isinstance(raw_viewer_str, str) and "\x00" not in raw_viewer_str:
        try:
            raw_viewer = Path(raw_viewer_str)
            if not raw_viewer.is_symlink():
                candidate_viewer = raw_viewer.resolve()
                if candidate_viewer.is_relative_to(tdir):
                    viewer_path = candidate_viewer
        except (OSError, RuntimeError, ValueError):
            pass
    try:
        viewer_exists = viewer_path.exists() and not viewer_path.is_symlink() and viewer_path.is_file()
    except (OSError, ValueError):
        viewer_exists = False

    synced_count = sync_state.get("upstream_event_count") if sync_state else None
    synced_last_id = sync_state.get("last_upstream_event_id") if sync_state else None
    synced_promoted = sync_state.get("promoted_candidate_id") if sync_state else None

    is_synced = (
        sync_state is not None
        and isinstance(synced_count, int)
        and not isinstance(synced_count, bool)
        and synced_count == upstream_count
        and synced_last_id == last_event_id
        and synced_promoted == promoted_id
        and viewer_exists
    )

    last_error = None
    if is_synced:
        state = "synced"
    elif hook_status and hook_status.get("status") == "error":
        state = "error"
        last_error = hook_status.get("error")
    else:
        state = "lagging"

    lag_events = (
        max(0, upstream_count - synced_count)
        if isinstance(synced_count, int) and not isinstance(synced_count, bool)
        else upstream_count
    )

    return {
        "enabled": True,
        "state": state,
        "command": cfg["command"],
        "trace_id": cfg["trace_id"],
        "store_dir": cfg["store_dir"],
        "upstream_event_count": upstream_count,
        "synced_event_count": synced_count,
        "lag_events": lag_events,
        "last_upstream_event_id": last_event_id,
        "synced_last_event_id": synced_last_id,
        "promoted_candidate_id": synced_promoted if is_synced else promoted_id,
        "best_attempt_id": sync_state.get("best_attempt_id") if sync_state else None,
        "viewer_path": str(viewer_path),
        "viewer_exists": viewer_exists,
        "synced_at": sync_state.get("synced_at") if sync_state else None,
        "last_error": last_error,
        "retry_command": retry_cmd,
    }


def needs_sync(store, tree=None):
    status = read_status(store, tree=tree)
    return bool(status.get("enabled") and status.get("state") != "synced")


def sync_after_commit(store, operation, tree=None):
    """Run external `agent-trace sync-harness` after a committed `accept` or `promote-candidate`.

    Never raises to roll back committed Harness state; records structured success/error status instead.
    """
    op_name = operation.get("op") if isinstance(operation, dict) else None
    if store.legacy or op_name not in TRIGGER_OPS:
        return None
    try:
        cfg = load_config(store)
    except (projectctl.Invalid, OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        return {"status": "error", "error": f"invalid tracing config: {exc}", "retry_command": ""}
    if not cfg or not cfg["enabled"]:
        return None

    retry_cmd = ""
    try:
        status_path = _status_file(store)
        with _hook_lock(store.directory):
            latest_tree = store.load() if store.path.exists() else tree
            upstream_count, last_event_id, _ = _tree_watermark(latest_tree)
            error_msg = None
            viewer_path = None
            try:
                _trace_dir(store, cfg)
                retry_cmd = build_retry_command(store, cfg)
                cmd = build_command(store, cfg)
            except (projectctl.Invalid, OSError, RuntimeError, ValueError) as exc:
                error_msg = f"invalid tracing path: {exc}"
            else:
                try:
                    proc = subprocess.run(
                        cmd,
                        cwd=str(store.root),
                        capture_output=True,
                        text=True,
                        timeout=SYNC_TIMEOUT_SECONDS,
                    )
                except FileNotFoundError:
                    error_msg = f"tracing CLI not found: {cmd[0]!r}"
                except subprocess.TimeoutExpired:
                    error_msg = f"tracing sync timed out after {SYNC_TIMEOUT_SECONDS}s"
                except OSError as exc:
                    error_msg = f"failed to invoke tracing CLI: {exc}"
                else:
                    if proc.returncode != 0:
                        detail = (proc.stderr or proc.stdout or "").strip()
                        error_msg = f"sync-harness exited with code {proc.returncode}" + (f": {detail}" if detail else "")
                    else:
                        try:
                            payload = json.loads(proc.stdout)
                        except ValueError as exc:
                            error_msg = f"sync-harness returned invalid JSON: {exc}"
                        else:
                            if not isinstance(payload, dict) or payload.get("project_id") != store.project_id or payload.get("trace_id") != cfg["trace_id"]:
                                error_msg = "sync-harness output identity mismatch"
                            else:
                                viewer_path = payload.get("viewer_path")

            record = {
                "schema": HOOK_STATUS_SCHEMA,
                "project_id": store.project_id,
                "trace_id": cfg["trace_id"],
                "trigger_op": op_name,
                "status": "error" if error_msg else "ok",
                "upstream_event_count": upstream_count,
                "last_upstream_event_id": last_event_id,
                "viewer_path": viewer_path,
                "error": error_msg,
                "retry_command": retry_cmd,
                "checked_at": _now(),
            }
            _atomic_json(status_path, record)
            return record
    except (projectctl.Invalid, OSError, RuntimeError, ValueError) as exc:
        return {"status": "error", "error": str(exc), "retry_command": retry_cmd}
