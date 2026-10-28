"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Bookmark, Check, ClipboardPaste, Copy, ExternalLink, Globe, Search, Store, Tag, X } from "lucide-react";
import { Money } from "@/components/money";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { type CardsPage, shortDay } from "@/lib/cards";
import { type OfferItem, type ReadOffers, type Sent, OFFER_PAGES, bookmarklet } from "@/lib/offers";
import { cn } from "@/lib/utils";

const DEMO = "Saving offers is off while the app shows sample data.";
const SHOWN = 8;

// ---- the list on the Credit Cards page

export function OffersCard({
  s,
  onSaved,
  onAdd,
  onSetup,
}: {
  s: CardsPage;
  onSaved: (p: CardsPage) => void;
  onAdd: () => void;
  onSetup: () => void;
}) {
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);
  const items = s.offers.items;
  const query = q.trim().toLowerCase();
  const shown = query ? items.filter((o) => o.merchant.toLowerCase().includes(query)) : items;
  const yours = items.filter((o) => o.visits > 0).length;
  const remove = async (o: OfferItem) => {
    if (s.demo) return toast(DEMO);
    try {
      onSaved(await api<CardsPage>(`/api/cards/offers/${o.id}`, { method: "DELETE" }));
      toast(`Took off ${o.merchant}.`);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <Card className="md:col-span-2">
      <CardHeader>
        <CardTitle>Card offers</CardTitle>
        {yours > 0 && <Badge>{yours === 1 ? "1 where you shop" : `${yours} where you shop`}</Badge>}
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        {items.length === 0 ? (
          <div className="grid grid-cols-1 gap-3">
            <p className="text-sm text-muted-foreground">
              Bring in your Chase Offers and Capital One Offers, and the store lookup above counts them on top of each card&apos;s
              rewards. One tap on a bookmark while the bank&apos;s offers page is open.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={onSetup}>
                <Bookmark aria-hidden /> Set up the bookmark
              </Button>
              <Button size="sm" variant="secondary" onClick={onAdd}>
                <ClipboardPaste aria-hidden /> Paste offers
              </Button>
            </div>
          </div>
        ) : (
          <>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {s.offers.cards.map((c) => (
                <li key={c.account_id}>
                  <span className="font-medium text-foreground">{c.card}</span> · {c.count} offer{c.count === 1 ? "" : "s"}
                  {c.seen && ` · sent ${shortDay.format(new Date(c.seen + "T12:00:00"))}`}
                </li>
              ))}
            </ul>
            {items.length > SHOWN && (
              <div className="relative">
                <Search className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
                <Input
                  type="search"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="Find a store"
                  aria-label="Find a store in your offers"
                  className="pl-10"
                  autoComplete="off"
                />
              </div>
            )}
            {shown.length === 0 ? (
              <p className="text-sm text-muted-foreground">No offer at a store called that.</p>
            ) : (
              <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {(all || query ? shown : shown.slice(0, SHOWN)).map((o) => (
                  <OfferRow key={o.id} o={o} onRemove={() => remove(o)} />
                ))}
              </ul>
            )}
            {!query && items.length > SHOWN && (
              <Button variant="link" className="justify-self-start" onClick={() => setAll((a) => !a)}>
                {all ? "Show fewer" : `Show all ${items.length}`}
              </Button>
            )}
            <div className="flex flex-wrap gap-2 border-t pt-4">
              <Button size="sm" variant="secondary" onClick={onSetup}>
                <Bookmark aria-hidden /> Send again
              </Button>
              <Button size="sm" variant="ghost" onClick={onAdd}>
                <ClipboardPaste aria-hidden /> Paste offers
              </Button>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function OfferRow({ o, onRemove }: { o: OfferItem; onRemove: () => void }) {
  return (
    <li className="flex items-start gap-3 rounded-2xl border p-3.5">
      <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl bg-accent text-accent-foreground" aria-hidden>
        <Tag className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <p className="truncate font-medium">{o.merchant}</p>
          <p className="shrink-0 font-semibold tabular-nums">{o.label}</p>
        </div>
        <p className="text-xs text-muted-foreground">
          {o.name} on {o.card}
          {o.channel && ` · ${o.channel === "online" ? "online only" : "in store only"}`}
          {o.max_cents ? ` · up to $${o.max_cents / 100} back` : ""}
          {o.min_spend_cents ? ` · on $${o.min_spend_cents / 100}+` : ""}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {o.visits > 0 && (
            <Badge variant="positive">
              You shop here · {o.visits}× · <Money cents={o.spent_cents} />
            </Badge>
          )}
          {o.ends && <Badge variant={o.soon ? "warning" : "muted"}>{o.ends}</Badge>}
          {o.added === true && (
            <Badge variant="muted">
              <Check className="size-3" aria-hidden /> Added
            </Badge>
          )}
          {o.added === false && <Badge variant="warning">Not added yet</Badge>}
        </div>
      </div>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Take off ${o.merchant}`}
        title="Used it, or not interested"
        className="-mt-1 -mr-1 grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground hover:bg-muted"
      >
        <X className="size-4" aria-hidden />
      </button>
    </li>
  );
}

// ---- bringing offers in: from the bookmark, or pasted

export function ImportSheet({
  page,
  sent,
  open,
  onOpenChange,
  onSaved,
}: {
  page: CardsPage;
  sent: Sent | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (p: CardsPage) => void;
}) {
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title={sent ? "Offers from the bank" : "Paste offers"}
      description={sent ? `Sent from ${sent.site || "the offers page"}. Check them, then save.` : undefined}
    >
      {open && <ImportForm page={page} sent={sent} onSaved={onSaved} onDone={() => onOpenChange(false)} />}
    </Sheet>
  );
}

function ImportForm({ page, sent, onSaved, onDone }: { page: CardsPage; sent: Sent | null; onSaved: (p: CardsPage) => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [read, setRead] = useState<ReadOffers | null>(null);
  const [account, setAccount] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  const go = async (body: Sent) => {
    if (page.demo) return setError("Reading offers is off while the app shows sample data.");
    setBusy(true);
    setError(null);
    try {
      const r = await api<ReadOffers>("/api/cards/offers/read", { body });
      setRead(r);
      setAccount(r.account_id ? String(r.account_id) : "");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (sent && !started.current) {
      started.current = true;
      void go(sent);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sent]);

  const save = async () => {
    if (!read || !account) return;
    setBusy(true);
    try {
      const next = await api<CardsPage>(`/api/cards/${account}/offers`, {
        method: "PUT",
        body: { issuer: read.issuer, offers: read.offers },
      });
      onSaved(next);
      const card = read.accounts.find((a) => String(a.account_id) === account)?.label ?? "the card";
      toast(`Saved ${read.offers.length} offer${read.offers.length === 1 ? "" : "s"} on ${card}.`);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!read) {
    return (
      <div className="grid grid-cols-1 gap-4">
        {sent ? (
          <p className="text-sm text-muted-foreground" role="status">
            {busy ? "Reading the offers…" : null}
          </p>
        ) : (
          <>
            <Field
              id="offers-text"
              label="The offers page's text"
              hint="On the Chase Offers or Capital One Offers page: select all, copy, and paste here. Only the offers are kept, not the page."
            >
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={7}
                className="w-full rounded-xl border bg-background px-3.5 py-3 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/20 md:text-sm"
                placeholder={"Food Lion\n5% cash back\nTommy Hilfiger\n15% cash back\nExpiring soon"}
              />
            </Field>
            <Button disabled={busy || !text.trim()} onClick={() => go({ text, site: "" })}>
              {busy ? "Reading…" : "Read offers"}
            </Button>
          </>
        )}
        {error && (
          <p className="text-sm text-negative" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  const n = read.offers.length;
  return (
    <div className="grid grid-cols-1 gap-4">
      {n === 0 ? (
        <div className="flex items-start gap-2 rounded-2xl bg-warning/10 p-4 text-sm text-warning" role="status">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            No offers found on that page. Open the offers list itself (Chase Offers under a card, or Capital One Offers), scroll so the
            offers load, and send again.
          </span>
        </div>
      ) : (
        <p className="text-sm">
          Found <span className="font-semibold">{n}</span> {read.issuer_name === "Card" ? "" : `${read.issuer_name} `}offer{n === 1 ? "" : "s"}
          {read.card_name && (
            <>
              {" "}
              for <span className="font-semibold">{read.card_name}</span>
              {read.mask && ` ••${read.mask}`}
            </>
          )}
          .
        </p>
      )}
      {n > 0 && (
        <Field
          id="offers-card"
          label="Which card are they on?"
          hint={read.account_id ? "Matched by the card's last four digits." : "The page didn't say which card. Pick it."}
        >
          <Select value={account} onChange={(e) => setAccount(e.target.value)}>
            <option value="" disabled>
              Pick a card
            </option>
            {read.accounts.map((a) => (
              <option key={a.account_id} value={a.account_id}>
                {a.label}
              </option>
            ))}
          </Select>
        </Field>
      )}
      {n > 0 && (
        <ul className="grid max-h-72 grid-cols-1 gap-1 overflow-y-auto rounded-2xl border p-2 text-sm" aria-label="Offers found">
          {read.offers.map((o) => (
            <li key={o.merchant} className="flex items-center gap-2 rounded-lg px-2 py-1.5">
              {o.channel === "online" ? (
                <Globe className="size-3.5 shrink-0 text-muted-foreground" aria-label="Online only" />
              ) : o.channel === "in store" ? (
                <Store className="size-3.5 shrink-0 text-muted-foreground" aria-label="In store only" />
              ) : (
                <Tag className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
              )}
              <span className="min-w-0 flex-1 truncate">{o.merchant}</span>
              {(o.soon || o.expires) && (
                <span className="shrink-0 text-xs text-muted-foreground">
                  {o.expires ? `ends ${shortDay.format(new Date(o.expires + "T12:00:00"))}` : "ends soon"}
                </span>
              )}
              <span className="shrink-0 font-medium tabular-nums">{o.label}</span>
            </li>
          ))}
        </ul>
      )}
      {n > 0 && (
        <p className="text-xs text-muted-foreground">Saving replaces this card&apos;s offers: ones no longer on the page were used or have ended.</p>
      )}
      {error && (
        <p className="text-sm text-negative" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        {n > 0 && (
          <Button className="flex-1" disabled={busy || !account} onClick={save}>
            {busy ? "Saving…" : `Save ${n} offer${n === 1 ? "" : "s"}`}
          </Button>
        )}
        <Button variant="secondary" className={n > 0 ? "" : "flex-1"} onClick={onDone}>
          {n > 0 ? "Cancel" : "Close"}
        </Button>
      </div>
    </div>
  );
}

// ---- setting up the bookmark

export function BookmarkSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Send offers with one tap"
      description="A bookmark that reads the offers page you have open and sends the offers here. It never sees your bank password."
    >
      {open && <BookmarkSteps />}
    </Sheet>
  );
}

function BookmarkSteps() {
  const code = useMemo(() => bookmarklet(window.location.origin), []);
  const link = useRef<HTMLAnchorElement>(null);
  const [copied, setCopied] = useState(false);
  // React won't render a javascript: link, so it's set directly, for dragging to the bookmarks bar on a laptop.
  useEffect(() => {
    link.current?.setAttribute("href", code);
  }, [code]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      toast("Copied. Paste it as the bookmark's address.");
    } catch {
      toast("Couldn't copy. Select the code below and copy it.", "error");
    }
  };

  return (
    <div className="grid grid-cols-1 gap-5 text-sm">
      <section className="grid grid-cols-1 gap-2">
        <h3 className="font-semibold">Once, on your iPhone</h3>
        <ol className="grid list-decimal gap-1.5 pl-5 text-muted-foreground">
          <li>
            Tap <span className="font-medium text-foreground">Copy the bookmark</span> below.
          </li>
          <li>In Safari, bookmark any page (Share, Add Bookmark) and name it &quot;Send to Finance&quot;.</li>
          <li>Open Bookmarks, tap Edit, open the new bookmark, clear its address and paste. Save.</li>
        </ol>
        <Button onClick={copy} className="justify-self-start">
          {copied ? <Check aria-hidden /> : <Copy aria-hidden />} {copied ? "Copied" : "Copy the bookmark"}
        </Button>
      </section>
      <section className="grid grid-cols-1 gap-2">
        <h3 className="font-semibold">Once, on a laptop</h3>
        <p className="text-muted-foreground">Drag this to your bookmarks bar:</p>
        <a
          ref={link}
          onClick={(e) => {
            e.preventDefault();
            toast("Drag it to the bookmarks bar instead.");
          }}
          className="inline-flex w-fit items-center gap-1.5 rounded-full border bg-card px-3.5 py-1.5 font-medium shadow-sm"
        >
          <Bookmark className="size-4 text-primary" aria-hidden /> Send to Finance
        </a>
      </section>
      <section className="grid grid-cols-1 gap-2">
        <h3 className="font-semibold">Each time, about once a week</h3>
        <ol className="grid list-decimal gap-1.5 pl-5 text-muted-foreground">
          <li>Open an offers page and sign in as usual (Apple Passwords fills it in):</li>
        </ol>
        <ul className="grid grid-cols-1 gap-2">
          {OFFER_PAGES.map((p) => (
            <li key={p.issuer}>
              <a
                href={p.url}
                target="_blank"
                rel="noreferrer"
                className={cn("flex items-center gap-3 rounded-2xl border p-3 hover:bg-muted")}
              >
                <ExternalLink className="size-4 shrink-0 text-primary" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{p.name}</span>
                  <span className="block text-xs text-muted-foreground">{p.hint}</span>
                </span>
              </a>
            </li>
          ))}
        </ul>
        <ol start={2} className="grid list-decimal gap-1.5 pl-5 text-muted-foreground">
          <li>Scroll to the bottom so every offer loads.</li>
          <li>
            On iPhone, tap the address bar and type <span className="font-medium text-foreground">Send</span>, then tap the bookmark. On a laptop,
            click it.
          </li>
          <li>This app opens with the offers. Check the card and tap Save. Do it once per card.</li>
        </ol>
      </section>
      <details className="rounded-2xl border px-4 py-3">
        <summary className="cursor-pointer font-medium">What it does and doesn&apos;t do</summary>
        <ul className="mt-2 grid list-disc gap-1.5 pl-5 text-muted-foreground">
          <li>It runs only when you tap it, on the page you have open. Nothing runs in the background.</li>
          <li>It keeps only the lines around each offer and the card&apos;s name, and puts them after a # in this app&apos;s address, which browsers never send to any server. Only the offers read from them are saved.</li>
          <li>It doesn&apos;t sign in, store passwords or tap &quot;+&quot; to add offers. The bank never talks to this app.</li>
          <li>Offers come from the page&apos;s words, so if the bank changes its page and nothing is found, paste the page&apos;s text instead.</li>
        </ul>
      </details>
      <details className="rounded-2xl border px-4 py-3">
        <summary className="cursor-pointer font-medium">The bookmark&apos;s code</summary>
        <pre className="mt-2 max-h-40 overflow-auto rounded-xl bg-muted p-3 text-xs break-all whitespace-pre-wrap">{code}</pre>
      </details>
    </div>
  );
}
