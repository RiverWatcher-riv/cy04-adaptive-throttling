# CY-04 — Build Journey, Bugs, and Rejected Approaches

The honest record of how this got built: what was tried, what broke, what was thrown away, and why. Every bug below was found by systematic checking rather than by a judge or a failing submission — which is the point of keeping the list.

## The arc

Six phases, built strictly in order, each gated on an exit checklist: simulator → scoring harness + naive baselines → the five-layer policy → tuning loop → generalization check → packaging, demo and write-up. Two extra passes were added on top: a deep QA pass per phase, and a final whole-project audit. Score went **40.00 (always-allow baseline) → 68.08 (best naive threshold) → 92.67 (untuned policy) → 93.95 / 95 (tuned)**.

---

## Bugs found and fixed

### 1. EWMA cold-start defect — blocked 164 legitimate client-seconds
**Symptom:** the policy scored a healthy-looking 88.99, but 164 legitimate client-seconds were being BLOCKed — the one thing the design promised never to do.

**Cause:** both rate EWMAs were initialised at zero. The slow EWMA (60s half-life) climbs toward the true rate far more slowly than the fast one (3s), so for the first minute-plus of the run *every* client — legitimate included — read as "spiking relative to its own baseline," purely because the baseline hadn't caught up. Legit clients therefore never accrued trust, and their persistence counters ran to 42–45.

**Fix:** warm-start both EWMAs from the client's first observation, plus a `relative_floor` so "3× your own baseline" only counts when the absolute level is non-trivial.

**Lesson worth keeping:** a good-looking aggregate score hid a categorical failure. The score alone would never have surfaced this; the invariant check did.

### 2. Block-safety rested on tuning, not structure
**Symptom:** after fix #1, zero legit blocks — but only because trust happened to stay above the ceiling.

**Cause:** two fragilities. The simulator permits *adjacent* burst windows, so a legit client can produce a 44–51 second continuous anomaly; and the separation between legit and attacker clean-history was only 20 vs 15 seconds — a five-second margin.

**Fix:** replaced the tuned guarantee with two structural ones. The cost-ratio BLOCK path is **mathematically closed** to legit traffic (legit cost is exactly 1/request and the cost/rate EWMAs share a half-life, so the ratio is exactly 1.0 — measured flag rate 100% low-and-slow, 0.0% everyone else). The duration BLOCK path's bar is **derived from config** to exceed the longest anomaly a legit client can physically produce (all bursts back-to-back plus decay tail).

**Cost, accepted deliberately:** ~3% AttackPrevention, ~1 point of total. A guarantee a judge can verify beats a number that happens to hold on the dev seed.

### 3. Capacity guard could bypass the block-safety guarantee
**Symptom:** none — it never fired in practice. Found by adversarial unit test, not by observation.

**Cause:** the system-level capacity guard (Layer 5) ranked clients purely by cost/trust and could push a THROTTLEd, history-protected client straight to BLOCK under capacity pressure — with no persistence or trust requirement at all, silently reopening the exact hole Layer 4 was built to close.

**Fix:** the guard now refuses any THROTTLE→BLOCK step for a client with an established consecutive-clean streak, and looks past it to the next candidate.

**Lesson:** "it never happened on any seed I tried" is not a guarantee when final evaluation runs on unseen seeds.

### 4. The immunity counter was noise-fragile
**Symptom:** on 3 of 5 tested seeds, a genuine sustained attacker became permanently BLOCK-immune.

**Cause:** the counter was a lifetime-cumulative tally that never reset, so an attacker could drift past the immunity bar (observed max 18, against a bar of 15) purely from scattered clean-looking seconds caused by ordinary Poisson noise.

**Fix:** renamed to `consecutive_clean_seconds` and reset on any anomalous second — a demonstrated *streak*, which is what the guarantee was always supposed to mean. Worst-case attacker streak afterward: 2 seconds.

### 5. Recorded results stopped reproducing from their own scripts
**Symptom:** re-running `phase3_incremental_scoring.py` and `phase4_tuning.py` produced numbers that contradicted the write-up — the tuning table collapsed to 93.83 repeated seven times.

**Cause:** both scripts built their baselines from `PolicyParams()` defaults, and Phase 4 later adopted three of the tuned changes *into* those defaults. So the Phase 3 script emitted post-tuning numbers labelled as the Phase 3 record, and the tuning loop's iterations became no-ops against themselves.

**Fix:** both baselines pinned explicitly in the scripts; `phase4_tuning.py` now self-checks that its final iteration still equals the shipped defaults and warns loudly if they diverge. A judge running either script now gets exactly the documented numbers.

### 6. Two rounds of stale figures in the write-up
Burst statistics were measured before tuning changed `throttle_persistence` (claimed 12.6s/22s and 1.3s/11s; actually 16.5s/27s and 3.1s/12s). The capacity-guard intervention rate was likewise pre-tuning (1.5% of seconds; actually 0.33%). Both corrected across repo docs, test comments and these notes. A verification claim of "12+ seeds" was also overstated — the true count was 11, so a **20-distinct-seed sweep** was run instead, making the claim accurate and stronger.

### 7. Demo-day UI bug: controls silently stopped responding
**Symptom:** the replay slider worked perfectly on a fresh load, but after a presenter dragged it, Reset and the jump buttons silently did nothing.

**Cause:** the slider was built with a positional default, so Streamlit's retained widget state overrode the new default on rerun.

**Fix:** bind the slider to session state with an explicit `key`. This then surfaced a *second*, related constraint: Streamlit refuses writes to a widget-keyed state entry once that widget has been instantiated in the same run — so the Restart button and the autoplay advance both have to execute **above** the slider in script order. Both are now covered by a regression test that performs the exact scrub-then-reset sequence.

### 8. Lint-level issues cleared in the final audit
Eleven findings, including two dead imports and a blind `pytest.raises(Exception)` that would have passed on any unrelated error (now asserts `FrozenInstanceError` specifically).

---

## Approaches tried and rejected

**Absolute cost as the BLOCK trigger.** Rejected: a sustained attacker (rate 4, cost 1/request) and a legit client mid-burst (rate 6, cost 1/request) produce comparable absolute cost load. `cost_ewma` cannot separate them, because the difference between them is duration, not price. BLOCK was split into two explicit paths instead — a fast cost-signature path and a slow duration path.

**Fast trust decay (0.30/sec).** Rejected: a 20-second burst wiped ~99.9% of a legit client's accrued trust, defeating the entire purpose of a trust accumulator. Lowered to 0.03.

**A single static rate threshold** (the naive baseline). Kept only as evidence of why it can't work: no threshold value scores well on more than one or two axes at once. Below 6 it punishes bursty-legit before catching the sustained attacker; above 1.5 the low-and-slow attacker passes untouched forever. Three settings were measured (1.5 → 68.08, 3 → 48.89, 5 → 41.53) to make the point with numbers rather than assertion.

**`trust_gate_threshold` 0.5 → 0.9 during tuning.** Rejected and *kept as evidence*: it bought **zero** AttackPrevention while costing 12.7 points of LegitimateAdmission. A deliberate demonstration that not every aggressive knob points at the actual bottleneck. The companion experiment (`absolute_rate_threshold` 2.5 → 1.8) was the favourable trade and was adopted: +3.5% AttackPrevention for −0.3% LegitimateAdmission.

**Tuning the two structural safety parameters.** Deliberately excluded from the Phase 4 search. `block_persistence_slow` and `block_clean_history_max` encode the block-safety guarantee that bugs #2–#4 were about; shrinking them to chase a fraction of a point would reopen exactly that fragility.

**A scripted, pitch-style demo** (the original Phase 6 spec: a 90-second narrated sequence, a "wow" counter, a staged ground-truth reveal). Superseded on direction: rebuilt as a live, explorable simulation instead — press Play and watch 600 seconds of traffic, decisions and scores advance, with the interest coming from the mechanism rather than the framing.

---

## What the QA process actually bought

Four of the eight bugs above (#1–#4) were live safety-invariant defects in the policy; three (#5–#6, #8) were evidence-integrity defects that would have made a sound project look fabricated; one (#7) would have failed live in front of a judge. **None were found by the score going down** — the score looked fine, or even better, in several of these states. They were found by checking invariants directly, re-measuring documented numbers rather than trusting them, and testing the demo's controls as sequences rather than in isolation.

Final state: 93.95 / 95 on the dev seed, 93.87 mean across a 20-seed sweep (range 93.70–93.98), zero legitimate clients ever blocked and all 50 attackers escalating on every seed tested, 77 tests passing, lint clean.

# Sources
the original build prescription
the Parameter Register note
the Build Phases note

