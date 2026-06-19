import { getSessionOwnerToken } from "./agentStream";
import type {
  BaselineUpsertPayload,
  ServiceDetail,
  ServiceSummary,
  ServiceUpsertPayload,
} from "../types/serviceKnowledge";

/** Shared auth headers — baseline/service endpoints are public but follow the same convention. */
function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Session-Owner": getSessionOwnerToken(),
  };
  const authToken = localStorage.getItem("authToken");
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }
  return headers;
}

async function readData<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  const json = (await response.json().catch(() => null)) as { data?: T } | null;
  if (!json || json.data === undefined) {
    throw new Error("响应缺少 data 字段");
  }
  return json.data;
}

export async function listServices(environment?: string): Promise<ServiceSummary[]> {
  const query = environment ? `?environment=${encodeURIComponent(environment)}` : "";
  const response = await fetch(`/api/memory/services${query}`, { headers: authHeaders() });
  return readData<ServiceSummary[]>(response);
}

export async function getService(
  serviceName: string,
  environment = "prod",
): Promise<ServiceDetail> {
  const response = await fetch(
    `/api/memory/services/${encodeURIComponent(serviceName)}?environment=${encodeURIComponent(environment)}`,
    { headers: authHeaders() },
  );
  return readData<ServiceDetail>(response);
}

export async function upsertService(
  serviceName: string,
  payload: ServiceUpsertPayload,
): Promise<void> {
  const response = await fetch(`/api/memory/services/${encodeURIComponent(serviceName)}`, {
    method: "PUT",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`保存服务失败 HTTP ${response.status}`);
  }
}

export async function upsertBaseline(
  serviceName: string,
  payload: BaselineUpsertPayload,
): Promise<void> {
  const response = await fetch(
    `/api/memory/services/${encodeURIComponent(serviceName)}/baselines`,
    {
      method: "PUT",
      headers: authHeaders(),
      body: JSON.stringify(payload),
    },
  );
  if (!response.ok) {
    throw new Error(`保存基线失败 HTTP ${response.status}`);
  }
}

export async function deleteBaseline(
  serviceName: string,
  metricName: string,
  environment = "prod",
): Promise<void> {
  const response = await fetch(
    `/api/memory/services/${encodeURIComponent(serviceName)}/baselines/${encodeURIComponent(metricName)}?environment=${encodeURIComponent(environment)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  if (!response.ok) {
    throw new Error(`删除基线失败 HTTP ${response.status}`);
  }
}
