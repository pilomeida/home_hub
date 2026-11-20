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

**Now (2026-11-20):** deploying step 6c (answers agree, English names) with step 7 (Health conditions, visits, medication); then step 8 (new categories). Step 10 is live except 10g (Ask's answer card and actions), which waits on Pedro (section 2).

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

### Step 6. Wiki facts point at subjects, and answers agree (E3, with C7) · M
- a. Wiki pages about a person, account, policy or thing carry its subject; entity keys come from the registry
  (B4's next step).
- b. One provider written two ways becomes one subject with aliases, so its facts stop flip-flopping; the two Telecom
  pages (Amigo, Vodafone Portugal) merge, and the duplicate keys (provider/Provider, category/Category) fold together.
  ✅ a and b 2026-11-20 live (5333737, home_hub fe9d046, deploy 44692748973); `scripts/settle_wiki_subjects.py`
  applied on the live wiki after a backup (`data/backup-2026-11-20-before-wiki-subjects.db`): 21 pages given their
  subject, 4 spellings of Amigo made aliases of Vodafone Portugal, the Amigo page joined into Vodafone Portugal's,
  44 keys folded, 6 duplicate current facts made history; 124 facts before and after (none deleted), 75 → 69
  current; a second run changes nothing. Amigo/Vodafone now has one current provider, "Vodafone Portugal".
  Each wiki page carries the subject it is about (`app/services/wiki_subjects.py`): a person's, policy's or thing's
  page its subject; a topic page "Telecom — Amigo" the provider after the dash; "Pedro: check-ups" a person the
  registry already holds (a topic title never creates a person). A provider written another way leads to its subject
  through its aliases, so it reuses the provider's page and its "provider" fact is written as the provider's name;
  fact keys have one spelling ("Provider" is "provider"). Ask's wiki index says whom a page is about, and reading a
  page lists the other pages about the same subject. Joining two subjects carries their pages. (Claude, 2026-11-20;
  Pedro may change: the name kept for Amigo is "Vodafone Portugal", with Amigo, Amigo (Vodafone), Amigo (Vodafone
  Portugal) and Vodafone as its other spellings.)
- c. (C7) The same spending question gives the same number on the Overview, Ask and the budget; the wiki's names are
  per category; the orphan check is fixed while links are rare. Language (Pedro, 2026-11-19): areas, topics and
  wiki guidance in English; each paper's own summary and wording stay in its language, shown as printed. Existing
  Portuguese area and topic names get English names, the Portuguese kept as an alias.
  ◐ c 2026-11-20, built and tested, deploying; then `scripts/english_names.py` on the live data. Home's and the
  Overview's Earnings and Spending now use the one rule the budget and Ask use (`spend_of`, new `earning_of`): live
  2026-11 so far, Spending was €7,384 on Home and €4,461 in the budget and Ask; the difference was loan instalments
  (€2,178) and moves between the family's own accounts (€600), and Earnings counted €2,312 of moves between own
  accounts since 2026-08. Home names what is not counted under Spending ("Not counted as spending this month: loan
  instalments €…, moves between your own accounts €…"). The wiki's names are per category (the same title in
  another category is another page); the orphan check counts a page about a subject as connected (all 21 live pages
  were flagged while no page had a link). Language: the readers name new areas, conditions, page titles and fact
  names in English, the paper's own summary and values as printed; the six Portuguese areas join their English
  twins, the two conditions get English names, the three Portuguese topic pages are renamed; each Portuguese name
  stays an alias that still leads there. (Claude, 2026-11-20; Pedro may change: loan instalments are not spending
  (they are named under it); the translations "Ischaemic stroke", "Atheromatous plaque", "Pedro: check-ups and
  tests", "Pedro: conditions and diagnoses", "Vicente: eyes".)
- Done when: Rute's facts are found from her subject, whichever page holds them; Amigo's provider fact keeps one value
  across bills.

### Step 7. Health conditions, visits and medication · L (four slices)
Design and data model: `docs/superpowers/plans/2026-10-19-conditions-master-plan.md` (its phases 1 and 1B are done).
The Hub only proposes; a person confirms. A condition is a subject (step 6).
- a. Conditions: `health_conditions`, `health_entry_conditions`; a condition page; a suggestion on each paper's box
  (confirm, pick another, none); "propose a new condition"; a one-time review of filed papers and the old guessed
  labels (through step 3).
- b. Follow-ups and reminders: `health_follow_ups`; a "next due" line; Health to-dos, overdue in red; a proposal from
  report text ("repetir em 12 meses"); due follow-ups reach the phone through the notices (C6).
- c. Visits, notes, medication: booked and done visits, notes (note, own reading, episode), medication and its
  changes; one timeline per condition.
- d. Ask and the Overview: Health Ask tools for conditions, topics, visits and medication (through
  `app/privacy/reach.py`, tier High, tripwire tests); an Overview card.
- Done when: a condition page shows its papers, visits, medication and next follow-up, and its reminder reaches the
  phone once.
  ✅ a–d 2026-11-20, built and tested, deploying with 6c (the first deploy, 77499c9, stopped at its test gate: the
  privacy tripwire's full Ask turn did not yet call the new conditions tool; fixed in the next commit) (`app/categories/health/conditions.py`, `follow_ups.py`,
  `care.py`; three new tables in three migrations; `/health/conditions/{id}`). a: a condition is a record per person
  and a subject ("Ischaemic stroke — Pedro Almeida"); each paper's box suggests the person's conditions named alike
  (confirm, pick another, none of these; a re-read keeps the answer); the old guessed condition labels are proposals
  on the person's page and on Waiting for you ("A condition for Pedro Almeida? …"), created only on a yes, with their
  papers attached; a paper deleted takes its links with it. b: a follow-up is a Health To-Do 14 days before it is due
  (titled with what and for whom, never the condition), "Next due" on the condition page, overdue in red; a paper's
  words ("repetir RM em 12 meses", "controlo dentro de 6 meses", "review in one year") propose one dated from the
  paper, never twice and never after a no; it reaches the phone once ("A Health follow-up for Pedro is due on
  2026-12-01: see To-Dos."). c: visits booked then done (done books the next), notes (note, own reading with its
  value, episode), medication whose changes are added, never overwritten; one timeline per condition, newest first.
  d: Ask's `health_conditions` tool (tier High, fail-closed, in the tripwire test); the Overview's Health card says
  "N conditions followed" and "next follow-up yyyy-mm-dd" or "N overdue", never a name. Left: bookings reach Google
  Calendar once Pedro sets it up (section 2). (Claude, 2026-11-20; Pedro may change: reminders 14 days before; the
  follow-up words read are Portuguese and English "repeat / control / review in N weeks, months or years".)

### Step 8. New categories · M each
Reuse: sysadminsmedia/homebox's "a thing with typed fields and attachments" (AGPL: idea only).
- a. Identity & Legal: ID card, passport, driving licence, residence papers, wills; holder (a subject), number,
  expiry; a reminder before expiry; suggested High.
- b. Contracts and letters: contracts (telecom, service agreement, NDA) and bank letters (the Handelsbanken FATCA/CRS
  letters) move out of "Other papers" into their own kinds, each with a level on the Privacy page.
- c. Vehicles: the car as a subject; insurance, inspection (IPO), road tax (IUC), service; reminders.
- d. Education: school papers per child, diplomas; dates and fees into Coming up.
- e. Pet (Pedro, 2026-11-19): a pet as a thing with a type, like the car: vet papers, vaccinations, insurance; reminders.
  Built with step 10's components.
- Done when: a passport is uploaded, read, and its expiry reminder appears on To-Dos and reaches the phone once.

### Step 9. What clean-up is left · S–M each
Whatever the steps above did not already touch.
| ID | What |
|---|---|
| D1 | Retire dead tables and columns (`wiki_changes`, `doc_type`, `health_entries.amount_*`, legacy Health kinds); the statement reader's unread `category_hint` (paid output) and the bill reader's own 15-word utility list |
| D2 | Router logic into services (transactions, loans, health); no router imports a router; spending-tree slugs out of services |
| D3 | No swallowed or logged-only failure in the intake paths; no raw exception or bank error on screen; the last field-level hints in `app/categories/fields.py` and the import script's `--dry-run` line follow the messages rule |
| D4 | One money filter and one date filter in templates; one three-part message macro |
| D5 | The mailbox check is never stuck behind bank sync or the wiki check |
| D6 | ARCHITECTURE.md, SYSADMIN.md and `.env.example` up to date |
| D7 | The test database enforces foreign keys everywhere, not only in `fk_session` (how the Health delete-order bug passed) |
| D8 | (from step 2, 2026-11-19) The payment entities and the supermarket list move into `rules` as Pedro's person rules, with a merchant-key column so the merchants they made keep their keys; they already rank right, so nothing a family member sees changes |
| D10 | (from step 3b, 2026-11-19) Health's outline confirmation onto "Waiting for you": it is a per-paper button today, with no list of papers waiting for it to adopt |
| D11 | (from step 4c, 2026-11-19) Revolut before 2024-01: document 51 (2022-08 → 2026-08, 1.3 MB) is refused whole by the AI provider ("cannot succeed by retrying"). Read large statements in page chunks and keep only the lines before 2024-01 (later months are in the feed and the monthly statements); compare live totals before and after |
| D12 | (from step 5d, 2026-11-19) Health reading, what is left of "first time right": narrative findings as a structure of their own (today a long text in a result); Pedro's field corrections kept per provider (the provider's name is learnt; other fields are not yet); grey bands on lab sheets (pypdf gives no shading; needs another reader); 5e grows the benchmark as Pedro sends papers |
| D9 | (from step 2c, 2026-11-19) AI calls in batches with an index per item: the bank sync's new-merchant pass sends one call per merchant today |

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
| 2026-11-19 | **Step 3 (C1)** one review queue, "Waiting for you" (db786d8 →): `review_items`, the `/waiting` page with area filters, 50 a page, "Ask me in a week" (7 days), "Remember this answer" (writes a step-2 rule); one count in the menu in place of the To-check counter. On it: AI answers saved switched off, To-check lines, statements that don't add up, Needs Review's unconfirmed merchants (1,138 money items live), Health areas, people and topics, Inbox "Same person?"; one question per thing; an answer given on an item's own page closes it with that answer's who and when. Leftover D10 in step 9 |
| 2026-11-19 | **Step 2 (C2, C3)** one memory of past answers (d0f2c6a → 7be06d1): the `rules` table and one reader (a person, then the AI, then the system; then priority); provider and recognition rules moved in; confirming a merchant writes one person's rule (92 earlier confirmations given theirs on the server) and no batch pass overrides it; the merchant AI states its confidence, below 0.8 its rule is saved switched off and the merchant waits in Needs Review; a provider written two ways resolves to one merchant through a rule, and merges remember each spelling; a paper's kind is decided once (the bill-or-statement prompt after approval is gone). Leftovers D8, D9 in step 9. "Approving in the Inbox writes one rule" read as: with "Remember this" ticked (Claude, 2026-11-19; Pedro may change) |
| 2026-11-19 | **Step 1 (E2)** search over papers (83a96f2, 6f3f46b): every filed paper has its text (`paper_texts`: 191 own PDF text, 4 read by the AI, 4 with nothing readable, of 199 on file), FTS5 search with accents folded behind a box at the top of the menu and inside Ask's `find_sources` (privacy tripwires cover it), the hourly "Paper text" job, **Other papers** (catch-all, offered by the Inbox when nothing fits), unsupported or oversized files listed under "Not added". Choices recorded in git (commit 83a96f2) and ARCHITECTURE.md; "search results are shown to every family member, with each paper's level" (Claude, 2026-11-19; Pedro may change) |
| 2026-11-17 | Hardening: a deploy that cannot back up stops plainly; nightly privacy self-test; to-dos with no house area. **E1** facts say when they were true; 8 Telecom and MetLife facts fixed |
