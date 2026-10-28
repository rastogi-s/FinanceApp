"use client";

import { useMemo, useState } from "react";
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import type { Company, Exposure } from "@/lib/portfolio";
import { percent } from "@/lib/portfolio";
import { cn } from "@/lib/utils";

const COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)"];
const DIRECT = "var(--chart-5)";
// The drawing's own units: a phone gets a narrower, taller frame so labels stay a readable size.
const WIDE = { w: 640, h: 500 };
const NARROW = { w: 380, h: 460 };

type Node = SimulationNodeDatum & {
  id: string;
  kind: "fund" | "company";
  label: string;
  name: string;
  r: number;
  percent: number;
  color: string;
  company?: Company;
};
type Link = SimulationLinkDatum<Node> & { source: string | Node; target: string | Node; percent: number; color: string };

function layout(exposure: Exposure, count: number, W: number, H: number) {
  const companies = exposure.companies.slice(0, count);
  const hubs = new Map<string, number>(); // fund symbol (or "direct") -> percent through it, among the shown companies
  for (const c of companies) for (const s of c.sources) hubs.set(s.symbol, (hubs.get(s.symbol) ?? 0) + s.percent);
  const order = [...hubs.keys()].sort((a, b) => (hubs.get(b) ?? 0) - (hubs.get(a) ?? 0));
  const funds = order.filter((h) => h !== "direct");
  const color = new Map<string, string>([["direct", DIRECT], ...funds.map((h, i) => [h, COLORS[i % COLORS.length]!] as [string, string])]);
  const fundName = new Map(exposure.funds.map((f) => [f.symbol, f.name]));
  const maxCompany = Math.max(...companies.map((c) => c.percent), 0.0001);
  const maxHub = Math.max(...hubs.values(), 0.0001);

  const nodes: Node[] = order.map((h, i) => {
    const angle = (i / Math.max(order.length, 1)) * Math.PI * 2;
    return {
      id: `fund:${h}`,
      kind: "fund",
      label: h === "direct" ? "Direct" : h,
      name: h === "direct" ? "Stocks you hold directly" : (fundName.get(h) ?? h),
      r: 18 + 16 * Math.sqrt((hubs.get(h) ?? 0) / maxHub),
      percent: hubs.get(h) ?? 0,
      color: color.get(h)!,
      x: W / 2 + Math.cos(angle) * 120,
      y: H / 2 + Math.sin(angle) * 120,
    };
  });
  companies.forEach((c, i) => {
    const main = c.sources[0]?.symbol ?? "direct";
    // A sunflower spiral to start from, so the layout is the same every time.
    const a = i * 2.399963;
    const d = 30 * Math.sqrt(i + 1);
    nodes.push({
      id: `co:${c.key}`,
      kind: "company",
      label: c.symbol ?? c.name.split(" ")[0]!,
      name: c.name,
      r: 4 + 26 * Math.sqrt(c.percent / maxCompany),
      percent: c.percent,
      color: color.get(main)!,
      company: c,
      x: W / 2 + Math.cos(a) * d,
      y: H / 2 + Math.sin(a) * d,
    });
  });
  const links: Link[] = companies.flatMap((c) =>
    c.sources.map((s) => ({ source: `fund:${s.symbol}`, target: `co:${c.key}`, percent: s.percent, color: color.get(s.symbol)! })),
  );
  const maxLink = Math.max(...links.map((l) => l.percent), 0.0001);

  const sim = forceSimulation<Node>(nodes)
    .force(
      "link",
      forceLink<Node, Link>(links)
        .id((n) => n.id)
        .distance((l) => 40 + (l.source as Node).r + (l.target as Node).r)
        .strength((l) => 0.08 + 0.5 * (l.percent / maxLink)),
    )
    .force("charge", forceManyBody<Node>().strength((n) => (n.kind === "fund" ? -260 : -30)))
    .force("collide", forceCollide<Node>().radius((n) => n.r + 2.5).strength(0.9))
    .force("x", forceX<Node>(W / 2).strength(0.06))
    .force("y", forceY<Node>(H / 2).strength(0.08))
    .stop();
  for (let i = 0; i < 320; i++) sim.tick();
  // Zoom to fit what the simulation drew, keeping the frame's shape, with room for labels above small bubbles.
  const pad = 18;
  const x0 = Math.min(...nodes.map((n) => (n.x ?? 0) - n.r)) - pad;
  const x1 = Math.max(...nodes.map((n) => (n.x ?? 0) + n.r)) + pad;
  const y0 = Math.min(...nodes.map((n) => (n.y ?? 0) - n.r)) - pad - 10;
  const y1 = Math.max(...nodes.map((n) => (n.y ?? 0) + n.r)) + pad;
  const scale = Math.min(W / (x1 - x0), H / (y1 - y0), 2.2);
  const ox = (W - (x1 - x0) * scale) / 2;
  const oy = (H - (y1 - y0) * scale) / 2;
  for (const n of nodes) {
    n.x = ox + ((n.x ?? 0) - x0) * scale;
    n.y = oy + ((n.y ?? 0) - y0) * scale;
    n.r *= scale;
  }
  return { nodes, links: links as (Link & { source: Node; target: Node })[], maxLink: maxLink / scale };
}

/**
 * Funds as hubs and companies as bubbles sized by your real share of them, linked by lines as thick as the money
 * that reaches you through each fund. Tap a company to light up every fund it comes through; tap a fund to light
 * up what's in it. The layout is computed once (no animation), so it's steady and kind to reduced motion.
 */
export function ExposureGraph({ exposure, onSelect }: { exposure: Exposure; onSelect?: (key: string | null) => void }) {
  const total = exposure.companies.length;
  // Fewer bubbles on a phone, so it stays readable. Only ever rendered in the browser, after data arrives.
  const [narrow] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 640px)").matches);
  const { w: W, h: H } = narrow ? NARROW : WIDE;
  const [count, setCount] = useState(() => Math.min(narrow ? 30 : 50, total));
  const [selected, setSelected] = useState<string | null>(null);
  const { nodes, links, maxLink } = useMemo(() => layout(exposure, count, W, H), [exposure, count, W, H]);

  const lit = useMemo(() => {
    if (!selected) return null;
    const set = new Set<string>([selected]);
    for (const l of links) {
      if (l.source.id === selected) set.add(l.target.id);
      if (l.target.id === selected) set.add(l.source.id);
    }
    return set;
  }, [selected, links]);

  const pick = (id: string | null) => {
    const next = id === selected ? null : id;
    setSelected(next);
    onSelect?.(next);
  };
  const sel = nodes.find((n) => n.id === selected) ?? null;

  if (total === 0) return null;
  return (
    <div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto max-h-[36rem] w-full touch-manipulation select-none"
        role="group"
        aria-label={`Your top ${count} companies and the funds they reach you through. Each is a button; the list below has the same numbers.`}
        onClick={() => pick(null)}
      >
        <g>
          {links.map((l) => {
            const on = lit ? lit.has(l.source.id) && lit.has(l.target.id) && (l.source.id === selected || l.target.id === selected) : false;
            return (
              <line
                key={`${l.source.id}-${l.target.id}`}
                x1={l.source.x}
                y1={l.source.y}
                x2={l.target.x}
                y2={l.target.y}
                stroke={l.color}
                strokeWidth={0.75 + 7 * (l.percent / maxLink)}
                strokeLinecap="round"
                className="transition-opacity duration-200 motion-reduce:transition-none"
                opacity={lit ? (on ? 0.75 : 0.05) : 0.22}
              />
            );
          })}
        </g>
        {nodes.map((n) => {
          const dim = lit && !lit.has(n.id);
          const isSel = n.id === selected;
          const showLabel = n.kind === "fund" || n.r >= 15 || isSel || (lit?.has(n.id) ?? false);
          return (
            <g
              key={n.id}
              transform={`translate(${n.x},${n.y})`}
              role="button"
              tabIndex={0}
              aria-pressed={isSel}
              aria-label={`${n.label}${n.kind === "company" ? `, ${n.name}` : ""}: ${percent(n.percent, 2)} of your portfolio`}
              className="cursor-pointer outline-none transition-opacity duration-200 motion-reduce:transition-none [&:focus-visible>circle:first-child]:stroke-ring"
              opacity={dim ? 0.18 : 1}
              onClick={(e) => {
                e.stopPropagation();
                pick(n.id);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  pick(n.id);
                } else if (e.key === "Escape") pick(null);
              }}
            >
              {n.kind === "fund" ? (
                <>
                  <circle r={n.r} fill="var(--card)" stroke={n.color} strokeWidth={isSel ? 4 : 2.5} />
                  <text textAnchor="middle" dy="0.35em" className="fill-foreground text-[12px] font-semibold">
                    {n.label.length > 8 ? `${n.label.slice(0, 7)}…` : n.label}
                  </text>
                </>
              ) : (
                <>
                  <circle r={n.r} fill={n.color} fillOpacity={isSel ? 0.95 : 0.78} stroke={isSel ? "var(--foreground)" : "var(--card)"} strokeWidth={isSel ? 2.5 : 1.25} />
                  {showLabel && (
                    <text
                      textAnchor="middle"
                      dy={n.r >= 15 ? "0.35em" : -(n.r + 5)}
                      className={cn("text-[11px] font-semibold", n.r >= 15 ? "fill-white" : "fill-foreground")}
                      style={n.r >= 15 ? { paintOrder: "stroke", stroke: "rgb(0 0 0 / 0.25)", strokeWidth: 2 } : undefined}
                    >
                      {n.label.length > 7 ? `${n.label.slice(0, 6)}…` : n.label}
                    </text>
                  )}
                </>
              )}
            </g>
          );
        })}
      </svg>

      <div className="mt-2 min-h-16 rounded-xl bg-muted/50 px-4 py-3 text-sm wrap-anywhere" aria-live="polite">
        {!sel && <p className="text-muted-foreground">Tap a bubble to see which funds it reaches you through, or a fund to see what&apos;s in it.</p>}
        {sel?.kind === "company" && sel.company && (
          <p>
            <span className="font-semibold">
              {sel.label} {percent(sel.percent, 2)}
            </span>
            <span className="text-muted-foreground">: </span>
            {sel.company.sources.map((s, i) => (
              <span key={s.symbol}>
                {i > 0 && ", "}
                {s.symbol === "direct" ? "direct" : s.symbol} {percent(s.percent, 2)}
              </span>
            ))}
          </p>
        )}
        {sel?.kind === "fund" && (
          <p>
            <span className="font-semibold">{sel.name}</span>
            <span className="text-muted-foreground">
              {" "}
              brings you {percent(sel.percent, 1)} across the {links.filter((l) => l.source.id === sel.id).length} companies shown, largest{" "}
              {links
                .filter((l) => l.source.id === sel.id)
                .sort((a, b) => b.percent - a.percent)
                .slice(0, 3)
                .map((l) => l.target.label)
                .join(", ")}
              .
            </span>
          </p>
        )}
      </div>

      {total > 10 && (
        <label className="mt-4 flex items-center gap-3 text-sm">
          <span className="shrink-0 text-muted-foreground">Show</span>
          <input
            type="range"
            min={10}
            max={Math.min(100, total)}
            step={5}
            value={count}
            onChange={(e) => {
              setCount(Number(e.target.value));
              setSelected(null);
            }}
            className="h-2 flex-1 cursor-pointer accent-[var(--primary)]"
            aria-label="How many companies to show"
          />
          <span className="w-24 shrink-0 text-right tabular-nums">top {count}</span>
        </label>
      )}
    </div>
  );
}
