/* 校园拾集 v0.4.4: 11 screens mapped to UI-01..UI-11, backed by the real Flask API.
   This file is presentation code and is excluded from the 1500-line backend target. */
(() => {
  "use strict";

  const app = document.getElementById("view");
  const topNav = document.getElementById("top-nav");
  const account = document.getElementById("account-bar");
  const toastArea = document.getElementById("toast");
  const productStatuses = {
    DRAFT: "草稿", PENDING_REVIEW: "待审核", REJECTED: "审核驳回",
    ON_SALE: "在售", DELISTED: "已下架", VIOLATION_DELISTED: "违规下架", SOLD: "已售出"
  };
  const bookingStatuses = {
    PENDING: "待确认", CONFIRMED: "已确认", CANCELLED: "已取消", COMPLETED: "已完成"
  };
  const roleLinks = [
    ["/browse", "商品首页"], ["/mine", "我的商品"], ["/bookings", "我的预约"],
    ["/received", "收到的预约"], ["/publish", "发布商品"], ["/seller-stats", "我的统计"]
  ];
  const adminLinks = [["/review", "商品审核"], ["/admin-stats", "平台统计"], ["/logs", "操作日志"]];
  const state = {
    token: sessionStorage.getItem("campus_market_token") || "",
    user: null, catalog: {keyword: "", category: "", min_price: "", max_price: "", availability: "", offset: 0},
    selected: {mine: "", bookings: "", received: "", review: "PENDING"},
    authMode: "login", afterLogin: "/browse", mineOffset: 0,
    bookingOffsets: {bookings: 0, received: 0}, logOffset: 0,
    reviewOffsets: {PENDING: 0, APPROVED: 0, REJECTED: 0, VIOLATION: 0},
    requestIds: new Map(), renderId: 0, authVersion: 0, toastTimeout: null
  };
  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
  const e = escapeHtml;
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const money = cents => `¥${(number(cents) / 100).toFixed(2)}`;
  // Catalog state stores cents for the API, but filter inputs always display yuan.
  const priceFilterValue = cents => cents === "" || cents == null ? "" :
    (Number(cents) / 100).toFixed(2).replace(/\.?0+$/, "");
  const dateText = value => value ? e(String(value).replace("T", " ").slice(0, 19)) : "—";
  const url = (path, params = {}) => {
    const p = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== "" && v !== null && v !== undefined) p.set(k, String(v));
    });
    const suffix = p.toString();
    return path + (suffix ? `?${suffix}` : "");
  };
  const statusPill = (status, kind = "product") => {
    const label = (kind === "reservation" ? bookingStatuses : productStatuses)[status] || status;
    const tone = ["REJECTED", "VIOLATION_DELISTED", "CANCELLED"].includes(status) ? "bad" :
      ["PENDING_REVIEW", "PENDING", "DELISTED"].includes(status) ? "warn" :
      ["SOLD", "COMPLETED"].includes(status) ? "neutral" : "";
    return `<span class="pill ${tone}">${e(label)}</span>`;
  };
  const artClass = category => /数码|电子|耳机|电脑|手机/i.test(category || "") ? "tech" :
    /生活|家居|服饰|日用/i.test(category || "") ? "life" : "book";
  const artwork = (category, large = false) => `<div class="product-image ${artClass(category)} ${large ? "large" : ""}" role="img" aria-label="商品示意图，不代表真实上传图片"><div class="object"></div></div>`;
  const btn = (label, action, id = "", style = "secondary") =>
    `<button type="button" class="button sm ${style}" data-act="${e(action)}" data-id="${e(id)}">${e(label)}</button>`;
  const empty = message => `<div class="empty"><div class="glyph">拾</div><p>${e(message)}</p></div>`;
  const card = product => `<article class="product-card">
    <a href="#/product/${number(product.id)}" aria-label="查看商品 ${e(product.title)}">${artwork(product.category)}</a>
    <div class="card-body">
      <a class="product-title" href="#/product/${number(product.id)}">${e(product.title || "未命名商品")}</a>
      <div class="product-meta">${e(product.category || "未分类")} · ${e(product.item_condition || "成色未标注")}</div>
      <div class="product-bottom"><span class="price"><small>¥</small>${(number(product.price_cents) / 100).toFixed(2)}</span>
      ${product.booked ? '<span class="pill warn">已被预约</span>' : '<span class="pill">可预约</span>'}</div>
    </div></article>`;
  const stat = (label, value) => `<div class="stat"><span class="caption">${e(label)}</span><strong>${e(value)}</strong></div>`;
  const options = (values, current, allLabel = "全部") =>
    `<option value="">${e(allLabel)}</option>` + values.map(([v, label]) =>
      `<option value="${e(v)}" ${String(v) === String(current) ? "selected" : ""}>${e(label)}</option>`).join("");
  const table = (headings, rows) => `<div class="table-wrap"><table class="data-table"><thead><tr>${headings.map(h => `<th>${e(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;

  // Every async view belongs to a specific route, render generation and identity.
  // Checking the route alone is insufficient when two accounts visit /mine.
  const captureView = () => ({
    renderId: state.renderId, authVersion: state.authVersion,
    hash: location.hash, token: state.token, userId: state.user?.id ?? null
  });
  const currentView = ctx => ctx.renderId === state.renderId &&
    ctx.authVersion === state.authVersion && ctx.hash === location.hash &&
    ctx.token === state.token && ctx.userId === (state.user?.id ?? null);
  function setAuthentication(token, user) {
    state.renderId += 1;
    state.authVersion += 1;
    state.token = token || "";
    state.user = user || null;
    state.visibleProduct = null;
    state.requestIds.clear();
    state.mineOffset = 0;
    state.bookingOffsets = {bookings: 0, received: 0};
    state.logOffset = 0;
    state.selected = {mine: "", bookings: "", received: "", review: "PENDING"};
    state.logFilter = {action: "", actor_query: "", start: "", end: ""};
    state.reviewOffsets = {PENDING: 0, APPROVED: 0, REJECTED: 0, VIOLATION: 0};
    // A modal from the previous identity must not remain visible after switching users.
    document.querySelectorAll("dialog.modal").forEach(dialog => dialog.close());
    if (token) sessionStorage.setItem("campus_market_token", token);
    else sessionStorage.removeItem("campus_market_token");
    updateHeader();
  }
  const staleError = () => {
    const err = new Error("过期请求已忽略");
    err.code = "STALE_RESPONSE";
    return err;
  };
  function showToast(message, error = false) {
    clearTimeout(state.toastTimeout);
    toastArea.textContent = message;
    toastArea.className = `toast visible${error ? " error" : ""}`;
    state.toastTimeout = setTimeout(() => { toastArea.className = "toast"; }, 4400);
  }
  async function api(path, {method = "GET", data, anonymous = false} = {}) {
    const requestToken = state.token;
    const requestAuthVersion = state.authVersion;
    const headers = {Accept: "application/json"};
    if (data !== undefined) headers["Content-Type"] = "application/json";
    if (!anonymous && requestToken) headers.Authorization = `Bearer ${requestToken}`;
    let response;
    try {
      response = await fetch(`/api${path}`, {
        method, headers, cache: "no-store", body: data === undefined ? undefined : JSON.stringify(data)
      });
    } catch (err) {
      if (!anonymous && (requestAuthVersion !== state.authVersion || requestToken !== state.token))
        throw staleError();
      throw new Error(`网络连接失败：${err.message}`);
    }
    // A response obtained under an old account must never affect the new account.
    if (!anonymous && (requestAuthVersion !== state.authVersion || requestToken !== state.token))
      throw staleError();
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error("服务器响应格式错误（无效 JSON），请重试");
    }
    if (payload === null || typeof payload !== "object" || Array.isArray(payload))
      throw new Error("服务器响应格式错误（JSON 结构不正确），请重试");
    if (!anonymous && (requestAuthVersion !== state.authVersion || requestToken !== state.token))
      throw staleError();
    if (!response.ok) {
      const message = payload.error?.message || `请求失败（HTTP ${response.status}）`;
      if (response.status === 401 && !anonymous && requestAuthVersion === state.authVersion && requestToken === state.token) {
        setAuthentication("", null);
        navigate("/auth");
      }
      const err = new Error(message);
      err.code = payload.error?.code;
      err.status = response.status;
      throw err;
    }
    return payload;
  }
  const pathname = () => decodeURIComponent((location.hash.slice(1) || "/browse").split("?")[0]);
  const navigate = to => {
    const target = `#${to}`;
    if (location.hash === target) void render(); else location.hash = target;
  };
  const asNormal = () => state.user?.account_type === "NORMAL";
  const asAdmin = () => state.user?.account_type === "ADMIN";
  function gate(admin = false) {
    if (!state.user) {
      state.afterLogin = pathname();
      showToast("请先登录，再使用这项功能");
      navigate("/auth");
      return false;
    }
    if (admin ? !asAdmin() : !asNormal()) {
      showToast(admin ? "只有管理员能够访问该页面" : "此页面仅供普通用户使用", true);
      navigate("/browse");
      return false;
    }
    return true;
  }
  function updateHeader() {
    const path = pathname();
    const navs = asAdmin() ? [["/browse", "商品首页"], ...adminLinks] :
      state.user ? roleLinks : [["/browse", "商品首页"], ["/auth", "注册与登录"]];
    topNav.innerHTML = navs.map(([href, label]) =>
      `<a href="#${href}" class="${path === href ? "current" : ""}">${e(label)}</a>`).join("");
    account.innerHTML = state.user ? `<span class="username" title="${e(state.user.username)}">${e(state.user.username)}</span>
      ${btn("退出", "logout", "", "ghost")}` : `<a class="button sm secondary" href="#/auth">登录 / 注册</a>`;
  }
  const titleBar = (title, subtitle, right = "") => `<div class="page-heading"><div><h1>${e(title)}</h1><div class="sub">${e(subtitle)}</div></div>${right}</div>`;
  const shell = (selected, title, subtitle, body, admin = false) => {
    const links = admin ? adminLinks : roleLinks.slice(1);
    return `<div class="layout-shell"><aside class="panel side-nav"><h3>${admin ? "管理中心" : "个人中心"}</h3>
      ${links.map(([link, text]) => `<a class="${link === selected ? "current" : ""}" href="#${link}"><i class="nav-dot"></i>${e(text)}</a>`).join("")}</aside>
      <div class="workspace">${titleBar(title, subtitle)}<section class="panel">${body}</section></div></div>`;
  };
  const tabs = (items, current, key) => `<div class="tabs">${items.map(([value, label]) =>
    `<button type="button" class="${value === current ? "current" : ""}" data-act="set-tab" data-tab="${e(key)}" data-value="${e(value)}">${e(label)}</button>`).join("")}</div>`;
  const queryForm = (formId, content, button = "查询") => `<form data-form="${e(formId)}" class="filters-inline">${content}
    <button class="button sm" type="submit">${e(button)}</button></form>`;
  function view(html) { app.innerHTML = html; }

  async function browse(ctx = captureView()) {
    const [products, facets] = await Promise.all([
      api(url("/products", {...state.catalog, limit: 12})), api("/products/facets")
    ]);
    if (!Array.isArray(products.items) || !Array.isArray(facets.categories))
      throw new Error("服务器响应格式错误（商品列表结构不正确），请重试");
    const categories = facets.categories.map(c => [c.category, `${c.category} (${c.total})`]);
    if (!currentView(ctx) || !["/browse", "/"].includes(pathname())) return;
    view(`<section class="hero"><div><div class="eyebrow">CAMPUS SECOND-HAND MARKET</div><h1>让闲置在校园里流转</h1>
      <p>发布方便 · 审核透明 · 线下交易更安心</p><div class="button-row"><a href="#/browse" class="button">浏览商品</a>
      <a href="#/publish" class="button secondary">发布闲置</a></div></div><div class="hero-visual">拾</div></section>
      <div class="section-head section"><div><h2>在售好物</h2><p class="sub">${facets.total_on_sale || 0} 件商品在售，${facets.available || 0} 件可预约</p></div></div>
      <div class="catalog-layout"><aside class="panel catalog-sidebar"><h3>商品筛选</h3>
      <form data-form="catalog"><div class="field"><label for="q-keyword">关键词</label><input id="q-keyword" name="keyword" maxlength="100" value="${e(state.catalog.keyword)}" placeholder="搜索教材、数码…"></div>
      <div class="field"><label for="q-category">商品分类</label><select id="q-category" name="category">${options(categories, state.catalog.category, "全部分类")}</select></div>
      <div class="field"><label for="q-min">最低价格（元）</label><input id="q-min" type="number" min="0" step="0.01" name="min_price" value="${e(priceFilterValue(state.catalog.min_price))}" placeholder="0"></div>
      <div class="field"><label for="q-max">最高价格（元）</label><input id="q-max" type="number" min="0" step="0.01" name="max_price" value="${e(priceFilterValue(state.catalog.max_price))}" placeholder="不限"></div>
      <div class="field"><label for="q-available">预约状态</label><select name="availability" id="q-available">${options([["AVAILABLE", "可预约"], ["RESERVED", "已被预约"]], state.catalog.availability, "全部在售")}</select></div>
      <div class="button-row"><button class="button sm" type="submit">查询</button>${btn("重置", "reset-search")}</div></form></aside>
      <section>${products.items?.length ? `<div class="products-grid">${products.items.map(card).join("")}</div>` : empty("暂无符合条件的商品")}
      <div class="button-row section"><button class="button secondary sm" data-act="catalog-prev" ${state.catalog.offset <= 0 ? "disabled" : ""}>上一页</button>
      <span class="small muted">第 ${Math.floor(state.catalog.offset / 12) + 1} 页</span>
      <button class="button secondary sm" data-act="catalog-next" ${products.items?.length < 12 ? "disabled" : ""}>下一页</button></div></section></div>`);
  }

  async function productDetail(productId, ctx = captureView()) {
    const product = await api(`/products/${productId}`);
    const booked = !!product.booked;
    const self = asNormal() && state.user.id === product.seller_id;
    const canReserve = asNormal() && !self && !booked;
    const control = !state.user ? `<a class="button" href="#/auth">登录后预约</a>` :
      canReserve ? `<button type="button" class="button" data-act="reserve" data-id="${productId}">申请预约</button>` :
      booked ? '<button class="button" disabled>商品已被预约</button>' :
      self ? '<a class="button secondary" href="#/mine">管理我的商品</a>' : '';
    const adminControl = asAdmin() ? `<div class="section notice">管理员可对在售商品进行违规处理。</div>${btn("违规下架", "violate-product", productId, "danger")}` : "";
    if (!currentView(ctx) || pathname() !== `/product/${productId}`) return;
    view(`<a href="#/browse" class="backlink">← 返回商品浏览</a>
      <div class="detail-layout"><section class="panel" style="padding:14px">${artwork(product.category, true)}<p class="hint">商品示意图（当前系统不提供图片上传）</p></section>
      <section class="panel detail-info"><div class="badge-row">${statusPill(product.status)}${booked ? '<span class="pill warn">已预约</span>' : '<span class="pill">可预约</span>'}</div>
      <h1 style="margin-top:16px">${e(product.title)}</h1><div class="price">${money(product.price_cents)}</div><div class="definition">
      <dt>商品分类</dt><dd>${e(product.category)}</dd><dt>商品成色</dt><dd>${e(product.item_condition)}</dd>
      <dt>交易地点</dt><dd>${e(product.trade_location)}</dd><dt>发布时间</dt><dd>${dateText(product.published_at)}</dd>
      <dt>商品编号</dt><dd>#${productId}</dd><dt>卖家信息</dt><dd>${e(product.seller_name || "未知卖家")}（平台账号）</dd>
      <dt>交易方式</dt><dd>线下面交</dd></div>
      <h3>商品描述</h3><p class="sub" style="font-size:13px;white-space:pre-wrap">${e(product.description)}</p>
      <div class="button-row section">${control}${adminControl}</div>
      <p class="hint">预约时会重新校验商品的状态、版本与价格，避免旧页面误操作。</p></section></div>`);
    // The version and expected price come from the displayed data, not from user-editable DOM.
    if (currentView(ctx)) state.visibleProduct = product;
  }

  async function authPage(ctx = captureView()) {
    if (!currentView(ctx)) return;
    const register = state.authMode === "register";
    view(`<div class="auth-shell"><section class="auth-art"><div><h2>欢迎来到校园拾集</h2><p class="sub">让闲置物品找到新的主人</p>
      <div class="big-logo" aria-hidden="true">拾</div></div></section><section class="auth-form"><div class="eyebrow">CAMPUS MARKET</div>
      <h2>${register ? "创建新账号" : "账号登录"}</h2><p class="sub">校内闲置交流，线下完成交易</p>
      <div class="auth-switch"><button data-act="auth-tab" data-value="login" class="${register ? "" : "current"}">登录</button>
      <button data-act="auth-tab" data-value="register" class="${register ? "current" : ""}">注册</button></div>
      <form data-form="auth"><div class="field"><label for="username">用户名</label><input required autocomplete="username" id="username" name="username" maxlength="32" placeholder="输入用户名"></div>
      <div class="field"><label for="password">密码</label><input required autocomplete="${register ? "new-password" : "current-password"}" id="password" name="password" type="password" minlength="8" maxlength="128" placeholder="至少 8 位字符"></div>
      ${register ? '<div class="field"><label for="password2">确认密码</label><input required id="password2" type="password" name="password2" minlength="8" maxlength="128" placeholder="再次输入密码"></div>' : ''}
      <button class="button" style="width:100%" type="submit">${register ? "注册并登录" : "登录"}</button></form>
      <p class="hint">普通账号同时拥有买家与卖家操作能力；管理员账号通过本地管理命令预创建。</p></section></div>`);
  }

  function productForm(product = null) {
    const status = product?.status || "DRAFT";
    const isNew = !product;
    const editableDraft = isNew || ["DRAFT", "REJECTED"].includes(status);
    const onSale = status === "ON_SALE";
    const blockedByBooking = onSale && !!product?.booked;
    const canEdit = (editableDraft || onSale) && !blockedByBooking;
    const readonly = !canEdit ? "disabled" : "";
    const fields = [
      ["title", "商品名称", "text"], ["category", "商品分类", "text"],
      ["price", "价格（元）", "text"], ["item_condition", "商品成色", "text"],
      ["trade_location", "交易地点", "text"]
    ];
    const inputs = fields.map(([name, label, type]) => {
      const value = name === "price" ? (product?.price_cents == null ? "" : (Number(product.price_cents) / 100).toFixed(2)) : (product?.[name] || "");
      return `<div class="field"><label for="f-${name}">${label}</label><input id="f-${name}" name="${name}" type="${type}" ${readonly} value="${e(value)}" placeholder="请输入${label}"></div>`;
    }).join("");
    const controls = isNew || editableDraft ? `<button name="mode" value="save" class="button secondary" type="submit">保存草稿</button>
      <button name="mode" value="submit" class="button" type="submit">保存并提交审核</button>` :
      onSale ? (blockedByBooking ? '<a class="button secondary" href="#/mine">返回我的商品（已有有效预约）</a>' :
        '<button name="mode" value="save" class="button" type="submit">保存修改并转为草稿</button>') :
      status === "DELISTED" || status === "VIOLATION_DELISTED" ? btn("申请重新审核", "submit-product", product.id, "") :
      '<a class="button secondary" href="#/mine">返回我的商品</a>';
    const note = blockedByBooking ? "商品存在待确认或已确认预约，按 REQ-005 暂不能修改商品信息。" :
      onSale ? "REQ-005 V1.2：六个字段均可修改；保存修改后商品转为草稿，暂停公开展示和预约；请在“我的商品”中再提交审核。" :
      status === "VIOLATION_DELISTED" ? "须先由管理员处理当前违规案件，才能重新提交审核。" :
      "草稿可以保存不完整信息；提交审核时将检查所有必填字段。";
    return shell("/publish", isNew ? "发布商品" : "编辑商品", isNew ? "完善商品信息，先保存草稿再提交审核" :
      `商品 #${product.id} · 当前状态：${productStatuses[status]}`, `<form data-form="product" data-id="${isNew ? "" : product.id}" data-status="${e(status)}">
      <h3>商品基本信息</h3><div class="fields section">${inputs}<div class="field full"><label for="f-description">商品描述</label>
      <textarea id="f-description" name="description" ${readonly} placeholder="描述商品的使用情况、配件、交易安排…">${e(product?.description || "")}</textarea></div></div>
      <div class="notice info">${e(note)}</div><div class="form-foot">${controls}</div></form>`);
  }
  async function publishPage(ctx = captureView()) {
    if (!gate()) return;
    const query = new URLSearchParams(location.hash.split("?")[1] || "");
    const id = query.get("edit");
    const product = id && /^\d+$/.test(id) ? await api(`/my/products/${id}`) : null;
    if (!currentView(ctx) || pathname() !== "/publish") return;
    view(productForm(product));
  }

  // M2 private catalogue: page through all records rather than truncating at 100.
  async function minePage(ctx = captureView()) {
    if (!gate()) return;
    const status = state.selected.mine;
    const pageSize = 20;
    const offset = state.mineOffset;
    const [products, dashboard] = await Promise.all([
      api(url("/my/products", {status, limit: pageSize + 1, offset})), api("/my/dashboard")
    ]);
    const statuses = Object.entries(productStatuses);
    const items = (products.items || []).slice(0, pageSize);
    const hasNext = (products.items || []).length > pageSize;
    const body = `${tabs([["", "全部商品"], ...statuses], status, "mine")}
      ${items.length ? table(["商品", "价格", "状态", "最近更新", "操作"], items.map(p => `<tr>
      <td><strong>${e(p.title || "未命名草稿")}</strong><div class="muted">#${p.id} · ${e(p.category || "未分类")}</div></td>
      <td>${p.price_cents == null ? "—" : money(p.price_cents)}</td><td>${statusPill(p.status)}</td><td>${dateText(p.updated_at)}</td>
      <td><div class="actions"><a class="button sm secondary" href="#/publish?edit=${p.id}">详情 / 编辑</a>
      ${["DRAFT", "REJECTED", "DELISTED", "VIOLATION_DELISTED"].includes(p.status) ? btn("提交审核", "submit-product", p.id, "light") : ""}
      ${p.status === "ON_SALE" ? btn("下架", "delist", p.id, "danger") : ""}
      ${btn("审核记录", "history", p.id, "ghost")}</div></td></tr>`)) : empty("当前筛选条件下没有商品")}
      <div class="section-head section"><p class="sub">第 ${offset / pageSize + 1} 页 · 当前显示 ${items.length} 件 · 共有 ${dashboard.products?.total ?? 0} 件商品</p>
      <div class="actions"><button class="button sm secondary" data-act="mine-prev" ${offset === 0 ? "disabled" : ""}>上一页</button>
      <button class="button sm secondary" data-act="mine-next" ${!hasNext ? "disabled" : ""}>下一页</button>
      <a class="button" href="#/publish">+ 发布新商品</a></div></div>`;
    if (!currentView(ctx) || pathname() !== "/mine") return;
    view(shell("/mine", "我的商品", "管理商品草稿、审核状态与下架操作", body));
  }

  async function bookingsPage(sellerSide = false, ctx = captureView()) {
    if (!gate()) return;
    const key = sellerSide ? "received" : "bookings";
    const status = state.selected[key];
    const endpoint = sellerSide ? "/reservations/received" : "/reservations/mine";
    const pageSize = 20;
    const offset = state.bookingOffsets[key] || 0;
    const data = await api(url(endpoint, {status, limit: pageSize + 1, offset}));
    const items = (data.items || []).slice(0, pageSize);
    const hasNext = (data.items || []).length > pageSize;
    const buttons = booking => {
      if (sellerSide && booking.status === "PENDING") return `${btn("确认", "confirm-booking", booking.id, "")}${btn("拒绝", "reject-booking", booking.id, "danger")}`;
      if (sellerSide && booking.status === "CONFIRMED") return btn("确认成交", "complete-booking", booking.id, "");
      if (!sellerSide && ["PENDING", "CONFIRMED"].includes(booking.status)) return btn("取消预约", "cancel-booking", booking.id, "danger");
      return '<span class="muted small">已结束</span>';
    };
    const rows = items.map(b => `<tr><td><strong>${e(b.product_title)}</strong><div class="muted">预约 #${b.id} · 商品 #${b.product_id}</div></td>
      <td>${e(b.counterparty_name || "—")}</td><td>${statusPill(b.status, "reservation")}</td>
      <td>${dateText(b.created_at)}</td><td>${e(b.cancel_reason || "—")}</td>
      <td><div class="actions">${buttons(b)}${btn("详情", "booking-detail", b.id, "ghost")}</div></td></tr>`);
    if (!currentView(ctx) || pathname() !== (sellerSide ? "/received" : "/bookings")) return;
    view(shell(sellerSide ? "/received" : "/bookings", sellerSide ? "收到的预约" : "我的预约",
      sellerSide ? "确认或拒绝买家的预约申请，线下成交后标记完成" : "查询预约状态、历史记录并取消仍有效的预约",
      `${tabs([["", "全部预约"], ...Object.entries(bookingStatuses)], status, key)}
      ${rows.length ? table(["商品及编号", sellerSide ? "买家" : "卖家", "预约状态", "申请时间", "取消原因", "操作"], rows) : empty("暂无对应预约记录")}
      <div class="section-head section"><span class="small muted">第 ${offset / pageSize + 1} 页 · 当前显示 ${items.length} 条</span>
      <div class="actions"><button class="button sm secondary" data-act="${key}-prev" ${offset === 0 ? "disabled" : ""}>上一页</button>
      <button class="button sm secondary" data-act="${key}-next" ${!hasNext ? "disabled" : ""}>下一页</button></div></div>`));
  }

  // UI-08: four moderation tabs, independent page cursors; records stay private.
  async function reviewPage(ctx = captureView()) {
    if (!gate(true)) return;
    const current = state.selected.review;
    const pageSize = 20;
    const offset = state.reviewOffsets[current] || 0;
    const paths = {
      PENDING: "/admin/products/pending", APPROVED: "/admin/products/reviewed?decision=APPROVED",
      REJECTED: "/admin/products/reviewed?decision=REJECTED", VIOLATION: "/admin/violations?status=OPEN"
    };
    const selectedPath = paths[current] || paths.PENDING;
    const join = selectedPath.includes("?") ? "&" : "?";
    const [queue, workload] = await Promise.all([
      api(`${selectedPath}${join}limit=${pageSize + 1}&offset=${offset}`),
      api("/admin/products/workload")
    ]);
    const items = (queue.items || []).slice(0, pageSize);
    const hasNext = (queue.items || []).length > pageSize;
    const work = `<div class="stats-grid">${stat("待审核商品", workload.PENDING_REVIEW || 0)}${stat("已驳回商品", workload.REJECTED || 0)}
      ${stat("未处理违规", workload.OPEN_VIOLATIONS || 0)}${stat("已处理违规", workload.RESOLVED_VIOLATIONS || 0)}</div>`;
    const rows = items.map(p => current === "VIOLATION" ? `<tr>
      <td><a class="table-product-link" href="#/admin-product/${p.product_id}">${e(p.product_title || "未命名商品")}</a></td>
      <td>${e(p.seller_name || "—")}</td><td>${e(p.reason)}</td><td>${dateText(p.created_at)}</td>
      <td><div class="actions"><a class="button sm secondary" href="#/admin-product/${p.product_id}">商品详情</a>${btn("记录处理结果", "resolve-violation", p.product_id, "")}</div></td></tr>` : `<tr>
      <td><a class="table-product-link" href="#/admin-product/${p.id}">${e(p.title || "未命名")}</a><div class="muted">#${p.id} · ${e(p.category || "未分类")}</div></td>
      <td>${e(p.seller_name || "—")}</td><td>${p.price_cents == null ? "—" : money(p.price_cents)}</td>
      <td>${dateText(p.reviewed_at || p.updated_at)}</td>
      <td><div class="actions">${current === "PENDING" ? btn("通过", "approve", p.id, "") + btn("驳回", "reject-review", p.id, "danger") : statusPill(p.status)}
      <a class="button sm secondary" href="#/admin-product/${p.id}">商品详情</a></div></td></tr>`);
    const headings = current === "VIOLATION" ? ["商品", "卖家", "违规原因", "下架时间", "操作"] :
      ["商品", "卖家", "价格", current === "PENDING" ? "提交时间" : "审核时间", "操作"];
    if (!currentView(ctx) || pathname() !== "/review") return;
    view(shell("/review", "商品审核", "管理员审核商品、处理违规案件并保留完整记录",
      work + tabs([["PENDING", "待审核"], ["APPROVED", "已通过"], ["REJECTED", "已驳回"], ["VIOLATION", "违规处理"]], current, "review") +
      (rows.length ? table(headings, rows) : empty("当前列表没有记录")) +
      `<div class="section-head section"><span class="small muted">第 ${offset / pageSize + 1} 页 · 当前显示 ${items.length} 条</span>
      <div class="actions"><button class="button sm secondary" data-act="review-prev" ${offset === 0 ? "disabled" : ""}>上一页</button>
      <button class="button sm secondary" data-act="review-next" ${!hasNext ? "disabled" : ""}>下一页</button></div></div>`, true));
  }

  async function sellerStatsPage(ctx = captureView()) {
    if (!gate()) return;
    const data = await api("/stats/seller");
    if (!currentView(ctx)) return;
    const counts = Object.entries(productStatuses).map(([key, label]) => [key, label, number(data.by_status?.[key] || 0)]);
    const max = Math.max(...counts.map(c => c[2]), 1);
    const graph = `<div class="chart-rows">${counts.map(([key, label, count]) => `<div class="chart-row"><span>${e(label)}</span>
      <div class="progress status-${e(key)}"><span style="width:${100 * count / max}%"></span></div><strong>${count}</strong></div>`).join("")}</div>`;
    if (pathname() !== "/seller-stats") return;
    view(shell("/seller-stats", "我的统计", "以商品状态和已完成交易为维度汇总个人数据",
      `<div class="stats-grid">${stat("商品总数", data.total ?? 0)}${stat("在售商品", data.by_status?.ON_SALE ?? 0)}
      ${stat("已完成交易", data.completed_transactions ?? 0)}${stat("已下架", number(data.by_status?.DELISTED) + number(data.by_status?.VIOLATION_DELISTED))}</div>
      <h3>各状态商品数量</h3>${graph}`));
  }

  function barsFromStats(data) {
    const grouped = new Map();
    for (const p of data.published || []) grouped.set(p.day, {...(grouped.get(p.day) || {published: 0, completed: 0}),
      published: (grouped.get(p.day)?.published || 0) + number(p.n)});
    for (const p of data.completed || []) grouped.set(p.day, {...(grouped.get(p.day) || {published: 0, completed: 0}),
      completed: (grouped.get(p.day)?.completed || 0) + number(p.n)});
    const days = [...grouped].sort((a, b) => a[0].localeCompare(b[0]));
    const max = Math.max(1, ...days.flatMap(([, x]) => [x.published, x.completed]));
    return days.length ? `<div class="legend"><span>发布数量</span><span>交易完成</span></div><div class="chart-bars">
      ${days.map(([day, counts]) => `<div class="bar-col"><div class="bar-pair">
      <div class="bar" title="发布 ${counts.published}" style="height:${Math.max(counts.published / max * 100, counts.published ? 2 : 0)}%"></div>
      <div class="bar secondary" title="成交 ${counts.completed}" style="height:${Math.max(counts.completed / max * 100, counts.completed ? 2 : 0)}%"></div></div><span>${e(day.slice(5))}</span></div>`).join("")}</div>` : empty("所选时段暂无发布或成交记录");
  }
  async function adminStatsPage(ctx = captureView()) {
    if (!gate(true)) return;
    const filters = state.adminStatsFilter || {start: "", end: "", category: ""};
    const all = await api(url("/admin/stats", {start: filters.start, end: filters.end}));
    const categoryNames = [...new Set([...(all.published || []), ...(all.completed || [])].map(x => x.category).filter(Boolean).concat(filters.category ? [filters.category] : []))].sort();
    const data = {published: (all.published || []).filter(x => !filters.category || x.category === filters.category),
      completed: (all.completed || []).filter(x => !filters.category || x.category === filters.category)};
    const published = data.published.reduce((a, b) => a + number(b.n), 0);
    const completed = data.completed.reduce((a, b) => a + number(b.n), 0);
    const dates = new Set([...data.published.map(x => x.day), ...data.completed.map(x => x.day)]);
    const byCategory = new Map();
    for (const item of data.published) byCategory.set(item.category, {...(byCategory.get(item.category) || {published: 0, completed: 0}), published: (byCategory.get(item.category)?.published || 0) + number(item.n)});
    for (const item of data.completed) byCategory.set(item.category, {...(byCategory.get(item.category) || {published: 0, completed: 0}), completed: (byCategory.get(item.category)?.completed || 0) + number(item.n)});
    if (!currentView(ctx) || pathname() !== "/admin-stats") return;
    view(shell("/admin-stats", "平台统计", "按日期和商品分类汇总发布及成交情况",
      `${queryForm("stats", `<div class="field"><label for="start">开始日期</label><input id="start" type="date" name="start" value="${e(filters.start)}"></div>
      <div class="field"><label for="end">结束日期</label><input id="end" type="date" name="end" value="${e(filters.end)}"></div>
      <div class="field"><label for="stats-category">商品分类</label><select id="stats-category" name="category">${options(categoryNames.map(x => [x, x]), filters.category, "全部分类")}</select></div>`, "查询统计")}
      <div class="button-row"><button class="button sm secondary" data-act="reset-admin-stats">重置筛选</button></div>
      <div class="stats-grid">${stat("商品发布数", published)}${stat("完成交易数", completed)}${stat("有数据的日期", dates.size)}${stat("涉及分类", byCategory.size)}</div>
      <h3>每日发布与成交情况</h3>${barsFromStats(data)}
      <div class="section-head section"><h3>按分类汇总</h3></div>
      ${byCategory.size ? table(["商品分类", "发布数量", "完成交易数量"], [...byCategory].map(([k, v]) =>
        `<tr><td><strong>${e(k)}</strong></td><td>${v.published}</td><td>${v.completed}</td></tr>`)) : empty("暂无分类统计数据")}`, true));
  }

  const actionLabels = {
    PRODUCT_DRAFT_CREATE: "创建商品草稿", PRODUCT_DRAFT_EDIT: "编辑商品草稿", PRODUCT_SUBMIT: "提交审核",
    PRODUCT_EDIT_RESUBMIT: "修改并重新审核（旧版本）", PRODUCT_EDIT_TO_DRAFT: "修改在售商品并转为草稿", PRODUCT_REVIEW_APPROVED: "审核通过", PRODUCT_REVIEW_REJECTED: "审核驳回",
    PRODUCT_DELIST: "主动下架", PRODUCT_VIOLATION_DELIST: "违规下架", PRODUCT_VIOLATION_RESOLVE: "处理违规",
    RESERVATION_CREATE: "申请预约", RESERVATION_CONFIRM: "确认预约", RESERVATION_REJECT: "拒绝预约",
    RESERVATION_CANCEL: "取消预约", RESERVATION_COMPLETE: "确认成交"
  };
  async function logsPage(ctx = captureView()) {
    if (!gate(true)) return;
    const filter = state.logFilter || {action: "", actor_query: "", start: "", end: ""};
    const pageSize = 20;
    const offset = state.logOffset;
    const data = await api(url("/admin/logs", {...filter, limit: pageSize + 1, offset}));
    const items = (data.items || []).slice(0, pageSize);
    const hasNext = (data.items || []).length > pageSize;
    const rows = items.map(l => `<tr><td>${dateText(l.created_at)}</td><td><strong>${e(l.actor_name || "用户")} (#${e(l.actor_id)})</strong></td>
      <td><span title="${e(l.action)}">${e(actionLabels[l.action] || l.action)}</span></td>
      <td>${e(l.target_type === "Product" ? "商品" : l.target_type === "Reservation" ? "预约" : l.target_type)} #${e(l.target_id)}</td>
      <td>${e(l.reason || "—")}</td><td>${l.result === "SUCCESS" ? "成功" : e(l.result || "—")}</td></tr>`);
    if (!currentView(ctx) || pathname() !== "/logs") return;
    view(shell("/logs", "操作日志", "管理员查看关键操作的行为审计记录",
      `${queryForm("logs", `<div class="field"><label for="log-action">操作类型</label><select id="log-action" name="action">${options(Object.entries(actionLabels), filter.action, "全部操作")}</select></div>
      <div class="field"><label for="log-actor">操作人（用户名或编号）</label><input id="log-actor" name="actor_query" maxlength="60" value="${e(filter.actor_query)}" placeholder="全部用户"></div>
      <div class="field"><label for="log-start">开始日期</label><input id="log-start" type="date" name="start" value="${e(filter.start)}"></div>
      <div class="field"><label for="log-end">结束日期</label><input id="log-end" type="date" name="end" value="${e(filter.end)}"></div>`, "查询日志")}
      <div class="section-head section"><h3>操作记录</h3><span class="small muted">支持分页查看全部匹配日志</span></div>
      ${rows.length ? table(["操作时间", "操作人", "操作类型", "对象", "原因", "结果"], rows) : empty("当前查询条件下没有操作记录")}
      <div class="section-head section"><span class="small muted">第 ${offset / pageSize + 1} 页 · 当前显示 ${items.length} 条</span>
      <div class="actions"><button class="button sm secondary" data-act="logs-prev" ${offset === 0 ? "disabled" : ""}>上一页</button>
      <button class="button sm secondary" data-act="logs-next" ${!hasNext ? "disabled" : ""}>下一页</button></div></div>`, true));
  }

  async function showHistory(productId, opener) {
    const ctx = captureView();
    const [history, violations] = await Promise.all([
      api(`/my/products/${productId}/reviews`), api(`/my/products/${productId}/violations`)
    ]);
    const contents = `<h3>审核历史</h3><div class="history section">${history.items.length ? history.items.map(h =>
      `<div class="history-entry"><strong>${h.decision === "APPROVED" ? "审核通过" : "审核驳回"}</strong>
      <p>${e(h.reason || "无备注")}</p><time>${dateText(h.reviewed_at)}</time></div>`).join("") : '<p class="muted">暂无审核历史</p>'}</div>
      <h3 class="section">违规处理历史</h3><div class="history section">${violations.items.length ? violations.items.map(c =>
      `<div class="history-entry"><strong>${c.resolved_at ? "违规已处理" : "待管理员处理"}</strong>
      <p>违规原因：${e(c.reason)}</p><p>处理说明：${e(c.handling_note || "—")}</p><time>${dateText(c.created_at)}</time></div>`).join("") : '<p class="muted">无违规记录</p>'}</div>`;
    if (currentView(ctx)) modal(`商品 #${productId} 历史记录`, contents, opener);
  }
  async function showBooking(id, opener) {
    const ctx = captureView();
    const b = await api(`/reservations/${id}`);
    if (!currentView(ctx)) return;
    modal(`预约 #${id} 详情`, `<dl class="definition">
      <dt>商品</dt><dd>${e(b.product_title)}</dd><dt>交易对象</dt><dd>${e(b.counterparty_name || "—")}</dd>
      <dt>状态</dt><dd>${statusPill(b.status, "reservation")}</dd><dt>提交价格</dt><dd>${b.expected_price_cents == null ? "—" : money(b.expected_price_cents)}</dd>
      <dt>提交版本</dt><dd>${e(b.expected_version)}</dd><dt>预约时间</dt><dd>${dateText(b.created_at)}</dd>
      <dt>确认时间</dt><dd>${dateText(b.confirmed_at)}</dd><dt>完成时间</dt><dd>${dateText(b.completed_at)}</dd>
      <dt>取消来源</dt><dd>${e(b.cancel_source || "—")}</dd><dt>取消原因</dt><dd>${e(b.cancel_reason || "—")}</dd></dl>`, opener);
  }
  async function adminProductPage(id, ctx = captureView()) {
    if (!gate(true)) return;
    const [p, reviews, violations] = await Promise.all([
      api(`/admin/products/${id}`),
      api(`/admin/products/${id}/reviews`),
      api(`/admin/products/${id}/violations`)
    ]);
    if (!currentView(ctx) || pathname() !== `/admin-product/${id}`) return;
    const openViolation = (violations.items || []).find(c => !c.resolved_at);
    const actions = p.status === "PENDING_REVIEW" ?
      `${btn("审核通过", "approve", id, "")}${btn("驳回审核", "reject-review", id, "danger")}` :
      p.status === "ON_SALE" ? btn("违规下架", "violate-product", id, "danger") :
      p.status === "VIOLATION_DELISTED" && openViolation ?
        btn("记录违规处理结果", "resolve-violation", id, "") : "";
    const reviewRows = (reviews.items || []).map(h => `<div class="history-entry">
      <strong>${h.decision === "APPROVED" ? "审核通过" : "审核驳回"}</strong>
      <p>${e(h.reason || "无审核说明")}</p><time>${dateText(h.reviewed_at)}</time></div>`).join("");
    const violationRows = (violations.items || []).map(c => `<div class="history-entry">
      <strong>${c.resolved_at ? "违规已处理" : "违规待处理"}</strong>
      <p>违规原因：${e(c.reason)}</p><p>处理说明：${e(c.handling_note || "—")}</p>
      <time>${dateText(c.created_at)}</time></div>`).join("");
    view(`<div class="admin-product-page"><a class="back-link" href="#/review">← 返回商品审核</a>
      ${titleBar("商品详情与审核", `商品 #${id} · 管理员可查看待审核及下架商品的完整资料`)}
      <div class="detail-layout"><section class="panel" style="padding:14px">
        ${artwork(p.category, true)}<p class="hint">商品示意图（目前不支持上传真实图片）</p></section>
      <section class="panel detail-info"><div class="badge-row">${statusPill(p.status)}
        ${p.booked ? '<span class="pill warn">已有有效预约</span>' : ""}</div>
        <h2 class="section">${e(p.title || "未填写商品名称")}</h2>
        <div class="price">${p.price_cents == null ? "—" : money(p.price_cents)}</div>
        <dl class="definition"><dt>卖家</dt><dd>${e(p.seller_name || "—")}（#${p.seller_id}）</dd>
        <dt>商品分类</dt><dd>${e(p.category || "未填写")}</dd>
        <dt>商品成色</dt><dd>${e(p.item_condition || "未填写")}</dd>
        <dt>线下交易地点</dt><dd>${e(p.trade_location || "未填写")}</dd>
        <dt>提交/修改时间</dt><dd>${dateText(p.updated_at)}</dd>
        <dt>发布时间</dt><dd>${dateText(p.published_at)}</dd>
        <dt>商品版本</dt><dd>${e(p.version)}</dd></dl>
        <h3>商品描述</h3><p class="sub" style="white-space:pre-wrap">${e(p.description || "暂无描述")}</p>
        ${actions ? `<div class="button-row section">${actions}</div>` : '<p class="muted section">当前状态无需审核操作。</p>'}
      </section></div>
      <div class="admin-product-history"><section class="panel"><h3>历史审核记录</h3>
        <div class="history section">${reviewRows || '<p class="muted">暂无审核记录</p>'}</div></section>
        <section class="panel"><h3>违规处理记录</h3>
        <div class="history section">${violationRows || '<p class="muted">暂无违规处理记录</p>'}</div></section></div>
    </div>`);
  }
  function modal(title, html, opener = null) {
    // The delegated action handler disables its button during the async request.
    // Keep the original opener explicitly: native dialog restoration cannot rely on it.
    const context = captureView();
    const returnFocus = opener instanceof HTMLElement ? opener : document.activeElement;
    const dialog = document.createElement("dialog");
    dialog.className = "modal";
    dialog.innerHTML = `<div class="modal-head"><h2>${e(title)}</h2><button class="button secondary sm" type="button" data-act="close-modal">关闭</button></div>
      <div class="modal-body">${html}</div>`;
    document.body.appendChild(dialog);
    dialog.addEventListener("close", () => {
      dialog.remove();
      // Handle Escape, close button and backdrop alike; avoid stale-user or
      // stale-route focus restoration, including a newer dialog being opened.
      queueMicrotask(() => {
        if (!currentView(context) || !(returnFocus instanceof HTMLElement) ||
            !returnFocus.isConnected || returnFocus.matches(":disabled") ||
            document.querySelector("dialog.modal[open]")) return;
        returnFocus.focus({preventScroll: true});
      });
    }, {once: true});
    dialog.addEventListener("click", event => { if (event.target === dialog) dialog.close(); });
    dialog.showModal();
  }

  function cents(text, required = false) {
    if (!String(text).trim()) {
      if (required) throw new Error("请填写价格");
      return null;
    }
    const value = String(text).trim();
    if (!/^(?:0|[1-9]\d*)(?:\.\d{1,2})?$/.test(value))
      throw new Error("价格应为非负金额，最多两位小数");
    const [yuan, fen = ""] = value.split(".");
    const result = BigInt(yuan) * 100n + BigInt(fen.padEnd(2, "0"));
    if (result > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error("价格过大");
    return Number(result);
  }
  async function handleForm(form, submitter) {
    const id = form.dataset.form;
    const values = Object.fromEntries(new FormData(form));
    if (id === "catalog") {
      state.catalog = {
        keyword: values.keyword.trim(), category: values.category,
        min_price: values.min_price.trim() ? cents(values.min_price) : "",
        max_price: values.max_price.trim() ? cents(values.max_price) : "",
        availability: values.availability, offset: 0
      };
      if (state.catalog.min_price !== "" && state.catalog.max_price !== "" &&
          state.catalog.min_price > state.catalog.max_price) throw new Error("最低价格不能高于最高价格");
      await render();
      return;
    }
    if (id === "auth") {
      const authStart = captureView();
      const username = values.username.trim();
      if (state.authMode === "register") {
        if (values.password !== values.password2) throw new Error("两次输入的密码不一致");
        await api("/auth/register", {method: "POST", data: {username, password: values.password}, anonymous: true});
      }
      const result = await api("/auth/login", {method: "POST", data: {username, password: values.password}, anonymous: true});
      if (!currentView(authStart)) return;
      setAuthentication(result.token, result.user);
      showToast(state.authMode === "register" ? "账号创建并登录成功" : "登录成功");
      navigate(state.afterLogin || "/browse");
      state.afterLogin = "/browse";
      return;
    }
    if (id === "product") {
      if (!gate()) return;
      const oldId = form.dataset.id;
      const oldStatus = form.dataset.status;
      const mode = submitter?.value || "save";
      const fields = {};
      const read = name => (values[name] || "").trim();
      for (const name of ["title", "category", "item_condition", "trade_location", "description"]) {
        if (read(name)) fields[name] = read(name);
      }
      if (read("price")) fields.price_cents = cents(read("price"));
      if (oldStatus === "ON_SALE") {
        // Send six fields so the server validates the whole edited product.
        // A no-op edit must not withdraw a published product.
        for (const name of ["title", "category", "item_condition", "trade_location", "description"]) {
          if (!read(name)) throw new Error("在售商品修改时，六个商品字段均须保持完整");
        }
        const payload = Object.fromEntries(
          ["title", "category", "item_condition", "trade_location", "description"]
            .map(name => [name, read(name)])
        );
        payload.price_cents = cents(read("price"), true);
        await api(`/products/${oldId}`, {method: "PATCH", data: payload});
        showToast("商品已撤回为草稿，请在“我的商品”中单独提交审核");
        navigate("/mine");
        return;
      }
      if (oldId) {
        if (!Object.keys(fields).length) throw new Error("请至少填写一项商品信息");
        await api(`/products/${oldId}`, {method: "PATCH", data: fields});
        if (mode === "submit") await api(`/products/${oldId}/submit`, {method: "POST"});
      } else {
        if (!Object.keys(fields).length) throw new Error("请至少填写一项商品信息");
        const product = await api("/products", {method: "POST", data: fields});
        if (mode === "submit") await api(`/products/${product.id}/submit`, {method: "POST"});
      }
      showToast(mode === "submit" ? "商品已提交审核" : "商品草稿已保存");
      navigate("/mine");
      return;
    }
    if (id === "stats") {
      state.adminStatsFilter = {start: values.start || "", end: values.end || "", category: values.category || ""};
      if (values.start && values.end && values.start > values.end) throw new Error("结束日期不能早于开始日期");
      await render();
      return;
    }
    if (id === "logs") {
      const filter = {action: values.action.trim(), actor_query: values.actor_query.trim(), start: values.start, end: values.end};
      if (values.start && values.end && values.start > values.end) throw new Error("结束日期不能早于开始日期");
      state.logFilter = filter;
      state.logOffset = 0;
      await render();
    }
  }
  async function handleAction(el) {
    const act = el.dataset.act;
    const id = Number(el.dataset.id);
    if (act === "close-modal") {
      el.closest("dialog")?.close();
      return;
    }
    if (act === "auth-tab") {
      state.authMode = el.dataset.value;
      await authPage();
      return;
    }
    if (act === "set-tab") {
      state.selected[el.dataset.tab] = el.dataset.value;
      if (el.dataset.tab === "mine") state.mineOffset = 0;
      if (el.dataset.tab === "bookings" || el.dataset.tab === "received")
        state.bookingOffsets[el.dataset.tab] = 0;
      if (el.dataset.tab === "review") state.reviewOffsets[el.dataset.value] = 0;
      await render();
      return;
    }
    if (["bookings-prev", "bookings-next", "received-prev", "received-next"].includes(act)) {
      const key = act.startsWith("bookings") ? "bookings" : "received";
      state.bookingOffsets[key] = Math.max(0,
        (state.bookingOffsets[key] || 0) + (act.endsWith("next") ? 20 : -20));
      await render(); return;
    }
    if (act === "logs-prev" || act === "logs-next") {
      state.logOffset = Math.max(0, state.logOffset + (act === "logs-next" ? 20 : -20));
      await render(); return;
    }
    if (act === "mine-prev" || act === "mine-next") {
      state.mineOffset = Math.max(0, state.mineOffset + (act === "mine-next" ? 20 : -20));
      await render(); return;
    }
    if (act === "review-prev" || act === "review-next") {
      const tab = state.selected.review;
      state.reviewOffsets[tab] = Math.max(0, (state.reviewOffsets[tab] || 0) + (act === "review-next" ? 20 : -20));
      await render(); return;
    }
    if (act === "reset-admin-stats") {
      state.adminStatsFilter = {start: "", end: "", category: ""};
      await render(); return;
    }
    if (act === "reset-search") {
      state.catalog = {keyword: "", category: "", min_price: "", max_price: "", availability: "", offset: 0};
      await render();
      return;
    }
    if (act === "catalog-prev" || act === "catalog-next") {
      state.catalog.offset = Math.max(0, state.catalog.offset + (act === "catalog-next" ? 12 : -12));
      await render();
      return;
    }
    if (act === "logout") {
      const logoutContext = captureView();
      await api("/auth/logout", {method: "POST"});
      if (!currentView(logoutContext)) return;
      setAuthentication("", null);
      showToast("已安全退出");
      navigate("/browse");
      return;
    }
    if (act === "reserve") {
      if (!gate()) return;
      const product = state.visibleProduct;
      if (!product || product.id !== id) throw new Error("商品信息已变化，请刷新页面");
      if (!confirm(`确认预约「${product.title}」？商品价格 ${money(product.price_cents)}，线下完成交易。`)) return;
      const identity = `${id}:${product.version}:${product.price_cents}`;
      if (!state.requestIds.has(identity)) state.requestIds.set(identity, crypto.randomUUID());
      const request_id = state.requestIds.get(identity);
      await api("/reservations", {method: "POST", data: {
        product_id: id, expected_version: product.version,
        expected_price_cents: product.price_cents, request_id
      }});
      state.requestIds.delete(identity);
      showToast("预约成功，请在我的预约中查看状态");
      navigate("/bookings");
      return;
    }
    if (act === "history") { await showHistory(id, el); return; }
    if (act === "booking-detail") { await showBooking(id, el); return; }
    if (act === "submit-product") {
      if (!confirm("确认提交审核？商品在审核通过前不会在公开列表出现。")) return;
      await api(`/products/${id}/submit`, {method: "POST"});
      showToast("已提交审核");
      await render(); return;
    }
    if (act === "delist") {
      if (!confirm("确认下架该商品？相关有效预约将一并取消，并记录原因。")) return;
      await api(`/products/${id}/delist`, {method: "POST"});
      showToast("商品已下架，关联预约已同步处理");
      await render(); return;
    }
    if (act === "confirm-booking" || act === "cancel-booking" || act === "complete-booking") {
      const map = {"confirm-booking": ["confirm", "确认接受买家的预约？"],
        "cancel-booking": ["cancel", "确认取消这笔预约？"],
        "complete-booking": ["complete", "确认双方已在线下完成交易？商品将变为已售出状态。"]};
      const [operation, question] = map[act];
      if (!confirm(question)) return;
      await api(`/reservations/${id}/${operation}`, {method: "POST"});
      showToast("预约状态已更新");
      await render(); return;
    }
    if (act === "reject-booking") {
      const reason = prompt("请填写拒绝理由（将保留在预约记录中）：");
      if (reason === null) return;
      if (!reason.trim()) throw new Error("拒绝理由不能为空");
      await api(`/reservations/${id}/reject`, {method: "POST", data: {reason: reason.trim()}});
      showToast("已拒绝预约并保存理由");
      await render(); return;
    }
    if (act === "approve" || act === "reject-review") {
      const accepted = act === "approve";
      const reason = accepted ? null : prompt("请填写驳回原因（卖家可以查看）：");
      if (reason === null && !accepted) return;
      if (!accepted && !reason.trim()) throw new Error("驳回理由不能为空");
      if (accepted && !confirm(`确认通过商品 #${id} 的审核并公开发布？`)) return;
      await api(`/admin/products/${id}/review`, {method: "POST", data: {
        decision: accepted ? "APPROVED" : "REJECTED", reason: accepted ? null : reason.trim()
      }});
      showToast(accepted ? "审核通过，商品已公开" : "已驳回并记录原因");
      await render(); return;
    }
    if (act === "violate-product") {
      const reason = prompt("请填写违规下架原因：");
      if (reason === null) return;
      if (!reason.trim()) throw new Error("违规下架必须填写原因");
      if (!confirm("违规下架后所有有效预约将被取消。确认继续？")) return;
      await api(`/admin/products/${id}/violation-delist`, {method: "POST", data: {reason: reason.trim()}});
      showToast("已违规下架并生成处理记录");
      navigate("/review"); return;
    }
    if (act === "resolve-violation") {
      const note = prompt("请输入管理员处理说明。处理后商品仍须由卖家重新提交审核：");
      if (note === null) return;
      if (!note.trim()) throw new Error("处理说明不能为空");
      await api(`/admin/products/${id}/violation-resolve`, {method: "POST", data: {handling_note: note.trim()}});
      showToast("违规案件已处理，卖家可重新提交审核");
      await render(); return;
    }
  }

  async function render() {
    const page = pathname();
    const ticket = ++state.renderId;
    // A route re-render must not leave a previous page's private modal visible.
    // Increment renderId first, so close handlers cannot focus a stale opener.
    document.querySelectorAll("dialog.modal[open]").forEach(dialog => dialog.close());
    const ctx = captureView();
    updateHeader();
    app.innerHTML = '<div class="loading">正在加载…</div>';
    try {
      if (page === "/browse" || page === "/") await browse(ctx);
      else if (/^\/product\/\d+$/.test(page)) await productDetail(Number(page.split("/")[2]), ctx);
      else if (page === "/auth") await authPage(ctx);
      else if (page === "/publish") await publishPage(ctx);
      else if (page === "/mine") await minePage(ctx);
      else if (page === "/bookings") await bookingsPage(false, ctx);
      else if (page === "/received") await bookingsPage(true, ctx);
      else if (page === "/review") await reviewPage(ctx);
      else if (/^\/admin-product\/\d+$/.test(page)) await adminProductPage(Number(page.split("/")[2]), ctx);
      else if (page === "/seller-stats") await sellerStatsPage(ctx);
      else if (page === "/admin-stats") await adminStatsPage(ctx);
      else if (page === "/logs") await logsPage(ctx);
      else view(`<div class="panel">${empty("未找到对应页面")}<a class="button" href="#/browse">返回首页</a></div>`);
      if (!currentView(ctx)) return;
      updateHeader();
    } catch (err) {
      if (!currentView(ctx)) return;
      view(`<section class="panel">${empty(err.message || "加载失败")}
        <div class="button-row" style="justify-content:center"><button class="button secondary" data-act="reload">重试</button>
        <a class="button" href="#/browse">返回商品首页</a></div></section>`);
    }
  }
  document.addEventListener("submit", event => {
    const form = event.target.closest("form[data-form]");
    if (!form) return;
    event.preventDefault();
    const submitter = event.submitter;
    const button = submitter || form.querySelector('button[type="submit"]');
    if (button?.disabled) return;
    if (button) button.disabled = true;
    handleForm(form, submitter).catch(err => { if (err.code !== "STALE_RESPONSE") showToast(err.message, true); })
      .finally(() => { if (button) button.disabled = false; });
  });
  document.addEventListener("click", event => {
    const trigger = event.target.closest("[data-act]");
    if (!trigger) return;
    event.preventDefault();
    if (trigger.disabled) return;
    if (trigger.dataset.act === "reload") { void render(); return; }
    trigger.disabled = true;
    Promise.resolve().then(() => handleAction(trigger))
      .catch(err => { if (err.code !== "STALE_RESPONSE") showToast(err.message, true); })
      .finally(() => { trigger.disabled = false; });
  });
  window.addEventListener("hashchange", () => { void render(); window.scrollTo({top: 0, behavior: "instant"}); });
  async function boot() {
    if (state.token) {
      const tokenAtBoot = state.token;
      try {
        const me = await api("/auth/me");
        if (state.token === tokenAtBoot) state.user = me;
      } catch {
        if (state.token === tokenAtBoot) setAuthentication("", null);
      }
    }
    await render();
  }
  void boot();
})();
