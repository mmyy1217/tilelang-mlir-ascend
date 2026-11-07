---
name: harness-remote-runner
description: 用有界 runner 在本地 controller 上向远程或本地执行目标提交算子构建、运行与采集作业，回收日志与产物并原样传播退出码。用于环境探针、候选构建/运行和结果回收，不替代上板精度/性能协议。
---

# Harness Remote Runner

把"本地 controller + 远程执行目标"这一格变成可复用工具，避免各节点重复拼接 `ssh ... docker exec ...` 并吞掉退出码。controller 是持有项目 `.workspace/` 的机器；执行目标由 runner 配置描述。

脚本为 `scripts/harness_runner.py`，只用标准库，可直接复制到项目私有目录。完整字段、退出码和产物布局见 [runner 契约](references/runner-contract.md)。

## 节点执行边界

当前会话或独立 agent 均可执行本节点。已收到 TASK 的子 agent 按 [节点契约](../harness-resume/references/node-contract.md) 读取输入，不递归派发。本技能是**工具入口**，不是流程节点：它不决定何时测精度、不选标杆、不判定目标达成。环境是否可用由上板约定决定，验收结论仍由 `harness-device-test` 给出。

## 使用顺序

1. **读环境约定**。从 `.workspace/ENVIRONMENT.md` 找到本节点绑定的 `environments/<id>.md`，确认传输方式、容器、工作目录、额度与允许操作。没有约定时先走 [harness-prepare-environment](../harness-prepare-environment/SKILL.md)，不要用本技能自行发明环境。
2. **写 runner 配置**。放在该环境的私有位置，字段按契约填写；SSH 只写 alias，不写密钥。`remote_root` 与 `local_data_dir` 必须是本任务私有目录。
3. **探针**。`probe` 验证连接、工作目录可写、解释器与架构；把实际命令、退出码和时间写进环境约定的能力表。probe 通过只说明执行链可用，不代表算子可编译或精度通过。
4. **提交作业**。`exec` 上传脚本，用 `setsid` 在目标上独立启动 wrapper（controller 超时也不影响它写终态），记录 request/upload 摘要、pid、起止时间、`exit_code` 与日志，随后回收 `remote/`。
5. **核对结果**。用 `receipt` / `status` 读 `workload_exit` 与 `status`；`ssh_exit=0` 不能替代工作负载退出码。失败、超时和状态未知分别处理。

```sh
python3 scripts/harness_runner.py probe  --config runner.json --json
python3 scripts/harness_runner.py exec   --config runner.json --job R1-build \
        --script build.sh --timeout 1800
python3 scripts/harness_runner.py status --config runner.json --job R1-build
```

## 解释器选择

`execution.interpreter` 显式指定时优先；否则 `.py` 用 `python3`，其余用 `bash`。需要激活 venv 或设置 `PYTHONPATH` 时，把激活写进脚本，或把 `execution.env` 写进配置（`host` 与 `docker` 两种模式都生效）；不要在配置里塞整条 shell 命令。

## 失败与恢复

- 退出码 2：配置错误或 **job ID 已被使用**。runner 拒绝覆盖已有 receipt 或目标上已有 `exit_code`/`lock_state` 的作业；换一个新 ID，不要清空旧记录。
- 退出码 1：工作负载失败，保留 `stdout.log`/`stderr.log` 与 `exit_code`，交回原节点判断是候选问题还是调用问题。工作负载自己返回 95 也是普通失败，不会被当成锁信号。
- 退出码 3：传输或容器失败，先核对 alias、容器名、挂载与路径映射，不要改工作负载。若 `fetched=false`，说明负载可能已成功但证据没回收，receipt 里仍保留真实 `workload_exit`。
- 退出码 4：controller 超时。wrapper 已用 `setsid` 独立运行，会继续写终态并释放锁；receipt 记录 `observed_state`，用 `status` 重新观察即可，不要重投。
- 退出码 5：状态未知。先查询原作业，确认结束/取消后才考虑新作业。
- 退出码 6：共享锁未取得，工作负载未启动（目标上 `lock_state=lock_timeout`）。按环境约定等待或改期，不擅自删除他人锁。

共享设备用 `lock` 串行化；`lock.key` 是**共享资源标识**（缺省 `environment_id`），同一设备的所有作业必须用同一 key，job ID 只出现在 `holder` 里。锁目录由环境约定指定。

## 边界

不做双向自动同步，`.workspace/` 是唯一权威记录。不启动 profiler、不分配设备许可、不做 guard 判定。不管理密钥、不改目标系统、不清理他人文件。不把传输成功写成执行成功，不把作业完成写成精度或性能通过。
