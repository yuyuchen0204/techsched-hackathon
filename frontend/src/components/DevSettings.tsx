import { useEffect, useState } from 'react'
import { api, fmtTime } from '../api/client'
import { usePolling } from '../stores/usePolling'
import AgentActivity from './AgentActivity'
import AgentTasksPanel from './AgentTasksPanel'
import NotificationsPanel from './NotificationsPanel'
import TechniciansPanel from './TechniciansPanel'
import type { DurationReport, Health } from '../types'

/** Developer settings (§13.2): implementation facts, duration shadow report, tool traces — and the four reference views
 *  (technicians, agent activity, notifications, schedule versions) that used to crowd the dispatcher's main surface.
 *  They stay one click away because they show real behaviour; they just no longer compete with the two decision queues. */
export default function DevSettings({ health, onClose, notice, onSelectOrder, onSelectTech }: { health: Health | null; onClose: () => void; notice: (m: string, k?: 'ok' | 'err') => void; onSelectOrder?: (id: string) => void; onSelectTech?: (id: string) => void }) {
  const techs = usePolling(api.technicians, 5000, [])
  const runs = usePolling(api.runs, 5000, [])
  const tasks = usePolling(api.agentTasks, 5000, [])
  const notifications = usePolling(() => api.notifications(), 6000, [])
  const versions = usePolling(api.versions, 8000, [])
  const [view, setView] = useState<'runtime' | 'technicians' | 'agent' | 'notifications' | 'versions'>('runtime')
  const [report, setReport] = useState<DurationReport | null>(null)
  const [traces, setTraces] = useState<Awaited<ReturnType<typeof api.toolTraces>>>([])
  const [busy, setBusy] = useState(false)
  const refresh = () => { api.durationReport().then(setReport).catch(() => undefined); api.toolTraces().then(setTraces).catch(() => undefined) }
  useEffect(refresh, [])
  const rep = report?.report as Record<string, unknown> | null
  return (
    <div className="fixed inset-y-0 right-0 z-[650] flex w-[560px] max-w-full flex-col border-l border-gray-200 bg-white shadow-xl" data-testid="dev-settings">
      <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2"><span className="text-xs font-semibold uppercase text-gray-500">Developer settings</span><button className="btn" onClick={onClose}>close</button></div>
      <div className="flex gap-1 border-b border-gray-100 px-2 py-1 text-[11px]">
        {([['runtime', 'Runtime & models'], ['technicians', `Technicians (${techs.data?.length ?? 0})`],
           ['agent', `Agent activity (${(tasks.data ?? []).filter((x) => ['pending', 'running', 'waiting_customer', 'waiting_human'].includes(x.status)).length})`],
           ['notifications', 'Notifications'], ['versions', 'Versions']] as const).map(([k, label]) => (
          <button key={k} data-testid={`dev-tab-${k}`} className={`rounded px-1.5 py-0.5 ${view === k ? 'bg-gray-900 text-white' : 'hover:bg-gray-100'}`} onClick={() => setView(k)}>{label}</button>
        ))}
      </div>
      {view === 'technicians' && <div className="min-h-0 flex-1 overflow-auto text-xs"><TechniciansPanel techs={techs.data ?? []} onSelect={(id) => { onSelectTech?.(id); onClose() }} /></div>}
      {view === 'agent' && (
        <div className="min-h-0 flex-1 overflow-auto text-xs">
          <div className="px-2 pt-1 text-[10px] font-semibold uppercase text-gray-400">Agent tasks (tool-driven, budgeted)</div>
          <AgentTasksPanel tasks={tasks.data ?? []} onSelectOrder={(id) => { onSelectOrder?.(id); onClose() }} notice={notice} onChanged={() => undefined} />
          <div className="border-t border-gray-100 px-2 pt-1 text-[10px] font-semibold uppercase text-gray-400">Fast-path runs (intake · scheduling · recovery)</div>
          <AgentActivity runs={runs.data ?? []} onSelectOrder={(id) => { onSelectOrder?.(id); onClose() }} />
        </div>
      )}
      {view === 'notifications' && <div className="min-h-0 flex-1 overflow-auto text-xs"><NotificationsPanel items={notifications.data ?? []} /></div>}
      {view === 'versions' && (
        <div className="min-h-0 flex-1 divide-y divide-gray-100 overflow-auto text-[11px]">
          {(versions.data ?? []).map((v) => (
            <div key={v.id} className="px-2 py-1">
              <div className="flex gap-1"><span className={`badge ${v.active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500'}`}>v{v.id}</span><span>{v.reason}</span><span className="ml-auto text-gray-400">sim {v.sim_now.slice(11, 16)} · {v.assignment_count} assignments</span></div>
              <div className="text-gray-400">parent v{v.parent_id ?? '–'} · policy {v.policy_version} · route {v.route_snapshot_id}{v.metrics.changed_orders ? ` · changed ${(v.metrics.changed_orders as string[]).length}` : ''}</div>
            </div>
          ))}
        </div>
      )}
      <div className={`min-h-0 flex-1 space-y-4 overflow-auto p-3 text-xs ${view === 'runtime' ? '' : 'hidden'}`}>
        <section>
          <div className="mb-1 font-semibold">Runtime</div>
          <div className="grid grid-cols-[130px_1fr] gap-y-0.5">
            <span className="text-gray-500">LLM</span><span>{health?.llm_mode}{health?.llm_mode === 'real' ? ` · ${health.llm_provider} · ${health.llm_model}` : ' (rule-based)'}</span>
            <span className="text-gray-500">Routing</span><span>{health?.route_mode}{health?.route_mode === 'osrm' ? ` · ${health.osrm_base_url} · ×${health.osrm_duration_factor} + ${health.osrm_base_minutes} min` : ''}</span>
            <span className="text-gray-500">Catalog</span><span>{health?.catalog_configured ? `${health.catalog_items} items` : 'NOT CONFIGURED'}</span>
            <span className="text-gray-500">Policy version</span><span>{health?.policy_version}</span>
            <span className="text-gray-500">Timezone</span><span>{health?.timezone}</span>
            <span className="text-gray-500">Agent budgets</span><span>12 tool calls · 3 searches · 2 transient retries per task (config/policy.yaml)</span>
            <span className="text-gray-500">Duration prediction</span><span>shadow mode — predictions are recorded next to actuals, never used for scheduling</span>
          </div>
        </section>
        <section>
          <div className="mb-1 flex items-center justify-between"><span className="font-semibold">Duration evaluation (shadow)</span><button className="btn" disabled={busy} onClick={() => { setBusy(true); api.buildDurationReport().then(() => { notice('Duration report rebuilt'); refresh() }).catch((e) => notice((e as Error).message, 'err')).finally(() => setBusy(false)) }}>Rebuild report</button></div>
          {rep ? (() => { const m = (rep.metrics ?? {}) as Record<string, unknown>; const src = (m.source_counts ?? {}) as Record<string, number>; return (
            <div className="rounded bg-gray-50 p-2">
              <div>version <span className="font-mono">{String(rep.id)}</span> · {String(rep.method)} · valid samples {String(m.observations_valid ?? '–')} / {String(m.observations_total ?? '–')} ({Object.entries(src).map(([k, v]) => `${k} ${v}`).join(', ')})</div>
              <div>MAE catalog baseline {String(m.mae_csv_baseline ?? '–')} min · MAE shadow model {String(m.mae_shadow_model ?? '–')} min · fallback ratio {String(m.fallback_ratio ?? '–')}</div>
              <div className="text-gray-500">{String(m.note ?? '')}</div>
            </div>) })() : <div className="text-gray-400">no report yet — needs completed observations (≥ 5 per problem for a prediction)</div>}
          <table className="mt-1 w-full">
            <thead><tr className="text-left text-gray-500"><th>order</th><th>problem</th><th>baseline</th><th>actual</th><th>pred</th><th>quality</th></tr></thead>
            <tbody>{(report?.recent_observations ?? []).slice(0, 15).map((o, i) => <tr key={i} className="border-t border-gray-100"><td className="font-mono">{o.order_id}</td><td className="truncate">{o.problem}</td><td>{o.baseline}</td><td>{o.actual}</td><td>{o.prediction ?? '–'}</td><td className={o.quality === 'ok' ? 'text-green-700' : 'text-amber-700'}>{o.quality}</td></tr>)}</tbody>
          </table>
        </section>
        <section>
          <div className="mb-1 font-semibold">Recent tool traces</div>
          <div className="max-h-72 overflow-auto rounded bg-gray-50 p-1 font-mono text-[10px]">
            {traces.length === 0 && <div className="text-gray-400">no tool calls recorded yet</div>}
            {traces.map((t, i) => <div key={i}>{fmtTime(t.at)} {t.task_id.slice(-6)} #{t.seq} <span className="text-blue-700">{t.phase}</span> {t.tool ?? ''} {t.status ?? ''} {t.reason_codes?.length ? `[${t.reason_codes.join(',')}]` : ''} {t.duration_ms != null ? `${t.duration_ms}ms` : ''} {t.decided_by ? `· ${t.decided_by}` : ''}</div>)}
          </div>
        </section>
      </div>
    </div>
  )
}
