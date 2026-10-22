"use client";

import { useState } from "react";
import Link from "next/link";
import { ChevronRight, Info, TriangleAlert } from "lucide-react";
import { Money } from "@/components/money";
import { Sheet } from "@/components/ui/sheet";
import { type SavingsBreakdown, type SavingsLine, type SavingsMonth, monthName } from "@/lib/budget";
import { cn } from "@/lib/utils";

const shortMonth = new Intl.DateTimeFormat("en-US", {
  month: "short",
  timeZone: "UTC",
});

/**
 * How a month's savings rate adds up: money in, money out (spending by group, with its categories), what's left,
 * and where that went. The same sums as Plan's "See each month", so the two pages agree.
 */
export function SavingsSheet({
  months,
  open,
  onOpenChange,
}: {
  months: SavingsMonth[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const m = months.find((x) => x.month === picked) ?? months[months.length - 1];
  if (!m) return null;
  const name = monthName.format(new Date(m.month));
  const b = m.breakdown;
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Savings rate"
      description={`How ${name} adds up, from your own transactions${m.basis === "gross" ? " and pay stubs" : ""}.${
        m.estimated_paychecks
          ? ` ${m.estimated_paychecks} paycheck${m.estimated_paychecks > 1 ? "s" : ""} without a stub filled in from your usual stub.`
          : ""
      }`}
    >
      <div className="grid grid-cols-1 gap-5">
        {months.length > 1 && (
          <div role="group" aria-label="Month" className="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-1">
            {months.map((x) => (
              <button
                key={x.month}
                type="button"
                aria-pressed={x.month === m.month}
                onClick={() => setPicked(x.month)}
                className={cn(
                  "flex shrink-0 flex-col items-center rounded-2xl border px-3 py-1.5 text-xs transition-colors",
                  x.month === m.month ? "border-primary bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-muted/60",
                )}
              >
                <span className="font-medium">{shortMonth.format(new Date(x.month))}</span>
                <span className={cn("tabular-nums", x.percent < 0 && "text-negative")}>{x.percent.toFixed(0)}%</span>
              </button>
            ))}
          </div>
        )}

        <div>
          <p className={cn("text-4xl font-semibold tracking-tight", m.percent < 0 && "text-negative")}>{m.percent.toFixed(0)}%</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {m.saved_cents >= 0 ? "Saved " : "Spent "}
            <Money cents={Math.abs(m.saved_cents)} className="font-medium text-foreground" />
            {m.saved_cents >= 0 ? " of " : " more than "}
            <Money cents={m.income_cents} /> {m.basis === "gross" ? "pay before taxes and other money in" : "that came in"}.
          </p>
          {m.usual && <UsualMonth m={m} />}
          <Split m={m} />
        </div>

        {b ? (
          <>
            <Section title="Money in" total={m.income_cents}>
              {b.money_in.map((l) => (
                <Row key={l.label} line={l} />
              ))}
            </Section>
            <Section title="Money out" total={m.income_cents - m.saved_cents}>
              {b.money_out.map((l) => (
                <Row key={l.label} line={l} />
              ))}
            </Section>
            <div className="flex items-baseline justify-between rounded-xl bg-muted px-4 py-3 text-sm font-semibold">
              <span>{m.saved_cents >= 0 ? "Saved" : "Overspent"}</span>
              <span className={m.saved_cents < 0 ? "text-negative" : "text-positive"}>
                <Money cents={m.saved_cents} /> · {m.percent.toFixed(1)}%
              </span>
            </div>
            {b.went.length > 0 && (
              <Section title="Where it went">
                {b.went.map((l) => (
                  <Row key={l.label} line={l} muted={l.kind === "left"} signed={l.cents < 0} />
                ))}
              </Section>
            )}
            {b.notes.length > 0 && <Notes notes={b.notes} />}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">Pull to refresh to see this month line by line.</p>
        )}

        <HowItUpdates />

        <p className="text-xs text-muted-foreground">
          Savings rate is what&apos;s left after taxes
          {m.basis === "gross" ? ", paycheck deductions" : ""} and spending, as a share of the money that came in. Money moved between your
          own accounts and card payments aren&apos;t counted as money in or as spending: what a card bought is counted when you buy it.
          Plan&apos;s <span className="font-medium">See each month</span> uses the same numbers.
        </p>
      </div>
    </Sheet>
  );
}

/** What the month looks like without its bonus, or the third paycheck a two-weekly schedule brings twice a year. */
function UsualMonth({ m }: { m: SavingsMonth }) {
  const u = m.usual!;
  const why = [
    u.bonus_cents > 0 && "a bonus",
    u.extra_paychecks > 0 && (u.extra_paychecks === 1 ? "a third paycheck" : `${u.extra_paychecks} extra paychecks`),
  ]
    .filter(Boolean)
    .join(" and ");
  return (
    <p className="mt-2 rounded-xl bg-muted px-3 py-2 text-sm">
      <span className="font-medium">{u.percent.toFixed(0)}% in a usual month.</span>{" "}
      <span className="text-muted-foreground">
        {name(why)} made this month look better than usual: without it you&apos;d have saved <Money cents={u.saved_cents} />.
        {u.bonus_cents > 0 && " Plan leaves bonuses out of what you usually save."}
      </span>
    </p>
  );
}

function name(why: string) {
  return why.charAt(0).toUpperCase() + why.slice(1);
}

/** How the savings rate keeps itself up to date, in plain words. */
function HowItUpdates() {
  return (
    <details className="rounded-xl border px-4 py-3 text-sm">
      <summary className="cursor-pointer font-medium select-none">How this updates</summary>
      <ul className="mt-2 grid list-disc gap-1.5 pl-5 text-muted-foreground">
        <li>It&apos;s worked out again every time you open it, from the transactions and pay stubs the app has.</li>
        <li>
          New transactions come in when your accounts sync (every few hours, and when you tap Sync). Each month shows the transactions dated
          in it; a month is complete once it&apos;s over and its last transactions have posted.
        </li>
        <li>
          Pay is only your payroll deposits. Adding a month&apos;s pay stubs swaps the estimate for the stub&apos;s real pay before taxes,
          withholding and 401(k); a paycheck without its stub is filled in from your usual stub.
        </li>
        <li>
          A bonus counts in the month it landed, but is kept out of the usual month, and so is a third paycheck in a month that usually has
          two.
        </li>
        <li>Recategorizing a transaction (a Zelle that paid for something, say) changes the months it&apos;s in straight away.</li>
      </ul>
    </details>
  );
}

/** One bar for the month: taxes and deductions, spending, and what was saved, as shares of money in. */
function Split({ m }: { m: SavingsMonth }) {
  if (m.income_cents <= 0) return null;
  const parts = [
    {
      label: "Taxes and deductions",
      cents: m.taxes_cents + m.deductions_cents,
      cls: "bg-chart-5",
    },
    { label: "Spending", cents: m.spending_cents, cls: "bg-chart-3" },
    { label: "Saved", cents: Math.max(m.saved_cents, 0), cls: "bg-chart-1" },
  ].filter((p) => p.cents > 0);
  const total = parts.reduce((s, p) => s + p.cents, 0);
  const pct = (c: number) => Math.round((c / m.income_cents) * 100);
  return (
    <div className="mt-3">
      <div
        role="img"
        aria-label={parts.map((p) => `${p.label} ${pct(p.cents)}%`).join(", ")}
        className="flex h-2.5 overflow-hidden rounded-full bg-muted"
      >
        {parts.map((p) => (
          <div key={p.label} className={p.cls} style={{ width: `${(p.cents / total) * 100}%` }} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-hidden>
        {parts.map((p) => (
          <li key={p.label} className="flex items-center gap-1.5">
            <span className={cn("size-2 rounded-full", p.cls)} />
            {p.label} {pct(p.cents)}%
          </li>
        ))}
      </ul>
    </div>
  );
}

function Section({ title, total, children }: { title: string; total?: number; children: React.ReactNode }) {
  return (
    <section>
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
        {total !== undefined && (
          <span className="text-xs text-muted-foreground">
            <Money cents={total} />
          </span>
        )}
      </div>
      <ul className="divide-y">{children}</ul>
    </section>
  );
}

/** A line of the sum; one with categories or payees under it opens to show them. */
function Row({ line, muted, signed }: { line: SavingsLine; muted?: boolean; signed?: boolean }) {
  const [open, setOpen] = useState(false);
  const amount = (
    <Money cents={line.cents} signed={signed} className={cn("tabular-nums", muted ? "text-muted-foreground" : "font-medium")} />
  );
  if (!line.items?.length) {
    return (
      <li className="flex items-baseline justify-between gap-3 py-2.5 text-sm">
        <span className={cn("min-w-0", muted && "text-muted-foreground")}>{line.label}</span>
        {amount}
      </li>
    );
  }
  return (
    <li className="py-1 text-sm">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-lg py-1.5 text-left"
      >
        <ChevronRight className={cn("size-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")} aria-hidden />
        <span className="min-w-0 flex-1">{line.label}</span>
        {amount}
      </button>
      {open && (
        <ul className="mb-1 ml-6 grid grid-cols-1 gap-1 border-l pl-3">
          {line.items.map((i) => (
            <li key={i.label} className="flex items-baseline justify-between gap-3 text-xs text-muted-foreground">
              <span className="min-w-0 truncate">{i.label}</span>
              <Money cents={i.cents} />
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Notes({ notes }: { notes: SavingsBreakdown["notes"] }) {
  return (
    <section className="grid grid-cols-1 gap-2">
      <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Worth a look</h3>
      {notes.map((n) => {
        const warn = n.kind !== "take_home";
        const Icon = warn ? TriangleAlert : Info;
        return (
          <div
            key={n.kind}
            className={cn("flex gap-3 rounded-xl px-4 py-3 text-sm", warn ? "bg-warning/10" : "bg-muted text-muted-foreground")}
          >
            <Icon className={cn("mt-0.5 size-4 shrink-0", warn ? "text-warning" : "text-muted-foreground")} aria-hidden />
            <p>
              {n.kind === "take_home" && (
                <>
                  This uses take-home pay, so 401(k) and HSA money taken out before payday isn&apos;t in it. Add a pay stub below for the
                  true rate.
                </>
              )}
              {n.kind === "unlinked_cards" && (
                <>
                  <Money cents={n.cents ?? 0} className="font-medium" /> paid credit cards that aren&apos;t linked. What was bought on them
                  isn&apos;t counted as spending, so this rate is likely too high.{" "}
                  <Link href="/settings/accounts" className="font-medium text-primary underline-offset-4 hover:underline">
                    Link the card
                  </Link>{" "}
                  to count it.
                </>
              )}
              {n.kind === "other_transfers" && (
                <>
                  <Money cents={n.cents ?? 0} className="font-medium" /> went out as transfers to accounts that aren&apos;t linked, like
                  Zelle or Venmo. If some of it paid for something (rent, a sitter), give it a category in{" "}
                  <Link href="/spending" className="font-medium text-primary underline-offset-4 hover:underline">
                    Spending
                  </Link>{" "}
                  and it counts as spending.
                </>
              )}
              {n.kind === "other_income" && (
                <>
                  <Money cents={n.cents ?? 0} className="font-medium" /> came in that isn&apos;t pay or interest. If some of it was money
                  moved from one of your own accounts, mark it as Transfer in{" "}
                  <Link href="/spending" className="font-medium text-primary underline-offset-4 hover:underline">
                    Spending
                  </Link>{" "}
                  so it isn&apos;t counted as income.
                </>
              )}
            </p>
          </div>
        );
      })}
    </section>
  );
}
