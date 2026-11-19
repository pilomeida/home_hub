# Design references (Pedro, 2026-11-19)

13 dashboard screenshots Pedro supplied as guidance for the Hub's UI redesign. They serve different purposes: some
overlap with what the Hub builds (money, tables, to-do cards), others are mood only. Read with
`docs/design/ux-workflow-and-screens.md` (the decisions) before using any of them.

**Pedro's rules on the references (2026-11-19):** no gradients anywhere (the peach glow of 01/02, the green blur and
"Special Offer" tile of 03 are out); no faces or avatars (the face circles in 03, 04, 05, 06, 12 are not wanted);
light theme for now.

| File | Product (as shown) | Use it for | Take | Leave |
|---|---|---|---|---|
| 01, 02 | PulseAI analytics | Money overview, key figures, chart cards | Big figure with small delta, hatched "previous" bars, ranked bars with values, heat-grid | Peach gradient glow, glass cards |
| 03 | CoreFlow | Bento home, soft mood | Rounded bento cards of different sizes, dotted win-rate grid, pill nav | Green blur, promo tile, avatars |
| 04 | Donezo | Sidebar workspace, to-dos/reminders | Left sidebar with groups, KPI card row, striped bars, reminder card, semicircle gauge | Avatars, "download app" tile |
| 05 | ACRU | Finance: budget, cost analysis, goals | Spending limit bar, stacked cost-by-category bar with legend, goal progress rows, transaction list, quick-action buttons | Bank-card visual |
| 06 | Sypher | Dense operator desk: charts + invoice table | Tabs under a thin top bar, KPI strip with mini bars, line chart with hover tooltip, filterable table with row checkboxes | Contact avatars |
| 07 | Expense breakdown widget | Spending by category | Ranked bars with percent at right, small contrast segment for change | — |
| 08 | Market vs actual widget | Category history / vs budget | Line against a baseline band, period selector chip | Pink fill |
| 09 | Portfolio trendline widget | Trend with scatter context | Period selector chip, clean axes | — |
| 10 | Conceptzilla leads | Queues and tables (Waiting for you, All lines) | Tabs-as-filters over a table, bulk-select rows, status chips, score bars | Avatars, orange CTA |
| 11 | Stock/spread terminal | Compact numeric tables and charts | Dense tables with green/red deltas, hover value tooltip, tab switch over a chart | Dark terminal blocks |
| 12 | Task cards (kanban) | Cards with tags, checklists, attachments | Tag chips, checklist rows, attachment count | Avatars, tilt/animation |
| 13 | Property portfolio (the most complete) | Whole-app layout, dense desk view | Top tabs for layouts, KPI strip, multi-line chart with legend toggles and tooltip, four small chart cards, collapsible sidebar, search with shortcut | Dark-mode toggle for now |

Mockups built from these: `docs/design/mockups/` (three options).
