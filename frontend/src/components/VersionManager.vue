<template>
<el-dialog
  :model-value="modelValue"
  class="version-manager"
  title="文档版本管理"
  width="min(1120px, calc(100vw - 32px))"
  top="5vh"
  append-to-body
  destroy-on-close
  :before-close="beforeClose"
  :close-on-click-modal="!isBusy"
  :close-on-press-escape="!isBusy"
  :show-close="!isBusy"
  @update:model-value="dialogVisibilityChanged"
>
  <template #header="{ titleId }">
    <div class="vm-header"><span class="vm-eyebrow">资料 / 不可变文件与解析修订</span><h2 :id="titleId">文档版本管理</h2><p v-if="version && !forbidden">{{ version.filename }} · 版本 #{{ version.version_id }}</p></div>
  </template>
  <div class="vm-body" :aria-busy="loading || !!busy">
    <div class="vm-toolbar"><p>发布状态、解析结果和日期适用分别记录，不能相互替代。</p><el-button :loading="loading" :disabled="isBusy || !activeVersionId" @click="refreshForReview"><AppIcon name="refresh" :size="16" />重新读取并核对</el-button></div>
    <div v-if="loadError" class="vm-banner vm-error" role="alert"><AppIcon name="warning" :size="18" /><div><b>{{ loadError.title }}</b><p>{{ loadError.message }}</p><p v-if="loadError.kind === 'unavailable'">旧服务可能尚未接入版本 API，或资料记录不存在。请核对版本能力及显式迁移状态；本面板不会自动迁移或修改数据库。</p><p v-if="loadError.kind === 'forbidden'">已清除当前资料与列表展示，写操作已停止。可在权限恢复后重新读取；未提交输入不会自动发出。</p><p v-else-if="version">当前显示的是上次读取记录。重新读取成功前不能提交写操作。</p></div></div>
    <div v-if="notice" class="vm-banner" :class="'vm-' + notice.kind" role="status"><AppIcon :name="notice.kind === 'warning' ? 'warning' : 'info'" :size="18" /><p>{{ notice.text }}</p></div>
    <div v-if="loading && !version" class="vm-loading" role="status"><p>正在读取版本记录…</p><el-skeleton :rows="7" /></div>
    <template v-else-if="version && !forbidden">
      <div class="vm-record-heading"><div><span class="vm-eyebrow">SERIES / {{ version.series_id }}</span><h3>{{ version.series_title }}</h3><p>{{ version.version_label }} · 元数据修订 #{{ version.metadata_revision }} · 系列修订 #{{ version.series_revision }}</p></div><span v-if="version.is_legacy" class="vm-state">历史资料迁入</span></div>
      <div v-if="!canManage" class="vm-banner vm-info" role="status"><AppIcon name="lock" :size="18" /><p>当前权限为只读。可以查看版本与解析修订，不提供元数据编辑、发布、归档或上传操作。</p></div>
      <div v-if="hasLegacySource" class="vm-banner vm-warning"><AppIcon name="warning" :size="18" /><p><b>legacy_unverified：</b>旧摘录已保留，但尚未重建可靠原文定位；不能把历史摘录视为原文件来源已经校验。</p></div>
      <div v-if="isBuilding || pollMessage" class="vm-poll" role="status"><span>{{ pollMessage }}</span><el-button v-if="isBuilding && pollCount >= pollLimit" text :disabled="isBusy" @click="resumePolling">继续有限自动更新</el-button></div>

      <el-tabs v-model="activeTab" class="vm-tabs" :before-leave="() => !isBusy || !canManage">
        <el-tab-pane label="版本概览" name="overview">
          <section class="vm-section" aria-labelledby="vm-status-title"><div class="vm-section-heading"><span class="vm-eyebrow">01 / 独立状态</span><h3 id="vm-status-title">发布与解析</h3></div><div class="vm-status-grid">
            <div><h4>发布状态</h4><span class="vm-state" :class="'vm-publication-' + version.publication_state">{{ publicationNames[version.publication_state] }}</span><p>决定是否作为已发布版本参与范围选择，不代表事实可信。</p></div>
            <div><h4>当前解析修订</h4><template v-if="version.current_revision"><span class="vm-state" :class="'vm-revision-' + version.current_revision.state">{{ revisionNames[version.current_revision.state] }}</span><p>修订 #{{ version.current_revision.id }} · {{ version.current_revision.chunk_count }} 个片段</p><p>{{ integrityNames[version.current_revision.source_integrity] || version.current_revision.source_integrity }}</p></template><p v-else>尚无当前解析修订。</p></div>
            <div><h4>最新解析尝试</h4><template v-if="version.latest_revision"><span class="vm-state" :class="'vm-revision-' + version.latest_revision.state">{{ revisionNames[version.latest_revision.state] }}</span><p>修订 #{{ version.latest_revision.id }}<span v-if="version.latest_revision.id === version.current_revision?.id"> · 与当前修订一致</span></p><p v-if="version.latest_revision.error_message" class="vm-error-text">{{ version.latest_revision.error_message }}</p><p v-if="version.latest_revision.state === 'failed' && version.current_revision?.state === 'success'">最新尝试失败；当前仍保留旧成功修订 #{{ version.current_revision.id }}。</p></template><p v-else>尚无解析尝试。</p></div>
          </div><p class="vm-help">verified 仅表示原文件来源校验；解析成功、元数据适用或有引用，都不等于内容真实、正确或证据充分。</p></section>

          <section class="vm-section" aria-labelledby="vm-validity-title"><div class="vm-section-heading"><span class="vm-eyebrow">02 / 日期与适用条件</span><h3 id="vm-validity-title">生效范围</h3></div><dl class="vm-facts"><div><dt>日期状态</dt><dd>{{ dateNames[version.date_status] || version.date_status }}</dd></div><div><dt>半开区间 [start, end)</dt><dd>{{ intervalText(version) }}</dd></div><div><dt>明确替代的版本</dt><dd><button v-if="version.supersedes_id" type="button" class="vm-text-button" :disabled="isBusy" @click="switchVersion(version.supersedes_id)">查看版本 #{{ version.supersedes_id }}</button><span v-else>未指定，不自动推断</span></dd></div><div><dt>并存说明</dt><dd class="vm-pre-wrap">{{ version.coexistence_note || '未填写' }}</dd></div></dl><p class="vm-help">业务日期按 Asia/Shanghai 判断；包含开始日，不包含结束日。未来版本可发布，但开始日期之前不提前用于当前时间范围。</p><div class="vm-subsection"><h4>分类适用</h4><dl v-if="Object.keys(version.applicability || {}).length" class="vm-applicability"><div v-for="(values, category) in version.applicability" :key="category"><dt>{{ category }}</dt><dd>{{ values.join('、') }}</dd></div></dl><p v-else>未设置分类限制（空对象），不代表所有具体业务条件已被核实。</p></div><details v-if="version.eligibility_reasons?.length" class="vm-details"><summary>查看元数据适用性判定说明</summary><ul><li v-for="reason in version.eligibility_reasons" :key="reason">{{ eligibilityText(reason) }}</li></ul><p class="vm-help">这些条件只说明候选范围，不替代权限、来源核验或事实判断。</p></details></section>

          <section class="vm-section" aria-labelledby="vm-file-title"><div class="vm-section-heading"><span class="vm-eyebrow">03 / 文件记录</span><h3 id="vm-file-title">原文件与校验值</h3></div><dl class="vm-facts"><div><dt>文件名称</dt><dd>{{ version.filename }}</dd></div><div><dt>类型与大小</dt><dd>{{ (version.file_type || '').toUpperCase() }} · {{ formatBytes(version.file_size) }}</dd></div><div><dt>创建时间（北京时间）</dt><dd>{{ formatTime(version.created_at) }}</dd></div><div><dt>记录更新时间（北京时间）</dt><dd>{{ formatTime(version.updated_at) }}</dd></div></dl><div class="vm-hash-heading"><h4>SHA-256</h4><el-button :disabled="!version.sha256" @click="copySha"><AppIcon name="copy" :size="14" />复制哈希</el-button></div><el-input v-if="version.sha256" class="vm-hash" :model-value="version.sha256" type="textarea" :rows="2" readonly aria-label="原文件 SHA-256，可选择复制" /><p v-else class="vm-help">接口未提供文件哈希，不能假定已完成原文件校验。</p></section>

          <section v-if="canManage" class="vm-section" aria-labelledby="vm-actions-title"><div class="vm-section-heading"><span class="vm-eyebrow">04 / 有原因的状态变更</span><h3 id="vm-actions-title">发布、撤回与归档</h3></div><p v-if="!version.can_publish" class="vm-help vm-warning-text">当前不能发布：需先建立成功且来源为 verified 的解析修订，并满足服务端发布条件。历史未校验摘录不能跳过此前置。</p><p v-if="version.publication_state === 'archived'" class="vm-help">归档保留文件和历史。恢复操作会同时发布，仍须满足相同解析与来源前置，不会仅凭归档记录直接放行。</p><el-form ref="actionFormRef" :model="{ reason: actionReason, acknowledge_unknown: acknowledgeUnknown }" label-position="top" :disabled="isBusy" @submit.prevent>
              <el-form-item label="操作原因（发布 / 撤回 / 归档必填）" prop="reason" required :error="actionErrors.reason"><el-input v-model="actionReason" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="说明本次状态调整的依据与影响。" /></el-form-item>
              <el-form-item v-if="version.validity === 'unknown' && version.publication_state !== 'published'" prop="acknowledge_unknown" :error="actionErrors.acknowledge_unknown"><el-checkbox v-model="acknowledgeUnknown">我明确确认：日期仍未知，本次发布不推断生效日期。</el-checkbox></el-form-item>
            </el-form><div v-if="operationError" class="vm-banner vm-error" role="alert"><div><b>{{ operationError.title }}</b><p>{{ operationError.message }}</p><button v-if="operationError.kind === 'conflict' || reloadRequired" type="button" class="vm-text-button" :disabled="isBusy" @click="refreshForReview">读取最新记录后核对</button></div></div><div class="vm-actions"><el-button v-if="version.publication_state !== 'published'" type="primary" :loading="busy === 'publish'" :disabled="writeBlocked || !version.can_publish" @click="publicationAction('publish')">{{ publishLabel }}</el-button><el-button v-if="version.publication_state === 'published'" :loading="busy === 'withdraw'" :disabled="writeBlocked" @click="publicationAction('withdraw')">撤回此版本</el-button><el-button v-if="version.publication_state !== 'archived'" :loading="busy === 'archive'" :disabled="writeBlocked" @click="publicationAction('archive')">归档并保留历史</el-button></div><div class="vm-reparse"><div><h4>重新解析已保存文件</h4><p>新尝试失败会保留旧成功修订。修改了本机文件时，应上传新版本，而不是重复解析旧文件。</p><p v-if="isBuilding" class="vm-help">已有构建中的修订，请等待或刷新后核对，当前不能重复提交。</p></div><el-button :loading="busy === 'reparse'" :disabled="writeBlocked || isBuilding" @click="reparseSavedFile">重新解析已保存文件</el-button></div></section>
        </el-tab-pane>

        <el-tab-pane v-if="canManage" label="编辑元数据" name="metadata">
          <div class="vm-tab-intro"><h3>明确范围，再记录修改原因</h3><p>仅提交实际改变的字段。当前草稿基于元数据修订 #{{ metadataBaseRevision }}；文件内容不会被此表单替换。</p></div>
          <div v-if="metadataStale || metadataError?.kind === 'conflict'" class="vm-banner vm-warning" role="alert"><div><b>草稿与服务器需要核对</b><p v-if="metadataError?.message">{{ metadataError.message }}</p><p>草稿基线 #{{ metadataBaseRevision }}，当前已读取修订 #{{ version.metadata_revision }}。未保存输入仍保留，不会悄悄更新提交基线。</p><div class="vm-actions"><el-button :disabled="isBusy" @click="refreshForReview">读取最新用于核对</el-button><el-button :disabled="isBusy || reloadRequired" @click="rebaseMetadataDraft">核对后保留改动并更新基线</el-button><el-button :disabled="isBusy || reloadRequired" @click="discardMetadataDraft">放弃草稿采用最新</el-button></div></div></div>
          <details v-if="metadataBaseline" class="vm-details"><summary>比较草稿、原基线与当前服务器记录</summary><p id="vm-meta-scroll" class="vm-help">这是字段对照，不是内容语义差异判断；窄屏在表内横向滚动。</p><div class="vm-table-scroll" tabindex="0" role="region" aria-label="元数据修改对照" aria-describedby="vm-meta-scroll"><table class="vm-compare-table"><thead><tr><th scope="col">字段</th><th scope="col">原基线</th><th scope="col">服务器最新</th><th scope="col">拟提交值</th></tr></thead><tbody><tr v-for="row in metadataReviewRows" :key="row.key"><th scope="row">{{ row.label }}</th><td><pre>{{ row.baseline }}</pre></td><td><pre>{{ row.current }}</pre></td><td><pre>{{ row.draft }}</pre></td></tr></tbody></table></div></details>
          <el-form ref="metadataFormRef" :model="metadataDraft" label-position="top" :disabled="isBusy" class="vm-form" @submit.prevent="saveMetadata">
            <div class="vm-form-grid"><el-form-item label="版本标签" prop="version_label" required :error="metadataErrors.version_label"><el-input v-model="metadataDraft.version_label" maxlength="80" show-word-limit placeholder="例如：2026 秋季修订（由你填写）" /></el-form-item><el-form-item label="日期明确性" prop="validity" required :error="metadataErrors.validity"><el-select v-model="metadataDraft.validity" @change="metadataDraft.unlimited_confirmed = false"><el-option label="日期未知，不推断" value="unknown" /><el-option label="日期已明确（可确认不限期）" value="known" /></el-select></el-form-item></div>
            <div class="vm-form-grid"><el-form-item label="开始日期（包含当天）" prop="effective_from" :error="metadataErrors.effective_from"><el-date-picker v-model="metadataDraft.effective_from" type="date" format="YYYY-MM-DD" value-format="YYYY-MM-DD" :disabled="metadataDraft.validity !== 'known'" placeholder="留空表示无起点" clearable @change="metadataDraft.unlimited_confirmed = false" /></el-form-item><el-form-item label="结束日期（不包含当天）" prop="effective_to" :error="metadataErrors.effective_to"><el-date-picker v-model="metadataDraft.effective_to" type="date" format="YYYY-MM-DD" value-format="YYYY-MM-DD" :disabled="metadataDraft.validity !== 'known'" placeholder="留空表示无终点" clearable @change="metadataDraft.unlimited_confirmed = false" /></el-form-item></div>
            <p class="vm-help">Asia/Shanghai 业务日期，采用半开区间 [start, end)：开始日包含，结束日不包含。选择“未知”后保存会清除原起止日期，不会根据文件时间猜测。</p>
            <el-form-item v-if="metadataDraft.validity === 'known' && !metadataDraft.effective_from && !metadataDraft.effective_to" prop="unlimited_confirmed" :error="metadataErrors.unlimited_confirmed"><el-checkbox v-model="metadataDraft.unlimited_confirmed">我已明确确认：此版本没有起止限制（不限期）。</el-checkbox></el-form-item>
            <el-form-item label="分类适用（JSON 对象）" prop="applicability_text" :error="metadataErrors.applicability_text"><el-input v-model="metadataDraft.applicability_text" type="textarea" :rows="5" class="vm-code-input" spellcheck="false" /><p class="vm-help">示例：<code>{{ applicabilityExample }}</code>。分类间同时满足，每类数组列出可选值；不推断同义词或用户身份。无条件填写 {}。最多 20 类，每类 1–20 项。</p></el-form-item>
            <el-form-item label="明确替代的版本 ID（可选）" prop="supersedes_id" :error="metadataErrors.supersedes_id"><el-input v-model="metadataDraft.supersedes_id" inputmode="numeric" placeholder="填写同系列版本 ID；留空清除替代关系" /><p class="vm-help">不会根据名称、日期或上传顺序自动推断。目标是否属于同系列仍由服务器校验。</p></el-form-item>
            <el-form-item label="并存说明" prop="coexistence_note" :error="metadataErrors.coexistence_note"><el-input v-model="metadataDraft.coexistence_note" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="说明与其他版本并存的业务边界；无需并存说明时可留空。" /></el-form-item>
            <el-form-item label="修改原因" prop="reason" required :error="metadataErrors.reason"><el-input v-model="metadataDraft.reason" type="textarea" :rows="3" maxlength="1000" show-word-limit placeholder="记录本次修改依据及实际影响，不能为空。" /></el-form-item>
          </el-form>
          <div v-if="metadataError && metadataError.kind !== 'conflict'" class="vm-banner" :class="metadataError.kind === 'info' ? 'vm-info' : 'vm-error'" role="alert"><div><b>{{ metadataError.title }}</b><p>{{ metadataError.message }}</p></div></div>
          <div v-if="Object.keys(metadataErrors).length" class="vm-validation-note" role="alert">尚未提交，请修正表单中标出的输入。</div>
          <div class="vm-actions"><el-button :disabled="isBusy || reloadRequired" @click="discardMetadataDraft">重置为已读取记录</el-button><el-button type="primary" :loading="busy === 'metadata'" :disabled="writeBlocked || metadataStale" @click="saveMetadata">保存实际修改字段</el-button></div>
        </el-tab-pane>

        <el-tab-pane label="历史版本" name="history">
          <div class="vm-tab-intro vm-list-heading"><div><h3>同一系列的物理版本</h3><p>每页 {{ pageSize }} 条；保留分页总数，不静默截断。查看另一版本不会修改服务器记录。</p></div><el-button :loading="historyLoading" :disabled="isBusy" @click="loadHistory">刷新本页版本</el-button></div>
          <div v-if="historyError" class="vm-banner vm-error" role="alert"><div><b>{{ historyError.title }}</b><p>{{ historyError.message }}</p><el-button :disabled="isBusy" @click="loadHistory">重新读取本页</el-button></div></div>
          <p id="vm-history-scroll" class="vm-help">窄屏可在版本表格内横向滚动；发布状态与解析状态独立显示。</p>
          <div class="vm-table-scroll" role="region" tabindex="0" aria-label="系列历史版本表" aria-describedby="vm-history-scroll"><el-table :data="history" row-key="version_id" v-loading="historyLoading" class="vm-wide-table">
            <el-table-column label="版本 / 原文件" min-width="260"><template #default="{ row }"><span class="vm-record-key">VERSION / {{ row.version_id }}</span><b class="vm-cell-title">{{ row.version_label }}</b><span class="vm-cell-note">{{ row.filename }}</span></template></el-table-column>
            <el-table-column label="发布状态" width="110"><template #default="{ row }"><span class="vm-state" :class="'vm-publication-' + row.publication_state">{{ publicationNames[row.publication_state] || row.publication_state }}</span></template></el-table-column>
            <el-table-column label="日期范围" min-width="190"><template #default="{ row }"><span>{{ dateNames[row.date_status] || row.date_status }}</span><span class="vm-cell-note">{{ intervalText(row) }}</span></template></el-table-column>
            <el-table-column label="当前 / 最新解析" min-width="240"><template #default="{ row }"><span>当前：{{ row.current_revision ? '#' + row.current_revision.id + ' · ' + (revisionNames[row.current_revision.state] || row.current_revision.state) : '暂无' }}</span><span class="vm-cell-note">最新：{{ row.latest_revision ? '#' + row.latest_revision.id + ' · ' + (revisionNames[row.latest_revision.state] || row.latest_revision.state) : '暂无' }}</span><span v-if="row.current_revision?.source_integrity === 'legacy_unverified'" class="vm-cell-note vm-warning-text">旧摘录未重建可靠原文定位</span></template></el-table-column>
            <el-table-column label="创建时间（北京时间）" width="180"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column>
            <el-table-column label="查看" width="130"><template #default="{ row }"><el-button text type="primary" :disabled="isBusy || row.version_id === activeVersionId" @click="switchVersion(row.version_id)">{{ row.version_id === activeVersionId ? '当前查看' : '查看此版本' }}</el-button></template></el-table-column>
            <template #empty><span v-if="historyLoading" role="status">正在读取历史版本…</span><span v-else-if="historyError">读取失败，不代表历史为空。</span><span v-else>{{ historyTotal ? '当前页没有记录，请选择前一页或刷新核对。' : '该系列尚无可显示的版本记录。' }}</span></template>
          </el-table></div>
          <div class="vm-pagination"><span>共 {{ historyTotal }} 个版本 · 第 {{ historyPage }} 页</span><el-pagination v-model:current-page="historyPage" background layout="prev, pager, next" :page-size="pageSize" :total="historyTotal" :pager-count="5" :disabled="historyLoading || isBusy" /></div>
        </el-tab-pane>

        <el-tab-pane label="解析修订" name="revisions">
          <div class="vm-tab-intro vm-list-heading"><div><h3>文件不变，解析尝试逐次保留</h3><p>当前修订与最新尝试是两个概念。最新失败不覆盖旧成功修订；重解析不会读取本机修改的文件。</p></div><el-button :loading="revisionsLoading" :disabled="isBusy" @click="loadRevisions()">刷新本页修订</el-button></div>
          <div class="vm-banner vm-info"><AppIcon name="info" :size="18" /><p><b>来源标记：</b>verified 表示原文件来源校验；unverified 表示未校验；legacy_unverified 表示旧摘录保留但未重建可靠原文定位。三者都不是事实正确性评分。</p></div>
          <div v-if="revisionsError" class="vm-banner vm-error" role="alert"><div><b>{{ revisionsError.title }}</b><p>{{ revisionsError.message }}</p><el-button :disabled="isBusy" @click="loadRevisions()">重新读取本页</el-button></div></div>
          <p id="vm-revisions-scroll" class="vm-help">每页 {{ pageSize }} 条；窄屏可在表内横向滚动查看解析器、任务编号和完整错误。</p>
          <div class="vm-table-scroll" role="region" tabindex="0" aria-label="解析修订历史表" aria-describedby="vm-revisions-scroll"><el-table :data="revisions" row-key="id" v-loading="revisionsLoading" class="vm-wide-table vm-revisions-table">
            <el-table-column label="修订 / 任务" width="180"><template #default="{ row }"><b class="vm-cell-title">修订 #{{ row.id }}</b><span class="vm-cell-note">{{ row.task_id == null ? '无关联任务编号' : '任务 #' + row.task_id }}</span><div class="vm-row-flags"><span v-if="row.is_current" class="vm-state">当前修订</span><span v-if="row.id === version.latest_revision?.id" class="vm-state">最新尝试</span></div></template></el-table-column>
            <el-table-column label="解析状态" width="135"><template #default="{ row }"><span class="vm-state" :class="'vm-revision-' + row.state">{{ revisionNames[row.state] || row.state }}</span></template></el-table-column>
            <el-table-column label="来源校验" min-width="225"><template #default="{ row }"><span>{{ integrityNames[row.source_integrity] || row.source_integrity }}</span><span class="vm-cell-note vm-record-key">{{ row.source_integrity }}</span><span v-if="row.source_integrity === 'legacy_unverified'" class="vm-cell-note vm-warning-text">旧摘录保留，未重建可靠原文定位。</span></template></el-table-column>
            <el-table-column label="解析器 / 片段" min-width="170"><template #default="{ row }"><span>{{ row.parser_version || '未记录解析器版本' }}</span><span class="vm-cell-note">{{ row.chunk_count }} 个片段</span></template></el-table-column>
            <el-table-column label="校验值与错误记录" min-width="310"><template #default="{ row }"><p v-if="row.error_message" class="vm-error-text vm-row-message">{{ row.error_message }}</p><details class="vm-row-details"><summary>查看修订校验值</summary><dl><dt>原文件 SHA-256</dt><dd class="vm-hash-text">{{ row.file_sha256 || '未记录' }}</dd><dt>解析内容哈希</dt><dd class="vm-hash-text">{{ row.content_hash || '未记录' }}</dd></dl></details></template></el-table-column>
            <el-table-column label="时间（北京时间）" width="190"><template #default="{ row }"><span class="vm-cell-note">开始：{{ formatTime(row.created_at) }}</span><span class="vm-cell-note">结束：{{ row.finished_at ? formatTime(row.finished_at) : '尚未结束或未记录' }}</span></template></el-table-column>
            <template #empty><span v-if="revisionsLoading" role="status">正在读取解析修订…</span><span v-else-if="revisionsError">读取失败，不代表没有解析历史。</span><span v-else>{{ revisionsTotal ? '当前页没有修订，请选择前一页或刷新。' : '尚无解析修订，不能据此认为资料已经可用。' }}</span></template>
          </el-table></div>
          <div class="vm-pagination"><span>共 {{ revisionsTotal }} 个修订 · 第 {{ revisionsPage }} 页</span><el-pagination v-model:current-page="revisionsPage" background layout="prev, pager, next" :page-size="pageSize" :total="revisionsTotal" :pager-count="5" :disabled="revisionsLoading || isBusy" /></div>
          <div v-if="canManage" class="vm-actions"><el-button :loading="busy === 'reparse'" :disabled="writeBlocked || isBuilding" @click="reparseSavedFile">重新解析已保存文件</el-button><span v-if="isBuilding" class="vm-help">已有构建中的修订，请勿重复提交。</span></div>
          <div v-if="operationError" class="vm-banner vm-error" role="alert"><div><b>{{ operationError.title }}</b><p>{{ operationError.message }}</p><el-button :disabled="isBusy" @click="refreshForReview">重新读取并核对</el-button></div></div>
        </el-tab-pane>

        <el-tab-pane v-if="canManage" label="上传新版本" name="upload">
          <div class="vm-tab-intro"><h3>保存新的物理文件版本</h3><p>目标系列：{{ version.series_title }}（#{{ version.series_id }}）。不会替换当前保存的文件，也不会自动推断替代关系或发布新版本。</p></div>
          <div v-if="uploadStale" class="vm-banner vm-warning" role="alert"><div><b>系列修订已变化</b><p>本次上传基线 #{{ uploadBaseRevision }}，最新已读取系列修订 #{{ version.series_revision }}。所选文件与标签仍保留。</p><div class="vm-actions"><el-button :disabled="isBusy" @click="refreshForReview">读取最新核对</el-button><el-button :disabled="isBusy || reloadRequired" @click="rebaseUploadDraft">核对后更新上传基线</el-button></div></div></div>
          <div v-if="uploadError" class="vm-banner vm-error" role="alert"><div><b>{{ uploadError.title }}</b><p>{{ uploadError.message }}</p><p v-if="uploadError.existingVersionId">服务器提示已有版本 ID：<b>#{{ uploadError.existingVersionId }}</b>。本次不计新增成功，请核对已有记录或选择不同文件。</p><div class="vm-actions"><el-button v-if="uploadError.existingVersionId" :disabled="isBusy" @click="switchVersion(uploadError.existingVersionId)">查看已有版本 #{{ uploadError.existingVersionId }}</el-button><el-button :disabled="isBusy" @click="refreshForReview">读取最新用于核对</el-button><el-button v-if="uploadError.kind === 'conflict' && !uploadError.existingVersionId" :disabled="isBusy || reloadRequired" @click="rebaseUploadDraft">核对后更新上传基线</el-button></div></div></div>
          <el-form ref="uploadFormRef" :model="{ version_label: uploadLabel, file: uploadFiles }" label-position="top" :disabled="isBusy" class="vm-form" @submit.prevent="uploadNewVersion">
            <el-form-item label="新版本标签" prop="version_label" required :error="uploadErrors.version_label"><el-input v-model="uploadLabel" maxlength="80" show-word-limit placeholder="由你填写新版本名称，不根据文件名自动推断" /></el-form-item>
            <el-form-item label="新版本文件（单个，不超过 50 MB）" prop="file" required :error="uploadErrors.file"><el-upload v-model:file-list="uploadFiles" drag :auto-upload="false" :limit="1" :multiple="false" :disabled="isBusy" :on-change="onFileChange" :on-exceed="onFileExceed" :on-remove="onFileRemove"><div class="vm-upload-zone"><AppIcon name="upload" :size="28" /><b>选择文件，或拖放到这里</b><span>这里只选择文件，点击下方确认后才上传。</span></div></el-upload></el-form-item>
            <el-form-item label="替代关系（默认不指定）"><el-checkbox v-model="uploadSupersedes">我明确指定：新版本替代当前所选版本 #{{ version.version_id }}（{{ version.version_label }}）。</el-checkbox><p class="vm-help">不勾选则不发送 supersedes_id，不能依上传顺序推断替代关系。原文件与历史记录仍保留。</p></el-form-item>
          </el-form>
          <div v-if="busy === 'upload'" class="vm-upload-progress" role="status"><el-progress v-if="uploadProgress !== null" :percentage="uploadProgress" /><p>{{ uploadProgress === null ? '正在传输，当前请求未返回可计算的字节总数。' : '显示的是文件传输进度，不是解析或发布进度。' }}</p><p>请等待服务端确认；传输达到 100% 也不代表版本已发布。</p></div>
          <div v-if="Object.keys(uploadErrors).length" class="vm-validation-note" role="alert">尚未上传，请修正文件或标签输入。</div>
          <div class="vm-actions"><span class="vm-record-key">提交基线：系列修订 #{{ uploadBaseRevision }}</span><el-button type="primary" :loading="busy === 'upload'" :disabled="writeBlocked || uploadStale" @click="uploadNewVersion">确认并上传新版本</el-button></div>
          <div v-if="lastUploadedVersionId" class="vm-banner vm-info"><div><b>服务器返回的新版本 #{{ lastUploadedVersionId }}</b><p>请核对草稿、解析修订与生效信息，再决定是否发布。</p><el-button :disabled="isBusy" @click="switchVersion(lastUploadedVersionId)">查看新版本记录</el-button></div></div>
        </el-tab-pane>
      </el-tabs>
    </template>
    <div v-else-if="!loadError" class="vm-empty">尚未读取版本。请选择有效资料，并确认系统已经显式启用版本能力。</div>
  </div>
  <template #footer><div class="vm-footer"><span v-if="isBusy" role="status">{{ confirming ? '请完成确认窗口中的选择。' : '正在提交，请等待服务端结果。' }}</span><span v-else-if="hasPendingInputs">有未提交输入，关闭或切换前会再次确认。</span><span v-else>只有服务端确认的修改才会通知资料页刷新。</span><el-button :disabled="isBusy" @click="requestClose">关闭版本管理</el-button></div></template>
</el-dialog>
</template>

<script setup>
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import {
  ElButton, ElCheckbox, ElDatePicker, ElDialog, ElForm, ElFormItem,
  ElInput, ElMessageBox, ElOption, ElPagination, ElProgress, ElSelect,
  ElSkeleton, ElTabPane, ElTable, ElTableColumn, ElTabs, ElUpload,
} from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/checkbox/style/css.mjs'
import 'element-plus/es/components/date-picker/style/css.mjs'
import 'element-plus/es/components/dialog/style/css.mjs'
import 'element-plus/es/components/form/style/css.mjs'
import 'element-plus/es/components/form-item/style/css.mjs'
import 'element-plus/es/components/input/style/css.mjs'
import 'element-plus/es/components/message-box/style/css.mjs'
import 'element-plus/es/components/option/style/css.mjs'
import 'element-plus/es/components/pagination/style/css.mjs'
import 'element-plus/es/components/progress/style/css.mjs'
import 'element-plus/es/components/select/style/css.mjs'
import 'element-plus/es/components/skeleton/style/css.mjs'
import 'element-plus/es/components/tab-pane/style/css.mjs'
import 'element-plus/es/components/table/style/css.mjs'
import 'element-plus/es/components/table-column/style/css.mjs'
import 'element-plus/es/components/tabs/style/css.mjs'
import 'element-plus/es/components/upload/style/css.mjs'
import AppIcon from './AppIcon.vue'
import { formatBytes } from '../utils/format'
import {
  versionDetailApi, seriesVersionsApi, versionRevisionsApi, patchVersionMetadataApi,
  publishVersionApi, withdrawVersionApi, archiveVersionApi, uploadVersionApi, reparseVersionApi,
} from '../api/versions'

const props = defineProps({ modelValue: { type: Boolean, default: false }, versionId: { type: Number, default: null } })
const emit = defineEmits(['update:modelValue', 'changed'])
const version = ref(null), activeVersionId = ref(null), activeTab = ref('overview')
const loading = ref(false), loadError = ref(null), forbidden = ref(false), notice = ref(null)
const busy = ref(''), confirming = ref(false), reloadRequired = ref(false)
const metadataError = ref(null), operationError = ref(null), uploadError = ref(null)
const metadataFormRef = ref(), uploadFormRef = ref(), actionFormRef = ref()
const metadataErrors = ref({}), uploadErrors = ref({}), actionErrors = ref({})
const emptyMetadata = () => ({ version_label: '', validity: 'unknown', effective_from: null, effective_to: null, applicability_text: '{}', supersedes_id: '', coexistence_note: '', reason: '', unlimited_confirmed: false })
const metadataDraft = ref(emptyMetadata()), metadataBaseline = ref(null), metadataBaseRevision = ref(null)
const metadataInitialUi = ref(JSON.stringify(metadataDraft.value))
const uploadFiles = ref([]), uploadLabel = ref(''), uploadSupersedes = ref(false), uploadBaseRevision = ref(null)
const uploadProgress = ref(null), lastUploadedVersionId = ref(null)
const actionReason = ref(''), acknowledgeUnknown = ref(false)
const history = ref([]), historyPage = ref(1), historyTotal = ref(0), historyLoading = ref(false), historyError = ref(null)
const revisions = ref([]), revisionsPage = ref(1), revisionsTotal = ref(0), revisionsLoading = ref(false), revisionsError = ref(null)
const pageSize = 20, pollLimit = 12, pollInterval = 5000, maxFileSize = 50 * 1024 * 1024
const pollCount = ref(0), pollMessage = ref('')
let session = 0, disposed = false, pollTimer, visibilityListening = false
const controllers = new Map(), requestSequence = { detail: 0, history: 0, revisions: 0 }
const isId = value => Number.isSafeInteger(value) && value > 0
const charLength = value => Array.from(String(value ?? '')).length
const openAndValid = () => props.modelValue && !disposed && isId(activeVersionId.value)
const canManage = computed(() => openAndValid() && !forbidden.value && version.value?.can_manage === true)
const isBusy = computed(() => !!busy.value || confirming.value)
const metadataDirty = computed(() => JSON.stringify(metadataDraft.value) !== metadataInitialUi.value)
const uploadDirty = computed(() => !!uploadFiles.value.length || !!uploadLabel.value || uploadSupersedes.value)
const hasPendingInputs = computed(() => metadataDirty.value || uploadDirty.value || !!actionReason.value || acknowledgeUnknown.value)
const metadataStale = computed(() => !!version.value && metadataBaseRevision.value !== version.value.metadata_revision)
const uploadStale = computed(() => !!version.value && uploadBaseRevision.value !== version.value.series_revision)
const isBuilding = computed(() => version.value?.latest_revision?.state === 'building' || version.value?.current_revision?.state === 'building')
const publicationNames = { draft: '草稿', published: '已发布', withdrawn: '已撤回', archived: '已归档' }
const revisionNames = { building: '解析构建中', success: '解析成功', failed: '解析失败', legacy_empty: '历史空修订' }
const integrityNames = { verified: '原文件来源已校验', legacy_unverified: '历史摘录 · 来源未重建', unverified: '原文件来源未校验' }
const dateNames = { current: '当前日期适用', future: '尚未生效', expired: '已过期', unknown: '日期未知' }
const editableFields = ['version_label', 'validity', 'effective_from', 'effective_to', 'applicability', 'supersedes_id', 'coexistence_note']
const hasLegacySource = computed(() => [version.value?.current_revision, version.value?.latest_revision].some(item => item?.source_integrity === 'legacy_unverified'))
const publishLabel = computed(() => version.value?.publication_state === 'archived' ? '恢复并发布此版本' : '发布此版本')
const applicabilityExample = '{"人群": ["在校学生"], "校区": ["东校区", "西校区"]}'

function intervalText(item) {
  if (!item || item.validity !== 'known') return '日期未知，不推断生效范围'
  if (!item.effective_from && !item.effective_to) return '明确确认不限期'
  return '[' + (item.effective_from || '无起点') + ', ' + (item.effective_to || '无终点') + ')'
}
function formatTime(value) {
  if (!value) return '—'
  const text = String(value), normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(text) ? text : text.replace(' ', 'T') + 'Z'
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return text
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}
function eligibilityText(reason) {
  const text = String(reason)
  if (text.startsWith('not_published:')) return '发布状态：' + (publicationNames[text.slice(14)] || text.slice(14))
  if (text === 'date_future') return '开始日期尚未到达'
  if (text === 'date_expired') return '结束日期已到达，半开区间不再包含该日'
  if (text === 'date_unknown') return '生效日期尚未明确'
  if (text.startsWith('missing_condition:')) return '缺少业务条件：' + text.slice(18)
  if (text.startsWith('mismatched_condition:')) return '业务条件不匹配：' + text.slice(21)
  return text
}
function validCalendarDate(value) {
  if (value == null || value === '') return true
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  if (year < 1 || month < 1 || month > 12 || day < 1) return false
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  return day <= [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
}
function normalizeApplicability(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('适用条件须为 JSON 对象；无条件时填写 {}。')
  const entries = Object.entries(value), seen = new Set(), normalized = []
  if (entries.length > 20) throw new Error('适用分类最多 20 项。')
  for (const [rawKey, rawValues] of entries) {
    const key = rawKey.trim()
    if (!key || charLength(key) > 64) throw new Error('分类名称需为 1–64 个字符。')
    if (seen.has(key)) throw new Error('去除首尾空格后分类名称重复：' + key)
    if (!Array.isArray(rawValues) || rawValues.length < 1 || rawValues.length > 20) throw new Error('每个分类须对应含 1–20 个字符串的数组；不限条件请删除该分类。')
    const values = rawValues.map(value => {
      if (typeof value !== 'string' || !value.trim() || charLength(value.trim()) > 120) throw new Error('适用选项须为 1–120 个字符的非空字符串。')
      return value.trim()
    })
    seen.add(key)
    normalized.push([key, [...new Set(values)].sort()])
  }
  return Object.fromEntries(normalized.sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0))
}
function canonicalMetadata(item) {
  return { version_label: item.version_label || '', validity: item.validity, effective_from: item.effective_from || null, effective_to: item.effective_to || null, applicability: normalizeApplicability(item.applicability || {}), supersedes_id: item.supersedes_id ?? null, coexistence_note: item.coexistence_note || '' }
}
function draftFromValues(values) {
  return { version_label: values.version_label, validity: values.validity, effective_from: values.effective_from, effective_to: values.effective_to, applicability_text: JSON.stringify(values.applicability, null, 2), supersedes_id: values.supersedes_id == null ? '' : String(values.supersedes_id), coexistence_note: values.coexistence_note, reason: '', unlimited_confirmed: false }
}
function adoptMetadata(item) {
  const values = canonicalMetadata(item)
  metadataBaseline.value = values
  metadataBaseRevision.value = item.metadata_revision
  metadataDraft.value = draftFromValues(values)
  metadataInitialUi.value = JSON.stringify(metadataDraft.value)
  metadataErrors.value = {}
  metadataError.value = null
}
function compileMetadataDraft(draft, versionId, { requireReason = true, requireUnlimited = true } = {}) {
  const errors = {}, label = draft.version_label.trim(), reason = draft.reason.trim()
  if (!label || charLength(label) > 80) errors.version_label = '版本标签须为 1–80 个字符。'
  if (!['known', 'unknown'].includes(draft.validity)) errors.validity = '请选择日期已明确或日期未知。'
  const start = draft.validity === 'known' ? draft.effective_from || null : null
  const end = draft.validity === 'known' ? draft.effective_to || null : null
  if (!validCalendarDate(start)) errors.effective_from = '开始日期须为有效的 YYYY-MM-DD。'
  if (!validCalendarDate(end)) errors.effective_to = '结束日期须为有效的 YYYY-MM-DD。'
  if (start && end && !errors.effective_from && !errors.effective_to && start >= end) errors.effective_to = '结束日期必须晚于开始日期；结束日不在适用范围内。'
  if (requireUnlimited && draft.validity === 'known' && !start && !end && !draft.unlimited_confirmed) errors.unlimited_confirmed = '两端留空表示不限期，请明确勾选确认，不会自动默认。'
  let applicability = {}
  try { applicability = normalizeApplicability(JSON.parse(draft.applicability_text)) } catch (e) { errors.applicability_text = e instanceof SyntaxError ? 'JSON 无法解析，请检查引号、逗号与括号。' : e.message }
  const predecessorText = String(draft.supersedes_id ?? '').trim()
  let supersedesId = null
  if (predecessorText) {
    supersedesId = Number(predecessorText)
    if (!/^[1-9]\d*$/.test(predecessorText) || !isId(supersedesId)) errors.supersedes_id = '替代版本 ID 必须为正整数，或留空清除。'
    else if (supersedesId === versionId) errors.supersedes_id = '不能将当前版本设置为自身的替代对象。'
  }
  if (charLength(draft.coexistence_note) > 1000) errors.coexistence_note = '并存说明不能超过 1000 个字符。'
  if (requireReason && (!reason || charLength(reason) > 1000)) errors.reason = '请填写 1–1000 个字符的修改原因。'
  return { values: { version_label: label, validity: draft.validity, effective_from: start, effective_to: end, applicability, supersedes_id: supersedesId, coexistence_note: draft.coexistence_note }, reason, errors }
}
function changedFields(values, baseline) {
  return Object.fromEntries(editableFields.filter(key => JSON.stringify(values[key]) !== JSON.stringify(baseline?.[key])).map(key => [key, values[key]]))
}
function errorInfo(error, fallback = '请求未完成，请检查服务连接后重试。') {
  const status = error?.response?.status || null, detail = error?.response?.data?.detail
  let message = typeof detail === 'string' ? detail : detail?.message
  if (!message && Array.isArray(detail)) message = detail.map(item => (item.loc?.filter(part => part !== 'body').join('.') || '输入') + '：' + (item.msg || '格式不正确')).join('；')
  message ||= error?.code === 'ECONNABORTED' ? '请求超时，服务端结果尚未确认，请先重新读取核对。' : fallback
  const rawExisting = detail?.existing_version_id
  const existingVersionId = (typeof rawExisting === 'number' || typeof rawExisting === 'string' && /^[1-9]\d*$/.test(rawExisting)) && isId(Number(rawExisting)) ? Number(rawExisting) : null
  let title = status ? '请求失败（' + status + '）' : '连接或请求异常', kind = 'error'
  if (status === 401 || status === 403) { title = '访问或管理权限不足'; kind = 'forbidden' }
  if (status === 409) { title = existingVersionId ? '重复版本，未确认新增成功' : '版本已变更，请核对后再提交'; kind = 'conflict' }
  if ([404, 405, 501].includes(status)) { title = '版本记录或接口不可用'; kind = 'unavailable' }
  if (status === 503 && /version|migration|schema|迁移|版本.*未/i.test(String(detail?.code || '') + ' ' + message)) { title = '版本能力未启用或需要显式迁移'; kind = 'unavailable' }
  return { status, title, message, kind, existingVersionId }
}
function isCanceled(error) { return error?.code === 'ERR_CANCELED' || error?.name === 'AbortError' }
function focusErrors(form, errors) { const field = Object.keys(errors)[0]; if (field) nextTick(() => form.value?.scrollToField?.(field)) }

const writeBlocked = computed(() => !canManage.value || isBusy.value || loading.value || reloadRequired.value)
function clearPoll() { clearTimeout(pollTimer); pollTimer = undefined }
function replaceController(key) {
  controllers.get(key)?.abort()
  const controller = new AbortController()
  controllers.set(key, controller)
  return controller
}
function abortAll() {
  clearPoll()
  for (const controller of controllers.values()) controller.abort()
  controllers.clear()
  for (const key of Object.keys(requestSequence)) requestSequence[key]++
}
function sameSession(token, id = activeVersionId.value) { return openAndValid() && token === session && id === activeVersionId.value }
function isVisible() { return typeof document !== 'undefined' && document.visibilityState === 'visible' }
function onVisibilityChange() {
  clearPoll()
  if (!openAndValid() || !isBuilding.value) return
  if (!isVisible()) pollMessage.value = '页面不可见，自动更新已暂停。'
  else schedulePoll()
}
function listenVisibility() {
  if (typeof document !== 'undefined' && !visibilityListening) {
    document.addEventListener('visibilitychange', onVisibilityChange)
    visibilityListening = true
  }
}
function clearSnapshot() {
  version.value = null
  history.value = []; historyTotal.value = 0
  revisions.value = []; revisionsTotal.value = 0
  historyLoading.value = false; revisionsLoading.value = false
  for (const key of ['history', 'revisions']) { controllers.get(key)?.abort(); controllers.delete(key); requestSequence[key]++ }
  clearPoll()
}
function resetInputs() {
  metadataDraft.value = emptyMetadata(); metadataInitialUi.value = JSON.stringify(metadataDraft.value)
  metadataBaseline.value = null; metadataBaseRevision.value = null; metadataErrors.value = {}; metadataError.value = null
  uploadFiles.value = []; uploadLabel.value = ''; uploadSupersedes.value = false; uploadBaseRevision.value = null
  uploadErrors.value = {}; uploadError.value = null; uploadProgress.value = null; lastUploadedVersionId.value = null
  actionReason.value = ''; acknowledgeUnknown.value = false; actionErrors.value = {}; operationError.value = null
}
function endSession() {
  session++
  abortAll(); clearSnapshot(); resetInputs()
  activeVersionId.value = null; activeTab.value = 'overview'
  loading.value = false; busy.value = ''; confirming.value = false; forbidden.value = false; reloadRequired.value = false
  loadError.value = null; historyError.value = null; revisionsError.value = null; notice.value = null
  historyPage.value = 1; revisionsPage.value = 1; pollCount.value = 0; pollMessage.value = ''
  if (typeof document !== 'undefined' && visibilityListening) {
    document.removeEventListener('visibilitychange', onVisibilityChange)
    visibilityListening = false
  }
}
function denyCurrentAccess(info) {
  forbidden.value = true; reloadRequired.value = true
  clearSnapshot()
  loadError.value = info
  activeTab.value = 'overview'
  // Drafts stay in memory until explicit close/discard. No denied server content or form is rendered.
}
function assertVersionPayload(data, id) {
  if (!data || data.version_id !== id || !isId(data.series_id) || !isId(data.kb_id) || !isId(data.metadata_revision) || !isId(data.series_revision) || typeof data.can_manage !== 'boolean' || typeof data.can_publish !== 'boolean') {
    throw { localMessage: '版本响应缺少冻结契约字段或版本 ID 不匹配。请先完成版本 API 接入；本组件不会猜测或迁移数据。' }
  }
  if (!Object.hasOwn(publicationNames, data.publication_state) || !['known', 'unknown'].includes(data.validity)) throw { localMessage: '版本响应包含不支持的发布或日期状态，请核对服务版本。' }
  canonicalMetadata(data)
}
function applyVersion(data, preserveDraft = true) {
  version.value = data; forbidden.value = false; reloadRequired.value = false
  if (!metadataBaseline.value || !preserveDraft || !metadataDirty.value) adoptMetadata(data)
  if (!uploadDirty.value || !isId(uploadBaseRevision.value)) uploadBaseRevision.value = data.series_revision
  if (!data.can_manage && ['metadata', 'upload'].includes(activeTab.value)) activeTab.value = 'overview'
}
async function loadDetail({ quiet = false, preserveDraft = true, scheduleNext = true } = {}) {
  if (!openAndValid()) return false
  const token = session, id = activeVersionId.value, sequence = ++requestSequence.detail
  const controller = replaceController('detail')
  if (!quiet) loading.value = true
  loadError.value = null
  try {
    const data = await versionDetailApi(id, { signal: controller.signal })
    if (!sameSession(token, id) || sequence !== requestSequence.detail) return false
    assertVersionPayload(data, id)
    applyVersion(data, preserveDraft)
    return true
  } catch (error) {
    if (!sameSession(token, id) || sequence !== requestSequence.detail || isCanceled(error)) return false
    const info = errorInfo(error, error?.localMessage || '版本读取失败，请检查服务连接后重试。')
    loadError.value = info; reloadRequired.value = true
    if ([401, 403].includes(info.status)) denyCurrentAccess(info)
    else if ([404, 405, 501].includes(info.status) || error?.localMessage) clearSnapshot()
    return false
  } finally {
    if (sameSession(token, id) && sequence === requestSequence.detail) {
      loading.value = false
      if (controllers.get('detail') === controller) controllers.delete('detail')
      if (scheduleNext) schedulePoll()
    }
  }
}
function assertPage(data, requestedPage) {
  if (!data || !Array.isArray(data.items) || !Number.isSafeInteger(data.total) || data.total < 0 || data.page !== requestedPage || data.size !== pageSize) {
    throw { localMessage: '分页响应不符合 items/total/page/size 契约，未将不完整结果当作完整历史。' }
  }
}
async function loadHistory() {
  if (!openAndValid() || !version.value || forbidden.value) return
  const token = session, id = activeVersionId.value, seriesId = version.value.series_id, page = historyPage.value
  const sequence = ++requestSequence.history, controller = replaceController('history')
  historyLoading.value = true; historyError.value = null; history.value = []
  try {
    const data = await seriesVersionsApi(seriesId, { page, size: pageSize, signal: controller.signal })
    if (!sameSession(token, id) || sequence !== requestSequence.history) return
    assertPage(data, page)
    if (data.items.some(item => !isId(item.version_id) || item.series_id !== seriesId)) throw { localMessage: '返回了其他系列的版本，已拒绝展示，请核对接口。' }
    history.value = data.items; historyTotal.value = data.total
  } catch (error) {
    if (!sameSession(token, id) || sequence !== requestSequence.history || isCanceled(error)) return
    history.value = []; historyTotal.value = 0
    historyError.value = errorInfo(error, error?.localMessage || '历史版本读取失败，请重试。')
    if ([401, 403].includes(historyError.value.status)) denyCurrentAccess(historyError.value)
  } finally {
    if (sameSession(token, id) && sequence === requestSequence.history) {
      historyLoading.value = false
      if (controllers.get('history') === controller) controllers.delete('history')
    }
  }
}
async function loadRevisions({ quiet = false } = {}) {
  if (!openAndValid() || !version.value || forbidden.value) return
  const token = session, id = activeVersionId.value, page = revisionsPage.value
  const sequence = ++requestSequence.revisions, controller = replaceController('revisions')
  if (!quiet) { revisionsLoading.value = true; revisions.value = [] }
  revisionsError.value = null
  try {
    const data = await versionRevisionsApi(id, { page, size: pageSize, signal: controller.signal })
    if (!sameSession(token, id) || sequence !== requestSequence.revisions) return
    assertPage(data, page)
    if (data.items.some(item => !isId(item.id) || item.version_id !== id)) throw { localMessage: '返回了其他版本的解析修订，已拒绝展示，请核对接口。' }
    revisions.value = data.items; revisionsTotal.value = data.total
  } catch (error) {
    if (!sameSession(token, id) || sequence !== requestSequence.revisions || isCanceled(error)) return
    revisions.value = []; revisionsTotal.value = 0
    revisionsError.value = errorInfo(error, error?.localMessage || '解析修订读取失败，请重试。')
    if ([401, 403].includes(revisionsError.value.status)) denyCurrentAccess(revisionsError.value)
  } finally {
    if (sameSession(token, id) && sequence === requestSequence.revisions) {
      revisionsLoading.value = false
      if (controllers.get('revisions') === controller) controllers.delete('revisions')
    }
  }
}
function schedulePoll() {
  clearPoll()
  if (!openAndValid() || !version.value || forbidden.value || loadError.value || isBusy.value || loading.value) return
  if (!isBuilding.value) { pollMessage.value = '当前没有构建中的解析修订；可手动刷新。'; return }
  if (!isVisible()) { pollMessage.value = '页面不可见，自动更新已暂停。'; return }
  if (pollCount.value >= pollLimit) { pollMessage.value = '本轮已自动更新 ' + pollLimit + ' 次，已暂停；可手动刷新或继续观察。'; return }
  pollMessage.value = '解析构建中，每 5 秒核对一次；本轮 ' + pollCount.value + ' / ' + pollLimit + ' 次。'
  const token = session, id = activeVersionId.value
  pollTimer = setTimeout(async () => {
    pollTimer = undefined
    if (!sameSession(token, id) || !isVisible() || isBusy.value) return
    pollCount.value++
    const ok = await loadDetail({ quiet: true, preserveDraft: true, scheduleNext: false })
    if (ok && sameSession(token, id) && isVisible() && activeTab.value === 'revisions') await loadRevisions({ quiet: true })
    if (sameSession(token, id)) schedulePoll()
  }, pollInterval)
}
function resumePolling() { pollCount.value = 0; schedulePoll() }
async function refreshForReview() {
  if (!openAndValid() || isBusy.value) return
  pollCount.value = 0
  const ok = await loadDetail({ preserveDraft: true })
  if (ok) {
    notice.value = { kind: 'info', text: hasPendingInputs.value ? '已读取最新记录用于核对；未提交的输入仍保留，不会自动更新草稿提交基线。' : '已读取最新版本记录。' }
    if (activeTab.value === 'history') await loadHistory()
    else if (activeTab.value === 'revisions') await loadRevisions()
  }
}
async function confirmChoice(message, title, confirmButtonText = '确认') {
  const token = session, id = activeVersionId.value
  confirming.value = true; clearPoll()
  try {
    await ElMessageBox.confirm(message, title, { type: 'warning', confirmButtonText, cancelButtonText: '取消', closeOnClickModal: false, distinguishCancelAndClose: true })
    return sameSession(token, id)
  } catch { return false }
  finally { if (sameSession(token, id)) { confirming.value = false; schedulePoll() } }
}
async function beginVersion(id) {
  if (!props.modelValue || !isId(id) || disposed) return
  session++; abortAll(); clearSnapshot(); resetInputs()
  activeVersionId.value = id; activeTab.value = 'overview'
  loading.value = false; busy.value = ''; confirming.value = false; forbidden.value = false; reloadRequired.value = false
  loadError.value = null; historyError.value = null; revisionsError.value = null; notice.value = null
  historyPage.value = 1; revisionsPage.value = 1; pollCount.value = 0; pollMessage.value = ''
  listenVisibility()
  await loadDetail({ preserveDraft: false })
}
async function switchVersion(id) {
  if (!isId(id) || id === activeVersionId.value || isBusy.value || !props.modelValue) return
  if (hasPendingInputs.value && !await confirmChoice('切换到版本 #' + id + ' 会放弃当前未提交的元数据、原因和上传文件。已保存的服务器记录不受影响。', '切换查看版本？', '放弃草稿并切换')) return
  await beginVersion(id)
}
async function canClose() {
  if (isBusy.value) return false
  if (!hasPendingInputs.value) return true
  return confirmChoice('尚有未提交的输入。关闭只放弃本面板草稿，不会撤销已经由服务器确认的操作。', '关闭版本管理？', '放弃草稿并关闭')
}
async function beforeClose(done) { if (await canClose()) done() }
function dialogVisibilityChanged(value) { if (!value) { endSession(); emit('update:modelValue', false) } }
async function requestClose() { if (await canClose()) dialogVisibilityChanged(false) }
watch(() => [props.modelValue, props.versionId], async ([visible, id]) => {
  if (!visible) { endSession(); return }
  if (!isId(id)) {
    endSession()
    loadError.value = { kind: 'error', title: '未选择有效文档版本', message: '请从已启用版本能力的资料页选择一个有效版本 ID。', status: null }
    return
  }
  if (activeVersionId.value == null) await beginVersion(id)
  else if (id !== activeVersionId.value) await switchVersion(id)
}, { immediate: true })
watch(activeTab, tab => {
  if (tab === 'history') loadHistory()
  else if (tab === 'revisions') loadRevisions()
})
watch(historyPage, () => { if (activeTab.value === 'history') loadHistory() })
watch(revisionsPage, () => { if (activeTab.value === 'revisions') loadRevisions() })
onBeforeUnmount(() => { disposed = true; endSession() })

async function performMutation(kind, request, errorTarget, successText, { onConfirmed, onFresh } = {}) {
  if (writeBlocked.value || !version.value) {
    errorTarget.value = { kind: 'error', title: '当前不能提交', message: '请确认管理权限，等待现有请求完成，并先重新读取未确认的服务器状态。', status: null }
    return false
  }
  const token = session, id = activeVersionId.value, controller = replaceController('mutation')
  busy.value = kind; errorTarget.value = null; notice.value = null; clearPoll()
  try {
    const result = await request(controller.signal)
    if (!sameSession(token, id)) return false
    onConfirmed?.(result)
    emit('changed')
    const refreshed = await loadDetail({ preserveDraft: kind !== 'metadata' })
    if (!sameSession(token, id)) return false
    if (refreshed) {
      notice.value = { kind: 'success', text: successText }
      onFresh?.(result)
      if (activeTab.value === 'history') await loadHistory()
      else if (activeTab.value === 'revisions') await loadRevisions()
    } else {
      reloadRequired.value = true
      notice.value = { kind: 'warning', text: '操作已获服务端确认，但最新记录未能读取。请先重新读取并核对，不要把刷新失败当作未提交后重复操作。' }
    }
    return true
  } catch (error) {
    if (!sameSession(token, id) || isCanceled(error)) return false
    const info = errorInfo(error, error?.localMessage || '提交结果未确认，输入仍保留；请先读取最新记录核对后再重试。')
    errorTarget.value = info
    if ([401, 403].includes(info.status)) denyCurrentAccess(info)
    if (!info.status || [408, 409, 500, 502, 503, 504].includes(info.status)) reloadRequired.value = true
    return false
  } finally {
    if (sameSession(token, id)) {
      busy.value = ''
      if (controllers.get('mutation') === controller) controllers.delete('mutation')
      schedulePoll()
    }
  }
}
async function saveMetadata() {
  metadataError.value = null
  if (!canManage.value || isBusy.value || !version.value) return
  if (metadataStale.value || reloadRequired.value) {
    metadataError.value = { kind: 'conflict', title: '提交基线尚未核对', message: '草稿仍保留。请先读取最新记录，再明确选择保留改动更新基线，或放弃草稿采用最新值。', status: null }
    return
  }
  const parsed = compileMetadataDraft(metadataDraft.value, activeVersionId.value)
  metadataErrors.value = parsed.errors
  if (Object.keys(parsed.errors).length) { focusErrors(metadataFormRef, parsed.errors); return }
  const changes = changedFields(parsed.values, metadataBaseline.value)
  if (!Object.keys(changes).length) {
    metadataError.value = { kind: 'info', title: '没有实际字段修改', message: '原因本身不会改变元数据。请修改需要更新的字段，或返回概览。', status: null }
    return
  }
  const id = activeVersionId.value, expectedRevision = metadataBaseRevision.value
  await performMutation('metadata', signal => patchVersionMetadataApi(id, { expected_revision: expectedRevision, reason: parsed.reason, ...changes }, { signal }), metadataError, '元数据已保存，以下记录已从服务器重新读取。')
}
async function discardMetadataDraft() {
  if (!version.value || isBusy.value || reloadRequired.value) return
  if (metadataDirty.value && !await confirmChoice('将丢弃本页未保存的元数据输入和修改原因，采用已重新读取的服务器记录。不会修改服务器。', '采用最新元数据？', '放弃元数据草稿')) return
  adoptMetadata(version.value)
  notice.value = { kind: 'info', text: '已采用最新元数据，仅重置本页草稿，未提交修改。' }
}
async function rebaseMetadataDraft() {
  if (!canManage.value || isBusy.value || !version.value || reloadRequired.value) return
  const parsed = compileMetadataDraft(metadataDraft.value, activeVersionId.value, { requireReason: false, requireUnlimited: false })
  metadataErrors.value = parsed.errors
  if (Object.keys(parsed.errors).length) { focusErrors(metadataFormRef, parsed.errors); return }
  const changes = changedFields(parsed.values, metadataBaseline.value)
  const latestRevision = version.value.metadata_revision
  if (!await confirmChoice('将以服务器修订 #' + latestRevision + ' 为新提交基线，保留你实际改动的字段，其余采用最新记录。日期按一组保留。此操作不会保存，请再次核对后提交。', '保留改动并更新基线？', '核对后更新基线')) return
  if (version.value?.metadata_revision !== latestRevision) {
    metadataError.value = { kind: 'conflict', title: '服务器记录再次变化', message: '未更新草稿基线，请重新读取后核对。', status: null }
    return
  }
  const latest = canonicalMetadata(version.value), merged = { ...latest, ...changes }, reason = metadataDraft.value.reason
  if (['validity', 'effective_from', 'effective_to'].some(key => Object.hasOwn(changes, key))) {
    for (const key of ['validity', 'effective_from', 'effective_to']) merged[key] = parsed.values[key]
  }
  metadataBaseline.value = latest
  metadataBaseRevision.value = latestRevision
  metadataInitialUi.value = JSON.stringify(draftFromValues(latest))
  metadataDraft.value = { ...draftFromValues(merged), reason }
  metadataError.value = null
  notice.value = { kind: 'info', text: '提交基线已明确更新，草稿尚未保存。请复核日期、适用条件和替代关系后再提交。' }
}
const metadataFieldNames = { version_label: '版本标签', validity: '日期明确性', effective_from: '开始日期', effective_to: '结束日期', applicability: '分类适用', supersedes_id: '替代版本 ID', coexistence_note: '并存说明' }
const showValue = value => value == null || value === '' ? '（空）' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
const metadataReviewRows = computed(() => {
  if (!version.value || !metadataBaseline.value) return []
  const current = canonicalMetadata(version.value), parsed = compileMetadataDraft(metadataDraft.value, activeVersionId.value, { requireReason: false, requireUnlimited: false })
  return editableFields.map(key => ({ key, label: metadataFieldNames[key], baseline: showValue(metadataBaseline.value[key]), current: showValue(current[key]), draft: key === 'applicability' && parsed.errors.applicability_text ? metadataDraft.value.applicability_text : showValue(parsed.values[key]) }))
})
function validateActionReason() {
  const reason = actionReason.value.trim()
  actionErrors.value = {}
  if (!reason || charLength(reason) > 1000) actionErrors.value.reason = '请填写 1–1000 个字符的操作原因。'
  return reason
}
async function publicationAction(action) {
  if (!canManage.value || isBusy.value || !version.value || reloadRequired.value) return
  operationError.value = null
  const item = version.value, reason = validateActionReason()
  if (action === 'publish' && item.validity === 'unknown' && !acknowledgeUnknown.value) actionErrors.value.acknowledge_unknown = '日期未知时，必须明确确认保留未知状态后再发布。'
  if (Object.keys(actionErrors.value).length) { focusErrors(actionFormRef, actionErrors.value); return }
  if (action === 'publish' && (!item.can_publish || item.publication_state === 'published')) {
    operationError.value = { kind: 'error', title: '尚不能发布', message: '请先建立成功且来源为 verified 的解析修订，并核对服务器发布条件；已发布版本无需重复发布。', status: null }
    return
  }
  if (action === 'withdraw' && item.publication_state !== 'published' || action === 'archive' && item.publication_state === 'archived') return
  const labels = { publish: item.publication_state === 'archived' ? '恢复并发布' : '发布', withdraw: '撤回', archive: '归档' }
  const consequences = {
    publish: '发布不等于事实可信。未来版本在开始日期之前不会用于当前时间范围；未知日期仍保持未知。',
    withdraw: '撤回后不再作为已发布版本参与后续范围选择；保留原文件、解析修订和历史引用。',
    archive: '归档保留原文件、解析修订和历史记录，不是删除。恢复时须重新满足发布前置。',
  }
  if (!Object.hasOwn(labels, action)) return
  const id = item.version_id, expectedRevision = item.metadata_revision, acknowledge = item.validity === 'unknown' && acknowledgeUnknown.value
  const message = '对象：' + item.filename + ' / ' + item.version_label + '（版本 #' + id + '）。' + consequences[action] + '；原因：' + reason
  if (!await confirmChoice(message, labels[action] + '此版本？', '确认' + labels[action])) return
  const methods = { publish: publishVersionApi, withdraw: withdrawVersionApi, archive: archiveVersionApi }
  const payload = { expected_revision: expectedRevision, reason, ...(action === 'publish' ? { acknowledge_unknown: acknowledge } : {}) }
  await performMutation(action, signal => methods[action](id, payload, { signal }), operationError, '服务器已确认' + labels[action] + '操作，当前状态以下方记录为准。', {
    onFresh: () => {
      actionReason.value = ''; acknowledgeUnknown.value = false; actionErrors.value = {}
      const expectedState = { publish: 'published', withdraw: 'withdrawn', archive: 'archived' }[action]
      if (version.value.publication_state !== expectedState) notice.value = { kind: 'warning', text: '服务器接受了操作，但最新状态已不是目标状态。请核对是否存在并发操作，不应据此认定当前仍已' + labels[action] + '。' }
    },
  })
}
async function reparseSavedFile() {
  if (!canManage.value || isBusy.value || !version.value || reloadRequired.value || isBuilding.value) return
  const item = version.value, id = item.version_id, kbId = item.kb_id
  if (!await confirmChoice('将重新解析系统已保存的「' + item.filename + '」（版本 #' + id + '），不会读取本机修改后的文件。失败保留旧成功修订；修改文件必须上传新版本。提交任务不等于解析成功或发布。', '重新解析已保存文件？', '提交重新解析')) return
  await performMutation('reparse', signal => reparseVersionApi(kbId, id, { signal }), operationError, '重新解析任务已提交；当前成功修订与最新尝试分别展示，发布状态未由本页面自动改变。', { onFresh: () => { pollCount.value = 0 } })
}
function onFileChange(file) {
  uploadErrors.value = {}; uploadError.value = null; uploadProgress.value = null
  const size = file.raw?.size ?? file.size
  if (!Number.isFinite(size) || size <= 0 || size > maxFileSize) {
    uploadFiles.value = uploadFiles.value.filter(item => item.uid !== file.uid)
    uploadErrors.value.file = size > maxFileSize ? '单文件不能超过 50 MB。' : '请选择非空文件。'
  }
}
function onFileExceed() { uploadErrors.value.file = '每次仅上传一个新版本文件。请先移除当前选择，再选择其他文件。' }
function onFileRemove() { uploadErrors.value.file = ''; uploadProgress.value = null }
async function rebaseUploadDraft() {
  if (!canManage.value || !version.value || isBusy.value || reloadRequired.value) return
  const revision = version.value.series_revision
  if (!await confirmChoice('将保留所选文件、版本标签与替代选项，以最新系列修订 #' + revision + ' 重新提交。此处只更新本地基线，不会开始上传；重复内容仍会被服务器拒绝。', '更新上传基线？', '核对后更新基线')) return
  if (version.value?.series_revision !== revision) {
    uploadError.value = { kind: 'conflict', title: '系列再次变化', message: '请重新读取并核对，尚未更新上传基线。', status: null }
    return
  }
  uploadBaseRevision.value = revision
  uploadError.value = null
  notice.value = { kind: 'info', text: '已更新上传基线，所选文件仍未上传。请确认标签与替代关系后提交。' }
}
async function uploadNewVersion() {
  if (!canManage.value || isBusy.value || !version.value) return
  uploadError.value = null; uploadErrors.value = {}
  const file = uploadFiles.value[0], label = uploadLabel.value.trim()
  if (!file?.raw || !file.raw.size || file.raw.size > maxFileSize) uploadErrors.value.file = '请选择一个非空且不超过 50 MB 的文件。'
  if (!label || charLength(label) > 80) uploadErrors.value.version_label = '请填写 1–80 个字符的版本标签。'
  if (Object.keys(uploadErrors.value).length) { focusErrors(uploadFormRef, uploadErrors.value); return }
  if (uploadStale.value || reloadRequired.value) {
    uploadError.value = { kind: 'conflict', title: '系列提交基线需要核对', message: '文件与标签仍保留。请先重新读取，再明确更新上传基线，不会自动覆盖其他人的新增版本。', status: null }
    return
  }
  const item = version.value, seriesId = item.series_id, predecessor = uploadSupersedes.value ? item.version_id : null
  if (!await confirmChoice('文件：' + file.name + '；标签：' + label + '；系列：' + item.series_title + '（#' + seriesId + '）。' + (predecessor ? '已明确选择替代版本 #' + predecessor + '。' : '没有指定替代关系，不会自动推断。') + '上传后为草稿，解析任务不等于发布。', '上传新的物理版本？', '确认上传新版本')) return
  const data = new FormData()
  data.append('file', file.raw)
  data.append('version_label', label)
  data.append('expected_series_revision', String(uploadBaseRevision.value))
  if (predecessor) data.append('supersedes_id', String(predecessor))
  const token = session, id = activeVersionId.value
  uploadProgress.value = null
  await performMutation('upload', signal => uploadVersionApi(seriesId, data, { signal, onUploadProgress: event => {
    if (!sameSession(token, id) || busy.value !== 'upload') return
    uploadProgress.value = event.total > 0 ? Math.min(100, Math.round(event.loaded / event.total * 100)) : null
  } }), uploadError, '上传请求已获服务端确认，新版本仍需核对解析与发布状态；不会自动发布。', {
    onConfirmed: result => {
      lastUploadedVersionId.value = isId(result?.version_id) && result.series_id === seriesId ? result.version_id : null
      uploadFiles.value = []; uploadLabel.value = ''; uploadSupersedes.value = false; uploadProgress.value = null
    },
    onFresh: result => {
      uploadBaseRevision.value = version.value.series_revision
      if (!lastUploadedVersionId.value) notice.value = { kind: 'warning', text: '上传已获服务端确认，但响应未给出可核对的新版本 ID。请打开历史版本确认，不要重复上传。' }
      else if (result.publication_state !== 'draft') notice.value = { kind: 'warning', text: '服务器返回的新版本不是预期草稿状态，请查看版本 #' + result.version_id + ' 核对。本页面没有自动调用发布接口。' }
    },
  })
}
async function copySha() {
  if (!version.value?.sha256) return
  const token = session, id = activeVersionId.value
  try {
    if (!globalThis.navigator?.clipboard?.writeText) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(version.value.sha256)
    if (sameSession(token, id)) notice.value = { kind: 'info', text: '已复制 SHA-256。哈希一致仅用于文件核对，不证明内容真实。' }
  } catch {
    if (sameSession(token, id)) notice.value = { kind: 'warning', text: '当前环境不能自动复制，可选择哈希文本后手动复制。' }
  }
}
</script>

<style scoped>
.vm-header { min-width: 0; padding-right: 24px; }
.vm-header h2 { margin: 8px 0; font-family: var(--font-display); font-size: 24px; line-height: 1.4; color: var(--color-ink); }
.vm-header p { margin: 0; color: var(--color-secondary); font-size: 14px; overflow-wrap: anywhere; }
.vm-eyebrow { color: var(--color-accent); font: 12px/1.65 var(--font-mono); letter-spacing: .06em; }
.vm-body { min-width: 0; color: var(--color-ink); font-size: 16px; line-height: 1.65; }
.vm-body :is(p, h3, h4, dd, li, label), .vm-footer { overflow-wrap: anywhere; }
.vm-toolbar { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 24px; }
.vm-toolbar p { flex: 1 1 320px; margin: 0; font-size: 14px; color: var(--color-secondary); }
.vm-banner { display: flex; align-items: flex-start; gap: 8px; padding: 16px; margin: 16px 0; border-left: 3px solid var(--color-info); background: var(--color-info-soft); color: var(--color-ink); font-size: 14px; }
.vm-banner > .app-icon { margin-top: 3px; color: var(--color-info); }
.vm-banner > div, .vm-banner > p { min-width: 0; }
.vm-banner p { margin: 4px 0 0; white-space: pre-wrap; }
.vm-banner .vm-actions { margin-top: 12px; }
.vm-warning { border-color: var(--color-warning); background: var(--color-warning-soft); }
.vm-warning > .app-icon { color: var(--color-warning); }
.vm-error { border-color: var(--color-danger); background: var(--color-danger-soft); }
.vm-error > .app-icon { color: var(--color-danger); }
.vm-success { border-color: var(--color-success); background: var(--color-success-soft); }
.vm-success > .app-icon { color: var(--color-success); }
.vm-error-text, .vm-validation-note { color: var(--color-danger); white-space: pre-wrap; overflow-wrap: anywhere; }
.vm-warning-text { color: var(--color-warning); }
.vm-loading, .vm-empty { padding: 24px 0; color: var(--color-secondary); }
.vm-record-heading { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 16px; padding: 16px 0 24px; border-bottom: 1px solid var(--color-line); }
.vm-record-heading > div { min-width: 0; }
.vm-record-heading h3 { margin: 8px 0; font-family: var(--font-display); font-size: 24px; line-height: 1.4; }
.vm-record-heading p { margin: 0; font-size: 12px; color: var(--color-secondary); }
.vm-poll { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 16px 0; font-size: 12px; color: var(--color-secondary); }
.vm-tabs { min-width: 0; }
.vm-tabs :deep(.el-tabs__content), .vm-tabs :deep(.el-tab-pane) { min-width: 0; }
.vm-tabs :deep(.el-tabs__item) { font-size: 14px; min-height: 44px; }
.vm-section { padding: 24px 0; border-bottom: 1px solid var(--color-line); }
.vm-section-heading h3, .vm-tab-intro h3 { margin: 8px 0 16px; font-family: var(--font-display); font-size: 22px; line-height: 1.4; }
.vm-section h4 { margin: 0 0 8px; font-size: 14px; color: var(--color-secondary); }
.vm-status-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; }
.vm-status-grid > div { min-width: 0; padding-left: 16px; border-left: 1px solid var(--color-line); }
.vm-status-grid > div:first-child { padding-left: 0; border-left: 0; }
.vm-status-grid p { margin: 8px 0 0; font-size: 14px; }
.vm-state { display: inline-flex; width: fit-content; align-items: center; padding: 3px 8px; border: 1px solid var(--color-line); border-radius: 4px; background: var(--color-page); color: var(--color-secondary); font-size: 12px; line-height: 1.55; }
.vm-publication-published { color: var(--color-info); background: var(--color-info-soft); border-color: var(--color-info); }
.vm-publication-withdrawn, .vm-revision-building { color: var(--color-warning); background: var(--color-warning-soft); border-color: var(--color-warning); }
.vm-revision-success { color: var(--color-success); background: var(--color-success-soft); border-color: var(--color-success); }
.vm-revision-failed { color: var(--color-danger); background: var(--color-danger-soft); border-color: var(--color-danger); }
.vm-help { margin: 12px 0; font-size: 12px; line-height: 1.75; color: var(--color-secondary); }
.vm-help code { white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--font-mono); }
.vm-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px 24px; margin: 0; }
.vm-facts > div { min-width: 0; }
.vm-facts dt, .vm-applicability dt { color: var(--color-muted); font-size: 12px; }
.vm-facts dd { margin: 4px 0 0; font-size: 16px; }
.vm-pre-wrap { white-space: pre-wrap; }
.vm-subsection { margin-top: 24px; }
.vm-applicability { margin: 0; }
.vm-applicability > div { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 16px; padding: 12px 0; border-bottom: 1px solid var(--color-line); }
.vm-applicability dd { margin: 0; font-size: 14px; }
.vm-hash-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; justify-content: space-between; margin: 24px 0 8px; }
.vm-hash-heading h4 { margin: 0; }
.vm-hash :deep(textarea), .vm-code-input :deep(textarea) { font-family: var(--font-mono); font-size: 14px; line-height: 1.75; overflow-wrap: anywhere; }
.vm-details { margin: 16px 0; border-block: 1px solid var(--color-line); }
.vm-details > summary, .vm-row-details > summary { cursor: pointer; color: var(--color-accent); padding: 12px 0; font-size: 14px; }
.vm-details > summary:focus-visible, .vm-row-details > summary:focus-visible, .vm-text-button:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }
.vm-details ul { padding-left: 24px; font-size: 14px; }
.vm-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 16px; }
.vm-actions .el-button { margin-left: 0; }
.vm-reparse { display: flex; align-items: flex-start; flex-wrap: wrap; justify-content: space-between; gap: 16px; margin-top: 24px; padding-top: 24px; border-top: 1px solid var(--color-line); }
.vm-reparse > div { flex: 1 1 320px; min-width: 0; }
.vm-reparse p { font-size: 14px; margin: 0; color: var(--color-secondary); }
.vm-text-button { display: inline-flex; align-items: center; border: 0; padding: 4px 0; min-height: 36px; background: transparent; color: var(--color-accent); font-size: 14px; text-align: left; }
.vm-text-button:hover { color: var(--color-accent-hover); text-decoration: underline; text-underline-offset: 4px; }
.vm-text-button:disabled { color: var(--color-muted); cursor: not-allowed; }
.vm-tab-intro { margin: 24px 0; }
.vm-tab-intro p { font-size: 14px; margin: 0; color: var(--color-secondary); }
.vm-list-heading { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 16px; justify-content: space-between; }
.vm-list-heading > div { flex: 1 1 360px; min-width: 0; }
.vm-form { margin-top: 24px; }
.vm-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 24px; }
.vm-body :deep(.el-form-item__content) { min-width: 0; }
.vm-body :deep(.el-select), .vm-body :deep(.el-date-editor.el-input) { width: 100%; min-width: 0; }
.vm-body :deep(.el-checkbox) { max-width: 100%; height: auto; min-height: 36px; margin-right: 0; white-space: normal; align-items: flex-start; }
.vm-body :deep(.el-checkbox__input) { margin-top: 5px; }
.vm-body :deep(.el-checkbox__label) { min-width: 0; white-space: normal; line-height: 1.65; overflow-wrap: anywhere; }
.vm-body :deep(.el-form-item__error) { position: static; font-size: 12px; padding-top: 6px; line-height: 1.55; }
.vm-validation-note { margin: 12px 0; font-size: 14px; }
.vm-table-scroll { max-width: 100%; width: 100%; min-width: 0; overflow-x: auto; overscroll-behavior-x: contain; }
.vm-table-scroll:focus-visible { outline: 2px solid var(--color-focus); outline-offset: -2px; }
.vm-wide-table { min-width: 1120px; width: 100%; }
.vm-revisions-table { min-width: 1250px; }
.vm-compare-table { width: 100%; min-width: 850px; border-collapse: collapse; table-layout: fixed; font-size: 14px; line-height: 1.55; }
.vm-compare-table :is(th, td) { border-bottom: 1px solid var(--color-line); padding: 16px; text-align: left; vertical-align: top; }
.vm-compare-table th { background: var(--color-page); font-weight: 600; }
.vm-compare-table th:first-child { width: 128px; }
.vm-compare-table pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 14px/1.65 var(--font-mono); }
.vm-cell-title, .vm-cell-note { display: block; overflow-wrap: anywhere; }
.vm-cell-title { font-size: 14px; margin: 4px 0; }
.vm-cell-note, .vm-record-key { color: var(--color-secondary); font-size: 12px; line-height: 1.65; }
.vm-record-key { font-family: var(--font-mono); }
.vm-row-flags { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.vm-row-message { margin: 0; font-size: 14px; }
.vm-row-details dl { margin: 0; font-size: 12px; }
.vm-row-details dt { color: var(--color-muted); }
.vm-row-details dd { margin: 4px 0 12px; }
.vm-hash-text { font-family: var(--font-mono); overflow-wrap: anywhere; user-select: text; }
.vm-pagination { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 16px; padding: 16px 0; font-size: 12px; color: var(--color-secondary); }
.vm-pagination :deep(.el-pagination), .vm-pagination :deep(.el-pager) { display: flex; flex-wrap: wrap; max-width: 100%; min-width: 0; gap: 4px; }
.vm-upload-zone { display: grid; justify-items: center; gap: 12px; padding: 16px 0; }
.vm-upload-zone .app-icon { color: var(--color-accent); }
.vm-upload-zone b { font-size: 16px; }
.vm-upload-zone span { color: var(--color-secondary); font-size: 12px; line-height: 1.65; }
.vm-upload-progress { margin: 16px 0; font-size: 14px; color: var(--color-secondary); }
.vm-footer { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 16px; font-size: 12px; color: var(--color-secondary); }
.vm-footer > span { flex: 1 1 260px; }
@media (max-width: 767px) {
  .vm-status-grid, .vm-form-grid, .vm-facts { grid-template-columns: minmax(0, 1fr); gap: 16px; }
  .vm-status-grid > div { border-left: 0; padding: 0 0 16px; border-bottom: 1px solid var(--color-line); }
  .vm-applicability > div { grid-template-columns: minmax(0, 1fr); gap: 4px; }
  .vm-actions .el-button, .vm-footer .el-button, .vm-toolbar .el-button, .vm-reparse > .el-button { min-height: 44px; }
}
@media (max-width: 479px) {
  .vm-header { padding-right: 16px; }
  .vm-header h2 { font-size: 22px; }
  .vm-record-heading h3 { font-size: 22px; }
  .vm-banner { padding: 12px; }
  .vm-actions .el-button { flex: 1 1 auto; max-width: 100%; height: auto; white-space: normal; line-height: 1.55; }
  .vm-actions .el-button :deep(span) { white-space: normal; overflow-wrap: anywhere; }
  .vm-footer .el-button { width: 100%; }
}
</style>
