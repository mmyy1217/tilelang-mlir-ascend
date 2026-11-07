# Runner 契约 v1

`scripts/harness_runner.py` 是 controller 侧的有界执行入口。controller 是持有项目 `.workspace/` 的机器；执行目标由配置文件描述。它只负责**传输、执行、回收和退出传播**，不做算子语义判断，不替代 `harness-prepare-environment` 的问询与探针验收，也不替代 `harness-device-test` 的精度/性能协议。

## 1. 覆盖范围

本版本只实现一格：**local controller + remote（或 local）host + docker（或 host）**。具体组合由配置表达，但只有实际跑过探针的组合才能记为 `verified`。

| 组合 | 本版本 |
| --- | --- |
| local controller × remote ssh × docker exec | 已实现并有实测（`docker exec fzm`） |
| local controller × remote ssh × host | 已实现，未在真实机器验收 |
| local controller × local × host | 已实现并有单测 |
| local controller × local × docker | 已实现，未在真实机器验收 |
| 远程 controller 调度另一台远程 | 不支持，不在本版本范围 |

未验收的组合在环境约定里写 `unverified`，不得因为代码路径存在就记 `verified`。

## 2. 配置文件

JSON，`contract` 固定为 `operator-harness/runner@1`。放在项目 `.workspace/` 或环境约定指向的私有位置；**不含密钥正文**，SSH 认证走既有 `~/.ssh/config` 与 agent。

```json
{
  "contract": "operator-harness/runner@1",
  "environment_id": "a5-fzm-docker",
  "transport": {
    "kind": "ssh",
    "host": "tilelang-A5-yellow",
    "connect_timeout": 20,
    "ssh_options": ["ServerAliveInterval=15"]
  },
  "execution": {
    "kind": "docker",
    "container": "fzm",
    "workdir": "/home/f00959234/work/my-operator",
    "interpreter": null,
    "env": {}
  },
  "paths": {
    "remote_root": "/home/f00959234/work/harness-jobs",
    "local_data_dir": ".harness-runner"
  },
  "limits": {"timeout_seconds": 600, "max_upload_mb": 256},
  "lock": {"dir": "/home/f00959234/work/harness-locks", "wait_seconds": 0},
  "identity": {"expected_arch": "aarch64"}
}
```

字段语义：

| 字段 | 含义 |
| --- | --- |
| `transport.kind` | `local` 或 `ssh`。`ssh` 使用系统 `ssh`，`BatchMode=yes`，失败即报错不交互 |
| `execution.kind` | `host` 直接执行，`docker` 用 `docker exec -i` 进入指定容器 |
| `execution.workdir` | 工作负载的 cwd；必须在目标上存在且可写 |
| `execution.interpreter` | 显式指定解释器；为空时按脚本后缀选（`.py` → `python3`，其余 → `bash`） |
| `paths.remote_root` | 每个 job 在目标上的私有目录根，`<remote_root>/<job_id>` |
| `paths.local_data_dir` | controller 侧 job 记录根，默认 `.harness-runner`，应加入 `.gitignore` |
| `limits.timeout_seconds` | controller 侧等待上限，覆盖排队与执行 |
| `lock` | 可选的共享锁目录；`wait_seconds=0` 表示拿不到立即失败（退出码 95） |
| `identity.expected_arch` | 可选；probe 时核对 `uname -m` |

## 3. 子命令与退出码

```sh
python3 harness_runner.py probe   --config C [--json]
python3 harness_runner.py upload  --config C --job J --src LOCAL:DEST [--src ...]
python3 harness_runner.py exec    --config C [--job J] --script S [--timeout N] [--no-lock]
python3 harness_runner.py fetch   --config C --job J [--dst D]
python3 harness_runner.py status  --config C --job J
python3 harness_runner.py receipt --config C --job J
```

`exec` 省略 `--job` 时自动生成 `<时间戳>-<6位随机>`。

退出码是接口的一部分，调用方必须原样传播：

| 码 | 含义 |
| --- | --- |
| 0 | 作业完成且工作负载退出 0 |
| 1 | 工作负载非零退出（候选/构建/测试失败） |
| 2 | 用法或配置错误，未接触目标 |
| 3 | 传输失败（连接、容器、上传、回收） |
| 4 | controller 超时；作业可能仍在运行 |
| 5 | 状态未知，禁止盲目重投 |
| 6 | 共享锁未取得，工作负载未启动 |

`ssh` 退出码与工作负载退出码分别记录。SSH 成功不等于设备执行成功，因此 `read_remote_exit` 必须从目标上的 `exit_code` 文件读回真实结果。

## 4. 每次作业的产物

目标上 `<remote_root>/<job_id>/`：

| 文件 | 内容 |
| --- | --- |
| `command.py` / `command.sh` | 上传的脚本，摘要记录在 receipt |
| `request.json` | 提交时的请求快照 |
| `upload-manifest.json` | 每个上传文件的相对路径、SHA-256、字节数 |
| `pid` / `started_at` / `finished_at` | 进程与时间证据 |
| `exit_code` | 工作负载真实退出码 |
| `stdout.log` / `stderr.log` | 完整输出 |
| `lock_state` | 使用锁时的 `acquired` / `lock_timeout` |

controller 侧 `<local_data_dir>/jobs/<job_id>/`：

| 文件 | 内容 |
| --- | --- |
| `receipt.json` | 契约、环境 ID、请求摘要、传输/执行身份、`status`、`ssh_exit`、`workload_exit`、`observed_state` |
| `request.json` | 提交快照副本 |
| `stdout.log` / `stderr.log` | 回收的输出副本 |
| `remote/` | 目标 job 目录的完整副本（`fetched: true` 时） |

`status` 取值：`completed` / `failed` / `transport_error` / `timeout` / `lock_timeout` / `running` / `unknown`。**作业完成不等于结果通过**；精度与性能判定仍由 `harness-device-test` 按协议给出。

## 5. 超时、断线与重投

- 超时后 controller 立即调用 `_observe_state` 记录目标实际状态（`running` / `finished:<rc>` / `absent` / `unknown`），不假定作业已停止。
- `status` 对 `timeout` 记录会重新观察目标；若已结束则补齐 `workload_exit`、日志与 `remote/` 副本，并清除过期的 `detail`。
- `unknown` 状态禁止重投；先用 `status` 或环境约定中的查询入口确认原作业。
- 上传后立即做摘要回读校验，不一致直接报传输错误，不进入执行。

## 6. 与节点的关系

| 节点 | 用法 |
| --- | --- |
| `harness-prepare-environment` | 用 `probe` 取得连接、路径、解释器、架构证据；把结果写进 `environments/<id>.md` 的能力表。probe 成功不等于算子可运行 |
| `harness-establish-baseline` / `harness-develop` | 用 `upload` + `exec` 提交构建或候选运行；用 `fetch` 回收产物 |
| `harness-device-test` | 用 `exec` 提交精度/采集脚本（脚本内自行完成 profiler 包围与 guard 检查）；用 `receipt` 与 `status` 核对退出码，不把 `ssh_exit=0` 当验收 |
| `harness-analysis` | 用 `fetch` 取回 trace/日志做离线分析 |

`harness_runner.py` 不启动 profiler、不分配设备许可、不做 guard 判定；这些仍由 device-test 的脚本与协议负责。

## 7. 明确不做

- 不做双向自动同步：`.workspace/` 是唯一权威记录，远程只放代码与产物。
- 不做后台常驻服务、登录启动项或跨会话调度。
- 不管理密钥、不写 `~/.ssh/config`、不改目标系统。
- 不猜测失败原因，不把传输成功写成执行成功。
