import { useState } from 'react'
import { api, fmtTime, priorityColor } from '../api/client'
import type { Order, Plan } from '../types'

interface Props { plans: Plan[]; orders: Order[]; onChanged: () => void; notice: (m: string, k?: 'ok' | 'err') => void; onSelectOrder: (id: string) => void; onHoverAffected: (ids: string[]) => void }

const fmtMin = (m: number | null | undefined, local?: string | null) => local ? fmtTime(local) : m == null ? '–' : `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
const strategyLabel = (p: Plan) => p.strategy_tags.map((t) => t.replace(/_/g, ' ')).join(' · ')
const decisionCls = (d: string) => d === 'auto' ? 'bg-green-100 text-green-800' : d === 'forbidden' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'

export default function PlanCards({ plans, orders, onChanged, notice, onSelectOrder, onHoverAffected }: Props) {
  const [busy, setBusy] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const orderById = new Map(orders.map((o) => [o.id, o]))
  const act = async (label: string, id: string, fn: () => Promise<unknown>) => {
    setBusy(id)
    try {
      const r = (await fn()) as Record<string, unknown>
      notice(`${label}: ${JSON.stringify(r.status ?? r.decision ?? 'ok')}`)
      onChanged()
    } catch (e) {
      const err = e as { code?: string; message: string; details?: unknown }
      notice(`${label} failed (${err.code ?? 'error'}): ${err.message}`, 'err')
      onChanged()
    } finally { setBusy(null) }
  }
  const groups = new Map<string, Plan[]>()
  for (const p of plans) groups.set(p.run_id, [...(groups.get(p.run_id) ?? []), p])
  if (plans.length === 0) return <div className="px-3 py-6 text-center text-xs text-gray-400">No candidate plans awaiting review. Auto-committed plans appear under “Recent plans” and in Agent activity.</div>
  return (
    <div className="space-y-3 p-2">
      {[...groups.entries()].map(([runId, group]) => {
        const target = group[0].target_order_id
        const o = orderById.get(target)
        const pri = group[0].metrics.target_priority as string | undefined
        const reviewable = group.filter((p) => p.status === 'PENDING_REVIEW')
        return (
          <div key={runId} className="rounded-md border border-gray-200">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-gray-100 bg-gray-50 px-2 py-1.5 text-xs">
              <span className="font-semibold">Target</span>
              <button className="font-mono text-blue-700 hover:underline" onClick={() => onSelectOrder(target)}>{target}</button>
              {pri && <span className={`badge ${priorityColor[pri] ?? ''}`}>{pri}</span>}
              {o && <span className="text-gray-500">{o.catalog_snapshot.problem_name} · {o.location.name} · window {fmtTime(o.window_start)}–{fmtTime(o.window_end)}</span>}
              <span className="ml-auto text-[10px] text-gray-400">base v{group[0].base_schedule_version} · expires {fmtTime(group[0].expires_at)}</span>
            </div>
            {reviewable.length > 1 && <Comparison plans={reviewable} target={target} />}
            <div className="flex flex-col gap-2 p-2">
              {group.map((p, i) => (
                <PlanCard key={p.id} p={p} index={i + 1} total={group.length} target={target} busy={busy === p.id} expanded={expanded === p.id}
                  onToggle={() => setExpanded(expanded === p.id ? null : p.id)} onHover={onHoverAffected} act={act} />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Comparison({ plans, target }: { plans: Plan[]; target: string }) {
  const tech = (p: Plan) => p.assignments.find((a) => a.order_id === target)?.technician_name ?? '–'
  const rows: [string, (p: Plan) => string][] = [
    ['Target start', (p) => fmtMin(p.metrics.target_service_start as number, p.metrics.target_service_start_local)],
    ['Technician', tech],
    ['Decision score (min)', (p) => p.decision_score?.toFixed(2) ?? '–'],
    ['Affected orders', (p) => `${p.affected_order_ids.length}${p.affected_order_ids.length ? ` (${p.affected_order_ids.map((i) => i.replace('wo_', '')).join(', ')})` : ''}`],
    ['Technician changes', (p) => String(p.metrics.technician_changes ?? 0)],
    ['Travel Δ (min)', (p) => String(p.metrics.travel_delta_minutes ?? '–')],
    ['Policy', (p) => p.policy_check.decision],
  ]
  return (
    <div className="overflow-x-auto border-b border-gray-100 px-2 py-1.5">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-left text-gray-400">
            <th className="pr-2 font-normal">Compare</th>
            {plans.map((p, i) => (
              <th key={p.id} className="pr-2 font-semibold text-gray-700">
                <span className="mr-1 inline-flex h-4 w-4 items-center justify-center rounded-full bg-gray-900 text-[10px] font-bold text-white">{i + 1}</span>
                <span className="capitalize">{strategyLabel(p)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows.map(([label, fn]) => <tr key={label} className="border-t border-gray-50"><td className="pr-2 text-gray-500">{label}</td>{plans.map((p) => <td key={p.id} className="pr-2 font-mono">{fn(p)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

function PlanCard({ p, index, total, target, busy, expanded, onToggle, onHover, act }: { p: Plan; index: number; total: number; target: string; busy: boolean; expanded: boolean; onToggle: () => void; onHover: (ids: string[]) => void; act: (label: string, id: string, fn: () => Promise<unknown>) => void }) {
  const over = p.status === 'OVER_LIMIT'
  const pending = p.status === 'PENDING_REVIEW'
  const targetAssign = p.assignments.find((a) => a.order_id === target)
  const diff = p.diff.filter((d) => d.change !== 'new')
  return (
    <div data-testid="plan-card" data-status={p.status} data-target={p.target_order_id}
      className={`rounded border p-2 text-xs ${over ? 'border-red-300 bg-red-50' : pending ? 'border-amber-300 bg-amber-50/40' : 'border-gray-200 bg-white'}`}
      onMouseEnter={() => onHover(p.affected_order_ids)} onMouseLeave={() => onHover([])}>
      <div className="flex flex-wrap items-center gap-1">
        <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-gray-900 text-[10px] font-bold text-white"
          title={`option ${index} of ${total} for this order`}>{index}</span>
        <span className="font-semibold capitalize">{strategyLabel(p)}</span>
        <span className={`badge ${decisionCls(p.policy_check.decision)}`}>policy: {p.policy_check.decision}</span>
        <span className={`badge ml-auto ${over ? 'bg-red-600 text-white' : pending ? 'bg-amber-200 text-amber-900' : 'bg-gray-100 text-gray-600'}`}>{p.status}</span>
      </div>
      <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-3">
        <KV k="Target start" v={fmtMin(p.metrics.target_service_start as number, p.metrics.target_service_start_local)} />
        <KV k="Technician" v={targetAssign?.technician_name ?? '–'} />
        <KV k="Travel / wait" v={targetAssign ? `${targetAssign.travel_minutes} / ${targetAssign.waiting_minutes} min` : '–'} />
        <KV k="Decision score" v={`${p.decision_score?.toFixed(2) ?? '–'}`} hint={`min of changed · >${p.policy_check.threshold} auto`} cls={(p.decision_score ?? 0) > p.policy_check.threshold ? 'text-green-700' : 'text-amber-700'} />
        <KV k="Affected" v={`${p.affected_order_ids.length}${p.policy_check.authority ? ` / ${p.policy_check.authority.max_affected ?? '∞'}` : ''}`} hint={p.policy_check.authority ? `movable: ${p.policy_check.authority.movable_priorities.join(', ') || 'none'}` : undefined} cls={over ? 'text-red-700' : ''} />
        <KV k="Travel Δ" v={`${String(p.metrics.travel_delta_minutes ?? '–')} min`} />
      </div>
      <p className="mt-1 text-[11px] leading-snug text-gray-700">{p.explanation}</p>
      {diff.length > 0 && (
        <div className="mt-1 overflow-x-auto">
          <table className="w-full whitespace-nowrap text-[10px]">
            <thead className="text-gray-400"><tr><th className="pr-2 text-left font-normal">order</th><th className="pr-2 text-left font-normal">pri</th><th className="pr-2 text-left font-normal">change</th><th className="pr-2 text-left font-normal">technician</th><th className="pr-2 text-left font-normal">start</th><th className="pr-2 text-right font-normal">Δ min</th><th className="text-center font-normal">counts</th></tr></thead>
            <tbody>{diff.map((d) => (
              <tr key={d.order_id} className={d.counts ? 'text-purple-800' : 'text-gray-500'}>
                <td className="pr-2 font-mono">{d.order_id.replace('wo_', '')}</td><td className="pr-2">{d.priority}</td><td className="pr-2">{d.change.replace('_', ' ')}</td>
                <td className="pr-2">{d.old_tech === d.new_tech || !d.old_tech ? d.new_tech : `${d.old_tech} → ${d.new_tech}`}</td>
                <td className="pr-2 font-mono">{fmtMin(d.old_start, d.old_start_local)}{d.shift_minutes ? ` → ${fmtMin(d.new_start, d.new_start_local)}` : ''}</td>
                <td className="pr-2 text-right font-mono">{d.shift_minutes ? `${d.shift_minutes > 0 ? '+' : ''}${d.shift_minutes}` : ''}</td><td className="text-center">{d.counts ? '✓' : ''}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
      <button className="mt-1 text-[10px] text-blue-700 hover:underline" onClick={onToggle}>{expanded ? 'hide' : 'show'} full diff & scores</button>
      {expanded && (
        <div className="mt-1 max-h-48 overflow-auto rounded bg-gray-50 p-1 font-mono text-[10px]">
          {p.assignments.map((a) => (
            <div key={a.order_id}>{a.order_id} → {a.technician_id} dep {fmtMin(a.departure, a.departure_local)} start {fmtMin(a.service_start, a.service_start_local)} end {fmtMin(a.service_end, a.service_end_local)} travel {a.travel_minutes} wait {a.waiting_minutes} score {a.match_score?.toFixed(1) ?? '–'}
              {a.score_components && <span className="text-gray-500"> [{Object.entries(a.score_components).filter(([k]) => k !== 'total').map(([k, v]) => `${k} ${(v as { value: number }).value?.toFixed(2)}`).join(', ')}]</span>}</div>
          ))}
          <div className="mt-1 text-gray-600">policy reasons: {p.policy_check.reasons.join(' | ')}</div>
          {p.policy_check.authority && p.policy_check.authority.violations.length > 0 && <div className="text-red-700">authority: {p.policy_check.authority.violations.join(' | ')}</div>}
          {!p.validation.ok && <div className="text-red-700">validation: {p.validation.violations.map((v) => v.code).join(', ')}</div>}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1">
        {pending && <button data-testid="approve" className="btn btn-primary" disabled={busy} onClick={() => act('Approve', p.id, () => api.approve(p.id, { actor: 'dispatcher', idempotency_key: `ui-${p.id}` }))}>Approve & commit</button>}
        {(pending || over) && <button className="btn" disabled={busy} onClick={() => act('Reject', p.id, () => api.reject(p.id, { actor: 'dispatcher', reason: 'rejected in UI', idempotency_key: `uir-${p.id}` }))}>Reject</button>}
        <button className="btn" disabled={busy} onClick={() => act('Recompute', p.id, () => api.recompute(p.id))}>Recompute</button>
        {p.status === 'EXPIRED' && <span className="text-[10px] text-gray-500">{p.status_reason}</span>}
      </div>
      {over && <div className="mt-1 text-[10px] text-red-700">Exceeds this priority's authority — no approve button. The dispatcher must resolve it outside the normal approval flow (e.g. ask the customer to reschedule, or wait for resources).</div>}
    </div>
  )
}

function KV({ k, v, hint, cls }: { k: string; v: string; hint?: string; cls?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wide text-gray-400">{k}</div>
      <div className={`truncate font-mono ${cls ?? ''}`} title={hint}>{v}{hint && <span className="ml-1 font-sans text-[10px] normal-case text-gray-400">({hint})</span>}</div>
    </div>
  )
}
