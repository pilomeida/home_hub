# Home Hub plan

**The only plan for the Hub (2026-11-19).** It replaces the work list (`docs/architecture/work-list.md`, moved here),
the backlog in `docs/privacy/DECISIONS.md`, the phase table of the Conditions master plan and the privacy phase (d)
plan. Anything pending goes here and nowhere else. Decisions stay in DECISIONS.md, the design in ARCHITECTURE.md, the
evidence in `docs/architecture/findings.md`; the dashboard (https://claude.ai/artifact/FAy2A5HYWy4bqk6u5AsRTa) is a
view of this file, and when they disagree this file wins.

**Goal.** One place for the family's admin life: anything can be brought in, it is read, sorted and remembered, and
the family can look it up, ask about it, and be told when something needs them. Built as functions (bring in, read
and check, sort and remember, review, answer, prompt, run and trust) over categories (Money, House, Health,
Insurance, and later Identity & Legal, Vehicles, Education). Knowledge sits in three layers: **tables** for numbers
and dates, the **wiki** for standing facts (each with its sources, its subject and when it was true), **search over
papers** for the long tail. A kind of paper gets its own reader only once the same exact question keeps being asked
about it.

**Now (2026-11-20):** step 8 is live (039ce55), checked live and in section 4; step 9 D8 is live (38519da: 24 payee rules, migration c9e4a2f6d018 applied, pages checked); D9 (new merchants asked together) built and tested, going live with this commit. Next in step 9: D11 (large Revolut statement read in parts); D12 partly waits on Pedro's Health papers. The choices made for Pedro are listed in section 2. Step 10 is live except 10g, which waits on Pedro (section 2).

**How this file is kept (Pedro, 2026-11-19).** This is the live document that rules execution, updated as work
happens, not afterwards: a new task is written in before it starts; a change to a task is edited into its entry,
dated; a slice that starts sets "Now"; a slice that lands is marked in its step (✅ date and commit, or ◐ with what is
left), and a finished step moves to section 4. Every commit touching Hub code carries this file (a hook refuses
one that does not; "PLAN-EXEMPT: <reason>" only for work no step covers). Rules in `CLAUDE.md`.

**Running it without Pedro (2026-11-19).** `/goal execute PLAN.md` runs section 1's steps in the order listed (1 → 5, then 10, then 6 → 9), slice
by slice (the lettered items), with no input from Pedro. What it may do, what it decides, and when it stops:
- **Authority.** Pedro's standing OK (2026-11-04) plus this section: commit, push and deploy each green slice; run
  one-off scripts on the server under the protocol below; refresh the dashboard when a step finishes.
- **Each slice:** tests first (test data from the production path; privacy tripwires for any new reader), the
  targeted tests green, commit with PLAN.md updated, deploy (`git subtree push --prefix="Home & Family" home_hub main`;
  the suite gates it), then check the live Hub: health page answers, journal clean, Problems page has nothing new.
- **Server scripts** (filling text, re-reading statements, merging pages): the nightly backup plus a named copy
  first; a dry run whose output is read; apply only when it changes nothing outside the slice's scope; check after;
  record counts in the step.
- **Decides on its own** every technical choice, recorded in the step as "(Claude, date)". Defaults already taken:
  FTS5 with diacritics folded (Portuguese); a paper with no text is read by the AI at its own level, in batches; AI
  rules below confidence 0.8 are saved switched off; review items snooze for 7 days; statement-to-feed matching
  within 3 days; lab test names: exact alias, then a near match of at least 0.9 (Python's difflib, no new
  dependency); Identity papers suggested High, contracts, letters, vehicles and education Mid; expiry reminders 60
  days ahead for passports and ID cards, 30 days for the rest; Health follow-up to-dos 14 days ahead.
- **Pedro's answers that set what the family sees** are written into steps 1, 3 and 6 (2026-11-19). A new question
  of that kind that is not answered here: take the choice that follows the existing pages, record it as "(Claude,
  date; Pedro may change)", and list it at the end of the run.
- **Not in the run:** step 2f (jev). Section 2 does not block: step 5 aims at the two benchmark cases on file
  (`cintramedica-lab-2024-11`, `hsm-doppler-2026-03`) and takes more when Pedro sends them; step 7c is built without
  the calendar. The files step 4c re-reads are on the server (documents 2, 22, 33; Revolut 2022-08 → 2026-08 is
  document 51).
- **Stops and asks Pedro only when:** a deploy or migration fails twice; a dry run would change or remove data
  outside its slice; a live balance, loan or budget total moves by more than a cent without the slice explaining
  it; anything would let a High or Health paper reach a lower route; a test would have to be weakened to pass; an
  AI step would cost more than €5 (the gateway's spend guard also refuses); the other session's uncommitted work
  would be overwritten; a slice of step 10 would need a data-model or reader change, or a page would show less or
  different data than before the redesign.
- **Resuming:** the "Now" line is where a run picks up after a break or a context reset.

**How a step is done.** Each step ships as green slices: tests, commit, deploy, then any one-off script on the server.
"Done when" names what a test shows. IDs in brackets are the old work list's (A–E), so code comments saying
"work-list C2" point here. Size: S under half a day, M one to two days, L three days or more. Reuse: only
MIT/Apache/BSD code is adapted, with attribution; GPL/AGPL projects give ideas only.

---

## 1. Next, in order

The order follows what each step needs from the one before: text of every paper first (search and the readers build
on it), then the memory of answers and the one review queue (every later step sends its doubts there), then the money
accuracy work, then Health reading, subjects in the wiki, Health conditions (a condition is a subject), and new
categories last (they need search, reminders and the review queue). Clean-up rides along: when a step touches code a
D item names, that D item is done in the same step.

### Step 10. UI redesign, Option A (Pedro, 2026-11-19) · XL (nine slices; run after step 5, before step 6)
Numbered 10 so no other step is renumbered; it is listed here because steps 6–8 add pages, and those should be built in
the new look instead of redone (Claude, 2026-11-19; Pedro may change). Pedro chose Option A ("super clean and readable")
and said tweaks will come from using it.
- **Spec (read before any slice):** `docs/design/ux-workflow-and-screens.md` (workflow, menu, the chart rules);
  `docs/design/mockups/option-a.html` and `chart-rules.html` (sample data; generator `_build/build_mockups.py`, whose
  `make_scale` and chart functions are the reference for the scale rule); `docs/design-references/` (the 13 inspirations);
  `Personal/brainstorms/2026-11-19-hub-ui-workflow-and-screens.md` (the reasoning and Pedro's exact words). Options B and
  C are archived: do not build them. Light theme only; no gradients; no faces or avatars.
- **What keeps this step from interfering with the others:**
  - Presentation only, unless a slice says "new behaviour": templates, one stylesheet, one chart component. The same
    queries, the same privacy reach (`app/privacy/reach.py`), the same routes and tables: a re-skinned page shows exactly
    what it showed. No data-model change and no reader change in this step.
  - A page that needs something a later step builds (a condition's page, visits, medication: step 7; a car, an ID, a
    school paper: step 8; the wiki by subject under "What the Hub knows": step 6) is drawn with what exists today.
    That later step builds its block with the new components. Those steps' "Done when" lines do not change.
  - Each new behaviour is its own slice with its own tests; privacy tripwires for anything the AI reads.
- **Each slice:** tests, commit with PLAN.md, deploy, then check the live page at desktop and phone width against
  the mock-up (a screenshot, e.g. Playwright), health page answers, journal clean, Problems has nothing new. Every page
  that is present today stays reachable, and old URLs keep working.
- a. Foundation: design tokens (colour, type, spacing, radii), one stylesheet, shared components (card, button, chip,
  tab, table, the three-part message, empty state) and **one chart component** (columns, history line, trendline,
  horizontal bars) on one scale function: real axis values, floor computed from the data and capped at 1.5 times the real
  difference (`MAX_EXAGGERATION`), the current month only as far as it has happened, past months with their full month
  and the value by the same day. Tests: for every existing chart, built from the code that feeds its page, the cap
  holds, no current-month value is past today, no forecast or budget is drawn. Every chart already on the Hub moves to it. ✅ 2026-11-19 (`app/ui/charts.py`: `make_scale`, columns, history line with
  trendline and reference band, horizontal bars; `app/static/hub.css`: Option A tokens, card, button, chip, tab,
  table, three-part message, empty state, and base.html's rules moved in unchanged). The Overview's trend cards,
  cash flow (now Income and Expenses side by side, each past month full and up to today's day), "Where it went" (this
  month so far on last month in full) and the Utilities series all draw through it; negative values (net flow) span
  zero. 316 tests.
- b. Shell: the menu (Home · Money · Health · Things · Papers · Coming up · Waiting for you (n)), the header (Ask/search,
  Who chip showing Everyone, + Add a paper, the gear, a Problems dot only when something is wrong), the phone bottom bar
  with a central +. The gear holds Privacy, Scheduled jobs, Bank connections and What the Hub knows, as they are today. ✅ 2026-11-20, b7851bf, live with 10a (deploy 44651773605 green, journal clean, Problems nothing new) (`app/ui/shell.py`: the seven sections, each with
  its own row of pages from the category registry; a category no section names yet shows under Papers; "Ask or
  search" in a bar under the header, as in the mock-up; Settings (`/settings`) behind the gear; the phone's More
  (`/more`) lists every page; the Problems dot only when something is open; old URLs unchanged). The "Who" chip shows
  Everyone until 10i. The 10a deploy failed once on a test that counted a style rule moved into the stylesheet (fixed
  with 10b; nothing reached the live Hub). Four older tests that looked for menu links on the Overview now look where
  the links live (Money's row, the gear).
- c. Home: Ask box first; Money this month (Earnings and Spending, three months each, past months filled to the same day;
  available balance after the bills due this month); Coming up with To-Dos; Waiting for you as four slim cards (area,
  count, button). Earnings = incoming bank lines without the family's own transfers; available balance = cash balance
  minus bills due this month (Claude, 2026-11-19; Pedro may change). The menu's Waiting count leaves out the unconfirmed
  merchants, which show as their own line inside the queue (changes step 3's count; Claude; Pedro may change). ✅ 2026-11-20 (`app/services/home.py`, `home.html`;
  "/" is Home, the old Overview is unchanged at `/overview`, linked from Home's row and "Money this month › Open".
  Available = the bank's balance plus lines since, minus unpaid bills and expected payments to the month's end and
  anything overdue; the chip says what the balance rests on (there is no "matches the bank" check to show). Coming up
  shows the next six by date: bills, regular payments and dated To-Dos, a bill's own to-do once. Waiting: Inbox,
  Money, Health, Merchants; the menu, the heading and the queue's "All" leave out the merchants, which are one line
  and their own tab on /waiting (live: 105 waiting plus 1,149 merchants). Older tests that read the Overview at "/"
  now read it at /overview.) The first 10c deploy (44654408534) failed on three more such tests that the local run
  had not covered; fixed, with the tests that assert something is absent from the Overview moved with it (so they
  still check something); nothing reached the live Hub. ✅ live 2026-11-20 with 10d (`5aa4370`, deploy 44666588749).
- d. Money: one page, tabs Spending, Bills & due, Loans, Utilities, All lines; the headline row; the breakdown with its
  trend. **New behaviour:** category history (any category at any level, a number of months; view columns, line,
  trendline or bars). ✅ 2026-11-20, live (`5aa4370`; `app/services/money_page.py`, `/money`, `/money/history`; the
  Money menu item and the phone's € open `/money`). Headline row: spent this month (with "if budgets are respected"
  as the budget panel says it), cash balance (what it rests on), due in the next 7 days plus overdue, open loans
  and red flags; each opens its page. Tabs are the section row: Spending (`/money`), Bills & due, Loans, Utilities,
  All lines (their pages, unchanged), Category history. Category history: every spending category at every level
  plus "Not filed yet", 3/6/12/24 months, columns / history line / trendline / bars, counted with the budget's rule
  (agrees with the Overview's breakdown, tested); a table by month (spent, by the same day, top merchant, its lines).
  The budget is not drawn and not in the history table: a yearly line has no monthly budget to compare (Claude; Pedro
  may change). The Overview stays at `/overview`.
- e. Waiting for you: the grouped list; a paper card by card (paper left, what the Hub read right, keyboard shortcuts);
  merchants as a bulk table with the best guess filled in. Existing behaviour, new look.
  Found 2026-11-20: the "See it" link of an AI answer waiting for a person goes to `/transactions?q=…`, a page that
  does not exist (45 live items); fix the link the queue writes and the items already stored, with 10e.
  ✅ 2026-11-20, live (`a5480ab`, deploy 44675573672; `app/services/queue_view.py`): `/waiting` opens with one row per area
  (count, the first item, how old the oldest is, "Open the first ›") and the merchants on their own row ("Open the
  table ›"), then the list as before. `/waiting/one?area=…` shows one item at a time: the paper on the left when the
  item is about one (a statement that doesn't add up, a line on a statement, a health paper read with doubts), the
  question and its answers on the right; Enter = the first answer, L = later, J / K = next / previous (moving along
  answers nothing). Merchants are a table: name, the category the Hub filed it under, its number of lines; tick the
  right ones and confirm them in one go (a merchant with no guess is chosen on its own page). The answers and what
  they do are unchanged. The "See it" link is fixed for new items and a migration repairs the stored ones (live: 45 repaired, 0 left).
- f. The rest, with what exists: Health (name tiles, no faces; a strip of follow-ups; a person's page with results and
  the reference range, timeline), Things (house and items as today), Papers (everything filed; category chips; search),
  Coming up with To-Dos as a tab, Problems and the gear's pages. Papers is the same list pattern as All lines (not drawn).
  ✅ 2026-11-20 live (3f6aa39, home_hub 128df36, deploy 44691099183; the full suite found 2 failures, fixed before the commit: Waiting counted before the queue was brought up to date; the card used an old variable name). Health opens on `/health/family` (`/health` is the server's health
  check, so it cannot be a page): a tile per person (name, next follow-up, latest paper, papers; no faces), the
  follow-ups across the family (open Health To-Dos, overdue first), papers by approved area or condition with the
  proposed and unsorted counts linking to their pages; a person's page `/health/people/{id}`: conditions, each result
  drawn on the chart component against the range printed on the paper (last 8 points; "Every result ›" keeps the full
  table), and a timeline (papers, and follow-ups ahead). Coming up's Dates tab adds the dated To-Dos of an area (a
  general To-Do stays on To-Dos only, as before; a bill's own To-Do is its bill), an area chip per row and chips to
  show one area; each month's total stays the money due. Papers (`/papers`): everything filed, newest first, a chip
  per category, and the search inside a chip. The gear's page: a card per setting with what it says today (open
  problems, bank connections, how many kinds of paper per level, the jobs and their last run, the facts the Hub
  knows), each opening its own page. House and To-Dos in the card style; their content is unchanged. Things stays
  the house and policies as today (a car or an ID is step 8). (Claude, 2026-11-20; Pedro may change: the tab names
  Family and Dates; a person's tile shows the full name as the paper prints it.)
- g. Ask: the answer card (one plain sentence, one matching visual, sources, Open the page / Save to wiki; no
  suggested-question chips). **New behaviour:** three tiers of action (undoable with Undo; a confirm card for what
  changes what the family sees; never from Ask: delete, privacy levels, bank connections) and a file attached to a
  question (goes to the Inbox as a suggestion). Through the existing Ask tools; tripwire tests cover each.
  **Waiting on Pedro (2026-11-20):** Undo and Confirm need the Hub to remember, for each answer, what Ask did or
  proposed, which means one new small table; the run's rule stops step 10 before any data-model change. The answer
  card's new look and the file attached to a question (it uses 10h's "Add a paper") wait with it.
- h. Phone: Home, Ask, and **new behaviour** a light "approve a paper" card (the file, four key fields, Approve / Edit /
  Later) for a paper uploaded on the run, using the Inbox's existing approval.
  ✅ 2026-11-20 live with 10f (3f6aa39). The Inbox had no way to add a paper from the page (only email
  and Telegram), so the + now opens "Add a paper" (`/inbox/add`: choose a file or take a photo, an optional note);
  the paper goes to the Inbox exactly as an emailed one (same recognition, same reading in a High run, same duplicate
  check) and opens its card (`/inbox/{id}/card`): the file, "Filed as" the Hub's guess, up to three of that kind's
  fields (the required ones first), Approve (the Inbox's own approval; a missing field comes back on the card),
  Edit (the full Inbox card) and Later. An unsure paper says so and sends you to choose. (Claude, 2026-11-20; Pedro
  may change: 25 MB limit from the page; the note is shown to whoever files it.)
- i. **New behaviour:** the Who chip works (Everyone / a person) on Papers, Coming up, Waiting for you and Ask; people
  come from the subjects registry; it filters and hides nothing from anyone's login.
  ✅ 2026-11-20 live with 10f and 10h (3f6aa39, `app/services/who.py`). The header chip lists Everyone
  and each person in the subjects registry (an alias counts as its person); the choice is kept in this browser
  (a cookie) and hides nothing from anyone. A paper is about a person when the Hub already links them: a Health
  paper through its person, a bank paper through the owner of the account its lines are on. Papers and Waiting for
  you show only that person's papers and items; Coming up shows their bills and To-Dos, says how many rows about
  nobody in particular are left out, and each month's total is theirs; Ask writes "About Rute: …" into the question
  (seen on the page; nothing hidden is added). Each filtered page says whose things it shows, with "Show everyone".
  (Claude, 2026-11-20; Pedro may change: commitments and papers with no person are left out while one person is
  chosen; the registry still lists loan contacts and duplicate spellings until they are merged.)
- Done when: every desktop and phone screen of `option-a.html` exists live and matches it in layout and chart rules; the
  family can do everything they could before; the privacy tripwires and the suite are green.

### Step 9. What clean-up is left · S–M each
Whatever the steps above did not already touch.
| ID | What |
|---|---|
| D1 | Retire dead tables and columns (`wiki_changes`, `doc_type`, `health_entries.amount_*`, legacy Health kinds); the statement reader's unread `category_hint` (paid output) and the bill reader's own 15-word utility list ✅ 2026-11-20 built and tested: migration b8d1f3a5c702 drops `wiki_changes`, `documents.doc_type` and the three Health amount columns; the wiki's "Earlier history" and Health's "this paper is a bill" notice are gone with them; legacy Health kinds removed; the statement reader no longer asks for a word per line (nothing read it); the bill reader's word list is the three the Hub uses (electricity, water, telecom) plus other |
| D2 | Router logic into services (transactions, loans, health); no router imports a router; spending-tree slugs out of services ✅ 2026-11-20 built and tested (the 78 test files around the moved code: 1,113 passed after one name clash in the bulk edit was fixed): the Transactions list, its bulk edit, a merchant's new commitment and the loan counterparty rule are services (`transaction_list`, `debt_ledger.learn_counterparty`); the Loans pages' rows, overview and history are `loans_page`; a Health paper's results, outline, the waiting papers, areas, topic tree and a topic's conclusions are `health_views`; the bank banners and connection rows are `bankapi/notices` (the Settings summary no longer imports a router either), the wiki-check count is `wiki_lint/findings`; page pieces more than one page shows (the To-check cards, Same person?, the pages Identity, Vehicles, Education and Pets share) are in `app/ui`. Every slug the code relies on is named once in `tree_slugs` and a test checks it exists in the tree the seed builds, and that no other code writes one (seed and history data exempt; the payment entities until D8). Tests guard both rules (`test_layers`, `test_tree_slugs`). The tree's single-word top groups ("unsorted", "loans-debt") stay as written (Claude, 2026-11-20) |
| D3 | No swallowed or logged-only failure in the intake paths; no raw exception or bank error on screen; the last field-level hints in `app/categories/fields.py` and the import script's `--dry-run` line follow the messages rule ✅ 2026-11-20 built and tested: the email and Telegram channels already record every failure as a Problem (logging only when recording itself fails); a paper that could not be read shows the plain three-part message on the new categories' pages (never the error text); the field hints now say what was not saved, why and what to do ("Item is missing, so nothing was saved. Fill it in, then save again.", a date or choice the Hub cannot read likewise); the import script's dry run says not imported, why and what to do; a problem that cannot be recorded is logged instead of dropped silently |
| D4 | One money filter and one date filter in templates; one three-part message macro ✅ 2026-11-20 built and tested (full local run 2,897 of 2,898; the one failure, a name clash on the wiki check page, fixed and its pages rerun, 286 passed): `money` filter (€1,234.56, or whole euros) for every amount shown with €; `display_date` for every yyyy-mm-dd; `_message.html` `three_part(p)` for every what/why/what-to-do (bank and loan macros now call it; the Problems page's link and details come from it). Plain numbers under a "€" column heading stay plain; the Settings page's three-line problem layout stays its own. A test refuses hand-made money, date or message markup in templates |
| D5 | The mailbox check is never stuck behind bank sync or the wiki check. ✅ 2026-11-20 built and tested: the scheduler has two lanes, each its own process on its own 5-minute timer (`home-hub-scheduler` quick: mailbox, notices, reminders; `home-hub-scheduler-long`: bank sync, wiki check, backup, paper text, privacy self-test); before, a running bank sync stopped every later tick for up to 45 minutes |
| D6 | ARCHITECTURE.md, SYSADMIN.md and `.env.example` up to date ✅ 2026-11-20: `.env.example` names every setting the app reads; SYSADMIN has the scheduler's two lanes and step 8's pieces; ARCHITECTURE no longer lists what D1 dropped and names the shared money, date and message formats |
| D7 | The test database enforces foreign keys everywhere, not only in `fk_session` (how the Health delete-order bug passed) ✅ 2026-11-20: links are on in the shared test database (`tests/conftest.py`); the 16 tests that pointed at rows that did not exist now build them (`tests/fakes/records.py` `a_loan`; the card tests create accounts 1, 3 and 4 as live has them); the missing-merchant test writes its one broken row with the check off, as old live rows were. Full suite with links on: 2,898 passed |
| D8 | (from step 2, 2026-11-19) The payment entities and the supermarket list move into `rules` as Pedro's person rules, with a merchant-key column so the merchants they made keep their keys; they already rank right, so nothing a family member sees changes ✅ 2026-11-20 built and tested: 24 person rules (14 entities, 4 payment texts, 6 chains) by migration c9e4a2f6d018, the 18 payees with a merchant key; the code reads the table (`payment_entities.payee_of`, `chain_of`), the first list kept as data (`payee_seed.py`). A payee rule names a new line's merchant and never outranks a person's later answer about that merchant (tested). Rehearsed on a server copy of the live data: all 4,041 bank texts and merchant names answer as before (0 differences, after one fix: a text naming two chains is the chain named first). 594 tests around it pass |
| D10 | (from step 3b, 2026-11-19) Health's outline confirmation onto "Waiting for you": it is a per-paper button today, with no list of papers waiting for it to adopt ✅ 2026-11-20 built and tested: "Is this outline right? <provider> paper for <person>" on Waiting for you (Health) for each paper whose provider's outline nobody has confirmed yet (only the first such paper matters: once a provider is confirmed its later papers are not asked); Right confirms it, Not right is not asked again (correct it on the paper); the paper's own button stays |
| D11 | (from step 4c, 2026-11-19) Revolut before 2024-01: document 51 (2022-08 → 2026-08, 1.3 MB) is refused whole by the AI provider ("cannot succeed by retrying"). Read large statements in page chunks and keep only the lines before 2024-01 (later months are in the feed and the monthly statements); compare live totals before and after |
| D12 | (from step 5d, 2026-11-19) Health reading, what is left of "first time right": narrative findings as a structure of their own (today a long text in a result); Pedro's field corrections kept per provider (the provider's name is learnt; other fields are not yet); grey bands on lab sheets (pypdf gives no shading; needs another reader); 5e grows the benchmark as Pedro sends papers |
| D9 | (from step 2c, 2026-11-19) AI calls in batches with an index per item: the bank sync's new-merchant pass sends one call per merchant today ✅ 2026-11-20 built and tested: before a bank sync or a statement files its lines, every line that would need the AI (no payee rule, no known merchant, no chain) is asked together, 25 per call with an index each, inside the lines' own privacy scope (a call outside one is refused, tested); the category list goes once per call, not once per line; two lines of one merchant are asked once; an item the answer leaves out or gets wrong, or a batch that cannot be read, asks alone as before, so nothing is lost. A stand-in classifier in a test offers no batch and works as before. 529 tests around it pass |

---

## 2. Waiting on Pedro
Each needs his hands or his call; nothing in section 1 waits on them unless it says so. As last recorded (date in
brackets); tick off here when done.
| What | Unblocks |
|---|---|
| Renew Rute's Santander access; ask her to look for a card/data-sharing setting in her Santander app (her card 5990 is not returned) (2026-11-03) | Her card in the feed; else her monthly statements stay the route |
| Emanuel's €15,000: when it was received, and anything paid back before 2025-10-10. Eduardo's balance before 2025-10-10 (2026-11-03) | Loans shown correctly |
| 3–5 Health papers from other providers (another lab, a clinic letter, a prescription, an insurer statement, a scan or photo), with the expected result where he has it; review paper 200's page (2026-10-19) | Step 5 benchmark |
| Approve the proposed Health areas, people and topics from papers 199 and 200 (2026-10-28) | Moves into the step-3 queue once built |
| Payee names for the numbers in `docs/unnamed-payment-entities.md` (2026-10-28) | Step 2 rules |
| Configure `/privacy`; decide "Levels are binding" and "Hide personal details"; whether Ask and the wiki check may read High papers (2026-10-15) | Levels enforced everywhere |
| Four Inbox papers: AEG appliances invoice (100, needs an item name), social-security notification (160), IRS receipt (168), paid 2022 IUC notice (161) (2026-10-18) | Filing; step 1 can take them as "Other papers" |
| File the papers waiting for a category as **Other papers** from the Inbox (choose "Other papers › Other paper", leave the details empty: the Hub reads them): contracts 144, 169, 170; bank letters 165–167; diploma 164; and 160, 161, 168 above. The run's server permission did not allow approving Inbox papers for you (2026-11-19) | They become searchable and readable by Ask |
| Whether the insurance booklets (CPAP/CGAP, 48–59 pages) should be read, not just filed (2026-10-18) | — |
| Re-point the gateway's `hub/classification` pairing to a cheap model (reclassify ~1 min per merchant) (2026-10-06) | Faster sorting |
| Tier for the Privatemode rows gpt-oss-120b and deepseek-ocr-2 (2026-10-15) | More High models |
| Google Calendar set-up for Health appointments (`docs/SYSADMIN.md`, "Google Calendar") | Step 7c bookings reach the calendar (7c is built without it) |
| Matias's loan: reconcile where it stands (he parked it, 2026-11-03) | — |
| Step 10g: may Ask's actions get one small new table that remembers what each answer did or proposed (so Undo and Confirm work)? (2026-11-20) | Step 10g |
| The live smoke test together: a Telegram upload, an emailed bill, approve, discard, a duplicate, a stranger's message; and a few real Ask questions (2026-10-28) | Confidence in the channels |


**Choices made for Pedro during the run (2026-11-19 → 2026-11-20; Pedro may change any of them).** Each is also in
its step; tell Claude which to change.
| Where | The choice |
|---|---|
| Step 1 | Search results are shown to every family member, with each paper's level |
| Step 2 | "Approving in the Inbox writes one rule" only when "Remember this" is ticked |
| Step 5 | A new lab test is asked about only when it looks like one already listed; a history point shows once on a person's results while each paper keeps what it printed |
| Step 6 | "Vodafone Portugal" as Amigo's name; loan instalments are not spending; the English names for areas, topics and page titles (Portuguese kept as aliases) |
| Step 7 | Health follow-up reminders 14 days before; the Portuguese and English follow-up words |
| Step 10 | Built before steps 6–8 so their pages came out in the new look; Home's earnings leave out the family's own transfers and "available" is cash minus bills due this month; the Waiting count leaves out unconfirmed merchants (their own line in the queue); the tab names Family and Dates; a person's tile shows the full name as printed; while one person is chosen, commitments and papers with no person are left out |
| Step 8 | Identity papers always High, reminded 90 days before; the holder kept as a name on the paper leading to its subject; the reminder days for vehicles, education and pets; Education under Papers, Vehicles and Pets under Things; school fees as Education papers with a To-Do rather than bills under Money; a car's and a pet's insurance stay under Insurance |

---

## 3. Parked (not scheduled)
- Share a person's Health records with a doctor (an export).
- Hard deletion of a stored file (withdrawing everything derived and hiding it is built; raw files stay immutable).
- Backups of Health data following the document and data levels (a separate discussion).
- Privatemode: speech-to-text adapter (whisper-large-v3); refresh the EUR rate on a schedule; Novita on the Mid veto
  list.
- "Hide personal details" for PDFs and photos (today text only; such calls are blocked and flagged).
- A saved Ask answer written while levels were off can keep details of a type later left out; the Inbox AI suggestion
  and the wiki's "existing pages" list at filing are not level-scoped (check against A3's fail-closed gate before
  treating as open).
- Known behaviours kept on purpose: bulk-classify totals include held lines; a bank printing only daily balances flags
  the whole broken day; statements with no balances stay "unverified".
- Check on the next real email that a signature logo lands under "Not added" and a real photo is kept.
- A web-upload retry queue; a plain message when an upload is over 100 MB, checked before it is sent.
- Tidy Health topics no paper uses any more; dates in insurance descriptions as yyyy-mm-dd.
- Link Matias's future transfers to his loan (not every transfer is a loan: a rule a person confirms, after step 2).
- Check the unexplained `matias` entry in the server's linger list.

---

## 4. Done
Details are in git history (`git log -- "Home & Family"`), ARCHITECTURE.md and DECISIONS.md.
| When | What |
|---|---|
| 2026-10-15 | Privacy levels (house_low/mid/high, fail-closed, Privatemode for High), the `/privacy` page, recognition and "What is this?", statements checked to the cent with To-check, Problems, Schedules (the old phase (d) plan) |
| 2026-10-16 → 10-21 | Health cluster live; checks and benchmark; papers say why they were made; outline, conclusions and numbers per section; written reports by their outline; topic baseline; read twice (Conditions phases 1 and 1B) |
| 2026-10-28 | Billing is not Health (bills go to Bills); every Health paper read twice, differences flagged |
| 2026-11-03 | **A1–A5** safety: nightly off-server backups and a snapshot before every migration; tests gate the deploy, pinned dependencies; privacy gate fails closed; failed presses show the three-part banner; notice bugs fixed. **C6** phone notices: bank access expired, a job failing, a bill due within 3 days, a new loan red flag; once each, one reminder, quiet at night |
| 2026-11-03 → 11-05 | **B** foundation: glossary and renames (B1), the kind carries its level, reader and filing (B2), one spending tree (B3), Coming up (B3b), registry of subjects slice 1 (B4), bank lines take their statement's level (B5), every paper has a kind (B6), one copy of each paper and no voids (B7) |
| 2026-11-19 | **Step 4 (C5)** statements against the bank feed (371d90c → f597040): a statement line the feed or another statement of the account already holds is kept but settled by its twin (dimmed, linked, counted once; checked lines untouched); a new Money kind "Credit card statement" through the normal intake (Rute's by Telegram, checked to the cent); the bank-download wrapper stripped on every channel; a line type in the reader's own words no longer refuses a statement. Re-read on the server: Santander 2024-03 (104 lines), 2025-02 (108), 2022-08 (1), all adding up; live totals unchanged (the only move was the 18:30 bank sync's five lines). Leftover D11 (document 51) in step 9 |
| 2026-11-19 | **Step 5 (C4)** shared reading blocks, Health first (39aa82a → e5680ae): one reading block (one error type, Portuguese numbers and dates, a 25 MB guard before upload); lab results matched to known tests, one line per test on a person's results page (88 live results → 65 tests; a new test is asked only when it looks like one listed: 3 asked); lab sheets get topics; one check-result shape for the statement, positions, card and Health checks, Health doubts on "Waiting for you"; "Read again" keeps a person's fields; the PDF's own layout settles headings and references; wrapper headings dropped; a finding or reference cut short completed from the second reading; a provider learnt once from the names a person confirmed. Benchmark: 0/2 before, 2/2 on two runs after (the benchmark now reads as the Hub does; `--fresh` without what it has learnt). Choices (Claude; Pedro may change): a new test is asked only when it looks like one listed; a history point shows once on a person's results while each paper keeps what it printed; the benchmark runner stays with Health until a second reader has cases; the benchmark reads with what the Hub has learnt. Leftover D12 in step 9 |
| 2026-11-20 | **Step 6 (E3, C7)** wiki facts point at subjects, and answers agree (5333737, 77499c9, 4de8954): each wiki page carries the subject it is about; a provider written another way leads to its one page and one "provider" value (Amigo's four spellings are aliases of Vodafone Portugal; 21 pages given subjects, 44 keys folded, 124 facts before and after); Home, the Overview, Ask and the budget count spending one way, and Home names what it leaves out (loan instalments, moves between own accounts); areas, topics and page titles in English with the Portuguese kept as aliases (6 areas joined, 2 conditions and 3 topics renamed). Choices (Claude; Pedro may change): "Vodafone Portugal" as Amigo's name; loan instalments are not spending; the five translations |
| 2026-11-20 | **Step 7** Health conditions, follow-ups, visits and medication (77499c9, 4de8954): a condition per person, suggested on each paper and confirmed by a person, old guessed labels proposed on Waiting for you; follow-ups become Health To-Dos 14 days before they are due and reach the phone once, proposed from a paper's words; visits, notes and medication with every change kept, one timeline per condition; Ask's conditions tool at High; the Overview's Health card never names a condition. Left: bookings to Google Calendar once Pedro sets it up (section 2). Choices (Claude; Pedro may change): reminders 14 days before; the Portuguese and English follow-up words |
| 2026-11-20 | **Step 8** new categories (2816bfc, 24a9d14; live 039ce55, deploy run 44819070261 green after two stops at the test gate): Identity & Legal (always High, renewal To-Do 90 days before expiry, told once on the phone); Contracts and Letters as kinds of Other papers; Vehicles, Education and Pets on shared paper pages, one typed reader and date reminders. Live check: /identity, /vehicles, /education, /pets, /other, /search and the money pages load; no new problem. Refile of Other papers on live: backup, dry run 0, applied 0 (no paper is filed under Other papers yet; 69 wait in the Inbox). With it went step 9 D1, D2, D4, D5, D6, D7, D10 |
| 2026-11-19 | **Step 3 (C1)** one review queue, "Waiting for you" (db786d8 →): `review_items`, the `/waiting` page with area filters, 50 a page, "Ask me in a week" (7 days), "Remember this answer" (writes a step-2 rule); one count in the menu in place of the To-check counter. On it: AI answers saved switched off, To-check lines, statements that don't add up, Needs Review's unconfirmed merchants (1,138 money items live), Health areas, people and topics, Inbox "Same person?"; one question per thing; an answer given on an item's own page closes it with that answer's who and when. Leftover D10 in step 9 |
| 2026-11-19 | **Step 2 (C2, C3)** one memory of past answers (d0f2c6a → 7be06d1): the `rules` table and one reader (a person, then the AI, then the system; then priority); provider and recognition rules moved in; confirming a merchant writes one person's rule (92 earlier confirmations given theirs on the server) and no batch pass overrides it; the merchant AI states its confidence, below 0.8 its rule is saved switched off and the merchant waits in Needs Review; a provider written two ways resolves to one merchant through a rule, and merges remember each spelling; a paper's kind is decided once (the bill-or-statement prompt after approval is gone). Leftovers D8, D9 in step 9. "Approving in the Inbox writes one rule" read as: with "Remember this" ticked (Claude, 2026-11-19; Pedro may change) |
| 2026-11-19 | **Step 1 (E2)** search over papers (83a96f2, 6f3f46b): every filed paper has its text (`paper_texts`: 191 own PDF text, 4 read by the AI, 4 with nothing readable, of 199 on file), FTS5 search with accents folded behind a box at the top of the menu and inside Ask's `find_sources` (privacy tripwires cover it), the hourly "Paper text" job, **Other papers** (catch-all, offered by the Inbox when nothing fits), unsupported or oversized files listed under "Not added". Choices recorded in git (commit 83a96f2) and ARCHITECTURE.md; "search results are shown to every family member, with each paper's level" (Claude, 2026-11-19; Pedro may change) |
| 2026-11-17 | Hardening: a deploy that cannot back up stops plainly; nightly privacy self-test; to-dos with no house area. **E1** facts say when they were true; 8 Telecom and MetLife facts fixed |
