import { motion } from "framer-motion";
import { Check, Loader2, Circle, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { StepState } from "@/stores/workflowStore";

interface StepTimelineProps {
  steps: StepState[];
}

const statusStyles: Record<StepState["status"], string> = {
  idle: "border-white/15 bg-white/[0.04] text-muted-foreground",
  pending: "border-white/15 bg-white/[0.04] text-muted-foreground",
  running: "border-amber-400/60 bg-amber-400/10 text-amber-200",
  done: "border-emerald-400/50 bg-emerald-400/10 text-emerald-200 shadow-[0_0_22px_-2px_rgba(16,185,129,0.55)]",
  error: "border-rose-400/50 bg-rose-400/10 text-rose-200 shadow-[0_0_22px_-2px_rgba(244,63,94,0.55)]",
};

const statusIcon: Record<StepState["status"], React.ReactNode> = {
  idle: <Circle className="h-3 w-3 opacity-60" />,
  pending: <Circle className="h-3 w-3 opacity-60" />,
  running: <Loader2 className="h-3 w-3 animate-spin" />,
  done: <Check className="h-3 w-3" strokeWidth={3} />,
  error: <AlertCircle className="h-3 w-3" />,
};

export function StepTimeline({ steps }: StepTimelineProps) {
  if (!steps.length) return null;
  const completed = steps.filter((s) => s.status === "done").length;

  return (
    <div className="glass rounded-2xl px-5 py-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          Pipeline steps
        </p>
        <p className="text-[10px] text-muted-foreground">
          {completed} / {steps.length} completed
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {steps.map((step, i) => {
          const isRunning = step.status === "running";
          return (
            <motion.div
              key={step.id}
              layout
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: i * 0.04 }}
              className="flex items-center gap-2"
            >
              <motion.div
                animate={{
                  boxShadow: isRunning
                    ? [
                        "0 0 14px -2px rgba(251, 191, 36, 0.45)",
                        "0 0 28px 2px rgba(251, 191, 36, 0.75)",
                        "0 0 14px -2px rgba(251, 191, 36, 0.45)",
                      ]
                    : "0 0 0 rgba(0,0,0,0)",
                }}
                transition={
                  isRunning
                    ? { duration: 1.4, repeat: Infinity, ease: "easeInOut" }
                    : { duration: 0.4 }
                }
                className={cn(
                  "flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-medium backdrop-blur transition-colors",
                  statusStyles[step.status],
                )}
              >
                {statusIcon[step.status]}
                <span>{step.label}</span>
              </motion.div>
              {i < steps.length - 1 && (
                <motion.span
                  animate={
                    step.status === "done"
                      ? { opacity: 1, color: "rgb(110 231 183)" }
                      : { opacity: 0.4, color: "rgb(148 163 184)" }
                  }
                  className="text-sm"
                >
                  →
                </motion.span>
              )}
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}
