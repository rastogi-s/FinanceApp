"use client";

import { useMemo, useState } from "react";
import { Minus, Plus, RotateCcw, Trash2 } from "lucide-react";
import { Money, formatMoney } from "@/components/money";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Slider } from "@/components/ui/slider";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import {
  COLORS,
  type ColorName,
  type Goal,
  type GoalIn,
  type GoalsState,
  ICONS,
  type IconName,
  PRIORITY,
  addMonths,
  monthLong,
  monthsBetween,
  monthsTo,
  neededMonthly,
} from "@/lib/goals";
import { resetHousehold } from "@/lib/household";
import { cn } from "@/lib/utils";

type Use = "none" | "amount" | "rest";

/** Add or change a goal: what it's for, how much and by when, which money counts toward it, and a what-if for the
 * monthly amount. The emergency fund keeps its name; its target is months of essentials unless one is typed in. */
export function GoalSheet({
  open,
  onOpenChange,
  state,
  goal,
  suggestion,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  state: GoalsState;
  goal: Goal | null;
  suggestion?: { name: string; icon: IconName; color: ColorName };
  onSaved: (s: GoalsState) => void;
}) {
  const emergency = goal?.kind === "emergency";
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={goal ? goal.name : "New goal"}
      description={
        emergency
          ? "What you'd live on if income stopped. Pick accounts to count only those; otherwise it counts the cash no other goal has."
          : "Only the money you pick counts toward it, and it stops counting as your emergency fund."
      }
    >
      <GoalForm state={state} goal={goal} suggestion={suggestion} onSaved={onSaved} done={() => onOpenChange(false)} />
    </Sheet>
  );
}

const dollars = (cents: number | null | undefined) => (cents === null || cents === undefined ? "" : String(Math.round(cents / 100)));
const toCents = (v: string) => {
  const n = Number(v.replace(/[$,\s]/g, ""));
  return v.trim() === "" || !Number.isFinite(n) ? null : Math.round(n * 100);
};

function GoalForm({
  state,
  goal,
  suggestion,
  onSaved,
  done,
}: {
  state: GoalsState;
  goal: Goal | null;
  suggestion?: { name: string; icon: IconName; color: ColorName };
  onSaved: (s: GoalsState) => void;
  done: () => void;
}) {
  const emergency = goal?.kind === "emergency";
  const [name, setName] = useState(goal?.name ?? suggestion?.name ?? "");
  const [icon, setIcon] = useState<IconName>(goal?.icon ?? suggestion?.icon ?? "star");
  const [color, setColor] = useState<ColorName>(goal?.color ?? suggestion?.color ?? "teal");
  const [priority, setPriority] = useState<1 | 2 | 3>(goal?.priority ?? 2);
  const [target, setTarget] = useState(goal && (goal.target_typed || !emergency) ? dollars(goal.target_cents) : "");
  const [months, setMonths] = useState(state.emergency.months);
  const [date, setDate] = useState(goal?.target_date?.slice(0, 7) ?? "");
  const [rate, setRate] = useState(goal?.rate_typed ? String(goal.rate_percent) : "");
  const [uses, setUses] = useState<Record<number, { use: Use; amount: string }>>(() =>
    Object.fromEntries(
      state.accounts.map((a) => {
        const f = goal?.funding.find((x) => x.account_id === a.id);
        return [a.id, { use: f ? (f.cents === null ? "rest" : "amount") : "none", amount: f?.cents ? dollars(f.cents) : "" }];
      }),
    ),
  );
  const [typedMonthly, setTypedMonthly] = useState<number | null>(goal?.monthly_typed_cents ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The numbers as they'd be with what's in the form: an estimate until it's saved (the server shares balances out).
  const targetCents = emergency && !target.trim() ? months * state.emergency.monthly_essentials_cents : toCents(target);
  const saved = useMemo(() => {
    const picked = state.accounts.filter((a) => uses[a.id]?.use !== "none");
    if (emergency && picked.length === 0) return goal?.saved_cents ?? 0;
    return picked.reduce((sum, a) => {
      const mine = goal?.funding.find((f) => f.account_id === a.id)?.counted_cents ?? 0;
      const room = a.free_cents + mine;
      const u = uses[a.id]!;
      return sum + (u.use === "rest" ? room : Math.min(toCents(u.amount) ?? 0, a.balance_cents));
    }, 0);
  }, [state.accounts, uses, emergency, goal]);
  const ratePct = rate.trim() === "" ? state.default_rate : Number(rate);
  const monthsLeft = date ? monthsBetween(state.month, `${date}-01`) : null;
  const needed = targetCents && monthsLeft !== null ? neededMonthly(targetCents, saved, monthsLeft, ratePct) : null;
  const appMonthly = goal?.monthly_source && goal.monthly_source !== "typed" ? (goal.monthly_cents ?? 0) : (needed ?? 0);
  const monthly = typedMonthly ?? appMonthly;
  const reachIn = targetCents ? monthsTo(targetCents, saved, monthly, ratePct) : null;
  const sliderMax = Math.max(
    100_000,
    Math.ceil((Math.max(needed ?? 0, appMonthly, monthly, state.summary.monthly_savings_cents ?? 0) * 1.5) / 10_000) * 10_000,
  );

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (state.demo) return toast("Goals can't be changed while the app shows sample data.");
    if (!emergency && !name.trim()) return setError("Give the goal a name.");
    if (!emergency && !(targetCents && targetCents > 0)) return setError("How much is the goal? Enter an amount.");
    if (target.trim() && !(toCents(target)! > 0)) return setError("Enter the target in dollars.");
    if (rate.trim() && !(Number(rate) >= 0 && Number(rate) <= 15)) return setError("Enter a yearly rate between 0% and 15%.");
    const funding: GoalIn["funding"] = [];
    for (const a of state.accounts) {
      const u = uses[a.id]!;
      if (u.use === "rest") funding.push({ account_id: a.id, cents: null });
      if (u.use === "amount") {
        const c = toCents(u.amount);
        if (!c || c <= 0) return setError(`Enter how much of ${a.name} goes toward it.`);
        funding.push({ account_id: a.id, cents: c });
      }
    }
    const body: GoalIn = {
      name: emergency ? goal!.name : name.trim(),
      icon,
      color,
      priority,
      target_cents: target.trim() ? toCents(target) : emergency ? null : targetCents,
      target_date: date ? `${date}-01` : null,
      rate_percent: rate.trim() ? Number(rate) : null,
      monthly_cents: typedMonthly,
      funding,
      ...(emergency ? { emergency_months: months } : {}),
    };
    setBusy(true);
    setError(null);
    try {
      const next = await api<GoalsState>(goal ? `/api/goals/${goal.id}` : "/api/goals", { method: goal ? "PUT" : "POST", body });
      onSaved(next);
      resetHousehold();
      toast(goal ? "Goal saved." : "Goal added.");
      done();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!goal) return;
    if (state.demo) return toast("Goals can't be changed while the app shows sample data.");
    setBusy(true);
    try {
      onSaved(await api<GoalsState>(`/api/goals/${goal.id}`, { method: "DELETE" }));
      resetHousehold();
      toast(`${goal.name} removed. Its money counts as your emergency fund again.`);
      done();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reach = reachIn === null ? null : addMonths(state.month, reachIn);
  const late = reach && date ? reach > new Date(`${date}-01T00:00:00Z`) : false;

  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-5 px-5 pt-2 pb-6">
      {!emergency && (
        <>
          <Field id="goal-name" label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder="House down payment" autoComplete="off" />
          </Field>
          <div className="grid grid-cols-1 gap-2">
            <p className="text-sm font-medium" id="goal-icon-label">
              Icon and color
            </p>
            <div role="radiogroup" aria-labelledby="goal-icon-label" className="grid grid-cols-6 gap-2">
              {(Object.keys(ICONS) as IconName[]).map((k) => {
                const Icon = ICONS[k];
                return (
                  <button
                    key={k}
                    type="button"
                    role="radio"
                    aria-checked={icon === k}
                    aria-label={k}
                    onClick={() => setIcon(k)}
                    className={cn(
                      "grid aspect-square place-items-center rounded-2xl border transition-colors",
                      icon === k ? cn("border-transparent ring-2 ring-primary", COLORS[color].tile) : "hover:bg-muted",
                    )}
                  >
                    <Icon className="size-5" aria-hidden />
                  </button>
                );
              })}
            </div>
            <div role="radiogroup" aria-label="Color" className="flex gap-3 pt-1">
              {(Object.keys(COLORS) as ColorName[]).map((c) => (
                <button
                  key={c}
                  type="button"
                  role="radio"
                  aria-checked={color === c}
                  aria-label={c}
                  onClick={() => setColor(c)}
                  className={cn(
                    "size-8 rounded-full ring-offset-2 ring-offset-card transition",
                    COLORS[c].dot,
                    color === c && "ring-2 ring-foreground",
                  )}
                />
              ))}
            </div>
          </div>
        </>
      )}

      {emergency && (
        <div className="grid grid-cols-1 gap-1.5">
          <div className="flex items-center justify-between gap-3 rounded-2xl border px-4 py-2.5">
            <span className="text-sm font-medium">Months of essentials</span>
            <div className="flex items-center gap-1">
              <Button type="button" variant="ghost" size="icon" aria-label="One month fewer" disabled={months <= 1} onClick={() => setMonths(months - 1)}>
                <Minus />
              </Button>
              <span className="w-20 text-center text-sm" aria-live="polite">
                {months} months
              </span>
              <Button type="button" variant="ghost" size="icon" aria-label="One month more" disabled={months >= 36} onClick={() => setMonths(months + 1)}>
                <Plus />
              </Button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {formatMoney(state.emergency.monthly_essentials_cents)} a month of bills and essentials, from the budget.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field
          id="goal-target"
          label={emergency ? "Or a set amount" : "Target"}
          hint={emergency && targetCents ? `Target: ${formatMoney(targetCents)}` : undefined}
        >
          <Input
            inputMode="decimal"
            autoComplete="off"
            placeholder={emergency ? dollars(months * state.emergency.monthly_essentials_cents) : "60000"}
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
        </Field>
        <Field id="goal-date" label="By (optional)">
          <Input type="month" value={date} min={state.month.slice(0, 7)} onChange={(e) => setDate(e.target.value)} />
        </Field>
      </div>

      <div className="grid grid-cols-1 gap-2">
        <p className="text-sm font-medium" id="goal-priority-label">
          Priority
        </p>
        <div role="radiogroup" aria-labelledby="goal-priority-label" className="grid grid-cols-3 gap-2">
          {([1, 2, 3] as const).map((p) => (
            <button
              key={p}
              type="button"
              role="radio"
              aria-checked={priority === p}
              onClick={() => setPriority(p)}
              className={cn(
                "rounded-xl border px-2.5 py-2 text-sm font-medium transition-colors",
                priority === p ? "border-primary bg-accent text-accent-foreground" : "hover:bg-muted",
              )}
            >
              {PRIORITY[p]}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">Higher priority goals get your savings first, and come first when an account is short.</p>
      </div>

      <fieldset className="grid grid-cols-1 gap-2">
        <legend className="mb-1 text-sm font-medium">Money toward it</legend>
        {state.accounts.length === 0 && <p className="text-sm text-muted-foreground">Link a checking, savings or brokerage account to set money aside.</p>}
        {state.accounts.map((a) => {
          const u = uses[a.id]!;
          const restTaken = a.rest_goal_id !== null && a.rest_goal_id !== goal?.id;
          return (
            <div key={a.id} className={cn("grid grid-cols-1 gap-2 rounded-2xl border px-4 py-3", u.use !== "none" && "border-primary/40 bg-accent/30")}>
              <div className="flex items-baseline justify-between gap-3">
                <p className="min-w-0 truncate text-sm font-medium">{a.name}</p>
                <Money cents={a.balance_cents} className="shrink-0 text-sm" />
              </div>
              {a.free_cents < a.balance_cents && (
                <p className="-mt-1.5 text-xs text-muted-foreground">
                  <Money cents={a.free_cents} /> of it isn&apos;t in a goal
                </p>
              )}
              <div className="grid grid-cols-2 gap-2">
                <Select
                  aria-label={`${a.name}: how much goes toward ${name || "this goal"}`}
                  value={u.use}
                  onChange={(e) => setUses({ ...uses, [a.id]: { ...u, use: e.target.value as Use } })}
                >
                  <option value="none">Not used</option>
                  <option value="amount">An amount</option>
                  <option value="rest" disabled={restTaken}>
                    {restTaken ? "The rest (taken)" : "The rest of it"}
                  </option>
                </Select>
                {u.use === "amount" && (
                  <Input
                    inputMode="decimal"
                    autoComplete="off"
                    aria-label={`Amount of ${a.name}`}
                    placeholder={dollars(a.free_cents) || "10000"}
                    value={u.amount}
                    onChange={(e) => setUses({ ...uses, [a.id]: { ...u, amount: e.target.value } })}
                  />
                )}
              </div>
            </div>
          );
        })}
      </fieldset>

      <div className="grid grid-cols-1 gap-4 rounded-2xl bg-muted/60 px-4 py-4">
        <p className="text-sm">
          <span className="font-semibold">
            <Money cents={saved} />
          </span>{" "}
          <span className="text-muted-foreground">
            set aside{targetCents ? <> of <Money cents={targetCents} /></> : null}
            {needed !== null && date && (
              <>
                {" "}
                · needs <Money cents={needed} className="font-medium text-foreground" /> a month to make{" "}
                {monthLong.format(new Date(`${date}-01T00:00:00Z`))}
              </>
            )}
          </span>
        </p>
        <Slider
          label="Put in each month"
          value={monthly}
          display={<Money cents={monthly} />}
          min={0}
          max={sliderMax}
          step={2_500}
          onChange={setTypedMonthly}
          hint={
            <>
              {targetCents && saved >= targetCents ? (
                "It's all there already."
              ) : reach ? (
                <span className={cn(late && "font-medium text-warning")}>
                  Gets there around {monthLong.format(reach)}
                  {late ? ", after the date you picked." : "."}
                </span>
              ) : (
                "At this pace it doesn't get there within 60 years."
              )}{" "}
              {typedMonthly === null
                ? goal?.monthly_source === "savings"
                  ? "This is the goal's share of what you save each month."
                  : "Move it to try another amount; saving keeps it."
                : "You set this amount."}
              {typedMonthly !== null && (
                <button type="button" className="ml-1 inline-flex items-center gap-1 font-medium text-primary" onClick={() => setTypedMonthly(null)}>
                  <RotateCcw className="size-3" aria-hidden /> Let the app work it out
                </button>
              )}
            </>
          }
        />
        <Field id="goal-rate" label="Earns a year (%)" hint={`Left blank: ${state.default_rate}%, a typical high-yield savings rate.`}>
          <Input inputMode="decimal" autoComplete="off" placeholder={String(state.default_rate)} value={rate} onChange={(e) => setRate(e.target.value)} />
        </Field>
      </div>

      {error && (
        <p className="text-sm font-medium text-negative" role="alert">
          {error}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" size="lg" disabled={busy} className="flex-1">
          {goal ? "Save" : "Add goal"}
        </Button>
        {goal && !emergency && (
          <Button type="button" size="lg" variant="ghost" disabled={busy} onClick={remove} aria-label={`Remove ${goal.name}`}>
            <Trash2 /> Remove
          </Button>
        )}
      </div>
    </form>
  );
}
