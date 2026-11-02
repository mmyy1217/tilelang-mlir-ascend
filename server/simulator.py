from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any, Callable, Coroutine, Dict, List, Optional

import httpx

from .seed_data import AGENTS_SEED, EXPLORATION_META, KNOWLEDGE_SEED, RESOURCES_SEED
from .service import WorkbenchService


class SimulationClient:
    """Unified async client that can drive either an HTTP API (httpx) or a local WorkbenchService."""

    def __init__(
        self,
        agent_id: str,
        service: Optional[WorkbenchService] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        agent_token: str = "agent-secret-token",
        internal_seed: bool = False,
    ) -> None:
        self.agent_id = agent_id
        self.service = service
        self.http_client = http_client
        self.agent_token = agent_token
        self.internal_seed = internal_seed

    def _headers(self, req_key: str) -> Dict[str, str]:
        return {
            "X-Agent-Id": self.agent_id,
            "X-Agent-Token": self.agent_token,
            "X-Request-Id": f"{self.agent_id}-{req_key}",
        }

    async def create_exploration(self, payload: Dict[str, Any], req_key: str) -> Dict[str, Any]:
        if self.http_client and not self.internal_seed:
            resp = await self.http_client.post(
                "/api/explorations", json=payload, headers=self._headers(req_key)
            )
            resp.raise_for_status()
            return resp.json()
        assert self.service is not None
        return await asyncio.to_thread(
            self.service.create_or_reset_exploration,
            payload,
            f"{self.agent_id}-{req_key}",
            _internal_seed=self.internal_seed,
        )

    async def heartbeat(self, exp_id: str, payload: Dict[str, Any], req_key: str) -> Dict[str, Any]:
        if self.http_client and not self.internal_seed:
            resp = await self.http_client.post(
                f"/api/explorations/{exp_id}/agents/heartbeat",
                json=payload,
                headers=self._headers(req_key),
            )
            resp.raise_for_status()
            return resp.json()
        assert self.service is not None
        return await asyncio.to_thread(
            self.service.upsert_agent_heartbeat, exp_id, payload, f"{self.agent_id}-{req_key}"
        )

    async def upload_resource(
        self, exp_id: str, res_def: Dict[str, Any], req_key: str
    ) -> Dict[str, Any]:
        content_bytes = res_def["content"].encode("utf-8")
        expected_sha256 = hashlib.sha256(content_bytes).hexdigest()
        payload = {**res_def, "expectedSha256": expected_sha256}
        if self.http_client and not self.internal_seed:
            resp = await self.http_client.post(
                f"/api/explorations/{exp_id}/resources",
                json=payload,
                headers=self._headers(req_key),
            )
            resp.raise_for_status()
            return resp.json()
        assert self.service is not None
        return await asyncio.to_thread(
            self.service.upload_resource_version,
            exp_id,
            payload,
            f"{self.agent_id}-{req_key}",
            _internal_seed=self.internal_seed,
        )

    async def link_resource(
        self, exp_id: str, payload: Dict[str, Any], req_key: str
    ) -> Dict[str, Any]:
        if self.http_client and not self.internal_seed:
            resp = await self.http_client.post(
                f"/api/explorations/{exp_id}/resource-links",
                json=payload,
                headers=self._headers(req_key),
            )
            resp.raise_for_status()
            return resp.json()
        assert self.service is not None
        return await asyncio.to_thread(
            self.service.create_resource_link, exp_id, payload, f"{self.agent_id}-{req_key}"
        )

    async def create_node(
        self, exp_id: str, payload: Dict[str, Any], req_key: str
    ) -> Dict[str, Any]:
        if self.http_client and not self.internal_seed:
            resp = await self.http_client.post(
                f"/api/explorations/{exp_id}/nodes",
                json=payload,
                headers=self._headers(req_key),
            )
            resp.raise_for_status()
            return resp.json()
        assert self.service is not None
        return await asyncio.to_thread(
            self.service.create_node,
            exp_id,
            payload,
            f"{self.agent_id}-{req_key}",
            _internal_seed=self.internal_seed,
        )

    async def create_reference(
        self, exp_id: str, payload: Dict[str, Any], req_key: str
    ) -> Dict[str, Any]:
        if self.http_client and not self.internal_seed:
            resp = await self.http_client.post(
                f"/api/explorations/{exp_id}/references",
                json=payload,
                headers=self._headers(req_key),
            )
            resp.raise_for_status()
            return resp.json()
        assert self.service is not None
        return await asyncio.to_thread(
            self.service.create_reference, exp_id, payload, f"{self.agent_id}-{req_key}"
        )

    async def start_attempt(
        self, exp_id: str, node_id: str, payload: Dict[str, Any], req_key: str
    ) -> Dict[str, Any]:
        if self.http_client and not self.internal_seed:
            resp = await self.http_client.post(
                f"/api/explorations/{exp_id}/nodes/{node_id}/attempts",
                json=payload,
                headers=self._headers(req_key),
            )
            resp.raise_for_status()
            return resp.json()
        assert self.service is not None
        return await asyncio.to_thread(
            self.service.start_stage_attempt,
            exp_id,
            node_id,
            payload,
            f"{self.agent_id}-{req_key}",
            _internal_seed=self.internal_seed,
        )

    async def finish_attempt(
        self,
        exp_id: str,
        node_id: str,
        attempt_id: str,
        payload: Dict[str, Any],
        req_key: str,
    ) -> Dict[str, Any]:
        if self.http_client and not self.internal_seed:
            resp = await self.http_client.patch(
                f"/api/explorations/{exp_id}/nodes/{node_id}/attempts/{attempt_id}",
                json=payload,
                headers=self._headers(req_key),
            )
            resp.raise_for_status()
            return resp.json()
        assert self.service is not None
        return await asyncio.to_thread(
            self.service.finish_stage_attempt,
            exp_id,
            node_id,
            attempt_id,
            payload,
            f"{self.agent_id}-{req_key}",
            _internal_seed=self.internal_seed,
        )

    async def upsert_knowledge(
        self, exp_id: str, payload: Dict[str, Any], req_key: str
    ) -> Dict[str, Any]:
        if self.http_client and not self.internal_seed:
            resp = await self.http_client.post(
                f"/api/explorations/{exp_id}/knowledge",
                json=payload,
                headers=self._headers(req_key),
            )
            resp.raise_for_status()
            return resp.json()
        assert self.service is not None
        return await asyncio.to_thread(
            self.service.upsert_knowledge_item,
            exp_id,
            payload,
            f"{self.agent_id}-{req_key}",
            _internal_seed=self.internal_seed,
        )


async def _run_single_attempt(
    client: SimulationClient,
    exp_id: str,
    node_id: str,
    att_id: str,
    stage_type: str,
    code: str,
    round_num: int,
    agent_id: str,
    started_at: str,
    ended_at: Optional[str],
    depends_on: List[str],
    summary: str,
    status: str = "completed",
    kernel_ver: Optional[str] = None,
    in_res: Optional[List[str]] = None,
    out_res: Optional[List[str]] = None,
    fail_reason: Optional[str] = None,
    reused_from: Optional[str] = None,
    correctness_passed: Optional[bool] = None,
    latency_us: Optional[float] = None,
    speedup: Optional[float] = None,
    pipeline_metrics: Optional[Dict[str, Any]] = None,
    node_exec_status: Optional[str] = None,
    node_content_status: Optional[str] = None,
    node_stop_reason: Optional[str] = None,
    missing_reasons: Optional[Dict[str, str]] = None,
    delay_sec: float = 0.0,
) -> None:
    await client.start_attempt(
        exp_id,
        node_id,
        {
            "id": att_id,
            "stageType": stage_type,
            "code": code,
            "round": round_num,
            "agentId": agent_id,
            "startedAt": started_at,
            "dependsOnAttemptIds": depends_on,
            "kernelVersionLabel": kernel_ver,
            "inputResourceVersionIds": in_res or [],
            "summary": summary if status == "running" else f"正在执行 {code} ({stage_type})...",
            "reusedFromAttemptId": reused_from,
        },
        req_key=f"start-{att_id}",
    )
    if delay_sec > 0:
        await asyncio.sleep(delay_sec)
    if status != "running":
        finish_payload: Dict[str, Any] = {
            "status": status,
            "endedAt": ended_at,
            "outputResourceVersionIds": out_res or [],
            "summary": summary,
            "failureOrSkipReason": fail_reason,
        }
        if correctness_passed is not None:
            finish_payload["correctnessPassed"] = correctness_passed
        if latency_us is not None:
            finish_payload["latencyUs"] = latency_us
        if speedup is not None:
            finish_payload["speedupVsBaseline"] = speedup
        if pipeline_metrics is not None:
            finish_payload["pipelineMetrics"] = pipeline_metrics
        if node_exec_status is not None:
            finish_payload["nodeExecutionStatus"] = node_exec_status
        if node_content_status is not None:
            finish_payload["nodeContentStatus"] = node_content_status
        if node_stop_reason is not None:
            finish_payload["nodeStopOrFailReason"] = node_stop_reason
        if missing_reasons is not None:
            finish_payload["missingStageReasons"] = missing_reasons

        await client.finish_attempt(
            exp_id, node_id, att_id, finish_payload, req_key=f"finish-{att_id}"
        )


async def run_two_agent_simulation(
    service: Optional[WorkbenchService] = None,
    http_client: Optional[httpx.AsyncClient] = None,
    delay_sec: float = 0.0,
    exp_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute the complete FlashAttention-2 MixCV exploration using two parallel Agent simulators:
    - Agent-Alpha (agent-cube): explores Cube L1/L0C tiling branches (N1, N2) and co-authors N4.
    - Agent-Beta (agent-vec): explores Vector Ping-Pong synchronization (N3), co-authors N4 rollback fix, and launches N5.
    """
    if service is None and http_client is not None:
        app_obj = getattr(getattr(http_client, "_transport", None), "app", None)
        if app_obj is not None:
            service = getattr(getattr(app_obj, "state", None), "service", None)

    target_exp_id = exp_id or EXPLORATION_META["id"]
    res_by_id = {r["id"]: r for r in RESOURCES_SEED}
    know_by_id = {k["id"]: k for k in KNOWLEDGE_SEED}

    seed_client = SimulationClient(
        "agent-coord", service=service, http_client=http_client, internal_seed=True
    )
    coord_client = SimulationClient("agent-coord", service=service, http_client=http_client)
    cube_client = SimulationClient("agent-cube", service=service, http_client=http_client)
    vec_client = SimulationClient("agent-vec", service=service, http_client=http_client)

    # 1. Create / Reset Exploration & Register Agents (Internal seed bootstrap)
    await seed_client.create_exploration(
        {**EXPLORATION_META, "id": target_exp_id}, req_key="init-exp"
    )
    exp_id = target_exp_id
    for ag in AGENTS_SEED:
        await coord_client.heartbeat(exp_id, ag, req_key=f"init-ag-{ag['id']}")

    # 2. Seed Root Node N0 + Pinned Cooled Baseline Resources + Knowledge K-01 (Internal seed bootstrap)
    for rid in ["res-cfg-v1", "res-n0-kernel-v1", "res-n0-exp-v1", "res-n0-prof-v1"]:
        await seed_client.upload_resource(exp_id, res_by_id[rid], req_key=f"up-{rid}")
    await seed_client.upsert_knowledge(exp_id, know_by_id["K-01"], req_key="know-K01")

    await seed_client.create_node(
        exp_id,
        {
            "id": "N0",
            "parentId": None,
            "branchLabel": "Root Baseline",
            "title": "N0 · Cooled 基线 (64x64 单缓冲顺序流水)",
            "hypothesis": "固定 cooled@v1.4 基线，采集初始耗时与 msprof 流水线瓶颈作为整棵搜索树起点。",
            "changeSummary": "初始 64x64 分块，Q/K 在内层循环重复搬运，Cube 与 Vector 使用单 event_id=0 串行等待。",
            "derivationNote": "根节点：绑定固定 cooled 基线 @v1.4 与共享评测契约 fa2_task_spec@v1.yaml。",
            "primaryKernelVersionId": "res-n0-kernel-v1",
            "agentIds": ["agent-coord", "agent-eval"],
            "executionStatus": "running",
            "contentStatus": "cooled",
        },
        req_key="create-N0",
    )
    await seed_client.link_resource(
        exp_id,
        {
            "id": "lnk-01",
            "nodeId": "N0",
            "stageType": "read_info",
            "attemptId": "N0-R1",
            "resourceVersionId": "res-cfg-v1",
            "role": "shared_baseline",
            "sourceNodeId": "N0",
            "isSharedReference": False,
            "note": "根节点定义基准评测契约",
        },
        req_key="lnk-01",
    )

    # Complete N0 8 stages
    n0_stages = [
        ("N0-R1", "read_info", "R1", "agent-coord", [], ["res-cfg-v1"], [], "读取共享算子契约 fa2_task_spec@v1 与上游节点瓶颈证据", None),
        ("N0-P1", "plan_idea", "P1", "agent-coord", ["N0-R1"], ["res-cfg-v1"], [], "完成当前分支改动方案设计与硬件容量校核", None),
        ("N0-G1", "gen_kernel", "G1", "agent-coord", ["N0-P1"], [], ["res-n0-kernel-v1"], "生成 TileLang NPUIR 算子内核源码 @v1", "v1"),
        ("N0-C1", "compile", "C1", "agent-eval", ["N0-G1"], ["res-n0-kernel-v1"], [], "tilelangir-opt + bishengir-compile 编译通过 (0 errors)", "v1"),
        ("N0-V1", "verify_correctness", "V1", "agent-eval", ["N0-C1"], ["res-n0-kernel-v1"], [], "对标 FP32 Golden 精度校验通过 (max_abs_diff <= 1.4e-4)", "v1"),
        ("N0-B1", "bench_perf", "B1", "agent-eval", ["N0-V1"], ["res-n0-kernel-v1"], ["res-n0-exp-v1"], "预热 20 次 + 测量 100 次 + npu.synchronize 计时完成", "v1"),
        ("N0-F1", "profiling", "F1", "agent-eval", ["N0-B1"], ["res-n0-exp-v1"], ["res-n0-prof-v1"], "msprof 采集 Cube / Vector / MTE2 / PIPE_FIX 流水线占比完成", "v1"),
        ("N0-S1", "summarize", "S1", "agent-coord", ["N0-F1"], ["res-n0-prof-v1"], [], "完成阶段总结与下一步分支建议记录", None),
    ]
    for idx, (aid, st, code, ag, deps, in_r, out_r, summ, kv) in enumerate(n0_stages):
        await _run_single_attempt(
            seed_client,
            exp_id,
            "N0",
            aid,
            st,
            code,
            1,
            ag,
            f"10:0{idx}:10",
            f"10:0{idx + 1}:10",
            deps,
            summ,
            kernel_ver=kv,
            in_res=in_r,
            out_res=out_r,
            correctness_passed=True if st == "verify_correctness" else None,
            latency_us=248.6 if st == "bench_perf" else None,
            speedup=1.0 if st == "bench_perf" else None,
            pipeline_metrics=(
                {
                    "cubePct": 34,
                    "vecPct": 29,
                    "mte2Pct": 68,
                    "bottleneck": "MTE2 搬运受限 (68%) + 单缓冲等待 (44%)",
                }
                if st == "profiling"
                else None
            ),
            node_exec_status="completed" if st == "summarize" else None,
            node_content_status="cooled" if st == "summarize" else None,
        )

    # 3. Parallel Phase 1: Agent-Cube (N1 + N2) and Agent-Vec (N3) write concurrently!
    async def simulate_agent_cube_phase1() -> None:
        # Branch N1
        await cube_client.create_node(
            exp_id,
            {
                "id": "N1",
                "parentId": "N0",
                "branchLabel": "Branch A · Cube Tiling",
                "title": "N1 · L1/L0C 128x128 分块与 Q_l1 驻留",
                "hypothesis": "将分块从 64x64 提升至 128x128 并将 Q_l1 提出内层循环，可显著降低 MTE2 重复搬运开销。",
                "changeSummary": "block_M=128, block_N=128；Q_l1 在 ko 循环外一次加载常驻 L1 (256KB)。",
                "derivationNote": "从 N0 派生：针对 N0 msprof 中 MTE2 占比 68% 的首要瓶颈做分块扩容。",
                "agentIds": ["agent-cube", "agent-eval"],
                "contentStatus": "hot",
                "derivationReference": {
                    "id": "edge-der-n0-n1",
                    "referencedNodeId": "N0",
                    "referencedKnowledgeId": "K-01",
                    "referencedKnowledgeVersion": "cooled@v1.2",
                    "referencedResourceVersionId": "res-n0-prof-v1",
                    "title": "派生依据：消除 N0 MTE2 68% 重复搬运瓶颈",
                    "rationale": "依据 N0 的 msprof_n0_summary@v1（MTE2 占比 68%）及基础知识 K-01@v1.2（Ascend 910B L1 512KB 容量规则），将分块从 64x64 提升至 128x128 并把 Q_l1 提出内层循环。",
                },
            },
            req_key="create-N1",
        )
        await cube_client.link_resource(
            exp_id,
            {
                "id": "lnk-05",
                "nodeId": "N1",
                "stageType": "read_info",
                "attemptId": "N1-R1",
                "resourceVersionId": "res-cfg-v1",
                "role": "shared_baseline",
                "sourceNodeId": "N0",
                "isSharedReference": True,
                "note": "共享引用 N0 评测契约 @v1",
            },
            req_key="lnk-05",
        )
        for rid in ["res-n1-idea-v1", "res-n1-kernel-v1", "res-n1-exp-v1", "res-n1-prof-v1"]:
            await cube_client.upload_resource(exp_id, res_by_id[rid], req_key=f"up-{rid}")

        n1_stages = [
            ("N1-R1", "read_info", "R1", "agent-cube", [], ["res-cfg-v1", "res-n0-prof-v1"], [], "读取共享算子契约 fa2_task_spec@v1 与上游节点瓶颈证据", None),
            ("N1-P1", "plan_idea", "P1", "agent-cube", ["N1-R1"], ["res-cfg-v1"], ["res-n1-idea-v1"], "完成当前分支改动方案设计与硬件容量校核", None),
            ("N1-G1", "gen_kernel", "G1", "agent-cube", ["N1-P1"], ["res-n1-idea-v1"], ["res-n1-kernel-v1"], "生成 TileLang NPUIR 算子内核源码 @v1", "v1"),
            ("N1-C1", "compile", "C1", "agent-eval", ["N1-G1"], ["res-n1-kernel-v1"], [], "tilelangir-opt + bishengir-compile 编译通过 (0 errors)", "v1"),
            ("N1-V1", "verify_correctness", "V1", "agent-eval", ["N1-C1"], ["res-n1-kernel-v1"], [], "对标 FP32 Golden 精度校验通过 (max_abs_diff <= 1.4e-4)", "v1"),
            ("N1-B1", "bench_perf", "B1", "agent-eval", ["N1-V1"], ["res-n1-kernel-v1"], ["res-n1-exp-v1"], "预热 20 次 + 测量 100 次 + npu.synchronize 计时完成", "v1"),
            ("N1-F1", "profiling", "F1", "agent-eval", ["N1-B1"], ["res-n1-exp-v1"], ["res-n1-prof-v1"], "msprof 采集 Cube / Vector / MTE2 / PIPE_FIX 流水线占比完成", "v1"),
            ("N1-S1", "summarize", "S1", "agent-cube", ["N1-F1"], ["res-n1-prof-v1"], [], "完成阶段总结与下一步分支建议记录", None),
        ]
        for idx, (aid, st, code, ag, deps, in_r, out_r, summ, kv) in enumerate(n1_stages):
            await _run_single_attempt(
                cube_client,
                exp_id,
                "N1",
                aid,
                st,
                code,
                1,
                ag,
                f"10:{5 + idx:02d}:10",
                f"10:{6 + idx:02d}:10",
                deps,
                summ,
                kernel_ver=kv,
                in_res=in_r,
                out_res=out_r,
                correctness_passed=True if st == "verify_correctness" else None,
                latency_us=176.2 if st == "bench_perf" else None,
                speedup=1.41 if st == "bench_perf" else None,
                pipeline_metrics=(
                    {
                        "cubePct": 52,
                        "vecPct": 38,
                        "mte2Pct": 34,
                        "bottleneck": "PIPE_FIX 单缓冲串行等待 (36%)",
                    }
                    if st == "profiling"
                    else None
                ),
                node_exec_status="completed" if st == "summarize" else None,
                delay_sec=delay_sec,
            )

        # Branch N2 (Radical 256x128 -> Compile C1/C2 fail -> Stopped Counterexample)
        await cube_client.create_node(
            exp_id,
            {
                "id": "N2",
                "parentId": "N0",
                "branchLabel": "Branch B · 极限分块 (已停止)",
                "title": "N2 · 256x128 激进分块 (UB 溢出早停)",
                "hypothesis": "尝试将 block_M 扩至 256，最大化单核 Cube 吞吐。",
                "changeSummary": "申请 (256, 128) FP32 S_ub + P_ub + O_ub，累计请求 320KB UB 空间。",
                "derivationNote": "从 N0 派生：探索分块上界，验证 256x128 是否在 910B2 硬件容量范围内。",
                "agentIds": ["agent-cube", "agent-eval"],
                "contentStatus": "hot",
                "derivationReference": {
                    "id": "edge-der-n0-n2",
                    "referencedNodeId": "N0",
                    "referencedResourceVersionId": "res-n0-prof-v1",
                    "title": "派生依据：探索 256x128 极限分块（反例分支）",
                    "rationale": "尝试一步到位将 block_M 扩至 256，检验单核 UB 是否能容纳全部中间累加器。后在 N2-C1/C2 中证实超出 192KB UB 上限。",
                    "reverifiedStatus": "failed_reverify",
                },
            },
            req_key="create-N2",
        )
        for rid in ["res-n2-kernel-v1", "res-n2-summary-v1"]:
            await cube_client.upload_resource(exp_id, res_by_id[rid], req_key=f"up-{rid}")

        await _run_single_attempt(cube_client, exp_id, "N2", "N2-R1", "read_info", "R1", 1, "agent-cube", "10:06:00", "10:06:20", [], "读取 N0 基线配置与 L1/UB 参数", in_res=["res-cfg-v1"], delay_sec=delay_sec)
        await _run_single_attempt(cube_client, exp_id, "N2", "N2-P1", "plan_idea", "P1", 1, "agent-cube", "10:06:20", "10:07:00", ["N2-R1"], "设计 256x128 极限分块方案", delay_sec=delay_sec)
        await _run_single_attempt(cube_client, exp_id, "N2", "N2-G1", "gen_kernel", "G1", 1, "agent-cube", "10:07:00", "10:07:30", ["N2-P1"], "生成 256x128 初版内核 (请求 320KB UB)", kernel_ver="v1", out_res=["res-n2-kernel-v1"], delay_sec=delay_sec)
        await _run_single_attempt(cube_client, exp_id, "N2", "N2-C1", "compile", "C1", 1, "agent-eval", "10:07:30", "10:08:15", ["N2-G1"], "编译失败：UB allocation 327,680B > 196,608B (192KB)", status="failed", kernel_ver="v1", in_res=["res-n2-kernel-v1"], fail_reason="超出 Ascend 910B2 单核 UB 192KB 物理上限", delay_sec=delay_sec)
        await _run_single_attempt(cube_client, exp_id, "N2", "N2-G2", "gen_kernel", "G2", 2, "agent-cube", "10:08:20", "10:09:00", ["N2-C1"], "回退尝试复用 P_ub 压缩缓冲，仍需 224KB UB", kernel_ver="v2", out_res=["res-n2-kernel-v1"], delay_sec=delay_sec)
        await _run_single_attempt(cube_client, exp_id, "N2", "N2-C2", "compile", "C2", 2, "agent-eval", "10:09:05", "10:09:50", ["N2-G2"], "编译仍失败：224KB > 192KB，证明 256x128 在 FP32 累加下不可行", status="failed", kernel_ver="v2", in_res=["res-n2-kernel-v1"], fail_reason="硬件容量硬约束限制，协调者裁定停止该分支", delay_sec=delay_sec)
        await _run_single_attempt(
            cube_client,
            exp_id,
            "N2",
            "N2-S1",
            "summarize",
            "S1",
            1,
            "agent-coord",
            "10:10:10",
            "10:11:40",
            ["N2-C2"],
            "归档 256x128 UB 溢出反例证据，标记为 ready-for-cool 反例记录",
            in_res=["res-n2-kernel-v1"],
            out_res=["res-n2-summary-v1"],
            node_exec_status="stopped",
            node_content_status="ready-for-cool",
            node_stop_reason="单分支停止：连续 2 轮编译 Attempt (C1/C2) 触发 192KB UB 容量硬上限，作为反例保留",
            missing_reasons={
                "verify_correctness": "上游编译 Stage (C1/C2) 失败，未生成可执行算子二进制，跳过精度验证",
                "bench_perf": "未通过编译与正确性验证，禁止进入性能测量与最佳内核排名",
                "profiling": "分支在编译阶段终止，未采集 msprof 数据",
            },
            delay_sec=delay_sec,
        )

    async def simulate_agent_vec_phase1() -> None:
        # Branch N3 (64x64 Ping-Pong sync: G1->C1->V1 fail -> G2->C2->V2 pass -> K-03)
        await vec_client.create_node(
            exp_id,
            {
                "id": "N3",
                "parentId": "N0",
                "branchLabel": "Branch C · 同步机制探索",
                "title": "N3 · 64x64 Ping-Pong 双缓冲同步配对验证",
                "hypothesis": "保持 64x64 分块不变，单独验证 Cube/Vector 跨核 Ping-Pong 双缓冲同步协议，消除 PIPE_FIX 串行等待。",
                "changeSummary": "在 G1->V1 发现缺少反向 Vector->Cube 等待导致覆写错误，回退 G2 补齐双向 event_id=ko%2 后验证通过。",
                "derivationNote": "从 N0 派生：正交探索跨核流水同步机制，其总结产物被 N4 跨分支引用。",
                "primaryKernelVersionId": "res-n0-kernel-v1",
                "agentIds": ["agent-vec", "agent-eval"],
                "contentStatus": "hot",
                "derivationReference": {
                    "id": "edge-der-n0-n3",
                    "referencedNodeId": "N0",
                    "referencedKnowledgeId": "K-02",
                    "referencedKnowledgeVersion": "cooled@v1.0",
                    "referencedResourceVersionId": "res-n0-prof-v1",
                    "title": "派生依据：独立验证 Cube/Vector Ping-Pong 双缓冲同步协议",
                    "rationale": "针对 N0 中 44% 的 PIPE_FIX 同步阻塞，保持 64x64 分块不变，单独探索 sync_block_set/wait 的双缓冲 event_id 配对机制。",
                },
            },
            req_key="create-N3",
        )
        await vec_client.upload_resource(
            exp_id, res_by_id["res-n3-summary-v1"], req_key="up-res-n3-summary-v1"
        )

        await _run_single_attempt(vec_client, exp_id, "N3", "N3-R1", "read_info", "R1", 1, "agent-vec", "10:10:10", "10:10:30", [], "读取共享算子契约 fa2_task_spec@v1 与上游节点瓶颈证据", in_res=["res-cfg-v1"], delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N3", "N3-P1", "plan_idea", "P1", 1, "agent-vec", "10:10:30", "10:10:55", ["N3-R1"], "完成当前分支改动方案设计与硬件容量校核", in_res=["res-cfg-v1"], delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N3", "N3-G1", "gen_kernel", "G1", 1, "agent-vec", "10:11:00", "10:11:45", ["N3-P1"], "生成初版单向 Ping-Pong 同步内核 @v1", kernel_ver="v1", delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N3", "N3-C1", "compile", "C1", 1, "agent-eval", "10:11:50", "10:12:20", ["N3-G1"], "编译 @v1 通过", kernel_ver="v1", delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N3", "N3-V1", "verify_correctness", "V1", 1, "agent-eval", "10:12:25", "10:13:05", ["N3-C1"], "精度验证失败：max_abs_diff = 0.842 > atol 1e-3", status="failed", kernel_ver="v1", fail_reason="ko>=2 时 Cube 提前覆写了 Vector 尚未消费完的 S_ub[0]", correctness_passed=False, delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N3", "N3-G2", "gen_kernel", "G2", 2, "agent-vec", "10:13:10", "10:13:50", ["N3-V1"], "回退修复：补齐 ko>=2 时 Vector->Cube 反向 sync_block_wait 配对 (@v2)", kernel_ver="v2", delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N3", "N3-C2", "compile", "C2", 2, "agent-eval", "10:13:55", "10:14:25", ["N3-G2"], "编译修复版 @v2 通过", kernel_ver="v2", delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N3", "N3-V2", "verify_correctness", "V2", 2, "agent-eval", "10:14:30", "10:15:00", ["N3-C2"], "精度验证通过：双向 event_id=ko%2 修复后 max_abs_diff = 1.3e-4", kernel_ver="v2", correctness_passed=True, delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N3", "N3-B1", "bench_perf", "B1", 1, "agent-eval", "10:15:10", "10:15:30", ["N3-V2"], "预热 20 次 + 测量 100 次 + npu.synchronize 计时完成", kernel_ver="v2", latency_us=192.4, speedup=1.29, delay_sec=delay_sec)
        await _run_single_attempt(
            vec_client,
            exp_id,
            "N3",
            "N3-F1",
            "profiling",
            "F1",
            1,
            "agent-eval",
            "10:15:35",
            "10:15:55",
            ["N3-B1"],
            "msprof 采集 Cube / Vector / MTE2 / PIPE_FIX 流水线占比完成",
            kernel_ver="v2",
            pipeline_metrics={
                "cubePct": 48,
                "vecPct": 44,
                "mte2Pct": 54,
                "bottleneck": "同步气泡消除 (11%)，但 64x64 分块受限于 MTE2 (54%)",
            },
            delay_sec=delay_sec,
        )
        await _run_single_attempt(
            vec_client,
            exp_id,
            "N3",
            "N3-S1",
            "summarize",
            "S1",
            1,
            "agent-vec",
            "10:16:00",
            "10:16:30",
            ["N3-F1"],
            "完成阶段总结与下一步分支建议记录",
            out_res=["res-n3-summary-v1"],
            node_exec_status="completed",
            node_content_status="ready-for-cool",
            delay_sec=delay_sec,
        )

    # Run Agent-Cube and Agent-Vec Phase 1 in parallel!
    await asyncio.gather(simulate_agent_cube_phase1(), simulate_agent_vec_phase1())

    # 4. Phase 2: Multi-Agent Crossover on N4 (Agent-Cube + Agent-Vec + Agent-Eval) & Parallel N5
    for rid in [
        "res-n4-idea-v1",
        "res-n4-kernel-v1",
        "res-n4-kernel-v2",
        "res-n4-exp-v2",
        "res-n4-prof-v1",
        "res-n4-summary-v1",
    ]:
        await vec_client.upload_resource(exp_id, res_by_id[rid], req_key=f"up-{rid}")

    await cube_client.create_node(
        exp_id,
        {
            "id": "N4",
            "parentId": "N1",
            "branchLabel": "Branch A1 · 跨分支融合 (当前最佳)",
            "title": "N4 · 128x128 分块 + 跨分支双缓冲流水融合",
            "hypothesis": "将父节点 N1 的 128x128 L1 驻留分块与分支 N3 验证通过的双缓冲同步配对融合，同时解决 MTE2 带宽与 PIPE_FIX 串行等待两大瓶颈。",
            "changeSummary": "在 N1 基础上引入 (2, 128, 128) Ping-Pong 缓冲与双向 event_id=ko%2 同步；G2 修复了 UB 辅助张量 32B 对齐问题。",
            "derivationNote": "主父节点 N1（继承 128x128 分块）+ 跨分支引用 N3（复用 K-03@v2 双向同步配对经验），在 N4 下重新验证通过。",
            "agentIds": ["agent-cube"],
            "contentStatus": "hot",
            "derivationReference": {
                "id": "edge-der-n1-n4",
                "referencedNodeId": "N1",
                "referencedResourceVersionId": "res-n1-prof-v1",
                "title": "主父节点派生：在 N1 (128x128 分块) 基础上消除 36% PIPE_FIX 气泡",
                "rationale": "N1 解决了 MTE2 带宽瓶颈（耗时降至 176.2 us），但 msprof_n1_summary@v1 显示单缓冲同步仍占 36%，因此从 N1 派生 N4 引入双缓冲流水。",
            },
        },
        req_key="create-N4",
    )

    # Register Cross-Branch Reference N3 -> N4 (initially pending_reverify until N4-V2 completes!)
    await vec_client.create_reference(
        exp_id,
        {
            "id": "edge-xref-n3-n4",
            "sourceNodeId": "N3",
            "targetNodeId": "N4",
            "purpose": "cross_branch",
            "referencedNodeId": "N3",
            "referencedKnowledgeId": "K-03",
            "referencedKnowledgeVersion": "hot@v2",
            "referencedResourceVersionId": "res-n3-summary-v1",
            "title": "跨分支参考：复用 N3 验证通过的双向 event_id=ko%2 配对模式",
            "rationale": "直接吸取分支 N3 在 G1->V1 踩坑（单向等待导致覆写脏数据）后在 G2->V2 修复的双缓冲反向释放协议，避免在 N4 重复踩坑；融合后已在 N4-V2/B2 中完整重验证通过。",
        },
        req_key="xref-n3-n4",
    )

    # Shared baseline link on N4
    await cube_client.link_resource(
        exp_id,
        {
            "id": "lnk-16",
            "nodeId": "N4",
            "stageType": "read_info",
            "attemptId": "N4-R1",
            "resourceVersionId": "res-cfg-v1",
            "role": "shared_baseline",
            "sourceNodeId": "N0",
            "isSharedReference": True,
            "note": "共享引用 N0 基准契约 @v1",
        },
        req_key="lnk-16",
    )

    # Execute N4 rollback & retry chain across Agent-Cube, Agent-Vec, and Agent-Eval
    await _run_single_attempt(
        cube_client,
        exp_id,
        "N4",
        "N4-R1",
        "read_info",
        "R1",
        1,
        "agent-cube",
        "10:17:00",
        "10:17:35",
        [],
        "读取父节点 N1 剖析记录 (prof_n1@v1) 与跨分支 N3 同步总结 (summary_n3@v1)",
        in_res=["res-cfg-v1", "res-n1-prof-v1", "res-n3-summary-v1"],
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-P1",
        "plan_idea",
        "P1",
        1,
        "agent-vec",
        "10:17:40",
        "10:18:20",
        ["N4-R1"],
        "制定 128x128 + Ping-Pong 双缓冲融合方案 (L1 占用 384KB, UB 占用 168KB)",
        in_res=["res-n1-prof-v1", "res-n3-summary-v1"],
        out_res=["res-n4-idea-v1"],
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-G1",
        "gen_kernel",
        "G1",
        1,
        "agent-vec",
        "10:18:25",
        "10:19:15",
        ["N4-P1"],
        "产出初版融合内核 kernel_n4_pingpong@v1.py (row_max_ub 尾维为 1)",
        kernel_ver="v1",
        in_res=["res-n4-idea-v1"],
        out_res=["res-n4-kernel-v1"],
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-C1",
        "compile",
        "C1",
        1,
        "agent-eval",
        "10:19:20",
        "10:19:50",
        ["N4-G1"],
        "编译 @v1 失败：row_max_ub (2, 128, 1) fp32 违反 UB 32B 对齐要求",
        status="failed",
        kernel_ver="v1",
        in_res=["res-n4-kernel-v1"],
        fail_reason="Ascend Vector 指令要求 UB 连续维至少 32 Bytes 对齐",
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-C2",
        "compile",
        "C2",
        1,
        "agent-eval",
        "10:19:55",
        "10:20:25",
        ["N4-G1"],
        "启用 --auto-pad-ub 编译选项重试 @v1 仍失败：需源码显式对齐尾维至 8",
        status="failed",
        kernel_ver="v1",
        in_res=["res-n4-kernel-v1"],
        fail_reason="编译器 Pass 不支持跨核共享 UB 张量的隐式尾维 Padding，触发回退至 G2",
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-V1",
        "verify_correctness",
        "V1",
        1,
        "agent-eval",
        "10:20:25",
        "10:20:26",
        ["N4-C2"],
        "因上游 C1/C2 (@v1) 编译失败跳过 V1，回退至 G2 生成 @v2",
        status="skipped",
        kernel_ver="v1",
        in_res=["res-n4-kernel-v1"],
        fail_reason="上游 C1/C2 未产出二进制，触发 G2 回退修复",
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-G2",
        "gen_kernel",
        "G2",
        2,
        "agent-vec",
        "10:20:30",
        "10:21:05",
        ["N4-C1", "N4-C2"],
        "从 C1/C2 回退修复：将 row_max_ub 尾维对齐至 8 (32B)，产出 kernel_n4_pingpong@v2.py",
        kernel_ver="v2",
        in_res=["res-n4-idea-v1", "res-n4-kernel-v1"],
        out_res=["res-n4-kernel-v2"],
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-C3",
        "compile",
        "C3",
        2,
        "agent-eval",
        "10:21:10",
        "10:22:00",
        ["N4-G2"],
        "编译 @v2 (依赖 G2) 成功生成算子二进制，0 warnings",
        kernel_ver="v2",
        in_res=["res-n4-kernel-v2"],
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-V2",
        "verify_correctness",
        "V2",
        2,
        "agent-eval",
        "10:22:05",
        "10:23:10",
        ["N4-C3"],
        "依赖 G2 → C3 (kernel @v2) 跨分支改动重验证通过：max_abs_diff = 1.1e-4 <= 1e-3",
        kernel_ver="v2",
        in_res=["res-n4-kernel-v2", "res-cfg-v1"],
        correctness_passed=True,
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-B1",
        "bench_perf",
        "B1",
        2,
        "agent-eval",
        "10:23:15",
        "10:24:20",
        ["N4-V2"],
        "首轮计时 134.1 us（检测到同机邻核轻微抖动，发起复测 B2）",
        kernel_ver="v2",
        in_res=["res-n4-kernel-v2"],
        latency_us=134.1,
        speedup=1.85,
        delay_sec=delay_sec,
    )
    await _run_single_attempt(
        vec_client,
        exp_id,
        "N4",
        "N4-B2",
        "bench_perf",
        "B2",
        2,
        "agent-eval",
        "10:24:30",
        "10:25:40",
        ["N4-C3", "N4-V2"],
        "代码未变直接复用 C3/V2 验证结果重测：中位耗时 132.4 us (1.88x vs N0)",
        kernel_ver="v2",
        reused_from="N4-V2",
        in_res=["res-n4-kernel-v2"],
        out_res=["res-n4-exp-v2"],
        latency_us=132.4,
        speedup=1.88,
        delay_sec=delay_sec,
    )

    # Parallel Finalization: Agent-Coord summarizes N4 & K-03 while Agent-Vec launches N5 in-flight!
    async def finalize_n4_by_coord() -> None:
        await _run_single_attempt(
            coord_client,
            exp_id,
            "N4",
            "N4-F1",
            "profiling",
            "F1",
            2,
            "agent-eval",
            "10:25:45",
            "10:27:10",
            ["N4-B2"],
            "msprof 确认 Cube 利用率升至 74%，PIPE_FIX 同步气泡从 36% 降至 9%",
            kernel_ver="v2",
            in_res=["res-n4-exp-v2"],
            out_res=["res-n4-prof-v1"],
            pipeline_metrics={
                "cubePct": 74,
                "vecPct": 61,
                "mte2Pct": 28,
                "bottleneck": "计算密集均衡态 (Cube 74% · PIPE_FIX 等待仅 9%)",
            },
            delay_sec=delay_sec,
        )
        await _run_single_attempt(
            coord_client,
            exp_id,
            "N4",
            "N4-S1",
            "summarize",
            "S1",
            2,
            "agent-coord",
            "10:27:15",
            "10:28:30",
            ["N4-F1"],
            "整理 N4 实测证据链与未证实假设，标记为 ready-for-cool 候选",
            in_res=["res-n4-exp-v2", "res-n4-prof-v1"],
            out_res=["res-n4-summary-v1"],
            node_exec_status="completed",
            node_content_status="ready-for-cool",
            delay_sec=delay_sec,
        )
        await coord_client.upsert_knowledge(exp_id, know_by_id["K-03"], req_key="know-K03")

    async def launch_n5_by_vec() -> None:
        await vec_client.create_node(
            exp_id,
            {
                "id": "N5",
                "parentId": "N4",
                "branchLabel": "Branch A1-1 · Vector 近似探索 (进行中)",
                "title": "N5 · Vector vexp 二阶泰勒展开近似 (验证中)",
                "hypothesis": "在 N4 (132.4 us) 基础上尝试用 vmul/vadd 二阶多项式替代部分 vexp 指令，试探能否进一步压缩 Vector 占比。",
                "changeSummary": "将内层 Softmax 指数计算替换为分段线性+二阶近似，当前正在运行 Stage 5 正确性验证。",
                "derivationNote": "从当前最佳节点 N4 派生：探索 Vector 侧算术指令近似的精度边界。",
                "primaryKernelVersionId": "res-n4-kernel-v2",
                "agentIds": ["agent-vec", "agent-eval"],
                "executionStatus": "running",
                "contentStatus": "hot",
                "stopOrFailReason": "尚未通过正确性验证 (V1 正在运行)，不进入最佳有效 kernel 排名",
                "missingStageReasons": {
                    "bench_perf": "等待 Stage 5 (正确性验证 V1) 完成；未生成性能记录",
                    "profiling": "尚未执行到 profiling 阶段；未生成 msprof 归档",
                    "summarize": "等待实验阶段收尾后再生成节点总结",
                },
                "derivationReference": {
                    "id": "edge-der-n4-n5",
                    "referencedNodeId": "N4",
                    "referencedResourceVersionId": "res-n4-prof-v1",
                    "title": "派生依据：在 N4 基础上探索 Vector 指数计算多项式近似 (进行中)",
                    "rationale": "N4 的 Cube 利用率已达 74%，尝试进一步压缩 Vector 侧 vexp 开销，当前正在执行正确性验证 V1。",
                    "reverifiedStatus": "pending_reverify",
                },
            },
            req_key="create-N5",
        )
        await _run_single_attempt(vec_client, exp_id, "N5", "N5-R1", "read_info", "R1", 1, "agent-vec", "10:29:10", "10:30:10", [], "读取共享算子契约 fa2_task_spec@v1 与上游节点瓶颈证据", in_res=["res-cfg-v1"], delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N5", "N5-P1", "plan_idea", "P1", 1, "agent-vec", "10:30:10", "10:31:10", ["N5-R1"], "完成当前分支改动方案设计与硬件容量校核", in_res=["res-cfg-v1"], delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N5", "N5-G1", "gen_kernel", "G1", 1, "agent-vec", "10:31:10", "10:32:10", ["N5-P1"], "生成 TileLang NPUIR 算子内核源码 @v1", kernel_ver="v1", out_res=["res-n4-kernel-v2"], delay_sec=delay_sec)
        await _run_single_attempt(vec_client, exp_id, "N5", "N5-C1", "compile", "C1", 1, "agent-eval", "10:32:10", "10:33:10", ["N5-G1"], "tilelangir-opt + bishengir-compile 编译通过 (0 errors)", kernel_ver="v1", in_res=["res-n4-kernel-v2"], delay_sec=delay_sec)
        # N5-V1 stays 'running' (in-flight evaluation reserving 1 budget slot!)
        await _run_single_attempt(
            vec_client,
            exp_id,
            "N5",
            "N5-V1",
            "verify_correctness",
            "V1",
            1,
            "agent-eval",
            "10:33:10",
            None,
            ["N5-C1"],
            "正在执行 FP32 Golden 容差比对 (atol=1e-3, rtol=1e-3)...",
            status="running",
            kernel_ver="v1",
            in_res=["res-cfg-v1"],
            delay_sec=delay_sec,
        )

    await asyncio.gather(finalize_n4_by_coord(), launch_n5_by_vec())

    # Refresh final agent heartbeats to match expected UI display
    for ag in AGENTS_SEED:
        await coord_client.heartbeat(exp_id, ag, req_key=f"final-ag-{ag['id']}")

    return {"explorationId": exp_id, "status": "seeded"}
