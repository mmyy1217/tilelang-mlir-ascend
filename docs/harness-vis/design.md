# Harness Vis v1

本版本在 agent-vis 的 React/FastAPI 工作台上增加 workspace 观察入口。Harness 开发 Agent 继续拥有源记录；可视化仅消费复制的产物。默认和 `/?legacy=1` 共用原 App、GraphCanvas、AgentTimeline、ProjectSidebar 与 RightInspector。默认入口只增加数据适配层，不另做页面布局；不把固定八阶段强套到 Harness Run。

## 数据流与边界

```text
live .workspace → capture/watch → immutable snapshot → parser → read-only Web
                                       ↓ (optional)
                                 Agent annotations → publish → same Web
```

capture、publish、install 使用 Python 标准库。serve 使用打包的 FastAPI 服务和前端；仅绑定本机回环地址。安装器复制整个 Skill、server 与已构建 web/dist，目标项目不需要引用源仓库。安装目标已存在时拒绝覆盖。第一版升级需明确选择新的安装位置或备份后替换，不静默改用户文件。

## 快照协议

`harness-vis/snapshot@1` 包含项目 ID（本地绝对项目路径摘要）、快照 UUID、采集时间、源路径、文件清单、字节数、SHA-256、文本标记、遗漏原因和一致性状态。同一路径多次采集归入同一项目；移动项目目前视为新身份。

存储位于源 `.workspace` 之外。每次先写隐藏 staging 目录，完成后原子重命名；服务忽略 staging。前后核对文件属性及内容摘要。`checked` 仅表示核对时未发现变化，不能声称文件系统事务性快照；检测到变化为 `partial`。目录符号链接、文件符号链接、特殊文件、`.env*`、`.git` 等不跟随/不复制。默认单文件 2 MB、单快照 50 MB，超限保留元数据。用户仍需选择适合本地查看的 workspace；这不是通用秘密扫描器。

目录外引用不自动追踪，不执行 copied scripts。第一版不保证快照足够重建原算子；这里只保证可视化能明确说明捕获范围。原始文件不能原地修改，Agent 说明发布到单独 views 目录，每次新增版本。

## 展示协议

`harness-vis/model@1` 包含 records、runs、warnings、annotations。程序按 Markdown 键值索引 TASK/RESULT/CANDIDATE；前端从候选清单与明确的任务输入生成展示模型，不改变源记录。

**主树一个节点对应一个 candidate_id。** 仅由 CANDIDATE 的 parent_candidate 建立父子边；TASK.parent_run 是任务前序，不能用来推断候选派生。相同候选的多次测试保留为节点内的多个 Run。候选存在但尚未测试时也显示。父候选缺失、重复身份或环会显示提示，不补造边。当前版本不从任务完成推断精度、性能或最佳候选。

候选内沿用原卡片的阶段条，展示分析、开发、测试三个任务组，不要求每组已有 Run，也不将它们拆成虚构的八阶段：

- producer_run 明确关联候选的生成任务；它显式引用的 analysis 前序可作为方案依据。
- analysis / device-test 的 TASK 中明确的 candidate_id / candidate / candidate_path，或“输入与读取顺序”表中的候选清单路径，关联到具体候选。只接受确切 ID 或清单路径（支持项目根 .workspace 前缀和 Markdown 链接）；不从散文、RESULT 的历史提及或执行先后猜归属。
- 同一分析可诊断父候选并为多个子候选提供依据；节点内分别可查看，Agent 时间线按 Run ID 去重。
- 环境、标杆、未产出候选及无法确认归属的 Run 进入“项目记录与未关联任务”。此入口不作为树节点，也不参与候选数量；没有候选时保留原文与执行记录入口。
- 未实现的想法仍在分析原文中查看，不创建虚构候选；后续若需要计划节点，需先引入稳定方向身份。

候选清单和测试结果始终可以打开原文。逐 case 测量、复测选择与协议可比性由程序按[测量契约](../../skills/harness-vis/references/measurements.md)校验；不依赖 Agent。浏览器用“记录为运行中”，不从快照推断外部作业仍活跃。

Agent 只生成附带源路径的 Markdown 注释。发布器校验每条路径确实已复制，并检查快照摘要。来源存在不证明注释内容正确，因此 UI 明确标记“Agent 观察”，旁边提供原始证据入口。说明不能覆盖程序解析的 Run 状态。

## 同步与界面

- 手动 capture 或显式 watch 自动采集；需要解读时才由专用 Agent 在独立上下文处理指定快照并输出 annotations，自动采集不继承旧说明。
- 浏览器每 5 秒读取快照索引与当前展示版本。只在用户选择“跟随最新快照”时切换新快照；固定历史快照保持不变。
- 刷新索引不执行采集。服务连接状态与“数据截至”时间分别显示。
- 保留原探索图 / Agent 双视图与右侧文件库，同一选择状态联动；仅在顶部加入快照选择和刷新。原文读取时再次校验 SHA-256，Agent 说明位于原总结入口。
- 时间轴显示记录创建时间，不伪造 Agent 工时、运行时长或精度测量。
- 原始 Markdown 按现有安全渲染器展示，不运行 HTML 或加载外部图片。

## 验收与后续

自动测试覆盖快照隔离、追加历史、大小限制、符号链接与私密文件排除、源变更、哈希损坏、注释引用、只读 API 与安装包完整性。另做干净目标安装、独立 Agent 冷启动、桌面/窄屏浏览检查。

后续优先：节点结束触发增量同步 → 文件监听/结构化事件。开发控制、设备轮询、多协调者与公网部署不属于 v1。

## 自动采集实现

`watch` 以捕获范围内的内容摘要检测变化（大文件仅比较遗漏元数据），默认 2 秒轮询、2 秒稳定窗口；私密文件和符号链接不读取、不触发新快照。调用既有 capture 保留大小限制和双遍校验。内容不变不重复采集；重启按 manifest.capturedAt 选择最近快照，不能按带随机后缀的目录名字判断同一秒内的先后。采集中变化允许同一观察指纹最多两次尝试。

每个 project/data-dir 用 POSIX flock 排他，进程退出自动释放；watch.json 通过原子替换记录心跳、阶段、最后快照和错误。API 将超时心跳投影为 stale，不推断源设备存活。正常退出与 SIGTERM 写 stopped；异常源读取不发布空快照，恢复后重新等待稳定窗口。可按采集次数或持续时间结束。

浏览器索引与模型各自串行轮询，较慢内容读取不因索引轮询被持续取消；只缓存最近三份快照内容。URL follow=1 表示跟随最新，单独 capture=ID 表示固定历史；保留候选、阶段与 Run 定位。历史数据不自动清理；不是原子文件系统快照，也不保证捕获每次中间写入。
