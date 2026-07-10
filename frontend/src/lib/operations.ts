// Operation bubble registry. Mirrors the prototype's catalogue but maps
// each operation to the new backend's PipelineNode `kind` enum.

export type BackendKind =
  | "source"
  | "filter"
  | "select"
  | "drop_nulls"
  | "fill_nulls"
  | "rename"
  | "deduplicate"
  | "aggregate"
  | "sort"
  | "custom"
  | "write";

export interface OperationBubble {
  operationId: string;
  backendKind: BackendKind;
  label: string;
  icon: string;
  color: string;
  presetPrompt: string;
  description: string;
}

export const OPERATION_BUBBLES: OperationBubble[] = [
  {
    operationId: "source",
    backendKind: "source",
    label: "Source",
    icon: "📂",
    color: "#3b82f6",
    presetPrompt: "",
    description: "Load CSV, JSON, or Excel file",
  },
  {
    operationId: "filter_rows",
    backendKind: "filter",
    label: "Filter",
    icon: "🔍",
    color: "#8b5cf6",
    presetPrompt: "Filter rows where ",
    description: "Filter rows by condition",
  },
  {
    operationId: "drop_nulls",
    backendKind: "drop_nulls",
    label: "Drop nulls",
    icon: "🧹",
    color: "#06b6d4",
    presetPrompt: "Drop rows with null values in ",
    description: "Remove rows with missing values",
  },
  {
    operationId: "fill_nulls",
    backendKind: "fill_nulls",
    label: "Fill nulls",
    icon: "✏️",
    color: "#14b8a6",
    presetPrompt: "Fill null values in  with ",
    description: "Fill missing values",
  },
  {
    operationId: "rename_columns",
    backendKind: "rename",
    label: "Rename",
    icon: "🏷️",
    color: "#f59e0b",
    presetPrompt: "Rename column  to ",
    description: "Rename columns",
  },
  {
    operationId: "dedupe",
    backendKind: "deduplicate",
    label: "Dedupe",
    icon: "🔄",
    color: "#ec4899",
    presetPrompt: "Remove duplicate rows based on ",
    description: "Remove duplicate rows",
  },
  {
    operationId: "aggregate",
    backendKind: "aggregate",
    label: "Aggregate",
    icon: "📊",
    color: "#6366f1",
    presetPrompt: "Sum of  by ",
    description: "Group and aggregate",
  },
  {
    operationId: "select_columns",
    backendKind: "select",
    label: "Select",
    icon: "📋",
    color: "#84cc16",
    presetPrompt: "Keep columns: ",
    description: "Select specific columns",
  },
  {
    operationId: "sort",
    backendKind: "sort",
    label: "Sort",
    icon: "↕️",
    color: "#a855f7",
    presetPrompt: "Sort by  ascending",
    description: "Sort rows",
  },
  {
    operationId: "custom",
    backendKind: "custom",
    label: "Custom",
    icon: "✨",
    color: "#64748b",
    presetPrompt: "",
    description: "Free-form natural language transform",
  },
  {
    operationId: "write",
    backendKind: "write",
    label: "Write",
    icon: "📁",
    color: "#22c55e",
    presetPrompt: "",
    description: "Materialize the result as a dataset",
  },
];

export function getOperationById(operationId: string): OperationBubble | undefined {
  return OPERATION_BUBBLES.find((o) => o.operationId === operationId);
}

export function getReactFlowNodeType(op: OperationBubble): string {
  if (op.backendKind === "source") return "source";
  if (op.backendKind === "write") return "write";
  return "operation";
}
