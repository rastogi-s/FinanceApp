"use client";

export type Position = {
  symbol: string;
  name: string;
  asset_class: string;
  value_cents: number;
  percent: number;
  accounts: { account: string; value_cents: number }[];
};

export type Source = { symbol: string; cents: number; percent: number }; // symbol "direct" = held directly

export type Company = {
  key: string;
  symbol: string | null;
  name: string;
  cents: number;
  percent: number;
  direct_percent: number;
  sources: Source[];
};

export type FundRow = {
  symbol: string;
  name: string;
  cents: number;
  percent: number;
  status: "ok" | "new" | "not_found" | "error";
  error: string | null;
  proxy_of: string | null;
  as_of: string | null;
  holdings: number;
  equity_percent: number | null;
  top: { symbol: string | null; name: string; weight_percent: number }[];
};

export type Exposure = {
  total_cents: number;
  companies: Company[];
  funds: FundRow[];
  looked_through_percent: number;
  other_percent: number;
  oldest_as_of: string | null;
};

export type Sources = { quotes: "finnhub" | "yahoo" | "off"; lookthrough: boolean; fundamentals: boolean };

export type Portfolio = {
  total_cents: number;
  positions: Position[];
  top5_percent: number;
  largest: string | null;
  exposure: Exposure;
  prices_as_of: string | null;
  market_open: boolean;
  sources: Sources;
};

export type Range = "1d" | "5d" | "1m" | "6m" | "1y" | "5y";
export const RANGES: { id: Range; label: string }[] = [
  { id: "1d", label: "1D" },
  { id: "5d", label: "5D" },
  { id: "1m", label: "1M" },
  { id: "6m", label: "6M" },
  { id: "1y", label: "1Y" },
  { id: "5y", label: "5Y" },
];

export type PriceRule = {
  id: number;
  kind: "price";
  params: { symbol: string; above?: number; below?: number; move_percent?: number };
  enabled: boolean;
  summary: string;
  triggered: boolean;
};

export type Security = {
  symbol: string;
  name: string;
  type: string | null;
  is_fund: boolean;
  exchange: string | null;
  currency: string;
  price: number | null;
  previous_close: number | null;
  change_percent: number | null;
  price_at: number | null;
  range: Range;
  points: [number, number][];
  stats: {
    day_low?: number | null;
    day_high?: number | null;
    year_low?: number | null;
    year_high?: number | null;
    volume?: number | null;
    dividend_yield?: number | null;
    market_cap?: number;
    pe?: number;
    beta?: number;
    eps?: number;
    industry?: string;
    country?: string;
  };
  position: {
    quantity: number;
    value_cents: number;
    cost_basis_cents: number | null;
    gain_cents: number | null;
    gain_percent: number | null;
    percent_of_portfolio: number | null;
    accounts: { account: string; quantity: number; value_cents: number }[];
    as_of: string;
  } | null;
  exposure: Company | null;
  fund: FundRow | null;
  rules: PriceRule[];
  market_data: boolean;
  sources: Sources;
};

export const price = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const big = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 });
export const compactNumber = (n: number) => big.format(n);
export const percent = (n: number, digits = 1) => `${n.toFixed(digits)}%`;

export function holdingHref(symbol: string) {
  return `/portfolio/holding?s=${encodeURIComponent(symbol)}`;
}

/** Only plain tickers have a detail page (the same check the server makes). */
export function hasDetail(symbol: string | null | undefined, assetClass?: string) {
  if (!symbol || assetClass === "Cash") return false;
  return /^[A-Z][A-Z0-9]{0,5}([.-][A-Z])?$/.test(symbol);
}

const shortDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
export function asOf(iso: string) {
  return shortDate.format(new Date(`${iso}T12:00:00`));
}
