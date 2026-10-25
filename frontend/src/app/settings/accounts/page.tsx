"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  BellRing,
  Building2,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  FileUp,
  Landmark,
  LineChart,
  PencilLine,
  Plus,
  RefreshCw,
} from "lucide-react";
import { Money, formatMoney } from "@/components/money";
import { SubPageTitle } from "@/components/page";
import { UploadSheet } from "@/components/upload-sheet";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import {
  type Account,
  type Connection,
  type ConnectionsData as Data,
  type Every,
  type Reminder,
  dueLabel,
  parseDollars,
  shortDate,
} from "@/lib/accounts";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { resetHousehold, useApi } from "@/lib/household";
import { cn } from "@/lib/utils";

const PROVIDER = { plaid: "Plaid", snaptrade: "SnapTrade", file: "Uploaded", manual: "Typed in" } as const;
const TYPE_LABEL: Record<string, string> = {
  depository: "Cash",
  credit: "Card",
  loan: "Loan",
  investment: "Investments",
  other: "Other",
};
const when = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const LINK_KEY = "finance-link-token";

export default function AccountsPage() {
  return (
    <Suspense>
      <Accounts />
    </Suspense>
  );
}

function Accounts() {
  const { data, error, reload } = useApi<Data>("/api/connections");
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const params = useSearchParams();
  const router = useRouter();
  const [adding, setAdding] = useState(false);
  // false, or open: into one account (accountId) or wherever the file says.
  const [uploading, setUploading] = useState<false | { accountId?: number }>(false);
  const [manual, setManual] = useState(false);
  const [editing, setEditing] = useState<{ account: Account; connection: Connection } | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const finished = useRef(false);

  const changed = () => {
    resetHousehold();
    reload();
  };

  // Back from Plaid's or SnapTrade's own page: finish linking and run the first sync.
  useEffect(() => {
    const provider = params.get("linked");
    if (!provider || finished.current) return;
    finished.current = true;
    let token: string | null = null;
    try {
      token = sessionStorage.getItem(LINK_KEY);
      sessionStorage.removeItem(LINK_KEY);
    } catch {}
    router.replace("/settings/accounts");
    if (!token) return;
    setFinishing(true);
    api<{ connection: Connection; synced: boolean; already_linked?: boolean }>("/api/connections/finish", {
      body: { token },
    })
      .then((r) => {
        toast(
          r.already_linked
            ? `${r.connection.institution} was already linked, so nothing was added twice.`
            : `${r.connection.institution} is linked${r.synced ? " and synced" : ". Its first sync is on the way"}.`,
        );
        changed();
      })
      .catch((e: Error) => toast(e.message, "error"))
      .finally(() => setFinishing(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on the way back
  }, [params]);

  const syncNow = async () => {
    setSyncing(true);
    try {
      const r = await api<{ ok: boolean }>("/api/sync", { method: "POST" });
      toast(r.ok ? "Everything's up to date." : "Synced, but something needs a look.", r.ok ? "ok" : "error");
      changed();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSyncing(false);
    }
  };

  // With a connection: reconnect it (sign in again on the same link), so nothing new is added and nothing counts twice.
  const link = async (
    provider: "plaid" | "snaptrade",
    kind: "banking" | "investments" = "banking",
    connectionId?: number,
  ) => {
    try {
      const r = await api<{ url: string; token: string }>(`/api/connections/${provider}/start`, {
        body: { kind, connection_id: connectionId ?? null },
      });
      try {
        sessionStorage.setItem(LINK_KEY, r.token);
      } catch {}
      window.location.assign(r.url);
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  // From an upload reminder on Home (?account=8): that account opens, once.
  const [fromLink, setFromLink] = useState(() => Number(params.get("account")) || null);
  const linkedTo = data?.connections.find((x) => x.accounts.some((a) => a.id === fromLink));
  const linkedAccount = linkedTo?.accounts.find((a) => a.id === fromLink);
  const sheet = editing ?? (linkedTo && linkedAccount ? { account: linkedAccount, connection: linkedTo } : null);

  const linkedCount = data?.connections.reduce((n, c) => n + c.accounts.length, 0) ?? 0;

  return (
    <>
      <SubPageTitle title="Accounts" back="/settings" subtitle="Every account, how it connects, and its backup." />
      {params.get("welcome") && linkedCount === 0 && (
        <Card className="mb-4 border-primary/30 bg-accent/40">
          <CardContent className="pt-5">
            <p className="font-medium">You&apos;re in. Now add your first account.</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Link a bank or card, connect a brokerage, or upload a file you downloaded. One at a time is fine.
            </p>
          </CardContent>
        </Card>
      )}
      {error && <p className="text-sm text-negative">{error}</p>}
      {!data && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-24 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
        </div>
      )}
      {data && (
        <div className="grid grid-cols-1 gap-4">
          <SyncCard
            sync={data.sync}
            onSync={syncNow}
            syncing={syncing || finishing}
            disabled={demo}
            hasLinks={data.connections.some((c) => c.provider === "plaid" || c.provider === "snaptrade")}
          />

          <Button size="lg" onClick={() => setAdding(true)} className="w-full md:w-auto md:justify-self-start">
            <Plus /> Add account
          </Button>

          {data.connections.length === 0 && (
            <Card>
              <CardContent className="flex flex-col items-center gap-3 py-10 text-center">
                <span className="grid size-12 place-items-center rounded-full bg-accent text-accent-foreground">
                  <Landmark className="size-6" aria-hidden />
                </span>
                <p className="font-medium">No accounts yet</p>
                <p className="max-w-xs text-sm text-muted-foreground">
                  Nothing here can move money: every link is read-only, and tokens are encrypted on your server.
                </p>
              </CardContent>
            </Card>
          )}

          {data.connections.map((c) => (
            <ConnectionCard
              key={c.id}
              connection={c}
              onEdit={(account) => setEditing({ account, connection: c })}
              onRelink={() => (c.provider === "plaid" || c.provider === "snaptrade") && link(c.provider, "banking", c.id)}
              onUpload={(accountId) => (demo ? toast("Uploads are off while the app shows sample data.") : setUploading({ accountId }))}
            />
          ))}

          {data.revoked.length > 0 && <DisconnectedCard revoked={data.revoked} disabled={demo} onRemoved={changed} />}
        </div>
      )}

      <AddSheet
        open={adding}
        onOpenChange={setAdding}
        providers={data?.providers ?? { plaid: false, snaptrade: false }}
        disabled={demo}
        onPick={(choice) => {
          setAdding(false);
          if (choice === "upload") setUploading({});
          else if (choice === "manual") setManual(true);
          else if (choice === "investments") link("plaid", "investments");
          else link(choice);
        }}
      />
      <UploadSheet
        open={uploading !== false}
        onOpenChange={(o) => !o && setUploading(false)}
        onSaved={changed}
        accountId={uploading ? uploading.accountId : undefined}
      />
      <ManualSheet open={manual} onOpenChange={setManual} onSaved={changed} />
      {sheet && data && (
        <AccountSheet
          key={sheet.account.id}
          account={sheet.account}
          connection={sheet.connection}
          people={data.people}
          disabled={demo}
          onClose={() => {
            setEditing(null);
            setFromLink(null);
          }}
          onUpload={() => {
            setEditing(null);
            setFromLink(null);
            setUploading({ accountId: sheet.account.id });
          }}
          onSaved={changed}
        />
      )}
    </>
  );
}

// Links that were revoked keep their history; this is where that history can be deleted for good.
function DisconnectedCard({
  revoked,
  disabled,
  onRemoved,
}: {
  revoked: Connection[];
  disabled: boolean;
  onRemoved: () => void;
}) {
  const [confirming, setConfirming] = useState<number | null>(null);
  const remove = async (c: Connection) => {
    try {
      await api(`/api/connections/${c.id}?delete_data=true`, { method: "DELETE" });
      toast(`${c.institution}'s history deleted.`);
      setConfirming(null);
      onRemoved();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Disconnected</CardTitle>
        <p className="text-sm text-muted-foreground">No longer syncing. Their history still counts until you delete it.</p>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3">
        {revoked.map((c) => (
          <div key={c.id} className="grid grid-cols-1 gap-2 rounded-2xl border p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{c.institution}</p>
                <p className="text-xs text-muted-foreground">
                  {PROVIDER[c.provider]} · {c.accounts.length} account{c.accounts.length === 1 ? "" : "s"}
                </p>
              </div>
              {confirming !== c.id && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-negative hover:text-negative"
                  onClick={() => setConfirming(c.id)}
                  disabled={disabled}
                >
                  Delete history
                </Button>
              )}
            </div>
            {confirming === c.id && (
              <div className="grid grid-cols-1 gap-2">
                <p className="text-xs text-muted-foreground">
                  Deletes its accounts, balances and transactions from this app. This can&apos;t be undone.
                </p>
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="destructive" onClick={() => remove(c)}>
                    Delete everything
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function SyncCard({
  sync,
  onSync,
  syncing,
  disabled,
  hasLinks,
}: {
  sync: Data["sync"];
  onSync: () => void;
  syncing: boolean;
  disabled: boolean;
  hasLinks: boolean;
}) {
  const nights = Math.min(sync.clean_days, 7);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sync</CardTitle>
        {sync.last_ok === false ? (
          <Badge variant="warning">Needs a look</Badge>
        ) : sync.last_at ? (
          <Badge variant="positive">Working</Badge>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm">
            {sync.last_at ? (
              <>Last synced {when.format(new Date(sync.last_at))}</>
            ) : hasLinks ? (
              "Not synced yet"
            ) : (
              "Runs every few hours once an account is linked"
            )}
          </p>
          {hasLinks && (
            <div className="mt-2 flex items-center gap-2" aria-label={`${nights} of 7 nights in a row without problems`}>
              <div className="flex gap-1" aria-hidden>
                {Array.from({ length: 7 }, (_, i) => (
                  <span key={i} className={cn("h-1.5 w-4 rounded-full", i < nights ? "bg-positive" : "bg-muted")} />
                ))}
              </div>
              <span className="text-xs text-muted-foreground">{nights} of 7 clean nights</span>
            </div>
          )}
        </div>
        {hasLinks && (
          <Button variant="outline" onClick={onSync} disabled={syncing || disabled || sync.running}>
            <RefreshCw className={cn(syncing && "animate-spin")} /> {syncing ? "Syncing…" : "Sync now"}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function ConnectionCard({
  connection: c,
  onEdit,
  onRelink,
  onUpload,
}: {
  connection: Connection;
  onEdit: (a: Account) => void;
  onRelink: () => void;
  /** With an account id, the file goes into that account unless changed. */
  onUpload: (accountId?: number) => void;
}) {
  // Uploaded and typed-in accounts only get new data by hand, so the upload sits right on the card.
  const byHand = c.provider === "file" || c.provider === "manual";
  return (
    <Card>
      <CardHeader className="items-center">
        <div className="flex min-w-0 items-center gap-3">
          <span
            className="grid size-9 shrink-0 place-items-center rounded-full bg-muted text-sm font-semibold text-muted-foreground"
            aria-hidden
          >
            {c.institution[0]}
          </span>
          <div className="min-w-0">
            <h2 className="truncate font-medium">{c.institution}</h2>
            <p className="text-xs text-muted-foreground">
              {PROVIDER[c.provider]}
              {c.last_synced_at && ` · ${when.format(new Date(c.last_synced_at))}`}
            </p>
          </div>
        </div>
        {c.status === "stale" ? (
          <Badge variant="warning">Stopped syncing</Badge>
        ) : byHand ? (
          <Button
            size="sm"
            variant="outline"
            className="shrink-0"
            onClick={() => onUpload(c.accounts.length === 1 ? c.accounts[0]?.id : undefined)}
            aria-label={`Upload transactions or a statement for ${c.institution}`}
          >
            <FileUp /> Upload
          </Button>
        ) : null}
      </CardHeader>
      {c.status === "stale" && (
        <div className="mx-5 mt-3 rounded-xl bg-warning/10 p-3.5 text-sm">
          <p className="flex items-start gap-2 font-medium text-warning">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />{" "}
            {c.last_error || "This connection needs you to sign in at the bank again."}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" onClick={onRelink}>
              Reconnect
            </Button>
            <Button size="sm" variant="outline" onClick={() => onUpload()}>
              <FileUp /> Upload a file instead
            </Button>
          </div>
        </div>
      )}
      <ul className="divide-y pt-2 pb-1">
        {c.accounts.map((a) => (
          <li key={a.id}>
            <button
              type="button"
              onClick={() => onEdit(a)}
              className="flex w-full items-center gap-3 px-5 py-3.5 text-left transition-colors hover:bg-muted/50"
            >
              <div className="min-w-0 flex-1">
                <p className={cn("truncate text-sm font-medium", a.hidden && "text-muted-foreground line-through")}>
                  {a.name}
                  {a.mask && <span className="ml-1.5 text-muted-foreground">••{a.mask}</span>}
                </p>
                <p className="text-xs text-muted-foreground">
                  {TYPE_LABEL[a.type] ?? a.type}
                  {!a.is_authoritative && " · not counted"}
                  <ReminderNote account={a} manual={c.provider === "manual"} />
                  {a.statement_diff_percent !== null && (
                    <span className={a.statement_diff_percent <= 1 ? "text-positive" : "text-warning"}>
                      {" "}
                      · {a.statement_diff_percent <= 1 ? "matches statement" : `${a.statement_diff_percent}% off statement`}
                    </span>
                  )}
                </p>
              </div>
              {a.balance_cents !== null ? (
                <Money
                  cents={a.type === "credit" || a.type === "loan" ? -a.balance_cents : a.balance_cents}
                  className="text-sm font-medium"
                />
              ) : (
                <span className="text-xs text-muted-foreground">Add a balance</span>
              )}
              <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

type Choice = "plaid" | "investments" | "snaptrade" | "upload" | "manual";

function AddSheet({
  open,
  onOpenChange,
  providers,
  disabled,
  onPick,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  providers: Data["providers"];
  disabled: boolean;
  onPick: (c: Choice) => void;
}) {
  const options: { key: Choice; icon: React.ReactNode; title: string; body: string; ready: boolean; off?: string }[] = [
    {
      key: "plaid",
      icon: <Building2 />,
      title: "Bank or credit card",
      body: "Chase, Bank of America, Amex and almost every US bank, through Plaid.",
      ready: providers.plaid,
      off: "Add PLAID_CLIENT_ID and PLAID_SECRET to .env to turn this on.",
    },
    {
      key: "snaptrade",
      icon: <LineChart />,
      title: "Brokerage",
      body: "E*TRADE, Fidelity, Schwab and more, read-only, through SnapTrade.",
      ready: providers.snaptrade,
      off: "Add SNAPTRADE_CLIENT_ID and SNAPTRADE_CONSUMER_KEY to .env to turn this on.",
    },
    {
      key: "investments",
      icon: <LineChart />,
      title: "401(k), HSA or IRA",
      body: "Retirement and health accounts through Plaid Investments.",
      ready: providers.plaid,
      off: "Needs Plaid keys in .env.",
    },
    {
      key: "upload",
      icon: <FileUp />,
      title: "Upload a file",
      body: "A positions or transactions CSV, or an OFX/QFX download. Works for any account.",
      ready: true,
    },
    {
      key: "manual",
      icon: <PencilLine />,
      title: "Type in a balance",
      body: "For anything without a download: a car loan, a house, an old pension.",
      ready: true,
    },
  ];
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Add an account" description="Read-only. Nothing here can move money.">
      <ul className="grid grid-cols-1 gap-2 pb-2">
        {options.map((o) => {
          const off = disabled || !o.ready;
          return (
            <li key={o.key}>
              <button
                type="button"
                disabled={off}
                onClick={() => onPick(o.key)}
                className="flex w-full items-start gap-3.5 rounded-2xl border p-4 text-left transition-colors hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span
                  className="grid size-10 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground [&_svg]:size-5"
                  aria-hidden
                >
                  {o.icon}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{o.title}</span>
                  <span className="mt-0.5 block text-sm text-muted-foreground">{!o.ready && o.off ? o.off : o.body}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {disabled && <p className="pb-2 text-xs text-muted-foreground">Adding accounts is off while the app shows sample data.</p>}
    </Sheet>
  );
}

function ManualSheet({ open, onOpenChange, onSaved }: { open: boolean; onOpenChange: (o: boolean) => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [institution, setInstitution] = useState("");
  const [type, setType] = useState("depository");
  const [balance, setBalance] = useState("");
  const [error, setError] = useState<string | null>(null);
  const cents = parseDollars(balance);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (cents === null) return setError("Type an amount like 1250.00");
    try {
      await api("/api/accounts/manual", { body: { name, institution, type, balance_cents: cents } });
      toast(`${name} added.`);
      onOpenChange(false);
      setName("");
      setInstitution("");
      setBalance("");
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Type in a balance"
      description="Update it whenever you like; the app keeps the history."
    >
      <form onSubmit={save} className="grid grid-cols-1 gap-4 pb-2">
        <Field id="m-name" label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="HSA" required />
        </Field>
        <Field id="m-inst" label="Institution (optional)">
          <Input value={institution} onChange={(e) => setInstitution(e.target.value)} placeholder="HealthEquity" />
        </Field>
        <Field id="m-type" label="Kind">
          <Select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="depository">Cash (checking, savings)</option>
            <option value="investment">Investments</option>
            <option value="credit">Credit card (what you owe)</option>
            <option value="loan">Loan (what you owe)</option>
            <option value="other">Something you own (house, car)</option>
          </Select>
        </Field>
        <Field id="m-balance" label={type === "credit" || type === "loan" ? "Amount owed" : "Balance"} error={error}>
          <Input value={balance} onChange={(e) => setBalance(e.target.value)} inputMode="decimal" placeholder="0.00" required />
        </Field>
        <Button size="lg" type="submit" disabled={!name || cents === null}>
          Add account
        </Button>
      </form>
    </Sheet>
  );
}

function AccountSheet({
  account: a,
  connection: c,
  people,
  disabled,
  onClose,
  onUpload,
  onSaved,
}: {
  account: Account;
  connection: Connection;
  people: Data["people"];
  disabled: boolean;
  onClose: () => void;
  /** Close this sheet and open the upload sheet with this account picked. */
  onUpload: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(a.name);
  const [statement, setStatement] = useState(a.statement_cents !== null ? (a.statement_cents / 100).toFixed(2) : "");
  const [balance, setBalance] = useState("");
  const [current, setCurrent] = useState(a);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const typed = c.provider === "manual" || c.provider === "file";

  const patch = async (body: Record<string, unknown>, message?: string) => {
    try {
      const r = await api<Connection>(`/api/accounts/${a.id}`, { method: "PATCH", body });
      const updated = r.accounts.find((x) => x.id === a.id);
      if (updated) setCurrent(updated);
      if (message) toast(message);
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const saveBalance = async () => {
    const cents = parseDollars(balance);
    if (cents === null) return;
    try {
      const r = await api<Connection>(`/api/accounts/${a.id}/balance`, { method: "PUT", body: { balance_cents: cents } });
      const updated = r.accounts.find((x) => x.id === a.id);
      if (updated) setCurrent(updated);
      setBalance("");
      toast("Balance updated.");
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const saveReminder = async (body: { every: Every; day: number | null }) => {
    try {
      const r = await api<Connection>(`/api/accounts/${a.id}/reminder`, { method: "PUT", body });
      const updated = r.accounts.find((x) => x.id === a.id);
      if (updated) setCurrent(updated);
      toast(body.every === "off" ? "No more upload reminders for this account." : "Reminder saved.");
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const remove = async (deleteData: boolean) => {
    try {
      const r = await api<{ problem: string | null }>(`/api/connections/${c.id}?delete_data=${deleteData}`, { method: "DELETE" });
      toast(r.problem ?? `${c.institution} removed.`, r.problem ? "error" : "ok");
      onClose();
      onSaved();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const diff = current.statement_diff_percent;
  return (
    <Sheet
      open
      onOpenChange={(o) => !o && onClose()}
      title={current.display_name}
      description={`${c.institution} · ${PROVIDER[c.provider]}`}
    >
      <div className="grid grid-cols-1 gap-6 pb-2">
        <div className="rounded-2xl bg-muted/60 p-4">
          <p className="text-xs text-muted-foreground">Balance{current.balance_date && ` on ${current.balance_date}`}</p>
          <p className="mt-1 text-3xl font-semibold tracking-tight">
            {current.balance_cents === null ? "—" : <Money cents={current.balance_cents} style="exact" />}
          </p>
        </div>

        {typed && (
          <div className="grid grid-cols-1 gap-2">
            <Button size="lg" onClick={onUpload} disabled={disabled}>
              <FileUp /> Upload transactions or statement
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              A CSV or OFX download works best; a PDF statement or screenshot can be read with AI. {current.display_name} is picked for you, and you check everything before it&apos;s saved.
            </p>
          </div>
        )}

        <fieldset disabled={disabled} className="grid grid-cols-1 gap-4">
          <Field id="a-name" label="Name">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => name !== current.name && name && patch({ name }, "Renamed.")}
            />
          </Field>
          {people.length > 1 && (
            <Field id="a-owner" label="Whose account">
              <Select
                value={current.owner_user_id ?? ""}
                onChange={(e) => patch({ owner_user_id: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">Joint</option>
                {people.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <Switch
            checked={current.is_authoritative}
            onCheckedChange={(v) => patch({ is_authoritative: v })}
            label="Count in net worth"
            description="Turn off when the same account also comes in another way, so it isn't counted twice."
          />
          <Switch checked={current.hidden} onCheckedChange={(v) => patch({ hidden: v })} label="Hide everywhere" />
        </fieldset>

        {typed && (
          <div className="grid grid-cols-1 gap-2">
            <Field id="a-balance" label="Update the balance">
              <Input
                value={balance}
                onChange={(e) => setBalance(e.target.value)}
                inputMode="decimal"
                placeholder="0.00"
                disabled={disabled}
              />
            </Field>
            <Button variant="outline" onClick={saveBalance} disabled={disabled || parseDollars(balance) === null}>
              Save balance
            </Button>
          </div>
        )}

        {current.reminder && (
          <ReminderCard reminder={current.reminder} manual={c.provider === "manual"} disabled={disabled} onChange={saveReminder} />
        )}

        <div className="grid grid-cols-1 gap-2 rounded-2xl border p-4">
          <p className="text-sm font-medium">Check against your statement</p>
          <p className="text-xs text-muted-foreground">Type the latest statement balance; the app should be within 1% of it.</p>
          <div className="flex gap-2">
            <Input
              aria-label="Statement balance"
              value={statement}
              onChange={(e) => setStatement(e.target.value)}
              inputMode="decimal"
              placeholder="0.00"
              disabled={disabled}
            />
            <Button
              variant="outline"
              className="h-12 md:h-11"
              disabled={disabled || parseDollars(statement) === null}
              onClick={() => patch({ statement_cents: parseDollars(statement) })}
            >
              Check
            </Button>
          </div>
          {diff !== null && (
            <p className={cn("flex items-center gap-1.5 text-sm font-medium", diff <= 1 ? "text-positive" : "text-warning")}>
              {diff <= 1 ? <CheckCircle2 className="size-4" aria-hidden /> : <CircleDashed className="size-4" aria-hidden />}
              {diff <= 1
                ? `Within ${diff}% of ${formatMoney(current.statement_cents ?? 0, "exact")}`
                : `${diff}% away from ${formatMoney(current.statement_cents ?? 0, "exact")}: worth a look`}
            </p>
          )}
        </div>

        {!confirmRemove ? (
          <Button variant="ghost" className="text-negative hover:text-negative" onClick={() => setConfirmRemove(true)} disabled={disabled}>
            Remove {c.institution}
          </Button>
        ) : (
          <div className="grid grid-cols-1 gap-2 rounded-2xl border border-negative/30 p-4">
            <p className="text-sm font-medium">Remove {c.institution}?</p>
            <p className="text-xs text-muted-foreground">
              {c.provider === "plaid" || c.provider === "snaptrade"
                ? "The link is revoked at the provider and its token is deleted. Keep the history to still see past numbers."
                : "This removes the account and everything uploaded into it."}
            </p>
            <div className="mt-1 flex flex-wrap gap-2">
              {(c.provider === "plaid" || c.provider === "snaptrade") && (
                <Button size="sm" variant="outline" onClick={() => remove(false)}>
                  Remove, keep history
                </Button>
              )}
              <Button size="sm" variant="destructive" onClick={() => remove(true)}>
                Remove everything
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmRemove(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>
    </Sheet>
  );
}

/** "· upload due" or "· next upload Nov 3" under an uploaded or typed-in account. */
function ReminderNote({ account: a, manual }: { account: Account; manual: boolean }) {
  const r = a.reminder;
  if (!r || r.every === "off") return null;
  const what = manual ? "update" : "upload";
  if (r.is_due && !r.snoozed_until) return <span className="text-warning"> · {what} due</span>;
  return r.due ? <> · next {what} {shortDate(r.due)}</> : null;
}

const EVERY: { value: Every; label: string }[] = [
  { value: "monthly", label: "Every month, after the statement" },
  { value: "quarterly", label: "Every 3 months, after the statement" },
  { value: "weekly", label: "Every week" },
  { value: "off", label: "Never" },
];
const DAYS = Array.from({ length: 28 }, (_, i) => i + 1);
const longDate = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });

function ordinal(n: number): string {
  if (n % 100 >= 11 && n % 100 <= 13) return `${n}th`;
  return `${n}${["th", "st", "nd", "rd"][n % 10] ?? "th"}`;
}

/** How often to be reminded to upload (or type in) the next statement, and when the next reminder comes. */
function ReminderCard({
  reminder: r,
  manual,
  disabled,
  onChange,
}: {
  reminder: Reminder;
  manual: boolean;
  disabled: boolean;
  onChange: (body: { every: Every; day: number | null }) => void;
}) {
  const statementBased = r.every === "monthly" || r.every === "quarterly";
  const due = r.is_due && !r.snoozed_until;
  let note = `Has data to ${shortDate(r.newest)}.`;
  if (r.every === "off") note += " No reminders.";
  else if (due) note = `${dueLabel({ provider: manual ? "manual" : "file", every: r.every, statement: r.statement })} is due. ${note}`;
  else if (r.snoozed_until) note += ` Snoozed until ${longDate.format(new Date(r.snoozed_until))}.`;
  else if (r.due) note += ` Next reminder ${longDate.format(new Date(r.due))}.`;
  return (
    <div className="grid grid-cols-1 gap-3 rounded-2xl border p-4">
      <div className="flex items-start gap-2.5">
        <BellRing className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />
        <div className="min-w-0">
          <p className="text-sm font-medium">Upload reminders</p>
          <p className="text-xs text-muted-foreground">
            Nothing comes in for this account by itself. Get a push when it&apos;s time to {manual ? "update the balance" : "upload"}.
          </p>
        </div>
      </div>
      <fieldset disabled={disabled} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field id="r-every" label="Remind me">
          <Select value={r.every} onChange={(e) => onChange({ every: e.target.value as Every, day: r.day })}>
            {EVERY.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        {statementBased && (
          <Field id="r-day" label="Statement closes on">
            <Select value={r.day ?? ""} onChange={(e) => onChange({ every: r.every, day: e.target.value ? Number(e.target.value) : null })}>
              <option value="">The last day of the month</option>
              {DAYS.map((d) => (
                <option key={d} value={d}>
                  The {ordinal(d)}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </fieldset>
      <p className={cn("text-sm", due ? "font-medium text-warning" : "text-muted-foreground")}>{note}</p>
    </div>
  );
}
