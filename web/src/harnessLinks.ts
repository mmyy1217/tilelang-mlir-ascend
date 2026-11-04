/** Resolve references only against files that were actually copied into this snapshot. */
export function snapshotReference(href: string, current: string, files: Set<string>): string | undefined {
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//') || href.startsWith('/') || href.startsWith('#')) return undefined;
  let decoded: string;
  try { decoded = decodeURIComponent(href.split('#')[0].split('?')[0]); } catch { return undefined; }
  const normalize = (value: string) => {
    const parts: string[] = [];
    for (const part of value.split('/')) {
      if (!part || part === '.') continue;
      if (part === '..') { if (!parts.length) return undefined; parts.pop(); }
      else parts.push(part);
    }
    return parts.join('/');
  };
  const sibling = normalize(current.split('/').slice(0,-1).concat(decoded).join('/'));
  const root = normalize(decoded.replace(/^\.workspace\//, ''));
  if (sibling && files.has(sibling)) return sibling;
  if (root && files.has(root)) return root;
  return undefined;
}
