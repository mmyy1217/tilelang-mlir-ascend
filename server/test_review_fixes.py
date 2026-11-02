"""Regression tests for immutable evidence and persisted permit rejection."""

from tempfile import TemporaryDirectory
import unittest

from server.db import WorkbenchStorage, ValidationError, ConflictError
from server.service import WorkbenchService
from server.seed_data import EXPLORATION_META


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = WorkbenchStorage(self.tmp.name)
        self.s = WorkbenchService(self.db)
        self.e = EXPLORATION_META["id"]
        self.s.create_or_reset_exploration(EXPLORATION_META)
        self.s.upsert_agent_heartbeat(self.e, {"id": "a"})
        self.node = dict(
            id="n", branchLabel="r", title="r", hypothesis="r", changeSummary="r"
        )
        self.s.create_node(self.e, self.node)
        for rid, cat in [("k", "kernel"), ("k2", "kernel"), ("log", "log")]:
            self.s.upload_resource_version(
                self.e,
                dict(
                    id=rid,
                    resourceId=rid,
                    version="v1",
                    title=rid,
                    filename=rid,
                    category=cat,
                    ownerNodeId="n",
                    content=rid,
                ),
            )

    def start(self, aid, stage, deps=(), **extra):
        return self.s.start_stage_attempt(
            self.e,
            "n",
            dict(
                id=aid,
                code=aid,
                agentId="a",
                stageType=stage,
                kernelVersionLabel="v1",
                dependsOnAttemptIds=list(deps),
                **extra,
            ),
        )

    def finish(self, aid, **extra):
        return self.s.finish_stage_attempt(
            self.e, "n", aid, dict(status="completed", **extra), "finish-" + aid
        )

    def chain(self):
        self.start("r", "read_info")
        self.finish("r")
        self.start("p", "plan_idea", ["r"])
        self.finish("p")
        self.start("g", "gen_kernel", ["p"])
        self.finish("g", outputResourceVersionIds=["k"])
        self.start("c", "compile", ["g"])
        self.finish("c")

    def test_fresh_session_can_resume_without_rewriting_history(self):
        self.start("r", "read_info")
        with self.assertRaises(ConflictError):
            self.s.request_stop(self.e, {"finalizeImmediately": True})
        self.s.request_stop(self.e, {})
        with self.assertRaises(ConflictError):
            self.s.resume_exploration(self.e, {})
        self.finish("r")
        self.s.upsert_agent_heartbeat(self.e, {"id": "fresh-session"})
        self.s.resume_exploration(self.e, {})
        self.s.start_stage_attempt(
            self.e,
            "n",
            dict(
                id="p-fresh",
                code="P1",
                agentId="fresh-session",
                stageType="plan_idea",
                dependsOnAttemptIds=["r"],
            ),
        )
        node = self.db.get_snapshot(self.e)["nodes"][0]
        self.assertEqual(node["stages"]["read_info"]["attempts"][0]["agentId"], "a")
        self.assertEqual(
            node["stages"]["plan_idea"]["attempts"][0]["agentId"], "fresh-session"
        )

    def test_unregistered_session_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.s.start_stage_attempt(
                self.e,
                "n",
                dict(
                    id="unknown",
                    code="R1",
                    agentId="unregistered",
                    stageType="read_info",
                ),
            )

    def test_invalid_kernel_and_mismatched_input(self):
        with self.assertRaises(ValidationError):
            self.s.create_node(
                self.e,
                {
                    **self.node,
                    "id": "bad",
                    "parentId": "n",
                    "primaryKernelVersionId": "missing",
                },
            )
        self.start("r", "read_info")
        self.finish("r")
        self.start("p", "plan_idea", ["r"])
        self.finish("p")
        self.start("g", "gen_kernel", ["p"])
        for rid in ["missing", "log"]:
            with self.assertRaises(ValidationError):
                self.finish("g", outputResourceVersionIds=[rid])
        self.finish("g", outputResourceVersionIds=["k"])
        with self.assertRaises(ValidationError):
            self.start("bad-c", "compile", ["g"], inputResourceVersionIds=["k2"])
        self.start("c", "compile", ["g"])
        with self.db.read_conn() as conn:
            self.assertIn(
                "k",
                conn.execute(
                    "SELECT input_resource_version_ids_json FROM stage_attempts WHERE id='c'"
                ).fetchone()[0],
            )

    def test_terminal_immutable_and_idempotent(self):
        self.start("r", "read_info")
        result = self.finish("r")
        self.assertEqual(result, self.finish("r"))
        with self.assertRaises(ConflictError):
            self.s.finish_stage_attempt(self.e, "n", "r", {"status": "failed"})
        self.assertEqual(
            self.db.get_snapshot(self.e)["nodes"][0]["stages"]["read_info"]["status"],
            "completed",
        )

    def test_budget_stop_survives_rejection(self):
        self.chain()
        with self.db.write_tx() as conn:
            conn.execute(
                "UPDATE explorations SET budget_json = ? WHERE id = ?",
                ('{"maxEvaluations":0}', self.e),
            )
        with self.assertRaises(ValidationError):
            self.start("v", "verify_correctness", ["c"])
        snap = self.db.get_snapshot(self.e)
        self.assertEqual(snap["coordinatorStatus"], "stopping")
        self.assertEqual(
            self.db.fetch_events_batch(self.e, snap["lastEventSeq"] - 1)[0][
                "eventType"
            ],
            "exploration.stopping",
        )
        with self.assertRaises(ValidationError):
            self.start("r2", "read_info")


if __name__ == "__main__":
    unittest.main()
