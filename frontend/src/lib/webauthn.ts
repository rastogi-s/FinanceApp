"use client";

import { useSyncExternalStore } from "react";

/** Passkeys in the browser: the server's JSON options in, the authenticator's JSON answer out. */

function fromB64url(value: string): ArrayBuffer {
  const b64 = value
    .replace(/-/g, "+")
    .replace(/_/g, "/")
    .padEnd(Math.ceil(value.length / 4) * 4, "=");
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

function toB64url(buffer: ArrayBuffer | null | undefined): string | undefined {
  if (!buffer) return undefined;
  const bytes = new Uint8Array(buffer);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

type Descriptor = { id: string; type: string; transports?: string[] };

export function passkeysSupported(): boolean {
  return typeof window !== "undefined" && "PublicKeyCredential" in window && !!navigator.credentials;
}

const noSubscribe = () => () => {};

// The sign-in page asks for the passkey on its own, unless this device has shown it doesn't use one here: the last
// sign-in on it was with password and code, or its passkey wasn't one of ours. Using or adding a passkey clears that.
// It holds no secret: the passkey itself is the proof.
const SKIP = "passkey-skip";

export function passkeyHere(): boolean {
  try {
    return localStorage.getItem(SKIP) !== "1";
  } catch {
    return true;
  }
}

export function rememberPasskeyHere(here: boolean): void {
  try {
    if (here) localStorage.removeItem(SKIP);
    else localStorage.setItem(SKIP, "1");
  } catch {
    // private window or blocked storage: the sign-in page asks each time it opens, which a tap on Cancel answers
  }
}

/** Whether this device has Face ID, Touch ID or Windows Hello for passkeys, so asking won't open a QR-code sheet. */
export async function platformPasskeys(): Promise<boolean> {
  try {
    return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable();
  } catch {
    return false;
  }
}

/** Whether this browser can use passkeys. Pages are pre-rendered, so the server's guess is "yes". */
export function usePasskeysSupported(): boolean {
  return useSyncExternalStore(noSubscribe, passkeysSupported, () => true);
}

export async function createPasskey(options: Record<string, unknown>): Promise<object> {
  const o = options as {
    challenge: string;
    user: { id: string; name: string; displayName: string };
    excludeCredentials?: Descriptor[];
  } & Record<string, unknown>;
  const publicKey = {
    ...o,
    challenge: fromB64url(o.challenge),
    user: { ...o.user, id: fromB64url(o.user.id) },
    excludeCredentials: (o.excludeCredentials ?? []).map((c) => ({ ...c, id: fromB64url(c.id) })),
  } as unknown as PublicKeyCredentialCreationOptions;
  const cred = (await navigator.credentials.create({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new Error("No passkey was made.");
  rememberPasskeyHere(true);
  const response = cred.response as AuthenticatorAttestationResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: toB64url(response.clientDataJSON),
      attestationObject: toB64url(response.attestationObject),
      transports: response.getTransports?.() ?? [],
    },
    clientExtensionResults: cred.getClientExtensionResults(),
    authenticatorAttachment: cred.authenticatorAttachment ?? undefined,
  };
}

export async function getPasskey(options: Record<string, unknown>): Promise<object> {
  const o = options as { challenge: string; allowCredentials?: Descriptor[] } & Record<string, unknown>;
  const publicKey = {
    ...o,
    challenge: fromB64url(o.challenge),
    allowCredentials: (o.allowCredentials ?? []).map((c) => ({ ...c, id: fromB64url(c.id) })),
  } as unknown as PublicKeyCredentialRequestOptions;
  const cred = (await navigator.credentials.get({ publicKey })) as PublicKeyCredential | null;
  if (!cred) throw new Error("No passkey was used.");
  rememberPasskeyHere(true);
  const response = cred.response as AuthenticatorAssertionResponse;
  return {
    id: cred.id,
    rawId: toB64url(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: toB64url(response.clientDataJSON),
      authenticatorData: toB64url(response.authenticatorData),
      signature: toB64url(response.signature),
      userHandle: toB64url(response.userHandle),
    },
    clientExtensionResults: cred.getClientExtensionResults(),
  };
}

/** Turns the browser's passkey errors into words. "Cancelled" is not an error worth shouting about. */
export function passkeyMessage(e: unknown): string | null {
  const name = (e as { name?: string })?.name;
  if (name === "NotAllowedError" || name === "AbortError") return null;
  if (name === "InvalidStateError") return "That passkey is already added.";
  if (name === "SecurityError") return "Passkeys need the app's secure address (https). Open it through Tailscale.";
  return (e as Error)?.message || "The passkey didn't work. Try again.";
}
