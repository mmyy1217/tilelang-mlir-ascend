import React, { useEffect, useMemo, useState } from 'react';
import { recordedStatus } from '../harnessAdapter';
import { snapshotReference } from '../harnessLinks';
import type { MeasurementView } from '../measurements';
import type {
  CooledKnowledgeView,
  Exploration,
  HarnessSearchView,
  HotKnowledgeView,
  NodeRecord,
  SearchNodeView,
} from '../types';
import { MarkdownContent } from './MarkdownContent';
import type { TrajectorySelection } from './TrajectoryWorkbench';

const KIND_LABELS: Record<string, string> = {
  observation: '观察 (Observation)',
  hypothesis: '假设 (Hypothesis)',
  conclusion: '结论 (Conclusion)',
};

const STAGE_LABELS: Record<string, string> = {
  analysis: '分析',
  develop: '开发',
  'device-test': '测试',
  'prepare-environment': '环境',
  'establish-baseline': '基线',
};

export const SearchAndKnowledgeInspector: React.FC<{
  exploration: Exploration;
  search: HarnessSearchView;
  selection: TrajectorySelection;
  onSelect: (sel: TrajectorySelection) => void;
}> = ({ exploration, search, selection, onSelect }) => {
  const [selectedHotRevision, setSelectedHotRevision] = useState<number | null>(null);
  const [selectedEvidenceIdx, setSelectedEvidenceIdx] = useState<number>(0);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(selection.runId || null);
  const [selectedRunDocRole, setSelectedRunDocRole] = useState<
    'result' | 'task' | 'manifest' | 'measurement'
  >('result');
  const [selectedCandId, setSelectedCandId] = useState<string | null>(
    selection.candidateId || null
  );
  const [selectedReportPath, setSelectedReportPath] = useState<string | null>(null);

  const resourceSet = useMemo(
    () => new Set(Object.keys(exploration.resources)),
    [exploration.resources]
  );

  const openLocalFile = (path: string) => {
    if (!exploration.resources[path]) return;
    onSelect({
      ...selection,
      kind: 'file',
      filePath: path,
    });
  };

  // Sync internal states when selection changes
  const activeSearchNode: SearchNodeView | undefined = useMemo(() => {
    const targetId =
      selection.searchNodeId || search.currentNodeId || search.searchNodes[0]?.id;
    return search.searchNodes.find((n) => n.id === targetId) || search.searchNodes[0];
  }, [search, selection.searchNodeId]);

  useEffect(() => {
    if (!activeSearchNode) return;
    if (
      selection.candidateId &&
      activeSearchNode.candidateIds.includes(selection.candidateId)
    ) {
      setSelectedCandId(selection.candidateId);
    } else {
      setSelectedCandId(
        activeSearchNode.candidateIds[activeSearchNode.candidateIds.length - 1] || null
      );
    }
    if (
      selection.runId &&
      activeSearchNode.runs.some((r) => r.id === selection.runId)
    ) {
      setSelectedRunId(selection.runId);
    } else {
      const lastRun = activeSearchNode.runs[activeSearchNode.runs.length - 1];
      setSelectedRunId(lastRun?.id || null);
    }
    setSelectedRunDocRole('result');
  }, [activeSearchNode?.id, selection.candidateId, selection.runId]);

  useEffect(() => {
    setSelectedHotRevision(null);
    setSelectedEvidenceIdx(0);
  }, [selection.hotId]);

  useEffect(() => {
    setSelectedReportPath(null);
  }, [selectedCandId]);

  // 1. Hot Knowledge Inspector
  if (selection.kind === 'hot' && selection.hotId) {
    const hot: HotKnowledgeView | undefined = search.hotKnowledge.find(
      (h) => h.id === selection.hotId
    );
    if (!hot) {
      return (
        <div className="hv-empty-state">
          <h2>未找到热知识 {selection.hotId}</h2>
        </div>
      );
    }
    const activeRev =
      hot.revisions.find((r) => r.revision === selectedHotRevision) || hot.latest;
    const activities = (hot.activities || []).filter(a => a.revision === activeRev.revision);
    const activeEvidence = activeRev.evidence[selectedEvidenceIdx] || activeRev.evidence[0];
    const rawResource = exploration.resources[activeRev.path];

    return (
      <div className="hv-dossier-scroll">
        <section className="hv-section">
          <div className="hv-title-row">
            <div className="hv-title-left">
              <span className="hv-dossier-cid">{hot.id}</span>
              <h2 className="hv-dossier-title">
                工作区热知识 · {KIND_LABELS[activeRev.kind] || activeRev.kind}
              </h2>
              <p>产出依据：{activeRev.evidenceSources.join(' · ') || '未记录'}</p>
              {(['use-hot', 'nominate-cooled'] as const).map(kind => <div key={kind}>
                <strong>{kind === 'use-hot' ? '使用此修订' : 'Cooled 候选提名'}</strong>
                {activities.filter(a => a.kind === kind).length === 0 ? ' · 未记录' :
                  activities.filter(a => a.kind === kind).map(a => <p key={a.path}>
                    <button className="hv-link-chip" onClick={() => onSelect({kind:'search-node',searchNodeId:a.node,runId:a.run})}>{a.node} / {a.run}</button> {a.reason}
                  </p>)}
              </div>)}

            </div>
            {hot.promotedCooledId ? (
              <button
                type="button"
                className="traj-promoted-pill"
                onClick={() =>
                  onSelect({ kind: 'cooled', cooledKey: hot.promotedCooledId })
                }
              >
                已冷却为长期知识 → {hot.promotedCooledId}
              </button>
            ) : (
              <span className="hv-status-pill tone-warn">HOT · 工作区活跃知识</span>
            )}
          </div>

          {hot.revisions.length > 1 && (
            <div className="hv-sec-bar">
              <span className="traj-meta-label">历史修订版本：</span>
              <div className="hv-pill-group">
                {hot.revisions.map((rev) => (
                  <button
                    key={rev.revision}
                    type="button"
                    className={`hv-pill-btn ${
                      activeRev.revision === rev.revision ? 'is-active' : ''
                    }`}
                    onClick={() => {
                      setSelectedHotRevision(rev.revision);
                      setSelectedEvidenceIdx(0);
                    }}
                  >
                    rev.{rev.revision} ({rev.kind})
                    {rev.revision === hot.latest.revision ? ' · 最新' : ''}
                  </button>
                ))}
              </div>
            </div>
          )}

          <p className="hv-summary-prose">{activeRev.statement}</p>

          <div className="traj-spec-grid">
            <div className="traj-spec-box">
              <strong>适用条件 (Applicability)</strong>
              <p>{activeRev.applicability || '未记录'}</p>
            </div>
            <div className="traj-spec-box">
              <strong>边界与局限 (Limitations)</strong>
              <p>{activeRev.limitations || '未记录'}</p>
            </div>
          </div>

          <div className="hv-lineage-row">
            <div className="hv-lineage-item">
              <span>关联搜索节点 (Nodes)：</span>
              {activeRev.nodes.map((nId) => {
                const sn = search.searchNodes.find((x) => x.id === nId);
                return (
                  <button
                    key={nId}
                    type="button"
                    className="hv-link-chip"
                    title={sn?.hypothesis}
                    onClick={() => onSelect({ kind: 'search-node', searchNodeId: nId })}
                  >
                    {nId}
                  </button>
                );
              })}
            </div>
          </div>
        </section>

        <section className="hv-section">
          <div className="hv-sec-bar">
            <h3 className="hv-sec-heading">
              不可变证据快照 (.workspace/knowledge/evidence/*.snapshot)
            </h3>
            {activeRev.evidence.length > 1 && (
              <div className="hv-pill-group">
                {activeRev.evidence.map((ev, idx) => (
                  <button
                    key={ev.path}
                    type="button"
                    className={`hv-pill-btn ${selectedEvidenceIdx === idx ? 'is-active' : ''}`}
                    onClick={() => setSelectedEvidenceIdx(idx)}
                  >
                    证据 #{idx + 1} · {ev.sourcePath?.split('/').slice(-2).join('/') || ev.path.slice(-14)}
                  </button>
                ))}
              </div>
            )}
          </div>

          {activeEvidence ? (
            <div className="hv-doc-viewer">
              <div className="hv-doc-header">
                <div className="hv-pill-group">
                  <strong className="hv-doc-title">
                    源路径: {activeEvidence.sourcePath || '未知来源'}
                  </strong>
                  {activeEvidence.sourcePath &&
                    exploration.resources[activeEvidence.sourcePath] && (
                      <button
                        type="button"
                        className="hv-pill-btn"
                        onClick={() => openLocalFile(activeEvidence.sourcePath!)}
                      >
                        打开源文件
                      </button>
                    )}
                </div>
                <span className="hv-doc-meta">
                  快照: {activeEvidence.path.split('/').pop()}
                </span>
              </div>
              <div className="hv-markdown-box">
                {activeEvidence.content ? (
                  activeEvidence.sourcePath?.endsWith('.md') ? (
                    <MarkdownContent
                      content={activeEvidence.content}
                      localLinks={{
                        resolve: (href) =>
                          snapshotReference(
                            href,
                            activeEvidence.sourcePath || activeEvidence.path,
                            resourceSet
                          ),
                        open: openLocalFile,
                      }}
                    />
                  ) : (
                    <pre className="preview-code-box">{activeEvidence.content}</pre>
                  )
                ) : (
                  <p className="hv-calm-empty">快照内容未读取</p>
                )}
              </div>
            </div>
          ) : (
            <p className="hv-calm-empty">未附带证据快照</p>
          )}

          {rawResource && (
            <details className="hv-raw-json">
              <summary>查看热知识原始 JSON ({activeRev.path})</summary>
              <pre className="preview-code-box">{rawResource.content}</pre>
            </details>
          )}
        </section>
      </div>
    );
  }

  // 2. Cooled Knowledge Inspector
  if (selection.kind === 'cooled' && selection.cooledKey) {
    const cooled: CooledKnowledgeView | undefined = search.cooledKnowledge.find(
      (c) => c.fullKey === selection.cooledKey
    );
    if (!cooled) {
      return (
        <div className="hv-empty-state">
          <h2>未找到冷却知识 {selection.cooledKey}</h2>
        </div>
      );
    }
    const rawResource = exploration.resources[cooled.path];


    return (
      <div className="hv-dossier-scroll">
        <section className="hv-section">
          <div className="hv-title-row">
            <div className="hv-title-left">
              <span className="hv-dossier-cid">{cooled.fullKey}</span>
              <h2 className="hv-dossier-title">
                冷却知识 · {KIND_LABELS[cooled.kind] || cooled.kind}
              </h2>
            </div>
            <span
              className={`hv-status-pill ${
                cooled.status === 'active'
                  ? 'tone-ok'
                  : cooled.status === 'needs_revalidation'
                    ? 'tone-warn'
                    : 'tone-neutral'
              }`}
            >
              {cooled.status === 'active'
                ? 'ACTIVE · 已生效'
                : cooled.status === 'needs_revalidation'
                  ? 'NEEDS REVALIDATION · 待复核'
                  : 'SUPERSEDED · 已替代'}
            </span>
          </div>

          <p className="hv-summary-prose">{cooled.statement}</p>

          {cooled.invalidationReason && (
            <div className="traj-alert-note">
              <strong>状态变更说明：</strong>
              <span>{cooled.invalidationReason}</span>
            </div>
          )}

          <div className="traj-spec-grid">
            <div className="traj-spec-box">
              <strong>适用范围 (Applicability)</strong>
              <p>{cooled.applicability || '未记录'}</p>
            </div>
            <div className="traj-spec-box">
              <strong>边界与局限 (Limitations)</strong>
              <p>{cooled.limitations || '未记录'}</p>
            </div>
          </div>

          <div className="traj-spec-box" style={{ marginTop: 10 }}>
            <strong>证据摘要 (Evidence Summary)</strong>
            <p>{cooled.evidenceSummary || '未记录'}</p>
            {cooled.documentPath && <button className="hv-link-chip" onClick={() => onSelect({kind:'file', filePath:cooled.documentPath})}>查看仓库文档原文</button>}
          </div>
        </section>

        <section className="hv-section">
          <div className="hv-sec-bar">
            <h3 className="hv-sec-heading">溯源与搜索节点引用记录 (Provenance & Usage)</h3>
          </div>

          <div className="hv-lineage-row" style={{ marginBottom: 14 }}>
            {cooled.provenance?.hotId && (
              <div className="hv-lineage-item">
                <span>来源热知识：</span>
                {search.hotKnowledge.some((h) => h.id === cooled.provenance!.hotId) ? (
                  <button
                    type="button"
                    className="hv-link-chip"
                    onClick={() =>
                      onSelect({ kind: 'hot', hotId: cooled.provenance!.hotId })
                    }
                  >
                    {cooled.provenance.hotId} rev.{cooled.provenance.revision}
                  </button>
                ) : (
                  <strong>
                    {cooled.provenance.hotId} rev.{cooled.provenance.revision}
                  </strong>
                )}
              </div>
            )}

            {cooled.provenance?.nodes && cooled.provenance.nodes.length > 0 && (
              <div className="hv-lineage-item">
                <span>归纳自节点：</span>
                {cooled.provenance.nodes.map((nId) => (
                  <button
                    key={nId}
                    type="button"
                    className="hv-link-chip"
                    onClick={() => onSelect({ kind: 'search-node', searchNodeId: nId })}
                  >
                    {nId}
                  </button>
                ))}
              </div>
            )}

            {cooled.review && (
              <div className="hv-lineage-item">
                <span>冷却评审：</span>
                <strong>
                  {cooled.review.id} · {cooled.review.reviewer}
                </strong>
                {cooled.review.delivery && <span>交付标识：{cooled.review.delivery}</span>}
              </div>
            )}
          </div>

          {cooled.usedByNodes.length > 0 ? (
            <table className="hv-table">
              <thead>
                <tr>
                  <th>引入节点</th>
                  <th>节点假设</th>
                  <th>引入理由 (use-cooled reason)</th>
                </tr>
              </thead>
              <tbody>
                {cooled.usedByNodes.map((u) => {
                  const sNode = search.searchNodes.find((n) => n.id === u.searchNodeId);
                  return (
                    <tr
                      key={u.searchNodeId}
                      className="hv-clickable-row"
                      onClick={() =>
                        onSelect({ kind: 'search-node', searchNodeId: u.searchNodeId })
                      }
                    >
                      <td>
                        <strong>{u.searchNodeId}</strong>
                      </td>
                      <td>{sNode?.hypothesis || '—'}</td>
                      <td>{u.reason}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <p className="hv-calm-empty">当前搜索树尚无节点通过 use-cooled 引用此条目。</p>
          )}
        </section>

        {rawResource && (
          <section className="hv-section">
            <details className="hv-raw-json">
              <summary>查看冷却知识原始 JSON ({cooled.path})</summary>
              <pre className="preview-code-box">{rawResource.content}</pre>
            </details>
          </section>
        )}
      </div>
    );
  }

  // 3. Direct File Inspector
  if (selection.kind === 'file' && selection.filePath) {
    const res = exploration.resources[selection.filePath];
    return (
      <div className="hv-dossier-scroll">
        <section className="hv-section">
          <div className="hv-title-row">
            <div className="hv-title-left">
              <span className="hv-dossier-cid">FILE</span>
              <h2 className="hv-dossier-title">{selection.filePath}</h2>
            </div>
            {selection.searchNodeId && (
              <button
                type="button"
                className="hv-btn"
                onClick={() =>
                  onSelect({
                    kind: 'search-node',
                    searchNodeId: selection.searchNodeId,
                    candidateId: selection.candidateId,
                    runId: selection.runId,
                  })
                }
              >
                ← 返回搜索节点 {selection.searchNodeId}
              </button>
            )}
          </div>

          {res ? (
            <div className="hv-doc-viewer">
              <div className="hv-doc-header">
                <strong className="hv-doc-title">{res.title || res.filename}</strong>
                <span className="hv-doc-meta tabular-nums">
                  {res.sizeBytes} bytes
                  {res.sha256 ? ` · sha256:${res.sha256.slice(0, 12)}` : ''}
                </span>
              </div>
              <div className="hv-markdown-box">
                {/\.(md|markdown)$/i.test(res.filename) ? (
                  <MarkdownContent
                    content={res.content}
                    localLinks={{
                      resolve: (href) =>
                        snapshotReference(href, res.filename, resourceSet),
                      open: openLocalFile,
                    }}
                  />
                ) : (
                  <pre className="preview-code-box">{res.content}</pre>
                )}
              </div>
            </div>
          ) : (
            <p className="hv-calm-empty">未能在快照中找到文件 {selection.filePath}</p>
          )}
        </section>
      </div>
    );
  }

  // 4. Default / Search Node Inspector (with embedded Candidate Scorecard & Run Reader)
  if (!activeSearchNode) {
    return (
      <div className="hv-empty-state">
        <h2>等待搜索树数据</h2>
      </div>
    );
  }

  const nodeCandidates: NodeRecord[] = activeSearchNode.candidateIds
    .map((cid) => exploration.nodes.find((n) => n.id === cid))
    .filter((n): n is NodeRecord => Boolean(n));

  const activeCand =
    nodeCandidates.find((c) => c.id === selectedCandId) ||
    nodeCandidates[nodeCandidates.length - 1] ||
    null;

  const measurements = exploration.harness?.measurements;
  const candReports =
    activeCand && measurements
      ? measurements.reports
          .filter((r) => r.subjectId === activeCand.id && r.kind === 'candidate' && r.valid)
          .sort((a, b) => Date.parse(b.recordedAt) - Date.parse(a.recordedAt))
      : [];

  const activeReport: MeasurementView | undefined =
    candReports.find((r) => r.path === selectedReportPath) || candReports[0];

  const activeRun =
    activeSearchNode.runs.find((r) => r.id === selectedRunId) ||
    activeSearchNode.runs[activeSearchNode.runs.length - 1] ||
    null;

  const activeRunDocPath = (() => {
    if (!activeRun) return null;
    if (selectedRunDocRole === 'task') return activeRun.taskPath || activeRun.resultPath || null;
    if (selectedRunDocRole === 'measurement')
      return activeRun.measurementPath || activeRun.resultPath || null;
    if (selectedRunDocRole === 'manifest') {
      const candFile = activeRun.files.find((f) => /CANDIDATE\.md$/.test(f));
      return candFile || activeCand?.harness?.manifestPath || activeRun.resultPath || null;
    }
    return activeRun.resultPath || activeRun.taskPath || activeRun.files[0] || null;
  })();

  const usedCooledItems = activeSearchNode.usedCooledIds
    .map((key) => search.cooledKnowledge.find((c) => c.fullKey === key))
    .filter((c): c is CooledKnowledgeView => Boolean(c));

  const producedHotItems = activeSearchNode.hotKnowledgeIds
    .map((hid) => search.hotKnowledge.find((h) => h.id === hid))
    .filter((h): h is HotKnowledgeView => Boolean(h));

  return (
    <div className="hv-dossier-scroll">
      {/* Section 1: Search Node Header, Hypothesis, Scope & Tree Lineage */}
      <section className="hv-section">
        <div className="hv-title-row">
          <div className="hv-title-left">
            <span className="hv-dossier-cid">{activeSearchNode.id}</span>
            <h2 className="hv-dossier-title">{activeSearchNode.hypothesis}</h2>
          </div>
          <span
            className={`hv-status-pill ${
              activeSearchNode.isCurrent || activeSearchNode.status === 'open'
                ? 'tone-ok'
                : activeSearchNode.status === 'parked'
                  ? 'tone-warn'
                  : 'tone-neutral'
            }`}
          >
            {activeSearchNode.isCurrent
              ? '● CURRENT · 当前探索焦点'
              : activeSearchNode.status.toUpperCase()}
          </span>
        </div>

        <h3>节点内实验</h3>
        <p>{activeSearchNode.runs.length} 条 Run · {activeSearchNode.loopCount == null ? '优化轮次未记录' : activeSearchNode.loopCount + ' 个已记录轮次'}</p>
        {activeSearchNode.runs.map(run => <button key={run.id} className="hv-link-chip" onClick={() =>
          onSelect({kind:run.resultPath ? 'file' : 'search-node',filePath:run.resultPath,searchNodeId:activeSearchNode.id,runId:run.id})}>{run.id} · {run.operation || run.stage}</button>)}
        {search.hotKnowledge.flatMap(h => (h.activities || []).filter(a => a.node === activeSearchNode.id).map(a =>
          <p key={a.path}><button className="hv-link-chip" onClick={() => onSelect({kind:'hot',hotId:h.id})}>{a.kind === 'use-hot' ? '使用 hot' : '提名 cooled 候选'} · {h.id} rev.{a.revision}</button> {a.run} · {a.reason}</p>))}
        <details open><summary>方向切换 · 账本登记顺序</summary>
          <ol>{search.events.filter(e => e.op === 'select' || e.op === 'node-status').map(e =>
            <li key={e.eventId}><time>{e.at}</time> · {e.op === 'select' ? '转入' : String(e.raw.status)} {String(e.raw.id)} · {String(e.raw.reason || '原因未记录')}</li>)}</ol>
          {!search.events.some(e => e.op === 'select' || e.op === 'node-status') && <p>切换记录缺失</p>}
        </details>
        {activeSearchNode.scope && (
          <p className="hv-summary-prose">验证范围 (Scope)：{activeSearchNode.scope}</p>
        )}

        <div className="hv-lineage-row">
          <div className="hv-lineage-item">
            <span>父搜索节点：</span>
            {activeSearchNode.parentId ? (
              <button
                type="button"
                className="hv-link-chip"
                onClick={() =>
                  onSelect({
                    kind: 'search-node',
                    searchNodeId: activeSearchNode.parentId!,
                  })
                }
              >
                ← {activeSearchNode.parentId}
              </button>
            ) : (
              <strong>根搜索节点 (Root)</strong>
            )}
          </div>

          {activeSearchNode.childrenIds.length > 0 && (
            <div className="hv-lineage-item">
              <span>派生子搜索节点：</span>
              {activeSearchNode.childrenIds.map((cid) => {
                const child = search.searchNodes.find((n) => n.id === cid);
                return (
                  <button
                    key={cid}
                    type="button"
                    className="hv-link-chip"
                    onClick={() => onSelect({ kind: 'search-node', searchNodeId: cid })}
                  >
                    → {cid} ({child?.status || 'open'})
                  </button>
                );
              })}
            </div>
          )}

          {exploration.resources['search/tree.json'] && (
            <button
              type="button"
              className="hv-link-chip"
              onClick={() =>
                onSelect({
                  kind: 'file',
                  searchNodeId: activeSearchNode.id,
                  filePath: 'search/tree.json',
                })
              }
            >
              search/tree.json
            </button>
          )}
        </div>
      </section>

      {(usedCooledItems.length > 0 || producedHotItems.length > 0) && (
        <div className="hv-knowledge-references" aria-label="节点知识引用">
          {usedCooledItems.map(item => <button key={item.fullKey} type="button" className="hv-link-chip"
            title={item.usedByNodes.find(u => u.searchNodeId === activeSearchNode.id)?.reason}
            onClick={() => onSelect({kind:'cooled',cooledKey:item.fullKey})}>引用 {item.fullKey}</button>)}
          {producedHotItems.map(item => <button key={item.id} type="button" className="hv-link-chip"
            onClick={() => onSelect({kind:'hot',hotId:item.id})}>产出 {item.id}</button>)}
        </div>
      )}

      {/* Section 3: Candidates & Per-Case Measurement Scorecard */}
      {nodeCandidates.length > 0 && activeCand && (
        <section className="hv-section">
          <div className="hv-sec-bar">
            <h3 className="hv-sec-heading">
              节点产出候选与测量成绩单 ({activeCand.id} · {activeCand.title})
            </h3>
            <div className="hv-pill-group">
              {nodeCandidates.length > 1 &&
                nodeCandidates.map((cand) => (
                  <button
                    key={cand.id}
                    type="button"
                    className={`hv-pill-btn ${activeCand.id === cand.id ? 'is-active' : ''}`}
                    onClick={() => setSelectedCandId(cand.id)}
                  >
                    候选 {cand.id}
                    {cand.speedupVsBaseline
                      ? ` (${cand.speedupVsBaseline.toFixed(2)}×)`
                      : ''}
                  </button>
                ))}
              {candReports.length > 1 &&
                candReports.map((rep, idx) => (
                  <button
                    key={rep.path}
                    type="button"
                    className={`hv-pill-btn ${
                      activeReport?.path === rep.path ? 'is-active' : ''
                    }`}
                    onClick={() => setSelectedReportPath(rep.path)}
                  >
                    {rep.runId}
                    {idx === 0 ? ' (最新)' : ''}
                  </button>
                ))}
              {activeCand.harness?.manifestPath && (
                <button
                  type="button"
                  className="hv-pill-btn"
                  onClick={() => openLocalFile(activeCand.harness!.manifestPath!)}
                >
                  CANDIDATE.md
                </button>
              )}
            </div>
          </div>

          {activeReport ? (
            <div className="hv-scorecard">
              <table className="hv-table tabular-nums">
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>输入规格</th>
                    <th>精度</th>
                    <th>耗时 Median / P95 (μs)</th>
                    <th>相对基线</th>
                  </tr>
                </thead>
                <tbody>
                  {activeReport.cases.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <strong>{c.id}</strong>
                      </td>
                      <td>{c.label}</td>
                      <td
                        className={
                          c.accuracy === 'passed'
                            ? 'hv-text-ok'
                            : c.accuracy === 'failed'
                              ? 'hv-text-fail'
                              : ''
                        }
                      >
                        {c.accuracy === 'passed'
                          ? '通过'
                          : c.accuracy === 'failed'
                            ? `失败${
                                c.maxAbsDiff !== undefined ? ` (误差 ${c.maxAbsDiff})` : ''
                              }`
                            : '未运行'}
                      </td>
                      <td>
                        {c.medianUs !== undefined
                          ? `${c.medianUs.toFixed(2)} / ${c.p95Us?.toFixed(2) ?? '—'}`
                          : '—'}
                      </td>
                      <td>
                        {activeReport.comparable && c.speedup ? (
                          <strong>{c.speedup.toFixed(2)}×</strong>
                        ) : (
                          <span className="hv-cell-sub">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="hv-scorecard-footer">
                <div className="hv-scorecard-verdict">
                  {activeReport.comparable && activeReport.speedup ? (
                    <span>
                      综合几何平均加速比：
                      <strong className="hv-text-ok">
                        {activeReport.speedup.toFixed(2)}×
                      </strong>
                    </span>
                  ) : (
                    <span className="hv-text-warn">{activeReport.comparisonReason}</span>
                  )}
                  {activeReport.protocol && (
                    <span className="hv-proto-meta">
                      预热 {activeReport.protocol.warmupRuns} 次 · 采样{' '}
                      {activeReport.protocol.repeatRuns} 次 ·{' '}
                      {activeReport.protocol.timingBoundary}
                    </span>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="hv-calm-empty">
              {activeCand.stopOrFailReason || '当前候选尚无多算例测量报告'}
            </p>
          )}
        </section>
      )}

      {/* Section 4: Search Node Execution Runs & Inline Artifact Reader */}
      <section className="hv-section hv-reader-section">
        <div className="hv-sec-bar">
          <h3 className="hv-sec-heading">
            节点执行循环与归档文件 ({activeSearchNode.runs.length} Runs)
          </h3>
        </div>

        {activeSearchNode.runs.length === 0 ? (
          <p className="hv-calm-empty">
            该搜索节点刚创建并完成前置冷却知识绑定，尚未执行分析/开发/测试任务。
          </p>
        ) : (
          <>
            <div className="hv-run-selector" role="tablist">
              {activeSearchNode.runs.map((run) => {
                const isSelected = activeRun?.id === run.id;
                return (
                  <button
                    key={run.id}
                    type="button"
                    role="tab"
                    aria-selected={isSelected}
                    className={`hv-run-card-btn ${isSelected ? 'is-active' : ''}`}
                    onClick={() => {
                      setSelectedRunId(run.id);
                      setSelectedRunDocRole('result');
                    }}
                  >
                    <div className="hv-rc-top">
                      <strong>
                        {STAGE_LABELS[run.stage] || run.stage} · {run.id}
                      </strong>
                      {run.candidateId && (
                        <span className="hv-rc-agent">{run.candidateId}</span>
                      )}
                    </div>
                    <span className="hv-rc-sub">{run.operation}</span>
                  </button>
                );
              })}
            </div>

            {activeRun && activeRunDocPath && exploration.resources[activeRunDocPath] && (
              <div className="hv-doc-viewer">
                <div className="hv-doc-header">
                  <div className="hv-pill-group">
                    {activeRun.resultPath && (
                      <button
                        type="button"
                        className={`hv-pill-btn ${
                          selectedRunDocRole === 'result' ? 'is-active' : ''
                        }`}
                        onClick={() => setSelectedRunDocRole('result')}
                      >
                        交付结论 (RESULT.md)
                      </button>
                    )}
                    {activeRun.taskPath && (
                      <button
                        type="button"
                        className={`hv-pill-btn ${
                          selectedRunDocRole === 'task' ? 'is-active' : ''
                        }`}
                        onClick={() => setSelectedRunDocRole('task')}
                      >
                        任务定义 (TASK.md)
                      </button>
                    )}
                    {activeRun.files.some((f) => /CANDIDATE\.md$/.test(f)) && (
                      <button
                        type="button"
                        className={`hv-pill-btn ${
                          selectedRunDocRole === 'manifest' ? 'is-active' : ''
                        }`}
                        onClick={() => setSelectedRunDocRole('manifest')}
                      >
                        候选清单 (CANDIDATE.md)
                      </button>
                    )}
                    {activeRun.measurementPath && (
                      <button
                        type="button"
                        className={`hv-pill-btn ${
                          selectedRunDocRole === 'measurement' ? 'is-active' : ''
                        }`}
                        onClick={() => setSelectedRunDocRole('measurement')}
                      >
                        测量数据 (measurement.json)
                      </button>
                    )}
                  </div>
                  <span className="hv-doc-meta">
                    {recordedStatus[activeRun.status] || activeRun.status} ·{' '}
                    {activeRunDocPath}
                  </span>
                </div>

                <div className="hv-markdown-box">
                  {/\.(md|markdown)$/i.test(activeRunDocPath) ? (
                    <MarkdownContent
                      content={exploration.resources[activeRunDocPath].content}
                      localLinks={{
                        resolve: (href) =>
                          snapshotReference(href, activeRunDocPath, resourceSet),
                        open: openLocalFile,
                      }}
                    />
                  ) : (
                    <pre className="preview-code-box">
                      {exploration.resources[activeRunDocPath].content}
                    </pre>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
};
