from tempfile import TemporaryDirectory
import unittest
from server.db import WorkbenchStorage, ValidationError, NotFoundError, ConflictError
from server.service import WorkbenchService
from server.seed_data import EXPLORATION_META


class GraphTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = WorkbenchStorage(tmp.name)
        self.s = WorkbenchService(self.db)
        self.e = EXPLORATION_META["id"]
        self.s.create_or_reset_exploration(EXPLORATION_META)
        for id in ["a", "b"]:
            self.s.create_node(self.e, dict(id=id, title=id))
        self.s.create_node(
            self.e,
            dict(
                id="c",
                title="fusion",
                parentIds=["a", "b"],
                derivationNote="combine both",
            ),
        )

    def relation(self, source, target, **extra):
        return dict(
            id=source + "-" + target,
            kind="derivation",
            sourceType="node",
            sourceId=source,
            targetType="node",
            targetId=target,
            rationale="explicit contribution",
            **extra,
        )

    def test_multiple_sources_and_cycles(self):
        snapshot = self.db.get_snapshot(self.e)
        self.assertEqual(
            next(n for n in snapshot["nodes"] if n["id"] == "c")["parentIds"],
            ["a", "b"],
        )
        with self.assertRaises(ValidationError):
            self.s.create_graph_relation(self.e, self.relation("c", "a"))
        with self.assertRaises(NotFoundError):
            self.s.create_graph_relation(self.e, self.relation("foreign", "c"))
        self.s.create_node(self.e, dict(id="d", title="independent"))
        edge = self.relation("c", "d")
        first = self.s.create_graph_relation(self.e, edge, "same")
        self.assertEqual(first, self.s.create_graph_relation(self.e, edge, "same"))
        with self.assertRaises(ConflictError):
            self.s.create_graph_relation(self.e, edge)
        with self.assertRaises(ValidationError):
            self.s.create_reference(
                self.e,
                dict(
                    id="cycle",
                    sourceNodeId="d",
                    targetNodeId="a",
                    purpose="derivation",
                    rationale="bad",
                ),
            )

    def test_knowledge_evidence_requires_real_attempt_and_version(self):
        self.s.upsert_knowledge_item(
            self.e,
            dict(
                id="k",
                title="claim",
                version="v1",
                category="optimization_experience",
                claimType="hypothesis",
                body="test",
                applicability="current input",
                unverifiedScope="other inputs",
            ),
        )
        edge = dict(
            id="evidence",
            kind="support",
            sourceType="node",
            sourceId="c",
            targetType="knowledge",
            targetId="k",
            rationale="observation",
            knowledgeVersion="v1",
        )
        with self.assertRaises(ValidationError):
            self.s.create_graph_relation(self.e, edge)
        self.s.upsert_agent_heartbeat(self.e, {"id": "session"})
        self.s.start_stage_attempt(
            self.e,
            "c",
            dict(id="attempt", code="R1", agentId="session", stageType="read_info"),
        )
        self.s.finish_stage_attempt(self.e, "c", "attempt", dict(status="completed"))
        edge["evidenceAttemptIds"] = ["attempt"]
        self.s.create_graph_relation(self.e, edge)
        with self.assertRaises(ConflictError):
            self.s.upsert_knowledge_item(
                self.e,
                dict(
                    id="k",
                    title="changed",
                    version="v2",
                    category="optimization_experience",
                    claimType="fact",
                    body="changed",
                    applicability="current input",
                    unverifiedScope="other inputs",
                ),
            )
        self.assertTrue(
            any(
                r["kind"] == "support"
                for r in self.db.get_snapshot(self.e)["graphRelations"]
            )
        )

    def test_legacy_projection(self):
        self.s.create_node(self.e, dict(id="old", title="legacy", parentId="a"))
        snapshot = self.db.get_snapshot(self.e)
        self.assertIn(
            "a", next(n for n in snapshot["nodes"] if n["id"] == "old")["parentIds"]
        )
