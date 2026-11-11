# 校园拾集 · 实验三代码（v0.4.6）

本目录是《软件工程》实验三的 **code/** 提交副本，可独立运行。项目实现了校园二手商品发布、审核、预约、成交、统计与日志。与实验一最新已确认需求一致；不支持在线支付、物流和即时聊天。

- **需求依据**：`docs/requirements_v12_confirmed.md`（REQ-001～020）。
- **设计依据**：提交包同级 `../UML/`（U1～U7，其中 U4 为 REQ-005 V1.2 对应实现版）。
- **界面参考**：`docs/UI_reference/`（UI-01～11）。
- **架构**：Flask + SQLite + Python 服务层，原生 JS/CSS，详见 `app/`。
- **版本**：v0.4.6。根据 2026-11-11 提供的独立 QA 结论，168 项 pytest、6 项 JS、缺陷回归、1600 步独立模型、跨操作并发、构建烟测通过；这些验收报告的完整证据保存在原 D 盘 QA 工作区，并未随课程 ZIP 打包。此说明不代替现场复跑。

## Windows 启动（推荐使用 D 盘）

以下命令在 **解压后本 `code/` 目录**的 PowerShell 中执行。要求安装 Python 3（与 Flask 3.1 兼容），浏览器访问本地服务即可。

```powershell
$ErrorActionPreference = 'Stop'
$here = (Get-Location).Path
if (-not (Test-Path -LiteralPath '.\requirements.txt')) { throw '请先切换到 code 目录' }
$env:TEMP = 'D:\CourseProjects\CampusMarket\temp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\CourseProjects\CampusMarket\pip-cache'
$env:PYTHONPYCACHEPREFIX = 'D:\CourseProjects\CampusMarket\pycache'
New-Item -ItemType Directory -Force -Path $env:TEMP,$env:PIP_CACHE_DIR,$env:PYTHONPYCACHEPREFIX | Out-Null
py -3 -m venv 'D:\CourseProjects\CampusMarket\.venv-lab3'
$python = 'D:\CourseProjects\CampusMarket\.venv-lab3\Scripts\python.exe'
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '安装依赖失败' }
& $python -m scripts.create_admin admin
if ($LASTEXITCODE -ne 0) { throw '创建管理员失败' }
& $python -m flask --app app:create_app run --host 127.0.0.1 --port 5000
```

`create_admin` 会交互式提示输入不少于 8 字符的管理员密码。请仅在首次初始化新数据库时创建该管理员。打开 `http://127.0.0.1:5000/`，普通用户在界面中注册；已有数据库重新启动时，直接执行最后一条 Flask 命令。项目数据库默认位于 `code/instance/campus_market.sqlite3`，首次启动可自动创建。请勿把实际数据库提交到 Git 或课程平台。

## 测试、构建与代码量

在安装依赖后运行：

```powershell
& $python -m pytest -q
& $python -m scripts.demo_flow
& $python -m scripts.count_backend_loc
node --check .\app\static\js\site.js
node .\qa_tests\frontend_vm_v042.cjs
```

Node.js 仅用于可选的前端专项测试，网页正常使用不依赖 Node.js。严格非 GUI Python 代码行数由脚本报告，排除了测试、GUI、注释和 docstring；最终打包前为 1690 行（请以再次运行的输出为准）。

## 代码结构

- `app/api/`：REST API 与授权入口。
- `app/core/`：校验、异常、商品/预约状态规则。
- `app/db/`：SQLite schema、迁移与数据读取。
- `app/services/`：身份、商品、审核、预约、成交、日志与统计等业务层。
- `app/static/`、`app/templates/`：11 页响应式 Web 前端。
- `tests/`：pytest 单元与 HTTP/集成回归。
- `qa_tests/`：前端 JS 与布局/弹窗专项测试工具（部分浏览器专项需要额外安装 Playwright）。
- `scripts/`：管理员初始化、最小演示、代码量统计与查询性能探针。
- `docs/`：已确认需求、设计追踪、实现演进、UI 参考及开发事实记录。

## 与实验二设计的关系

实验二提交的 U1～U7 原图保存在历史 Git 提交中。实验三根据确认后的 REQ-005 更新了 **提交副本中的 U4**：在售商品的六字段实际修改先转 `DRAFT`，卖家另行提交审核进入 `PENDING_REVIEW`。更新后的 `.puml` 是权威可编辑源，`.png` 为同等状态和连线语义的本地 Graphviz 排版图。其他 U1～U3/U5～U7 直接继承实验二原版。

## 提交说明

课程 ZIP 另有 `UML/` 和由学生本人独立撰写的 `实验报告.pdf`。本目录仅存源码、运行依赖、测试和必要的实现说明，不包含 Git 历史、虚拟环境、QA 截图/trace、SQLite 测试数据库等中间文件。
