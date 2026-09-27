import { useEffect, useState } from 'react'
import { api, fmtTime, priorityColor, statusColor } from '../api/client'
import type { Order, StandbyResponse } from '../types'

interface Props { orderId: string; onClose: () => void; onChanged: () => void; notice: (m: string, k?: 'ok' | 'err') => void; tick: number }

export default function OrderDetail({ orderId, onClose, onChanged, notice, tick }: Props) {
  const [order, setOrder] = useState<Order | null>(null)
  const [standby, setStandby] = useState<StandbyResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    api.order(orderId).then((o) => { if (alive) { setOrder(o); setErr(null) } }).catch((e) => alive && setErr((e as Error).message))
    api.standby(orderId).then((s) => alive && setStandby(s)).catch(() => undefined)
    return () => { alive = false }
  }, [orderId, tick])
  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true)
    try { await fn(); notice(`${label} ok`); onChanged() } catch (e) { const x = e as { code?: string; message: string }; notice(`${label} failed (${x.code ?? 'error'}): ${x.message}`, 'err') } finally { setBusy(false) }
  }
  if (err) return <Drawer onClose={onClose}><div className="p-3 text-sm text-red-700">{err}</div></Drawer>
  if (!order) return <Drawer onClose={onClose}><div className="p-3 text-sm text-gray-500">Loading…</div></Drawer>
  const a = order.assignment
  const next = order.lifecycle_status === 'OPEN' ? 'depart' : order.lifecycle_status === 'EN_ROUTE' ? 'arrive' : order.lifecycle_status === 'ARRIVED' ? 'start' : order.lifecycle_status === 'IN_PROGRESS' ? 'complete' : null
  return (
    <Drawer onClose={onClose}>
      <div className="space-y-3 p-3 text-xs">
        <div className="flex items-center gap-2">
          <span data-testid="detail-order-id" className="font-mono text-base font-bold">{order.id}</span>
          <span className={`badge ${priorityColor[order.effective_priority]}`}>{order.effective_priority}</span>
          <span className={`badge ${statusColor[order.lifecycle_status]}`}>{order.lifecycle_status}</span>
          <span className={`badge ${statusColor[order.scheduling_status]}`}>{order.scheduling_status}</span>
          {order.paid_expedite && <span className="badge bg-pink-100 text-pink-800">paid expedite (simulated)</span>}
          {order.locked && <span className="badge bg-gray-800 text-white">🔒 locked</span>}
        </div>
        <Section title="Customer request">
          <div className="text-gray-700">“{order.description || '—'}”</div>
          <KV k="Customer" v={`${order.customer_name} · ${order.contact_phone}`} />
          <KV k="Location" v={order.location.name} />
          <KV k="Window" v={`${fmtTime(order.window_start)}–${fmtTime(order.window_end)}`} />
        </Section>
        <Section title="Catalog entry (fixed parameters)">
          <KV k="Trade / problem" v={`${order.catalog_snapshot.trade_type} – ${order.catalog_snapshot.problem_name}`} />
          <KV k="Complexity" v={`level ${order.catalog_snapshot.complexity_level} (technician needs ≥ this level)`} />
          <KV k="Duration" v={`${order.catalog_snapshot.repair_duration_minutes} min (from catalog v${order.catalog_snapshot.catalog_version})`} />
        </Section>
        <Section title="Priority reasons">
          <KV k="Base / risk / effective" v={`${order.base_priority} / ${order.risk_priority} / ${order.effective_priority}`} />
          <ul className="list-disc pl-4">{order.priority_reasons.map((r, i) => <li key={i}><b>{r.priority}</b> {r.type}: {r.detail}</li>)}</ul>
          {order.breach_recorded_at && <div className="text-red-700">Deadline breached at {fmtTime(order.breach_recorded_at)} (recorded, not erased by recovery)</div>}
          {order.recovery_start && <div className="text-orange-700">Recovery start (relaxed window): {fmtTime(order.recovery_start)}</div>}
        </Section>
        <Section title="Assignment & execution">
          {a ? (
            <>
              <KV k="Technician" v={`${order.technician_name} (${a.technician_id})`} />
              <KV k="Plan" v={`depart ${fmtTime(a.departure)} · arrive ${fmtTime(a.arrival)} (${a.travel_minutes} min) · wait ${a.waiting_minutes} · service ${fmtTime(a.service_start)}–${fmtTime(a.service_end)}`} />
              <KV k="Match score" v={a.match_score != null ? `${a.match_score.toFixed(1)} — ${Object.entries(a.score_components).filter(([k]) => k !== 'total').map(([k, v]) => `${k} ${(v.value ?? 0).toFixed(2)}×${v.weight}`).join(' · ')}` : 'n/a (seed/locked)'} />
            </>
          ) : <div className="text-gray-500">No active assignment{order.scheduling_status === 'UNRESOLVED' ? ' — awaiting recovery (no feasible plan within authority)' : order.scheduling_status === 'PENDING_REVIEW' ? ' — candidate plans await review' : ''}.</div>}
          <KV k="Actual" v={`departed ${fmtTime(order.departed_at)} · arrived ${fmtTime(order.arrived_at)} · started ${fmtTime(order.service_started_at)} · completed ${fmtTime(order.completed_at)}`} />
          <div className="mt-1 flex flex-wrap gap-1">
            {next && a && <button className="btn" disabled={busy} onClick={() => act(`Technician ${next}`, () => api.executionEvent(order.id, next))}>Technician panel: {next}</button>}
            {order.can_cancel && <button data-testid="cancel-order" className="btn btn-danger" disabled={busy} onClick={() => act('Cancel (dispatcher on behalf of customer)', () => api.cancelOrder(order.id, { actor: 'dispatcher', reason: 'cancelled from dashboard' }))}>Cancel order</button>}
            {!order.can_cancel && order.lifecycle_status !== 'CANCELLED' && order.lifecycle_status !== 'COMPLETED' && <span className="self-center text-gray-500">cancel not allowed: technician already {order.lifecycle_status.toLowerCase().replace('_', ' ')}</span>}
            {order.lifecycle_status === 'OPEN' && <button className="btn" disabled={busy} onClick={() => act('Dispatch', () => api.dispatch(order.id))}>Re-run dispatch</button>}
          </div>
          {order.can_abort_execution && <AbortExecution order={order} busy={busy} act={act} />}
        </Section>
        {standby && (order.effective_priority === 'P2' || standby.candidates.length > 0) && (
          <Section title="P2 standby (not a reservation)">
            <KV k="Current assignment valid" v={standby.has_valid_assignment ? 'yes — kept, no technician change' : 'no — zero-disturbance re-dispatch / awaiting recovery'} />
            {standby.candidates.length === 0 ? <div className="text-gray-500">no standby candidates computed{order.effective_priority !== 'P2' ? ' (only prepared for P2 with a valid assignment)' : ''}</div> : (
              <table className="w-full"><thead className="text-gray-400"><tr><th className="text-left">technician</th><th>earliest start</th><th>skill</th><th>valid</th><th>computed</th></tr></thead>
                <tbody>{standby.candidates.map((c) => <tr key={c.id}><td>{c.technician_name}</td><td className="text-center font-mono">{fmtTime(c.earliest_start)}</td><td className="text-center">{c.skill_match.level}/{c.skill_match.required}</td><td className="text-center">{c.valid ? '✓' : '✗'}</td><td className="text-center">{fmtTime(c.computed_at)}</td></tr>)}</tbody></table>
            )}
            <div className="text-[10px] text-gray-500">{standby.note}</div>
          </Section>
        )}
        {order.risks && order.risks.length > 0 && (
          <Section title="Active risks">{order.risks.map((r) => <div key={r.id}><b>{r.severity}</b> {r.type} — {String(r.payload.detail ?? '')} <span className="text-gray-400">({r.status})</span></div>)}</Section>
        )}
        {order.plans && order.plans.length > 0 && (
          <Section title="Recent candidate plans">
            {order.plans.map((p) => <div key={p.id} className="flex gap-1"><span className="badge bg-gray-100">{p.status}</span><span>{p.strategy}</span><span className="text-gray-500">score {p.decision_score?.toFixed(1)} · affected {p.affected_order_ids.length} · {p.policy_check.decision}</span></div>)}
          </Section>
        )}
      </div>
    </Drawer>
  )
}

/** The only way out of a departed order that can no longer be served. Deliberate and audited: a reason is required,
 *  and the dispatcher chooses whether the order goes back into dispatch or is closed. */
function AbortExecution({ order, busy, act }: { order: Order; busy: boolean; act: (label: string, fn: () => Promise<unknown>) => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [outcome, setOutcome] = useState<'reschedule' | 'cancel'>('reschedule')
  if (!open) {
    return (
      <div className="mt-1">
        <button data-testid="abort-execution-open" className="btn btn-danger" disabled={busy} onClick={() => setOpen(true)}>Stop this visit…</button>
        <div className="mt-0.5 text-[10px] text-gray-500">Releases the execution lock when the technician can no longer serve this order (accident, no access, safety). Depart/arrive facts are kept.</div>
      </div>
    )
  }
  return (
    <form className="mt-1 space-y-1 rounded-lg border border-red-200 bg-red-50 p-2" data-testid="abort-execution-form"
      onSubmit={(e) => { e.preventDefault(); act(`Stop visit (${outcome})`, () => api.abortExecution(order.id, { reason, outcome })).then(() => setOpen(false)) }}>
      <div className="font-semibold text-red-900">Stop the visit on {order.id} ({order.lifecycle_status.toLowerCase().replace('_', ' ')})</div>
      <input className="w-full rounded border px-2 py-1" placeholder="Reason (required, recorded)" value={reason} onChange={(e) => setReason(e.target.value)} required />
      <div className="flex gap-2">
        <label className="flex items-center gap-1"><input type="radio" checked={outcome === 'reschedule'} onChange={() => setOutcome('reschedule')} />re-dispatch to another technician</label>
        <label className="flex items-center gap-1"><input type="radio" checked={outcome === 'cancel'} onChange={() => setOutcome('cancel')} />cancel the order</label>
      </div>
      <div className="flex gap-1">
        <button type="button" className="btn" onClick={() => setOpen(false)}>Back</button>
        <button data-testid="abort-execution-submit" className="btn btn-danger flex-1 justify-center" disabled={busy || !reason.trim()}>Confirm — release {order.technician_name ?? 'the technician'}</button>
      </div>
    </form>
  )
}

function Drawer({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-y-0 right-0 z-[600] flex w-[440px] max-w-full flex-col border-l border-gray-200 bg-white shadow-xl">
      <div className="flex items-center justify-between border-b border-gray-100 px-3 py-1.5"><span className="text-xs font-semibold uppercase text-gray-500">Order detail</span><button className="btn" onClick={onClose}>close</button></div>
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </div>
  )
}
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <div><div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">{title}</div><div className="space-y-0.5">{children}</div></div>
}
function KV({ k, v }: { k: string; v: string }) {
  return <div className="grid grid-cols-[110px_1fr] gap-1"><span className="text-gray-500">{k}</span><span className="text-gray-800">{v}</span></div>
}
