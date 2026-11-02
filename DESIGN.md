---
name: TileLang-Ascend Multi-Agent Operator Optimization Workbench
description: Geological and Stratigraphic Core Log Folio for inspecting multi-agent operator search trees, 8-stage attempt trajectories, and versioned NPU artifacts.
colors:
  primary: "#8c2f1b"
  primary-soft: "#faeae6"
  secondary: "#0e6b48"
  secondary-soft: "#e3f5ed"
  tertiary: "#1b4d8c"
  tertiary-soft: "#e5eef9"
  amber-running: "#9a6112"
  amber-soft: "#fcf2e3"
  danger-failed: "#a32424"
  danger-soft: "#fbe8e8"
  paper-bg: "#f4f1ea"
  paper-elevated: "#fbf9f4"
  paper-recessed: "#ede8dc"
  code-obsidian: "#1b1916"
  ink-primary: "#191714"
  ink-secondary: "#3e3a32"
  ink-muted: "#6d6658"
  border-strong: "#191714"
  border-hairline: "#cfc8b8"
typography:
  display:
    fontFamily: "'IBM Plex Sans', 'PingFang SC', 'Noto Sans SC', system-ui, sans-serif"
    fontSize: "1.15rem"
    fontWeight: 700
    lineHeight: 1.25
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "'IBM Plex Sans', 'PingFang SC', 'Noto Sans SC', system-ui, sans-serif"
    fontSize: "0.95rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.005em"
  title:
    fontFamily: "'IBM Plex Sans', 'PingFang SC', 'Noto Sans SC', system-ui, sans-serif"
    fontSize: "0.82rem"
    fontWeight: 700
    lineHeight: 1.35
    letterSpacing: "0em"
  body:
    fontFamily: "'IBM Plex Sans', 'PingFang SC', 'Noto Sans SC', system-ui, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "0em"
  label:
    fontFamily: "'JetBrains Mono', 'IBM Plex Mono', 'SFMono-Regular', Consolas, monospace"
    fontSize: "0.72rem"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.02em"
rounded:
  none: "0px"
  sm: "2px"
  md: "4px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.ink-primary}"
    textColor: "{colors.paper-elevated}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "6px 12px"
  button-primary-hover:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.paper-elevated}"
  button-secondary:
    backgroundColor: "{colors.paper-elevated}"
    textColor: "{colors.ink-primary}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "5px 10px"
  badge-ready-for-cool:
    backgroundColor: "{colors.secondary-soft}"
    textColor: "{colors.secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
  badge-cooled:
    backgroundColor: "{colors.tertiary-soft}"
    textColor: "{colors.tertiary}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
  badge-hot:
    backgroundColor: "{colors.primary-soft}"
    textColor: "{colors.primary}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "2px 6px"
---

# Design System: TileLang-Ascend Multi-Agent Operator Optimization Workbench

## Overview

**Creative North Star: "The Stratigraphic Core Log Folio"**

The TileLang-Ascend Multi-Agent Operator Optimization Workbench treats multi-branch kernel exploration not as a transient chat stream or a glowing cyberpunk telemetry wall, but as a precision geological survey and hardware engineering ledger. Each search depth forms a horizontal stratum (`STRATUM D0` through `STRATUM D3`); each optimization node is a core sample bearing an 8-stage execution strip, Ascend 910B2 hardware pipeline utilization bars, and immutable `@vN` file provenance.

**Key Characteristics:**
- Daylight-first warm archival limestone surface (`#f4f1ea`) paired with deep obsidian ink (`#191714`) and high-contrast mineral semantic pigments.
- Dual-axis truth separation: execution status (`completed`, `running`, `failed`, `stopped`, `skipped`, `pending`) and content cooling state (`hot`, `ready-for-cool`, `cooled`) never share a badge shape or conflate color + icon encoding.
- Tabular monospace precision (`font-variant-numeric: tabular-nums`) across every latency readout (`132.4 us`), speedup ratio (`1.88x`), SHA256 digest, and stage attempt code (`G1@v1←P1`, `G2@v2←C1,C2`).
- Dual-view synchronized navigation: switching between the Stratigraphic Search Tree and the Agent Trajectory Swimlane preserves the active node, stage, attempt, and URL query locator (`?view=...&node=...&stage=...&attempt=...&edge=...`).

## Colors

### Primary (Cinnabar Rust — Active Focus & `hot` Mutable State)
- **Cinnabar Rust (`#8c2f1b`)** and **Cinnabar Wash (`#faeae6`)**: Marks active node selection rings, `hot` uncooled working artifacts, and primary interactive focus states.

### Secondary (Core-Sample Malachite — Verified Pass & `ready-for-cool` Candidate)
- **Core-Sample Malachite (`#0e6b48`)** and **Malachite Wash (`#e3f5ed`)**: Marks verified accuracy-passing candidates (`132.4 us · 1.88x`), `ready-for-cool` candidate badges, and completed stage pills.

### Tertiary (Cobalt Slate — Immutable `cooled` Baseline & Cross-Branch Reference)
- **Cobalt Slate (`#1b4d8c`)** and **Cobalt Wash (`#e5eef9`)**: Marks immutable `cooled` baseline artifacts (`cooled@v1.4`), shared cross-node resources (`summary_n3_pipe_sync@v1.md`), and cross-branch reference edges (`K-03: N3 → N4`).

### Neutral & Semantic Pigments
- **Warm Archival Paper (`#f4f1ea`)**, **Elevated Card Folio (`#fbf9f4`)**, and **Recessed Ledger Well (`#ede8dc`)**: Form the three daylight elevation tiers without drop-shadow blur.
- **Obsidian Code Ground (`#1b1916`)**: Dedicated high-contrast dark well reserved exclusively for live TileLang kernel source code and compiler/profiler logs.
- **Ochre Amber (`#9a6112` / `#fcf2e3`)** and **Iron Oxide Crimson (`#a32424` / `#fbe8e8`)**: Encode in-flight `running` evaluations and `failed` compilation/verification attempts (`C1@v1`, `C2@v1`).

**The Dual-State Orthogonal Rule.** Never merge `executionStatus` and `contentStatus` into a single badge or color channel; every node and resource card must render execution status with its geometric status icon alongside a separate content cooling badge (`hot` / `ready-for-cool` / `cooled`).

**The Verified-Only Best Kernel Rule.** Never style an unverified or in-flight node (`N5`) with the Malachite "Best Valid" badge; only nodes that have passed Stage 5 (`verify`) may enter the best valid kernel ranking.

## Typography

- **UI & Structural Hierarchy (`IBM Plex Sans` / `PingFang SC` / `Noto Sans SC`)**: Used for node titles, optimization hypotheses, stage descriptions, and comparison tables. Headings stay compact (`0.82rem`–`1.15rem`, weight `700`) so dense multi-pane inspection remains scannable above the fold.
- **Technical & Telemetry Ledger (`JetBrains Mono` / `IBM Plex Mono`)**: Required for all node IDs (`N0`–`N5`), stage attempt badges (`G2@v2←C1,C2`), latency metrics (`132.4 us`), budget counters (`16 + 1 / 24`), and SHA256 hashes, always with `font-variant-numeric: tabular-nums`.

**The Tabular Telemetry Rule.** Every numeric metric, latency delta, core utilization percentage, and version identifier must use the monospace stack with `tabular-nums` enabled so vertical comparison columns align to the pixel.

## Layout

- **Four-Zone Desktop Workbench (`>1024px`)**:
  1. **Top Task & Coordination Ledger Bar**: Fixed obsidian header (`#191714`) displaying the static prototype badge (`静态交互原型 · 演示数据`), task title, pinned baseline (`cooled@v1.4 · 248.6 us`), current best valid node (`N4 · 132.4 us · 1.88x`), global evaluation budget (`已用 16 + 预留 1 / 24`), coordination state, and shareable URL locator button.
  2. **Task Context & Breadcrumb Strip**: Full-width summary bar with optimization target, hardware environment (`Ascend 910B2 · CANN 8.0.RC3`), and active selection breadcrumb.
  3. **Left Dual-View Canvas (`1fr` Fluid)**: Synchronized toggle between the React Flow Stratigraphic Search Tree (`TreeCanvas`) and the 4-Agent Chronological Swimlane (`AgentTimeline`).
  4. **Right Inspector & File Repository (`580px` Fixed on `>1360px`, `480px` on `1024px–1360px`)**: Houses the 8-stage state machine matrix and a 2-column `.file-repo-split` (`270px` categorized file list + `1fr` sticky live code/provenance preview pane) so both the 8-stage dependency chain and live kernel code sit above the fold at `1440x960`.
- **Responsive Drawer Collapse (`<=1024px`)**: Stacks the canvas above a collapsible inspector drawer controlled by `.mobile-drawer-toggle` (`展开详情抽屉` / `收起详情抽屉`) and hides inline breadcrumb separators (`.ctx-sep`).

**The Above-the-Fold Provenance Rule.** On standard desktop viewports (`1440x960`), the Right Inspector must use the 2-column `.file-repo-split` layout so the selected node's 8-stage attempt chain and live `@vN` source preview are simultaneously visible without scrolling.

## Elevation & Depth

- **Tonal Paper Layering + Crisp Ink Rules**: Hierarchy is established through warm paper value shifts (`#ede8dc` recessed well → `#f4f1ea` canvas → `#fbf9f4` card folio) and crisp `1px solid #cfc8b8` / `1.5px solid #191714` structural borders.
- **Hard Architectural Offset Shadow**: Active or selected stratigraphic node cards use a crisp offset shadow (`3px 3px 0px rgba(25, 23, 20, 0.14)` or `3px 3px 0px rgba(140, 47, 27, 0.22)` when focused) rather than diffuse glassmorphism or neon glow.

**The No-Neon-Glow Rule.** Never use blurred neon drop shadows, dark-on-dark glassmorphism, or gradients to indicate active selection; use a `2px solid #8c2f1b` Cinnabar border paired with a hard `3px 3px 0` architectural offset shadow.

## Shapes

- **Precision Ledger Geometry (`2px`–`4px` Radius)**: Buttons, badges, stage cells, and inspector cards use `2px` (`--radius-sm`) or `4px` (`--radius-md`) corners to preserve the feel of a ruled technical folio.
- **Stratigraphic Survey Bands**: Horizontal depth strata (`STRATUM D0`, `STRATUM D1`, `STRATUM D2–D3`) span the full width of the tree canvas with `1px dashed #cfc8b8` upper/lower boundaries and dedicated inter-stratum channels for orthogonal edge routing.
- **Dual-Lineage Graph Edges**: Parent-child derivation edges render as `2px` solid Obsidian ink lines (`#191714`); cross-branch reference edges (`K-01`, `K-02`, `K-03`) render as `2px` dashed Cobalt Slate lines (`#1b4d8c`) with interactive pill labels.

## Components

### Stratigraphic Node Card (`.strat-node`)
- Compact `292px` card displaying node ID, branch label, dual status badges (`executionStatus` + `contentStatus`), short title, 3-column metric strip (latency, speedup vs. baseline, accuracy status), 8-cell stage strip (`读/思/生/编/验/测/剖/结`) with active attempt code, Ascend 910B2 `Cube/Vec/MTE2` micro-bars, agent participation pills, and an inline `展开尝试` drawer toggle.

### 8-Stage State Machine Matrix (`.stage-matrix-grid`)
- 4x2 grid of stage cards inside the Right Inspector. Each stage card lists its chronological `StageAttempt` pills (`G1@v1←P1`, `G2@v2←C1,C2`) with explicit upstream attempt lineage (`←`) and produced resource version (`@vN`). Clicking any attempt pill opens its exact input/output resource snapshot and failure/rollback diagnosis.

### Deduplicated Node File Repository & Live Preview Split (`.file-repo-split`)
- Left column (`270px`) groups all node-associated resources by category (`Kernel 内核源码`, `优化思路`, `阶段总结`, `性能与硬件剖析`, `编译与精度日志`, `任务与基线契约`), deduplicated by immutable `resourceVersionId` so a version produced by one stage and consumed by another appears once with a combined role summary (`G2 output · C3 input`) and explicit ownership badge (`独有 (v2)` vs. `共享 (源自 N3 · v1)`).
- Right column (`1fr`) renders the sticky `.resource-preview-pane` with version provenance metadata (`ID`, `版本`, `SHA256`, `来源`), stage input/output lineage badges, and safe previewers for code, structured experiment tables, and binary `msprof` pipeline summaries.

### Synchronized Agent Trajectory Swimlane (`.timeline-lane`)
- Per-agent horizontal swimlanes (`Agent-coord`, `Agent-cube`, `Agent-vec`, `Agent-eval`) plotting chronological `StageAttempt` blocks with node badges, `@vN` tags, duration readouts, and multi-agent handoff markers on shared nodes (`N0`, `N4`, `N5`).

## Do's and Don'ts

### Do:
- **Do** preserve the warm archival limestone palette (`#f4f1ea` / `#fbf9f4` / `#191714`) and high-contrast mineral accents (`#8c2f1b`, `#0e6b48`, `#1b4d8c`) across all views.
- **Do** deduplicate immutable `ResourceVersion` entries in node file lists while displaying their combined stage roles (e.g., `G2 output · C3 input`) and ownership scope (`独有` vs. `共享`).
- **Do** keep URL query parameters (`?view=...&node=...&stage=...&attempt=...&edge=...`) synchronized with every selection change so any inspection state can be shared via link.
- **Do** show explicit upstream attempt dependencies (`G1 → C1/C2 fail → G2 → C3 → V2 pass`) both in the 8-stage matrix and in the selected attempt detail box.

### Don't:
- **Don't** introduce chat boxes, prompt input bars, "Run Optimization" action buttons, or fake WebSocket streaming animations in the read-only inspection prototype.
- **Don't** conflate node/stage execution status (`completed`/`running`/`failed`/`stopped`) with resource cooling state (`hot`/`ready-for-cool`/`cooled`).
- **Don't** allow unverified or in-progress nodes (`N5`) to appear as the best valid kernel in the header or summary rankings.
- **Don't** render raw binary `.db` or `.bin` dumps in the file previewer; always render structured summary cards with `SHA256` provenance and `msprof` pipeline utilization bars.
