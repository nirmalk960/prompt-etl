import { useId } from "react";
import { Plus, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { defaultItem, isVisible, type OpParam, type OpSpec } from "@/lib/catalog";

type Values = Record<string, unknown>;

interface OpConfigFormProps {
  spec: OpSpec;
  config: Values;
  onChange: (config: Values) => void;
  /** Known columns per input (index 0 = left/only, 1 = right); null = unknown. */
  inputColumns: (string[] | null)[];
}

const inputCls =
  "w-full rounded-lg border border-white/10 bg-black/30 px-2.5 py-1.5 text-[12px] text-foreground/95 outline-none transition-colors placeholder:text-muted-foreground/50 focus:border-violet-400/60";

export function OpConfigForm({ spec, config, onChange, inputColumns }: OpConfigFormProps) {
  const set = (name: string, value: unknown) => onChange({ ...config, [name]: value });
  return (
    <div className="space-y-3">
      {spec.params.map((p) =>
        isVisible(p, config) ? (
          <Field key={p.name} param={p} value={config[p.name]} values={config}
                 onChange={(v) => set(p.name, v)} inputColumns={inputColumns} />
        ) : null,
      )}
    </div>
  );
}

interface FieldProps {
  param: OpParam;
  value: unknown;
  values: Values;
  onChange: (v: unknown) => void;
  inputColumns: (string[] | null)[];
  compact?: boolean;
}

function Field({ param, value, values, onChange, inputColumns, compact }: FieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
        {param.label}
        {param.required && <span className="text-rose-300">*</span>}
      </label>
      <Control param={param} value={value} values={values} onChange={onChange}
               inputColumns={inputColumns} compact={compact} />
      {param.help && !compact && (
        <p className="text-[10px] leading-snug text-muted-foreground/70">{param.help}</p>
      )}
    </div>
  );
}

function Control({ param, value, onChange, inputColumns }: FieldProps) {
  const listId = useId();
  const cols = inputColumns[param.input ?? 0] ?? null;

  switch (param.type) {
    case "column":
      return (
        <>
          <input
            className={inputCls}
            list={cols ? listId : undefined}
            value={(value as string) ?? ""}
            placeholder={param.placeholder || (cols ? "Pick a column" : "Column name")}
            onChange={(e) => onChange(e.target.value)}
          />
          {cols && (
            <datalist id={listId}>
              {cols.map((c) => <option key={c} value={c} />)}
            </datalist>
          )}
        </>
      );
    case "columns":
      return <ColumnsPicker value={(value as string[]) ?? []} onChange={onChange} columns={cols} />;
    case "number":
    case "integer":
      return (
        <input
          type="number"
          step={param.type === "integer" ? 1 : "any"}
          className={inputCls}
          value={value === undefined || value === null ? "" : String(value)}
          placeholder={param.placeholder}
          onChange={(e) => {
            const raw = e.target.value;
            onChange(raw === "" ? null : Number(raw));
          }}
        />
      );
    case "select":
      return (
        <select className={inputCls} value={(value as string) ?? ""}
                onChange={(e) => onChange(e.target.value)}>
          {!param.required && param.default === undefined && <option value="">—</option>}
          {param.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      );
    case "multiselect": {
      const selected = new Set((value as string[]) ?? []);
      return (
        <div className="flex flex-wrap gap-1">
          {param.options?.map((o) => (
            <Chip key={o.value} active={selected.has(o.value)} onClick={() => {
              const next = new Set(selected);
              if (next.has(o.value)) next.delete(o.value); else next.add(o.value);
              onChange(param.options!.map((x) => x.value).filter((v) => next.has(v)));
            }}>{o.label}</Chip>
          ))}
        </div>
      );
    }
    case "boolean":
      return (
        <label className="flex items-center gap-2 text-[12px] text-foreground/90">
          <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)}
                 className="h-3.5 w-3.5 accent-violet-500" />
          {value ? "Yes" : "No"}
        </label>
      );
    case "expression":
      return (
        <textarea
          rows={2}
          className={cn(inputCls, "resize-y font-mono")}
          style={{ fontFamily: "'JetBrains Mono', ui-monospace, monospace" }}
          value={(value as string) ?? ""}
          placeholder={param.placeholder || "price * quantity"}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case "list":
      return <ListEditor param={param} value={(value as Values[]) ?? []} onChange={onChange}
                         inputColumns={inputColumns} />;
    default:
      return (
        <input className={inputCls} value={(value as string) ?? ""} placeholder={param.placeholder}
               onChange={(e) => onChange(e.target.value)} />
      );
  }
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10.5px] transition-colors",
        active
          ? "border-violet-400/60 bg-violet-500/25 text-violet-100"
          : "border-white/10 bg-white/[0.03] text-foreground/70 hover:bg-white/[0.07]",
      )}
    >
      {children}
    </button>
  );
}

function ColumnsPicker({ value, onChange, columns }: {
  value: string[];
  onChange: (v: unknown) => void;
  columns: string[] | null;
}) {
  if (!columns) {
    return (
      <input
        className={inputCls}
        value={value.join(", ")}
        placeholder="col_a, col_b"
        onChange={(e) => onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
      />
    );
  }
  const selected = new Set(value);
  const extra = value.filter((v) => !columns.includes(v));
  return (
    <div className="flex flex-wrap gap-1">
      {[...columns, ...extra].map((c) => (
        <Chip key={c} active={selected.has(c)} onClick={() => {
          onChange(selected.has(c) ? value.filter((v) => v !== c) : [...value, c]);
        }}>{c}</Chip>
      ))}
    </div>
  );
}

function ListEditor({ param, value, onChange, inputColumns }: {
  param: OpParam;
  value: Values[];
  onChange: (v: unknown) => void;
  inputColumns: (string[] | null)[];
}) {
  const fields = param.fields ?? [];
  const update = (i: number, item: Values) => onChange(value.map((v, j) => (j === i ? item : v)));
  return (
    <div className="space-y-2">
      {value.map((item, i) => (
        <div key={i} className="relative rounded-xl border border-white/10 bg-white/[0.025] p-2.5 pr-8">
          <button
            type="button"
            title="Remove"
            onClick={() => onChange(value.filter((_, j) => j !== i))}
            className="absolute right-2 top-2 rounded-md p-1 text-muted-foreground hover:bg-rose-500/15 hover:text-rose-300"
          >
            <Trash2 className="h-3 w-3" />
          </button>
          <div className="grid grid-cols-2 gap-2">
            {fields.map((f) =>
              isVisible(f, item ?? {}) ? (
                <div key={f.name} className={f.type === "list" || f.type === "columns" ? "col-span-2" : ""}>
                  <Field param={f} value={item?.[f.name]} values={item ?? {}} compact
                         onChange={(v) => update(i, { ...(item ?? {}), [f.name]: v })}
                         inputColumns={inputColumns} />
                </div>
              ) : null,
            )}
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...value, defaultItem(fields)])}
        className="flex items-center gap-1 rounded-lg border border-dashed border-white/15 px-2.5 py-1 text-[11px] text-foreground/75 hover:border-violet-400/50 hover:text-violet-200"
      >
        <Plus className="h-3 w-3" /> Add {param.label.toLowerCase().replace(/s$/, "")}
      </button>
    </div>
  );
}
