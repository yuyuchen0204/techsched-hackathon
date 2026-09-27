# TechSched Business Proposal

| Item | Value |
|---|---|
| Product | TechSched — intelligent dispatch for on-site repair |
| In one line | Lets a 5–20 technician repair company run routine dispatch with zero human involvement, and hands the decisions that do need a person to that person together with the reasoning |
| Team / team code | *(to be filled in)* |
| Code repository | https://github.com/yuyuchen0204/techsched-hackathon |
| Live system | https://byyyc.com/techsched/ |
| Technical design document | `docs/TechSched-Technical-Design.pdf` (architecture, agent workflows, algorithms, security, deployment, evaluation) |
| Submission date | *(to be filled in)* |

> **This document complements the technical design document.** It answers why the problem is worth solving, for whom
> value is created, how that value is measured, and whether it can be deployed. Technical detail — architecture
> diagrams, algorithms, tool definitions, prompts and tests — lives in the technical design document and is cited here
> only where it supports a business claim.
>
> **About the numbers.** Two kinds appear. Those marked **[measured]** come from the evaluations and tests in the code
> repository and can be reproduced. Those marked **[assumed]** are our estimates of the target customer's operations,
> not yet validated in the field; they are stated explicitly so the reasoning can be checked and corrected.

## Contents

| Chapter | Title |
|---|---|
| 1 | One Business Story |
| 2 | Problem and Opportunity |
| 3 | The Solution in Business Terms |
| 4 | Business Value |
| 5 | Impact and Measurable Outcomes |
| 6 | Feasibility |
| 7 | Scalability |
| 8 | Commercial Model and Route to Market |
| 9 | Claims and Demonstration Evidence |
| 10 | Assumptions and Limitations Register |

---

# 1. One Business Story

**The problem we are solving.** A repair company with 10 technicians takes 20–30 jobs a day. What actually consumes people is not producing the schedule but what happens after it: a technician calls in sick, a customer chases, the previous job overruns. Every one of those changes means somebody has to work out again who can go, whether sending them will make someone else late, and how many phone calls that takes.

**Who experiences it.** A company this size normally has no dedicated dispatch role. The work falls to the owner or an administrator, who takes calls while looking for a technician on a spreadsheet, judges from experience whether they will make it in time, and leaves no record of what was changed or why.

**What the agents create.** TechSched takes over three things: understanding the customer, deciding whether a job can actually be scheduled, and working out who a change would affect. Routine orders run end to end with nobody involved. When something changes, the system re-plans within the authority the company has configured and presents the result as one card carrying a decision score, the list of affected orders and a line-by-line diff — so the dispatcher's job becomes approve or reject, not re-derive. When the situation is beyond that authority or has no feasible answer, the system does not fail silently: it hands over a case with the actions it already tried.

**The outcomes we expect.** Less time spent on dispatch, faster response to changes, a higher on-window arrival rate, and a share of human involvement that can be tracked month by month. On the part the system can already measure, **the number of rule violations in committed plans is always zero** [measured].

---

# 2. Problem and Opportunity

## 2.1 Target Customer and Stakeholders

| Stakeholder | Role in this process | What they care about |
|---|---|---|
| Owner / administrator (dispatch) | Takes bookings, assigns work, handles changes | Whether any job cannot be placed today, which ones need my decision, whether a change will upset a customer |
| Field technician | Carries out the visit | Where the next job is, when to leave, the unit number, why a job was inserted |
| Reporting customer | Requests the repair, waits | When somebody will come, whether they can fix it, whether it can be moved or expedited |
| Owner (as a business) | Looks at results | Labour spent on dispatch, on-time rate, cost per job, whether volume can grow without new headcount |

Target profile: a Singapore-based on-site repair company with 5–20 technicians, 15–40 jobs a day, covering two or more trades. That size falls within Singapore's official definition of a small or medium enterprise (annual turnover not exceeding S$100 million, or no more than 200 employees).

## 2.2 Today's Process and Its Pain Points

| Pain point | How it shows up | Business consequence |
|---|---|---|
| Feasibility is estimated | At scheduling time there is no way to confirm the technician will arrive in time or hold the right skill | Late arrivals and repeat visits; customer complaints |
| Change has no boundary | Several customers' appointments get moved to rescue one urgent job, purely on judgement | Damage to long-standing customers, with no way to review it afterwards |
| Decisions are not traceable | A change is made and that is the end of it; no version, no reason | Disputes cannot be settled; experience cannot be accumulated |
| Incomplete information | Missing unit number, unclear problem description | A technician makes a wasted trip |
| Quality cannot be reproduced | Dispatch quality equals one person's condition that day | The company cannot grow past that person |

## 2.3 Why It Is Worth Solving

- **It happens constantly.** Change is not an edge case. Our evaluation scenarios contain six classes of event in a single day — new insertions, paid expedites, technician cancellations and more [measured: 6.1 events per random seed]. In real operations, leave, reschedules and traffic delays are routine.
- **The cost is immediate.** One badly handled change costs a customer their time, a technician half a day, and possibly a bad review.
- **Without solving it the company cannot grow.** Dispatch capability is tied to one individual; at 20 technicians that person becomes the constraint.

## 2.4 The Opportunity

Field-service management software is a growing segment; several research firms put the global market for 2026 between US$5.6 billion and US$6.1 billion, with a compound annual growth rate of roughly 14–15%. But the pricing and implementation complexity of those systems target mid-size and large enterprises, and **repair companies with 5–20 technicians are still working with a phone and a spreadsheet.** Large language models have, for the first time, made "turn what the customer said into a structured work order" cheap enough for a product aimed at that size of customer to be viable.

---

# 3. The Solution in Business Terms

## 3.1 What the System Does

Three front ends over one back office:

| Front end | Used by | What it does |
|---|---|---|
| Customer app | Reporting customers | Book by conversation, confirm the address, choose a window that is **genuinely available**, track progress, expedite or reschedule |
| Technician app | Field technicians | See the next job (with unit number and customer history), report execution state, take leave, declare a rest |
| Dispatch console | Owner / administrator | See the day's schedule and risks, approve the plans that need a decision, work the human queue |

Behind them are three agents and a deterministic scheduling engine. **The key product judgement is that the model understands and chooses, while rules decide feasibility and authority.** Skill matching, repair duration, whether something may execute automatically and how many orders may be moved are all determined by code and by the company's own configuration, not by the model.

## 3.2 Before and After

<figure class="fig fig-chart">
<img src="handbook-assets/en/bp-workflow.svg" alt="Figure 1 The dispatch process, today vs with TechSched">
<figcaption>Figure 1　The same process under both ways of working. In the right-hand column only the last step keeps a person — and that step is approve or reject, not re-derive.</figcaption>
</figure>

## 3.3 Where the Human Stays

The boundary of automation is explicit and configurable, not a matter of the model's discretion.

<figure class="fig fig-chart">
<img src="handbook-assets/en/bp-decision-routing.svg" alt="Figure 2 Where every dispatch decision ends up">
<figcaption>Figure 2　The three destinations of a dispatch decision and the conditions that send it to each. The rules live in one configuration file; the company can move the boundary, but no configuration can bypass the feasibility check or the authority limits.</figcaption>
</figure>

For the owner this means three things:

1. **The degree of automation is adjustable.** To be more conservative, raise the score threshold for automatic execution and more plans go to review.
2. **High-risk decisions always carry a signature.** Whenever an urgent job needs to move another customer's appointment, dispatcher approval is mandatory no matter how high the score [measured: guaranteed by the order of the policy ruling and covered by tests].
3. **The system never pretends it coped.** With no feasible plan it produces an evidenced case for a person rather than going quiet or retrying indefinitely.

---

# 4. Business Value

Value is set out along the six dimensions. Items marked [measured] are behaviours the system already has and that can be verified; items marked [assumed] are the operational effects inferred from them, to be validated in a pilot.

| Dimension | What the agents do | Effect on the business |
|---|---|---|
| **Productivity** | A routine order runs from the customer's description to a dispatched job with nobody involved [measured: the fast path completes within one request and creates no human task] | Dispatch staff move from handling every job to handling only exceptions [assumed: frees roughly 60% of dispatch time] |
| **Cost** | Dispatch decisions are made by deterministic code; the model is used only for conversation and classification [measured] | Model cost grows with the **number of conversations**, not with schedule complexity — more and harder jobs do not raise unit cost |
| **Revenue** | Expediting is a chargeable feature with transparent consequences: before payment the customer is told how many normal jobs will move and whether dispatcher confirmation is needed [measured] | A revenue line tied directly to the scheduling capability [assumed] |
| **Service** | The customer gets "09:30–11:00, Bala expected at 10:33" instead of "this afternoon", and an explanation whenever they are moved [measured] | Fewer chasing calls and complaints [assumed] |
| **Risk** | Hard constraints and authority are re-checked by an independent validator; over-authority plans have no approval control at all [measured: zero violations in committed plans across both evaluations] | Fewer promises the company cannot keep, and fewer disputes arising from them |
| **Scale** | The same rules handle 12-, 20- and 32-order scenarios, with a mean solve time of 0.9 milliseconds [measured] | Growth in volume does not require a proportional increase in dispatch staff |

---

# 5. Impact and Measurable Outcomes

## 5.1 KPI Design

Each KPI states where in the system it is read from, so a pilot can take the figures directly rather than relying on impressions.

| Business outcome | KPI | Baseline (today) | Target | Where it is read |
|---|---|---|---|---|
| Reduce manual work | Share of routine orders needing a person | [assumed] 100%; every job passes through a person | < 10% | Agent scorecard `autonomy.rate_pct` |
| Improve productivity | Time to handle one change | [assumed] about 15 minutes (re-derive plus phone calls) | < 3 minutes (approve one card) | Approval event timestamps (plan generated → approved) |
| Improve customer service | On-window start rate for the original window | [assumed] no record today; judged from complaints | Visible monthly and improving | `on_window_rate` from evaluation and execution events |
| Reduce risk | Rule violations in committed plans | [assumed] unknowable; no validation exists | Always 0 | `hard_or_authority_violations` [measured: already 0] |
| Improve response | Urgent jobs left unserved | [assumed] no definition today | Declining monthly | `urgent_unserved` |
| Support growth | Technicians supported per dispatcher | [assumed] about 10 | 20 or more | Technician count ÷ dispatch FTE |

## 5.2 A Worked Value Model

For a company with 10 technicians. **Every input states its source**, so the company can substitute its own figures and recompute.

| Input | Value | Source |
|---|---|---|
| Technicians | 10 | Assumed (target profile) |
| Jobs per day | 25 | Assumed (same density as the 8-technician, 20-order demonstration scenario) |
| Working days per month | 26 | Assumed |
| Dispatch effort today | 2.5 hours a day (about 0.3 FTE) | Assumed |
| Share of that spent on routine dispatch | 60% | Assumed |
| Human involvement in routine orders | 0 | **Measured** (the fast path creates no human task) |
| Time to handle one change | 15 minutes → 3 minutes | The first assumed; the second an estimate of approving one card |

**Result**: dispatch effort falls from 2.5 hours a day to about 0.9, roughly 42 hours a month. At an assumed fully loaded dispatcher rate of S$18 an hour that is about **S$750 of labour released per month** — and, more importantly, that time was previously unavailable for taking bookings or looking after customers.

> This is a **model, not a promise**. Its purpose is to break the claim into inputs that can each be challenged. The first task of a pilot is to replace this table with real data and, after 4–8 weeks, read the KPIs in 5.1 against it.

## 5.3 Evidence Already Measured

The figures below come from the offline evaluations in the code repository and can be reproduced with `POST /api/evaluations`. They run on synthetic worlds and are used for like-for-like comparison under a common baseline; they are not a quantified claim about real business returns.

<figure class="fig fig-chart">
<img src="handbook-assets/en/chart-eval-v2.svg" alt="Figure 3 Dispatch-strategy comparison">
<figcaption>Figure 3　Nearest feasible insertion compared with this system's strategy over the same initial schedule and event sequence. Each of the four panels covers one measure.</figcaption>
</figure>

| Claim verified | Evidence |
|---|---|
| Better service of urgent jobs | Urgent events left unserved fall from 6/10 to 3/10 |
| The cost is quantified and shown | Total travel +115 minutes and 6 existing orders affected (3 technician changes, 205 minutes of shift) — the system shows this trade-off to the dispatcher rather than hiding it |
| The rules are not broken | Across both strategies and three load scenarios, hard-constraint and authority violations in committed plans are **zero** |
| Performance is not the constraint | Mean solve time 0.9 ms, maximum 33 ms, against a 3–5 second budget |
| The agents do not run away | Under a real model, five tasks each used 4–5 tool calls and all ended in an evidenced human case; no loops, no exhausted budgets |
| The system actually runs | 128 backend tests and 42 three-end assertions all pass; the deployment is reachable |

---

# 6. Feasibility

## 6.1 Data and Knowledge Needed to Maintain It

This is the part of adoption cost most often underestimated, so it is set out separately.

| What is needed | Who maintains it | Effort | How we handle it |
|---|---|---|---|
| Repair catalogue (trade, problem, complexity, standard duration) | The company's technical lead | Compile once, then top up quarterly | One CSV; 46 records already cover 10 trades. The import reports the actual headers, the field mapping and any bad rows, and **conflicting duplicate keys abort the import rather than being guessed** |
| Technician roster and skill levels | The owner | Enter once, update on staff changes | One JSON or form. Skill level is a hard dispatch condition, so **an error shows up as a job that cannot be assigned, never as a silent downgrade** |
| Business rules (priority, authority, approval threshold, rest limits) | The owner | Set once, rarely changed | Centralised in one configuration file; changing a rule needs no code change |
| Customers and history | Accumulated automatically | None | Completed jobs become history and preferences |

**When the catalogue is missing the system refuses to create orders rather than falling back to an invented one** [measured]. That is a deliberate choice: better not to work at all than to promise a time derived from a guessed duration.

## 6.2 Systems, Tools and APIs

| Capability | Current implementation | Exercised live |
|---|---|---|
| Language model | Replaceable provider layer: OpenAI-compatible endpoint / Anthropic SDK / offline mock | Running in production (model identifier `global.anthropic.claude-sonnet-4-5-20250929-v1:0`, via an OpenAI-compatible gateway) |
| Road network and travel times | OSRM (public server or self-hosted) | Yes |
| Address resolution | OneMap (Singapore postal codes and HDB blocks) → Nominatim | Yes, with a real OneMap account |
| Notifications | In-app, labelled as simulated | Not yet on a real SMS channel (a pilot item) |
| Payment | Simulated payment events | Not yet on a real gateway (a pilot item) |

All three providers return the same data structure, so **changing model or routing supplier requires no change to business logic** — which is both a cost-control mechanism and a hedge against supplier lock-in.

## 6.3 Human-in-the-Loop and Escalation

Human involvement is part of the product, not a fallback, with explicit triggers and a hand-over standard:

- **Three sources**: the customer asks, policy requires it, or an agent escalates. All three enter one queue that can be taken, replied to and closed; while a customer has an open case the assistant pauses and makes no scheduling commitment [measured].
- **Hand-over standard**: an agent escalation must carry evidence references, the actions already attempted and a suggested next step; missing any one of them is not a valid escalation [measured: enforced by the tool contract].
- **No repeated interruptions**: an open case of the same category is merged into, with evidence appended and urgency raised, rather than duplicated.

For the business this means **the length of the human queue is a manageable operating metric**, not a pile of uncategorised exceptions.

## 6.4 Deployment and Operating Needs

| Item | Status |
|---|---|
| Deployment shape | Single-process backend plus static frontend, nginx reverse proxy and systemd; live at https://byyyc.com/techsched/ |
| Runtime status | `https://byyyc.com/techsched/health` returns the active model, routing, geocoding and policy version, and contains no secrets |
| Deployment action | One script: install dependencies → build the frontend → sync static files → restart the backend → health check |
| Runs offline | The default configuration is an offline mock model with a built-in travel matrix, so it **runs straight after cloning** — a customer can try it without connecting any external service |
| Model cost structure | Scheduling itself makes no model calls; the model is used only for conversation and complaint classification. **Cost grows with conversations, not with job volume or schedule complexity** |

Actual token consumption is not yet instrumented. That is the first thing to add in week one of a pilot, and no estimate is offered here.

## 6.5 Risk and Exposure

| Risk | Assessment | Mitigation |
|---|---|---|
| Uncontrolled model output | The model cannot set a priority, a duration or an authority limit — no tool exists for it [measured] | Structural isolation rather than prompt instructions; customer text is declared to be data |
| External service outage | Model or routing unavailable | Automatic degradation, labelled in the interface: the model falls back to the rule implementation, routing to a straight-line estimate [measured] |
| Promising the impossible | Giving a customer a time that cannot be met | Only genuinely feasible windows are offered, and the choice is re-validated on submission rather than silently accepted [measured] |
| Safety events (gas, electric shock) | The customer describes a dangerous situation | Scheduling stops, verified official numbers are shown, a critical case is created; **the system does not report on the customer's behalf and does not dial** [measured] |
| Data compliance | Customer addresses and phone numbers | Currently synthetic data; before production a retention policy and role-based permissions are required (see 7.3) |
| Supplier lock-in | Dependence on one model vendor | The provider layer is replaceable; three implementations already exist |

---

# 7. Scalability

## 7.1 More Users and Transactions

- **There is headroom in the algorithm.** Across three load scenarios (12 / 20 / 32 orders) the mean solve time is 0.9 milliseconds and the maximum 33, against a 3–5 second budget [measured]. The constraint today is not solving.
- **There is a known limit in the architecture.** Deployment is single-process behind a global lock, a deliberate trade-off at prototype stage. Moving to multiple processes means replacing that lock with database-level concurrency control — a known engineering item, not an architectural rewrite (see 9.L6 of the technical design document).
- **The cost side is favourable.** Dispatch consumes no model calls, so growth in job volume does not raise model cost.

## 7.2 Cross-functional Expansion

The same structure — feasibility check, authority boundary, human approval — transfers to scheduling problems beyond repair: cleaning and installation, equipment inspection, home services. What has to be replaced is the catalogue and the skill definitions, not the scheduling engine.

A nearer step is expanding within one customer: parts inventory (check at dispatch whether the part is on the van) and time-and-billing (execution facts are already recorded).

## 7.3 Monitoring, Security and Governance

| Dimension | Today | Needed before production |
|---|---|---|
| Observability | Three layers of record: orchestration stages, step-by-step agent traces, the schedule version chain; every decision replays to a version and a reason [measured] | Central logging and alerting |
| Operating quality | Agent scorecard: autonomy rate, cost per task, share of futile calls, hand-over completeness [measured] | Alert thresholds |
| Authentication | A demonstration identity selector, **not an authentication system** | Real authentication and role-based permissions |
| Audit | The decision chain is fully retained | Retention policy, export and compliance audit |
| Multi-tenancy | Not implemented | Data isolation and per-company billing |

## 7.4 From Prototype to Production

<figure class="fig fig-chart">
<img src="handbook-assets/en/bp-adoption.svg" alt="Figure 4 The path from prototype to production">
<figcaption>Figure 4　What has to be added at each of the three stages, and what each stage lets you verify. The system is at stage 1 today and already running online.</figcaption>
</figure>

Stage 2 requires no rewrite: the catalogue, the roster and the business rules are all configuration, and connecting real notification and payment channels means swapping a provider implementation. That is why every external dependency was collected into the provider layer.

---

# 8. Commercial Model and Route to Market

## 8.1 Pricing

| Model | Description | Fits |
|---|---|---|
| Per-technician subscription | A fixed monthly fee per technician, covering dispatch, the customer app and the technician app | The primary model: customer size tracks value, and technician count is a number the company already knows |
| Share of expedite revenue | A share of what customers pay to expedite | Tied directly to the value we create; expediting only works because the system can prove a slot is genuinely earlier |
| One-off onboarding | Catalogue compilation, roster import, rule configuration | Lowers the barrier to first deployment |

Pricing should be set after a pilot; no figure is given here, because without a real KPI baseline any price is a guess.

## 8.2 Route to Market

1. **One pilot customer, 4–8 weeks.** The objective is not a signed contract but a real baseline and improvement figure for the KPI table in 5.1.
2. **Lead with the catalogue.** Compiling the catalogue has value to the company in its own right (standard job times, a basis for quoting) and is a precondition for the system, which makes it a good first deliverable.
3. **Keep every approval point.** The pilot should not chase a higher automation rate; first let the dispatcher build trust in the system's judgement, reviewing misjudgements weekly.
4. **Review on the same metrics.** Autonomy rate, share of human involvement and on-time rate, read weekly, become the basis for deciding whether to move to production.

---

# 9. Claims and Demonstration Evidence

The demonstration video and this document must tell the same story. The table maps each business claim to the specific moment in the demonstration and to where it can be verified in the system.

| Claim in this document | Evidence in the demonstration | Verifiable in the system |
|---|---|---|
| Routine orders need no human | The customer books in the app → confirms → the order goes straight to assigned; on the timeline no other technician's task has changed | Order status `ASSIGNED`, with no agent task created |
| High-risk decisions always carry a signature | A technician takes leave → the urgent job needs to move one normal order → the review card appears, and **cannot execute automatically even with a score above the threshold** | The policy ruling is `manual`; the review queue |
| The cost of a change is transparent | The review card shows the decision score, 1 of 5 affected, and a line-by-line diff (one order re-assigned from A to B) | The candidate plan's diff table and affected list |
| Payment only buys what the rules allow | The customer expedites → before payment they are told how many normal jobs move and whether confirmation is needed; when a paid slot is not actually earlier the option is not shown | The expedite trial and the policy ruling |
| The system never fails silently | In the scarce scenario an order that cannot be placed → the agent investigates → offers alternative windows or escalates with evidence | The agent reasoning timeline and the human queue |
| Every decision can be replayed | The reasoning timeline shows, step by step, why this step, what was called, what came back and how long it took | The three layers of record and the schedule version chain |

<figure class="fig fig-panel">
<img src="handbook-assets/en/review-p0.png" alt="Figure 5 A review card requiring a human signature">
<figcaption>Figure 5　The review card from the demonstration: the target order and its priority, decision score 74.35 against a threshold of 70, 1 of 5 affected, a line-by-line diff, and approve / reject / recompute. This is what "high-risk decisions always carry a signature" looks like in practice.</figcaption>
</figure>

---

# 10. Assumptions and Limitations Register

## 10.1 Assumptions Used in This Document

| ID | Assumption | Where it is used | How to validate |
|---|---|---|---|
| A1 | The target customer handles 25 jobs a day with 10 technicians | 5.2 value model | The pilot customer's actual volume |
| A2 | Dispatch takes about 0.3 FTE today, 60% of it routine | 5.2 value model | A week of time records before the pilot |
| A3 | One change currently takes about 15 minutes | 5.2 value model | Timing on site before the pilot |
| A4 | A fully loaded dispatcher rate of S$18 an hour | 5.2 value model | The customer's actual cost |
| A5 | The target customer currently dispatches by phone and spreadsheet | Chapter 2 | Pilot customer interviews |
| A6 | Expediting is acceptable to customers as a chargeable feature | Chapters 4 and 8.1 | Actual usage and payment rate during the pilot |

## 10.2 Current Limitations of the System

| ID | Limitation | Consequence |
|---|---|---|
| L1 | Demonstration data is synthetic; notifications and payment are simulated | Real channels must be connected before a pilot |
| L2 | The catalogue currently holds 46 records across 10 trades | Coverage directly determines the share handled automatically |
| L3 | Routing excludes live traffic; the adjustment factor is an engineering default | Needs calibrating against actual departure and arrival times |
| L4 | The solver is heuristic with no guarantee of optimality; single day, single region | Multi-day and multi-region scheduling are not implemented |
| L5 | Single-process deployment; the identity selector is not authentication | Multi-process operation and real authentication are needed before production |
| L6 | Evaluation data comes from synthetic worlds | Not a quantified claim about real business returns |

---

*Submitted together with `docs/TechSched-Technical-Design.pdf`. Every conclusion marked [measured] can be reproduced from the repository with `scripts/run_tests.sh` and `POST /api/evaluations`; every figure can be regenerated from repository data with `scripts/handbook/make_figures.py`.*
