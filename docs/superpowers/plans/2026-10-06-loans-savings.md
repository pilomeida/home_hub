# Loans & Savings (Plan 3 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Know every loan's pending capital, rate, instalment split (capital vs interest) and payoff date, and every savings balance, by reading the Santander monthly statements and the online-banking loan-history printouts ("Consulta Movimentos Empréstimo") Pedro uploads every ~5 months (positions and loan movements only, never account transactions); link loan instalments to their loan automatically; show it all on a "Loans & Savings" page; include loan instalments in the Overview's Net; remind Pedro when it's time to upload.

**Architecture:** A second, positions-only extraction pass on a statement PDF (through the llmsel gateway, like every other extraction) yields loans, savings holdings and balances; deterministic reconciliation checks (balance continuity instalment to instalment, capital + interest = scheduled instalment) reject bad reads. Results go into new snapshot/movement tables hanging off the existing `Debt` model. Transactions whose bank text contains a loan's account number are linked to that loan automatically and filed under the Loans nodes of the category tree (Plan 1). A page, an Overview hook (Debt card, Net) and a reminder service (Overview item + Telegram push) sit on top.

**Tech Stack:** FastAPI, SQLModel, Alembic (SQLite batch), Jinja2 + htmx, python-telegram-bot (already in the app), pytest.

**Spec:** `/home/pedro/Desktop/Claude_Corner/brainstorms/2026-10-05-transaction-category-hierarchy.md` (section "Plan 3 decisions") and Plans 1–2 under `docs/superpowers/plans/`.

## Global Constraints
- No `git commit` / `git push` without Pedro's explicit order; commit steps are "ready to commit", do not run them.
- **Statements are read for positions only** — loans, savings funds, deposits, card balances. Never create `Transaction` rows from them (bank sync already collects those 3×/day).
- Interest and pending capital come **from the statements**; Pedro never types interest. (Informal person-to-person loans are the exception: manual records.)
- Matching instalments to loans is **fully automatic** (no confirmation step) for statement loans, by account number in the bank text. For informal loans, the first manual link creates a rule that auto-links later matches.
- Every LLM call goes through the llmsel gateway (`app/llm_gateway.py`): no provider SDK, model name or key. Response schemas must pass the strictness rules in `tests/test_schema_strictness.py` (accept nulls where the parser defaults).
- Test data comes from production code and the shared `tests/fakes/fake_gateway.py`; **no real statement PDFs or real personal data in the repo or in tests** — use synthetic values in the same layout.
- Reconciliation, not trust: a position extract whose numbers don't reconcile is stored as failed with the reason, never silently saved.
- Reminder text, exactly: `Reminder: to keep loan and savings data updated, you have 30 days to download the latest digital bank statements from Santander and share them with me.` With a "+" next to it that opens the multi-PDF upload page. Cadence: every 5th month since the last positions upload.
- Money: `Decimal(12,2)` for balances/rates-derived amounts (as `Debt.current_balance` does); rates as percent floats.
- Colours: debt = muted red, savings = muted green, stale data = muted amber; light and dark mode.
- The production DB has no staging: test migrations on a scratch copy; back up on the VPS before the push (deploy runs `alembic upgrade head`).

## What Pedro's real files contain (verified on the 13 PDFs he put in ~/Downloads on 2026-10-06: statements Apr–Sep 2026 and five loan-history printouts)
- **The monthly statement only has loan blocks for the two housing-loan tranches** (…0001, €100k; …0002, €110k). The **third loan** (account …0003, instalment nº 280, balance ≈ €20k, no statement block) appears **only** in the online loan-history printouts and, as `COB.REC.31.000200000000003/…` debits, in transactions.
- **Loan-history printouts** (`descarga (N).pdf`, title "Consulta Movimentos Empréstimo"): rows `date | value date | instalment nº | PRESTACAO - CAPIT./JUROS/SEGURO/SEG ED | amount | Saldo em Dívida`. The balance column is filled on `CAPIT.` rows only (visible with `pdftotext -raw`; the layout mode cuts it). **There is no loan number on a printout**; the loan must be identified by matching (instalment nº + capital + interest) against known movements, or assigned by Pedro. Overlapping/duplicate printouts occur (descarga 8 and 9 are identical; 7 is a shorter view of the same loan) — ingestion must be idempotent.
- **An instalment can be paid in several pieces**: e.g. tranche …0002, instalment nº 20 = JUROS 200,00 + CAPIT. 150,00 + 50,00 + 30,00 (the account was topped up piecemeal), total 430,00 = the scheduled instalment. These are **partial payments of one instalment, not extra amortisations**: sum all CAPIT. rows of an instalment number.
- Each instalment also carries **insurance components** (`SEGURO` = life, `SEG ED` = building), which are debited from the account as separate `SEG VIDA …` / `SEG:EDF …` / `SEG:LAR …` lines.
- Rate revisions happen (Euribor): tranche …0002's instalment rose from 430,00 to 460,00 in one month. The loan block shows the applied rate (`Tx. Juro Aplicada: 3,000%`); for the third loan the rate is **implied**: interest × 12 ÷ balance before the instalment (≈ 5.0%).
- The summary line `RESPONSABILIDADES … CRÉDITO HABITAÇÃO` is the bank's own total and does **not** equal the sum of the tranche blocks; store it only informationally, never use it for totals.

## What the statements contain (verified on the Jul- and Sep-2026 statements; all text below is Santander "Extrato Consolidado")
- `Resumo das Contas`: deposits (`DEPÓSITOS À ORDEM`), `FUNDOS DE INVESTIMENTO` totals with date; `RESPONSABILIDADES`: `CRÉDITO HABITAÇÃO` total, `CARTAO DE CREDITO` balances.
- Per loan block `Empréstimo CRÉDITO HABITAÇÃO Número: 0001.00000000001`: `Tx. Juro Aplicada: 3,000%`, `Prazo: 300 MESES`, `Capital Concedido: 100.000,00`, `Capital Vincendo: 90.000,00`, `Data Formalização`, `Capital Vencido Pago`; movements `PRESTAÇÃO Nº.00035` with `CAPITAL 200,00` and `JUROS 230,00`, `Saldo Inicial/Final`; an instalment paid in pieces appears as several `PRESTAÇÃO Nº.00035 … CAPITAL x` lines (sum them); `Próxima Prestação` (due date, nº, TAN, indexante, spread, total, capital/interest detail).
- Fund accounts: `Conta Fundo Nº`, holder name, fund (`SANTANDER AFORROPPR`), units, invested, price, value at date; `SUBSCRICAO PERIODICA` amounts; `Agenda da Conta` with next periodic subscription date/amount.
- Portuguese number format: `1.234,56` = 1234.56.
- Loan account numbers appear in transaction text as `COB.REC.31.000100000000001/ 35` (digits without dots; the statement shows `0001.00000000001`).

## File structure
- Modify `app/models/debt.py` (new nullable columns) and create `app/models/position.py` (`LoanMovement`, `LoanSnapshot`, `SavingsSnapshot`, `BalanceSnapshot`, `PositionExtraction`, `ReminderLog`, `DebtMatchRule`).
- Create `alembic/versions/c3a9d4e7f215_loans_savings.py`.
- Modify `app/services/taxonomy_seed.py` (Savings group) + a data step in the migration via `ensure_taxonomy`.
- Create `app/services/position_extraction.py` (gateway call + parsing + reconciliation), `app/services/position_store.py` (apply to DB, idempotent), `app/services/loan_linking.py` (auto-link), `app/services/loan_math.py` (payoff, totals), `app/services/statement_reminder.py`.
- Create `app/routers/loans.py` with templates `app/templates/loans/{index,detail,upload,_summary}.html`; modify `app/main.py` (router), `app/domains/financials/spec.py` (nav link), `app/services/overview_service.py` + `app/templates/dashboard/*` (Debt card, reminder), `app/services/budget_service.py` + `_budget_panel.html` (loan instalments in Net).
- Create `app/jobs/statement_reminder.py` + `deploy/systemd/home-hub-reminder.{service,timer}`; modify `deploy/systemd/` install script list if it enumerates units.
- Tests: `tests/test_position_models.py`, `test_position_extraction.py`, `test_position_store.py`, `test_loan_linking.py`, `test_loan_math.py`, `test_loans_router.py`, `test_statement_reminder.py`; extend `test_overview_service.py`, `test_budget_service.py`, `test_schema_strictness.py`.

---

### Task 1: Data model, migration, Savings category group

**Files:** Create `app/models/position.py`, `alembic/versions/c3a9d4e7f215_loans_savings.py`; Modify `app/models/debt.py`, `app/models/__init__.py`, `app/services/taxonomy_seed.py`; Test `tests/test_position_models.py`, `tests/test_taxonomy.py` (append).

**Interfaces — Produces:**
```python
# Debt gains (all nullable, no behaviour change for existing rows):
name: Optional[str]; external_number: Optional[str] (index, digits only, e.g. "000100000000001");
capital_granted: Optional[float]; term_months: Optional[int]; start_date: Optional[date];
status: str = "active"            # "active" | "closed"
loan_type: Optional[str]          # "mortgage" | "personal" | "other"

class LoanMovement:   id, debt_id FK, instalment_number: int, movement_date: date (date of the first piece),
                      capital: float, interest: float, insurance: float = 0.0,   # one row per (loan, instalment nº),
                      balance_after: Optional[float],                            # pieces already summed
                      document_id FK; UniqueConstraint(debt_id, instalment_number)
class LoanSnapshot:   id, debt_id FK, as_of: date, capital_remaining: float, rate_percent: Optional[float],
                      next_due_date: Optional[date], next_instalment: Optional[float],
                      next_capital: Optional[float], next_interest: Optional[float], document_id FK;
                      UniqueConstraint(debt_id, as_of)
class SavingsSnapshot: id, as_of: date, holder: Optional[str], product: str ("fund"), label: str,
                      account_ref: str, units: Optional[float], invested: Optional[float],
                      value: float, periodic_amount: Optional[float], next_periodic_date: Optional[date],
                      document_id FK; UniqueConstraint(account_ref, label, as_of)
class BalanceSnapshot: id, as_of: date, kind: str ("deposit"|"card"|"investments_total"|"loans_total"),
                      label: str, amount: float, document_id FK; UniqueConstraint(kind, label, as_of)
class PositionExtraction: id, document_id FK UNIQUE, status ("ok"|"failed"|"needs_loan"), error: Optional[str],
                      payload_json: Optional[str]   # parsed loan-history awaiting a loan assignment (no second LLM call), extracted_at: datetime
class ReminderLog: id, key: str, sent_at: datetime, cycle_start: date   # one row per reminder cycle
class DebtMatchRule: id, debt_id FK, normalized_key: str (unique index)      # informal-loan auto-link rule (used in Task 5)
```
Taxonomy addition (append to `SEED` in `taxonomy_seed.py`): out group `("Savings & investments", [("Contributions", M, None, ["Fund subscriptions", "Deposits to savings"])])` (monthly, budgetable); in group `("Savings & investments (in)", [("Withdrawals", NONE, L.INCOME, ["Fund redemptions"])])`. Slugs: `savings-investments.contributions.fund-subscriptions`, `savings-investments-in.withdrawals.fund-redemptions`. `ensure_taxonomy` is idempotent, so the migration just calls it (as `a1c4e7b92d10` does).

- [ ] **Step 1: Failing tests** — `tests/test_position_models.py`: (a) each model round-trips through a `session` with the unique constraints enforced (inserting the same `(debt_id, as_of)` snapshot twice raises `IntegrityError`; same for `SavingsSnapshot`, `BalanceSnapshot`, `LoanMovement` key, `PositionExtraction.document_id`); (b) a `LoanMovement` second row for the same `(debt_id, instalment_number)` raises `IntegrityError`; (c) a `Debt` created the old way (no new fields) still works and has `status == "active"`. Append to `tests/test_taxonomy.py`: `ensure_taxonomy` creates `savings-investments.contributions.fund-subscriptions` with `kind == "out"`, `cadence == "monthly"` and `savings-investments-in.withdrawals.fund-redemptions` with `kind == "in"`; budgets can be set on the first (`set_budget`) and not on the second.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement the models and seed lines exactly as above (SQLModel style as in `app/models/debt.py`; money columns as `Numeric(12, 2)` only where the existing `Debt.current_balance` pattern fits, floats elsewhere to match `Transaction.amount`). **Step 4:** migration `c3a9d4e7f215` (`down_revision = "b7d2f5a81c34"`, current head): `batch_alter_table("debts")` adds the columns (explicit index `ix_debts_external_number`), creates the seven tables with the unique constraints named `uq_<table>_<cols>`, then `ensure_taxonomy(Session(bind=op.get_bind()))`; downgrade reverses. **Step 5:** verify up/down on a scratch copy of the DB (never the live file) and `pytest tests/test_position_models.py tests/test_taxonomy.py tests/test_schema_strictness.py tests/test_debt_model.py -v` → PASS.
- [ ] **Step 6: Ready to commit:** `git commit -m "feat(loans): position tables, Debt fields, savings category group"`

---

### Task 2: Positions-only extraction with reconciliation

**Files:** Create `app/services/position_extraction.py`; Modify `tests/test_schema_strictness.py`; Test `tests/test_position_extraction.py`.

**Interfaces — Produces:**
```python
@dataclass class ComponentRow:            # one raw line of a loan table; the LLM only transcribes, code aggregates
    date: date; instalment_number: int
    component: str                        # "capital" | "interest" | "insurance_life" | "insurance_building"
    amount: float                         # positive magnitude
    balance_after: Optional[float]        # "Saldo em Dívida" / "Empréstimo" balance shown on that line, positive

@dataclass class Instalment:              # produced by aggregate_components
    number: int; date: date               # date of the earliest piece
    capital: float; interest: float; insurance: float; balance_after: Optional[float]   # balance after the LAST capital piece

@dataclass class ExtractedLoan:           # from a monthly statement block
    number: str                           # digits only, e.g. "000100000000001"
    label: str                            # e.g. "CRÉDITO HABITAÇÃO"
    rate_percent: Optional[float]; term_months: Optional[int]; capital_granted: Optional[float]
    start_date: Optional[date]; capital_remaining: float; opening_balance: Optional[float]
    rows: list[ComponentRow]              # the statement-month movement lines (CAPITAL / JUROS per PRESTAÇÃO Nº)
    next_due_date: Optional[date]; next_instalment_number: Optional[int]
    next_instalment: Optional[float]; next_capital: Optional[float]; next_interest: Optional[float]

@dataclass class ExtractedFund: account_ref: str; holder: Optional[str]; label: str; units: Optional[float]
                                 invested: Optional[float]; value: float
                                 periodic_amount: Optional[float]; next_periodic_date: Optional[date]
@dataclass class ExtractedBalance: kind: str  # "deposit"|"investments_total"|"loans_total"|"card"
                                   label: str; amount: float
@dataclass class ExtractedPositions: as_of: date; loans: list[ExtractedLoan]; funds: list[ExtractedFund]
                                      balances: list[ExtractedBalance]; warnings: list[str]
@dataclass class ExtractedLoanHistory: rows: list[ComponentRow]; warnings: list[str]   # online-banking printout, one loan, no number
class PositionExtractionError(Exception)

async def extract_statement_positions(file_path: str, gateway=None) -> ExtractedPositions
async def extract_loan_history(file_path: str, gateway=None) -> ExtractedLoanHistory
def aggregate_components(rows: list[ComponentRow]) -> list[Instalment]        # sums pieces per instalment number
def reconcile_loan(rows: list[ComponentRow], opening_balance: Optional[float] = None) -> list[str]
def reconcile(positions: ExtractedPositions) -> list[str]
```
Rules:
- Both gateway calls mirror `extract_statement_transactions` (`VISION_EXTRACTION`, `build_content_block`, `response_schema`, `stop_reason == "max_tokens"` → `PositionExtractionError`). Prompts (English, naming the Portuguese labels from "What Pedro's real files contain"): extract ONLY loans / fund accounts / deposit and investment totals / card balances (statement) or ONLY the loan table rows (printout); ignore every account movement, purchase and transfer; **transcribe each table line as its own row — never add rows together**; numbers are Portuguese-formatted (`1.234,56`) and must be returned as plain positive JSON numbers; components are mapped `CAPIT./CAPITAL`→`capital`, `JUROS`→`interest`, `SEGURO`→`insurance_life`, `SEG ED`→`insurance_building`; `balance_after` is the balance column value on that line when present, else null; loan numbers digits only; use null where not shown; never guess.
- Schemas (`_POSITIONS_SCHEMA`, `_LOAN_HISTORY_SCHEMA`): required keys are only those the parser indexes (`as_of`; `rows`), everything else nullable/`.get`-defaulted (strictness rule in `tests/test_schema_strictness.py`).
- `aggregate_components`: group by `instalment_number`; `capital`/`interest`/`insurance` = sums (insurance = life + building); `date` = earliest row date; `balance_after` = the balance on the capital row with the **lowest** balance (the last piece); ordered by number.
- `reconcile_loan(rows, opening_balance)`: (1) **continuity**: sorted by instalment number, `balance_after[n-1] − capital[n] == balance_after[n]` within €0.02 for consecutive instalments that both have balances; (2) if `opening_balance` is given: `opening_balance − capital[first] == balance_after[first]`; (3) no negative amounts; (4) an instalment with capital but no interest (or the reverse) is a problem ("incomplete instalment nº N").
- `reconcile(positions)`: runs `reconcile_loan` per loan (`rows`, `opening_balance`), plus per loan `next_capital + next_interest == next_instalment` (±€0.02) when all known, `capital_remaining ≥ 0`, and per fund `value ≥ 0`. Problems are strings like `"loan 0001…: instalment 37 balance 90000.00 - capital 200.00 != 89000.00"`.
- A reply that parses but has problems returns normally with `warnings` filled; the caller decides (Task 3).

- [ ] **Step 1: Failing tests** — `tests/test_position_extraction.py` with `tests/fakes/fake_gateway.py`. Synthetic data only (invent numbers; reuse the *shapes* above): a statement loan block (opening 1,000.00; PRESTAÇÃO nº 37 as two CAPITAL pieces 150.00 + 50.00 and JUROS 80.00 → balance 800.00; next instalment 380.00 = 200.00 + 180.00) → happy path to the dataclasses; request carries the PDF attachment and the schema; `max_tokens` → error; non-JSON → error; missing optional keys parse (`loans == []`). `aggregate_components`: the pieces-of-one-instalment case (205.96 + 80.00 + 19.76 capital + 280.95 interest → capital 305.72, interest 280.95, date of the first piece, balance of the lowest-balance capital row) and insurance summing. `reconcile_loan`: continuity passes on a consistent 3-instalment series and flags a broken one; negative amount flagged; capital-without-interest flagged. `reconcile`: bad next_capital+next_interest flagged. Printout test: a synthetic reply shaped like descarga (10) rows parses via `extract_loan_history`. Append to `tests/test_schema_strictness.py`: null-heavy but parser-valid replies validate against both schemas (`{"as_of": "2026-07-31", "loans": [{"number": "1", "capital_remaining": 1.0}], "funds": null, "balances": null}`; `{"rows": [{"date": "2026-10-02", "instalment_number": 38, "component": "capital", "amount": 1.0}]}`).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** run → PASS; `pytest tests/test_schema_strictness.py tests/test_extraction.py -q` PASS.
- [ ] **Step 5: Manual accuracy check (not automated; record the outcome in the task report):** if the gateway is reachable from the dev machine, run both extractors on the real files in `~/Downloads` (read-only; **never copy them into the repo**; the latest monthly statement and one loan-history printout) and compare by eye against the target figures in the controller's private rollout checklist (kept outside the repo): a statement loan's remaining capital, an instalment's capital/interest split, the third loan's last balance, a fund value. If the gateway isn't reachable, say so; the check moves to Task 9.
- [ ] **Step 6: Ready to commit:** `git commit -m "feat(loans): positions and loan-history extraction with reconciliation"`

---

### Task 3: Store positions and loan histories (idempotent), document categories

**Files:** Create `app/services/position_store.py`; Modify `app/domains/financials/categories.py`, `app/domains/financials/spec.py`, `app/domains/financials/handler.py`; Test `tests/test_position_store.py`.

**Interfaces:**
- Consumes: Task 1 models, Task 2 (`extract_statement_positions`, `extract_loan_history`, `aggregate_components`, `reconcile`, `reconcile_loan`), existing `ingest()`/`IncomingFile`/`Classification` (see `app/routers/bills.py`).
- Produces:
```python
async def process_positions_document(session: Session, document: Document, gateway=None) -> PositionExtraction   # category statement_positions
async def process_loan_history_document(session: Session, document: Document, gateway=None) -> PositionExtraction # category loan_history
def apply_positions(session: Session, document: Document, positions: ExtractedPositions) -> dict               # counts
def apply_loan_history(session: Session, document: Document, debt: Debt, instalments: list[Instalment]) -> dict
def match_loan_for_history(session: Session, instalments: list[Instalment]) -> Optional[Debt]
def assign_history_to_loan(session: Session, extraction: PositionExtraction, debt: Debt) -> dict  # applies payload_json, no LLM call
def create_loan_from_assignment(session: Session, number: str, name: str, loan_type: str) -> Debt   # manual loan (e.g. the third loan)
```
Rules:
- New financials categories `FinancialsCategory.POSITIONS = "statement_positions"` ("Statement – loans & savings positions only") and `FinancialsCategory.LOAN_HISTORY = "loan_history"` ("Online-banking loan movements printout"), registered in `spec.py`; `process_financials_document` routes them to the two processors, **never** to transaction extraction.
- `process_positions_document`: if a `PositionExtraction` row exists and is `ok` → return it (idempotent). Else extract; on `PositionExtractionError` or any `reconcile` problem → store `PositionExtraction(status="failed", error=…)`, `mark_needs_attention(document, reason)`, apply nothing. Else `apply_positions` (then `link_all_unlinked` from Task 4 once it exists) and `status="ok"`.
- `process_loan_history_document`: extract rows → `aggregate_components` → `reconcile_loan(rows)`; problems → `failed` as above. Else `match_loan_for_history`: a known loan matches when **every** instalment of the printout that already exists as a `LoanMovement` for that loan agrees on (number, capital, interest within €0.02) and at least **two** instalments agree; if exactly one loan matches → `apply_loan_history`, `ok`. If none or several match → store `PositionExtraction(status="needs_loan", payload_json=<instalments as JSON>)` and do **not** mark the document as needing attention (it awaits assignment in the UI, Task 8).
- `assign_history_to_loan` loads `payload_json`, applies it to the chosen `Debt`, sets `ok`. `create_loan_from_assignment(number, name, loan_type)` creates a `FORMAL`, `OWED_BY_US` `Debt` with `external_number = digits_only(number)`, `name`, `loan_type`, `original_amount = capital_granted or first balance_after + first capital`, `interest_rate=None`.
- `apply_positions`: for each loan, find `Debt` by `external_number == number` or create one (`kind=FORMAL`, `direction=OWED_BY_US`, `name=f"{label} …{number[-4:]}"`, `external_number`, `capital_granted`, `term_months`, `start_date`, `interest_rate=rate_percent`, `loan_type` = `"mortgage"` if label contains "HABITA" else `"personal"` if it contains "EMPR" or "PESSOAL" else `"other"`, `original_amount=capital_granted or capital_remaining`). Insert `LoanSnapshot` for `(debt, as_of)` if absent; set `Debt.current_balance`/`interest_rate` **only if this `as_of` is newer than every existing snapshot and every existing movement date** for that debt (so uploading older months never rolls the balance back). Movements: `aggregate_components(loan.rows)` upserted by `(debt_id, instalment_number)` (see below). `capital_remaining == 0` and newest → `status="closed"`.
- `apply_loan_history`: upsert each `Instalment` as a `LoanMovement`; update `Debt.current_balance` from the **newest** instalment's `balance_after` when that is newer than any snapshot/movement already recorded; if `interest_rate` is None, set it to the **implied rate** `interest × 12 ÷ (balance_after + capital)` of the newest instalment, rounded to 3 decimals. The loan has no `LoanSnapshot` row (no statement block) unless a statement later provides one.
- **Upsert rule** for a movement that already exists: if capital/interest agree within €0.02 keep the existing row, but fill `insurance`/`balance_after` if they were empty; if they disagree, keep the existing row and add a warning to the returned counts dict (`"conflicts": n`) — never overwrite silently.
- Funds → `SavingsSnapshot` (idempotent on `(account_ref, label, as_of)`); balances → `BalanceSnapshot` (idempotent).
- Returns counts `{"loans": n, "movements": n, "snapshots": n, "funds": n, "balances": n, "conflicts": n}` of **newly inserted** rows (`conflicts` = disagreements found).
- Uploading a file whose content hash already exists as an ordinary `statement` document (the 49 already-ingested statements): `ingest` reports a duplicate; the upload route (Task 8) still calls the processor on the existing `Document`. Cover it in the Task 8 route test.

- [ ] **Step 1: Failing tests** — `tests/test_position_store.py` (synthetic data only; use the Task 2 builders/fake gateway): (a) `apply_positions` creates the Debt (name, external_number, type mortgage, rate), snapshot, aggregated movements (a two-piece capital instalment becomes ONE row with summed capital), fund and balances; (b) applying the same positions twice inserts nothing the second time; (c) an **older** `as_of` after a newer one keeps `Debt.current_balance` from the newer; (d) `capital_remaining == 0` closes the loan; (e) `process_positions_document` with a gateway reply that fails `reconcile` stores `PositionExtraction(failed)`, leaves the document `needs_attention`, creates no Debt; (f) a second call on an `ok` extraction does not call the gateway again; (g) a `statement_positions` document through `process_financials_document` creates **no** `Transaction` rows; (h) `loan_history` for a printout whose instalments match an existing loan's movements applies to that loan (`ok`), **and applying a duplicate printout again changes nothing** (descarga 8 vs 9); (i) a printout matching no loan → `needs_loan` with `payload_json`, document not `needs_attention`; `create_loan_from_assignment` + `assign_history_to_loan` then fills movements and sets balance and the implied rate (compute the expected rate in the test from the synthetic numbers independently); (j) a conflicting duplicate instalment (different capital) is kept as is and reported in `conflicts`; (k) a printout and a statement describing the same instalment merge into one row with `insurance` and `balance_after` filled.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement; keep `FinancialsHandler.withdraw` consistent: deleting a positions/loan-history document must delete the movements, snapshots and extraction rows that came from that document (add to `withdraw`, with a test). **Step 4:** run → PASS plus `pytest tests/test_financials_handler.py tests/test_financials_refile.py tests/test_domain_registry.py -q`.
- [ ] **Step 5: Ready to commit:** `git commit -m "feat(loans): store statement positions and loan histories idempotently"`

---

### Task 4: Automatic loan linking (instalments → loan, filed under Loans)

**Files:** Create `app/services/loan_linking.py`; Modify `app/services/classification_engine.py` (call after classification), `app/services/bankapi/sync.py` only if classification is not already the single path (check first); Test `tests/test_loan_linking.py`.

**Interfaces — Produces:**
```python
def digits_only(text: str) -> str
def find_loan_for(session: Session, provider_text: str) -> Optional[Debt]
def link_transaction_to_loan(session: Session, txn: Transaction) -> bool          # True if linked now
def link_all_unlinked(session: Session) -> int                                    # backfill; returns count
```
Rules:
- A statement-loan `Debt` (has `external_number`) matches a transaction when `external_number` (digits) is a substring of `digits_only(txn.provider)` **and** the debit direction is out. Ambiguity (two loans match) → no link.
- On match: `txn.debt_id = debt.id`, and file the transaction with `file_transaction` (Plan 1) under `loans-debt.loan-repayments.mortgage` for `loan_type == "mortgage"`, `…personal-loans` for `"personal"`, else `…personal-loans`; set `txn.debt_candidate_reviewed = True`. Never overwrite an existing different `debt_id`.
- Idempotent: a transaction already linked is returned `False` and untouched.
- `classify_transaction` (single entry point used by ingestion and sync) calls `link_transaction_to_loan` after merchant resolution so new bank-sync rows link at once; `link_all_unlinked` is called at the end of `apply_positions`' caller (Task 3's `process_positions_document`) so a freshly created loan back-links all history.
- Does **not** touch `Debt.current_balance` (snapshots own the balance for statement loans).

- [ ] **Step 1: Failing tests:** `digits_only("COB.REC.31.000100000000001/ 35") == "31000100000000001350"`-style (assert substring match works for the real text pattern with a synthetic number); a debit with the loan's number in `provider` gets `debt_id`, node `…mortgage` and `category_id`/legacy agree; a credit with the same text is NOT linked; two loans where one number is a substring of the other's text → no link; a transaction already linked elsewhere is untouched; `link_all_unlinked` links many and returns the count; end-to-end: `classify_transaction` with the shared fake gateway for a new instalment row links it (production path, not hand-set). Add: `process_positions_document` back-links existing transactions (assert one pre-existing instalment row gets `debt_id` after processing).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** `pytest tests/test_loan_linking.py tests/test_classification_engine.py tests/test_bank_sync.py tests/test_position_store.py -q` → PASS.
- [ ] **Step 5: Ready to commit:** `git commit -m "feat(loans): link bank instalments to loans automatically"`

---

### Task 5: Loan maths and informal loans

**Files:** Create `app/services/loan_math.py`; Modify `app/routers/transactions.py` (`link_debt`), `app/services/classification_engine.py`; Test `tests/test_loan_math.py`, `tests/test_transactions_router.py` (append).

**Interfaces — Produces:**
```python
@dataclass class LoanSummary:
    debt_id: int; name: str; loan_type: Optional[str]; rate_percent: Optional[float]
    capital_granted: Optional[float]; capital_remaining: float; as_of: Optional[date]
    paid_capital_total: float; paid_interest_total: float; paid_insurance_total: float   # from LoanMovement rows
    last_insurance: float                                       # insurance part of the latest instalment
    next_due_date: Optional[date]; next_instalment: Optional[float]   # capital + interest only (insurance is debited separately)
    projected: bool                                             # True when next_* is derived from the latest movement, not a statement
    payoff_date: Optional[date]; months_left: Optional[int]; stale_days: Optional[int]
def months_to_payoff(balance: float, annual_rate_percent: float, instalment: float) -> Optional[int]   # None if instalment <= monthly interest or inputs invalid
def summarize_loan(session: Session, debt: Debt, today: date) -> LoanSummary
def summarize_all(session: Session, today: date) -> list[LoanSummary]       # statement loans, active first
def informal_balance(session: Session, debt: Debt) -> Decimal             # original_amount adjusted by linked transactions
```
(`DebtMatchRule` already exists from Task 1.)
Rules:
- `months_to_payoff`: standard annuity: `r = rate/1200`; if `r == 0` → `ceil(balance/instalment)`; else `n = -ln(1 - balance*r/instalment) / ln(1+r)` rounded up; `None` if `instalment <= balance*r` or balance ≤ 0 → 0.
- `summarize_loan` uses the newest `LoanSnapshot` when one exists. **A loan with no snapshot (the third loan, known only from printouts)** uses `Debt.current_balance` as remaining capital, the newest movement date as `as_of`, `next_instalment = latest movement's capital + interest`, `next_due_date` = latest movement date + one month (same day-of-month, clamped), `projected = True`; its rate is `Debt.interest_rate` (implied, see Task 3); `payoff_date` = `next_due_date` plus `months_left − 1` months; `stale_days = (today − as_of).days`.
- Informal debts (`external_number is None`): `informal_balance` = `original_amount` minus linked transactions that repay it plus linked transactions that increase it, by direction: for `OWED_BY_US` a DEBIT linked reduces the balance and a CREDIT increases it; for `OWED_TO_US` the reverse; never below 0. The existing `link_debt` route (Needs Review "Possible debt transfers") additionally stores a `DebtMatchRule(debt_id, normalized_key=normalize_provider(txn.provider))` and updates `Debt.current_balance = informal_balance(...)`; `classify_transaction` links later transactions whose normalized provider equals a rule's key (and recomputes that debt's balance) — fully automatic after the first manual link.

- [ ] **Step 1: Failing tests:** `months_to_payoff(100000, 3.0, 500)` against a hand-computed value (compute it independently in the test with a loop that amortises month by month — do not reuse the formula); zero rate; instalment below interest → `None`; `summarize_loan` totals `paid_capital_total/paid_interest_total/paid_insurance_total` from movements and picks the newest snapshot; a loan with movements but **no snapshot** gets balance from `Debt.current_balance`, `projected=True`, next instalment = latest capital+interest, due date one month after the latest movement; informal balance both directions; the `link_debt` route creates a rule and a second matching transaction through `classify_transaction` auto-links and lowers the balance.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** `pytest tests/test_loan_math.py tests/test_transactions_router.py tests/test_debt_model.py tests/test_classification_engine.py -q` → PASS.
- [ ] **Step 5: Ready to commit:** `git commit -m "feat(loans): payoff maths, informal loan balances and auto-link rules"`

---

### Task 6: The "Loans & Savings" page

**Files:** Create `app/routers/loans.py`, `app/templates/loans/{index,detail}.html`; Modify `app/main.py`, `app/domains/financials/spec.py` (`NavLink("Loans & Savings", "/financials/loans")`), `app/templates/base.html` (page styles); Test `tests/test_loans_router.py`.

**Interfaces — Consumes:** `summarize_all`, `summarize_loan`, `SavingsSnapshot`, `BalanceSnapshot`. **Produces:** `GET /financials/loans` (index), `GET /financials/loans/{debt_id}` (detail), a `savings_summary(session, today)` helper in `loan_math.py`:
```python
@dataclass class SavingsLine: holder: Optional[str]; label: str; invested: Optional[float]; value: float; gain: Optional[float]; as_of: date
@dataclass class PositionTotals: savings_total: float; deposits_total: float; debt_total: float; cards_total: float; net_position: float; as_of: Optional[date]; stale_days: Optional[int]
def savings_lines(session: Session) -> list[SavingsLine]        # newest snapshot per (account_ref, label)
def position_totals(session: Session, today: date) -> PositionTotals
```
Layout (mirror the Overview's `fc-*` look): header with three cards **Savings & investments** (muted green), **Debt** (muted red: loans + cards), **Net position**, and a freshness line "Data as of 31 Jul 2026" turning amber when `stale_days > 150`. Section **Loans**: one row per loan: name, rate, capital remaining with a thin bar (remaining/granted), next instalment (date, total, capital/interest split, plus "+ €X insurance debited separately"; marked "estimated from last payment" when `projected`), paid capital, interest and insurance to date, payoff estimate ("≈ 22 yrs, Aug 2048"); closed loans collapsed below. Section **Savings & investments**: each fund holder/label: invested, value, gain (€ and %), monthly subscription and next date. Section **Deposits & cards**: balance lines. Loan row links to the detail page: instalments table (nº, date, capital, interest, insurance, balance after), and a month-by-month remaining-capital bar strip like the Overview cash-flow bars. Empty state: "No loan or savings data yet — upload your latest Santander statements with the + button" with the same `+` link as the reminder (Task 8). Informal loans listed in their own small section with balance and linked transactions link (`/financials/transactions?debt_id=N`).

- [ ] **Step 1: Failing tests** (`tests/test_loans_router.py`; build data through `apply_positions` with synthetic `ExtractedPositions`, and informal debts through the `link_debt` route): index shows the loan name, capital remaining, rate, next instalment and a payoff estimate; savings section shows value and gain; totals: `net_position == savings_total + deposits_total − debt_total − cards_total`; amber stale marker when the newest snapshot is older than 150 days (test through `position_totals` with a fixed `today`, and the template via a `today` query override is NOT allowed — assert the helper and that the template renders the marker class when `stale_days` is passed in a direct template render); detail page lists one row per instalment (nº, date, capital, interest, insurance, balance after) with partial payments already summed; unknown loan id → 404; empty database → 200 with the empty-state text and the `+` link; nav link present.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement routes, helpers, templates. **Step 4:** `pytest tests/test_loans_router.py tests/test_dashboard_router.py -q` → PASS.
- [ ] **Step 5: Look at it** on a scratch DB with synthetic positions applied via a small throwaway script, in the browser pane: desktop width and dark mode; do not add a viewport tag or global CSS.
- [ ] **Step 6: Ready to commit:** `git commit -m "feat(loans): Loans & Savings page"`

---

### Task 7: Overview integration — Debt card and loan instalments in Net

**Files:** Modify `app/services/overview_service.py` (`_debt_net_position`, `get_debt_kpi`), `app/services/budget_service.py` (`BudgetOverview`, `get_budget_overview`), `app/templates/dashboard/_budget_panel.html`; Test `tests/test_overview_service.py`, `tests/test_budget_service.py`, `tests/test_dashboard_router.py` (append).

**Interfaces — Produces:** `BudgetOverview` gains `loan_instalments: float` (expected instalments for the period) and `loan_instalments_paid: float`; `net` now subtracts `loan_instalments`.

Rules:
- Debt KPI: value = sum of the newest `capital_remaining` of every active statement loan + informal balances owed by us − informal balances owed to us (keep the existing `max(0, …)`), plus the latest card balances; `drill_down_url` → `/financials/loans`. If no snapshots exist, behave exactly as before (existing tests must pass unchanged).
- Month view `loan_instalments` = sum over active loans (statement loans and printout-only loans; for the latter `projected` next instalment) of the loan's `next_instalment` if its `next_due_date` falls in the current month **and no linked instalment transaction has been paid this month yet**, plus instalments already paid this month (`loan_instalments_paid`, from `Transaction.debt_id` rows of that month under loan nodes, DEBIT, signed like spend). Year view: remaining months projected at the latest `next_instalment` each (stop at `payoff_date`), plus paid YTD. Informal loans contribute only what has actually been paid.
- Net caption changes from "before loan repayments" to "after loan instalments €X" when `loan_instalments > 0`; when there are no loans it stays as Plan 2 wrote it. Show a "Loan instalments" row under the Spending card caption (`+ €X loan instalments`, link to `/financials/loans`). Spending's own figure is unchanged (instalments are not budgeted spend).

- [ ] **Step 1: Failing tests:** Debt KPI uses snapshot balances and links to `/financials/loans`; with no snapshots the old behaviour is unchanged; month view: a loan due this month and unpaid adds its instalment to `loan_instalments` and lowers `net.expected`; once a linked debit exists this month the instalment counts once (as paid), not twice; a closed loan contributes nothing; year view projects remaining months and stops at the payoff month; caption text switches; no loans → caption as before.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** `pytest tests/test_overview_service.py tests/test_budget_service.py tests/test_dashboard_router.py -q` → PASS.
- [ ] **Step 5: Ready to commit:** `git commit -m "feat(overview): real debt balances and loan instalments in net"`

---

### Task 8: Multi-PDF upload ("+") and the reminder

**Files:** Create `app/services/statement_reminder.py`, `app/templates/loans/upload.html`, `app/jobs/statement_reminder.py`, `deploy/systemd/home-hub-reminder.service`, `deploy/systemd/home-hub-reminder.timer`; Modify `app/routers/loans.py` (upload routes), `app/services/overview_service.py` (`get_needs_attention`), `app/templates/dashboard/_needs_attention.html`, `app/config.py` (`HUB_REMINDER_TELEGRAM_NAME`, default `"Pedro"`), `deploy/install_user_units.sh` if it lists units; Test `tests/test_statement_reminder.py`, `tests/test_loans_router.py` (append).

**Interfaces — Produces:**
```python
REMINDER_TEXT = "Reminder: to keep loan and savings data updated, you have 30 days to download the latest digital bank statements from Santander and share them with me."
@dataclass class ReminderState:
    due: bool; overdue: bool; cycle_start: Optional[date]; deadline: Optional[date]; last_positions: Optional[date]
def get_statement_reminder(session: Session, today: date) -> ReminderState
def reminder_to_send(session: Session, today: date) -> bool         # due and not yet sent for this cycle
def mark_reminder_sent(session: Session, today: date) -> None
GET  /financials/loans/upload              # page with TWO multi-file inputs (accept=.pdf, multiple): "Monthly statements" and "Loan history printouts (Netbanco: Consulta Movimentos Empréstimo)"
POST /financials/loans/upload              # fields: statements: list[UploadFile], histories: list[UploadFile]; returns the per-file results page
POST /financials/loans/assign              # form: extraction_id + (debt_id | new loan: number, name, loan_type) → assign_history_to_loan
```
Rules:
- `last_positions` = newest of: `as_of` of any `LoanSnapshot`/`SavingsSnapshot`/`BalanceSnapshot`, and `movement_date` of any `LoanMovement`. `cycle_start` = `last_positions` + 5 calendar months (clamped to month end); `deadline = cycle_start + 30 days`. `due = today >= cycle_start`; `overdue = today > deadline`. With no data at all → `due = False` (the page's empty state already prompts the first upload; no reminder spam before the first upload).
- Overview Needs-attention gets one item while `due`: the exact `REMINDER_TEXT` followed by a `+` link to `/financials/loans/upload` (amber, red when overdue). It disappears as soon as a newer upload moves `last_positions` forward.
- Telegram push: `app/jobs/statement_reminder.py` (`python -m app.jobs.statement_reminder`, run daily by the systemd timer at 09:00 Europe/Lisbon, `Persistent=true`) sends `REMINDER_TEXT` once per cycle to the allowed Telegram user whose name equals `HUB_REMINDER_TELEGRAM_NAME` (case-insensitive) using the bot token via the Bot API (`telegram.Bot(token).send_message`), then `mark_reminder_sent` (`ReminderLog(key="statement_positions", cycle_start=…)`; a cycle with a log row is never re-sent). No recipient or no token → log a warning and send nothing (the Overview item still shows). Never crash the timer on a Telegram error: log and exit 0 without marking sent.
- Upload route: for each PDF in `statements`: `ingest(session, IncomingFile(name, bytes, DocumentSource.MANUAL, user_email), Classification(domain=Domain.FINANCIALS, category="statement_positions", fields={}))`; for each in `histories`: same with `category="loan_history"`. If the file's content hash already exists as another financials document (duplicate, e.g. an already-ingested monthly statement, or the same printout downloaded twice), take the existing document and run the matching processor on it (a `statement_positions` run on an old `statement` document is fine; a duplicate that already has an `ok` extraction reports "already read"). Non-PDF files are rejected with a per-file message. **Processing order inside one request: all statements first, then histories** (so a printout can be matched against loans the statements just created). Results page: one row per file — "read: N loans, N instalments, N funds" / "already read" / failed with the reason (incl. reconciliation messages) / **"needs a loan"** with an inline form (choose an existing loan, or "new loan": account number as it appears in the `COB.REC.31.<number>` text of its transactions, a name, type mortgage/personal/other) that posts to `/financials/loans/assign`; plus a link back to the Loans & Savings page. Process files sequentially (one gateway call per file); cap at 12 files per request (400 beyond that).
- The upload page text lists what to download: the monthly statements, and — for loans that have no block in the statements — the loan-history printout from Netbanco; the Loans page shows, per loan, which source its data came from and a hint "update from the online loan history" for printout-only loans.

- [ ] **Step 1: Failing tests** (`tests/test_statement_reminder.py`, fixed `today` values): no data → not due; `last_positions = 2026-02-28` (from a snapshot) → `cycle_start = 2026-07-28`, due on that day, not before, `deadline = 2026-08-27`, overdue after; month-end overflow (`last_positions = 2026-08-31` → cycle start `2027-01-31`; `2026-09-30` → `2027-02-28`); uploading a newer snapshot **or a loan-history printout with newer instalments** clears `due`; `reminder_to_send` true once, false after `mark_reminder_sent`, true again for the next cycle; the job with a fake Telegram sender (inject the sender function) sends the exact `REMINDER_TEXT` to the configured name, sends nothing when the name isn't an allowed user, and doesn't mark sent when the sender raises. Router tests (append to `tests/test_loans_router.py`): Overview shows the reminder text and a `+` link to `/financials/loans/upload` only when due; the upload page has two inputs, both `multiple` and `accept=".pdf"`; POSTing two statements + two printouts (fake gateway, synthetic) applies positions and histories and the results page lists all four; statements are processed before histories even if the form lists them last (assert via the gateway's request order); re-posting a file whose hash already exists as an ordinary statement still produces positions; **the same printout posted twice** reports "already read" the second time and changes no rows; a printout matching no loan shows "needs a loan"; posting `/financials/loans/assign` with a new loan (number, name, type) creates the loan, fills its movements and links its existing `COB.REC` transactions (Task 4); a `.txt` upload is rejected; 13 files → 400.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement; the timer/service units follow `home-hub-banksync.*` (user units, `EnvironmentFile=/srv/home-hub/app/.env`). **Step 4:** `pytest tests/test_statement_reminder.py tests/test_loans_router.py tests/test_dashboard_router.py tests/test_deploy_units.py -q` → PASS; the full suite → PASS.
- [ ] **Step 5: Look at it:** upload two synthetic PDFs on a scratch DB in the browser pane; check the Overview reminder item and its `+` link.
- [ ] **Step 6: Ready to commit:** `git commit -m "feat(loans): multi-PDF statement upload, 5-monthly reminder with Telegram push"`

---

### Task 9: Roll out (needs Pedro's explicit word to push/deploy)
Order matters: the deploy runs `alembic upgrade head` on push, and the units must be installed by the deploy script (check `docs/SYSADMIN.md`; background jobs are systemd **user** units for `home-hub`, with `loginctl enable-linger home-hub` already done).
- [ ] Full suite green; migration `c3a9d4e7f215` up/down on a fresh scratch copy of the live DB.
- [ ] **Before the push:** back up the live DB on the VPS (`docs/SYSADMIN.md` path rules).
- [ ] Pedro says "push" → `git subtree push --prefix="Home & Family" home_hub main`; verify the new timer is active (`systemctl --user list-timers`), as `home-hub`.
- [ ] Pedro uploads, via the `+` link, the monthly statements of the last ~6 months (duplicate copies are ignored) and the loan-history printouts from Netbanco (identical or overlapping printouts are ignored; a printout that matches no known loan asks which loan: create the third loan with its account number from the `COB.REC.31.<number>` text of its transactions, a name and a type, then confirm). Verify on the Loans & Savings page against the real documents (target figures: private rollout checklist, outside the repo): each loan's remaining capital and rate (the revised next-instalment rate, not the last applied one), the instalment splits, the third loan's balance and derived rate, and the fund values.
- [ ] After the first upload: run `link_all_unlinked` effect is automatic; spot-check the Transactions list filtered by `debt_id` for each loan; confirm `COB.REC` rows now sit under Loans › Loan repayments and the Overview Net shows "after loan instalments".
- [ ] Reminder dry run: temporarily set the cycle by hand (scratch only) or wait for the first cycle; verify the Telegram text is exact.

## Self-review
- **Spec coverage:** loans both directions ✔ (statement loans owed by us = T3–T4; informal both directions = T5) · loan repayments reconciled, many repayments per loan ✔ (movements, `debt_id` links, balances) · interest/pending capital derived from statements, no manual interest ✔ (T2–T3, reconcile) · statements without transactions ✔ (positions category, T3 test g) · fully automatic matching ✔ (T4; T5 rule after first manual link) · "Loans" page ✔ (T6, named Loans & Savings) · savings data ✔ (funds, deposits, totals; contributions get a category) · 5-monthly reminder with exact text, "+" upload link, multiple PDFs ✔ (T8) · loan payments in cash-flow forecast ✔ (T7) · interest as a cost: the instalment split lives in movements and is shown on the Loans pages; the transaction itself stays one row under Loans › Loan repayments (no transaction splitting) — **decision flagged: interest is not yet a separate line in Spending**.
- **Placeholder scan:** extraction prompt text, template markup and some test bodies are specified by rules and required cases rather than pasted code (their exact fixtures depend on Tasks 1–3 outputs); every function name, signature, rule and test case is listed. The one deliberate gap is real-PDF accuracy, which is a manual check (Task 2 step 5 / Task 9) because the gateway may not be reachable from the dev machine.
- **Type consistency:** `ExtractedPositions/Loan/Fund/Balance`, `apply_positions`, `process_positions_document`, `link_transaction_to_loan`, `LoanSummary`, `PositionTotals`, `ReminderState`, `REMINDER_TEXT`, table and unique-constraint names are used identically across tasks.
- **Known limitations:** Santander's layout may change (the reconciliation check is the guard); a loan with a mid-term extra amortisation that changes the instalment is projected at the latest `next_instalment`; interest is not separated from principal in the Spending numbers (see above).
