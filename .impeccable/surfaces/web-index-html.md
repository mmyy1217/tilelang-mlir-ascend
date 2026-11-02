---
version: 1
slug: "web-index-html"
primary_target: "web/index.html"
related_targets: ["web/src/App.tsx"]
---

# Operator Knowledge & Experiment Workbench (`web/index.html`)

- **Mode**: `operate`
- **Scope**: Step 1 shareable interactive prototype (`web/`) for the TileLang-Ascend Operator Knowledge & Experiment Service.
- **Task & Content**: Inspect multi-agent operator optimization search trees (`FlashAttention-2 MixCV` & `Matmul L1/L0C`), switch seamlessly between Tree View and Agent Timeline View, expand node-internal 8-stage state machines (`StageAttempt` history and rollback chains like `G2 → C3 → V2`), inspect clickable parent-child derivation edges and cross-branch reference edges, and browse the right-hand node file repository (TileLang kernel code, optimization rationale, stage summary, experiment protocol, and NPU `msprof` bottleneck metrics).

## Direction contract

THESIS: An engineering stratigraphic core-log workbench where multi-agent kernel exploration, 8-stage node state machines, and hardware pipeline bottlenecks read as synchronized scientific survey strata—refusing generic SaaS analytics cards and decorative dark-neon dashboards.

OWN-WORLD: Uncoated warm survey stock (`#F4F1EA`) and alabaster sheet panes (`#FAF8F3`) framed by 1px basalt hairlines (`#1C1917`), deep core-head charcoal (`#1C1917`) for the top coordination bar, iron-oxide vermilion (`#9A3412`) for active selection and hot state markers, mineral jade (`#047857`) for verified `ready-for-cool` candidates, and cobalt (`#1D4ED8`) for baseline/Cube pipeline metrics.

STORY: A compiler or kernel engineer opens a shared exploration link, immediately sees why the best valid node (`N4 · 132.4 us`) beat the `cooled` baseline (`248.6 us`), traces how a compile/correctness failure rolled back (`G1→C1/C2 fail → G2→C3→V2 pass`) and borrowed a cross-branch sync fix from `N3`, and inspects the exact versioned artifacts and `ready-for-cool` evidence.

FIRST VIEWPORT: Full-height desktop workbench (`100vh`) with a 54px top task/budget/status coordination bar (carrying the `DEMO PROTOTYPE` badge, baseline vs. best valid latency, budget counter, stop reason, and URL share locator) over a resizable split workspace: left pane (54% width) showing the interactive React Flow search tree with depth strata, compact 8-cell stage strips, inline Cube/Vector/MTE2 utilization micro-bars, and view switcher (`搜索树视图` / `Agent 轨迹视图`); right pane (46% width) open by default on best valid node `N4`, showing its 8-stage state machine matrix, attempt dependency chain, and categorized file repository with live code/profile preview.

FORM: Geological & Stratigraphic Core Log Folio (position 4 of 7 on the ordered grounded list; `seed:2448234b0ae0f8f8`), raised by inline micro-bar pipeline utilization strips (`from: ikeda-datamatics`), paired claim-and-proof attempt badges (`from: digital-design-canon-monochrome-product-marketing`), contextual non-path dimming on selection (`from: pop-culture-shelf-streaming-title-card-wall`), and 8-cell typographic stage state strips (`from: rw-timetable-slide-rack`).

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
