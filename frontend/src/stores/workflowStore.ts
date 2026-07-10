import { create } from "zustand";
import type { NodeRunStatus } from "@/lib/nodeStatus";

export interface PreviewData {
  columns: string[];
  rows: any[][];
  total_rows?: number | null;
  schema?: { name: string; dtype: string }[];
}

export interface StepState {
  id: string;                       // node id
  label: string;
  status: NodeRunStatus;            // idle | pending | running | done | error
  preview?: PreviewData | null;
  generatedCode?: string | null;
  rowCount?: number;
  byteSize?: number;
  error?: string | null;
}

export type RunStatus = "idle" | "running" | "success" | "error";

interface State {
  // Current run
  runStatus: RunStatus;
  jobId: string | null;
  engine: string | null;             // selected by router for the active job
  engineReason: string | null;
  detectedRamBytes: number | null;
  steps: StepState[];
  runError: string | null;
  resultDatasetId: string | null;
  resultPreview: PreviewData | null;

  // Source upload state (per source node, keyed by reactflow node id)
  preview: PreviewData | null;       // first uploaded source preview (for empty inspector)

  // UI
  selectedNodeId: string | null;
  edgeType: "smoothstep" | "step" | "straight";
  showCode: boolean;

  // Actions
  setSelectedNodeId: (id: string | null) => void;
  setEdgeType: (t: "smoothstep" | "step" | "straight") => void;
  setShowCode: (v: boolean) => void;
  setPreview: (p: PreviewData | null) => void;

  resetRun: () => void;
  setRunStatus: (s: RunStatus) => void;
  setJobId: (id: string | null) => void;
  setSteps: (s: StepState[]) => void;
  updateStep: (id: string, patch: Partial<StepState>) => void;
  setEngine: (engine: string | null, reason?: string | null, ram?: number | null) => void;
  setRunError: (e: string | null) => void;
  setResult: (datasetId: string | null, preview: PreviewData | null) => void;
}

export const useWorkflowStore = create<State>((set) => ({
  runStatus: "idle",
  jobId: null,
  engine: null,
  engineReason: null,
  detectedRamBytes: null,
  steps: [],
  runError: null,
  resultDatasetId: null,
  resultPreview: null,

  preview: null,

  selectedNodeId: null,
  edgeType: "smoothstep",
  showCode: false,

  setSelectedNodeId: (id) => set({ selectedNodeId: id }),
  setEdgeType: (t) => set({ edgeType: t }),
  setShowCode: (v) => set({ showCode: v }),
  setPreview: (p) => set({ preview: p }),

  resetRun: () =>
    set({
      runStatus: "idle",
      jobId: null,
      engine: null,
      engineReason: null,
      steps: [],
      runError: null,
      resultDatasetId: null,
      resultPreview: null,
    }),
  setRunStatus: (runStatus) => set({ runStatus }),
  setJobId: (jobId) => set({ jobId }),
  setSteps: (steps) => set({ steps }),
  updateStep: (id, patch) =>
    set((s) => ({
      steps: s.steps.map((st) => (st.id === id ? { ...st, ...patch } : st)),
    })),
  setEngine: (engine, reason = null, ram = null) =>
    set({ engine, engineReason: reason, detectedRamBytes: ram }),
  setRunError: (runError) => set({ runError }),
  setResult: (resultDatasetId, resultPreview) =>
    set({ resultDatasetId, resultPreview }),
}));

// -------- SSE event mapper --------
// Maps backend JobEvent payloads to step state updates. Returns true if the
// event indicates the pipeline is finished (succeeded / failed / cancelled).

export interface JobSseEvent {
  seq: number;
  kind: string;
  payload: any;
}

export function applyJobEvent(
  ev: JobSseEvent,
  helpers: {
    updateStep: (id: string, patch: Partial<StepState>) => void;
    setEngine: (engine: string | null, reason?: string | null, ram?: number | null) => void;
    setRunStatus: (s: RunStatus) => void;
    setRunError: (e: string | null) => void;
    setResult: (datasetId: string | null, preview: PreviewData | null) => void;
  },
): boolean {
  const { kind, payload } = ev;

  const markTerminalStepError = (message: string) => {
    useWorkflowStore.setState((state) => {
      const failedNodeId: string | undefined = payload?.node_id;
      let marked = false;
      const nextSteps = state.steps.map((step) => {
        if (failedNodeId && step.id === failedNodeId) {
          marked = true;
          return { ...step, status: "error" as const, error: message };
        }
        if (!failedNodeId && step.status === "running") {
          marked = true;
          return { ...step, status: "error" as const, error: message };
        }
        return step;
      });
      // Fallback: if no running step exists (race or missed event), mark the
      // most recent non-done step as failed so the UI does not keep glowing.
      if (!marked) {
        for (let i = nextSteps.length - 1; i >= 0; i -= 1) {
          const s = nextSteps[i];
          if (s.status !== "done") {
            nextSteps[i] = { ...s, status: "error" as const, error: message };
            break;
          }
        }
      }
      return { steps: nextSteps };
    });
  };

  switch (kind) {
    case "engine_selected":
      helpers.setEngine(
        payload?.engine ?? null,
        payload?.reason ?? null,
        payload?.detected_ram_bytes ?? null,
      );
      return false;
    case "job_started":
      helpers.setRunStatus("running");
      return false;
    case "step_started":
      if (payload?.node_id) {
        helpers.updateStep(payload.node_id, { status: "running" });
      }
      return false;
    case "step_completed":
      if (payload?.node_id) {
        helpers.updateStep(payload.node_id, {
          status: "done",
          preview: payload.preview ?? null,
          generatedCode: payload.code ?? null,
          rowCount: payload.row_count,
          byteSize: payload.byte_size,
        });
      }
      return false;
    case "result_dataset_ready":
      if (payload?.dataset_id) {
        helpers.setResult(payload.dataset_id, null);
      }
      return false;
    case "job_succeeded":
      if (payload?.result_dataset_id) {
        helpers.setResult(payload.result_dataset_id, null);
      }
      helpers.setRunStatus("success");
      return true;
    case "job_failed":
      markTerminalStepError(payload?.error ?? "Pipeline failed");
      helpers.setRunError(payload?.error ?? "Pipeline failed");
      helpers.setRunStatus("error");
      return true;
    case "job_cancelled":
      markTerminalStepError("Job cancelled");
      helpers.setRunStatus("error");
      helpers.setRunError("Job cancelled");
      return true;
    default:
      return false;
  }
}
