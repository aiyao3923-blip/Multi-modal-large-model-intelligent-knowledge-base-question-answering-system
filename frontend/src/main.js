import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { ElLoading } from 'element-plus'
import 'element-plus/es/components/loading/style/css.mjs'
import 'element-plus/es/components/message/style/css.mjs'
import 'element-plus/es/components/message-box/style/css.mjs'
import App from './App.vue'
import router from './router'
import AppIcon from './components/AppIcon.vue'
import PageHeading from './components/PageHeading.vue'
import EmptyState from './components/EmptyState.vue'
import StatusBadge from './components/StatusBadge.vue'
import './styles.css'
const app = createApp(App)
app.use(createPinia())
app.use(router)
app.directive('loading', ElLoading.directive)
app.component('AppIcon', AppIcon).component('PageHeading', PageHeading).component('EmptyState', EmptyState).component('StatusBadge', StatusBadge)
app.mount('#app')
