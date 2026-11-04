import {Exploration} from '../types';
import {MeasurementView} from '../measurements';

export function MeasurementEvidence({report}:{report:MeasurementView}){
 return <section className="structured-exp-box measurement-evidence" aria-label="逐 case 测量记录">
  <p className="capture-note"><strong>{report.synthetic?'模拟测量 · 非实测':'导入测量记录'}</strong> · {report.runId || 'Run 未确认'} · {report.recordedAt || '时间未确认'}</p>
  {!report.valid && <p className="text-fail">记录无效：{report.issues.join('；')}</p>}
  {report.protocol && <p className="capture-note">预热 {report.protocol.warmupRuns} 次 · 采样 {report.protocol.repeatRuns} 次 · {report.protocol.timingBoundary} · {report.protocol.syncMethod}</p>}
  <div className="measurement-table-scroll"><table className="compare-table"><thead><tr><th>必选 case</th><th>精度记录</th><th>样本</th><th>Median / P95 (μs)</th><th>相对基线</th></tr></thead><tbody>{report.cases.map(c=><tr key={c.id}><td>{c.label}<small className="measurement-case-id">{c.id}</small></td><td className={c.accuracy==='failed'?'text-fail':c.accuracy==='passed'?'text-ok':''}>{c.accuracy==='passed'?'通过':c.accuracy==='failed'?'失败':'未验证'}</td><td>{c.samplesUs.length}</td><td>{c.medianUs?.toFixed(2) ?? '—'} / {c.p95Us?.toFixed(2) ?? '—'}</td><td>{report.valid&&report.comparable&&c.speedup?`${c.speedup.toFixed(2)}×`:'不可比'}</td></tr>)}</tbody></table></div>
  <p className="capture-note">{report.comparisonReason}{report.valid&&report.comparable&&report.speedup?`：${report.speedup.toFixed(2)}×`:''}</p>
 </section>;
}

export function MeasurementComparison({exploration,onOpen}:{exploration:Exploration;onOpen:(path:string)=>void}){
 const data=exploration.harness!.measurements!;
 return <section className="compare-sheet"><h2 className="sheet-title">候选测量对比</h2><p className="capture-note">基线：{data.baselinePath || '未配置有效基线'}。综合加速比为全部必选 case 的等权几何平均；不同 shape 的时延分别保留。</p>
 {data.errors.map((error,i)=><p key={i} className="text-fail">{error}</p>)}
 <div className="measurement-table-scroll"><table className="compare-table"><thead><tr><th>候选</th><th>最近有效记录</th><th>必选 case</th><th>综合加速比</th><th>可比性</th></tr></thead><tbody>{exploration.nodes.filter(n=>n.harness?.kind==='candidate').map(node=>{
  const report=data.reports.find(r=>r.path===data.latest[node.id]);
  return <tr key={node.id}><td>{node.id} · {node.title}</td><td>{report?<button className="toolbar-btn" onClick={()=>onOpen(report.path)}>{report.runId}{report.synthetic?' · 模拟':''}</button>:'待核对 / 未导入'}</td><td>{report?`${report.cases.filter(c=>c.accuracy==='passed').length}/${report.cases.length} 通过`:'—'}</td><td>{report?.comparable?`${report.speedup!.toFixed(2)}×`:'—'}</td><td>{report?.comparisonReason || node.stopOrFailReason || '暂无测量记录'}</td></tr>;
 })}</tbody></table></div>
 <h3 className="sub-heading">全部测量与复测记录</h3>
 {data.reports.map((r,i)=><button className="graph-relation-row" key={`${r.path}:${i}`} onClick={()=>onOpen(r.path)} disabled={!exploration.resources[r.path]}>{r.subjectId} · {r.runId || r.path} · {r.synthetic?'模拟':'导入'}<small>{r.valid?`${r.recordedAt} · ${r.comparisonReason}`:r.issues.join('；')}</small></button>)}
 </section>;
}
