import { Exploration } from './types';

export interface Project {
  id: string;
  name: string;
  folderId: string | null;
  kind: 'local' | 'server';
  snapshot: Exploration;
  createdAt: string;
  updatedAt: string;
}
export interface ProjectFolder { id: string; name: string; }
let database: Promise<IDBDatabase> | undefined;
function openDatabase() {
  if (!database) database = new Promise<IDBDatabase>((resolve, reject) => {
  const req = indexedDB.open('workbench-projects', 1);
  req.onupgradeneeded = () => {
    req.result.createObjectStore('projects', { keyPath: 'id' });
    req.result.createObjectStore('folders', { keyPath: 'id' });
  };
  req.onsuccess = () => resolve(req.result);
  req.onerror = () => reject(req.error);
  });
  return database;
}
export async function readLibrary(): Promise<{projects: Project[]; folders: ProjectFolder[]}> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(['projects', 'folders']);
    const projects = tx.objectStore('projects').getAll();
    const folders = tx.objectStore('folders').getAll();
    tx.oncomplete = () => resolve({projects: projects.result, folders: folders.result});
    tx.onerror = () => reject(tx.error);
  });
}
export async function saveEntries(store: 'projects' | 'folders', values: (Project | ProjectFolder)[]) {
  const db = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(store, 'readwrite');
    values.forEach(value => tx.objectStore(store).put(value));
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(tx.error || new Error('本地存储写入失败'));
    tx.onerror = () => reject(tx.error);
  });
}
export function newExploration(name: string): Exploration {
  return {
    id: crypto.randomUUID(), title: name, operatorName: name, targetGoal: '', inputDomain: '', environment: '',
    baselineRef: {id: '', version: '', latencyUs: 0, description: ''},
    budget: {usedEvaluations: 0, reservedEvaluations: 0, maxEvaluations: 24, elapsedMinutes: 0, maxMinutes: 60, noGainWindow: 3, minGainPct: 2},
    coordinatorStatus: 'stopped', stopReason: '待配置任务并接入执行器', bestValidNodeId: '', unfinishedDirections: [],
    nodes: [], agents: [], references: [], resources: {}, resourceLinks: [], knowledgeItems: [],
  };
}
export function forkExploration(source: Exploration, name: string): Exploration {
  const fork = structuredClone(source);
  fork.id = crypto.randomUUID();
  fork.title = name;
  fork.forkSource = {explorationId: source.id, title: source.title, eventSeq: source.lastEventSeq ?? 0, capturedAt: new Date().toISOString()};
  fork.coordinatorStatus = 'stopped';
  fork.stopReason = '从已保存截面派生，等待新 session 接手；不继承源项目在途执行';
  fork.lastEventSeq = 0;
  fork.version = 1;
  // Completed evidence and exact dependencies remain unchanged; active work is
  // detached in this copy only. Source sessions and device jobs are unaffected.
  fork.budget.reservedEvaluations = 0;
  for (const node of fork.nodes) {
    node.explorationId = fork.id;
    if (node.executionStatus === 'running') node.executionStatus = 'stopped';
    for (const stage of Object.values(node.stages)) {
      if (stage.status === 'running') stage.status = 'stopped';
      for (const attempt of stage.attempts) {
        if (attempt.status === 'running' || attempt.status === 'pending') {
          attempt.sourceStatus = attempt.status;
          attempt.status = 'stopped';
          attempt.failureOrSkipReason = '截面派生：此副本不继承在途执行；源任务状态需在源项目核对';
        }
      }
    }
  }
  for (const agent of fork.agents) {
    agent.historicalOnly = true;
    agent.status = 'stopped';
    delete agent.currentNodeId;
    delete agent.currentStage;
  }
  fork.references.forEach(ref => { ref.explorationId = fork.id; });
  fork.knowledgeItems.forEach(item => { item.explorationId = fork.id; });
  return fork;
}
export function localProject(snapshot: Exploration, name = snapshot.title): Project {
  const now = new Date().toISOString();
  return {id: crypto.randomUUID(), name, folderId: null, kind: 'local', snapshot, createdAt: now, updatedAt: now};
}
