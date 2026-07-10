import { motion, AnimatePresence } from "framer-motion";
import type { Node } from "@xyflow/react";
import {
  Code2,
  Sparkles,
  AlertCircle,
  MousePointer2,
  Workflow,
  ShieldAlert,
  Cpu,
} from "lucide-react";
import { getOperationById } from "@/lib/operations";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { useWorkflowStore } from "@/stores/workflowStore";
import { hexToRgba } from "@/lib/utils";
import type { PreflightIssue } from "@/lib/preflight";
import { SchemaPanel } from "./SchemaPanel";

interface InspectorPanelProps {
  selectedNode: Node | null;
  onPromptChange: (nodeId: string, prompt: string) => void;
  preflightIssues: PreflightIssue[];
}

export function InspectorPanel({
  selectedNode,
  onPromptChange,
  preflightIssues,
}: InspectorPanelProps) {
  const {
    resultPreview,
    preview,
    runError,
    steps,
    showCode,
    setShowCode,
    engine,
    engineReason,
  } = useWorkflowStore();
  const selectedStep = selectedNode ? steps.find((s) => s.id === selectedNode.id) : null;
  const selectedIssues = selectedNode
    ? preflightIssues.filter((issue) => issue.nodeId === selectedNode.id)
    : [];

  if (!selectedNode) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center gap-2 border-b border-white/5 px-5 py-3.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-violet-500/30 to-cyan-500/20">
            <MousePointer2 className="h-3.5 w-3.5 text-violet-300" />
          </div>
          <div>
            <h2 className="text-[13px] font-semibold tracking-tight">Inspector</h2>
            <p className="text-[10px] text-muted-foreground">
              Select a node to edit its prompt
            </p>
          </div>
        </div>
        <div className="scrollbar-thin flex-1 overflow-auto px-5 py-4 space-y-5">
          {preflightIssues.length > 0 && (
            <div className="rounded-xl border border-rose-400/35 bg-rose-500/[0.08] p-3 text-[11px] text-rose-100">
              <div className="flex items-center gap-2 font-semibold">
                <AlertCircle className="h-3.5 w-3.5 text-rose-300" />
                Preflight found {preflightIssues.length} issue
                {preflightIssues.length === 1 ? "" : "s"}
              </div>
              <div className="mt-2 space-y-1.5 text-[10.5px] text-rose-100/90">
                {preflightIssues.slice(0, 4).map((issue, idx) => (
                  <div key={`${issue.nodeId}-${idx}`}>
                    {issue.label}: {issue.message}
                  </div>
                ))}
                {preflightIssues.length > 4 && (
                  <div className="text-rose-200/80">
                    ...and {preflightIssues.length - 4} more.
                  </div>
                )}
              </div>
            </div>
          )}
          {engine && (
            <div className="flex items-start gap-2 rounded-xl border border-violet-400/25 bg-violet-400/[0.06] p-3 text-[11px] text-violet-100">
              <Cpu className="mt-0.5 h-3.5 w-3.5 shrink-0 text-violet-300" />
              <div>
                <div className="font-semibold">Engine: {engine}</div>
                {engineReason && (
                  <div className="mt-0.5 text-[10.5px] text-violet-200/80">
                    {engineReason}
                  </div>
                )}
              </div>
            </div>
          )}
          {preview ? (
            <SchemaPanel preview={preview} title="Loaded dataset" />
          ) : (
            <p className="text-[11px] italic text-muted-foreground/70">
              Upload data via the Source bubble to see its schema here.
            </p>
          )}
          {resultPreview && (
            <div className="border-t border-white/5 pt-4">
              <SchemaPanel preview={resultPreview} title="Pipeline output" />
            </div>
          )}
        </div>
      </div>
    );
  }

  const op = getOperationById((selectedNode.data?.operationId as string) ?? "");
  const color = (selectedNode.data?.color as string) || op?.color || "#a78bfa";
  const icon = (selectedNode.data?.icon as string) || op?.icon || "✨";
  const label = (selectedNode.data?.label as string) || op?.label || String(selectedNode.type);
  const promptValue = (selectedNode.data?.prompt as string) ?? "";
  const isWrite = selectedNode.type === "write";
  const isSource = selectedNode.type === "source";
  const isOperation = !isWrite && !isSource;

  const schemaPreview =
    isSource && selectedNode.data?.preview
      ? (selectedNode.data?.preview as any)
      : selectedStep?.preview ??
        (resultPreview && selectedNode.id === steps[steps.length - 1]?.id ? resultPreview : null);

  const isRefusal =
    selectedStep?.status === "error" &&
    !!runError &&
    runError.toLowerCase().startsWith("agent refused");

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <motion.div
        key={selectedNode.id}
        initial={{ opacity: 0, y: -6 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center gap-3 border-b border-white/5 px-5 py-3.5"
      >
        <div
          className="flex h-9 w-9 items-center justify-center rounded-xl text-base"
          style={{
            background: `linear-gradient(135deg, ${hexToRgba(color, 0.45)}, ${hexToRgba(color, 0.15)})`,
            border: `1px solid ${hexToRgba(color, 0.4)}`,
            boxShadow: `inset 0 1px 0 rgba(255,255,255,0.15), 0 4px 12px -4px ${hexToRgba(color, 0.55)}`,
          }}
        >
          {icon}
        </div>
        <div className="flex-1">
          <h2 className="text-[13px] font-semibold tracking-tight">{label}</h2>
          {op && <p className="text-[10px] text-muted-foreground">{op.description}</p>}
        </div>
        {selectedStep && (
          <span
            className={
              selectedStep.status === "done"
                ? "chip text-emerald-300"
                : selectedStep.status === "running"
                ? "chip text-amber-300"
                : selectedStep.status === "error"
                ? "chip text-rose-300"
                : "chip text-muted-foreground"
            }
          >
            {selectedStep.status}
          </span>
        )}
      </motion.div>

      <div className="scrollbar-thin flex-1 space-y-4 overflow-auto px-5 py-4">
        {isOperation && op && (
          <div
            className="flex items-center gap-2 rounded-xl border px-3 py-2 text-[10.5px]"
            style={{
              background: `linear-gradient(135deg, ${hexToRgba(color, 0.12)}, ${hexToRgba(color, 0.04)})`,
              borderColor: hexToRgba(color, 0.3),
            }}
          >
            <Workflow className="h-3.5 w-3.5" style={{ color }} />
            <span className="text-foreground/85">
              Agent scope:&nbsp;
              <span className="font-semibold">{op.label}</span> only · prompts outside this
              scope are refused.
            </span>
          </div>
        )}

        {selectedIssues.length > 0 && (
          <div className="rounded-xl border border-rose-400/35 bg-rose-500/[0.08] p-3 text-[11px] text-rose-100">
            <div className="flex items-center gap-2 font-semibold">
              <AlertCircle className="h-3.5 w-3.5 text-rose-300" />
              Preflight issues on this node
            </div>
            <div className="mt-2 space-y-2">
              {selectedIssues.map((issue, idx) => (
                <div key={`${issue.nodeId}-${idx}`} className="rounded-lg bg-black/10 p-2">
                  <div>{issue.message}</div>
                  {issue.suggestion && (
                    <div className="mt-1 text-[10.5px] text-rose-100/85">
                      Suggestion: {issue.suggestion}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {isOperation && (
          <div className="flex flex-col gap-2">
            <label className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
              <Sparkles className="h-3 w-3 text-violet-400" />
              Natural language prompt
            </label>
            <Textarea
              value={promptValue}
              onChange={(e) => onPromptChange(selectedNode.id, e.target.value)}
              placeholder={op?.presetPrompt || "Describe what to do with the data…"}
              rows={6}
              className="min-h-[130px] resize-none font-mono text-[12px] leading-relaxed text-foreground/95"
              style={{ fontFamily: "'JetBrains Mono', ui-monospace, monospace" }}
            />
            <p className="text-[10px] italic text-muted-foreground">
              Mention column names. Keep the request focused on {op?.label.toLowerCase()}.
            </p>

            <AnimatePresence>
              {isRefusal && (
                <motion.div
                  initial={{ opacity: 0, y: -4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  className="flex items-start gap-2 rounded-xl border border-amber-400/30 bg-amber-400/[0.08] p-3 text-[11px] text-amber-100"
                >
                  <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300" />
                  <div>
                    <div className="font-semibold">Agent refused</div>
                    <div className="mt-0.5 text-[10.5px] text-amber-100/85">{runError}</div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            <AnimatePresence>
              {selectedStep?.generatedCode && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  className="overflow-hidden"
                >
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setShowCode(!showCode)}
                    className="-ml-2 text-foreground/80"
                  >
                    <Code2 className="h-3 w-3" />
                    {showCode ? "Hide" : "Show"} generated code
                  </Button>
                  <AnimatePresence>
                    {showCode && (
                      <motion.pre
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: "auto" }}
                        exit={{ opacity: 0, height: 0 }}
                        className="scrollbar-thin mt-2 max-h-44 overflow-auto rounded-xl border border-white/5 bg-black/40 p-3 text-[10px] leading-relaxed text-emerald-200/90"
                        style={{ fontFamily: "'JetBrains Mono', ui-monospace, monospace" }}
                      >
                        {selectedStep.generatedCode}
                      </motion.pre>
                    )}
                  </AnimatePresence>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {isSource && (
          <p className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-[11px] leading-relaxed text-muted-foreground/85">
            Click the Source node on the canvas and choose a <strong>CSV</strong>,{" "}
            <strong>JSON</strong>, <strong>Excel</strong>, or <strong>Parquet</strong> file.
            The file uploads directly to object storage via presigned URLs and gets converted
            to Parquet automatically.
          </p>
        )}
        {isWrite && (
          <p className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-[11px] leading-relaxed text-muted-foreground/85">
            Configure an output dataset name on the Write node. The result is materialized as
            a versioned Parquet dataset in object storage.
          </p>
        )}

        {schemaPreview ? (
          <div className="border-t border-white/5 pt-4">
            <SchemaPanel
              preview={schemaPreview}
              title={
                isSource
                  ? "Loaded dataset"
                  : selectedStep?.status === "done"
                  ? "After this step"
                  : "Step preview"
              }
            />
          </div>
        ) : (
          isOperation && (
            <div className="rounded-xl border border-white/5 bg-white/[0.02] p-3 text-[10.5px] italic text-muted-foreground/70">
              Run the pipeline to see schema and head after this step.
            </div>
          )
        )}

        {runError && !isRefusal && (
          <div className="flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-[11px] text-rose-200">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <div>{runError}</div>
          </div>
        )}
      </div>
    </div>
  );
}
