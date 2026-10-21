<template>
<div class="page admin-page user-workbench">
  <PageHeading title="用户与权限">
    <el-button :loading="loading" @click="load"><AppIcon name="refresh" :size="16" />刷新</el-button><el-button type="primary" @click="openCreate"><AppIcon name="plus" :size="16" />新增用户</el-button>
  </PageHeading>
  <div v-if="error" class="error-strip" role="alert"><AppIcon name="warning" :size="16" /><span>{{ error }}</span><button type="button" class="text-button" @click="load">重新加载</button></div>
  <section class="panel admin-section" aria-label="用户账户台账">
    <div class="toolbar admin-toolbar"><div class="toolbar-group">
      <div class="admin-filter-field admin-filter-search"><label for="user-search">账号、显示名或邮箱</label><el-input id="user-search" v-model="search" placeholder="搜索用户" clearable><template #prefix><AppIcon name="search" :size="16" /></template></el-input></div>
      <div class="admin-filter-field"><label for="user-role">访问角色</label><el-select id="user-role" v-model="roleFilter"><el-option label="全部角色" value="" /><el-option v-for="(name, code) in roleNames" :key="code" :label="name" :value="code" /></el-select></div>
      <div class="admin-filter-field"><label for="user-status">账号状态</label><el-select id="user-status" v-model="status"><el-option label="全部状态" value="" /><el-option label="已启用" value="active" /><el-option label="已停用" value="inactive" /></el-select></div>
    </div></div>
    <p id="user-table-note" class="sr-only">窄屏可在用户表内横向滚动。</p>
    <div class="admin-table-scroll" role="region" tabindex="0" aria-label="用户账户表" aria-describedby="user-table-note" style="--admin-table-min-width: 1120px">
      <el-table :data="paged" row-key="id" v-loading="loading">
        <el-table-column label="账户 / 显示名" min-width="265"><template #default="{ row }"><div class="admin-statusline"><span class="admin-record-title">{{ row.display_name || row.username }}</span><span v-if="row.id === current.user?.id" class="badge">当前账号</span></div><div class="admin-record-meta">@{{ row.username }} · USER / {{ row.id }}</div></template></el-table-column>
        <el-table-column label="电子邮箱" min-width="210"><template #default="{ row }"><span class="user-email">{{ row.email || '未设置' }}</span></template></el-table-column>
        <el-table-column label="角色" width="160"><template #default="{ row }"><span class="badge">{{ roleNames[row.role_code] || row.role_code }}</span></template></el-table-column>
        <el-table-column label="状态" width="110"><template #default="{ row }"><StatusBadge :status="row.is_active ? 'enabled' : 'disabled'" /></template></el-table-column>
        <el-table-column label="创建时间" width="150"><template #default="{ row }">{{ formatDate(row.created_at) }}</template></el-table-column>
        <el-table-column label="账户操作" width="225"><template #default="{ row }"><div class="admin-action-group"><el-button text type="primary" @click="edit(row)">编辑</el-button><el-button text :disabled="row.id === current.user?.id || saving" @click="toggle(row)">{{ row.is_active ? '停用' : '启用' }}</el-button></div><div v-if="row.id === current.user?.id" class="admin-record-meta">为避免自锁，不可停用本账号</div></template></el-table-column>
        <template #empty><p v-if="loading" class="secondary" role="status">正在读取用户账户…</p><EmptyState v-else-if="error" icon="warning" title="用户台账未能加载" description="请检查连接后重新加载。" /><EmptyState v-else icon="users" title="没有匹配的用户" description="请调整关键词、角色或账户状态筛选。" /></template>
      </el-table>
    </div>
    <div class="table-footer admin-pagination"><span>{{ loaded ? '共 ' + filtered.length + ' 位用户 · 第 ' + page + ' 页' : '等待账户数据' }}</span><el-pagination background v-model:current-page="page" layout="prev, pager, next" :total="filtered.length" :page-size="20" :pager-count="5" /></div>
  </section>

  <el-dialog v-model="dialog" class="admin-dialog" :title="editId ? '编辑用户信息' : '新增工作台用户'" width="min(580px, calc(100vw - 32px))" @closed="form.password = ''" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <div v-if="saveError" class="error-strip" role="alert">{{ saveError }}</div>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="save">
      <div class="admin-form-grid"><el-form-item label="用户名" prop="username"><el-input v-model="form.username" :disabled="!!editId" maxlength="64" placeholder="登录时使用" autocomplete="off" /><p v-if="editId" class="input-help">已创建账户的用户名不可修改。</p></el-form-item><el-form-item label="显示名称" prop="display_name"><el-input v-model="form.display_name" maxlength="64" placeholder="在工作台中显示" /></el-form-item></div>
      <el-form-item label="电子邮箱" prop="email"><el-input v-model="form.email" maxlength="128" placeholder="可选，便于联系" /></el-form-item>
      <el-form-item label="访问角色" prop="role_code"><el-select v-model="form.role_code" :disabled="editId === current.user?.id"><el-option v-for="(name, code) in roleNames" :key="code" :label="name" :value="code" /></el-select><p class="input-help">{{ selectedRoleDescription }}{{ editId === current.user?.id ? '。当前账号不能调整自身角色，以避免自锁。' : '。知识库访问按拥有者与分享授权校验。' }}</p></el-form-item>
      <el-form-item :label="editId ? '重置密码（可选）' : '初始密码'" prop="password"><el-input v-model="form.password" type="password" show-password :placeholder="editId ? '留空保留原密码' : '请设置 8–64 位密码'" maxlength="64" autocomplete="new-password" /><p class="input-help">{{ editId ? '填写后将替换当前密码，保存时会再次确认。请先告知账号使用者。' : '不会自动生成通用密码，请通过安全方式交付给账号使用者。' }}</p></el-form-item>
    </el-form>
    <template #footer><div class="dialog-actions"><el-button :disabled="saving" @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">{{ editId ? '保存修改' : '创建用户' }}</el-button></div></template>
  </el-dialog>
</div>
</template>

<script setup>
import './common.css'
import { ElButton, ElInput, ElSelect, ElOption, ElTable, ElTableColumn, ElPagination, ElDialog, ElForm, ElFormItem } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/input/style/css.mjs'
import 'element-plus/es/components/select/style/css.mjs'
import 'element-plus/es/components/option/style/css.mjs'
import 'element-plus/es/components/table/style/css.mjs'
import 'element-plus/es/components/table-column/style/css.mjs'
import 'element-plus/es/components/pagination/style/css.mjs'
import 'element-plus/es/components/dialog/style/css.mjs'
import 'element-plus/es/components/form/style/css.mjs'
import 'element-plus/es/components/form-item/style/css.mjs'

import { ref, computed, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { usersApi,createUserApi,patchUserApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { roleNames as baseRoleNames,formatDate } from '@/utils/format'
import { useLoad } from '@/composables/useLoad'
const roleNames = { ...baseRoleNames, operator: '知识库维护员', user: '普通用户' }
const current=useUserStore(),users=ref([]),search=ref(''),roleFilter=ref(''),status=ref(''),page=ref(1),dialog=ref(false),editId=ref(null),formRef=ref(),saving=ref(false),form=ref({})
const loaded=ref(false), saveError=ref('')
const roleCards=[{code:'admin',icon:'shield',description:'管理系统配置和账号；私库仍需显式授权'},{code:'operator',icon:'book',description:'维护自己的知识库，查看获授权资料与反馈'},{code:'user',icon:'chat',description:'创建和管理自己的知识库，使用获授权资料问答'}]
const {loading,error,load}=useLoad(async()=>{users.value=await usersApi();loaded.value=true})
const filtered=computed(()=>users.value.filter(u=>(!roleFilter.value||u.role_code===roleFilter.value)&&(!status.value||(status.value==='active')===u.is_active)&&[u.username,u.display_name,u.email].join(' ').toLowerCase().includes(search.value.trim().toLowerCase())))
const paged=computed(()=>filtered.value.slice((page.value-1)*20,page.value*20));watch([search,roleFilter,status],()=>{page.value=1})
const rules={username:[{validator:(_r,v,cb)=>/^[\w.-]{2,64}$/.test(v||'')?cb():cb(new Error('用户名为 2–64 位字母、数字、下划线、点或短横线')),trigger:'blur'}],password:[{validator:(_r,v,cb)=>editId.value&&!v||v?.length>=8&&v.length<=64?cb():cb(new Error('请设置 8–64 位密码')),trigger:'blur'}],email:[{type:'email',message:'请输入有效的邮箱地址',trigger:'blur'}]}
function openCreate(){saveError.value='';editId.value=null;form.value={username:'',display_name:'',email:'',role_code:'user',password:''};dialog.value=true}
function edit(u){saveError.value='';editId.value=u.id;form.value={username:u.username,display_name:u.display_name||'',email:u.email||'',role_code:u.role_code,password:''};dialog.value=true}
async function save(){if(saving.value||!await formRef.value.validate().catch(()=>false))return;saveError.value='';try{if(editId.value&&form.value.password)await ElMessageBox.confirm('将替换「'+form.value.username+'」的登录密码。请确认账号使用者已知悉此次重置。','确认重置密码？',{confirmButtonText:'确认替换',cancelButtonText:'取消',type:'warning'});saving.value=true;if(editId.value)await patchUserApi(editId.value,{display_name:form.value.display_name.trim(),email:form.value.email.trim(),role_code:form.value.role_code,password:form.value.password||undefined});else await createUserApi({...form.value,username:form.value.username.trim(),display_name:form.value.display_name.trim(),email:form.value.email.trim()});dialog.value=false;form.value.password='';ElMessage.success('用户信息已保存');await load();if(editId.value===current.user?.id){const updated=users.value.find(u=>u.id===editId.value);if(updated)current.setLogin(current.token,updated)}}catch(e){if(e!=='cancel'&&e!=='close')saveError.value=typeof e?.response?.data?.detail==='string'?e.response.data.detail:'用户信息未能保存，请检查输入或连接后重试。'}finally{saving.value=false}}
async function toggle(u){try{await ElMessageBox.confirm(u.is_active?'停用后该账号不能登录或继续调用接口，原有记录保留。':'启用后该账号可以恢复登录。',u.is_active?'停用「'+u.username+'」？':'启用「'+u.username+'」？',{confirmButtonText:'确认',cancelButtonText:'取消',type:'warning'});saving.value=true;await patchUserApi(u.id,{is_active:!u.is_active});ElMessage.success('账号状态已更新');await load()}catch{}finally{saving.value=false}}
onMounted(load)

const selectedRoleDescription=computed(()=>roleCards.find(role=>role.code===form.value.role_code)?.description||'')
watch(() => filtered.value.length, count => { page.value = Math.min(page.value, Math.max(1, Math.ceil(count / 20))) })
</script>

<style scoped>
.user-email { color: var(--color-secondary); overflow-wrap: anywhere; }
</style>

