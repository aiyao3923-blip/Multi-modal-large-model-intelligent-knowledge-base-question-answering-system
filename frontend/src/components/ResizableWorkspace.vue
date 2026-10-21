<template>
  <div ref="root" class="reading-workspace" :class="{ resizing: drag, focused: focused }">
    <header class="workspace-tools"><slot name="toolbar" :toggle-sidebar="toggleSidebar" :sidebar-open="sidebarVisible && !focused" :toggle-inspector="toggleInspector" :inspector-open="inspector && !focused" :toggle-focus="toggleFocus" :focused="focused" :reset="resetLayout" /></header>
    <div class="workspace-columns" :style="{ gridTemplateColumns: columns }">
      <button v-if="overlayActive" class="panel-backdrop" type="button" aria-label="关闭侧栏" @click="closeOverlays" />
      <aside v-if="sidebarVisible && !focused" :id="sidebarId" ref="sidebarElement" class="workspace-panel library-panel" :class="{ floating: leftOverlay }" :style="leftOverlay ? { width: Math.min(layout.left + 48, width - 40) + 'px' } : {}" :role="leftOverlay ? 'dialog' : undefined" :aria-modal="leftOverlay ? true : undefined" aria-label="论文与会话" @keydown="trapFocus($event, leftOverlay)">
        <button v-if="leftOverlay" type="button" class="floating-close" aria-label="关闭论文与会话" @click="closeSidebar"><AppIcon name="close" :size="18" /></button>
        <slot name="sidebar" />
      </aside>
      <div v-if="leftDocked" class="panel-divider" role="separator" tabindex="0" aria-label="调整论文与会话栏宽度" aria-orientation="vertical" :aria-controls="sidebarId" :aria-valuemin="leftBounds[0]" :aria-valuemax="leftBounds[1]" :aria-valuenow="leftWidth" title="拖动调整宽度；方向键微调，Home/End 最小/最大" @pointerdown="startResize($event, 'left')" @pointermove="moveResize" @pointerup="stopResize" @pointercancel="stopResize" @lostpointercapture="stopResize" @keydown="resizeKey($event, 'left')"><span /></div>
      <section class="workspace-answer" :inert="overlayActive" aria-label="论文问答"><slot /></section>
      <div v-if="rightDocked" class="panel-divider" role="separator" tabindex="0" aria-label="调整原文证据栏宽度" aria-orientation="vertical" :aria-controls="inspectorId" :aria-valuemin="rightBounds[0]" :aria-valuemax="rightBounds[1]" :aria-valuenow="rightWidth" title="拖动调整宽度；方向键微调，Home/End 最小/最大" @pointerdown="startResize($event, 'right')" @pointermove="moveResize" @pointerup="stopResize" @pointercancel="stopResize" @lostpointercapture="stopResize" @keydown="resizeKey($event, 'right')"><span /></div>
      <aside v-if="inspector && !focused" :id="inspectorId" ref="inspectorElement" class="workspace-panel evidence-panel" :class="{ floating: rightOverlay }" :style="rightOverlay ? { width: Math.min(layout.right, width - 32) + 'px' } : {}" :role="rightOverlay ? 'dialog' : undefined" :aria-modal="rightOverlay ? true : undefined" aria-label="原文证据" @keydown="trapFocus($event, rightOverlay)">
        <slot name="inspector" :close="closeInspector" />
      </aside>
    </div>
  </div>
</template>
<script setup>
import { computed, getCurrentInstance, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { DEFAULT_LAYOUT, clamp, readLayout, resizeBounds, writeLayout } from '@/utils/workspaceLayout'
const props = defineProps({ storageKey: { type: String, default: 'paper-workspace-layout-v1' }, landing: { type: Boolean, default: false } })
const root = ref(), sidebarElement = ref(), inspectorElement = ref()
const width = ref(1200), layout = reactive({ ...DEFAULT_LAYOUT })
const sidebar = ref(true), inspector = ref(false), focused = ref(false), drag = ref(null)
const uid = getCurrentInstance().uid
const sidebarId = 'paper-library-' + uid, inspectorId = 'paper-evidence-' + uid
let observer, returnFocus
const sidebarRequested = ref(false)
const leftOverlay = computed(() => width.value < 1000 || props.landing || inspector.value && width.value - layout.left - layout.right - 12 < 760)
const sidebarVisible = computed(() => sidebar.value && (!leftOverlay.value || sidebarRequested.value))
const rightOverlay = computed(() => width.value - layout.right - 6 < 760)
const leftDocked = computed(() => sidebarVisible.value && !focused.value && !leftOverlay.value)
const rightDocked = computed(() => inspector.value && !focused.value && !rightOverlay.value)
const overlayActive = computed(() => !focused.value && (sidebarVisible.value && leftOverlay.value || inspector.value && rightOverlay.value))
const leftBounds = computed(() => resizeBounds('left', width.value, rightDocked.value ? layout.right : 0))
const leftWidth = computed(() => clamp(layout.left, ...leftBounds.value))
const rightBounds = computed(() => resizeBounds('right', width.value, leftDocked.value ? leftWidth.value : 0))
const rightWidth = computed(() => clamp(layout.right, ...rightBounds.value))
const columns = computed(() => `${leftDocked.value ? leftWidth.value + 'px 6px ' : ''}minmax(0, 1fr)${rightDocked.value ? ' 6px ' + rightWidth.value + 'px' : ''}`)
function storage() { try { return window.localStorage } catch { return null } }
function persist() { writeLayout(storage(), props.storageKey, { ...layout, sidebar: sidebar.value }) }
function restore() { Object.assign(layout, readLayout(storage(), props.storageKey)); sidebar.value = layout.sidebar; sidebarRequested.value = false }
function rememberFocus() { returnFocus = document.activeElement }
async function focusPanel(panel) { await nextTick(); panel.value?.querySelector('button,input,[tabindex="0"]')?.focus({ preventScroll: true }) }
function restoreFocus() { if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true }) }
function toggleSidebar() {
  focused.value = false
  if (leftOverlay.value) { sidebarRequested.value = !sidebarVisible.value; sidebar.value = true }
  else sidebar.value = !sidebar.value
  if (sidebarVisible.value && leftOverlay.value) { rememberFocus(); inspector.value = false; focusPanel(sidebarElement) }
  if (!leftOverlay.value) persist()
}
function openSidebar() { if (!sidebarVisible.value || focused.value) { focused.value = false; sidebarRequested.value = false; sidebar.value = false; toggleSidebar() } }
function openInspector() {
  focused.value = false; sidebarRequested.value = false; inspector.value = true
  if (rightOverlay.value) { rememberFocus(); focusPanel(inspectorElement) }
}
function toggleInspector() { if (inspector.value && !focused.value) closeInspector(); else openInspector() }
function closeSidebar() { sidebarRequested.value = false; sidebar.value = false; persist(); restoreFocus() }
function closeInspector() { inspector.value = false; restoreFocus() }
function closeOverlays() { if (leftOverlay.value) sidebarRequested.value = false; if (rightOverlay.value) inspector.value = false; restoreFocus() }
function toggleFocus() { focused.value = !focused.value }
function resetLayout() { Object.assign(layout, DEFAULT_LAYOUT); sidebar.value = true; sidebarRequested.value = false; focused.value = false; persist() }
function bounds(side) { return side === 'left' ? leftBounds.value : rightBounds.value }
function startResize(event, side) {
  if (event.button !== 0) return
  event.preventDefault()
  event.currentTarget.focus({ preventScroll: true })
  event.currentTarget.setPointerCapture(event.pointerId)
  drag.value = { side, x: event.clientX, initial: side === 'left' ? leftWidth.value : rightWidth.value, pointer: event.pointerId }
}
function moveResize(event) {
  if (!drag.value || event.pointerId !== drag.value.pointer) return
  const { side, x, initial } = drag.value
  layout[side] = clamp(initial + (event.clientX - x) * (side === 'left' ? 1 : -1), ...bounds(side))
}
function stopResize() { if (!drag.value) return; drag.value = null; persist() }
function resizeKey(event, side) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const range = bounds(side), current = side === 'left' ? leftWidth.value : rightWidth.value
  const delta = (event.key === 'ArrowRight' ? 1 : -1) * (side === 'left' ? 1 : -1) * (event.shiftKey ? 64 : 16)
  layout[side] = event.key === 'Home' ? range[0] : event.key === 'End' ? range[1] : clamp(current + delta, ...range)
  persist()
}
function trapFocus(event, enabled) {
  if (!enabled || event.key !== 'Tab') return
  const nodes = [...event.currentTarget.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(el => el.getClientRects().length)
  const first = nodes[0], last = nodes.at(-1)
  if (!nodes.length) { event.preventDefault(); return }
  if (!nodes.includes(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first)?.focus(); return }
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
function escape(event) { if (event.key === 'Escape') { if (overlayActive.value) closeOverlays(); else if (focused.value) focused.value = false } }
watch(() => props.storageKey, restore)
watch(leftOverlay, (value, old) => { if (value && !old) sidebarRequested.value = false })
watch(() => props.landing, () => { sidebarRequested.value = false })
onMounted(() => {
  width.value = root.value.clientWidth
  restore()
  observer = new ResizeObserver(entries => { width.value = entries[0].contentRect.width })
  observer.observe(root.value)
  window.addEventListener('keydown', escape)
})
onBeforeUnmount(() => { observer?.disconnect(); window.removeEventListener('keydown', escape) })
defineExpose({ openSidebar, openInspector, closeInspector, toggleSidebar, closeSidebar, resetLayout })
</script>
<style scoped>
.reading-workspace { height: 100%; min-height: 0; min-width: 0; display: flex; flex-direction: column; background: var(--color-page); }
.workspace-tools { flex: none; min-width: 0; border-bottom: 1px solid var(--color-line); background: var(--color-surface); }
.workspace-columns { display: grid; flex: 1; min-height: 0; min-width: 0; position: relative; overflow: hidden; }
.workspace-answer { min-width: 0; min-height: 0; display: flex; flex-direction: column; background: var(--color-surface); overflow: hidden; }
.workspace-panel { min-height: 0; min-width: 0; overflow: hidden; background: var(--color-surface); display: flex; flex-direction: column; }
.library-panel { background: var(--color-page); }
.panel-divider { touch-action: none; cursor: col-resize; background: var(--color-page); display: flex; align-items: center; justify-content: center; outline-offset: -2px; z-index: 2; }
.panel-divider span { width: 3px; height: 32px; border-radius: 4px; background: var(--color-line); }
.panel-divider:hover span, .panel-divider:focus-visible span, .resizing .panel-divider span { background: var(--color-accent); }
.panel-divider:focus-visible { outline: 2px solid var(--color-accent); }
.resizing { cursor: col-resize; user-select: none; }
.resizing :deep(iframe) { pointer-events: none; }
.floating { position: absolute; inset-block: 0; z-index: 22; box-shadow: 0 12px 40px #17203322; max-width: calc(100% - 24px); }
.library-panel.floating { left: 0; padding-top: 36px; }
.evidence-panel.floating { right: 0; }
.floating-close { position: absolute; right: 10px; top: 7px; background: none; border: 0; color: var(--color-secondary); padding: 5px; cursor: pointer; }
.panel-backdrop { border: 0; background: #17203338; position: absolute; inset: 0; z-index: 20; cursor: pointer; }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }

.workspace-tools { min-height: 56px; }
.workspace-panel.evidence-panel { border-left: 1px solid var(--color-line); }
.panel-divider { background: var(--color-page); }
.workspace-answer { border-radius: 0; }
</style>