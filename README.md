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

**Phase 2 complete** — scoring harness + naive baselines confirm the
harness scores sensibly, and reproduce the threat-model note's "no
static threshold works" argument with real numbers:

| Baseline | AttackPrevention | LegitimateAdmission | OverloadFree | LegitimateBlockSafety | Weighted total |
|---|---:|---:|---:|---:|---:|
| Always-ALLOW | 0.00 | 1.00 | 0.00 | 1.00 | 40.00 |
| Static threshold = 1.5 | 0.87 | 0.33 | 1.00 | 0.78 | 68.08 |
| Static threshold = 3 | 0.44 | 0.75 | 0.06 | 0.96 | 48.89 |
| Static threshold = 5 | 0.18 | 0.85 | 0.00 | 0.98 | 41.53 |

No single threshold scores well on more than one or two axes at once —
the real policy's bar isn't beating the best total, it's beating the shape.

- [x] Phase 1 — Simulator
- [x] Phase 2 — Scoring harness + naive baselines
- [ ] Phase 3 — Real policy (5 layers)
- [ ] Phase 4 — Tuning loop
- [ ] Phase 5 — Generalization check
- [ ] Phase 6 — Packaging, demo & submission materials

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

pytest                          # run the test suite
python scripts/phase1_report.py # sanity-check the traffic generator
python scripts/phase2_report.py # score both baselines
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
├── policy.py       Phase 3 — the controller (not yet built)
└── run_eval.py     Phase 3+ — causal evaluation loop (not yet built)
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
