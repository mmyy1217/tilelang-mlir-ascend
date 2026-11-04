import React, { useEffect, useRef } from 'react';
import { Exploration } from '../types';

export function TaskPanels({ exploration }: {exploration: Exploration}) {
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) container.current?.querySelectorAll('details[open]').forEach(d => d.removeAttribute('open'));
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') container.current?.querySelectorAll<HTMLDetailsElement>('details[open]').forEach(d => { d.open = false; d.querySelector('summary')?.focus(); });
    };
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', escape); };
  }, []);
  const contracts = Object.values(exploration.resources).filter(r => exploration.harness ? /(?:REQUIREMENTS|STATE|PROJECT)\.md$/.test(r.filename) : /\.(ya?ml|json)$/i.test(r.filename) && /契约|contract|task.?spec/i.test(`${r.title} ${r.filename}`));
  const shapes = exploration.inputShapes?.length ? exploration.inputShapes : exploration.inputDomain ? [exploration.inputDomain] : [];
  const measuredShapes = [...new Set(Object.values(exploration.resources).flatMap(r => r.experimentData?.inputShape ? [r.experimentData.inputShape] : []))];
  // JSON-quoted strings are valid YAML scalars, including embedded newlines.
  const summaryYaml = [
    `operator: ${JSON.stringify(exploration.operatorName)}`,
    `goal: ${JSON.stringify(exploration.targetGoal || '')}`,
    `input_domain: ${JSON.stringify(exploration.inputDomain || '')}`,
    `environment: ${JSON.stringify(exploration.environment || '')}`,
    `baseline: ${JSON.stringify(exploration.baselineRef.id || '')}`,
    '# 待配置；不从实验结果反推任务要求',
    'verification:',
    '  reference: null',
    '  atol: null',
    '  rtol: null',
    'benchmark_protocol:',
    '  warmup: null',
    '  repeat: null',
    '  timing_boundary: null',
    '  sync_method: null',
  ].join('\n');
  const environment = exploration.harness ? Object.values(exploration.resources).filter(r=>/(?:ENVIRONMENT\.md|environments\/)/.test(r.filename)).map(r=>r.content || r.missingReason || r.filename) : exploration.environment.split(/\s*[·\n]\s*/).filter(Boolean);
  return <div className="task-panels" ref={container} key={exploration.id} onClickCapture={event => {
    const summary = (event.target as HTMLElement).closest('summary');
    if (summary) container.current?.querySelectorAll('details[open]').forEach(d => { if (d !== summary.parentElement) d.removeAttribute('open'); });
  }}>
    <details className="task-details">
      <summary>契约</summary>
      <div className="task-details-panel contract-panel">
        <div className="task-panel-heading"><strong>任务契约</strong><span>{exploration.harness ? '原始记录' : 'YAML'}</span></div>
        <p className="task-goal">优化目标：{exploration.targetGoal || '尚未配置'}</p>
        <p className="task-panel-note">精度要求：参考实现、atol / rtol；测量协议：预热、重复次数、计时边界和同步方式。以契约原文为准，未填写不视为已约定。</p>
        {contracts.length ? contracts.map(resource => <section key={resource.id}>
          <div className="contract-file">{resource.filename} · {resource.version}</div>
          {resource.missingReason ? <p>{resource.missingReason}</p> : <pre className="contract-yaml"><code>{resource.content || '# 契约文件内容为空'}</code></pre>}
        </section>) : <><p className="task-panel-note">尚未关联契约文件；以下为现有任务字段摘要，不代表完整契约。</p><pre className="contract-yaml"><code>{summaryYaml}</code></pre></>}
        {exploration.forkSource && <p className="task-panel-note">Fork 来源：{exploration.forkSource.title} · 水位 #{exploration.forkSource.eventSeq}<br/>{exploration.forkSource.capturedAt}</p>}
      </div>
    </details>
    {!exploration.harness && <details className="task-details">
      <summary>输入 Shape{shapes.length > 1 ? ` · ${shapes.length}` : ''}</summary>
      <div className="task-details-panel shape-panel">
        <div className="task-panel-heading"><strong>输入 Shape</strong><span>{shapes.length} 组</span></div>
        {shapes.length ? <ol className="shape-list">{shapes.map((shape, index) => <li key={index}><span>{index + 1}</span><code>{shape}</code></li>)}</ol> : <p>尚未配置输入 Shape。</p>}
        {measuredShapes.length > 0 && <><p className="task-panel-note">实验记录中的 Shape（不代表覆盖全部输入域）</p><ul className="measured-shapes">{measuredShapes.map(shape => <li key={shape}><code>{shape}</code></li>)}</ul></>}
      </div>
    </details>}
    <details className="task-details">
      <summary>环境</summary>
      <div className="task-details-panel environment-panel">
        <div className="task-panel-heading"><strong>运行环境</strong></div>
        {environment.length ? <ul className="environment-list">{environment.map((value, index) => <li key={index}>{value}</li>)}</ul> : <p>尚未配置运行环境。</p>}
      </div>
    </details>
    {exploration.harness && <details className="task-details"><summary>采集范围{exploration.harness.warnings.length ? ` · ${exploration.harness.warnings.length} 项提示` : ''}</summary><div className="task-details-panel"><strong>Workspace 快照</strong><p className="task-panel-note">手动采集，索引每 5 秒更新；不等于实时设备状态。外部引用未归档。采集前后校验不等于文件系统原子快照。</p>{exploration.harness.warnings.map((w,i)=><p key={i}>{w.message}：{w.paths.join(', ')}</p>)}</div></details>}
  </div>;
}
