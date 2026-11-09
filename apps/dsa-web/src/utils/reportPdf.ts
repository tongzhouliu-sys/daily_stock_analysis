/**
 * 把分享海报 PNG 在浏览器本地转换为 A4 多页 PDF。
 *
 * 设计取舍：
 * - 不依赖后端 PDF 渲染器，因此 Docker / 桌面端 / 本地都无需额外二进制；
 * - 复用已经生成的分享海报，PDF 与图片分享在视觉上完全一致；
 * - jsPDF 体积较大，使用动态 import，只有用户点击“导出 PDF”时才加载；
 * - 每页统一为 A4，按比例切片后逐页绘制，长图不会被压成一张畸形页面。
 */

/** A4 纵向尺寸（pt）。 */
const PAGE_WIDTH_PT = 595.28;
const PAGE_HEIGHT_PT = 841.89;
/** 海报底色兜底值，仅在无法采样图片像素时使用。 */
const FALLBACK_BACKGROUND = '#0b1020';

export interface PdfPageSlice {
  /** 原图上的纵向起点（px）。 */
  top: number;
  /** 本页覆盖的原图高度（px）。 */
  height: number;
}

/** 每页可容纳的原图像素高度，由 A4 宽高比换算得出。 */
const pixelsPerPage = (imageWidth: number): number => {
  const ratio = PAGE_HEIGHT_PT / PAGE_WIDTH_PT;
  return Math.max(1, Math.floor(imageWidth * ratio));
};

/**
 * 按 A4 宽高比把长图切成若干页。
 *
 * 每页宽度固定为 A4 宽度，最后一页不足整页时由渲染阶段用海报底色补齐，
 * 保证所有页面都是标准 A4，打印和阅读器翻页都不会出现竖条页面。
 */
export const planPdfPageSlices = (imageWidth: number, imageHeight: number): PdfPageSlice[] => {
  if (!Number.isFinite(imageWidth) || !Number.isFinite(imageHeight)) {
    return [];
  }
  if (imageWidth <= 0 || imageHeight <= 0) {
    return [];
  }

  const pageHeightPx = pixelsPerPage(imageWidth);
  const slices: PdfPageSlice[] = [];
  for (let top = 0; top < imageHeight; top += pageHeightPx) {
    slices.push({ top, height: Math.min(pageHeightPx, imageHeight - top) });
  }
  return slices;
};

const loadImage = (blob: Blob): Promise<HTMLImageElement> => new Promise((resolve, reject) => {
  const objectUrl = URL.createObjectURL(blob);
  const image = new Image();
  image.onload = () => {
    URL.revokeObjectURL(objectUrl);
    resolve(image);
  };
  image.onerror = () => {
    URL.revokeObjectURL(objectUrl);
    reject(new Error('Failed to decode share image'));
  };
  image.src = objectUrl;
});

/** 取海报左上角像素作为补边底色，保证最后一页没有透明/黑边。 */
const sampleBackgroundColor = (image: HTMLImageElement): string => {
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 1;
    canvas.height = 1;
    const context = canvas.getContext('2d');
    if (!context) {
      return FALLBACK_BACKGROUND;
    }
    context.drawImage(image, 0, 0, 1, 1, 0, 0, 1, 1);
    const [red, green, blue] = context.getImageData(0, 0, 1, 1).data;
    return `rgb(${red}, ${green}, ${blue})`;
  } catch {
    return FALLBACK_BACKGROUND;
  }
};

/**
 * 渲染一页 A4 内容：上方是该页对应的海报切片，下方不足一页时用底色补齐。
 */
const renderSlice = (
  image: HTMLImageElement,
  slice: PdfPageSlice,
  pageHeightPx: number,
  backgroundColor: string,
): string => {
  const canvas = document.createElement('canvas');
  canvas.width = image.naturalWidth;
  canvas.height = pageHeightPx;
  const context = canvas.getContext('2d');
  if (!context) {
    throw new Error('Canvas 2D context is unavailable');
  }
  context.fillStyle = backgroundColor;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(
    image,
    0,
    slice.top,
    image.naturalWidth,
    slice.height,
    0,
    0,
    image.naturalWidth,
    slice.height,
  );
  return canvas.toDataURL('image/jpeg', 0.92);
};

/**
 * 将分享海报 PNG 转换为 A4 多页 PDF 文件。
 *
 * @param poster 分享海报图片，通常来自 `/share-image` 接口或桌面端 IPC。
 * @returns 可直接下载或分享的 PDF Blob。
 */
export const buildReportPdf = async (poster: Blob): Promise<Blob> => {
  const image = await loadImage(poster);
  const slices = planPdfPageSlices(image.naturalWidth, image.naturalHeight);
  if (slices.length === 0) {
    throw new Error('Share image is empty');
  }

  const { jsPDF } = await import('jspdf');
  const backgroundColor = sampleBackgroundColor(image);
  const pageHeightPx = pixelsPerPage(image.naturalWidth);
  const document = new jsPDF({ orientation: 'portrait', unit: 'pt', format: 'a4' });

  slices.forEach((slice, index) => {
    if (index > 0) {
      document.addPage('a4', 'portrait');
    }
    document.addImage(
      renderSlice(image, slice, pageHeightPx, backgroundColor),
      'JPEG',
      0,
      0,
      PAGE_WIDTH_PT,
      PAGE_HEIGHT_PT,
    );
  });

  return document.output('blob');
};
