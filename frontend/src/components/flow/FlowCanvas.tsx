import { useCallback, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ConnectionLineType,
  addEdge,
  type Connection,
  type Node,
  type Edge,
  type OnNodesChange,
  type OnEdgesChange,
} from "@xyflow/react";
import { motion, AnimatePresence } from "framer-motion";
import { Play, Loader2, CheckCircle2, AlertCircle, MousePointerClick } from "lucide-react";
import { cn } from "@/lib/utils";
import { SourceNode } from "./SourceNode";
import { OperationNode } from "./OperationNode";
import { WriteNode, type WriteConfig } from "./WriteNode";

const nodeTypes = {
  source: SourceNode,
  operation: OperationNode,
  dashboard: OperationNode,
  write: WriteNode,
};

const CONNECTION_MAP: Record<string, ConnectionLineType> = {
  straight: ConnectionLineType.Straight,
  step: ConnectionLineType.Step,
  smoothstep: ConnectionLineType.SmoothStep,
};

interface FlowCanvasProps {
  nodes: Node[];
  edges: Edge[];
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  edgeType: string;
  onConnect: (connection: Connection) => void;
  onSelectionChange?: (nodeId: string | null) => void;
  onSourceFileSelect: (nodeId: string, file: File) => void;
  onWriteConfigChange: (nodeId: string, config: WriteConfig) => void;
  onPickOutput: (nodeId: string) => void;
  onRun: () => void;
  runStatus: string;
  canRun: boolean;
}

export function FlowCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  edgeType,
  onConnect: onConnectProp,
  onSelectionChange,
  onSourceFileSelect,
  onWriteConfigChange,
  onPickOutput,
  onRun,
  runStatus,
  canRun,
}: FlowCanvasProps) {
  const rfRef = useRef<{
    screenToFlowPosition: (p: { x: number; y: number }) => { x: number; y: number };
  } | null>(null);
  const [dragActive, setDragActive] = useState(false);

  const nodesWithHandlers = nodes.map((n) => ({
    ...n,
    data: {
      ...n.data,
      onFileSelect:
        n.type === "source" ? (file: File) => onSourceFileSelect(n.id, file) : undefined,
      onWriteConfigChange:
        n.type === "write"
          ? (config: WriteConfig) => onWriteConfigChange(n.id, config)
          : undefined,
      onPickOutput:
        n.type === "write"
          ? () => onPickOutput(n.id)
          : undefined,
    },
  }));

  const onConnect = useCallback(
    (params: Connection) => onConnectProp(params),
    [onConnectProp],
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    if (e.dataTransfer.types.includes("application/prompt-etl-operation")) {
      setDragActive(true);
    }
  }, []);

  const onDragLeave = useCallback((e: React.DragEvent) => {
    if (e.currentTarget === e.target) setDragActive(false);
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const operationId = e.dataTransfer.getData("application/prompt-etl-operation");
    if (!operationId || !rfRef.current) return;
    const position = rfRef.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    window.dispatchEvent(
      new CustomEvent("prompt-etl-add-operation", { detail: { operationId, position } }),
    );
  }, []);

  const empty = nodes.length === 0;

  return (
    <div className="relative flex h-full flex-col">
      <div
        className={cn("relative flex-1 min-h-0", dragActive && "canvas-drop-active")}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <ReactFlow
          nodes={nodesWithHandlers}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onInit={(inst) => {
            rfRef.current = inst;
          }}
          nodeTypes={nodeTypes}
          onNodeClick={(_, node) => onSelectionChange?.(node.id)}
          onPaneClick={() => onSelectionChange?.(null)}
          connectionLineType={CONNECTION_MAP[edgeType] ?? ConnectionLineType.SmoothStep}
          defaultEdgeOptions={{ type: edgeType, animated: true }}
          fitView
          proOptions={{ hideAttribution: true }}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={24}
            size={1.2}
            color="rgba(180, 200, 255, 0.18)"
          />
          <Controls showInteractive={false} />
          <MiniMap
            nodeColor={(n) => (n.data?.color as string) || "#a78bfa"}
            maskColor="rgba(7, 9, 27, 0.65)"
            pannable
            zoomable
          />
        </ReactFlow>

        <AnimatePresence>
          {empty && (
            <motion.div
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0 }}
              className="pointer-events-none absolute inset-0 flex items-center justify-center"
            >
              <div className="glass max-w-md rounded-3xl px-7 py-6 text-center">
                <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500/30 to-cyan-500/30">
                  <MousePointerClick className="h-5 w-5 text-violet-200" />
                </div>
                <h3
                  className="gradient-text text-lg font-bold tracking-tight"
                  style={{ fontFamily: "'Space Grotesk', sans-serif" }}
                >
                  Start your pipeline
                </h3>
                <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                  Drag an operation from the bar above onto this canvas, or click any bubble
                  to add it. Fill in each box's settings, or describe the step in plain
                  English with a prompt.
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="flex items-center justify-between border-t border-white/5 bg-white/[0.02] px-5 py-2.5 backdrop-blur-xl">
        <div className="flex items-center gap-2 text-[11px]">
          {runStatus === "running" && (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin text-violet-400" />
              <span className="font-medium text-violet-300">Running pipeline…</span>
            </>
          )}
          {runStatus === "success" && (
            <>
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
              <span className="font-medium text-emerald-300">Pipeline completed</span>
            </>
          )}
          {runStatus === "error" && (
            <>
              <AlertCircle className="h-3.5 w-3.5 text-rose-400" />
              <span className="font-medium text-rose-300">Pipeline failed</span>
            </>
          )}
          {runStatus === "idle" && (
            <span className="text-muted-foreground">
              {nodes.length === 0
                ? "Drag an operation to begin"
                : "Connect nodes and press Run"}
            </span>
          )}
        </div>
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.97 }}
          onClick={onRun}
          disabled={!canRun || runStatus === "running"}
          className={cn(
            "group relative flex items-center gap-2 overflow-hidden rounded-full px-6 py-2 text-xs font-semibold text-white shadow-[0_8px_24px_-6px_rgba(168,85,247,0.5)] transition-all disabled:opacity-50",
            "bg-gradient-to-r from-violet-500 via-fuchsia-500 to-cyan-500",
          )}
        >
          <span className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/25 to-transparent transition-transform duration-700 group-hover:translate-x-full" />
          {runStatus === "running" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Play className="h-3.5 w-3.5 fill-white" />
          )}
          Run pipeline
        </motion.button>
      </div>
    </div>
  );
}

export { addEdge };
