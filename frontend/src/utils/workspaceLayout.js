export const DEFAULT_LAYOUT = Object.freeze({ left: 240, right: 360, sidebar: true })
export const LIMITS = Object.freeze({ left: [220, 320], right: [320, 480] })
export function clamp(value, minimum, maximum) { return Math.min(maximum, Math.max(minimum, value)) }
export function normalizeLayout(value) {
  const result = { ...DEFAULT_LAYOUT }
  if (!value || typeof value !== 'object') return result
  for (const side of ['left', 'right']) {
    if (typeof value[side] === 'number' && Number.isFinite(value[side])) result[side] = clamp(Math.round(value[side]), ...LIMITS[side])
  }
  if (typeof value.sidebar === 'boolean') result.sidebar = value.sidebar
  return result
}
export function resizeBounds(side, containerWidth, otherWidth = 0) {
  const [minimum, maximum] = LIMITS[side]
  return [minimum, Math.max(minimum, Math.min(maximum, containerWidth - 760 - otherWidth - (otherWidth ? 12 : 6)))]
}
function currentKey(key) { return key.replace('paper-workspace-layout-v1', 'paper-workspace-layout-v2') }
export function readLayout(storage, key) {
  try {
    const target = currentKey(key), saved = storage.getItem(target)
    if (saved !== null) return normalizeLayout(JSON.parse(saved))
    const legacy = key.includes('paper-workspace-layout-v2') ? key.replace('paper-workspace-layout-v2', 'paper-workspace-layout-v1') : key
    const value = normalizeLayout(JSON.parse(storage.getItem(legacy)))
    if (target !== legacy && storage.getItem(legacy) !== null) storage.setItem(target, JSON.stringify(value))
    return value
  } catch { return normalizeLayout(null) }
}
export function writeLayout(storage, key, value) {
  try { storage.setItem(currentKey(key), JSON.stringify(normalizeLayout(value))) } catch { /* Optional local layout preference. */ }
}
