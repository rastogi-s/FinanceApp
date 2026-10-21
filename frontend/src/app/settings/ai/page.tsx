"use client";

import { useState } from "react";
import {
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  ExternalLink,
  EyeOff,
  FileSearch,
  Home,
  KeyRound,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Wallet,
} from "lucide-react";
import { SubPageTitle } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { type AIKind, type AIOverview, type AIProviderRow, type AITask, usd } from "@/lib/ai";
import { api } from "@/lib/api";
import { useApi } from "@/lib/household";
import { cn } from "@/lib/utils";

const when = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const tokens = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

const BLURB: Record<AIKind["kind"], string> = {
  anthropic: "Claude, from Anthropic. Very good at reading statements and pay stubs.",
  openai: "GPT models, from OpenAI.",
  gemini: "Gemini models, from Google.",
  openai_compatible: "Ollama, LM Studio or another server that speaks the OpenAI API. On your own computer, nothing leaves home.",
};

export default function AIPage() {
  const loaded = useApi<AIOverview>("/api/ai");
  const [fresh, setFresh] = useState<AIOverview | null>(null);
  const data = fresh ?? loaded.data;
  const [adding, setAdding] = useState(false);

  // Every change returns the whole page's data, so the page updates without another request.
  const change = async (path: string, options: Parameters<typeof api>[1], done?: string): Promise<AIOverview | null> => {
    try {
      const next = await api<AIOverview>(path, options);
      setFresh(next);
      if (next.error) toast(next.error, "error");
      else if (done) toast(done);
      return next;
    } catch (e) {
      toast((e as Error).message, "error");
      return null;
    }
  };

  return (
    <>
      <SubPageTitle title="AI" back="/settings" subtitle="Let AI read the files the app can't. Off until you set it up." />
      <div className="grid grid-cols-1 gap-4">
        <PrivacyPromise />
        {data === null ? (
          <>
            <Skeleton className="h-36 rounded-xl" />
            <Skeleton className="h-28 rounded-xl" />
          </>
        ) : (
          <>
            <Providers data={data} change={change} onAdd={() => setAdding(true)} />
            {data.providers.length > 0 && <Tasks data={data} change={change} />}
            <HideWords data={data} change={change} />
            <Spending data={data} change={change} />
          </>
        )}
      </div>
      {data && (
        <AddProviderSheet
          open={adding}
          onOpenChange={setAdding}
          kinds={data.kinds}
          onAdded={(next) => {
            setFresh(next);
            setAdding(false);
          }}
        />
      )}
    </>
  );
}

type Change = (path: string, options: Parameters<typeof api>[1], done?: string) => Promise<AIOverview | null>;

function PrivacyPromise() {
  const points = [
    { icon: <ShieldCheck />, title: "Nothing is sent until you say so", body: "Only to a provider you add here, and only for the jobs you pick." },
    {
      icon: <EyeOff />,
      title: "Identifying details come out first",
      body: "Social Security and account numbers (all but the last 4 digits), names, addresses, emails and phone numbers.",
    },
    { icon: <FileSearch />, title: "You check before anything is saved", body: "You see what will be sent, then what came back." },
    { icon: <KeyRound />, title: "Keys are locked away", body: "Encrypted on this server and never shown again, not even to you." },
  ];
  return (
    <Card>
      <CardContent className="grid grid-cols-1 gap-4 pt-5 sm:grid-cols-2">
        {points.map((p) => (
          <div key={p.title} className="flex gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground [&_svg]:size-4" aria-hidden>
              {p.icon}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium">{p.title}</p>
              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{p.body}</p>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

// ---- providers

function Providers({ data, change, onAdd }: { data: AIOverview; change: Change; onAdd: () => void }) {
  const [rekey, setRekey] = useState<AIProviderRow | null>(null);
  const [removing, setRemoving] = useState<AIProviderRow | null>(null);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Providers</CardTitle>
        <BrainCircuit className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        {data.providers.length === 0 ? (
          <div className="rounded-xl bg-muted/60 px-4 py-4 text-sm">
            <p className="font-medium">No AI yet</p>
            <p className="mt-1 text-muted-foreground">
              Uploads of CSV and OFX files work without it. Add a provider to also read PDFs, spreadsheets, screenshots and scanned pay stubs.
            </p>
          </div>
        ) : (
          <ul className="grid grid-cols-1 gap-3">
            {data.providers.map((p) => (
              <ProviderRow
                key={p.id}
                p={p}
                canEdit={data.can_edit}
                change={change}
                onRekey={() => setRekey(p)}
                onRemove={() => setRemoving(p)}
              />
            ))}
          </ul>
        )}
        {data.can_edit ? (
          <Button variant={data.providers.length ? "outline" : "default"} onClick={onAdd} className="justify-self-start" disabled={!data.encryption}>
            <Plus /> Add a provider
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">Only the household&apos;s owner can change AI settings.</p>
        )}
        {!data.encryption && <p className="text-sm text-warning">The server has no encryption key, so it can&apos;t store API keys yet.</p>}
      </CardContent>
      <KeySheet provider={rekey} onOpenChange={(o) => !o && setRekey(null)} change={change} />
      <RemoveSheet provider={removing} onOpenChange={(o) => !o && setRemoving(null)} change={change} />
    </Card>
  );
}

function Mark({ kind }: { kind: AIProviderRow["kind"] }) {
  if (kind === "openai_compatible") {
    return (
      <span className="grid size-10 shrink-0 place-items-center rounded-full bg-positive/10 text-positive" aria-hidden>
        <Home className="size-5" />
      </span>
    );
  }
  const letter = { anthropic: "C", openai: "O", gemini: "G" }[kind];
  return (
    <span className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-base font-semibold text-accent-foreground" aria-hidden>
      {letter}
    </span>
  );
}

function ProviderRow({
  p,
  canEdit,
  change,
  onRekey,
  onRemove,
}: {
  p: AIProviderRow;
  canEdit: boolean;
  change: Change;
  onRekey: () => void;
  onRemove: () => void;
}) {
  const [checking, setChecking] = useState(false);
  const local = p.kind === "openai_compatible";
  const recheck = async () => {
    setChecking(true);
    await change(`/api/ai/providers/${p.id}/check`, { method: "POST" }, `${p.label} works.`);
    setChecking(false);
  };
  return (
    <li className={cn("grid grid-cols-1 gap-3 rounded-2xl border p-4", !p.enabled && "bg-muted/30")}>
      <div className="flex items-start gap-3">
        <Mark kind={p.kind} />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            {p.label}
            {p.enabled ? <Badge variant="positive">On</Badge> : <Badge variant="muted">Off</Badge>}
          </p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {local ? p.base_url : p.kind_name}
            {p.key_hint && ` · key ending ${p.key_hint}`}
            {p.models.length > 0 && ` · ${p.models.length} model${p.models.length === 1 ? "" : "s"}`}
          </p>
        </div>
      </div>
      {p.last_error && (
        <p className="flex items-start gap-2 rounded-xl bg-negative/10 px-3 py-2 text-xs font-medium text-negative">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden /> {p.last_error}
        </p>
      )}
      <div className="grid grid-cols-1 gap-1 border-t pt-2">
        <Switch
          checked={p.enabled}
          onCheckedChange={(enabled) =>
            change(`/api/ai/providers/${p.id}`, { method: "PATCH", body: { enabled } }, enabled ? `${p.label} is on.` : `${p.label} is off.`)
          }
          label="Use this provider"
          description="Off stops every request to it straight away."
          disabled={!canEdit}
        />
        <Switch
          checked={p.allow_pictures}
          onCheckedChange={(allow_pictures) => change(`/api/ai/providers/${p.id}`, { method: "PATCH", body: { allow_pictures } })}
          label="Send pictures and scans"
          description={
            local
              ? "Screenshots and scanned pages stay on your network."
              : "Screenshots and scanned pages can't have details taken out first, so they go as they are. You're asked each time."
          }
          disabled={!canEdit}
        />
      </div>
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={recheck} disabled={checking}>
            <RefreshCw className={cn(checking && "animate-spin")} /> {checking ? "Checking…" : "Check"}
          </Button>
          <Button variant="secondary" size="sm" onClick={onRekey}>
            <KeyRound /> {local ? "Change address" : "Replace key"}
          </Button>
          <Button variant="ghost" size="sm" onClick={onRemove} className="text-negative hover:text-negative">
            <Trash2 /> Remove
          </Button>
        </div>
      )}
    </li>
  );
}

function AddProviderSheet({
  open,
  onOpenChange,
  kinds,
  onAdded,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  kinds: AIKind[];
  onAdded: (data: AIOverview) => void;
}) {
  const [kind, setKind] = useState<AIKind | null>(null);
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("http://localhost:11434/v1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setKind(null);
    setKey("");
    setLabel("");
    setError(null);
  };
  const close = (o: boolean) => {
    if (!o) reset();
    onOpenChange(o);
  };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!kind) return;
    setBusy(true);
    setError(null);
    try {
      const next = await api<AIOverview>("/api/ai/providers", {
        body: { kind: kind.kind, api_key: key, label, base_url: kind.kind === "openai_compatible" ? baseUrl : "" },
      });
      toast(`${label || kind.name} is set up.`);
      reset();
      onAdded(next);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet
      open={open}
      onOpenChange={close}
      title={kind ? `Add ${kind.name}` : "Add an AI provider"}
      description={kind ? "The key is checked by asking for the list of models, which costs nothing." : "You can add more than one and pick which does what."}
    >
      {!kind ? (
        <ul className="grid grid-cols-1 gap-2 pb-2">
          {kinds.map((k) => (
            <li key={k.kind}>
              <button
                type="button"
                onClick={() => setKind(k)}
                className="flex w-full items-center gap-3 rounded-2xl border p-4 text-left transition-colors hover:bg-muted/50"
              >
                <Mark kind={k.kind} />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{k.name}</span>
                  <span className="mt-0.5 block text-xs text-muted-foreground">{BLURB[k.kind]}</span>
                </span>
                <ChevronRight className="size-5 shrink-0 text-muted-foreground" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <form onSubmit={save} className="grid grid-cols-1 gap-4 pb-2">
          {kind.kind === "openai_compatible" && (
            <Field id="ai-url" label="Server address" hint="Ollama: http://localhost:11434/v1 (or your computer's address if the app runs elsewhere).">
              <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} inputMode="url" autoComplete="off" spellCheck={false} required />
            </Field>
          )}
          <Field
            id="ai-key"
            label={kind.needs_key ? "API key" : "API key (if the server needs one)"}
            hint={
              kind.key_url ? (
                <>
                  Make one at{" "}
                  <a href={kind.key_url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 font-medium text-primary">
                    {new URL(kind.key_url).host} <ExternalLink className="size-3" aria-hidden />
                  </a>
                  . Give it a small monthly limit there too.
                </>
              ) : undefined
            }
          >
            <Input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              required={kind.needs_key}
            />
          </Field>
          <Field id="ai-label" label="Name (optional)">
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder={kind.name} maxLength={80} />
          </Field>
          {error && (
            <p role="alert" className="flex items-start gap-2 text-sm font-medium text-negative">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden /> {error}
            </p>
          )}
          <div className="grid grid-cols-2 gap-2">
            <Button type="button" variant="outline" size="lg" onClick={() => setKind(null)} disabled={busy}>
              Back
            </Button>
            <Button type="submit" size="lg" disabled={busy || (kind.needs_key && !key.trim())}>
              {busy ? "Checking…" : "Check and save"}
            </Button>
          </div>
        </form>
      )}
    </Sheet>
  );
}

function KeySheet({ provider, onOpenChange, change }: { provider: AIProviderRow | null; onOpenChange: (o: boolean) => void; change: Change }) {
  const [key, setKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const local = provider?.kind === "openai_compatible";
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!provider) return;
    setBusy(true);
    const next = await change(
      `/api/ai/providers/${provider.id}/key`,
      { method: "PUT", body: { api_key: key, base_url: local ? baseUrl || provider.base_url : "" } },
      "Saved. The new key works.",
    );
    setBusy(false);
    if (next) {
      setKey("");
      setBaseUrl("");
      onOpenChange(false);
    }
  };
  return (
    <Sheet
      open={provider !== null}
      onOpenChange={onOpenChange}
      title={local ? "Change the server" : "Replace the key"}
      description="It's checked first: if it doesn't work, the old one stays."
    >
      <form onSubmit={save} className="grid grid-cols-1 gap-4 pb-2">
        {local && (
          <Field id="rk-url" label="Server address">
            <Input value={baseUrl} placeholder={provider?.base_url ?? ""} onChange={(e) => setBaseUrl(e.target.value)} inputMode="url" spellCheck={false} />
          </Field>
        )}
        <Field id="rk-key" label={local ? "API key (if the server needs one)" : "New API key"}>
          <Input type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" spellCheck={false} required={!local} />
        </Field>
        <Button type="submit" size="lg" disabled={busy || (!local && !key.trim())}>
          {busy ? "Checking…" : "Check and save"}
        </Button>
      </form>
    </Sheet>
  );
}

function RemoveSheet({ provider, onOpenChange, change }: { provider: AIProviderRow | null; onOpenChange: (o: boolean) => void; change: Change }) {
  const [busy, setBusy] = useState(false);
  const remove = async () => {
    if (!provider) return;
    setBusy(true);
    const next = await change(`/api/ai/providers/${provider.id}`, { method: "DELETE" }, `${provider.label} removed.`);
    setBusy(false);
    if (next) onOpenChange(false);
  };
  return (
    <Sheet
      open={provider !== null}
      onOpenChange={onOpenChange}
      title={`Remove ${provider?.label ?? ""}?`}
      description="Its key is deleted from this server. Jobs it did stop until you pick another provider. Revoke the key at the provider too."
    >
      <div className="grid grid-cols-2 gap-2 pb-2">
        <Button variant="outline" size="lg" onClick={() => onOpenChange(false)}>
          Keep it
        </Button>
        <Button variant="destructive" size="lg" onClick={remove} disabled={busy}>
          Remove
        </Button>
      </div>
    </Sheet>
  );
}

// ---- which model does what

function Tasks({ data, change }: { data: AIOverview; change: Change }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>What AI does</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        {data.tasks.map((t) => (
          <TaskRow key={t.id} task={t} data={data} change={change} />
        ))}
      </CardContent>
    </Card>
  );
}

function TaskRow({ task: t, data, change }: { task: AITask; data: AIOverview; change: Change }) {
  const [trying, setTrying] = useState(false);
  const [tried, setTried] = useState<AIOverview["tried"] | null>(null);
  const provider = data.providers.find((p) => p.id === t.route?.provider_id) ?? null;
  const models = provider?.models ?? [];
  const [priceIn, setPriceIn] = useState(t.route?.price_in?.toString() ?? "");
  const [priceOut, setPriceOut] = useState(t.route?.price_out?.toString() ?? "");
  const guessing = t.price !== null && !t.price.known;

  const route = (patch: Partial<NonNullable<AITask["route"]>> | null) =>
    change("/api/ai/settings", { method: "PUT", body: { routes: { [t.id]: patch === null ? null : { ...t.route, ...patch } } } });

  const pickProvider = (id: number) => {
    const p = data.providers.find((x) => x.id === id);
    const kind = data.kinds.find((k) => k.kind === p?.kind);
    const model = p?.models.includes(kind?.suggested_model ?? "") ? kind!.suggested_model! : (p?.models[0] ?? "");
    if (model) route({ provider_id: id, model, price_in: undefined, price_out: undefined });
  };

  const savePrices = () => {
    const i = parseFloat(priceIn);
    const o = parseFloat(priceOut);
    if (Number.isFinite(i) && Number.isFinite(o)) route({ price_in: i, price_out: o }).then((n) => n && toast("Prices saved."));
  };

  const test = async () => {
    setTrying(true);
    setTried(null);
    const next = await change("/api/ai/try", { body: { task: t.id } });
    if (next?.tried) setTried(next.tried);
    setTrying(false);
  };

  return (
    <div className="grid grid-cols-1 gap-3 rounded-2xl border p-4">
      <div>
        <p className="flex items-center gap-2 font-medium">
          {t.title}
          {t.ready ? <Badge variant="positive">Ready</Badge> : <Badge variant="muted">Not set up</Badge>}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{t.detail}</p>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Select
          aria-label={`Provider for ${t.title.toLowerCase()}`}
          value={t.route?.provider_id ?? ""}
          onChange={(e) => (e.target.value ? pickProvider(Number(e.target.value)) : route(null))}
          disabled={!data.can_edit}
        >
          <option value="">Don&apos;t use AI</option>
          {data.providers.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
              {p.enabled ? "" : " (off)"}
            </option>
          ))}
        </Select>
        {t.route && (
          <Select
            aria-label={`Model for ${t.title.toLowerCase()}`}
            value={t.route.model}
            onChange={(e) => route({ model: e.target.value, price_in: undefined, price_out: undefined })}
            disabled={!data.can_edit}
          >
            {!models.includes(t.route.model) && <option value={t.route.model}>{t.route.model}</option>}
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </Select>
        )}
      </div>
      {t.problem && (
        <p className="flex items-start gap-2 text-xs font-medium text-warning">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden /> {t.problem}
        </p>
      )}
      {t.price && (
        <p className="text-xs text-muted-foreground">
          {t.price.in === 0 && t.price.out === 0
            ? "Free: it runs on your own machine."
            : `$${t.price.in} per million tokens in, $${t.price.out} out${guessing ? " (a guess, on the high side)" : ""}. A pay stub is usually a few thousand tokens.`}
        </p>
      )}
      {data.can_edit && t.route && (guessing || t.route.price_in !== undefined) && (
        <div className="grid grid-cols-[1fr_1fr_auto] items-end gap-2">
          <Field id={`${t.id}-in`} label="$ per M in">
            <Input inputMode="decimal" value={priceIn} onChange={(e) => setPriceIn(e.target.value)} placeholder="1.25" />
          </Field>
          <Field id={`${t.id}-out`} label="$ per M out">
            <Input inputMode="decimal" value={priceOut} onChange={(e) => setPriceOut(e.target.value)} placeholder="10" />
          </Field>
          <Button variant="secondary" onClick={savePrices} className="mb-0.5">
            Save
          </Button>
        </div>
      )}
      {data.can_edit && t.ready && (
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" size="sm" onClick={test} disabled={trying}>
            {trying ? "Asking…" : "Send a test"}
          </Button>
          {tried && (
            <span className={cn("flex items-center gap-1.5 text-xs font-medium", tried.ok ? "text-positive" : "text-warning")}>
              <CheckCircle2 className="size-3.5" aria-hidden />
              {tried.ok ? "It answered" : "It answered, oddly"} · {tokens.format(tried.tokens)} tokens · {usd(tried.cost)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// ---- extra words to take out

function HideWords({ data, change }: { data: AIOverview; change: Change }) {
  const [word, setWord] = useState("");
  const save = (words: string[]) => change("/api/ai/settings", { method: "PUT", body: { hide_words: words } });
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const w = word.trim();
    if (w.length < 2) return;
    if (await save([...data.hide_words, w])) setWord("");
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Also take out</CardTitle>
        <EyeOff className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        <p className="text-sm text-muted-foreground">
          Everyone&apos;s name and email in this household are always taken out. Add anything else that identifies you: a surname,
          your children&apos;s names, an employee number.
        </p>
        {data.hide_words.length > 0 && (
          <ul className="flex flex-wrap gap-2">
            {data.hide_words.map((w) => (
              <li key={w}>
                <span className="inline-flex items-center gap-1 rounded-full bg-muted py-1 pr-1 pl-3 text-sm">
                  {w}
                  {data.can_edit && (
                    <button
                      type="button"
                      aria-label={`Stop taking out ${w}`}
                      onClick={() => save(data.hide_words.filter((x) => x !== w))}
                      className="grid size-6 place-items-center rounded-full text-muted-foreground hover:bg-background hover:text-foreground"
                    >
                      <Trash2 className="size-3.5" aria-hidden />
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
        {data.can_edit && (
          <form onSubmit={add} className="grid grid-cols-[1fr_auto] items-end gap-2">
            <Field id="ai-hide" label="Word or name">
              <Input value={word} onChange={(e) => setWord(e.target.value)} maxLength={60} autoComplete="off" />
            </Field>
            <Button type="submit" variant="secondary" className="mb-0.5" disabled={word.trim().length < 2}>
              Add
            </Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

// ---- spending

function Spending({ data, change }: { data: AIOverview; change: Change }) {
  const [cap, setCap] = useState((data.cap_cents / 100).toString());
  const capDollars = data.cap_cents / 100;
  const saveCap = () => {
    const v = parseFloat(cap);
    if (Number.isFinite(v) && v >= 0) change("/api/ai/settings", { method: "PUT", body: { monthly_cap_cents: Math.round(v * 100) } }, "Cap saved.");
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Spending this month</CardTitle>
        <Wallet className="size-4 text-muted-foreground" aria-hidden />
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-4">
        <div className="grid grid-cols-1 gap-2">
          <p className="flex items-baseline justify-between gap-2">
            <span className="text-2xl font-semibold tracking-tight tabular-nums">{usd(data.spent)}</span>
            <span className="text-sm text-muted-foreground">of ${capDollars.toFixed(2)} cap</span>
          </p>
          <Progress value={data.spent} max={capDollars || 0.0001} label="AI spending against the monthly cap" />
          <p className="text-xs text-muted-foreground">
            A request that could go over the cap isn&apos;t sent.{data.guessed && " Some costs this month are estimates (no price set for the model)."}
          </p>
        </div>
        {data.can_edit && (
          <div className="grid grid-cols-[1fr_auto] items-end gap-2">
            <Field id="ai-cap" label="Monthly cap ($)">
              <Input inputMode="decimal" value={cap} onChange={(e) => setCap(e.target.value)} />
            </Field>
            <Button variant="secondary" onClick={saveCap} className="mb-0.5">
              Save
            </Button>
          </div>
        )}
        <div>
          <p className="mb-2 text-sm font-medium">Recent requests</p>
          {data.usage.length === 0 ? (
            <p className="text-sm text-muted-foreground">None yet. Each request is listed here, with what it cost, but never what was in it.</p>
          ) : (
            <ol className="divide-y rounded-xl border">
              {data.usage.map((u) => (
                <li key={u.id} className="flex items-start gap-3 px-4 py-3 text-sm">
                  <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", u.ok ? "bg-positive" : "bg-negative")} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">
                      {u.task}
                      {u.who && <span className="font-normal text-muted-foreground"> · {u.who}</span>}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {u.model} · {tokens.format(u.input_tokens + u.output_tokens)} tokens
                      {!u.ok && u.error && ` · ${u.error}`}
                    </p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="text-xs font-medium tabular-nums">
                      {usd(u.cost)}
                      {!u.priced && "*"}
                    </p>
                    <time dateTime={u.at} className="text-xs text-muted-foreground">
                      {when.format(new Date(u.at))}
                    </time>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
