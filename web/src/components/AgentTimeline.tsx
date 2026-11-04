import { recordedStatus } from '../harnessAdapter';
import React, { useMemo, useState } from 'react';
import {
  Exploration,
  NodeRecord,
  SelectionState,
  STAGE_METAS,
  StageAttempt,
  StageType,
} from '../types';
import { ChevronIcon, ContentStateIcon, StatusIcon } from '../icons';

interface AgentTimelineProps {
  projectId?: string;
  exploration: Exploration;
  selection: SelectionState;
  onSelectNode: (nodeId: string) => void;
  onSelectAttempt: (nodeId: string, stageType: StageType, attemptId: string) => void;
}

const STATUS_LABELS: Record<string, string> = {
  completed: '已完成',
  running: '进行中',
  failed: '失败',
  stopped: '已停止',
  skipped: '已跳过',
  pending: '未开始',
};

export const AgentTimeline: React.FC<AgentTimelineProps> = ({
  exploration,
  projectId,
  selection,
  onSelectNode,
  onSelectAttempt,
}) => {
  const storageKey = `agent-folds:${projectId || exploration.id}`;
  const [collapsed, setCollapsed] = useState<string[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(storageKey) || '[]'); } catch { return []; }
  });
  const updateCollapsed = (ids: string[]) => {
    setCollapsed(ids);
    try { sessionStorage.setItem(storageKey, JSON.stringify(ids)); } catch { /* storage optional */ }
  };
  const nodeMap = useMemo(() => {
    const map: Record<string, NodeRecord> = {};
    exploration.nodes.forEach((n) => {
      map[n.id] = n;
    });
    return map;
  }, [exploration.nodes]);

  const stageMetaMap = useMemo(() => {
    const map: Record<string, { index: number; label: string; shortLabel: string }> = {};
    STAGE_METAS.forEach((m) => {
      map[m.type] = m;
    });
    return map;
  }, []);

  const agentTrajectories = useMemo(() => {
    return exploration.agents.map((agent) => {
      const attempts: StageAttempt[] = [];
      const participatedNodeIds = new Set<string>();

      exploration.nodes.forEach((node) => {
        if (node.agentIds.includes(agent.id)) {
          participatedNodeIds.add(node.id);
        }
        (node.stageMetas || STAGE_METAS).forEach((meta) => {
          const st = node.stages[meta.type];
          st.attempts.forEach((att) => {
            if (att.agentId === agent.id) {
              if (!exploration.harness || !attempts.some(a=>a.id===att.id)) attempts.push(att);
              participatedNodeIds.add(node.id);
            }
          });
        });
      });

      attempts.sort((a, b) => a.startedAt.localeCompare(b.startedAt));

      return {
        agent,
        participatedNodes: Array.from(participatedNodeIds).map((id) => nodeMap[id]),
        attempts,
      };
    });
  }, [exploration, nodeMap]);

  return (
    <div className="agent-timeline-container">
      <div className="agent-timeline-toolbar">
        <div className="agent-toolbar-info">
          <strong>{exploration.harness ? 'Agent 执行记录' : 'Agent Session 轨迹'}</strong>
          <span>
            {exploration.harness ? '按执行者查看 Run；共享分析仅计一次，可在上方切换关联候选。' : '一个 Agent 对应一个 session；按节点与阶段查看其迭代和交付。'}
          </span>
        </div>
        <div className="agent-fold-actions">
          <button className="toolbar-btn" onClick={() => updateCollapsed([])}>全部展开</button>
          <button className="toolbar-btn" onClick={() => updateCollapsed(exploration.agents.map(a => a.id))}>全部折叠</button>
        </div>
      </div>

      <div className="agent-lanes-scroll">
        {agentTrajectories.map(({ agent, participatedNodes, attempts }) => {
          const isAgentHighlighted =
            selection.nodeId !== null &&
            participatedNodes.some((n) => n?.id === selection.nodeId);

          const folded = collapsed.includes(agent.id);
          return (
            <section
              key={agent.id}
              className={`agent-lane ${folded ? 'lane-collapsed' : ''} ${isAgentHighlighted ? 'lane-highlighted' : ''}`}
            >
              <div className="agent-lane-header">
                <div className="agent-title-row">
                  <button className="agent-fold-toggle" aria-expanded={!folded}
                    aria-controls={`agent-track-${agent.id}`}
                    onClick={() => updateCollapsed(folded ? collapsed.filter(id => id !== agent.id) : [...collapsed, agent.id])}>
                    <ChevronIcon direction={folded ? 'right' : 'down'} size={14} />
                    <strong title={`Session: ${agent.id} · ${agent.role} · 心跳: ${agent.lastHeartbeat}`}>{agent.name}</strong>
                    <span>{participatedNodes.length} 节点 · {attempts.length} 次执行</span>
                  </button>
                  <span className={`agent-status-pill agent-${agent.status}`}>
                    {agent.id === 'unrecorded-executor' ? '身份未记录' : agent.historicalOnly ? '历史 session' : agent.status === 'active' ? '在线运行' : agent.status === 'stopped' ? '已结束' : '空闲待命'}
                  </span>

                </div>
                <p className="agent-role" hidden={folded} title={`Session: ${agent.id} · ${agent.role}`}>Session: {agent.id} · {agent.role}</p>

                <div className="agent-nodes-bar" hidden={folded}>
                  <span className="nodes-bar-label">节点：</span>
                  {participatedNodes.map((node) => {
                    if (!node) return null;
                    const isNodeSelected = selection.nodeId === node.id;
                    const isSharedMultiAgent = node.agentIds.length > 1;
                    return (
                      <button
                        key={node.id}
                        type="button"
                        className={`shared-node-chip ${
                          isNodeSelected ? 'chip-selected' : ''
                        }`}
                        onClick={() => onSelectNode(node.id)}
                        title={
                          isSharedMultiAgent
                            ? `多 Agent 协同节点 (${node.agentIds.join(', ')})`
                            : node.title
                        }
                      >
                        <strong>{node.id}</strong>
                        <span>{node.branchLabel}</span>
                        {isSharedMultiAgent && (
                          <span className="multi-agent-tag">
                            {node.agentIds.length} Agents 协同
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div id={`agent-track-${agent.id}`} className="agent-attempts-track" role="list" hidden={folded}>
                {attempts.map((att) => {
                  const owningNode = nodeMap[att.nodeId];
                  const stageMeta = nodeMap[att.nodeId]?.stageMetas?.find(m=>m.type===att.stageType) || stageMetaMap[att.stageType];
                  const isAttemptSelected =
                    selection.attemptId === att.id ||
                    (selection.nodeId === att.nodeId &&
                      selection.stageType === att.stageType &&
                      !selection.attemptId);
                  const isNodeActive = selection.nodeId === att.nodeId;

                  return (
                    <div
                      key={att.id}
                      role="listitem"
                      className={`timeline-attempt-item status-${att.status} ${
                        isAttemptSelected ? 'item-selected' : ''
                      } ${isNodeActive ? 'item-node-match' : ''}`}
                      onClick={() =>
                        onSelectAttempt(att.nodeId, att.stageType, att.id)
                      }
                    >
                      <div className="attempt-time-rail">
                        <span className="attempt-time">{att.startedAt}</span>
                        <span className="attempt-dot" />
                        {att.endedAt && (
                          <span className="attempt-end-time">{att.endedAt}</span>
                        )}
                      </div>

                      <div className="attempt-body">
                        <div className="attempt-top-row">
                          <span className="attempt-owner-node">
                            {att.nodeId}
                          </span>
                          <span className="attempt-stage-name">
                            {stageMeta?.index}. {stageMeta?.label} ·{' '}
                            <strong>{att.code}</strong>
                            {att.kernelVersionLabel ? ` (@${att.kernelVersionLabel})` : ''}
                          </span>
                          <span className={`exec-badge exec-${att.status}`}>
                            <StatusIcon status={att.status} size={11} />
                            <span>{owningNode?.harness ? recordedStatus[att.recordedStatus || 'unknown'] || att.recordedStatus : STATUS_LABELS[att.status]}</span>
                          </span>
                          {owningNode && !owningNode.harness && (
                            <span
                              className={`content-badge content-${owningNode.contentStatus}`}
                            >
                              <ContentStateIcon
                                status={owningNode.contentStatus}
                                size={10}
                              />
                              <span>{owningNode.contentStatus}</span>
                            </span>
                          )}
                        </div>

                        <p className="attempt-summary">{att.summary}</p>

                        {(att.dependsOnAttemptIds.length > 0 ||
                          att.reusedFromAttemptId ||
                          att.failureOrSkipReason) && (
                          <div className="attempt-meta-row">
                            {att.dependsOnAttemptIds.length > 0 && (
                              <span className="dep-tag">
                                依赖上游: {att.dependsOnAttemptIds.join(', ')}
                              </span>
                            )}
                            {att.reusedFromAttemptId && (
                              <span className="reuse-tag">
                                复用验证: {att.reusedFromAttemptId}
                              </span>
                            )}
                            {att.failureOrSkipReason && (
                              <span className="fail-reason-tag">
                                诊断: {att.failureOrSkipReason}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
};
