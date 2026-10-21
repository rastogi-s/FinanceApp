"use client";

import { useState } from "react";
import { BellRing, Mail, Moon, Plus, Send, Smartphone } from "lucide-react";
import { KIND_ICON, type Rule, type RuleIn, RuleSheet } from "@/components/alert-sheets";
import { BriefSettingsCard } from "@/components/brief";
import { SubPageTitle } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { type Alert, KIND_DOT, resetHousehold, useApi } from "@/lib/household";
import { cn } from "@/lib/utils";

type Quiet = { enabled: boolean; start: string; end: string; timezone: string };
type AlertsState = {
  rules: Rule[];
  events: Alert[];
  quiet: Quiet;
  channels: { ntfy: boolean; email: boolean };
  symbols: string[];
  categories: { id: number; name: string }[];
};

const when = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const STATUS: Record<string, string> = {
  sent: "Sent",
  held: "Waiting for quiet hours to end",
  pending: "Sending",
  unsent: "In the app only",
  failed: "Couldn't send",
};

function clock(hhmm: string) {
  const [h, m] = hhmm.split(":").map(Number);
  return new Date(2000, 0, 1, h, m).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export default function AlertsPage() {
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const { data, error } = useApi<AlertsState>("/api/alerts");
  const [editing, setEditing] = useState<Rule | null>(null);
  const [adding, setAdding] = useState(false);
  const [quietOpen, setQuietOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const [local, setLocal] = useState<AlertsState | null>(null);
  const s = local ?? data;

  const apply = (next: AlertsState) => {
    setLocal(next);
    resetHousehold();
  };
  const guard = () => {
    if (demo) toast("Alerts can't be changed while the app shows sample data.");
    return demo;
  };

  const save = async (rule: RuleIn, id?: number) => {
    const next = await api<AlertsState>(id ? `/api/alerts/rules/${id}` : "/api/alerts/rules", {
      method: id ? "PUT" : "POST",
      body: { rule, enabled: true },
    });
    apply(next);
    toast(id ? "Alert saved." : "Alert added.");
  };
  const toggle = async (r: Rule, enabled: boolean) => {
    if (guard()) return;
    try {
      apply(await api<AlertsState>(`/api/alerts/rules/${r.id}`, { method: "PATCH", body: { enabled } }));
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const remove = async (r: Rule) => {
    if (guard()) return;
    try {
      apply(await api<AlertsState>(`/api/alerts/rules/${r.id}`, { method: "DELETE" }));
      setEditing(null);
      toast("Alert removed.");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const test = async () => {
    if (guard()) return;
    setTesting(true);
    try {
      const r = await api<{ channel: string }>("/api/alerts/test", { method: "POST" });
      toast(r.channel === "ntfy" ? "Sent. Check your phone." : "Sent by email.");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <SubPageTitle title="Alerts" back="/settings" subtitle="Prices, dips, budgets and how concentrated you are, sent to your phone." />
      {!s && !error && (
        <div className="grid grid-cols-1 gap-4" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-28 rounded-xl" />
          <Skeleton className="h-64 rounded-xl" />
        </div>
      )}
      {error && !s && <p className="text-sm text-negative">{error}</p>}
      {s && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Where they go</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-3">
              <Channel icon={<Smartphone />} title="Phone (ntfy)" on={s.channels.ntfy} hint="NTFY_URL in .env, then subscribe to the topic in the ntfy app." />
              <Channel icon={<Mail />} title="Email" on={s.channels.email} hint={s.channels.ntfy ? "Used if the phone can't be reached." : "SMTP settings and ALERT_EMAIL in .env."} />
              <Button variant="secondary" size="sm" className="mt-1 justify-self-start" onClick={test} disabled={testing || (!s.channels.ntfy && !s.channels.email)}>
                <Send /> Send a test
              </Button>
              <p className="text-xs text-muted-foreground">Messages never include balances or dollar amounts of your own money, so a lock screen gives nothing away.</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Quiet hours</CardTitle>
              <Moon className="size-4 text-muted-foreground" aria-hidden />
            </CardHeader>
            <CardContent>
              <p className="text-sm">
                {s.quiet.enabled ? (
                  <>
                    {clock(s.quiet.start)} to {clock(s.quiet.end)}
                  </>
                ) : (
                  "Off: alerts go out any time"
                )}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {s.quiet.enabled ? `Anything that happens overnight arrives as one message at ${clock(s.quiet.end)}. ` : ""}
                Security alerts never wait. Times in {s.quiet.timezone.replace(/_/g, " ")}.
              </p>
              <Button variant="secondary" size="sm" className="mt-3" onClick={() => !guard() && setQuietOpen(true)}>
                Change
              </Button>
            </CardContent>
          </Card>

          <BriefSettingsCard demo={demo} />

          <Card className="md:col-span-2">
            <CardHeader>
              <CardTitle>Rules</CardTitle>
              <Button size="sm" onClick={() => !guard() && setAdding(true)}>
                <Plus /> New
              </Button>
            </CardHeader>
            {s.rules.length === 0 && (
              <CardContent>
                <p className="text-sm text-muted-foreground">No alerts yet. Add one for a price, a dip, a budget, or any one company getting too big.</p>
              </CardContent>
            )}
            <ul className="divide-y pb-2">
              {s.rules.map((r) => {
                const Icon = KIND_ICON[r.kind];
                return (
                  <li key={r.id} className="flex items-center gap-3 px-5 py-3">
                    <span className={cn("grid size-9 shrink-0 place-items-center rounded-full [&_svg]:size-4", r.enabled ? "bg-accent text-accent-foreground" : "bg-muted text-muted-foreground")} aria-hidden>
                      <Icon />
                    </span>
                    <button className="min-w-0 flex-1 text-left" onClick={() => !guard() && setEditing(r)}>
                      <span className={cn("block text-sm font-medium", !r.enabled && "text-muted-foreground")}>{r.summary}</span>
                      <span className="block text-xs text-muted-foreground">
                        {r.kind === "price"
                          ? "Checked every 15 minutes while markets are open"
                          : r.kind === "dip"
                            ? "Checked with every price update, against yesterday's trends"
                            : r.kind === "budget"
                              ? "Checked after every sync"
                              : "Checked with every price update"}
                      </span>
                    </button>
                    {r.triggered && <Badge variant="warning">Triggered</Badge>}
                    <Switch checked={r.enabled} onCheckedChange={(v) => toggle(r, v)} label={r.summary} hideLabel />
                  </li>
                );
              })}
            </ul>
          </Card>

          <Card className="md:col-span-2">
            <CardHeader>
              <CardTitle>Recent</CardTitle>
              <BellRing className="size-4 text-muted-foreground" aria-hidden />
            </CardHeader>
            <CardContent className="pt-1">
              {s.events.length === 0 ? (
                <p className="text-sm text-muted-foreground">Nothing yet. When a rule fires it shows up here and on Home.</p>
              ) : (
                <ul className="divide-y">
                  {s.events.map((e) => (
                    <li key={e.id} className="flex items-start gap-3 py-3">
                      <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", KIND_DOT[e.kind])} aria-hidden />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium">{e.title}</p>
                        <p className="text-xs text-muted-foreground">
                          {when.format(new Date(e.at))} · {STATUS[e.status ?? "sent"] ?? e.status}
                          {e.channel === "email" && " by email"}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {s && (
        <>
          <RuleSheet
            open={adding || editing !== null}
            onOpenChange={(o) => {
              if (!o) {
                setAdding(false);
                setEditing(null);
              }
            }}
            rule={editing}
            symbols={s.symbols}
            categories={s.categories}
            save={save}
            onRemove={remove}
          />
          <QuietSheet open={quietOpen} onOpenChange={setQuietOpen} quiet={s.quiet} onSaved={apply} />
        </>
      )}
    </>
  );
}

function Channel({ icon, title, on, hint }: { icon: React.ReactNode; title: string; on: boolean; hint: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className={cn("grid size-9 shrink-0 place-items-center rounded-full [&_svg]:size-4", on ? "bg-accent text-accent-foreground" : "bg-muted text-muted-foreground")} aria-hidden>
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{on ? "Set up" : `Not set up: ${hint}`}</p>
      </div>
      <Badge variant={on ? "positive" : "muted"}>{on ? "On" : "Off"}</Badge>
    </div>
  );
}

function QuietSheet({
  open,
  onOpenChange,
  quiet,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  quiet: Quiet;
  onSaved: (s: AlertsState) => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Quiet hours" description="Alerts wait until the end, then arrive together.">
      {open && <QuietForm quiet={quiet} onSaved={onSaved} done={() => onOpenChange(false)} />}
    </Sheet>
  );
}

function QuietForm({ quiet, onSaved, done }: { quiet: Quiet; onSaved: (s: AlertsState) => void; done: () => void }) {
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const [enabled, setEnabled] = useState(quiet.enabled);
  const [start, setStart] = useState(quiet.start);
  const [end, setEnd] = useState(quiet.end);
  const [zone, setZone] = useState(quiet.timezone);
  const [busy, setBusy] = useState(false);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      onSaved(await api<AlertsState>("/api/alerts/quiet", { method: "PUT", body: { enabled, start, end, timezone: zone } }));
      toast("Quiet hours saved.");
      done();
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={save} className="grid grid-cols-1 gap-4">
      <Switch checked={enabled} onCheckedChange={setEnabled} label="Quiet hours" description="Hold alerts overnight." />
      <div className="grid grid-cols-2 gap-3">
        <Field id="quiet-start" label="From">
          <Input type="time" value={start} onChange={(e) => setStart(e.target.value)} disabled={!enabled} required />
        </Field>
        <Field id="quiet-end" label="Until">
          <Input type="time" value={end} onChange={(e) => setEnd(e.target.value)} disabled={!enabled} required />
        </Field>
      </div>
      <p className="text-sm text-muted-foreground">
        Time zone: {zone.replace(/_/g, " ")}
        {browserZone && browserZone !== zone && (
          <>
            {" "}
            <button type="button" className="font-medium text-primary" onClick={() => setZone(browserZone)}>
              Use {browserZone.replace(/_/g, " ")}
            </button>
          </>
        )}
      </p>
      <Button type="submit" size="lg" disabled={busy}>
        Save
      </Button>
    </form>
  );
}
