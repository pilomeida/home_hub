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
| B1 | **Glossary and names.** Decide the words: category, paper kind, spending tree. Rename in the code step by step, behind aliases: `Domain` becomes category, the code's `category` becomes kind. Insurance stays a category (Pedro, 2026-11-03); fix its double inversion so its kinds are papers (policy, renewal notice, claim), and what is covered becomes a field. **Insurance fixed 2026-11-04** (B1b, first slice): kinds Policy and Claim (Pedro); `covers` (home, car, health, life) and `part` are details (migration `d2b6f8a1c437`); a premium notice is a bill (Pedro: "first and foremost bills"), filed once under Bills with its policy number and listed on the policy's card, never counted twice (`scripts/move_premium_notices_to_bills.py` moves the six on file). **Renames in code, in steps that fail loudly:** slice 1, 2026-11-04: `CategorySpec` → `KindSpec` (and `.kinds`, `.kind_label`). Slice 2, 2026-11-04: the attribute `.category` → `.paper_kind` (not `.kind`, which already means document-or-record and a recognition rule's sender-or-words); columns and form fields keep their names; the models refuse an unknown keyword. Slice 3, 2026-11-04: the kind enums and modules (`FinancialsKind`…, `kinds.py`); `Domain` → `Category`, `DomainSpec` → `CategorySpec`, `app/domains` → `app/categories`; then `.domain` → `.category` (columns keep `domain`; the models carrying it refuse an unknown keyword). Pages and messages now say "Category" and "Kind of paper" (the edit form said "Domain" and "Category"; the Inbox "Area" and "Type"). Then the parameters: 56 functions' `category` (a kind) → `paper_kind`, deployed before 50 functions' `domain` → `category`, so a missed call fails instead of passing a kind as a category (2026-11-04). Then the names that said *domain* (functions, modules, Inbox suggestion fields, page helpers, local variables), each pair swapped in two deploys the same way. **Done 2026-11-04.** Kept on purpose: database columns, HTML form field names and the AI readers' JSON keys (glossary). | F6 | The glossary is in ARCHITECTURE.md; the code, forms and docs use one word for each idea | M |
| B2 | **The kind carries its four attributes.** Tier default, reader, checks and filing target are declared on the kind's spec. Handlers dispatch from the spec instead of `if` branches. **Slice 1 built 2026-11-04, the level:** each kind declares its suggested level (`KindSpec.suggested_tier`, never High) and a category may force one (`CategorySpec.forced_tier`: Health is High); the Privacy page, the checks and Health's reader read those declarations, so the hard-coded list and the reader's own copy are gone. **Built 2026-11-04, the rest:** each kind names its reader (`KindSpec.reader`; Money's bill, statement, positions and loan-history readers, House's warranty reader) and the handlers dispatch from the spec (`read_by_kind`), with no branch on the kind; each kind names where its reading writes (`KindSpec.files_as`), and a delete takes back exactly that (B7's check). The check comes with the reader (the statement reader includes the add-up) until C4 gives the checks one shape and lifts them out. Shown by a test: a card statement declared on Money's spec alone is read into bank lines with the add-up, offered its level, and known to a delete. | F5 | Adding a kind is a spec change only (shown with one existing kind) | L |
| B3 | **One spending taxonomy.** Move the Overview, Ask spending and commitments onto the tree, then drop the old `Transaction.category` enum. Built 2026-11-03: one counting rule (`budget_service.spend_of`) for the budget, the Overview's "Where it went" (now by group of the tree) and Ask; commitments carry a tree node (migration `c9e2f4a6b815`); the bill reader finds a utility from the tree leaf; debt candidates go by the tree; the enum, its mappings and the hint mapper are gone and nothing writes the old columns. | F8 | No reader of `Transaction.category` remains; the Overview and the budget give the same totals | M |
| B3b | **Budgets and commitments both stay, with distinct jobs** (Pedro, 2026-11-03). A budget is a planned maximum, a baseline to monitor against. A commitment is a known spend that is coming, used to anticipate cash flow. Put commitments on the spending tree; show them in a cash-flow view of what is due and when; let a commitment be created by hand as well as from Needs Review. **Built 2026-11-05** (Pedro: its own Money page, commitments and unpaid bills, three months, a form and "this repeats"): `/financials/coming-up` (`services/coming_up.py`) lists the next three months by month, with overdue bills on top; a bill on the same tree leaf within 20 days of a commitment's date is that payment, shown once; commitments are added on the page or from "↻ repeats" on a bank line, and a commitment still linked to lines or a loan is not removed. The Overview shows the next 30 days in one line. | F20 | A commitment shows on the cash-flow view with its due date, separately from the budget | M |
| B4 | **Registry of subjects.** One `subjects` store (person, account, counterparty, thing, policy, condition) with aliases and merge, built from Health's `people.py`. Link `people`, `health_people`, accounts (with owner), and the merchants that are people. Wiki entity keys point at subjects. One name normaliser. **Slice 1 built 2026-11-05** (Pedro: the Hub proposes, a person confirms; no person page for now): `subjects` and `subject_matches` (`services/subjects.py`, built on Health's people); Loans' people, Health's people, bank owners, accounts (by owner) and payees that are people point at their subject; a new name joins only on the exact same key, anything looser is asked on the Inbox ("Same person?"), a yes joins for good (the fuller name kept), a no is never asked again; one name key (`services/names.py`) for people, Health and Ask. `scripts/build_subject_registry.py` registers what exists. **Next:** wiki entity keys point at subjects (the wiki keeps its own key until then, so its stored keys do not change). | F1, F9 | Matias is one subject seen from the loan, the merchant and the bank lines; Rute is one subject across Health and Money | L |
| B5 | **Bank lines inherit the statement's level** (Pedro, 2026-11-03: no level of their own). Make the synthetic feed document explicit (named, documented, tested) instead of a workaround. The AI calls are already scoped at the statement level (A3). **Built 2026-11-04:** the feed statement is named and documented (`categories/financials/feed.py`: one per account, a statement-kind paper with no file); bank sync and reconcile use it instead of a hidden key; it shows no broken file link (To-check says the line came from the bank feed) and is never re-filed (refused with the reason, which now comes before any check of the new place). Tests: every line takes its paper's level, and no code makes a line without its paper. On live data, 2026-11-04: 5,477 statement lines and 56 bill lines, every one on a paper of a known kind. | F7, F10 | Every row the AI can see has a tier, through its statement | S |
| B6 | **Give every document a kind.** Built 2026-11-03: a paper is filed only with a kind (`services/ingestion.py`, otherwise it goes to a person); `scripts/give_every_document_a_kind.py` gives the 5 Money documents theirs and moves the Revolut history onto the Revolut account; `doc_type` is out of the code (the column stays: additive-only migrations). Inbox papers (68) get their kind when approved; the dental receipt moves with `scripts/file_dental_receipt.py` (other session). | F6, F20 | No filed document without a kind; no dead kind | S |
| B7 | **One copy of each paper, and no voids** (Pedro, 2026-11-04). Built 2026-11-04: `services/paper_uses.py` lists every place a paper is used, and Health's delete and the Inbox discard refuse with that list, each place a link (a Health delete still takes back its own record and wiki facts). A test fails when a new table links to papers without the check reading it. On live data, 2026-11-04: no Health paper would be refused. Each file is already stored once (by content) and most uses link to it by id. Missing: one shared "where is this paper used" check over every link (records, bill and bank lines, health entries, positions, to-dos, wiki sources, the Inbox, a premium notice's policy) that every delete or discard runs first. Today Health's "delete" and the Inbox "discard" take a paper out of use without it, and each area takes back its own data its own way. | F10 | Deleting a paper still used elsewhere is refused with the list of places, each a link (Pedro, 2026-11-04: refuse, not warn); it can be deleted once nothing uses it | M |

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
| D1 | Retire dead tables and columns (`wiki_changes`, `doc_type`, `health_entries.amount_*`, legacy Health kinds). `commitments` stays (B3b). Also the AI readers' own spending words (found 2026-11-04): the statement reader asks the model for a `category_hint` per line that nothing reads (paid-for output), and the bill reader's hint picks the utility type of a bill not yet filed, from its own 15-word list; let the tree decide, as B3 does once filed | F20 | S |
| D2 | Move router logic into services (transactions, loans, health); stop routers importing routers; take spending-tree slugs out of services | F19 | M |
| D3 | Visible failures: no swallowed or logged-only failure in the intake paths; no raw exception or bank error on screen; messages built with `Problem`, not matched word for word | F18 | M |
| D4 | One money filter and one date filter in templates; one three-part message macro | F19 | S |
| D5 | Run jobs so the mailbox check is never stuck behind bank sync or the wiki check | F17 | S |
| D6 | Bring ARCHITECTURE.md, SYSADMIN.md and `.env.example` up to date | F22 | S |

## E. Knowledge layers (Pedro, 2026-11-17)
Three layers, each with one job: **tables** for numbers and dates (the truth), the **wiki** for standing facts (each
with its sources and its subject), **search over papers** for the long tail. Anything can be uploaded from day one;
a kind of paper gets its own reader only once the same exact question keeps being asked about it.
| # | What | Done when | Size |
|---|---|---|---|
| E1 | **Facts say when they were true** (Graphiti's two clocks: when it was true, apart from when the Hub learned it). **Built 2026-11-17:** `wiki_claims.valid_from/valid_to/was_correction` (migration `a3c7e1f9b250`); each kind declares how its papers are dated (`KindSpec.dated_by`, else `CategorySpec.dated_by`: Health by the paper's date, a warranty by the purchase date, maintenance by the service date, a bill by its issue date); a newer paper closes the old fact where the new one begins; **an older paper arriving late adds history and no longer overwrites the present**; a correction from the same paper is marked never true; the AI reader may give a fact's own dates (a tariff "from 2025-01-01"). The wiki page and Ask show "true since / true from–to". `scripts/date_wiki_facts.py` dates the facts on file. **Fixed the same day:** a fact stays current until a paper newer than its *latest* confirmation says otherwise, and an undated paper never replaces a dated fact; `resettle_facts` replays that rule over the facts on file (it fixed 8 Telecom and MetLife facts an older or undated paper had overwritten). | A fact on a wiki page shows since when it holds; a backfilled old paper leaves the current fact current (tested) | M |
| E2 | **"Anything else" paper kind and search over papers.** A catch-all kind with no dedicated reader: stored, given a level, turned into text (the PDF's own text first, the AI reading the page only when there is none), titled, dated and summarised, then found by a search over every paper's text (SQLite FTS5, no new service) from a search box and from Ask, with citations, inside the privacy reach. | A paper of an unknown kind is uploaded, approved and found by a word inside it, from the page and from Ask; a High paper never reaches a lower call | L |
| E3 | **Wiki facts point at subjects** (B4's next step) | Rute's facts are found from her subject, whichever page holds them | M |

### What we can reuse from open source (survey 2026-11-17)
Only MIT/Apache/BSD code may be adapted (with attribution); GPL/AGPL projects give ideas only.
| For | Take | From (licence) |
|---|---|---|
| E1 | The rules for closing a fact by date (older closes at newer's start; never delete); a deadline still to come is not an end | getzep/graphiti `utils/maintenance/edge_operations.py`, `temporal_edge_utils.py` (Apache-2.0): used in E1 |
| E2 | "Use the PDF's own text, read the page only when there is none"; prompts for title, date and kind | paperless-ngx consumer (GPL: idea only); icereed/paperless-gpt `default_prompts/` (MIT: adapt) |
| C1, C2 | A rules table: field, match kind (contains/exact/regex/IBAN), pattern, priority, **source (person > AI > system)**, confidence, approved by/when; an AI rule below the confidence bar waits, switched off, for review; batches with an index per item | maciejb2k/open-banking-rails `app/models/merchant_rule.rb`, `services/llm/merchant_suggester.rb` (MIT: adapt); paperless-ngx `matching.py` fuzzy ≥ 90 (idea) |
| C4 | Lab values: the reader returns the paper's own wording; code matches it to a known test (exact alias, then fuzzy, else a new one flagged for a person), never a long list in the prompt; field set value/unit/range/abnormal | giovi321/asclepius `normalization/resolver.py`, `prompts_data/extraction_lab_test.yaml` (MIT, very young: adapt with care) |
| C5 | Statement lines vs feed lines: match by the bank's id first, then by amount and nearest date; on a tie prefer the line with no bank id; never overwrite a reconciled line | actualbudget/actual `server/accounts/sync.ts` `reconcileTransactions` (MIT: port the idea) |
| C6 | Nothing to copy; a `notices(dedup_key unique, sent_at, reminded_at)` table; Apprise (BSD) only if a second channel comes | caronc/apprise |
| New categories | A generic "thing" with typed fields and attachments; Identity & Legal is "a paper with an expiry and a reminder" | sysadminsmedia/homebox (AGPL: idea only) |

### Build plan, in order (2026-11-17)
The order follows what each step needs from the one before: text first (search and the readers build on it), then
the memory of answers (the review queue shows its waiting rules), then the money accuracy work, then Health, phone
notices, subjects in the wiki, and new categories last (they need search, notices and the review queue).
Each step is a green slice: tests, commit, deploy, then any one-off script on the server.

**1. E2: "Anything else" and search over papers** (L). Reuse: paperless-gpt's prompts (MIT), the paperless-ngx "own text first" rule (idea).
- a. A text layer for every paper: a `paper_texts` table (paper, text, how it was got: `pdf_text` or `ai_read`), filled at filing. pypdf text first; the AI reads the page only when there is no text, at the paper's own level. A script fills it for the papers on file.
- b. Search: an SQLite FTS5 index over that text plus title and fields; `search_papers` replaces the word-match in Ask's `find_sources`, inside `reachable_scope` (tripwire test).
- c. The catch-all kind "Other paper" (its own category, suggested Mid): its reader writes a title, date, summary and who or what it is about (paperless-gpt's title/date prompts, rewritten for the gateway). The Inbox offers it when nothing else fits.
- d. A search box on the menu: results with the paper, its level and the line that matched.
- Done when: an unknown paper is uploaded, approved, and found by a word inside it, from the page and from Ask; a High paper never reaches a lower call.

**2. C2: One memory of past answers** (L). Reuse: open-banking-rails `merchant_rule.rb` and `merchant_suggester.rb` (MIT).
- a. A `rules` table: what it looks at (description, counterparty, IBAN, Multibanco entity, sender, words), how (contains, exact, regex, prefix, IBAN), the pattern, the answer (merchant, spending leaf, kind of paper, subject), priority, **source (person, AI, system)**, confidence, switched on or off, approved by and when.
- b. One reader of it, in one order: person, then AI, then system; then priority. Every classifier and batch re-file asks it first. Move today's provider rules, payment entities and code-held keyword lists into it (a migration, nothing lost).
- c. An AI suggestion below the confidence bar is saved switched off and waits for a person (feeds step 3). AI calls go in batches with an index per item.
- Done when: confirming a merchant or approving in the Inbox writes one rule, and no batch pass overrides a person (tested).

**3. C1: One review queue** (L). Reuse: open-banking-rails' "switched-off rule waits for review"; SureHub's status words (idea only, no licence).
- a. A `review_items` table: subject, why, evidence and its page, the options, "remember this answer" (writes a step-2 rule), status (waiting, approved, discarded, snoozed), who and when.
- b. Move the review points onto it one by one, starting with Needs Review, then the Health queues, Inbox "Same person?" and step 2's waiting rules.
- c. One "Waiting for you" count in the menu, replacing the separate counters.
- Done when: one count, and every decision stamps who and when.

**4. C5: Statements against the bank feed** (M). Reuse: Actual Budget `reconcileTransactions` (MIT, the idea ported to Python).
- a. Match an uploaded statement's line to a feed line: by the bank's own id first, then same amount and nearest date (within a few days); on a tie, prefer the line without a bank id; never change a line a person has checked.
- b. Overlapping periods are caught and shown as overlap, not doubled. The Loans upload and card statements go through the same intake (scope, duplicates, Problems).
- c. Then re-read the missing history (Santander 2024-03 and 2025-02; Revolut before 2024-01; see Backlog) through it.
- Done when: a statement for a feed period adds no double lines (tested on a real overlap), and Rute's card statement can be sent by Telegram.

**5. C4: Shared reading blocks, Health first** (L). Reuse: asclepius `normalization/resolver.py` and `extraction_lab_test.yaml` (MIT, young project: adapt and test well).
- a. One reader interface: text from step 1, Portuguese numbers and dates (`1.234,56`, `dd/mm/aaaa`; our own, nothing to copy), a size guard, one error type.
- b. Lab values: the reader returns the paper's own wording, value, unit and range. Code matches the wording to a known test (exact alias, then near match, else a new test marked for a person in the step-3 queue), so no long list goes in the prompt.
- c. One check-result shape (verdict, doubtful items) for the statement add-up, positions, card and Health checks, flowing into step 3.
- Done when: the same blood test from two labs lands on one line of Rute's results, and every check returns the same shape.

**6. C6: Phone notices** (M). Nothing to copy; Apprise only if a second channel comes.
- a. A `notices` table (dedup key unique, first sent, reminded, closed); one sender for Telegram; a push rule per kind.
- b. The four agreed notices: bank access expired, a job keeps failing, a bill due within 3 days, a new loan red flag. Each sent once, at most one reminder while it stays open.
- Done when: the two copied Telegram senders are gone, and expired bank access reaches the phone once.

**7. E3: Wiki facts point at subjects** (M). Our own (B4's next step).
- a. Wiki pages about a person, account, policy or thing carry its subject; entity keys come from the subject registry.
- b. The same provider written two ways ("Amigo", "Amigo (Vodafone Portugal)") becomes one subject with aliases, so its facts stop flip-flopping between spellings. Merge the two Telecom pages.
- Done when: Rute's facts are found from her subject, and Amigo's provider fact keeps one value across bills.

**8. New categories: Identity & Legal, then Vehicles, then Education** (M each). Reuse: Homebox's "a thing with typed fields and attachments" (idea only).
- a. Identity & Legal: ID card, passport, driving licence, residence papers, wills and contracts; each with holder (a subject), number, expiry; a reminder before expiry (step 6); suggested High for identity papers.
- b. Vehicles: the car as a subject; insurance, inspection (IPO), road tax (IUC), service; reminders.
- c. Education: school papers per child; dates and fees into Coming up.
- Done when: a passport is uploaded, read, and its expiry reminder appears on To-Dos and reaches the phone once.

## Backlog (parked, not scheduled)
- **Matias's loan: reconcile where we are** (Pedro, 2026-11-03). The Hub shows it at 0 and still active. Park until Pedro picks it up.
- **Emanuel's €15,000 from before 2025-10-10.** It is already counted as an opening balance of 15,000 on 2025-10-09, which is why the balance shows 17,000. Pedro wants it brought in properly: when it was received, how, and whether any of it was paid back before the bank data starts. Needs the date and the amount.

- **Missing bank history** (found by B6, 2026-11-03): the Santander statements of 2024-03 (no lines at all) and 2025-02 (1 line) failed to read on 2026-08-18 with bugs fixed since, and Revolut before 2024-01 was never loaded. Read them again through the normal path, with a check against lines already stored so nothing doubles. Before the 2025-10-10 baseline, so history and spending analysis only.

- **Assess jev for the Hub's narrow decisions** (Pedro, 2026-11-04; https://openrouter.ai/docs/guides/community/jev). Jev (TypeSafe, on OpenRouter) gives typed answers with probabilities instead of text: pick one option, yes/no, a place on a scale. You pay for what it reads; its answers cost nothing. Many of the Hub's calls are that shape: the kind of a new paper, the spending leaf of a merchant, which bank line paid a bill (the three MetLife receipts), whether something is a duplicate, and the verdict of a check. Its probabilities could decide when the Hub asks a person instead of guessing, which would feed C1 and C2. To check:
  - **Gateway:** it would be a new llmsel adapter. The Hub never calls OpenRouter directly.
  - **Privacy:** House High stays Privatemode only, so jev may read Low and, if TypeSafe keeps no data and runs in the US or EU, Mid papers.
  - **Accuracy on large inputs:** it is less accurate above about 32 KB of text, so it should decide on facts already read from a paper, not on the whole paper.
  - **No reasons given:** it never says why. The "why" in a message must come from the facts the Hub gave it.
  - **Stability:** the API is still in alpha.

  Done when: a short benchmark, on the Health benchmark's pattern, compares jev with the current pairing on two or three of these decisions (accuracy, cost in EUR, speed), and says keep, try or drop.

## Pedro's to-dos from this mapping
1. Renew Rute's Santander access on the Bank connections page; it has been expired since before today.
2. Emanuel's €15,000: the date it was received, and anything paid back before 2025-10-10 (see Backlog).
3. (Done 2026-11-03: keep both budgets and commitments; the four phone notices agreed.)
