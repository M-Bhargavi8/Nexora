/**
 * Tests for the drop zone's drag highlight.
 *
 * Drag events also fire on the zone's child elements, so entering the inner
 * text produces a second dragenter and leaving it a dragleave. The highlight
 * should only clear when the pointer leaves the zone entirely.
 */

import { createRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FileDropZone } from './FileDropZone'

function renderZone() {
  render(<FileDropZone onFiles={vi.fn()} inputRef={createRef<HTMLInputElement>()} />)
  return screen.getByText(/Drag files here/).closest('div')!
}

const HIGHLIGHT = 'border-brand-500'

describe('drag highlight', () => {
  it('stays on while the pointer moves between child elements', () => {
    const zone = renderZone()

    fireEvent.dragEnter(zone)
    fireEvent.dragEnter(zone) // pointer crosses onto the inner text
    fireEvent.dragLeave(zone) // ...and leaves it again, still inside the zone

    expect(zone.className).toContain(HIGHLIGHT)
  })

  it('turns off once every dragenter has a matching dragleave', () => {
    const zone = renderZone()

    fireEvent.dragEnter(zone)
    fireEvent.dragEnter(zone)
    fireEvent.dragLeave(zone)
    fireEvent.dragLeave(zone)

    expect(zone.className).not.toContain(HIGHLIGHT)
  })

  it('resets on drop, so the next drag starts from zero', () => {
    const zone = renderZone()

    fireEvent.dragEnter(zone)
    fireEvent.dragEnter(zone)
    fireEvent.drop(zone, { dataTransfer: { files: [] } })
    expect(zone.className).not.toContain(HIGHLIGHT)

    // Had the count survived the drop, this single leave would not clear it.
    fireEvent.dragEnter(zone)
    fireEvent.dragLeave(zone)
    expect(zone.className).not.toContain(HIGHLIGHT)
  })
})
