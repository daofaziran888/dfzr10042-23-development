import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import SupplierReconView from '@/views/SupplierReconView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径必须早于 /:moduleKey，否则会被动态模块路由吃掉。
    { path: '/supplier_recon', name: 'supplier_recon', component: SupplierReconView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
