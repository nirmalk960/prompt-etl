import { motion, AnimatePresence } from "framer-motion";
import { Check, Loader2, AlertCircle } from "lucide-react";
import { STATUS_DOT, type NodeRunStatus } from "@/lib/nodeStatus";

export function StatusBadge({ status }: { status: NodeRunStatus }) {
  const color = STATUS_DOT[status];
  if (!color) return null;
  return (
    <AnimatePresence>
      <motion.div
        key={status}
        initial={{ opacity: 0, scale: 0.6 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.6 }}
        className="pointer-events-none absolute -right-1.5 -top-1.5 z-10"
      >
        <div
          className="flex h-5 w-5 items-center justify-center rounded-full backdrop-blur"
          style={{
            background: color.replace("0.95", "0.18"),
            border: `1.5px solid ${color}`,
            boxShadow: `0 0 14px ${color.replace("0.95", "0.65")}`,
          }}
        >
          {status === "running" ? (
            <Loader2 className="h-2.5 w-2.5 animate-spin text-amber-100" />
          ) : status === "done" ? (
            <Check className="h-2.5 w-2.5 text-emerald-100" strokeWidth={3.5} />
          ) : status === "error" ? (
            <AlertCircle className="h-2.5 w-2.5 text-rose-100" />
          ) : null}
        </div>
        {status === "running" && (
          <motion.div
            className="absolute inset-0 rounded-full"
            style={{ background: color }}
            animate={{ scale: [1, 1.7], opacity: [0.45, 0] }}
            transition={{ duration: 1.2, repeat: Infinity, ease: "easeOut" }}
          />
        )}
      </motion.div>
    </AnimatePresence>
  );
}
