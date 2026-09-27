import { useEffect, useState } from 'react'
import { api, fmtTime } from '../api/client'
import type { AgentTask } from '../types'

const stCls: Record<string, string> = { pending: 'bg-gray-100 text-gray-600', running: 'bg-blue-100 text-blue-800', waiting_customer: 'bg-violet-100 text-violet-800', waiting_human: 'bg-amber-100 text-amber-800', done: 'bg-green-100 text-green-800', failed: 'bg-red-100 text-red-800', cancelled: 'bg-gray-100 text-gray-500' }
const phaseCls: Record<string, string> = { planned: 'text-gray-500', called: 'text-blue-700', returned: 'text-gray-800', validated: 'text-green-700', submitted: 'text-green-800', error: 'text-red-700', waiting: 'text-violet-700', woke: 'text-violet-700', budget: 'text-amber-700', decision: 'text-gray-600' }

/** Agent activity (§13.2 / §10): each task shows its phases (planned → called → returned → validated → submitted), budgets and outcome. */
export default function AgentTasksPanel({ tasks, onSelectOrder, notice, onChanged }: { tasks: AgentTask[]; onSelectOrder: (id: string) => void; notice: (m: string, k?: 'ok' | 'err') => void; onChanged: () => void }) {
  const [open, setOpen] = useState<string | null>(null)
  const [detail, setDetail] = useState<AgentTask | null>(null)
  useEffect(() => { if (!open) { setDetail(null); return } api.agentTask(open).then(setDetail).catch(() => setDetail(null)) }, [open, tasks])
  if (tasks.length === 0) return <div className="px-3 py-4 text-center text-xs text-gray-400">No agent tasks yet — tasks appear when the fast path cannot place an order, when a technician needs rest, or when a human resolves a case.</div>
  return (
    <div className="divide-y divide-gray-100 text-[11px]">
      {tasks.map((t) => (
        <div key={t.id} className="px-2 py-1" data-testid="agent-task">
          <div className="flex items-center gap-1">
            <span className={`badge ${stCls[t.status] ?? 'bg-gray-100'}`}>{t.status.replace('_', ' ')}</span>
            <span className="font-semibold">{t.role}</span>
            {t.order_id && <button className="font-mono text-blue-700 hover:underline" onClick={() => onSelectOrder(t.order_id!)}>{t.order_id}</button>}
            {t.technician_id && <span className="font-mono text-gray-500">{t.technician_id}</span>}
            <span className="ml-auto text-gray-400">{fmtTime(t.created_at)} · tools {t.tool_budget_used} · searches {t.search_budget_used} · {t.execution_mode}</span>
            {t.status === 'pending' && <button className="text-blue-700 hover:underline" onClick={() => api.runTask(t.id).then(() => { notice('Task run'); onChanged() }).catch((e) => notice((e as Error).message, 'err'))}>run</button>}
            <button className="text-blue-700 hover:underline" onClick={() => setOpen(open === t.id ? null : t.id)}>{open === t.id ? 'hide' : 'trace'}</button>
          </div>
          <div className="truncate text-gray-600">{t.goal}{t.outcome ? ` → ${String((t.outcome as { summary?: string }).summary ?? JSON.stringify(t.outcome)).slice(0, 160)}` : ''}{t.wakeup_reason ? ` · woke: ${t.wakeup_reason}` : ''}</div>
          {open === t.id && detail?.traces && (
            <ol className="mt-1 space-y-0.5 rounded bg-gray-50 p-1 font-mono text-[10px]">
              {detail.traces.map((x) => (
                <li key={x.seq}>
                  <span className={phaseCls[x.phase] ?? 'text-gray-700'}>#{x.seq} {x.phase}</span> {x.tool && <span className="font-semibold">{x.tool}</span>} {x.result_status && <span className={x.result_status === 'ok' ? 'text-green-700' : 'text-amber-700'}>{x.result_status}</span>} {x.reason_codes?.length ? <span className="text-amber-700">[{x.reason_codes.join(', ')}]</span> : null} {x.duration_ms != null ? <span className="text-gray-400">{x.duration_ms}ms</span> : null} {x.decided_by && <span className="text-gray-400">· {x.decided_by}</span>}
                  {x.args && <div className="pl-4 text-gray-500">args {JSON.stringify(x.args).slice(0, 200)}</div>}
                  {x.data && <div className="pl-4 text-gray-600">{JSON.stringify(x.data).slice(0, 240)}</div>}
                </li>
              ))}
            </ol>
          )}
        </div>
      ))}
    </div>
  )
}
