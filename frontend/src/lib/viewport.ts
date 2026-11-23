"use client";

import { useEffect, type RefObject } from "react";

/**
 * iOS home-screen apps (iOS 26 especially) can lose track of the screen's real bottom: after the keyboard, a Face ID
 * or passkey sheet, or coming back from the background, the page keeps a shrunk or shifted viewport and anything
 * pinned to the bottom floats halfway up with the page showing underneath it. The browser only fixes it on a
 * force-quit. This keeps the tab bar on the real bottom edge:
 *
 * 1. "Heal": when the keyboard goes away or the app comes back, force the page to measure itself again (hide and show
 *    the root in one frame, then put the scroll back), which is what makes iOS let go of the stale viewport.
 * 2. "Fit": if the bar still ends above the bottom of what you can see, slide it down by the difference.
 *
 * Both only run in the installed app, never while you are typing, and never while zoomed, so a regular browser and
 * a laptop behave exactly as before.
 */
export function usePinnedToBottom(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv || !standalone()) return;

    const bar = ref.current;
    let frame = 0;
    const timers: number[] = [];

    const fit = () => {
      frame = 0;
      const el = ref.current;
      if (!el) return;
      el.style.transform = "";
      if (typing() || Math.abs(vv.scale - 1) > 0.01 || getComputedStyle(el).display === "none") return;
      const gap = vv.offsetTop + vv.height - el.getBoundingClientRect().bottom;
      if (gap > 2) el.style.transform = `translateY(${Math.round(gap)}px)`;
    };
    const fitSoon = () => {
      if (!frame) frame = requestAnimationFrame(fit);
    };

    const heal = () => {
      if (typing()) return;
      const root = document.documentElement;
      const { scrollX, scrollY } = window;
      root.style.display = "none";
      void root.offsetHeight; // a synchronous re-layout, nothing is painted in between
      root.style.display = "";
      if (window.scrollX !== scrollX || window.scrollY !== scrollY) window.scrollTo(scrollX, scrollY);
      fit();
    };
    // iOS settles the viewport in steps after the keyboard or a sheet closes, so look again a little later too.
    const healSoon = () => {
      for (const t of timers.splice(0)) clearTimeout(t);
      for (const ms of [150, 500]) timers.push(window.setTimeout(heal, ms));
    };
    const onFocusOut = () => healSoon();
    const onVisible = () => {
      if (document.visibilityState === "visible") healSoon();
    };

    vv.addEventListener("resize", fitSoon);
    vv.addEventListener("scroll", fitSoon);
    window.addEventListener("scroll", fitSoon, { passive: true });
    window.addEventListener("resize", fitSoon);
    window.addEventListener("orientationchange", healSoon);
    window.addEventListener("pageshow", healSoon);
    document.addEventListener("focusout", onFocusOut);
    document.addEventListener("visibilitychange", onVisible);
    fitSoon();
    return () => {
      if (frame) cancelAnimationFrame(frame);
      for (const t of timers) clearTimeout(t);
      vv.removeEventListener("resize", fitSoon);
      vv.removeEventListener("scroll", fitSoon);
      window.removeEventListener("scroll", fitSoon);
      window.removeEventListener("resize", fitSoon);
      window.removeEventListener("orientationchange", healSoon);
      window.removeEventListener("pageshow", healSoon);
      document.removeEventListener("focusout", onFocusOut);
      document.removeEventListener("visibilitychange", onVisible);
      if (bar) bar.style.transform = "";
    };
  }, [ref]);
}

function standalone(): boolean {
  return (
    (navigator as Navigator & { standalone?: boolean }).standalone === true ||
    window.matchMedia("(display-mode: standalone)").matches
  );
}

function typing(): boolean {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  if (el.isContentEditable) return true;
  if (el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
  if (el.tagName !== "INPUT") return false;
  return !["button", "checkbox", "radio", "range", "submit", "reset", "file", "color"].includes((el as HTMLInputElement).type);
}
