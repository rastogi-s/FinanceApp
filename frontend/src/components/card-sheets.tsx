"use client";

import { useState } from "react";
import { AlertTriangle, ExternalLink } from "lucide-react";
import { Money } from "@/components/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { type CardRow, type CardsPage, type ProductDetail, type Role, MONTHS, PERIOD, ROLES, longDay, shortDay } from "@/lib/cards";
import { cn } from "@/lib/utils";

const DEMO = "Changing cards is off while the app shows sample data.";

/** A pill that's one of a set of choices. Put a few inside a role="radiogroup". */
export function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={on}
      onClick={onClick}
      className={cn(
        "rounded-full border px-3 py-1.5 text-sm font-medium transition-colors",
        on ? "border-primary bg-accent text-accent-foreground" : "hover:bg-muted",
      )}
    >
      {children}
    </button>
  );
}

/** Dark text on a light card color, white on a dark one. */
function lightColor(hex: string): boolean {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return false;
  const n = parseInt(m[1]!, 16);
  const lin = (v: number) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(n >> 16) + 0.7152 * lin((n >> 8) & 255) + 0.0722 * lin(n & 255) > 0.4;
}

/** The card itself, drawn in its product's color. */
export function CardFace({ row, className }: { row: CardRow; className?: string }) {
  const color = row.product?.color ?? "#64748b";
  const light = lightColor(color);
  return (
    <div
      className={cn(
        "relative flex aspect-[1.586] w-full flex-col justify-between overflow-hidden rounded-2xl p-4 shadow-sm ring-1 ring-black/5 transition-transform group-hover:-translate-y-0.5 dark:ring-white/10",
        light ? "text-slate-900" : "text-white",
        className,
      )}
      style={{
        background: `linear-gradient(135deg, ${color} 0%, color-mix(in srgb, ${color} 72%, ${light ? "#000" : "#fff"}) 100%)`,
      }}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs font-medium opacity-80">{row.product?.issuer ?? row.institution}</span>
        {row.role && row.role !== "active" && (
          <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", light ? "bg-black/10" : "bg-white/20")}>
            {row.role === "drawer" ? "In a drawer" : "Closed"}
          </span>
        )}
      </div>
      <div>
        <p className="text-base leading-tight font-semibold">{row.product?.short ?? row.name}</p>
        <div className="mt-1 flex items-baseline justify-between gap-2 text-xs opacity-85">
          <span className="tabular-nums">{row.mask ? `•••• ${row.mask}` : ""}</span>
          <span className="font-medium tracking-wide uppercase">{row.product?.network ?? (row.linked ? "" : "Pick which card")}</span>
        </div>
      </div>
    </div>
  );
}

/** One card: what it earns, its credits and statement, and which card product it is. */
export function CardSheet({
  row,
  page,
  open,
  onOpenChange,
  onSaved,
}: {
  row: CardRow | null;
  page: CardsPage;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (page: CardsPage) => void;
}) {
  if (!row) return null;
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={row.label}
      description={row.product ? `${row.product.name} · ${row.institution}` : `${row.institution} · pick which card this is`}
    >
      <div className="grid grid-cols-1 gap-6 pb-2">
        {row.product && <Rewards p={row.product} />}
        {row.caps.length > 0 && <Caps row={row} />}
        {row.statement && <StatementBlock row={row} />}
        <LinkForm
          key={`${row.account_id}-${row.product?.id ?? ""}`}
          row={row}
          page={page}
          onSaved={onSaved}
          onDone={() => onOpenChange(false)}
        />
        {row.product && <Source p={row.product} />}
      </div>
    </Sheet>
  );
}

function Heading({ children }: { children: React.ReactNode }) {
  return <h3 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">{children}</h3>;
}

function Rewards({ p }: { p: ProductDetail }) {
  return (
    <>
      <section>
        <Heading>What it earns</Heading>
        <ul className="divide-y rounded-2xl border">
          {p.earn.map((e, i) => (
            <li key={i} className="flex items-start gap-3 px-4 py-2.5 text-sm">
              <span className="w-14 shrink-0 font-semibold tabular-nums">{e.rate}</span>
              <span className="min-w-0 flex-1">
                {e.what}
                {(e.portal || e.until || e.activate) && (
                  <span className="block text-xs text-muted-foreground">
                    {[
                      e.portal && `Only through ${e.portal}`,
                      e.activate && "Turn on each quarter",
                      e.until && `Until ${longDay.format(new Date(e.until))}`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted-foreground">
          {p.annual_fee ? (
            <>
              <Money cents={p.annual_fee * 100} /> a year
            </>
          ) : (
            "No annual fee"
          )}
          {p.foreign_fee ? " · charges a fee abroad" : " · no fee abroad"}
          {p.pairs ? ` · ${p.pairs}` : ""}
        </p>
      </section>
      {p.credits.length > 0 && (
        <section>
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <Heading>Credits</Heading>
            <span className="text-xs text-muted-foreground">
              Worth up to <Money cents={p.credits_per_year * 100} /> a year
            </span>
          </div>
          <ul className="divide-y rounded-2xl border">
            {p.credits.map((c) => (
              <li key={c.name} className="px-4 py-2.5 text-sm">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-medium">{c.name}</span>
                  <span className="shrink-0 tabular-nums">
                    <Money cents={c.amount * 100} style="exact" /> {PERIOD[c.period]}
                  </span>
                </div>
                {c.note && <p className="mt-0.5 text-xs text-muted-foreground">{c.note}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {p.perks.length > 0 && (
        <section>
          <Heading>Perks</Heading>
          <ul className="grid list-disc grid-cols-1 gap-1 pl-5 text-sm">
            {p.perks.map((k) => (
              <li key={k}>{k}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function Caps({ row }: { row: CardRow }) {
  return (
    <section>
      <Heading>Bonus limits</Heading>
      <ul className="grid grid-cols-1 gap-3">
        {row.caps.map((c) => {
          const pct = c.cap_cents ? Math.min((c.used_cents / c.cap_cents) * 100, 100) : 0;
          return (
            <li key={c.what} className="text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span>{c.what}</span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  <Money cents={c.used_cents} /> of <Money cents={c.cap_cents} /> {PERIOD[c.period as keyof typeof PERIOD] ?? c.period}
                </span>
              </div>
              <div
                className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted"
                role="progressbar"
                aria-label={`${c.what} used`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(pct)}
              >
                <div className={cn("h-full rounded-full", pct >= 100 ? "bg-warning" : "bg-primary")} style={{ width: `${pct}%` }} />
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function StatementBlock({ row }: { row: CardRow }) {
  const s = row.statement!;
  const lines: [string, React.ReactNode][] = [];
  if (s.statement_cents !== null)
    lines.push([
      s.statement_date ? `Statement (${shortDay.format(new Date(s.statement_date))})` : "Statement balance",
      <Money key="s" cents={s.statement_cents} style="exact" />,
    ]);
  if (s.minimum_cents !== null) lines.push(["Minimum payment", <Money key="m" cents={s.minimum_cents} style="exact" />]);
  if (s.due_date) lines.push(["Due", longDay.format(new Date(s.due_date))]);
  if (s.last_payment_cents !== null)
    lines.push([
      "Last payment",
      <span key="l">
        <Money cents={s.last_payment_cents} style="exact" />
        {s.last_payment_date ? ` on ${shortDay.format(new Date(s.last_payment_date))}` : ""}
      </span>,
    ]);
  if (s.purchase_apr !== null) lines.push(["Purchase APR", `${s.purchase_apr.toFixed(2)}%`]);
  for (const a of s.aprs.filter((a) => a.type !== "purchase_apr")) lines.push([aprName(a.type), `${a.percent.toFixed(2)}%`]);
  return (
    <section>
      <Heading>Statement</Heading>
      {s.overdue && (
        <p className="mb-2 flex items-center gap-1.5 text-sm font-medium text-negative">
          <AlertTriangle className="size-4" aria-hidden /> The bank says a payment is overdue.
        </p>
      )}
      <dl className="divide-y rounded-2xl border text-sm">
        {lines.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-3 px-4 py-2.5">
            <dt className="text-muted-foreground">{k}</dt>
            <dd className="text-right font-medium tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function aprName(type: string): string {
  return (
    {
      balance_transfer_apr: "Balance transfer APR",
      cash_apr: "Cash advance APR",
      special: "Promo APR",
    }[type] ?? "Other APR"
  );
}

function Source({ p }: { p: ProductDetail }) {
  return (
    <p className="text-xs text-muted-foreground">
      {!p.verified && (
        <Badge variant="warning" className="mr-1.5 align-middle">
          Not checked
        </Badge>
      )}
      {p.verified
        ? `Checked against the issuer's page on ${longDay.format(new Date(p.checked))}.`
        : "The issuer's page couldn't be read, so check these rates."}{" "}
      <a href={p.source} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 font-medium text-primary">
        Issuer&apos;s page <ExternalLink className="size-3" aria-hidden />
      </a>
    </p>
  );
}

function LinkForm({ row, page, onSaved, onDone }: { row: CardRow; page: CardsPage; onSaved: (p: CardsPage) => void; onDone: () => void }) {
  const [product, setProduct] = useState(row.product?.id ?? row.suggested ?? "");
  const [choice, setChoice] = useState(row.product?.choice ?? "");
  const [role, setRole] = useState<Role>(row.role ?? "active");
  const [renewal, setRenewal] = useState(row.renewal_month ? String(row.renewal_month) : "");
  const [busy, setBusy] = useState(false);
  const chosen = page.catalog.find((p) => p.id === product);
  const suggested = row.suggested ? page.catalog.find((p) => p.id === row.suggested) : undefined;
  const issuers = [...new Set(page.catalog.map((p) => p.issuer))];

  const send = async (method: "PUT" | "DELETE") => {
    if (page.demo) return toast(DEMO);
    setBusy(true);
    try {
      const body =
        method === "PUT"
          ? {
              product,
              choice: chosen?.choices.length ? choice || chosen.choices[0]!.id : null,
              role,
              renewal_month: renewal ? Number(renewal) : null,
            }
          : undefined;
      const next = await api<CardsPage>(`/api/cards/${row.account_id}/link`, {
        method,
        body,
      });
      onSaved(next);
      toast(method === "PUT" ? `${row.label} is a ${chosen?.short ?? "card"}.` : `${row.label} isn't linked to a card now.`);
      if (method === "DELETE") onDone();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="grid grid-cols-1 gap-4 rounded-2xl bg-muted/50 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (product) send("PUT");
      }}
    >
      <Field
        id="card-product"
        label="Which card is this?"
        hint={
          suggested && !row.linked
            ? `Looks like a ${suggested.short} from the name.`
            : "Not listed? Pick the flat rate that matches what it earns."
        }
      >
        <Select
          value={product}
          onChange={(e) => {
            setProduct(e.target.value);
            setChoice("");
          }}
        >
          <option value="" disabled>
            Pick a card
          </option>
          {suggested && (
            <optgroup label="Suggested">
              <option value={suggested.id}>{suggested.name}</option>
            </optgroup>
          )}
          {issuers.map((i) => (
            <optgroup key={i} label={i}>
              {page.catalog
                .filter((p) => p.issuer === i && p.id !== suggested?.id)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.annual_fee ? ` ($${p.annual_fee})` : ""}
                  </option>
                ))}
            </optgroup>
          ))}
        </Select>
      </Field>
      {chosen && chosen.choices.length > 0 && (
        <Field id="card-choice" label="Your bonus category" hint="The category you picked with the bank.">
          <Select value={choice || chosen.choices[0]!.id} onChange={(e) => setChoice(e.target.value)}>
            {chosen.choices.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </Select>
        </Field>
      )}
      <div>
        <p id="card-role" className="mb-1.5 text-sm font-medium">
          How you use it
        </p>
        <div role="radiogroup" aria-labelledby="card-role" className="grid grid-cols-3 rounded-full bg-muted p-1 text-sm">
          {ROLES.map(([r, label]) => (
            <button
              key={r}
              type="button"
              role="radio"
              aria-checked={role === r}
              onClick={() => setRole(r)}
              className={cn("rounded-full py-2 font-medium transition-colors", role === r ? "bg-card shadow-sm" : "text-muted-foreground")}
            >
              {label}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-muted-foreground">
          {role === "active"
            ? "Suggested for purchases."
            : role === "drawer"
              ? "Kept open but not suggested."
              : "Left out of suggestions and reports."}
        </p>
      </div>
      {chosen && chosen.annual_fee > 0 && (
        <Field id="card-renewal" label="Annual fee month" hint="So the app can remind you to check it's still worth the fee.">
          <Select value={renewal} onChange={(e) => setRenewal(e.target.value)}>
            <option value="">Not set</option>
            {MONTHS.map((m, i) => (
              <option key={m} value={i + 1}>
                {m}
              </option>
            ))}
          </Select>
        </Field>
      )}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        {row.linked && (
          <Button type="button" variant="ghost" disabled={busy} onClick={() => send("DELETE")}>
            Unlink
          </Button>
        )}
        <Button type="submit" disabled={busy || !product}>
          {row.linked ? "Save" : "Use this card"}
        </Button>
      </div>
    </form>
  );
}

/** What a point is worth in each program, in cents, so points and cash back compare fairly. */
export function ValuesSheet({
  page,
  open,
  onOpenChange,
  onSaved,
}: {
  page: CardsPage;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (page: CardsPage) => void;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="What points are worth"
      description="In cents per point. 1 cent is what most points get as cash back."
    >
      {open && <ValuesForm page={page} onSaved={onSaved} onDone={() => onOpenChange(false)} />}
    </Sheet>
  );
}

function ValuesForm({ page, onSaved, onDone }: { page: CardsPage; onSaved: (p: CardsPage) => void; onDone: () => void }) {
  // Only the programs your cards earn, unless no card is set up yet.
  const held = new Set(page.cards.filter((c) => c.product && c.role !== "closed").map((c) => c.product!.program));
  const points = page.programs.filter((p) => p.unit !== "%");
  const programs = points.some((p) => held.has(p.name)) ? points.filter((p) => held.has(p.name)) : points;
  const [text, setText] = useState<Record<string, string>>(() => Object.fromEntries(programs.map((p) => [p.id, String(p.cpp)])));
  const [busy, setBusy] = useState(false);
  const parsed = Object.fromEntries(Object.entries(text).map(([k, v]) => [k, Number(v)]));
  const bad = (id: string) => {
    const v = parsed[id] ?? NaN;
    return !(v > 0 && v <= 5);
  };
  const invalid = programs.some((p) => bad(p.id));

  const save = async () => {
    if (page.demo) return toast(DEMO);
    setBusy(true);
    try {
      onSaved(
        await api<CardsPage>("/api/cards/values", {
          method: "PUT",
          body: { cpp: parsed },
        }),
      );
      toast("Point values saved.");
      onDone();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="grid grid-cols-1 gap-4 pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (!invalid) save();
      }}
    >
      {programs.map((p) => (
        <Field
          key={p.id}
          id={`cpp-${p.id}`}
          label={p.name}
          error={bad(p.id) ? "Between 0.01 and 5 cents." : null}
          hint={
            p.transfers
              ? `Transfers to airlines and hotels, where points can be worth more. Default ${p.default}¢.`
              : `Default ${p.default}¢.`
          }
        >
          <Input
            inputMode="decimal"
            autoComplete="off"
            value={text[p.id] ?? ""}
            onChange={(e) => setText((t) => ({ ...t, [p.id]: e.target.value }))}
          />
        </Field>
      ))}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button
          type="button"
          variant="ghost"
          disabled={busy}
          onClick={() => setText(Object.fromEntries(programs.map((p) => [p.id, String(p.default)])))}
        >
          Use defaults
        </Button>
        <Button type="submit" disabled={busy || invalid}>
          Save
        </Button>
      </div>
    </form>
  );
}
