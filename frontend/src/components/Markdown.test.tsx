/**
 * Tests for markdown rendering.
 *
 * The first group is the important one. Model output is untrusted: it is
 * shaped by whatever the user typed, and from Phase 5 by whatever is inside
 * the documents they upload. If a model can be talked into emitting HTML and
 * we render it, that is stored XSS with extra steps.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Markdown } from './Markdown'

describe('untrusted HTML in model output', () => {
  it('does not create an element from a raw <img> tag', () => {
    const { container } = render(
      <Markdown>{'<img src=x onerror="alert(1)">'}</Markdown>,
    )

    expect(container.querySelector('img')).toBeNull()
  })

  it('does not create a script element', () => {
    const { container } = render(
      <Markdown>{'<script>alert(1)</script>'}</Markdown>,
    )

    expect(container.querySelector('script')).toBeNull()
  })

  it('does not create an iframe', () => {
    const { container } = render(
      <Markdown>{'<iframe src="https://evil.example"></iframe>'}</Markdown>,
    )

    expect(container.querySelector('iframe')).toBeNull()
  })

  it('does not attach an inline event handler to a rendered element', () => {
    const { container } = render(
      <Markdown>{'<div onclick="alert(1)">click me</div>'}</Markdown>,
    )

    expect(container.querySelector('[onclick]')).toBeNull()
  })
})

describe('links', () => {
  it('opens in a new tab without leaking the opener or referrer', () => {
    // A model can emit any URL. `noopener` stops the opened page reaching back
    // through window.opener; `noreferrer` keeps this app's URL out of the
    // target's logs.
    render(<Markdown>{'[docs](https://example.com)'}</Markdown>)

    const link = screen.getByRole('link', { name: 'docs' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(link.getAttribute('rel')).toContain('noreferrer')
  })
})

describe('code', () => {
  it('renders a fenced block with its language and a copy button', () => {
    render(<Markdown>{'```python\nprint("hi")\n```'}</Markdown>)

    expect(screen.getByText('python')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument()
    expect(screen.getByText(/print\("hi"\)/)).toBeInTheDocument()
  })

  it('treats a fence with no language as a block, not inline code', () => {
    // A `language-` check alone would miss this and render multi-line code as
    // an inline span.
    render(<Markdown>{'```\nline one\nline two\n```'}</Markdown>)

    expect(screen.getByRole('button', { name: 'Copy' })).toBeInTheDocument()
    expect(screen.getByText('code')).toBeInTheDocument()
  })

  it('renders inline code without a copy button', () => {
    render(<Markdown>{'Use the `print` function.'}</Markdown>)

    expect(screen.getByText('print')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Copy' })).not.toBeInTheDocument()
  })
})

describe('gfm', () => {
  it('renders tables, which models emit routinely', () => {
    render(
      <Markdown>{'| a | b |\n| - | - |\n| 1 | 2 |'}</Markdown>,
    )

    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'a' })).toBeInTheDocument()
  })

  it('renders lists', () => {
    render(<Markdown>{'- first\n- second'}</Markdown>)

    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })
})

describe('partial markdown mid-stream', () => {
  it('renders an unterminated code fence without throwing', () => {
    // Half of a fenced block is on screen for as long as it takes to stream
    // the rest, so this is the normal case, not an edge case.
    expect(() =>
      render(<Markdown>{'Here you go:\n```python\nprint("hi'}</Markdown>),
    ).not.toThrow()
  })

  it('renders an unterminated bold marker without throwing', () => {
    expect(() => render(<Markdown>{'this is **bol'}</Markdown>)).not.toThrow()
  })
})
