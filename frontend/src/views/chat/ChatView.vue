<template>
  <div class="chat-workbench" :style="{ '--reader-font-size': readerFont + 'px' }">
    <ResizableWorkspace v-if="user.user" ref="workspace" :storage-key="'paper-workspace-layout-v1-' + user.user.id" :landing="isLanding">
      <template #toolbar="{ toggleSidebar, sidebarOpen, toggleInspector, inspectorOpen, toggleFocus, focused, reset }">
        <div class="conversation-toolbar" aria-label="会话工具栏">
          <div class="conversation-navigation">
            <button type="button" class="paper-button icon-button" aria-label="切换论文与会话栏" :aria-pressed="sidebarOpen" @click="toggleSidebar"><AppIcon name="book" :size="18" /></button>
            <button v-if="!sidebarOpen || sidebarTab !== 'sessions'" type="button" class="paper-button toolbar-new" :disabled="asking || initialLoading" @click="newSession"><AppIcon name="plus" :size="17" /><span>新对话</span></button>
          </div>
          <h1 class="conversation-title" tabindex="0" :title="currentSession?.title || currentKb?.name || '知识库问答'">{{ currentSession?.title || currentKb?.name || '知识库问答' }}</h1>
          <div class="conversation-tools">
            <button type="button" class="paper-button translation-tool" aria-label="中英翻译" @click="openTranslation()"><AppIcon name="globe" :size="17" /><span>翻译</span></button>
            <details class="toolbar-menu" name="chat-toolbar-menu" @keydown.esc="closeToolMenu($event)">
              <summary class="paper-button"><AppIcon name="template" :size="17" /><span>阅读设置</span><AppIcon name="down" :size="14" /></summary>
              <div class="toolbar-popover reading-options">
                <div class="font-setting"><span>正文字号</span><button type="button" class="paper-button icon-button" aria-label="缩小回答字号" :disabled="readerFont <= 14" @click="changeFont(-1)">A−</button><output aria-live="polite">{{ readerFont }}px</output><button type="button" class="paper-button icon-button" aria-label="放大回答字号" :disabled="readerFont >= 20" @click="changeFont(1)">A+</button></div>
                <button type="button" class="paper-button" :aria-pressed="focused" @click="toggleFocus(); closeToolMenu($event)"><AppIcon name="external" :size="17" />{{ focused ? '退出专注阅读' : '专注阅读' }}</button>
                <button type="button" class="paper-button" @click="resetReading(reset); closeToolMenu($event)"><AppIcon name="refresh" :size="17" />恢复默认布局与字号</button>
              </div>
            </details>
            <button type="button" class="paper-button" aria-label="切换原文证据栏" :aria-pressed="inspectorOpen" @click="toggleInspector"><AppIcon name="layers" :size="17" /><span>来源</span></button>
            <details class="toolbar-menu" name="chat-toolbar-menu" @keydown.esc="closeToolMenu($event)">
              <summary class="paper-button"><AppIcon name="more" :size="17" /><span>更多</span></summary>
              <div class="toolbar-popover">
                <button v-if="!sidebarOpen || sidebarTab !== 'sessions'" type="button" class="paper-button compact-new" :disabled="asking || initialLoading" @click="newSession(); closeToolMenu($event)"><AppIcon name="plus" :size="17" />新对话</button>
                <button type="button" class="paper-button" :disabled="!messages.length || asking || historyLoading" aria-label="导出当前对话" @click="exportConversation(); closeToolMenu($event)"><AppIcon name="download" :size="17" />导出当前对话</button>
              </div>
            </details>
          </div>
        </div>
      </template>
      <template #sidebar>
        <div class="library-scope"><label for="current-kb">当前知识库</label><el-select id="current-kb" v-model="currentKbId" :disabled="asking || initialLoading || historyLoading" placeholder="选择知识库" aria-label="当前知识库" @change="onKbChange"><el-option v-if="kbUnavailable" :label="currentKb?.name || '原知识库不可用'" :value="currentKbId" disabled /><el-option v-for="kb in availableKbs" :key="kb.id" :label="kb.name" :value="kb.id" /></el-select><span class="scope-badge"><AppIcon name="shield" :size="15" />{{ scopeLabel }}</span></div>
        <div class="library-tabs" role="group" aria-label="资料与会话"><button type="button" :class="{ active: sidebarTab === 'papers' }" :aria-pressed="sidebarTab === 'papers'" @click="sidebarTab = 'papers'">资料 <span>{{ currentKb?.doc_count ?? '—' }}</span></button><button type="button" :class="{ active: sidebarTab === 'sessions' }" :aria-pressed="sidebarTab === 'sessions'" @click="sidebarTab = 'sessions'">会话 <span>{{ scopedSessions.length }}</span></button></div>
        <template v-if="sidebarTab === 'papers'">
          <div class="rail-search"><AppIcon name="search" :size="16" /><input v-model="documentSearch" type="search" aria-label="搜索当前库论文" placeholder="搜索论文标题" /></div>
          <div class="rail-caption"><span>当前知识库</span><button type="button" class="answer-action" :disabled="docsLoading" aria-label="刷新论文列表" @click="loadDocuments"><AppIcon name="refresh" :size="14" /></button></div>
          <p v-if="docsLoading" class="rail-state" role="status">正在加载论文…</p><div v-else-if="docsError" class="rail-state" role="alert"><p>{{ docsError }}</p><button type="button" class="answer-action" @click="loadDocuments">重试</button></div>
          <ul v-else-if="documents.length" class="paper-list"><li v-for="doc in documents" :key="doc.id"><button type="button" class="paper-item" :class="{ selected: activeCitation?.doc_id === doc.id }" @click="openPaper(doc)"><AppIcon name="file" :size="18" /><span><strong>{{ doc.filename }}</strong><small :class="{ failed: doc.status === 'failed' }">{{ docStatusNames[doc.status] || doc.status }}<template v-if="doc.page_count"> · {{ doc.page_count }} 页</template></small></span></button></li></ul>
          <div v-else class="rail-state"><p>{{ documentSearch ? '没有匹配的论文' : '这个知识库还没有论文' }}</p><RouterLink v-if="currentKbId && !kbUnavailable" :to="'/admin/kbs/' + currentKbId" class="answer-action">{{ canManageKb ? '上传资料' : '查看资料' }}<AppIcon :name="canManageKb ? 'upload' : 'arrow'" :size="14" /></RouterLink></div>
          <div v-if="documentTotal > 30" class="rail-pagination"><button type="button" :disabled="docPage <= 1 || docsLoading" @click="docPage--; loadDocuments()">上一页</button><span>{{ docPage }} / {{ Math.ceil(documentTotal / 30) }}</span><button type="button" :disabled="docPage * 30 >= documentTotal || docsLoading" @click="docPage++; loadDocuments()">下一页</button></div>
          <RouterLink v-if="currentKbId && !kbUnavailable" :to="'/admin/kbs/' + currentKbId" class="rail-manage"><AppIcon :name="canManageKb ? 'upload' : 'book'" :size="16" />{{ canManageKb ? '上传与管理资料' : '查看库中资料' }}<AppIcon name="arrow" :size="15" /></RouterLink>
        </template>
        <template v-else>
          <div class="rail-search"><AppIcon name="search" :size="16" /><input ref="historySearchInput" v-model="sessionSearch" type="search" aria-label="搜索当前库会话" placeholder="搜索会话" /></div>
          <div class="rail-caption"><span>本库对话</span><button type="button" class="answer-action" :disabled="asking || initialLoading" @click="newSession"><AppIcon name="plus" :size="16" />新对话</button><button type="button" class="answer-action" :disabled="sessionsLoading || asking" aria-label="刷新会话列表" @click="refreshSessionList"><AppIcon name="refresh" :size="14" /></button></div>
          <p v-if="sessionListError || sessionActionError" class="rail-state error" role="alert">{{ sessionActionError || sessionListError }}</p>
          <ul v-if="filteredSessions.length" class="session-list"><li v-for="session in filteredSessions" :key="session.id" class="session-row" :class="{ selected: session.id === currentSessionId }"><button type="button" class="session-open" :disabled="asking" :aria-current="session.id === currentSessionId ? 'true' : undefined" @click="openSession(session)"><strong>{{ session.title || '新对话' }}</strong><time :datetime="session.updated_at">{{ formatDate(session.updated_at, true) }}</time></button><div class="session-actions"><button type="button" class="answer-action" :disabled="asking || sessionActionId !== null" :aria-label="'重命名 ' + session.title" @click="sessionAction('rename', session)"><AppIcon name="edit" :size="14" /></button><button type="button" class="answer-action" :disabled="asking || sessionActionId !== null" :aria-label="'删除 ' + session.title" @click="sessionAction('delete', session)"><AppIcon name="trash" :size="14" /></button></div></li></ul>
          <p v-else class="rail-state">{{ sessionSearch ? '没有匹配的对话' : '本库还没有对话' }}</p>
        </template>
      </template>
      <template #default>
        <div class="conversation-main">
          <div class="conversation-content" :class="{ 'is-landing': isLanding }">
            <header v-if="isLanding" class="landing-heading">
              <h2>让资料成为有出处的回答</h2>
              <p tabindex="0" :title="currentKb?.name"><span>{{ currentKb?.name }}</span> · {{ libraryCountLabel }}</p>
              <div class="landing-actions"><button type="button" class="paper-button primary" :disabled="composerBlocked" @click="focusQuestion"><AppIcon name="chat" :size="17" />开始提问</button><RouterLink v-if="!kbUnavailable" :to="'/admin/kbs/' + currentKbId" class="paper-button"><AppIcon :name="canManageKb ? 'folder' : 'book'" :size="17" />{{ canManageKb ? '管理资料' : '查看资料' }}</RouterLink><button v-else type="button" class="paper-button" @click="workspace?.openSidebar()">选择可用知识库</button></div>
            </header>
            <section v-if="!isLanding" ref="messageBox" class="message-scroll" tabindex="0" aria-label="问答阅读区" :aria-busy="initialLoading || historyLoading" @scroll="onScroll">
              <div v-if="initialLoading || historyLoading" class="reading-state" role="status"><h2>{{ initialLoading ? '正在打开工作区' : '正在读取对话' }}</h2><el-skeleton :rows="5" :animated="false" /></div>
              <div v-else-if="loadError || historyError" class="reading-state" role="alert"><h2>暂时无法加载</h2><p>{{ loadError || historyError }}</p><button type="button" class="paper-button" @click="loadError ? initialize() : refreshMessages()"><AppIcon name="refresh" :size="16" />重试</button></div>
              <div v-else-if="!hasReadingContext" class="reading-state"><AppIcon name="book" :size="36" /><h2>建立你的第一个知识库</h2><p>上传资料后，即可检索、提问并查看原文。</p><RouterLink to="/admin/kbs" class="paper-button primary"><AppIcon name="plus" :size="17" />新建知识库</RouterLink></div>
          <div v-else class="reading-sheet"><div class="message-stack">
            <article v-for="message in messages" :id="'message-' + message.id" :key="message.id" class="message" :class="message.role === 'assistant' ? 'answer-document' : 'question-document'">
              <header class="message-meta"><span class="message-avatar"><AppIcon :name="message.role === 'assistant' ? 'sparkles' : 'user'" :size="16" /></span><h2>{{ message.role === 'assistant' ? (message.mode === 'degraded' ? '生成未完成' : '论文助手') : '你' }}</h2><span v-if="message.role === 'assistant'" class="answer-mode" :class="{ 'is-caution': ['degraded', 'none'].includes(message.mode) }">{{ answerLabel(message) }}</span><time :datetime="message.created_at">{{ formatDate(message.created_at) }}</time></header>
              <MarkdownContent v-if="message.role === 'assistant'" :content="message.content" @source="rank => openRank(message, rank)" /><div v-else class="user-content">{{ message.content }}</div>
              <template v-if="message.role === 'assistant'">
                <details v-if="message.retrieval" class="retrieval-details">
                  <summary>{{ retrievalNames[message.retrieval.method] || '检索' }} · <template v-if="message.retrieval.method === 'overview'">{{ overviewCount(message.retrieval.covered_documents) }}/{{ overviewCount(message.retrieval.total_documents) }}篇资料 · {{ overviewCount(message.retrieval.source_count) }}条证据</template><template v-else>{{ message.retrieval.source_count }} 条证据</template></summary>
                  <p v-if="message.retrieval.method === 'overview'">按代表片段选取证据，并非全文通读。<span v-if="message.retrieval.coverage_limited">本次仅覆盖部分资料。</span></p>
                  <p v-else>向量匹配 {{ message.retrieval.vector_hits }} · 关键词匹配 {{ message.retrieval.keyword_hits }}<span v-if="message.retrieval.vector_status !== 'ready'"> · 向量{{ message.retrieval.vector_status === 'unavailable' ? '不可用' : '未配置' }}</span></p>
                </details>
                <details v-if="message.citations?.length" class="answer-sources" :open="message.citations.length <= 3"><summary><AppIcon name="layers" :size="16" />{{ message.citations.length }} 个引用来源<span>展开核查原文</span></summary><div class="citation-list" aria-label="这条回答的引用来源"><button v-for="(citation, index) in message.citations" :key="citation.rank || index" type="button" class="citation-reference" :class="{ selected: activeCitation === citation }" :aria-label="'阅读来源 ' + (citation.rank || index + 1) + '：' + citation.doc_name" @click="openCitation(citation, message.citations)"><span class="source-number">{{ citation.rank || index + 1 }}</span><span class="citation-label"><strong>{{ citation.doc_name }}</strong><small>{{ citation.page_no ? '第 ' + citation.page_no + ' 页' : '文本片段' }}</small></span><AppIcon name="chevron" :size="15" /></button></div></details>
                <div class="message-actions"><button v-if="canTranslateMessage(message)" type="button" class="answer-action" @click="openTranslation(message)">翻译</button><button type="button" class="answer-action" @click="copyMessage(message)"><AppIcon name="copy" :size="15" />复制</button><button v-if="message.mode === 'degraded'" type="button" class="answer-action" :disabled="composerBlocked" @click="retryAnswer(message)"><AppIcon name="refresh" :size="15" />重新生成</button><details class="message-more"><summary class="answer-action"><AppIcon name="more" :size="16" />反馈与更多</summary><div class="message-secondary-actions"><button type="button" class="answer-action" :class="{ selected: message.feedback_type === 'like' }" :aria-pressed="message.feedback_type === 'like'" :disabled="message.voting" @click="vote(message, 'like')"><AppIcon name="like" :size="15" />有帮助</button><button type="button" class="answer-action" :class="{ selected: negativeFeedbackTypes.includes(message.feedback_type) }" :disabled="message.voting || feedbackSaving" @click="openFeedback(message)"><AppIcon name="feedback" :size="15" />反馈</button><button type="button" class="answer-action" :disabled="asking || historyLoading" @click="exportConversation">导出对话</button></div></details><span v-if="message.latency_ms != null" class="answer-duration">{{ (message.latency_ms / 1000).toFixed(1) }}s</span></div><p v-if="message.feedback_error" class="inline-feedback-error" role="alert">{{ message.feedback_error }}</p>
              </template>
            </article>
            <div v-if="asking" class="waiting-answer" role="status" aria-live="polite" aria-atomic="true"><span class="waiting-dot" /><div><span>{{ generationLabel }}…</span><span v-if="generationProgress.received_chars !== null" class="generation-detail">已接收 {{ generationProgress.received_chars }} 字符（过程统计）</span><span v-if="generationProgress.elapsed_seconds !== null" class="generation-detail">已等待 {{ Math.floor(generationProgress.elapsed_seconds) }} 秒</span><small class="generation-detail">综合与校验完成后一次显示完整回答，可随时停止等待。</small></div></div>
          </div></div>

            </section>
            <!-- One textarea and one draft: the grid moves this same composer, never copies it. -->
            <section v-if="!initialLoading && !loadError && hasReadingContext" class="composer-region" :class="{ 'landing-composer': isLanding }" aria-label="提问输入区">
              <div class="composer-wrap">
                <div v-if="isLanding" class="composer-heading"><span class="section-eyebrow">基于当前知识库</span><h2>从你的问题开始</h2><p>提出问题、比较方法，或梳理资料中的关键结论。</p></div>
                <div v-if="sendError" id="send-error" class="send-error" role="alert"><span>{{ sendError }}</span><button type="button" class="answer-action" :disabled="asking" @click="refreshMessages">刷新对话</button></div>
                <p v-if="kbUnavailable" id="scope-unavailable" class="scope-notice">知识库已停用或无权访问，请选择可用知识库。<button type="button" class="answer-action" @click="workspace?.openSidebar()">选择知识库</button></p>
                <div v-else-if="noReadyDocuments" id="scope-empty" class="scope-notice"><span>{{ canManageKb ? '当前库暂无就绪资料，请先上传或检查解析任务。' : '当前库暂无就绪资料。你只有读取权限，请联系库所有者处理。' }}</span><RouterLink :to="'/admin/kbs/' + currentKbId">{{ canManageKb ? '上传 / 查看导入任务' : '查看资料状态' }}</RouterLink></div>
                <div class="question-composer" :class="{ focused: inputFocused, unavailable: composerBlocked }">
                  <label for="question-input" class="question-label">{{ isLanding ? '你的问题' : '继续提问' }}</label>
                  <textarea id="question-input" ref="questionInput" v-model="question" :disabled="composerBlocked" :aria-describedby="['question-help', 'question-count', 'capability-notice', sendError && 'send-error', kbUnavailable && 'scope-unavailable', noReadyDocuments && 'scope-empty'].filter(Boolean).join(' ')" :placeholder="kbUnavailable ? '请选择可用知识库' : noReadyDocuments ? '资料就绪后即可提问' : '这些资料的核心方法是什么？也可以继续追问…'" rows="3" @focus="inputFocused = true" @blur="inputFocused = false" @compositionstart="composing = true" @compositionend="composing = false" @keydown="onQuestionKeydown" />
                  <div class="composer-bottom"><span id="question-help" class="composer-shortcut">Enter 发送 · Shift + Enter 换行</span><span id="question-count" class="question-limit">{{ questionChars }} 字符</span><button type="button" class="send-button" :disabled="!asking && !canSend" @click="asking ? stopAsking() : send()"><AppIcon :name="asking ? 'close' : 'send'" :size="17" />{{ asking ? '停止' : '发送' }}</button></div>
                </div>
                <p id="capability-notice" class="capability-notice"><AppIcon name="info" :size="15" /><span>{{ capabilityLabel }} · 已配置不代表已连接验证。{{ capability.notice || '仅基于当前知识库检索；使用在线模型时，问题与相关资料会发往已配置服务。' }}</span></p>
                <div v-if="isLanding" class="suggestion-section"><h3>试试这样问</h3><div class="suggestion-list"><button v-for="(text, index) in suggestions" :key="text" type="button" :disabled="composerBlocked" @click="useSuggestion(text)"><AppIcon :name="['file', 'layers', 'search', 'feedback'][index]" :size="18" /><span>{{ text }}</span><AppIcon name="arrow" :size="16" /></button></div><p class="source-hint"><AppIcon name="shield" :size="16" />完整回答生成后，可点击来源核查原文。</p></div>
                <button v-if="!nearBottom && messages.length" type="button" class="jump-latest" @click="scrollBottom(true)"><AppIcon name="down" :size="15" />返回最新</button><span class="chat-visually-hidden" aria-live="polite">{{ replyAnnouncement }}</span>
              </div>
            </section>
            <details v-if="isLanding" class="landing-documents" open>
              <summary><span><AppIcon name="file" :size="18" />当前资料</span><AppIcon name="down" :size="16" /></summary>
              <div class="landing-documents-body">
                <p class="landing-caption">库中资料，不代表本次已引用</p>
                <p v-if="docsLoading" role="status" class="landing-state">正在读取当前库资料…</p>
                <div v-else-if="docsError" role="alert" class="landing-state"><p>{{ docsError }}</p><button type="button" class="answer-action" @click="loadDocuments">重新加载</button></div>
                <ul v-else-if="landingDocuments.length" class="landing-paper-list"><li v-for="doc in landingDocuments" :key="doc.id"><button type="button" class="landing-paper" :disabled="kbUnavailable" :title="doc.filename" @click="openPaper(doc)"><span class="document-type"><AppIcon name="file" :size="22" /><span>{{ documentType(doc.filename) }}</span></span><span class="document-description"><strong>{{ doc.filename }}</strong><span class="document-status" :class="{ ready: doc.status === 'success', failed: doc.status === 'failed' }">{{ docStatusNames[doc.status] || '状态未知' }}<template v-if="doc.page_count"> · {{ doc.page_count }} 页</template></span></span></button></li></ul>
                <p v-else class="landing-state">{{ documentSearch ? '当前搜索没有匹配资料，可查看全部资料。' : '还没有可展示的资料。' }}</p>
                <RouterLink v-if="!kbUnavailable" :to="'/admin/kbs/' + currentKbId" class="landing-all">查看全部资料<AppIcon name="arrow" :size="16" /></RouterLink>
              </div>
            </details>
            <section v-if="isLanding" class="landing-library" aria-label="当前库范围">
              <span class="library-node-icon"><AppIcon name="book" :size="28" /></span><span class="section-eyebrow">当前知识库</span><h2>{{ currentKb?.name }}</h2><span class="scope-badge"><AppIcon name="shield" :size="15" />{{ scopeLabel }}</span><p>{{ libraryCountLabel }}</p><p class="library-boundary">仅使用当前库<br />不跨库检索</p><span class="flow-arrow flow-before" aria-hidden="true">→</span><span class="flow-arrow flow-after" aria-hidden="true">→</span>
            </section>
          </div>
        </div>
      </template>
      <template #inspector="{ close }"><CitationReader ref="citationReader" :kb-id="currentKbId" :citation="activeCitation" :sources="sourceList" @close="close" @select="selectCitation" /></template>
    </ResizableWorkspace>
    <TranslationPanel v-if="user.user && translationVisible" v-model="translationVisible" :source-text="translationSource.text" :source-message-id="translationSource.messageId" />
    <el-dialog v-model="exportVisible" title="导出对话" width="min(680px, calc(100vw - 32px))" top="8vh" append-to-body><label for="export-preview" class="field-label">Markdown · 包含回答与来源</label><textarea id="export-preview" ref="exportPreview" class="export-preview" :value="exportData.content" readonly rows="12" /><template #footer><el-button @click="exportVisible = false">关闭</el-button><el-button @click="copyExport">复制</el-button><el-button type="primary" @click="downloadText(exportData.name, exportData.content)">下载 Markdown</el-button></template></el-dialog>
    <el-dialog v-model="feedbackVisible" title="反馈回答" width="min(480px, calc(100vw - 32px))" top="8vh" append-to-body><form id="answer-feedback-form" @submit.prevent="submitFeedback"><fieldset class="feedback-fieldset" :disabled="feedbackSaving"><legend>问题类型</legend><div class="feedback-options"><label><input v-model="feedbackForm.type" type="radio" name="answer-feedback" value="dislike" />不准确</label><label><input v-model="feedbackForm.type" type="radio" name="answer-feedback" value="lack_source" />缺少依据</label><label><input v-model="feedbackForm.type" type="radio" name="answer-feedback" value="no_relevant" />不相关</label></div></fieldset><label for="feedback-comment" class="field-label">补充说明（可选）</label><textarea id="feedback-comment" v-model="feedbackForm.comment" class="feedback-comment" :disabled="feedbackSaving" rows="4" maxlength="1000" /><p v-if="feedbackMessage?.feedback_error" class="inline-feedback-error" role="alert">{{ feedbackMessage.feedback_error }}</p></form><template #footer><el-button @click="feedbackVisible = false">取消</el-button><el-button native-type="submit" form="answer-feedback-form" type="primary" :loading="feedbackSaving">提交</el-button></template></el-dialog>
  </div>
</template>
<script setup>
import { ElButton, ElSelect, ElOption, ElSkeleton, ElDialog, ElMessage, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/select/style/css.mjs'
import 'element-plus/es/components/option/style/css.mjs'
import 'element-plus/es/components/skeleton/style/css.mjs'
import 'element-plus/es/components/dialog/style/css.mjs'
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { kbsApi, docsApi, sessionsApi, askApi, sessionMessagesApi, renameSessionApi, deleteSessionApi, feedbackApi, capabilitiesApi } from '@/api'
import { useUserStore } from '@/stores/user'
import MarkdownContent from '@/components/MarkdownContent.vue'
import ResizableWorkspace from '@/components/ResizableWorkspace.vue'
import CitationReader from '@/components/CitationReader.vue'
import TranslationPanel from '@/components/TranslationPanel.vue'
import { formatDate, modeNames, downloadText } from '@/utils/format'

const user = useUserStore()
const route = useRoute()
const workspace = ref(), citationReader = ref()
const sidebarTab = ref('papers'), readerFont = ref(16)
const documents = ref([]), documentTotal = ref(0), docPage = ref(1), documentSearch = ref('')
const docsLoading = ref(false), docsError = ref('')
const readyDocumentCount = ref(null)
const docStatusNames = { success: '已就绪', pending: '待解析', parsing: '解析中', failed: '解析失败' }
const retrievalNames = { hybrid: '向量 + 关键词', vector: '向量检索', keyword: '关键词检索', overview: '论文总览' }
const overviewCount = value => Number.isSafeInteger(value) && value >= 0 ? value : '—'
let docVersion = 0, docSearchTimer

const kbs = ref([])
const sessions = ref([])
const messages = ref([])
const currentKbId = ref(null)
const currentSessionId = ref(null)
const sessionSearch = ref('')
const question = ref('')
const questionChars = computed(() => Array.from(question.value).length)
const asking = ref(false)
const generationProgress = ref({ stage: 'retrieving', received_chars: null, elapsed_seconds: null })
const generationNames = { retrieving: '正在检索论文', generating: '正在综合生成回答', validating: '正在校验完整回答', translating: '正在翻译' }
const generationLabel = computed(() => generationNames[generationProgress.value.stage] || '正在处理请求')
let activeAsk = null, askSequence = 0
const initialLoading = ref(true)
const historyLoading = ref(false)
const sessionsLoading = ref(false)
const loadError = ref('')
const historyError = ref('')
const sendError = ref('')
const sessionListError = ref('')
const sessionActionError = ref('')
const sessionActionId = ref(null)
const capability = ref({ mode: '' })
const messageBox = ref()
const questionInput = ref()
const inputFocused = ref(false)
const composing = ref(false)
const nearBottom = ref(true)
const replyAnnouncement = ref('')

const historyOpen = ref(false)
const historySearchInput = ref()
const citationVisible = ref(false)
const activeCitation = ref(null)
const sourceList = ref([])
const feedbackVisible = ref(false)
const feedbackMessage = ref()
const feedbackSaving = ref(false)
const feedbackForm = ref({ type: 'dislike', comment: '' })
const negativeFeedbackTypes = ['dislike', 'lack_source', 'no_relevant']
const exportVisible = ref(false)
const exportData = ref({ name: '', content: '' })
const exportPreview = ref()
const translationVisible = ref(false)
const translationSource = ref({ text: '', messageId: null })

let alive = true
let historyVersion = 0
let initializationVersion = 0
let sessionListVersion = 0

const availableKbs = computed(() => kbs.value.filter(kb => kb.status === 'enabled'))
const currentKb = computed(() => kbs.value.find(kb => kb.id === currentKbId.value))
const currentSession = computed(() => sessions.value.find(session => session.id === currentSessionId.value))
const kbUnavailable = computed(() => currentKbId.value !== null && !availableKbs.value.some(kb => kb.id === currentKbId.value))
const hasReadingContext = computed(() => availableKbs.value.length > 0 || currentSessionId.value !== null)
const isLanding = computed(() => !initialLoading.value && !historyLoading.value && !loadError.value && !historyError.value && hasReadingContext.value && messages.value.length === 0)
const canManageKb = computed(() => {
  if (!currentKb.value || kbUnavailable.value) return false
  if (typeof currentKb.value.can_manage === 'boolean') return currentKb.value.can_manage
  return user.user?.id != null && Number(currentKb.value.owner_id) === Number(user.user.id)
})
const scopeLabel = computed(() => kbUnavailable.value ? '不可访问' : !canManageKb.value ? '共享 · 只读' : currentKb.value?.granted_roles?.length ? '共享 · 可管理' : '私人知识库')
const landingDocuments = computed(() => documents.value.slice(0, 3))
const noReadyDocuments = computed(() => !kbUnavailable.value && (currentKb.value?.doc_count === 0 || readyDocumentCount.value === 0))
const libraryCountLabel = computed(() => readyDocumentCount.value !== null ? readyDocumentCount.value + ' 份资料已就绪' : Number.isSafeInteger(currentKb.value?.doc_count) ? currentKb.value.doc_count + ' 份资料 · 就绪数量待确认' : '资料数量待确认')
const composerBlocked = computed(() => noReadyDocuments.value || asking.value || initialLoading.value || historyLoading.value || Boolean(historyError.value) || kbUnavailable.value || !currentKbId.value)
const canSend = computed(() => !composerBlocked.value && Boolean(question.value.trim()))
const capabilityLabel = computed(() => initialLoading.value ? '检查中' : ['local', 'online'].includes(capability.value.mode) ? '模型已配置' : '生成未启用')
const scopedSessions = computed(() => sessions.value.filter(item => item.kb_id === currentKbId.value))
const filteredSessions = computed(() => {
  const keyword = sessionSearch.value.trim().toLocaleLowerCase()
  return scopedSessions.value.filter(session => String(session.title || '未命名对话').toLocaleLowerCase().includes(keyword))
})
const suggestions = ['概括这些论文的核心研究问题与贡献', '比较不同论文的方法、优势和局限', '解释论文中的实验设计与评价指标', '有哪些值得进一步研究的问题？']

async function initialize() {
  if (asking.value) return
  const version = ++initializationVersion
  initialLoading.value = true
  loadError.value = ''
  try {
    const [loadedKbs, loadedSessions, loadedCapability] = await Promise.all([kbsApi(), sessionsApi(), capabilitiesApi()])
    if (!alive || version !== initializationVersion) return
    if (![loadedKbs, loadedSessions].every(Array.isArray)) throw new Error('Invalid workspace response')
    kbs.value = loadedKbs
    sessions.value = loadedSessions
    capability.value = loadedCapability || { mode: '' }
    sessionListError.value = ''
    // A historical session keeps its original scope, even if that scope is no longer available.
    if (currentSessionId.value === null) {
      let savedKb = null
      try { savedKb = localStorage.getItem('selected-kb-' + user.user.id) } catch { /* Preferences are optional. */ }
      const preferred = Number(route.query.kb_id || route.query.kb || savedKb)
      currentKbId.value = availableKbs.value.find(kb => kb.id === preferred)?.id
        || availableKbs.value.find(kb => kb.doc_count > 0)?.id
        || availableKbs.value[0]?.id || null
    }
    void loadDocuments()
    if (!question.value && typeof route.query.q === 'string') question.value = route.query.q
  } catch {
    if (alive && version === initializationVersion) loadError.value = '知识库、会话或能力信息加载失败。请检查连接后重试，原有记录不会因此被删除。'
  } finally {
    if (alive && version === initializationVersion) initialLoading.value = false
  }
}

function openHistory() {
  sidebarTab.value = 'sessions'
  workspace.value?.openSidebar()
}

async function focusHistorySearch() {
  // Element Plus emits open-auto-focus without forwarding the native event.
  await nextTick()
  if (historyOpen.value) historySearchInput.value?.focus()
}

async function focusQuestion() {
  await nextTick()
  if (alive && !exportVisible.value && !feedbackVisible.value && !translationVisible.value) {
    if (isLanding.value) questionInput.value?.scrollIntoView?.({ block: 'nearest' })
    questionInput.value?.focus({ preventScroll: true })
  }
}

function newSession() {
  if (asking.value) return
  const wasHistoryOpen = historyOpen.value
  translationVisible.value = false
  translationSource.value = { text: '', messageId: null }
  historyVersion++
  workspace.value?.closeInspector()
  currentSessionId.value = null
  messages.value = []
  question.value = ''
  composing.value = false
  sendError.value = ''
  historyError.value = ''
  replyAnnouncement.value = ''
  historyLoading.value = false
  historyOpen.value = false
  citationVisible.value = false
  activeCitation.value = null
  sourceList.value = []
  nearBottom.value = true
  // Drawer dismissal restores its own trigger; direct actions can focus the composer.
  if (!wasHistoryOpen) focusQuestion()
}

function onKbChange() {
  try { localStorage.setItem('selected-kb-' + user.user.id, String(currentKbId.value)) } catch { /* Keep the selection in memory when storage is unavailable. */ }
  newSession()
}

async function openSession(session, options = {}) {
  if (asking.value) return
  const version = ++historyVersion
  translationVisible.value = false
  translationSource.value = { text: '', messageId: null }
  workspace.value?.closeInspector()
  activeCitation.value = null
  sourceList.value = []
  currentSessionId.value = session.id
  currentKbId.value = session.kb_id
  historyLoading.value = true
  historyOpen.value = false
  citationVisible.value = false
  historyError.value = ''
  if (!options.keepDraft) {
    sendError.value = ''
    question.value = ''
  }
  messages.value = []
  replyAnnouncement.value = ''
  try {
    const data = await sessionMessagesApi(session.id)
    if (!alive || version !== historyVersion) return
    if (!Array.isArray(data)) throw new Error('Invalid history response')
    messages.value = data
  } catch {
    if (alive && version === historyVersion) historyError.value = '读取失败，无法确认这段会话的完整内容。请重新读取，或新建对话；当前输入会保留。'
  } finally {
    if (alive && version === historyVersion) {
      historyLoading.value = false
      await scrollBottom(true)
    }
  }
}

async function refreshSessionList() {
  const version = ++sessionListVersion
  sessionsLoading.value = true
  sessionListError.value = ''
  try {
    const data = await sessionsApi()
    if (!alive || version !== sessionListVersion) return false
    if (!Array.isArray(data)) throw new Error('Invalid sessions response')
    sessions.value = data
    return true
  } catch {
    if (alive && version === sessionListVersion) sessionListError.value = '会话列表未能刷新，已显示的内容仍保留。请稍后重试。'
    return false
  } finally {
    if (alive && version === sessionListVersion) sessionsLoading.value = false
  }
}

async function refreshMessages() {
  if (asking.value || historyLoading.value || initialLoading.value) return
  const hadSession = currentSessionId.value !== null
  if (hadSession) await openSession({ id: currentSessionId.value, kb_id: currentKbId.value }, { keepDraft: true })
  const refreshed = await refreshSessionList()
  // A timed-out first question may have created a session without returning its id.
  if (alive && !hadSession && refreshed) openHistory()
}

async function sessionAction(command, session) {
  if (asking.value || sessionActionId.value !== null || !['rename', 'delete'].includes(command)) return
  sessionActionId.value = session.id
  sessionActionError.value = ''
  try {
    if (command === 'rename') {
      const result = await ElMessageBox.prompt('给这段对话起个容易找到的名字', '重命名对话', {
        inputValue: session.title,
        inputValidator: value => Boolean(value?.trim()) && value.trim().length <= 100 || '名称应为 1–100 个字符',
        confirmButtonText: '保存',
        cancelButtonText: '取消',
      })
      const updated = await renameSessionApi(session.id, result.value.trim())
      if (!alive) return
      sessions.value = sessions.value.map(item => item.id === session.id ? { ...item, ...updated } : item)
      ElMessage.success('对话名称已更新')
    } else {
      await ElMessageBox.confirm('删除后将移除这段对话、引用与反馈记录，此操作无法在界面恢复。', '删除「' + session.title + '」？', {
        type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '保留对话',
      })
      await deleteSessionApi(session.id)
      if (!alive) return
      sessions.value = sessions.value.filter(item => item.id !== session.id)
      if (currentSessionId.value === session.id) newSession()
      ElMessage.success('对话已删除')
    }
  } catch (error) {
    if (alive && error !== 'cancel' && error !== 'close') sessionActionError.value = (command === 'rename' ? '重命名' : '删除') + '未完成，请检查连接后重试。'
  } finally {
    if (alive) sessionActionId.value = null
  }
}

function useSuggestion(text) {
  if (composerBlocked.value) return
  question.value = text
  focusQuestion()
}

function onQuestionKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !composing.value && event.keyCode !== 229) {
    event.preventDefault()
    send()
  }
}

function isCurrentAsk(request) {
  return alive && activeAsk === request && !request.controller.signal.aborted
    && currentKbId.value === request.kbId && currentSessionId.value === request.sessionId && historyVersion === request.historyVersion
}
function stopAsking({ silent = false } = {}) {
  const request = activeAsk
  if (!request) return
  const sameScope = currentKbId.value === request.kbId && currentSessionId.value === request.sessionId && historyVersion === request.historyVersion
  activeAsk = null
  request.controller.abort()
  if (!alive) return
  asking.value = false
  messages.value = messages.value.filter(message => message.id !== request.optimisticId)
  if (!silent && sameScope) {
    question.value = request.text
    sendError.value = '已停止等待，问题已保留。若服务器已完成，请先刷新对话核对记录，避免重复发送。'
    replyAnnouncement.value = '已停止等待，完整问题已保留在输入框。'
    focusQuestion()
  }
}
async function send() {
  const text = question.value
  if (!text.trim() || noReadyDocuments.value || asking.value || initialLoading.value || historyLoading.value || historyError.value || !currentKbId.value) return
  if (!availableKbs.value.some(kb => kb.id === currentKbId.value)) {
    ElMessage.warning('这个知识库已停用或不可访问，请选择其他知识库。')
    return
  }
  const request = { controller: new AbortController(), kbId: currentKbId.value, sessionId: currentSessionId.value, historyVersion, text, optimisticId: 'draft-' + (++askSequence) }
  activeAsk = request
  messages.value.push({ id: request.optimisticId, role: 'user', content: text, created_at: new Date().toISOString() })
  question.value = ''
  asking.value = true
  generationProgress.value = { stage: 'retrieving', received_chars: null, elapsed_seconds: null }
  sendError.value = ''
  replyAnnouncement.value = ''
  try {
    await scrollBottom(true)
    if (!isCurrentAsk(request)) return
    const data = await askApi({ session_id: request.sessionId, kb_id: request.kbId, question: text }, {
      signal: request.controller.signal,
      onProgress(event) { if (isCurrentAsk(request)) generationProgress.value = { ...generationProgress.value, ...event } },
    })
    if (!isCurrentAsk(request)) return
    if (!Number.isSafeInteger(data?.session_id) || data.session_id <= 0 || !Number.isSafeInteger(data?.message_id) || data.message_id <= 0 || typeof data.answer !== 'string') throw new Error('Incomplete answer response')
    const generated = data.generation_status === 'generated'
    if (generated && !data.answer.trim()) throw new Error('Empty generated answer')
    // A terminal failure may retain all citation records, but cannot masquerade
    // as a completed model answer. Historical messages remain readable as stored.
    const content = generated ? data.answer : typeof data.notice === 'string' && data.notice.trim() ? data.notice
      : data.mode === 'none' ? '没有找到相关证据，请调整问题后再试。' : '生成未完成，请先刷新对话核对记录，再决定是否重新生成。'
    currentSessionId.value = data.session_id
    messages.value.push({
      id: data.message_id, role: 'assistant', content,
      citations: Array.isArray(data.citations) ? data.citations : [], mode: data.mode,
      latency_ms: data.latency_ms, created_at: new Date().toISOString(), question: text,
      kb_id: request.kbId, status: data.generation_status, notice: data.notice, retrieval: data.retrieval,
    })
    replyAnnouncement.value = generated ? '完整回答已返回。' : data.mode === 'none' ? '没有找到相关证据。' : '生成未完成，来源记录已保留。'
    void scrollBottom()
    void refreshSessionList()
  } catch (error) {
    if (!isCurrentAsk(request)) return
    messages.value = messages.value.filter(message => message.id !== request.optimisticId)
    question.value = text
    const cancelled = error?.name === 'AbortError' || error?.response?.status === 499
    const detail = error?.response?.data?.detail
    sendError.value = cancelled ? '已停止等待，问题已保留。若服务器已完成，请先刷新对话核对记录，避免重复发送。'
      : (typeof detail === 'string' && detail.trim() ? detail + ' ' : '请求未完成。') + '问题已保留，请先刷新对话核对记录，再决定是否重新发送。'
    replyAnnouncement.value = cancelled ? '已停止等待，问题已保留在输入框。' : '请求未完成，问题已保留在输入框。'
  } finally {
    if (activeAsk === request) {
      activeAsk = null
      if (alive) { asking.value = false; focusQuestion() }
    }
  }
}

function canTranslateMessage(message) {
  return message?.role === 'assistant' && (message.status === 'generated' || message.generation_status === 'generated' || (typeof message.content === 'string' && Boolean(message.content.trim())))
}
function openTranslation(message) {
  if (message && !canTranslateMessage(message)) return
  translationSource.value = {
    text: typeof message?.content === 'string' ? message.content : '',
    messageId: Number.isSafeInteger(message?.id) && message.id > 0 ? message.id : null,
  }
  translationVisible.value = true
}

function onScroll() {
  const box = messageBox.value
  if (box) nearBottom.value = box.scrollHeight - box.scrollTop - box.clientHeight < 120
}

async function scrollBottom(force = false) {
  await nextTick()
  if (alive && messageBox.value && (force || nearBottom.value)) {
    messageBox.value.scrollTop = messageBox.value.scrollHeight
    nearBottom.value = true
  }
}

function openCitation(citation, sources) {
  historyOpen.value = false
  activeCitation.value = citation
  sourceList.value = sources
  citationVisible.value = true
  workspace.value?.openInspector()
}

async function copyMessage(message) {
  try {
    await navigator.clipboard.writeText(message.content)
    ElMessage.success('回答已复制')
  } catch {
    ElMessage.warning('浏览器未允许复制，请选择回答文本后手动复制。')
  }
}

async function vote(message, type, comment = '') {
  if (!message || message.voting) return false
  message.voting = true
  message.feedback_error = ''
  try {
    await feedbackApi({ message_id: message.id, feedback_type: type, comment })
    if (!alive) return false
    message.feedback_type = type
    ElMessage.success('反馈已保存，感谢帮助改进')
    return true
  } catch {
    if (alive) message.feedback_error = '反馈未能保存，请检查连接后重试；尚未标记为提交成功。'
    return false
  } finally {
    if (alive) message.voting = false
  }
}

function openFeedback(message) {
  if (message.voting || feedbackSaving.value) return
  feedbackMessage.value = message
  feedbackForm.value = { type: negativeFeedbackTypes.includes(message.feedback_type) ? message.feedback_type : 'dislike', comment: '' }
  feedbackVisible.value = true
}

async function submitFeedback() {
  if (feedbackSaving.value || !feedbackMessage.value) return
  feedbackSaving.value = true
  try {
    if (await vote(feedbackMessage.value, feedbackForm.value.type, feedbackForm.value.comment.trim())) feedbackVisible.value = false
  } finally {
    if (alive) feedbackSaving.value = false
  }
}

function exportConversation() {
  const title = currentSession.value?.title || '知识库对话'
  const sections = messages.value.map(message => [
    '## ' + (message.role === 'user' ? '提问' : '回答 · ' + (message.mode === 'degraded' ? '生成未完成' : modeNames[message.mode] || '模式未提供')),
    message.content,
    ...(message.citations || []).map(citation => {
      const heading = '> 来源 ' + citation.rank + '：' + citation.doc_name + '（' + (citation.page_no ? '第 ' + citation.page_no + ' 页' : '文本片段') + '）'
      const excerpt = String(citation.content_excerpt || '此引用未返回摘录').split('\n').map(line => '> ' + line).join('\n')
      return heading + '\n' + excerpt
    }),
  ].join('\n\n'))
  const content = ['# ' + title, '知识库：' + (currentKb.value?.name || '原知识库不可用'), sections.join('\n\n---\n\n')].join('\n\n')
  const name = title.replace(/[\\/:*?"<>|\u0000-\u001F]/g, '_').slice(0, 60) || '知识库对话'
  exportData.value = { name: name + '.md', content }
  exportVisible.value = true
}

async function copyExport() {
  try {
    await navigator.clipboard.writeText(exportData.value.content)
    ElMessage.success('导出内容已复制')
  } catch {
    exportPreview.value?.focus()
    exportPreview.value?.select()
    ElMessage.warning('浏览器未允许复制，请手动复制已选中的 Markdown 内容。')
  }
}

onMounted(() => {
  try { const saved = Number(localStorage.getItem('paper-reader-font-' + user.user.id)); if (saved >= 14 && saved <= 20) readerFont.value = saved } catch { /* Optional preference. */ }
  initialize()
})
onBeforeUnmount(() => {
  alive = false
  stopAsking({ silent: true })
  historyVersion++
  initializationVersion++
  sessionListVersion++
  docVersion++
  clearTimeout(docSearchTimer)
})

function closeToolMenu(event) {
  const details = event.currentTarget?.closest('details')
  if (!details || event.key === 'Escape' && !details.open) return
  if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation() }
  details.open = false
  details.querySelector('summary')?.focus()
}
function documentType(filename) {
  const extension = String(filename || '').split('.').pop().toUpperCase()
  return ['PDF', 'DOC', 'DOCX', 'TXT', 'MD'].includes(extension) ? extension : '文档'
}
function changeFont(delta) {
  readerFont.value = Math.max(14, Math.min(20, readerFont.value + delta))
  try { localStorage.setItem('paper-reader-font-' + user.user.id, String(readerFont.value)) } catch { /* Optional preference. */ }
}
function resetReading(reset) { reset(); changeFont(16 - readerFont.value) }
function answerLabel(message) {
  if (message.mode === 'none') return '没有相关证据'
  if (message.mode === 'degraded') return '生成未完成'
  return message.mode === 'local' ? '本地模型' : message.mode === 'online' ? '模型回答' : '历史回答'
}
function openRank(message, rank) {
  const found = message.citations?.find(item => item.rank === rank)
  if (found) openCitation(found, message.citations)
  else ElMessage.warning('这条来源没有可用的引用记录。')
}
function selectCitation(citation) { activeCitation.value = citation }
async function openPaper(doc) {
  activeCitation.value = { doc_id: doc.id, doc_name: doc.filename, page_no: 1, content_excerpt: '', rank: null }
  sourceList.value = []
  workspace.value?.openInspector()
  await nextTick()
  citationReader.value?.showOriginal()
}
async function retryAnswer(message) {
  if (composerBlocked.value) return
  const position = messages.value.indexOf(message)
  const preceding = [...messages.value.slice(0, position)].reverse().find(item => item.role === 'user')
  question.value = message.question || preceding?.content || ''
  await send()
}
async function loadDocuments() {
  const request = ++docVersion, kb = currentKbId.value
  const pageNumber = docPage.value, search = documentSearch.value.trim()
  if (!kb) { documents.value = []; documentTotal.value = 0; docsLoading.value = false; return }
  docsLoading.value = true; docsError.value = ''
  try {
    const page = await docsApi(kb, pageNumber, 30, { q: search })
    if (!alive || request !== docVersion || kb !== currentKbId.value) return
    if (!Array.isArray(page?.items)) throw new Error('Invalid documents response')
    documents.value = page.items; documentTotal.value = page.total
    // A paginated/search result is not a reliable ready total. Count only a complete,
    // unfiltered snapshot; never infer all-library readiness from its first page.
    if (!search && pageNumber === 1) {
      readyDocumentCount.value = Number.isSafeInteger(page.total) && page.total === page.items.length
        ? page.items.filter(doc => doc.status === 'success').length : null
    }
  } catch {
    if (alive && request === docVersion) docsError.value = '论文列表加载失败。'
  } finally { if (alive && request === docVersion) docsLoading.value = false }
}
watch(currentKbId, () => {
  if (activeAsk && activeAsk.kbId !== currentKbId.value) stopAsking({ silent: true })
  translationVisible.value = false
  translationSource.value = { text: '', messageId: null }
  docVersion++; documents.value = []; documentTotal.value = 0; readyDocumentCount.value = null; docPage.value = 1
  documentSearch.value = ''; clearTimeout(docSearchTimer); void loadDocuments()
}, { flush: 'sync' })
watch(documentSearch, () => {
  clearTimeout(docSearchTimer)
  docSearchTimer = setTimeout(() => { docPage.value = 1; void loadDocuments() }, 220)
})
watch(() => route.query.kb_id || route.query.kb, value => {
  if (asking.value || initialLoading.value) return
  const next = Number(value)
  if (next !== currentKbId.value && availableKbs.value.some(kb => kb.id === next)) { currentKbId.value = next; onKbChange() }
})

</script>
<style scoped src="./chat.css"></style>
