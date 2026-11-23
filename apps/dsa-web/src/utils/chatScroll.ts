interface ScrollMetrics {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
}

const DEFAULT_THRESHOLD = 96;

export function isNearBottom(
  metrics: ScrollMetrics,
  threshold = DEFAULT_THRESHOLD,
): boolean {
  const distanceFromBottom =
    metrics.scrollHeight - (metrics.scrollTop + metrics.clientHeight);

  return distanceFromBottom <= threshold;
}

/**
 * True when the reader is more than one viewport away from the newest message.
 * The "back to bottom" affordance stays hidden for small scroll offsets so the
 * button does not hover over a thread the reader is still following.
 */
export function isBeyondOneScreen(metrics: ScrollMetrics): boolean {
  const distanceFromBottom =
    metrics.scrollHeight - (metrics.scrollTop + metrics.clientHeight);
  return distanceFromBottom > metrics.clientHeight;
}

/**
 * Scroll a scroll container so `element` sits `padding` px below its top edge.
 * Uses viewport-relative rects so the container's own offset/positioned
 * ancestors never skew the result.
 */
export function scrollToMessage(
  viewport: HTMLElement,
  element: HTMLElement,
  padding = 16,
): void {
  const viewportRect = viewport.getBoundingClientRect();
  const elementRect = element.getBoundingClientRect();
  const target = viewport.scrollTop + (elementRect.top - viewportRect.top) - padding;
  viewport.scrollTop = Math.max(0, target);
}

/**
 * Given user-message DOM nodes in document order, return the `data-message-id`
 * of the last one whose top has passed the viewport's top edge. This is the
 * "current question" for the right-hand outline scroll-spy.
 */
export function anchorMessageIdAtViewportTop(
  viewport: HTMLElement,
  messageElements: HTMLElement[],
): string | null {
  const viewportTop = viewport.getBoundingClientRect().top;
  let current: string | null = null;
  for (const element of messageElements) {
    if (element.getBoundingClientRect().top <= viewportTop + 8) {
      current = element.dataset.messageId ?? null;
    } else {
      break;
    }
  }
  return current;
}
