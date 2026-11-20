"use client";

import Link from "next/link";
import { Sparkles } from "lucide-react";
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
      {ask && (
        <Button variant="secondary" size="sm" className="justify-self-start" asChild>
          <Link href={askHref(d.symbol)}>
            <Sparkles /> Ask AI what to do
          </Link>
        </Button>
      )}
    </div>
  );
}
