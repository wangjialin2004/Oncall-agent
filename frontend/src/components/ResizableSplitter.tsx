import { type PointerEvent as ReactPointerEvent, type KeyboardEvent, useCallback, useEffect, useRef } from "react";

import { useResizableWidth } from "./hooks/useResizableWidth";

type ResizableSplitterProps = {
  /** Min/Max/Default in pixels. */
  min: number;
  max: number;
  defaultWidth: number;
  /** localStorage key (omit to disable persistence). */
  storageKey?: string;
  /** Selector used to look up the panel element for measuring layout bounds. */
  targetSelector: string;
  /** ARIA label for the handle. */
  label?: string;
};

const KEY_STEP = 8;
const KEY_STEP_LARGE = 32;

/**
 * A thin vertical handle that lets the user drag-resize the right process panel.
 *
 * Drag listeners are attached **synchronously inside `onPointerDown`** to
 * `window`, not inside a `useEffect` keyed on `isDragging`. This guarantees
 * the move handler is already wired up before the first pointermove event
 * can be dispatched — a useEffect would miss the first frame because the
 * `isDragging` state change is async.
 */
export function ResizableSplitter({
  min,
  max,
  defaultWidth,
  storageKey,
  targetSelector,
  label = "拖动调整过程栏宽度，双击重置",
}: ResizableSplitterProps) {
  const { liveWidth, isDragging, startDrag, updateDrag, endDrag, resetToDefault, setCommittedWidth } =
    useResizableWidth({ storageKey, min, max, defaultWidth });

  const startXRef = useRef<number>(0);
  const startWidthRef = useRef<number>(0);
  // Mirrors of the latest callbacks so window listeners always see fresh values.
  const updateDragRef = useRef<typeof updateDrag>(updateDrag);
  const endDragRef = useRef<typeof endDrag>(endDrag);
  const startDragRef = useRef<typeof startDrag>(startDrag);

  useEffect(() => {
    updateDragRef.current = updateDrag;
  }, [updateDrag]);
  useEffect(() => {
    endDragRef.current = endDrag;
  }, [endDrag]);
  useEffect(() => {
    startDragRef.current = startDrag;
  }, [startDrag]);

  // Latest liveWidth mirrored into a ref so the first move after pointerdown
  // uses the width captured at the moment of click, not a stale closure value.
  const liveWidthRef = useRef<number>(liveWidth);
  useEffect(() => {
    liveWidthRef.current = liveWidth;
  }, [liveWidth]);

  const detachDrag = useCallback(() => {
    window.removeEventListener("pointermove", handleWindowMove);
    window.removeEventListener("pointerup", handleWindowUp);
    window.removeEventListener("pointercancel", handleWindowUp);
  }, []);

  function handleWindowMove(event: PointerEvent) {
    // Dragging the separator to the LEFT grows the process panel (and
    // shrinks the workspace), because the process panel lives on the
    // right side of the screen. So we invert the pointer delta: a
    // negative `clientX - startX` should INCREASE the width.
    const delta = event.clientX - startXRef.current;
    updateDragRef.current(startWidthRef.current - delta);
  }

  function handleWindowUp() {
    detachDrag();
    endDragRef.current();
  }

  const onPointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    // Synchronous capture — no useEffect, no React render in between.
    startXRef.current = event.clientX;
    startWidthRef.current = liveWidthRef.current;
    startDragRef.current(liveWidthRef.current);
    // If a previous detach failed to fire, clear it before re-attaching.
    detachDrag();
    window.addEventListener("pointermove", handleWindowMove);
    window.addEventListener("pointerup", handleWindowUp);
    window.addEventListener("pointercancel", handleWindowUp);
  }, [detachDrag]);

  // Safety net: if the component unmounts mid-drag, don't leak listeners.
  useEffect(() => detachDrag, [detachDrag]);

  const onDoubleClick = useCallback(() => {
    resetToDefault();
  }, [resetToDefault]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      const step = event.shiftKey ? KEY_STEP_LARGE : KEY_STEP;
      let next: number | null = null;
      switch (event.key) {
        case "ArrowLeft":
          // The separator sits on the left edge of the process panel, so
          // pressing Left mirrors dragging the separator to the Left,
          // which grows the panel.
          next = liveWidth + step;
          break;
        case "ArrowRight":
          next = liveWidth - step;
          break;
        case "Home":
          next = max;
          break;
        case "End":
          next = min;
          break;
        case "Enter":
        case " ":
          event.preventDefault();
          resetToDefault();
          return;
        default:
          return;
      }
      event.preventDefault();
      if (next !== null) {
        setCommittedWidth(next);
      }
    },
    [liveWidth, min, max, resetToDefault, setCommittedWidth],
  );

  // Observe the panel so a viewport resize can be detected (cleanup only).
  useEffect(() => {
    if (typeof window === "undefined" || typeof ResizeObserver === "undefined") {
      return;
    }
    const target = document.querySelector(targetSelector);
    if (!target) {
      return;
    }
    const observer = new ResizeObserver(() => {
      // intentionally empty — width clamping on viewport resize happens in the hook
    });
    observer.observe(target);
    return () => observer.disconnect();
  }, [targetSelector]);

  return (
    <div
      className={`resize-handle${isDragging ? " is-dragging" : ""}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(liveWidth)}
      tabIndex={0}
      title={label}
      onPointerDown={onPointerDown}
      onDoubleClick={onDoubleClick}
      onKeyDown={onKeyDown}
    >
      <span className="resize-handle-grip" aria-hidden="true" />
    </div>
  );
}
