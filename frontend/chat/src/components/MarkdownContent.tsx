import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

type MarkdownContentProps = {
  content: string
  className?: string
}

export function MarkdownContent({ content, className }: MarkdownContentProps) {
  return (
    <div className={className ?? 'markdown-body'}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          // Never load remote images from model output: a prompt-injected
          // reply could encode the user's data in an image URL. Show alt text.
          img: ({ alt }) => (alt ? <span>{alt}</span> : null),
          table: ({ children }) => (
            <div className="markdown-table-wrap">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
