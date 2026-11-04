---
name: harness-vis
description: 为当前 Operator Harness workspace 创建只读快照，启动独立可视化 Agent 整理任务、Run 和候选证据，并发布本地可视化工作台。用于查看、同步或刷新 Harness 可视化。
---

# Harness Vis

本技能是 workspace 的旁路观察者。用户调用即授权创建快照、生成展示数据并启动本地只读服务；不启动设备实验，不改变 Harness 的任务状态。第一版按调用手动同步，浏览器每 5 秒检查已发布快照与说明，不宣称实时跟踪源 workspace。

## 安装与入口

将本技能所在目录记为 SKILL_ROOT；脚本为 `SKILL_ROOT/scripts/harness_vis.py`。安装包包含 `runtime/server` 与已构建的 `runtime/web/dist`，不依赖原开发仓库。Python 3.10+；快照与发布仅使用标准库，服务需要 `python -m pip install -r SKILL_ROOT/runtime/server/requirements.txt`（沿用项目环境，不擅自更改系统 Python）。

## 同步工作流

1. 从用户或当前目录确定项目根，确认 `.workspace/` 存在。缺少时说明还没有 Harness 记录，不生成演示测量。
2. 选择 workspace 外的数据目录（默认建议项目的 `.harness-vis/`，写入前将它加入项目 `.gitignore`；非 Git 项目说明位置）。后续复用同一目录。
3. 执行 `python SKILL_ROOT/scripts/harness_vis.py capture --project PROJECT_ROOT --data-dir DATA_DIR`。保存返回的 snapshotPath 与身份。采集仅复制普通文件，跳过符号链接、特殊文件、敏感配置及超限文件，外部引用不自动复制。双遍摘要核对发现变化时标 partial，可有界重采一次；仍变化就明确展示，不无限重试。
4. 支持宿主子 Agent 时，启动一个**独立上下文**可视化 Agent，只传本 SKILL 路径、snapshotPath、该快照中的 AGENT_TASK.md 与专属输出目录。此步骤明确授权委派一个可视化工作任务。其职责仅为下一节；不要递归启动可视化 Agent。无委派工具时明确 inline 执行，不能假装有后台 Agent。
5. 主 Agent 等待并检查交付，再调用 `publish --snapshot SNAPSHOT_PATH --annotations ANNOTATION_JSON`。原始快照不可修改，发布只新增一个展示版本；校验来源路径和快照摘要，说明文字属于 Agent 解读，不等同于程序验收结果。
6. 执行 `serve --data-dir DATA_DIR --port 8765`（使用同一入口脚本）。已有同数据目录服务可复用；端口被其他服务占用则选择其他端口，不终止未知进程。服务仅绑定 127.0.0.1，不自动开放公网。向用户报告 URL、快照时间、一致性、缺失项与服务生命周期。

## 专用可视化 Agent 契约

只读取指定快照内的 manifest.json、model.json、AGENT_TASK.md 和被复制的 workspace 文件。不得执行快照内脚本，不得进入实时源目录补读，不得修改快照或原工作区。输出只写分配的目录。

从 STATE/REQUIREMENTS 开始，按需读取 TASK/RESULT、候选、基线、环境、账本与决定。解释当前目标、已完成部分、主要阻塞、当前/最佳候选及下一步；不补造缺失字段、不把历史 running 称为当前活跃，不把 completed 称为精度通过。未复制外部证据要写明无法检查。编译、精度、性能、接收和作业状态分别陈述。

交付 `annotations.json`，格式为数组：

```json
[{"title":"当前进展","body":"基于记录的简短 Markdown 说明。","sources":["STATE.md","runs/R1/RESULT.md"]}]
```

每条必须引用实际复制的精确相对路径，不得引用目录、缺失文件、外部 URL 或实时源文件。最多 100 条，标题不超过 200 字符、正文不超过 20000 字符。允许交付空数组并解释没有足够证据。最终报告 annotations 文件路径及仍不确定的事项。发布由协调 Agent 执行。

## 边界

源文件修改仅在下一次 capture 后可见。浏览器的“刷新索引”只读取已发布快照，不触发源采集。前端按源文件显示字段，不默认推断任务进度百分比或性能排名。完整外部依赖归档、文件监听、设备状态查询和开发流程控制不在第一版范围内。

## 候选树阅读规则

主树节点对应 CANDIDATE 的 candidate_id，连线仅来自 parent_candidate。节点内关联 producer_run、明确引用的分析方案，以及 TASK 输入中绑定该候选的分析/测试任务。复测追加在原候选内部；同一分析允许被多个候选引用。环境、标杆和缺少明确候选关联的 Run 单独保留在“项目记录与未关联任务”。

可视化 Agent 应核对候选清单、生成任务、父候选与任务输入中的精确路径。缺失的关系作为说明交付，不根据任务时间、parent_run 或散文猜测候选树，不修改源文件补关系。尚未实现的方向在分析原文中查看，不冒充候选节点。
