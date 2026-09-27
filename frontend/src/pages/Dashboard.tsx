import { useCallback, useEffect, useState } from 'react'
import { api } from '../api/client'
import { usePolling } from '../stores/usePolling'
import TopBar from '../components/TopBar'
import KpiBar from '../components/KpiBar'
import Timeline from '../components/Timeline'
import MapView from '../components/MapView'
import OrdersTable from '../components/OrdersTable'
import OrderDetail from '../components/OrderDetail'
import PlanCards from '../components/PlanCards'
import RiskPanel from '../components/RiskPanel'
import DemoControls from '../components/DemoControls'
import DevSettings from '../components/DevSettings'
import HumanCasesPanel from '../components/HumanCasesPanel'
import AgentReasoning from '../components/AgentReasoning'
import AgentSkills from '../components/AgentSkills'
import AgentScorecard from '../components/AgentScorecard'
import AttentionModal from '../components/AttentionModal'
import TechnicianDetail from '../components/TechnicianDetail'
import type { LocationRef, Plan } from '../types'

interface Notice { id: number; msg: string; kind: 'ok' | 'err' }

export default function Dashboard() {
  const [tick, setTick] = useState(0)
  const refresh = useCallback(() => setTick((t) => t + 1), [])
  const clock = usePolling(api.clock, 2000, [tick])
  const health = usePolling(api.health, 15000, [])
  const schedule = usePolling(api.schedule, 3000, [tick])
  const techs = usePolling(api.technicians, 3000, [tick])
  const orders = usePolling(() => api.orders(), 3000, [tick])
  const risks = usePolling(api.risks, 3000, [tick])
  const plans = usePolling(() => api.plans(), 3000, [tick])
  const positions = usePolling(api.positions, 1500, [tick])
  const cases = usePolling(() => api.humanCases(), 3000, [tick])
  const incidents = usePolling(api.incidents, 5000, [tick])
  const agentTasks = usePolling(api.agentTasks, 4000, [tick])
  const [locations, setLocations] = useState<LocationRef[]>([])
  useEffect(() => { api.locations().then(setLocations).catch(() => undefined) }, [])
  const [selectedOrder, setSelectedOrder] = useState<string | null>(null)
  const [selectedTech, setSelectedTech] = useState<string | null>(null)
  const [hoverAffected, setHoverAffected] = useState<string[]>([])
  const [demo, setDemo] = useState<false | { techId?: string }>(false)
  const [dev, setDev] = useState(false)
  const [techDetail, setTechDetail] = useState<string | null>(null)
  const openTech = (id: string) => { setSelectedTech(id); setTechDetail(id); setSelectedOrder(null) }
  const [notices, setNotices] = useState<Notice[]>([])
  const [tab, setTab] = useState<'plans' | 'human' | 'agents'>('plans')
  const [agentView, setAgentView] = useState<'reasoning' | 'skills' | 'scorecard'>('reasoning')
  const notice = useCallback((msg: string, kind: 'ok' | 'err' = 'ok') => {
    const id = Date.now() + Math.random()
    setNotices((n) => [...n, { id, msg, kind }])
    setTimeout(() => setNotices((n) => n.filter((x) => x.id !== id)), kind === 'err' ? 9000 : 5000)
  }, [])
  const reviewPlans = (plans.data ?? []).filter((p) => ['PENDING_REVIEW', 'OVER_LIMIT'].includes(p.status))
  const recentOther = (plans.data ?? []).filter((p) => !['PENDING_REVIEW', 'OVER_LIMIT'].includes(p.status)).slice(0, 12)
  const anyError = [clock, schedule, techs, orders].map((x) => x.error).find(Boolean)
  const openCases = (cases.data ?? []).filter((c) => c.status !== 'resolved')
  const openIncidents = (incidents.data ?? []).filter((i) => i.status !== 'resolved')
  const riskOrders = new Set((risks.data ?? []).map((r) => r.target_order_id).filter(Boolean)).size
  const liveAgents = (agentTasks.data ?? []).filter((t) => ['pending', 'running', 'waiting_customer', 'waiting_human', 'waiting_agent'].includes(t.status)).length
  const availableTechs = (techs.data ?? []).filter((t) => !['UNAVAILABLE', 'OFF_SHIFT'].includes(t.status)).length
  const catalogMissing = health.data && !health.data.catalog_configured
  return (
    <div className="flex h-full flex-col">
      <TopBar clock={clock.data} health={health.data} onChanged={refresh} onReset={() => { setSelectedOrder(null); setSelectedTech(null); setTechDetail(null); setHoverAffected([]); setDemo(false); setDev(false); setTab('plans'); setAgentView('reasoning') }} onDemo={() => setDemo({})} onDev={() => setDev(true)} notice={notice} />
      {anyError && <div className="bg-red-100 px-3 py-1 text-xs text-red-800">Backend unreachable or failing: {anyError}. Retrying… (is the backend running on :8100?)</div>}
      {catalogMissing && <div className="bg-amber-100 px-3 py-1 text-xs text-amber-900">Repair problem catalog not configured — place the CSV at the path shown in /health (REPAIR_CATALOG_PATH) and call POST /api/catalog/reload. Orders cannot be created until then.</div>}
      <KpiBar schedule={schedule.data} clock={clock.data} extra={{ pendingHuman: openCases.length + openIncidents.length, availableTechs, riskOrders }} />
      <div className="grid min-h-0 flex-1 grid-cols-12 gap-2 px-3 pb-2">
        <div className="col-span-7 flex min-h-0 flex-col gap-2 overflow-y-auto pr-1">
          <div className="panel shrink-0">
            <div className="panel-title"><span>Technician timeline</span><span className="normal-case text-gray-400">{techs.loading ? 'loading…' : `${techs.data?.length ?? 0} technicians · click a task for its order, a name for the technician`}</span></div>
            {techs.data && orders.data ? <Timeline techs={techs.data} orders={orders.data} clock={clock.data} selectedOrder={selectedOrder} selectedTech={selectedTech} onSelectOrder={(id) => { setSelectedOrder(id); setTechDetail(null) }} onSelectTech={openTech} /> : <div className="p-4 text-xs text-gray-400">loading…</div>}
          </div>
          <div className="panel flex h-[440px] shrink-0 flex-col overflow-hidden">
            <div className="panel-title"><span>Map</span><span className="normal-case text-gray-400">{selectedTech ? `route of ${techs.data?.find((t) => t.id === selectedTech)?.name ?? selectedTech}` : selectedOrder ? `route containing ${selectedOrder}` : 'click a technician marker or an order to show the route'}</span></div>
            <div className="min-h-0 flex-1">{techs.data && orders.data && <MapView techs={techs.data} orders={orders.data} locations={locations} positions={positions.data?.technicians} selectedOrder={selectedOrder} selectedTech={selectedTech} affectedIds={hoverAffected} onSelectOrder={(id) => { setSelectedOrder(id); setTechDetail(null) }} onSelectTech={openTech} />}</div>
          </div>
          <div className="panel flex h-[420px] shrink-0 flex-col overflow-hidden">
            <div className="panel-title"><span>Work orders</span><span className="normal-case text-gray-400">click a row for details · 💳 paid expedite · 🔒 departed · 📍 map-pinned address</span></div>
            <div className="min-h-0 flex-1">{orders.data ? <OrdersTable orders={orders.data} selected={selectedOrder} onSelect={(id) => { setSelectedOrder(id); setTechDetail(null) }} /> : <div className="p-3 text-xs text-gray-400">loading…</div>}</div>
          </div>
        </div>
        <div className="col-span-5 flex min-h-0 flex-col gap-2">
          <div className="panel max-h-[220px] overflow-auto">
            <div className="panel-title"><span>Active risks</span><span className="normal-case text-gray-400">{risks.data?.length ?? 0}</span></div>
            <RiskPanel risks={risks.data ?? []} onSelectOrder={setSelectedOrder} />
          </div>
          <div className="panel flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="panel-title">
              <div className="flex gap-1">
                {(['plans', 'human', 'agents'] as const).map((t) => <button key={t} data-testid={`tab-${t}`} className={`rounded px-1.5 py-0.5 ${tab === t ? 'bg-gray-900 text-white' : 'hover:bg-gray-100'} ${t === 'human' && openCases.length + openIncidents.length > 0 && tab !== t ? 'text-red-700' : ''}`} onClick={() => setTab(t)}>{t === 'plans' ? `Review queue (${reviewPlans.length})` : t === 'human' ? `Human queue (${openCases.length + openIncidents.length})` : `Agents (${liveAgents})`}</button>)}
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              {tab === 'plans' && (
                <>
                  <PlanCards plans={reviewPlans} orders={orders.data ?? []} onChanged={refresh} notice={notice} onSelectOrder={setSelectedOrder} onHoverAffected={setHoverAffected} />
                  {recentOther.length > 0 && <RecentPlans plans={recentOther} onSelectOrder={setSelectedOrder} />}
                </>
              )}
              {tab === 'human' && <HumanCasesPanel cases={cases.data ?? []} incidents={incidents.data ?? []} onChanged={refresh} notice={notice} onSelectOrder={setSelectedOrder} />}
              {tab === 'agents' && (
                <>
                  <div className="sticky top-0 z-10 flex gap-1 border-b border-gray-100 bg-white px-2 py-1 text-[11px]">
                    {([['reasoning', 'Reasoning'], ['skills', 'Skills'], ['scorecard', 'Scorecard']] as const).map(([k, label]) => (
                      <button key={k} data-testid={`agent-view-${k}`} className={`rounded px-1.5 py-0.5 ${agentView === k ? 'bg-gray-200 font-semibold text-gray-900' : 'text-gray-500 hover:bg-gray-100'}`} onClick={() => setAgentView(k)}>{label}</button>
                    ))}
                    {agentView === 'reasoning' && (
                      <button className="ml-auto self-center text-[10px] text-blue-700 hover:underline"
                        title="Opens a dispatcher-role agent over every order the fast path could not place. It reads each one, delegates a recovery agent per order in urgency order, and reports a single consolidated result."
                        onClick={() => api.supervise([], 'dispatcher requested')
                          .then((t) => { notice(`Supervisor ${t.id} opened over ${((t.facts_summary as { order_ids?: string[] })?.order_ids ?? []).length} order(s)`); refresh() })
                          .catch((e) => notice((e as Error).message, 'err'))}>
                        supervise unresolved orders
                      </button>
                    )}
                    <span className={`self-center text-[10px] text-gray-400 ${agentView === 'reasoning' ? '' : 'ml-auto'}`}>{agentView === 'reasoning' ? 'click a task to read how it decided' : agentView === 'skills' ? 'the playbook each agent was given' : 'how the agent layer itself performed'}</span>
                  </div>
                  {agentView === 'reasoning' && <AgentReasoning tasks={agentTasks.data ?? []} onSelectOrder={(id) => { setSelectedOrder(id); setTechDetail(null) }} />}
                  {agentView === 'skills' && <AgentSkills notice={notice} />}
                  {agentView === 'scorecard' && <AgentScorecard tick={tick} />}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
      {selectedOrder && !techDetail && <OrderDetail orderId={selectedOrder} onClose={() => setSelectedOrder(null)} onChanged={refresh} notice={notice} tick={tick} />}
      {techDetail && techs.data && orders.data && (() => { const t = techs.data.find((x) => x.id === techDetail); return t ? <TechnicianDetail tech={t} orders={orders.data} locations={locations} onClose={() => setTechDetail(null)} onSelectOrder={(id) => { setTechDetail(null); setSelectedOrder(id) }} onInjectUnavailable={(id) => { setTechDetail(null); setDemo({ techId: id }) }} /> : null })()}
      {demo && techs.data && orders.data && <DemoControls orders={orders.data} techs={techs.data} presetTech={demo.techId} simNow={clock.data?.now ?? null} onClose={() => setDemo(false)} onChanged={refresh} notice={notice} />}
      <AttentionModal cases={cases.data ?? []} incidents={incidents.data ?? []} risks={risks.data ?? []}
        ready={cases.data !== null && incidents.data !== null && risks.data !== null}
        onOpenQueue={() => setTab('human')} onSelectOrder={(id) => { setSelectedOrder(id); setTechDetail(null) }} />
      {dev && <DevSettings health={health.data} onClose={() => setDev(false)} notice={notice} onSelectOrder={setSelectedOrder} onSelectTech={openTech} />}
      <div className="pointer-events-none fixed bottom-3 left-1/2 z-[800] flex -translate-x-1/2 flex-col gap-1">
        {notices.map((n) => <div key={n.id} className={`rounded-md px-3 py-1.5 text-xs shadow-lg ${n.kind === 'err' ? 'bg-red-600 text-white' : 'bg-gray-900 text-white'}`}>{n.msg}</div>)}
      </div>
    </div>
  )
}

function RecentPlans({ plans, onSelectOrder }: { plans: Plan[]; onSelectOrder: (id: string) => void }) {
  return (
    <div className="border-t border-gray-100 px-2 py-1">
      <div className="text-[10px] font-semibold uppercase text-gray-400">Recent plans (committed / superseded / expired)</div>
      {plans.map((p) => (
        <div key={p.id} className="flex items-center gap-1 text-[11px]">
          <span className={`badge ${p.status === 'COMMITTED' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-500'}`}>{p.status}</span>
          <button className="font-mono text-blue-700 hover:underline" onClick={() => onSelectOrder(p.target_order_id)}>{p.target_order_id}</button>
          <span className="text-gray-500">{p.strategy_tags.join('/')} · score {p.decision_score?.toFixed(1)} · affected {p.affected_order_ids.length} · {p.policy_check.decision}</span>
          {p.status_reason && <span className="truncate text-gray-400">· {p.status_reason}</span>}
        </div>
      ))}
    </div>
  )
}
