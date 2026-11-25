const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),ts=require('typescript');
require.extensions['.ts']=(m,f)=>m._compile(ts.transpileModule(fs.readFileSync(f,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,f);
const {harnessExploration}=require('../src/harnessAdapter.ts');
const {nativeVerdicts,acceptedRun}=require('../src/nativeRecords.ts');
const fixture=JSON.parse(fs.readFileSync(__dirname+'/fixtures/harness-native-v1.json','utf8'));
test('native searchctl outputs bind runs, accepted results and original hot evidence',()=>{
 const e=harnessExploration(fixture.model,fixture.contents);
 assert.equal(e.harness.search.searchNodes.length,1);
 assert.equal(e.harness.search.searchNodes[0].runs[0].status,'completed');
 assert.equal(e.harness.search.searchNodes[0].loopCount,0);
 assert.equal(e.harness.search.hotKnowledge[0].latest.evidence[0].content,fixture.contents['runs/R1/RESULT.md']);
 assert.equal(e.bestValidNodeId,'');
 assert.match(acceptedRun(fixture.contents['search/tree.json'],'R1',fixture.model.manifest.files),/已登记接收/);
 const verdicts=nativeVerdicts(fixture.contents['runs/R1/RESULT.md']);
 assert.equal(verdicts.find(v=>v[0]==='精度')[1],'not_run');
});
test('missing or future search schema never invents nodes from candidates',()=>{
 for(const tree of ['',JSON.stringify({schema:'operator-harness/search@2',nodes:{N0:{}}})]){
 const e=harnessExploration(fixture.model,{...fixture.contents,'search/tree.json':tree});
 assert.equal(e.harness.search.searchNodes.length,0);
 }
});
test('native records do not acquire rankings from legacy vis-only formats',()=>{
 const e=harnessExploration(fixture.model,{...fixture.contents,'MEASUREMENTS.json':'{}'});
 assert.equal(e.bestValidNodeId,'');assert.equal(e.harness.measurements,undefined);
 assert.ok(e.harness.warnings.some(w=>w.kind==='legacy_measurements'));
});
test('tampered accepted result loses acceptance, not silently substituted',()=>{
 const files=fixture.model.manifest.files.map(f=>f.path==='runs/R1/RESULT.md'?{...f,sha256:'0'.repeat(64)}:f);
 assert.match(acceptedRun(fixture.contents['search/tree.json'],'R1',files),/不符/);
});
test('cross-branch arrows come from verified search plans and omit tree edges', () => {
  const tree = JSON.parse(fixture.contents['search/tree.json']);
  const path = 'plans/merge.json';
  const plan = JSON.stringify({schema:'operator-harness/search-plan@1',project_id:'example',prior_nodes:['N0','N1','N1']});
  const sha256 = require('node:crypto').createHash('sha256').update(plan).digest('hex');
  tree.project_id = 'example';
  tree.nodes.N1 = {parent:'N0'};
  tree.nodes.N2 = {parent:'N0',plan:{path,sha256}};
  const contents = {...fixture.contents,[path]:plan,'search/tree.json':JSON.stringify(tree)};
  const file = {path,status:'copied',text:true,sha256};
  const model = {...fixture.model,manifest:{...fixture.model.manifest,files:[...fixture.model.manifest.files,file]}};
  assert.deepEqual(harnessExploration(model,contents).harness.search.crossLinks,[{source:'N1',target:'N2'}]);
  file.sha256 = '0'.repeat(64);
  assert.deepEqual(harnessExploration(model,contents).harness.search.crossLinks,[]);
});
