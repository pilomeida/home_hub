from app.models.account import Account, AccountType


def test_create_and_read_account(session):
    account = Account(
        name="Millennium Current Account",
        institution="Millennium BCP",
        currency="EUR",
        account_type=AccountType.CHECKING,
        identifier="PT50...1234",
    )
    session.add(account)
    session.commit()
    session.refresh(account)

    fetched = session.get(Account, account.id)
    assert fetched.name == "Millennium Current Account"
    assert fetched.account_type == AccountType.CHECKING
    assert fetched.currency == "EUR"
    assert fetched.identifier == "PT50...1234"


def test_account_type_defaults_to_checking(session):
    account = Account(name="Revolut", institution="Revolut", currency="EUR")
    session.add(account)
    session.commit()
    session.refresh(account)

    assert account.account_type == AccountType.CHECKING
    assert account.identifier is None
