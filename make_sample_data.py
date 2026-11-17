"""
Create a small, de-identified SAMPLE file for the public repo / submission.

The real export stays private (data/ is git-ignored). The sample:
  * keeps 150 random customers' order histories,
  * replaces customer and order IDs with new codes,
  * shifts each customer's dates by a random -10..+10 days (gaps preserved),
  * shuffles gender / age group / county between customers,
so no row can be traced back to a real customer, but the app and tests run
exactly as they do on the real file.
"""
import sys

import numpy as np
import pandas as pd

src = sys.argv[1] if len(sys.argv) > 1 else "data/Immaculate_Vibes_Sales.csv"
dst = sys.argv[2] if len(sys.argv) > 2 else "sample_data/sample_sales.csv"
rng = np.random.default_rng(8402)

df = pd.read_csv(src, keep_default_na=False, na_values=[""], parse_dates=["order_datetime"])
keep = rng.choice(df["customer_id"].unique(), size=150, replace=False)
s = df[df["customer_id"].isin(keep)].copy()

new_id = {c: f"S{i:04d}" for i, c in enumerate(rng.permutation(keep), 1)}
shift = {c: pd.Timedelta(days=int(rng.integers(-10, 11))) for c in keep}
s["order_datetime"] = s.apply(lambda r: r["order_datetime"] + shift[r["customer_id"]], axis=1)
lo, hi = df["order_datetime"].min(), df["order_datetime"].max()
s["order_datetime"] = s["order_datetime"].clip(lo, hi)

prof = s.groupby("customer_id")[["customer_gender", "customer_age_group", "customer_county"]].first()
prof[:] = prof.sample(frac=1, random_state=1).values
s = s.drop(columns=prof.columns).merge(prof, left_on="customer_id", right_index=True)
s["customer_id"] = s["customer_id"].map(new_id)

s = s.sort_values("order_datetime").reset_index(drop=True)
s["order_id"] = [f"SMP-{i:06d}" for i in range(1, len(s) + 1)]
s["order_number_for_customer"] = s.groupby("customer_id").cumcount() + 1
s["customer_type"] = np.where(s["order_number_for_customer"] == 1, "New", "Returning")
d = s["order_datetime"]
s["order_date"], s["order_time"] = d.dt.strftime("%Y-%m-%d"), d.dt.strftime("%H:%M:%S")
s["year"], s["quarter"] = d.dt.year, "Q" + d.dt.quarter.astype(str)
s["year_month"], s["weekday"], s["hour"] = d.dt.strftime("%Y-%m"), d.dt.day_name(), d.dt.hour
s["is_weekend"] = d.dt.dayofweek >= 5
s = s[df.columns]
s.to_csv(dst, index=False)
print(f"Wrote {dst}: {len(s)} orders, {s['customer_id'].nunique()} customers")
