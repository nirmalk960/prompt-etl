import type { Edge, Node } from "@xyflow/react";
import { getOpSpec, missingParams } from "./catalog";
import { nodeKind, nodeMode } from "./operations";
import type { NodeSchemaInfo } from "@/stores/workflowStore";

export interface PreflightIssue {
  nodeId: string;
  label: string;
  message: string;
  suggestion?: string;
}

interface PreflightResult {
  ok: boolean;
  issues: PreflightIssue[];
}

type AggMetric = { fn: string; column: string };

function normalizeCol(value: string): string {
  return value
    .trim()
    .replace(/^["'`]|["'`]$/g, "")
    .toLowerCase()
    .replace(/\s+/g, "_");
}

function candidateColKeys(raw: string): string[] {
  const base = normalizeCol(raw);
  const out = new Set<string>([base]);
  if (base.startsWith("total_")) out.add(`sum_${base.slice("total_".length)}`);
  if (base.startsWith("sum_")) out.add(`total_${base.slice("sum_".length)}`);
  if (base.startsWith("average_")) out.add(`avg_${base.slice("average_".length)}`);
  if (base.startsWith("avg_")) out.add(`average_${base.slice("avg_".length)}`);
  if (base.startsWith("mean_")) out.add(`avg_${base.slice("mean_".length)}`);
  if (base.startsWith("avg_")) out.add(`mean_${base.slice("avg_".length)}`);
  return Array.from(out);
}

function parseSelectColumns(prompt: string): string[] | null {
  const m = prompt.match(
    /(?:keep|select|show|choose)\s+(?:only\s+)?(?:columns?\s*:?\s*)?(.+)$/i,
  );
  if (!m) return null;
  return m[1]
    .split(",")
    .flatMap((x) => x.split(/\s+and\s+/i))
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseSortColumns(prompt: string): string[] | null {
  const m = prompt.match(/sort\s+by\s+(.+)$/i);
  if (!m) return null;
  const raw = m[1]
    .replace(/\bascending\b/gi, "")
    .replace(/\bdescending\b/gi, "")
    .replace(/\basc\b/gi, "")
    .replace(/\bdesc\b/gi, "");
  return raw
    .split(",")
    .flatMap((x) => x.split(/\s+and\s+/i))
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseRename(prompt: string): { from: string; to: string } | null {
  const m = prompt.match(/rename\s+column\s+([A-Za-z_]\w*)\s+to\s+([A-Za-z_]\w*)/i);
  if (!m) return null;
  return { from: m[1], to: m[2] };
}

function parseGroupByColumns(prompt: string): string[] | null {
  const m = prompt.match(/group\s+by\s+(.+?)(?:\s+and\s+calculate|\s+calculate|$)/i);
  if (!m) return null;
  return m[1]
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)
    .flatMap((x) => x.split(/\s+and\s+/i))
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => {
      const mm = x.match(/month\s*\(\s*([A-Za-z_]\w*)\s*\)/i);
      if (mm) return "month";
      return x;
    });
}

const AGG_FN = "sum|mean|avg|average|count|min|max";

function normalizeAggFn(raw: string): string {
  const fn = raw.toLowerCase();
  if (fn === "mean" || fn === "average") return "avg";
  return fn;
}

function parseAggregateAliases(prompt: string): string[] {
  const out = new Set<string>();
  let m: RegExpExecArray | null = null;

  // total_revenue (sum order_total)  — alias must be a single identifier
  const p1 = new RegExp(
    `\\b([A-Za-z_]\\w*)\\s*\\(\\s*\\b(${AGG_FN})\\b\\s*(?:of\\s+)?\\(?\\s*([A-Za-z_]\\w*)\\s*\\)?\\s*\\)`,
    "gi",
  );
  while ((m = p1.exec(prompt)) !== null) out.add(normalizeCol(m[1]));

  // total_revenue = sum order_total  /  total_revenue: the sum of order_total
  const p2 = new RegExp(
    `\\b([A-Za-z_]\\w*)\\s*(?:=|:)\\s*(?:the\\s+)?\\b(${AGG_FN})\\b\\s*(?:of\\s+)?\\(?\\s*([A-Za-z_]\\w*)\\s*\\)?`,
    "gi",
  );
  while ((m = p2.exec(prompt)) !== null) out.add(normalizeCol(m[1]));

  // sum(order_total) as total_revenue
  const p3 = new RegExp(
    `\\b(${AGG_FN})\\b\\s*\\(?\\s*([A-Za-z_]\\w*)\\s*\\)?\\s+as\\s+([A-Za-z_]\\w*)`,
    "gi",
  );
  while ((m = p3.exec(prompt)) !== null) out.add(normalizeCol(m[3]));

  // total_revenue as [the] sum [of] order_total
  const p4 = new RegExp(
    `\\b([A-Za-z_]\\w*)\\s+as\\s+(?:the\\s+)?\\b(${AGG_FN})\\b\\s*(?:of\\s+)?\\(?\\s*([A-Za-z_]\\w*)\\s*\\)?`,
    "gi",
  );
  while ((m = p4.exec(prompt)) !== null) out.add(normalizeCol(m[1]));

  // Drop filler / reserved words that greedy matches can occasionally capture.
  for (const junk of ["the", "a", "an", "and", "by", "of", "group", "calculate", "as"]) {
    out.delete(junk);
  }
  return Array.from(out);
}

function parseAggregateMetrics(prompt: string): AggMetric[] {
  const out = new Map<string, AggMetric>();
  // Word boundaries are required so "avg" inside "avg_order_value" is not
  // treated as a metric fn with a fake column "_order_value".
  const metricExpr = new RegExp(
    `\\b(${AGG_FN})\\b\\s*(?:of\\s+)?\\(?\\s*([A-Za-z_]\\w*)\\s*\\)?`,
    "gi",
  );
  let m: RegExpExecArray | null = null;
  while ((m = metricExpr.exec(prompt)) !== null) {
    const fn = normalizeAggFn(m[1]);
    const column = m[2].trim();
    // Reject leftovers from partial identifier splits (e.g. "_order_value").
    if (column.startsWith("_")) continue;
    out.set(`${fn}:${normalizeCol(column)}`, { fn, column });
  }
  return Array.from(out.values());
}

function requireColumns(
  node: Node,
  needed: string[],
  available: string[],
  issues: PreflightIssue[],
  suggestion?: string,
) : { missing: string[]; resolved: string[] } {
  const canonical = new Map<string, string>();
  for (const col of available) {
    canonical.set(normalizeCol(col), col);
  }
  const missing: string[] = [];
  const resolved: string[] = [];
  for (const col of needed) {
    const keys = candidateColKeys(col);
    let hit: string | undefined;
    for (const key of keys) {
      hit = canonical.get(key);
      if (hit) break;
    }
    if (!hit) {
      for (const [k, orig] of canonical.entries()) {
        if (keys.some((cand) => k.includes(cand) || cand.includes(k))) {
          hit = orig;
          break;
        }
      }
    }
    if (hit) resolved.push(hit);
    else missing.push(col);
  }
  if (missing.length > 0) {
    issues.push({
      nodeId: node.id,
      label: (node.data?.label as string) || node.id,
      message: `Cannot execute: missing input column(s): ${missing.join(", ")}`,
      suggestion,
    });
  }
  return { missing, resolved };
}

export function preflightPipeline(
  nodes: Node[],
  edges: Edge[],
  serverSchemas: Record<string, NodeSchemaInfo> = {},
): PreflightResult {
  const issues: PreflightIssue[] = [];
  const nodeById = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const parents = new Map<string, string[]>();
  for (const e of edges) {
    parents.set(e.target, [...(parents.get(e.target) ?? []), e.source]);
  }
  const inDegree = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  for (const e of edges) inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1);
  const ready = nodes.filter((n) => (inDegree.get(n.id) ?? 0) === 0).map((n) => n.id);
  const order: string[] = [];
  while (ready.length) {
    ready.sort();
    const id = ready.shift()!;
    order.push(id);
    for (const e of edges.filter((x) => x.source === id)) {
      const d = (inDegree.get(e.target) ?? 0) - 1;
      inDegree.set(e.target, d);
      if (d === 0) ready.push(e.target);
    }
  }
  if (order.length !== nodes.length) {
    return {
      ok: false,
      issues: [{ nodeId: "pipeline", label: "Pipeline", message: "Cannot execute: graph has a cycle." }],
    };
  }

  // null = columns unknown until the pipeline runs (pivot, prompt steps, ...)
  const schemaByNode = new Map<string, string[] | null>();

  for (const id of order) {
    const node = nodeById[id];
    if (!node) continue;
    const kind = nodeKind(node);
    const label = (node.data?.label as string) || id;
    const prompt = ((node.data?.prompt as string) || "").trim();

    if (kind === "source") {
      const schemaCols =
        ((node.data?.preview as any)?.schema as Array<{ name: string; dtype: string }> | undefined)?.map((c) => c.name) ??
        ((node.data?.preview as any)?.columns as string[] | undefined) ??
        [];
      if (schemaCols.length === 0) {
        issues.push({
          nodeId: id,
          label,
          message: "Cannot execute: source schema is unavailable.",
          suggestion: "Upload and ingest a dataset from the Source node first.",
        });
      }
      schemaByNode.set(id, schemaCols);
      continue;
    }

    const p = parents.get(id) ?? [];
    if (p.length === 0) {
      issues.push({
        nodeId: id,
        label,
        message: "Cannot execute: step has no upstream input.",
      });
      schemaByNode.set(id, []);
      continue;
    }
    const parentCols = p.map((parentId) => schemaByNode.get(parentId) ?? null);
    const known = parentCols.every((c) => c !== null);
    const inputCols = Array.from(
      new Set(parentCols.flatMap((c) => c ?? []).map((c) => c.trim())),
    );
    let outCols: string[] | null = known ? [...inputCols] : null;

    if (kind === "write") {
      schemaByNode.set(id, outCols);
      continue;
    }

    const spec = getOpSpec(kind);
    if (spec && nodeMode(node, spec) === "configure") {
      if (spec.inputs === 2 && p.length !== 2) {
        issues.push({
          nodeId: id,
          label,
          message: `Cannot execute: ${spec.label} needs two inputs.`,
          suggestion: "Connect one step to the 'left' input and one to the 'right' input.",
        });
      }
      const missing = missingParams(spec, (node.data?.config as Record<string, unknown>) ?? {});
      if (missing.length) {
        issues.push({
          nodeId: id,
          label,
          message: `Cannot execute: fill in ${missing.join(", ")}.`,
        });
      }
      const server = serverSchemas[id];
      if (server?.error && !missing.length) {
        issues.push({ nodeId: id, label, message: `Cannot execute: ${server.error}` });
      }
      schemaByNode.set(id, server?.columns ? server.columns.map((c) => c.name) : null);
      continue;
    }

    if (!known) {
      // Columns upstream are unknown: only check that a prompt exists.
      if (!prompt) issues.push({ nodeId: id, label, message: "Cannot execute: prompt is empty." });
      schemaByNode.set(id, null);
      continue;
    }

    if (!prompt) {
      issues.push({
        nodeId: id,
        label,
        message: "Cannot execute: prompt is empty.",
      });
      schemaByNode.set(id, outCols);
      continue;
    }

    if (kind === "select") {
      const cols = parseSelectColumns(prompt);
      if (!cols || cols.length === 0) {
        issues.push({
          nodeId: id,
          label,
          message: "Cannot execute: select prompt is unclear.",
          suggestion: "Use: Keep columns: col1, col2, col3",
        });
      } else {
        const checked = requireColumns(
          node,
          cols,
          inputCols,
          issues,
          "Check spelling and upstream schema.",
        );
        if (checked.missing.length === 0) {
          outCols = checked.resolved;
        }
      }
    } else if (kind === "sort") {
      const cols = parseSortColumns(prompt);
      if (!cols || cols.length === 0) {
        issues.push({
          nodeId: id,
          label,
          message: "Cannot execute: sort prompt is unclear.",
          suggestion: "Use: Sort by month ascending, total_revenue descending",
        });
      } else {
        requireColumns(node, cols, inputCols, issues, "Sort can only use existing columns.");
      }
    } else if (kind === "rename") {
      const ren = parseRename(prompt);
      if (!ren) {
        issues.push({
          nodeId: id,
          label,
          message: "Cannot execute: rename prompt is unclear.",
          suggestion: "Use: Rename column old_name to new_name",
        });
      } else {
        requireColumns(node, [ren.from], inputCols, issues, "Column to rename must exist.");
        outCols = inputCols.map((c) => (c === ren.from ? ren.to : c));
      }
    } else if (kind === "filter" || kind === "drop_nulls" || kind === "fill_nulls" || kind === "deduplicate") {
      // Keep schema unchanged. We rely on backend validation + runtime errors for
      // complex expressions, but still guard obvious prompt omissions.
      if (kind === "filter" && !/\bwhere\b/i.test(prompt)) {
        issues.push({
          nodeId: id,
          label,
          message: "Cannot execute: filter prompt should include a condition.",
          suggestion: "Use: Keep rows where <column> <condition>",
        });
      }
    } else if (kind === "aggregate") {
      const groupCols = parseGroupByColumns(prompt) ?? [];
      const metrics = parseAggregateMetrics(prompt);
      const metricCols = metrics.map((m) => m.column);
      const aliases = parseAggregateAliases(prompt);
      if (!/\bgroup\s+by\b/i.test(prompt)) {
        issues.push({
          nodeId: id,
          label,
          message: "Cannot execute: aggregate prompt should include group by columns.",
          suggestion:
            "Use: Group by month and region and calculate total_revenue (sum order_total), total_orders (count order_id)",
        });
      }
      const checkCols = groupCols.filter((c) => c !== "month");
      requireColumns(
        node,
        [...checkCols, ...metricCols],
        inputCols,
        issues,
        "Aggregate references columns not present in upstream schema.",
      );
      const fallbackMetricNames = metrics.map((m) => `${m.fn}_${normalizeCol(m.column)}`);
      outCols = [...groupCols.map((c) => normalizeCol(c)), ...(aliases.length ? aliases : fallbackMetricNames)];
    }

    schemaByNode.set(id, outCols);
  }

  return { ok: issues.length === 0, issues };
}
