<template>
<div class="page admin-page channel-workbench">
  <PageHeading title="模型通道">
    <el-button :loading="loading" @click="load"><AppIcon name="refresh" :size="16" />刷新配置</el-button><el-button type="primary" :disabled="saving || testing !== null" @click="openCreate"><AppIcon name="plus" :size="16" />新增模型通道</el-button>
  </PageHeading>
  <div v-if="error" class="error-strip" role="alert"><AppIcon name="warning" :size="16" /><span>{{ error }}</span><button type="button" class="text-button" @click="load">重新加载</button></div>
  <div v-if="testing !== null" class="channel-testing-note" role="status">正在检测选定通道，请等待结果；其他检测和配置操作暂时禁用。</div>

  <section class="admin-section" aria-labelledby="generation-channels-title">
    <div class="admin-section-heading"><div><h2 id="generation-channels-title">生成通道</h2><p>{{ loaded ? generationChannels.length + ' 个通道' : '正在读取通道配置…' }}</p></div></div>
    <div v-if="loading && !loaded" class="admin-empty-loading" role="status"><p>正在读取模型通道…</p></div>
    <EmptyState v-else-if="error && !loaded" icon="warning" title="通道清单未能加载" description="请检查连接后重试。" />
    <div v-else-if="generationChannels.length" class="channel-register" v-loading="loading">
      <article v-for="channel in generationChannels" :key="channel.id" class="channel-record">
        <div class="channel-record-id"><span class="admin-record-key">CHANNEL / {{ String(channel.id).padStart(3, '0') }}</span><span class="badge">{{ typeNames[channel.channel_type] || channel.channel_type }}</span></div>
        <div class="channel-record-content"><div class="channel-record-heading"><h3>{{ channel.name }}</h3><StatusBadge :status="channel.enabled ? 'enabled' : 'disabled'" /></div>
          <dl class="channel-facts"><div><dt>模型标识</dt><dd class="channel-code">{{ channel.model_name || '未设置' }}</dd></div><div><dt>访问密钥</dt><dd><AppIcon name="lock" :size="14" /> {{ channel.has_key ? '已配置 · 密钥不回显' : '未配置密钥' }}</dd></div><div class="channel-endpoint"><dt>目标地址</dt><dd class="channel-code">{{ channel.base_url || '未设置' }}</dd></div><div><dt>响应超时</dt><dd><span class="admin-number">{{ channel.timeout_s }}</span> 秒</dd></div><div><dt>生成温度</dt><dd class="admin-number">{{ channel.temperature }}</dd></div></dl>
          <div class="channel-record-footer"><span class="channel-health" :class="'health-' + (channel.health_status || 'unknown')"><AppIcon :name="channel.health_status === 'ok' ? 'checkCircle' : channel.health_status === 'error' ? 'warning' : 'clock'" :size="16" />{{ healthNames[channel.health_status] || '未检测' }}</span></div>
        </div>
        <div class="channel-actions"><el-button :disabled="saving || testing !== null" @click="edit(channel)">编辑配置</el-button><el-button :loading="testing === channel.id" :disabled="saving || (testing !== null && testing !== channel.id)" @click="test(channel)" title="检测会向此端点发送请求，在线服务可能计费">检测连通性</el-button><div class="channel-enable-control" title="启用后问题和知识片段可能发往此端点，在线服务可能计费"><span>{{ channel.enabled ? '通道已启用' : '通道已停用' }}</span><el-switch :model-value="channel.enabled" :disabled="saving || testing !== null" :aria-label="'启用通道：' + channel.name" @change="value => toggle(channel, value)" /></div></div>
      </article>
    </div>
    <EmptyState v-else-if="loaded" icon="connection" title="尚未配置生成通道" description="添加本地模型服务或在线兼容 API。"><el-button :disabled="saving || testing !== null" @click="openCreate">新增模型通道</el-button></EmptyState>
  </section>

  <details v-if="loaded && fallbackChannels.length" class="admin-disclosure fallback-register">
    <summary>内置检索 · {{ fallbackChannels.length }} 项</summary>
    <article v-for="channel in fallbackChannels" :key="channel.id" class="fallback-record">
      <div><h3>{{ channel.name }}</h3><p>返回检索片段与来源，不调用生成模型。</p></div>
      <StatusBadge :status="channel.enabled ? 'enabled' : 'disabled'" />
    </article>
  </details>

  <el-dialog v-model="dialog" class="admin-dialog" :title="editId ? '编辑模型通道' : '新增模型通道'" width="min(600px, calc(100vw - 32px))" @closed="form.api_key = ''" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <p class="channel-form-note">{{ editId ? '修改已启用通道会影响后续问答。保存前将再次确认通道名称与目标地址。' : '新通道默认关闭。保存配置不等于检测通过，也不会自动启用。' }}</p>
    <div v-if="saveError" class="error-strip" role="alert">{{ saveError }}</div>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="save">
      <div class="admin-form-grid"><el-form-item label="通道名称" prop="name"><el-input v-model="form.name" maxlength="128" placeholder="便于识别的服务名称" /></el-form-item><el-form-item label="通道类型" prop="channel_type"><el-select v-model="form.channel_type" :disabled="!!editId"><el-option label="本地模型服务" value="local" /><el-option label="在线兼容 API" value="online" /></el-select></el-form-item></div>
      <el-form-item label="模型名称" prop="model_name"><el-input v-model="form.model_name" maxlength="128" placeholder="填写服务实际提供的模型标识" /></el-form-item>
      <el-form-item label="服务地址（Base URL）" prop="base_url"><el-input v-model="form.base_url" maxlength="512" placeholder="http://127.0.0.1:11434 或兼容 API 基础地址" /><p class="input-help">仅向此处明确配置的目标发送请求。地址不能包含账号密码、查询参数或片段，勿把密钥写入 URL。</p></el-form-item>
      <el-form-item label="API Key（可选）"><el-input v-model="form.api_key" type="password" show-password :placeholder="editId ? '留空保留已有密钥' : '仅填写当前服务的访问密钥'" autocomplete="new-password" /><p class="input-help">服务端不会回传完整密钥；编辑时留空不会清除原值。</p></el-form-item>
      <div class="admin-form-grid"><el-form-item label="生成温度"><el-input-number v-model="form.temperature" :min="0" :max="1" :step="0.1" :precision="1" /></el-form-item><el-form-item label="响应超时（秒）"><el-input-number v-model="form.timeout_s" :min="5" :max="60" /></el-form-item></div>
    </el-form>
    <template #footer><div class="dialog-actions"><el-button :disabled="saving" @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存通道</el-button></div></template>
  </el-dialog>
</div>
</template>

<script setup>
import './common.css'
import { ElButton, ElSwitch, ElDialog, ElForm, ElFormItem, ElInput, ElSelect, ElOption, ElInputNumber } from 'element-plus'
import 'element-plus/es/components/button/style/css.mjs'
import 'element-plus/es/components/switch/style/css.mjs'
import 'element-plus/es/components/dialog/style/css.mjs'
import 'element-plus/es/components/form/style/css.mjs'
import 'element-plus/es/components/form-item/style/css.mjs'
import 'element-plus/es/components/input/style/css.mjs'
import 'element-plus/es/components/select/style/css.mjs'
import 'element-plus/es/components/option/style/css.mjs'
import 'element-plus/es/components/input-number/style/css.mjs'

import { ref,computed,onMounted } from 'vue'
import { ElMessage,ElMessageBox } from 'element-plus'
import { channelsApi,createChannelApi,patchChannelApi,channelHealthApi } from '@/api'
import { useLoad } from '@/composables/useLoad'
const channels=ref([]),dialog=ref(false),editId=ref(null),formRef=ref(),form=ref({}),saving=ref(false),testing=ref(null)
const loaded=ref(false), saveError=ref('')
const generationChannels=computed(()=>channels.value.filter(channel=>channel.channel_type!=='degraded'))
const fallbackChannels=computed(()=>channels.value.filter(channel=>channel.channel_type==='degraded'))
const typeNames={local:'本地模型',online:'在线模型',degraded:'检索式 · 内置'},healthNames={ok:'最近检测正常',error:'最近检测失败',unknown:'尚未测试'}
const {loading,error,load}=useLoad(async()=>{channels.value=await channelsApi();loaded.value=true})
const validUrl=(value)=>{try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!url.username&&!url.password&&!url.search&&!url.hash}catch{return false}}
const rules={name:[{required:true,message:'请输入通道名称',trigger:'blur'}],model_name:[{required:true,message:'请输入实际模型名称',trigger:'blur'}],base_url:[{validator:(_r,v,cb)=>validUrl(v)?cb():cb(new Error('请输入不含账号密码、查询参数的 HTTP(S) 基础地址')),trigger:'blur'}]}
function openCreate(){saveError.value='';editId.value=null;form.value={name:'',channel_type:'local',base_url:'',model_name:'',api_key:'',temperature:.3,timeout_s:30,enabled:false};dialog.value=true}
function edit(c){saveError.value='';editId.value=c.id;form.value={name:c.name,channel_type:c.channel_type,base_url:c.base_url,model_name:c.model_name,api_key:'',temperature:c.temperature,timeout_s:c.timeout_s};dialog.value=true}
async function save(){if(saving.value||!await formRef.value.validate().catch(()=>false))return;saveError.value='';try{await ElMessageBox.confirm('将保存通道「'+form.value.name+'」，目标地址为 '+form.value.base_url+'。该配置仅影响本应用；修改已有启用通道将影响后续问答。','确认通道配置',{confirmButtonText:'确认保存',cancelButtonText:'返回检查',type:'warning'});saving.value=true;const data={...form.value,name:form.value.name.trim(),model_name:form.value.model_name.trim(),base_url:form.value.base_url.trim().replace(/\/$/,'')};if(editId.value)await patchChannelApi(editId.value,{...data,api_key:data.api_key||undefined});else await createChannelApi(data);form.value.api_key='';dialog.value=false;ElMessage.success('通道配置已保存');await load()}catch(e){if(e!=='cancel'&&e!=='close')saveError.value='配置未能保存，请核对字段和服务连接；输入仍保留，可修正后重试。'}finally{saving.value=false}}
async function toggle(c,value){try{await ElMessageBox.confirm(value?'启用后，问题和知识片段可能发送到 '+c.base_url+'；如为计费服务，可能产生费用。可随时停用此通道。':'停用后不再使用此通道；后续问答按剩余可用配置处理。',value?'确认启用「'+c.name+'」？':'停用此通道？',{confirmButtonText:value?'确认启用':'确认停用',cancelButtonText:'取消',type:'warning'});saving.value=true;await patchChannelApi(c.id,{enabled:value});ElMessage.success('通道状态已更新');await load()}catch{}finally{saving.value=false}}
async function test(c){if(testing.value)return;try{await ElMessageBox.confirm('将向 '+c.base_url+' 发送健康检查请求，只使用当前通道配置。在线服务可能计费。','测试通道连通性？',{confirmButtonText:'开始检测',cancelButtonText:'取消'});testing.value=c.id;const result=await channelHealthApi(c.id);const refreshed=await channelsApi();channels.value=refreshed;const state=refreshed.find(item=>item.id===c.id);if(state?.health_status==='ok')ElMessage.success(result.message||'连接正常');else ElMessage.warning(result.message||'连接失败，请检查服务配置')}catch{}finally{testing.value=null}}
onMounted(load)
</script>

<style scoped>
.channel-testing-note { margin: 16px 0; color: var(--color-info); font-size: 14px; }
.channel-register { display: grid; gap: 12px; }
.channel-record { display: grid; grid-template-columns: 104px minmax(0, 1fr) 144px; gap: 16px; padding: 20px; border: 1px solid var(--color-line); border-radius: 8px; background: var(--color-surface); }
.channel-record-id { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
.channel-record-content { min-width: 0; }
.channel-record-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-bottom: 16px; }
.channel-record-heading h3, .fallback-record h3 { margin: 0; font-size: 17px; line-height: 1.5; font-weight: 600; overflow-wrap: anywhere; }
.channel-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 16px; margin: 0; }
.channel-facts > div { min-width: 0; }
.channel-facts dt { font-size: 12px; color: var(--color-secondary); }
.channel-facts dd { margin: 4px 0 0; font-size: 14px; color: var(--color-ink); overflow-wrap: anywhere; }
.channel-code { font-family: var(--font-mono); }
.channel-endpoint { grid-column: 1 / -1; }
.channel-record-footer { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.channel-health { display: inline-flex; align-items: center; gap: 8px; color: var(--color-secondary); font-size: 12px; }
.channel-health.health-ok { color: var(--color-success); }
.channel-health.health-error { color: var(--color-danger); }
.channel-actions { display: flex; flex-direction: column; align-items: stretch; gap: 8px; border-left: 1px solid var(--color-line); padding-left: 16px; }
.channel-enable-control { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; font-size: 12px; color: var(--color-secondary); }
.fallback-register { border-top: 1px solid var(--color-line); }
.fallback-record { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 16px; border: 1px solid var(--color-line); border-radius: 8px; background: var(--color-surface); }
.fallback-record > div { min-width: 0; }
.fallback-record p { margin: 8px 0 0; font-size: 14px; color: var(--color-secondary); }
.channel-form-note { color: var(--color-secondary); font-size: 13px; }
@media (max-width: 1199px) { .channel-record { grid-template-columns: 96px minmax(0, 1fr); } .channel-actions { grid-column: 2; flex-direction: row; flex-wrap: wrap; align-items: center; border-left: 0; padding-left: 0; } .channel-enable-control { margin-top: 0; } }
@media (max-width: 767px) { .channel-record { grid-template-columns: minmax(0, 1fr); padding: 16px; } .channel-record-id { flex-direction: row; flex-wrap: wrap; align-items: center; } .channel-actions { grid-column: auto; } }
@media (max-width: 479px) { .channel-facts { grid-template-columns: minmax(0, 1fr); } .channel-endpoint { grid-column: auto; } .channel-actions .el-button { min-height: 44px; } .fallback-record { flex-wrap: wrap; } }
</style>

