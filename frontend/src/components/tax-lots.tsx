"use client";

import { useState } from "react";
import { Clock } from "lucide-react";
import { Money } from "@/components/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { type TaxLot, type TaxLots, asOf } from "@/lib/portfolio";
import { cn } from "@/lib/utils";

// Dividends reinvested every quarter make lots of small lots; the first few show, the rest on request.
const FIRST = 8;
const shares = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 4 });
const month = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric" });

function acquired(l: TaxLot) {
  if (l.acquired) return asOf(l.acquired);
  return l.acquired_before ? `Before ${month.format(new Date(`${l.acquired_before}T12:00:00`))}` : "Earlier";
}

function perShare(l: TaxLot) {
  return l.cost_cents != null && l.quantity > 0 ? Math.round(l.cost_cents / l.quantity) : null;
}

function Term({ l }: { l: TaxLot }) {
  if (!l.taxable) return <Badge variant="muted">No tax here</Badge>;
  if (l.term === "long") return <Badge variant="positive">Long-term{l.estimated && l.acquired == null ? "*" : ""}</Badge>;
  if (l.term === "short") return <Badge variant="warning">Short-term</Badge>;
  return <Badge variant="muted">Term unknown</Badge>;
}

function Gain({ l, className }: { l: TaxLot; className?: string }) {
  if (l.gain_cents == null || l.cost_cents == null) return <span className={cn("text-muted-foreground", className)}>No cost</span>;
  const up = l.gain_cents >= 0;
  const pct = l.cost_cents > 0 ? (l.gain_cents / l.cost_cents) * 100 : null;
  return (
    <span className={cn("tabular-nums", up ? "text-positive" : "text-negative", className)}>
      <Money cents={l.gain_cents} signed />
      {pct != null && ` (${up ? "+" : ""}${pct.toFixed(1)}%)`}
    </span>
  );
}

function turns(l: TaxLot) {
  return l.taxable && l.term === "short" && l.long_term_on ? `Long-term on ${asOf(l.long_term_on)}` : null;
}

function LotList({ rows }: { rows: TaxLot[] }) {
  return (
    <ul className="divide-y md:hidden">
      {rows.map((l, i) => {
        const each = perShare(l);
        return (
          <li key={i} className="py-3 text-sm">
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-medium">{acquired(l)}</span>
              <Money cents={l.value_cents} className="font-medium tabular-nums" />
            </div>
            <div className="mt-0.5 flex items-baseline justify-between gap-3 text-xs">
              <span className="text-muted-foreground tabular-nums">
                <span className="money">{shares(l.quantity)}</span> sh
                {each != null && (
                  <>
                    {" "}at <Money cents={each} style="exact" />
                  </>
                )}
              </span>
              <Gain l={l} />
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <Term l={l} />
              {turns(l) && <span className="text-xs text-muted-foreground">{turns(l)}</span>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function LotTable({ rows }: { rows: TaxLot[] }) {
  return (
    <table className="hidden w-full table-fixed text-sm md:table">
      <colgroup>
        <col className="w-[15%]" />
        <col className="w-[11%]" />
        <col className="w-[11%]" />
        <col className="w-[11%]" />
        <col className="w-[11%]" />
        <col className="w-[21%]" />
        <col className="w-[20%]" />
      </colgroup>
      <thead>
        <tr className="border-b text-left text-xs text-muted-foreground">
          <th scope="col" className="py-2 pr-3 font-medium">Bought</th>
          <th scope="col" className="py-2 pr-3 text-right font-medium">Shares</th>
          <th scope="col" className="py-2 pr-3 text-right font-medium">Per share</th>
          <th scope="col" className="py-2 pr-3 text-right font-medium">Cost</th>
          <th scope="col" className="py-2 pr-3 text-right font-medium">Value</th>
          <th scope="col" className="py-2 pr-3 text-right font-medium">Gain</th>
          <th scope="col" className="py-2 pl-3 font-medium">Term</th>
        </tr>
      </thead>
      <tbody className="divide-y">
        {rows.map((l, i) => {
          const each = perShare(l);
          return (
            <tr key={i}>
              <td className="py-2.5 pr-3 font-medium whitespace-nowrap">{acquired(l)}</td>
              <td className="py-2.5 pr-3 text-right tabular-nums">
                <span className="money">{shares(l.quantity)}</span>
              </td>
              <td className="py-2.5 pr-3 text-right tabular-nums">{each != null ? <Money cents={each} style="exact" /> : "—"}</td>
              <td className="py-2.5 pr-3 text-right tabular-nums">{l.cost_cents != null ? <Money cents={l.cost_cents} /> : "—"}</td>
              <td className="py-2.5 pr-3 text-right tabular-nums">
                <Money cents={l.value_cents} />
              </td>
              <td className="py-2.5 pr-3 text-right whitespace-nowrap">
                <Gain l={l} />
              </td>
              <td className="py-2.5 pl-3">
                <div className="flex flex-col items-start gap-0.5">
                  <Term l={l} />
                  {turns(l) && <span className="text-xs text-muted-foreground">{turns(l)}</span>}
                </div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Group({ account, rows, showName }: { account: string; rows: TaxLot[]; showName: boolean }) {
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, FIRST);
  return (
    <div className="mt-4 first:mt-0">
      {showName && (
        <div className="mb-1 flex items-center gap-2">
          <h3 className="text-sm font-semibold">{account}</h3>
          {!rows[0]!.taxable && <span className="text-xs text-muted-foreground">tax-advantaged</span>}
        </div>
      )}
      <LotList rows={shown} />
      <LotTable rows={shown} />
      {rows.length > FIRST && (
        <Button variant="ghost" size="sm" className="mt-1 -ml-2" onClick={() => setAll(!all)} aria-expanded={all}>
          {all ? "Show fewer" : `Show all ${rows.length} lots`}
        </Button>
      )}
    </div>
  );
}

function Tile({ label, cents, hint }: { label: string; cents: number; hint: string }) {
  return (
    <div className="rounded-lg bg-muted/60 px-3 py-2.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 text-lg font-semibold tracking-tight", cents >= 0 ? "text-positive" : "text-negative")}>
        <Money cents={cents} signed />
      </p>
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

export function TaxLotsCard({ lots }: { lots: TaxLots }) {
  const groups = new Map<number, TaxLot[]>();
  for (const l of lots.rows) groups.set(l.account_id, [...(groups.get(l.account_id) ?? []), l]);
  const next = lots.next_long_term;
  const earlier = lots.rows.some((l) => l.acquired == null);
  const guessed = lots.rows.some((l) => l.acquired == null && l.term === "long" && l.estimated);
  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Tax lots</CardTitle>
        <Badge variant="muted">
          {lots.lot_count} {lots.lot_count === 1 ? "lot" : "lots"}
        </Badge>
      </CardHeader>
      <CardContent>
        {lots.has_taxable ? (
          <div className="mb-4 grid grid-cols-2 gap-3">
            <Tile label="Long-term gain" cents={lots.long_gain_cents} hint="Held over a year: lower tax rate" />
            <Tile label="Short-term gain" cents={lots.short_gain_cents} hint="Taxed like your pay if sold" />
          </div>
        ) : (
          <p className="mb-3 text-sm text-muted-foreground">
            These shares are in a tax-advantaged account, so selling them there isn&apos;t taxed and how long you&apos;ve held them
            doesn&apos;t matter.
          </p>
        )}
        {next && (
          <p className="mb-4 flex gap-2.5 rounded-lg border px-3 py-2.5 text-sm">
            <Clock className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
            <span>
              <span className="money">{shares(next.quantity)}</span> shares with a <Money cents={next.gain_cents} /> gain turn long-term{" "}
              {next.days <= 1 ? "tomorrow" : `in ${next.days} days`} ({asOf(next.on)}). Selling them after that usually means less tax.
            </span>
          </p>
        )}
        {/* Taxable accounts first: that's where a lot's term matters. */}
        {[...groups.entries()].sort(([, a], [, b]) => Number(b[0]!.taxable) - Number(a[0]!.taxable)).map(([id, rows]) => (
          <Group key={id} account={rows[0]!.account} rows={rows} showName={groups.size > 1 || !rows[0]!.taxable} />
        ))}
        <p className="mt-3 text-xs text-muted-foreground">
          Built from your brokerage&apos;s buys and sells, oldest shares sold first. Its own lots can differ if you picked lots
          when selling, if it uses average cost (common for mutual funds), or after a wash sale.
          {earlier &&
            " “Before” lots are shares older than the two years of activity the brokerage shares; their cost is what's left of its total cost basis."}
          {guessed && " *Long-term because they're more than two years old."}
        </p>
      </CardContent>
    </Card>
  );
}
