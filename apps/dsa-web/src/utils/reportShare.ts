/**
 * 报告分享相关的纯前端工具：深链生成、深链解析与剪贴板写入。
 *
 * 这些能力不依赖后端转图引擎，因此“复制链接”在任何部署环境下都可用。
 */

/**
 * 构造历史报告深链。
 *
 * 报告选择只存在前端 store 中，需要显式写入 `recordId` 才能让链接定位到
 * 同一份报告。路径基于 Vite 的 `BASE_URL`，避免子路径部署时拼错。
 */
export const buildReportLink = (recordId: number): string => {
  if (!Number.isSafeInteger(recordId) || recordId <= 0) {
    throw new Error(`Invalid record id: ${recordId}`);
  }
  const base = import.meta.env.BASE_URL || '/';
  const url = new URL(base, window.location.origin);
  url.searchParams.set('recordId', String(recordId));
  return url.toString();
};

/**
 * 解析 `?recordId=` 中的记录 ID。
 *
 * 只接受正安全整数，非法值返回 undefined，调用方按“没有深链”处理。
 */
export const parseReportRecordId = (value: string | null | undefined): number | undefined => {
  if (typeof value !== 'string') {
    return undefined;
  }
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) {
    return undefined;
  }
  const recordId = Number(trimmed);
  return Number.isSafeInteger(recordId) && recordId > 0 ? recordId : undefined;
};

const writeWithTextarea = (text: string): boolean => {
  if (typeof document === 'undefined') {
    return false;
  }
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.top = '-9999px';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);

  try {
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    return document.execCommand('copy');
  } catch {
    return false;
  } finally {
    document.body.removeChild(textarea);
  }
};

/**
 * 复制文本到剪贴板。
 *
 * `navigator.clipboard` 只在安全上下文可用（https 或 localhost），局域网
 * http 部署会缺失，因此保留 textarea 回退。失败时返回 false，由调用方提示。
 */
export const copyTextToClipboard = async (text: string): Promise<boolean> => {
  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 权限被拒绝或非安全上下文，继续尝试同步回退
    }
  }
  return writeWithTextarea(text);
};
