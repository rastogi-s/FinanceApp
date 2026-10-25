"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { ArrowDownRight, ArrowUpRight, Briefcase, FileUp, Minus, NotebookPen, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { AIOfferCard, shrinkPicture, type AIOffer } from "@/components/ai-offer";
import { YearBars } from "@/components/earnings-chart";
import { Money, formatMoney } from "@/components/money";
import { BackLink, Notice, PageTitle } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { parseDollars } from "@/lib/accounts";
import { usd } from "@/lib/ai";
import { api } from "@/lib/api";
import {
  HOW,
  pct,
  type EarningsState,
  type EarningsYear,
  type Letter,
  type LetterRead,
  type ReadAnswer,
  type SourceKey,
  type YearRead,
} from "@/lib/earnings";
import { resetHousehold, useApi } from "@/lib/household";
import { cn } from "@/lib/utils";

const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const shortDay = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

export default function Earnings() {
  const { data, error } = useApi<EarningsState>("/api/earnings");
  const [local, setLocal] = useState<EarningsState | null>(null);
  const e = local ?? data;
  return (
    <>
      <BackLink href="/plan">Plan</BackLink>
      <PageTitle
        title="Income over time"
        subtitle="Your pay each year by where it came from, how it grew, and whether raises kept up with prices."
      />
      {error && !e && (
        <Notice icon={<Briefcase className="size-5" />} title="Couldn't load your income">
          {error}. Check that the server is running, then reload the page.
        </Notice>
      )}
      {!e && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-40 rounded-xl" />
          <Skeleton className="h-64 rounded-xl" />
        </div>
      )}
      {e && <EarningsView e={e} onChange={setLocal} />}
    </>
  );
}

type Change = (state: EarningsState) => void;

function EarningsView({ e, onChange }: { e: EarningsState; onChange: Change }) {
  const [letterOpen, setLetterOpen] = useState(false);
  const [editing, setEditing] = useState<{ year: number | null } | null>(null);
  const years = e.years.filter((y) => y.pay_cents > 0 || y.typed.length || y.current);
  const hasPay = years.some((y) => y.full_pay_cents > 0);
  // On a phone the years come right after the chart; on a laptop these sit under it, beside the years.
  const extras = (
    <>
      <OlderYears e={e} onAdd={() => setEditing({ year: null })} />
      <About e={e} />
    </>
  );
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] md:items-start">
      <div className="grid grid-cols-1 gap-4">
        <PayNow e={e} onLetter={() => setLetterOpen(true)} />
        {hasPay ? (
          <Card>
            <CardHeader>
              <CardTitle>Each year</CardTitle>
            </CardHeader>
            <CardContent>
              <YearBars years={years.filter((y) => y.full_comp_cents > 0)} />
            </CardContent>
          </Card>
        ) : (
          <Notice
            icon={<Briefcase className="size-5" />}
            title="No pay seen yet"
            action={
              <Button asChild size="sm" className="mt-3">
                <Link href="/budget#pay">Add a pay stub</Link>
              </Button>
            }
          >
            Pay stubs and paychecks landing in a linked account fill this in by themselves. For years before that, add them below.
          </Notice>
        )}
        <div className="hidden grid-cols-1 gap-4 md:grid">{extras}</div>
      </div>
      <div className="grid grid-cols-1 gap-4">
        {years.map((y) => (
          <YearCard key={y.year} y={y} onEdit={() => setEditing({ year: y.year })} />
        ))}
        <div className="grid grid-cols-1 gap-4 md:hidden">{extras}</div>
      </div>
      <LetterSheet open={letterOpen} onOpenChange={setLetterOpen} e={e} onChange={onChange} />
      <YearSheet editing={editing} onClose={() => setEditing(null)} e={e} onChange={onChange} />
    </div>
  );
}

// ---- now

function PayNow({ e, onLetter }: { e: EarningsState; onLetter: () => void }) {
  const n = e.now;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Your pay now</CardTitle>
        <Briefcase className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        {n?.base_cents ? (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <p className="text-xs text-muted-foreground">Base pay a year</p>
              <p className="text-2xl font-semibold tracking-tight">
                <Money cents={n.base_cents} />
              </p>
              <p className="text-xs text-muted-foreground">{n.base_how === "letter" ? "From your raise letter" : "From your latest pay stub"}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Bonus target</p>
              <p className="text-2xl font-semibold tracking-tight">
                {n.bonus_target_cents ? <Money cents={n.bonus_target_cents} /> : <span className="text-muted-foreground">—</span>}
              </p>
              <p className="text-xs text-muted-foreground">
                {n.bonus_target_percent ? `${n.bonus_target_percent}% of base` : "Add a letter to set it"}
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">Add a pay stub, or a raise or offer letter, and your base pay shows here.</p>
        )}
        {n?.cash_cents && n.bonus_target_cents ? (
          <p className="rounded-xl bg-muted px-4 py-3 text-sm">
            <Money cents={n.cash_cents} className="font-semibold" /> a year with the bonus at target, before stock and your employer&apos;s 401(k)
            and HSA money.
          </p>
        ) : null}
        {n?.coming && (
          <p className="flex items-start gap-2 rounded-xl bg-accent/60 px-4 py-3 text-sm">
            <ArrowUpRight className="mt-0.5 size-4 shrink-0 text-positive" aria-hidden />
            <span>
              From {day.format(new Date(n.coming.effective))}: <Money cents={n.coming.base_cents} className="font-semibold" /> a year. This
              year&apos;s numbers and Plan&apos;s monthly savings already count it.
            </span>
          </p>
        )}
        <Button onClick={onLetter} variant={e.letters.length ? "outline" : "default"}>
          <FileUp /> Add a raise or bonus letter
        </Button>
      </CardContent>
    </Card>
  );
}

// ---- years

function Growth({ value, label }: { value: number | null; label: string }) {
  if (value === null) return null;
  const Icon = value > 0.05 ? ArrowUpRight : value < -0.05 ? ArrowDownRight : Minus;
  return (
    <span className={cn("inline-flex items-center gap-0.5 text-sm font-medium", value > 0.05 ? "text-positive" : value < -0.05 ? "text-negative" : "text-muted-foreground")}>
      <Icon className="size-4" aria-hidden />
      {pct(value)}
      <span className="sr-only"> {label}</span>
    </span>
  );
}

function YearCard({ y, onEdit }: { y: EarningsYear; onEdit: () => void }) {
  const on = !!y.projected;
  const shown = y.sources.filter((s) => (on ? s.full_cents : s.cents) > 0);
  const pay = shown.filter((s) => !["match", "employer_hsa"].includes(s.key));
  const extra = shown.filter((s) => ["match", "employer_hsa"].includes(s.key));
  const r = y.raise;
  // Where the numbers came from: said once when every row agrees, else on each row.
  const hows = [...new Set(shown.map((s) => s.how))];
  const common = hows.length === 1 ? HOW[hows[0]!] : "";
  return (
    <Card>
      <CardHeader className="items-start">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base font-semibold text-foreground">
            {y.year}
            {y.current && <Badge variant="muted">{on ? "On track" : "So far"}</Badge>}
            {!y.current && !y.complete && <Badge variant="warning">Part of the year</Badge>}
            {y.source === "w2" && <Badge variant="muted">From a W-2</Badge>}
            {y.source === "stub" && <Badge variant="muted">From a pay stub</Badge>}
            {!y.source && y.typed.length > 0 && <Badge variant="muted">Typed in</Badge>}
          </CardTitle>
          <p className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <Money cents={on ? y.full_pay_cents : y.pay_cents} className="text-2xl font-semibold tracking-tight" />
            {y.growth && <Growth value={y.growth.pay} label={`pay on ${y.year - 1}`} />}
          </p>
          <p className="text-xs text-muted-foreground">
            {on
              ? `Pay for the year at this rate: ${formatMoney(y.pay_cents)} so far, ${y.projected!.paychecks_left} paycheck${y.projected!.paychecks_left === 1 ? "" : "s"} to go`
              : y.current
                ? `Pay so far${y.through ? `, through ${shortDay.format(new Date(y.through))}` : ""}`
                : y.complete
                  ? "Pay for the year, before taxes"
                  : `Pay${y.through ? ` through ${shortDay.format(new Date(y.through))}` : ""}; the linked accounts start partway through`}
            {common && !y.take_home && `, ${common}`}
            {y.take_home && ". Take-home only: add a pay stub for pay before taxes"}
          </p>
        </div>
        <Button size="icon" variant="ghost" onClick={onEdit} aria-label={`Edit ${y.year}`}>
          <Pencil />
        </Button>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        <ul className="divide-y text-sm">
          {pay.map((s) => (
            <SourceRow key={s.key} s={s} on={on} showHow={!common} />
          ))}
          {extra.length > 0 && (
            <>
              {extra.map((s) => (
                <SourceRow key={s.key} s={s} on={on} muted showHow={!common} />
              ))}
              <li className="flex items-center justify-between gap-3 py-2.5 font-medium">
                <span>Total compensation</span>
                <span className="flex items-center gap-3">
                  {y.growth && <Growth value={y.growth.comp} label="total compensation" />}
                  <Money cents={on ? y.full_comp_cents : y.comp_cents} />
                </span>
              </li>
            </>
          )}
        </ul>
        {r && <RaiseLine r={r} year={y.year} />}
        {y.bonus_guessed && (
          <p className="text-xs text-muted-foreground">A stub well over your usual one had no bonus line, so the extra is counted as bonus.</p>
        )}
        {(y.note || y.events.length > 0) && (
          <div className="flex items-start gap-2 rounded-xl bg-muted/60 px-3 py-2.5 text-sm">
            <NotebookPen className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0">
              {y.note && <p>{y.note}</p>}
              {y.events
                .filter((ev) => !y.note.includes(ev.text))
                .map((ev) => (
                  <p key={ev.id} className="text-muted-foreground">
                    {shortDay.format(new Date(ev.effective))}: {ev.text}
                  </p>
                ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SourceRow({
  s,
  on,
  muted = false,
  showHow,
}: {
  s: EarningsYear["sources"][number];
  on: boolean;
  muted?: boolean;
  showHow: boolean;
}) {
  return (
    <li className="flex items-center justify-between gap-3 py-2.5">
      <span className="min-w-0">
        <span className={cn("block", muted && "text-muted-foreground")}>{muted ? `+ ${s.label}` : s.label}</span>
        {showHow && HOW[s.how] && <span className="block text-xs text-muted-foreground">{HOW[s.how]}</span>}
      </span>
      <span className="shrink-0 text-right">
        <Money cents={on ? s.full_cents : s.cents} className="font-medium" />
        {on && s.full_cents !== s.cents && (
          <span className="block text-xs text-muted-foreground">
            <Money cents={s.cents} /> so far
          </span>
        )}
      </span>
    </li>
  );
}

function RaiseLine({ r, year }: { r: NonNullable<EarningsYear["raise"]>; year: number }) {
  const infl = r.inflation;
  const tone = r.verdict === "ahead" ? "text-positive" : r.verdict === "behind" ? "text-negative" : "text-foreground";
  return (
    <div className="rounded-xl border px-3 py-2.5 text-sm">
      <p>
        <span className="font-medium">Base pay rate {pct(r.percent)}</span>{" "}
        <span className="text-muted-foreground">
          ({formatMoney(r.from_cents)} a year before)
          {infl && (
            <>
              , prices {pct(infl.percent)} {infl.latest ? `over the 12 months to ${monthYear(infl.through)}` : `in ${year}`}
            </>
          )}
          .
        </span>
      </p>
      {r.real !== null && (
        <p className={cn("mt-0.5 font-medium", tone)}>
          {r.verdict === "even"
            ? "Kept up with prices, about even."
            : r.verdict === "ahead"
              ? `Ahead of prices by ${Math.abs(r.real).toFixed(1)}%: a real raise.`
              : `Behind prices by ${Math.abs(r.real).toFixed(1)}%: your pay buys less than the year before.`}
        </p>
      )}
    </div>
  );
}

function monthYear(iso: string | null) {
  if (!iso) return "now";
  const [y, m] = iso.split("-").map(Number);
  return new Date(Date.UTC(y!, m! - 1, 1)).toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
}

function OlderYears({ e, onAdd }: { e: EarningsState; onAdd: () => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Earlier years</CardTitle>
        <Plus className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        <p className="text-sm text-muted-foreground">
          {e.linked_from
            ? `Your linked accounts reach back to ${day.format(new Date(e.linked_from))}. `
            : "Linked accounts usually reach back about two years. "}
          Add years before that from a W-2 or the year&apos;s last pay stub, or type them in, to see growth over your whole career.
        </p>
        <Button variant="outline" onClick={onAdd}>
          <Plus /> Add a year
        </Button>
      </CardContent>
    </Card>
  );
}

function About({ e }: { e: EarningsState }) {
  return (
    <details className="rounded-xl border bg-card px-4 py-3 text-sm">
      <summary className="cursor-pointer font-medium">How these are worked out</summary>
      <ul className="mt-2 grid list-disc gap-1.5 pl-5 text-muted-foreground">
        <li>Pay is before taxes: the last pay stub&apos;s year-to-date column where there is one, else the stubs added up.</li>
        <li>A paycheck that landed without its stub is filled in from your usual stub, scaled to what landed.</li>
        <li>Base pay is pay less bonus and stock. Raises compare your pay rate at the end of each year, so a raise in March counts in full.</li>
        <li>This year &quot;on track&quot; adds the paychecks left at your usual pay, or a raise letter&apos;s new salary from its start. Bonuses aren&apos;t guessed.</li>
        <li>Your employer&apos;s HSA money counts toward the same yearly HSA limit as your own; Taxes checks it.</li>
        <li>
          {e.inflation.note}{" "}
          {e.inflation.sources.map((s, i) => (
            <span key={s.url}>
              {i > 0 && ", "}
              <a href={s.url} target="_blank" rel="noreferrer" className="font-medium text-primary">
                {s.title.split(":")[0]}
              </a>
            </span>
          ))}
          .
        </li>
      </ul>
    </details>
  );
}

// ---- reading a document

function useRead<T>(kind: "letter" | "year", demo: boolean, onRead: (r: T) => void) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [offer, setOffer] = useState<{ file: File; offer: AIOffer } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [read, setRead] = useState<{ name: string; warnings: string[]; cost?: number } | null>(null);
  const upload = async (original: File, withAI = false) => {
    if (demo) return toast("Reading documents is off while the app shows sample data.");
    setBusy(true);
    setError(null);
    try {
      const file = await shrinkPicture(original);
      const form = new FormData();
      form.append("file", file);
      const answer = await api<ReadAnswer<T & { warnings: string[]; ai_cost?: number }>>(
        `/api/earnings/read?kind=${kind}${withAI ? "&read_with_ai=true" : ""}`,
        { form },
      );
      if ("ai_offer" in answer) {
        setOffer({ file, offer: answer.ai_offer });
        return;
      }
      setOffer(null);
      setRead({ name: file.name, warnings: answer.warnings, cost: answer.ai_cost });
      onRead(answer);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  const box = (title: string, hint: string) => (
    <div className="rounded-2xl border border-dashed p-4">
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf,image/png,image/jpeg,image/webp"
        className="sr-only"
        aria-label={title}
        tabIndex={-1}
        onChange={(ev) => ev.target.files?.[0] && upload(ev.target.files[0])}
      />
      <div className="flex items-center gap-3">
        {read ? <Sparkles className="size-5 shrink-0 text-primary" aria-hidden /> : <FileUp className="size-5 shrink-0 text-primary" aria-hidden />}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{read ? `Filled in from ${read.name}` : title}</p>
          <p className="text-xs text-muted-foreground">
            {read
              ? `Check the numbers, then save.${read.cost !== undefined ? ` Read by AI, ${usd(read.cost)}.` : ""}`
              : hint}
          </p>
        </div>
        <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? "Reading…" : read ? "Another" : "Upload"}
        </Button>
      </div>
      {offer && (
        <div className="mt-4">
          <AIOfferCard offer={offer.offer} busy={busy} onAccept={() => upload(offer.file, true)} onCancel={() => setOffer(null)} />
        </div>
      )}
      {error && (
        <p className="mt-3 text-sm text-negative" role="alert">
          {error}
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
  );
  return { box, reset: () => (setRead(null), setOffer(null), setError(null)) };
}

const dollars = (c: number | null | undefined) => (c ? String(c / 100) : "");

function money(text: string): number | null | undefined {
  if (!text.trim()) return undefined;
  const c = parseDollars(text);
  return c === null || c < 0 ? null : c;
}

// ---- a raise or bonus letter

const LETTER_KINDS: [Letter["kind"], string][] = [
  ["raise", "Raise"],
  ["promotion", "Promotion"],
  ["bonus", "Bonus award"],
  ["offer", "New job offer"],
];

function LetterSheet({ open, onOpenChange, e, onChange }: { open: boolean; onOpenChange: (o: boolean) => void; e: EarningsState; onChange: Change }) {
  const blank = { kind: "raise" as Letter["kind"], effective: "", title: "", base: "", pct: "", target: "", bonus: "", stock: "" };
  const [f, setF] = useState(blank);
  const [saving, setSaving] = useState(false);
  const reader = useRead<LetterRead>("letter", e.demo, (r) =>
    setF({
      kind: r.kind,
      effective: r.effective ?? "",
      title: r.title,
      base: dollars(r.base_cents),
      pct: r.bonus_target_percent ? String(r.bonus_target_percent) : "",
      target: dollars(r.bonus_target_cents),
      bonus: dollars(r.bonus_cents),
      stock: dollars(r.stock_cents),
    }),
  );
  const c = { base: money(f.base), target: money(f.target), bonus: money(f.bonus), stock: money(f.stock) };
  const pctValue = f.pct.trim() ? Number(f.pct) : undefined;
  const pctBad = pctValue !== undefined && !(pctValue > 0 && pctValue <= 200);
  const bad = Object.values(c).some((v) => v === null) || pctBad;
  const says = c.base || c.target || c.bonus || pctValue || f.title.trim();
  const ok = !!f.effective && !!says && !bad;
  const close = (o: boolean) => {
    onOpenChange(o);
    if (!o) {
      setF(blank);
      reader.reset();
    }
  };
  const save = async () => {
    if (e.demo) return toast("Letters can't be saved while the app shows sample data.");
    setSaving(true);
    try {
      const next = await api<EarningsState>("/api/earnings/letters", {
        body: {
          kind: f.kind,
          effective: f.effective,
          title: f.title.trim(),
          base_cents: c.base || null,
          bonus_target_percent: pctValue ?? null,
          bonus_target_cents: c.target || null,
          bonus_cents: c.bonus || null,
          stock_cents: c.stock || null,
        },
      });
      onChange(next);
      resetHousehold();
      toast(
        c.base
          ? `Saved. From ${shortDay.format(new Date(f.effective))} this year's numbers and Plan's savings use the new pay.`
          : "Saved, with a note on that year.",
      );
      close(false);
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };
  const field = (key: keyof typeof f, label: string, hint?: string, error?: string | null, mode: "decimal" | "text" = "decimal") => (
    <Field id={`letter-${key}`} label={label} hint={hint} error={error}>
      <Input inputMode={mode} autoComplete="off" value={f[key]} onChange={(ev) => setF((v) => ({ ...v, [key]: ev.target.value }))} />
    </Field>
  );
  return (
    <Sheet open={open} onOpenChange={close} title="A raise or bonus letter" description="The new pay, the bonus target and when it starts. It updates this year's numbers and Plan.">
      <form
        className="grid grid-cols-1 gap-4 pb-2"
        onSubmit={(ev) => {
          ev.preventDefault();
          if (ok) save();
        }}
      >
        {reader.box("Have the letter? Upload it", "A PDF, screenshot or photo, read with AI after you see what's sent. The file isn't kept.")}
        <div className="grid grid-cols-2 gap-3">
          <Field id="letter-kind" label="What it is">
            <Select value={f.kind} onChange={(ev) => setF((v) => ({ ...v, kind: ev.target.value as Letter["kind"] }))}>
              {LETTER_KINDS.map(([k, l]) => (
                <option key={k} value={k}>
                  {l}
                </option>
              ))}
            </Select>
          </Field>
          <Field id="letter-effective" label={f.kind === "bonus" ? "Paid on" : "Starts"}>
            <Input type="date" value={f.effective} onChange={(ev) => setF((v) => ({ ...v, effective: ev.target.value }))} />
          </Field>
        </div>
        {f.kind !== "bonus" && field("base", "New base salary a year", undefined, c.base === null ? "Type an amount, like 130,000." : null)}
        {f.kind !== "bonus" && (
          <div className="grid grid-cols-2 gap-3">
            {field("pct", "Bonus target, %", "Of base pay", pctBad ? "A percent, like 15." : null)}
            {field("target", "Or as an amount", "A year", c.target === null ? "Type an amount." : null)}
          </div>
        )}
        {f.kind === "bonus" && field("bonus", "Bonus amount", "Before taxes", c.bonus === null ? "Type an amount." : null)}
        {(f.kind === "promotion" || f.kind === "offer" || f.title) && field("title", "New title (optional)", undefined, null, "text")}
        {f.kind !== "bonus" && field("stock", "New stock grant (optional)", "Its value when granted", c.stock === null ? "Type an amount." : null)}
        <Button type="submit" disabled={saving || !ok}>
          Save letter
        </Button>
        {e.letters.length > 0 && <SavedLetters e={e} onChange={onChange} />}
      </form>
    </Sheet>
  );
}

function SavedLetters({ e, onChange }: { e: EarningsState; onChange: Change }) {
  const remove = async (id: string) => {
    try {
      onChange(await api<EarningsState>(`/api/earnings/letters/${id}`, { method: "DELETE" }));
      resetHousehold();
      toast("Letter removed.");
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };
  return (
    <div className="border-t pt-4">
      <p className="mb-1 text-sm font-medium">Saved letters</p>
      <ul className="divide-y text-sm">
        {[...e.letters].reverse().map((l) => (
          <li key={l.id} className="flex items-center justify-between gap-3 py-2">
            <span className="min-w-0">
              <span className="block">
                {LETTER_KINDS.find(([k]) => k === l.kind)?.[1]}, {day.format(new Date(l.effective))}
              </span>
              <span className="block text-xs text-muted-foreground">
                {[
                  l.base_cents && `Base ${formatMoney(l.base_cents)}`,
                  l.bonus_target_percent && `bonus target ${l.bonus_target_percent}%`,
                  l.bonus_cents && `bonus ${formatMoney(l.bonus_cents)}`,
                  l.title,
                ]
                  .filter(Boolean)
                  .join(", ")}
              </span>
            </span>
            <Button type="button" size="icon" variant="ghost" aria-label="Remove letter" disabled={e.demo} onClick={() => remove(l.id)}>
              <Trash2 />
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---- a year by hand

const YEAR_FIELDS: [SourceKey, string, string?][] = [
  ["base", "Base pay"],
  ["bonus", "Bonus"],
  ["rsu", "Stock (RSU vests, ESPP)"],
  ["other", "Other pay"],
  ["match", "Employer 401(k) match", "On top of pay"],
  ["employer_hsa", "Employer HSA money", "On top of pay"],
];

function YearSheet({ editing, onClose, e, onChange }: { editing: { year: number | null } | null; onClose: () => void; e: EarningsState; onChange: Change }) {
  return (
    <Sheet
      open={editing !== null}
      onOpenChange={(o) => !o && onClose()}
      title={editing?.year ? `${editing.year}` : "Add a year"}
      description="Typed numbers replace what the app works out. Leave one empty to keep the app's."
    >
      {editing && <YearForm key={editing.year ?? "new"} year={editing.year} e={e} onChange={onChange} onClose={onClose} />}
    </Sheet>
  );
}

function YearForm({ year, e, onChange, onClose }: { year: number | null; e: EarningsState; onChange: Change; onClose: () => void }) {
  const row = year ? e.years.find((y) => y.year === year) : undefined;
  const typed = (k: SourceKey) => (row?.typed.includes(k) ? dollars(row.sources.find((s) => s.key === k)?.cents) : "");
  const [pick, setPick] = useState<number>(year ?? Math.min(...e.years.map((y) => y.year), e.year) - 1);
  const [values, setValues] = useState(() => Object.fromEntries(YEAR_FIELDS.map(([k]) => [k, typed(k)])) as Record<SourceKey, string>);
  const [note, setNote] = useState(row?.note ?? "");
  const [source, setSource] = useState<"typed" | "w2" | "stub">(row?.source ?? "typed");
  const [saving, setSaving] = useState(false);
  const reader = useRead<YearRead>("year", e.demo, (r) => {
    if (r.year && !year) setPick(r.year);
    setSource(r.source);
    setValues({
      base: dollars(r.base_cents),
      bonus: dollars(r.bonus_cents),
      rsu: dollars(r.rsu_cents),
      other: "",
      match: dollars(r.match_cents),
      employer_hsa: dollars(r.employer_hsa_cents),
    });
  });
  const cents = Object.fromEntries(YEAR_FIELDS.map(([k]) => [k, money(values[k])])) as Record<SourceKey, number | null | undefined>;
  const bad = Object.values(cents).some((v) => v === null);
  const target = year ?? pick;
  const save = async () => {
    if (e.demo) return toast("Years can't be changed while the app shows sample data.");
    setSaving(true);
    try {
      const body: Record<string, unknown> = { note: note.trim() || null, source };
      for (const [k] of YEAR_FIELDS) body[`${k}_cents`] = cents[k] ?? null;
      onChange(await api<EarningsState>(`/api/earnings/years/${target}`, { method: "PUT", body }));
      toast(`${target} saved.`);
      onClose();
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };
  const clear = async () => {
    setSaving(true);
    try {
      onChange(await api<EarningsState>(`/api/earnings/years/${target}`, { method: "DELETE" }));
      toast(`${target} is back to the app's numbers.`);
      onClose();
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };
  const choices = Array.from({ length: 30 }, (_, i) => e.year - i);
  return (
    <form
      className="grid grid-cols-1 gap-4 pb-2"
      onSubmit={(ev) => {
        ev.preventDefault();
        if (!bad) save();
      }}
    >
      {reader.box("Have a W-2 or the year's last stub? Upload it", "A stub PDF is read here; a W-2 or a photo with AI, after you see what's sent.")}
      {!year && (
        <Field id="year-pick" label="Year">
          <Select value={pick} onChange={(ev) => setPick(Number(ev.target.value))}>
            {choices.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </Select>
        </Field>
      )}
      <div className="grid grid-cols-2 gap-3">
        {YEAR_FIELDS.map(([k, label, hint]) => {
          const app = row && !row.typed.includes(k) ? row.sources.find((s) => s.key === k)?.cents : undefined;
          return (
            <Field key={k} id={`year-${k}`} label={label} hint={app ? `The app has ${formatMoney(app)}` : hint} error={cents[k] === null ? "Type an amount." : null}>
              <Input
                inputMode="decimal"
                autoComplete="off"
                placeholder={app ? String(Math.round(app / 100)) : "0"}
                value={values[k]}
                onChange={(ev) => {
                  setValues((v) => ({ ...v, [k]: ev.target.value }));
                  setSource("typed");
                }}
              />
            </Field>
          );
        })}
      </div>
      <Field id="year-note" label="Note (optional)" hint="Promoted, new bonus target, changed jobs…">
        <Input value={note} maxLength={200} onChange={(ev) => setNote(ev.target.value)} />
      </Field>
      <Button type="submit" disabled={saving || bad}>
        Save {target}
      </Button>
      {row && (row.typed.length > 0 || row.note) && (
        <Button type="button" variant="ghost" disabled={saving} onClick={clear}>
          Go back to the app&apos;s numbers
        </Button>
      )}
    </form>
  );
}
