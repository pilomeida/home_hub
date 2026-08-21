# Financial OS Requirements — v2

**Date:** 2026-08-21
**Status:** Design validated via mockup iteration; not yet planned or built
**Supersedes:** `~/Downloads/Financial OS Requirements, v1.odt` (original recommendations) plus a follow-up UX/product-research note (Copilot, Monarch, Lunch Money, YNAB, Origin, Ledgerly, FinMin)
**Scope:** The next major evolution of the Home & Family Hub — from a bills/bank-statement tracker into a full personal financial operating system. Architectural in scope; too large for one implementation plan (see "Decomposition" below).

This document captures the design as it stands after both source documents and an extensive mockup-driven brainstorm of the Overview screen. It is the reference point for writing the implementation plan(s) that will actually build this.

## What changed from v1

The v1 document's own recommendation (§6, §11) was to **normalize annual/irregular costs into a monthly-equivalent figure** (e.g., "IRS €3,600/yr → €300/month," folded into a blended "sustainable monthly cost" metric). That normalization is explicitly **rejected** here.

Instead: a distinct **Yearly Commitments** concept — a planned estimate for the year, tracked against actual spend as it accrues, never silently averaged into a monthly number. This covers not just formal periodic obligations (IMI, IRS, insurance renewals) but also large, roughly-annual discretionary spend like **vacation** — as opposed to smaller, more-frequent discretionary spend like **weekend breaks**, which stays ordinary Travel-category spend, not a yearly commitment. The distinguishing question is cadence/scale ("does this happen ~once a year and deserve its own budget line?"), not merely "is this a bill."

Everything else in v1 (§1–§30) stands as the architectural foundation, refined below where the mockup work sharpened or extended it.

## Decomposition (unchanged in spirit from v1 §28–29, restated)

This is too large for one plan. Suggested build order, each its own spec → plan → subagent-driven-development cycle:

1. **Data model foundation** — Accounts, `Commitment` entity (recurring + yearly, with the frequency/scale distinction above), Debt (formal + informal via a lightweight Person record), richer Category/Nature classification, reporting-period abstraction. Everything else depends on this.
2. **Overview redesign** — the screen fully validated in this session (see below). Can be built incrementally against today's simpler data, then deepened once (1) lands.
3. **Transactions UX + classification engine** — filters, bulk edit, merchant-normalization rules, a "needs review" queue.
4. **Commitments & Planning** — upcoming-bills calendar/timeline, cash-runway projection.
5. **Analysis** — trends, category/merchant drill-downs, period-over-period comparisons, the category-matrix pattern (v1 §10, §20).
6. **Taxes** — dedicated tax tracking (may fold into 4).

## Data model additions (beyond v1 §2–§6)

- **Debt** — two kinds, one concept:
  - *Formal*: mortgage and similar (balance, rate, payment schedule).
  - *Informal*: person-to-person loans, in either direction. Modeled via a lightweight **Person** record (not free-text), so multiple loans to/from the same person roll up together on that person's record.
  - Surfaced on the Overview as a single net position (see below), drilling into a dedicated Debt view breaking out formal vs. informal (and, for informal, by person).
- **Yearly Commitments** — a planned-estimate-vs-actual tracked bucket per year (e.g., "2026 plan: €4,800"), accruing actual spend against it as transactions/commitments tagged as yearly-cadence occur. Progress is reported both as "% of plan consumed" and "% of year elapsed," so under/over-pace is visible at a glance. Distinguishing a transaction/commitment as "yearly" vs. "regular recurring" is a first-class classification decision (the IMI/vacation vs. weekend-break distinction above) — needs its own tagging mechanism in the classification model (v1 §4, §14).

## The Overview screen — validated design

This was iterated in a visual-companion mockup session through 13 versions. The result:

### Layout

Two labeled KPI-card groups, side by side per group (3 cards each), on a page capped to roughly 4/5 of the viewport width:

- **This month** (flow metrics, recompute every period): Income, Expenses, Net flow
- **Position** (snapshot metrics, no period-over-period delta in the same sense): Cash, Debt, Yearly Commitments

Below that: a narrative insight banner, the 12-month cash-flow chart, a merged category-comparison table, and an actionable Needs Attention list. Every KPI card and Needs Attention row links to its own detail page or filtered view — drill-down is the central interaction model throughout (v1 §24, reinforced by every product reference in the addendum).

### KPI cards (Income, Expenses, Net flow, Cash, Debt)

Each card:
- Big bold EUR value (23px) with a small uppercase label above it and a drill-down affordance (↗).
- Below that, a small multi-column chart: **Now** (the current month-to-date value) plus rolling averages at **1/3/6/12/18/24 months**, seven columns total.
  - Bars are **auto-scaled per card** to that card's own min–max range (not a literal zero baseline) — with a small minimum floor so the lowest point never fully disappears. This was a deliberate accuracy-vs-legibility tradeoff: strict zero-based scaling made narrow-range metrics like Income (which only moves ~7% across all six horizons) look visually flat. Shape legibility won.
  - A zero-axis line runs through the chart; small (9px), always-visible, light-grey period labels (`Now`, `1M`, `3M`, `6M`, `12M`, `18M`, `24M`) sit right at the axis rather than inside the bar area, so bars use the full available height.
  - **Now** is visually distinct from the six historical bars: outline + a diagonal-stripe fill pattern, not a solid color — it's the reference point everything else is compared against, not itself a judgment.
  - The **18M/24M columns sit inside a light-grey highlight box**, taller than the bar area itself, setting the "distant past" pair apart from the more recent four.
  - Hovering any bar shows its exact period + absolute € value (not a %, not a diff from average — the plain historical figure, e.g. "12M avg €6,640").
- **Color is assigned by whether a value is good news for that specific metric, not by literal direction.** Income, Net flow, and Cash use green for higher values (favorable). Expenses and Debt use a **uniform red fill** across all bars, since more spending and more debt are both unfavorable directions — this was a genuine bug in earlier iterations (color was initially tied to arrow direction, which is backwards for exactly these two metrics) and is now fixed at the design level, not just patched.
- **Debt is displayed as a positive magnitude** (e.g. "€142,300," not "-€142,300"), styled identically to Expenses (uniform red, bars growing upward) — "more debt" and "more spending" are the same category of unfavorable metric, and the sign-reversal makes that visually and mentally consistent rather than requiring an inverted-good-is-down convention.

### Yearly Commitments card

Different mechanic entirely — not a trend chart, a **progress bar**: actual € consumed vs. planned € for the year, plus "% of plan consumed" vs. "% of year elapsed" (so pace, not just totals, is visible), and the next known upcoming item (e.g., "next: IMI, 30 Nov"). This is the card that directly embodies the v1-recommendation change described above.

### Supporting sections

- **Narrative insight banner** — one sentence, not a chart: "Your spending fell 7.1% vs July. Travel accounted for €600 of the reduction, partly offset by €120 more in Restaurants." (Directly from the Ledgerly reference in the addendum — insight-first, evidence-underneath, not "six graphs on the screen.")
- **Cash-flow chart** — 12-month grouped bars (income/expense) with inline inline period-range pills (1M/3M/6M/9M/12M/YTD) directly above the chart rather than in a separate settings control (from the Origin reference), and the chart itself is responsive (fills the container width at any window size — an early mockup bug, fixed).
- **"Where did it go" is merged with "What changed"** into one category-comparison table: category name, ranked bar, current value, rolling-average value for the selected period, and Δ% — replacing what were originally two separate, differently-ordered lists. One aligned view, not two.
- **Needs Attention** — a short, actionable list (review queue, upcoming bill, new-recurring-payment confirmation, category anomaly), each row linking directly to where the user can act on it, not just see it (from the Copilot reference).

### Interaction model (from v1 §24, §9 of the addendum, reinforced throughout)

`Overview KPI or category → filtered Analysis/Transactions view → underlying transactions`, with breadcrumbs preserved. Every number should ultimately explain itself down to source transactions ("numbers have provenance," v1 §30).

## Product references consulted (addendum, for future screens)

- **Copilot** — review queue + upcoming recurring on one screen, compact restrained charts, category bars over pie charts.
- **Monarch** — information architecture: Transactions (what happened) / Recurring (what will happen) / Reports (what it means) / Goals (what I want) — matches the five-area nav already planned in v1 §19, §21.
- **Lunch Money** — analytics as an exploration environment, not a fixed dashboard; deep category drill-down.
- **YNAB** — period/account/category-filterable reports with clean presets; stacked-bars-plus-trend-line for composition-over-time questions.
- **Origin** — inline timeframe pills directly above a chart (adopted above); calendar/heatmap spending view (candidate for a later Commitments/Calendar screen, not yet designed).
- **Ledgerly** — narrative-first dashboards ("insight, then evidence") — adopted as the Overview's leading design principle.
- **FinMin** — the "monthly review" / financial-briefing concept (candidate for a future digest/summary feature, not yet designed).

## Not yet decided / explicitly deferred

- The full data model for Debt (formal + informal/Person) — sketched conceptually above, not yet schema'd.
- How "yearly-cadence" gets tagged on a transaction/commitment at classification time (v1 §14's rules engine will need to know about this new dimension).
- Every screen beyond Overview (Transactions, Commitments/Calendar, Analysis, Taxes, Planning) — referenced in the addendum but not mocked up yet.
- Whether the Overview's chart component gets built as reusable HTML/CSS (matching the Utilities domain's existing no-JS-dependency charts) or warrants a small charting library, given its complexity (auto-scaling, dual-direction bars, hover tooltips) relative to the simple bar charts built for Utilities.
