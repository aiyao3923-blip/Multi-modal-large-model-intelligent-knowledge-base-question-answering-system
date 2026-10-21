import axios from 'axios'
import { ElMessage } from 'element-plus'
import { useUserStore } from '@/stores/user'
import router from '@/router'
const http = axios.create({ baseURL: '/api', timeout: 30000 })
http.interceptors.request.use((config) => { const token = useUserStore().token; config.sessionToken = token; if (token) config.headers.Authorization = 'Bearer ' + token; return config })
http.interceptors.response.use((response) => response.data, (error) => {
  if (axios.isCancel(error)) return Promise.reject(error)
  const status = error?.response?.status, detail = error?.response?.data?.detail
  if (status === 401) { const user = useUserStore(); if (user.token === (error.config?.sessionToken || '')) { user.logout(); if (router.currentRoute.value.path !== '/login') router.replace({ path: '/login', query: { redirect: router.currentRoute.value.fullPath } }) } }
  else if (!error.config?.silent) {
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? '请检查输入内容：' + (detail[0]?.msg || '格式不正确') : error.code === 'ECONNABORTED' ? '请求超时，请检查任务状态后再试' : !error.response ? '无法连接服务，请确认后端已启动' : '操作失败，请稍后重试'
    ElMessage.error({ message, grouping: true })
  }
  return Promise.reject(error)
})
export default http
