# Build brief — executing the House / Inbox / Ask plans

You are building three features for the Home Hub app by following written plans **exactly**. These plans are the source of truth and already contain the design decisions, code, files and tests. Your job is to execute them faithfully. Do not redesign anything.

## Where things are

- Repo root (git): `/home/pedro/Desktop/Claude_Corner/Personal/`. The app is in the subfolder `Home & Family/`, and all app work happens inside it.
- The plans, executed strictly in this order:
  1. `Home & Family/docs/superpowers/plans/2026-09-24-house-tab.md`: Tasks 1–25
  2. `Home & Family/docs/superpowers/plans/2026-09-24-inbox-and-ingestion-channels.md`: Tasks 1–9. Task 10 is not yours; see below.
  3. `Home & Family/docs/superpowers/plans/2026-09-24-ask.md`: Tasks 1–15. Task 16 is not yours; see below.
- Read `Home & Family/docs/ARCHITECTURE.md` before starting.

## Git rules

- Work on your own branch, one per plan: `feat/house-tab`, then `feat/inbox-channels` (branched from `feat/house-tab`), then `feat/ask` (branched from `feat/inbox-channels`). Create the first branch from `master`.
- The repo has **unrelated uncommitted changes** in other folders (Recipes, WhatsApp To-Dos, Comics project). Never touch, stage, stash, reset or commit them. Stage only paths inside `Home & Family/`, listing the files explicitly. Never run `git add -A`, `git add .`, `git stash`, `git reset --hard` or `git checkout -- .`.
- Make one local commit per completed task, with the message `<plan>: Task N — <task title>`.
- **Never `git push`, never `git subtree push`, never touch any remote.** The `origin` remote points to an unrelated repository. Pushing is what deploys to the live site, and only Pedro does that.

## How to work each task

1. Read the whole task before starting it.
2. Follow its steps in order: write the failing test, watch it fail, implement, then watch it pass. Use the exact test commands the plan gives.
3. Before committing, run the **full** test suite from `Home & Family/` with `python -m pytest -q`. Everything must pass. Never skip, delete, loosen or `xfail` a test to make it pass.
4. Commit (see Git rules), then append an entry to the build log (below).

**If a step doesn't match reality** (a file, function or line the plan refers to isn't as described, a test can't pass as written, or the plan seems wrong), **STOP**. Do not improvise, "simplify", or pick an easier path. Write what you found in the build log under the task, marked `BLOCKED`, and wait for instructions.

Small mechanical fixes are fine if you log them as `DEVIATION` with one line of explanation. Examples: a typo in the plan's code, an import the plan forgot, a line-number shift.

## Never do

- Never connect to the VPS (no SSH or rsync), and never run deploy scripts, `systemctl`, `sudo`, `loginctl` or `install_user_units.sh`.
- Never touch `Home & Family/data/`. That is real family data. Tests use their own temporary databases. If a task says to try a migration on a database copy, copy the database into a temporary directory first and never run migrations against `data/` itself.
- Never create real accounts, bots, mailboxes or API keys, and never put secrets in any file. Tasks for the email and Telegram channels are built and tested with the fakes the plan provides.
- Never make real calls to the Anthropic API. Tests use the fakes in the plan.
- If a task includes a deploy, production, operator or "Pedro does this" step, skip that step and note `SKIPPED (operator step)` in the log.

## Checkpoints — STOP and wait for review

When you reach a checkpoint, finish and commit the task, write `CHECKPOINT` in the log, and stop. Don't continue until you're told to.

| After | Why |
|---|---|
| House Task 3 | Trial run. Review of faithfulness to the plan before the rest of the build. |
| House Task 5 | The existing Financials pipeline has been moved onto the new core. |
| House Task 9 | The existing wiki data is converted to the new claims structure (migration on real-shaped data). |
| House Task 19 | Migration chain + wiki cross-links finished. |
| House Task 24 | Financials re-filing rule (deletes derived transactions), which is the most data-sensitive task. |
| House Task 25 | End of the House plan: full review before Inbox starts. |
| Inbox Task 5 | The Inbox approval flow is wired to the ingestion core. |
| Inbox Task 9 | End of the Inbox plan. **Do not do Task 10** (go-live is done by Pedro with Claude). |
| Ask Task 7 | The Ask engine is finished. |
| Ask Task 15 | End of the Ask plan. **Do not do Task 16** (real-API check, done with Claude). |

## Build log

Keep `Home & Family/docs/superpowers/plans/2026-09-25-build-log.md`. Append one entry per task:

```
## <plan> Task N — <title>
- Commit: <short hash>
- Tests: <passed count> passed, 0 failed (full suite)
- Deviations: none | DEVIATION: <one line each>
- Status: DONE | BLOCKED: <what and why> | CHECKPOINT
```

Commit the build log along with each task's commit.
