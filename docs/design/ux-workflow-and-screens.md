# Hub UI: workflow and screens (the call)

2026-11-19. Decided with Pedro in the interview `Personal/brainstorms/2026-11-19-hub-ui-workflow-and-screens.md`
(read it for the reasoning and exact words). References: `docs/design-references/`. Mock-ups: `docs/design/mockups/`.
Mock-ups only: no Hub code was touched. Rules of structure: `~/.claude/skills/structure-project/references/architecture-principles.md`
(S1 functions, not topic boxes; S5 subjects once; S6 one review queue; W2 one formatter; Q6 what/why/what to do).

## Who and where
- The whole family logs in, children included. Everyone sees the same; nothing is hidden from anyone; every feature is
  the same for everyone (no roles, no per-person views).
- Phone for quick things (everyone); desktop to operate (mainly Pedro). So: **work screens are desktop-first, look-up
  and act screens are phone-first.**
- Children: hand something in (upload or photo), look things up, browse money and health. They do not use To-Dos.

## The workflow, in the order people meet it
1. **Ask is the front door.** Home ranks: Ask > Money > Coming up > Waiting for you (Pedro's order D > C > B > A).
2. **Ask answers and acts.** An answer is one plain sentence, then one matching visual (number, chart, table or
   document), then sources and two buttons (Open the page, Save to wiki). No suggested-question chips. Ask also takes
   a file (attach, drop, photo). Three tiers of action, the same for everyone: (1) undoable: do at once with Undo (add
   a to-do, set a reminder, upload to the Inbox as a suggestion); (2) changes what the family sees: a confirm card
   first (mark paid, approve papers, re-read a statement, remember a merchant); (3) never from Ask, point to the page
   (delete, privacy levels, bank connections).
3. **Waiting for you** (PLAN step 3) is one queue with one count, grouped by area. A paper or statement opens
   card by card (the paper on the left, what the Hub read on the right, keyboard shortcuts). Merchants open as a table
   with bulk confirm and the best guess filled in. It is worked every other day to weekly. **On the phone, a paper
   uploaded "on the run" can be approved from a light card** (file, 4 key fields, Approve / Edit / Later).
4. **Money** is one page: a headline row (spent against budget, cash balance with "matches the bank", due soon, loans),
   the spending breakdown and a trend, with tabs Spending, Bills & due, Loans, Utilities, All lines. Plus a
   **category history** view: pick a category (any level of the tree) and a number of months.
5. **Health is person-first.** Name tiles (no faces) with next follow-up and latest paper; a person page with
   Conditions, Latest results (trend with the reference range), Timeline; area and topic are filters. A strip of the
   family's follow-ups on top. Printed wording stays as printed (Portuguese); the labels are English.
6. **Things get pages; people are a filter.** A thing gathers every paper, date and reminder across categories: house and
   its items, cars, properties, pet, insurance policy, loan. People are a global "Who" chip (Everyone / Pedro / …) on
   Papers, Coming up, Waiting for you and Ask. No per-person page. ID and passport expiries show in Coming up.
7. **Behind the scenes goes behind a gear.** Settings holds Privacy, Scheduled jobs, Bank connections and a minimal
   "What the Hub knows" (the wiki: not a destination, Ask's memory). **Problems** shows as a red dot in the header only when
   something is wrong; opened, each says what, why and what to do.

## Home, Money this month (Option A, Pedro 2026-11-19)
Earnings and Spending are two blocks, each three columns (Sep, Oct, current month). A previous month's column is a
tinted block (its full month) with a solid part filling it to the same day of the month as today ("by the 19th").
The current month shows only its solid part: no budget and no forecast. The axis does not start at zero: its floor is
recomputed from the data each day so differences show (a small "scale starts at €…" note under the baseline). Under
the blocks: available balance with the bills due this month deducted. "Waiting for you": a row of four slim cards
(area, count, small button), no description of what is pending.

## Chart rules (Pedro, 2026-11-19): every chart the Hub draws, of any type, including those Ask generates on request
Types: columns, history line, trendline, horizontal bars (and any other that is asked for). One shared component and one
scale function (S4), in the mock-up generator as `make_scale`, `col_chart`, `line_chart`, `hbar_chart`
(`docs/design/mockups/_build/build_mockups.py`). Proof on many datasets and all types: `docs/design/mockups/chart-rules.html`.
1. **The axis shows its real values** (ticks with numbers on the y axis, or the x axis for horizontal bars). No "scale
   starts at" note.
2. **The floor is computed from the numbers, never set by hand, and never exaggerates small differences.** It shows the
   difference (40% of the spread below the smallest value) but is capped so a visual change is at most
   `MAX_EXAGGERATION` = 1.5 times the real change (floor at most a third of the smallest value), then lowered to a tick.
   Numbers close together therefore sit on a near-zero axis and stay honest; if the floor reaches zero the axis starts
   at zero. One constant to tune.
3. **The current month shows only what has happened so far:** no forecast, no expected value, no budget (budgets stay in
   numbers and tables). A past month shows its full month and the value by the same day of the month as today.
   - Columns: a tinted block (full month) with a solid part up to the same day; the current month is a solid part only.
   - History line: a strong line of the same-day value of each month, ending today; a pale line of the full months.
   - Trendline: a dashed straight-line fit through the strong points, drawn only over the data, never beyond it.
   - Horizontal bars: tinted track = last month in full, solid fill = this month so far.
4. **Look:** flat base, rounded ends, no outlines, no legend, faint gridlines at the ticks; pale colour for the past,
   strong for the present.
5. **Measurements over time** (dozens of lab tests): same rule from the values (no test-specific setting); the lab is
   named under each date. The reference range printed on the paper (two-sided, upper-only or lower-only) is a pale band
   behind the data (only the part inside the axis), a value outside it is red, and the range is written under the chart.

## Menu (every option)
Home · Money · Health · Things · Papers · Coming up · Waiting for you (n). Header: Ask/search (always reachable), the
Who chip, + Add a paper, the gear, the Problems dot. Phone: bottom bar Home, Money, Health, Things, More; a central
+ for adding a paper.

## My calls where Pedro did not rule (change freely)
- **To-Dos live inside Coming up** (a tab) rather than their own page: they share the same dates and nobody asked for
  them as a destination.
- **The Waiting count excludes the bulk merchant list.** 1,430 unconfirmed merchants would make the count meaningless;
  it shows its own line "1,430 merchants, best guess filled in" inside the queue.
- **Papers** is the one place to browse everything filed (category chips: Money, Health, House, Insurance, Identity,
  Contracts, Education, Other; the Who chip; search). Not drawn in the mock-ups: it is the same list pattern as All lines.
- Light theme only for now; no gradients; no avatars; hatched or striped bars are fine and drawn as patterns.
- A **pet** has no category in `docs/PLAN.md` yet (vet papers, vaccinations, insurance); it is a thing with a type.

## Screens drawn in each option (sample data throughout)
Desktop: 1 Home · 2 Ask (answer, undoable action, confirm card, attach) · 3 Money · 4 Category history · 5 Waiting for
you · 6 Paper review · 7 Merchants in bulk · 8 Health (people) · 9 Health person · 10 Thing (a car) · 11 Coming up ·
12 Settings and Problems. Phone: Home · Ask · Approve a paper.
