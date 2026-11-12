import { describe, expect, it } from 'vitest'
import { passwordError } from './password'

describe('passwordError', () => {
  it('matches the server rule', () => {
    expect(passwordError('short')).toMatch(/at least 12/i)
    expect(passwordError('test-password')).toMatch(/uppercase/i)
    expect(passwordError('alllowercase1!')).toMatch(/uppercase/i)
    expect(passwordError('ALLUPPERCASE1!')).toMatch(/lowercase/i)
    expect(passwordError('NoDigitsHere!!')).toMatch(/digit/i)
    expect(passwordError('NoSpecial1234')).toMatch(/special/i)
    expect(passwordError('Test-password1!')).toBeNull()
  })
})
