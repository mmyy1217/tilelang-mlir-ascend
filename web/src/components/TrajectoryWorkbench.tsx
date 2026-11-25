import { HarnessDag } from './HarnessDag';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type {
  CooledKnowledgeView,
  Exploration,
  HarnessSearchView,
  HotKnowledgeView,
  NodeRecord,
  SearchNodeView,
  SearchRunEntry,
} from '../types';

export interface TrajectorySelection {
  kind: 'search-node' | 'candidate' | 'hot' | 'cooled' | 'file';
  searchNodeId?: string;
  candidateId?: string;
  runId?: string;
  hotId?: string;
  cooledKey?: string;
  filePath?: string;
}

interface ArrowSegment {
  id: string;
  kind: 'cooled-to-node' | 'node-to-hot' | 'cross-link';
  sourceKey: string;
  targetKey: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  status?: string;
  isHighlighted: boolean;
  isDimmed: boolean;
}

const STAGE_SHORT: Record<string, string> = {
  analysis: '分析',
  develop: '开发',
  'device-test': '测试',
  'prepare-environment': '环境',
  'establish-baseline': '基线',
};

const KIND_LABELS: Record<string, string> = {
  observation: '观察',
  hypothesis: '假设',
  conclusion: '结论',
};

const COOLED_STATUS_META: Record<
  string,
  { label: string; tone: 'ok' | 'warn' | 'neutral' }
> = {
  active: { label: 'ACTIVE', tone: 'ok' },
  needs_revalidation: { label: '待复核', tone: 'warn' },
  superseded: { label: '已替代', tone: 'neutral' },
};

const NODE_STATUS_META: Record<
  string,
  { label: string; tone: 'ok' | 'warn' | 'neutral' }
> = {
  open: { label: '探索中', tone: 'ok' },
  parked: { label: '已搁置', tone: 'warn' },
  closed: { label: '已结项', tone: 'neutral' },
};

const FolderIcon: React.FC<{ open?: boolean; accent?: boolean }> = ({ open, accent }) => (
  <svg
    width="14"
    height="14"
    viewBox="0 0 16 16"
    fill="none"
    className={`outliner-icon ${accent ? 'is-accent' : ''}`}
    aria-hidden="true"
  >
    {open ? (
      <path
        d="M1.5 3.5A1 1 0 0 1 2.5 2.5H6.2L7.7 4H13.5A1 1 0 0 1 14.5 5V6H3.2L1.5 12.5V3.5ZM2.2 13L3.8 7H15L13.4 13H2.2Z"
        fill="currentColor"
      />
    ) : (
      <path
        d="M1.5 3.5A1 1 0 0 1 2.5 2.5H6.2L7.7 4H13.5A1 1 0 0 1 14.5 5V12.5A1 1 0 0 1 13.5 13.5H2.5A1 1 0 0 1 1.5 12.5V3.5Z"
        fill="currentColor"
      />
    )}
  </svg>
);

const FileIcon: React.FC<{ json?: boolean }> = ({ json }) => (
  <svg
    width="13"
    height="13"
    viewBox="0 0 16 16"
    fill="none"
    className={`outliner-icon is-file ${json ? 'is-json' : ''}`}
    aria-hidden="true"
  >
    <path
      d="M3.5 1.5H9.8L13.5 5.2V13.5A1 1 0 0 1 12.5 14.5H3.5A1 1 0 0 1 2.5 13.5V2.5A1 1 0 0 1 3.5 1.5Z"
      stroke="currentColor"
      strokeWidth="1.3"
    />
    <path d="M9.5 1.5V5.5H13.5" stroke="currentColor" strokeWidth="1.3" />
  </svg>
);

export const TrajectoryWorkbench: React.FC<{
  dag: boolean;
  exploration: Exploration;
  search: HarnessSearchView;
  selection: TrajectorySelection;
  onSelect: (sel: TrajectorySelection) => void;
}> = ({ exploration, search, selection, onSelect, dag }) => {
  // Single unified set of expanded folder keys:
  // - "node:N0" .. "node:N6" (Search Node folders — all expanded by default)
  // - "runs:N6" (The `runs/` subfolder inside a Search Node — expanded for current node by default)
  // - "run:D6", "run:T6-repeat" (Individual run folders inside `runs/`)
  // - "ws:root", "ws:search", "ws:inputs" (Workspace folders)
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    for (const n of search.searchNodes) {
      initial.add(`node:${n.id}`);
    }
    if (search.currentNodeId) {
      initial.add(`runs:${search.currentNodeId}`);
    }
    initial.add('ws:root');
    initial.add('ws:search');
    return initial;
  });

  const [hoveredEntity, setHoveredEntity] = useState<{
    type: 'node' | 'hot' | 'cooled';
    id: string;
  } | null>(null);

  const expansionProject = exploration.harness ? exploration.id.split(':').slice(0, 2).join(':') : exploration.id;
  const initializedProject = useRef(expansionProject);
  useEffect(() => {
    if (initializedProject.current === expansionProject) return;
    initializedProject.current = expansionProject;
    const next = new Set<string>();
    for (const n of search.searchNodes) {
      next.add(`node:${n.id}`);
    }
    if (search.currentNodeId) {
      next.add(`runs:${search.currentNodeId}`);
    }
    next.add('ws:root');
    next.add('ws:search');
    setExpandedFolders(next);
  }, [expansionProject, search.currentNodeId, search.searchNodes]);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const treeScrollRef = useRef<HTMLDivElement | null>(null);
  const knowledgeScrollRef = useRef<HTMLDivElement | null>(null);
  const nodeRowRefs = useRef(new Map<string, HTMLElement>());
  const hotRowRefs = useRef(new Map<string, HTMLElement>());
  const cooledRowRefs = useRef(new Map<string, HTMLElement>());

  const [arrows, setArrows] = useState<ArrowSegment[]>([]);
  const [svgSize, setSvgSize] = useState<{ width: number; height: number }>({
    width: 800,
    height: 600,
  });

  const nodesById = useMemo(() => {
    const m = new Map<string, SearchNodeView>();
    for (const n of search.searchNodes) m.set(n.id, n);
    return m;
  }, [search.searchNodes]);

  const candidatesById = useMemo(() => {
    const m = new Map<string, NodeRecord>();
    for (const n of exploration.nodes) {
      if (n.harness?.kind === 'candidate') m.set(n.id, n);
    }
    return m;
  }, [exploration.nodes]);

  const rootNodes = useMemo(
    () => search.searchNodes.filter((n) => !n.parentId || !nodesById.has(n.parentId)),
    [search.searchNodes, nodesById]
  );

  const activeEntity = useMemo(() => {
    if (hoveredEntity) return hoveredEntity;
    if (
      (selection.kind === 'search-node' ||
        selection.kind === 'candidate' ||
        selection.kind === 'file') &&
      selection.searchNodeId
    ) {
      return { type: 'node' as const, id: selection.searchNodeId };
    }
    if (selection.kind === 'hot' && selection.hotId) {
      return { type: 'hot' as const, id: selection.hotId };
    }
    if (selection.kind === 'cooled' && selection.cooledKey) {
      return { type: 'cooled' as const, id: selection.cooledKey };
    }
    return null;
  }, [hoveredEntity, selection]);

  const findVisibleNodeRow = useCallback(
    (nodeId: string): HTMLElement | undefined => {
      let cursor: string | null = nodeId;
      while (cursor) {
        const el = nodeRowRefs.current.get(cursor);
        if (el) return el;
        cursor = nodesById.get(cursor)?.parentId || null;
      }
      return undefined;
    },
    [nodesById]
  );

  const recomputeArrows = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const cRect = container.getBoundingClientRect();
    if (cRect.width === 0 || cRect.height === 0) return;

    setSvgSize({ width: cRect.width, height: cRect.height });
    const nextArrows: ArrowSegment[] = [];

    // 1. Cooled Knowledge -> Search Node (use-cooled)
    for (const cooled of search.cooledKnowledge) {
      const cooledEl = cooledRowRefs.current.get(cooled.fullKey);
      if (!cooledEl) continue;
      const kRect = cooledEl.getBoundingClientRect();
      const x1 = kRect.left - cRect.left;
      const y1 = kRect.top - cRect.top + kRect.height * 0.42;

      for (const usage of cooled.usedByNodes) {
        const nodeEl = findVisibleNodeRow(usage.searchNodeId);
        if (!nodeEl) continue;
        const nRect = nodeEl.getBoundingClientRect();
        const x2 = nRect.right - cRect.left;
        const y2 = nRect.top - cRect.top + nRect.height * 0.45;

        if ((y1 < 34 && y2 < 34) || (y1 > cRect.height - 6 && y2 > cRect.height - 6)) {
          continue;
        }

        const isHighlighted =
          activeEntity?.type === 'cooled'
            ? activeEntity.id === cooled.fullKey
            : activeEntity?.type === 'node'
              ? activeEntity.id === usage.searchNodeId
              : false;
        const isDimmed = Boolean(activeEntity && !isHighlighted);

        nextArrows.push({
          id: `cooled:${cooled.fullKey}->${usage.searchNodeId}`,
          kind: 'cooled-to-node',
          sourceKey: cooled.fullKey,
          targetKey: usage.searchNodeId,
          x1,
          y1,
          x2,
          y2,
          status: cooled.status,
          isHighlighted,
          isDimmed,
        });
      }
    }

    // 2. Search Node -> Hot Knowledge (hot.nodes)
    for (const hot of search.hotKnowledge) {
      const hotEl = hotRowRefs.current.get(hot.id);
      if (!hotEl) continue;
      const hRect = hotEl.getBoundingClientRect();
      const x2 = hRect.left - cRect.left;
      const y2 = hRect.top - cRect.top + hRect.height * 0.5;

      for (const sNodeId of hot.latest.nodes) {
        const nodeEl = findVisibleNodeRow(sNodeId);
        if (!nodeEl) continue;
        const nRect = nodeEl.getBoundingClientRect();
        const x1 = nRect.right - cRect.left;
        const y1 = nRect.top - cRect.top + nRect.height * 0.58;

        if ((y1 < 34 && y2 < 34) || (y1 > cRect.height - 6 && y2 > cRect.height - 6)) {
          continue;
        }

        const isHighlighted =
          activeEntity?.type === 'hot'
            ? activeEntity.id === hot.id
            : activeEntity?.type === 'node'
              ? activeEntity.id === sNodeId
              : false;
        const isDimmed = Boolean(activeEntity && !isHighlighted);

        nextArrows.push({
          id: `hot:${sNodeId}->${hot.id}`,
          kind: 'node-to-hot',
          sourceKey: sNodeId,
          targetKey: hot.id,
          x1,
          y1,
          x2,
          y2,
          isHighlighted,
          isDimmed,
        });
      }
    }

    if (!dag) {
      for (const { source, target } of search.crossLinks || []) {
        const from = nodeRowRefs.current.get(source)?.getBoundingClientRect();
        const to = nodeRowRefs.current.get(target)?.getBoundingClientRect();
        if (!from || !to) continue;
        const isHighlighted = activeEntity?.type === 'node' && [source, target].includes(activeEntity.id);
        nextArrows.push({ id: JSON.stringify([source, target]), kind: 'cross-link', sourceKey: source, targetKey: target,
          x1: from.right - cRect.left, y1: from.top + from.height/2 - cRect.top,
          x2: to.right - cRect.left, y2: to.top + to.height/2 - cRect.top,
          isHighlighted, isDimmed: Boolean(activeEntity && !isHighlighted) });
      }
    }
    setArrows(nextArrows);
  }, [dag, search.crossLinks, search.cooledKnowledge, search.hotKnowledge, findVisibleNodeRow, activeEntity]);

  useEffect(() => {
    const frame = requestAnimationFrame(recomputeArrows);
    return () => cancelAnimationFrame(frame);
  }, [recomputeArrows, expandedFolders, selection]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const ro = new ResizeObserver(() => {
      requestAnimationFrame(recomputeArrows);
    });
    ro.observe(container);
    if (treeScrollRef.current) ro.observe(treeScrollRef.current);
    if (knowledgeScrollRef.current) ro.observe(knowledgeScrollRef.current);
    window.addEventListener('resize', recomputeArrows);
    return () => {
      ro.disconnect();
      window.removeEventListener('resize', recomputeArrows);
    };
  }, [recomputeArrows]);

  const workspaceGroups = useMemo(() => {
    const rootFiles = [
      { path: 'STATE.md', desc: '当前进展' },
      { path: 'REQUIREMENTS.md', desc: '任务契约' },
      { path: 'BASELINE.md', desc: '测量基线 B0' },
      { path: 'ENVIRONMENT.md', desc: '运行环境' },
      { path: 'PROJECT.md', desc: '项目说明' },
      { path: 'MEASUREMENTS.json', desc: '统一测量索引' },
    ].filter((f) => exploration.resources[f.path]);

    const searchAndReview = [
      { path: 'search/tree.json', desc: '搜索树与知识索引' },
      { path: 'decisions/review-1.md', desc: '冷却评审决议' },
    ].filter((f) => exploration.resources[f.path]);

    const measurementInputs = [
      { path: 'measurement-inputs/cases.json', desc: '测试算例集' },
      { path: 'measurement-inputs/timing.json', desc: 'kernel-only 协议' },
      { path: 'measurement-inputs/end-to-end.json', desc: '端到端协议' },
      { path: 'measurement-inputs/reference.md', desc: 'FP32 参考定义' },
    ].filter((f) => exploration.resources[f.path]);

    return { rootFiles, searchAndReview, measurementInputs };
  }, [exploration.resources]);

  const toggleFolder = (key: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleExpandAll = () => {
    const all = new Set<string>(['ws:root', 'ws:search', 'ws:inputs']);
    for (const n of search.searchNodes) {
      all.add(`node:${n.id}`);
      all.add(`runs:${n.id}`);
      for (const r of n.runs) all.add(`run:${r.id}`);
    }
    setExpandedFolders(all);
  };

  const handleDefaultOutline = () => {
    const next = new Set<string>(['ws:root', 'ws:search']);
    for (const n of search.searchNodes) {
      next.add(`node:${n.id}`);
    }
    if (search.currentNodeId) {
      next.add(`runs:${search.currentNodeId}`);
    }
    setExpandedFolders(next);
  };

  const locateSearchNode = (id: string) => {
    setExpandedFolders(previous => {
      const next = new Set(previous);
      const seen = new Set<string>();
      let parent = nodesById.get(id)?.parentId;
      while (parent && !seen.has(parent)) {
        seen.add(parent);
        next.add(`node:${parent}`);
        parent = nodesById.get(parent)?.parentId;
      }
      return next;
    });
    onSelect({kind:'search-node',searchNodeId:id});
    requestAnimationFrame(() => requestAnimationFrame(() => {
      nodeRowRefs.current.get(id)?.scrollIntoView({block:'nearest',inline:'nearest'});
    }));
  };

  const renderSearchNodeFolder = (node: SearchNodeView): React.ReactNode => {
    const children = node.childrenIds
      .map((cid) => nodesById.get(cid))
      .filter((c): c is SearchNodeView => Boolean(c));
    const hasChildren = children.length > 0;
    const nodeKey = `node:${node.id}`;
    const runsKey = `runs:${node.id}`;
    const isNodeOpen = expandedFolders.has(nodeKey);
    const isRunsOpen = expandedFolders.has(runsKey);

    const isNodeSelected =
      (selection.kind === 'search-node' || selection.kind === 'candidate') &&
      selection.searchNodeId === node.id &&
      !selection.runId;

    const isLinked =
      (activeEntity?.type === 'hot' && node.hotKnowledgeIds.includes(activeEntity.id)) ||
      (activeEntity?.type === 'cooled' && node.usedCooledIds.includes(activeEntity.id));

    const statusMeta = NODE_STATUS_META[node.status] || NODE_STATUS_META.open;
    const nodeCandidates = node.candidateIds
      .map((cid) => candidatesById.get(cid))
      .filter((c): c is NodeRecord => Boolean(c));
    const primaryCand =
      nodeCandidates.find((c) => c.id === exploration.bestValidNodeId) ||
      nodeCandidates.find((c) => c.speedupVsBaseline != null) ||
      nodeCandidates[nodeCandidates.length - 1];

    return (
      <div key={node.id} className="outliner-node-group">
        {/* Single-Line Search Node Row */}
        <div
          ref={(el) => {
            if (el) nodeRowRefs.current.set(node.id, el);
            else nodeRowRefs.current.delete(node.id);
          }}
          className={`outliner-row row-search-node ${isNodeSelected ? 'is-selected' : ''} ${
            isLinked ? 'is-linked' : ''
          } ${node.isCurrent ? 'is-current' : ''}`}
          onMouseEnter={() => setHoveredEntity({ type: 'node', id: node.id })}
          onMouseLeave={() => setHoveredEntity(null)}
          onClick={() =>
            onSelect({
              kind: 'search-node',
              searchNodeId: node.id,
              candidateId: node.candidateIds[node.candidateIds.length - 1],
            })
          }
          title={`${node.id} (${statusMeta.label})：${node.hypothesis}${
            node.scope ? `\n范围：${node.scope}` : ''
          }`}
        >
          {hasChildren ? (
            <button
              type="button"
              className="outliner-chevron"
              onClick={(e) => toggleFolder(nodeKey, e)}
              aria-label={isNodeOpen ? `收起 ${node.id} 子节点` : `展开 ${node.id} 子节点`}
              title={isNodeOpen ? '收起子搜索节点' : '展开子搜索节点'}
            >
              {isNodeOpen ? '▾' : '▸'}
            </button>
          ) : (
            <span className="outliner-leaf-dot" aria-hidden="true" />
          )}

          <span className={`outliner-node-id ${node.isCurrent ? 'is-current' : ''}`}>
            {node.id}
          </span>

          <span className="outliner-row-title">{node.hypothesis}</span>

          <div className="outliner-row-meta tabular-nums">
            {primaryCand ? (
              <button
                type="button"
                className={`outliner-cand-badge ${
                  exploration.bestValidNodeId === primaryCand.id
                    ? 'tone-best'
                    : primaryCand.executionStatus === 'failed'
                      ? 'tone-fail'
                      : primaryCand.speedupVsBaseline
                        ? 'tone-ok'
                        : 'tone-warn'
                }`}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect({
                    kind: 'candidate',
                    searchNodeId: node.id,
                    candidateId: primaryCand.id,
                  });
                }}
                title={
                  nodeCandidates.length > 1
                    ? `包含候选：${nodeCandidates.map((c) => c.id).join(', ')}`
                    : undefined
                }
              >
                {primaryCand.id}{' '}
                {primaryCand.speedupVsBaseline
                  ? `${primaryCand.speedupVsBaseline.toFixed(2)}×${
                      exploration.bestValidNodeId === primaryCand.id ? ' ★' : ''
                    }`
                  : primaryCand.executionStatus === 'failed'
                    ? '失败'
                    : '不可比'}
              </button>
            ) : (
              <span
                className={`outliner-status-badge tone-${
                  node.isCurrent ? 'current' : statusMeta.tone
                }`}
              >
                {node.isCurrent ? '当前' : statusMeta.label}
              </span>
            )}

            {node.runs.length > 0 && (
              <button
                type="button"
                className={`outliner-runs-btn ${isRunsOpen ? 'is-open' : ''}`}
                onClick={(e) => toggleFolder(runsKey, e)}
                title={
                  isRunsOpen
                    ? `收起 ${node.id} 的 runs/ 任务文件夹 (${node.runs
                        .map((r) => r.id)
                        .join(' · ')})`
                    : `展开 ${node.id} 的 runs/ 任务文件夹 (${node.runs
                        .map((r) => r.id)
                        .join(' · ')})`
                }
              >
                <FolderIcon open={isRunsOpen} />
                <span>{node.runs.length}</span>
                <span className="outliner-runs-caret">{isRunsOpen ? '▾' : '▸'}</span>
              </button>
            )}
          </div>
        </div>

        {/* Optional Recessed Runs & Files Drawer for this Search Node */}
        {isRunsOpen && node.runs.length > 0 && (
          <div className="outliner-runs-drawer">
            {node.runs.map((run: SearchRunEntry) => {
              const runFolderKey = `run:${run.id}`;
              const isRunOpen = expandedFolders.has(runFolderKey);
              const isRunSelected =
                selection.searchNodeId === node.id &&
                selection.runId === run.id &&
                selection.kind !== 'file';

              return (
                <div key={run.id} className="outliner-run-group">
                  <div
                    className={`outliner-row row-run ${
                      isRunSelected ? 'is-selected' : ''
                    }`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect({
                        kind: 'search-node',
                        searchNodeId: node.id,
                        candidateId: run.candidateId || node.candidateIds[0],
                        runId: run.id,
                      });
                    }}
                  >
                    <button type="button" className="outliner-chevron"
                      aria-label={`${isRunOpen ? '收起' : '展开'} ${run.id} 文件`}
                      onClick={e => toggleFolder(runFolderKey, e)}>{isRunOpen ? '▾' : '▸'}</button>
                    <FolderIcon open={isRunOpen} />
                    <span className="outliner-folder-name">runs/{run.id}/</span>
                    <span className="outliner-stage-pill">
                      {STAGE_SHORT[run.stage] || run.stage}
                    </span>
                    <span className="outliner-row-title">{run.operation}</span>
                  </div>

                  {isRunOpen && (
                    <div className="outliner-children">
                      {run.files.map((filePath) => {
                        const baseName = filePath.split('/').pop() || filePath;
                        const isFileSelected =
                          selection.kind === 'file' && selection.filePath === filePath;
                        return (
                          <button
                            key={filePath}
                            type="button"
                            className={`outliner-row row-file ${
                              isFileSelected ? 'is-selected' : ''
                            }`}
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelect({
                                kind: 'file',
                                searchNodeId: node.id,
                                candidateId: run.candidateId || undefined,
                                runId: run.id,
                                filePath,
                              });
                            }}
                          >
                            <span className="outliner-chevron-spacer" />
                            <FileIcon json={baseName.endsWith('.json')} />
                            <span className="outliner-file-name">{baseName}</span>
                            <span className="outliner-file-tag">
                              {baseName === 'CANDIDATE.md'
                                ? '候选清单'
                                : baseName === 'RESULT.md'
                                  ? '结果报告'
                                  : baseName === 'TASK.md'
                                    ? '任务输入'
                                    : '测量数据'}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Stepped Child Search Nodes Container */}
        {hasChildren && isNodeOpen && (
          <div className="outliner-tree-children">
            {children.map((child) => (
              <div key={child.id} className="outliner-tree-branch">
                {renderSearchNodeFolder(child)}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className={dag ? 'traj-workbench traj-with-dag' : 'traj-workbench'} ref={containerRef}>
      {/* SVG Relational Arrow Overlay across the Gutter */}
      <svg
        className="traj-svg-overlay"
        width={svgSize.width}
        height={svgSize.height}
        viewBox={`0 0 ${svgSize.width} ${svgSize.height}`}
        aria-hidden="true"
      >
        <defs>
          <marker id="traj-arrow-cross" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,1 L7,4 L0,7 Z" fill="#334155" />
          </marker>
          <marker
            id="traj-arrow-cooled"
            markerWidth="8"
            markerHeight="8"
            refX="7"
            refY="4"
            orient="auto"
          >
            <path d="M0,1 L7,4 L0,7 Z" fill="#1D4ED8" />
          </marker>
          <marker
            id="traj-arrow-cooled-warn"
            markerWidth="8"
            markerHeight="8"
            refX="7"
            refY="4"
            orient="auto"
          >
            <path d="M0,1 L7,4 L0,7 Z" fill="#B45309" />
          </marker>
          <marker
            id="traj-arrow-hot"
            markerWidth="8"
            markerHeight="8"
            refX="7"
            refY="4"
            orient="auto"
          >
            <path d="M0,1 L7,4 L0,7 Z" fill="#9A3412" />
          </marker>
        </defs>

        {arrows.map((arrow) => {
          const dx = Math.max(24, Math.abs(arrow.x2 - arrow.x1) * 0.45);
          const cross = arrow.kind === 'cross-link';
          const path = cross
            ? `M ${arrow.x1} ${arrow.y1} C ${arrow.x1 + 40} ${arrow.y1}, ${arrow.x2 + 40} ${arrow.y2}, ${arrow.x2 + 2} ${arrow.y2}`
            :
            arrow.kind === 'cooled-to-node'
              ? `M ${arrow.x1} ${arrow.y1} C ${arrow.x1 - dx} ${arrow.y1}, ${arrow.x2 + dx} ${arrow.y2}, ${arrow.x2 + 2} ${arrow.y2}`
              : `M ${arrow.x1} ${arrow.y1} C ${arrow.x1 + dx} ${arrow.y1}, ${arrow.x2 - dx} ${arrow.y2}, ${arrow.x2 - 2} ${arrow.y2}`;

          const isWarnCooled =
            arrow.kind === 'cooled-to-node' && arrow.status === 'needs_revalidation';
          const stroke = cross ? "#334155" :
            arrow.kind === 'cooled-to-node'
              ? isWarnCooled
                ? '#B45309'
                : '#1D4ED8'
              : '#9A3412';
          const markerEnd = cross ? "url(#traj-arrow-cross)" :
            arrow.kind === 'cooled-to-node'
              ? isWarnCooled
                ? 'url(#traj-arrow-cooled-warn)'
                : 'url(#traj-arrow-cooled)'
              : 'url(#traj-arrow-hot)';

          return (
            <g
              key={arrow.id}
              className={`traj-arrow-group ${arrow.isHighlighted ? 'is-highlighted' : ''} ${
                arrow.isDimmed ? 'is-dimmed' : ''
              }`}
            >
              <path
                d={path}
                fill="none"
                stroke={stroke}
                strokeWidth={arrow.isHighlighted ? 2.2 : 1.45}
                strokeDasharray={isWarnCooled || cross ? '4 3' : 'none'}
                markerEnd={markerEnd}
              />
              <circle cx={arrow.x1} cy={arrow.y1} r={2.3} fill={stroke} />
            </g>
          );
        })}
      </svg>

      {/* Column I: Single-Line Folder & File Outliner */}
      {dag ? <HarnessDag key={exploration.harness?.snapshotId} search={search} selection={selection} onSelect={onSelect}
        onMove={recomputeArrows} register={(id, el) => {
          if (el) nodeRowRefs.current.set(id, el);
          else nodeRowRefs.current.delete(id);
        }} /> : <div
        className="traj-tree-col"
        ref={treeScrollRef}
        onScroll={recomputeArrows}
      >
        <div className="traj-col-header">
          <div className="traj-col-title-row">
            <h2 className="traj-col-heading">文件与搜索树大纲</h2>
            <span className="traj-col-meta tabular-nums">
              {search.searchNodes.length} 节点 · {Object.keys(exploration.resources).length} 文件
            </span>
          </div>
          <div className="traj-col-actions">
            <button type="button" className="traj-mini-btn" onClick={handleExpandAll}>
              展开全部
            </button>
            <button type="button" className="traj-mini-btn" onClick={handleDefaultOutline}>
              收起任务目录
            </button>
          </div>
        </div>

        <div className="outliner-scroll-body" role="tree" aria-label="工作区与搜索树文件大纲">
          {/* Search tree and run records */}
          <div className="outliner-root-section">
            <div className="outliner-root-banner">
              <FolderIcon open accent />
              <strong>搜索树与执行记录</strong>
              <button type="button" className="hv-link-chip" onClick={() => onSelect({kind:'file',filePath:'search/tree.json'})}>.workspace/search/tree.json</button>
              <span>.workspace/runs/ · 按搜索节点组织</span>
            </div>

            <div className="outliner-root-items">
              {rootNodes.map((root) => renderSearchNodeFolder(root))}
            </div>
          </div>

          {/* Root Folder 2: .workspace/ Project & Contract Files */}
          <div className="outliner-root-section">
            <div
              className="outliner-root-banner is-clickable"
              onClick={() => toggleFolder('ws:root')}
            >
              <span className="outliner-chevron">
                {expandedFolders.has('ws:root') ? '▾' : '▸'}
              </span>
              <FolderIcon open={expandedFolders.has('ws:root')} />
              <strong>.workspace/</strong>
              <span>项目契约、搜索树索引与评审决议</span>
            </div>

            {expandedFolders.has('ws:root') && (
              <div className="outliner-children">
                {workspaceGroups.rootFiles.map((f) => {
                  const isSelected =
                    selection.kind === 'file' && selection.filePath === f.path;
                  return (
                    <button
                      key={f.path}
                      type="button"
                      className={`outliner-row row-file ${isSelected ? 'is-selected' : ''}`}
                      onClick={() => onSelect({ kind: 'file', filePath: f.path })}
                    >
                      <span className="outliner-chevron-spacer" />
                      <FileIcon json={f.path.endsWith('.json')} />
                      <span className="outliner-file-name">{f.path}</span>
                      <span className="outliner-file-tag">{f.desc}</span>
                    </button>
                  );
                })}

                {workspaceGroups.searchAndReview.map((f) => {
                  const isSelected =
                    selection.kind === 'file' && selection.filePath === f.path;
                  return (
                    <button
                      key={f.path}
                      type="button"
                      className={`outliner-row row-file ${isSelected ? 'is-selected' : ''}`}
                      onClick={() => onSelect({ kind: 'file', filePath: f.path })}
                    >
                      <span className="outliner-chevron-spacer" />
                      <FileIcon json={f.path.endsWith('.json')} />
                      <span className="outliner-file-name">{f.path}</span>
                      <span className="outliner-file-tag">{f.desc}</span>
                    </button>
                  );
                })}

                {/* Subfolder: measurement-inputs/ */}
                {workspaceGroups.measurementInputs.length > 0 && (
                  <div className="outliner-subfolder-group">
                    <div
                      className="outliner-row row-subfolder"
                      onClick={() => toggleFolder('ws:inputs')}
                    >
                      <span className="outliner-chevron">
                        {expandedFolders.has('ws:inputs') ? '▾' : '▸'}
                      </span>
                      <FolderIcon open={expandedFolders.has('ws:inputs')} />
                      <span className="outliner-folder-name">measurement-inputs/</span>
                      <span className="outliner-folder-summary">
                        测试算例与计时协议 ({workspaceGroups.measurementInputs.length})
                      </span>
                    </div>

                    {expandedFolders.has('ws:inputs') && (
                      <div className="outliner-children">
                        {workspaceGroups.measurementInputs.map((f) => {
                          const baseName = f.path.split('/').pop() || f.path;
                          const isSelected =
                            selection.kind === 'file' && selection.filePath === f.path;
                          return (
                            <button
                              key={f.path}
                              type="button"
                              className={`outliner-row row-file ${
                                isSelected ? 'is-selected' : ''
                              }`}
                              onClick={() => onSelect({ kind: 'file', filePath: f.path })}
                            >
                              <span className="outliner-chevron-spacer" />
                              <FileIcon json={baseName.endsWith('.json')} />
                              <span className="outliner-file-name">{baseName}</span>
                              <span className="outliner-file-tag">{f.desc}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>}

      {/* Center Relational Arrow Gutter */}
      <div className="traj-gutter-col" aria-hidden="true">
        <div className="traj-gutter-header">关联</div>
      </div>

      {/* Column II: Cooled & Hot Knowledge Ledger */}
      <div
        className="traj-knowledge-col"
        ref={knowledgeScrollRef}
        onScroll={recomputeArrows}
      >
        <div className="traj-col-header">
          <div className="traj-col-title-row">
            <h2 className="traj-col-heading">知识账本 · Cooled & Hot</h2>
            <span className="traj-col-meta tabular-nums">
              {search.cooledKnowledge.length} 冷却 · {search.hotKnowledge.length} 热知识
            </span>
          </div>
          <div className="traj-legend-strip">
            <span className="traj-legend-item legend-cooled">──► 引用冷却知识</span>
            <span className="traj-legend-item legend-hot">──► 沉淀热知识</span>
          </div>
        </div>

        <div className="traj-knowledge-body">
          {/* Section A: Cooled Knowledge (knowledge/cooled/) */}
          <div className="traj-k-group">
            <div className="traj-k-group-header">
              <strong>冷却知识 · knowledge/cooled/</strong>
              <span>跨任务长期复用（须经评审）</span>
            </div>

            {search.cooledKnowledge.length === 0 ? (
              <div className="traj-k-empty">当前快照尚无冷却知识条目</div>
            ) : (
              search.cooledKnowledge.map((cooled: CooledKnowledgeView) => {
                const isSelected =
                  selection.kind === 'cooled' && selection.cooledKey === cooled.fullKey;
                const isLinked =
                  activeEntity?.type === 'node' &&
                  cooled.usedByNodes.some((u) => u.searchNodeId === activeEntity.id);
                const statusMeta =
                  COOLED_STATUS_META[cooled.status] || COOLED_STATUS_META.active;

                return (
                  <div
                    key={cooled.fullKey}
                    ref={(el) => {
                      if (el) cooledRowRefs.current.set(cooled.fullKey, el);
                      else cooledRowRefs.current.delete(cooled.fullKey);
                    }}
                    className={`traj-k-card kind-cooled ${isSelected ? 'is-selected' : ''} ${
                      isLinked ? 'is-linked' : ''
                    }`}
                    onMouseEnter={() =>
                      setHoveredEntity({ type: 'cooled', id: cooled.fullKey })
                    }
                    onMouseLeave={() => setHoveredEntity(null)}
                    onClick={() => onSelect({ kind: 'cooled', cooledKey: cooled.fullKey })}
                  >
                    <div className="traj-k-top">
                      <div className="traj-k-id-row">
                        <span className="traj-k-id cooled-id">{cooled.fullKey}</span>
                        <span className="traj-k-kind">
                          {KIND_LABELS[cooled.kind] || cooled.kind}
                        </span>
                      </div>
                      <span className={`traj-k-status tone-${statusMeta.tone}`}>
                        {statusMeta.label}
                      </span>
                    </div>

                    <p className="traj-k-statement">{cooled.statement}</p>

                    <div className="traj-k-footer">
                      {cooled.usedByNodes.length > 0 && (
                        <details className="traj-k-bindings" onClick={e => e.stopPropagation()}>
                          <summary>关联 {new Set(cooled.usedByNodes.map(u => u.searchNodeId)).size} 个节点</summary>
                          {cooled.usedByNodes.map((u) => (
                            <button
                              key={u.searchNodeId}
                              type="button"
                              className="traj-node-link-pill"
                              onClick={(e) => {
                                e.stopPropagation();
                                locateSearchNode(u.searchNodeId);
                              }}
                              title={u.reason}
                            >
                              → {u.searchNodeId}
                            </button>
                          ))}
                        </details>
                      )}

                      {cooled.provenance?.hotId && (
                        <span className="traj-k-prov">
                          源自 {cooled.provenance.hotId} rev.{cooled.provenance.revision}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Section B: Hot Knowledge (.workspace/knowledge/hot/) */}
          <div className="traj-k-group">
            <div className="traj-k-group-header">
              <strong>工作区热知识 · .workspace/knowledge/hot/</strong>
              <span>探索过程沉淀（带不可变证据快照）</span>
            </div>

            {search.hotKnowledge.length === 0 ? (
              <div className="traj-k-empty">当前快照尚无热知识条目</div>
            ) : (
              search.hotKnowledge.map((hot: HotKnowledgeView) => {
                const isSelected = selection.kind === 'hot' && selection.hotId === hot.id;
                const isLinked =
                  activeEntity?.type === 'node' &&
                  hot.latest.nodes.includes(activeEntity.id);

                return (
                  <div
                    key={hot.id}
                    ref={(el) => {
                      if (el) hotRowRefs.current.set(hot.id, el);
                      else hotRowRefs.current.delete(hot.id);
                    }}
                    className={`traj-k-card kind-hot ${isSelected ? 'is-selected' : ''} ${
                      isLinked ? 'is-linked' : ''
                    }`}
                    onMouseEnter={() => setHoveredEntity({ type: 'hot', id: hot.id })}
                    onMouseLeave={() => setHoveredEntity(null)}
                    onClick={() => onSelect({ kind: 'hot', hotId: hot.id })}
                  >
                    <div className="traj-k-top">
                      <div className="traj-k-id-row">
                        <span className="traj-k-id hot-id">{hot.id}</span>
                        <span className="traj-k-rev tabular-nums">
                          rev.{hot.latest.revision}
                          {hot.revisions.length > 1 ? ` (${hot.revisions.length}版)` : ''}
                        </span>
                        <span className="traj-k-kind">
                          {KIND_LABELS[hot.latest.kind] || hot.latest.kind}
                        </span>
                      </div>
                      {hot.promotedCooledId ? (
                        <button
                          type="button"
                          className="traj-promoted-pill"
                          onClick={(e) => {
                            e.stopPropagation();
                            onSelect({
                              kind: 'cooled',
                              cooledKey: hot.promotedCooledId,
                            });
                          }}
                          title="已通过评审冷却为长期知识，点击查看"
                        >
                          已冷却 → {hot.promotedCooledId}
                        </button>
                      ) : (
                        <span className="traj-k-status tone-hot">HOT</span>
                      )}
                    </div>

                    <p className="traj-k-statement">{hot.latest.statement}</p>

                    <div className="traj-k-footer">
                      <details className="traj-k-bindings" onClick={e => e.stopPropagation()}>
                        <summary>关联 {hot.latest.nodes.length} 个节点</summary>
                        {hot.latest.nodes.map((nId) => (
                          <button
                            key={nId}
                            type="button"
                            className="traj-node-link-pill"
                            onClick={(e) => {
                              e.stopPropagation();
                              locateSearchNode(nId);
                            }}
                          >
                            ← {nId}
                          </button>
                        ))}
                      </details>
                      <span className="traj-k-prov tabular-nums">
                        {hot.latest.evidence.length} 份快照证据
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
