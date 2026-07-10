import { memo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Download, CheckCircle2, FolderOpen } from "lucide-react";
import { cn } from "@/lib/utils";
import { STATUS_GLOW, useNodeRunStatus } from "@/lib/nodeStatus";
import { StatusBadge } from "./StatusBadge";

export interface WriteConfig {
  datasetName?: string;
  format?: "parquet" | "csv" | "json" | "txt";
  destination?: "download" | "local";
  fileName?: string;
  outputDirName?: string;
  autoExport?: boolean;
}

export interface WriteData {
  writeConfig?: WriteConfig;
  resultDatasetId?: string | null;
  onWriteConfigChange?: (config: WriteConfig) => void;
  onPickOutput?: () => void;
}

function WriteNodeComponent({ id, data, selected }: NodeProps) {
  const d = data as unknown as WriteData;
  const writeConfig = d.writeConfig ?? {
    datasetName: "",
    format: "parquet",
    destination: "download",
    fileName: "prompt-etl-output",
    autoExport: true,
  };
  const onWriteConfigChange = d.onWriteConfigChange;
  const onPickOutput = d.onPickOutput;

  const status = useNodeRunStatus(id);
  const statusGlow = STATUS_GLOW[status];
  const isRunning = status === "running";

  const baseShadow = selected
    ? "0 0 0 1px rgba(74,222,128,0.6), 0 18px 40px -14px rgba(34,197,94,0.55), inset 0 1px 0 rgba(255,255,255,0.1)"
    : "0 14px 32px -16px rgba(34,197,94,0.5), inset 0 1px 0 rgba(255,255,255,0.08)";

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92 }}
      animate={{
        opacity: 1,
        scale: 1,
        boxShadow: statusGlow || baseShadow,
      }}
      transition={{
        type: "spring",
        stiffness: 220,
        damping: 20,
        boxShadow: isRunning
          ? { duration: 1.4, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" }
          : { duration: 0.45 },
      }}
      whileHover={{ y: -2 }}
      className={cn(
        "relative flex min-w-[220px] flex-col items-center gap-2 rounded-3xl px-5 py-4",
      )}
      style={{
        background:
          "linear-gradient(155deg, rgba(34,197,94,0.28) 0%, rgba(16,185,129,0.18) 45%, rgba(15,17,32,0.85) 100%)",
        border: `1px solid rgba(74,222,128,${selected ? 0.7 : 0.4})`,
        backdropFilter: "blur(20px) saturate(170%)",
        WebkitBackdropFilter: "blur(20px) saturate(170%)",
      }}
    >
      <StatusBadge status={status} />
      <Handle type="target" position={Position.Top} />
      <div
        className="flex h-12 w-12 items-center justify-center rounded-2xl"
        style={{
          background: "linear-gradient(135deg, rgba(74,222,128,0.45), rgba(16,185,129,0.25))",
          border: "1px solid rgba(74,222,128,0.45)",
          boxShadow: "inset 0 1px 0 rgba(255,255,255,0.15), 0 6px 16px -6px rgba(34,197,94,0.6)",
        }}
      >
        <Download className="h-5 w-5 text-emerald-100" strokeWidth={2.2} />
      </div>
      <div className="text-center">
        <div className="text-[11px] font-semibold text-foreground">Write</div>
        <div className="text-[9px] font-medium uppercase tracking-[0.16em] text-emerald-300/80">
          Materialize
        </div>
      </div>

      <AnimatePresence initial={false}>
        {selected && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="flex w-full flex-col gap-2"
          >
            <div className="flex flex-col gap-1">
              <p className="text-[9px] font-semibold uppercase tracking-[0.14em] text-emerald-200/80">
                Output dataset name
              </p>
              <input
                value={writeConfig.datasetName ?? ""}
                onChange={(e) =>
                  onWriteConfigChange?.({ ...writeConfig, datasetName: e.target.value })
                }
                placeholder="my-pipeline-output"
                className="glass-input rounded-lg px-2 py-1 text-[11px] font-mono"
              />
            </div>

            <div className="flex flex-col gap-1">
              <p className="text-[9px] font-semibold uppercase tracking-[0.14em] text-emerald-200/80">
                Output file format
              </p>
              <div className="flex flex-wrap gap-1">
                {(["parquet", "csv", "json", "txt"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => onWriteConfigChange?.({ ...writeConfig, format: f })}
                    className={cn(
                      "rounded-full px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider",
                      writeConfig.format === f
                        ? "bg-gradient-to-r from-emerald-500 to-teal-500 text-white"
                        : "border border-white/10 bg-white/[0.03] text-foreground/80",
                    )}
                  >
                    {f}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <p className="text-[9px] font-semibold uppercase tracking-[0.14em] text-emerald-200/80">
                Destination
              </p>
              <div className="flex gap-1">
                <button
                  onClick={() =>
                    onWriteConfigChange?.({ ...writeConfig, destination: "download" })
                  }
                  className={cn(
                    "rounded-full px-2 py-0.5 text-[9px] font-semibold",
                    writeConfig.destination === "download"
                      ? "bg-gradient-to-r from-emerald-500 to-teal-500 text-white"
                      : "border border-white/10 bg-white/[0.03] text-foreground/80",
                  )}
                >
                  Browser download
                </button>
                <button
                  onClick={() => onWriteConfigChange?.({ ...writeConfig, destination: "local" })}
                  className={cn(
                    "rounded-full px-2 py-0.5 text-[9px] font-semibold",
                    writeConfig.destination === "local"
                      ? "bg-gradient-to-r from-emerald-500 to-teal-500 text-white"
                      : "border border-white/10 bg-white/[0.03] text-foreground/80",
                  )}
                >
                  Local folder
                </button>
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <p className="text-[9px] font-semibold uppercase tracking-[0.14em] text-emerald-200/80">
                Output file name
              </p>
              <input
                value={writeConfig.fileName ?? ""}
                onChange={(e) =>
                  onWriteConfigChange?.({ ...writeConfig, fileName: e.target.value })
                }
                placeholder="orders_2025_cleaned"
                className="glass-input rounded-lg px-2 py-1 text-[11px] font-mono"
              />
              {writeConfig.destination === "local" && (
                <button
                  onClick={() => onPickOutput?.()}
                  className="flex items-center justify-center gap-1 rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] text-foreground/85"
                >
                  <FolderOpen className="h-3 w-3" />
                  {writeConfig.outputDirName
                    ? `Folder: ${writeConfig.outputDirName}`
                    : "Pick local folder"}
                </button>
              )}
              <p className="text-[9px] italic text-muted-foreground">
                Writes a dataset in object storage, then exports in selected format.
              </p>
            </div>

            <label className="flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.03] px-2 py-1.5 text-[10px] text-foreground/85">
              <input
                type="checkbox"
                checked={writeConfig.autoExport !== false}
                onChange={(e) =>
                  onWriteConfigChange?.({ ...writeConfig, autoExport: e.target.checked })
                }
                className="h-3.5 w-3.5 rounded border-white/20 bg-transparent"
              />
              Auto-export after successful run
            </label>
          </motion.div>
        )}
      </AnimatePresence>

      {d.resultDatasetId && (
        <div className="flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[9px] font-medium text-emerald-300">
          <CheckCircle2 className="h-2.5 w-2.5" />
          <span className="max-w-[140px] truncate font-mono">
            {d.resultDatasetId.slice(0, 8)}…
          </span>
        </div>
      )}
    </motion.div>
  );
}

export const WriteNode = memo(WriteNodeComponent);
