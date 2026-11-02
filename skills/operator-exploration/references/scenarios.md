# 三条使用闭环

这些是操作模板，ID 和结果由真实调用返回；不应将示例性能数字或占位 ID 上报为实验。

## 从零探索

1. `project create` 保存算子契约、输入域、环境、目标与预算。
2. 为当前 session 建立独立上下文，读取 `context`，创建基线方案节点。
3. 记录读取依据与优化思路，再上传 kernel。按真实执行依赖申请、执行并结束编译、正确性验证、性能测量和 profiling 的 Attempt。
4. 新方向创建方案节点，可列多个来源并说明各自贡献。需要并行时由主 Agent 使用宿主能力启动新 session，分别绑定上下文；工作台不自动派发。
5. 将日志和测量样本保存为资源版本；经验先作为假设登记，再建立具体支持/反例关系。将总结保存为 Markdown 资源。

## 失败修复

编译 C1 失败：上传报错日志，以 `failed` 结束 C1；同一节点创建 G2，依赖 C1 并指定新代码标签 v2。G2 输出新 kernel 资源后，C2 引用 G2 和该精确 kernel 版本，再重新验证与测量。保留 C1 和 v1，不把“旧版本验证通过”沿用到 v2。

## 暂停、换 session 与 fork

1. `project pause` 请求停止；外部执行器确认结束后上报终态。原 session 丢失时，operator 先核实执行器，再 `attempt reconcile`。
2. 保存交接摘要与证据，`session close`。新 session 使用新的文件和身份，读 `context` 获得交接及未完成方向。
3. 继续原图：使用最新 `context.version` 显式 `project resume`。
4. 探索另一路：`checkpoint create`，用返回的 ID 执行 `project fork`；为 fork 开新 session 并显式恢复。源图和源 session 不受影响。

## 已自动化验证的范围

`server/test_cli.py` 通过真实 HTTP 服务、CLI 子进程和独立心跳验证上传/下载、阶段链、版本冲突、请求重试、知识关系、导入导出、检查点 fork、session 交接和暂停恢复；测试使用明确标注的合成记录，没有启动编译器或设备测量。真实算子优化及外部执行器取消仍需选定任务后单独验证。
