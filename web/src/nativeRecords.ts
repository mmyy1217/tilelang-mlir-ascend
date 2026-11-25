import type {HarnessModel} from './harnessAdapter';

export function markdownSection(text:string, heading:string):string {
  const lines=text.split(/\r?\n/);
  const start=lines.findIndex(line=>line.trim()===`## ${heading}`);
  if(start<0)return '';
  const end=lines.findIndex((line,i)=>i>start&&/^#{1,2}\s/.test(line));
  return lines.slice(start+1,end<0?undefined:end).join('\n').trim();
}

export function nativeVerdicts(text:string) {
  const section=markdownSection(text,'结果与证据');
  const verdicts:string[][]=[];
  for(const line of section.split('\n')){
    if(!line.trim().startsWith('|'))continue;
    const cells=line.trim().slice(1,-1).split(/(?<!\\)\|/).map(s=>s.trim().replace(/^`|`$/g,''));
    if(cells.length!==3||!['编译','精度','性能目标','采集完整性'].includes(cells[0]))continue;
    if(!['passed','failed','not_run','invalid','not_applicable'].includes(cells[1]))continue;
    if(verdicts.some(v=>v[0]===cells[0]))return [];
    verdicts.push(cells);
  }
  return verdicts;
}

export function isNativeHarness(model:HarnessModel,contents:Record<string,string>):boolean {
  return Boolean(contents['search/tree.json']?.includes('operator-harness/search@')) ||
    model.records.some(r=>r.fields.contract?.startsWith('operator-harness/'));
}

export function projectRecordPath(path:string, projectId?:string):string {
  const prefix = projectId ? `.workspace/${projectId}/` : '';
  return prefix && path.startsWith(prefix) ? path.slice(prefix.length) : path.replace(/^\.workspace\//, '');
}

export function acceptedRun(treeText:string,runId:string,files:HarnessModel['manifest']['files']):string {
  try {
    const tree=JSON.parse(treeText);
    if(tree.schema!=='operator-harness/search@1')return '接收状态无法解释';
    const ref=tree.runs?.[runId]?.result;
    if(!ref)return '未登记接收';
    const path=projectRecordPath(String(ref.path||''),tree.project_id);
    return /^[a-f0-9]{64}$/.test(ref.sha256||'') && files.some(f=>f.path===path&&f.status==='copied'&&f.sha256===ref.sha256)
      ? '已登记接收（不等同于独立验证）' : '接收引用缺失或摘要不符';
  }catch{return '接收状态未提供';}
}
