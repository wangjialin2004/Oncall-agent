/** Service knowledge / baseline types — mirror app/api/memory.py response shapes. */

export type MetricName = "cpu" | "memory" | "qps" | "p95" | string;

export type Baseline = {
  metric_name: MetricName;
  min_value: number;
  max_value: number;
  unit: string;
  sample_window: string;
};

export type ServiceRelation = {
  source_service: string;
  target_service: string;
  relation_type: string;
  environment: string;
};

/** Row shape returned by GET /memory/services (list). */
export type ServiceSummary = {
  service_name: string;
  environment: string;
  owner_team: string;
  owner_user: string;
  description: string;
  enabled: boolean;
  updated_at: string;
};

/** Full shape returned by GET /memory/services/{name}. */
export type ServiceDetail = ServiceSummary & {
  baselines: Baseline[];
  relations: ServiceRelation[];
};

/** Body for PUT /memory/services/{name}. */
export type ServiceUpsertPayload = {
  environment: string;
  owner_team?: string;
  owner_user?: string;
  description?: string;
  enabled?: boolean;
};

/** Body for PUT /memory/services/{name}/baselines. */
export type BaselineUpsertPayload = {
  service_name: string;
  environment: string;
  metric_name: MetricName;
  min_value: number;
  max_value: number;
  unit?: string;
  sample_window?: string;
};
