<template>
  <section class="page" data-module="supplier_recon">
    <header class="page-head">
      <div>
        <h2>供应商归属对账</h2>
        <p class="page-desc">
          在业务键关联之上按合同归属与生效日核验入账资格；报告模板只从归属对账批次计算应付。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="generate-batch" type="button" :disabled="busy" @click="onCreateBatch">
          生成归属对账批次
        </button>
        <button class="btn" data-testid="generate-report" type="button" :disabled="busy" @click="onGenerateReport">
          生成本月应付报告
        </button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="onQueryAssociation">
      <label class="filter-item">
        <span>对账期</span>
        <input v-model="period" data-testid="filter-period" placeholder="YYYY-MM" />
      </label>
      <label class="filter-item">
        <span>公司</span>
        <select v-model="company" data-testid="filter-company">
          <option value="">全部公司</option>
          <option v-for="item in companies" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>订单号</span>
        <input v-model="orderKey" data-testid="filter-order" placeholder="按订单号检索" />
      </label>
      <button class="btn" data-testid="query-association" type="submit">查关联入口</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">对账期</span>
        <strong class="stat-value stat-range">{{ association?.date_bounds.start ?? '—' }} ~ {{ association?.date_bounds.end ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">有关联行 / 有资格行</span>
        <strong class="stat-value">{{ association?.summary.line_count ?? 0 }} / {{ association?.summary.eligible_count ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">有证明的应付金额</span>
        <strong class="stat-value" data-testid="association-amount">{{ money(association?.summary.eligible_amount ?? 0) }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">本月实际入账</span>
        <strong class="stat-value" data-testid="booked-amount">{{ report ? money(report.totals.booked) : '—' }}</strong>
      </article>
    </div>

    <p class="status-legend" data-testid="reason-legend">
      <span v-for="code in reasonCodes" :key="code" class="legend-item" :data-reason="code">
        {{ reasonLabels[code] }}：{{ association?.summary.reason_counts[code] ?? 0 }}
      </span>
    </p>

    <p v-if="message" class="notice-text" data-testid="message">{{ message }}</p>
    <p v-if="errorMessage" class="error-text" data-testid="error">{{ errorMessage }}</p>

    <!-- 关联入口：按订单号看两公司、日期边界与拒绝原因 -->
    <h3 class="section-title">关联入口 · 按订单号核验归属</h3>
    <table class="data-table" data-testid="association-table">
      <thead>
        <tr>
          <th>订单号</th><th>公司</th><th>部门</th><th>金额</th><th>交易日期</th>
          <th>合同</th><th>合同有效期</th><th>日期在有效期内</th><th>入账资格</th><th>拒绝原因</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="order in association?.orders ?? []" :key="order.order_key">
          <tr
            v-for="row in order.rows"
            :key="row.行号"
            :data-testid="`assoc-${row.行号}`"
            :data-eligible="row.入账资格"
            :data-reason="row.拒绝码"
          >
            <td>{{ row.订单号 }}</td>
            <td>{{ row.公司 }}</td>
            <td>{{ row.部门 }}</td>
            <td>{{ money(row.金额) }}</td>
            <td>{{ row.交易日期 }}</td>
            <td>{{ row.合同 }}</td>
            <td>{{ row.合同开始 }} ~ {{ row.合同结束 }}</td>
            <td>{{ row.日期在有效期内 ? '是' : '否' }}</td>
            <td>{{ row.入账资格 }}</td>
            <td>{{ row.拒绝原因 }}</td>
          </tr>
        </template>
        <tr v-if="!association?.orders.length">
          <td colspan="10" class="empty-state">当前条件下没有关联明细</td>
        </tr>
      </tbody>
    </table>

    <!-- 归属对账批次 -->
    <h3 class="section-title">归属对账批次</h3>
    <table class="data-table" data-testid="batch-table">
      <thead>
        <tr><th>批次号</th><th>公司</th><th>部门</th><th>应付金额</th><th>有资格</th><th>被拒绝</th><th>拒绝分布</th><th>创建时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="batch in batches" :key="batch.batch_id" :data-testid="`batch-${batch.batch_id}`">
          <td>{{ batch.batch_no }}</td>
          <td>{{ batch.company }}</td>
          <td>{{ batch.department }}</td>
          <td>{{ money(batch.payable) }}</td>
          <td>{{ batch.eligible_count }}</td>
          <td>{{ batch.rejected_count }}</td>
          <td>{{ reasonSummary(batch.reason_counts) }}</td>
          <td>{{ batch.created_at }}</td>
        </tr>
        <tr v-if="!batches.length">
          <td colspan="8" class="empty-state">尚未生成归属对账批次</td>
        </tr>
      </tbody>
    </table>

    <!-- 账单流转 -->
    <h3 class="section-title">待签账单 · 领取 / 剔除 / 退回 / 签发</h3>
    <table class="data-table" data-testid="bill-table">
      <thead>
        <tr><th>账单号</th><th>公司 / 部门</th><th>状态</th><th>应付</th><th>阻断行</th><th>已入账</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="bill in bills" :key="bill.bill_id" :data-testid="`bill-${bill.bill_id}`" :data-status="bill.status">
          <td>{{ bill.bill_no }}</td>
          <td>{{ bill.company }} / {{ bill.department }}</td>
          <td>{{ bill.status }}</td>
          <td>{{ money(bill.payable) }}</td>
          <td>
            <span v-if="!bill.blockers.length">—</span>
            <span v-for="blocker in bill.blockers" :key="blocker.行号" class="blocker-tag" :data-reason="blocker.拒绝码">
              {{ blocker.行号 }} · {{ blocker.拒绝原因 }}
            </span>
          </td>
          <td>{{ bill.booked ? money(bill.signed_amount) : '—' }}</td>
          <td class="row-actions">
            <button
              v-if="bill.status === '待领取' || bill.status === '已退回复核'"
              class="link" type="button" :data-testid="`claim-${bill.bill_id}`" @click="onClaim(bill)"
            >领取</button>
            <button
              v-for="blocker in bill.blockers"
              :key="`remove-${blocker.行号}`"
              class="link" type="button" :data-testid="`remove-${bill.bill_id}-${blocker.行号}`"
              @click="onRemove(bill, blocker.行号)"
            >剔除 {{ blocker.行号 }}</button>
            <button
              v-if="bill.status === '已领取'"
              class="link" type="button" :data-testid="`return-${bill.bill_id}`" @click="onReturn(bill)"
            >退回复核</button>
            <button
              class="link" type="button" :data-testid="`sign-${bill.bill_id}`"
              :disabled="!bill.can_sign" @click="onSign(bill)"
            >签发</button>
          </td>
        </tr>
        <tr v-if="!bills.length">
          <td colspan="7" class="empty-state">尚未生成账单</td>
        </tr>
      </tbody>
    </table>

    <!-- 月报：只采用有归属证明的入账行 -->
    <section v-if="report" class="report-panel" data-testid="report-panel">
      <h3 class="section-title">{{ report.report_no }} · {{ report.template }}（{{ report.period }}）</h3>
      <div class="stat-row">
        <article class="stat-card"><span class="stat-label">报告应付</span><strong class="stat-value">{{ money(report.totals.payable) }}</strong></article>
        <article class="stat-card"><span class="stat-label">实际入账</span><strong class="stat-value">{{ money(report.totals.booked) }}</strong></article>
        <article class="stat-card"><span class="stat-label">未入账</span><strong class="stat-value">{{ money(report.totals.unbooked) }}</strong></article>
        <article class="stat-card"><span class="stat-label">未签发账单</span><strong class="stat-value">{{ report.totals.unsigned_bill_count }}</strong></article>
      </div>

      <h4 class="section-title">部门接收金额</h4>
      <table class="data-table" data-testid="receipt-table">
        <thead><tr><th>公司</th><th>部门</th><th>报告应付</th><th>实际入账</th><th>未入账</th><th>批次</th></tr></thead>
        <tbody>
          <tr v-for="receipt in report.department_receipts" :key="`${receipt.company}-${receipt.department}`">
            <td>{{ receipt.company }}</td>
            <td>{{ receipt.department }}</td>
            <td>{{ money(receipt.payable) }}</td>
            <td>{{ money(receipt.booked) }}</td>
            <td>{{ money(receipt.unbooked) }}</td>
            <td>{{ receipt.batch_nos.join('、') }}</td>
          </tr>
          <tr v-if="!report.department_receipts.length">
            <td colspan="6" class="empty-state">报告期内没有归属对账批次</td>
          </tr>
        </tbody>
      </table>

      <h4 class="section-title">未能签发的记录</h4>
      <table class="data-table" data-testid="unsigned-table">
        <thead><tr><th>账单号</th><th>公司 / 部门</th><th>状态</th><th>应付</th><th>阻断原因</th><th>最近一次签发</th></tr></thead>
        <tbody>
          <tr v-for="item in report.unsigned_bills" :key="item.bill_no">
            <td>{{ item.bill_no }}</td>
            <td>{{ item.company }} / {{ item.department }}</td>
            <td>{{ item.status }}</td>
            <td>{{ money(item.payable) }}</td>
            <td>
              <span v-for="blocker in item.blockers" :key="blocker.行号" class="blocker-tag" :data-reason="blocker.拒绝码">
                {{ blocker.行号 }} · {{ blocker.拒绝原因 }}
              </span>
              <span v-if="!item.blockers.length">—</span>
            </td>
            <td>{{ item.last_attempt ? item.last_attempt.at : '尚未尝试签发' }}</td>
          </tr>
          <tr v-if="!report.unsigned_bills.length">
            <td colspan="6" class="empty-state">报告期内账单均已签发</td>
          </tr>
        </tbody>
      </table>

      <h4 class="section-title">实际入账的对账数据</h4>
      <table class="data-table" data-testid="booked-table">
        <thead><tr><th>入账 ID</th><th>账单号</th><th>金额</th><th>签发人</th><th>签发时间</th><th>口径哈希</th></tr></thead>
        <tbody>
          <tr v-for="entry in report.booked_entries" :key="entry.entry_id">
            <td>{{ entry.entry_id }}</td>
            <td>{{ entry.bill_no }}</td>
            <td>{{ money(entry.amount) }}</td>
            <td>{{ entry.signed_by }}</td>
            <td>{{ entry.signed_at }}</td>
            <td>{{ entry.basis_hash }}</td>
          </tr>
          <tr v-if="!report.booked_entries.length">
            <td colspan="6" class="empty-state">本期尚无入账记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>对账期 {{ period }}，合同 {{ contractCount }} 份，拒绝原因 {{ reasonCodes.length }} 类</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  claimBill,
  createBatch,
  generateReport,
  loadAssociation as loadAssociationApi,
  loadBatches,
  loadBills,
  removeBillLine,
  returnBill,
  signBill,
  type AssociationView,
  type Bill,
  type PayableReport,
  type ReconBatch,
} from '@/api/recon'
import { fetchJson } from '@/api/client'

const period = ref('2026-11')
const company = ref('')
const orderKey = ref('')
const association = ref<AssociationView | null>(null)
const batches = ref<ReconBatch[]>([])
const bills = ref<Bill[]>([])
const report = ref<PayableReport | null>(null)
const message = ref('')
const errorMessage = ref('')
const busy = ref(false)
const contractCount = ref(0)

const companies = computed(() => association.value?.companies ?? [])
const reasonLabels = computed<Record<string, string>>(
  () => association.value?.summary.reason_labels ?? {},
)
const reasonCodes = computed(() => Object.keys(reasonLabels.value))

function money(value: number): string {
  return Number(value ?? 0).toFixed(2)
}

function reasonSummary(counts: Record<string, number>): string {
  const parts = Object.entries(counts)
    .filter(([code, count]) => count > 0 && code !== 'ELIGIBLE')
    .map(([code, count]) => `${reasonLabels.value[code] ?? code} ×${count}`)
  return parts.length ? parts.join('，') : '无拒绝'
}

function resetFilters() {
  company.value = ''
  orderKey.value = ''
  void run(async () => {
    await loadAssociationOnly()
    await refresh()
  })
}

async function loadAssociationOnly() {
  association.value = await loadAssociationApi({
    period: period.value,
    company: company.value,
    order_key: orderKey.value,
  })
}

async function refresh() {
  errorMessage.value = ''
  const [batchItems, billItems, contracts] = await Promise.all([
    loadBatches(period.value),
    loadBills(period.value),
    fetchJson<{ items: unknown[] }>('/api/recon/contracts'),
  ])
  batches.value = batchItems
  bills.value = billItems
  contractCount.value = contracts.items.length
}

async function run(action: () => Promise<void>) {
  busy.value = true
  errorMessage.value = ''
  try {
    await action()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  } finally {
    busy.value = false
  }
}

async function onQueryAssociation() {
  await run(async () => {
    await loadAssociationOnly()
    message.value = ''
  })
}

async function onCreateBatch() {
  await run(async () => {
    const result = await createBatch(period.value, company.value || '甲公司')
    message.value = result.message
    await loadAssociationOnly()
    await refresh()
  })
}

async function onGenerateReport() {
  await run(async () => {
    report.value = await generateReport(period.value)
    message.value = `已生成应付月报 ${report.value.report_no}`
  })
}

async function onClaim(bill: Bill) {
  await run(async () => {
    const result = await claimBill(bill.bill_id)
    message.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await refresh()
  })
}

async function onRemove(bill: Bill, lineId: string) {
  await run(async () => {
    const result = await removeBillLine(bill.bill_id, lineId, '财务剔除')
    message.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await refresh()
  })
}

async function onReturn(bill: Bill) {
  await run(async () => {
    const result = await returnBill(bill.bill_id, '退回复核')
    message.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await refresh()
  })
}

async function onSign(bill: Bill) {
  await run(async () => {
    const result = await signBill(bill.bill_id)
    message.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await refresh()
    if (report.value) report.value = await generateReport(period.value)
  })
}

onMounted(() => {
  void run(async () => {
    await loadAssociationOnly()
    await refresh()
  })
})
</script>

<style scoped>
.stat-range { font-size: 14px; }
.section-title { margin: 20px 0 8px; font-size: 15px; }
.notice-text { color: #1f6feb; font-size: 13px; }
.blocker-tag { display: inline-block; margin-right: 6px; padding: 1px 6px; border-radius: 4px; background: #fef3c7; color: #92400e; font-size: 12px; }
.blocker-tag[data-reason='CONTRACT_REVOKED'] { background: #fee2e2; color: #b42318; }
.blocker-tag[data-reason='EXPIRED_ORDER'] { background: #e0e7ff; color: #3730a3; }
.blocker-tag[data-reason='WRONG_DEPARTMENT'] { background: #dbeafe; color: #1d4ed8; }
.blocker-tag[data-reason='UNAUTHORIZED'] { background: #fce7f3; color: #9d174d; }
.report-panel { margin-top: 12px; }
</style>
