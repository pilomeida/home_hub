# Payment entities (Multibanco "PAG SERVICOS" entity numbers)

A line like `PAG SERVICOS 1835 27134-627088996` is a payment through Multibanco: **27134** is the entity,
**627088996** the payment reference. The entity number identifies who was paid, so it is the stable key
for tagging; the reference changes every time. Noted by Pedro 2026-10-19 for reference and tagging down the line.

| Entity | Who is paid |
|---|---|
| 27134 | Município de Mafra |
| 40141 | LigaT Telecom |
| 12442 | Amigo (Vodafone) |
| 12605 | Fixando |
| 10095 | Estado, Taxas e Impostos |
| 21056 | Estado, Seg Social |
| 20160 | Santa Casa da Misericórdia |

Not wired into any rule yet: today these lines are resolved through the merchant memory / classifier like any
other text. When tagging them, match on the entity number, not on the merchant name the bank prints
(Eupago and other payment institutions front several of these).
