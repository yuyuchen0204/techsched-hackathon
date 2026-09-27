import { useEffect, useState } from 'react'
import { api, fmtTime } from '../api/client'
import type { HumanCase, ReschedulePreview, SafetyIncident } from '../types'

const srcCls: Record<string, string> = { CUSTOMER_REQUEST: 'bg-blue-100 text-blue-800', POLICY_REQUIRED: 'bg-amber-100 text-amber-800', AGENT_ESCALATION: 'bg-red-100 text-red-800' }
const stCls: Record<string, string> = { pending: 'bg-red-50 text-red-700', in_progress: 'bg-amber-50 text-amber-800', waiting_customer: 'bg-violet-50 text-violet-800', resolved: 'bg-green-50 text-green-700' }

/** Human queue (§9): three case sources, take / resolve; safety incidents; reschedule approvals.
 *  No free-text channel to the customer from here — the customer app is not a chat console for the dispatcher. */
export default function HumanCasesPanel({ cases, incidents, onChanged, notice, onSelectOrder, author = 'dispatcher' }: { cases: HumanCase[]; incidents: SafetyIncident[]; onChanged: () => void; notice: (m: string, k?: 'ok' | 'err') => void; onSelectOrder: (id: string) => void; author?: string }) {
  const [open, setOpen] = useState<string | null>(null)
  const [detail, setDetail] = useState<HumanCase | null>(null)
  const [resolution, setResolution] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!open) { setDetail(null); return } api.humanCase(open).then(setDetail).catch(() => setDetail(null)) }, [open, cases])
  const act = async (label: string, fn: () => Promise<unknown>) => { setBusy(true); try { await fn(); notice(`${label}: done`); onChanged() } catch (e) { notice(`${label} failed: ${(e as Error).message}`, 'err') } finally { setBusy(false) } }
  const active = cases.filter((c) => c.status !== 'resolved')
  const resolved = cases.filter((c) => c.status === 'resolved').slice(0, 10)
  const openIncidents = incidents.filter((i) => i.status !== 'resolved')
  return (
    <div className="text-[11px]">
      {openIncidents.length > 0 && (
        <div className="border-b border-red-200 bg-red-50 px-2 py-1.5" data-testid="incident-list">
          <div className="font-semibold text-red-800">Safety incidents ({openIncidents.length})</div>
          {openIncidents.map((i) => (
            <div key={i.id} className="mt-1 flex items-start gap-1">
              <span className="badge bg-red-600 text-white">{i.danger_type}</span>
              <div className="min-w-0 flex-1"><div className="truncate">“{i.description}”</div><div className="text-gray-600">{String(i.known_location?.formatted_address ?? 'location unknown')} · call the customer and confirm they are safe</div></div>
              <button className="btn" disabled={busy} onClick={() => act('Resolve incident', () => api.resolveIncident(i.id, 'handled by coordinator', author))}>resolve</button>
            </div>
          ))}
        </div>
      )}
      {active.length === 0 && <div className="px-3 py-4 text-center text-gray-400">No open human cases</div>}
      {active.map((c) => (
        <div key={c.id} className={`border-b border-gray-100 ${open === c.id ? 'bg-blue-50/40' : ''}`} data-testid="human-case">
          {/* The whole header is the target, not a 30px "open" link: these cards are read and opened constantly, and
              the summary lines are clamped rather than truncated so the case can be triaged without opening it. */}
          <div role="button" tabIndex={0} aria-expanded={open === c.id} data-testid="case-toggle"
            className={`flex w-full cursor-pointer items-start gap-2 px-2 py-2 text-left transition-colors ${open === c.id ? '' : 'hover:bg-blue-50/60'}`}
            onClick={() => setOpen(open === c.id ? null : c.id)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(open === c.id ? null : c.id) } }}>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-1">
                <span className={`badge ${srcCls[c.source] ?? 'bg-gray-100'}`}>{c.source.replace('_', ' ').toLowerCase()}</span>
                <span className={`badge ${stCls[c.status] ?? 'bg-gray-100'}`}>{c.status.replace('_', ' ')}</span>
                {c.urgency !== 'normal' && <span className="badge bg-red-600 text-white">{c.urgency}</span>}
                <span className="text-gray-500">{c.category}</span>
                {c.order_id && (
                  <button className="rounded px-1 font-mono text-blue-700 hover:bg-blue-100 hover:underline"
                    title={`open order ${c.order_id}`}
                    onClick={(e) => { e.stopPropagation(); onSelectOrder(c.order_id!) }}>{c.order_id}</button>
                )}
                <span className="ml-auto text-gray-400" title={`raised ${c.created_at} (wall clock)`}>sim {fmtTime(c.updated_at ?? c.created_at)}{c.assignee ? ` · ${c.assignee}` : ''}</span>
              </div>
              <div className="mt-0.5 line-clamp-2 text-gray-700">{c.reason_summary}</div>
              {c.suggested_next_action && <div className="mt-0.5 line-clamp-2 text-gray-500">→ {c.suggested_next_action}</div>}
            </div>
            <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500 shadow-sm"
              aria-hidden="true" title={open === c.id ? 'collapse' : 'open this case'}>
              <span className="transition-transform duration-200" style={{ display: 'inline-block', transform: open === c.id ? 'rotate(90deg)' : 'none' }}>▸</span>
            </span>
          </div>
          {open === c.id && (
            <div className="mx-2 mb-2 space-y-1 rounded bg-white p-2 shadow-inner">
              {detail?.order && <div className="text-gray-600">Order {detail.order.id} · {detail.order.problem} · {detail.order.priority} · {detail.order.status} / {detail.order.scheduling} · window {fmtTime(detail.order.window[0])}–{fmtTime(detail.order.window[1])}</div>}
              {detail?.reschedule_request && <RescheduleDecision req={detail.reschedule_request} preview={detail.reschedule_preview} author={author} busy={busy} act={act} />}
              {detail?.conversation && detail.conversation.length > 0 && (
                <div className="max-h-32 overflow-auto rounded bg-gray-50 p-1">
                  <div className="font-semibold text-gray-500">Conversation (attached, customer does not repeat)</div>
                  {detail.conversation.map((m, i) => <div key={i}><span className={m.role === 'user' ? 'text-blue-700' : 'text-gray-500'}>{m.role}:</span> {m.text}</div>)}
                </div>
              )}
              {c.attempted_actions.length > 0 && <div className="text-gray-500">attempted: {JSON.stringify(c.attempted_actions).slice(0, 300)}</div>}
              {c.unresolved_questions.length > 0 && <div className="text-gray-700">open questions: {c.unresolved_questions.join(' · ')}</div>}
              {c.evidence_refs.length > 0 && <div className="text-gray-400">evidence: {c.evidence_refs.join(', ')}</div>}
              {detail?.agent_tasks && detail.agent_tasks.length > 0 && <div className="text-gray-500">agent tasks: {detail.agent_tasks.map((t) => `${t.id.slice(-6)} ${t.role} ${t.status}`).join(' · ')}</div>}
              {c.replies.length > 0 && <div className="space-y-0.5">{c.replies.map((r, i) => <div key={i}><span className="font-medium">{r.author}</span> <span className="text-gray-400">{fmtTime(r.at)}</span>: {r.text}</div>)}</div>}
              <div className="flex flex-wrap gap-1">
                {c.status === 'pending' && <button data-testid="case-take" className="btn btn-primary" disabled={busy} onClick={() => act('Take', () => api.takeCase(c.id, author))}>Take</button>}

              </div>
              <form className="flex gap-1" onSubmit={(e) => { e.preventDefault(); act('Resolve', () => api.resolveCase(c.id, resolution || 'resolved', author)); setResolution('') }}>
                <input data-testid="case-resolution" className="min-w-0 flex-1 rounded border px-2 py-1" placeholder="resolution note" value={resolution} onChange={(e) => setResolution(e.target.value)} />
                <button data-testid="case-resolve" className="btn" disabled={busy}>Resolve</button>
              </form>
            </div>
          )}
        </div>
      ))}
      {resolved.length > 0 && (
        <div className="px-2 py-1">
          <div className="text-[10px] font-semibold uppercase text-gray-400">Recently resolved</div>
          {resolved.map((c) => <div key={c.id} className="truncate text-gray-500"><span className="badge bg-green-50 text-green-700">resolved</span> {c.category} · {c.reason_summary} · {c.resolution}</div>)}
        </div>
      )}
    </div>
  )
}

/** Deciding a reschedule request, which the queue previously could not do at all: the case could only be closed,
 *  which left the request `pending` for ever and never moved the appointment.
 *
 *  The trial result is shown before the buttons on purpose — approving releases the confirmed appointment first, so
 *  "can it actually be served then?" has to be answered before the dispatcher commits, not after. */
function RescheduleDecision({ req, preview, author, busy, act }: {
  req: { id: string; status: string; reason: string; window: [string, string] }
  preview?: ReschedulePreview
  author: string
  busy: boolean
  act: (label: string, fn: () => Promise<unknown>) => Promise<void>
}) {
  const [note, setNote] = useState('')
  const pending = req.status === 'pending'
  const ok = preview?.feasible === true
  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50/60 p-2" data-testid="reschedule-decision">
      <div className="flex items-center gap-1">
        <span className="font-semibold text-blue-900">Reschedule request</span>
        <span className={`badge ${pending ? 'bg-amber-100 text-amber-800' : req.status === 'approved' ? 'bg-green-100 text-green-800' : 'bg-gray-200 text-gray-600'}`}>{req.status}</span>
        <span className="ml-auto font-mono text-gray-500">{fmtTime(req.window[0])}–{fmtTime(req.window[1])}</span>
      </div>
      {req.reason && <div className="text-gray-600">reason: {req.reason}</div>}
      {pending && (
        preview === undefined ? <div className="text-gray-400">checking whether that window can be served…</div>
        : ok ? (
          <div className="text-green-800">
            ✓ Can be served: {preview.technician_name} at {fmtTime(preview.new_start)}
            {preview.disturbs_others
              ? ` · moves ${preview.affected_count} other appointment(s): ${(preview.affected_order_ids ?? []).join(', ')}`
              : ' · no other appointment moves'}
            {preview.decision === 'manual' && <span className="text-amber-800"> · the re-dispatch will still need plan approval</span>}
            <div className="text-gray-500">now: {preview.current_technician ?? '–'} at {fmtTime(preview.current_start)} · {preview.note}</div>
          </div>
        ) : (
          <div className="text-red-800">✗ Cannot be served in that window: {preview.reason}<div className="text-gray-500">Decline and the original appointment stands.</div></div>
        )
      )}
      {pending && (
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <input className="min-w-0 flex-1 rounded border px-2 py-1" placeholder="note to the customer (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button data-testid="reschedule-approve" className="btn btn-primary" disabled={busy || !ok}
            title={ok ? 'Move the appointment and re-dispatch' : 'The trial says this window cannot be served'}
            onClick={() => act('Approve reschedule', () => api.approveReschedule(req.id, author))}>Approve &amp; re-dispatch</button>
          <button data-testid="reschedule-decline" className="btn" disabled={busy}
            onClick={() => act('Decline reschedule', () => api.declineReschedule(req.id, author, note || 'the requested time could not be arranged'))}>Keep original time</button>
        </div>
      )}
    </div>
  )
}
