// Thin fetch wrapper for the TrackSound API. All paths are absolute (`/api/...`) so the app
// works both behind the Vite dev proxy and when served by the backend at `/`.

export class ApiError extends Error {
  status: number;
  detail: unknown;
  path: string;
  constructor(status: number, detail: unknown, path: string) {
    super(describeDetail(detail) || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
    this.path = path;
  }
  /** The endpoint does not exist (yet) on this backend, rather than a missing resource. */
  get unavailable(): boolean {
    if (this.status === 405 || this.status === 501) return true;
    if (this.status === 404) {
      const d = typeof this.detail === "string" ? this.detail : "";
      // FastAPI's own 404 for unknown routes (and the SPA fallback for /api/*) vs our "not found: <id>"
      return d === "Not Found" || d === "not found" || d === "";
    }
    return false;
  }
}

/** Turn a FastAPI `detail` (string, or a list of validation errors) into readable text. */
export function describeDetail(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc)
            ? ((d as { loc: unknown[] }).loc.filter((x) => x !== "body").join("."))
            : "";
          // pydantic prefixes our own messages with "Value error, "
          const msg = String((d as { msg: string }).msg).replace(/^Value error, /, "");
          return loc ? `${loc}: ${msg}` : msg;
        }
        return String(d);
      })
      .join("; ");
  }
  if (typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return String(detail);
    }
  }
  return String(detail);
}

export function errorMessage(err: unknown): string {
  if (!err) return "";
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function isUnavailable(err: unknown): boolean {
  return err instanceof ApiError && err.unavailable;
}

async function parseError(res: Response, path: string): Promise<ApiError> {
  let detail: unknown = res.statusText;
  try {
    const text = await res.text();
    if (text) {
      try {
        const j = JSON.parse(text);
        detail = j && typeof j === "object" && "detail" in j ? j.detail : j;
      } catch {
        detail = text.slice(0, 300);
      }
    } else {
      detail = "";
    }
  } catch {
    /* ignore */
  }
  return new ApiError(res.status, detail, path);
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, q?: Query): string {
  if (!q) return path;
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `${path}?${s}` : path;
}

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { method, signal, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (e) {
    if ((e as Error)?.name === "AbortError") throw e;
    throw new ApiError(0, "Cannot reach the TrackSound server. Is `tracksound serve` running?", path);
  }
  if (!res.ok) throw await parseError(res, path);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

export const api = {
  get: <T>(path: string, q?: Query, signal?: AbortSignal) => request<T>("GET", withQuery(path, q), undefined, signal),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  del: (path: string) => request<void>("DELETE", path),
  form: <T>(path: string, form: FormData) => request<T>("POST", path, form),
};

export type UploadHandle<T> = { promise: Promise<T>; abort: () => void };

/** POST a multipart form with real upload progress (fetch can't report upload progress). */
export function uploadWithProgress<T>(
  path: string,
  form: FormData,
  onProgress: (loaded: number, total: number) => void,
): UploadHandle<T> {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<T>((resolve, reject) => {
    xhr.open("POST", path);
    xhr.responseType = "text";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = xhr.responseText;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as T);
      else {
        const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : body;
        reject(new ApiError(xhr.status, detail, path));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload failed: the server closed the connection", path));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}
