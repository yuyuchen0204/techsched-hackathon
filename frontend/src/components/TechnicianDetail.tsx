import { fmtTime, minutesOfDay, priorityColor, statusColor } from '../api/client'
import type { LocationRef, Order, Technician } from '../types'

interface Props { tech: Technician; orders: Order[]; locations: LocationRef[]; onClose: () => void; onSelectOrder: (id: string) => void; onInjectUnavailable: (techId: string) => void }

const techStatusCls: Record<string, string> = { AVAILABLE: 'bg-green-100 text-green-800', EN_ROUTE: 'bg-indigo-100 text-indigo-800', ARRIVED: 'bg-violet-100 text-violet-800', BUSY: 'bg-purple-100 text-purple-800', UNAVAILABLE: 'bg-red-100 text-red-800', OFF_SHIFT: 'bg-gray-200 text-gray-600' }

export function SkillBar({ level }: { level: number }) {
  return <span className="inline-flex gap-0.5 align-middle" title={`level ${level} of 5`}>{[1, 2, 3, 4, 5].map((i) => <span key={i} className={`h-2.5 w-2.5 rounded-sm ${i <= level ? 'bg-blue-600' : 'bg-gray-200'}`} />)}</span>
}

export default function TechnicianDetail({ tech, orders, locations, onClose, onSelectOrder, onInjectUnavailable }: Props) {
  const orderById = new Map(orders.map((o) => [o.id, o]))
  const locName = (id: string) => locations.find((l) => l.id === id)?.name ?? id
  const shiftLen = minutesOfDay(tech.shift_end) - minutesOfDay(tech.shift_start)
  const travel = tech.route.reduce((a, r) => a + r.travel_minutes, 0)
  const wait = tech.route.reduce((a, r) => a + r.waiting_minutes, 0)
  const service = tech.route.reduce((a, r) => a + (minutesOfDay(r.service_end) - minutesOfDay(r.service_start)), 0)
  const done = orders.filter((o) => o.technician_id === tech.id && o.lifecycle_status === 'COMPLETED')
  const util = shiftLen ? Math.round(((travel + wait + service) / shiftLen) * 100) : 0
  const current = tech.route.find((r) => r.locked)
  return (
    <div className="fixed inset-y-0 right-0 z-[600] flex w-[460px] max-w-full flex-col border-l border-gray-200 bg-white shadow-xl">
      <div className="flex items-center justify-between border-b border-gray-100 px-3 py-1.5"><span className="text-xs font-semibold uppercase text-gray-500">Technician</span><button className="btn" onClick={onClose}>close</button></div>
      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3 text-xs">
        <div>
          <div className="flex items-center gap-2"><span className="text-base font-bold">{tech.name}</span><span className="font-mono text-gray-400">{tech.id}</span><span className={`badge ${techStatusCls[tech.status] ?? 'bg-gray-100'}`}>{tech.status}</span></div>
          <div className="mt-1 text-gray-600">{current ? <>Now: <button className="font-mono text-blue-700 hover:underline" onClick={() => onSelectOrder(current.order_id)}>{current.order_id}</button> ({orderById.get(current.order_id)?.lifecycle_status ?? current.lifecycle_status}) — {orderById.get(current.order_id)?.catalog_snapshot.problem_name}</> : 'No task in execution right now.'}</div>
        </div>

        <Section title="Skills (trade → level; a job needs level ≥ its complexity)">
          <table className="w-full"><tbody>
            {Object.entries(tech.skills).sort((a, b) => b[1] - a[1]).map(([trade, level]) => (
              <tr key={trade} className="border-t border-gray-50"><td className="py-1 pr-2 text-gray-800">{trade}</td><td className="py-1 pr-2"><SkillBar level={level} /></td><td className="py-1 text-right font-mono text-gray-500">L{level}</td></tr>
            ))}
          </tbody></table>
          {tech.certifications.length > 0 && <div className="mt-1 text-gray-500">Certifications: {tech.certifications.join(', ')}</div>}
        </Section>

        <Section title="Shift & availability (simulated company rules)">
          <KV k="Shift" v={`${fmtTime(tech.shift_start)}–${fmtTime(tech.shift_end)} (${shiftLen} min)`} />
          <KV k="Breaks" v={tech.breaks.length ? tech.breaks.map((b) => `${fmtTime(b.start)}–${fmtTime(b.end)}`).join(', ') : 'none'} />
          <KV k="Unavailable" v={tech.unavailable_intervals.length ? tech.unavailable_intervals.map((u) => `${fmtTime(u.start)}–${fmtTime(u.end)}${u.reason ? ` (${u.reason})` : ''}`).join('; ') : 'none'} cls={tech.unavailable_intervals.length ? 'text-red-700' : ''} />
          <KV k="Home base" v={locName(tech.home_location_id)} />
          <KV k="Next free from" v={`${tech.anchor.location_name} at ${fmtTime(tech.anchor.time)}`} hint="anchor: where and when the next movable task can depart" />
        </Section>

        <Section title="Today's load">
          <div className="grid grid-cols-4 gap-2">
            <Stat label="Planned tasks" v={String(tech.route.filter((r) => !r.locked).length)} />
            <Stat label="Travel" v={`${travel} min`} />
            <Stat label="Service" v={`${service} min`} />
            <Stat label="Utilisation" v={`${util}%`} hint="(travel + wait + service) / shift" />
          </div>
          <div className="mt-1 text-[10px] text-gray-400">{done.length} completed today · wait {wait} min</div>
        </Section>

        <Section title="Route (in order)">
          {tech.route.length === 0 && <div className="text-gray-400">no tasks assigned</div>}
          <table className="w-full table-fixed"><colgroup><col className="w-[86px]" /><col /><col className="w-[52px]" /><col className="w-[92px]" /><col className="w-[64px]" /></colgroup><thead className="text-[10px] text-gray-400"><tr><th className="text-left font-normal">order</th><th className="text-left font-normal">problem</th><th className="text-left font-normal">depart</th><th className="text-left font-normal">start–end</th><th className="text-right font-normal">travel</th></tr></thead>
            <tbody>{tech.route.map((r) => {
              const o = orderById.get(r.order_id)
              return (
                <tr key={r.order_id} className="border-t border-gray-50 hover:bg-blue-50">
                  <td className="whitespace-nowrap py-1"><button className="font-mono text-blue-700 hover:underline" onClick={() => onSelectOrder(r.order_id)}>{r.order_id.replace('wo_', '').slice(0, 6)}</button>{r.locked && '🔒'} <span className={`badge ${priorityColor[o?.effective_priority ?? r.priority]}`}>{o?.effective_priority ?? r.priority}</span></td>
                  <td className="truncate py-1 pr-1 text-gray-700">{o?.catalog_snapshot.problem_name ?? ''}</td>
                  <td className="whitespace-nowrap py-1 font-mono">{fmtTime(r.departure)}</td>
                  <td className="whitespace-nowrap py-1 font-mono">{fmtTime(r.service_start)}–{fmtTime(r.service_end)}</td>
                  <td className="whitespace-nowrap py-1 text-right font-mono text-gray-500">{r.travel_minutes}m{r.waiting_minutes ? ` +${r.waiting_minutes}w` : ''}</td>
                </tr>)
            })}</tbody></table>
          {done.length > 0 && <div className="mt-1 text-[10px] text-gray-400">Completed: {done.map((o) => <button key={o.id} className="mr-1 font-mono text-blue-700 hover:underline" onClick={() => onSelectOrder(o.id)}>{o.id.replace('wo_', '').slice(0, 6)}</button>)}</div>}
        </Section>

        <div className="flex gap-1">
          <button className="btn" onClick={() => onInjectUnavailable(tech.id)}>Report unavailable…</button>
          <span className="self-center text-[10px] text-gray-400">opens the event injector preset to this technician</span>
        </div>
        <div className="text-[10px] text-gray-400">Status legend: <span className={`badge ${statusColor.OPEN}`}>OPEN</span> = not departed · EN ROUTE / ARRIVED / IN PROGRESS = locked (cannot be re-assigned) · UNAVAILABLE = reported absent</div>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <div><div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400">{title}</div>{children}</div>
}
function KV({ k, v, hint, cls }: { k: string; v: string; hint?: string; cls?: string }) {
  return <div className="grid grid-cols-[110px_1fr] gap-1"><span className="text-gray-500">{k}</span><span className={`text-gray-800 ${cls ?? ''}`} title={hint}>{v}</span></div>
}
function Stat({ label, v, hint }: { label: string; v: string; hint?: string }) {
  return <div className="rounded border border-gray-100 bg-gray-50 px-2 py-1" title={hint}><div className="text-[10px] uppercase text-gray-400">{label}</div><div className="font-mono text-sm">{v}</div></div>
}
