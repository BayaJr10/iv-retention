"""
Weekly batch job: score customers and write this week's action list.

Usage
-----
    python run_pipeline.py --data data/Immaculate_Vibes_Sales.csv
    python run_pipeline.py --data data/latest.csv --snapshot today --consent data/consent_register.csv

Outputs (in --out, default ./outputs):
    action_list_<date>.csv     who to message this week (incl. hold-out group)
    customer_status_<date>.csv every customer: lifecycle status + RFM segment
    segments_<date>.csv        segment summary
    kpis_<date>.json           KPI snapshot
    data_quality_<date>.json   data-quality report for this export
    kpi_history.csv            one row appended per run (for trend monitoring)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from iv_retention import pipeline as p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, help="Sales export CSV")
    ap.add_argument("--out", default="outputs", help="Output folder")
    ap.add_argument("--snapshot", default=None,
                    help="'today', a date YYYY-MM-DD, or omit for day after last order")
    ap.add_argument("--consent", default=None, help="Optional consent register CSV (customer_id, whatsapp_consent)")
    ap.add_argument("--drift-days", type=float, default=None, help="Override drift threshold (default: auto P75)")
    ap.add_argument("--lapse-days", type=float, default=None, help="Override lapse threshold (default: auto P90)")
    args = ap.parse_args(argv)

    snapshot = date.today().isoformat() if args.snapshot == "today" else args.snapshot
    cfg = p.make_config(snapshot_date=snapshot, drift_days=args.drift_days, lapse_days=args.lapse_days)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    df = p.load_orders(args.data)
    qr = p.quality_report(df)
    if not qr["ok"]:
        print("STOPPED – the export failed blocking checks:", *qr["blocking"], sep="\n  - ")
        (out / "data_quality_FAILED.json").write_text(json.dumps(qr, indent=2, default=str))
        return 2

    consent = pd.read_csv(args.consent) if args.consent else None
    res = p.run(df, cfg, consent)
    tag = res.snapshot.strftime("%Y-%m-%d")

    res.actions.to_csv(out / f"action_list_{tag}.csv", index=False)
    cols = ["status", "Segment", "RFM_score", "delivered_orders", "total_revenue", "days_since_last_order",
            "first_channel", "county", "age_group", "gender"]
    res.lifecycle[cols].to_csv(out / f"customer_status_{tag}.csv")
    res.segments.to_csv(out / f"segments_{tag}.csv")
    (out / f"kpis_{tag}.json").write_text(json.dumps(res.kpis, indent=2, default=str))
    (out / f"data_quality_{tag}.json").write_text(json.dumps(res.quality, indent=2, default=str))

    hist = out / "kpi_history.csv"
    row = pd.DataFrame([res.kpis])
    if hist.exists():
        h = pd.read_csv(hist)
        h = pd.concat([h[h["snapshot_date"] != tag], row], ignore_index=True)
    else:
        h = row
    h.to_csv(hist, index=False)

    k = res.kpis
    to_msg = res.actions[res.actions["group"] == "Message"]
    print(f"Snapshot {tag} | {k['customers']} customers | {k['orders']} orders")
    print(f"2nd order within 90 days: {k['second_order_90d']:.1%} (target {cfg['targets']['second_order_90d']:.0%})")
    print(f"Cancel+return rate: {k['cancel_return_rate']:.1%}  (last 90 days {k['cancel_return_rate_last90']:.1%})")
    print(f"Drifting {k['drifting']} | Lapsed {k['lapsed']} | Win-back pool KES {k['winback_pool_revenue']:,.0f}")
    print(f"Action list: {len(res.actions)} customers ({len(to_msg)} to message, "
          f"{len(res.actions) - len(to_msg)} hold-out) -> {out / f'action_list_{tag}.csv'}")
    for w in res.quality["warnings"]:
        print("  warning:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
