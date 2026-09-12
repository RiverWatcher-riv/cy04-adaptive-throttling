# CY-04 — Policy Explanation

## The one-paragraph version

> Two decoupled EWMA signals (rate, cost) per client, a slow-building trust score that gates escalation speed, a persistence requirement before any hard action, THROTTLE as the default lever with automatic decay-back to ALLOW, BLOCK reserved for sustained + low-trust + high-cost cases, and an independent capacity-guard layer that overrides individual classification when aggregate cost nears the cap.

Everything below is that paragraph, unpacked one layer at a time, each tied to a specific class behavior and a specific term in the scoring function — with the actual measured number, not just the claim.

## Layer 1 — Two-signal core

**Claim:** every client class is separable in *(rate, cost)* space; none is separable in rate alone.

**Why it matters:** a naive request-count limiter has exactly one dial, and there is no position for that dial that works. Set it below 6 (bursty-legit's burst rate) and it fires on real users before it ever catches the sustained attacker (rate 4). Set it above 1.5 and the low-and-slow attacker (rate 1.5, but 5× the cost per request) passes through untouched forever — it's cost-weighted more heavily than the sustained attacker (7.5 cost/sec vs. 4) while sending *fewer* requests.

**Measured:** at settled state (t≥60), `rate_ewma_fast` for the four classes is 0.90 (bursty-legit), 1.53 (low-and-slow), 1.00 (normal-legit), 4.00 (sustained) — rate alone puts low-and-slow closer to normal-legit than to the other attacker class. Tracking cost separately is what recovers the signal: `cost_ewma` for low-and-slow is 7.63, nearly double the sustained attacker's 4.00, despite the lower rate.

## Layer 2 — Cost-per-request ratio detector

**Target:** the low-and-slow attacker, specifically — the flagship feature, because it's the one class a rate-only limiter structurally cannot see.

```
cost_ratio = cost_ewma / rate_ewma_fast
```

**Why it's nearly free:** legitimate traffic costs exactly 1 per request by construction. There is no legitimate path to a client-level average materially above 1 — the separation is structural, not statistical.

**Measured:** `cost_ratio` settles at exactly 1.0 for bursty-legit, normal-legit, *and* the sustained attacker (all cost-1-per-request classes) — and exactly 5.0 for low-and-slow. Flag rate: **100% for low-and-slow, 0.0% for the other three classes**, no exceptions, on the full 600-second run. Scored in isolation (no other layer active), this detector alone moves `AttackPrevention` from 0.00 to **0.27** while leaving `LegitimateAdmission` and `LegitimateBlockSafety` untouched at 1.00 — proof the near-zero-false-positive claim holds in practice, not just in theory.

## Layer 3 — Trust accumulator + persistence window

**Target:** bursty-legit vs. sustained-attacker — the one pair that looks identical in an instantaneous snapshot (both spike rate hard), separated only by history and duration.

**Mechanism:** a per-client trust score rises during clean behavior and falls on anomalous signal, gating how readily escalation happens. A persistence counter requires *sustained* anomalous signal, not a single hot second, before anything acts on it.

**Measured:** at settled state, trust is 0.90 for bursty-legit and 0.99 for normal-legit — both high, because both spend the large majority of their time looking clean. Trust for both attacker classes sits at **0.000** — never above it, because neither is ever clean long enough to accrue any. That asymmetry is what lets a bursty-legit client's burst be tolerated (mean 1.3s throttled per isolated burst) while a sustained attacker escalates within seconds.

## Layer 4 — Escalation ladder: THROTTLE-first, BLOCK as a narrow compound case

**Target:** the block/throttle cost asymmetry in the scoring model — THROTTLE only clips a client's admitted requests to 1/sec and costs nothing on `LegitimateBlockSafety`; BLOCK denies everything and is the one action that axis actually penalizes.

**As built, BLOCK is two explicit paths, not one** — a single absolute-cost condition can't distinguish a sustained attacker from a legit client mid-burst, since both produce comparable absolute cost load (the difference is duration, not price):

- **Fast path** (low-and-slow): low trust + cost-ratio flagged + persistence ≥ 4s.
- **Slow path** (sustained/volumetric, no cost signature needed): low trust + persistence ≥ 90s — deliberately set above the longest anomaly a legit client can physically produce (all 3 bursts back-to-back plus decay tail), so this path is unreachable for legit traffic by construction.
- A third guard applies to both: a client with an established **consecutive** clean streak (≥15s) is permanently BLOCK-ineligible regardless of current signals.

**Decay-back is automatic, not a separate mechanism:** the decision function is stateless given current signals, so the moment they normalize, neither escalation condition holds and the action reverts on its own.

**Measured:** this layer is overwhelmingly the biggest lever in the whole policy — adding it (Phase 3, step 4) moves the weighted total from 62.15 to 92.43, a **+30.28** jump, dwarfing every other layer's contribution. Final action mix: sustained attacker spends 84.5% of client-seconds BLOCKed, low-and-slow spends 99.4% BLOCKed; bursty-legit spends 97.9% ALLOWed and **0.00%** BLOCKed; normal-legit spends 99.8% ALLOWed and **0.00%** BLOCKed. All 30 sustained-attacker clients and all 20 low-and-slow clients reach BLOCK at least once; zero legitimate client-seconds ever do, verified across 12+ seeds.

## Layer 5 — Independent capacity guard

**Target:** `OverloadFree`, the one axis that's a system-level property rather than a per-client classification outcome.

**Mechanism:** projects next-second admitted cost per client from current EWMA state (never from the future — still fully causal), and if the total would exceed a soft cap, downgrades the highest cost-signal / lowest-trust clients first — a priority reordering, not a second classifier with its own thresholds.

**Measured:** intervenes on **2 of 600 seconds (0.33%)**, affecting 17 of 120,000 client-seconds (0.014%), with the current tuned thresholds. This confirms the designed role directly: it's a rare backstop, and layers 1-4 do the real work, not an accident of weak per-client thresholds. Adding it (Phase 3, step 5, pre-tuning parameters) moved the total by a marginal **+0.24** — small and expected.

## Reading the whole thing together

| Layer | Protects | Measured contribution |
|---|---|---|
| L1 (signals) | Foundation for L2/L5 | n/a directly — enables the rest |
| L2 (cost ratio) | `AttackPrevention`, near-zero FP | +9.51 in isolation, 100%/0% flag separation |
| L3 (trust + persistence) | `LegitimateAdmission`, `LegitimateBlockSafety` | +12.64; trust 0.90-0.99 legit vs. 0.000 attackers |
| L4 (escalation ladder) | `LegitimateBlockSafety` (narrow BLOCK), `LegitimateAdmission` (decay-back) | **+30.28** — the dominant lever |
| L5 (capacity guard) | `OverloadFree`, independent of classification | +0.24 (pre-tuning) — confirmed rare backstop (0.33% of seconds, post-tuning) |

Full derivation and class-by-class threat model: see the project's Obsidian vault (Scoring Model, Threat Model and Client Classes, Policy Design notes) and `docs/RESULTS.md` for every number's reproduction script.
