import { useCallback, useEffect, useRef, useState } from "react";

export type ResizableWidthOptions = {
  /** localStorage key. When omitted, the value lives in memory only. */
  storageKey?: string;
  /** Lower bound (px). */
  min: number;
  /** Upper bound (px). */
  max: number;
  /** Fallback when nothing is stored or the stored value is invalid. */
  defaultWidth: number;
  /** CSS variable the width is published to. Defaults to --process-panel-width. */
  cssVar?: string;
};

export type ResizableWidthResult = {
  /** Current persisted/committed width (px). */
  width: number;
  /** Live width during drag. Equal to `width` outside a drag. */
  liveWidth: number;
  /** True while a pointer drag is in progress. */
  isDragging: boolean;
  /** Begin a drag — locks onto the supplied starting width. */
  startDrag: (startWidth: number) => void;
  /** Update the live width during drag. Clamped to [min, max]. */
  updateDrag: (next: number) => void;
  /** Commit the live width, persist it, and end the drag. */
  endDrag: () => void;
  /** Restore the default width and persist it. */
  resetToDefault: () => void;
  /** Imperative setter used by keyboard handlers. */
  setCommittedWidth: (next: number) => void;
};

function readStoredWidth(storageKey: string | undefined, fallback: number): number {
  if (!storageKey || typeof window === "undefined") {
    return fallback;
  }
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
      return fallback;
    }
    const parsed = Number.parseFloat(raw);
    return Number.isFinite(parsed) ? parsed : fallback;
  } catch {
    return fallback;
  }
}

function writeStoredWidth(storageKey: string | undefined, value: number) {
  if (!storageKey || typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage.setItem(storageKey, String(value));
  } catch {
    // localStorage may be disabled (private mode, quota). Best-effort only.
  }
}

function clamp(value: number, min: number, max: number) {
  if (Number.isNaN(value)) {
    return min;
  }
  if (value < min) {
    return min;
  }
  if (value > max) {
    return max;
  }
  return value;
}

/**
 * Manages a single horizontal width with localStorage persistence and a live
 * "drag" overlay so the grid column can be driven from a CSS variable without
 * re-rendering React on every pointermove.
 */
export function useResizableWidth(options: ResizableWidthOptions): ResizableWidthResult {
  const { storageKey, min, max, defaultWidth, cssVar = "--process-panel-width" } = options;
  const [width, setWidth] = useState<number>(() => {
    const stored = readStoredWidth(storageKey, defaultWidth);
    return clamp(stored, min, max);
  });
  const [liveWidth, setLiveWidth] = useState<number>(width);
  const [isDragging, setIsDragging] = useState<boolean>(false);
  const liveWidthRef = useRef<number>(width);
  const widthRef = useRef<number>(width);
  const rafRef = useRef<number | null>(null);
  const pendingRef = useRef<number | null>(null);

  // Publish to the CSS variable on every change of the live width.
  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }
    document.documentElement.style.setProperty(cssVar, `${liveWidth}px`);
    liveWidthRef.current = liveWidth;
  }, [liveWidth, cssVar]);

  // When the viewport shrinks below the current width, clamp down gracefully.
  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    function handleResize() {
      const viewportMax = Math.max(min, window.innerWidth - 480);
      const upper = Math.min(max, viewportMax);
      setWidth((current) => {
        const next = clamp(current, min, upper);
        widthRef.current = next;
        if (next !== current) {
          writeStoredWidth(storageKey, next);
        }
        return next;
      });
      setLiveWidth((current) => {
        const next = clamp(current, min, upper);
        liveWidthRef.current = next;
        return next;
      });
    }
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [min, max, storageKey]);

  const scheduleStyleUpdate = useCallback((next: number) => {
    pendingRef.current = next;
    if (rafRef.current !== null) {
      return;
    }
    rafRef.current = window.requestAnimationFrame(() => {
      rafRef.current = null;
      if (pendingRef.current !== null) {
        const value = pendingRef.current;
        pendingRef.current = null;
        if (typeof document !== "undefined") {
          document.documentElement.style.setProperty(cssVar, `${value}px`);
        }
      }
    });
  }, [cssVar]);

  const startDrag = useCallback((startWidth: number) => {
    const clamped = clamp(startWidth, min, max);
    liveWidthRef.current = clamped;
    setIsDragging(true);
    setLiveWidth(clamped);
  }, [min, max]);

  const updateDrag = useCallback((next: number) => {
    const clamped = clamp(next, min, max);
    liveWidthRef.current = clamped;
    setLiveWidth(clamped);
    scheduleStyleUpdate(clamped);
  }, [min, max, scheduleStyleUpdate]);

  const endDrag = useCallback(() => {
    const committed = liveWidthRef.current;
    widthRef.current = committed;
    setIsDragging(false);
    setWidth(committed);
    writeStoredWidth(storageKey, committed);
  }, [storageKey]);

  const resetToDefault = useCallback(() => {
    const next = clamp(defaultWidth, min, max);
    liveWidthRef.current = next;
    widthRef.current = next;
    setWidth(next);
    setLiveWidth(next);
    writeStoredWidth(storageKey, next);
  }, [defaultWidth, min, max, storageKey]);

  const setCommittedWidth = useCallback((next: number) => {
    const clamped = clamp(next, min, max);
    liveWidthRef.current = clamped;
    widthRef.current = clamped;
    setWidth(clamped);
    setLiveWidth(clamped);
    writeStoredWidth(storageKey, clamped);
  }, [min, max, storageKey]);

  // Clean up any pending RAF on unmount.
  useEffect(() => () => {
    if (rafRef.current !== null) {
      window.cancelAnimationFrame(rafRef.current);
    }
  }, []);

  return {
    width,
    liveWidth,
    isDragging,
    startDrag,
    updateDrag,
    endDrag,
    resetToDefault,
    setCommittedWidth,
  };
}
