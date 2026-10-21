/** Passwords belong to the browser credential manager, never application storage. */
function passwordManager(scope, operation) {
  if (scope?.isSecureContext !== true || typeof scope?.PasswordCredential !== 'function') return null
  const manager = scope.navigator?.credentials
  return typeof manager?.[operation] === 'function' ? manager : null
}

function validLogin(username, password) {
  return typeof username === 'string' && Boolean(username.trim()) && username.length <= 64
    && typeof password === 'string' && password.length > 0
}

/** Silent means that opening the form never triggers an account chooser or login. */
export async function readSavedLogin(scope = globalThis) {
  try {
    const manager = passwordManager(scope, 'get')
    if (!manager) return null
    const credential = await manager.get({ password: true, mediation: 'silent' })
    if (credential?.type !== 'password' || !validLogin(credential.id, credential.password)) return null
    return { username: credential.id, password: credential.password }
  } catch {
    // Browser policy, cancellation and unsupported credential types fall back to typing.
    return null
  }
}

/** Only call after the existing login endpoint has accepted these credentials. */
export async function saveLogin(login, scope = globalThis) {
  try {
    const manager = passwordManager(scope, 'store')
    if (!manager || !validLogin(login?.username, login?.password)) return false
    await manager.store(new scope.PasswordCredential({ id: login.username, password: login.password }))
    // This is an accepted API request, not proof that the user chose Save in browser UI.
    return true
  } catch {
    return false
  }
}

/** Synchronize native autofill, including deliberate empty edits, before validation. */
export function readLoginInputs(element, fallback) {
  const value = (name) => {
    const input = element?.querySelector?.(`input[name="${name}"]`)
    return typeof input?.value === 'string' ? input.value : fallback[name]
  }
  return { username: value('username'), password: value('password') }
}
