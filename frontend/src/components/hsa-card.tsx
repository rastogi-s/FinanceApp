"use client";

import Link from "next/link";
import { AlertTriangle, Check, ChevronRight, HeartPulse } from "lucide-react";
import { Money } from "@/components/money";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** The yearly HSA limit, shared by your own payroll HSA money, your employer's, and what you put in yourself. */
export type HsaCheck = {
  coverage: "self" | "family" | "none" | null;
  has_hsa: boolean;
  limit_cents: number;
  self_cents: number;
  family_cents: number;
  catch_up: boolean;
  own_cents: number;
  employer_cents: number;
  outside_cents: number;
  total_cents: number;
  room_cents: number;
  over_cents: number;
  paychecks_left: number;
  per_paycheck_cents: number;
  year: number;
  taxed_cents?: number;
};

const COVERAGE = [
  ["self", "Just me"],
  ["family", "Family"],
  ["none", "No HSA"],
] as const;

/** On Taxes, with the coverage question (onCoverage); on Plan, read-only with a link to Taxes. */
export function HsaCard({ h, onCoverage, busy }: { h: HsaCheck; onCoverage?: (c: "self" | "family" | "none") => void; busy?: boolean }) {
  if (h.coverage === "none" || (!h.has_hsa && !onCoverage)) return null;
  const share = (c: number) => `${(c / Math.max(h.limit_cents, h.total_cents, 1)) * 100}%`;
  const body = (
    <CardContent className="grid grid-cols-1 gap-3">
      <p className="text-sm">
        {h.over_cents > 0 ? (
          <span className="flex items-start gap-2 font-medium text-negative">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>
              On track for <Money cents={h.over_cents} /> over the {h.year} limit of <Money cents={h.limit_cents} />. Lower your HSA
              amount in payroll{h.outside_cents ? " or put in less yourself" : ""}; money over the limit is taxed.
            </span>
          </span>
        ) : h.room_cents > 0 ? (
          <>
            <span className="font-medium">
              <Money cents={h.room_cents} /> of room left
            </span>{" "}
            <span className="text-muted-foreground">
              of the <Money cents={h.limit_cents} /> limit
              {h.per_paycheck_cents > 0 && (
                <>
                  : about <Money cents={h.per_paycheck_cents} /> more a paycheck for the {h.paychecks_left} left would fill it
                </>
              )}
              . HSA money goes in before every tax.
            </span>
          </>
        ) : (
          <span className="flex items-center gap-2 font-medium text-positive">
            <Check className="size-4" aria-hidden /> Right at the <Money cents={h.limit_cents} /> limit.
          </span>
        )}
      </p>
      <div className="flex h-2.5 gap-[2px] overflow-hidden rounded-full bg-muted" aria-hidden>
        <div className="h-full bg-chart-1" style={{ width: share(h.own_cents) }} />
        <div className="h-full bg-chart-4" style={{ width: share(h.employer_cents) }} />
        <div className="h-full bg-chart-3" style={{ width: share(h.outside_cents) }} />
      </div>
      <ul className="grid gap-1 text-xs text-muted-foreground">
        <li className="flex items-center justify-between gap-3">
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-chart-1" /> Yours, from your pay
          </span>
          <Money cents={h.own_cents} className="font-medium text-foreground" />
        </li>
        <li className="flex items-center justify-between gap-3">
          <span className="inline-flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm bg-chart-4" /> Your employer&apos;s
          </span>
          <Money cents={h.employer_cents} className="font-medium text-foreground" />
        </li>
        {h.outside_cents > 0 && (
          <li className="flex items-center justify-between gap-3">
            <span className="inline-flex items-center gap-1.5">
              <span className="size-2.5 rounded-sm bg-chart-3" /> Put in yourself
            </span>
            <Money cents={h.outside_cents} className="font-medium text-foreground" />
          </li>
        )}
      </ul>
      <p className="text-xs text-muted-foreground">
        For the whole year, from your pay stubs. Both share one limit
        {h.coverage === null ? " (the self-only one until you say)" : h.coverage === "family" ? " (family coverage)" : " (self-only coverage)"}
        {h.catch_up ? ", plus $1,000 from age 55" : ""}.
      </p>
      {onCoverage && (
        <fieldset>
          <legend className="mb-2 text-sm font-medium">Your health plan covers</legend>
          <div className="flex flex-wrap gap-2">
            {COVERAGE.map(([value, label]) => (
              <button
                key={value}
                type="button"
                disabled={busy}
                aria-pressed={h.coverage === value}
                onClick={() => onCoverage(value)}
                className={cn(
                  "inline-flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm font-medium transition-colors",
                  h.coverage === value ? "border-transparent bg-primary text-primary-foreground" : "bg-card hover:bg-muted",
                )}
              >
                {h.coverage === value && <Check className="size-4" aria-hidden />}
                {label}
              </button>
            ))}
          </div>
        </fieldset>
      )}
    </CardContent>
  );
  return onCoverage ? (
    <Card>
      <CardHeader>
        <CardTitle>HSA this year</CardTitle>
        <HeartPulse className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      {body}
    </Card>
  ) : (
    <Link href="/taxes" className="block">
      <Card className="transition-colors hover:bg-muted/40">
        <CardHeader>
          <CardTitle>HSA this year</CardTitle>
          <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
        </CardHeader>
        {body}
      </Card>
    </Link>
  );
}
