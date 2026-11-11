# 实验三设计版本说明（技术记录，非实验报告）

- 项目版本：v0.4.6；需求基线：`requirements_v12_confirmed.md`。
- 实验二 UML 原始记录不修改；提交 `../UML/U4_商品状态图.puml` 使用 REQ-005 V1.2 版本。
- 关键差异：原图 `ON_SALE → PENDING_REVIEW`，当前实现为 `ON_SALE → DRAFT → PENDING_REVIEW`。
- 其他 U1/U2/U3/U5/U6/U7 为实验二文件的原样复制；U6 描述的锁操作由 SQLite `BEGIN IMMEDIATE` 实现等效业务串行化。
- `U4_商品状态图.png` 与 `.puml` 均含 7 个状态、相同转移边和文字语义。PNG 在本交付环境中用 Graphviz 排版，PlantUML 源文件保持可编辑。
- QA：完整独立验证报告位于用户 D 盘独立 QA 目录，未随提交包复制；复验以运行测试为准。

本文件用于追溯设计差异，不是学生本人实验报告的替代文本。
