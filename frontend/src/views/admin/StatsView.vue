<template>
<div class="page admin-page statistics-workbench">
  <PageHeading title="使用统计"><el-button :loading="loading" @click="load"><AppIcon name="refresh" :size="16" />更新数据</el-button></PageHeading>
  <div v-if="error" class="error-strip" role="alert"><AppIcon name="warning" :size="16" /><span>{{ error }}</span><button type="button" class="text-button" :disabled="loading" @click="load">重新加载</button></div>
  <div class="metric-grid" v-loading="loading" aria-label="使用与资料统计"><div v-for="m in metrics" :key="m.label" class="metric-card"><div class="metric-label">{{ m.label }}</div><div class="metric-value">{{ m.value == null ? '—' : formatNumber(m.value) }}<small v-if="m.value != null">{{ m.unit }}</small></div><div class="metric-note">{{ m.note }}</div></div></div>

  <details class="admin-disclosure stats-definitions"><summary>指标说明</summary><dl><div><dt>累计问答 / 回答模式</dt><dd>来自已完成的问答事件。删除对话不会删除已记录的累计事件。</dd></div><div><dt>引用覆盖率</dt><dd>当前含引用的助手消息 ÷ 当前助手消息数。{{ summaryReady ? '当前分母为 ' + (ov.assistant_total || 0) + ' 条。' : '当前数据未读取。' }}有引用不代表内容已经核实正确。</dd></div><div><dt>文档解析成功率</dt><dd>当前解析成功的文档 ÷ 当前文档总数。{{ summaryReady ? '当前分母为 ' + (ov.doc_total || 0) + ' 份。' : '' }}解析完成只描述资料处理结果。</dd></div><div><dt>用户反馈</dt><dd>统计当前保留的反馈，包含全部处理状态；评价比例不是自动评测结论。</dd></div></dl><p>下方知识库筛选仅作用于 30 天趋势及其每日数据表，不改变本页累计指标、排行或分布。</p></details>

  <section class="panel admin-section" aria-labelledby="stats-trend-title">
    <div class="panel-head"><div><h2 id="stats-trend-title">问答使用趋势</h2><p>{{ trendScope }} · 最近 30 天{{ trendReady ? ' · ' + formatNumber(trendTotal) + ' 次问答' : '' }}</p></div><div class="stats-filter"><label for="stats-kb">仅筛选本趋势</label><el-select id="stats-kb" v-model="filterKb" clearable placeholder="全部知识库" :disabled="loading" @change="loadTrend"><el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" /></el-select></div></div>
    <div class="stats-chart-body" v-loading="trendLoading"><DataChart :show-data-table="false" v-if="trendReady && trend.length" :option="trendOption" label="最近三十天问答次数与检索式回答次数；下方可展开每日数据" :height="300" /><div v-else-if="trendLoading" class="admin-empty-loading" role="status"><p>正在读取趋势数据…</p><el-skeleton :rows="4" /></div><EmptyState v-else-if="trendError" icon="warning" title="趋势未能读取" description="可重新加载数据或调整知识库筛选。" /><EmptyState v-else icon="chart" title="当前没有趋势数据" description="数据返回后，图与每日数据表将使用同一份记录。" /></div>
    <details v-if="trendReady && trend.length" class="admin-disclosure stats-table-disclosure"><summary>查看每日数据 · {{ trend.length }} 天</summary><p id="stats-trend-scroll" class="admin-table-note">问答总量按累计事件计数；“保留引用回答”来自当前消息，删除后可能与累计事件口径不同。窄屏可横向滚动。</p><div class="admin-table-scroll" role="region" tabindex="0" aria-label="每日问答数据表" aria-describedby="stats-trend-scroll"><table class="admin-data-table stats-daily-table"><caption>{{ trendScope }} · 北京时间（UTC+8）· 单位：次</caption><thead><tr><th scope="col">日期</th><th scope="col" class="is-numeric">问答总量</th><th scope="col" class="is-numeric">本地模型</th><th scope="col" class="is-numeric">在线模型</th><th scope="col" class="is-numeric">检索式</th><th scope="col" class="is-numeric">无结果</th><th scope="col" class="is-numeric">保留引用回答</th></tr></thead><tbody><tr v-for="point in trend" :key="point.date"><th scope="row">{{ point.date }}</th><td class="is-numeric">{{ point.qa_count }}</td><td class="is-numeric">{{ point.local }}</td><td class="is-numeric">{{ point.online }}</td><td class="is-numeric">{{ point.degraded }}</td><td class="is-numeric">{{ point.none }}</td><td class="is-numeric">{{ point.cited }}</td></tr></tbody></table></div></details>
  </section>

  <div v-if="!summaryReady" class="admin-annotation is-neutral" role="status"><AppIcon name="info" :size="16" /><p>{{ loading ? '正在读取累计分布…' : '累计分布尚未读取，不能将它显示为零。请更新数据。' }}</p></div>
  <template v-else>
    <div class="stats-distributions">
      <section class="stats-block" aria-labelledby="stats-kb-title"><div class="admin-section-heading"><div><h2 id="stats-kb-title">使用排行</h2><p>累计问答量前 6 个 · 单位：次</p></div></div><DataChart :show-data-table="false" v-if="kbDist.some(k => k.qa_count)" :option="kbOption" label="前六个知识库累计问答量；下方可查看完整排行" :height="280" /><EmptyState v-else icon="chart" title="尚无知识库问答事件" description="首次完成问答后，排行会按实际事件显示。" /><details class="admin-disclosure"><summary>查看完整知识库数据 · {{ allKbDist.length }} 个</summary><table class="admin-data-table stats-compact-table"><caption>全部可见知识库 · 累计事件口径</caption><thead><tr><th scope="col">知识库</th><th scope="col" class="is-numeric">问答次数</th></tr></thead><tbody><tr v-for="kb in allKbDist" :key="kb.kb_id"><th scope="row"><RouterLink class="text-button" :to="'/admin/kbs/' + kb.kb_id">{{ kb.kb_name }}</RouterLink></th><td class="is-numeric">{{ kb.qa_count }}</td></tr><tr v-if="!allKbDist.length"><td colspan="2">当前没有可见知识库。</td></tr></tbody></table></details></section>
      <section class="stats-block" aria-labelledby="stats-mode-title"><div class="admin-section-heading"><div><h2 id="stats-mode-title">模式分布</h2><p>累计事件样本：{{ formatNumber(ov.qa_total) }} 次</p></div></div><DataChart :show-data-table="false" v-if="ov.qa_total" :option="modeOption" label="本地、在线、检索式与无结果的累计次数；下方列出相同数据" :height="250" /><EmptyState v-else icon="chat" title="还没有问答记录" description="没有样本时不计算模式占比。" /><table class="admin-data-table stats-compact-table"><caption>累计回答模式</caption><thead><tr><th scope="col">方式</th><th scope="col" class="is-numeric">次数</th><th scope="col" class="is-numeric">占比</th></tr></thead><tbody><tr v-for="row in modeRows" :key="row.name"><th scope="row">{{ row.name }}</th><td class="is-numeric">{{ row.value }}</td><td class="is-numeric">{{ percentage(row.value, ov.qa_total) }}</td></tr></tbody></table></section>
    </div>
    <section class="stats-feedback-block admin-section" aria-labelledby="stats-feedback-title"><div class="admin-section-heading"><div><h2 id="stats-feedback-title">反馈构成</h2><p>当前保留 {{ ov.feedback?.total || 0 }} 条 · 包含全部处理状态</p></div><RouterLink to="/admin/feedbacks" class="text-button">进入反馈队列<AppIcon name="arrow" :size="14" /></RouterLink></div><div class="stats-feedback-content"><div><DataChart :show-data-table="false" v-if="ov.feedback?.total" :option="feedbackOption" label="当前保留反馈的类型和数量；旁侧表格提供相同数据" :height="250" /><EmptyState v-else icon="feedback" title="尚无用户反馈" description="问答提交评价后在此显示。" /></div><table class="admin-data-table stats-compact-table"><caption>当前用户评价</caption><thead><tr><th scope="col">类型</th><th scope="col" class="is-numeric">条数</th><th scope="col" class="is-numeric">占比</th></tr></thead><tbody><tr v-for="row in feedbackRows" :key="row.name"><th scope="row">{{ row.name }}</th><td class="is-numeric">{{ row.value }}</td><td class="is-numeric">{{ percentage(row.value, ov.feedback?.total) }}</td></tr></tbody></table></div></section>
  </template>
</div>
</template>

<script setup>
import './common.css'
import { ElButton, ElSelect, ElOption, ElSkeleton } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/select/style/css.mjs'
import 'element-plus/es/components/option/style/css.mjs'
import 'element-plus/es/components/skeleton/style/css.mjs'
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { kbsApi, statsOverviewApi, statsTrendApi } from '@/api'
import { formatNumber } from '@/utils/format'
import DataChart from '@/components/DataChart.vue'

const ov = ref({}), kbs = ref([]), trend = ref([]), filterKb = ref(null)
const loading = ref(false), trendLoading = ref(false), summaryReady = ref(false), trendReady = ref(false)
const summaryError = ref(''), kbFilterError = ref(''), trendError = ref('')
const error = computed(() => [summaryError.value, kbFilterError.value, trendError.value].filter(Boolean).join('；'))
let trendVersion = 0, loadVersion = 0, alive = true
const requestMessage = (e, fallback) => typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : fallback
async function load() {
  const version = ++loadVersion
  loading.value = true
  summaryError.value = ''
  kbFilterError.value = ''
  const [overviewResult, kbResult] = await Promise.allSettled([statsOverviewApi(), kbsApi()])
  if (version !== loadVersion || !alive) return
  if (overviewResult.status === 'fulfilled') {
    ov.value = overviewResult.value
    summaryReady.value = true
  } else {
    summaryReady.value = false
    summaryError.value = requestMessage(overviewResult.reason, '累计统计读取失败，请重试。')
  }
  if (kbResult.status === 'fulfilled') kbs.value = kbResult.value
  else kbFilterError.value = requestMessage(kbResult.reason, '知识库筛选项读取失败。')
  await loadTrend()
  if (version === loadVersion && alive) loading.value = false
}
async function loadTrend() {
  const version = ++trendVersion
  trendLoading.value = true
  trendReady.value = false
  trendError.value = ''
  try {
    const data = await statsTrendApi(filterKb.value || undefined)
    if (version === trendVersion && alive) { trend.value = data; trendReady.value = true }
  } catch (e) {
    if (version === trendVersion && alive) trendError.value = requestMessage(e, '趋势读取失败，请重新加载。')
  } finally {
    if (version === trendVersion && alive) trendLoading.value = false
  }
}
const metrics = computed(() => [
  { label: '累计问答', value: summaryReady.value ? ov.value.qa_total : null, unit: '次', note: '已完成的真实问答事件' },
  { label: '引用覆盖率', value: summaryReady.value && ov.value.assistant_total > 0 ? ov.value.cite_rate : null, unit: '%', note: !summaryReady.value ? '统计未读取' : ov.value.assistant_total ? ov.value.cited_messages + ' / ' + ov.value.assistant_total + ' 条保留回答含引用' : '尚无保留回答，暂无样本' },
  { label: '文档解析成功率', value: summaryReady.value && ov.value.doc_total > 0 ? ov.value.doc_success_rate : null, unit: '%', note: !summaryReady.value ? '统计未读取' : ov.value.doc_total ? ov.value.doc_success + ' / ' + ov.value.doc_total + ' 份当前文档解析成功' : '尚无文档，暂无样本' },
  { label: '检索式回答', value: summaryReady.value ? ov.value.mode_dist?.degraded ?? 0 : null, unit: '次', note: '未使用生成模型的已完成问答' },
])
const axisLabel = { color: 'var(--color-secondary)', fontSize: 12, hideOverlap: true }
const tooltip = { trigger: 'axis', renderMode: 'richText', textStyle: { fontSize: 14, color: 'var(--color-ink)' }, backgroundColor: 'var(--color-surface)', borderColor: 'var(--color-line)' }
const axisLine = { lineStyle: { color: 'var(--color-line)' } }
const splitLine = { lineStyle: { color: 'var(--color-line)', type: 'dashed' } }
const trendOption = computed(() => ({
  tooltip,
  legend: { bottom: 0, itemWidth: 20, itemHeight: 6, textStyle: { color: 'var(--color-secondary)', fontSize: 12 } },
  grid: { left: 44, right: 20, top: 32, bottom: 64 },
  xAxis: { type: 'category', boundaryGap: false, data: trend.value.map(d => d.date.slice(5)), axisLabel, axisLine, axisTick: { show: false } },
  yAxis: { type: 'value', name: '次', minInterval: 1, axisLabel, splitLine, nameTextStyle: { color: 'var(--color-secondary)', fontSize: 12 } },
  series: [
    { name: '问答量', type: 'line', smooth: false, showSymbol: false, data: trend.value.map(d => d.qa_count), itemStyle: { color: 'var(--color-accent)' }, lineStyle: { width: 2, color: 'var(--color-accent)' } },
    { name: '检索式回答', type: 'line', smooth: false, showSymbol: false, data: trend.value.map(d => d.degraded), itemStyle: { color: 'var(--color-info)' }, lineStyle: { width: 2, type: 'dashed', color: 'var(--color-info)' } },
  ],
}))
const allKbDist = computed(() => [...(ov.value.kb_dist || [])].sort((a, b) => b.qa_count - a.qa_count))
const kbDist = computed(() => allKbDist.value.slice(0, 6).reverse())
const kbOption = computed(() => ({
  tooltip, grid: { left: 112, right: 56, top: 16, bottom: 32 },
  xAxis: { type: 'value', minInterval: 1, axisLabel, splitLine },
  yAxis: { type: 'category', data: kbDist.value.map(k => k.kb_name), axisLabel: { ...axisLabel, width: 94, overflow: 'truncate' }, axisTick: { show: false }, axisLine: { show: false } },
  series: [{ name: '累计问答', type: 'bar', data: kbDist.value.map(k => k.qa_count), barMaxWidth: 16, itemStyle: { color: 'var(--color-accent)', borderRadius: [0, 3, 3, 0] }, label: { show: true, position: 'right', color: 'var(--color-ink)', fontSize: 12 } }],
}))
const modeRows = computed(() => { const m = ov.value.mode_dist || {}; return [
  { name: '本地模型', value: m.local || 0, color: 'var(--color-ink)' },
  { name: '在线模型', value: m.online || 0, color: 'var(--color-accent)' },
  { name: '检索式回答', value: m.degraded || 0, color: 'var(--color-info)' },
  { name: '无结果', value: m.none || 0, color: 'var(--color-muted)' },
] })
const feedbackRows = computed(() => { const f = ov.value.feedback || {}; return [
  { name: '有帮助', value: f.like || 0, color: 'var(--color-success)' },
  { name: '回答不准确', value: f.dislike || 0, color: 'var(--color-danger)' },
  { name: '缺少依据', value: f.lack_source || 0, color: 'var(--color-warning)' },
  { name: '内容不相关', value: f.no_relevant || 0, color: 'var(--color-info)' },
] })
const distributionOption = rows => ({
  tooltip, grid: { left: 96, right: 52, top: 16, bottom: 32 },
  xAxis: { type: 'value', minInterval: 1, axisLabel, splitLine },
  yAxis: { type: 'category', inverse: true, data: rows.map(row => row.name), axisLabel: { ...axisLabel, width: 82, overflow: 'break' }, axisTick: { show: false }, axisLine: { show: false } },
  series: [{ name: '记录数', type: 'bar', barMaxWidth: 16, data: rows.map(row => ({ value: row.value, itemStyle: { color: row.color } })), label: { show: true, position: 'right', color: 'var(--color-ink)', fontSize: 12 } }],
})
const modeOption = computed(() => distributionOption(modeRows.value))
const feedbackOption = computed(() => distributionOption(feedbackRows.value))
const trendScope = computed(() => filterKb.value ? kbs.value.find(k => k.id === filterKb.value)?.name || '所选知识库' : '全部可见知识库')
const trendTotal = computed(() => trend.value.reduce((total, point) => total + point.qa_count, 0))
const percentage = (value, total) => total > 0 ? (value / total * 100).toFixed(1) + '%' : '—'
onMounted(load)
onBeforeUnmount(() => { alive = false; loadVersion++; trendVersion++ })
</script>

<style scoped>
.stats-definitions { margin: -8px 0 32px; border-block: 1px solid var(--color-line); }
.stats-definitions dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; margin: 0 0 24px; }
.stats-definitions dt { margin-bottom: 8px; font-weight: 600; font-size: 14px; color: var(--color-ink); }
.stats-definitions dd { margin: 0; font-size: 14px; line-height: 1.8; color: var(--color-secondary); }
.stats-definitions > p { font-size: 14px; color: var(--color-secondary); margin-bottom: 24px; }
.stats-filter { display: grid; gap: 8px; width: 220px; max-width: 100%; }
.stats-filter label { color: var(--color-secondary); font-size: 12px; }
.stats-filter .el-select { min-width: 0; width: 100%; }
.stats-chart-body { min-width: 0; padding: 16px; }
.stats-table-disclosure { border-top: 1px solid var(--color-line); }
.stats-table-disclosure > summary { padding: 16px 24px; }
.stats-daily-table { min-width: 860px; }
.stats-daily-table th { white-space: nowrap; }
.stats-distributions { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; margin: 32px 0; }
.stats-block { min-width: 0; padding-top: 24px; border-top: 1px solid var(--color-line); }
.stats-compact-table { table-layout: fixed; }
.stats-compact-table :is(th, td) { overflow-wrap: anywhere; }
.stats-compact-table tbody th { font-weight: 500; }
.stats-compact-table .text-button { padding: 0; display: inline; font-weight: 500; }
.stats-feedback-block { padding-top: 24px; border-top: 1px solid var(--color-line); }
.stats-feedback-content { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 24px; align-items: start; }
.stats-feedback-content > div { min-width: 0; }
@media (max-width: 1023px) { .stats-distributions, .stats-feedback-content { grid-template-columns: minmax(0, 1fr); gap: 24px; } }
@media (max-width: 767px) { .stats-definitions dl { grid-template-columns: minmax(0, 1fr); } .statistics-workbench :deep(.panel-head) { flex-wrap: wrap; } .stats-filter { width: 100%; } }
@media (max-width: 479px) { .stats-chart-body { padding: 8px; } .stats-table-disclosure > summary { padding-inline: 16px; } .stats-compact-table :is(th, td) { padding: 12px 8px; } }
</style>

