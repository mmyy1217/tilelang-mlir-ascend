# Harness Vis

将 Operator Harness 的 `.workspace/` 复制为可追溯快照，由程序直接解析并以只读工作台展示，Agent 解读按需生成。第一版支持手动同步、历史快照、沿用原 agent-vis 的探索图、Agent 时间轴、可调分栏与右侧文件库。主树一个节点对应一个候选版本，按 `parent_candidate` 连线；分析、开发、测试及复测收在候选内部，项目级与未关联任务单独查看。增加快照接入、版本选择与证据适配；原探索数据兼容入口为 `/?legacy=1`。

## 快速开始

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r server/requirements.txt
npm --prefix web ci
npm --prefix web run build

# PROJECT 必须已有 .workspace；DATA 必须在该 .workspace 之外
.venv/bin/python skills/harness-vis/scripts/harness_vis.py capture --project /path/to/operator --data-dir /path/to/vis-data
.venv/bin/python skills/harness-vis/scripts/harness_vis.py serve --data-dir /path/to/vis-data --port 8765
```

打开 http://127.0.0.1:8765。页面每 5 秒检查已发布数据。默认手动 capture；启动 watch 后自动追加稳定变化的快照，原快照保留。

## 安装到 Harness 项目

构建前端后执行：

```sh
python3 skills/harness-vis/scripts/harness_vis.py install --project /path/to/harness-project
```

安装到 `.agents/skills/harness-vis/`，包含 Skill、采集/发布工具、服务端与构建后的 Web。刷新宿主技能后调用 `$harness-vis 可视化当前 workspace`。默认 capture → serve 即可使用；仅需解释或总结时，Skill 才引导宿主启动独立可视化 Agent。无需额外模型 API Key，模型调用由已有宿主负责。

独立 Agent 输出带来源路径的 annotations.json，协调者执行：

```sh
python3 skills/harness-vis/scripts/harness_vis.py publish --snapshot /path/to/snapshot --annotations /path/to/annotations.json
```

原始快照不变，说明发布为独立版本。服务依赖须在目标 Python 环境安装，详见安装后的 SKILL.md。已存在的安装不覆盖；第一版不提供自动升级。

## 验证与范围

```sh
.venv/bin/python -m pytest server -q
node --test web/tests/*.test.cjs
npm --prefix web run build
# 可选：创建明确标注的合成夹具，没有任何硬件测量
python3 examples/harness-vis/create_fixture.py /tmp/harness-vis-fixture
```

默认每文件最多 2 MB、每快照最多 50 MB；遗漏保留清单。跳过符号链接与部分私密配置，不复制 workspace 外的引用。采集前后核对文件与摘要，变化标为 partial；核对一致也不是文件系统原子快照。运行中/完成均是历史记录状态，不能替代设备查询或精度验收。详见 [v1 设计](docs/harness-vis/design.md) 与 [Skill](skills/harness-vis/SKILL.md)。

---

以下为保留的 agent-vis 工作台说明。

# 算子探索工作台

以持久探索图记录算子优化方案、实验与证据，Web 和 CLI 使用同一服务。Agent 是外部 session，工作台不启动模型或编译器。默认浅色双视图：方案图与 Agent 轨迹，右侧展示状态机、文件和来源。

## 本地启动

需要 Python 3.10+、Node.js 20+。在仓库根目录执行：

```shell
python3 -m venv .venv
.venv/bin/python -m pip install -r server/requirements.txt
.venv/bin/python -m pip install -e './skills/operator-exploration[yaml]'
npm --prefix web ci
npm --prefix web run build
.venv/bin/python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。Windows 使用 `.venv\Scripts\python.exe`。首次启动自动生成演示项目，其数据不是真实硬件测量。数据默认位于 `data/`，可用 `WORKBENCH_DATA_DIR` 指定持久目录。

服务端使用 `WORKBENCH_AGENT_TOKEN` 和 `WORKBENCH_OPERATOR_TOKEN`；本地演示默认值分别为 `agent-secret-token`、`operator-secret-token`。共享部署前显式设置新令牌，并配置 HTTPS 和入口访问控制；当前读取接口不单独鉴权，不能直接把敏感项目暴露到公网。

## Agent 接入

[可携带的 skill](skills/operator-exploration/SKILL.md) 包含 CLI 和 [使用协议](skills/operator-exploration/references/cli.md)。复制整个目录到 Agent 宿主的 skills 目录；也可直接读取 skill 并运行脚本，无需 MCP。

```shell
export OPWORK_URL=http://127.0.0.1:8000
# OPWORK_TOKEN / OPWORK_OPERATOR_TOKEN 与服务端配置一致
.venv/bin/opwork doctor
.venv/bin/opwork project create --file task.json
.venv/bin/opwork --context .sessions/explorer.json session open --project PROJECT_ID --name explorer
.venv/bin/opwork --context .sessions/explorer.json context
```

项目创建的最小输入：

```json
{"operator":"my_operator","goal":"明确优化目标","input_shapes":["明确 shape 与 dtype"],"environment":"硬件、工具链、代码版本","budget":{"max_evaluations":12,"max_minutes":30}}
```

实际任务还应提供参考实现、精度容差、测量协议、完整环境和基线；原文件将保存为契约资源。CLI 不自动补出未知信息。浏览器新建/导入的本地项目需要先导出快照，再用 CLI `project import` 导入服务端。

## 验证

```shell
.venv/bin/python -m pytest server -q
node --test web/tests/*.test.cjs
npm --prefix web run build
```

CLI 集成测试使用临时 HTTP 服务、子进程和合成数据，不编译或测量真实算子。真实任务需外部 Agent 调用已有编译、验证与硬件测试工具，并通过 CLI 登记证据。

需求见 [requirements.md](docs/requirements.md)，架构见 [design.md](docs/design.md)。

## 模拟测量演示

`python3 examples/harness-vis/create_measurement_demo.py /path/to/new-demo-project` 创建六候选 Matmul 用例，包含分块优化、精度失败、性能退化、双缓冲和复测。随后按快速开始执行 capture / serve；可用 publish 发布项目目录里的 `demo-notes.json`。

全部指标人工生成，不执行设备调用。只有显式 `SYNTHETIC_DEMO.json` 标记及匹配 schema 的模拟测量才会填充演示指标；普通 workspace 不受影响。页面、节点和原始产物保留模拟标签。101 个确定性样本用于计算中位数和 P95，最近一次模拟复测覆盖节点摘要，历史数据仍保留。

## 自动同步 workspace

在另一个终端启动，同一个 DATA 目录下的 serve 会看到新快照：

```sh
.venv/bin/python skills/harness-vis/scripts/harness_vis.py watch --project /path/to/operator --data-dir /path/to/vis-data --interval 2 --settle 2
```

选择“跟随最新”后，页面会自动更新。选择具体快照则固定历史版本。watch 仅在内容变化稳定后复制产物，不启动设备任务或重用旧 Agent 说明。Ctrl-C 停止；可用 `--max-duration 3600` 限制为一小时，或 `--max-captures 10` 限制新增数量。快照是追加存储，不自动删除历史；正常停止及心跳过期会显示在工具栏。

## 正式测量接入

无需 Agent 整理。测试产物遵循 [测量契约](skills/harness-vis/references/measurements.md)，提供 `MEASUREMENTS.json` 索引及带摘要引用的逐 case 报告。页面直接展示精度记录、Median/P95、复测历史及协议可比性。只有所有必选 case 完整通过且与基线协议一致时，才计算等权几何平均加速比；最新失败或不完整结果不会回退到旧成绩。

`python3 examples/harness-vis/create_measurement_contract_demo.py /path/to/new-demo-project` 创建正式格式的模拟夹具，包含三种 shape、失败、不同计时协议和复测。所有数值明确为模拟，不调用设备，也不需要 publish。没有正式索引的旧 workspace 仍可查看候选树与原文。
