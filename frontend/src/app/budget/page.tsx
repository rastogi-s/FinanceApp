"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  CalendarClock,
  CalendarRange,
  ChevronRight,
  FileText,
  History,
  Minus,
  Pencil,
  PiggyBank,
  Plus,
  ShieldCheck,
  Sparkles,
  Target,
  Trash2,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { AIBudgetSheet } from "@/components/ai-budget-sheet";
import { AddSheet, FREQUENCY_LABEL, LineSheet, PaycheckSheet } from "@/components/budget-sheets";
import { Money, formatMoney } from "@/components/money";
import { SavingsSheet } from "@/components/savings-sheet";
import { HouseholdPage, Notice } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/household";
import type { ReviewState } from "@/lib/review";
import { type BudgetLine, type BudgetState, type Forecast, type SavingsMonth, monthName, useBudget } from "@/lib/budget";
import { monthOf } from "@/lib/search";
import { cn } from "@/lib/utils";

const short = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const stamp = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

export default function BudgetPage() {
  return (
    <HouseholdPage title="Budget" subtitle="Every category's monthly limit, drafted from your own spending. Tap one to see what went into it this month.">
      {() => <Budget />}
    </HouseholdPage>
  );
}

function Budget() {
  const { data, error, change } = useBudget();
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const [editing, setEditing] = useState<BudgetLine | null>(null);
  const [adding, setAdding] = useState(false);
  const [paying, setPaying] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [sort, setSort] = useState<LineSort>(savedSort);
  // Home's savings rate tile opens the breakdown straight away (/budget?savings=1).
  const [saving, setSaving] = useState(
    () => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("savings") === "1",
  );
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("savings")) {
      window.history.replaceState(window.history.state, "", window.location.pathname);
    }
  }, []);

  if (error)
    return (
      <Notice icon={<PiggyBank className="size-5" />} title="Couldn't load the budget">
        {error}
      </Notice>
    );
  if (!data) {
    return (
      <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
        <div className="grid grid-cols-2 gap-4">
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-28 rounded-xl" />
        </div>
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }

  const run = async (path: string, method: string, body: unknown, done: string) => {
    if (demo) return toast("Changing the budget is off while the app shows sample data.");
    try {
      await change(path, method, body);
      toast(done);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const pick = (categoryId: number) => {
    const existing = data.lines.find((l) => l.category_id === categoryId);
    const c = data.categories.find((x) => x.id === categoryId);
    if (existing) setEditing(existing);
    else if (c) {
      const spent = data.unbudgeted.find((u) => u.category_id === categoryId)?.spent_cents ?? 0;
      setEditing({
        category_id: c.id,
        category: c.name,
        kind: c.kind,
        essential: c.essential,
        limit_cents: 0,
        spent_cents: spent,
        typical_cents: 0,
      });
    }
    setAdding(false);
  };

  return (
    <div className="grid grid-cols-1 gap-4">
      {!data.saved && data.lines.length > 0 && (
        <Card className="border-primary/30 bg-accent/40">
          <CardContent className="flex gap-4 pt-5">
            <div className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground">
              <Sparkles className="size-5" aria-hidden />
            </div>
            <div className="min-w-0">
              <p className="font-medium">Here&apos;s a first draft</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Each limit is what that category usually costs over the last {data.months_used} month{data.months_used === 1 ? "" : "s"},
                with a little room on everyday spending.{data.rough && " That's not much history yet, so treat it as rough."} Tap any line
                to change it, or keep it as it is.
              </p>
              <Button size="sm" className="mt-3" onClick={() => run("/api/budget/draft", "POST", undefined, "Budget saved.")}>
                Looks right, save it
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {data.lines.length === 0 && (
        <Notice
          icon={<PiggyBank className="size-5" />}
          title={data.months_used ? "Nothing to budget yet" : "Your budget needs a full month of spending"}
        >
          {data.months_used
            ? "No bills or everyday spending in the last few months. Add a category to set a limit by hand."
            : "Once a whole month of transactions is in, the app drafts a budget from it. You can add categories by hand meanwhile."}
        </Notice>
      )}

      <div className="grid grid-cols-2 gap-4">
        <Card className="h-full">
          <CardHeader>
            <CardTitle>Safe to spend</CardTitle>
            <Wallet className="size-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent>
            {data.safe_to_spend ? (
              <>
                <p className="text-2xl font-semibold tracking-tight">
                  <Money cents={data.safe_to_spend.cents} />
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  left for {data.safe_to_spend.days_left} day{data.safe_to_spend.days_left === 1 ? "" : "s"} of everyday spending
                </p>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">Set an everyday budget to see this.</p>
            )}
          </CardContent>
        </Card>
        {data.savings_rate ? (
          <button
            type="button"
            onClick={() => setSaving(true)}
            aria-haspopup="dialog"
            className="rounded-xl text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <Card className="h-full transition-colors hover:bg-muted/40">
              <CardHeader>
                <CardTitle>Savings rate</CardTitle>
                <PiggyBank className="size-4 text-muted-foreground" aria-hidden />
              </CardHeader>
              <CardContent>
                <p className={cn("text-2xl font-semibold tracking-tight", data.savings_rate.percent < 0 && "text-negative")}>
                  {data.savings_rate.percent.toFixed(0)}%
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  of {data.savings_rate.basis === "gross" ? "gross" : "take-home"} pay in{" "}
                  {monthName.format(new Date(data.savings_rate.month))}
                </p>
                <p className="mt-2 flex items-center gap-0.5 text-xs font-medium text-primary">
                  See how it adds up <ChevronRight className="size-3.5" aria-hidden />
                </p>
              </CardContent>
            </Card>
          </button>
        ) : (
          <Card>
            <CardHeader>
              <CardTitle>Savings rate</CardTitle>
              <PiggyBank className="size-4 text-muted-foreground" aria-hidden />
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">Shows up after a month with income in it.</p>
            </CardContent>
          </Card>
        )}
      </div>

      <CategoryCheck />

      {data.forecast && <MonthEnd data={data} />}

      {data.lines.length > 3 && (
        <div className="-mb-1 flex items-center justify-between gap-3 px-1">
          <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Categories</h2>
          <Select
            aria-label="Sort categories"
            value={sort}
            onChange={(e) => {
              const v = e.target.value as LineSort;
              setSort(v);
              try {
                localStorage.setItem(SORT_KEY, v);
              } catch {
                // storage blocked: the choice lasts until the page closes
              }
            }}
            className="h-9 rounded-full pr-9 text-sm md:h-9"
          >
            {LINE_SORTS.map((x) => (
              <option key={x.id} value={x.id}>
                {x.label}
              </option>
            ))}
          </Select>
        </div>
      )}

      {(["flexible", "fixed"] as const).map((kind) => {
        const lines = sortLines(
          data.lines.filter((l) => l.kind === kind),
          sort,
        );
        return lines.length ? (
          <Group key={kind} title={kind === "fixed" ? "Bills" : "Everyday"} lines={lines} month={data.month} onPick={setEditing} />
        ) : null;
      })}

      {data.unbudgeted.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Outside the budget</CardTitle>
            <span className="text-sm text-muted-foreground">
              <Money cents={data.unbudgeted.reduce((s, u) => s + u.spent_cents, 0)} />
            </span>
          </CardHeader>
          <CardContent className="pt-0">
            <p className="mb-2 text-sm text-muted-foreground">
              Spent this month in categories without a limit. It comes out of safe to spend.
            </p>
            <ul className="divide-y">
              {data.unbudgeted.map((u) => (
                <li key={u.category} className="flex items-center gap-3 py-1 text-sm">
                  <Link
                    href={monthOf(u.category_id ?? "none")}
                    className="-ml-2 flex min-w-0 flex-1 items-center gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-muted/50"
                    aria-label={`${u.category}: see this month's transactions`}
                  >
                    <span className="min-w-0 flex-1 truncate font-medium">{u.category}</span>
                    <Money cents={u.spent_cents} />
                  </Link>
                  {u.category_id && data.categories.some((c) => c.id === u.category_id) ? (
                    <button type="button" onClick={() => pick(u.category_id!)} className="py-2 text-xs font-medium text-primary">
                      Set a limit
                    </button>
                  ) : u.category === "Uncategorized" ? (
                    <Link href="/review" className="py-2 text-xs font-medium text-primary">
                      Sort them
                    </Link>
                  ) : null}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={() => setAdding(true)}>
          <Plus /> Add a category
        </Button>
        <Button
          variant="outline"
          onClick={() => (demo ? toast("Drafting with AI is off while the app shows sample data.") : setDrafting(true))}
        >
          <Sparkles /> Draft with AI
        </Button>
      </div>

      <Savings data={data} onAdd={() => setPaying(true)} run={run} />
      <Emergency data={data} run={run} />
      <Versions data={data} run={run} />

      <LineSheet line={editing} budget={data} change={change} onClose={() => setEditing(null)} />
      <AddSheet open={adding} budget={data} onClose={() => setAdding(false)} onPick={pick} />
      <AIBudgetSheet
        open={drafting}
        onOpenChange={setDrafting}
        lines={data.lines}
        save={(lines) => change("/api/budget", "PUT", { lines, source: "ai" })}
      />
      <SavingsSheet months={data.savings_history} open={saving && !!data.savings_rate} onOpenChange={setSaving} />
      <PaycheckSheet open={paying} budget={data} change={change} onClose={() => setPaying(false)} />
    </div>
  );
}

type LineSort = "usual" | "used" | "spent" | "least-left" | "most-left" | "name";
const LINE_SORTS: { id: LineSort; label: string }[] = [
  { id: "usual", label: "Usual order" },
  { id: "used", label: "Most used" },
  { id: "least-left", label: "Least left" },
  { id: "most-left", label: "Most left" },
  { id: "spent", label: "Most spent" },
  { id: "name", label: "A to Z" },
];
const SORT_KEY = "budget-sort";

function savedSort(): LineSort {
  try {
    const saved = localStorage.getItem(SORT_KEY);
    return LINE_SORTS.find((x) => x.id === saved)?.id ?? "usual";
  } catch {
    return "usual";
  }
}

/** "Usual order" keeps the server's order; a category with no limit counts as fully used. */
function sortLines(lines: BudgetLine[], sort: LineSort): BudgetLine[] {
  const used = (l: BudgetLine) => (l.limit_cents ? l.spent_cents / l.limit_cents : l.spent_cents ? 1e9 : 0);
  const left = (l: BudgetLine) => l.limit_cents - l.spent_cents;
  const by: Record<LineSort, ((a: BudgetLine, b: BudgetLine) => number) | null> = {
    usual: null,
    used: (a, b) => used(b) - used(a),
    spent: (a, b) => b.spent_cents - a.spent_cents,
    "least-left": (a, b) => left(a) - left(b),
    "most-left": (a, b) => left(b) - left(a),
    name: (a, b) => a.category.localeCompare(b.category),
  };
  const f = by[sort];
  return f ? [...lines].sort((a, b) => f(a, b) || a.category.localeCompare(b.category)) : lines;
}

/** How far to trust the numbers above: the share of money sorted with confidence, and a way to fix the rest. */
function CategoryCheck() {
  const { data } = useApi<ReviewState>("/api/review");
  if (!data || data.score === null) return null;
  const clean = data.items === 0;
  return (
    <Link href="/review" className="block rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
      <Card className={cn("transition-colors hover:bg-muted/40", !clean && data.score < 95 && "border-warning/40")}>
        <CardContent className="flex items-center gap-4 pt-5">
          <div
            className={cn(
              "grid size-11 shrink-0 place-items-center rounded-full text-sm font-semibold tabular-nums",
              clean || data.score >= 98 ? "bg-positive/10 text-positive" : data.score >= 90 ? "bg-accent text-accent-foreground" : "bg-warning/10 text-warning",
            )}
          >
            {data.score}%
          </div>
          <div className="min-w-0 flex-1">
            <p className="font-medium">{clean ? "Every category checks out" : `${data.items} transaction${data.items === 1 ? "" : "s"} to check`}</p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {clean ? (
                "Card payments, investments and moves between your accounts are kept out of spending."
              ) : (
                <>
                  <Money cents={data.doubtful_cents} /> the app isn&apos;t sure about. Sorting it makes your savings rate exact.
                </>
              )}
            </p>
          </div>
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        </CardContent>
      </Card>
    </Link>
  );
}

function Group({ title, lines, month, onPick }: { title: string; lines: BudgetLine[]; month: string; onPick: (l: BudgetLine) => void }) {
  const spent = lines.reduce((s, b) => s + b.spent_cents, 0);
  const limit = lines.reduce((s, b) => s + b.limit_cents, 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <span className="text-sm text-muted-foreground">
          <Money cents={spent} /> of <Money cents={limit} />
        </span>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-1 px-2 pb-3">
        {lines.map((b) => {
          const left = b.limit_cents - b.spent_cents;
          return (
            <div key={b.category_id} className="relative">
              <Link
                href={monthOf(b.category_id)}
                className="block rounded-xl py-2.5 pr-12 pl-3 transition-colors hover:bg-muted/50"
                aria-label={`${b.category}: ${b.limit_cents ? Math.round((b.spent_cents / b.limit_cents) * 100) : 0}% used.${b.forecast ? ` ${outlookText(b, b.forecast)}` : ""} See this month's transactions`}
              >
                <div className="mb-1.5 flex items-baseline justify-between gap-3 text-sm">
                  <span className="font-medium">{b.category}</span>
                  <span className={left < 0 ? "text-negative" : "text-muted-foreground"}>
                    {left > 0 ? (
                      <>
                        <Money cents={left} /> left
                      </>
                    ) : left === 0 ? (
                      "Paid"
                    ) : (
                      <>
                        <Money cents={-left} /> over
                      </>
                    )}
                  </span>
                </div>
                <Progress
                  value={b.spent_cents}
                  max={b.limit_cents}
                  label={`${b.category} budget used`}
                  tone={b.kind === "fixed" ? "bill" : "budget"}
                  projected={b.forecast && b.forecast.status !== "over" ? b.forecast.projected_cents : undefined}
                />
                {b.forecast && <Outlook line={b} f={b.forecast} month={month} />}
              </Link>
              <button
                type="button"
                onClick={() => onPick(b)}
                className="absolute top-1/2 right-1 grid size-10 -translate-y-1/2 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                aria-label={`Change the ${b.category} limit`}
              >
                <Pencil className="size-4" aria-hidden />
              </button>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

/** The month-end line under a category's bar. */
function Outlook({ line, f, month }: { line: BudgetLine; f: Forecast; month: string }) {
  if (f.status === "over") return null;
  if (line.kind === "fixed") {
    if (!f.due.length && f.status !== "heading_over") return null;
    const next = f.due[0];
    return (
      <p className={cn("mt-1.5 text-xs", f.status === "heading_over" ? "text-warning" : "text-muted-foreground")}>
        {f.status === "heading_over" ? (
          <>
            On track to spend <Money cents={f.projected_cents} />, <Money cents={f.over_cents} /> over
          </>
        ) : (
          <>
            <Money cents={f.due_cents} /> still to come
            {next && ` · ${next.merchant}${f.due.length > 1 ? ` and ${f.due.length - 1} more` : ""}, ${dayOf(month, next.day)}`}
          </>
        )}
      </p>
    );
  }
  return (
    <p className={cn("mt-1.5 flex items-center gap-1 text-xs", f.status === "heading_over" ? "text-warning" : "text-muted-foreground")}>
      {f.status === "heading_over" && <TrendingUp className="size-3.5 shrink-0" aria-hidden />}
      <span>
        On track to spend <Money cents={f.projected_cents} />
        {f.status === "heading_over" ? (
          <>
            , <Money cents={f.over_cents} /> over
          </>
        ) : f.status === "close" ? (
          ", just under"
        ) : null}
      </span>
    </p>
  );
}

function outlookText(line: BudgetLine, f: Forecast): string {
  if (f.status === "over") return "";
  if (f.status === "heading_over")
    return `On track to spend ${formatMoney(f.projected_cents)} by month-end, ${formatMoney(f.over_cents)} over.`;
  if (line.kind === "fixed") return f.due_cents ? `${formatMoney(f.due_cents)} still to come this month.` : "";
  return `On track to spend ${formatMoney(f.projected_cents)} by month-end.`;
}

function dayOf(month: string, day: number): string {
  return short.format(new Date(`${month.slice(0, 8)}${String(day).padStart(2, "0")}`));
}

/** Where the month is heading: the categories on pace to go over, or a calm "on track". */
function MonthEnd({ data }: { data: BudgetState }) {
  const f = data.forecast!;
  const over = data.lines.filter((l) => f.heading_over.includes(l.category_id));
  const days = `${f.days_left} day${f.days_left === 1 ? "" : "s"}`;
  return (
    <Card className={cn(over.length > 0 && "border-warning/40")}>
      <CardHeader>
        <CardTitle>By month-end</CardTitle>
        <CalendarRange className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        {over.length ? (
          <>
            <p className="text-sm">
              <span className="font-medium">
                {over.length === 1 ? `${over[0]!.category} is` : `${over.length} categories are`} on pace to go over
              </span>{" "}
              <span className="text-muted-foreground">with {days} to go. Slowing down now keeps it in budget.</span>
            </p>
            <ul className="grid grid-cols-1 gap-1">
              {over.map((l) => (
                <li key={l.category_id}>
                  <Link
                    href={monthOf(l.category_id)}
                    className="flex w-full items-center gap-3 rounded-xl bg-warning/10 px-3 py-2.5 text-left text-sm transition-colors hover:bg-warning/15"
                  >
                    <TrendingUp className="size-4 shrink-0 text-warning" aria-hidden />
                    <span className="min-w-0 flex-1 truncate font-medium">{l.category}</span>
                    <span className="text-muted-foreground">
                      <Money cents={l.forecast!.projected_cents} /> of <Money cents={l.limit_cents} />
                    </span>
                    <span className="font-medium text-warning">
                      <Money cents={l.forecast!.over_cents} signed />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-sm">
            <span className="font-medium">Everything&apos;s on track.</span>{" "}
            <span className="text-muted-foreground">
              At this pace you&apos;ll spend about <Money cents={f.projected_cents} /> of <Money cents={f.limit_cents} /> this month.
            </span>
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          Bills still to come are counted once. Everyday spending follows this month&apos;s pace, leaning on a usual month early on.
        </p>
      </CardContent>
    </Card>
  );
}

type Run = (path: string, method: string, body: unknown, done: string) => Promise<void>;

function Savings({ data, onAdd, run }: { data: BudgetState; onAdd: () => void; run: Run }) {
  const rate = data.savings_rate;
  const hasMatch = data.paychecks.some((p) => p.employer_match_cents > 0);
  return (
    <Card id="pay" className="scroll-mt-20">
      <CardHeader>
        <CardTitle>Where the pay went</CardTitle>
        {rate && <span className="text-sm text-muted-foreground">{monthName.format(new Date(rate.month))}</span>}
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        {rate ? (
          <>
            <dl className="grid grid-cols-1 gap-2 text-sm">
              <Row label={rate.basis === "gross" ? "Pay before taxes, and other income" : "Money in"} cents={rate.income_cents} />
              {rate.taxes_cents !== 0 && <Row label="Taxes" cents={-rate.taxes_cents} />}
              {rate.deductions_cents !== 0 && <Row label="Other paycheck deductions" cents={-rate.deductions_cents} />}
              <Row label="Spending" cents={-rate.spending_cents} />
              <div className="mt-1 flex items-baseline justify-between border-t pt-3 font-semibold">
                <dt>Saved</dt>
                <dd className={rate.saved_cents < 0 ? "text-negative" : "text-positive"}>
                  <Money cents={rate.saved_cents} /> · {rate.percent.toFixed(1)}%
                </dd>
              </div>
            </dl>
            {data.savings_history.length > 1 && <SavingsBars months={data.savings_history} />}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">No income in the last full month yet. It shows up here once a paycheck lands.</p>
        )}
        {rate?.basis !== "gross" && (
          <p className="rounded-xl bg-muted px-4 py-3 text-sm text-muted-foreground">
            This uses take-home pay, so 401(k) and HSA money taken out before payday isn&apos;t counted yet. Add a pay stub for the true
            rate.
          </p>
        )}
        {data.schedules.map((sc) => (
          <div key={sc.user_id ?? "me"} className="flex items-center gap-3 rounded-xl bg-muted px-4 py-3 text-sm">
            <CalendarClock className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            <p className="min-w-0 flex-1">
              <span className="font-medium">
                {data.people.length > 1 && sc.person ? `${sc.person}: ` : ""}
                {FREQUENCY_LABEL[sc.frequency]}
              </span>
              <span className="text-muted-foreground">
                {" "}
                · next payday {short.format(new Date(sc.next_date))} · about <Money cents={sc.monthly_gross_cents} /> gross a month
              </span>
            </p>
          </div>
        ))}
        {(data.stub_requests ?? []).map((r) => (
          <div
            key={`${r.employer}-${r.date}`}
            className="flex items-start gap-3 rounded-xl border border-primary/30 bg-primary/5 px-4 py-3 text-sm"
          >
            <FileText className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="font-medium">Add your {short.format(new Date(r.date))} pay stub</p>
              <p className="text-muted-foreground">
                {r.reason === "changed" && r.before_cents !== null ? (
                  <>
                    {r.employer} paid <Money cents={r.cents} />, {r.cents > r.before_cents ? "up" : "down"} from{" "}
                    <Money cents={r.before_cents} />, so taxes or 401(k) may have changed.
                  </>
                ) : r.reason === "new_year" ? (
                  "It's the first paycheck of the year, when withholding and 401(k) limits reset."
                ) : (
                  <>
                    <Money cents={r.cents} /> from {r.employer} looks like a bonus, which is taxed differently.
                  </>
                )}{" "}
                Until then it&apos;s filled in from your usual stub.
              </p>
            </div>
            <Button size="sm" variant="secondary" onClick={onAdd}>
              Add
            </Button>
          </div>
        ))}
        {data.paychecks.length > 0 && (
          <ul className="divide-y rounded-xl border">
            {data.paychecks.slice(0, 4).map((p) => (
              <li key={p.id} className="flex items-center gap-3 px-4 py-3 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="font-medium">
                    {short.format(new Date(p.date))}
                    {data.people.length > 1 && p.person && ` · ${p.person}`}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    <Money cents={p.gross_cents} /> gross, <Money cents={p.net_cents} /> take-home
                    {p.deposit ? " · deposit found" : ""}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Remove the ${short.format(new Date(p.date))} pay stub`}
                  onClick={() => run(`/api/paychecks/${p.id}`, "DELETE", undefined, "Pay stub removed.")}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        )}
        {hasMatch && (
          <div className="rounded-2xl border p-4">
            <Switch
              checked={data.include_match}
              onCheckedChange={(on) =>
                run("/api/budget/settings", "PATCH", { include_match: on }, on ? "Employer match counted." : "Employer match left out.")
              }
              label="Count employer match"
              description="Adds your employer's 401(k) match to both pay and savings."
            />
          </div>
        )}
        <Button variant="secondary" className="justify-self-start" onClick={onAdd}>
          <Plus /> Add a pay stub
        </Button>
      </CardContent>
    </Card>
  );
}

function Row({ label, cents }: { label: string; cents: number }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>
        <Money cents={cents} />
      </dd>
    </div>
  );
}

/** One bar per month: the savings rate, so a good or bad month stands out at a glance. */
function SavingsBars({ months }: { months: SavingsMonth[] }) {
  const top = Math.max(...months.map((m) => Math.abs(m.percent)), 1);
  const label = months.map((m) => `${monthName.format(new Date(m.month))} ${m.percent.toFixed(0)}%`).join(", ");
  return (
    <div role="img" aria-label={`Savings rate by month: ${label}.`} className="flex h-24 items-end gap-2">
      {months.map((m) => (
        <div key={m.month} className="flex h-full flex-1 flex-col items-center justify-end gap-1">
          <span className="text-[11px] text-muted-foreground">{m.percent.toFixed(0)}%</span>
          <div
            className={cn("w-full max-w-10 rounded-md", m.percent < 0 ? "bg-negative/70" : "bg-chart-1")}
            style={{ height: `${Math.max((Math.abs(m.percent) / top) * 60, 3)}%` }}
          />
          <span className="text-[11px] text-muted-foreground">{monthName.format(new Date(m.month)).slice(0, 3)}</span>
        </div>
      ))}
    </div>
  );
}

function Emergency({ data, run }: { data: BudgetState; run: Run }) {
  const e = data.emergency;
  const months = e.monthly_cents ? e.have_cents / e.monthly_cents : 0;
  const setMonths = (n: number) => run("/api/budget/settings", "PATCH", { emergency_months: n }, `Target set to ${n} months.`);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Emergency fund</CardTitle>
        <ShieldCheck className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        {e.monthly_cents ? (
          <>
            <p className="text-sm">
              <span className="text-2xl font-semibold tracking-tight">
                <Money cents={e.have_cents} />
              </span>{" "}
              <span className="text-muted-foreground">
                of <Money cents={e.target_cents} />
              </span>
            </p>
            <Progress value={e.have_cents} max={e.target_cents} label="Emergency fund target reached" tone="goal" />
            <p className="text-sm text-muted-foreground">
              {e.picked ? "The accounts you picked cover" : "Your cash covers"} {months >= 10 ? Math.floor(months) : months.toFixed(1)}{" "}
              months of essentials (<Money cents={e.monthly_cents} /> a month: bills plus the everyday categories marked essential).
              {e.set_aside_cents > 0 && !e.picked && (
                <>
                  {" "}
                  Not counting <Money cents={e.set_aside_cents} /> set aside for other goals.
                </>
              )}
            </p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            Once the budget has bills or essentials in it, this shows how many months your cash covers.
          </p>
        )}
        <Link href="/goals" className="flex items-center gap-3 rounded-2xl border px-4 py-3 text-sm transition-colors hover:bg-muted">
          <Target className="size-4 text-muted-foreground" aria-hidden />
          <span className="flex-1">
            <span className="block font-medium">Saving for something else?</span>
            <span className="block text-xs text-muted-foreground">
              Set money aside for a house, a car or a trip in Goals, so it isn&apos;t counted here.
            </span>
          </span>
          <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
        </Link>
        {e.target_typed ? (
          <p className="text-sm text-muted-foreground">
            The target is a set amount, <Money cents={e.target_cents} />. Change it on the{" "}
            <Link href="/goals" className="font-medium text-primary">
              Goals
            </Link>{" "}
            page.
          </p>
        ) : (
          <div className="flex items-center justify-between gap-3 rounded-2xl border px-4 py-2.5">
            <span className="text-sm font-medium">Target</span>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                aria-label="One month fewer"
                disabled={e.months <= 1}
                onClick={() => setMonths(e.months - 1)}
              >
                <Minus />
              </Button>
              <span className="w-20 text-center text-sm" aria-live="polite">
                {e.months} months
              </span>
              <Button
                variant="ghost"
                size="icon"
                aria-label="One month more"
                disabled={e.months >= 36}
                onClick={() => setMonths(e.months + 1)}
              >
                <Plus />
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const SOURCE = { draft: "Drafted from spending", edit: "Edited", restore: "Brought back", ai: "Drafted with AI" } as const;

function Versions({ data, run }: { data: BudgetState; run: Run }) {
  const [open, setOpen] = useState(false);
  if (!data.versions.length) return null;
  const current = data.version?.id;
  return (
    <Card>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-5 py-4 text-left"
      >
        <History className="size-4 text-muted-foreground" aria-hidden />
        <span className="flex-1 text-sm font-medium">Budget history</span>
        <span className="text-xs text-muted-foreground">
          {data.versions.length} version{data.versions.length === 1 ? "" : "s"}
        </span>
        <ChevronRight className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-90")} aria-hidden />
      </button>
      {open && (
        <ul className="divide-y border-t">
          {data.versions.map((v) => (
            <li key={v.id} className="flex items-center gap-3 px-5 py-3 text-sm">
              <div className="min-w-0 flex-1">
                <p className="font-medium">
                  {SOURCE[v.source]}
                  {v.by && ` by ${v.by}`}
                </p>
                <p className="text-xs text-muted-foreground">
                  {stamp.format(new Date(v.at))} · <Money cents={v.total_cents} /> a month
                </p>
              </div>
              {v.id === current ? (
                <span className="text-xs font-medium text-muted-foreground">Current</span>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => run(`/api/budget/versions/${v.id}/restore`, "POST", undefined, "Budget brought back.")}
                >
                  Bring back
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
