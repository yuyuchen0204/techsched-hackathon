import { useState } from 'react'
import { fmtTime, priorityColor, statusColor } from '../api/client'
import type { Order } from '../types'

export const shortId = (id: string) => (id.startsWith('wo_') ? id.slice(3) : id).replace(/^([0-9a-f]{10})$/, (m) => m.slice(0, 6))

export default function OrdersTable({ orders, selected, onSelect }: { orders: Order[]; selected: string | null; onSelect: (id: string) => void }) {
  const [pri, setPri] = useState('')
  const [st, setSt] = useState('')
  const [q, setQ] = useState('')
  const rows = orders.filter((o) => (!pri || o.effective_priority === pri) && (!st || o.lifecycle_status === st || o.scheduling_status === st)
    && (!q || `${o.id} ${o.customer_name} ${o.catalog_snapshot.trade_type} ${o.catalog_snapshot.problem_name} ${o.location.name}`.toLowerCase().includes(q.toLowerCase())))
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-xs">
        <input className="w-56 rounded border border-gray-300 px-2 py-1" placeholder="search id / customer / problem / place" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="rounded border border-gray-300 px-2 py-1" value={pri} onChange={(e) => setPri(e.target.value)}>
          <option value="">all priorities</option>{['P0', 'P1', 'P2', 'P3'].map((p) => <option key={p}>{p}</option>)}
        </select>
        <select className="rounded border border-gray-300 px-2 py-1" value={st} onChange={(e) => setSt(e.target.value)}>
          <option value="">all statuses</option>
          {['OPEN', 'EN_ROUTE', 'ARRIVED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED', 'UNASSIGNED', 'PENDING_REVIEW', 'ASSIGNED', 'UNRESOLVED'].map((s) => <option key={s}>{s}</option>)}
        </select>
        <span className="ml-auto text-gray-500">{rows.length} of {orders.length}</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full table-fixed text-xs">
          <colgroup>
            <col className="w-[98px]" /><col className="w-[44px]" /><col /><col className="w-[150px]" /><col className="w-[104px]" /><col className="w-[150px]" /><col className="w-[190px]" />
          </colgroup>
          <thead className="sticky top-0 z-10 bg-gray-50 text-left text-[11px] uppercase tracking-wide text-gray-500">
            <tr><th className="px-3 py-1.5">Order</th><th>Pri</th><th>Problem</th><th>Location</th><th>Window</th><th>Technician · start</th><th className="pr-3">Status</th></tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400">no orders match</td></tr>}
            {rows.map((o, i) => (
              <tr key={o.id} data-testid="order-row" data-order={o.id} data-status={o.lifecycle_status} data-priority={o.effective_priority} onClick={() => onSelect(o.id)}
                className={`cursor-pointer border-t border-gray-100 hover:bg-blue-50 ${selected === o.id ? 'bg-blue-50' : i % 2 ? 'bg-gray-50/40' : ''}`}>
                <td className="whitespace-nowrap px-3 py-1.5 font-mono" title={o.id}>{shortId(o.id)}{o.paid_expedite && <span className="ml-1" title="paid expedite (simulated)">💳</span>}</td>
                <td><span className={`badge ${priorityColor[o.effective_priority]}`}>{o.effective_priority}</span></td>
                <td className="truncate pr-2" title={`${o.catalog_snapshot.trade_type} – ${o.catalog_snapshot.problem_name}`}>
                  <span className="text-gray-900">{o.catalog_snapshot.problem_name}</span>
                  <span className="ml-1 text-[10px] text-gray-400">{o.catalog_snapshot.trade_type} · L{o.catalog_snapshot.complexity_level} · {o.catalog_snapshot.repair_duration_minutes}m</span>
                </td>
                <td className="truncate pr-2 text-gray-600" title={o.location.name}>{o.location.id.startsWith('pt_') ? '📍 ' : ''}{o.location.name}</td>
                <td className="whitespace-nowrap font-mono text-gray-700">{fmtTime(o.window_start)}–{fmtTime(o.window_end)}</td>
                <td className="truncate pr-2">{o.assignment ? <><span className="text-gray-900">{o.technician_name}</span><span className="ml-1 font-mono text-gray-500">@ {fmtTime(o.assignment.service_start)}</span></> : <span className="text-gray-400">unassigned</span>}</td>
                <td className="whitespace-nowrap pr-3">
                  <span className={`badge ${statusColor[o.lifecycle_status]}`}>{o.lifecycle_status.replace('_', ' ')}</span>
                  {o.lifecycle_status === 'OPEN' && o.scheduling_status !== 'ASSIGNED' && <span className={`badge ml-1 ${statusColor[o.scheduling_status]}`}>{o.scheduling_status.replace('_', ' ')}</span>}
                  {o.locked && <span className="ml-1" title="departed — locked">🔒</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
