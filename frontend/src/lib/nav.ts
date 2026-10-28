"use client";

import { useEffect, useState } from "react";

/*
 * Going back should put you where you were. Pages load their numbers after they mount, so the browser's own
 * scroll restoration lands on a short loading page and gives up at the top. This module remembers each page's
 * scroll position (and any list state a page asks it to keep) per address, and puts it back when you return
 * with the back button, a swipe, or one of the app's own back links.
 */

const memory = new Map<string, string>();

function read(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return memory.get(key) ?? null;
  }
}

function write(key: string, value: string) {
  memory.set(key, value);
  try {
    sessionStorage.setItem(key, value);
  } catch {
    // storage blocked: remembered until the page closes
  }
}

const here = () => window.location.pathname + window.location.search;

let returnedTo: string | null = null; // the address the last back/forward landed on, until you go somewhere else
let current: string | null = null; // pathnames
let previous: string | null = null;
let restoring = 0; // the restore loop's id; 0 when idle
let saveTimer: ReturnType<typeof setTimeout> | undefined;

function saveScroll(key = here()) {
  if (!restoring) write(`scroll:${key}`, String(Math.round(window.scrollY)));
}

/** Scroll back to `y` once the page is tall enough, following it while late content loads in above. */
function restore(y: number) {
  const id = (restoring = performance.now());
  const start = id;
  let height = 0;
  let steady = 0;
  const stop = () => {
    if (restoring === id) restoring = 0;
    removeEventListener("wheel", stop);
    removeEventListener("touchstart", stop);
    removeEventListener("keydown", stop);
  };
  addEventListener("wheel", stop, { passive: true });
  addEventListener("touchstart", stop, { passive: true });
  addEventListener("keydown", stop);
  const step = (now: number) => {
    if (restoring !== id) return;
    const doc = document.documentElement;
    const reachable = doc.scrollHeight - window.innerHeight >= y - 1;
    if (reachable && Math.abs(window.scrollY - y) > 1) window.scrollTo(0, y);
    steady = reachable && doc.scrollHeight === height ? steady + 1 : 0;
    height = doc.scrollHeight;
    // Done once the page has stopped growing for about half a second, or after three seconds whatever happens.
    if (steady > 30 || now - start > 3000) {
      if (!reachable) window.scrollTo(0, y);
      return stop();
    }
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

let installed = false;

/** Called once by the app shell. */
export function installNavMemory() {
  if (installed) return;
  installed = true;
  current = window.location.pathname;
  addEventListener(
    "scroll",
    () => {
      // Waits for the scroll to settle, so the jump to the top of a newly opened page is filed under that page.
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => saveScroll(), 150);
    },
    { passive: true },
  );
  // Tapping a link: file the position under the page being left, before anything moves.
  addEventListener(
    "click",
    (e) => {
      if ((e.target as Element | null)?.closest?.("a[href]")) {
        clearTimeout(saveTimer);
        saveScroll();
      }
    },
    true,
  );
  addEventListener("popstate", () => {
    clearTimeout(saveTimer);
    const key = here();
    returnedTo = key;
    const saved = Number(read(`scroll:${key}`));
    if (saved > 0) restore(saved);
  });
}

/** Called by the app shell whenever the page changes. */
export function noteNavigation() {
  const path = window.location.pathname;
  if (path === current) return;
  previous = current;
  current = path;
  if (returnedTo !== here()) returnedTo = null;
}

/**
 * Opened at an address with a section in it (/budget#pay, from a notification or a link on another page): scroll
 * to that section once it has loaded. The browser only tries before the page's numbers are in, and gives up.
 */
export function followHash() {
  const id = decodeURIComponent(window.location.hash.slice(1));
  if (!/^[\w-]{1,40}$/.test(id) || isReturning()) return;
  const start = performance.now();
  const step = (now: number) => {
    const el = document.getElementById(id);
    if (el) return el.scrollIntoView({ block: "start" });
    if (now - start < 3000) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** Whether this page was reached by going back (or forward), so it should look the way it was left. */
export function isReturning(): boolean {
  return typeof window !== "undefined" && returnedTo === here();
}

/** The page you came from, when it is `href` (so a back link can go back instead of starting afresh). */
export function cameFrom(href: string): boolean {
  return previous === href;
}

/**
 * State for a list (a search, a sort, which row is open) that comes back as it was when you return to the page,
 * and starts fresh when you arrive any other way. With `always`, it also comes back when you arrive another way
 * (tapping the tab), until the browser tab or home-screen app is closed. `valid` drops a saved value that no
 * longer fits, such as a sort that was renamed or an account that's gone.
 */
export function useRemembered<T>(
  name: string,
  initial: T | (() => T),
  { always = false, valid }: { always?: boolean; valid?: (v: T) => boolean } = {},
): [T, (v: T | ((prev: T) => T)) => void] {
  const [key] = useState(() => (typeof window === "undefined" ? name : `view:${window.location.pathname}:${name}`));
  const [value, setValue] = useState<T>(() => {
    if (always || isReturning()) {
      const saved = read(key);
      if (saved !== null) {
        try {
          const v = JSON.parse(saved) as T;
          if (!valid || valid(v)) return v;
        } catch {}
      }
    }
    return typeof initial === "function" ? (initial as () => T)() : initial;
  });
  useEffect(() => {
    write(key, JSON.stringify(value));
  }, [key, value]);
  return [value, setValue];
}
