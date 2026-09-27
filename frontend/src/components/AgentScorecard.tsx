import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { AgentScorecard as Card } from '../types'

/** How the agent layer itself performed — not how good the schedule is, which every other panel already shows.
 *  Every number is derived from the task and trace rows the runtime writes, so it cannot drift from what happened. */
export default function AgentScorecard({ tick }: { tick: number }) {
  const [card, setCard] = useState<Card | null>(null)
  useEffect(() => { api.agentScorecard().then(setCard).catch(() => setCard(null)) }, [tick])
  if (!card) return <div className="px-3 py-4 text-center text-xs text-gray-400">loading…</div>
  if (!card.tasks) return <div className="px-3 py-4 text-center text-xs text-gray-400">{card.note ?? 'No agent tasks in this scenario yet.'}</div>
  const a = card.autonomy, c = card.cost, d = card.discipline, h = card.handover_quality
  const col = card.collaboration, tr = card.transparency, em = card.execution_mode
  return (
    <div className="space-y-2 p-2 text-[11px]">
      <div className="grid grid-cols-2 gap-2">
        <Metric label="Settled without a human" value={a?.rate_pct} unit="%" good={(a?.rate_pct ?? 0) >= 60}
          detail={`${a?.resolved ?? 0} resolved · ${a?.escalated ?? 0} escalated`} />
        <Metric label="Tool calls per task" value={c?.avg_tool_calls} good={(c?.avg_tool_calls ?? 99) <= (c?.tool_call_budget ?? 12) / 2}
          detail={`budget ${c?.tool_call_budget} · ${c?.budget_exhausted ?? 0} ran out`} />
        <Metric label="Calls that could never work" value={d?.wasted_pct} unit="%" good={(d?.wasted_pct ?? 0) <= 5} invert
          detail={`${d?.wasted_calls ?? 0} of ${d?.total_calls ?? 0} · ${d?.repeated_searches ?? 0} repeated searches`} />
        <Metric label="Escalations with full evidence" value={h?.rate_pct} unit="%" good={(h?.rate_pct ?? 0) >= 90}
          detail={`${h?.complete ?? 0} of ${h?.escalations ?? 0} hand-overs`} />
        <Metric label="Steps that recorded why" value={tr?.rate_pct} unit="%" good={(tr?.rate_pct ?? 0) >= 95}
          detail={`${tr?.with_reason ?? 0} of ${tr?.steps ?? 0} steps`} />
        <Metric label="Hand-offs between agents" value={col?.delegations} good={(col?.delegations ?? 0) > 0}
          detail={`${col?.child_tasks ?? 0} sub-tasks · depth ${col?.max_depth ?? 0}`} />
      </div>

      {col && Object.keys(col.pairs).length > 0 && (
        <Block title="Who handed work to whom">
          {Object.entries(col.pairs).map(([pair, n]) => (
            <span key={pair} className="rounded bg-sky-50 px-1.5 py-0.5 font-mono text-[10px] text-sky-800 ring-1 ring-sky-200">{pair} × {n}</span>
          ))}
        </Block>
      )}
      {d && Object.keys(d.reason_codes).length > 0 && (
        <Block title="Why those calls failed before they ran">
          {Object.entries(d.reason_codes).map(([code, n]) => (
            <span key={code} className="rounded bg-amber-50 px-1.5 py-0.5 font-mono text-[10px] text-amber-800 ring-1 ring-amber-200">{code} × {n}</span>
          ))}
        </Block>
      )}
      {h && Object.keys(h.missing_fields).length > 0 && (
        <Block title="Evidence missing from hand-overs">
          {Object.entries(h.missing_fields).map(([f, n]) => (
            <span key={f} className="rounded bg-red-50 px-1.5 py-0.5 font-mono text-[10px] text-red-700 ring-1 ring-red-200">{f} × {n}</span>
          ))}
        </Block>
      )}
      <Block title="Who decided">
        {Object.entries(em?.decided_by ?? {}).map(([k, n]) => (
          <span key={k} className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-700">{k === 'model' ? 'model' : 'rule policy'} × {n}</span>
        ))}
        {(em?.degraded_tasks ?? 0) > 0 && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-900">{em!.degraded_tasks} task(s) fell back to the rule policy</span>}
      </Block>
      {(card.skills_never_used?.length ?? 0) > 0 && (
        <Block title="Playbooks not exercised in this scenario">
          {card.skills_never_used!.map((s) => <span key={s} className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] text-gray-500">{s}</span>)}
        </Block>
      )}
    </div>
  )
}

function Metric({ label, value, unit, detail, good, invert }: { label: string; value: number | null | undefined; unit?: string; detail?: string; good?: boolean; invert?: boolean }) {
  const shown = value == null ? '—' : `${value}${unit ?? ''}`
  const tone = value == null ? 'text-gray-400' : good ? 'text-green-700' : invert ? 'text-red-700' : 'text-amber-700'
  return (
    <div className="rounded border border-gray-200 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wide text-gray-400">{label}</div>
      <div className={`text-lg font-semibold leading-tight ${tone}`}>{shown}</div>
      {detail && <div className="text-[10px] text-gray-500">{detail}</div>}
    </div>
  )
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{title}</div>
      <div className="mt-0.5 flex flex-wrap gap-1">{children}</div>
    </div>
  )
}
