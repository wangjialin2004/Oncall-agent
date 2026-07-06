import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { AlertCircle, Gauge, Plus, RefreshCw, Trash2 } from "lucide-react";

import {
  deleteBaseline,
  getService,
  listServices,
  upsertBaseline,
  upsertService,
} from "../api/serviceKnowledgeApi";
import type { MetricName, ServiceDetail, ServiceSummary } from "../types/serviceKnowledge";

const METRIC_OPTIONS: { value: MetricName; label: string; unit: string }[] = [
  { value: "cpu", label: "CPU", unit: "%" },
  { value: "memory", label: "内存", unit: "%" },
  { value: "qps", label: "QPS", unit: "" },
  { value: "p95", label: "延迟 P95", unit: "ms" },
];

const ENV_OPTIONS = ["prod", "staging", "dev"];

function serviceKey(name: string, environment: string): string {
  return `${name}@${environment}`;
}

export function ServiceBaselineManager() {
  const [services, setServices] = useState<ServiceSummary[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [detail, setDetail] = useState<ServiceDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showNewService, setShowNewService] = useState(false);

  async function refreshList(selectAfter?: { name: string; environment: string }) {
    setLoading(true);
    setError("");
    try {
      const list = await listServices();
      setServices(list);
      if (selectAfter) {
        await openService(selectAfter.name, selectAfter.environment);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function openService(name: string, environment: string) {
    setError("");
    setSelectedKey(serviceKey(name, environment));
    try {
      setDetail(await getService(name, environment));
    } catch (e) {
      setDetail(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    void refreshList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <section className="baseline-manager">
      <header className="baseline-header">
        <div className="baseline-title">
          <Gauge size={18} aria-hidden="true" />
          <h2>服务基线管理</h2>
        </div>
        <button className="icon-button" type="button" onClick={() => void refreshList()} title="刷新">
          <RefreshCw size={15} aria-hidden="true" />
          刷新
        </button>
      </header>

      {error && (
        <div className="baseline-error" role="alert">
          <AlertCircle size={15} aria-hidden="true" />
          {error}
        </div>
      )}

      <div className="baseline-body">
        <aside className="baseline-list">
          <button
            className="sidebar-action"
            type="button"
            onClick={() => setShowNewService((v) => !v)}
          >
            <Plus size={15} aria-hidden="true" />
            新建服务
          </button>

          {showNewService && (
            <NewServiceForm
              onCreated={async (name, environment) => {
                setShowNewService(false);
                await refreshList({ name, environment });
              }}
              onError={setError}
            />
          )}

          {loading && services.length === 0 ? (
            <p className="baseline-hint">加载中...</p>
          ) : services.length === 0 ? (
            <p className="baseline-hint">暂无服务，请先新建。</p>
          ) : (
            <ul>
              {services.map((s) => {
                const key = serviceKey(s.service_name, s.environment);
                return (
                  <li key={key}>
                    <button
                      type="button"
                      className={`baseline-list-item${selectedKey === key ? " active" : ""}`}
                      onClick={() => void openService(s.service_name, s.environment)}
                    >
                      <span className="baseline-list-name">{s.service_name}</span>
                      <span className="baseline-list-env">{s.environment}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <div className="baseline-detail">
          {detail ? (
            <ServiceDetailView
              detail={detail}
              onChanged={() =>
                void openService(detail.service_name, detail.environment)
              }
              onError={setError}
            />
          ) : (
            <p className="baseline-hint">从左侧选择一个服务以查看/编辑基线。</p>
          )}
        </div>
      </div>
    </section>
  );
}

type NewServiceFormProps = {
  onCreated: (name: string, environment: string) => void | Promise<void>;
  onError: (message: string) => void;
};

function NewServiceForm({ onCreated, onError }: NewServiceFormProps) {
  const [name, setName] = useState("");
  const [environment, setEnvironment] = useState("prod");
  const [ownerTeam, setOwnerTeam] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) {
      onError("服务名不能为空");
      return;
    }
    setSaving(true);
    try {
      await upsertService(name.trim(), {
        environment,
        owner_team: ownerTeam.trim(),
        description: description.trim(),
      });
      await onCreated(name.trim(), environment);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="baseline-form" onSubmit={submit}>
      <div className="login-field">
        <label className="login-label" htmlFor="new-service-name">
          服务名
        </label>
        <input
          id="new-service-name"
          className="login-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="checkout-api"
          disabled={saving}
        />
      </div>
      <div className="login-field">
        <label className="login-label" htmlFor="new-service-env">
          环境
        </label>
        <select
          id="new-service-env"
          className="login-input"
          value={environment}
          onChange={(e) => setEnvironment(e.target.value)}
          disabled={saving}
        >
          {ENV_OPTIONS.map((env) => (
            <option key={env} value={env}>
              {env}
            </option>
          ))}
        </select>
      </div>
      <div className="login-field">
        <label className="login-label" htmlFor="new-service-owner">
          归属团队
        </label>
        <input
          id="new-service-owner"
          className="login-input"
          value={ownerTeam}
          onChange={(e) => setOwnerTeam(e.target.value)}
          placeholder="payments"
          disabled={saving}
        />
      </div>
      <div className="login-field">
        <label className="login-label" htmlFor="new-service-desc">
          描述
        </label>
        <input
          id="new-service-desc"
          className="login-input"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          disabled={saving}
        />
      </div>
      <button className="login-submit" type="submit" disabled={saving || !name.trim()}>
        {saving ? "保存中..." : "创建服务"}
      </button>
    </form>
  );
}

type ServiceDetailViewProps = {
  detail: ServiceDetail;
  onChanged: () => void | Promise<void>;
  onError: (message: string) => void;
};

function ServiceDetailView({ detail, onChanged, onError }: ServiceDetailViewProps) {
  return (
    <>
      <div className="baseline-detail-head">
        <h3>
          {detail.service_name}
          <span className="baseline-list-env">{detail.environment}</span>
        </h3>
        <p className="baseline-hint">
          归属：{detail.owner_team || detail.owner_user || "未设置"}
          {detail.description ? ` · ${detail.description}` : ""}
        </p>
      </div>

      <table className="baseline-table">
        <thead>
          <tr>
            <th>指标</th>
            <th>下限</th>
            <th>上限</th>
            <th>单位</th>
            <th>采样窗口</th>
            <th aria-label="操作" />
          </tr>
        </thead>
        <tbody>
          {detail.baselines.length === 0 ? (
            <tr>
              <td colSpan={6} className="baseline-hint">
                暂无基线，请在下方新增。
              </td>
            </tr>
          ) : (
            detail.baselines.map((b) => (
              <tr key={b.metric_name}>
                <td>{b.metric_name}</td>
                <td>{b.min_value}</td>
                <td>{b.max_value}</td>
                <td>{b.unit}</td>
                <td>{b.sample_window}</td>
                <td>
                  <button
                    type="button"
                    className="icon-button"
                    title="删除"
                    onClick={async () => {
                      try {
                        await deleteBaseline(detail.service_name, b.metric_name, detail.environment);
                        await onChanged();
                      } catch (e) {
                        onError(e instanceof Error ? e.message : String(e));
                      }
                    }}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      <BaselineForm detail={detail} onSaved={onChanged} onError={onError} />
    </>
  );
}

type BaselineFormProps = {
  detail: ServiceDetail;
  onSaved: () => void | Promise<void>;
  onError: (message: string) => void;
};

function BaselineForm({ detail, onSaved, onError }: BaselineFormProps) {
  const [metric, setMetric] = useState<MetricName>("cpu");
  const [minValue, setMinValue] = useState("");
  const [maxValue, setMaxValue] = useState("");
  const [unit, setUnit] = useState("%");
  const [sampleWindow, setSampleWindow] = useState("7d");
  const [saving, setSaving] = useState(false);

  const defaultUnit = useMemo(
    () => METRIC_OPTIONS.find((m) => m.value === metric)?.unit ?? "",
    [metric],
  );

  function changeMetric(value: MetricName) {
    setMetric(value);
    setUnit(METRIC_OPTIONS.find((m) => m.value === value)?.unit ?? "");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const min = Number(minValue);
    const max = Number(maxValue);
    if (minValue === "" || maxValue === "" || Number.isNaN(min) || Number.isNaN(max)) {
      onError("上下限必须为数字");
      return;
    }
    if (min > max) {
      onError("下限不能大于上限");
      return;
    }
    setSaving(true);
    try {
      await upsertBaseline(detail.service_name, {
        service_name: detail.service_name,
        environment: detail.environment,
        metric_name: metric,
        min_value: min,
        max_value: max,
        unit: unit || defaultUnit,
        sample_window: sampleWindow,
      });
      setMinValue("");
      setMaxValue("");
      await onSaved();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="baseline-form baseline-form-row" onSubmit={submit}>
      <div className="login-field">
        <label className="login-label" htmlFor="baseline-metric">
          指标
        </label>
        <select
          id="baseline-metric"
          className="login-input"
          value={metric}
          onChange={(e) => changeMetric(e.target.value)}
          disabled={saving}
        >
          {METRIC_OPTIONS.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
      </div>
      <div className="login-field">
        <label className="login-label" htmlFor="baseline-min">
          下限
        </label>
        <input
          id="baseline-min"
          className="login-input"
          type="number"
          step="any"
          value={minValue}
          onChange={(e) => setMinValue(e.target.value)}
          disabled={saving}
        />
      </div>
      <div className="login-field">
        <label className="login-label" htmlFor="baseline-max">
          上限
        </label>
        <input
          id="baseline-max"
          className="login-input"
          type="number"
          step="any"
          value={maxValue}
          onChange={(e) => setMaxValue(e.target.value)}
          disabled={saving}
        />
      </div>
      <div className="login-field">
        <label className="login-label" htmlFor="baseline-unit">
          单位
        </label>
        <input
          id="baseline-unit"
          className="login-input"
          value={unit}
          onChange={(e) => setUnit(e.target.value)}
          disabled={saving}
        />
      </div>
      <div className="login-field">
        <label className="login-label" htmlFor="baseline-window">
          采样窗口
        </label>
        <input
          id="baseline-window"
          className="login-input"
          value={sampleWindow}
          onChange={(e) => setSampleWindow(e.target.value)}
          disabled={saving}
        />
      </div>
      <button className="login-submit" type="submit" disabled={saving}>
        {saving ? "保存中..." : "新增/更新基线"}
      </button>
    </form>
  );
}
