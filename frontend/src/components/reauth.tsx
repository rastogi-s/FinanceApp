"use client";

import { useState, useSyncExternalStore } from "react";
import { KeyRound } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { getPasskey, passkeyMessage, passkeysSupported } from "@/lib/webauthn";
import { Button } from "@/components/ui/button";
import { CodeInput } from "@/components/ui/code-input";
import { Sheet } from "@/components/ui/sheet";

// One request at a time; anything else that needs it waits for the same answer.
let pending: { promise: Promise<boolean>; resolve: (ok: boolean) => void } | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

/** Asks the person to confirm it's them (passkey or code). Resolves true when they did. */
export function requestReauth(): Promise<boolean> {
  if (!pending) {
    let resolve!: (ok: boolean) => void;
    const promise = new Promise<boolean>((r) => (resolve = r));
    pending = { promise, resolve };
    emit();
  }
  return pending.promise;
}

function finish(ok: boolean) {
  pending?.resolve(ok);
  pending = null;
  emit();
}

export function ReauthDialog() {
  const open = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => pending !== null,
    () => false,
  );
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const done = (ok: boolean) => {
    setCode("");
    setError(null);
    finish(ok);
  };

  const withPasskey = async () => {
    setError(null);
    setBusy(true);
    try {
      const { token, options } = await api<{ token: string; options: Record<string, unknown> }>("/api/auth/passkey/options", {
        body: { purpose: "reauth" },
      });
      const credential = await getPasskey(options);
      await api("/api/auth/passkey/verify", { body: { token, credential, purpose: "reauth" } });
      done(true);
    } catch (e) {
      const message = e instanceof ApiError ? e.message : passkeyMessage(e);
      if (message) setError(message);
    } finally {
      setBusy(false);
    }
  };

  const withCode = async (value: string) => {
    setError(null);
    setBusy(true);
    try {
      await api("/api/auth/reauth", { body: { code: value } });
      done(true);
    } catch (e) {
      setError((e as Error).message);
      setCode("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet
      open={open}
      onOpenChange={(o) => !o && done(false)}
      title="Confirm it's you"
      description="Changes like this need a quick check, in case someone else picked up your phone."
    >
      <div className="grid grid-cols-1 gap-5 pb-2">
        {passkeysSupported() && (
          <Button size="lg" onClick={withPasskey} disabled={busy}>
            <KeyRound /> Use your passkey
          </Button>
        )}
        <div className="grid grid-cols-1 gap-2">
          <label htmlFor="reauth-code" className="text-sm text-muted-foreground">
            Or the 6-digit code from your authenticator app
          </label>
          <CodeInput id="reauth-code" value={code} onChange={setCode} onComplete={withCode} disabled={busy} autoFocus={false} />
        </div>
        {error && (
          <p role="alert" className="text-sm font-medium text-negative">
            {error}
          </p>
        )}
      </div>
    </Sheet>
  );
}
