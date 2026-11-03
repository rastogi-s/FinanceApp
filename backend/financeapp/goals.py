"""Goals: what the household is saving for, how much of its money is set aside for each, and when each gets there.

The emergency fund is always one of the goals. Its target is months of essentials (the budget's setting) unless
one is typed in. With no accounts picked for it, it counts the cash (checking and savings) that no other goal has
claimed, so setting $40,000 of a savings account aside for a house takes it out of the emergency fund straight
away and nothing is counted twice.

Sharing out a balance. A goal is funded by accounts: a fixed amount of one (e.g. $40,000 of the HYSA), or the
rest of it. On each account the fixed amounts are filled first, in goal priority order (high, then medium, then
low, then oldest first), never more than the balance; the one goal that takes the rest of an account gets what is
left. So if the HYSA drops under what's promised, the lower-priority goal is the one that comes up short.

Getting there. A goal with a date says what it needs a month to get there, at what the money earns (3.5% a year,
a typical high-yield savings rate, unless typed in). What goes in each month is the number typed in, or else a
share of what the household actually saves a month (plan.py's measured savings): goals with a date first, in
priority order, each up to what it needs; whatever is left goes to the goals with no date, three shares to a
high-priority one, two to a medium and one to a low. The projected month is when that monthly amount, growing at
the goal's rate, reaches the target.

Going backwards. Money that leaves an account set aside for a goal (an emergency, a big bill paid from it) takes the
goal down with it. Each goal says how much it's down over the last 30 days and which accounts the money left. That's
worked out with the goal's accounts as they're set up today against their balances 30 days ago, so changing a goal
in the app never reads as money leaving. A goal that drops 5 points or more of its target sends an alert
(alerts.goal_hits).
"""

import math
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from financeapp.models import Account, Balance, Goal, GoalFunding
from financeapp.sync import today

DEFAULT_RATE = 3.5  # % a year: a typical high-yield savings account
FUNDABLE = ("depository", "investment")
CASH = "depository"
MILESTONES = (25, 50, 75, 100)
ICONS = ("house", "car", "plane", "ring", "school", "baby", "heart", "gift", "laptop", "piggy", "shield", "star")
COLORS = ("teal", "blue", "amber", "pink", "slate")
PRIORITY = {1: "High", 2: "Medium", 3: "Low"}
DOWN_DAYS = 30  # how far back "down" looks
DOWN_MIN_CENTS = 100_00  # smaller dips (a fee, a rounding) aren't worth a line
WEIGHT = {1: 3, 2: 2, 3: 1}  # how what's left is split between goals with no date
# Ideas to start from on an empty Goals page; nothing is created until someone picks one and sets the amount.
SUGGESTIONS = [
    {"name": "House down payment", "icon": "house", "color": "blue"},
    {"name": "New car", "icon": "car", "color": "amber"},
    {"name": "Travel", "icon": "plane", "color": "pink"},
    {"name": "Wedding", "icon": "ring", "color": "pink"},
    {"name": "Education", "icon": "school", "color": "slate"},
]


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def months_between(start: date, end: date) -> int:
    """Whole months from start's month to end's month (Oct 2026 to Mar 2028 is 17)."""
    return (end.year - start.year) * 12 + end.month - start.month


def needed_monthly(target: int, start: int, months: int, rate_percent: float) -> int:
    """What has to go in at the end of each month for `start` to reach `target` in `months`, at a yearly rate."""
    if start >= target:
        return 0
    if months <= 0:
        return target - start
    r = rate_percent / 100 / 12
    if r == 0:
        return -(-(target - start) // months)  # rounded up
    f = (1 + r) ** months
    return max(math.ceil((target - start * f) * r / (f - 1)), 0)


def rank(g: Goal) -> tuple[int, int]:
    return (g.priority, g.id)


def latest_balances(db: DbSession, accounts: list[Account]) -> dict[int, int]:
    out = {}
    for a in accounts:
        b = db.scalar(select(Balance).where(Balance.account_id == a.id).order_by(Balance.date.desc()).limit(1))
        out[a.id] = max(b.current_cents, 0) if b else 0
    return out


def balances_on(db: DbSession, accounts: list[Account], day: date) -> dict[int, int]:
    """Each account's balance as of a day (its latest on or before it); accounts with no balance that far back are
    left out, so they count as unchanged."""
    out = {}
    for a in accounts:
        b = db.scalar(
            select(Balance)
            .where(Balance.account_id == a.id, Balance.date <= day)
            .order_by(Balance.date.desc())
            .limit(1)
        )
        if b:
            out[a.id] = max(b.current_cents, 0)
    return out


def share_out(goals: list[Goal], funding: list[GoalFunding], balances: dict[int, int]) -> dict[tuple[int, int], int]:
    """How much of each account counts for each goal: {(goal_id, account_id): cents}. Fixed amounts first, by
    goal priority; then the rest of the account to the goal that takes it."""
    order = {g.id: rank(g) for g in goals}
    counted: dict[tuple[int, int], int] = {}
    by_account: dict[int, list[GoalFunding]] = {}
    for f in funding:
        if f.goal_id in order:
            by_account.setdefault(f.account_id, []).append(f)
    for account_id, rows in by_account.items():
        left = balances.get(account_id, 0)
        rows.sort(key=lambda f: (f.cents is None, order[f.goal_id]))
        for f in rows:
            take = left if f.cents is None else min(f.cents, left)
            counted[(f.goal_id, account_id)] = take
            left -= take
    return counted


def emergency_goal(db: DbSession) -> Goal:
    g = db.scalar(select(Goal).where(Goal.kind == "emergency").order_by(Goal.id).limit(1))
    if g is None:  # removed by hand from the database: put it back
        g = Goal(kind="emergency", name="Emergency fund", icon="shield", color="teal", priority=1)
        db.add(g)
        db.flush()
    return g


def cash_in_goals(db: DbSession, accounts: list[Account]) -> dict:
    """What the emergency fund has, for the budget: the accounts picked for it, or else the cash no other goal has
    claimed. Also how much cash is set aside for other goals, and a typed-in target if there is one."""
    goals = list(db.scalars(select(Goal)))
    emergency = next((g for g in goals if g.kind == "emergency"), None)
    fundable = [a for a in accounts if a.type in FUNDABLE]
    balances = latest_balances(db, fundable)
    funding = list(db.scalars(select(GoalFunding).where(GoalFunding.account_id.in_(list(balances)))))
    counted = share_out(goals, funding, balances)
    cash_ids = {a.id for a in fundable if a.type == CASH}
    eid = emergency.id if emergency else None
    picked = any(f.goal_id == eid for f in funding)
    elsewhere = sum(c for (gid, aid), c in counted.items() if gid != eid and aid in cash_ids)
    if picked:
        have = sum(c for (gid, _), c in counted.items() if gid == eid)
    else:
        have = sum(balances[a] for a in cash_ids) - elsewhere
    return {
        "have_cents": have,
        "set_aside_cents": elsewhere,
        "picked": picked,
        "target_cents": emergency.target_cents if emergency else None,
    }


def build(db: DbSession, on: date | None = None, inputs: dict | None = None) -> dict:
    """Every goal with its progress, what it needs a month, what goes in, and when it gets there."""
    from financeapp import budget, household

    on = on or today()
    if inputs is None:
        _, inputs = household.build_with_plan(db)
    em = inputs["emergency"] if inputs else budget.build(db)["emergency"]
    monthly_savings = inputs["monthly_savings_cents"] if inputs else None

    emergency_goal(db)
    accounts = [a for a in household.counted(db) if a.type in FUNDABLE]
    balances = latest_balances(db, accounts)
    funding = list(db.scalars(select(GoalFunding).where(GoalFunding.account_id.in_(list(balances)))))
    shown = [(a.id, household.display_name(a), a.type) for a in accounts]
    before = balances_on(db, accounts, on - timedelta(days=DOWN_DAYS))
    return assemble(list(db.scalars(select(Goal))), funding, balances, shown, em, monthly_savings, on, before)


def assemble(
    goals: list[Goal],
    funding: list[GoalFunding],
    balances: dict[int, int],
    accounts: list[tuple[int, str, str]],
    em: dict,
    monthly_savings: int | None,
    on: date,
    before: dict[int, int] | None = None,
) -> dict:
    """The Goals page from the goals, what funds them, the balances, and the budget's emergency fund numbers.
    `before` is the balances DOWN_DAYS ago (balances_on); left out, nothing reads as down."""
    goals = sorted(goals, key=lambda g: (g.kind != "emergency", *rank(g)))
    names = {aid: name for aid, name, _ in accounts}
    counted = share_out(goals, funding, balances)
    then_balances = {**balances, **(before or {})}
    then_counted = share_out(goals, funding, then_balances)
    cash = [aid for aid, _, kind in accounts if kind == CASH]

    rows = []
    for g in goals:
        own = [f for f in funding if f.goal_id == g.id]
        auto = g.kind == "emergency" and not own
        saved = em["have_cents"] if auto else sum(counted.get((g.id, f.account_id), 0) for f in own)
        target = g.target_cents if g.target_cents is not None else (em["target_cents"] if g.kind == "emergency" else 0)
        rate = float(g.rate_percent) if g.rate_percent is not None else DEFAULT_RATE
        months_left = months_between(on, g.target_date) if g.target_date else None
        needed = needed_monthly(target, saved, months_left, rate) if months_left is not None and target else None
        down, down_from = went_down(g, own, auto, counted, then_counted, balances, then_balances, cash, names)
        if down < DOWN_MIN_CENTS or (target > 0 and down * 100 < target):  # under $100, or under 1% of the target
            down, down_from = 0, []
        rows.append(
            {
                "id": g.id,
                "kind": g.kind,
                "name": g.name,
                "icon": g.icon,
                "color": g.color,
                "priority": g.priority,
                "target_cents": target,
                "target_typed": g.target_cents is not None,
                "target_date": g.target_date.isoformat() if g.target_date else None,
                "rate_percent": rate,
                "rate_typed": g.rate_percent is not None,
                "monthly_typed_cents": g.monthly_cents,
                "saved_cents": saved,
                "percent": round(saved / target * 100, 1) if target > 0 else None,
                "months_left": months_left,
                "needed_cents": needed,
                "auto": auto,
                # Down this much over the last DOWN_DAYS days, because money left these accounts.
                "down_cents": down,
                "down_accounts": down_from,
                "down_since": (on - timedelta(days=DOWN_DAYS)).isoformat(),
                "was_percent": round((saved + down) / target * 100, 1) if target > 0 and down else None,
                "funding": [
                    {
                        "account_id": f.account_id,
                        "account": names.get(f.account_id, "Account"),
                        "cents": f.cents,
                        "counted_cents": counted.get((g.id, f.account_id), 0),
                        # A fixed amount the account can't cover right now (others came first, or it went down).
                        "short": f.cents is not None and counted.get((g.id, f.account_id), 0) < f.cents,
                    }
                    for f in sorted(own, key=lambda f: names.get(f.account_id, ""))
                ],
            }
        )

    plan_monthly(rows, monthly_savings)
    for r in rows:
        finish(r, on)

    claimed: dict[int, int] = {}
    for (_, aid), c in counted.items():
        claimed[aid] = claimed.get(aid, 0) + c
    open_rows = [r for r in rows if r["status"] not in ("done", "no_target")]
    needed_total = sum(r["needed_cents"] or 0 for r in open_rows)
    return {
        "demo": False,
        "goals": rows,
        "accounts": [
            {
                "id": aid,
                "name": name,
                "type": kind,
                "balance_cents": balances.get(aid, 0),
                "free_cents": max(balances.get(aid, 0) - claimed.get(aid, 0), 0),
                "rest_goal_id": next((f.goal_id for f in funding if f.account_id == aid and f.cents is None), None),
            }
            for aid, name, kind in sorted(accounts, key=lambda a: (a[2] != CASH, a[1]))
        ],
        "summary": {
            "monthly_savings_cents": monthly_savings,
            "needed_cents": needed_total,
            "planned_cents": sum(r["monthly_cents"] or 0 for r in open_rows),
            "set_aside_cents": sum(r["saved_cents"] for r in rows if not r["auto"]),
            "short_cents": max(needed_total - monthly_savings, 0) if monthly_savings is not None else None,
        },
        "emergency": {"months": em["months"], "monthly_essentials_cents": em["monthly_cents"]},
        "suggestions": SUGGESTIONS,
        "default_rate": DEFAULT_RATE,
        "month": on.replace(day=1).isoformat(),
    }


def went_down(
    g: Goal,
    own: list[GoalFunding],
    auto: bool,
    counted: dict[tuple[int, int], int],
    then_counted: dict[tuple[int, int], int],
    balances: dict[int, int],
    then_balances: dict[int, int],
    cash: list[int],
    names: dict[int, str],
) -> tuple[int, list[str]]:
    """How much less the goal has than DOWN_DAYS ago, and the accounts it came out of. The emergency fund with no
    accounts picked has the cash no other goal claimed, so it's down when that is."""
    if auto:

        def free(c, b):  # fmt: skip
            return sum(b.get(a, 0) for a in cash) - sum(v for (gid, a), v in c.items() if gid != g.id and a in cash)

        down = free(then_counted, then_balances) - free(counted, balances)
        fell = [a for a in cash if then_balances.get(a, 0) > balances.get(a, 0)]
    else:
        ids = [f.account_id for f in own]
        down = sum(then_counted.get((g.id, a), 0) - counted.get((g.id, a), 0) for a in ids)
        fell = [a for a in ids if then_counted.get((g.id, a), 0) > counted.get((g.id, a), 0)]
    return max(down, 0), sorted(names.get(a, "Account") for a in fell) if down > 0 else []


def plan_monthly(rows: list[dict], savings: int | None) -> None:
    """What goes into each goal a month: typed in, or a share of what the household saves (see the module doc)."""
    left = max(savings, 0) if savings is not None else None
    reached = {r["id"] for r in rows if r["target_cents"] and r["saved_cents"] >= r["target_cents"]}
    todo = sorted((r for r in rows if r["id"] not in reached), key=lambda r: (r["priority"], r["id"]))
    for r in rows:
        r["monthly_cents"], r["monthly_source"] = (0, "done") if r["id"] in reached else (None, None)
    for r in todo:
        if r["monthly_typed_cents"] is not None:
            r["monthly_cents"], r["monthly_source"] = r["monthly_typed_cents"], "typed"
            if left is not None:
                left = max(left - r["monthly_typed_cents"], 0)
    for r in todo:
        if r["monthly_source"] is None and r["needed_cents"] is not None:
            if left is None:
                r["monthly_cents"], r["monthly_source"] = r["needed_cents"], "needed"
            else:
                give = min(r["needed_cents"], left)
                r["monthly_cents"], r["monthly_source"] = give, "savings"
                left -= give
    open_ended = [r for r in todo if r["monthly_source"] is None and r["target_cents"]]
    shares = sum(WEIGHT[r["priority"]] for r in open_ended)
    for r in open_ended:
        if left is not None:
            r["monthly_cents"], r["monthly_source"] = left * WEIGHT[r["priority"]] // shares, "savings"


def finish(r: dict, on: date) -> None:
    """When the goal gets there at what goes in a month, and whether that's in time."""
    from financeapp import plan  # plan reads the budget, which reads goals

    target, saved = r["target_cents"], r["saved_cents"]
    if not target:
        r["projected_month"], r["status"] = None, "no_target"
        return
    if saved >= target:
        r["projected_month"], r["status"] = None, "done"
        return
    months = plan.months_to(target, saved, r["monthly_cents"] or 0, r["rate_percent"])
    r["projected_month"] = add_months(on, months).isoformat() if months is not None else None
    if r["target_date"] is None:
        r["status"] = "open"
    elif months is not None and months <= r["months_left"]:
        r["status"] = "on_track"
    else:
        r["status"] = "behind"
