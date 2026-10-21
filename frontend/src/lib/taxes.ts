/** The Taxes page's numbers, as GET /api/taxes sends them (backend/financeapp/taxes.py works them out). */

export type FilingStatus = "single" | "mfj" | "mfs" | "hoh";

export type TaxItem = {
  key: string;
  label: string;
  cents: number;
  derived_cents: number | null;
  typed: boolean;
  negative_ok: boolean;
  note: string;
};

export type Band = { rate: number; from_cents: number; cents: number; tax_cents: number };
export type Source = { title: string; url: string };

export type TaxProfile = {
  filing_status: FilingStatus | null;
  state: string;
  state_rate: number | null;
  deduction: "standard" | "itemized";
  state_deduction: "standard" | "itemized";
  children: number;
  other_dependents: number;
  qualified_share: number;
};

export type Hint = { kind: "more" | "less"; per_check_cents: number; deductions_cents?: number } | null;

export type TaxState = {
  demo?: boolean;
  year: number;
  table_year: number;
  table_checked: string | null;
  profile: TaxProfile;
  answered: boolean;
  statuses: Record<FilingStatus, string>;
  states: Record<string, string>;
  states_with_tables: string[];
  no_wage_tax: string[];
  people: {
    name: string;
    latest: string;
    frequency: string | null;
    paychecks_left: number;
    stubs_this_year: number;
    ytd_source: "stub" | "estimated" | "none";
    split_estimated: boolean;
  }[];
  items: TaxItem[];
  federal: {
    income_cents: number;
    wages_taxed_cents: number;
    gains_ordinary_cents: number;
    gains_preferred_cents: number;
    capital_loss_cents: number;
    agi_cents: number;
    standard_cents: number;
    itemizing: boolean;
    deduction_cents: number;
    taxable_cents: number;
    ordinary_cents: number;
    preferred_cents: number;
    ordinary_rows: Band[];
    gains_rows: Band[];
    ordinary_tax_cents: number;
    gains_tax_cents: number;
    child_credit_cents: number;
    credits_cents: number;
    income_tax_cents: number;
    niit_cents: number;
    niit_base_cents: number;
    tax_cents: number;
    paid_cents: number;
    balance_cents: number;
    marginal_rate: number;
    effective_rate: number;
  };
  fica: {
    social_security_cents: number;
    medicare_cents: number;
    additional_medicare_cents: number;
    due_cents: number;
    withheld_cents: number;
    difference_cents: number;
    paid_cents: number;
    balance_cents: number;
  };
  state: {
    code: string;
    name: string;
    supported: boolean;
    rate: number;
    no_wage_tax?: boolean;
    rate_typed?: boolean;
    standard_cents?: number;
    deduction_cents?: number;
    itemizing?: boolean;
    child_deduction_cents?: number;
    taxable_cents?: number;
    sources?: Source[];
    notes?: string[];
    tax_cents: number;
    paid_cents: number;
    balance_cents: number;
  };
  total: { tax_cents: number; paid_cents: number; balance_cents: number };
  hints: {
    paychecks_left: number;
    federal: Hint;
    state: Hint;
    quarterly: {
      federal_needed: boolean;
      federal_shortfall_cents: number;
      required_cents: number;
      uses_last_year: boolean;
      state_needed: boolean;
      state_cents: number;
      due_dates: string[];
      per_date_cents: number | null;
    } | null;
  };
  sources: Source[];
  assumptions: string[];
};

// Which card each money number sits on.
export const INCOME = [
  "wages",
  "stock_to_come",
  "spouse_wages",
  "retirement",
  "hsa_benefits",
  "spouse_pretax",
  "interest",
  "dividends",
  "qualified_dividends",
  "st_gains",
  "lt_gains",
  "other_income",
  "adjustments",
];
export const DEDUCTIONS = ["itemized", "state_itemized", "other_credits"];
export const PAID = [
  "federal_withheld",
  "spouse_federal_withheld",
  "federal_estimated",
  "state_withheld",
  "spouse_state_withheld",
  "state_estimated",
  "social_security_withheld",
  "medicare_withheld",
  "last_year_tax",
];

export function percent(rate: number, digits = 0) {
  return `${(rate * 100).toFixed(digits).replace(/\.0+$/, "")}%`;
}
