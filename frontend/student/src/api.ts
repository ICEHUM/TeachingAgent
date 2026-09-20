export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function api<T>(path: string, userId: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-User-Id": userId, ...(init.headers || {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "请求失败" }));
    const detail = body.detail;
    const code = typeof detail === "object" ? detail.code : String(detail || "request_failed");
    const message = typeof detail === "object" ? detail.message : String(detail || "请求失败");
    throw new ApiError(response.status, code, message);
  }
  return response.json() as Promise<T>;
}

export function newOperation(prefix: string): string {
  return `${prefix}:${crypto.randomUUID()}`;
}
