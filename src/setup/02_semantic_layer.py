# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Semantic layer: gold tables, benchmarks, metric views, ontology
# MAGIC
# MAGIC This notebook gives the loss ratio **one governed definition** that the dashboard, the Genie Agent, Genie One and the
# MAGIC alert job all share:
# MAGIC
# MAGIC 1. `gold_daily_loss_run`: incurred loss (as reported) and earned premium per day × line of business × region
# MAGIC 2. `lr_targets` and `industry_benchmarks`: the bars the book is judged against (with public sources)
# MAGIC 3. `mv_loss_ratio` and `mv_loss_run`: Unity Catalog **metric views** (YAML v1.1 with display names, synonyms and formats)
# MAGIC 4. `v_loss_ratio_watchlist`: the daily RAG view (7-day, 30-day and YTD loss ratio, frequency vs severity driver)
# MAGIC 5. **Ontology context**: table/column comments and governed tags that Genie Ontology picks up

# COMMAND ----------

dbutils.widgets.text("catalog", "")
dbutils.widgets.text("schema", "loss_ratio_coworker")
CATALOG = dbutils.widgets.get("catalog") or spark.sql("SELECT current_catalog()").first()[0]
SCHEMA = dbutils.widgets.get("schema")
spark.sql(f"USE CATALOG `{CATALOG}`")
spark.sql(f"USE SCHEMA `{SCHEMA}`")

def run(sql):
    spark.sql(sql)

# COMMAND ----------

# MAGIC %md ## 1 · Gold: daily loss run

# COMMAND ----------

run("""
CREATE OR REPLACE TABLE gold_daily_loss_run
COMMENT 'Daily loss run: incurred loss on the date it was reported, earned premium for the day, by line of business and region.'
AS
WITH c AS (
  SELECT CAST(report_date AS DATE) AS as_of_date, line_of_business, region,
         SUM(incurred_loss)                                       AS incurred_loss,
         SUM(paid_loss)                                           AS paid_loss,
         SUM(case_reserve)                                        AS case_reserve,
         COUNT(*)                                                 AS claim_count,
         SUM(CASE WHEN large_loss_flag THEN incurred_loss ELSE 0 END) AS large_loss_incurred,
         SUM(CASE WHEN large_loss_flag THEN 1 ELSE 0 END)         AS large_loss_count,
         SUM(CASE WHEN cat_event_flag THEN incurred_loss ELSE 0 END)  AS cat_incurred
  FROM claims GROUP BY ALL
)
SELECT CAST(e.as_of_date AS DATE) AS as_of_date, e.line_of_business, e.region, e.earned_premium,
       COALESCE(c.incurred_loss,0) incurred_loss, COALESCE(c.paid_loss,0) paid_loss,
       COALESCE(c.case_reserve,0) case_reserve, COALESCE(c.claim_count,0) claim_count,
       COALESCE(c.large_loss_incurred,0) large_loss_incurred, COALESCE(c.large_loss_count,0) large_loss_count,
       COALESCE(c.cat_incurred,0) cat_incurred
FROM earned_premium_daily e
LEFT JOIN c ON CAST(e.as_of_date AS DATE) = c.as_of_date
  AND e.line_of_business = c.line_of_business AND e.region = c.region
""")

# COMMAND ----------

# MAGIC %md ## 2 · Targets and public industry benchmarks

# COMMAND ----------

run("""
CREATE OR REPLACE TABLE lr_targets (
  line_of_business STRING COMMENT 'Line of business',
  target_loss_ratio DOUBLE COMMENT 'Planned loss ratio for the line (incurred / earned)',
  expense_ratio DOUBLE COMMENT 'Planned expense ratio (commission + operating expense / premium)',
  amber_pts DOUBLE COMMENT 'Points above target that turn the 7-day watch AMBER',
  red_pts DOUBLE COMMENT 'Points above target that turn the 7-day watch RED',
  owner_team STRING COMMENT 'Team accountable for the line'
) COMMENT 'Loss-ratio plan and alert thresholds per line of business.'
""")
run("""
INSERT INTO lr_targets VALUES
 ('Property',  0.60, 0.29, 10, 25, 'Property Underwriting'),
 ('Motor',     0.70, 0.24,  8, 20, 'Motor Underwriting'),
 ('Liability', 0.66, 0.30, 10, 25, 'Casualty Underwriting'),
 ('Marine',    0.57, 0.31, 15, 35, 'Marine Underwriting')
""")

run("""
CREATE OR REPLACE TABLE industry_benchmarks (
  benchmark STRING, metric STRING, period STRING, value DOUBLE, basis STRING, source STRING, url STRING
) COMMENT 'Public industry reference points for combined ratio. Used for context only, not as targets.'
""")
run("""
INSERT INTO industry_benchmarks VALUES
 ('US P&C industry', 'Combined ratio', 'FY2025', 0.929, 'Net combined ratio after dividends (statutory)', 'AM Best, 2025 Financial Results', 'https://www.ambest.com/bestlink/IndustryReports/pcssaft1new.pdf'),
 ('US P&C industry', 'Combined ratio', 'FY2024', 0.966, 'Net combined ratio after dividends (statutory)', 'AM Best, 2025 Financial Results', 'https://www.ambest.com/bestlink/IndustryReports/pcssaft1new.pdf'),
 ('AXA Group P&C', 'Combined ratio', 'FY2025', 0.906, 'All-year, discounted (as reported)', 'AXA Full-Year 2025 results', 'https://www.axa.com/press/press-releases/2025-full-year-earnings'),
 ('AXA Group P&C', 'Combined ratio', 'FY2025', 0.941, 'All-year, undiscounted (reported + 3.5 pts discount benefit)', 'AXA Full-Year 2025 results', 'https://www.axa.com/press/press-releases/2025-full-year-earnings')
""")

# COMMAND ----------

# MAGIC %md ## 3 · Metric views (YAML 1.1 with agent metadata)

# COMMAND ----------

PCT = "format:\n      type: percentage\n      decimal_places:\n        type: exact\n        places: 1"
EUR = "format:\n      type: currency\n      currency_code: EUR\n      decimal_places:\n        type: exact\n        places: 0\n      abbreviation: compact"

run(f"""
CREATE OR REPLACE VIEW mv_loss_ratio
WITH METRICS LANGUAGE YAML
COMMENT 'Governed loss ratio, combined ratio, frequency and severity by day, line of business and region.'
AS $$
version: 1.1
source: |
  SELECT g.*, t.target_loss_ratio, t.expense_ratio, t.owner_team
  FROM {CATALOG}.{SCHEMA}.gold_daily_loss_run g
  JOIN {CATALOG}.{SCHEMA}.lr_targets t USING (line_of_business)
fields:
  - name: as_of_date
    expr: as_of_date
    display_name: Report Date
    synonyms: [date, day, reported date, booking date]
  - name: week
    expr: DATE_TRUNC('WEEK', as_of_date)
    display_name: Week
  - name: month
    expr: DATE_TRUNC('MONTH', as_of_date)
    display_name: Month
  - name: line_of_business
    expr: line_of_business
    display_name: Line of Business
    synonyms: [LOB, line, class of business, product line]
  - name: region
    expr: region
    display_name: Region
    synonyms: [territory, market, geography]
  - name: owner_team
    expr: owner_team
    display_name: Owner Team
    synonyms: [accountable team, underwriting team]
measures:
  - name: earned_premium
    expr: SUM(earned_premium)
    display_name: Earned Premium
    synonyms: [EP, premium earned]
    {EUR}
  - name: incurred_loss
    expr: SUM(incurred_loss)
    display_name: Incurred Loss
    synonyms: [incurred, losses, claims cost]
    {EUR}
  - name: loss_ratio
    expr: SUM(incurred_loss) / NULLIF(SUM(earned_premium), 0)
    comment: Incurred loss (paid + case reserves, as reported) divided by earned premium. The approved management basis.
    display_name: Loss Ratio
    synonyms: [LR, loss run ratio, claims ratio, incurred loss ratio]
    {PCT}
  - name: target_loss_ratio
    expr: SUM(target_loss_ratio * earned_premium) / NULLIF(SUM(earned_premium), 0)
    comment: Plan loss ratio, weighted by earned premium.
    display_name: Target Loss Ratio
    synonyms: [plan, plan loss ratio, budget]
    {PCT}
  - name: loss_ratio_vs_target_pts
    expr: (SUM(incurred_loss) - SUM(target_loss_ratio * earned_premium)) / NULLIF(SUM(earned_premium), 0) * 100
    display_name: Loss Ratio vs Target (pts)
    synonyms: [variance to plan, gap to target]
  - name: combined_ratio
    expr: (SUM(incurred_loss) + SUM(expense_ratio * earned_premium)) / NULLIF(SUM(earned_premium), 0)
    comment: Loss ratio plus planned expense ratio. Below 100% is an underwriting profit.
    display_name: Combined Ratio
    synonyms: [COR, combined operating ratio]
    {PCT}
  - name: claim_count
    expr: SUM(claim_count)
    display_name: Claim Count
    synonyms: [number of claims, claims reported, frequency count]
  - name: avg_severity
    expr: SUM(incurred_loss) / NULLIF(SUM(claim_count), 0)
    display_name: Average Severity
    synonyms: [severity, average claim size, cost per claim]
    {EUR}
  - name: large_loss_share
    expr: SUM(large_loss_incurred) / NULLIF(SUM(incurred_loss), 0)
    display_name: Large Loss Share
    synonyms: [large losses, shock losses]
    {PCT}
  - name: cat_loss_ratio
    expr: SUM(cat_incurred) / NULLIF(SUM(earned_premium), 0)
    display_name: Cat Loss Ratio
    synonyms: [catastrophe loss ratio, nat cat, event losses]
    {PCT}
$$
""")

run(f"""
CREATE OR REPLACE VIEW mv_loss_run
WITH METRICS LANGUAGE YAML
COMMENT 'Claim-level loss run: incurred, paid, reserves and recoveries by cause of loss, status and event.'
AS $$
version: 1.1
source: {CATALOG}.{SCHEMA}.claims
fields:
  - name: report_date
    expr: report_date
    display_name: Report Date
  - name: loss_date
    expr: loss_date
    display_name: Loss Date
    synonyms: [date of loss, accident date]
  - name: line_of_business
    expr: line_of_business
    display_name: Line of Business
    synonyms: [LOB, line]
  - name: region
    expr: region
    display_name: Region
  - name: cause_of_loss
    expr: cause_of_loss
    display_name: Cause of Loss
    synonyms: [peril, cause, loss cause]
  - name: claim_status
    expr: claim_status
    display_name: Claim Status
  - name: event_tag
    expr: COALESCE(event_tag, 'Attritional')
    display_name: Event
    synonyms: [cat event, catastrophe, incident]
  - name: large_loss_flag
    expr: large_loss_flag
    display_name: Large Loss
measures:
  - name: incurred_loss
    expr: SUM(incurred_loss)
    display_name: Incurred Loss
    {EUR}
  - name: paid_loss
    expr: SUM(paid_loss)
    display_name: Paid Loss
    {EUR}
  - name: case_reserve
    expr: SUM(case_reserve)
    display_name: Case Reserves
    synonyms: [reserves, outstanding]
    {EUR}
  - name: claim_count
    expr: COUNT(claim_id)
    display_name: Claim Count
  - name: avg_severity
    expr: SUM(incurred_loss) / NULLIF(COUNT(claim_id), 0)
    display_name: Average Severity
    {EUR}
  - name: large_loss_count
    expr: SUM(CASE WHEN large_loss_flag THEN 1 ELSE 0 END)
    display_name: Large Loss Count
$$
""")

# COMMAND ----------

# MAGIC %md ## 4 · The daily watchlist (what the alert job and Genie One read every morning)

# COMMAND ----------

run("""
CREATE OR REPLACE VIEW v_loss_ratio_watchlist
COMMENT 'Daily loss-ratio watch per line of business and region: 7-day, 30-day and YTD loss ratio vs target, the main driver, and a RED/AMBER/GREEN status.'
AS
WITH d AS (
  SELECT g.*, t.target_loss_ratio, t.amber_pts, t.red_pts, t.owner_team,
         (SELECT MAX(as_of_date) FROM gold_daily_loss_run) AS today
  FROM gold_daily_loss_run g JOIN lr_targets t USING (line_of_business)
),
w AS (
  SELECT line_of_business, region, owner_team, MAX(today) AS as_of_date,
    MAX(target_loss_ratio) AS target_loss_ratio, MAX(amber_pts) amber_pts, MAX(red_pts) red_pts,
    SUM(IF(as_of_date >  today - 7,  incurred_loss, 0)) / NULLIF(SUM(IF(as_of_date >  today - 7,  earned_premium, 0)), 0) AS lr_7d,
    SUM(IF(as_of_date >  today - 30, incurred_loss, 0)) / NULLIF(SUM(IF(as_of_date >  today - 30, earned_premium, 0)), 0) AS lr_30d,
    SUM(IF(YEAR(as_of_date) = YEAR(today), incurred_loss, 0)) / NULLIF(SUM(IF(YEAR(as_of_date) = YEAR(today), earned_premium, 0)), 0) AS lr_ytd,
    -- frequency (claims per EUR 1m earned) and severity: last 7 days vs prior 90-day baseline
    SUM(IF(as_of_date > today - 7, claim_count, 0)) * 1e6 / NULLIF(SUM(IF(as_of_date > today - 7, earned_premium, 0)), 0) AS freq_7d,
    SUM(IF(as_of_date BETWEEN today - 97 AND today - 7, claim_count, 0)) * 1e6
      / NULLIF(SUM(IF(as_of_date BETWEEN today - 97 AND today - 7, earned_premium, 0)), 0) AS freq_base,
    SUM(IF(as_of_date > today - 7, incurred_loss, 0)) / NULLIF(SUM(IF(as_of_date > today - 7, claim_count, 0)), 0) AS sev_7d,
    SUM(IF(as_of_date BETWEEN today - 97 AND today - 7, incurred_loss, 0))
      / NULLIF(SUM(IF(as_of_date BETWEEN today - 97 AND today - 7, claim_count, 0)), 0) AS sev_base,
    SUM(IF(as_of_date > today - 7, large_loss_count, 0)) AS large_losses_7d,
    SUM(IF(as_of_date > today - 7, cat_incurred, 0)) / NULLIF(SUM(IF(as_of_date > today - 7, incurred_loss, 0)), 0) AS cat_share_7d,
    SUM(IF(as_of_date > today - 7, incurred_loss, 0)) AS incurred_7d
  FROM d GROUP BY line_of_business, region, owner_team
)
SELECT *,
  ROUND((lr_7d - target_loss_ratio) * 100, 1) AS gap_7d_pts,
  ROUND((lr_30d - target_loss_ratio) * 100, 1) AS gap_30d_pts,
  ROUND(freq_7d / NULLIF(freq_base, 0) - 1, 3) AS freq_change,
  ROUND(sev_7d / NULLIF(sev_base, 0) - 1, 3) AS sev_change,
  CASE
    WHEN cat_share_7d >= 0.5 THEN 'Catastrophe event'
    WHEN large_losses_7d > 0 AND sev_7d / NULLIF(sev_base,0) > 1.5 THEN 'Large loss'
    WHEN freq_7d / NULLIF(freq_base,0) - 1 >= 0.25 THEN 'Frequency'
    WHEN sev_7d / NULLIF(sev_base,0) - 1 >= 0.25 THEN 'Severity'
    ELSE 'Within normal variation'
  END AS primary_driver,
  CASE
    WHEN (lr_7d - target_loss_ratio) * 100 >= red_pts OR (lr_30d - target_loss_ratio) * 100 >= red_pts * 0.6 THEN 'RED'
    WHEN (lr_7d - target_loss_ratio) * 100 >= amber_pts THEN 'AMBER'
    ELSE 'GREEN'
  END AS status
FROM w
""")

# COMMAND ----------

# MAGIC %md ## 4b · Alert and action tables (written by the daily watch job, read by Genie and the dashboard)

# COMMAND ----------

run("""
CREATE TABLE IF NOT EXISTS lr_alerts (
  alert_id STRING, as_of_date DATE, line_of_business STRING, region STRING, status STRING, primary_driver STRING,
  lr_7d DOUBLE, lr_30d DOUBLE, target_loss_ratio DOUBLE, gap_7d_pts DOUBLE, freq_change DOUBLE, sev_change DOUBLE,
  large_losses_7d INT, genie_explanation STRING, created_at TIMESTAMP
) COMMENT 'Daily loss-ratio alerts (RED/AMBER) with the Genie Agent explanation of why the segment moved.'
""")
run("""
CREATE TABLE IF NOT EXISTS lr_action_recommendations (
  alert_id STRING, team STRING, action STRING, due_within STRING, priority STRING, state STRING, created_at TIMESTAMP
) COMMENT 'Actions recommended to downstream teams for each loss-ratio alert. state = Open / In progress / Done.'
""")

# COMMAND ----------

# MAGIC %md ## 5 · Ontology context: comments and governed tags
# MAGIC Genie Ontology combines this curated Unity Catalog context with what it infers from usage. Clear comments and tags
# MAGIC are the cheapest accuracy win you can buy.

# COMMAND ----------

COLS = {
  "claims": {
    "incurred_loss": "Paid loss plus case reserve for the claim (EUR), before recoveries.",
    "report_date": "Date the claim was reported to the insurer. Daily loss ratio uses this date.",
    "loss_date": "Date the loss event happened.",
    "cause_of_loss": "Peril that caused the loss (e.g. Hail, Collision, Bodily Injury).",
    "event_tag": "Named event the claim belongs to, null for attritional claims.",
    "large_loss_flag": "True when incurred loss is EUR 250k or more.",
    "cat_event_flag": "True when the claim belongs to a catastrophe event.",
  },
  "gold_daily_loss_run": {
    "as_of_date": "Report date (the day losses hit the books).",
    "earned_premium": "Premium earned on the day (EUR).",
    "incurred_loss": "Incurred loss reported on the day (EUR).",
  },
}
for tbl, cols in COLS.items():
    for col, txt in cols.items():
        run(f"ALTER TABLE {tbl} ALTER COLUMN {col} COMMENT '{txt}'")

# Tag keys can be governed by tag policies in your account; tagging is best-effort so it never blocks setup.
TAGS = "('business_area' = 'insurance', 'subject_area' = 'loss_ratio', 'lrc_certified' = 'true')"
for kind, objs in [("TABLE", ["claims", "gold_daily_loss_run", "lr_targets", "industry_benchmarks"]),
                   ("VIEW", ["mv_loss_ratio", "mv_loss_run", "v_loss_ratio_watchlist"])]:
    for obj in objs:
        try:
            run(f"ALTER {kind} {obj} SET TAGS {TAGS}")
        except Exception as e:
            print(f"tagging {obj} skipped: {str(e).splitlines()[0][:160]}")

# COMMAND ----------

display(spark.sql("SELECT line_of_business, region, status, primary_driver, ROUND(lr_7d,3) lr_7d, ROUND(lr_30d,3) lr_30d, target_loss_ratio, gap_7d_pts FROM v_loss_ratio_watchlist ORDER BY gap_7d_pts DESC"))
