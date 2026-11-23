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

**Now (2026-11-23):** step 12 (the Inbox sorts every paper by what it is: house or people, items and rooms, people tags, sensible names, caption first) is designed (2026-11-23); slice 12a (foundation) landed 2026-11-23; slice 12b landed 2026-11-23 and the 69 waiting papers carry the new guess (the guess: caption first; area, kind or a new one, who, items with rooms, tags, date to act on, filed name; waiting papers guessed again); next is 12c, the Inbox card. Steps 1 to 11 are done and live (section 4). Step 9 D12 is parked until Pedro uploads new Health papers (section 3). What else waits on Pedro is in section 2.

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

### Step 12: the Inbox sorts every paper by what it is (Pedro, 2026-11-23)
Pedro's Inbox screenshots, 2026-11-23: the AEG invoice came in with item and room blank; the Católica diploma got
"Not sure where this belongs" although the Hub had seen it was a diploma; every paper keeps the name Telegram gave it.
What he asked for (paraphrase agreed 2026-11-23):
- **House or people first.** The Inbox decides whether a paper is about the house (an appliance, a room, a repair) or
  about a person (a diploma, an ID, a medical letter), instead of only trying Money or House.
- **House papers:** read out each item bought (an invoice can list several), take the purchase date (the warranty runs
  from it) and suggest a room for each item, for Pedro to approve. Room becomes a filter later.
- **People papers:** tag them by what they are: area (e.g. Education), kind of paper (diploma or certificate), level
  or degree, grade if there is one, and the person. If no existing category fits, suggest one, clearly marked as new.
- **A sensible name.** Every filed paper gets a name that says what it is (e.g. "2025-12 Certificado conclusão gestão
  TIC — Católica — Pedro"), not the name Telegram or the email gave it.
- **The caption or subject first.** A Telegram caption or email subject is the strongest clue for kind, person, tags
  and name; the paper's text comes after.
- Pedro still approves everything in the Inbox; the Hub only fills in its guesses.

Designed with Pedro 2026-11-23 (grill-me; notes in `Personal/brainstorms/2026-11-23-hub-inbox-sorts-by-what-it-is.md`):
- Papers go straight to an area; no separate house-or-people question. "Who" only on papers about a person, from one
  family list (Pedro, Rute, Matias, Vicente, + added; unknown = "New person") (S5).
- Rooms from one fixed list: Kitchen, Matias' bedroom, Vicente's bedroom, Suite bedroom, Social restroom, 1st floor
  restroom, Suite restroom, Solarium, Living/dining room, Store room (annex), Outside restroom, Garden, Parking, Office,
  Hall, Porch, Whole house. Unknown room = "New room", added on approval (S5).
- A house paper listing several things: one editable row per physical item (name, room, warranty end = purchase date
  + 3-year legal guarantee unless the paper says otherwise); fees are not items; each item gets its own card with the
  paper attached.
- Tagging: a small fixed core (who, area, kind, room, a date to act on) + free tags + the paper's text read by the AI;
  no per-area forms (S4).
- Every paper has a type; no "Other". Nothing fits = a new kind or new area suggested, marked new, created on approval
  even for one paper; a new area is a foundation entry with a derived page, not new code (S2/S3).
- Filed name: `date · kind · what it is · who or item` (e.g. `2025-12-15 · Certificate · Gestão TIC, Católica ·
  Pedro`) everywhere, downloads included (clarified 2026-11-23); the file's content is kept as received (Q4), the
  original name only as a "where it came from" line. No accents in names, as a general rule: ç → c, ã → a, every
  accent dropped (Pedro, 2026-11-23). Papers already received are renamed too (Pedro, 2026-11-23).

Slices (Claude, 2026-11-23):
- **12a Foundation:** the family list and the room list defined once; areas and kinds can be added from an approval;
  the "Other" area retired, its papers re-sorted into real types (W1: backup, dry run, apply, verify). ✅ 2026-11-23
  (full suite 2,970 green; deploy run 45252742638 failed: "re-read now" depended on the machine's clock, fixed with a
  flag and a test; live 2026-11-23, deploy run 45254678146, commits 6db54b5 and
  76c2b2a; `scripts/seed_household.py` applied on the server after a named backup: Pedro (#7) and Rute (#8) marked as
  family, Matias and Vicente added, 17 rooms added; a second run changed nothing; health 200, no errors, problems
  still 3). How (Claude, 2026-11-23):
  - An area is an open value, no longer a fixed list in code: the nine built-in areas stay as they are, an area Pedro
    approves is a row in a new `paper_types` table (one row per added area or added kind). Stored the same way as
    today, so no paper changes (S2).
  - The registry merges the built-in areas with those rows. Every process (web, Telegram bot, both schedulers)
    notices a new row within seconds (S2/S3).
  - An added kind or area is read by the generic paper reader (Other's, moved: title, date, who or what it is about,
    summary) with the generic fields; an added area gets its pages at `/areas/<name>` from the shared paper pages;
    its kinds appear on the Privacy page, suggested Mid (S3/S4, D16 unchanged).
  - Inbox: "+ New kind of paper" under every area and "+ New area" in the area list; approving creates it, then files
    the paper. 12c adds the Hub's own suggestion with its "New" badge.
  - "Other" retired: the live Hub has 0 papers, records, to-dos or wiki pages in it (checked 2026-11-23), so nothing
    to re-sort; its area, pages and nav link go, old `/other` links lead to All papers.
  - The family and the rooms are subjects (S5): a household flag marks Pedro, Rute, Matias and Vicente; rooms are a
    new subject kind with the 17 rooms; both seeded by a migration. House's room field suggests from the room list,
    and filing an item in a room not on it adds that room.
- **12b The guess:** caption or subject first, then the paper; it returns area and kind (or a new one, marked new),
  who, items with rooms, free tags, a date to act on and the filed name. Papers already waiting in the Inbox are
  guessed again (their September guesses predate step 8). ✅ 2026-11-23 (dea8cac, fix 81c3a25, deploy 45278065682; full suite 2985 passed; backup `data/backups/before-12b-2026-11-23.db`). First 3 re-guessed live: the diploma (#164) is now Education › Diploma, who Pedro, dated 2002-12-06; the AEG invoice (#100) has its 6 items, all Kitchen; a Seguranca Social letter (#160) got a new area "Social benefits", marked new. Found: the AI's "what it is" ran to a sentence (amounts, numbers) and the name was cut mid-word; fixed the same day (prompt asks at most 8 words; the formatter keeps 12 words and cuts at a word; deploy 45280170671). Then all 69 waiting papers re-guessed, none failed, no type created (Pedro approves new ones in 12c): 45 Brave Generation Academy invoices now Education › School fee for Matias, 9 bills, 5 policies, the diploma, the AEG invoice, and 7 new kinds or areas offered (Work, Telecom, Benefits; two Handelsbanken FATCA/CRS kinds). Problems unchanged (3 open, none new). How (Claude, 2026-11-23):
  - Still ONE AI call per paper, inside the High run as today (D20), its answer checked by code before it is kept
    (Q1: AI output is untrusted): an area or kind must exist or be offered as new (never confident: Pedro looks at
    every new type); "who" matched to the family list, else kept as a name; rooms matched to the room list, else
    marked new; dates must be real dates; at most 20 items and 10 tags.
  - The prompt names the family and the rooms (from the subjects, S5) and says the caption or subject is the
    strongest clue.
  - The whole guess is kept on the Inbox item (a new `guess_json` column) for 12c to show and 12d to file; the
    card's "Best guess" line already names a new area or kind, marked new.
  - The filed name is built by code, one formatter (W2): `date · kind · what it is · who or item`, no accents, no
    characters a file name cannot hold; the date is the paper's own, else the day it arrived.
  - Guessing again goes through the same path as a new arrival (rules first, then the AI at High):
    `scripts/guess_waiting_again.py`, dry run first, then in small batches (W1); cents in total.
- **12c The Inbox card:** item rows (edit, remove, add); "New" badges on a new kind, area, room or person; tags shown
  and editable; approving creates what is new.
- **12d Filing and finding:** approval files under the new name, one card per item, tags stored; House filters by room,
  every people page by who; checked live on the AEG invoice and the Católica diploma (guess only; Pedro approves).
- **12e Rename what is already in:** the 273 papers on the live Hub (2026-11-23: 184 Money, 70 waiting in the Inbox,
  16 Insurance, 3 Health) get names by the same rule. A name is built from what the Hub already knows of the paper;
  the AI reads it only where that is not enough, through the privacy gate (D16). W1: backup, a dry run whose list of
  old → new names is read (a sample shown to Pedro), apply, verify, a second run changes nothing. The 70 Inbox papers
  get theirs from 12b's new guess.

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


**Choices made for Pedro during the run (2026-11-19 → 2026-11-20; Pedro may change any of them).** Each is also in
its step; tell Claude which to change.
| Where | The choice |
|---|---|
| Step 1 | Search results are shown to every family member, with each paper's level |
| Step 2 | "Approving in the Inbox writes one rule" only when "Remember this" is ticked |
| Step 5 | A new lab test is asked about only when it looks like one already listed; a history point shows once on a person's results while each paper keeps what it printed |
| Step 6 | "Vodafone Portugal" as Amigo's name; loan instalments are not spending; the English names for areas, topics and page titles (Portuguese kept as aliases) |
| Step 7 | Health follow-up reminders 14 days before; the Portuguese and English follow-up words |
| Step 10 | Built before steps 6–8 so their pages came out in the new look; Home's earnings leave out the family's own transfers and "available" is cash minus bills due this month; the Waiting count leaves out unconfirmed merchants (their own line in the queue); the tab names Family and Dates; a person's tile shows the full name as printed; while one person is chosen, commitments and papers with no person are left out; 10g: Ask's actions are a to-do (Undo), and after a confirm card: mark a bill paid, approve the Inbox papers the Hub is sure of, give a merchant its category (no re-reading a statement: there is no "read again" for statements); a file with no question asks the AI nothing; Ask never reads an unfiled paper |
| Step 8 | Identity papers always High, reminded 90 days before; the holder kept as a name on the paper leading to its subject; the reminder days for vehicles, education and pets; Education under Papers, Vehicles and Pets under Things; school fees as Education papers with a To-Do rather than bills under Money; a car's and a pet's insurance stay under Insurance |

---

## 3. Parked (not scheduled)
- Step 9 D12, Health reading "first time right" (parked by Pedro 2026-11-21 until he can upload new Health
  documents): built against 3–5 Health papers from other providers and his first corrections of a field the Hub got
  wrong. Its parts: findings written as prose kept as their own records; a person's corrections remembered per
  provider; shaded bands on lab sheets (needs a second PDF reader and a sheet that has them); the benchmark grows with
  each paper.
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
| 2026-11-20 | **Step 9** clean-up (7187d0c, b4e7260, bfbf9e5, 04e777c, db4797a, 6639a7e, a178e3a, ad3158e, a37bfc9; all live by c77005f): dead tables and columns retired (D1); router logic into services, no router imports a router, tree slugs named once (D2); no swallowed failure in the intake paths (D3); one money, date and message format (D4); long jobs in their own scheduler lane (D5); docs up to date (D6); links checked in the test database (D7); payee numbers, payment texts and supermarkets as Pedro's rules, 4,041 texts unchanged (D8); new merchants asked of the AI together (D9); Health outlines on Waiting for you (D10); Revolut 2022-08 → 2023-12 read in parts and filed, 584 lines, totals unchanged (D11). D12 moved to section 2: it waits on Pedro's papers |
| 2026-11-23 | **Step 11** desktop UI rework (Pedro's screenshots, 2026-11-23; 7c1ab11, cafec77, f994a69, af77579): 11a type grows with the screen (15px → 20px) and one 96rem content width shared by header, section row and page, the section's pages beside search; 11b plain elements take Home's look, older page styles on the tokens (rem, sentence case, light-only), one orange per form, the listed pages restyled, `docs/STYLE.md` written and referenced from CLAUDE.md; 11c every page about one paper shows what was read beside a small first-page preview (one include, drawn on the server, kept in `data/previews/`), opening on Full view or Side by side. Choices (Claude): the sizes and the 96rem width; Full view first; no Side by side under 900px. Pedro saw it on his screen and approved the look, 2026-11-23 |
| 2026-11-21 | **Step 10** UI redesign, Option A (slices a–i; 10a–10f, 10h, 10i live 2026-11-19 → 11-20, see git; 10g live 2026-11-21: d91a0b9, 060f73f): one stylesheet and one chart component on Pedro's scale rule; the menu, header and phone bar; Home; Money with category history; Waiting for you card by card and merchants in bulk; Health person-first, Papers, Coming up, the gear; "Add a paper" and the light approve card; the Who chip; Ask's answer card (one sentence, one visual from the cited data, sources, Open the page / Save to wiki), actions in three tiers (a to-do with Undo; mark a bill paid, approve sure Inbox papers, give a merchant its category after Do it; never delete, privacy or bank connections) kept in `ask_actions`, and a file attached to a question going to the Inbox as a suggestion. The full text of the step is in git history (PLAN.md before this commit) |
| 2026-11-19 | **Step 3 (C1)** one review queue, "Waiting for you" (db786d8 →): `review_items`, the `/waiting` page with area filters, 50 a page, "Ask me in a week" (7 days), "Remember this answer" (writes a step-2 rule); one count in the menu in place of the To-check counter. On it: AI answers saved switched off, To-check lines, statements that don't add up, Needs Review's unconfirmed merchants (1,138 money items live), Health areas, people and topics, Inbox "Same person?"; one question per thing; an answer given on an item's own page closes it with that answer's who and when. Leftover D10 in step 9 |
| 2026-11-19 | **Step 2 (C2, C3)** one memory of past answers (d0f2c6a → 7be06d1): the `rules` table and one reader (a person, then the AI, then the system; then priority); provider and recognition rules moved in; confirming a merchant writes one person's rule (92 earlier confirmations given theirs on the server) and no batch pass overrides it; the merchant AI states its confidence, below 0.8 its rule is saved switched off and the merchant waits in Needs Review; a provider written two ways resolves to one merchant through a rule, and merges remember each spelling; a paper's kind is decided once (the bill-or-statement prompt after approval is gone). Leftovers D8, D9 in step 9. "Approving in the Inbox writes one rule" read as: with "Remember this" ticked (Claude, 2026-11-19; Pedro may change) |
| 2026-11-19 | **Step 1 (E2)** search over papers (83a96f2, 6f3f46b): every filed paper has its text (`paper_texts`: 191 own PDF text, 4 read by the AI, 4 with nothing readable, of 199 on file), FTS5 search with accents folded behind a box at the top of the menu and inside Ask's `find_sources` (privacy tripwires cover it), the hourly "Paper text" job, **Other papers** (catch-all, offered by the Inbox when nothing fits), unsupported or oversized files listed under "Not added". Choices recorded in git (commit 83a96f2) and ARCHITECTURE.md; "search results are shown to every family member, with each paper's level" (Claude, 2026-11-19; Pedro may change) |
| 2026-11-17 | Hardening: a deploy that cannot back up stops plainly; nightly privacy self-test; to-dos with no house area. **E1** facts say when they were true; 8 Telecom and MetLife facts fixed |
