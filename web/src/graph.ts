import { Exploration, GraphRelation } from './types';
export const relationLabels = {derivation:'派生／融合',reference:'参考',support:'支持',refute:'反驳'};
export const graphId = (type: string, id: string) => type === 'knowledge' ? `knowledge:${id}` : id;
export function graphRelations(exp: Exploration): GraphRelation[] {
  const result = [...(exp.graphRelations || [])];
  const add = (r: GraphRelation) => { if (!result.some(e => e.kind===r.kind && e.sourceType===r.sourceType && e.sourceId===r.sourceId && e.targetType===r.targetType && e.targetId===r.targetId)) result.push(r); };
  for (const r of exp.references) {
    add({id:r.id,kind:r.purpose==='derivation'?'derivation':'reference',sourceType:'node',sourceId:r.sourceNodeId,targetType:'node',targetId:r.targetNodeId,rationale:r.rationale,resourceVersionId:r.referencedResourceVersionId});
    if (r.referencedKnowledgeId && exp.knowledgeItems.some(k=>k.id===r.referencedKnowledgeId)) add({id:`knowledge:${r.id}`,kind:'reference',sourceType:'knowledge',sourceId:r.referencedKnowledgeId,targetType:'node',targetId:r.targetNodeId,rationale:r.rationale,knowledgeVersion:r.referencedKnowledgeVersion});
  }
  for (const n of exp.nodes) for (const parent of n.parentIds || (n.parentId ? [n.parentId] : [])) add({id:`legacy:${parent}:${n.id}`,kind:'derivation',sourceType:'node',sourceId:parent,targetType:'node',targetId:n.id,rationale:n.derivationNote});
  for (const k of exp.knowledgeItems) for (const kind of ['support','refute'] as const) for (const id of kind==='support'?k.supportingNodeIds:k.counterexampleNodeIds) add({id:`${kind}:${id}:${k.id}`,kind,sourceType:'node',sourceId:id,targetType:'knowledge',targetId:k.id,rationale:'知识条目中的证据关联；不等同于因果证明',knowledgeVersion:k.version,evidenceAttemptIds:k.evidenceAttemptIds});
  return result;
}
// Derivation depth is calculated from all sources, not from a legacy main parent.
export function graphDepths(exp: Exploration): Map<string, number> {
  const relations = graphRelations(exp).filter(r=>r.kind==='derivation');
  const depths = new Map<string,number>();
  const visiting = new Set<string>();
  const visit = (id: string): number => {
    if (depths.has(id)) return depths.get(id)!;
    if (visiting.has(id)) throw new Error('方案派生关系存在环，请修正导入数据');
    visiting.add(id);
    const parents=relations.filter(r=>r.targetId===id);
    const value=parents.length?Math.max(...parents.map(r=>visit(r.sourceId)))+1:0;
    visiting.delete(id);depths.set(id,value);return value;
  };
  exp.nodes.forEach(n=>visit(n.id));return depths;
}
