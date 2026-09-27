import { useState } from 'react'
import { fmtTime } from '../api/client'
import type { AgentRun } from '../types'

const statusCls: Record<string, string> = { completed: 'bg-green-100 text-green-800', pending_review: 'bg-amber-100 text-amber-800', unresolved: 'bg-red-100 text-red-800', failed: 'bg-red-200 text-red-900', no_action: 'bg-gray-100 text-gray-600', processing: 'bg-blue-100 text-blue-800' }

export default function AgentActivity({ runs, onSelectOrder }: { runs: AgentRun[]; onSelectOrder: (id: string) => void }) {
  const [open, setOpen] = useState<string | null>(null)
  if (runs.length === 0) return <div className="px-3 py-4 text-center text-xs text-gray-400">No agent runs yet</div>
  return (
    <div className="divide-y divide-gray-100">
      {runs.map((r) => (
        <div key={r.id} className="px-2 py-1 text-[11px]">
          <div className="flex items-center gap-1">
            <span className={`badge ${statusCls[r.status] ?? 'bg-gray-100'}`}>{r.status}</span>
            <span className="font-semibold">{r.agent}</span>
            <span className="text-gray-500">{r.trigger}</span>
            {r.target_order_id && <button className="font-mono text-blue-700 hover:underline" onClick={() => onSelectOrder(r.target_order_id!)}>{r.target_order_id}</button>}
            <span className="ml-auto text-gray-400">{fmtTime(r.started_at)} · {r.duration_ms ?? '–'} ms · {r.execution_mode}</span>
            <button className="text-blue-700 hover:underline" onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? 'hide' : 'steps'}</button>
          </div>
          <div className="truncate text-gray-600">{r.summary}{r.error ? ` · error: ${r.error}` : ''}</div>
          {open === r.id && (
            <ol className="mt-1 space-y-0.5 rounded bg-gray-50 p-1 font-mono text-[10px]">
              {r.steps.map((s, i) => (
                <li key={i}>
                  <span className={s.status === 'error' ? 'text-red-700' : 'text-gray-800'}>{i + 1}. {s.agent} › {s.step}</span> <span className="text-gray-400">{s.duration_ms ?? 0} ms</span>
                  {s.summary && <span className="text-gray-600"> — {s.summary}</span>}
                  {s.tool_calls.map((t, j) => <div key={j} className="pl-4 text-gray-500">↳ {t.tool} {JSON.stringify(t.facts).slice(0, 220)}</div>)}
                  {s.error && <div className="pl-4 text-red-700">{s.error}</div>}
                </li>
              ))}
            </ol>
          )}
        </div>
      ))}
    </div>
  )
}
