import pytest

from financeapp import kinds


@pytest.mark.parametrize(
    ("type_", "subtype", "name", "kind"),
    [
        ("depository", "checking", "Chase Total Checking", "checking"),
        ("depository", "savings", "Ally Online Savings", "savings"),
        ("depository", "money market", "Money Market", "savings"),
        ("depository", "cash management", "Cash Account", "savings"),
        ("depository", "cd", "12 Month Term", "cd"),
        ("depository", None, "Marcus 12-month CD", "cd"),  # typed in or uploaded: the name says it
        ("depository", None, "Certificate of Deposit", "cd"),
        ("depository", None, "High-yield savings", "savings"),
        ("depository", None, "Everyday", "checking"),
        ("depository", "hsa", "Health Savings Account", "hsa"),
        ("depository", "checking", "Retirement fund checking", "checking"),  # the subtype wins
        ("investment", "brokerage", "Individual", "brokerage"),
        ("investment", "brokerage", "Rollover IRA", "retirement"),
        ("investment", "401k", "NetApp 401(k) Plan", "retirement"),
        ("investment", "roth", "Roth", "retirement"),
        ("investment", "stock plan", "Stock Plan", "stock_plan"),
        ("investment", None, "E*TRADE Stock Plan", "stock_plan"),
        ("investment", "hsa", "HealthEquity", "hsa"),
        ("investment", None, "HealthEquity HSA", "hsa"),
        ("investment", "529", "College", "education"),
        ("investment", None, "Fidelity", "brokerage"),
        ("credit", "credit card", "Sapphire Preferred", "card"),
        ("loan", "mortgage", "Home loan", "loan"),
        ("other", None, "House", "other"),
    ],
)
def test_detect(type_, subtype, name, kind):
    assert kinds.detect(type_, subtype, name) == kind


def test_an_override_wins_but_stays_on_its_side():
    assert kinds.kind_of("depository", None, "Marcus", "cd") == "cd"
    assert kinds.kind_of("investment", "brokerage", "Joint", "retirement") == "retirement"
    assert kinds.kind_of("depository", None, "Everyday", "card") == "checking"  # owned can't become owed
    assert kinds.kind_of("credit", None, "Visa", "savings") == "card"
    assert kinds.kind_of("credit", None, "Store loan", "loan") == "loan"
    assert kinds.kind_of("depository", None, "Everyday", "nonsense") == "checking"


def test_every_kind_adds_up_in_a_net_worth_group():
    from financeapp import networth

    groups = {k for k, *_ in networth.GROUPS}
    assert set(kinds.NET_WORTH_GROUP.values()) <= groups
    assert {k for k in kinds.KEYS if kinds.NET_WORTH_GROUP[k] in networth.OWED} == kinds.OWED
