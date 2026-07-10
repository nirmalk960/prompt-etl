// Typed API client.
// In dev, Vite proxies /api/* to the backend (see vite.config.ts).
// In prod, set VITE_API_BASE_URL or serve the SPA from the same origin as the API.

const ENV_BASE = (import.meta as any).env?.VITE_API_BASE_URL as string | undefined;
const DEFAULT_API_BASE = ENV_BASE ? ENV_BASE.replace(/\/$/, "") : "/api";
const API_BASE_KEY = "prompt-etl.api-base-url";

function resolveApiBase(): string {
  const fromStorage = localStorage.getItem(API_BASE_KEY)?.trim();
  if (fromStorage) return fromStorage.replace(/\/$/, "");
  return DEFAULT_API_BASE;
}

function tokenFromStorage(): string {
  const email = localStorage.getItem("prompt-etl.email") || "you@local";
  return `Bearer dev:${email}`;
}

async function request<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const apiBase = resolveApiBase();
  const headers = new Headers(init.headers || {});
  if (!(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("Authorization", tokenFromStorage());
  const res = await fetch(`${apiBase}${path}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function getApiBaseUrl(): string {
  return resolveApiBase();
}

export function setApiBaseUrl(url: string): void {
  const cleaned = url.trim().replace(/\/$/, "");
  if (!cleaned) {
    localStorage.removeItem(API_BASE_KEY);
    return;
  }
  localStorage.setItem(API_BASE_KEY, cleaned);
}

// -------- Health / Ready --------
export interface ReadyState {
  status: string;
  app_env: string;
  ram_bytes: number;
  cpus: number;
  ollama: { available: boolean; model: string; disabled: boolean };
  object_store: string;
  spark_threshold_bytes: number;
}
export const getReady = () => request<ReadyState>("/ready");

// -------- Auth --------
export interface Me {
  user: { id: string; email: string; display_name: string | null };
  workspace: { id: string; name: string };
}
export const getMe = () => request<Me>("/auth/me");

// -------- Datasets --------
export interface DatasetSummary {
  id: string;
  name: string;
  source_format: string;
  status: string;
  raw_size_bytes: number;
  parquet_size_bytes: number | null;
  row_count: number | null;
  created_at: string;
  error: string | null;
}

export interface DatasetDetail extends DatasetSummary {
  schema_json: { fields?: { name: string; dtype: string }[] } | null;
  raw_object_key: string;
  parquet_object_key: string | null;
}

export interface DatasetInitResponse {
  dataset_id: string;
  upload_id: string;
  parts: { part_number: number; url: string }[];
  object_key: string;
  part_size_bytes: number;
}

export const listDatasets = () => request<DatasetSummary[]>("/datasets");
export const getDataset = (id: string) => request<DatasetDetail>(`/datasets/${id}`);
export const previewDataset = (id: string, n = 50) =>
  request<{ columns: string[]; rows: any[][]; total_rows: number | null }>(
    `/datasets/${id}/preview?n=${n}`,
  );

export async function initUpload(opts: {
  name: string;
  source_format: "csv" | "json" | "xlsx" | "parquet";
  size_bytes: number;
}): Promise<DatasetInitResponse> {
  return request<DatasetInitResponse>("/datasets/init", {
    method: "POST",
    body: JSON.stringify(opts),
  });
}

export async function completeUpload(
  datasetId: string,
  uploadId: string,
  parts: { part_number: number; etag: string }[],
): Promise<{ dataset_id: string; status: string }> {
  return request(`/datasets/${datasetId}/complete`, {
    method: "POST",
    body: JSON.stringify({ upload_id: uploadId, parts }),
  });
}

// -------- Workflows --------
export interface PipelineNodePayload {
  id: string;
  kind: string;
  label?: string | null;
  prompt?: string | null;
  config?: Record<string, unknown>;
  dataset_id?: string | null;
  output_name?: string | null;
}
export interface PipelineEdgePayload {
  source: string;
  target: string;
}
export interface PipelineDefinition {
  nodes: PipelineNodePayload[];
  edges: PipelineEdgePayload[];
  engine?: "auto" | "pandas" | "polars" | "duckdb";
}
export interface WorkflowSummary {
  id: string;
  name: string;
  description: string | null;
}
export interface WorkflowDetail extends WorkflowSummary {
  definition: PipelineDefinition;
}

export const listWorkflows = () => request<WorkflowSummary[]>("/workflows");
export const getWorkflow = (id: string) => request<WorkflowDetail>(`/workflows/${id}`);
export const createWorkflow = (body: {
  name: string;
  description?: string;
  definition: PipelineDefinition;
}) =>
  request<WorkflowDetail>("/workflows", { method: "POST", body: JSON.stringify(body) });

// -------- Jobs --------
export interface JobSummary {
  id: string;
  status: string;
  engine: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result_dataset_id: string | null;
}
export interface JobEvent {
  seq: number;
  kind: string;
  payload: any;
  created_at: string;
}
export interface JobDetail extends JobSummary {
  definition: PipelineDefinition;
  events: JobEvent[];
}

export const createJob = (definition: PipelineDefinition, workflow_id?: string) =>
  request<JobSummary>("/jobs", {
    method: "POST",
    body: JSON.stringify({ definition, workflow_id }),
  });
export const listJobs = () => request<JobSummary[]>("/jobs");
export const getJob = (id: string) => request<JobDetail>(`/jobs/${id}`);
export const cancelJob = (id: string) =>
  request<JobSummary>(`/jobs/${id}/cancel`, { method: "POST" });

export function jobEventsUrl(id: string, after = 0): string {
  return `${resolveApiBase()}/jobs/${id}/events?after=${after}`;
}

// -------- Direct upload to S3-compatible store --------
export async function uploadPart(url: string, blob: Blob): Promise<string> {
  // S3/MinIO presigned URLs are absolute; the local-store upload endpoints
  // are returned relative to the API base (e.g. "/uploads/<id>/<part>").
  const target = /^https?:\/\//i.test(url) ? url : `${resolveApiBase()}${url}`;
  const res = await fetch(target, { method: "PUT", body: blob });
  if (!res.ok) {
    throw new Error(`Part upload failed: ${res.status} ${await res.text()}`);
  }
  const etag = res.headers.get("ETag") || res.headers.get("etag");
  if (!etag) throw new Error("No ETag returned by storage");
  return etag.replaceAll('"', "");
}

export async function exportDataset(
  datasetId: string,
  fmt: "parquet" | "csv" | "json" | "txt",
): Promise<Blob> {
  const apiBase = resolveApiBase();
  const res = await fetch(`${apiBase}/datasets/${datasetId}/export?fmt=${fmt}`, {
    method: "GET",
    headers: {
      Authorization: tokenFromStorage(),
    },
  });
  if (!res.ok) {
    throw new Error(`Export failed: ${res.status} ${await res.text()}`);
  }
  return await res.blob();
}
