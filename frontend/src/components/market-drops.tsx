"use client";

import Link from "next/link";
import { Activity, Percent, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** How far a market index is under its record close (backend alerts.drawdown). */
export type MarketDrop = {
  symbol: string;
  name: string;
  level: number;
  peak: number;
  peak_on: string | null;
  under_percent: number;
};

const level = (x: number) => x.toLocaleString("en-US", { maximumFractionDigits: 0 });
const day = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
export const underText = (x: number) => `${x < 0.1 ? "0" : x.toFixed(1).replace(/\.0$/, "")}%`;

/** The usual names for a fall from the high: a pullback, a correction (10%), a bear market (20%). */
export function dropStage(under: number): { label: string; tone: "muted" | "warning" | "negative" } {
  if (under >= 20) return { label: "Bear market", tone: "negative" };
  if (under >= 10) return { label: "Correction", tone: "warning" };
  if (under >= 5) return { label: "Pullback", tone: "muted" };
  if (under < 0.1) return { label: "At its high", tone: "muted" };
  return { label: "Near its high", tone: "muted" };
}

/** The question "Ask AI what to do" starts with, about one index's fall. */
export function dropQuestion(d: MarketDrop): string {
  return `The ${d.name} is ${underText(d.under_percent)} under its record high. With my holdings, cash and goals, should I invest more, hold or sell?`;
}

/** The VIX now, and a month ago (backend alerts.vix_now). */
export type Vix = { level: number; month_ago: number | null; as_of: string };
/** The 10-year and 2-year Treasury yields, and the gap (negative: an inverted curve). From FRED, daily. */
export type Yields = { ten_year: number; two_year: number; spread: number; as_of: string };
export type Markets = { drops?: MarketDrop[]; vix?: Vix | null; yields?: Yields | null };

export function vixStage(v: number): { label: string; tone: "muted" | "warning" | "negative" } {
  if (v >= 40) return { label: "Panic", tone: "negative" };
  if (v >= 30) return { label: "Fear", tone: "negative" };
  if (v >= 20) return { label: "Nervous", tone: "warning" };
  return { label: "Calm", tone: "muted" };
}

export function vixQuestion(v: Vix): string {
  return `The VIX is at ${v.level.toFixed(1)}. With my holdings, cash and goals, should I invest more, hold or sell?`;
}

export function yieldsQuestion(y: Yields): string {
  const curve = y.spread < 0 ? " and the yield curve is inverted" : "";
  return `The 10-year Treasury yields ${y.ten_year.toFixed(2)}%${curve}. With my holdings, cash and goals, should I change anything, like moving cash into Treasuries or CDs?`;
}

/** The question for the indicator a link names: an index symbol, ^VIX, or "yields". */
export function questionFor(m: Markets, key: string): string | null {
  if (key === "^VIX") return m.vix ? vixQuestion(m.vix) : null;
  if (key === "yields") return m.yields ? yieldsQuestion(m.yields) : null;
  const d = (m.drops ?? []).find((x) => x.symbol === key);
  return d ? dropQuestion(d) : null;
}

export function VixRow({ v, ask = true }: { v: Vix; ask?: boolean }) {
  const stage = vixStage(v.level);
  return (
    <div className="grid grid-cols-1 gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="flex items-center gap-1.5 text-sm font-medium">
          <Activity className="size-4 text-muted-foreground" aria-hidden /> VIX{" "}
          <span className="font-normal tabular-nums text-muted-foreground">{v.level.toFixed(1)}</span>
        </p>
        <Badge variant={stage.tone}>{stage.label}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        How big a swing stocks are expected to make.{v.month_ago ? ` ${v.month_ago.toFixed(1)} a month ago.` : ""} Under 20 is calm; over 30, fear.
      </p>
      {ask && <AskButton href={askHref("^VIX")} />}
    </div>
  );
}

export function YieldsRow({ y, ask = true }: { y: Yields; ask?: boolean }) {
  const inverted = y.spread < 0;
  return (
    <div className="grid grid-cols-1 gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="flex items-center gap-1.5 text-sm font-medium">
          <Percent className="size-4 text-muted-foreground" aria-hidden /> 10-year Treasury{" "}
          <span className="font-normal tabular-nums text-muted-foreground">{y.ten_year.toFixed(2)}%</span>
        </p>
        <Badge variant={inverted ? "warning" : "muted"}>{inverted ? "Curve inverted" : "Curve normal"}</Badge>
      </div>
      <p className="text-xs text-muted-foreground">
        The 2-year pays <span className="tabular-nums">{y.two_year.toFixed(2)}%</span>,{" "}
        <span className="tabular-nums">{Math.abs(y.spread).toFixed(2)}</span> points {inverted ? "more" : "less"} than the 10-year.
        {inverted ? " An inverted curve has come before most recessions, often a year or more ahead." : ""} As of {day(y.as_of)}.
      </p>
      {ask && <AskButton href={askHref("yields")} />}
    </div>
  );
}

function AskButton({ href }: { href: string }) {
  return (
    <Button variant="secondary" size="sm" className="justify-self-start" asChild>
      <Link href={href}>
        <Sparkles /> Ask AI what to do
      </Link>
    </Button>
  );
}

export const askHref = (symbol: string) => `/portfolio/moves?market=${encodeURIComponent(symbol)}`;

/** One index: where it is, how far under its record close, and a bar from the record down to 30% under. */
export function MarketDropRow({ d, ask = true }: { d: MarketDrop; ask?: boolean }) {
  const stage = dropStage(d.under_percent);
  const fill = Math.min(d.under_percent / 30, 1) * 100;
  return (
    <div className="grid grid-cols-1 gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-sm font-medium">
          {d.name} <span className="font-normal tabular-nums text-muted-foreground">{level(d.level)}</span>
        </p>
        <Badge variant={stage.tone}>{stage.label}</Badge>
      </div>
      <div className="relative h-2 overflow-hidden rounded-full bg-muted" aria-hidden>
        <div
          className={cn(
            "absolute inset-y-0 left-0 rounded-full",
            d.under_percent >= 20 ? "bg-negative" : d.under_percent >= 10 ? "bg-warning" : "bg-chart-5",
          )}
          style={{ width: `${Math.max(fill, d.under_percent > 0 ? 2 : 0)}%` }}
        />
        {[10, 20].map((m) => (
          <span key={m} className="absolute inset-y-0 w-px bg-background" style={{ left: `${(m / 30) * 100}%` }} />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        <span className="font-medium tabular-nums text-foreground">{underText(d.under_percent)}</span> under its record close of{" "}
        <span className="tabular-nums">{level(d.peak)}</span>
        {d.peak_on ? ` on ${day(d.peak_on)}` : ""}.
      </p>
      {ask && <AskButton href={askHref(d.symbol)} />}
    </div>
  );
}
