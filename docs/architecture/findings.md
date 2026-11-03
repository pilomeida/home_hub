# Home Hub as it is, against the agreed model (v2)

Date: 2026-10-28. Read-only. Covers every file in `app/` (about 200 files, 25,800 lines), all 77 templates, 53 migrations, 24 scripts, the deploy workflow, and the live database (57 tables). Model: `../../../docs/architecture-redesign/model.md`. Findings F1–F4 are in `../../../docs/architecture-redesign/findings.md`; this file continues from F5.

Code references are `path:line` under `app/`. "Live" means the production database on 2026-10-28.

## 1. Where each model element lives today

| Model element | Where it lives in the code | Shape vs the model |
|---|---|---|
| **F1 Categories and paper kinds** | `domains/registry.py`, `domains/base.py` (`DomainSpec` = category, `CategorySpec` = paper kind), one `spec.py` per area | **Partial.** The registry exists and all four areas plug in the same way. But a kind carries none of the four things the model puts on it (F5). The words are inverted (F6). |
| **F2 Registry of subjects** | none. People: `people`, `health_people`, wiki `person` pages, Telegram names. Counterparties: `merchants`, `provider_rules.key`, Health `provider` text, Insurance `insurer` text. Things: House `item_name` text. Policies: `policy_number` text. Accounts: `accounts` (no owner) | **Missing** (F1, F9) |
| **F3 Filing and wiki** | Sources: `documents` + `records` (shared). Derived: `fields_json`, `health_entries`/`health_measurements`, `transactions`/`statement_checks`/`utility_readings`, `position_extractions` + loan tables. Wiki: one shared store, `services/wiki_store.py` | **Sources shared, derived split** into five stores (F10) |
| **F4 Privacy and the AI route** | `llm_gateway.py` → `privacy/outbound.py` (the one checkpoint); `privacy/scope.py`, `reach.py`, `ratchet.py`, `settings.py` | **One checkpoint, but it lets calls through untiered** when no scope is set (F7) |
| **Fn1 Bring in** | `channels/email_poller.py`, `channels/telegram_bot.py`, `services/bankapi/`, `services/ingestion.py`, `routers/bills.py`, `routers/loans.py`, card scripts | **Close to the model.** One core, hash duplicates, failures become Problems. Two side doors (F11) |
| **Fn2 Read and check** | About 10 readers (`services/extraction.py`, `position_extraction.py`, `domains/health/extraction.py`, `house/warranty.py`, `card_figures.py`…); 5 check patterns | **One-offs**, no shared reading or checking block (F12) |
| **Fn3 Sort and remember** | 3 paper-kind classifiers + about 10 spending/subject classifiers; 5+ memories of past answers | **Largest gap** (F13) |
| **Fn4 Review and decide** | 23 review points on about 10 pages | **Fragmented** (F3, F14) |
| **Fn5 Answer** | `services/overview_service.py`, `budget_service.py`, `loan_math.py`, Ask (`services/ask/`), wiki pages, per-area browse pages | **Works**, but two spending taxonomies give different answers (F8) |
| **Fn6 Prompt** | Two Telegram pushes (statement reminder, check nudge) with copied code; loan alerts, to-dos and problems are dashboard-only; Health appointments → Google Calendar | **No single mechanism** (F15) |
| **R Run and trust** | `jobs/` (one scheduler, `job_runs`, Problems), `problems.py`, `.github/workflows/deploy.yml` | **Good failure messages, weak safety net.** No backup, no test gate, unpinned dependencies (F16, F17) |

## 2. Corrections to figures given earlier today
- **Bank lines do hang off a document.** All 5,468 have a `document_id`. Feed lines hang off one synthetic "statement" document per account (`services/bankapi/sync.py:56-67`, `source=API`, no file). That document has no tier.
- **Unconfirmed merchants: 1,109**, not 1,430. The higher figure included 321 already merged away.
- **Needs Review: 678 lines**, all on the taxonomy node `unsorted.needs-review`. Only 10 lines have no node at all.
- **Inbox: 64 items** (57 email, 7 Telegram), the oldest from 2026-09-27.
- **`records` is in use** by House maintenance logs. It is not dead.

## 3. Findings

### F5. A paper kind does not yet carry what the model says it carries
The model puts four things on each kind: privacy tier, how it is read, how it is checked, and what it is filed as. `CategorySpec` (`domains/base.py:55-62`) holds only a label, description, accepted media and `kind` (document or record). None of the four is there. Where each actually lives:
- **Tier:** in three places.
  - The database table `document_type_settings`, which has 0 rows live.
  - A hard-coded suggestion list (`privacy/settings.py:30-45`).
  - A Health override (`settings.py:50`), repeated again inside Health's reader (`domains/health/extraction.py:307`).
- **How read:** `if` branches inside each handler (`domains/financials/handler.py:76-80`).
- **How checked:** five unrelated mechanisms (F12).
- **Filed as:** whatever tables the handler writes.

Consequence: adding a new kind means editing a handler, not declaring the kind.

### F6. The words are inverted, and "category" has four meanings
| Model term | What the code calls it |
|---|---|
| category | `Domain` |
| paper kind | `category` |
| paper kind (Insurance only) | `document_kind`, a field |

On top of that, "category" also means the 142-node spending tree (`category_nodes`) and the old 16-value `Transaction.category` enum. The form says "Domain *" (`templates/documents/edit.html:8`). Insurance inverts things a second time: its `CategorySpec` is what is covered (home, car, life, health), and the actual paper kind is a field (`domains/insurance/spec.py:34`).

### F7. The privacy gate lets a call through with no tier (most serious)
`privacy/outbound.py:81-85`: when a call arrives with no tier and no scope is active, it is audited as "unwired" and **sent**. Scopes are entered only by:
- filing a paper (`services/ingestion.py:202`);
- the Inbox suggestion (`inbox_service.py:118`);
- Ask (`services/ask/engine.py:70`);
- the wiki check (`services/wiki_lint/runner.py:102`).

These AI calls run with no scope:
- **Bank sync merchant classification**, three times a day. Bank descriptions go to the AI with no tier (`services/bankapi/sync.py:340` → `services/classification_engine.py:144`).
- The **Loans upload** and re-read of positions and loan history (`routers/loans.py:300`, `services/position_store.py:432,452`).
- Field edits and hand-entered records that update the wiki (`services/ingestion.py:270,298,321`). Health is safe because its reader passes High explicitly.
- The **Unsorted reclassify** (`services/taxonomy_refile.py:193`) and five scripts:
  - `backfill_issue_dates`
  - `backfill_revolut_pedro_account`
  - `backfill_transaction_classification`
  - `import_position_files`
  - `refile_to_taxonomy --reclassify`

Not verified: whether the llmsel gateway itself treats a missing tier as strict. Either way the Hub's side breaks "one gate, fails closed". It does not matter today only because levels are not binding. It becomes a real leak the day Pedro switches them on.

Two more gaps sit inside the gate:
- Redaction knows names only from `people` and the Telegram list (`privacy/scope.py:50-61`). It does not know `health_people`, insurers or House names.
- Bank lines have no tier column. They are covered only through their synthetic document's type, and only for Ask and the wiki check.

### F8. Spending is classified twice, and the answers differ
Every filing writes both the old enum (`Transaction.category`) and the tree (`category_id`) (`services/taxonomy.py:80-86`). The two are read in different places:
- **Old enum:** the Overview's "Where it went", its narrative and anomalies (`services/overview_service.py:282,316,326`); Ask's spending tool (`domains/financials/ask.py:54,60,103`); and commitments.
- **Tree:** budgets, the Transactions page, Needs Review.

Live example: 1,534 lines are `OTHER_EXPENSE` in the enum, but most of them have a precise leaf in the tree. The same question can give two answers on the same page.

### F9. Subjects: more stores than the earlier count
This extends F1. Identity lives in:
- `people` (loans) and `health_people` (none confirmed);
- wiki `entity_key` text: person names, policy numbers;
- `merchants` (46 live ones carry family names; the AI prompt itself tells the classifier to make "Matias Almeida" a merchant, `services/classification_engine.py:60-64`);
- `DebtMatchRule.normalized_key` (a merchant key standing in for a person);
- `BankConnection.owner` and `SavingsSnapshot.holder` (plain text);
- `accounts` (no owner link).

There are five separate name normalisers: `domains/health/text.match_key`, `wiki_store.normalize_entity_key`, `Merchant.normalized_key`, `provider_key`, and the `DebtMatchRule` key.

Health's `domains/health/people.py` (find-or-create, confirm, merge with alias) is already the registry the model wants, just living inside Health.

### F10. Sources are filed once; what is read from them is filed five ways
`documents` and `records` are the one shared source store (`domains/entries.py:46-70`). What is read from a source goes to five separate places:
- `fields_json`
- the Health tables
- the Money line tables
- `position_extractions` plus the loan tables
- `inbox_items`

Each area also has its own withdraw and purge logic. Some of these stores repeat each other: `health_entries` copies person, date, provider and summary from `fields_json` (`domains/health/store.py:54-82`), and also carries three money columns that nothing writes.

The wiki is one shared store with a single page namespace. A page keeps whichever category created it first (`services/wiki_store.py:76-98`). `wiki_links` is empty because only House item→room and saved Ask answers write links, so the wiki check flags every page as an orphan (`services/wiki_lint/checks.py:33-38`).

### F11. Bring-in has two side doors
- **Loans upload** (`routers/loans.py:266-311`) has its own size check, PDF clean-up, duplicate and compatibility rules, and re-processing. It runs outside the privacy scope (F7). It is the only channel that strips the bank's PDF wrapper (`services/pdf_bytes.py`), so the same PDF sent by email gets a different hash and is not seen as a duplicate.
- **Card statements** load only by script (`services/card_figures.py`): no Inbox, no Problem, no privacy scope. The lines hang on whichever document the card account already has (202-206). Rute's monthly card statements go this way.

Duplicate catching uses seven separate mechanisms:
1. file hash
2. fuzzy bill match
3. bank reference number
4. renumbered twin
5. feed-absorbs-statement (one direction only)
6. card line match
7. bill-settled-by-bank

An uploaded statement is **not** compared with feed lines that already exist (`domains/financials/handler.py:357-363`). Live today there is no overlap (0 matching pairs; each account's statements end before its feed starts). But uploading a statement for a feed period would double-count, and the balance check would not see it.

Hand uploads skip the rules and the Inbox suggestion. A duplicate on hand upload silently redirects to the existing paper, with no message.

### F12. About ten readers and five ways of checking
- **AI readers:** bill, bill-vs-statement, statement lines, utility detail, positions, loan history, Health, warranty, Inbox suggestion.
- **Text readers:** loan history, card list and statement, recognition, Health text.

They share only the file-to-AI block and the JSON clean-up. Each has its own number parsing (Portuguese decimals are handled in one reader, not the others), its own error type and its own size guard.

The five check patterns:
1. **Statement add-up** (`services/statement_check.py`): the best one. It gives a verdict, flags the doubtful lines, and sends them to the To-check list.
2. **Position reconcile:** a list of strings; any problem fails the whole paper.
3. **Card add-up:** raises an error.
4. **Health checks:** fidelity against the paper's own text, plus a second independent reading.
5. **Real-world checks:**
   - Bill-settled-by-bank: the list of unmatched bills is thrown away (`services/document_reconcile.py:116-125`).
   - Bank balance roll-forward: a Problem.

Health's read-twice-and-check (`domains/health/reading.py`, `checks.py`) and its benchmark (`domains/health/benchmark.py`) are reusable blocks that only Health uses.

### F13. Sorting has 13 decision points and five or more memories
**Paper kind** is decided up to three times:
1. rules (`privacy/recognise.py`, with 0 rules live, so every Inbox paper goes to the AI);
2. the Inbox AI suggestion;
3. a second AI inside the Money handler, which decides bill or statement after the person has approved (`domains/financials/handler.py:68-74`). No person sees that second answer.

**Spending and subject** are decided by:
- code tables (structured providers, Multibanco entities, the supermarket keyword);
- the AI;
- loan and insurance linkers;
- debt rules;
- provider rules;
- merchant defaults;
- the old-enum mapping.

Then batch jobs can re-file over these: majority vote, AI reclassify, entity re-file, card memory, transfer direction, top-ups. They apply the rules in a different order. Example: `services/merchant_reconcile.py:37-54` ignores a person's provider rule, so a person's choice can be undone. Some passes respect a line a person has checked (`checked_by`) and some do not.

**Memories of past answers** are kept in five or more places:
- recognition rules
- merchant defaults
- provider rules
- debt and insurance rules
- merchant aliases
- plus several lists written in code: entities, keywords, card stems

Confirming a merchant teaches the Inbox nothing, and approving in the Inbox teaches merchant memory nothing.

### F14. Review: 23 points, three counters, half without "who"
This extends F3. There are 23 places where a person settles something:
- **Needs Review:** 5 kinds
- **Bulk classify**
- **Inbox**
- **To-check:** lines and statements
- **Health:** values, notes, outline, areas, topics, people, failed paper
- **Loans:** printout match, red flags
- **Problems**
- **Wiki check**
- **Privacy suggestions**
- **Bank account mapping**

The **To-check list** (`services/to_check.py`, `templates/to_check/_line.html`) is the best pattern. It shows why the Hub is unsure, highlights the doubtful field and the source page, offers confirm, fix or reject, and records who and when.

The rest fall short of it:
- Nine of them store no "who": merchant confirm, recurring, debt link, Health values, areas, topics, people, loan match, account map.
- Health "it's right" writes straight to the table with no stamp (`routers/health.py:317`).
- The three counters disagree: To-check, Problems, and Overview's review count. None of them counts everything waiting.
- Doubt sits under topic menus in at least 8 places. The model wants one.
- Loans keeps its own doubt state (`needs_loan`): the paper shows as done, and only the Loans results page shows the doubt.

### F15. Prompt: two pushes, copied code, and two notices that go stale
- **What is pushed:**
  - Only the statement reminder and the weekly to-check nudge are pushed, by Telegram.
  - Their code is copied (`jobs/runners.py:169-200` and `216-239`).
  - Nothing is ever emailed.
- **What is shown but never pushed:**
  - loan red flags (15 live, all acknowledged)
  - Problems (Rute's expired bank access has been raised 28 times since it lapsed; nobody was told)
  - to-dos
  - Health appointments go to Google Calendar, which does its own reminding.
- **Bugs:**
  - The reminder runner returns early on non-due days, so an old reminder problem never clears (`jobs/runners.py:166-167`).
  - One failed send writes two problems (`jobs/statement_reminder.py:80`, `jobs/scheduler.py:87`).
  - Bill to-dos have no duplicate guard (`services/todo_engine.py:22-38`).
  - The bank runner has drifted from the job body. It never raises "connected but no key" and never clears the top-ups problem (`jobs/runners.py:110-127`).

### F16. No backup of the Hub
Checked live:
- The server's backup job (`/usr/local/bin/ergon-db-backup.sh`) covers outreach, fundraising and career_outreach only.
- Nothing in the code or the deploy backs up the database or the uploaded files.
- The last manual snapshot is from 2026-10-21 (`/root/home_family_pre_topics_200_20261021.db`).
- Every deploy runs `alembic upgrade` on the live database with no snapshot first (`.github/workflows/deploy.yml:23`).

### F17. Deploy is unguarded
`.github/workflows/deploy.yml`:
- runs no tests and has no rollback;
- installs 14 dependencies, all `>=` with no lock file, so any push can pull a new major version;
- ships `pytest` to production.

The scheduler runs jobs one after another, so a stuck bank sync or wiki check can delay the 10-minute mailbox check by up to 45 minutes (`jobs/scheduler.py:100-106`).

### F18. Failures a person cannot see
These break the Messages rule. Some are swallowed or only logged:
- Recording the problem itself can fail silently (`services/ingestion.py:138`, `services/inbox_service.py:130`).
- A line that fails classification is only printed (`domains/financials/handler.py:203,324`).
- Bank rows the Hub cannot read are counted, with no Problem (`services/bankapi/sync.py:313-316`).
- Loan link and alert failures are only logged (`services/position_store.py:37-57`).

Some are shown raw:
- Raw bank errors appear on the Bank page (`templates/bank/connections.html:57-58`).
- Exception text becomes the message (`routers/ask.py:84`, `routers/inbox.py:119,140`, `routers/loans.py:547`, `routers/bills.py:58`).

The error box itself is broken on some pages:
- `templates/base.html:320-322` stops htmx's error event from firing, so a failed press on Needs Review or the budget panel **replaces the whole list with one plain sentence**.
- The overview's budget errors are bare text (`routers/dashboard.py:61-92`).

The wording is also fragile. Routers raise bare strings that `problems.py:218-260` matches word for word, so rewording a router silently drops the plain message. Loans parses its own service's notes with regexes (`routers/loans.py:190-231`).

### F19. Logic in the wrong layer
- **Routers holding business rules:**
  - `routers/transactions.py`: the ledger query engine, the debt-link decision tree that learns a rule (559-624), and commitment creation using the old enum (530-550).
  - `routers/loans.py`: an upload pipeline, its own message system, and loan aggregation.
  - `routers/health.py`: interpretation, a background reader outside `jobs/`, and a direct `fields_json` write.
- **Routers importing other routers:** `routers/bills.py:27`, `routers/inbox.py:20`, `routers/wiki.py:16`.
- **Spending-tree slugs written into services:** `services/loan_insurance.py:27`, `services/loan_linking.py:19`, `services/debt_ledger.py:29`, `services/budget_service.py:84`. Renaming a node silently breaks them.
- **Presentation repeated:** about 10 ways of writing euros, many dates bypassing the yyyy-mm-dd filter ("due None" shows when a to-do has no date), and about 10 copies of the three-part message box.

### F20. Tables: what is waiting, never used, or dead
| Table | Verdict |
|---|---|
| `budgets`, `records`, `job_schedules` (defaults live in code), `ask_*`, `document_fingerprints`, `reminder_logs` | Wired and waiting for first use |
| `document_type_settings`, `privacy_settings`, `tier_changes` | Wired, never used: **binding privacy has never run live** |
| `statement_checks` | Wired; rarely triggered now that the bank feed replaced statement uploads |
| `debt_match_rules`, `wiki_links` | Wired, never triggered |
| `commitments` | Effectively dead: overlaps budgets and loans, uses the old enum, and no one has created one |
| `wiki_changes`, `documents.doc_type`, `health_entries.amount_*`, legacy Health kinds `medical_bill`/`claim_statement` | Dead |
| `baseline_positions` | Written by a script; nothing in the app reads it (unverified beyond grep) |

### F21. Live data that looks wrong (Pedro to confirm)
- Rute's Santander access has expired. All 3 of her connections say EXPIRED, although they claim to be valid until 2027-04-17, so nothing of hers syncs. A stale PENDING connection (id 1) also remains.
- Matias's loan (debt 4) has a balance of 0 but is still `active`.
- Emanuel's loan (debt 6) started at 2,000 but its balance is 17,000.

### F22. The design docs have drifted
- `docs/ARCHITECTURE.md`:
  - says 20 migrations; there are 53;
  - lists 4 jobs; there are 5;
  - says everything runs in the request process;
  - presents commitments as live.
- `docs/SYSADMIN.md`:
  - lists 6 of 24 scripts;
  - says `pypdf` is deliberately absent, but it is listed;
  - names Anthropic models directly, although calls now go through llmsel;
  - says a script exists only on the server, but it is in the repo.
- `.env.example` is missing 9 settings the code reads.

## 4. Questions only Pedro can answer
Answered 2026-11-03: Insurance is a category of its own; bank lines take no privacy level of their own (they
inherit the statement's); Matias is parked in the backlog; Emanuel's 15,000 is to be brought in properly (work-list
Backlog). Budgets and commitments both stay, with distinct jobs: a budget is a planned maximum to monitor
against, a commitment is a known coming spend for cash flow (work-list B3b). The first four phone notices are
agreed: bank access expired, a job that keeps failing, a bill due within 3 days, a new loan red flag (work-list C6).

1. **Insurance:** a fourth category, or policies as subjects under Money & Bills and House? The code treats it as a category today.
2. **Bank lines:** should a feed line count as a paper of kind "bank line" with its own privacy level? Proposed: yes, and lines inherit it.
3. **Commitments:** budgets now cover planned spending. Retire commitments?
4. **Prompts:** which notices should reach your phone rather than wait on a page? Candidates: expired bank access, a failed job, loan red flags, bills due.
5. **F21:** the two loan balances.
