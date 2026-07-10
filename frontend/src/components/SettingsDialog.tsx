import { motion, AnimatePresence } from "framer-motion";
import { X } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

interface SettingsDialogProps {
  open: boolean;
  onClose: () => void;
  enginePref: "auto" | "pandas" | "polars" | "duckdb";
  onEnginePrefChange: (v: "auto" | "pandas" | "polars" | "duckdb") => void;
  apiBaseUrl: string;
  onApiBaseUrlChange: (v: string) => void;
}

const ENGINES = ["auto", "pandas", "polars", "duckdb"] as const;

export function SettingsDialog({
  open,
  onClose,
  enginePref,
  onEnginePrefChange,
  apiBaseUrl,
  onApiBaseUrlChange,
}: SettingsDialogProps) {
  const [email, setEmail] = useState(
    () => localStorage.getItem("prompt-etl.email") || "you@local",
  );
  const [apiUrl, setApiUrl] = useState(apiBaseUrl);

  useEffect(() => {
    if (!open) return;
    setEmail(localStorage.getItem("prompt-etl.email") || "you@local");
    setApiUrl(apiBaseUrl);
  }, [open, apiBaseUrl]);

  function saveEmail() {
    localStorage.setItem("prompt-etl.email", email.trim() || "you@local");
  }

  function saveApiUrl() {
    onApiBaseUrlChange(apiUrl.trim());
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md"
          onClick={onClose}
        >
          <motion.div
            initial={{ scale: 0.95, y: 10, opacity: 0 }}
            animate={{ scale: 1, y: 0, opacity: 1 }}
            exit={{ scale: 0.95, y: 10, opacity: 0 }}
            transition={{ type: "spring", stiffness: 280, damping: 26 }}
            className="glass-strong relative w-[460px] max-w-[92vw] rounded-2xl p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={onClose}
              className="absolute right-3 top-3 rounded-lg p-1.5 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
            <h2 className="text-base font-semibold tracking-tight">Settings</h2>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Local-dev settings. The deployed server controls the rest.
            </p>

            <div className="mt-5 space-y-5">
              <section>
                <label className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Server URL
                </label>
                <div className="mt-1 flex gap-2">
                  <input
                    value={apiUrl}
                    onChange={(e) => setApiUrl(e.target.value)}
                    className="glass-input flex-1 rounded-lg px-3 py-1.5 text-sm"
                    placeholder="http://localhost:8000"
                  />
                  <Button size="sm" onClick={saveApiUrl}>
                    Save
                  </Button>
                </div>
                <p className="mt-1 text-[10px] text-muted-foreground">
                  Point this to a local server IP / internal DNS (example:
                  <code className="font-mono"> http://10.0.0.25:8000</code>).
                </p>
              </section>

              <section>
                <label className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Dev auth identity
                </label>
                <div className="mt-1 flex gap-2">
                  <input
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="glass-input flex-1 rounded-lg px-3 py-1.5 text-sm"
                    placeholder="you@local"
                  />
                  <Button size="sm" onClick={saveEmail}>
                    Save
                  </Button>
                </div>
                <p className="mt-1 text-[10px] text-muted-foreground">
                  Only used in local development. Sent as
                  <code className="font-mono"> Bearer dev:&lt;email&gt;</code>.
                </p>
              </section>

              <section>
                <label className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
                  Engine preference
                </label>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {ENGINES.map((e) => (
                    <button
                      key={e}
                      onClick={() => onEnginePrefChange(e)}
                      className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-wider transition-all ${
                        enginePref === e
                          ? "bg-gradient-to-r from-violet-500 to-fuchsia-500 text-white shadow-[0_4px_12px_-4px_rgba(168,85,247,0.6)]"
                          : "border border-white/10 bg-white/[0.04] text-foreground/75 hover:bg-white/[0.08]"
                      }`}
                    >
                      {e}
                    </button>
                  ))}
                </div>
                <p className="mt-1 text-[10px] text-muted-foreground">
                  <strong>auto</strong> lets the router pick based on dataset size + available
                  RAM. Override only if you know what you want.
                </p>
              </section>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
