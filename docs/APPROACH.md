# Approach: managing the loss ratio daily with an AI coworker

This document is the detailed design behind the repo. It is written for the CUO/CFO office, the data and AI team, and
the teams that act on loss-ratio signals.

---

## 1. Problem

The loss ratio (incurred loss ÷ earned premium) is the single most important number in P&C underwriting, yet most
carriers see it **monthly or quarterly**, through a slide built by an analyst. By then:

- a hailstorm's claims have been reported for three weeks without a reinsurance notification,
- a creeping rise in motor collision frequency has already been renewed into next year's book,
- a single large bodily-injury loss has distorted the month and triggered the wrong conversation.

The market context makes this sharper. The US P&C industry ran a **92.9% combined ratio in FY2025** (AM Best), down from
96.6% in 2024. AXA reported a **90.6% P&C combined ratio for FY2025** (94.1% undiscounted). In a market this profitable,
the gap between a good and an average year is a few points of loss ratio. Those points are won or lost in the weeks
between month-ends.

## 2. Goal and success measures

**Goal:** every morning, the right person sees which segments moved, why, and what to do, and can ask a follow-up in
the tools they already use, with the same governed number everywhere.

| Measure | Before (typical) | Target |
|---|---|---|
| Time from loss reported to the exec seeing the segment move | 3–6 weeks (month-end pack) | < 24 hours |
| Definitions of "loss ratio" in circulation | several (paid vs incurred, written vs earned) | 1 (metric view) |
| Time from signal to an owned action | ad hoc | same morning, owner and due date assigned |
| Analyst hours on the monthly loss-ratio pack | days | review only |

## 3. Design principles

1. **One definition, everywhere.** Loss ratio lives once, in a Unity Catalog metric view. The dashboard, Genie Agent,
   Genie One and the alert job all call it. Nobody recomputes it.
2. **Context before chat.** Accuracy comes from business context (definitions, synonyms, targets, owners, benchmarks),
   not from a cleverer prompt. Genie Ontology picks this context up from Unity Catalog.
3. **Deterministic where it matters.** Detection thresholds and the action playbook are rules in code and tables, so
   every alert and recommendation can be explained to an auditor. Genie explains *why*; it does not decide *whether*.
4. **Meet people where they work.** Executives live in Slack, Teams and email. The answer goes there, and the follow-up
   question can be asked there.
5. **Governed by default.** Every question runs with the asker's Unity Catalog permissions. Alerts and actions are
   written to Delta as an audit trail.

## 4. Architecture

![Flow](diagrams/flow.png)

| Layer | Component | In this repo |
|---|---|---|
| Data | Daily loss run (claims by report date) and earned premium per day | `src/setup/01_generate_loss_run.py` (synthetic) |
| Gold | `gold_daily_loss_run` (day × line of business × region) | `src/setup/02_semantic_layer.py` |
| Semantics | Metric views `mv_loss_ratio`, `mv_loss_run` (YAML 1.1 with display names, synonyms, formats) | same |
| Context | `lr_targets` (plan, thresholds, owner team), `industry_benchmarks` (AM Best, AXA), comments, tags | same |
| Watch | `v_loss_ratio_watchlist`: 7-day, 30-day and YTD loss ratio, frequency vs severity, RAG status, driver | same |
| Agent | Genie Agent *Loss Ratio Coworker* on the metric views, with instructions and example SQL | `deploy/genie_agent.py` |
| Dashboard | AI/BI dashboard *Daily Loss Ratio Watch* | `deploy/dashboard.py` |
| Coworker | Genie One skill `/loss-ratio-daily-brief` + scheduled task; Genie apps for Slack and Teams | `skills/`, `docs/CONNECT_SLACK_TEAMS_GMAIL_OUTLOOK.md` |
| Automation | Daily job: detect → explain (Genie) → recommend (playbook) → record → notify | `src/jobs/daily_loss_ratio_watch.py` |
| Delivery | Genie One **Slack** connector posts the brief; **Gmail** connector creates one draft per team (never sends) | `skills/` |

## 5. Detection logic

For every line of business × region, each morning:

| Signal | Definition |
|---|---|
| 7-day loss ratio | incurred reported in the last 7 days ÷ earned premium in the same days |
| 30-day and YTD loss ratio | same, over 30 days and the calendar year |
| Frequency change | claims per EUR 1m earned, last 7 days vs the prior 90 days |
| Severity change | incurred per claim, last 7 days vs the prior 90 days |
| Large losses | claims ≥ EUR 250k reported in the last 7 days |
| Cat share | share of 7-day incurred tagged to a catastrophe event |

**Status:** RED if the 7-day gap to target ≥ `red_pts` *or* the 30-day gap ≥ 60% of `red_pts`; AMBER if the 7-day gap ≥
`amber_pts`; otherwise GREEN. Thresholds are per line of business in `lr_targets` (Marine is wider because it is more
volatile).

**Driver** (first match wins): catastrophe event (cat share ≥ 50%) → large loss (a large loss and severity > 1.5× base)
→ frequency (+25%) → severity (+25%) → within normal variation.

Why 7 and 30 days: the 7-day window catches events fast; the 30-day window stops a slow creep from hiding under the
weekly noise. Exposure-weighting by earned premium keeps small segments from dominating.

## 6. From signal to action: the playbook

| Driver | Team | Recommended action | Due |
|---|---|---|---|
| Catastrophe event | Claims | Event response: surge adjusters, fast-track triage, daily event count | 24h |
| | Reinsurance | Notify cat XL reinsurers, estimate recovery vs retention | 48h |
| | Reserving | Book event IBNR for the month-end | 5 days |
| | Underwriting | Check accumulation, hold new binds above referral limit | 48h |
| | Finance | Flag gross and net impact in the month-end outlook | Month-end |
| Frequency | Underwriting | Review risk selection and rate adequacy at renewal; broker mix | Next renewals |
| | Pricing | Test the trend in the rating model; frequency load if it persists | 2 weeks |
| | Claims | Screen the new-claim cluster for fraud indicators | 1 week |
| Large loss | Claims | Senior handler, large-loss committee, reserve review | 48h |
| | Reinsurance | Check per-risk XL attachment, notify | 1 week |
| | Underwriting | Review the account and similar risks before renewal | Before renewal |
| Severity | Claims | Analyse repair, supplier and legal cost inflation | 2 weeks |
| | Pricing | Refresh severity trend assumptions | Next review |

Actions are written to `lr_action_recommendations` with priority (P1 for RED), owner team and state, so they can be
tracked, reported on and asked about ("@Genie what is still open for Reinsurance?").

## 7. How it makes an executive's life easier

| Moment | Before | With the coworker |
|---|---|---|
| 07:30 | Opens email, nothing on loss ratio until month-end | Genie One brief in Slack/Teams: 3 RED segments, why, who is acting |
| 08:00 exec meeting | "Do we know why Property is up?" Analyst request raised | `@Genie why did DACH Property jump?` → hail, 236 claims, cat share 78%, answered in the meeting |
| 09:00 | Reinsurance learns about the event at month-end | Reinsurance already has a P1 action with a 48h due date |
| 08:15 | Writes follow-up emails to six team leads | Opens Gmail: six drafts with actions, due dates and the why; reviews and sends |
| Board pack | Debate about whose loss ratio is right | One governed number, with the benchmark source next to it |

## 8. Governance and controls

- **Permissions:** Genie One, its connectors and the Genie Agent run with the user's Unity Catalog permissions through
  Unity Gateway. Connector write tools (Slack send, Gmail draft) are enabled per workspace by an admin.
- **Human in the loop:** Genie One drafts emails but cannot send them; every instruction to a team is sent by a person.
- **Explainability:** every Genie answer shows its SQL; every alert stores Genie's explanation, the thresholds and the
  driver; the playbook is code.
- **Benchmarks are context, not targets.** The instructions tell Genie to name the source and basis every time.
- **Data:** this repo is 100% synthetic. In production, point `01_generate_loss_run` at the real claims and policy feeds
  and keep everything else.

## 9. Rollout plan (6 weeks)

| Week | Step | Exit criteria |
|---|---|---|
| 1 | Agree the loss-ratio definition, targets and thresholds with Actuarial and Finance | Signed-off `lr_targets`; metric view reconciles to the monthly pack |
| 2 | Build gold + metric views on production feeds; add comments, synonyms, tags | Dashboard numbers match finance within tolerance |
| 3 | Genie Agent + 20-question benchmark from the CUO office | ≥ 90% correct on the benchmark |
| 4 | Slack/Teams apps enabled for a pilot channel; skill + 07:30 schedule | Exec pilot group uses it daily |
| 5 | Daily watch job live to team channels with the playbook | Every RED has an owner and due date the same morning |
| 6 | Review: alerts that mattered vs noise; tune thresholds; enable Slack posting and Gmail drafts | Threshold tuning agreed; scale to all lines |

## 10. Risks

| Risk | Mitigation |
|---|---|
| Alert fatigue from noisy 7-day ratios in small segments | Per-line thresholds, 30-day confirmation, exposure weighting, weekly tuning |
| Reported-basis loss ratio swings with reporting lags | Show 30-day and YTD next to 7-day; add IBNR in a later iteration |
| Executives trust a chat answer more than they should | Answers show SQL and source; definitions are governed; benchmark basis always stated |
| Teams app cannot run scheduled tasks | Proactive Teams alerts come from the job webhook (path B) |
| Preview features change | Slack/Teams apps and skill sharing are preview/Beta; the job and dashboard use GA APIs |
