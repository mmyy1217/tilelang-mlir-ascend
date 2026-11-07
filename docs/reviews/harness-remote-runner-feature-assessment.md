# Remote runner 特性必要性复核

复核对象：本分支修复提交 `29aae18b1`。本文供后续开发 agent 判断工作方向；属于架构复核建议，不表示用户已经决定删除代码或撤销分支。

## 结论

**当前没有充分理由继续把这套独立的通用脚本 runner 建设为 Harness 的默认功能。建议暂缓合入和扩展，优先适配已有 Onboard/DeepJIT 与 simulator。**

“需要统一执行入口”和“需要重新实现 SSH、传输、锁及作业管理”不是同一项需求。之前实现 review 中的修复建议不构成对该特性必要性的认可；最初将统一接入缺口直接归结为需要新增此 runner，判断不充分，此处更正。

这不是认为通用脚本执行永远没有价值，也不是认为已有后端已经完成 Harness 端到端验收。若后续存在具体且不能由现有后端承接的任务，可以重新评估一个可选后端。

## 核查依据

本次重新检查了现有 Onboard/DeepJIT 与 simulator 的本地源码和文档；下列路径是各自仓库内的接口定位，不要求用户采用某个外部固定版本。

| 已有能力 | 已检查的入口 | 对新增 runner 的影响 |
| --- | --- | --- |
| 本地 lowering、SSH worker 远端编译及执行 | Onboard `docs/remote-execution/README.md`、`deep_jit/remote/__init__.py` 的 `RemoteRuntime` | 远端上板执行并非空白能力 |
| 输入上传、完整输出下载、编译缓存和产物身份 | `RemoteBuffers`、`RemoteRuntime._compile`、remote cache | 不应再让 agent 通过通用脚本重新拼同一条执行链 |
| 性能和流水采集回传 | `RemoteRuntime.profile`、`profile_pipeline`、`deep_jit/remote/pipeline.py` | Harness 应绑定采集范围和证据身份，而非替代已有采集入口 |
| SSH 请求超时、错误返回、不自动重放 | `deep_jit/remote/transport.py` 的 `SSHTransport.request/abort` | 已有故障边界，但尚不等于持久 job 恢复 |
| 仿真环境检查、执行、锁、超时及 partial profile | simulator `bin/simctl` 的 doctor/profile、flock、timeout | 无需另建一套仿真执行机制 |
| 仿真容器内执行及环境检查 | simulator `bin/project-container` | 容器执行已有可复用入口；不据此宣称远程矩阵全部已验收 |

Harness 自身的 [复用计划](../REUSE_PLAN.md) 第 3、4 节明确要求适配这些已有能力；[路线图](../ROADMAP.md) 的“随 harness 交付统一 runner”明确要求通过适配层复用 Onboard/DeepJIT。这里的 Onboard 指上板工具，区别于负责项目接入的 `harness-onboard` skill。

目前新增的 `harness_runner.py` 没有调用上述后端，而是接收任意脚本，独立管理 SSH/Docker、上传、锁、后台进程、轮询、日志和回执。修复提交改善了这套实现的正确性，但没有改变这个分工，也没有提供一个已有入口无法覆盖的具体算子任务。

## 哪些能力仍值得补齐

1. **候选执行契约与后端适配。** 把候选摘要、源码/产物身份、ABI、launch 参数、case、测量协议及环境修订交给已有执行后端；返回结构化结果及证据索引，避免 agent 重新猜测调用方式。
2. **Harness 状态与后端状态的映射。** 区分编译失败、运行失败、精度失败、连接中断、部分采集和缺失证据，不把 shell 返回值当作完整设备结果。
3. **特定的长任务恢复需求。** DeepJIT 当前在会话超时或断线后关闭会话，不提供跨会话恢复原请求。若业务确实需要，应明确原 job 身份、完成判据、取消与回收责任，再决定扩展哪个后端。

外层脚本成功/失败不能自动说明内层 DeepJIT 会话、设备 kernel 或 profiler 的状态。把已有远程客户端再包进通用远程 runner，可能形成两套配置、两层超时和两套 job 身份，不能视为已解决恢复问题。

## 建议给开发 agent 的下一步

- 暂停为当前独立 runner 继续扩展功能；先确认需求和后端边界。
- 在后续方案调整中，撤回 `harness-device-test`、环境准备指引及分发 AGENTS 中强制或优先使用新 runner 的要求，保留已验证的 Onboard/DeepJIT、simulator 和项目测试入口为默认路径。本文提交本身不修改这些文件。
- 选择一个实际算子，通过现有后端完成最小闭环，列出 Harness 需要但后端无法提供的字段或操作；据此做薄适配。
- 不把“既有工具未在 Harness 中统一接入”写成“没有执行能力”；也不把外部工具存在写成已完成全矩阵验收。
- 本分支从较早的 Harness 基点开发。若继续整合，应核对目标分支新的多项目及共享环境约定，避免继续引入全局 `.workspace/STATE.md` / ENVIRONMENT 绑定；不要只修 runner 自身而忽略流程契约变化。
- 保留当前分支与测试作为研究结果；是否删除或撤销实现另行决定，不因本文自动清理。

只有同时说明以下内容，才建议重新启动独立 runner 特性：明确的真实任务；已有后端为何不能承接；新增后端的最小职责；状态/锁/取消归属；与既有后端的验收对照。可能的场景是必须在指定容器执行、且不适合 DeepJIT runtime 的第三方构建或测试脚本，但当前提交尚未证明这一需求。

## 修复复核与限制

修复后的原有测试共 26 个（runner 20 个、profile tools 6 个），本次全部通过。资源锁、复用 ID 拒绝、本地超时后的 wrapper 存活、日志编码、host 环境变量及摘要校验等已有对应修正。这说明修复有效推进，不代表完整远程执行验收或特性必要性已成立。

另外，补充故障注入仍确认一个恢复边界：让真实 `_launch` 启动负载后模拟启动确认丢失、抛出 `TransportError`，`cmd_exec` 将其记为终态 `transport_error`；目标实际写入 `exit_code=0` 后，`status` 仍返回旧的 `transport_error`，CLI 退出 0。定位为 `cmd_exec` 包围 `_launch` 的异常处理及 `cmd_status` 的终态短路。启动确认丢失不能证明任务未提交，必须保留可观察的不确定状态。该结果通过本地启动加故障注入获得，不是实机断网测试。

本次未进行真实 SSH/Docker/NPU 验收，也未全面重新审计所有新代码。不要将测试通过描述为所有旧问题及边界均已关闭。优先决定是否需要这个独立后端，再决定其后续修复投入。
