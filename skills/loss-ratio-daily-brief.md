# Genie One skill: `/loss-ratio-daily-brief`

Paste the **Skill instructions** below into Genie One (*"Create a skill called loss-ratio-daily-brief with these
instructions…"*), in the Genie One UI or from the Databricks Genie app in Slack. Then:

- run it on demand: `/loss-ratio-daily-brief` (or `/loss-ratio-daily-brief Motor`)
- connect **Slack** and **Gmail** or **Microsoft 365** (for Outlook) in Genie One. Steps 6–7 need the connectors' write
  tools (Slack send message, Gmail or Outlook draft), which an admin may need to enable. Setup:
  [`docs/CONNECT_SLACK_TEAMS_GMAIL_OUTLOOK.md`](../docs/CONNECT_SLACK_TEAMS_GMAIL_OUTLOOK.md)
- schedule it: *"Run /loss-ratio-daily-brief every weekday at 07:30 Europe/London"*. Genie One scheduled tasks post the
  result in a chat thread and send an email with a PDF. You can create scheduled tasks from the Genie app in Slack.
- share it with your team (skill sharing is Beta at the time of writing)

---

## Skill instructions

**Name:** loss-ratio-daily-brief
**When to use:** the user asks for the morning loss-ratio briefing, "how is the loss ratio today", "what's red", or
"what should my team do about the loss ratio".

**Steps**

1. Use the **Loss Ratio Coworker** Genie Agent. Do not compute loss ratio from raw tables. Always use the governed
   metric view `mv_loss_ratio` (loss ratio = incurred loss / earned premium).
2. Read `v_loss_ratio_watchlist` for today. List every RED segment, then AMBER, with:
   7-day loss ratio, 30-day loss ratio, target, gap in points, and `primary_driver`.
   If the user named a line of business or region, filter to it.
3. For each RED segment, explain **why** in one sentence: frequency vs severity change against the prior 90 days,
   the top cause of loss and any event or large loss (from `mv_loss_run`).
4. Read `lr_action_recommendations` for the latest alerts and group the actions **by team** (Claims, Underwriting,
   Pricing, Reinsurance, Reserving, Finance), with priority and due-within.
5. Close with the book context: year-to-date loss ratio and combined ratio versus target, and versus the public
   benchmarks in `industry_benchmarks` (AM Best US P&C FY2025 92.9%; AXA Group P&C FY2025 90.6% reported, 94.1%
   undiscounted). Name the source. Do not present benchmarks as targets.

6. **Slack** (Genie One Slack connector): post the executive version to `#exec-loss-ratio`: the headline, the RED
   segments with gap and driver, and the P1 actions by team.
7. **Gmail** (Genie One Gmail connector): create **one draft per team** that has P1/P2 actions, addressed to that team's
   lead, subject `Loss ratio actions for <team> – <date>`, listing the team's actions with due dates and a one-line why.
   **Only create drafts. Never send email.** The executive reviews and sends.
   *Outlook variant:* replace "Gmail connector" with "Microsoft 365 connector" and create **Outlook drafts** instead.
   Neither connector can send email.

**Output format** (keep it readable on a phone):

```
Loss ratio brief · <date>
🔴 RED
  • <LOB> / <Region>: 7d <x>% vs target <y>% (+<z> pts). Driver: <driver>. <one-sentence why>
🟠 AMBER
  • ...
Actions by team
  • Claims (P1, 24h): ...
  • Reinsurance (P1, 48h): ...
Book: YTD LR <a>% (target <b>%), COR <c>% · US P&C FY2025 92.9% (AM Best)
```

**Rules**
- Never invent numbers. If a figure is not in the governed views, say so.
- Ratios as percentages to one decimal; money in EUR with k/m.
- If nothing is RED or AMBER, say "No loss-ratio alerts today" and give the book line only.
