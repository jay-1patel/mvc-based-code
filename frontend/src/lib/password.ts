/** Same rule the API enforces in routes/auth.py `_validate_password_strength`. */
export const PASSWORD_MIN_LENGTH = 12

const COMMON_PASSWORDS = ['password', '12345678', 'qwerty', 'admin']

export const PASSWORD_HINT =
  'At least 12 characters, with an uppercase letter, a lowercase letter, a digit, and a symbol.'

/** Null when the password is acceptable. Otherwise the message the API would return. */
export function passwordError(password: string): string | null {
  if (!password || password.length < PASSWORD_MIN_LENGTH) {
    return `Password must be at least ${PASSWORD_MIN_LENGTH} characters long`
  }
  if (COMMON_PASSWORDS.includes(password.toLowerCase())) {
    return 'Password is too common'
  }
  if (!/[A-Z]/.test(password)) {
    return 'Password must contain at least one uppercase letter'
  }
  if (!/[a-z]/.test(password)) {
    return 'Password must contain at least one lowercase letter'
  }
  if (!/\d/.test(password)) {
    return 'Password must contain at least one digit'
  }
  if (!/[!@#$%^&*()_+\-=[\]{}|;:,.<>?]/.test(password)) {
    return 'Password must contain at least one special character'
  }
  return null
}
