import { useState } from 'react'
import { fmtTime, priorityColor } from '../api/client'
import type { Risk } from '../types'

/** One card per active risk, each carrying what is currently being done about it.
 *  An active risk with an empty review queue is normal (auto-committed recovery, or a risk that only clears when the
 *  technician starts work) — the handling line is what tells the dispatcher "handled" from "ignored". */
const stateStyle: Record<string, { dot: string; text: string }> = {
  pending_review: { dot: 'bg-amber-500', text: 'text-amber-800' },
  with_human: { dot: 'bg-violet-500', text: 'text-violet-800' },
  executing: { dot: 'bg-blue-500', text: 'text-blue-800' },
  scheduled: { dot: 'bg-green-500', text: 'text-green-800' },
  searching: { dot: 'bg-sky-500', text: 'text-sky-800' },
  unresolved: { dot: 'bg-red-500', text: 'text-red-800' },
  closed: { dot: 'bg-gray-400', text: 'text-gray-500' },
  none: { dot: 'bg-gray-300', text: 'text-gray-500' },
}

export default function RiskPanel({ risks, onSelectOrder }: { risks: Risk[]; onSelectOrder: (id: string) => void }) {
  if (risks.length === 0) return <div className="px-3 py-4 text-center text-xs text-gray-400">No active risks</div>
  return (
    <div className="space-y-1 p-1.5">
      {risks.map((r) => <RiskCard key={r.id} r={r} onSelectOrder={onSelectOrder} />)}
    </div>
  )
}

function RiskCard({ r, onSelectOrder }: { r: Risk; onSelectOrder: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const h = r.handling
  const st = stateStyle[h?.state ?? 'none'] ?? stateStyle.none
  return (
    <div className="rounded-lg border border-gray-200 bg-white text-[11px]" data-testid="risk-card">
      <button className="flex w-full items-start gap-2 px-2 py-1.5 text-left hover:bg-gray-50" onClick={() => setOpen((s) => !s)}>
        <span className={`badge ${priorityColor[r.severity]}`}>{r.severity}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1">
            <span className="font-semibold">{r.type.replace(/_/g, ' ').toLowerCase()}</span>
            {r.target_order_id && <span className="font-mono text-blue-700" onClick={(e) => { e.stopPropagation(); onSelectOrder(r.target_order_id!) }}>{r.target_order_id}</span>}
            {r.status === 'manual' && <span className="badge bg-gray-200 text-gray-700">manual queue</span>}
          </div>
          <div className={`flex items-center gap-1 ${st.text}`} data-testid="risk-handling">
            <i className={`inline-block h-1.5 w-1.5 shrink-0 rounded-full ${st.dot}`} />
            <span className="truncate">{h?.label ?? 'handling unknown'}</span>
          </div>
        </div>
        <span className="mt-0.5 shrink-0 text-gray-400">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className="space-y-0.5 border-t border-gray-100 px-2 py-1.5 text-gray-600">
          <div><span className="text-gray-400">why: </span>{String(r.payload.detail ?? r.type)}</div>
          {h?.detail && <div><span className="text-gray-400">plan: </span>{h.detail}</div>}
          {h?.state === 'pending_review' && <div className="text-amber-800">Open the Review queue tab to approve or reject.</div>}
          {h?.state === 'unresolved' && <div className="text-red-800">Nothing found within the current authority — widen the window, or handle it by hand.</div>}
          {h?.state === 'executing' && <div className="text-blue-800">Open the order to stop the visit if the technician cannot finish it.</div>}
          <div className="text-gray-400">since {fmtTime(r.first_seen)} · last seen {fmtTime(r.last_seen)}{h?.human_case_id ? ` · case ${h.human_case_id}` : ''}</div>
          {r.target_order_id && <button className="btn mt-1" onClick={() => onSelectOrder(r.target_order_id!)}>Open order {r.target_order_id}</button>}
        </div>
      )}
    </div>
  )
}
