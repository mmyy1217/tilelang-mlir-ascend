from __future__ import annotations

from typing import Any, Dict, List

EXPLORATION_META: Dict[str, Any] = {
    "id": "exp-fa2-mixcv",
    "title": "Ascend 910B2 FlashAttention-2 MixCV 算子探索图",
    "operatorName": "flash_attention_v2_fwd (Cube + Vector 融合)",
    "targetGoal": "在 B=4, H=32, S=4096, D=128 (fp16) 下通过精度校验 (1e-3) 并将耗时压降至 <= 135 us",
    "inputDomain": "B=4, H=32, Sq=4096, Skv=4096, D=128 · float16 (FP32 L0C/UB 累加)",
    "environment": "Ascend 910B2 (24 AI Cores) · CANN 8.0.RC3 · TileLang-NPUIR",
    "baselineRef": {
        "id": "cooled-fa2-base",
        "version": "cooled@v1.4",
        "latencyUs": 248.6,
        "description": "64x64 分块 + 单缓冲顺序等待基线内核",
    },
    "budget": {
        "usedEvaluations": 1,
        "reservedEvaluations": 0,
        "maxEvaluations": 24,
        "elapsedMinutes": 34,
        "maxMinutes": 60,
        "noGainWindow": 3,
        "minGainPct": 2.0,
    },
    "coordinatorStatus": "running",
    "stopReason": "全局目标已由 N4 (132.4 us <= 135 us) 达成；在途尝试 N5-V1 收尾后将结束整棵树探索",
    "bestValidNodeId": "N0",
    "unfinishedDirections": [
        "N5 正在验证 Vector vexp 多项式近似的精度边界 (V1 running)",
        "L2 Cache Swizzle 跨 Core Group 亲和性调度留待下一轮探索树验证",
    ],
}

AGENTS_SEED: List[Dict[str, Any]] = [
    {
        "id": "agent-coord",
        "name": "Agent-Coord (协调与归纳)",
        "role": "全局预算控制、分支早停裁决、经验提炼与 ready-for-cool 候选整理",
        "status": "active",
        "lastHeartbeat": "10:33:42 (2s ago)",
        "currentNodeId": "N4",
        "currentStage": "summarize",
    },
    {
        "id": "agent-cube",
        "name": "Agent-Cube (矩阵分块探索)",
        "role": "L1 / L0A / L0B / L0C 分块切分与 ND2NZ 搬运优化",
        "status": "idle",
        "lastHeartbeat": "10:33:40 (4s ago)",
        "currentNodeId": "N4",
        "currentStage": "read_info",
    },
    {
        "id": "agent-vec",
        "name": "Agent-Vec (向量与流水优化)",
        "role": "UB 内存对齐、Ping-Pong 双缓冲同步与 Online Softmax 向量流水",
        "status": "active",
        "lastHeartbeat": "10:33:43 (1s ago)",
        "currentNodeId": "N5",
        "currentStage": "gen_kernel",
    },
    {
        "id": "agent-eval",
        "name": "Agent-Eval (编译与真机评测器)",
        "role": "执行 bishengir 编译、Golden 精度比对、百次同步计时与 msprof 剖析",
        "status": "active",
        "lastHeartbeat": "10:33:44 (just now)",
        "currentNodeId": "N5",
        "currentStage": "verify_correctness",
    },
]

RESOURCES_SEED: List[Dict[str, Any]] = [
    {
        "id": "res-cfg-v1",
        "resourceId": "task_input_spec",
        "version": "v1",
        "title": "算子契约与基准评测配置 (Shared)",
        "filename": "fa2_task_spec@v1.yaml",
        "category": "artifact",
        "ownerNodeId": "N0",
        "contentStatus": "cooled",
        "createdAt": "10:00:05",
        "content": """# FlashAttention-2 Ascend 910B2 算子优化契约 (cooled@v1)
operator: flash_attention_v2_fwd
shape:
  B: 4
  H: 32
  S_q: 4096
  S_kv: 4096
  D: 128
dtype: float16
accumulate_dtype: float32
hardware: Ascend 910B2 (24 AI Cores, L1=512KB, L0C=256KB, UB=192KB)
cann_version: 8.0.RC3.alpha002
verification:
  reference: torch_npu.npu_fusion_attention (golden fp32)
  atol: 1e-3
  rtol: 1e-3
benchmark_protocol:
  warmup: 20
  repeat: 100
  timing_boundary: device_event_start_to_end
  sync_method: torch.npu.synchronize()""",
    },
    {
        "id": "res-n0-kernel-v1",
        "resourceId": "kernel_fa2_baseline",
        "version": "v1",
        "title": "基线 TileLang MixCV 内核 (64x64 顺序流水)",
        "filename": "kernel_n0_baseline@v1.py",
        "category": "kernel",
        "ownerNodeId": "N0",
        "contentStatus": "cooled",
        "createdAt": "10:01:12",
        "content": """import tilelang as T

@T.prim_func
def flash_attn_mixcv_baseline(
    Q: T.Tensor((4, 32, 4096, 128), "float16"),
    K: T.Tensor((4, 32, 4096, 128), "float16"),
    V: T.Tensor((4, 32, 4096, 128), "float16"),
    O: T.Tensor((4, 32, 4096, 128), "float16"),
):
    # Cooled Baseline @v1.4: block_M=64, block_N=64, 单缓冲顺序等待
    with T.Kernel(256, is_npuir=True) as (cid, _):
        with T.Scope("Cube"):
            Q_l1 = T.alloc_L1((64, 128), "float16")
            K_l1 = T.alloc_L1((64, 128), "float16")
            S_l0c = T.alloc_L0C((64, 64), "float32")
            for ko in T.serial(64):
                T.load_nd2nz(Q_l1, Q[cid // 64, cid % 32, :64, :128])
                T.load_nd2nz(K_l1, K[cid // 64, cid % 32, ko * 64 : (ko + 1) * 64, :128])
                T.gemm(Q_l1, K_l1, S_l0c, transpose_B=True, init=True)
                T.store_fixpipe(S_ub_shared, S_l0c)
                T.sync_block_set("Cube", "Vector", event_id=0)
                T.sync_block_wait("Vector", "Cube", event_id=0)
        with T.Scope("Vector"):
            S_ub = T.alloc_ub((64, 64), "float32")
            for ko in T.serial(64):
                T.sync_block_wait("Cube", "Vector", event_id=0)
                T.vexp(S_ub, S_ub)
                T.sync_block_set("Vector", "Cube", event_id=0)""",
    },
    {
        "id": "res-n0-exp-v1",
        "resourceId": "exp_n0",
        "version": "v1",
        "title": "根节点基线实测记录 (248.6 us)",
        "filename": "exp_n0_record@v1.json",
        "category": "experiment",
        "ownerNodeId": "N0",
        "contentStatus": "cooled",
        "createdAt": "10:02:30",
        "content": "实验环境: Ascend 910B2 · CANN 8.0.RC3 · 预热 20 次 / 测量 100 次\n精度校验: PASS (max_abs_diff = 1.4e-4 <= atol 1e-3)\n中位耗时: 248.6 us (P95: 251.2 us, Min: 246.9 us, Max: 254.0 us)",
        "experimentData": {
            "hardware": "Ascend 910B2 (24 Cores)",
            "cannVersion": "8.0.RC3.alpha002",
            "referenceImpl": "torch_npu.npu_fusion_attention (fp32 ref)",
            "inputShape": "B=4, H=32, Sq=4096, Skv=4096, D=128",
            "dtype": "float16",
            "atol": "1e-3",
            "rtol": "1e-3",
            "maxAbsDiff": "1.4e-4",
            "correctnessPassed": True,
            "warmupRuns": 20,
            "repeatRuns": 100,
            "timingBoundary": "ACL Event Record around Kernel Launch",
            "syncMethod": "torch.npu.synchronize()",
            "medianUs": 248.6,
            "p95Us": 251.2,
            "minUs": 246.9,
            "maxUs": 254.0,
            "samplesUs": [248.1, 249.0, 248.5, 247.9, 250.2, 248.6],
            "isComparable": True,
        },
    },
    {
        "id": "res-n0-prof-v1",
        "resourceId": "prof_n0",
        "version": "v1",
        "title": "基线 msprof 流水剖析与二进制归档",
        "filename": "msprof_n0_summary@v1.bin",
        "category": "profile",
        "ownerNodeId": "N0",
        "contentStatus": "cooled",
        "createdAt": "10:03:00",
        "sizeBytes": 4829100,
        "isBinary": True,
        "content": "msprof 剖析结论: Cube 利用率仅 34%，MTE2 搬运与 PIPE_FIX 同步等待占比高达 68%。64x64 分块过小导致 L1 复用率低，且单缓冲同步造成 Cube 与 Vector 相互串行阻塞。",
        "profileData": {
            "cubeUtil": 34,
            "vecUtil": 29,
            "mte2Util": 68,
            "pipeFixWaitPct": 44,
            "l1UsageKb": 128,
            "ubUsageKb": 64,
            "bottleneckTag": "MTE2 带宽受限 (68%) + 单缓冲同步气泡 (44%)",
            "rawArchiveName": "PROF_0001_N0_20261031.db",
            "rawArchiveSize": "4.61 MB",
        },
    },
    {
        "id": "res-n1-idea-v1",
        "resourceId": "idea_n1",
        "version": "v1",
        "title": "N1 优化思路：L1/L0C 分块扩容至 128x128 并驻留 Q_l1",
        "filename": "idea_n1_cube_tiling@v1.md",
        "category": "idea",
        "ownerNodeId": "N1",
        "contentStatus": "hot",
        "createdAt": "10:05:10",
        "content": """### 优化依据与改动意图
- **触发瓶颈**：根节点 `N0` 的 `msprof` 显示 MTE2 搬运占比 68%，`Q` 矩阵在内层循环被重复执行 `load_nd2nz`。
- **方案改动**：
  1. 将分块从 `block_M=64, block_N=64` 提升至 `block_M=128, block_N=128`；
  2. 将 `Q_l1` 提出 `ko` 循环外常驻 L1 Buffer（占用 32KB），仅在循环内流式加载 `K_l1` 与 `V_l1`。""",
    },
    {
        "id": "res-n1-kernel-v1",
        "resourceId": "kernel_n1",
        "version": "v1",
        "title": "N1 TileLang 内核 (128x128 L1 驻留 Q)",
        "filename": "kernel_n1_tiling128@v1.py",
        "category": "kernel",
        "ownerNodeId": "N1",
        "contentStatus": "hot",
        "createdAt": "10:06:40",
        "content": """@T.prim_func
def flash_attn_mixcv_n1(
    Q: T.Tensor((4, 32, 4096, 128), "float16"),
    K: T.Tensor((4, 32, 4096, 128), "float16"),
    V: T.Tensor((4, 32, 4096, 128), "float16"),
    O: T.Tensor((4, 32, 4096, 128), "float16"),
):
    # Node N1 @v1: block_M=128, block_N=128, Q_l1 hoisted out of ko loop
    with T.Kernel(128, is_npuir=True) as (cid, _):
        with T.Scope("Cube"):
            Q_l1 = T.alloc_L1((128, 128), "float16")
            K_l1 = T.alloc_L1((128, 128), "float16")
            S_l0c = T.alloc_L0C((128, 128), "float32")
            T.load_nd2nz(Q_l1, Q[cid // 32, cid % 32, :128, :128])
            for ko in T.serial(32):
                T.load_nd2nz(K_l1, K[cid // 32, cid % 32, ko * 128 : (ko + 1) * 128, :128])
                T.gemm(Q_l1, K_l1, S_l0c, transpose_B=True, init=True)
                T.store_fixpipe(S_ub_shared, S_l0c)
                T.sync_block_set("Cube", "Vector", event_id=0)
                T.sync_block_wait("Vector", "Cube", event_id=0)""",
    },
    {
        "id": "res-n1-exp-v1",
        "resourceId": "exp_n1",
        "version": "v1",
        "title": "N1 实测记录 (176.2 us · 提速 29.1%)",
        "filename": "exp_n1_record@v1.json",
        "category": "experiment",
        "ownerNodeId": "N1",
        "contentStatus": "hot",
        "createdAt": "10:09:15",
        "content": "精度验证通过 (max_abs_diff = 1.2e-4)。中位耗时降至 176.2 us (较基线 248.6 us 缩短 72.4 us)。",
        "experimentData": {
            "hardware": "Ascend 910B2 (24 Cores)",
            "cannVersion": "8.0.RC3.alpha002",
            "referenceImpl": "torch_npu.npu_fusion_attention (fp32 ref)",
            "inputShape": "B=4, H=32, Sq=4096, Skv=4096, D=128",
            "dtype": "float16",
            "atol": "1e-3",
            "rtol": "1e-3",
            "maxAbsDiff": "1.2e-4",
            "correctnessPassed": True,
            "warmupRuns": 20,
            "repeatRuns": 100,
            "timingBoundary": "ACL Event Record around Kernel Launch",
            "syncMethod": "torch.npu.synchronize()",
            "medianUs": 176.2,
            "p95Us": 178.5,
            "minUs": 175.0,
            "maxUs": 180.4,
            "samplesUs": [176.0, 176.4, 175.8, 176.2, 177.1],
            "isComparable": True,
        },
    },
    {
        "id": "res-n1-prof-v1",
        "resourceId": "prof_n1",
        "version": "v1",
        "title": "N1 msprof 流水剖析 (Cube 52% · PIPE_FIX 等待仍占 36%)",
        "filename": "msprof_n1_summary@v1.bin",
        "category": "profile",
        "ownerNodeId": "N1",
        "contentStatus": "hot",
        "createdAt": "10:10:05",
        "sizeBytes": 4512000,
        "isBinary": True,
        "content": "MTE2 搬运占比从 68% 降至 34%，Cube 利用率升至 52%；但单缓冲 event_id=0 导致 Cube 写回 UB 后仍需空等 Vector 完成 Softmax，PIPE_FIX 等待占 36%。",
        "profileData": {
            "cubeUtil": 52,
            "vecUtil": 38,
            "mte2Util": 34,
            "pipeFixWaitPct": 36,
            "l1UsageKb": 256,
            "ubUsageKb": 112,
            "bottleneckTag": "Cube/Vector 单缓冲串行等待 (36%)",
            "rawArchiveName": "PROF_0002_N1_20261031.db",
            "rawArchiveSize": "4.30 MB",
        },
    },
    {
        "id": "res-n2-kernel-v1",
        "resourceId": "kernel_n2",
        "version": "v1",
        "title": "N2 激进 256x128 UB 分块内核 (编译失败样本)",
        "filename": "kernel_n2_ub256@v1.py",
        "category": "kernel",
        "ownerNodeId": "N2",
        "contentStatus": "hot",
        "createdAt": "10:07:20",
        "content": """# Node N2 @v1: 尝试 block_M=256, block_N=128 全驻留 UB
S_ub = T.alloc_ub((256, 128), "float32")  # 128KB
P_ub = T.alloc_ub((256, 128), "float16")  # 64KB
O_ub = T.alloc_ub((256, 128), "float32")  # 128KB -> 累计 320KB > 192KB UB 上限""",
    },
    {
        "id": "res-n2-summary-v1",
        "resourceId": "summary_n2",
        "version": "v1",
        "title": "N2 失败诊断与分支早停记录 (反例证据)",
        "filename": "summary_n2_ub_overflow@v1.md",
        "category": "summary",
        "ownerNodeId": "N2",
        "contentStatus": "ready-for-cool",
        "createdAt": "10:11:40",
        "content": """### 分支停止与反例结论 (保留为反例候选)
- **编译错误**：`bishengir-compile: error: UB allocation (327,680 bytes) exceeds hardware limit (196,608 bytes) on Ascend 910B2`.
- **重试情况**：Attempt `C1` 与降维尝试 `G2 -> C2` 均因 FP32 累加器 + Online Softmax 临时缓冲超出 192KB UB 上限失败。
- **协调者裁决**：触发**单分支停止规则**（遇到硬件容量硬限制），标记为反例证据，禁止后续分支将单核 UB 累加块扩至 `256x128`。""",
    },
    {
        "id": "res-n3-summary-v1",
        "resourceId": "summary_n3",
        "version": "v1",
        "title": "N3 跨核同步配对修正总结 (供跨分支引用)",
        "filename": "summary_n3_pipe_sync@v1.md",
        "category": "summary",
        "ownerNodeId": "N3",
        "contentStatus": "ready-for-cool",
        "createdAt": "10:16:00",
        "content": """### N3 节点内回退修复与关键发现 (Knowledge K-03@v2)
- **Attempt G1 → C1 → V1 (失败)**：在 64x64 分块上直接开启 `event_id = ko % 2` 双缓冲，但未在循环尾补齐反向 `sync_block_wait("Vector", "Cube", event_id=ko % 2)`，导致 `ko >= 2` 时 Cube 覆写尚未消费完的 `S_ub[0]`，精度校验 `max_abs_diff = 0.84` 失败。
- **Attempt G2 → C2 → V2 → B1 (修复通过)**：补齐双向配对 `event_id = ko % 2` 后精度通过（耗时 `192.4 us`，因 64x64 分块较小未超越 N1，但**双缓冲同步配对模板**验证完备，已被 `N4` 跨分支引用）。""",
    },
    {
        "id": "res-n4-idea-v1",
        "resourceId": "idea_n4",
        "version": "v1",
        "title": "N4 优化思路：融合 N1 128x128 分块与 N3 双缓冲同步模板",
        "filename": "idea_n4_crossover@v1.md",
        "category": "idea",
        "ownerNodeId": "N4",
        "contentStatus": "ready-for-cool",
        "createdAt": "10:18:00",
        "content": """### 派生与跨分支融合依据
1. **主父节点派生 (`N1 -> N4`)**：继承 `N1` 已验证的 `128x128` L1 驻留分块，解决 MTE2 重复搬运问题。
2. **跨分支引用 (`N3 -> N4`, Ref `R-02`)**：针对 `N1` 暴露的 36% `PIPE_FIX` 同步等待瓶颈，引入 `N3` 在 `summary_n3_pipe_sync@v1.md` 中验证通过的双缓冲 `event_id = ko % 2` 配对范式。
3. **重验证要求**：融合代码在 `N4` 下必须完整重跑编译、正确性验证与百次计时。""",
    },
    {
        "id": "res-n4-kernel-v1",
        "resourceId": "kernel_n4",
        "version": "v1",
        "title": "N4 初版内核 (G1 产出 · 编译 C1 报 UB 32B 对齐错误)",
        "filename": "kernel_n4_pingpong@v1.py",
        "category": "kernel",
        "ownerNodeId": "N4",
        "contentStatus": "hot",
        "createdAt": "10:19:10",
        "content": """# Node N4 @v1 (Generated in StageAttempt G1 - Failed in Compile C1)
# 原因: row_max_ub 分配形状为 (2, 128, 1) float32，尾维 4B 未满足 Ascend Vector 32B 对齐约束
row_max_ub = T.alloc_ub((2, 128, 1), "float32")  # Compile Error in C1: UB stride not 32B aligned""",
    },
    {
        "id": "res-n4-kernel-v2",
        "resourceId": "kernel_n4",
        "version": "v2",
        "title": "N4 修复版内核 (G2 产出 · C3/V2/B1/B2 验证通过 · 132.4 us)",
        "filename": "kernel_n4_pingpong@v2.py",
        "category": "kernel",
        "ownerNodeId": "N4",
        "contentStatus": "ready-for-cool",
        "createdAt": "10:21:05",
        "content": """import tilelang as T

@T.prim_func
def flash_attn_mixcv_n4_v2(
    Q: T.Tensor((4, 32, 4096, 128), "float16"),
    K: T.Tensor((4, 32, 4096, 128), "float16"),
    V: T.Tensor((4, 32, 4096, 128), "float16"),
    O: T.Tensor((4, 32, 4096, 128), "float16"),
):
    # Node N4 @v2 (G2 -> C3 -> V2 -> B1/B2 -> F1 -> S1)
    # 1. 继承 N1: 128x128 L1 常驻 Q_l1
    # 2. 跨分支引用 N3 (K-03@v2): Ping-Pong 双缓冲 event_id = ko % 2 双向配对
    # 3. 阶段回退修复 (G1->C1/C2 fail -> G2->C3->V2 pass): row_max_ub 尾维对齐至 8 (32 Bytes)
    with T.Kernel(128, is_npuir=True) as (cid, _):
        with T.Scope("Cube"):
            Q_l1 = T.alloc_L1((128, 128), "float16")
            K_l1 = T.alloc_L1((2, 128, 128), "float16")
            S_l0c = T.alloc_L0C((128, 128), "float32")
            T.load_nd2nz(Q_l1, Q[cid // 32, cid % 32, :128, :128])
            for ko in T.serial(32):
                buf = ko % 2
                if ko >= 2:
                    T.sync_block_wait("Vector", "Cube", event_id=buf)
                T.load_nd2nz(K_l1[buf], K[cid // 32, cid % 32, ko * 128 : (ko + 1) * 128, :128])
                T.gemm(Q_l1, K_l1[buf], S_l0c, transpose_B=True, init=True)
                T.store_fixpipe(S_ub_pingpong[buf], S_l0c)
                T.sync_block_set("Cube", "Vector", event_id=buf)

        with T.Scope("Vector"):
            S_ub_pingpong = T.alloc_ub((2, 128, 128), "float32")
            row_max_ub = T.alloc_ub((2, 128, 8), "float32")  # 32B aligned
            for ko in T.serial(32):
                buf = ko % 2
                T.sync_block_wait("Cube", "Vector", event_id=buf)
                T.vexp(S_ub_pingpong[buf], S_ub_pingpong[buf])
                T.sync_block_set("Vector", "Cube", event_id=buf)""",
    },
    {
        "id": "res-n4-exp-v2",
        "resourceId": "exp_n4",
        "version": "v2",
        "title": "N4 实测记录 (B2 复测 · 132.4 us · 1.88x 提速)",
        "filename": "exp_n4_record@v2.json",
        "category": "experiment",
        "ownerNodeId": "N4",
        "contentStatus": "ready-for-cool",
        "createdAt": "10:25:40",
        "content": "绑定依赖链: G2 (kernel_n4@v2) -> C3 (编译通过) -> V2 (max_abs_diff=1.1e-4) -> B2 (复测锁定频点 132.4 us)。\n较 cooled 基线 (248.6 us) 提速 1.88x (-46.7%)，较父节点 N1 (176.2 us) 进一步缩短 43.8 us (-24.9%)。",
        "experimentData": {
            "hardware": "Ascend 910B2 (24 Cores)",
            "cannVersion": "8.0.RC3.alpha002",
            "referenceImpl": "torch_npu.npu_fusion_attention (fp32 ref)",
            "inputShape": "B=4, H=32, Sq=4096, Skv=4096, D=128",
            "dtype": "float16",
            "atol": "1e-3",
            "rtol": "1e-3",
            "maxAbsDiff": "1.1e-4",
            "correctnessPassed": True,
            "warmupRuns": 20,
            "repeatRuns": 100,
            "timingBoundary": "ACL Event Record around Kernel Launch",
            "syncMethod": "torch.npu.synchronize()",
            "medianUs": 132.4,
            "p95Us": 133.8,
            "minUs": 131.7,
            "maxUs": 135.2,
            "samplesUs": [132.4, 132.1, 132.8, 132.3, 132.5, 131.9],
            "isComparable": True,
        },
    },
    {
        "id": "res-n4-prof-v1",
        "resourceId": "prof_n4",
        "version": "v1",
        "title": "N4 msprof 流水剖析 (Cube 利用率升至 74% · 气泡降至 9%)",
        "filename": "msprof_n4_summary@v1.bin",
        "category": "profile",
        "ownerNodeId": "N4",
        "contentStatus": "ready-for-cool",
        "createdAt": "10:27:10",
        "sizeBytes": 4680000,
        "isBinary": True,
        "content": "双缓冲 Ping-Pong 彻底掩盖了 Vector Online Softmax 与 Cube GEMM 的串行等待：PIPE_FIX 等待占比从 36% 降至 9%，Cube 利用率达到 74%。",
        "profileData": {
            "cubeUtil": 74,
            "vecUtil": 61,
            "mte2Util": 28,
            "pipeFixWaitPct": 9,
            "l1UsageKb": 384,
            "ubUsageKb": 168,
            "bottleneckTag": "计算密集平衡态 (Cube 74% · 同步等待仅 9%)",
            "rawArchiveName": "PROF_0004_N4_20261031.db",
            "rawArchiveSize": "4.46 MB",
        },
    },
    {
        "id": "res-n4-summary-v1",
        "resourceId": "summary_n4",
        "version": "v1",
        "title": "N4 阶段总结与 ready-for-cool 候选整理",
        "filename": "summary_n4_candidate@v1.md",
        "category": "summary",
        "ownerNodeId": "N4",
        "contentStatus": "ready-for-cool",
        "createdAt": "10:28:30",
        "content": """### 节点 N4 实测结论与因果归因
- **已验证事实 (Verified Fact)**：在 `B=4, H=32, S=4096, D=128 (fp16)` 下，融合 `128x128` L1 常驻分块与双缓冲 `event_id = ko % 2` 跨核同步，中位耗时从 `176.2 us` 降至 `132.4 us`，`PIPE_FIX` 等待从 `36%` 降至 `9%`。
- **回退修复记录**：Attempt `G1 -> C1/C2` 暴露出归约辅助张量尾维 `1 * 4B` 违反 UB 32B 对齐约束；回退至 `G2` 补齐尾维 Padding 至 `8 * 4B = 32B` 后通过 `C3 -> V2 -> B1/B2`。
- **未证实假设 (Retained as Hypothesis)**：当前尾块余数处理耗时下降约 `3.1 us` 是否源于编译器指令重排尚未单独隔离验证，保留为假设，不写入通用定律。""",
    },
]

KNOWLEDGE_SEED: List[Dict[str, Any]] = [
    {
        "id": "K-01",
        "category": "basic_knowledge",
        "claimType": "fact",
        "title": "Ascend 910B2 L1 / UB 硬件容量与 32B 地址对齐硬约束",
        "version": "cooled@v1.2",
        "contentStatus": "cooled",
        "applicability": "Ascend 910B 系列 · CANN 8.0+ · TileLang NPUIR Developer/Expert 模式",
        "unverifiedScope": "A5 架构独立 SIMT 访存通道不在本条约束范围内",
        "body": "单核 L1 Buffer 上限 512KB，Unified Buffer (UB) 上限 192KB；所有 UB 向量指令与 DMA 搬运的起始地址及连续轴 stride 必须为 32 Bytes 的整数倍（FP16 至少 16 元素，FP32 至少 8 元素）。",
        "supportingNodeIds": ["N0", "N1", "N4"],
        "counterexampleNodeIds": ["N2"],
        "evidenceAttemptIds": ["N2-C1", "N4-C1", "N4-C2"],
    },
    {
        "id": "K-03",
        "category": "optimization_experience",
        "claimType": "method",
        "title": "MixCV 双缓冲流水必须成对闭合 Cube→Vector 与 Vector→Cube 的双路 event_id",
        "version": "ready-for-cool@v2",
        "contentStatus": "ready-for-cool",
        "applicability": "包含 T.gemm + T.store_fixpipe + Vector 后处理的 MixCV 算子，且 L1/UB 具备双份 Ping-Pong 容量（如 128x128 FP16/FP32）",
        "unverifiedScope": "3-stage 环形缓冲（Triple Buffering）在 S < 1024 短序列下的收益尚未验证",
        "body": '当 msprof 显示 PIPE_FIX 等待 > 25% 时，将 UB 缓冲扩为 (2, block_M, block_N) 并按 buf = ko % 2 交替：除正向 sync_block_set/wait("Cube", "Vector", buf) 外，必须在 ko >= 2 时于 Cube 写入前执行 sync_block_wait("Vector", "Cube", buf)，否则将发生跨轮次 UB 覆写导致精度崩溃。',
        "supportingNodeIds": ["N3", "N4"],
        "counterexampleNodeIds": ["N2"],
        "evidenceAttemptIds": ["N3-V1", "N3-V2", "N4-V2", "N4-B2", "N4-F1"],
    },
]
