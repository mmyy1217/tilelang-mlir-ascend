import React, { useRef, useState } from 'react';
import { Exploration } from '../types';
import { Project, ProjectFolder } from '../projects';
import { ChevronIcon } from '../icons';
import { parseSnapshot } from './WorkbenchMenu';

interface Props {
  projects: Project[]; folders: ProjectFolder[]; activeId: string | null; ready: boolean;
  onSelect: (project: Project) => Promise<void>;
  onCreate: (name: string) => Promise<void>;
  onImport: (snapshot: Exploration) => Promise<void>;
  onFork: (name: string, count: number) => Promise<void>;
  onFolder: (name: string, id?: string) => Promise<void>;
  onMove: (project: Project, folderId: string | null) => Promise<void>;
  onRename: (project: Project, name: string) => Promise<void>;
}
export function ProjectSidebar(props: Props) {
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem('projects-collapsed') === 'true'; } catch { return false; } });
  const [folded, setFolded] = useState<string[]>(() => {
    try { const saved = JSON.parse(localStorage.getItem('project-folders-folded') || '[]'); return Array.isArray(saved) ? saved.filter(id => typeof id === 'string') : []; } catch { return []; }
  });
  const toggleFolder = (id: string) => {
    setFolded(previous => {
      const next = previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id];
      try { localStorage.setItem('project-folders-folded', JSON.stringify(next)); } catch { /* optional preference */ }
      return next;
    });
  };
  const [editor, setEditor] = useState<{kind: 'project' | 'folder' | 'rename-folder' | 'rename-project' | 'fork'; id?: string} | null>(null);
  const [name, setName] = useState('');
  const [count, setCount] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const file = useRef<HTMLInputElement>(null);
  const active = props.projects.find(p => p.id === props.activeId);
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await action(); setEditor(null); } catch (e) { setError(e instanceof Error ? e.message : '操作失败，请重试'); }
    finally { setBusy(false); }
  };
  const edit = (kind: NonNullable<typeof editor>['kind'], value = '', id?: string) => { setName(value); setCount(1); setEditor({kind, id}); setError(''); };
  const moveDrop = (e: React.DragEvent, folderId: string | null) => {
    e.preventDefault();
    const project = props.projects.find(p => p.id === e.dataTransfer.getData('application/x-workbench-project'));
    if (project && !busy) void run(() => props.onMove(project, folderId));
  };
  const rows = (folderId: string | null) => props.projects.filter(p => p.folderId === folderId).sort((a,b) => b.updatedAt.localeCompare(a.updatedAt)).map(project =>
    <div key={project.id} className={`project-row ${project.id === props.activeId ? 'selected' : ''}`} draggable={!busy}
      onDragStart={e => { e.dataTransfer.setData('application/x-workbench-project', project.id); e.dataTransfer.effectAllowed = 'move'; }}>
      <button disabled={busy} className="project-open" onClick={() => void run(() => props.onSelect(project))} title={project.name} aria-current={project.id === props.activeId ? 'page' : undefined}>
        <span>{project.name}</span><small>{project.kind === 'server' ? '服务端' : project.snapshot.forkSource ? 'Fork · 本地' : '本地'} · {project.snapshot.nodes.length} 节点</small>
      </button>
    </div>);
  return <aside className={`project-sidebar ${collapsed ? 'collapsed' : ''}`} aria-label="项目管理">
    <div className="project-sidebar-heading">
      {!collapsed && <strong>项目</strong>}
      <button className="sidebar-toggle" aria-label={collapsed ? '展开项目栏' : '收起项目栏'} aria-expanded={!collapsed} onClick={() => {
        setCollapsed(!collapsed); try { localStorage.setItem('projects-collapsed', String(!collapsed)); } catch { /* optional preference */ }
      }}><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/></svg></button>
    </div>
    {!collapsed && <>
      <div className="project-actions">
        <button disabled={!props.ready || busy} onClick={() => edit('project')}>新建项目</button>
        <button disabled={!props.ready || busy} onClick={() => file.current?.click()}>导入</button>
        <button disabled={!props.ready || busy} onClick={() => edit('folder')}>新建文件夹</button>
      </div>
      <input ref={file} type="file" accept=".json,application/json" hidden onChange={e => {
        const value = e.target.files?.[0]; e.target.value = ''; if (!value) return;
        void run(async () => { if (value.size > 50 * 1024 * 1024) throw new Error('快照超过 50 MB'); await props.onImport(parseSnapshot(await value.text())); });
      }}/>
      {editor && <form className="project-editor" onSubmit={e => { e.preventDefault(); const title = name.trim(); if (!title) return;
        void run(async () => {
          if (editor.kind === 'project') await props.onCreate(title);
          if (editor.kind === 'folder' || editor.kind === 'rename-folder') await props.onFolder(title, editor.id);
          if (editor.kind === 'rename-project') { const p = props.projects.find(p => p.id === editor.id); if (p) await props.onRename(p, title); }
          if (editor.kind === 'fork') await props.onFork(title, count);
        });
      }}>
        <label>{editor.kind === 'fork' ? '从当前已保存截面 Fork' : editor.kind.includes('rename') ? '重命名' : editor.kind === 'folder' ? '文件夹名称' : '项目名称'}
          <input autoFocus required maxLength={120} value={name} onChange={e => setName(e.target.value)} />
        </label>
        {editor.kind === 'fork' && <><label>副本数量<input type="number" min={1} max={10} value={count} onChange={e => setCount(Number(e.target.value))}/></label><p>保留证据，不启动 Agent 或复制在途任务。</p></>}
        <div><button disabled={busy || !name.trim()}>确定</button><button type="button" onClick={() => setEditor(null)}>取消</button></div>
      </form>}
      {error && <p className="project-error" role="alert">{error}</p>}
      <div className="project-list" aria-busy={busy}>
        {!props.ready && <p className="project-empty">正在读取项目…</p>}
        <section className="project-folder" onDragOver={e => e.preventDefault()} onDrop={e => moveDrop(e, null)}>
          <div className="project-folder-heading"><button className="folder-toggle" aria-expanded={!folded.includes('__unfiled__')} onClick={() => toggleFolder('__unfiled__')}><ChevronIcon direction={folded.includes('__unfiled__') ? 'right' : 'down'} size={12}/><span>未分类</span><small>{props.projects.filter(p => !p.folderId).length}</small></button></div>
          {!folded.includes('__unfiled__') && <>{rows(null)}{!props.projects.some(p => !p.folderId) && <p className="project-empty">可将项目拖到这里</p>}</>}
        </section>
        {props.folders.map(folder => <section key={folder.id} className="project-folder" onDragOver={e => e.preventDefault()} onDrop={e => moveDrop(e, folder.id)}>
          <div className="project-folder-heading"><button className="folder-toggle" aria-expanded={!folded.includes(folder.id)} onClick={() => toggleFolder(folder.id)}><ChevronIcon direction={folded.includes(folder.id) ? 'right' : 'down'} size={12}/><span>{folder.name}</span><small>{props.projects.filter(p => p.folderId === folder.id).length}</small></button><button title="重命名文件夹" aria-label={`重命名文件夹 ${folder.name}`} onClick={() => edit('rename-folder', folder.name, folder.id)}>改名</button></div>
          {!folded.includes(folder.id) && <>{rows(folder.id)}{!props.projects.some(p => p.folderId === folder.id) && <p className="project-empty">拖入项目</p>}</>}
        </section>)}
      </div>
      {active && <div className="active-project-actions">
        <div><button disabled={busy} onClick={() => edit('fork', `${active.name} · Fork`)}>Fork 当前项目</button><button disabled={busy} onClick={() => edit('rename-project', active.name, active.id)}>改名</button></div>
        <label>移至<select value={active.folderId || ''} disabled={busy} onChange={e => void run(() => props.onMove(active, e.target.value || null))}><option value="">未分类</option>{props.folders.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
        <small>本地项目保存在此浏览器；请导出备份。</small>
      </div>}
    </>}
  </aside>;
}
