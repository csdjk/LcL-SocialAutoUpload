import { createRouter, createWebHashHistory } from 'vue-router'
import DailyPublish from '../views/DailyPublish.vue'
import AccountManagement from '../views/AccountManagement.vue'
import TaskCenter from '../views/TaskCenter.vue'
import ContentHub from '../views/ContentHub.vue'
import SettingsHub from '../views/SettingsHub.vue'

const routes = [
  {
    path: '/',
    name: 'DailyPublish',
    component: DailyPublish
  },
  {
    path: '/dashboard',
    redirect: '/'
  },
  {
    path: '/account-management',
    name: 'AccountManagement',
    component: AccountManagement
  },
  {
    path: '/material-management',
    redirect: { path: '/content', query: { tab: 'materials' } }
  },
  {
    path: '/publish-center',
    name: 'PublishCenter',
    component: TaskCenter
  },
  {
    path: '/video-library',
    redirect: '/content'
  },
  {
    path: '/automation',
    redirect: '/settings'
  },
  {
    path: '/about',
    redirect: { path: '/settings', query: { tab: 'about' } }
  },
  { path: '/content', name: 'ContentHub', component: ContentHub },
  { path: '/settings', name: 'SettingsHub', component: SettingsHub }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router
