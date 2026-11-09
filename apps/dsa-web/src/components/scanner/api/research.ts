import { useEffect, useState } from "react";
import { api, ApiError, displayTimezone, type Data } from "./client";
export type ResearchJobStatus =
  | "QUEUED"
  | "RUNNING"
  | "CANCEL_REQUESTED"
  | "CANCELLED"
  | "SUCCEEDED"
  | "PARTIAL"
  | "FAILED"
  | "SKIPPED";
export type ReportState =
  "NOT_READY" | "INDEXING" | "READY" | "CORRUPT" | "UNSUPPORTED_VERSION";
export type DataKind = "SYNTHETIC" | "REAL" | "UNKNOWN";
export interface DatasetDTO extends Data {
  id: string;
  dataset_id: string;
  name: string;
  data_kind: DataKind;
  snapshot_hash: string;
  coverage: { start_date: string; end_date: string; instruments: number };
  evidence: { rth: boolean; pit: boolean };
  default_draft: Data;
}
export interface CatalogDTO extends Data {
  datasets: DatasetDTO[];
  runtime_ready: boolean;
  next_cursor: number | null;
}
export interface ResearchRunDTO extends Data {
  id: string;
  run_id: string;
  name: string;
  status: ResearchJobStatus;
  report_state: ReportState;
  data_kind: DataKind;
  phase: string;
  processed: number | null;
  total: number | null;
  heartbeat_at: string | null;
  created_at: string;
  attempt: number;
  attempts: Data[];
  external: boolean;
  engine_job_id: string | null;
  start_date: string;
  end_date: string;
  draft?: Data;
  dataset_id?: string;
}
export interface ResearchListDTO extends Data {
  items: ResearchRunDTO[];
  next_cursor: number | null;
  total: number;
}
export interface ValidationDTO extends Data {
  id: string;
  validation_id: string;
  status: ResearchJobStatus;
  result_status?: "VALID" | "INVALID";
  config_digest: string;
  manifest: Data;
  issues: { field: string; severity: string; code: string }[];
  capabilities: { can_run: boolean };
}
export interface DistributionDTO extends Data {
  status: "NO_SAMPLES" | "INSUFFICIENT" | "SUFFICIENT";
  completed: number;
  pending: number;
  unresolved: number;
  excluded: number;
  net: {
    mean: string | null;
    p05: string | null;
    p25: string | null;
    p50: string | null;
    p75: string | null;
    p95: string | null;
  };
  gross: {
    mean: string | null;
    p05: string | null;
    p25: string | null;
    p50: string | null;
    p75: string | null;
    p95: string | null;
  };
}
export interface StatisticsDTO extends Data {
  items: DistributionDTO[];
  filters: Data;
  report_hash: string;
  query_version: string;
  denominators: { label_rows: number; economic_events: number };
}
export interface ReportDTO extends Data {
  api_schema_version: string;
  report_schema_version: number;
  report_hash: string;
  business_output_hash: string;
  identity: Data;
  coverage: Data;
  review: Data;
  data_kind: DataKind;
  defaults: Data;
  limitations: string[];
  biases: string[];
}
export interface RowsDTO extends Data {
  section: string;
  items: Data[];
  total: number;
  next_cursor: number | null;
  report_hash: string;
  filters: Data;
}
export interface ChartDTO extends Data {
  availability: "AVAILABLE" | "UNAVAILABLE";
  report_hash: string;
  reason?: string;
  evaluation_clock?: string;
  bars?: (Data & {
    market_date: string;
    revision: number;
    available_at: string;
    open: string;
    high: string;
    low: string;
    close: string;
  })[];
  markers?: Data[];
  thresholds?: Data[];
}
export interface VariantPlanDTO extends Data {
  available: boolean;
  reason?: string | null;
  choices?: (Data & {
    rule_id: string;
    parameter: string;
    baseline: number;
    values: number[];
    integer: boolean;
  })[];
  items: (Data &
    Pick<
      ResearchRunDTO,
      "id" | "name" | "created_at" | "status" | "report_state"
    >)[];
  next_cursor?: number | null;
  validations?: Data[];
  next_validation_cursor?: number | null;
}
export interface ProbeHistoryDTO extends Data {
  items: (Data & {
    id: string;
    status: "PASS" | "FAIL" | "SKIPPED" | "VERSION_UNAVAILABLE" | null;
    expected_hash: string | null;
    actual_hash: string | null;
    network_calls: number | null;
    account_writes: number | null;
  })[];
  next_cursor: number | null;
  total: number;
}
export const base = "/api/rule-validation";
export const terminal = (d: Data) =>
  ["SUCCEEDED", "PARTIAL", "FAILED", "SKIPPED", "CANCELLED"].includes(d.status);
export function useResearch<T extends Data = Data>(
  path: string | null,
  polling = false,
) {
  const [state, setState] = useState<{
    data: T | null;
    error: string;
    loading: boolean;
    asOf: string;
  }>({ data: null, error: "", loading: !!path, asOf: "" });
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    setState({ data: null, error: "", loading: !!path, asOf: "" });
    async function read() {
      if (!path) return;
      try {
        const data = (await api(
          base + path,
          "GET",
          undefined,
          undefined,
          controller.signal,
        )) as T;
        if (controller.signal.aborted) return;
        failures = 0;
        setState({
          data,
          error: "",
          loading: false,
          asOf: new Date().toISOString(),
        });
        const active = data.status
          ? !terminal(data)
          : data.items?.some((r: Data) => !terminal(r));
        if (polling && active)
          timer = setTimeout(read, document.hidden ? 15000 : 2500);
      } catch (e) {
        if (controller.signal.aborted) return;
        setState((s) => ({
          ...s,
          loading: false,
          error: (e as Error).message,
        }));
        const retryable =
          !(e instanceof ApiError) ||
          [
            "DEPENDENCY_UNAVAILABLE",
            "RESEARCH_NOT_INITIALIZED",
            "REQUEST_FAILED",
            "RATE_LIMITED",
            "QUEUE_FULL",
          ].includes(e.code);
        if (polling && retryable)
          timer = setTimeout(read, Math.min(30000, 3000 * 2 ** failures++));
      }
    }
    read();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [path, polling]);
  return state;
}
export const mutateResearch = (path: string, body: Data, key: string) =>
  api(base + path, "POST", body, key);
export const wallTime = (s: string | null) =>
  s
    ? new Date(s).toLocaleString("zh-CN", {
        timeZone: displayTimezone,
        hour12: false,
      })
    : "—";
export const ratio = (v: unknown) =>
  v == null ? "—" : `${(Number(v) * 100).toFixed(3)}%`;
export function params(values: Data) {
  const p = new URLSearchParams();
  Object.entries(values).forEach(([k, v]) => {
    if (v !== "" && v != null) p.set(k, String(v));
  });
  return p.toString();
}
