import { Table2 } from "lucide-react";
import type { PreviewData } from "@/stores/workflowStore";

interface SchemaPanelProps {
  preview: PreviewData;
  title?: string;
}

export function SchemaPanel({ preview, title = "Preview" }: SchemaPanelProps) {
  const cols = preview.columns ?? [];
  const rows = preview.rows ?? [];
  const sch = preview.schema ?? [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          <Table2 className="h-3 w-3 text-violet-300" />
          {title}
        </div>
        <span className="chip text-[10px] text-muted-foreground">
          {preview.total_rows != null
            ? `${preview.total_rows.toLocaleString()} rows`
            : `${rows.length} rows`}
          {cols.length ? ` · ${cols.length} cols` : ""}
        </span>
      </div>

      {sch.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {sch.slice(0, 12).map((c) => (
            <span
              key={c.name}
              className="chip text-[10px] font-mono"
              title={c.dtype}
            >
              <span className="text-foreground/90">{c.name}</span>
              <span className="text-muted-foreground">: {c.dtype}</span>
            </span>
          ))}
          {sch.length > 12 && (
            <span className="chip text-[10px] text-muted-foreground">
              +{sch.length - 12} more
            </span>
          )}
        </div>
      )}

      <div className="scrollbar-thin max-h-72 overflow-auto rounded-xl border border-white/5 bg-white/[0.02]">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-[hsl(230_30%_10%/0.85)] backdrop-blur">
            <tr>
              {cols.map((c) => (
                <th
                  key={c}
                  className="border-b border-white/5 px-2 py-1.5 text-left font-semibold text-foreground/80"
                >
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 50).map((r, i) => (
              <tr key={i} className="border-b border-white/5 last:border-b-0">
                {r.map((v: any, j: number) => (
                  <td
                    key={j}
                    className="px-2 py-1 align-top text-foreground/85 font-mono"
                  >
                    {v === null || v === undefined
                      ? <span className="text-muted-foreground/60 italic">null</span>
                      : String(v)}
                  </td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td
                  colSpan={Math.max(1, cols.length)}
                  className="px-2 py-4 text-center text-[10px] italic text-muted-foreground"
                >
                  (no rows)
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
