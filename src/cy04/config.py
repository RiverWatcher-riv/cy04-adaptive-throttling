"""Central configuration for the CY-04 adaptive throttling controller.

Every numeric constant used by the simulator, policy, and metrics lives
here, so nothing downstream carries a hidden magic number. Values marked
"resolved default" were not pinned by an external spec and were decided
in-house against a sub-4-hour build budget -- see the project's Obsidian
note "Technical Requirements and Tools" for the reasoning behind each one.
"""

from __future__ import annotations

from enum import Enum


class ClientClass(str, Enum):
    NORMAL_LEGIT = "normal_legit"
    BURSTY_LEGIT = "bursty_legit"
    SUSTAINED_ATTACKER = "sustained_attacker"
    LOW_AND_SLOW_ATTACKER = "low_and_slow_attacker"


ATTACKER_CLASSES = {ClientClass.SUSTAINED_ATTACKER, ClientClass.LOW_AND_SLOW_ATTACKER}
LEGIT_CLASSES = {ClientClass.NORMAL_LEGIT, ClientClass.BURSTY_LEGIT}

# --- Simulation frame --------------------------------------------------
SEED = 20260911
DURATION_S = 600
N_CLIENTS = 200
CAPACITY_CAP = 220  # max admitted cost/sec before the system is "overloaded"

# --- Class sizes (resolved default) -------------------------------------
# Legit traffic dominates a realistic population (75%); within attackers,
# the loud sustained class outnumbers the sneaky low-and-slow class 30:20,
# since real low-and-slow campaigns are the rarer, harder-to-catch threat --
# this also stress-tests the cost-ratio detector against a small cohort
# rather than a conveniently large one.
CLASS_SIZES: dict[ClientClass, int] = {
    ClientClass.NORMAL_LEGIT: 100,
    ClientClass.BURSTY_LEGIT: 50,
    ClientClass.SUSTAINED_ATTACKER: 30,
    ClientClass.LOW_AND_SLOW_ATTACKER: 20,
}
assert sum(CLASS_SIZES.values()) == N_CLIENTS

# --- Arrival rates (lambda, requests/sec) -------------------------------
LAMBDA_NORMAL_LEGIT = 1.0  # resolved default
LAMBDA_BURSTY_BASELINE = 0.3
LAMBDA_BURSTY_BURST = 6.0
LAMBDA_SUSTAINED_ATTACKER = 4.0
LAMBDA_LOW_AND_SLOW = 1.5

BASE_LAMBDA: dict[ClientClass, float] = {
    ClientClass.NORMAL_LEGIT: LAMBDA_NORMAL_LEGIT,
    ClientClass.BURSTY_LEGIT: LAMBDA_BURSTY_BASELINE,
    ClientClass.SUSTAINED_ATTACKER: LAMBDA_SUSTAINED_ATTACKER,
    ClientClass.LOW_AND_SLOW_ATTACKER: LAMBDA_LOW_AND_SLOW,
}

# --- Cost per request ----------------------------------------------------
COST_PER_REQUEST: dict[ClientClass, int] = {
    ClientClass.NORMAL_LEGIT: 1,
    ClientClass.BURSTY_LEGIT: 1,
    ClientClass.SUSTAINED_ATTACKER: 1,
    ClientClass.LOW_AND_SLOW_ATTACKER: 5,
}

# --- Bursty-legit burst shape ---------------------------------------------
BURST_COUNT = 3
BURST_DURATION_S = 20
BURST_START_RANGE = (20, 560)  # inclusive bounds on the burst start second
