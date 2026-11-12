import { describe, expect, it } from 'vitest'
// @ts-expect-error plain-node script, no types — it is the single
// implementation of the token guard, shared with `npm run check:tokens`.
import { runTokenCheck } from '../../scripts/check-tokens.mjs'

describe('remake theme tokens', () => {
  it('every semantic colour is defined, and remake files carry no colour literals or typo’d token classes', () => {
    expect(runTokenCheck(process.cwd())).toEqual([])
  })
})
