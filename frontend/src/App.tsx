import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addEdge,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
} from "@xyflow/react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { motion, AnimatePresence } from "framer-motion";

import {
  getReactFlowNodeType,
  nodeKind,
  nodeMode,
  visualFor,
  type NodeMode,
} from "@/lib/operations";
import { defaultConfig, getOpSpec, useCatalogStore } from "@/lib/catalog";
import {
  applyJobEvent,
  useWorkflowStore,
  type StepState,
} from "@/stores/workflowStore";
import { topoSort } from "@/lib/utils";
import { preflightPipeline, type PreflightIssue } from "@/lib/preflight";
import {
  cancelJob,
  completeUpload,
  createJob,
  exportDataset,
  getDataset,
  getAccessToken,
  getApiBaseUrl,
  getReady,
  initUpload,
  jobEventsUrl,
  previewDataset,
  previewPipelineSchema,
  setApiBaseUrl,
  uploadPart,
  type PipelineDefinition,
  type PipelineEdgePayload,
  type PipelineNodePayload,
  type ReadyState,
} from "@/api/client";

import { TopBar } from "@/components/layout/TopBar";
import { OperationBubbleBar } from "@/components/flow/OperationBubbleBar";
import { FlowCanvas } from "@/components/flow/FlowCanvas";
import { InspectorPanel } from "@/components/flow/InspectorPanel";
import { StepTimeline } from "@/components/flow/StepTimeline";
import { SettingsDialog } from "@/components/SettingsDialog";
import type { WriteConfig } from "@/components/flow/WriteNode";

// ---------------------------------------------------------------------------

function isKnownKind(kind: string): boolean {
  return kind === "source" || kind === "write" || Boolean(getOpSpec(kind));
}

function createNodeFromOperation(kind: string, position: { x: number; y: number }): Node {
  const spec = getOpSpec(kind);
  const visual = visualFor(kind, spec);
  const id = `${kind}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
  const base: Node = {
    id,
    type: getReactFlowNodeType(kind),
    position,
    data: {
      operationId: kind,
      backendKind: kind,
      label: visual.label,
      color: visual.color,
      icon: visual.icon,
      prompt: spec?.preset_prompt ?? "",
      ...(spec
        ? {
            mode: spec.supports_configure ? "configure" : "prompt",
            config: spec.supports_configure ? defaultConfig(spec) : {},
          }
        : {}),
    },
  };
  if (kind === "source") {
    base.data = {
      ...base.data,
      uploadStatus: "idle",
      uploadProgress: 0,
    };
  }
  if (kind === "write") {
    base.data = {
      ...base.data,
      writeConfig: {
        datasetName: "",
        format: "parquet",
        destination: "download",
        fileName: "prompt-etl-output",
        autoExport: true,
      } satisfies WriteConfig,
    };
  }
  return base;
}

const DEFAULT_BOARD_NAME = "Untitled board";

/** Make a board name safe for use in file names. */
function slugifyBoardName(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "prompt-etl";
}

function inferSourceFormat(filename: string): "csv" | "json" | "xlsx" | "parquet" {
  const ext = filename.toLowerCase().split(".").pop() || "";
  if (ext === "json") return "json";
  if (ext === "xlsx" || ext === "xls") return "xlsx";
  if (ext === "parquet" || ext === "pq") return "parquet";
  return "csv";
}

function buildPipelineDefinition(
  nodes: Node[],
  edges: Edge[],
  enginePref: "auto" | "pandas" | "polars" | "duckdb",
): PipelineDefinition {
  const pNodes: PipelineNodePayload[] = nodes.map((n) => {
    const kind = nodeKind(n);
    const payload: PipelineNodePayload = {
      id: n.id,
      kind,
      label: (n.data?.label as string) ?? null,
      prompt: (n.data?.prompt as string) ?? null,
    };
    if (kind !== "source" && kind !== "write") {
      const mode = nodeMode(n);
      payload.mode = mode;
      if (mode === "configure") payload.config = (n.data?.config as Record<string, unknown>) ?? {};
    }
    if (kind === "source") {
      payload.dataset_id = (n.data?.datasetId as string) ?? null;
    }
    if (kind === "write") {
      const cfg = (n.data?.writeConfig as WriteConfig) || {};
      if (cfg.datasetName) payload.output_name = cfg.datasetName;
    }
    return payload;
  });
  const pEdges: PipelineEdgePayload[] = edges.map((e) => ({
    source: e.source,
    target: e.target,
    target_handle: e.targetHandle ?? null,
  }));
  return { nodes: pNodes, edges: pEdges, engine: enginePref };
}

// ---------------------------------------------------------------------------

export default function App() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [backendOk, setBackendOk] = useState(false);
  const [ready, setReady] = useState<ReadyState | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(() => !getAccessToken());
  const [enginePref, setEnginePref] = useState<
    "auto" | "pandas" | "polars" | "duckdb"
  >("auto");
  const [apiBaseUrl, setApiBaseUrlState] = useState(() => getApiBaseUrl());
  const [boardName, setBoardName] = useState(
    () => localStorage.getItem("prompt-etl.board-name") || DEFAULT_BOARD_NAME,
  );
  const [exporting, setExporting] = useState(false);
  const [preflightIssues, setPreflightIssues] = useState<PreflightIssue[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const lastEventAtRef = useRef<number>(Date.now());
  const outputDirHandlesRef = useRef<Record<string, any>>({});
  const lastExportedJobRef = useRef<string | null>(null);

  const {
    runStatus,
    setRunStatus,
    jobId,
    setJobId,
    steps,
    setSteps,
    updateStep,
    setEngine,
    setRunError,
    setResult,
    selectedNodeId,
    setSelectedNodeId,
    edgeType,
    setEdgeType,
    resetRun,
    setPreview,
    engine,
  } = useWorkflowStore();

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) ?? null;
  const catalog = useCatalogStore((s) => s.catalog);
  const catalogError = useCatalogStore((s) => s.error);
  const loadCatalog = useCatalogStore((s) => s.load);
  const nodeSchemas = useWorkflowStore((s) => s.nodeSchemas);
  const setNodeSchemas = useWorkflowStore((s) => s.setNodeSchemas);

  useEffect(() => {
    localStorage.setItem("prompt-etl.board-name", boardName);
  }, [boardName]);

  useEffect(() => {
    if (backendOk && getAccessToken()) loadCatalog();
  }, [backendOk, loadCatalog]);

  useEffect(() => {
    if (!getAccessToken() || (catalogError != null && catalogError.startsWith("401"))) {
      setSettingsOpen(true);
    }
  }, [catalogError]);

  // Ask the backend for each node's output columns (no data is processed).
  // Keyed on the definition so dragging nodes around doesn't refetch.
  const definitionKey = useMemo(
    () => (catalog ? JSON.stringify(buildPipelineDefinition(nodes, edges, "auto")) : ""),
    [catalog, nodes, edges],
  );
  useEffect(() => {
    if (!definitionKey || !backendOk) return;
    const definition = JSON.parse(definitionKey) as PipelineDefinition;
    if (!definition.nodes.some((n) => n.kind === "source" && n.dataset_id)) {
      setNodeSchemas({});
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      previewPipelineSchema(definition, ctrl.signal)
        .then((r) => setNodeSchemas(r.nodes))
        .catch(() => {
          // Preview is best-effort; the run itself reports real errors.
        });
    }, 400);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [definitionKey, backendOk, setNodeSchemas]);

  useEffect(() => {
    const preflight = preflightPipeline(nodes, edges, nodeSchemas);
    setPreflightIssues(preflight.issues);
  }, [nodes, edges, nodeSchemas, catalog]);

  // Columns offered by the settings form: per input, from the predicted
  // schema of each parent (left input first).
  const inputColumns = useMemo<(string[] | null)[]>(() => {
    if (!selectedNode) return [];
    const incoming = edges
      .filter((e) => e.target === selectedNode.id)
      .sort((a, b) => (a.targetHandle === "right" ? 1 : 0) - (b.targetHandle === "right" ? 1 : 0));
    return incoming.map((e) => {
      const server = nodeSchemas[e.source]?.columns;
      if (server) return server.map((c) => c.name);
      const parent = nodes.find((n) => n.id === e.source);
      const local = (parent?.data?.preview as any)?.schema as { name: string }[] | undefined;
      return local ? local.map((c) => c.name) : null;
    });
  }, [selectedNode, edges, nodes, nodeSchemas]);

  // ---- health / ready polling --------------------------------------------
  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const r = await getReady();
        if (cancelled) return;
        setReady(r);
        setBackendOk(true);
      } catch {
        if (cancelled) return;
        setBackendOk(false);
      }
    }
    poll();
    const id = setInterval(poll, 10000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // ---- drag-drop from bubble bar -----------------------------------------
  useEffect(() => {
    const raw = localStorage.getItem("prompt-etl.workflow");
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed.nodes)) setNodes(parsed.nodes);
      if (Array.isArray(parsed.edges)) setEdges(parsed.edges);
      if (typeof parsed.name === "string" && parsed.name.trim()) {
        setBoardName(parsed.name.trim());
      }
      if (
        parsed.edgeType === "smoothstep" ||
        parsed.edgeType === "step" ||
        parsed.edgeType === "straight"
      ) {
        setEdgeType(parsed.edgeType);
      }
    } catch {
      // ignore invalid local state
    }
  }, [setNodes, setEdges, setEdgeType]);

  useEffect(() => {
    const handler = (e: Event) => {
      const { operationId, position } = (e as CustomEvent).detail;
      if (!isKnownKind(operationId)) return;
      setNodes((nds) => [...nds, createNodeFromOperation(operationId, position)]);
    };
    window.addEventListener("prompt-etl-add-operation", handler);
    return () => window.removeEventListener("prompt-etl-add-operation", handler);
  }, [setNodes]);

  const onDragStart = useCallback((e: React.DragEvent, operationId: string) => {
    e.dataTransfer.setData("application/prompt-etl-operation", operationId);
    e.dataTransfer.effectAllowed = "move";
    const ghost = document.createElement("div");
    ghost.style.width = "1px";
    ghost.style.height = "1px";
    document.body.appendChild(ghost);
    e.dataTransfer.setDragImage(ghost, 0, 0);
    setTimeout(() => document.body.removeChild(ghost), 0);
  }, []);

  const onAddOperation = useCallback(
    (operationId: string) => {
      if (!isKnownKind(operationId)) return;
      const position = { x: 240 + nodes.length * 36, y: 140 + nodes.length * 52 };
      setNodes((nds) => [...nds, createNodeFromOperation(operationId, position)]);
    },
    [nodes.length, setNodes],
  );

  const onConnect = useCallback(
    (params: Connection) =>
      setEdges((eds) =>
        addEdge({ ...params, type: edgeType, animated: true }, eds),
      ),
    [edgeType, setEdges],
  );

  // ---- Source upload via presigned multipart -----------------------------
  const onSourceFileSelect = useCallback(
    async (nodeId: string, file: File) => {
      const fmt = inferSourceFormat(file.name);
      // mark uploading
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId
            ? {
                ...n,
                data: {
                  ...n.data,
                  filename: file.name,
                  uploadStatus: "uploading",
                  uploadProgress: 0,
                  error: null,
                  datasetId: null,
                  preview: null,
                },
              }
            : n,
        ),
      );

      try {
        const init = await initUpload({
          name: file.name,
          source_format: fmt,
          size_bytes: file.size,
        });

        // Upload parts sequentially with simple progress.
        const completedParts: { part_number: number; etag: string }[] = [];
        let bytesUploaded = 0;
        for (const p of init.parts) {
          const start = (p.part_number - 1) * init.part_size_bytes;
          const end = Math.min(start + init.part_size_bytes, file.size);
          const blob = file.slice(start, end);
          const etag = await uploadPart(p.url, blob);
          completedParts.push({ part_number: p.part_number, etag });
          bytesUploaded += blob.size;
          const pct = Math.round((bytesUploaded / file.size) * 100);
          setNodes((nds) =>
            nds.map((n) =>
              n.id === nodeId
                ? { ...n, data: { ...n.data, uploadProgress: pct } }
                : n,
            ),
          );
        }

        // Mark ingesting after upload completes
        setNodes((nds) =>
          nds.map((n) =>
            n.id === nodeId
              ? {
                  ...n,
                  data: { ...n.data, uploadStatus: "ingesting", uploadProgress: 100 },
                }
              : n,
          ),
        );

        await completeUpload(init.dataset_id, init.upload_id, completedParts);

        // Poll the dataset until status === ready / failed
        const poll = async (): Promise<void> => {
          const ds = await getDataset(init.dataset_id);
          if (ds.status === "ready") {
            const preview = await previewDataset(init.dataset_id, 20);
            const schema =
              ds.schema_json?.fields?.map((f) => ({ name: f.name, dtype: f.dtype })) ??
              preview.columns.map((c) => ({ name: c, dtype: "unknown" }));
            const fullPreview = { ...preview, schema };
            setNodes((nds) =>
              nds.map((n) =>
                n.id === nodeId
                  ? {
                      ...n,
                      data: {
                        ...n.data,
                        datasetId: init.dataset_id,
                        uploadStatus: "ready",
                        uploadProgress: 100,
                        rowCount: ds.row_count,
                        sizeBytes: ds.raw_size_bytes,
                        preview: fullPreview,
                      },
                    }
                  : n,
              ),
            );
            setPreview(fullPreview);
            return;
          }
          if (ds.status === "failed") {
            throw new Error(ds.error || "Ingest failed");
          }
          await new Promise((r) => setTimeout(r, 800));
          return poll();
        };

        await poll();
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Upload failed";
        setNodes((nds) =>
          nds.map((n) =>
            n.id === nodeId
              ? {
                  ...n,
                  data: {
                    ...n.data,
                    uploadStatus: "failed",
                    error: msg,
                  },
                }
              : n,
          ),
        );
      }
    },
    [setNodes, setPreview],
  );

  const onWriteConfigChange = useCallback(
    (nodeId: string, config: WriteConfig) => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId ? { ...n, data: { ...n.data, writeConfig: config } } : n,
        ),
      );
    },
    [setNodes],
  );

  const onPickOutput = useCallback(
    async (nodeId: string) => {
      try {
        const picker = (window as any).showDirectoryPicker;
        if (!picker) {
          alert("Folder picker is not supported in this browser.");
          return;
        }
        const dirHandle = await picker.call(window);
        outputDirHandlesRef.current[nodeId] = dirHandle;
        setNodes((nds) =>
          nds.map((n) =>
            n.id === nodeId
              ? {
                  ...n,
                  data: {
                    ...n.data,
                    writeConfig: {
                      ...(n.data?.writeConfig as WriteConfig),
                      outputDirName: dirHandle?.name ?? "selected-folder",
                      destination: "local",
                    },
                  },
                }
              : n,
          ),
        );
      } catch {
        // user cancelled picker
      }
    },
    [setNodes],
  );

  const onPromptChange = useCallback(
    (nodeId: string, prompt: string) => {
      setNodes((nds) =>
        nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, prompt } } : n)),
      );
    },
    [setNodes],
  );

  const onConfigChange = useCallback(
    (nodeId: string, config: Record<string, unknown>) => {
      setNodes((nds) =>
        nds.map((n) => (n.id === nodeId ? { ...n, data: { ...n.data, config } } : n)),
      );
    },
    [setNodes],
  );

  const onModeChange = useCallback(
    (nodeId: string, mode: NodeMode) => {
      setNodes((nds) =>
        nds.map((n) => {
          if (n.id !== nodeId) return n;
          const spec = getOpSpec(nodeKind(n));
          const cfg = (n.data?.config as Record<string, unknown>) ?? {};
          const config =
            mode === "configure" && spec && Object.keys(cfg).length === 0 ? defaultConfig(spec) : cfg;
          return { ...n, data: { ...n.data, mode, config } };
        }),
      );
    },
    [setNodes],
  );

  const runExport = useCallback(
    async (datasetId: string, opts?: { writeNode?: Node | null }) => {
      const writeNode =
        opts?.writeNode ?? nodes.find((n) => n.type === "write") ?? null;
      if (!writeNode) {
        throw new Error("Add a Write node to export output files.");
      }
      const cfg = (writeNode.data?.writeConfig as WriteConfig) || {};
      const format = cfg.format ?? "parquet";
      const fileStem = (
        cfg.fileName || `${slugifyBoardName(boardName)}-output`
      ).replace(/\.[^.]+$/, "");
      const ext = format === "txt" ? "txt" : format;

      setExporting(true);
      try {
        const blob = await exportDataset(datasetId, format);
        if (cfg.destination === "local") {
          const dirHandle = outputDirHandlesRef.current[writeNode.id];
          if (dirHandle && dirHandle.getFileHandle) {
            const fileHandle = await dirHandle.getFileHandle(`${fileStem}.${ext}`, {
              create: true,
            });
            const writable = await fileHandle.createWritable();
            await writable.write(blob);
            await writable.close();
          } else if ((window as any).showSaveFilePicker) {
            const fh = await (window as any).showSaveFilePicker({
              suggestedName: `${fileStem}.${ext}`,
            });
            const writable = await fh.createWritable();
            await writable.write(blob);
            await writable.close();
          } else {
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `${fileStem}.${ext}`;
            a.click();
            URL.revokeObjectURL(a.href);
          }
        } else {
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = `${fileStem}.${ext}`;
          a.click();
          URL.revokeObjectURL(a.href);
        }
      } finally {
        setExporting(false);
      }
    },
    [nodes, boardName],
  );

  // ---- Run pipeline (POST /jobs + SSE) -----------------------------------
  const closeSse = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
    }
  }, []);

  useEffect(() => () => closeSse(), [closeSse]);

  const handleStop = useCallback(async () => {
    if (!jobId) return;
    try {
      await cancelJob(jobId);
    } catch {
      // ignore cancel API errors, still close local stream
    } finally {
      closeSse();
      setRunStatus("error");
      setRunError("Pipeline stopped by user.");
    }
  }, [jobId, closeSse, setRunError, setRunStatus]);

  useEffect(() => {
    if (runStatus !== "running" || !jobId) return;
    const id = setInterval(async () => {
      const idleMs = Date.now() - lastEventAtRef.current;
      if (idleMs < 120000) return; // fail-fast after 2m of no progress events
      try {
        await cancelJob(jobId);
      } catch {
        // ignore
      }
      closeSse();
      setRunStatus("error");
      setRunError("Fail-fast: no pipeline progress for 2 minutes. Job cancelled.");
    }, 10000);
    return () => clearInterval(id);
  }, [runStatus, jobId, closeSse, setRunError, setRunStatus]);

  const handleRun = useCallback(async () => {
    const preflight = preflightPipeline(nodes, edges, nodeSchemas);
    setPreflightIssues(preflight.issues);
    if (!preflight.ok) {
      const first = preflight.issues[0];
      setRunStatus("error");
      setRunError(`${first.message}${first.suggestion ? ` Suggestion: ${first.suggestion}` : ""}`);
      setSelectedNodeId(first.nodeId === "pipeline" ? null : first.nodeId);
      return;
    }

    // Validate
    const sourceNodes = nodes.filter((n) => n.type === "source");
    if (!sourceNodes.length || sourceNodes.some((n) => !n.data?.datasetId)) {
      alert("Add a Source node and upload a file first.");
      return;
    }
    const opNodes = nodes.filter((n) => n.type !== "source" && n.type !== "write");
    if (!opNodes.length) {
      alert("Add at least one operation.");
      return;
    }
    const missingPrompt = opNodes.find(
      (n) => nodeMode(n) === "prompt" && !((n.data?.prompt as string) || "").trim(),
    );
    if (missingPrompt) {
      alert(`Add a prompt for "${missingPrompt.data?.label}".`);
      return;
    }

    closeSse();
    resetRun();
    lastExportedJobRef.current = null;
    setRunStatus("running");
    lastEventAtRef.current = Date.now();

    const orderedIds = topoSort(
      nodes.map((n) => n.id),
      edges,
    );
    const nodeMap = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const stepList: StepState[] = orderedIds.map((id) => ({
      id,
      label: (nodeMap[id]?.data?.label as string) || String(nodeMap[id]?.type),
      status: "pending",
    }));
    setSteps(stepList);

    const definition = buildPipelineDefinition(nodes, edges, enginePref);

    let createdJobId: string | null = null;
    try {
      const job = await createJob(definition);
      createdJobId = job.id;
      setJobId(job.id);

      // Open SSE. Backend emits named events (`event: step_started`, etc.)
      // so we listen to each kind explicitly. `EventSource` only routes
      // unnamed events to `onmessage`.
      const es = new EventSource(jobEventsUrl(job.id, 0));
      esRef.current = es;

      const handle = (msg: MessageEvent) => {
        try {
          lastEventAtRef.current = Date.now();
          const ev = JSON.parse(msg.data);
          const finished = applyJobEvent(ev, {
            updateStep,
            setEngine,
            setRunStatus,
            setRunError,
            setResult,
          });
          if (finished) {
            closeSse();
          }
        } catch (e) {
          console.warn("Bad SSE payload", e);
        }
      };
      const KINDS = [
        "engine_selected",
        "job_started",
        "step_started",
        "step_completed",
        "result_dataset_ready",
        "job_succeeded",
        "job_failed",
        "job_cancelled",
      ];
      for (const k of KINDS) es.addEventListener(k, handle);
      es.onmessage = handle; // for any future un-named events
      es.onerror = () => {
        // Browser auto-reconnects. The server closes cleanly when the job
        // finishes; we already close manually on terminal events above.
      };
    } catch (err) {
      setRunError(err instanceof Error ? err.message : "Failed to start job");
      setRunStatus("error");
      if (createdJobId) {
        try {
          await cancelJob(createdJobId);
        } catch {
          // ignore
        }
      }
    }
  }, [
    nodes,
    edges,
    nodeSchemas,
    enginePref,
    closeSse,
    resetRun,
    setRunStatus,
    setSteps,
    setJobId,
    updateStep,
    setEngine,
    setRunError,
    setResult,
  ]);

  // ---- Once a job succeeds, fetch the output preview ---------------------
  const resultDatasetId = useWorkflowStore((s) => s.resultDatasetId);
  useEffect(() => {
    if (!resultDatasetId || runStatus !== "success") return;
    if (!jobId || lastExportedJobRef.current === jobId) return;
    // Claim this job synchronously BEFORE any await. The effect re-fires when
    // `nodes` changes (we call setNodes below mid-flight), and marking the job
    // exported only after the download finished allowed two overlapping runs
    // to each trigger a download.
    lastExportedJobRef.current = jobId;
    (async () => {
      try {
        const ds = await getDataset(resultDatasetId);
        const prev = await previewDataset(resultDatasetId, 50);
        const schema =
          ds.schema_json?.fields?.map((f) => ({ name: f.name, dtype: f.dtype })) ??
          prev.columns.map((c) => ({ name: c, dtype: "unknown" }));

        // Materialize the write-node's result and propagate to global result.
        setResult(resultDatasetId, { ...prev, schema });
        setNodes((nds) =>
          nds.map((n) =>
            n.type === "write"
              ? { ...n, data: { ...n.data, resultDatasetId } }
              : n,
          ),
        );

        const writeNode = nodes.find((n) => n.type === "write") ?? null;
        const cfg = ((writeNode?.data?.writeConfig as WriteConfig) || {
          autoExport: true,
        }) as WriteConfig;
        if (cfg.autoExport === false) return;
        await runExport(resultDatasetId, { writeNode });
      } catch {
        // Release the claim so a later state change can retry the export.
        if (lastExportedJobRef.current === jobId) {
          lastExportedJobRef.current = null;
        }
      }
    })();
  }, [resultDatasetId, runStatus, setNodes, setResult, nodes, jobId, runExport]);

  const canRun =
    runStatus !== "running" &&
    nodes.some(
      (n) => n.type === "source" && (n.data as any)?.uploadStatus === "ready",
    );
  const canExport =
    runStatus !== "running" &&
    Boolean(resultDatasetId) &&
    nodes.some((n) => n.type === "write");

  // ----------------------------------------------------------------------

  return (
    <div className="relative flex h-screen flex-col overflow-hidden">
      <div className="aurora" />
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="relative z-10"
      >
        <TopBar
          backendOk={backendOk}
          ready={ready}
          engine={engine}
          boardName={boardName}
          onBoardNameChange={setBoardName}
          onRun={handleRun}
          onNew={() => {
            if (!nodes.length || confirm("Start a new workflow? Unsaved layout will be cleared.")) {
              closeSse();
              setNodes([]);
              setEdges([]);
              resetRun();
              setBoardName(DEFAULT_BOARD_NAME);
              lastExportedJobRef.current = null;
            }
          }}
          onSave={() => {
            const payload = JSON.stringify(
              { name: boardName, nodes, edges, edgeType },
              null,
              2,
            );
            localStorage.setItem("prompt-etl.workflow", payload);
            const blob = new Blob([payload], { type: "application/json" });
            const a = document.createElement("a");
            a.href = URL.createObjectURL(blob);
            a.download = `${slugifyBoardName(boardName)}.json`;
            a.click();
            URL.revokeObjectURL(a.href);
          }}
          onOpen={() => {
            const input = document.createElement("input");
            input.type = "file";
            input.accept = ".json,application/json";
            input.onchange = async () => {
              const file = input.files?.[0];
              if (!file) return;
              try {
                const text = await file.text();
                const parsed = JSON.parse(text);
                if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) {
                  throw new Error("Invalid pipeline file");
                }
                setNodes(parsed.nodes);
                setEdges(parsed.edges);
                const openedName =
                  typeof parsed.name === "string" && parsed.name.trim()
                    ? parsed.name.trim()
                    : file.name.replace(/\.json$/i, "");
                setBoardName(openedName);
                if (
                  parsed.edgeType === "smoothstep" ||
                  parsed.edgeType === "step" ||
                  parsed.edgeType === "straight"
                ) {
                  setEdgeType(parsed.edgeType);
                }
                localStorage.setItem(
                  "prompt-etl.workflow",
                  JSON.stringify({
                    name: openedName,
                    nodes: parsed.nodes,
                    edges: parsed.edges,
                    edgeType: parsed.edgeType ?? "smoothstep",
                  }),
                );
              } catch (e) {
                alert(e instanceof Error ? e.message : "Failed to open pipeline file");
              }
            };
            input.click();
          }}
          onStop={handleStop}
          onExportNow={async () => {
            if (!resultDatasetId) {
              alert("Run a pipeline first to produce a result dataset.");
              return;
            }
            try {
              await runExport(resultDatasetId);
            } catch (e) {
              alert(e instanceof Error ? e.message : "Export failed");
            }
          }}
          onSettings={() => setSettingsOpen(true)}
          runStatus={runStatus}
          canRun={canRun}
          canExport={canExport}
          exporting={exporting}
          preflightIssueCount={preflightIssues.length}
        />
      </motion.div>
      <motion.div
        initial={{ opacity: 0, y: -6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0.05, ease: "easeOut" }}
        className="relative z-10"
      >
        <OperationBubbleBar
          onDragStart={onDragStart}
          onAddOperation={onAddOperation}
          edgeType={edgeType}
          onEdgeTypeChange={(t) => {
            setEdgeType(t);
            setEdges((eds) => eds.map((e) => ({ ...e, type: t })));
          }}
        />
      </motion.div>
      <div className="relative z-10 flex-1 min-h-0 px-3 pb-3 pt-2">
        <PanelGroup direction="horizontal" className="h-full">
          <Panel defaultSize={70} minSize={40}>
            <div className="glass relative h-full overflow-hidden rounded-2xl">
              <FlowCanvas
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                edgeType={edgeType}
                onConnect={onConnect}
                onSelectionChange={setSelectedNodeId}
                onSourceFileSelect={onSourceFileSelect}
                onWriteConfigChange={onWriteConfigChange}
                onPickOutput={onPickOutput}
                onRun={handleRun}
                runStatus={runStatus}
                canRun={canRun}
              />
            </div>
          </Panel>
          <PanelResizeHandle className="group mx-2 flex w-1.5 items-center justify-center">
            <div className="h-12 w-1 rounded-full bg-white/10 transition-colors group-hover:bg-primary/60" />
          </PanelResizeHandle>
          <Panel defaultSize={30} minSize={22}>
            <div className="glass h-full overflow-hidden rounded-2xl">
              <InspectorPanel
                selectedNode={selectedNode}
                onPromptChange={onPromptChange}
                onConfigChange={onConfigChange}
                onModeChange={onModeChange}
                inputColumns={inputColumns}
                preflightIssues={preflightIssues}
              />
            </div>
          </Panel>
        </PanelGroup>
      </div>
      <AnimatePresence>
        {steps.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 16 }}
            transition={{ duration: 0.25 }}
            className="relative z-10 px-3 pb-3"
          >
            <StepTimeline steps={steps} />
          </motion.div>
        )}
      </AnimatePresence>
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        enginePref={enginePref}
        onEnginePrefChange={setEnginePref}
        apiBaseUrl={apiBaseUrl}
        onApiBaseUrlChange={(v) => {
          setApiBaseUrl(v);
          setApiBaseUrlState(getApiBaseUrl());
          setBackendOk(false);
        }}
        onAccessTokenSaved={() => {
          useCatalogStore.setState({ catalog: null, error: null });
          if (getAccessToken()) void loadCatalog();
        }}
      />
      {jobId && runStatus === "running" && (
        <div className="pointer-events-none fixed bottom-3 left-3 z-20 text-[10px] font-mono text-muted-foreground/70">
          job {jobId.slice(0, 8)}…
        </div>
      )}
    </div>
  );
}
