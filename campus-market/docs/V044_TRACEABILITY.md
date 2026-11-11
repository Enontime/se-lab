# v0.4.4 需求—UML—UI—测试追踪表（开发核对）

本表是**实现阶段的交叉对照**，不修改实验二原始 UML/PNG 或课程实验报告。`PASS(local)` 表示 Python/JS 当前本地验证，`PENDING(Chrome)` 表示独立真实 Chrome 尚待验证。

| 需求 | UML/模块 | UI | v0.4.4 对应代码 | 本轮必须的实际核验 |
|---|---|---|---|---|
| REQ-001/002/019 登录权限、隔离 | U1/U2/M1 | UI-03 | auth.py、api/routes.py、site.js | 登录/注销、旧200/401、越权、跨账号残留 PENDING(Chrome) |
| REQ-003/004 草稿审核 | U4/M2-M3 | UI-04/05/08 | products.py、reviews.py | 草稿、驳回、重提、发布/私有隔离 PASS(local) |
| **REQ-005** 已发布修改 | **U4 演进版**、U6 | UI-04/05 | **edit_on_sale_core、state_rules、site.js** | 六字段单独变更，ON_SALE→DRAFT→PENDING_REVIEW→ON_SALE；无预约、有效预约、空字段、无变化、版本与审计 PASS(local), PENDING(Chrome) |
| REQ-006 下架违规 | U4/U7/M3-M4 | UI-05/08 | market.py、violations.py | 预约原子取消、原因、处理后重审 PASS(local) |
| REQ-007/018 查询及性能 | U1/U2/M5 | UI-01 | catalog.py | 组合筛选、价格单位、万件 20并发 1000 HTTP 待新版本实测 |
| REQ-008/012/013/020 预约并发幂等 | U5/U6/M4 | UI-02 | reservations.py、market.py | 20组竞争、旧价格版本、幂等、多账号 PENDING(Chrome) |
| REQ-009/010/011 预约处理/历史 | U5/U6/M4 | **UI-06/07** | read_models.py、**site.js** | 100/101/128/129条、筛选、下一页、完整性、取消理由 PASS(local API), PENDING(Chrome) |
| REQ-014/015 旧页和一致性 | U3/U4/U5/U6/U7 | UI-02/04/05 | db/schema.sql、market.py | 改价和分类后的旧页提交、有效预约索引、回滚、售出终态 PENDING(Chrome) |
| REQ-016 商品交易统计 | U1/M5 | UI-09/10 | catalog.py | 状态计数、日期及分类、重置 PENDING(Chrome) |
| REQ-017 操作日志 | U2/U7/M6 | **UI-11** | audit.py、**site.js** | 100/101/227条、组合筛选、页码清空、日志原子性 PASS(local API), PENDING(Chrome) |

## 七张 UML 的实际状态

| UML | 本版状态 |
|---|---|
| U1 用例图 | 用例不变；UI-06/07/11 补可达性，待真实 Chrome 核验 |
| U2 组件图 | 六模块分层不变；无结构重构 |
| U3 核心领域类图 | 五个核心实体关联不变；补充公开详情查询 `username`，不增加实体 |
| U4 商品状态图 | **原图是历史基线，V1.2 已发生变更**；在 `docs/implementation_U4_v12.puml` 中新增 `ON_SALE→DRAFT` 并取消旧直达待审核边 |
| U5 预约状态图 | 预约四态不变 |
| U6 预约时序图 | 继续采用 SQLite BEGIN IMMEDIATE 与部分唯一索引；原图 FOR UPDATE 为设计层泛化，罕见冲突异常分类仍待专项核查 |
| U7 下架时序图 | 同事务下架、取消预约、写日志不变 |

## 11 张 UI 的逐页整改/回归要求

UI-01 浏览与筛选：维持；UI-02 详情：**卖家账号名**；UI-03 注册登录：旧200/401跨账号安全；UI-04 发布/编辑：**六字段与先草稿后送审**；UI-05 我的商品：检查新草稿、原有101件分页；UI-06 买家预约：**历史分页**；UI-07 卖家预约：**历史分页**；UI-08 审核：审查新草稿不可直接批准、正常重审；UI-09 个人统计：撤回导致状态计数变化；UI-10 平台统计：保持日期/分类查询；UI-11 管理日志：**无限历史分页（通过页码访问，不是一次加载全部）**。

功能缺陷修复完成不意味着零 UI 差异。视觉细节以实验二 `docs/UI_reference` 原图为准，尚需真实浏览器对照，不能凭静态字符串检查判 PASS。
