<template>
<div class="page admin-page feedback-workbench">
  <PageHeading title="问答反馈">
    <el-button :loading="loading" @click="load"><AppIcon name="refresh" :size="16" />刷新反馈</el-button>
  </PageHeading>
  <div v-if="error" class="error-strip" role="alert"><AppIcon name="warning" :size="16" /><span>{{ error }}</span><button type="button" class="text-button" @click="load">重新加载</button></div>

  <section class="panel admin-section" aria-label="反馈队列">
    <div class="toolbar admin-toolbar">
      <div class="feedback-tabs" role="group" aria-label="反馈处理状态"><button v-for="tab in tabs" :key="tab.value" type="button" :class="{ active: status === tab.value }" :aria-pressed="status === tab.value" @click="status = tab.value">{{ tab.label }}</button></div>
      <div class="admin-filter-field feedback-search"><label for="feedback-search">原问题、意见或用户名</label><el-input id="feedback-search" v-model="search" clearable placeholder="搜索反馈内容"><template #prefix><AppIcon name="search" :size="16" /></template></el-input></div>
    </div>
    <p id="feedback-table-note" class="sr-only">点击问题查看原问答。窄屏可在反馈表内横向滚动。</p>
    <div class="admin-table-scroll" role="region" tabindex="0" aria-label="反馈问题台账" aria-describedby="feedback-table-note" style="--admin-table-min-width: 1140px">
      <el-table :data="list" row-key="id" v-loading="loading">
        <el-table-column label="原问题 / 用户意见" min-width="365"><template #default="{ row }"><div class="admin-record-key">FEEDBACK / {{ row.id }}</div><button type="button" class="feedback-question" @click="showDetail(row)">{{ row.question || '查看原始问答' }}</button><div class="feedback-comment">{{ row.comment || '用户未补充说明' }}</div></template></el-table-column>
        <el-table-column label="反馈类型" width="130"><template #default="{ row }"><span class="feedback-type" :class="{ 'is-helpful': row.feedback_type === 'like' }"><AppIcon :name="row.feedback_type === 'like' ? 'like' : 'feedback'" :size="14" />{{ feedbackLabels[row.feedback_type] || row.feedback_type }}</span></template></el-table-column>
        <el-table-column label="提交用户" prop="username" min-width="120" />
        <el-table-column label="处理状态" width="130"><template #default="{ row }"><span v-if="row.status === 'handled'" class="badge">已处理 · 记录</span><StatusBadge v-else :status="row.status" :label="row.status === 'pending' ? '待处理' : ''" /></template></el-table-column>
        <el-table-column label="提交时间" width="150"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="190"><template #default="{ row }"><div class="admin-action-group"><el-button text type="primary" @click="showDetail(row)">{{ row.status === 'pending' ? '查看并处理' : '查看详情' }}</el-button><el-button v-if="row.status !== 'pending'" text @click="reopen(row)">重开</el-button></div></template></el-table-column>
        <template #empty><p v-if="loading" class="secondary" role="status">正在读取反馈队列…</p><EmptyState v-else-if="error" icon="warning" title="反馈未能加载" description="请检查连接后重新加载。" /><EmptyState v-else icon="feedback" :title="status === 'pending' && !search ? '当前没有待处理的反馈' : '没有符合条件的反馈'" description="可调整筛选，或稍后再查看。" /></template>
      </el-table>
    </div>
    <div class="table-footer admin-pagination"><span>{{ loaded ? '共 ' + total + ' 条反馈 · 第 ' + page + ' 页' : '等待反馈数据' }}</span><el-pagination background layout="prev, pager, next" v-model:current-page="page" :page-size="size" :total="total" :pager-count="5" /></div>
  </section>

  <el-drawer v-model="detailVisible" class="admin-drawer feedback-drawer" :title="selected ? '反馈 #' + selected.id : '反馈详情'" size="min(760px, 100vw)" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <template v-if="selected">
      <div class="feedback-detail-meta"><span class="feedback-type">{{ feedbackLabels[selected.feedback_type] || selected.feedback_type }}</span><span v-if="selected.status === 'handled'" class="badge">已处理 · 记录</span><StatusBadge v-else :status="selected.status" :label="selected.status === 'pending' ? '待处理' : ''" /><span>{{ selected.username }} · {{ formatDate(selected.created_at) }}</span></div>
      <section class="admin-detail-section"><h3>原始问题</h3><blockquote class="feedback-original-question">{{ selected.question || '原始问题不可用' }}</blockquote><h4>关联回答</h4><div class="feedback-answer"><MarkdownContent :content="selected.answer || '原始回答不可用'" /></div></section>
      <section class="admin-detail-section"><h3>补充说明</h3><p class="feedback-opinion">{{ selected.comment || '用户未填写补充意见。' }}</p></section>
      <section class="admin-detail-section"><h3>核查与处理</h3>
        <div v-if="selected.handler_note" class="handled-note"><b>已有处理备注</b><p>{{ selected.handler_note }}</p></div>
        <p v-else class="secondary">尚无处理备注。</p>
        <div v-if="selected.status === 'pending'" class="feedback-note-editor"><label for="feedback-note">处理备注</label><el-input id="feedback-note" ref="noteInput" v-model="note" type="textarea" :rows="4" maxlength="1000" show-word-limit aria-describedby="feedback-note-help" placeholder="记录核对了哪些资料、发现了什么问题、采取了哪些措施。" /><p id="feedback-note-help">标记已处理前须填写核查结果；处理记录不代表已完成答案复测。</p></div>
        <div v-if="actionError" class="error-strip" role="alert">{{ actionError }}</div>
      </section>
    </template>
    <template #footer><div class="dialog-actions"><el-button :disabled="saving" @click="detailVisible = false">返回反馈队列</el-button><template v-if="selected?.status === 'pending'"><el-button :loading="saving" @click="handle('closed')">关闭此反馈</el-button><el-button type="primary" :loading="saving" @click="handle('handled')">记录为已处理</el-button></template></div></template>
  </el-drawer>
</div>
</template>

<script setup>
import './common.css'
import { ElButton, ElInput, ElTable, ElTableColumn, ElPagination, ElDrawer } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/input/style/css.mjs'
import 'element-plus/es/components/table/style/css.mjs'
import 'element-plus/es/components/table-column/style/css.mjs'
import 'element-plus/es/components/pagination/style/css.mjs'
import 'element-plus/es/components/drawer/style/css.mjs'

import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { feedbacksPageApi, handleFeedbackApi } from '@/api'
import { formatDate } from '@/utils/format'
import MarkdownContent from '@/components/MarkdownContent.vue'
const status=ref('pending'),search=ref(''),list=ref([]),page=ref(1),size=20,total=ref(0),loading=ref(false),error=ref(''),detailVisible=ref(false),selected=ref(null),note=ref(''),saving=ref(false)
const loaded=ref(false), actionError=ref(''), noteInput=ref()
const feedbackLabels={like:'有帮助',dislike:'回答不准确',no_relevant:'内容不相关',lack_source:'缺少依据'},tabs=[{value:'pending',label:'待处理'},{value:'handled',label:'已处理'},{value:'closed',label:'已关闭'},{value:'',label:'全部反馈'}]
let debounce,version=0,alive=true
async function load(){const current=++version;loading.value=true;error.value='';try{const data=await feedbacksPageApi({status:status.value||undefined,q:search.value.trim()||undefined,page:page.value,size});if(current!==version||!alive)return;list.value=data.items;total.value=data.total;loaded.value=true;if(page.value>1&&!data.items.length)page.value=Math.max(1,Math.ceil(data.total/size))}catch(e){if(current===version)error.value=typeof e?.response?.data?.detail==='string'?e.response.data.detail:'反馈加载失败，请检查连接后重试。'}finally{if(current===version)loading.value=false}}
function changeFilter(){if(page.value!==1)page.value=1;else load()}
watch(status,changeFilter);watch(search,()=>{clearTimeout(debounce);debounce=setTimeout(changeFilter,300)});watch(page,load)
function showDetail(row){actionError.value='';selected.value=row;note.value='';detailVisible.value=true}
async function handle(action) {
  if (saving.value || !selected.value) return
  actionError.value = ''
  if (action === 'handled' && !note.value.trim()) {
    actionError.value = '请填写处理备注，方便后续追溯。'
    noteInput.value?.focus()
    return ElMessage.warning(actionError.value)
  }
  saving.value = true
  try {
    await handleFeedbackApi(selected.value.id, action, note.value.trim())
    ElMessage.success(action === 'handled' ? '反馈已处理' : '反馈已关闭')
    detailVisible.value = false
    await load()
  } catch (e) {
    actionError.value = typeof e?.response?.data?.detail === 'string' ? e.response.data.detail : '处理未能保存，请检查连接后重试。'
  } finally {
    saving.value = false
  }
}
async function reopen(row){try{await ElMessageBox.confirm('这条反馈将恢复为待处理，已有备注保留以供核查。','重新打开反馈 #'+row.id+'？',{confirmButtonText:'重新打开',cancelButtonText:'取消'});await handleFeedbackApi(row.id,'pending','');ElMessage.success('已恢复为待处理');await load()}catch{}}
onMounted(load);onBeforeUnmount(()=>{alive=false;version++;clearTimeout(debounce)})
</script>

<style scoped>
.feedback-tabs { display: flex; flex-wrap: wrap; gap: 4px 16px; align-self: stretch; align-items: flex-end; }
.feedback-tabs button { border: 0; border-bottom: 2px solid transparent; background: transparent; padding: 12px 0; min-height: 44px; color: var(--color-secondary); font-size: 14px; }
.feedback-tabs button.active { border-bottom-color: var(--color-accent); color: var(--color-accent); font-weight: 600; }
.feedback-search { flex: 0 1 320px; }
.feedback-question { display: block; border: 0; background: none; color: var(--color-ink); text-align: left; padding: 4px 0; font-size: 14px; font-weight: 600; line-height: 1.65; overflow-wrap: anywhere; }
.feedback-question:hover { color: var(--color-accent); text-decoration: underline; text-underline-offset: 4px; }
.feedback-comment { margin-top: 4px; color: var(--color-secondary); font-size: 14px; line-height: 1.55; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.feedback-type { display: inline-flex; align-items: center; gap: 4px; color: var(--color-warning); font-size: 12px; line-height: 1.65; }
.feedback-type.is-helpful { color: var(--color-success); }
.feedback-detail-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; font-size: 12px; color: var(--color-secondary); padding-bottom: 24px; }
.feedback-drawer .admin-section-label { display: block; margin-bottom: 8px; }
.feedback-original-question { margin: 0 0 24px; padding: 16px; border-left: 3px solid var(--color-accent); background: var(--color-page); color: var(--color-ink); font-size: 16px; line-height: 1.85; white-space: pre-wrap; overflow-wrap: anywhere; }
.feedback-answer { min-width: 0; color: var(--color-ink); font-size: 16px; overflow-wrap: anywhere; }
.feedback-answer :deep(pre), .feedback-answer :deep(table) { max-width: 100%; }
.feedback-opinion, .handled-note p { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 16px; line-height: 1.85; }
.handled-note { padding: 16px; background: var(--color-page); border-left: 2px solid var(--color-control-border); }
.handled-note b { font-size: 14px; }
.handled-note p { margin: 8px 0 0; }
.feedback-note-editor { display: grid; gap: 8px; margin-top: 24px; }
.feedback-note-editor label { font-size: 14px; font-weight: 600; }
.feedback-note-editor p { margin: 0; color: var(--color-secondary); font-size: 12px; }
@media (max-width: 900px) { .feedback-search { flex-basis: 100%; } }
@media (max-width: 479px) { .feedback-tabs { width: 100%; gap: 4px 16px; } }
</style>


