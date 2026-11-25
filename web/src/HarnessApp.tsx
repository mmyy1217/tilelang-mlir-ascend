import { HarnessRecords } from './components/HarnessRecords';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Capture, harnessExploration, HarnessModel } from './harnessAdapter';
import { Exploration } from './types';
import { SearchAndKnowledgeInspector } from './components/SearchAndKnowledgeInspector';
import { TrajectorySelection, TrajectoryWorkbench } from './components/TrajectoryWorkbench';
import { LinkShareIcon } from './icons';
import './harness.css';

const staticSnapshot = document.querySelector('meta[name="harness-static"]') !== null;
const emptyModel: HarnessModel = {
  projectId: 'empty',
  snapshotId: 'empty',
  projectName: 'Harness Vis',
  capturedAt: '',
  consistency: 'unknown',
  records: [],
  runs: [],
  annotations: [],
  warnings: [],
  manifest: { files: [] },
};

const emptyExploration: Exploration = {
  ...harnessExploration(emptyModel, {}),
  nodes: [],
  harness: { snapshotId: '', capturedAt: '', consistency: 'unknown', runCount: 0, warnings: [] },
};

async function readJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new Error(`读取失败 (${response.status})：${(await response.text()).slice(0, 160)}`);
  }
  return response.json();
}

export function HarnessApp() {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [watchers, setWatchers] = useState<
    { projectId: string; state: string; error?: string | null; intervalSeconds: number; settleSeconds: number }[]
  >([]);
  const [project, setProject] = useState(
    () => new URLSearchParams(location.search).get('captureProject') || ''
  );
  const [pinned, setPinned] = useState(() =>
    new URLSearchParams(location.search).get('follow') === '1'
      ? ''
      : new URLSearchParams(location.search).get('capture') || ''
  );
  const [exploration, setExploration] = useState<Exploration>(emptyExploration);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);
  const [refresh, setRefresh] = useState(0);

  const [trajSelection, setTrajSelection] = useState<TrajectorySelection>(() => {
    const params = new URLSearchParams(location.search);
    const sNode = params.get('searchNode');
    const hot = params.get('hot');
    const cooled = params.get('cooled');
    const file = params.get('file');
    if (hot) return { kind: 'hot', hotId: hot };
    if (cooled) return { kind: 'cooled', cooledKey: cooled };
    if (file) return { kind: 'file', filePath: file, searchNodeId: sNode || undefined };
    return {
      kind: 'search-node',
      searchNodeId: sNode || undefined,
      candidateId: params.get('node') || undefined,
      runId: params.get('attempt') || undefined,
    };
  });

  const [leftWidthPct, setLeftWidthPct] = useState(58);
  const [copiedNotice, setCopiedNotice] = useState(false);

  const isDraggingRef = useRef(false);
  const workspaceRef = useRef<HTMLDivElement | null>(null);
  const cache = useRef(new Map<string, Record<string, string>>());
  const lastVersion = useRef('');

  const current =
    captures.find((c) => c.projectId === project && (!pinned || c.id === pinned)) ||
    (!project ? captures[0] : undefined);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await readJson<{
          items: Capture[];
          unavailable: string[];
          watchers?: typeof watchers;
        }>(staticSnapshot ? './data/index.json' : '/api/harness/snapshots', controller.signal);
        if (!active) return;
        setCaptures(result.items);
        setWatchers(result.watchers || []);
        setProject((p) => p || result.items[0]?.projectId || '');
        setConnected(true);
        setReady(true);
        setError(result.unavailable.length ? `${result.unavailable.length} 份快照不可读取` : '');
      } catch (e) {
        if (active) {
          setError(String(e));
          setConnected(false);
          setReady(true);
        }
      } finally {
        if (active && !staticSnapshot) timer = setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh]);

  const key = current ? `${current.projectId}/${current.id}` : '';
  useEffect(() => {
    if (!current) return;
    const controller = new AbortController();
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const base = `/api/harness/projects/${current.projectId}/snapshots/${current.id}`;
    async function load() {
      try {
        const model = await readJson<HarnessModel>(staticSnapshot ? `./data/${key}/model.json` : base, controller.signal);
        const revision = key + ':' + (model.viewRevision || 'base');
        if (lastVersion.current === revision) return;
        let contents = cache.current.get(key);
        if (!contents) {
          contents = {};
          const files = model.manifest.files.filter((f) => f.status === 'copied' && f.text);
          for (let i = 0; i < files.length; i += 6) {
            await Promise.all(
              files.slice(i, i + 6).map(async (file) => {
                try {
                  const data = await readJson<{ content: string }>(
                    staticSnapshot ? `./data/${key}/files/${file.path.split('/').map(encodeURIComponent).join('/')}.json` : `${base}/file?path=${encodeURIComponent(file.path)}`,
                    controller.signal
                  );
                  contents![file.path] = data.content;
                } catch (e) {
                  if (controller.signal.aborted) throw e;
                }
              })
            );
          }
          if (!active) return;
          cache.current.set(key, contents);
          while (cache.current.size > 3) {
            cache.current.delete(cache.current.keys().next().value!);
          }
        }
        if (active) {
          setExploration(harnessExploration(model, contents));
          lastVersion.current = revision;
        }
      } catch (e) {
        if (active) setError(String(e));
      } finally {
        if (active && !staticSnapshot) timer = setTimeout(load, 5000);
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [key, refresh]);

  const candidateNodes = useMemo(
    () => exploration.nodes.filter((n) => n.harness?.kind === 'candidate'),
    [exploration.nodes]
  );

  const bestNode = useMemo(
    () => candidateNodes.find((n) => n.id === exploration.bestValidNodeId) || candidateNodes[0] || null,
    [candidateNodes, exploration.bestValidNodeId]
  );

  // Reconcile selection when exploration loads/updates
  useEffect(() => {
    const s = exploration.harness?.search;
    if (!exploration.harness?.snapshotId || !s || s.searchNodes.length === 0) return;
    setTrajSelection((prev) => {
      if (prev.kind === 'hot' && prev.hotId && s.hotKnowledge.some((h) => h.id === prev.hotId)) {
        return prev;
      }
      if (
        prev.kind === 'cooled' &&
        prev.cooledKey &&
        s.cooledKnowledge.some((c) => c.fullKey === prev.cooledKey)
      ) {
        return prev;
      }
      if (prev.kind === 'file' && prev.filePath && exploration.resources[prev.filePath]) {
        return prev;
      }
      if (prev.searchNodeId && s.searchNodes.some((n) => n.id === prev.searchNodeId)) {
        return prev;
      }
      const focusId = s.currentNodeId || s.searchNodes[s.searchNodes.length - 1].id;
      const focusNode = s.searchNodes.find((n) => n.id === focusId);
      return {
        kind: 'search-node',
        searchNodeId: focusId,
        candidateId:
          focusNode?.candidateIds[focusNode.candidateIds.length - 1] ||
          exploration.bestValidNodeId ||
          undefined,
      };
    });
  }, [exploration.id, exploration.harness?.snapshotId, exploration.bestValidNodeId]);

  // Sync URL query params for deep-link sharing
  useEffect(() => {
    if (!ready || !exploration.harness?.snapshotId) return;
    const params = new URLSearchParams();
    if (project) params.set('captureProject', project);
    if (exploration.harness.snapshotId) params.set('capture', exploration.harness.snapshotId);
    if (!pinned) params.set('follow', '1');
    if (trajSelection.searchNodeId) params.set('searchNode', trajSelection.searchNodeId);
    if (trajSelection.candidateId) params.set('node', trajSelection.candidateId);
    if (trajSelection.runId) params.set('attempt', trajSelection.runId);
    if (trajSelection.kind === 'hot' && trajSelection.hotId) params.set('hot', trajSelection.hotId);
    if (trajSelection.kind === 'cooled' && trajSelection.cooledKey)
      params.set('cooled', trajSelection.cooledKey);
    if (trajSelection.kind === 'file' && trajSelection.filePath)
      params.set('file', trajSelection.filePath);
    window.history.replaceState(null, '', `${window.location.pathname}?${params.toString()}`);
  }, [ready, project, pinned, exploration.harness?.snapshotId, trajSelection]);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!isDraggingRef.current || !workspaceRef.current) return;
      const rect = workspaceRef.current.getBoundingClientRect();
      const pct = ((e.clientX - rect.left) / rect.width) * 100;
      setLeftWidthPct(Math.min(68, Math.max(38, pct)));
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

  const handleCopyShareLink = useCallback(() => {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(window.location.href).catch(() => {});
    }
    setCopiedNotice(true);
    window.setTimeout(() => setCopiedNotice(false), 2000);
  }, []);

  const uniqueProjects = useMemo(
    () => [...new Map(captures.map((c) => [c.projectId, c])).values()],
    [captures]
  );

  const watch = watchers.find((w) => w.projectId === project);
  const watchActive = watch && ['watching', 'settling', 'capturing'].includes(watch.state);

  const measurements = exploration.harness?.measurements;
  const baselineReport = useMemo(() => {
    if (!measurements?.baselinePath) return undefined;
    return measurements.reports.find((r) => r.path === measurements.baselinePath);
  }, [measurements]);

  const baselineMainLatency =
    baselineReport?.cases.find((c) => c.id === 'main')?.medianUs ||
    baselineReport?.cases[0]?.medianUs ||
    exploration.baselineRef.latencyUs;

  return (
    <div className="workbench-shell hv-shell">
      <header className="hv-topbar">
        <div className="hv-brand-group">
          {uniqueProjects.length > 1 && (
            <select
              aria-label="切换项目"
              className="hv-project-select"
              value={project}
              onChange={(e) => {
                setProject(e.target.value);
                setPinned('');
                setExploration(emptyExploration);
              }}
            >
              {uniqueProjects.map((p) => (
                <option key={p.projectId} value={p.projectId}>
                  {p.projectName}
                </option>
              ))}
            </select>
          )}
          <h1 className="hv-title">{exploration.title || 'Harness Vis'}</h1>
          {(exploration.inputDomain || exploration.environment) && (
            <span
              className="hv-spec-chip"
              title={[exploration.environment, exploration.inputDomain].filter(Boolean).join(' · ')}
            >
              {[
                exploration.environment.replace(/（.*?）|\(.*?\)/g, '').trim(),
                exploration.inputDomain.replace(/\s*\/.*$/, '').trim(),
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
          )}
          {exploration.harness?.synthetic && (
            <span
              className="hv-synthetic-badge"
              title="当前工作区数据为合成演示或模拟测量，未经真实硬件执行"
            >
              模拟数据
            </span>
          )}
        </div>

        <div className="hv-topbar-right">
          <div className="hv-summary-strip tabular-nums">
            {exploration.harness?.search?.currentNodeId && (
              <button
                type="button"
                className="hv-metric-pill"
                onClick={() =>
                  setTrajSelection({
                    kind: 'search-node',
                    searchNodeId: exploration.harness?.search?.currentNodeId || undefined,
                  })
                }
                title="点击定位当前活跃搜索节点"
              >
                <span>当前节点</span>
                <strong>{exploration.harness.search.currentNodeId}</strong>
              </button>
            )}
            {(exploration.baselineRef.id || baselineMainLatency > 0) && (
              <button
                type="button"
                className="hv-metric-pill"
                onClick={() => {
                  if (exploration.resources['BASELINE.md']) {
                    setTrajSelection({ kind: 'file', filePath: 'BASELINE.md' });
                  }
                }}
                title="点击查看基线定义与测量说明"
              >
                <span>基线 {exploration.baselineRef.id || 'B0'}</span>
                {baselineMainLatency > 0 && <strong>{baselineMainLatency.toFixed(1)} μs</strong>}
              </button>
            )}
            {exploration.bestValidNodeId && bestNode && (
              <button
                type="button"
                className="hv-best-pill"
                onClick={() =>
                  setTrajSelection({
                    kind: 'search-node',
                    searchNodeId:
                      bestNode.harness?.searchNodeId ||
                      exploration.harness?.search?.currentNodeId ||
                      undefined,
                    candidateId: bestNode.id,
                  })
                }
                title="点击定位最佳候选方案所在搜索节点"
              >
                <span>最佳 {bestNode.id}</span>
                <strong>
                  {bestNode.speedupVsBaseline
                    ? `${bestNode.speedupVsBaseline.toFixed(2)}×`
                    : bestNode.latencyUs
                    ? `${bestNode.latencyUs.toFixed(1)} μs`
                    : ''}
                </strong>
              </button>
            )}
          </div>

          <div className="hv-snapshot-controls">
            {watchActive && (
              <span className="hv-watch-dot" title="watch 自动采集运行中">
                自动同步
              </span>
            )}
            <label className="hv-snap-label">
              <span>快照</span>
              <select
                aria-label="快照版本"
                value={pinned}
                onChange={(e) => setPinned(e.target.value)}
              >
                <option value="">跟随最新</option>
                {captures
                  .filter((c) => c.projectId === project)
                  .map((c) => (
                    <option value={c.id} key={c.id}>
                      {new Date(c.capturedAt).toLocaleString()}
                    </option>
                  ))}
              </select>
            </label>
            <button
              type="button"
              className="hv-btn"
              onClick={() => setRefresh((v) => v + 1)}
              title="重新读取已采集快照"
            >
              {connected ? '刷新' : '重连'}
            </button>
            <button
              type="button"
              className="hv-btn"
              onClick={handleCopyShareLink}
              title="复制当前视图定位链接"
            >
              <LinkShareIcon size={13} />
              <span>{copiedNotice ? '已复制' : '分享'}</span>
            </button>
          </div>
        </div>
      </header>

      {error && (
        <div className="hv-error-banner" role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => setError('')}>
            关闭
          </button>
        </div>
      )}

      <main className="hv-workspace" ref={workspaceRef}>
        <section
          className="hv-left-pane"
          style={{ width: `${leftWidthPct}%` }}
          aria-label="搜索树大纲与知识账本"
        >
          {exploration.harness?.search ? (
            <TrajectoryWorkbench
              dag
              exploration={exploration}
              search={exploration.harness.search}
              selection={trajSelection}
              onSelect={setTrajSelection}
            />
          ) : (
            <div className="hv-empty-state">
              <h2>等待快照数据</h2>
              <p>正在加载工作区快照…</p>
            </div>
          )}
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

        <aside
          className="hv-right-pane"
          style={{ width: `${100 - leftWidthPct}%` }}
          aria-label="详情与文件检视器"
        >
          <HarnessRecords exploration={exploration} selection={trajSelection} onSelect={setTrajSelection} />
          {exploration.harness?.search ? (
            <SearchAndKnowledgeInspector
              exploration={exploration}
              search={exploration.harness.search}
              selection={trajSelection}
              onSelect={setTrajSelection}
            />
          ) : (
            <div className="hv-empty-state">
              <h2>等待快照数据</h2>
              <p>正在加载工作区快照…</p>
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}
export default HarnessApp;
