import { useState } from 'react'
import { api } from '../api/client'
import type { Clock, Health } from '../types'

interface Props { clock: Clock | null; health: Health | null; onChanged: () => void; onReset: () => void; onDemo: () => void; onDev: () => void; notice: (msg: string, kind?: 'ok' | 'err') => void }

export const RESET_KEY = 'techsched_demo_reset'
/** Tell the customer / technician tabs (same browser) that the demo was reset so they drop their sessions. */
export function broadcastReset() { try { localStorage.setItem(RESET_KEY, String(Date.now())) } catch { /* ignore */ } }

export default function TopBar({ clock, health, onChanged, onReset, onDemo, onDev, notice }: Props) {
  const [busy, setBusy] = useState<string | null>(null)
  const run = async (label: string, fn: () => Promise<unknown>, done?: (r: unknown) => string) => {
    setBusy(label)
    try {
      const r = await fn()
      notice(done ? done(r) : `${label} done`)
      onChanged()
    } catch (e) {
      notice(`${label} failed: ${(e as Error).message}`, 'err')
    } finally { setBusy(null) }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-gray-200 bg-white px-3 py-2">
      <div className="flex items-center gap-1">
        {[1, 5, 15].map((m) => (
          <button key={m} className="btn" disabled={!!busy} onClick={() => run(`+${m} min`, () => api.advance(m), (r) => {
            const d = r as { fired_events: unknown[]; dispatched: unknown[] }
            return `Advanced ${m} min · ${d.fired_events.length} execution event(s), ${d.dispatched.length} dispatch(es)`
          })}>+{m}m</button>
        ))}
        <button className="btn" disabled={!!busy} onClick={() => run(clock?.running ? 'Pause' : 'Run', () => api.control(!clock?.running))}>{clock?.running ? '⏸ Pause' : '▶ Run'}</button>
        <button className="btn" disabled={!!busy} onClick={() => run('Scan', () => api.scan(), (r) => { const d = r as { evaluated: number; dispatched: unknown[] }; return `Scan: ${d.evaluated} orders evaluated, ${d.dispatched.length} dispatched` })}>Scan now</button>
      </div>
      <div className="ml-auto flex items-center gap-1">
        <span className="badge bg-gray-100 text-gray-700">scenario {clock?.scenario_name ?? '-'} · gen {clock?.scenario_generation ?? '-'}</span>
        {health && !health.catalog_configured && <span className="badge bg-red-100 text-red-800">catalog NOT CONFIGURED</span>}
        <button className="btn btn-primary" disabled={!!busy} onClick={() => run('Generate Schedule', () => api.initial(), (r) => {
          const d = r as { decision: string; unassigned: string[]; status: string }
          return `Initial scheduling: ${d.status}, decision ${d.decision}, ${d.unassigned.length} unassigned`
        })}>Generate Schedule</button>
        <button data-testid="open-demo" className="btn" disabled={!!busy} onClick={onDemo}>Demo controls</button>
        <button data-testid="reset-demo" className="btn btn-danger" disabled={!!busy} title="scenario main at 08:30; clears all orders, plans, cases, tasks, chat sessions and the customer / technician app state in this browser"
          onClick={() => { if (confirm('Reset the whole demo? Scenario main is reloaded at 08:30 and every order, plan, case, task and chat session is cleared (customer and technician tabs reset too).')) run('Reset demo', async () => { const r = await api.reset('main'); broadcastReset(); onReset(); return r }, (r) => `Demo reset · scenario main · generation ${(r as { clock: Clock }).clock.scenario_generation} · 08:30`) }}>Reset demo</button>
        <button data-testid="open-dev" className="btn" disabled={!!busy} onClick={onDev} title="implementation details: models, routing, duration shadow report, tool traces">⚙ Dev</button>
      </div>
      {busy && <span className="text-xs text-gray-500">{busy}…</span>}
    </div>
  )
}
