"""Real HTTP + subprocess CLI integration, using synthetic records, never hardware."""

import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest
import uvicorn

from .app import create_app
from .db import ConflictError, ValidationError, WorkbenchStorage
from .service import WorkbenchService
from .workspace import Workspace

CLI = (
    Path(__file__).resolve().parents[1]
    / "skills/operator-exploration/scripts/opwork.py"
)


@pytest.fixture
def live_service(tmp_path):
    app = create_app(tmp_path / "database", auto_seed=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    yield app, f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True
    thread.join(5)
    sock.close()


def test_cli_end_to_end(live_service, tmp_path):
    app, url = live_service
    env = {
        **os.environ,
        "OPWORK_URL": url,
        "OPWORK_TOKEN": "agent-secret-token",
        "OPWORK_OPERATOR_TOKEN": "operator-secret-token",
        "OPWORK_STATE_DIR": str(tmp_path / "requests"),
    }
    context = tmp_path / "session.json"
    helpers = []

    def file(name, data):
        path = tmp_path / name
        path.write_text(json.dumps(data))
        return str(path)

    def run(*args, ok=True, context_file=context, request=None):
        cmd = [sys.executable, str(CLI), "--context", str(context_file)]
        if request:
            cmd += ["--request-id", request]
        proc = subprocess.run(
            cmd + list(args), env=env, text=True, capture_output=True, timeout=10
        )
        result = json.loads(proc.stdout)
        assert result["ok"] == ok, (args, result, proc.stderr)
        assert (proc.returncode == 0) == ok
        return result["data"] if ok else result["error"]

    def wait_heartbeat(path):
        for _ in range(100):
            status = Path(str(path) + ".heartbeat.json")
            if (
                status.exists()
                and json.loads(status.read_text()).get("directive") == "continue"
            ):
                return
            time.sleep(0.05)
        pytest.fail("Heartbeat helper did not become ready")

    try:
        assert "graph" in run("doctor")["capabilities"]
        task = file(
            "contract.json",
            {
                "operator": "Synthetic test operator",
                "goal": "CLI protocol test only",
                "input_shapes": ["[8]"],
                "budget": {"max_evaluations": 20},
                "baselineRef": {
                    "id": "ref",
                    "version": "v1",
                    "latencyUs": 100,
                    "description": "synthetic test baseline",
                },
            },
        )
        project = run("project", "create", "--file", task, request="create-test")[
            "projectId"
        ]
        opened = run("session", "open", "--project", project, "--name", "integration")
        helpers.append(opened["helperPid"])
        wait_heartbeat(context)
        node = file(
            "node.json",
            {"id": "n1", "title": "synthetic baseline", "hypothesis": "protocol only"},
        )
        run("node", "create", "--file", node, request="node-test")
        assert run("request", "retry", "node-test")["nodeId"] == "n1"
        assert run("request", "show", "node-test")["state"] == "committed"
        other = file("other.json", {"id": "n2", "title": "other"})
        assert (
            run("node", "create", "--file", other, request="node-test", ok=False)[
                "code"
            ]
            == "REQUEST_CONFLICT"
        )
        kernel = tmp_path / "kernel.py"
        kernel.write_text("# synthetic test fixture, not executable kernel\n")
        resource = run(
            "resource",
            "put",
            "--file",
            str(kernel),
            "--category",
            "kernel",
            "--node",
            "n1",
        )["resourceVersionId"]
        binary = tmp_path / "profile.bin"
        binary.write_bytes(b"\xff\x00\x01")
        binary_id = run(
            "resource",
            "put",
            "--file",
            str(binary),
            "--category",
            "profile",
            "--node",
            "n1",
        )["resourceVersionId"]
        run("resource", "get", binary_id, "--output", str(tmp_path / "download.bin"))
        assert (tmp_path / "download.bin").read_bytes() == binary.read_bytes()
        previous = None
        for index, stage in enumerate(
            (
                "read_info",
                "plan_idea",
                "gen_kernel",
                "compile",
                "verify_correctness",
                "bench_perf",
            )
        ):
            aid = "a" + str(index)
            start = {
                "id": aid,
                "code": aid,
                "depends_on": [previous] if previous else [],
            }
            if index >= 2:
                start["kernelVersionLabel"] = "v1"
            if index >= 3:
                start["kernel_version_id"] = resource
            run(
                "attempt",
                "start",
                "--node",
                "n1",
                "--stage",
                stage,
                "--file",
                file("start.json", start),
            )
            assert run("context")["inFlight"][0]["id"] == aid
            if index == 0:
                app.state.service.upsert_agent_heartbeat(
                    project, {"id": opened["agentId"]}
                )
                assert (
                    app.state.storage.get_snapshot(project)["agents"][0][
                        "currentNodeId"
                    ]
                    == "n1"
                )
            finish = {
                "status": "completed",
                "summary": "Synthetic integration evidence; not an actual measurement",
            }
            if stage == "gen_kernel":
                finish["output_versions"] = [resource]
            if stage == "verify_correctness":
                finish["correctness_passed"] = True
            if stage == "bench_perf":
                finish["latency_us"] = 80
            finish_file = file("finish.json", finish)
            run(
                "attempt",
                "finish",
                aid,
                "--expected-version",
                "99",
                "--file",
                finish_file,
                ok=False,
            )
            run(
                "attempt",
                "finish",
                aid,
                "--expected-version",
                "1",
                "--file",
                finish_file,
            )
            assert run("attempt", "show", aid)["version"] == 2
            previous = aid
        knowledge = file(
            "knowledge.json",
            {
                "id": "k1",
                "title": "Synthetic claim",
                "body": "No real performance conclusion",
                "claim_type": "hypothesis",
            },
        )
        run("knowledge", "put", "--file", knowledge)
        relation = file(
            "relation.json",
            {
                "id": "s1",
                "kind": "support",
                "source_type": "node",
                "source_id": "n1",
                "target_type": "knowledge",
                "target_id": "k1",
                "knowledge_version": "v1",
                "rationale": "Fixture result only",
                "evidence_attempt_ids": ["a5"],
            },
        )
        run("relation", "add", "--file", relation)
        assert run("provenance", "show", "--knowledge", "k1")["relations"]
        exported = tmp_path / "snapshot.json"
        run("project", "export", "--output", str(exported))
        imported = run(
            "project", "import", "--file", str(exported), request="import-test"
        )["projectId"]
        imp = app.state.storage.get_snapshot(imported)
        assert (
            imp["coordinatorStatus"] == "stopped"
            and not imp["nodes"][0]["correctnessPassed"]
        )
        assert run("request", "retry", "import-test")["projectId"] == imported
        corrupt = json.loads(exported.read_text())
        corrupt["exploration"]["resources"][resource]["content"] = "tampered"
        count = len(app.state.storage.list_explorations())
        run("project", "import", "--file", file("corrupt.json", corrupt), ok=False)
        assert len(app.state.storage.list_explorations()) == count
        cp = run("checkpoint", "create", "--name", "stable")["checkpointId"]
        fork = run(
            "project",
            "fork",
            "--from",
            project,
            "--checkpoint",
            cp,
            "--name",
            "test fork",
        )["projectId"]
        assert app.state.storage.get_snapshot(fork)["nodes"][0]["correctnessPassed"]
        handoff = run(
            "handoff",
            "save",
            "--file",
            file(
                "handoff.json",
                {
                    "summary": "Synthetic session complete",
                    "evidence_versions": [resource],
                },
            ),
        )["handoffId"]
        run("candidate", "mark", "--file", file("candidate.json", {"node_ids": ["n1"]}))
        assert run("node", "show", "n1")["contentStatus"] == "ready-for-cool"
        after = run("checkpoint", "create", "--name", "handoff saved")["checkpointId"]
        after_fork = run(
            "project",
            "fork",
            "--from",
            project,
            "--checkpoint",
            after,
            "--name",
            "with handoff",
        )["projectId"]
        assert (
            Workspace(app.state.storage).context(after_fork)["latestHandoff"]["id"]
            == handoff
        )
        portable = tmp_path / "with-handoff.json"
        run("project", "export", "--output", str(portable))
        imported_handoff = run("project", "import", "--file", str(portable))[
            "projectId"
        ]
        assert (
            Workspace(app.state.storage).context(imported_handoff)["latestHandoff"][
                "id"
            ]
            == handoff
        )
        run("session", "close", "--handoff", handoff)
        assert (
            run("node", "create", "--file", other, ok=False)["code"] == "SESSION_CLOSED"
        )
        with pytest.raises(ConflictError):
            app.state.service.upsert_agent_heartbeat(project, {"id": opened["agentId"]})
        fresh = tmp_path / "new-session.json"
        second = run(
            "session",
            "open",
            "--project",
            project,
            "--name",
            "new session",
            context_file=fresh,
        )
        helpers.append(second["helperPid"])
        wait_heartbeat(fresh)
        assert second["agentId"] != opened["agentId"]
        assert (
            run("context", context_file=fresh)["latestHandoff"]["summary"]
            == "Synthetic session complete"
        )
        run("project", "pause", "--reason", "test pause", context_file=fresh)
        ctx = run("context", context_file=fresh)
        run(
            "project",
            "resume",
            "--expected-version",
            str(ctx["version"] - 1),
            context_file=fresh,
            ok=False,
        )
        run(
            "project",
            "resume",
            "--expected-version",
            str(ctx["version"]),
            context_file=fresh,
        )
        run(
            "attempt",
            "start",
            "--node",
            "n1",
            "--stage",
            "read_info",
            "--file",
            file("ongoing.json", {"id": "ongoing"}),
            context_file=fresh,
        )
        run(
            "attempt",
            "reconcile",
            "ongoing",
            "--expected-version",
            "1",
            "--file",
            file(
                "reconcile.json",
                {
                    "status": "stopped",
                    "reason": "External test executor confirmed terminated",
                },
            ),
            context_file=fresh,
        )
    finally:
        for pid in helpers:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass


def test_import_validation_and_fork_isolation(tmp_path):
    db = WorkbenchStorage(tmp_path)
    s = WorkbenchService(db)
    w = Workspace(db)
    s.create_or_reset_exploration(
        dict(
            id="e",
            title="x",
            operatorName="x",
            targetGoal="",
            inputDomain="",
            environment="",
            baselineRef={},
            budget={},
        )
    )
    s.create_node("e", dict(id="a", title="a"))
    s.create_node(
        "e",
        dict(id="b", title="b", parentIds=["a"], sourceContributions={"a": "reuse"}),
    )
    s.upsert_agent_heartbeat("e", {"id": "old"})
    s.start_stage_attempt(
        "e", "a", dict(id="running", agentId="old", stageType="read_info", code="R")
    )
    cp = w.checkpoint("e", {}, "cp")["checkpointId"]
    fork = w.fork("e", {"checkpointId": cp}, "fork")["projectId"]
    assert (
        db.get_snapshot(fork)["nodes"][0]["stages"]["read_info"]["attempts"][0][
            "status"
        ]
        == "stopped"
    )
    assert w.context("e")["inFlight"][0]["id"] == "running"
    snapshot = db.get_snapshot("e")
    bad = copy.deepcopy(snapshot)
    bad["graphRelations"].append(
        dict(
            id="cycle",
            kind="derivation",
            sourceType="node",
            sourceId="b",
            targetType="node",
            targetId="a",
            rationale="invalid cycle",
        )
    )
    before = len(db.list_explorations())
    with pytest.raises(ValidationError):
        w.import_snapshot(bad, "bad")
    assert len(db.list_explorations()) == before
    imported = w.import_snapshot(snapshot, "good")["projectId"]
    assert not w.context(imported)["inFlight"]
