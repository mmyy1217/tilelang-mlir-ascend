import {SyntheticDistribution} from './SyntheticDistribution';
import { recordedStatus, stageStatus } from '../harnessAdapter';
import { snapshotReference } from '../harnessLinks';
import { graphRelations, relationLabels } from '../graph';
import { MarkdownContent } from './MarkdownContent';
import React, { useEffect, useMemo, useState } from 'react';
import {
  Exploration,
  NodeRecord,
  ResourceCategory,
  ResourceLink,
  ResourceVersion,
  SelectionState,
  STAGE_METAS,
  StageType,
} from '../types';
import { CategoryIcon, ContentStateIcon, StatusIcon } from '../icons';

interface RightInspectorProps {
  exploration: Exploration;
  selection: SelectionState;
  onSelectNode: (nodeId: string) => void;
  onSelectStage: (nodeId: string, stageType: StageType | null) => void;
  onSelectAttempt: (nodeId: string, stageType: StageType, attemptId: string) => void;
  onSelectEdge: (edgeId: string | null) => void;
  onChangeTab: (tab: 'inspector' | 'compare' | 'candidates') => void;
  onChangeCompareNode: (compareNodeId: string) => void;
}

const STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  running: '进行中',
  failed: '失败',
  stopped: '已停止',
  skipped: '已跳过',
  pending: '未开始',
};

const CATEGORY_ORDER: { key: ResourceCategory; label: string }[] = [
  { key: 'kernel', label: 'Kernel 内核源码' },
  { key: 'idea', label: '优化思路 (Idea)' },
  { key: 'summary', label: '阶段总结 (Summary)' },
  { key: 'experiment', label: '实验测量记录 (Experiment)' },
  { key: 'profile', label: '硬件剖析文件 (Profile)' },
  { key: 'artifact', label: '基准契约与其他产物 (Artifact)' },
];

export const RightInspector: React.FC<RightInspectorProps> = ({
  exploration,
  selection,
  onSelectNode,
  onSelectStage,
  onSelectAttempt,
  onSelectEdge,
  onChangeTab,
  onChangeCompareNode,
}) => {
  const activeNode: NodeRecord = useMemo(() => {
    const found = exploration.nodes.find((n) => n.id === selection.nodeId);
    return found || exploration.nodes.find((n) => n.id === exploration.bestValidNodeId) || exploration.nodes[0];
  }, [exploration, selection.nodeId]);

  const activeEdge = useMemo(() => {
    if (!selection.edgeId) return null;
    return exploration.references.find((r) => r.id === selection.edgeId) || null;
  }, [exploration.references, selection.edgeId]);

  const compareNode: NodeRecord = useMemo(() => {
    const found = exploration.nodes.find((n) => n.id === selection.compareNodeId);
    return found || exploration.nodes[0];
  }, [exploration.nodes, selection.compareNodeId]);

  const nodeResourceLinks = useMemo(() => {
    const allForNode = exploration.resourceLinks.filter((l) => l.nodeId === activeNode.id);
    if (!selection.stageType) return allForNode;
    return allForNode.filter((l) => l.stageType === selection.stageType);
  }, [exploration.resourceLinks, activeNode.id, selection.stageType]);

  const [selectedResourceId, setSelectedResourceId] = useState<string | null>(null);

  useEffect(() => {
    if (nodeResourceLinks.length > 0) {
      if (
        !selection.stageType &&
        activeNode.primaryKernelVersionId &&
        nodeResourceLinks.some(
          (l) => l.resourceVersionId === activeNode.primaryKernelVersionId
        )
      ) {
        setSelectedResourceId(activeNode.primaryKernelVersionId);
        return;
      }
      if (exploration.harness && !selection.stageType && activeNode.harness?.manifestPath) {
        setSelectedResourceId(activeNode.harness.manifestPath);
        return;
      }
      const selectedAttemptLinks = selection.attemptId ? nodeResourceLinks.filter(l=>l.attemptId===selection.attemptId) : [];
      const preferred = selectedAttemptLinks.find(l=>l.role==='output') || selectedAttemptLinks[0] ||
        nodeResourceLinks.find((l) => l.role === 'output') || nodeResourceLinks[0];
      setSelectedResourceId(preferred.resourceVersionId);
    } else {
      const anyForNode = exploration.resourceLinks.find((l) => l.nodeId === activeNode.id);
      setSelectedResourceId(anyForNode ? anyForNode.resourceVersionId : null);
    }
  }, [
    activeNode.id,
    activeNode.primaryKernelVersionId,
    selection.stageType,
    selection.attemptId,
    nodeResourceLinks,
    exploration.resourceLinks,
  ]);

  const activeResource: ResourceVersion | null = useMemo(() => {
    if (!selectedResourceId) return null;
    return exploration.resources[selectedResourceId] || null;
  }, [exploration.resources, selectedResourceId]);

  const otherReferencingNodeIds = useMemo(() => {
    if (!activeResource) return [];
    const ids = new Set<string>();
    exploration.resourceLinks.forEach((lnk) => {
      if (lnk.resourceVersionId === activeResource.id) {
        ids.add(lnk.nodeId);
      }
    });
    return Array.from(ids);
  }, [exploration.resourceLinks, activeResource]);

  const nodeDerivationRef = useMemo(() => {
    return exploration.references.find(
      (r) => r.targetNodeId === activeNode.id && r.purpose === 'derivation'
    );
  }, [exploration.references, activeNode.id]);

  const nodeCrossRefs = useMemo(() => {
    return exploration.references.filter(
      (r) =>
        r.purpose === 'cross_branch' &&
        (r.sourceNodeId === activeNode.id || r.targetNodeId === activeNode.id)
    );
  }, [exploration.references, activeNode.id]);

  return (
    <aside className="right-inspector" aria-label="右侧节点状态机与文件库">
      <div className="inspector-tabs-bar">
        <div className="inspector-tabs">
          <button
            type="button"
            className={`insp-tab ${selection.rightTab === 'inspector' ? 'active' : ''}`}
            onClick={() => onChangeTab('inspector')}
          >
            {activeEdge ? '关联边与引用依据' : `节点 ${activeNode.id} 状态机与文件库`}
          </button>
          <button
            type="button"
            className={`insp-tab ${selection.rightTab === 'compare' ? 'active' : ''}`}
            onClick={() => {
              onSelectEdge(null);
              onChangeTab('compare');
            }}
          >
            节点对比 ({activeNode.id} vs {compareNode.id})
          </button>
          <button
            type="button"
            className={`insp-tab ${selection.rightTab === 'candidates' ? 'active' : ''}`}
            onClick={() => {
              onSelectEdge(null);
              onChangeTab('candidates');
            }}
          >
            {exploration.harness ? 'Agent 观察' : '总结与待冷却候选'}
          </button>
        </div>
      </div>

      {selection.rightTab === 'inspector' && activeEdge && (
        <div className="inspector-scroll">
          <section className="edge-detail-sheet">
            <div className="sheet-header-row">
              <h2 className="sheet-title">{activeEdge.title}</h2>
              <button
                type="button"
                className="minor-action-btn"
                onClick={() => onSelectEdge(null)}
              >
                返回节点 {activeEdge.targetNodeId} 文件库
              </button>
            </div>

            <div className="edge-meta-grid">
              <div className="meta-cell">
                <span className="meta-label">关联关系类型</span>
                <strong className="meta-val">
                  {activeEdge.purpose === 'cross_branch'
                    ? '参考边 (Cross-Branch Reference · 不增加父节点)'
                    : '父子派生边 (Parent-Child Derivation)'}
                </strong>
              </div>
              <div className="meta-cell">
                <span className="meta-label">来源节点 → 目标节点</span>
                <div className="meta-val-links">
                  <button
                    type="button"
                    className="node-jump-btn"
                    onClick={() => onSelectNode(activeEdge.sourceNodeId)}
                  >
                    定位来源 {activeEdge.sourceNodeId}
                  </button>
                  <span>→</span>
                  <button
                    type="button"
                    className="node-jump-btn"
                    onClick={() => onSelectNode(activeEdge.targetNodeId)}
                  >
                    定位目标 {activeEdge.targetNodeId}
                  </button>
                </div>
              </div>
              <div className="meta-cell">
                <span className="meta-label">关联引用知识版本</span>
                <strong className="meta-val">
                  {activeEdge.referencedKnowledgeId
                    ? `${activeEdge.referencedKnowledgeId} (${activeEdge.referencedKnowledgeVersion})`
                    : '未绑定独立知识条目（直接依据实验剖析记录派生）'}
                </strong>
              </div>
              <div className="meta-cell">
                <span className="meta-label">目标方案重验证状态</span>
                <strong className="meta-val">
                  {activeEdge.reverifiedStatus === 'verified'
                    ? `已在目标节点 Attempt ${activeEdge.reverifiedInAttemptId} 重新验证通过`
                    : activeEdge.reverifiedStatus === 'failed_reverify'
                    ? '目标节点验证未通过（保留为反例）'
                    : '目标节点正在验证中 (pending_reverify)'}
                </strong>
              </div>
            </div>

            <div className="rationale-block">
              <h3 className="sub-heading">决策依据与改动说明</h3>
              <p>{activeEdge.rationale}</p>
            </div>

            {activeEdge.referencedResourceVersionId &&
              exploration.resources[activeEdge.referencedResourceVersionId] && (
                <div className="edge-ref-resource">
                  <h3 className="sub-heading">引用的具体证据文件版本</h3>
                  <div className="resource-meta-strip">
                    <strong>
                      {
                        exploration.resources[activeEdge.referencedResourceVersionId]
                          .filename
                      }
                    </strong>
                    <span>
                      版本:{' '}
                      {
                        exploration.resources[activeEdge.referencedResourceVersionId]
                          .version
                      }
                    </span>
                    <span>
                      SHA256:{' '}
                      {
                        exploration.resources[activeEdge.referencedResourceVersionId]
                          .sha256
                      }
                    </span>
                    <span>
                      来源节点:{' '}
                      {
                        exploration.resources[activeEdge.referencedResourceVersionId]
                          .ownerNodeId
                      }
                    </span>
                  </div>
                  <pre className="preview-code-box">
                    {
                      exploration.resources[activeEdge.referencedResourceVersionId]
                        .content
                    }
                  </pre>
                </div>
              )}
          </section>
        </div>
      )}

      {selection.rightTab === 'inspector' && !activeEdge && (
        <div className="inspector-scroll">
          <section className="node-overview-sheet">
            <div className="sheet-header-row">
              <h2 className="sheet-title">{activeNode.title}</h2>
              <div className="sheet-badges">
                <span className={`exec-badge exec-${activeNode.executionStatus}`}>
                  <StatusIcon status={activeNode.executionStatus} size={12} />
                  <span>{activeNode.harness ? recordedStatus[activeNode.harness.recordedStatus] || activeNode.harness.recordedStatus : STATUS_LABELS[activeNode.executionStatus]}</span>
                </span>
                {!activeNode.harness && <span className={`content-badge content-${activeNode.contentStatus}`}>
                  <ContentStateIcon status={activeNode.contentStatus} size={12} />
                  <span>{activeNode.contentStatus}</span>
                </span>}
              </div>
            </div>

            <p className="node-hypothesis">{activeNode.changeSummary}</p>

            <div className="node-lineage-bar">
              <span>
                {exploration.harness ? '父候选：' : '来源方案：'}{' '}
                {graphRelations(exploration).filter(r=>r.kind==='derivation' && r.targetId===activeNode.id).map(r=><button key={r.id} className="inline-link-btn" onClick={()=>onSelectNode(r.sourceId)}>{r.sourceId}</button>)}
                {!graphRelations(exploration).some(r=>r.kind==='derivation' && r.targetId===activeNode.id) && '无（独立起点）'}
              </span>
              {nodeDerivationRef && (
                <button
                  type="button"
                  className="inline-link-btn"
                  onClick={() => onSelectEdge(nodeDerivationRef.id)}
                >
                  查看来源贡献 ({nodeDerivationRef.sourceNodeId}→{nodeDerivationRef.targetNodeId})
                </button>
              )}
              {nodeCrossRefs.map((xref) => (
                <button
                  key={xref.id}
                  type="button"
                  className="inline-link-btn xref-btn"
                  onClick={() => onSelectEdge(xref.id)}
                >
                  参考: {xref.sourceNodeId}→{xref.targetNodeId} (
                  {xref.referencedKnowledgeId || '实验结论'})
                </button>
              ))}
            </div>
          </section>

          <section className="node-knowledge-section">
            <details>
              <summary>基础知识与优化经验 · {exploration.knowledgeItems.filter(k => graphRelations(exploration).some(r =>
                (r.sourceType==='node' && r.sourceId===activeNode.id && r.targetType==='knowledge' && r.targetId===k.id) ||
                (r.targetType==='node' && r.targetId===activeNode.id && r.sourceType==='knowledge' && r.sourceId===k.id))).length}</summary>
              {exploration.knowledgeItems.filter(k => graphRelations(exploration).some(r =>
                (r.sourceType==='node' && r.sourceId===activeNode.id && r.targetType==='knowledge' && r.targetId===k.id) ||
                (r.targetType==='node' && r.targetId===activeNode.id && r.sourceType==='knowledge' && r.sourceId===k.id))).map(k => <article className="node-knowledge-entry" key={k.id}>
                  <strong>{k.title}</strong><small>{k.id} · {k.version} · {k.claimType}</small>
                  <MarkdownContent content={k.body}/>
                  <p>适用条件：{k.applicability}</p><p>未验证范围：{k.unverifiedScope}</p>
                  {graphRelations(exploration).filter(r => (r.sourceType==='knowledge'&&r.sourceId===k.id)||(r.targetType==='knowledge'&&r.targetId===k.id)).map(r=><button className="graph-relation-row" key={r.id} onClick={()=>onSelectEdge(r.id)}>{relationLabels[r.kind]} · {r.sourceId} → {r.targetId}</button>)}
                </article>)}
            </details>
          </section>

          <section className="stage-machine-section">
            <div className="sec-head-row">
              <h3 className="sub-heading">
                {exploration.harness ? '关联 Run 与原始记录' : '节点内部 8 阶段 Stage 状态机与 Attempt 依赖链'}
              </h3>
              {selection.stageType && (
                <button
                  type="button"
                  className="minor-action-btn"
                  onClick={() => onSelectStage(activeNode.id, null)}
                >
                  显示节点 {activeNode.id} 全部阶段与资源
                </button>
              )}
            </div>

            {activeNode.harness?.manifestPath && <button className="toolbar-btn" onClick={()=>setSelectedResourceId(activeNode.harness!.manifestPath!)}>查看候选清单 · {activeNode.id}</button>}
            <div className="stage-matrix-grid">
              {(activeNode.stageMetas || STAGE_METAS).map((meta) => {
                const st = activeNode.stages[meta.type];
                const isSelected = selection.stageType === meta.type;
                return (
                  <div
                    key={meta.type}
                    className={`stage-matrix-card status-${st.status} ${
                      isSelected ? 'is-selected' : ''
                    }`}
                    onClick={() =>
                      onSelectStage(
                        activeNode.id,
                        isSelected ? null : meta.type
                      )
                    }
                  >
                    <div className="smc-top">
                      <span className="smc-index">
                        {meta.index}. {meta.label}
                      </span>
                      <span className={`exec-badge exec-${st.status}`}>
                        <StatusIcon status={st.status} size={11} />
                        <span>{activeNode.harness ? stageStatus(st) : STATUS_LABELS[st.status]}</span>
                      </span>
                    </div>

                    <div className="smc-attempts">
                      {st.attempts.length > 0 ? (
                        st.attempts.map((att) => (
                          <button
                            key={att.id}
                            type="button"
                            className={`smc-att-chip att-${att.status} ${
                              selection.attemptId === att.id ? 'att-active' : ''
                            }`}
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectAttempt(activeNode.id, meta.type, att.id);
                            }}
                            title={exploration.harness ? `${att.summary} · ${recordedStatus[att.recordedStatus || 'unknown'] || att.recordedStatus}` : att.summary}
                          >
                            <strong>{att.code}</strong>
                            {att.kernelVersionLabel ? `@${att.kernelVersionLabel}` : ''}
                            {att.dependsOnAttemptIds.length > 0 && (
                              <span className="att-dep">
                                ←{att.dependsOnAttemptIds.map((d) => exploration.harness ? d : d.split('-')[1]).join(',')}
                              </span>
                            )}
                          </button>
                        ))
                      ) : (
                        <span className="smc-no-att">无执行尝试 ({activeNode.harness ? stageStatus(st) : STATUS_LABELS[st.status]})</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {selection.stageType && (
              <div className="selected-stage-detail">
                {(() => {
                  const st = activeNode.stages[selection.stageType];
                  const meta = (activeNode.stageMetas || STAGE_METAS).find((m) => m.type === selection.stageType);
                  return (
                    <>
                      <div className="ssd-head">
                        <strong>
                          Stage {meta?.index}. {meta?.label} — 执行尝试历史
                        </strong>
                        <span>{meta?.description}</span>
                      </div>
                      {st.attempts.length > 0 ? (
                        <div className="ssd-attempt-list">
                          {st.attempts.map((att) => (
                            <div key={att.id} className="ssd-attempt-row">
                              <div className="ssd-att-main">
                                <strong>
                                  Attempt {att.code}{' '}
                                  {att.kernelVersionLabel ? `(对应内核 ${att.kernelVersionLabel})` : ''}
                                </strong>
                                <span className="ssd-att-agent">执行者: {att.agentId}</span>
                                <span className="ssd-att-time">
                                  {att.startedAt} {att.endedAt ? `→ ${att.endedAt}` : exploration.harness ? '（记录时间）' : '(运行中)'}
                                </span>
                                <span className={`exec-badge exec-${att.status}`}>
                                  {exploration.harness ? recordedStatus[att.recordedStatus || 'unknown'] || att.recordedStatus : STATUS_LABELS[att.status]}
                                </span>
                              </div>
                              <p className="ssd-att-desc">{att.summary}</p>
                              <div className="ssd-att-deps">
                                {att.dependsOnAttemptIds.length > 0 && (
                                  <span>上游 Attempt 依赖: {att.dependsOnAttemptIds.join(' → ')}</span>
                                )}
                                {att.reusedFromAttemptId && (
                                  <span>代码未变复用验证结果: {att.reusedFromAttemptId}</span>
                                )}
                                {att.failureOrSkipReason && (
                                  <span className="ssd-fail-note">
                                    失败/回退原因: {att.failureOrSkipReason}
                                  </span>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <div className="ssd-missing-box">
                          <strong>未生成该阶段尝试记录：</strong>
                          <span>{st.missingOutputReason || '该阶段尚未启动或已被跳过'}</span>
                        </div>
                      )}
                    </>
                  );
                })()}
              </div>
            )}
          </section>

          <section className="file-repo-section">
            <div className="sec-head-row">
              <h3 className="sub-heading">
                节点 {activeNode.id} 文件库{' '}
                {selection.stageType
                  ? `(已定位至 Stage: ${
                      (activeNode.stageMetas || STAGE_METAS).find((m) => m.type === selection.stageType)?.label
                    })`
                  : '(全部关联资源与共享引用)'}
              </h3>
              <span className="repo-count-hint">
                区分节点独有文件与跨节点共享引用 · 同一共享版本保持全局一致
              </span>
            </div>

            <div className="file-repo-split">
              <div className="file-repo-categories">
                {CATEGORY_ORDER.map((cat) => {
                  const rawLinksInCat = nodeResourceLinks.filter((lnk) => {
                    const res = exploration.resources[lnk.resourceVersionId];
                    return res && res.category === cat.key;
                  });

                  // Deduplicate by immutable resourceVersionId while preserving all roles/attempts
                  const dedupMap = new Map<
                    string,
                    { primaryLink: ResourceLink; roleSummary: string }
                  >();
                  rawLinksInCat.forEach((lnk) => {
                    const existing = dedupMap.get(lnk.resourceVersionId);
                    const attemptShort = lnk.attemptId
                      ? (exploration.harness ? lnk.attemptId : lnk.attemptId.split('-')[1])
                      : '';
                    const badgeText = attemptShort
                      ? `${attemptShort} ${lnk.role}`
                      : lnk.role;
                    if (!existing) {
                      dedupMap.set(lnk.resourceVersionId, {
                        primaryLink: lnk,
                        roleSummary: badgeText,
                      });
                    } else if (!existing.roleSummary.includes(badgeText)) {
                      existing.roleSummary = `${existing.roleSummary} · ${badgeText}`;
                    }
                  });
                  const dedupedEntries = Array.from(dedupMap.values());

                  const isMissingProfile =
                    !exploration.harness && cat.key === 'profile' &&
                    dedupedEntries.length === 0 &&
                    activeNode.stages.profiling.status !== 'completed';
                  const isMissingExp =
                    !exploration.harness && cat.key === 'experiment' &&
                    dedupedEntries.length === 0 &&
                    activeNode.stages.bench_perf.status !== 'completed';

                  if (dedupedEntries.length === 0 && !isMissingProfile && !isMissingExp) {
                    return null;
                  }

                  return (
                    <div key={cat.key} className="repo-category-group">
                      <div className="repo-cat-header">
                        <CategoryIcon category={cat.key} size={13} />
                        <span>{cat.label}</span>
                      </div>

                      {dedupedEntries.map(({ primaryLink: lnk, roleSummary }) => {
                        const res = exploration.resources[lnk.resourceVersionId];
                        if (!res) return null;
                        const isSelected = selectedResourceId === res.id;
                        return (
                          <div
                            key={res.id}
                            className={`repo-file-row ${isSelected ? 'file-selected' : ''}`}
                            onClick={() => setSelectedResourceId(res.id)}
                          >
                            <div className="file-row-left">
                              <strong className="file-name">{res.filename}</strong>
                              <span className="file-title">{res.title}</span>
                            </div>
                            <div className="file-row-right">
                              <span
                                className={`ownership-tag ${
                                  lnk.isSharedReference ? 'tag-shared' : 'tag-owned'
                                }`}
                              >
                                {lnk.isSharedReference
                                  ? `共享 (源自 ${res.ownerNodeId}·${res.version})`
                                  : `独有 (${res.version})`}
                              </span>
                              <span className="role-tag">{roleSummary}</span>
                              {!exploration.harness && <span className={`content-badge content-${res.contentStatus}`}>
                                {res.contentStatus}
                              </span>}
                            </div>
                          </div>
                        );
                      })}

                      {(isMissingProfile || isMissingExp) && (
                        <div className="repo-file-missing">
                          <strong>
                            [未生成 / 缺失]{' '}
                            {isMissingProfile
                              ? 'msprof 硬件剖析文件尚未生成'
                              : '性能测量实验记录尚未生成'}
                          </strong>
                          <span>
                            {isMissingProfile
                              ? activeNode.stages.profiling.missingOutputReason
                              : activeNode.stages.bench_perf.missingOutputReason}
                          </span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {activeResource && (
                <div className="resource-preview-pane">
                  <div className="preview-header">
                    <div>
                      <h4 className="preview-title">
                        {activeResource.filename} — {activeResource.title}
                      </h4>
                      <div className="preview-provenance">
                        <span>ID: {activeResource.resourceId}</span>
                        <span>版本: {activeResource.version}</span>
                        <span title={`完整 SHA256: ${activeResource.sha256}`}>
                          SHA256: {activeResource.sha256.slice(0, 16)}
                        </span>
                        <span>来源: {activeResource.ownerNodeId}</span>
                        <span>
                          {(activeResource.sizeBytes / 1024).toFixed(2)} KB
                        </span>
                      </div>
                    </div>
                    {otherReferencingNodeIds.length > 1 && (
                      <div className="shared-consumers">
                        <span>共同引用节点:</span>
                        {otherReferencingNodeIds.map((nid) => (
                          <button
                            key={nid}
                            type="button"
                            className={`consumer-chip ${
                              nid === activeNode.id ? 'current' : ''
                            }`}
                            onClick={() => onSelectNode(nid)}
                          >
                            {nid}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>

                  {activeResource.experimentData && (
                    <div className={`structured-exp-box ${exploration.harness?.synthetic ? 'synthetic-exp-box' : ''}`}>
                      <table className="exp-proto-table">
                        <tbody>
                          <tr>
                            <th>环境与容差</th>
                            <td>
                              {activeResource.experimentData.hardware} ·{' '}
                              {activeResource.experimentData.referenceImpl} (atol=
                              {activeResource.experimentData.atol})
                            </td>
                          </tr>
                          <tr>
                            <th>精度与计时协议</th>
                            <td>
                              {activeResource.experimentData.correctnessPassed
                                ? `PASS (max_diff=${activeResource.experimentData.maxAbsDiff})`
                                : 'FAIL'}{' '}
                              · warmup={activeResource.experimentData.warmupRuns}, repeat=
                              {activeResource.experimentData.repeatRuns}
                            </td>
                          </tr>
                          <tr>
                            <th>耗时分布</th>
                            <td className="tabular-nums">
                              {exploration.harness?.synthetic ? <><strong>模拟 Median：{activeResource.experimentData.medianUs?.toFixed(1) || '—'} μs</strong><br/>P95：{activeResource.experimentData.p95Us?.toFixed(1) || '—'} μs · 样本数：{activeResource.experimentData.samplesUs?.length || 0}</> : <><strong>Median: {activeResource.experimentData.medianUs} us</strong> | P95: {activeResource.experimentData.p95Us} us | 样本: {activeResource.experimentData.samplesUs?.join(' / ')} us</>}
                            </td>
                          </tr>
                        </tbody>
                      </table>
                      {exploration.harness?.synthetic && <SyntheticDistribution samples={activeResource.experimentData.samplesUs || []}/>}
                    </div>
                  )}

                  {activeResource.profileData && (
                    <div className="structured-prof-box">
                      <div className="prof-bars-grid">
                        <div className="prof-metric-row">
                          <span>Cube 利用率</span>
                          <div className="prof-track">
                            <div
                              className="prof-fill fill-cube"
                              style={{ width: `${activeResource.profileData.cubeUtil}%` }}
                            />
                          </div>
                          <strong>{activeResource.profileData.cubeUtil}%</strong>
                        </div>
                        <div className="prof-metric-row">
                          <span>Vector 利用率</span>
                          <div className="prof-track">
                            <div
                              className="prof-fill fill-vec"
                              style={{ width: `${activeResource.profileData.vecUtil}%` }}
                            />
                          </div>
                          <strong>{activeResource.profileData.vecUtil}%</strong>
                        </div>
                        <div className="prof-metric-row">
                          <span>MTE2 搬运占比</span>
                          <div className="prof-track">
                            <div
                              className="prof-fill fill-mte"
                              style={{ width: `${activeResource.profileData.mte2Util}%` }}
                            />
                          </div>
                          <strong>{activeResource.profileData.mte2Util}%</strong>
                        </div>
                        <div className="prof-metric-row">
                          <span>PIPE_FIX 等待</span>
                          <div className="prof-track">
                            <div
                              className="prof-fill fill-wait"
                              style={{
                                width: `${activeResource.profileData.pipeFixWaitPct}%`,
                              }}
                            />
                          </div>
                          <strong>{activeResource.profileData.pipeFixWaitPct}%</strong>
                        </div>
                      </div>
                      <div className="binary-archive-notice">
                        <span>
                          二进制归档: <strong>{activeResource.profileData.rawArchiveName}</strong>{' '}
                          ({activeResource.profileData.rawArchiveSize})
                        </span>
                        <a
                          className="binary-tag"
                          href={`/api/explorations/${exploration.id}/resources/${activeResource.id}/download`}
                          target="_blank"
                          rel="noreferrer"
                          title="通过受控资源 ID 接口下载原始归档，不暴露服务器物理路径"
                        >
                          展示结构化摘要 · 受控下载原始归档
                        </a>
                      </div>
                    </div>
                  )}

                  {activeResource.category === 'summary' || /\.(md|markdown)$/i.test(activeResource.filename)
                    ? <MarkdownContent content={activeResource.content} localLinks={exploration.harness ? {resolve:href=>snapshotReference(href,activeResource.filename,new Set(Object.values(exploration.resources).filter(r=>!r.missingReason&&!r.isBinary).map(r=>r.filename))),open:path=>setSelectedResourceId(path)} : undefined} />
                    : exploration.harness?.synthetic && activeResource.experimentData ? <details className="synthetic-raw"><summary>查看完整模拟样本与 JSON</summary><pre className="preview-code-box">{activeResource.content}</pre></details> : <pre className="preview-code-box">{activeResource.content}</pre>}
                </div>
              )}
            </div>
          </section>
        </div>
      )}

      {selection.rightTab === 'compare' && exploration.harness && <div className="inspector-scroll"><h2 className="sheet-title">候选记录对比</h2>{exploration.harness.synthetic ? <><p className="capture-note">模拟测量 · 全部数值人工生成，未经设备验证。统一输入、协议和模拟基线；精度失败候选不排名。</p><table className="compare-table"><thead><tr><th>候选与改动</th><th>模拟精度</th><th>中位耗时</th><th>相对基线</th></tr></thead><tbody>{exploration.nodes.filter(n=>n.harness?.kind==='candidate').map(n=><tr key={n.id}><td><button className="toolbar-btn" onClick={()=>{onSelectNode(n.id);onChangeTab('inspector');}}>{n.id} · {n.title}</button></td><td>{n.harness?.synthetic ? n.correctnessPassed?'模拟通过':'模拟失败':'未测'}</td><td>{n.latencyUs?.toFixed(1) || '—'} {n.latencyUs?'μs':''}</td><td>{n.speedupVsBaseline?.toFixed(2) || '—'}{n.speedupVsBaseline?'×':''}</td></tr>)}</tbody></table></> : <p className="capture-note">快照未提供结构化的可比性能结果。选择节点原文核对候选身份、协议和逐 case 结果；不从 completed 推断精度通过。</p>}{exploration.nodes.filter(n=>n.harness?.kind==='candidate').map(n=><button className="graph-relation-row" key={n.id} onClick={()=>{onSelectNode(n.id);onChangeTab('inspector');}}>{n.id} · {n.title}<small>{n.changeSummary}</small></button>)}</div>}
      {selection.rightTab === 'compare' && !exploration.harness && (
        <div className="inspector-scroll">
          <section className="compare-sheet">
            <div className="sheet-header-row">
              <h2 className="sheet-title">
                节点实验对比：{activeNode.id} 对照 {compareNode.id}
              </h2>
              <div className="compare-selector">
                <label htmlFor="compare-select">选择对照节点:</label>
                <select
                  id="compare-select"
                  value={compareNode.id}
                  onChange={(e) => onChangeCompareNode(e.target.value)}
                >
                  {exploration.nodes
                    .map((n) => (
                      <option key={n.id} value={n.id}>
                        {n.id} — {n.branchLabel} (
                        {n.latencyUs ? `${n.latencyUs} us` : STATUS_LABELS[n.executionStatus]})
                      </option>
                    ))}
                </select>
              </div>
            </div>

            <div className="compare-rule-banner">
              <strong>可比性校验规则：</strong>
              先核对代码改动、硬件环境与测量协议（参考实现、容差、预热/重复次数与同步边界），再比较可比结果；未通过正确性验证的节点不得进入有效性能排名。
            </div>

            <table className="compare-table">
              <thead>
                <tr>
                  <th>对比维度</th>
                  <th>当前选中：{activeNode.id} ({activeNode.branchLabel})</th>
                  <th>对照节点：{compareNode.id} ({compareNode.branchLabel})</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th>1. 硬件与输入域</th>
                  <td>{exploration.environment} · {exploration.inputDomain}</td>
                  <td>{exploration.environment} · {exploration.inputDomain}</td>
                </tr>
                <tr>
                  <th>2. 验证与计时协议</th>
                  <td>FP32 Golden (atol=1e-3, rtol=1e-3) · warmup=20, repeat=100, npu.synchronize</td>
                  <td>FP32 Golden (atol=1e-3, rtol=1e-3) · warmup=20, repeat=100, npu.synchronize</td>
                </tr>
                <tr>
                  <th>3. 核心代码改动</th>
                  <td>{activeNode.changeSummary}</td>
                  <td>{compareNode.changeSummary}</td>
                </tr>
                <tr>
                  <th>4. 正确性验证状态</th>
                  <td>
                    {activeNode.correctnessPassed ? (
                      <strong className="text-ok">已通过 (可参与性能比较)</strong>
                    ) : (
                      <strong className="text-fail">
                        未通过 ({activeNode.stopOrFailReason}) · 不可直接排名
                      </strong>
                    )}
                  </td>
                  <td>
                    {compareNode.correctnessPassed ? (
                      <strong className="text-ok">已通过 (可参与性能比较)</strong>
                    ) : (
                      <strong className="text-fail">
                        未通过 ({compareNode.stopOrFailReason}) · 不可直接排名
                      </strong>
                    )}
                  </td>
                </tr>
                <tr>
                  <th>5. 中位耗时与提速比</th>
                  <td className="tabular-nums">
                    {activeNode.correctnessPassed && activeNode.latencyUs
                      ? `${activeNode.latencyUs} us (${activeNode.speedupVsBaseline}x vs 基线)`
                      : 'N/A (缺乏有效可比实测值)'}
                  </td>
                  <td className="tabular-nums">
                    {compareNode.correctnessPassed && compareNode.latencyUs
                      ? `${compareNode.latencyUs} us (${compareNode.speedupVsBaseline}x vs 基线)`
                      : 'N/A (缺乏有效可比实测值)'}
                  </td>
                </tr>
                <tr>
                  <th>6. msprof 流水线画像</th>
                  <td>
                    {activeNode.pipelineMetrics
                      ? `Cube ${activeNode.pipelineMetrics.cubePct}% | Vec ${activeNode.pipelineMetrics.vecPct}% | MTE2 ${activeNode.pipelineMetrics.mte2Pct}% (${activeNode.pipelineMetrics.bottleneck})`
                      : '未生成 profile'}
                  </td>
                  <td>
                    {compareNode.pipelineMetrics
                      ? `Cube ${compareNode.pipelineMetrics.cubePct}% | Vec ${compareNode.pipelineMetrics.vecPct}% | MTE2 ${compareNode.pipelineMetrics.mte2Pct}% (${compareNode.pipelineMetrics.bottleneck})`
                      : '未生成 profile'}
                  </td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
      )}

      {selection.rightTab === 'candidates' && exploration.harness && <div className="inspector-scroll"><h2 className="sheet-title">Agent 观察</h2><p className="capture-note">根据当前快照整理，不替代验收判定。点击依据文件查看原文。</p>{exploration.resources['@agent/observations.md'] ? <MarkdownContent content={exploration.resources['@agent/observations.md'].content} localLinks={{resolve:href=>snapshotReference(href,'',new Set(Object.keys(exploration.resources))),open:path=>{setSelectedResourceId(path);onChangeTab('inspector');}}}/> : <p>尚未发布可视化 Agent 说明。</p>}</div>}
      {selection.rightTab === 'candidates' && !exploration.harness && (
        <div className="inspector-scroll">
          <section className="candidates-sheet">
            <h2 className="sheet-title">探索图{exploration.harness ? 'Agent 观察' : '总结与待冷却候选'} (ready-for-cool)</h2>
            <p className="sheet-desc">
              当前原型流程收尾于整理 <strong>ready-for-cool</strong> 候选（仍属于 hot，不代表已审批或已合入远端 cooled 主线）。耗时改善与原因解释分别记录，未经证实的原因保留为假设。
            </p>

            <div className="coord-rules-box">
              <h3 className="sub-heading">探索状态与证据</h3>
              <p>{exploration.coordinatorStatus} · {exploration.stopReason || '尚未记录停止原因'}</p>
              <p>最佳有效方案：{exploration.bestValidNodeId || '暂无'}</p>
              <p>未完成方向：{exploration.unfinishedDirections.join('；') || '尚未记录'}</p>
              <p>知识与经验的支持、反驳及参考关系请在探索图中点击查看；引用不代表已证实的性能归因。</p>
            </div>

            <div className="knowledge-candidates-list">
              <h3 className="sub-heading">提炼的知识与经验候选 (关联支持节点与反例)</h3>
              {exploration.knowledgeItems.map((k) => (
                <div key={k.id} className="k-candidate-block">
                  <div className="k-head">
                    <strong>
                      [{k.id}] {k.title}
                    </strong>
                    <div className="k-badges">
                      <span className="claim-tag">{k.claimType.toUpperCase()}</span>
                      <span className={`content-badge content-${k.contentStatus}`}>
                        <ContentStateIcon status={k.contentStatus} size={11} />
                        <span>{k.contentStatus}</span>
                      </span>
                    </div>
                  </div>
                  <MarkdownContent content={k.body} />
                  <div className="k-meta-grid">
                    <div>
                      <strong>适用条件：</strong> {k.applicability}
                    </div>
                    <div>
                      <strong>未验证范围 (保留为假设)：</strong> {k.unverifiedScope}
                    </div>
                  </div>
                  <div className="k-evidence-links">
                    <span>支持节点:</span>
                    {k.supportingNodeIds.map((nid) => (
                      <button
                        key={nid}
                        type="button"
                        className="inline-link-btn"
                        onClick={() => {
                          onChangeTab('inspector');
                          onSelectNode(nid);
                        }}
                      >
                        {nid}
                      </button>
                    ))}
                    <span>反例节点:</span>
                    {k.counterexampleNodeIds.map((nid) => (
                      <button
                        key={nid}
                        type="button"
                        className="inline-link-btn xref-btn"
                        onClick={() => {
                          onChangeTab('inspector');
                          onSelectNode(nid);
                        }}
                      >
                        {nid} (反例)
                      </button>
                    ))}
                    <span className="evidence-attempts">
                      证据 Attempt: {k.evidenceAttemptIds.join(', ')}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </aside>
  );
};
