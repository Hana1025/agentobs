import createClient from "openapi-fetch";
import type { paths } from "./schema";

export const api = createClient<paths>({ baseUrl: "" });

export class ApiError extends Error {
  status: number;
  detail?: string;
  constructor(status: number, detail?: string) {
    super(detail ?? `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

/** 把 openapi-fetch 的 {data, error, response} 统一成抛错。 */
export function unwrap<T>(r: { data?: T; error?: unknown; response: Response }): T {
  if (r.error !== undefined || !r.response.ok) {
    const e = r.error as { detail?: string } | undefined;
    throw new ApiError(r.response.status, e?.detail);
  }
  return r.data as T;
}
