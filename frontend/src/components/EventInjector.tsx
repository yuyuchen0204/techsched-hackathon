import { useState } from 'react'
import { api } from '../api/client'
import type { Order, Technician } from '../types'

interface Props { orders: Order[]; techs: Technician[]; onClose: () => void; onChanged: () => void; notice: (m: string, k?: 'ok' | 'err') => void; presetTech?: string; simNow?: string | null }

export default function EventInjector({ orders, techs, onClose, onChanged, notice, presetTech, simNow }: Props) {
  const [type, setType] = useState('technician_unavailable')
  const [orderId, setOrderId] = useState(orders.find((o) => o.lifecycle_status === 'OPEN')?.id ?? '')
  const [techId, setTechId] = useState(presetTech ?? techs[0]?.id ?? '')
  const [reason, setReason] = useState('sick leave')
  const [until, setUntil] = useState('')   // HH:MM on the scenario day; empty = rest of the day
  const [text, setText] = useState('')
  const [ctype, setCtype] = useState('attitude')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const submit = async () => {
    setBusy(true)
    const body: Record<string, unknown> = { type, idempotency_key: `ui-${Date.now()}` }
    if (type === 'technician_unavailable') {
      Object.assign(body, { technician_id: techId, reason })
      if (until && simNow) body.end = `${simNow.slice(0, 10)}T${until}:00${simNow.slice(16) || '+08:00'}`  // same day, same offset as the sim clock
    }
    if (type === 'paid_expedite_order' || type === 'customer_cancel') Object.assign(body, { order_id: orderId, reason })
    if (type === 'lateness_complaint') Object.assign(body, { order_id: orderId, text })
    if (type === 'non_scheduling_complaint') Object.assign(body, { order_id: orderId, complaint_type: ctype, text })
    try {
      const r = await api.event(body)
      setResult(JSON.stringify(r, null, 1))
      notice(`Event ${type} processed`)
      onChanged()
    } catch (e) {
      const x = e as { code?: string; message: string }
      setResult(`${x.code}: ${x.message}`)
      notice(`Event failed: ${x.message}`, 'err')
    } finally { setBusy(false) }
  }
  const openOrders = orders.filter((o) => !['CANCELLED', 'COMPLETED'].includes(o.lifecycle_status))
  return (
    <div className="fixed inset-0 z-[700] flex items-center justify-center bg-black/30" onClick={onClose}>
      <div className="w-[520px] max-w-full rounded-lg bg-white p-4 text-xs shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-2 text-sm font-semibold">Inject event (structured, effective at the current sim time)</div>
        <label className="block">Type
          <select data-testid="event-type" className="mt-0.5 w-full rounded border px-1 py-1" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="technician_unavailable">technician_unavailable (undeparted tasks re-dispatched; executing tasks → manual)</option>
            <option value="paid_expedite_order">paid_expedite_order (simulated payment → base P1)</option>
            <option value="lateness_complaint">lateness_complaint (verified against clock/window)</option>
            <option value="non_scheduling_complaint">non_scheduling_complaint (manual queue, no priority change)</option>
            <option value="customer_cancel">customer_cancel (dispatcher on behalf; undeparted only)</option>
          </select>
        </label>
        {type === 'technician_unavailable' ? (
          <label className="mt-2 block">Technician
            <select data-testid="event-tech" className="mt-0.5 w-full rounded border px-1 py-1" value={techId} onChange={(e) => setTechId(e.target.value)}>{techs.map((t) => <option key={t.id} value={t.id}>{t.name} ({t.id}) · {t.status} · {t.route.filter((r) => !r.locked).length} undeparted task(s)</option>)}</select>
          </label>
        ) : (
          <label className="mt-2 block">Order
            <select className="mt-0.5 w-full rounded border px-1 py-1" value={orderId} onChange={(e) => setOrderId(e.target.value)}>{openOrders.map((o) => <option key={o.id} value={o.id}>{o.id} · {o.effective_priority} · {o.catalog_snapshot.problem_name} · {o.lifecycle_status}{o.technician_name ? ` · ${o.technician_name}` : ''}</option>)}</select>
          </label>
        )}
        {(type === 'technician_unavailable' || type === 'customer_cancel') && <label className="mt-2 block">Reason<input className="mt-0.5 w-full rounded border px-1 py-1" value={reason} onChange={(e) => setReason(e.target.value)} /></label>}
        {type === 'technician_unavailable' && (
          <label className="mt-2 block">Unavailable from now until <span className="text-gray-400">(HH:MM, empty = rest of the day; e.g. 12:30 lets the technician return for a late recovery)</span>
            <input type="time" className="mt-0.5 w-40 rounded border px-1 py-1" value={until} onChange={(e) => setUntil(e.target.value)} />
          </label>
        )}
        {type === 'non_scheduling_complaint' && <label className="mt-2 block">Complaint type<select className="mt-0.5 w-full rounded border px-1 py-1" value={ctype} onChange={(e) => setCtype(e.target.value)}><option>attitude</option><option>quality</option><option>other</option></select></label>}
        {(type === 'lateness_complaint' || type === 'non_scheduling_complaint') && <label className="mt-2 block">Text<textarea className="mt-0.5 w-full rounded border px-1 py-1" rows={2} value={text} onChange={(e) => setText(e.target.value)} /></label>}
        <div className="mt-3 flex gap-2"><button data-testid="event-send" className="btn btn-primary" disabled={busy} onClick={submit}>{busy ? 'processing…' : 'Send event'}</button><button className="btn" onClick={onClose}>Close</button></div>
        {result && <pre className="mt-2 max-h-56 overflow-auto rounded bg-gray-50 p-2 text-[10px]">{result}</pre>}
      </div>
    </div>
  )
}
