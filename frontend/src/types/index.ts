export type Priority = 'P0' | 'P1' | 'P2' | 'P3'
export type Lifecycle = 'DRAFT' | 'NEEDS_INFO' | 'OPEN' | 'EN_ROUTE' | 'ARRIVED' | 'IN_PROGRESS' | 'COMPLETED' | 'CANCELLED'
export type SchedulingStatus = 'UNASSIGNED' | 'PROPOSED' | 'PENDING_REVIEW' | 'ASSIGNED' | 'UNRESOLVED'

export interface CatalogItem {
  id: string; trade_type: string; problem_name: string; complexity_level: number; repair_duration_minutes: number; catalog_version: string; source_row: number
}
export interface CatalogResponse {
  status: { configured: boolean; active_count: number; catalog_version: string | null; path: string; last_import: Record<string, unknown> | null }
  items: CatalogItem[]
}
export interface LocationRef { id: string; name: string; lat: number; lon: number; area?: string | null }

export interface AssignmentRow {
  id: string; order_id: string; technician_id: string; schedule_version_id: number; origin_location_id: string
  departure: string; arrival: string; service_start: string; service_end: string; travel_minutes: number; waiting_minutes: number
  status: string; locked: boolean; match_score: number | null; score_components: Record<string, { value: number; weight: number; [k: string]: unknown }>
}
export interface Order {
  id: string; customer_ref: string; customer_name: string; contact_phone: string; description: string
  location: LocationRef; catalog_item_id: string
  catalog_snapshot: { trade_type: string; problem_name: string; complexity_level: number; repair_duration_minutes: number; catalog_version: string }
  window_start: string; window_end: string; paid_expedite: boolean
  base_priority: Priority; risk_priority: Priority; effective_priority: Priority
  priority_reasons: { type: string; priority: Priority; detail: string }[]
  lifecycle_status: Lifecycle; scheduling_status: SchedulingStatus; technician_id: string | null; technician_name: string | null; version: number
  departed_at: string | null; arrived_at: string | null; service_started_at: string | null; completed_at: string | null; cancelled_at: string | null
  cancel_reason: string | null; recovery_start: string | null; breach_recorded_at: string | null; can_cancel: boolean; can_abort_execution: boolean; locked: boolean
  assignment: AssignmentRow | null; pending_plan_run_id: string | null
  risks?: Risk[]; plans?: Plan[]
}
export interface RouteStop {
  order_id: string; departure: string; arrival: string; service_start: string; service_end: string; travel_minutes: number; waiting_minutes: number
  locked: boolean; location_id: string; origin_location_id: string; priority: Priority; lifecycle_status: Lifecycle
}
export interface Technician {
  id: string; name: string; skills: Record<string, number>; certifications: string[]; home_location_id: string; current_location_id: string
  lat: number; lon: number; shift_start: string; shift_end: string
  breaks: { start: string; end: string; status?: string; kind?: string; reason?: string; created_by?: string }[]
  unavailable_intervals: { start: string; end: string; reason?: string }[]; status: string; version: number
  anchor: { location_id: string; location_name: string; time: string }; route: RouteStop[]
}
export interface RiskHandling {
  state: 'pending_review' | 'with_human' | 'executing' | 'scheduled' | 'searching' | 'unresolved' | 'closed' | 'none'
  label: string; detail: string; order_status?: string; plan_id?: string; run_id?: string; human_case_id?: string; task_id?: string
  decision_score?: number | null; affected_count?: number; technician_id?: string; technician_name?: string | null; planned_start?: string | null; urgency?: string
}
export interface Risk {
  id: string; type: string; target_order_id: string | null; technician_id: string | null; severity: Priority; status: string
  payload: Record<string, unknown>; effective_time: string; first_seen: string; last_seen: string; resolved_at: string | null; run_id: string | null
  handling?: RiskHandling
}
export interface PlanAssignment {
  order_id: string; technician_id: string; technician_name: string; origin_location_id: string; departure: number; arrival: number; service_start: number; service_end: number
  travel_minutes: number; waiting_minutes: number; locked: boolean; match_score: number | null; score_components: Record<string, { value: number; weight: number; [k: string]: unknown }>
  departure_local?: string; arrival_local?: string; service_start_local?: string; service_end_local?: string
}
export interface DiffEntry {
  order_id: string; change: string; old_tech: string | null; new_tech: string | null; old_start: number | null; new_start: number | null; shift_minutes: number; priority: Priority; counts: boolean
  old_start_local?: string | null; new_start_local?: string | null
}
export interface Plan {
  id: string; run_id: string; target_order_id: string; strategy: string; strategy_tags: string[]; status: string; status_reason: string | null
  decision: string; decision_score: number | null; scores: Record<string, number>; affected_order_ids: string[]
  assignments: PlanAssignment[]; diff: DiffEntry[]
  policy_check: { decision: string; reasons: string[]; decision_score: number | null; threshold: number; authority: { ok: boolean; max_affected: number | null; movable_priorities: string[]; affected_count: number; violations: string[] } | null; over_limit: boolean }
  validation: { ok: boolean; violations: { code: string; message: string }[] }
  metrics: Record<string, unknown> & { target_service_start_local?: string; strategy_tags?: string[]; target_priority?: string; unassigned?: string[]; batch?: boolean }
  base_schedule_version: number; generated_at: string; expires_at: string; explanation: string; approvable: boolean
}
export interface AgentRun {
  id: string; agent: string; trigger: string; target_order_id: string | null; status: string; execution_mode: string; started_at: string; finished_at: string | null
  duration_ms: number | null; steps: { step: string; agent: string; status: string; summary: string; duration_ms?: number; tool_calls: { tool: string; facts: Record<string, unknown> }[]; error?: string }[]
  summary: string; error: string | null; result: Record<string, unknown>
}
export interface Notification { id: string; recipient_ref: string; recipient_type: string; type: string; message: string; order_id: string | null; delivery_mode: string; sim_time: string }
export interface ScheduleVersion { id: number; parent_id: number | null; reason: string; created_at: string; sim_now: string; active: boolean; policy_version: string; route_snapshot_id: string; plan_id: string | null; metrics: Record<string, unknown>; assignment_count: number }
export interface Schedule {
  version: ScheduleVersion | null; assignments: AssignmentRow[]; unassigned_order_ids: string[]
  route_snapshot: { id: string; provider: string; degraded: boolean; reason: string | null }
  kpis: { orders_open: number; assigned: number; unassigned: number; active_risks: number; manual_queue: number; total_travel_minutes: number; urgent_orders: number; pending_review_plans: number; completed: number; cancelled: number }
}
export interface Clock { now: string; timezone: string; running: boolean; scenario_generation: number; scenario_name: string; seed: number; day_origin: string; now_minutes: number }
export interface Health { status: string; app_mode: string; llm_mode: string; llm_provider: string | null; llm_model: string | null; llm_configured: boolean; route_mode: string; osrm_base_url?: string | null; osrm_duration_factor?: number | null; osrm_base_minutes?: number | null; catalog_configured: boolean; catalog_items: number; policy_version: string; timezone: string }
export interface StandbyResponse {
  order_id: string; effective_priority: Priority; has_valid_assignment: boolean; current_assignment: AssignmentRow | null; scheduling_status: string
  candidates: { id: string; technician_id: string; technician_name: string; earliest_start: string; skill_match: { level: number; required: number }; computed_at: string; expires_at: string; valid: boolean }[]; note: string
}
export interface RouteLeg { order_id: string; from_id: string; to_id: string; points: [number, number][]; schematic: boolean; provider: string; minutes: number; provider_minutes: number | null; distance_km: number | null; locked: boolean }
export interface TechnicianRoute { technician_id: string; legs: RouteLeg[]; route_provider: string; degraded: boolean }
export interface ChatOption {
  type: string; id?: string; label?: string; order_id?: string; window_start?: string; window_end?: string; paid?: boolean; text?: string; lat?: number; lon?: number; name?: string; source?: string
  technician_id?: string; earliest_start?: string; incident_id?: string; category?: string; prefill?: Record<string, unknown>
  urgent?: boolean; now?: boolean; paid_required?: boolean; affected_count?: number; decision?: string
  state?: 'available' | 'paid' | 'impossible' | 'unknown'; selectable?: boolean; note?: string
}
export interface ExpediteState {
  status: 'unchanged' | 'pending_review' | 'moved' | 'revert_pending' | 'reverted' | string; summary: string; message?: string; message_at?: string
  original_window_start?: string; original_window_end?: string; original_planned_start?: string | null; new_planned_start?: string; proposed_start?: string | null
  affected_order_ids?: string[]; moved_at?: string
}
export interface ExpeditePreview {
  order_id: string; possible: boolean; earliest_start: string | null; affected_count: number; current_start: string | null; decision?: string | null
  technician_name?: string; text: string; reason?: string
}
export interface ChatOrder {
  order_id: string; lifecycle_status: Lifecycle; scheduling_status: string; effective_priority: Priority; paid_expedite: boolean; can_cancel: boolean
  eta: string | null; technician_name: string | null; window_start: string; window_end: string; problem: string; text: string; cancel_block_reason: string | null
  address?: string | null; report_status?: string | null; expedite?: ExpediteState | null
}
export interface ChatResponse {
  session_id: string; state: string; draft: Record<string, unknown> & { catalog_item?: CatalogItem | null; location?: LocationRef | null; paid_expedite?: boolean; customer_name?: string; contact_phone?: string; window_start?: string; window_end?: string }
  reply: { text: string; options: ChatOption[]; card: Record<string, unknown> | null; order?: ChatOrder; classification?: Record<string, unknown>; human_case?: HumanCase; incident?: SafetyIncident
    history?: { orders: { order_id: string; problem: string; date?: string; technician_name?: string; status?: string }[]; addresses: SavedAddress[]; negative_technicians: { technician_id: string; name?: string; rating?: number }[] } }
  messages: { role: string; text: string; at: string; options?: ChatOption[]; card?: Record<string, unknown> | null; pending?: boolean }[]
  orders: ChatOrder[]; llm: { provider?: string; model?: string; degraded?: boolean; reason?: string | null }; sim_now: string; mode: string
  human_case?: HumanCase | null; open_questions?: OpenQuestion[]; customer?: { id: string; name: string; phone: string; demo_login: string | null; addresses: SavedAddress[] } | null
}
export interface ApiError { code: string; message: string; details: unknown; request_id: string }

// ---------------------------------------------------------------- V3 (three-end closed loop)
export interface CustomerLogin { demo_login: string; name: string; returning: boolean }
export interface AddressResult { name: string; address: string; lat: number; lon: number; source: string; postal: string | null; kind: string | null; score: number | null }
export interface AddressSearch { results: AddressResult[]; provider: string | null; degraded?: boolean; reason?: string | null; cached?: boolean; note?: string }
export interface SavedAddress {
  id: string; location_id: string; formatted_address: string; postal_code: string | null; building_name: string | null; street_address: string | null
  unit_number: string | null; unit_not_applicable: boolean; latitude: number; longitude: number; source: string; is_default?: boolean; last_used_at?: string | null
}
export interface HumanCase {
  id: string; source: string; category: string; urgency: string; status: string; customer_id: string | null; session_id: string | null; order_id: string | null
  incident_id: string | null; reason_summary: string; evidence_refs: string[]; attempted_actions: unknown[]; unresolved_questions: string[]
  suggested_next_action: string | null; assignee: string | null; resolution: string | null
  replies: { author: string; text: string; at: string; role?: string }[]; escalations: unknown[]; created_at: string; updated_at: string; resolved_at: string | null
  conversation?: { role: string; text: string; at?: string }[]; order?: { id: string; status: string; scheduling: string; priority: Priority; problem: string; window: [string, string] } | null
  agent_tasks?: AgentTask[]
  reschedule_request?: { id: string; status: string; reason: string; window: [string, string] }
  reschedule_preview?: ReschedulePreview
}
export interface ReschedulePreview {
  request_id?: string; order_id?: string; feasible?: boolean; reason?: string; disturbs_others?: boolean
  current_window?: [string, string]; current_start?: string | null; current_technician?: string | null
  requested_window?: [string, string]; new_start?: string; technician_name?: string; affected_count?: number
  affected_order_ids?: string[]; decision_score?: number; decision?: string; note?: string
}
export interface SafetyIncident {
  id: string; danger_type: string; status: string; description: string; customer_id: string | null; session_id: string | null; order_id: string | null
  known_location: Record<string, unknown>
  human_case_id: string | null; resolution: string | null; created_at: string; resolved_at: string | null
}
export interface OpenQuestion { id: string; kind: string; question: string; options: { value: string; label: string }[] }
export interface CustomerOrderDetail extends ChatOrder {
  address: string | null; report_status: string | null; description: string; address_full: Record<string, unknown> | null; customer_name: string; contact_phone: string
  catalog_snapshot: Order['catalog_snapshot']; created_at: string; progress: { event: string; at: string; source: string }[]
  risks: { type: string; severity: Priority }[]; human_case: HumanCase | null
  feedback: { rating: number; target: string; reasons: string[]; comment: string } | null
  reschedule_requests: { id: string; status: string; window: [string, string] }[]
  tracking: Tracking
  actions: { cancel: boolean; expedite: boolean; keep_original_time: boolean; complain: boolean; rate: boolean; reschedule: boolean; handoff: boolean }
}
export interface Tracking {
  active: boolean; reason?: string; technician?: { id: string; name: string; status: string }; current_coords?: [number, number]; moving?: boolean; leg_progress?: number | null
  eta?: string | null; planned_service_start?: string | null; route_points?: [number, number][]; destination?: { lat: number; lon: number }; source?: string; sim_time?: string
}
export interface TechPosition {
  technician_id: string; name: string; status: string; sim_mode: string; current_coords: [number, number]; moving: boolean; leg_progress: number | null
  current_service_location: { order_id: string; location_id: string; name: string; status: string | null } | null
  next_destination: { order_id: string; location_id: string; name: string; lat: number; lon: number; planned_departure: string; eta: string } | null
  current_leg: { order_id: string; from_location_id: string; to_location_id: string; progress: number | null } | null
  source: string; sim_time: string
}
export interface Positions { sim_now: string; running: boolean; technicians: TechPosition[] }
export interface TechJob {
  order_id: string; lifecycle_status: Lifecycle; priority: Priority; problem: string; complexity: number; duration_minutes: number; description: string; window: [string, string]
  planned: { departure: string; arrival: string; service_start: string; service_end: string; travel_minutes: number; waiting_minutes: number }
  actual: { departed_at: string | null; arrived_at: string | null; service_started_at: string | null; completed_at: string | null }
  address: { formatted_address: string; unit_number: string | null; unit_not_applicable: boolean | null; postal_code: string | null; building_name: string | null; lat: number; lon: number; location_id: string }
  customer_name: string; contact_phone: string; locked: boolean; report_status: string | null
  report: { id: string; status: string; actual_problem_text: string; resolution: string; notes: string; actual_start_at: string | null; actual_end_at: string | null } | null
  history_hint: { wording: string; count: number; trade_type: string; window_days: number; records: { order_id: string; date: string; problem: string; actual_problem: string | null; resolution: string | null }[] } | null; next_action: string | null
}
export interface TechToday {
  technician: { id: string; name: string; status: string; skills: Record<string, number>; sim_mode: string; shift: [string, string]; home_location_id: string }
  sim_now: string
  timeline: { kind: string; start: string; end: string; order_id?: string; priority?: Priority; status?: string; id?: string; reason?: string | null }[]
  jobs: TechJob[]; current_job: TechJob | null; next_job: TechJob | null
  break_facts: { work_minutes_since_break: number; last_break_end: string | null; level: string; planned_break_ahead: { id: string; start: string; end: string; status: string; reason?: string }[]; in_break: string | null }
  mode_note: string
  notices?: { id: string; type: string; order_id: string | null; message: string; at: string | null }[]
}
export interface TechLogin { id: string; name: string; status: string; sim_mode: string }
export interface AgentTrace {
  seq: number; phase: string; tool: string | null; thought: string | null; args: Record<string, unknown> | null; result_status: string | null
  reason_codes: string[]; data: Record<string, unknown> | null; evidence_refs: string[]; duration_ms: number | null; decided_by: string | null
  sim_time: string | null; at: string
}
export interface AgentSkillRef { name: string; title: string; source: string; tools: string[] }
export interface AgentTask {
  id: string; role: string; goal: string; status: string; order_id: string | null; technician_id: string | null; customer_id: string | null; session_id: string | null
  incident_id: string | null; execution_mode: string; tool_budget_used: number; search_budget_used: number; wakeup_reason: string | null; human_case_id: string | null
  pending_question_id: string | null; tried_plan_ids: string[]; evidence_refs: string[]; outcome: Record<string, unknown> | null; facts_summary: Record<string, unknown> | null
  parent_task_id: string | null; child_task_ids: string[]; delegation_depth: number; waiting_child_id: string | null; skill: AgentSkillRef | null
  created_at: string; updated_at: string
  traces?: AgentTrace[]
}
export interface AgentSkill {
  name: string; title: string; description: string; tools: string[]; withheld_by_skill: string[]; declared_but_not_permitted: string[]
  max_tool_calls: number | null; max_searches: number | null; escalate_when: string[]; body: string; source: string
}
export interface AgentScorecard {
  scenario_generation: number; tasks: number; note?: string
  by_role?: Record<string, number>; by_status?: Record<string, number>; skills_loaded?: string[]; skills_never_used?: string[]
  autonomy?: { label: string; resolved: number; escalated: number; rate_pct: number | null }
  cost?: { label: string; avg_tool_calls: number | null; avg_searches: number | null; tool_call_budget: number; budget_exhausted: number; budget_exhausted_pct: number | null }
  discipline?: { label: string; total_calls: number; wasted_calls: number; wasted_pct: number | null; repeated_searches: number; reason_codes: Record<string, number> }
  handover_quality?: { label: string; escalations: number; complete: number; rate_pct: number | null; missing_fields: Record<string, number> }
  collaboration?: { label: string; delegations: number; child_tasks: number; max_depth: number; pairs: Record<string, number> }
  transparency?: { label: string; steps: number; with_reason: number; rate_pct: number | null }
  execution_mode?: { label: string; by_mode: Record<string, number>; decided_by: Record<string, number>; degraded_tasks: number }
}
export interface DurationReport {
  report: Record<string, unknown> | null
  recent_observations: { order_id: string; problem: string; baseline: number; actual: number; interruptions: number; quality: string; source: string; prediction: number | null; prediction_version: string | null; observed_at: string }[]
}
export interface ScenarioInfo { name: string; label?: string; description?: string; technicians: number; orders: number }
