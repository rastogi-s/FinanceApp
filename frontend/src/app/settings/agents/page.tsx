"use client";

import { useState, useSyncExternalStore } from "react";
import { Bot, Check, Copy, Eye, KeyRound, Plus, ShieldCheck, Trash2, Wifi } from "lucide-react";
import { SubPageTitle } from "@/components/page";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/household";

type AgentKey = {
  id: number;
  label: string;
  hint: string;
  by: string | null;
  created_at: string;
  last_used_at: string | null;
  uses: number;
};
type Agents = { keys: AgentKey[]; can_edit: boolean; tools: string[] };

const day = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
const when = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/** The address agents connect to: this app's own origin, so it's right on the laptop, the tailnet name, anywhere. */
function useServerUrl() {
  return useSyncExternalStore(
    () => () => {},
    () => `${window.location.origin}/mcp`,
    () => "/mcp",
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      variant="secondary"
      size="sm"
      onClick={() =>
        navigator.clipboard?.writeText(text).then(
          () => {
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
          },
          () => {},
        )
      }
    >
      {copied ? <Check /> : <Copy />} {copied ? "Copied" : label}
    </Button>
  );
}

function Snippet({ title, text }: { title: string; text: string }) {
  return (
    <div className="grid grid-cols-1 gap-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium">{title}</p>
        <CopyButton text={text} label="Copy" />
      </div>
      <pre className="overflow-x-auto rounded-xl bg-muted/60 px-3 py-2 font-mono text-[11px] leading-relaxed">{text}</pre>
    </div>
  );
}

function snippets(url: string, token: string) {
  return {
    claudeCode: `claude mcp add --transport http finance ${url} \\\n  --header "Authorization: Bearer ${token}"`,
    json: JSON.stringify(
      {
        mcpServers: {
          finance: { command: "npx", args: ["-y", "mcp-remote", url, "--header", `Authorization: Bearer ${token}`] },
        },
      },
      null,
      2,
    ),
  };
}

export default function AgentsPage() {
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const { data, error } = useApi<Agents>("/api/agents");
  const [local, setLocal] = useState<Agents | null>(null);
  const [adding, setAdding] = useState(false);
  const [revoking, setRevoking] = useState<AgentKey | null>(null);
  const s = local ?? data;
  const url = useServerUrl();

  const reload = async () => setLocal(await api<Agents>("/api/agents"));

  return (
    <>
      <SubPageTitle
        title="Agents"
        back="/settings"
        subtitle="Let your own AI assistant answer questions about your money, like “how much did we spend on dining this year?”"
      />
      {!s && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-40 rounded-xl" />
        </div>
      )}
      {error && !s && <p className="text-sm text-negative">{error}</p>}
      {s && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Card className="md:col-span-2">
            <CardContent className="grid grid-cols-1 gap-3 pt-5 sm:grid-cols-3">
              <Pledge icon={<Eye />} title="Look, never touch">
                Agents can read balances, spending, the budget and investments. There is nothing they can change.
              </Pledge>
              <Pledge icon={<KeyRound />} title="A key per agent">
                Shown once, stored only as a fingerprint. Revoke one and that agent is locked out at once.
              </Pledge>
              <Pledge icon={<Wifi />} title="Stays on your network">
                Works for agents on a device in your Tailscale network. Nothing on the internet can reach it.
              </Pledge>
            </CardContent>
          </Card>

          <Card className="md:col-span-2">
            <CardHeader>
              <CardTitle>Keys</CardTitle>
              {s.can_edit && !demo && (
                <Button size="sm" onClick={() => setAdding(true)}>
                  <Plus /> New key
                </Button>
              )}
            </CardHeader>
            {s.keys.length === 0 ? (
              <CardContent>
                <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed px-6 py-8 text-center">
                  <span className="grid size-12 place-items-center rounded-full bg-accent text-accent-foreground" aria-hidden>
                    <Bot className="size-6" />
                  </span>
                  <p className="font-medium">No agents yet</p>
                  <p className="max-w-sm text-sm text-muted-foreground">
                    {demo
                      ? "Agents can't connect while the app shows sample data."
                      : s.can_edit
                        ? "Make a key for Claude Code, Claude Desktop, Cursor or a script of your own, then paste it into that app."
                        : "The household's owner can make a key for an agent."}
                  </p>
                </div>
              </CardContent>
            ) : (
              <ul className="divide-y pb-2">
                {s.keys.map((k) => (
                  <li key={k.id} className="flex items-center gap-3 px-5 py-3">
                    <span className="grid size-9 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground [&_svg]:size-4" aria-hidden>
                      <Bot />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {k.label} <span className="font-mono text-xs text-muted-foreground">…{k.hint}</span>
                      </p>
                      <p className="text-xs text-muted-foreground">
                        Made {day.format(new Date(k.created_at))}
                        {k.by && ` by ${k.by}`} ·{" "}
                        {k.last_used_at ? `last used ${when.format(new Date(k.last_used_at))}, ${k.uses} request${k.uses === 1 ? "" : "s"}` : "not used yet"}
                      </p>
                    </div>
                    {s.can_edit && (
                      <Button variant="ghost" size="icon" aria-label={`Revoke ${k.label}`} onClick={() => setRevoking(k)}>
                        <Trash2 />
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Server address</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-3">
              <p className="rounded-xl bg-muted/60 px-3 py-2 font-mono text-sm break-all">{url}</p>
              <div className="flex flex-wrap items-center gap-2">
                <CopyButton text={url} label="Copy address" />
              </div>
              <p className="text-xs text-muted-foreground">
                MCP over HTTP. Send the key as <span className="font-mono">Authorization: Bearer …</span>
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>What an agent can see</CardTitle>
              <ShieldCheck className="size-4 text-muted-foreground" aria-hidden />
            </CardHeader>
            <CardContent>
              <ul className="grid grid-cols-1 gap-1.5 text-sm">
                {s.tools.map((t) => (
                  <li key={t} className="flex items-center gap-2">
                    <Check className="size-3.5 shrink-0 text-positive" aria-hidden /> {t}
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-muted-foreground">
                Accounts show only their last 4 digits. What the agent does with the answers is up to the agent and its AI
                provider.
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      <NewKeySheet open={adding} onOpenChange={setAdding} url={url} onMade={reload} />
      <RevokeSheet k={revoking} onOpenChange={(o) => !o && setRevoking(null)} onDone={reload} />
    </>
  );
}

function Pledge({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3">
      <span className="grid size-9 shrink-0 place-items-center rounded-full bg-accent text-accent-foreground [&_svg]:size-4" aria-hidden>
        {icon}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium">{title}</p>
        <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{children}</p>
      </div>
    </div>
  );
}

function NewKeySheet({
  open,
  onOpenChange,
  url,
  onMade,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  url: string;
  onMade: () => void;
}) {
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const close = (o: boolean) => {
    if (!o) {
      setToken(null);
      setLabel("");
    }
    onOpenChange(o);
  };
  const make = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api<{ token: string }>("/api/agents", { method: "POST", body: { label } });
      setToken(r.token);
      onMade();
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const s = token ? snippets(url, token) : null;
  return (
    <Sheet
      open={open}
      onOpenChange={close}
      title={token ? "Your new key" : "New agent key"}
      description={token ? "Copy it now: it won't be shown again." : "Name it after the app that will use it."}
    >
      {!token ? (
        <form onSubmit={make} className="grid grid-cols-1 gap-4">
          <Field id="agent-label" label="Name">
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Claude Code on the laptop" maxLength={80} required autoFocus />
          </Field>
          <Button type="submit" size="lg" disabled={busy || !label.trim()}>
            <KeyRound /> {busy ? "Making…" : "Make the key"}
          </Button>
        </form>
      ) : (
        <div className="grid grid-cols-1 gap-5 pb-2">
          <div className="grid grid-cols-1 gap-2 rounded-2xl bg-muted/60 p-4">
            <p className="font-mono text-sm break-all" data-testid="agent-token">
              {token}
            </p>
            <div>
              <CopyButton text={token} label="Copy key" />
            </div>
          </div>
          {s && (
            <>
              <Snippet title="Claude Code" text={s.claudeCode} />
              <Snippet title="Claude Desktop, Cursor and others (config file)" text={s.json} />
            </>
          )}
          <p className="text-xs text-muted-foreground">
            Anyone with this key can read your finances, so keep it in that app&apos;s settings and nowhere else. Then ask it
            something like “What did we spend on groceries last month?”
          </p>
          <Button size="lg" onClick={() => close(false)}>
            Done
          </Button>
        </div>
      )}
    </Sheet>
  );
}

function RevokeSheet({ k, onOpenChange, onDone }: { k: AgentKey | null; onOpenChange: (o: boolean) => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const revoke = async () => {
    if (!k) return;
    setBusy(true);
    try {
      await api(`/api/agents/${k.id}`, { method: "DELETE" });
      toast(`${k.label} can't connect any more.`);
      onDone();
      onOpenChange(false);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Sheet open={k !== null} onOpenChange={onOpenChange} title={`Revoke ${k?.label ?? "key"}?`} description="That agent stops working at once. You can always make a new key.">
      <div className="grid grid-cols-2 gap-2">
        <Button variant="outline" size="lg" onClick={() => onOpenChange(false)} disabled={busy}>
          Keep it
        </Button>
        <Button variant="destructive" size="lg" onClick={revoke} disabled={busy}>
          Revoke
        </Button>
      </div>
    </Sheet>
  );
}
