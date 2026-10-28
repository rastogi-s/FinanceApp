"use client";

import { useId, useMemo, useRef, useState } from "react";
import { price as fmtPrice } from "@/lib/portfolio";
import { cn } from "@/lib/utils";

const dayTime = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit" });
const dayDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });
const longDate = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });

export function pointLabel(t: number, intraday: boolean) {
  const d = new Date(t * 1000);
  return intraday ? `${dayDate.format(d)}, ${dayTime.format(d)}` : longDate.format(d);
}

/**
 * A price line you can scrub: drag a finger (or the mouse) across it, or focus it and use the arrow keys, and
 * `onScrub` reports the point under it. Green when the range ended higher than it started, red when lower. On the
 * one-day chart a dashed line marks yesterday's close.
 */
export function PriceChart({
  points,
  baseline,
  intraday,
  onScrub,
  label,
  format = (v) => fmtPrice.format(v),
  className,
}: {
  points: [number, number][];
  baseline?: number | null;
  intraday: boolean;
  onScrub: (p: [number, number] | null) => void;
  label: string;
  /** How a point's value is read out to a screen reader (a share price by default). */
  format?: (v: number) => string;
  className?: string;
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<number | null>(null);
  const w = 640;
  const h = 220;
  const pad = { t: 12, b: 12 };
  const shape = useMemo(() => {
    if (points.length < 2) return null;
    const values = points.map((p) => p[1]);
    if (baseline) values.push(baseline);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const x = (i: number) => (i / (points.length - 1)) * w;
    const y = (v: number) => pad.t + (1 - (v - min) / span) * (h - pad.t - pad.b);
    const line = points.map(([, v], i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const start = baseline ?? points[0]![1];
    const up = points[points.length - 1]![1] >= start;
    return { x, y, line, up, base: baseline ? y(baseline) : null };
  }, [points, baseline, pad.t, pad.b]);

  if (!shape) {
    return (
      <div className="grid h-48 place-items-center rounded-xl bg-muted/50 text-sm text-muted-foreground">
        No price history to show.
      </div>
    );
  }
  const color = shape.up ? "var(--positive)" : "var(--negative)";

  const pick = (clientX: number) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const i = Math.round(((clientX - box.left) / box.width) * (points.length - 1));
    const clamped = Math.max(0, Math.min(points.length - 1, i));
    setActive(clamped);
    onScrub(points[clamped]!);
  };
  const clear = () => {
    setActive(null);
    onScrub(null);
  };
  const key = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 10 : 1;
    let i = active ?? points.length - 1;
    if (e.key === "ArrowLeft") i = Math.max(0, i - step);
    else if (e.key === "ArrowRight") i = Math.min(points.length - 1, i + step);
    else if (e.key === "Home") i = 0;
    else if (e.key === "End") i = points.length - 1;
    else if (e.key === "Escape") return clear();
    else return;
    e.preventDefault();
    setActive(i);
    onScrub(points[i]!);
  };
  const a = active !== null ? points[active] : null;

  return (
    <div
      ref={ref}
      role="slider"
      tabIndex={0}
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={points.length - 1}
      aria-valuenow={active ?? points.length - 1}
      aria-valuetext={
        a ? `${format(a[1])} on ${pointLabel(a[0], intraday)}` : `${format(points[points.length - 1]![1])}, latest`
      }
      className={cn(
        "money relative h-52 w-full touch-pan-y rounded-lg outline-none select-none focus-visible:ring-2 focus-visible:ring-ring md:h-60",
        className,
      )}
      onPointerMove={(e) => pick(e.clientX)}
      onPointerDown={(e) => pick(e.clientX)}
      onPointerLeave={clear}
      onKeyDown={key}
      onBlur={clear}
    >
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="absolute inset-0 size-full" aria-hidden>
        <defs>
          <linearGradient id={`${id}-fill`} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.22" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        {shape.base !== null && (
          <line x1={0} x2={w} y1={shape.base} y2={shape.base} stroke="var(--muted-foreground)" strokeDasharray="3 5" strokeWidth="1" vectorEffect="non-scaling-stroke" opacity="0.6" />
        )}
        <path d={`${shape.line} L${w},${h} L0,${h} Z`} fill={`url(#${id}-fill)`} />
        <path d={shape.line} fill="none" stroke={color} strokeWidth="2.25" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        {a && active !== null && (
          <line x1={shape.x(active)} x2={shape.x(active)} y1={0} y2={h} stroke="var(--foreground)" strokeOpacity="0.35" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        )}
      </svg>
      {a && active !== null && (
        <span
          className="pointer-events-none absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card"
          style={{ left: `${(shape.x(active) / w) * 100}%`, top: `${(shape.y(a[1]) / h) * 100}%`, background: color }}
          aria-hidden
        />
      )}
    </div>
  );
}
