import { useWorkflowStore } from "@/stores/workflowStore";

export type NodeRunStatus = "idle" | "pending" | "running" | "done" | "error";

export const STATUS_DOT: Record<NodeRunStatus, string | undefined> = {
  idle: undefined,
  pending: "rgba(148, 163, 184, 0.95)",
  running: "rgba(251, 191, 36, 0.95)",
  done: "rgba(52, 211, 153, 0.95)",
  error: "rgba(244, 63, 94, 0.95)",
};

export const STATUS_GLOW: Record<NodeRunStatus, string | undefined> = {
  idle: undefined,
  pending: undefined,
  running:
    "0 0 0 1px rgba(251, 191, 36, 0.5), 0 20px 50px -10px rgba(251, 191, 36, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.1)",
  done:
    "0 0 0 1px rgba(52, 211, 153, 0.45), 0 18px 40px -12px rgba(52, 211, 153, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.1)",
  error:
    "0 0 0 1px rgba(244, 63, 94, 0.5), 0 18px 40px -12px rgba(244, 63, 94, 0.5), inset 0 1px 0 rgba(255, 255, 255, 0.1)",
};

export function useNodeRunStatus(nodeId: string): NodeRunStatus {
  const step = useWorkflowStore((s) => s.steps.find((x) => x.id === nodeId));
  if (!step) return "idle";
  return step.status;
}
