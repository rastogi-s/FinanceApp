"""Puts transactions in categories, in this order (the first that applies wins):

1. A category someone picked by hand ("user") is never changed.
2. Transfers between your own accounts (see transfers.py) are "Transfer", or "Credit card payment" when they pay a card.
3. Your merchant rules: fixing one transaction's category makes a rule for that merchant.
4. Built-in rules for well-known merchants.
5. The category the bank or Plaid suggested.
6. Money in that matched nothing is "Other income"; anything else is "Uncategorized".
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp.models import Category, MerchantRule, Transaction

BUILTIN: list[tuple[str, str]] = [
    (r"payroll|direct dep|\badp\b|gusto|paychex|salary", "Paycheck"),
    (r"interest paid|interest earned|dividend", "Interest & dividends"),
    (
        r"trader joe|whole foods|safeway|kroger|aldi|wegmans|publix|h-e-b|\bheb\b|sprouts|instacart|grocery|"
        r"market basket|food lion|giant eagle|stop & shop|ralphs|vons|albertsons|meijer|hy-vee|costco",
        "Groceries",
    ),
    (
        r"uber ?eats|doordash|grubhub|postmates|starbucks|coffee|cafe|restaurant|pizza|chipotle|sweetgreen|"
        r"mcdonald|burger|taco|sushi|bakery|\bbar\b|grill|kitchen|diner|panera|dunkin|chick-fil-a|wendy",
        "Dining out",
    ),
    (
        r"\buber\b|\blyft\b|shell|chevron|exxon|mobil|\bbp\b|arco|valero|fuel|gas station|parking|transit|clipper|"
        r"\bmta\b|\bbart\b|metro|toll|fastrak|e-zpass|amtrak",
        "Transport",
    ),
    (
        r"netflix|spotify|hulu|disney\+|disney plus|hbo|max\.com|youtube premium|apple\.com/bill|icloud|"
        r"amazon prime|audible|nytimes|new york times|patreon|dropbox|google storage|chatgpt|openai|claude\.ai",
        "Subscriptions",
    ),
    (r"comcast|xfinity|verizon|at&t|\batt\b|t-mobile|spectrum|sonic\.net|google fi|mint mobile", "Phone & internet"),
    (r"pg&e|pge|con ?ed|edison|electric|water|sewer|\bgas co|utility|national grid|duke energy|pse&g", "Utilities"),
    (r"geico|state farm|progressive|allstate|lemonade|insurance|usaa ins|liberty mutual", "Insurance"),
    (r"\brent\b|mortgage|property mgmt|apartments", "Rent & mortgage"),
    (
        r"amazon|amzn|target|walmart|best buy|etsy|ebay|ikea|apple store|nordstrom|macy|old navy|\bgap\b|"
        r"uniqlo|zara|tj ?maxx|marshalls|costco\.com",
        "Shopping",
    ),
    (r"home depot|lowe'?s|ace hardware|wayfair|crate & barrel|williams-sonoma", "Home"),
    (
        r"\bamc\b|cinema|movie|theatre|theater|ticketmaster|stubhub|steam|playstation|xbox|nintendo|concert",
        "Entertainment",
    ),
    (
        r"airline|airways|delta air|united air|american air|southwest|jetblue|alaska air|hotel|marriott|hilton|"
        r"hyatt|airbnb|vrbo|expedia|booking\.com",
        "Travel",
    ),
    (
        r"cvs|walgreens|rite aid|pharmacy|medical|dental|dentist|doctor|clinic|hospital|kaiser|optometr|"
        r"vision|labcorp|quest diag",
        "Health",
    ),
    (r"salon|barber|spa\b|nail|sephora|ulta", "Personal care"),
    (r"daycare|childcare|tuition|school|university|college|kumon", "Childcare & education"),
    (r"charity|donation|gofundme|red cross|unicef|church", "Gifts & donations"),
    (r"\birs\b|franchise tax|tax payment|dept of revenue|treas tax", "Taxes"),
    (r"\batm\b|cash withdrawal", "Cash"),
    (r"\bfee\b|interest charge|finance charge|late charge|overdraft", "Fees & interest"),
    (r"student loan|navient|nelnet|sallie mae|auto loan|car payment|loan payment", "Loan payments"),
]
_BUILTIN = [(re.compile(p, re.IGNORECASE), name) for p, name in BUILTIN]

# Plaid's personal finance categories -> ours. Detailed codes first, then the primary code.
PLAID: dict[str, str] = {
    "INCOME_WAGES": "Paycheck",
    "INCOME_DIVIDENDS": "Interest & dividends",
    "INCOME_INTEREST_EARNED": "Interest & dividends",
    "INCOME": "Other income",
    "TRANSFER_IN": "Transfer",
    "TRANSFER_OUT": "Transfer",
    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT": "Credit card payment",
    "LOAN_PAYMENTS": "Loan payments",
    "BANK_FEES": "Fees & interest",
    "ENTERTAINMENT": "Entertainment",
    "FOOD_AND_DRINK_GROCERIES": "Groceries",
    "FOOD_AND_DRINK": "Dining out",
    "GENERAL_MERCHANDISE": "Shopping",
    "HOME_IMPROVEMENT": "Home",
    "MEDICAL": "Health",
    "PERSONAL_CARE": "Personal care",
    "GENERAL_SERVICES_INSURANCE": "Insurance",
    "GENERAL_SERVICES_CHILDCARE": "Childcare & education",
    "GENERAL_SERVICES_EDUCATION": "Childcare & education",
    "GENERAL_SERVICES": "Shopping",
    "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT": "Taxes",
    "GOVERNMENT_AND_NON_PROFIT_DONATIONS": "Gifts & donations",
    "GOVERNMENT_AND_NON_PROFIT": "Taxes",
    "TRANSPORTATION": "Transport",
    "TRAVEL": "Travel",
    "RENT_AND_UTILITIES_RENT": "Rent & mortgage",
    "RENT_AND_UTILITIES_TELEPHONE": "Phone & internet",
    "RENT_AND_UTILITIES_INTERNET_AND_CABLE": "Phone & internet",
    "RENT_AND_UTILITIES": "Utilities",
}


def normalize_merchant(merchant: str) -> str:
    text = re.sub(r"[^a-z0-9&+ ]+", " ", merchant.lower())
    return re.sub(r"\s+", " ", text).strip()[:200]


def rule_matches(pattern: str, merchant: str) -> bool:
    m = normalize_merchant(merchant)
    return m == pattern or m.startswith(pattern + " ")


def builtin_category(text: str) -> str | None:
    for rx, name in _BUILTIN:
        if rx.search(text):
            return name
    return None


def plaid_category(code: str | None) -> str | None:
    if not code:
        return None
    if code in PLAID:
        return PLAID[code]
    for key in sorted(PLAID, key=len, reverse=True):
        if code.startswith(key):
            return PLAID[key]
    return None


def categorize(db: DbSession, transactions: list[Transaction] | None = None) -> int:
    """Categorizes the given transactions (or every one not picked by hand). Returns how many changed."""
    by_name = {c.name: c.id for c in db.scalars(select(Category))}
    rules = sorted(db.scalars(select(MerchantRule)), key=lambda r: len(r.pattern), reverse=True)
    if transactions is None:
        transactions = list(
            db.scalars(select(Transaction).where(Transaction.category_source.notin_(["user", "transfer"])))
        )
    changed = 0
    for t in transactions:
        if t.category_source in ("user", "transfer"):
            continue
        name, source = None, "none"
        rule = next((r for r in rules if rule_matches(r.pattern, t.merchant)), None)
        if rule is not None:
            category_id, source = rule.category_id, "rule"
        else:
            name = builtin_category(f"{t.merchant} {t.description}")
            source = "builtin" if name else "none"
            # A payroll or interest rule only makes sense for money coming in.
            if name in ("Paycheck", "Interest & dividends") and t.amount_cents < 0:
                name, source = None, "none"
            if name is None and (name := plaid_category(t.provider_category)):
                source = "provider"
            if name is None:
                name = "Other income" if t.amount_cents > 0 else "Uncategorized"
            category_id = by_name.get(name)
        if t.category_id != category_id or t.category_source != source:
            t.category_id, t.category_source = category_id, source
            changed += 1
    return changed
