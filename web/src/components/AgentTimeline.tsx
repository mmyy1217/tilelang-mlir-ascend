import React, { useMemo } from 'react';
import {
  Exploration,
  NodeRecord,
  SelectionState,
  STAGE_METAS,
  StageAttempt,
  StageType,
} from '../types';
import { ContentStateIcon, StatusIcon } from '../icons';

interface AgentTimelineProps {
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
  selection,
  onSelectNode,
  onSelectAttempt,
}) => {
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
        STAGE_METAS.forEach((meta) => {
          const st = node.stages[meta.type];
          st.attempts.forEach((att) => {
            if (att.agentId === agent.id) {
              attempts.push(att);
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
          <strong>多 Agent 协同轨迹视图</strong>
          <span>
            按时间轴展示各 Agent 在不同搜索树节点内执行的 StageAttempt；多 Agent 协同同一节点时共享唯一节点 ID，不复制节点或资源。
          </span>
        </div>
      </div>

      <div className="agent-lanes-scroll">
        {agentTrajectories.map(({ agent, participatedNodes, attempts }) => {
          const isAgentHighlighted =
            selection.nodeId !== null &&
            participatedNodes.some((n) => n?.id === selection.nodeId);

          return (
            <section
              key={agent.id}
              className={`agent-lane ${isAgentHighlighted ? 'lane-highlighted' : ''}`}
            >
              <div className="agent-lane-header">
                <div className="agent-title-row">
                  <h3 className="agent-name">{agent.name}</h3>
                  <span className={`agent-status-pill agent-${agent.status}`}>
                    {agent.status === 'active' ? '在线运行' : '空闲待命'}
                  </span>
                  <span className="agent-heartbeat">心跳: {agent.lastHeartbeat}</span>
                </div>
                <p className="agent-role">{agent.role}</p>

                <div className="agent-nodes-bar">
                  <span className="nodes-bar-label">参与节点 (共享节点身份):</span>
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

              <div className="agent-attempts-track" role="list">
                {attempts.map((att) => {
                  const owningNode = nodeMap[att.nodeId];
                  const stageMeta = stageMetaMap[att.stageType];
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
                            归属节点 {att.nodeId}
                          </span>
                          <span className="attempt-stage-name">
                            Stage {stageMeta?.index}. {stageMeta?.label} · Attempt{' '}
                            <strong>{att.code}</strong>
                            {att.kernelVersionLabel ? ` (@${att.kernelVersionLabel})` : ''}
                          </span>
                          <span className={`exec-badge exec-${att.status}`}>
                            <StatusIcon status={att.status} size={11} />
                            <span>{STATUS_LABELS[att.status]}</span>
                          </span>
                          {owningNode && (
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
