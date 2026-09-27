import { useEffect, useRef, useState } from 'react'
import { fmtTime } from '../api/client'
import type { HumanCase, Risk, SafetyIncident } from '../types'

/** Things that need a person interrupt the dispatcher instead of waiting in a tab (V3.1).
 *
 *  Rules, so this stays an alarm and not noise:
 *   - only items nobody has picked up: unresolved safety incidents, `pending` human cases of high/critical urgency,
 *     and P0 risks whose handling state says no plan exists or a human already owns them;
 *   - every id fires at most once per session — acknowledging closes the alert, it never resolves the case;
 *   - what already existed when the board was opened does not fire (otherwise every reload pops), with one exception:
 *     a live safety incident always does, because that is the one case where "already there" is not reassuring;
 *   - several at once share one dialog rather than stacking modals.
 */
export interface Attention { id: string; kind: 'safety' | 'case' | 'risk'; title: string; detail: string; orderId: string | null; at: string }

function collect(cases: HumanCase[], incidents: SafetyIncident[], risks: Risk[]): Attention[] {
  const out: Attention[] = []
  for (const i of incidents) {
    if (i.status === 'resolved') continue
    out.push({ id: `inc:${i.id}`, kind: 'safety', title: `Safety incident · ${i.danger_type.replace(/_/g, ' ')}`,
               detail: i.description || 'A customer reported a dangerous situation.', orderId: i.order_id, at: i.created_at })
  }
  for (const c of cases) {
    if (c.status !== 'pending' || !['high', 'critical'].includes(c.urgency)) continue
    out.push({ id: `case:${c.id}`, kind: 'case', title: `${c.urgency === 'critical' ? 'Critical' : 'Urgent'} case · ${c.category.replace(/_/g, ' ')}`,
               detail: (c.reason_summary || '').split('\n')[0], orderId: c.order_id, at: c.created_at })
  }
  for (const r of risks) {
    if (r.severity !== 'P0') continue
    const state = r.handling?.state
    if (state !== 'unresolved' && state !== 'with_human') continue
    out.push({ id: `risk:${r.id}`, kind: 'risk', title: `P0 · ${r.type.replace(/_/g, ' ').toLowerCase()}`,
               detail: `${String(r.payload.detail ?? '')} — ${r.handling?.label ?? ''}`, orderId: r.target_order_id, at: r.first_seen })
  }
  return out
}

export default function AttentionModal({ cases, incidents, risks, ready, onOpenQueue, onSelectOrder }:
  { cases: HumanCase[]; incidents: SafetyIncident[]; risks: Risk[]; ready: boolean; onOpenQueue: () => void; onSelectOrder: (id: string) => void }) {
  const seen = useRef<Set<string> | null>(null)
  const [queue, setQueue] = useState<Attention[]>([])
  useEffect(() => {
    // the baseline of "what was already there" can only be taken once the first poll of every source has landed —
    // seeding it from the empty initial render made every pre-existing case look new and pop on load
    if (!ready) return
    const items = collect(cases, incidents, risks)
    if (seen.current === null) {
      seen.current = new Set(items.map((i) => i.id))
      const live = items.filter((i) => i.kind === 'safety')
      if (live.length > 0) setQueue(live)
      return
    }
    const fresh = items.filter((i) => !seen.current!.has(i.id))
    if (fresh.length === 0) return
    fresh.forEach((i) => seen.current!.add(i.id))
    setQueue((q) => [...q, ...fresh])
  }, [cases, incidents, risks, ready])

  // an item someone else closed while the dialog was open disappears from it
  const live = new Set(collect(cases, incidents, risks).map((i) => i.id))
  const shown = queue.filter((i) => live.has(i.id))
  useEffect(() => { if (queue.length > 0 && shown.length === 0) setQueue([]) }, [queue.length, shown.length])
  if (shown.length === 0) return null
  const worst = shown.some((i) => i.kind === 'safety')
  return (
    <div className="fixed inset-0 z-[900] flex items-center justify-center bg-black/50 p-4" data-testid="attention-modal">
      <div className={`w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl ring-2 ${worst ? 'ring-red-500' : 'ring-amber-400'}`}>
        <div className={`px-4 py-2 text-sm font-semibold text-white ${worst ? 'bg-red-600' : 'bg-amber-500'}`}>
          {worst ? 'Safety — a person must take this now' : `${shown.length} item${shown.length > 1 ? 's need' : ' needs'} a person`}
        </div>
        <div className="max-h-[50vh] space-y-2 overflow-auto p-4 text-xs">
          {shown.map((i) => (
            <div key={i.id} className="rounded-xl border border-gray-200 p-3">
              <div className="flex items-center gap-2">
                <span className={`badge ${i.kind === 'safety' ? 'bg-red-600 text-white' : i.kind === 'risk' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'}`}>{i.kind}</span>
                <span className="font-semibold">{i.title}</span>
                <span className="ml-auto text-gray-400">{fmtTime(i.at)}</span>
              </div>
              <div className="mt-1 text-gray-700">{i.detail}</div>
              {i.orderId && <button className="mt-1 font-mono text-blue-700 hover:underline" onClick={() => { onSelectOrder(i.orderId!); setQueue([]) }}>open {i.orderId}</button>}
            </div>
          ))}
        </div>
        <div className="flex items-center gap-2 border-t border-gray-100 px-4 py-2 text-xs">
          <span className="text-gray-500">Acknowledging only closes this alert — the {shown.length > 1 ? 'items stay' : 'item stays'} in the human queue.</span>
          <button className="btn ml-auto" onClick={() => setQueue([])}>Acknowledge</button>
          <button data-testid="attention-open-queue" className="btn btn-primary" onClick={() => { setQueue([]); onOpenQueue() }}>Open the human queue</button>
        </div>
      </div>
    </div>
  )
}
