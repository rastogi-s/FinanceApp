"use client";

import { useMemo, useRef, useState } from "react";
import { formatMoney } from "@/components/money";
import type { Band } from "@/lib/plan";

const monthYear = new Intl.DateTimeFormat("en-US", {
  month: "short",
  year: "numeric",
});

type Point = {
  t: number;
  past?: number;
  low?: number;
  mid?: number;
  high?: number;
  label: string;
};

function yearOf(iso: string) {
  const d = new Date(iso + "T12:00:00");
  const start = new Date(d.getFullYear(), 0, 1).getTime();
  const end = new Date(d.getFullYear() + 1, 0, 1).getTime();
  return d.getFullYear() + (d.getTime() - start) / (end - start);
}

/** Round numbers for the gridlines: 0, then steps of 1, 2 or 5 times a power of ten. */
function niceStep(max: number) {
  const raw = max / 3;
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) ?? raw;
}

/**
 * Net worth so far (a solid line) running into the projection (a shaded range from the low to the high return,
 * with the middle as a line), the amount that's enough as a dashed line, and a dot where the middle reaches it.
 * Drag across it, or focus it and use the arrow keys, to read any year.
 */
export function PlanChart({
  history,
  bands,
  target,
  fireYear,
  birthYear,
  years,
}: {
  history: { date: string; cents: number }[];
  bands: Band[];
  target: number | null;
  fireYear: number | null;
  birthYear: number | null;
  years: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  const w = 640;
  const h = 240;
  const pad = { t: 14, b: 26 };

  const shape = useMemo(() => {
    const now = history.length ? yearOf(history[history.length - 1]!.date) : bands[0]!.year + 0.8;
    const future = bands.slice(0, years + 1);
    const points: Point[] = [
      ...history.map((p) => ({
        t: yearOf(p.date),
        past: p.cents,
        label: monthYear.format(new Date(p.date + "T12:00:00")),
      })),
      ...future.map((b, i) => ({
        t: now + i,
        low: b.low,
        mid: b.mid,
        high: b.high,
        past: i === 0 ? b.mid : undefined,
        label: String(b.year),
      })),
    ];
    const t0 = points[0]!.t;
    const t1 = points[points.length - 1]!.t;
    const top = Math.max(
      ...points.map((p) => p.high ?? p.past ?? 0),
      target && target < future[future.length - 1]!.high * 1.6 ? target : 0,
      1,
    );
    const step = niceStep(top);
    const max = Math.ceil((top * 1.04) / step) * step;
    const x = (t: number) => ((t - t0) / (t1 - t0 || 1)) * w;
    const y = (v: number) => pad.t + (1 - Math.max(v, 0) / max) * (h - pad.t - pad.b);
    const pathOf = (pts: Point[], key: "past" | "low" | "mid" | "high") =>
      pts.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p[key]!).toFixed(1)}`).join(" ");
    const fut = points.filter((p) => p.mid !== undefined);
    const past = points.filter((p) => p.past !== undefined);
    const area = `${pathOf(fut, "high")} ${[...fut]
      .reverse()
      .map((p) => `L${x(p.t).toFixed(1)},${y(p.low!).toFixed(1)}`)
      .join(" ")} Z`;
    const grid = [];
    for (let v = step; v < max; v += step) grid.push(v);
    // Year labels: about five, on whole years in the future part.
    const every = Math.max(1, Math.ceil(fut.length / 5));
    const ticks = fut.filter((_, i) => i % every === 0).map((p) => ({ x: x(p.t), label: p.label }));
    const fire = fireYear !== null ? fut.find((p) => Number(p.label) === fireYear) : undefined;
    return {
      points,
      x,
      y,
      max,
      grid,
      ticks,
      area,
      now: x(now),
      pastLine: pathOf(past, "past"),
      mid: pathOf(fut, "mid"),
      fire,
    };
  }, [history, bands, years, target, fireYear, pad.t, pad.b]);

  const pick = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const t = ((clientX - box.left) / box.width) * w;
    let best = 0;
    shape.points.forEach((p, i) => {
      if (Math.abs(shape.x(p.t) - t) < Math.abs(shape.x(shape.points[best]!.t) - t)) best = i;
    });
    setActive(best);
  };
  const key = (e: React.KeyboardEvent) => {
    const last = shape.points.length - 1;
    let i = active ?? last;
    if (e.key === "ArrowLeft") i = Math.max(0, i - (e.shiftKey ? 10 : 1));
    else if (e.key === "ArrowRight") i = Math.min(last, i + (e.shiftKey ? 10 : 1));
    else if (e.key === "Home") i = 0;
    else if (e.key === "End") i = last;
    else if (e.key === "Escape") return setActive(null);
    else return;
    e.preventDefault();
    setActive(i);
  };

  const a = active !== null ? shape.points[active]! : null;
  const isFuture = a ? a.mid !== undefined && a.past === undefined : false;
  const age = a && birthYear && isFuture ? Number(a.label) - birthYear : null;
  const readout = a
    ? isFuture
      ? `${a.label}${age ? `, age ${age}` : ""}: about ${formatMoney(a.mid!, "compact")} (${formatMoney(a.low!, "compact")} to ${formatMoney(a.high!, "compact")})`
      : `${a.label}: ${formatMoney(a.past!, "compact")}`
    : null;
  const pct = (v: number, of: number) => `${(v / of) * 100}%`;

  return (
    <div>
      <p className="money mb-2 h-5 text-sm font-medium tabular-nums" aria-live="polite">
        {readout ?? <span className="text-muted-foreground">Drag across the chart to read any year</span>}
      </p>
      <div
        ref={ref}
        role="slider"
        tabIndex={0}
        aria-label="Net worth so far and projected"
        aria-valuemin={0}
        aria-valuemax={shape.points.length - 1}
        aria-valuenow={active ?? shape.points.length - 1}
        aria-valuetext={readout ?? "Latest"}
        className="money relative h-56 w-full touch-pan-y rounded-lg outline-none select-none focus-visible:ring-2 focus-visible:ring-ring md:h-64"
        onPointerMove={(e) => pick(e.clientX)}
        onPointerDown={(e) => pick(e.clientX)}
        onPointerLeave={() => setActive(null)}
        onKeyDown={key}
        onBlur={() => setActive(null)}
      >
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="absolute inset-0 size-full" aria-hidden>
          {shape.grid.map((v) => (
            <line
              key={v}
              x1={0}
              x2={w}
              y1={shape.y(v)}
              y2={shape.y(v)}
              stroke="var(--border)"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
          ))}
          <line x1={0} x2={w} y1={shape.y(0)} y2={shape.y(0)} stroke="var(--border)" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <path d={shape.area} fill="var(--chart-1)" opacity="0.14" />
          {target !== null && target <= shape.max && (
            <line
              x1={0}
              x2={w}
              y1={shape.y(target)}
              y2={shape.y(target)}
              stroke="var(--chart-3)"
              strokeDasharray="5 5"
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
            />
          )}
          <path
            d={shape.mid}
            fill="none"
            stroke="var(--chart-1)"
            strokeWidth="2"
            strokeDasharray="1 0"
            opacity="0.8"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={shape.pastLine}
            fill="none"
            stroke="var(--chart-1)"
            strokeWidth="2.5"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
          <line
            x1={shape.now}
            x2={shape.now}
            y1={pad.t}
            y2={shape.y(0)}
            stroke="var(--muted-foreground)"
            strokeOpacity="0.4"
            strokeDasharray="2 4"
            vectorEffect="non-scaling-stroke"
          />
          {a && (
            <line
              x1={shape.x(a.t)}
              x2={shape.x(a.t)}
              y1={0}
              y2={h - pad.b}
              stroke="var(--foreground)"
              strokeOpacity="0.35"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
        {shape.grid.map((v) => (
          <span
            key={v}
            className="pointer-events-none absolute left-0 -translate-y-full pb-0.5 text-[11px] text-muted-foreground"
            style={{ top: pct(shape.y(v), h) }}
          >
            {formatMoney(v, "compact")}
          </span>
        ))}
        {target !== null && target <= shape.max && (
          <span
            className="pointer-events-none absolute right-0 -translate-y-full rounded bg-card/80 pb-0.5 pl-1 text-[11px] font-medium"
            style={{ top: pct(shape.y(target), h), color: "var(--chart-3)" }}
          >
            Enough: {formatMoney(target, "compact")}
          </span>
        )}
        {shape.ticks.map((t, i) => (
          <span
            key={t.label}
            className="pointer-events-none absolute bottom-0 text-[11px] text-muted-foreground"
            style={{
              left: pct(t.x, w),
              transform: i === 0 ? "none" : "translateX(-50%)",
            }}
          >
            {i === 0 ? "Now" : t.label}
          </span>
        ))}
        {shape.fire && (
          <span
            className="pointer-events-none absolute size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card"
            style={{
              left: pct(shape.x(shape.fire.t), w),
              top: pct(shape.y(shape.fire.mid!), h),
              background: "var(--chart-3)",
            }}
            aria-hidden
          />
        )}
        {a && (
          <span
            className="pointer-events-none absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-[var(--chart-1)]"
            style={{
              left: pct(shape.x(a.t), w),
              top: pct(shape.y(a.mid ?? a.past!), h),
            }}
            aria-hidden
          />
        )}
      </div>
    </div>
  );
}
