import type {
  BaselineUpsertPayload,
  ServiceDetail,
  ServiceSummary,
  ServiceUpsertPayload,
} from "../types/serviceKnowledge";
import { apiFetch } from "./httpClient";

async function readData<T>(response: Response): Promise<T> {
  const json = (await response.json().catch(() => null)) as { data?: T } | null;
  if (!json || json.data === undefined) {
    throw new Error("响应缺少 data 字段");
  }
  return json.data;
}

export async function listServices(environment?: string): Promise<ServiceSummary[]> {
  const query = environment ? `?environment=${encodeURIComponent(environment)}` : "";
  const response = await apiFetch(`/api/memory/services${query}`, undefined, {
    errorMessage: "List services failed",
  });
  return readData<ServiceSummary[]>(response);
}

export async function getService(
  serviceName: string,
  environment = "prod",
): Promise<ServiceDetail> {
  const response = await apiFetch(
    `/api/memory/services/${encodeURIComponent(serviceName)}?environment=${encodeURIComponent(environment)}`,
    undefined,
    { errorMessage: "Get service failed" },
  );
  return readData<ServiceDetail>(response);
}

export async function upsertService(
  serviceName: string,
  payload: ServiceUpsertPayload,
): Promise<void> {
  await apiFetch(
    `/api/memory/services/${encodeURIComponent(serviceName)}`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
    { errorMessage: "保存服务失败" },
  );
}

export async function upsertBaseline(
  serviceName: string,
  payload: BaselineUpsertPayload,
): Promise<void> {
  await apiFetch(
    `/api/memory/services/${encodeURIComponent(serviceName)}/baselines`,
    {
      method: "PUT",
      body: JSON.stringify(payload),
    },
    { errorMessage: "保存基线失败" },
  );
}

export async function deleteBaseline(
  serviceName: string,
  metricName: string,
  environment = "prod",
): Promise<void> {
  await apiFetch(
    `/api/memory/services/${encodeURIComponent(serviceName)}/baselines/${encodeURIComponent(metricName)}?environment=${encodeURIComponent(environment)}`,
    { method: "DELETE" },
    { errorMessage: "删除基线失败" },
  );
}
