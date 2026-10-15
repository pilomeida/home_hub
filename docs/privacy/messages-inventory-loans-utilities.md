# Messages inventory: loans and utilities (messages rule, D14)

Scope: `app/routers/loans.py`, `app/routers/utilities.py`, `app/templates/loans/`, `app/templates/utilities/`, and the service text they display (`position_store.py` sentences only). Every message is now *what did not happen* / *why* / *what to do*. Technical text stays only under "Technical details".

Web errors that are a plain-string `HTTPException` keep their string, so API callers still get the same JSON. The three-part text lives in `_HTTP_EXACT` / `_HTTP_PATTERNS` (block marked `# --- added by pages-loans-utilities agent ---`).

## Web errors (HTTPException, via the central table)

| Location | Current text | What / why / next |
|---|---|---|
| `POST /financials/loans/upload`, no file | Choose at least one file | Already three parts (central table): Nothing was uploaded. / No file was chosen. / Choose a file and try again. Unchanged. |
| `POST /financials/loans/upload`, more than 12 | Upload at most 12 files at a time | Already three parts (pattern). Unchanged. |
| `POST /financials/loans/assign`, upload gone | Upload not found | Couldn't open that. / The Hub has no upload with that number... / Go back and choose it again. Unchanged (pattern). |
| `POST /financials/loans/assign`, not waiting for a loan | This printout does not need a loan assignment | **Converted.** Now a separate key, "This printout is not waiting for a loan": Not assigned. / Its figures were already read, or could not be read, so there is no loan to choose. / Nothing to do here; to retry an unreadable printout, upload it again. (The old wording was wrong for a printout that had already been read.) |
| `POST /financials/loans/alerts/ack-group`, unknown kind | Unknown alert kind | **Converted.** Not acknowledged. / That kind of red flag doesn't exist... / Reload the Loans page and try again. |
| `POST /financials/loans/alerts/{id}/ack`, note too long | The note is at most 300 characters | Already three parts (pattern). Unchanged. |
| Alert, loan, entry, informal loan, upload not found (404) | "... not found" | Already three parts (pattern). Unchanged. |
| Informal loan: amount not a number | The amount must be a number | **Converted.** Not saved. / The amount has letters or symbols in it... / Type the amount as a number, for example 250.00. |
| Informal loan: amount zero or less | The amount must be greater than zero | **Converted.** Not saved. / An amount of zero or less can't be recorded... / Type the size as a positive number. |
| Informal loan: date not year-month-day | The date must look like 2026-01-31 | **Converted.** Not saved. / The date isn't in the year-month-day form... / Pick it from the calendar, or type 2026-01-31. |
| Informal loan: no person name | Give the person's name | **Converted.** Not saved. / The loan needs the name of the person it is with... / Type their name, then create the loan again. |
| Informal loan: name longer than 80 | The name is at most 80 characters | **Converted** (new pattern). Not saved. / It is longer than 80 characters. / Shorten it and save again. |
| Informal loan: no direction | Choose who owes whom | **Converted.** Not saved. / The Hub needs to know whether they owe you or you owe them... / Choose one of the two options. |
| Informal loan: note longer than 300 | The note is at most 300 characters | Already three parts (pattern). Unchanged. |
| Entry: unknown kind | Choose advance, repayment or an adjustment | **Converted.** Not saved. / The kind of entry wasn't one of the four choices... / Pick one from the list, then save again. |
| Entry: delete a bank-linked entry | An entry made from a bank transaction cannot be deleted | **Converted.** Not deleted. / It was copied from a bank transaction, and the Hub keeps those so the loan matches the bank. / To correct the amount, add an adjustment entry here instead. |
| `GET /financials/utilities/{tab}`, unknown tab | Unknown utility tab | Already three parts (central table). Unchanged. |

## Inline notices on the assignment page (results.html, inline 400)

| Location | Current text | What / why / next |
|---|---|---|
| assign, account number not 8+ digits | The account number must be digits only, at least 8 of them. | **Converted.** Not assigned. / An account number is made of at least 8 digits, with no letters or symbols. / Copy the digits from the COB.REC.31 text (spaces are fine). |
| assign, no name | Give the loan a name. | **Converted.** Not assigned. / The loan needs a name... / Type a name such as "Car loan". |
| assign, bad type | Choose mortgage, personal or other. | **Converted.** Not assigned. / The loan type isn't one of the three... / Choose mortgage, personal or other. |
| assign, number overlaps an existing loan (`loan_number_conflict`, service text rewritten) | loan number X overlaps the existing loan number Y | **Converted.** Not assigned. / The loan number X overlaps the existing loan number Y, and two loans can't share one. / Choose that existing loan if this printout is for it; otherwise check the account number. |
| assign, picked loan gone | Choose one of the existing loans, or a new loan. | **Converted.** Not assigned. / The loan picked is no longer in the list... / Reload the page and choose again. |
| assign, printout disagrees with a loan's stored instalments (`assign_history_to_loan`, service text rewritten) | instalment N of this printout has different amounts ... - it looks like another loan | **Converted.** Not assigned. / Instalment N on this printout has different amounts ... so it looks like another loan's printout. / Choose the loan this printout really belongs to, or create a new loan with its own account number. |

## Upload results (per file, results.html)

| Location | Current text | What / why / next |
|---|---|---|
| file over the size limit | file too large (the limit is 25 MB per file) | **Converted.** Not uploaded. / The file is larger than the 25 MB limit for one file. / Split it into smaller files (one month at a time). |
| name not .pdf | only PDF files are accepted | **Converted** (split by cause). Not uploaded. / Only PDF files can be read, and this file isn't named as one. / Download it from the bank as a PDF. |
| bytes not a PDF | only PDF files are accepted | **Converted.** Not uploaded. / The contents aren't a PDF, so they can't be read. / Download it from the bank as a PDF, then upload that. |
| truncated PDF | file looks truncated | **Converted.** Not uploaded. / The file looks cut off... / Download it again from the bank. |
| already uploaded as another kind | this file was already uploaded as X | **Converted.** Not read again. / This exact file was already uploaded as X, so it isn't read again as a ... / Nothing is lost; upload the right printout for this box if this is a different one. |
| stored failure reason (`extraction.error` / `document.failure_reason`), shown as "failed: ..." | raw stored text, e.g. "Could not parse position extraction response: ..." | **Converted** via `_explain`: a known cause gives its own why (e.g. "The AI's answer wasn't in the form..."); plain English is shown as the why; anything else gets a generic why. The raw text is only under "Technical details". Not read. |
| exception while reading (`str(exc)` or class name) | raw exception text | **Converted** the same way. Not read. |
| `InvalidClassification` | `str(exc)` (dict repr) | **Converted**: the field messages are used as the why; raw text under Technical details. |
| printout matches no loan (needs_loan) | this printout does not match any loan I know yet | **Converted.** Not assigned yet. / This printout doesn't match any loan the Hub knows yet. / Choose the loan it belongs to below, or enter a new loan's account number and name. |
| read, but a stored note: oldest instalment ignored (cropped printout) | (oldest instalment nº N ignored: printout is cropped) | **Converted** to a warning on the row. Instalment nº N was not recorded. / The printout is cut off at its edge... / Upload a complete loan-history printout from Netbanco. |
| read, but a stored note: instalments disagree | (N instalment(s) disagree with data already stored) | **Converted** to a warning. Some instalments were not updated. / N instalment(s) ... so the stored figures were kept. / Compare the two printouts with the bank. |

Note: the "read" and "already read" summary text itself is a positive fact and is unchanged.

## Loans pages (index, detail, macros)

| Location | Current text | What / why / next |
|---|---|---|
| index, loan with unknown balance | balance unknown — upload a statement or a longer loan history | **Converted.** Balance not known yet. / The Hub hasn't read a statement or loan history that shows it. / Upload one with the + button. |
| index, totals note | totals exclude N loan(s) with unknown balance | **Converted.** Not in the totals: N loan(s) whose balance isn't known yet. / ... Upload a statement or a longer loan history with the + button to add them. |
| freshness, stale data | Data as of X — N days old, upload newer statements | **Converted.** Not up to date. / The newest figures are from X, N days ago, so the totals may be out of date. / Upload newer statements with the + button. (Non-stale line unchanged.) |
| red flags, each kind (`interest_only`, `spread_changed`, `rate_inconsistent`) | the alert message only | The alert message (the fact) is unchanged. A why-and-what-to-do line is now shown beside it (macro `flag_help`), once per group. |
| detail, big figure when balance unknown | balance unknown | **Converted** to "balance not known yet", with a sub-line: Not in the totals. No statement or loan history has shown the balance yet. Upload one with the + button. |
| detail, rate-history cell tooltip (changed spread) | differs from the baseline spread X% | **Converted.** Differs from the baseline spread X%, which should never change. Check it against the contract, and acknowledge it under Red flags once explained. |

## Utilities

| Location | Current text | What / why / next |
|---|---|---|
| utility tab, no readings | No data yet. | **Converted.** No readings yet: nothing has been read from a X bill. Upload one and it will appear here once it has been read. |

## Left unchanged (not a failure, or outside this area)

- **Empty states**: "No loan or savings data yet — upload ...", "No open loans.", "No funds recorded.", "No deposits or cards recorded.", "No informal loans yet.", "No entries yet.", "No instalments recorded yet — upload ...". They describe an empty list, not a failure; the one with a next step already gives it.
- **Field hints and help text**: placeholders ("Note (optional), e.g. bank error, known"), the COB.REC.31 help line, and the upload instructions. These are instructions, not warnings.
- **Alert message texts** (`app/services/loan_alerts.py`: "Spread changed ...", "Instalment N (...): interest only ...", "Rate inconsistent ..."). These are stored facts used for acknowledgement. The why-and-next is shown beside them (see above).
- **`app/services/pdf_bytes.py` constants** `NOT_PDF` / `TRUNCATED`: unchanged. They are now mapped to three-part text in the router.
- **Unreachable service errors**: `debt_ledger` raises "Statement loans have no ledger" and "Unknown entry kind", but the routes reject those cases first (informal debts only; kind checked against `KINDS`). Not shown to anyone.
- **Outside this area**: `scripts/import_position_files.py` (CLI) now prints the three-part text for each file result (it read `FileResult.message`, which is now empty for failures). Its `--dry-run` "REJECTED" line still prints the bare PDF reason (`only PDF files are accepted`); left for the scripts owner.
- **Outside this area**: `document.failure_reason` is also written by `mark_needs_attention` for other document pages; `app/domains/financials/handler.py` writes the utility-reading failure reason. Both belong to other pages and were not touched.
- **Problems page**: the loans upload and utility pages show failures to the person who is looking at them, so nothing here is logged there. No background job in this area.
