import { useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Search } from "lucide-react";
import { IO_BUBBLES } from "@/lib/operations";
import { opColor, opIcon, useCatalogStore } from "@/lib/catalog";
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

interface Bubble {
  id: string;
  label: string;
  icon: string;
  color: string;
  description: string;
}

export function OperationBubbleBar({
  onDragStart,
  onAddOperation,
  edgeType,
  onEdgeTypeChange,
}: OperationBubbleBarProps) {
  const catalog = useCatalogStore((s) => s.catalog);
  const catalogError = useCatalogStore((s) => s.error);
  const [category, setCategory] = useState<string>("all");
  const [query, setQuery] = useState("");

  const bubbles: Bubble[] = useMemo(() => {
    const q = query.trim().toLowerCase();
    const ops = (catalog?.operations ?? [])
      .filter((o) => q || category === "all" || o.category === category)
      .filter((o) =>
        !q || `${o.label} ${o.description} ${o.category} ${o.id}`.toLowerCase().includes(q),
      )
      .map((o) => ({
        id: o.id,
        label: o.label,
        icon: opIcon(o),
        color: opColor(o),
        description: o.description,
      }));
    const io = IO_BUBBLES.filter(
      (b) => !q || b.label.toLowerCase().includes(q) || b.description.toLowerCase().includes(q),
    ).map((b) => ({ ...b, id: b.operationId }));
    return [...io, ...ops];
  }, [catalog, category, query]);

  return (
    <div className="border-b border-white/5 bg-gradient-to-r from-white/[0.02] via-white/[0.04] to-white/[0.02] px-5 py-2.5 backdrop-blur-xl">
      <div className="flex items-center gap-2">
        <div className="relative shrink-0">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search operations"
            className="w-44 rounded-full border border-white/10 bg-white/[0.04] py-1.5 pl-7 pr-3 text-[11px] outline-none placeholder:text-muted-foreground/60 focus:border-violet-400/50"
          />
        </div>
        <div className="scrollbar-thin flex flex-1 items-center gap-1 overflow-x-auto">
          {[{ id: "all", label: "All" }, ...(catalog?.categories ?? [])].map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => {
                setCategory(c.id);
                setQuery("");
              }}
              className={cn(
                "shrink-0 rounded-full px-2.5 py-1 text-[10.5px] font-medium transition-colors",
                category === c.id && !query
                  ? "bg-white/[0.12] text-foreground"
                  : "text-foreground/60 hover:bg-white/[0.06] hover:text-foreground",
              )}
              style={
                category === c.id && !query && c.id !== "all"
                  ? { boxShadow: `inset 0 0 0 1px ${hexToRgba(opColor({ category: c.id }), 0.6)}` }
                  : undefined
              }
            >
              {c.label}
            </button>
          ))}
          {catalogError && (
            <span className="shrink-0 text-[10.5px] text-rose-300">Operations unavailable: {catalogError}</span>
          )}
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

      <div className="scrollbar-thin mt-2 flex items-center gap-2 overflow-x-auto pb-1 pr-2">
        {bubbles.map((op, idx) => (
          <motion.button
            key={op.id}
            type="button"
            draggable
            data-op-id={op.id}
            onDragStart={(e) => {
              (e.currentTarget as HTMLElement).classList.add("bubble-dragging");
              onDragStart(e as unknown as React.DragEvent, op.id);
            }}
            onDragEnd={(e) => (e.currentTarget as HTMLElement).classList.remove("bubble-dragging")}
            onClick={() => onAddOperation(op.id)}
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(idx, 12) * 0.02, duration: 0.25, ease: "easeOut" }}
            whileHover={{ y: -2 }}
            whileTap={{ scale: 0.94 }}
            title={op.description}
            className="group relative flex shrink-0 cursor-grab items-center gap-2 rounded-full px-3.5 py-1.5 text-xs font-medium text-foreground/90 outline-none transition-all duration-200 active:cursor-grabbing"
            style={{
              background: `linear-gradient(135deg, ${hexToRgba(op.color, 0.18)}, ${hexToRgba(op.color, 0.06)})`,
              border: `1px solid ${hexToRgba(op.color, 0.35)}`,
              boxShadow: `0 4px 18px -6px ${hexToRgba(op.color, 0.45)}, inset 0 1px 0 rgba(255,255,255,0.08)`,
              backdropFilter: "blur(14px) saturate(160%)",
            }}
          >
            <span className="text-base leading-none" style={{ filter: "drop-shadow(0 1px 2px rgba(0,0,0,0.4))" }}>
              {op.icon}
            </span>
            <span className="tracking-wide">{op.label}</span>
            <span
              className="pointer-events-none absolute inset-0 rounded-full opacity-0 transition-opacity duration-200 group-hover:opacity-100"
              style={{ boxShadow: `0 0 0 1px ${hexToRgba(op.color, 0.55)}, 0 0 22px ${hexToRgba(op.color, 0.45)}` }}
            />
          </motion.button>
        ))}
        {query && bubbles.length === 0 && (
          <span className="text-[11px] text-muted-foreground">No operation matches “{query}”.</span>
        )}
      </div>
    </div>
  );
}
