#!/usr/bin/env python3
"""Project records and cooperative ownership; stdlib, POSIX, no device execution.

Locks serialize tool calls, not arbitrary editor writes. Owner and checkpoint
claims must come from the host; this tool cannot authenticate sessions or jobs.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


from records import Invalid, metadata


def require(condition, message):
    if not condition:
        raise Invalid(message)


def name(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value),
            "name must be 1-64 lowercase letters, digits, underscores or hyphens")
    require(value not in {"environments", "repos", "builds", "runs", "search", "knowledge",
                          "drafts", "research", "codebase", "candidates", "lifecycle"}, "reserved name")
    return value


def nonempty(value):
    require(isinstance(value, str) and bool(value.strip()), "nonempty string required")
    return value


TRACING_SCHEMA = "operator-harness/tracing-config@1"


def default_tracing_store_dir(project_id):
    return f".workspace/{name(project_id)}/tracing"


def tracing_trace_id(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value),
            "invalid tracing trace_id")
    return value


def tracing_command(value):
    nonempty(value)
    require("\x00" not in value and "\n" not in value and "\r" not in value,
            "invalid tracing command")
    return value.strip()


def validate_tracing_schema(project_id, config):
    """Pure structural validation of tracing config (no live filesystem checks)."""
    require(isinstance(config, dict), "invalid tracing config")
    require(config.get("schema") == TRACING_SCHEMA, "unsupported tracing config schema")
    require(type(config.get("enabled")) is bool, "tracing enabled must be a boolean")
    cmd = tracing_command(config.get("command", "agent-trace"))
    tid = tracing_trace_id(config.get("trace_id", f"harness-{project_id}"))
    store_dir = config.get("store_dir", default_tracing_store_dir(project_id))
    require(isinstance(store_dir, str) and bool(store_dir.strip()),
            "tracing store_dir must be a non-empty relative path")
    require("\x00" not in store_dir and "\n" not in store_dir and "\r" not in store_dir,
            "invalid tracing store_dir")
    rel = Path(store_dir.strip())
    require(not rel.is_absolute() and ".." not in rel.parts and rel.parts not in ((), (".",)),
            "tracing store_dir must be workspace-relative")
    return {
        "schema": TRACING_SCHEMA,
        "enabled": config["enabled"],
        "command": cmd,
        "trace_id": tid,
        "store_dir": rel.as_posix(),
    }


def validate_tracing_store_dir(workspace_root, project_id, store_dir):
    """Validate live filesystem containment and symlink safety under `.workspace/<project>/tracing`."""
    rel = Path(store_dir)
    require(not rel.is_absolute() and ".." not in rel.parts and rel.parts not in ((), (".",)),
            "tracing store_dir must be workspace-relative")
    root = Path(workspace_root).resolve()
    raw_target = root / rel
    for part in [raw_target, *raw_target.parents]:
        if part == root:
            break
        require(not part.is_symlink(), "tracing store_dir must not traverse symlinks")
    resolved = raw_target.resolve()
    require(resolved.is_relative_to(root) and resolved != root, "tracing store_dir escapes workspace")
    ws_dir = root / ".workspace"
    require(not ws_dir.is_symlink(), ".workspace must not be a symlink")
    private_root = (ws_dir.resolve() / name(project_id) / "tracing").resolve()
    require(resolved == private_root or resolved.is_relative_to(private_root),
            f"tracing store_dir must stay under .workspace/{project_id}/tracing")
    return resolved


def validate_tracing_config(workspace_root, project_id, config):
    cfg = validate_tracing_schema(project_id, config)
    validate_tracing_store_dir(workspace_root, project_id, cfg["store_dir"])
    return cfg


def resolve_tracing_command(workspace_root, command):
    """Return `(invocation_executable, resolved_existing_path_or_none)` consistently across callers.

    Bare executable names stay bare for invocation and are searched on PATH.
    Path-valued commands expand `~`, resolve relative paths against `workspace_root`,
    and reuse the exact resolved path for status checks, execution, and retry commands.
    """
    has_sep = os.sep in command or (os.altsep is not None and os.altsep in command)
    if not has_sep:
        return command, shutil.which(command)
    try:
        raw = Path(command).expanduser()
    except RuntimeError as exc:
        raise Invalid(f"invalid tracing command path: {exc}") from exc
    if not raw.is_absolute():
        raw = Path(workspace_root).resolve() / raw
    resolved = raw.resolve()
    found = str(resolved) if (resolved.is_file() and os.access(resolved, os.X_OK)) else None
    return str(resolved), found


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def lock(path):
    require(not path.is_symlink(), "lock must not be a symlink")
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def git(path, *args):
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    require(result.returncode == 0, result.stderr.strip() or "git failed")
    return result.stdout.strip()


class Workspace:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve(strict=True)
        require(self.root.is_dir(), "workspace root must be a directory")
        self.directory = self.root / ".workspace"
        require(not self.directory.is_symlink(), ".workspace must not be a symlink")

    def project(self, project_id):
        path = self.directory / name(project_id)
        require(not path.is_symlink(), "project must not be a symlink")
        return path

    def load(self, project_id):
        path = self.project(project_id) / "project.json"
        require(not path.is_symlink(), "metadata must not be a symlink")
        data = read(path)
        require(data["schema"] == "operator-harness/project@1", "unsupported project schema")
        require(data["id"] == project_id, "project identity mismatch")
        require(data["status"] in {"active", "freezing", "frozen", "archived"}, "invalid lifecycle state")
        require(isinstance(data["repositories"], dict) and isinstance(data["events"], list), "invalid metadata")
        require(data["owner"] is None or isinstance(data["owner"], str), "invalid owner")
        if "tracing" in data and data["tracing"] is not None:
            validate_tracing_schema(project_id, data["tracing"])
        return data

    def list(self, all_projects=False):
        projects = []
        if self.directory.exists():
            for path in sorted(self.directory.iterdir()):
                if not path.is_dir() or not (path / "project.json").exists():
                    continue
                data = self.load(path.name)
                if all_projects or data["status"] in {"active", "freezing"}:
                    projects.append({key: data[key] for key in ("id", "title", "status", "owner")})
        return projects

    def resolve(self, project_id=None):
        if project_id:
            self.load(project_id)
            return project_id
        projects = [p for p in self.list() if p["status"] == "active"]
        require(len(projects) == 1, "specify --project; no unique active project")
        return projects[0]["id"]

    def save(self, data, action, detail=None):
        data["events"].append({"sequence": len(data["events"]) + 1,
                               "at": datetime.now(timezone.utc).isoformat(),
                               "action": action, "owner": data["owner"], "detail": detail})
        atomic(self.project(data["id"]) / "project.json", data)

    def create(self, project_id, title, owner):
        path = self.project(project_id)
        nonempty(title)
        nonempty(owner)
        self.directory.mkdir(exist_ok=True)
        # mkdir is exclusive across sessions; never overwrite an incomplete create.
        path.mkdir()
        data = {"schema": "operator-harness/project@1", "id": project_id,
                "title": title, "status": "active", "owner": owner,
                "repositories": {}, "events": []}
        self.save(data, "create")
        return data

    @contextmanager
    def edit(self, project_id, owner, states=("active",)):
        path = self.project(project_id)
        require(path.is_dir(), "project not found")
        with lock(path / ".project.lock"):
            data = self.load(project_id)
            require(data["status"] in states, "project is not writable in this lifecycle state")
            require(nonempty(owner) == data["owner"], "project is owned by another session; claim or hand off first")
            yield data

    def claim(self, project_id, owner, previous=None, evidence=None):
        nonempty(owner)
        with lock(self.project(project_id) / ".project.lock"):
            data = self.load(project_id)
            if data["owner"] == owner:
                return data
            if data["owner"] is not None:
                require(previous == data["owner"], "owner conflict; explicit previous owner and handoff evidence required")
                ref, checkpoint = self.checkpoint(project_id, evidence)
                require(checkpoint.get("previous_owner_stopped") is True, "previous writer not confirmed stopped")
            else:
                ref = None
            old = data["owner"]
            data["owner"] = owner
            self.save(data, "claim", {"previous": old, "evidence": ref})
            return data

    def local(self, project_id, relative):
        require(isinstance(relative, str) and not Path(relative).is_absolute(), "expected project-relative path")
        base = self.project(project_id)
        path = base / relative
        require(path.resolve().is_relative_to(base.resolve()), "path escapes project")
        # Managed records/outputs must not traverse symlink aliases.
        require(not any(p.is_symlink() for p in [path, *path.parents] if p != self.root.parent),
                "managed path must not traverse symlinks")
        return path

    def checkpoint(self, project_id, relative):
        path = self.local(project_id, nonempty(relative))
        data = read(path)
        require(data.get("schema") == "operator-harness/project-checkpoint@1" and
                data.get("project_id") == project_id, "checkpoint identity/schema mismatch")
        nonempty(data.get("summary"))
        return {"path": relative, "sha256": sha(path)}, data

    def inventory(self, project_id, data):
        result = {}
        for key, repo in data["repositories"].items():
            path = self.local(project_id, repo["path"])
            require(repo["state"] == "ready", "repository preparation incomplete; recover add-repo first")
            require(path.is_dir(), f"repository missing: {key}")
            require(Path(git(path, "rev-parse", "--show-toplevel")).resolve() == path.resolve(), "repository root mismatch")
            branch = git(path, "symbolic-ref", "--short", "HEAD")
            require(branch == repo["branch"], f"repository branch changed: {key}")
            untracked = git(path, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
            result[key] = {"head": git(path, "rev-parse", "HEAD"), "branch": branch,
                           "status": git(path, "status", "--porcelain=v1"),
                           "tracked_patch": git(path, "diff", "--binary", "HEAD"),
                           "untracked": {p: sha(path / p) for p in untracked if p and (path / p).is_file()}}
        return result

    def add_repo(self, project_id, owner, repo_id, source, revision, branch=None):
        name(repo_id)
        source = Path(source).expanduser().resolve(strict=True)
        branch = branch or f"codex/{project_id}/{repo_id}"
        git(source, "check-ref-format", "--branch", branch)
        base = git(source, "rev-parse", "--verify", f"{revision}^{{commit}}")
        with self.edit(project_id, owner) as data:
            dest = self.local(project_id, f"repos/{repo_id}")
            expected = {"path": f"repos/{repo_id}", "source": str(source), "base": base,
                        "branch": branch, "state": "preparing"}
            old = data["repositories"].get(repo_id)
            if old:
                require({**old, "state": "preparing"} == expected, "repository registration conflict")
                if old["state"] == "ready":
                    self.inventory(project_id, {"repositories": {repo_id: old}})
                    return old
            else:
                require(not dest.exists(), "repository destination already exists")
                data["repositories"][repo_id] = expected
                self.save(data, "prepare-repository", {"repository": repo_id})
            dest.parent.mkdir(parents=True, exist_ok=True)
            registered = git(source, "worktree", "list", "--porcelain")
            if f"worktree {dest}\n" not in registered + "\n":
                require(not dest.exists(), "partial destination exists; inspect before recovery")
                git(source, "worktree", "add", "-b", branch, str(dest), base)
            require(git(dest, "rev-parse", "HEAD") == base, "interrupted worktree no longer matches requested base")
            require(git(dest, "symbolic-ref", "--short", "HEAD") == branch, "worktree branch mismatch")
            expected["state"] = "ready"
            data["repositories"][repo_id] = expected
            self.save(data, "repository-ready", {"repository": repo_id})
            return expected

    def begin_close(self, project_id, owner, target):
        require(target in {"frozen", "archived"}, "invalid close target")
        with self.edit(project_id, owner, ("active", "freezing")) as data:
            if data["status"] == "freezing":
                require(data["close_target"] == target, "already closing with a different target")
                return data
            data.update(status="freezing", close_target=target)
            self.save(data, "begin-close", target)
            return data

    def finish_close(self, project_id, owner, evidence):
        with self.edit(project_id, owner, ("freezing",)) as data:
            ref, check = self.checkpoint(project_id, evidence)
            require(check.get("writers_stopped") is True and check.get("jobs") == [] and
                    check.get("unsettled_resources") == [] and check.get("open_runs") == [],
                    "checkpoint has unconfirmed writers, jobs, runs or resources")
            tree = self.local(project_id, "search/tree.json")
            paused = {}
            if tree.exists():
                search = read(tree)
                control = search.get("control", {})
                require(all(job["status"] in ("exited", "cancelled", "not_submitted")
                            for job in control.get("jobs", {}).values()) and
                        all(item["settled"] for item in control.get("reservations", {}).values()),
                        "search control has outstanding jobs or resources")
                held = {run_id: lease['generation'] for run_id, lease in
                        search.get('scheduling', {}).get('leases', {}).items() if not lease['released']}
                require(not held or check.get('stopped_workspace_leases') == held,
                        'checkpoint must confirm every held workspace generation has stopped')
                for run_id, run in search["runs"].items():
                    if run.get("result") or run.get("closure"):
                        continue
                    relative = check.get("paused_runs", {}).get(run_id)
                    require(relative, "search contains outstanding runs without paused checkpoints")
                    path = self.local(project_id, relative)
                    fields = metadata(path.read_text(encoding="utf-8"))
                    require(fields.get("run_id") == run_id and fields.get("status") in
                            ("interrupted", "blocked", "needs_input"), "invalid paused run checkpoint")
                    paused[run_id] = {"path": relative, "sha256": sha(path)}
            snapshot = {"checkpoint": ref, "paused_runs": paused,
                        "repositories": self.inventory(project_id, data)}
            data["closed_snapshot"] = snapshot
            data["status"] = data.pop("close_target")
            self.save(data, "close", snapshot)
            return data

    def restore(self, project_id, owner, evidence):
        with self.edit(project_id, owner, ("frozen", "archived")) as data:
            for ref in [data["closed_snapshot"]["checkpoint"],
                        *data["closed_snapshot"].get("paused_runs", {}).values()]:
                require(sha(self.local(project_id, ref["path"])) == ref["sha256"],
                        "closed checkpoint changed; preserve and investigate before restoring")
            ref, check = self.checkpoint(project_id, evidence)
            require(check.get("jobs") == [] and check.get("unsettled_resources") == [] and
                    check.get("open_runs") == [] and check.get("reviewed_changes") is True,
                    "restore requires job reconciliation and explicit review of changed inputs")
            inventory = self.inventory(project_id, data)
            changes = inventory != data["closed_snapshot"]["repositories"]
            data["status"] = "active"
            self.save(data, "restore", {"checkpoint": ref, "repositories_changed": changes,
                                        "repositories": inventory})
            return data

    def tracing_status(self, project_id):
        data = self.load(project_id)
        raw = data.get("tracing")
        if not raw:
            _, cli_path = resolve_tracing_command(self.root, "agent-trace")
            return {
                "project_id": project_id,
                "enabled": False,
                "config": None,
                "cli_found": bool(cli_path),
                "cli_path": cli_path,
                "cli_error": None,
                "store_dir_error": None,
            }
        cfg = validate_tracing_schema(project_id, raw)
        cli_path = None
        cli_error = None
        try:
            _, cli_path = resolve_tracing_command(self.root, cfg["command"])
        except (Invalid, OSError, RuntimeError, ValueError) as exc:
            cli_error = str(exc)
        store_dir_error = None
        if cfg["enabled"]:
            try:
                validate_tracing_store_dir(self.root, project_id, cfg["store_dir"])
            except (Invalid, OSError, ValueError) as exc:
                store_dir_error = str(exc)
        return {
            "project_id": project_id,
            "enabled": cfg["enabled"],
            "config": cfg,
            "cli_found": bool(cli_path),
            "cli_path": cli_path,
            "cli_error": cli_error,
            "store_dir_error": store_dir_error,
        }

    def set_tracing(self, project_id, owner, enabled, command=None, trace_id=None, store_dir=None):
        require(type(enabled) is bool, "enabled must be a boolean")
        with self.edit(project_id, owner) as data:
            prior = data.get("tracing") if isinstance(data.get("tracing"), dict) else {}
            candidate = {
                "schema": TRACING_SCHEMA,
                "enabled": enabled,
                "command": command if command is not None else prior.get("command", "agent-trace"),
                "trace_id": trace_id if trace_id is not None else prior.get("trace_id", f"harness-{project_id}"),
                "store_dir": store_dir if store_dir is not None else prior.get("store_dir", default_tracing_store_dir(project_id)),
            }
            cfg = validate_tracing_schema(project_id, candidate)
            cli_path = None
            if enabled:
                validate_tracing_store_dir(self.root, project_id, cfg["store_dir"])
                _, cli_path = resolve_tracing_command(self.root, cfg["command"])
            else:
                try:
                    _, cli_path = resolve_tracing_command(self.root, cfg["command"])
                except (Invalid, OSError, RuntimeError, ValueError):
                    cli_path = None
            data["tracing"] = cfg
            self.save(data, "tracing-enable" if enabled else "tracing-disable", cfg)
            return {
                "project_id": project_id,
                "enabled": cfg["enabled"],
                "config": cfg,
                "cli_found": bool(cli_path),
                "cli_path": cli_path,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="explicit workspace directory; never inferred from Git")
    parser.add_argument("--project")
    parser.add_argument("--owner", help="host session/coordinator ID, not authentication")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--title", required=True)
    listing = commands.add_parser("list")
    listing.add_argument("--all", action="store_true")
    commands.add_parser("show")
    commands.add_parser("inspect")
    claim = commands.add_parser("claim")
    claim.add_argument("--previous-owner")
    claim.add_argument("--checkpoint")
    repo = commands.add_parser("add-repo")
    repo.add_argument("--name", required=True)
    repo.add_argument("--source", required=True)
    repo.add_argument("--revision", required=True)
    repo.add_argument("--branch")
    close = commands.add_parser("begin-close")
    close.add_argument("--target", choices=("frozen", "archived"), required=True)
    for command in ("finish-close", "restore"):
        commands.add_parser(command).add_argument("--checkpoint", required=True)
    tracing = commands.add_parser("tracing")
    tracing_sub = tracing.add_subparsers(dest="tracing_command", required=True)
    tr_enable = tracing_sub.add_parser("enable")
    tr_enable.add_argument("--command", dest="tracing_cli", help="agent-trace executable name or path (default: agent-trace)")
    tr_enable.add_argument("--trace-id", help="destination trace ID (default: harness-<project>)")
    tr_enable.add_argument("--store-dir", help="workspace-relative trace store dir (default: .workspace/<project>/tracing)")
    tracing_sub.add_parser("disable")
    tracing_sub.add_parser("status")
    args = parser.parse_args()
    try:
        workspace = Workspace(args.workspace)
        if args.command == "list":
            result = {"projects": workspace.list(args.all),
                      "legacy_present": (workspace.directory / "STATE.md").exists()}
        elif args.command == "create":
            result = workspace.create(args.project, args.title, args.owner)
        else:
            project = workspace.resolve(args.project)
            if args.command == "show":
                result = workspace.load(project)
            elif args.command == "inspect":
                data = workspace.load(project)
                result = {"project": data, "repositories": workspace.inventory(project, data)}
            elif args.command == "claim":
                result = workspace.claim(project, args.owner, args.previous_owner, args.checkpoint)
            elif args.command == "add-repo":
                result = workspace.add_repo(project, args.owner, args.name, args.source, args.revision, args.branch)
            elif args.command == "begin-close":
                result = workspace.begin_close(project, args.owner, args.target)
            elif args.command == "finish-close":
                result = workspace.finish_close(project, args.owner, args.checkpoint)
            elif args.command == "tracing":
                if args.tracing_command == "status":
                    result = workspace.tracing_status(project)
                elif args.tracing_command == "enable":
                    result = workspace.set_tracing(
                        project, args.owner, True,
                        command=args.tracing_cli,
                        trace_id=args.trace_id,
                        store_dir=args.store_dir,
                    )
                else:
                    result = workspace.set_tracing(project, args.owner, False)
            else:
                result = workspace.restore(project, args.owner, args.checkpoint)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (Invalid, OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"projectctl: {error}\n")


if __name__ == "__main__":
    main()
