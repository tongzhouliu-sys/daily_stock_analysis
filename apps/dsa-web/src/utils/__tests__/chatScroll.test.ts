import { describe, expect, it, vi } from 'vitest';
import { anchorMessageIdAtViewportTop, isBeyondOneScreen, isNearBottom, scrollToMessage } from '../chatScroll';

describe('isNearBottom', () => {
  it('returns true when the viewport is within the default threshold', () => {
    expect(
      isNearBottom({
        scrollTop: 604,
        clientHeight: 320,
        scrollHeight: 1000,
      }),
    ).toBe(true);
  });

  it('returns false when the viewport is far from the bottom', () => {
    expect(
      isNearBottom({
        scrollTop: 240,
        clientHeight: 320,
        scrollHeight: 1000,
      }),
    ).toBe(false);
  });
});

describe('scrollToMessage', () => {
  it('positions the element below the viewport top by the given padding', () => {
    const viewport = document.createElement('div');
    const element = document.createElement('div');
    vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue({ top: 100 } as DOMRect);
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ top: 700 } as DOMRect);
    viewport.scrollTop = 400;

    scrollToMessage(viewport, element, 20);

    // 400 + (700 - 100) - 20 = 980
    expect(viewport.scrollTop).toBe(980);
  });

  it('clamps to zero instead of scrolling into negative space', () => {
    const viewport = document.createElement('div');
    const element = document.createElement('div');
    vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue({ top: 200 } as DOMRect);
    vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({ top: 100 } as DOMRect);
    viewport.scrollTop = 0;

    scrollToMessage(viewport, element, 16);

    expect(viewport.scrollTop).toBe(0);
  });
});

describe('anchorMessageIdAtViewportTop', () => {
  function element(top: number, id: string): HTMLElement {
    const el = document.createElement('div');
    el.dataset.messageId = id;
    vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({ top } as DOMRect);
    return el;
  }

  it('returns the last user message whose top passed the viewport top', () => {
    const viewport = document.createElement('div');
    vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue({ top: 500 } as DOMRect);

    const anchor = anchorMessageIdAtViewportTop(viewport, [
      element(100, 'q1'),
      element(400, 'q2'),
      element(510, 'q3'),
      element(800, 'q4'),
    ]);

    expect(anchor).toBe('q2');
  });

  it('returns null when no message has scrolled past the top', () => {
    const viewport = document.createElement('div');
    vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue({ top: 0 } as DOMRect);

    expect(anchorMessageIdAtViewportTop(viewport, [element(100, 'q1')])).toBeNull();
  });
});


describe('isBeyondOneScreen', () => {
  it('stays false while the reader is within one viewport of the newest message', () => {
    expect(isBeyondOneScreen({ scrollTop: 300, clientHeight: 400, scrollHeight: 1000 })).toBe(false);
  });

  it('turns true only after a full viewport of distance', () => {
    expect(isBeyondOneScreen({ scrollTop: 100, clientHeight: 400, scrollHeight: 1000 })).toBe(true);
  });
});
