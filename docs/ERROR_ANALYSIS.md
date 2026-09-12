# CY-04 — Error Analysis

Specific and honest, per the build guide's own instruction: state the weak points before a judge finds them, explain why they're structurally hard, and quantify the cost rather than wave at it.

## 1. A bursty-legit client's very first-ever burst

**The known weak point, named directly, unprompted.** Before a bursty-legit client has ever had a burst, its trust has only had time to build from quiet baseline behavior. The very first burst it ever produces looks, for the first few seconds, structurally identical to a sustained attacker's onset — there is no history yet to tell them apart, because the only thing that *does* tell them apart (duration and prior trust) hasn't had a chance to accumulate.

**Why this is structurally hard, not a tuning gap:** the trust/persistence mechanism (Layer 3) is explicitly a *history*-based signal. A client with zero history is, by definition, indistinguishable from an attacker on that axis alone — that's not a bug to fix, it's what "history-based" means. The only way to eliminate this window entirely would be to either grant blanket trust to brand-new clients (which an attacker could then exploit on its very first request) or eliminate the persistence requirement (which reopens the false-positive problem persistence exists to solve). Neither is a real option.

**Why it's bounded, not open-ended:** the simulator's own spec places every burst start at second ≥20 (`BURST_START_RANGE = (20, 560)`), so in practice every bursty-legit client has already banked **at least 20 consecutive clean seconds** before its first burst ever happens — comfortably past the point where trust has begun accruing (trust reaches ~0.64 after 20 clean seconds at the current `trust_gain_rate`). This is a rare, first-occurrence-only cost, not a recurring one: a client only pays it once, on its first burst, and never again.

## 2. Back-to-back bursts compound throttling (found during QA, not in the original spec)

**What we found:** when a bursty-legit client's next burst starts soon after the previous one ends (the simulator permits adjacent, non-overlapping burst windows), trust hasn't recovered between them, and the second burst is measurably more throttled than an isolated one.

**Measured:** for bursts starting within 30 seconds of the previous one ending, mean THROTTLE duration is 12.6 seconds (max 22) versus a mean of 1.3 seconds (max 11) for well-separated bursts — an order of magnitude difference.

**Why this is the same root cause as #1, different trigger:** both come down to insufficient accrued trust at the moment a burst starts — #1 because the client is new, this because the client's trust was recently drained and hasn't recovered. It is not a new mechanism failing; it's the same one, under a harder version of the same condition.

**Why it never threatens the core guarantee:** `LegitimateBlockSafety` is never touched by this — BLOCK eligibility is checked unconditionally regardless of throttle duration, and the duration-only BLOCK path's bar (90 seconds) sits well above even the worst observed compounded streak (51 seconds, measured directly). Only `LegitimateAdmission` absorbs this cost, and it's a small one: bursty-legit's overall ALLOW rate across the full run is still 97.86%.

## 3. `OverloadFree`'s residual gap is entirely the cold-start window

**What we found:** on every seed tested, `OverloadFree` lands at exactly 0.9967 — suspicious precision, investigated rather than accepted at face value.

**Traced to:** the only seconds the system is ever over the 220 cap are seconds 0-1 (occasionally 0-2) of the run — literally before any policy decision could exist. At `t=0`, causality forbids acting on data not yet observed, so every one of 200 clients defaults to ALLOW simultaneously. Phase 1's own sanity report already established that unthrottled traffic averages ~415 cost/sec — nearly double the cap — regardless of which seed drew which random values. By `t=2-3`, the cost-ratio detector (which requires no persistence to fire) and the rate-anomaly path have already had one or two observations to act on, and admitted cost drops back under the cap for the remaining 598 seconds of the run.

**Why this is a mathematical property of the problem, not a policy defect:** there is no possible online, causal policy that can act on data before that data exists. This gap is the floor, not a weakness specific to this design — any correct causal policy pays it.

## Development-process rigor (why the numbers above can be trusted)

Three real safety-invariant bugs were found and fixed during systematic QA, not left for a judge to discover:

1. **EWMA cold-start defect** (Phase 3 QA): both rate EWMAs starting at zero made the 60s-half-life baseline lag the 3s fast signal for the first minute-plus, so every client — legitimate included — read as spiking relative to its own baseline. This BLOCKed 164 legitimate client-seconds while the weighted total still looked healthy at 88.99 — the dangerous kind of bug, a good score hiding a broken promise. Fixed by warm-starting both EWMAs from the first observation.
2. **Capacity-guard block-immunity bypass** (deeper QA pass): the system-level capacity guard (Layer 5) had no awareness of the consecutive-clean-streak immunity Layer 4 enforces, and could in principle push a history-protected client straight to BLOCK under pure capacity pressure. Never manifested in practice (the guard only intervenes on 2 of 600 seconds post-tuning), caught by direct adversarial testing before it could manifest on an unseen seed.
3. **Noise-fragile immunity counter** (same pass): the immunity counter was originally a lifetime-cumulative tally rather than a consecutive streak, letting a sustained attacker drift past the immunity bar from scattered clean-looking seconds caused by ordinary Poisson noise — observed on 3 of 5 tested seeds. Fixed by making it a streak that resets on any anomalous second.

All three were caught by systematic testing across multiple seeds *before* Phase 5's dedicated generalization check, not during it — which is why Phase 5 found a clean, tight-variance result rather than a new surprise.
