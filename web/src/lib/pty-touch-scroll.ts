import type { Terminal } from "@xterm/xterm";

/** xterm 6's custom scrollbar handles wheels, but not phone swipes. */
export function attachPtyTouchScroll(host: HTMLElement, term: Terminal): () => void {
  let gesture: { id: number; x: number; y: number; lastY: number; pending: number; dragging: boolean } | null = null;
  const start = (event: TouchEvent) => {
    gesture = null;
    // Preserve pinch zoom, long-press selection and text editing gestures.
    if (event.touches.length !== 1 || term.hasSelection()) return;
    const touch = event.touches[0];
    gesture = { id: touch.identifier, x: touch.clientX, y: touch.clientY, lastY: touch.clientY, pending: 0, dragging: false };
  };
  const move = (event: TouchEvent) => {
    if (!gesture || event.touches.length !== 1 || term.hasSelection()) {
      gesture = null;
      return;
    }
    const touch = event.touches[0];
    if (touch.identifier !== gesture.id) return;
    if (!gesture.dragging) {
      const dy = Math.abs(touch.clientY - gesture.y);
      const dx = Math.abs(touch.clientX - gesture.x);
      if (dy < 8) return;
      if (dx > dy) { gesture = null; return; }
      gesture.dragging = true;
    }
    event.preventDefault();
    event.stopPropagation();
    const rowHeight = host.querySelector(".xterm-screen")?.getBoundingClientRect().height;
    const pixelsPerRow = rowHeight && term.rows ? rowHeight / term.rows : 16;
    gesture.pending += (gesture.lastY - touch.clientY) / pixelsPerRow;
    gesture.lastY = touch.clientY;
    const lines = Math.trunc(gesture.pending);
    if (lines) {
      term.scrollLines(lines);
      gesture.pending -= lines;
    }
  };
  const end = (event: TouchEvent) => {
    // A completed drag must not also focus the hidden input/open the keyboard.
    if (gesture?.dragging && event.cancelable) event.preventDefault();
    gesture = null;
  };
  host.addEventListener("touchstart", start, { passive: true });
  host.addEventListener("touchmove", move, { passive: false });
  host.addEventListener("touchend", end, { passive: false });
  host.addEventListener("touchcancel", end, { passive: true });
  return () => {
    host.removeEventListener("touchstart", start);
    host.removeEventListener("touchmove", move);
    host.removeEventListener("touchend", end);
    host.removeEventListener("touchcancel", end);
  };
}
