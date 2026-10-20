import { cn } from "@/lib/utils";

const whole = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const exact = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const compact = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

export function formatMoney(cents: number, style: "whole" | "exact" | "compact" = "whole") {
  const f = style === "exact" ? exact : style === "compact" ? compact : whole;
  return f.format(cents / 100);
}

/** An amount of money. Always use this, so the privacy button can blur it. */
export function Money({
  cents,
  style = "whole",
  signed = false,
  className,
}: {
  cents: number;
  style?: "whole" | "exact" | "compact";
  signed?: boolean;
  className?: string;
}) {
  const text = formatMoney(Math.abs(cents), style);
  const sign = cents < 0 ? "−" : signed && cents > 0 ? "+" : "";
  return (
    <span className={cn("money", className)}>
      {sign}
      {text}
    </span>
  );
}
