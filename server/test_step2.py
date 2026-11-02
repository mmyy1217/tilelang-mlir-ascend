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

    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        # 1. Run the two parallel agent simulators (agent-cube & agent-vec) over HTTP!
        sim_res = await run_two_agent_simulation(http_client=client, delay_sec=0.0)
        assert sim_res["explorationId"] == "exp-fa2-mixcv"

        # 2. Fetch full normalized snapshot and verify watermark + graph + 8-stage state machine
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
        assert set(n4["agentIds"]) == {
            "agent-cube",
            "agent-vec",
            "agent-eval",
            "agent-coord",
        }

        g_attempts = n4["stages"]["gen_kernel"]["attempts"]
        assert [a["code"] for a in g_attempts] == ["G1", "G2"]
        assert g_attempts[1]["dependsOnAttemptIds"] == ["N4-C1", "N4-C2"]
        assert g_attempts[1]["kernelVersionLabel"] == "v2"

        c_attempts = n4["stages"]["compile"]["attempts"]
        assert [
            (a["code"], a["status"], a["kernelVersionLabel"]) for a in c_attempts
        ] == [
            ("C1", "failed", "v1"),
            ("C2", "failed", "v1"),
            ("C3", "completed", "v2"),
        ]
        assert c_attempts[2]["dependsOnAttemptIds"] == ["N4-G2"]

        v_attempts = n4["stages"]["verify_correctness"]["attempts"]
        assert [
            (a["code"], a["status"], a["kernelVersionLabel"]) for a in v_attempts
        ] == [
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
        assert {"res-cfg-v1", "res-n1-prof-v1", "res-n3-summary-v1"}.issubset(
            shared_ver_ids
        )

        content_resp = await client.get(
            "/api/explorations/exp-fa2-mixcv/resources/res-n4-kernel-v2/content"
        )
        assert content_resp.status_code == 200
        content_data = content_resp.json()
        assert "def flash_attn_mixcv_n4_v2" in content_data["content"]
        assert (
            content_data["sha256"]
            == hashlib.sha256(content_data["content"].encode("utf-8")).hexdigest()
        )

        dl_resp = await client.get(
            "/api/explorations/exp-fa2-mixcv/resources/res-n4-prof-v1/download"
        )
        assert dl_resp.status_code == 200
        assert (
            'attachment; filename="msprof_n4_summary@v1.bin"'
            in dl_resp.headers["content-disposition"]
        )

        # 5. Verify Idempotency (same X-Request-Id + same payload => 200; different payload => 409)
        hb_payload = {"id": "agent-cube", "status": "idle", "currentNodeId": "N4"}
        r1 = await client.post(
            "/api/explorations/exp-fa2-mixcv/agents/heartbeat",
            json=hb_payload,
            headers={
                "X-Agent-Token": "agent-secret-token",
                "X-Request-Id": "idem-hb-1",
            },
        )
        assert r1.status_code == 200
        r2 = await client.post(
            "/api/explorations/exp-fa2-mixcv/agents/heartbeat",
            json=hb_payload,
            headers={
                "X-Agent-Token": "agent-secret-token",
                "X-Request-Id": "idem-hb-1",
            },
        )
        assert r2.status_code == 200
        assert r2.json()["event"]["eventId"] == r1.json()["event"]["eventId"]

        r_conflict = await client.post(
            "/api/explorations/exp-fa2-mixcv/agents/heartbeat",
            json={"id": "agent-cube", "status": "active", "currentNodeId": "N5"},
            headers={
                "X-Agent-Token": "agent-secret-token",
                "X-Request-Id": "idem-hb-1",
            },
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
            headers={
                "X-Agent-Token": "agent-secret-token",
                "X-Request-Id": "sse-gap-1",
            },
        )
        await client.post(
            "/api/explorations/exp-fa2-mixcv/agents/heartbeat",
            json={"id": "agent-eval", "status": "active", "currentNodeId": "N5"},
            headers={
                "X-Agent-Token": "agent-secret-token",
                "X-Request-Id": "sse-gap-2",
            },
        )

        sse_resp = await client.get(
            f"/api/explorations/exp-fa2-mixcv/events?after_seq={watermark_before}&max_events=2"
        )
        assert sse_resp.status_code == 200
        sse_text = sse_resp.text
        assert f"id: {watermark_before + 1}" in sse_text
        assert f"id: {watermark_before + 2}" in sse_text
        assert "event: domain_event" in sse_text


@pytest.mark.anyio
async def test_codex_review_regressions(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data_codex", auto_seed=False)
    transport = httpx.ASGITransport(app=app)
    agent_headers = {"X-Agent-Token": "agent-secret-token"}
    op_headers = {"X-Operator-Token": "operator-secret-token"}

    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        await run_two_agent_simulation(
            service=app.state.service, http_client=client, delay_sec=0.0
        )

        # ------------------------------------------------------------------
        # [P1 #1] /simulate requires operator credential AND demo exp_id isolation
        # ------------------------------------------------------------------
        sim_no_auth = await client.post(
            "/api/explorations/exp-fa2-mixcv/simulate",
            json={"mode": "sync", "delaySec": 0.0},
        )
        assert sim_no_auth.status_code == 403

        sim_non_demo = await client.post(
            "/api/explorations/exp-fa2-mixcv/simulate",
            json={"mode": "sync", "delaySec": 0.0},
            headers=op_headers,
        )
        assert sim_non_demo.status_code == 403

        sim_demo_ok = await client.post(
            "/api/explorations/demo-fa2-mixcv/simulate",
            json={"mode": "sync", "delaySec": 0.0},
            headers=op_headers,
        )
        assert sim_demo_ok.status_code == 200
        assert sim_demo_ok.json()["explorationId"] == "demo-fa2-mixcv"

        # ------------------------------------------------------------------
        # [P1 #4] Internal seed bypass flags are rejected on public APIs
        # ------------------------------------------------------------------
        bypass_reset = await client.post(
            "/api/explorations",
            json={
                "id": "exp-fa2-mixcv",
                "title": "Overwrite attempt",
                "resetIfExists": True,
            },
            headers=agent_headers,
        )
        assert bypass_reset.status_code == 422

        bypass_node = await client.post(
            "/api/explorations/exp-fa2-mixcv/nodes",
            json={
                "id": "N-BYPASS",
                "parentId": "N0",
                "branchLabel": "Bypass",
                "title": "Bypass stop",
                "allowDuringSeed": True,
            },
            headers=agent_headers,
        )
        assert bypass_node.status_code == 422

        bypass_att = await client.post(
            "/api/explorations/exp-fa2-mixcv/nodes/N4/attempts",
            json={
                "id": "N4-BYPASS-ATT",
                "stageType": "bench_perf",
                "code": "B9",
                "round": 2,
                "agentId": "agent-eval",
                "dependsOnAttemptIds": ["N4-V2"],
                "kernelVersionLabel": "v2",
                "skipBudgetReservation": True,
            },
            headers=agent_headers,
        )
        assert bypass_att.status_code == 422

        bypass_cooled = await client.post(
            "/api/explorations/exp-fa2-mixcv/knowledge",
            json={
                "id": "K-BYPASS",
                "category": "basic_knowledge",
                "claimType": "fact",
                "title": "Fake cooled",
                "version": "cooled@v9",
                "contentStatus": "cooled",
                "allowPinnedCooledBaseline": True,
            },
            headers=agent_headers,
        )
        assert bypass_cooled.status_code == 422

        # ------------------------------------------------------------------
        # [P1 #2] Stage preconditions cannot be bypassed; uncompiled/unmeasured
        #         nodes cannot become bestValidNodeId
        # ------------------------------------------------------------------
        c_node = await client.post(
            "/api/explorations/exp-fa2-mixcv/nodes",
            json={
                "id": "N-EXPLOIT",
                "parentId": "N0",
                "branchLabel": "Exploit test",
                "title": "N-EXPLOIT",
                "hypothesis": "Test stage precondition enforcement",
                "changeSummary": "None",
                "agentIds": ["agent-vec"],
            },
            headers=agent_headers,
        )
        assert c_node.status_code == 200

        # Cannot jump straight to verify_correctness or bench_perf with empty dependsOnAttemptIds
        jump_verify = await client.post(
            "/api/explorations/exp-fa2-mixcv/nodes/N-EXPLOIT/attempts",
            json={
                "id": "NEX-V1",
                "stageType": "verify_correctness",
                "code": "V1",
                "round": 1,
                "agentId": "agent-eval",
                "dependsOnAttemptIds": [],
                "kernelVersionLabel": "v1",
            },
            headers=agent_headers,
        )
        assert jump_verify.status_code == 422

        jump_bench = await client.post(
            "/api/explorations/exp-fa2-mixcv/nodes/N-EXPLOIT/attempts",
            json={
                "id": "NEX-B1",
                "stageType": "bench_perf",
                "code": "B1",
                "round": 1,
                "agentId": "agent-eval",
                "dependsOnAttemptIds": [],
                "kernelVersionLabel": "v1",
            },
            headers=agent_headers,
        )
        assert jump_bench.status_code == 422

        # ------------------------------------------------------------------
        # [P1 #3] New kernel version (@v2) invalidates @v1 latency & ranking
        #         until @v2 completes its own compile -> verify -> bench_perf chain
        # ------------------------------------------------------------------
        c_ver = await client.post(
            "/api/explorations/exp-fa2-mixcv/nodes",
            json={
                "id": "N-VER",
                "parentId": "N4",
                "branchLabel": "Version chain test",
                "title": "N-VER",
                "hypothesis": "Test version-bound attempt chain",
                "changeSummary": "Test @v1 -> @v2 latency invalidation",
                "primaryKernelVersionId": "res-n4-kernel-v2",
                "agentIds": ["agent-vec", "agent-eval"],
            },
            headers=agent_headers,
        )
        assert c_ver.status_code == 200

        async def run_att(
            aid: str,
            st: str,
            code: str,
            rnd: int,
            deps: list[str],
            kv: str | None = None,
            corr: bool | None = None,
            lat: float | None = None,
        ) -> None:
            r_s = await client.post(
                "/api/explorations/exp-fa2-mixcv/nodes/N-VER/attempts",
                json={
                    "id": aid,
                    "stageType": st,
                    "code": code,
                    "round": rnd,
                    "agentId": "agent-eval",
                    "dependsOnAttemptIds": deps,
                    "kernelVersionLabel": kv,
                },
                headers=agent_headers,
            )
            assert r_s.status_code == 200, r_s.text
            fin: dict = {"status": "completed", "summary": f"done {aid}"}
            if corr is not None:
                fin["correctnessPassed"] = corr
            if lat is not None:
                fin["latencyUs"] = lat
            r_f = await client.patch(
                f"/api/explorations/exp-fa2-mixcv/nodes/N-VER/attempts/{aid}",
                json=fin,
                headers=agent_headers,
            )
            assert r_f.status_code == 200, r_f.text

        await run_att("NVER-R1", "read_info", "R1", 1, [])
        await run_att("NVER-P1", "plan_idea", "P1", 1, ["NVER-R1"])
        await run_att("NVER-G1", "gen_kernel", "G1", 1, ["NVER-P1"], kv="v1")
        await run_att("NVER-C1", "compile", "C1", 1, ["NVER-G1"], kv="v1")

        # Verify that finishing verify_correctness without explicit correctnessPassed or with latencyUs fails
        r_v1_start = await client.post(
            "/api/explorations/exp-fa2-mixcv/nodes/N-VER/attempts",
            json={
                "id": "NVER-V1",
                "stageType": "verify_correctness",
                "code": "V1",
                "round": 1,
                "agentId": "agent-eval",
                "dependsOnAttemptIds": ["NVER-C1"],
                "kernelVersionLabel": "v1",
            },
            headers=agent_headers,
        )
        assert r_v1_start.status_code == 200

        r_v1_missing_bool = await client.patch(
            "/api/explorations/exp-fa2-mixcv/nodes/N-VER/attempts/NVER-V1",
            json={"status": "completed", "summary": "missing correctnessPassed"},
            headers=agent_headers,
        )
        assert r_v1_missing_bool.status_code == 422

        r_v1_illegal_lat = await client.patch(
            "/api/explorations/exp-fa2-mixcv/nodes/N-VER/attempts/NVER-V1",
            json={
                "status": "completed",
                "correctnessPassed": True,
                "latencyUs": 99.0,
                "summary": "illegal latency in verify stage",
            },
            headers=agent_headers,
        )
        assert r_v1_illegal_lat.status_code == 422

        # Finish V1 properly, then B1 with 120.0 us -> N-VER becomes bestValidNodeId
        r_v1_ok = await client.patch(
            "/api/explorations/exp-fa2-mixcv/nodes/N-VER/attempts/NVER-V1",
            json={
                "status": "completed",
                "correctnessPassed": True,
                "summary": "V1 pass",
            },
            headers=agent_headers,
        )
        assert r_v1_ok.status_code == 200
        await run_att("NVER-B1", "bench_perf", "B1", 1, ["NVER-V1"], kv="v1", lat=120.0)

        snap_v1 = (await client.get("/api/explorations/exp-fa2-mixcv/snapshot")).json()
        assert snap_v1["bestValidNodeId"] == "N-VER"

        # Now generate @v2 (G2 -> C2 -> V2 pass) WITHOUT running B2 (@v2):
        # N-VER must NOT inherit @v1's 120.0 us latency and must NOT be bestValidNodeId!
        await run_att("NVER-G2", "gen_kernel", "G2", 2, ["NVER-P1"], kv="v2")
        await run_att("NVER-C2", "compile", "C2", 2, ["NVER-G2"], kv="v2")
        await run_att(
            "NVER-V2", "verify_correctness", "V2", 2, ["NVER-C2"], kv="v2", corr=True
        )

        snap_v2_unbenched = (
            await client.get("/api/explorations/exp-fa2-mixcv/snapshot")
        ).json()
        nver_node = {n["id"]: n for n in snap_v2_unbenched["nodes"]}["N-VER"]
        assert nver_node["correctnessPassed"] is True
        assert nver_node["latencyUs"] is None
        assert snap_v2_unbenched["bestValidNodeId"] == "N4"

        # Once B2 (@v2) completes with 118.5 us, N-VER lawfully becomes bestValidNodeId
        await run_att("NVER-B2", "bench_perf", "B2", 2, ["NVER-V2"], kv="v2", lat=118.5)
        snap_v2_benched = (
            await client.get("/api/explorations/exp-fa2-mixcv/snapshot")
        ).json()
        nver_node_after = {n["id"]: n for n in snap_v2_benched["nodes"]}["N-VER"]
        assert nver_node_after["latencyUs"] == 118.5
        assert snap_v2_benched["bestValidNodeId"] == "N-VER"
