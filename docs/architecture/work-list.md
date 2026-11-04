# Home Hub work list (from findings.md, 2026-10-28)

**Status: proposed, not approved. Nothing here is built yet.**

The order puts safety first, then the foundation, then the functions. Each item names its finding, what done looks like, and whose decision it is. It replaces the 7-step order proposed earlier today. That order started with renaming, before anything that protects data.

Size: S is under half a day, M is one to two days, L is three days or more.

## A. Safety (do first; small, and each one protects data)
| # | What | Finding | Done when | Size |
|---|---|---|---|---|
| A1 | Back up the Hub database and uploaded files every night, off the server, keeping 30 days. Take a snapshot before every migration in the deploy. | F16 | A restore from last night is rehearsed once; the deploy log shows a snapshot before `alembic upgrade` | S |
| A2 | Add a test gate to the deploy: the full suite must pass before the server is touched. Pin dependencies with a lock file. Stop shipping pytest to production. | F17 | A failing test blocks a push to main; `requirements.lock` is in use | S |
| A3 | Make the privacy gate fail closed. A call with no tier and no scope is refused, not sent. Then put bank-sync classification, the Loans upload and re-read, field-edit and record wiki updates, the Unsorted reclassify and the five scripts inside the right scope. | F7 | A tripwire test: any gateway call without a tier raises; the existing tests pass | M |
| A4 | Fix the error box. A failed press shows the three-part banner instead of replacing the list. | F18 | Needs Review and the budget panel keep their content on a failed press (tested) | S |
| A5 | Fix the prompt bugs: the stale reminder problem, the double problem for one failure, the bank runner drift, and the bill to-do duplicate guard. | F15 | One test per bug | S |

## B. Foundation
**Order (2026-11-03):** B1a glossary (done: top of docs/ARCHITECTURE.md) → B6 every document a kind → B3 one
spending taxonomy (it frees the word "category" in the code) → B1b rename in code and fix Insurance's kinds → B2 each
kind carries its four attributes → B5 bank lines explicit → B3b commitments for cash flow → B4 registry of subjects
(largest; last, because it builds on the renamed kinds). Renames happen in code only; database columns keep their names (additive-only migrations, docs/ARCHITECTURE.md).
| # | What | Finding | Done when | Size |
|---|---|---|---|---|
| B1 | **Glossary and names.** Decide the words: category, paper kind, spending tree. Rename in the code step by step, behind aliases: `Domain` becomes category, the code's `category` becomes kind. Insurance stays a category (Pedro, 2026-11-03); fix its double inversion so its kinds are papers (policy, renewal notice, claim), and what is covered becomes a field. **Insurance fixed 2026-11-04** (B1b, first slice): kinds Policy and Claim (Pedro); `covers` (home, car, health, life) and `part` are details (migration `d2b6f8a1c437`); a premium notice is a bill (Pedro: "first and foremost bills"), filed once under Bills with its policy number and listed on the policy's card, never counted twice (`scripts/move_premium_notices_to_bills.py` moves the six on file). **Next slices:** rename in code in steps that fail loudly: `CategorySpec` → kind, then `DomainSpec`/`Domain` → category; attribute renames last, `.category` → `.kind` deployed before `.domain` → `.category`, so a missed rename errors instead of comparing the wrong thing. | F6 | The glossary is in ARCHITECTURE.md; the code, forms and docs use one word for each idea | M |
| B2 | **The kind carries its four attributes.** Tier default, reader, checks and filing target are declared on the kind's spec. Handlers dispatch from the spec instead of `if` branches. | F5 | Adding a kind is a spec change only (shown with one existing kind) | L |
| B3 | **One spending taxonomy.** Move the Overview, Ask spending and commitments onto the tree, then drop the old `Transaction.category` enum. Built 2026-11-03: one counting rule (`budget_service.spend_of`) for the budget, the Overview's "Where it went" (now by group of the tree) and Ask; commitments carry a tree node (migration `c9e2f4a6b815`); the bill reader finds a utility from the tree leaf; debt candidates go by the tree; the enum, its mappings and the hint mapper are gone and nothing writes the old columns. | F8 | No reader of `Transaction.category` remains; the Overview and the budget give the same totals | M |
| B3b | **Budgets and commitments both stay, with distinct jobs** (Pedro, 2026-11-03). A budget is a planned maximum, a baseline to monitor against. A commitment is a known spend that is coming, used to anticipate cash flow. Put commitments on the spending tree; show them in a cash-flow view of what is due and when; let a commitment be created by hand as well as from Needs Review. | F20 | A commitment shows on the cash-flow view with its due date, separately from the budget | M |
| B4 | **Registry of subjects.** One `subjects` store (person, account, counterparty, thing, policy, condition) with aliases and merge, built from Health's `people.py`. Link `people`, `health_people`, accounts (with owner), and the merchants that are people. Wiki entity keys point at subjects. One name normaliser. | F1, F9 | Matias is one subject seen from the loan, the merchant and the bank lines; Rute is one subject across Health and Money | L |
| B5 | **Bank lines inherit the statement's level** (Pedro, 2026-11-03: no level of their own). Make the synthetic feed document explicit (named, documented, tested) instead of a workaround. The AI calls are already scoped at the statement level (A3). | F7, F10 | Every row the AI can see has a tier, through its statement | S |
| B6 | **Give every document a kind.** Built 2026-11-03: a paper is filed only with a kind (`services/ingestion.py`, otherwise it goes to a person); `scripts/give_every_document_a_kind.py` gives the 5 Money documents theirs and moves the Revolut history onto the Revolut account; `doc_type` is out of the code (the column stays: additive-only migrations). Inbox papers (68) get their kind when approved; the dental receipt moves with `scripts/file_dental_receipt.py` (other session). | F6, F20 | No filed document without a kind; no dead kind | S |
| B7 | **One copy of each paper, and no voids** (Pedro, 2026-11-04). Each file is already stored once (by content) and most uses link to it by id. Missing: one shared "where is this paper used" check over every link (records, bill and bank lines, health entries, positions, to-dos, wiki sources, the Inbox, a premium notice's policy) that every delete or discard runs first. Today Health's "delete" and the Inbox "discard" take a paper out of use without it, and each area takes back its own data its own way. | F10 | Deleting a paper still used elsewhere is refused with the list of places, each a link (Pedro, 2026-11-04: refuse, not warn); it can be deleted once nothing uses it | M |

## C. Functions
| # | What | Finding | Done when | Size |
|---|---|---|---|---|
| C1 | **One review pattern.** A shared review item (subject, why, evidence and page, options, remember-the-answer choice, who and when) modelled on To-check. Move the 23 review points onto it one by one, starting with Needs Review and the Health queues. One counter in the menu. | F3, F14 | One "Waiting for you" count; every decision stamps who and when | L |
| C2 | **One memory of past answers.** One rules table that every classifier and batch re-file reads in the same order. A person's choice always wins, and batch passes respect `checked_by`. Move the code-held lists (entities, keywords) into data. | F13 | Confirming a merchant or approving in the Inbox teaches the same memory; no batch pass overrides a person | L |
| C3 | **Kind decided once.** Remove the second, hidden bill-or-statement AI decision after approval; the Inbox suggestion gives the full kind. Hand uploads go through the same recognition. | F13 | A paper's kind is chosen once and seen by a person | S |
| C4 | **Shared reading and checking blocks.** One reader interface (text extraction, Portuguese numbers, size guard, error type) and one check-result shape (verdict, doubtful items to review). Lift Health's read-twice and benchmark into the shared block. | F12 | Statement, positions, card and Health checks all return the same shape into C1 | L |
| C5 | **Close the side doors.** The Loans upload and card statements go through the ingestion core (scope, duplicates, Problems). Strip the PDF wrapper on every channel. Compare an uploaded statement with existing feed lines. | F11 | Rute's card statement can be sent by Telegram; a statement for a feed period is caught as overlap | M |
| C6 | **One notification mechanism.** A notices store with one duplicate rule, one channel router (Telegram), and a per-kind push policy. The first four kinds that reach the phone (Pedro, 2026-11-03): **bank access expired; a scheduled job keeps failing; a bill due within 3 days; a new loan red flag.** Each is sent once when it appears, at most one reminder while it stays open, and never repeated per run. Everything else stays on its page. | F15 | The two copied Telegram senders are gone; expired bank access reaches the phone once, not 28 silent times | M |
| C7 | **Answers agree.** Unify the Overview, Ask and budget figures after B3. Make the wiki namespace category-aware; fix the orphan check while links are rare. | F8, F10 | The same spending question gives the same number everywhere | S |

## D. Clean-up
| # | What | Finding | Size |
|---|---|---|---|
| D1 | Retire dead tables and columns (`wiki_changes`, `doc_type`, `health_entries.amount_*`, legacy Health kinds). `commitments` stays (B3b) | F20 | S |
| D2 | Move router logic into services (transactions, loans, health); stop routers importing routers; take spending-tree slugs out of services | F19 | M |
| D3 | Visible failures: no swallowed or logged-only failure in the intake paths; no raw exception or bank error on screen; messages built with `Problem`, not matched word for word | F18 | M |
| D4 | One money filter and one date filter in templates; one three-part message macro | F19 | S |
| D5 | Run jobs so the mailbox check is never stuck behind bank sync or the wiki check | F17 | S |
| D6 | Bring ARCHITECTURE.md, SYSADMIN.md and `.env.example` up to date | F22 | S |

## Backlog (parked, not scheduled)
- **Matias's loan: reconcile where we are** (Pedro, 2026-11-03). The Hub shows it at 0 and still active. Park until Pedro picks it up.
- **Emanuel's €15,000 from before 2025-10-10.** It is already counted as an opening balance of 15,000 on 2025-10-09, which is why the balance shows 17,000. Pedro wants it brought in properly: when it was received, how, and whether any of it was paid back before the bank data starts. Needs the date and the amount.

- **Missing bank history** (found by B6, 2026-11-03): the Santander statements of 2024-03 (no lines at all) and 2025-02 (1 line) failed to read on 2026-08-18 with bugs fixed since, and Revolut before 2024-01 was never loaded. Read them again through the normal path, with a check against lines already stored so nothing doubles. Before the 2025-10-10 baseline, so history and spending analysis only.

## Pedro's to-dos from this mapping
1. Renew Rute's Santander access on the Bank connections page; it has been expired since before today.
2. Emanuel's €15,000: the date it was received, and anything paid back before 2025-10-10 (see Backlog).
3. (Done 2026-11-03: keep both budgets and commitments; the four phone notices agreed.)
