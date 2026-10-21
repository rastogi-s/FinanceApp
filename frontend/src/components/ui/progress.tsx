import { cn } from "@/lib/utils";

/**
 * A bar that fills toward a limit. "budget" turns amber near the limit and red at it; "bill" only turns red past
 * it (a bill paid in full is fine); "goal" turns green once reached.
 */
export function Progress({
  value,
  max,
  label,
  tone: kind = "budget",
}: {
  value: number;
  max: number;
  label: string;
  tone?: "budget" | "bill" | "goal";
}) {
  const ratio = max > 0 ? value / max : 0;
  const tone =
    kind === "goal"
      ? ratio >= 1
        ? "bg-positive"
        : "bg-primary"
      : kind === "bill"
        ? ratio > 1
          ? "bg-negative"
          : "bg-primary"
        : ratio >= 1
          ? "bg-negative"
          : ratio >= 0.85
            ? "bg-warning"
            : "bg-primary";
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
