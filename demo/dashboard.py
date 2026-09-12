"""CY-04 exploration dashboard.

An analytical instrument, not a pitch. The interest is meant to come
from the mechanism and the numbers themselves -- rate vs. cost as two
separate signals, trust and persistence gating escalation, the
scoring formulas evaluated live -- not from dramatized reveals or
framed-up counters. Every number shown is also computed directly by
`cy04.metrics`/`cy04.policy`; nothing here is a separate presentation-
layer calculation that could drift from the real one.

Two independent ways to look at the run:
  - a time-scrubbable view of system state at a chosen second (capacity,
    score-to-date, the live client table, the rate/cost map)
  - a client deep-dive that shows one client's entire 600-second trace
    at once, independent of the scrubber -- the natural way to actually
    look at a burst or an escalation from start to end
"""

from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from cy04.config import (
    BASE_LAMBDA,
    CAPACITY_CAP,
    CLASS_SIZES,
    COST_PER_REQUEST,
    DURATION_S,
    SEED,
    ClientClass,
)
from cy04.metrics import apply_actions, score
from cy04.run_eval import run_policy
from cy04.simulator import client_roster, generate_traffic

ACTION_COLOR = {"ALLOW": "#2ecc71", "THROTTLE": "#e08e0b", "BLOCK": "#d64545"}
CLASS_COLOR = {
    "normal_legit": "#3498db",
    "bursty_legit": "#9b59b6",
    "sustained_attacker": "#d64545",
    "low_and_slow_attacker": "#e08e0b",
}
ACTION_RANK = {"ALLOW": 0, "THROTTLE": 1, "BLOCK": 2}

METRICS_FORMULAS = """\
AttackPrevention      = 1 - attacker_cost_admitted / attacker_cost_generated
LegitimateAdmission   =     legit_cost_admitted    / legit_cost_generated
OverloadFree          =     seconds_under_cap      / total_seconds          (cap = 220)
LegitimateBlockSafety = 1 - legit_client_seconds_blocked / legit_client_seconds_total
(each clipped to [0,1]; weighted 35 / 30 / 20 / 10 out of 95)"""

st.set_page_config(page_title="CY-04 Explorer", layout="wide")


@st.cache_data(show_spinner="Running the 600s policy replay once...")
def load_run(seed: int):
    traffic = generate_traffic(seed)
    log = run_policy(traffic)
    admitted = apply_actions(traffic, log[["second", "client_id", "action"]])
    admitted["reason"] = log["reason"]
    roster = {c.client_id: c for c in client_roster(seed)}
    full_score = score(traffic, log[["second", "client_id", "action"]])
    return traffic, log, admitted, roster, full_score


traffic, log, admitted, roster, full_score = load_run(SEED)

if "second" not in st.session_state:
    st.session_state.second = 0
if "playing" not in st.session_state:
    st.session_state.playing = False

st.title("CY-04 — Adaptive API Abuse Throttling")
st.caption(
    "Online, causal, class-blind controller: per-second request count and "
    "cost per client in, ALLOW / THROTTLE / BLOCK out. Explore the signals "
    "and decisions directly below."
)

with st.expander("Simulation parameters"):
    param_rows = [
        {
            "class": cls.value,
            "lambda": BASE_LAMBDA[cls],
            "cost/request": COST_PER_REQUEST[cls],
            "population": CLASS_SIZES[cls],
        }
        for cls in ClientClass
    ]
    st.dataframe(pd.DataFrame(param_rows), hide_index=True)
    st.caption(
        f"{DURATION_S}s run, seed {SEED} (NumPy PCG64), capacity cap {CAPACITY_CAP} "
        "admitted cost/sec. Bursty-legit clients get exactly 3 non-overlapping "
        "20-second bursts, starting between seconds 20-560."
    )

st.divider()

# --- Time controls -----------------------------------------------------

ctrl = st.columns([1, 1, 1, 4])
with ctrl[0]:
    if st.button("Pause" if st.session_state.playing else "Play"):
        st.session_state.playing = not st.session_state.playing
with ctrl[1]:
    step = st.selectbox("Step size", [1, 5, 20], index=1, label_visibility="collapsed")
with ctrl[2]:
    if st.button("Reset"):
        st.session_state.second, st.session_state.playing = 0, False
with ctrl[3]:
    st.slider("Second", 0, DURATION_S - 1, key="second")

t = st.session_state.second
window = admitted[admitted["second"] <= t]
current = admitted[admitted["second"] == t]
latest = log[log["second"] == t].copy()

# --- System state at this second ----------------------------------------

col1, col2, col3 = st.columns(3)
admitted_now = int(current["admitted_cost"].sum())
col1.metric(f"Admitted cost, second {t}", f"{admitted_now} / {CAPACITY_CAP}")

attacker_mask = window["client_class"].isin(
    [ClientClass.SUSTAINED_ATTACKER.value, ClientClass.LOW_AND_SLOW_ATTACKER.value]
)
gen = int(window.loc[attacker_mask, "cost"].sum())
adm = int(window.loc[attacker_mask, "admitted_cost"].sum())
col2.metric("Attacker cost denied / generated so far", f"{gen - adm:,} / {gen:,}")

action_counts = latest["action"].value_counts()
col3.metric(
    "Clients right now",
    f"{action_counts.get('ALLOW', 0)} allow · "
    f"{action_counts.get('THROTTLE', 0)} throttle · "
    f"{action_counts.get('BLOCK', 0)} block",
)

# --- Score to date, with formulas visible --------------------------------

st.subheader(f"Score over seconds 0–{t}")
partial_traffic = traffic[traffic["second"] <= t]
partial_actions = log[log["second"] <= t][["second", "client_id", "action"]]
s = score(partial_traffic, partial_actions)
d = s.as_dict()
sc = st.columns(5)
sc[0].metric("AttackPrevention · 35", f"{d['AttackPrevention']:.3f}")
sc[1].metric("LegitimateAdmission · 30", f"{d['LegitimateAdmission']:.3f}")
sc[2].metric("OverloadFree · 20", f"{d['OverloadFree']:.3f}")
sc[3].metric("LegitimateBlockSafety · 10", f"{d['LegitimateBlockSafety']:.3f}")
sc[4].metric("Weighted total", f"{d['WeightedTotal']:.2f} / 95")
with st.expander("Formulas"):
    st.code(METRICS_FORMULAS, language=None)

st.divider()

# --- Client explorer: sortable, filterable table of all 200 clients -----

st.subheader("Client explorer — all 200 clients, second " + str(t))
show_class = st.checkbox(
    "Show true class",
    value=False,
    help="Not used by the policy to decide anything -- shown here only so "
    "you can check its calls against ground truth.",
)

table_cols = ["client_id", "action", "reason", "rate_ewma_fast", "cost_ratio", "trust", "persistence_counter"]
table = latest[table_cols].copy()
if show_class:
    table.insert(1, "true_class", latest["client_class"].to_numpy())

filter_cols = st.columns(2)
with filter_cols[0]:
    action_filter = st.multiselect("Filter: action", ["ALLOW", "THROTTLE", "BLOCK"])
with filter_cols[1]:
    class_filter = st.multiselect(
        "Filter: true class", [c.value for c in ClientClass], disabled=not show_class
    )

if action_filter:
    table = table[table["action"].isin(action_filter)]
if class_filter and show_class:
    table = table[table["true_class"].isin(class_filter)]

st.dataframe(
    table.sort_values("client_id").round(3),
    hide_index=True,
    height=320,
    width="stretch",
)

st.divider()

# --- Live map: rate vs. cost-per-request ---------------------------------

st.subheader("Rate vs. cost-per-request map, second " + str(t))
st.caption(
    "x = request-rate EWMA, y = cost-per-request EWMA. Legitimate traffic sits "
    "at y≈1 regardless of rate; the low-and-slow class sits at y≈5 while its "
    "rate looks unremarkable -- the axis a request-count limiter can't see."
)
color_by = st.radio("Color by", ["policy action", "true class"], horizontal=True)
plot_df = latest[["client_id", "rate_ewma_fast", "cost_ratio", "action", "client_class"]].copy()
if color_by == "policy action":
    plot_df["color"] = plot_df["action"].map(ACTION_COLOR)
else:
    plot_df["color"] = plot_df["client_class"].map(CLASS_COLOR)
st.scatter_chart(plot_df, x="rate_ewma_fast", y="cost_ratio", color="color", size=50)

st.divider()

# --- Client deep dive: one client's full 600s trace, independent of t ---

st.subheader("Client deep dive — full run, any client")
client_id = st.number_input("Client ID", min_value=0, max_value=199, value=1, step=1)
c = roster[int(client_id)]
info = f"true class: **{c.client_class.value}** · base λ: {c.base_lambda} · cost/request: {c.cost_per_request}"
if c.burst_windows:
    info += f" · burst windows: {sorted(c.burst_windows)}"
st.caption(info)

trace = log[log["client_id"] == client_id].set_index("second")

dd = st.columns(2)
with dd[0]:
    st.caption("Rate EWMA (fast vs. slow/own-baseline)")
    st.line_chart(trace[["rate_ewma_fast", "rate_ewma_slow"]])
with dd[1]:
    st.caption("Cost-per-request EWMA (cost_ratio)")
    st.line_chart(trace[["cost_ratio"]])

dd2 = st.columns(2)
with dd2[0]:
    st.caption("Trust")
    st.line_chart(trace[["trust"]])
with dd2[1]:
    st.caption("Action over time (0=ALLOW, 1=THROTTLE, 2=BLOCK)")
    st.area_chart(trace["action"].map(ACTION_RANK))

with st.expander("Raw per-second trace"):
    st.dataframe(trace[["requests", "cost", "action", "reason"]], width="stretch")

st.divider()

# --- Aggregate action mix across the full run, by class ------------------

st.subheader("Action mix across the full run, by class")
dist = log.groupby(["client_class", "action"]).size().unstack(fill_value=0)
dist_pct = (dist.T / dist.sum(axis=1)).T * 100
st.dataframe(dist_pct.round(2), width="stretch")

# --- Autoplay -------------------------------------------------------------

if st.session_state.playing:
    if st.session_state.second >= DURATION_S - 1:
        st.session_state.playing = False
    else:
        time.sleep(0.05)
        st.session_state.second = min(DURATION_S - 1, st.session_state.second + step)
        st.rerun()
