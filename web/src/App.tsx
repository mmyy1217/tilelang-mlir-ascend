import { GraphDetails } from './components/GraphDetails';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { EXPLORATIONS } from './mockData';
import { Exploration, SelectionState, StageType } from './types';
import { TaskPanels } from './components/TaskPanels';
import { ProjectSidebar } from './components/ProjectSidebar';
import { Project, ProjectFolder, readLibrary, saveEntries, localProject, newExploration, forkExploration } from './projects';
import { WorkbenchMenu } from './components/WorkbenchMenu';
import { GraphCanvas } from './components/GraphCanvas';
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
    explorationId: params.get('exploration') || EXPLORATIONS[0].id,
    viewMode: viewParam === 'agent' || viewParam === 'timeline' ? 'agent' : 'tree',
    nodeId: nodeParam || 'N4',
    stageType: stageParam || null,
    attemptId: attemptParam || null,
    edgeId: edgeParam || null,
    compareNodeId: 'N1',
    rightTab: 'inspector',
    showAllCrossRefs: false,
    expandedNodeIds: [],
  };
}

export const App: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [folders, setFolders] = useState<ProjectFolder[]>([]);
  const [libraryReady, setLibraryReady] = useState(false);
  const [libraryError, setLibraryError] = useState('');
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [selection, setSelection] = useState<SelectionState>(parseInitialSelection);
  const [importedExploration, setImportedExploration] = useState<Exploration | null>(null);
  const [liveExploration, setLiveExploration] = useState<Exploration | null>(null);
  const [sseState, setSseState] = useState<SseSyncState>('connecting');
  const [eventWatermark, setEventWatermark] = useState<number>(0);
  const [lastEventSummary, setLastEventSummary] = useState<string>('等待快照水位...');
  const [leftWidthPct, setLeftWidthPct] = useState<number>(50);
  const [copiedNotice, setCopiedNotice] = useState<boolean>(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState<boolean>(true);

  const offlineRef = useRef(false);
  const currentExplorationRef = useRef(selection.explorationId);
  const initialLocation = useRef(window.location.search);
  const isDraggingRef = useRef<boolean>(false);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const watermarkRef = useRef<number>(0);
  const seenEventSeqRef = useRef<number>(0);
  const snapshotAbortRef = useRef<AbortController | null>(null);
  const snapshotReqSeqRef = useRef<number>(0);

  const exploration = useMemo(() => {
    if (importedExploration) return importedExploration;
    if (liveExploration) return liveExploration;
    return (
      EXPLORATIONS.find((e) => e.id === selection.explorationId) || EXPLORATIONS[0]
    );
  }, [importedExploration, liveExploration, selection.explorationId]);

  const bestNode = useMemo(() => {
    return (
      exploration.nodes.find((n) => n.id === exploration.bestValidNodeId) ||
      exploration.nodes[0]
    );
  }, [exploration]);

  const activateProject = useCallback(async (project: Project, preserveLocation = false) => {
    currentExplorationRef.current = project.snapshot.id;
    offlineRef.current = project.kind === 'local';
    snapshotReqSeqRef.current += 1;
    snapshotAbortRef.current?.abort();
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    watermarkRef.current = 0;
    seenEventSeqRef.current = 0;
    setEventWatermark(0);
    setActiveProjectId(project.id);
    setImportedExploration(project.kind === 'local' ? project.snapshot : null);
    setLiveExploration(project.kind === 'server' ? project.snapshot : null);
    setSseState(project.kind === 'server' ? 'connecting' : 'static_fallback');
    const snapshot = project.snapshot;
    const requested = new URLSearchParams(initialLocation.current);
    const requestedNode = preserveLocation ? requested.get('node') : null;
    const validNode = requestedNode && snapshot.nodes.some(n => n.id === requestedNode);
    setSelection(prev => ({...prev, explorationId: snapshot.id, nodeId: validNode ? requestedNode : snapshot.bestValidNodeId || snapshot.nodes[0]?.id || null,
      knowledgeId: null, compareNodeId: snapshot.nodes[0]?.id || null, stageType: validNode ? prev.stageType : null,
      attemptId: validNode ? prev.attemptId : null, edgeId: validNode ? prev.edgeId : null, expandedNodeIds: []}));
    try { localStorage.setItem('active-project', project.id); } catch { /* optional preference */ }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const library = await readLibrary();
        const known = library.projects;
        // List all server explorations, retaining cached projects when offline.
        let remote: Exploration[] = [];
        try {
          const response = await fetch('/api/explorations');
          if (response.ok) {
            const {items} = await response.json();
            const snapshots = await Promise.allSettled(items.map(async (item: {id: string}) => {
              const res = await fetch(`/api/explorations/${encodeURIComponent(item.id)}/snapshot`);
              if (!res.ok) throw new Error('snapshot unavailable');
              return res.json() as Promise<Exploration>;
            }));
            remote = snapshots.flatMap(result => result.status === 'fulfilled' ? [result.value as Exploration] : []);
          }
        } catch { /* cached library remains usable offline */ }
        if (!known.length && !remote.length) remote = EXPLORATIONS;
        for (const snapshot of remote) {
          const existing = known.find(p => p.kind === 'server' && p.snapshot.id === snapshot.id);
          if (existing) existing.snapshot = snapshot;
          else known.push({...localProject(snapshot), id: `server:${snapshot.id}`, kind: 'server'});
        }
        await saveEntries('projects', known);
        if (cancelled) return;
        setProjects(known); setFolders(library.folders); setLibraryReady(true);
        let saved: string | null = null;
        try { saved = localStorage.getItem('active-project'); } catch { /* optional preference */ }
        const requested = new URLSearchParams(initialLocation.current).get('project');
        const project = known.find(p => p.id === (requested || saved)) || known[0];
        if (project) await activateProject(project, true);
      } catch (error) { if (!cancelled) setLibraryError(`无法读取项目库：${error instanceof Error ? error.message : '存储不可用'}`); }
    })();
    return () => { cancelled = true; };
  }, [activateProject]);

  useEffect(() => {
    if (!liveExploration || !libraryReady || importedExploration) return;
    const current = projects.find(p => p.id === activeProjectId && p.kind === 'server' && p.snapshot.id === liveExploration.id);
    if (!current) return;
    const updated = {...current, snapshot: liveExploration, updatedAt: new Date().toISOString()};
    void saveEntries('projects', [updated]).then(() => {
      setProjects(prev => prev.map(p => p.id === updated.id ? {...p, snapshot: updated.snapshot, updatedAt: updated.updatedAt} : p));
    }).catch(() => setLibraryError('项目快照自动保存失败，请导出备份。'));
  }, [liveExploration, libraryReady, activeProjectId, importedExploration]);

  const importProject = async (snapshot: Exploration) => {
    if (!libraryReady) throw new Error('项目库尚未就绪');
    const project = localProject(snapshot);
    await saveEntries('projects', [project]);
    setProjects(prev => [...prev, project]);
    await activateProject(project);
  };
  const updateProject = async (project: Project) => {
    await saveEntries('projects', [project]);
    setProjects(prev => prev.map(p => p.id === project.id ? project : p));
  };

  // Fetch snapshot from SQLite backend with monotonic request seq & watermark checks
  const fetchSnapshot = useCallback(async (expId: string): Promise<number | null> => {
    if (offlineRef.current || expId !== currentExplorationRef.current) return null;
    const reqId = ++snapshotReqSeqRef.current;
    if (snapshotAbortRef.current) {
      snapshotAbortRef.current.abort();
    }
    const controller = new AbortController();
    snapshotAbortRef.current = controller;

    try {
      const resp = await fetch(`/api/explorations/${encodeURIComponent(expId)}/snapshot`, {
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
      if (offlineRef.current || expId !== currentExplorationRef.current) return;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      setSseState('connecting');
      const es = new EventSource(
        `/api/explorations/${encodeURIComponent(expId)}/events?after_seq=${afterSeq}`
      );
      eventSourceRef.current = es;

      es.onopen = () => {
        if (es !== eventSourceRef.current || offlineRef.current) return;
        setSseState('connected');
      };

      es.addEventListener('domain_event', (msg: MessageEvent) => {
        if (es !== eventSourceRef.current || offlineRef.current) return;
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
        if (es !== eventSourceRef.current || offlineRef.current) return;
        setLastEventSummary('历史水位越界，重拉完整快照...');
        void fetchSnapshot(expId).then((newSeq) => {
          if (newSeq !== null) {
            connectSseFromWatermark(expId, newSeq);
          }
        });
      });

      es.onerror = () => {
        if (es !== eventSourceRef.current || offlineRef.current) return;
        setSseState((prev) => (prev === 'paused' ? 'paused' : 'reconnecting'));
      };
    },
    [fetchSnapshot]
  );

  // Initial snapshot load + SSE subscription
  useEffect(() => {
    if (!libraryReady || importedExploration) return;
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
  }, [libraryReady, activeProjectId, importedExploration, selection.explorationId, fetchSnapshot, connectSseFromWatermark]);

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
    if (!libraryReady) return;
    const params = new URLSearchParams();
    if (activeProjectId) params.set('project', activeProjectId);
    params.set('exploration', selection.explorationId);
    params.set('view', selection.viewMode === 'tree' ? 'graph' : selection.viewMode);
    if (selection.nodeId) params.set('node', selection.nodeId);
    if (selection.stageType) params.set('stage', selection.stageType);
    if (selection.attemptId) params.set('attempt', selection.attemptId);
    if (selection.edgeId) params.set('edge', selection.edgeId);
    const nextUrl = `${window.location.pathname}?${params.toString()}`;
    window.history.replaceState(null, '', nextUrl);
  }, [
    libraryReady, activeProjectId, selection.explorationId,
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
      knowledgeId: null,
      edgeId: null,
      attemptId: null,
      rightTab: prev.rightTab,
    }));
    setMobileDrawerOpen(true);
  }, []);

  const handleSelectStage = useCallback(
    (nodeId: string, stageType: StageType | null) => {
      setSelection((prev) => ({
        ...prev,
        nodeId,
        knowledgeId: null,
        stageType,
        attemptId: null,
        edgeId: null,
        rightTab: prev.rightTab === 'compare' ? 'compare' : 'inspector',
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
        knowledgeId: null,
        stageType,
        attemptId,
        edgeId: null,
        rightTab: prev.rightTab === 'compare' ? 'compare' : 'inspector',
      }));
      setMobileDrawerOpen(true);
    },
    []
  );

  const handleSelectEdge = useCallback((edgeId: string | null) => {
    setSelection((prev) => ({
      ...prev,
      edgeId,
      knowledgeId: null,
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
          <WorkbenchMenu exploration={exploration} offline={!!importedExploration}
            onImport={importProject} onLive={() => {
              const project = projects.find(p => p.kind === 'server');
              if (project) void activateProject(project);
            }} />
          <h1 className="top-title" title={exploration.title}>{exploration.operatorName.replace(/\s*[（(].*$/, '') || '未命名算子'}</h1>
          <span className="demo-mode-badge" title={importedExploration ? '离线快照 · 未连接实时数据' : `${syncBadgeLabel} · ${lastEventSummary}`} >
            {importedExploration ? '离线快照' : sseState === 'connected' ? '已连接' : sseState === 'static_fallback' ? '演示' : '连接中'}
          </span>
        </div>
        <div className="top-metrics-strip">
          <span className="tm-baseline" title={`${exploration.baselineRef.description} · ${exploration.baselineRef.version}`}>
            基线 {exploration.baselineRef.id ? `${exploration.baselineRef.latencyUs} μs` : '—'}
          </span>
          <button className="tm-best-btn" disabled={!exploration.bestValidNodeId} onClick={() => handleSelectNode(exploration.bestValidNodeId)} title={`基线 ${exploration.baselineRef.latencyUs} us · 加速 ${bestNode?.speedupVsBaseline ?? '--'}x`}>
            最佳 {exploration.bestValidNodeId ? bestNode?.id : '—'} · {exploration.bestValidNodeId ? bestNode?.latencyUs ?? '—' : '—'} μs
          </button>
          <span title={`已用 ${exploration.budget.usedEvaluations}，在途预留 ${exploration.budget.reservedEvaluations}，总预算 ${exploration.budget.maxEvaluations}`}>
            评估 {exploration.budget.usedEvaluations}+{exploration.budget.reservedEvaluations}/{exploration.budget.maxEvaluations}
          </span>
          <span title={exploration.stopReason}>{exploration.coordinatorStatus}</span>
        </div>
        <div className="top-actions-group">
          <TaskPanels key={activeProjectId} exploration={exploration} />
          <button className="share-loc-btn" onClick={handleCopyShareLink} title="复制当前节点和视图的定位链接">
            <LinkShareIcon size={13} />{copiedNotice ? '已复制' : '分享'}
          </button>
          <button className="mobile-drawer-toggle" onClick={() => setMobileDrawerOpen(v => !v)}>{mobileDrawerOpen ? '收起文件' : '文件库'}</button>
        </div>
      </header>

      <div className="project-workspace">
        <ProjectSidebar projects={projects} folders={folders} activeId={activeProjectId} ready={libraryReady}
          onSelect={activateProject} onImport={importProject}
          onCreate={async name => { await importProject(newExploration(name)); }}
          onFolder={async (name, id) => {
            const folder = {id: id || crypto.randomUUID(), name};
            await saveEntries('folders', [folder]);
            setFolders(prev => id ? prev.map(f => f.id === id ? folder : f) : [...prev, folder]);
          }}
          onRename={async (project, name) => { await updateProject({...project, name}); }}
          onMove={async (project, folderId) => { await updateProject({...project, folderId}); }}
          onFork={async (name, count) => {
            if (!Number.isInteger(count) || count < 1 || count > 10) throw new Error('一次可 Fork 1–10 个项目');
            const copies = Array.from({length: count}, (_, i) => {
              const title = count === 1 ? name : `${name} ${i + 1}`;
              return {...localProject(forkExploration(exploration, title), title), folderId: projects.find(p => p.id === activeProjectId)?.folderId || null};
            });
            await saveEntries('projects', copies);
            setProjects(prev => [...prev, ...copies]);
            await activateProject(copies[0]);
          }} />
      <main className="split-workspace" ref={workspaceRef}>
        {libraryError && <div className="library-error" role="alert">{libraryError}<button onClick={() => setLibraryError('')}>关闭</button></div>}
        <section
          className="left-view-pane"
          style={{ width: `${leftWidthPct}%` }}
          aria-label="左侧探索图与 Agent 双视图联动区"
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
                <span>探索图 · {exploration.nodes.length}</span>
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
                <span>Agent · {exploration.agents.length}</span>
              </button>
            </div>

            <div className="view-sync-indicator">
              <span>{selection.nodeId}</span>
            </div>
          </div>

          <div className="view-stage-body">
            {exploration.nodes.length === 0 ? <div className="empty-project"><h2>探索图尚未开始</h2><p>项目已保存。接入执行器并创建第一个方案节点后，这里会显示探索过程。</p><p>你也可以导入已有快照，或从历史项目 Fork。</p></div> : selection.viewMode === 'tree' ? (
              <GraphCanvas key={activeProjectId}
                exploration={exploration}
                selection={selection}
                onSelectKnowledge={id => setSelection(prev=>({...prev,knowledgeId:id,edgeId:null,rightTab:'inspector'}))}
                onSelectNode={handleSelectNode}
                onSelectStage={(nid, st) => handleSelectStage(nid, st)}
                onSelectEdge={(eid) => handleSelectEdge(eid)}
                onToggleExpandNode={handleToggleExpandNode}
                onToggleAllCrossRefs={handleToggleAllCrossRefs}
              />
            ) : (
              <AgentTimeline key={activeProjectId} projectId={activeProjectId || undefined}
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
          {selection.rightTab === 'inspector' && (selection.knowledgeId || selection.edgeId) ? <GraphDetails exploration={exploration} selection={selection} onNode={handleSelectNode} onKnowledge={id=>setSelection(prev=>({...prev,knowledgeId:id,edgeId:null}))} onEdge={handleSelectEdge}/> : exploration.nodes.length > 0 ? <RightInspector key={activeProjectId}
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
          /> : <div className="empty-project"><h2>节点与文件库</h2><p>创建方案节点后，查看阶段、实验和相关文件。</p></div>}
        </div>
      </main>
      </div>
    </div>
  );
};
export default App;
