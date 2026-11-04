"""Generate a labelled, deterministic synthetic Matmul optimization story. No device calls."""
import argparse
import datetime
import json
import random
import statistics
from pathlib import Path

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('target')
root=Path(parser.parse_args().target).resolve()
workspace=root/'.workspace'
if workspace.exists():raise SystemExit('Refusing to overwrite an existing workspace')
workspace.mkdir(parents=True)
files={}
clock=datetime.datetime(2026,1,15,9,0,tzinfo=datetime.timezone(datetime.timedelta(hours=8)))
serial=0

def put(path,text):files[path]=text

def run(rid,node,title,parent=None,candidate=None,agent='analyst',body=''):
 global clock,serial
 serial+=1;clock+=datetime.timedelta(minutes=7)
 inputs='' if not candidate else f'\n## 输入与读取顺序\n\n| 顺序 | 用途 | 路径 | 模式 |\n| --- | --- | --- | --- |\n| 1 | 候选 | `.workspace/runs/D{candidate}/CANDIDATE.md` | snapshot |\n'
 put(f'runs/{rid}/TASK.md',f'# {title}\n\n模拟任务，未执行设备调用。\n\n- run_id: {rid}\n- node: {node}\n- operation: {title}\n- created_at: {clock.isoformat()}\n- agent_id: {agent}\n- parent_run: {".workspace/runs/"+parent+"/RESULT.md" if parent else "not_applicable"}\n{inputs}')
 put(f'runs/{rid}/RESULT.md',f'# {title}\n\n> 模拟结果：全部数值人工生成，未经设备验证。\n\n- status: completed\n\n{body}\n')

put('PROJECT.md','# Matmul · 分块与流水优化\n\n> 演示项目：全部测量、误差、流水画像及任务记录均人工生成，无设备实验。\n\n展示一棵候选探索树：有效优化、失败分支、退化路线与复测记录。\n')
put('REQUIREMENTS.md','# 模拟任务契约\n\n- operation: C = A × B\n- shape: M=2048, N=4096, K=2048\n- dtype: FP16 输入输出 / FP32 累加\n- target: 模拟中位耗时低于 150 μs\n\n## 模拟协议\n\nFP32 参考；atol=1e-3、rtol=1e-3。预热 20 次，采样 101 次，kernel-only，排除编译与传输。协议仅用于展示，没有执行任何调用。\n')
put('ENVIRONMENT.md','# 演示环境\n\n- target: Ascend 910B（模拟目标）\n- device: unavailable\n\n没有连接设备。所有计时、精度与周期占比均为合成数据，不能用于硬件性能判断。\n')
put('BASELINE.md','# 模拟基线 B0\n\n- baseline_id: DEMO-B0\n- synthetic: true\n- median_us: 248.6\n\n此数值为人工构造的比较起点，不代表任何库的真实性能。\n')
run('P0','prepare-environment','登记离线演示环境',agent='coordinator',body='设备不可用，仅创建模拟用例。')
run('B0','establish-baseline','固定模拟基线与协议','P0',agent='coordinator',body='模拟基线 248.6 μs；所有候选使用同一输入与协议。')
rows=[
 ('1',None,'初始分块实现',248.6,True,0.00048,(42,8,50),'搬运等待较多','直接分块，先建立可比较的初始实现。'),
 ('2','1','增大 Tile · 复用数据',192.4,True,0.00051,(55,8,37),'数据复用改善','调整 M/N 分块，减少重复搬运；模拟耗时降低 22.6%。'),
 ('3','1','布局重排 · 尾部失败',None,False,0.0372,(48,13,39),'尾部掩码错误','布局改写遗漏尾部掩码，模拟误差超限；停止性能验收。'),
 ('4','2','双缓冲 · 搬算重叠',143.8,True,0.00056,(69,8,23),'同步开销仍可压缩','双缓冲重叠搬运与计算；模拟耗时低于目标 150 μs。'),
 ('5','2','加深缓冲 · 性能退化',206.2,True,0.00053,(48,9,43),'资源压力与等待增加','缓冲深度增加，但模拟资源压力抵消收益；不继续此分支。'),
 ('6','4','尾部与同步优化',132.4,True,0.00059,(76,7,17),'接近当前模拟上限','减少冗余同步，单独处理尾部；两次模拟复测保持一致。'),
]
measurements=[]
for cid,parent,title,median,passed,error,profile,bottleneck,summary in rows:
 analysis=f'A{cid}';develop=f'D{cid}';test=f'T{cid}'
 run(analysis,'analysis',f'C{cid} 方案：{title}',f'T{parent}' if parent else 'B0',parent,body=summary+'\n\n这是用于讲解分支选择的模拟分析。')
 run(develop,'develop',title,analysis,parent,agent='kernel-dev',body=f'生成模拟候选 C{cid}。配置记录保留在候选清单；没有编译或设备执行。')
 put(f'runs/{develop}/CANDIDATE.md',f'# {title}\n\n- candidate_id: C{cid}\n- producer_run: .workspace/runs/{develop}/TASK.md\n- parent_candidate: {".workspace/runs/D"+parent+"/CANDIDATE.md" if parent else "not_applicable"}\n- synthetic: true\n\n## 修改与假设\n\n{summary}\n\n## 来源\n\n此清单用于 UI 演示，不包含已编译的真实 kernel。\n')
 for suffix,target in ([('',median),('-repeat',132.7)] if cid=='6' else [('',median)]):
  tid=test+suffix
  # Reproducible synthetic jitter, centred on the requested median; keep acquisition order.
  rng=random.Random(f'C{cid}:{tid}')
  jitter=[rng.gauss(0,target*.006) for _ in range(101)] if target else []
  centre=statistics.median(jitter) if jitter else 0
  samples=[round(target+v-centre,4) for v in jitter]
  data={'schema':'harness-vis/synthetic-measurement@1','synthetic':True,'candidateId':f'C{cid}','runId':tid,'correctnessPassed':passed,'maxAbsDiff':error,'samplesUs':samples,'reason':'模拟精度失败：尾部掩码错误，未进行性能验收' if not passed else '', 'summary':summary,'profile':dict(zip(['cubePct','vecPct','mte2Pct'],profile))|{'bottleneck':bottleneck},'protocol':{'warmup':20,'repeat':len(samples),'timing':'synthetic kernel-only','atol':1e-3,'rtol':1e-3}}
  path=f'runs/{tid}/synthetic-measurement.json';measurements.append(path)
  put(path,json.dumps(data,ensure_ascii=False,indent=2)+'\n')
  body=f'## 模拟判定\n\n- 精度：{"模拟通过" if passed else "模拟失败"}\n- 最大绝对误差：{error}\n- 中位耗时：{str(target)+" μs（模拟）" if target else "not_run（精度失败）"}\n\n{summary}\n\n[模拟原始样本与协议](synthetic-measurement.json)'
  run(tid,'device-test',f'C{cid} {"复测" if suffix else "精度与性能"}（模拟）',test if suffix else develop,cid,agent='device-eval',body=body)
put('STATE.md','# 当前进展 · 模拟\n\n当前候选 C6；模拟最佳 C6。C3 因模拟精度失败排除；C5 相比 C2 退化。C6 首次 132.4 μs，复测 132.7 μs，最终展示最近一次复测。所有数字人工生成，未经上板验证。\n\n下一方向：进一步减少尾部开销；尚未实现，不创建候选节点。\n')
put('SYNTHETIC_DEMO.json',json.dumps({'schema':'harness-vis/synthetic-demo@1','synthetic':True,'title':'Matmul · 分块与流水优化','environment':'Ascend 910B · 模拟环境','inputShape':'M=2048 · N=4096 · K=2048','baselineUs':248.6,'measurements':measurements},ensure_ascii=False,indent=2)+'\n')
for path,text in files.items():
 target=workspace/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
(root/'demo-notes.json').write_text(json.dumps([
 {'title':'模拟调优结论','body':'这是一套人工生成的演示记录，未经设备验证。有效路线为 **C1 → C2 → C4 → C6**；C6 最近一次模拟复测为 **132.7 μs**，相对模拟基线约 **1.87×**。','sources':['STATE.md','runs/T6-repeat/synthetic-measurement.json']},
 {'title':'两条值得保留的失败经验','body':'C3 展示布局重排中的尾部掩码错误，精度失败后不生成性能排名；C5 展示缓冲加深引起的模拟性能退化。失败分支保留在树上，解释为什么选择另一条路线。','sources':['runs/T3/RESULT.md','runs/T5/RESULT.md']},
],ensure_ascii=False,indent=2))
print(root)
