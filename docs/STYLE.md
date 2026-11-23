# Home Hub style guide

> **Update this file when a component changes.** Any change to `app/static/hub.css` that adds, removes or changes a
> token or a component is edited in here in the same commit. Pages are built from what is listed here; a page that
> needs something new adds it here first (as a component), never as a one-page patch or an inline `style=`.

The look is Home's (PLAN step 10, Option A "super clean and readable", Pedro 2026-11-19; step 11, 2026-11-23): warm
light background, white rounded cards, one orange for the main action, green and peach tints for groups. Light theme
only: no dark mode, no gradients, no shadows except the Who menu.

## 1. Tokens (`:root` in `hub.css`)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#FAF3EA` | page background |
| `--surface` | `#FFFFFF` | cards, tables, inputs |
| `--t1` / `--t2` | `#FFE6D3` peach / `#E3EBDA` green | tinted cards (`.card.tint1/2`), month rows, chips |
| `--ink` / `--muted` | `#2A2019` / `#8A7B6E` | text / secondary text, hints, table headers |
| `--line` | `#EDE0D2` | borders and row dividers |
| `--accent` | `#E5632B` | the main action, links, this month's bar |
| `--accent2`, `--good`, `--bad`, `--warn` | greens, red, amber | earnings, OK, problems, "check this" |
| `--fs` | `clamp(15px, 9.5px + 0.38vw, 20px)` | the root size: 15px on phones and laptops, ~17px at 1920px, 20px from ~2700px |
| `--fs-small`, `--fs-xs` | `.88em`, `.78em` | hints, captions |
| `--h1`, `--h2`, `--h3` | `1.75rem`, `1.3rem`, `1.08rem` | headings |
| `--pad`, `--gap`, `--side` | `1.33rem`, `1.07rem`, `1.33rem` | card padding, space between blocks, page side gutter (16px on phones) |
| `--radius`, `--radius-s`, `--radius-pill` | `1.45rem`, `.85rem`, `999px` | cards, small boxes and inputs, pills and buttons |
| `--page-max` | `96rem` | the one content width |

**Size in `rem` or `em`, never `px`** (except hairlines: 1px borders). Everything then grows with the screen through
`--fs`. Colours always come from a token; a new colour is a new token here.

## 2. Layout and width

- One content width, `--page-max` (96rem: about 1,440px on a laptop and 1,920px on a 2,880px-wide screen), shared by
  the header, the section row and `<main>`, so every edge lines up.
- **Header:** logo left, the seven menu pills centred, Who, "+ Add a paper", the gear and the Problems count right.
- **Section row:** under the header, the section's own pages as tabs (`.sectionbar .tab`, the current one white) on
  the left, search filling the rest of the row.
- **Page:** `h1` (or `.page-h` for a title with a button on the right), then one grey line saying what the page is
  (the first `p` after `h1` is grey automatically; or use `.lede`), then cards.
- **Grids:** `.g2a` (wide + narrow, Home), `.g4.k`, `.g4.p` (four tiles), `.grid2` (cards two by two, each at least
  30rem). Grids fall to one column under 1100px or when a column would be narrower than its minimum.
- **Breakpoints:** 1100px (grids fold, the header's pills wrap onto their own row) and 760px (phone: the pills go
  to the bottom phone bar, search takes its own row, tabs scroll sideways). Check every page at 1920, 1366 and 375.

## 3. Components

**Card** `.card`: white, `--radius`, `--pad`. Title row `.card-h` (bold title left, `.card-r` link or note right; an
`h2.card-h` is the same look). Tints: `.card.tint1`, `.card.tint2`. Hero (Home's and Ask's question box):
`.card.tint1.hero`. Group a page into cards; text straight on the background is only the title and its grey line.

**Stat tile** `.g4.k .card` / Home's `.wi`: a label, a `.big` number (`.big.s` smaller), a `.sub` line; the whole tile
links to its page.

**Table:** a plain `<table>` in `<main>` already looks right (white, rounded, muted headers, row dividers); `.tbl`
inside a `.card.flush` for tables that share a card. Numbers right-aligned with `.num`; a month or group row is
`tr.mh`. Long tables keep their columns on phones by scrolling inside the card, not the page.

**Form field:** a plain `input`, `select` or `textarea` already looks right (white, rounded, orange focus ring).
Label above the field: wrap in `.field` (`<div class="field"><label>Name <input …></label></div>`); fields side by
side: a `form.form-row`. Hints under a field: `small` or `.hint`. Checkboxes and radios take the orange.

**Buttons:** a plain `<button>` is the secondary button (white pill). The one main action of a form or card is
orange: `class="pri"` on a `<button>`, or `.btn.pri` on a link. One orange per form; a Save on every table row
stays white. Something that destroys: `class="danger"` (red outline). Links styled as buttons: `.btn`.

**Tag / chip** `.chip` (and `.badge`, the same look): a small pill naming a kind or area. States: `.chip.ok`,
`.chip.warn`, `.chip.bad`, `.chip.on` (chosen filter, dark). Filters above a list: `.chips` of `a.chip`, or a
`nav.subnav` of links (the current one `.active`).

**Messages** (the Messages rule in `CLAUDE.md`: what didn't happen, why, what to do): `.msg` with `.what`, `.why`,
`.next` (red edge); `.msg.info` for a neutral notice (green edge). Built from `app/problems.py`; a failed button press
shows the same banner at the top (`#problem-banner .problem`). Never a bare exception, never a colour alone.

**Empty state** `.empty`: one centred grey sentence in a white box saying why there is nothing and, where there is
one, the button that makes something (`.empty .btn`).

**Charts:** the one chart component (`app/ui/charts.py`, `.chart`), on Pedro's scale rule; never a second chart style.


## 4. Writing on the page

Plain English, sentence case (no capitals-and-tracking labels), dates `yyyy-mm-dd`, money `€1,234.56`. Every warning
says what, why and what to do (section 3, Messages).
