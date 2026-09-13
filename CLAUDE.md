# CLAUDE.md — Personal

A monorepo-style container for several personal projects, each with its own purpose and (where it has one) its own `claude.md`/`CLAUDE.md`:

- `Recipes/` — recipe app.
- `Home & Family/` — home/family hub (finances, docs).
- `Exercise App/`, `Comics project/` — smaller personal builds.
- `WhatsApp To-Dos/` — stale; not actively maintained. Check with Pedro before building on it.

There is no shared deploy pipeline or shared code at this root — each subproject is independent. When working in one, prefer that subproject's own `claude.md` for specifics; this file is only a directory.

## Never
- `git commit`/`git push` without explicit order — this repo holds several live personal projects; a push affects whichever subproject's deploy hook is configured.
