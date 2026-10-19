# Payment entities (Multibanco "PAG SERVICOS" entity numbers)

A line like `PAG SERVICOS 1835 27134-627088996` is a payment through Multibanco: **27134** is the entity,
**627088996** the payment reference. The entity number identifies who was paid, so it is the stable key
for tagging; the reference changes every time. Noted by Pedro 2026-10-19 for reference and tagging down the line.

| Entity | Who is paid | Category |
|---|---|---|
| 27134 | Município de Mafra | Family › Sports › Sports fees |
| 23332 | Câmara Municipal de Mafra | Taxes › Other taxes & fees |
| 40141 | LigaT Telecom | Housing › Utilities › Telecom |
| 12442 | Amigo (Vodafone) | Housing › Utilities › Telecom |
| 12605 | Fixando | Psi expenses › Practice costs › Platform & licences (Rute's licence to answer enquiries: a cost of doing her job, like the therapy room) |
| 10095 | Estado, Taxas e Impostos | Taxes › Other taxes & fees |
| 21056 | Estado, Segurança Social | Taxes › Social security |
| 20160 | Santa Casa da Misericórdia | Family › Leisure › Betting (new) |
| 22255 | LigaT Telecom (second entity; the bank prints "Eupago Ligat Te") | Housing › Utilities › Telecom |
| 21317 | Ordem dos Psicólogos | Psi expenses › Practice costs › Professional membership (new) |
| 12092 | Wamos (tranches of Matias' snow trip: we paid, he reimbursed afterwards) | Loans & debt › Money lent out › To family |
| 10597 | Hospital CUF Descobertas | Health › Care › Medical appointments |
| 11797 | Instituto dos Registos e do Notariado (fee for an official house-registry document) | Taxes › Other taxes & fees |
| 20998 | Autoestrada (toll fee) | Transport › Car running costs › Tolls & parking |

Payees named by their text alone (no entity number printed; `_TEXT_RULES` in `payment_entities.py`):
`PAG SERVICOS BGA` → Brave Generation Academy (School fees); `LIGAT` (Eupago*Ligat Te / Ligate) → LigaT Telecom;
`AMIGO*EASYPAY` → Amigo (Vodafone); a card-3315 purchase `COMPRA 3315 EUPAGO- INST PAG LDA PORTO` → Fixando
(Pedro: "3315 Eupago: Fixando"; the same text on Rute's card 5990 is NOT assumed to be Fixando).

Wired (2026-10-19): `app/services/payment_entities.py` holds this table; `structured_merchant` uses it, so a new
line with a known entity gets its merchant and category with no model call. `scripts/refile_payment_entities.py`
(dry run unless `--apply`) re-files stored entries; entries Pedro has checked are left alone. An entity not in the
table is resolved by the classifier as before: add it to `ENTITIES` when Pedro names it.
