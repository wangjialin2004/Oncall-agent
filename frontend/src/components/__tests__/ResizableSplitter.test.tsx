import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
// Note: drag tests require PointerEvent which jsdom does not support.
// Drag state machine is covered in useResizableWidth.test.ts.

import { ResizableSplitter } from "../ResizableSplitter";

afterEach(() => {
  cleanup();
  localStorage.clear();
  document.documentElement.style.removeProperty("--process-panel-width");
});

function renderSplitter(overrides: Partial<React.ComponentProps<typeof ResizableSplitter>> = {}) {
  return render(
    <ResizableSplitter
      min={320}
      max={720}
      defaultWidth={360}
      targetSelector=".process-panel"
      {...overrides}
    />,
  );
}

describe("ResizableSplitter – accessibility", () => {
  it("renders a separator with correct aria attributes", () => {
    renderSplitter();
    const handle = screen.getByRole("separator");
    expect(handle).toBeInTheDocument();
    expect(handle).toHaveAttribute("aria-orientation", "vertical");
    expect(handle).toHaveAttribute("aria-valuemin", "320");
    expect(handle).toHaveAttribute("aria-valuemax", "720");
    expect(handle).toHaveAttribute("tabindex", "0");
  });

  it("applies a title attribute for tooltip", () => {
    renderSplitter({ label: "拖动调整过程栏宽度，双击重置" });
    const handle = screen.getByRole("separator");
    expect(handle).toHaveAttribute("title", "拖动调整过程栏宽度，双击重置");
  });
});

describe("ResizableSplitter – keyboard", () => {
  it("ArrowLeft increases width by 8px (drag-left semantics)", () => {
    renderSplitter();
    const handle = screen.getByRole("separator");
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(Number(handle.getAttribute("aria-valuenow"))).toBe(368);
  });

  it("ArrowRight decreases width by 8px", () => {
    renderSplitter();
    const handle = screen.getByRole("separator");
    fireEvent.keyDown(handle, { key: "ArrowRight" });
    expect(Number(handle.getAttribute("aria-valuenow"))).toBe(352);
  });

  it("Shift+ArrowLeft increases width by 32px", () => {
    renderSplitter();
    const handle = screen.getByRole("separator");
    fireEvent.keyDown(handle, { key: "ArrowLeft", shiftKey: true });
    expect(Number(handle.getAttribute("aria-valuenow"))).toBe(392);
  });

  it("Home sets width to max", () => {
    renderSplitter();
    const handle = screen.getByRole("separator");
    fireEvent.keyDown(handle, { key: "Home" });
    expect(Number(handle.getAttribute("aria-valuenow"))).toBe(720);
  });

  it("End sets width to min", () => {
    renderSplitter();
    const handle = screen.getByRole("separator");
    fireEvent.keyDown(handle, { key: "End" });
    expect(Number(handle.getAttribute("aria-valuenow"))).toBe(320);
  });

  it("Enter resets to default", () => {
    renderSplitter({ defaultWidth: 400 });
    const handle = screen.getByRole("separator");
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(Number(handle.getAttribute("aria-valuenow"))).toBe(408);
    fireEvent.keyDown(handle, { key: "Enter" });
    expect(Number(handle.getAttribute("aria-valuenow"))).toBe(400);
  });

  it("ArrowLeft from max stays at max", () => {
    renderSplitter({ defaultWidth: 720 });
    const handle = screen.getByRole("separator");
    fireEvent.keyDown(handle, { key: "ArrowLeft" });
    expect(Number(handle.getAttribute("aria-valuenow"))).toBe(720);
  });
});

