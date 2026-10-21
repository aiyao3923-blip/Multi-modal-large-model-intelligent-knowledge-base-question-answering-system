import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { renderMarkdown, safeLink } from '../src/utils/markdown.js'
import { parseJson, formatBytes, formatDate, formatNumber, downloadText } from '../src/utils/format.js'

test('Markdown renders headings, lists, code and tables', () => {
  const html=renderMarkdown('# 标题\n\n- 一项\n- 二项\n\n\x60\x60\x60js\nconst x = "<img>"\n\x60\x60\x60\n\n|列|值|\n|--|--|\n|a|b|')
  assert.match(html,/<h1>标题<\/h1>/);assert.match(html,/<ul>/);assert.match(html,/<pre>/);assert.match(html,/&lt;img&gt;/);assert.match(html,/<table>/)
})
test('Untrusted raw HTML is escaped, including SVG and event handlers',()=>{
  for(const input of ['<script>alert(1)</script>','<img src=x onerror=alert(1)>','<svg onload=alert(1)>x</svg>','<iframe srcdoc="<script>alert(1)</script>"></iframe>']){
    const html=renderMarkdown(input);assert.doesNotMatch(html,/<(?:script|img|svg|iframe)\b/i);assert.match(html,/&lt;/)
  }
})
test('Unsafe Markdown links never become navigable links',()=>{
  for(const href of ['javascript:alert(1)','data:text/html,boom','vbscript:msgbox(1)','file:///x','java&#x73;cript:alert(1)','//example.com']){
    assert.equal(safeLink(href),null);assert.doesNotMatch(renderMarkdown('[打开]('+href+')'),/<a\s/i)
  }
})
test('Remote images do not trigger tracking and normal links isolate opener',()=>{
  const image=renderMarkdown('![说明](https://example.com/tracker.png)');assert.doesNotMatch(image,/<img\b/);assert.match(image,/说明/)
  const link=renderMarkdown('[资料](https://example.com/docs)');assert.match(link,/rel="noopener noreferrer"/);assert.match(link,/target="_blank"/)
})
test('Malformed persisted JSON cannot crash application startup',()=>{
  assert.equal(parseJson('{broken',null),null);assert.deepEqual(parseJson('',[]),[]);assert.deepEqual(parseJson('[1,2]'),[1,2])
})
test('File and metric formatting have defined empty states',()=>{
  assert.equal(formatBytes(0),'0 B');assert.equal(formatBytes(1024),'1.0 KB');assert.equal(formatBytes(1048576),'1.0 MB');assert.equal(formatNumber(1234),'1,234');assert.equal(formatDate('not a date'),'—');assert.equal(formatDate(null),'—')
})
test('All v-html rendering is centralized behind the safe Markdown component',async()=>{
  const source=await readFile(new URL('../src/views/chat/ChatView.vue',import.meta.url),'utf8')
  assert.doesNotMatch(source,/v-html=/);assert.match(source,/MarkdownContent/)
  const component=await readFile(new URL('../src/components/MarkdownContent.vue',import.meta.url),'utf8')
  assert.match(component,/computed\(\(\) => renderMarkdown/)
})
test('Enter sends only when IME composition is complete',async()=>{
  const source=await readFile(new URL('../src/views/chat/ChatView.vue',import.meta.url),'utf8')
  assert.match(source,/!event\.isComposing/);assert.match(source,/event\.keyCode !== 229/);assert.match(source,/!event\.shiftKey/)
})
test('Charts release ResizeObserver and chart resources on unmount',async()=>{
  const source=await readFile(new URL('../src/components/DataChart.vue',import.meta.url),'utf8')
  assert.match(source,/observer\?\.disconnect\(\)/);assert.match(source,/chart\?\.dispose\(\)/);assert.doesNotMatch(source,/from 'echarts'/)
})

test('Text export uses an attached anchor and releases its blob after consumption', (t) => {
  const events=[]
  const anchor={click(){events.push('click')},remove(){events.push('remove')}}
  const previousDocument=globalThis.document
  globalThis.document={createElement:()=>anchor,body:{appendChild(a){assert.equal(a,anchor);events.push('append')}}}
  t.after(()=>{if(previousDocument===undefined)delete globalThis.document;else globalThis.document=previousDocument})
  let exportedBlob,release
  t.mock.method(URL,'createObjectURL',blob=>{exportedBlob=blob;return 'blob:test'})
  t.mock.method(URL,'revokeObjectURL',url=>events.push('revoke:'+url))
  t.mock.method(globalThis,'setTimeout',(callback,delay)=>{assert.ok(delay>=10000);release=callback;return 0})
  downloadText('测试对话.md','# 问答\n\n回答原文')
  assert.deepEqual(events,['append','click','remove'])
  assert.equal(anchor.href,'blob:test');assert.equal(anchor.download,'测试对话.md');assert.equal(anchor.hidden,true)
  assert.equal(exportedBlob.type,'text/markdown;charset=utf-8')
  release();assert.equal(events.at(-1),'revoke:blob:test')
})
