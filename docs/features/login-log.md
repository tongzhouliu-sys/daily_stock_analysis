# 登录日志

记录管理员登录与认证操作的 IP、位置、时间和客户端信息，方便审计和安全排查。

## 记录了什么

系统会在以下时机记录日志：

- **登录**（`login`）：管理员通过 `/auth/login` 登录，成功或失败都会记录
- **首次设置密码**（`initial_setup`）：首次设置管理员密码
- **开启认证**（`auth_enable`）：通过 `/auth/settings` 开启认证功能
- **关闭认证**（`auth_disable`）：通过 `/auth/settings` 关闭认证功能
- **修改密码**（`password_change`）：通过 `/auth/change-password` 修改密码

每条记录包含：

- 时间（UTC）
- 事件类型
- 结果（成功/失败）
- 失败原因（仅失败时）
- IP 地址（应用层 IP 和 TCP 对端地址）
- IP 归属地（国家、省/州、城市）
- 客户端信息（浏览器/桌面端/脚本、操作系统、设备类型）
- 原始 User-Agent

**不记录的内容**：密码、会话值、Cookie、请求体、账号原文。

## 事件类型与失败原因

### 事件类型

| 值 | 中文 | 说明 |
| --- | --- | --- |
| `login` | 登录 | 管理员登录 |
| `initial_setup` | 首次设置密码 | 首次设置管理员密码 |
| `auth_enable` | 开启认证 | 开启认证功能 |
| `auth_disable` | 关闭认证 | 关闭认证功能 |
| `password_change` | 修改密码 | 修改管理员密码 |

### 失败原因

| 值 | 中文 | 说明 |
| --- | --- | --- |
| `invalid_credentials` | 账号或密码错误 | 登录时账号或密码错误 |
| `password_mismatch` | 两次输入的密码不一致 | 首次设置密码时两次输入不一致 |
| `password_rejected` | 密码不符合要求 | 少于 10 位或不符合其他要求 |
| `current_password_invalid` | 当前密码错误 | 修改密码、开启/关闭认证时当前密码错误 |
| `setup_token_missing` | 缺少初始化令牌 | 首次设置时公网客户端未提供 `ADMIN_SETUP_TOKEN` |
| `setup_token_invalid` | 初始化令牌无效 | 首次设置时提供的令牌不匹配 |

## 离线 IP 地理库配置

系统支持使用离线 MMDB 格式的 IP 地理库显示登录位置。推荐以下两种：

### DB-IP City Lite（推荐）

- **下载地址**：https://db-ip.com/db/download/ip-to-city-lite
- **许可**：CC BY 4.0（需要署名）
- **大小**：约 121 MB
- **更新频率**：每月
- **特点**：包含中文名称，免费

### MaxMind GeoLite2-City

- **下载地址**：需要注册 MaxMind 账号
- **许可**：GeoLite2 EULA（需要署名）
- **特点**：业界标准

### 配置步骤

1. 下载 `.mmdb` 文件到服务器
2. 在 `.env` 中配置路径：

```bash
LOGIN_LOG_GEOIP_DB_PATH=./data/geoip/dbip-city-lite.mmdb
```

相对路径按进程工作目录解析，和 `DATABASE_PATH` 一致。

### Docker 部署

将 `.mmdb` 文件放到挂载的数据目录：

```bash
# 创建数据目录
mkdir -p ./data/geoip

# 下载 MMDB 文件到 ./data/geoip/

# 启动容器时挂载数据目录
docker run -v $(pwd)/data:/app/data your-image
```

然后在 `.env` 中配置：

```bash
LOGIN_LOG_GEOIP_DB_PATH=./data/geoip/dbip-city-lite.mmdb
```

### 署名要求

- **DB-IP**：前端会自动显示 "IP Geolocation by DB-IP" 链接到 https://db-ip.com
- **MaxMind**：前端会自动显示 GeoLite2 署名

## 在线查询

当前版本只支持离线 `.mmdb` 地理库，不提供在线 IP 查询（避免把登录 IP 以明文发送给第三方）。`LOGIN_LOG_GEO_ONLINE_PROVIDER` 已移除；如未来引入，需单独评估隐私与许可。

## 反向代理下的 IP 准确性

如果应用部署在反向代理（如 Nginx、Caddy）后面，需要正确配置以获取真实客户端 IP：

### 配置步骤

1. 在 `.env` 中启用：

```bash
TRUST_X_FORWARDED_FOR=true
```

2. 配置可信代理地址（可选，默认仅本机回环）：

```bash
GUARD_TRUSTED_PROXIES=127.0.0.1,::1
```

3. 确保反向代理正确设置 `X-Forwarded-For` 头：

```nginx
# Nginx 示例
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

### 注意事项

- 日志会同时记录应用层 IP（`X-Forwarded-For` 最右值）和 TCP 对端地址
- 如果应用可以被直连（不经过代理），应用层 IP 可能被伪造
- 日志中的"经代理"标记只反映应用层是否采用了 XFF，不反映协议层

## 保留与清理

- **保留天数**：默认 90 天，可通过 `LOGIN_LOG_RETENTION_DAYS` 配置（范围 1-3650）
- **行数上限**：5000 行，防止数据库被暴力尝试撑爆
- **限流前提**：登录失败限流是进程内计数（5 次 / 5 分钟），重启清零；多 worker/多副本会各自计数
- **清理时机**：每次写入后自动清理超期或超限的记录
- **清理顺序**：先按时间删除，再按行数上限删除最旧的记录

## 已知限制

### 客户端识别

- **浏览器版本**：浏览器已冻结 UA 中的系统版本（Windows 11 仍报 `Windows NT 10.0`，macOS 固定报 `10_15_7`），所以只记录系统家族
- **iPadOS**：iPadOS 上的 Safari 默认伪装成 macOS，会被识别成 macOS 电脑
- **UA 伪造**：User-Agent 可以被客户端随意伪造，只能作参考

### IP 位置

- **VPN/代理**：使用 VPN 或代理时，位置会显示为 VPN/代理服务器的位置
- **城市精度**：IP 地理库的城市级精度有限，可能只显示国家或省级位置
- **移动网络**：移动网络的 IP 位置可能不准确

### 其他

- **429 不记录**：被限流的请求（返回 429）不会记录日志，因为限流前的失败已经记录了
- **退出登录不记录**：退出登录不会记录日志
- **认证关闭时不记录**：认证功能关闭时不会记录日志
