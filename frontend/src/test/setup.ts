/**
 * Global test setup, loaded once before any test file.
 *
 * Two jobs:
 *
 * 1. Register jest-dom's matchers (`toBeInTheDocument`, `toBeDisabled`, ...),
 *    which describe intent far better than poking at DOM properties by hand.
 * 2. Unmount everything after each test. Without this, components from a
 *    finished test stay in the document and the next test's queries can match
 *    *their* elements -- producing failures that look impossible and depend on
 *    the order tests happen to run in.
 */

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})
