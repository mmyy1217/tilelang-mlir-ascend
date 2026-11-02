# CLI 使用协议

## 安装、连接与身份

从仓库安装 `python -m pip install -e './skills/operator-exploration[yaml]'`，提供 `opwork` 命令。也可直接运行 `python <skill目录>/scripts/opwork.py`，JSON 输入无需第三方依赖；YAML 需要 PyYAML。复制整个 skill 目录到宿主的 skills 目录即可携带脚本；安装 CLI 不会自动更改宿主配置。

服务启动方式见仓库 `README.md`。设置 `OPWORK_URL`、`OPWORK_TOKEN`；暂停、恢复和人工核对未决尝试使用独立的 `OPWORK_OPERATOR_TOKEN`。令牌值应与服务端配置一致，不写入输入文件、session 文件或日志。共享部署须由部署者配置 HTTPS 和访问控制；当前服务是单工作区共享凭据模型，session ID 是归属记录，不是独立安全身份。

全局参数 `--url`、`--context`、`--request-id` **放在子命令之前**。可用 `OPWORK_CONTEXT` 指定 session 文件。每个真实 session 使用不同的文件；新 session 不复用旧身份。

```shell
opwork doctor
opwork project create --file task.json
opwork --context .sessions/explorer.json session open --project PROJECT_ID --name explorer
opwork --context .sessions/explorer.json context
```

`doctor` 返回协议与实际能力。`context` 返回任务、预算、版本、最近 20 个节点摘要、在途尝试、交接和心跳状态；其他节点使用 `node list/show`。资源正文仅按需读取。上下文和请求日志以私有文件权限保存，不含令牌。

## 项目与截面

- `project create --file task.json`：`operator` 必填，支持 `title`、`goal`、`input_shapes`（字符串数组）、`environment`（字符串）、`budget.max_evaluations/max_minutes` 和 `baselineRef`（`id/version/latencyUs/description`）。完整输入作为契约资源保存；数值基线缺失时不显示虚构加速比。
- `project list`：列出服务端项目；浏览器本地项目须先导出，再用 `project import --file snapshot.json` 导入服务端。
- `project export --output snapshot.json`：输出当前绑定项目的完整快照和二进制文件；不覆盖已有文件。
- `project import --file snapshot.json`：原子导入为新项目，校验文件哈希和关系，保留历史证据；在途工作标为历史停止，导入的正确性声明不用于排名，需重新验证。来源记录保留在 `context.metadata`。
- `checkpoint create --name NAME`：获取服务端一致截面，返回 `checkpointId`。
- `project fork --from PROJECT_ID --checkpoint CHECKPOINT_ID --name NAME`：从服务端检查点创建停止状态的新项目，保留原项目已验证记录；不启动 agent、不取消源任务。新 session 绑定新项目后显式恢复。
- `project pause --reason TEXT` / `project resume --expected-version N`：需要 operator 凭据。恢复使用 `context.version`；未决尝试未核对时拒绝恢复。
- `project configure` 暂不支持并返回非零退出码。任务变更可保存新契约资源，但不会静默改写服务端目标与预算。

## 方案、资源与尝试

`node create --file node.json` 接受 `id`（可省略）、`title`、`hypothesis`、`change_summary`、`parent_ids`、`source_contributions`（来源 ID 到采用内容的映射）。`node list` / `node show NODE_ID` 查看。

```json
{"id":"n1","title":"调整分块","hypothesis":"减少重复搬运","parent_ids":["baseline"],"source_contributions":{"baseline":"保留语义与验证方法，改变分块"}}
```

`resource put --node NODE_ID --category kernel --file kernel.py --version v1` 返回不可变 `resourceVersionId`，上传时校验 SHA256；category 可选 `kernel/idea/summary/experiment/profile/artifact`。`--metadata metadata.json` 只接受 `experimentData`、`profileData`，保存实验协议、样本或 profile 摘要。`resource get VERSION_ID --output PATH` 下载并复核哈希，覆盖必须显式 `--overwrite`。

`attempt start --node NODE_ID --stage STAGE --file start.json` 申请许可；成功后才执行外部工具。`attempt finish ATTEMPT_ID --expected-version N --file result.json` 上报终态；`attempt show ATTEMPT_ID` 读取版本和依赖。

```json
{"id":"C1","code":"C1","depends_on":["G1"],"kernel_version_id":"EXACT_RESOURCE_VERSION_ID"}
```

```json
{"status":"completed","output_versions":["LOG_VERSION_ID"],"summary":"实际工具结果"}
```

Stage：`read_info → plan_idea → gen_kernel → compile → verify_correctness → bench_perf → profiling → summarize`。这是依赖约束，不是要求伪造每个环节：编译/验证失败可回到 `gen_kernel` 新 Attempt；确实跳过时以 `skipped` 和 `reason` 留证，但不会自动满足需要成功结果的下游条件。依赖必须属于同一节点；跨节点来源使用图关系，新方案需要独立验证。

`gen_kernel` 开始需 `kernelVersionLabel`，完成需唯一 kernel 输出资源；下游以 `kernel_version_id` 指定精确资源（自动填入标签与输入资源）。验证完成显式 `correctness_passed: true/false`；性能完成显式正数 `latency_us`。失败写 `status: failed` 和 `reason`。资源版本不能被覆盖，修复生成新版本与新 Attempt。`round` 仅显示轮次，`depends_on` 才是实际依赖。

`session open` 启动独立心跳进程，每 5 秒更新同目录 `.heartbeat.json`；`context.heartbeat.directive` 为 `continue/stop/unknown`。开始 Attempt 要求 20 秒内有效心跳；后端再次验证预算与停止状态。外部执行器必须读取停止信号并实际终止任务，然后上报终态；CLI 不自动杀远端设备进程。心跳失联时不能假定任务终止，也不能重复派发。

若原 session 已丢失，先核实外部执行器确已终止，再用 operator 凭据执行 `attempt reconcile ATTEMPT_ID --expected-version N --file result.json`，只接受 `stopped/failed`，`reason` 必须记录核对证据。不能通过此命令宣称实验成功。

## 知识与来源

`knowledge put --file knowledge.json`：`title/body` 必填；`id/version/category/claim_type/applicability/unverified_scope` 描述知识与适用边界。默认假设，不因性能变快自动升级因果解释。已引用的条目不可覆盖，修订用新 ID。

`relation add --file relation.json`：支持 `derivation/reference/support/refute`，字段 `source_type/source_id/target_type/target_id/rationale`。知识端点要求准确 `knowledge_version`；support/refute 还需来源节点已终态的 `evidence_attempt_ids`。派生关系不得成环，其他关系表达借鉴或证据。

```json
{"kind":"reference","source_type":"knowledge","source_id":"k1","knowledge_version":"v1","target_type":"node","target_id":"n1","rationale":"采用其分块原则；本节点重新验证"}
```

`provenance show --knowledge KNOWLEDGE_ID` 返回该知识与直接相邻的关系。需要继续追溯时用 `node show`，当前不提供自动因果归因或任意深度的递归查询。画布仅绘制方案节点；知识/经验及其关联仍可在右侧查看。

`candidate mark --file candidate.json` 可标记待冷却候选，字段为 `node_ids/resource_version_ids/knowledge_ids` 数组，`content_status` 默认 `ready-for-cool`。这不执行审批或合入。

## 交接与错误恢复

`handoff save --file handoff.json` 必需 `summary`，可带 `best_node_id/evidence_versions/open_questions/suggested_next_steps/in_flight`；交接文件写真实外部执行器标识和未决项。`session close --handoff HANDOFF_ID` 要求该 session 无未决工作；关闭不等于停止项目。新 session 使用新上下文和新 ID，先读 `context` 再继续。`session open --resume-session ID` 仅用于同一真实 session 丢失本地绑定后的恢复，已关闭身份不可恢复。

除 help 外 stdout 始终为 JSON：成功 `{ok:true, request_id, data}`，失败 `{ok:false, request_id, error:{code,message}}`。退出码：0 成功、2 输入/上下文问题、3 凭据问题、4 冲突/校验失败、5 服务或连接失败、6 不支持。

每次写入先保存本地请求日志。`request show REQUEST_ID` 查询当前项目的服务端提交记录；`unknown` 不等于未执行。`request retry REQUEST_ID` 从日志重发**完全相同**的请求，不能换请求编号重复未知结果。它重试数据库登记，不重跑外部实验。创建/导入尚无绑定时直接用原上下文路径和 `request retry`；导入请求在独立服务端命名空间中保存。

`PARTIAL_CREATE` 表示项目已创建但契约上传未完成，错误包含项目 ID：先绑定该项目，通过 `resource put --node '' --category artifact --file task.json` 补传，不能重复创建项目。本地请求日志可能包含代码和实验数据，按工作数据保管，不提交到 Git。
