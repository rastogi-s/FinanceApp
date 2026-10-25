"use client";

import { useRef, useState } from "react";
import { formatMoney } from "@/components/money";
import { monthLabel } from "@/lib/income";
import { cn } from "@/lib/utils";

/** Round numbers for the gridlines: steps of 1, 2, 2.5 or 5 times a power of ten. */
export function niceStep(max: number) {
  const raw = max / 3;
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? raw;
}

/** Picking a point by dragging across, or with the arrow keys once focused (the same feel as the Plan chart). */
export function usePicker(count: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  const pick = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || !count) return;
    setActive(Math.min(count - 1, Math.max(0, Math.floor(((clientX - box.left) / box.width) * count))));
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    let i = active ?? count - 1;
    if (e.key === "ArrowLeft") i = Math.max(0, i - 1);
    else if (e.key === "ArrowRight") i = Math.min(count - 1, i + 1);
    else if (e.key === "Home") i = 0;
    else if (e.key === "End") i = count - 1;
    else if (e.key === "Escape") return setActive(null);
    else return;
    e.preventDefault();
    setActive(i);
  };
  const handlers = {
    ref,
    onPointerMove: (e: React.PointerEvent) => pick(e.clientX),
    onPointerDown: (e: React.PointerEvent) => pick(e.clientX),
    onPointerLeave: () => setActive(null),
    onBlur: () => setActive(null),
    onKeyDown,
  };
  return { active, handlers };
}

export type Bar = { month: string; cents: number; expected?: boolean };

/**
 * Money by month as bars: what came in (solid) and, after it, what's expected (lighter). Drag across, or focus and
 * use the arrow keys, to read a month.
 */
export function MonthBars({ bars, label, hint }: { bars: Bar[]; label: string; hint: string }) {
  const { active, handlers } = usePicker(bars.length);
  const top = Math.max(...bars.map((b) => b.cents), 1);
  const step = niceStep(top);
  const max = Math.ceil((top * 1.05) / step) * step;
  const grid: number[] = [];
  for (let v = step; v < max; v += step) grid.push(v);
  const a = active !== null ? bars[active]! : null;
  const readout = a ? `${monthLabel(a.month, true)}: ${formatMoney(a.cents)}${a.expected ? " expected" : ""}` : null;
  const firstExpected = bars.findIndex((b) => b.expected);
  const labelEvery = bars.length > 12 ? 6 : 3;

  return (
    <div>
      <p className="money mb-2 h-5 text-sm font-medium tabular-nums" aria-live="polite">
        {readout ?? <span className="text-muted-foreground">{hint}</span>}
      </p>
      <div
        {...handlers}
        role="slider"
        tabIndex={0}
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={bars.length - 1}
        aria-valuenow={active ?? bars.length - 1}
        aria-valuetext={readout ?? "Latest month"}
        className="money relative h-44 w-full touch-pan-y rounded-lg outline-none select-none focus-visible:ring-2 focus-visible:ring-ring md:h-52"
      >
        <div className="absolute inset-x-0 top-0 bottom-6">
          {grid.map((v) => (
            <div key={v} className="absolute inset-x-0 border-t border-border/70" style={{ bottom: `${(v / max) * 100}%` }}>
              <span className="absolute left-0 z-10 -translate-y-1/2 rounded bg-card/85 px-1 text-[11px] text-muted-foreground">{formatMoney(v, "compact")}</span>
            </div>
          ))}
          <div className="absolute inset-x-0 bottom-0 border-t border-border" />
          <div className="absolute inset-0 flex items-end gap-[2px] md:gap-1">
            {bars.map((b, i) => (
              <div key={b.month} className="flex h-full flex-1 items-end">
                <div
                  className={cn(
                    "w-full rounded-t-[3px] transition-opacity",
                    b.expected ? "bg-chart-1/35" : "bg-chart-1",
                    active !== null && active !== i && "opacity-45",
                  )}
                  style={{ height: `${Math.max((b.cents / max) * 100, b.cents > 0 ? 1.5 : 0)}%` }}
                />
              </div>
            ))}
          </div>
          {firstExpected > 0 && (
            <div
              className="pointer-events-none absolute top-0 bottom-0 border-l border-dashed border-muted-foreground/50"
              style={{ left: `${(firstExpected / bars.length) * 100}%` }}
            />
          )}
        </div>
        <div className="absolute inset-x-0 bottom-0 flex h-5">
          {bars.map((b, i) => (
            <span key={b.month} className="relative flex-1">
              {i % labelEvery === 0 && (
                <span className="absolute left-0 text-[11px] whitespace-nowrap text-muted-foreground">
                  {monthLabel(b.month)}
                  {b.month.endsWith("-01") || i === 0 ? ` ’${b.month.slice(2, 4)}` : ""}
                </span>
              )}
            </span>
          ))}
        </div>
      </div>
      {firstExpected > 0 && (
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-chart-1" aria-hidden /> Paid
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-chart-1/35" aria-hidden /> Expected
          </span>
        </div>
      )}
    </div>
  );
}

/** Two lines over the years: with reinvesting and without. Drag across to read a year. */
export function TwoLines({
  on,
  off,
  onLabel,
  offLabel,
  label,
  highlight,
}: {
  on: number[];
  off: number[];
  onLabel: string;
  offLabel: string;
  label: string;
  highlight: "on" | "off";
}) {
  const { active, handlers } = usePicker(on.length);
  const w = 640;
  const h = 220;
  const pad = { t: 12, b: 24 };
  const top = Math.max(...on, ...off, 1);
  const step = niceStep(top);
  const max = Math.ceil((top * 1.04) / step) * step;
  const x = (i: number) => ((i + 0.5) / on.length) * w;
  const y = (v: number) => pad.t + (1 - v / max) * (h - pad.t - pad.b);
  const path = (vs: number[]) => vs.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const grid: number[] = [];
  for (let v = step; v < max; v += step) grid.push(v);
  const readout =
    active !== null
      ? `Year ${active + 1}: ${formatMoney(on[active]!)} ${onLabel.toLowerCase()}, ${formatMoney(off[active]!)} ${offLabel.toLowerCase()}`
      : null;
  const pct = (v: number, of: number) => `${(v / of) * 100}%`;

  return (
    <div>
      <p className="money mb-2 min-h-5 text-sm font-medium tabular-nums" aria-live="polite">
        {readout ?? <span className="text-muted-foreground">Drag across the chart to read any year</span>}
      </p>
      <div
        {...handlers}
        role="slider"
        tabIndex={0}
        aria-label={label}
        aria-valuemin={1}
        aria-valuemax={on.length}
        aria-valuenow={(active ?? on.length - 1) + 1}
        aria-valuetext={readout ?? `Year ${on.length}`}
        className="money relative h-52 w-full touch-pan-y rounded-lg outline-none select-none focus-visible:ring-2 focus-visible:ring-ring md:h-60"
      >
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="absolute inset-0 size-full" aria-hidden>
          {grid.map((v) => (
            <line key={v} x1={0} x2={w} y1={y(v)} y2={y(v)} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          ))}
          <line x1={0} x2={w} y1={y(0)} y2={y(0)} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <path
            d={path(off)}
            fill="none"
            stroke="var(--chart-5)"
            strokeWidth={highlight === "off" ? 2.5 : 2}
            strokeDasharray={highlight === "off" ? undefined : "5 4"}
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={path(on)}
            fill="none"
            stroke="var(--chart-1)"
            strokeWidth={highlight === "on" ? 2.5 : 2}
            strokeDasharray={highlight === "on" ? undefined : "5 4"}
            vectorEffect="non-scaling-stroke"
          />
          {active !== null && (
            <line x1={x(active)} x2={x(active)} y1={0} y2={h - pad.b} stroke="var(--foreground)" strokeOpacity="0.35" vectorEffect="non-scaling-stroke" />
          )}
        </svg>
        {grid.map((v) => (
          <span key={v} className="pointer-events-none absolute left-0 -translate-y-full pb-0.5 text-[11px] text-muted-foreground" style={{ top: pct(y(v), h) }}>
            {formatMoney(v, "compact")}
          </span>
        ))}
        {on.map((_, i) =>
          i === 0 || (i + 1) % 5 === 0 ? (
            <span
              key={i}
              className={cn(
                "pointer-events-none absolute bottom-0 text-[11px] whitespace-nowrap text-muted-foreground",
                i === on.length - 1 ? "-translate-x-full" : i === 0 ? "" : "-translate-x-1/2",
              )}
              style={{ left: i === on.length - 1 ? "100%" : i === 0 ? 0 : pct(x(i), w) }}
            >
              {i === 0 ? "Yr 1" : `Yr ${i + 1}`}
            </span>
          ) : null,
        )}
        {active !== null &&
          [
            [on[active]!, "bg-[var(--chart-1)]"],
            [off[active]!, "bg-[var(--chart-5)]"],
          ].map(([v, c]) => (
            <span
              key={c as string}
              className={cn("pointer-events-none absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card", c as string)}
              style={{ left: pct(x(active), w), top: pct(y(v as number), h) }}
              aria-hidden
            />
          ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded bg-[var(--chart-1)]" aria-hidden /> {onLabel}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded bg-[var(--chart-5)]" aria-hidden /> {offLabel}
        </span>
      </div>
    </div>
  );
}
