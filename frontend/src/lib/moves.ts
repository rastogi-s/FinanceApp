/** Any moves today?: a second opinion from AI on buying, selling or holding (backend: ai/moves.py). */

export type MoveAction = "buy" | "sell" | "trim" | "add" | "rebalance" | "harvest_loss";
export type Urgency = "today" | "this_week" | "this_month" | "no_rush";
export type Verdict = "hold" | "small_moves" | "act";
export type HoldingVerdict = "keep" | "watch" | "trim" | "add" | "sell";

export type Move = {
  action: MoveAction;
  symbol: string;
  name: string | null;
  held: boolean;
  account: string;
  amount_cents: number;
  urgency: Urgency;
  confidence: "low" | "medium" | "high";
  why: string;
  tax_impact: string;
  risks: string;
};

export type IndexRow = { name: string; level: number; change_percent?: number };

export type MovesAnswer = {
  verdict: Verdict;
  headline: string;
  summary: string;
  market: string;
  moves: Move[];
  holdings: { symbol: string; name: string; percent: number | null; verdict: HoldingVerdict; note: string }[];
  watch: string[];
  caveats: string;
  indexes: IndexRow[];
  headline_count: number;
};

export type MovesResult = {
  at: string;
  model: string;
  cost: number;
  question: string;
  prices_as_of: string | null;
  market_open: boolean;
  answer: MovesAnswer;
};

export type MovesOffer = {
  available: boolean;
  problem: string | null;
  provider: string | null;
  model: string | null;
  max_cost: number | null;
  holdings: number;
  headlines: number;
  preview: string | null;
  last: MovesResult | null;
};

export const ACTION_LABEL: Record<MoveAction, string> = {
  buy: "Buy",
  sell: "Sell",
  trim: "Trim",
  add: "Add to",
  rebalance: "Rebalance",
  harvest_loss: "Harvest loss",
};

export const URGENCY_LABEL: Record<Urgency, string> = {
  today: "Today",
  this_week: "This week",
  this_month: "This month",
  no_rush: "No rush",
};

export const VERDICT_TEXT: Record<Verdict, string> = {
  hold: "Hold steady",
  small_moves: "A few small moves",
  act: "Worth acting on",
};

/** Buying is green, selling is red, rebalancing (both) is neutral. */
export function actionTone(a: MoveAction): "positive" | "negative" | "default" {
  if (a === "buy" || a === "add") return "positive";
  if (a === "sell" || a === "trim" || a === "harvest_loss") return "negative";
  return "default";
}

export function holdingTone(v: HoldingVerdict): "positive" | "negative" | "warning" | "muted" {
  if (v === "add") return "positive";
  if (v === "trim" || v === "sell") return "negative";
  if (v === "watch") return "warning";
  return "muted";
}

const when = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const clock = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });

/** "today at 2:14 PM" or "Fri, Oct 23, 2:14 PM". */
export function checkedAt(iso: string): string {
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString() ? `today at ${clock.format(d)}` : when.format(d);
}
