"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, Copy, KeyRound, ShieldCheck, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { CodeInput } from "@/components/ui/code-input";
import { Field, Input } from "@/components/ui/input";
import { toast } from "@/components/ui/toast";
import { api, ApiError } from "@/lib/api";
import { useAuth, type User } from "@/lib/auth";
import { createPasskey, passkeyMessage, passkeysSupported } from "@/lib/webauthn";
import { cn } from "@/lib/utils";

type Enroll = { token: string; totp_uri: string; secret: string; qr: string };
type Step = "details" | "authenticator" | "passkey";

const STEPS: { key: Step; label: string }[] = [
  { key: "details", label: "You" },
  { key: "authenticator", label: "Code app" },
  { key: "passkey", label: "Passkey" },
];

/**
 * Creating a sign-in, for the first person (with the setup code from the server's log) or someone invited:
 * details and a password, then an authenticator app (required: a password alone never signs anyone in), then a
 * passkey so day-to-day sign-in is just Face ID or a fingerprint.
 */
export function Enrollment({ mode, inviteCode, inviteName }: { mode: "setup" | "invite"; inviteCode?: string; inviteName?: string }) {
  const { signedIn } = useAuth();
  const router = useRouter();
  const [step, setStep] = useState<Step>("details");
  const [setupCode, setSetupCode] = useState("");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [enroll, setEnroll] = useState<Enroll | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const run = async (fn: () => Promise<void>) => {
    setError(null);
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      const message = e instanceof ApiError ? e.message : passkeyMessage(e);
      if (message) setError(message);
    } finally {
      setBusy(false);
    }
  };

  const submitDetails = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      const r =
        mode === "setup"
          ? await api<Enroll>("/api/auth/setup", { body: { code: setupCode, name, email, password } })
          : await api<Enroll>("/api/auth/invite/accept", { body: { code: inviteCode, email, password } });
      setPassword("");
      setEnroll(r);
      setStep("authenticator");
    });
  };

  const submitCode = (value: string) =>
    enroll &&
    run(async () => {
      try {
        const r = await api<{ user: User; csrf: string }>("/api/auth/enroll", { body: { token: enroll.token, code: value } });
        signedIn(r.user, r.csrf);
        setStep("passkey");
      } catch (e) {
        setCode("");
        throw e;
      }
    });

  const addPasskey = () =>
    run(async () => {
      const { token, options } = await api<{ token: string; options: Record<string, unknown> }>("/api/auth/passkeys/options", {
        method: "POST",
      });
      const credential = await createPasskey(options);
      await api("/api/auth/passkeys", { body: { token, credential } });
      toast("Passkey added. Next time, just use Face ID or your fingerprint.");
      finish();
    });

  const finish = () => router.replace(mode === "setup" ? "/settings/accounts?welcome=1" : "/");

  const strength = passwordHint(password);
  const index = STEPS.findIndex((s) => s.key === step);

  return (
    <Card className="p-1">
      <CardContent className="grid grid-cols-1 gap-6 pt-6">
        <ol className="flex items-center gap-2" aria-label="Steps">
          {STEPS.map((s, i) => (
            <li key={s.key} className="flex flex-1 items-center gap-2" aria-current={i === index ? "step" : undefined}>
              <span
                className={cn(
                  "grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold",
                  i < index
                    ? "bg-primary text-primary-foreground"
                    : i === index
                      ? "bg-accent text-accent-foreground"
                      : "bg-muted text-muted-foreground",
                )}
              >
                {i < index ? <Check className="size-3.5" aria-hidden /> : i + 1}
              </span>
              <span className={cn("text-xs font-medium whitespace-nowrap", i === index ? "text-foreground" : "text-muted-foreground")}>
                {s.label}
              </span>
              {i < STEPS.length - 1 && <span className="h-px flex-1 bg-border" aria-hidden />}
            </li>
          ))}
        </ol>

        {step === "details" && (
          <form onSubmit={submitDetails} className="grid grid-cols-1 gap-5">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">
                {mode === "setup" ? "Set up Finance" : `Welcome${inviteName ? `, ${inviteName}` : ""}`}
              </h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {mode === "setup"
                  ? "Create the first sign-in. You can invite your partner afterwards."
                  : "You've been invited to see the household's money. Create your sign-in."}
              </p>
            </div>
            {mode === "setup" && (
              <>
                <Field id="setup-code" label="Setup code" hint="Printed in the server's log when the app starts: docker compose logs app">
                  <Input
                    value={setupCode}
                    onChange={(e) => setSetupCode(e.target.value.toUpperCase())}
                    placeholder="ABCD-EFGH-JKLM"
                    autoComplete="off"
                    autoCapitalize="characters"
                    spellCheck={false}
                    className="font-mono tracking-wider"
                    required
                  />
                </Field>
                <Field id="name" label="Your name">
                  <Input value={name} onChange={(e) => setName(e.target.value)} autoComplete="given-name" required />
                </Field>
              </>
            )}
            <Field id="email" label="Email">
              <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required />
            </Field>
            <Field id="new-password" label="Password" hint={strength}>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                minLength={12}
                required
              />
            </Field>
            <Button size="lg" type="submit" disabled={busy || password.length < 12}>
              Continue
            </Button>
          </form>
        )}

        {step === "authenticator" && enroll && (
          <div className="grid grid-cols-1 gap-5">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Add a code app</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Scan this with an authenticator app (1Password, Google Authenticator, Authy, or your phone&apos;s Passwords app). It&apos;s
                the backup way in if a passkey isn&apos;t handy.
              </p>
            </div>
            <div className="flex flex-col items-center gap-3 rounded-2xl bg-white p-4">
              {/* eslint-disable-next-line @next/next/no-img-element -- a data: URI made on the server, not a remote image */}
              <img src={enroll.qr} alt="QR code for your authenticator app" className="size-48" width={192} height={192} />
            </div>
            <div className="flex items-center gap-2 rounded-xl bg-muted px-3 py-2">
              <code className="min-w-0 flex-1 font-mono text-xs leading-relaxed break-words" aria-label="Setup key">
                {enroll.secret.match(/.{1,4}/g)?.join(" ")}
              </code>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  navigator.clipboard?.writeText(enroll.secret).then(
                    () => setCopied(true),
                    () => {},
                  );
                }}
              >
                {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy key"}
              </Button>
            </div>
            <div className="grid grid-cols-1 gap-2">
              <label htmlFor="enroll-code" className="text-sm font-medium">
                Then type the 6-digit code it shows
              </label>
              <CodeInput id="enroll-code" value={code} onChange={setCode} onComplete={submitCode} disabled={busy} autoFocus={false} />
            </div>
          </div>
        )}

        {step === "passkey" && (
          <div className="grid grid-cols-1 gap-5">
            <div className="grid size-14 place-items-center rounded-2xl bg-accent text-accent-foreground">
              <ShieldCheck className="size-7" aria-hidden />
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Sign in with a glance</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                Add a passkey and signing in is just Face ID, Touch ID or your fingerprint. It can&apos;t be phished or guessed, and it
                never leaves your device.
              </p>
            </div>
            {passkeysSupported() ? (
              <Button size="lg" onClick={addPasskey} disabled={busy}>
                <KeyRound /> Add a passkey
              </Button>
            ) : (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Smartphone className="size-4" aria-hidden /> This browser can&apos;t make passkeys. Add one later from your phone.
              </p>
            )}
            <Button variant="ghost" onClick={finish}>
              Not now
            </Button>
          </div>
        )}

        {error && (
          <p role="alert" className="-mt-1 text-sm font-medium text-negative">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function passwordHint(p: string): string {
  if (!p) return "At least 12 characters. A few unrelated words make a strong one you'll remember.";
  if (p.length < 12) return `${12 - p.length} more character${12 - p.length === 1 ? "" : "s"}.`;
  if (p.length < 16) return "Good. A little longer is even better.";
  return "Strong.";
}
