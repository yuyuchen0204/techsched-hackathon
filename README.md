# Technician Scheduling — intelligent service & dispatch prototype (V3)

An SME on-site repair scheduling system: customer app (chat + address + order page) → catalog-backed work order →
skill/time/travel-feasible scheduling → P0–P3 authority-bounded rescheduling → auto execution or dispatcher approval →
simulated technician app (manual driving, leave, rest, service reports) → customer tracking, handoff and rating.

**V3** adds the three-end closed loop (`/customer`, `/technician`, `/`), human cases (customer request · policy
required · agent escalation), a tool-driven agent runtime with budgets and traces, dynamic rest instead of a fixed
lunch, customer history / preferences, safety incidents with a verified emergency-contact entry point, feasible-window
negotiation, live simulated positions, duration capture with a shadow prediction, and three load scenarios.
Each agent's playbook is a file in `config/agent_skills/` (front matter = the enforced tool allow-list and budget,
body = the prose the model is given); agents can delegate to each other with `delegate_task`; the dispatcher's
**Agents** tab shows the reasoning timeline, the playbooks and an agent scorecard.

See `docs/v3-change-summary.md`, `docs/v3-agent-tools.md`, `docs/v3-demo-guide.md` (中文).

**Everything in the demo is synthetic** (technicians, customers, phones, travel matrix). Notifications are written to
the app (`delivery_mode=simulated`); no SMS/email/payment is ever sent. The **only business source** for trades,
problems, complexity and repair durations is the user's CSV at `data/reference/repair_object_problem_database.csv`.

## Quick start (local, offline: mock LLM + fixture routes)

Prerequisites: Python 3.12 (`uv` recommended), Node 20+ (developed with Node 26.7 / Python 3.12.14).

```bash
scripts/setup.sh          # venv + deps + .env
scripts/dev.sh            # backend http://127.0.0.1:8100  ·  frontend http://127.0.0.1:5174
```

Manual start:

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --port 8100 --reload
cd frontend && npx vite --port 5174
```

On first start the backend creates `data/app.db`, imports the catalog (46 items from the CSV), loads the fixture
locations and seeds scenario `main` (8 technicians, 20 orders, 2 already-departed tasks, baseline schedule).
Restarting keeps the database; **Reset** (dashboard button or `POST /api/demo/reset`) is the only thing that re-seeds.

- Dispatch Dashboard: <http://127.0.0.1:5174/> · Customer Chatbot: <http://127.0.0.1:5174/customer>
- OpenAPI: <http://127.0.0.1:8100/docs> · health/mode: <http://127.0.0.1:8100/health>

## The catalog CSV

Path: `data/reference/repair_object_problem_database.csv` (override with `REPAIR_CATALOG_PATH`). Expected columns
(aliases accepted, see `backend/app/services/catalog_importer.py`): trade type, specific problem, complexity (1–5),
repair duration in minutes. The importer reports actual headers, mapping, valid/duplicate/error rows via
`POST /api/catalog/reload` and `GET /api/catalog`. Conflicting duplicate keys abort the import; the file is never modified.
Without the file the product shows "catalog not configured" and refuses to create orders — it never falls back to a made-up catalog.

## Modes (`.env`)

| Variable | Values | Notes |
|---|---|---|
| `OSRM_DURATION_FACTOR` / `OSRM_BASE_MINUTES` | 1.25 / 3 | applied on top of OSRM free-flow time; set `1` / `0` for raw OSRM |
| `LLM_MODE` | `mock` (default) / `real` | `real` + `LLM_PROVIDER=openai_compat` calls any OpenAI-style `/v1/chat/completions` endpoint (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) — **verified live with DeepSeek `deepseek-ai/DeepSeek-V4.1-Flash` via ModelScope** (`https://api-inference.modelscope.cn/v1`, 4–19 s per turn, JSON mode + schema validation). `LLM_PROVIDER=anthropic` uses the Anthropic SDK (`claude-opus-5` default; not called live here). Any failure/timeout (`LLM_TIMEOUT_SECONDS`, 30) degrades to mock and is labelled in the UI/agent log. |
| `ROUTE_MODE` | `fixture` (default) / `osrm` / `estimated` | `osrm` = real road network via an OSRM server (`OSRM_BASE_URL`; **verified live against the public demo server for Singapore**); customers may then pin any exact location on the map. `estimated` = haversine at 28 km/h. On provider failure the whole matrix degrades to `estimated` and the schedule shows `DEGRADED`. See *Routing* below. |
| `RISK_SCAN_INTERVAL_SECONDS` | 60 | real-time background scan while the sim clock is paused |
| `CLOCK_RUN_SIM_MINUTES_PER_SECOND` | 1 | sim minutes advanced per real second when the clock is running |

## Routing (how travel times and map routes are computed)

| Mode | Travel time | Map route | Customer location |
|---|---|---|---|
| `fixture` | offline 18×18 minute matrix (haversine × 1.35 at 30 km/h + 5 min, deterministic asymmetry) | straight schematic line | 18 preset areas; a map pin snaps to the nearest preset |
| `osrm` | **real road network** from an OSRM server: `ceil(free-flow minutes × OSRM_DURATION_FACTOR) + OSRM_BASE_MINUTES` (defaults 1.25 / 3 — OSRM has no live traffic, the factor/allowance are engineering defaults) | real road polyline per leg (`/api/routes/technician/{id}`) | presets **or an exact map pin** (`POST /api/locations/resolve`) |
| `estimated` | haversine × 1.3 at 28 km/h + 4 min | straight schematic line | presets or exact map pin |

Matrices from OSRM are cached on disk (`data/cache/matrix_<snapshot>.json`, keyed by the location set + factor/base) and
route geometries in memory, so restarts/resets do not re-query the server. Custom pins are deleted on demo reset.

**Option 1 — public demo server (zero install, light use only):**
```
ROUTE_MODE=osrm
OSRM_BASE_URL=https://router.project-osrm.org
```
Run by the OSRM project for demos; no SLA, rate-limited, may be down. Fine for a hackathon demo thanks to the cache.

**Option 2 — your own OSRM server for Singapore (recommended for the real demo; needs Docker):**
```bash
mkdir -p ~/osrm && cd ~/osrm
curl -LO https://download.geofabrik.de/asia/malaysia-singapore-brunei-latest.osm.pbf     # ~200 MB, includes Singapore
docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-extract -p /opt/car.lua /data/malaysia-singapore-brunei-latest.osm.pbf
docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-partition /data/malaysia-singapore-brunei-latest.osrm
docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-customize /data/malaysia-singapore-brunei-latest.osrm
docker run -d --name osrm -p 5000:5000 -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-routed --algorithm mld --max-table-size 500 /data/malaysia-singapore-brunei-latest.osrm
curl "http://localhost:5000/table/v1/driving/103.8990,1.3298;103.9403,1.3525"     # smoke test → {"code":"Ok",...}
```
then `OSRM_BASE_URL=http://localhost:5000` and restart the backend (delete `data/cache/matrix_*.json` if you want to force a refresh).
Extract/partition/customize take a few minutes once; the server then answers in milliseconds, offline, unlimited.

## Geocoding (typed addresses → coordinates)

When the customer types an address (“Blk 123 Tampines St 11”, “521123”, “Hougang Mall”), the UnderstandingAgent calls a
geocoding **tool** — the LLM never produces coordinates. Provider chain (`GEOCODE_MODE=auto`):

| Provider | Needs | Strength | Verified |
|---|---|---|---|
| **OneMap** (Singapore Land Authority) | free account: register at <https://www.onemap.gov.sg/apidocs/register>, then either `ONEMAP_EMAIL` + `ONEMAP_PASSWORD` (the backend fetches/refreshes the 3-day token) or a long-lived `ONEMAP_TOKEN` | exact postal codes and HDB block numbers | **verified live** with the user's account: "Blk 125 Tampines St 11 #05-123" → 125 Tampines Street 11 S521125; "邮编 520123" → 123 Simei Street 1. OneMap rejects "Blk/Block/B210A" prefixes and unit numbers, so the chain normalizes them (`normalize_address`); a 6-digit postal code is queried on its own |
| **Nominatim** (OpenStreetMap) | nothing (≤1 request/s, User-Agent set, results cached in `data/cache/geocode.json`) | streets, landmarks, malls | **verified live** (`Hougang Mall`, `Tampines Street 11`); does not know individual HDB block numbers |

Resolution rules: an exact result is used when it is the only hit or the only hit carrying every number the customer
typed (block / postal); otherwise up to 4 candidates are offered as buttons; if lookup fails, the area name falls back to
the preset list and the customer can always drop a pin on the map. In `ROUTE_MODE=fixture` any geocoded point snaps to the
nearest preset area (and says so); with `osrm`/`estimated` the exact point is used for travel times.

## Deploy to AWS Lightsail

1. Lightsail console → *Create instance* → Linux/Unix → **OS Only → Ubuntu 24.04 LTS** → plan with ≥ 1 GB RAM → create.
2. Instance → *Networking* → *Attach static IP*. The default firewall (22, 80) is sufficient; add 443 only if you set up HTTPS.
3. *Account → SSH keys* → download the default key (`LightsailDefaultKey-<region>.pem`).
4. From the dev machine: `scripts/lightsail/push.sh <static-ip> ~/Downloads/LightsailDefaultKey-<region>.pem`

The app is then served at `http://<static-ip>/techsched/` (nginx → uvicorn, systemd unit `techsched-backend`). Re-run
step 4 to redeploy; the server's `data/app.db` is kept. Logs: `sudo journalctl -u techsched-backend -f`.

## Tests & checks

```bash
scripts/run_tests.sh                     # pytest (103) + ruff + mypy + tsc + vite build
scripts/e2e/run.sh                       # Playwright three-end flow, 21 checks (servers must be running)
```

## Docs

**Business proposal** — the submission deliverable judged on Problem & Opportunity, Business Value, Impact & Outcomes,
Feasibility & Scalability and Proposal Quality (~15 pages): `docs/business-proposal.zh.md` →
**`docs/TechSched-商业计划书.pdf`** · `docs/business-proposal.en.md` → **`docs/TechSched-Business-Proposal.pdf`**.
Build with `scripts/build_handbook_pdf.sh zh business-proposal` / `... en business-proposal`.

**Technical design document** — the submission deliverable for *documentation of your system design, codes*
(scope, architecture, agent workflows, data flow, human approval, security, deployment, evaluation; ~22 pages):
`docs/technical-design.zh.md` → **`docs/TechSched-技术设计文档.pdf`** · `docs/technical-design.en.md` →
**`docs/TechSched-Technical-Design.pdf`**. Build with `scripts/build_handbook_pdf.sh zh technical-design` / `... en technical-design`.

**Product handbook** — the long-form internal reference the technical design doc was condensed from (also covers market,
users, value and roadmap, which belong in the business proposal), two editions with identical structure:
`docs/product-handbook.zh.md` → **`docs/TechSched-产品手册.pdf`** (67 pages) · `docs/product-handbook.en.md` →
**`docs/TechSched-Product-Handbook.pdf`** (74 pages). Rebuild with `scripts/build_handbook_pdf.sh` / `scripts/build_handbook_pdf.sh en`,
which regenerates the data-derived figures from `data/evaluation/*.json`, the catalog CSV, the scenarios and `config/policy.yaml`
via `scripts/handbook/make_figures.py`, then measures the contents page numbers in a two-pass build ·
`docs/project-brief.md` (V2 spec) · `docs/v3-upgrade-brief.md` (V3 spec) · `docs/v3-change-summary.md` ·
`docs/v3-agent-tools.md` · `docs/v3-demo-guide.md` (中文) · `docs/architecture.md` · `docs/data-contracts.md` ·
`docs/scoring.md` · `docs/decisions.md` · `docs/demo.md` · `docs/demo-guide-zh.md` · `docs/evaluation.md` · `docs/progress.md`

## Docker

`compose.yaml` + Dockerfiles are provided but **were not run on the development machine (no Docker installed)**.
