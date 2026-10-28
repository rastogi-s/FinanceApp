"use client";

/**
 * Card offers (backend offers.py): Chase Offers and Capital One Offers, sent from the bank's own offers page by the
 * "Send to Finance" bookmark, or pasted.
 *
 * The bookmark runs only when tapped, on the page you have open. It keeps the lines around each offer ("5% cash
 * back", "$20 back", "5X miles") and the card's name, with each picture's label read as a line of its own (Capital One
 * shows the store only as a logo; its picture's address names the store's website, sent as "Logo for bestbuy.com"), leaving out anything about balances, payments or account
 * numbers, and opens this app at /cards#offers=… with them. A #fragment
 * never goes to any server, and the Credit Cards page takes it out of the address as soon as it reads it.
 */

export type OfferRead = {
  merchant: string;
  kind: "percent" | "amount" | "multiplier";
  value: number;
  up_to: boolean;
  label: string;
  min_spend_cents: number | null;
  max_cents: number | null;
  channel: "online" | "in store" | null;
  expires: string | null;
  soon: boolean;
  added: boolean | null;
};

export type ReadOffers = {
  issuer: "chase" | "capital_one" | "other";
  issuer_name: string;
  card_name: string | null;
  mask: string | null;
  account_id: number | null;
  accounts: { account_id: number; label: string }[];
  offers: OfferRead[];
};

export type OfferItem = {
  id: number | string;
  account_id: number;
  card: string;
  issuer: string;
  name: string;
  merchant: string;
  label: string;
  kind: OfferRead["kind"];
  value: number;
  channel: OfferRead["channel"];
  min_spend_cents: number | null;
  max_cents: number | null;
  expires: string | null;
  ends: string | null;
  soon: boolean;
  added: boolean | null;
  how: string | null;
  visits: number;
  spent_cents: number;
};

export type Offers = {
  items: OfferItem[];
  cards: { account_id: number; card: string; issuer: string; count: number; seen: string | null }[];
};

/** How an offer shows on a card in the store lookup. */
export type RankedOffer = {
  id: number | string;
  name: string;
  label: string;
  ends: string | null;
  added: boolean | null;
  how: string | null;
  value_cents: number;
};

export type Sent = { text: string; site: string };

/** Offers near me: in-store offers at stores around you. */
export type Near = {
  offers: {
    id: number | string;
    merchant: string;
    store: string;
    meters: number;
    label: string;
    name: string;
    card: string;
    ends: string | null;
    added: boolean | null;
    how: string | null;
    check_terms: boolean;
  }[];
  stores: number;
  radius_m: number;
};

const HASH = "#offers=";
const STASH = "finance.sent-offers";

/** What the bookmark runs. `origin` is this app's address, so the offers come back here and nowhere else. */
export function bookmarklet(origin: string): string {
  const js = `(()=>{
const v=/(\\d+(\\.\\d+)?\\s?%|\\$\\d+(\\.\\d\\d)?|\\d+(\\.\\d)?\\s?x)\\s*(in\\s+)?(cash\\s?back|back|off|rewards?|statement credit|(the\\s+)?miles|(the\\s+)?points)/i;
const c=/(\\.{2,3}|\\u2026|ending in|x{2,4})\\s?\\d{4}/i;
const a=[];
try{document.querySelectorAll('img[alt],img[src*="domain="],[role=img][aria-label]').forEach(m=>{let d='';try{d=new URL(m.src||'').searchParams.get('domain')||'';}catch(e){}const t=d?'Logo for '+d:(m.getAttribute('alt')||m.getAttribute('aria-label')||'').trim();if(t.length<2||t.length>60||!m.parentNode)return;const e=document.createElement('span');e.textContent=t;e.style.cssText='position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap';m.parentNode.insertBefore(e,m);a.push(e);});}catch(e){}
const l=document.body.innerText.split(/\\n+/).map(s=>s.trim()).filter(Boolean);
a.forEach(e=>e.remove());
const k=new Set();let card='';
const no=/balance|available|payment|statement|routing|account (number|ending)/i;
l.forEach((s,i)=>{if(!card&&c.test(s)&&s.length<80&&!no.test(s))card=s;if(v.test(s))for(let j=Math.max(0,i-5);j<Math.min(l.length,i+5);j++)if(!no.test(l[j]))k.add(j);});
if(!k.size){alert('No offers on this page. Open Chase Offers or Capital One Offers first.');return;}
const t=[card,...[...k].sort((a,b)=>a-b).map(i=>l[i])].join('\\n').slice(0,150000);
const u=${JSON.stringify(origin)}+'/cards${HASH}'+encodeURIComponent(JSON.stringify({site:location.hostname,text:t}));
if(!window.open(u,'_blank'))location.href=u;
})()`;
  return "javascript:" + js.replace(/\n/g, "");
}

/** Offers sent by the bookmark: from the address, or kept from before signing in. Takes them out of both. */
export function takeSent(): Sent | null {
  if (typeof window === "undefined") return null;
  let raw: string | null = null;
  if (window.location.hash.startsWith(HASH)) {
    raw = window.location.hash.slice(HASH.length);
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  } else {
    try {
      raw = sessionStorage.getItem(STASH);
      sessionStorage.removeItem(STASH);
    } catch {
      raw = null;
    }
  }
  if (!raw) return null;
  try {
    const sent = JSON.parse(decodeURIComponent(raw)) as Partial<Sent>;
    if (typeof sent.text !== "string" || !sent.text.trim()) return null;
    return { text: sent.text.slice(0, 200_000), site: typeof sent.site === "string" ? sent.site.slice(0, 120) : "" };
  } catch {
    return null;
  }
}

/** Before sending someone to sign in: keep offers that came in the address, for after. */
export function keepSent(): boolean {
  if (typeof window === "undefined" || !window.location.hash.startsWith(HASH)) return false;
  try {
    sessionStorage.setItem(STASH, window.location.hash.slice(HASH.length));
  } catch {
    return false;
  }
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return true;
}

/** Whether offers are waiting from before signing in. */
export function hasKept(): boolean {
  try {
    return typeof window !== "undefined" && !!sessionStorage.getItem(STASH);
  } catch {
    return false;
  }
}

export const OFFER_PAGES = [
  { issuer: "chase", name: "Chase Offers", url: "https://secure.chase.com/", hint: "Sign in, then pick Chase Offers under a card." },
  { issuer: "capital_one", name: "Capital One Offers", url: "https://capitaloneoffers.com/", hint: "Sign in with your Capital One login." },
] as const;
