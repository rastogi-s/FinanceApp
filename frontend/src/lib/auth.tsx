"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, setApiHandlers, setCsrf } from "@/lib/api";
import { resetHousehold } from "@/lib/household";
import { hasKept, keepSent } from "@/lib/offers";
import { passkeyHere } from "@/lib/webauthn";

export type User = { id: number; name: string; email: string; role: "owner" | "member" };
type AuthState =
  | { status: "loading" }
  | { status: "setup" }
  | { status: "signed-out"; reason?: "idle" | "out" }
  | { status: "signed-in"; user: User; demo: boolean };

type Ctx = {
  state: AuthState;
  refresh: () => Promise<AuthState>;
  signedIn: (user: User, csrf: string) => void;
  signOut: () => Promise<void>;
};

const AuthContext = createContext<Ctx | null>(null);

// Pages anyone can open. Everything else needs a signed-in person.
export const PUBLIC_PATHS = ["/signin", "/setup", "/welcome"];
export const isPublic = (path: string) => PUBLIC_PATHS.some((p) => path === p || path.startsWith(p + "/"));

// The page you were sent to sign in from (a tapped notification, or a page left open until the session ended), so
// signing in carries on there instead of starting at Home. Kept in this tab only.
const PLACE = "signin-return";

function keepPlace() {
  const place = window.location.pathname + window.location.search + window.location.hash;
  try {
    if (place === "/") sessionStorage.removeItem(PLACE);
    else sessionStorage.setItem(PLACE, place);
  } catch {}
}

/** Where to go after signing in: the page you were sent here from, or Home. Only a page of this app. */
export function placeAfterSignIn(): string {
  let place: string | null = null;
  try {
    place = sessionStorage.getItem(PLACE);
  } catch {}
  if (!place || !place.startsWith("/") || place.startsWith("//") || place.includes("\\")) return "/";
  const url = new URL(place, window.location.origin);
  return url.origin !== window.location.origin || isPublic(url.pathname) ? "/" : place;
}

function forgetPlace() {
  try {
    sessionStorage.removeItem(PLACE);
  } catch {}
}

// Leaving the app locks it on a device that signs in with a passkey: the session ends on the server and the next open
// asks for Face ID. Swiping the app away locks it straight away (a reload or a link followed within the app doesn't);
// switching to another app locks it after a minute, so a quick look at a text or a code stays signed in.
const LEFT = "left-at";
const LOCK_AFTER_MS = 60_000;
const LOCK_AFTER_CLOSE_MS = 5_000;

function noteLeft() {
  try {
    localStorage.setItem(LEFT, String(Date.now()));
  } catch {}
}

function awayTooLong(opened: boolean): boolean {
  let at = 0;
  try {
    at = Number(localStorage.getItem(LEFT)) || 0;
    localStorage.removeItem(LEFT);
  } catch {}
  const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
  const grace = opened && nav?.type !== "reload" ? LOCK_AFTER_CLOSE_MS : LOCK_AFTER_MS;
  return at > 0 && Date.now() - at > grace && passkeyHere();
}

async function lock() {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  setCsrf(null);
  resetHousehold();
}

type ServerState = { demo: boolean; setup_required: boolean; user: User | null; csrf: string | null; idle_minutes?: number };

async function fetchState(idleMinutes: { current: number }, opened = false): Promise<AuthState> {
  try {
    const s = await api<ServerState>("/api/auth/state", { quiet401: true });
    setCsrf(s.csrf);
    idleMinutes.current = s.idle_minutes ?? 15;
    if (s.user && !s.demo && awayTooLong(opened)) {
      await lock();
      return { status: "signed-out", reason: "idle" };
    }
    return s.user ? { status: "signed-in", user: s.user, demo: s.demo } : s.setup_required ? { status: "setup" } : { status: "signed-out" };
  } catch {
    return { status: "signed-out" };
  }
}

export function AuthProvider({ children, reauth }: { children: React.ReactNode; reauth: () => Promise<boolean> }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const idleMinutes = useRef(15);
  const lastActive = useRef(0);
  const router = useRouter();
  const pathname = usePathname();

  const refresh = useCallback(async (): Promise<AuthState> => {
    const next = await fetchState(idleMinutes);
    setState(next);
    return next;
  }, []);

  const signedIn = useCallback((user: User, csrf: string) => {
    setCsrf(csrf);
    resetHousehold();
    lastActive.current = Date.now();
    setState({ status: "signed-in", user, demo: false });
  }, []);

  const signOut = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    setCsrf(null);
    resetHousehold();
    setState({ status: "signed-out", reason: "out" });
    router.replace("/signin");
  }, [router]);

  useEffect(() => {
    let live = true;
    fetchState(idleMinutes, true).then((next) => live && setState(next));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    setApiHandlers({
      signedOut: () => {
        setCsrf(null);
        resetHousehold();
        setState({ status: "signed-out", reason: "idle" });
      },
      reauth,
    });
  }, [reauth]);

  // The server ends a session after 15 idle minutes. Follow it here, so money doesn't stay on an unattended screen.
  useEffect(() => {
    if (state.status !== "signed-in" || state.demo) return;
    lastActive.current = Date.now();
    const touch = () => (lastActive.current = Date.now());
    const events = ["pointerdown", "keydown", "scroll", "touchstart"] as const;
    events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    const check = () => {
      if (Date.now() - lastActive.current > idleMinutes.current * 60_000) {
        setCsrf(null);
        resetHousehold();
        setState({ status: "signed-out", reason: "idle" });
      }
    };
    const timer = setInterval(check, 20_000);
    // A phone pauses timers while the app is in the background, so check the moment it comes back, before money shows.
    const back = () => {
      if (document.visibilityState !== "visible") return noteLeft();
      if (awayTooLong(false)) lock().then(() => setState({ status: "signed-out", reason: "idle" }));
      else check();
    };
    document.addEventListener("visibilitychange", back);
    window.addEventListener("pageshow", back);
    window.addEventListener("pagehide", noteLeft);
    return () => {
      events.forEach((e) => window.removeEventListener(e, touch));
      clearInterval(timer);
      document.removeEventListener("visibilitychange", back);
      window.removeEventListener("pageshow", back);
      window.removeEventListener("pagehide", noteLeft);
    };
  }, [state]);

  // Send people where they need to be.
  useEffect(() => {
    if (state.status === "loading") return;
    if (state.status === "setup" && pathname !== "/setup" && !pathname.startsWith("/welcome")) router.replace("/setup");
    if (state.status === "signed-out" && !isPublic(pathname)) {
      keepSent(); // card offers sent by the bookmark wait in this tab until you're signed in
      if (state.reason !== "out") keepPlace(); // signing out on purpose starts afresh at Home
      else forgetPlace();
      router.replace(state.reason === "idle" ? "/signin?idle=1" : "/signin");
    }
    if (state.status === "signed-in" && !isPublic(pathname)) forgetPlace(); // arrived
    if (state.status === "signed-in" && hasKept() && pathname !== "/cards") router.replace("/cards");
  }, [state, pathname, router]);

  return <AuthContext.Provider value={{ state, refresh, signedIn, signOut }}>{children}</AuthContext.Provider>;
}

export function useAuth(): Ctx {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

export function useUser(): User | null {
  const { state } = useAuth();
  return state.status === "signed-in" ? state.user : null;
}
