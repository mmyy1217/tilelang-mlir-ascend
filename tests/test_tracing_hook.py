"""CPU-only integration tests for optional per-project `agent-trace` integration."""

import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PROJECT_SCRIPT = ROOT / "assets/skills/harness-new-project/scripts/projectctl.py"
SEARCH_SCRIPT = ROOT / "assets/skills/harness-resume/scripts/searchctl.py"
sys.path.insert(0, str(PROJECT_SCRIPT.parent))
sys.path.insert(0, str(SEARCH_SCRIPT.parent))

import projectctl
import searchctl
import tracing_hook
from records import render


def _snapshot_tree(base: Path):
    if not base.exists():
        return {}
    return {
        str(path.relative_to(base)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(base.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _make_executable(path: Path, script: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class TracingHookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # Resolve so macOS /var -> /private/var symlink does not trip symlink guards.
        self.root = Path(self._tmp.name).resolve()
        self.ws = projectctl.Workspace(self.root)
        self.ws.create("example", "Example operator", "session")
        self.store = searchctl.Store(self.root, "example", "session")
        self.store.init("example", "requirements-v1", "initial implementation", "all cases")
        self.seq = 0

    def write(self, rel_path: str, text: str) -> str:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return rel_path

    def write_json(self, rel_path: str, data) -> dict:
        path = self.write(rel_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return self.store.reference(path)

    def apply(self, op: str, **values):
        self.seq += 1
        return self.store.apply({"event_id": f"ev-{self.seq}", "op": op, **values})

    def project_cli(self, *args, project_id="example", owner="session"):
        cmd = [sys.executable, str(PROJECT_SCRIPT), "--workspace", str(self.root)]
        if project_id is not None:
            cmd.extend(["--project", project_id])
        if owner is not None:
            cmd.extend(["--owner", owner])
        cmd.extend(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def search_cli(self, *args, project_id="example", owner="session", legacy=False):
        cmd = [sys.executable, str(SEARCH_SCRIPT), "--workspace", str(self.root)]
        if legacy:
            cmd.append("--legacy")
        else:
            if project_id is not None:
                cmd.extend(["--project", project_id])
            if owner is not None:
                cmd.extend(["--owner", owner])
        cmd.extend(args)
        return subprocess.run(cmd, capture_output=True, text=True)

    def build_validated_develop_run(
        self,
        run_id: str,
        candidate_id: str,
        search_node_id: str = "N0",
        parent_candidate: str = None,
        samples_ms=None,
        source_code: str = None,
    ) -> str:
        """Create a contract-@4 validated develop run purely on CPU (portable across macOS & Linux)."""
        samples_ms = samples_ms or [0.42, 0.44]
        prefix = f".workspace/example/runs/{run_id}/"
        shared_prefix = ".workspace/example/shared/"

        # Share identical frozen evaluation inputs so multiple candidates share one comparison_identity.
        inputs = {}
        for key, body in (
            ("requirements", "req-v1\n"),
            ("cases", "cases-v1\n"),
            ("protocol", "protocol-v1\n"),
            ("environment", "env-cpu\n"),
            ("reference", "def run(s): return s['x'] * 2\n"),
            ("comparator", "def compare(a, e, c): return {'out': a == e}\n"),
        ):
            inputs[key] = self.store.reference(self.write(shared_prefix + f"{key}.txt", body))
        adapter_ref = self.store.reference(self.write(shared_prefix + "adapter.py", "# stub adapter\n"))

        plan = {
            "schema": "operator-harness/evaluation-plan@1",
            "project_id": "example",
            "run_id": run_id,
            "inputs": inputs,
            "performance_kind": "absolute",
            "statistic": "mean",
            "cases": [{"id": "case_a", "outputs": ["out"], "min_samples": 2, "max_latency_ms": 10.0}],
            "execution": {"adapter": adapter_ref, "backend": "cpu", "timeout_seconds": 5},
        }
        plan_ref = self.write_json(prefix + "plan.json", plan)

        mode = "implement-and-validate" if parent_candidate else "initial-and-validate"
        task_fields = {
            "contract": "operator-harness/node-task@4",
            "project_id": "example",
            "run_id": run_id,
            "node": "develop",
            "search_node_id": search_node_id,
            "operation": mode,
            "performance_required": "true",
            "diagnostics_required": "false",
            "evaluation_plan": plan_ref["path"],
            "aicpu_policy": "forbidden",
        }
        if not parent_candidate:
            task_fields["initial_design"] = self.write(prefix + "design.md", "Initial vectorized implementation.\n")
        task_path = self.write(prefix + "TASK.md", render("Task", task_fields))
        self.apply(
            "run",
            id=run_id,
            stage="develop",
            candidate_id=parent_candidate,
            search_node_id=search_node_id,
            task=task_path,
            legacy=False,
        )

        # Register candidate@2 manifest and artifacts.
        src_ref = self.store.reference(
            self.write(prefix + "candidate.py", source_code or f"def kernel(x): return x * 2  # {candidate_id}\n")
        )
        bld_ref = self.write_json(prefix + "build.json", {"candidate": candidate_id})
        lch_ref = self.write_json(prefix + "launch.json", {"candidate": candidate_id})
        artifacts = {"source": src_ref, "build": bld_ref, "launch": lch_ref}

        parent_manifest_value = parent_candidate if parent_candidate else "not_applicable"
        cand_fields = {
            "contract": "operator-harness/candidate@2",
            "project_id": "example",
            "candidate_id": candidate_id,
            "search_node_id": search_node_id,
            "parent_candidate": parent_manifest_value,
            "source": src_ref["path"],
            "source_sha256": src_ref["sha256"],
            "build": bld_ref["path"],
            "build_sha256": bld_ref["sha256"],
            "launch": lch_ref["path"],
            "launch_sha256": lch_ref["sha256"],
        }
        cand_path = self.write(f".workspace/example/candidates/{candidate_id}.md", render("Candidate", cand_fields))
        cand_ref = self.store.reference(cand_path)
        self.apply(
            "candidate",
            id=candidate_id,
            search_node_id=search_node_id,
            parent_candidate=parent_candidate,
            manifest=cand_path,
        )

        # Build structured process/execution/case/validation evidence accepted by evidence.py.
        req_id = f"req-{run_id}"
        proc_ref = self.write_json(
            prefix + "process-receipt.json",
            {
                "schema": "operator-harness/process-receipt@1",
                "project_id": "example",
                "run_id": run_id,
                "request_id": req_id,
                "status": "exited",
                "exit_codes": {"process": 0},
                "elapsed_seconds": 0.12,
            },
        )
        receipt = {
            "schema": "operator-harness/execution-receipt@1",
            "project_id": "example",
            "run_id": run_id,
            "candidate_id": candidate_id,
            "request_id": req_id,
            "status": "exited",
            "exit_codes": {"process": 0},
            "valid": True,
            "producer": "evaluate@1",
            "backend": "cpu",
            "process": proc_ref,
            "artifacts": artifacts,
            "evaluation_plan": plan_ref,
        }
        raw_acc = self.write_json(prefix + "raw-acc.json", {"out": True})
        raw_perf = self.write_json(prefix + "raw-perf.json", {"samples_ms": samples_ms})
        acc_report = {
            "schema": "operator-harness/accuracy@1",
            "project_id": "example",
            "run_id": run_id,
            "candidate_id": candidate_id,
            "case_id": "case_a",
            "request_id": req_id,
            "artifacts": artifacts,
            "evaluation_plan": plan_ref,
            "outputs": {"out": True},
            "raw_files": [raw_acc],
        }
        perf_report = {
            "schema": "operator-harness/performance@1",
            "project_id": "example",
            "run_id": run_id,
            "candidate_id": candidate_id,
            "case_id": "case_a",
            "request_id": req_id,
            "artifacts": artifacts,
            "evaluation_plan": plan_ref,
            "metric": "cpu_wall_ms",
            "samples_ms": samples_ms,
            "raw_files": [raw_perf],
        }
        validation_ref = self.write_json(
            prefix + "validation.json",
            {
                "schema": "operator-harness/validation@1",
                "project_id": "example",
                "run_id": run_id,
                "evaluation_plan": plan_ref,
                "candidate": cand_ref,
                "candidate_id": candidate_id,
                "artifacts": artifacts,
                "receipts": [receipt],
                "cases": [{"id": "case_a", "accuracy": acc_report, "performance": perf_report}],
                "diagnostics": [],
            },
        )
        analysis_data = self.write(
            prefix + "analysis-data.md",
            render(
                "Analysis Data",
                {"project_id": "example", "run_id": run_id, "candidate_id": candidate_id, "status": "not_applicable"},
            ),
        )
        regression = self.write(prefix + "regression.md", "No regression.\n")
        result_path = self.write(
            prefix + "RESULT.md",
            render(
                "Result",
                {
                    "contract": "operator-harness/node-result@4",
                    "project_id": "example",
                    "run_id": run_id,
                    "node": "develop",
                    "search_node_id": search_node_id,
                    "status": "completed",
                    "accuracy_status": "passed",
                    "performance_status": "passed",
                    "regression_cause": "none",
                    "regression_evidence": regression,
                    "analysis_data": analysis_data,
                    "candidate_manifest": cand_path,
                    "validation_evidence": validation_ref["path"],
                },
            ),
        )
        return result_path

    def make_stub_cli(self, name="stub-agent-trace", mode="ok"):
        """Create a portable executable stub mimicking `agent-trace sync-harness`."""
        log_path = self.root / f".stub-logs/{name}.jsonl"
        script_path = self.root / f".stub-bin/{name}"
        code = textwrap.dedent(
            f"""\
            #!{sys.executable}
            import argparse, json, sys
            from pathlib import Path

            LOG = Path({str(log_path)!r})
            MODE = {mode!r}

            parser = argparse.ArgumentParser()
            parser.add_argument("--dir", required=True)
            parser.add_argument("--trace", required=True)
            parser.add_argument("--json", action="store_true")
            sub = parser.add_subparsers(dest="cmd", required=True)
            sp = sub.add_parser("sync-harness")
            sp.add_argument("--project-dir", required=True)
            args = parser.parse_args()

            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({{
                    "argv": sys.argv[1:],
                    "dir": args.dir,
                    "trace": args.trace,
                    "json": args.json,
                    "project_dir": args.project_dir,
                }}) + "\\n")

            if MODE == "fail":
                sys.stderr.write("boom: synthetic sync failure\\n")
                sys.exit(2)
            if MODE == "bad-json":
                sys.stdout.write("not-json\\n")
                sys.exit(0)
            if MODE == "wrong-identity":
                sys.stdout.write(json.dumps({{"project_id": "other", "trace_id": args.trace}}) + "\\n")
                sys.exit(0)

            proj_dir = Path(args.project_dir).resolve()
            proj = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
            tree = json.loads((proj_dir / "search/tree.json").read_text(encoding="utf-8"))
            events = tree.get("events") or []
            last_id = events[-1]["operation"]["event_id"] if events else None
            best = tree.get("best") or {{}}
            promoted = best.get("candidate_id")

            tdir = Path(args.dir).resolve() / "traces" / args.trace
            tdir.mkdir(parents=True, exist_ok=True)
            viewer = tdir / "index.html"
            viewer.write_text(f"<html><body>trace={{args.trace}} events={{len(events)}} promoted={{promoted}}</body></html>", encoding="utf-8")
            state = {{
                "schema": "agent-tracing/harness-sync@1",
                "project_id": proj["id"],
                "project_dir": str(proj_dir),
                "trace_id": args.trace,
                "upstream_event_count": len(events),
                "last_upstream_event_id": last_id,
                "promoted_candidate_id": promoted,
                "best_attempt_id": promoted or "C1",
                "selected_attempt_id": None,
                "viewer_path": str(viewer.resolve()),
                "synced_at": "2026-01-01T00:00:00+00:00",
            }}
            (tdir / "harness_sync_state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
            out = {{
                "project_id": proj["id"],
                "project_dir": str(proj_dir),
                "trace_id": args.trace,
                "upstream_event_count": len(events),
                "last_upstream_event_id": last_id,
                "promoted_candidate_id": promoted,
                "viewer_path": str(viewer.resolve()),
            }}
            if args.json:
                print(json.dumps(out))
            else:
                print(f"Synced {{proj['id']}} -> {{args.trace}}")
            """
        )
        _make_executable(script_path, code)
        return script_path, log_path

    def read_invocations(self, log_path: Path):
        if not log_path.exists():
            return []
        return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_disabled_by_default_zero_side_effects(self):
        res = self.project_cli("tracing", "status")
        self.assertEqual(res.returncode, 0, res.stderr)
        status = json.loads(res.stdout)
        self.assertFalse(status["enabled"])
        self.assertIsNone(status["config"])

        self.assertEqual(self.store.reconcile()["tracing"], {"enabled": False, "state": "disabled"})
        tstatus = self.search_cli("tracing-status")
        self.assertEqual(tstatus.returncode, 0, tstatus.stderr)
        self.assertEqual(json.loads(tstatus.stdout), {"enabled": False, "state": "disabled"})

        result_path = self.build_validated_develop_run("d1", "C1")
        with patch.object(tracing_hook.subprocess, "run") as mock_run:
            self.apply("accept", id="d1", result=result_path)
            self.apply("promote-candidate", run_id="d1", reason="initial baseline passed")
            mock_run.assert_not_called()

        self.assertFalse((self.store.directory / "tracing-status.json").exists())
        self.assertFalse((self.store.directory / ".tracing-hook.lock").exists())
        self.assertEqual(self.store.reconcile()["tracing"], {"enabled": False, "state": "disabled"})

    def test_projectctl_tracing_enable_disable_status_and_isolation(self):
        stub, _ = self.make_stub_cli("ok-cli", mode="ok")
        self.ws.create("other", "Other project", "session2")

        # Default store_dir stays inside private .workspace/<project>/tracing
        default_en = self.project_cli("tracing", "enable", "--command", str(stub))
        self.assertEqual(default_en.returncode, 0, default_en.stderr)
        self.assertEqual(json.loads(default_en.stdout)["config"]["store_dir"], ".workspace/example/tracing")

        # Enable only on `example` with explicit custom store_dir under private tracing dir
        res = self.project_cli(
            "tracing", "enable",
            "--command", str(stub),
            "--trace-id", "custom-trace-1",
            "--store-dir", ".workspace/example/tracing/custom",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        enabled = json.loads(res.stdout)
        self.assertTrue(enabled["enabled"])
        self.assertTrue(enabled["cli_found"])
        self.assertEqual(enabled["config"]["trace_id"], "custom-trace-1")
        self.assertEqual(enabled["config"]["store_dir"], ".workspace/example/tracing/custom")
        self.assertEqual(enabled["config"]["command"], str(stub))

        # `other` remains completely unaffected / disabled
        other_res = self.project_cli("tracing", "status", project_id="other", owner="session2")
        self.assertEqual(other_res.returncode, 0, other_res.stderr)
        self.assertFalse(json.loads(other_res.stdout)["enabled"])

        # Owner enforcement
        wrong_owner = self.project_cli("tracing", "disable", owner="intruder")
        self.assertNotEqual(wrong_owner.returncode, 0)
        self.assertIn("another session", wrong_owner.stderr)

        # Disable preserves settings for clean re-enable
        dis = self.project_cli("tracing", "disable")
        self.assertEqual(dis.returncode, 0, dis.stderr)
        dis_json = json.loads(dis.stdout)
        self.assertFalse(dis_json["enabled"])
        self.assertEqual(dis_json["config"]["trace_id"], "custom-trace-1")
        self.assertEqual(dis_json["config"]["store_dir"], ".workspace/example/tracing/custom")

        reen = self.project_cli("tracing", "enable")
        self.assertEqual(reen.returncode, 0, reen.stderr)
        reen_json = json.loads(reen.stdout)
        self.assertTrue(reen_json["enabled"])
        self.assertEqual(reen_json["config"]["trace_id"], "custom-trace-1")
        self.assertEqual(reen_json["config"]["store_dir"], ".workspace/example/tracing/custom")

        # Validation guards: bad store_dir (including tracked product dirs & symlinks) / trace_id / command
        link = self.root / ".workspace/example/tracing/sym-sub"
        (self.root / ".workspace/example/tracing").mkdir(parents=True, exist_ok=True)
        (self.root / "real-traces").mkdir()
        link.symlink_to(self.root / "real-traces", target_is_directory=True)
        for bad_store in (
            "", "   ", ".", "../escape", str(self.root / "abs"),
            ".agent-tracing", "assets/skills", "docs",
            ".workspace", ".workspace/sub", ".workspace/other/tracing",
            ".workspace/example", ".workspace/example/search", ".workspace/example/runs",
            ".workspace/example/tracing/sym-sub", ".workspace/example/tracing/sym-sub/nested",
        ):
            with self.subTest(bad_store=bad_store), self.assertRaises(projectctl.Invalid):
                self.ws.set_tracing("example", "session", True, store_dir=bad_store)
        for bad_tid in ("", "../bad", "bad/id", "bad id", ".leadingdot"):
            with self.subTest(bad_tid=bad_tid), self.assertRaises(projectctl.Invalid):
                self.ws.set_tracing("example", "session", True, trace_id=bad_tid)
        for bad_cmd in ("", "   ", "agent-trace\nrm", "agent-trace\x00", "~no-such-user-99999/bin/agent-trace"):
            with self.subTest(bad_cmd=bad_cmd), self.assertRaises(projectctl.Invalid):
                self.ws.set_tracing("example", "session", True, command=bad_cmd)
        # Ensure failed `set_tracing` on unexpandable tilde did NOT overwrite saved config!
        self.assertEqual(self.ws.load("example")["tracing"]["command"], str(stub))

        # Lifecycle enforcement: freezing project rejects tracing mutation
        self.ws.begin_close("example", "session", "frozen")
        with self.assertRaises(projectctl.Invalid):
            self.ws.set_tracing("example", "session", False)

    def test_hook_triggers_only_on_accept_and_promote_candidate(self):
        stub, log_path = self.make_stub_cli("trigger-cli", mode="ok")
        self.ws.set_tracing("example", "session", True, command=str(stub))

        # Before any accept/promote: lagging (init event exists), zero invocations
        pre = self.store.reconcile()["tracing"]
        self.assertTrue(pre["enabled"])
        self.assertEqual(pre["state"], "lagging")
        self.assertEqual(self.read_invocations(log_path), [])

        # Non-trigger ops: add-node, select, hot, run, candidate, close-run must NOT spawn CLI
        self.apply("add-node", id="N1", parent="N0", hypothesis="vectorize", scope="all")
        self.apply("select", id="N1", reason="try vectorize")
        ev_file = self.write(".workspace/example/evidence/obs.md", "obs\n")
        self.apply(
            "hot", id="H1", base_revision=0, kind="observation",
            statement="ub bound", applicability="all", limitations="synthetic",
            nodes=["N1"], evidence=[ev_file],
        )
        # Build a close-run fixture
        r_close_task = self.write(
            ".workspace/example/runs/rc/TASK.md",
            "- project_id: example\n- run_id: rc\n- node: analysis\n- search_node_id: N1\n",
        )
        self.apply("run", id="rc", stage="analysis", candidate_id=None, search_node_id="N1", task=r_close_task, legacy=True)
        r_close_chk = self.write(
            ".workspace/example/runs/rc/checkpoint.md",
            "- project_id: example\n- run_id: rc\n- node: analysis\n- search_node_id: N1\n- status: blocked\n",
        )
        r_close_raw = self.store.reference(self.write(".workspace/example/runs/rc/raw.txt", "stopped"))
        r_close_ev = self.write_json(
            ".workspace/example/runs/rc/close.json",
            {
                "schema": "operator-harness/run-closure@1",
                "project_id": "example",
                "run_id": "rc",
                "summary": "abandoned analysis",
                "writers_stopped": True,
                "jobs": [],
                "unsettled_resources": [],
                "evidence": [r_close_raw],
            },
        )
        self.apply(
            "close-run", id="rc", disposition="abandoned", reason="not needed",
            checkpoint=r_close_chk, evidence=r_close_ev["path"],
        )

        # Prepare validated develop run d1 (includes `run` + `candidate` ops — neither triggers hook)
        result_d1 = self.build_validated_develop_run("d1", "C1", search_node_id="N1", samples_ms=[0.80, 0.82])
        self.assertEqual(self.read_invocations(log_path), [])

        # 1st trigger: accept d1
        accept_op = {"event_id": "ev-accept-d1", "op": "accept", "id": "d1", "result": result_d1}
        self.store.apply(accept_op)
        calls = self.read_invocations(log_path)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["trace"], "harness-example")
        self.assertTrue(calls[0]["json"])
        self.assertEqual(Path(calls[0]["project_dir"]).resolve(), (self.root / ".workspace/example").resolve())

        st1 = self.store.reconcile()["tracing"]
        self.assertEqual(st1["state"], "synced")
        self.assertEqual(st1["lag_events"], 0)
        self.assertTrue(st1["viewer_exists"])
        self.assertIsNone(st1["promoted_candidate_id"])
        self.assertIsNone(st1["last_error"])

        # Idempotent replay of exact same accept while already synced -> NO duplicate CLI call
        self.store.apply(accept_op)
        self.assertEqual(len(self.read_invocations(log_path)), 1)

        # 2nd trigger: promote-candidate C1 (no new benchmark!)
        self.apply("promote-candidate", run_id="d1", reason="C1 passed all checks")
        self.assertEqual(len(self.read_invocations(log_path)), 2)
        st2 = self.store.reconcile()["tracing"]
        self.assertEqual(st2["state"], "synced")
        self.assertEqual(st2["promoted_candidate_id"], "C1")

        # 3rd trigger: accept d2 (C2), then re-promote C2 without new benchmark
        result_d2 = self.build_validated_develop_run(
            "d2", "C2", search_node_id="N1", parent_candidate="C1", samples_ms=[0.45, 0.47],
        )
        self.assertEqual(len(self.read_invocations(log_path)), 2)
        self.apply("accept", id="d2", result=result_d2)
        self.assertEqual(len(self.read_invocations(log_path)), 3)
        self.assertEqual(self.store.reconcile()["tracing"]["promoted_candidate_id"], "C1")

        # Re-promotion alone (no new run/accept) triggers hook and updates promoted_candidate_id
        self.apply("promote-candidate", run_id="d2", reason="C2 is faster on same comparison")
        self.assertEqual(len(self.read_invocations(log_path)), 4)
        st4 = self.store.reconcile()["tracing"]
        self.assertEqual(st4["state"], "synced")
        self.assertEqual(st4["promoted_candidate_id"], "C2")
        self.assertEqual(st4["lag_events"], 0)

    def test_failure_never_rolls_back_and_recovers_on_manual_retry(self):
        fail_cli, fail_log = self.make_stub_cli("fail-cli", mode="fail")
        ok_cli, ok_log = self.make_stub_cli("ok-cli", mode="ok")
        self.ws.set_tracing("example", "session", True, command=str(fail_cli))

        result_d1 = self.build_validated_develop_run("d1", "C1")
        stderr_buf = io.StringIO()
        accept_op = {"event_id": "ev-accept-fail", "op": "accept", "id": "d1", "result": result_d1}
        with patch("sys.stderr", stderr_buf):
            self.store.apply(accept_op)

        # Ledger MUST be durably committed even though sync failed!
        tree = self.store.load()
        self.assertIsNotNone(tree["runs"]["d1"]["result"])
        err_text = stderr_buf.getvalue()
        self.assertIn("tracing sync failed", err_text)
        self.assertIn("ledger committed", err_text)
        self.assertIn("boom: synthetic sync failure", err_text)
        self.assertIn("sync-harness", err_text)
        self.assertEqual(len(self.read_invocations(fail_log)), 1)

        # reconcile + tracing-status surface error + lag + retry_command
        st_err = self.store.reconcile()["tracing"]
        self.assertEqual(st_err["state"], "error")
        self.assertIn("boom: synthetic sync failure", st_err["last_error"])
        self.assertGreater(st_err["lag_events"], 0)
        self.assertFalse(st_err["viewer_exists"])
        self.assertIn("sync-harness", st_err["retry_command"])

        # Switch command to working CLI and execute the exact retry_command manually (no NPU rerun!)
        self.ws.set_tracing("example", "session", True, command=str(ok_cli))
        retry_cmd = self.store.reconcile()["tracing"]["retry_command"]
        proc = subprocess.run(shlex.split(retry_cmd), cwd=str(self.root), capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.read_invocations(ok_log)), 1)

        # Status immediately clears error and reports synced without touching tree.json
        st_ok = self.store.reconcile()["tracing"]
        self.assertEqual(st_ok["state"], "synced")
        self.assertIsNone(st_ok["last_error"])
        self.assertEqual(st_ok["lag_events"], 0)
        self.assertTrue(st_ok["viewer_exists"])

        # Also test missing CLI, invalid JSON, and mismatched identity failures never roll back
        for label, bad_cmd, expected_msg in (
            ("missing", str(self.root / ".stub-bin/does-not-exist"), "not found"),
            ("bad-json", str(self.make_stub_cli("badjson-cli", mode="bad-json")[0]), "invalid JSON"),
            ("wrong-id", str(self.make_stub_cli("wrongid-cli", mode="wrong-identity")[0]), "identity mismatch"),
        ):
            with self.subTest(label=label):
                self.ws.set_tracing("example", "session", True, command=bad_cmd)
                buf = io.StringIO()
                with patch("sys.stderr", buf):
                    self.apply("promote-candidate", run_id="d1", reason=f"promote under {label}")
                self.assertEqual(self.store.load()["best"]["candidate_id"], "C1")
                self.assertIn(expected_msg, buf.getvalue())
                self.assertEqual(self.store.reconcile()["tracing"]["state"], "error")
                self.assertIn(expected_msg, self.store.reconcile()["tracing"]["last_error"])

        # Idempotent replay of an already-committed promote-candidate while in error state retries sync!
        self.ws.set_tracing("example", "session", True, command=str(ok_cli))
        last_op = self.store.load()["events"][-1]["operation"]
        before_calls = len(self.read_invocations(ok_log))
        self.store.apply(last_op)
        self.assertEqual(len(self.read_invocations(ok_log)), before_calls + 1)
        self.assertEqual(self.store.reconcile()["tracing"]["state"], "synced")

    def test_status_and_reconcile_are_strictly_read_only(self):
        stub, log_path = self.make_stub_cli("readonly-cli", mode="ok")
        self.ws.set_tracing("example", "session", True, command=str(stub))

        # Lagging state (never synced yet)
        ws_before = _snapshot_tree(self.root / ".workspace")
        tr_before = _snapshot_tree(self.root / ".agent-tracing")
        for args in (("reconcile",), ("tracing-status",)):
            proc = self.search_cli(*args)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        pproc = self.project_cli("tracing", "status")
        self.assertEqual(pproc.returncode, 0, pproc.stderr)
        self.assertEqual(self.read_invocations(log_path), [])
        self.assertEqual(_snapshot_tree(self.root / ".workspace"), ws_before)
        self.assertEqual(_snapshot_tree(self.root / ".agent-tracing"), tr_before)

        # Legacy layout read-only tracing-status
        tree = self.store.load()
        tree.pop("project_id")
        legacy_path = self.root / ".workspace/search/tree.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps(tree), encoding="utf-8")
        leg = self.search_cli("tracing-status", legacy=True)
        self.assertEqual(leg.returncode, 0, leg.stderr)
        leg_json = json.loads(leg.stdout)
        self.assertFalse(leg_json["enabled"])
        self.assertEqual(leg_json["state"], "disabled")

    def test_e2e_with_real_agent_trace_sync_harness(self):
        agent_tracing_repo = ROOT.parent / "harness-agent-tracing"
        if not (agent_tracing_repo / "agent_tracing" / "harness_sync.py").is_file():
            self.skipTest("sibling harness-agent-tracing repo not present")

        # Create a thin executable wrapper pointing at the real agent_tracing package.
        wrapper = self.root / ".stub-bin/agent-trace"
        _make_executable(
            wrapper,
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import sys
                sys.path.insert(0, {str(agent_tracing_repo.resolve())!r})
                from agent_tracing.cli import main
                sys.exit(main())
                """
            ),
        )

        # Enable tracing on `example` via projectctl CLI
        en = self.project_cli("tracing", "enable", "--command", str(wrapper))
        self.assertEqual(en.returncode, 0, en.stderr)
        self.assertTrue(json.loads(en.stdout)["enabled"])

        # Add search node and run validated develop candidate C1 -> accept triggers real sync-harness!
        self.apply("add-node", id="N1", parent="N0", hypothesis="double buffer", scope="all")
        self.apply("select", id="N1", reason="test double buffering")
        res_d1 = self.build_validated_develop_run("d1", "C1", search_node_id="N1", samples_ms=[0.90, 0.92])
        self.apply("accept", id="d1", result=res_d1)

        st1 = json.loads(self.search_cli("tracing-status").stdout)
        self.assertTrue(st1["enabled"])
        self.assertEqual(st1["state"], "synced")
        self.assertEqual(st1["lag_events"], 0)
        self.assertTrue(st1["viewer_exists"])
        self.assertTrue(Path(st1["viewer_path"]).is_file())
        self.assertIsNone(st1["promoted_candidate_id"])
        self.assertEqual(st1["best_attempt_id"], "C1")

        # Promote C1 via searchctl -> triggers real sync-harness again with NO new benchmark!
        self.apply("promote-candidate", run_id="d1", reason="C1 passed accuracy and latency target")
        st2 = self.store.reconcile()["tracing"]
        self.assertEqual(st2["state"], "synced")
        self.assertEqual(st2["promoted_candidate_id"], "C1")
        self.assertEqual(st2["lag_events"], 0)
        self.assertIsNone(st2["last_error"])

        # Verify sync_state lives in private .workspace/<project>/tracing and separates promotion from selection
        private_store = self.root / ".workspace/example/tracing"
        sync_state = json.loads(
            (private_store / "traces/harness-example/harness_sync_state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sync_state["promoted_candidate_id"], "C1")
        self.assertEqual(sync_state["promoted_reason"], "C1 passed accuracy and latency target")
        self.assertIsNone(sync_state["selected_attempt_id"])
        # Default private storage must NOT leak .agent-tracing into tracked workspace root
        self.assertFalse((self.root / ".agent-tracing").exists())

        # Verify read-only `agent-trace doctor` passes on the generated trace
        doc = subprocess.run(
            [str(wrapper), "--dir", str(private_store), "--trace", "harness-example", "--json", "doctor"],
            capture_output=True, text=True,
        )
        self.assertEqual(doc.returncode, 0, doc.stderr)
        doc_json = json.loads(doc.stdout)
        self.assertEqual(doc_json["trace"]["status"], "ok", doc_json)
        self.assertGreater(doc_json["trace"]["valid_lines"], 0)

    def test_symlinked_store_or_trace_dir_never_breaks_lifecycle_or_reconcile(self):
        stub, log_path = self.make_stub_cli("sym-safe-cli", mode="ok")
        self.ws.set_tracing("example", "session", True, command=str(stub))

        # 1. Replace configured store_dir with a symlink AFTER enablement
        outside = self.root / "outside-target"
        outside.mkdir()
        store_dir = self.root / ".workspace/example/tracing"
        store_dir.symlink_to(outside, target_is_directory=True)

        # Core project lifecycle operations MUST still succeed
        self.assertEqual(self.ws.load("example")["id"], "example")
        self.assertEqual([p["id"] for p in self.ws.list()], ["example"])
        st_cli = self.project_cli("tracing", "status")
        self.assertEqual(st_cli.returncode, 0, st_cli.stderr)
        self.assertIn("symlink", json.loads(st_cli.stdout)["store_dir_error"])

        # reconcile and tracing-status MUST still succeed and report structured error
        rec = self.store.reconcile()
        self.assertEqual(rec["tracing"]["state"], "error")
        self.assertIn("symlink", rec["tracing"]["last_error"])

        # User can recover by disabling or re-pointing tracing without deadlock
        dis = self.project_cli("tracing", "disable")
        self.assertEqual(dis.returncode, 0, dis.stderr)
        self.assertEqual(self.store.reconcile()["tracing"], {"enabled": False, "state": "disabled"})

        # 2. Re-enable on a clean dir, then symlink <store_dir>/traces outside the store
        store_dir.unlink()
        self.ws.set_tracing("example", "session", True, command=str(stub))
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / "traces").symlink_to(outside, target_is_directory=True)

        rec2 = self.store.reconcile()
        self.assertEqual(rec2["tracing"]["state"], "error")
        self.assertIn("symlink", rec2["tracing"]["last_error"])
        tstatus = self.search_cli("tracing-status")
        self.assertEqual(tstatus.returncode, 0, tstatus.stderr)
        self.assertEqual(json.loads(tstatus.stdout)["state"], "error")

        # Accepting and idempotently replaying must commit tree.json, report error, and NEVER invoke CLI
        result_d1 = self.build_validated_develop_run("d1", "C1")
        accept_op = {"event_id": "ev-sym-accept", "op": "accept", "id": "d1", "result": result_d1}
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            self.store.apply(accept_op)
            self.store.apply(accept_op)
        self.assertIsNotNone(self.store.load()["runs"]["d1"]["result"])
        self.assertIn("symlink", buf.getvalue())
        self.assertEqual(self.read_invocations(log_path), [])

    def test_relative_and_tilde_command_paths_resolve_consistently_across_cwds(self):
        stub, log_path = self.make_stub_cli("rel-cli", mode="ok")
        other_cwd = self.root / "unrelated-cwd"
        other_cwd.mkdir()

        # Case A: workspace-relative command path invoked from an unrelated caller cwd
        self.ws.set_tracing("example", "session", True, command="./.stub-bin/rel-cli")
        st_proc = subprocess.run(
            [sys.executable, str(PROJECT_SCRIPT), "--workspace", str(self.root), "--project", "example", "tracing", "status"],
            cwd=str(other_cwd), capture_output=True, text=True,
        )
        self.assertEqual(st_proc.returncode, 0, st_proc.stderr)
        st_json = json.loads(st_proc.stdout)
        self.assertTrue(st_json["cli_found"])
        self.assertEqual(Path(st_json["cli_path"]).resolve(), stub.resolve())

        result_d1 = self.build_validated_develop_run("d1", "C1")
        op_file = self.root / "accept-d1.json"
        op_file.write_text(json.dumps({"event_id": "ev-rel-1", "op": "accept", "id": "d1", "result": result_d1}), encoding="utf-8")
        ap_proc = subprocess.run(
            [sys.executable, str(SEARCH_SCRIPT), "--workspace", str(self.root), "--project", "example", "--owner", "session", "apply", str(op_file)],
            cwd=str(other_cwd), capture_output=True, text=True,
        )
        self.assertEqual(ap_proc.returncode, 0, ap_proc.stderr)
        self.assertEqual(len(self.read_invocations(log_path)), 1)
        retry_cmd = self.store.reconcile()["tracing"]["retry_command"]
        self.assertEqual(Path(shlex.split(retry_cmd)[0]).resolve(), stub.resolve())

        # Case B: tilde-prefixed command path expands consistently for status, execution, and retry_command
        fake_home = self.root / "fake-home"
        tilde_cli = fake_home / "bin/tilde-agent-trace"
        _make_executable(tilde_cli, stub.read_text(encoding="utf-8"))
        env = {**os.environ, "HOME": str(fake_home)}
        self.ws.set_tracing("example", "session", True, command="~/bin/tilde-agent-trace")

        st2 = subprocess.run(
            [sys.executable, str(PROJECT_SCRIPT), "--workspace", str(self.root), "--project", "example", "tracing", "status"],
            cwd=str(other_cwd), env=env, capture_output=True, text=True,
        )
        self.assertEqual(st2.returncode, 0, st2.stderr)
        st2_json = json.loads(st2.stdout)
        self.assertTrue(st2_json["cli_found"])
        self.assertEqual(Path(st2_json["cli_path"]).resolve(), tilde_cli.resolve())

        prom_file = self.root / "promote-d1.json"
        prom_file.write_text(
            json.dumps({"event_id": "ev-tilde-prom", "op": "promote-candidate", "run_id": "d1", "reason": "tilde test"}),
            encoding="utf-8",
        )
        ap2 = subprocess.run(
            [sys.executable, str(SEARCH_SCRIPT), "--workspace", str(self.root), "--project", "example", "--owner", "session", "apply", str(prom_file)],
            cwd=str(other_cwd), env=env, capture_output=True, text=True,
        )
        self.assertEqual(ap2.returncode, 0, ap2.stderr)
        self.assertEqual(len(self.read_invocations(log_path)), 2)

        rec2 = subprocess.run(
            [sys.executable, str(SEARCH_SCRIPT), "--workspace", str(self.root), "--project", "example", "tracing-status"],
            cwd=str(other_cwd), env=env, capture_output=True, text=True,
        )
        self.assertEqual(rec2.returncode, 0, rec2.stderr)
        rec2_json = json.loads(rec2.stdout)
        self.assertEqual(rec2_json["state"], "synced")
        self.assertEqual(Path(shlex.split(rec2_json["retry_command"])[0]).resolve(), tilde_cli.resolve())


        # Corrupted external watermark with NUL bytes in project_dir or viewer_path must degrade
        # gracefully to stale/lagging rather than breaking reconcile, tracing-status, or replay.
        sync_file = self.root / ".workspace/example/tracing/traces/harness-example/harness_sync_state.json"
        sync_file.parent.mkdir(parents=True, exist_ok=True)
        for bad_payload in (
            {
                "schema": "agent-tracing/harness-sync@1",
                "project_id": "example",
                "trace_id": "harness-example",
                "project_dir": str(self.root / ".workspace/example") + "\x00bad",
                "upstream_event_count": len(self.store.load()["events"]),
                "last_upstream_event_id": self.store.load()["events"][-1]["operation"]["event_id"],
                "promoted_candidate_id": "C1",
                "viewer_path": str(sync_file.parent / "index.html"),
            },
            {
                "schema": "agent-tracing/harness-sync@1",
                "project_id": "example",
                "trace_id": "harness-example",
                "project_dir": str(self.root / ".workspace/example"),
                "upstream_event_count": len(self.store.load()["events"]),
                "last_upstream_event_id": self.store.load()["events"][-1]["operation"]["event_id"],
                "promoted_candidate_id": "C1",
                "viewer_path": str(sync_file.parent / "index.html") + "\x00bad",
            },
        ):
            sync_file.write_text(json.dumps(bad_payload), encoding="utf-8")
            rec_corrupt = subprocess.run(
                [sys.executable, str(SEARCH_SCRIPT), "--workspace", str(self.root), "--project", "example", "reconcile"],
                cwd=str(other_cwd), env=env, capture_output=True, text=True,
            )
            self.assertEqual(rec_corrupt.returncode, 0, rec_corrupt.stderr)
            self.assertEqual(
                subprocess.run(
                    [sys.executable, str(SEARCH_SCRIPT), "--workspace", str(self.root), "--project", "example", "tracing-status"],
                    cwd=str(other_cwd), env=env, capture_output=True, text=True,
                ).returncode,
                0,
            )

        # Even if project.json is later edited on disk with an unexpandable ~user command,
        # reconcile, tracing-status, accept, and projectctl tracing disable must NOT crash!
        proj_path = self.root / ".workspace/example/project.json"
        raw_proj = json.loads(proj_path.read_text(encoding="utf-8"))
        raw_proj["tracing"]["command"] = "~no-such-user-99999/bin/agent-trace"
        proj_path.write_text(json.dumps(raw_proj, indent=2) + "\n", encoding="utf-8")

        st_bad = self.project_cli("tracing", "status")
        self.assertEqual(st_bad.returncode, 0, st_bad.stderr)
        self.assertIsNotNone(json.loads(st_bad.stdout)["cli_error"])

        rec_bad = self.store.reconcile()["tracing"]
        self.assertEqual(rec_bad["state"], "error")
        self.assertIn("invalid tracing command path", rec_bad["last_error"])

        buf_bad = io.StringIO()
        with patch("sys.stderr", buf_bad):
            self.apply("promote-candidate", run_id="d1", reason="promote with broken tilde")
        self.assertEqual(self.store.load()["best"]["candidate_id"], "C1")
        self.assertIn("invalid tracing command path", buf_bad.getvalue())

        dis_bad = self.project_cli("tracing", "disable")
        self.assertEqual(dis_bad.returncode, 0, dis_bad.stderr)
        self.assertFalse(json.loads(dis_bad.stdout)["enabled"])

    def test_harness_tracing_skill_is_managed_and_linked(self):
        skill_dir = ROOT / "assets/skills/harness-tracing"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "agents/openai.yaml").is_file())
        dev_link = ROOT / ".agents/skills/harness-tracing"
        self.assertTrue(dev_link.is_symlink())
        self.assertEqual(os.readlink(dev_link), "../../assets/skills/harness-tracing")

        install_ws = self.root / "installed-ws"
        install_ws.mkdir()
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/install_codex_skills.py"), "--workspace", str(install_ws)],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((install_ws / ".agents/skills/harness-tracing/SKILL.md").is_file())
        self.assertTrue((install_ws / ".agents/skills/harness-tracing/agents/openai.yaml").is_file())
        self.assertTrue((install_ws / ".agents/skills/harness-resume/scripts/tracing_hook.py").is_file())

        # Upgrade plan works cleanly on a workspace with harness-tracing installed
        plan = subprocess.run(
            [sys.executable, str(ROOT / "scripts/install_codex_skills.py"), "--workspace", str(install_ws), "--upgrade-plan"],
            capture_output=True, text=True,
        )
        self.assertEqual(plan.returncode, 0, plan.stderr)
        self.assertEqual(json.loads(plan.stdout)["changes"], [])


if __name__ == "__main__":
    unittest.main()
