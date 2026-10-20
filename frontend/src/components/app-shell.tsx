"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";
import { ChartPie, Eye, EyeOff, House, PiggyBank, ReceiptText, Settings, TrendingUp } from "lucide-react";
import { ReauthDialog, requestReauth } from "@/components/reauth";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/toast";
import { AuthProvider, isPublic, useAuth } from "@/lib/auth";
import { useMeta } from "@/lib/household";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/", label: "Home", icon: House },
  { href: "/spending", label: "Spending", icon: ReceiptText },
  { href: "/budget", label: "Budget", icon: PiggyBank },
  { href: "/portfolio", label: "Portfolio", icon: ChartPie },
  { href: "/plan", label: "Plan", icon: TrendingUp },
] as const;

// The privacy setting lives in localStorage (or in memory when storage is blocked) and is shared by every tab.
let privacyMemory = false;
const privacyListeners = new Set<() => void>();

function readPrivacy(): boolean {
  try {
    return localStorage.getItem("privacy") === "on";
  } catch {
    return privacyMemory;
  }
}

function subscribePrivacy(onChange: () => void) {
  privacyListeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    privacyListeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

function usePrivacy(): [boolean, () => void] {
  const on = useSyncExternalStore(subscribePrivacy, readPrivacy, () => false);
  useEffect(() => {
    document.documentElement.dataset.privacy = on ? "on" : "off";
  }, [on]);
  const toggle = () => {
    privacyMemory = !on;
    try {
      localStorage.setItem("privacy", privacyMemory ? "on" : "off");
    } catch {}
    privacyListeners.forEach((l) => l());
  };
  return [on, toggle];
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider reauth={requestReauth}>
      <Shell>{children}</Shell>
      <ReauthDialog />
      <Toaster />
    </AuthProvider>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { state } = useAuth();

  useEffect(() => {
    if ("serviceWorker" in navigator && process.env.NODE_ENV === "production") {
      navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
  }, []);

  if (isPublic(pathname)) return <PublicShell>{children}</PublicShell>;
  if (state.status !== "signed-in") return <Waiting />;
  return <PrivateShell>{children}</PrivateShell>;
}

/** Sign-in, first-run setup and invitations: just the task, centered, no navigation. */
function PublicShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col items-center px-4 pt-[max(2.5rem,env(safe-area-inset-top))] pb-10 md:justify-center md:pt-10">
      <div className="mb-8 flex items-center gap-2.5 text-lg font-semibold tracking-tight">
        <Logo /> Finance
      </div>
      <main id="main" className="w-full max-w-md">
        {children}
      </main>
    </div>
  );
}

/** While checking who's signed in: the page's shape, never a flash of someone's numbers or an empty screen. */
function Waiting() {
  return (
    <div className="mx-auto grid w-full max-w-3xl gap-4 px-4 pt-20" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-8 w-40 rounded-lg" />
      <Skeleton className="h-40 rounded-xl" />
      <Skeleton className="h-28 rounded-xl" />
    </div>
  );
}

function PrivateShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const meta = useMeta();
  const [privacy, togglePrivacy] = usePrivacy();
  const active = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[15rem_1fr]">
      <a
        href="#main"
        className="sr-only z-50 rounded-full bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>

      {/* Laptop: a quiet sidebar. */}
      <aside className="sticky top-0 hidden h-dvh flex-col border-r px-4 py-6 md:flex">
        <Link href="/" className="mb-8 flex items-center gap-2.5 px-2 font-semibold tracking-tight">
          <Logo /> Finance
        </Link>
        <nav aria-label="Main" className="flex flex-col gap-1">
          {TABS.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              data-tab={label.toLowerCase()}
              aria-current={active(href) ? "page" : undefined}
              className={cn(
                "flex items-center gap-3 rounded-full px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
                active(href) && "bg-accent text-accent-foreground hover:bg-accent hover:text-accent-foreground",
              )}
            >
              <Icon className="size-[18px]" aria-hidden />
              {label}
            </Link>
          ))}
        </nav>
        <div className="mt-auto px-3 text-xs text-muted-foreground">{meta && `Version ${meta.version}`}</div>
      </aside>

      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 border-b bg-background/80 pt-[env(safe-area-inset-top)] backdrop-blur-lg md:border-b-0 md:bg-transparent md:backdrop-blur-none">
          <div className="mx-auto flex h-14 w-full max-w-3xl items-center gap-2 px-4">
            <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight md:hidden">
              <Logo /> Finance
            </Link>
            {meta?.demo && (
              <span className="ml-1 rounded-full bg-warning/10 px-2.5 py-0.5 text-xs font-medium text-warning">Sample data</span>
            )}
            <div className="ml-auto flex items-center gap-1">
              <Button
                variant="ghost"
                size="icon"
                onClick={togglePrivacy}
                aria-pressed={privacy}
                aria-label={privacy ? "Show amounts" : "Hide amounts"}
                title={privacy ? "Show amounts" : "Hide amounts"}
              >
                {privacy ? <EyeOff /> : <Eye />}
              </Button>
              <Button variant="ghost" size="icon" asChild>
                <Link href="/settings" aria-label="Settings" title="Settings" data-tab="settings">
                  <Settings />
                </Link>
              </Button>
            </div>
          </div>
        </header>

        <main id="main" className="mx-auto w-full max-w-3xl flex-1 px-4 pt-4 pb-28 md:pb-12">
          {children}
        </main>
      </div>

      {/* Phone: a thumb-reachable tab bar. */}
      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-40 border-t bg-background/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-lg md:hidden"
      >
        <ul className="mx-auto grid max-w-md grid-cols-5">
          {TABS.map(({ href, label, icon: Icon }) => (
            <li key={href}>
              <Link
                href={href}
                data-tab={label.toLowerCase()}
                aria-current={active(href) ? "page" : undefined}
                className={cn(
                  "flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium text-muted-foreground transition-colors",
                  active(href) && "text-primary",
                )}
              >
                <span className={cn("grid h-7 w-12 place-items-center rounded-full transition-colors", active(href) && "bg-accent")}>
                  <Icon className="size-5" aria-hidden />
                </span>
                {label}
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
      <rect width="32" height="32" rx="9" fill="var(--primary)" />
      <path
        d="M8 21.5 13.5 16l4 3.5L24 12"
        fill="none"
        stroke="var(--primary-foreground)"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
