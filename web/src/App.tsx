import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { EXPLORATIONS } from './mockData';
import { Exploration, SelectionState, StageType } from './types';
import { TreeCanvas } from './components/TreeCanvas';
import { AgentTimeline } from './components/AgentTimeline';
import { RightInspector } from './components/RightInspector';
import { LinkShareIcon, TimelineIcon, TreeIcon } from './icons';

type SseSyncState = 'connecting' | 'connected' | 'reconnecting' | 'paused' | 'static_fallback';

function parseInitialSelection(): SelectionState {
  const params = new URLSearchParams(window.location.search);
  const viewParam = params.get('view');
  const nodeParam = params.get('node');
  const stageParam = params.get('stage') as StageType | null;
  const attemptParam = params.get('attempt');
  const edgeParam = params.get('edge');

  return {
    explorationId: EXPLORATIONS[0].id,
    viewMode: viewParam === 'agent' || viewParam === 'timeline' ? 'agent' : 'tree',
    nodeId: nodeParam || 'N4',
    stageType: stageParam || null,
    attemptId: attemptParam || null,
    edgeId: edgeParam || null,
    compareNodeId: 'N1',
    rightTab: 'inspector',
    showAllCrossRefs: true,
    expandedNodeIds: [],
  };
}

export const App: React.FC = () => {
  const [selection, setSelection] = useState<SelectionState>(parseInitialSelection);
  const [liveExploration, setLiveExploration] = useState<Exploration | null>(null);
  const [sseState, setSseState] = useState<SseSyncState>('connecting');
  const [eventWatermark, setEventWatermark] = useState<number>(0);
  const [lastEventSummary, setLastEventSummary] = useState<string>('等待快照水位...');
  const [leftWidthPct, setLeftWidthPct] = useState<number>(50);
  const [copiedNotice, setCopiedNotice] = useState<boolean>(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState<boolean>(true);

  const isDraggingRef = useRef<boolean>(false);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const watermarkRef = useRef<number>(0);
  const seenEventSeqRef = useRef<number>(0);
  const snapshotAbortRef = useRef<AbortController | null>(null);
  const snapshotReqSeqRef = useRef<number>(0);

  const exploration = useMemo(() => {
    if (liveExploration) return liveExploration;
    return (
      EXPLORATIONS.find((e) => e.id === selection.explorationId) || EXPLORATIONS[0]
    );
  }, [liveExploration, selection.explorationId]);

  const bestNode = useMemo(() => {
    return (
      exploration.nodes.find((n) => n.id === exploration.bestValidNodeId) ||
      exploration.nodes[0]
    );
  }, [exploration]);

  // Fetch snapshot from SQLite backend with monotonic request seq & watermark checks
  const fetchSnapshot = useCallback(async (expId: string): Promise<number | null> => {
    const reqId = ++snapshotReqSeqRef.current;
    if (snapshotAbortRef.current) {
      snapshotAbortRef.current.abort();
    }
    const controller = new AbortController();
    snapshotAbortRef.current = controller;

    try {
      const resp = await fetch(`/api/explorations/${expId}/snapshot`, {
        signal: controller.signal,
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const snap: Exploration = await resp.json();

      // Reject superseded or out-of-order responses
      if (reqId !== snapshotReqSeqRef.current) {
        return null;
      }
      const seq = typeof snap.lastEventSeq === 'number' ? snap.lastEventSeq : 0;
      if (seq < watermarkRef.current) {
        return watermarkRef.current;
      }

      watermarkRef.current = seq;
      seenEventSeqRef.current = Math.max(seenEventSeqRef.current, seq);
      setEventWatermark(seq);
      setLiveExploration(snap);

      // Reconcile selection: preserve selected node/stage/attempt/edge if still valid
      setSelection((prev) => {
        const nodeExists = prev.nodeId && snap.nodes.some((n) => n.id === prev.nodeId);
        const nextNodeId = nodeExists ? prev.nodeId : snap.bestValidNodeId || snap.nodes[0]?.id || null;
        return {
          ...prev,
          nodeId: nextNodeId,
        };
      });
      return seq;
    } catch (err: unknown) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        return null;
      }
      if (reqId === snapshotReqSeqRef.current) {
        setSseState('static_fallback');
      }
      return null;
    }
  }, []);

  // Connect / Reconnect SSE from a specific event watermark (after_seq)
  const connectSseFromWatermark = useCallback(
    (expId: string, afterSeq: number) => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setSseState('connecting');
      const es = new EventSource(
        `/api/explorations/${expId}/events?after_seq=${afterSeq}`
      );
      eventSourceRef.current = es;

      es.onopen = () => {
        setSseState('connected');
      };

      es.addEventListener('domain_event', (msg: MessageEvent) => {
        try {
          const evt = JSON.parse(msg.data);
          const seq = Number(evt.seq || 0);
          if (seq <= seenEventSeqRef.current) {
            return; // Deduplicate already-seen event seq
          }
          seenEventSeqRef.current = seq;
          setEventWatermark(Math.max(watermarkRef.current, seq));
          setLastEventSummary(`#${seq} ${evt.eventType} (${evt.entityId})`);
          void fetchSnapshot(expId);
        } catch {
          // ignore malformed event
        }
      });

      es.addEventListener('resync_required', () => {
        setLastEventSummary('历史水位越界，重拉完整快照...');
        void fetchSnapshot(expId).then((newSeq) => {
          if (newSeq !== null) {
            connectSseFromWatermark(expId, newSeq);
          }
        });
      });

      es.onerror = () => {
        setSseState((prev) => (prev === 'paused' ? 'paused' : 'reconnecting'));
      };
    },
    [fetchSnapshot]
  );

  // Initial snapshot load + SSE subscription
  useEffect(() => {
    let cancelled = false;
    watermarkRef.current = 0;
    seenEventSeqRef.current = 0;
    void fetchSnapshot(selection.explorationId).then((seq) => {
      if (!cancelled && seq !== null) {
        setLastEventSummary(`快照就绪 (#${seq}) · SSE 监听中`);
        connectSseFromWatermark(selection.explorationId, seq);
      }
    });
    return () => {
      cancelled = true;
      if (snapshotAbortRef.current) {
        snapshotAbortRef.current.abort();
        snapshotAbortRef.current = null;
      }
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [selection.explorationId, fetchSnapshot, connectSseFromWatermark]);

  // Read-only SSE disconnect & resumable catch-up test via after_seq
  const handleTestDisconnectCatchup = useCallback(() => {
    if (sseState === 'static_fallback') return;
    const currentSeq = watermarkRef.current;
    const rewindSeq = Math.max(0, currentSeq - 2);
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setSseState('paused');
    seenEventSeqRef.current = rewindSeq;
    setLastEventSummary(`已模拟断线，从水位 #${rewindSeq} 补发最近事件...`);

    window.setTimeout(() => {
      connectSseFromWatermark(selection.explorationId, rewindSeq);
    }, 260);
  }, [selection.explorationId, sseState, connectSseFromWatermark]);

  // Keep URL query parameters synchronized with current selection for link sharing
  useEffect(() => {
    const params = new URLSearchParams();
    params.set('view', selection.viewMode);
    if (selection.nodeId) params.set('node', selection.nodeId);
    if (selection.stageType) params.set('stage', selection.stageType);
    if (selection.attemptId) params.set('attempt', selection.attemptId);
    if (selection.edgeId) params.set('edge', selection.edgeId);
    const nextUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState(null, '', nextUrl);
  }, [
    selection.viewMode,
    selection.nodeId,
    selection.stageType,
    selection.attemptId,
    selection.edgeId,
  ]);

  const handleSelectNode = useCallback((nodeId: string) => {
    setSelection((prev) => ({
      ...prev,
      nodeId,
      edgeId: null,
      attemptId: null,
      rightTab: prev.rightTab === 'candidates' ? 'inspector' : prev.rightTab,
    }));
    setMobileDrawerOpen(true);
  }, []);

  const handleSelectStage = useCallback(
    (nodeId: string, stageType: StageType | null) => {
      setSelection((prev) => ({
        ...prev,
        nodeId,
        stageType,
        attemptId: null,
        edgeId: null,
        rightTab: 'inspector',
      }));
      setMobileDrawerOpen(true);
    },
    []
  );

  const handleSelectAttempt = useCallback(
    (nodeId: string, stageType: StageType, attemptId: string) => {
      setSelection((prev) => ({
        ...prev,
        nodeId,
        stageType,
        attemptId,
        edgeId: null,
        rightTab: 'inspector',
      }));
      setMobileDrawerOpen(true);
    },
    []
  );

  const handleSelectEdge = useCallback((edgeId: string | null) => {
    setSelection((prev) => ({
      ...prev,
      edgeId,
      rightTab: 'inspector',
    }));
    if (edgeId) setMobileDrawerOpen(true);
  }, []);

  const handleToggleExpandNode = useCallback((nodeId: string) => {
    setSelection((prev) => {
      const exists = prev.expandedNodeIds.includes(nodeId);
      return {
        ...prev,
        expandedNodeIds: exists
          ? prev.expandedNodeIds.filter((id) => id !== nodeId)
          : [...prev.expandedNodeIds, nodeId],
      };
    });
  }, []);

  const handleToggleAllCrossRefs = useCallback(() => {
    setSelection((prev) => ({
      ...prev,
      showAllCrossRefs: !prev.showAllCrossRefs,
    }));
  }, []);

  const handleCopyShareLink = useCallback(() => {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(window.location.href).catch(() => {});
    }
    setCopiedNotice(true);
    window.setTimeout(() => setCopiedNotice(false), 2200);
  }, []);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!isDraggingRef.current || !workspaceRef.current) return;
      const rect = workspaceRef.current.getBoundingClientRect();
      const pct = ((e.clientX - rect.left) / rect.width) * 100;
      const clamped = Math.min(68, Math.max(36, pct));
      setLeftWidthPct(clamped);
    };
    const onMouseUp = () => {
      isDraggingRef.current = false;
      document.body.style.cursor = '';
    };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  const syncBadgeLabel = useMemo(() => {
    if (sseState === 'connected') {
      return `SQLite+SSE 实时同步 · 水位 #${eventWatermark}`;
    }
    if (sseState === 'reconnecting' || sseState === 'connecting') {
      return `SSE 同步连接中 · 水位 #${eventWatermark}`;
    }
    if (sseState === 'paused') {
      return `SSE 断线重连测试 · 锁定 #${eventWatermark}`;
    }
    return '静态交互原型 · 演示数据';
  }, [sseState, eventWatermark]);

  return (
    <div className="workbench-shell">
      <header className="top-coord-bar">
        <div className="top-brand-group">
          <span
            className={`demo-mode-badge ${sseState === 'connected' ? 'live-sync-badge' : ''}`}
            title={lastEventSummary}
          >
            {syncBadgeLabel}
          </span>
          <h1 className="top-title">{exploration.title}</h1>
        </div>

        <div className="top-metrics-strip">
          <div className="top-metric-item">
            <span className="tm-label">固定基线</span>
            <strong className="tm-val tabular-nums">
              {exploration.baselineRef.version} ({exploration.baselineRef.latencyUs} us)
            </strong>
          </div>
          <div className="top-metric-item">
            <span className="tm-label">当前最佳有效</span>
            <button
              type="button"
              className="tm-best-btn tabular-nums"
              onClick={() => handleSelectNode(exploration.bestValidNodeId)}
            >
              {bestNode?.id} · {bestNode?.latencyUs ?? '--'} us (
              {bestNode?.speedupVsBaseline ?? '--'}x)
            </button>
          </div>
          <div className="top-metric-item">
            <span className="tm-label">全局预算</span>
            <strong className="tm-val tabular-nums">
              已用 {exploration.budget.usedEvaluations} + 预留{' '}
              {exploration.budget.reservedEvaluations} / {exploration.budget.maxEvaluations} 评估
            </strong>
          </div>
          <div className="top-metric-item">
            <span className="tm-label">协调状态</span>
            <span className="tm-coord-pill" title={exploration.stopReason}>
              {exploration.coordinatorStatus === 'running'
                ? 'N4 目标达成 · N5 收尾中'
                : `状态: ${exploration.coordinatorStatus}`}
            </span>
          </div>
        </div>

        <div className="top-actions-group">
          {sseState !== 'static_fallback' && (
            <button
              type="button"
              className="sim-action-btn"
              onClick={handleTestDisconnectCatchup}
              title="模拟 SSE 断开并从上一水位 after_seq 重新订阅补发事件（只读操作）"
            >
              <span>验证断线续传</span>
            </button>
          )}
          <button
            type="button"
            className="share-loc-btn"
            onClick={handleCopyShareLink}
            title="复制包含当前选中节点、Stage 和视图定位的分享链接（只读定位，不含写入凭据）"
          >
            <LinkShareIcon size={13} />
            <span>{copiedNotice ? '已复制定位链接' : '分享当前定位'}</span>
          </button>
        </div>
      </header>

      <div className="sub-context-bar">
        <div className="sub-ctx-left">
          <span>
            <strong>优化目标：</strong>
            {exploration.targetGoal}
          </span>
          <span className="ctx-sep">|</span>
          <span>
            <strong>环境与输入：</strong>
            {exploration.environment} · {exploration.inputDomain}
          </span>
        </div>
        <div className="sub-ctx-right">
          <span className="tabular-nums" title={lastEventSummary}>
            当前定位: 节点 <strong>{selection.nodeId || '-'}</strong>
            {selection.stageType ? ` / Stage: ${selection.stageType}` : ''}
            {selection.attemptId ? ` / Attempt: ${selection.attemptId}` : ''}
            {selection.edgeId ? ` / 边: ${selection.edgeId}` : ''}
          </span>
          <button
            type="button"
            className="mobile-drawer-toggle"
            onClick={() => setMobileDrawerOpen((v) => !v)}
          >
            {mobileDrawerOpen ? '收起详情抽屉' : '展开右侧文件库'}
          </button>
        </div>
      </div>

      <main className="split-workspace" ref={workspaceRef}>
        <section
          className="left-view-pane"
          style={{ width: `${leftWidthPct}%` }}
          aria-label="左侧搜索树与 Agent 双视图联动区"
        >
          <div className="view-switcher-bar">
            <div className="view-switch-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={selection.viewMode === 'tree'}
                className={`view-tab-btn ${selection.viewMode === 'tree' ? 'active' : ''}`}
                onClick={() =>
                  setSelection((prev) => ({ ...prev, viewMode: 'tree' }))
                }
              >
                <TreeIcon size={14} />
                <span>树状图视图 (Search Tree · {exploration.nodes.length} 节点)</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={selection.viewMode === 'agent'}
                className={`view-tab-btn ${selection.viewMode === 'agent' ? 'active' : ''}`}
                onClick={() =>
                  setSelection((prev) => ({ ...prev, viewMode: 'agent' }))
                }
              >
                <TimelineIcon size={14} />
                <span>Agent 轨迹视图 ({exploration.agents.length} Agents 并行进展)</span>
              </button>
            </div>

            <div className="view-sync-indicator">
              <span>双视图同源同步 · 切换保留选中节点 {selection.nodeId}</span>
            </div>
          </div>

          <div className="view-stage-body">
            {selection.viewMode === 'tree' ? (
              <TreeCanvas
                exploration={exploration}
                selection={selection}
                onSelectNode={handleSelectNode}
                onSelectStage={(nid, st) => handleSelectStage(nid, st)}
                onSelectEdge={(eid) => handleSelectEdge(eid)}
                onToggleExpandNode={handleToggleExpandNode}
                onToggleAllCrossRefs={handleToggleAllCrossRefs}
              />
            ) : (
              <AgentTimeline
                exploration={exploration}
                selection={selection}
                onSelectNode={handleSelectNode}
                onSelectAttempt={handleSelectAttempt}
              />
            )}
          </div>
        </section>

        <div
          className="split-resizer"
          role="separator"
          aria-orientation="vertical"
          title="拖拽调整左右分栏宽度"
          onMouseDown={() => {
            isDraggingRef.current = true;
            document.body.style.cursor = 'col-resize';
          }}
        />

        <div
          className={`right-pane-wrapper ${mobileDrawerOpen ? 'drawer-open' : 'drawer-closed'}`}
          style={{ width: `${100 - leftWidthPct}%` }}
        >
          <RightInspector
            exploration={exploration}
            selection={selection}
            onSelectNode={handleSelectNode}
            onSelectStage={handleSelectStage}
            onSelectAttempt={handleSelectAttempt}
            onSelectEdge={handleSelectEdge}
            onChangeCompareNode={(cid) =>
              setSelection((prev) => ({ ...prev, compareNodeId: cid }))
            }
            onChangeTab={(tab) =>
              setSelection((prev) => ({ ...prev, rightTab: tab }))
            }
          />
        </div>
      </main>
    </div>
  );
};
export default App;
