"use client";

import { useState } from "react";
import { KeyRound, Laptop, LockKeyhole, ShieldAlert, Smartphone, Trash2 } from "lucide-react";
import { SubPageTitle } from "@/components/page";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { useAuth, useUser } from "@/lib/auth";
import { resetHousehold, useApi } from "@/lib/household";
import { createPasskey, passkeyMessage, passkeysSupported } from "@/lib/webauthn";

type Passkey = { id: number; name: string; created_at: string; last_used_at: string | null };
type SessionRow = { id: number; device: string; method: string; created_at: string; last_seen_at: string; current: boolean };
type Event = { id: number; at: string; who: string | null; action: string; text: string; detail: Record<string, unknown> };

const when = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const WARN = new Set([
  "signin_failed",
  "signin_locked",
  "signin_new_device",
  "reauth_failed",
  "setup_code_rejected",
  "connection_stale",
  "revoke_all",
]);

export default function SecurityPage() {
  const user = useUser();
  const { state } = useAuth();
  const demo = state.status === "signed-in" && state.demo;
  const passkeys = useApi<Passkey[]>("/api/auth/passkeys");
  const sessions = useApi<SessionRow[]>("/api/auth/sessions");
  const audit = useApi<Event[]>("/api/auth/audit?limit=40");
  const [password, setPassword] = useState(false);
  const [revoking, setRevoking] = useState(false);

  const addPasskey = async () => {
    try {
      const { token, options } = await api<{ token: string; options: Record<string, unknown> }>("/api/auth/passkeys/options", {
        method: "POST",
      });
      const credential = await createPasskey(options);
      await api("/api/auth/passkeys", { body: { token, credential } });
      toast("Passkey added.");
      passkeys.reload();
      audit.reload();
    } catch (e) {
      const m = passkeyMessage(e);
      if (m) toast(m, "error");
    }
  };

  const removePasskey = async (p: Passkey) => {
    try {
      await api(`/api/auth/passkeys/${p.id}`, { method: "DELETE" });
      toast(`${p.name} removed.`);
      passkeys.reload();
      audit.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const signOutOthers = async () => {
    try {
      const r = await api<{ signed_out: number }>("/api/auth/sessions/sign-out-others", { method: "POST" });
      toast(r.signed_out ? `Signed out ${r.signed_out} other device${r.signed_out === 1 ? "" : "s"}.` : "No other devices were signed in.");
      sessions.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <>
      <SubPageTitle title="Security" back="/settings" subtitle="How you sign in, where you're signed in, and what happened." />
      <div className="grid grid-cols-1 gap-4">
        <Card>
          <CardHeader>
            <CardTitle>Passkeys</CardTitle>
            <KeyRound className="size-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-3">
            <p className="text-sm text-muted-foreground">
              Sign in with Face ID, Touch ID or a fingerprint. Add one on each phone and laptop you use.
            </p>
            {passkeys.data === null ? (
              <Skeleton className="h-12 rounded-xl" />
            ) : passkeys.data.length === 0 ? (
              <p className="rounded-xl bg-warning/10 px-3.5 py-3 text-sm font-medium text-warning">
                No passkey yet: you sign in with a password and code.
              </p>
            ) : (
              <ul className="divide-y rounded-xl border">
                {passkeys.data.map((p) => (
                  <li key={p.id} className="flex items-center gap-3 px-4 py-3">
                    <KeyRound className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{p.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {p.last_used_at
                          ? `Last used ${when.format(new Date(p.last_used_at))}`
                          : `Added ${when.format(new Date(p.created_at))}`}
                      </p>
                    </div>
                    <Button variant="ghost" size="icon" aria-label={`Remove ${p.name}`} onClick={() => removePasskey(p)} disabled={demo}>
                      <Trash2 />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            {passkeysSupported() && (
              <Button variant="outline" onClick={addPasskey} disabled={demo} className="justify-self-start">
                <KeyRound /> Add a passkey on this device
              </Button>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Signed in</CardTitle>
            <Smartphone className="size-4 text-muted-foreground" aria-hidden />
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-3">
            {sessions.data === null ? (
              <Skeleton className="h-12 rounded-xl" />
            ) : (
              <ul className="divide-y rounded-xl border">
                {sessions.data.map((s) => (
                  <li key={s.id} className="flex items-center gap-3 px-4 py-3">
                    {/iPhone|Android|iPad/.test(s.device) ? (
                      <Smartphone className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    ) : (
                      <Laptop className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{s.device}</p>
                      <p className="text-xs text-muted-foreground">
                        {s.method === "passkey" ? "Passkey" : "Password and code"} · active {when.format(new Date(s.last_seen_at))}
                      </p>
                    </div>
                    {s.current && <Badge>This device</Badge>}
                  </li>
                ))}
              </ul>
            )}
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={signOutOthers} disabled={demo}>
                Sign out everywhere else
              </Button>
              <Button variant="ghost" onClick={() => setPassword(true)} disabled={demo}>
                <LockKeyhole /> Change password
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">Sessions end after 15 minutes without use, and after 12 hours at most.</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Activity</CardTitle>
          </CardHeader>
          <CardContent>
            {audit.data === null ? (
              <Skeleton className="h-32 rounded-xl" />
            ) : (
              <ol className="grid grid-cols-1 gap-3">
                {audit.data.map((e) => (
                  <li key={e.id} className="flex items-start gap-3 text-sm">
                    <span
                      className={
                        WARN.has(e.action)
                          ? "mt-1.5 size-2 shrink-0 rounded-full bg-warning"
                          : "mt-1.5 size-2 shrink-0 rounded-full bg-muted-foreground/40"
                      }
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">
                        {e.text}
                        {e.who && <span className="font-normal text-muted-foreground"> · {e.who}</span>}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">{describe(e)}</p>
                    </div>
                    <time dateTime={e.at} className="shrink-0 text-xs text-muted-foreground">
                      {when.format(new Date(e.at))}
                    </time>
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>

        {user?.role === "owner" && (
          <Card className="border-negative/30">
            <CardHeader>
              <CardTitle className="text-negative">If something feels wrong</CardTitle>
              <ShieldAlert className="size-4 text-negative" aria-hidden />
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-3">
              <p className="text-sm text-muted-foreground">
                Revoke every account connection at Plaid and SnapTrade and delete every token on this server. Your history stays; nothing
                syncs until you link accounts again.
              </p>
              <Button variant="destructive" className="justify-self-start" onClick={() => setRevoking(true)} disabled={demo}>
                Revoke all connections
              </Button>
            </CardContent>
          </Card>
        )}
      </div>
      <PasswordSheet open={password} onOpenChange={setPassword} />
      <RevokeSheet open={revoking} onOpenChange={setRevoking} onDone={audit.reload} />
    </>
  );
}

function describe(e: Event): string {
  const d = e.detail;
  const parts = [
    d.device,
    d.institution,
    d.method === "passkey" ? "with a passkey" : d.method === "password" ? "with password and code" : null,
    d.step ? `at the ${d.step} step` : null,
    d.format,
  ];
  return parts.filter(Boolean).join(" · ") || " ";
}

function PasswordSheet({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api("/api/auth/password", { body: { password } });
      toast("Password changed. Other devices were signed out.");
      setPassword("");
      onOpenChange(false);
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <Sheet open={open} onOpenChange={onOpenChange} title="Change password" description="Other devices are signed out when it changes.">
      <form onSubmit={save} className="grid grid-cols-1 gap-4 pb-2">
        <Field id="np" label="New password" hint="At least 12 characters." error={error}>
          <Input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={12}
            required
          />
        </Field>
        <Button size="lg" type="submit" disabled={password.length < 12}>
          Change password
        </Button>
      </form>
    </Sheet>
  );
}

function RevokeSheet({ open, onOpenChange, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; onDone: () => void }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const revoke = async () => {
    setBusy(true);
    try {
      const r = await api<{ revoked: number; problems: string[] }>("/api/connections/revoke-all", { method: "POST" });
      toast(
        r.problems.length
          ? `Revoked ${r.revoked}. ${r.problems.join(" ")}`
          : `Revoked ${r.revoked} connection${r.revoked === 1 ? "" : "s"}.`,
        r.problems.length ? "error" : "ok",
      );
      resetHousehold();
      setTyped("");
      onOpenChange(false);
      onDone();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Sheet
      open={open}
      onOpenChange={onOpenChange}
      title="Revoke all connections?"
      description="This can't be undone: each account has to be linked again."
    >
      <div className="grid grid-cols-1 gap-4 pb-2">
        <Field id="confirm" label="Type REVOKE to confirm">
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" autoCapitalize="characters" />
        </Field>
        <Button variant="destructive" size="lg" disabled={typed !== "REVOKE" || busy} onClick={revoke}>
          Revoke everything
        </Button>
      </div>
    </Sheet>
  );
}
