"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight, FileUp } from "lucide-react";
import { type AIOffer, AIOfferCard, shrinkPicture } from "@/components/ai-offer";
import { Money, formatMoney } from "@/components/money";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { parseDollars } from "@/lib/accounts";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { type BudgetLine, type BudgetState, type Frequency, linesWith } from "@/lib/budget";
import { monthOf } from "@/lib/search";
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
        {line.forecast && line.forecast.status !== "over" && <MonthEnd line={line} month={budget.month} />}
        <Link
          href={monthOf(line.category_id)}
          className="-my-1 flex items-center justify-between rounded-2xl bg-muted/60 px-4 py-3 text-sm font-medium transition-colors hover:bg-muted"
        >
          See this month&apos;s {line.category.toLowerCase()} transactions
          <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
        </Link>
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

const dayFormat = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

/** How the month-end number adds up: spent, bills still to come, and the everyday pace for the days left. */
function MonthEnd({ line, month }: { line: BudgetLine; month: string }) {
  const f = line.forecast!;
  const over = f.status === "heading_over";
  const days = `${f.days_left} day${f.days_left === 1 ? "" : "s"}`;
  return (
    <div className={cn("rounded-2xl border p-4 text-sm", over && "border-warning/40 bg-warning/5")}>
      <p className="font-medium">
        By month-end, about <Money cents={f.projected_cents} />
        {over && (
          <span className="text-warning">
            {" "}
            · <Money cents={f.over_cents} /> over
          </span>
        )}
      </p>
      <dl className="mt-3 grid grid-cols-1 gap-1.5 text-muted-foreground">
        <div className="flex justify-between gap-3">
          <dt>Spent so far</dt>
          <dd>
            <Money cents={line.spent_cents} />
          </dd>
        </div>
        {f.due.map((d) => (
          <div key={d.merchant} className="flex justify-between gap-3">
            <dt className="min-w-0 truncate">
              {d.merchant}, usually {dayFormat.format(new Date(`${month.slice(0, 8)}${String(d.day).padStart(2, "0")}`))}
            </dt>
            <dd>
              <Money cents={d.cents} signed />
            </dd>
          </div>
        ))}
        {f.pace_cents > 0 && (
          <div className="flex justify-between gap-3">
            <dt>{line.kind === "fixed" ? "The rest of a usual month" : `At your pace, ${days} more`}</dt>
            <dd>
              <Money cents={f.pace_cents} signed />
            </dd>
          </div>
        )}
      </dl>
    </div>
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
      description="Upload the PDF or a photo, or type five numbers from it. Amounts for this one paycheck, not year to date."
    >
      {open && <PaycheckForm key={who} who={who} setPerson={setPerson} budget={budget} change={change} onClose={onClose} />}
    </Sheet>
  );
}

const FREQUENCIES = [
  ["weekly", "Weekly"],
  ["biweekly", "Every 2 weeks"],
  ["semimonthly", "Twice a month"],
  ["monthly", "Monthly"],
] as const;
export const FREQUENCY_LABEL: Record<string, string> = Object.fromEntries(FREQUENCIES);

export type StubDetail = {
  this: Partial<Record<string, number>>;
  ytd: Partial<Record<string, number>>;
  deposits?: { mask: string; cents: number }[]; // where take-home was split to
};
const SPLIT = [
  ["federal", "Federal income tax"],
  ["state", "State income tax"],
  ["social_security", "Social Security"],
  ["medicare", "Medicare"],
  ["retirement", "Pre-tax 401(k)"],
  ["hsa", "HSA"],
] as const;
type SplitKey = (typeof SPLIT)[number][0];
const BLANK_SPLIT = Object.fromEntries(SPLIT.map(([k]) => [k, ""])) as Record<SplitKey, string>;

type Read = {
  date: string | null;
  period_start: string | null;
  period_end: string | null;
  frequency: Frequency | null;
  gross_cents: number | null;
  taxes_cents: number | null;
  savings_cents: number | null;
  net_cents: number | null;
  employer_match_cents: number | null;
  detail?: StubDetail;
  warnings: string[];
};
type ReadAnswer = Read | { ai_offer: AIOffer };

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
  const last = budget.paychecks.find((p) => p.user_id === who);
  const schedule = budget.schedules.find((s) => s.user_id === who);
  // The next payday on their schedule, once it has come; otherwise today.
  const [date, setDate] = useState(() => (schedule && schedule.next_date <= today() ? schedule.next_date : today()));
  const [frequency, setFrequency] = useState<Frequency | null>(last?.frequency ?? null);
  const [period, setPeriod] = useState<{ start: string | null; end: string | null }>({ start: null, end: null });
  const [values, setValues] = useState(() =>
    last
      ? {
          gross: dollars(last.gross_cents),
          taxes: dollars(last.taxes_cents),
          savings: dollars(last.savings_cents),
          net: dollars(last.net_cents),
          match: last.employer_match_cents ? dollars(last.employer_match_cents) : "",
        }
      : { gross: "", taxes: "", savings: "", net: "", match: "" },
  );
  // For the Taxes page: the stub's taxes by kind (typed or read), and its year-to-date column when read.
  const [split, setSplit] = useState<Record<SplitKey, string>>(BLANK_SPLIT);
  const [readDetail, setReadDetail] = useState<StubDetail | null>(null);
  const [splitOpen, setSplitOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const [read, setRead] = useState<{ name: string; warnings: string[] } | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const [offer, setOffer] = useState<{ file: File; offer: AIOffer } | null>(null);

  const upload = async (original: File, withAI = false) => {
    if (demo) return toast("Reading pay stubs is off while the app shows sample data.");
    setReading(true);
    setReadError(null);
    try {
      const file = await shrinkPicture(original);
      const form = new FormData();
      form.append("file", file);
      const answer = await api<ReadAnswer>(`/api/paychecks/read${withAI ? "?read_with_ai=true" : ""}`, { form });
      if ("ai_offer" in answer) {
        setOffer({ file, offer: answer.ai_offer });
        return;
      }
      setOffer(null);
      const r = answer;
      const amount = (c: number | null) => (c === null ? "" : c ? dollars(c) : "0");
      setValues({
        gross: amount(r.gross_cents),
        taxes: amount(r.taxes_cents),
        savings: amount(r.savings_cents),
        net: amount(r.net_cents),
        match: r.employer_match_cents ? dollars(r.employer_match_cents) : "",
      });
      setReadDetail(r.detail ?? null);
      setSplit(
        Object.fromEntries(SPLIT.map(([k]) => [k, r.detail?.this[k] !== undefined ? dollars(r.detail.this[k]!) : ""])) as Record<
          SplitKey,
          string
        >,
      );
      if (r.date) setDate(r.date);
      if (r.frequency) setFrequency(r.frequency);
      setPeriod({ start: r.period_start, end: r.period_end });
      setRead({ name: file.name, warnings: r.warnings });
    } catch (e) {
      setReadError((e as Error).message);
    } finally {
      setReading(false);
      if (input.current) input.current.value = "";
    }
  };

  const cents = {
    gross: parseDollars(values.gross),
    taxes: parseDollars(values.taxes || "0"),
    savings: parseDollars(values.savings || "0"),
    net: parseDollars(values.net),
    match: parseDollars(values.match || "0"),
  };
  const splitCents = Object.fromEntries(SPLIT.map(([k]) => [k, split[k].trim() ? parseDollars(split[k]) : undefined])) as Record<
    SplitKey,
    number | null | undefined
  >;
  const splitBad = Object.values(splitCents).some((v) => v === null || (v !== undefined && v < 0));
  const complete = Object.values(cents).every((v) => v !== null && v >= 0) && (cents.gross ?? 0) > 0 && !splitBad;
  const other = complete ? cents.gross! - cents.taxes! - cents.savings! - cents.net! : null;

  const save = async () => {
    if (demo) return toast("Adding pay stubs is off while the app shows sample data.");
    if (!complete || other === null || other < 0) return;
    setBusy(true);
    try {
      await change("/api/paychecks", "POST", {
        user_id: who,
        date,
        frequency,
        period_start: period.start,
        period_end: period.end,
        gross_cents: cents.gross,
        taxes_cents: cents.taxes,
        savings_cents: cents.savings,
        net_cents: cents.net,
        employer_match_cents: cents.match,
        detail: {
          this: { ...readDetail?.this, ...Object.fromEntries(Object.entries(splitCents).filter(([, v]) => v !== undefined)) },
          ytd: readDetail?.ytd ?? {},
          deposits: readDetail?.deposits ?? [],
        },
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
    <Field
      id={`pay-${key}`}
      label={label}
      hint={hint}
      error={
        values[key] && cents[key] === null
          ? "Type an amount, like 4,500.00."
          : read && key !== "match" && !values[key]
            ? "Not found on the stub. Type it in."
            : null
      }
    >
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
      <div className="rounded-2xl border border-dashed p-4">
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf,image/png,image/jpeg,image/webp"
          className="sr-only"
          id="stub-file"
          aria-label="Pay stub PDF or picture"
          tabIndex={-1}
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
        />
        <div className="flex items-center gap-3">
          <FileUp className="size-5 shrink-0 text-primary" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{read ? `Filled in from ${read.name}` : "Have the PDF or a photo? Upload it"}</p>
            <p className="text-xs text-muted-foreground">
              {read ? "Check the numbers against the stub, then save." : "The numbers fill in by themselves. The file isn't kept."}
            </p>
          </div>
          <Button type="button" size="sm" variant="secondary" disabled={reading} onClick={() => input.current?.click()}>
            {reading ? "Reading…" : read ? "Another" : "Upload"}
          </Button>
        </div>
        {offer && (
          <div className="mt-4">
            <AIOfferCard offer={offer.offer} busy={reading} onAccept={() => upload(offer.file, true)} onCancel={() => setOffer(null)} />
          </div>
        )}
        {readError && (
          <p className="mt-3 text-sm text-negative" role="alert">
            {readError}
          </p>
        )}
        {read && read.warnings.length > 0 && (
          <ul className="mt-3 grid gap-1 text-sm text-warning" role="status">
            {read.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        )}
      </div>
      <div className={cn("grid gap-4", budget.people.length > 1 ? "grid-cols-2" : "grid-cols-1")}>
        <Field id="pay-date" label="Pay date">
          <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
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
      <fieldset>
        <legend className="mb-2 text-sm font-medium">How often you&apos;re paid</legend>
        <div className="flex flex-wrap gap-2">
          {FREQUENCIES.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={frequency === value}
              onClick={() => setFrequency(frequency === value ? null : value)}
              className={cn(
                "inline-flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm font-medium transition-colors",
                frequency === value ? "border-transparent bg-primary text-primary-foreground" : "bg-card hover:bg-muted",
              )}
            >
              {frequency === value && <Check className="size-4" aria-hidden />}
              {label}
            </button>
          ))}
        </div>
      </fieldset>
      {field("gross", "Gross pay")}
      {field("taxes", "Taxes", "Federal, state, Social Security and Medicare together.")}
      {field("savings", "Saved before pay", "401(k), HSA and ESPP contributions.")}
      {field("net", "Take-home pay", "What landed in the bank.")}
      {field("match", "Employer match (optional)", "Only counted if you turn it on under the savings rate.")}
      <div className="rounded-2xl border">
        <button
          type="button"
          aria-expanded={splitOpen}
          onClick={() => setSplitOpen(!splitOpen)}
          className="flex w-full items-center gap-3 px-4 py-3 text-left"
        >
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-medium">Taxes by kind (optional)</span>
            <span className="block text-xs text-muted-foreground">
              {Object.values(splitCents).some((v) => v !== undefined)
                ? "Filled in. The Taxes page uses these exact numbers."
                : "Federal, state, Social Security, Medicare. Without them, the Taxes page splits the total by the usual rates."}
            </span>
          </span>
          <ChevronDown
            className={cn("size-4 shrink-0 text-muted-foreground transition-transform", splitOpen && "rotate-180")}
            aria-hidden
          />
        </button>
        {splitOpen && (
          <div className="grid grid-cols-2 gap-3 px-4 pb-4">
            {SPLIT.map(([key, label]) => (
              <Field key={key} id={`pay-split-${key}`} label={label} error={splitCents[key] === null ? "Type an amount." : null}>
                <Input
                  inputMode="decimal"
                  autoComplete="off"
                  placeholder="0.00"
                  value={split[key]}
                  onChange={(e) => setSplit((v) => ({ ...v, [key]: e.target.value }))}
                />
              </Field>
            ))}
          </div>
        )}
      </div>
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
