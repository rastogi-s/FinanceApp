"use client";

import { useState } from "react";
import { Money, formatMoney } from "@/components/money";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { parseDollars } from "@/lib/accounts";
import { useAuth } from "@/lib/auth";
import { type BudgetLine, type BudgetState, linesWith } from "@/lib/budget";
import { cn } from "@/lib/utils";

type Change = (path: string, method: string, body?: unknown) => Promise<BudgetState>;

function useDemo() {
  const { state } = useAuth();
  return state.status === "signed-in" && state.demo;
}

const dollars = (cents: number) => (cents / 100).toFixed(cents % 100 ? 2 : 0);

type LineProps = { budget: BudgetState; change: Change; onClose: () => void };

/** One category's limit: start from what it usually costs, nudge it, save. Also whether it's essential. */
export function LineSheet({ line, ...props }: LineProps & { line: BudgetLine | null }) {
  return line ? <LineEditor key={line.category_id} line={line} {...props} /> : null;
}

function LineEditor({ line, budget, change, onClose }: LineProps & { line: BudgetLine }) {
  const demo = useDemo();
  const [text, setText] = useState(() =>
    line.limit_cents ? dollars(line.limit_cents) : line.typical_cents ? dollars(line.typical_cents) : "",
  );
  const [busy, setBusy] = useState(false);
  const cents = parseDollars(text || "0");
  const inBudget = budget.lines.some((l) => l.category_id === line.category_id);
  const typical = line.typical_cents;
  const base = cents ?? line.limit_cents;

  const save = async (limit: number) => {
    if (demo) return toast("Changing the budget is off while the app shows sample data.");
    setBusy(true);
    try {
      await change("/api/budget", "PUT", { lines: linesWith(budget.lines, line.category_id, limit) });
      toast(limit > 0 ? `${line.category}: ${formatMoney(limit)} a month.` : `${line.category} is out of the budget.`);
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const essential = async (on: boolean) => {
    if (demo) return toast("Changing the budget is off while the app shows sample data.");
    try {
      await change(`/api/categories/${line.category_id}`, "PATCH", { essential: on });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const isEssential = budget.categories.find((c) => c.id === line.category_id)?.essential ?? line.essential;

  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={line.category}
      description={
        <>
          <Money cents={line.spent_cents} /> spent this month
          {typical > 0 && (
            <>
              {" "}
              · usually <Money cents={typical} />
            </>
          )}
        </>
      }
    >
      <form
        className="grid grid-cols-1 gap-5 pb-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (cents !== null) save(cents);
        }}
      >
        <Field
          id="limit"
          label="Monthly limit"
          error={cents === null ? "Type an amount, like 450 or 450.50." : null}
          hint={typical > 0 ? `The middle of the last ${budget.months_used} months was ${formatMoney(typical)}.` : undefined}
        >
          <Input inputMode="decimal" autoComplete="off" value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Quick amounts">
          {typical > 0 && <Quick onClick={() => setText(dollars(typical))}>Usual · {formatMoney(typical)}</Quick>}
          <Quick onClick={() => setText(dollars(Math.max(Math.round((base * 0.9) / 1000) * 1000, 0)))}>− 10%</Quick>
          <Quick onClick={() => setText(dollars(Math.round((base * 1.1) / 1000) * 1000))}>+ 10%</Quick>
        </div>
        <div className="rounded-2xl border p-4">
          <Switch
            checked={isEssential}
            onCheckedChange={essential}
            label="Essential"
            description="Counts toward your emergency fund: what you'd still pay if income stopped."
          />
        </div>
        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          {inBudget && (
            <Button type="button" variant="ghost" disabled={busy} onClick={() => save(0)}>
              Take out of the budget
            </Button>
          )}
          <Button type="submit" disabled={busy || cents === null}>
            {inBudget ? "Save" : "Add to the budget"}
          </Button>
        </div>
      </form>
    </Sheet>
  );
}

function Quick({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex h-9 items-center rounded-full border bg-card px-3.5 text-sm font-medium transition-colors hover:bg-muted"
    >
      {children}
    </button>
  );
}

/** Pick a category that isn't in the budget yet. */
export function AddSheet({
  open,
  budget,
  onClose,
  onPick,
}: {
  open: boolean;
  budget: BudgetState;
  onClose: () => void;
  onPick: (id: number) => void;
}) {
  const inBudget = new Set(budget.lines.map((l) => l.category_id));
  const left = budget.categories.filter((c) => !inBudget.has(c.id));
  return (
    <Sheet open={open} onOpenChange={(o) => !o && onClose()} title="Add a category" description="Then set its monthly limit.">
      <div className="grid grid-cols-1 gap-5 pb-2">
        {(["flexible", "fixed"] as const).map((kind) => {
          const items = left.filter((c) => c.kind === kind);
          if (!items.length) return null;
          return (
            <div key={kind}>
              <p className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                {kind === "fixed" ? "Bills" : "Everyday"}
              </p>
              <div className="flex flex-wrap gap-2">
                {items.map((c) => (
                  <Quick key={c.id} onClick={() => onPick(c.id)}>
                    {c.name}
                  </Quick>
                ))}
              </div>
            </div>
          );
        })}
        {left.length === 0 && <p className="text-sm text-muted-foreground">Every category already has a budget.</p>}
      </div>
    </Sheet>
  );
}

const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in the phone's time zone

/**
 * A pay stub in five numbers. It starts filled in from that person's last stub, so a regular paycheck is a date
 * check and one tap. Whatever gross pay leaves after taxes, savings and take-home pay (health insurance and the
 * like) is worked out, not typed.
 */
export function PaycheckSheet({
  open,
  budget,
  change,
  onClose,
}: {
  open: boolean;
  budget: BudgetState;
  change: Change;
  onClose: () => void;
}) {
  const { state } = useAuth();
  const me = state.status === "signed-in" ? state.user.id : null;
  const [person, setPerson] = useState<number | null>(null);
  const who = person ?? me ?? budget.people[0]?.id ?? null;
  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Add a pay stub"
      description="Five numbers from the stub. Amounts for this one paycheck, not year to date."
    >
      {open && <PaycheckForm key={who} who={who} setPerson={setPerson} budget={budget} change={change} onClose={onClose} />}
    </Sheet>
  );
}

function PaycheckForm({
  who,
  setPerson,
  budget,
  change,
  onClose,
}: {
  who: number | null;
  setPerson: (id: number) => void;
  budget: BudgetState;
  change: Change;
  onClose: () => void;
}) {
  const demo = useDemo();
  const [date, setDate] = useState(today);
  const [values, setValues] = useState(() => {
    const last = budget.paychecks.find((p) => p.user_id === who);
    return last
      ? {
          gross: dollars(last.gross_cents),
          taxes: dollars(last.taxes_cents),
          savings: dollars(last.savings_cents),
          net: dollars(last.net_cents),
          match: last.employer_match_cents ? dollars(last.employer_match_cents) : "",
        }
      : { gross: "", taxes: "", savings: "", net: "", match: "" };
  });
  const [busy, setBusy] = useState(false);

  const cents = {
    gross: parseDollars(values.gross),
    taxes: parseDollars(values.taxes || "0"),
    savings: parseDollars(values.savings || "0"),
    net: parseDollars(values.net),
    match: parseDollars(values.match || "0"),
  };
  const complete = Object.values(cents).every((v) => v !== null && v >= 0) && (cents.gross ?? 0) > 0;
  const other = complete ? cents.gross! - cents.taxes! - cents.savings! - cents.net! : null;

  const save = async () => {
    if (demo) return toast("Adding pay stubs is off while the app shows sample data.");
    if (!complete || other === null || other < 0) return;
    setBusy(true);
    try {
      await change("/api/paychecks", "POST", {
        user_id: who,
        date,
        gross_cents: cents.gross,
        taxes_cents: cents.taxes,
        savings_cents: cents.savings,
        net_cents: cents.net,
        employer_match_cents: cents.match,
      });
      toast("Pay stub saved. The savings rate now uses gross pay.");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const field = (key: keyof typeof values, label: string, hint?: string) => (
    <Field id={`pay-${key}`} label={label} hint={hint} error={values[key] && cents[key] === null ? "Type an amount, like 4,500.00." : null}>
      <Input
        inputMode="decimal"
        autoComplete="off"
        placeholder="0.00"
        value={values[key]}
        onChange={(e) => setValues((v) => ({ ...v, [key]: e.target.value }))}
      />
    </Field>
  );

  return (
    <form
      className="grid grid-cols-1 gap-4 pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <div className={cn("grid gap-4", budget.people.length > 1 ? "grid-cols-2" : "grid-cols-1")}>
        <Field id="pay-date" label="Pay date">
          <Input type="date" value={date} max={today()} onChange={(e) => setDate(e.target.value)} />
        </Field>
        {budget.people.length > 1 && (
          <Field id="pay-person" label="Whose pay">
            <Select value={who ?? ""} onChange={(e) => setPerson(Number(e.target.value))}>
              {budget.people.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </div>
      {field("gross", "Gross pay")}
      {field("taxes", "Taxes", "Federal, state, Social Security and Medicare together.")}
      {field("savings", "Saved before pay", "401(k), HSA and ESPP contributions.")}
      {field("net", "Take-home pay", "What landed in the bank.")}
      {field("match", "Employer match (optional)", "Only counted if you turn it on under the savings rate.")}
      {other !== null && (
        <p
          className={cn("rounded-xl px-4 py-3 text-sm", other < 0 ? "bg-negative/10 text-negative" : "bg-muted text-muted-foreground")}
          role="status"
        >
          {other < 0 ? (
            <>
              Taxes, savings and take-home pay come to <Money cents={-other} style="exact" /> more than gross pay. Check the numbers.
            </>
          ) : (
            <>
              Other deductions (health insurance and the like): <Money cents={other} style="exact" />
            </>
          )}
        </p>
      )}
      <Button type="submit" disabled={busy || !complete || (other ?? -1) < 0}>
        Save pay stub
      </Button>
    </form>
  );
}
