import { useEffect, useMemo, useState } from 'react'
import { api, fmtTime } from '../api/client'
import type { AgentTask, AgentTrace } from '../types'

/** Reasoning timeline: the same ToolTrace rows the dev drawer lists, read as a story instead of a log.
 *  Each step pairs the agent's own reason for acting with what the tool answered, and a delegated task is
 *  nested under the step that handed it over — so a hand-off between agents is visible, not inferred. */

const statusChip: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-600', running: 'bg-blue-100 text-blue-800',
  waiting_customer: 'bg-violet-100 text-violet-800', waiting_human: 'bg-amber-100 text-amber-800',
  waiting_agent: 'bg-sky-100 text-sky-800', succeeded: 'bg-green-100 text-green-800',
  no_solution: 'bg-orange-100 text-orange-800', failed: 'bg-red-100 text-red-800', stale: 'bg-gray-100 text-gray-500',
}
const roleChip: Record<string, string> = {
  scheduling: 'bg-blue-50 text-blue-700 ring-blue-200', recovery: 'bg-rose-50 text-rose-700 ring-rose-200',
  break: 'bg-cyan-50 text-cyan-700 ring-cyan-200', customer: 'bg-violet-50 text-violet-700 ring-violet-200',
  dispatcher: 'bg-amber-50 text-amber-800 ring-amber-200',
}
const okStatus = (s: string | null) => s === 'ok' || s === 'waiting'

/** One tool call = the "planned" row (why) plus the row that came back (what). Shown as a single step. */
interface Step { seq: number; tool: string; thought: string | null; args: Record<string, unknown> | null; result: AgentTrace | null }

function toSteps(traces: AgentTrace[]): Step[] {
  const out: Step[] = []
  for (const t of traces) {
    if (t.phase === 'planned') { out.push({ seq: t.seq, tool: t.tool ?? '', thought: t.thought, args: t.args, result: null }); continue }
    const open = [...out].reverse().find((s) => s.tool === t.tool && s.result === null)
    if (open) open.result = t
    else out.push({ seq: t.seq, tool: t.tool ?? '', thought: t.thought, args: t.args, result: t })
  }
  return out
}

function summarise(step: Step): string | null {
  const d = (step.result?.data ?? {}) as Record<string, unknown>
  const n = (v: unknown) => (Array.isArray(v) ? v.length : undefined)
  if (step.tool === 'get_order_context' && d.effective_priority) {
    const a = d.authority as { max_affected?: number | null; movable_priorities?: string[] } | undefined
    const cap = a?.max_affected === null ? 'no limit' : a?.max_affected
    return `${d.effective_priority} · may move ${cap ?? '0'} of ${(a?.movable_priorities ?? []).join('/') || 'nothing'}`
  }
  if (n(d.candidates) !== undefined) return `${n(d.candidates)} candidate plan(s)`
  if (n(d.slots) !== undefined) return `${n(d.slots)} rest slot(s) with no knock-on effect`
  if (n(d.windows) !== undefined) return `${n(d.windows)} feasible window(s)`
  if (d.submitted) return d.submitted === 'committed' ? `committed · schedule v${d.schedule_version}` : 'sent to the review queue'
  if (d.child_task_ids || d.child_task_id) return `handed to a ${String(d.role ?? '')} agent`
  if (d.human_case_id) return `human case ${String(d.human_case_id)}`
  if (d.question_id) return 'question sent to the customer'
  if (d.level) return `rest level ${String(d.level)} · ${String(d.work_minutes_since_break ?? '?')} min since last rest`
  return null
}

export default function AgentReasoning({ tasks, onSelectOrder }: { tasks: AgentTask[]; onSelectOrder: (id: string) => void }) {
  const [open, setOpen] = useState<string | null>(null)
  const [detail, setDetail] = useState<Record<string, AgentTask>>({})
  const roots = useMemo(() => tasks.filter((t) => !t.parent_task_id), [tasks])
  const childrenOf = useMemo(() => {
    const m: Record<string, AgentTask[]> = {}
    for (const t of tasks) if (t.parent_task_id) (m[t.parent_task_id] ??= []).push(t)
    return m
  }, [tasks])

  // fetch the traces of whatever is expanded, plus its children, so a hand-off can be read in one place
  useEffect(() => {
    if (!open) return
    const wanted = [open, ...(childrenOf[open] ?? []).map((c) => c.id)]
    Promise.all(wanted.map((id) => api.agentTask(id).catch(() => null)))
      .then((rows) => setDetail(Object.fromEntries(rows.filter(Boolean).map((r) => [r!.id, r!]))))
  }, [open, childrenOf, tasks])

  if (roots.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-xs text-gray-400">
        No agent has been asked to think yet.<br />
        A task opens when the fast path cannot place an order, when a technician needs rest, or when a human resolves a case.
      </div>
    )
  }
  return (
    <div className="divide-y divide-gray-100">
      {roots.map((t) => (
        <TaskBlock key={t.id} task={t} detail={detail} subtasks={childrenOf[t.id] ?? []}
          expanded={open === t.id} onToggle={() => setOpen(open === t.id ? null : t.id)} onSelectOrder={onSelectOrder} depth={0} />
      ))}
    </div>
  )
}

function TaskBlock({ task, detail, subtasks, expanded, onToggle, onSelectOrder, depth }: {
  task: AgentTask; detail: Record<string, AgentTask>; subtasks: AgentTask[]
  expanded: boolean; onToggle: () => void; onSelectOrder: (id: string) => void; depth: number
}) {
  const full = detail[task.id]
  const steps = full?.traces ? toSteps(full.traces) : []
  return (
    <div className={depth > 0 ? 'ml-4 border-l-2 border-sky-200 pl-2' : ''} data-testid="agent-reasoning-task">
      <div
        role={depth === 0 ? 'button' : undefined}
        tabIndex={depth === 0 ? 0 : undefined}
        aria-expanded={depth === 0 ? expanded : undefined}
        data-testid={depth === 0 ? 'reasoning-toggle' : undefined}
        onClick={depth === 0 ? onToggle : undefined}
        onKeyDown={depth === 0 ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle() } } : undefined}
        className={`px-2 py-1.5 ${depth === 0 ? 'cursor-pointer hover:bg-gray-50' : ''}`}
      >
        <div className="flex flex-wrap items-center gap-1 text-[11px]">
          <span className={`badge ring-1 ${roleChip[task.role] ?? 'bg-gray-50 text-gray-700 ring-gray-200'}`}>{task.role}</span>
          <span className={`badge ${statusChip[task.status] ?? 'bg-gray-100 text-gray-600'}`}>{task.status.replace('_', ' ')}</span>
          {task.order_id && (
            <button className="font-mono text-blue-700 hover:underline" onClick={(e) => { e.stopPropagation(); onSelectOrder(task.order_id!) }}>{task.order_id}</button>
          )}
          {task.technician_id && <span className="font-mono text-gray-500">{task.technician_id}</span>}
          <span className="ml-auto flex items-center gap-1.5 text-gray-400">
            <span title="tool calls this task spent; the skill file sets the ceiling">{task.tool_budget_used} tool calls</span>
            <span>·</span>
            <span title={task.execution_mode === 'real' ? 'decided by the configured model' : 'decided by the labelled rule policy'}>
              {task.execution_mode === 'real' ? 'model' : 'rule policy'}
            </span>
            <span>·</span>
            <span>{fmtTime(task.created_at)}</span>
          </span>
        </div>
        <div className="mt-0.5 text-[11px] text-gray-600">{task.goal}</div>
        {task.skill && (
          <div className="mt-0.5 text-[10px] text-gray-400">
            playbook <span className="font-mono text-gray-500">{task.skill.source}</span> · {task.skill.tools.length} tools granted
          </div>
        )}
      </div>

      {expanded && (
        <div className="px-2 pb-2">
          {steps.length === 0 && <div className="py-2 text-[11px] text-gray-400">loading the trace…</div>}
          <ol className="space-y-1">
            {steps.map((s) => {
              const isFinish = s.tool === 'finish'
              const delegated = s.tool === 'delegate_task'
              return (
                <li key={s.seq}>
                  {s.thought && (
                    <div className="flex gap-1.5 text-[11px] text-gray-700">
                      <span className="shrink-0 text-gray-400">{isFinish ? '✓' : '💭'}</span>
                      <span className="italic">{s.thought}</span>
                    </div>
                  )}
                  {!isFinish && (
                    <div className="ml-5 flex flex-wrap items-baseline gap-1.5 text-[11px]">
                      <span className="font-mono font-semibold text-gray-800">{s.tool}</span>
                      {s.result ? (
                        <span className={okStatus(s.result.result_status) ? 'text-green-700' : 'text-amber-700'}>
                          {s.result.result_status}
                        </span>
                      ) : <span className="text-gray-400">no result</span>}
                      {s.result?.reason_codes?.length ? (
                        <span className="font-mono text-[10px] text-amber-700">[{s.result.reason_codes.join(', ')}]</span>
                      ) : null}
                      {summarise(s) && <span className="text-gray-500">{summarise(s)}</span>}
                      {s.result?.duration_ms != null && <span className="text-gray-300">{s.result.duration_ms}ms</span>}
                    </div>
                  )}
                  {delegated && subtasks
                    .filter((c) => !s.args?.order_id || c.order_id === s.args.order_id)
                    .filter((c) => !s.args?.technician_id || c.technician_id === s.args.technician_id)
                    .map((c) => (
                      <div key={c.id} className="mt-1">
                        <TaskBlock task={c} detail={detail} subtasks={[]} expanded onToggle={() => undefined}
                          onSelectOrder={onSelectOrder} depth={depth + 1} />
                      </div>
                    ))}
                </li>
              )
            })}
          </ol>
          {task.outcome && typeof (task.outcome as { degraded?: string }).degraded === 'string' && (
            <div className="mt-1 rounded bg-amber-50 px-1.5 py-1 text-[10px] text-amber-800">
              degraded: {(task.outcome as { degraded?: string }).degraded}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
