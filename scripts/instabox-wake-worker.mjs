const originUnavailable = new Set([502, 503, 504, 520, 521, 522, 523, 524, 530]);

function recoveryPage(retryAfter) {
  return new Response(
    '<!doctype html><html lang="zh-CN"><meta charset="utf-8">' +
      '<meta name="viewport" content="width=device-width,initial-scale=1">' +
      '<title>服务正在恢复</title><h1>服务正在恢复</h1>' +
      `<p>请在 ${retryAfter} 秒后刷新页面。如果实例进入了平台冷却期，恢复时间以 Instabox 为准。</p></html>`,
    {
      status: 503,
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store',
        'Retry-After': String(retryAfter),
      },
    },
  );
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname !== env.PUBLIC_HOST) return new Response(null, { status: 404 });
    const navigation = request.method === 'GET' && request.headers.get('accept')?.includes('text/html') &&
      !/^\/(api|assets)(\/|$)/.test(url.pathname);
    try {
      const initial = await fetch(request, { redirect: 'manual' });
      if (!navigation || !originUnavailable.has(initial.status)) return initial;
    } catch (error) {
      if (!navigation) throw error;
    }

    let ready;
    try {
      const probe = new URL('/ready', env.WAKE_URL);
      if (probe.protocol !== 'https:' || probe.hostname === url.hostname) return recoveryPage(30);
      ready = await fetch(probe, { redirect: 'manual', signal: AbortSignal.timeout(25000) });
      if (ready.ok && (await ready.text()).trim() !== 'ready') return recoveryPage(30);
    } catch {
      return recoveryPage(30);
    }

    if (!ready.ok) {
      const retry = Number(ready.headers.get('retry-after'));
      return recoveryPage(Number.isInteger(retry) && retry > 0 ? retry : 30);
    }

    try {
      const afterWake = await fetch(request, { redirect: 'manual' });
      return originUnavailable.has(afterWake.status) ? recoveryPage(10) : afterWake;
    } catch {
      return recoveryPage(10);
    }
  },
};
