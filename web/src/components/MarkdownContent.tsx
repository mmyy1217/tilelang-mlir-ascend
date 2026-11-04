import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** Render evidence as text/Markdown; never execute embedded HTML or load remote images. */
export function MarkdownContent({content, localLinks}: {content: string; localLinks?: {resolve: (href: string) => string | undefined; open: (path: string) => void}}) {
  return <div className="markdown-content"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
    components={{
      a: ({href, children}) => {
        if (!href) return <span>{children}</span>;
        if (localLinks && !/^https?:\/\//i.test(href)) {
          const path = localLinks.resolve(href);
          return path ? <button className="hv-evidence-link" onClick={() => localLinks.open(path)}>{children}</button>
            : <span title="此引用未包含在快照中">{children}（未归档引用）</span>;
        }
        return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
      },
      img: ({alt}) => <span className="markdown-image-note">[图片：{alt || '未提供说明'}]</span>,
      table: ({children}) => <div className="markdown-table-scroll"><table>{children}</table></div>,
    }}>{content}</ReactMarkdown></div>;
}
