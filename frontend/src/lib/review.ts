/** Check categories (/api/review): what the app isn't sure about, grouped by merchant or person. */

export type Reason = "no_category" | "guess" | "sent" | "money_in" | "refund";

export type ReviewGroup = {
  key: string;
  reason: Reason;
  person: string | null;
  merchant: string;
  count: number;
  total_cents: number;
  category_id: number | null;
  category: string;
  last_date: string;
  ids: string[];
  examples: { date: string; amount_cents: number; description: string; account: string }[];
};

export type ReviewState = {
  since: string;
  score: number | null;
  checked_cents: number;
  doubtful_cents: number;
  items: number;
  kept_out: { card_cents: number; invest_cents: number; own_cents: number };
  groups: ReviewGroup[];
};

export type AISuggestion = { category_id: number; category: string; reason: string };

/** Each kind of doubt: its heading, what it means for the numbers, and the order they're shown in. */
export const REASONS: { id: Reason; title: string; detail: string }[] = [
  {
    id: "sent",
    title: "Sent to someone",
    detail:
      "Left out of spending for now, so your savings rate counts it as saved. If it paid for something, pick what. If it went to another account of yours, say so.",
  },
  {
    id: "no_category",
    title: "No category yet",
    detail: "Counted as spending, but in no budget line. Pick where it belongs.",
  },
  {
    id: "money_in",
    title: "Money in",
    detail: "Counted as income in your savings rate. If it was a refund or your own money moving, say so.",
  },
  {
    id: "refund",
    title: "Money back",
    detail: "Comes off spending. Pick what it was a refund for, so it comes off the right budget line.",
  },
  {
    id: "guess",
    title: "Best guesses",
    detail: "Picked from a word in the name or the bank's broadest group. Confirm or change them.",
  },
];

/** "Zelle to Maria Lopez", or the merchant. */
export function groupTitle(g: ReviewGroup): string {
  if (!g.person) return g.merchant;
  return `${g.merchant} ${g.total_cents < 0 ? "to" : "from"} ${g.person}`;
}
