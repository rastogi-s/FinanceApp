export type AIProviderRow = {
  id: number;
  kind: "anthropic" | "openai" | "gemini" | "openai_compatible";
  kind_name: string;
  label: string;
  base_url: string | null;
  key_hint: string | null;
  enabled: boolean;
  allow_pictures: boolean;
  models: string[];
  last_checked_at: string | null;
  last_error: string | null;
};

export type AIRoute = { provider_id: number; model: string; price_in?: number; price_out?: number };

export type AITask = {
  id: string;
  title: string;
  detail: string;
  pictures: boolean;
  route: AIRoute | null;
  price: { in: number; out: number; known: boolean } | null;
  ready: boolean;
  problem: string | null;
};

export type AIUsageRow = {
  id: number;
  at: string;
  who: string | null;
  task: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  priced: boolean;
  ok: boolean;
  error: string | null;
};

export type AIKind = {
  kind: AIProviderRow["kind"];
  name: string;
  needs_key: boolean;
  suggested_model: string | null;
  key_url: string | null;
};

export type AIOverview = {
  providers: AIProviderRow[];
  tasks: AITask[];
  cap_cents: number;
  spent: number; // dollars this month
  guessed: boolean;
  usage: AIUsageRow[];
  kinds: AIKind[];
  can_edit: boolean;
  encryption: boolean;
  error?: string;
  tried?: { ok: boolean; model: string; tokens: number; cost: number };
};

/** Dollars for AI costs, which are often fractions of a cent. */
export function usd(dollars: number): string {
  if (dollars === 0) return "$0";
  if (dollars < 0.01) return `$${dollars.toFixed(4)}`;
  return `$${dollars.toFixed(2)}`;
}
