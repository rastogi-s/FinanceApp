"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";
import { ChartPie, Eye, EyeOff, House, PiggyBank, ReceiptText, RefreshCw, Settings, TrendingUp } from "lucide-react";
import { AlertsBell } from "@/components/alerts-bell";
import { PullToRefresh } from "@/components/pull-to-refresh";
import { ReauthDialog, requestReauth } from "@/components/reauth";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/toast";
import { UpdateBanner } from "@/components/updates";
import { AuthProvider, isPublic, useAuth } from "@/lib/auth";
import { refreshAll, useMeta } from "@/lib/household";
import { followHash, installNavMemory, noteNavigation } from "@/lib/nav";
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

  useEffect(installNavMemory, []);
  useEffect(noteNavigation, [pathname]);
  useEffect(followHash, [pathname, state.status]);

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

// Wide pages: 72rem from 1280px screens, 88rem from 1536px, and the whole width from 1800px (where the holdings
// table fits without scrolling sideways), with the gutters growing a little too.
const WIDE_MAIN =
  "xl:has-[[data-wide]]:max-w-6xl xl:has-[[data-wide]]:px-6 2xl:has-[[data-wide]]:max-w-[88rem] 2xl:has-[[data-wide]]:px-8 3xl:has-[[data-wide]]:max-w-none";
const WIDE_HEADER =
  "xl:group-has-[[data-wide]]/shell:max-w-6xl xl:group-has-[[data-wide]]/shell:px-6 2xl:group-has-[[data-wide]]/shell:max-w-[88rem] 2xl:group-has-[[data-wide]]/shell:px-8 3xl:group-has-[[data-wide]]/shell:max-w-none";

// Top bar buttons: 44px to tap on a phone (Apple's minimum) with 20px icons, 40px for a mouse.
const TOP_ICON = "size-11 [&_svg]:size-5 md:size-10";

function PrivateShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const meta = useMeta();
  const [privacy, togglePrivacy] = usePrivacy();
  // Goals and Taxes are reached from Plan, so Plan stays lit there; Credit cards from Spending.
  const active = (href: string) =>
    href === "/"
      ? pathname === "/"
      : pathname.startsWith(href) ||
        (href === "/plan" && /^\/(goals|taxes|earnings)/.test(pathname)) ||
        (href === "/spending" && pathname.startsWith("/cards"));

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[15rem_1fr]">
      <a
        href="#main"
        className="sr-only z-50 rounded-full bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>
      <PullToRefresh />

      {/* Laptop: a quiet sidebar. */}
      <aside className="sticky top-0 hidden h-dvh flex-col border-r px-4 py-6 md:flex">
        <Link href="/" title="Home" className="mb-8 flex items-center gap-2.5 rounded-full px-2 font-semibold tracking-tight transition-opacity hover:opacity-80">
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

      {/* A page that lays out in columns (it marks itself data-wide) gets the laptop's width; forms stay narrow. */}
      <div className="group/shell flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 border-b bg-background/80 pt-[env(safe-area-inset-top)] backdrop-blur-lg md:border-b-0 md:bg-transparent md:backdrop-blur-none">
          <div className={cn("mx-auto flex h-14 w-full max-w-3xl items-center gap-2 px-4", WIDE_HEADER)}>
            <Link href="/" title="Home" className="-ml-1 flex min-h-11 items-center gap-2 rounded-full px-1 font-semibold tracking-tight transition-opacity active:opacity-70 md:hidden">
              <Logo /> Finance
            </Link>
            {meta?.demo && (
              <span className="ml-1 rounded-full bg-warning/10 px-2.5 py-0.5 text-xs font-medium text-warning">Sample data</span>
            )}
            <div className="-mr-1.5 ml-auto flex items-center md:mr-0 md:gap-1">
              <RefreshButton />
              <AlertsBell demo={!!meta?.demo} className={TOP_ICON} />
              <Button
                variant="ghost"
                size="icon"
                className={TOP_ICON}
                onClick={togglePrivacy}
                aria-pressed={privacy}
                aria-label={privacy ? "Show amounts" : "Hide amounts"}
                title={privacy ? "Show amounts" : "Hide amounts"}
              >
                {privacy ? <EyeOff /> : <Eye />}
              </Button>
              <Button variant="ghost" size="icon" className={TOP_ICON} asChild>
                <Link href="/settings" aria-label="Settings" title="Settings" data-tab="settings">
                  <Settings />
                </Link>
              </Button>
            </div>
          </div>
        </header>

        <main id="main" className={cn("mx-auto w-full max-w-3xl flex-1 px-4 pt-4 pb-28 md:pb-12", WIDE_MAIN)}>
          <UpdateBanner />
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

/** A laptop has no pull to refresh: this does the same (sync with the banks, then fresh numbers everywhere). */
function RefreshButton() {
  const [busy, setBusy] = useState(false);
  const refresh = () => {
    if (busy) return;
    setBusy(true);
    refreshAll().finally(() => setBusy(false));
  };
  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn(TOP_ICON, "hidden md:inline-flex")}
      onClick={refresh}
      aria-label={busy ? "Refreshing" : "Refresh"}
      aria-busy={busy}
      title="Refresh: sync with your banks"
      data-tab="refresh"
    >
      <RefreshCw className={cn(busy && "animate-spin motion-reduce:animate-none")} />
    </Button>
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
