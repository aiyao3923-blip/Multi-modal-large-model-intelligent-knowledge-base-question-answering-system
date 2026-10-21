<template>
  <div class="workspace-shell" :class="{ 'is-reading': readingMode, 'nav-collapsed': collapsed && !compact }">
    <button v-if="compact && menuOpen" type="button" class="sidebar-backdrop" tabindex="-1" aria-hidden="true" @click="closeNavigation"></button>
    <aside id="workspace-navigation" ref="sidebar" class="workspace-sidebar" :class="{ open: menuOpen }" tabindex="-1"
      :inert="compact && !menuOpen" :aria-hidden="compact && !menuOpen ? true : undefined"
      :role="compact && menuOpen ? 'dialog' : undefined" :aria-modal="compact && menuOpen ? true : undefined" aria-label="主导航">
      <div class="mobile-sidebar-bar">
        <span>导航</span>
        <button ref="closeButton" type="button" class="icon-button" aria-label="关闭导航" @click="closeNavigation"><AppIcon name="close" :size="20" /></button>
      </div>
      <RouterLink to="/" class="brand-lockup" aria-label="论文知识库，返回论文问答" @click="closeNavigation">
        <span class="brand-symbol" aria-hidden="true"><AppIcon name="book" :size="20" /></span><strong>知识库</strong>
      </RouterLink>
      <button v-if="!compact" type="button" class="navigation-toggle" :aria-label="collapsed ? '展开主导航' : '收起主导航'" :aria-expanded="!collapsed" @click="toggleNavigationWidth"><AppIcon :name="collapsed ? 'chevron' : 'back'" :size="16" /><span>{{ collapsed ? '展开' : '收起导航' }}</span></button>
      <nav class="main-navigation" aria-label="论文工作区">
        <RouterLink v-for="link in primaryLinks" :key="link.to" :to="link.to" class="nav-link" :class="{ active: activeLink(link.to) }"
          :aria-current="activeLink(link.to) ? 'page' : undefined" @click="closeNavigation">
          <AppIcon :name="link.icon" :size="18" /><span class="nav-label-wide">{{ link.label }}</span><span class="nav-label-short">{{ link.short }}</span>
        </RouterLink>
      </nav>
      <details v-if="user.isOperator" class="sidebar-group" :open="toolsOpen" @toggle="toolsOpen = $event.target.open">
        <summary @click="expandForGroup"><AppIcon name="grid" :size="18" /><span>工具</span><AppIcon name="down" class="group-chevron" :size="14" /></summary>
        <nav class="group-navigation" aria-label="辅助工具">
          <RouterLink v-for="link in toolLinks" :key="link.to" :to="link.to" class="nav-link" :class="{ active: activeLink(link.to) }"
            :aria-current="activeLink(link.to) ? 'page' : undefined" @click="closeNavigation"><span>{{ link.label }}</span></RouterLink>
        </nav>
      </details>
      <details v-if="user.isAdmin" class="sidebar-group" :open="settingsOpen" @toggle="settingsOpen = $event.target.open">
        <summary @click="expandForGroup"><AppIcon name="shield" :size="18" /><span>设置</span><AppIcon name="down" class="group-chevron" :size="14" /></summary>
        <nav class="group-navigation" aria-label="系统设置">
          <RouterLink v-for="link in settingsLinks" :key="link.to" :to="link.to" class="nav-link" :class="{ active: activeLink(link.to) }"
            :aria-current="activeLink(link.to) ? 'page' : undefined" @click="closeNavigation"><span>{{ link.label }}</span></RouterLink>
        </nav>
      </details>
      <div class="sidebar-bottom">
        <div class="profile-row">
          <span class="profile-avatar" aria-hidden="true">{{ displayName.slice(0, 1) }}</span>
          <div class="profile-text"><b :title="displayName">{{ displayName }}</b><small>{{ roleLabels[user.role] || '用户' }}</small></div>
          <button type="button" class="icon-button" aria-label="退出登录" title="退出登录" @click="logout"><AppIcon name="logout" :size="18" /></button>
        </div>
      </div>
    </aside>
    <div class="workspace-content" :inert="compact && menuOpen">
      <header v-if="!readingMode" class="workspace-header">
        <div class="header-location">
          <button ref="openButton" type="button" class="icon-button mobile-menu" aria-label="打开导航" aria-controls="workspace-navigation" :aria-expanded="menuOpen" @click="menuOpen = true"><AppIcon name="menu" :size="20" /></button>
          <span class="header-page" :title="route.meta.title">{{ route.meta.title || '论文问答' }}</span>
        </div>
        <RouterLink :to="route.path === '/' ? '/admin/kbs' : '/'" class="header-quick">
          <AppIcon :name="route.path === '/' ? 'book' : 'chat'" :size="16" /><span>{{ route.path === '/' ? '知识库' : '论文问答' }}</span>
        </RouterLink>
      </header>
      <button v-if="readingMode && compact" ref="readingMenuButton" type="button" class="reading-menu icon-button" aria-label="打开导航" aria-controls="workspace-navigation" :aria-expanded="menuOpen" @click="menuOpen = true"><AppIcon name="menu" :size="20" /></button>
      <main id="main-content" tabindex="-1" ref="mainContent" class="workspace-main"><slot /></main>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'

const user = useUserStore(), route = useRoute(), router = useRouter()
const menuOpen = ref(false)
const readingMode = computed(() => route.path === '/')
const collapsed = ref(route.path === '/')
const readingMenuButton = ref()
function navigationKey() { return 'knowledge-navigation-v2-' + (user.user?.id || 'anonymous') + (readingMode.value ? '-reading' : '-management') }
function restoreNavigationWidth() {
  let saved
  try { saved = window.localStorage?.getItem(navigationKey()) } catch { /* Optional preference. */ }
  collapsed.value = saved === 'collapsed' ? true : saved === 'expanded' ? false : readingMode.value
}
function toggleNavigationWidth() {
  collapsed.value = !collapsed.value
  try { window.localStorage?.setItem(navigationKey(), collapsed.value ? 'collapsed' : 'expanded') } catch { /* Optional preference. */ }
}
function expandForGroup() { if (collapsed.value && !compact.value) toggleNavigationWidth() }
function navigationTrigger() { return readingMode.value ? readingMenuButton.value || openButton.value : openButton.value }
const compactQuery = window.matchMedia('(max-width: 899.98px)')
const compact = ref(compactQuery.matches)
const sidebar = ref(), closeButton = ref(), openButton = ref(), mainContent = ref()
const displayName = computed(() => user.user?.display_name || user.user?.username || '当前用户')
const roleLabels = { admin: '管理员', operator: '知识库维护员', user: '普通用户' }
// Authentication gates the workspace; the API owns per-library read/write authorization.
const primaryLinks = [
  { to: '/', icon: 'chat', label: '论文问答', short: '问答' },
  { to: '/admin/kbs', icon: 'book', label: '知识库', short: '库' },
  { to: '/admin/tasks', icon: 'tasks', label: '导入任务', short: '任务' },
]
const toolLinks = [
  { to: '/admin/overview', label: '概览' },
  { to: '/admin/feedbacks', label: '问答反馈' },
  { to: '/admin/stats', label: '使用统计' },
]
const settingsLinks = [
  { to: '/admin/channels', label: '模型通道' },
  { to: '/admin/users', label: '用户与权限' },
]
function activeLink(target) { return route.path === target || target === '/admin/kbs' && route.path.startsWith('/admin/kbs/') }
const toolsOpen = ref(toolLinks.some(link => activeLink(link.to)))
const settingsOpen = ref(settingsLinks.some(link => activeLink(link.to)))
function closeNavigation() { menuOpen.value = false }
function onCompactChange(event) {
  const focusWasInside = sidebar.value?.contains(document.activeElement)
  compact.value = event.matches
  closeNavigation()
  if (focusWasInside) nextTick(() => (event.matches ? navigationTrigger() : mainContent.value)?.focus())
}
function focusableNavigation() {
  return [...(sidebar.value?.querySelectorAll('a[href], button:not([disabled]), summary, [tabindex="0"]') || [])]
    .filter(element => element.getClientRects().length && !element.closest('[inert]'))
}
function onNavigationKey(event) {
  if (!compact.value || !menuOpen.value) return
  if (event.key === 'Escape') { event.preventDefault(); closeNavigation(); return }
  if (event.key !== 'Tab') return
  const controls = focusableNavigation()
  const first = controls[0], last = controls[controls.length - 1]
  if (!first) { event.preventDefault(); sidebar.value?.focus(); return }
  const active = document.activeElement
  if (!controls.includes(active)) { event.preventDefault(); (event.shiftKey ? last : first).focus() }
  else if (event.shiftKey && active === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && active === last) { event.preventDefault(); first.focus() }
}
function onNavigationFocus(event) {
  if (compact.value && menuOpen.value && !sidebar.value?.contains(event.target)) closeButton.value?.focus()
}
watch(menuOpen, async value => {
  await nextTick()
  if (compact.value) (value ? closeButton.value : navigationTrigger())?.focus()
})
watch(readingMode, restoreNavigationWidth)
watch(() => route.fullPath, () => {
  closeNavigation()
  toolsOpen.value = toolLinks.some(link => activeLink(link.to))
  settingsOpen.value = settingsLinks.some(link => activeLink(link.to))
})
onMounted(() => {
  restoreNavigationWidth()
  compactQuery.addEventListener('change', onCompactChange)
  window.addEventListener('keydown', onNavigationKey)
  window.addEventListener('focusin', onNavigationFocus)
})
onBeforeUnmount(() => {
  compactQuery.removeEventListener('change', onCompactChange)
  window.removeEventListener('keydown', onNavigationKey)
  window.removeEventListener('focusin', onNavigationFocus)
})
function logout() { user.logout(); router.replace('/login') }
</script>

<style scoped>
.workspace-shell { display: flex; height: 100dvh; min-height: 0; overflow: hidden; }
.workspace-sidebar { width: var(--workspace-sidebar-width); flex-shrink: 0; background: var(--color-page); border-right: 1px solid var(--color-line); display: flex; flex-direction: column; padding: 0 12px; z-index: 50; overflow-y: auto; overscroll-behavior: contain; }
.brand-lockup { display: flex; align-items: center; gap: 8px; min-height: 68px; padding: 16px 8px; flex-shrink: 0; }
.brand-symbol { width: 32px; height: 32px; display: grid; place-items: center; border-radius: 8px; color: var(--color-accent); background: var(--color-accent-soft); }
.brand-lockup strong { font-size: 15px; line-height: 1.4; font-weight: 600; }
.main-navigation { display: grid; gap: 4px; margin-bottom: 16px; }
.nav-link { display: flex; align-items: center; gap: 10px; min-height: 40px; padding: 8px 12px; color: var(--color-secondary); border: 1px solid transparent; border-radius: 6px; font-size: 14px; transition: background .15s, color .15s; }
.nav-link:hover { color: var(--color-ink); background: var(--color-page); }
.nav-link.active { color: var(--color-accent); background: var(--color-accent-soft); font-weight: 600; }
.sidebar-group { margin-bottom: 4px; }
.sidebar-group summary { display: flex; align-items: center; gap: 10px; min-height: 40px; padding: 8px 12px; border-radius: 6px; color: var(--color-secondary); font-size: 14px; cursor: pointer; list-style: none; }
.sidebar-group summary::-webkit-details-marker { display: none; }
.sidebar-group summary:hover { color: var(--color-ink); background: var(--color-page); }
.sidebar-group summary:focus-visible { outline: 2px solid var(--color-focus); outline-offset: -2px; }
.group-chevron { margin-left: auto; transition: transform .15s; }
.sidebar-group[open] .group-chevron { transform: rotate(180deg); }
.group-navigation { display: grid; gap: 4px; margin: 4px 0 8px 20px; padding-left: 8px; border-left: 1px solid var(--color-line); }
.group-navigation .nav-link { min-height: 36px; padding: 6px 12px; font-size: 13px; }
.sidebar-bottom { margin-top: auto; padding: 16px 0 12px; }
.profile-row { display: flex; align-items: center; gap: 8px; padding: 12px 0 0; border-top: 1px solid var(--color-line); }
.profile-avatar { width: 28px; height: 28px; display: grid; place-items: center; border: 1px solid var(--color-line); border-radius: 6px; background: var(--color-page); color: var(--color-secondary); font-size: 13px; flex-shrink: 0; }
.profile-text { flex: 1; min-width: 0; }
.profile-text b { display: block; font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.profile-text small { display: block; font-size: 12px; color: var(--color-muted); }
.workspace-content { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.workspace-header { height: var(--workspace-header-height); flex-shrink: 0; background: var(--color-surface); border-bottom: 1px solid var(--color-line); padding: 8px var(--page-padding); display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.header-location { display: flex; align-items: center; gap: 8px; min-width: 0; }
.header-page { font-size: 14px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.header-quick { display: inline-flex; align-items: center; gap: 8px; min-height: 36px; font-size: 13px; color: var(--color-accent); white-space: nowrap; flex-shrink: 0; }
.header-quick:hover { color: var(--color-accent-hover); text-decoration: underline; text-underline-offset: 4px; }
.workspace-main { flex: 1; min-width: 0; min-height: 0; overflow: auto; scrollbar-gutter: stable; }
.workspace-main:focus { outline-offset: -3px; }
.mobile-menu, .sidebar-backdrop, .mobile-sidebar-bar { display: none; }
@media (max-width: 899.98px) {
  .workspace-sidebar { position: fixed; inset: 0 auto 0 0; width: var(--workspace-drawer-width); max-width: calc(100vw - 48px); transform: translateX(-100%); transition: transform .18s; }
  .workspace-sidebar.open { transform: translateX(0); }
  .mobile-sidebar-bar { display: flex; align-items: center; justify-content: space-between; min-height: 52px; padding-left: 12px; color: var(--color-secondary); font-size: 13px; }
  .mobile-sidebar-bar .icon-button { width: 44px; height: 44px; }
  .brand-lockup { min-height: 52px; padding-top: 8px; padding-bottom: 12px; }
  .sidebar-backdrop { display: block; position: fixed; inset: 0; z-index: 49; border: 0; background: rgba(23, 32, 51, .32); touch-action: none; }
  .mobile-menu { display: inline-flex; }
  .nav-link, .sidebar-group summary, .group-navigation .nav-link { min-height: 44px; }
  .workspace-header { padding-inline: 12px 16px; }
  .workspace-main { scrollbar-gutter: auto; }
}
@media (prefers-reduced-motion: reduce) {
  .workspace-sidebar, .nav-link, .group-chevron { transition: none; }
}

/* UI-R2: navigation density is independent of the reading workspace. */
.workspace-content { position: relative; }
.nav-label-short { display: none; }
.navigation-toggle { display: flex; align-items: center; gap: 10px; min-height: 36px; padding: 6px 12px; margin-bottom: 14px; color: var(--color-secondary); border: 0; background: transparent; font: inherit; font-size: 12px; border-radius: 8px; cursor: pointer; }
.navigation-toggle:hover { background: var(--color-surface); }
.nav-link { border-radius: 8px; min-height: 44px; }
.nav-link.active { background: var(--color-surface); box-shadow: 0 1px 4px rgba(23,32,51,.06); }
.brand-symbol { width: 36px; height: 36px; background: var(--color-accent); color: white; border-radius: 10px; }
.nav-collapsed .workspace-sidebar { width: 64px; padding-inline: 6px; }
.nav-collapsed .brand-lockup { padding: 14px 0; justify-content: center; min-height: 64px; }
.nav-collapsed .brand-lockup strong, .nav-collapsed .nav-label-wide, .nav-collapsed .profile-text, .nav-collapsed .profile-avatar, .nav-collapsed .group-chevron { display: none; }
.nav-collapsed .nav-label-short { display: block; }
.nav-collapsed .nav-link, .nav-collapsed .sidebar-group summary { flex-direction: column; gap: 4px; justify-content: center; padding: 8px 2px; font-size: 12px; min-height: 56px; }
.nav-collapsed .navigation-toggle { flex-direction: column; gap: 2px; margin-bottom: 8px; padding: 4px 0; }
.nav-collapsed .group-navigation { display: none; }
.nav-collapsed .profile-row { justify-content: center; }
.is-reading .workspace-main { overflow: hidden; scrollbar-gutter: auto; }
.reading-menu { position: absolute; top: 9px; left: 10px; z-index: 20; background: var(--color-surface); }
@media (max-width: 899.98px) { .is-reading :deep(.conversation-toolbar) { padding-left: 54px; min-height: 56px; } }
</style>

