"use strict";

// ------------------------------------------------------------------ state
const KEY_STORE = "gw_master_key";
const ROLE_STORE = "gw_is_master";
const LANG_STORE = "gw_lang";
let MASTER = sessionStorage.getItem(KEY_STORE) || "";
// Whether the session is the master key (vs a logged-in user). Only the master
// may manage users; this drives the UI (the backend enforces it regardless).
let IS_MASTER = sessionStorage.getItem(ROLE_STORE) !== "false";
let ROUTE = "overview";
const ref = { providers: [], aliases: [], credentials: [], providerTypes: [] };

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return n;
};

// ------------------------------------------------------------------ i18n
// English strings are the keys; other languages provide overrides. Missing
// entries fall back to the key itself (i.e. English). Use {name} placeholders.
const I18N = {
  zh: {
    // chrome / nav
    "Overview": "概览", "Providers": "供应商", "Credentials": "凭证",
    "Aliases": "别名", "Deployments": "部署", "Virtual keys": "虚拟密钥",
    "Traffic": "流量", "Playground": "调试台", "Sign out": "退出登录",
    // login
    "Signal router console — authenticate with the master key.": "信号路由控制台 —— 使用主密钥登录。",
    "Master key": "主密钥", "Connect": "连接",
    // modal
    "Cancel": "取消", "Save": "保存", "Done": "完成", "Copy": "复制",
    "Discard unsaved changes?": "放弃未保存的修改吗？",
    "new": "新建", "edit": "编辑", "secret": "密钥",
    "New {x}": "新建{x}", "Edit {x}": "编辑{x}", "Virtual key created": "虚拟密钥已创建",
    "+ New {x}": "+ 新建{x}",
    "Copied to clipboard": "已复制到剪贴板",
    "Copy it now — this is the only time the full key is shown.": "请立即复制 —— 完整密钥仅显示这一次。",
    "Invalid JSON in one of the fields.": "某个字段的 JSON 格式无效。",
    "Unauthorized — check the master key.": "未授权 —— 请检查主密钥。",
    // singulars
    "Provider": "供应商", "Credential": "凭证", "Alias": "别名",
    "Deployment": "部署", "Virtual key": "虚拟密钥",
    // generic
    "{x} created": "{x}已创建", "{x} updated": "{x}已更新", "{x} deleted": "{x}已删除",
    "No {x} yet. Create the first one.": "暂无{x}，先创建一个吧。",
    "Edit": "编辑", "Delete": "删除", "Apply": "应用",
    'Delete {kind} "{label}"? This cannot be undone.': '确定删除{kind}"{label}"？此操作不可撤销。',
    "enabled": "已启用", "disabled": "已停用",
    "inherit": "继承", "on": "开", "off": "关",
    // columns / common labels
    "ID": "ID", "Name": "名称", "Type": "类型", "Base URL": "基础 URL",
    "State": "状态", "Weight": "权重", "RPM": "RPM", "TPM": "TPM",
    "Balancing": "负载均衡", "Fallback": "降级", "Cache": "缓存",
    "Prefix": "前缀", "Allowed": "允许范围", "Spend / budget": "已花费 / 预算",
    "Upstream model": "上游模型", "In $/M": "输入 $/百万", "Out $/M": "输出 $/百万",
    "Price book": "价目表",
    "Per-1M-token prices for prefix routing (provider/model calls without a deployment), e.g. {\"gpt-4o\": {\"input\": 2.5, \"output\": 10}}.":
      "用于前缀路由（无部署的 provider/model 调用）的每百万 token 单价，例如 {\"gpt-4o\": {\"input\": 2.5, \"output\": 10}}。",
    // descriptions
    "Upstream vendors. The name doubles as a routing prefix, e.g. calling openai/gpt-4o targets the openai provider.":
      "上游供应商。名称同时作为路由前缀，例如调用 openai/gpt-4o 会指向 openai 供应商。",
    "API keys per provider. Keys are encrypted at rest and never returned by the API.":
      "每个供应商的 API 密钥。密钥加密存储，接口绝不返回明文。",
    "Client-facing model names. Each alias is a load-balancing group over one or more deployments, with an optional fallback chain.":
      "面向客户端的模型名。每个别名是一个或多个部署组成的负载均衡组，并可配置降级链。",
    "A concrete target: an alias routed through one credential to one upstream model, with pinned params and pricing.":
      "一个具体目标：别名经某个凭证路由到某个上游模型，可写死参数与定价。",
    "Keys issued to downstream callers, each with its own model allowlist, rate limits, and budget.":
      "发放给下游调用方的密钥，各自拥有模型白名单、限流和预算。",
    // field labels
    "Adapter type": "适配器类型", "Default base URL": "默认基础 URL", "Enabled": "启用",
    "API key": "API 密钥", "Base URL override": "基础 URL 覆盖", "Organization": "组织",
    "Requests / min": "每分钟请求数", "Tokens / min": "每分钟 Token 数", "Extra headers": "额外请求头",
    "Load balancing": "负载均衡策略", "Fallback aliases": "降级别名", "Response cache": "响应缓存",
    "Credential": "凭证", "Pinned params": "写死参数", "Default params": "默认参数",
    "Drop params": "丢弃参数", "Input price / 1M tokens": "输入价 / 百万 Token",
    "Output price / 1M tokens": "输出价 / 百万 Token", "Allowed models": "允许的模型",
    "Budget": "预算", "Budget period": "预算周期", "Expires": "过期时间",
    // hints
    "Routing prefix, e.g. openai / kimi / deepseek.": "路由前缀，例如 openai / kimi / deepseek。",
    "Optional; credentials may override per key.": "可选；凭证可按密钥单独覆盖。",
    "Stored encrypted. Leave blank when editing to keep the current key.": "加密存储。编辑时留空表示保持原密钥不变。",
    "Blank = unlimited.": "留空 = 不限。",
    "JSON object sent with every upstream call.": "随每次上游调用发送的 JSON 对象。",
    "What clients pass as model, e.g. gpt-4o-balanced.": "客户端传入的 model 值，例如 gpt-4o-balanced。",
    "Comma-separated alias names to try when this pool is exhausted.": "逗号分隔的别名，当本组耗尽时依次尝试。",
    "Inherit uses the global default.": "“继承”表示使用全局默认值。",
    "The provider's model id, e.g. gpt-4o, moonshot-v1-8k.": "供应商的模型 id，例如 gpt-4o、moonshot-v1-8k。",
    'Forced over client values, e.g. {"temperature": 0}.': '强制覆盖客户端传值，例如 {"temperature": 0}。',
    "Filled only when the client omits them.": "仅在客户端未传时填充。",
    "Param names to strip before the upstream call.": "上游调用前要剔除的参数名。",
    "Alias names or provider prefixes. * allows everything.": "别名或供应商前缀。* 表示全部允许。",
    "Max spend per period. Blank = unlimited.": "每个周期的最大花费。留空 = 不限。",
    // overview
    "console": "控制台", "Signal overview": "信号总览",
    "Live configuration and the last 24 hours of routed traffic.": "实时配置与最近 24 小时的路由流量。",
    "Requests · 24h": "请求数 · 24h", "Tokens · 24h": "Token · 24h", "Spend · 24h": "花费 · 24h",
    "throughput by alias · 24h": "按别名的吞吐 · 24h",
    "No traffic recorded in the last 24 hours.": "最近 24 小时无流量记录。",
    "Requests": "请求数", "Tokens": "Token", "Cost": "成本", "Avg latency": "平均延迟",
    "circuit breaker": "熔断器", "All deployments nominal — no failures recorded.": "所有部署正常 —— 无失败记录。",
    "deployment #{id}": "部署 #{id}", "available": "可用", "cooling down": "冷却中",
    "{n} fails": "{n} 次失败", "Failures": "失败次数", "Cooldown": "冷却剩余",
    // login + users
    "Signal router console — sign in with the master key, or a username + password.":
      "信号路由控制台 —— 用主密钥,或用户名 + 密码登录。",
    "Username": "用户名", "Leave blank to use the master key": "留空则使用主密钥登录",
    "Master key / password": "主密钥 / 密码", "Invalid username or password": "用户名或密码错误",
    "Users": "用户", "User": "用户", "access": "访问控制",
    "Console operator accounts. Each logs in with a username + auto-generated password and has full admin access. The password is shown once on creation or reset.":
      "控制台操作员账号。每个账号用 用户名 + 自动生成的密码 登录,拥有完整管理权限。密码仅在创建或重置时显示一次。",
    "No users yet. Create the first one.": "还没有用户,先创建一个。",
    "User management requires the master key.": "用户管理需要主密钥。",
    "Created": "创建时间", "Last login": "最近登录", "Reset password": "重置密码",
    "Disable": "禁用", "Enable": "启用",
    "User created": "用户已创建", "Password reset": "密码已重置",
    "Copy it now — this is the only time the password is shown.": "立即复制 —— 密码只会显示这一次。",
    'Reset password for "{name}"? The old password stops working.': "重置「{name}」的密码?旧密码将立即失效。",
    'Delete user "{name}"? This cannot be undone.': "删除用户「{name}」?此操作不可撤销。",
    // analytics
    "Analytics": "分析", "insights": "洞察", "Usage analytics": "用量分析",
    "Spend, tokens and traffic broken down by alias and by key.": "按别名和密钥拆分的花费、Token 与流量。",
    "Window": "时间范围", "Last 24 hours": "最近 24 小时", "Last 7 days": "最近 7 天", "Last 30 days": "最近 30 天",
    "Spend": "花费", "spend by alias": "按别名的花费", "requests by alias": "按别名的请求数",
    "spend by key": "按密钥的花费", "(no key)": "（无密钥）", "No traffic in this window.": "该时间范围内无流量。",
    "recent admin activity": "最近的管理操作", "No admin changes recorded.": "无管理变更记录。",
    "Method": "方法", "Path": "路径", "Actor": "操作者",
    // logs
    "traffic": "流量", "Request log": "请求日志",
    "Every proxied request, newest first — tokens, cost, latency, retries, and cache hits. Click a row to inspect the exact request and response exchanged with the provider.":
      "所有代理请求（最新在前）—— Token、成本、延迟、重试与缓存命中。点击某一行可查看与供应商之间实际收发的请求与响应。",
    "filter by alias": "按别名过滤", "Limit": "条数", "No requests match.": "没有匹配的请求。",
    "Time": "时间", "Status": "状态", "Alias / model": "别名 / 模型", "Provider": "供应商",
    "Latency": "延迟", "Retries": "重试", "hit": "命中",
    "Inspect upstream request / response": "查看上游请求 / 响应",
    "Request detail": "请求详情", "Sent to provider": "发送给供应商的内容",
    "Provider response": "供应商响应", "No upstream body captured.": "未捕获上游内容。",
    "Error": "错误",
    // playground
    "playground": "调试台", "Test bench": "测试台",
    "Send a request through the real routing path and see which deployment served it. Authenticated by the master key — virtual-key budgets and limits are bypassed.":
      "通过真实路由路径发送请求，查看由哪个部署处理。使用主密钥鉴权 —— 不受虚拟密钥的预算与限流约束。",
    "alias name or provider/model": "别名 或 provider/model",
    "Optional system prompt": "可选的系统提示词", "Your message": "你的消息",
    "Reply with a single word: ping.": "只回复一个词：ping。",
    "default": "默认", "Send request": "发送请求", "Routing…": "路由中…",
    "Send a request to see the response and routing metadata.": "发送请求以查看响应与路由元数据。",
    "Waiting for upstream…": "等待上游响应…", "(empty response)": "（空响应）",
    "Model": "模型", "System": "系统", "Message": "消息", "Temperature": "温度", "Max tokens": "最大 Token",
    "A configured alias, or provider/model like kimi/moonshot-v1-8k.": "已配置的别名，或 provider/model 形式，例如 kimi/moonshot-v1-8k。",
    "response": "响应", "synthetic": "动态合成",
    "alias": "别名", "deployment": "部署", "provider": "供应商", "model": "模型",
    "tokens": "tokens", "cost": "成本", "latency": "延迟", "retries": "重试",
  },
};

const _storedLang = localStorage.getItem(LANG_STORE);
let LANG = _storedLang || ((navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en");

function t(key, vars) {
  let s = (I18N[LANG] && I18N[LANG][key]) || key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll("{" + k + "}", v);
  return s;
}

function applyStaticI18n() {
  document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = t(n.getAttribute("data-i18n")); });
  document.querySelectorAll("[data-i18n-ph]").forEach((n) => { n.setAttribute("placeholder", t(n.getAttribute("data-i18n-ph"))); });
  document.querySelectorAll(".lang-select").forEach((sel) => { sel.value = LANG; });
  document.documentElement.lang = LANG === "zh" ? "zh-CN" : "en";
}

function setLang(lang) {
  if (lang === LANG) return;
  LANG = lang;
  localStorage.setItem(LANG_STORE, lang);
  applyStaticI18n();
  if (!$("#app").hidden) renderView(ROUTE);
}

// ------------------------------------------------------------------ api
async function api(method, path, body) {
  const opts = { method, headers: { Authorization: "Bearer " + MASTER } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    logout();
    throw new Error(t("Unauthorized — check the master key."));
  }
  if (res.status === 204) return null;
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error?.message)) || text || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

// ------------------------------------------------------------------ toast
let toastTimer;
function toast(msg, isErr = false) {
  const node = $("#toast");
  node.textContent = msg;
  node.className = "toast" + (isErr ? " err" : "");
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), 3200);
}

// ------------------------------------------------------------------ formatters
const fmt = {
  pill(enabled) {
    return el("span", { class: "pill " + (enabled ? "on" : "off") }, t(enabled ? "enabled" : "disabled"));
  },
  tri(v) {
    if (v === null || v === undefined) return el("span", { class: "cell-muted" }, t("inherit"));
    return el("span", { class: "pill " + (v ? "on" : "off") }, t(v ? "on" : "off"));
  },
  list(arr) {
    if (!arr || !arr.length) return el("span", { class: "cell-muted" }, "—");
    return el("span", {}, arr.join(", "));
  },
  num(v) {
    if (v === null || v === undefined) return el("span", { class: "cell-muted" }, "∞");
    return String(v);
  },
  money(v) {
    return el("span", { class: "cell-amber" }, "$" + Number(v || 0).toFixed(4));
  },
  providerName(id) {
    const p = ref.providers.find((x) => x.id === id);
    return p ? p.name : "#" + id;
  },
  aliasName(id) {
    const a = ref.aliases.find((x) => x.id === id);
    return a ? a.name : "#" + id;
  },
  credName(id) {
    const c = ref.credentials.find((x) => x.id === id);
    return c ? c.name : "#" + id;
  },
};

// ------------------------------------------------------------------ schemas
// Labels/titles/descs/hints are English keys, translated at render time via t().
const STRATEGIES = ["round_robin", "weighted", "least_busy", "random"];

const SCHEMAS = {
  providers: {
    title: "Providers",
    singular: "Provider",
    endpoint: "/admin/providers",
    desc: "Upstream vendors. The name doubles as a routing prefix, e.g. calling openai/gpt-4o targets the openai provider.",
    columns: [
      { key: "id", label: "ID", cls: "cell-muted" },
      { key: "name", label: "Name", cls: "cell-strong" },
      { key: "provider_type", label: "Type" },
      { key: "default_base_url", label: "Base URL", cls: "cell-muted" },
      { key: "enabled", label: "State", fmt: (v) => fmt.pill(v) },
    ],
    fields: [
      { name: "name", label: "Name", type: "text", required: true, hint: "Routing prefix, e.g. openai / kimi / deepseek." },
      { name: "provider_type", label: "Adapter type", type: "select", options: () => ref.providerTypes.map((x) => ({ value: x, label: x })) },
      { name: "default_base_url", label: "Default base URL", type: "text", hint: "Optional; credentials may override per key." },
      { name: "model_prices", label: "Price book", type: "json", hint: "Per-1M-token prices for prefix routing (provider/model calls without a deployment), e.g. {\"gpt-4o\": {\"input\": 2.5, \"output\": 10}}." },
      { name: "enabled", label: "Enabled", type: "checkbox", default: true },
    ],
  },

  credentials: {
    title: "Credentials",
    singular: "Credential",
    endpoint: "/admin/credentials",
    desc: "API keys per provider. Keys are encrypted at rest and never returned by the API.",
    columns: [
      { key: "id", label: "ID", cls: "cell-muted" },
      { key: "name", label: "Name", cls: "cell-strong" },
      { key: "provider_id", label: "Provider", fmt: (v) => fmt.providerName(v) },
      { key: "base_url", label: "Base URL", cls: "cell-muted" },
      { key: "weight", label: "Weight" },
      { key: "rpm_limit", label: "RPM", fmt: (v) => fmt.num(v) },
      { key: "tpm_limit", label: "TPM", fmt: (v) => fmt.num(v) },
      { key: "enabled", label: "State", fmt: (v) => fmt.pill(v) },
    ],
    fields: [
      { name: "provider_id", label: "Provider", type: "select", required: true, createOnly: true, options: () => ref.providers.map((p) => ({ value: p.id, label: p.name })) },
      { name: "name", label: "Name", type: "text", required: true },
      { name: "api_key", label: "API key", type: "password", requiredOnCreate: true, omitIfEmpty: true, hint: "Stored encrypted. Leave blank when editing to keep the current key." },
      { name: "base_url", label: "Base URL override", type: "text" },
      { name: "org", label: "Organization", type: "text" },
      { name: "weight", label: "Weight", type: "number", default: 1 },
      { name: "rpm_limit", label: "Requests / min", type: "number", hint: "Blank = unlimited." },
      { name: "tpm_limit", label: "Tokens / min", type: "number", hint: "Blank = unlimited." },
      { name: "extra_headers", label: "Extra headers", type: "json", hint: "JSON object sent with every upstream call." },
      { name: "enabled", label: "Enabled", type: "checkbox", default: true },
    ],
  },

  aliases: {
    title: "Aliases",
    singular: "Alias",
    endpoint: "/admin/aliases",
    desc: "Client-facing model names. Each alias is a load-balancing group over one or more deployments, with an optional fallback chain.",
    columns: [
      { key: "id", label: "ID", cls: "cell-muted" },
      { key: "name", label: "Name", cls: "cell-strong" },
      { key: "lb_strategy", label: "Balancing" },
      { key: "fallback_aliases", label: "Fallback", fmt: (v) => fmt.list(v) },
      { key: "cache_enabled", label: "Cache", fmt: (v) => fmt.tri(v) },
      { key: "enabled", label: "State", fmt: (v) => fmt.pill(v) },
    ],
    fields: [
      { name: "name", label: "Name", type: "text", required: true, hint: "What clients pass as model, e.g. gpt-4o-balanced." },
      { name: "lb_strategy", label: "Load balancing", type: "select", options: () => STRATEGIES.map((s) => ({ value: s, label: s })) },
      { name: "fallback_aliases", label: "Fallback aliases", type: "csv", hint: "Comma-separated alias names to try when this pool is exhausted." },
      { name: "cache_enabled", label: "Response cache", type: "tristate", hint: "Inherit uses the global default." },
      { name: "enabled", label: "Enabled", type: "checkbox", default: true },
    ],
  },

  deployments: {
    title: "Deployments",
    singular: "Deployment",
    endpoint: "/admin/deployments",
    desc: "A concrete target: an alias routed through one credential to one upstream model, with pinned params and pricing.",
    columns: [
      { key: "id", label: "ID", cls: "cell-muted" },
      { key: "alias_id", label: "Alias", fmt: (v) => fmt.aliasName(v), cls: "cell-strong" },
      { key: "credential_id", label: "Credential", fmt: (v) => fmt.credName(v) },
      { key: "upstream_model", label: "Upstream model", cls: "cell-amber" },
      { key: "weight", label: "Weight" },
      { key: "input_price", label: "In $/M", fmt: (v) => String(v) },
      { key: "output_price", label: "Out $/M", fmt: (v) => String(v) },
      { key: "enabled", label: "State", fmt: (v) => fmt.pill(v) },
    ],
    fields: [
      { name: "alias_id", label: "Alias", type: "select", required: true, createOnly: true, options: () => ref.aliases.map((a) => ({ value: a.id, label: a.name })) },
      { name: "credential_id", label: "Credential", type: "select", required: true, options: () => ref.credentials.map((c) => ({ value: c.id, label: c.name + " · " + fmt.providerName(c.provider_id) })) },
      { name: "upstream_model", label: "Upstream model", type: "text", required: true, hint: "The provider's model id, e.g. gpt-4o, moonshot-v1-8k." },
      { name: "weight", label: "Weight", type: "number", default: 1 },
      { name: "rpm_limit", label: "Requests / min", type: "number" },
      { name: "tpm_limit", label: "Tokens / min", type: "number" },
      { name: "pinned_params", label: "Pinned params", type: "json", hint: 'Forced over client values, e.g. {"temperature": 0}.' },
      { name: "default_params", label: "Default params", type: "json", hint: "Filled only when the client omits them." },
      { name: "drop_params", label: "Drop params", type: "csv", hint: "Param names to strip before the upstream call." },
      { name: "input_price", label: "Input price / 1M tokens", type: "number", default: 0 },
      { name: "output_price", label: "Output price / 1M tokens", type: "number", default: 0 },
      { name: "enabled", label: "Enabled", type: "checkbox", default: true },
    ],
  },

  keys: {
    title: "Virtual keys",
    singular: "Virtual key",
    endpoint: "/admin/keys",
    desc: "Keys issued to downstream callers, each with its own model allowlist, rate limits, and budget.",
    columns: [
      { key: "id", label: "ID", cls: "cell-muted" },
      { key: "name", label: "Name", cls: "cell-strong" },
      { key: "key_prefix", label: "Prefix", cls: "cell-muted" },
      { key: "allowed_aliases", label: "Allowed", fmt: (v) => fmt.list(v) },
      { key: "rpm_limit", label: "RPM", fmt: (v) => fmt.num(v) },
      { key: "_budget", label: "Spend / budget", fmt: (_v, r) => budgetCell(r) },
      { key: "enabled", label: "State", fmt: (v) => fmt.pill(v) },
    ],
    fields: [
      { name: "name", label: "Name", type: "text", required: true },
      { name: "allowed_aliases", label: "Allowed models", type: "csv", default: ["*"], hint: "Alias names or provider prefixes. * allows everything." },
      { name: "rpm_limit", label: "Requests / min", type: "number" },
      { name: "tpm_limit", label: "Tokens / min", type: "number" },
      { name: "max_budget", label: "Budget", type: "number", hint: "Max spend per period. Blank = unlimited." },
      { name: "budget_period", label: "Budget period", type: "select", options: () => ["total", "daily", "monthly"].map((p) => ({ value: p, label: p })) },
      { name: "expires_at", label: "Expires", type: "datetime-local" },
    ],
  },

  users: {
    title: "Users",
    singular: "User",
    endpoint: "/admin/users",
    desc: "Console operator accounts. Each logs in with a username + auto-generated password and has full admin access. The password is shown once on creation or reset.",
    fields: [
      { name: "username", label: "Username", type: "text", required: true, hint: "Used to log into the console. A password is generated automatically." },
    ],
  },
};

function budgetCell(r) {
  const spend = "$" + Number(r.spend || 0).toFixed(2);
  if (r.max_budget == null) return el("span", {}, el("span", { class: "cell-amber" }, spend), el("span", { class: "cell-muted" }, " / ∞ " + r.budget_period));
  const over = r.spend >= r.max_budget;
  return el("span", {}, el("span", { class: over ? "" : "cell-amber", style: over ? "color:var(--err)" : "" }, spend), el("span", { class: "cell-muted" }, " / $" + r.max_budget + " " + r.budget_period));
}

// ------------------------------------------------------------------ reference data
async function loadRef() {
  const [providers, aliases, credentials, ptypes] = await Promise.all([
    api("GET", "/admin/providers"),
    api("GET", "/admin/aliases"),
    api("GET", "/admin/credentials"),
    api("GET", "/admin/providers/provider-types"),
  ]);
  ref.providers = providers || [];
  ref.aliases = aliases || [];
  ref.credentials = credentials || [];
  ref.providerTypes = (ptypes && ptypes.provider_types) || [];
}

// ------------------------------------------------------------------ views
async function renderView(route) {
  ROUTE = route;
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.route === route));
  const crumb = $("#crumb-section");
  const content = $("#content");
  content.innerHTML = "";
  try {
    if (route === "overview") return await renderOverview(content, crumb);
    if (route === "analytics") return await renderAnalytics(content, crumb);
    if (route === "logs") return await renderLogs(content, crumb);
    if (route === "users") {
      crumb.textContent = t("Users");
      if (!IS_MASTER) { content.append(el("div", { class: "empty" }, t("User management requires the master key."))); return; }
      return await renderUsers(content, crumb);
    }
    if (route === "playground") return await renderPlayground(content, crumb);
    return await renderCrud(route, content, crumb);
  } catch (e) {
    content.append(el("div", { class: "empty" }, e.message));
  }
}

async function renderCrud(route, content, crumb) {
  const schema = SCHEMAS[route];
  crumb.textContent = t(schema.title);
  await loadRef();
  const rows = await api("GET", schema.endpoint);

  content.append(
    el("div", { class: "view-head" },
      el("div", {},
        el("div", { class: "eyebrow" }, t(schema.title)),
        el("h1", { class: "view-title" }, t(schema.title)),
        el("p", { class: "view-desc" }, t(schema.desc)),
      ),
      el("button", { class: "btn btn-signal", onclick: () => openModal(route, null) }, t("+ New {x}", { x: t(schema.singular) })),
    )
  );

  if (!rows || !rows.length) {
    content.append(el("div", { class: "panel" }, el("div", { class: "empty" }, t("No {x} yet. Create the first one.", { x: t(schema.title) }))));
    return;
  }

  const thead = el("thead", {}, el("tr", {},
    ...schema.columns.map((c) => el("th", {}, t(c.label))),
    el("th", {}, ""),
  ));
  const tbody = el("tbody", {});
  for (const r of rows) {
    const tds = schema.columns.map((c) => {
      const raw = r[c.key];
      const cell = c.fmt ? c.fmt(raw, r) : (raw == null || raw === "" ? el("span", { class: "cell-muted" }, "—") : String(raw));
      return el("td", { class: c.cls || "" }, cell);
    });
    tds.push(el("td", { class: "col-actions" },
      el("div", { class: "row-actions" },
        el("button", { class: "btn btn-sm btn-ghost", onclick: () => openModal(route, r) }, t("Edit")),
        el("button", { class: "btn btn-sm btn-danger", onclick: () => removeRow(route, r) }, t("Delete")),
      )
    ));
    tbody.append(el("tr", {}, ...tds));
  }
  content.append(el("div", { class: "panel" }, el("div", { class: "table-wrap" }, el("table", {}, thead, tbody))));
}

async function removeRow(route, row) {
  const schema = SCHEMAS[route];
  const label = row.name || row.upstream_model || "#" + row.id;
  if (!confirm(t('Delete {kind} "{label}"? This cannot be undone.', { kind: t(schema.singular), label }))) return;
  try {
    await api("DELETE", schema.endpoint + "/" + row.id);
    toast(t("{x} deleted", { x: t(schema.singular) }));
    renderView(route);
  } catch (e) {
    toast(e.message, true);
  }
}

// ------------------------------------------------------------------ overview
async function renderOverview(content, crumb) {
  crumb.textContent = t("Overview");
  await loadRef();
  const [deployments, keys, usage, health] = await Promise.all([
    api("GET", "/admin/deployments"),
    api("GET", "/admin/keys"),
    api("GET", "/admin/usage?since_hours=24").catch(() => []),
    api("GET", "/admin/deployment-health").catch(() => ({ deployments: {} })),
  ]);

  const totReq = usage.reduce((a, u) => a + u.requests, 0);
  const totTok = usage.reduce((a, u) => a + u.total_tokens, 0);
  const totCost = usage.reduce((a, u) => a + u.cost, 0);

  content.append(
    el("div", { class: "view-head" }, el("div", {},
      el("div", { class: "eyebrow" }, t("console")),
      el("h1", { class: "view-title" }, t("Signal overview")),
      el("p", { class: "view-desc" }, t("Live configuration and the last 24 hours of routed traffic.")),
    ))
  );

  const stat = (label, value, unit) => el("div", { class: "stat" },
    el("div", { class: "stat-label" }, label),
    el("div", { class: "stat-value" }, String(value), unit ? el("span", { class: "unit" }, unit) : null),
  );

  content.append(el("div", { class: "stat-grid" },
    stat(t("Providers"), ref.providers.length),
    stat(t("Aliases"), ref.aliases.length),
    stat(t("Deployments"), (deployments || []).length),
    stat(t("Virtual keys"), (keys || []).length),
    stat(t("Requests · 24h"), totReq.toLocaleString()),
    stat(t("Tokens · 24h"), totTok.toLocaleString()),
    stat(t("Spend · 24h"), "$" + totCost.toFixed(2)),
  ));

  // usage by alias
  content.append(el("div", { class: "eyebrow section-label" }, t("throughput by alias · 24h")));
  if (!usage.length) {
    content.append(el("div", { class: "panel" }, el("div", { class: "empty" }, t("No traffic recorded in the last 24 hours."))));
  } else {
    const tb = el("tbody", {});
    for (const u of usage) {
      tb.append(el("tr", {},
        el("td", { class: "cell-strong" }, u.alias || "—"),
        el("td", { class: "cell-num" }, u.requests.toLocaleString()),
        el("td", { class: "cell-num" }, u.total_tokens.toLocaleString()),
        el("td", { class: "cell-num" }, fmt.money(u.cost)),
        el("td", { class: "cell-num cell-muted" }, u.avg_latency_ms + " ms"),
      ));
    }
    content.append(el("div", { class: "panel" }, el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, t("Alias")), el("th", { class: "cell-num" }, t("Requests")), el("th", { class: "cell-num" }, t("Tokens")), el("th", { class: "cell-num" }, t("Cost")), el("th", { class: "cell-num" }, t("Avg latency")))),
      tb))));
  }

  // deployment health
  const hmap = (health && health.deployments) || {};
  const hids = Object.keys(hmap);
  content.append(el("div", { class: "eyebrow section-label", style: "margin-top:24px" }, t("circuit breaker")));
  if (!hids.length) {
    content.append(el("div", { class: "panel" }, el("div", { class: "empty" }, t("All deployments nominal — no failures recorded."))));
  } else {
    const tb = el("tbody", {});
    for (const id of hids) {
      const h = hmap[id];
      const pillCls = h.available ? "on" : "err";
      const stateTxt = t(h.available ? "available" : "cooling down");
      tb.append(el("tr", {},
        el("td", { class: "cell-strong" }, t("deployment #{id}", { id })),
        el("td", {}, el("span", { class: "pill " + pillCls }, stateTxt)),
        el("td", { class: "cell-num" }, t("{n} fails", { n: h.failures })),
        el("td", { class: "cell-num cell-muted" }, h.cooldown_remaining > 0 ? h.cooldown_remaining + "s" : "—"),
      ));
    }
    content.append(el("div", { class: "panel" }, el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, t("Deployment")), el("th", {}, t("State")), el("th", { class: "cell-num" }, t("Failures")), el("th", { class: "cell-num" }, t("Cooldown")))),
      tb))));
  }
}

// ------------------------------------------------------------------ analytics
function _bars(rows, valueText) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  const host = el("div", { class: "bars" });
  for (const r of rows) {
    const pct = r.value > 0 ? Math.max(3, Math.round((r.value / max) * 100)) : 0;
    host.append(el("div", { class: "bar-row" },
      el("div", { class: "bar-label", title: r.label }, r.label),
      el("div", { class: "bar-track" }, el("div", { class: "bar-fill", style: "width:" + pct + "%" })),
      el("div", { class: "bar-val" }, valueText(r.value, r)),
    ));
  }
  return host;
}

async function renderAnalytics(content, crumb) {
  crumb.textContent = t("Analytics");
  await loadRef();
  let hours = 24;
  let allKeys = [];

  content.append(
    el("div", { class: "view-head" }, el("div", {},
      el("div", { class: "eyebrow" }, t("insights")),
      el("h1", { class: "view-title" }, t("Usage analytics")),
      el("p", { class: "view-desc" }, t("Spend, tokens and traffic broken down by alias and by key.")),
    ))
  );

  const windowSel = el("select", { class: "lang-select", onchange: (e) => { hours = +e.target.value; reload(); } },
    el("option", { value: "24" }, t("Last 24 hours")),
    el("option", { value: "168" }, t("Last 7 days")),
    el("option", { value: "720" }, t("Last 30 days")),
  );
  content.append(el("div", { class: "filters" },
    el("label", { class: "field" }, el("span", { class: "field-label" }, t("Window")), windowSel)));

  const host = el("div", {});
  content.append(host);

  const keyName = (id) => {
    if (id == null) return t("(no key)");
    const k = allKeys.find((x) => x.id === id);
    return k ? k.name : "#" + id;
  };

  const barPanel = (label, rows, valueText, extraStyle) => {
    host.append(el("div", { class: "eyebrow section-label", style: extraStyle || "" }, label));
    if (!rows.length) {
      host.append(el("div", { class: "panel" }, el("div", { class: "empty" }, t("No traffic in this window."))));
    } else {
      host.append(el("div", { class: "panel panel-pad" }, _bars(rows, valueText)));
    }
  };

  async function reload() {
    host.innerHTML = "";
    let usage, byKey, audit;
    try {
      [usage, byKey, allKeys, audit] = await Promise.all([
        api("GET", `/admin/usage?since_hours=${hours}`),
        api("GET", `/admin/usage/by-key?since_hours=${hours}`),
        api("GET", "/admin/keys").catch(() => []),
        api("GET", "/admin/audit?limit=20").catch(() => []),
      ]);
    } catch (e) { host.append(el("div", { class: "empty" }, e.message)); return; }

    const totReq = usage.reduce((a, u) => a + u.requests, 0);
    const totTok = usage.reduce((a, u) => a + u.total_tokens, 0);
    const totCost = usage.reduce((a, u) => a + u.cost, 0);
    const stat = (label, value) => el("div", { class: "stat" },
      el("div", { class: "stat-label" }, label),
      el("div", { class: "stat-value" }, String(value)));
    host.append(el("div", { class: "stat-grid" },
      stat(t("Requests"), totReq.toLocaleString()),
      stat(t("Tokens"), totTok.toLocaleString()),
      stat(t("Spend"), "$" + totCost.toFixed(2)),
    ));

    const costRows = usage.map((u) => ({ label: u.alias || "—", value: u.cost })).sort((a, b) => b.value - a.value);
    barPanel(t("spend by alias"), costRows, (v) => "$" + v.toFixed(4));

    const reqRows = usage.map((u) => ({ label: u.alias || "—", value: u.requests })).sort((a, b) => b.value - a.value);
    barPanel(t("requests by alias"), reqRows, (v) => v.toLocaleString(), "margin-top:24px");

    const keyRows = byKey
      .map((u) => ({ label: keyName(u.virtual_key_id), value: u.cost, req: u.requests }))
      .sort((a, b) => b.value - a.value);
    barPanel(t("spend by key"), keyRows, (v, r) => "$" + v.toFixed(4) + " · " + r.req, "margin-top:24px");

    // recent admin activity (audit trail)
    host.append(el("div", { class: "eyebrow section-label", style: "margin-top:24px" }, t("recent admin activity")));
    if (!audit.length) {
      host.append(el("div", { class: "panel" }, el("div", { class: "empty" }, t("No admin changes recorded."))));
    } else {
      const tb = el("tbody", {});
      for (const a of audit) {
        tb.append(el("tr", {},
          el("td", { class: "cell-muted" }, new Date(a.ts).toLocaleString()),
          el("td", {}, el("span", { class: "pill " + (a.status < 400 ? "on" : "err") }, a.method)),
          el("td", { class: "cell-strong" }, a.path),
          el("td", { class: "cell-num" }, String(a.status)),
          el("td", { class: "cell-muted" }, a.actor || "—"),
        ));
      }
      host.append(el("div", { class: "panel" }, el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {},
          el("th", {}, t("Time")), el("th", {}, t("Method")), el("th", {}, t("Path")),
          el("th", { class: "cell-num" }, t("Status")), el("th", {}, t("Actor")))),
        tb))));
    }
  }

  await reload();
}

// ------------------------------------------------------------------ users
async function renderUsers(content, crumb) {
  crumb.textContent = t("Users");
  content.append(
    el("div", { class: "view-head" },
      el("div", {},
        el("div", { class: "eyebrow" }, t("access")),
        el("h1", { class: "view-title" }, t("Users")),
        el("p", { class: "view-desc" }, t(SCHEMAS.users.desc)),
      ),
      el("button", { class: "btn btn-signal", onclick: () => openModal("users", null) }, t("+ New {x}", { x: t("User") })),
    )
  );

  let users;
  try { users = await api("GET", "/admin/users"); }
  catch (e) { content.append(el("div", { class: "empty" }, e.message)); return; }

  if (!users.length) {
    content.append(el("div", { class: "panel" }, el("div", { class: "empty" }, t("No users yet. Create the first one."))));
    return;
  }

  const tb = el("tbody", {});
  for (const u of users) {
    tb.append(el("tr", {},
      el("td", { class: "cell-muted" }, "#" + u.id),
      el("td", { class: "cell-strong" }, u.username),
      el("td", {}, fmt.pill(u.enabled)),
      el("td", { class: "cell-muted" }, new Date(u.created_at).toLocaleDateString()),
      el("td", { class: "cell-muted" }, u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "—"),
      el("td", { class: "col-actions" }, el("div", { class: "row-actions" },
        el("button", { class: "btn btn-sm btn-ghost", onclick: () => resetUserPassword(u) }, t("Reset password")),
        el("button", { class: "btn btn-sm btn-ghost", onclick: () => toggleUser(u) }, t(u.enabled ? "Disable" : "Enable")),
        el("button", { class: "btn btn-sm btn-danger", onclick: () => deleteUser(u) }, t("Delete")),
      )),
    ));
  }
  content.append(el("div", { class: "panel" }, el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, t("ID")), el("th", {}, t("Username")), el("th", {}, t("State")),
      el("th", {}, t("Created")), el("th", {}, t("Last login")), el("th", {}, ""))),
    tb))));
}

async function resetUserPassword(u) {
  if (!confirm(t('Reset password for "{name}"? The old password stops working.', { name: u.username }))) return;
  try {
    const r = await api("POST", "/admin/users/" + u.id + "/reset-password");
    revealSecret(t("Password reset"), u.username, r.password,
      t("Copy it now — this is the only time the password is shown."));
  } catch (e) { toast(e.message, true); }
}

async function toggleUser(u) {
  try {
    await api("PATCH", "/admin/users/" + u.id, { enabled: !u.enabled });
    toast(t("{x} updated", { x: t("User") }));
    renderView("users");
  } catch (e) { toast(e.message, true); }
}

async function deleteUser(u) {
  if (!confirm(t('Delete user "{name}"? This cannot be undone.', { name: u.username }))) return;
  try {
    await api("DELETE", "/admin/users/" + u.id);
    toast(t("{x} deleted", { x: t("User") }));
    renderView("users");
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ logs
async function renderLogs(content, crumb) {
  crumb.textContent = t("Traffic");
  content.append(
    el("div", { class: "view-head" }, el("div", {},
      el("div", { class: "eyebrow" }, t("traffic")),
      el("h1", { class: "view-title" }, t("Request log")),
      el("p", { class: "view-desc" }, t("Every proxied request, newest first — tokens, cost, latency, retries, and cache hits. Click a row to inspect the exact request and response exchanged with the provider.")),
    ))
  );

  const aliasInput = el("input", { type: "text", placeholder: t("filter by alias"), id: "f-alias" });
  const limitInput = el("input", { type: "number", value: "100", id: "f-limit" });
  const tableHost = el("div", { class: "panel" });

  async function reload() {
    tableHost.innerHTML = "";
    const params = new URLSearchParams();
    const a = aliasInput.value.trim();
    if (a) params.set("alias", a);
    params.set("limit", limitInput.value || "100");
    let rows;
    try { rows = await api("GET", "/admin/logs?" + params.toString()); }
    catch (e) { tableHost.append(el("div", { class: "empty" }, e.message)); return; }
    if (!rows || !rows.length) { tableHost.append(el("div", { class: "empty" }, t("No requests match."))); return; }
    const tb = el("tbody", {});
    for (const r of rows) {
      const ok = r.status === 200;
      tb.append(el("tr", { class: "log-row", title: t("Inspect upstream request / response"), onclick: () => showLogDetail(r.id) },
        el("td", { class: "cell-muted" }, new Date(r.ts).toLocaleString()),
        el("td", {}, el("span", { class: "pill " + (ok ? "on" : "err") }, String(r.status))),
        el("td", { class: "cell-strong" }, r.alias || r.requested_model),
        el("td", { class: "cell-muted" },
          r.provider_name
            ? el("span", {}, el("span", { class: "cell-strong" }, r.provider_name),
                r.provider_type ? el("span", { class: "cell-muted" }, " · " + r.provider_type) : null)
            : (r.provider_type || "—")),
        el("td", { class: "cell-num" }, (r.total_tokens || 0).toLocaleString()),
        el("td", { class: "cell-num" }, fmt.money(r.cost)),
        el("td", { class: "cell-num cell-muted" }, (r.latency_ms || 0) + " ms"),
        el("td", { class: "cell-num cell-muted" }, String(r.retries)),
        el("td", {}, r.cache_hit ? el("span", { class: "pill warn" }, t("hit")) : el("span", { class: "cell-muted" }, "—")),
      ));
    }
    tableHost.append(el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, t("Time")), el("th", {}, t("Status")), el("th", {}, t("Alias / model")), el("th", {}, t("Provider")),
        el("th", { class: "cell-num" }, t("Tokens")), el("th", { class: "cell-num" }, t("Cost")), el("th", { class: "cell-num" }, t("Latency")), el("th", { class: "cell-num" }, t("Retries")), el("th", {}, t("Cache")))),
      tb)));
  }

  content.append(el("div", { class: "filters" },
    el("label", { class: "field" }, el("span", { class: "field-label" }, t("Alias")), aliasInput),
    el("label", { class: "field" }, el("span", { class: "field-label" }, t("Limit")), limitInput),
    el("button", { class: "btn", onclick: reload }, t("Apply")),
  ));
  content.append(tableHost);
  await reload();
}

function _jsonBlock(value) {
  if (value === null || value === undefined) {
    return el("p", { class: "reveal-note" }, t("No upstream body captured."));
  }
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return el("pre", { class: "io-block" }, text);
}

// Inspect a single request's exact upstream request/response — useful to verify
// that params like the reasoning level were translated and sent correctly.
async function showLogDetail(id) {
  let r;
  try { r = await api("GET", "/admin/logs/" + id); }
  catch (e) { toast(e.message, true); return; }

  resetModalChrome();
  modalState = null;
  modalDirty = false;
  $("#modal-eyebrow").textContent = t("traffic");
  $("#modal-title").textContent = t("Request detail");
  const form = $("#modal-form");
  form.innerHTML = "";
  $("#modal-err").hidden = true;

  const routed = (r.provider_name || r.provider_type || "—")
    + (r.provider_name && r.provider_type ? " (" + r.provider_type + ")" : "")
    + (r.credential_id != null ? " · cred #" + r.credential_id : "");
  const meta = el("div", { class: "io-meta" },
    el("span", {}, routed + " · " + (r.alias || r.requested_model)),
    el("span", { class: "pill " + (r.status === 200 ? "on" : "err") }, String(r.status)),
    el("span", { class: "cell-muted" }, (r.total_tokens || 0).toLocaleString() + " tok · $" + Number(r.cost || 0).toFixed(4) + " · " + (r.latency_ms || 0) + " ms"),
  );
  const sections = [
    el("div", { class: "io-sec" }, el("div", { class: "io-label" }, t("Sent to provider")),
      r.upstream_url ? el("div", { class: "io-url" }, r.upstream_url) : null,
      _jsonBlock(r.upstream_request)),
    el("div", { class: "io-sec" }, el("div", { class: "io-label" }, t("Provider response")),
      _jsonBlock(r.upstream_response)),
  ];
  if (r.error) {
    sections.push(el("div", { class: "io-sec" }, el("div", { class: "io-label" }, t("Error")),
      el("pre", { class: "io-block io-err" }, r.error)));
  }
  form.append(meta, ...sections);

  $("#modal-save").hidden = true;
  const cancel = $("#modal-cancel");
  cancel.textContent = t("Done");
  cancel.classList.remove("btn-ghost");
  cancel.classList.add("btn-signal");
  $("#modal-scrim").hidden = false;
}

// ------------------------------------------------------------------ playground
async function renderPlayground(content, crumb) {
  crumb.textContent = t("Playground");
  await loadRef();

  content.append(
    el("div", { class: "view-head" }, el("div", {},
      el("div", { class: "eyebrow" }, t("playground")),
      el("h1", { class: "view-title" }, t("Test bench")),
      el("p", { class: "view-desc" }, t("Send a request through the real routing path and see which deployment served it. Authenticated by the master key — virtual-key budgets and limits are bypassed.")),
    ))
  );

  const datalist = el("datalist", { id: "pg-aliases" }, ...ref.aliases.map((a) => el("option", { value: a.name })));
  const modelInput = el("input", { type: "text", id: "pg-model", list: "pg-aliases", placeholder: t("alias name or provider/model") });
  if (ref.aliases[0]) modelInput.value = ref.aliases[0].name;
  const sysInput = el("textarea", { id: "pg-system", placeholder: t("Optional system prompt") });
  const userInput = el("textarea", { id: "pg-user", placeholder: t("Your message") }, t("Reply with a single word: ping."));
  const tempInput = el("input", { type: "number", id: "pg-temp", step: "any", placeholder: t("default") });
  const maxInput = el("input", { type: "number", id: "pg-max", placeholder: t("default") });
  const sendBtn = el("button", { class: "btn btn-signal", type: "submit" }, t("Send request"));

  const output = el("div", { class: "pg-output empty" }, t("Send a request to see the response and routing metadata."));
  const meta = el("div", { class: "pg-meta", hidden: "" });

  async function send(ev) {
    ev.preventDefault();
    const model = modelInput.value.trim();
    if (!model) return;
    const messages = [];
    if (sysInput.value.trim()) messages.push({ role: "system", content: sysInput.value });
    messages.push({ role: "user", content: userInput.value });
    const body = { model, messages };
    if (tempInput.value !== "") body.temperature = Number(tempInput.value);
    if (maxInput.value !== "") body.max_tokens = Number(maxInput.value);

    sendBtn.disabled = true;
    sendBtn.textContent = t("Routing…");
    meta.hidden = true;
    output.className = "pg-output empty";
    output.textContent = t("Waiting for upstream…");
    const t0 = performance.now();
    try {
      const res = await api("POST", "/admin/playground/chat", body);
      output.className = "pg-output";
      output.textContent = res.content || t("(empty response)");
      const m = res.meta;
      meta.innerHTML = "";
      const chip = (label, val, amber) => el("span", { class: "pg-chip" + (amber ? " amber" : "") }, el("b", {}, t(label)), String(val));
      meta.append(
        chip("alias", m.alias, true),
        chip("deployment", m.deployment_id == null ? t("synthetic") : "#" + m.deployment_id),
        chip("provider", m.provider_type),
        chip("model", m.upstream_model),
        chip("tokens", `${m.prompt_tokens}+${m.completion_tokens}=${m.total_tokens}`),
        chip("cost", "$" + m.cost),
        chip("latency", m.latency_ms + " ms"),
        chip("retries", m.retries),
      );
      meta.hidden = false;
    } catch (e) {
      output.className = "pg-output pg-error";
      output.textContent = "✕ " + e.message + " (" + Math.round(performance.now() - t0) + " ms)";
    } finally {
      sendBtn.disabled = false;
      sendBtn.textContent = t("Send request");
    }
  }

  const field = (label, ctrl, hint) => el("label", { class: "field" },
    el("span", { class: "field-label" }, label), ctrl, hint ? el("span", { class: "field-hint" }, hint) : null);

  const form = el("form", { class: "panel pg-form", onsubmit: send },
    field(t("Model"), modelInput, t("A configured alias, or provider/model like kimi/moonshot-v1-8k.")),
    field(t("System"), sysInput),
    field(t("Message"), userInput),
    el("div", { class: "pg-row" },
      field(t("Temperature"), tempInput),
      field(t("Max tokens"), maxInput),
    ),
    el("div", { class: "pg-actions" }, sendBtn),
  );

  const responsePanel = el("div", { class: "panel pg-response" },
    el("div", { class: "pg-response-head eyebrow" }, t("response")),
    meta,
    output,
  );

  content.append(el("div", { class: "pg-grid" }, form, responsePanel));
}

// ------------------------------------------------------------------ modal / forms
let modalState = null;
let modalDirty = false;

function fieldControl(f, value) {
  const id = "f_" + f.name;
  const set = (n) => { n.id = id; n.name = f.name; return n; };
  if (f.type === "checkbox") {
    const inp = set(el("input", { type: "checkbox" }));
    if (value === undefined ? f.default : value) inp.checked = true;
    return el("label", { class: "field field-check" }, inp, el("span", { class: "field-label" }, t(f.label)));
  }
  let control;
  if (f.type === "select") {
    control = set(el("select", {}));
    for (const o of f.options()) control.append(el("option", { value: o.value }, o.label));
    if (value !== undefined && value !== null) control.value = value;
  } else if (f.type === "tristate") {
    control = set(el("select", {}));
    [["", t("inherit")], ["true", t("on")], ["false", t("off")]].forEach(([v, l]) => control.append(el("option", { value: v }, l)));
    control.value = value === true ? "true" : value === false ? "false" : "";
  } else if (f.type === "json") {
    control = set(el("textarea", {}));
    control.value = value ? JSON.stringify(value, null, 2) : "";
  } else if (f.type === "csv") {
    control = set(el("input", { type: "text" }));
    control.value = Array.isArray(value) ? value.join(", ") : (value || (f.default ? f.default.join(", ") : ""));
  } else if (f.type === "datetime-local") {
    control = set(el("input", { type: "datetime-local" }));
    if (value) control.value = String(value).slice(0, 16);
  } else {
    control = set(el("input", { type: f.type === "password" ? "password" : f.type === "number" ? "number" : "text" }));
    if (value !== undefined && value !== null) control.value = value;
    else if (f.default !== undefined && f.type !== "password") control.value = f.default;
  }
  if (f.type === "number") control.step = "any";
  return el("label", { class: "field" },
    el("span", { class: "field-label" }, t(f.label)),
    control,
    f.hint ? el("span", { class: "field-hint" }, t(f.hint)) : null,
  );
}

function openModal(route, record) {
  resetModalChrome();
  const schema = SCHEMAS[route];
  const editing = !!record;
  modalState = { route, schema, editing, id: record ? record.id : null };
  $("#modal-eyebrow").textContent = t(editing ? "edit" : "new");
  $("#modal-title").textContent = t(editing ? "Edit {x}" : "New {x}", { x: t(schema.singular) });
  const form = $("#modal-form");
  form.innerHTML = "";
  $("#modal-err").hidden = true;

  for (const f of schema.fields) {
    if (editing && f.createOnly) continue;
    const val = record ? record[f.name] : undefined;
    form.append(fieldControl(f, val));
  }
  $("#modal-scrim").hidden = false;
  modalDirty = false;
  const first = form.querySelector("input, select, textarea");
  if (first) first.focus();
}

function closeModal() {
  $("#modal-scrim").hidden = true;
  modalState = null;
  modalDirty = false;
}

// Guarded close: only confirm when the form has unsaved edits, so accidental
// backdrop clicks / Escape don't silently throw away work.
function requestCloseModal() {
  if (modalDirty && !window.confirm(t("Discard unsaved changes?"))) return;
  closeModal();
}

function collectForm() {
  const { schema, editing } = modalState;
  const form = $("#modal-form");
  const out = {};
  for (const f of schema.fields) {
    if (editing && f.createOnly) continue;
    const node = form.elements[f.name];
    if (!node) continue;
    if (f.type === "checkbox") { out[f.name] = node.checked; continue; }
    const raw = node.value.trim();
    if (f.type === "password" && f.omitIfEmpty && raw === "") continue;
    if (f.type === "tristate") { out[f.name] = raw === "" ? null : raw === "true"; continue; }
    if (f.type === "number") {
      if (raw === "") { out[f.name] = f.default !== undefined ? f.default : null; }
      else out[f.name] = Number(raw);
      continue;
    }
    if (f.type === "json") {
      if (raw === "") { out[f.name] = {}; continue; }
      out[f.name] = JSON.parse(raw); // throws -> caught by caller
      continue;
    }
    if (f.type === "csv") {
      out[f.name] = raw === "" ? [] : raw.split(",").map((s) => s.trim()).filter(Boolean);
      continue;
    }
    if (f.type === "datetime-local") { out[f.name] = raw === "" ? null : new Date(raw).toISOString(); continue; }
    out[f.name] = raw;
  }
  return out;
}

async function submitModal(ev) {
  ev.preventDefault();
  const { schema, editing, id } = modalState;
  let payload;
  try { payload = collectForm(); }
  catch { showModalErr(t("Invalid JSON in one of the fields.")); return; }

  try {
    if (editing) {
      await api("PATCH", schema.endpoint + "/" + id, payload);
      toast(t("{x} updated", { x: t(schema.singular) }));
      closeModal();
      renderView(modalStateRoute());
    } else {
      const created = await api("POST", schema.endpoint, payload);
      if (schema.endpoint === "/admin/keys" && created && created.key) {
        closeModal();
        revealKey(created);
      } else if (schema.endpoint === "/admin/users" && created && created.password) {
        closeModal();
        revealSecret(t("User created"), created.username, created.password,
          t("Copy it now — this is the only time the password is shown."));
      } else {
        toast(t("{x} created", { x: t(schema.singular) }));
        closeModal();
      }
      renderView(ROUTE);
    }
  } catch (e) {
    showModalErr(e.message);
  }
}
function modalStateRoute() { return modalState ? modalState.route : ROUTE; }
function showModalErr(msg) { const n = $("#modal-err"); n.textContent = msg; n.hidden = false; }

function revealKey(created) {
  revealSecret(t("Virtual key created"), created.name + " · " + created.key_prefix + "…",
    created.key, t("Copy it now — this is the only time the full key is shown."));
}

function revealSecret(title, label, value, note) {
  // reuse modal chrome to present a one-time secret (key or password)
  $("#modal-eyebrow").textContent = t("secret");
  $("#modal-title").textContent = title;
  const form = $("#modal-form");
  form.innerHTML = "";
  const code = el("code", {}, value);
  form.append(el("div", { class: "reveal" },
    el("div", { class: "reveal-label" }, label),
    el("div", { class: "reveal-value" }, code,
      el("button", { class: "btn btn-sm", type: "button", onclick: () => { navigator.clipboard?.writeText(value); toast(t("Copied to clipboard")); } }, t("Copy"))),
    el("p", { class: "reveal-note" }, note),
  ));
  $("#modal-err").hidden = true;
  $("#modal-scrim").hidden = false;
  // turn the footer into a single acknowledge button
  $("#modal-save").hidden = true;
  const cancel = $("#modal-cancel");
  cancel.textContent = t("Done");
  cancel.classList.remove("btn-ghost");
  cancel.classList.add("btn-signal");
}

// reset footer buttons whenever the modal opens for a form
function resetModalChrome() {
  $("#modal-save").hidden = false;
  $("#modal-save").textContent = t("Save");
  const cancel = $("#modal-cancel");
  cancel.textContent = t("Cancel");
  cancel.classList.add("btn-ghost");
  cancel.classList.remove("btn-signal");
}

// ------------------------------------------------------------------ auth / boot
function showApp() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#status-text").textContent = location.host;
  // Only the master may manage users — hide the nav entry otherwise.
  const usersNav = document.querySelector('.nav-item[data-route="users"]');
  if (usersNav) usersNav.style.display = IS_MASTER ? "" : "none";
  renderView(location.hash.slice(1) || "overview");
}
function logout() {
  MASTER = "";
  sessionStorage.removeItem(KEY_STORE);
  sessionStorage.removeItem(ROLE_STORE);
  $("#app").hidden = true;
  $("#login").hidden = false;
  $("#master-key").value = "";
  const u = $("#login-user");
  if (u) u.value = "";
}

async function tryLogin(username, secret) {
  if (username) {
    // User login: exchange username + password for a session token.
    const res = await fetch("/admin/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password: secret }),
    });
    if (!res.ok) throw new Error(t("Invalid username or password"));
    MASTER = (await res.json()).token;
    IS_MASTER = false;
  } else {
    // Master-key login: the key is itself the bearer.
    MASTER = secret;
    await api("GET", "/admin/providers/provider-types");  // validate
    IS_MASTER = true;
  }
  sessionStorage.setItem(KEY_STORE, MASTER);
  sessionStorage.setItem(ROLE_STORE, String(IS_MASTER));
  showApp();
}

function wire() {
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const errn = $("#login-err");
    errn.hidden = true;
    try { await tryLogin($("#login-user").value.trim(), $("#master-key").value); }
    catch (err) { errn.textContent = err.message; errn.hidden = false; }
  });
  $("#signout").addEventListener("click", logout);
  $("#nav").addEventListener("click", (e) => {
    const item = e.target.closest(".nav-item");
    if (!item) return;
    e.preventDefault();
    location.hash = item.dataset.route;
  });
  window.addEventListener("hashchange", () => { if (!$("#app").hidden) renderView(location.hash.slice(1) || "overview"); });

  $("#modal-close").addEventListener("click", requestCloseModal);
  $("#modal-cancel").addEventListener("click", requestCloseModal);
  $("#modal-form").addEventListener("submit", submitModal);
  // Mark the form dirty on any edit so the guard knows there's work to protect.
  $("#modal-form").addEventListener("input", () => { modalDirty = true; });
  $("#modal-form").addEventListener("change", () => { modalDirty = true; });

  // Backdrop dismiss: require the press AND release to land on the scrim itself,
  // so dragging/selecting text inside a field and releasing outside never closes.
  let scrimPressTarget = null;
  $("#modal-scrim").addEventListener("mousedown", (e) => { scrimPressTarget = e.target; });
  $("#modal-scrim").addEventListener("click", (e) => {
    const pressedScrim = scrimPressTarget && scrimPressTarget.id === "modal-scrim";
    scrimPressTarget = null;
    if (e.target.id === "modal-scrim" && pressedScrim) requestCloseModal();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal-scrim").hidden) requestCloseModal(); });

  document.querySelectorAll(".lang-select").forEach((sel) => sel.addEventListener("change", (e) => setLang(e.target.value)));
}

(async function boot() {
  wire();
  applyStaticI18n();
  if (MASTER) {
    try { await api("GET", "/admin/providers/provider-types"); showApp(); }
    catch { logout(); }
  } else {
    $("#login").hidden = false;
  }
})();
