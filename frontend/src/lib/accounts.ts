/** Shapes and helpers for Settings → Accounts. */

export type Account = {
  id: number;
  name: string;
  display_name: string;
  type: string;
  mask: string | null;
  hidden: boolean;
  is_authoritative: boolean;
  owner_user_id: number | null;
  balance_cents: number | null;
  balance_date: string | null;
  statement_cents: number | null;
  statement_date: string | null;
  statement_diff_percent: number | null;
};
export type Connection = {
  id: number;
  provider: "plaid" | "snaptrade" | "file" | "manual";
  institution: string;
  status: "active" | "stale" | "revoked";
  last_synced_at: string | null;
  last_error: string | null;
  accounts: Account[];
};
export type ConnectionsData = {
  connections: Connection[];
  revoked: Connection[];
  providers: { plaid: boolean; snaptrade: boolean };
  people: { id: number; name: string }[];
  sync: { last_at: string | null; last_ok: boolean | null; clean_days: number; running: boolean };
};

export function parseDollars(text: string): number | null {
  const v = text.replace(/[$,\s]/g, "");
  if (!/^-?\d+(\.\d{0,2})?$/.test(v)) return null;
  return Math.round(parseFloat(v) * 100);
}
