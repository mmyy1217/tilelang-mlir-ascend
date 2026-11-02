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
node web/tests/projects.test.cjs
npm --prefix web run build
```

CLI 集成测试使用临时 HTTP 服务、子进程和合成数据，不编译或测量真实算子。真实任务需外部 Agent 调用已有编译、验证与硬件测试工具，并通过 CLI 登记证据。

需求见 [requirements.md](docs/requirements.md)，架构见 [design.md](docs/design.md)。
