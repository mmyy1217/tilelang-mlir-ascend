/** Compact histogram of the generated samples, with explicit units and provenance. */
export function SyntheticDistribution({samples}:{samples:number[]}) {
 if(!samples.length)return null;
 const min=Math.min(...samples),max=Math.max(...samples),span=max-min||1;
 const bins=Array.from({length:12},()=>0);
 for(const value of samples)bins[Math.min(11,Math.floor((value-min)/span*12))]++;
 const peak=Math.max(...bins);
 return <figure className="synthetic-distribution"><figcaption>模拟耗时分布 · {samples.length} 个样本</figcaption><svg viewBox="0 0 360 125" role="img" aria-label={`模拟样本直方图，范围 ${min.toFixed(1)} 至 ${max.toFixed(1)} 微秒；柱高表示样本数`}><text x="12" y="13" fontSize="10" fill="currentColor">样本数 · 峰值 {peak}</text>{bins.map((count,i)=><rect key={i} x={12+i*28} y={91-count/peak*65} width="24" height={count/peak*65} fill="var(--state-jade)"/>)}<path d="M12 92H348" stroke="currentColor"/><text x="12" y="113" fontSize="11" fill="currentColor">{min.toFixed(1)} μs</text><text x="348" y="113" textAnchor="end" fontSize="11" fill="currentColor">{max.toFixed(1)} μs</text></svg></figure>;
}
