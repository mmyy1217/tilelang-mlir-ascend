const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
const {snapshotReference} = require('../src/harnessLinks.ts');
test('snapshot evidence links stay inside the captured file set', () => {
 const files = new Set(['STATE.md','runs/R1/RESULT.md','runs/R2/TASK.md']);
 assert.equal(snapshotReference('runs/R1/RESULT.md','STATE.md',files),'runs/R1/RESULT.md');
 assert.equal(snapshotReference('../R1/RESULT.md#result','runs/R2/TASK.md',files),'runs/R1/RESULT.md');
 assert.equal(snapshotReference('.workspace/runs/R1/RESULT.md','STATE.md',files),'runs/R1/RESULT.md');
 for(const href of ['../../secret','file:///etc/passwd','/etc/passwd','//evil/path','https://example.com','missing.md','%E0%A4%A']) assert.equal(snapshotReference(href,'STATE.md',files),undefined);
});
const {harnessExploration} = require('../src/harnessAdapter.ts');
const {graphDepths} = require('../src/graph.ts');
function model(){return {snapshotId:'S1',projectId:'P1',projectName:'Test',capturedAt:'2026-11-03T10:00:00Z',consistency:'checked',records:[{id:'STATE.md',path:'STATE.md',kind:'state',runId:null,title:'State',fields:{},sha256:'abc'}],runs:[{id:'R1',node:'analysis',operation:'分析',status:'completed',records:['runs/R1/TASK.md'],taskPath:'runs/R1/TASK.md'},{id:'R2',node:'device-test',operation:'测试',status:'blocked',parentRun:'.workspace/runs/R1/RESULT.md',records:['runs/R2/RESULT.md'],resultPath:'runs/R2/RESULT.md'}],annotations:[{title:'说明',body:'无实测',sources:['STATE.md']}],warnings:[],manifest:{files:[{path:'STATE.md',status:'copied',text:true,sizeBytes:10,sha256:'abc'},{path:'runs/R2/RESULT.md',status:'copied',text:true,sizeBytes:12,sha256:'def'}]}};}

function candidate(m,id,parent,run,title=id){
 const path=`runs/${run}/CANDIDATE.md`;
 m.records.push({id:path,path,kind:'candidate',runId:run,title,fields:{candidate_id:id,producer_run:`.workspace/runs/${run}/TASK.md`,parent_candidate:parent},sha256:id});
 m.manifest.files.push({path,status:'copied',text:true,sizeBytes:10,sha256:id});
}
function run(m,id,node,parentRun,candidateId,status='completed'){
 const taskPath=`runs/${id}/TASK.md`,resultPath=`runs/${id}/RESULT.md`;
 m.runs.push({id,node,operation:`${node} ${id}`,status,parentRun,taskPath,resultPath,createdAt:`2026-11-03T10:${String(m.runs.length).padStart(2,'0')}:00Z`,records:[taskPath,resultPath]});
 m.records.push({id:taskPath,path:taskPath,kind:'task',runId:id,title:id,fields:candidateId?{candidate_id:candidateId}:{},sha256:id});
 for(const path of [taskPath,resultPath])m.manifest.files.push({path,status:'copied',text:true,sizeBytes:10,sha256:id});
}
function branching(){
 const m=model();m.runs=[];
 run(m,'A1','analysis');run(m,'D1','develop','runs/A1/RESULT.md');candidate(m,'C1','not_applicable','D1');
 run(m,'A2','analysis',undefined,'C1');
 run(m,'D2','develop','runs/A2/RESULT.md');candidate(m,'C2','.workspace/runs/D1/CANDIDATE.md','D2');
 run(m,'D3','develop','runs/A2/RESULT.md');candidate(m,'C3','C1','D3');
 run(m,'T2','device-test','runs/D3/RESULT.md','C2','blocked');
 run(m,'T3','device-test','runs/T2/RESULT.md','C2');
 run(m,'E1','prepare-environment');
 return m;
}
test('candidate tree branches by candidate ancestry; retests do not create nodes or inherit results',()=>{
 const m=branching(),e=harnessExploration(m,{});
 assert.deepEqual(e.nodes.filter(n=>n.harness.kind==='candidate').map(n=>n.id),['C1','C2','C3']);
 assert.deepEqual(e.graphRelations.map(r=>[r.sourceId,r.targetId]),[['C1','C2'],['C1','C3']]);
 assert.equal(graphDepths(e).get('C3'),1);
 const c2=e.nodes.find(n=>n.id==='C2');
 assert.deepEqual(c2.stages.verify_correctness.attempts.map(a=>a.id),['T2','T3']);
 assert.equal(c2.stages.verify_correctness.attempts[0].recordedStatus,'blocked');
 assert.equal(c2.stageMetas.length,3);
 assert.equal(e.nodes.find(n=>n.id==='C3').stages.verify_correctness.attempts.length,0);
 assert.equal(e.bestValidNodeId,'');assert.ok(e.nodes.every(n=>!n.correctnessPassed&&n.latencyUs===undefined));
 assert.equal(e.agents[0].name,'未记录执行者');
 assert.match(e.resources['@agent/observations.md'].content,/\[STATE.md\]\(STATE.md\)/);
 assert.equal(m.warnings.length,0);
});
test('proposal analysis binds to the child candidates it produces, while project Runs stay outside the tree',()=>{
 const m=branching();run(m,'Adiag','analysis',undefined,'C1');
 const e=harnessExploration(m,{});
 assert.deepEqual(e.nodes.find(n=>n.id==='C1').stages.plan_idea.attempts.map(a=>a.id),['A1','Adiag']);
 for(const id of ['C2','C3'])assert.ok(e.nodes.find(n=>n.id===id).stages.plan_idea.attempts.some(a=>a.id==='A2'));
 const project=e.nodes.find(n=>n.harness.kind==='workspace');
 assert.deepEqual(project.harness.runIds,['E1']);
 assert.equal(e.graphRelations.some(r=>r.sourceId===project.id||r.targetId===project.id),false);
 for(const id of ['C2','C3'])assert.ok(e.resourceLinks.some(l=>l.nodeId===id&&l.resourceVersionId==='runs/A2/RESULT.md'));
});
test('TASK input table binds a test to a candidate; prose and parent_run cannot invent membership',()=>{
 const m=branching();run(m,'T4','device-test','runs/D2/RESULT.md');run(m,'T5','device-test','runs/D2/RESULT.md');
 const e=harnessExploration(m,{'runs/T4/TASK.md':'## 输入与读取顺序\n\n| 顺序 | 用途 | 路径 | 模式 |\n| 1 | 候选 | `.workspace/runs/D2/CANDIDATE.md` | snapshot |\n\n## 执行方式\n其他内容','runs/T5/TASK.md':'## 目标\n比较 C2 与 C3；历史 runs/D2/CANDIDATE.md。'});
 assert.ok(e.nodes.find(n=>n.id==='C2').harness.runIds.includes('T4'));
 assert.ok(e.nodes.find(n=>n.harness.kind==='workspace').harness.runIds.includes('T5'));
});
test('missing, duplicate and cyclic candidate ancestry is explicit and cannot crash the tree',()=>{
 const m=branching();m.records.find(r=>r.fields.candidate_id==='C1'&&r.kind==='candidate').fields.parent_candidate='C2';
 const e=harnessExploration(m,{});assert.doesNotThrow(()=>graphDepths(e));assert.ok(e.harness.warnings.some(w=>w.kind==='cyclic_parent_candidate'));
 const missing=branching();missing.records.find(r=>r.kind==='candidate'&&r.fields.candidate_id==='C2').fields.parent_candidate='outside/CANDIDATE.md';
 const me=harnessExploration(missing,{});assert.ok(me.harness.warnings.some(w=>w.kind==='missing_parent_candidate'));assert.equal(me.nodes.find(n=>n.id==='C2').parentId,null);
 candidate(missing,'C3','C1','D9');const dupe=harnessExploration(missing,{});assert.ok(dupe.harness.warnings.some(w=>w.kind==='ambiguous_candidate'));assert.equal(dupe.nodes.some(n=>n.id==='C3'),false);
});
test('pre-candidate Runs and documents remain inspectable without fake solution nodes',()=>{
 const m=model(),e=harnessExploration(m,{'STATE.md':'state'});
 assert.equal(e.nodes.length,1);assert.equal(e.nodes[0].harness.kind,'workspace');assert.equal(e.graphRelations.length,0);
 assert.equal(e.nodes[0].harness.runIds.length,2);assert.ok(e.resourceLinks.some(l=>l.resourceVersionId==='STATE.md'));
 m.runs=[];const empty=harnessExploration(m,{});assert.equal(empty.nodes[0].stageMetas.length,0);assert.equal(empty.agents.length,0);
});

test('frozen copies of a candidate bind by identical digest without creating duplicate nodes',()=>{
 const m=branching();const original=m.records.find(r=>r.kind==='candidate'&&r.fields.candidate_id==='C2');
 const path='runs/T4/inputs/CANDIDATE.md';m.records.push({...original,id:path,path,runId:'T4'});
 run(m,'T4','device-test',undefined,path);
 const e=harnessExploration(m,{});
 assert.equal(e.nodes.filter(n=>n.id==='C2').length,1);
 assert.ok(e.nodes.find(n=>n.id==='C2').harness.runIds.includes('T4'));
 m.records.find(r=>r.path===path).sha256='changed';const changed=harnessExploration(m,{});
 assert.ok(changed.nodes.find(n=>n.harness.kind==='workspace').harness.runIds.includes('T4'));
});

test('synthetic measurements require explicit opt-in, preserve failed branches and compute ranking from samples',()=>{
 const m=branching();run(m,'T5','device-test',undefined,'C3');
 const config='SYNTHETIC_DEMO.json',good='runs/T3/synthetic-measurement.json',bad='runs/T5/synthetic-measurement.json';
 for(const path of [config,good,bad])m.manifest.files.push({path,status:'copied',text:true,sizeBytes:10,sha256:'demo'});
 const contents={
  [config]:JSON.stringify({schema:'harness-vis/synthetic-demo@1',synthetic:true,baselineUs:200,measurements:[good,bad]}),
  [good]:JSON.stringify({schema:'harness-vis/synthetic-measurement@1',synthetic:true,candidateId:'C2',runId:'T3',correctnessPassed:true,samplesUs:[102,98,100]}),
  [bad]:JSON.stringify({schema:'harness-vis/synthetic-measurement@1',synthetic:true,candidateId:'C3',runId:'T5',correctnessPassed:false,samplesUs:[]})
 };
 const e=harnessExploration(m,contents);assert.equal(e.harness.synthetic,true);assert.equal(e.bestValidNodeId,'C2');
 assert.equal(e.nodes.find(n=>n.id==='C2').latencyUs,100);assert.equal(e.nodes.find(n=>n.id==='C2').speedupVsBaseline,2);
 assert.equal(e.nodes.find(n=>n.id==='C3').latencyUs,undefined);
 const unmarked={...contents,[config]:JSON.stringify({schema:'harness-vis/synthetic-demo@1',synthetic:false,baselineUs:200,measurements:[good]})};
 assert.equal(harnessExploration(m,unmarked).bestValidNodeId,'');
 const wrongRun={...contents,[good]:JSON.stringify({...JSON.parse(contents[good]),runId:'T99'})};
 assert.equal(harnessExploration(m,wrongRun).nodes.find(n=>n.id==='C2').latencyUs,undefined);
});

test('search tree, hot knowledge revisions, cooled knowledge inputs and promotions parse with relational links',()=>{
 const m=branching();
 const treePath='search/tree.json';
 const hotPath1='knowledge/hot/H1/rev1.json';
 const hotPath2='knowledge/hot/H1/rev2.json';
 const evPath='knowledge/evidence/ev1.snapshot';
 const cooledPath1='knowledge/cooled/K0--v1.json';
 const cooledPath2='knowledge/cooled/H1--review-1.json';
 for(const path of [treePath,hotPath1,hotPath2,evPath,cooledPath1,cooledPath2]){
  m.manifest.files.push({path,status:'copied',text:true,sizeBytes:20,sha256:'a'.repeat(64)});
 }
 const contents={
  [treePath]:JSON.stringify({
   schema:'operator-harness/search@1',
   operator:'matmul',
   task_contract:'req-v1',
   current_node:'N1',
   nodes:{
    N0:{parent:null,hypothesis:'初始分块',scope:'建立可比初版',status:'closed'},
    N1:{parent:'N0',hypothesis:'双缓冲与复用',scope:'L1/L0 流水',status:'open'},
   },
   candidates:{
    C1:{search_node_id:'N0',parent_candidate:null,manifest:{path:'.workspace/runs/D1/CANDIDATE.md',sha256:'C1'}},
    C2:{search_node_id:'N1',parent_candidate:'C1',manifest:{path:'.workspace/runs/D2/CANDIDATE.md',sha256:'C2'}},
   },
   runs:{
    A1:{search_node_id:'N0',stage:'analysis',candidate_id:null},
    D1:{search_node_id:'N0',stage:'develop',candidate_id:'C1'},
    A2:{search_node_id:'N1',stage:'analysis',candidate_id:'C1'},
    D2:{search_node_id:'N1',stage:'develop',candidate_id:'C2'},
    T2:{search_node_id:'N1',stage:'device-test',candidate_id:'C2'},
   },
   hot:{
    H1:[{path:`.workspace/${hotPath1}`,sha256:'a'.repeat(64)},{path:`.workspace/${hotPath2}`,sha256:'a'.repeat(64)}],
   },
   cooled_inputs:[
    {search_node_id:'N1',artifact:{path:cooledPath1,sha256:'a'.repeat(64)},reason:'遵循基线分块约束'},
   ],
   events:[
    {at:'2026-11-03T10:00:00Z',operation:{event_id:'ev-1',op:'bootstrap',root_id:'N0',operator:'matmul'}},
   ],
  }),
  [evPath]:'maxAbsDiff=0.00051, latency=192.4us',
  [hotPath1]:JSON.stringify({id:'H1',revision:1,kind:'observation',statement:'初版搬运等待占比 50%',applicability:'M=2048',limitations:'仅单核',nodes:['N0'],evidence:[{path:`.workspace/${evPath}`,sha256:'a'.repeat(64)}],evidence_sources:['.workspace/runs/A1/RESULT.md']}),
  [hotPath2]:JSON.stringify({id:'H1',revision:2,producer_node_id:'N1',kind:'conclusion',statement:'增大 L1 复用并结合双缓冲可隐藏 MTE2 等待',applicability:'M>=1024',limitations:'需 L1 容量充足',nodes:['N0','N1'],evidence:[{path:`.workspace/${evPath}`,sha256:'a'.repeat(64)}],evidence_sources:['.workspace/runs/T2/RESULT.md']}),
  [cooledPath1]:JSON.stringify({schema:'operator-harness/cooled@1',id:'K0',version:'v1',status:'needs_revalidation',kind:'conclusion',statement:'Ascend 910B Cube 基本分块对齐约束',applicability:'FP16 Matmul',limitations:'不含动态尾块',evidence_summary:'基线验证',reason:'CANN 升级待复核'}),
  [cooledPath2]:JSON.stringify({schema:'operator-harness/cooled@1',id:'H1',version:'review-1',status:'active',kind:'conclusion',statement:'增大 L1 复用并结合双缓冲可隐藏 MTE2 等待',applicability:'M>=1024',limitations:'需 L1 容量充足',evidence_summary:'T2 实测确认',provenance:{operator:'matmul',task_contract:'req-v1',hot_id:'H1',revision:2,hot_sha256:'a'.repeat(64),nodes:['N0','N1']},review:{id:'review-1',reviewer:'arch-lead',reviewed_at:'2026-11-03T11:00:00Z',delivery:'.workspace/decisions/review-1.md'}}),
 };
 const e=harnessExploration(m,contents);
 const s=e.harness.search;
 assert.ok(s);
 assert.equal(s.synthetic,undefined);
 assert.equal(s.currentNodeId,'N1');
 assert.equal(s.searchNodes.length,2);
 const n0=s.searchNodes.find(n=>n.id==='N0'),n1=s.searchNodes.find(n=>n.id==='N1');
 assert.deepEqual(n0.childrenIds,['N1']);
 assert.equal(n1.depth,1);
 assert.deepEqual(n1.usedCooledIds,['K0--v1']);
 assert.deepEqual(n0.hotKnowledgeIds,[]);
 assert.deepEqual(n1.hotKnowledgeIds,['H1']);
 assert.equal(s.hotKnowledge.length,1);
 assert.equal(s.hotKnowledge[0].revisions.length,2);
 assert.equal(s.hotKnowledge[0].latest.revision,2);
 assert.equal(s.hotKnowledge[0].promotedCooledId,'H1--review-1');
 assert.equal(s.hotKnowledge[0].latest.evidence[0].content,'maxAbsDiff=0.00051, latency=192.4us');
 assert.equal(s.cooledKnowledge.length,2);
 assert.ok(e.harness.warnings.some(w=>w.kind==='cooled_knowledge_status'));
});



test('index-only runs remain unknown even when the index claims completion',()=>{
 const m=model();m.runs=[];
 const e=harnessExploration(m,{'search/tree.json':JSON.stringify({schema:'operator-harness/search@1',nodes:{N0:{}},runs:{R9:{search_node_id:'N0',stage:'device-test',status:'completed'}}})});
 assert.equal(e.harness.search.searchNodes[0].runs[0].status,'unknown');
 assert.ok(e.harness.warnings.some(w=>w.kind==='unarchived_search_run'));
});

test('cooled references must match the captured version before creating links',()=>{
 const path='knowledge/cooled/K1.json';
 for(const digest of ['a'.repeat(64),'b'.repeat(64),'']){
  const m=model();m.manifest.files.push({path,status:'copied',text:true,sha256:'a'.repeat(64)});
  const e=harnessExploration(m,{
   'search/tree.json':JSON.stringify({schema:'operator-harness/search@1',nodes:{N0:{}},cooled_inputs:[{search_node_id:'N0',artifact:{path,sha256:digest}}]}),
   [path]:JSON.stringify({schema:'operator-harness/cooled@1',id:'K1',version:'v1',status:'active'})
  });
  const valid=digest==='a'.repeat(64);
  assert.equal(e.harness.search.searchNodes[0].usedCooledIds.length,valid?1:0);
  assert.equal(e.harness.search.cooledKnowledge[0].usedByNodes.length,valid?1:0);
  assert.equal(e.harness.warnings.some(w=>w.kind==='cooled_input_digest_mismatch'),!valid);
  assert.ok(e.resources[path]);
 }
});
