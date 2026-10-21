<template>
<div class="page paper-detail">
  <nav class="paper-breadcrumb" aria-label="面包屑"><RouterLink class="paper-back" to="/admin/kbs">知识库</RouterLink><AppIcon name="chevron" :size="14" /><span>{{ kb?.name || '资料详情' }}</span></nav>
  <header class="paper-heading">
    <div class="paper-heading-text"><h1>{{ kb?.name || '论文库' }}</h1><p v-if="kb?.description?.trim()">{{ kb.description }}</p></div>
    <div class="paper-actions">
      <el-button :disabled="!kb || kb.status !== 'enabled' || Boolean(error)" @click="ask"><AppIcon name="chat" :size="16" />进入问答</el-button>
      <el-button v-if="canManage" type="primary" :disabled="!kb || loading || Boolean(error)" :aria-describedby="error ? 'document-load-error' : undefined" @click="openUpload"><AppIcon name="upload" :size="16" />上传论文</el-button>
    </div>
  </header>
  <p v-if="kb" class="paper-permission"><AppIcon name="shield" :size="15" />{{ canManage ? '可管理此知识库' : '共享只读 · 可阅读和提问' }}</p>
  <div v-if="kb" class="paper-summary" aria-label="论文库资料统计">
    <span>{{ stats.doc_count == null ? '—' : formatNumber(stats.doc_count) }} 份文档</span>
    <span>{{ stats.chunk_count == null ? '—' : formatNumber(stats.chunk_count) }} 个片段</span>
    <span v-if="stats.last_upload_at">最近上传 {{ formatDate(stats.last_upload_at) }}</span>
    <span v-if="polling" role="status">待解析 / 解析中 {{ stats.pending_docs }} 份</span>
    <button v-if="stats.failed_docs" type="button" class="paper-text-button is-danger" @click="status = 'failed'">{{ stats.failed_docs }} 份解析失败</button>
    <span v-if="kb.status === 'disabled'">问答已停用，已保存论文仍保留</span>
  </div>
  <div v-if="error" id="document-load-error" class="paper-error" role="alert"><span>{{ error }}</span><el-button text @click="load">重新加载</el-button></div>
  <div v-if="operationError" class="paper-error" role="alert">{{ operationError }}</div>

  <section class="papers-panel" aria-label="论文与资料" :aria-busy="loading">
    <div class="paper-toolbar">
      <div class="paper-search"><label for="doc-search">搜索文件名</label><el-input id="doc-search" v-model="search" clearable placeholder="论文文件名"><template #prefix><AppIcon name="search" :size="16" /></template></el-input></div>
      <div class="paper-filter"><label for="doc-status">解析状态</label><el-select id="doc-status" v-model="status" placeholder="全部状态"><el-option label="全部" value="" /><el-option label="已入库" value="success" /><el-option label="待解析" value="pending" /><el-option label="解析中" value="parsing" /><el-option label="解析失败" value="failed" /></el-select></div>
      <el-button :loading="loading" @click="load"><AppIcon name="refresh" :size="16" />刷新</el-button>
    </div>
    <div v-if="loading && !kb" class="paper-empty" role="status"><p>正在加载论文…</p><el-skeleton :rows="4" /></div>
    <div v-else-if="!docs.length && error" class="paper-empty"><p>{{ accessStatus === 403 ? '无权访问此论文库' : accessStatus === 404 ? '论文库不存在' : '资料未能加载' }}</p><RouterLink v-if="accessStatus" to="/admin/kbs" class="paper-back">返回论文库</RouterLink><el-button v-else @click="load">重试</el-button></div>
    <div v-else-if="!docs.length" class="paper-empty"><p>{{ search || status ? '没有匹配的论文' : '还没有上传论文' }}</p><el-button v-if="search || status" @click="resetFilters">清除筛选</el-button><el-button v-else-if="canManage" type="primary" @click="openUpload">上传论文</el-button><p v-else class="paper-help">此库尚未共享可阅读的论文。</p></div>
    <div v-else class="papers-table-scroll" role="region" tabindex="0" aria-label="文档资料表，可横向滚动"><table class="papers-list"><thead><tr><th scope="col">资料名称</th><th scope="col">状态与版本</th><th scope="col">操作</th></tr></thead><tbody>
      <tr v-for="doc in docs" :key="doc.id" class="paper-row">
        <td class="paper-row-content">
          <h2><button type="button" class="paper-title" :disabled="Boolean(error)" @click="readOriginal(doc)">{{ documentTitle(doc) }}</button></h2>
          <p class="paper-source"><AppIcon name="file" :size="14" /><span v-if="documentTitle(doc) !== doc.filename">{{ doc.filename }}</span><span>{{ (doc.file_type || '').toUpperCase() }}</span><span>{{ formatBytes(doc.file_size) }}</span><span v-if="doc.page_count > 0">{{ doc.page_count }} 页</span></p>
          </td><td class="paper-row-meta"><div class="paper-facts"><StatusBadge :status="doc.status" :label="doc.status === 'success' ? '已入库' : ''" /><span>{{ doc.chunk_count == null ? '—' : formatNumber(doc.chunk_count) }} 个片段</span><span>{{ doc.version?.updated_at ? '更新于' : '上传于' }} {{ formatDate(doc.version?.updated_at || doc.created_at) }}</span><button v-if="doc.version" type="button" class="paper-text-button" :disabled="Boolean(error)" @click="openVersion(doc)">版本 {{ doc.version.version_label }} · {{ publicationLabel(doc.version.publication_state) }}</button></div>
          <p v-if="doc.error_message" class="paper-parse-error" role="status">{{ doc.error_message }}</p>
        </td><td class="paper-row-actions">
          <el-button text type="primary" :disabled="Boolean(error)" @click="readOriginal(doc)">阅读原文</el-button>
          <el-button text :disabled="doc.status !== 'success' || Boolean(error)" @click="preview(doc)">查看片段</el-button>
          <el-dropdown v-if="canWriteDoc(doc)" trigger="click" @command="command => documentAction(command, doc)">
            <button type="button" class="paper-more" :disabled="isBusy(doc) || Boolean(error)" :aria-label="'管理论文：' + doc.filename"><AppIcon name="more" :size="18" /></button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="reparse" :disabled="isBusy(doc)">重新解析</el-dropdown-item><el-dropdown-item command="remove" :disabled="isBusy(doc)">{{ kb?.versioning_enabled ? '归档论文' : '删除论文' }}</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
        </td></tr></tbody></table></div>
    <div v-if="kb" class="paper-pagination">
      <div class="paper-page-size"><span>{{ error ? '数量暂未确认' : '共 ' + total + ' 份' }}</span><label for="doc-page-size">每页</label><el-select id="doc-page-size" v-model="size"><el-option v-for="n in [20, 50, 100]" :key="n" :label="n + ' 份'" :value="n" /></el-select></div>
      <el-pagination :disabled="loading || Boolean(error)" background layout="prev, pager, next" v-model:current-page="page" :page-size="size" :total="total" :pager-count="5" />
    </div>
  </section>

  <el-dialog v-model="uploadVisible" title="上传论文" width="min(600px, calc(100vw - 32px))" :close-on-click-modal="!uploading" :close-on-press-escape="!uploading" :show-close="!uploading">
    <p class="paper-help">上传到：<b>{{ kb?.name }}</b></p>
    <el-upload v-model:file-list="uploadFiles" drag multiple :auto-upload="false" :limit="10" :disabled="uploading" :on-exceed="onExceed" :on-change="validateFile" accept=".pdf,.docx,.txt,.md,.markdown,.xlsx,.csv,.html,.htm,.png,.jpg,.jpeg,.bmp" aria-label="选择要上传的论文文件">
      <div class="paper-upload-zone"><AppIcon name="upload" :size="24" /><p>选择论文文件或拖放到这里</p><small>每批最多 10 个 · 每个不超过 50 MB</small><small>PDF、Word、文本、Markdown、表格、HTML、图片</small></div>
    </el-upload>
    <p class="paper-help">扫描件和图片需要可用的 OCR；解析结果以资料列表的实际状态为准。</p>
    <div v-if="uploading || uploadDone" class="paper-upload-progress" aria-live="polite"><el-progress :percentage="uploadPercent" :status="uploadDone ? (uploadResult.failed ? 'exception' : 'success') : undefined" /><p>{{ uploading ? '正在上传，请等待当前批次完成。' : '成功 ' + uploadResult.success + ' 份（其中 ' + uploadResult.existing + ' 份已存在），失败 ' + uploadResult.failed + ' 份。' }}</p></div>
    <ul v-if="Object.keys(uploadErrors).length" class="paper-upload-errors" aria-label="上传失败原因"><template v-for="file in uploadFiles" :key="file.uid"><li v-if="uploadErrors[file.uid]"><b>{{ file.name }}</b><span>{{ uploadErrors[file.uid] }}</span></li></template></ul>
    <template #footer><el-button :disabled="uploading" @click="closeUpload">{{ uploadDone ? '完成' : '取消' }}</el-button><el-button type="primary" :loading="uploading" :disabled="!pendingUploads || !canManage" @click="startUpload">{{ uploadDone && pendingUploads ? '重试失败文件' : '上传论文' }}（{{ pendingUploads }}）</el-button></template>
  </el-dialog>

  <el-drawer v-model="previewVisible" :title="previewDoc?.filename || '论文片段'" size="min(720px, 100vw)">
    <div class="paper-preview-heading"><span>解析文本 · {{ previewLoading || previewError ? '—' : previewChunks.length }} 个片段</span><el-button :disabled="!previewDoc || Boolean(error)" @click="readOriginal(previewDoc)">阅读原文件</el-button></div>
    <p class="paper-help">以下是已解析的文本，非原文件版式。页码仅显示解析结果提供的页码。</p>
    <div v-if="previewLoading" class="paper-empty" role="status"><p>正在读取片段…</p><el-skeleton :rows="5" /></div>
    <div v-else-if="previewError" class="paper-error" role="alert"><span>{{ previewError }}</span><el-button text @click="preview(previewDoc)">重试</el-button></div>
    <ol v-else-if="previewChunks.length" class="paper-chunks"><li v-for="chunk in previewChunks" :key="chunk.id"><div class="paper-chunk-heading"><span>片段 {{ chunk.seq_no }}</span><button v-if="chunk.page_no > 0" type="button" class="paper-text-button" @click="readOriginal(previewDoc, chunk.page_no)">原文第 {{ chunk.page_no }} 页</button></div><p>{{ chunk.content }}</p></li></ol>
    <p v-else class="paper-help">没有可查看的片段，请核对论文解析状态。</p>
  </el-drawer>

  <el-dialog v-model="readerVisible" :title="readerDoc?.filename || '论文原文'" width="min(1040px, calc(100vw - 24px))" destroy-on-close>
    <div v-if="readerLoading" class="paper-empty" role="status">正在读取原文件…</div>
    <div v-else-if="readerError" class="paper-error" role="alert"><span>{{ readerError }}</span><el-button text @click="readOriginal(readerDoc, readerPage)">重试</el-button></div>
    <template v-else-if="readerUrl"><iframe v-if="readerIsPdf" class="paper-pdf" :src="readerSource" :title="readerDoc.filename + ' 原文阅读'" referrerpolicy="no-referrer" /><p v-else class="paper-help">此格式请下载原文件后阅读。</p></template>
    <template #footer><div class="paper-reader-actions"><a v-if="readerUrl" :href="readerUrl" :download="readerDoc.filename" class="paper-download">下载原文件</a><el-button @click="closeReader">关闭</el-button></div></template>
  </el-dialog>
  <VersionManager v-if="versionVisible && versionDoc" :key="kbId + ':' + versionDoc.version.version_id" v-model="versionVisible" :version-id="versionDoc.version.version_id" @changed="load" />
</div>
</template>
<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount, defineAsyncComponent } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave, onBeforeRouteUpdate } from 'vue-router'
import { ElButton, ElInput, ElSelect, ElOption, ElDropdown, ElDropdownMenu, ElDropdownItem, ElPagination, ElDialog, ElUpload, ElProgress, ElDrawer, ElSkeleton, ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/input/style/css.mjs'
import 'element-plus/es/components/select/style/css.mjs'
import 'element-plus/es/components/option/style/css.mjs'
import 'element-plus/es/components/dropdown/style/css.mjs'
import 'element-plus/es/components/dropdown-menu/style/css.mjs'
import 'element-plus/es/components/dropdown-item/style/css.mjs'
import 'element-plus/es/components/pagination/style/css.mjs'
import 'element-plus/es/components/dialog/style/css.mjs'
import 'element-plus/es/components/upload/style/css.mjs'
import 'element-plus/es/components/progress/style/css.mjs'
import 'element-plus/es/components/drawer/style/css.mjs'
import 'element-plus/es/components/skeleton/style/css.mjs'
import { kbApi, kbStatsApi, docsApi, uploadDocApi, deleteDocApi, reparseDocApi, chunksApi, documentFileApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { formatBytes, formatDate, formatNumber } from '@/utils/format'
const VersionManager = defineAsyncComponent(() => import('@/components/VersionManager.vue'))

const route = useRoute(), router = useRouter(), user = useUserStore()
const kbId = computed(() => Number(route.params.id)), kb = ref(null), stats = ref({}), docs = ref([])
const loading = ref(false), error = ref(''), accessStatus = ref(null), operationError = ref('')
const search = ref(''), status = ref(''), page = ref(1), size = ref(20), total = ref(0), polling = ref(false), busyDocs = ref(new Set())
let pollTimer, searchTimer, requestVersion = 0, alive = true, scopeVersion = 0, resettingScope = false
const canManage = computed(() => {
  if (!kb.value) return false
  if (typeof kb.value.can_manage === 'boolean') return kb.value.can_manage
  return Boolean(user.user?.id != null && Number(kb.value.owner_id) === Number(user.user.id))
})
function currentDoc(doc) { return Boolean(doc && kb.value && Number(kb.value.id) === kbId.value && Number(doc.kb_id) === kbId.value) }
function canWriteDoc(doc) { return canManage.value && currentDoc(doc) && doc.version?.can_manage !== false }
function isBusy(doc) { return busyDocs.value.has(doc.id) || ['pending', 'parsing'].includes(doc.status) }
function documentTitle(doc) { return doc.version?.series_title?.trim() || doc.filename }
function publicationLabel(state) { return ({ draft: '草稿', published: '已发布', withdrawn: '已撤回', archived: '已归档' })[state] || state }
function messageFor(cause, fallback) { return typeof cause?.response?.data?.detail === 'string' ? cause.response.data.detail : fallback }
function ask() { if (kb.value?.status === 'enabled' && Number(kb.value.id) === kbId.value && !error.value) router.push({ path: '/', query: { kb_id: kbId.value } }) }
function schedulePoll() {
  clearTimeout(pollTimer)
  polling.value = Number(stats.value.pending_docs) > 0
  if (polling.value && alive) pollTimer = setTimeout(() => { if (document.visibilityState === 'visible') load(true); else schedulePoll() }, 3000)
}
async function load(quiet = false) {
  quiet = quiet === true
  const version = ++requestVersion, id = kbId.value
  if (!quiet) loading.value = true
  error.value = ''; accessStatus.value = null
  if (!Number.isSafeInteger(id) || id < 1) {
    error.value = '论文库编号无效，请返回论文库列表。'; accessStatus.value = 404
    kb.value = null; stats.value = {}; docs.value = []; total.value = 0
    previewVersion++; previewVisible.value = false; previewChunks.value = []
    clearTimeout(pollTimer); polling.value = false; loading.value = false
    return
  }
  try {
    const [k, s, d] = await Promise.all([kbApi(id), kbStatsApi(id), docsApi(id, page.value, size.value, { q: search.value.trim() || undefined, status: status.value || undefined })])
    if (version !== requestVersion || !alive || id !== kbId.value) return
    kb.value = { ...k, versioning_enabled: d.versioning_enabled === true }; stats.value = s; docs.value = d.items; total.value = d.total
    if (page.value > 1 && !d.items.length && d.total < (page.value - 1) * size.value + 1) page.value = Math.max(1, Math.ceil(d.total / size.value))
    schedulePoll()
  } catch (cause) {
    if (version !== requestVersion || !alive || id !== kbId.value) return
    const status = cause?.response?.status
    accessStatus.value = status === 401 || status === 403 ? 403 : status === 404 ? 404 : null
    error.value = status === 401 || status === 403 ? '无权读取此论文库，请返回列表确认访问权限。'
      : status === 404 ? '此论文库不存在或已被移除，请返回论文库列表。'
      : typeof cause?.response?.data?.detail === 'string' ? cause.response.data.detail : '论文加载失败，请检查连接后重试。'
    clearTimeout(pollTimer); polling.value = false
    // A revoked/deleted scope must not retain previously readable content.
    if (accessStatus.value) {
      kb.value = null; stats.value = {}; docs.value = []; total.value = 0
      previewVersion++; previewVisible.value = false; previewChunks.value = []
    }
  } finally { if (version === requestVersion && alive) loading.value = false }
}
function resetFilters() { search.value = ''; status.value = '' }
watch(search, () => {
  if (resettingScope) return
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { if (page.value !== 1) page.value = 1; else load() }, 300)
}, { flush: 'sync' })
watch([status, size], () => {
  if (resettingScope) return
  clearTimeout(searchTimer)
  if (page.value !== 1) page.value = 1; else load()
}, { flush: 'sync' })
watch(page, () => { if (!resettingScope) { clearTimeout(searchTimer); load() } }, { flush: 'sync' })

const uploadVisible = ref(false), uploadFiles = ref([]), uploading = ref(false), uploadDone = ref(false), uploadPercent = ref(0)
const uploadErrors = ref({}), uploadResult = ref({ success: 0, existing: 0, failed: 0 })
const pendingUploads = computed(() => uploadFiles.value.filter(file => file.status !== 'success').length)
function openUpload() { if (canManage.value && kb.value && !loading.value && !error.value) uploadVisible.value = true }
function onExceed() { ElMessage.warning('每批最多选择 10 个文件，请分批上传。') }
function validateFile(file) {
  const supported = /\.(pdf|docx|txt|md|markdown|xlsx|csv|html|htm|png|jpg|jpeg|bmp)$/i.test(file.name || '')
  if (!supported || !file.size || file.size > 50 * 1024 * 1024) {
    uploadFiles.value = uploadFiles.value.filter(item => item.uid !== file.uid)
    ElMessage.warning(!supported ? '不支持此文件格式' : !file.size ? '不能上传空文件' : '文件超过 50 MB 限制')
  }
  uploadDone.value = false
}
function clearUpload() { uploadFiles.value = []; uploadDone.value = false; uploadPercent.value = 0; uploadErrors.value = {}; uploadResult.value = { success: 0, existing: 0, failed: 0 } }
function closeUpload() { if (uploading.value) return; uploadVisible.value = false; clearUpload() }
watch(uploadVisible, visible => { if (!visible && !uploading.value) clearUpload() }, { flush: 'sync' })
async function startUpload() {
  if (uploading.value || !kb.value || error.value || loading.value) return
  if (!canManage.value) return
  const files = uploadFiles.value.filter(file => file.status !== 'success')
  if (!files.length || files.length > 10) return
  const targetKbId = kbId.value, scope = scopeVersion
  uploading.value = true; uploadDone.value = false; uploadErrors.value = {}; uploadResult.value = { success: 0, existing: 0, failed: 0 }; uploadPercent.value = 0
  try {
    for (let i = 0; i < files.length; i++) {
      if (!alive || scope !== scopeVersion || targetKbId !== kbId.value || !canManage.value || error.value) return
      const file = files[i]
      file.status = 'uploading'
      try {
        if (!file.raw || !file.size || file.size > 50 * 1024 * 1024) throw new Error('invalid upload')
        const data = new FormData(); data.append('file', file.raw)
        const result = await uploadDocApi(targetKbId, data)
        if (!alive || scope !== scopeVersion || targetKbId !== kbId.value) return
        file.status = 'success'; uploadResult.value.success++
        if (result?.is_new === false) uploadResult.value.existing++
      } catch (cause) {
        if (!alive || scope !== scopeVersion || targetKbId !== kbId.value) return
        file.status = 'fail'; uploadResult.value.failed++
        uploadErrors.value[file.uid] = messageFor(cause, '上传失败，请检查文件格式、大小或连接后重试。')
      }
      uploadPercent.value = Math.round((i + 1) / files.length * 100)
    }
    uploadDone.value = true
    if (uploadResult.value.success) await load()
  } finally { if (alive && scope === scopeVersion) uploading.value = false }
}

let previewVersion = 0
const previewVisible = ref(false), previewDoc = ref(null), previewChunks = ref([]), previewLoading = ref(false), previewError = ref('')
async function preview(doc) {
  if (!currentDoc(doc) || doc.status !== 'success' || error.value) return
  const version = ++previewVersion, targetKbId = kbId.value
  previewDoc.value = doc; previewChunks.value = []; previewError.value = ''; previewVisible.value = true; previewLoading.value = true
  try {
    const chunks = await chunksApi(doc.id)
    if (version === previewVersion && alive && targetKbId === kbId.value) previewChunks.value = chunks
  } catch (cause) {
    if (version === previewVersion && alive && targetKbId === kbId.value) previewError.value = messageFor(cause, '片段读取失败，请重试。')
  } finally { if (version === previewVersion && alive) previewLoading.value = false }
}
watch(previewVisible, visible => { if (!visible) { previewVersion++; previewDoc.value = null; previewChunks.value = []; previewLoading.value = false; previewError.value = '' } }, { flush: 'sync' })

const readerVisible = ref(false), readerDoc = ref(null), readerUrl = ref(''), readerMime = ref(''), readerPage = ref(1), readerLoading = ref(false), readerError = ref('')
let readerVersion = 0
const readerIsPdf = computed(() => readerMime.value === 'application/pdf')
const readerSource = computed(() => readerUrl.value ? `${readerUrl.value}#page=${readerPage.value}` : '')
function releaseReaderUrl() { if (readerUrl.value) URL.revokeObjectURL(readerUrl.value); readerUrl.value = ''; readerMime.value = '' }
function clearReader() { readerVersion++; releaseReaderUrl(); readerDoc.value = null; readerLoading.value = false; readerError.value = ''; readerPage.value = 1 }
function closeReader() { readerVisible.value = false; clearReader() }
watch(readerVisible, visible => { if (!visible) clearReader() }, { flush: 'sync' })
async function fileErrorMessage(cause) {
  const data = cause?.response?.data
  if (data instanceof Blob) {
    try { const detail = JSON.parse(await data.text()).detail; if (typeof detail === 'string') return detail } catch { /* Non-JSON file errors use the fallback below. */ }
  }
  return messageFor(cause, '原文件读取失败，请检查访问权限或连接后重试。')
}
async function readOriginal(doc, pageNumber = 1) {
  if (!currentDoc(doc) || error.value) return
  const version = ++readerVersion, targetKbId = kbId.value
  releaseReaderUrl(); readerDoc.value = doc; readerVisible.value = true; readerLoading.value = true; readerError.value = ''
  readerPage.value = Number.isSafeInteger(Number(pageNumber)) && Number(pageNumber) > 0 ? Number(pageNumber) : 1
  try {
    const blob = await documentFileApi(targetKbId, doc.id)
    if (version !== readerVersion || !alive || targetKbId !== kbId.value || !readerVisible.value) return
    if (!(blob instanceof Blob) || !blob.size) throw new Error('The file response is empty or is not a Blob')
    // Only the server's actual PDF MIME is embedded. HTML and other formats are download-only.
    readerMime.value = blob.type.split(';')[0].toLowerCase(); readerUrl.value = URL.createObjectURL(blob)
  } catch (cause) {
    const message = await fileErrorMessage(cause)
    if (version === readerVersion && alive && targetKbId === kbId.value) readerError.value = message
  } finally { if (version === readerVersion && alive) readerLoading.value = false }
}

const versionVisible = ref(false), versionDoc = ref(null)
function openVersion(doc) { if (currentDoc(doc) && doc.version && !error.value) { versionDoc.value = doc; versionVisible.value = true } }
function documentAction(command, doc) { if (command === 'reparse') return reparse(doc); if (command === 'remove') return remove(doc) }
async function reparse(doc) {
  if (!canWriteDoc(doc) || isBusy(doc) || error.value) return
  const scope = scopeVersion, targetKbId = kbId.value
  try { await ElMessageBox.confirm('重新解析系统已保存的原文件，不会读取本机修改后的文件。历史问答引用快照保留。', '重新解析「' + doc.filename + '」？', { confirmButtonText: '重新解析', cancelButtonText: '取消' }) } catch { return }
  if (!alive || scope !== scopeVersion || !canWriteDoc(doc) || isBusy(doc) || error.value) return
  busyDocs.value.add(doc.id); operationError.value = ''
  try {
    const result = await reparseDocApi(targetKbId, doc.id)
    if (!alive || scope !== scopeVersion) return
    ElMessage.success(result?.message || '已提交解析请求'); await load()
  } catch (cause) { if (alive && scope === scopeVersion) operationError.value = messageFor(cause, '重新解析失败，请重试。') }
  finally { if (scope === scopeVersion) busyDocs.value.delete(doc.id) }
}
async function remove(doc) {
  if (!canWriteDoc(doc) || isBusy(doc) || error.value) return
  const scope = scopeVersion, targetKbId = kbId.value, archived = kb.value.versioning_enabled
  try { await ElMessageBox.confirm(archived ? '归档后不再参与新问答，原文件与历史版本保留。' : '将删除原文件、片段和解析任务，历史问答引用快照保留。删除后无法在界面恢复。', (archived ? '归档' : '删除') + '「' + doc.filename + '」？', { type: 'warning', confirmButtonText: archived ? '确认归档' : '确认删除', cancelButtonText: '保留论文' }) } catch { return }
  if (!alive || scope !== scopeVersion || !canWriteDoc(doc) || isBusy(doc) || error.value) return
  busyDocs.value.add(doc.id); operationError.value = ''
  try {
    const result = await deleteDocApi(targetKbId, doc.id)
    if (!alive || scope !== scopeVersion) return
    if (readerDoc.value?.id === doc.id) closeReader()
    if (previewDoc.value?.id === doc.id) previewVisible.value = false
    if (versionDoc.value?.id === doc.id) { versionVisible.value = false; versionDoc.value = null }
    ElMessage.success(result?.message || (archived ? '论文已归档' : '论文已删除')); await load()
  } catch (cause) { if (alive && scope === scopeVersion) operationError.value = messageFor(cause, '操作失败，请重试。') }
  finally { if (scope === scopeVersion) busyDocs.value.delete(doc.id) }
}
function guardUpload() { if (uploading.value) { ElMessage.warning('论文正在上传，请等待当前批次完成后再离开。'); return false } return true }
onBeforeRouteLeave(guardUpload); onBeforeRouteUpdate(guardUpload)
watch(accessStatus, denied => {
  if (denied) { closeReader(); versionVisible.value = false; versionDoc.value = null; closeUpload() }
}, { flush: 'sync' })
watch(kbId, () => {
  // Clear the old scope synchronously, before a new request or render can expose it.
  resettingScope = true; scopeVersion++; requestVersion++; previewVersion++
  clearTimeout(pollTimer); clearTimeout(searchTimer); polling.value = false
  kb.value = null; stats.value = {}; docs.value = []; total.value = 0; error.value = ''; accessStatus.value = null; operationError.value = ''
  previewVisible.value = false; previewDoc.value = null; previewChunks.value = []; previewError.value = ''; previewLoading.value = false
  busyDocs.value = new Set(); uploading.value = false; closeUpload(); closeReader(); versionVisible.value = false; versionDoc.value = null
  page.value = 1; search.value = ''; status.value = ''; resettingScope = false
  load()
}, { flush: 'sync' })
watch([() => route.query.upload, () => kb.value?.id, loading, error], ([intent, id, busy, failure]) => {
  if (intent !== '1' || id !== kbId.value || busy || failure) return
  if (canManage.value) openUpload(); else ElMessage.warning('只有所有者或管理员可以上传论文。')
  const query = { ...route.query }; delete query.upload
  router.replace({ path: route.path, query })
})
onMounted(() => load())
onBeforeUnmount(() => { alive = false; scopeVersion++; requestVersion++; previewVersion++; clearReader(); clearTimeout(pollTimer); clearTimeout(searchTimer) })
</script>

<style scoped>
.paper-detail { min-width: 0; color: var(--color-ink, #172033); font: 14px/1.5 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; }
.paper-back { display: inline-flex; align-items: center; gap: 4px; margin-bottom: 12px; color: var(--color-secondary, #5D687B); text-decoration: none; }
.paper-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
.paper-heading-text { flex: 1 1 240px; min-width: 0; }
.paper-heading h1 { font-family: inherit; margin: 0; font-size: 24px; font-weight: 600; overflow-wrap: anywhere; }
.paper-heading p { margin: 4px 0 0; color: var(--color-secondary, #5D687B); white-space: pre-line; overflow-wrap: anywhere; }
.paper-actions, .paper-summary, .paper-facts, .paper-row-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; }
.paper-actions :deep(.el-button + .el-button), .paper-row-actions :deep(.el-button + .el-button) { margin-left: 0; }
.paper-summary { margin-bottom: 16px; color: var(--color-secondary, #5D687B); font-size: 13px; }
.papers-panel { min-width: 0; overflow: hidden; border: 1px solid var(--color-line, #DFE4ED); border-radius: 8px; background: var(--color-surface, white); }
.paper-toolbar { display: flex; align-items: end; flex-wrap: wrap; gap: 12px; padding: 16px; border-bottom: 1px solid var(--color-line, #DFE4ED); }
.paper-toolbar label { display: block; margin-bottom: 4px; color: var(--color-secondary, #5D687B); font-size: 13px; }
.paper-search { flex: 1 1 200px; min-width: 0; max-width: 560px; }
.paper-filter { flex: 0 0 144px; }
.papers-list { list-style: none; padding: 0; margin: 0; }
.paper-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; padding: 16px; border-bottom: 1px solid var(--color-line, #DFE4ED); }
.paper-row:last-child { border-bottom: 0; }
.paper-row-content { flex: 1 1 340px; min-width: 0; }
.paper-row h2 { font-family: inherit; margin: 0; font-size: 16px; }
.paper-title { display: inline; padding: 0; text-align: left; font: inherit; font-weight: 600; color: var(--color-ink, #172033); border: 0; background: transparent; cursor: pointer; overflow-wrap: anywhere; }
.paper-title:hover { color: var(--color-accent, #365CF5); text-decoration: underline; }
.paper-source { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 8px; margin: 4px 0; color: var(--color-secondary, #5D687B); font-size: 13px; }
.paper-source span:first-of-type { overflow-wrap: anywhere; }
.paper-facts { color: var(--color-secondary, #5D687B); font-size: 13px; }
.paper-row-actions { gap: 4px; }
.paper-row-actions :deep(.el-button) { padding: 6px 8px; }
.paper-more { display: inline-flex; align-items: center; justify-content: center; width: 34px; min-height: 34px; border: 1px solid var(--color-line, #DFE4ED); border-radius: 6px; background: var(--color-surface, white); color: var(--color-secondary, #5D687B); cursor: pointer; }
.paper-text-button { border: 0; background: transparent; color: var(--color-accent, #365CF5); font: inherit; padding: 2px 0; text-align: left; cursor: pointer; }
.paper-text-button:hover { text-decoration: underline; }
.paper-parse-error { margin: 8px 0 0; font-size: 13px; color: var(--color-danger, #C23B4B); white-space: pre-wrap; overflow-wrap: anywhere; }
.paper-error { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 8px 12px; margin-bottom: 12px; border: 1px solid var(--color-danger, #C23B4B); border-radius: 6px; color: var(--color-danger, #C23B4B); overflow-wrap: anywhere; }
.is-danger { color: var(--color-danger, #C23B4B); }
.paper-pagination, .paper-page-size { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; color: var(--color-secondary, #5D687B); }
.paper-pagination { justify-content: space-between; padding: 12px 16px; border-top: 1px solid var(--color-line, #DFE4ED); }
.paper-page-size :deep(.el-select) { width: 100px; }
.paper-empty { padding: 24px 16px; color: var(--color-secondary, #5D687B); }
.paper-empty p { margin: 0 0 12px; }
.paper-help { margin: 8px 0 12px; color: var(--color-secondary, #5D687B); font-size: 14px; line-height: 1.6; overflow-wrap: anywhere; }
.paper-upload-zone { padding: 8px 0; }
.paper-upload-zone p { margin: 8px 0; font-size: 16px; }
.paper-upload-zone small { display: block; font-size: 13px; color: var(--color-secondary, #5D687B); }
.paper-upload-progress { margin-top: 16px; }
.paper-upload-errors { list-style: none; padding: 0; margin: 12px 0 0; }
.paper-upload-errors li { display: grid; gap: 4px; padding: 8px 0; border-top: 1px solid var(--color-line, #DFE4ED); color: var(--color-danger, #C23B4B); overflow-wrap: anywhere; }
.paper-preview-heading, .paper-chunk-heading { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; color: var(--color-secondary, #5D687B); font-size: 13px; }
.paper-chunks { list-style: none; margin: 0; padding: 0; }
.paper-chunks > li { padding: 16px 0; border-top: 1px solid var(--color-line, #DFE4ED); }
.paper-chunks p { margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 16px; line-height: 1.75; color: var(--color-ink, #172033); }
.paper-pdf { display: block; width: 100%; height: clamp(320px, 68vh, 900px); border: 1px solid var(--color-line, #DFE4ED); border-radius: 6px; background: var(--color-page, #F4F6F9); }
.paper-reader-actions { display: flex; justify-content: flex-end; align-items: center; flex-wrap: wrap; gap: 12px; }
.paper-download { display: inline-flex; align-items: center; min-height: 34px; color: var(--color-accent, #365CF5); text-underline-offset: 3px; }
.paper-detail :deep(.el-button) { min-height: 34px; }
.paper-detail :deep(.el-input__wrapper), .paper-detail :deep(.el-select__wrapper) { min-height: 34px; }
a:focus-visible, button:focus-visible { outline: 2px solid var(--color-accent, #365CF5); outline-offset: 3px; }
button:disabled { cursor: not-allowed; opacity: .6; }
@media (max-width: 600px) { .paper-search { flex-basis: 100%; max-width: none; } .paper-row-content { flex-basis: 100%; } .paper-row-actions { gap: 8px; } .paper-pagination { justify-content: flex-start; } .paper-heading h1 { font-size: 22px; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; animation: none !important; } }

.paper-breadcrumb { display: flex; align-items: center; gap: 8px; color: var(--color-secondary); margin-bottom: 20px; font-size: 13px; min-width: 0; }
.paper-breadcrumb > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.paper-breadcrumb .paper-back { margin: 0; }
.paper-permission { display: flex; gap: 6px; align-items: center; color: var(--color-source); font-size: 13px; }
.paper-heading { margin-bottom: 12px; }
.paper-summary { padding: 16px 20px; background: white; border: 1px solid var(--color-line); border-radius: 12px; margin-block: 20px; gap: 12px 24px; }
.papers-panel { border-radius: 12px; }
.paper-toolbar { padding: 20px; }
.papers-table-scroll { max-width: 100%; overflow-x: auto; }
.papers-list { width: 100%; min-width: 800px; border-collapse: collapse; margin: 0; }
.papers-list thead { background: var(--color-page); }
.papers-list th { text-align: left; padding: 12px 20px; font-weight: 600; font-size: 13px; color: var(--color-secondary); border-bottom: 1px solid var(--color-line); }
.papers-list .paper-row { display: table-row; }
.papers-list td { padding: 20px; vertical-align: top; border-bottom: 1px solid var(--color-line); }
.papers-list tr:last-child td { border-bottom: 0; }
.paper-row-content { width: 46%; }
.paper-row-meta { width: 32%; }
.paper-facts { display: flex; align-items: start; flex-direction: column; gap: 8px; }
.papers-list .paper-row-actions { display: table-cell; width: 22%; white-space: nowrap; }
.paper-source { align-items: center; line-height: 1.7; margin-top: 8px; }
.paper-title { font-size: 15px; line-height: 1.6; }
.paper-more { border-radius: 8px; border-color: var(--color-control-border); }
@media (max-width: 767px) { .paper-toolbar { padding: 16px; } .paper-summary { padding: 16px; gap: 10px; } .paper-detail :deep(.el-button), .paper-more { min-height: 44px; } }
</style>
