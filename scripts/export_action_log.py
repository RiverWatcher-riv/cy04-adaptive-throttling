"""Phase 6 evidence: export the full action log to CSV.

Per client, per second: signals, action taken, and a reason code --
the per-decision "why" the error analysis and demo depend on. Not
committed to git (120,000 rows) -- regenerate on demand.
"""

from __future__ import annotations

from pathlib import Path

from cy04.config import SEED
from cy04.run_eval import run_policy
from cy04.simulator import generate_traffic


def main() -> None:
    traffic = generate_traffic(SEED)
    log = run_policy(traffic)

    out_dir = Path(__file__).resolve().parent.parent / "artifacts"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"action_log_seed{SEED}.csv"
    log.to_csv(out_path, index=False)

    print(f"Wrote {len(log):,} rows to {out_path}")
    print(f"Columns: {list(log.columns)}")
    print("\nReason code distribution:")
    print(log["reason"].value_counts().to_string())


if __name__ == "__main__":
    main()
