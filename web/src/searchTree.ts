import {projectRecordPath} from './nativeRecords';
import type {
  CooledKnowledgeStatus,
  CooledKnowledgeView,
  Exploration,
  HarnessSearchView,
  HotKnowledgeRevisionView,
  HotKnowledgeView,
  KnowledgeItemKind,
  KnowledgeRecord,
  SearchEventView,
  SearchNodeStatus,
  SearchNodeView,
  SearchRunEntry,
} from './types';
import type { HarnessModel } from './harnessAdapter';

const cleanPath = (value?: string): string =>
  (value || '')
    .trim()
    .replace(/^\[[^\]]*\]\(([^)]+)\)$/, '$1')
    .replace(/^[`<]|[`>]$/g, '')
    .replace(/^\.workspace\//, '')
    .replace(/^\.\//, '');

function validNodeStatus(value: unknown): SearchNodeStatus {
  if (value === 'open' || value === 'parked' || value === 'closed') return value;
  return 'open';
}

function validKnowledgeKind(value: unknown): KnowledgeItemKind {
  if (value === 'observation' || value === 'hypothesis' || value === 'conclusion') return value;
  return 'observation';
}

function validCooledStatus(value: unknown): CooledKnowledgeStatus {
  if (value === 'active' || value === 'needs_revalidation' || value === 'superseded') return value;
  return 'active';
}

function summarizeSearchEvent(op: Record<string, unknown>): string {
  const kind = String(op.op || 'event');
  switch (kind) {
    case 'bootstrap':
      return `初始化搜索树 · 根节点 ${op.root_id || 'N0'}（${op.operator || ''}）`;
    case 'add-node':
      return `新增搜索节点 ${op.id}（父节点 ${op.parent || '无'}）：${op.hypothesis || ''}`;
    case 'select':
      return `切换当前焦点节点 → ${op.id}`;
    case 'accept':
      return `登记接收任务 ${op.id} 的结果（不等同于精度通过）`;
    case 'node-status':
      return `更新节点 ${op.id} 状态 → ${op.status}`;
    case 'run':
      return `登记任务 ${op.id}（${op.stage}）→ 节点 ${op.search_node_id}${op.candidate_id ? ` · 候选 ${op.candidate_id}` : ''}`;
    case 'candidate':
      return `登记候选 ${op.id} → 节点 ${op.search_node_id}（父候选 ${op.parent_candidate || '无'}）`;
    case 'hot':
      return `写入热知识 ${op.id}（${op.kind || '知识'} · 关联 ${Array.isArray(op.nodes) ? op.nodes.join(', ') : ''}）`;
    case 'use-cooled':
      return `节点 ${op.search_node_id} 引入冷却知识 ${cleanPath(String(op.path || ''))}`;
    case 'promote-cooled':
      return `热知识 ${op.hot_id} rev.${op.revision} 经评审 ${op.review_id} 冷却为长期知识`;
    case 'invalidate-cooled':
      return `冷却知识状态变更 → ${op.status}（${op.reason || ''}）`;
    default:
      return `${kind}`;
  }
}

function computeTreeDepths(nodesMap: Map<string, SearchNodeView>, warn: (kind: string, message: string, paths: string[]) => void) {
  for (const node of nodesMap.values()) {
    node.childrenIds = [];
  }
  for (const node of nodesMap.values()) {
    if (!node.parentId) continue;
    const parent = nodesMap.get(node.parentId);
    if (!parent) {
      warn('missing_search_parent', `搜索节点 ${node.id} 的父节点 ${node.parentId} 不存在`, ['search/tree.json']);
      node.parentId = null;
      continue;
    }
    const ancestors = new Set<string>([node.id]);
    let cursor: string | null = parent.id;
    let cyclic = false;
    while (cursor) {
      if (ancestors.has(cursor)) {
        cyclic = true;
        break;
      }
      ancestors.add(cursor);
      cursor = nodesMap.get(cursor)?.parentId || null;
    }
    if (cyclic) {
      warn('cyclic_search_parent', `搜索节点 ${node.id} 存在父子环路，已断开父边`, ['search/tree.json']);
      node.parentId = null;
      continue;
    }
    parent.childrenIds.push(node.id);
  }
  const visit = (node: SearchNodeView, depth: number) => {
    node.depth = depth;
    for (const childId of node.childrenIds) {
      const child = nodesMap.get(childId);
      if (child) visit(child, depth + 1);
    }
  };
  for (const node of nodesMap.values()) {
    if (!node.parentId) visit(node, 0);
  }
}

export function applySearchTree(
  exp: Exploration,
  contents: Record<string, string>,
  model: HarnessModel
): boolean {
  const warnings = exp.harness?.warnings || [];
  const warn = (kind: string, message: string, paths: string[]) => warnings.push({ kind, message, paths });
  const recordByPath = (path?: string) => model.records.find((r) => r.path === path);

  const rawText = contents['search/tree.json'];
  if (!rawText) {
    warn('missing_search_tree', '未捕获原生 search/tree.json；保留文件记录，不推导搜索节点', []);
    exp.harness!.search = synthesizeFallbackSearchView(exp, model);
    return false;
  }

  let parsed: Record<string, any>;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    warn('invalid_search_tree', 'search/tree.json 不是合法的 JSON', ['search/tree.json']);
    exp.harness!.search = synthesizeFallbackSearchView(exp, model);
    return false;
  }

  if (!parsed || parsed.schema !== 'operator-harness/search@1' || typeof parsed.nodes !== 'object' || !parsed.nodes || Array.isArray(parsed.nodes)) {
    warn('invalid_search_tree', 'search/tree.json 协议版本未知或 nodes 无效；保留原文，不解释为搜索树', ['search/tree.json']);
    exp.harness!.search = synthesizeFallbackSearchView(exp, model);
    return false;
  }

  const recordPath = (value?: string) => cleanPath(projectRecordPath(value || '', parsed.project_id));
  const matchesReference = (ref: any): boolean => {
    const path = recordPath(ref?.path);
    return Boolean(path && /^[a-f0-9]{64}$/.test(ref?.sha256 || '') &&
      model.manifest.files.some(f => f.path === path && f.status === 'copied' && f.sha256 === ref.sha256));
  };
  const currentNodeId = typeof parsed.current_node === 'string' ? parsed.current_node : null;
  const nodesMap = new Map<string, SearchNodeView>();

  for (const [nodeId, rawNode] of Object.entries<any>(parsed.nodes)) {
    if (!rawNode || typeof rawNode !== 'object') continue;
    nodesMap.set(nodeId, {
      id: nodeId,
      parentId: typeof rawNode.parent === 'string' ? rawNode.parent : null,
      childrenIds: [],
      depth: 0,
      hypothesis: String(rawNode.hypothesis || ''),
      scope: String(rawNode.scope || ''),
      status: validNodeStatus(rawNode.status),
      isCurrent: nodeId === currentNodeId,
      runs: [],
      candidateIds: [],
      loopCount: null,
      usedCooledIds: [],
      hotKnowledgeIds: [],
    });
  }

  computeTreeDepths(nodesMap, warn);

  // Bind candidates to search nodes
  const rawCandidates = parsed.candidates && typeof parsed.candidates === 'object' ? parsed.candidates : {};
  for (const [cid, cinfo] of Object.entries<any>(rawCandidates)) {
    if (!matchesReference(cinfo?.manifest)) {
      warn('candidate_digest', `候选 ${cid} 的清单引用缺失或摘要不符，未建立关联`, ['search/tree.json']);
      continue;
    }
    const sNodeId = typeof cinfo?.search_node_id === 'string' ? cinfo.search_node_id : undefined;
    if (sNodeId && nodesMap.has(sNodeId)) {
      const sNode = nodesMap.get(sNodeId)!;
      if (!sNode.candidateIds.includes(cid)) sNode.candidateIds.push(cid);
      const candNode = exp.nodes.find((n) => n.id === cid);
      if (candNode?.harness) candNode.harness.searchNodeId = sNodeId;
    }
  }
  for (const rec of model.records) {
    if (rec.kind === 'candidate' && rec.fields.candidate_id && !rawCandidates[rec.fields.candidate_id] && rec.fields.search_node_id) {
      const cid = rec.fields.candidate_id;
      const sNodeId = rec.fields.search_node_id;
      const sNode = nodesMap.get(sNodeId);
      if (sNode && !sNode.candidateIds.includes(cid)) sNode.candidateIds.push(cid);
      const candNode = exp.nodes.find((n) => n.id === cid);
      if (candNode?.harness && !candNode.harness.searchNodeId) candNode.harness.searchNodeId = sNodeId;
    }
  }

  // Bind runs to search nodes
  const rawRuns = parsed.runs && typeof parsed.runs === 'object' ? parsed.runs : {};
  const orderedRuns = [...model.runs].sort(
    (a, b) => (a.createdAt || '').localeCompare(b.createdAt || '') || a.id.localeCompare(b.id)
  );
  const seenRunIds = new Set<string>();

  for (const run of orderedRuns) {
    seenRunIds.add(run.id);
    const rawRun = rawRuns[run.id];
    const taskRec = recordByPath(run.taskPath);
    if (rawRun?.task && !matchesReference(rawRun.task)) {
      warn('search_task_digest', `任务 ${run.id} 的冻结 TASK 引用不匹配，未建立搜索节点关联`, [run.taskPath || 'search/tree.json']);
      continue;
    }
    if (rawRun?.result && !matchesReference(rawRun.result)) {
      warn('search_result_digest', `任务 ${run.id} 的已接收 RESULT 引用不匹配，原文仍可查看`, [run.resultPath || 'search/tree.json']);
    }
    const resultRec = recordByPath(run.resultPath);
    const identities = [rawRun?.search_node_id, taskRec?.fields.search_node_id, resultRec?.fields.search_node_id].filter(Boolean);
    if (new Set(identities).size > 1 || (rawRun?.stage && rawRun.stage !== run.node)) {
      warn('run_binding_conflict', `任务 ${run.id} 的阶段或搜索节点绑定冲突，未建立关联`, [run.taskPath || 'search/tree.json']);
      continue;
    }
    const sNodeId =
      (typeof rawRun?.search_node_id === 'string' ? rawRun.search_node_id : undefined) ||
      taskRec?.fields.search_node_id ||
      resultRec?.fields.search_node_id;

    if (!sNodeId || !nodesMap.has(sNodeId)) continue;
    const sNode = nodesMap.get(sNodeId)!;
    const runFiles = model.manifest.files
      .filter((f) => f.path.startsWith(`runs/${run.id}/`))
      .map((f) => f.path);
    const measurementPath = runFiles.find((p) => /measurement\.json$/.test(p));
    const candManifest = model.records.find((r) => r.kind === 'candidate' && r.runId === run.id);
    const candidateId =
      (typeof rawRun?.candidate_id === 'string' ? rawRun.candidate_id : null) ||
      candManifest?.fields.candidate_id ||
      taskRec?.fields.candidate_id ||
      null;

    sNode.runs.push({
      id: run.id,
      searchNodeId: sNodeId,
      stage: String(rawRun?.stage || run.node || 'analysis'),
      candidateId,
      operation: run.operation || String(rawRun?.stage || run.id),
      status: run.status || 'unknown',
      createdAt: run.createdAt,
      parentRun: run.parentRun ? recordPath(run.parentRun) : undefined,
      taskPath: run.taskPath,
      resultPath: run.resultPath,
      measurementPath,
      files: runFiles,
    });
  }

  // Also include any run in rawRuns that wasn't in model.runs
  for (const [runId, rawRun] of Object.entries<any>(rawRuns)) {
    if (seenRunIds.has(runId) || !rawRun || typeof rawRun !== 'object') continue;
    const sNodeId = typeof rawRun.search_node_id === 'string' ? rawRun.search_node_id : undefined;
    if (!sNodeId || !nodesMap.has(sNodeId)) continue;
    const taskPath = recordPath(rawRun.task?.path);
    const resultPath = recordPath(rawRun.result?.path);
    const files = [taskPath, resultPath].filter(Boolean);
    warn('unarchived_search_run', `任务 ${runId} 仅在搜索索引中登记，执行状态未知`, ['search/tree.json', ...files]);
    nodesMap.get(sNodeId)!.runs.push({
      id: runId,
      searchNodeId: sNodeId,
      stage: String(rawRun.stage || 'analysis'),
      candidateId: typeof rawRun.candidate_id === 'string' ? rawRun.candidate_id : null,
      operation: `${rawRun.stage || 'run'} ${runId}`,
      status: 'unknown',
      taskPath: taskPath || undefined,
      resultPath: resultPath || undefined,
      files,
    });
  }

  for (const sNode of nodesMap.values()) {
    const loops = sNode.runs.map(run => recordByPath(run.taskPath)?.fields.loop_id);
    if (loops.length && loops.every(Boolean)) sNode.loopCount = new Set(loops).size;
  }

  // Parse Hot Knowledge
  const rawHot = parsed.hot && typeof parsed.hot === 'object' ? parsed.hot : {};
  const hotPathsById = new Map<string, { path: string; sha256: string }[]>();

  for (const [hotId, entries] of Object.entries<any>(rawHot)) {
    if (!Array.isArray(entries)) continue;
    const list = hotPathsById.get(hotId) || [];
    for (const item of entries) {
      const p = recordPath(item?.path);
      if (p && !list.some((x) => x.path === p)) {
        list.push({ path: p, sha256: String(item?.sha256 || '') });
      }
    }
    hotPathsById.set(hotId, list);
  }

  for (const file of model.manifest.files) {
    const m = file.path.match(/^knowledge\/hot\/([^/]+)\/[^/]+\.json$/);
    if (!m) continue;
    const hotId = m[1];
    const list = hotPathsById.get(hotId) || [];
    if (!list.some((x) => x.path === file.path)) {
      list.push({ path: file.path, sha256: file.sha256 || '' });
    }
    hotPathsById.set(hotId, list);
  }

  const hotKnowledge: HotKnowledgeView[] = [];
  for (const [hotId, refList] of hotPathsById.entries()) {
    const revisions: HotKnowledgeRevisionView[] = [];
    for (const ref of refList) {
      const text = contents[ref.path];
      if (!text) continue;
      if (!matchesReference(ref)) {
        warn('hot_digest_mismatch', `热知识引用摘要不匹配，未解释：${ref.path}`, [ref.path]);
        continue;
      }
      try {
        const obj = JSON.parse(text);
        const evidenceSources = Array.isArray(obj.evidence_sources)
          ? obj.evidence_sources.map((s: unknown) => recordPath(String(s)))
          : [];
        const evidence = Array.isArray(obj.evidence)
          ? obj.evidence.map((ev: any, idx: number) => {
              const evPath = recordPath(ev?.path);
              if (!matchesReference(ev)) warn('hot_evidence_unavailable', `热知识 ${hotId} 的证据缺失或摘要不符：${evPath}`, [evPath]);
              return {
                path: evPath,
                sha256: String(ev?.sha256 || ''),
                sourcePath: evidenceSources[idx] || evPath,
                content: model.manifest.files.some(f => f.path === evPath && f.status === 'copied' && f.sha256 === ev.sha256) ? contents[evPath] : undefined,
              };
            })
          : [];
        const relatedNodes: string[] = Array.isArray(obj.nodes) ? obj.nodes.map(String) : [];
        const owners = new Set<string>();
        for (const path of evidenceSources) {
          const match = /^runs\/([^/]+)\/RESULT\.md$/.exec(path);
          const run = match && rawRuns[match[1]];
          if (run && recordPath(run.result?.path) === path && matchesReference(run.result) &&
              evidence.some((ev: {sourcePath:string; sha256:string}) => ev.sourcePath === path && ev.sha256 === run.result.sha256)) {
            owners.add(run.search_node_id);
          }
        }
        const inferred = owners.size === 1 ? [...owners][0] : relatedNodes.length === 1 ? relatedNodes[0] : undefined;
        const producer = obj.producer_node_id || inferred;
        revisions.push({
          id: String(obj.id || hotId),
          revision: Number(obj.revision) || revisions.length + 1,
          path: ref.path,
          sha256: ref.sha256,
          kind: validKnowledgeKind(obj.kind),
          statement: String(obj.statement || ''),
          applicability: String(obj.applicability || ''),
          limitations: String(obj.limitations || ''),
          nodes: producer && relatedNodes.includes(producer) && nodesMap.has(producer) ? [producer] : [],
          relatedNodes,
          evidence,
          evidenceSources,
        });
      } catch {
        warn('invalid_hot_knowledge', `热知识文件解析失败：${ref.path}`, [ref.path]);
      }
    }
    if (!revisions.length) continue;
    revisions.sort((a, b) => a.revision - b.revision);
    const latest = revisions[revisions.length - 1];
    for (const nId of latest.nodes) {
      const sNode = nodesMap.get(nId);
      if (sNode && !sNode.hotKnowledgeIds.includes(hotId)) {
        sNode.hotKnowledgeIds.push(hotId);
      }
    }
    hotKnowledge.push({ id: hotId, latest, revisions });
  }
  hotKnowledge.sort((a, b) => a.id.localeCompare(b.id));

  // Parse Cooled Knowledge
  const cooledByPath = new Map<string, CooledKnowledgeView>();
  const cooledCandidatePaths = new Set<string>();
  for (const file of model.manifest.files) {
    if (/^knowledge\/cooled\/[^/]+\.json$/.test(file.path)) {
      cooledCandidatePaths.add(file.path);
    }
  }
  const rawCooledInputs = Array.isArray(parsed.cooled_inputs) ? parsed.cooled_inputs : [];
  for (const inp of rawCooledInputs) {
    const p = recordPath(inp?.artifact?.path);
    if (p) cooledCandidatePaths.add(p);
  }

  for (const path of cooledCandidatePaths) {
    const text = contents[path];
    if (!text) continue;
    try {
      const obj = JSON.parse(text);
      if (obj.schema !== 'operator-harness/cooled@1' || !['active', 'needs_revalidation', 'superseded'].includes(obj.status)) {
        warn('unsupported_cooled', `冷却知识协议或状态无法解释：${path}；保留原文`, [path]);
        continue;
      }
      const id = String(obj.id || path.replace(/^knowledge\/cooled\/|\.json$/g, ''));
      const version = String(obj.version || 'v1');
      const fullKey = `${id}--${version}`;
      const manifestEntry = model.manifest.files.find((f) => f.path === path);
      const prov = obj.provenance && typeof obj.provenance === 'object' ? obj.provenance : undefined;
      const rev = obj.review && typeof obj.review === 'object' ? obj.review : undefined;
      cooledByPath.set(path, {
        id,
        version,
        fullKey,
        path,
        sha256: manifestEntry?.sha256 || String(obj.approval_sha256 || ''),
        status: validCooledStatus(obj.status),
        kind: validKnowledgeKind(obj.kind),
        statement: String(obj.statement || ''),
        applicability: String(obj.applicability || ''),
        limitations: String(obj.limitations || ''),
        evidenceSummary: String(obj.evidence_summary || ''),
        provenance: prov
          ? {
              operator: String(prov.operator || ''),
              taskContract: String(prov.task_contract || ''),
              hotId: String(prov.hot_id || ''),
              revision: Number(prov.revision) || 1,
              hotSha256: String(prov.hot_sha256 || ''),
              nodes: Array.isArray(prov.nodes) ? prov.nodes.map(String) : [],
            }
          : undefined,
        review: rev
          ? {
              id: String(rev.id || ''),
              reviewer: String(rev.reviewer || ''),
              reviewedAt: String(rev.reviewed_at || ''),
              delivery: String(rev.delivery || ''),
            }
          : undefined,
        supersedesSha256: obj.supersedes_sha256 ? String(obj.supersedes_sha256) : undefined,
        invalidationReason: obj.reason ? String(obj.reason) : undefined,
        usedByNodes: [],
      });
    } catch {
      warn('invalid_cooled_knowledge', `冷却知识文件解析失败：${path}`, [path]);
    }
  }

  for (const item of cooledByPath.values()) {
    if (!item.supersedesSha256) continue;
    const previous = [...cooledByPath.values()].find(c => c.sha256 === item.supersedesSha256 && c.id === item.id && c.path !== item.path);
    if (previous) previous.status = 'superseded';
  }

  for (const inp of rawCooledInputs) {
    const sNodeId = String(inp?.search_node_id || '');
    const artifactPath = recordPath(inp?.artifact?.path);
    const reason = String(inp?.reason || '');
    const sha256 = String(inp?.artifact?.sha256 || '');
    const cooled = cooledByPath.get(artifactPath);
    if (!cooled) {
      warn('missing_cooled_input', `搜索节点 ${sNodeId} 引用的冷却知识未找到：${artifactPath}`, [
        'search/tree.json',
        artifactPath,
      ]);
      continue;
    }
    const captured = model.manifest.files.find(f => f.path === artifactPath);
    if (!captured || captured.status !== 'copied' || !/^[a-f0-9]{64}$/.test(sha256) || captured.sha256 !== sha256) {
      warn('cooled_input_digest_mismatch', `搜索节点 ${sNodeId} 引用的冷却知识摘要缺失或不匹配，未建立关联：${artifactPath}`, ['search/tree.json', artifactPath]);
      continue;
    }
    cooled.usedByNodes.push({ searchNodeId: sNodeId, reason, artifactPath, sha256 });
    const sNode = nodesMap.get(sNodeId);
    if (sNode && !sNode.usedCooledIds.includes(cooled.fullKey)) {
      sNode.usedCooledIds.push(cooled.fullKey);
    }
    if (cooled.status !== 'active') {
      warn(
        'cooled_knowledge_status',
        `搜索节点 ${sNodeId} 引用了状态为 ${cooled.status} 的冷却知识 ${cooled.fullKey}${cooled.invalidationReason ? `（${cooled.invalidationReason}）` : ''}`,
        [artifactPath]
      );
    }
  }

  for (const file of model.manifest.files) {
    if (!file.path.startsWith('knowledge/documents/') || !file.path.endsWith('.json')) continue;
    try {
      const doc = JSON.parse(contents[file.path]);
      if (doc.schema !== 'harness-vis/document-input@1' || !matchesReference(doc.source)) continue;
      const fullKey = doc.id + '--' + doc.version;
      const node = nodesMap.get(doc.search_node_id);
      if (!node) continue;
      cooledByPath.set(file.path, {
        id: doc.id, version: doc.version, fullKey, path: file.path, sha256: file.sha256 || '',
        status: 'active', kind: 'observation', statement: doc.statement, applicability: doc.applicability,
        limitations: doc.limitations, evidenceSummary: doc.citation, documentPath: recordPath(doc.source.path),
        usedByNodes: [{searchNodeId: node.id, reason: doc.reason, artifactPath: file.path, sha256: file.sha256 || ''}],
      });
      node.usedCooledIds.push(fullKey);
    } catch {
      warn('invalid_document_input', '仓库文档引用无法解析', [file.path]);
    }
  }

  const cooledKnowledge = [...cooledByPath.values()].sort((a, b) => a.fullKey.localeCompare(b.fullKey));

  for (const file of model.manifest.files) {
    if (!/^knowledge\/activities\/[^/]+\.json$/.test(file.path)) continue;
    try {
      const entry = JSON.parse(contents[file.path]);
      const hot = hotKnowledge.find(h => h.id === entry.hot_id);
      const run = nodesMap.get(entry.search_node_id)?.runs.find(r => r.id === entry.run_id);
      if (entry.schema !== 'harness-vis/knowledge-activity@1' ||
          !['use-hot', 'nominate-cooled'].includes(entry.kind) || !run ||
          typeof entry.reason !== 'string' || !entry.reason.trim() ||
          !hot?.revisions.some(r => r.revision === entry.revision && r.sha256 === entry.hot_sha256)) {
        throw new Error('invalid binding');
      }
      (hot.activities ||= []).push({kind:entry.kind, revision:entry.revision, node:entry.search_node_id,
        run:run.id, reason:entry.reason, path:file.path});
    } catch {
      warn('invalid_knowledge_activity', '知识活动缺少有效的 Run 或修订引用', [file.path]);
    }
  }

  // Connect hot knowledge promotions to cooled knowledge
  for (const cooled of cooledKnowledge) {
    const hotId = cooled.provenance?.hotId;
    if (!hotId) continue;
    const hot = hotKnowledge.find((h) => h.id === hotId);
    const revision = hot?.revisions.find(r => r.revision === cooled.provenance?.revision && r.sha256 === cooled.provenance?.hotSha256);
    if (cooled.review?.reviewer && cooled.review?.id && cooled.review?.reviewedAt && hot && revision && revision.revision === hot.latest.revision && (!hot.promotedCooledId || cooled.status === 'active')) {
      hot.promotedCooledId = cooled.fullKey;
    }
  }

  // Parse events
  const rawEvents = Array.isArray(parsed.events) ? parsed.events : [];
  const events: SearchEventView[] = rawEvents.map((ev: any, idx: number) => {
    const op = ev?.operation && typeof ev.operation === 'object' ? ev.operation : {};
    return {
      at: String(ev?.at || ''),
      eventId: String(op.event_id || `ev-${idx + 1}`),
      op: String(op.op || 'event'),
      summary: summarizeSearchEvent(op),
      raw: op,
    };
  });

  // Populate legacy exp.knowledgeItems for compatibility
  const kItems: KnowledgeRecord[] = [];
  for (const h of hotKnowledge) {
    kItems.push({
      id: h.id,
      explorationId: exp.id,
      category: 'optimization_experience',
      claimType: h.latest.kind === 'conclusion' ? 'fact' : h.latest.kind,
      title: h.latest.statement,
      version: `rev.${h.latest.revision}`,
      contentStatus: h.promotedCooledId ? 'cooled' : 'hot',
      applicability: h.latest.applicability,
      unverifiedScope: h.latest.limitations,
      body: h.latest.statement,
      supportingNodeIds: h.latest.nodes,
      counterexampleNodeIds: [],
      evidenceAttemptIds: h.latest.evidenceSources,
    });
  }
  for (const c of cooledKnowledge) {
    kItems.push({
      id: c.fullKey,
      explorationId: exp.id,
      category: 'basic_knowledge',
      claimType: c.kind === 'conclusion' ? 'fact' : c.kind,
      title: c.statement,
      version: c.version,
      contentStatus: 'cooled',
      applicability: c.applicability,
      unverifiedScope: c.limitations,
      body: `${c.statement}\n\n证据摘要：${c.evidenceSummary}`,
      supportingNodeIds: c.provenance?.nodes || c.usedByNodes.map((u) => u.searchNodeId),
      counterexampleNodeIds: [],
      evidenceAttemptIds: [],
    });
  }
  exp.knowledgeItems = kItems;

  // A plan can cite prior search nodes on other branches of the search tree.
  const crossLinks: { source: string; target: string }[] = [];
  for (const node of nodesMap.values()) {
    const ref = parsed.nodes[node.id]?.plan;
    if (!ref || !matchesReference(ref)) continue;
    try {
      const plan = JSON.parse(contents[recordPath(ref.path)]);
      if (plan.schema !== 'operator-harness/search-plan@1' || plan.project_id !== parsed.project_id ||
          !Array.isArray(plan.prior_nodes)) continue;
      for (const source of new Set<string>(plan.prior_nodes)) {
        if (source !== node.id && source !== node.parentId && nodesMap.has(source)) {
          crossLinks.push({ source, target: node.id });
        }
      }
    } catch {
      warn('invalid_search_plan', '搜索计划无法解析', [recordPath(ref.path)]);
    }
  }

  exp.harness!.search = {
    schema: String(parsed.schema || 'operator-harness/search@1'),
    operator: String(parsed.operator || exp.operatorName || ''),
    taskContract: String(parsed.task_contract || ''),
    currentNodeId,
    searchNodes: [...nodesMap.values()],
    crossLinks,
    hotKnowledge,
    cooledKnowledge,
    events,
  };

  return true;
}

function synthesizeFallbackSearchView(exp: Exploration, _model: HarnessModel): HarnessSearchView {
  return {schema:'operator-harness/search-unavailable', operator:exp.operatorName,
    taskContract:'',currentNodeId:null,searchNodes:[],hotKnowledge:[],cooledKnowledge:[],events:[]};
}
