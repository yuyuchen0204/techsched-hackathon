import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import L from 'leaflet'
import { api, fmtTime, statusColor } from '../api/client'
import { sessionId } from './Customer'
import type { CustomerOrderDetail, ExpeditePreview, Tracking } from '../types'

/** Independent order page (§7.3): status, actions (cancel / expedite / complaint / reschedule / handoff), human replies,
 *  rating after completion and live tracking after departure. All actions are idempotent on the server. */
export default function CustomerOrder() {
  const { id = '' } = useParams()
  const sid = sessionId()
  const [d, setD] = useState<CustomerOrderDetail | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [complaint, setComplaint] = useState('')
  const [rating, setRating] = useState({ rating: 5, target: 'technician', comment: '', reasons: [] as string[] })
  const [resched, setResched] = useState({ start: '', end: '', reason: '' })
  const [showResched, setShowResched] = useState(false)
  const [preview, setPreview] = useState<ExpeditePreview | null>(null)
  const load = useCallback(() => api.customerOrder(id, sid).then((x) => { setD(x); setErr(null) }).catch((e) => setErr((e as Error).message)), [id, sid])
  useEffect(() => { load(); const t = setInterval(load, 1500); return () => clearInterval(t) }, [load])
  // read-only trial of what an expedite would do now — fetched once per page load, not on every poll (it runs the solver)
  const canExpedite = d?.actions.expedite ?? false
  useEffect(() => { if (canExpedite) api.customerExpeditePreview(id, sid).then(setPreview).catch(() => setPreview(null)) }, [canExpedite, id, sid])
  const run = async (label: string, fn: () => Promise<unknown>, done?: (r: unknown) => string) => {
    setBusy(label); setMsg(null)
    try { const r = await fn(); setMsg(done ? done(r) : `${label}: done`); await load() } catch (e) { setMsg(`${label} failed: ${(e as Error).message}`) } finally { setBusy(null) }
  }
  if (err && !d) return <Shell><div className="p-4 text-sm text-red-700">{err}</div></Shell>
  if (!d) return <Shell><div className="p-4 text-sm text-gray-400">loading…</div></Shell>
  const a = d.actions
  const day = d.window_start.slice(0, 10)
  return (
    <Shell>
      <div className="space-y-3 p-3 text-sm">
        <div>
          <div className="flex items-center gap-1"><span className="font-mono text-xs font-semibold">{d.order_id}</span><span className={`badge ${statusColor[d.lifecycle_status]}`}>{d.lifecycle_status.replace('_', ' ')}</span>{d.paid_expedite && <span className="badge bg-pink-100 text-pink-800">expedited</span>}</div>
          <div className="mt-1 text-base font-semibold">{d.problem}</div>
          <div className="text-xs text-gray-500">about {d.catalog_snapshot.repair_duration_minutes} min on site</div>
        </div>
        <div className="rounded-xl bg-gray-50 p-3 text-xs">
          <div className="grid grid-cols-[76px_1fr] gap-y-1">
            <span className="text-gray-500">Status</span><span data-testid="order-status-text">{d.text}</span>
            <span className="text-gray-500">Window</span><span>{fmtTime(d.window_start)}–{fmtTime(d.window_end)}{d.eta ? ` · planned start ${fmtTime(d.eta)}` : ''}</span>
            <span className="text-gray-500">Address</span><span>{String(d.address_full?.formatted_address ?? d.address ?? '')}{d.address_full?.unit_number ? ` #${String(d.address_full.unit_number)}` : d.address_full?.unit_not_applicable ? ' (no unit)' : ''}</span>
            <span className="text-gray-500">Technician</span><span>{d.technician_name ?? 'not confirmed yet'}</span>
            <span className="text-gray-500">Contact</span><span>{d.customer_name} · {d.contact_phone}</span>
            {d.description && <><span className="text-gray-500">Notes</span><span>{d.description}</span></>}
            {d.expedite && <><span className="text-gray-500">Expedite</span><span data-testid="expedite-summary">{d.expedite.summary}</span></>}
          </div>
          {d.risks.length > 0 && <div className="mt-2 text-amber-800">⚠ {d.risks.map((r) => r.type).join(', ')} — the dispatcher is handling this.</div>}
        </div>
        {d.expedite?.message && (
          <div className="rounded-xl border border-pink-200 bg-pink-50 p-3 text-xs text-pink-900" data-testid="expedite-banner">{d.expedite.message}</div>
        )}
        {d.tracking.active ? <TrackingMap t={d.tracking} /> : <div className="rounded-xl border border-dashed border-gray-200 p-3 text-xs text-gray-500">Live tracking appears once the technician departs ({d.tracking.reason}).</div>}
        {d.progress.length > 0 && (
          <div className="text-xs">
            <div className="mb-1 font-semibold text-gray-700">Progress</div>
            <ol className="space-y-0.5">{d.progress.map((p, i) => <li key={i} className="flex gap-2"><span className="font-mono text-gray-400">{fmtTime(p.at)}</span><span>{p.event.replace('_', ' ')}</span><span className="text-gray-400">· {p.source}</span></li>)}</ol>
          </div>
        )}
        {d.human_case && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs" data-testid="order-human-case">
            <div className="font-semibold text-amber-900">Support case {d.human_case.id} · {d.human_case.status.replace('_', ' ')}{d.human_case.assignee ? ` · ${d.human_case.assignee}` : ''}</div>
            <div className="text-gray-700">{d.human_case.reason_summary}</div>
            {d.human_case.replies.length > 0 && <div className="mt-1 space-y-0.5">{d.human_case.replies.map((r, i) => <div key={i}><span className="font-medium">{r.author}:</span> {r.text} <span className="text-gray-400">{fmtTime(r.at)}</span></div>)}</div>}
            {d.human_case.resolution && <div className="mt-1 text-green-800">Resolved: {d.human_case.resolution}</div>}
          </div>
        )}
        {d.reschedule_requests.length > 0 && <div className="text-xs text-gray-600">Reschedule requests: {d.reschedule_requests.map((r) => `${fmtTime(r.window[0])}–${fmtTime(r.window[1])} (${r.status})`).join(', ')}</div>}
        {a.expedite && (
          <div className="text-[11px] text-gray-500" data-testid="expedite-preview">
            {preview ? preview.text : 'Checking the earliest possible start…'} · payment is simulated, no refund
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          {a.expedite && <button data-testid="act-expedite" className="btn rounded-full" disabled={!!busy} onClick={() => run('Expedite', () => api.customerExpedite(d.order_id, sid), (r) => (r as { message?: string }).message ?? 'Your order is expedited.')}>💳 Expedite (simulated payment)</button>}
          {a.keep_original_time && <button data-testid="act-keep-original" className="btn rounded-full" disabled={!!busy} onClick={() => run('Keep original time', () => api.customerKeepOriginalTime(d.order_id, sid), (r) => (r as { message?: string }).message ?? 'Original time kept.')}>↩ Keep original time</button>}
          {a.cancel && <button data-testid="act-cancel" className="btn btn-danger rounded-full" disabled={!!busy} onClick={() => { if (confirm('Cancel this order?')) run('Cancel', () => api.customerCancel(d.order_id, sid, 'customer app')) }}>Cancel order</button>}
          {a.reschedule && <button className="btn rounded-full" disabled={!!busy} onClick={() => setShowResched((s) => !s)}>🗓 Ask to reschedule</button>}
          {a.handoff && <button data-testid="act-handoff" className="btn rounded-full" disabled={!!busy || Boolean(d.human_case && d.human_case.status !== 'resolved')} onClick={() => run('Talk to a human', () => api.customerHandoff(d.order_id, sid, `customer asked for a human on ${d.order_id}`), () => 'A support agent will pick this up; the assistant is paused')}>🙋 Talk to a human</button>}
        </div>
        {showResched && (
          <form className="space-y-1 rounded-xl border border-gray-200 p-3 text-xs" onSubmit={(e) => { e.preventDefault(); run('Reschedule request', () => api.customerReschedule(d.order_id, sid, { window_start: `${day}T${resched.start}:00+08:00`, window_end: `${day}T${resched.end}:00+08:00`, reason: resched.reason }), () => 'Request sent — a coordinator checks feasibility and replies here') }}>
            <div className="flex gap-1"><input type="time" className="rounded border px-2 py-1" value={resched.start} onChange={(e) => setResched({ ...resched, start: e.target.value })} required /><span className="self-center">to</span><input type="time" className="rounded border px-2 py-1" value={resched.end} onChange={(e) => setResched({ ...resched, end: e.target.value })} required /></div>
            <input className="w-full rounded border px-2 py-1" placeholder="reason (optional)" value={resched.reason} onChange={(e) => setResched({ ...resched, reason: e.target.value })} />
            <button className="btn btn-primary" disabled={!!busy}>Send request</button>
          </form>
        )}
        {a.complain && (
          <form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); if (!complaint.trim()) return; const t = complaint; setComplaint(''); run('Complaint', () => api.customerComplaint(d.order_id, sid, t), (r) => (r as { reply: { text: string } }).reply.text) }}>
            <input data-testid="complaint-input" className="min-w-0 flex-1 rounded-full border px-3 py-1.5 text-xs" placeholder="Complaint, e.g. technician is late / was rude" value={complaint} onChange={(e) => setComplaint(e.target.value)} />
            <button className="btn rounded-full" disabled={!!busy || !complaint.trim()}>Send</button>
          </form>
        )}
        {a.rate && (
          <form className="space-y-1 rounded-xl border border-green-200 bg-green-50 p-3 text-xs" data-testid="rating-form" onSubmit={(e) => { e.preventDefault(); run('Rating', () => api.customerFeedback(d.order_id, sid, rating), () => 'Thanks for rating') }}>
            <div className="font-semibold text-green-900">How was the service?</div>
            <div className="flex gap-1">{[1, 2, 3, 4, 5].map((n) => <button type="button" key={n} className={`text-xl ${n <= rating.rating ? 'text-amber-500' : 'text-gray-300'}`} onClick={() => setRating({ ...rating, rating: n })}>★</button>)}</div>
            <div className="flex gap-2"><label><input type="radio" checked={rating.target === 'technician'} onChange={() => setRating({ ...rating, target: 'technician' })} /> technician</label><label><input type="radio" checked={rating.target === 'dispatch'} onChange={() => setRating({ ...rating, target: 'dispatch' })} /> scheduling</label><label><input type="radio" checked={rating.target === 'platform'} onChange={() => setRating({ ...rating, target: 'platform' })} /> app</label></div>
            {rating.rating <= 2 && <div className="flex flex-wrap gap-1">{['late', 'attitude', 'quality', 'communication'].map((r) => <label key={r} className="rounded-full border px-2 py-0.5"><input type="checkbox" checked={rating.reasons.includes(r)} onChange={(e) => setRating({ ...rating, reasons: e.target.checked ? [...rating.reasons, r] : rating.reasons.filter((x) => x !== r) })} /> {r}</label>)}</div>}
            <input className="w-full rounded border px-2 py-1" placeholder="comment (optional)" value={rating.comment} onChange={(e) => setRating({ ...rating, comment: e.target.value })} />
            <button className="btn btn-primary" disabled={!!busy}>Submit rating</button>
          </form>
        )}
        {d.feedback && <div className="text-xs text-gray-600">Your rating: {'★'.repeat(d.feedback.rating)} ({d.feedback.target}){d.feedback.comment ? ` — ${d.feedback.comment}` : ''}</div>}
        {msg && <div className="rounded-lg bg-gray-100 px-3 py-2 text-xs" data-testid="order-msg">{msg}</div>}
      </div>
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full justify-center bg-gray-200/60">
      <div className="flex h-full w-full max-w-md flex-col overflow-auto bg-white shadow-xl">
        <header className="flex items-center gap-2 border-b border-gray-100 px-3 py-2"><Link to="/customer" className="text-sm text-blue-700">‹ Back</Link><span className="text-sm font-semibold">Order details</span></header>
        {children}
      </div>
    </div>
  )
}

function TrackingMap({ t }: { t: Tracking }) {
  const ref = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const layerRef = useRef<L.LayerGroup | null>(null)
  const vehicleRef = useRef<L.Marker | null>(null)
  const fitted = useRef(false)
  useEffect(() => {
    if (!ref.current || mapRef.current) return
    const map = L.map(ref.current, { center: t.current_coords ?? [1.35, 103.85], zoom: 13, zoomControl: false })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap' }).addTo(map)
    layerRef.current = L.layerGroup().addTo(map)
    const box = map.getContainer()
    map.on('zoomstart viewreset', () => box.classList.add('no-glide'))
    map.on('zoomend', () => setTimeout(() => box.classList.remove('no-glide'), 50))
    mapRef.current = map
    const timer = setTimeout(() => map.invalidateSize(), 50)
    return () => { clearTimeout(timer); map.stop(); map.remove(); mapRef.current = null; vehicleRef.current = null }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    const layer = layerRef.current; const map = mapRef.current
    if (!layer || !map) return
    layer.clearLayers()
    if (t.route_points && t.route_points.length > 1) L.polyline(t.route_points, { color: '#2563eb', weight: 4, opacity: 0.8 }).addTo(layer)
    if (t.destination) L.circleMarker([t.destination.lat, t.destination.lon], { radius: 7, color: '#dc2626', fillColor: '#dc2626', fillOpacity: 0.9 }).bindTooltip('your address').addTo(layer)
    if (t.current_coords) {
      if (vehicleRef.current) vehicleRef.current.setLatLng(t.current_coords)   // glide (CSS transition), never re-created
      else vehicleRef.current = L.marker(t.current_coords, { icon: L.divIcon({ className: 'tech-marker', iconSize: [28, 28], iconAnchor: [14, 14], html: '<div style="width:28px;height:28px;border-radius:50%;background:#4f46e5;color:#fff;font:700 14px/28px system-ui;text-align:center;border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.4)">🚐</div>' }), zIndexOffset: 1000 }).addTo(map)
      if (!fitted.current) {   // fit once; later polls must not re-centre under the customer's finger
        const b = L.latLngBounds([t.current_coords, ...(t.destination ? [[t.destination.lat, t.destination.lon] as [number, number]] : [])])
        map.fitBounds(b.pad(0.4), { animate: false })
        fitted.current = true
      }
    }
  }, [t])
  return (
    <div className="overflow-hidden rounded-xl border border-gray-200" data-testid="tracking-map">
      <div ref={ref} className="h-48 w-full" />
      <div className="px-3 py-2 text-xs"><span className="font-semibold">{t.technician?.name}</span> · {t.technician?.status?.replace('_', ' ').toLowerCase()}{t.moving ? ` · ${Math.round((t.leg_progress ?? 0) * 100)}% of the way` : ''}{t.eta ? ` · ETA ${fmtTime(t.eta)}` : ''}<div className="text-[10px] text-gray-400">{t.source}</div></div>
    </div>
  )
}
