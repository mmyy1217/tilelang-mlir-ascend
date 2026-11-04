"""Create a synthetic branched candidate tree for UI acceptance. No hardware measurements."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('target')
args = parser.parse_args()
root = Path(args.target).resolve() / '.workspace'
if root.exists():
    raise SystemExit('Refusing to overwrite an existing workspace')
root.mkdir(parents=True)
files = {
    'PROJECT.md': '# 候选树验证样例\n\n所有记录为合成样例，不是真实算子实验。\n',
    'STATE.md': '# 当前任务\n\n合成样例：C1 分叉到 C2、C3，C2 再派生 C4。C2 包含两次测试任务。\n\n- 当前候选：C4\n- 最佳已验证候选：none\n\n所有候选均无真实编译、精度或性能测量；completed 仅表示样例任务交付。\n',
    'ENVIRONMENT.md': '# 环境索引\n\n- device: unverified\n\n未执行设备探针。\n',
    'REQUIREMENTS.md': '# 验收范围\n\n只验证候选派生、Run 归属和原文导航，不验证算子。\n',
}

def run(rid, node, operation, parent=None, candidate=None, status='completed'):
    inputs = ('\n## 输入与读取顺序\n\n| 顺序 | 用途 | 路径 | 模式 |\n| --- | --- | --- | --- |\n'
              f'| 1 | 候选实现 | `.workspace/{candidate}` | snapshot |\n') if candidate else ''
    files[f'runs/{rid}/TASK.md'] = (f'# {operation}\n\n- run_id: {rid}\n- node: {node}\n- operation: {operation}\n'
        f'- created_at: 2026-11-03T10:{len(files):02d}:00+08:00\n- parent_run: {".workspace/runs/" + parent + "/RESULT.md" if parent else "not_applicable"}\n'
        f'- agent_id: {"fixture-developer" if node=="develop" else "fixture-analyst" if node=="analysis" else "fixture-tester"}\n{inputs}')
    files[f'runs/{rid}/RESULT.md'] = f'# {operation}：样例交付\n\n- status: {status}\n\n合成记录；未执行硬件实验。编译、精度、性能均为 not_run。\n'

def candidate(cid, rid, title, parent=None):
    files[f'runs/{rid}/CANDIDATE.md'] = (f'# {title}\n\n- candidate_id: {cid}\n- producer_run: .workspace/runs/{rid}/TASK.md\n'
        f'- parent_candidate: {".workspace/runs/" + parent + "/CANDIDATE.md" if parent else "not_applicable"}\n\n'
        '此候选是界面验证样例，无真实 kernel 和实测结果。\n')

run('R0', 'prepare-environment', '项目级环境准备', status='blocked')
run('R1', 'analysis', '初始实现方案')
run('R2', 'develop', '生成初始实现', 'R1'); candidate('C1', 'R2', '初始实现')
run('R3', 'analysis', '分析 C1，提出分块与布局两个方向', 'R2', 'runs/R2/CANDIDATE.md')
run('R4', 'develop', '调整分块', 'R3'); candidate('C2', 'R4', '调整分块', 'R2')
run('R5', 'develop', '改变数据布局', 'R3'); candidate('C3', 'R5', '改变数据布局', 'R2')
run('R6', 'device-test', 'C2 首次测试任务', 'R4', 'runs/R4/CANDIDATE.md', 'blocked')
run('R7', 'device-test', 'C2 复测任务', 'R6', 'runs/R4/CANDIDATE.md', 'blocked')
run('R8', 'analysis', '在 C2 上提出双缓冲方案', 'R7', 'runs/R4/CANDIDATE.md')
run('R9', 'develop', '增加双缓冲', 'R8'); candidate('C4', 'R9', '增加双缓冲', 'R4')
for relative, content in files.items():
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
print(root.parent)
