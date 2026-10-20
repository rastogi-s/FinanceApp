from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from financeapp.connectors.files import FileError, clean_merchant, money, parse

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 19)


def load(name: str):
    return parse((FIXTURES / name).read_bytes(), name, TODAY)


def test_fidelity_positions_split_by_account():
    p = load("fidelity_positions.csv")
    assert p.kind == "positions" and p.format == "Fidelity positions"
    individual, roth = p.accounts
    assert (individual.mask, individual.name, individual.institution) == ("5678", "Individual", "Fidelity")
    assert [h.symbol for h in individual.holdings] == ["SPAXX", "FXAIX", "AAPL"]  # "Pending Activity" left out
    spaxx, fxaix, _ = individual.holdings
    assert spaxx.asset_class == "Cash" and spaxx.value_cents == 123456
    assert fxaix.quantity == Decimal("100.5") and fxaix.value_cents == 2_010_000
    assert fxaix.cost_basis_cents == 1_500_000
    assert individual.balance_cents == 123456 + 2_010_000 + 850_000
    assert roth.mask == "3444" and roth.balance_cents == 250_000


def test_etrade_positions_with_cash_and_total():
    p = load("etrade_positions.csv")
    assert p.format == "E*TRADE positions"
    (a,) = p.accounts
    assert a.mask == "6621" and a.institution == "E*TRADE"
    assert [(h.symbol, h.value_cents) for h in a.holdings] == [("AAPL", 1_901_200), ("CASH", 123_456)]
    assert a.holdings[0].cost_basis_cents == 1_500_000
    assert a.file_total_cents == a.balance_cents == 2_024_656
    assert p.warnings == []


def test_bank_csv_keeps_its_signs():
    p = load("chase_checking_4417.csv")
    (a,) = p.accounts
    assert p.kind == "transactions" and not p.flipped and a.mask == "4417"
    amounts = [t.amount_cents for t in a.transactions]
    assert amounts == [-5420, 320000, -50000, -8810, -650, -650]
    # Two identical coffees on the same day are two transactions, with different ids.
    assert len({t.external_id for t in a.transactions}) == 6


def test_card_csv_with_positive_charges_is_flipped():
    p = load("amex_1008.csv")
    (a,) = p.accounts
    assert p.flipped and a.type == "credit" and a.mask == "1008"
    assert [t.amount_cents for t in a.transactions] == [-1575, 50000, -1549, -4210, -6399]
    assert a.transactions[3].merchant == "Corner Bistro"


def test_ofx_reads_transactions_and_balance():
    p = load("ally.qfx")
    (a,) = p.accounts
    assert a.mask == "2290" and a.institution == "Ally Bank" and a.balance_cents == 3_890_000
    assert [(t.date, t.amount_cents) for t in a.transactions] == [
        (date(2026, 10, 1), 4122),
        (date(2026, 10, 16), 150000),
    ]


def test_same_file_twice_gives_the_same_ids():
    a = load("chase_checking_4417.csv").accounts[0].transactions
    b = load("chase_checking_4417.csv").accounts[0].transactions
    assert [t.external_id for t in a] == [t.external_id for t in b]


def test_unknown_layouts_and_huge_files_are_refused():
    with pytest.raises(FileError, match="layout"):
        parse(b"hello,world\n1,2\n", "x.csv", TODAY)
    with pytest.raises(FileError, match="5 MB"):
        parse(b"a" * (5 * 1024 * 1024 + 1), "x.csv", TODAY)
    with pytest.raises(FileError):
        parse(bytes(range(256)) * 10, "x.bin", TODAY)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$1,234.56", Decimal("1234.56")),
        ("(12.00)", Decimal("-12.00")),
        ("-$5", Decimal("-5")),
        ("--", None),
        ("+0.60%", Decimal("0.60")),
        ("", None),
        ("abc", None),
    ],
)
def test_money(text, expected):
    assert money(text) == expected


def test_clean_merchant():
    assert clean_merchant("TRADER JOE'S #123 SAN FRANCISCO CA") == "Trader Joe's San Francisco"
    assert clean_merchant("SQ *BLUE BOTTLE COFFEE 1234") == "Blue Bottle Coffee"
    assert clean_merchant("Netflix.com") == "Netflix.com"
