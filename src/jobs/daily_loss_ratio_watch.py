# Databricks notebook source
# MAGIC %md
# MAGIC # Daily loss-ratio watch: detect, explain, recommend, notify
# MAGIC
# MAGIC Runs every weekday morning after the overnight claims load.
# MAGIC
# MAGIC 1. **Detect**: read `v_loss_ratio_watchlist` and keep RED/AMBER segments.
# MAGIC 2. **Explain**: ask the loss-ratio **Genie Agent** *why* each segment moved (frequency vs severity, causes, large losses).
# MAGIC    The question goes through the same governed agent that Genie One uses, so the explanation matches what an exec would get.
# MAGIC 3. **Recommend**: map the driver to a deterministic **action playbook** per downstream team (Claims, Underwriting,
# MAGIC    Pricing, Reinsurance, Reserving, Finance). The playbook is rules, not an LLM, so actions are auditable.
# MAGIC 4. **Record**: write alerts and actions to Delta (`lr_alerts`, `lr_action_recommendations`) for follow-up and audit.
# MAGIC 5. **Notify**: post a Slack message (Block Kit) and a Microsoft Teams card (Adaptive Card) to each team's channel.

# COMMAND ----------

dbutils.widgets.text("catalog", "")
dbutils.widgets.text("schema", "loss_ratio_coworker")
dbutils.widgets.text("genie_space_id", "")
dbutils.widgets.text("secret_scope", "loss_ratio_coworker")
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"])

CATALOG = dbutils.widgets.get("catalog") or spark.sql("SELECT current_catalog()").first()[0]
SCHEMA = dbutils.widgets.get("schema")
SPACE_ID = dbutils.widgets.get("genie_space_id")
SCOPE = dbutils.widgets.get("secret_scope")
DRY_RUN = dbutils.widgets.get("dry_run") == "true"
FQ = f"`{CATALOG}`.`{SCHEMA}`"

# COMMAND ----------

# MAGIC %md ## 1 · Detect

# COMMAND ----------

watch = spark.sql(f"""
  SELECT * FROM {FQ}.v_loss_ratio_watchlist
  WHERE status IN ('RED','AMBER')
  ORDER BY CASE status WHEN 'RED' THEN 0 ELSE 1 END, gap_7d_pts DESC
""").toPandas()
benchmark = spark.sql(f"""
  SELECT value FROM {FQ}.industry_benchmarks
  WHERE benchmark = 'US P&C industry' AND period = 'FY2025'
""").first()[0]
book = spark.sql(f"""
  SELECT MEASURE(loss_ratio) lr, MEASURE(combined_ratio) cr
  FROM {FQ}.mv_loss_ratio WHERE YEAR(as_of_date) = YEAR(current_date())
""").first()
print(f"{len(watch)} segments need attention | book YTD LR {book.lr:.1%}, COR {book.cr:.1%} vs industry {benchmark:.1%}")
display(watch)

# COMMAND ----------

# MAGIC %md ## 2 · Explain with the Genie Agent

# COMMAND ----------

import time
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

def ask_genie(question: str) -> str:
    """Genie Conversation API over REST (works with any databricks-sdk version on serverless)."""
    if not SPACE_ID:
        return ""
    try:
        base = f"/api/2.0/genie/spaces/{SPACE_ID}"
        start = w.api_client.do("POST", f"{base}/start-conversation", body={"content": question})
        cid, mid = start["conversation_id"], start["message_id"]
        for _ in range(60):
            msg = w.api_client.do("GET", f"{base}/conversations/{cid}/messages/{mid}")
            if msg.get("status") in ("COMPLETED", "FAILED", "CANCELLED"):
                break
            time.sleep(3)
        atts = msg.get("attachments") or []
        parts = [a["text"]["content"] for a in atts if a.get("text", {}).get("content")]
        if not parts:
            parts = [a["query"]["description"] for a in atts if a.get("query", {}).get("description")]
        return "\n".join(parts).strip()
    except Exception as e:  # never block the alert on the explanation
        return f"(Genie explanation unavailable: {e})"

explanations = {}
for r in watch.itertuples():
    q = (f"Why did the {r.line_of_business} loss ratio in {r.region} rise over the last 7 days? "
         f"Split it into claim frequency versus severity, list the top causes of loss and any large or "
         f"catastrophe losses, and compare it with the target loss ratio. Answer in three short bullet points.")
    explanations[(r.line_of_business, r.region)] = ask_genie(q)

# COMMAND ----------

# MAGIC %md ## 3 · Recommend: the action playbook

# COMMAND ----------

PLAYBOOK = {
    "Catastrophe event": [
        ("Claims", "Stand up the event response: surge adjusters, fast-track triage of the event claims, daily event count.", "24h"),
        ("Reinsurance", "Notify cat XL reinsurers of a potential event and estimate the recovery against the retention.", "48h"),
        ("Reserving", "Book an event IBNR estimate so the month-end loss ratio reflects claims not yet reported.", "5 days"),
        ("Underwriting", "Check accumulation in the affected area and hold new binds above the referral limit.", "48h"),
        ("Finance", "Flag the event in the month-end outlook with gross and net-of-reinsurance impact.", "Month-end"),
    ],
    "Frequency": [
        ("Underwriting", "Review risk selection and rate adequacy on renewals in this segment; check broker and channel mix.", "Next renewal cycle"),
        ("Pricing", "Test whether the frequency trend is in the rating model and price a frequency load if it persists.", "2 weeks"),
        ("Claims", "Screen the cluster of new claims for fraud indicators and repeat insureds.", "1 week"),
    ],
    "Large loss": [
        ("Claims", "Assign a senior handler, convene the large-loss committee and review the case reserve.", "48h"),
        ("Reinsurance", "Check whether the claim reaches the per-risk XL attachment and notify reinsurers.", "1 week"),
        ("Underwriting", "Review the account and similar risks before renewal.", "Before renewal"),
    ],
    "Severity": [
        ("Claims", "Analyse repair, supplier and legal cost inflation on the affected causes of loss.", "2 weeks"),
        ("Pricing", "Refresh the severity trend assumption in pricing.", "Next pricing review"),
    ],
    "Within normal variation": [
        ("Underwriting", "Monitor. No action unless the 30-day loss ratio also breaches.", "Next daily run"),
    ],
}

alerts, actions = [], []
for r in watch.itertuples():
    alert_id = f"LR-{r.as_of_date:%Y%m%d}-{r.line_of_business[:3].upper()}-{r.region.split()[0][:4].upper()}"
    alerts.append(dict(alert_id=alert_id, as_of_date=r.as_of_date, line_of_business=r.line_of_business,
                       region=r.region, status=r.status, primary_driver=r.primary_driver,
                       lr_7d=float(r.lr_7d), lr_30d=float(r.lr_30d), target_loss_ratio=float(r.target_loss_ratio),
                       gap_7d_pts=float(r.gap_7d_pts), freq_change=float(r.freq_change or 0),
                       sev_change=float(r.sev_change or 0), large_losses_7d=int(r.large_losses_7d),
                       genie_explanation=explanations[(r.line_of_business, r.region)]))
    for team, action, due in PLAYBOOK[r.primary_driver]:
        actions.append(dict(alert_id=alert_id, team=team, action=action, due_within=due,
                            priority="P1" if r.status == "RED" else "P2", state="Open"))

# COMMAND ----------

# MAGIC %md ## 4 · Record (append-only audit trail)

# COMMAND ----------

import pandas as pd
from pyspark.sql import functions as F

if alerts:
    (spark.createDataFrame(pd.DataFrame(alerts)).withColumn("created_at", F.current_timestamp())
        .write.mode("append").option("mergeSchema", "true").saveAsTable(f"{FQ}.lr_alerts"))
    (spark.createDataFrame(pd.DataFrame(actions)).withColumn("created_at", F.current_timestamp())
        .write.mode("append").option("mergeSchema", "true").saveAsTable(f"{FQ}.lr_action_recommendations"))
print(f"recorded {len(alerts)} alerts and {len(actions)} actions")

# COMMAND ----------

# MAGIC %md ## 5 · Notify Slack and Microsoft Teams
# MAGIC Webhook URLs live in a secret scope (`slack_webhook_url`, `teams_webhook_url`). With `dry_run=true` the payloads are
# MAGIC printed instead of sent.

# COMMAND ----------

import json, requests

ICON = {"RED": ":red_circle:", "AMBER": ":large_orange_circle:"}

def slack_payload(a, acts):
    lines = "\n".join(f"• *{x['team']}* ({x['due_within']}): {x['action']}" for x in acts)
    return {"text": f"Loss ratio {a['status']}: {a['line_of_business']} / {a['region']}", "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": f"Loss ratio alert: {a['line_of_business']}, {a['region']}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Status*\n{ICON[a['status']]} {a['status']}"},
            {"type": "mrkdwn", "text": f"*Driver*\n{a['primary_driver']}"},
            {"type": "mrkdwn", "text": f"*7-day LR*\n{a['lr_7d']:.0%} vs target {a['target_loss_ratio']:.0%} (+{a['gap_7d_pts']:.0f} pts)"},
            {"type": "mrkdwn", "text": f"*30-day LR*\n{a['lr_30d']:.0%}"}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Why (Genie)*\n{a['genie_explanation'][:2500] or '_n/a_'}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Recommended actions*\n{lines}"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text":
            f"Alert {a['alert_id']} · book YTD COR {book.cr:.1%} vs US P&C FY2025 {benchmark:.1%} (AM Best) · ask follow-ups with @Genie"}]},
    ]}

def teams_payload(a, acts):
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
        "body": [
            {"type": "TextBlock", "size": "Large", "weight": "Bolder",
             "text": f"Loss ratio {a['status']}: {a['line_of_business']}, {a['region']}",
             "color": "Attention" if a["status"] == "RED" else "Warning"},
            {"type": "FactSet", "facts": [
                {"title": "Driver", "value": a["primary_driver"]},
                {"title": "7-day LR", "value": f"{a['lr_7d']:.0%} (target {a['target_loss_ratio']:.0%}, +{a['gap_7d_pts']:.0f} pts)"},
                {"title": "30-day LR", "value": f"{a['lr_30d']:.0%}"},
                {"title": "Alert", "value": a["alert_id"]}]},
            {"type": "TextBlock", "wrap": True, "text": f"**Why (Genie)** {a['genie_explanation'][:1500]}"},
            {"type": "TextBlock", "weight": "Bolder", "text": "Recommended actions"},
            *[{"type": "TextBlock", "wrap": True, "text": f"- **{x['team']}** ({x['due_within']}): {x['action']}"} for x in acts],
        ]}}]}

def secret(key):
    try:
        return dbutils.secrets.get(SCOPE, key)
    except Exception:
        return None

slack_url, teams_url = secret("slack_webhook_url"), secret("teams_webhook_url")
for a in alerts:
    acts = [x for x in actions if x["alert_id"] == a["alert_id"]]
    for name, url, payload in [("slack", slack_url, slack_payload(a, acts)), ("teams", teams_url, teams_payload(a, acts))]:
        if DRY_RUN or not url:
            print(f"[{name} dry-run] {a['alert_id']}\n{json.dumps(payload)[:600]}...\n")
        else:
            resp = requests.post(url, json=payload, timeout=20)
            print(f"[{name}] {a['alert_id']} -> HTTP {resp.status_code}")
