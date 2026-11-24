/** 供应商归属对账接口：关联入口、批次、账单、账本与应付月报。 */
import { fetchJson, request } from './client'

export type RejectionReason = {
  code: string
  label: string
  count: number
}

export type AssociationRow = {
  行号: string
  订单号: string
  公司: string
  部门: string
  金额: number
  交易日期: string
  合同: string
  入账资格: '有' | '无'
  拒绝原因: string
  拒绝码: string
  说明: string
  合同开始: string
  合同结束: string
  合同归属: string
  日期在有效期内: boolean
  对账期内: boolean
}

export type AssociationOrder = {
  order_key: string
  companies: string[]
  line_count: number
  eligible_amount: number
  rows: AssociationRow[]
}

export type AssociationView = {
  entry: string
  period: string
  date_bounds: { start: string; end: string }
  company: string
  companies: string[]
  orders: AssociationOrder[]
  summary: {
    line_count: number
    order_count: number
    eligible_count: number
    eligible_amount: number
    reason_counts: Record<string, number>
    reason_labels: Record<string, string>
  }
}

export type BatchCheck = AssociationRow

export type ReconBatch = {
  batch_id: number
  batch_no: string
  period: string
  company: string
  department: string
  created_at: string
  status: string
  bill_id: number
  payable: number
  eligible_count: number
  rejected_count: number
  reason_counts: Record<string, number>
  checks: BatchCheck[]
  events: { at: string; event: string; [key: string]: unknown }[]
}

export type BillBlocker = {
  行号: string
  订单号: string
  金额: number
  拒绝原因: string
  拒绝码: string
}

export type Bill = {
  bill_id: number
  bill_no: string
  batch_id: number
  period: string
  company: string
  department: string
  status: string
  line_ids: string[]
  removed_line_ids: string[]
  claimed_by: string
  signed_at: string
  signed_by: string
  signed_amount: number
  checks: BatchCheck[]
  payable: number
  blockers: BillBlocker[]
  can_sign: boolean
  booked: boolean
  events: { at: string; event: string; [key: string]: unknown }[]
}

export type PayableReport = {
  report_id: number
  report_no: string
  template: string
  period: string
  date_bounds: { start: string; end: string }
  generated_at: string
  batch_count: number
  batches: {
    batch_no: string
    company: string
    department: string
    payable: number
    eligible_count: number
    rejected_count: number
    reason_counts: Record<string, number>
  }[]
  batch_lines: (AssociationRow & { batch_no: string; company: string; department: string })[]
  department_receipts: {
    company: string
    department: string
    payable: number
    booked: number
    unbooked: number
    batch_nos: string[]
  }[]
  booked_entries: {
    entry_id: number
    bill_no: string
    amount: number
    signed_at: string
    signed_by: string
    basis: AssociationRow[]
    basis_hash: string
  }[]
  unsigned_bills: {
    bill_no: string
    company: string
    department: string
    status: string
    payable: number
    blockers: BillBlocker[]
    last_attempt: { at: string; result: string; reasons: unknown } | null
  }[]
  sign_rejections: { at: string; result: string; reasons: unknown }[]
  totals: {
    payable: number
    booked: number
    unbooked: number
    batch_count: number
    bill_count: number
    signed_bill_count: number
    unsigned_bill_count: number
  }
}

export async function loadAssociation(params: {
  period: string
  company?: string
  order_key?: string
}): Promise<AssociationView> {
  const query = new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, value]) => value)),
  ).toString()
  return await fetchJson<AssociationView>(`/api/recon/association?${query}`)
}

export async function loadBatches(period: string): Promise<ReconBatch[]> {
  const payload = await fetchJson<{ items: ReconBatch[] }>(
    `/api/recon/batches?${new URLSearchParams({ period })}`,
  )
  return payload.items
}

export async function createBatch(period: string, company: string): Promise<{
  created: boolean
  message: string
  batch: ReconBatch
  bill: Bill | null
}> {
  const response = await request('/api/recon/batches', {
    method: 'POST',
    body: JSON.stringify({ period, company }),
  })
  if (!response.ok) throw new Error('生成归属对账批次失败')
  return await response.json()
}

export async function loadBills(period: string): Promise<Bill[]> {
  const payload = await fetchJson<{ items: Bill[] }>(
    `/api/recon/bills?${new URLSearchParams({ period })}`,
  )
  return payload.items
}

type BillActionResult = { ok: boolean; message: string; bill?: Bill }

async function billAction(path: string, body: Record<string, unknown>): Promise<BillActionResult> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) throw new Error('账单操作失败')
  return await response.json()
}

export function claimBill(billId: number): Promise<BillActionResult> {
  return billAction(`/api/recon/bills/${billId}/claim`, {})
}

export function removeBillLine(billId: number, lineId: string, reason = ''): Promise<BillActionResult> {
  return billAction(`/api/recon/bills/${billId}/remove-line`, { line_id: lineId, reason })
}

export function returnBill(billId: number, reason: string): Promise<BillActionResult> {
  return billAction(`/api/recon/bills/${billId}/return`, { reason })
}

export function signBill(billId: number): Promise<BillActionResult & { entry?: unknown; duplicate?: boolean }> {
  return billAction(`/api/recon/bills/${billId}/sign`, {})
}

export async function generateReport(period: string): Promise<PayableReport> {
  const response = await request('/api/recon/reports', {
    method: 'POST',
    body: JSON.stringify({ period }),
  })
  if (!response.ok) throw new Error('生成应付月报失败')
  return await response.json()
}
