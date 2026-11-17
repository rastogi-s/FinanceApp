// The Profit page's numbers: how much the holdings have grown over what they cost, from the tax lots
// (backend/financeapp/profit.py). The server sends them per account, so picking accounts adds up here instantly.

export type ProfitAccount = { id: number; name: string; taxable: boolean; history_from: string | null };

export type ProfitHolding = {
  symbol: string;
  name: string;
  account_id: number;
  value_cents: number;
  cost_cents: number; // of the lots whose cost is on file
  short_gain_cents: number;
  long_gain_cents: number;
  unknown_gain_cents: number; // shares from before the activity on file, buy date not known yet
  uncosted_value_cents: number; // lots with no cost on file: left out of the gain
  lots: number;
};

export type ProfitSale = {
  date: string;
  symbol: string;
  name: string;
  account_id: number;
  taxable: boolean;
  quantity: number;
  proceeds_cents: number;
  cost_cents: number | null;
  gain_cents: number | null;
  short_gain_cents: number;
  long_gain_cents: number;
  uncosted_proceeds_cents: number;
};

export type YearAccount = {
  account_id: number;
  count: number;
  proceeds_cents: number;
  short_gain_cents: number;
  long_gain_cents: number;
  uncosted_proceeds_cents: number;
};

export type Profit = {
  as_of: string;
  accounts: ProfitAccount[];
  holdings: ProfitHolding[];
  sales: ProfitSale[];
  sales_by_year: { year: number; by_account: YearAccount[] }[];
};

export type Totals = {
  value: number;
  cost: number;
  gain: number;
  short: number;
  long: number;
  unknown: number;
  uncosted: number;
  untaxed: number; // the gain in retirement and health accounts
  percent: number | null;
};

/** The chosen accounts (none chosen: every account). */
export function picked(p: Profit, chosen: number[]): Set<number> {
  const ids = new Set(p.accounts.map((a) => a.id));
  const keep = chosen.filter((id) => ids.has(id));
  return new Set(keep.length ? keep : ids);
}

export function gainOf(h: Pick<ProfitHolding, "short_gain_cents" | "long_gain_cents" | "unknown_gain_cents">) {
  return h.short_gain_cents + h.long_gain_cents + h.unknown_gain_cents;
}

export function totals(p: Profit, ids: Set<number>): Totals {
  const taxable = new Map(p.accounts.map((a) => [a.id, a.taxable]));
  const t: Totals = { value: 0, cost: 0, gain: 0, short: 0, long: 0, unknown: 0, uncosted: 0, untaxed: 0, percent: null };
  for (const h of p.holdings) {
    if (!ids.has(h.account_id)) continue;
    t.value += h.value_cents;
    t.uncosted += h.uncosted_value_cents;
    t.cost += h.cost_cents;
    t.gain += gainOf(h);
    if (taxable.get(h.account_id)) {
      t.short += h.short_gain_cents;
      t.long += h.long_gain_cents;
      t.unknown += h.unknown_gain_cents;
    } else {
      t.untaxed += gainOf(h);
    }
  }
  t.percent = t.cost > 0 ? (t.gain / t.cost) * 100 : null;
  return t;
}

/** One row per holding across the chosen accounts (a ticker held in two accounts is one row). */
export function byHolding(p: Profit, ids: Set<number>) {
  const rows = new Map<string, ProfitHolding & { accounts: number[] }>();
  for (const h of p.holdings) {
    if (!ids.has(h.account_id)) continue;
    const r = rows.get(h.symbol);
    if (!r) {
      rows.set(h.symbol, { ...h, accounts: [h.account_id] });
      continue;
    }
    r.value_cents += h.value_cents;
    r.cost_cents += h.cost_cents;
    r.short_gain_cents += h.short_gain_cents;
    r.long_gain_cents += h.long_gain_cents;
    r.unknown_gain_cents += h.unknown_gain_cents;
    r.uncosted_value_cents += h.uncosted_value_cents;
    r.lots += h.lots;
    r.accounts.push(h.account_id);
  }
  return [...rows.values()];
}

export function soldIn(p: Profit, year: number, ids: Set<number>) {
  const y = p.sales_by_year.find((x) => x.year === year);
  const t = { count: 0, proceeds: 0, short: 0, long: 0, uncosted: 0, untaxed: 0 };
  const taxable = new Map(p.accounts.map((a) => [a.id, a.taxable]));
  for (const a of y?.by_account ?? []) {
    if (!ids.has(a.account_id)) continue;
    t.count += a.count;
    t.proceeds += a.proceeds_cents;
    t.uncosted += a.uncosted_proceeds_cents;
    if (taxable.get(a.account_id)) {
      t.short += a.short_gain_cents;
      t.long += a.long_gain_cents;
    } else {
      t.untaxed += a.short_gain_cents + a.long_gain_cents;
    }
  }
  return t;
}

export const signedPercent = (n: number) => `${n >= 0 ? "+" : "−"}${Math.abs(n).toFixed(1)}%`;
