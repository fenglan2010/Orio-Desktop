import { onMounted, onUnmounted, unref } from "vue";

/** Call close() when Escape is pressed and source is truthy (a ref, getter, or value). */
export function useEscape(source, close) {
  function onKey(e) {
    if (e.key !== "Escape") return;
    const open = typeof source === "function" ? source() : unref(source);
    if (open) close();
  }
  onMounted(() => window.addEventListener("keydown", onKey));
  onUnmounted(() => window.removeEventListener("keydown", onKey));
}
