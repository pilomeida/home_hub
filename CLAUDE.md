# CLAUDE.md — Home & Family Hub

Operations, secrets and deploy: `docs/SYSADMIN.md`. Every LLM call goes through the llmsel gateway (`app/llm_gateway.py`) — never a provider SDK, model name or API key in this app.

## Test data must come from the production path (recurring defect class)
A green suite has hidden production bugs when a test used a value typed by hand instead of one produced by the real code (e.g. error text the app never actually stores). Rules:
- Build test inputs with the production objects that make them — e.g. `str(GatewayError(status, detail))`, not a typed string; the shared `tests/fakes/fake_gateway.py`, not an ad-hoc fake.
- Where a value crosses a boundary (stored, logged, shown to the family), assert on what the production path actually wrote.

## Messages rule (Pedro, 2026-10-15)
Every warning or error a person can see says **what** didn't happen, **why**, and **what they can do** — in that order, in plain words ("Not uploaded. This kind of document has no privacy level yet, so nothing may read it. Set its level on the Privacy page, then try again."). Build it with `app/problems.py` (`Problem`, `classify_reason`, `explain`, `record_problem`); raise `HTTPException(detail={"what", "why", "next_step"})` for a route's own message; anything that can fail where nobody is looking (background jobs, Telegram, email) must also `record_problem` so it shows on the Problems page. Never show a bare fact, a bare exception text, or a silent failure. A button that can fail must show its failure (the page already shows a banner for failed HTMX presses).

## Privacy levels: what the AI may read (D16)
Anything that shows the AI documents, records, wiki claims, transactions or to-dos beyond one document being filed (Ask tools, the wiki check, any new reader) must go through `app/privacy/reach.py` (`reach_for(session)`, `settled_entries(..., reach=)`, `wiki_view`) and run inside `reachable_scope(session)`. Never read those tables straight into a prompt. The tripwire tests (`tests/test_privacy_reach*.py`) must cover any new tool.

## docs/PLAN.md rules execution (Pedro, 2026-11-19)
`docs/PLAN.md` is the Hub's only plan and the live document that rules what is done, in what order. Read its "Now"
line before starting any Hub work, and keep it true as you go, not at the end:
- **A new task or request** (from Pedro, a finding, a bug) goes into PLAN.md before work starts: into the step it
  belongs to, as a new step in the order, under "Waiting on Pedro", or under "Parked". Never a separate plan, backlog
  or to-do file.
- **A change to a task** (scope, order, approach, a decision from Pedro) is edited into its entry, dated.
- **Execution, partial or complete:** when a slice starts, set "Now"; when it lands, mark it in its step (✅ date and
  commit, or ◐ with what is left) and move finished steps to "Done". A slice that stops halfway says what is left.
- **Every commit that touches Hub code carries PLAN.md** with it. A hook refuses a commit that does not
  (`~/.claude/hooks/hub-plan-guard.py`); "PLAN-EXEMPT: <reason>" in the message only for work no step covers.
- The dashboard (https://claude.ai/artifact/FAy2A5HYWy4bqk6u5AsRTa) is refreshed from PLAN.md when a step finishes.

## Never
- `git commit`/`git push` without explicit order (see the monorepo root CLAUDE.md).
- A plan, backlog or to-do list anywhere but `docs/PLAN.md`.
