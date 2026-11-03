"""The approved category tree (proposal of 2026-10-05) as data."""

M, Y, LOAN, NONE = "monthly", "yearly", "loan", None

# kind -> [(group, [(category, cadence, [subs])])]
# A sub is a name, or (name, cadence) when it differs from its category's cadence.
# A group may appear under several kinds (e.g. Loans & debt): each leaf keeps its own kind.
# Pedro's own structure, applied 2026-10-08 (brainstorms/2026-10-05-transaction-category-hierarchy.md).
SEED = {
"out": [
 ("Housing", [
  ("Utilities", M, ["Electricity", "Water", "Gas", "Telecom", "Streaming", "Software & apps"]),
  ("Home running costs", M, ["Condominium", "Cleaning", "Repairs & maintenance",
                                     "Furniture & appliances", "Garden & DIY"]),
 ]),
 ("Food", [
  ("Groceries", M, ["Supermarket", "Butcher, fish & bakery", "Markets"]),
  ("Eat out", M, ["Restaurants", "Cafés & Snacks", "Takeaway & delivery"]),
 ]),
 ("Transport", [
  ("Car running costs", M, ["Fuel", "Tolls & parking", "Car maintenance & repairs",
                                  ("Car inspection (IPO)", Y)]),
  ("Other transport", M, ["Public transport", "Taxi & rideshare"]),
 ]),
 ("Health", [
  ("Care", M, ["Medical appointments", "Medical exams", "Dental", "Pharmacy", "Therapy & wellness"]),
 ]),
 ("Family", [
  ("Education", M, ["School fees", ("Books, staples, …", Y)]),
  ("Sports", M, ["Sports fees", "Sports gear"]),
  ("Shopping", M, ["Clothing"]),
  ("Personal", M, ["Clothing", "Personal care", "General shopping", "Rute card (not itemised)"]),
  ("Leisure", M, ["Culture & events", "Gifts", "Betting"]),
 ]),
 ("Holidays & travel", [
  ("Holidays", Y, ["Flights", "Accommodation", "On-trip spending"]),
  ("Short breaks", Y, ["Weekend trips"]),
 ]),
 ("Insurances", [
  # The two house-loan policies are debited separately from the instalment: monthly spend,
  # filed by the loan-insurance linker (loan_insurance.py), never by the LLM.
  ("Home", Y, ["Home insurance", ("Life insurance, house loan", M),
                            ("Building insurance, house loan", M)]),
  ("Cars", Y, ["Car insurance"]),
  ("Personal", Y, ["Health insurance", ("Life insurance", M)]),
 ]),
 ("Taxes & financial costs", [
  ("Taxes", Y, ["IMI property tax", "IUC road tax", "Income tax (IRS) settlement",
                      "Social security", "Other taxes & fees"]),
  ("Financial costs", M, ["Bank & card fees"]),
 ]),
 ("Loans & debt", [
  ("Loan repayments", LOAN, ["Mortgage", "Car loan", "Personal loans"]),
  ("Interest & fees", M, ["Loan interest", "Loan fees"]),
  ("Money lent out", LOAN, ["To family", "To friends"]),
 ]),
 ("Cash & giving", [
  ("Cash", M, ["ATM withdrawals"]),
  ("Giving", Y, ["Donations"]),
 ]),
 ("Savings & investments", [
  ("Contributions", M, ["Fund / investment subscriptions", "Deposits to savings"]),
 ]),
 ("Psi expenses", [
  ("Premises", M, ["Session room rental"]),
  ("Practice costs", M, ["Platform & licences", "Professional membership"]),
 ]),
 ("Unsorted", [
  ("Needs review", NONE, ["Needs review"]),
 ]),
],
"in": [
 ("Income", [
  ("Psi", NONE, ["Sessions"]),
  ("Freelance", NONE, ["Ballet classes"]),
  ("Employment", NONE, ["Salary", "Bonus"]),
  ("Rental", NONE, ["House rent"]),
  ("Benefits & refunds from the state", NONE, ["IRS refund", "Family or other benefits"]),
  ("Gifts & other", NONE, ["Gifts received"]),
 ]),
 ("Loans & debt", [
  ("Money borrowed", LOAN, ["Mortgage drawdown", "Personal loan received"]),
  ("Money lent out", LOAN, ["From family", "From friends"]),
 ]),
 ("Savings & investments", [
  ("Earnings", NONE, ["Interest", "Dividends"]),
  ("Withdrawals", NONE, ["Fund / investment redemptions"]),
 ]),
],
"neutral": [
 ("Internal transfers", [
  ("Between my accounts", NONE, ["Santander ↔ card", "Santander ↔ Revolut", "Top-ups & card payments", "Cash paid into the account"]),
 ]),
],
}
