# v0.4.5 修复记录：QA-044-001

## 缺陷与证据

以 v0.4.4 为基线。独立 QA `others(2).zip` 中 `BUGS.md` 报告：1000 个连续 `X` 的标题及 1000 个连续 `D` 的描述是合法输入；公开商品详情在 1440×1000 下 `document.scrollWidth=16722`，在 390×844 下为 `16426`。正常短文本无明显溢出。截图与 `extreme_layout.json` 可重现。

原因：`.detail-layout` 的 `1fr 1fr` 网格轨道具有基于内容的自动最小宽度；未提供断词机会的长字符串使第二轨道无法按视口收缩，`.detail-info` 中标题和描述也缺少断行策略。

## 修改范围

仅修改 `app/static/css/site.css`：

1. 双栏网格采用 `repeat(2,minmax(0,1fr))`，移动端单栏采用 `minmax(0,1fr)`；
2. 设置详情页两侧 `.panel` 的 `min-width:0`，允许网格子项收缩；
3. 为 `.detail-info` 设置 `overflow-wrap:anywhere`，使 1000 字连续字符串、长 URL、交易地点和公开用户名能够在卡片内合理折行；
4. 保持原文本完整，不加 `overflow:hidden`、省略号或截断逻辑，不调整 API、数据库、身份校验、业务状态机。

新增 `tests/test_v045_layout.py`（结构性回归）与 `qa_tests/layout_stress_v045.py`（实际 Chrome/Chromium 组件布局烟测）。后者只模拟真实 `productDetail()` 输出的 DOM 与真实 CSS，**不能替代 Flask 服务上的端到端 Playwright**。请独立 QA 继续使用真实服务、合法字段和公开产品详情验证。

## 下一轮最低验收

- **必测 P2 复现**：通过真实 UI 创建合法 1000 字标题/描述，审核后打开 `/#/product/<id>`，分别在 1440×1000 与 390×844 下检查 `documentElement.scrollWidth <= innerWidth+1`，标题、描述完整可见；同时复测超长英文 URL 和连续中文。
- 检查移动端的商品示意图、操作按钮、价格、卖家信息和描述可操作、可读，不能靠裁剪隐藏溢出文本。
- 复跑 v0.4.4 全量 pytest（报告中为 163 项；新增 2 项后预计 165 项）、JS 6 项、Chrome 关键旅程、原 P0、REQ-005、原四项 P2、并发与约定性能。以实际测试发现为准。
- 对 U1～U7 原版 14 文件、数据模型和正式源码重新做哈希核验。实验二 UML 不应变化。
- 更新 QA 报告 `RUN_SUMMARY.md`、`BUGS.md`、`UI_DIFF.md`、`COVERAGE_MATRIX.md`、`NOT_RUN.md`，标明 P2 已关闭或仍未关闭，记录全部条件和证据。

## 本地测试限制

本工作环境未安装 Flask，因此无法完整执行 Flask HTTP pytest；Python 与 Node.js 测试、Chrome 组件布局测试可在可用环境中执行。不能将旧版 Codex 的 163/163 成绩视为 v0.4.5 的全量成绩。
