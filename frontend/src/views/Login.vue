<template>
  <main id="main-content" class="login-page" tabindex="-1">
    <section class="login-card" aria-labelledby="login-form-title">
      <div class="login-brand"><span class="brand-mark" aria-hidden="true"><AppIcon name="book" :size="20" /></span><strong>论文知识库</strong></div>
      <h1 id="login-form-title">登录</h1>
      <el-form id="login-form" ref="loginForm" method="post" action="/api/auth/login" autocomplete="on" :model="form" :rules="rules" label-position="top" :aria-busy="loading" @submit.prevent="doLogin">
        <el-form-item label="用户名" prop="username" for="login-username">
          <el-input id="login-username" v-model="form.username" name="username" @input="markEdited" autocomplete="username" placeholder="请输入用户名" size="large" maxlength="64" :disabled="loading" :aria-describedby="error ? 'login-error' : undefined" />
        </el-form-item>
        <el-form-item label="密码" prop="password" for="login-password">
          <el-input id="login-password" v-model="form.password" name="password" @input="markEdited" :type="showPassword ? 'text' : 'password'" autocomplete="current-password" placeholder="请输入密码" size="large" :disabled="loading" :aria-describedby="error ? 'login-error' : undefined">
            <template #suffix>
              <button type="button" class="password-toggle" :aria-label="showPassword ? '隐藏密码' : '显示密码'" :aria-pressed="showPassword" :disabled="loading" @click="showPassword = !showPassword">{{ showPassword ? '隐藏' : '显示' }}</button>
            </template>
          </el-input>
        </el-form-item>
        <div v-if="error" id="login-error" class="login-error" role="alert"><AppIcon name="warning" :size="18" /><span>{{ error }}</span></div>
        <el-button native-type="submit" type="primary" size="large" :loading="loading" :disabled="loading" class="login-submit">{{ loading ? '正在登录' : '进入工作区' }}</el-button>
      </el-form>
      <p class="login-help" aria-live="polite">{{ autoFilled ? '已填入保存的账号密码，可直接进入。' : '浏览器可记住密码，下次自动填充。' }}</p>
    </section>
  </main>
</template>

<script setup>
import { ElForm, ElFormItem, ElInput, ElButton } from 'element-plus'
import 'element-plus/es/components/form/style/css.mjs'
import 'element-plus/es/components/form-item/style/css.mjs'
import 'element-plus/es/components/input/style/css.mjs'
import 'element-plus/es/components/button/style/css.mjs'
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { loginApi } from '@/api'
import { useUserStore } from '@/stores/user'
import { readSavedLogin, saveLogin, readLoginInputs } from '@/utils/loginCredentials'

const router = useRouter(), route = useRoute(), user = useUserStore()
const loginForm = ref()
const form = reactive({ username: 'admin', password: '' })
const loading = ref(false), showPassword = ref(false), error = ref('')
const inputsEdited = ref(false), autoFilled = ref(false)
let disposed = false
const rules = {
  username: [{ required: true, whitespace: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}
function markEdited() {
  inputsEdited.value = true
  autoFilled.value = false
}
async function prefillSavedLogin() {
  const intendedUsername = form.username
  const saved = await readSavedLogin()
  if (!saved || saved.username !== intendedUsername || disposed || loading.value || inputsEdited.value) return
  if (form.username !== intendedUsername || form.password) return
  const visible = readLoginInputs(loginForm.value?.$el, form)
  if (visible.username !== intendedUsername || visible.password) return
  form.username = saved.username
  form.password = saved.password
  autoFilled.value = true
}
onMounted(prefillSavedLogin)
onBeforeUnmount(() => {
  disposed = true
  form.password = ''
})
async function doLogin() {
  if (loading.value || !loginForm.value) return
  // Some native password managers fill DOM controls without emitting input events.
  Object.assign(form, readLoginInputs(loginForm.value.$el, form))
  loading.value = true
  error.value = ''
  try {
    if (!await loginForm.value.validate().catch(() => false)) return
    const credentials = { username: form.username.trim(), password: form.password }
    const data = await loginApi(credentials)
    user.setLogin(data.token, data.user)
    // A browser save prompt must never hold up a successful login.
    void saveLogin(credentials)
    form.password = ''
    const target = route.query.redirect
    const localTarget = typeof target === 'string' && target.startsWith('/') && !target.startsWith('//')
      && !target.includes('\\') && !/[\u0000-\u001f]/.test(target) && !/^\/login(?:[/?#]|$)/i.test(target)
    await router.replace(localTarget ? target : '/')
  } catch (e) {
    error.value = typeof e?.response?.data?.detail === 'string'
      ? e.response.data.detail
      : '暂时无法登录，请检查连接后重试。'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page { min-height: 100svh; display: grid; place-items: center; padding: 24px 16px; background: var(--color-page); }
.login-page:focus-visible { outline: 2px solid var(--color-focus); outline-offset: -2px; }
.login-card { width: 100%; max-width: 400px; padding: 32px; border: 1px solid var(--color-line); border-radius: 10px; background: var(--color-surface); box-shadow: 0 4px 20px rgba(23, 32, 51, .04); }
.login-brand { display: flex; align-items: center; gap: 8px; margin-bottom: 24px; }
.brand-mark { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 8px; color: var(--color-accent); background: var(--color-accent-soft); }
.login-brand strong { font-size: 15px; font-weight: 600; }
.login-card h1 { margin: 0 0 24px; font-family: var(--font-body); font-size: 22px; line-height: 1.4; font-weight: 600; }
.login-card :deep(.el-input__wrapper) { min-height: 44px; }
.password-toggle { min-height: 36px; padding: 4px 8px; border: 0; border-radius: 6px; background: transparent; color: var(--color-secondary); font-size: 13px; }
.password-toggle:hover { color: var(--color-accent); }
.login-submit { width: 100%; margin-top: 4px; }
.login-error { display: flex; align-items: flex-start; gap: 8px; padding: 12px; margin-bottom: 16px; border: 1px solid var(--color-danger); border-radius: 6px; background: var(--color-danger-soft); color: var(--color-danger); font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.login-error .app-icon { margin-top: 2px; }
.login-help { margin: 16px 0 0; color: var(--color-secondary); font-size: 13px; text-align: center; }
@media (max-width: 479px) { .login-card { padding: 24px; } }
</style>

