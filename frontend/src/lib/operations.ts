// Canvas node helpers. Source and Write are built into the canvas; every
// other box is an operation from the backend catalog (see catalog.ts).

import type { Node } from "@xyflow/react";
import { getOpSpec, opColor, opIcon, type OpSpec } from "./catalog";

export type NodeMode = "configure" | "prompt";

export interface IoBubble {
  operationId: "source" | "write";
  label: string;
  icon: string;
  color: string;
  description: string;
}

export const IO_BUBBLES: IoBubble[] = [
  {
    operationId: "source",
    label: "Source",
    icon: "📂",
    color: "#3b82f6",
    description: "Load a CSV, JSON, Excel or Parquet file",
  },
  {
    operationId: "write",
    label: "Write",
    icon: "📁",
    color: "#22c55e",
    description: "Save the result as a dataset and export it",
  },
];

export interface NodeVisual {
  label: string;
  icon: string;
  color: string;
  description: string;
}

/** Label, icon, colour and description for a palette id or node kind. */
export function visualFor(kind: string, spec?: OpSpec): NodeVisual {
  const io = IO_BUBBLES.find((b) => b.operationId === kind);
  if (io) return io;
  const s = spec ?? getOpSpec(kind);
  return {
    label: s?.label ?? kind,
    icon: opIcon(s),
    color: opColor(s),
    description: s?.description ?? "",
  };
}

/** The backend `kind` for a canvas node. */
export function nodeKind(n: Node): string {
  if (n.type === "source") return "source";
  if (n.type === "write") return "write";
  return (n.data?.backendKind as string) || (n.data?.operationId as string) || "custom";
}

/** Effective mode, mirroring the backend's PipelineNode.effective_mode. */
export function nodeMode(n: Node, spec?: OpSpec): NodeMode {
  const s = spec ?? getOpSpec(nodeKind(n));
  if (s && !s.supports_prompt) return "configure";
  if (s && !s.supports_configure) return "prompt";
  const mode = n.data?.mode as NodeMode | undefined;
  if (mode) return mode;
  const cfg = n.data?.config as Record<string, unknown> | undefined;
  return cfg && Object.keys(cfg).length > 0 ? "configure" : "prompt";
}

export function getReactFlowNodeType(kind: string): string {
  if (kind === "source") return "source";
  if (kind === "write") return "write";
  if (kind === "dashboard") return "dashboard";
  return "operation";
}
