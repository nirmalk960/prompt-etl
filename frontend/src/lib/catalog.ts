// Operation catalog served by GET /operations. Drives the palette, node
// visuals and the settings form, so new backend ops need no frontend change.

import { create } from "zustand";
import { getOperations } from "@/api/client";

export type ParamType =
  | "column"
  | "columns"
  | "number"
  | "integer"
  | "text"
  | "select"
  | "multiselect"
  | "boolean"
  | "expression"
  | "list";

export interface OpParam {
  name: string;
  label: string;
  type: ParamType;
  required: boolean;
  default?: unknown;
  options?: { value: string; label: string }[];
  help?: string;
  input?: number;
  fields?: OpParam[];
  show_if?: { param: string; values: unknown[] };
  placeholder?: string;
}

export interface OpSpec {
  id: string;
  label: string;
  category: string;
  description: string;
  engine: "sql" | "python" | "prompt";
  inputs: number;
  params: OpParam[];
  supports_prompt: boolean;
  supports_configure: boolean;
  preset_prompt: string;
  persist_output: boolean;
}

export interface Catalog {
  categories: { id: string; label: string }[];
  operations: OpSpec[];
}

export const CATEGORY_COLORS: Record<string, string> = {
  columns: "#84cc16",
  rows: "#8b5cf6",
  text: "#f59e0b",
  dates: "#06b6d4",
  combine: "#3b82f6",
  reshape: "#6366f1",
  quality: "#14b8a6",
  predict: "#ec4899",
  visualize: "#f97316",
  custom: "#64748b",
};

const OP_ICONS: Record<string, string> = {
  select: "📋",
  drop_columns: "✂️",
  rename: "🏷️",
  cast: "🔁",
  formula: "🧮",
  conditional_column: "🔀",
  concat_columns: "🔗",
  filter: "🔍",
  drop_nulls: "🧹",
  fill_nulls: "✏️",
  deduplicate: "🔄",
  sort: "↕️",
  top_n: "🏆",
  sample: "🎲",
  clean_text: "🧽",
  replace_text: "🔤",
  split_column: "🪓",
  regex_extract: "🧲",
  parse_date: "📅",
  date_parts: "🗓️",
  date_diff: "⏱️",
  join: "🤝",
  union: "➕",
  aggregate: "📊",
  pivot: "🔄",
  unpivot: "↩️",
  window: "🪟",
  bin_column: "🗂️",
  profile: "🩺",
  validate: "✅",
  predict_regression: "📈",
  predict_class: "🏷️",
  cluster: "🫧",
  detect_anomalies: "🚨",
  forecast: "🔮",
  dashboard: "📊",
  custom: "✨",
};

export function opColor(spec: Pick<OpSpec, "category"> | undefined): string {
  return (spec && CATEGORY_COLORS[spec.category]) || "#a78bfa";
}

export function opIcon(spec: Pick<OpSpec, "id"> | undefined): string {
  return (spec && OP_ICONS[spec.id]) || "▢";
}

interface CatalogState {
  catalog: Catalog | null;
  error: string | null;
  load: () => Promise<void>;
}

export const useCatalogStore = create<CatalogState>((set, get) => ({
  catalog: null,
  error: null,
  load: async () => {
    if (get().catalog) return;
    try {
      set({ catalog: await getOperations(), error: null });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : "Could not load operations" });
    }
  },
}));

export function useOpSpec(kind: string | undefined): OpSpec | undefined {
  return useCatalogStore((s) => s.catalog?.operations.find((o) => o.id === kind));
}

export function getOpSpec(kind: string | undefined): OpSpec | undefined {
  return useCatalogStore.getState().catalog?.operations.find((o) => o.id === kind);
}

// ---- config helpers --------------------------------------------------------

export function isVisible(param: OpParam, values: Record<string, unknown>): boolean {
  if (!param.show_if) return true;
  return param.show_if.values.includes(values[param.show_if.param] as never);
}

function isEmpty(v: unknown): boolean {
  return (
    v === undefined ||
    v === null ||
    (typeof v === "string" && v.trim() === "") ||
    (Array.isArray(v) && v.length === 0)
  );
}

export function defaultItem(fields: OpParam[]): Record<string, unknown> {
  const item: Record<string, unknown> = {};
  for (const f of fields) if (f.default !== undefined) item[f.name] = f.default;
  return item;
}

export function defaultConfig(spec: OpSpec): Record<string, unknown> {
  const cfg: Record<string, unknown> = {};
  for (const p of spec.params) {
    if (p.default !== undefined) cfg[p.name] = p.default;
    else if (p.type === "list" && p.required && p.fields) cfg[p.name] = [defaultItem(p.fields)];
  }
  return cfg;
}

/** Missing required settings, as human-readable labels. */
export function missingParams(spec: OpSpec, cfg: Record<string, unknown>): string[] {
  const missing: string[] = [];
  const check = (params: OpParam[], values: Record<string, unknown>, prefix: string) => {
    for (const p of params) {
      if (!isVisible(p, values)) continue;
      const v = values[p.name];
      if (p.type === "list" && Array.isArray(v) && p.fields) {
        if (p.required && v.length === 0) missing.push(prefix + p.label);
        v.forEach((item, i) =>
          check(p.fields!, (item as Record<string, unknown>) || {}, `${prefix}${p.label} #${i + 1}: `),
        );
        continue;
      }
      if (p.required && isEmpty(v)) missing.push(prefix + p.label);
    }
  };
  check(spec.params, cfg, "");
  return missing;
}

function short(v: unknown): string {
  if (Array.isArray(v)) return v.map(short).join(", ");
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v ?? "");
}

const OP_SYMBOL: Record<string, string> = {
  "=": "=", "!=": "≠", ">": ">", ">=": "≥", "<": "<", "<=": "≤",
  between: "between", in: "in", not_in: "not in", contains: "contains",
  not_contains: "not contains", starts_with: "starts with", ends_with: "ends with",
  regex: "matches", is_null: "is empty", not_null: "is not empty",
};

/** One-line description of a configured node for the canvas. */
export function summarizeConfig(spec: OpSpec, cfg: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const p of spec.params) {
    if (!isVisible(p, cfg)) continue;
    const v = cfg[p.name];
    if (isEmpty(v) || v === p.default) continue;
    if (p.type === "list" && Array.isArray(v)) {
      const items = v.map((raw) => {
        const it = (raw || {}) as Record<string, unknown>;
        if ("operator" in it) {
          const val = isEmpty(it.value) ? "" : ` ${short(it.value)}`;
          return `${short(it.column)} ${OP_SYMBOL[String(it.operator)] ?? it.operator}${val}`;
        }
        if ("from" in it && "to" in it) return `${short(it.from)}→${short(it.to)}`;
        if ("left" in it && "right" in it) return `${short(it.left)}=${short(it.right)}`;
        if ("function" in it) {
          return `${short(it.function)}(${isEmpty(it.column) ? "*" : short(it.column)})`;
        }
        return Object.values(it).filter((x) => !isEmpty(x)).map(short).slice(0, 2).join(" ");
      });
      parts.push(items.join(", "));
    } else {
      const opt = p.options?.find((o) => o.value === v);
      parts.push(`${p.label.toLowerCase()}: ${opt ? opt.label : short(v)}`);
    }
  }
  return parts.join(" · ");
}
