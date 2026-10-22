// The Dividends & interest page's numbers. What came in and what's expected next come from the server
// (backend/financeapp/income.py); the what-if projections are worked out here so the toggles and sliders answer
// instantly.

export type Month = { month: string; cents: number }; // "2026-10"

export type DividendHolding = {
  symbol: string;
  name: string;
  asset_class: string;
  accounts: string[];
  shares: number;
  price: number;
  value_cents: number;
  cost_basis_cents: number | null;
  received_12m_cents: number;
  annual_per_share: number;
  annual_cents: number;
  yield_percent: number | null;
  yield_on_cost_percent: number | null;
  frequency: number; // payments a year; 0 = stopped paying
  last_ex_date: string | null;
  last_amount: number;
  next_ex_date: string | null;
  next_pay_date: string | null;
  next_cents: number | null;
  growth_5y: number | null; // yearly growth of the dividend per share, e.g. 0.06
  reinvesting: boolean;
  from: "history" | "payments";
};

export type Upcoming = { symbol: string; name: string; ex_date: string; pay_date: string; cents: number; reinvesting: boolean };

export type InterestAccount = {
  id: number;
  name: string;
  kind: string; // savings | cd | money market | checking | hsa
  balance_cents: number;
  interest_12m_cents: number;
  apy: number | null; // typed in
  implied_apy: number | null; // from the last three months of interest
  maturity: string | null;
  term_months: number | null;
  last_paid: string | null;
  months: Month[];
};

export type TaxYear = {
  year: number;
  through: string;
  complete: boolean;
  history_from: string;
  covers_whole_year: boolean;
  dividends: {
    total_cents: number;
    qualified_cents: number;
    ordinary_cents: number;
    unlabeled_cents: number;
    estimated_qualified_cents: number;
    estimated_ordinary_cents: number;
    capital_gain_distributions_cents: number;
  };
  interest: { total_cents: number; bank_cents: number; brokerage_cents: number };
  tax_advantaged_cents: number;
  estimated: boolean;
};

export type Income = {
  demo?: boolean;
  as_of: string;
  history_from: string;
  dividends: {
    received_12m_cents: number;
    received_prior_12m_cents: number | null;
    this_year_cents: number;
    last_year_same_period_cents: number | null;
    forward_annual_cents: number;
    yield_percent: number | null;
    yield_on_cost_percent: number | null;
    capital_gains_12m_cents: number;
    by_month: Month[];
    by_year: { year: number; cents: number }[];
    expected_months: Month[];
    upcoming: Upcoming[];
    holdings: DividendHolding[];
    top_share_percent: number | null;
    top3_share_percent: number | null;
    invested_cents: number;
    non_payers_cents: number;
    reinvesting: string[];
    recent: { date: string; symbol: string | null; name: string; account: string; cents: number; tax: string }[];
  };
  interest: {
    received_12m_cents: number;
    this_year_cents: number;
    brokerage_12m_cents: number;
    by_month: Month[];
    by_year: { year: number; cents: number }[];
    accounts: InterestAccount[];
  };
  tax: TaxYear[];
};

export const DEFAULT_DIVIDEND_GROWTH = 0.04; // when a holding has no five-year history
export const HORIZONS = [1, 5, 10, 20] as const;

export type DividendScenario = {
  drip: boolean;
  priceGrowth: number; // yearly, e.g. 0.05
  dividendGrowth: number | null; // null: each holding's own five-year growth
};

export type DividendYear = {
  year: number; // 1 = the next twelve months
  incomeCents: number;
  valueCents: number;
  collectedCents: number; // every dividend so far, reinvested or not
  addedShareValueCents: number; // what the shares bought by reinvesting are worth (0 without DRIP)
};

/**
 * Today's dividend payers carried forward year by year, each paying its usual number of times a year. With DRIP, each
 * payment buys more shares at that day's price, and those shares are paid on from then on. No new money is added.
 */
export function projectDividends(holdings: DividendHolding[], s: DividendScenario, years = 20): DividendYear[] {
  const out: DividendYear[] = Array.from({ length: years }, (_, i) => ({
    year: i + 1,
    incomeCents: 0,
    valueCents: 0,
    collectedCents: 0,
    addedShareValueCents: 0,
  }));
  for (const h of holdings) {
    if (h.shares <= 0 || h.price <= 0) continue;
    const g = s.dividendGrowth ?? h.growth_5y ?? DEFAULT_DIVIDEND_GROWTH;
    const per = Math.max(h.frequency, 1);
    let shares = h.shares;
    let collected = 0;
    for (let y = 0; y < years; y++) {
      const dps = h.annual_per_share * (1 + g) ** y;
      let income = 0;
      for (let k = 0; k < per; k++) {
        const t = y + (k + 0.5) / per;
        const paid = (shares * dps) / per;
        income += paid;
        if (s.drip) shares += paid / (h.price * (1 + s.priceGrowth) ** t);
      }
      collected += income;
      const price = h.price * (1 + s.priceGrowth) ** (y + 1);
      out[y]!.incomeCents += income * 100;
      out[y]!.valueCents += shares * price * 100;
      out[y]!.collectedCents += collected * 100;
      out[y]!.addedShareValueCents += (shares - h.shares) * price * 100;
    }
  }
  return out.map((r) => ({
    ...r,
    incomeCents: Math.round(r.incomeCents),
    valueCents: Math.round(r.valueCents),
    collectedCents: Math.round(r.collectedCents),
    addedShareValueCents: Math.round(r.addedShareValueCents),
  }));
}

/** The APY the projections use: what was typed in, else what recent interest works out to. */
export function apyOf(a: InterestAccount): number | null {
  return a.apy ?? a.implied_apy;
}

export type InterestYear = { year: number; interestCents: number; balanceCents: number; cumulativeCents: number };

/**
 * Interest left in the accounts and compounding monthly at each account's APY moved by `shift` points (rates going
 * up or down). A CD that matures rolls over at `rollover` when it's given, else keeps its rate.
 */
export function projectInterest(
  accounts: InterestAccount[],
  shift: number,
  years = 10,
  rollover: Record<number, number> = {},
  today = new Date(),
): InterestYear[] {
  const out: InterestYear[] = Array.from({ length: years }, (_, i) => ({ year: i + 1, interestCents: 0, balanceCents: 0, cumulativeCents: 0 }));
  for (const a of accounts) {
    const base = apyOf(a);
    if (base === null || a.balance_cents <= 0) continue;
    let balance = a.balance_cents;
    let cumulative = 0;
    const maturesIn = a.maturity ? monthsBetween(today, new Date(a.maturity + "T12:00:00")) : null;
    for (let y = 0; y < years; y++) {
      let interest = 0;
      for (let m = 0; m < 12; m++) {
        const month = y * 12 + m;
        const rate = maturesIn !== null && month >= maturesIn && rollover[a.id] !== undefined ? rollover[a.id]! : base;
        const r = Math.max(rate + shift, 0) / 100;
        const earned = balance * ((1 + r) ** (1 / 12) - 1);
        interest += earned;
        balance += earned;
      }
      cumulative += interest;
      out[y]!.interestCents += Math.round(interest);
      out[y]!.balanceCents += Math.round(balance);
      out[y]!.cumulativeCents += Math.round(cumulative);
    }
  }
  return out;
}

/** What a CD earns from today to its maturity, and over a new term after it at `rate`. */
export function cdWhatIf(a: InterestAccount, rate: number, termMonths: number, today = new Date()) {
  const apy = apyOf(a) ?? 0;
  const left = a.maturity ? Math.max(monthsBetween(today, new Date(a.maturity + "T12:00:00")), 0) : 0;
  const atMaturity = a.balance_cents * (1 + apy / 100) ** (left / 12);
  const after = atMaturity * (1 + rate / 100) ** (termMonths / 12);
  return {
    monthsLeft: left,
    toMaturityCents: Math.round(atMaturity - a.balance_cents),
    atMaturityCents: Math.round(atMaturity),
    rolloverCents: Math.round(after - atMaturity),
    endCents: Math.round(after),
  };
}

export function monthsBetween(a: Date, b: Date): number {
  return (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth()) + (b.getDate() >= a.getDate() ? 0 : -1);
}

const monthShort = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });
const monthLong = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
const dayShort = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const dayLong = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

export const monthLabel = (m: string, long = false) => (long ? monthLong : monthShort).format(new Date(m + "-01T00:00:00Z"));
export const dayLabel = (d: string, withYear = false) => (withYear ? dayLong : dayShort).format(new Date(d + "T00:00:00Z"));

export const FREQUENCY: Record<number, string> = { 12: "Monthly", 4: "Quarterly", 2: "Twice a year", 1: "Yearly", 0: "Stopped" };

export function pct(v: number | null | undefined, digits = 2): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(digits)}%`;
}
