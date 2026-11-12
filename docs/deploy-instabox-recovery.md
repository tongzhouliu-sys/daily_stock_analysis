# Instabox 免费实例的访问唤醒与服务恢复

`a.985.edu.kg` 通过 Cloudflare Tunnel 访问 Instabox 上的 DSA。免费实例闲置会自动暂停，达到连续运行上限也会暂停并进入平台冷却。访问触发的唤醒由 Instabox 处理。冷却期间的可用性取决于平台返回的恢复时间。 [Instabox 自动暂停和冷却规则](https://instabox.sh/docs/machines/auto-pause)

## 故障与处理

2026 年 11 月 10 日，`a.985.edu.kg` 返回 HTTP `530` 和 Cloudflare 错误 `1033`。控制台确认 Instabox 已暂停。实例恢复后，本机 DSA 健康检查已返回 `200`，隧道仍无活动连接。单独重启 `dsa-ibagent-tunnel` 后公网恢复，应用与数据继续使用原容器和持久目录。

- `scripts/instabox-recovery.py check` 每分钟检查本机应用、公网健康和隧道就绪状态。连续两次确认故障后，只重启对应的应用或隧道容器。同一组件的重启间隔至少五分钟。程序和自动部署共用锁，并在部署切换未完成时跳过检查。
- `scripts/instabox-recovery.py serve` 在单独的回环端口提供唤醒探针。访问 Instabox 发布的原生 HTTPS 端口，由平台先恢复机器。`/ready` 只返回应用就绪状态，`/` 在应用可用时跳转到主站，其他 GET 路径返回 `404`。
- `scripts/instabox-wake-worker.mjs` 用于在 Cloudflare 边缘接入主站访问。正常请求直接转发。浏览器页面 GET 遇到源站不可用时，Worker 请求 Instabox 唤醒探针，并在探针就绪后重试一次原访问。平台返回的 `Retry-After` 用于提示恢复时间。业务写请求和 API 请求沿原路径转发。

## 云端运行位置

- 实例名为 `ibagent`。应用和隧道分别运行在 `dsa-ibagent-web`、`dsa-ibagent-tunnel` 容器内。宿主机 `/data/home/daily-stock-analysis/state/data` 挂载到应用的 `/app/data`，供配置及历史数据持续使用。
- 健康检查程序安装在 `/usr/local/libexec/dsa-instabox-recovery.py`，运行配置位于 `/etc/dsa-instabox-recovery.env`。
- `dsa-instabox-watch.timer` 触发 `dsa-instabox-watch.service`。定时器使用持久调度，在机器恢复后补跑错过的检查。自愈检查在实例运行期间执行。
- `dsa-instabox-wake.service` 以独立动态用户运行健康端口。唤醒入口使用 Instabox 自带的端口发布功能，仅包含健康状态和主站跳转。 [直接唤醒入口](https://15009-agqxzsrkxalomzacqqzzzsstbm.instabox.tech/)
- `dsa-github-deploy.timer` 在实例运行时检查 GitHub 更新。自愈检查与部署共用 `/data/home/daily-stock-analysis/deploy/deploy.lock`，部署切换期间会跳过检查。

## 验收记录

2026 年 11 月 10 日，新加坡时间约 20:13，访问 Instabox 原生健康端口后，控制台显示实例恢复运行。约 20:16，隧道报告四条活动连接，公网首页和健康接口均返回 `200`。本轮隧道恢复期间，服务日志未记录自愈重启。

- 主站上线版本为 GitHub 提交 `af4825f85d90963b375fc50fe0d2e73caae73263`。登录状态接口返回 `200`，未登录访问扫描器接口返回 `401`。
- Instabox 唤醒入口的 `/ready` 返回 `200`，`/` 返回 `303` 跳到主站，业务路径 `/api/v1/system/config` 返回 `404`。
- 唤醒服务、自愈定时器和 GitHub 自动部署定时器均为 `active`。扫描和每日行情采集进程均在运行。行情采集的完整交易日结果需单独验收。
- 同日约 20:26，Cloudflare Worker `dsa-instabox-recovery` 的版本 `c8117347-da4c-45dd-a441-1e7302431419` 上线，路由为 `a.985.edu.kg/*`。Worker 发生执行错误或超限时，Cloudflare 会将请求发往原隧道。
- 路由启用后，首页、静态资源和健康接口均返回 `200`，登录状态接口确认认证已启用，未登录访问扫描器接口仍返回 `401`。Cloudflare 指标已记录 Worker 调用。代码通过了模拟唤醒及冷却测试，主域名触发下一次自然休眠后的唤醒仍待实测。

## 重新发布主域名唤醒程序

发布 `scripts/instabox-wake-worker.mjs` 到 `dsa-instabox-recovery`。环境变量 `PUBLIC_HOST` 填 `a.985.edu.kg`，`WAKE_URL` 填 Instabox 独立唤醒入口。发布代码并核对环境变量后，为该 Worker 添加 `a.985.edu.kg/*` 路由。主域名的底层 DNS 指向原有 Cloudflare Tunnel。 [Cloudflare Workers 路由](https://developers.cloudflare.com/workers/configuration/routing/routes/)

添加路由后，检查首页、健康接口、静态文件和未登录的扫描器接口。平台下一次自动暂停后，以浏览器访问主域名，确认 Worker 能触发唤醒并恢复页面。Instabox 强制冷却期间会拒绝唤醒，恢复页面按平台给出的时间提示重试。 [Instabox 冷却规则](https://instabox.sh/docs/machines/auto-pause)

## 检查与回退

在 Instabox 控制台检查服务状态和最近的恢复记录。

```bash
systemctl is-enabled dsa-instabox-watch.timer dsa-instabox-wake.service
systemctl list-timers dsa-instabox-watch.timer --no-pager
sudo journalctl -u dsa-instabox-watch.service -n 30 --no-pager
curl -fsS --max-time 6 https://a.985.edu.kg/api/health
curl -fsS --max-time 6 https://15009-agqxzsrkxalomzacqqzzzsstbm.instabox.tech/ready
```

如需回退已经接入的 Worker，在 Cloudflare 移除 `a.985.edu.kg/*` 对恢复 Worker 的绑定，主域名随后直接使用原隧道。若也要撤销服务器自愈和独立唤醒，在 Instabox 取消发布健康端口并停止两个恢复服务。

```bash
sudo systemctl disable --now dsa-instabox-watch.timer dsa-instabox-wake.service
```

实例停机或平台冷却期间，服务器内的部署定时器和行情采集进程会暂停。访问唤醒在平台允许时发生。 [Instabox 端口与自动恢复规则](https://instabox.sh/docs/machines/auto-pause#what-wakes-a-machine)
