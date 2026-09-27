# Scoring

Feasibility first, score second. The match score is a **preference**, not a probability of success. It never bypasses a
hard constraint or an authority limit (PolicyEngine checks those before looking at the score).

## Per-assignment match score

For an assignment `a` of order `o` to technician `t` inside a candidate plan:

| Component | Weight | Formula (each clamped to [0,1]) | Raw quantities shown in UI |
|---|---:|---|---|
| skill_fit | 0.30 | `0` if `level(t, trade) < required`; `1.0` if `required == 5`; else `0.7 + 0.3 · (level − required) / (5 − required)` | level, required |
| travel | 0.25 | `1 − travel_minutes / 60` | inbound travel minutes |
| response | 0.20 | `1 − wait / 120`, `wait = service_start − reference`, `reference = max(now, window_start)` (overdue targets: `now`) | reference, wait |
| workload | 0.10 | `1 − (actual + planned) / shift_length`; actual = completed/locked blocks (travel+wait+service), planned = movable blocks in the plan; nothing counted twice | actual, planned, shift |
| stability | 0.15 | `1 − (0.5 · min(1, affected / 2) + 0.5 · own)`, `own = max(min(1, own_shift / 60), 0.5 if the order's technician changed)` | affected count, own shift |

`match_score = 100 · Σ weight · component`. Weights/scales live in `config/policy.yaml → scoring` (engineering defaults).

Why these choices: exact skill matches still score well (0.7) so ordinary jobs stay auto; travel is the main cost lever;
response uses `max(now, window_start)` so a far-future appointment is not punished for being far away; the stability
scale (`affected reference 2`, technician-change penalty 0.5) was calibrated so a zero-disturbance plan beats a plan that
moves a customer for a few minutes of travel — see `docs/decisions.md`.

## Decision score

`decision_score = min(match_score over all NEW or CHANGED assignments in the plan)` (a changed assignment = technician or
planned service start differs from the committed one). Unchanged assignments are not re-scored and never block a plan.
Plans with no change return `no_action` (no score is invented).

## Threshold

`auto_score_threshold: 70`, operator `>` (strict): 70.00 → dispatcher review, 70.01 → may auto-commit. Comparison uses
the unrounded float; the UI shows two decimals to make borderline cases readable.

## Priority never adds score

Priority selects the queue order and the authority (which orders may move, how many). It does not add a uniform bonus to
every technician of the same order — that would carry no information for the choice.
