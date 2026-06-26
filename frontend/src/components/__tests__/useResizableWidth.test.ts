import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useResizableWidth } from "../hooks/useResizableWidth";

const STORAGE_KEY = "test.panel.width";
const MIN = 320;
const MAX = 720;
const DEFAULT = 360;

function makeOpts(overrides: Partial<Parameters<typeof useResizableWidth>[0]> = {}) {
  return { storageKey: STORAGE_KEY, min: MIN, max: MAX, defaultWidth: DEFAULT, ...overrides };
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.style.removeProperty("--process-panel-width");
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useResizableWidth – initial state", () => {
  it("uses defaultWidth when nothing is stored", () => {
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    expect(result.current.width).toBe(DEFAULT);
    expect(result.current.liveWidth).toBe(DEFAULT);
  });

  it("reads a valid stored value", () => {
    localStorage.setItem(STORAGE_KEY, "480");
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    expect(result.current.width).toBe(480);
  });

  it("clamps an out-of-range stored value to max", () => {
    localStorage.setItem(STORAGE_KEY, "900");
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    expect(result.current.width).toBe(MAX);
  });

  it("clamps an out-of-range stored value to min", () => {
    localStorage.setItem(STORAGE_KEY, "100");
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    expect(result.current.width).toBe(MIN);
  });

  it("falls back to defaultWidth for invalid stored value", () => {
    localStorage.setItem(STORAGE_KEY, "not-a-number");
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    expect(result.current.width).toBe(DEFAULT);
  });

  it("publishes the CSS variable on mount", () => {
    renderHook(() => useResizableWidth(makeOpts()));
    expect(document.documentElement.style.getPropertyValue("--process-panel-width")).toBe(`${DEFAULT}px`);
  });
});

describe("useResizableWidth – drag lifecycle", () => {
  it("startDrag sets isDragging and liveWidth", () => {
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    act(() => result.current.startDrag(400));
    expect(result.current.isDragging).toBe(true);
    expect(result.current.liveWidth).toBe(400);
  });

  it("updateDrag clamps and updates liveWidth", () => {
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    act(() => {
      result.current.startDrag(400);
      result.current.updateDrag(800);
    });
    expect(result.current.liveWidth).toBe(MAX);
  });

  it("endDrag commits liveWidth, persists, clears isDragging", () => {
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    act(() => {
      result.current.startDrag(400);
      result.current.updateDrag(500);
      result.current.endDrag();
    });
    expect(result.current.isDragging).toBe(false);
    expect(result.current.width).toBe(500);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("500");
  });
});

describe("useResizableWidth – keyboard / imperative", () => {
  it("setCommittedWidth clamps and persists", () => {
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    act(() => result.current.setCommittedWidth(200));
    expect(result.current.width).toBe(MIN);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(String(MIN));
  });

  it("resetToDefault restores defaultWidth", () => {
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    act(() => {
      result.current.setCommittedWidth(600);
      result.current.resetToDefault();
    });
    expect(result.current.width).toBe(DEFAULT);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(String(DEFAULT));
  });
});

describe("useResizableWidth – localStorage unavailable", () => {
  it("degrades gracefully when localStorage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("unavail"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("unavail"); });
    const { result } = renderHook(() => useResizableWidth(makeOpts()));
    expect(result.current.width).toBe(DEFAULT);
    act(() => result.current.setCommittedWidth(450));
    expect(result.current.width).toBe(450);
  });
});
