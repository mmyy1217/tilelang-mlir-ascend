# Product

## Harness Vis 适配约束

用户明确要求沿用原 agent-vis 界面。保留既有顶部协调条、项目栏、左右可调分栏、探索图 / Agent 双视图、节点卡片和阶段展开、右侧文件库与对比入口。不得另建任务总览页面或替换整套布局。DESIGN.md 与现有 styles.css 是视觉依据，模式为 Operate。

Harness v1 通过数据适配层把快照映射到同一组组件：Run 作为工作节点，阶段条只显示实际记录的任务职责；快照版本选择与刷新放进原工具栏，Agent 说明放在原总结入口。未知精度、性能、预算和执行者明确保留未知。原始探索数据继续使用同一界面，兼容入口为 `/?legacy=1`。

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + TypeScript + Vite + 普通 CSS + React Flow（后端目标为 Python + FastAPI + Uvicorn + SQLite + 本地文件目录 + SSE；第一步交付可分享静态交互原型，后续由后端同源托管）

## Users

算子研发工程师、AI 编译器工程师与多 Agent 优化系统研究者。在开展面向 Ascend NPU 的 TileLang 算子（如 Matmul、FlashAttention 混合 Cube/Vector 算子）性能优化时，需要监控、回溯和审阅多个优化 Agent 并行探索形成的搜索树、节点内 Stage 状态机推进记录、回退修复依赖链以及关联的硬件性能剖析（Profiling）证据。

## Product Purpose

供人和不同优化 Agent 共用的算子知识与实验服务工作台。让每一次算子优化探索过程可追溯、可解释、可比较，帮助工程师快速看清不同分支尝试了什么、因何继续或停止、性能收益与瓶颈证据是什么，并从中整理出证据完备的待冷却候选（`ready-for-cool`），减少重复探索并避免跨环境误用经验。

## Positioning

以「搜索树 → 节点 → 节点内 8 阶段 Stage 状态机（含回退修复与显式上游 Attempt 依赖）」为核心数据结构，在同一份规范化数据上提供**搜索树视图**与**Agent 时间轴视图**的实时双向联动，支持点击父子派生边与跨分支参考边直接追溯所引用的知识版本与实验依据，严格区分客观执行状态与知识冷却状态（`hot` / `ready-for-cool` / `cooled`）。

## Operating Context

工程师在桌面宽屏显示器前进行高信息密度的算子调优诊断、分支对比与团队链接分享：
- 左半屏在整棵搜索树拓扑（父子派生与按需展开的跨分支引用）与多 Agent 并行时间轨迹之间无缝切换；
- 右半屏默认聚焦当前最佳有效节点（或选中节点/Stage/边），直接检视 TileLang kernel 源码、优化思路、阶段总结、实验测量记录（含参考实现、容差、预热/重复次数与计时同步边界）以及 Ascend NPU Profiling 摘要（如 Cube/Vector/MTE2 流水利用率与瓶颈指标）；
- 区分节点独有产物与跨节点共享的不可变版本化资源，显式标记未生成、缺失或失败回退的内容。

## Capabilities and Constraints

- **核心层级**：`Exploration` → `Node` → `Stage`（`读取信息 → 形成优化思路 → 生成 kernel → 编译 → 正确性验证 → 性能测量 → profiling → 获取结果并总结`）→ `StageAttempt`。
- **回退修复与依赖**：编译或验证失败可回退至上游阶段（如生成 kernel v2），下游 StageAttempt 显式绑定上游 Attempt 与资源版本（如 `G2 → C3 → V2`）。
- **双类状态分离**：执行状态（`pending` / `running` / `completed` / `failed` / `skipped` / `stopped`）与内容冷却状态（`hot` / `ready-for-cool` / `cooled`）独立展示，绝不混用。
- **停止与预算规则**：支持目标达成、预算耗尽、用户停止、单分支连续无收益早停及全部分支结束；未通过正确性验证的节点不得进入最佳有效 kernel 排名。
- **第一步范围约束（原型阶段）**：采用固定真实感样例数据（TileLang Ascend FlashAttention / Matmul 多分支调优）完成完整双视图联动、节点内 Stage 状态机展开、右侧文件库及可点击派生/跨分支引用边；界面明确标注演示/原型数据状态，暂不实现人工审批合入与远端冷却接口。

## Evidence on Hand

- 需求基线：`docs/requirements.md`（v0.3）
- 架构与交互设计基线：`docs/design.md`（v0.5）
- 仓库内真实的 TileLang-Ascend 算子特征（Cube `T.gemm` / `load_nd2nz` / `store_fixpipe`、Vector `vexp` / `vmul` / `vadd`、双缓冲流水与 `sync_block_set/wait`、`msprof` 瓶颈分析指标），用于构造真实可信的内置演示数据集（标注为演示数据）。

## Product Principles

1. **证据与可信优先**：严格区分实测事实与未证实假设，缺失文件、未生成产物、失败尝试和不可比条件必须显式呈现，绝不用空白成功态掩盖问题。
2. **高密度专业工具感**：像编译器 IR 检视器与硬件 Profiler 一样紧凑、克制、扫视高效，拒绝浮夸的营销卡片、无意义装饰动效和模糊语义。
3. **单一真相与双向同源**：树状图视图、Agent 轨迹视图与右侧文件库共享同一份规范化状态与选中定位（`node_id` / `stage_id` / `attempt_id` / `edge_id`），切换视图不丢失上下文。
4. **因果与谱系透明**：不仅展示最终最佳 kernel，更通过父子派生边、跨分支参考边和节点内回退链条完整还原“为什么这么改”和“哪条路走不通”。

## Harness 候选树语义

Harness 模式下，主树的节点是候选版本，边是 parent_candidate 记录的派生关系。分析、开发、测试 Run 在候选内部归组，同一候选的复测不产生新树节点。环境、标杆与无法关联候选的任务从独立的项目记录入口查看。原八阶段仅适用于原探索数据，不强套到 Harness。
