# CY-04 — Adaptive API Abuse Throttling

An online, causal, class-blind controller that watches per-second request
count and cost per client and issues `ALLOW` / `THROTTLE` / `BLOCK` — trying
to starve attackers of cost while preserving legitimate traffic, including
legitimate clients who occasionally spike hard.

Deterministic, explainable, heuristic policy. No trained model: the
controller never sees ground-truth class, and final evaluation runs on
unseen seeds, so nothing here is fit to labels.

## The core idea

Every naive rate limiter fails because it only tracks request count. This
policy tracks **rate and cost as two decoupled signals**, which is the
entire reason the low-and-slow attacker class (modest rate, 5x cost per
request) becomes visible at all — its rate looks like a normal user, but
its cost-per-request ratio cannot be produced by legitimate traffic.

Full design rationale, threat model, and phase-by-phase build plan live in
the project's Obsidian vault (five linked notes: overview, scoring model,
threat model, policy design, build phases + parameter register).

## Status

**Phase 4 complete** — the five-layer policy, tuned, scores
**93.95 / 95** (dev seed) / 93.86 average across 8 seeds, with no axis
left collapsed:

| Policy | AttackPrevention | LegitimateAdmission | OverloadFree | LegitimateBlockSafety | Weighted total |
|---|---:|---:|---:|---:|---:|
| Always-ALLOW baseline | 0.00 | 1.00 | 0.00 | 1.00 | 40.00 |
| Static threshold = 1.5 | 0.87 | 0.33 | 1.00 | 0.78 | 68.08 |
| Static threshold = 3 | 0.44 | 0.75 | 0.06 | 0.96 | 48.89 |
| Static threshold = 5 | 0.18 | 0.85 | 0.00 | 0.98 | 41.53 |
| This policy, untuned (Phase 3) | 0.94 | 0.99 | 1.00 | 1.00 | 92.67 |
| **This policy, tuned (Phase 4)** | **0.98** | **0.99** | **1.00** | **1.00** | **93.95** |

The point isn't the total — it's the shape. Every naive threshold buys
one axis by wrecking another; this buys all four at once, and Phase 4's
tuning loop moved `AttackPrevention` further (0.94→0.98) without
touching the other three.

**Tuning found one bad lever and one good one, deliberately.** Making
`trust_gate_threshold` far more aggressive (0.5→0.9) bought zero
`AttackPrevention` and cost 12.7 points of `LegitimateAdmission` — a
pure loss, kept as evidence in the Parameter Register. Lowering
`absolute_rate_threshold` (2.5→1.8) instead bought +3.5%
`AttackPrevention` for −0.3% `LegitimateAdmission`, re-verified safe
across 8 seeds, and was adopted. Not every aggressive setting is
pointed at the actual bottleneck.

**No legitimate client is ever blocked**, and that holds by construction
rather than by tuning (verified across 8 seeds, including after tuning):

- The **cost-ratio BLOCK path is closed to legit traffic mathematically** —
  legit cost is exactly 1/request and the cost/rate EWMAs share a
  half-life, so their ratio is exactly 1.0. Measured flag rate: 100% for
  low-and-slow attackers, 0.0% for all three other classes.
- The **duration BLOCK path is unreachable for legit traffic** — its bar is
  derived from config to sit above the longest anomaly a legit client can
  physically produce (all bursts back-to-back plus the EWMA decay tail).

The capacity guard intervenes on 1.5% of seconds, confirming layers 1–4
do the real work.

- [x] Phase 1 — Simulator
- [x] Phase 2 — Scoring harness + naive baselines
- [x] Phase 3 — Real policy (5 layers)
- [x] Phase 4 — Tuning loop
- [x] Phase 5 — Generalization check
- [ ] Phase 6 — Packaging, demo & submission materials

**Phase 5 generalization** (frozen Phase 4 config, run unchanged against 3 seeds never touched during tuning):

| Seed | Total |
|---|---:|
| 20260911 (dev) | 93.95 |
| 7 (unseen) | 93.79 |
| 12345 (unseen) | 93.87 |
| 2027010100 (unseen) | 93.82 |

Spread across the 3 unseen seeds: 0.087 points (0.09%). This generalizes. The only seconds the system is ever over the 220 cap, on every seed, are the causally-unavoidable first 1-2 seconds of the run — before any decision could possibly have been made, every client defaults to ALLOW. That's the entire remaining `OverloadFree` gap, confirmed structural rather than a per-seed weakness.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

pytest                          # run the test suite
python scripts/phase1_report.py # sanity-check the traffic generator
python scripts/phase2_report.py # score both baselines
python scripts/phase3_report.py # verify the policy's exit checklist
python -m cy04.run_eval                       # run the policy, print the four scores
python scripts/phase3_incremental_scoring.py  # per-layer scoring table (build-guide requirement)
python scripts/phase4_tuning.py               # tuning loop + tension-point analysis
```

## Local demo

```bash
docker compose -f deploy/docker-compose.yml up --build
```

Then open http://localhost:8501. See [`deploy/README.md`](deploy/README.md).

## Project structure

```
src/cy04/
├── config.py       every numeric constant — single source of truth
├── simulator.py    Phase 1 — traffic generator
├── metrics.py      Phase 2 — scoring formulas
├── baselines.py    Phase 2 — always-ALLOW + static-threshold baselines
├── policy.py       Phase 3 — the 5-layer controller, one section per layer
└── run_eval.py     Phase 3 — causal evaluation loop + action log
tests/              pytest, one file per module
scripts/            sanity-check / report scripts
demo/               Streamlit live-replay dashboard
deploy/             Docker + Compose for one-command local hosting
```

## Simulation parameters

| Class | λ (req/sec) | Cost/req | Population |
|---|---:|---:|---:|
| Normal legit | 1.0 | 1 | 100 |
| Bursty legit | 0.3 baseline → 6 in burst | 1 | 50 |
| Sustained attacker | 4.0 | 1 | 30 |
| Low-and-slow attacker | 1.5 | 5 | 20 |

600 seconds, seed `20260911` (NumPy PCG64), capacity cap 220 admitted
cost/sec. Bursty-legit clients get exactly 3 non-overlapping 20-second
bursts, starting between seconds 20–560.
