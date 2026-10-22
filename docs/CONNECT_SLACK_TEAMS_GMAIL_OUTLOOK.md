# Connect the coworker to Slack, Microsoft Teams, Gmail and Outlook

This guide covers every way the loss-ratio coworker reaches people: the morning brief, follow-up questions, team
alerts and the per-team email drafts.

Docs: [Genie One connectors](https://docs.databricks.com/aws/en/genie-one/external-sources) ·
[Genie One in Slack](https://docs.databricks.com/aws/en/genie-one/genie-slack) ·
[Genie in Microsoft Teams](https://docs.databricks.com/aws/en/integrations/msft-teams)

## Which path does what

| Channel | Brief delivered by | Follow-up questions | Team alerts | Email drafts |
|---|---|---|---|---|
| **Slack** | Genie One **Slack connector** (send message) | Databricks **Genie app for Slack** (`@Genie`) | Daily watch job → Slack webhook | — |
| **Microsoft Teams** | Daily watch job → Teams webhook (alert card) | Databricks **Genie app for Teams** (`@Databricks Genie`) | Daily watch job → Teams webhook | — |
| **Gmail** | Scheduled-task email (PDF) | — | — | Genie One **Gmail connector** (draft) |
| **Outlook** | Scheduled-task email (PDF) | — | — | Genie One **Microsoft 365 connector** (draft Outlook email) |

Neither email connector can send. Genie One prepares the drafts, and the executive reviews and sends them.

| Connector | What Genie One can do (per docs) |
|---|---|
| Slack | Search Slack messages and channels, and send messages. Cannot edit or delete messages after sending |
| Gmail | Search, read and draft email. Cannot send or modify existing messages |
| Microsoft 365 | Search SharePoint, Teams, Outlook and Calendar; draft Outlook email. Cannot send email |

## Prerequisites

- Genie One enabled in the workspace, in a region where **Unity Gateway** is supported (connectors are only available there).
- The **Loss Ratio Coworker** Genie Agent deployed (`deploy/genie_agent.py`) and the skill created
  (`skills/loss-ratio-daily-brief.md`).
- Every connector call goes through Unity Gateway, which authorizes each tool call against the user's Unity Catalog
  permissions and records usage and audit logs in system tables.

---

## 1 · Slack

### 1a. Genie One Slack connector (posts the brief)

1. **Each user**: in Genie One, click **+** in the chat box (or open **Connectors**), choose **Slack** and sign in
   (OAuth, per user).
2. **Admin**: make sure the connector's **send-message tool** is enabled. In Catalog Explorer open the built-in MCP
   service `system.ai.slack` → **Edit tools**. In some workspaces only the read/search tools are on by default.
3. Create the channel (for example `#exec-loss-ratio`) and confirm the user can post in it.
4. Test in Genie One: *"Post 'Loss ratio coworker test' to #exec-loss-ratio."*
5. Step 6 of the skill then post the brief every morning.

### 1b. Databricks Genie app for Slack (questions in Slack) — Public Preview

1. **Account admin**: enable the Databricks Genie app for Slack in the account console. If the workspace uses IP
   access lists, allow the documented control-plane outbound IPs.
2. **Slack admin**: approve and install the *Databricks Genie* app.
3. **Each user**: DM the app once and sign in to Databricks. Answers run with *their* Unity Catalog permissions.
4. **Channel owner**: invite the app to `#exec-loss-ratio` and configure the channel to **pin the Loss Ratio Coworker
   agent**, so `@Genie` questions there go to that agent.
5. Optional: create the skill and the 07:30 scheduled task from a DM with the app.

Try: `@Genie which lines are RED on loss ratio today?` → `@Genie why did DACH Property jump?`

Limits: no compute selection, no PrivateLink, Genie Agents cannot be created from Slack.

### 1c. Slack webhook (team alerts from the daily job)

See [section 4](#4--daily-watch-job--slack-and-teams-webhooks).

---

## 2 · Microsoft Teams

Genie One's Microsoft 365 connector can **search** Teams, but it does not post to Teams. Use the two paths below.

### 2a. Databricks Genie app for Teams (questions in Teams)

1. **Account admin**: enable the Genie app for Microsoft Teams in the account console.
2. **Teams admin**: approve the *Databricks Genie* app (it also works inside Microsoft 365 Copilot).
3. **Users**: chat in a DM, or mention `@Databricks Genie` in a channel or group chat, and sign in once.
4. **Channel owner**: send `config` in the channel to set the workspace and **pin the Loss Ratio Coworker agent**.

The Teams app is conversational only. Triggering actions in Databricks from Teams is not supported, and skills and
scheduled tasks are not listed for Teams. That is why proactive Teams alerts come from the job webhook.

### 2b. Teams webhook (alert cards from the daily job)

See [section 4](#4--daily-watch-job--slack-and-teams-webhooks).

---

## 3 · Email drafts: Gmail and Outlook

Pick the connector that matches your mail system. Both create **drafts only**.

### 3a. Gmail

1. **Each user**: in Genie One, **+** → **Gmail** → sign in with Google.
2. **Admin**: make sure the **draft** tool is enabled on `system.ai.gmail` (Catalog Explorer → **Edit tools**).
3. Test: *"Create a Gmail draft to me with subject 'Loss ratio coworker test'."* Check your Drafts folder.

### 3b. Outlook (Microsoft 365 connector)

1. **Each user**: in Genie One, **+** → **Microsoft 365** → sign in with the work account. Your Microsoft Entra ID
   tenant may require an administrator to approve the app before users can consent.
2. **Admin**: make sure the **draft Outlook email** tool is enabled on the Microsoft 365 MCP service in Catalog
   Explorer (`system.ai`) → **Edit tools**.
3. Test: *"Draft an Outlook email to me with subject 'Loss ratio coworker test'."* Check Outlook → Drafts.
4. In the skill, use the Outlook variant of step 7 (see `skills/loss-ratio-daily-brief.md`).

The same connector lets Genie One search Teams messages, SharePoint and Calendar, which is useful for questions such as
*"Did Reinsurance reply about the DACH hail notification?"*

### Scheduled-task email

Independently of the connectors, every Genie One **scheduled task** sends its result by email with a PDF, so the brief
also lands in the inbox (Gmail or Outlook) without any connector.

---

## 4 · Daily watch job → Slack and Teams webhooks

The job `src/jobs/daily_loss_ratio_watch.py` detects RED/AMBER segments, asks the Genie Agent *why*, applies the
playbook and posts a **Slack Block Kit** message and a **Teams Adaptive Card** with each team's actions.

1. Create incoming webhooks:
   - **Slack**: a Slack app with *Incoming Webhooks* for each team channel (`#claims`, `#reinsurance`, `#uw-property`, …).
   - **Teams**: in each channel, add a **Workflows** flow "Post to a channel when a webhook request is received" and copy the URL.
2. Store them as secrets:
   ```bash
   databricks secrets create-scope loss_ratio_coworker
   databricks secrets put-secret loss_ratio_coworker slack_webhook_url --string-value "https://hooks.slack.com/services/…"
   databricks secrets put-secret loss_ratio_coworker teams_webhook_url --string-value "https://…"
   ```
3. Run `[LRC] 2 · Daily loss-ratio watch` once with `dry_run=true` (prints both payloads), then set `dry_run=false`
   and unpause the schedule (weekdays 07:00 Europe/London).

Each card carries the alert id. Actions are tracked in `lr_action_recommendations` (state `Open` until the team updates it).

---

## 5 · Checklist

| Check | How |
|---|---|
| Slack post works | Genie One test message appears in `#exec-loss-ratio` |
| `@Genie` answers from the right agent | Ask "which lines are RED today?"; the answer cites Loss Ratio Coworker |
| Teams Q&A works | `@Databricks Genie which lines are RED today?` in the pinned channel |
| Teams alert card arrives | Run the job with `dry_run=false` |
| Gmail / Outlook drafts appear | Run `/loss-ratio-daily-brief`; one draft per team in Drafts |
| Schedule works | Genie One → Scheduled tasks shows the next 07:30 run |

## 6 · Troubleshooting

| Symptom | Fix |
|---|---|
| Genie One writes the Slack post or drafts in the chat but says it cannot deliver them | The connector's write tool is disabled. Ask an admin to enable it (**Edit tools** on the MCP service) |
| A scheduled task suddenly fails | A connection's permissions changed or the login expired. Each user must re-authenticate; chat doesn't warn when authentication expires |
| Genie One answers from the wrong tables | Name the agent in the skill ("Use the Loss Ratio Coworker Genie Agent") or pin it to the Slack/Teams channel |
| Microsoft 365 sign-in is blocked | The Entra ID tenant requires admin approval for the app |
| No connectors visible | The workspace region doesn't support Unity Gateway yet |
