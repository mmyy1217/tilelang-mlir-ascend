import React from 'react';
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react';
import type { HarnessSearchView } from '../types';
import type { TrajectorySelection } from './TrajectoryWorkbench';

interface Item {
  id: string;
  title: string;
  meta: string;
  text: string;
  level: number;
  selection: TrajectorySelection;
}

export function HarnessDag({ search, selection, onSelect, onMove, register }: {
  search: HarnessSearchView;
  onMove: () => void;
  register: (id: string, el: HTMLElement | null) => void;
  selection: TrajectorySelection;
  onSelect: (value: TrajectorySelection) => void;
}) {
  const items: Item[] = [];
  const edges: Edge[] = [];
  const known = new Set(search.searchNodes.map(node => node.id));
  function connect(source: string, target: string, knowledge = false) {
    const color = knowledge ? '#a14c30' : '#697675';
    edges.push({ id: JSON.stringify([source, target]), source, target,
      style: { stroke: color, strokeWidth: 1.6, strokeDasharray: knowledge ? '5 3' : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, color } });
  }
  for (const node of search.searchNodes) {
    const status = node.status === 'parked' ? '搁置' : node.status === 'closed' ? '关闭' : node.isCurrent ? '当前' : '开放';
    items.push({ id: 'node/'+node.id, title: node.id, meta: [node.candidateIds.join(' · '), status].filter(Boolean).join(' · '),
      text: node.hypothesis, level: node.depth * 2, selection: { kind: 'search-node', searchNodeId: node.id } });
    for (const run of node.runs) {
      const runKey = 'run/' + run.id;
      items.push({ id: runKey, title: run.id, meta: '实验记录',
        text: run.operation || run.stage, level: node.depth * 2 + 1,
        selection: { kind: 'search-node', searchNodeId: node.id, runId: run.id } });
      connect('node/' + node.id, runKey);
    }
    if (node.parentId && known.has(node.parentId)) connect('node/'+node.parentId, 'node/'+node.id);
  }
  for (const link of search.crossLinks || []) connect('node/'+link.source, 'node/'+link.target, true);
  if (!items.length) return <p className="hv-empty-state">此快照没有可绘制的搜索节点或知识记录。</p>;
  const rows = new Map<number, Item[]>();
  for (const item of items) {
    if (!rows.has(item.level)) rows.set(item.level, []);
    rows.get(item.level)!.push(item);
  }
  const selected = selection.kind === 'hot' ? 'hot/'+selection.hotId :
    selection.kind === 'cooled' ? 'cold/'+selection.cooledKey : selection.runId ? 'run/'+selection.runId : 'node/'+selection.searchNodeId;
  const nodes: Node[] = Array.from(rows).flatMap(([level,row]) => row.map((item,index) => ({
    id: item.id, type: 'default',
    position: { x: (index-(row.length-1)/2)*265, y: level*166 },
    className: 'hv-dag-card' + (selected === item.id ? ' is-selected' : ''),
    data: { selection: item.selection, label: <button ref={el => { if (!item.selection.runId) register(item.title, el); }} className="nodrag nopan" type="button" title={item.text}>
      <span><b>{item.title}</b><small>{item.meta}</small></span><strong>{item.text}</strong>
    </button> },
  })));
  return <div className="hv-dag" aria-label="优化路径 DAG">
    <ReactFlow onNodeClick={(_, node) => onSelect(node.data.selection as TrajectorySelection)} onMove={onMove} nodes={nodes} edges={edges} fitView fitViewOptions={{padding: 0.16, maxZoom: 1.2}}
      minZoom={0.08} nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}>
      <Background gap={18} size={1} color="#c9c7be" /><Controls showInteractive={false} />
    </ReactFlow>
    <div className="hv-dag-key"><span>{search.searchNodes.length} 个策略 · {items.length - search.searchNodes.length} 条实验记录</span>{Boolean(search.crossLinks?.length) && <span>跨分支引用</span>}</div>
  </div>;
}
