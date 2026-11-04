"""Create a simulated multi-case dataset using the same contract as device-produced reports."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('target')
root=Path(parser.parse_args().target).resolve()
subprocess.run([sys.executable,str(Path(__file__).with_name('create_measurement_demo.py')),str(root)],check=True)
workspace=root/'.workspace'
def write(path,data):
 target=workspace/path;target.parent.mkdir(parents=True,exist_ok=True)
 target.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n' if isinstance(data,dict) else data)
def ref(path):return {'path':path,'sha256':hashlib.sha256((workspace/path).read_bytes()).hexdigest()}
def samples(target,key):
 rng=random.Random(key);values=[rng.gauss(0,target*.006) for _ in range(101)];centre=statistics.median(values)
 return [round(target+value-centre,4) for value in values]
case_definitions=[{'id':'small','label':'M256 × N4096 × K2048 · FP16'},{'id':'main','label':'M2048 × N4096 × K2048 · FP16'},{'id':'tail','label':'M2051 × N4093 × K2047 · FP16'}]
write('measurement-inputs/cases.json',{'schema':'harness-vis/case-set@1','cases':case_definitions})
protocol={'schema':'harness-vis/timing-protocol@1','unit':'us','warmupRuns':20,'repeatRuns':101,'timingBoundary':'kernel-only','syncMethod':'synchronize before and after each sample'}
write('measurement-inputs/timing.json',protocol)
write('measurement-inputs/end-to-end.json',{**protocol,'timingBoundary':'end-to-end including transfer'})
write('measurement-inputs/reference.md','# 模拟参考身份\n\nFP32 参考；atol=1e-3，rtol=1e-3。全部输入和输出均为演示构造，未执行设备。\n')
entries=[]
def make_report(subject_id,kind,run_id,manifest,medians,passed=True,timing='measurement-inputs/timing.json',recorded_at=None):
 task=(workspace/f'runs/{run_id}/TASK.md').read_text()
 timestamp=next(line.split(': ',1)[1] for line in task.splitlines() if line.startswith('- created_at:'))
 timestamp=recorded_at or (datetime.datetime.fromisoformat(timestamp)+datetime.timedelta(minutes=2)).isoformat()
 results=[]
 for i,case in enumerate(case_definitions):
  accuracy='passed' if passed or i<2 else 'failed'
  results.append({'id':case['id'],'accuracy':accuracy,'maxAbsDiff':.00059 if accuracy=='passed' else .0372,
   'samplesUs':samples(medians[i],f'{subject_id}:{run_id}:{case["id"]}') if passed else []})
 report={'schema':'harness-vis/measurement@1','synthetic':True,'subject':{'kind':kind,'id':subject_id,'manifest':ref(manifest)},'runId':run_id,'recordedAt':timestamp,'environment':ref('ENVIRONMENT.md'),'reference':ref('measurement-inputs/reference.md'),'caseSet':ref('measurement-inputs/cases.json'),'protocol':ref(timing),'cases':results}
 path=f'runs/{run_id}/measurement.json';write(path,report)
 entries.append({**ref(path),'kind':kind,'subjectId':subject_id})
 return ref(path)
baseline=make_report('DEMO-B0','baseline','B0','BASELINE.md',[54.692,248.6,263.516])
for cid,value in [('C1',248.6),('C2',192.4),('C3',200),('C4',143.8),('C5',206.2),('C6',132.4)]:
 number=cid[1:]
 make_report(cid,'candidate',f'T{number}',f'runs/D{number}/CANDIDATE.md',[value*.22,value,value*1.06],cid!='C3',timing='measurement-inputs/end-to-end.json' if cid=='C5' else 'measurement-inputs/timing.json')
make_report('C6','candidate','T6-repeat','runs/D6/CANDIDATE.md',[29.194,132.7,140.662])
write('MEASUREMENTS.json',{'schema':'harness-vis/measurements@1','baseline':baseline,'reports':entries})
write('PROJECT.md','# Matmul · 逐 case 测量接入演示\n\n所有报告均 synthetic=true，仅用于离线验证统一测量契约。C3 尾部精度失败；C5 计时边界不同，不能和 kernel-only 基线排名。C6 保留两次测量历史。无需 Agent 解读。\n')
write('STATE.md','# 当前进展 · 模拟\n\n六个候选、三个必选 case。C6 是当前可比组的模拟最佳；C3 尾部精度失败，C5 采用不同计时边界而不可比。旧的单 case 演示 JSON 仍可查看，但存在 MEASUREMENTS.json 时只使用统一契约汇总。\n')
print('Generated unified measurement index:', workspace/'MEASUREMENTS.json')
