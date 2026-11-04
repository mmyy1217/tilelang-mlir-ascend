import {useEffect,useRef,useState} from 'react';
import {App} from './App';
import {harnessExploration, Capture, HarnessModel} from './harnessAdapter';
import {Exploration} from './types';
import {Project} from './projects';
import './harness.css';
const empty:HarnessModel={projectId:'empty',snapshotId:'empty',projectName:'Harness Vis',capturedAt:'',consistency:'unknown',records:[],runs:[],annotations:[],warnings:[],manifest:{files:[]}};
const emptyExploration:Exploration={...harnessExploration(empty,{}),nodes:[],harness:{snapshotId:'',capturedAt:'',consistency:'unknown',runCount:0,warnings:[]}};
async function read<T>(url:string,signal?:AbortSignal):Promise<T>{const response=await fetch(url,{signal});if(!response.ok)throw new Error(`读取失败 (${response.status})：${(await response.text()).slice(0,160)}`);return response.json();}
/** Transport only. All layout and rendering remain in the original agent-vis components. */
export function HarnessApp(){
 const [captures,setCaptures]=useState<Capture[]>([]);
 const [watchers,setWatchers]=useState<{projectId:string;state:string;error?:string|null;intervalSeconds:number;settleSeconds:number}[]>([]);
 const [project,setProject]=useState(()=>new URLSearchParams(location.search).get('captureProject')||'');
 const [pinned,setPinned]=useState(()=>new URLSearchParams(location.search).get('follow')==='1' ? '' : new URLSearchParams(location.search).get('capture')||'');
 const [exploration,setExploration]=useState<Exploration>(emptyExploration);
 const [ready,setReady]=useState(false);
 const [error,setError]=useState('');
 const [connected,setConnected]=useState(false);
 const [refresh,setRefresh]=useState(0);
 const cache=useRef(new Map<string,Record<string,string>>());
 const lastVersion=useRef('');
 const current=captures.find(c=>c.projectId===project&&(!pinned||c.id===pinned))||(!project?captures[0]:undefined);
 useEffect(()=>{let active=true;const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
  async function poll(){try{const result=await read<{items:Capture[];unavailable:string[];watchers?:typeof watchers}>('/api/harness/snapshots',controller.signal);if(!active)return;setCaptures(result.items);setWatchers(result.watchers||[]);setProject(p=>p||result.items[0]?.projectId||'');setConnected(true);setReady(true);setError(result.unavailable.length?`${result.unavailable.length} 份快照不可读取`: '');}catch(e){if(active){setError(String(e));setConnected(false);setReady(true);}}finally{if(active)timer=setTimeout(poll,5000);}}
  void poll();return()=>{active=false;controller.abort();clearTimeout(timer);};
 },[refresh]);
 const key=current?`${current.projectId}/${current.id}`:'';
 useEffect(()=>{if(!current)return;const controller=new AbortController();let active=true;let timer:ReturnType<typeof setTimeout>;
  const base=`/api/harness/projects/${current.projectId}/snapshots/${current.id}`;
  async function load(){try{const model=await read<HarnessModel>(base,controller.signal);const revision=key+':'+(model.viewRevision||'base');if(lastVersion.current===revision)return;
   let contents=cache.current.get(key);
   if(!contents){contents={};const files=model.manifest.files.filter(f=>f.status==='copied'&&f.text);for(let i=0;i<files.length;i+=6){await Promise.all(files.slice(i,i+6).map(async file=>{try{const data=await read<{content:string}>(`${base}/file?path=${encodeURIComponent(file.path)}`,controller.signal);contents![file.path]=data.content;}catch(e){if(controller.signal.aborted)throw e;}}));}if(!active)return;cache.current.set(key,contents);while(cache.current.size>3)cache.current.delete(cache.current.keys().next().value!);}
   if(active){setExploration(harnessExploration(model,contents));lastVersion.current=revision;}
  }catch(e){if(active)setError(String(e));}finally{if(active)timer=setTimeout(load,5000);}}
  void load();return()=>{active=false;controller.abort();clearTimeout(timer);};
 },[key,refresh]);
 const projects:Project[]=[...new Map(captures.map(c=>[c.projectId,c])).values()].map(c=>({id:c.projectId,name:c.projectName,folderId:null,kind:'local',createdAt:c.capturedAt,updatedAt:c.capturedAt,snapshot:c.projectId===current?.projectId?exploration:{...emptyExploration,id:c.projectId,title:c.projectName}}));
 const select=async(p:Project)=>{if(p.id===project)return;setProject(p.id);setPinned('');setExploration(emptyExploration);};
 const watch=watchers.find(w=>w.projectId===project);
 const watchLabel=watch?({watching:'自动采集已开启',settling:'等待文件稳定',capturing:'正在采集',partial:'采集不稳定，等待变化',error:'自动采集异常',stale:'采集心跳已过期',stopped:'自动采集已停止'}[watch.state]||'采集状态未知'):'手动采集';
 const controls=<div className="capture-controls"><span className="capture-watch-state" role="status" title={watch?.error || (watch?`轮询 ${watch.intervalSeconds} 秒，稳定等待 ${watch.settleSeconds} 秒；浏览器每 5 秒更新`:'启动 Skill 的 watch 命令可持续采集 workspace')}>{watchLabel}</span><label className="capture-select-label">快照<select aria-label="快照版本" value={pinned} onChange={e=>setPinned(e.target.value)}><option value="">跟随最新</option>{captures.filter(c=>c.projectId===project).map(c=><option value={c.id} key={c.id}>{new Date(c.capturedAt).toLocaleString()}</option>)}</select></label><button className="share-loc-btn" onClick={()=>setRefresh(v=>v+1)} title="刷新已采集快照；自动采集由独立 watch 进程执行">{connected?'刷新索引':'重新连接'}</button></div>;
 return <App harness={{exploration,projects,activeId:project,ready,error,onSelect:select,controls,followLatest:!pinned}}/>;
}
