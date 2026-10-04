# CLAUDE.md — Home & Family Hub

Operations, secrets and deploy: `docs/SYSADMIN.md`. Every LLM call goes through the llmsel gateway (`app/llm_gateway.py`) — never a provider SDK, model name or API key in this app.

## Test data must come from the production path (recurring defect class)
A green suite has hidden production bugs when a test used a value typed by hand instead of one produced by the real code (e.g. error text the app never actually stores). Rules:
- Build test inputs with the production objects that make them — e.g. `str(GatewayError(status, detail))`, not a typed string; the shared `tests/fakes/fake_gateway.py`, not an ad-hoc fake.
- Where a value crosses a boundary (stored, logged, shown to the family), assert on what the production path actually wrote.

## Never
- `git commit`/`git push` without explicit order (see the monorepo root CLAUDE.md).
