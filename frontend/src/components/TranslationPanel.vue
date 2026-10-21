<template>
  <el-dialog :model-value="modelValue" class="ui-translation-dialog" title="中英翻译" width="min(1040px, calc(100vw - 24px))" top="4vh" append-to-body :close-on-click-modal="false" @opened="focusLanguage" @update:model-value="setVisible" @close="stopTranslation({ silent: true })">
    <div class="translation-panel">
      <div id="translation-disclosure" class="translation-disclosure"><strong>外发提醒 · 发送前请确认</strong><p>使用当前模型通道翻译；在线通道会将所选文本外发给模型服务，请确认有权发送。原回答不会被覆盖，译文不保存到会话。</p></div>
      <div class="translation-toolbar">
        <div class="translation-target"><label for="translation-language">目标语言</label><select id="translation-language" ref="languageSelect" v-model="targetLanguage" :disabled="translating" aria-describedby="translation-disclosure"><option value="zh">中文</option><option value="en">English</option></select></div>
        <span class="translation-stage" role="status">{{ translating ? progressLabel : errorMessage ? '翻译未完成' : infoMessage ? '翻译已停止' : translationResult ? (resultIsCurrent ? '翻译已完成' : '保留上次译文') : '等待开始' }}</span>
      </div>
      <div class="translation-columns">
        <section class="translation-column" aria-label="待翻译原文">
          <label for="translation-source" class="translation-label">原文 <span id="translation-source-count">{{ sourceChars }} 字符</span></label>
          <textarea id="translation-source" v-model="draft" class="translation-text" :disabled="translating" rows="12" aria-describedby="translation-disclosure translation-source-note translation-source-count" placeholder="粘贴需要翻译的中文或英文，也可从回答的「翻译」按钮打开。" @input="markSourceEdited" />
          <p id="translation-source-note" class="translation-hint">{{ usesMessageSource ? '将按原消息校验访问权限；编辑原文后改为仅发送文本。' : '仅发送上方文本，不附带其他会话或引用原文。' }}前端不设字符上限，实际长度仍受当前模型能力约束。</p>
        </section>
        <section class="translation-column translation-output" aria-label="翻译结果">
          <template v-if="translationResult">
            <label for="translation-result" class="translation-label">{{ resultIsCurrent && !infoMessage ? '完整译文' : '上次完成的译文' }} · {{ translationResult.target_language === 'zh' ? '中文' : 'English' }} <span id="translation-result-count">{{ resultChars }} 字符</span></label>
            <textarea id="translation-result" ref="resultPreview" class="translation-text translation-result" :value="translationResult.translation" readonly rows="12" :aria-label="resultIsCurrent && !infoMessage ? '完整译文，只读 Markdown 文本' : '上次完成的译文，只读 Markdown 文本'" aria-describedby="translation-result-note translation-result-count" />
            <p id="translation-result-note" class="translation-hint">{{ translationResult.mode === 'local' ? '本地模型' : '在线模型' }} · 保留 Markdown 文本与来源标记；这里的来源标记不可跳转。可复制或下载完整内容。</p>
          </template>
          <template v-else>
            <h3 class="translation-label">译文 <span>完整结果区</span></h3>
            <div class="translation-empty"><strong>{{ translating ? '正在等待完整译文' : errorMessage ? '本次未生成完整译文' : infoMessage ? '尚无已完成的译文' : '译文将在这里显示' }}</strong><p>{{ translating ? '仅显示阶段和过程统计，不展示未完成内容。' : errorMessage ? '原文已保留，请检查提示后手动重试。' : '选择目标语言后开始翻译，完整译文将在这里显示。' }}</p></div>
          </template>
          <div v-if="translating" class="translation-progress" role="status" aria-live="polite"><strong>{{ progressLabel }}…</strong><span v-if="progress.received_chars !== null">已接收 {{ progress.received_chars }} 字符（过程统计）</span><span v-if="progress.elapsed_seconds !== null">已等待 {{ Math.floor(progress.elapsed_seconds) }} 秒</span><small>生成完成后一次显示完整译文，可随时停止等待。</small></div>
          <p v-if="errorMessage" class="translation-error" role="alert">{{ errorMessage }}</p>
          <p v-if="infoMessage" class="translation-notice" role="status">{{ infoMessage }}</p>
          <p v-if="copyStatus" class="translation-notice" role="status" aria-live="polite">{{ copyStatus }}</p>
        </section>
      </div>
    </div>
    <template #footer><div class="translation-footer"><el-button @click="setVisible(false)">{{ translating ? '关闭并停止' : '关闭' }}</el-button><el-button :disabled="!translationResult" @click="copyTranslation">复制译文</el-button><el-button :disabled="!translationResult" @click="downloadTranslation">下载 Markdown</el-button><el-button :type="translating ? 'default' : 'primary'" :disabled="!translating && !canTranslate" @click="translating ? stopTranslation() : startTranslation()">{{ translating ? '停止翻译' : '开始翻译' }}</el-button></div></template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount, nextTick } from 'vue'
import { ElButton, ElDialog } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/dialog/style/css.mjs'
import { translateApi } from '@/api'
import { downloadText } from '@/utils/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  sourceText: { type: String, default: '' },
  sourceMessageId: { type: Number, default: null },
})
const emit = defineEmits(['update:modelValue'])
const draft = ref(''), targetLanguage = ref('zh'), sourceEdited = ref(false)
const translating = ref(false), translationResult = ref(null), completedSource = ref('')
const errorMessage = ref(''), infoMessage = ref(''), copyStatus = ref(''), resultPreview = ref()
const languageSelect = ref()
// Element Plus owns the focus trap and restores the opener on close.
function focusLanguage() { if (props.modelValue && !translating.value) languageSelect.value?.focus() }
const progress = ref({ stage: 'translating', received_chars: null, elapsed_seconds: null })
const sourceChars = computed(() => Array.from(draft.value).length)
const resultChars = computed(() => Array.from(translationResult.value?.translation || '').length)
const usesMessageSource = computed(() => !sourceEdited.value && draft.value === props.sourceText && Number.isSafeInteger(props.sourceMessageId) && props.sourceMessageId > 0)
const canTranslate = computed(() => Boolean(draft.value.trim()) && ['zh', 'en'].includes(targetLanguage.value))
const progressNames = { retrieving: '正在准备原文', generating: '正在生成译文', validating: '正在校验完整译文', translating: '正在翻译' }
const progressLabel = computed(() => progressNames[progress.value.stage] || '正在翻译')
const resultIsCurrent = computed(() => !translating.value && !errorMessage.value && completedSource.value === draft.value && translationResult.value?.target_language === targetLanguage.value)
let alive = true, activeTranslation = null

function markSourceEdited() {
  sourceEdited.value = true
  errorMessage.value = ''
  infoMessage.value = ''
  copyStatus.value = ''
}
function isCurrent(request) {
  return alive && props.modelValue && activeTranslation === request && !request.controller.signal.aborted
}
async function startTranslation() {
  if (!alive || !props.modelValue || translating.value || !canTranslate.value) return
  const request = { controller: new AbortController(), text: draft.value, target: targetLanguage.value, messageId: usesMessageSource.value ? props.sourceMessageId : null }
  const payload = request.messageId === null ? { text: request.text, target_language: request.target } : { message_id: request.messageId, target_language: request.target }
  activeTranslation = request
  translating.value = true
  errorMessage.value = ''; infoMessage.value = ''; copyStatus.value = ''
  progress.value = { stage: 'translating', received_chars: null, elapsed_seconds: null }
  try {
    const data = await translateApi(payload, {
      signal: request.controller.signal,
      onProgress(event) { if (isCurrent(request)) progress.value = { ...progress.value, ...event } },
    })
    if (!isCurrent(request)) return
    if (data?.generation_status !== 'generated' || typeof data.translation !== 'string' || !data.translation.trim()
      || data.target_language !== request.target || !['local', 'online'].includes(data.mode) || data.source_message_id !== request.messageId) {
      throw new Error('Incomplete translation response')
    }
    translationResult.value = data
    completedSource.value = request.text
  } catch (error) {
    if (!isCurrent(request)) return
    if (error?.name === 'AbortError' || error?.response?.status === 499) infoMessage.value = '翻译已停止，原文与已完成译文保留。'
    else {
      const detail = error?.response?.data?.detail
      errorMessage.value = (typeof detail === 'string' && detail.trim() ? detail : '翻译未完成，请检查连接或当前模型通道后手动重试。') + ' 原文与上次完成的译文不会被覆盖。'
    }
  } finally {
    if (activeTranslation === request) { activeTranslation = null; translating.value = false }
  }
}
function stopTranslation({ silent = false } = {}) {
  const request = activeTranslation
  if (!request) return
  activeTranslation = null
  request.controller.abort()
  translating.value = false
  if (!silent && alive) infoMessage.value = '已停止翻译，原文与已完成译文保留，未完成内容不会显示。'
}
function setVisible(value) {
  if (!value) stopTranslation({ silent: true })
  emit('update:modelValue', value)
}
async function copyTranslation() {
  const result = translationResult.value
  if (!result) return
  try {
    await navigator.clipboard.writeText(result.translation)
    if (alive && props.modelValue && translationResult.value === result) copyStatus.value = '完整译文已复制。'
  } catch {
    if (!alive || !props.modelValue || translationResult.value !== result) return
    await nextTick()
    if (!alive || !props.modelValue || translationResult.value !== result) return
    resultPreview.value?.focus()
    resultPreview.value?.select()
    copyStatus.value = '浏览器未允许复制，请手动复制已选中的完整译文。'
  }
}
function downloadTranslation() {
  const result = translationResult.value
  if (!result) return
  try {
    downloadText('译文-' + result.target_language + '.md', result.translation, 'text/markdown;charset=utf-8')
    copyStatus.value = '已请求下载完整 UTF-8 Markdown 译文，请检查浏览器下载记录。'
  } catch { copyStatus.value = '下载未完成，请复制译文，或选择完整译文后手动保存。' }
}
watch(() => [props.modelValue, props.sourceText, props.sourceMessageId], ([open]) => {
  stopTranslation({ silent: true })
  if (!open) return
  draft.value = props.sourceText
  targetLanguage.value = 'zh'
  sourceEdited.value = false
  translationResult.value = null; completedSource.value = ''
  errorMessage.value = ''; infoMessage.value = ''; copyStatus.value = ''
  progress.value = { stage: 'translating', received_chars: null, elapsed_seconds: null }
}, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { alive = false; stopTranslation({ silent: true }) })
</script>

<style scoped>
/* Dialog is teleported: the unique class limits these rules to this reader. */
:global(.ui-translation-dialog) { display: flex; flex-direction: column; max-height: 92vh; max-height: 92dvh; margin-bottom: 0; border-radius: 16px; padding: 24px; }
:global(.ui-translation-dialog .el-dialog__header) { flex: none; padding-bottom: 16px; }
:global(.ui-translation-dialog .el-dialog__title) { color: var(--color-ink, #172033); font-size: 20px; font-weight: 600; }
:global(.ui-translation-dialog .el-dialog__headerbtn) { width: 44px; height: 44px; }
:global(.ui-translation-dialog .el-dialog__body) { min-height: 0; overflow-y: auto; padding: 4px; }
:global(.ui-translation-dialog .el-dialog__footer) { flex: none; padding-top: 16px; }
:global(.ui-translation-dialog .el-dialog__headerbtn:focus-visible) { outline: 2px solid var(--color-accent, #365CF5); outline-offset: -4px; }
.translation-panel { min-width: 0; color: var(--color-ink, #172033); font: 14px/1.65 var(--font-body, 'Segoe UI', 'Microsoft YaHei', sans-serif); }
.translation-disclosure { margin: 0 0 16px; padding: 12px 16px; background: var(--color-warning-soft, #FFF7E6); border-left: 3px solid var(--color-warning, #9A6700); border-radius: 8px; color: var(--color-ink, #172033); font-size: 14px; }
.translation-disclosure strong { display: block; color: var(--color-warning, #9A6700); font-size: 14px; }
.translation-disclosure p { margin: 4px 0 0; overflow-wrap: anywhere; }
.translation-toolbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.translation-target { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.translation-target label { font-weight: 600; }
.translation-target select { min-height: 44px; min-width: 140px; max-width: 100%; padding: 8px 12px; border: 1px solid var(--color-control-border, #8591A5); border-radius: 8px; color: var(--color-ink, #172033); background: var(--color-surface, #FFFFFF); font: inherit; }
.translation-stage { color: var(--color-secondary, #5D687B); background: var(--color-page, #F4F6F9); padding: 4px 8px; border-radius: 8px; font-size: 12px; }
.translation-columns { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; gap: 24px; }
.translation-column { min-width: 0; }
.translation-label { display: flex; align-items: baseline; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin: 0 0 12px; font-size: 16px; font-weight: 600; }
.translation-label span, .translation-hint { color: var(--color-secondary, #5D687B); font-size: 12px; font-weight: 400; overflow-wrap: anywhere; }
.translation-hint { margin: 12px 0 0; }
.translation-text { display: block; box-sizing: border-box; width: 100%; min-width: 0; min-height: 280px; height: 36vh; max-height: 56vh; padding: 16px; border: 1px solid var(--color-control-border, #8591A5); border-radius: 8px; background: var(--color-surface, #FFFFFF); color: var(--color-ink, #172033); font: 16px/1.8 var(--font-body, 'Segoe UI', 'Microsoft YaHei', sans-serif); resize: vertical; white-space: pre-wrap; overflow-wrap: anywhere; }
.translation-result { background: var(--color-page, #F4F6F9); }
.translation-text:disabled, select:disabled { background: var(--color-page, #F4F6F9); color: var(--color-secondary, #5D687B); opacity: 1; cursor: not-allowed; }
.translation-text:focus-visible, select:focus-visible, .translation-footer :deep(.el-button:focus-visible) { outline: 2px solid var(--color-accent, #365CF5); outline-offset: 2px; }
.translation-progress { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 12px; margin-top: 12px; padding: 12px 16px; background: var(--color-accent-soft, #EDF1FF); border-radius: 8px; color: var(--color-secondary, #5D687B); font-size: 14px; }
.translation-progress strong { color: var(--color-ink, #172033); }
.translation-progress small { flex-basis: 100%; font-size: 12px; }
.translation-error { padding: 12px 16px; background: var(--color-danger-soft, #FDF0F2); border-left: 3px solid var(--color-danger, #C23B4B); border-radius: 8px; color: var(--color-danger, #C23B4B); overflow-wrap: anywhere; margin: 12px 0 0; }
.translation-notice { padding: 12px 16px; background: var(--color-page, #F4F6F9); border-radius: 8px; color: var(--color-secondary, #5D687B); margin: 12px 0 0; overflow-wrap: anywhere; }
.translation-empty { display: flex; flex-direction: column; justify-content: center; min-height: 280px; height: 36vh; box-sizing: border-box; padding: 24px; border: 1px dashed var(--color-control-border, #8591A5); border-radius: 8px; color: var(--color-secondary, #5D687B); background: var(--color-page, #F4F6F9); text-align: center; }
.translation-empty strong { color: var(--color-ink, #172033); font-size: 16px; }
.translation-empty p { margin: 8px 0 0; }
.translation-footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
.translation-footer :deep(.el-button) { min-height: 44px; height: auto; padding: 10px 16px; font-size: 14px; border-radius: 8px; }
.translation-footer :deep(.el-button + .el-button) { margin-left: 0; }
@media (max-width: 767px) {
  :global(.ui-translation-dialog) { padding: 16px; }
  .translation-columns { grid-template-columns: minmax(0, 1fr); gap: 24px; }
  .translation-text { min-height: 200px; height: 28vh; padding: 12px; }
  .translation-empty { min-height: 160px; height: auto; padding: 24px 16px; }
}
@media (max-width: 480px) {
  .translation-disclosure { padding: 12px; }
  .translation-target { flex: 1 1 100%; }
  .translation-target select { flex: 1; font-size: 16px; }
  .translation-footer { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); padding-bottom: env(safe-area-inset-bottom); }
  .translation-footer :deep(.el-button) { min-width: 0; padding-inline: 8px; white-space: normal; line-height: 1.5; }
}
</style>
