import React from 'react';
import type {Exploration} from '../types';
import type {TrajectorySelection} from './TrajectoryWorkbench';
import {MarkdownContent} from './MarkdownContent';
import {markdownSection,nativeVerdicts,acceptedRun} from '../nativeRecords';
import {recordedStatus} from '../harnessAdapter';

export function HarnessRecords({exploration,selection,onSelect}:{exploration:Exploration;selection:TrajectorySelection;onSelect:(s:TrajectorySelection)=>void}){
  const resources=Object.values(exploration.resources);
  const environment=resources.filter(r=>r.filename.startsWith('environments/')||r.filename==='ENVIRONMENT.md');
  const warnings=exploration.harness?.warnings||[];
  const node=exploration.harness?.search?.searchNodes.find(n=>n.id===(selection.searchNodeId||exploration.harness?.search?.currentNodeId));
  const run=node?.runs.find(r=>r.id===selection.runId)||node?.runs[node.runs.length-1];
  const result=run?.resultPath?exploration.resources[run.resultPath]:undefined;
  const content=result?.content||'';
  const verdicts=nativeVerdicts(content);
  const updated=content.match(/^- updated_at:\s*(.+)$/m)?.[1];
  const jobs=markdownSection(content,'作业、资源与预算');
  const acceptance=run?acceptedRun(exploration.resources['search/tree.json']?.content||'',run.id,resources.map(r=>({path:r.id,status:r.missingReason?'omitted':'copied',sha256:r.sha256,sizeBytes:r.sizeBytes||0}))):'';
  const binding=run?.taskPath?markdownSection(exploration.resources[run.taskPath]?.content||'','权限、预算与归属'):'';
  return <div className="harness-native-records">
    {warnings.length>0&&<details><summary>读取提示 · {warnings.length}</summary>{warnings.map((w,i)=><p key={i}>{w.message} {w.paths.map(path=><button key={path} className="hv-link-chip" onClick={()=>onSelect({kind:'file',filePath:path})}>{path}</button>)}</p>)}</details>}
    <details><summary>环境与仿真约定 · {environment.length} 份原始记录</summary>
      <p>仅展示 Harness 记录；不执行探测、命令或模拟。具体型号与版本以约定原文为准。</p>
      {environment.map(r=><button key={r.id} className="hv-link-chip" onClick={()=>onSelect({kind:'file',filePath:r.id})}>{r.filename}</button>)}
      {!environment.length&&<p>尚未捕获环境约定。</p>}
    </details>
    {run&&<details className="native-checkpoint"><summary>{run.id} · {recordedStatus[run.status]||'状态未知'}{updated&&` · ${updated}`}</summary>
      <p>快照检查点，不代表执行器当前存活。任务完成、证据判定与外部作业状态相互独立。</p><p>{acceptance}</p>
      {verdicts.length>0?<table className="compare-table"><tbody>{verdicts.map(row=><tr key={row[0]}>{row.map((cell,i)=><td key={i}>{cell}</td>)}</tr>)}</tbody></table>:<p>未读到明确的原生判定表，请查看 RESULT 原文。</p>}
      {result&&<button className="hv-link-chip" onClick={()=>onSelect({kind:'file',filePath:result.id})}>查看 RESULT 原文</button>}
      {binding&&<details><summary>本次 TASK 环境绑定、权限和预算原文</summary><MarkdownContent content={binding}/></details>}
      {jobs&&<details><summary>作业、资源与预算原文</summary><MarkdownContent content={jobs}/></details>}
    </details>}
  </div>;
}
