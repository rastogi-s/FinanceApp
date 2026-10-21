import { cn } from "@/lib/utils";

/**
 * A bar that fills toward a limit. "budget" turns amber near the limit and red at it; "bill" only turns red past
 * it (a bill paid in full is fine); "goal" turns green once reached. `projected` adds a faint fill out to where the
 * month is heading, amber once that's past the limit.
 */
export function Progress({
  value,
  max,
  label,
  tone: kind = "budget",
  projected,
}: {
  value: number;
  max: number;
  label: string;
  tone?: "budget" | "bill" | "goal";
  projected?: number;
}) {
  const ratio = max > 0 ? value / max : 0;
  const ahead = projected !== undefined && max > 0 ? projected / max : 0;
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
      className="relative h-2 w-full overflow-hidden rounded-full bg-muted"
    >
      {ahead > ratio && ratio < 1 && (
        <div
          aria-hidden
          className={cn("absolute inset-y-0 left-0 rounded-full opacity-35 transition-[width] duration-500", ahead > 1 ? "bg-warning" : tone)}
          style={{ width: `${Math.min(ahead, 1) * 100}%` }}
        />
      )}
      <div className={cn("relative h-full rounded-full transition-[width] duration-500", tone)} style={{ width: `${Math.min(ratio, 1) * 100}%` }} />
    </div>
  );
}
