<template>
<div class="page admin-page overview-page">
  <PageHeading title="概览">
    <el-button :loading="loading" @click="load"><AppIcon name="refresh" :size="16" />刷新</el-button>
    <el-button @click="router.push({ path: '/admin/kbs', query: { create: '1' } })"><AppIcon name="plus" :size="16" />新建知识库</el-button>
    <el-button type="primary" @click="router.push('/')">论文问答<AppIcon name="arrow" :size="16" /></el-button>
  </PageHeading>
  <div v-if="error" class="error-strip" role="alert"><AppIcon name="warning" :size="16" /><span>部分信息未能读取：{{ error }}。已读取区块仍可使用。</span><button type="button" class="text-button" :disabled="loading" @click="load">重新加载</button></div>
  <div class="metric-grid" aria-label="知识与使用摘要" v-loading="loading"><div v-for="m in metrics" :key="m.label" class="metric-card"><div class="metric-label">{{ m.label }}</div><div class="metric-value">{{ m.value == null ? '—' : formatNumber(m.value) }}<small v-if="m.value != null">{{ m.unit }}</small></div><div class="metric-note">{{ m.note }}</div></div></div>

  <div class="overview-columns">
    <div class="overview-main">
      <section class="panel admin-section" aria-labelledby="overview-task-title">
        <div class="panel-head"><div><h2 id="overview-task-title">最近导入</h2><p>最近 {{ ready('tasks') ? recentTasks.length : '—' }} 条 · 失败和处理中任务优先列出</p></div><RouterLink to="/admin/tasks" class="text-button">全部任务<AppIcon name="arrow" :size="14" /></RouterLink></div>
        <div v-if="ready('tasks') && recentTasks.length" class="overview-task-summary"><span :class="{ 'has-failure': failedCount }"><b>{{ failedCount }}</b> 条失败待核查</span><span><b>{{ activeCount }}</b> 条处理中</span><small>仅统计上述最近任务，不是全部待办数量</small></div>
        <div v-if="loading && !ready('tasks')" class="admin-empty-loading" role="status"><p>正在读取最近任务…</p><el-skeleton :rows="4" /></div>
        <EmptyState v-else-if="!ready('tasks')" icon="warning" title="最近任务暂不可读" description="请重新加载，或进入任务中心核对。" />
        <template v-else><p id="overview-task-scroll" class="sr-only">窄屏可在记录表内横向滚动。点击文档名进入对应知识库资料。</p><div class="admin-table-scroll" role="region" tabindex="0" aria-label="最近导入" aria-describedby="overview-task-scroll" style="--admin-table-min-width: 730px"><el-table :data="attentionTasks" row-key="id"><el-table-column label="文档 / 任务" min-width="250"><template #default="{ row }"><RouterLink class="admin-record-title" :to="'/admin/kbs/' + row.kb_id">{{ row.filename }}</RouterLink><div class="admin-record-meta">任务 #{{ row.id }}</div><div v-if="row.phase === 'failed' && row.error_message" class="admin-record-meta admin-error-text">{{ row.error_message }}</div></template></el-table-column><el-table-column label="当前阶段" width="120"><template #default="{ row }"><StatusBadge :status="row.phase" /></template></el-table-column><el-table-column prop="chunk_count" label="片段数" width="90" align="right" /><el-table-column label="提交时间" width="150"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column><template #empty><EmptyState icon="tasks" title="尚无解析任务" description="上传文件后，可在这里核对实际处理阶段。" /></template></el-table></div></template>
      </section>

      <section class="admin-section" aria-labelledby="recent-kbs-title">
        <div class="admin-section-heading"><div><h2 id="recent-kbs-title">最近知识库</h2></div><RouterLink to="/admin/kbs" class="text-button">完整目录<AppIcon name="arrow" :size="14" /></RouterLink></div>
        <div v-if="loading && !ready('kbs')" class="admin-empty-loading" role="status"><p>正在读取知识库目录…</p><el-skeleton :rows="3" /></div>
        <EmptyState v-else-if="!ready('kbs')" icon="warning" title="知识库目录暂不可读" description="请重试加载，确认当前账号的访问范围。" />
        <ol v-else-if="recentKbs.length" class="overview-kb-directory"><li v-for="kb in recentKbs" :key="kb.id"><span class="admin-record-key">KB / {{ String(kb.id).padStart(3, '0') }}</span><div><RouterLink :to="'/admin/kbs/' + kb.id" class="admin-record-title">{{ kb.name }}</RouterLink><p v-if="kb.description">{{ kb.description }}</p><span class="admin-record-meta">{{ kb.doc_count }} 份文档 · {{ kb.chunk_count }} 个片段 · {{ formatDate(kb.created_at, true) }} 创建</span></div><StatusBadge :status="kb.status" /><RouterLink :to="'/admin/kbs/' + kb.id" class="text-button" :aria-label="'进入知识库：' + kb.name">进入<AppIcon name="arrow" :size="14" /></RouterLink></li></ol>
        <EmptyState v-else icon="book" title="当前没有可见知识库" description="新建知识库后上传论文。"><el-button @click="router.push('/admin/kbs')">管理知识库</el-button></EmptyState>
      </section>
    </div>

    <aside class="overview-aside">
      <section class="overview-trend-section admin-section" aria-labelledby="overview-trend-title"><div class="admin-section-heading"><h2 id="overview-trend-title">近 7 天问答</h2><RouterLink to="/admin/stats" class="text-button">统计</RouterLink></div><p class="trend-total"><b>{{ ready('trend') ? formatNumber(weekTotal) : '—' }}</b><span>次 · 北京时间</span></p><template v-if="ready('trend') && weekTrend.length"><TrendSparkline :data="weekTrend" class="overview-trend" /><details class="admin-disclosure"><summary>查看每日数据</summary><table class="admin-data-table"><caption>最近 7 天已完成的问答事件</caption><thead><tr><th scope="col">日期</th><th scope="col" class="is-numeric">问答次数</th></tr></thead><tbody><tr v-for="point in weekTrend" :key="point.date"><th scope="row">{{ point.date }}</th><td class="is-numeric">{{ point.qa_count }}</td></tr></tbody></table></details></template><p v-else class="secondary">{{ loading ? '正在读取趋势…' : ready('trend') ? '当前没有趋势数据。' : '趋势数据未读取，请刷新。' }}</p></section>
    </aside>
  </div>
</div>
</template>

<script setup>
import './common.css'
import { ElButton, ElTable, ElTableColumn, ElSkeleton } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/table/style/css.mjs'
import 'element-plus/es/components/table-column/style/css.mjs'
import 'element-plus/es/components/skeleton/style/css.mjs'
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { kbsApi, statsOverviewApi, statsTrendApi, tasksApi } from '@/api'
import { formatNumber, formatDate } from '@/utils/format'
import TrendSparkline from '@/components/TrendSparkline.vue'

const router = useRouter()
const kbs = ref([]), overview = ref({}), trend = ref([]), recentTasks = ref([])
const loading = ref(false), problems = ref([])
const states = ref({ kbs: 'idle', stats: 'idle', trend: 'idle', tasks: 'idle' })
const error = computed(() => problems.value.join('；'))
const ready = key => states.value[key] === 'ready'
let requestVersion = 0, alive = true
async function load() {
  const current = ++requestVersion
  loading.value = true
  problems.value = []
  const jobs = [
    { key: 'kbs', label: '知识库', request: () => kbsApi(), apply: value => { kbs.value = value } },
    { key: 'stats', label: '统计摘要', request: () => statsOverviewApi(), apply: value => { overview.value = value } },
    { key: 'trend', label: '问答趋势', request: () => statsTrendApi(), apply: value => { trend.value = value } },
    { key: 'tasks', label: '解析任务', request: () => tasksApi(undefined, { limit: 4 }), apply: value => { recentTasks.value = value.slice(0, 4) } },
  ]
  const results = await Promise.allSettled(jobs.map(job => job.request()))
  if (current !== requestVersion || !alive) return
  results.forEach((result, index) => {
    const job = jobs[index]
    if (result.status === 'fulfilled') {
      job.apply(result.value)
      states.value[job.key] = 'ready'
    } else {
      states.value[job.key] = 'failed'
      const detail = result.reason?.response?.data?.detail
      problems.value.push(job.label + '：' + (typeof detail === 'string' ? detail : '读取失败，请重试'))
    }
  })
  loading.value = false
}
const weekTrend = computed(() => trend.value.slice(-7))
const weekTotal = computed(() => weekTrend.value.reduce((sum, point) => sum + point.qa_count, 0))
const recentKbs = computed(() => [...kbs.value].sort((a, b) => b.id - a.id).slice(0, 3))
const failedCount = computed(() => recentTasks.value.filter(task => task.phase === 'failed').length)
const activeCount = computed(() => recentTasks.value.filter(task => !['success', 'failed'].includes(task.phase)).length)
const taskPriority = phase => phase === 'failed' ? 0 : phase === 'success' ? 2 : 1
const attentionTasks = computed(() => [...recentTasks.value].sort((a, b) => taskPriority(a.phase) - taskPriority(b.phase)))
const metrics = computed(() => [
  { label: '可见知识库', value: ready('kbs') ? kbs.value.length : null, unit: '个', note: ready('kbs') ? kbs.value.filter(k => k.status === 'enabled').length + ' 个已启用' : '目录尚未读取' },
  { label: '知识文档', value: ready('stats') ? overview.value.doc_total : null, unit: '份', note: ready('stats') ? formatNumber(overview.value.doc_success) + ' 份已解析完成' : '统计尚未读取' },
  { label: '累计问答', value: ready('stats') ? overview.value.qa_total : null, unit: '次', note: '已完成的问答事件' },
  { label: '引用覆盖率', value: ready('stats') && overview.value.assistant_total > 0 ? overview.value.cite_rate : null, unit: '%', note: !ready('stats') ? '统计尚未读取' : overview.value.assistant_total ? overview.value.cited_messages + ' / ' + overview.value.assistant_total + ' 条回答含引用' : '尚无保留回答，暂无样本' },
])
onMounted(load)
onBeforeUnmount(() => { alive = false; requestVersion++ })
</script>

<style scoped>
.overview-columns { display: grid; grid-template-columns: minmax(0, 1fr) minmax(240px, 288px); gap: 24px; align-items: start; }
.overview-main, .overview-aside { min-width: 0; }
.overview-aside { padding-left: 20px; border-left: 1px solid var(--color-line); }
.overview-task-summary { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; padding: 12px 20px; border-bottom: 1px solid var(--color-line); font-size: 13px; color: var(--color-secondary); }
.overview-task-summary b { font-variant-numeric: tabular-nums; }
.overview-task-summary .has-failure { color: var(--color-danger); }
.overview-task-summary small { font-size: 12px; color: var(--color-muted); }
.overview-kb-directory { list-style: none; padding: 0; margin: 0; border-top: 1px solid var(--color-line); }
.overview-kb-directory li { display: grid; grid-template-columns: 64px minmax(0, 1fr) auto auto; gap: 12px; align-items: center; padding: 16px 0; border-bottom: 1px solid var(--color-line); }
.overview-kb-directory li > div { min-width: 0; }
.overview-kb-directory p { margin: 4px 0; color: var(--color-secondary); font-size: 13px; }
.trend-total { display: flex; align-items: baseline; gap: 8px; margin-bottom: 16px; }
.trend-total b { font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; }
.trend-total span { font-size: 12px; color: var(--color-secondary); }
.overview-trend { width: 100%; height: 120px; }
@media (max-width: 1199px) { .overview-columns { grid-template-columns: minmax(0, 1fr); gap: 0; } .overview-aside { border-left: 0; padding-left: 0; } }
@media (max-width: 639px) { .overview-kb-directory li { grid-template-columns: minmax(0, 1fr) auto; } .overview-kb-directory li > .admin-record-key { display: none; } .overview-kb-directory li > .text-button { grid-column: 2; } .overview-task-summary { padding-inline: 16px; } }
</style>

