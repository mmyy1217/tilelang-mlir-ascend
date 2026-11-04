const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const crypto=require('node:crypto');
const ts=require('typescript');
require.extensions['.ts']=(module,filename)=>module._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
const {harnessExploration}=require('../src/harnessAdapter.ts');
const {sampleStatistics}=require('../src/measurements.ts');
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
function fixture(){
 const files={},records=[],runs=[];
 const put=(path,data)=>files[path]=typeof data==='string'?data:JSON.stringify(data);
 const ref=path=>({path,sha256:hash(files[path])});
 const record=(path,kind,fields,runId=null)=>{put(path,JSON.stringify(fields));records.push({id:path,path,kind,fields,runId,title:path});};
 const run=(id,node,candidate)=>{const taskPath=`runs/${id}/TASK.md`;record(taskPath,'task',candidate?{candidate_id:candidate}:{},id);runs.push({id,node,status:'completed',taskPath,resultPath:`runs/${id}/RESULT.md`,records:[taskPath]});};
 run('B','establish-baseline');record('BASELINE.md','baseline',{baseline_id:'B0'});
 for(const id of ['C1','C2']){run('D'+id,'develop');record(`runs/D${id}/CANDIDATE.md`,'candidate',{candidate_id:id,producer_run:`runs/D${id}/TASK.md`,parent_candidate:id==='C1'?'not_applicable':'C1'},'D'+id);run('T'+id,'device-test',id);}
 run('T2repeat','device-test','C2');
 put('environment.md','environment@1');put('reference.md','reference@1');
 put('cases.json',{schema:'harness-vis/case-set@1',cases:[{id:'small',label:'small shape'},{id:'large',label:'large shape'}]});
 put('protocol.json',{schema:'harness-vis/timing-protocol@1',unit:'us',warmupRuns:2,repeatRuns:3,timingBoundary:'kernel-only',syncMethod:'device sync'});
 const paths=[];
 function report(id,runId,values,day,kind='candidate'){
  const path=`runs/${runId}/measurement.json`;
  put(path,{schema:'harness-vis/measurement@1',synthetic:true,subject:{kind,id,manifest:ref(kind==='baseline'?'BASELINE.md':`runs/D${id}/CANDIDATE.md`)},runId,recordedAt:`2026-01-${day}T10:00:00Z`,environment:ref('environment.md'),reference:ref('reference.md'),caseSet:ref('cases.json'),protocol:ref('protocol.json'),cases:values.map((value,i)=>({id:i?'large':'small',accuracy:'passed',samplesUs:[value*1.01,value,value*.99]}))});
  paths.push({path,subjectId:id,kind});return path;
 }
 const baseline=report('B0','B',[10,20],'01','baseline');report('C1','TC1',[8,16],'02');report('C2','TC2',[5,10],'03');report('C2','T2repeat',[4,8],'04');
 const refresh=()=>put('MEASUREMENTS.json',{schema:'harness-vis/measurements@1',baseline:ref(baseline),reports:paths.map(p=>({...p,...ref(p.path)}))});
 refresh();
 return {files,paths,ref,put,refresh,edit(path,change){const value=JSON.parse(files[path]);change(value);put(path,value);refresh();},build(){const model={projectId:'P',snapshotId:'S',projectName:'contract',capturedAt:'2026-01-05T00:00:00Z',consistency:'checked',records:records.map(r=>({...r,sha256:hash(files[r.path])})),runs,annotations:[],warnings:[],manifest:{files:Object.entries(files).map(([path,content])=>({path,status:'copied',text:true,sizeBytes:Buffer.byteLength(content),sha256:hash(content)}))}};return harnessExploration(model,files);}};
}
const candidate=(exp,id='C2')=>exp.nodes.find(n=>n.id===id);
const latest=(exp,id='C2')=>exp.harness.measurements.reports.find(r=>r.path===exp.harness.measurements.latest[id]);

test('multi-case reports use exact sample statistics and latest timestamp, never mean latency',()=>{
 assert.deepEqual(sampleStatistics([4,1,3,2]),{medianUs:2.5,p95Us:4});
 const f=fixture();f.paths.reverse();f.refresh();const e=f.build();
 assert.equal(e.bestValidNodeId,'C2');assert.equal(candidate(e).latencyUs,undefined);
 assert.equal(candidate(e).speedupVsBaseline,2.5);assert.equal(latest(e).runId,'T2repeat');
 assert.equal(latest(e).cases[1].medianUs,8);assert.equal(latest(e).cases[0].p95Us,4.04);
 assert.equal(e.harness.measurements.reports.length,4);
});
test('latest failed or incomplete retest cannot fall back to a previous winning report',()=>{
 for(const change of [r=>{r.cases[1].accuracy='failed';r.cases[1].samplesUs=[];},r=>{r.cases.pop();},r=>{r.cases[1].samplesUs=[];}]){
  const f=fixture();f.edit('runs/T2repeat/measurement.json',change);const e=f.build();
  assert.equal(latest(e).comparable,false);assert.equal(candidate(e).speedupVsBaseline,undefined);assert.equal(e.bestValidNodeId,'C1');
 }
});
test('protocol, environment, case-set, reference and provenance mismatches remain visible but unranked',()=>{
 for(const field of ['protocol','environment','caseSet','reference','synthetic']){
  const f=fixture();f.edit('runs/T2repeat/measurement.json',r=>{
   if(field==='synthetic'){r.synthetic=false;return;}
   const path=r[field].path,alternate='alternate-'+path;
   const value=f.files[path];
   if(field==='protocol'){const p=JSON.parse(value);p.timingBoundary='end-to-end';f.put(alternate,p);}
   else if(field==='caseSet'){const c=JSON.parse(value);c.cases[0].label='different input shape';f.put(alternate,c);}
   else f.put(alternate,value+' changed');
   r[field]=f.ref(alternate);
  });const e=f.build();assert.equal(latest(e).valid,true,field);assert.equal(latest(e).comparable,false,field);assert.equal(candidate(e).speedupVsBaseline,undefined,field);
 }
});
test('invalid identities, samples and timestamps block summaries instead of retaining old passes',()=>{
 const mutations=[r=>r.subject.manifest.sha256='0'.repeat(64),r=>r.subject.id='C1',r=>r.runId='TC1',r=>delete r.synthetic,r=>r.recordedAt='yesterday',r=>r.cases[0].samplesUs=[1,-2,3],r=>r.cases[0].samplesUs=[1,2],r=>r.cases[0].samplesUs=[1,null,3],r=>r.cases.push(r.cases[0]),r=>r.cases[0].id='unknown',r=>r.cases[0].accuracy='failed'];
 for(const mutation of mutations){const f=fixture();f.edit('runs/T2repeat/measurement.json',mutation);const e=f.build();assert.equal(candidate(e).correctnessPassed,false);assert.equal(candidate(e).speedupVsBaseline,undefined);assert.equal(latest(e),undefined);assert.ok(e.harness.warnings.some(w=>w.kind==='measurement_invalid'));}
});
test('changed candidate manifests and report digest tampering are rejected',()=>{
 const f=fixture();f.files['runs/DC2/CANDIDATE.md']+=' changed';const e=f.build();assert.equal(latest(e),undefined);
 const g=fixture();g.files['runs/T2repeat/measurement.json']+=' ';const altered=g.build();assert.equal(latest(altered),undefined);assert.match(altered.harness.warnings.find(w=>w.kind==='measurement_invalid').message,/摘要/);
});
test('missing baseline disables comparisons while retaining valid per-case latency',()=>{
 const f=fixture();const index=JSON.parse(f.files['MEASUREMENTS.json']);delete index.baseline;f.put('MEASUREMENTS.json',index);const e=f.build();
 assert.equal(candidate(e).correctnessPassed,true);assert.equal(latest(e).cases[0].medianUs,4);assert.equal(e.bestValidNodeId,'');
});
test('duplicate entries and tied retest timestamps do not choose an arbitrary winner',()=>{
 const f=fixture();f.paths.push(f.paths[3]);f.refresh();assert.equal(latest(f.build()),undefined);
 const g=fixture();g.edit('runs/T2repeat/measurement.json',r=>r.recordedAt='2026-01-03T10:00:00Z');assert.equal(latest(g.build()),undefined);
});
test('invalid formal index never falls back to synthetic demo metrics',()=>{
 const f=fixture();f.put('MEASUREMENTS.json','broken json');f.put('SYNTHETIC_DEMO.json',{schema:'harness-vis/synthetic-demo@1',synthetic:true,baselineUs:999,measurements:[]});const e=f.build();
 assert.ok(e.harness.measurements.errors.length);assert.equal(e.bestValidNodeId,'');assert.equal(candidate(e).speedupVsBaseline,undefined);
});
