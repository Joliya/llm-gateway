# llm-gateway 使用文档

一份面向使用者的完整指南：如何接入管理 API、客户端如何用 **OpenAI SDK** 接入、以及在 **Web UI** 上如何配置负载均衡。

- 默认服务地址：`http://localhost:8000`
- OpenAI 兼容入口：`http://localhost:8000/v1`
- Web 控制台：`http://localhost:8000/`（重定向到 `/ui/`）
- 两套鉴权：
  - **管理端 `/admin/*`** → `Authorization: Bearer <GW_MASTER_KEY>`（master key，来自环境变量）
  - **代理端 `/v1/*`** → `Authorization: Bearer <virtual-key>`（在 UI/管理 API 里签发的虚拟 Key，形如 `sk-gw-...`）

---

## 0. 核心概念（先理解这 5 个对象）

| 对象 | 作用 | 关键字段 |
|------|------|----------|
| **Provider** 供应商 | 一类上游服务，决定用哪个适配器与默认地址 | `provider_type`（`openai_compat`/`anthropic`/`gemini`）、`default_base_url` |
| **Credential** 凭证 | 某个 Provider 下的一把 API Key（**加密落库**），可带权重/限流/自定义 header | `api_key`、`weight`、`rpm_limit`、`base_url` 覆盖 |
| **Alias** 别名 | 对外暴露的逻辑模型名，也是**负载均衡组**与**降级链**的载体 | `name`、`lb_strategy`、`fallback_aliases` |
| **Deployment** 部署 | 把「某个 Alias + 某把 Credential + 某个上游模型」绑成一个可调用实例，并可**写死参数** | `upstream_model`、`pinned_params`、`weight`、价目 |
| **VirtualKey** 虚拟 Key | 发给客户端的密钥，限定可用别名、限流、预算 | `allowed_aliases`、`rpm_limit`、`max_budget` |

关系：一个 **Alias** 下挂多个 **Deployment** → 负载均衡；Deployment 引用 **Credential**；Credential 归属 **Provider**。客户端请求里的 `model` 字段填 **Alias 名**（推荐）或 `provider/model` 前缀。

请求链路：

```
客户端(OpenAI SDK)
  → Auth(虚拟 Key) → Budget(预算) → RateLimit(限流)
  → Router(解析 model: alias 名 | provider/model 前缀)
  → LoadBalancer(从 alias 池选 deployment)
  → CircuitBreaker(跳过冷却中的不健康节点)
  → Transform(drop→default→pinned 参数策略 + 供应商格式转换)
  → ProviderAdapter(httpx 调上游, 支持 SSE)
  → [失败: 池内重试 → fallback 链]
  → 归一化回 OpenAI 格式 → 记录用量/成本/缓存 → 返回
```

---

## 1. 通过管理 API 接入（脚本化配置）

所有管理接口需带 master key。下面用环境变量简化：

```bash
export BASE=http://localhost:8000
export M="Authorization: Bearer $GW_MASTER_KEY"
export J="content-type: application/json"
```

### 1.1 创建 Provider

```bash
curl -s $BASE/admin/providers -H "$M" -H "$J" -d '{
  "name": "openai",
  "provider_type": "openai_compat",
  "default_base_url": "https://api.openai.com/v1",
  "model_prices": {"gpt-4o": {"input": 2.5, "output": 10}}
}'
```

`model_prices` 为可选的价目表（每百万 token 单价），用于给**前缀路由**（`provider/model` 形式、无 Deployment）计费，详见 2.5。

`provider_type` 可选值（查询 `GET /admin/provider-types` 获取最新列表）：

- `openai_compat` / `openai` — OpenAI 及所有兼容服务（Kimi/Moonshot、DeepSeek、通义/DashScope-compatible、vLLM、Ollama…），靠 `base_url` 区分。
- `anthropic` — Claude 原生 API（参数自动在 OpenAI ↔ Anthropic 间转换）。
- `gemini` — Google Gemini（参数自动在 OpenAI ↔ Gemini 间转换）。

> 接 Kimi：再建一个 Provider，`name=kimi`，`provider_type=openai_compat`，`default_base_url=https://api.moonshot.cn/v1`。

### 1.2 创建 Credential（API Key 加密存储）

```bash
curl -s $BASE/admin/credentials -H "$M" -H "$J" -d '{
  "provider_id": 1,
  "name": "openai-key-1",
  "api_key": "sk-...",
  "weight": 1,
  "rpm_limit": 600
}'
```

- `api_key` 用 Fernet 加密落库，接口**永不回显**明文。
- `base_url` 可在凭证级覆盖 Provider 默认地址（多区域/代理时有用）。
- `weight` 用于 `weighted` 负载均衡；`rpm_limit`/`tpm_limit` 是凭证级限流。
- `extra_headers` 可注入自定义请求头（如 `{"x-api-version":"2024-..."}`）。

### 1.3 创建 Alias（负载均衡组）

```bash
curl -s $BASE/admin/aliases -H "$M" -H "$J" -d '{
  "name": "gpt-4o-balanced",
  "lb_strategy": "round_robin",
  "fallback_aliases": ["gpt-4o-backup"]
}'
```

- `lb_strategy`：`round_robin`（默认，轮询）｜ `weighted`（按部署权重）｜ `least_busy`（最少在途请求）｜ `random`。
- `fallback_aliases`：本组全部失败后，按顺序降级到的其它 Alias 名（形成降级链）。
- `cache_enabled`：三态，`null`=随全局、`true/false`=覆盖全局缓存开关。

### 1.4 创建 Deployment（绑定上游模型 + 写死参数）

```bash
curl -s $BASE/admin/deployments -H "$M" -H "$J" -d '{
  "alias_id": 1,
  "credential_id": 1,
  "upstream_model": "gpt-4o",
  "weight": 1,
  "pinned_params": {"temperature": 0},
  "default_params": {"max_tokens": 1024},
  "drop_params": ["presence_penalty"],
  "input_price": 2.5,
  "output_price": 10
}'
```

参数策略按顺序应用，这正是「添加模型时写死参数」的能力：

1. **`drop_params`** — 先剔除上游不支持的字段（客户端传了也丢）。
2. **`default_params`** — 客户端**没传**时才补默认值。
3. **`pinned_params`** — **强制覆盖**客户端传值（写死，客户端改不了）。

> 想让同一个 `gpt-4o-balanced` 同时打到 OpenAI 和 Azure？再建一个 Deployment，`alias_id` 相同、换 `credential_id` 与 `upstream_model` 即可——这就是负载均衡的本质。

`input_price`/`output_price` 为每百万 token 单价，用于成本统计。

### 1.5 签发 Virtual Key（发给客户端）

```bash
curl -s $BASE/admin/keys -H "$M" -H "$J" -d '{
  "name": "team-a",
  "allowed_aliases": ["gpt-4o-balanced", "kimi-balanced"],
  "rpm_limit": 60,
  "max_budget": 50,
  "budget_period": "monthly"
}'
```

- 响应里的 `key` 字段（`sk-gw-...`）**只在创建时返回一次**，请立即保存。
- `allowed_aliases`：`["*"]` 表示放行全部别名；否则白名单限定。
- `budget_period`：`total` ｜ `daily` ｜ `monthly`；`max_budget` 单位与价目一致（美元）。
- `expires_at`：可选过期时间（ISO 8601）。

### 1.6 查询用量与日志

```bash
curl -s "$BASE/admin/logs?limit=50" -H "$M"            # 逐条请求日志(成功与失败都记录)
curl -s "$BASE/admin/logs/123" -H "$M"                 # 单条详情:实际发给供应商的请求体 + 上游原始响应
curl -s "$BASE/admin/usage?since_hours=24" -H "$M"     # 按 alias 汇总(请求数/token/成本/平均延迟)
curl -s "$BASE/admin/deployment-health" -H "$M"        # 各部署的熔断/健康状态
```

**上游请求/响应留痕(排查参数转换的利器)**:每条日志都会记录网关**实际发给供应商的 JSON 请求体**(`upstream_request`)与**上游返回的原始响应**(`upstream_response`)及 `upstream_url`。`GET /admin/logs` 列表用 `has_upstream_io` 标记是否有留痕,完整内容通过 `GET /admin/logs/{id}` 取(响应体可能较大,故不放进列表)。

- 失败请求(如上游 401/429/超时)**同样记录**,并保留所用的 alias/部署/供应商信息,便于定位是哪一路出错。
- 用它可以**直接核对思考等级等参数是否正确传递**——例如确认发给 DeepSeek 的请求体里确实有 `"thinking":{"type":"enabled"}` 和 `"reasoning_effort":"high"`。
- 开关:`GW_LOG_UPSTREAM_IO`(默认 `true`);请求体含 prompt 内容,介意隐私可设 `false`。`GW_LOG_UPSTREAM_MAX_CHARS` 限制超长响应的存储字符数(默认 20000)。

**异步落库(高并发降尾延迟)**:默认日志写库**不在请求路径上**——入内存队列、由后台 worker 批量写入(`GW_LOG_ASYNC=true`,SQLite 后端除外,始终同步内联以避开单写锁)。这样每个请求不必等日志 insert/commit,显著降低高 QPS 下的尾延迟与数据库写压力。

- 队列满(极端过载、写库跟不上)时**丢弃该条日志并计数,绝不阻塞请求**。注意:计费 `spend` 始终同步落库,丢的只是观测用的日志行,不影响计费准确性。
- 异步模式下日志有**极短的写入延迟**(亚秒级),发完请求立刻查 `GET /admin/logs` 可能还没出现,稍候即可。需要严格「读己之写」可设 `GW_LOG_ASYNC=false`。
- 相关旋钮:`GW_LOG_QUEUE_MAX`(队列容量,默认 10000)、`GW_LOG_BATCH_SIZE`(单事务批量,默认 100)、`GW_LOG_FLUSH_INTERVAL`(最长刷新间隔秒,默认 0.5)。

### 1.7 管理 API 速查表

| 资源 | 方法与路径 |
|------|-----------|
| Providers | `GET/POST /admin/providers`、`PATCH/DELETE /admin/providers/{id}`、`GET /admin/provider-types` |
| Credentials | `GET/POST /admin/credentials`、`PATCH/DELETE /admin/credentials/{id}` |
| Aliases | `GET/POST /admin/aliases`、`PATCH/DELETE /admin/aliases/{id}` |
| Deployments | `GET/POST /admin/deployments`、`PATCH/DELETE /admin/deployments/{id}` |
| Virtual keys | `GET/POST /admin/keys`、`PATCH/DELETE /admin/keys/{id}` |
| 观测 | `GET /admin/logs`、`GET /admin/logs/{id}`(含上游请求/响应)、`GET /admin/usage`、`GET /admin/deployment-health` |

---

## 2. 客户端用 OpenAI SDK 接入

只需把 SDK 的 **base_url 指向网关的 `/v1`**，**api_key 换成你的虚拟 Key**，`model` 填 Alias 名即可。其余用法与官方完全一致。

### 2.1 Python（openai>=1.0）

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="sk-gw-...",            # 你的虚拟 Key
)

resp = client.chat.completions.create(
    model="gpt-4o-balanced",        # 这里填 Alias 名
    messages=[{"role": "user", "content": "你好"}],
)
print(resp.choices[0].message.content)
```

流式：

```python
stream = client.chat.completions.create(
    model="gpt-4o-balanced",
    messages=[{"role": "user", "content": "讲个笑话"}],
    stream=True,
)
for chunk in stream:
    delta = chunk.choices[0].delta.content or ""
    print(delta, end="", flush=True)
```

### 2.2 Node.js / TypeScript（openai 包）

```ts
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:8000/v1",
  apiKey: "sk-gw-...",
});

const resp = await client.chat.completions.create({
  model: "gpt-4o-balanced",
  messages: [{ role: "user", content: "Hello" }],
});
console.log(resp.choices[0].message.content);
```

### 2.3 LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8000/v1",
    api_key="sk-gw-...",
    model="gpt-4o-balanced",
)
print(llm.invoke("你好").content)
```

### 2.4 curl

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-gw-..." \
  -H "content-type: application/json" \
  -d '{"model":"gpt-4o-balanced","messages":[{"role":"user","content":"hi"}]}'
```

### 2.5 `model` 字段的两种写法

1. **Alias 名（推荐）**：如 `gpt-4o-balanced`。命中你配置的负载均衡组，享受 LB / fallback / 写死参数 / 成本统计全套能力。
2. **`provider/model` 前缀**：如 `openai/gpt-4o`、`kimi/moonshot-v1-8k`。按 Provider 名路由，在该 Provider 的多把凭证间轮询，并自动做参数格式转换。适合「不想建 Alias、直接点名供应商和模型」的临时调用。
   - **计费**：前缀路由没有 Deployment 行，价格来自 **Provider 的价目表（`model_prices`）**——在 Provider 上配置 `{"gpt-4o": {"input": 2.5, "output": 10}}`（每百万 token 单价）即可。若该模型恰好也有 Deployment 配了价，Deployment 的价优先；都没配则成本记 0。

### 2.6 支持的 OpenAI 兼容端点

| 端点 | 说明 |
|------|------|
| `POST /v1/chat/completions` | 对话补全，支持 `stream: true` |
| `POST /v1/completions` | 文本补全 |
| `POST /v1/embeddings` | 向量嵌入 |
| `GET /v1/models` | 列出可用模型（即已配置的 Alias） |

> 跨供应商透明：即便底层打到 Anthropic 或 Gemini，请求与响应都按 OpenAI 格式收发，客户端无需改代码。

### 2.7 思考模式 / 推理等级（跨供应商统一字段）

各家「思考/推理」的字段都不一样。网关把它们统一成**一个 OpenAI 风格字段 `reasoning_effort`**，客户端只需传它，网关按 provider_type（以及 base_url 识别的兼容厂商）自动翻译成上游真正需要的格式。

**取值**：`"minimal" | "low" | "medium" | "high" | "max"`，或 `"none"`（关闭，同义词 `off`/`false`），也接受布尔值（`true`=medium，`false`=none）。`max` 是为 DeepSeek 等支持「最高档」的供应商预留的等级。

```python
client.chat.completions.create(
    model="claude-balanced",            # 底层无论是 OpenAI / Claude / Gemini / Qwen
    messages=[{"role": "user", "content": "证明素数有无穷多个"}],
    reasoning_effort="high",            # 统一字段，网关负责翻译
)
```

> OpenAI SDK 会校验字段，非其原生字段请放进 `extra_body={"reasoning_effort": "high"}`；`reasoning_effort` 本身是 OpenAI 原生字段，可直接传。

网关的翻译规则：

| provider（识别依据） | `reasoning_effort` 被翻译成 | 关闭（`none`）时 |
|------|------|------|
| **OpenAI / GPT-5 / o 系列**（`openai.com`） | 原样保留 `reasoning_effort` | 不下发该字段 |
| **Anthropic / Claude**（`anthropic`） | `thinking: {type:"enabled", budget_tokens:N}`，并自动抬高 `max_tokens`、移除 `temperature/top_p/top_k`（思考模式下上游禁止） | 不开启 thinking |
| **Gemini 2.5**（`gemini`） | `generationConfig.thinkingConfig: {thinkingBudget:N}` | `thinkingBudget: 0` |
| **通义 / Qwen**（base_url 含 `dashscope`/`aliyuncs`） | `enable_thinking: true` + `thinking_budget: N` | `enable_thinking: false` |
| **DeepSeek**（base_url 含 `deepseek`） | `thinking: {type:"enabled"}` + `reasoning_effort: "high"`（`max` 档 → `"max"`） | `thinking: {type:"disabled"}`（上游默认开启，需显式关闭） |
| **Kimi / Moonshot**（base_url 含 `moonshot`） | 丢弃该字段（推理靠选思考模型变体） | — |

各等级对应的 token 预算（budget-based 供应商）：

| 等级 | Anthropic `budget_tokens` | Gemini `thinkingBudget` | Qwen `thinking_budget` |
|------|------|------|------|
| minimal | 1024 | 512 | 1024 |
| low | 2048 | 2048 | 4096 |
| medium | 8192 | 8192 | 16384 |
| high | 16384 | 24576 | 32768 |
| max | 32000 | 32768 | 38912 |

要点：

- **DeepSeek 用请求级开关**（如 `deepseek-v4-pro`，参见[官方文档](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode)）：默认开启思考，网关把 `none` 映射为 `thinking:{type:"disabled"}` 来关闭；其余等级开启思考并下发 `reasoning_effort`（DeepSeek 只接受 `high`/`max`，低于 high 的等级归一到 `high`）。
- **Kimi 仍是「换模型」而非「传参」**：把 `upstream_model` 设成其思考模型变体即可，`reasoning_effort` 会被安全丢弃，不会让请求报错。
- **思考内容不混入答案**：Anthropic 的 thinking 块、Gemini 的 thought parts 在归一化时被过滤，`choices[].message.content` 只含最终答案。
- **可写死在部署上**：把 `reasoning_effort` 放进 Deployment 的 `pinned_params`（强制）或 `default_params`（客户端没传才补），就能做出 `claude-think-high` / `claude-fast` 这种「等级即别名」的模型，客户端连字段都不用传。识别厂商用的是该部署凭证的 base_url，所以同一别名下挂不同厂商的部署也能各自正确翻译。

---

## 3. 在 Web UI 上配置负载均衡

打开 `http://localhost:8000/`，用 `GW_MASTER_KEY` 登录。左侧导航对应上面的对象：`01 Providers → 02 Credentials → 03 Aliases → 04 Deployments → 05 Virtual keys → 06 Traffic → 07 Playground`。右上角可切换语言（EN / 中文）。

> 负载均衡 = **一个 Alias 选定策略 + 该 Alias 下挂多个 Deployment**。所以配置顺序是：先有 Provider 和 Credential，再建 Alias，最后往 Alias 里加多个 Deployment。

### 步骤 1 — 准备上游（Providers + Credentials）

1. **01 Providers** → `New`：填 `name`、选 `provider_type`、填 `default_base_url`。
   - 想在两个供应商间均衡（如 OpenAI 与 Kimi），就建两个 Provider。
2. **02 Credentials** → `New`：选所属 Provider，填 `name` 与 `api_key`。
   - 想在同一供应商的多把 Key 间均衡，就建多把 Credential，并设各自 `weight`。

### 步骤 2 — 建负载均衡组（Alias + 选策略）

进入 **03 Aliases** → `New`：

| 字段 | 填什么 | 对负载均衡的影响 |
|------|--------|------------------|
| **name** | 对外模型名，如 `gpt-4o-balanced` | 客户端 `model` 就填它 |
| **lb_strategy** | 选一个策略 | 决定如何在池内选节点 |
| **fallback_aliases** | 其它 Alias 名（可空） | 本组全挂后按序降级 |
| **cache_enabled** | 留空/开/关 | 是否缓存该别名响应 |

`lb_strategy` 四种策略：

- **round_robin（默认）**：轮询，请求依次轮转到各 Deployment，最均匀。
- **weighted**：按各 Deployment 的 `weight` 加权，权重大的分到更多流量（适合大小规格混用）。
- **least_busy**：选当前在途请求最少的节点（适合上游延迟波动大）。
- **random**：随机。

### 步骤 3 — 往组里加多个部署（Deployments，这是关键）

进入 **04 Deployments** → `New`，**为同一个 Alias 创建多条 Deployment**，每条指向不同的 Credential / 上游模型：

```
Alias: gpt-4o-balanced
 ├─ Deployment A: credential=openai-key-1, upstream_model=gpt-4o,        weight=2
 ├─ Deployment B: credential=openai-key-2, upstream_model=gpt-4o,        weight=1
 └─ Deployment C: credential=azure-key,     upstream_model=gpt-4o-2024,  weight=1
```

每条 Deployment 可填：

- **alias_id**：选刚建的 `gpt-4o-balanced`（UI 里是下拉选 Alias）。
- **credential_id**：选用哪把凭证。
- **upstream_model**：上游真实模型名。
- **weight**：`weighted` 策略下的权重。
- **rpm_limit / tpm_limit**：该部署的限流。
- **pinned_params / default_params / drop_params**：参数写死/默认/丢弃（JSON 输入）。
- **input_price / output_price**：每百万 token 单价，用于成本统计。

> 至此负载均衡已生效：客户端用 `model="gpt-4o-balanced"` 发请求，网关就按所选策略在 A/B/C 之间分发；某节点失败会自动池内重试，池内耗尽再走 `fallback_aliases` 降级链；连续失败的节点被熔断冷却，期间自动跳过。

### 步骤 4 — 配置降级链（可选）

回到 **03 Aliases**，编辑 `gpt-4o-balanced`，在 `fallback_aliases` 里填备用别名（如 `["gpt-4o-backup"]`，需另建该 Alias 及其 Deployment）。主组全部失败时按顺序尝试备用组。

### 步骤 5 — 签发 Virtual Key（给客户端用）

进入 **05 Virtual keys** → `New`：填 `name`，`allowed_aliases` 选 `gpt-4o-balanced`（或 `*`），按需设 `rpm_limit` / `max_budget` / `budget_period`。**保存后弹出的 `sk-gw-...` 只显示一次**，复制给客户端（即第 2 节里的 api_key）。

### 步骤 6 — 用 Playground 验证

进入 **07 Playground**：选 alias、填 prompt 发送。它走**真实路由路径**（负载均衡、写死参数、fallback、真实上游调用），并回显**实际命中的 Deployment、token、成本、延迟、重试次数**——用来确认负载均衡是否按预期分发。

> Playground 用 master key 鉴权，会**绕过**虚拟 Key 的预算与限流，仅供管理员测试。

### 步骤 7 — 观察流量（Traffic）

进入 **06 Traffic**：查看逐条请求日志，确认流量在多个 Deployment 间的分布是否符合所选策略。**点击任意一行**可展开详情，看到该次请求**实际发送给供应商的请求体**与**上游返回的原始响应**——用来核对思考等级等参数有没有被正确翻译并传递。失败的请求(如上游报错)也会记录在此。

---

## 4. 常见问题

- **改了配置多久生效？** 路由层用短 TTL 内存快照，默认 `GW_CONFIG_CACHE_TTL_SECONDS=5` 秒内热生效，无需重启。
- **`model` 填了不存在的名字？** 返回路由错误；请确认填的是已启用的 Alias 名，或合法的 `provider/model` 前缀。
- **客户端能改写死参数吗？** 不能。`pinned_params` 在服务端强制覆盖，客户端传值会被忽略。
- **返回 429 / 402？** 429=触发限流（虚拟 Key/部署/凭证级，可能转而走 fallback）；预算超限会被拒。
- **日志里成本一直是 0？** 几种可能：① 该请求失败（无 token，成本自然为 0）；② 流式请求未拿到用量——网关会自动给上游加 `stream_options.include_usage`，请确认上游为 OpenAI 兼容端点且会回传末尾 usage 块；③ 走 Alias 但 Deployment 没填 `input_price`/`output_price`；④ 走前缀路由但对应 Provider 没配 `model_prices` 价目表（见 2.5）。
- **多实例部署如何共享限流/缓存？** 设置 `GW_REDIS_URL`，限流与缓存自动切到 Redis 后端。
- **生产数据库？** 设 `GW_DATABASE_URL=postgresql+asyncpg://...`、`GW_AUTO_CREATE_TABLES=false`，由 `alembic upgrade head` 管理 schema（Docker 镜像入口已自动执行）。
