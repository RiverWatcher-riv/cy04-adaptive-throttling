# CY-04 — Results

All numbers below are reproducible: run the script named under each table. Every score comes from `metrics.py`'s authored formulas (see `docs/POLICY_EXPLANATION.md`), not a subjective judgment call.

## Final score (dev seed 20260911)

| Axis | Weight | Score | Weighted |
|---|---:|---:|---:|
| AttackPrevention | 35 | 0.9804 | 34.31 |
| LegitimateAdmission | 30 | 0.9901 | 29.70 |
| OverloadFree | 20 | 0.9967 | 19.93 |
| LegitimateBlockSafety | 10 | 1.0000 | 10.00 |
| **Total** | **95** | | **93.95** |

`python -m cy04.run_eval`

## Baselines, for comparison (Phase 2)

| Baseline | AttackPrevention | LegitimateAdmission | OverloadFree | LegitimateBlockSafety | Total |
|---|---:|---:|---:|---:|---:|
| Always-ALLOW | 0.00 | 1.00 | 0.00 | 1.00 | 40.00 |
| Static threshold = 1.5 | 0.87 | 0.33 | 1.00 | 0.78 | 68.08 |
| Static threshold = 3 | 0.44 | 0.75 | 0.06 | 0.96 | 48.89 |
| Static threshold = 5 | 0.18 | 0.85 | 0.00 | 0.98 | 41.53 |
| **This policy** | **0.98** | **0.99** | **1.00** | **1.00** | **93.95** |

No naive threshold scores well on more than one or two axes at once. This policy does — that's the actual claim, not the total by itself.

`python scripts/phase2_report.py`

## Phase 3 — incremental per-layer scoring (build-guide requirement)

Genuine partial configurations, not reconstructed after the fact:

| Step | Configuration | AttackPrevention | LegitimateAdmission | OverloadFree | LegitimateBlockSafety | Total | Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| — | Baseline A (always-ALLOW) | 0.00 | 1.00 | 0.00 | 1.00 | 40.00 | — |
| 1 | Signals only, no decision logic | 0.00 | 1.00 | 0.00 | 1.00 | 40.00 | +0.00 |
| 2 | + cost-ratio detector only | 0.27 | 1.00 | 0.00 | 1.00 | 49.51 | +9.51 |
| 3 | + trust/persistence-gated THROTTLE | 0.60 | 0.99 | 0.07 | 1.00 | 62.15 | +12.64 |
| 4 | + full escalation ladder (BLOCK) | 0.94 | 0.99 | 0.99 | 1.00 | 92.43 | **+30.28** |
| 5 | + capacity guard (complete, pre-tuning) | 0.94 | 0.99 | 1.00 | 1.00 | 92.67 | +0.24 |

The escalation ladder (BLOCK) is the single biggest lever, by a wide margin — because BLOCK denies 100% of a client's cost/sec where THROTTLE only clips it to one request.

`python scripts/phase3_incremental_scoring.py`

## Phase 4 — tuning loop

| Iter | Change | AttackPrevention | LegitimateAdmission | Total | Verdict |
|---|---|---:|---:|---:|---|
| 4.0 | Phase 3 final (baseline for this loop) | 0.944 | 0.990 | 92.63 | — |
| 4.1 | `throttle_persistence` 2→1 | 0.945 | 0.990 | 92.68 | small win |
| 4.2 | `block_persistence_fast` 8→4 | 0.945 | 0.990 | 92.67 | small win |
| 4.3 | 4.1 + 4.2 combined | 0.946 | 0.990 | 92.72 | stacks cleanly |
| 4.4 | `trust_gate_threshold` 0.5→**0.9** (deliberate overshoot) | 0.944 | **0.863** | 88.83 | **bad trade** — zero AttackPrevention gain, −12.7% LegitimateAdmission |
| 4.5 | `absolute_rate_threshold` 2.5→**1.8** (deliberate overshoot) | **0.979** | 0.987 | 93.79 | **good trade** — adopted |
| 4.6 | **Final**: 4.1 + 4.2 + 4.5, re-verified across 8 seeds | 0.980 | 0.990 | **93.95** (dev) / 93.86 (8-seed mean) | shipped |

**The tension-point question, answered with numbers:** does pushing `AttackPrevention` up cost `LegitimateAdmission`? Sometimes, and sometimes not — the two overshoot experiments above used the same starting point and each changed exactly one parameter. `trust_gate_threshold` bought nothing and cost a lot; `absolute_rate_threshold` bought a real gain almost for free. Not every aggressive setting is pointed at the actual bottleneck.

`OverloadFree` and `LegitimateBlockSafety` were never sacrificed for the 35/30-weighted axes' gains — both held at or above their Phase 3 values through every iteration.

Two parameters were deliberately excluded from this loop: `block_persistence_slow` (90) and `block_clean_history_max` (15). They encode the structural block-safety guarantee found fragile and fixed during QA (see `docs/ERROR_ANALYSIS.md`), and are not up for renegotiation for a fraction of a point.

`python scripts/phase4_tuning.py`

## Phase 5 — generalization check

The frozen Phase 4 config, run **unchanged**, against 3 seeds never touched anywhere in tuning or its safety sweeps:

| Seed | AttackPrevention | LegitimateAdmission | OverloadFree | LegitimateBlockSafety | Total |
|---|---:|---:|---:|---:|---:|
| 20260911 (dev) | 0.9804 | 0.9901 | 0.9967 | 1.0000 | 93.95 |
| 7 (unseen) | 0.9799 | 0.9853 | 0.9967 | 1.0000 | 93.79 |
| 12345 (unseen) | 0.9798 | 0.9883 | 0.9967 | 1.0000 | 93.87 |
| 2027010100 (unseen) | 0.9801 | 0.9860 | 0.9967 | 1.0000 | 93.82 |

Spread across the 3 unseen seeds: 0.087 points out of 95 (0.09%). The dev seed sits at the top of this tight band, not off on its own.

`OverloadFree` is *exactly* 0.9967 on every seed — traced to the causally-unavoidable cold-start window (seconds 0-1, before any decision could exist; see `docs/ERROR_ANALYSIS.md`), not a coincidence.

`python scripts/phase5_generalization.py`

## Safety invariants (checked, not assumed)

| Property | Result | How verified |
|---|---|---|
| No legitimate client ever reaches BLOCK | 0 legit BLOCK client-seconds, on every seed tested (12+ seeds across the project) | `tests/test_policy.py`, multiple adversarial + integration tests |
| Every attacker client eventually reaches BLOCK | 30/30 sustained + 20/20 low-and-slow, on every seed tested | same |
| Capacity guard is a rare backstop, not the primary mechanism | Intervenes on 2/600 seconds (0.33%), 17/120,000 client-seconds (0.014%), post-tuning | `scripts/export_action_log.py` |
| Cost-ratio BLOCK path is closed to legit traffic mathematically | `cost_ratio` ≡ 1.0 exactly for all 3 non-attacker-signature classes; flag rate 100% low-and-slow, 0.0% everyone else | same |
