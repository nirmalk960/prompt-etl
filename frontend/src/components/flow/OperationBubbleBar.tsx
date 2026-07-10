import { motion } from "framer-motion";
import { OPERATION_BUBBLES } from "@/lib/operations";
import { cn, hexToRgba } from "@/lib/utils";

interface OperationBubbleBarProps {
  onDragStart: (e: React.DragEvent, operationId: string) => void;
  onAddOperation: (operationId: string) => void;
  edgeType: string;
  onEdgeTypeChange: (type: "smoothstep" | "step" | "straight") => void;
}

const EDGE_TYPES = [
  { type: "smoothstep" as const, label: "Smooth" },
  { type: "step" as const, label: "Step" },
  { type: "straight" as const, label: "Linear" },
];

export function OperationBubbleBar({
  onDragStart,
  onAddOperation,
  edgeType,
  onEdgeTypeChange,
}: OperationBubbleBarProps) {
  return (
    <div className="border-b border-white/5 bg-gradient-to-r from-white/[0.02] via-white/[0.04] to-white/[0.02] px-5 py-3 backdrop-blur-xl">
      <div className="flex items-center gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          Operations
        </span>

        <div className="scrollbar-thin ml-1 flex flex-1 items-center gap-2 overflow-x-auto pb-1 pr-2">
          {OPERATION_BUBBLES.map((op, idx) => (
            <motion.button
              key={op.operationId}
              type="button"
              draggable
              onDragStart={(e) => {
                (e.currentTarget as HTMLElement).classList.add("bubble-dragging");
                onDragStart(e as unknown as React.DragEvent, op.operationId);
              }}
              onDragEnd={(e) =>
                (e.currentTarget as HTMLElement).classList.remove("bubble-dragging")
              }
              onClick={() => onAddOperation(op.operationId)}
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.03, duration: 0.3, ease: "easeOut" }}
              whileHover={{ y: -2 }}
              whileTap={{ scale: 0.94 }}
              title={op.description}
              className={cn(
                "group relative flex shrink-0 cursor-grab items-center gap-2 rounded-full px-3.5 py-1.5 text-xs font-medium text-foreground/90 outline-none transition-all duration-200 active:cursor-grabbing",
              )}
              style={{
                background: `linear-gradient(135deg, ${hexToRgba(op.color, 0.18)}, ${hexToRgba(op.color, 0.06)})`,
                border: `1px solid ${hexToRgba(op.color, 0.35)}`,
                boxShadow: `0 4px 18px -6px ${hexToRgba(op.color, 0.45)}, inset 0 1px 0 rgba(255,255,255,0.08)`,
                backdropFilter: "blur(14px) saturate(160%)",
              }}
            >
              <span
                className="text-base leading-none drop-shadow"
                style={{ filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.4))" }}
              >
                {op.icon}
              </span>
              <span className="tracking-wide">{op.label}</span>
              <span
                className="pointer-events-none absolute inset-0 rounded-full opacity-0 transition-opacity duration-200 group-hover:opacity-100"
                style={{
                  boxShadow: `0 0 0 1px ${hexToRgba(op.color, 0.55)}, 0 0 22px ${hexToRgba(op.color, 0.45)}`,
                }}
              />
            </motion.button>
          ))}
        </div>

        <div className="ml-auto flex shrink-0 items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] p-1 backdrop-blur-xl">
          <span className="px-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
            Edge
          </span>
          {EDGE_TYPES.map((et) => (
            <button
              key={et.type}
              onClick={() => onEdgeTypeChange(et.type)}
              className={cn(
                "rounded-full px-2.5 py-1 text-[11px] font-medium transition-all",
                edgeType === et.type
                  ? "bg-gradient-to-r from-violet-500/90 to-fuchsia-500/90 text-white shadow-[0_4px_12px_-4px_rgba(168,85,247,0.6)]"
                  : "text-foreground/70 hover:bg-white/[0.06] hover:text-foreground",
              )}
            >
              {et.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
