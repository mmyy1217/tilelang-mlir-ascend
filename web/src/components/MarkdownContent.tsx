import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/** Render evidence as text/Markdown; never execute embedded HTML or load remote images. */
export function MarkdownContent({content}: {content: string}) {
  return <div className="markdown-content"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
    components={{
      a: ({href, children}) => href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>,
      img: ({alt}) => <span className="markdown-image-note">[图片：{alt || '未提供说明'}]</span>,
      table: ({children}) => <div className="markdown-table-scroll"><table>{children}</table></div>,
    }}>{content}</ReactMarkdown></div>;
}
