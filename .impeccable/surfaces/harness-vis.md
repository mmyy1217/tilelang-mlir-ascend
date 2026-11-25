# Harness Vis Single-Line Outliner & Search-Knowledge Workbench

Mode: Operate. Target: web/src/HarnessApp.tsx, web/src/components/TrajectoryWorkbench.tsx, web/src/components/SearchAndKnowledgeInspector.tsx, web/src/searchTree.ts, web/src/harness.css, web/src/harnessAdapter.ts.

Visual authority: incumbent warm archival paper palette (`--bg-survey`, `--bg-sheet`, `--bg-stratum`, `--ink-primary`, `--ink-secondary`, `--accent-vermilion`, `--state-jade`, `--state-crimson`, `--state-amber`, Cobalt `#1D4ED8`) and typography from `DESIGN.md` and `styles.css`. Legacy mode (`/?legacy=1`) preserves the original `App` untouched.

Architecture (`HarnessApp`):
- Single compact top bar showing project title (`PROJECT.md`), concise environment/shape spec, current Search Node focus pill (`当前节点 N6`), baseline/best candidate pills, and snapshot controls (`跟随最新`, refresh, deep-link share).
- Unified left workbench (`TrajectoryWorkbench`, with all secondary view tabs removed for maximum clarity):
  - Column I (Left): Single-line-per-row expandable folder/file outliner for the Search Tree (`.workspace/search/nodes/` `N0..N6` connected by 1px CSS geometric tree lines, with leaf indicators vs parent chevrons, primary candidate outcome badge, and per-node `runs/N` folder drawer expanding `runs/A_i, D_i, T_i` and leaf files `TASK.md`, `RESULT.md`, `CANDIDATE.md`, `measurement.json`) plus `.workspace/` governance & contract files (`STATE.md`, `search/tree.json`, `decisions/review-1.md`, `measurement-inputs/`).
  - Center Gutter: Dynamic SVG cubic Bezier relational arrows connecting `Cooled Knowledge ──► Search Node` (`use-cooled` in Cobalt/Amber) and `Search Node ──► Hot Knowledge` (`hot.nodes` in Vermilion).
  - Column II (Center): Cooled (`knowledge/cooled/*.json`) & Hot (`.workspace/knowledge/hot/**`) Knowledge Ledger with revision counts, promotion links (`已冷却 → H2--review-1`), and node bindings.
- Right pane (`SearchAndKnowledgeInspector`): Unified inspector for Search Nodes (including candidate scorecard and inline run artifacts), Hot Knowledge (with revision switcher and inline `.snapshot` evidence reader), Cooled Knowledge (with provenance, review delivery markdown, and `use-cooled` table), and raw workspace files.
