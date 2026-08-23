/**
 * Renders model output as markdown.
 *
 * SECURITY: `react-markdown` builds a React element tree; it never assigns to
 * `innerHTML`. A model that emits `<img src=x onerror=alert(1)>` therefore
 * renders those characters as text instead of executing them. That safety is
 * the entire reason this library was chosen over a markdown-to-HTML-string
 * parser. **Do not add `rehype-raw`** -- it re-enables raw HTML and hands that
 * hole straight back.
 *
 * Model output is untrusted input. It is shaped by whatever the user typed,
 * and from Phase 5 by whatever is inside the documents they upload.
 */

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CopyButton } from './CopyButton'

interface CodeBlockProps {
  code: string
  language: string | null
}

function CodeBlock({ code, language }: CodeBlockProps) {
  return (
    <div className="group relative my-3 overflow-hidden rounded-lg border border-ink-700 bg-ink-950">
      <div className="flex items-center justify-between border-b border-ink-700/70 bg-ink-900/60 px-3 py-1.5">
        <span className="font-mono text-[11px] text-mist-500">{language ?? 'code'}</span>
        <CopyButton text={code} />
      </div>
      {/* overflow-x-auto, so a long line scrolls inside the block rather than
          stretching the whole conversation sideways. */}
      <pre className="overflow-x-auto px-3 py-2.5">
        <code className="font-mono text-[13px] leading-relaxed text-mist-200">
          {code}
        </code>
      </pre>
    </div>
  )
}

interface MarkdownProps {
  children: string
}

export function Markdown({ children }: MarkdownProps) {
  return (
    <div className="text-sm leading-relaxed text-mist-200">
      <ReactMarkdown
        // GitHub-flavoured markdown: tables, strikethrough, task lists,
        // autolinks. Models emit all of these routinely.
        remarkPlugins={[remarkGfm]}
        components={{
          // CodeBlock renders its own <pre>, so the default wrapper is
          // unwrapped here to avoid nesting one inside another.
          pre: ({ children: preChildren }) => <>{preChildren}</>,
          code: ({ className, children: codeChildren, ...rest }) => {
            const text = String(codeChildren ?? '')
            const language = /language-(\w+)/.exec(className ?? '')?.[1] ?? null
            // A fenced block always ends in a newline; inline code never
            // contains one. That covers fences with no language tag, which a
            // `language-` check alone would miss and render as inline code.
            const isBlock = language !== null || text.includes('\n')

            if (isBlock) {
              return <CodeBlock code={text.replace(/\n$/, '')} language={language} />
            }
            return (
              <code
                className="rounded bg-ink-800 px-1 py-0.5 font-mono text-[12.5px] text-brand-200"
                {...rest}
              >
                {codeChildren}
              </code>
            )
          },
          a: ({ children: linkChildren, ...rest }) => (
            <a
              {...rest}
              // SECURITY: a model can emit any URL. `noopener` stops the opened
              // page reaching back through `window.opener`, and `noreferrer`
              // keeps this app's URL out of the target's referer log.
              target="_blank"
              rel="noopener noreferrer nofollow"
              className="text-brand-300 underline underline-offset-2 hover:text-brand-200"
            >
              {linkChildren}
            </a>
          ),
          p: ({ children: p }) => <p className="my-2 first:mt-0 last:mb-0">{p}</p>,
          ul: ({ children: c }) => (
            <ul className="my-2 list-disc space-y-1 pl-5">{c}</ul>
          ),
          ol: ({ children: c }) => (
            <ol className="my-2 list-decimal space-y-1 pl-5">{c}</ol>
          ),
          h1: ({ children: c }) => (
            <h3 className="mt-4 mb-2 text-base font-semibold text-mist-100">{c}</h3>
          ),
          h2: ({ children: c }) => (
            <h3 className="mt-4 mb-2 text-[15px] font-semibold text-mist-100">{c}</h3>
          ),
          h3: ({ children: c }) => (
            <h4 className="mt-3 mb-1.5 text-sm font-semibold text-mist-100">{c}</h4>
          ),
          blockquote: ({ children: c }) => (
            <blockquote className="my-2 border-l-2 border-ink-600 pl-3 text-mist-400">
              {c}
            </blockquote>
          ),
          table: ({ children: c }) => (
            <div className="my-3 overflow-x-auto">
              <table className="w-full border-collapse text-left text-[13px]">{c}</table>
            </div>
          ),
          th: ({ children: c }) => (
            <th className="border border-ink-700 bg-ink-850 px-2 py-1 font-medium text-mist-200">
              {c}
            </th>
          ),
          td: ({ children: c }) => (
            <td className="border border-ink-700 px-2 py-1">{c}</td>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
