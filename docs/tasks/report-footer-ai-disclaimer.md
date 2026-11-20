# 开发任务书：报告导出与飞书消息底部统一 AI 声明

> 状态：已实施  
> 基线：`b2298e31`  
> 任务类型：`feat` + `refactor` + `docs`  
> 影响面：分享图 / PDF / Markdown 报告 / 飞书通知 / 配置 / 打包 / 文档 / 测试

> 定稿确认：统一文案为 `本数据由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。`；
> 飞书仅分析报告推送追加声明；PDF 只在末尾/最后一页出现一次；页脚保留 RHCLOUD 品牌标题/标语，
> 删除仓库地址与小红书区。

## 1. 背景与现状

### 1.1 用户诉求

- 导出 PDF 和分享图片底部删除“小红书广告 + 开源项目地址”。
- 统一替换为 RHCLOUD PTE. LTD. 的 AI 生成声明。
- 每个分析报告和飞书消息底部都用小字展示该提醒。
- 当前阶段只交付开发任务书，不实施代码改动。

### 1.2 现有链路

| 入口 | 当前位置 | 现状 |
| --- | --- | --- |
| 分享图页脚 | `src/share_image.py` `_footer()` 约 2038 行 | 展示 DSA 品牌、`ZhuLinsen/daily_stock_analysis` 仓库地址、小红书二维码/昵称，以及旧版风险声明 |
| 小红书卡片 | `src/share_image.py` `_xiaohongshu_card()` 约 2012 行 | 渲染二维码、昵称、账号链接，默认使用仓库内置二维码 |
| 页脚样式 | `src/share_image.py` 约 2161 行 | `.repo-line`、`.qr-card`、`.qr-frame`、`.disclaimer` 等样式与页脚结构耦合 |
| Web 导出 PDF | `apps/dsa-web/src/utils/reportPdf.ts` `buildReportPdf()` 约 122 行 | 直接复用分享图 PNG 切页生成 A4 PDF，没有独立页脚 |
| 通知转图 | `src/md2img.py` 约 303-314 行 | 从配置读取小红书品牌，再调用同一个 `build_share_image_html()` |
| 历史报告分享图 | `api/v1/endpoints/history.py` 约 773 / 827 行 | HTML 与 PNG 两个接口都传入 `branding` |
| 历史报告重新生成 | `src/services/history_service.py` `_generate_single_stock_markdown()` 约 1120 行 | 按旧版仪表盘格式重建 Markdown，没有统一页脚 |
| Markdown 模板 | `templates/report_markdown.j2`、`report_wechat.j2`、`report_brief.j2` | 只输出生成时间/模型，没有统一免责声明 |
| 日报/仪表盘报告 | `src/notification.py` `generate_daily_report()` 约 924 行、`generate_dashboard_report()` 约 1294 行 | 页脚只有生成时间/模型，部分路径完全没有免责声明 |
| 企微报告 | `src/notification.py` `generate_wechat_dashboard()` 约 1663 行、`generate_wechat_summary()` 约 1858 行 | 摘要版仍使用旧 `not_investment_advice` 文案 |
| 简报/单股报告 | `src/notification.py` `generate_brief_report()` 约 1933 行、`generate_single_stock_report()` 约 1998 行 | 简报无统一声明；单股仍使用旧 `not_investment_advice` 文案 |
| 大盘复盘 | `src/market_analyzer.py` 约 2097 行、`src/core/market_review.py` 约 518 / 531 行 | 复盘模板末尾只有“复盘时间”，没有统一声明；结构化重渲染也没有统一页脚 |
| 飞书卡片 | `src/notification_sender/feishu_card.py` `CARD_SUBTITLE` 约 34 行、`_note()` 约 165 行 | 页脚自动补“仅供研究参考 · 不构成投资建议” |
| 飞书紧凑卡片 | `src/notification_sender/feishu_card.py` `build_feishu_card()` 约 640-647 行 | 紧凑卡片只放一个 `div`，不带 `note`，因此可能完全没有免责声明 |
| 飞书纯文本兜底 | `src/notification_sender/feishu_sender.py` 约 322 / 615 行 | 卡片失败后直接发送正文，是否带声明取决于上游报告内容 |
| 配置 | `src/config.py` 约 1298-1301、2311-2314 行；`.env.example` 约 766-769 行 | 小红书 URL/昵称/ID/二维码路径四个可选配置 |
| 打包 | `scripts/build-backend-macos.sh` 约 124 行、`scripts/build-backend.ps1` 约 144 行 | PyInstaller 把 `src/assets/share_image` 一并打进桌面包 |

### 1.3 需要解决的问题

1. 导出图片和 PDF 仍带小红书二维码、昵称和 GitHub 仓库地址，不符合新的对外物料要求。
2. 报告免责声明分散在 `report_language.py`、`share_image.py`、`feishu_card.py` 和多个生成函数中，文案、位置、是否出现都不一致。
3. 飞书紧凑卡片降级后可能没有页脚声明；纯文本兜底也不保证带声明。
4. 旧报告、旧模板和旧 LLM 输出可能自带旧版免责声明，直接追加会造成重复。
5. 删除小红书能力后，配置、二维码资产、PyInstaller 参数、文档和测试都需要一起收敛，避免留下死代码。

## 2. 目标与非目标

### 2.1 目标

- G1：分享图、通知转图、Web PNG、桌面端截图、导出 PDF 不再出现小红书二维码/昵称/链接和 GitHub 仓库地址。
- G2：上述导出物料底部统一展示 RHCLOUD PTE. LTD. 的 AI 生成声明，字号小于正文。
- G3：所有分析报告 Markdown（日报、仪表盘、企微、简报、单股、大盘复盘、历史报告重建）末尾统一展示该声明。
- G4：飞书富卡片、轻卡片、紧凑卡片以及纯文本兜底都在页脚展示该声明。
- G5：声明文案只有一个代码真源；旧声明在输出边界被幂等替换，不重复、不误删正文风险提示。
- G6：删除小红书配置、二维码资产和相关打包参数，文档与测试同步收敛。

### 2.2 非目标

- 不修改分析逻辑、LLM Prompt、评分、买卖建议或报告正文结构。
- 不把正文风险提示中的“不构成投资建议”全部删除；本次只处理固定页脚/导出物料。
- 不回填数据库中的历史报告原文；历史报告在再次导出/分享时做渲染期归一化即可。
- 不修改 README 中的小红书赞助二维码和 `docs/assets/xiaohongshu_tick.jpg`。
- 不新增用户可配置的免责声明开关，避免部署矩阵继续膨胀。
- 不把声明翻译成英文/韩文；是否本地化另立需求。
- 不改变飞书 Stream Bot 的交互式问答回复（`bot/platforms/feishu_stream.py` 不走本次卡片发送链路）。

## 3. 统一文案与唯一真源

### 3.1 文案版本

| 版本 | 文案 | 说明 |
| --- | --- | --- |
| 用户原文 | `本数据有RHCLOUD PTE. LTD. 根据AI生成，不构成投资建议。` | 仅记录；“有”疑为“由”的笔误，不直接使用 |
| 建议正式版 | `本数据由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。` | 默认实现；补“由”，英文实体与中文之间加空格，句末保留句号 |
| 最小改动版 | `本数据由RHCLOUD PTE. LTD. 根据AI生成，不构成投资建议。` | 如要求尽量贴近原文空格，只改常量一处 |

默认采用“建议正式版”。如法务要求逐字一致，以最终确认字符串为准；所有入口必须引用同一个常量，禁止各自拼接。

### 3.2 唯一真源

在 `src/report_language.py` 增加模块级常量与幂等工具：

```python
REPORT_AI_DISCLAIMER = "本数据由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。"

_LEGACY_DISCLAIMER_MARKERS = (
    "AI生成，仅供参考，不构成投资建议",
    "AI-generated content for reference only. Not investment advice.",
    "AI 생성 참고용이며 투자 권유가 아닙니다.",
    "仅供研究参考 · 不构成投资建议",
    "AI 生成，仅供研究交流，不构成投资建议",
    "AI 生成，仅供研究交流，不构成投资建议。市场有风险，决策需谨慎。",
)


def strip_report_disclaimers(text: str) -> str:
    """只移除末尾已知的独立声明行，保留正文风险提示。"""


def ensure_report_disclaimer(text: str) -> str:
    """确保文本末尾只有一条统一声明，返回 Markdown 斜体行。"""
```

放在 `report_language.py` 的原因：

- 该模块已经被 `notification.py`、`report_renderer.py`、`market_analyzer.py`、`market_review.py`、`history_service.py` 共用。
- 不新增平行实现，也不会引入循环依赖；`share_image.py`、`feishu_card.py`、`feishu_sender.py` 可以直接导入。
- 如果维护者更倾向独立模块（例如 `src/report_disclaimer.py`），可以调整位置，但必须保持“全仓一个常量、一个 helper”的约束。

### 3.3 幂等规则

- 只处理文本**末尾连续区域**（建议最后 8 行）中的已知独立声明行，不按“包含‘不构成投资建议’”做全文删除。
- 已知声明行可以是斜体、带 `📋`、带前后空白；比较前统一 `strip()` 并去掉首尾 `*`。
- 正文中的“风险提示”“市场有风险，投资需谨慎”等段落不匹配上述完整标记，必须保留。
- 如果末尾已有统一声明：不重复追加。
- 如果末尾是旧声明：先移除，再在最后追加统一声明。
- 如果旧声明和统一声明同时存在：折叠为一条统一声明。
- 应用边界：
  - 各报告生成函数 `return` 前；
  - Jinja2 模板渲染上下文；
  - `build_share_image_html()` 入口（防止旧报告正文把旧页脚带进海报）；
  - 飞书 `_extract_footer()` / `_note()`；
  - 飞书纯文本兜底发送前。

### 3.4 旧声明清理清单

| 旧文案 | 出现位置 | 处理 |
| --- | --- | --- |
| `AI生成，仅供参考，不构成投资建议` | `src/report_language.py` 中文 label，原用于企微摘要/单股页脚 | 替换为统一声明 |
| `AI-generated content for reference only. Not investment advice.` | `src/report_language.py` 英文 label | 替换为统一声明 |
| `AI 생성 참고용이며 투자 권유가 아닙니다.` | `src/report_language.py` 韩文 label | 替换为统一声明 |
| `仅供研究参考 · 不构成投资建议` | `feishu_card.py` `CARD_SUBTITLE` | 改为中性副标题，声明只放页脚 |
| `AI 生成，仅供研究交流，不构成投资建议。市场有风险，决策需谨慎。` | `share_image.py` 海报页脚 | 移除，改用统一声明 |
| `市场有风险，投资需谨慎。以上数据仅供参考，不构成投资建议。` | `market_analyzer.py` 复盘“风险提示”正文 | 保留为正文风险提示；页脚另加统一声明 |

## 4. 改动清单

### 4.1 分享图 / PNG / PDF

#### 4.1.1 `src/share_image.py`

- 删除 `PROJECT_URL`、`PROJECT_REPOSITORY`；保留 `PROJECT_DISPLAY_NAME` 作为品牌标题。
- 删除 `DEFAULT_XIAOHONGSHU_QR_PATH`、`DEFAULT_XIAOHONGSHU_HANDLE`、`ShareImageBranding`、`share_image_branding_from_config()`、`_xiaohongshu_card()`。
- 删除 `_asset_path()`、`_asset_data_uri()` 及仅为二维码服务的 `base64`、`mimetypes`、`sys`、`Path` 导入。
- `_footer()` 改为 `_footer(source_line: str, language: str) -> str`：
  - 保留 DSA 品牌标题、`PROJECT_DISPLAY_NAME` 和 tagline（是否保留品牌区见待确认项）。
  - 删除 `.repo-line`（GitHub 地址）和整个社交卡片区域。
  - 数据源单独一行展示，不再和免责声明拼在同一行。
  - 页脚固定渲染 `REPORT_AI_DISCLAIMER`。
- `build_share_image_html()` 删除 `branding` 参数；入口先调用 `strip_report_disclaimers(markdown_text)`，避免旧报告正文重复带旧页脚。
- 对 `structured_payload` 中承载完整 Markdown 的字段（至少 `markdown_report`，如存在 `review_report` 也同样处理）做尾部声明剥离；使用浅拷贝或渲染期副本，不原地修改调用方字典。
- `__all__` 删除已移除的常量/类/函数。
- `_poster_text()` 删除 `open_source`、`xiaohongshu`、`disclaimer` 三个 key。
- CSS 清理：
  - 删除 `.repo-line`、`.qr-card`、`.qr-frame`、`.social-link` 相关规则；
  - `.poster-footer` 从双列表格改为单列块级布局；
  - 新增 `.poster-source` 小字数据源行；
  - `.disclaimer` 保持底部通栏浅色背景，字号 14px 左右，文案居中。

建议页脚结构：

```html
<footer class="poster-footer">
  <div class="footer-brand full">
    <div class="footer-title"><strong>DSA</strong><span>股票智能分析系统</span></div>
    <small>让股票研究更简单、更高效</small>
  </div>
</footer>
<div class="poster-source">数据源：腾讯财经</div>
<div class="disclaimer">本数据由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。</div>
```

没有数据源时省略 `.poster-source`，不要把空字符串渲染成空行。

#### 4.1.2 调用方

| 文件 | 改动 |
| --- | --- |
| `src/md2img.py` | 删除 `ShareImageBranding` / `share_image_branding_from_config` 导入与 `_share_image_branding()`；`_markdown_to_image_*` 全部去掉 `branding` 参数；`markdown_to_image()` 不再读配置；`build_share_image_html()` 调用去掉 `branding` |
| `api/v1/endpoints/history.py` | 删除 `ShareImageBranding` 导入、`_history_share_image_branding()`；HTML/PNG 两个接口调用 builder 时不再传 `branding` |
| `apps/dsa-web/src/utils/reportPdf.ts` | 无需改代码；PDF 复用海报 PNG，页脚自然继承。补人工/截图验证 |
| `apps/dsa-desktop/` | 无需改代码；桌面端通过 `/share-image-html` 渲染，页脚随 HTML 变化。补构建验证 |
| `src/notification.py` | `_share_image_payload()` 与转图参数不变 |

#### 4.1.3 PDF 边界说明

当前 PDF 是把一张长海报 PNG 按 A4 高度切片，不是逐页重新排版。因此统一声明只会出现在海报末尾，也就是 PDF 的最后一页底部；不会在每一张 A4 页面重复。

如果验收要求“PDF 每一页底部都有声明”，需要另开任务：在 `reportPdf.ts` 用 jsPDF 逐页叠加文字，并解决中文字体嵌入、页边距和图片遮挡问题。本任务默认不做。

### 4.2 Markdown 报告

| 入口 | 文件/函数 | 改动 |
| --- | --- | --- |
| 日报 | `src/notification.py` `generate_daily_report()` 约 924 行 | `return` 前调用 `ensure_report_disclaimer()` |
| 仪表盘（模板路径） | `templates/report_markdown.j2` | 末尾追加 `*{{ report_disclaimer }}*` |
| 仪表盘（fallback） | `src/notification.py` `generate_dashboard_report()` 约 1294 行 | 在模型行之后追加统一声明 |
| 企微仪表盘（模板路径） | `templates/report_wechat.j2` | 末尾追加 `*{{ report_disclaimer }}*` |
| 企微仪表盘（fallback） | `src/notification.py` `generate_wechat_dashboard()` 约 1663 行 | 在模型行之后追加统一声明 |
| 企微摘要 | `src/notification.py` `generate_wechat_summary()` 约 1858 行 | 删除旧 `not_investment_advice` 行；保留“详细报告见”，在最后追加统一声明 |
| 简报（模板路径） | `templates/report_brief.j2` | 末尾追加 `*{{ report_disclaimer }}*` |
| 简报（fallback） | `src/notification.py` `generate_brief_report()` 约 1933 行 | 在模型行之后追加统一声明 |
| 单股报告 | `src/notification.py` `generate_single_stock_report()` 约 1998 行 | 用统一声明替换旧 `not_investment_advice` 行 |
| 模板渲染器 | `src/services/report_renderer.py` 约 263 行 | context 增加 `report_disclaimer=REPORT_AI_DISCLAIMER`；模板统一引用 |
| 历史报告重建 | `src/services/history_service.py` `_generate_single_stock_markdown()` 约 1120 行 | `return` 前追加统一声明，保证 Web 历史详情与分享图一致 |
| 旧 label | `src/report_language.py` 约 449 / 584 / 719 行 | 删除 `not_investment_advice`；确认全仓无调用后同步清理三语 key |

约束：

- 多股报告只在文档最末尾追加一次，不能放进逐股循环。
- 模板路径和 fallback 路径必须产出相同页脚，不能只改其中一条。
- 报告正文里的 `risk_warning`、风险提示小节保持原样，不做全文替换。
- `generate_wechat_summary()` 的“详细报告见”行放在声明之前，声明必须是最后一行。

### 4.3 大盘复盘

| 文件 | 改动 |
| --- | --- |
| `src/market_analyzer.py` 复盘模板约 2097 行 | 在 `复盘时间` 之后追加统一声明；保留“风险提示”正文中的风险句 |
| `src/market_analyzer.py` Prompt 约 1690 / 1710 行 | 本任务不改 Prompt；避免影响 LLM 输出结构。如要求全文只出现一次“不构成投资建议”，另开任务评估 Prompt 调整 |
| `src/core/market_review.py` `_render_market_review_payload_markdown()` 约 518 行 | 结构化重渲染后调用 `ensure_report_disclaimer()`，保证保存/推送的 Markdown 带页脚 |
| `src/core/market_review.py` `_render_market_review_merge_markdown()` 约 531 行 | 合并通知路径同样保证只有一条声明，避免与单市场 Markdown 叠加重复 |
| `src/core/market_review.py` 持久化路径 | `markdown_report` 入库前已带声明；历史详情和分享图再次渲染时由 helper 幂等处理 |

### 4.4 飞书消息

#### 4.4.1 卡片层 `src/notification_sender/feishu_card.py`

- `CARD_SUBTITLE` 从 `仅供研究参考 · 不构成投资建议` 改为中性副标题，建议 `仅供研究参考`；声明只放在 `note` 页脚，避免标题区和页脚各出现一次法律文案。
- `_note()` 改为补齐 `REPORT_AI_DISCLAIMER`，不再补 `CARD_SUBTITLE`。
- `_extract_footer()`：
  - 遇到旧版独立声明行时丢弃，不放进 `notes`；
  - 遇到统一声明行时保留并去重；
  - 同行如果还带数据源/模型信息，只剥离声明片段，保留其余元信息。
- `_META_KEYWORDS` 增加 `RHCLOUD`，确保统一声明行能被识别为页脚元信息。
- `build_feishu_card()` 紧凑卡片路径：
  - 使用已剥离页脚的 `body_lines` 组装 `compact_body`，避免声明同时出现在正文和 `note`；
  - `compact_elements` 追加 `_note(notes)`；
  - 没有正文时返回 `[_note(notes)]`；
  - 继续用 `_fits()` 校验字节预算，超限返回 `None`，交给发送层纯文本兜底。
- 富卡片、轻卡片、紧凑卡片三条路径都必须带 `note`；`note` 中声明保持在最后。

#### 4.4.2 发送层 `src/notification_sender/feishu_sender.py`

- `send_to_feishu()` 在 `strip_hidden_markdown_metadata()` 之后调用一次 `ensure_report_disclaimer()`，保证卡片正文有统一页脚；文件模式由下面 `send_feishu_file()` 单独处理。
- `_app_send_once()` 纯文本兜底：在 `json.dumps({"text": ...})` 前调用 `ensure_report_disclaimer()`。
- `_send_via_webhook()` 纯文本兜底：在 `_apply_keyword_prefix()` 之前调用 `ensure_report_disclaimer()`，确保关键词前缀在前、声明在最后。
- `_send_feishu_chunked()`：
  - 成功走卡片的每个分片由 `_note()` 自动补声明；
  - 某个分片降级纯文本时，由 `_send_feishu_message()`/`_send_via_webhook()` 的兜底逻辑补声明；
  - 不为每个卡片分片手工追加正文声明，避免正文重复。
- `send_feishu_file()` / `NotificationService.send_feishu_file()`：文件模式保存的 Markdown 也必须带声明；如果上游内容没有，保存前调用 `ensure_report_disclaimer()`。
- 关键词校验：`_add_keyword_to_card_footer()` 把关键词插入 `note` 开头，声明仍在最后；现有 `FEISHU_KEYWORD` 逻辑不变。

#### 4.4.3 范围

- 覆盖 `send_to_feishu()` 的主动推送，包括 Webhook 与 App Bot 两条链路、报告推送、告警和测试消息。
- 不覆盖 `bot/platforms/feishu_stream.py` 的 Stream Bot 交互式回复；该链路不使用 `build_feishu_card()`。
- 如只希望对“分析报告”追加声明，可在 `send_to_feishu()` 增加 `route_type == "report"` 判断；默认建议按“所有飞书通知消息”处理，和用户原话保持一致。

### 4.5 配置、资产与打包

| 文件 | 改动 |
| --- | --- |
| `src/config.py` 约 1298-1301 行 | 删除 `share_image_xiaohongshu_url/handle/id/qr_path` 四个字段 |
| `src/config.py` 约 2311-2314 行 | 删除四个 `SHARE_IMAGE_XIAOHONGSHU_*` 环境变量解析 |
| `.env.example` 约 766-769 行 | 删除小红书配置注释与示例 |
| `src/assets/share_image/xiaohongshu_qr.jpg` | 删除；目录为空时一并删除 |
| `scripts/build-backend-macos.sh` 约 124 行 | 删除 `--add-data "src/assets/share_image:src/assets/share_image"` |
| `scripts/build-backend.ps1` 约 144 行 | 删除 `'--add-data', 'src/assets/share_image;src/assets/share_image'` |
| `docs/assets/xiaohongshu_tick.jpg` | 保留；这是 README 赞助区素材，不属于导出物料 |
| `README.md` / `docs/README_CHT.md` | 保留小红书赞助二维码，不在本任务范围 |

部署兼容说明：已配置的 `SHARE_IMAGE_XIAOHONGSHU_*` 环境变量在升级后会被忽略，不会导致启动失败；文档需明确这些变量已失效，避免用户误以为仍可自定义分享图品牌。

### 4.6 文档

| 文件 | 改动 |
| --- | --- |
| `docs/share-images.md` | 重写首段与“视觉与内容边界”；删除小红书配置章节、代码示例中的 `ShareImageBranding`；补充新页脚、数据源独立行、PDF 继承说明 |
| `docs/notifications.md` | 更新飞书卡片页脚说明（约 63 行）和图片分享模板说明（约 133-135 行）；删除小红书配置；写明紧凑卡片也带声明 |
| `docs/full-guide.md` | 删除 `SHARE_IMAGE_XIAOHONGSHU_*` 三行配置说明（约 143-145 行） |
| `docs/full-guide_EN.md` | 删除对应三行英文配置说明（约 131-133 行） |
| `docs/CHANGELOG.md` | `[Unreleased]` 增加扁平格式 `[改进]` / `[文档]` 条目，不新增 `###` 类目标题 |
| `docs/ai/CODEMAP.md` | 运行 `python scripts/gen_ai_codemap.py` 重新生成；删除 `ShareImageBranding` 等符号后再跑 `--check` |
| `docs/ai/STATUS.md` | 可选：如果该任务进入当前迭代跟踪，补一行状态；否则不改 |

## 5. 验收标准

### 5.1 分享图 / PNG / PDF

- 分享图 HTML、PNG、Web PDF、桌面端截图都不包含：
  - `小红书`
  - `xiaohongshu`
  - `ZhuLinsen/daily_stock_analysis`
  - `GitHub`
  - `qr-frame` / `data:image`
- 页脚只出现一次 `REPORT_AI_DISCLAIMER`，且位于海报最底部。
- 数据源独立成行，不与声明混写；无数据源时不渲染空行。
- 旧报告 Markdown 自带旧页脚时，导出结果不出现旧声明，只出现新声明一次。
- 1080px 宽度、页脚背景通栏、字号明显小于正文；PDF 最后一页底部可见声明。

### 5.2 Markdown 报告

- `generate_daily_report()`、`generate_dashboard_report()`（模板/fallback）、`generate_wechat_dashboard()`（模板/fallback）、`generate_wechat_summary()`、`generate_brief_report()`（模板/fallback）、`generate_single_stock_report()` 均以统一声明结尾。
- 多股报告只出现一次声明，不在每只股票后重复。
- 大盘复盘 Markdown（单市场/合并/结构化重渲染/历史保存）均以统一声明结尾。
- 历史报告 `_generate_single_stock_markdown()` 同样带声明。
- 英文/韩文报告仍显示中文统一声明（待确认）；不再出现旧三语免责声明。
- 正文风险提示段落保留，不被 helper 误删。

### 5.3 飞书

- 富卡片、轻卡片、紧凑卡片最后都有 `note`，内容包含统一声明。
- 紧凑卡片不再出现“没有 note”的路径；短消息也带声明。
- `CARD_SUBTITLE` 不再包含“不构成投资建议”。
- 旧报告内容中的旧声明不会在 `note` 中重复出现。
- 纯文本兜底（Webhook/App Bot）包含统一声明。
- 长报告分片：每个成功卡片分片的 `note` 都有声明；降级纯文本的分片也有声明。
- 关键词、加签、分片预算、文件发送逻辑不回归。

### 5.4 清理

- 全仓运行 grep，运行路径和文档中不再出现：
  - `SHARE_IMAGE_XIAOHONGSHU`
  - `share_image_xiaohongshu`
  - `ShareImageBranding`
  - `DEFAULT_XIAOHONGSHU`
  - `xiaohongshu_qr`
  - `PROJECT_REPOSITORY`
- 允许保留的例外：`README.md`、`docs/README_CHT.md` 中的小红书赞助区，以及 `docs/assets/xiaohongshu_tick.jpg`。
- `src/config.py` 不再暴露四个小红书字段；`.env.example` 不再出现对应变量。
- 两个后端打包脚本不再引用 `src/assets/share_image`。

### 5.5 质量门禁

- `python -m pytest -m "not network"` 通过。
- `./scripts/ci_gate.sh` 通过。
- `cd apps/dsa-web && npm run lint && npm run build` 通过（即使前端无代码改动，也建议跑一次确认分享图接口未受影响）。
- `python scripts/check_ai_assets.py` 通过。
- `python scripts/gen_ai_codemap.py --check` 通过。
- PR 描述附前后对比截图：分享图 PNG、PDF 首页/末页、飞书富卡片/紧凑卡片、纯文本兜底。截图只放 PR 描述或附件，不入库。

## 6. 测试矩阵

| 测试文件 | 更新/新增用例 |
| --- | --- |
| `tests/test_share_image.py` | 删除全部小红书/品牌配置用例；新增“页脚无社交/仓库地址”“统一声明只出现一次”“数据源独立行”“旧页脚被剥离”“个股/大盘/聚合三类海报一致” |
| `tests/test_md2img_template.py` | 删除 `TEST_BRANDING` 与配置转发用例；断言三种渲染器收到的 HTML 都含统一声明且不含仓库/二维码；更新调用签名 |
| `tests/test_history_share_image.py` | 断言 HTML/PNG 接口不再传 `branding`；旧报告输入经 builder 后只保留新声明 |
| `tests/test_config_env_compat.py` | 删除四个 `share_image_xiaohongshu_*` 断言；补一条“Config 不再暴露小红书字段”的防回归断言 |
| `tests/test_feishu_card.py` | 更新短消息紧凑卡片元素数量；断言富/轻/紧凑 `note` 都含统一声明；旧声明被替换；`CARD_SUBTITLE` 不再含法律文案；超预算仍返回 `None` |
| `tests/test_feishu_f1_hotfix.py` | 断言纯文本兜底、关键词 note、分片发送都带统一声明；关键词仍在 note 开头 |
| `tests/test_notification.py` | 对六个生成函数做参数化断言：末尾一条统一声明、旧 label 不存在、模板/fallback 一致、多股只出现一次 |
| `tests/test_report_renderer.py` | 断言 `report_markdown` / `report_wechat` / `report_brief` 三个模板输出末尾带声明 |
| `tests/test_report_language.py` | 断言 `REPORT_AI_DISCLAIMER` 常量；测试 `strip_report_disclaimers()` 保留正文风险句、只删末尾旧声明、重复调用幂等 |
| `tests/test_market_analyzer_generate_text.py` | 断言复盘模板/LLM 输出最终 Markdown 以统一声明结尾 |
| `tests/test_market_review.py` | 断言单市场、合并、结构化重渲染三条路径都只有一条声明 |
| `tests/test_analysis_history.py` | 断言 `get_markdown_report()` 返回值带统一声明 |
| `tests/test_signal_attribution_e2e.py` / `test_signal_attribution_real_paths.py` | 审计现有断言，确认新增页脚不影响正文匹配；必要时补末尾声明断言 |

建议的专项命令：

```bash
source .venv/bin/activate
python -m pytest -m "not network" \
  tests/test_share_image.py \
  tests/test_md2img_template.py \
  tests/test_history_share_image.py \
  tests/test_feishu_card.py \
  tests/test_feishu_f1_hotfix.py \
  tests/test_notification.py \
  tests/test_report_renderer.py \
  tests/test_report_language.py \
  tests/test_market_analyzer_generate_text.py \
  tests/test_market_review.py \
  tests/test_analysis_history.py
./scripts/ci_gate.sh
```

如果本机没有 `python` 命令，使用项目虚拟环境或 `python3`；以 `AGENTS.md` 的验证矩阵为准。

## 7. 实施顺序

1. **基线确认**：从最新 `main` 拉分支，确认工作区干净；记录当前分享图/PDF/飞书卡片截图作为 before。
2. **统一常量与 helper**：在 `report_language.py` 落常量、剥离/幂等函数及单测；先不接业务。
3. **分享图与导出链路**：改 `share_image.py` 页脚/CSS/签名，改 `md2img.py`、`history.py` 调用方，删除资产/配置/打包参数。
4. **Markdown 报告**：改三个模板、`report_renderer.py`、`notification.py` 六个生成函数、`history_service.py`。
5. **大盘复盘**：改 `market_analyzer.py` 模板与 `market_review.py` 两个渲染函数。
6. **飞书**：改 `feishu_card.py` 的 `_note`/`_extract_footer`/紧凑卡片，再改 `feishu_sender.py` 纯文本兜底与文件发送。
7. **文档与治理**：更新 `docs/share-images.md`、`docs/notifications.md`、中英文 full-guide、CHANGELOG；重新生成 CODEMAP。
8. **全量验证**：跑专项测试、`ci_gate.sh`、Web lint/build；补 before/after 截图。
9. **提交与 PR**：按 `AGENTS.md` 交付结构写 PR body；未经确认不执行 `git commit` / `git push` / `gh pr create`。

如果改动面过大，可按三个 PR 拆分：A 分享图/配置/打包清理，B Markdown 报告页脚，C 飞书卡片与纯文本兜底。但每个 PR 必须独立可回滚，且不能出现中间态重复声明。

## 8. 风险与回滚

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 删除小红书配置导致自定义部署外观变化 | 升级后分享图不再显示自定义二维码/昵称 | 文档明确变量失效；这是本次需求预期变化；回滚可恢复旧代码与资产 |
| 页脚变短导致 PDF 分页/截图基线变化 | 视觉回归、快照差异 | 更新 before/after 截图；检查 1080px 宽度、末页声明可见 |
| 紧凑卡片追加 note 后超出 `FEISHU_MAX_BYTES` | 卡片降级为纯文本 | 保留 `_fits()` 判断；纯文本兜底也补声明；测试超预算路径 |
| 长报告分片后只有最后一片有正文声明 | 前面分片可能看不到声明 | 卡片 `note` 每片自动补；纯文本兜底每片补；测试分片发送 |
| 旧报告声明变体未覆盖，出现重复/遗漏 | 历史报告导出不一致 | 用已知文案清单 + 末尾区域匹配；补旧报告 fixture；不做全文关键词删除 |
| helper 误删正文风险句 | 报告内容缺失 | 只匹配完整旧声明行，不匹配“包含不构成投资建议”的任意句子；单测覆盖风险段落 |
| 英文/韩文报告出现中文声明 | 观感不统一 | 这是“统一合规文案”的预期；如需要本地化，另立需求并补三语常量 |
| 历史报告入库内容未回填 | 旧记录直接看原文仍带旧声明 | 本次只在渲染/导出边界归一化；如需改库，另做迁移并评估风险 |
| 打包脚本漏删资产路径 | 桌面端构建失败 | 全仓 grep `src/assets/share_image`；跑 macOS/Windows 构建脚本静态检查 |

回滚方式：整体 revert 本次 PR 即可。没有数据库 schema 变更、没有数据迁移、没有新增依赖；旧配置和二维码资产随代码回滚恢复。历史报告在回滚后按旧逻辑渲染。

## 9. 待确认项

以下条目已在实施前确认，按“建议默认”落地；此处保留作为决策记录。

| # | 问题 | 建议默认 | 影响 |
| --- | --- | --- | --- |
| 1 | 文案用“有”还是“由”？空格如何保留？ | 用“由”，采用建议正式版 `本数据由 RHCLOUD PTE. LTD. 根据 AI 生成，不构成投资建议。` | 全仓唯一常量；确认后只改一处 |
| 2 | 飞书范围是仅分析报告，还是所有 `send_to_feishu()` 消息？ | 所有飞书通知消息；Stream Bot 交互回复不变 | 影响告警、测试消息是否带声明 |
| 3 | PDF 是只在文档末尾出现一次，还是每张 A4 页都要有？ | 只在末尾/最后一页；当前 PDF 是长图切片 | 每页都要需另做 jsPDF 文字叠加和中文字体 |
| 4 | 页脚是否保留 DSA 品牌标题和 tagline？ | 保留品牌区，只删 GitHub 仓库行和小红书区域 | 影响页脚高度和 PDF 分页 |
| 5 | 英文/韩文报告是否也显示中文声明？ | 是，保持法律文案一致 | 如要翻译，需新增三语常量并确认译文 |
| 6 | 历史报告再次导出时是否替换旧声明？ | 是，渲染期剥离旧声明并追加新声明 | 不修改数据库原文 |
| 7 | 小红书配置是彻底删除，还是保留但忽略？ | 彻底删除，文档说明变量失效 | 删除更干净；保留会产生死代码 |
| 8 | 数据源信息保留还是删除？ | 保留，单独一行小字 | 用户只要求删广告/仓库地址，未要求删数据源 |
| 9 | README 小红书赞助二维码是否处理？ | 不处理，属于仓库首页赞助区 | 与导出物料无关 |
| 10 | `RHCLOUD PTE. LTD.` 是否为最终法定名称、大小写和标点？ | 按用户原文大写与句点 | 法务文案需最终确认 |

## 10. 交付要求

- PR 标题建议：`feat: 统一报告导出与飞书消息底部 AI 声明`。
- PR 描述按 `AGENTS.md` 写清：
  - 改了什么；
  - 为什么这么改；
  - 验证情况；
  - 未验证项；
  - 风险点；
  - 回滚方式。
- 必须附 before/after 可视证据：
  - 个股分享图 PNG；
  - 大盘复盘分享图 PNG；
  - PDF 首页与末页；
  - 飞书富卡片、紧凑卡片、纯文本兜底。
- 截图只放 PR 描述、评论、Actions artifact 或外部链接，不作为仓库文件合入。
- 未经明确确认，不执行 `git commit`、`git tag`、`git push`、`gh pr create`。
- 实施完成后同步更新 `docs/CHANGELOG.md` 的 `[Unreleased]`，使用扁平格式，例如：
  - `- [改进] 报告导出与飞书消息底部统一展示 RHCLOUD PTE. LTD. AI 生成声明，移除分享图/PDF 的小红书二维码和 GitHub 仓库地址。`
  - `- [文档] 新增报告页脚统一声明开发任务书（docs/tasks/report-footer-ai-disclaimer.md）。`
