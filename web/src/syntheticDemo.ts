import {Exploration} from './types';
/** Explicit opt-in demo projection. Never reads ordinary measurement files as synthetic results. */
export function applySyntheticDemo(exp:Exploration, contents:Record<string,string>){
 if(!contents['SYNTHETIC_DEMO.json'])return;
 let config:any;
 try{config=JSON.parse(contents['SYNTHETIC_DEMO.json']);}catch{return;}
 if(config.schema!=='harness-vis/synthetic-demo@1'||config.synthetic!==true||!Array.isArray(config.measurements)||!Number.isFinite(config.baselineUs)||config.baselineUs<=0)return;
 exp.harness!.synthetic=true;
 exp.title=typeof config.title==='string'?config.title:'模拟调优用例';
 exp.operatorName=exp.title;
 exp.environment=String(config.environment||'模拟设备环境');exp.inputDomain=String(config.inputShape||'模拟输入');
 exp.baselineRef={id:'DEMO-B0',version:'synthetic',latencyUs:config.baselineUs,description:'人工生成的模拟基线，未经设备测量'};
 for(const path of config.measurements){
  if(typeof path!=='string'||!exp.resources[path]||!contents[path])continue;
  try{
   const data=JSON.parse(contents[path]);
   const node=exp.nodes.find(n=>n.id===data.candidateId&&n.harness?.kind==='candidate');
   if(data.schema!=='harness-vis/synthetic-measurement@1'||data.synthetic!==true||!node||!node.harness||!node.stages.verify_correctness.attempts.some(a=>a.id===data.runId)||typeof data.correctnessPassed!=='boolean')continue;
   const samples=data.samplesUs;
   if(!Array.isArray(samples)||!samples.every((v:unknown)=>typeof v==='number'&&Number.isFinite(v)&&v>0)||data.correctnessPassed&&samples.length<3)continue;
   const sorted=[...samples].sort((a,b)=>a-b),count=sorted.length;
   const median=count?(sorted[Math.floor((count-1)/2)]+sorted[Math.floor(count/2)])/2:undefined;
   node.harness.synthetic=true;
   node.correctnessPassed=data.correctnessPassed;
   node.latencyUs=data.correctnessPassed?median:undefined;
   node.speedupVsBaseline=node.latencyUs?config.baselineUs/node.latencyUs:undefined;
   node.executionStatus=data.correctnessPassed?'completed':'failed';
   node.harness.recordedStatus=data.correctnessPassed?'模拟精度通过':'模拟精度失败';
   node.stopOrFailReason=data.correctnessPassed?'模拟测量':String(data.reason||'模拟精度失败，不参与排名');
   node.changeSummary=String(data.summary||node.changeSummary);
   const profile=data.profile;
   if(profile&&['cubePct','vecPct','mte2Pct'].every(k=>typeof profile[k]==='number'&&profile[k]>=0&&profile[k]<=100))node.pipelineMetrics={...profile,bottleneck:`模拟流水画像：${profile.bottleneck||''}`};
   const resource=exp.resources[path];
   resource.title='模拟测量 · 原始样本与协议';resource.category='experiment';
   resource.experimentData={hardware:exp.environment,cannVersion:'模拟配置',referenceImpl:'模拟 FP32 参考',inputShape:exp.inputDomain,dtype:'FP16 / FP32 accumulate',atol:'1e-3',rtol:'1e-3',maxAbsDiff:String(data.maxAbsDiff),correctnessPassed:data.correctnessPassed,warmupRuns:20,repeatRuns:count,timingBoundary:'模拟 kernel-only，排除编译与传输',syncMethod:'模拟同步边界；未执行设备调用',medianUs:node.latencyUs,p95Us:count?sorted[Math.ceil(count*.95)-1]:undefined,minUs:sorted[0],maxUs:sorted[count-1],samplesUs:samples,isComparable:data.correctnessPassed,incomparableReason:data.correctnessPassed?undefined:'模拟精度失败'};
   node.primaryKernelVersionId=path;
  }catch{exp.harness!.warnings.push({kind:'invalid_synthetic_measurement',message:'模拟测量文件不可解析',paths:[path]});}
 }
 const ranked=exp.nodes.filter(n=>n.harness?.synthetic&&n.correctnessPassed&&n.latencyUs).sort((a,b)=>a.latencyUs!-b.latencyUs!);
 exp.bestValidNodeId=ranked[0]?.id||'';
}
