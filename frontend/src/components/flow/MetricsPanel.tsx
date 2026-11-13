import { BarChart3 } from "lucide-react";

const LABELS: Record<string, string> = {
  r2: "R²",
  mae: "MAE",
  rmse: "RMSE",
  accuracy: "Accuracy",
  f1: "F1",
  precision: "Precision",
  recall: "Recall",
  silhouette: "Silhouette",
  inertia: "Inertia",
  k: "Clusters",
  anomalies: "Anomalies",
  anomaly_pct: "Anomaly %",
  mape_pct: "MAPE %",
  train_rows: "Train rows",
  test_rows: "Test rows",
  scored_rows: "Scored rows",
  history_periods: "History periods",
  horizon: "Horizon",
  seasonal_periods: "Season length",
  frequency: "Frequency",
  method: "Method",
  model: "Model",
  target: "Target",
};

const PCT = new Set(["accuracy", "f1", "precision", "recall"]);
const SKIP = new Set(["task", "features", "importance", "confusion", "sizes", "cluster_means", "notice"]);

function fmt(key: string, v: unknown): string {
  if (typeof v === "number") {
    if (PCT.has(key)) return `${(v * 100).toFixed(1)}%`;
    return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(4).replace(/\.?0+$/, "");
  }
  return String(v);
}

export function MetricsPanel({ metrics }: { metrics: Record<string, any> }) {
  const scalars = Object.entries(metrics).filter(
    ([k, v]) => !SKIP.has(k) && v !== null && v !== undefined && typeof v !== "object",
  );
  const importance = (metrics.importance as { feature: string; importance: number }[] | undefined) ?? [];
  const confusion = metrics.confusion as { labels: string[]; matrix: number[][] } | undefined;
  const sizes = metrics.sizes as Record<string, number> | undefined;
  const means = (metrics.cluster_means as Record<string, number>[] | undefined) ?? [];
  const maxImp = Math.max(...importance.map((i) => i.importance), 0.0001);

  return (
    <div className="space-y-3 rounded-xl border border-pink-400/25 bg-pink-500/[0.05] p-3">
      <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-pink-200">
        <BarChart3 className="h-3 w-3" />
        Model results{metrics.task ? ` · ${metrics.task}` : ""}
      </div>

      <div className="grid grid-cols-3 gap-1.5">
        {scalars.map(([k, v]) => (
          <div key={k} className="rounded-lg bg-black/25 px-2 py-1.5">
            <div className="text-[9px] uppercase tracking-[0.12em] text-muted-foreground">{LABELS[k] ?? k}</div>
            <div className="truncate text-[12px] font-semibold text-foreground/95" title={String(v)}>
              {fmt(k, v)}
            </div>
          </div>
        ))}
      </div>

      {metrics.notice && <p className="text-[10px] italic text-amber-200/80">{metrics.notice}</p>}

      {importance.length > 0 && (
        <div>
          <div className="mb-1 text-[9.5px] uppercase tracking-[0.14em] text-muted-foreground">
            What drives the prediction
          </div>
          <div className="space-y-1">
            {importance.map((i) => (
              <div key={i.feature} className="flex items-center gap-2 text-[10.5px]">
                <span className="w-24 truncate text-foreground/80" title={i.feature}>{i.feature}</span>
                <div className="h-1.5 flex-1 rounded-full bg-white/[0.06]">
                  <div className="h-1.5 rounded-full bg-pink-400/80"
                       style={{ width: `${(i.importance / maxImp) * 100}%` }} />
                </div>
                <span className="w-10 text-right font-mono text-[9.5px] text-muted-foreground">
                  {(i.importance * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {confusion && confusion.labels.length > 0 && (
        <div>
          <div className="mb-1 text-[9.5px] uppercase tracking-[0.14em] text-muted-foreground">
            Confusion matrix (rows = actual, columns = predicted)
          </div>
          <div className="scrollbar-thin overflow-auto">
            <table className="text-[10px]">
              <thead>
                <tr>
                  <th />
                  {confusion.labels.map((l) => (
                    <th key={l} className="px-1.5 py-0.5 font-medium text-muted-foreground">{l}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {confusion.matrix.map((row, i) => (
                  <tr key={confusion.labels[i]}>
                    <td className="pr-1.5 font-medium text-muted-foreground">{confusion.labels[i]}</td>
                    {row.map((n, j) => (
                      <td key={j} className={`px-1.5 py-0.5 text-center font-mono ${i === j ? "bg-emerald-500/20 text-emerald-100" : "text-foreground/80"}`}>
                        {n}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {sizes && (
        <div className="flex flex-wrap gap-1">
          {Object.entries(sizes).map(([c, n]) => (
            <span key={c} className="rounded-full bg-white/[0.06] px-2 py-0.5 text-[10px]">
              cluster {c}: {n.toLocaleString()} rows
            </span>
          ))}
        </div>
      )}

      {means.length > 0 && (
        <div className="scrollbar-thin overflow-auto">
          <div className="mb-1 text-[9.5px] uppercase tracking-[0.14em] text-muted-foreground">Cluster averages</div>
          <table className="text-[10px]">
            <thead>
              <tr>
                {Object.keys(means[0]).map((k) => (
                  <th key={k} className="px-1.5 py-0.5 text-left font-medium text-muted-foreground">{k}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {means.map((row, i) => (
                <tr key={i}>
                  {Object.entries(row).map(([k, v]) => (
                    <td key={k} className="px-1.5 py-0.5 font-mono text-foreground/80">{fmt(k, v)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
