# 测量契约 v1

本格式用于程序直接读取测试产物，无需可视化 Agent。校验的是记录身份、完整性与可比性，不独立证明设备执行或精度结论真实。生产者负责运行验证并准确记录；合成数据必须 `synthetic: true`。

所有路径相对于 `.workspace/`，使用 `/`；所有引用使用 `{ "path": "…", "sha256": "文件原始字节的64位小写SHA-256" }`。引用文件必须随快照归档，不能引用外部路径或遗漏文件。先固定候选、环境、参考、case 集及协议，再执行测试，最后写报告与索引；修改被引用文件后必须重新验证并生成新报告。建议原子替换索引，避免 watch 捕获半写入。

## 索引

`.workspace/MEASUREMENTS.json`：

```json
{
  "schema": "harness-vis/measurements@1",
  "reports": [
    {"path": "runs/B0/measurement.json", "sha256": "<摘要>", "kind": "baseline", "subjectId": "B0"},
    {"path": "runs/T1/measurement.json", "sha256": "<摘要>", "kind": "candidate", "subjectId": "C1"}
  ],
  "baseline": {"path": "runs/B0/measurement.json", "sha256": "<摘要>"}
}
```

最多 1000 份报告，路径不可重复。baseline 是显式冻结的比较基准，也可引用通过完整校验的候选报告；省略时保留各 case 结果，不排名。正式索引存在但无效时不会回退到模拟演示数据。

## 报告

`runs/T1/measurement.json`：

```json
{
  "schema": "harness-vis/measurement@1",
  "synthetic": false,
  "subject": {"kind": "candidate", "id": "C1", "manifest": {"path": "candidates/C1/CANDIDATE.md", "sha256": "<摘要>"}},
  "runId": "T1",
  "recordedAt": "2026-01-01T12:00:00Z",
  "environment": {"path": "ENVIRONMENT.md", "sha256": "<摘要>"},
  "reference": {"path": "measurement-inputs/reference.md", "sha256": "<摘要>"},
  "caseSet": {"path": "measurement-inputs/cases.json", "sha256": "<摘要>"},
  "protocol": {"path": "measurement-inputs/timing.json", "sha256": "<摘要>"},
  "cases": [{"id": "small", "accuracy": "passed", "samplesUs": [10.1, 10.0, 10.2], "maxAbsDiff": 0.001}]
}
```

示例摘要是占位符，生产者必须计算实际值。候选报告必须位于声明的 device-test Run 目录内；TASK 必须明确绑定该候选。manifest 必须与候选身份摘要一致。标杆报告使用 `kind: baseline`，manifest 指向含对应 baseline_id 的标杆清单，Run 为 establish-baseline。recordedAt 必须有时区，按时间选择复测，而非文件顺序。

accuracy 为 passed / failed / not_run。样本单位固定 μs，必须全部是有限正数，数量等于协议 repeatRuns；未采样用空数组。failed/not_run 不允许带常规性能样本。maxAbsDiff 可省略，提供时须有限且非负。缺少必选 case 视为 not_run；重复或未知 case 拒绝。参考文件应记录实现版本、精度容差及验证方法，环境文件应固定设备、工具链和版本。

## case 与协议

`measurement-inputs/cases.json`：

```json
{"schema":"harness-vis/case-set@1","cases":[{"id":"small","label":"M=128 N=128 K=256 · FP16"}]}
```

case ID 唯一且标签非空，可附加 shape、dtype 等字段；整个文件的摘要参与可比性判断。

`measurement-inputs/timing.json`：

```json
{"schema":"harness-vis/timing-protocol@1","unit":"us","warmupRuns":20,"repeatRuns":3,"timingBoundary":"kernel-only","syncMethod":"device synchronize before and after sample"}
```

预热次数为非负整数，采样次数至少 3。计时边界和同步方法必须明确；可附加计时器、缓存策略等字段。报告保留原始样本，Median 为排序后中间值（偶数取中间两数均值），P95 使用 nearest-rank。

## 汇总规则

- 环境、参考、case 集、协议的摘要及 synthetic 标记必须与基线完全一致。语义相同但字节不同也暂不比较。
- 所有必选 case 精度通过且采样完整后，逐 case 加速比为基线 Median / 候选 Median；综合值是等权几何平均。不同 shape 的时延不平均成一个 μs 数。
- 同候选的最新报告失败、不完整或不可比时，不回退到旧报告参与排名。历史报告保留供检查。
- 同候选任一报告身份/格式无效，或最新时间存在并列时，暂停该候选汇总，直到生产者修正索引/记录。原始证据仍可查看。
- 任务 completed 不代表精度通过；没有正式测量索引时只展示结构记录。Agent 注释不参与数值计算。

完整可运行模拟产物生成器位于开发仓库 `examples/harness-vis/create_measurement_contract_demo.py`；它覆盖三种 shape、精度失败、协议不一致与复测，全部明确标注模拟。
