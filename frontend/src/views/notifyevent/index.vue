<template>
  <section class="page" data-module="notifyevent">
    <header class="page-head">
      <div>
        <h2>通知投递事件</h2>
        <p class="page-desc">
          SLA升级消息按稳定幂等键去重生成投递事件，投递尝试与确认回执原子落库；
          SLA监控只依据通知投递事件快照核对异常升级、送达结果与静默时段结果。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="showBatchPanel = !showBatchPanel">
          多选升级记录提交批次
        </button>
        <button class="btn" type="button" @click="generateSnapshot">生成投递事件快照</button>
        <button class="btn ghost" type="button" @click="reloadAll">刷新</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <nav class="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab-btn"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}<span v-if="tab.count !== null" class="tab-count">{{ tab.count }}</span>
      </button>
    </nav>

    <!-- 多条升级记录多选一次提交为投递批次 -->
    <form v-if="showBatchPanel" class="batch-panel" @submit.prevent="submitBatch">
      <div class="select-group">
        <span class="group-title">SLA升级记录（多选）</span>
        <label v-for="sla in slaRules" :key="String(sla.id)" class="check-item">
          <input v-model="selectedSlaIds" type="checkbox" :value="Number(sla.id)" />
          {{ sla['规则编号'] }} · {{ sla['适用作业'] }} · 通知组：{{ sla['升级通知组'] }}
        </label>
      </div>
      <div class="select-group">
        <span class="group-title">通知渠道（多选，留空则沿用各升级通知组的既有渠道配置）</span>
        <label v-for="chan in channels" :key="String(chan.id)" class="check-item">
          <input v-model="selectedChannelIds" type="checkbox" :value="Number(chan.id)" />
          {{ chan['渠道编号'] }} · {{ chan['渠道类型'] }} · {{ chan['接收地址'] }}
        </label>
      </div>
      <div class="batch-foot">
        <button class="btn primary" type="submit">一次提交为投递批次并批量生成事件</button>
        <span class="hint">已选 SLA {{ selectedSlaIds.length }} 条 / 渠道 {{ selectedChannelIds.length }} 个，重复提交按幂等键自动去重</span>
      </div>
    </form>

    <!-- 投递事件 -->
    <template v-if="activeTab === 'events'">
      <form class="filter-bar" @submit.prevent="reloadEvents">
        <label class="filter-item">
          <span>事件/幂等键/消息编号</span>
          <input v-model="filters.keyword" placeholder="按关键字检索" />
        </label>
        <label class="filter-item">
          <span>事件状态</span>
          <select v-model="filters.status">
            <option value="">全部</option>
            <option v-for="s in statusOptions" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>值班组</span>
          <input v-model="filters.group" placeholder="按值班组过滤" />
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in eventColumns" :key="column">{{ column }}</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in events" :key="String(row.id)">
            <td v-for="column in eventColumns" :key="column">
              <span v-if="column === '事件状态'" class="status-tag" :class="statusClass(row[column])">{{ row[column] }}</span>
              <span v-else-if="column === '采纳记录' || column === '冗余送达'">{{ boolText(row[column]) }}</span>
              <span v-else>{{ display(row[column]) }}</span>
            </td>
            <td class="row-actions">
              <button
                v-if="row['事件状态'] === '待投递'"
                class="link" type="button"
                @click="sendEvent(Number(row.id), false)"
              >投递</button>
              <button
                v-if="row['事件状态'] === '待投递'"
                class="link danger" type="button"
                @click="sendEvent(Number(row.id), true)"
              >模拟失败</button>
              <button
                v-if="row['事件状态'] === '投递失败'"
                class="link" type="button"
                @click="resetEvent(Number(row.id))"
              >重置后从最后尝试继续</button>
              <button
                v-if="row['事件状态'] === '已送达' || row['事件状态'] === '已确认送达'"
                class="link" type="button"
                @click="registerReceipt(Number(row.id), '送达确认')"
              >重推送达回执</button>
              <button
                v-if="row['事件状态'] === '已送达' || row['事件状态'] === '已确认送达'"
                class="link danger" type="button"
                @click="registerReceipt(Number(row.id), '失败回执')"
              >迟到失败回执</button>
            </td>
          </tr>
          <tr v-if="!events.length">
            <td :colspan="eventColumns.length + 1" class="empty-state">暂无投递事件，请先多选升级记录提交投递批次</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 投递批次 -->
    <template v-else-if="activeTab === 'batches'">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in batchColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in batches" :key="String(row.id)">
            <td v-for="column in batchColumns" :key="column">{{ display(row[column]) }}</td>
          </tr>
          <tr v-if="!batches.length">
            <td :colspan="batchColumns.length" class="empty-state">暂无投递批次</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 确认回执 -->
    <template v-else-if="activeTab === 'receipts'">
      <form class="filter-bar" @submit.prevent="reloadReceipts">
        <label class="filter-item">
          <span>按事件ID过滤（留空查全部）</span>
          <input v-model="receiptFilterEventId" placeholder="如 1" />
        </label>
        <button class="btn" type="submit">查询回执</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in receiptColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in receipts" :key="String(row.id)">
            <td v-for="column in receiptColumns" :key="column">
              <span v-if="column === '回执类型'" class="status-tag" :class="receiptClass(row[column])">{{ row[column] }}</span>
              <span v-else>{{ display(row[column]) }}</span>
            </td>
          </tr>
          <tr v-if="!receipts.length">
            <td :colspan="receiptColumns.length" class="empty-state">暂无确认回执</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 数据快照 -->
    <template v-else>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in snapshotColumns" :key="column">{{ column }}</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in snapshots" :key="String(row.id)">
            <td v-for="column in snapshotColumns" :key="column">{{ display(row[column]) }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="openSnapshot(Number(row.id))">查看固化事件</button>
            </td>
          </tr>
          <tr v-if="!snapshots.length">
            <td :colspan="snapshotColumns.length + 1" class="empty-state">暂无快照，SLA监控在快照生成前会拒绝确认升级</td>
          </tr>
        </tbody>
      </table>

      <div v-if="snapshotDetail" class="snapshot-detail">
        <h3>快照 {{ snapshotDetail['快照版本'] }} 固化的投递事件（不可变）</h3>
        <table class="data-table">
          <thead>
            <tr>
              <th v-for="column in frozenColumns" :key="column">{{ column }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in snapshotDetail.events" :key="String(row.id)">
              <td v-for="column in frozenColumns" :key="column">{{ display(row[column]) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- SLA 监控只依据快照核对升级 -->
    <section class="confirm-panel">
      <h3>SLA升级核对（只读取通知投递事件数据快照）</h3>
      <form class="filter-bar" @submit.prevent="confirmSla">
        <label class="filter-item">
          <span>SLA规则ID</span>
          <input v-model.number="confirmForm.slaRuleId" type="number" min="1" required />
        </label>
        <label class="filter-item">
          <span>快照版本ID（留空取最新）</span>
          <input v-model.number="confirmForm.snapshotVersion" type="number" min="1" />
        </label>
        <button class="btn primary" type="submit">核对升级动作</button>
      </form>
      <p v-if="confirmMessage" class="confirm-message" :class="{ ok: confirmOk, fail: !confirmOk }">{{ confirmMessage }}</p>
      <table v-if="confirmResult" class="data-table">
        <tbody>
          <tr><th>规则编号</th><td>{{ confirmResult['规则编号'] }}</td><th>值班组</th><td>{{ confirmResult['值班组'] }}</td></tr>
          <tr><th>快照版本</th><td>{{ confirmResult['快照版本'] }}</td><th>快照校验和</th><td>{{ confirmResult['快照校验和'] }}</td></tr>
          <tr><th>采纳消息编号</th><td>{{ display(confirmResult['采纳消息编号']) }}</td><th>采纳回执编号</th><td>{{ display(confirmResult['采纳回执编号']) }}</td></tr>
          <tr><th>渠道确认数</th><td>{{ confirmResult['渠道确认数'] }}</td><th>送达数</th><td>{{ confirmResult['送达数'] }}</td></tr>
          <tr><th>静默抑制数</th><td>{{ confirmResult['静默抑制数'] }}</td><th>失败数</th><td>{{ confirmResult['失败数'] }}</td></tr>
          <tr><th>重复通知次数</th><td>{{ confirmResult['重复通知次数'] }}</td><th>确认通过</th><td>{{ boolText(confirmResult['确认通过']) }}</td></tr>
        </tbody>
      </table>
      <table v-if="confirmResult && confirmResult.confirmations.length" class="data-table confirm-sub">
        <thead>
          <tr>
            <th v-for="column in confirmationColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in confirmResult.confirmations" :key="row['事件编号']">
            <td v-for="column in confirmationColumns" :key="column">
              <span v-if="column === '同一次送达' || column === '采纳记录' || column === '冗余送达'">{{ boolText(row[column]) }}</span>
              <span v-else>{{ display(row[column]) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条通知投递事件记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="actionMessage" class="ok-text">{{ actionMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type CellValue = string | number | boolean | null | undefined | string[] | number[]
type Row = Record<string, CellValue>

interface ConfirmationRow {
  [key: string]: CellValue
  '事件编号': string
  '消息编号': string
  '同一次送达': boolean
  '采纳记录': boolean
  '冗余送达': boolean
}

interface SlaConfirmResult {
  slaRuleId: number
  '规则编号': string
  '值班组': string
  '快照版本': string
  '快照校验和': string
  '确认通过': boolean
  渠道确认数: number
  送达数: number
  静默抑制数: number
  失败数: number
  重复通知次数: number
  '采纳消息编号': string | null
  '采纳回执编号': string | null
  confirmations: ConfirmationRow[]
}

const ENDPOINT = '/api/notifyevent'

const eventColumns = ['事件编号', '幂等键', '规则编号', '值班组', '渠道编号', '消息编号', '事件状态', '送达结果', '投递尝试次数', '回执编号', '采纳记录', '冗余送达', '静默时段结果', '快照版本']
const batchColumns = ['批次编号', '提交时间', 'SLA规则ID列表', '渠道ID列表', '生成事件数', '去重跳过数', '静默抑制数', '状态']
const receiptColumns = ['回执编号', '事件编号', '消息编号', '幂等键', '回执类型', '回执状态', '回执详情', '登记时间']
const snapshotColumns = ['快照版本', '生成时间', '事件总数', '已送达数', '失败数', '静默抑制数', '待投递数', '校验和', '备注']
const frozenColumns = ['事件编号', '幂等键', '消息编号', '渠道编号', '事件状态', '送达结果', '回执编号', '采纳记录', '冗余送达', '静默时段结果']
const confirmationColumns = ['事件编号', '消息编号', '幂等键', '渠道编号', '接收地址', '事件状态', '送达结果', '回执编号', '静默时段结果', '采纳记录', '冗余送达', '投递尝试次数', '同一次送达']
const statusOptions = ['待投递', '静默抑制', '投递失败', '已送达', '已确认送达']

const events = ref<Row[]>([])
const batches = ref<Row[]>([])
const receipts = ref<Row[]>([])
const snapshots = ref<Row[]>([])
const slaRules = ref<Row[]>([])
const channels = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const actionMessage = ref('')
const activeTab = ref<'events' | 'batches' | 'receipts' | 'snapshots'>('events')
const showBatchPanel = ref(false)
const selectedSlaIds = ref<number[]>([])
const selectedChannelIds = ref<number[]>([])
const receiptFilterEventId = ref('')
const snapshotDetail = ref<(Row & { events: Row[] }) | null>(null)

const filters = reactive({ keyword: '', status: '', group: '' })
const confirmForm = reactive<{ slaRuleId: number | null; snapshotVersion: number | null }>({
  slaRuleId: null,
  snapshotVersion: null,
})
const confirmResult = ref<SlaConfirmResult | null>(null)
const confirmMessage = ref('')
const confirmOk = ref(false)

const stats = computed(() => [
  { label: '投递事件总数', value: total.value },
  { label: '已送达/已确认', value: events.value.filter((row) => row['事件状态'] === '已送达' || row['事件状态'] === '已确认送达').length },
  { label: '投递失败', value: events.value.filter((row) => row['事件状态'] === '投递失败').length },
  { label: '静默抑制', value: events.value.filter((row) => row['事件状态'] === '静默抑制').length },
])

const tabs = computed(() => [
  { key: 'events' as const, label: '投递事件', count: null },
  { key: 'batches' as const, label: '投递批次', count: batches.value.length },
  { key: 'receipts' as const, label: '确认回执', count: receipts.value.length },
  { key: 'snapshots' as const, label: '数据快照', count: snapshots.value.length },
])

function display(value: CellValue): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.join('、')
  return String(value)
}

function boolText(value: CellValue): string {
  return value ? '是' : '否'
}

function statusClass(value: CellValue): string {
  const map: Record<string, string> = {
    待投递: 'tag-pending',
    静默抑制: 'tag-muted',
    投递失败: 'tag-fail',
    已送达: 'tag-ok',
    已确认送达: 'tag-ok',
  }
  return map[String(value)] ?? 'tag-muted'
}

function receiptClass(value: CellValue): string {
  const map: Record<string, string> = {
    送达确认: 'tag-ok',
    失败回执: 'tag-fail',
    重复回执: 'tag-muted',
    冲突回执: 'tag-warn',
  }
  return map[String(value)] ?? 'tag-muted'
}

async function getJson(path: string): Promise<Record<string, unknown>> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as Record<string, unknown>
}

async function postAction(path: string, body: Record<string, unknown>): Promise<{ ok: boolean; message: string; entry?: Row }> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const payload = (await response.json()) as { ok: boolean; message: string; entry?: Row }
  if (!response.ok) throw new Error(payload.message || '操作未生效')
  return payload
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  filters.group = ''
  void reloadEvents()
}

async function reloadEvents() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.status) query.set('status', filters.status)
  if (filters.group) query.set('group', filters.group)
  query.set('size', '200')
  try {
    const payload = await getJson(`${ENDPOINT}?${query.toString()}`)
    events.value = (payload.items as Row[]) ?? []
    total.value = Number(payload.total ?? events.value.length)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '投递事件读取失败'
  }
}

async function reloadBatches() {
  const payload = await getJson(`${ENDPOINT}/batches`)
  batches.value = (payload.items as Row[]) ?? []
}

async function reloadReceipts() {
  const query = receiptFilterEventId.value ? `?event_id=${encodeURIComponent(receiptFilterEventId.value)}` : ''
  const payload = await getJson(`${ENDPOINT}/receipts${query}`)
  receipts.value = (payload.items as Row[]) ?? []
}

async function reloadSnapshots() {
  const payload = await getJson(`${ENDPOINT}/snapshots`)
  snapshots.value = (payload.items as Row[]) ?? []
}

async function reloadReferenceData() {
  const [slaPayload, chanPayload] = await Promise.all([
    getJson('/api/slavalidate?size=200'),
    getJson('/api/notifychan?size=200'),
  ])
  slaRules.value = (slaPayload.items as Row[]) ?? []
  channels.value = (chanPayload.items as Row[]) ?? []
}

async function reloadAll() {
  await Promise.all([reloadEvents(), reloadBatches(), reloadReceipts(), reloadSnapshots()])
}

async function submitBatch() {
  errorMessage.value = ''
  actionMessage.value = ''
  if (!selectedSlaIds.value.length) {
    errorMessage.value = '请至少选择一条SLA升级记录'
    return
  }
  try {
    const result = await postAction(`${ENDPOINT}/batches`, {
      slaRuleIds: selectedSlaIds.value,
      channelIds: selectedChannelIds.value.length ? selectedChannelIds.value : null,
    })
    actionMessage.value = result.message
    selectedSlaIds.value = []
    selectedChannelIds.value = []
    showBatchPanel.value = false
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批次提交失败'
  }
}

async function sendEvent(eventId: number, simulateFailure: boolean) {
  errorMessage.value = ''
  actionMessage.value = ''
  try {
    const result = await postAction(`${ENDPOINT}/${eventId}/send`, { simulateFailure })
    actionMessage.value = result.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '投递失败'
  }
}

async function resetEvent(eventId: number) {
  errorMessage.value = ''
  try {
    const result = await postAction(`${ENDPOINT}/${eventId}/reset`, {})
    actionMessage.value = result.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重置失败'
  }
}

async function registerReceipt(eventId: number, receiptStatus: string) {
  errorMessage.value = ''
  try {
    const result = await postAction(`${ENDPOINT}/${eventId}/receipts`, { receiptStatus, detail: '页面手工登记回执' })
    actionMessage.value = result.message
    await reloadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回执登记失败'
  }
}

async function generateSnapshot() {
  errorMessage.value = ''
  try {
    const result = await postAction(`${ENDPOINT}/snapshots`, { remark: '页面生成快照' })
    actionMessage.value = result.message
    await reloadSnapshots()
    await reloadEvents()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '快照生成失败'
  }
}

async function openSnapshot(version: number) {
  errorMessage.value = ''
  try {
    snapshotDetail.value = (await getJson(`${ENDPOINT}/snapshots/${version}`)) as Row & { events: Row[] }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '快照读取失败'
  }
}

async function confirmSla() {
  errorMessage.value = ''
  confirmResult.value = null
  if (!confirmForm.slaRuleId) {
    errorMessage.value = '请输入要核对的SLA规则ID'
    return
  }
  try {
    const body: Record<string, unknown> = {}
    if (confirmForm.snapshotVersion) body.snapshotVersion = confirmForm.snapshotVersion
    const result = await postAction(`/api/slavalidate/${confirmForm.slaRuleId}/confirm-delivery`, body)
    confirmOk.value = result.ok
    confirmMessage.value = result.message
    confirmResult.value = (result.entry ?? null) as SlaConfirmResult | null
  } catch (error) {
    confirmOk.value = false
    confirmMessage.value = error instanceof Error ? error.message : 'SLA核对失败'
  }
}

onMounted(() => {
  void reloadReferenceData()
  void reloadAll()
})
</script>

<style scoped>
.tab-bar { display: flex; gap: 8px; margin-bottom: 12px; }
.tab-btn { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px; }
.tab-btn.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.tab-count { margin-left: 6px; font-size: 12px; opacity: 0.8; }
.batch-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
.select-group { display: flex; flex-wrap: wrap; gap: 8px 18px; align-items: center; margin-bottom: 10px; }
.group-title { width: 100%; font-size: 13px; color: var(--muted); font-weight: 600; }
.check-item { font-size: 13px; display: flex; align-items: center; gap: 4px; }
.batch-foot { display: flex; align-items: center; gap: 12px; }
.hint { font-size: 12px; color: var(--muted); }
.status-tag { padding: 2px 8px; border-radius: 10px; font-size: 12px; white-space: nowrap; }
.tag-ok { background: #e7f6ec; color: #117a37; }
.tag-fail { background: #fdecec; color: #b42318; }
.tag-pending { background: #eef2ff; color: #3538cd; }
.tag-warn { background: #fff4e5; color: #b54708; }
.tag-muted { background: #f1f3f7; color: #64748b; }
.link.danger { color: #b42318; }
.snapshot-detail { margin-top: 14px; }
.snapshot-detail h3 { font-size: 14px; }
.confirm-panel { margin-top: 18px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
.confirm-panel h3 { margin: 0 0 10px; font-size: 15px; }
.confirm-message { font-size: 13px; }
.confirm-message.ok { color: #117a37; }
.confirm-message.fail { color: #b42318; }
.confirm-sub { margin-top: 10px; }
.ok-text { color: #117a37; }
</style>
