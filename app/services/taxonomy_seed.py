"""The approved category tree (proposal of 2026-10-05) as data."""

from app.models.transaction import Category as L

M, Y, LOAN, NONE = "monthly", "yearly", "loan", None

# kind -> [(group, [(category, cadence, legacy, [subs])])]
# A sub is a name, or (name, cadence) when it differs from its category's cadence.
# A group may appear under several kinds (e.g. Loans & debt): each leaf keeps its own kind.
# Pedro's own structure, applied 2026-10-08 (brainstorms/2026-10-05-transaction-category-hierarchy.md).
SEED = {
"out": [
 ("Housing", [
  ("Utilities", M, None, ["Electricity", "Water", "Gas", "Telecom", "Streaming", "Software & apps"]),
  ("Home running costs", M, L.HOME, ["Condominium", "Cleaning", "Repairs & maintenance",
                                     "Furniture & appliances", "Garden & DIY"]),
 ]),
 ("Food", [
  ("Groceries", M, L.GROCERIES, ["Supermarket", "Butcher, fish & bakery", "Markets"]),
  ("Eat out", M, L.RESTAURANTS, ["Restaurants", "Cafés & Snacks", "Takeaway & delivery"]),
 ]),
 ("Transport", [
  ("Car running costs", M, None, ["Fuel", "Tolls & parking", "Car maintenance & repairs",
                                  ("Car inspection (IPO)", Y)]),
  ("Other transport", M, None, ["Public transport", "Taxi & rideshare"]),
 ]),
 ("Health", [
  ("Care", M, L.HEALTH, ["Medical appointments", "Medical exams", "Pharmacy", "Therapy & wellness"]),
 ]),
 ("Family", [
  ("Education", M, None, ["School fees", ("Books, staples, …", Y)]),
  ("Sports", M, None, ["Sports fees", "Sports gear"]),
  ("Shopping", M, L.SHOPPING, ["Clothing"]),
  ("Personal", M, L.SHOPPING, ["Clothing", "Personal care", "General shopping"]),
  ("Leisure", M, None, ["Culture & events", "Gifts"]),
 ]),
 ("Holidays & travel", [
  ("Holidays", Y, None, ["Flights", "Accommodation", "On-trip spending"]),
  ("Short breaks", Y, None, ["Weekend trips"]),
 ]),
 ("Insurances", [
  # The two house-loan policies are debited separately from the instalment: monthly spend,
  # filed by the loan-insurance linker (loan_insurance.py), never by the LLM.
  ("Home", Y, L.INSURANCE, ["Home insurance", ("Life insurance, house loan", M),
                            ("Building insurance, house loan", M)]),
  ("Cars", Y, L.INSURANCE, ["Car insurance"]),
  ("Personal", Y, L.INSURANCE, ["Health insurance", ("Life insurance", M)]),
 ]),
 ("Taxes & financial costs", [
  ("Taxes", Y, None, ["IMI property tax", "IUC road tax", "Income tax (IRS) settlement",
                      "Social security", "Other taxes & fees"]),
  ("Financial costs", M, None, ["Bank & card fees"]),
 ]),
 ("Loans & debt", [
  ("Loan repayments", LOAN, None, ["Mortgage", "Car loan", "Personal loans"]),
  ("Interest & fees", M, None, ["Loan interest", "Loan fees"]),
  ("Money lent out", LOAN, None, ["To family", "To friends"]),
 ]),
 ("Cash & giving", [
  ("Cash", M, L.ATM_WITHDRAWAL, ["ATM withdrawals"]),
  ("Giving", Y, None, ["Donations"]),
 ]),
 ("Savings & investments", [
  ("Contributions", M, None, ["Fund / investment subscriptions", "Deposits to savings"]),
 ]),
 ("Psi expenses", [
  ("Premises", M, None, ["Session room rental"]),
 ]),
 ("Unsorted", [
  ("Needs review", NONE, L.OTHER_EXPENSE, ["Needs review"]),
 ]),
],
"in": [
 ("Income", [
  ("Psi", NONE, L.INCOME, ["Sessions"]),
  ("Freelance", NONE, L.INCOME, ["Ballet classes"]),
  ("Employment", NONE, L.INCOME, ["Salary", "Bonus"]),
  ("Rental", NONE, L.INCOME, ["House rent"]),
  ("Benefits & refunds from the state", NONE, L.INCOME, ["IRS refund", "Family or other benefits"]),
  ("Gifts & other", NONE, L.INCOME, ["Gifts received"]),
 ]),
 ("Loans & debt", [
  ("Money borrowed", LOAN, L.INCOME, ["Mortgage drawdown", "Personal loan received"]),
  ("Money lent out", LOAN, L.INCOME, ["From family", "From friends"]),
 ]),
 ("Savings & investments", [
  ("Earnings", NONE, L.INCOME, ["Interest", "Dividends"]),
  ("Withdrawals", NONE, L.INCOME, ["Fund / investment redemptions"]),
 ]),
],
"neutral": [
 ("Internal transfers", [
  ("Between my accounts", NONE, L.TRANSFER, ["Santander ↔ card", "Santander ↔ Revolut", "Top-ups & card payments", "Cash paid into the account"]),
 ]),
],
}

# Utilities sub-categories carry their own legacy value (old enum had one per utility).
SUB_LEGACY = {
    "Electricity": L.ELECTRICITY, "Water": L.WATER, "Gas": L.GAS, "Telecom": L.TELECOM,
    "Streaming": L.SUBSCRIPTIONS, "Software & apps": L.SUBSCRIPTIONS,
}

# Old Category value -> slug of the tree leaf it lands on when history is re-filed.
# None = ambiguous, send to Unsorted for review.
LEGACY_TO_SLUG = {
    L.ELECTRICITY: "housing.utilities.electricity",
    L.WATER: "housing.utilities.water",
    L.GAS: "housing.utilities.gas",
    L.TELECOM: "housing.utilities.telecom",
    L.SUBSCRIPTIONS: "housing.utilities.streaming",
    L.GROCERIES: "food.groceries.supermarket",
    L.RESTAURANTS: "food.eat-out.restaurants",
    L.ATM_WITHDRAWAL: "cash-giving.cash.atm-withdrawals",
    L.SHOPPING: "family.personal.general-shopping",
    L.HEALTH: None, L.HOME: None, L.INSURANCE: None, L.INCOME: None,
    L.TRANSFER: None, L.OTHER_EXPENSE: None, L.OTHER: None,
}
