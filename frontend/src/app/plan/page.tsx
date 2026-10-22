"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ChevronRight, Landmark, Pencil, RotateCcw, Scissors, Target, Telescope, TrendingUp } from "lucide-react";
import { PlanChart } from "@/components/plan-chart";
import { Money, formatMoney } from "@/components/money";
import { Notice, PageTitle } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { COLORS, type GoalsState, ICONS } from "@/lib/goals";
import { resetHousehold, useApi } from "@/lib/household";
import {
  LEVEL_NAMES,
  type Level,
  type CountsAs,
  type MoneyIn,
  type PlanInputs,
  type PlanState,
  type SavingsMonth,
  type Scenario,
  type Utility,
  project,
} from "@/lib/plan";
import { cn } from "@/lib/utils";

const monthName = new Intl.DateTimeFormat("en-US", {
  month: "long",
  timeZone: "UTC",
});
const LEVELS: Level[] = ["lean", "regular", "fat"];

type Linked = Extract<PlanState, { linked: true }>;

export default function Plan() {
  const { data, error } = useApi<PlanState>("/api/plan");
  const [local, setLocal] = useState<PlanState | null>(null);
  const state = local ?? data;
  return (
    <>
      <PageTitle
        title="Plan"
        subtitle="Where today's savings take you, and when work becomes optional. A range, because no one knows future returns."
      />
      {error && !state && (
        <Notice icon={<Telescope className="size-5" />} title="Couldn't load the plan">
          {error}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {!state && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-96 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      )}
      {state && !state.linked && (
        <Notice
          icon={<Telescope className="size-5" />}
          title="No accounts yet"
          action={
            <Button asChild size="sm" className="mt-3">
              <Link href="/settings/accounts">Add an account</Link>
            </Button>
          }
        >
          The plan starts from your net worth and what you save each month. Link an account and it fills in.
        </Notice>
      )}
      {state?.linked && (
        <PlanView
          key={JSON.stringify(state.inputs)}
          p={state}
          onSaved={(next) => {
            setLocal(next);
            resetHousehold();
          }}
        />
      )}
    </>
  );
}

function savedScenario(i: PlanInputs): Scenario {
  return {
    monthly: i.monthly_savings_cents ?? 0,
    ret: i.returns.mid,
    yearly: i.levels[i.level].yearly_cents,
    withdrawal: i.withdrawal_rate,
  };
}

function PlanView({ p, onSaved }: { p: Linked; onSaved: (p: PlanState) => void }) {
  const i = p.inputs;
  const [s, setS] = useState<Scenario>(() => savedScenario(i));
  const [level, setLevel] = useState<Level>(i.level);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  const result = useMemo(() => project(i, s), [i, s]);
  const saved = savedScenario(i);
  const changed =
    s.monthly !== saved.monthly ||
    s.ret !== saved.ret ||
    s.yearly !== saved.yearly ||
    s.withdrawal !== saved.withdrawal ||
    level !== i.level;

  const save = async (body: Record<string, unknown>, done: string): Promise<boolean> => {
    if (p.demo) {
      toast("The plan can't be changed while the app shows sample data.");
      return false;
    }
    setSaving(true);
    try {
      onSaved(await api<PlanState>("/api/plan/settings", { method: "PATCH", body }));
      toast(done);
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    } finally {
      setSaving(false);
    }
  };
  const countAs = async (key: string, as: CountsAs) => {
    if (p.demo) {
      toast("This can't be changed while the app shows sample data.");
      return;
    }
    try {
      onSaved(await api<PlanState>("/api/plan/money-in", { method: "PUT", body: { key, counts_as: as } }));
      toast(as === "pay" ? "Counted as pay" : as === "me" ? "Counted as your own money, not money in" : "Counted as other money in");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const saveScenario = () =>
    save(
      {
        level,
        withdrawal_rate: s.withdrawal,
        // Typed in only when it differs from what the app measured; the same number keeps following the budget.
        savings_cents: s.monthly === i.measured_savings_cents ? null : s.monthly,
        return_mid: s.ret === i.returns_source.derived_mid ? null : s.ret,
        ...(s.yearly !== i.levels[level].yearly_cents ? { spending: { [level]: s.yearly } } : {}),
      },
      "Saved as your plan.",
    );

  if (i.monthly_savings_cents === null && !changed) {
    return (
      <div className="grid grid-cols-1 gap-4">
        <Notice
          icon={<Telescope className="size-5" />}
          title="How much do you save a month?"
          action={
            <div className="mt-3 flex flex-wrap gap-2">
              <Button asChild size="sm">
                <Link href="/budget#pay">Add a pay stub</Link>
              </Button>
              <Button size="sm" variant="outline" onClick={() => setAboutOpen(true)}>
                Type it in
              </Button>
            </div>
          }
        >
          The app works it out from your pay stubs, or from paychecks landing in a linked account, against what you spend. It hasn&apos;t
          seen any pay yet.
        </Notice>
        <GoalsCard />
        <TaxesLink />
        <Ideas p={p} s={s} />
        <About open={aboutOpen} onOpenChange={setAboutOpen} p={p} save={save} saving={saving} askSavings />
      </div>
    );
  }

  const fire = result.fire;
  const age = (year: number | null) => (year !== null && i.birth_year ? year - i.birth_year : null);
  const horizon = Math.min(30, Math.max(12, (fire.low ?? fire.mid ?? i.year + 25) - i.year + 3));
  const in10 = result.bands[10]!;

  return (
    <div className="grid grid-cols-1 gap-4">
      <Card className="overflow-hidden">
        <CardHeader>
          <CardTitle>Work becomes optional</CardTitle>
          <Button variant="ghost" size="sm" onClick={() => setAboutOpen(true)} className="-mt-1.5 -mr-2">
            <Pencil /> {i.birth_year ? "Your details" : "Add your age"}
          </Button>
        </CardHeader>
        <CardContent>
          {result.target === null ? (
            <p className="text-lg font-semibold">Set the spending you&apos;d like later to see a date.</p>
          ) : fire.mid === null ? (
            <>
              <p className="text-3xl font-semibold tracking-tight">Not within 60 years</p>
              <p className="mt-1 text-sm text-muted-foreground">Saving more, or planning to spend less later, brings it into view.</p>
            </>
          ) : fire.mid <= i.year ? (
            <>
              <p className="text-3xl font-semibold tracking-tight">You&apos;re there</p>
              <p className="mt-1 text-sm text-muted-foreground">
                <Money cents={i.start_cents} style="compact" /> already covers <Money cents={s.yearly} style="compact" /> a year at a{" "}
                {s.withdrawal}% withdrawal.
              </p>
            </>
          ) : (
            <>
              <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-4xl font-semibold tracking-tight">Around {fire.mid}</span>
                {age(fire.mid) !== null && <span className="text-lg font-medium text-muted-foreground">at age {age(fire.mid)}</span>}
              </p>
              <p className="mt-1.5 text-sm text-muted-foreground">
                {fire.high !== null && fire.low !== null && fire.high !== fire.low ? (
                  <>
                    Between {fire.high} and {fire.low}, depending on markets.{" "}
                  </>
                ) : fire.low === null ? (
                  <>Later, or never, if markets disappoint. </>
                ) : null}
                That&apos;s when about <Money cents={result.target} style="compact" /> covers <Money cents={s.yearly} style="compact" /> a
                year, taking out {s.withdrawal}% a year.
              </p>
            </>
          )}
          <div className="mt-5">
            <PlanChart
              history={p.history}
              bands={result.bands}
              target={result.target}
              fireYear={fire.mid}
              birthYear={i.birth_year}
              years={horizon}
            />
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            In today&apos;s dollars: returns are after inflation ({result.returns.low}% to {result.returns.high}% a year). The solid line is
            your net worth so far
            {p.history.length < 30 ? "; it grows a day at a time from when you linked your accounts" : ""}.
          </p>
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <CardHeader>
            <CardTitle>In 10 years</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold tracking-tight">
              <Money cents={in10.mid} style="compact" />
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              <Money cents={in10.low} style="compact" /> to <Money cents={in10.high} style="compact" />
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Saving a month</CardTitle>
            <TrendingUp className="size-4 text-positive" aria-hidden />
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-semibold tracking-tight">
              <Money cents={s.monthly} />
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              {s.monthly === i.measured_savings_cents
                ? i.savings_source.short
                : i.savings_typed && s.monthly === i.monthly_savings_cents
                  ? "the number you typed in"
                  : "what if"}
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>What if</CardTitle>
          {changed && (
            <Button
              variant="ghost"
              size="sm"
              className="-mt-1.5 -mr-2"
              onClick={() => {
                setS(saved);
                setLevel(i.level);
              }}
            >
              <RotateCcw /> Reset
            </Button>
          )}
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-6">
          <Slider
            label="Saving each month"
            value={s.monthly}
            display={<Money cents={s.monthly} />}
            min={0}
            max={Math.max(1_000_000, Math.ceil(((i.measured_savings_cents ?? s.monthly) * 2.5) / 50_000) * 50_000)}
            step={5_000}
            onChange={(v) => setS({ ...s, monthly: v })}
            hint={
              <Source
                note={i.savings_source.note}
                breakdown={i.savings_source.breakdown}
                excluded={i.savings_source.excluded}
                months={i.savings_source.months}
                moneyIn={i.savings_source.money_in ?? []}
                onCountAs={countAs}
                differs={i.measured_savings_cents !== null && s.monthly !== i.measured_savings_cents}
                typed={i.savings_typed && s.monthly === i.monthly_savings_cents}
                derived={i.measured_savings_cents !== null ? <Money cents={i.measured_savings_cents} /> : null}
                onUse={() => i.measured_savings_cents !== null && setS({ ...s, monthly: i.measured_savings_cents })}
              />
            }
          />
          <div className="grid grid-cols-1 gap-2">
            <p className="text-sm font-medium" id="level-label">
              Spending, once work is optional
            </p>
            <div role="radiogroup" aria-labelledby="level-label" className="grid grid-cols-3 gap-2">
              {LEVELS.map((l) => (
                <button
                  key={l}
                  type="button"
                  role="radio"
                  aria-checked={level === l}
                  onClick={() => {
                    setLevel(l);
                    setS({ ...s, yearly: i.levels[l].yearly_cents });
                  }}
                  className={cn(
                    "rounded-xl border px-2.5 py-2.5 text-left transition-colors",
                    level === l ? "border-primary bg-accent text-accent-foreground" : "hover:bg-muted",
                  )}
                >
                  <span className="block text-sm font-medium">{LEVEL_NAMES[l].title}</span>
                  <span className="block text-xs opacity-80">
                    <Money cents={i.levels[l].yearly_cents} style="compact" />
                    /yr
                  </span>
                </button>
              ))}
            </div>
          </div>
          <Slider
            label="Yearly spending"
            value={s.yearly}
            display={<Money cents={s.yearly} />}
            min={0}
            max={Math.max(20_000_000, Math.ceil((i.levels.fat.yearly_cents * 1.5) / 1_000_000) * 1_000_000)}
            step={100_000}
            onChange={(v) => setS({ ...s, yearly: v })}
            hint={
              <Source
                note={`${LEVEL_NAMES[level].title}: ${i.levels[level].note.charAt(0).toLowerCase()}${i.levels[level].note.slice(1)}`}
                differs={s.yearly !== i.levels[level].derived_cents}
                typed={i.levels[level].typed && s.yearly === i.levels[level].yearly_cents}
                derived={<Money cents={i.levels[level].derived_cents} />}
                onUse={() => setS({ ...s, yearly: i.levels[level].derived_cents })}
              />
            }
          />
          <Slider
            label="Market return, after inflation"
            value={s.ret}
            display={`${s.ret}% a year`}
            min={1}
            max={10}
            step={0.5}
            onChange={(v) => setS({ ...s, ret: v })}
            hint={
              <Source
                note={i.returns_source.note}
                differs={s.ret !== i.returns_source.derived_mid}
                typed={i.returns_source.typed && s.ret === i.returns.mid}
                derived={`${i.returns_source.derived_mid}%`}
                onUse={() => setS({ ...s, ret: i.returns_source.derived_mid })}
              />
            }
          />
          <Slider
            label="Taking out each year"
            value={s.withdrawal}
            display={`${s.withdrawal}%`}
            min={3}
            max={5}
            step={0.25}
            onChange={(v) => setS({ ...s, withdrawal: v })}
            hint="4% is the common rule of thumb for a 30-year retirement; 3.5% is safer for a longer one."
          />
          {changed && (
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={saveScenario} disabled={saving}>
                Save as my plan
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <Ideas p={p} s={s} />
      <GoalsCard />
      <TaxesLink />
      <About open={aboutOpen} onOpenChange={setAboutOpen} p={p} save={save} saving={saving} />
    </div>
  );
}

/** Under a slider: where the app's number came from, and a way back to it once the slider has moved away. */
function Source({
  note,
  breakdown,
  excluded,
  months,
  moneyIn,
  onCountAs,
  differs,
  typed,
  derived,
  onUse,
}: {
  note: string;
  breakdown?: { label: string; cents: number }[];
  excluded?: { label: string; cents: number } | null;
  months?: SavingsMonth[];
  moneyIn?: MoneyIn[];
  onCountAs?: (key: string, as: CountsAs) => void;
  differs: boolean;
  typed: boolean;
  derived: React.ReactNode;
  onUse: () => void;
}) {
  return (
    <>
      {breakdown && breakdown.length > 0 && (
        <Breakdown lines={breakdown} excluded={excluded ?? null} months={months ?? []} moneyIn={moneyIn ?? []} onCountAs={onCountAs} />
      )}
      {typed && <span className="font-medium text-foreground">You typed this in. </span>}
      {differs && derived !== null ? <>The app works out {derived}. </> : null}
      {note}
      {differs && derived !== null && (
        <>
          {" "}
          <button type="button" onClick={onUse} className="font-medium text-primary underline-offset-4 hover:underline">
            Use it
          </button>
        </>
      )}
    </>
  );
}

const shortMonth = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });

/**
 * The sum the app's number comes from: money in, then what comes off it, then the total. Below it, what was left
 * out as transfers between the household's own accounts, and each month on its own, to check against the bank.
 */
function Breakdown({
  lines,
  excluded,
  months,
  moneyIn,
  onCountAs,
}: {
  lines: { label: string; cents: number; detail?: string }[];
  excluded: { label: string; cents: number } | null;
  months: SavingsMonth[];
  moneyIn: MoneyIn[];
  onCountAs?: (key: string, as: CountsAs) => void;
}) {
  const total = lines.reduce((sum, l) => sum + l.cents, 0);
  return (
    <div className="mb-2 rounded-lg bg-muted/50 px-3 py-2.5">
      <dl className="money grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-xs tabular-nums">
        {lines.map((l) => (
          <div key={l.label} className="contents">
            <dt className="text-muted-foreground">
              {l.label}
              {l.detail && <span className="block text-[11px] opacity-80">{l.detail}</span>}
            </dt>
            <dd className={l.cents < 0 ? "text-right text-foreground" : "text-right text-positive"}>
              {l.cents < 0 ? "−" : "+"}
              <Money cents={Math.abs(l.cents)} />
            </dd>
          </div>
        ))}
        <div className="contents">
          <dt className="border-t pt-1 font-medium text-foreground">Saved in an average month</dt>
          <dd className="border-t pt-1 text-right font-medium text-foreground">
            <Money cents={total} />
          </dd>
        </div>
        {excluded !== null && excluded.cents > 0 && (
          <div className="contents">
            <dt className="pt-1 text-muted-foreground">{excluded.label}</dt>
            <dd className="pt-1 text-right text-muted-foreground">
              <Money cents={excluded.cents} />
            </dd>
          </div>
        )}
      </dl>
      {months.length > 0 && (
        <details className="group mt-2 text-xs">
          <summary className="cursor-pointer font-medium text-primary select-none">See each month</summary>
          <div className="mt-2 overflow-x-auto">
            <table className="money w-full text-right tabular-nums">
              <caption className="sr-only">Money in and out each month</caption>
              <thead className="text-muted-foreground">
                <tr>
                  <th scope="col" className="py-1 text-left font-normal">
                    Month
                  </th>
                  <th scope="col" className="py-1 font-normal">
                    Pay (deposits)
                  </th>
                  <th scope="col" className="py-1 font-normal">
                    Other in
                  </th>
                  <th scope="col" className="py-1 font-normal">
                    Spent
                  </th>
                  <th scope="col" className="py-1 font-normal">
                    Transfers
                  </th>
                </tr>
              </thead>
              <tbody>
                {months.map((m) => (
                  <tr key={m.month} className="border-t border-border/60">
                    <th scope="row" className="py-1 text-left font-normal text-muted-foreground">
                      {shortMonth.format(new Date(m.month + "T00:00:00Z"))}
                    </th>
                    <td className="py-1">
                      <Money cents={m.pay_cents} />
                      {m.pay_deposits !== undefined && <span className="ml-1 text-muted-foreground">({m.pay_deposits})</span>}
                    </td>
                    <td className="py-1">
                      <Money cents={m.other_in_cents} />
                    </td>
                    <td className="py-1">
                      <Money cents={m.spending_cents + m.taxes_cents} />
                    </td>
                    <td className="py-1 text-muted-foreground">
                      <Money cents={m.moved_cents} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-1.5 text-muted-foreground">
              Transfers are money arriving from your own accounts, like a move from savings or a card payment. They aren&apos;t counted. If
              a month&apos;s pay looks wrong, open Spending and check how its deposits are categorized.
            </p>
          </div>
          {moneyIn.length > 0 && <MoneyInList rows={moneyIn} months={months.length} onCountAs={onCountAs} />}
        </details>
      )}
    </div>
  );
}

const COUNTS_AS: Record<CountsAs, string> = { pay: "Pay", other: "Other money in", me: "My own money" };

/** Who sent the money that came in, and how each is counted, with a way to change it (remembered for next time). */
function MoneyInList({ rows, months, onCountAs }: { rows: MoneyIn[]; months: number; onCountAs?: (key: string, as: CountsAs) => void }) {
  return (
    <div className="mt-3 border-t pt-2">
      <p className="font-medium text-foreground">Money in, by who sent it</p>
      <p className="mt-0.5 text-muted-foreground">
        Only payroll deposits count as pay. If something is counted wrong, change it here and it stays that way.
      </p>
      <ul className="mt-2 grid gap-2">
        {rows.map((r) => (
          <li key={`${r.as}:${r.key}`} className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-foreground capitalize">{r.key}</p>
              <p className="money text-muted-foreground tabular-nums">
                <Money cents={r.monthly_cents} /> a month, {r.count} deposit{r.count === 1 ? "" : "s"} in {months} month
                {months === 1 ? "" : "s"}
              </p>
            </div>
            <select
              aria-label={`Count money from ${r.key} as`}
              className="h-8 rounded-md border bg-background px-2 text-xs text-foreground"
              value={r.as}
              disabled={!onCountAs}
              onChange={(e) => onCountAs?.(r.key, e.target.value as CountsAs)}
            >
              {(["pay", "other", "me"] as const).map((k) => (
                <option key={k} value={k}>
                  {COUNTS_AS[k]}
                </option>
              ))}
            </select>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Ideas({ p, s }: { p: Linked; s: Scenario }) {
  const { subscriptions, rising, subscriptions_yearly_cents: yearly } = p.ideas;
  const utilities = p.ideas.utilities ?? [];
  const i = p.inputs;
  const monthly = Math.round(yearly / 12);
  // What cancelling every subscription would do: more saved now, and less needed later.
  const sooner = useMemo(() => {
    if (!monthly || s.yearly <= 0) return null;
    const now = project(i, s).fire.mid;
    const then = project(i, {
      ...s,
      monthly: s.monthly + monthly,
      yearly: Math.max(s.yearly - yearly, 0),
    }).fire.mid;
    return now !== null && then !== null && now > i.year ? now - then : null;
  }, [i, s, monthly, yearly]);
  if (!subscriptions.length && !rising.length && !utilities.length) {
    return (
      <Notice icon={<Scissors className="size-5" />} title="Ways to save more">
        Ideas show up here once there are a few months of transactions: subscriptions you may have forgotten, and categories creeping up.
      </Notice>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Ways to save more</CardTitle>
        <Scissors className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-5">
        {subscriptions.length > 0 && (
          <div>
            <p className="text-sm">
              <span className="font-medium">
                {subscriptions.length} subscription
                {subscriptions.length === 1 ? "" : "s"}
              </span>{" "}
              cost <Money cents={yearly} /> a year.
              {sooner !== null && sooner > 0 && (
                <span className="text-muted-foreground">
                  {" "}
                  Without them, work becomes optional about {sooner} year
                  {sooner === 1 ? "" : "s"} sooner.
                </span>
              )}
            </p>
            <ul className="mt-2 divide-y">
              {subscriptions.slice(0, 8).map((x) => (
                <li key={x.merchant} className="flex items-center justify-between gap-3 py-2.5 text-sm">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate">{x.merchant}</span>
                    {x.went_up && <Badge variant="negative">Price went up</Badge>}
                  </span>
                  <span className="shrink-0 text-muted-foreground">
                    <Money cents={x.monthly_cents} style="exact" className="font-medium text-foreground" />
                    /mo
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {utilities.length > 0 && <Utilities utilities={utilities} />}
        {rising.length > 0 && (
          <div>
            <p className="text-sm font-medium">Creeping up</p>
            <ul className="mt-2 divide-y">
              {rising.map((r) => (
                <li key={r.category_id} className="py-2.5 text-sm">
                  <Link href="/budget" className="flex items-center justify-between gap-3">
                    <span className="min-w-0">
                      <span className="block">{r.category}</span>
                      <span className="block text-xs text-muted-foreground">
                        <Money cents={r.spent_cents} /> in {monthName.format(new Date(r.month))}, usually <Money cents={r.usual_cents} />
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-1 font-medium text-negative">
                      <Money cents={r.change_cents} signed />
                      <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** Utilities and other bills: not something to cancel, but a price rise is worth a call or a look at the plan. */
function Utilities({ utilities }: { utilities: Utility[] }) {
  const onlyUtilities = utilities.every((u) => u.kind === "utility");
  const flagged = utilities.filter((u) => u.went_up || u.higher).length;
  return (
    <div>
      <p className="text-sm">
        <span className="font-medium">{onlyUtilities ? "Utilities" : "Utilities and bills"}</span>{" "}
        <span className="text-muted-foreground">
          {flagged
            ? `aren't ones to cancel, but ${flagged === 1 ? "one costs" : `${flagged} cost`} more than usual. A new plan or a call can bring it down.`
            : "aren't ones to cancel. Prices look steady."}
        </span>
      </p>
      <ul className="mt-2 divide-y">
        {utilities.slice(0, 8).map((x) => (
          <li key={x.merchant} className="flex items-center justify-between gap-3 py-2.5 text-sm">
            <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
              <span className="truncate">{x.merchant}</span>
              {x.went_up ? (
                <Badge variant="negative">Price went up</Badge>
              ) : x.higher ? (
                <Badge variant="warning">Higher than usual</Badge>
              ) : null}
            </span>
            <span className="shrink-0 text-right text-muted-foreground">
              <Money cents={x.monthly_cents} style="exact" className="font-medium text-foreground" />
              /mo
              {(x.went_up || x.higher) && (
                <span className="block text-xs">
                  usually <Money cents={x.usual_cents} />
                </span>
              )}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Goals at a glance (the emergency fund is one): each one's progress, and the way to the Goals page. */
function GoalsCard() {
  const { data } = useApi<GoalsState>("/api/goals");
  return (
    <Card>
      <CardHeader>
        <CardTitle>Goals</CardTitle>
        <Button asChild variant="ghost" size="sm" className="-mt-1.5 -mr-2">
          <Link href="/goals">
            {data && data.goals.length > 1 ? "All goals" : "Add a goal"} <ChevronRight />
          </Link>
        </Button>
      </CardHeader>
      <CardContent>
        {!data ? (
          <Skeleton className="h-24 rounded-xl" />
        ) : (
          <ul className="grid grid-cols-1 gap-3">
            {data.goals.slice(0, 5).map((g) => {
              const Icon = ICONS[g.icon] ?? Target;
              const color = COLORS[g.color] ?? COLORS.teal;
              const ratio = g.target_cents > 0 ? Math.min(g.saved_cents / g.target_cents, 1) : 0;
              return (
                <li key={g.id}>
                  <Link href="/goals" className="flex items-center gap-3 rounded-xl py-0.5">
                    <span className={cn("grid size-9 shrink-0 place-items-center rounded-xl", color.tile)} aria-hidden>
                      <Icon className="size-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-2 text-sm">
                        <span className="truncate font-medium">{g.name}</span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          <Money cents={g.saved_cents} style="compact" />
                          {g.target_cents > 0 && (
                            <>
                              {" "}
                              of <Money cents={g.target_cents} style="compact" />
                            </>
                          )}
                        </span>
                      </span>
                      <span
                        role="progressbar"
                        aria-label={`${g.name} progress`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={Math.round(ratio * 100)}
                        className="mt-1.5 block h-1.5 w-full overflow-hidden rounded-full bg-muted"
                      >
                        <span
                          className={cn("block h-full rounded-full", ratio >= 1 ? "bg-positive" : color.bar)}
                          style={{ width: `${ratio * 100}%` }}
                        />
                      </span>
                    </span>
                  </Link>
                </li>
              );
            })}
            {data.goals.length === 1 && (
              <li className="text-xs text-muted-foreground">
                Saving for a house or a car? Make it a goal, so the money set aside for it isn&apos;t counted as your emergency fund.
              </li>
            )}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function TaxesLink() {
  return (
    <Link href="/taxes" className="block">
      <Card className="transition-colors hover:bg-muted/40">
        <CardHeader>
          <CardTitle>Taxes this year</CardTitle>
          <Landmark className="size-4 text-muted-foreground" aria-hidden />
        </CardHeader>
        <CardContent className="flex items-center gap-3">
          <p className="flex-1 text-sm">
            What you&apos;ll owe or get back at filing time, from your pay stubs and accounts, and what to change on your W-4.
          </p>
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        </CardContent>
      </Card>
    </Link>
  );
}

function dollars(cents: number | null | undefined) {
  return cents === null || cents === undefined ? "" : String(Math.round(cents / 100));
}

function About({
  open,
  onOpenChange,
  p,
  save,
  saving,
  askSavings = false,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  p: Linked;
  save: (body: Record<string, unknown>, done: string) => Promise<boolean>;
  saving: boolean;
  askSavings?: boolean;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Your details" description="Leave a number blank to let the app work it out.">
      <AboutForm p={p} save={save} saving={saving} askSavings={askSavings} onDone={() => onOpenChange(false)} />
    </Sheet>
  );
}

function AboutForm({
  p,
  save,
  saving,
  askSavings,
  onDone,
}: {
  p: Linked;
  save: (body: Record<string, unknown>, done: string) => Promise<boolean>;
  saving: boolean;
  askSavings: boolean;
  onDone: () => void;
}) {
  const i = p.inputs;
  // Mounted each time the sheet opens, so it starts from the saved numbers.
  const [birth, setBirth] = useState(i.birth_year ? String(i.birth_year) : "");
  const [savings, setSavings] = useState(i.savings_typed ? dollars(i.monthly_savings_cents) : "");
  const [levels, setLevels] = useState<Record<Level, string>>({
    lean: i.levels.lean.typed ? dollars(i.levels.lean.yearly_cents) : "",
    regular: i.levels.regular.typed ? dollars(i.levels.regular.yearly_cents) : "",
    fat: i.levels.fat.typed ? dollars(i.levels.fat.yearly_cents) : "",
  });
  const year = new Date().getFullYear();
  const birthNum = birth ? Number(birth) : null;
  const birthBad = birthNum !== null && (!Number.isInteger(birthNum) || birthNum < 1920 || birthNum > year - 10);
  const cents = (v: string) => (v.trim() === "" ? null : Math.round(Number(v.replace(/[$,\s]/g, "")) * 100));
  const bad = (v: string) => v.trim() !== "" && !(Number(v.replace(/[$,\s]/g, "")) >= 0);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (birthBad || bad(savings) || LEVELS.some((l) => bad(levels[l]))) return;
    const ok = await save(
      {
        birth_year: birthNum,
        savings_cents: cents(savings),
        spending: {
          lean: cents(levels.lean),
          regular: cents(levels.regular),
          fat: cents(levels.fat),
        },
      },
      "Plan updated.",
    );
    if (ok) onDone();
  };

  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-4 px-5 pt-2 pb-6">
      <Field
        id="birth"
        label="Year you were born"
        hint="Shows dates as ages. Stays on your server."
        error={birthBad ? "Enter a year like 1988." : null}
      >
        <Input inputMode="numeric" autoComplete="off" placeholder="1988" value={birth} onChange={(e) => setBirth(e.target.value)} />
      </Field>
      <Field
        id="savings"
        label="Saving each month"
        hint={
          i.measured_savings_cents !== null
            ? `The app works out ${formatMoney(i.measured_savings_cents)} a month. ${i.savings_source.note}`
            : "No pay or income seen yet to work it out from. Add a pay stub on the Budget page, or type a number."
        }
        error={bad(savings) ? "Enter an amount in dollars." : null}
      >
        <Input
          inputMode="decimal"
          autoComplete="off"
          placeholder={dollars(i.measured_savings_cents) || "2000"}
          value={savings}
          onChange={(e) => setSavings(e.target.value)}
          autoFocus={askSavings}
        />
      </Field>
      <fieldset className="grid grid-cols-1 gap-3">
        <legend className="mb-1 text-sm font-medium">Spending a year, once work is optional</legend>
        {LEVELS.map((l) => (
          <Field
            key={l}
            id={`level-${l}`}
            label={`${LEVEL_NAMES[l].title}: ${LEVEL_NAMES[l].body.toLowerCase()}`}
            error={bad(levels[l]) ? "Enter an amount in dollars." : null}
          >
            <Input
              inputMode="decimal"
              autoComplete="off"
              placeholder={dollars(i.levels[l].derived_cents)}
              value={levels[l]}
              onChange={(e) => setLevels({ ...levels, [l]: e.target.value })}
            />
          </Field>
        ))}
      </fieldset>
      <Button type="submit" size="lg" disabled={saving}>
        Save
      </Button>
    </form>
  );
}
