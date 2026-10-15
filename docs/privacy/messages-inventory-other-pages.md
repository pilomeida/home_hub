# Messages inventory: Transactions, House, Insurance, Documents, To-dos, Wiki check

Decision D14 (the messages rule): every message a person can see says what did not happen, why, and what they can do.
"Already" = the message already met the rule before this change (through the central table or pattern in `app/problems.py`).
"Converted" = the text was bare, technical, or missing a part and is now three-part.
"Left" = not changed, with the reason.

Files: `app/routers/{transactions,house,insurance,documents,todos,wiki_lint,wiki}.py`, `app/problems.py` (block
`# --- added by pages-other agent ---`), `app/templates/transactions/_needs_review_rows.html`, `house/_item_card.html`,
`house/record.html`.

| Location | Current text (before) | Status | After (what / why / next) |
|---|---|---|---|
| transactions: `bulk_edit`, `confirm_merchant` | `Unknown category` (400) | Already | Not saved. / the category doesn't exist (list may have changed) / reload and choose again |
| transactions: `bulk_edit` | `Ambiguous or unknown category; pick a tree category` (400) | Already | Not saved. / the name matches several or no categories / pick from the list |
| transactions: merchant and transaction routes | `Merchant not found`, `Transaction not found` (404) | Already (pattern) | Couldn't open that. / no merchant/transaction with that number / go back and choose again |
| transactions: `bulk_apply` | `Pick a sub-category to apply`, `Tick at least one description`, `Tick at least one merchant` (400) | Already (exact) | Nothing was changed. / nothing chosen or ticked / pick or tick, then try again |
| transactions: `link_debt` | `Transaction not found` (404, plain text) | Converted | Not linked. / the transaction isn't in the Hub any more / reload Needs Review and try again |
| transactions: `link_debt` | `Debt not found` (404) | Converted | Not linked. / the loan no longer exists / reload and choose the loan again |
| transactions: `link_debt` | `The role must be advance or repayment` (400) | Converted | Not linked. / role has to be advance or repayment / choose one and press Link Debt again |
| transactions: `link_debt` | `This transaction is already linked to a debt` (400) | Converted | Not linked. / already belongs to a loan, one transaction can be on one loan / nothing to do; reload Needs Review |
| transactions: `link_debt` (ledger text) | `This transaction is already linked to another debt` (400) | Converted | Not linked. / belongs to a different loan / leave it; moving it isn't possible from this page |
| transactions: `link_debt` | `Unknown direction` (400) | Converted | Not linked. / a loan is either money lent or money borrowed / choose one and press again |
| transactions: `link_debt` | `Choose the direction: a transfer does not say whether you lent or borrowed` (400) | Converted | Not linked. / a transfer doesn't say whether money was lent or borrowed / choose "we lent" or "we borrowed" in the new-loan box |
| transactions: `link_debt` | `The name is at most 80 characters` (400) | Converted | Not linked. / name longer than 80 characters / shorten it |
| transactions: `link_debt` | `Give the person's name`, `Choose a direction` (ledger text) | Converted | Not linked. / a new loan needs a name / a direction / type or choose it, press again |
| transactions: `link_debt` | `Choose a role (advance or repayment): a transfer has no reliable direction` (ledger text) | Converted | Not linked. / a transfer's role can't be worked out by itself / choose advance or repayment |
| transactions: `link_debt` | `The role does not fit this transaction and direction: money going out starts a loan we gave, ...` (400) | Converted | Not linked. / the role doesn't fit this payment / choose the matching role or leave it on automatic |
| transactions: Needs Review, unclassified card | `<txn> — 12.34 EUR — flagged for manual attention` | Converted | Not filed yet. / no merchant could be matched / select it under All transactions and set its category with "Apply to selected" |
| transactions: Needs Review JS box | shows the response text as is | Already | Shows the three-part text from the routes above |
| house: `upload` (no file) | `Choose a file to upload` | Converted | Nothing was uploaded. / no file was chosen / choose a file, then press Upload again |
| house: `attach` (no file) | `Choose a file` | Converted | Nothing was attached. / no file was chosen / choose a file, then press Attach again |
| house and insurance: ingestion `<label> accepts image, pdf files only` | (file error, bare) | Converted | Not saved. / the category takes only photo or PDF files and this file is another kind / choose a file of one of those kinds |
| house and insurance: ingestion `Choose a file to upload` (unknown type) | (file error) | Converted | Nothing was uploaded. / the Hub doesn't recognise this kind of file / choose a PDF or a photo |
| house and documents: ingestion `This file is already filed elsewhere` | (file error, bare) | Converted | Not filed. / the Hub already has this exact file, filed elsewhere, one copy of each file / find it or choose a different file |
| house: `attach`, `RefileRefusedError` "This record already has a file attached." | (bare) | Converted | Not attached. / a record holds one file / open the record to see the file it has |
| house: `attach` form on an attached record | notice was inside the form, hidden when a file was attached | Converted (template) | Notice is shown below the attachment, in both states |
| house: `update_fields`, `RefileRefusedError` | uncaught: a 500 "Something broke" | Converted | 409: Not saved. / the file belongs to a record typed in by hand / open the record and edit it there |
| house: item card marker | `!` (no words) | Converted | Not read: the Hub couldn't process this file. / open it to see why and what to do |
| house: `document detail`, `failure_reason` | friendly_reason (shared filter) | Already | Unchanged |
| house and insurance: upload scan notice | "photos and scans..." | Not an error | Unchanged (privacy notice, D7) |
| insurance: `upload` (no file) | `Choose a file to upload` | Converted | Nothing was uploaded. / no file was chosen / choose a file, then press Upload again |
| insurance: `Insurance document not found` (404) | | Already (pattern) | Couldn't open that. / no insurance document with that number |
| documents: `refile` refusals (`This file is attached to a hand-entered record; edit/re-file the record instead.`, `This record was already re-filed.`, `A hand-entered record can only become a document if...`) | `refused=str(exc)` (bare, 409) | Converted | Not saved. / Not moved. with a reason and a next step (table in `app/problems.py`) |
| documents: refile blocked by linked transactions (financials handler) | `2 transaction(s) from this document are linked ... Remove those links in Transactions first — re-filing would discard them.` | Converted (pattern) | Not moved. / N transaction(s) linked to a commitment, debt or transfer, and moving would discard those links / remove those links in Transactions first |
| documents: `Choose a domain` (field hint) | instruction | Left | An instruction under the field, obvious reason; not bloated (brief rule) |
| documents: `Document not found`, `Record not found` (404) | | Already (pattern) | Couldn't open that. / no ... with that number |
| todos: `add` bad date | `What was entered isn't valid: Invalid isoformat string: '10/10'.` (raw exception text, double period) | Converted | To-do not added. / the due date isn't a real date in YYYY-MM-DD / pick the date again or leave it empty |
| todos: `add` empty title | `What was entered isn't valid: A to-do needs a title..` | Converted | To-do not added. / the title is empty / type a title and add again |
| todos: `add` unknown domain | `Unknown domain` (400) | Already (exact) | Not done. / the area doesn't exist / reload and choose again |
| todos: `done` missing | `Todo not found` (404) | Already (pattern) | Couldn't open that. / no todo with that number |
| wiki: `wiki_page_detail` | `Wiki page not found` (404) | Already (pattern) | Couldn't open that. / no wiki page with that number |
| wiki_lint: `run` while a check runs | `A check is already running` (query string, bare) | Converted | Not started. / a check is already running, and only one runs at a time / wait, then refresh |
| wiki_lint: `run` started | `Check started - refresh in a minute` | Success notice | Unchanged |
| wiki_lint: `add-link` on a non-missing finding (400) | `Only missing-link findings can add a link` | Already (exact) | Link not added. / not a missing-link finding / use Dismiss or Fixed |
| wiki_lint: `dismiss`, `fixed`, `add-link` missing finding (404) | `Finding not found` | Already (pattern) | Couldn't open that. / no finding with that number |
| wiki_lint: `add-link` when the link already exists | `add_link` returns False and the finding closes as Fixed | Left | Not a failure: the link is already there |
| shared: `app/domains/fields.py` field errors (`X is required`, `Choose a valid X`, `must be a date (YYYY-MM-DD)`) | field hints under inputs | Left | Field hints outside my area (fields.py) and instructions with an obvious reason |
| shared: ingestion `Choose a category` (category hint) | instruction | Left | Instruction hint; not rewritten (file_notices only rewrites known refusals) |
| shared: `app/services/presentation.py` `friendly_reason` | shared filter | Left | Outside my area; already three-part |

Left for a later pass, with the reason:
- Field-level hints in `app/domains/fields.py` and `ingestion.py` are not in my area. They are rewritten only where they appear as file or category refusals (`app/problems.py` `file_notices`).
- Inline `errors` dicts are still the raw service text; the three-part wording is applied at the router/template boundary, not at the source, because the services are outside my area.
