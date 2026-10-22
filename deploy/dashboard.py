"""Create, update and publish the "Daily Loss Ratio Watch" AI/BI dashboard (widget helpers adapted from allianz-insurance-demo)."""
from __future__ import annotations

import json
import os

from common import DASHBOARD_TITLE, PARENT_PATH, WAREHOUSE_ID, cli, fq, new_id

COLORS = ["#1B3139", "#FF3621", "#00A972", "#F2A900", "#2272B4", "#8BCAE7"]
TODAY = f"(SELECT MAX(as_of_date) FROM {fq('gold_daily_loss_run')})"

DATASETS = {
    "kpi": f"""
      SELECT MEASURE(loss_ratio) AS ytd_loss_ratio, MEASURE(target_loss_ratio) AS ytd_target,
             MEASURE(combined_ratio) AS ytd_combined_ratio, MEASURE(loss_ratio_vs_target_pts) AS ytd_gap_pts,
             (SELECT COUNT(*) FROM {fq('v_loss_ratio_watchlist')} WHERE status = 'RED') AS red_segments,
             (SELECT COUNT(*) FROM {fq('v_loss_ratio_watchlist')} WHERE status = 'AMBER') AS amber_segments
      FROM {fq('mv_loss_ratio')} WHERE YEAR(as_of_date) = YEAR({TODAY})""",
    "trend": f"""
      WITH d AS (SELECT as_of_date, line_of_business, SUM(incurred_loss) inc, SUM(earned_premium) ep
                 FROM {fq('gold_daily_loss_run')} GROUP BY ALL)
      SELECT as_of_date, line_of_business,
             SUM(inc) OVER w / SUM(ep) OVER w AS rolling_7d_loss_ratio
      FROM d WHERE as_of_date >= {TODAY} - INTERVAL 120 DAYS
      WINDOW w AS (PARTITION BY line_of_business ORDER BY as_of_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)""",
    "lob_vs_target": f"""
      SELECT line_of_business, 'Actual (30 days)' AS measure, MEASURE(loss_ratio) AS value
      FROM {fq('mv_loss_ratio')} WHERE as_of_date > {TODAY} - INTERVAL 30 DAYS GROUP BY line_of_business
      UNION ALL
      SELECT line_of_business, 'Target', MEASURE(target_loss_ratio)
      FROM {fq('mv_loss_ratio')} WHERE as_of_date > {TODAY} - INTERVAL 30 DAYS GROUP BY line_of_business""",
    "watchlist": f"""
      SELECT status, line_of_business, region, primary_driver,
             ROUND(lr_7d * 100, 1) AS lr_7d_pct, ROUND(lr_30d * 100, 1) AS lr_30d_pct,
             ROUND(target_loss_ratio * 100, 1) AS target_pct, gap_7d_pts,
             ROUND(freq_change * 100, 0) AS freq_change_pct, ROUND(sev_change * 100, 0) AS sev_change_pct,
             large_losses_7d, owner_team
      FROM {fq('v_loss_ratio_watchlist')}
      ORDER BY CASE status WHEN 'RED' THEN 0 WHEN 'AMBER' THEN 1 ELSE 2 END, gap_7d_pts DESC""",
    "actions": f"""
      SELECT a.priority, a.team, al.line_of_business, al.region, al.primary_driver, a.action, a.due_within, a.state
      FROM {fq('lr_action_recommendations')} a JOIN {fq('lr_alerts')} al USING (alert_id)
      WHERE al.as_of_date = (SELECT MAX(as_of_date) FROM {fq('lr_alerts')})
      QUALIFY ROW_NUMBER() OVER (PARTITION BY a.alert_id, a.team ORDER BY a.created_at DESC) = 1
      ORDER BY a.priority, a.team""",
    "benchmarks": f"""
      SELECT 'Our book (YTD, synthetic)' AS benchmark, MEASURE(combined_ratio) AS combined_ratio
      FROM {fq('mv_loss_ratio')} WHERE YEAR(as_of_date) = YEAR({TODAY})
      UNION ALL
      SELECT CONCAT(benchmark, ' ', period, IF(basis LIKE '%undiscounted%', ' (undiscounted)', '')), value
      FROM {fq('industry_benchmarks')}""",
}


def pos(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


def text(lines, x, y, w, h):
    return {"widget": {"name": new_id()[:10], "multilineTextboxSpec": {"lines": lines}}, "position": pos(x, y, w, h)}


def query(ds, fields, disaggregated=False):
    return [{"name": "main_query", "query": {"datasetName": ds, "disaggregated": disaggregated,
                                             "fields": [{"name": n, "expression": e} for n, e in fields]}}]


def counter(ds, field, title, x, y, pct=False, w=1, h=3):
    enc = {"value": {"fieldName": field, "displayName": title}}
    if pct:
        enc["value"]["format"] = {"type": "number-percent", "decimalPlaces": {"type": "exact", "places": 1}}
    return {"widget": {"name": new_id()[:10], "queries": query(ds, [(field, f"`{field}`")], True),
                       "spec": {"version": 2, "widgetType": "counter", "encodings": enc,
                                "frame": {"showTitle": True, "title": title}}}, "position": pos(x, y, w, h)}


def chart(kind, ds, xf, yf, cf, title, x, y, w, h, xscale="categorical", agg="SUM", pct=True):
    yexpr = f"{agg}(`{yf}`)"
    enc = {"x": {"fieldName": xf, "scale": {"type": xscale}, "displayName": xf.replace("_", " ")},
           "y": {"fieldName": yf, "scale": {"type": "quantitative"}, "displayName": yf.replace("_", " ")},
           "color": {"fieldName": cf, "scale": {"type": "categorical"}, "displayName": cf.replace("_", " ")}}
    if pct:
        enc["y"]["format"] = {"type": "number-percent", "decimalPlaces": {"type": "exact", "places": 0}}
    spec = {"version": 3, "widgetType": kind, "encodings": enc, "frame": {"showTitle": True, "title": title},
            "mark": {"colors": COLORS}}
    if kind == "bar":
        spec["mark"]["layout"] = "group"
    fields = [(xf, f"`{xf}`"), (yf, yexpr), (cf, f"`{cf}`")]
    return {"widget": {"name": new_id()[:10], "queries": query(ds, fields), "spec": spec}, "position": pos(x, y, w, h)}


def hbar(ds, cat, val, title, x, y, w, h):
    """Horizontal bar with value labels, for comparing a handful of close percentages."""
    enc = {"y": {"fieldName": cat, "scale": {"type": "categorical", "sort": {"by": "x-reversed"}}, "displayName": " "},
           "x": {"fieldName": val, "scale": {"type": "quantitative"}, "displayName": "combined ratio",
                 "format": {"type": "number-percent", "decimalPlaces": {"type": "exact", "places": 1}}},
           "label": {"show": True}}
    spec = {"version": 3, "widgetType": "bar", "encodings": enc, "frame": {"showTitle": True, "title": title},
            "mark": {"colors": [COLORS[0]]}}
    return {"widget": {"name": new_id()[:10], "queries": query(ds, [(cat, f"`{cat}`"), (val, f"SUM(`{val}`)")]),
                       "spec": spec}, "position": pos(x, y, w, h)}


def table(ds, cols, title, x, y, w, h):
    return {"widget": {"name": new_id()[:10], "queries": query(ds, [(c, f"`{c}`") for c in cols], True),
                       "spec": {"version": 2, "widgetType": "table",
                                "encodings": {"columns": [{"fieldName": c, "displayName": c.replace("_", " ")} for c in cols]},
                                "frame": {"showTitle": True, "title": title}}}, "position": pos(x, y, w, h)}


def build() -> dict:
    layout = [
        text(["## Daily Loss Ratio Watch\n",
              "Synthetic European commercial P&C book · loss ratio = incurred / earned (governed metric view `mv_loss_ratio`) · "
              "refreshed every morning · alerts and recommended actions go to Slack and Teams\n"], 0, 0, 6, 2),
        counter("kpi", "ytd_loss_ratio", "YTD loss ratio", 0, 2, pct=True),
        counter("kpi", "ytd_target", "YTD target", 1, 2, pct=True),
        counter("kpi", "ytd_combined_ratio", "YTD combined ratio", 2, 2, pct=True),
        counter("kpi", "ytd_gap_pts", "Gap to target (pts)", 3, 2),
        counter("kpi", "red_segments", "RED segments today", 4, 2),
        counter("kpi", "amber_segments", "AMBER segments today", 5, 2),
        chart("line", "trend", "as_of_date", "rolling_7d_loss_ratio", "line_of_business",
              "Rolling 7-day loss ratio by line of business (last 120 days)", 0, 5, 4, 6, xscale="temporal"),
        hbar("benchmarks", "benchmark", "combined_ratio",
             "Combined ratio vs public benchmarks (AM Best, AXA FY2025)", 4, 5, 2, 6),
        chart("bar", "lob_vs_target", "line_of_business", "value", "measure",
              "30-day loss ratio vs target", 0, 11, 2, 6),
        table("watchlist", ["status", "line_of_business", "region", "primary_driver", "lr_7d_pct", "lr_30d_pct",
                            "target_pct", "gap_7d_pts", "freq_change_pct", "sev_change_pct", "large_losses_7d",
                            "owner_team"], "Today's watchlist (RED / AMBER / GREEN)", 2, 11, 4, 6),
        table("actions", ["priority", "team", "line_of_business", "region", "primary_driver", "action", "due_within",
                          "state"], "Recommended actions for downstream teams (latest run)", 0, 17, 6, 6),
    ]
    return {
        "datasets": [{"name": k, "displayName": k.replace("_", " ").title(), "queryLines": [" ".join(v.split()) + " "]}
                     for k, v in DATASETS.items()],
        "pages": [{"name": "watch", "displayName": "Daily watch", "layout": layout}],
    }


def main() -> None:
    serialized = json.dumps(build())
    os.makedirs("dashboards", exist_ok=True)
    with open("dashboards/daily_loss_ratio_watch.lvdash.json", "w") as f:
        json.dump(json.loads(serialized), f, indent=2)
    body = {"display_name": DASHBOARD_TITLE, "warehouse_id": WAREHOUSE_ID, "serialized_dashboard": serialized}
    if PARENT_PATH:
        body["parent_path"] = PARENT_PATH
    found = None
    for d in cli("api", "get", "/api/2.0/lakeview/dashboards").get("dashboards", []):
        if d.get("display_name") == DASHBOARD_TITLE and d.get("lifecycle_state") != "TRASHED":
            found = d["dashboard_id"]
    if found:
        body.pop("parent_path", None)
        cli("api", "patch", f"/api/2.0/lakeview/dashboards/{found}", body=body)
        dash_id = found
    else:
        dash_id = cli("api", "post", "/api/2.0/lakeview/dashboards", body=body)["dashboard_id"]
    cli("api", "post", f"/api/2.0/lakeview/dashboards/{dash_id}/published",
        body={"embed_credentials": True, "warehouse_id": WAREHOUSE_ID})
    print(f"DASHBOARD_ID={dash_id}")


if __name__ == "__main__":
    main()
