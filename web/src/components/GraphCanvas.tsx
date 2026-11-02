import { graphRelations, graphDepths, graphId, relationLabels } from '../graph';
import React, { useCallback, useEffect, useMemo } from 'react';
import {
  MarkerType,
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  Edge,
  EdgeLabelRenderer,
  EdgeProps,
  getSmoothStepPath,
  Handle,
  Node,
  NodeProps,
  Position,
  ReactFlow,
  useNodesState,
  useNodes,
  useEdgesState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Exploration,
  NodeRecord,
  ReferenceRecord,
  SelectionState,
  STAGE_METAS,
  StageType,
} from '../types';
import { ChevronIcon, ContentStateIcon, StatusIcon } from '../icons';

interface StratNodeData extends Record<string, unknown> {
  node: NodeRecord;
  agentNames: Record<string, string>;
  isBestValid: boolean;
  isSelected: boolean;
  selectedStageType: StageType | null;
  isExpanded: boolean;
  isDimmed: boolean;
  crossRefCount: number;
  onSelectKnowledge?: (id: string) => void;
  onSelectNode: (nodeId: string) => void;
  onSelectStage: (nodeId: string, stageType: StageType) => void;
  onToggleExpand: (nodeId: string) => void;
}

interface RefEdgeData extends Record<string, unknown> {
  reference: ReferenceRecord;
  isSelected: boolean;
  isDimmed: boolean;
  onSelectEdge: (edgeId: string) => void;
}

function layoutGraphNodes(prev: Node[]): Node[] {
  const levels = new Map<number, Node[]>();
  for (const n of prev) {
    const depth = Number(n.data.depth ?? (n.data.node as NodeRecord)?.depth ?? 0);
    levels.set(depth, [...(levels.get(depth) || []), n]);
  }
  const positions = new Map<string, {x: number; y: number}>();
  let y = 24;
  for (const depth of [...levels.keys()].sort((a, b) => a - b)) {
    const row = levels.get(depth)!;
    let x = 24;
    let height = 0;
    for (const n of row) {
      positions.set(n.id, {x, y});
      x += (n.measured?.width || 326) + 32;
      height = Math.max(height, n.measured?.height || 300);
    }
    y += height + 80;
  }
  return prev.map(n => ({...n, position: positions.get(n.id)!}));
}

const NODE_POSITIONS: Record<string, { x: number; y: number }> = {
  N0: { x: 350, y: 26 },
  N1: { x: 16, y: 348 },
  N2: { x: 350, y: 348 },
  N3: { x: 684, y: 348 },
  N4: { x: 16, y: 672 },
  N5: { x: 430, y: 692 },
};

const STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  running: '进行中',
  failed: '失败',
  stopped: '已停止',
  skipped: '已跳过',
  pending: '未开始',
};

const StratigraphicNode: React.FC<NodeProps<Node<StratNodeData>>> = ({ data }) => {
  const {
    node,
    agentNames,
    isBestValid,
    isSelected,
    selectedStageType,
    isExpanded,
    isDimmed,
    crossRefCount,
    onSelectNode,
    onSelectStage,
    onToggleExpand,
  } = data;

  return (
    <div
      className={`strat-node ${isSelected ? 'is-selected' : ''} ${isDimmed ? 'is-dimmed' : ''} status-${node.executionStatus}`}
      onClick={(e) => {
        e.stopPropagation();
        onSelectNode(node.id);
      }}
    >
      <Handle type="target" position={Position.Top} className="strat-handle" />

      <div className="strat-node-header">
        <div className="strat-node-id-group">
          <span className="strat-node-id">{node.id}</span>
          <span className="strat-branch-tag">{node.branchLabel}</span>
          {isBestValid && <span className="strat-best-badge">当前最佳有效</span>}
        </div>
        <div className="strat-node-badges">
          <span className={`exec-badge exec-${node.executionStatus}`} title="执行状态">
            <StatusIcon status={node.executionStatus} size={12} />
            <span>{STATUS_LABELS[node.executionStatus]}</span>
          </span>
          <span className={`content-badge content-${node.contentStatus}`} title="内容冷却状态">
            <ContentStateIcon status={node.contentStatus} size={11} />
            <span>{node.contentStatus}</span>
          </span>
        </div>
      </div>

      <h3 className="strat-node-title">{node.title}</h3>

      <div className="strat-node-metrics">
        {node.correctnessPassed && node.latencyUs !== undefined ? (
          <div className="metric-primary">
            <span className="metric-val">{node.latencyUs.toFixed(1)} us</span>
            {node.speedupVsBaseline && (
              <span className="metric-speedup">{node.speedupVsBaseline.toFixed(2)}x vs 基线</span>
            )}
            <span className="metric-verify-ok">精度通过</span>
          </div>
        ) : (
          <div className="metric-unverified">
            <span>{node.stopOrFailReason || '未完成正确性验证 · 不参与排名'}</span>
          </div>
        )}
      </div>

      <div className="strat-stage-strip" aria-label="节点内部 8 阶段状态机">
        {STAGE_METAS.map((meta) => {
          const st = node.stages[meta.type];
          const attemptCount = st.attempts.length;
          const latestAttempt = st.attempts[attemptCount - 1];
          const isStageActive = isSelected && selectedStageType === meta.type;
          return (
            <button
              key={meta.type}
              type="button"
              className={`stage-cell cell-${st.status} ${isStageActive ? 'cell-selected' : ''}`}
              title={`${meta.index}. ${meta.label} (${STATUS_LABELS[st.status]})${
                latestAttempt ? ` · 最新尝试 ${latestAttempt.code}` : ''
              }`}
              onClick={(e) => {
                e.stopPropagation();
                onSelectStage(node.id, meta.type);
              }}
            >
              <span className="cell-short">{meta.shortLabel}</span>
              <span className="cell-code">{latestAttempt ? latestAttempt.code : '-'}</span>
              <span className="cell-agent" title={latestAttempt ? `${agentNames[latestAttempt.agentId] || latestAttempt.agentId} · Session ${latestAttempt.agentId}` : '尚未分配执行 Agent'}>
                {latestAttempt ? latestAttempt.agentId.replace(/^agent-/, '') : '未分配'}
              </span>
            </button>
          );
        })}
      </div>

      {node.pipelineMetrics ? (
        <div className="strat-barcode-wrap" title={node.pipelineMetrics.bottleneck}>
          <div className="strat-barcode-labels">
            <span>Cube {node.pipelineMetrics.cubePct}%</span>
            <span>Vec {node.pipelineMetrics.vecPct}%</span>
            <span>MTE2 {node.pipelineMetrics.mte2Pct}%</span>
          </div>
          <div className="strat-barcode-bar">
            <div
              className="bar-seg seg-cube"
              style={{ width: `${node.pipelineMetrics.cubePct}%` }}
            />
            <div
              className="bar-seg seg-vec"
              style={{ width: `${node.pipelineMetrics.vecPct}%` }}
            />
            <div
              className="bar-seg seg-mte2"
              style={{ width: `${node.pipelineMetrics.mte2Pct}%` }}
            />
          </div>
        </div>
      ) : (
        <div className="strat-barcode-missing">未生成 msprof 流水剖析摘要</div>
      )}

      <div className="strat-node-footer">
        <div className="strat-agents-row">
          {node.agentIds.map((aid) => (
            <span key={aid} className="agent-chip">
              {agentNames[aid] || aid}
            </span>
          ))}
          {crossRefCount > 0 && (
            <span className="xref-chip">证据与引用 ×{crossRefCount}</span>
          )}
        </div>
        <button
          type="button"
          className="expand-stage-btn"
          onClick={(e) => {
            e.stopPropagation();
            onToggleExpand(node.id);
          }}
        >
          <ChevronIcon direction={isExpanded ? 'up' : 'down'} size={12} />
          <span>{isExpanded ? '收起历史' : '展开尝试'}</span>
        </button>
      </div>

      {isExpanded && (
        <div className="strat-node-expanded" onClick={(e) => e.stopPropagation()}>
          <div className="expanded-head">节点内 Stage 尝试与回退链条</div>
          {STAGE_METAS.map((meta) => {
            const st = node.stages[meta.type];
            return (
              <div
                key={meta.type}
                className="expanded-stage-row"
                onClick={() => onSelectStage(node.id, meta.type)}
              >
                <span className="exp-st-name">
                  {meta.index}. {meta.label}
                </span>
                <div className="exp-attempts">
                  {st.attempts.length > 0 ? (
                    st.attempts.map((att) => (
                      <span
                        key={att.id}
                        className={`att-pill att-${att.status}`}
                        title={`${att.summary}${
                          att.dependsOnAttemptIds.length
                            ? ` (依赖: ${att.dependsOnAttemptIds.join(', ')})`
                            : ''
                        }`}
                      >
                        <span title={`Session ${att.agentId}`}>{agentNames[att.agentId] || att.agentId}</span> · {att.code}
                        {att.kernelVersionLabel ? `@${att.kernelVersionLabel}` : ''}
                      </span>
                    ))
                  ) : (
                    <span className="att-empty">{STATUS_LABELS[st.status]}</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="strat-handle" />
    </div>
  );
};

const ReferenceEdge: React.FC<EdgeProps<Edge<RefEdgeData>>> = ({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  markerEnd,
}) => {
  const graphNodes = useNodes();
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
  });

  if (!data) return null;
  const { reference, isSelected, isDimmed, onSelectEdge } = data;
  const isCrossBranch = reference.purpose !== 'derivation';
  let safeLabelY = labelY + (isCrossBranch ? 24 : 0);
  // Keep the label outside measured solution cards, even for long crossing edges.
  for (let i = 0; i < graphNodes.length + 1; i++) {
    const obstacle = graphNodes.find(n => {
      const width = n.measured?.width || 326, height = n.measured?.height || 300;
      return labelX + 120 > n.position.x && labelX - 120 < n.position.x + width &&
        safeLabelY + 14 > n.position.y && safeLabelY - 14 < n.position.y + height;
    });
    if (!obstacle) break;
    safeLabelY = obstacle.position.y - 22;
  }


  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: isSelected
            ? '#9A3412'
            : reference.purpose === 'support' ? '#047857' : reference.purpose === 'refute' ? '#B91C1C' : isCrossBranch
            ? '#1D4ED8'
            : '#1C1917',
          strokeWidth: isSelected ? 2.8 : isCrossBranch ? 2 : 1.7,
          strokeDasharray: isCrossBranch ? '6 4' : undefined,
          opacity: isDimmed ? 0.25 : 1,
        }}
      />
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${safeLabelY}px)`,
            pointerEvents: 'all',
            zIndex: 6,
          }}
          className="nodrag nopan"
        >
          <button
            type="button"
            className={`rf-edge-pill ${isCrossBranch ? 'pill-xref' : 'pill-der'} ${
              isSelected ? 'pill-selected' : ''
            } ${isDimmed ? 'pill-dimmed' : ''}`}
            onClick={(e) => {
              e.stopPropagation();
              onSelectEdge(reference.id);
            }}
            title="点击查看关联边类型、引用知识与具体版本"
          >
            <span>
              {reference.purpose === 'cross_branch' ? '参考' : relationLabels[reference.purpose]}: {reference.sourceNodeId.replace(/^knowledge:/, '')}→
              {reference.targetNodeId.replace(/^knowledge:/, '')}
            </span>
            {reference.referencedKnowledgeId && (
              <span className="edge-k-tag">{reference.referencedKnowledgeId}</span>
            )}
          </button>
        </div>
      </EdgeLabelRenderer>
    </>
  );
};

const nodeTypes = { stratigraphic: StratigraphicNode };
const edgeTypes = { referenceEdge: ReferenceEdge };

interface GraphCanvasProps {
  exploration: Exploration;
  selection: SelectionState;
  onSelectKnowledge?: (id: string) => void;
  onSelectNode: (nodeId: string) => void;
  onSelectStage: (nodeId: string, stageType: StageType) => void;
  onSelectEdge: (edgeId: string) => void;
  onToggleExpandNode: (nodeId: string) => void;
  onToggleAllCrossRefs: () => void;
}

export const GraphCanvas: React.FC<GraphCanvasProps> = ({
  exploration,
  selection,
  onSelectKnowledge,
  onSelectNode,
  onSelectStage,
  onSelectEdge,
  onToggleExpandNode,
  onToggleAllCrossRefs,
}) => {
  const activeRelatedNodeIds = useMemo(() => {
    const set = new Set<string>();
    if (selection.edgeId) {
      const ref = graphRelations(exploration).find((r) => r.id === selection.edgeId);
      if (ref) {
        set.add(graphId(ref.sourceType,ref.sourceId));
        set.add(graphId(ref.targetType,ref.targetId));
      }
      return set;
    }
    if (!selection.nodeId) return set;

    const relations=graphRelations(exploration);
    const pending=[selection.knowledgeId ? graphId('knowledge',selection.knowledgeId) : selection.nodeId];
    while(pending.length) {
      const current=pending.pop(); if(!current || set.has(current)) continue;
      set.add(current);
      relations.filter(r=>r.kind==='derivation' && r.targetId===current).forEach(r=>pending.push(r.sourceId));
    }
    relations.forEach(r=>{
      const source=graphId(r.sourceType,r.sourceId), target=graphId(r.targetType,r.targetId);
      if(source===selection.nodeId || target===selection.nodeId || source===graphId('knowledge',selection.knowledgeId || '') || target===graphId('knowledge',selection.knowledgeId || '')) {set.add(source);set.add(target);}
    });
    exploration.references.forEach((r) => {
      if (r.sourceNodeId === selection.nodeId || r.targetNodeId === selection.nodeId) {
        set.add(r.sourceNodeId);
        set.add(r.targetNodeId);
      }
    });
    return set;
  }, [exploration, selection.nodeId, selection.edgeId]);

  const computedNodes: Node[] = useMemo(() => {
    const depths = graphDepths(exploration);
    const solutionNodes: Node<StratNodeData>[] = exploration.nodes.map((n) => {
      const pos = NODE_POSITIONS[n.id] || { x: n.depth * 260, y: n.depth * 240 };
      const crossRefCount = exploration.references.filter(
        (r) =>
          r.purpose === 'cross_branch' &&
          (r.sourceNodeId === n.id || r.targetNodeId === n.id)
      ).length;
      const hasFocus = Boolean(selection.nodeId || selection.edgeId);
      const isDimmed = hasFocus && !activeRelatedNodeIds.has(n.id);

      return {
        id: n.id,
        type: 'stratigraphic',
        position: pos,
        zIndex: 2,
        data: {
          node: {...n, depth: depths.get(n.id) || 0},
          agentNames: Object.fromEntries(exploration.agents.map(a => [a.id, a.name])),
          isBestValid: exploration.bestValidNodeId === n.id,
          isSelected: selection.nodeId === n.id && !selection.edgeId,
          selectedStageType: selection.nodeId === n.id ? selection.stageType : null,
          isExpanded: selection.expandedNodeIds.includes(n.id),
          isDimmed,
          crossRefCount,
          onSelectNode,
          onSelectStage,
          onToggleExpand: onToggleExpandNode,
        },
      };
    });

    return solutionNodes;
  }, [
    exploration,
    selection,
    activeRelatedNodeIds,
    onSelectNode,
    onSelectStage,
    onToggleExpandNode,
  ]);

  const computedEdges: Edge<RefEdgeData>[] = useMemo(() => {
    return graphRelations(exploration).filter(r=>r.sourceType==='node' && r.targetType==='node').map(r=>({id:r.id,explorationId:exploration.id,sourceNodeId:graphId(r.sourceType,r.sourceId),targetNodeId:graphId(r.targetType,r.targetId),purpose:r.kind,title:relationLabels[r.kind],rationale:r.rationale,reverifiedStatus:'pending_reverify' as const}))
      .filter((ref) => {
        if (ref.purpose === 'derivation') return true;
        if (selection.knowledgeId && [ref.sourceNodeId,ref.targetNodeId].includes(graphId('knowledge',selection.knowledgeId))) return true;
        if (selection.showAllCrossRefs) return true;
        if (selection.edgeId === ref.id) return true;
        if (
          selection.nodeId &&
          (ref.sourceNodeId === selection.nodeId || ref.targetNodeId === selection.nodeId)
        ) {
          return true;
        }
        return false;
      })
      .map((ref) => {
        const isSelected = selection.edgeId === ref.id;
        const hasFocus = Boolean(selection.nodeId || selection.edgeId);
        const isDimmed =
          hasFocus &&
          !isSelected &&
          !(
            activeRelatedNodeIds.has(ref.sourceNodeId) &&
            activeRelatedNodeIds.has(ref.targetNodeId)
          );

        return {
          id: ref.id,
          source: ref.sourceNodeId,
          target: ref.targetNodeId,
          type: 'referenceEdge',
          markerEnd: {type: MarkerType.ArrowClosed},
          animated: false,
          data: {
            reference: ref,
            isSelected,
            isDimmed,
            onSelectEdge,
          },
        };
      });
  }, [exploration, selection, activeRelatedNodeIds, onSelectEdge]);

  const [nodes, setNodes, onNodesChange] = useNodesState(computedNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(computedEdges);

  useEffect(() => {
    setNodes((prev) =>
      computedNodes.map((cn) => {
        const existing = prev.find((p) => p.id === cn.id);
        return existing ? { ...existing, ...cn, position: existing.position } : cn;
      })
    );
  }, [computedNodes, setNodes]);

  useEffect(() => {
    setEdges(computedEdges);
  }, [computedEdges, setEdges]);

  const dimensionKey = nodes.map(n => `${n.id}:${n.measured?.height || 0}:${n.measured?.width || 0}`).join('|');
  useEffect(() => {
    setNodes(prev => {
      return layoutGraphNodes(prev);
    });
  }, [dimensionKey, selection.expandedNodeIds.join('|'), setNodes]);

  const handleResetLayout = useCallback(() => {
    setNodes(prev => layoutGraphNodes(computedNodes.map(n => ({ ...n, measured: prev.find(p => p.id === n.id)?.measured }))));
  }, [computedNodes, setNodes]);

  return (
    <div className="graph-canvas-container">
      <div className="canvas-toolbar">
        <div className="canvas-toolbar-left">
          <label className="xref-toggle-label">
            <input
              type="checkbox"
              checked={selection.showAllCrossRefs}
              onChange={onToggleAllCrossRefs}
            />
            <span>证据与引用</span>
          </label>
          <button type="button" className="toolbar-btn" onClick={handleResetLayout}>
            重置布局
          </button>
        </div>
        <div className="canvas-legend" aria-label="状态与边图例">
          <span className="legend-item">
            <span className="legend-line line-der" /> 派生／融合
          </span>
          <span className="legend-item">
            <span className="legend-line line-xref" /> 参考
          </span>
          <span className="legend-item">
            <span className="legend-dot dot-cooled" /> cooled
          </span>
          <span className="legend-item">
            <span className="legend-dot dot-ready" /> ready-for-cool
          </span>
          <span className="legend-item">
            <span className="legend-dot dot-hot" /> hot
          </span>
        </div>
      </div>

      <div className="reactflow-wrapper">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.06, minZoom: 0.2 }}
          minZoom={0.2}
          maxZoom={1.5}
          proOptions={{ hideAttribution: true }}
        >
          <Background
            variant={BackgroundVariant.Lines}
            gap={32}
            size={1}
            color="#DFD9CC"
          />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
};
