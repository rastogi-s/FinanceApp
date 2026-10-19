import { cn } from "@/lib/utils";

/** A budget-style bar: fills toward the limit and turns amber near it, red past it. */
export function Progress({ value, max, label }: { value: number; max: number; label: string }) {
  const ratio = max > 0 ? value / max : 0;
  const tone = ratio >= 1 ? "bg-negative" : ratio >= 0.85 ? "bg-warning" : "bg-primary";
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(ratio * 100)}
      className="h-2 w-full overflow-hidden rounded-full bg-muted"
    >
      <div className={cn("h-full rounded-full transition-[width] duration-500", tone)} style={{ width: `${Math.min(ratio, 1) * 100}%` }} />
    </div>
  );
}
