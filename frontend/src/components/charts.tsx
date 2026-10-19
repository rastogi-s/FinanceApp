import { formatMoney } from "@/components/money";

/** Net worth over time as a soft area line. Decorative detail; the label carries the numbers. */
export function Sparkline({ points, label }: { points: number[]; label: string }) {
  if (points.length < 2) return null;
  const w = 320;
  const h = 72;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const xy = points.map((p, i) => [(i / (points.length - 1)) * w, h - 4 - ((p - min) / span) * (h - 8)] as const);
  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img" aria-label={label} className="money h-16 w-full">
      <defs>
        <linearGradient id="spark-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="var(--chart-1)" stopOpacity="0.25" />
          <stop offset="100%" stopColor="var(--chart-1)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${w},${h} L0,${h} Z`} fill="url(#spark-fill)" />
      <path d={line} fill="none" stroke="var(--chart-1)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** The three-band projection: a shaded range from the low to the high return, with the middle as a line. */
export function ProjectionChart({ bands }: { bands: { year: number; low: number; mid: number; high: number }[] }) {
  const w = 640;
  const h = 240;
  const pad = { l: 8, r: 8, t: 12, b: 24 };
  const max = Math.max(...bands.map((b) => b.high));
  const x = (i: number) => pad.l + (i / (bands.length - 1)) * (w - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / max) * (h - pad.t - pad.b);
  const path = (key: "low" | "mid" | "high") => bands.map((b, i) => `${i ? "L" : "M"}${x(i)},${y(b[key])}`).join(" ");
  const area = `${path("high")} ${[...bands].reverse().map((b, i) => `L${x(bands.length - 1 - i)},${y(b.low)}`).join(" ")} Z`;
  const last = bands[bands.length - 1]!;
  const ticks = bands.filter((_, i) => i % 5 === 0);
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      role="img"
      aria-label={`Projected net worth in ${last.year}: between ${formatMoney(last.low, "compact")} and ${formatMoney(last.high, "compact")}, most likely about ${formatMoney(last.mid, "compact")}.`}
      className="money h-auto w-full"
    >
      <path d={area} fill="var(--chart-1)" opacity="0.14" />
      <path d={path("mid")} fill="none" stroke="var(--chart-1)" strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
      {ticks.map((b) => {
        const i = bands.indexOf(b);
        return (
          <text key={b.year} x={x(i)} y={h - 6} textAnchor={i === 0 ? "start" : "middle"} className="fill-muted-foreground text-[11px]">
            {b.year}
          </text>
        );
      })}
    </svg>
  );
}
