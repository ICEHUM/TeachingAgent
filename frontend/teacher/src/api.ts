export class ApiError extends Error {
  status: number; code: string;
  constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
}
export async function api<T>(path: string, userId: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, headers: authHeaders(userId, init.headers) });
  if (!response.ok) await throwApiError(response);
  return response.json() as Promise<T>;
}
export const newOperation = (prefix: string) => `${prefix}:${crypto.randomUUID()}`;

function authHeaders(userId: string, extra?: HeadersInit): HeadersInit {
  return { "Content-Type": "application/json", "X-User-Id": userId, ...(extra || {}) };
}

async function throwApiError(response: Response): Promise<never> {
  const body = await response.json().catch(() => ({ detail: "请求失败" }));
  const detail = (body as { detail?: unknown }).detail;
  throw new ApiError(
    response.status,
    typeof detail === "object" && detail ? String((detail as { code?: string }).code) : String(detail),
    typeof detail === "object" && detail ? String((detail as { message?: string }).message) : String(detail),
  );
}

/** Reads a server-sent event stream and hands each decoded event to the caller. */
export async function apiStream<T>(path: string, userId: string, init: RequestInit, onEvent: (event: string, data: T) => void): Promise<void> {
  const response = await fetch(path, { ...init, headers: authHeaders(userId, { Accept: "text/event-stream", ...(init.headers || {}) }) });
  if (!response.ok) await throwApiError(response);
  if (!response.body) throw new ApiError(500, "stream_unsupported", "当前浏览器不支持流式回答");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const lines = frame.split("\n");
      const name = lines.find((line) => line.startsWith("event:"))?.slice(6).trim() || "message";
      const payload = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
      if (payload) onEvent(name, JSON.parse(payload) as T);
    }
  }
}
