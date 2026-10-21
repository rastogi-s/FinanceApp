"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Layers } from "lucide-react";
import { Money } from "@/components/money";
import { type Group, dayCents, gain, gainCents, priceOf, shareChange } from "@/lib/holdings";
import type { Holding } from "@/lib/household";
import { type Trend, hasDetail, holdingHref, price as priceFormat } from "@/lib/portfolio";
import { cn } from "@/lib/utils";

/** The sorts a column header can set; each column toggles between its two directions. */
export type TableSort =
  | "value"
  | "up"
  | "down"
  | "up-cents"
  | "down-cents"
  | "today"
  | "gain"
  | "loss"
  | "gain-cents"
  | "loss-cents"
  | "name";

type Row = Group | Holding;
export type Section = { account: string; cents: number; rows: Holding[] };

const shares = new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 });
const signed = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  signDisplay: "exceptZero",
});
const pct = (n: number) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(2)}%`;

// The sideways position, kept while the app is open, so coming back from a holding lands on the same columns.
let scrolledX = 0;

/**
 * Holdings as a table, Google Finance style: the ticker and name stay pinned on the left while the numbers scroll
 * sideways. Tap a row for the holding's chart and details. Column headers with arrows sort the list.
 */
export function HoldingsTable({
  rows,
  sections,
  trends,
  total,
  sort,
  onSort,
  hasGain,
}: {
  rows?: Row[];
  sections?: Section[];
  trends: Record<string, Trend> | undefined;
  total: number;
  sort: TableSort;
  onSort: (s: TableSort) => void;
  hasGain: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.scrollLeft = scrolledX;
    setScrolled(el.scrollLeft > 0);
  }, []);

  const columns: {
    id: string;
    label: string;
    sorts?: [TableSort, TableSort];
    needsCost?: boolean;
    hint?: string;
  }[] = [
    { id: "price", label: "Price" },
    { id: "change", label: "Change", hint: "Per share, today" },
    { id: "percent", label: "% change", sorts: ["up", "down"] },
    { id: "trend", label: "1 month" },
    { id: "value", label: "Value", sorts: ["value", "value"] },
    { id: "day", label: "Day gain", sorts: ["up-cents", "down-cents"] },
    {
      id: "gain",
      label: "Total gain",
      sorts: ["gain-cents", "loss-cents"],
      needsCost: true,
    },
    { id: "gainp", label: "Total %", sorts: ["gain", "loss"], needsCost: true },
    { id: "shares", label: "Shares" },
    { id: "cost", label: "Cost", needsCost: true },
    { id: "weight", label: "Weight" },
    { id: "range", label: "52-week range" },
    { id: "avg", label: "vs 50 / 200-day" },
    { id: "account", label: "Account" },
  ];
  const shown = columns.filter((c) => !c.needsCost || hasGain);

  const head = (
    <tr>
      <th
        scope="col"
        aria-sort={sort === "name" ? "ascending" : undefined}
        className={cn(
          "sticky left-0 z-20 border-b bg-card py-2 pr-3 pl-5 text-left text-xs font-medium text-muted-foreground",
          scrolled && "shadow-[6px_0_8px_-6px_rgb(0_0_0/0.25)]",
        )}
      >
        <SortButton active={sort === "name"} up onClick={() => onSort("name")}>
          Symbol
        </SortButton>
      </th>
      {shown.map((c) => {
        const active = c.sorts && (sort === c.sorts[0] || sort === c.sorts[1]);
        return (
          <th
            key={c.id}
            scope="col"
            title={c.hint}
            aria-sort={active ? (sort === c.sorts![1] && c.sorts![0] !== c.sorts![1] ? "ascending" : "descending") : undefined}
            className={cn(
              "border-b px-3 py-2 text-xs font-medium whitespace-nowrap text-muted-foreground",
              c.id === "account" || c.id === "trend" || c.id === "range" ? "text-left" : "text-right",
              c.id === "account" && "pr-5",
            )}
          >
            {c.sorts ? (
              <SortButton
                active={!!active}
                up={sort === c.sorts[1] && c.sorts[0] !== c.sorts[1]}
                right
                onClick={() => onSort(sort === c.sorts![0] ? c.sorts![1] : c.sorts![0])}
              >
                {c.label}
              </SortButton>
            ) : (
              c.label
            )}
          </th>
        );
      })}
    </tr>
  );

  const line = (x: Row, key: string) => (
    <Line key={key} x={x} trend={trends?.[x.symbol]} total={total} hasGain={hasGain} scrolled={scrolled} />
  );

  return (
    <div
      ref={box}
      onScroll={(e) => {
        scrolledX = e.currentTarget.scrollLeft;
        const s = scrolledX > 0;
        if (s !== scrolled) setScrolled(s);
      }}
      className="relative overflow-x-auto overscroll-x-contain pb-2 [scrollbar-width:thin]"
      role="region"
      aria-label="Holdings table, scrolls sideways"
      tabIndex={0}
    >
      <table className="w-max min-w-full border-separate border-spacing-0 text-sm">
        <thead>{head}</thead>
        {sections ? (
          sections.map((s) => (
            <tbody key={s.account}>
              <tr>
                <th
                  scope="rowgroup"
                  colSpan={shown.length + 1}
                  className="border-b bg-muted/40 p-0 text-left text-xs font-medium text-muted-foreground"
                >
                  {/* Pinned like the symbols, so the account's name stays in view while the numbers scroll. */}
                  <span className="sticky left-0 inline-flex max-w-[100vw] gap-2 px-5 py-2">
                    <span className="truncate">{s.account}</span>
                    <span className="shrink-0">
                      · {s.rows.length} · <Money cents={s.cents} />
                    </span>
                  </span>
                </th>
              </tr>
              {s.rows.map((x) => line(x, `${x.symbol}-${x.account}`))}
            </tbody>
          ))
        ) : (
          <tbody>{rows!.map((x) => line(x, x.symbol))}</tbody>
        )}
      </table>
    </div>
  );
}

function SortButton({
  active,
  up,
  right,
  onClick,
  children,
}: {
  active: boolean;
  up?: boolean;
  right?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  const Icon = up ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "-my-1 inline-flex items-center gap-0.5 rounded py-1 whitespace-nowrap hover:text-foreground",
        right && "flex-row-reverse",
        active && "text-foreground",
      )}
    >
      {children}
      <Icon className={cn("size-3 shrink-0", !active && "opacity-0")} aria-hidden />
    </button>
  );
}

function Line({ x, trend, total, hasGain, scrolled }: { x: Row; trend?: Trend; total: number; hasGain: boolean; scrolled: boolean }) {
  const link = useRef<HTMLAnchorElement>(null);
  const detail = hasDetail(x.symbol, x.asset_class);
  const parts = "parts" in x ? x.parts : null;
  const account = parts ? (parts.length > 1 ? null : parts[0]!.account) : (x as Holding).account;
  const p = priceOf(x);
  const move = shareChange(x);
  const g = gain(x);
  const gc = gainCents(x);
  const day = dayCents(x);
  const tone = (n: number | null) => (n == null || Math.abs(n) < 0.005 ? "" : n > 0 ? "text-positive" : "text-negative");
  const cell = "border-b px-3 py-3 text-right whitespace-nowrap tabular-nums";

  return (
    <tr
      className={cn("group", detail && "cursor-pointer")}
      // The whole row opens the holding, through its link, so going back returns to the same spot.
      onClick={(e) => {
        if (detail && !(e.target as Element).closest("a,button")) link.current?.click();
      }}
    >
      <th
        scope="row"
        className={cn(
          "sticky left-0 z-10 w-[8.5rem] max-w-[8.5rem] border-b bg-card py-2.5 pr-3 pl-5 text-left font-normal sm:w-56 sm:max-w-56",
          "transition-colors group-hover:bg-[color-mix(in_oklab,var(--muted)_50%,var(--card))]",
          scrolled && "shadow-[6px_0_8px_-6px_rgb(0_0_0/0.25)]",
        )}
      >
        {detail ? (
          <Link ref={link} href={holdingHref(x.symbol)} className="block focus-visible:outline-none" aria-label={`${x.symbol}, ${x.name}`}>
            <Name x={x} />
          </Link>
        ) : (
          <Name x={x} />
        )}
      </th>
      <td className={cn(cell, "font-medium group-hover:bg-muted/50")}>{p != null ? priceFormat.format(p) : "—"}</td>
      <td className={cn(cell, tone(move), "group-hover:bg-muted/50")}>{move != null && p != null ? signed.format(move) : "—"}</td>
      <td className={cn(cell, "group-hover:bg-muted/50")}>
        <Percent n={x.change_today_percent} />
      </td>
      <td className="border-b px-3 py-2 group-hover:bg-muted/50">
        <Sparkline closes={trend?.closes ?? []} last={p} />
      </td>
      <td className={cn(cell, "font-medium group-hover:bg-muted/50")}>
        <Money cents={x.value_cents} />
      </td>
      <td className={cn(cell, tone(day), "group-hover:bg-muted/50")}>
        <Money cents={Math.round(day)} signed />
      </td>
      {hasGain && (
        <>
          <td className={cn(cell, tone(gc), "group-hover:bg-muted/50")}>{gc != null ? <Money cents={gc} signed /> : "—"}</td>
          <td className={cn(cell, tone(g), "group-hover:bg-muted/50")}>{g != null ? pct(g * 100) : "—"}</td>
        </>
      )}
      <td className={cn(cell, "group-hover:bg-muted/50")}>
        {x.quantity != null && x.asset_class !== "Cash" ? <span className="money">{shares.format(x.quantity)}</span> : "—"}
      </td>
      {hasGain && (
        <td className={cn(cell, "group-hover:bg-muted/50")}>{x.cost_basis_cents ? <Money cents={x.cost_basis_cents} /> : "—"}</td>
      )}
      <td className={cn(cell, "group-hover:bg-muted/50")}>{total ? `${((x.value_cents / total) * 100).toFixed(1)}%` : "—"}</td>
      <td className="border-b px-3 py-3 group-hover:bg-muted/50">
        <Range price={p} trend={trend} />
      </td>
      <td className={cn(cell, "text-xs group-hover:bg-muted/50")}>
        <Averages price={p} trend={trend} />
      </td>
      <td className="max-w-56 truncate border-b px-3 py-3 pr-5 text-left text-muted-foreground group-hover:bg-muted/50">
        {account ?? (
          <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-foreground">
            <Layers className="size-3" aria-hidden />
            {parts!.length} accounts
          </span>
        )}
      </td>
    </tr>
  );
}

function Name({ x }: { x: Row }) {
  return (
    <>
      <span className="flex items-center gap-1.5 font-semibold">
        {x.symbol}
        {x.live && <span className="size-1.5 rounded-full bg-positive" title="Live price" aria-label="live price" />}
      </span>
      <span className="block truncate text-xs text-muted-foreground">{x.name}</span>
    </>
  );
}

/** Today's percent with its direction as an arrow and a sign, not only a color. */
function Percent({ n }: { n: number }) {
  const r = Math.round(n * 100);
  const dir = r > 0 ? "up" : r < 0 ? "down" : "flat";
  const Icon = dir === "up" ? ArrowUp : ArrowDown;
  return (
    <span
      className={cn(
        "inline-flex items-center justify-end gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium",
        dir === "up" ? "bg-positive/12 text-positive" : dir === "down" ? "bg-negative/12 text-negative" : "text-muted-foreground",
      )}
    >
      {dir !== "flat" && <Icon className="size-3" aria-hidden />}
      <span className="sr-only">{dir === "up" ? "Up" : dir === "down" ? "Down" : "Unchanged"} </span>
      {Math.abs(n).toFixed(2)}%
    </span>
  );
}

/**
 * The last month of daily closes, ending at today's price, against a dotted line at where the month began.
 * Green when it's up over the month, red when down; empty until the day's closes have been looked up.
 */
export function Sparkline({ closes, last, className }: { closes: number[]; last: number | null; className?: string }) {
  const points = last != null && closes.length ? [...closes, last] : closes;
  const W = 72;
  const H = 28;
  if (points.length < 2) return <span className="block h-7 w-[72px] text-xs text-muted-foreground">—</span>;
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const y = (v: number) => 2 + (H - 4) * (1 - (v - lo) / span);
  const x = (i: number) => (i / (points.length - 1)) * W;
  const d = points.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const up = points.at(-1)! >= points[0]!;
  const change = ((points.at(-1)! - points[0]!) / points[0]!) * 100;
  return (
    <svg
      width={W}
      height={H}
      viewBox={`0 0 ${W} ${H}`}
      className={cn("block overflow-visible", up ? "text-positive" : "text-negative", className)}
      role="img"
      aria-label={`${up ? "Up" : "Down"} ${Math.abs(change).toFixed(1)}% over the last month`}
    >
      <line
        x1={0}
        x2={W}
        y1={y(points[0]!)}
        y2={y(points[0]!)}
        className="stroke-muted-foreground/50"
        strokeWidth={1}
        strokeDasharray="1 2"
      />
      <path d={`${d}L${W},${H}L0,${H}Z`} fill="currentColor" opacity={0.12} />
      <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={W} cy={y(points.at(-1)!)} r={2} fill="currentColor" />
    </svg>
  );
}

/** Where the price sits between its 52-week low and high. */
function Range({ price, trend }: { price: number | null; trend?: Trend }) {
  const lo = trend?.year_low;
  const hi = trend?.year_high;
  if (price == null || lo == null || hi == null || hi <= lo) return <span className="text-muted-foreground">—</span>;
  const at = Math.min(Math.max((price - lo) / (hi - lo), 0), 1);
  return (
    <span className="flex w-36 items-center gap-2 text-xs text-muted-foreground tabular-nums">
      <span>{lo.toFixed(0)}</span>
      <span className="relative h-1 flex-1 rounded-full bg-muted" aria-hidden>
        <span
          className="absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-foreground"
          style={{ left: `${at * 100}%` }}
        />
      </span>
      <span>{hi.toFixed(0)}</span>
      <span className="sr-only">, now {Math.round(at * 100)}% of the way from the low to the high</span>
    </span>
  );
}

/** How far the price is above or below its 50- and 200-day averages. */
function Averages({ price, trend }: { price: number | null; trend?: Trend }) {
  const one = (avg: number | null | undefined) => {
    if (price == null || !avg) return <span className="text-muted-foreground">—</span>;
    const n = ((price - avg) / avg) * 100;
    return <span className={n >= 0 ? "text-positive" : "text-negative"}>{pct(n)}</span>;
  };
  return (
    <span className="inline-flex gap-1.5">
      {one(trend?.average_50)}
      <span className="text-muted-foreground">/</span>
      {one(trend?.average_200)}
    </span>
  );
}
