<template>
<div class="page admin-page task-workbench">
  <PageHeading title="导入任务">
    <el-button :loading="loading" @click="load"><AppIcon name="refresh" :size="16" />刷新</el-button>
    <el-button type="primary" @click="router.push('/admin/kbs')"><AppIcon name="upload" :size="16" />上传论文</el-button>
  </PageHeading>
  <p class="task-intro">从上传到可检索，查看资料的每一步处理状态。失败任务保留原因，不影响已就绪资料。</p>
  <div v-if="error" class="error-strip" role="alert"><AppIcon name="warning" :size="16" /><span>{{ error }}</span><button type="button" class="text-button" @click="load">重新加载</button></div>
  <div v-if="kbFilterError" class="error-strip" role="alert"><span>{{ kbFilterError }}</span><button type="button" class="text-button" @click="loadKbs">重试筛选项</button></div>

  <section class="panel admin-section" aria-labelledby="task-log-title">
    <div class="panel-head"><div><h2 id="task-log-title">导入记录</h2><p>{{ loaded ? '符合筛选的任务共 ' + total + ' 条' : '正在等待任务数据' }}</p></div><div class="auto-refresh"><label for="task-auto-refresh">自动更新</label><el-switch id="task-auto-refresh" v-model="autoRefresh" aria-label="自动更新导入任务" /></div></div>
    <div class="toolbar admin-toolbar"><div class="toolbar-group">
      <div class="admin-filter-field admin-filter-search"><label for="task-search">文档名称</label><el-input id="task-search" v-model="search" placeholder="搜索任务文档" clearable><template #prefix><AppIcon name="search" :size="16" /></template></el-input></div>
      <div class="admin-filter-field"><label for="task-kb">知识库</label><el-select id="task-kb" v-model="filterKb" clearable placeholder="全部知识库"><el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" /></el-select></div>
      <div class="admin-filter-field"><label for="task-phase">当前阶段</label><el-select id="task-phase" v-model="phase" placeholder="全部阶段"><el-option label="全部阶段" value="" /><el-option v-for="item in phases" :key="item.value" :label="item.label" :value="item.value" /></el-select></div>
    </div></div>
    <p id="task-table-note" class="sr-only">窄屏可在任务表格内横向滚动。</p>
    <div class="admin-table-scroll" role="region" tabindex="0" aria-label="导入任务台账" aria-describedby="task-table-note" style="--admin-table-min-width: 1200px">
      <el-table :data="tasks" row-key="id" v-loading="loading">
        <el-table-column label="任务 / 文档" min-width="260"><template #default="{ row }"><div class="admin-record-key">TASK / {{ row.id }}</div><div class="admin-record-title">{{ row.filename }}</div><div class="admin-record-meta">{{ row.kb_name || '知识库文档' }}</div></template></el-table-column>
        <el-table-column label="当前阶段" width="120"><template #default="{ row }"><StatusBadge :status="row.phase" /></template></el-table-column>
        <el-table-column label="进度" width="170"><template #default="{ row }"><el-progress v-if="Number.isFinite(row.progress)" :percentage="row.progress" :stroke-width="6" :status="row.phase === 'failed' ? 'exception' : row.phase === 'success' ? 'success' : undefined" :aria-label="'任务 ' + row.id + ' 处理进度 ' + row.progress + '%'" /><span v-else class="secondary">进度未返回</span></template></el-table-column>
        <el-table-column label="片段数" prop="chunk_count" width="90" align="right" />
        <el-table-column label="提交时间" width="150"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="处理记录" min-width="250"><template #default="{ row }"><p class="task-log-message" :class="{ 'admin-error-text': row.phase === 'failed' }">{{ row.error_message || (row.phase === 'success' ? '知识索引已就绪' : row.phase === 'queued' ? '等待后台处理' : '任务正在处理中…') }}</p><span v-if="row.phase === 'failed'" class="admin-record-meta">先修正原因，再重新解析系统保存的文件。</span></template></el-table-column>
        <el-table-column label="操作" width="160"><template #default="{ row }"><div class="admin-action-group"><el-button text type="primary" @click="router.push('/admin/kbs/' + row.kb_id)">进入资料</el-button><el-button v-if="row.phase === 'failed' && canRetry(row)" text :disabled="retrying.has(row.id)" :loading="retrying.has(row.id)" @click="retry(row)">重试</el-button></div></template></el-table-column>
        <template #empty><p v-if="loading" class="secondary" role="status">正在读取导入任务…</p><EmptyState v-else-if="error" icon="warning" title="任务记录未能加载" description="请检查连接后重试。" /><EmptyState v-else icon="tasks" title="暂无符合条件的任务" description="可以调整筛选，或进入知识库上传资料。" /></template>
      </el-table>
    </div>
    <div class="table-footer admin-pagination"><div><span>共 {{ total }} 个任务 · 第 {{ page }} 页</span><p class="poll-note" v-if="activeCount && autoRefresh">当前页 {{ activeCount }} 个任务处理中 · 页面可见时每 4 秒更新</p><p class="poll-note" v-else-if="!autoRefresh">自动更新已关闭。</p></div><el-pagination background layout="prev, pager, next" v-model:current-page="page" :total="total" :page-size="size" :pager-count="5" /></div>
  </section>
</div>
</template>

<script setup>
import './common.css'
import { ElButton, ElInput, ElSelect, ElOption, ElSwitch, ElTable, ElTableColumn, ElProgress, ElPagination } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/input/style/css.mjs'
import 'element-plus/es/components/select/style/css.mjs'
import 'element-plus/es/components/option/style/css.mjs'
import 'element-plus/es/components/switch/style/css.mjs'
import 'element-plus/es/components/table/style/css.mjs'
import 'element-plus/es/components/table-column/style/css.mjs'
import 'element-plus/es/components/progress/style/css.mjs'
import 'element-plus/es/components/pagination/style/css.mjs'

import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { kbsApi, tasksPageApi, reparseDocApi } from '@/api'
import { formatDate } from '@/utils/format'
const router=useRouter(),kbs=ref([]),tasks=ref([]),filterKb=ref(null),phase=ref(''),search=ref(''),page=ref(1),size=20,total=ref(0),autoRefresh=ref(true),loading=ref(false),error=ref(''),retrying=ref(new Set())
const loaded=ref(false), kbFilterError=ref('')
const phases=[{value:'queued',label:'排队中'},{value:'extracting',label:'内容抽取'},{value:'chunking',label:'文档分段'},{value:'vectorizing',label:'构建索引'},{value:'success',label:'已完成'},{value:'failed',label:'处理失败'}]
const activeCount=computed(()=>tasks.value.filter(t=>!['success','failed'].includes(t.phase)).length)
let timer,debounce,version=0,alive=true
function schedule(){clearTimeout(timer);if(autoRefresh.value&&activeCount.value&&alive)timer=setTimeout(()=>{if(document.visibilityState==='visible')load(true);else schedule()},4000)}
async function load(quiet=false){quiet=quiet===true;const current=++version;if(!quiet)loading.value=true;error.value='';try{const data=await tasksPageApi({kb_id:filterKb.value||undefined,phase:phase.value||undefined,q:search.value.trim()||undefined,page:page.value,size});if(current!==version||!alive)return;tasks.value=data.items;total.value=data.total;loaded.value=true;schedule()}catch(e){if(current===version)error.value=typeof e?.response?.data?.detail==='string'?e.response.data.detail:'任务加载失败，请检查连接后重试。'}finally{if(current===version)loading.value=false}}
function filterChanged(){if(page.value!==1)page.value=1;else load()}
watch([filterKb,phase],filterChanged);watch(search,()=>{clearTimeout(debounce);debounce=setTimeout(filterChanged,300)});watch(page,()=>load());watch(autoRefresh,schedule)
function canRetry(task) { return kbs.value.some(kb => kb.id === task.kb_id && kb.can_manage === true) }
async function retry(task) {
  if (!canRetry(task) || retrying.value.has(task.id)) return
  try {
    await ElMessageBox.confirm('修正错误原因后，可创建新的导入任务。', '重新解析「' + task.filename + '」？', { confirmButtonText: '重新解析', cancelButtonText: '取消' })
    retrying.value.add(task.id)
    await reparseDocApi(task.kb_id, task.document_id)
    ElMessage.success('新的导入任务已提交')
    phase.value = ''
    await load()
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') error.value = typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : '重新解析未能提交，请检查连接或权限后重试。'
  } finally {
    retrying.value.delete(task.id)
  }
}
async function loadKbs(){kbFilterError.value='';try{const data=await kbsApi();if(alive)kbs.value=data}catch{if(alive)kbFilterError.value='知识库筛选项读取失败；仍可使用任务搜索和阶段筛选。'}}
onMounted(async()=>{await loadKbs();if(alive)load()});onBeforeUnmount(()=>{alive=false;version++;clearTimeout(timer);clearTimeout(debounce)})
</script>

<style scoped>
.auto-refresh { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 13px; color: var(--color-secondary); }
.task-log-message { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.poll-note { margin: 4px 0 0; color: var(--color-secondary); font-size: 12px; }
@media (max-width: 767px) { .task-workbench :deep(.panel-head) { flex-wrap: wrap; } }

.task-intro { margin: -8px 0 24px; color: var(--color-secondary); font-size: 14px; line-height: 1.7; max-width: 760px; }
.task-workbench .panel { border-radius: 12px; }
</style>


