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

**Now (2026-11-19):** step 2e (one merchant for a provider written two ways) deploying; next: d (a paper's kind decided once).

**How this file is kept (Pedro, 2026-11-19).** This is the live document that rules execution, updated as work
happens, not afterwards: a new task is written in before it starts; a change to a task is edited into its entry,
dated; a slice that starts sets "Now"; a slice that lands is marked in its step (✅ date and commit, or ◐ with what is
left), and a finished step moves to section 4. Every commit touching Hub code carries this file (a hook refuses
one that does not; "PLAN-EXEMPT: <reason>" only for work no step covers). Rules in `CLAUDE.md`.

**Running it without Pedro (2026-11-19).** `/goal execute PLAN.md` runs section 1's steps in order, 1 → 9, slice
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
  would be overwritten.
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

### Step 2. One memory of past answers (C2, with C3) · L
Reuse: maciejb2k/open-banking-rails `merchant_rule.rb`, `merchant_suggester.rb` (MIT).
- a. A `rules` table: what it looks at (description, counterparty, IBAN, Multibanco entity, sender, words), how
  (contains, exact, regex, prefix, IBAN), the pattern, the answer (merchant, spending leaf, kind of paper, subject),
  priority, **source (person, AI, system)**, confidence, on or off, approved by and when.
- b. One reader, one order: person, then AI, then system; then priority. Every classifier and batch re-file asks it
  first. Today's provider rules, payment entities, recognition fingerprints and code-held keyword lists move into it
  (a migration, nothing lost).
- c. An AI suggestion below the confidence bar is saved switched off and waits for a person (step 3). AI calls go in
  batches with an index per item.
- d. (C3) The kind of a paper is decided once: the hidden second bill-or-statement decision after approval goes; hand
  uploads go through the same recognition.
- e. A provider written two ways ("amigo (Vodafone)", "amigo (Vodafone Portugal)") resolves through a rule to one
  merchant instead of making a new one each time.
- f. Optional, not part of an autonomous run (a new adapter in the gateway repository and a new paid service):
  benchmark jev (typed answers with probabilities, on OpenRouter) against the current pairing
  on two or three narrow decisions (kind of paper, spending leaf, which bank line paid a bill): accuracy, EUR, speed;
  keep, try or drop. It would be a new llmsel adapter, Low and Mid only, deciding on facts already read (less
  accurate above ~32 KB), and it gives no reasons, so the "why" comes from the Hub's facts. API still alpha.
- Done when: confirming a merchant or approving in the Inbox writes one rule, and no batch pass overrides a person
  (tested).
- ◐ 2026-11-19 (Claude): a, and b for two of the stores. The `rules` table and its one reader (`app/services/rules.py`:
  person, then AI, then system; then priority; switched-off rules answer nothing) are in. The provider rules (filing
  by bank text) and the recognition rules (sender, words, file name → kind of paper) moved into it by migration
  d7f2b4a8c615 (the old tables stay, no longer read); the Privacy page and the Inbox "remember" write person rules.
  ◐ 2026-11-19 (Claude): confirming a merchant writes one person's rule (counterparty → spending leaf; money-in
  defaults stay on the merchant); a new bank line takes a person's rule (bank text, then counterparty) before the
  merchant's stored default, and the merchant consistency pass cannot move it back (tested). The supermarket list is
  Pedro's own ruling (2026-10-07), so it keeps its place above a confirmed merchant and moves in as a person's rule,
  not a system one. "Approving in the Inbox writes one rule" is read as: with "Remember this" ticked, one rule per
  thing remembered, as today (Claude, 2026-11-19; Pedro may change). On the server: 92 of the 105 merchants
  confirmed before rules existed got their person's rule (13 skipped: money in or no real category; only `rules`
  written; `scripts/rules_from_confirmed_merchants.py`).
  ◐ c, 2026-11-19 (Claude): the merchant AI now states how sure it is; a new merchant's AI answer is saved as an AI
  rule, switched off below 0.8, and then the merchant waits in Needs Review instead of being filed on a guess; an
  answer with no stated confidence keeps today's behaviour. Left: AI calls in batches with an index per item.
  ✅ e, 2026-11-19 (Claude): before a new merchant is made, the engine asks the rules whether the name is another
  spelling of a known counterparty; merging merchants writes each merged spelling as a person's rule (tested with
  "Amigo (Vodafone Portugal)" → "Amigo (Vodafone)"). The live Amigo / Amigo EasyPay / Vodafone Portugal merchants
  are left as they are: their merge is step 6b's subject merge.
  Left in step 2: payment entities and the supermarket list into the table; the rest of c; d.

### Step 3. One review queue: "Waiting for you" (C1) · L
Reuse: open-banking-rails' "a switched-off rule waits for review" (MIT).
- a. A `review_items` table: subject, why, evidence and its page, the options, "remember this answer" (writes a step-2
  rule), status (waiting, approved, discarded, snoozed), who and when.
- b. Move the review points onto it one by one: Needs Review (1,430 merchants unconfirmed on 2026-10-28), To-check
  lines, the Health queues (areas, people, topics, outline confirmation), Inbox "Same person?", step 2's waiting
  rules.
- c. One "Waiting for you" count in the menu, replacing the separate counters; the page filters by area (Money,
  Health, Inbox…) (Pedro, 2026-11-19: one count, filters inside).
- Done when: one count, and every decision stamps who and when.

### Step 4. Statements against the bank feed, then the missing history (C5) · M
Reuse: actualbudget/actual `server/accounts/sync.ts` `reconcileTransactions` (MIT, the idea ported).
- a. Match a statement line to a feed line: by the bank's id first, then same amount and nearest date (a few days); on
  a tie prefer the line with no bank id; never change a line a person has checked.
- b. Overlapping periods show as overlap, never doubled. The Loans upload and card statements go through the same
  intake (scope, duplicates, Problems); the PDF wrapper is stripped on every channel.
- c. Re-read through it: Santander 2024-03 (no lines) and 2025-02 (one line), Revolut before 2024-01, and the four
  older statements that need attention (documents 2, 22, 33, 51: two invalid line types, an old API error, an empty
  credit balance). Before the 2025-10-10 baseline, so history and spending analysis only.
- Done when: a statement for a feed period adds no double lines (tested on a real overlap), and Rute's card statement
  can be sent by Telegram.

### Step 5. Shared reading blocks, Health first (C4, with Health "first time right") · L
Reuse: giovi321/asclepius `normalization/resolver.py`, `prompts_data/extraction_lab_test.yaml` (MIT, young project:
adapt and test well).
- a. One reader interface: text from step 1, Portuguese numbers and dates (`1.234,56`, `dd/mm/aaaa`), a size guard,
  one error type.
- b. Lab values: the reader returns the paper's own wording, value, unit and range; code matches the wording to a
  known test (exact alias, then near match, else a new test sent to the step-3 queue). Lab-sheet headings get the
  topic baseline written reports already have.
- c. One check-result shape (verdict, doubtful items) for the statement add-up, positions, card and Health checks,
  flowing into step 3. Read-twice and the benchmark move into the shared block.
- d. Health reading, from the agreed "first time right" list: read the PDF's structure with code (positions, fonts,
  grey bands) so the model only interprets; learn each provider once (a profile Pedro confirms, his corrections kept
  for that provider); narrative findings get a structure of their own instead of long text in a result; "Read again"
  keeps fields a person edited; a history point printed on several papers is stored once.
- e. The benchmark grows with each provider Pedro supplies (see section 2); every reader change is run through
  `scripts/health_benchmark.py run` before and after.
- Done when: the same blood test from two labs lands on one line of Rute's results; every check returns the same shape;
  the benchmark passes on every case it holds.

### Step 6. Wiki facts point at subjects, and answers agree (E3, with C7) · M
- a. Wiki pages about a person, account, policy or thing carry its subject; entity keys come from the registry
  (B4's next step).
- b. One provider written two ways becomes one subject with aliases, so its facts stop flip-flopping; the two Telecom
  pages (Amigo, Vodafone Portugal) merge, and the duplicate keys (provider/Provider, category/Category) fold together.
- c. (C7) The same spending question gives the same number on the Overview, Ask and the budget; the wiki's names are
  per category; the orphan check is fixed while links are rare. Language (Pedro, 2026-11-19): areas, topics and
  wiki guidance in English; each paper's own summary and wording stay in its language, shown as printed. Existing
  Portuguese area and topic names get English names, the Portuguese kept as an alias.
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

### Step 8. New categories · M each
Reuse: sysadminsmedia/homebox's "a thing with typed fields and attachments" (AGPL: idea only).
- a. Identity & Legal: ID card, passport, driving licence, residence papers, wills; holder (a subject), number,
  expiry; a reminder before expiry; suggested High.
- b. Contracts and letters: contracts (telecom, service agreement, NDA) and bank letters (the Handelsbanken FATCA/CRS
  letters) move out of "Other papers" into their own kinds, each with a level on the Privacy page.
- c. Vehicles: the car as a subject; insurance, inspection (IPO), road tax (IUC), service; reminders.
- d. Education: school papers per child, diplomas; dates and fees into Coming up.
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
- **UI redesign, mock-ups only (Pedro, 2026-11-19).** The Hub's look is not attractive. Pedro sent 13 reference
  dashboards; plan: save them under `docs/design-references/`, decide the screens and workflow from how the family
  uses the Hub (interview in `Personal/brainstorms/2026-11-19-hub-ui-workflow-and-screens.md`), then draw 3 wireframe
  options, each with coherent screens. No code until Pedro picks one; then it becomes a numbered step. Not part of
  the autonomous run.

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
| 2026-11-19 | **Step 1 (E2)** search over papers (83a96f2, 6f3f46b): every filed paper has its text (`paper_texts`: 191 own PDF text, 4 read by the AI, 4 with nothing readable, of 199 on file), FTS5 search with accents folded behind a box at the top of the menu and inside Ask's `find_sources` (privacy tripwires cover it), the hourly "Paper text" job, **Other papers** (catch-all, offered by the Inbox when nothing fits), unsupported or oversized files listed under "Not added". Choices recorded in git (commit 83a96f2) and ARCHITECTURE.md; "search results are shown to every family member, with each paper's level" (Claude, 2026-11-19; Pedro may change) |
| 2026-11-17 | Hardening: a deploy that cannot back up stops plainly; nightly privacy self-test; to-dos with no house area. **E1** facts say when they were true; 8 Telecom and MetLife facts fixed |
