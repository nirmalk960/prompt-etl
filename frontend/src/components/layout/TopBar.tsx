import { motion } from "framer-motion";
import {
  Sparkles,
  Play,
  Settings2,
  FilePlus2,
  Save,
  FolderOpen,
  Square,
  Download,
  Loader2,
  Wifi,
  WifiOff,
  Cpu,
  Database,
  AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ReadyState } from "@/api/client";

interface TopBarProps {
  backendOk: boolean;
  ready: ReadyState | null;
  engine: string | null;
  boardName: string;
  onBoardNameChange: (name: string) => void;
  onRun: () => void;
  onNew: () => void;
  onSave: () => void;
  onOpen: () => void;
  onStop: () => void;
  onExportNow: () => void;
  onSettings: () => void;
  runStatus: string;
  canRun: boolean;
  canExport: boolean;
  exporting: boolean;
  preflightIssueCount: number;
}

function fmtRam(bytes: number): string {
  const gb = bytes / (1024 ** 3);
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  const mb = bytes / (1024 ** 2);
  return `${mb.toFixed(0)} MB`;
}

export function TopBar({
  backendOk,
  ready,
  engine,
  boardName,
  onBoardNameChange,
  onRun,
  onNew,
  onSave,
  onOpen,
  onStop,
  onExportNow,
  onSettings,
  runStatus,
  canRun,
  canExport,
  exporting,
  preflightIssueCount,
}: TopBarProps) {
  const ollamaOk = ready?.ollama.available === true;
  const ollamaDisabled = ready?.ollama.disabled === true;

  return (
    <header className="glass-strong relative flex items-center justify-between border-b border-white/5 px-5 py-3">
      <div className="flex items-center gap-4">
        <motion.div
          initial={{ scale: 0.85, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          className="flex items-center gap-2.5"
        >
          <div className="relative">
            <div className="absolute inset-0 rounded-xl bg-gradient-to-br from-violet-500 via-fuchsia-500 to-cyan-400 opacity-70 blur-md" />
            <div className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 via-fuchsia-500 to-cyan-400 shadow-lg">
              <Sparkles className="h-4 w-4 text-white drop-shadow" strokeWidth={2.5} />
            </div>
          </div>
          <div className="leading-tight">
            <h1
              className="gradient-text text-[15px] font-bold tracking-tight"
              style={{ fontFamily: "'Space Grotesk', 'Inter', sans-serif" }}
            >
              Prompt ETL Studio
            </h1>
            <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-muted-foreground">
              AI data pipelines · scales with your server
            </p>
          </div>
        </motion.div>

        <input
          value={boardName}
          onChange={(e) => onBoardNameChange(e.target.value)}
          onBlur={(e) => {
            if (!e.target.value.trim()) onBoardNameChange("Untitled board");
          }}
          spellCheck={false}
          title="Board name — used in saved pipeline files and exports"
          className="w-44 rounded-lg border border-white/10 bg-white/[0.04] px-2.5 py-1.5 text-[12px] font-semibold tracking-tight text-foreground/90 outline-none transition-colors placeholder:text-muted-foreground/60 hover:border-white/20 focus:border-violet-400/50 focus:bg-white/[0.07]"
          placeholder="Board name"
        />

        <div className="ml-2 flex flex-wrap items-center gap-2">
          <div className="chip" title={backendOk ? "Backend healthy" : "Backend unreachable"}>
            {backendOk ? (
              <Wifi className="h-3 w-3 text-emerald-400" />
            ) : (
              <WifiOff className="h-3 w-3 text-rose-400" />
            )}
            <span className={backendOk ? "text-emerald-300" : "text-rose-300"}>
              API {backendOk ? "online" : "offline"}
            </span>
          </div>

          {ready && (
            <div className="chip" title={`${ready.cpus} CPUs · ${fmtRam(ready.ram_bytes)} RAM`}>
              <Cpu className="h-3 w-3 text-violet-300" />
              <span className="text-foreground/85">
                {ready.cpus}× · {fmtRam(ready.ram_bytes)}
              </span>
            </div>
          )}

          <div className="chip" title={ready?.ollama.model || ""}>
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                ollamaDisabled
                  ? "bg-zinc-500"
                  : ollamaOk
                  ? "bg-emerald-400 shadow-[0_0_12px_2px_rgba(52,211,153,0.6)]"
                  : "bg-amber-400 shadow-[0_0_12px_2px_rgba(251,191,36,0.55)] animate-pulse",
              )}
            />
            <span className="text-foreground/85">
              {ollamaDisabled
                ? "LLM stub"
                : ollamaOk
                ? `Ollama: ${ready?.ollama.model}`
                : "Ollama starting"}
            </span>
          </div>

          {engine && (
            <div
              className="chip"
              title="Engine selected by the router for the current job"
            >
              <Database className="h-3 w-3 text-cyan-300" />
              <span className="text-cyan-200/90 font-semibold uppercase tracking-wider text-[10px]">
                {engine}
              </span>
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1.5">
        <Button variant="ghost" size="sm" onClick={onNew} className="text-foreground/80">
          <FilePlus2 className="h-3.5 w-3.5" />
          New
        </Button>
        <Button variant="ghost" size="sm" onClick={onSave} className="text-foreground/80">
          <Save className="h-3.5 w-3.5" />
          Save
        </Button>
        <Button variant="ghost" size="sm" onClick={onOpen} className="text-foreground/80">
          <FolderOpen className="h-3.5 w-3.5" />
          Open
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onStop}
          disabled={runStatus !== "running"}
          className={cn(runStatus === "running" && "border-rose-400/40 text-rose-200")}
        >
          <Square className="h-3.5 w-3.5" />
          Stop
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={onExportNow}
          disabled={!canExport || exporting}
        >
          {exporting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          Export now
        </Button>
        <Button variant="outline" size="sm" onClick={onSettings}>
          <Settings2 className="h-3.5 w-3.5" />
          Settings
        </Button>
        {preflightIssueCount > 0 && (
          <div className="chip border-rose-400/35 bg-rose-500/[0.08] text-rose-200">
            <AlertTriangle className="h-3 w-3 text-rose-300" />
            {preflightIssueCount} preflight issue
            {preflightIssueCount === 1 ? "" : "s"}
          </div>
        )}
        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.97 }}>
          <Button
            size="sm"
            onClick={onRun}
            disabled={!canRun || runStatus === "running"}
            className="relative overflow-hidden border-0 bg-gradient-to-r from-violet-500 via-fuchsia-500 to-cyan-500 text-white shadow-[0_8px_24px_-6px_rgba(168,85,247,0.5)] hover:shadow-[0_12px_32px_-6px_rgba(168,85,247,0.65)]"
          >
            {runStatus === "running" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5 fill-white" />
            )}
            Run pipeline
          </Button>
        </motion.div>
      </div>
    </header>
  );
}
