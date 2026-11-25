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
write('PROJECT.md','# Matmul · 分块与流水优化（Search Tree & Knowledge）\n\n所有报告均 synthetic=true，用于展示 Search Tree（N0..N6）、Hot/Cooled Knowledge 演进以及统一多 case 测量契约。C3 尾部精度失败；C5 计时边界不同不可比；C6 保留两次复测历史并取得 1.87× 最佳加速。\n')
write('STATE.md','# 当前进展 · 模拟\n\n当前搜索焦点：N6（消除双缓冲循环尾部冗余同步并独立剥离尾块流水），最佳候选 C6（主算例 132.7 μs · 1.87×）。\n\n- N0（初始分块，C1）已完成并收口。\n- N1（数据布局与分块复用）派生出 N3（离线预重排，C3 尾部精度失败后挂起 parked，并衍生 N5 在线 ND2NZ 尾块掩码方案）与 N4（两轮迭代 C2 1.29× 有效、C5 缓冲加深退化，沉淀热知识 H2 并经 review-1 冷却为 H2--review-1）。\n- N2（MTE2/Cube 双缓冲重叠，C4 1.73×）进一步派生出当前节点 N6（C6 1.87×），沉淀热知识 H3。\n')
write('decisions/review-1.md','# 评审记录 review-1 · L1 Tile 驻留与双缓冲预算结论冷却\n\n- review_id: review-1\n- reviewer: arch-review-board\n- reviewed_at: 2026-01-15T11:40:00+08:00\n- hot_id: H2\n- revision: 2\n\n## 评审意见\n\n经核查 N1、N4（C2 与 C5 对比实验）及 N2（C4 双缓冲实验）证据快照，确认在 Ascend 910B 上放大 L1 Tile 必须限制在双缓冲半区容量预算之内，超出会触发 L1/UB 挤占。批准冷却为跨任务可复用知识 `H2--review-1`。\n')

def write_root(rel_path, data):
 target = root / rel_path
 target.parent.mkdir(parents=True, exist_ok=True)
 text = json.dumps(data, ensure_ascii=False, indent=2) + '\n' if isinstance(data, dict) else data
 target.write_text(text)
 return {'path': rel_path, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}

def snapshot_evidence(ws_rel_path):
 raw_bytes = (workspace / ws_rel_path).read_bytes()
 digest = hashlib.sha256(raw_bytes).hexdigest()
 snap_rel = f'knowledge/evidence/{digest}.snapshot'
 target = workspace / snap_rel
 target.parent.mkdir(parents=True, exist_ok=True)
 target.write_bytes(raw_bytes)
 return {'path': f'.workspace/{snap_rel}', 'sha256': digest}, f'.workspace/{ws_rel_path}'

def write_hot_revision(hid, revision, kind, statement, applicability, limitations, nodes, source_paths):
 ev_list = []
 src_list = []
 for sp in source_paths:
  ev_ref, src_ref = snapshot_evidence(sp)
  ev_list.append(ev_ref)
  src_list.append(src_ref)
 payload = {
  'id': hid,
  'revision': revision,
  'kind': kind,
  'statement': statement,
  'applicability': applicability,
  'limitations': limitations,
  'nodes': nodes,
  'evidence': ev_list,
  'evidence_sources': src_list,
 }
 raw = (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
 digest = hashlib.sha256(raw).hexdigest()
 rel = f'knowledge/hot/{hid}/{digest}.json'
 target = workspace / rel
 target.parent.mkdir(parents=True, exist_ok=True)
 target.write_bytes(raw)
 return {'path': f'.workspace/{rel}', 'sha256': digest}

h1_rev1 = write_hot_revision(
 'H1', 1, 'observation',
 '手工重排 K 维分块步长而未对 M2051×N4093×K2047 尾块施加边界掩码时，L0C 累加出现 maxAbsDiff=0.0372 的尾部越界污染。',
 '非 16 对齐尾块（如 M2051 × N4093 × K2047）的 FP16 Matmul 静态偏移分块改写',
 '仅在静态偏移改写路径复现；硬件随路 load_nd2nz 边界填充行为留待 N5 验证',
 ['N3', 'N5'],
 ['runs/T3/RESULT.md', 'runs/T3/measurement.json']
)
h2_rev1 = write_hot_revision(
 'H2', 1, 'hypothesis',
 '放大 M/N L1 Tile 尺寸可将 MTE2 搬运等待占比从 50% 降至 37%，主算例中位耗时从 248.6 μs 降至 192.4 μs（1.29×）。',
 'Ascend 910B FP16 Matmul，M>=1024 且 L1 驻留充足场景',
 '尚未测试加深缓冲深度后的 L1/UB 挤占边界',
 ['N1', 'N4'],
 ['runs/T2/RESULT.md', 'runs/D2/CANDIDATE.md']
)
h2_rev2 = write_hot_revision(
 'H2', 2, 'conclusion',
 'L1 Tile 放大须控制在双缓冲半区预算内（C2 192.4 μs · 1.29× 有效）；盲目加深缓冲（C5）会触发资源等待回退至 206.2 μs 且破坏计时边界可比性。',
 'Ascend 910B FP16 Matmul 单核 L1 分块与多缓冲容量规划',
 '针对静态分块内核；动态 shape 分块需结合 tiling key 分档配置',
 ['N1', 'N4', 'N2'],
 ['runs/T2/RESULT.md', 'runs/T5/RESULT.md', 'runs/D4/CANDIDATE.md']
)
h3_rev1 = write_hot_revision(
 'H3', 1, 'conclusion',
 '在双缓冲 MTE2/Cube 重叠（C4 143.8 μs）基础上剥离尾块冗余同步屏障（C6），可将主算例耗时稳定压至 132.7 μs（1.87×），Cube 利用率达 76%。',
 'Ascend 910B FP16 输入 / FP32 累加，启用 L1/L0 ping-pong 双缓冲的 Matmul 内核',
 '小算例（M=256）受核启动固定开销影响，加速比（1.87×）仍由主算例与尾块算例主导',
 ['N2', 'N6'],
 ['runs/T6-repeat/RESULT.md', 'runs/T6-repeat/measurement.json', 'runs/D6/CANDIDATE.md']
)

cooled_k0 = write_root('knowledge/cooled/K0--910b-l1-alignment.json', {
 'schema': 'operator-harness/cooled@1',
 'id': 'K0',
 'version': '910b-l1-alignment',
 'status': 'active',
 'kind': 'conclusion',
 'statement': 'Ascend 910B Cube L1→L0A/L0B 搬运须满足 16×16 分形对齐与 512B 起始地址对齐，单核双缓冲总驻留上限为 512 KB。',
 'applicability': 'Ascend 910B Cube FP16/BF16 矩阵乘算子',
 'limitations': '不适用于纯 Vector 算子或无 L1 复用的直通路径',
 'evidence_summary': '架构手册约束与 DEMO-B0 基线核查确认。',
 'provenance': {
  'operator': 'matmul',
  'task_contract': 'matmul-fp16-v1',
  'hot_id': 'K0-init',
  'revision': 1,
  'hot_sha256': '0' * 64,
  'nodes': ['N0'],
 },
 'review': {
  'id': 'review-0',
  'reviewer': 'arch-review-board',
  'reviewed_at': '2026-01-15T09:10:00+08:00',
  'delivery': '.workspace/BASELINE.md',
 },
})

cooled_k1 = write_root('knowledge/cooled/K1--nd2nz-tail-guard.json', {
 'schema': 'operator-harness/cooled@1',
 'id': 'K1',
 'version': 'nd2nz-tail-guard',
 'status': 'needs_revalidation',
 'kind': 'observation',
 'statement': 'load_nd2nz 在非 16 倍数 K 维尾块搬运时需显式配置随路 pad 掩码或清零 L1 尾行缓冲，避免脏数据进入 L0A/L0B。',
 'applicability': '含非对齐尾块（如 M2051×N4093×K2047）的 ND→NZ 随路转换内核',
 'limitations': '新版本编译器随路谓词生成规则已更新，需在 N5 实测复核',
 'evidence_summary': '历史算子库尾块经验条目，关联 N3 尾部越界诊断。',
 'reason': '编译器 load_nd2nz 尾块谓词降级行为更新，待 N5 上板复核后重新激活',
})

cooled_h2 = write_root('knowledge/cooled/H2--review-1.json', {
 'schema': 'operator-harness/cooled@1',
 'id': 'H2',
 'version': 'review-1',
 'status': 'active',
 'kind': 'conclusion',
 'statement': 'L1 Tile 放大须控制在双缓冲半区预算内（C2 192.4 μs · 1.29× 有效）；盲目加深缓冲（C5）会触发资源等待回退至 206.2 μs 且破坏计时边界可比性。',
 'applicability': 'Ascend 910B FP16 Matmul 单核 L1 分块与多缓冲容量规划',
 'limitations': '针对静态分块内核；动态 shape 分块需结合 tiling key 分档配置',
 'evidence_summary': '基于 N1、N4（C2 vs C5）与 N2（C4）实测证据快照，经 review-1 评审通过。',
 'provenance': {
  'operator': 'matmul',
  'task_contract': 'matmul-fp16-v1',
  'hot_id': 'H2',
  'revision': 2,
  'hot_sha256': h2_rev2['sha256'],
  'nodes': ['N1', 'N4', 'N2'],
 },
 'review': {
  'id': 'review-1',
  'reviewer': 'arch-review-board',
  'reviewed_at': '2026-01-15T11:40:00+08:00',
  'delivery': '.workspace/decisions/review-1.md',
 },
})

def ws_ref(rel):
 d = hashlib.sha256((workspace / rel).read_bytes()).hexdigest()
 return {'path': f'.workspace/{rel}', 'sha256': d}

candidate_nodes = {'1': 'N0', '2': 'N4', '3': 'N3', '4': 'N2', '5': 'N4', '6': 'N6'}
candidate_parents = {'1': None, '2': 'C1', '3': 'C1', '4': 'C2', '5': 'C2', '6': 'C4'}

tree_candidates = {}
tree_runs = {}
for cid_num, snode in candidate_nodes.items():
 cid = f'C{cid_num}'
 tree_candidates[cid] = {
  'search_node_id': snode,
  'parent_candidate': candidate_parents[cid_num],
  'manifest': ws_ref(f'runs/D{cid_num}/CANDIDATE.md'),
 }
 for stage_prefix, stage_name, cand_ref in [('A', 'analysis', candidate_parents[cid_num]), ('D', 'develop', cid), ('T', 'device-test', cid)]:
  rid = f'{stage_prefix}{cid_num}'
  tree_runs[rid] = {
   'search_node_id': snode,
   'stage': stage_name,
   'candidate_id': cand_ref,
   'task': ws_ref(f'runs/{rid}/TASK.md'),
   'result': ws_ref(f'runs/{rid}/RESULT.md'),
  }
tree_runs['T6-repeat'] = {
 'search_node_id': 'N6',
 'stage': 'device-test',
 'candidate_id': 'C6',
 'task': ws_ref('runs/T6-repeat/TASK.md'),
 'result': ws_ref('runs/T6-repeat/RESULT.md'),
}

search_tree = {
 'schema': 'operator-harness/search@1',
 'operator': 'matmul',
 'task_contract': 'matmul-fp16-v1',
 'current_node': 'N6',
 'nodes': {
  'N0': {
   'parent': None,
   'hypothesis': '建立初始分块实现与可比基准点',
   'scope': 'M/N/K 基础分块与 GM→L1→L0A/L0B→L0C 标准搬算链路',
   'status': 'closed',
  },
  'N1': {
   'parent': 'N0',
   'hypothesis': '通过数据布局重排与 L1 分块复用降低 MTE2 搬运等待占比',
   'scope': '分叉探索离线布局重排（N3）与 L1 Tile 驻留放大（N4）两条子路径',
   'status': 'closed',
  },
  'N3': {
   'parent': 'N1',
   'hypothesis': '离线预重排输入分块步长以消除非连续搬运',
   'scope': '重排 K 维内部分块偏移（C3 因非对齐尾块 M2051×N4093×K2047 精度越界挂起）',
   'status': 'parked',
  },
  'N5': {
   'parent': 'N3',
   'hypothesis': '在 load_nd2nz 搬运阶段结合随路尾部掩码修复非对齐尾块精度',
   'scope': '保留 N3 重排思路，引入 K1 尾块保护规范（待下一轮开发验证）',
   'status': 'open',
  },
  'N4': {
   'parent': 'N1',
   'hypothesis': '放大 M/N L1 Tile 尺寸提升 A/B 分块复用率并探索缓冲深度上限',
   'scope': '执行 2 轮 A→D→T 迭代：C2（192.4 μs · 1.29×）验证有效，C5 验证加深缓冲导致资源挤占退化',
   'status': 'closed',
  },
  'N2': {
   'parent': 'N0',
   'hypothesis': '计算与搬运双缓冲流水化重叠（MTE2 / Cube 异步协同）',
   'scope': '在 C2 分块基础上引入 L1/L0 ping-pong 双缓冲，C4 将主算例压至 143.8 μs（1.73×）',
   'status': 'closed',
  },
  'N6': {
   'parent': 'N2',
   'hypothesis': '消除双缓冲主循环尾部冗余同步并独立剥离尾块流水',
   'scope': '压缩 PIPE_MTE2→PIPE_M 同步粒度，C6 经两次复测稳定达到 132.7 μs（1.87× 当前最佳）',
   'status': 'open',
  },
 },
 'candidates': tree_candidates,
 'runs': tree_runs,
 'hot': {
  'H1': [h1_rev1],
  'H2': [h2_rev1, h2_rev2],
  'H3': [h3_rev1],
 },
 'cooled_inputs': [
  {
   'search_node_id': 'N1',
   'artifact': cooled_k0,
   'reason': '遵循 910B L1/Cube 16×16 分形对齐与 512B 地址对齐约束设计分块',
  },
  {
   'search_node_id': 'N2',
   'artifact': cooled_k0,
   'reason': '双缓冲 ping-pong 切分下继续满足单核 L1 512 KB 驻留上限',
  },
  {
   'search_node_id': 'N5',
   'artifact': cooled_k1,
   'reason': '引入 load_nd2nz 非对齐尾块掩码与清零规范以修复 N3 尾部越界问题',
  },
  {
   'search_node_id': 'N6',
   'artifact': cooled_h2,
   'reason': '复用经 review-1 冷却确认的 L1 Tile 半区容量预算结论，专注压缩尾部同步开销',
  },
 ],
 'events': [
  {'at': '2026-01-15T09:15:00+08:00', 'operation': {'event_id': 'ev-01', 'op': 'bootstrap', 'root_id': 'N0', 'operator': 'matmul', 'task_contract': 'matmul-fp16-v1'}},
  {'at': '2026-01-15T09:35:00+08:00', 'operation': {'event_id': 'ev-02', 'op': 'register-candidate', 'id': 'C1', 'node': 'N0', 'parent_candidate': None}},
  {'at': '2026-01-15T09:42:00+08:00', 'operation': {'event_id': 'ev-03', 'op': 'add-node', 'id': 'N1', 'parent': 'N0', 'hypothesis': '通过数据布局重排与 L1 分块复用降低 MTE2 搬运等待占比'}},
  {'at': '2026-01-15T09:43:00+08:00', 'operation': {'event_id': 'ev-04', 'op': 'use-cooled', 'node': 'N1', 'path': cooled_k0['path'], 'reason': '遵循 910B L1/Cube 对齐约束'}},
  {'at': '2026-01-15T09:45:00+08:00', 'operation': {'event_id': 'ev-05', 'op': 'add-node', 'id': 'N3', 'parent': 'N1', 'hypothesis': '离线预重排输入分块步长以消除非连续搬运'}},
  {'at': '2026-01-15T10:05:00+08:00', 'operation': {'event_id': 'ev-06', 'op': 'node-status', 'id': 'N3', 'status': 'parked'}},
  {'at': '2026-01-15T10:06:00+08:00', 'operation': {'event_id': 'ev-07', 'op': 'put-hot', 'id': 'H1', 'kind': 'observation', 'nodes': ['N3', 'N5']}},
  {'at': '2026-01-15T10:08:00+08:00', 'operation': {'event_id': 'ev-08', 'op': 'add-node', 'id': 'N5', 'parent': 'N3', 'hypothesis': '在 load_nd2nz 搬运阶段结合随路尾部掩码修复非对齐尾块精度'}},
  {'at': '2026-01-15T10:09:00+08:00', 'operation': {'event_id': 'ev-09', 'op': 'use-cooled', 'node': 'N5', 'path': cooled_k1['path'], 'reason': '引入 load_nd2nz 尾块保护规范'}},
  {'at': '2026-01-15T10:12:00+08:00', 'operation': {'event_id': 'ev-10', 'op': 'add-node', 'id': 'N4', 'parent': 'N1', 'hypothesis': '放大 M/N L1 Tile 尺寸提升 A/B 分块复用率并探索缓冲深度上限'}},
  {'at': '2026-01-15T11:10:00+08:00', 'operation': {'event_id': 'ev-11', 'op': 'put-hot', 'id': 'H2', 'kind': 'conclusion', 'nodes': ['N1', 'N4', 'N2']}},
  {'at': '2026-01-15T11:40:00+08:00', 'operation': {'event_id': 'ev-12', 'op': 'promote-cooled', 'hot_id': 'H2', 'revision': 2, 'review_id': 'review-1'}},
  {'at': '2026-01-15T11:45:00+08:00', 'operation': {'event_id': 'ev-13', 'op': 'add-node', 'id': 'N2', 'parent': 'N0', 'hypothesis': '计算与搬运双缓冲流水化重叠（MTE2 / Cube 异步协同）'}},
  {'at': '2026-01-15T12:15:00+08:00', 'operation': {'event_id': 'ev-14', 'op': 'add-node', 'id': 'N6', 'parent': 'N2', 'hypothesis': '消除双缓冲主循环尾部冗余同步并独立剥离尾块流水'}},
  {'at': '2026-01-15T12:16:00+08:00', 'operation': {'event_id': 'ev-15', 'op': 'use-cooled', 'node': 'N6', 'path': cooled_h2['path'], 'reason': '复用经 review-1 冷却确认的 L1 Tile 容量预算结论'}},
  {'at': '2026-01-15T12:40:00+08:00', 'operation': {'event_id': 'ev-16', 'op': 'put-hot', 'id': 'H3', 'kind': 'conclusion', 'nodes': ['N2', 'N6']}},
  {'at': '2026-01-15T12:42:00+08:00', 'operation': {'event_id': 'ev-17', 'op': 'select-node', 'id': 'N6'}},
 ],
}
write('search/tree.json', search_tree)
print('Generated unified measurement index and search tree:', workspace/'search/tree.json')
