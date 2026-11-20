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

Payees named by their text alone (no entity number printed):
`PAG SERVICOS BGA` → Brave Generation Academy (School fees); `LIGAT` (Eupago*Ligat Te / Ligate) → LigaT Telecom;
`AMIGO*EASYPAY` → Amigo (Vodafone); a card-3315 purchase `COMPRA 3315 EUPAGO- INST PAG LDA PORTO` → Fixando
(Pedro: "3315 Eupago: Fixando"; the same text on Rute's card 5990 is NOT assumed to be Fixando).

Where they live (since 2026-11-20, PLAN step 9 D8): this table, the payment texts and the supermarket chains
(Intermarché, Continente, Modelo Hiper, Aldi, Lidl, Pingo Doce) are Pedro's person rules in the `rules` table: an
entity looks at the entity number and carries a `merchant_key` (`entity:27134`) so its merchant keeps its key; a
payment text and a chain look at the bank text. `app/services/payment_entities.py` reads them (`payee_of`, `chain_of`);
the first list, as loaded once by migration c9e4a2f6d018, is `app/services/payee_seed.py`. A new payee is a new row
in `rules` (no code change); switching a row off stops it. A payee rule names a new line's merchant; the category then
follows that merchant, so a person's later answer about the merchant wins. `scripts/refile_payment_entities.py`
(dry run unless `--apply`) re-files stored entries; entries Pedro has checked are left alone.
