from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from .app import create_app
from .simulator import run_two_agent_simulation


@pytest.mark.anyio
async def test_step2_parallel_agents_and_domain_invariants(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", auto_seed=False)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Run the two parallel agent simulators (agent-cube & agent-vec) over HTTP!
        sim_res = await run_two_agent_simulation(http_client=client, delay_sec=0.0)
        assert sim_res["explorationId"] == "exp-fa2-mixcv"

        # 2. Fetch full normalized snapshot and verify watermark + tree + 8-stage state machine
        snap_resp = await client.get("/api/explorations/exp-fa2-mixcv/snapshot")
        assert snap_resp.status_code == 200
        snap = snap_resp.json()

        assert snap["id"] == "exp-fa2-mixcv"
        assert snap["bestValidNodeId"] == "N4"
        assert snap["lastEventSeq"] > 40
        assert snap["budget"]["usedEvaluations"] == 16
        assert snap["budget"]["reservedEvaluations"] == 1

        nodes_by_id = {n["id"]: n for n in snap["nodes"]}
        assert set(nodes_by_id.keys()) == {"N0", "N1", "N2", "N3", "N4", "N5"}

        # Verify N4 rollback chain: G1->C1/C2 fail -> V1 skipped -> G2(<-C1,C2)->C3(<-G2)->V2(<-C3) pass
        n4 = nodes_by_id["N4"]
        assert n4["parentId"] == "N1"
        assert n4["correctnessPassed"] is True
        assert n4["latencyUs"] == 132.4
        assert n4["contentStatus"] == "ready-for-cool"
        assert set(n4["agentIds"]) == {"agent-cube", "agent-vec", "agent-eval", "agent-coord"}

        g_attempts = n4["stages"]["gen_kernel"]["attempts"]
        assert [a["code"] for a in g_attempts] == ["G1", "G2"]
        assert g_attempts[1]["dependsOnAttemptIds"] == ["N4-C1", "N4-C2"]
        assert g_attempts[1]["kernelVersionLabel"] == "v2"

        c_attempts = n4["stages"]["compile"]["attempts"]
        assert [(a["code"], a["status"], a["kernelVersionLabel"]) for a in c_attempts] == [
            ("C1", "failed", "v1"),
            ("C2", "failed", "v1"),
            ("C3", "completed", "v2"),
        ]
        assert c_attempts[2]["dependsOnAttemptIds"] == ["N4-G2"]

        v_attempts = n4["stages"]["verify_correctness"]["attempts"]
        assert [(a["code"], a["status"], a["kernelVersionLabel"]) for a in v_attempts] == [
            ("V1", "skipped", "v1"),
            ("V2", "completed", "v2"),
        ]
        assert v_attempts[1]["dependsOnAttemptIds"] == ["N4-C3"]

        # Verify N5 is in-flight (running V1) and NOT ranked as bestValidNodeId
        n5 = nodes_by_id["N5"]
        assert n5["executionStatus"] == "running"
        assert n5["correctnessPassed"] is False
        assert n5["stages"]["verify_correctness"]["status"] == "running"

        # 3. Verify Derivation vs Cross-Branch References & Re-verification
        refs_by_id = {r["id"]: r for r in snap["references"]}
        assert refs_by_id["edge-der-n1-n4"]["purpose"] == "derivation"
        xref = refs_by_id["edge-xref-n3-n4"]
        assert xref["purpose"] == "cross_branch"
        assert xref["sourceNodeId"] == "N3"
        assert xref["targetNodeId"] == "N4"
        assert xref["referencedResourceVersionId"] == "res-n3-summary-v1"
        assert xref["reverifiedStatus"] == "verified"
        assert xref["reverifiedInAttemptId"] == "N4-V2"

        # 4. Verify Shared Files & Content-Addressable Blob Retrieval + Download
        n4_links = [lnk for lnk in snap["resourceLinks"] if lnk["nodeId"] == "N4"]
        shared_links = [lnk for lnk in n4_links if lnk["isSharedReference"]]
        shared_ver_ids = {lnk["resourceVersionId"] for lnk in shared_links}
        assert {"res-cfg-v1", "res-n1-prof-v1", "res-n3-summary-v1"}.issubset(shared_ver_ids)

        content_resp = await client.get(
            "/api/explorations/exp-fa2-mixcv/resources/res-n4-kernel-v2/content"
        )
        assert content_resp.status_code == 200
        content_data = content_resp.json()
        assert "def flash_attn_mixcv_n4_v2" in content_data["content"]
        assert content_data["sha256"] == hashlib.sha256(content_data["content"].encode("utf-8")).hexdigest()

        dl_resp = await client.get(
            "/api/explorations/exp-fa2-mixcv/resources/res-n4-prof-v1/download"
        )
        assert dl_resp.status_code == 200
        assert "attachment; filename=\"msprof_n4_summary@v1.bin\"" in dl_resp.headers["content-disposition"]

        # 5. Verify Idempotency (same X-Request-Id + same payload => 200; different payload => 409)
        hb_payload = {"id": "agent-cube", "status": "idle", "currentNodeId": "N4"}
        r1 = await client.post(
            "/api/explorations/exp-fa2-mixcv/agents/heartbeat",
            json=hb_payload,
            headers={"X-Agent-Token": "agent-secret-token", "X-Request-Id": "idem-hb-1"},
        )
        assert r1.status_code == 200
        r2 = await client.post(
            "/api/explorations/exp-fa2-mixcv/agents/heartbeat",
            json=hb_payload,
            headers={"X-Agent-Token": "agent-secret-token", "X-Request-Id": "idem-hb-1"},
        )
        assert r2.status_code == 200
        assert r2.json()["event"]["eventId"] == r1.json()["event"]["eventId"]

        r_conflict = await client.post(
            "/api/explorations/exp-fa2-mixcv/agents/heartbeat",
            json={"id": "agent-cube", "status": "active", "currentNodeId": "N5"},
            headers={"X-Agent-Token": "agent-secret-token", "X-Request-Id": "idem-hb-1"},
        )
        assert r_conflict.status_code == 409

        # 6. Verify Kernel Version Mismatch Rejection & cooled Direct Write Rejection
        bad_ver_resp = await client.post(
            "/api/explorations/exp-fa2-mixcv/nodes/N4/attempts",
            json={
                "id": "N4-B-BAD",
                "stageType": "bench_perf",
                "code": "B3",
                "round": 2,
                "agentId": "agent-eval",
                "dependsOnAttemptIds": ["N4-C1"],  # C1 is v1, cannot validate v2!
                "kernelVersionLabel": "v2",
            },
            headers={"X-Agent-Token": "agent-secret-token"},
        )
        assert bad_ver_resp.status_code == 422

        bad_cool_resp = await client.post(
            "/api/explorations/exp-fa2-mixcv/candidates/mark",
            json={"contentStatus": "cooled", "nodeIds": ["N4"]},
            headers={"X-Agent-Token": "agent-secret-token"},
        )
        assert bad_cool_resp.status_code == 422

        # 7. Verify SSE Resumable Event Replay (after_seq & Last-Event-ID catch-up)
        watermark_before = r1.json()["event"]["seq"]
        # Emit 2 new events while "disconnected"
        await client.post(
            "/api/explorations/exp-fa2-mixcv/agents/heartbeat",
            json={"id": "agent-vec", "status": "active", "currentNodeId": "N5"},
            headers={"X-Agent-Token": "agent-secret-token", "X-Request-Id": "sse-gap-1"},
        )
        await client.post(
            "/api/explorations/exp-fa2-mixcv/agents/heartbeat",
            json={"id": "agent-eval", "status": "active", "currentNodeId": "N5"},
            headers={"X-Agent-Token": "agent-secret-token", "X-Request-Id": "sse-gap-2"},
        )

        sse_resp = await client.get(
            f"/api/explorations/exp-fa2-mixcv/events?after_seq={watermark_before}&max_events=2"
        )
        assert sse_resp.status_code == 200
        sse_text = sse_resp.text
        assert f"id: {watermark_before + 1}" in sse_text
        assert f"id: {watermark_before + 2}" in sse_text
        assert "event: domain_event" in sse_text
