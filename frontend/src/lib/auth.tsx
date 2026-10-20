"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, setApiHandlers, setCsrf } from "@/lib/api";
import { resetHousehold } from "@/lib/household";

export type User = { id: number; name: string; email: string; role: "owner" | "member" };
type AuthState =
  | { status: "loading" }
  | { status: "setup" }
  | { status: "signed-out"; reason?: "idle" }
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

type ServerState = { demo: boolean; setup_required: boolean; user: User | null; csrf: string | null; idle_minutes?: number };

async function fetchState(idleMinutes: { current: number }): Promise<AuthState> {
  try {
    const s = await api<ServerState>("/api/auth/state", { quiet401: true });
    setCsrf(s.csrf);
    idleMinutes.current = s.idle_minutes ?? 15;
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
    setState({ status: "signed-out" });
    router.replace("/signin");
  }, [router]);

  useEffect(() => {
    let live = true;
    fetchState(idleMinutes).then((next) => live && setState(next));
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
    const timer = setInterval(() => {
      if (Date.now() - lastActive.current > idleMinutes.current * 60_000) {
        setCsrf(null);
        resetHousehold();
        setState({ status: "signed-out", reason: "idle" });
      }
    }, 20_000);
    return () => {
      events.forEach((e) => window.removeEventListener(e, touch));
      clearInterval(timer);
    };
  }, [state]);

  // Send people where they need to be.
  useEffect(() => {
    if (state.status === "loading") return;
    if (state.status === "setup" && pathname !== "/setup" && !pathname.startsWith("/welcome")) router.replace("/setup");
    if (state.status === "signed-out" && !isPublic(pathname)) {
      router.replace(state.reason === "idle" ? "/signin?idle=1" : "/signin");
    }
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
