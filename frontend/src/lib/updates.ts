"use client";

/**
 * Updates: is a newer version out, and how "Update now" is going. The server asks GitHub; the browser only asks the
 * server. "Update now" leaves a request that the server's own updater (update.sh) picks up, so this page can only
 * watch: it polls until the server comes back on the new version.
 */

import { useSyncExternalStore } from "react";

export type ReleaseNotes = {
  version: string;
  title: string;
  features: { title: string; body: string }[];
};
export type ServerState = "checking" | "installing" | "done" | "current" | "failed" | "rolled_back";

export type Updates = {
  current: string;
  latest: string | null;
  available: boolean;
  published_at: string | null;
  releases: ReleaseNotes[];
  checked_at: string | null;
  check_failed: boolean;
  check_enabled: boolean;
  server: { in_app: boolean; nightly: boolean };
  status: {
    state: ServerState;
    from: string | null;
    to: string | null;
    at: string | null;
  } | null;
  requested_at: string | null;
  stuck: boolean;
  can_install: boolean;
};

export const ONE_TIME_SETUP = "cd /opt/financeapp && sudo ./update.sh";

const ago = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });
export function since(iso: string | null): string {
  if (!iso) return "never";
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60000);
  if (minutes > -1) return "just now";
  if (minutes > -60) return ago.format(minutes, "minute");
  if (minutes > -60 * 24) return ago.format(Math.round(minutes / 60), "hour");
  return ago.format(Math.round(minutes / 1440), "day");
}

// "Later" on the banner hides it until the next version. Kept per browser; the Settings card always shows.
const listeners = new Set<() => void>();
let memory: string | null = null;

function readDismissed(): string | null {
  try {
    return localStorage.getItem("update-dismissed");
  } catch {
    return memory;
  }
}

export function useDismissed(): [string | null, (version: string) => void] {
  const value = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    readDismissed,
    () => null,
  );
  const dismiss = (version: string) => {
    memory = version;
    try {
      localStorage.setItem("update-dismissed", version);
    } catch {}
    listeners.forEach((l) => l());
  };
  return [value, dismiss];
}
