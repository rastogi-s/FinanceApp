"use client";

import Link from "next/link";
import { useState } from "react";
import { CalendarClock, Check, ChevronRight, ExternalLink, FileText, Landmark, Pencil, ReceiptText, RotateCcw } from "lucide-react";
import { HsaCard } from "@/components/hsa-card";
import { Money, formatMoney } from "@/components/money";
import { BackLink, Column, Columns, Notice, PageTitle, Wide } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { parseDollars } from "@/lib/accounts";
import { api } from "@/lib/api";
import { useApi } from "@/lib/household";
import { DEDUCTIONS, INCOME, PAID, type FilingStatus, type TaxItem, type TaxProfile, type TaxState, percent } from "@/lib/taxes";
import { cn } from "@/lib/utils";

const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

export default function Taxes() {
  const { data, error } = useApi<TaxState>("/api/taxes");
  const [local, setLocal] = useState<TaxState | null>(null);
  const t = local ?? data;
  return (
    <>
      <Wide />
      <BackLink href="/plan">Plan</BackLink>
      <PageTitle
        title="Taxes"
        subtitle={`A rough estimate for ${t?.year ?? "this year"} from your pay stubs and accounts, with every number open to change. Not tax advice.`}
      />
      {error && !t && (
        <Notice icon={<Landmark className="size-5" />} title="Couldn't load the estimate">
          {error}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {!t && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-56 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      )}
      {t && <TaxView t={t} onSaved={setLocal} />}
    </>
  );
}

type Save = (body: Record<string, unknown>, done: string) => Promise<boolean>;

function TaxView({ t, onSaved }: { t: TaxState; onSaved: (t: TaxState) => void }) {
  const [editing, setEditing] = useState<TaxItem | null>(null);
  const [answersOpen, setAnswersOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const items = Object.fromEntries(t.items.map((i) => [i.key, i]));
  const pick = (keys: string[]) => keys.map((k) => items[k]).filter(Boolean) as TaxItem[];

  const save: Save = async (body, done) => {
    if (t.demo) {
      toast("Taxes can't be changed while the app shows sample data.");
      return false;
    }
    setSaving(true);
    try {
      onSaved(await api<TaxState>("/api/taxes/settings", { method: "PATCH", body }));
      toast(done);
      return true;
    } catch (e) {
      toast((e as Error).message, "error");
      return false;
    } finally {
      setSaving(false);
    }
  };

  // Laptop: the answer and what to do about it on the left, the line-by-line math on the right.
  return (
    <Columns>
      <Column>
        {!t.answered && <FirstQuestions t={t} save={save} saving={saving} />}
        <Bottom t={t} />
        <Hints t={t} />
        <HsaCard h={t.hsa} busy={saving} onCoverage={(c) => save({ hsa_coverage: c }, c === "none" ? "Noted: no HSA." : "Coverage saved.")} />
        <Answers t={t} onEdit={() => setAnswersOpen(true)} />
      </Column>
      <Column>
        <ItemCard title="Income" icon={<ReceiptText className="size-4 text-muted-foreground" aria-hidden />} items={pick(INCOME)} onEdit={setEditing}>
          {t.people.length === 0 && (
            <p className="mb-2 text-sm text-muted-foreground">
              No pay stubs yet.{" "}
              <Link href="/budget#pay" className="font-medium text-primary">
                Add one on Budget
              </Link>{" "}
              and pay, withholding and pre-tax savings fill in by themselves.
            </p>
          )}
          <p className="mb-2 text-sm text-muted-foreground">
            Interest, dividends and fund gain distributions come from{" "}
            <Link href="/portfolio/income" className="font-medium text-primary">
              Dividends &amp; interest
            </Link>
            .
          </p>
        </ItemCard>
        {pick(DEDUCTIONS).length > 0 && <ItemCard title="Deductions and credits" items={pick(DEDUCTIONS)} onEdit={setEditing} />}
        <Federal t={t} />
        <Fica t={t} />
        <StateCard t={t} />
        <ItemCard title="Already paid" icon={<Check className="size-4 text-muted-foreground" aria-hidden />} items={pick(PAID)} onEdit={setEditing}>
          {t.people.map((p) => (
            <p key={p.name} className="mb-2 text-sm text-muted-foreground">
              {t.people.length > 1 && <span className="font-medium text-foreground">{p.name}: </span>}
              Latest stub {day.format(new Date(p.latest))}, {p.paychecks_left} paycheck{p.paychecks_left === 1 ? "" : "s"} left this year.
              {p.split_estimated && " Some stubs only had total taxes, so they're split by the usual rates."}
            </p>
          ))}
        </ItemCard>
        <Assumptions t={t} />
      </Column>
      <AmountSheet item={editing} onClose={() => setEditing(null)} save={save} saving={saving} />
      <AnswersSheet t={t} open={answersOpen} onOpenChange={setAnswersOpen} save={save} saving={saving} />
    </Columns>
  );
}

// ---- the answer

function Owe({ cents, className }: { cents: number; className?: string }) {
  // Under $5 either way is "about even": withholding rounds by the paycheck.
  if (Math.abs(cents) < 500) return <span className={className}>About even</span>;
  return (
    <span className={cn(cents > 0 ? "text-negative" : "text-positive", className)}>
      {cents > 0 ? "Owe " : "Back "}
      <Money cents={Math.abs(cents)} />
    </span>
  );
}

function Bottom({ t }: { t: TaxState }) {
  const b = t.total.balance_cents;
  const rows = [
    { name: "Federal income tax", tax: t.federal.tax_cents, paid: t.federal.paid_cents, balance: t.federal.balance_cents },
    { name: "Social Security and Medicare", tax: t.fica.due_cents, paid: t.fica.paid_cents, balance: t.fica.balance_cents },
    { name: t.state.name, tax: t.state.tax_cents, paid: t.state.paid_cents, balance: t.state.balance_cents },
  ];
  return (
    <Card className="overflow-hidden">
      <CardHeader>
        <CardTitle>When you file for {t.year}</CardTitle>
        <Badge variant="muted">Estimate</Badge>
      </CardHeader>
      <CardContent>
        {Math.abs(b) < 500 ? (
          <p className="text-4xl font-semibold tracking-tight">About even</p>
        ) : (
          <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className={cn("text-4xl font-semibold tracking-tight", b > 0 ? "text-negative" : "text-positive")}>
              <Money cents={Math.abs(b)} />
            </span>
            <span className="text-lg font-medium text-muted-foreground">{b > 0 ? "to pay" : "back as a refund"}</span>
          </p>
        )}
        <p className="mt-1.5 text-sm text-muted-foreground">
          About <Money cents={t.total.tax_cents} /> in tax for the year, and <Money cents={t.total.paid_cents} /> paid by the end of it
          at today&apos;s withholding.
        </p>
        <ul className="mt-5 grid grid-cols-1 gap-3">
          {rows.map((r) => (
            <li key={r.name}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="font-medium">{r.name}</span>
                <Owe cents={r.balance} className="shrink-0 font-medium tabular-nums" />
              </div>
              <PaidBar tax={r.tax} paid={r.paid} label={r.name} />
              <p className="mt-1 text-xs text-muted-foreground tabular-nums">
                <Money cents={r.paid} /> paid of <Money cents={r.tax} />
              </p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function PaidBar({ tax, paid, label }: { tax: number; paid: number; label: string }) {
  const top = Math.max(tax, paid, 1);
  return (
    <div className="relative mt-1.5 h-2 overflow-hidden rounded-full bg-muted" role="img" aria-label={`${label}: ${formatMoney(paid)} paid of ${formatMoney(tax)}`}>
      <div className="absolute inset-y-0 left-0 rounded-full bg-primary" style={{ width: `${(Math.min(paid, tax) / top) * 100}%` }} />
      {paid > tax && (
        <div
          className="absolute inset-y-0 rounded-r-full bg-positive"
          style={{ left: `${(tax / top) * 100}%`, width: `${((paid - tax) / top) * 100}%` }}
        />
      )}
      {tax > paid && (
        <div
          className="absolute inset-y-0 rounded-r-full bg-negative/35"
          style={{ left: `${(paid / top) * 100}%`, width: `${((tax - paid) / top) * 100}%` }}
        />
      )}
    </div>
  );
}

function Hints({ t }: { t: TaxState }) {
  const h = t.hints;
  const lines: { key: string; title: string; body: React.ReactNode }[] = [];
  const left = h.paychecks_left;
  if (h.federal?.kind === "more") {
    lines.push({
      key: "w4",
      title: `Withhold about ${formatMoney(h.federal.per_check_cents)} more per paycheck`,
      body: (
        <>
          On a new W-4, put it in Step 4(c), extra withholding. Over your {left} paycheck{left === 1 ? "" : "s"} left, that covers the
          federal balance. Set it back to zero in January.
        </>
      ),
    });
  } else if (h.federal?.kind === "less") {
    lines.push({
      key: "w4",
      title: `You could keep about ${formatMoney(h.federal.per_check_cents)} more per paycheck`,
      body: (
        <>
          You&apos;re on track for a federal refund. A W-4 with about <Money cents={h.federal.deductions_cents ?? 0} /> in Step 4(b),
          deductions, lowers withholding by about that much for the {left} paycheck{left === 1 ? "" : "s"} left. Or keep it as a forced
          saving; a refund isn&apos;t a penalty.
        </>
      ),
    });
  }
  if (h.state) {
    const form = t.state.code === "NC" ? "NC-4" : "your state's withholding form";
    lines.push({
      key: "state",
      title:
        h.state.kind === "more"
          ? `${t.state.name}: about ${formatMoney(h.state.per_check_cents)} more per paycheck`
          : `${t.state.name}: about ${formatMoney(h.state.per_check_cents)} less per paycheck would still cover it`,
      body:
        h.state.kind === "more" ? (
          <>Add it as additional withholding per pay period on {form}.</>
        ) : (
          <>{t.state.name} is withholding a little more than the estimate needs.</>
        ),
    });
  }
  const q = h.quarterly;
  if (q) {
    const dates = q.due_dates.map((d) => day.format(new Date(d))).join(", ");
    lines.push({
      key: "quarterly",
      title: q.federal_needed ? "An estimated payment would avoid a penalty" : `${t.state.name} estimated payment`,
      body: (
        <>
          {q.federal_needed && (
            <>
              To be safe from the underpayment penalty, the year needs <Money cents={q.required_cents} /> paid
              {q.uses_last_year ? " (the smaller of 90% of this year and last year's safe harbor)" : " (90% of this year's tax)"}, which is{" "}
              <Money cents={q.federal_shortfall_cents} /> more.{" "}
              {dates ? (
                <>
                  Pay it with Form 1040-ES by {dates}, or raise withholding: withholding counts as paid evenly through the year, so it can
                  also cover earlier quarters.
                </>
              ) : (
                <>Raise withholding for what&apos;s left of the year; withholding counts as paid evenly through the year.</>
              )}{" "}
              {!q.uses_last_year && "Adding last year's tax under Already paid may lower this."}
            </>
          )}
          {q.state_needed && (
            <>
              {q.federal_needed && " "}
              {t.state.name} expects estimated payments when you&apos;d owe <Money cents={100_000} /> or more after withholding (here{" "}
              <Money cents={q.state_cents} />){t.state.code === "NC" ? ", on Form NC-40" : ""}.
            </>
          )}
        </>
      ),
    });
  }
  if (!lines.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>What you could do</CardTitle>
        <CalendarClock className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent>
        <ul className="grid grid-cols-1 gap-4">
          {lines.map((l) => (
            <li key={l.key}>
              <p className="font-medium">{l.title}</p>
              <p className="mt-1 text-sm text-muted-foreground">{l.body}</p>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

// ---- the answers

function FirstQuestions({ t, save, saving }: { t: TaxState; save: Save; saving: boolean }) {
  const [status, setStatus] = useState<FilingStatus | null>(null);
  const [state, setState] = useState(t.profile.state);
  return (
    <Card className="border-primary/40">
      <CardHeader>
        <CardTitle className="text-base font-semibold text-foreground">Two questions first</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        <StatusChips t={t} value={status} onChange={setStatus} />
        <Field id="first-state" label="State you live in" hint={state === "NC" ? "North Carolina (Durham). Change it if that's not right." : undefined}>
          <Select value={state} onChange={(e) => setState(e.target.value)}>
            {Object.entries(t.states).map(([code, name]) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </Select>
        </Field>
        <p className="text-sm text-muted-foreground">
          The rest (deductions, children, a spouse&apos;s pay, investment gains) is under Your answers and Income below.
        </p>
        <Button disabled={!status || saving} onClick={() => save({ filing_status: status, state }, "Saved. The estimate uses your answers now.")}>
          Save
        </Button>
      </CardContent>
    </Card>
  );
}

function StatusChips({ t, value, onChange }: { t: TaxState; value: FilingStatus | null; onChange: (s: FilingStatus) => void }) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium">How you file</legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {(Object.entries(t.statuses) as [FilingStatus, string][]).map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={value === key}
            onClick={() => onChange(key)}
            className={cn(
              "flex h-11 items-center gap-2 rounded-xl border px-3.5 text-left text-sm font-medium transition-colors",
              value === key ? "border-transparent bg-primary text-primary-foreground" : "bg-card hover:bg-muted",
            )}
          >
            {value === key && <Check className="size-4 shrink-0" aria-hidden />}
            {label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function Answers({ t, onEdit }: { t: TaxState; onEdit: () => void }) {
  const p = t.profile;
  const rows: [string, React.ReactNode][] = [
    ["Filing status", p.filing_status ? t.statuses[p.filing_status] : "Not set (single for now)"],
    ["State", t.state.name],
    [
      "Federal deduction",
      t.federal.itemizing ? (
        <>
          Itemized, <Money cents={t.federal.deduction_cents} />
        </>
      ) : (
        <>
          Standard, <Money cents={t.federal.standard_cents} />
        </>
      ),
    ],
    ["Children under 17", String(p.children)],
  ];
  if (p.other_dependents) rows.push(["Other dependents", String(p.other_dependents)]);
  if (t.state.supported && t.state.deduction_cents !== undefined)
    rows.splice(3, 0, [
      `${t.state.name} deduction`,
      <>
        {t.state.itemizing ? "Itemized" : "Standard"}, <Money cents={t.state.deduction_cents} />
      </>,
    ]);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your answers</CardTitle>
        <Button variant="ghost" size="sm" onClick={onEdit} className="-mt-1.5 -mr-2">
          <Pencil /> Change
        </Button>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-2 text-sm">
          {rows.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="text-right font-medium">{v}</dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function AnswersSheet({
  t,
  open,
  onOpenChange,
  save,
  saving,
}: {
  t: TaxState;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  save: Save;
  saving: boolean;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Your answers" description="How you file and who counts. Every number below follows them.">
      <AnswersForm t={t} save={save} saving={saving} onDone={() => onOpenChange(false)} />
    </Sheet>
  );
}

function AnswersForm({ t, save, saving, onDone }: { t: TaxState; save: Save; saving: boolean; onDone: () => void }) {
  // Mounted each time the sheet opens, so it starts from the saved answers.
  const [p, setP] = useState<TaxProfile>(t.profile);
  const [rate, setRate] = useState(t.profile.state_rate === null ? "" : String(t.profile.state_rate));
  const [share, setShare] = useState(String(t.profile.qualified_share));
  const hasTable = t.states_with_tables.includes(p.state);
  const noTax = t.no_wage_tax.includes(p.state);
  const rateNum = rate.trim() === "" ? null : Number(rate);
  const rateBad = rateNum !== null && !(rateNum >= 0 && rateNum <= 15);
  const shareNum = Number(share);
  const shareBad = !(Number.isInteger(shareNum) && shareNum >= 0 && shareNum <= 100);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (rateBad || shareBad) return;
    const ok = await save(
      {
        filing_status: p.filing_status,
        state: p.state,
        state_rate: hasTable || noTax ? undefined : rateNum,
        deduction: p.deduction,
        state_deduction: p.state_deduction,
        children: p.children,
        other_dependents: p.other_dependents,
        qualified_share: shareNum,
      },
      "Answers saved.",
    );
    if (ok) onDone();
  };

  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-5 pb-4">
      <StatusChips t={t} value={p.filing_status} onChange={(s) => setP({ ...p, filing_status: s })} />
      {p.filing_status === "mfj" && (
        <p className="-mt-2 text-sm text-muted-foreground">
          Both of you count: pay stubs of everyone in the app, and a spouse&apos;s pay from a job not in the app under Income.
        </p>
      )}
      <Field id="tax-state" label="State you live in">
        <Select value={p.state} onChange={(e) => setP({ ...p, state: e.target.value })}>
          {Object.entries(t.states).map(([code, name]) => (
            <option key={code} value={code}>
              {name}
            </option>
          ))}
        </Select>
      </Field>
      {!hasTable && !noTax && (
        <Field
          id="tax-state-rate"
          label="State tax rate (%)"
          hint="The app has tables for North Carolina only. A flat rate on income less the state deduction under Deductions works as a rough guess."
          error={rateBad ? "Enter a rate between 0 and 15." : null}
        >
          <Input inputMode="decimal" autoComplete="off" placeholder="5" value={rate} onChange={(e) => setRate(e.target.value)} />
        </Field>
      )}
      <Toggle
        legend="Federal deduction"
        value={p.deduction}
        options={[
          ["standard", `Standard, ${formatMoney(t.federal.standard_cents)}`],
          ["itemized", "Itemized, my own amount"],
        ]}
        onChange={(v) => setP({ ...p, deduction: v as TaxProfile["deduction"] })}
      />
      {hasTable && (
        <Toggle
          legend={`${t.state.code === p.state ? t.state.name : t.states[p.state]} deduction`}
          value={p.state_deduction}
          options={[
            ["standard", "Standard"],
            ["itemized", "Itemized, my own amount"],
          ]}
          onChange={(v) => setP({ ...p, state_deduction: v as TaxProfile["state_deduction"] })}
        />
      )}
      {(p.deduction === "itemized" || (hasTable && p.state_deduction === "itemized")) && (
        <p className="-mt-2 text-sm text-muted-foreground">Type the amount under Deductions and credits once this is saved.</p>
      )}
      <div className="grid grid-cols-2 gap-4">
        <Stepper id="children" label="Children under 17" value={p.children} onChange={(n) => setP({ ...p, children: n })} />
        <Stepper id="dependents" label="Other dependents" value={p.other_dependents} onChange={(n) => setP({ ...p, other_dependents: n })} />
      </div>
      <Field
        id="tax-qualified"
        label="Dividends that are qualified (%)"
        hint="Used only when the app hasn't seen dividends to sort. Most US stock fund dividends are qualified; your 1099-DIV, box 1b, has the real number."
        error={shareBad ? "Enter a whole number from 0 to 100." : null}
      >
        <Input inputMode="numeric" autoComplete="off" value={share} onChange={(e) => setShare(e.target.value)} />
      </Field>
      <Button type="submit" size="lg" disabled={saving}>
        Save
      </Button>
    </form>
  );
}

function Toggle({
  legend,
  value,
  options,
  onChange,
}: {
  legend: string;
  value: string;
  options: [string, string][];
  onChange: (v: string) => void;
}) {
  return (
    <fieldset>
      <legend className="mb-2 text-sm font-medium">{legend}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map(([v, label]) => (
          <button
            key={v}
            type="button"
            aria-pressed={value === v}
            onClick={() => onChange(v)}
            className={cn(
              "inline-flex h-9 items-center gap-1.5 rounded-full border px-3.5 text-sm font-medium transition-colors",
              value === v ? "border-transparent bg-primary text-primary-foreground" : "bg-card hover:bg-muted",
            )}
          >
            {value === v && <Check className="size-4" aria-hidden />}
            {label}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

function Stepper({ id, label, value, onChange }: { id: string; label: string; value: number; onChange: (n: number) => void }) {
  return (
    <div className="grid gap-1.5">
      <span id={`${id}-label`} className="text-sm font-medium">
        {label}
      </span>
      <div className="flex h-12 items-center justify-between rounded-xl border px-1.5 md:h-11" role="group" aria-labelledby={`${id}-label`}>
        <Button type="button" variant="ghost" size="icon" className="size-9" aria-label={`Fewer ${label.toLowerCase()}`} disabled={value <= 0} onClick={() => onChange(value - 1)}>
          −
        </Button>
        <span className="font-medium tabular-nums" aria-live="polite">
          {value}
        </span>
        <Button type="button" variant="ghost" size="icon" className="size-9" aria-label={`More ${label.toLowerCase()}`} disabled={value >= 20} onClick={() => onChange(value + 1)}>
          +
        </Button>
      </div>
    </div>
  );
}

// ---- money numbers, each open to change

function ItemCard({
  title,
  icon,
  items,
  onEdit,
  children,
}: {
  title: string;
  icon?: React.ReactNode;
  items: TaxItem[];
  onEdit: (i: TaxItem) => void;
  children?: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        {children}
        <ul className="-mx-2 grid grid-cols-1">
          {items.map((i) => (
            <li key={i.key}>
              <button
                type="button"
                onClick={() => onEdit(i)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg px-2 py-2.5 text-left transition-colors hover:bg-muted/60",
                  i.key === "qualified_dividends" && "pl-6",
                )}
              >
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
                    {i.label}
                    {i.typed && <Badge variant="default">Typed in</Badge>}
                  </span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">{i.note}</span>
                </span>
                <span className={cn("shrink-0 text-sm font-medium tabular-nums", i.cents === 0 && "text-muted-foreground")}>
                  <Money cents={i.cents} />
                </span>
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function AmountSheet({ item, onClose, save, saving }: { item: TaxItem | null; onClose: () => void; save: Save; saving: boolean }) {
  return (
    <Sheet open={item !== null} onOpenChange={(o) => !o && onClose()} title={item?.label ?? ""} description={item?.note}>
      {item && <AmountForm key={item.key} item={item} save={save} saving={saving} onDone={onClose} />}
    </Sheet>
  );
}

function AmountForm({ item, save, saving, onDone }: { item: TaxItem; save: Save; saving: boolean; onDone: () => void }) {
  const [text, setText] = useState(String(item.cents / 100));
  const cents = parseDollars(text);
  const bad = cents === null || (!item.negative_ok && cents < 0);
  const derived = item.derived_cents;
  return (
    <form
      className="grid grid-cols-1 gap-4 pb-4"
      onSubmit={async (e) => {
        e.preventDefault();
        if (bad) return;
        if (await save({ amounts: { [item.key]: cents } }, `${item.label}: ${formatMoney(cents!)} for the year.`)) onDone();
      }}
    >
      <Field
        id="tax-amount"
        label="For the whole year"
        hint={
          derived !== null ? (
            <>
              The app works out <Money cents={derived} />.
            </>
          ) : item.negative_ok ? (
            "A loss goes in as a negative number."
          ) : undefined
        }
        error={text && bad ? (item.negative_ok ? "Type an amount, like 2,500 or -1,200." : "Type an amount, like 2,500.") : null}
      >
        <Input inputMode={item.negative_ok ? "text" : "decimal"} autoComplete="off" autoFocus value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={bad || saving}>
          Save
        </Button>
        {item.typed && (
          <Button
            type="button"
            variant="secondary"
            disabled={saving}
            onClick={async () => {
              if (await save({ amounts: { [item.key]: null } }, derived !== null ? "Back to the app's number." : "Cleared.")) onDone();
            }}
          >
            <RotateCcw /> {derived !== null ? "Use the app's number" : "Clear"}
          </Button>
        )}
      </div>
    </form>
  );
}

// ---- how it adds up

type Row = [React.ReactNode, number, ("plus" | "minus" | "total" | "plain")?];

function Lines({ rows }: { rows: Row[] }) {
  return (
    <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([label, cents, kind = "plain"], i) => (
        <div key={i} className={cn("contents", kind === "total" && "font-medium")}>
          {kind === "total" && <div className="col-span-2 border-t" aria-hidden />}
          <dt className={cn(kind !== "total" && "text-muted-foreground")}>{label}</dt>
          <dd className="text-right tabular-nums">
            {kind === "minus" && cents ? "−" : ""}
            <Money cents={Math.abs(cents)} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Federal({ t }: { t: TaxState }) {
  const f = t.federal;
  const rows: Row[] = [
    ["Pay, less pre-tax 401(k), HSA and premiums", f.wages_taxed_cents],
  ];
  const other = f.income_cents - f.wages_taxed_cents;
  if (other) rows.push(["Interest, dividends, gains and other income", other]);
  if (f.income_cents !== f.agi_cents) rows.push(["Adjustments", f.income_cents - f.agi_cents, "minus"]);
  rows.push(["Adjusted gross income", f.agi_cents, "total"]);
  rows.push([f.itemizing ? "Itemized deductions" : "Standard deduction", f.deduction_cents, "minus"]);
  rows.push(["Taxable income", f.taxable_cents, "total"]);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Federal income tax</CardTitle>
        <Landmark className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-5">
        <Lines rows={rows} />
        <div>
          <p className="mb-2 text-sm font-medium">By bracket</p>
          <ul className="grid grid-cols-1 gap-1.5 text-sm">
            {f.ordinary_rows.map((r) => (
              <Bracket key={`o${r.rate}`} rate={r.rate} cents={r.cents} tax={r.tax_cents} max={f.taxable_cents} />
            ))}
            {f.gains_rows.map((r) => (
              <Bracket key={`g${r.rate}`} rate={r.rate} cents={r.cents} tax={r.tax_cents} max={f.taxable_cents} gains />
            ))}
          </ul>
          {f.preferred_cents > 0 && (
            <p className="mt-2 text-xs text-muted-foreground">
              Long-term gains and qualified dividends (<Money cents={f.preferred_cents} />) sit on top of the rest and are taxed at 0%, 15% or
              20%.
            </p>
          )}
        </div>
        <Lines
          rows={[
            ["Tax on income", f.ordinary_tax_cents + f.gains_tax_cents],
            ...(f.credits_cents ? ([["Credits", f.credits_cents, "minus"]] as Row[]) : []),
            ...(f.niit_cents
              ? ([[`Net investment income tax (3.8% of ${formatMoney(f.niit_base_cents)})`, f.niit_cents, "plus"]] as Row[])
              : []),
            ["Federal tax for the year", f.tax_cents, "total"],
          ]}
        />
        <p className="text-xs text-muted-foreground">
          Top bracket {percent(f.marginal_rate)}, so each extra $1,000 of pay costs about {formatMoney(f.marginal_rate * 100_000)} more. Overall{" "}
          {percent(f.effective_rate, 1)} of adjusted gross income.
        </p>
      </CardContent>
    </Card>
  );
}

function Bracket({ rate, cents, tax, max, gains }: { rate: number; cents: number; tax: number; max: number; gains?: boolean }) {
  return (
    <li className="grid grid-cols-[3.25rem_1fr_auto] items-center gap-3">
      <span className={cn("font-medium tabular-nums", gains && "text-positive")}>{percent(rate)}</span>
      <span className="min-w-0">
        <span className="block truncate text-xs text-muted-foreground">
          on <Money cents={cents} />
          {gains ? " of gains and dividends" : ""}
        </span>
        <span className="mt-1 block h-1.5 rounded-full bg-muted">
          <span className={cn("block h-full rounded-full", gains ? "bg-positive/70" : "bg-primary/70")} style={{ width: `${Math.max((cents / Math.max(max, 1)) * 100, 1)}%` }} />
        </span>
      </span>
      <span className="tabular-nums">
        <Money cents={tax} />
      </span>
    </li>
  );
}

function Fica({ t }: { t: TaxState }) {
  const f = t.fica;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Social Security and Medicare</CardTitle>
        <Owe cents={f.balance_cents} className="text-sm font-medium" />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        <Lines
          rows={[
            ["Social Security, 6.2% up to the wage base", f.social_security_cents],
            ["Medicare, 1.45%", f.medicare_cents],
            ...(f.additional_medicare_cents ? ([["Additional Medicare, 0.9% over the threshold", f.additional_medicare_cents]] as Row[]) : []),
            ["Due for the year", f.due_cents, "total"],
            ["Withheld by the end of the year", f.withheld_cents, "minus"],
          ]}
        />
        {Math.abs(f.difference_cents) >= 500 && (
          <p className="text-sm">
            Payroll is on track to withhold <Money cents={Math.abs(f.difference_cents)} /> {f.difference_cents < 0 ? "more" : "less"} than the
            rates give. Only the extra 0.9% Medicare settles on your return, so ask payroll if this looks wrong.
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          Employers usually get these exactly right, so this is mostly a check. A spouse&apos;s own are taken as withheld in full.
        </p>
      </CardContent>
    </Card>
  );
}

function StateCard({ t }: { t: TaxState }) {
  const s = t.state;
  if (s.no_wage_tax) {
    return (
      <Notice icon={<Landmark className="size-5" />} title={`${s.name} has no income tax on pay`}>
        Nothing to estimate for the state.
      </Notice>
    );
  }
  const rows: Row[] = [
    ["Federal adjusted gross income", t.federal.agi_cents],
    [s.itemizing || !s.supported ? `${s.name} deductions` : `${s.name} standard deduction`, s.deduction_cents ?? 0, "minus"],
  ];
  if (s.child_deduction_cents) rows.push(["Child deduction", s.child_deduction_cents, "minus"]);
  rows.push(["Taxable", s.taxable_cents ?? 0, "total"]);
  rows.push([`Tax at ${percent(s.rate, 2)}`, s.tax_cents, "total"]);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{s.name}</CardTitle>
        <Owe cents={s.balance_cents} className="text-sm font-medium" />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        {!s.supported && !s.rate_typed ? (
          <p className="text-sm text-muted-foreground">
            The app has tax tables for North Carolina only. Add {s.name}&apos;s rate under Your answers for a rough number.
          </p>
        ) : (
          <Lines rows={rows} />
        )}
        {s.notes?.map((n) => (
          <p key={n} className="text-xs text-muted-foreground">
            {n}
          </p>
        ))}
      </CardContent>
    </Card>
  );
}

function Assumptions({ t }: { t: TaxState }) {
  const sources = [...t.sources, ...(t.state.sources ?? [])];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Assumptions and sources</CardTitle>
        <FileText className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        <ul className="grid list-disc grid-cols-1 gap-1.5 pl-5 text-sm text-muted-foreground">
          {t.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
        <div>
          <p className="mb-1.5 text-sm font-medium">
            {t.table_year} tax tables{t.table_checked && `, checked ${day.format(new Date(t.table_checked))}`}
          </p>
          <ul className="grid grid-cols-1 gap-1 text-sm">
            {sources.map((s) => (
              <li key={s.url}>
                <a href={s.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-start gap-1 text-primary hover:underline">
                  {s.title}
                  <ExternalLink className="mt-1 size-3 shrink-0" aria-hidden />
                </a>
              </li>
            ))}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}
