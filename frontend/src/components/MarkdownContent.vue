<template><div class="markdown-body" v-html="html" @click="onSourceClick"></div></template>
<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
const props = defineProps({ content: { type: String, default: '' } })
const emit = defineEmits(['source'])
const html = computed(() => renderMarkdown(props.content))
function onSourceClick(event) {
  const button = event.target.closest?.('button[data-source-rank]')
  if (button && event.currentTarget.contains(button)) emit('source', Number(button.dataset.sourceRank))
}
</script>
<style scoped>
.markdown-body { min-width: 0; font-size: var(--reader-font-size, 16px); color: var(--color-ink); line-height: 1.8; overflow-wrap: anywhere; }
.markdown-body :deep(p) { margin: 0 0 16px; }
.markdown-body :deep(p:last-child) { margin-bottom: 0; }
.markdown-body :deep(h1), .markdown-body :deep(h2), .markdown-body :deep(h3), .markdown-body :deep(h4) { font-size: 18px; line-height: 1.55; font-weight: 600; margin: 24px 0 12px; color: var(--color-ink); }
.markdown-body :deep(h1) { font-size: 22px; }
.markdown-body :deep(ul), .markdown-body :deep(ol) { padding-left: 25px; }
.markdown-body :deep(li) { margin-bottom: 7px; }
.markdown-body :deep(a) { color: var(--color-accent); text-decoration: underline; text-underline-offset: 3px; }
.markdown-body :deep(a:hover) { color: var(--color-accent-hover); }
.markdown-body :deep(pre) { background: var(--color-page); border: 1px solid var(--color-line); border-radius: 4px; padding: 16px; overflow-x: auto; max-width: 100%; line-height: 1.75; }
.markdown-body :deep(code) { font-family: var(--font-mono); font-size: 14px; background: var(--color-page); padding: 2px 5px; border-radius: 3px; }
.markdown-body :deep(pre code) { background: none; padding: 0; white-space: pre; }
.markdown-body :deep(blockquote) { margin: 20px 0; border-left: 3px solid var(--color-accent); padding: 12px 18px; background: var(--color-page); color: var(--color-secondary); }
.markdown-body :deep(table) { display: block; overflow-x: auto; border-collapse: collapse; width: 100%; max-width: 100%; font-size: 14px; margin: 20px 0; }
.markdown-body :deep(th), .markdown-body :deep(td) { border: 1px solid var(--color-line); padding: 10px 12px; min-width: 85px; }
.markdown-body :deep(th) { background: var(--color-page); text-align: left; }
.markdown-body :deep(hr) { border: 0; border-top: 1px solid var(--color-line); margin: 24px 0; }
@media (max-width: 640px) { .markdown-body { font-size: var(--reader-font-size, 16px); } .markdown-body :deep(pre) { padding: 12px; } .markdown-body :deep(blockquote) { padding: 10px 14px; } }
.markdown-body :deep(.inline-citation) { display: inline; border: 0; background: var(--color-accent-soft); color: var(--color-accent); border-radius: 4px; padding: 1px 4px; font: inherit; font-size: .75em; vertical-align: .1em; line-height: 1.6; cursor: pointer; margin: 0 2px; }
.markdown-body :deep(.inline-citation:hover) { text-decoration: underline; }
.markdown-body :deep(.inline-citation:focus-visible) { outline: 2px solid var(--color-accent); outline-offset: 2px; }
</style>
