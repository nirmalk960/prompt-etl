import { memo } from "react";
import { motion } from "framer-motion";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { ExternalLink, AlertTriangle } from "lucide-react";
import { cn, hexToRgba } from "@/lib/utils";
import { missingParams, summarizeConfig, useOpSpec } from "@/lib/catalog";
import { nodeKind, nodeMode, visualFor } from "@/lib/operations";
import { STATUS_GLOW, useNodeRunStatus } from "@/lib/nodeStatus";
import { useWorkflowStore } from "@/stores/workflowStore";
import { dashboardUrl } from "@/api/client";
import { StatusBadge } from "./StatusBadge";

export interface OperationData {
  operationId: string;
  backendKind?: string;
  label?: string;
  color?: string;
  icon?: string;
  prompt?: string;
  mode?: "configure" | "prompt";
  config?: Record<string, unknown>;
}

const METRIC_LABELS: [string, string, (v: number) => string][] = [
  ["r2", "R²", (v) => v.toFixed(3)],
  ["accuracy", "accuracy", (v) => `${(v * 100).toFixed(1)}%`],
  ["silhouette", "silhouette", (v) => v.toFixed(2)],
  ["anomalies", "anomalies", (v) => String(v)],
  ["mape_pct", "MAPE", (v) => `${v.toFixed(1)}%`],
];

function headlineMetric(metrics: Record<string, any> | null | undefined): string | null {
  if (!metrics) return null;
  for (const [key, label, fmt] of METRIC_LABELS) {
    if (typeof metrics[key] === "number") return `${label} ${fmt(metrics[key])}`;
  }
  return null;
}

function OperationNodeComponent({ id, data, selected, type }: NodeProps) {
  const d = data as unknown as OperationData;
  const kind = nodeKind({ id, data, type } as Node);
  const spec = useOpSpec(kind);
  const visual = visualFor(kind, spec);
  const color = d.color || visual.color;
  const icon = visual.icon || d.icon || "▢";
  const label = d.label || visual.label;
  const mode = nodeMode({ id, data, type } as Node, spec);
  const config = d.config ?? {};
  const prompt = d.prompt || "";
  const twoInputs = (spec?.inputs ?? 1) === 2;
  const isDashboard = kind === "dashboard";

  const status = useNodeRunStatus(id);
  const step = useWorkflowStore((s) => s.steps.find((x) => x.id === id));
  const schemaError = useWorkflowStore((s) => s.nodeSchemas[id]?.error);
  const statusGlow = STATUS_GLOW[status];
  const isRunning = status === "running";

  const missing = spec && mode === "configure" ? missingParams(spec, config) : [];
  const summary = spec && mode === "configure" ? summarizeConfig(spec, config) : "";
  const metric = headlineMetric(step?.metrics);

  const baseShadow = selected
    ? `0 0 0 1px ${hexToRgba(color, 0.6)}, 0 18px 40px -14px ${hexToRgba(color, 0.55)}, inset 0 1px 0 rgba(255,255,255,0.08)`
    : `0 12px 28px -14px ${hexToRgba(color, 0.5)}, inset 0 1px 0 rgba(255,255,255,0.06)`;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92, y: 6 }}
      animate={{ opacity: 1, scale: 1, y: 0, boxShadow: statusGlow || baseShadow }}
      transition={{
        type: "spring",
        stiffness: 260,
        damping: 22,
        boxShadow: isRunning
          ? { duration: 1.4, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" }
          : { duration: 0.45 },
      }}
      whileHover={{ y: -2 }}
      className={cn("relative min-w-[210px] max-w-[260px] rounded-2xl px-3.5 py-2.5 transition-all")}
      style={{
        background: `linear-gradient(150deg, ${hexToRgba(color, 0.22)} 0%, rgba(15,17,32,0.78) 60%)`,
        border: `1px solid ${hexToRgba(color, selected ? 0.7 : 0.35)}`,
        backdropFilter: "blur(20px) saturate(170%)",
        WebkitBackdropFilter: "blur(20px) saturate(170%)",
      }}
    >
      <StatusBadge status={status} />
      {twoInputs ? (
        <>
          <Handle type="target" id="left" position={Position.Top} style={{ left: "28%" }} />
          <Handle type="target" id="right" position={Position.Top} style={{ left: "72%" }} />
          <div className="pointer-events-none absolute -top-4 left-0 right-0 flex justify-between px-[18%] text-[8.5px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            <span>left</span>
            <span>right</span>
          </div>
        </>
      ) : (
        <Handle type="target" position={Position.Top} />
      )}
      <div className="flex items-center gap-2">
        <div
          className="flex h-7 w-7 items-center justify-center rounded-lg text-base"
          style={{
            background: `linear-gradient(135deg, ${hexToRgba(color, 0.45)}, ${hexToRgba(color, 0.18)})`,
            border: `1px solid ${hexToRgba(color, 0.4)}`,
            boxShadow: `inset 0 1px 0 rgba(255,255,255,0.15), 0 4px 10px -4px ${hexToRgba(color, 0.6)}`,
          }}
        >
          <span style={{ filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.5))" }}>{icon}</span>
        </div>
        <div className="flex-1">
          <div className="text-[11px] font-semibold tracking-wide text-foreground/95">{label}</div>
          <div
            className="text-[9px] font-medium uppercase tracking-[0.16em]"
            style={{ color: hexToRgba(color, 0.9) }}
          >
            {mode === "prompt" ? "Prompt" : spec?.engine === "python" ? "Python" : "Configured"}
            {step?.engine && status === "done" && step.engine !== "python" ? ` · ${step.engine}` : ""}
          </div>
        </div>
      </div>

      {mode === "prompt" ? (
        <p
          className={cn(
            "mt-2 line-clamp-2 border-t border-white/5 pt-2 text-[10px] leading-relaxed",
            prompt ? "text-muted-foreground" : "italic text-muted-foreground/70",
          )}
        >
          {prompt ? `${prompt.slice(0, 110)}${prompt.length > 110 ? "…" : ""}` : "Select to add a prompt…"}
        </p>
      ) : (
        <p
          className={cn(
            "mt-2 line-clamp-2 border-t border-white/5 pt-2 text-[10px] leading-relaxed",
            missing.length ? "italic text-amber-200/80" : "text-muted-foreground",
          )}
        >
          {missing.length
            ? `Set ${missing.slice(0, 2).join(", ")}${missing.length > 2 ? "…" : ""}`
            : summary || "Default settings"}
        </p>
      )}

      {schemaError && status !== "done" && (
        <p className="mt-1.5 flex items-start gap-1 text-[9.5px] leading-snug text-rose-300">
          <AlertTriangle className="mt-[1px] h-2.5 w-2.5 shrink-0" />
          <span className="line-clamp-2">{schemaError}</span>
        </p>
      )}

      {metric && status === "done" && (
        <div className="mt-1.5 inline-flex rounded-full bg-white/[0.06] px-2 py-0.5 text-[9.5px] font-semibold text-foreground/85">
          {metric}
        </div>
      )}

      {isDashboard && (
        <button
          type="button"
          disabled={!step?.dashboardPath}
          onClick={(e) => {
            e.stopPropagation();
            if (step?.dashboardPath) window.open(dashboardUrl(step.dashboardPath), "_blank", "noopener");
          }}
          className={cn(
            "nodrag mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg px-2 py-1.5 text-[10.5px] font-semibold transition-colors",
            step?.dashboardPath
              ? "bg-orange-500/80 text-white hover:bg-orange-500"
              : "cursor-not-allowed bg-white/[0.05] text-muted-foreground",
          )}
          title={step?.dashboardPath ? "Open the dashboard in a new tab" : "Run the pipeline to build the dashboard"}
        >
          <ExternalLink className="h-3 w-3" />
          {step?.dashboardPath ? "Open dashboard" : "Run to build dashboard"}
        </button>
      )}

      <Handle type="source" position={Position.Bottom} />
    </motion.div>
  );
}

export const OperationNode = memo(OperationNodeComponent);
