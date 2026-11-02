export const PRODUCT_SEARCH_DEBOUNCE_MS = 350;

export function scheduleDebounced(
  callback: () => void,
  delay = PRODUCT_SEARCH_DEBOUNCE_MS,
): () => void {
  const timer = window.setTimeout(callback, delay);
  return () => window.clearTimeout(timer);
}
