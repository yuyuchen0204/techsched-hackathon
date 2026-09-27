import { useEffect, useState } from 'react'
import { api } from '../api/client'
import EventInjector from './EventInjector'
import type { Order, ScenarioInfo, Technician } from '../types'

interface Props { orders: Order[]; techs: Technician[]; simNow: string | null; onClose: () => void; onChanged: () => void; notice: (m: string, k?: 'ok' | 'err') => void; presetTech?: string }

/** Demo controls (§13.2): scenario loading and structured event injection live here, off the main dispatcher surface. */
export default function DemoControls({ orders, techs, simNow, onClose, onChanged, notice, presetTech }: Props) {
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([])
  const [current, setCurrent] = useState<{ scenario: string; generation: number; seed: number } | null>(null)
  const [load, setLoad] = useState<{ utilisation_by_technician: Record<string, number>; mean_utilisation: number; unassigned: string[] } | null>(null)
  const [seed, setSeed] = useState('')
  const [busy, setBusy] = useState(false)
  const [inject, setInject] = useState(Boolean(presetTech))
  const refresh = () => { api.scenarios().then((r) => { setScenarios(r.scenarios); setCurrent(r.current) }).catch(() => undefined); api.loadSummary().then(setLoad).catch(() => undefined) }
  useEffect(refresh, [])
  const reset = async (name: string) => {
    if (!confirm(`Load scenario "${name}"? Current orders, plans, cases and tasks are cleared.`)) return
    setBusy(true)
    try { const r = await api.resetWithSeed(name, seed ? Number(seed) : undefined); notice(`Scenario ${name} loaded (generation ${r.clock.scenario_generation}, seed ${r.clock.seed})`); onChanged(); refresh() } catch (e) { notice(`Reset failed: ${(e as Error).message}`, 'err') } finally { setBusy(false) }
  }
  const techName = (id: string) => techs.find((t) => t.id === id)?.name ?? id
  return (
    <div className="fixed inset-y-0 right-0 z-[650] flex w-[520px] max-w-full flex-col border-l border-gray-200 bg-white shadow-xl" data-testid="demo-controls">
      <div className="flex items-center justify-between border-b border-gray-100 px-3 py-2"><span className="text-xs font-semibold uppercase text-gray-500">Demo controls</span><button className="btn" onClick={onClose}>close</button></div>
      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-3 text-xs">
        <section>
          <div className="mb-1 font-semibold">Scenario (load profile)</div>
          <div className="space-y-1">
            {scenarios.map((s) => (
              <div key={s.name} className={`flex items-center gap-2 rounded-lg border px-3 py-2 ${current?.scenario === s.name ? 'border-blue-400 bg-blue-50' : 'border-gray-200'}`}>
                <div className="min-w-0 flex-1"><div className="font-semibold">{s.name}{current?.scenario === s.name ? ' · loaded' : ''}</div><div className="text-gray-500">{s.description || `${s.technicians} technicians · ${s.orders} orders`}</div></div>
                <button className="btn" disabled={busy} onClick={() => reset(s.name)}>Load</button>
              </div>
            ))}
          </div>
          <div className="mt-2 flex items-center gap-2"><span className="text-gray-500">seed</span><input className="w-24 rounded border px-2 py-1" placeholder={String(current?.seed ?? 42)} value={seed} onChange={(e) => setSeed(e.target.value)} /><span className="text-gray-400">generation {current?.generation ?? '–'} · reset is atomic (old generation invisible)</span></div>
        </section>
        {load && (
          <section>
            <div className="mb-1 font-semibold">Load summary <span className="font-normal text-gray-500">(service+travel / shift, before dynamic rest)</span></div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
              {Object.entries(load.utilisation_by_technician).map(([id, u]) => (
                <div key={id} className="flex items-center gap-1"><span className="w-24 truncate">{techName(id)}</span><div className="h-2 flex-1 rounded bg-gray-100"><div className={`h-2 rounded ${u > 0.85 ? 'bg-red-500' : u > 0.6 ? 'bg-amber-400' : 'bg-green-500'}`} style={{ width: `${Math.min(100, u * 100)}%` }} /></div><span className="w-9 text-right tabular-nums">{Math.round(u * 100)}%</span></div>
              ))}
            </div>
            <div className="mt-1 text-gray-500">mean {Math.round(load.mean_utilisation * 100)}% · unassigned {load.unassigned.length}{load.unassigned.length ? ` (${load.unassigned.join(', ')})` : ''}</div>
          </section>
        )}
        <section>
          <div className="mb-1 flex items-center justify-between"><span className="font-semibold">Inject event</span><button className="btn" onClick={() => setInject((s) => !s)}>{inject ? 'hide' : 'open'}</button></div>
          <div className="text-gray-500">Structured events at the current sim time: technician unavailable, paid expedite, complaints, dispatcher cancel. Customer and technician apps generate the same events through their own screens.</div>
        </section>
      </div>
      {inject && <EventInjector orders={orders} techs={techs} presetTech={presetTech} simNow={simNow} onClose={() => setInject(false)} onChanged={onChanged} notice={notice} />}
    </div>
  )
}
