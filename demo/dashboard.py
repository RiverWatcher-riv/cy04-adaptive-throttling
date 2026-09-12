"""CY-04 live simulation dashboard.

Runs the 600-second simulation as a live, playing simulation: press Play
and traffic, signals, decisions and scores all advance second by second
in front of you. Charts grow as the run proceeds rather than showing a
finished picture, so the system reads as something actually running.

Every number is computed by the same `cy04.metrics` / `cy04.policy`
code the tests and CLI use -- no separate presentation-layer maths that
could drift from the real results.
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
ACTION_LABEL = {"ALLOW": "Allowed", "THROTTLE": "Throttled", "BLOCK": "Blocked"}
ACTION_RANK = {"ALLOW": 0, "THROTTLE": 1, "BLOCK": 2}

CLASS_COLOR = {
    "normal_legit": "#3498db",
    "bursty_legit": "#9b59b6",
    "sustained_attacker": "#d64545",
    "low_and_slow_attacker": "#e08e0b",
}
CLASS_LABEL = {
    "normal_legit": "Normal users",
    "bursty_legit": "Bursty users",
    "sustained_attacker": "Sustained attackers",
    "low_and_slow_attacker": "Low-and-slow attackers",
}
REASON_LABEL = {
    "cold_start_default": "No data yet",
    "clean": "Nothing unusual",
    "cost_ratio_flagged": "Cost per request too high",
    "sustained_rate_anomaly_low_trust": "Sustained unusual rate, low trust",
    "block_fast_path_cost_ratio_signature": "Blocked — costly requests, low trust",
    "block_slow_path_sustained_duration": "Blocked — sustained unusual activity",
    "capacity_guard_override": "Throttled to protect capacity",
}
COLUMN_LABEL = {
    "client_id": "Client",
    "action": "Action",
    "reason": "Why",
    "rate_ewma_fast": "Requests / sec",
    "cost_ratio": "Cost / request",
    "trust": "Trust",
    "persistence_counter": "Seconds flagged",
    "true_class": "True type",
}

SPEEDS = {"1× (real time, 10 min)": 1, "5×": 5, "20×": 20, "60× (fast)": 60}

st.set_page_config(page_title="CY-04 Live Simulation", layout="wide")


@st.cache_data(show_spinner="Preparing the 600-second simulation...")
def load_run(seed: int):
    traffic = generate_traffic(seed)
    log = run_policy(traffic)
    admitted = apply_actions(traffic, log[["second", "client_id", "action"]])
    admitted["reason"] = log["reason"]
    roster = {c.client_id: c for c in client_roster(seed)}

    # Pre-pivoted per-second series, so each frame is a cheap slice.
    offered = traffic.pivot_table(index="second", columns="client_class", values="requests", aggfunc="sum")
    offered = offered.rename(columns=CLASS_LABEL)
    admitted_cost_ps = admitted.groupby("second")["admitted_cost"].sum().rename("Cost let through")
    offered_cost_ps = traffic.groupby("second")["cost"].sum().rename("Cost attempted")
    actions_ps = (
        log.groupby(["second", "action"]).size().unstack(fill_value=0).rename(columns=ACTION_LABEL)
    )
    for col in ("Allowed", "Throttled", "Blocked"):
        if col not in actions_ps:
            actions_ps[col] = 0
    actions_ps = actions_ps[["Allowed", "Throttled", "Blocked"]]
    return traffic, log, admitted, roster, offered, admitted_cost_ps, offered_cost_ps, actions_ps


traffic, log, admitted, roster, offered, admitted_cost_ps, offered_cost_ps, actions_ps = load_run(SEED)

# --- State, and all mutations to it, BEFORE the keyed slider is built ------
# Streamlit refuses writes to a widget-keyed state entry once that widget
# has been instantiated in the same run, so the autoplay advance and the
# buttons must all run above the slider.

if "sec" not in st.session_state:
    st.session_state.sec = 0
if "playing" not in st.session_state:
    st.session_state.playing = False

step = SPEEDS[st.session_state.get("speed", "20×")]
if st.session_state.playing:
    st.session_state.sec = min(DURATION_S - 1, st.session_state.sec + step)
    if st.session_state.sec >= DURATION_S - 1:
        st.session_state.playing = False

st.title("CY-04 — Adaptive API Abuse Throttling")
st.markdown(
    "A simulated API serves **200 clients for 600 seconds**. Most are real users; "
    "some are attackers. Each second the controller sees only each client's "
    "**request count** and **cost** — never who the attackers are — and decides to "
    "**Allow**, **Throttle** (1 request/sec) or **Block**. Press **Play** to watch it run."
)

controls = st.columns([1, 1, 2, 6])
with controls[0]:
    if st.button("▶ Play" if not st.session_state.playing else "⏸ Pause", type="primary"):
        st.session_state.playing = not st.session_state.playing
with controls[1]:
    if st.button("Restart"):
        st.session_state.sec, st.session_state.playing = 0, False
with controls[2]:
    st.selectbox("Speed", list(SPEEDS), index=2, key="speed", label_visibility="collapsed")
with controls[3]:
    st.slider("Second", 0, DURATION_S - 1, key="sec", label_visibility="collapsed")

t = st.session_state.sec
upto = slice(0, t)
window = admitted[admitted["second"] <= t]
current = admitted[admitted["second"] == t]
latest = log[log["second"] == t].copy()

st.caption(f"**Second {t} of {DURATION_S - 1}** " + ("· running" if st.session_state.playing else "· paused"))

# --- Headline metrics -------------------------------------------------------

partial_actions = log[log["second"] <= t][["second", "client_id", "action"]]
s = score(traffic[traffic["second"] <= t], partial_actions)
d = s.as_dict()

attacker_mask = window["client_class"].isin(
    [ClientClass.SUSTAINED_ATTACKER.value, ClientClass.LOW_AND_SLOW_ATTACKER.value]
)
atk_gen = int(window.loc[attacker_mask, "cost"].sum())
atk_adm = int(window.loc[attacker_mask, "admitted_cost"].sum())
legit_mask = ~attacker_mask
lg_gen = int(window.loc[legit_mask, "cost"].sum())
lg_adm = int(window.loc[legit_mask, "admitted_cost"].sum())
admitted_now = int(current["admitted_cost"].sum())
counts = latest["action"].value_counts()

m = st.columns(5)
m[0].metric("Attack prevention · 35", f"{d['AttackPrevention']:.2f}",
            help=f"Attacker cost denied: {atk_gen - atk_adm:,} of {atk_gen:,} attempted.")
m[1].metric("Legitimate admission · 30", f"{d['LegitimateAdmission']:.2f}",
            help=f"Real-user cost let through: {lg_adm:,} of {lg_gen:,} attempted.")
m[2].metric("Overload-free · 20", f"{d['OverloadFree']:.2f}",
            help=f"Seconds under the {CAPACITY_CAP}/sec cap, out of {t + 1} elapsed.")
m[3].metric("Block safety · 10", f"{d['LegitimateBlockSafety']:.2f}",
            help="Share of real-user time NOT spent fully blocked. Must stay at 1.00.")
m[4].metric("Overall score", f"{d['WeightedTotal']:.1f} / 95")

m2 = st.columns(4)
m2[0].metric("Cost through this second", f"{admitted_now} / {CAPACITY_CAP}")
m2[1].metric("Allowed now", int(counts.get("ALLOW", 0)))
m2[2].metric("Throttled now", int(counts.get("THROTTLE", 0)))
m2[3].metric("Blocked now", int(counts.get("BLOCK", 0)))

st.divider()

# --- Live charts ------------------------------------------------------------

live_left, live_right = st.columns(2)

with live_left:
    st.markdown("**Traffic by client type** — requests attempted per second, live")
    st.caption(
        "Bursty users' spikes are visible as sharp peaks; sustained attackers hold a "
        "steady high line; low-and-slow attackers stay deliberately unremarkable here."
    )
    st.line_chart(offered.loc[upto], height=260)

with live_right:
    st.markdown(f"**System load vs. capacity** — cap is {CAPACITY_CAP} cost/sec")
    st.caption(
        "'Cost attempted' is everything clients tried to send; 'Cost let through' is "
        "what the controller admitted. The gap between them is the work it's doing."
    )
    load = pd.concat([offered_cost_ps.loc[upto], admitted_cost_ps.loc[upto]], axis=1)
    load["Capacity cap"] = CAPACITY_CAP
    st.line_chart(load, height=260)

live_left2, live_right2 = st.columns(2)

with live_left2:
    st.markdown("**Controller decisions over time** — clients in each state, per second")
    st.caption("Escalation is visible as the Blocked band growing while Allowed stays high.")
    st.area_chart(actions_ps.loc[upto], height=260,
                  color=["#2ecc71", "#e08e0b", "#d64545"])

with live_right2:
    st.markdown("**Rate vs. cost per request** — every client, right now")
    st.caption(
        "Real traffic always costs 1 per request, so it stays low no matter how fast it "
        "sends. Low-and-slow attackers sit at 5 — the axis a request-counter can't see."
    )
    colour_by = st.radio("Colour by", ["Decision", "True type"], horizontal=True,
                         label_visibility="collapsed")
    plot = latest[["rate_ewma_fast", "cost_ratio", "action", "client_class"]].copy()
    if colour_by == "Decision":
        plot["c"] = plot["action"].map(ACTION_COLOR)
        legend = [(ACTION_LABEL[k], v) for k, v in ACTION_COLOR.items()]
    else:
        plot["c"] = plot["client_class"].map(CLASS_COLOR)
        legend = [(CLASS_LABEL[k], v) for k, v in CLASS_COLOR.items()]
    st.markdown(
        "&nbsp;&nbsp;&nbsp;".join(f"<span style='color:{c}'>●</span> {n}" for n, c in legend),
        unsafe_allow_html=True,
    )
    st.scatter_chart(plot, x="rate_ewma_fast", y="cost_ratio", color="c", size=45, height=230)

st.divider()

# --- Detail tabs ------------------------------------------------------------

tab_clients, tab_one, tab_setup = st.tabs(["All clients now", "Follow one client", "Simulation setup"])

with tab_clients:
    st.caption("Every client at this second. Click a column header to sort; filters below.")
    show_class = st.checkbox("Show each client's true type", value=False,
                             help="Never used by the controller — shown only so you can check its calls.")
    cols = ["client_id", "action", "reason", "rate_ewma_fast", "cost_ratio", "trust", "persistence_counter"]
    table = latest[cols].copy()
    if show_class:
        table.insert(1, "true_class", latest["client_class"].to_numpy())
    f = st.columns(2)
    with f[0]:
        af = st.multiselect("Only these actions", ["ALLOW", "THROTTLE", "BLOCK"],
                            format_func=lambda a: ACTION_LABEL[a])
    with f[1]:
        cf = st.multiselect("Only these true types", [c.value for c in ClientClass],
                            format_func=lambda c: CLASS_LABEL[c], disabled=not show_class)
    if af:
        table = table[table["action"].isin(af)]
    if cf and show_class:
        table = table[table["true_class"].isin(cf)]
    table["action"] = table["action"].map(ACTION_LABEL)
    table["reason"] = table["reason"].map(REASON_LABEL)
    if show_class:
        table["true_class"] = table["true_class"].map(CLASS_LABEL)
    st.dataframe(table.round(3).rename(columns=COLUMN_LABEL).sort_values("Client"),
                 hide_index=True, height=340, width="stretch")

with tab_one:
    st.caption("A single client's whole 600 seconds — independent of the playhead above.")
    cid = st.selectbox("Client", list(range(200)), index=1,
                       format_func=lambda i: f"Client {i} — {CLASS_LABEL[roster[i].client_class.value]}")
    c = roster[int(cid)]
    info = f"Typical rate {c.base_lambda} req/sec · cost {c.cost_per_request} per request"
    if c.burst_windows:
        info += " · bursts at " + ", ".join(f"{a}–{b}s" for a, b in sorted(c.burst_windows))
    st.caption(info)
    tr = log[log["client_id"] == cid].set_index("second")
    g = st.columns(2)
    with g[0]:
        st.markdown("**Request rate** (fast reading vs. own baseline)")
        st.line_chart(tr[["rate_ewma_fast", "rate_ewma_slow"]], height=220)
        st.markdown("**Trust** (earned by consistently normal behaviour)")
        st.line_chart(tr[["trust"]], height=220)
    with g[1]:
        st.markdown("**Cost per request** (1 = legitimate, 5 = low-and-slow attacker)")
        st.line_chart(tr[["cost_ratio"]], height=220)
        st.markdown("**Action** (0 Allowed · 1 Throttled · 2 Blocked)")
        st.area_chart(tr["action"].map(ACTION_RANK), height=220)
    with st.expander("Second-by-second data"):
        raw = tr[["requests", "cost", "action", "reason"]].copy()
        raw["action"] = raw["action"].map(ACTION_LABEL)
        raw["reason"] = raw["reason"].map(REASON_LABEL)
        st.dataframe(raw, width="stretch")

with tab_setup:
    st.dataframe(
        pd.DataFrame([
            {"Client type": CLASS_LABEL[c.value], "Requests/sec": BASE_LAMBDA[c],
             "Cost per request": COST_PER_REQUEST[c], "How many": CLASS_SIZES[c]}
            for c in ClientClass
        ]), hide_index=True, width="stretch")
    st.caption(
        f"{DURATION_S} seconds · seed {SEED} · capacity {CAPACITY_CAP} cost/sec. Bursty users "
        "each get 3 separate 20-second bursts at random points. Scores are weighted "
        "35 / 30 / 20 / 10 out of 95."
    )
    st.markdown("**How each group was treated across the whole run**")
    dist = log.groupby("client_class")["action"].value_counts().unstack(fill_value=0)
    pct = (dist.T / dist.sum(axis=1)).T * 100
    pct = pct.rename(index=CLASS_LABEL, columns=ACTION_LABEL)
    st.dataframe(pct.round(1), width="stretch")

# --- Drive the clock --------------------------------------------------------

if st.session_state.playing:
    time.sleep(1.0 if step == 1 else 0.12)
    st.rerun()
