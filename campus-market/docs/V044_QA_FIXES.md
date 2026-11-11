# v0.4.4 开发变更及验证记录（非课程实验报告）

## 基线、来源与状态

- 开发基线：v0.4.3。独立 Codex 真实 Chrome 验收：pytest 142/142、JS 6/6、浏览器 25/29，确认 P2×4（QA-043-001~004），无新 P0/P1。
- 新业务依据：Git `a5d07a9` 所更新的 REQ-005 V1.2，6 个可编辑字段、有效预约禁止编辑、修改后 `DRAFT`、卖家单独提交审核。`docs/requirements_v12_confirmed.md` 为供开发参考的当前需求快照。
- 本轮目标：v0.4.4 定向整改、补足回归；**不宣称真实 Windows Chrome 或 HTTP 性能通过，未通过最终交付门槛**。

## 逐项改变

| 编号 | 原问题 | 实际更改 | 验收重点 |
|---|---|---|---|
| REQ-005 | 在售编辑仅名称/价格，直接待审，与 V1.2 不符 | `products.edit_on_sale_core` 允许六字段真实变更，检查归属与有效预约，原子 `ON_SALE → DRAFT`；保存后须单独提交审核。无变化返回 `NO_CHANGES`，新增审计动作 `PRODUCT_EDIT_TO_DRAFT` | 六字段分别测、全量编辑、同时改价、有效预约 PENDING/CONFIRMED 阻止、旧页面、审核历史、回滚 |
| QA-043-001 | UI-11 日志显示最近 100 条，历史无入口 | `logsPage` 每页20条，API limit21+offset，上一页/下一页，查询重置页码 | 0/1/20/21/100/101/227条、组合筛选、翻页后重查、权限 |
| QA-043-002 | UI-06 买家历史最多100条 | 买家页独立 `bookingOffsets.bookings`；翻页与状态筛选联动 | 101/128条、取消理由、单账号私有数据、切换身份清空 |
| QA-043-003 | UI-07 卖家历史最多100条 | 卖家页独立 `bookingOffsets.received`；翻页与状态筛选联动 | 101/129条、卖家确认/拒绝/成交、切换账号 |
| QA-043-004 | UI-02 公开详情无卖家 | `/api/products/<id>` 只读关联查询 `users.username AS seller_name`，页面显示卖家账号名 | 公开在售有卖家；未审核/撤回不可公开；不暴露 password_hash/token |

实现中数据库表结构及预约四态保持不变；商品七态仍为七种，但新增 V1.2 `ON_SALE → DRAFT`，移除直接 `ON_SALE → PENDING_REVIEW` 业务边。新的审计动作沿用现有 `operation_logs` 表，无数据库迁移。

## 图纸及差异

- `UML/U1`～`U7` `.puml`/`.png` 是实验二原始设计文档，**保持逐字节不修改**，不能把新版当前实现误称“与原 U4 完全一致”。
- `docs/implementation_U4_v12.puml` 是依据新确认需求的**演进版**商品状态图，具体改变为 `ON_SALE → DRAFT → PENDING_REVIEW`，其余主要状态边保留。原 UI-04 的编辑页承载六字段和两个不同状态下的按钮；UI-02 新增卖家账号名，UI-06/07/11 增加分页入口。
- 旧 `docs/V03_*`、`docs/V042_*`、`docs/V043_*` 和 `docs/requirements_lab1_final.md` 是**历史快照**，其中“REQ-005 未确认”等仅对应当时版本，不代表当前开发状态。详见 `docs/V044_TRACEABILITY.md`。

## 本地执行与证据等级

- 在当前容器环境，`python -m pytest` 实测 **122 passed，7 个 Flask 测试模块 skipped**（本地缺少 Flask）。新增 `test_v044_http.py` 也属 Flask 路径，只有 Windows 安装依赖后才能确认。
- 旧 3 个与 V1.2 冲突的断言已按**实际新需求**调整，并额外增加独立测试覆盖完整修改流程、日志事务失败回滚及分页边界；这不是删除旧测试或忽略失败。
- `node --check app/static/js/site.js` 与 `node qa_tests/frontend_vm_v042.cjs` 实测通过（原 6/6 通过）。
- 当前受执行环境限制，Chromium 导航会返回 `ERR_BLOCKED_BY_ADMINISTRATOR`，不能把任何模拟 UI 请求当作真实浏览器验收结果。
- Windows 全量 pytest/Flask、真实 Chrome/Playwright、HTTP 性能及 11 页实际 UI 仍**待 Codex 独立复验**。不得复用 v0.4.3 性能数字作为 v0.4.4 成绩。

## 设计与安全相关的边界

- 所有已完成历史预约和日志仍可查询，但使用 offset 页码时在并发新增记录的情况下可能出现跨页重复/漂移；当前课程要求未规定严格快照分页。独立 QA 应在稳定数据集下核验全部记录唯一性，并将真实同时写入场景作为额外稳定性测试。
- 卖家信息展示的是用户自行设置的**公开账号名**，不能宣称其为已验证学号。管理员的审核资料查询继续限 ADMIN。
- 无变化的已发布商品表单提交返回错误，不会因误点击从在售撤回；若需要“完全无变化也重新审核”的功能，必须另行确认需求。
- 未对实际网络丢包、低视力屏幕阅读器、多浏览器兼容性、生产环境安全性作出保证。
