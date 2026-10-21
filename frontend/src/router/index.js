import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/stores/user'

const routes = [
  { path: '/login', name: 'login', component: () => import('@/views/Login.vue'), meta: { public: true, title: '登录' } },
  { path: '/', component: () => import('@/views/chat/ChatLayout.vue'), children: [
    { path: '', name: 'chat', component: () => import('@/views/chat/ChatView.vue'), meta: { title: '论文问答' } },
  ] },
  // The legacy /admin prefix is a layout, not a role boundary. Library owners may use it.
  { path: '/admin', component: () => import('@/views/admin/AdminLayout.vue'), redirect: '/admin/kbs', children: [
    { path: 'kbs', name: 'admin-kbs', component: () => import('@/views/admin/KbList.vue'), meta: { title: '知识库' } },
    { path: 'kbs/:id', name: 'admin-kb-detail', component: () => import('@/views/admin/KbDetail.vue'), meta: { title: '知识库文档' } },
    { path: 'tasks', name: 'admin-tasks', component: () => import('@/views/admin/TaskCenter.vue'), meta: { title: '导入任务' } },
    { path: 'overview', name: 'admin-overview', component: () => import('@/views/admin/Overview.vue'), meta: { op: true, title: '概览' } },
    { path: 'feedbacks', name: 'admin-feedbacks', component: () => import('@/views/admin/FeedbackCenter.vue'), meta: { op: true, title: '问答反馈' } },
    { path: 'stats', name: 'admin-stats', component: () => import('@/views/admin/StatsView.vue'), meta: { op: true, title: '使用统计' } },
    { path: 'users', name: 'admin-users', component: () => import('@/views/admin/UserManage.vue'), meta: { admin: true, title: '用户与权限' } },
    { path: 'channels', name: 'admin-channels', component: () => import('@/views/admin/ChannelManage.vue'), meta: { admin: true, title: '模型通道' } },
    // Keep old bookmarks safe without importing or rendering the removed feature.
    { path: 'templates/:pathMatch(.*)*', redirect: '/admin/kbs' },
  ] },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({ history: createWebHistory(), routes })
router.beforeEach(async (to) => {
  const user = useUserStore()
  if (user.token && !user.checked) await user.validateSession()
  if (to.meta.public) return user.isLogin && to.path === '/login' ? '/' : true
  if (!user.isLogin) return { path: '/login', query: { redirect: to.fullPath } }
  if ((to.meta.admin && !user.isAdmin) || (to.meta.op && !user.isOperator)) return '/'
  return true
})
router.afterEach((to) => { document.title = (to.meta.title || '论文问答') + ' · 论文知识库' })
export default router
