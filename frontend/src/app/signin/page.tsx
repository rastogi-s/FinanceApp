"use client";

import { Suspense, useEffect, useEffectEvent, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { KeyRound, Moon } from "lucide-react";
import { BackHeading } from "@/components/auth/back-heading";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { CodeInput } from "@/components/ui/code-input";
import { Field, Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { placeAfterSignIn, useAuth, type User } from "@/lib/auth";
import { getPasskey, passkeyHere, passkeyMessage, rememberPasskeyHere, usePasskeysSupported } from "@/lib/webauthn";

type Step = { kind: "start" } | { kind: "password" } | { kind: "code"; token: string };
type SignedIn = { user: User; csrf: string };

// Opening the app signed out asks for the passkey straight away, once. Cancelling leaves the button to tap; the
// automatic ask comes back only when the app is opened again (a reload, or back after a while in the background),
// never in a loop. These live outside the page so a re-render or remount can't ask twice.
const REOPENED_AFTER_MS = 10_000;
let askedThisOpening = false;
let hiddenAt = 0;

export default function SignInPage() {
  return (
    <Suspense>
      <SignIn />
    </Suspense>
  );
}

function SignIn() {
  const { state, signedIn } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [step, setStep] = useState<Step>({ kind: "start" });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canPasskey = usePasskeysSupported();
  const [openings, setOpenings] = useState(0);

  useEffect(() => {
    if (state.status === "signed-in" && !state.demo) router.replace(placeAfterSignIn());
  }, [state, router]);

  const done = (r: SignedIn) => {
    askedThisOpening = false; // the next time this tab lands here signed out is a new opening
    signedIn(r.user, r.csrf);
    router.replace(placeAfterSignIn());
  };

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

  const passkey = () =>
    run(async () => {
      const { token, options } = await api<{ token: string; options: Record<string, unknown> }>("/api/auth/passkey/options", { body: {} });
      const credential = await getPasskey(options);
      try {
        done(await api<SignedIn>("/api/auth/passkey/verify", { body: { token, credential } }));
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) rememberPasskeyHere(false); // not one of ours: stop asking on open
        throw e;
      }
    });

  // Not after signing out on purpose (you meant to leave), and only on a device that has used a passkey here before.
  const askOnOpen =
    canPasskey && step.kind === "start" && state.status === "signed-out" && state.reason !== "out" && passkeyHere();
  const askNow = useEffectEvent(() => {
    if (askedThisOpening || busy || document.visibilityState !== "visible") return;
    askedThisOpening = true;
    passkey();
  });

  useEffect(() => {
    if (!askOnOpen) return;
    const t = setTimeout(askNow, 0); // after the page has painted, so the sheet rises over it
    return () => clearTimeout(t);
  }, [askOnOpen, openings]);

  useEffect(() => {
    const seen = () => {
      if (document.visibilityState === "hidden") {
        hiddenAt = Date.now();
      } else if (hiddenAt && Date.now() - hiddenAt > REOPENED_AFTER_MS) {
        hiddenAt = 0;
        askedThisOpening = false;
        setOpenings((n) => n + 1);
      }
    };
    document.addEventListener("visibilitychange", seen);
    return () => document.removeEventListener("visibilitychange", seen);
  }, []);

  const submitPassword = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      const r = await api<{ token: string }>("/api/auth/login", { body: { email, password } });
      setPassword("");
      setStep({ kind: "code", token: r.token });
    });
  };

  const submitCode = (value: string) =>
    step.kind === "code" &&
    run(async () => {
      try {
        done(await api<SignedIn>("/api/auth/login/code", { body: { token: step.token, code: value } }));
      } catch (e) {
        setCode("");
        if (e instanceof ApiError && e.status === 410) setStep({ kind: "password" });
        throw e;
      }
    });

  return (
    <Card className="p-1">
      <CardContent className="grid grid-cols-1 gap-6 pt-6">
        {params.get("idle") && step.kind === "start" && (
          <p className="flex items-center gap-2.5 rounded-xl bg-muted px-3.5 py-3 text-sm text-muted-foreground" role="status">
            <Moon className="size-4 shrink-0" aria-hidden /> Signed out after a while away, to keep your numbers private.
          </p>
        )}

        {step.kind === "start" && (
          <>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Welcome back</h1>
              <p className="mt-1 text-sm text-muted-foreground">Your household&apos;s money, for your eyes only.</p>
            </div>
            {canPasskey && (
              <Button size="lg" onClick={passkey} disabled={busy}>
                <KeyRound /> Sign in with passkey
              </Button>
            )}
            <Button variant={canPasskey ? "outline" : "default"} size="lg" onClick={() => setStep({ kind: "password" })}>
              Use password and code
            </Button>
          </>
        )}

        {step.kind === "password" && (
          <form onSubmit={submitPassword} className="grid grid-cols-1 gap-5">
            <BackHeading title="Password and code" onBack={() => setStep({ kind: "start" })} />
            <Field id="email" label="Email">
              <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
            </Field>
            <Field id="password" label="Password">
              <Input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </Field>
            <Button size="lg" type="submit" disabled={busy || !email || !password}>
              Continue
            </Button>
          </form>
        )}

        {step.kind === "code" && (
          <div className="grid grid-cols-1 gap-5">
            <BackHeading title="Enter your code" onBack={() => setStep({ kind: "password" })} />
            <p className="-mt-2 text-sm text-muted-foreground">Open your authenticator app and type the 6-digit code for Finance.</p>
            <CodeInput id="code" value={code} onChange={setCode} onComplete={submitCode} disabled={busy} />
            <label htmlFor="code" className="sr-only">
              6-digit code
            </label>
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
