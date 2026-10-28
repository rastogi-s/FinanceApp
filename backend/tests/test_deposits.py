from datetime import date, timedelta

from sqlalchemy import select

from financeapp import deposits, rates
from financeapp.deposits import CdInputs
from financeapp.models import Account, AccountTerms, AlertEvent, Transaction
from tests.conftest import csrf, db_of, needs_db
from tests.test_money import plaid  # noqa: F401  (the fixture)

TODAY = date(2026, 10, 27)


def test_cd_earned_so_far_and_at_maturity():
    c = deposits.cd(
        CdInputs(balance_cents=25_000_00, apy=4.1, opened=date(2026, 3, 1), term_months=12, principal_cents=25_000_00),
        TODAY,
    )
    # 240 days at 4.10% APY: $25,000 x (1.041^(240/365) - 1). The APY already includes compounding.
    assert c["earned_cents"] == round(25_000_00 * (1.041 ** (240 / 365) - 1)) == 669_33
    assert c["maturity"] == "2027-03-01" and "maturity" in c["guessed"]
    assert c["value_at_maturity_cents"] == 26_025_00
    assert c["days_left"] == 125 and c["days_total"] == 365 and c["progress"] == round(240 / 365, 3)
    assert c["status"] == "open" and c["grace_ends"] == "2027-03-08" and c["grace_typical"]
    # Nothing was credited in a transaction and the balance hasn't moved: all of it is building up inside the CD.
    assert c["credited_cents"] == 0 and c["built_up_cents"] == c["earned_cents"]
    assert c["missing"] == []


def test_cd_guesses_from_maturity_and_history():
    # Only the maturity and term typed; the bank added interest to the balance without transactions.
    c = deposits.cd(CdInputs(balance_cents=10_200_00, apy=4.0, term_months=6, maturity=date(2027, 1, 15)), TODAY)
    assert c["opened"] == "2026-07-15" and "opened" in c["guessed"]
    assert c["principal_cents"] == 10_200_00 and "principal_cents" in c["guessed"]

    # The opening deposit is in the history: it sets the opening date and the amount put in.
    c = deposits.cd(
        CdInputs(
            balance_cents=10_200_00, apy=4.0, term_months=12, first_deposit=(date(2026, 1, 10), 10_000_00),
            credited_cents=150_00,
        ),
        TODAY,
    )  # fmt: skip
    assert c["opened"] == "2026-01-10" and c["maturity"] == "2027-01-10" and c["principal_cents"] == 10_000_00
    # The balance says $200 came in, more than the $150 seen in transactions.
    assert c["credited_cents"] == 200_00
    assert c["built_up_cents"] == max(c["earned_cents"] - 200_00, 0)

    # Not enough to go on: say what's missing instead of guessing a number.
    c = deposits.cd(CdInputs(balance_cents=5_000_00, apy=None), TODAY)
    assert c["earned_cents"] is None and set(c["missing"]) == {"apy", "opened", "maturity"}


def test_cd_paid_out_penalty_and_breaking_it():
    c = deposits.cd(
        CdInputs(
            balance_cents=20_000_00, apy=3.0, opened=date(2026, 6, 1), term_months=24, principal_cents=20_000_00,
            penalty_days=90, pays_out=True,
        ),
        TODAY,
        rates.BUNDLED,
    )  # fmt: skip
    # Interest paid out each month doesn't compound, and the CD gives back just what was put in.
    assert c["earned_cents"] == round(20_000_00 * 0.03 * 148 / 365)
    assert c["value_at_maturity_cents"] == 20_000_00
    assert c["interest_total_cents"] == round(20_000_00 * 0.03 * 731 / 365)  # 2028 is a leap year
    assert c["penalty_cents"] == round(20_000_00 * 0.03 * 90 / 365)
    # 19 months left: a 2-year Treasury at 4.69% beats 3% by more than the penalty.
    b = c["break_even"]
    assert b["rate"] == 4.69 and b["gain_cents"] > 0


def test_cd_after_maturity():
    inputs = dict(balance_cents=5_000_00, apy=4.0, opened=date(2025, 10, 20), maturity=date(2026, 10, 24))
    assert deposits.cd(CdInputs(**inputs), TODAY)["status"] == "grace"
    late = TODAY + timedelta(days=10)
    assert deposits.cd(CdInputs(**inputs, auto_renew=True), late)["status"] == "renewed"
    c = deposits.cd(CdInputs(**inputs), late)
    assert c["status"] == "matured" and c["days_left"] == 0
    # Earned stops at maturity.
    assert c["earned_cents"] == c["interest_total_cents"]


def test_cd_notices():
    c = deposits.cd(CdInputs(balance_cents=1, apy=4.0, opened=date(2026, 5, 26), maturity=date(2026, 11, 26)), TODAY)
    on = date(2026, 10, 27)  # 30 days before
    [(stage, title, body)] = deposits.notices("Marcus CD", c, on)
    assert stage == "30d" and title == "Marcus CD matures in 30 days" and "Nov 26" in body
    assert "$" not in body  # never an amount
    assert deposits.notices("Marcus CD", c, on + timedelta(days=4)) == []  # too old to send late
    assert deposits.notices("Marcus CD", c, date(2026, 11, 19))[0][0] == "7d"
    assert deposits.notices("Marcus CD", c, date(2026, 11, 26))[0][1] == "Marcus CD matures today"
    [(stage, title, _)] = deposits.notices("Marcus CD", c, date(2026, 12, 2))
    assert stage == "grace" and "tomorrow" in title
    assert deposits.notices("Marcus CD", c, date(2026, 10, 1)) == []


def test_maturing_soon_for_home():
    def cd(maturity, **kw):
        inputs = CdInputs(balance_cents=5_000_00, apy=4.0, opened=date(2025, 10, 1), maturity=maturity, **kw)
        return {"cd": deposits.cd(inputs, TODAY)}

    details = {
        1: cd(date(2026, 11, 20)),  # 24 days: listed
        2: cd(date(2027, 1, 20)),  # months away: not yet
        3: cd(date(2026, 10, 24)),  # matured 3 days ago, in its grace period: listed first
        4: cd(date(2026, 9, 1), auto_renew=True),  # renewed long ago: done
        5: {"cd": None},  # a savings account
    }
    rows = deposits.maturing(details, {i: f"CD {i}" for i in details})
    assert [r["account_id"] for r in rows] == [3, 1]
    assert rows[0]["status"] == "grace" and rows[1]["days_left"] == 24 and rows[1]["name"] == "CD 1"
    assert rows[1]["value_cents"] > 5_000_00


def test_benchmarks():
    assert rates.benchmark(rates.BUNDLED, "savings")["national"] == 0.38
    b = rates.benchmark(rates.BUNDLED, "cd", 12)
    assert (b["national"], b["national_label"], b["treasury"], b["treasury_label"]) == (
        1.76, "12-month CD", 4.44, "1-year Treasury"
    )  # fmt: skip
    assert rates.benchmark(rates.BUNDLED, "money market")["national_label"] == "money market"
    page = (
        "<p>Monthly Rate Cap Information as of October 19, 2026</p><table><tr><td>Deposit Products</td><td>National"
        " Deposit Rates</td></tr><tr><td>Savings</td><td>0.38</td><td>1.13</td></tr><tr><td>Interest Checking</td>"
        "<td>0.07</td></tr><tr><td>Money Market</td><td>0.63</td></tr><tr><td>12 month CD</td><td>1.76</td></tr>"
    )
    f = rates.parse_fdic(page)
    assert f is not None and f["as_of"] == "2026-10-19" and f["savings"] == 0.38 and f["cd"] == {"12": 1.76}
    assert rates.parse_fdic("<html>Not found</html>") is None
    assert rates.parse_fred("observation_date,DGS1\n2026-10-22,4.40\n2026-10-23,\n") == (date(2026, 10, 22), 4.40)


@needs_db
def test_accounts_page_details(client, home, plaid):  # noqa: F811
    home.setup()
    r = client.post("/api/connections/plaid/start", json={"kind": "banking"}, headers=csrf(client))
    client.post("/api/connections/finish", json={"token": r.json()["token"]}, headers=csrf(client))
    today = date.today()
    cd = client.post(
        "/api/accounts/manual",
        json={"name": "Marcus 12-month CD", "institution": "Marcus", "type": "depository", "balance_cents": 25_000_00},
        headers=csrf(client),
    ).json()["accounts"][0]
    assert cd["kind"] == "cd" and cd["details"]["cd"]["missing"] == ["apy", "opened", "maturity"]

    with db_of(client).session() as db:
        checking = db.scalar(select(Account).where(Account.name == "Checking"))
        for i, (days, cents, cat) in enumerate(
            [
                (3, 120, "INCOME_INTEREST_EARNED"),
                (20, -35_00, "BANK_FEES_OVERDRAFT_FEES"),
                (40, -3_00, "BANK_FEES_ATM_FEES"),
            ]
        ):
            db.add(Transaction(account_id=checking.id, external_id=f"dep{i}", date=today - timedelta(days=days),
                               amount_cents=cents, merchant="Bank", description="x", provider_category=cat,
                               source="plaid"))  # fmt: skip

    page = client.get("/api/connections").json()
    chk = next(a for c in page["connections"] for a in c["accounts"] if a["name"] == "Checking")
    d = chk["details"]
    # The APY the bank sent with the sync.
    assert d["kind"] == "checking" and d["bank_apy"] == 0.01 and d["apy"] == 0.01 and d["apy_from"] == "bank"
    assert d["market"]["national_label"] == "interest checking"
    if (today - timedelta(days=40)).year == today.year:
        assert d["fees"]["ytd_cents"] == 38_00 and d["fees"]["by_kind"][0] == {"label": "Overdraft", "cents": 35_00}
    assert d["interest"]["12m_cents"] == 120
    card = next(a for c in page["connections"] for a in c["accounts"] if a["name"] == "Sapphire")
    assert card["details"] is None

    # The CD's terms typed in on its sheet.
    opened = today - timedelta(days=100)
    body = {"apy": 4.1, "opened": opened.isoformat(), "term_months": 12, "maturity": None, "principal_cents": 25_000_00,
            "penalty_days": 90, "auto_renew": True, "grace_days": 10, "pays_out": None, "monthly_fee_cents": None,
            "min_balance_cents": None}  # fmt: skip
    r = client.put(f"/api/accounts/{cd['id']}/terms", json=body, headers=csrf(client))
    assert r.status_code == 200, r.text
    c = r.json()["accounts"][0]["details"]["cd"]
    assert c["earned_cents"] == round(25_000_00 * (1.041 ** (100 / 365) - 1))
    assert c["penalty_cents"] == round(25_000_00 * 0.041 * 90 / 365) and c["grace_days"] == 10
    assert r.json()["accounts"][0]["details"]["market"]["national_label"] == "12-month CD"
    bad = body | {"maturity": opened.isoformat()}
    assert client.put(f"/api/accounts/{cd['id']}/terms", json=bad, headers=csrf(client)).status_code == 422
    assert client.put(f"/api/accounts/{card['id']}/terms", json=body, headers=csrf(client)).status_code == 404

    # Dividends & interest reads the same terms, and saving there keeps what it doesn't show.
    row = next(a for a in client.get("/api/income").json()["interest"]["accounts"] if a["id"] == cd["id"])
    assert row["kind"] == "cd" and row["apy"] == 4.1
    client.put(f"/api/income/accounts/{cd['id']}", json={"apy": 4.2, "maturity": None, "term_months": 12},
               headers=csrf(client))  # fmt: skip
    with db_of(client).session() as db:
        t = db.get(AccountTerms, cd["id"])
        assert float(t.apy) == 4.2 and t.penalty_days == 90 and t.opened == opened


@needs_db
def test_cd_and_rate_pushes(client, home):
    home.setup()
    cd = client.post(
        "/api/accounts/manual",
        json={"name": "Ally CD", "type": "depository", "balance_cents": 10_000_00},
        headers=csrf(client),
    ).json()["accounts"][0]
    savings = client.post(
        "/api/accounts/manual",
        json={"name": "Ally savings", "type": "depository", "balance_cents": 30_000_00},
        headers=csrf(client),
    ).json()["accounts"][0]
    today = date.today()
    maturity = today + timedelta(days=7)
    client.put(
        f"/api/accounts/{cd['id']}/terms",
        json={"apy": 4.0, "maturity": maturity.isoformat(), "term_months": 12, "auto_renew": False},
        headers=csrf(client),
    )
    # Home's "Needs you" lists it, with the button to decide.
    [soon] = client.get("/api/household").json()["health"]["maturing"]
    assert soon["account_id"] == cd["id"] and soon["days_left"] == 7 and soon["auto_renew"] is False
    last_month = deposits.add_months(date(today.year, today.month, 1), -1)
    with db_of(client).session() as db:
        db.add(AccountTerms(account_id=savings["id"], bank_apy=3.5, rates={f"{last_month:%Y-%m}": 4.0}))
    with db_of(client).session() as db:
        assert deposits.queue(db, today) == 2
        assert deposits.queue(db, today) == 0  # once
    with db_of(client).session() as db:
        events = {e.dedup_key: e for e in db.scalars(select(AlertEvent))}
        assert f"cd:{cd['id']}:{maturity}:7d" in events
        e = events[f"cd:{cd['id']}:{maturity}:7d"]
        assert e.title == "Ally CD matures in 7 days" and "Decide whether to renew it" in e.body
        assert e.link == f"/settings/accounts?account={cd['id']}"  # tapping the push opens the CD
        assert events[f"rate:{savings['id']}:{today:%Y-%m}"].title == "Ally savings cut its rate"
        assert db.get(AccountTerms, savings["id"]).rates[f"{today:%Y-%m}"] == 3.5


@needs_db
def test_migration_moves_typed_rates(fresh_db):
    """0017 moves the APY, maturity and term typed on Dividends & interest into account_terms."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from financeapp import db as dbmod

    engine = create_engine(fresh_db)

    def migrate(step, to: str) -> None:
        config = Config()
        config.set_main_option("script_location", str(dbmod.MIGRATIONS))
        with engine.begin() as conn:
            config.attributes["connection"] = conn
            step(config, to)

    migrate(command.upgrade, "0016")
    with engine.begin() as c:
        c.execute(text("INSERT INTO connections (provider, institution, state, status, failure_count, created_at) "
                       "VALUES ('manual', 'Bank', '{}', 'active', 0, now())"))  # fmt: skip
        c.execute(text("INSERT INTO accounts (id, connection_id, external_id, name, institution, type, "
                       "is_authoritative, hidden, created_at) VALUES (7, 1, 'x', 'CD', 'Bank', 'depository', true, "
                       "false, now())"))  # fmt: skip
        c.execute(text("INSERT INTO preferences (key, value) VALUES ('interest_accounts', CAST(:v AS jsonb))"),
                  {"v": '{"7": {"apy": 4.4, "maturity": "2027-01-09", "term_months": 12}, "99": {}}'})  # fmt: skip
    migrate(command.upgrade, "0017")
    with engine.begin() as c:
        row = c.execute(text("SELECT apy, maturity, term_months FROM account_terms")).one()
        assert (float(row.apy), row.maturity, row.term_months) == (4.4, date(2027, 1, 9), 12)
        assert c.execute(text("SELECT count(*) FROM preferences WHERE key = 'interest_accounts'")).scalar() == 0
    migrate(command.downgrade, "0016")
    with engine.begin() as c:
        value = c.execute(text("SELECT value FROM preferences WHERE key = 'interest_accounts'")).scalar()
        assert value == {"7": {"apy": 4.4, "maturity": "2027-01-09", "term_months": 12}}
    engine.dispose()
