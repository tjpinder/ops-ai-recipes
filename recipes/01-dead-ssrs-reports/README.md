# 01 · Find the SSRS reports nobody runs

**Problem.** An SSRS server collects reports for years. Copies, "v1"s, archive folders, reports for a manager who left. Before a migration, or just to cut noise, you need a list of what nobody uses. The obvious query ("no rows in the execution log") gets it wrong in four ways, and every one of them leads to retiring something that's still in use.

**What this does.** One read-only T-SQL script against the ReportServer database. It sorts every report into a status and refuses to answer when the data can't support one.

| Status | Means |
| --- | --- |
| `retire candidate` | No runs in the window, no active subscription, not a subreport, not a linked report, older than the window |
| `review` | No runs in the window, but an active subscription is still sending it (or failing to) |
| `not logged (subreport)` | SSRS logs the parent, not the subreport. No log rows proves nothing |
| `not logged (linked)` | SSRS doesn't log linked reports directly |
| `too new to judge` | Created inside the window |
| `active` | Ran at least once in the window, interactive or by subscription |

No model in this one, on purpose. Deciding what's dead is a deterministic job, and it should stay one.

The why, and the cutover that taught it: [How to find the SSRS reports nobody uses, without retiring the ones people still need](https://startvest.ai/blog/find-ssrs-reports-nobody-uses).

## The four ways the obvious query is wrong

1. **The log doesn't go back as far as you think.** SSRS keeps execution history for `ExecutionLogDaysKept` days (60 by default), then deletes it. Ask "unused for 90 days" on a 60-day log and every quarterly report looks dead. This script reads the setting and the oldest log row, and **stops with an error** if the log doesn't cover the whole window.
2. **Subreports never appear in the log.** The parent report's execution covers them. The script reads every report definition, finds the subreports it points at, and marks them `not logged`, never `retire candidate`.
3. **Linked reports aren't logged directly** either. Same treatment.
4. **"Nobody opened it" isn't "nobody gets it."** A report emailed every Monday by subscription has an audience. Subscription runs count as runs. A report with an active subscription and no runs at all goes to `review`: something is scheduled and not delivering.

## Inputs

- The `ReportServer` database (SSRS 2008 R2 or later, which has the `ExecutionLog3` view)
- A login with `db_datareader` on it. Nothing more.

## Guardrails

- **Read-only.** Only `SELECT`s. Run it with a read-only login anyway.
- **No sensitive columns.** It never reads data source credentials, report parameters, subscription recipients, or any business data. It reads report definitions only to find subreport references.
- **Refuses instead of guessing** when the log is shorter than the window.
- **Conservative on purpose.** Subreport matching errs toward protecting a report. When in doubt, it's not a retire candidate.
- **It's a candidate list, not a delete list.** Check each candidate with its owner before you retire anything.

## Run it

In SSMS, open `find_dead_reports.sql`, connect to the ReportServer database, and run it. Or:

```sh
sqlcmd -S your-ssrs-db-server -d ReportServer -E -i find_dead_reports.sql -s "," -W -o report-status.csv
```

Defaults: the window is `ExecutionLogDaysKept` days ending now. To use a shorter window, edit `@WindowDays` at the top.

## Tested on

A synthetic estate: the fictional distributor from [sql-xray](https://github.com/tjpinder/sql-xray)'s demo, with 63 reports, a 60-day log, planted copies, a subreport, a linked report, and a subscription with no runs. `tests/run_test.py` loads it into a throwaway database on a local SQL Server, runs the script, and checks every status against the fixture's answer key:

```text
Guardrail: 90-day window over a 60-day log was refused
Statuses: active 56, not logged (linked) 1, not logged (subreport) 1, retire candidate 4, review 1
PASS: 63 of 63 reports match the answer key
```

Not yet run on a production ReportServer. If you run it on yours and a status looks wrong, open an issue.

## Want the full picture?

This script answers one question: what's unused. [sql-xray](https://github.com/tjpinder/sql-xray) does the whole estate: duplicates, reports that compute the same metric differently, what breaks if something is removed, and a keep, consolidate, retire, or review list where every finding cites its source.
