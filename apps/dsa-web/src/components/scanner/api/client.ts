// The imported versioned API uses heterogeneous evidence and rule payloads.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Data = Record<string, any>;
let csrf = "";
export let displayTimezone = "Asia/Singapore";
export const scannerApiPath = (path: string) => path.startsWith("/api/v1/scanner/") ? path : "/api/v1/scanner" + path.replace(/^\/api(?=\/)/, "");
export class ApiError extends Error {
  code: string;
  details: Data;
  constructor(
    code: string,
    message: string,
    details: Data = {},
  ) {
    super(message);
    this.code = code;
    this.details = details;
  }
}
export async function api(
  path: string,
  method = "GET",
  body?: Data,
  key?: string,
  signal?: AbortSignal,
): Promise<Data> {
  const headers: Record<string, string> = {};
  if (body) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    headers["X-CSRF-Token"] = csrf;
    headers["Idempotency-Key"] = key || crypto.randomUUID();
    if (body?.expected_revision)
      headers["If-Match"] = String(body.expected_revision);
  }
  const response = await fetch(scannerApiPath(path), {
    method,
    signal,
    headers,
    credentials: "same-origin",
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json();
  if (response.status === 401)
    window.dispatchEvent(new Event("scanner-session-expired"));
  if (!response.ok)
    throw new ApiError(
      result.error?.code || "REQUEST_FAILED",
      result.error?.message || "请求失败",
      result.error?.details,
    );
  if (result.data?.csrf_token) csrf = result.data.csrf_token;
  if (
    path.split("?")[0] === "/api/configs/preferences" &&
    result.data?.value?.display_timezone
  )
    displayTimezone = result.data.value.display_timezone;
  return result.data;
}
