import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtTime, statusColor } from '../api/client'
import AddressModal, { type AddressPayload } from '../components/AddressModal'
import type { ChatOption, ChatOrder, ChatResponse, CustomerLogin, OpenQuestion } from '../types'

export const SESSION_KEY = 'techsched_customer_session'
export function sessionId(): string {
  try {
    let s = localStorage.getItem(SESSION_KEY)
    if (!s) { s = `sess_${Math.random().toString(36).slice(2, 10)}`; localStorage.setItem(SESSION_KEY, s) }
    return s
  } catch { return `sess_${Math.random().toString(36).slice(2, 10)}` }
}

type Send = (b: { message?: string; action?: string; payload?: Record<string, unknown>; display?: string }) => void

/** Customer mobile app (§7): identity → chat (catalog-only problems, address modal, feasible windows) → order list.
 *  Everything is simulated: no SMS, no payment, no dialling. */
export default function Customer() {
  const [sid, setSid] = useState(sessionId())
  const [chat, setChat] = useState<ChatResponse | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [logins, setLogins] = useState<CustomerLogin[]>([])
  const [addressFor, setAddressFor] = useState<null | Record<string, unknown>>(null)
  const [contact, setContact] = useState({ customer_name: '', contact_phone: '' })
  const [view, setView] = useState<'chat' | 'orders'>('chat')
  const [switcher, setSwitcher] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)
  // a demo reset (dashboard "Reset demo" in another tab, or a scenario reload noticed by generation) starts a fresh session
  useEffect(() => {
    const GEN_KEY = 'techsched_customer_gen'
    api.clock().then((c) => {
      let stored: string | null = null
      try { stored = localStorage.getItem(GEN_KEY); localStorage.setItem(GEN_KEY, String(c.scenario_generation)) } catch { /* ignore */ }
      if (stored !== null && stored !== String(c.scenario_generation)) newSession()
    }).catch(() => undefined)
    const onStorage = (e: StorageEvent) => { if (e.key === 'techsched_demo_reset') { setAddressFor(null); setView('chat'); newSession() } }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { api.customerLogins().then(setLogins).catch(() => undefined) }, [chat?.customer?.id])

  const send: Send = useCallback(async (body) => {
    setBusy(true); setErr(null)
    const { display, ...req } = body
    const pendingText = display ?? body.message ?? (body.action ? `[${body.action.replace(/_/g, ' ')}]` : '')
    if (pendingText) {
      setChat((c) => {
        const base = c ?? { session_id: sid, state: 'collecting', draft: {}, reply: { text: '', options: [], card: null }, messages: [], orders: [], llm: {}, sim_now: '', mode: 'simulated' }
        return { ...base, messages: [...base.messages, { role: 'user', text: pendingText, at: base.sim_now, pending: true }], reply: { text: '', options: [], card: null } }
      })
    }
    try { const r = await api.chat({ session_id: sid, ...req }); setChat(r) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }, [sid])

  useEffect(() => { api.chatSession(sid).then((r) => setChat((c) => c ?? { ...r, reply: { text: '', options: [], card: null }, llm: {} })).catch((e) => setErr((e as Error).message)) }, [sid])
  useEffect(() => { const id = setInterval(() => { if (!busy) api.chatSession(sid).then((r) => setChat((c) => c ? { ...c, orders: r.orders, sim_now: r.sim_now, human_case: r.human_case, open_questions: r.open_questions } : c)).catch(() => undefined) }, 4000); return () => clearInterval(id) }, [sid, busy])
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }) }, [chat?.messages.length, busy])

  const onSubmit = (e: React.FormEvent) => { e.preventDefault(); if (!input.trim()) return; const m = input; setInput(''); send({ message: m }) }
  const options = chat?.reply.options ?? []
  const lastCard = chat?.reply.card
  const customer = chat?.customer
  const humanCase = chat?.human_case
  const questions = chat?.open_questions ?? []
  const newSession = () => { try { localStorage.removeItem(SESSION_KEY) } catch { /* ignore */ } setChat(null); setSid(sessionId()) }
  const confirmAddress = (p: AddressPayload) => { setAddressFor(null); send({ action: 'set_address', payload: { ...p }, display: `[address] ${p.formatted_address}${p.unit_number ? ' #' + p.unit_number : ' (no unit)'}` }) }

  return (
    <div className="flex h-full justify-center bg-gray-200/60">
      <div className="flex h-full w-full max-w-md flex-col bg-white shadow-xl">
        <header className="flex items-center gap-2 border-b border-gray-100 px-3 py-2">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold">Repair booking</div>
            <div className="truncate text-[11px] text-gray-500">{customer ? `${customer.name}${customer.phone ? ` · ${customer.phone}` : ''}` : 'not signed in'} · sim {fmtTime(chat?.sim_now)}</div>
          </div>
          <button data-testid="demo-account" className="rounded-lg border px-2.5 py-1 text-[11px] font-medium text-gray-700 hover:bg-gray-50" disabled={busy} onClick={() => setSwitcher(true)}
            title={customer ? 'Sign in as someone else' : 'Sign in to reuse your saved address and see your orders'}>👤 {customer ? 'Switch' : 'Sign in'}</button>
        </header>
        <nav className="flex border-b border-gray-100 text-xs">
          <button className={`flex-1 py-2 ${view === 'chat' ? 'border-b-2 border-blue-600 font-semibold text-blue-700' : 'text-gray-500'}`} onClick={() => setView('chat')}>Chat</button>
          <button data-testid="tab-orders" className={`flex-1 py-2 ${view === 'orders' ? 'border-b-2 border-blue-600 font-semibold text-blue-700' : 'text-gray-500'}`} onClick={() => setView('orders')}>My orders ({chat?.orders.length ?? 0})</button>
        </nav>
        {humanCase && humanCase.status !== 'resolved' && (
          <div className="border-b border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            <div className="font-semibold">A support agent has your request ({humanCase.status.replace('_', ' ')}{humanCase.assignee ? ` · ${humanCase.assignee}` : ''})</div>
            <div>The assistant is paused; your messages go to the agent. {humanCase.replies.length > 0 && <>Latest reply: “{humanCase.replies[humanCase.replies.length - 1].text}”</>}</div>
          </div>
        )}
        {view === 'orders' ? (
          <div className="min-h-0 flex-1 overflow-auto p-3">
            {(chat?.orders ?? []).length === 0 && <div className="py-8 text-center text-sm text-gray-400">No orders yet.</div>}
            <div className="space-y-2">{(chat?.orders ?? []).map((o) => <OrderRow key={o.order_id} o={o} />)}</div>
          </div>
        ) : (
          <div className="min-h-0 flex-1 space-y-2 overflow-auto p-3 text-sm">
            {(!chat || chat.messages.length === 0) && (
              <div className="rounded-xl bg-gray-50 p-3 text-gray-600">
                <div className="font-medium text-gray-800">Hi! What needs repair?</div>
                <div className="mt-1 text-xs">Describe the problem in your own words (English or 中文). I'll match it to our repair catalog, then confirm the address, a feasible time window and your contact.</div>
                {logins.length > 0 && <div className="mt-2 text-xs text-gray-500">Booked with us before? We'll offer your saved address when we get to that step.</div>}
              </div>
            )}
            {chat?.messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3 py-2 ${m.role === 'user' ? 'rounded-br-md bg-blue-600 text-white' : 'rounded-bl-md bg-gray-100 text-gray-900'} ${m.pending ? 'opacity-70' : ''}`}>{m.text}<div className={`mt-0.5 text-[10px] ${m.role === 'user' ? 'text-blue-100' : 'text-gray-400'}`}>{m.pending ? 'sending…' : fmtTime(m.at)}</div></div>
              </div>
            ))}
            {chat?.reply.history && !busy && (
              <div className="rounded-xl border border-gray-200 p-3 text-xs">
                <div className="mb-1 font-semibold text-gray-700">Your recent repairs</div>
                {chat.reply.history.orders.map((h, i) => <div key={i} className="text-gray-600">· {h.problem}{h.date ? ` · ${h.date.slice(0, 10)}` : ''}{h.technician_name ? ` · ${h.technician_name}` : ''}</div>)}
                {chat.reply.history.negative_technicians.length > 0 && <div className="mt-1 text-gray-500">We remember your earlier low rating and will prefer another technician where possible.</div>}
              </div>
            )}
            {lastCard && !busy && (
              <div className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs" data-testid="confirm-card">
                <div className="mb-1 font-semibold">Please confirm</div>
                <div className="grid grid-cols-[84px_1fr] gap-y-0.5">
                  <span className="text-gray-500">Problem</span><span>{String(lastCard.problem)} · ~{String(lastCard.repair_duration_minutes)} min</span>
                  <span className="text-gray-500">Address</span><span>{String(lastCard.location)} · unit {String(lastCard.unit)}</span>
                  <span className="text-gray-500">Window</span><span>{String(lastCard.window)}</span>
                  <span className="text-gray-500">Contact</span><span>{String(lastCard.customer_name)} · {String(lastCard.contact_phone)}</span>
                  <span className="text-gray-500">Urgent</span><span data-testid="card-urgent">{lastCard.urgent ? 'yes' : 'no'}</span>
                  <span className="text-gray-500">Paid</span><span data-testid="card-paid">{lastCard.paid_expedite ? 'yes (simulated payment)' : 'no'}</span>
                  {Array.isArray(lastCard.excluded_technicians) && lastCard.excluded_technicians.length > 0 && <><span className="text-gray-500">Excluded</span><span>{(lastCard.excluded_technicians as string[]).join(', ')} (this order only)</span></>}
                </div>
              </div>
            )}
            {questions.length > 0 && !busy && questions.map((q) => <QuestionCard key={q.id} q={q} busy={busy} send={send} />)}
            {options.length > 0 && !busy && (
              <div className="flex flex-wrap gap-1.5">
                {options.map((o, i) => <OptionButton key={i} o={o} busy={busy} send={send} contact={contact} setContact={setContact} onAddress={(prefill) => setAddressFor(prefill ?? {})} />)}
              </div>
            )}
            {busy && (
              <div className="flex justify-start"><div className="rounded-2xl rounded-bl-md bg-gray-100 px-3 py-2 text-xs text-gray-500">
                <span className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:-0.3s]" /><span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400 [animation-delay:-0.15s]" /><span className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400" /><span className="ml-1">thinking…</span></span>
              </div></div>
            )}
            {err && <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{err}</div>}
            <div ref={bottom} />
          </div>
        )}
        {view === 'chat' && (
          <form onSubmit={onSubmit} className="border-t border-gray-100 p-2">
            <div className="flex gap-2">
              <input data-testid="chat-input" className="min-w-0 flex-1 rounded-full border px-3 py-2 text-sm" placeholder="Describe the problem, ask for status…" value={input} onChange={(e) => setInput(e.target.value)} disabled={busy} />
              <button data-testid="chat-send" className="btn btn-primary rounded-full" disabled={busy || !input.trim()}>Send</button>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1 text-[11px]">
              <button type="button" className="btn" disabled={busy} onClick={() => send({ action: 'reset' })}>New request</button>
              <button type="button" className="btn" disabled={busy} onClick={() => setAddressFor({})}>📍 Address</button>
              <button type="button" data-testid="handoff" className="btn" disabled={busy || Boolean(humanCase && humanCase.status !== 'resolved')} onClick={() => send({ action: 'handoff', payload: { reason: 'customer pressed “talk to a human”' }, display: '[talk to a human]' })}>🙋 Talk to a human</button>
              <button type="button" className="btn ml-auto" onClick={newSession} title="Simulate a different customer / device">New session</button>
            </div>
            <div className="mt-1 text-center text-[10px] text-gray-400">simulated demo · no real payment, SMS or dispatch</div>
          </form>
        )}
      </div>
      {switcher && (
        <div className="fixed inset-0 z-[700] flex items-end justify-center bg-black/40 sm:items-center" onClick={() => setSwitcher(false)}>
          <div className="w-full max-w-md space-y-1 rounded-t-2xl bg-white p-4 text-xs shadow-xl sm:rounded-2xl" data-testid="demo-account-sheet" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold">Sign in</div>
            <div className="pb-1 text-gray-500">Your contact details and past repairs are only loaded once you pick who you are. <span className="text-gray-400">(Demo: this stands in for a real login.)</span></div>
            {logins.map((l) => (
              <button key={l.demo_login} data-testid="demo-account-option" className="flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left hover:bg-blue-50" disabled={busy}
                onClick={() => { setSwitcher(false); send({ action: 'identify', payload: { demo_login: l.demo_login }, display: `[signed in as ${l.name}]` }) }}>
                <span className="flex-1 font-medium">{l.name}</span>{l.returning && <span className="text-[10px] text-gray-400">returning</span>}
              </button>
            ))}
            <button className="w-full rounded-lg border px-3 py-2 text-left hover:bg-blue-50" disabled={busy}
              onClick={() => { setSwitcher(false); send({ action: 'identify', payload: { demo_login: `new_${sid.slice(-4)}` }, display: '[signed in as a new customer]' }) }}>I'm new here</button>
            <button className="btn mt-1 w-full justify-center" onClick={() => setSwitcher(false)}>Close</button>
          </div>
        </div>
      )}
      {addressFor && <AddressModal prefill={addressFor} saved={customer?.addresses ?? []} busy={busy} onConfirm={confirmAddress} onCancel={() => setAddressFor(null)}
        onUseSaved={(id) => { setAddressFor(null); send({ action: 'use_saved_address', payload: { address_id: id }, display: '[saved address]' }) }} />}
    </div>
  )
}

function OrderRow({ o }: { o: ChatOrder }) {
  return (
    <Link to={`/customer/orders/${o.order_id}`} className="block rounded-xl border border-gray-200 p-3 hover:bg-blue-50" data-testid="order-row">
      <div className="flex items-center gap-1"><span className="font-mono text-xs font-semibold">{o.order_id}</span><span className={`badge ${statusColor[o.lifecycle_status]}`}>{o.lifecycle_status.replace('_', ' ')}</span>{o.paid_expedite && <span className="badge bg-pink-100 text-pink-800">expedited</span>}</div>
      <div className="mt-1 text-sm">{o.problem}</div>
      <div className="text-xs text-gray-500">{fmtTime(o.window_start)}–{fmtTime(o.window_end)} · {o.address ?? ''}</div>
      <div className="mt-1 text-xs text-gray-800">{o.text}</div>
    </Link>
  )
}

function QuestionCard({ q, busy, send }: { q: OpenQuestion; busy: boolean; send: Send }) {
  return (
    <div className="rounded-xl border border-violet-200 bg-violet-50 p-3 text-xs" data-testid="open-question">
      <div className="mb-1 font-semibold text-violet-900">The scheduling assistant asks:</div>
      <div className="text-gray-800">{q.question}</div>
      <div className="mt-2 flex flex-wrap gap-1">
        {(q.options ?? []).map((op, i) => <button key={i} className="btn" disabled={busy} onClick={() => send({ action: 'answer_question', payload: { question_id: q.id, answer: op.value }, display: `[answer] ${op.label}` })}>{op.label}</button>)}
      </div>
    </div>
  )
}

function OptionButton({ o, busy, send, contact, setContact, onAddress }: { o: ChatOption; busy: boolean; send: Send; contact: { customer_name: string; contact_phone: string }; setContact: (c: { customer_name: string; contact_phone: string }) => void; onAddress: (prefill?: Record<string, unknown>) => void }) {
  const cls = 'btn rounded-full'
  switch (o.type) {
    case 'address': return <button data-testid="opt-address" className={`${cls} btn-primary`} disabled={busy} onClick={() => onAddress(o.prefill)}>📍 Enter address & unit</button>
    case 'saved_address': return <button className={cls} disabled={busy} onClick={() => send({ action: 'use_saved_address', payload: { address_id: o.id }, display: `[saved address] ${o.label}` })}>🏠 {o.label}</button>
    case 'use_saved': return <button data-testid="opt-use-saved" className={`${cls} btn-primary`} disabled={busy} onClick={() => send({ action: 'use_saved', display: '[use saved details]' })}>{o.label}</button>
    case 'new_details': return <button className={cls} disabled={busy} onClick={() => send({ action: 'new_details', display: '[new details]' })}>{o.label}</button>
    case 'catalog': return <button data-testid="opt-catalog" className={cls} disabled={busy} onClick={() => send({ action: 'select_catalog', payload: { catalog_item_id: o.id }, display: `[selected] ${o.label}` })}>🔧 {o.label}</button>
    case 'expedite_now': return <button data-testid="opt-expedite-now" data-now={o.now ? '1' : '0'} className={`${cls} ${o.now ? 'btn-primary' : ''}`} disabled={busy} onClick={() => send({ action: 'set_expedite_now', payload: { now: Boolean(o.now) }, display: `[expedite] ${o.label}` })}>{o.now ? '🚨' : '🕒'} {o.label}</button>
    case 'payment': return <button data-testid="opt-payment" data-paid={o.paid ? '1' : '0'} className={`${cls} ${o.paid ? 'btn-primary' : ''}`} disabled={busy} onClick={() => send({ action: 'confirm_payment', payload: { paid: Boolean(o.paid) }, display: `[payment] ${o.label}` })}>{o.paid ? '💳 ' : ''}{o.label}</button>
    case 'window': {
      // a slot nobody can serve is shown greyed out rather than hidden: the customer sees the real shape of the day
      const off = o.selectable === false
      return <button data-testid="opt-window" data-state={o.state ?? 'available'} data-paid-required={o.paid_required ? '1' : '0'}
        className={`${cls} ${off ? 'cursor-not-allowed opacity-45' : o.paid_required ? 'border-pink-400 text-pink-800' : ''}`}
        disabled={busy || off} title={off ? (o.note ?? 'not available today') : o.paid_required ? 'Already booked — a simulated fee frees it up' : undefined}
        onClick={() => send({ action: 'set_window', payload: { window_start: o.window_start, window_end: o.window_end, technician_id: o.technician_id, paid_required: Boolean(o.paid_required), affected_count: o.affected_count ?? 0, decision: o.decision }, display: `[slot] ${o.label}` })}>
        {o.state === 'paid' ? '💳' : o.state === 'impossible' ? '🚫' : '🕒'} {o.label}</button>
    }
    case 'more_windows': return <button className={cls} disabled={busy} onClick={() => send({ action: 'more_windows', display: '[none of these windows]' })}>{o.label}</button>
    case 'exclude_technician': return <button className={cls} disabled={busy} onClick={() => send({ action: 'exclude_technician', payload: { technician_id: o.technician_id }, display: `[exclude] ${o.label}` })}>🚫 {o.label}</button>
    case 'confirm': return <button data-testid="opt-confirm" className={`${cls} btn-primary`} disabled={busy} onClick={() => send({ action: 'confirm', display: '[confirm]' })}>✓ Confirm & submit</button>
    case 'restart_expedite': return <button className={cls} disabled={busy} onClick={() => send({ action: 'restart_expedite', display: '[expedite] reconsider' })}>🚨 {o.label}</button>
    case 'cancel_order': return <button className={`${cls} btn-danger`} disabled={busy} onClick={() => send({ action: 'cancel_order', payload: { order_id: o.order_id }, display: `[cancel] ${o.order_id}` })}>{o.label}</button>
    case 'expedite_order': return <button data-testid="opt-expedite-order" className={`${cls} btn-primary`} disabled={busy} onClick={() => send({ action: 'expedite_order', payload: { order_id: o.order_id }, display: `[expedite now] ${o.order_id}` })}>💳 {o.label}</button>
    case 'keep_order': return <button data-testid="opt-keep-order" className={cls} disabled={busy} onClick={() => send({ action: 'keep_order', display: '[keep as is]' })}>{o.label}</button>
    case 'complaint': return <button className={cls} disabled={busy} onClick={() => send({ action: 'complaint', payload: { order_id: o.order_id, text: o.text }, display: `[complaint on ${o.order_id}] ${o.text ?? ''}` })}>Complaint about {o.label}</button>
    case 'handoff': return <button data-testid="opt-handoff" className={cls} disabled={busy} onClick={() => send({ action: 'handoff', payload: { reason: o.label, category: o.category }, display: `[talk to a human] ${o.label}` })}>🙋 {o.label}</button>
    case 'geocode': return <button className={cls} disabled={busy} title={`source: ${o.source}`} onClick={() => onAddress({ latitude: o.lat, longitude: o.lon, formatted_address: o.name, source: o.source })}>📍 {o.label}</button>
    case 'location': return <button className={cls} disabled={busy} onClick={() => send({ action: 'select_location', payload: { location_id: o.id }, display: `[area] ${o.label}` })}>📍 {o.label}</button>
    case 'map_pick': return <button className={cls} disabled={busy} onClick={() => onAddress({})}>🗺 Pick on map</button>
    case 'contact_form': return (
      <form className="flex w-full flex-wrap gap-1" data-testid="contact-form" onSubmit={(e) => { e.preventDefault(); send({ action: 'set_contact', payload: contact, display: `[contact] ${contact.customer_name}, ${contact.contact_phone}` }) }}>
        <input className="min-w-0 flex-1 rounded-full border px-3 py-1.5 text-xs" placeholder="Your name" value={contact.customer_name} onChange={(e) => setContact({ ...contact, customer_name: e.target.value })} required />
        <input className="min-w-0 flex-1 rounded-full border px-3 py-1.5 text-xs" placeholder="Phone" value={contact.contact_phone} onChange={(e) => setContact({ ...contact, contact_phone: e.target.value })} required />
        <button className="btn btn-primary rounded-full" disabled={busy}>Save</button>
      </form>
    )
    default: return null
  }
}
