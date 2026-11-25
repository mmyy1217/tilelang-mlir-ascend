# 原生接口适配范围

核对对象：`upstream/harness` 的 `737a3c10`，包括 environment-contract、node-contract、search-knowledge、validation-protocol、TASK/RESULT 模板及 searchctl.py。版本升级须重新核对，不能把本次适配当成未来协议保证。

| 原生输入 | 可视化行为 |
| --- | --- |
| search/tree.json，search@1 | 显式搜索节点、Run/候选登记、事件、知识引用；缺失或未知版本不补造树 |
| node-task/result@1 | 前言身份与检查点，四类结果判定；环境绑定与作业信息保留 Markdown |
| run.task/result 摘要引用 | 校验捕获版本；接收与运行、精度状态分开 |
| environment-index/environment@1 | 读取索引与各环境约定，保留能力状态、型号与工具链原文 |
| hot 文件与证据引用 | 核对摘要，读取直接指向的原生文件；晋升绑定确切修订 |
| 默认 knowledge/cooled | 捕获文件、来源及引用，review.delivery 按标识处理 |

## 仍有接口限制

- **执行器实时状态**：本次核对的 Harness 没有统一心跳接口。RESULT 的 running/updated_at 只能展示记录时状态；watch 心跳只说明采集器状态。
- **simulator 输出**：由环境约定确定，没有统一测量 JSON 或指标单位。可查看原始报告；不能可靠自动生成速度排行或将模拟周期换算为微秒。
- **环境与作业**：SKU、CANN/TVM/TileLang 版本、环境绑定与 job 信息主要是 Markdown/free text。Vis 保留完整原文，缺失字段不自行探测补齐；不把 completed 解读为外部 job 已结束。
- **捕获范围**：当前读取 `.workspace/` 和默认项目 `knowledge/cooled/`。Harness 允许自定义 cooled 输出目录，其他目录及外部证据不会自动递归读取；引用未捕获会提示。未被索引引用的外部知识也不会出现在界面。
- **未知或歧义格式**：未知协议、重复身份字段、摘要不匹配、不能识别的判定表保留原文并提示。自由文本不会被猜测成结构化事实。

验证使用当前 searchctl.py 生成的隔离夹具，无设备或模拟执行。可视化不调用 Harness 写入命令，不创建新执行、探测或测量协议。
