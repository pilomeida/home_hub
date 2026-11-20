"""Pedro's payee rulings as first written (docs/payment-entities.md; supermarkets 2026-10-07, entities and payment texts
2026-10-19). They were loaded once into the rules table as his person rules (PLAN step 9 D8, migration
c9e4a2f6d018); the rules table is the live copy, so a change is made there, not here. Tests load the same list
through `app.services.payment_entities.seed`."""

from app.services import tree_slugs

# A Multibanco entity number -> who was paid and the spending leaf.
ENTITIES = (
    ("27134", "Município de Mafra", "family.sports.sports-fees"),
    ("23332", "Câmara Municipal de Mafra", "taxes-financial-costs.taxes.other-taxes-fees"),
    ("40141", "LigaT Telecom", "housing.utilities.telecom"),
    ("12442", "Amigo (Vodafone)", "housing.utilities.telecom"),
    ("12605", "Fixando", "psi-expenses.practice-costs.platform-licences"),
    ("10095", "Estado, Taxas e Impostos", "taxes-financial-costs.taxes.other-taxes-fees"),
    ("21056", "Estado, Segurança Social", "taxes-financial-costs.taxes.social-security"),
    ("20160", "Santa Casa da Misericórdia", "family.leisure.betting"),
    ("22255", "LigaT Telecom", "housing.utilities.telecom"),
    ("21317", "Ordem dos Psicólogos", "psi-expenses.practice-costs.professional-membership"),
    # Tranches of Matias' snow trip, paid by us and reimbursed by him afterwards: money lent to family.
    ("12092", "Wamos", "loans-debt.money-lent-out.to-family"),
    ("10597", "Hospital CUF Descobertas", "health.care.medical-appointments"),
    ("11797", "Instituto dos Registos e do Notariado", "taxes-financial-costs.taxes.other-taxes-fees"),
    ("20998", "Autoestrada", "transport.car-running-costs.tolls-parking"),
)

# Payments whose text names the payee but prints no entity number: (pattern, key, payee, leaf).
TEXTS = (
    (r"PAG SERVICOS\s+BGA\b", "text:bga", "Brave Generation Academy", "family.education.school-fees"),
    (r"LIGAT", "text:ligat", "LigaT Telecom", "housing.utilities.telecom"),
    (r"AMIGO\*EASYPAY", "text:amigo", "Amigo (Vodafone)", "housing.utilities.telecom"),
    # a card-3315 purchase at Eupago (the card prints Eupago, not the payee) is Fixando
    (r"COMPRA\s+\*?3315\s+EUPAGO", "text:fixando", "Fixando", "psi-expenses.practice-costs.platform-licences"),
)

# Supermarket chains: always supermarket spending; the counterparty is the chain, not the store
# ('MODELO HIPER 2640-MAFR' is 'Modelo Hiper').
CHAINS = (
    (r"\bintermarch[eé]\b", "Intermarché"),
    (r"\bcontinente\b", "Continente"),
    (r"\bmodelo\b", "Modelo Hiper"),
    (r"\baldi\b", "Aldi"),
    (r"\blidl\b", "Lidl"),
    (r"\bpingo\s+doce\b", "Pingo Doce"),
)
CHAIN_LEAF = tree_slugs.SUPERMARKET
