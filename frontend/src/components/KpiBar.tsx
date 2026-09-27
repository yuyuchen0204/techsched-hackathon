import { useState } from 'react'
import { fmtTime } from '../api/client'
import type { Clock, Schedule } from '../types'

interface Extra { pendingHuman: number; availableTechs: number; riskOrders: number }

/** Simulation clock + four dispatcher KPIs (§13.2); the engineering counters stay one click away.
 *  The clock lives here rather than in the very top row, which screen recordings cut off. */
export default function KpiBar({ schedule, extra, clock }: { schedule: Schedule | null; extra: Extra; clock: Clock | null }) {
  const [more, setMore] = useState(false)
  const k = schedule?.kpis
  const pending = k ? k.unassigned + k.pending_review_plans : null
  const main: [string, string | number, string, string][] = [
    ['Pending orders', pending ?? '–', pending ? 'text-amber-600' : '', 'unassigned + awaiting dispatcher approval'],
    ['Orders at risk', extra.riskOrders, extra.riskOrders > 0 ? 'text-red-600' : '', 'orders with an active risk (overdue, late ETA, technician gone)'],
    ['Pending human', extra.pendingHuman, extra.pendingHuman > 0 ? 'text-red-600' : '', 'open human cases (customer request · policy · agent escalation)'],
    ['Available technicians', extra.availableTechs, '', 'on shift, not unavailable'],
  ]
  const moreItems: [string, string | number][] = k ? [['Open orders', k.orders_open], ['Assigned', k.assigned], ['Unassigned', k.unassigned], ['Urgent (P0/P1)', k.urgent_orders], ['Active risks', k.active_risks], ['Manual queue', k.manual_queue], ['Total travel', `${k.total_travel_minutes} min`], ['Completed', k.completed], ['Cancelled', k.cancelled]] : []
  return (
    <div className="flex flex-wrap items-stretch gap-2 px-3 py-2">
      <div className="panel flex min-w-[150px] flex-col justify-center px-3 py-1.5" data-testid="sim-clock">
        <div className="flex items-baseline gap-1.5">
          <span className="font-mono text-2xl font-bold tabular-nums leading-none">{clock ? fmtTime(clock.now) : '--:--'}</span>
          <span className={`badge ${clock?.running ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>{clock?.running ? 'RUNNING' : 'PAUSED'}</span>
        </div>
        <div className="text-[10px] text-gray-500">{clock ? clock.now.slice(0, 10) : '----'} · {clock?.timezone ?? ''}</div>
      </div>
      {main.map(([label, value, cls, title]) => (
        <div key={label} className="panel min-w-[120px] px-3 py-1.5" title={title} data-testid={`kpi-${label.toLowerCase().replace(/[^a-z]+/g, '-')}`}>
          <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
          <div className={`text-xl font-semibold tabular-nums ${cls}`}>{value}</div>
        </div>
      ))}
      <button className="panel px-3 py-1.5 text-left text-[11px] text-gray-500 hover:bg-gray-50" onClick={() => setMore((m) => !m)}>
        schedule v{schedule?.version?.id ?? '–'} · route {schedule?.route_snapshot.provider ?? '–'}{schedule?.route_snapshot.degraded ? ' (DEGRADED)' : ''}
        <div className="text-blue-700">{more ? 'hide counters' : 'more counters'}</div>
      </button>
      {more && moreItems.map(([label, value]) => (
        <div key={label} className="panel min-w-[84px] px-2 py-1"><div className="text-[10px] uppercase tracking-wide text-gray-400">{label}</div><div className="text-sm font-semibold tabular-nums text-gray-700">{value}</div></div>
      ))}
    </div>
  )
}
