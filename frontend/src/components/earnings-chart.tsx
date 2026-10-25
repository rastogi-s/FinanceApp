"use client";

import { formatMoney } from "@/components/money";
import { niceStep, usePicker } from "@/components/income-charts";
import { SERIES, type EarningsYear } from "@/lib/earnings";
import { cn } from "@/lib/utils";

/**
 * Each year's pay as a stacked bar, oldest on the left: base, bonus, stock, other, and what the employer put in on
 * top. This year's bar is what came so far, with what's still to come lighter above it. Drag across, or focus and
 * use the arrow keys, to read a year.
 */
export function YearBars({ years }: { years: EarningsYear[] }) {
  const rows = [...years].reverse();
  const { active, handlers } = usePicker(rows.length);
  const top = Math.max(...rows.map((r) => r.full_comp_cents), 1);
  const step = niceStep(top);
  const max = Math.ceil((top * 1.05) / step) * step;
  const grid: number[] = [];
  for (let v = step; v < max; v += step) grid.push(v);
  const sum = (r: EarningsYear, keys: string[], full = false) =>
    r.sources.filter((s) => keys.includes(s.key)).reduce((t, s) => t + (full ? s.full_cents : s.cents), 0);
  const shown = SERIES.filter((s) => rows.some((r) => sum(r, s.keys, true) > 0));
  const a = active !== null ? rows[active]! : null;
  const readout = a
    ? `${a.year}${a.projected ? " on track" : ""}: ${formatMoney(a.full_comp_cents)} — ` +
      shown
        .map((s) => [s.label.toLowerCase(), sum(a, s.keys, true)] as const)
        .filter(([, v]) => v > 0)
        .map(([l, v]) => `${l} ${formatMoney(v, "compact")}`)
        .join(", ")
    : null;

  return (
    <div>
      <p className="money mb-2 min-h-5 text-sm font-medium tabular-nums" aria-live="polite">
        {readout ?? <span className="text-muted-foreground">Drag across the chart to read a year</span>}
      </p>
      <div
        {...handlers}
        role="slider"
        tabIndex={0}
        aria-label="Pay and what your employer put in, by year"
        aria-valuemin={0}
        aria-valuemax={rows.length - 1}
        aria-valuenow={active ?? rows.length - 1}
        aria-valuetext={readout ?? "Latest year"}
        className="money relative h-48 w-full touch-pan-y rounded-lg outline-none select-none focus-visible:ring-2 focus-visible:ring-ring md:h-56"
      >
        <div className="absolute inset-x-0 top-0 bottom-6">
          {grid.map((v) => (
            <div key={v} className="absolute inset-x-0 border-t border-border/70" style={{ bottom: `${(v / max) * 100}%` }}>
              <span className="absolute left-0 z-10 -translate-y-1/2 rounded bg-card/85 px-1 text-[11px] text-muted-foreground">
                {formatMoney(v, "compact")}
              </span>
            </div>
          ))}
          <div className="absolute inset-x-0 bottom-0 border-t border-border" />
          <div className="absolute inset-0 flex items-end justify-center gap-2 pl-10 md:gap-4">
            {rows.map((r, i) => {
              const later = r.full_comp_cents - r.comp_cents;
              return (
                <div
                  key={r.year}
                  className={cn(
                    "flex h-full max-w-16 flex-1 flex-col-reverse gap-[2px] transition-opacity",
                    active !== null && active !== i && "opacity-45",
                  )}
                >
                  {shown.map((s) => {
                    const v = sum(r, s.keys);
                    return v > 0 ? (
                      <div
                        key={s.label}
                        className="w-full first:rounded-b-[2px] last:rounded-t-[4px]"
                        style={{ height: `${(v / max) * 100}%`, background: s.color }}
                      />
                    ) : null;
                  })}
                  {later > 0 && (
                    <div
                      className="w-full rounded-t-[4px] border border-dashed border-chart-1/60 bg-chart-1/15"
                      style={{ height: `${(later / max) * 100}%` }}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
        <div className="absolute inset-x-0 bottom-0 flex h-5 justify-center gap-2 pl-10 md:gap-4">
          {rows.map((r) => (
            <span key={r.year} className="max-w-16 flex-1 text-center text-[11px] text-muted-foreground">
              {r.year}
            </span>
          ))}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {shown.map((s) => (
          <span key={s.label} className="inline-flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ background: s.color }} aria-hidden /> {s.label}
          </span>
        ))}
        {rows.some((r) => r.full_comp_cents > r.comp_cents) && (
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm border border-dashed border-chart-1/60 bg-chart-1/15" aria-hidden /> Still to come
          </span>
        )}
      </div>
    </div>
  );
}
