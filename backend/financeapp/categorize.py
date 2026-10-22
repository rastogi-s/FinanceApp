"""Puts transactions in categories, in this order (the first that applies wins):

1. A category someone picked by hand ("user") is never changed.
2. Transfers between your own accounts (see transfers.py) are "Transfer", or "Credit card payment" when they pay a card.
3. Your rules: fixing a transaction's category makes a rule for that merchant (store numbers and card-reader
   prefixes don't count, so a rule for "TRADER JOE'S #552" covers every Trader Joe's), or for the person a Zelle or
   Venmo payment went to.
4. Built-in rules for well-known brands (Whole Foods, Netflix, Duke Energy...).
5. The category the bank or Plaid suggested.
6. Built-in rules for loose words ("cafe", "water", "school"...): only when nothing better is known, and marked as a
   guess, because a word like "bar" or "grill" can sit in any name.
7. A refund that matched nothing goes with the merchant's purchases, or in "Uncategorized" (so it comes off
   spending rather than counting as income); other money in that matched nothing is "Other income"; anything
   else is "Uncategorized".

category_source says how sure that is: user, rule, builtin, provider, refund and transfer are trusted; "guess" (a loose
word, or Plaid's broadest groups) and "none" (nothing matched) are what the review screen (review.py) asks about.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp.models import Account, Category, MerchantRule, Transaction

# Well-known brands: specific enough to trust over Plaid.
BUILTIN: list[tuple[str, str]] = [
    (r"payroll|direct dep|\badp\b|gusto|paychex|salary", "Paycheck"),
    (r"interest paid|interest earned|dividend", "Interest & dividends"),
    (
        r"trader joe|whole foods|safeway|kroger|aldi|wegmans|publix|h-e-b|\bheb\b|sprouts|instacart|"
        r"market basket|food lion|giant eagle|stop & shop|ralphs|vons|albertsons|meijer|hy-vee|harris teeter|\blidl\b",
        "Groceries",
    ),
    (
        r"uber ?eats|doordash|grubhub|postmates|starbucks|chipotle|sweetgreen|mcdonald|panera|dunkin|chick-fil-a|"
        r"wendy'?s",
        "Dining out",
    ),
    (r"\buber\b|\blyft\b|\bshell oil|chevron|exxon|valero|fastrak|e-zpass|amtrak|clipper card", "Transport"),
    (
        r"netflix|spotify|hulu|disney\+|disney plus|hbo|max\.com|youtube premium|apple\.com/bill|icloud|"
        r"amazon prime|audible|nytimes|new york times|patreon|dropbox|google storage|chatgpt|openai|claude\.ai",
        "Subscriptions",
    ),
    (r"comcast|xfinity|verizon|at&t|\batt\b|t-mobile|spectrum|sonic\.net|google fi|mint mobile", "Phone & internet"),
    (r"pg&e|\bpge\b|con ?ed\b|edison|national grid|duke energy|pse&g|dominion energy", "Utilities"),
    (r"geico|state farm|progressive ins|allstate|lemonade ins|usaa ins|liberty mutual", "Insurance"),
    (
        r"amazon|amzn|\btarget\b|walmart|best buy|etsy|ebay|ikea|apple store|nordstrom|macy'?s|old navy|\bgap\b|"
        r"uniqlo|zara|tj ?maxx|marshalls",
        "Shopping",
    ),
    (r"home depot|lowe'?s|ace hardware|wayfair|crate & barrel|williams-sonoma", "Home"),
    (r"\bamc\b|ticketmaster|stubhub|steampowered|playstation|xbox|nintendo", "Entertainment"),
    (
        r"delta air|united air|american air|southwest air|jetblue|alaska air|marriott|hilton|hyatt|airbnb|vrbo|"
        r"expedia|booking\.com",
        "Travel",
    ),
    (r"\bcvs\b|walgreens|rite aid|kaiser|labcorp|quest diag", "Health"),
    (r"sephora|\bulta\b", "Personal care"),
    (r"kumon", "Childcare & education"),
    (r"gofundme|red cross|unicef", "Gifts & donations"),
    (r"\birs\b|franchise tax|tax payment|dept of revenue|treas tax", "Taxes"),
    (r"\batm\b|cash withdrawal", "Cash"),
    (r"interest charge|finance charge|late charge|overdraft", "Fees & interest"),
    (r"student loan|navient|nelnet|sallie mae", "Loan payments"),
]
# Loose words: right more often than not, but only used when neither a brand nor Plaid says otherwise.
WORDS: list[tuple[str, str]] = [
    (r"grocery|costco", "Groceries"),
    (
        r"coffee|cafe|restaurant|pizza|burger|taco|sushi|bakery|\bbar\b|grill|kitchen|diner",
        "Dining out",
    ),
    (r"\bshell\b|\bmobil\b|\bbp\b|\barco\b|fuel|gas station|parking|transit|\bmta\b|\bbart\b|metro|toll", "Transport"),
    (r"electric|water|sewer|\bgas co|utility", "Utilities"),
    (r"insurance|progressive|lemonade", "Insurance"),
    (r"\brent\b|mortgage|property mgmt|apartments", "Rent & mortgage"),
    (r"cinema|movie|theatre|theater|concert|\bsteam\b", "Entertainment"),
    (r"airline|airways|southwest|hotel", "Travel"),
    (r"pharmacy|medical|dental|dentist|doctor|clinic|hospital|optometr|vision", "Health"),
    (r"salon|barber|spa\b|nail", "Personal care"),
    (r"daycare|childcare|tuition|school|university|college", "Childcare & education"),
    (r"charity|donation|church", "Gifts & donations"),
    (r"\bfee\b", "Fees & interest"),
    (r"auto loan|car payment|loan payment", "Loan payments"),
]
_BUILTIN = [(re.compile(p, re.IGNORECASE), name) for p, name in BUILTIN]
_WORDS = [(re.compile(p, re.IGNORECASE), name) for p, name in WORDS]
REFUND = re.compile(r"refund|return|credit adj|reversal|merchandise credit", re.IGNORECASE)
INCOMING_ONLY = ("Paycheck", "Interest & dividends")

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
# Plaid's broadest groups, when that's all it could say: a guess worth a second look ("GENERAL_SERVICES_OTHER..." is
# no more Shopping than it is anything else, and "GOVERNMENT_AND_NON_PROFIT" isn't always taxes).
ROUGH = {"GENERAL_SERVICES", "GOVERNMENT_AND_NON_PROFIT", "INCOME"}


def normalize_merchant(merchant: str) -> str:
    text = re.sub(r"[^a-z0-9&+ ]+", " ", merchant.lower())
    return re.sub(r"\s+", " ", text).strip()[:200]


# Card-reader and bank prefixes that aren't part of the name: "SQ *BLUE BOTTLE", "POS DEBIT TRADER JOE'S".
_PREFIX = re.compile(r"^(?:(?:sq|tst|sp|pp|pos|debit|purchase|card|checkcard|recurring|ach|visa|dbt|pmt)\s+)+")


def merchant_key(merchant: str) -> str:
    """The part of a merchant name that stays the same from store to store and day to day: "SQ *BLUE BOTTLE 0042
    OAKLAND CA" and "Blue Bottle #17" are both "blue bottle". It stops at the first word with a digit in it (a store
    number or date), unless the name starts with one ("7-ELEVEN"), then the digit words are dropped instead."""
    words = _PREFIX.sub("", normalize_merchant(merchant)).split()
    if not words:
        return normalize_merchant(merchant)
    cut = next((i for i, w in enumerate(words) if any(ch.isdigit() for ch in w)), len(words))
    kept = words[:cut] if cut else [w for w in words if not any(ch.isdigit() for ch in w)]
    return " ".join(kept or words)


def rule_matches(pattern: str, merchant: str) -> bool:
    m, p = merchant_key(merchant), merchant_key(pattern)
    return bool(p) and (m == p or m.startswith(p + " "))


# Zelle, Venmo and the like: the merchant is the app, so a rule goes by the person the money went to or came from.
P2P = re.compile(r"zelle|venmo|cash ?app|paypal|apple cash", re.IGNORECASE)
SENDER = re.compile(
    r"\b(?:from|to)\s+([a-z][a-z .'-]*?)(?=\s+(?:conf|confirmation|on|ref|id|for|memo|via|trn|txn)\b|\s*\d|$)"
)
PERSON = "person "  # a MerchantRule pattern for a person: "person jane doe"


def sender_of(t: Transaction) -> str | None:
    """Who sent (or got) a Zelle or Venmo payment, from its description: "Zelle payment from JANE DOE conf# 12"."""
    text = normalize_merchant(f"{t.merchant} {t.description}")
    if not P2P.search(text):
        return None
    m = SENDER.search(text)
    name = m.group(1).strip() if m else ""
    name = re.sub(r"^(zelle|venmo|cash app|cashapp|paypal)\s+", "", name)
    return name or None


def person_rule(rules: list[MerchantRule], t: Transaction) -> MerchantRule | None:
    who = sender_of(t)
    return next((r for r in rules if r.pattern == PERSON + who), None) if who else None


def builtin_category(text: str) -> str | None:
    for rx, name in _BUILTIN:
        if rx.search(text):
            return name
    return None


def word_category(text: str) -> str | None:
    for rx, name in _WORDS:
        if rx.search(text):
            return name
    return None


def plaid_match(code: str | None) -> tuple[str | None, bool]:
    """Our category for a Plaid code, and whether it's only a rough guess."""
    if not code:
        return None, False
    if code in PLAID:
        return PLAID[code], code in ROUGH
    for key in sorted(PLAID, key=len, reverse=True):
        if code.startswith(key):
            return PLAID[key], key in ROUGH
    return None, False


def plaid_category(code: str | None) -> str | None:
    return plaid_match(code)[0]


def is_refund(t: Transaction, account_type: str | None = None) -> bool:
    """Money back from a shop: refund wording, or anything coming in on a card (card payments are transfers by now)."""
    return t.amount_cents > 0 and (account_type == "credit" or bool(REFUND.search(f"{t.merchant} {t.description}")))


def categorize(db: DbSession, transactions: list[Transaction] | None = None) -> int:
    """Categorizes the given transactions (or every one not picked by hand). Returns how many changed."""
    by_name = {c.name: c.id for c in db.scalars(select(Category))}
    rules = sorted(db.scalars(select(MerchantRule)), key=lambda r: len(r.pattern), reverse=True)
    merchant_rules = [(merchant_key(r.pattern), r) for r in rules if not r.pattern.startswith(PERSON)]
    merchant_rules = sorted([(k, r) for k, r in merchant_rules if k], key=lambda kr: len(kr[0]), reverse=True)
    account_types = {i: kind for i, kind in db.execute(select(Account.id, Account.type))}
    if transactions is None:
        transactions = list(
            db.scalars(select(Transaction).where(Transaction.category_source.notin_(["user", "transfer"])))
        )
    # What each merchant's purchases usually are, for refunds: the latest outgoing, trusted category.
    bought: dict[str, int] = {}
    refunds = [t for t in transactions if t.amount_cents > 0]
    if refunds:
        keys = {merchant_key(t.merchant) for t in refunds}
        for t in db.scalars(
            select(Transaction)
            .where(
                Transaction.amount_cents < 0,
                Transaction.category_id.is_not(None),
                Transaction.category_source.in_(["user", "rule", "builtin", "provider"]),
            )
            .order_by(Transaction.date)
        ):
            if (k := merchant_key(t.merchant)) in keys:
                bought[k] = t.category_id
    changed = 0
    for t in transactions:
        if t.category_source in ("user", "transfer"):
            continue
        name, source, category_id = None, "none", None
        text = f"{t.merchant} {t.description}"
        mk = merchant_key(t.merchant)
        rule = person_rule(rules, t) or next((r for k, r in merchant_rules if mk == k or mk.startswith(k + " ")), None)
        if rule is not None:
            category_id, source = rule.category_id, "rule"
        else:
            if name := builtin_category(text):
                source = "builtin"
            # A payroll or interest rule only makes sense for money coming in.
            if name in INCOMING_ONLY and t.amount_cents < 0:
                name, source = None, "none"
            if name is None:
                name, rough = plaid_match(t.provider_category)
                source = ("guess" if rough else "provider") if name else "none"
            if name is None and (name := word_category(text)):
                source = "guess"
            if name in INCOMING_ONLY and t.amount_cents < 0:
                name, source = None, "none"
            if name is None and is_refund(t, account_types.get(t.account_id)):
                category_id = bought.get(mk)
                source, name = ("refund", None) if category_id else ("none", "Uncategorized")
            elif name is None:
                name = "Other income" if t.amount_cents > 0 else "Uncategorized"
            if name is not None:
                category_id = by_name.get(name)
        if t.category_id != category_id or t.category_source != source:
            t.category_id, t.category_source = category_id, source
            changed += 1
    return changed
