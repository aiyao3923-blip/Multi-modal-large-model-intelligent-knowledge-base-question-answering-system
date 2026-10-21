<template>
<div class="page paper-library" :class="'view-' + viewMode">
  <header class="library-heading">
    <div><span class="library-eyebrow">资料空间</span><h1>知识库</h1><p v-if="loaded">{{ kbs.length }} 个库 · {{ formatNumber(docTotal) }} 份文档</p></div>
    <div class="library-actions">
      <el-button :loading="loading" @click="load"><AppIcon name="refresh" :size="16" />刷新</el-button>
      <el-button type="primary" @click="openCreate"><AppIcon name="plus" :size="16" />新建论文库</el-button>
    </div>
  </header>

  <div v-if="error" class="library-error" role="alert"><span>{{ error }}</span><el-button text @click="load">重试</el-button></div>
  <div v-if="actionError" class="library-error" role="alert">{{ actionError }}</div>
  <section class="library-panel" aria-label="论文库列表" :aria-busy="loading">
    <div class="library-toolbar">
      <div class="library-search"><label for="kb-search">搜索论文库</label><el-input id="kb-search" v-model="search" clearable placeholder="名称或说明"><template #prefix><AppIcon name="search" :size="16" /></template></el-input></div>
      <div class="library-access-filter"><label for="kb-access">访问范围</label><el-select id="kb-access" v-model="accessFilter" placeholder="全部可访问"><el-option label="全部可访问" value="" /><el-option label="我管理的" value="managed" /><el-option label="共享只读" value="readonly" /></el-select></div>
      <div class="library-status"><label for="kb-status">状态</label><el-select id="kb-status" v-model="status" placeholder="全部状态"><el-option label="全部" value="" /><el-option label="已启用" value="enabled" /><el-option label="已停用" value="disabled" /></el-select></div>
    </div>
    <div class="library-view-switch" role="group" aria-label="知识库显示方式"><button type="button" :aria-pressed="viewMode === 'cards'" @click="setViewMode('cards')"><AppIcon name="grid" :size="17" />卡片</button><button type="button" :aria-pressed="viewMode === 'list'" @click="setViewMode('list')"><AppIcon name="tasks" :size="17" />列表</button></div>
    <div v-if="loading && !loaded" class="library-empty" role="status"><p>正在加载论文库…</p><el-skeleton :rows="3" /></div>
    <div v-else-if="error && !loaded" class="library-empty"><p>论文库未能加载</p><el-button @click="load">重新加载</el-button></div>
    <div v-else-if="!filtered.length" class="library-empty">
      <p>{{ kbs.length ? '没有匹配的论文库' : '还没有论文库' }}</p>
      <el-button v-if="kbs.length" @click="resetFilters">清除筛选</el-button>
      <el-button v-else type="primary" @click="openCreate">新建私人论文库</el-button>
    </div>
    <div v-else class="library-list">
      <article v-for="kb in paged" :key="kb.id" class="library-entry">
        <div class="library-entry-body">
          <span class="library-card-icon" aria-hidden="true"><AppIcon name="book" :size="24" /></span><div class="library-title"><h2><RouterLink :to="'/admin/kbs/' + kb.id">{{ kb.name }}</RouterLink></h2><span class="library-access">{{ !canManage(kb) ? '共享只读' : kb.granted_roles?.length ? '已共享' : '私人' }}</span><StatusBadge v-if="kb.status !== 'enabled'" :status="kb.status" /></div>
          <p v-if="kb.description?.trim()" class="library-description">{{ kb.description }}</p>
          <div class="library-facts">
            <span>{{ kb.doc_count == null ? '—' : formatNumber(kb.doc_count) }} 份文档</span>
            <span>{{ kb.chunk_count == null ? '—' : formatNumber(kb.chunk_count) }} 个片段</span>
            <span v-if="kb.updated_at">更新于 {{ formatDate(kb.updated_at) }}</span>
            <span v-else-if="kb.created_at">创建于 {{ formatDate(kb.created_at) }}</span>
          </div>
        </div>
        <div class="library-entry-actions">
          <RouterLink v-if="canManage(kb)" class="library-link" :to="{ path: '/admin/kbs/' + kb.id, query: { upload: '1' } }"><AppIcon name="upload" :size="16" />上传论文</RouterLink>
          <RouterLink class="library-link" :to="'/admin/kbs/' + kb.id">查看论文</RouterLink>
          <RouterLink v-if="kb.status === 'enabled'" class="library-link is-primary" :to="chatLocation(kb)">进入问答</RouterLink>
          <span v-else class="library-disabled">已停用问答</span>
          <el-dropdown v-if="canManage(kb)" trigger="click" @command="command => cardAction(command, kb)">
            <button type="button" class="library-more" :disabled="busyKb === kb.id" :aria-label="'管理论文库：' + kb.name"><AppIcon name="more" :size="18" /></button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="edit">编辑名称与说明</el-dropdown-item><el-dropdown-item command="grant">共享设置</el-dropdown-item><el-dropdown-item command="status">{{ kb.status === 'enabled' ? '停用问答' : '启用问答' }}</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </div>
      </article>
    </div>
    <div v-if="filtered.length" class="library-pagination"><span>共 {{ filtered.length }} 个论文库</span><el-pagination background layout="prev, pager, next" v-model:current-page="page" :total="filtered.length" :page-size="pageSize" :pager-count="5" /></div>
  </section>

  <el-dialog v-model="editorVisible" :title="editing ? '编辑论文库' : '新建私人论文库'" width="min(520px, calc(100vw - 32px))" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <div v-if="editorError" class="library-error" role="alert">{{ editorError }}</div>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" :disabled="saving" @submit.prevent="save">
      <el-form-item label="论文库名称" prop="name"><el-input v-model="form.name" maxlength="128" placeholder="输入名称" /></el-form-item>
      <el-form-item label="说明（可选）"><el-input v-model="form.description" type="textarea" :rows="3" maxlength="1000" placeholder="研究主题或资料范围" /></el-form-item>
      <p v-if="!editing" class="library-help">默认不向其他角色共享，可在创建后单独设置共享。</p>
    </el-form>
    <template #footer><el-button :disabled="saving" @click="editorVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">{{ editing ? '保存' : '创建并上传论文' }}</el-button></template>
  </el-dialog>
  <el-dialog v-model="grantVisible" title="共享设置" width="min(480px, calc(100vw - 32px))" :close-on-click-modal="!grantSaving" :close-on-press-escape="!grantSaving" :show-close="!grantSaving">
    <h2 class="library-grant-name">{{ grantKb?.name }}</h2>
    <p id="kb-sharing-help" class="library-help">所选角色的所有账号都可读取此库。共享仅授予读取权限；只有所有者可以维护资料，不选则保持私人。</p>
    <div v-if="grantError" class="library-error" role="alert">{{ grantError }}</div>
    <el-checkbox-group v-model="grantRoles" :disabled="grantSaving" aria-label="共享给哪些角色" aria-describedby="kb-sharing-help"><el-checkbox value="user" label="user">普通用户</el-checkbox><el-checkbox value="operator" label="operator">知识库维护员</el-checkbox><el-checkbox value="admin" label="admin">系统管理员</el-checkbox></el-checkbox-group>
    <template #footer><el-button :disabled="grantSaving" @click="grantVisible = false">取消</el-button><el-button type="primary" :loading="grantSaving" @click="submitGrant">保存共享设置</el-button></template>
  </el-dialog>
</div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElButton, ElInput, ElSelect, ElOption, ElSkeleton, ElDropdown, ElDropdownMenu, ElDropdownItem, ElPagination, ElDialog, ElForm, ElFormItem, ElCheckboxGroup, ElCheckbox, ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/input/style/css.mjs'
import 'element-plus/es/components/select/style/css.mjs'
import 'element-plus/es/components/option/style/css.mjs'
import 'element-plus/es/components/skeleton/style/css.mjs'
import 'element-plus/es/components/dropdown/style/css.mjs'
import 'element-plus/es/components/dropdown-menu/style/css.mjs'
import 'element-plus/es/components/dropdown-item/style/css.mjs'
import 'element-plus/es/components/pagination/style/css.mjs'
import 'element-plus/es/components/dialog/style/css.mjs'
import 'element-plus/es/components/form/style/css.mjs'
import 'element-plus/es/components/form-item/style/css.mjs'
import 'element-plus/es/components/checkbox-group/style/css.mjs'
import 'element-plus/es/components/checkbox/style/css.mjs'
import { kbsApi, createKbApi, grantKbApi, patchKbApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { formatDate, formatNumber } from '@/utils/format'

const router = useRouter(), route = useRoute(), user = useUserStore()
const kbs = ref([]), search = ref(''), status = ref(''), page = ref(1), pageSize = 12
const accessFilter = ref(''), viewMode = ref('cards')
function viewKey() { return 'knowledge-list-view-v2-' + (user.user?.id || 'anonymous') }
function restoreViewMode() { try { viewMode.value = window.localStorage.getItem(viewKey()) === 'list' ? 'list' : 'cards' } catch { viewMode.value = 'cards' } }
function setViewMode(value) { if (!['cards', 'list'].includes(value)) return; viewMode.value = value; try { window.localStorage.setItem(viewKey(), value) } catch { /* Optional preference. */ } }
const loading = ref(false), loaded = ref(false), error = ref(''), actionError = ref(''), busyKb = ref(null)
let loadVersion = 0, alive = true
const docTotal = computed(() => kbs.value.reduce((sum, kb) => sum + (Number(kb.doc_count) || 0), 0))
const filtered = computed(() => kbs.value.filter(kb => (!accessFilter.value || (accessFilter.value === 'managed' ? canManage(kb) : !canManage(kb))) && (!status.value || kb.status === status.value) && `${kb.name} ${kb.description || ''}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const paged = computed(() => filtered.value.slice((page.value - 1) * pageSize, page.value * pageSize))
function canManage(kb) {
  if (!kb) return false
  if (typeof kb.can_manage === 'boolean') return kb.can_manage
  return Boolean(user.user?.id != null && Number(kb.owner_id) === Number(user.user.id))
}
function chatLocation(kb) { return { path: '/', query: { kb_id: kb.id } } }
function errorMessage(cause, fallback) { return typeof cause?.response?.data?.detail === 'string' ? cause.response.data.detail : fallback }
async function load() {
  const version = ++loadVersion
  loading.value = true; error.value = ''
  try {
    const result = await kbsApi()
    if (version === loadVersion && alive) { kbs.value = result; loaded.value = true }
  } catch (cause) {
    if (version !== loadVersion || !alive) return
    error.value = errorMessage(cause, '论文库加载失败，请重试。')
    if ([401, 403].includes(cause?.response?.status)) { kbs.value = []; loaded.value = false }
  } finally { if (version === loadVersion && alive) loading.value = false }
}
function resetFilters() { search.value = ''; status.value = ''; accessFilter.value = '' }
watch([search, status, accessFilter], () => { page.value = 1 })
watch(() => filtered.value.length, count => { page.value = Math.min(page.value, Math.max(1, Math.ceil(count / pageSize))) })

const editorVisible = ref(false), editing = ref(null), formRef = ref(), saving = ref(false), editorError = ref('')
const form = ref({ name: '', description: '' })
const rules = { name: [{ validator: (_rule, value, done) => value?.trim() ? done() : done(new Error('请输入论文库名称')), trigger: 'blur' }] }
function openCreate() { editorError.value = ''; editing.value = null; form.value = { name: '', description: '' }; editorVisible.value = true }
function openEdit(kb) {
  if (!canManage(kb)) return
  editorError.value = ''; editing.value = kb.id; form.value = { name: kb.name, description: kb.description || '' }; editorVisible.value = true
}
async function save() {
  if (saving.value) return
  const name = form.value.name.trim()
  if (!name) { editorError.value = '请输入论文库名称'; return }
  if (formRef.value && !await formRef.value.validate().catch(() => false)) return
  saving.value = true; editorError.value = ''
  const id = editing.value, data = { name, description: form.value.description.trim() }
  try {
    if (id != null) {
      if (!canManage(kbs.value.find(kb => kb.id === id))) { editorError.value = '当前账号不能管理此论文库'; return }
      await patchKbApi(id, data)
      if (!alive) return
      editorVisible.value = false; ElMessage.success('论文库已更新'); await load()
    } else {
      const created = await createKbApi({ ...data, role_codes: [] })
      if (!alive) return
      editorVisible.value = false; ElMessage.success('私人论文库已创建')
      await router.push({ path: '/admin/kbs/' + created.id, query: { upload: '1' } })
    }
  } catch (cause) { if (alive) editorError.value = errorMessage(cause, '保存失败，请重试。') }
  finally { if (alive) saving.value = false }
}

const grantVisible = ref(false), grantKb = ref(null), grantRoles = ref([]), grantSaving = ref(false), grantError = ref('')
function openGrant(kb) {
  if (!canManage(kb)) return
  grantError.value = ''; grantKb.value = kb; grantRoles.value = [...(kb.granted_roles || [])]; grantVisible.value = true
}
async function submitGrant() {
  if (grantSaving.value || !canManage(grantKb.value)) return
  grantSaving.value = true; grantError.value = ''
  try {
    await grantKbApi(grantKb.value.id, [...grantRoles.value])
    if (!alive) return
    grantVisible.value = false; ElMessage.success('共享设置已保存'); await load()
  } catch (cause) { if (alive) grantError.value = errorMessage(cause, '共享设置保存失败，请重试。') }
  finally { if (alive) grantSaving.value = false }
}
async function cardAction(command, kb) {
  if (!canManage(kb) || busyKb.value != null) return
  if (command === 'edit') return openEdit(kb)
  if (command === 'grant') return openGrant(kb)
  if (command !== 'status') return
  const nextStatus = kb.status === 'enabled' ? 'disabled' : 'enabled'
  try {
    await ElMessageBox.confirm(nextStatus === 'disabled' ? '停止基于此库发起新问答，已保存论文不会删除。' : '允许有访问权限的用户基于此库提问。', (nextStatus === 'disabled' ? '停用' : '启用') + '「' + kb.name + '」问答？', { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' })
  } catch { return }
  if (!alive || !canManage(kb)) return
  busyKb.value = kb.id; actionError.value = ''
  try { await patchKbApi(kb.id, { status: nextStatus }); if (alive) await load() }
  catch (cause) { if (alive) actionError.value = errorMessage(cause, '状态更新失败，请重试。') }
  finally { if (alive) busyKb.value = null }
}
watch(() => route.query.create, value => {
  if (value === '1') { openCreate(); router.replace({ path: '/admin/kbs', query: {} }) }
}, { immediate: true })
onMounted(() => { restoreViewMode(); load() })
onBeforeUnmount(() => { alive = false; loadVersion++ })
</script>

<style scoped>
.paper-library { min-width: 0; color: var(--color-ink, #172033); font: 14px/1.5 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; }
.library-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.library-heading h1 { font-family: inherit; margin: 0; font-size: 24px; font-weight: 600; }
.library-heading p { margin: 4px 0 0; color: var(--color-secondary, #5D687B); }
.library-actions, .library-entry-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.library-actions :deep(.el-button + .el-button) { margin-left: 0; }
.library-panel { background: var(--color-surface, white); border: 1px solid var(--color-line, #DFE4ED); border-radius: 8px; overflow: hidden; }
.library-toolbar { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; padding: 16px; border-bottom: 1px solid var(--color-line, #DFE4ED); }
.library-toolbar label { display: block; margin-bottom: 4px; color: var(--color-secondary, #5D687B); font-size: 13px; }
.library-search { flex: 1 1 240px; max-width: 560px; min-width: 0; }
.library-status { flex: 0 0 128px; }
.library-entry { display: flex; align-items: center; flex-wrap: wrap; gap: 12px 24px; padding: 16px; border-bottom: 1px solid var(--color-line, #DFE4ED); }
.library-entry:last-child { border-bottom: 0; }
.library-entry-body { flex: 1 1 300px; min-width: 0; }
.library-title { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.library-title h2 { font-family: inherit; min-width: 0; margin: 0; font-size: 16px; font-weight: 600; overflow-wrap: anywhere; }
.library-title a { color: var(--color-ink, #172033); text-decoration: none; }
.library-title a:hover { color: var(--color-accent, #365CF5); text-decoration: underline; }
.library-access { color: var(--color-secondary, #5D687B); font-size: 12px; padding: 0 6px; border: 1px solid var(--color-line, #DFE4ED); border-radius: 6px; }
.library-description { margin: 4px 0; color: var(--color-secondary, #5D687B); overflow-wrap: anywhere; white-space: pre-line; }
.library-facts { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 4px; font-size: 13px; color: var(--color-secondary, #5D687B); }
.library-link, .library-more { display: inline-flex; align-items: center; justify-content: center; gap: 4px; min-height: 34px; padding: 4px 8px; box-sizing: border-box; border: 1px solid var(--color-line, #DFE4ED); border-radius: 6px; color: var(--color-ink, #172033); background: var(--color-surface, white); font: inherit; text-decoration: none; cursor: pointer; }
.library-link.is-primary { color: var(--color-accent, #365CF5); border-color: var(--color-accent, #365CF5); }
.library-link:hover, .library-more:hover { color: var(--color-accent, #365CF5); background: var(--color-page, #F4F6F9); }
.library-more { width: 34px; }
.library-disabled { color: var(--color-secondary, #5D687B); font-size: 13px; }
.library-pagination { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; padding: 12px 16px; border-top: 1px solid var(--color-line, #DFE4ED); color: var(--color-secondary, #5D687B); }
.library-empty { padding: 24px 16px; color: var(--color-secondary, #5D687B); }
.library-empty p { margin: 0 0 12px; }
.library-error { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 8px 12px; margin-bottom: 12px; border: 1px solid var(--color-danger, #C23B4B); border-radius: 6px; color: var(--color-danger, #C23B4B); overflow-wrap: anywhere; }
.library-help { margin: 8px 0 16px; color: var(--color-secondary, #5D687B); font-size: 14px; line-height: 1.6; }
.library-grant-name { margin: 0; font-size: 16px; overflow-wrap: anywhere; }
.paper-library :deep(.el-button) { min-height: 34px; }
.paper-library :deep(.el-input__wrapper), .paper-library :deep(.el-select__wrapper) { min-height: 34px; }
a:focus-visible, button:focus-visible { outline: 2px solid var(--color-accent, #365CF5); outline-offset: 3px; }
@media (max-width: 600px) { .library-entry { gap: 12px; } .library-entry-body { flex-basis: 100%; } .library-entry-actions { gap: 8px; } .library-search { flex-basis: 100%; max-width: none; } .library-status { flex-basis: 128px; } .library-pagination { justify-content: flex-start; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; animation: none !important; } }

.library-eyebrow { display: block; margin-bottom: 6px; color: var(--color-source); font-size: 12px; font-weight: 600; letter-spacing: .08em; }
.library-heading { margin-block: 8px 24px; }
.library-panel { border: 0; background: transparent; overflow: visible; }
.library-toolbar { padding: 16px 20px; background: var(--color-surface); border: 1px solid var(--color-line); border-radius: 12px; margin-bottom: 20px; align-items: end; }
.library-access-filter { flex: 0 0 144px; }
.library-view-switch { display: flex; gap: 4px; margin-left: auto; align-self: center; padding: 4px; border-radius: 9px; background: var(--color-page); }
.library-view-switch button { border: 0; border-radius: 6px; min-height: 32px; display: flex; align-items: center; gap: 6px; padding: 6px 10px; font: inherit; color: var(--color-secondary); background: transparent; cursor: pointer; }
.library-view-switch button[aria-pressed="true"] { color: var(--color-accent); background: white; box-shadow: 0 1px 4px #17203312; }
.library-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); gap: 20px; }
.library-entry { padding: 22px; border: 1px solid var(--color-line); border-radius: 12px; background: var(--color-surface); align-content: start; flex-direction: column; align-items: stretch; gap: 20px; min-width: 0; }
.library-entry:last-child { border-bottom: 1px solid var(--color-line); }
.library-entry-body { flex: 1 1 auto; }
.library-card-icon { display: grid; place-items: center; width: 44px; height: 44px; margin-bottom: 18px; color: var(--color-accent); background: var(--color-accent-soft); border-radius: 12px; }
.library-title { gap: 8px; align-items: start; }
.library-title h2 { flex: 1 1 100%; font-size: 18px; }
.library-access { background: var(--color-page); border: 0; padding: 3px 8px; }
.library-description { margin-block: 12px; line-height: 1.7; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.library-facts { font-size: 12px; gap: 8px 12px; }
.library-entry-actions { padding-top: 16px; border-top: 1px solid var(--color-line); gap: 8px; }
.library-link { border: 0; padding: 6px 0; font-size: 13px; min-height: 36px; }
.library-link.is-primary { margin-left: auto; background: var(--color-accent-soft); color: var(--color-accent); padding-inline: 12px; border-radius: 8px; }
.library-more { border-color: var(--color-control-border); border-radius: 8px; }
.library-pagination { border: 0; padding-inline: 0; margin-top: 12px; }
.library-empty { background: white; border: 1px dashed var(--color-control-border); border-radius: 12px; padding: 48px 24px; text-align: center; }
.view-list .library-list { grid-template-columns: 1fr; gap: 12px; }
.view-list .library-entry { flex-direction: row; align-items: center; }
.view-list .library-card-icon { display: none; }
.view-list .library-entry-actions { border-top: 0; padding-top: 0; }
.view-list .library-entry-body { flex: 1; }
@media (max-width: 767px) { .library-toolbar { padding: 16px; } .library-access-filter { flex: 1 1 140px; } .library-status { flex: 1 1 128px; } .library-view-switch { margin: 0; } .view-list .library-entry { flex-direction: column; align-items: stretch; } .library-entry { padding: 20px; } .library-entry-actions .library-link, .library-more, .library-view-switch button { min-height: 44px; } }
</style>