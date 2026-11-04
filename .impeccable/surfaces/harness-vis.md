# Harness Vis adaptation of the existing workbench

Mode: Operate. Target: web/src/App.tsx, components/*, harnessAdapter.ts.

User-pinned authority: original agent-vis PRODUCT.md, DESIGN.md, styles.css and components. Preserve the exact incumbent shell: compact coordination bar, collapsible project list, adjustable split pane, exploration graph / Agent timeline tabs, stratigraphic node cards with expandable stage records, right file repository. No new dashboard, overview page, headline section or alternative visual system. HarnessApp is transport only and renders the existing App.

Add only snapshot history and refresh controls in the existing toolbar, source-aware resource links, Run-specific stage metadata, and honest unknown states. Snapshot-derived Agent notes use the existing summary inspector. Keep underlying capture and publish pipeline independent of source workspace. Verify both original exploration and Harness snapshots in the shared components.

The main graph preserves the original candidate exploration semantics: one node per candidate identity, edges only from parent_candidate. Analysis, development, and test Runs are grouped inside each candidate. Repeated tests do not grow the tree. Project/unlinked work stays in a separate toolbar entry; never draw workflow order as candidate derivation. Preserve the existing visual language while correcting this information architecture.
