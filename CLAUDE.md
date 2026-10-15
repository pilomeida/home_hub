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

## Never
- `git commit`/`git push` without explicit order (see the monorepo root CLAUDE.md).
