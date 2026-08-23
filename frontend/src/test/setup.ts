/**
 * Global test setup, loaded once before any test file.
 *
 * Three jobs:
 *
 * 1. Register jest-dom's matchers (`toBeInTheDocument`, `toBeDisabled`, ...),
 *    which describe intent far better than poking at DOM properties by hand.
 * 2. Give async queries enough headroom to survive a loaded machine.
 * 3. Unmount everything after each test. Without this, components from a
 *    finished test stay in the document and the next test's queries can match
 *    *their* elements -- producing failures that look impossible and depend on
 *    the order tests happen to run in.
 */

import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterEach } from 'vitest'

configure({
  // Testing Library's default is 1000ms, which is fine for one file and too
  // tight for eight running in parallel: a `findBy*` waiting on a state update
  // can lose its budget to CPU contention rather than to anything being wrong.
  // That produces a test that passes alone and fails in a full run -- the
  // worst kind, because the failure says nothing about the code. A larger
  // budget costs nothing when the element does appear; only a genuine failure
  // waits the whole time.
  asyncUtilTimeout: 5000,
})

afterEach(() => {
  cleanup()
})
