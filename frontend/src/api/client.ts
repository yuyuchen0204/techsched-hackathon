import type { AddressSearch, AgentRun, AgentScorecard, AgentSkill, AgentTask, CatalogResponse, ChatOrder, ChatResponse, Clock, CustomerLogin, CustomerOrderDetail, DurationReport, ExpeditePreview, Health, HumanCase, LocationRef, Notification, Order, Plan, Positions, Risk, SafetyIncident, ScenarioInfo, Schedule, ScheduleVersion, StandbyResponse, TechLogin, TechToday, Technician, TechnicianRoute, Tracking } from '../types'

export class HttpError extends Error {
  status: number
  code: string
  details: unknown
  constructor(status: number, code: string, message: string, details: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

const BASE = import.meta.env.BASE_URL.replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { 'content-type': 'application/json' }, ...init })
  if (!res.ok) {
    let body: { error?: { code: string; message: string; details: unknown } } = {}
    try { body = await res.json() } catch { /* ignore */ }
    const err = body.error
    throw new HttpError(res.status, err?.code ?? `http_${res.status}`, err?.message ?? res.statusText, err?.details)
  }
  return res.json() as Promise<T>
}

const get = <T,>(path: string) => request<T>(path)
const post = <T,>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) })

export const api = {
  health: () => get<Health>('/health'),
  clock: () => get<Clock>('/api/demo/clock'),
  advance: (minutes: number) => post<{ clock: Clock; fired_events: unknown[]; dispatched: unknown[] }>('/api/demo/clock/advance', { minutes }),
  control: (running: boolean) => post<Clock>('/api/demo/clock/control', { running }),
  reset: (scenario = 'main') => post<{ summary: Record<string, unknown>; clock: Clock }>('/api/demo/reset', { scenario }),
  scan: () => post<Record<string, unknown>>('/api/demo/scan'),
  catalog: (q?: string) => get<CatalogResponse>(`/api/catalog${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  reloadCatalog: () => post<Record<string, unknown>>('/api/catalog/reload'),
  locations: () => get<LocationRef[]>('/api/locations'),
  orders: (params?: Record<string, string>) => get<Order[]>(`/api/orders${params ? `?${new URLSearchParams(params)}` : ''}`),
  order: (id: string) => get<Order>(`/api/orders/${id}`),
  createOrder: (body: unknown) => post<{ order: Order; dispatch: unknown }>('/api/orders', body),
  cancelOrder: (id: string, body: { customer_ref?: string; reason?: string; actor?: string; idempotency_key?: string }) => post<Record<string, unknown>>(`/api/orders/${id}/cancel`, body),
  abortExecution: (id: string, body: { reason: string; outcome: 'reschedule' | 'cancel' }) => post<Record<string, unknown>>(`/api/orders/${id}/abort-execution`, { ...body, actor: 'dispatcher' }),
  executionEvent: (id: string, event: string) => post<Record<string, unknown>>(`/api/orders/${id}/execution-events`, { event, actor: 'technician_panel' }),
  standby: (id: string) => get<StandbyResponse>(`/api/orders/${id}/standby`),
  technicians: () => get<Technician[]>('/api/technicians'),
  schedule: () => get<Schedule>('/api/schedules/current'),
  versions: () => get<ScheduleVersion[]>('/api/schedules/versions'),
  version: (id: number) => get<ScheduleVersion & { snapshot: unknown[] }>(`/api/schedules/${id}`),
  initial: () => post<Record<string, unknown>>('/api/scheduling/initial', {}),
  dispatch: (order_id: string) => post<Record<string, unknown>>('/api/scheduling/dispatch', { order_id, trigger: 'manual_dispatch' }),
  risks: () => get<Risk[]>('/api/risks'),
  plans: (status?: string) => get<Plan[]>(`/api/plans${status ? `?status=${status}` : ''}`),
  plan: (id: string) => get<Plan>(`/api/plans/${id}`),
  approve: (id: string, body: { actor?: string; reason?: string; idempotency_key?: string; expected_versions?: Record<string, number> }) => post<Record<string, unknown>>(`/api/plans/${id}/approve`, body),
  reject: (id: string, body: { actor?: string; reason?: string; idempotency_key?: string }) => post<Record<string, unknown>>(`/api/plans/${id}/reject`, body),
  recompute: (id: string) => post<Record<string, unknown>>(`/api/plans/${id}/recompute`),
  runs: () => get<AgentRun[]>('/api/agent-runs'),
  run: (id: string) => get<AgentRun & { plans: Plan[] }>(`/api/runs/${id}`),
  notifications: (recipient?: string) => get<Notification[]>(`/api/notifications${recipient ? `?recipient_ref=${encodeURIComponent(recipient)}` : ''}`),
  event: (body: Record<string, unknown>) => post<Record<string, unknown>>('/api/events', body),
  chat: (body: { session_id: string; message?: string; action?: string; payload?: Record<string, unknown> }) => post<ChatResponse>('/api/chat/messages', body),
  chatSession: (id: string) => get<ChatResponse & { slots: { window_start: string; window_end: string; label: string }[] }>(`/api/chat/sessions/${id}`),
  technicianRoute: (id: string) => get<TechnicianRoute>(`/api/routes/technician/${id}`),
  resolvePoint: (lat: number, lon: number, name?: string) => post<{ location: LocationRef; snapped: boolean; note: string; arbitrary_points_supported: boolean; postal_code?: string | null; formatted_address?: string | null }>('/api/locations/resolve', { lat, lon, name }),
  geometry: (from: string, to: string) => get<{ points: [number, number][]; schematic: boolean; provider: string; minutes?: number }>(`/api/routes/geometry?from_id=${from}&to_id=${to}`),
  // ---- V3: customer app
  customerLogins: () => get<CustomerLogin[]>('/api/customer/logins'),
  addressSearch: (q: string) => get<AddressSearch>(`/api/customer/address/search?q=${encodeURIComponent(q)}`),
  sessionOrders: (sid: string) => get<ChatOrder[]>(`/api/customer/session/${sid}/orders`),
  customerOrder: (id: string, sid: string) => get<CustomerOrderDetail>(`/api/customer/orders/${id}?session_id=${encodeURIComponent(sid)}`),
  customerCancel: (id: string, sid: string, reason?: string) => post<Record<string, unknown>>(`/api/customer/orders/${id}/cancel`, { session_id: sid, reason }),
  customerExpedite: (id: string, sid: string) => post<{ message?: string; idempotent?: boolean; outcome?: string }>(`/api/customer/orders/${id}/expedite`, { session_id: sid }),
  customerExpeditePreview: (id: string, sid: string) => get<ExpeditePreview>(`/api/customer/orders/${id}/expedite-preview?session_id=${encodeURIComponent(sid)}`),
  customerKeepOriginalTime: (id: string, sid: string) => post<{ message?: string; idempotent?: boolean; outcome?: string }>(`/api/customer/orders/${id}/keep-original-time`, { session_id: sid }),
  customerComplaint: (id: string, sid: string, text: string) => post<ChatResponse>(`/api/customer/orders/${id}/complaint`, { session_id: sid, text }),
  customerFeedback: (id: string, sid: string, body: { rating: number; target: string; reasons: string[]; comment: string }) => post<Record<string, unknown>>(`/api/customer/orders/${id}/feedback`, { session_id: sid, ...body }),
  customerHandoff: (id: string, sid: string, reason: string) => post<ChatResponse>(`/api/customer/orders/${id}/handoff`, { session_id: sid, reason }),
  customerReschedule: (id: string, sid: string, body: { window_start: string; window_end: string; reason: string }) => post<Record<string, unknown>>(`/api/customer/orders/${id}/reschedule`, { session_id: sid, ...body }),
  tracking: (id: string, sid: string) => get<Tracking>(`/api/customer/orders/${id}/tracking?session_id=${encodeURIComponent(sid)}`),
  // ---- V3: technician app
  techLogins: () => get<TechLogin[]>('/api/technician/logins'),
  techToday: (id: string) => get<TechToday>(`/api/technician/${id}/today`),
  techAction: (id: string, oid: string, event: 'depart' | 'arrive' | 'start' | 'complete') => post<Record<string, unknown>>(`/api/technician/${id}/orders/${oid}/${event}`),
  techMode: (id: string, mode: 'auto' | 'manual') => post<Record<string, unknown>>(`/api/technician/${id}/mode`, { mode }),
  techLeave: (id: string, body: { end?: string; reason: string; idempotency_key?: string }) => post<Record<string, unknown>>(`/api/technician/${id}/leave`, body),
  techReport: (id: string, oid: string, body: Record<string, unknown>) => post<Record<string, unknown>>(`/api/technician/${id}/orders/${oid}/report`, body),
  techBreak: (id: string, minutes: number, reason: string) => post<Record<string, unknown>>(`/api/technician/${id}/break`, { minutes, reason }),
  // ---- V3: dispatcher
  positions: () => get<Positions>('/api/positions'),
  humanCases: (status?: string) => get<HumanCase[]>(`/api/human-cases${status ? `?status=${status}` : ''}`),
  humanCase: (id: string) => get<HumanCase>(`/api/human-cases/${id}`),
  declineReschedule: (id: string, author: string, resolution: string) => post<Record<string, unknown>>(`/api/reschedule-requests/${id}/decline`, { resolution, author }),
  takeCase: (id: string, author: string) => post<HumanCase>(`/api/human-cases/${id}/take`, { text: '', author }),
  replyCase: (id: string, text: string, author: string) => post<HumanCase>(`/api/human-cases/${id}/reply`, { text, author }),
  resolveCase: (id: string, resolution: string, author: string) => post<HumanCase>(`/api/human-cases/${id}/resolve`, { resolution, author }),
  approveReschedule: (id: string, author: string) => post<Record<string, unknown>>(`/api/reschedule-requests/${id}/approve`, { resolution: 'approved', author }),
  incidents: () => get<SafetyIncident[]>('/api/safety-incidents'),
  resolveIncident: (id: string, resolution: string, author: string) => post<SafetyIncident>(`/api/safety-incidents/${id}/resolve`, { resolution, author }),
  agentTasks: () => get<AgentTask[]>('/api/agent-tasks'),
  agentTask: (id: string) => get<AgentTask>(`/api/agent-tasks/${id}`),
  runTask: (id: string) => post<AgentTask>(`/api/agent-tasks/${id}/run`),
  agentSkills: () => get<{ skills: AgentSkill[]; directory: string }>('/api/agent-skills'),
  reloadSkills: () => post<{ reloaded: string[] }>('/api/agent-skills/reload'),
  agentScorecard: () => get<AgentScorecard>('/api/agent-scorecard'),
  supervise: (order_ids: string[], trigger = 'manual') => post<AgentTask>('/api/agent-tasks/supervise', { order_ids, trigger }),
  scenarios: () => get<{ scenarios: ScenarioInfo[]; current: { scenario: string; generation: number; seed: number } }>('/api/demo/scenarios'),
  loadSummary: () => get<{ utilisation_by_technician: Record<string, number>; mean_utilisation: number; unassigned: string[]; note: string }>('/api/demo/load-summary'),
  resetWithSeed: (scenario: string, seed?: number) => post<{ summary: Record<string, unknown>; clock: Clock }>('/api/demo/reset', seed == null ? { scenario } : { scenario, seed }),
  durationReport: () => get<DurationReport>('/api/dev/duration-report'),
  buildDurationReport: () => post<Record<string, unknown>>('/api/dev/duration-report/build'),
  toolTraces: () => get<{ task_id: string; seq: number; phase: string; tool: string | null; status: string | null; reason_codes: string[]; duration_ms: number | null; decided_by: string | null; at: string }[]>('/api/dev/tool-traces'),
}
export const techStatusColor: Record<string, string> = { AVAILABLE: '#16a34a', EN_ROUTE: '#4f46e5', ARRIVED: '#7c3aed', BUSY: '#9333ea', IN_PROGRESS: '#9333ea', BREAK: '#0891b2', UNAVAILABLE: '#dc2626', OFF_SHIFT: '#6b7280' }

export const fmtTime = (iso: string | null | undefined) => (iso ? iso.slice(11, 16) : '--:--')
export const minutesOfDay = (iso: string) => parseInt(iso.slice(11, 13)) * 60 + parseInt(iso.slice(14, 16))
export const priorityColor: Record<string, string> = { P0: 'bg-red-600 text-white', P1: 'bg-orange-500 text-white', P2: 'bg-amber-300 text-gray-900', P3: 'bg-gray-200 text-gray-800' }
export const statusColor: Record<string, string> = {
  OPEN: 'bg-blue-100 text-blue-800', EN_ROUTE: 'bg-indigo-100 text-indigo-800', ARRIVED: 'bg-violet-100 text-violet-800', IN_PROGRESS: 'bg-purple-100 text-purple-800',
  COMPLETED: 'bg-green-100 text-green-800', CANCELLED: 'bg-gray-200 text-gray-500', UNASSIGNED: 'bg-yellow-100 text-yellow-800', PENDING_REVIEW: 'bg-amber-100 text-amber-800',
  ASSIGNED: 'bg-green-50 text-green-700', UNRESOLVED: 'bg-red-100 text-red-800', PROPOSED: 'bg-sky-100 text-sky-800',
}

/** Split a route leg at `frac` of its length → [already driven, still to drive].
 *  Mirrors position_service._interpolate on the backend (same distance weighting with a cos(lat) correction), so the
 *  trimmed line and the gliding marker stay on the same point instead of drifting apart. */
export function splitPolyline(points: [number, number][], frac: number): [[number, number][], [number, number][]] {
  if (points.length < 2) return [[], points]
  if (frac <= 0) return [[], points]
  if (frac >= 1) return [points, [points[points.length - 1]]]
  const seg = points.slice(0, -1).map((p, i) =>
    Math.hypot(points[i + 1][0] - p[0], (points[i + 1][1] - p[1]) * Math.cos((p[0] * Math.PI) / 180)))
  const total = seg.reduce((a, b) => a + b, 0) || 1
  let acc = 0
  const target = frac * total
  for (let i = 0; i < seg.length; i++) {
    if (acc + seg[i] >= target) {
      const t = seg[i] ? (target - acc) / seg[i] : 0
      const cut: [number, number] = [points[i][0] + (points[i + 1][0] - points[i][0]) * t,
                                     points[i][1] + (points[i + 1][1] - points[i][1]) * t]
      return [[...points.slice(0, i + 1), cut], [cut, ...points.slice(i + 1)]]
    }
    acc += seg[i]
  }
  return [points, [points[points.length - 1]]]
}
