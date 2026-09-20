export class ApiError extends Error {
  status: number; code: string;
  constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
}
export async function api<T>(path: string, userId: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", "X-User-Id": userId, ...(init.headers || {}) } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "请求失败" }));
    const detail = body.detail;
    throw new ApiError(response.status, typeof detail === "object" ? detail.code : String(detail), typeof detail === "object" ? detail.message : String(detail));
  }
  return response.json() as Promise<T>;
}
export const newOperation = (prefix: string) => `${prefix}:${crypto.randomUUID()}`;
