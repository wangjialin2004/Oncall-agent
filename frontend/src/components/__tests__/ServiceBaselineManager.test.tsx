import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ServiceBaselineManager } from "../ServiceBaselineManager";

// Avoid crypto.randomUUID in jsdom; we only need a stable owner token.
vi.mock("../../api/agentStream", () => ({
  getSessionOwnerToken: () => "owner-test",
}));

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ code: status, message: "ok", data }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SUMMARY = {
  service_name: "checkout-api",
  environment: "prod",
  owner_team: "payments",
  owner_user: "",
  description: "",
  enabled: true,
  updated_at: "",
};

const DETAIL = { ...SUMMARY, baselines: [], relations: [] };

describe("ServiceBaselineManager", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("lists services, opens one, and PUTs a baseline with the correct body", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/memory/services" && method === "GET") {
        return jsonResponse([SUMMARY]);
      }
      if (url.startsWith("/api/memory/services/checkout-api?") && method === "GET") {
        return jsonResponse(DETAIL);
      }
      if (url.includes("/api/memory/services/checkout-api/baselines") && method === "PUT") {
        return jsonResponse({ service_name: "checkout-api" });
      }
      return jsonResponse(null, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ServiceBaselineManager />);

    // Service from listServices() shows in the left list.
    const item = await screen.findByText("checkout-api");
    await user.click(item);

    // Detail loaded via getService().
    expect(await screen.findByText(/归属：payments/)).toBeInTheDocument();

    // Fill and submit the baseline form.
    await user.selectOptions(screen.getByLabelText("指标"), "cpu");
    await user.type(screen.getByLabelText("下限"), "10");
    await user.type(screen.getByLabelText("上限"), "70");
    await user.click(screen.getByRole("button", { name: "新增/更新基线" }));

    const putCall = fetchMock.mock.calls.find(
      ([u, i]) =>
        typeof u === "string" && u.includes("/baselines") && (i as RequestInit)?.method === "PUT",
    );
    expect(putCall).toBeTruthy();
    const body = JSON.parse((putCall![1] as RequestInit).body as string);
    expect(body).toMatchObject({
      service_name: "checkout-api",
      environment: "prod",
      metric_name: "cpu",
      min_value: 10,
      max_value: 70,
    });
  });

  it("blocks submission when min > max", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (url === "/api/memory/services" && method === "GET") {
        return jsonResponse([SUMMARY]);
      }
      if (url.startsWith("/api/memory/services/checkout-api?") && method === "GET") {
        return jsonResponse(DETAIL);
      }
      return jsonResponse(null, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<ServiceBaselineManager />);

    await user.click(await screen.findByText("checkout-api"));
    await screen.findByText(/归属：payments/);

    await user.type(screen.getByLabelText("下限"), "90");
    await user.type(screen.getByLabelText("上限"), "10");
    await user.click(screen.getByRole("button", { name: "新增/更新基线" }));

    expect(await screen.findByText("下限不能大于上限")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([, i]) => (i as RequestInit)?.method === "PUT")).toBe(false);
  });
});
