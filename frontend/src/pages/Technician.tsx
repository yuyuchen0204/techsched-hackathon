import { useCallback, useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import { api, fmtTime, minutesOfDay, priorityColor, splitPolyline, statusColor } from '../api/client'
import type { TechJob, TechLogin, TechToday, TechnicianRoute } from '../types'

const KEY = 'techsched_technician_id'
/** What the technician should read at the door: building or street, never the coordinates behind them.
 *  A pin that the geocoder could not name still falls back to a marker rather than a decimal pair. */
function placeName(a: TechJob['address']): string {
  const raw = (a.formatted_address ?? '').trim()
  const isCoordinates = /^(map pin|pinned point)?\s*\(?-?\d+\.\d+\s*,\s*-?\d+\.\d+\)?$/i.test(raw)
  if (raw && !isCoordinates) return raw
  return a.building_name?.trim() || (a.postal_code ? `Singapore ${a.postal_code}` : 'Address pinned on the map — call the customer')
}

const kindCls: Record<string, string> = { travel: 'bg-blue-200', wait: 'bg-gray-200', service: 'bg-violet-400', break: 'bg-cyan-300', unavailable: 'bg-red-300' }

/** Simulated technician app (§7.4): today's timeline, next job with address/unit/history hint, depart→arrive→start→complete,
 *  manual/auto driving, leave, service report and dynamic rest. */
export default function Technician() {
  const [logins, setLogins] = useState<TechLogin[]>([])
  const [id, setId] = useState<string>(() => { try { return localStorage.getItem(KEY) ?? '' } catch { return '' } })
  const [d, setD] = useState<TechToday | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [reportFor, setReportFor] = useState<string | null>(null)
  const [leaveOpen, setLeaveOpen] = useState(false)
  const [route, setRoute] = useState<TechnicianRoute | null>(null)
  useEffect(() => { api.techLogins().then((l) => { setLogins(l); if (!id && l[0]) setId(l[0].id) }).catch((e) => setErr((e as Error).message)) }, [id])
  useEffect(() => { try { if (id) localStorage.setItem(KEY, id) } catch { /* ignore */ } }, [id])
  const load = useCallback(() => { if (!id) return; api.techToday(id).then((x) => { setD(x); setErr(null) }).catch((e) => setErr((e as Error).message)) }, [id])
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t) }, [load])
  useEffect(() => {
    const onStorage = (e: StorageEvent) => { if (e.key === 'techsched_demo_reset') { setD(null); setMsg('Demo was reset'); setReportFor(null); setLeaveOpen(false); load() } }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [load])
  const routeKey = d?.jobs.map((j) => `${j.order_id}@${j.planned.departure}`).join(',') ?? ''
  useEffect(() => { if (!id) return; api.technicianRoute(id).then(setRoute).catch(() => setRoute(null)) }, [id, routeKey])
  const run = async (label: string, fn: () => Promise<unknown>, done?: (r: unknown) => string) => {
    setBusy(label); setMsg(null)
    try { const r = await fn(); setMsg(done ? done(r) : `${label}: done`); await load() } catch (e) { setMsg(`${label} failed: ${(e as Error).message}`) } finally { setBusy(null) }
  }
  const t = d?.technician
  const manual = t?.sim_mode === 'manual'
  const focus = d?.current_job ?? d?.next_job ?? null
  return (
    <div className="flex h-full flex-col items-center gap-2 overflow-auto bg-gray-200/60 py-2 lg:flex-row lg:items-start lg:justify-center">
      {d && (
        <div className="order-2 w-full max-w-md shrink-0 rounded-xl border border-dashed border-gray-400 bg-white/70 px-3 py-2 text-xs lg:order-1 lg:mt-2 lg:w-56" data-testid="sim-mode-panel">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">simulator control · not part of the app</div>
          <div className="mt-0.5 font-semibold">{manual ? 'Manual mode' : 'Auto mode'}</div>
          <div className="text-gray-500">{d.mode_note}</div>
          <button data-testid="mode-toggle" className="btn mt-1 w-full justify-center" disabled={!!busy} onClick={() => run('Mode', () => api.techMode(id, manual ? 'auto' : 'manual'), () => manual ? 'Simulator drives this technician again' : 'You drive this technician now')}>{manual ? 'Hand back to simulator' : 'Take manual control'}</button>
        </div>
      )}
      <div className="order-1 flex h-full min-h-0 w-full max-w-md flex-col overflow-auto rounded-xl bg-white shadow-xl lg:order-2">
        <header className="flex items-center gap-2 border-b border-gray-100 px-3 py-2">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold">Technician app <span className="font-normal text-gray-400">· simulated</span></div>
            <div className="text-[11px] text-gray-500">{t ? `${t.name} · ${t.status} · shift ${fmtTime(t.shift[0])}–${fmtTime(t.shift[1])}` : '—'} · sim {fmtTime(d?.sim_now)}</div>
          </div>
          <select data-testid="tech-login" className="max-w-[140px] rounded-lg border px-2 py-1 text-xs" value={id} onChange={(e) => { setD(null); setId(e.target.value) }}>
            {logins.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </header>
        {err && <div className="bg-red-50 px-3 py-1 text-xs text-red-700">{err}</div>}
        {!d ? <div className="p-4 text-sm text-gray-400">loading…</div> : (
          <div className="space-y-3 p-3 text-sm">
            <DayBar d={d} />
            {(d.notices ?? []).length > 0 && (
              <div className="rounded-xl border border-pink-200 bg-pink-50 p-2 text-xs text-pink-900" data-testid="tech-notices">
                {(d.notices ?? []).map((n) => <div key={n.id}><span className="font-mono text-[10px] text-pink-700">{fmtTime(n.at)}</span> {n.message}</div>)}
              </div>
            )}
            {focus ? <JobCard j={focus} tech={id} manual={manual} busy={busy} run={run} onReport={() => setReportFor(focus.order_id)} isCurrent={Boolean(d.current_job)} simNow={d.sim_now} /> : <div className="rounded-xl border border-dashed p-3 text-xs text-gray-500">No further jobs today.</div>}
            <RouteMap d={d} route={route} />
            <div>
              <div className="mb-1 text-xs font-semibold text-gray-700">All jobs today ({d.jobs.length})</div>
              <div className="space-y-1">
                {d.jobs.map((j) => (
                  <div key={j.order_id} className={`rounded-lg border px-3 py-1.5 text-xs ${focus?.order_id === j.order_id ? 'border-blue-300 bg-blue-50' : 'border-gray-200'}`}>
                    <div className="flex items-center gap-1"><span className="font-mono">{fmtTime(j.window[0])}–{fmtTime(j.window[1])}</span><span className={`badge ${priorityColor[j.priority]}`}>{j.priority}</span><span className={`badge ${statusColor[j.lifecycle_status]}`}>{j.lifecycle_status.replace('_', ' ')}</span><span className="ml-auto text-gray-400">{j.duration_minutes} min on site</span></div>
                    <div className="truncate">{j.problem}</div>
                    <div className="truncate text-gray-500">{placeName(j.address)}{j.address.postal_code ? ` · S${j.address.postal_code}` : ''}{j.address.unit_number ? ` #${j.address.unit_number}` : ''}</div>
                    <div className="text-gray-400">≈{Math.max(5, Math.round(j.planned.travel_minutes / 5) * 5)} min drive · estimate, no live traffic</div>
                    {j.lifecycle_status === 'COMPLETED' && j.report_status === 'pending' && <button className="mt-1 text-blue-700 hover:underline" onClick={() => setReportFor(j.order_id)}>fill service report</button>}
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-xl border border-gray-200 p-3 text-xs">
              <div className="flex items-center justify-between"><span className="font-semibold">Rest</span><span className={`badge ${d.break_facts.level === 'escalate' ? 'bg-red-100 text-red-800' : d.break_facts.level === 'evaluate' ? 'bg-amber-100 text-amber-800' : 'bg-gray-100 text-gray-600'}`}>{d.break_facts.in_break ? 'on break' : d.break_facts.level}</span></div>
              <div className="text-gray-600">{d.break_facts.work_minutes_since_break} min worked since the last break{d.break_facts.last_break_end ? ` (ended ${fmtTime(d.break_facts.last_break_end)})` : ''}{d.break_facts.planned_break_ahead.length > 0 ? ` · planned ${d.break_facts.planned_break_ahead.map((b) => `${fmtTime(b.start)}–${fmtTime(b.end)} (${b.status})`).join(', ')}` : ' · no break planned yet'}</div>
              <div className="mt-1 text-[11px] text-gray-500">Breaks are arranged dynamically after long stretches of work; you can also declare one now (the schedule is re-checked, later jobs may move).</div>
              <button className="btn mt-1" disabled={!!busy || Boolean(d.break_facts.in_break)} onClick={() => run('Break', () => api.techBreak(id, 30, 'declared by technician'), (r) => String((r as { note?: string }).note ?? 'break recorded'))}>☕ Take a 30-min break now</button>
            </div>
            <div className="rounded-xl border border-gray-200 p-3 text-xs">
              <div className="flex items-center justify-between"><span className="font-semibold">Leave / unavailable</span><button data-testid="leave-open" className="btn" onClick={() => setLeaveOpen((s) => !s)}>{leaveOpen ? 'close' : 'report'}</button></div>
              {leaveOpen && <LeaveForm tech={id} simNow={d.sim_now} busy={busy} run={run} />}
            </div>
            {msg && <div className="rounded-lg bg-gray-100 px-3 py-2 text-xs" data-testid="tech-msg">{msg}</div>}
          </div>
        )}
        {reportFor && d && <ReportModal tech={id} job={d.jobs.find((j) => j.order_id === reportFor)!} busy={busy} run={run} onClose={() => setReportFor(null)} />}
      </div>
    </div>
  )
}

function DayBar({ d }: { d: TechToday }) {
  const s = minutesOfDay(d.technician.shift[0]), e = minutesOfDay(d.technician.shift[1]), now = minutesOfDay(d.sim_now)
  const pct = (m: number) => `${Math.max(0, Math.min(100, ((m - s) / (e - s)) * 100))}%`
  return (
    <div className="text-xs">
      <div className="relative h-5 w-full overflow-hidden rounded bg-gray-100">
        {d.timeline.map((x, i) => <div key={i} className={`absolute top-0 h-full ${kindCls[x.kind] ?? 'bg-gray-300'}`} style={{ left: pct(minutesOfDay(x.start)), width: `calc(${pct(minutesOfDay(x.end))} - ${pct(minutesOfDay(x.start))})` }} title={`${x.kind} ${fmtTime(x.start)}–${fmtTime(x.end)}${x.order_id ? ' ' + x.order_id : ''}`} />)}
        <div className="absolute top-0 h-full w-0.5 bg-red-600" style={{ left: pct(now) }} />
      </div>
      <div className="mt-0.5 flex justify-between text-[10px] text-gray-400"><span>{fmtTime(d.technician.shift[0])}</span><span>travel · service · break · unavailable</span><span>{fmtTime(d.technician.shift[1])}</span></div>
    </div>
  )
}

function JobCard({ j, tech, manual, busy, run, onReport, isCurrent, simNow }: { j: TechJob; tech: string; manual: boolean; busy: string | null; run: (l: string, f: () => Promise<unknown>, d?: (r: unknown) => string) => Promise<void>; onReport: () => void; isCurrent: boolean; simNow: string }) {
  const next = j.next_action as 'depart' | 'arrive' | 'start' | 'complete' | null
  const labels: Record<string, string> = { depart: '🚐 Depart now', arrive: '📍 Arrived', start: '🔧 Start work', complete: '✅ Complete' }
  // leaving early rewrites the plan and locks the task, so the backend refuses it; mirror that here instead of
  // letting the technician tap a button that can only fail
  const minutesEarly = next === 'depart' ? minutesOfDay(j.planned.departure) - minutesOfDay(simNow) : 0
  const tooEarly = minutesEarly > 0
  const blocked = !manual || tooEarly
  return (
    <div className="rounded-2xl border-2 border-blue-500 p-3" data-testid="job-card">
      <div className="flex items-center gap-1 text-xs"><span className="font-semibold uppercase tracking-wide text-blue-700">{isCurrent ? 'Current job' : 'Next job'}</span><span className="font-mono text-gray-400">{j.order_id}</span><span className={`badge ${priorityColor[j.priority]}`}>{j.priority}</span><span className={`badge ${statusColor[j.lifecycle_status]}`}>{j.lifecycle_status.replace('_', ' ')}</span></div>
      <div className="mt-1 text-base font-semibold">{j.problem}</div>
      <div className="text-xs text-gray-600">Window {fmtTime(j.window[0])}–{fmtTime(j.window[1])} · about {j.duration_minutes} min on site</div>
      <div className="mt-2 rounded-lg bg-gray-50 px-3 py-2 text-xs">
        <div className="font-medium">{placeName(j.address)}</div>
        <div className="text-gray-700">{j.address.postal_code ? `Singapore ${j.address.postal_code} · ` : ''}Unit <span className="font-semibold">{j.address.unit_number ? `#${j.address.unit_number}` : j.address.unit_not_applicable ? 'none (landed / shop)' : <span className="text-amber-700">not provided — ask dispatcher/customer</span>}</span></div>
        <div className="text-gray-700">{j.customer_name} · {j.contact_phone}</div>
        {j.description && <div className="mt-1 text-gray-600">“{j.description}”</div>}
      </div>
      {j.history_hint && <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" data-testid="history-hint">🕘 {j.history_hint.wording}{j.history_hint.records.length > 0 && <div className="mt-0.5 text-[11px] text-amber-800">{j.history_hint.records.map((r) => `${r.date.slice(0, 10)}: ${r.problem}${r.actual_problem ? ` → ${r.actual_problem}` : ''}${r.resolution ? ` (${r.resolution})` : ''}`).join(' · ')}</div>}</div>}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {next && <button data-testid={`act-${next}`} className="btn btn-primary rounded-full" disabled={!!busy || blocked} title={!manual ? 'switch to manual mode to drive this job yourself' : tooEarly ? `departure is planned for ${fmtTime(j.planned.departure)}` : ''} onClick={() => run(labels[next], () => api.techAction(tech, j.order_id, next), (r) => { const x = r as { anomalies?: string[] }; return `${labels[next]} recorded at sim time${x.anomalies?.length ? ` · noted: ${x.anomalies.join(', ')}` : ''}` })}>{labels[next]}</button>}
        {!manual && next && <span className="self-center text-[11px] text-gray-500">auto mode: the simulator performs these steps</span>}
        {manual && tooEarly && <span data-testid="depart-not-yet" className="self-center text-[11px] text-amber-700">not yet — leave at {fmtTime(j.planned.departure)} ({minutesEarly} min)</span>}
        {j.lifecycle_status === 'COMPLETED' && j.report_status === 'pending' && <button data-testid="act-report" className="btn rounded-full" onClick={onReport}>📝 Service report</button>}
        {j.report && j.report.status === 'complete' && <span className="self-center text-[11px] text-green-700">report filed · {j.report.resolution}</span>}
      </div>
    </div>
  )
}

function LeaveForm({ tech, simNow, busy, run }: { tech: string; simNow: string; busy: string | null; run: (l: string, f: () => Promise<unknown>, d?: (r: unknown) => string) => Promise<void> }) {
  const [reason, setReason] = useState('unwell')
  const [until, setUntil] = useState('')
  const key = useRef(`leave-${tech}-${Date.now()}`)
  return (
    <form className="mt-2 space-y-1" onSubmit={(e) => { e.preventDefault(); run('Leave', () => api.techLeave(tech, { reason, end: until ? `${simNow.slice(0, 10)}T${until}:00+08:00` : undefined, idempotency_key: key.current }), (r) => { const x = r as { affected?: { released_from?: string }[]; interrupted?: unknown[]; note?: string }; const rel = x.affected ?? []; const onRoad = rel.filter((a) => a.released_from === 'EN_ROUTE').length
      return `Leave recorded · ${rel.length} job(s) released for recovery${onRoad ? ` (${onRoad} you were driving to — being re-sent now)` : ''}${x.interrupted?.length ? ` · ${x.interrupted.length} on-site job(s) sent to a human` : ''}` }) }}>
      <input className="w-full rounded border px-2 py-1" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="reason" />
      <div className="flex items-center gap-1">until <input type="time" className="rounded border px-2 py-1" value={until} onChange={(e) => setUntil(e.target.value)} /><span className="text-gray-400">(empty = rest of day)</span></div>
      <button data-testid="leave-send" className="btn btn-danger" disabled={!!busy}>Report unavailable from now</button>
    </form>
  )
}

function ReportModal({ tech, job, busy, run, onClose }: { tech: string; job: TechJob; busy: string | null; run: (l: string, f: () => Promise<unknown>, d?: (r: unknown) => string) => Promise<void>; onClose: () => void }) {
  const [f, setF] = useState({ actual_problem_text: job.report?.actual_problem_text ?? '', resolution: (job.report?.resolution || 'fixed') as string, notes: '', interruption_minutes: 0, anomaly_flags: [] as string[] })
  return (
    <div className="fixed inset-0 z-[700] flex items-end justify-center bg-black/40 sm:items-center" onClick={onClose}>
      <form className="w-full max-w-md space-y-2 rounded-t-2xl bg-white p-4 text-xs shadow-xl sm:rounded-2xl" data-testid="report-modal" onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => { e.preventDefault(); run('Service report', () => api.techReport(tech, job.order_id, f), () => 'Report filed; actual duration recorded for the shadow prediction').then(onClose) }}>
        <div className="text-sm font-semibold">Service report · {job.order_id}</div>
        <div className="text-gray-500">Actual time on site: {fmtTime(job.actual.service_started_at)}–{fmtTime(job.actual.completed_at)} (from your start/complete taps)</div>
        <label className="block">What was actually wrong?<input className="mt-0.5 w-full rounded border px-2 py-1" value={f.actual_problem_text} onChange={(e) => setF({ ...f, actual_problem_text: e.target.value })} placeholder={job.problem} /></label>
        <label className="block">Resolution<select className="mt-0.5 w-full rounded border px-2 py-1" value={f.resolution} onChange={(e) => setF({ ...f, resolution: e.target.value })}><option value="fixed">fixed</option><option value="partial">partial</option><option value="needs_parts">needs parts</option><option value="not_fixed">not fixed</option></select></label>
        <label className="block">Interruptions (minutes not working, e.g. waiting for the customer)<input type="number" min={0} className="mt-0.5 w-24 rounded border px-2 py-1" value={f.interruption_minutes} onChange={(e) => setF({ ...f, interruption_minutes: Number(e.target.value) })} /></label>
        <div className="flex flex-wrap gap-1">{['customer_absent', 'wrong_address', 'parts_missing', 'unsafe_site'].map((x) => <label key={x} className="rounded-full border px-2 py-0.5"><input type="checkbox" checked={f.anomaly_flags.includes(x)} onChange={(e) => setF({ ...f, anomaly_flags: e.target.checked ? [...f.anomaly_flags, x] : f.anomaly_flags.filter((y) => y !== x) })} /> {x.replace('_', ' ')}</label>)}</div>
        <label className="block">Notes<textarea className="mt-0.5 w-full rounded border px-2 py-1" rows={2} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
        <div className="flex gap-2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button data-testid="report-submit" className="btn btn-primary flex-1 justify-center" disabled={!!busy}>Submit report</button></div>
      </form>
    </div>
  )
}

function RouteMap({ d, route }: { d: TechToday; route: TechnicianRoute | null }) {
  const ref = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const layerRef = useRef<L.LayerGroup | null>(null)
  useEffect(() => {
    if (!ref.current || mapRef.current) return
    const map = L.map(ref.current, { center: [1.35, 103.85], zoom: 11, zoomControl: false })
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { attribution: '© OpenStreetMap' }).addTo(map)
    layerRef.current = L.layerGroup().addTo(map)
    mapRef.current = map
    const t = setTimeout(() => map.invalidateSize(), 50)
    return () => { clearTimeout(t); map.stop(); map.remove(); mapRef.current = null }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    const layer = layerRef.current; const map = mapRef.current
    if (!layer || !map) return
    layer.clearLayers()
    const pts: [number, number][] = []
    for (const j of d.jobs) {
      if (['COMPLETED', 'CANCELLED'].includes(j.lifecycle_status)) continue
      pts.push([j.address.lat, j.address.lon])
      L.circleMarker([j.address.lat, j.address.lon], { radius: 6, color: '#fff', weight: 1.5, fillColor: { P0: '#dc2626', P1: '#f97316', P2: '#f59e0b', P3: '#3b82f6' }[j.priority], fillOpacity: 0.9 }).bindTooltip(`${fmtTime(j.planned.service_start)} ${j.problem}`).addTo(layer)
    }
    // the leg being driven shrinks as the day advances, using the same fraction the backend derives for the map markers
    const driving = d.jobs.find((j) => j.lifecycle_status === 'EN_ROUTE')
    const legProgress = driving
      ? Math.max(0, Math.min(1, (minutesOfDay(d.sim_now) - minutesOfDay(driving.planned.departure)) /
          Math.max(1, minutesOfDay(driving.planned.arrival) - minutesOfDay(driving.planned.departure))))
      : null
    if (route) for (const leg of route.legs) {
      const style = { color: leg.locked ? '#7c3aed' : '#2563eb', weight: 3, opacity: 0.8, dashArray: leg.schematic ? '6 4' : undefined }
      if (driving && legProgress !== null && leg.order_id === driving.order_id) {
        const [done, left] = splitPolyline(leg.points, legProgress)
        if (done.length > 1) L.polyline(done, { ...style, color: '#9ca3af', opacity: 0.45, dashArray: '4 5' }).addTo(layer)
        if (left.length > 1) L.polyline(left, style).addTo(layer)
      } else {
        L.polyline(leg.points, style).addTo(layer)
      }
    }
    if (pts.length > 0) map.fitBounds(L.latLngBounds(pts).pad(0.3), { animate: false })
  }, [d, route])
  return <div className="overflow-hidden rounded-xl border border-gray-200"><div ref={ref} className="h-44 w-full" /><div className="px-3 py-1 text-[10px] text-gray-400">route: {route ? `${route.route_provider}${route.degraded ? ' (degraded)' : ''}` : 'loading'} · remaining jobs only · grey = already driven</div></div>
}
