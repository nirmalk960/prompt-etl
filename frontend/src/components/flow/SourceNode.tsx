import { memo, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  FolderOpen,
  CheckCircle2,
  Upload,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { STATUS_GLOW, useNodeRunStatus } from "@/lib/nodeStatus";
import { StatusBadge } from "./StatusBadge";

export interface SourceData {
  datasetId?: string | null;
  filename?: string;
  uploadStatus?: "idle" | "uploading" | "ingesting" | "ready" | "failed";
  uploadProgress?: number;          // 0..100
  rowCount?: number | null;
  sizeBytes?: number | null;
  error?: string | null;
  onFileSelect?: (file: File) => void;
}

function fmtBytes(n: number | null | undefined): string {
  if (n == null) return "";
  const u = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 2 : v < 100 ? 1 : 0)} ${u[i]}`;
}

function SourceNodeComponent({ id, data, selected }: NodeProps) {
  const d = data as unknown as SourceData;
  const inputRef = useRef<HTMLInputElement>(null);
  const status = useNodeRunStatus(id);
  const statusGlow = STATUS_GLOW[status];
  const isRunning = status === "running";
  const uploadStatus = d.uploadStatus ?? "idle";

  const baseShadow = selected
    ? "0 0 0 1px rgba(96,165,250,0.6), 0 18px 40px -14px rgba(59,130,246,0.55), inset 0 1px 0 rgba(255,255,255,0.1)"
    : "0 14px 32px -16px rgba(59,130,246,0.5), inset 0 1px 0 rgba(255,255,255,0.08)";

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
        "relative flex min-w-[200px] flex-col items-center gap-2 rounded-3xl px-5 py-4",
      )}
      style={{
        background:
          "linear-gradient(155deg, rgba(59,130,246,0.28) 0%, rgba(14,165,233,0.18) 45%, rgba(15,17,32,0.85) 100%)",
        border: `1px solid rgba(96,165,250,${selected ? 0.7 : 0.4})`,
        backdropFilter: "blur(20px) saturate(170%)",
        WebkitBackdropFilter: "blur(20px) saturate(170%)",
      }}
    >
      <StatusBadge status={status} />
      <Handle type="source" position={Position.Bottom} />

      <div
        className="flex h-12 w-12 items-center justify-center rounded-2xl"
        style={{
          background:
            "linear-gradient(135deg, rgba(96,165,250,0.45), rgba(14,165,233,0.25))",
          border: "1px solid rgba(96,165,250,0.45)",
          boxShadow:
            "inset 0 1px 0 rgba(255,255,255,0.15), 0 6px 16px -6px rgba(59,130,246,0.6)",
        }}
      >
        <FolderOpen className="h-5 w-5 text-blue-100" strokeWidth={2.2} />
      </div>

      <div className="text-center">
        <div className="text-[11px] font-semibold text-foreground">Source</div>
        <div className="text-[9px] font-medium uppercase tracking-[0.16em] text-blue-300/80">
          Data input
        </div>
      </div>

      <AnimatePresence>
        {selected && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            className="flex w-full flex-col items-center gap-1.5"
          >
            <input
              ref={inputRef}
              type="file"
              accept=".csv,.json,.xlsx,.xls,.parquet"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) d.onFileSelect?.(file);
                e.target.value = "";
              }}
            />
            <button
              onClick={() => inputRef.current?.click()}
              disabled={uploadStatus === "uploading" || uploadStatus === "ingesting"}
              className="flex items-center gap-1.5 rounded-full bg-gradient-to-r from-blue-500 to-cyan-500 px-3 py-1 text-[10px] font-semibold text-white shadow-[0_4px_14px_-4px_rgba(59,130,246,0.6)] hover:shadow-[0_6px_18px_-4px_rgba(59,130,246,0.8)] transition-shadow disabled:opacity-60"
            >
              <Upload className="h-3 w-3" />
              {d.filename ? "Replace file" : "Choose file"}
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {uploadStatus === "uploading" && (
        <div className="flex w-full flex-col items-center gap-1">
          <div className="flex items-center gap-1 rounded-full bg-sky-500/15 px-2 py-0.5 text-[9px] font-medium text-sky-200">
            <Loader2 className="h-2.5 w-2.5 animate-spin" />
            Uploading {d.uploadProgress ?? 0}%
          </div>
          <div className="h-1 w-full overflow-hidden rounded-full bg-white/10">
            <div
              className="h-full bg-gradient-to-r from-blue-400 to-cyan-300 transition-all"
              style={{ width: `${d.uploadProgress ?? 0}%` }}
            />
          </div>
        </div>
      )}
      {uploadStatus === "ingesting" && (
        <div className="flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[9px] font-medium text-amber-200">
          <Loader2 className="h-2.5 w-2.5 animate-spin" />
          Converting to Parquet…
        </div>
      )}
      {uploadStatus === "failed" && (
        <div className="flex items-center gap-1 rounded-full bg-rose-500/15 px-2 py-0.5 text-[9px] font-medium text-rose-200">
          <AlertCircle className="h-2.5 w-2.5" />
          <span className="max-w-[140px] truncate" title={d.error ?? ""}>
            {d.error ?? "Upload failed"}
          </span>
        </div>
      )}
      {uploadStatus === "ready" && d.filename && (
        <div className="flex flex-col items-center gap-0.5">
          <div className="flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[9px] font-medium text-emerald-300">
            <CheckCircle2 className="h-2.5 w-2.5" />
            <span className="max-w-[120px] truncate">{d.filename}</span>
          </div>
          <div className="text-[9px] text-muted-foreground">
            {d.rowCount != null ? `${d.rowCount.toLocaleString()} rows` : ""}
            {d.sizeBytes ? ` · ${fmtBytes(d.sizeBytes)}` : ""}
          </div>
        </div>
      )}
    </motion.div>
  );
}

export const SourceNode = memo(SourceNodeComponent);
