"""The approved category tree (proposal of 2026-10-05) as data."""

from app.models.transaction import Category as L

M, Y, LOAN, NONE = "monthly", "yearly", "loan", None

# kind -> [(group, [(category, cadence, legacy, [subs])])]
SEED = {
"out": [
 ("Housing", [
  ("Utilities", M, None, ["Electricity", "Water", "Gas", "Internet & TV", "Mobile phones"]),
  ("Home running costs", M, L.HOME, ["Condominium", "Cleaning", "Household supplies"]),
  ("Home upkeep", M, L.HOME, ["Repairs & maintenance", "Furniture & appliances", "Garden & DIY"]),
  ("Property taxes & insurance", Y, L.INSURANCE, ["IMI property tax", "Home insurance", "Condominium extras"]),
 ]),
 ("Food", [
  ("Groceries", M, L.GROCERIES, ["Supermarket", "Butcher, fish & bakery", "Markets"]),
  ("Dining out", M, L.RESTAURANTS, ["Restaurants", "Cafés & takeaway", "Food delivery"]),
 ]),
 ("Transport", [
  ("Car running", M, None, ["Fuel", "Tolls & parking", "Maintenance & repairs"]),
  ("Car yearly", Y, L.INSURANCE, ["Car insurance", "IUC road tax", "Inspection (IPO)"]),
  ("Other transport", M, None, ["Public transport", "Taxi & rideshare"]),
 ]),
 ("Health", [
  ("Care", M, L.HEALTH, ["Doctors & dental", "Pharmacy", "Therapy & wellness"]),
  ("Health cover", Y, L.INSURANCE, ["Health insurance"]),
 ]),
 ("Family & education", [
  ("Children & school", M, None, ["School & childcare", "Activities & clubs", "Kids' clothes & items"]),
  ("Education", Y, None, ["Courses & training", "Books & materials"]),
 ]),
 ("Personal & lifestyle", [
  ("Personal", M, L.SHOPPING, ["Clothing & shoes", "Personal care", "General shopping"]),
  ("Subscriptions", M, L.SUBSCRIPTIONS, ["Streaming", "Software & apps", "Memberships"]),
  ("Leisure", M, None, ["Hobbies & sport", "Culture & events", "Gifts"]),
 ]),
 ("Holidays & travel", [
  ("Holidays", Y, None, ["Flights", "Accommodation", "On-trip spending"]),
  ("Short breaks", Y, None, ["Weekend trips"]),
 ]),
 ("Taxes & financial costs", [
  ("Taxes", Y, None, ["Income tax (IRS) settlement", "Social security", "Other taxes & fees"]),
  ("Banking", M, None, ["Bank & card fees", "Insurance, life & other"]),
 ]),
 ("Loans & debt", [
  ("Loan repayments", LOAN, None, ["Mortgage", "Car loan", "Personal loans"]),
  ("Interest & fees", M, None, ["Loan interest", "Loan fees"]),
  # Insurance debited separately from the instalment: ordinary monthly spend, not a loan instalment.
  ("Loan insurance", M, L.INSURANCE, ["Life insurance (loan)", "Building insurance (loan)"]),
  ("Money lent out", LOAN, None, ["Loan to family", "Loan to friends"]),
 ]),
 ("Cash & giving", [
  ("Cash", M, L.ATM_WITHDRAWAL, ["ATM withdrawals"]),
  ("Giving", Y, None, ["Donations", "Charity"]),
 ]),
 ("Savings & investments", [
  ("Contributions", M, None, ["Fund subscriptions", "Deposits to savings"]),
 ]),
 ("Unsorted", [
  ("Needs review", NONE, L.OTHER_EXPENSE, ["Needs review"]),
 ]),
],
"in": [
 ("Income", [
  ("Psi", NONE, L.INCOME, ["Sessions", "Other Psi income"]),
  ("Employment", NONE, L.INCOME, ["Salary", "Bonus"]),
  ("Rental", NONE, L.INCOME, ["House rent"]),
  ("Benefits & refunds from the state", NONE, L.INCOME, ["IRS refund", "Family or other benefits"]),
  ("Investments & interest", NONE, L.INCOME, ["Interest", "Dividends"]),
  ("Gifts & other", NONE, L.INCOME, ["Gifts received", "Other income"]),
 ]),
 ("Refunds & reimbursements", [
  ("Refunds", NONE, L.INCOME, ["Purchase refunds", "Insurance claims", "Expense reimbursements"]),
 ]),
 ("Loans & debt (in)", [
  ("Money borrowed", LOAN, L.INCOME, ["Mortgage drawdown", "Personal loan received"]),
  ("Repayments received", LOAN, L.INCOME, ["From family", "From friends"]),
 ]),
 ("Savings & investments (in)", [
  ("Withdrawals", NONE, L.INCOME, ["Fund redemptions"]),
 ]),
],
"neutral": [
 ("Internal transfers", [
  ("Between my accounts", NONE, L.TRANSFER, ["Santander ↔ card", "Santander ↔ Revolut", "Top-ups & card payments"]),
 ]),
],
}

# Utilities sub-categories carry their own legacy value (old enum had one per utility).
SUB_LEGACY = {
    "Electricity": L.ELECTRICITY, "Water": L.WATER, "Gas": L.GAS,
    "Internet & TV": L.TELECOM, "Mobile phones": L.TELECOM,
}

# Old Category value -> slug of the tree leaf it lands on when history is re-filed.
# None = ambiguous, send to Unsorted for review.
LEGACY_TO_SLUG = {
    L.ELECTRICITY: "housing.utilities.electricity",
    L.WATER: "housing.utilities.water",
    L.GAS: "housing.utilities.gas",
    L.TELECOM: "housing.utilities.internet-tv",
    L.SUBSCRIPTIONS: "personal-lifestyle.subscriptions.streaming",
    L.GROCERIES: "food.groceries.supermarket",
    L.RESTAURANTS: "food.dining-out.restaurants",
    L.ATM_WITHDRAWAL: "cash-giving.cash.atm-withdrawals",
    L.SHOPPING: "personal-lifestyle.personal.general-shopping",
    L.HEALTH: None, L.HOME: None, L.INSURANCE: None, L.INCOME: None,
    L.TRANSFER: None, L.OTHER_EXPENSE: None, L.OTHER: None,
}
