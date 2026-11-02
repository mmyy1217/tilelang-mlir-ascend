import { graphDepths } from '../graph';
import React, { useEffect, useRef, useState } from 'react';
import { Exploration, STAGE_METAS } from '../types';

const SAVE_KEY = 'workbench-snapshot-v1';


// Check the required rendering contract before replacing the current workspace.
export function parseSnapshot(text: string): Exploration {
  const parsed = JSON.parse(text);
  const data = parsed.format === 'workbench-snapshot' ? parsed.exploration : parsed;
  const object = (v: unknown): v is Record<string, any> => !!v && typeof v === 'object' && !Array.isArray(v);
  const strings = (v: any, keys: string[]) => object(v) && keys.every(k => typeof v[k] === 'string');
  const array = (v: any) => Array.isArray(v);
  if (!strings(data, ['id', 'title', 'operatorName', 'targetGoal', 'environment', 'inputDomain', 'bestValidNodeId']) ||
      !object(data.budget) || !Object.values(data.budget).every(v => typeof v === 'number') ||
      !strings(data.baselineRef, ['id', 'version', 'description']) || typeof data.baselineRef.latencyUs !== 'number' ||
      !array(data.nodes) || !array(data.agents) || !array(data.references) ||
      !object(data.resources) || !array(data.resourceLinks) || !array(data.knowledgeItems) || !array(data.unfinishedDirections)) {
    throw new Error('不是有效的工作台快照，请选择由“导出快照”生成的 JSON。');
  }
  if (data.inputShapes !== undefined && (!array(data.inputShapes) || !data.inputShapes.every((shape: unknown) => typeof shape === 'string'))) throw new Error('输入 Shape 必须为字符串列表');
  for (const node of data.nodes) {
    if (!strings(node, ['id', 'title', 'hypothesis', 'changeSummary', 'derivationNote', 'branchLabel', 'executionStatus', 'contentStatus', 'currentStage']) ||
        !array(node.agentIds) || !object(node.stages) || !STAGE_METAS.every(({type}) => {
          const stage = node.stages[type];
          return strings(stage, ['stageType', 'status']) && array(stage.attempts) && stage.attempts.every((a: any) =>
            strings(a, ['id', 'code', 'nodeId', 'agentId', 'stageType', 'status', 'startedAt', 'summary']) &&
            ['dependsOnAttemptIds', 'inputResourceVersionIds', 'outputResourceVersionIds'].every(k => array(a[k])));
        })) throw new Error('节点或阶段记录不完整，未导入。');
  }
  if (!data.agents.every((a: any) => strings(a, ['id', 'name', 'role', 'status'])) ||
      !Object.values(data.resources).every(r => strings(r, ['id', 'filename', 'category', 'content', 'title', 'version'])) ||
      !data.references.every((r: any) => strings(r, ['id', 'sourceNodeId', 'targetNodeId', 'purpose', 'title', 'rationale'])) ||
      !data.resourceLinks.every((r: any) => strings(r, ['id', 'nodeId', 'resourceVersionId'])) ||
      !data.knowledgeItems.every((k: any) => strings(k, ['id', 'title', 'body', 'applicability', 'unverifiedScope']) &&
        ['supportingNodeIds', 'counterexampleNodeIds', 'evidenceAttemptIds'].every(key => array(k[key])))) {
    throw new Error('快照资源或引用记录不完整，未导入。');
  }
  if (data.graphRelations !== undefined) {
    if (!array(data.graphRelations)) throw new Error('graphRelations 必须为数组');
    for (const edge of data.graphRelations) {
      if (!strings(edge, ['id','kind','sourceType','sourceId','targetType','targetId','rationale']) || !['derivation','reference','support','refute'].includes(edge.kind)) throw new Error('图关系格式错误');
      for (const end of ['source','target']) {
        const type=edge[end+'Type'];
        if (!['node','knowledge'].includes(type) || !(type==='node'?data.nodes:data.knowledgeItems).some((item: any)=>item.id===edge[end+'Id'])) throw new Error('图关系端点缺失');
      }
      if (edge.kind==='derivation' && (edge.sourceType!=='node' || edge.targetType!=='node')) throw new Error('派生关系只能连接方案');
      if (edge.evidenceAttemptIds !== undefined && (!array(edge.evidenceAttemptIds) || !edge.evidenceAttemptIds.every((id: unknown)=>typeof id==='string'))) throw new Error('证据 ID 格式错误');
    }
  }
  for(const node of data.nodes) {
    const parents=node.parentIds ?? (node.parentId?[node.parentId]:[]);
    if(!array(parents) || !parents.every((id: unknown)=>typeof id==='string' && data.nodes.some((n:any)=>n.id===id))) throw new Error('来源方案缺失');
  }
  graphDepths(data as Exploration);
  return data as Exploration;
}

export function WorkbenchMenu({ exploration, offline, onImport, onLive }: {
  exploration: Exploration; offline: boolean; onImport: (data: Exploration) => Promise<void>; onLive: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [notice, setNotice] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    delete document.documentElement.dataset.theme;
    try { localStorage.removeItem('workbench-theme'); } catch { /* Storage may be disabled. */ }
  }, []);
  useEffect(() => {
    const dismiss = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const escape = (e: KeyboardEvent) => { if (e.key === 'Escape' && open) { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener('pointerdown', dismiss); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape); };
  }, [open]);
  const serialize = () => JSON.stringify({format: 'workbench-snapshot', version: 1, exportedAt: new Date().toISOString(), exploration}, null, 2);
  const run = async (action: () => void | Promise<void>) => { try { await action(); } catch (error) { setNotice(error instanceof Error ? error.message : '操作失败，请重试。'); } };
  return <div className="workbench-menu" ref={root}>
    <button ref={trigger} className="app-menu-trigger" aria-label="工作台菜单" aria-expanded={open} aria-controls="workbench-menu-panel" onClick={() => setOpen(v => !v)}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg>
    </button>
    <input ref={input} type="file" accept=".json,application/json" hidden onChange={async e => {
      const file = e.target.files?.[0]; e.target.value = ''; if (!file) return;
      try {
        if (file.size > 50 * 1024 * 1024) throw new Error('快照超过 50 MB，请选择较小的文件。');
        await onImport(parseSnapshot(await file.text())); setNotice(`已导入 ${file.name}（离线浏览）`);
      } catch (error) { setNotice(error instanceof Error ? error.message : '导入失败'); }
    }} />
    {open && <div id="workbench-menu-panel" className="app-menu-panel">
      <button onClick={() => input.current?.click()}>导入快照…</button>
      <button onClick={() => run(() => { localStorage.setItem(SAVE_KEY, serialize()); setNotice('已保存到当前浏览器'); })}>保存到浏览器</button>
      <button onClick={() => run(async () => { const saved = localStorage.getItem(SAVE_KEY); if (!saved) throw new Error('当前浏览器没有已保存的快照'); await onImport(parseSnapshot(saved)); setNotice('已打开本地保存（离线浏览）'); })}>打开本地保存</button>
      <button onClick={() => run(() => {
        const url = URL.createObjectURL(new Blob([serialize()], {type: 'application/json'}));
        const a = document.createElement('a'); a.href = url; a.download = `${exploration.id.replace(/[^a-zA-Z0-9_-]/g, '_')}.json`;
        a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); setNotice('已导出快照');
      })}>导出快照…</button>
      {offline && <button onClick={() => { onLive(); setNotice('已返回实时数据'); }}>返回实时数据</button>}
      <p className="menu-hint">保存与导出包含记录及内嵌文件，不包含外部文件。</p>
    </div>}
    {notice && <div className="menu-notice" role="status">{notice}<button aria-label="关闭提示" onClick={() => setNotice('')}>关闭</button></div>}
  </div>;
}
