"use client";

import { useState } from "react";
import { AlertTriangle, CalendarClock, Landmark, PencilLine, TrendingDown } from "lucide-react";
import { Money, formatMoney } from "@/components/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { toast } from "@/components/ui/toast";
import {
  type Account,
  type CdDetails,
  type Connection,
  type Details,
  type TermsIn,
  parseDollars,
  percent,
} from "@/lib/accounts";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const dayYear = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const monthName = new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" });
const fmt = (iso: string, year = false) => (year ? dayYear : day).format(new Date(iso + "T00:00:00Z"));
const FROM = { typed: "you typed", bank: "from the bank", recent: "from recent interest" } as const;
const TERMS = [1, 3, 6, 9, 12, 18, 24, 36, 48, 60];

/** The extra bit under a checking, savings or CD account on Settings → Accounts. */
export function DepositNote({ d }: { d: Details }) {
  if (d.cd) {
    const c = d.cd;
    if (c.status === "grace" && c.grace_ends) return <span className="text-warning"> · matured, move it by {fmt(c.grace_ends)}</span>;
    if (c.status === "matured") return <span className="text-warning"> · matured</span>;
    if (c.missing.length) return <span className="text-warning"> · add its rate and dates</span>;
    return (
      <>
        {" "}
        · {percent(d.apy ?? 0)} · matures {fmt(c.maturity!)}
        {c.earned_cents !== null && (
          <>
            {" "}
            · <Money cents={c.earned_cents} /> earned
          </>
        )}
      </>
    );
  }
  return (
    <>
      {d.apy !== null && d.apy >= 0.1 && (
        <>
          {" "}
          · {d.apy_from === "recent" && "about "}
          {percent(d.apy)} APY
        </>
      )}
      {d.interest.ytd_cents > 0 && d.kind === "savings" && (
        <>
          {" "}
          · <Money cents={d.interest.ytd_cents} /> this year
        </>
      )}
      {d.fees.ytd_cents > 0 && (
        <span className="text-warning">
          {" "}
          · <Money cents={d.fees.ytd_cents} /> in fees this year
        </span>
      )}
    </>
  );
}

/** On the account sheet: what it pays and has earned, how that compares, and for a CD its terms. */
export function DepositDetails({
  account: a,
  details: d,
  disabled,
  onSaved,
}: {
  account: Account;
  details: Details;
  disabled: boolean;
  onSaved: (c: Connection) => void;
}) {
  const [editing, setEditing] = useState(false);
  const save = async (body: TermsIn) => {
    try {
      const r = await api<Connection>(`/api/accounts/${a.id}/terms`, { method: "PUT", body });
      toast("Saved.");
      setEditing(false);
      onSaved(r);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <div className="grid grid-cols-1 gap-3">
      {d.cd ? <CdPanel d={d} c={d.cd} onEdit={() => setEditing(true)} disabled={disabled} /> : <RatePanel d={d} onEdit={() => setEditing(true)} disabled={disabled} />}
      {d.kind === "checking" && <CheckingPanel d={d} />}
      {d.fdic.over_cents > 0 && (
        <Warn>
          <Money cents={d.fdic.bank_cents} /> is at this bank, <Money cents={d.fdic.over_cents} /> over the $250,000 FDIC limit for one
          person at one bank. A joint account has its own limit; otherwise consider moving the rest.
        </Warn>
      )}
      {editing && <TermsForm d={d} onCancel={() => setEditing(false)} onSave={save} />}
    </div>
  );
}

function Warn({ children, icon: Icon = AlertTriangle }: { children: React.ReactNode; icon?: React.ComponentType<{ className?: string }> }) {
  return (
    <p className="flex items-start gap-2 rounded-xl bg-warning/10 p-3 text-sm text-warning">
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}

function Stat({ label, children, note }: { label: string; children: React.ReactNode; note?: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 text-base font-semibold tabular-nums">{children}</dd>
      {note && <dd className="text-xs text-muted-foreground">{note}</dd>}
    </div>
  );
}

/** "National average for a 12-month CD: 1.76% · 1-year Treasury: 4.44%" */
function Market({ d }: { d: Details }) {
  const m = d.market;
  return (
    <p className="text-xs text-muted-foreground">
      National average for {m.national_label === "interest checking" || m.national_label === "savings" || m.national_label === "money market" ? "" : "a "}
      {m.national_label}: {percent(m.national)} · {m.treasury_label}: {percent(m.treasury)}. As of {fmt(m.treasury_as_of)}.
    </p>
  );
}

function Edit({ onEdit, disabled, children }: { onEdit: () => void; disabled: boolean; children: React.ReactNode }) {
  return (
    <Button size="sm" variant="outline" onClick={onEdit} disabled={disabled} className="justify-self-start">
      <PencilLine /> {children}
    </Button>
  );
}

const STATUS: Record<NonNullable<CdDetails["status"]>, string> = {
  open: "",
  grace: "In its grace period",
  matured: "Matured",
  renewed: "Probably renewed",
};

function CdPanel({ d, c, onEdit, disabled }: { d: Details; c: CdDetails; onEdit: () => void; disabled: boolean }) {
  const guessed = c.guessed.filter((g) => g !== "maturity" && g !== "term_months");
  if (c.earned_cents === null) {
    return (
      <section className="grid grid-cols-1 gap-3 rounded-2xl border p-4" aria-label="CD terms">
        <p className="flex items-center gap-2 text-sm font-medium">
          <CalendarClock className="size-4 text-primary" aria-hidden /> Add this CD&apos;s terms
        </p>
        <p className="text-sm text-muted-foreground">
          {c.missing.includes("apy") ? "Its rate" : ""}
          {c.missing.includes("apy") && (c.missing.includes("opened") || c.missing.includes("maturity")) ? " and " : ""}
          {c.missing.includes("maturity") || c.missing.includes("opened") ? "when it matures (or when it opened and for how long)" : ""} would
          show what it has earned so far, what it&apos;s worth at maturity, and remind you before it renews. They&apos;re on the CD&apos;s receipt
          or the bank&apos;s site.
        </p>
        <Edit onEdit={onEdit} disabled={disabled}>
          Add terms
        </Edit>
      </section>
    );
  }
  const status = c.status && c.status !== "open" ? STATUS[c.status] : null;
  return (
    <section className="grid grid-cols-1 gap-4 rounded-2xl border p-4" aria-label="CD terms">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted-foreground">Earned so far</p>
          <p className="mt-0.5 text-3xl font-semibold tracking-tight">
            <Money cents={c.earned_cents} style="exact" />
          </p>
          {c.interest_total_cents !== null && c.maturity && (
            <p className="text-xs text-muted-foreground">
              of <Money cents={c.interest_total_cents} /> by {fmt(c.maturity, true)}
            </p>
          )}
        </div>
        {status ? (
          <Badge variant="warning" className="shrink-0">{status}</Badge>
        ) : (
          c.days_left !== null && (
            <Badge variant={c.days_left <= 30 ? "warning" : "muted"} className="shrink-0">
              {c.days_left} day{c.days_left === 1 ? "" : "s"} left
            </Badge>
          )
        )}
      </div>

      {c.progress !== null && c.opened && c.maturity && (
        <div>
          <div
            role="progressbar"
            aria-label="Through its term"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(c.progress * 100)}
            className="h-2 w-full overflow-hidden rounded-full bg-muted"
          >
            <div className="h-full rounded-full bg-primary" style={{ width: `${c.progress * 100}%` }} />
          </div>
          <div className="mt-1.5 flex justify-between text-xs text-muted-foreground">
            <span>Opened {fmt(c.opened, true)}</span>
            <span>Matures {fmt(c.maturity, true)}</span>
          </div>
        </div>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Stat label="Rate" note={d.apy_from ? FROM[d.apy_from] : undefined}>
          {percent(d.apy ?? 0)} APY
        </Stat>
        <Stat label={c.pays_out ? "Gives back" : "Worth at maturity"} note={c.term_months ? `${c.term_months}-month term` : undefined}>
          {c.value_at_maturity_cents !== null ? <Money cents={c.value_at_maturity_cents} /> : "—"}
        </Stat>
        <Stat label="Put in">
          <Money cents={c.principal_cents} />
        </Stat>
        {c.pays_out ? (
          <Stat label="Paid out so far" note="to another account">
            <Money cents={c.earned_cents} />
          </Stat>
        ) : (
          <Stat label="Added so far" note={c.built_up_cents ? <><Money cents={c.built_up_cents} /> more building up</> : undefined}>
            <Money cents={c.credited_cents} />
          </Stat>
        )}
      </dl>

      <ul className="grid grid-cols-1 gap-1.5 text-sm">
        <li>
          <span className="text-muted-foreground">Taking it out early: </span>
          {c.penalty_days ? (
            <>
              {c.penalty_days} days of interest, about <Money cents={c.penalty_cents ?? 0} />
            </>
          ) : (
            "penalty not set"
          )}
        </li>
        <li>
          <span className="text-muted-foreground">At maturity: </span>
          {c.auto_renew === true ? "renews by itself at the bank's rate then" : c.auto_renew === false ? "doesn't renew" : "renewal not set"}
          {c.grace_ends && (
            <>
              ; grace period to {fmt(c.grace_ends)}
              {c.grace_typical && " (7 days is typical; the bank's notice says)"}
            </>
          )}
        </li>
      </ul>

      {c.break_even && c.break_even.gain_cents > 0 && (
        <p className="flex items-start gap-2 rounded-xl bg-accent/60 p-3 text-sm">
          <TrendingDown className="mt-0.5 size-4 shrink-0 rotate-180 text-primary" aria-hidden />
          <span>
            Treasuries for the time left pay {percent(c.break_even.rate)}. Breaking the CD and moving it would come out about{" "}
            <Money cents={c.break_even.gain_cents} /> ahead after the penalty.
          </span>
        </p>
      )}
      {c.status === "renewed" && (
        <Warn icon={CalendarClock}>It probably renewed for another term. Type the new rate and maturity date so the numbers stay right.</Warn>
      )}
      {c.balance_gap_cents !== null && (
        <Warn>
          The balance is <Money cents={Math.abs(c.balance_gap_cents)} /> {c.balance_gap_cents > 0 ? "more" : "less"} than the amount put in plus the
          interest seen so far. Check the amount and the rate.
        </Warn>
      )}
      <Market d={d} />
      {guessed.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {guessed.includes("opened") && guessed.includes("principal_cents")
            ? "When it opened and how much went in are"
            : guessed.includes("opened")
              ? "When it opened is"
              : "How much went in is"}{" "}
          worked out from the account&apos;s history. Edit the terms to set {guessed.length > 1 ? "them" : "it"}.
        </p>
      )}
      <Edit onEdit={onEdit} disabled={disabled}>
        Edit terms
      </Edit>
    </section>
  );
}

function RatePanel({ d, onEdit, disabled }: { d: Details; onEdit: () => void; disabled: boolean }) {
  // Checking keeps money for bills, so its nudge is the idle cash above that (CheckingPanel), not the whole balance.
  const behind = d.kind !== "checking" && d.apy !== null && d.expected_12m_cents !== null && d.market.treasury - d.apy >= 1;
  const extra = behind && d.apy !== null && d.expected_12m_cents ? Math.round((d.expected_12m_cents / d.apy) * (d.market.treasury - d.apy)) : 0;
  const history = d.rates.length >= 2 ? d.rates : [];
  const top = Math.max(...history.map((r) => r.apy), 0.01);
  return (
    <section className="grid grid-cols-1 gap-4 rounded-2xl border p-4" aria-label="Interest">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-muted-foreground">Rate</p>
          <p className="mt-0.5 text-3xl font-semibold tracking-tight">{d.apy !== null ? `${d.apy_from === "recent" ? "≈ " : ""}${percent(d.apy)}` : "—"}</p>
          <p className="text-xs text-muted-foreground">
            {d.apy !== null ? <>APY, {FROM[d.apy_from!]}</> : "No rate yet: the bank doesn't send one, and no interest has come in."}
          </p>
        </div>
        {d.expected_12m_cents !== null && d.expected_12m_cents > 0 && (
          <div className="text-right">
            <p className="text-xs text-muted-foreground">A year at this balance</p>
            <p className="mt-0.5 text-base font-semibold">
              <Money cents={d.expected_12m_cents} />
            </p>
          </div>
        )}
      </div>
      <dl className="grid grid-cols-3 gap-3">
        <Stat label="This month">
          <Money cents={d.interest.month_cents} />
        </Stat>
        <Stat label="This year">
          <Money cents={d.interest.ytd_cents} />
        </Stat>
        <Stat label="Last 12 months">
          <Money cents={d.interest["12m_cents"]} />
        </Stat>
      </dl>
      {history.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs text-muted-foreground">Rate by month</p>
          <div className="flex h-14 items-end gap-1.5" role="img" aria-label={history.map((r) => `${r.month}: ${percent(r.apy)}`).join(", ")}>
            {history.map((r, i) => (
              <div key={r.month} className="flex min-w-0 flex-1 flex-col items-center gap-1">
                <div
                  className={cn("w-full rounded-t-md", i === history.length - 1 ? "bg-primary" : "bg-primary/35")}
                  style={{ height: `${Math.max((r.apy / top) * 40, 2)}px` }}
                />
                <span className="text-[10px] text-muted-foreground">{monthName.format(new Date(r.month + "-01T00:00:00Z"))}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {behind && (
        <p className="flex items-start gap-2 rounded-xl bg-accent/60 p-3 text-sm">
          <Landmark className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
          <span>
            A {d.market.treasury_label} pays {percent(d.market.treasury)}. At that rate this balance would earn about <Money cents={extra} /> more a
            year.
          </span>
        </p>
      )}
      <Market d={d} />
      <Edit onEdit={onEdit} disabled={disabled}>
        {d.kind === "checking" ? "Edit rate and fees" : "Set the rate"}
      </Edit>
    </section>
  );
}

function CheckingPanel({ d }: { d: Details }) {
  const items = [];
  if (d.fees.ytd_cents > 0) {
    items.push(
      <li key="fees">
        <span className="font-medium">
          <Money cents={d.fees.ytd_cents} /> in bank fees this year
        </span>
        <span className="text-muted-foreground">
          : {d.fees.by_kind.map((f, i) => (
            <span key={f.label}>
              {i > 0 && ", "}
              {f.label} <Money cents={f.cents} />
            </span>
          ))}
        </span>
      </li>,
    );
  }
  if (d.monthly_fee_cents) {
    items.push(
      <li key="monthly" className={d.below_minimum ? "text-warning" : undefined}>
        <Money cents={d.monthly_fee_cents} /> a month
        {d.min_balance_cents ? (
          <>
            {" "}
            unless it stays above <Money cents={d.min_balance_cents} />
            {d.below_minimum ? ": it's below that now." : "."}
          </>
        ) : (
          "."
        )}
      </li>,
    );
  }
  if (d.idle) {
    items.push(
      <li key="idle">
        <span className="font-medium">
          About <Money cents={d.idle.cents} /> could be earning more.
        </span>{" "}
        <span className="text-muted-foreground">
          That&apos;s above a month and a half of what usually leaves this account (<Money cents={d.idle.monthly_out_cents} /> a month). At{" "}
          {percent(d.idle.rate)} {d.idle.rate_from === "savings" ? "in your savings" : `in a ${d.market.treasury_label}`} it would earn about{" "}
          <Money cents={d.idle.earn_cents} /> a year.
        </span>
      </li>,
    );
  }
  if (!items.length) return null;
  return <ul className="grid grid-cols-1 gap-2.5 rounded-2xl border p-4 text-sm">{items}</ul>;
}

/** Typed-in terms. A blank field is sent as null: for a CD the app goes back to guessing it. */
function TermsForm({ d, onCancel, onSave }: { d: Details; onCancel: () => void; onSave: (b: TermsIn) => void }) {
  const c = d.cd;
  const typed = (k: CdDetails["guessed"][number]) => (c && !c.guessed.includes(k) ? c : null);
  const [apy, setApy] = useState(d.apy_typed?.toString() ?? "");
  const [opened, setOpened] = useState(typed("opened")?.opened ?? "");
  const [term, setTerm] = useState(typed("term_months")?.term_months?.toString() ?? "");
  const [maturity, setMaturity] = useState(typed("maturity")?.maturity ?? "");
  const [principal, setPrincipal] = useState(typed("principal_cents") ? (c!.principal_cents / 100).toFixed(2) : "");
  const [penalty, setPenalty] = useState(c?.penalty_days?.toString() ?? "");
  const [renew, setRenew] = useState(c?.auto_renew === null || !c ? "" : String(c.auto_renew));
  const [grace, setGrace] = useState(c?.grace_days?.toString() ?? "");
  const [paysOut, setPaysOut] = useState(c?.pays_out === null || !c ? "" : String(c.pays_out));
  const [fee, setFee] = useState(d.monthly_fee_cents !== null ? (d.monthly_fee_cents / 100).toFixed(2) : "");
  const [minimum, setMinimum] = useState(d.min_balance_cents !== null ? (d.min_balance_cents / 100).toFixed(2) : "");
  const [error, setError] = useState<string | null>(null);

  const num = (s: string) => (s.trim() === "" ? null : Number(s));
  const money = (s: string) => (s.trim() === "" ? null : parseDollars(s));
  const flag = (s: string) => (s === "" ? null : s === "true");
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const rate = num(apy);
    if (rate !== null && (isNaN(rate) || rate < 0 || rate > 25)) return setError("Type the APY as a percent, like 4.25");
    if (opened && maturity && maturity <= opened) return setError("The maturity date has to come after the day it opened.");
    for (const [label, v] of [
      ["amount", principal],
      ["fee", fee],
      ["minimum", minimum],
    ] as const) {
      if (v.trim() && money(v) === null) return setError(`Type the ${label} like 1250.00`);
    }
    onSave({
      apy: rate,
      opened: c ? opened || null : null,
      term_months: c ? num(term) : null,
      maturity: c ? maturity || null : null,
      principal_cents: c ? money(principal) : null,
      penalty_days: c ? num(penalty) : null,
      auto_renew: c ? flag(renew) : null,
      grace_days: c ? num(grace) : null,
      pays_out: c ? flag(paysOut) : null,
      monthly_fee_cents: d.kind === "checking" ? money(fee) : null,
      min_balance_cents: d.kind === "checking" ? money(minimum) : null,
    });
  };
  return (
    <form onSubmit={submit} className="grid grid-cols-1 gap-4 rounded-2xl border border-primary/30 p-4" aria-label="Terms">
      <p className="text-sm font-medium">{c ? "The CD's terms" : "Rate and fees"}</p>
      <p className="-mt-2 text-xs text-muted-foreground">
        From the {c ? "CD's receipt, a statement" : "bank's site or app"}. Leave anything you don&apos;t know blank.
      </p>
      <Field
        id="t-apy"
        label="APY (%)"
        hint={
          d.bank_apy !== null
            ? `The bank reports ${percent(d.bank_apy)}. Blank uses that.`
            : d.implied_apy !== null
              ? `Recent interest works out to about ${percent(d.implied_apy)}. Blank uses that.`
              : undefined
        }
      >
        <Input value={apy} onChange={(e) => setApy(e.target.value)} inputMode="decimal" placeholder="4.25" />
      </Field>
      {c && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <Field id="t-opened" label="Opened on">
              <Input type="date" value={opened} onChange={(e) => setOpened(e.target.value)} />
            </Field>
            <Field id="t-term" label="Term">
              <Select value={term} onChange={(e) => setTerm(e.target.value)}>
                <option value="">Not sure</option>
                {TERMS.map((m) => (
                  <option key={m} value={m}>
                    {m} months
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Field id="t-maturity" label="Matures on" hint="Blank works it out from the opening date and term.">
            <Input type="date" value={maturity} onChange={(e) => setMaturity(e.target.value)} />
          </Field>
          <Field id="t-principal" label="Amount put in" hint={`Blank: about ${formatMoney(c.principal_cents)}, from the account's history.`}>
            <Input value={principal} onChange={(e) => setPrincipal(e.target.value)} inputMode="decimal" placeholder="25000.00" />
          </Field>
          <Field id="t-pays" label="Interest">
            <Select value={paysOut} onChange={(e) => setPaysOut(e.target.value)}>
              <option value="">Not sure (added to the CD)</option>
              <option value="false">Added to the CD</option>
              <option value="true">Paid to another account</option>
            </Select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field id="t-penalty" label="Early withdrawal penalty" hint="Days of interest">
              <Input value={penalty} onChange={(e) => setPenalty(e.target.value)} inputMode="numeric" placeholder="90" />
            </Field>
            <Field id="t-grace" label="Grace period" hint="Days after maturity">
              <Input value={grace} onChange={(e) => setGrace(e.target.value)} inputMode="numeric" placeholder="7" />
            </Field>
          </div>
          <Field id="t-renew" label="At maturity">
            <Select value={renew} onChange={(e) => setRenew(e.target.value)}>
              <option value="">Not sure</option>
              <option value="true">Renews by itself</option>
              <option value="false">Pays out, doesn&apos;t renew</option>
            </Select>
          </Field>
        </>
      )}
      {d.kind === "checking" && (
        <div className="grid grid-cols-2 gap-3">
          <Field id="t-fee" label="Monthly fee">
            <Input value={fee} onChange={(e) => setFee(e.target.value)} inputMode="decimal" placeholder="0.00" />
          </Field>
          <Field id="t-min" label="No fee above">
            <Input value={minimum} onChange={(e) => setMinimum(e.target.value)} inputMode="decimal" placeholder="1500.00" />
          </Field>
        </div>
      )}
      {error && (
        <p className="text-sm font-medium text-negative" role="alert">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit">Save</Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
