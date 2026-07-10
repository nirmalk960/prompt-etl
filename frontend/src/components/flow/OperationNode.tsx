import { memo } from "react";
import { motion } from "framer-motion";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { cn, hexToRgba } from "@/lib/utils";
import { getOperationById } from "@/lib/operations";
import { STATUS_GLOW, useNodeRunStatus } from "@/lib/nodeStatus";
import { StatusBadge } from "./StatusBadge";

export interface OperationData {
  operationId: string;
  label?: string;
  color?: string;
  icon?: string;
  prompt?: string;
}

function OperationNodeComponent({ id, data, selected }: NodeProps) {
  const d = data as unknown as OperationData;
  const op = getOperationById(d.operationId);
  const color = d.color || op?.color || "#a78bfa";
  const icon = d.icon || op?.icon || "▢";
  const label = d.label || op?.label || "Step";
  const prompt = d.prompt || "";

  const status = useNodeRunStatus(id);
  const statusGlow = STATUS_GLOW[status];
  const isRunning = status === "running";

  const baseShadow = selected
    ? `0 0 0 1px ${hexToRgba(color, 0.6)}, 0 18px 40px -14px ${hexToRgba(color, 0.55)}, inset 0 1px 0 rgba(255,255,255,0.08)`
    : `0 12px 28px -14px ${hexToRgba(color, 0.5)}, inset 0 1px 0 rgba(255,255,255,0.06)`;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.92, y: 6 }}
      animate={{
        opacity: 1,
        scale: 1,
        y: 0,
        boxShadow: statusGlow || baseShadow,
      }}
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
      <Handle type="target" position={Position.Top} />
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
            Operation
          </div>
        </div>
      </div>
      {prompt && (
        <p className="mt-2 line-clamp-2 border-t border-white/5 pt-2 text-[10px] leading-relaxed text-muted-foreground">
          {prompt.slice(0, 110)}
          {prompt.length > 110 ? "…" : ""}
        </p>
      )}
      {!prompt && (
        <p className="mt-2 border-t border-white/5 pt-2 text-[10px] italic text-muted-foreground/70">
          Select to add a prompt…
        </p>
      )}
      <Handle type="source" position={Position.Bottom} />
    </motion.div>
  );
}

export const OperationNode = memo(OperationNodeComponent);
