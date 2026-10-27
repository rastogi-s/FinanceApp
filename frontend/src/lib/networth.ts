// The Net worth sheet's numbers (backend/financeapp/networth.py): how today's figure adds up, and what moved it.

export type GroupKey = "cash" | "investments" | "retirement" | "other" | "cards" | "loans";

export type NetWorthGroup = {
  key: GroupKey;
  label: string;
  about: string;
  owed: boolean;
  cents: number; // what's owed is negative
  accounts: { id: number; name: string; cents: number; as_of: string | null; stale: boolean }[];
};

export type WindowAccount = {
  id: number;
  name: string;
  group: GroupKey;
  start_cents: number;
  end_cents: number;
  change_cents: number;
  market_cents: number | null; // null: not an investment account, or it can't be split
  other_cents: number | null;
  split: "holdings" | "activity" | "none" | "n/a";
  since: string;
  new: boolean; // first synced after the window started: counted from then
};

export type NetWorthWindow = {
  key: "today" | "week" | "month" | "3m" | "ytd" | "year";
  label: string;
  start: string;
  since: string; // later than start when the history doesn't reach back that far
  change_cents: number;
  market_cents: number;
  saving_cents: number;
  unsplit_cents: number;
  new_accounts: string[];
  accounts: WindowAccount[];
  movers: { symbol: string; name: string; cents: number }[];
};

export type NetWorth =
  | { linked: false }
  | {
      linked: true;
      cents: number;
      assets_cents: number;
      owed_cents: number;
      within_reach_cents: number;
      groups: NetWorthGroup[];
      windows: NetWorthWindow[];
      history: { date: string; cents: number }[];
      unvested: { cents: number; holdings: { symbol: string; name: string; shares: number; value_cents: number; account: string }[] };
    };

/** One color per group, the same in the bar, its legend and the rows. */
export const GROUP_COLOR: Record<GroupKey, string> = {
  cash: "bg-chart-2",
  investments: "bg-chart-1",
  retirement: "bg-chart-4",
  other: "bg-chart-5",
  cards: "bg-chart-3",
  loans: "bg-chart-3",
};
