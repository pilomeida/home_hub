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

Wired (2026-10-19): `app/services/payment_entities.py` holds this table; `structured_merchant` uses it, so a new
line with a known entity gets its merchant and category with no model call. `scripts/refile_payment_entities.py`
(dry run unless `--apply`) re-files stored entries; entries Pedro has checked are left alone. An entity not in the
table is resolved by the classifier as before: add it to `ENTITIES` when Pedro names it.
