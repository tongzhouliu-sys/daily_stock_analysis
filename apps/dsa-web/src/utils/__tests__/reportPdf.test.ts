import { describe, expect, it } from 'vitest';
import { planPdfPageSlices } from '../reportPdf';

describe('planPdfPageSlices', () => {
  it('splits a long poster into A4-proportioned pages', () => {
    const slices = planPdfPageSlices(1080, 2190);

    expect(slices.length).toBeGreaterThan(1);
    // 1080 * (841.89 / 595.28) ≈ 1527px per page
    expect(slices[0]).toEqual({ top: 0, height: 1527 });
    expect(slices[1].top).toBe(1527);
    expect(slices[slices.length - 1].top + slices[slices.length - 1].height).toBe(2190);
  });

  it('keeps a short poster on a single page', () => {
    expect(planPdfPageSlices(1080, 800)).toEqual([{ top: 0, height: 800 }]);
  });

  it('covers the source image exactly once without gaps or overlaps', () => {
    const slices = planPdfPageSlices(1080, 4000);
    let cursor = 0;
    for (const slice of slices) {
      expect(slice.top).toBe(cursor);
      expect(slice.height).toBeGreaterThan(0);
      cursor += slice.height;
    }
    expect(cursor).toBe(4000);
  });

  it('returns no pages for invalid dimensions', () => {
    expect(planPdfPageSlices(0, 2190)).toEqual([]);
    expect(planPdfPageSlices(1080, 0)).toEqual([]);
    expect(planPdfPageSlices(Number.NaN, 2190)).toEqual([]);
  });
});
