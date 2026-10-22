# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Generate a daily P&C loss run (synthetic)
# MAGIC
# MAGIC Builds a **synthetic** European commercial P&C book that is refreshed *as of today*, so the loss ratio can be
# MAGIC watched **daily** rather than at quarter-end. Everything is generated with a fixed seed. No real insurer data is used.
# MAGIC
# MAGIC | Table | Grain | What it is |
# MAGIC |---|---|---|
# MAGIC | `policies` | policy | 150,000 annual policies, 4 lines of business, 5 regions |
# MAGIC | `claims` | claim | **the loss run**: loss/report date, cause, paid, reserve, incurred, recovery, status |
# MAGIC | `earned_premium_daily` | LOB × region × day | premium earned each day (pro-rata of written premium) |
# MAGIC
# MAGIC Three **seeded events** in the last three weeks give the daily watch something real to find:
# MAGIC 1. **Hailstorm, DACH Property** (loss dates T-6…T-2): a burst of high-severity claims, several large losses.
# MAGIC 2. **Frequency creep, UK & Ireland Motor** (last 21 days): ~45% more collisions, normal severity.
# MAGIC 3. **Large bodily-injury loss, Southern Europe Liability** (reported T-3): one EUR 2.4m claim.

# COMMAND ----------

dbutils.widgets.text("catalog", "")
dbutils.widgets.text("schema", "loss_ratio_coworker")
CATALOG = dbutils.widgets.get("catalog") or spark.sql("SELECT current_catalog()").first()[0]
SCHEMA = dbutils.widgets.get("schema")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`")
print(f"Target: {CATALOG}.{SCHEMA}")

# COMMAND ----------

import numpy as np
import pandas as pd
from datetime import date, timedelta

SEED = 42
rng = np.random.default_rng(SEED)

TODAY = np.datetime64(date.today())
START = np.datetime64("2025-01-01")
DAYS = pd.date_range(str(START), str(TODAY), freq="D")
N_DAYS = len(DAYS)

LOBS = ["Property", "Motor", "Liability", "Marine"]
LOB_W = np.array([34, 30, 24, 12], float)
REGIONS = ["UK & Ireland", "France", "DACH", "Southern Europe", "Benelux"]
REG_W = np.array([26, 20, 24, 18, 12], float)
BROKERS = ["Broker A", "Broker B", "Broker C", "Broker D", "Direct"]

PREM_MU = {"Property": 10.6, "Motor": 10.2, "Liability": 10.4, "Marine": 10.9}
FREQ = {"Property": 0.55, "Motor": 1.35, "Liability": 0.45, "Marine": 0.75}       # claims / policy-year
SEV_MU = {"Property": 8.9, "Motor": 7.7, "Liability": 9.3, "Marine": 8.7}
SEV_SIG = {"Property": 0.9, "Motor": 0.7, "Liability": 1.0, "Marine": 0.9}
LAG = {"Property": 6, "Motor": 4, "Liability": 30, "Marine": 12}                   # mean report lag (days)
TARGET_LR = {"Property": 0.60, "Motor": 0.70, "Liability": 0.66, "Marine": 0.57}   # calibration level
CAUSES = {
    "Property": (["Fire", "Flood", "Storm/Wind", "Water Damage", "Theft", "Hail"], [22, 12, 20, 22, 16, 8]),
    "Motor": (["Collision", "Theft", "Third-Party PD", "Third-Party BI", "Weather"], [46, 10, 22, 14, 8]),
    "Liability": (["Bodily Injury", "Property Damage", "Professional Error", "Product Liability"], [34, 28, 22, 16]),
    "Marine": (["Cargo Damage", "Cargo Theft", "Vessel Collision", "Heavy Weather"], [38, 16, 18, 28]),
}

# COMMAND ----------

# MAGIC %md ## Policies

# COMMAND ----------

N_POL = 150_000
lob = rng.choice(LOBS, N_POL, p=LOB_W / LOB_W.sum())
region = rng.choice(REGIONS, N_POL, p=REG_W / REG_W.sum())
inception = np.datetime64("2024-01-01") + rng.integers(0, (TODAY - np.datetime64("2024-01-01")).astype(int) - 14, N_POL).astype("timedelta64[D]")
expiry = inception + np.timedelta64(365, "D")
written = np.round(np.exp([rng.normal(PREM_MU[l], 0.8) for l in lob]), 2)

policies = pd.DataFrame({
    "policy_id": [f"POL-{i:07d}" for i in range(1_000_000, 1_000_000 + N_POL)],
    "line_of_business": lob,
    "region": region,
    "broker": rng.choice(BROKERS, N_POL, p=[0.26, 0.24, 0.2, 0.18, 0.12]),
    "inception_date": pd.to_datetime(inception),
    "expiry_date": pd.to_datetime(expiry),
    "written_premium": written,
    "sum_insured": np.round(written * rng.uniform(15, 60, N_POL), 0),
    "underwriting_year": pd.to_datetime(inception).year,
})

# COMMAND ----------

# MAGIC %md ## Earned premium per day (LOB × region × day)

# COMMAND ----------

day0 = START.astype("datetime64[D]")
idx_start = np.clip((inception - day0).astype(int), 0, N_DAYS)
idx_end = np.clip((expiry - day0).astype(int), 0, N_DAYS)          # exclusive
daily = written / 365.0
keys = [(l, r) for l in LOBS for r in REGIONS]
earned_rows = []
for l, r in keys:
    m = (lob == l) & (region == r)
    diff = np.zeros(N_DAYS + 1)
    np.add.at(diff, idx_start[m], daily[m])
    np.add.at(diff, idx_end[m], -daily[m])
    earned = np.cumsum(diff)[:N_DAYS]
    earned_rows.append(pd.DataFrame({"as_of_date": DAYS, "line_of_business": l, "region": r,
                                     "earned_premium": np.round(earned, 2)}))
earned_premium_daily = pd.concat(earned_rows, ignore_index=True)

# COMMAND ----------

# MAGIC %md ## Claims: the loss run (baseline, then the three seeded events)

# COMMAND ----------

def draw_claims(pol_idx, n_per_policy, loss_lo, loss_hi, cause=None, sev=None, large_p=0.001):
    rows = []
    for i, n in zip(pol_idx, n_per_policy):
        l = lob[i]
        for _ in range(int(n)):
            lo = max(loss_lo[i] if np.ndim(loss_lo) else loss_lo, START)
            hi = min(loss_hi[i] if np.ndim(loss_hi) else loss_hi, TODAY)
            if hi <= lo:
                continue
            loss_dt = lo + np.timedelta64(int(rng.integers(0, (hi - lo).astype(int))), "D")
            report_dt = loss_dt + np.timedelta64(int(rng.exponential(LAG[l])), "D")
            if report_dt > TODAY:
                continue
            inc = float(np.exp(rng.normal(*(sev or (SEV_MU[l], SEV_SIG[l])))))
            if rng.random() < large_p:
                inc *= rng.uniform(8, 20)
            c = cause or rng.choice(CAUSES[l][0], p=np.array(CAUSES[l][1]) / sum(CAUSES[l][1]))
            rows.append((policies.policy_id[i], l, region[i], loss_dt, report_dt, c, inc))
    return rows

# baseline: Poisson by exposure inside the observation window
exposure_years = (np.minimum(expiry, TODAY) - np.maximum(inception, START)).astype(int).clip(0) / 365.0
n_claims = rng.poisson([FREQ[l] * e for l, e in zip(lob, exposure_years)])
base = draw_claims(range(N_POL), n_claims, np.maximum(inception, START), np.minimum(expiry, TODAY))
claims = pd.DataFrame(base, columns=["policy_id", "line_of_business", "region", "loss_date",
                                     "report_date", "cause_of_loss", "incurred_loss"])

# calibrate each LOB to a credible baseline loss ratio (shape preserved, level scaled)
ep = earned_premium_daily.groupby("line_of_business").earned_premium.sum()
inc = claims.groupby("line_of_business").incurred_loss.sum()
claims["incurred_loss"] *= claims.line_of_business.map({l: TARGET_LR[l] * ep[l] / inc[l] for l in LOBS})
claims["event_tag"] = None

in_force = lambda l, r: np.where((lob == l) & (region == r) & (inception <= TODAY) & (expiry > TODAY))[0]
scale_of = lambda l: TARGET_LR[l] * ep[l] / inc[l]
T = lambda d: TODAY - np.timedelta64(d, "D")

# Event 1: hailstorm, DACH Property. ~420 claims with loss dates T-6..T-2, average ~EUR 45k, a handful above EUR 250k
p = rng.choice(in_force("Property", "DACH"), 420, replace=False)
ev1 = pd.DataFrame(draw_claims(p, np.ones(len(p)), T(6), T(1), cause="Hail", sev=(10.45, 0.75), large_p=0.0),
                   columns=claims.columns[:7])
ev1["event_tag"] = "Hailstorm DACH"

# Event 2: frequency creep, UK & Ireland Motor. 45% more collisions over the last 21 days at normal severity
p = in_force("Motor", "UK & Ireland")
n_extra = rng.poisson(0.45 * 21 * len(p) * FREQ["Motor"] / 365)
ev2 = pd.DataFrame(draw_claims(rng.choice(p, n_extra), np.ones(n_extra), T(21), TODAY, cause="Collision", large_p=0.0),
                   columns=claims.columns[:7])
ev2["incurred_loss"] *= scale_of("Motor"); ev2["event_tag"] = "Motor frequency creep"

# Event 3: one large bodily-injury loss, Southern Europe Liability, reported T-3
i = int(rng.choice(in_force("Liability", "Southern Europe")))
ev3 = pd.DataFrame([(policies.policy_id[i], "Liability", "Southern Europe", T(9), T(3), "Bodily Injury", 2_400_000.0)],
                   columns=claims.columns[:7])
ev3["event_tag"] = "Large BI loss"

claims = pd.concat([claims, ev1, ev2, ev3], ignore_index=True)

# paid / reserve / recovery / status
age = (TODAY - claims.report_date.values.astype("datetime64[D]")).astype(int)
p_closed = np.clip(0.2 + age / 400 - np.where(claims.line_of_business == "Liability", 0.25, 0), 0.05, 0.95)
closed = rng.random(len(claims)) < p_closed
claims["incurred_loss"] = claims.incurred_loss.round(2)
claims["paid_loss"] = np.where(closed, claims.incurred_loss, (claims.incurred_loss * rng.uniform(0.05, 0.6, len(claims))).round(2))
claims["case_reserve"] = (claims.incurred_loss - claims.paid_loss).round(2)
claims["claim_status"] = np.where(closed, "Closed", "Open")
rec = claims.cause_of_loss.isin(["Theft", "Cargo Theft", "Third-Party PD", "Vessel Collision", "Property Damage"])
claims["recovery_amount"] = np.where(rec & (rng.random(len(claims)) < 0.4), (claims.incurred_loss * rng.uniform(0.05, 0.3, len(claims))).round(2), 0.0)
claims["large_loss_flag"] = claims.incurred_loss >= 250_000
claims["cat_event_flag"] = claims.event_tag.eq("Hailstorm DACH")
claims = claims.sort_values("report_date").reset_index(drop=True)
claims.insert(0, "claim_id", [f"CLM-{i:07d}" for i in range(3_000_000, 3_000_000 + len(claims))])
claims["loss_date"] = pd.to_datetime(claims.loss_date).dt.date
claims["report_date"] = pd.to_datetime(claims.report_date).dt.date
earned_premium_daily["as_of_date"] = earned_premium_daily.as_of_date.dt.date
policies["inception_date"] = policies.inception_date.dt.date
policies["expiry_date"] = policies.expiry_date.dt.date

# COMMAND ----------

def write(df, name, comment):
    (spark.createDataFrame(df).write.mode("overwrite").option("overwriteSchema", "true")
         .saveAsTable(f"`{CATALOG}`.`{SCHEMA}`.{name}"))
    spark.sql(f"COMMENT ON TABLE `{CATALOG}`.`{SCHEMA}`.{name} IS '{comment}'")
    print(f"wrote {name:<22} {len(df):>8,} rows")

write(policies, "policies", "Synthetic commercial P&C policies (one row per annual policy).")
write(claims, "claims", "Synthetic loss run: one row per claim with loss/report dates, cause, paid, reserve, incurred, recovery and status.")
write(earned_premium_daily, "earned_premium_daily", "Premium earned per day by line of business and region (pro-rata of written premium).")

lr = claims.incurred_loss.sum() / earned_premium_daily.earned_premium.sum()
print(f"\nAs of {TODAY}: book loss ratio {lr:.1%} | {len(claims):,} claims | seeded events: "
      f"{len(ev1)} hail, {len(ev2)} motor, {len(ev3)} large BI")
