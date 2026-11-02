#!/usr/bin/env python3
"""Agent-facing operator exploration CLI. JSON stdout; no model/runtime dependency."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Failure(Exception):
    def __init__(self, code, message, exit_code=2, **details):
        self.code, self.message, self.exit_code, self.details = (
            code,
            message,
            exit_code,
            details,
        )
        super().__init__(message)


def read_file(path):
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise Failure("YAML_UNAVAILABLE", "Install PyYAML, or provide JSON")
        try:
            value = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise Failure("INVALID_INPUT", str(error)) from error
    else:
        value = json.loads(text)
    if not isinstance(value, dict):
        raise Failure("INVALID_INPUT", "Expected a JSON/YAML object")
    return value


def write_json(path, data, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    else:
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def camel(data):
    # Only translate protocol keys; arbitrary mappings such as source IDs are data.
    aliases = {
        "parent_ids": "parentIds",
        "source_contributions": "sourceContributions",
        "change_summary": "changeSummary",
        "derivation_note": "derivationNote",
        "depends_on": "dependsOnAttemptIds",
        "input_versions": "inputResourceVersionIds",
        "output_versions": "outputResourceVersionIds",
        "kernel_version_id": "kernelVersionId",
        "correctness_passed": "correctnessPassed",
        "latency_us": "latencyUs",
        "reason": "failureOrSkipReason",
        "source_type": "sourceType",
        "source_id": "sourceId",
        "target_type": "targetType",
        "target_id": "targetId",
        "resource_version_id": "resourceVersionId",
        "knowledge_version": "knowledgeVersion",
        "evidence_attempt_ids": "evidenceAttemptIds",
        "claim_type": "claimType",
        "content_status": "contentStatus",
        "unverified_scope": "unverifiedScope",
        "supporting_node_ids": "supportingNodeIds",
        "counterexample_node_ids": "counterexampleNodeIds",
        "best_node_id": "bestNodeId",
        "evidence_versions": "evidenceVersions",
        "open_questions": "openQuestions",
        "suggested_next_steps": "suggestedNextSteps",
        "in_flight": "inFlight",
        "node_ids": "nodeIds",
        "resource_version_ids": "resourceVersionIds",
        "knowledge_ids": "knowledgeIds",
    }
    return {aliases.get(k, k): v for k, v in data.items()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, args):
        self.args = args
        self.context_path = (
            Path(args.context or os.environ.get("OPWORK_CONTEXT", ""))
            .expanduser()
            .resolve()
            if (args.context or os.environ.get("OPWORK_CONTEXT"))
            else None
        )
        self.binding = (
            read_file(self.context_path)
            if self.context_path and self.context_path.exists()
            else {}
        )
        self.url = (
            args.url
            or os.environ.get("OPWORK_URL")
            or self.binding.get("url")
            or "http://127.0.0.1:8000"
        ).rstrip("/")
        parsed = urllib.parse.urlsplit(self.url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise Failure(
                "INVALID_URL",
                "Use an HTTP(S) service URL without credentials, query or fragment",
            )
        if self.binding.get("url") and self.url != self.binding["url"]:
            raise Failure("CONTEXT_MISMATCH", "Service differs from bound context")
        self.request_id = None

    def project(self):
        value = self.binding.get("projectId")
        if not value:
            raise Failure(
                "NEEDS_CONTEXT", "Use session open with a distinct --context file"
            )
        return value

    def session(self):
        if self.binding.get("closed"):
            raise Failure("SESSION_CLOSED", "Open a new session context")
        if not self.binding.get("sessionId"):
            raise Failure("NEEDS_CONTEXT", "Register this session first")
        return self.binding["sessionId"]

    def prefix(self, project=None):
        return "/api/explorations/" + urllib.parse.quote(
            project or self.project(), safe=""
        )

    def journal(self, identity):
        directory = (
            self.context_path.parent / ".opwork-requests"
            if self.context_path
            else Path(
                os.environ.get(
                    "OPWORK_STATE_DIR", str(Path.home() / ".local/state/opwork")
                )
            )
        )
        return directory / (hashlib.sha256(identity.encode()).hexdigest() + ".json")

    def http(
        self, method, path, body=None, operator=False, request_id=None, journal=True
    ):
        headers = {"Accept": "application/json"}
        if method != "GET":
            key = "OPWORK_OPERATOR_TOKEN" if operator else "OPWORK_TOKEN"
            if not os.environ.get(key):
                raise Failure("NEEDS_CREDENTIAL", f"Set {key}", 3)
            headers["X-Operator-Token" if operator else "X-Agent-Token"] = os.environ[
                key
            ]
            self.request_id = (
                request_id or self.args.request_id or "req-" + uuid.uuid4().hex
            )
            headers["X-Request-Id"] = self.request_id
            if journal:
                record = {
                    "url": self.url,
                    "method": method,
                    "path": path,
                    "body": body,
                    "operator": operator,
                    "requestId": self.request_id,
                    "state": "pending",
                }
                dest = self.journal(self.request_id)
                if dest.exists():
                    old = read_file(dest)
                    if any(
                        old.get(k) != record[k]
                        for k in ("url", "method", "path", "body", "operator")
                    ):
                        raise Failure(
                            "REQUEST_CONFLICT",
                            "Request ID already used with different payload",
                            4,
                        )
                else:
                    write_json(dest, record, exclusive=True)
        elif os.environ.get("OPWORK_TOKEN"):
            headers["X-Agent-Token"] = os.environ["OPWORK_TOKEN"]
        raw = (
            json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        )
        if raw is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.url + path, data=raw, headers=headers, method=method
        )
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as res:
                content = res.read()
                result = (
                    json.loads(content)
                    if "application/json" in res.headers.get("Content-Type", "")
                    else content
                )
        except urllib.error.HTTPError as error:
            try:
                detail = json.loads(error.read()).get("detail", str(error))
            except Exception:
                detail = str(error.code)
            raise Failure(
                "HTTP_" + str(error.code),
                str(detail),
                3 if error.code in (401, 403) else 4 if error.code in (409, 422) else 5,
                http_status=error.code,
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            raise Failure(
                "OUTCOME_UNKNOWN" if method != "GET" else "CONNECTION_FAILED",
                str(error),
                5,
                request_id=self.request_id,
            )
        if method != "GET" and journal:
            record["state"] = "committed"
            record["result"] = result
            write_json(dest, record)
        return result

    def snapshot(self):
        return self.http("GET", self.prefix() + "/snapshot")

    def attempt(self, identity):
        for n in self.snapshot()["nodes"]:
            for stage in n["stages"].values():
                for attempt in stage["attempts"]:
                    if attempt["id"] == identity:
                        return n["id"], attempt
        raise Failure("NOT_FOUND", "Attempt not found", 4)


def heartbeat(context):
    # Separate process: heartbeat continues during long external compiler/device calls.
    args = argparse.Namespace(context=context, url=None, request_id=None)
    client = Client(args)
    while True:
        try:
            if not Path(context).exists():
                return
            binding = read_file(context)
            if binding.get("closed"):
                return
            result = client.http(
                "POST",
                client.prefix() + "/agents/heartbeat",
                {"id": client.session()},
                journal=False,
            )
            write_json(
                str(context) + ".heartbeat.json",
                {"updatedAt": time.time(), "pid": os.getpid(), **result},
            )
        except Failure as error:
            if error.code == "HTTP_409":
                return
            write_json(
                str(context) + ".heartbeat.json",
                {
                    "updatedAt": time.time(),
                    "pid": os.getpid(),
                    "error": str(error),
                    "directive": "unknown",
                },
            )
        except Exception as error:
            write_json(
                str(context) + ".heartbeat.json",
                {
                    "updatedAt": time.time(),
                    "pid": os.getpid(),
                    "error": str(error),
                    "directive": "unknown",
                },
            )
        time.sleep(5)


def execute(c, a):
    group, action = a.group, getattr(a, "action", None)
    if group == "doctor":
        return c.http("GET", "/api/capabilities")
    if group == "project":
        if action == "list":
            return c.http("GET", "/api/explorations")
        if action == "create":
            task = read_file(a.file)
            name = task.get("operatorName") or task.get("operator")
            if not name:
                raise Failure("INVALID_INPUT", "operator required")
            budget = task.get("budget", {})
            body = {
                "id": task.get("id") or "exp-" + uuid.uuid4().hex,
                "title": task.get("title", name),
                "operatorName": name,
                "targetGoal": task.get("goal", task.get("targetGoal", "")),
                "inputDomain": task.get("inputDomain")
                or "\n".join(task.get("input_shapes", [])),
                "environment": task.get("environment", ""),
                "baselineRef": task.get(
                    "baselineRef",
                    {"id": "", "version": "", "latencyUs": 0, "description": ""},
                ),
                "budget": {
                    "usedEvaluations": 0,
                    "reservedEvaluations": 0,
                    "maxEvaluations": budget.get("max_evaluations", 24),
                    "maxMinutes": budget.get("max_minutes", 60),
                    "elapsedMinutes": 0,
                    "noGainWindow": 3,
                    "minGainPct": 2,
                },
                "bestValidNodeId": "",
            }
            result = c.http("POST", "/api/explorations", body)
            # Keep the complete contract as immutable evidence; a partial upload is explicit.
            content = Path(a.file).read_text(encoding="utf-8")
            try:
                contract = c.http(
                    "POST",
                    c.prefix(body["id"]) + "/resources",
                    dict(
                        id="contract-v1",
                        resourceId="contract",
                        version="v1",
                        title="任务契约",
                        filename=Path(a.file).name,
                        category="artifact",
                        ownerNodeId="",
                        content=content,
                    ),
                    request_id="contract-" + result["explorationId"],
                )
            except Failure as error:
                raise Failure(
                    "PARTIAL_CREATE",
                    f"Project created but contract upload failed: {error.message}",
                    5,
                    project_id=result["explorationId"],
                )
            return {
                **result,
                "projectId": result["explorationId"],
                "contract": contract,
            }
        if action == "export":
            snapshot = c.snapshot()
            # Include bytes for binary resources, rather than exporting only preview text.
            for resource in snapshot["resources"].values():
                if resource.get("isBinary") and not resource.get("missingReason"):
                    raw = c.http(
                        "GET",
                        c.prefix()
                        + "/resources/"
                        + urllib.parse.quote(resource["id"], safe="")
                        + "/download",
                    )
                    resource["binaryBase64"] = base64.b64encode(raw).decode()
            write_json(
                a.output,
                {"format": "workbench-snapshot", "version": 1, "exploration": snapshot},
                exclusive=True,
            )
            return {
                "output": str(Path(a.output).resolve()),
                "observedEventSeq": snapshot["lastEventSeq"],
            }
        if action == "fork":
            return c.http(
                "POST",
                c.prefix(a.source) + "/workspace/fork",
                {"checkpointId": a.checkpoint, "name": a.name},
            )
        if action == "pause":
            return c.http(
                "POST", c.prefix() + "/stop", {"reason": a.reason}, operator=True
            )
        if action == "resume":
            return c.http(
                "POST",
                c.prefix() + "/resume",
                {"expectedVersion": a.expected_version},
                operator=True,
            )
        if action == "import":
            return c.http("POST", "/api/projects/import", read_file(a.file))
        raise Failure(
            "UNSUPPORTED",
            "project configure is not implemented; create a new versioned contract resource",
            6,
        )
    if group == "session":
        if action == "open":
            if not c.context_path:
                raise Failure("NEEDS_CONTEXT", "Provide a distinct --context PATH")
            if c.context_path.exists():
                raise Failure(
                    "CONTEXT_EXISTS", "Context exists; do not overwrite another session"
                )
            sid = a.resume_session or "session-" + uuid.uuid4().hex
            result = c.http(
                "POST",
                c.prefix(a.project) + "/agents/heartbeat",
                {"id": sid, "name": a.name, "role": a.role},
            )
            write_json(
                c.context_path,
                {
                    "url": c.url,
                    "projectId": a.project,
                    "sessionId": sid,
                    "closed": False,
                },
                exclusive=True,
            )
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "__heartbeat",
                    str(c.context_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {**result, "context": str(c.context_path), "helperPid": proc.pid}
        if action == "close":
            result = c.http(
                "POST",
                c.prefix() + "/workspace/session-close",
                {"sessionId": c.session(), "handoffId": a.handoff},
            )
            write_json(c.context_path, {**c.binding, "closed": True})
            return result
    if group == "context":
        result = c.http("GET", c.prefix() + "/context")
        status = Path(str(c.context_path) + ".heartbeat.json")
        result["heartbeat"] = (
            read_file(status) if status.exists() else {"directive": "unknown"}
        )
        return result
    if group == "node":
        if action == "create":
            c.session()
            body = camel(read_file(a.file))
            body.setdefault("id", "node-" + uuid.uuid4().hex)
            body.setdefault("title", body.get("hypothesis", "方案"))
            body.setdefault("branchLabel", "")
            body["agentIds"] = [c.session()]
            return c.http("POST", c.prefix() + "/nodes", body)
        nodes = c.snapshot()["nodes"]
        if action == "list":
            return {
                "items": [
                    {
                        "id": n["id"],
                        "title": n["title"],
                        "parentIds": n["parentIds"],
                        "status": n["executionStatus"],
                    }
                    for n in nodes
                ]
            }
        return (
            next((n for n in nodes if n["id"] == a.identity), None) or fail_not_found()
        )
    if group == "resource":
        if action == "put":
            c.session()
            raw = Path(a.file).read_bytes()
            identity = "res-" + uuid.uuid4().hex
            body = {
                "id": identity,
                "resourceId": identity,
                "version": a.version,
                "filename": Path(a.file).name,
                "title": a.title or Path(a.file).name,
                "category": a.category,
                "ownerNodeId": a.node,
                "expectedSha256": hashlib.sha256(raw).hexdigest(),
            }
            try:
                body["content"] = raw.decode("utf-8")
            except UnicodeDecodeError:
                body.update(
                    isBinary=True,
                    binaryBase64=base64.b64encode(raw).decode(),
                    content="",
                )
            if a.metadata:
                metadata = read_file(a.metadata)
                if set(metadata) - {"experimentData", "profileData"}:
                    raise Failure(
                        "INVALID_INPUT",
                        "Metadata accepts experimentData/profileData only",
                    )
                body.update(metadata)
            return c.http("POST", c.prefix() + "/resources", body)
        resource = c.snapshot()["resources"].get(a.identity)
        if not resource:
            return fail_not_found()
        raw = c.http(
            "GET",
            c.prefix()
            + "/resources/"
            + urllib.parse.quote(a.identity, safe="")
            + "/download",
        )
        if not isinstance(raw, bytes):
            raise Failure("INVALID_DOWNLOAD", "Expected resource bytes", 5)
        if hashlib.sha256(raw).hexdigest() != resource["sha256"]:
            raise Failure(
                "HASH_MISMATCH", "Downloaded content differs from stored version", 4
            )
        with open(a.output, "wb" if a.overwrite else "xb") as f:
            f.write(raw)
        return {"output": a.output, "sha256": resource["sha256"]}
    if group == "attempt":
        if action == "show":
            return c.attempt(a.identity)[1]
        body = camel(read_file(a.file))
        if action == "start":
            sid = c.session()
            status = Path(str(c.context_path) + ".heartbeat.json")
            if (
                not status.exists()
                or time.time() - read_file(status).get("updatedAt", 0) > 20
                or read_file(status).get("directive") != "continue"
            ):
                raise Failure(
                    "HEARTBEAT_NOT_READY",
                    "Heartbeat is missing, stale or says stop; inspect context before starting",
                    4,
                )
            body.update(
                id=body.get("id") or "attempt-" + uuid.uuid4().hex,
                agentId=sid,
                stageType=a.stage,
            )
            body.setdefault("code", body["id"])
            kernel = body.pop("kernelVersionId", None)
            if kernel:
                resource = c.snapshot()["resources"].get(kernel)
                if not resource or resource["category"] != "kernel":
                    raise Failure(
                        "INVALID_INPUT", "Exact kernel resource version required"
                    )
                body["inputResourceVersionIds"] = list(
                    dict.fromkeys(body.get("inputResourceVersionIds", []) + [kernel])
                )
                body.setdefault("kernelVersionLabel", resource["version"])
            return c.http(
                "POST",
                c.prefix()
                + "/nodes/"
                + urllib.parse.quote(a.node, safe="")
                + "/attempts",
                body,
            )
        node, attempt = c.attempt(a.identity)
        if action == "reconcile":
            body["expectedVersion"] = a.expected_version
            return c.http(
                "POST",
                c.prefix()
                + "/nodes/"
                + urllib.parse.quote(node, safe="")
                + "/attempts/"
                + urllib.parse.quote(a.identity, safe="")
                + "/reconcile",
                body,
                operator=True,
            )
        if attempt["agentId"] != c.session():
            raise Failure(
                "EXECUTOR_MISMATCH",
                "Only the original session can finish; use an operator to reconcile interrupted work",
                4,
            )
        body["expectedVersion"] = a.expected_version
        return c.http(
            "PATCH",
            c.prefix()
            + "/nodes/"
            + urllib.parse.quote(node, safe="")
            + "/attempts/"
            + urllib.parse.quote(a.identity, safe=""),
            body,
        )
    if group in ("relation", "knowledge"):
        c.session()
        body = camel(read_file(a.file))
        body.setdefault("id", group + "-" + uuid.uuid4().hex)
        if group == "knowledge":
            body.setdefault("version", "v1")
            body.setdefault("applicability", "")
            body.setdefault("unverifiedScope", "")
            body.setdefault("category", "optimization_experience")
            body.setdefault("claimType", "hypothesis")
        return c.http(
            "POST",
            c.prefix() + ("/relations" if group == "relation" else "/knowledge"),
            body,
        )
    if group == "candidate":
        c.session()
        return c.http("POST", c.prefix() + "/candidates/mark", camel(read_file(a.file)))
    if group == "handoff":
        body = camel(read_file(a.file))
        body["sessionId"] = c.session()
        return c.http("POST", c.prefix() + "/workspace/handoff", body)
    if group == "checkpoint":
        return c.http("POST", c.prefix() + "/workspace/checkpoint", {"name": a.name})
    if group == "provenance":
        snapshot = c.snapshot()
        return {
            "knowledge": next(
                (k for k in snapshot["knowledgeItems"] if k["id"] == a.knowledge), None
            ),
            "relations": [
                r
                for r in snapshot["graphRelations"]
                if (r["sourceType"] == "knowledge" and r["sourceId"] == a.knowledge)
                or (r["targetType"] == "knowledge" and r["targetId"] == a.knowledge)
            ],
            "observedEventSeq": snapshot["lastEventSeq"],
        }
    if group == "request":
        if action == "show":
            return c.http(
                "GET",
                c.prefix() + "/requests/" + urllib.parse.quote(a.identity, safe=""),
            )
        record = read_file(c.journal(a.identity))
        if record["url"] != c.url:
            raise Failure("CONTEXT_MISMATCH", "Request belongs to another service", 4)
        return c.http(
            record["method"],
            record["path"],
            record["body"],
            record["operator"],
            request_id=record["requestId"],
        )
    raise Failure("UNSUPPORTED", "Unsupported command", 6)


def fail_not_found():
    raise Failure("NOT_FOUND", "Object not found", 4)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("INVALID_ARGUMENT", message)


def parser():
    p = Parser(description="Persistent operator exploration CLI")
    p.add_argument("--url")
    p.add_argument("--context")
    p.add_argument("--request-id")
    groups = p.add_subparsers(dest="group", required=True)
    groups.add_parser("doctor")
    groups.add_parser("context")

    def command(group, names):
        g = groups.add_parser(group)
        sub = g.add_subparsers(dest="action", required=True)
        return {name: sub.add_parser(name) for name in names}

    project = command(
        "project",
        ["create", "list", "import", "export", "fork", "pause", "resume", "configure"],
    )
    for name in ("create", "import", "configure"):
        project[name].add_argument("--file", required=True)
    project["export"].add_argument("--output", required=True)
    project["fork"].add_argument("--from", dest="source", required=True)
    project["fork"].add_argument("--checkpoint", required=True)
    project["fork"].add_argument("--name", required=True)
    project["pause"].add_argument("--reason", required=True)
    project["resume"].add_argument("--expected-version", type=int, required=True)
    session = command("session", ["open", "close"])
    for flag in ("project", "name"):
        session["open"].add_argument("--" + flag, required=True)
    session["open"].add_argument("--role", default="exploration")
    session["open"].add_argument("--resume-session")
    session["close"].add_argument("--handoff", required=True)
    node = command("node", ["create", "list", "show"])
    node["create"].add_argument("--file", required=True)
    node["show"].add_argument("identity")
    resource = command("resource", ["put", "get"])
    for flag in ("file", "category", "node"):
        resource["put"].add_argument("--" + flag, required=True)
    resource["put"].add_argument("--version", default="v1")
    resource["put"].add_argument("--title")
    resource["put"].add_argument("--metadata")
    resource["get"].add_argument("identity")
    resource["get"].add_argument("--output", required=True)
    resource["get"].add_argument("--overwrite", action="store_true")
    attempt = command("attempt", ["start", "finish", "show", "reconcile"])
    for name in ("start", "finish", "reconcile"):
        attempt[name].add_argument("--file", required=True)
    attempt["start"].add_argument("--node", required=True)
    attempt["start"].add_argument("--stage", required=True)
    attempt["finish"].add_argument("identity")
    attempt["finish"].add_argument("--expected-version", type=int, required=True)
    attempt["show"].add_argument("identity")
    attempt["reconcile"].add_argument("identity")
    attempt["reconcile"].add_argument("--expected-version", type=int, required=True)
    for group, action in [
        ("relation", "add"),
        ("knowledge", "put"),
        ("handoff", "save"),
        ("candidate", "mark"),
    ]:
        command(group, [action])[action].add_argument("--file", required=True)
    command("checkpoint", ["create"])["create"].add_argument("--name", required=True)
    command("provenance", ["show"])["show"].add_argument("--knowledge", required=True)
    for name, sub in command("request", ["show", "retry"]).items():
        sub.add_argument("identity")
    return p


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "__heartbeat":
        heartbeat(sys.argv[2])
        return
    c = None
    try:
        args = parser().parse_args()
        c = Client(args)
        data = execute(c, args)
        print(
            json.dumps(
                {"ok": True, "request_id": c.request_id, "data": data},
                ensure_ascii=False,
            )
        )
    except (Failure, OSError, ValueError, KeyError) as error:
        if not isinstance(error, Failure):
            error = Failure("INVALID_INPUT", str(error))
        print(
            json.dumps(
                {
                    "ok": False,
                    "request_id": c.request_id if c else None,
                    "error": {
                        "code": error.code,
                        "message": error.message,
                        **error.details,
                    },
                },
                ensure_ascii=False,
            )
        )
        sys.exit(error.exit_code)


if __name__ == "__main__":
    main()
