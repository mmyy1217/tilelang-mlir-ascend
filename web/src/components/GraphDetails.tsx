import { Exploration, SelectionState } from '../types';
import { graphRelations, relationLabels } from '../graph';
import { MarkdownContent } from './MarkdownContent';
export function GraphDetails({exploration, selection, onNode, onKnowledge, onEdge}: {exploration: Exploration;selection: SelectionState;onNode:(id:string)=>void;onKnowledge:(id:string)=>void;onEdge:(id:string|null)=>void}) {
  const relations=graphRelations(exploration);
  const edge=relations.find(r=>r.id===selection.edgeId);
  const knowledge=exploration.knowledgeItems.find(k=>k.id===selection.knowledgeId);
  const jump=(type:string,id:string)=>type==='node'?onNode(id):onKnowledge(id);
  if (!edge && !knowledge) return null;
  return <aside className="right-inspector"><div className="inspector-tabs-bar"><strong>{edge?'关系与证据来源':'知识与经验来源'}</strong><button className="toolbar-btn" onClick={()=>onNode(selection.nodeId || exploration.nodes[0]?.id)}>返回方案</button></div><div className="inspector-scroll">
    {edge && <section><h2 className="sheet-title">{relationLabels[edge.kind]}</h2><p className="graph-endpoints"><button onClick={()=>jump(edge.sourceType,edge.sourceId)}>{edge.sourceType==='node'?'方案':'知识'} {edge.sourceId}</button> → <button onClick={()=>jump(edge.targetType,edge.targetId)}>{edge.targetType==='node'?'方案':'知识'} {edge.targetId}</button></p><MarkdownContent content={edge.rationale || '尚未记录具体贡献说明'} /><p>知识版本：{edge.knowledgeVersion || '未关联'}</p><p>资源版本：{edge.resourceVersionId || '未关联'}</p><p>证据 Attempt：{edge.evidenceAttemptIds?.join(', ') || '未关联'}</p>{edge.resourceVersionId && exploration.resources[edge.resourceVersionId] && <details><summary>查看证据内容</summary><MarkdownContent content={exploration.resources[edge.resourceVersionId].content}/></details>}<p className="task-panel-note">关系记录描述引用或证据主张，不自动证明因果，也不继承正确性。</p></section>}
    {!edge && knowledge && <section><h2 className="sheet-title">{knowledge.title}</h2><p>{knowledge.id} · {knowledge.version} · {knowledge.claimType} · {knowledge.contentStatus}</p><MarkdownContent content={knowledge.body}/><p>适用条件：{knowledge.applicability}</p><p>未验证范围：{knowledge.unverifiedScope}</p><h3>来源与使用</h3>{relations.filter(r=>(r.sourceType==='knowledge'&&r.sourceId===knowledge.id)||(r.targetType==='knowledge'&&r.targetId===knowledge.id)).map(r=><button className="graph-relation-row" key={r.id} onClick={()=>onEdge(r.id)}>{relationLabels[r.kind]} · {r.sourceId} → {r.targetId}<small>{r.rationale}</small></button>)}</section>}
  </div></aside>;
}
