---
name: harness-tracing
description: 为 Operator Harness 项目安装检查、按项目开启/关闭、只读查看、手动同步、预览与升级可选的 agent-tracing 白盒 DAG 与时间线视图。用于 $harness-tracing、开启/查看/同步/升级白盒追踪。
---

# Harness Tracing

为单个 Operator Harness 项目管理可选的外部白盒追踪（`agent-trace sync-harness`）。本技能与 `$harness-onboard`、`$harness-status` 同级，随 Harness 受管安装器分发与升级；底层 `agent-trace` CLI 与前端渲染器保持独立发行与独立升级，未启用项目保持零依赖。

## 边界与铁律

1. **不造第二套事实源**：`.workspace/<project>/`（`project.json`、`search/tree.json`、候选与评测证据）是唯一权威事实源。在 Operator Harness 项目中**绝不**让 Agent 手动调用 `agent-trace init/attempt/run/action` 双写事件，所有追踪记录均通过 `sync-harness` 幂等派生。
2. **安装与启用分离**：工作区只安装一次 `agent-trace` CLI，每个项目独立决定是否开启；安装/检查时只运行只读 `agent-trace doctor`，**绝不**默认执行会改写历史 trace 的 `--repair`。
3. **私有存储边界**：所有追踪产物与离线页面固定落在 `.workspace/<project>/tracing`（或其子目录）下，受 `.gitignore` 保护，不污染源码或受管技能目录。
4. **只读查询不触发写操作**：查询状态时只调只读 `searchctl.py tracing-status` / `projectctl.py tracing status`，不隐式触发同步或修复。

## 确定目标项目

按 [项目管理契约](../harness-new-project/references/project-management.md) 确定 `workspace_root`、`project_id` 与当前会话 `owner`：

- 多项目时先只读运行 `projectctl.py --workspace <ws> list`，显式确认目标项目，不按修改时间猜测。
- 涉及写配置（`enable` / `disable`）前核对项目处于 `active` 且 `owner` 一致。

## 支持的操作

### 1. 开启 / 初始化（默认动作，或用户说“开启追踪”）

1. 先只读检查 CLI 与当前项目配置：
   ```bash
   python3 .agents/skills/harness-new-project/scripts/projectctl.py \
     --workspace <ws> --project <project_id> tracing status
   ```
2. **若 `cli_found` 为 `false`**：
   - 先向用户确认安装方式与来源（默认源为 `https://github.com/mmyy1217/harness-agent-tracing.git` 的 `operator-harness` 分支；用户也可指定镜像源、本地已有克隆路径或已有虚拟环境内的 `agent-trace` 路径）：
     - **推荐（可编辑安装，后续支持直接 `agent-trace upgrade` 同步更新）**：
       ```bash
       git clone -b operator-harness https://github.com/mmyy1217/harness-agent-tracing.git ~/.local/share/harness-agent-tracing
       python3 -m pip install -e ~/.local/share/harness-agent-tracing
       ```
     - **或直接 `pip` 安装（不保留源码克隆）**：
       ```bash
       python3 -m pip install "git+https://github.com/mmyy1217/harness-agent-tracing.git@operator-harness"
       ```
   - 经用户确认后执行所选命令（若当前环境需在特定 venv/conda 中安装，先使用用户指定的 Python 解释器），随后运行只读 `agent-trace --version && agent-trace doctor` 验证；若用户已有独立路径，直接记录到 `--command <path>`。
   - **若沙箱无外网、用户暂不安装或安装未完成**：不强行开启自动同步，保持关闭并给出上述命令供用户在宿主机终端执行，装好后随时再调用 `$harness-tracing` 即可。
3. **CLI 就绪后按项目启用**：
   ```bash
   python3 .agents/skills/harness-new-project/scripts/projectctl.py \
     --workspace <ws> --project <project_id> --owner <owner> tracing enable \
     [--command <cli_name_or_path>] [--trace-id harness-<project_id>] \
     [--store-dir .workspace/<project_id>/tracing]
   ```
4. **存量历史首次追平**：若 `.workspace/<project_id>/search/tree.json` 已存在且 `searchctl.py tracing-status` 显示 `state` 为 `lagging`，立即执行一次返回结果中的 `retry_command`（即 `sync-harness`），立刻导入历史并生成离线 `index.html`。
5. 启用后，每当 `searchctl.py apply` 成功提交 `accept` 或 `promote-candidate` 时，Harness 会自动调用 `sync-harness` 刷新视图；同步失败绝不回滚已提交的 Harness 账本。

### 2. 只读查看状态与页面路径（`状态` / `status`）

```bash
python3 .agents/skills/harness-resume/scripts/searchctl.py \
  --workspace <ws> --project <project_id> tracing-status
```

只读汇报：`enabled`、`state`（`disabled` / `synced` / `lagging` / `error`）、`upstream_event_count` / `synced_event_count` / `lag_events`、`promoted_candidate_id`、`viewer_path`（以及若为 `lagging`/`error` 时的 `last_error` 与 `retry_command`）。

### 3. 手动同步 / 失败重试（`同步` / `sync`）

从 `tracing-status` 读取 `retry_command`（或拼接等价 `agent-trace --dir <ws>/.workspace/<project_id>/tracing --trace <trace_id> sync-harness --project-dir <ws>/.workspace/<project_id>`）并执行，追平水位且无需重跑 NPU 作业。

### 4. 打开离线页面 / 实时预览（`预览` / `serve`）

先通过只读的 `searchctl.py tracing-status` 获取该项目实际配置的 `viewer_path`、`store_dir`、`trace_id`，并通过 `projectctl.py tracing status` 获取解析后的可执行路径 `cli_path`（确保使用该项目配置的虚拟环境 CLI 与子目录 store，不硬编码默认路径）：

- **单文件离线页面**（可直接用浏览器打开或发给他人评审）：直接汇报 `tracing-status` 返回的 `viewer_path`（默认位于 `.workspace/<project_id>/tracing/traces/<trace_id>/index.html`）。
- **本地实时预览服务**（边跑 `$harness-automatic` 边看 DAG 与时间线刷新）：
  ```bash
  <cli_path> \
    --dir <ws>/<store_dir> \
    --trace <trace_id> \
    serve --port 8765
  ```
  启动后台常驻预览服务前先向用户说明监听地址与端口；无需常驻时直接给出命令与 `viewer_path` 即可。

### 5. 同步升级（`升级` / `upgrade`）

Skill 挂接层与外部追踪引擎解耦、各自独立升级：

1. **升级 `agent-trace` CLI 与前端视图**：先通过 `projectctl.py tracing status` 获取该项目实际使用的 `cli_path` 并向用户确认更新来源。若为可编辑源码安装，执行 `<cli_path> upgrade`（拉取对应分支最新代码）；若为非源码 `pip` 安装，使用 `<cli_path>` 所属环境的 Python 解释器（从 `<cli_path>` 的 shebang 或同目录环境定位 `<cli_python>`，不直接假设 shell 默认 `python3`）执行 `<cli_python> -m pip install --upgrade "git+https://github.com/mmyy1217/harness-agent-tracing.git@operator-harness"`（或用户指定源）。升级后运行只读 `<cli_path> doctor` 确认健康；若项目已启用追踪，重跑一次 `sync-harness` 即可用新版前端重绘离线页面，不触碰 Harness 账本。
2. **升级 Harness 受管技能（含 `$harness-tracing` 本身）**：在 Harness 仓库根目录使用 `scripts/install_codex_skills.py --workspace <ws> --upgrade-plan` 与 `--upgrade <PLAN_ID>` 执行受管事务升级与回滚（详见 [执行控制契约](../harness-resume/references/execution-control.md) 与 Harness 仓库的 `docs/installation-upgrades.md`）。

### 6. 关闭项目追踪（`关闭` / `disable`）

```bash
python3 .agents/skills/harness-new-project/scripts/projectctl.py \
  --workspace <ws> --project <project_id> --owner <owner> tracing disable
```

关闭后保留已有账本与已生成的历史页面，后续 `accept` / `promote-candidate` 不再调用外部 CLI。
