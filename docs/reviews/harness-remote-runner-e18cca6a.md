审查对象：`origin/harness-remote-runner`，提交 `e18cca6a4941f6404bad4b9a180e9de06ca8654e`。审查相对分叉点 `0c9c650eb075dd2c1d840cf80796e1347fc2a0ad` 的 14 个文件；当前 `upstream/harness` 为 `84b9888c1`，未把 upstream 的其他变更算入本次提交。

结论：建议修复以下问题后再合入。确认 4 个 P1、8 个 P2。未改动被审查源码。下文代码链接固定到被审查提交；各条目包含复现条件、实际结果及修复建议。

1. **[P1] 共享设备锁必须按资源标识，而不是按 job ID 标识。** [harness_runner.py:344](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L344)

   配置同一个 `lock.dir` 的两个作业分别创建 `lock-a.lock` 和 `lock-b.lock`，都能获得锁，因而无法实现技能所承诺的共享设备串行化。实际同时启动两个本地作业，第二个检测到第一个仍持有工作资源，并以 workload 77 退出；runner 没有在锁阶段拦住它。真实设备上会造成竞争并污染稳态性能测量。应让同一共享资源使用同一锁键，job ID 只作为持有者信息。现有测试预建的是同名 job 的锁，覆盖不到不同 job 的争用。

2. **[P1] 拒绝覆盖已有执行记录，防止旧退出码被归到新请求。** [harness_runner.py:472](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L472)

   `exec` 无条件复用 job 目录并覆盖 receipt，目标上的旧 `exit_code`、时间和日志却继续存在。复现：先让 `--job reused` 成功，再以同一 ID 运行 `sleep 2; exit 7` 并设 `--timeout 1`，新请求的 `observed_state` 是旧的 `finished:0`，随后 `status` 报 `completed/workload_exit=0`；新负载实际到达失败分支。这会把错误候选记录为执行成功。应区分“仅上传、尚未执行”的 job 与已有执行记录，采用排他的提交/请求身份校验；不能通过清空仍运行的 job 来解决。

3. **[P1] 本地超时不能杀掉唯一负责写结果的 wrapper 后留下工作负载。** [harness_runner.py:198](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L198)

   `subprocess.run(..., timeout=...)` 超时会杀死直接子进程，即本地 wrapper，但它启动的脚本仍可能继续执行；写 `exit_code`、`finished_at` 和释放锁的逻辑都在已死的 wrapper 中。独立新 job 的复现中，超时后子脚本已写下完成标记，目标却始终没有 `exit_code`，`status` 返回 5/unknown，无法按契约恢复真实结果。需要让结果记录进程独立于 controller 的等待超时存活，或实现明确的进程组取消和终态记录。

4. **[P1] 提交后的传输失败必须允许重新观察远端状态。** [harness_runner.py:600](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L600)

   结果回读连接失败会把 receipt 写成 `transport_error`，而 `status` 把这个状态当作终态直接返回，不再执行 `_observe_state`。因此 SSH 在提交后断开、或者只在读取结果时暂时失败，都会让可能仍运行或已完成的作业永久不可恢复。故障注入只令一次 `read_remote_exit` 不可用，真实目标已有 `exit_code=0`；恢复后 `status` 仍返回 `transport_error`，且 CLI 退出 0。应区分提交前的确定失败和提交后的不确定状态，后者允许查询和收回结果。

5. **[P2] 产物回收失败应传播传输失败退出码。** [harness_runner.py:548](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L548)

   `fetch_tree` 的 `TransportError` 只写入 `fetched=false`，最终仍依据 workload 返回 0 或 1。真实负载成功后注入回收连接失败，得到 `status=completed, fetched=false`，但 `exec` 退出 0。契约明确把回收失败归为退出码 3，按 CLI 退出码推进的调用方会在缺少证据/产物时继续。应保留 workload 的真实退出码，同时通过命令退出码报告回收失败。

6. **[P2] host 执行模式需要应用 execution.env。** [harness_runner.py:169](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L169)

   只有 docker 分支处理 `execution.env`；host 分支直接返回 `bash -c`。给 local/host 配置一个专用环境变量，负载实际读到 `missing`。技能明确支持用该字段设置 `PYTHONPATH` 等环境变量，这会导致 host 与 docker 模式使用不同的 Python 包、设备或编译环境。应在 host 包装命令中同样设置环境，并验证 local/host 与 ssh/host。

7. **[P2] 不要仅凭传输返回 95 判断锁未取得。** [harness_runner.py:522](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L522)

   wrapper 原样以 workload 的退出码退出，因此真实脚本也能返回 95。未配置任何锁时运行 `exit 95`，目标已写 `exit_code=95`，runner 却返回 6/lock_timeout，声称 workload 未启动，并丢掉真实 workload_exit。这会误导失败归因及重试。应依据本次请求的 `lock_state`/退出记录识别锁失败，而不能把正常 workload 退出码作为独占的控制信号。

8. **[P2] probe 必须在写权限验证成功后才输出 writable。** [harness_runner.py:426](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L426)

   成功标记在 `touch .runner-probe` 之前输出，解析又只检查输出包含该标记。对 chmod 0555 的目录以普通用户执行 probe，即使 touch 失败，仍返回 0 并记录 `workdir=verified/writable`。环境准备会据此接受不可写的执行目录。应让写入/清理全部成功后输出唯一成功标记，并检查探针命令的退出码。

9. **[P2] 摘要不匹配必须通过退出状态明确拒绝。** [harness_runner.py:276](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L276)

   远端 Python 在发现不匹配时只打印 `mismatch:<filename>`，仍返回 0；controller 又用输出包含 `ok` 判断成功。因此目标文件名含 `ok` 时，不匹配消息也会通过。实际在校验前篡改 `ok_payload.txt` 后，`upload_tree` 正常返回 manifest，而目标哈希与 manifest 不同。应让校验进程在任何 mismatch 时非零退出，并解析明确的成功结果。

10. **[P2] sha256sum 回退路径传入的内容不是校验清单。** [harness_runner.py:271](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L271)

    `checks` 拼成 `echo "<hash> <path>" && ...`，然后作为一个被 quote 的字符串交给 `printf`，导致 `sha256sum -c` 读到的是 shell 命令文本，而不是 `<hash>  <path>` 格式。用不含 python3、但含 bash/tar/sha256sum 的目标 PATH 实测，完整上传的文件也返回 3。应生成真正的逐行清单，或使用其他可验证的摘要协议。该回退能力是本实现注释明确支持的路径。

11. **[P2] 日志不能使用可能出现在负载输出中的分隔符拆分。** [harness_runner.py:381](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/scripts/harness_runner.py#L381)

    `read_remote_exit` 把 stdout/stderr 拼接后按 `---\n` 拆分。负载 stdout 为 `before\n---\nafter\n`、stderr 为 `error\n` 时，controller 保存的 stdout 只剩 `before\n`，stderr 却变成 `after\n---\nerror\n`。`remote/` 的原始文件正确，controller 顶层的两份日志已经失真，会影响后续诊断和解析。应分别传输文件，或用明确长度/结构化编码封装。

12. **[P2] 对外契约文档需要移除私人环境信息。** [runner-contract.md:34](https://github.com/mmyy1217/tilelang-mlir-ascend/blob/e18cca6a4941f6404bad4b9a180e9de06ca8654e/assets/skills/harness-remote-runner/references/runner-contract.md#L34)

    `assets/skills/` 中的通用文档写入了具体 SSH alias、容器名、个人 home 目录路径，以及对应环境的实测记录。仓库 AGENTS.md 明确要求产品资源不得包含私人机器路径和具体工作记录。应使用中性占位符，并把实际配置与实测证据留在 `.workspace/`，避免随技能分发。

验证情况：

- 分支原有测试共 14 个（runner 8 个、profile tools 6 个），全部通过；未修改测试源码。
- 补充执行了 10 个运行场景及一个日志分隔符场景，实际结果已写入对应条目。其中第 4、5 项通过 mock 注入结果回读/产物回收故障，其余运行场景使用真实的本地 shell 和文件传输。
- 未连接真实 SSH/Docker/NPU 环境；不据此判断这些环境已经通过验收。
- 修复时建议将第 1–11 项的复现条件转为回归测试；第 12 项检查分发资源与文档中的私人配置。

原有测试命令（在仓库根目录，使用 sys.executable 指向有效 Python 的环境）：

```sh
python3 -m unittest discover -s tests -v
```
