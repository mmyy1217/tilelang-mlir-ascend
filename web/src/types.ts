export type ExecutionStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped'
  | 'stopped';

export type ContentStatus = 'hot' | 'ready-for-cool' | 'cooled';

export type StageType =
  | 'read_info'
  | 'plan_idea'
  | 'gen_kernel'
  | 'compile'
  | 'verify_correctness'
  | 'bench_perf'
  | 'profiling'
  | 'summarize';

export interface StageMeta {
  type: StageType;
  index: number;
  shortLabel: string;
  label: string;
  description: string;
}

export const STAGE_METAS: StageMeta[] = [
  { type: 'read_info', index: 1, shortLabel: '读', label: '读取信息', description: '读取已有信息与优化依据' },
  { type: 'plan_idea', index: 2, shortLabel: '思', label: '形成优化思路', description: '分析瓶颈并提出当前节点改动方案' },
  { type: 'gen_kernel', index: 3, shortLabel: '生', label: '生成 kernel', description: '产出当前方案的 TileLang 内核代码' },
  { type: 'compile', index: 4, shortLabel: '编', label: '编译', description: '通过 tilelangir / bishengir 编译生成算子二进制' },
  { type: 'verify_correctness', index: 5, shortLabel: '验', label: '正确性验证', description: '对标 Golden 参考实现与容差校验精度' },
  { type: 'bench_perf', index: 6, shortLabel: '测', label: '性能测量', description: '按预热、重复次数与设备同步协议测量耗时' },
  { type: 'profiling', index: 7, shortLabel: '剖', label: 'profiling', description: '采集 msprof 流水线利用率与硬件瓶颈摘要' },
  { type: 'summarize', index: 8, shortLabel: '结', label: '获取结果并总结', description: '总结因果解释、未证实假设与待冷却候选标记' },
];

export type ResourceCategory =
  | 'kernel'
  | 'idea'
  | 'summary'
  | 'experiment'
  | 'profile'
  | 'artifact';

export interface ProfileSummary {
  cubeUtil: number;
  vecUtil: number;
  mte2Util: number;
  pipeFixWaitPct: number;
  l1UsageKb: number;
  ubUsageKb: number;
  bottleneckTag: string;
  rawArchiveName: string;
  rawArchiveSize: string;
}

export interface ExperimentProtocol {
  hardware: string;
  cannVersion: string;
  referenceImpl: string;
  inputShape: string;
  dtype: string;
  atol: string;
  rtol: string;
  maxAbsDiff?: string;
  correctnessPassed: boolean;
  warmupRuns: number;
  repeatRuns: number;
  timingBoundary: string;
  syncMethod: string;
  medianUs?: number;
  p95Us?: number;
  minUs?: number;
  maxUs?: number;
  samplesUs?: number[];
  isComparable: boolean;
  incomparableReason?: string;
}

export interface ResourceVersion {
  id: string;
  resourceId: string;
  version: string;
  title: string;
  filename: string;
  category: ResourceCategory;
  sha256: string;
  ownerNodeId: string;
  contentStatus: ContentStatus;
  createdAt: string;
  sizeBytes: number;
  isBinary?: boolean;
  missingReason?: string;
  content: string;
  experimentData?: ExperimentProtocol;
  profileData?: ProfileSummary;
}

export interface ResourceLink {
  id: string;
  nodeId: string;
  stageType?: StageType;
  attemptId?: string;
  resourceVersionId: string;
  role: 'input' | 'output' | 'shared_baseline';
  sourceNodeId: string;
  isSharedReference: boolean;
  note?: string;
}

export interface StageAttempt {
  sourceStatus?: ExecutionStatus;
  id: string;
  code: string;
  nodeId: string;
  stageType: StageType;
  round: number;
  agentId: string;
  status: ExecutionStatus;
  startedAt: string;
  endedAt?: string;
  dependsOnAttemptIds: string[];
  kernelVersionLabel?: string;
  inputResourceVersionIds: string[];
  outputResourceVersionIds: string[];
  summary: string;
  failureOrSkipReason?: string;
  reusedFromAttemptId?: string;
}

export interface StageRecord {
  stageType: StageType;
  status: ExecutionStatus;
  currentAttemptId?: string;
  attempts: StageAttempt[];
  missingOutputReason?: string;
}

export interface NodeRecord {
  id: string;
  explorationId: string;
  parentId: string | null; // Legacy snapshot compatibility
  parentIds?: string[];
  depth: number;
  branchLabel: string;
  title: string;
  hypothesis: string;
  changeSummary: string;
  derivationNote: string;
  primaryKernelVersionId?: string;
  agentIds: string[];
  executionStatus: ExecutionStatus;
  contentStatus: ContentStatus;
  currentStage: StageType;
  latencyUs?: number;
  speedupVsBaseline?: number;
  correctnessPassed: boolean;
  stopOrFailReason?: string;
  pipelineMetrics?: {
    cubePct: number;
    vecPct: number;
    mte2Pct: number;
    bottleneck: string;
  };
  stages: Record<StageType, StageRecord>;
}

export interface ReferenceRecord {
  id: string;
  explorationId: string;
  sourceNodeId: string;
  targetNodeId: string;
  purpose: 'derivation' | 'cross_branch' | 'reference' | 'support' | 'refute';
  referencedNodeId?: string;
  referencedKnowledgeId?: string;
  referencedKnowledgeVersion?: string;
  referencedResourceVersionId?: string;
  title: string;
  rationale: string;
  reverifiedInAttemptId?: string;
  reverifiedStatus: 'verified' | 'pending_reverify' | 'failed_reverify';
}

export interface AgentRecord {
  historicalOnly?: boolean;
  id: string;
  name: string;
  role: string;
  status: 'active' | 'idle' | 'stopped';
  lastHeartbeat: string;
  currentNodeId?: string;
  currentStage?: StageType;
}

export interface KnowledgeRecord {
  id: string;
  explorationId: string;
  category: 'basic_knowledge' | 'optimization_experience';
  claimType: 'fact' | 'observation' | 'hypothesis' | 'method';
  title: string;
  version: string;
  contentStatus: ContentStatus;
  applicability: string;
  unverifiedScope: string;
  body: string;
  supportingNodeIds: string[];
  counterexampleNodeIds: string[];
  evidenceAttemptIds: string[];
}

export interface GraphRelation {
  id: string;
  kind: 'derivation' | 'reference' | 'support' | 'refute';
  sourceType: 'node' | 'knowledge'; sourceId: string;
  targetType: 'node' | 'knowledge'; targetId: string;
  rationale: string;
  resourceVersionId?: string;
  knowledgeVersion?: string;
  evidenceAttemptIds?: string[];
}
export interface Exploration {
  graphRelations?: GraphRelation[];
  forkSource?: { explorationId: string; title: string; eventSeq: number; capturedAt: string };
  id: string;
  title: string;
  operatorName: string;
  targetGoal: string;
  inputDomain: string;
  inputShapes?: string[];
  environment: string;
  baselineRef: {
    id: string;
    version: string;
    latencyUs: number;
    description: string;
  };
  budget: {
    usedEvaluations: number;
    reservedEvaluations: number;
    maxEvaluations: number;
    elapsedMinutes: number;
    maxMinutes: number;
    noGainWindow: number;
    minGainPct: number;
  };
  coordinatorStatus: 'running' | 'stopping' | 'stopped' | 'completed';
  stopReason?: string;
  bestValidNodeId: string;
  unfinishedDirections: string[];
  version?: number;
  lastEventSeq?: number;
  nodes: NodeRecord[];
  agents: AgentRecord[];
  references: ReferenceRecord[];
  resources: Record<string, ResourceVersion>;
  resourceLinks: ResourceLink[];
  knowledgeItems: KnowledgeRecord[];
}

export interface SelectionState {
  knowledgeId?: string | null;
  explorationId: string;
  viewMode: 'tree' | 'agent';
  nodeId: string | null;
  stageType: StageType | null;
  attemptId: string | null;
  edgeId: string | null;
  compareNodeId: string | null;
  rightTab: 'inspector' | 'compare' | 'candidates';
  showAllCrossRefs: boolean;
  expandedNodeIds: string[];
}
