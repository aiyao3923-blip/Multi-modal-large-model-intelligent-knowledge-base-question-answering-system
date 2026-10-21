import { Marked, Renderer } from 'marked'
export const escapeHtml = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
export function safeLink(href) { return /^(https?:\/\/)[^\s<>"'\\]+$/i.test(href || '') ? href : null }
const renderer = new Renderer()
// Treat imported documents and model output as untrusted text, never executable HTML.
renderer.html = (html) => escapeHtml(html)
renderer.link = (href, title, text) => { const safe = safeLink(href); return safe ? '<a href="' + escapeHtml(safe) + '" target="_blank" rel="noopener noreferrer">' + text + '</a>' : text }
renderer.image = (href, title, text) => '<span class="markdown-image">[图片：' + escapeHtml(text || '未命名') + ']</span>'
const parser = new Marked({ renderer, gfm: true, breaks: true })
parser.use({ extensions: [{
  name: 'paperCitation', level: 'inline',
  start(source) { return source.indexOf('[来源') },
  tokenizer(source) {
    const match = /^\[来源([1-9]\d{0,2})\]/.exec(source)
    if (match) return { type: 'paperCitation', raw: match[0], rank: Number(match[1]) }
  },
  renderer(token) { return '<button type="button" class="inline-citation" data-source-rank="' + token.rank + '" aria-label="查看来源 ' + token.rank + '">[' + token.rank + ']</button>' },
}] })
export const renderMarkdown = (text) => parser.parse(String(text || ''))
