import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
const Etljob = () => import('@/views/etljob/index.vue')
const Dag = () => import('@/views/dag/index.vue')
const Dependency = () => import('@/views/dependency/index.vue')
const Sourcecheck = () => import('@/views/sourcecheck/index.vue')
const Runtime = () => import('@/views/runtime/index.vue')
const Rerun = () => import('@/views/rerun/index.vue')
const Baseline = () => import('@/views/baseline/index.vue')
const Partitioncheck = () => import('@/views/partitioncheck/index.vue')
const Comparator = () => import('@/views/comparator/index.vue')
const Metastore = () => import('@/views/metastore/index.vue')
const Resource = () => import('@/views/resource/index.vue')
const Slavalidate = () => import('@/views/slavalidate/index.vue')
const Sqltpl = () => import('@/views/sqltpl/index.vue')
const Backuprestore = () => import('@/views/backuprestore/index.vue')
const Logarchive = () => import('@/views/logarchive/index.vue')
const Notifychan = () => import('@/views/notifychan/index.vue')
const Notifyevent = () => import('@/views/notifyevent/index.vue')
const Publishplan = () => import('@/views/publishplan/index.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/etljob', name: 'etljob', component: Etljob },
    { path: '/dag', name: 'dag', component: Dag },
    { path: '/dependency', name: 'dependency', component: Dependency },
    { path: '/sourcecheck', name: 'sourcecheck', component: Sourcecheck },
    { path: '/runtime', name: 'runtime', component: Runtime },
    { path: '/rerun', name: 'rerun', component: Rerun },
    { path: '/baseline', name: 'baseline', component: Baseline },
    { path: '/partitioncheck', name: 'partitioncheck', component: Partitioncheck },
    { path: '/comparator', name: 'comparator', component: Comparator },
    { path: '/metastore', name: 'metastore', component: Metastore },
    { path: '/resource', name: 'resource', component: Resource },
    { path: '/slavalidate', name: 'slavalidate', component: Slavalidate },
    { path: '/sqltpl', name: 'sqltpl', component: Sqltpl },
    { path: '/backuprestore', name: 'backuprestore', component: Backuprestore },
    { path: '/logarchive', name: 'logarchive', component: Logarchive },
    { path: '/notifychan', name: 'notifychan', component: Notifychan },
    { path: '/notifyevent', name: 'notifyevent', component: Notifyevent },
    { path: '/publishplan', name: 'publishplan', component: Publishplan },
  ],
})

export default router
