import type {Exploration} from './types';
import type {HarnessModel} from './harnessAdapter';

export interface CaseMeasurement {
 id:string; label:string; accuracy:'passed'|'failed'|'not_run'; samplesUs:number[];
 medianUs?:number; p95Us?:number; maxAbsDiff?:number; speedup?:number;
}
export interface MeasurementView {
 path:string; subjectId:string; kind:'candidate'|'baseline'; runId:string; recordedAt:string;
 synthetic:boolean; valid:boolean; issues:string[]; cases:CaseMeasurement[];
 correctnessPassed:boolean; complete:boolean; comparable:boolean; comparisonReason:string;
 speedup?:number; group:string; protocol?:{warmupRuns:number;repeatRuns:number;timingBoundary:string;syncMethod:string};
}
export interface MeasurementCollection {
 reports:MeasurementView[]; latest:Record<string,string>; baselinePath?:string; errors:string[];
}
type JsonObject=Record<string,unknown>;
function object(value:unknown):value is JsonObject {return !!value&&typeof value==='object'&&!Array.isArray(value);}
function requireValue(condition:unknown,message:string):asserts condition {if(!condition)throw new Error(message);}
const positive=(n:unknown):n is number=>typeof n==='number'&&Number.isFinite(n)&&n>0;
const integer=(n:unknown,min:number):n is number=>typeof n==='number'&&Number.isInteger(n)&&n>=min;
const text=(n:unknown):n is string=>typeof n==='string'&&n.trim().length>0;
export function sampleStatistics(samples:number[]){
 const sorted=[...samples].sort((a,b)=>a-b),n=sorted.length;
 return {medianUs:n?(sorted[Math.floor((n-1)/2)]+sorted[Math.floor(n/2)])/2:undefined,p95Us:n?sorted[Math.ceil(n*.95)-1]:undefined};
}

/** Schema-backed imports are record validation, not hardware execution or independent attestation. */
export function applyMeasurements(exp:Exploration,contents:Record<string,string>,model:HarnessModel):boolean {
 if(!Object.prototype.hasOwnProperty.call(contents,'MEASUREMENTS.json'))return false;
 const collection:MeasurementCollection={reports:[],latest:{},errors:[]};
 exp.harness!.measurements=collection;
 const readJson=(path:string):unknown=>{requireValue(typeof contents[path]==='string',`文件未归档或不可读取：${path}`);return JSON.parse(contents[path]);};
 const reference=(value:unknown):{path:string;sha256:string}=>{
  requireValue(object(value)&&text(value.path)&&typeof value.sha256==='string'&&/^[a-f0-9]{64}$/.test(value.sha256),'引用必须包含精确路径与 SHA-256');
  const resource=exp.resources[value.path];
  requireValue(resource&&!resource.missingReason&&resource.sha256===value.sha256,`文件缺失或摘要不匹配：${value.path}`);
  return {path:value.path,sha256:value.sha256};
 };
 let index:JsonObject;
 try{
  const value=readJson('MEASUREMENTS.json');
  requireValue(object(value)&&value.schema==='harness-vis/measurements@1'&&Array.isArray(value.reports),'测量索引格式无效');
  requireValue(value.reports.length<=1000,'测量索引超过 1000 份报告');index=value;
 }catch(error){collection.errors.push(String(error));return true;}
 const entries=index.reports as unknown[];
 for(const entry of entries){
  const hint=object(entry)?entry:{};
  const view:MeasurementView={path:typeof hint.path==='string'?hint.path:'',subjectId:typeof hint.subjectId==='string'?hint.subjectId:'未知',kind:hint.kind==='baseline'?'baseline':'candidate',runId:'',recordedAt:'',synthetic:false,valid:false,issues:[],cases:[],correctnessPassed:false,complete:false,comparable:false,comparisonReason:'未校验',group:''};
  collection.reports.push(view);
  try{
   requireValue(text(hint.subjectId)&&(hint.kind==='candidate'||hint.kind==='baseline'),'索引条目必须声明 kind 与 subjectId');
   const source=reference(entry);
   const report=readJson(source.path);
   requireValue(object(report)&&report.schema==='harness-vis/measurement@1','测量报告 schema 无效');
   requireValue(typeof report.synthetic==='boolean','报告必须明确声明 synthetic');view.synthetic=report.synthetic;
   requireValue(object(report.subject)&&report.subject.kind===hint.kind&&report.subject.id===hint.subjectId,'报告与索引的主体身份不一致');
   const subject=reference(report.subject.manifest);
   requireValue(text(report.runId)&&source.path.startsWith(`runs/${report.runId}/`),'报告必须属于声明 Run 的目录');view.runId=report.runId;
   const run=model.runs.find(r=>r.id===report.runId);
   requireValue(run,'报告的 Run 未归档');
   if(view.kind==='candidate'){
    const node=exp.nodes.find(n=>n.id===view.subjectId&&n.harness?.kind==='candidate');
    requireValue(node&&exp.resources[node.harness!.manifestPath!]?.sha256===subject.sha256,'候选清单身份不匹配');
    requireValue(run.node==='device-test'&&node.stages.verify_correctness.attempts.some(a=>a.id===run.id),'测试 Run 未明确绑定该候选');
   }else{
    const record=model.records.find(r=>r.path===subject.path);
    requireValue(record?.kind==='baseline'&&record.fields.baseline_id===view.subjectId&&run.node==='establish-baseline','标杆身份或生成 Run 不匹配');
   }
   requireValue(text(report.recordedAt)&&/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(report.recordedAt)&&Number.isFinite(Date.parse(report.recordedAt)),'recordedAt 必须是带时区的有效时间');view.recordedAt=report.recordedAt;
   const environment=reference(report.environment),referenceImpl=reference(report.reference),caseSet=reference(report.caseSet),protocol=reference(report.protocol);
   const protocolData=readJson(protocol.path),caseData=readJson(caseSet.path);
   requireValue(object(protocolData)&&protocolData.schema==='harness-vis/timing-protocol@1'&&protocolData.unit==='us'&&integer(protocolData.warmupRuns,0)&&integer(protocolData.repeatRuns,3)&&text(protocolData.timingBoundary)&&text(protocolData.syncMethod),'计时协议必须包含 us 单位、预热/采样次数和明确计时/同步边界');
   view.protocol={warmupRuns:protocolData.warmupRuns,repeatRuns:protocolData.repeatRuns,timingBoundary:protocolData.timingBoundary,syncMethod:protocolData.syncMethod};
   requireValue(object(caseData)&&caseData.schema==='harness-vis/case-set@1'&&Array.isArray(caseData.cases)&&caseData.cases.length>0,'必选 case 集无效');
   const required=caseData.cases;
   requireValue(required.every(c=>object(c)&&text(c.id)&&text(c.label))&&new Set(required.map(c=>(c as JsonObject).id)).size===required.length,'必选 case ID 必须唯一，且包含标签');
   requireValue(Array.isArray(report.cases)&&report.cases.length>0,'报告缺少 case 结果');
   const seen=new Set<string>();
   for(const result of report.cases){
    requireValue(object(result)&&text(result.id)&&!seen.has(result.id),'报告 case ID 缺失或重复');seen.add(result.id);
    const definition=required.find(c=>(c as JsonObject).id===result.id) as JsonObject|undefined;
    requireValue(definition,`未知 case：${result.id}`);
    requireValue(['passed','failed','not_run'].includes(String(result.accuracy)),'精度状态必须明确为 passed/failed/not_run');
    requireValue(Array.isArray(result.samplesUs)&&result.samplesUs.every(positive),'原始样本必须是有限正数，单位 μs');
    requireValue(result.samplesUs.length===0||result.samplesUs.length===protocolData.repeatRuns,`case ${result.id} 采样数与协议不符`);
    requireValue(result.accuracy==='passed'||result.samplesUs.length===0,'未通过精度的 case 不得提供常规性能样本');
    requireValue(result.maxAbsDiff===undefined||typeof result.maxAbsDiff==='number'&&Number.isFinite(result.maxAbsDiff)&&result.maxAbsDiff>=0,'误差必须为有限非负数');
    view.cases.push({id:result.id,label:String(definition.label),accuracy:result.accuracy as CaseMeasurement['accuracy'],samplesUs:result.samplesUs,...sampleStatistics(result.samplesUs),maxAbsDiff:result.maxAbsDiff as number|undefined});
   }
   for(const definition of required as JsonObject[])if(!seen.has(String(definition.id)))view.cases.push({id:String(definition.id),label:String(definition.label),accuracy:'not_run',samplesUs:[]});
   view.cases.sort((a,b)=>required.findIndex(c=>(c as JsonObject).id===a.id)-required.findIndex(c=>(c as JsonObject).id===b.id));
   view.correctnessPassed=view.cases.every(c=>c.accuracy==='passed');
   view.complete=view.correctnessPassed&&view.cases.every(c=>c.samplesUs.length===protocolData.repeatRuns);
   view.group=JSON.stringify([report.synthetic,environment.sha256,referenceImpl.sha256,caseSet.sha256,protocol.sha256]);
   view.valid=true;view.comparisonReason=view.complete?'尚未选择可比基线':'必选 case 未全部通过或未完成采样';
  }catch(error){view.issues.push(String(error));view.comparisonReason='记录校验失败';}
  if(exp.resources[view.path]){exp.resources[view.path].measurementData=view;exp.resources[view.path].category='experiment';}
 }
 // Ambiguous duplicate file entries and tied timestamps cannot silently choose a winning result.
 const duplicatePaths=new Set(collection.reports.filter((r,i,all)=>all.findIndex(x=>x.path===r.path)!==i).map(r=>r.path));
 for(const report of collection.reports)if(duplicatePaths.has(report.path)){report.valid=false;report.issues.push('索引重复引用同一报告');}
 let baseline:MeasurementView|undefined;
 if(index.baseline!==undefined){
  try{const ref=reference(index.baseline);baseline=collection.reports.find(r=>r.path===ref.path);requireValue(baseline?.valid&&baseline.complete,'选定基线未完整通过校验、精度或采样');collection.baselinePath=baseline.path;}
  catch(error){collection.errors.push(`基线不可用：${String(error)}`);baseline=undefined;}
 }
 for(const report of collection.reports){
  if(!report.valid)continue;
  if(!baseline){report.comparisonReason='未配置有效基线';continue;}
  if(report.group!==baseline.group){report.comparisonReason='与基线的模拟标记、环境、参考、case 集或计时协议不一致';continue;}
  if(!report.complete)continue;
  for(const c of report.cases)c.speedup=baseline.cases.find(b=>b.id===c.id)!.medianUs!/c.medianUs!;
  const speedup=Math.exp(report.cases.reduce((sum,c)=>sum+Math.log(c.speedup!),0)/report.cases.length);
  if(!positive(speedup)){report.comparisonReason='加速比超出可表示范围';continue;}
  report.comparable=true;report.speedup=speedup;report.comparisonReason='全部必选 case 与基线可比；等权几何平均加速比';
 }
 for(const node of exp.nodes.filter(n=>n.harness?.kind==='candidate')){
  const reports=collection.reports.filter(r=>r.subjectId===node.id&&r.kind==='candidate');
  if(!reports.length)continue;
  // Any invalid record for this identity blocks summary ranking until corrected; raw history stays visible.
  const invalid=reports.some(r=>!r.valid);
  const ordered=reports.filter(r=>r.valid).sort((a,b)=>Date.parse(b.recordedAt)-Date.parse(a.recordedAt));
  const latest=ordered[0];
  const tied=ordered.length>1&&Date.parse(ordered[0].recordedAt)===Date.parse(ordered[1].recordedAt);
  if(invalid||tied||!latest){node.harness!.recordedStatus='测量待核对';node.stopOrFailReason=invalid?'存在身份或格式不匹配的报告，暂停汇总':'复测时间相同，无法确定最新结果';continue;}
  collection.latest[node.id]=latest.path;node.harness!.measurementPath=latest.path;node.harness!.measurementCaseCount=latest.cases.length;node.harness!.synthetic=latest.synthetic;
  node.correctnessPassed=latest.correctnessPassed;node.latencyUs=latest.cases.length===1&&latest.complete?latest.cases[0].medianUs:undefined;
  node.speedupVsBaseline=latest.comparable?latest.speedup:undefined;
  node.harness!.recordedStatus=(latest.synthetic?'模拟':'记录')+(latest.correctnessPassed?'精度通过':latest.cases.some(c=>c.accuracy==='failed')?'精度失败':'验证未完成');
  node.executionStatus=latest.cases.some(c=>c.accuracy==='failed')?'failed':latest.complete?'completed':'pending';
  node.stopOrFailReason=latest.comparable?`${latest.cases.length} 个 case 可比`:latest.comparisonReason;
  node.primaryKernelVersionId=latest.path;
 }
 const eligible=exp.nodes.filter(n=>n.harness?.measurementPath&&n.speedupVsBaseline!==undefined).sort((a,b)=>b.speedupVsBaseline!-a.speedupVsBaseline!);
 exp.bestValidNodeId=eligible[0]?.id||'';
 if(baseline){exp.baselineRef={id:baseline.subjectId,version:baseline.path,latencyUs:baseline.cases.length===1?baseline.cases[0].medianUs!:0,description:'所选测量基线；多 case 不合并为单一时延'};}
 // Mixed or invalid provenance is not advertised as wholly synthetic or wholly device-derived.
 const valid=collection.reports.filter(r=>r.valid);
 exp.harness!.synthetic=valid.length>0&&valid.every(r=>r.synthetic);
 for(const report of collection.reports)if(report.issues.length)exp.harness!.warnings.push({kind:'measurement_invalid',message:report.issues.join('；'),paths:report.path?[report.path]:[]});
 for(const message of collection.errors)exp.harness!.warnings.push({kind:'measurement_index',message,paths:['MEASUREMENTS.json']});
 return true;
}
