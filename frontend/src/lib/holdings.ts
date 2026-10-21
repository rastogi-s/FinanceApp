import type { Holding } from "@/lib/household";

/** One investment, added up across the accounts that hold it. */
export type Group = {
  symbol: string;
  name: string;
  asset_class: string;
  value_cents: number;
  change_today_percent: number;
  cost_basis_cents: number | null;
  quantity: number | null;
  price: number | null;
  live: boolean;
  parts: Holding[];
};

export const gain = (x: { value_cents: number; cost_basis_cents?: number | null }) =>
  x.cost_basis_cents ? (x.value_cents - x.cost_basis_cents) / x.cost_basis_cents : null;

export const gainCents = (x: { value_cents: number; cost_basis_cents?: number | null }) =>
  x.cost_basis_cents ? x.value_cents - x.cost_basis_cents : null;

export function combine(holdings: Holding[]): Group[] {
  const by = new Map<string, Holding[]>();
  for (const x of holdings) by.set(x.symbol, [...(by.get(x.symbol) ?? []), x]);
  return [...by.values()].map((parts) => {
    parts.sort((a, b) => b.value_cents - a.value_cents);
    const value = parts.reduce((s, x) => s + x.value_cents, 0);
    const known = parts.every((x) => x.cost_basis_cents);
    const shares = parts.every((x) => x.quantity != null);
    return {
      symbol: parts[0]!.symbol,
      name: parts[0]!.name,
      asset_class: parts[0]!.asset_class,
      value_cents: value,
      // Same symbol, same price: weighting by value only matters when an account's price is from an older sync.
      change_today_percent: value ? parts.reduce((s, x) => s + x.change_today_percent * x.value_cents, 0) / value : 0,
      cost_basis_cents: known ? parts.reduce((s, x) => s + (x.cost_basis_cents ?? 0), 0) : null,
      quantity: shares ? parts.reduce((s, x) => s + (x.quantity ?? 0), 0) : null,
      // The live price when an account has one (they all do at once), else the largest account's synced price.
      price: (parts.find((x) => x.live) ?? parts[0]!).price ?? null,
      live: parts.some((x) => x.live),
      parts,
    };
  });
}

/** What a holding gained or lost today, in cents: its value now less its value at yesterday's close. */
export const dayCents = (x: Group | Holding): number =>
  "parts" in x
    ? x.parts.reduce((s, p) => s + dayCents(p), 0)
    : x.change_today_percent > -100
      ? x.value_cents - x.value_cents / (1 + x.change_today_percent / 100)
      : 0;

/** A price has a meaning per share only for something that trades (not cash, not a balance with no shares). */
export const priceOf = (x: Group | Holding): number | null => (x.asset_class === "Cash" || !x.price || x.price <= 0 ? null : x.price);

/** Today's move of one share, from the price and today's percent. */
export const shareChange = (x: Group | Holding): number | null => {
  const p = priceOf(x);
  return p == null || x.change_today_percent <= -100 ? null : p - p / (1 + x.change_today_percent / 100);
};
