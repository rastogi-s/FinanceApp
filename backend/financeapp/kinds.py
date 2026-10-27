"""What kind of account each one is (checking, savings, a CD, a card, a 401(k)...), for grouping them.

Plaid and SnapTrade send a broad type (depository, credit, loan, investment) and, usually, a subtype ("cd",
"money market", "401k", "stock plan"). Files and typed-in accounts only have the type. So the kind comes from the
subtype first, then the account's name ("Marcus 12-month CD", "Roth IRA"), then the type. When that guesses wrong,
the account's own `kind` (set on its sheet in Settings → Accounts) wins.

A kind only changes where an account is listed (Settings → Accounts and the net worth sheet). It never changes
the account's type, so what counts as spending, and whether the balance is owned or owed, stay as they were. That
is also why an override stays on its side: an asset can't be made a card, and a card can't be made savings.
"""

import re

# key, the group's label, one account's label, what it holds, the net worth group it adds up in. In page order.
KINDS = [
    ("checking", "Checking", "Checking", "Everyday accounts pay comes into and bills leave from", "cash"),
    ("savings", "Savings", "Savings", "Savings, money market and cash management", "cash"),
    ("cd", "CDs", "CD", "Certificates of deposit, locked until they mature", "cash"),
    ("brokerage", "Brokerage", "Brokerage", "Taxable investing you can sell from any time", "investments"),
    ("stock_plan", "Stock plans", "Stock plan", "Shares from work: RSUs, ESPP and options", "investments"),
    ("retirement", "Retirement", "Retirement", "401(k), 403(b), IRA and Roth: tax-advantaged, for later", "retirement"),
    ("hsa", "HSA", "HSA", "Health savings: tax-free for medical costs", "retirement"),
    ("education", "Education", "Education", "529 and other education savings", "retirement"),
    ("other", "Home, car and other", "Other asset", "Anything else you own and count", "other"),
    ("card", "Credit cards", "Credit card", "What you owe on cards right now", "cards"),
    ("loan", "Loans", "Loan", "Mortgage, car and student loans", "loans"),
]
KEYS = {k for k, *_ in KINDS}
LABEL = {k: one for k, _, one, *_ in KINDS}  # one account's
NET_WORTH_GROUP = {k: g for k, *_, g in KINDS}
OWED = {"card", "loan"}

HSA = re.compile(r"\bhsa\b|health\s+savings", re.I)
EDUCATION = re.compile(r"529|education|coverdell|\besa\b", re.I)
RETIREMENT = re.compile(
    r"401|403|457|\bira\b|roth|pension|retire|keogh|annuity|\btsp\b|\bsep\b|simple\s+ira|profit\s+sharing", re.I
)
STOCK_PLAN = re.compile(r"stock\s+plan|\bespp\b|\brsus?\b|equity\s+award|stock\s+options?", re.I)
CD = re.compile(r"^cd$|\bcds?\b|certificate", re.I)
SAVINGS = re.compile(r"savings|money\s+market|\bmmk?a\b|cash\s+management", re.I)


def detect(type_: str, subtype: str | None, name: str) -> str:
    """The kind an account looks like, from its type, subtype and name (no override)."""
    if type_ == "credit":
        return "card"
    if type_ == "loan":
        return "loan"
    if type_ == "other":
        return "other"
    sub = (subtype or "").strip().lower()
    for text in (sub, name):  # the institution's subtype is surer than a name the user may have typed
        if not text:
            continue
        if HSA.search(text):
            return "hsa"
        if EDUCATION.search(text):
            return "education"
        if RETIREMENT.search(text):
            return "retirement"
        if STOCK_PLAN.search(text):
            return "stock_plan"
        if type_ == "depository" and CD.search(text):
            return "cd"
        if type_ == "depository" and SAVINGS.search(text):
            return "savings"
        if type_ == "depository" and text == sub and sub in ("checking", "prepaid", "paypal", "ebt"):
            return "checking"
    return "brokerage" if type_ == "investment" else "checking"


def allowed(type_: str) -> list[str]:
    """The kinds an account of this type can be set to: owed stays owed, owned stays owned."""
    if type_ in ("credit", "loan"):
        return [k for k, *_ in KINDS if k in OWED]
    return [k for k, *_ in KINDS if k not in OWED]


def kind_of(type_: str, subtype: str | None, name: str, override: str | None = None) -> str:
    if override in KEYS and override in allowed(type_):
        return override
    return detect(type_, subtype, name)


def listing() -> list[dict]:
    """The kinds, in order, for the page to group by."""
    return [
        {"key": k, "label": label, "one": one, "about": about, "owed": k in OWED} for k, label, one, about, _ in KINDS
    ]
