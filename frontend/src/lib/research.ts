/** Ask AI on a holding's page: research on one ticker with its sources (backend: ai/research.py). */

export type Sentiment = "positive" | "mixed" | "negative" | "unclear";

export type ResearchSource = { n: number; title: string; url: string; publisher: string; date: string };

export type ResearchAnswer = {
  sentiment: Sentiment;
  headline: string;
  answer: string;
  points: { side: "bull" | "bear" | "neutral"; text: string; sources: number[] }[];
  analysts: string;
  fundamentals: string;
  for_you: string;
  watch: string[];
  sources: ResearchSource[];
  caveats: string;
};

export type ResearchEntry = {
  at: string;
  question: string;
  model: string;
  cost: number;
  searched: number;
  web_search: boolean;
  headlines: number;
  answer: ResearchAnswer;
};

export type ResearchOffer = {
  available: boolean;
  problem: string | null;
  provider: string | null;
  model: string | null;
  max_cost: number | null;
  web_search: boolean;
  held: boolean;
  headlines: number;
  preview: string | null;
  suggested: string[];
  thread: ResearchEntry[];
};

export const SENTIMENT_TEXT: Record<Sentiment, string> = {
  positive: "Sentiment: positive",
  mixed: "Sentiment: mixed",
  negative: "Sentiment: negative",
  unclear: "Sentiment: unclear",
};

export const SIDE_TITLE = { bull: "In its favor", bear: "Against it", neutral: "Worth knowing" } as const;

export const askPath = (symbol: string) => `/api/securities/${encodeURIComponent(symbol)}/ask`;

/** "2 hours ago", "yesterday", "Oct 21": how fresh an answer is. */
export function ago(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hr ago`;
  if (s < 172800) return "yesterday";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}
