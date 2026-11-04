import {applyMeasurements} from './measurements';
import {applySyntheticDemo} from './syntheticDemo';
import { Exploration, ExecutionStatus, NodeRecord, ResourceCategory, StageMeta, StageRecord, StageType, STAGE_METAS } from './types';
export type Capture = {id:string;projectId:string;projectName:string;capturedAt:string;consistency:string;totalBytes:number};
export type HarnessModel = {
 snapshotId:string;projectId:string;projectName:string;capturedAt:string;consistency:string;viewRevision?:string;
 records:{id:string;path:string;kind:string;runId:string|null;title:string;fields:Record<string,string>;sha256:string}[];
 runs:{id:string;node:string;operation:string;status:string;createdAt?:string;parentRun?:string;records:string[];taskPath?:string;resultPath?:string}[];
 annotations:{title:string;body:string;sources:string[]}[];
 warnings:{kind:string;message:string;paths:string[]}[];
 manifest:{files:{path:string;status:string;text?:boolean;sizeBytes:number;sha256?:string;reason?:string}[]};
};
const labels:Record<string,string>={'prepare-environment':'准备环境','establish-baseline':'建立标杆',analysis:'分析',develop:'开发','device-test':'测试'};
export const recordedStatus:Record<string,string>={completed:'交付已完成',running:'记录为运行中',failed:'交付失败',blocked:'受阻',needs_input:'等待输入',interrupted:'已中断',unknown:'未记录',registered:'候选已登记',workspace:'项目记录'};
export function stageStatus(stage:StageRecord):string {
 const last=stage.attempts[stage.attempts.length-1];
 return last ? recordedStatus[last.recordedStatus || 'unknown'] || last.recordedStatus! : '尚无记录';
}
const stageTypes:Record<string,StageType>={analysis:'plan_idea',develop:'gen_kernel','device-test':'verify_correctness','prepare-environment':'read_info','establish-baseline':'summarize'};
const candidateStages:StageMeta[]=[
 {type:'plan_idea',index:1,shortLabel:'分析',label:'分析与方案依据',description:'形成此候选的方案，以及针对该候选的诊断；可被多个候选引用'},
 {type:'gen_kernel',index:2,shortLabel:'开发',label:'候选开发',description:'生成此候选身份的开发任务'},
 {type:'verify_correctness',index:3,shortLabel:'测试',label:'测试与复测',description:'绑定同一候选的测试任务；Run 交付完成不代表精度或性能通过'},
];
type Run=HarnessModel['runs'][number];
const cleanRef=(value:string)=>value.trim().replace(/^\[[^\]]*\]\(([^)]+)\)$/, '$1').replace(/^[`<]|[`>]$/g,'').replace(/^\.workspace\//,'').replace(/^\.\//,'');
const none=(value:string)=>/^(?:not_applicable|none|null|unknown|未记录)(?:\s|$)/i.test(value);
const execStatus=(value:string):ExecutionStatus=>['completed','running','failed'].includes(value)?value as ExecutionStatus:value==='interrupted'?'stopped':'pending';

/** Candidate identity defines graph nodes. Execution order never defines candidate lineage. */
export function harnessExploration(model:HarnessModel, contents:Record<string,string>):Exploration {
 const id=`harness:${model.projectId}:${model.snapshotId}`;
 const warnings=[...model.warnings];
 const warn=(kind:string,message:string,paths:string[])=>warnings.push({kind,message,paths});
 const stages=():Record<StageType,StageRecord>=>Object.fromEntries(STAGE_METAS.map(m=>[m.type,{stageType:m.type,status:'pending',attempts:[]}])) as unknown as Record<StageType,StageRecord>;
 const record=(path?:string)=>model.records.find(r=>r.path===path);
 const resolveRun=(value?:string)=>model.runs.find(r=>value&&(cleanRef(value)===r.id||cleanRef(value)===r.taskPath||cleanRef(value)===r.resultPath));
 // Only actual manifests create candidates; duplicate IDs are ambiguous, not silently merged.
 const manifests=model.records.filter(r=>r.kind==='candidate'&&r.fields.candidate_id);
 const candidates:HarnessModel['records']=[];
 for(const candidateId of new Set(manifests.map(r=>r.fields.candidate_id))){
  const copies=manifests.filter(r=>r.fields.candidate_id===candidateId);
  const originals=copies.filter(r=>/^runs\/[^/]+\/CANDIDATE\.md$/.test(r.path));
  const choices=originals.length?originals:copies;
  if(choices.length===1)candidates.push(choices[0]);
  else if(!originals.length&&new Set(choices.map(r=>r.sha256)).size===1)candidates.push(choices[0]);
  else warn('ambiguous_candidate','候选 ID 重复，未合并或绘制；请核对清单身份',choices.map(r=>r.path));
 }
 const resolveCandidate=(value:string)=>{
  const ref=cleanRef(value);
  const direct=candidates.find(c=>ref===c.fields.candidate_id||ref===c.path);
  if(direct)return direct;
  const copy=manifests.find(c=>c.path===ref);
  return copy?candidates.find(c=>c.fields.candidate_id===copy.fields.candidate_id&&c.sha256===copy.sha256):undefined;
 };
 const nodes:NodeRecord[]=candidates.map(c=>({id:c.fields.candidate_id,explorationId:id,parentId:null,depth:0,branchLabel:'候选',title:c.title==='候选清单' ? resolveRun(c.fields.producer_run)?.operation || c.fields.candidate_id : c.title,hypothesis:'见候选清单与分析原文',changeSummary:'以候选清单固定版本；关联任务完成不代表精度或性能通过。',derivationNote:c.fields.parent_candidate||'父候选未记录',agentIds:[],executionStatus:'pending',contentStatus:'hot',currentStage:'gen_kernel',correctnessPassed:false,stopOrFailReason:'精度与性能：见测试原文',stages:stages(),harness:{recordedStatus:'registered',kind:'candidate',manifestPath:c.path,runIds:[]},stageMetas:candidateStages.map(m=>({...m}))}));
 let workspaceId='@workspace';while(nodes.some(n=>n.id===workspaceId))workspaceId+='-records';
 const workspace:NodeRecord={id:workspaceId,explorationId:id,parentId:null,depth:0,branchLabel:'项目记录',title:'项目记录与未关联任务',hypothesis:'未归入候选树',changeSummary:'环境、标杆、未产出候选及缺少明确候选引用的任务保留在这里，不推断分支。',derivationNote:'',agentIds:[],executionStatus:'pending',contentStatus:'hot',currentStage:'read_info',correctnessPassed:false,stages:stages(),stageMetas:[],harness:{recordedStatus:'workspace',kind:'workspace',runIds:[]}};
 const exp:Exploration={id,title:model.projectName,operatorName:model.projectName,targetGoal:'以 REQUIREMENTS / STATE 原文为准',inputDomain:'',environment:'以 ENVIRONMENT 原文为准',baselineRef:{id:'',version:'',latencyUs:0,description:'未从文档推断测量'},budget:{usedEvaluations:0,reservedEvaluations:0,maxEvaluations:0,elapsedMinutes:0,maxMinutes:0,noGainWindow:0,minGainPct:0},coordinatorStatus:'stopped',bestValidNodeId:'',unfinishedDirections:[],nodes,agents:[],references:[],resources:{},resourceLinks:[],knowledgeItems:[],graphRelations:[],harness:{snapshotId:model.snapshotId,capturedAt:model.capturedAt,consistency:model.consistency,runCount:model.runs.length,warnings}};
 for(const c of candidates){
  const node=nodes.find(n=>n.id===c.fields.candidate_id)!;
  const raw=c.fields.parent_candidate;
  if(!raw||none(raw)){if(!raw)warn('missing_parent_candidate','父候选未记录，保持独立节点',[c.path]);continue;}
  const parent=resolveCandidate(raw);
  if(!parent){warn('missing_parent_candidate','父候选缺失或身份不明确，未推断连线',[c.path]);continue;}
  const parentId=parent.fields.candidate_id;
  const ancestors=new Set<string>();let cursor:string|undefined=parentId;
  while(cursor&&!ancestors.has(cursor)){ancestors.add(cursor);cursor=nodes.find(n=>n.id===cursor)?.parentIds?.[0];}
  if(ancestors.has(node.id)){warn('cyclic_parent_candidate','候选父关系存在环，未绘制此边',[c.path]);continue;}
  node.parentIds=[parentId];node.parentId=parentId;
  exp.graphRelations!.push({id:`candidate:${parentId}:${node.id}`,kind:'derivation',sourceType:'node',sourceId:parentId,targetType:'node',targetId:node.id,rationale:`候选清单 parent_candidate: ${raw}。派生关系不继承父候选的验证结果。`,resourceVersionId:c.path});
 }
 const associations=new Map<string,Set<string>>();
 const associate=(run:Run,nodeId:string)=>{const set=associations.get(run.id)||new Set<string>();set.add(nodeId);associations.set(run.id,set);};
 for(const c of candidates){
  const producer=resolveRun(c.fields.producer_run);
  if(!producer){warn('missing_producer_run','候选的生成任务未归档或未记录',[c.path]);continue;}
  associate(producer,c.fields.candidate_id);
  // Only an explicitly referenced analysis can serve as the producer's proposal.
  const analysis=resolveRun(producer.parentRun);
  if(analysis?.node==='analysis')associate(analysis,c.fields.candidate_id);
 }
 // Candidate inputs come from explicit fields or the TASK input table, never prose mentions.
 for(const run of model.runs){
  const task=record(run.taskPath);
  if(!task||run.node==='develop'||!['analysis','device-test'].includes(run.node))continue;
  const refs=[task.fields.candidate_id,task.fields.candidate,task.fields.candidate_path].filter(Boolean);
  const text=contents[task.path]||'';
  const inputSection=text.match(/^##\s+输入与读取顺序\s*\n([\s\S]*?)(?=^##\s|$(?![\s\S]))/m)?.[1]||'';
  for(const line of inputSection.split('\n')){
   if(!line.trim().startsWith('|'))continue;
   const cells=line.split('|').slice(1,-1).map(v=>v.trim());
   if(cells.length>=3)refs.push(cells[2]);
  }
  for(const ref of refs){const c=resolveCandidate(ref);if(c)associate(run,c.fields.candidate_id);}
 }
 const orderedRuns=[...model.runs].sort((a,b)=>(a.createdAt||'').localeCompare(b.createdAt||'')||a.id.localeCompare(b.id));
 for(const run of orderedRuns){
  const targetIds=associations.get(run.id);
  const targets=targetIds?nodes.filter(n=>targetIds.has(n.id)):[workspace];
  if(!targetIds&&['analysis','develop','device-test'].includes(run.node))warn('unlinked_run','任务缺少可确认的候选关联，保留在项目记录',[run.taskPath||run.id]);
  const fields=record(run.taskPath)?.fields||{},resultFields=record(run.resultPath)?.fields||{};
  const agent=fields.agent_id||fields.executor_id||resultFields.agent_id||'unrecorded-executor';
  const stage=stageTypes[run.node]||'profiling';
  for(const node of targets){
   if(!node.stageMetas!.some(m=>m.type===stage))node.stageMetas!.push({type:stage,index:node.stageMetas!.length+1,shortLabel:labels[run.node]||'其他',label:labels[run.node]||'其他任务',description:'项目级或未关联候选的原始任务'});
   const st=node.stages[stage];
   const parent=resolveRun(run.parentRun);
   st.attempts.push({id:run.id,code:run.id,nodeId:node.id,stageType:stage,round:st.attempts.length+1,agentId:agent,status:execStatus(run.status),recordedStatus:run.status,startedAt:run.createdAt||'时间未记录',dependsOnAttemptIds:parent?[parent.id]:[],inputResourceVersionIds:run.taskPath?[run.taskPath]:[],outputResourceVersionIds:run.resultPath?[run.resultPath]:[],summary:run.operation||'操作未记录'});
   st.status=execStatus(run.status);st.currentAttemptId=run.id;
   node.harness!.runIds!.push(run.id);if(!node.agentIds.includes(agent))node.agentIds.push(agent);
  }
 }
 nodes.push(workspace);
 for(const node of nodes.filter(n=>n.harness?.kind==='candidate')){
  const tests=node.stages.verify_correctness.attempts;
  node.stopOrFailReason=tests.length?`${tests.length} 次测试记录 · 精度与性能见原文`:'尚无关联测试记录';
  node.changeSummary=`${node.harness!.runIds!.length} 个关联 Run；测试记录 ${tests.length} 次。候选身份及派生依据见清单。`;
 }
 const category=(path:string):ResourceCategory=>/\.(py|mlir|cpp|cc|h)$/.test(path)?'kernel':/RESULT\.md$/.test(path)?'summary':/TASK\.md$/.test(path)?'idea':/\.(csv|json)$/.test(path)?'experiment':'artifact';
 for(const file of model.manifest.files){
  const rec=record(file.path);
  const ownRun=model.runs.find(r=>file.path.startsWith(`runs/${r.id}/`));
  const manifest=candidates.find(c=>c.path===file.path);
  const associated=ownRun?associations.get(ownRun.id):undefined;
  const targets=manifest?nodes.filter(n=>n.id===manifest.fields.candidate_id):ownRun?(associated?nodes.filter(n=>associated.has(n.id)):[workspace]):nodes;
  const owner=targets[0]?.id||workspace.id;
  exp.resources[file.path]={id:file.path,resourceId:file.path,version:model.snapshotId,title:rec?.title||file.path,filename:file.path,category:category(file.path),sha256:file.sha256||'',ownerNodeId:owner,contentStatus:'hot',createdAt:model.capturedAt,sizeBytes:file.sizeBytes,isBinary:!file.text,content:contents[file.path]||'',missingReason:file.status!=='copied'?`未复制：${file.reason||file.status}`:file.text&&contents[file.path]===undefined?'内容未能读取':!file.text?'二进制：仅显示归档元数据':undefined};
  for(const node of targets)exp.resourceLinks.push({id:`${node.id}:${file.path}`,nodeId:node.id,stageType:ownRun?(stageTypes[ownRun.node]||'profiling'):undefined,attemptId:ownRun?.id,resourceVersionId:file.path,role:/RESULT\.md$/.test(file.path)?'output':'input',sourceNodeId:owner,isSharedReference:targets.length>1,note:'workspace 快照内的精确文件'});
 }
 if(model.annotations.length){const path='@agent/observations.md';exp.resources[path]={id:path,resourceId:path,version:model.viewRevision||model.snapshotId,title:'Agent 观察（非验收判定）',filename:path,category:'summary',sha256:'',ownerNodeId:workspace.id,contentStatus:'hot',createdAt:model.capturedAt,sizeBytes:0,content:model.annotations.map(a=>`## ${a.title}\n\n${a.body}\n\n依据：${a.sources.map(s=>`[${s}](${s})`).join(' · ')}`).join('\n\n')};for(const node of nodes)exp.resourceLinks.push({id:`notes:${node.id}`,nodeId:node.id,resourceVersionId:path,role:'input',sourceNodeId:workspace.id,isSharedReference:true});}
 exp.agents=[...new Set(nodes.flatMap(n=>n.agentIds))].map(agent=>({id:agent,name:agent==='unrecorded-executor'?'未记录执行者':agent,role:'快照中的执行身份；非实时存活状态',status:'stopped',historicalOnly:true,lastHeartbeat:'未采集'}));
 if(!applyMeasurements(exp,contents,model))applySyntheticDemo(exp,contents);
 return exp;
}
