<template>
  <section class="citation-reader" aria-label="原文与证据">
    <header class="reader-topbar">
      <div><span class="reader-eyebrow">来源阅读</span><strong>原文与证据</strong></div>
      <button type="button" class="reader-icon" aria-label="关闭原文证据" @click="$emit('close')"><AppIcon name="close" :size="20" /></button>
    </header>
    <div v-if="!citation" class="reader-empty" role="status"><AppIcon name="book" :size="28" /><strong>从来源开始阅读</strong><p>选择回答中的来源，查看引用片段与可访问的原文。</p></div>
    <template v-else>
      <div class="reader-document">
        <span class="source-tag">{{ citation.rank ? '来源 ' + citation.rank : '来源序号未知' }}</span>
        <h2>{{ citation.doc_name || '文件名未知' }}</h2>
        <p class="reader-metadata">{{ Number(citation.page_no) > 0 ? '第 ' + citation.page_no + ' 页' : '页码未知 · 文本片段' }}</p>
        <p v-if="!citation.doc_id" class="reader-availability">未提供原文标识，无法预览或下载原文。</p>
      </div>
      <nav class="reader-tabs" aria-label="证据视图">
        <button type="button" :class="{ active: view === 'excerpt' }" :aria-pressed="view === 'excerpt'" @click="view = 'excerpt'">引用片段 <span lang="en">excerpt</span></button>
        <button type="button" :class="{ active: view === 'original' }" :aria-pressed="view === 'original'" :disabled="!citation.doc_id" @click="showOriginal">论文原文 <span lang="en">original</span></button>
        <button type="button" class="download-source" aria-label="下载当前原文" :disabled="loading || !citation.doc_id" @click="downloadOriginal"><AppIcon name="download" :size="18" /><span>下载原文</span></button>
      </nav>
      <div v-if="view === 'excerpt'" class="excerpt-scroll" role="region" aria-label="引用片段" tabindex="0">
        <p class="reader-context">保存的引用片段 · 不等同于全文</p>
        <p class="source-excerpt" :class="{ 'excerpt-missing': !citation.content_excerpt }">{{ citation.content_excerpt || '这条历史引用没有保存片段。' }}</p>
        <p v-if="loading" class="reader-inline-status" role="status">正在加载原文…</p>
        <div v-else-if="error" class="reader-inline-status reader-error" role="alert"><p>{{ error }}</p><button type="button" @click="downloadOriginal">重试下载</button></div>
      </div>
      <div v-else class="original-view" role="region" aria-label="论文原文" :aria-busy="loading">
        <p v-if="loading" class="reader-status" role="status">正在加载原文…</p>
        <div v-else-if="error" class="reader-status reader-error" role="alert"><p>{{ error }}</p><button type="button" @click="loadFile">重试</button></div>
        <iframe v-else-if="fileUrl && isPdf" :src="pdfUrl" :title="(citation.doc_name || '文件名未知') + ' 原文预览'" class="pdf-preview" />
        <pre v-else-if="textContent !== null" class="text-preview" tabindex="0">{{ textContent }}</pre>
        <div v-else class="reader-status"><p>此格式可下载后阅读。</p><button type="button" :disabled="loading || !citation.doc_id" @click="downloadOriginal">下载原文</button></div>
      </div>
      <nav v-if="sources.length > 1" class="source-pagination" aria-label="切换来源">
        <p class="source-pagination-label">全部来源 <span>{{ sources.length }} 项 · 可滚动查看</span></p>
        <div class="source-pagination-items">
          <button v-for="(item, index) in sources" :key="index" type="button" :class="{ selected: item === citation }" :aria-pressed="item === citation" :aria-current="item === citation ? 'true' : undefined" :aria-label="'阅读来源 ' + (item.rank || index + 1) + '：' + (item.doc_name || '文件名未知')" @click="$emit('select', item)">{{ item.rank || index + 1 }}</button>
        </div>
      </nav>
    </template>
  </section>
</template>
<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { documentFileApi } from '@/api'
const props = defineProps({ kbId: Number, citation: Object, sources: { type: Array, default: () => [] } })
defineEmits(['close', 'select'])
const view = ref('excerpt'), loading = ref(false), error = ref(''), fileUrl = ref(''), textContent = ref(null), isPdf = ref(false)
let version = 0, fileBlob = null
const pdfUrl = computed(() => fileUrl.value + '#page=' + Math.max(1, Number(props.citation?.page_no) || 1) + '&view=FitH')
function release() { version++; if (fileUrl.value) URL.revokeObjectURL(fileUrl.value); fileUrl.value = ''; fileBlob = null; textContent.value = null; loading.value = false; error.value = '' }
watch(() => [props.kbId, props.citation?.doc_id, props.citation?.chunk_id, props.citation?.rank], () => { release(); view.value = 'excerpt' })
async function loadFile() {
  if (!props.kbId || !props.citation?.doc_id || loading.value) return false
  if (fileBlob) return true
  const request = ++version, kb = props.kbId, doc = props.citation.doc_id
  loading.value = true; error.value = ''
  try {
    const blob = await documentFileApi(kb, doc)
    if (request !== version) return false
    if (!(blob instanceof Blob) || !blob.size) throw new Error('empty file')
    // Only render a PDF as a frame. HTML/SVG and office documents are download-only.
    isPdf.value = blob.type.split(';')[0] === 'application/pdf' && /\.pdf$/i.test(props.citation.doc_name || '')
    if (isPdf.value) fileUrl.value = URL.createObjectURL(blob)
    else if (/\.(md|txt|csv)$/i.test(props.citation.doc_name || '') && blob.size <= 2 * 1024 * 1024) {
      const text = await blob.text()
      if (request !== version) return false
      textContent.value = text
    }
    if (request !== version || kb !== props.kbId || doc !== props.citation?.doc_id) return false
    fileBlob = blob
    return true
  } catch (failure) {
    if (request === version) { fileBlob = null; textContent.value = null; if (fileUrl.value) URL.revokeObjectURL(fileUrl.value); fileUrl.value = ''; error.value = failure?.response?.status === 403 ? '没有这份论文的访问权限。' : failure?.response?.status === 404 ? '原文已不存在，已保存的引用片段仍可阅读。' : '原文加载失败，请重试。' }
    return false
  } finally { if (request === version) loading.value = false }
}
async function showOriginal() { view.value = 'original'; await loadFile() }
async function downloadOriginal() {
  const kb = props.kbId, doc = props.citation?.doc_id, name = props.citation?.doc_name || 'paper'
  if (!await loadFile() || !fileBlob || kb !== props.kbId || doc !== props.citation?.doc_id) return
  const url = URL.createObjectURL(fileBlob), link = document.createElement('a')
  link.href = url; link.download = name; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
onBeforeUnmount(release)
defineExpose({ showOriginal })
</script>
<style scoped>
.citation-reader { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; overflow-y: auto; color: var(--color-ink); background: var(--color-surface); font: 14px/1.65 var(--font-body, 'Segoe UI', 'Microsoft YaHei', sans-serif); }
.reader-topbar { display: flex; flex: none; align-items: center; justify-content: space-between; gap: 12px; min-height: 64px; padding: 12px 16px; border-bottom: 1px solid var(--color-line); }
.reader-topbar strong { display: block; font-size: 18px; font-weight: 600; }
.reader-eyebrow { color: var(--color-source, #0F766E); font-size: 12px; }
.reader-icon { display: inline-flex; flex: none; align-items: center; justify-content: center; background: transparent; color: var(--color-secondary); border: 1px solid transparent; width: 44px; height: 44px; border-radius: 8px; cursor: pointer; }
.reader-icon:hover { color: var(--color-source, #0F766E); background: var(--color-source-soft, #ECFDF5); }
.reader-document { flex: none; padding: 16px; overflow-wrap: anywhere; }
.source-tag { display: inline-block; color: var(--color-source, #0F766E); background: var(--color-source-soft, #ECFDF5); padding: 4px 8px; border-radius: 8px; font-size: 12px; font-weight: 600; }
.reader-document h2 { font-size: 16px; line-height: 1.6; font-weight: 600; margin: 12px 0 8px; }
.reader-metadata, .reader-availability { color: var(--color-secondary); font-size: 12px; margin: 0; }
.reader-availability { margin-top: 8px; }
.reader-tabs { display: flex; flex: none; flex-wrap: wrap; align-items: stretch; gap: 8px; padding: 0 16px 12px; border-bottom: 1px solid var(--color-line); }
.reader-tabs > button { display: flex; flex: 1 1 auto; flex-direction: column; align-items: center; justify-content: center; min-height: 48px; padding: 8px; border: 1px solid var(--color-control-border, #8591A5); border-radius: 8px; background: var(--color-surface); font: inherit; font-size: 14px; line-height: 1.4; color: var(--color-secondary); cursor: pointer; }
.reader-tabs > button > span[lang] { font-size: 12px; }
.reader-tabs > button.active { color: var(--color-source, #0F766E); background: var(--color-source-soft, #ECFDF5); border-color: var(--color-source, #0F766E); font-weight: 600; }
.reader-tabs > .download-source { flex-direction: row; gap: 8px; }
.reader-tabs > button:hover:not(:disabled) { background: var(--color-source-soft, #ECFDF5); color: var(--color-source, #0F766E); }
.reader-tabs > button:disabled { background: var(--color-page); color: var(--color-muted); cursor: not-allowed; }
.excerpt-scroll { overflow-y: auto; flex: 1 0 180px; min-height: 180px; padding: 16px; }
.reader-context { color: var(--color-secondary); font-size: 12px; margin: 0 0 12px; }
.source-excerpt { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 16px; line-height: 1.8; margin: 0; padding: 12px 16px; border-left: 3px solid var(--color-source, #0F766E); border-radius: 0 8px 8px 0; background: var(--color-source-soft, #ECFDF5); }
.excerpt-missing { font-size: 14px; color: var(--color-secondary); background: var(--color-page); border-left-color: var(--color-line); }
.original-view { flex: 1 0 240px; min-height: 240px; display: flex; overflow: auto; }
.pdf-preview { flex: 1; width: 100%; min-height: 240px; border: 0; background: var(--color-page); }
.text-preview { width: 100%; min-width: 0; box-sizing: border-box; margin: 0; padding: 16px; white-space: pre-wrap; overflow-wrap: anywhere; font: 16px/1.8 var(--font-body, sans-serif); }
.reader-status, .reader-empty { padding: 24px 16px; color: var(--color-secondary); font-size: 14px; line-height: 1.8; overflow-wrap: anywhere; }
.reader-empty { margin-block: auto; text-align: center; }
.reader-empty strong { display: block; color: var(--color-ink); font-size: 16px; margin: 12px 0 8px; }
.reader-empty p { margin: 0; }
.reader-inline-status { font-size: 14px; margin-top: 12px; }
.reader-error { color: var(--color-danger); }
.reader-status button, .reader-inline-status button { min-height: 44px; border: 1px solid var(--color-control-border, #8591A5); background: var(--color-surface); border-radius: 8px; color: var(--color-ink); padding: 8px 16px; font: inherit; cursor: pointer; }
.source-pagination { flex: none; border-top: 1px solid var(--color-line); padding: 12px 16px; }
.source-pagination-label { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; margin: 0 0 8px; font-size: 14px; font-weight: 600; }
.source-pagination-label span { color: var(--color-secondary); font-size: 12px; font-weight: 400; }
.source-pagination-items { display: flex; gap: 8px; flex-wrap: wrap; max-height: 152px; overflow-y: auto; padding: 4px; }
.source-pagination button { min-width: 44px; min-height: 44px; padding: 8px; background: var(--color-surface); border: 1px solid var(--color-control-border, #8591A5); border-radius: 8px; color: var(--color-secondary); font: inherit; font-size: 14px; cursor: pointer; }
.source-pagination button.selected, .source-pagination button:hover { background: var(--color-source-soft, #ECFDF5); color: var(--color-source, #0F766E); border-color: var(--color-source, #0F766E); }
button:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--color-accent); outline-offset: 2px; }
.excerpt-scroll:focus-visible, .text-preview:focus-visible { outline-offset: -3px; }
@media (max-width: 480px) { .reader-topbar { padding-top: 8px; padding-bottom: 8px; } .source-pagination { padding-bottom: max(12px, env(safe-area-inset-bottom)); } }
</style>
