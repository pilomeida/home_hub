"""Every place in the spending tree the code itself relies on, by what it is for. The tree's slugs are made from its
names (app/services/taxonomy_seed.py), so renaming a node there changes its slug: tests/test_tree_slugs.py fails
when a name here no longer exists in the tree the seed builds, or when a slug is written anywhere else in the code.
Seed and history data that only lists slugs (payment entities, the tag seed, the old-to-new map) is exempt."""

UNSORTED = "unsorted.needs-review.needs-review"

# Loans
MORTGAGE = "loans-debt.loan-repayments.mortgage"
PERSONAL_LOAN_REPAYMENT = "loans-debt.loan-repayments.personal-loans"
LENT_TO_FRIENDS = "loans-debt.money-lent-out.to-friends"
REPAID_BY_FRIENDS = "loans-debt.money-lent-out.from-friends"
PERSONAL_LOAN_RECEIVED = "loans-debt.money-borrowed.personal-loan-received"
LIFE_INSURANCE_HOUSE_LOAN = "insurances.home.life-insurance-house-loan"
BUILDING_INSURANCE_HOUSE_LOAN = "insurances.home.building-insurance-house-loan"

# Cards and moves between own accounts
CARD_FEES = "taxes-financial-costs.financial-costs.bank-card-fees"
CARD_TOP_UPS_AND_PAYMENTS = "internal-transfers.between-my-accounts.top-ups-card-payments"
SANTANDER_TO_REVOLUT = "internal-transfers.between-my-accounts.santander-revolut"
RUTE_CARD_NOT_ITEMISED = "family.personal.rute-card-not-itemised"

# Keyword rules
SUPERMARKET = "food.groceries.supermarket"

# Income the budget treats as recurring (a group: every node under it counts)
RECURRING_INCOME = frozenset({
    "income.psi", "income.employment", "income.rental", "income.benefits-refunds-from-the-state",
    "savings-investments.earnings",
})

ALL = frozenset({
    UNSORTED, MORTGAGE, PERSONAL_LOAN_REPAYMENT, LENT_TO_FRIENDS, REPAID_BY_FRIENDS, PERSONAL_LOAN_RECEIVED,
    LIFE_INSURANCE_HOUSE_LOAN, BUILDING_INSURANCE_HOUSE_LOAN, CARD_FEES, CARD_TOP_UPS_AND_PAYMENTS,
    SANTANDER_TO_REVOLUT, RUTE_CARD_NOT_ITEMISED, SUPERMARKET, *RECURRING_INCOME,
})
