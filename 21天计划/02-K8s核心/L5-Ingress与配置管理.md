# L5 · Ingress 与配置管理（懂路由 → 懂配置注入 → 不把密钥写进 Git）

> **本篇定位**：把"流量怎么进集群"和"配置/密钥怎么进容器"两件事讲透。**最需要讲清的认知：Ingress 本身不做任何转发，它只是一份给 Ingress Controller 看的路由配置。最常见的误解：base64 是加密。**
> **前置**：[`L4-Service与网络`](L4-Service与网络.md)（Service 是集群内的稳定入口；Ingress 作用在它上面）、[`L2-K8s架构与对象模型`](L2-K8s架构与对象模型.md)（声明式与配置属主）。
> **学完能做什么**：写出一份带 TLS、带多 host 的 Ingress 并解释每个字段为什么这么填；拿到一个 404/502 能在三步内判断是"没 controller / 注解错 / pathType 不兼容 / TLS 配错"里的哪一个；说清 ConfigMap 三种注入方式的生效差异；解释 base64 为什么不等于加密，并给出正确的密钥管理路径。
> **在 21 天里**：D3（网络与配置）的主体；D17（等保与 DevSecOps）的密钥治理部分。
> **环境/版本基准**：本文字段语义以本地 kind 集群 `kubectl v1.37.1` / `Server v1.37.0` 为准；官方中文文档对应 v1.37 版本页（Ingress API 自 v1.19 起 GA）。**生产 ACK 的 Ingress Controller 由云厂商提供（ALB Ingress），注解体系与社区 nginx 不同，注解名必须以所用 Controller 的文档为准。**
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L5 Ingress 与配置管理
│
├─ 1. 概念
│   ├─ 1.1 一句话：Ingress 是"路由配置"，不是"转发器"
│   ├─ 1.2 没有 Ingress 时的问题
│   ├─ 1.3 三层认知：Ingress 资源 / Ingress Controller / 底层负载均衡
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 Ingress Controller 的工作循环（watch → 翻译 → reload）
│   ├─ 2.2 pathType 三种取值与匹配规则
│   ├─ 2.3 host 路由与通配符
│   ├─ 2.4 TLS 终结发生在哪一层
│   ├─ 2.5 注解：厂商扩展的主要载体（社区约定，非官方 API）
│   ├─ 2.6 ConfigMap 的三种消费方式与生效差异
│   ├─ 2.7 Secret 的类型体系
│   ├─ 2.8 base64 是编码不是加密（讲透）
│   ├─ 2.9 正确的密钥治理路径：静态加密 + RBAC + 外部密钥管理
│   └─ 2.10 关键参数与默认值表
│
├─ 3. 动手
│   ├─ 3.1 装一个 Ingress Controller
│   ├─ 3.2 写一份 Ingress 并验证
│   ├─ 3.3 ConfigMap 的三种注入实测
│   ├─ 3.4 Secret 的创建与"还原"演示
│   └─ 3.5 验证清单
│
├─ 4. 资源清单逐字段
│   ├─ 4.1 ConfigMap
│   └─ 4.2 Secret
│
├─ 5. 横向对比
│   ├─ 5.1 ConfigMap vs Secret vs 外部密钥管理
│   └─ 5.2 env 注入 vs volume 挂载
│
├─ 6. 踩坑与排障（症状 → 状态 → 原因）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 一句话定义

> **Ingress 是一个 API 对象，里面装的是一份"HTTP 路由规则表"。它自己不监听端口、不转发流量、不终结 TLS——真正干活的是集群里另一个程序：Ingress Controller。**

官方原文（英文，中文页同义）：

> **You must have an Ingress controller to satisfy an Ingress. Only creating an Ingress resource has no effect.**

> **仅创建 Ingress 资源本身没有任何效果。**

**这一句话是本篇的地基。** 它直接推出三个必然结论：

| 结论 | 推论 |
|---|---|
| **Ingress 不监听端口** | 它的 `spec` 里没有 `port` 字段——**它不是 Service 类型** |
| **Ingress 不转发流量** | 转发规则写在 **Controller 所在节点**上（nginx.conf / ALB 规则） |
| **Ingress 不终结 TLS** | 证书由 Controller 加载并解密，**后端收到的是明文 HTTP**（除非显式配后端协议） |

> **最常见的认知错误**：以为 `kubectl apply -f ingress.yaml` 之后，那个域名就能用了。**实际上如果没有 Ingress Controller，这个 Ingress 对象只是一条躺在 etcd 里的记录，没有任何程序会读它。**
>
> **这一条在第 6 节的踩坑表里排第一位**："404 但 Pod 明明活着"。

### 1.2 没有 Ingress 时的问题

假设你有 5 个服务要对外暴露，域名分别是 `api.example.com`、`admin.example.com`、`pay.example.com`……

| 裸机 / 简单方案 | 问题 |
|---|---|
| **每个服务一个 LoadBalancer** | **5 个 LB，每个月账单 ×5**；每个 LB 都要单独配证书、单独做安全组 |
| **NodePort + 域名解析到节点** | 客户端要访问 `节点IP:30001`，**换节点就挂**；要在客户端侧做负载均衡；证书要在每个节点上配 |
| **在业务里做路由（每个服务自己判 host）** | **每个服务都要实现一遍 TLS 终结和路由逻辑**；新加一个域名要改所有服务 |

**Ingress 一次性解决这四个问题**：

```
一个 Ingress Controller（通常前面挂一个 LB）
        │
        ├── host: api.example.com   → Service A
        ├── host: admin.example.com → Service B
        ├── host: pay.example.com   → Service C
        └── /                        → 默认后端
```

**一个入口、一份证书（通配符或 SAN）、一次路由表变更。**

> **Ingress 的核心价值不是"转发"，是"把七层路由集中管理"。** 这是它和 LoadBalancer（只管四层）的本质区别。

### 1.3 三层认知：Ingress 资源 / Ingress Controller / 底层负载均衡

**这三层必须分开。混淆它们是排障绕路的根源。**

```
┌──────────────────────────────────────────────────────────────────┐
│ 第 3 层：Ingress 资源（API 对象，躺在 etcd 里）                    │
│   spec.ingressClassName / rules[].host / paths[].path / backend   │
│   「我要什么路由」                                                │
└───────────────────────────┬──────────────────────────────────────┘
                            │  Controller 通过 watch 感知到变化
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ 第 2 层：Ingress Controller（集群里的一个 Deployment / DaemonSet） │
│   ingress-nginx / 阿里云 ALB Ingress Controller / Traefik ...     │
│   「把路由翻译成具体代理配置，并 reload」                          │
└───────────────────────────┬──────────────────────────────────────┘
                            │  Controller 把自己暴露出去
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│ 第 1 层：底层负载均衡 / 入口                                      │
│   云 SLB / ALB / 节点端口 / 裸机 Nginx                              │
│   「四层入口 + TLS 终结（可能在这里）」                            │
└──────────────────────────────────────────────────────────────────┘
```

**排障时先定位是哪一层**：

| 层 | 典型症状 | 查什么 |
|---|---|---|
| **第 1 层（LB）** | 域名根本解析不到 / 443 不通 | DNS 解析、LB 监听端口、`kubectl get svc -o wide` 看 EXTERNAL-IP |
| **第 2 层（Controller）** | 域名能解析、连得上，但 **404 / 502** | Controller Pod 是否 Running；`kubectl get ingress` 的 ADDRESS 是否为空；Controller 日志 |
| **第 3 层（Ingress 资源）** | Controller 正常但路由不对 | `kubectl describe ingress` 看规则；pathType、host、注解、TLS 配置 |

> **一个非常实用的判别法**：
> - **`kubectl get ingress` 的 `ADDRESS` 列是空的** → **第 2 层没接管**（没有匹配的 Controller / `ingressClassName` 写错）
> - **ADDRESS 有值但访问 404** → **路由规则没匹配上**（pathType / host 问题）
> - **ADDRESS 有值、404/502 且 Controller 日志有报错** → **注解或后端问题**
>
> **这四步能把 90% 的 Ingress 问题定位掉，而且每一步都是一条命令。**

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Ingress | 入口/路由 | **七层路由配置对象**（本身不转发） |
| Ingress Controller | 控制器 | 集群里真正实现路由的程序 |
| IngressClass | 路由类 | 声明"这类 Ingress 归哪个 Controller 管" |
| `ingressClassName` | 路由类名 | 指向 IngressClass 资源 |
| `spec.rules[].host` | 主机规则 | 按 HTTP Host 头匹配 |
| `spec.rules[].http.paths[].path` | 路径规则 | 按 URL 路径匹配 |
| `pathType` | 路径类型 | `Exact` / `Prefix` / `ImplementationSpecific` |
| `backend.service` | 后端服务 | 指向集群内的 Service（不是 Pod） |
| `defaultBackend` | 默认后端 | 匹配不上任何规则时的去处 |
| Annotation | 注解 | **厂商/社区扩展的主要载体**（非官方 API） |
| TLS Secret | TLS 密钥 | 类型 `kubernetes.io/tls`，含 `tls.crt` + `tls.key` |
| ConfigMap | 配置映射 | 非敏感配置（key-value） |
| Secret | 密钥 | 敏感配置（**默认只是 base64，不是加密**） |
| `stringData` | 明文数据字段 | 写入时由 API Server 自动转成 base64 |
| `binaryData` | 二进制字段 | ConfigMap 专用，base64 编码的二进制 |
| `immutable` | 不可变 | ConfigMap/Secret 可设为不可变 |
| EncryptionConfiguration | 加密配置 | apiserver 侧配置，对 etcd 里的资源做静态加密 |
| External Secrets Operator | 外部密钥同步器 | 从 Vault/KMS 把密钥同步成 K8s Secret |

---

## 2. 机制

### 2.1 Ingress Controller 的工作循环

**这一节是本篇最需要讲清的机制。** 官方对 Ingress Controller 的定位是：

> An Ingress controller is responsible for fulfilling the Ingress, usually with a load balancer, though it may also configure your edge router or additional frontends to help handle the traffic.

**"fulfilling the Ingress"（兑现 Ingress）** —— 这个词很准确：Controller 的工作就是**把 Ingress 资源里声明的路由，兑现成真实可用的转发规则**。

**社区 ingress-nginx 的典型工作循环**（⚠️ 以下为**社区 ingress-nginx 的实现方式**，非 K8s 官方 API 行为，不同 Controller 细节不同）：

```
① watch Ingress 资源
   Controller 通过 apiserver 的 watch 接口监听 Ingress / IngressClass
   变化时触发 reconcile
        │
        ▼
② 翻译成具体代理配置
   把 spec.rules 翻译成 nginx.conf 的 server / location / upstream 块
   把 backend.service.name 翻译成 upstream，指向 Service 的 ClusterIP
        │
        ▼
③ 写入共享目录
   写到一个 emptyDir 或 ConfigMap 挂载出来的目录
        │
        ▼
④ reload
   执行 nginx -s reload（平滑重载，不断开已有连接）
        │
        ▼
⑤ 对外暴露自己
   Controller 自己也是一个 Deployment + Service
   Service 通常是 LoadBalancer 或 NodePort 类型
   → 前面再挂云 LB 或直接用节点 IP
```

**第 ④ 步的"平滑重载"值得强调**：nginx 的 reload 是**优雅的**——老 worker 处理完手上的连接再退出，新 worker 接收新连接。**这就是为什么 Ingress 更新几乎不掉流量**（对比 Deployment 滚动更新还要等就绪探针）。

**但第 ⑤ 步有一个常见误解**：很多教程让你"直接访问 NodePort 上的 Controller 端口"，**但实际上 Controller 前面通常还有一层 LB**。真实的访问链路是：

```
用户 → 域名 DNS → 云 SLB/ALB（TLS 可能终结在这）
     → Ingress Controller 的 Pod（nginx worker）
     → upstream（Service ClusterIP）
     → kube-proxy → Pod
```

> **如果是社区 ingress-nginx 自己配了证书**（用 `kubernetes.io/tls` Secret），**TLS 终结在 nginx 这一层**；
> **如果是云厂商的 ALB Ingress**，**证书挂在 ALB 上**，TLS 在 ALB 那层终结，Controller 收到的是 HTTP。
> **这两种架构的排查路径不一样**——所以你说的是哪种 Controller，很重要。

### 2.2 pathType 三种取值与匹配规则

**这是本篇最容易踩坑、也最容易讲错的地方。** 官方对三种取值的定义：

| pathType | 官方定义（中文原文） | 匹配规则 |
|---|---|---|
| **`Exact`** | 精确匹配 URL 路径，且区分大小写 | **完全相等**才算匹配 |
| **`Prefix`** | 基于以 `/` 分隔的 URL 路径前缀匹配。匹配区分大小写，并且对路径中各个元素逐个执行匹配操作 | **按 `/` 分段**逐段前缀匹配 |
| **`ImplementationSpecific`** | 对于这种路径类型，匹配方法取决于 IngressClass。具体实现可以将其作为单独的 `pathType` 处理或者作与 `Prefix` 或 `Exact` 类型相同的处理 | **完全由 Controller 决定** |

**官方给出的完整匹配表（这张表值得原样记住）**：

| 类型 | 路径 | 请求路径 | 匹配？ |
|---|---|---|---|
| Prefix | `/`（所有路径） | — | 是 |
| Exact | `/foo` | `/foo` | 是 |
| Exact | `/foo` | `/bar` | 否 |
| Exact | `/foo` | `/foo/` | **否** |
| Exact | `/foo/` | `/foo` | **否** |
| Prefix | `/foo` | `/foo`, `/foo/` | 是 |
| Prefix | `/foo/` | `/foo`, `/foo/` | 是 |
| Prefix | `/aaa/bb` | `/aaa/bbb` | **否** |
| Prefix | `/aaa/bbb` | `/aaa/bbb` | 是 |
| Prefix | `/aaa/bbb` | `/aaa/bbb/` | 是，忽略尾部斜线 |
| Prefix | `/aaa/bbb` | `/aaa/bbb/ccc` | 是，匹配子路径 |
| Prefix | `/aaa/bbb` | `/aaa/bbbxyz` | **否**，字符串前缀不匹配 |
| Prefix | `/`, `/aaa` | `/aaa/ccc` | 是，匹配 `/aaa` 前缀 |
| Prefix | `/`, `/aaa`, `/aaa/bbb` | `/aaa/bbb` | 是，匹配 `/aaa/bbb` 前缀 |
| Prefix | `/`, `/aaa`, `/aaa/bbb` | `/ccc` | 是，匹配 `/` 前缀 |
| Prefix | `/aaa` | `/ccc` | 否，使用默认后端 |
| 混合 | `/foo` (Prefix), `/foo` (Exact) | `/foo` | 是，**优选 Exact 类型** |

**从表里能读出三条关键结论**：

**① `Prefix` 不是字符串前缀，是"按 `/` 分段"匹配。**
`/aaa/bbb` **不匹配** `/aaa/bbbxyz` —— 因为最后一段 `bbbxyz` 不是 `bbb` 的前缀。**这和很多人以为的"字符串 startsWith"完全不同。**
**这直接导致一个高频 404**：路径写成 `/api`（Prefix），请求 `/apiv2/xxx` → **不匹配** → 走默认后端 → 404。
**正确写法是 `/api` 配成 Prefix 时，请求 `/api/v2/xxx` 是匹配的**（多一段），而 `/apiv2/xxx` 不匹配。

**② `Exact` 区分尾斜线。**
`/foo`（Exact）**不匹配** `/foo/`。**这是"我明明配了为什么 404"的经典来源**——浏览器或客户端自动补了个尾斜线。

**③ 多重匹配时的优先级**（官方原文）：

> In some cases, multiple paths within an Ingress will match a request. In those cases **precedence will be given first to the longest matching path**. If two paths are still equally matched, **precedence will be given to paths with an exact path type over prefix path type**.

**优先级：最长匹配路径 > Exact > Prefix。**

**`ImplementationSpecific` 的兼容性问题（这是本节的重点）**：

- 官方定义它"完全由 IngressClass 决定"——**这不是偷懒，是因为 K8s 无法为所有代理实现（nginx / Envoy / ALB / Traefik）定义一套统一的路径语义**
- **实际后果**：**同一个 Ingress 清单，换一个 Controller 可能行为就变了**
  - nginx 的 `location /foo` 传统上是**前缀匹配**（且会匹配 `/foobar`）
  - Envoy / ALB 的路径匹配规则与 nginx 不完全一致
- **官方甚至专门列出了 `Exact`/`Prefix` 在不同实现间的差异警告**（在 Ingress 概念页的 Path Matching 一节，⚠️ 具体措辞以官方对应版本页为准）
- **实践建议**：
  - **能用 `Prefix` 就不用 `ImplementationSpecific`**（虽然老教程大量用后者）
  - **`ImplementationSpecific` 迁移到新 API 时要逐条回归测试**——路径匹配行为可能变
  - **Ingress API 已冻结**（官方原文："The Ingress API is frozen"），**新项目直接上 Gateway API**

> **面试这么答**："`pathType` 有三种。`Exact` 完全相等且区分大小写和尾斜线；`Prefix` 是**按 `/` 分段**匹配，不是字符串 startsWith——所以 `/aaa/bbb` 匹配 `/aaa/bbb/ccc` 但不匹配 `/aaa/bbbxyz`；`ImplementationSpecific` 把语义交给 Controller，**这正是跨 Controller 不兼容的根源**。多路径同时匹配时，**最长路径优先，再 Exact 优先于 Prefix**。我们统一用 `Prefix`，因为它语义明确且官方定义了行为。"

### 2.3 host 路由与通配符

官方定义：

> Each HTTP rule contains an optional `host`. In this example, no host is specified, so the rule applies to **all inbound HTTP traffic** through the IP address specified. If a host is provided (e.g. `foo.bar.com`), the rules apply to that host.

| host 写法 | 匹配规则 | 官方示例 |
|---|---|---|
| **不写** | 匹配所有 Host（**catch-all**） | — |
| **精确** `foo.bar.com` | HTTP `Host` 头与值**完全匹配** | — |
| **通配符** `*.foo.com` | Host 头与**后缀**相同 | `bar.foo.com` ✅；`baz.bar.foo.com` ❌；`foo.com` ❌ |

**通配符的关键限制（官方原文）**：

> 通配符仅覆盖了一个 DNS 标签

**也就是说 `*.foo.com` 只匹配 `bar.foo.com`，不匹配 `baz.bar.foo.com`（那是两个标签）。** 通配符**只能是最左边一段**。

**多条规则同时匹配时的行为**：

- 官方示例（fanout）：一个 Ingress 里 `foo.bar.com` 和 `*.foo.com` 两条规则并存
- **精确 host 优先于通配符**（这是所有主流 Controller 的通用行为，⚠️ 具体细节以所用 Controller 文档为准）

**TLS 与 host 的强约束（官方原文的重要提醒）**：

> 注意，不能针对默认规则使用 TLS，因为这样做需要为所有可能的子域名签发证书。因此，**`tls` 字段中的 `hosts` 的取值需要与 `rules` 字段中的 `host` 完全匹配。**

**这条的实际后果**：你配了 `tls.hosts: ["*.example.com"]` 但 `rules` 里写的是 `api.example.com`（精确），**两者不完全一致 → 证书可能不生效**。
**正确做法：`tls.hosts` 与 `rules[].host` 严格对齐。**

### 2.4 TLS 终结发生在哪一层

**官方原文（很明确）**：

> Ingress 资源只支持一个 TLS 端口 **443**，并**假定 TLS 连接终止于 Ingress 节点**（**与 Service 及其 Pod 间的流量都以明文传输**）。

**这句话回答了"TLS 终结发生在哪一层"**：

```
用户 ──HTTPS──► [TLS 终结点]
                   │
                   ├─ 情况 A：Ingress Controller（社区 nginx + kubernetes.io/tls Secret）
                   │     · Controller 加载证书并解密
                   │     · 从 Controller 到 Service/Pod 是 **明文 HTTP**
                   │
                   └─ 情况 B：云厂商 LB/ALB（ALB Ingress，证书挂在 LB 上）
                         · LB 终结 TLS
                         · 从 LB 到 Controller 是明文 HTTP
                         · 从 Controller 到 Pod 仍是明文 HTTP
```

**TLS Secret 的结构（官方原文）**：

> TLS Secret 的数据中必须包含键名为 **`tls.crt`** 的证书和键名为 **`tls.key`** 的私钥，才能用于 TLS 目的。

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: testsecret-tls
type: kubernetes.io/tls
data:
  tls.crt: <base64 编码的证书>
  tls.key: <base64 编码的私钥>
```

**必须讲清的两个后果**：

1. **Ingress 只终结 HTTPS 到 HTTP。后端 Pod 收到的是明文。** → **如果你的服务需要"必须是 HTTPS"的语义（比如合规要求），光靠 Ingress 不够**，要么在应用层再校验，要么在 Ingress 上配"强制跳转 HTTPS"（这是注解，见 2.5）。
2. **后端 Service 不需要知道 TLS。** 它的 `targetPort` 指向容器监听的**明文 HTTP 端口**。**把 Ingress 的 `port.number` 和 Service 的 `targetPort` 对上，别对到 443。**

### 2.5 注解：厂商扩展的主要载体

**这是本篇最需要"标注清楚"的一节。**

官方对注解的定位只有一句：

> **Ingress controllers frequently use annotations to configure behavior. Review the documentation for your choice of ingress controller to learn which annotations are expected and / or supported.**

**翻译：注解是 Controller 行为的配置入口，但具体支持哪些注解，必须看你用的那个 Controller 的文档。**

> ## ⚠️ 关于注解的三条硬性说明
>
> **① 注解不是 K8s 官方 API。** `nginx.ingress.kubernetes.io/*` 这些键**在 K8s API 里没有任何字段定义**，apiserver 不校验、不认识、不报错。**它纯粹是"某个 Controller 约定的一段字符串"。**
>
> **② 换 Controller = 注解全部失效。** 社区 ingress-nginx 的注解搬到阿里云 ALB Ingress Controller 上**一条都不认**，而且**不报错**——只是静默不生效。
>
> **③ 官方 Ingress 文档里的例子也用了社区注解**（官方 fanout 示例的 describe 输出里出现了 `nginx.ingress.kubernetes.io/rewrite-target: /`），但那是**举例说明"注解存在"**，不代表这是官方 API。

**常见的社区 ingress-nginx 注解（⚠️ 均为社区约定，非官方 API，实际支持情况以所用 Controller 版本文档为准）**：

| 注解 | 作用 | 典型场景 |
|---|---|---|
| `nginx.ingress.kubernetes.io/rewrite-target` | **路径重写**。`/$2` 表示把捕获的第 2 组替换进去 | 前端路径 `/api` 要转到后端 `/` |
| `nginx.ingress.kubernetes.io/backend-protocol` | **后端协议**。可设 `HTTP` / `HTTPS` / `GRPC` | 后端 Service 本身是 HTTPS 或 gRPC |
| `nginx.ingress.kubernetes.io/ssl-redirect` | **强制跳转 HTTPS**（`"true"` / `"false"`） | 全站 HTTPS |
| `nginx.ingress.kubernetes.io/proxy-body-size` | **请求体大小上限** | 上传接口调大（默认 nginx 是 1m） |
| `nginx.ingress.kubernetes.io/proxy-read-timeout` | **读超时** | 长耗时接口 |
| `nginx.ingress.kubernetes.io/backend-modifier` | **负载均衡算法** | `ewma` 等 |
| `nginx.ingress.kubernetes.io/canary` + `canary-weight` | **金丝雀灰度** | 灰度发布 |

**阿里云 ALB Ingress Controller 的注解体系不同**（前缀是 `alb.ingress.kubernetes.io/*` 之类），⚠️ **具体注解名与支持矩阵必须查对应版本的 ACK 文档**（`help.aliyun.com/zh/ack/user-guide/`，见第 7 节 S12）。

> **面试这么答（这条很能体现工程判断）**："Ingress 的字段是**官方冻结的 API**，能力就那么几项——host、path、pathType、backend、TLS。**所有超出这个范围的需求，厂商都是通过 annotation 实现的**。所以 annotation 有三个特点：**① 它不是官方 API，apiserver 不校验，写错不报错；② 它和 Controller 强绑定，换厂商全部失效；③ 它是扩展的主要载体，但也意味着可移植性差。** 我们把 annotation 收敛在一个模板层里统一管理，就是怕散落各处到时候没人知道哪个 Controller 认哪个键。"

### 2.6 ConfigMap 的三种消费方式与生效差异

官方原文（ConfigMap 页）：

> 你可以使用**四种方式**来使用 ConfigMap 配置 Pod 中的容器：
> 1. 在容器命令和参数内
> 2. 容器的环境变量
> 3. 在只读卷里面添加一个文件，让应用来读取
> 4. 编写代码在 Pod 中运行，使用 Kubernetes API 来读取 ConfigMap

**对前三种方式，kubelet 使用 ConfigMap 中的数据在 Pod 中启动容器。第四种方式意味着你必须编写代码才能读取 ConfigMap 及其数据。**

**三种主流方式的生效差异（本节的表格是全篇最实用的部分）**：

| 方式 | 写法 | **改 ConfigMap 后会自动生效吗** | 延迟 | 适合 |
|---|---|---|---|---|
| **① 环境变量** | `env[].valueFrom.configMapKeyRef` / `envFrom[].configMapRef` | **❌ 不会** | **必须重启 Pod** | 少量开关、连接串 |
| **② 卷挂载（整个 ConfigMap 挂成目录）** | `volumes[].configMap` + `volumeMounts` | **✅ 会**（内容最终会被更新） | **kubelet 同步周期 + 缓存传播延迟** | **配置文件**（application.yml） |
| **③ 卷挂载 + `subPath`（挂单个文件）** | `volumeMounts[].subPath` | **❌ 不会** | — | 单个文件且确实不需要热更新 |

**官方对第 ② 种的原文（这段必须引用，因为很多人不知道有延迟）**：

> 当卷中使用的 ConfigMap 被更新时，**所投射的键最终也会被更新**。kubelet 组件会在每次周期性同步时检查所挂载的 ConfigMap 是否为最新。不过，kubelet 使用的是其本地的高速缓存来获得 ConfigMap 的当前值。……因此，从 ConfigMap 被更新的那一刻算起，到新的主键被投射到 Pod 中去，这一时间跨度**可能与 kubelet 的同步周期加上高速缓存的传播延迟相等**。

**官方对环境变量的原文（直接判死）**：

> **以环境变量方式使用的 ConfigMap 数据不会被自动更新。更新这些数据需要重新启动 Pod。**

**官方对 `subPath` 的原文（专门点名的坑）**：

> **使用 ConfigMap 作为 subPath 卷挂载的容器将不会收到 ConfigMap 的更新。**

> **一个必须说清的细节**：**"文件内容被更新了" ≠ "应用用上了新值"。**
> kubelet 会把新内容写进挂载目录的文件，**但你的应用如果只在启动时读一次配置文件，它永远不会重新读**。
> **所以第 ② 种方式的完整链路是：改 ConfigMap → kubelet 同步文件（延迟）→ 应用自己重读文件 → 应用生效。**
> **三个环节缺一不可。** Spring Boot 这类框架需要配合 `spring.config.import` + `/actuator/refresh` 或专门的 config watch 机制才做得到。

**第 ④ 种方式（应用直接调 API 读 ConfigMap）的独特价值**：官方原文——"由于你是直接使用 Kubernetes API，因此只要 ConfigMap 发生更改，**你的应用就能够通过订阅来获取更新，并且在这样的情况发生的时候做出反应**"。**这是唯一能做到"配置变了立刻感知"的方式**（通过 watch），代价是要写代码 + 需要 RBAC 授权。

### 2.7 Secret 的类型体系

官方原文：

> Secret 类型有助于对 Secret 数据进行编程处理。Kubernetes 提供若干种内置的类型，用于一些常见的使用场景。针对这些类型，**Kubernetes 所执行的合法性检查操作以及对其所实施的限制各不相同**。

| 类型 | 必需键 | 用途 | 校验严格度 |
|---|---|---|---|
| **`Opaque`**（默认） | 无 | **用户定义的任意数据** | 无 |
| `kubernetes.io/tls` | `tls.crt` + `tls.key` | **TLS 证书与私钥**（Ingress 用它） | **API Server 验证键存在** |
| `kubernetes.io/dockerconfigjson` | `.dockerconfigjson` | **私有镜像仓库凭据**（imagePullSecrets） | **API Server 检查是合法 JSON** |
| `kubernetes.io/dockercfg` | `.dockercfg` | 老式 Docker 配置（`~/.dockercfg`） | 同上 |
| `kubernetes.io/basic-auth` | `username` + `password` | 基本认证凭据 | **API Server 验证键存在** |
| `kubernetes.io/ssh-auth` | `ssh-privatekey` | SSH 认证凭据 | **API Server 验证键存在** |
| `kubernetes.io/service-account-token` | — | 服务账号令牌 | 控制器自动管理 |
| `bootstrap.kubernetes.io/token` | `token-id` + `token-secret` | 节点启动引导令牌 | **API Server 验证键存在** |

**官方关于自定义类型的原文**：

> 通过为 Secret 对象的 `type` 字段设置一个非空的字符串值，你也可以定义和使用自己 Secret 类型……Kubernetes 并不对类型的名称作任何限制。不过，如果你要使用内置类型之一，则你必须满足为该类型所定义的所有要求。

**自定义类型的命名约定（官方原文）**：

> 如果你要定义一种公开使用的 Secret 类型，请遵守 Secret 类型的约定和结构，**在类型名前面添加域名，并用 `/` 隔开**。例如：`cloud-hosting.example.net/cloud-api-credentials`。

**`data` vs `stringData`（这是必考细节）**：

| 字段 | 内容 | 谁做 base64 |
|---|---|---|
| `data` | **必须是 base64 编码的字符串** | 你自己编 |
| `stringData` | **任意字符串（明文）** | **API Server 写库时自动转 base64** |

官方原文：

> `stringData` 字段中的所有键值对都会在内部被合并到 `data` 字段中。如果某个主键同时出现在 `data` 和 `stringData` 字段中，**`stringData` 所指定的键值具有高优先级**。

> **`stringData` 的价值**：**它让你可以在 Git 里写明文的配置（虽然依然不该提交密码），而不用手工 base64。** 官方 Secret 文档里对 `stringData` 有一句评价："**Secret 的 `stringData` 字段不能很好地与服务器端应用配合使用**" —— 意思是很多服务端在合并/处理 Secret 时只认 `data`，用 `stringData` 创建的 Secret 可能在某些控制器链路上出问题。**生产上建议显式用 `data` 并让工具链负责 base64。**

### 2.8 base64 是编码不是加密（讲透）

**这是本篇必须讲透、也是全 K8s 生态最高频误解的一个点。**

**先看官方怎么说（这段是铁证）**：

> `auths` 值是 base64 编码的，其内容**被屏蔽但未被加密**。**任何能够读取该 Secret 的人都可以了解镜像库的访问令牌。**

> 值为 base64 编码，**这样会掩盖它们，但不会提供任何有用的机密性级别**。

**再确认一遍事实**：

```
Secret 对象里的内容（etcd 里也是）：
  data:
    password: cGFzc3dvcmQxMjM0        ← 这就是 base64

$ echo cGFzc3dvcmQxMjM0 | base64 -d
password123
```

**一条命令还原。任何拿到 Secret 读权限的人都能做到。**

**base64 到底是什么？**

| 问题 | 答案 |
|---|---|
| **它是什么？** | 一种**字符编码**（字符集 ↔ 二进制的映射规则），**设计目标是"能在纯文本环境里安全传输二进制数据"** |
| **它有密钥吗？** | **没有。** 编码表是公开的标准，谁都会解 |
| **它是加密吗？** | **不是。** 加密需要**密钥**，且没有密钥就无法还原 |
| **它能防什么？** | **几乎什么都防不了。** 只能防"眼睛不小心扫过去"这一种场景 |

**所以"Secret 是 base64 编码的"这句话的完整意思是**：

```
Secret = 一份"用 base64 装起来的明文"
Secret ≠ 一份"加密过的密文"
```

**那 K8s 官方为什么还提供 Secret？** 因为它解决的是**另一个维度**的问题：

| Secret 相比"直接写进 yaml"的价值 | 说明 |
|---|---|
| **① 与镜像解耦** | 同一个镜像跑 dev/prod，**配置和密钥不进镜像** |
| **② 与代码解耦** | 密钥不在代码仓库里，不进 Git 历史 |
| **③ 独立的访问控制** | **RBAC 可以只对 Secret 收权限**（`resourceNames` 精确到某个 Secret） |
| **④ 可被特定组件消费** | `imagePullSecrets`、`kubernetes.io/tls` 等类型有专门语义 |
| **⑤ 配合 etcd 静态加密** | **真正的保密性来自这一层，不来自 base64** |

> **官方给的四步安全建议（原文）**：
> 1. 为 Secret 启用静态加密
> 2. 以最小特权访问 Secret 并启用或配置 RBAC 规则
> 3. 限制 Secret 对特定容器的访问
> 4. 考虑使用外部 Secret 存储驱动
>
> **官方自己把"静态加密"排在第一位、"外部存储"排在第四位——base64 本身不在这个清单里。** 这就是官方对"base64 是不是加密"的表态。

**官方对"Secret 在 etcd 里是明文"的原文（这一段必须背）**：

> **默认情况下，Kubernetes Secret 未加密地存储在 API 服务器的底层数据存储（etcd）中。任何拥有 API 访问权限的人都可以检索或修改 Secret，任何有权访问 etcd 的人也可以。** 此外，**任何有权限在命名空间中创建 Pod 的人都可以使用该访问权限读取该命名空间中的任何 Secret**；这包括间接访问，例如创建 Deployment 的能力。

> **最后那句"能创建 Pod 就能读 Secret"是最容易被忽略的严重性**：**一个普通开发者只要有在自己 namespace 里建 Deployment 的权限，就能读到那个 namespace 里的所有 Secret。** 这也是第 7 节的 RBAC 那一条必须写细的原因。

### 2.9 正确的密钥治理路径

**按"防护强度"从低到高排成四层：**

```
第 1 层：base64 编码（默认，什么都没有）
    ↑ 至少做到"不写进 Git、不打进镜像"
第 2 层：RBAC 最小权限（谁能读哪个 Secret）
    ↑ 挡住"内部越权"
第 3 层：etcd 静态加密（EncryptionConfiguration + KMS）
    ↑ 挡住"拿到 etcd 快照的人"
第 4 层：外部密钥管理（Vault / 云 KMS + External Secrets Operator）
    ↑ 密钥根本不在集群里，集群里只有"投影"
```

#### 第 3 层：etcd 静态加密（EncryptionConfiguration）

**思路**：让 apiserver 在**写入 etcd 之前**把资源加密。

官方原文：

> 默认情况下，API Server 将资源的明文表示存储到 etcd 中，**没有静态加密**。

核心机制（官方原文）：

> `providers` 数组是一个**有序**的可能加密提供者列表。每个 provider 支持多个密钥——**密钥按顺序尝试解密**，如果该 provider 是列表中的第一个 provider，则用它的第一个密钥加密。

**配置骨架**：

```yaml
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
      - configmaps
    providers:
      - aescbc:                     # ← 第一个 provider = 写入时用
          keys:
          - name: key1
            secret: <base64 编码的 32 字节密钥>
      - identity: {}                # ← 兜底：还能读旧的明文数据（迁移用）
```

**关键设计（这是本节最值得讲的一点）**：

| 设计 | 含义 |
|---|---|
| **providers 是有序的** | **第一个 = 加密写入用**；后面的 = **解密读取时依次尝试** |
| **`identity` 放最后** | **迁移期兜底**：能同时读加密数据和历史明文数据。**迁移完成后可以把它去掉**（但去掉前必须重写所有数据） |
| **密钥可以配多个** | **轮换密钥**：新密钥放第一个，老密钥留在后面 → **写入用新的，读取时新旧都能解** |
| **密钥本身是 base64** | 讽刺但真实：**加密 etcd 的密钥在配置文件里也是 base64 的**——所以那个配置文件权限必须严格收紧 |

**挂载方式**：`kube-apiserver --encryption-provider-config=/etc/kubernetes/enc/enc.yaml`，配置文件只读挂载到静态 Pod 清单里，然后重启 apiserver。

**判断是否真的生效（官方给了明确判据）**：

> 如果你运行 `kube-apiserver` 时带上了 `--encryption-provider-config` 参数，且它引用的文件把 `identity` provider 列在 providers 列表的**第一位**，**那么你没有启用静态加密**。

> **注意**：这个检查**不能告诉你之前的迁移是否成功**——历史数据可能还是明文，需要额外重写。

**通配符（v1.27 起支持）**：

| 通配符 | 含义 |
|---|---|
| `*.apps` | 加密 `apps` API 组的所有资源 |
| `*.` | 加密 core 组的所有资源 |
| `*.*` | 加密所有资源，**包括 apiserver 启动后新增的自定义资源** |
| 具体资源名（放在通配符**之前**） | **豁免**该资源不加密 |

**KMS provider（v1.29 起稳定）—— 这是生产环境的正解**：

官方原文：

> `kms` v2 使用**信封加密**方案，每个 apiserver 有一个 DEK。数据由数据加密密钥（DEK）使用 AES-GCM 加密；DEK 再由 KMS 中的密钥加密密钥（KEK）按配置加密。**Kubernetes 每次加密都从 secret seed 派生一个新的 DEK，KEK 轮换时 seed 也会轮换。**

**信封加密画出来**：

```
你的 Secret 明文
      │
      ▼  用 DEK（AES-GCM）加密
   密文  ─────────────────┐
      │                   │
      │  DEK 用 KEK 加密    │
      ▼                   ▼
  加密的 DEK  ────────►  etcd 里存：{ 密文, 加密后的 DEK }
                            ▲
                            │  读取时：apiserver 调 KMS，用 KEK 解开 DEK
                            └──────  KEK 存在 KMS 里，不在集群里
```

官方原文（这是 KMS 最大的价值）：

> 使用信封加密会产生对密钥加密密钥（KEK）的依赖，**而 KEK 不存储在 Kubernetes 中**。在 KMS 的情况下，一个想要获取未授权明文的攻击者**需要同时攻破 etcd 和第三方 KMS 提供者**。

> **这就是第 3 层和第 4 层的分界**：静态加密用本地密钥，**密钥本身要放在控制平面主机上**（虽然权限收紧）；KMS provider 把 KEK 放进外部 KMS，**集群里一个密钥都没有**。

**⚠️ 单源待补**：`EncryptionConfiguration` 的**完整任务页**（含 `EncryptionConfiguration` 字段逐条说明、`kms` provider 的 v2 配置示例、密钥轮换步骤）在 09-参考索引中**没有对应条目**。本节的机制性描述以官方 Secret 概念页（S1）的"静态加密 / KMS / 外部 Secret 存储驱动"要求为依据，**知识点已标 `⚠️ 单源待补`**。

#### 第 4 层：外部密钥管理（External Secrets Operator 模式）

**它解决什么问题**（官方 Secret 页的四条建议里的第 4 条："考虑使用外部 Secret 存储驱动"）：

| 问题 | 集群内原生 Secret 的困境 | 外部密钥管理的解法 |
|---|---|---|
| **密钥入库那一刻就被复制了** | 即使开了静态加密，**明文仍在 apiserver 内存和 kubelet 内存里出现过** | 集群里**从来没有**真正的密钥，只有"投影" |
| **轮转要改集群** | 改 Secret → 要重启/触发 reload → **每个环境的集群都要动** | **外部系统轮转 → Operator 同步 → 集群自动更新** |
| **审计在集群里** | 只有 apiserver 的审计日志，**看不到"谁在什么业务场景用了这个密钥"** | **审计在 KMS 侧**，能按业务维度查 |
| **Git 里放什么** | 只能放 base64（一样是泄露） | **Git 里只放"从哪里取"的声明**（引用外部路径、密钥名），**仓库里一个密钥值都没有** |

**工作模式（Operator + CRD，官方 Operator/CRD 概念页的通用模式）**：

```
① 外部密钥系统（Vault / 阿里云 KMS / AWS Secrets Manager）
      │  保存真正的密钥
      ▼
② External Secrets Operator（集群里的一个控制器）
      │  watch 自定义资源
      ▼
③ ExternalSecret（CRD，只声明"取什么、放哪"）
      │  Operator 读取外部系统 → 写入目标
      ▼
④ K8s Secret（普通 Secret 对象）
      │  被 Deployment 正常消费
      ▼
⑤ Pod 挂载 / 环境变量
```

**这个模式的关键性质**（也是它被称为 Operator 模式的原因）：

- **声明式**：ExternalSecret 是"期望态"，Operator 是"协调器"——**和 L2 的三态模型完全同构**
- **Git 里只有引用，没有密钥**：**GitOps 友好**——仓库可以被扫描、可被审计、密钥不泄露
- **轮转自动化**：外部系统改密钥 → Operator 同步 → 依赖它的 Pod 重启/热加载（**这一段依赖应用是否支持热加载**）
- **⚠️ 它没有消除风险，只是把风险挪了位置**：**K8s Secret 该被读还是会被读**。**Operator 本身在集群里，它有权限去读外部密钥并写 Secret** —— **它的 ServiceAccount 权限就是新的关键攻击面**

> **面试这么答（这条能体现分层思维）**："base64 只是编码，我可以在任何一台装了 base64 的机器上还原。所以 Secret 的保密性**不来自 Secret 这个对象**，来自三层：**① RBAC 控制谁能读；② etcd 静态加密（EncryptionConfiguration，KMS provider 用信封加密，KEK 存在集群外，攻击者要同时拿到 etcd 和 KMS）；③ 外部密钥管理（External Secrets Operator + Vault/KMS），集群里只有投影，连真密钥都不落地。** 另外要注意官方那句提醒——**任何能在 namespace 里创建 Pod 的人都能读那个 namespace 的所有 Secret**，所以 RBAC 的粒度设计比'用不用 Secret'更重要。"

### 2.10 关键参数与默认值表

| 参数 | 默认 | 含义 | 实务要点 |
|---|---|---|---|
| `Ingress.pathType` | **必填**（不填无法通过校验） | `Exact`/`Prefix`/`ImplementationSpecific` | **统一用 `Prefix`**；`ImplementationSpecific` 跨 Controller 不兼容 |
| `Ingress.ingressClassName` | 无（则用默认 IngressClass） | 选哪个 Controller | **不写可能落到错误的 Controller 上** |
| `Ingress.rules[].host` | 无 = 匹配所有 | Host 匹配 | 通配符**只覆盖最左一个 DNS 标签** |
| `Ingress.tls[].hosts` | — | 要启用 TLS 的 host | **必须与 `rules[].host` 完全匹配** |
| `Ingress.tls[].secretName` | — | 证书 Secret（`kubernetes.io/tls`） | **必须含 `tls.crt` + `tls.key`** |
| Ingress TLS 端口 | **只支持 443** | HTTPS 入口 | 后端是明文 HTTP |
| `ConfigMap.data` | 无 | UTF-8 字符串键值对 | **总大小 ≤ 1 MiB** |
| `ConfigMap.binaryData` | 无 | base64 的二进制 | **键名不能与 `data` 重叠** |
| `ConfigMap.immutable` | false | 不可变（v1.19+） | 一旦 true **不可逆**，只能删了重建 |
| `Secret.type` | **Opaque** | 类型 | 内置类型有**键名与格式校验** |
| `Secret.data` | 无 | **base64 字符串** | **不是加密** |
| `Secret.stringData` | 无 | 明文（API Server 转 base64） | 与 `data` 同键时**优先** |
| Secret 大小上限 | **1 MiB** | 尺寸限制 | 大文件用卷/对象存储，**别塞 Secret** |
| ConfigMap 大小上限 | **1 MiB** | 尺寸限制 | 同上 |
| 卷挂载的更新延迟 | kubelet 同步周期 + 缓存传播延迟 | 自动更新的延迟 | **应用还要自己重读** |
| `subPath` 挂载 | **不更新** | — | **别用它做热更新** |

---

## 3. 动手

> 本节命令在 kind 集群（`day2`）上执行。**目的是亲手观察，不是让你改生产。**

### 3.1 装一个 Ingress Controller

**kind 默认不带 Ingress Controller，必须自己装。**

```bash
# ① 官方安装清单（社区 ingress-nginx）
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/kind/deploy.yaml

# ② 等它跑起来（关键：必须等 Pod Running 且 Controller 有 ADDRESS）
kubectl -n ingress-nginx get pods -w
kubectl -n ingress-nginx get svc
```

**看到什么算对**：

| 检查 | 命令 | 期望 |
|---|---|---|
| Controller Pod 在跑 | `kubectl -n ingress-nginx get pods` | `Running` |
| Controller 有入口 | `kubectl -n ingress-nginx get svc ingress-nginx-controller` | EXTERNAL-IP 有值（kind 里是 NodePort 或 localhost） |
| Controller 能看到 Ingress | `kubectl -n ingress-nginx logs deploy/ingress-nginx-controller` | 日志里有 watch/sync 相关输出 |

> **本地 kind 常见的一个坑**：Controller 的 Service 是 NodePort 类型，端口 80/443 映射到宿主机。**先确认端口映射对不对，再去访问。**

### 3.2 写一份 Ingress 并验证

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: day2-web
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /$2   # ← 社区注解，非官方 API
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - web.day2.local
    secretName: day2-web-tls
  rules:
  - host: web.day2.local
    http:
      paths:
      - path: /app
        pathType: Prefix
        backend:
          service:
            name: day2-web
            port:
              number: 80
```

```bash
# ① 证书（本地自签，用 openssl 生成）
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout tls.key -out tls.crt -subj "/CN=web.day2.local" \
  -addext "subjectAltName=DNS:web.day2.local"
kubectl create secret tls day2-web-tls --cert=tls.crt --key=tls.key

# ② 应用
kubectl apply -f ingress.yaml

# ③ 看状态
kubectl get ingress day2-web
kubectl describe ingress day2-web
```

**看到什么算对**：

| 检查 | 命令 | 期望 |
|---|---|---|
| **Controller 接管了** | `kubectl get ingress day2-web` | **ADDRESS 列有值** |
| 规则被识别 | `kubectl describe ingress day2-web` | Rules 段列出 host 和 path 和 backend |
| TLS 被识别 | 同上 | TLS 段列出 `web.day2.local` 和 secret 名 |
| Controller 日志无报错 | `kubectl -n ingress-nginx logs deploy/ingress-nginx-controller --tail=50` | 无 `error` |

> **ADDRESS 为空 = Controller 没接管** → 排查 `ingressClassName` 和 Controller 是否在跑。**这是最常见的第一故障点。**

### 3.3 ConfigMap 的三种注入实测

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: day2-cfg
data:
  APP_MODE: "prod"
  app.properties: |
    server.port=8080
    app.name=day2-web
```

**方式 ①：环境变量**

```yaml
        env:
        - name: APP_MODE
          valueFrom:
            configMapKeyRef:
              name: day2-cfg
              key: APP_MODE
```

**方式 ②：整个 ConfigMap 挂成目录**

```yaml
        volumeMounts:
        - name: cfg
          mountPath: /etc/cfg          # ← 目录
          readOnly: true
      volumes:
      - name: cfg
        configMap:
          name: day2-cfg
```

**方式 ③：subPath 挂单个文件**

```yaml
        volumeMounts:
        - name: cfg
          mountPath: /etc/app.properties   # ← 文件
          subPath: app.properties          # ← ⚠️ 不会自动更新
          readOnly: true
      volumes:
      - name: cfg
        configMap:
          name: day2-cfg
```

```bash
kubectl apply -f cfg-demo.yaml

# ① 容器内确认注入成功
kubectl exec -it <pod> -- printenv APP_MODE
kubectl exec -it <pod> -- ls -la /etc/cfg
kubectl exec -it <pod> -- cat /etc/cfg/app.properties
```

**热更新实测（这是本节的重点）**：

```bash
# ② 改 ConfigMap
kubectl patch configmap day2-cfg --type merge -p '{"data":{"APP_MODE":"staging"}}'

# ③ 等 1~2 分钟（kubelet 同步周期 + 缓存延迟），然后看目录里的文件
kubectl exec -it <pod> -- cat /etc/cfg/app.properties
# 期望：内容已更新（方式 ②）

# ④ 但环境变量不会变
kubectl exec -it <pod> -- printenv APP_MODE
# 期望：仍然是 prod（方式 ① 不热更新）

# ⑤ 方式 ③ 的文件也不会变
kubectl exec -it <pod> -- cat /etc/app.properties
# 期望：仍然是旧值（subPath 不更新）
```

> **这次实测一次性证明了三个结论**：
> **① 目录挂载会更新（但有延迟）；② 环境变量不更新；③ subPath 不更新。**
> **④ 而且即便文件更新了，应用不重读也不会生效。**

### 3.4 Secret 的创建与"还原"演示

```bash
# ① 创建（推荐用 --from-literal，API Server 自动转 base64）
kubectl create secret generic day2-db \
  --from-literal=username=app \
  --from-literal=password='S3cr3t!'

# ② 看一眼（data 里是 base64）
kubectl get secret day2-db -o jsonpath='{.data.password}'; echo

# ③ 还原（这一条就是"base64 不是加密"的全部证据）
kubectl get secret day2-db -o jsonpath='{.data.password}' | base64 -d; echo
# 期望输出：S3cr3t!

# ④ stringData 写法（明文写入）
kubectl create secret generic day2-db2 \
  --from-literal=password='S3cr3t!'
kubectl get secret day2-db2 -o yaml | grep -A2 'data:'
# 期望：显示的仍然是 base64（API Server 自动转的）
```

> **第 ③ 步请务必亲手敲一次。** **三秒钟的实验，胜过记住十遍"base64 不是加密"。**

**验证"Secret 在 etcd 里默认是明文"的思路**（⚠️ 只读、不改动环境）：

```bash
# 用 etcdctl 读某个 key（需要 etcd 证书，⚠️ 生产环境慎用，只读）
# 观察到的应该是 base64 字符串，而不是密文
```

### 3.5 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| Controller 在跑 | `kubectl -n ingress-nginx get pods` | Running |
| Ingress 被接管 | `kubectl get ingress day2-web` | **ADDRESS 有值** |
| 规则被识别 | `kubectl describe ingress day2-web` | Rules / TLS 段正确 |
| Service 后端存在 | `kubectl get endpoints day2-web` | 有 IP:PORT |
| TLS Secret 结构对 | `kubectl get secret day2-web-tls -o jsonpath='{.type}'` | `kubernetes.io/tls` |
| TLS 键齐全 | `kubectl get secret day2-web-tls -o jsonpath='{.data.tls\.crt}'` | 非空 |
| ConfigMap 注入成功 | 容器内 `printenv APP_MODE` / `ls /etc/cfg` | 有值 |
| Secret 注入成功 | 容器内 `printenv DB_PASSWORD` | 有值 |
| **知道 base64 能还原** | `kubectl get secret X -o jsonpath='{.data.k}'` 后用 base64 解码 | **明文** |

---

## 4. 资源清单逐字段

### 4.1 ConfigMap

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: v1` | string | ✅ | API 组与版本 | 写 `apps/v1` → no matches for kind |
| `kind: ConfigMap` | string | ✅ | 对象类型 | 拼错 → apply 报错 |
| `metadata.name` | string | ✅ | 名称，**必须是合法 DNS 子域名** | 含大写/下划线 → apply 报错 |
| `metadata.namespace` | string | | 命名空间 | **Pod 和 ConfigMap 必须同 ns** |
| `data` | map[string]string | | UTF-8 字符串键值对 | **总大小 > 1 MiB → apply 报错** |
| `binaryData` | map[string][]byte | | **base64 编码的**二进制数据 | **键名与 `data` 重叠 → apply 报错** |
| `immutable` | bool | | **v1.19+**；设 true 后不可改 | **一旦 true 不可逆**，改内容只能删了重建；**已挂载的 Pod 建议重建** |
| `metadata.annotations` | map | | 控制器注解 | 键写错 → 不报错，不生效 |

**命名与引用约束（官方原文）**：

| 约束 | 说明 |
|---|---|
| **名字必须是合法 DNS 子域名** | 不能有大写字母 |
| **`data` / `binaryData` 的键名** | **只能包含字母、数字、`-`、`_`、`.`** |
| **两者的键名不能重叠** | 官方原文："在 `data` 下保存的键名不可以与在 `binaryData` 下出现的键名有重叠" |
| **Pod 与 ConfigMap 必须同命名空间** | 官方原文："这个 Pod 和 ConfigMap 必须要在同一个名字空间里" |
| **静态 Pod 不能引用 ConfigMap** | 官方原文明确说明 |

### 4.2 Secret

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: v1` | string | ✅ | API 组与版本 | — |
| `kind: Secret` | string | ✅ | 对象类型 | — |
| `metadata.name` | string | ✅ | 名称，**合法 DNS 子域名** | — |
| `metadata.namespace` | string | | 命名空间 | 同 ns 限制 |
| `type` | string | | **默认 Opaque**；可填自定义类型 | **内置类型缺必需键 → apply 报错**；自定义类型名应带域名前缀 |
| `data` | map[string]string | | **必须 base64 编码** | **写成明文 → 存的还是明文字符串的 base64，解出来是乱码**；**⚠️ 以为它加密了 → 直接泄露** |
| `stringData` | map[string]string | | 明文（**API Server 自动转 base64**） | 与 `data` 同键时**优先**；**部分服务端组件只认 `data`** |
| `immutable` | bool | | 不可变 | 同 ConfigMap，**不可逆** |
| `metadata.annotations` | map | | 注解 | — |

**按类型的必需键对照（apply 会校验）**：

| `type` | 必需键 | 写错的后果 |
|---|---|---|
| `Opaque` | 无 | — |
| `kubernetes.io/tls` | `tls.crt` + `tls.key` | **缺键 → apply 报错**（Ingress 会因证书不完整而无法终结 TLS） |
| `kubernetes.io/dockerconfigjson` | `.dockerconfigjson`（**合法 JSON**） | **不是合法 JSON → apply 报错**；**非法 Docker 配置内容则不报错**（官方明确说明 API Server 不检查内容） |
| `kubernetes.io/basic-auth` | `username` + `password` | 缺键 → apply 报错 |
| `kubernetes.io/ssh-auth` | `ssh-privatekey` | 缺键 → apply 报错 |
| `bootstrap.kubernetes.io/token` | `token-id` + `token-secret` | 缺键 → apply 报错 |

**尺寸限制（官方原文）**：

> **每个 Secret 的尺寸最多为 1 MiB。**

> ConfigMap 在设计上不是用来保存大量数据的。**在 ConfigMap 中保存的数据不可超过 1 MiB。**

---

## 5. 横向对比

### 5.1 ConfigMap vs Secret vs 外部密钥管理

**这张表是本篇的核心产出。三者的差别是"保密性强度"，不是"功能"。**

| 维度 | **ConfigMap** | **Secret**（原生） | **外部密钥管理**（External Secrets + Vault/KMS） |
|---|---|---|---|
| **本质** | 明文键值对 | **base64 的明文键值对** | **集群里只有"投影"，真密钥在外部** |
| **保密性来源** | **无** | **RBAC + etcd 静态加密**（不是 base64） | **外部系统的访问控制 + KMS 加密** |
| **etcd 里存的是什么** | 明文 | **静态加密开启后是密文；未开启是 base64** | **Secret 对象（可再叠加静态加密）** |
| **谁能读** | 任何能读该 ns 配置的人 | **任何能在该 ns 创建 Pod 的人**（官方原文） | Operator 的 SA + 读 Secret 的人 |
| **Git 里能放吗** | ✅ 可以 | **❌ 绝对不行**（base64 一还原就泄露，且 Git 历史删不掉） | ✅ **只放"从哪取"的声明** |
| **轮转密钥** | 改文件 → 重新 apply | 改 Secret → **重启/触发 reload** | **外部轮转 → Operator 同步 → 自动更新** |
| **审计能力** | 无 | apiserver 审计日志（只能看到"谁读了这个 Secret"） | **KMS 侧审计：谁在什么业务场景取了密钥** |
| **合规（等保）友好度** | ✅ 非敏感配置 | ⚠️ **需叠加静态加密 + 细粒度 RBAC** | ✅✅ 最佳（密钥不落地、可审计） |
| **运维成本** | 最低 | 低 | **高**：要部署 Operator、配置 SecretStore、对接 KMS |
| **典型场景** | 应用配置、日志级别、功能开关 | **TLS 证书**、镜像仓库凭据、**低敏感度的连接串** | **数据库密码**、**第三方 API Key**、**支付网关密钥** |

> **选型建议（按敏感度分档）**：

| 数据类型 | 放哪 | 理由 |
|---|---|---|
| **公开配置**（端口、日志级别、开关） | **ConfigMap** | 没有保密需求，别过度设计 |
| **TLS 证书**（公开部分 + 私钥） | **Secret + 静态加密** | 私钥必须受控；但证书本身是公开的 |
| **镜像仓库凭据** | **Secret**（`kubernetes.io/dockerconfigjson`）+ `imagePullSecrets` | 范围小、生命周期短 |
| **数据库密码 / 第三方 API Key** | **外部密钥管理** | **这是等保和审计的重点** |

> **重要提醒**：**"Secret 更安全"是个错觉。** 原生 Secret 的保密性**完全依赖你有没有配静态加密和 RBAC**。**一个没开静态加密的集群里，Secret 和 ConfigMap 的安全性差别只有"多了一层 base64"** —— 而 base64 连一层防护都不算。

### 5.2 env 注入 vs volume 挂载

| 维度 | **env / envFrom 注入** | **volume 挂载（目录）** | **volume 挂载（subPath 单文件）** |
|---|---|---|---|
| **应用怎么读** | `System.getenv("X")` | 读 `/etc/cfg/app.properties` | 读 `/etc/app.properties` |
| **改 ConfigMap 后自动更新** | **❌ 不会** | **✅ 会**（kubelet 同步 + 缓存延迟） | **❌ 不会** |
| **要重启 Pod 吗** | **✅ 必须** | 文件层面不需要；**应用重读才行** | 必须 |
| **值的形态** | **字符串**（数字/布尔要自己转） | **原样保留文件格式** | 原样 |
| **适合什么** | 少量开关、连接串 | **整个配置文件**（application.yml / nginx.conf） | 单个文件且确定不需要更新 |
| **大配置** | ❌ 环境变量有长度限制 | ✅ | ✅ |
| **多容器共享** | ❌ 每个容器要各写一遍 | ✅ **同一个卷可挂给多个容器** | ✅ |
| **排障可见性** | `printenv` 一眼看到 | `ls` + `cat` | `cat` |
| **安全** | ⚠️ **环境变量能被 `printenv`/`/proc` 看到，容易进日志** | ✅ 文件可设只读 + 权限 | ✅ |

**选型口诀**：

> **"配置是一份文件" → volume 挂载目录；"配置是几个开关" → env。**
> **"需要热更新" → volume 目录挂载 + 应用支持重读；否则 env 最省事。**

> **一条实战经验**：**数据库密码这类又要热更新、又不想进环境变量的场景，最佳解是"外部密钥管理 → Secret → volume 挂载"**，让应用从文件读并支持重载。这样既不落环境变量（不进日志），又不用每次改配置都重启 Pod。

---

## 6. 踩坑与排障（症状 → 状态 → 原因）

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **访问 Ingress 域名 404，但 Pod 一切正常** | `kubectl get ingress` **ADDRESS 为空** | **① 集群里没有 Ingress Controller**，或 `ingressClassName` 写的 Controller 不存在 → **只创建 Ingress 资源没有任何效果** | 装 Controller；确认 `ingressClassName` 与 IngressClass 的 `controller` 匹配 | **部署后必查 `kubectl get ingress` 的 ADDRESS 列** |
| 同上 | ADDRESS 有值，404 | **② `pathType` 与实际路径不兼容**（如 Prefix `/api` 匹配不到 `/apiv2`；Exact `/foo` 匹配不到 `/foo/`） | 改 `pathType`；把路径改成按 `/` 分段的完整前缀 | **统一用 `Prefix`**；路径写完整（`/api` 而非 `/api/v1` 当你想匹配整个 `/api` 时要写对） |
| 同上 | ADDRESS 有值，404 | **③ 注解写错 / Controller 不认**（如在 ALB Ingress 上写 `nginx.ingress.kubernetes.io/*`） | 查**所用 Controller 的官方文档**确认注解名 | 注解收敛到一处模板管理；换 Controller 必回归 |
| 同上 | ADDRESS 有值，404 | **④ TLS 配置错**：`tls.hosts` 与 `rules[].host` 不完全一致；Secret 缺 `tls.crt`/`tls.key`；类型不是 `kubernetes.io/tls` | 逐条核对 hosts、Secret 键名与类型 | TLS Secret 用 `kubectl create secret tls` 生成 |
| 同上 | ADDRESS 有值，**502** | **后端 Service 不存在 / 没有就绪端点** | `kubectl get endpoints <svc>`；`kubectl describe ingress` 看 backend | Ingress 后端一定指向 Service，**不是 Pod** |
| 同上 | **HTTPS 报证书错误** | 证书 CN/SAN 与访问的域名不匹配；或 `tls.hosts` 没覆盖这个 host | 换证书或补 SAN；核对 hosts | 证书用通配符或完整 SAN 列表 |
| 同上 | **HTTP 正常但 HTTPS 404** | Ingress 里只配了 `tls` 没配对应 `rules[].host`（host 不完全匹配） | 让 `tls.hosts` 与 `rules[].host` 严格一致 | 两者成对维护 |
| **改了 ConfigMap，Pod 里还是旧值** | Pod `Running` 正常 | **用了环境变量注入 → 环境变量不会自动更新，必须重启 Pod** | `kubectl rollout restart deployment/<name>` | 配置更新和发布流程配套；或改用 volume 挂载 |
| 同上 | 文件没变 | **用了 `subPath` 挂载 → 不会收到更新** | 改成整目录挂载（去掉 `subPath`） | **subPath 只用于"确定永远不需要更新"的单文件** |
| 同上 | 文件变了但应用行为没变 | **kubelet 同步了文件，但应用不重读** | 应用开启配置热加载（如 Spring Cloud 的 refresh 机制） | 应用侧提前确认"能不能热读配置" |
| 同上 | 延迟很长 | kubelet 同步周期 + **本地缓存传播延迟** | 容忍延迟或走第四种方式（应用 watch API） | 变更后**留出缓冲时间再验证** |
| **以为 Secret 是加密的，把 base64 当安全** | — | **base64 是编码不是加密，一条 `base64 -d` 就还原** | 启用 etcd 静态加密 + 细粒度 RBAC；敏感密钥走外部密钥管理 | **CI 加扫描规则：禁止 `data` 里出现 base64 后的已知弱口令**；**禁止把 Secret 提交 Git** |
| **Secret 提交进了 Git** | — | base64 不是加密，**且 Git 历史无法彻底删除** | **立刻轮换该密钥**（这是唯一正确的补救）；清理历史 + 通知相关方 | **CI 挂 pre-commit 密钥扫描**（如 gitleaks 类工具）；密钥一律走外部密钥管理 |
| **改了 Secret，Pod 里还是旧值** | Pod 正常 | 同 ConfigMap 的环境变量问题 | 重启 Pod | 同上 |
| **Templating 报"缺少 tls.crt"** | — | Secret 类型不是 `kubernetes.io/tls`，或键名写错（`tls.crt` vs `cert`） | 用 `kubectl create secret tls` 重新生成 | 键名严格用官方规定的 `tls.crt` / `tls.key` |
| **Secret apply 报缺必需键** | — | 内置类型有校验：`kubernetes.io/tls` 要 `tls.crt`+`tls.key`，`dockerconfigjson` 要 `.dockerconfigjson` | 补齐键 | 建 Secret 前先查类型要求 |
| **ConfigMap apply 报大小超限** | — | **超过 1 MiB** | 拆分成多个 ConfigMap；大文件用卷/对象存储 | 配置别往 ConfigMap 里塞 |
| **`kubectl get secret` 输出一堆 base64 没人看懂** | — | 正常，**这就是它的存储格式** | `base64 -d` 还原 | 记住它不是加密 |
| **任何人都能看到别人的 Secret** | — | **官方明确：任何有权限在命名空间中创建 Pod 的人都能读取该命名空间中的任何 Secret** | 用 RBAC 精确到 `resourceNames`；限制开发者建 Pod 的权限 | **Secret 的 RBAC 要按 namespace 逐个设计** |
| **etcd 备份泄露 = 全部 Secret 泄露** | — | **没开静态加密 → etcd 里就是 base64** | 启用 EncryptionConfiguration；KMS provider 更佳 | **etcd 备份按敏感数据管理** |
| **ConfigMap 里的敏感信息被同 namespace 的人看到** | — | ConfigMap **本来就不保密**，你放错了地方 | 移到 Secret（或外部密钥管理） | **做一次"配置分级"评审** |
| **`immutable: true` 后改不动** | apply 报错 | **不可逆，只能删了重建** | 删了重建；已挂载的 Pod 建议重建 | 设置 immutable 前确认内容已定稿 |

**Ingress 不生效的四步排查（背下来）**：

```
① kubectl get ingress <name>
   → ADDRESS 为空？
     · 空 → 【第一层/第二层】没 Controller 或 ingressClassName 写错
            → kubectl get ingressclass 核对 controller 字段
            → kubectl -n <ns> get pods 看 Controller 在不在
   → ADDRESS 有值
        │
② kubectl describe ingress <name>
   → Rules 段的 host / path / backend 对不对？
     · host 不对 → 客户端 Host 头没匹配（检查访问用的域名）
     · path 不对 → 【pathType 不兼容】改 Prefix 或调整路径
     · backend 不对 → 确认 Service 名字和端口号
        │
③ kubectl get endpoints <backend-service>
   → 空？→ Service 没后端（selector 没匹配 / Pod 没 Ready）
            → 这时 502 而不是 404
        │
④ TLS 段 + Secret
   → kubectl get secret <tls-secret> -o yaml
   → 类型对不对？tls.crt / tls.key 齐不齐？
   → tls.hosts 与 rules[].host 是否完全一致？
```

> **第 ① 步就能分开"没 controller"和"路由不对"两大类，这是最高效的分叉点。**

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/configuration/secret/` | Secret 的默认未加密状态与四条安全建议、内置类型表、各类型必需键、`data`/`stringData` 合并规则、尺寸限制 1 MiB、immutable、base64"被屏蔽但未被加密"原文 | ✅ 2026-10-24 |
| S2 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/configuration/configmap/` | ConfigMap 定义与动机、四种消费方式、卷挂载自动更新的同步周期与缓存延迟、环境变量不自动更新、subPath 不更新、immutable、1 MiB 限制、键名约束 | ✅ 2026-10-24 |
| S3 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/services-networking/ingress/` | Ingress 定义与"必须有 Ingress Controller"、pathType 三种取值与完整匹配表、多重匹配优先级、host 通配符、TLS 终结位置与 `tls.crt`/`tls.key` 要求、IngressClass 与默认类、`kubernetes.io/ingress.class` 废弃注解 | ✅ 2026-10-24 |
| S4 | 官方 | `kubernetes.io/docs/reference/using-api/` | ConfigMap / Secret / Ingress v1 API 字段语义交叉验证 | ✅ 2026-10-24 |
| S5 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/architecture/` | 集群 DNS 与插件组件、Ingress/Controller 在架构中的位置 | ✅ 2026-10-24 |
| S6 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/extend-kubernetes/operator/` | Operator 模式（External Secrets 的实现范式） | ✅ 2026-10-24 |
| S7 | 官方（中文） | `kubernetes.io/zh-cn/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/` | CRD（ExternalSecret 属于自定义资源） | ✅ 2026-10-24 |
| S8 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/security/rbac-good-practices/` | Secret 的最小权限访问控制 | ✅ 2026-10-24 |
| S9 | 官方（中文） | `kubernetes.io/zh-cn/docs/tasks/manage-kubernetes-objects/declarative-config/` | 声明式管理、字段属主（配置变更走 apply 而非手改） | ✅ 2026-10-24 |
| S10 | 官方（中文） | `kubernetes.io/zh-cn/docs/reference/kubectl/quick-reference/` | `kubectl create secret`、`kubectl get -o jsonpath`、`kubectl exec`、`kubectl rollout restart` | ✅ 2026-10-24 |
| S11 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/services-networking/service/` | Ingress 后端指向 Service 的关系（backend.service.name/port） | ✅ 2026-10-24 |
| S12 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 的 ALB Ingress Controller 与 KMS 集成实践差异 | ✅ 2026-10-24 |
| S13 | 云厂商 | `cloud.tencent.com/document/product/457` | TKE 上 Ingress Controller 与云 KMS 的实践形态 | ✅ 2026-10-24 |

> **本篇没有引用任何 09-参考索引之外的 URL。** 所有具体章节均在已验证 URL 后标注章节名。
> **⚠️ 单源待补（3 处，均为索引缺项）**：
> 1. **etcd 静态加密的完整任务页**（`EncryptionConfiguration` 逐字段说明、`kms` v2 provider 配置示例、密钥轮换与数据重写步骤、通配符 `*.*` 的豁免顺序）——09-参考索引中没有对应条目。本节以 S1（Secret 页的"静态加密 / KMS / 外部存储驱动"要求）为依据。**建议把该官方页补进索引。**
> 2. **External Secrets Operator 的官方文档**（CRD 字段 `secretStoreRef` / `target` / `data` 的精确语义、SecretStore 与 ClusterSecretStore 的区别）——属第三方项目文档，不在索引内。本篇按官方 Operator/CRD 通用模式（S6/S7）描述机制，**具体字段名未逐一引用**。
> 3. **Ingress Controller 各实现的注解支持矩阵**（`nginx.ingress.kubernetes.io/*` 与 `alb.ingress.kubernetes.io/*` 的完整清单）——按规范**明确标注为社区/厂商约定而非 K8s 官方 API**，具体支持情况以所用 Controller 版本的官方文档为准。
> **⚠️ 待验证**：`Ingress` 在不同 Controller 下 `Exact` / `Prefix` 的**尾斜线与大小写处理细节**存在实现差异，官方概念页列出了参考匹配表但注明各实现可能不同。**本篇的匹配表以官方参考表为准，跨 Controller 迁移前需逐条回归。**

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入：一份 Ingress 清单（`ingressClassName` / `rules[].host` / `paths[].path` / `pathType` / `backend` / `tls`）。输出：**一份路由规则表躺在 etcd 里**——**注意：这一步还没有任何流量被转发**。真正生效要靠 Ingress Controller watch 到变化 → 翻译成代理配置 → reload → 由 Controller 自己的 Service 对外暴露。**"Ingress 对象存在"和"域名能访问"是两件事。**
2. **能故意改坏再倒推吗？** —— 四个可执行的破坏实验：
   - **实验 A（没有 Controller → 404）**：先 `kubectl delete ingress` 再删掉 namespace 里的 Controller（或用 `ingressClassName` 指向一个不存在的类），然后 apply 一份 Ingress。**现象：`kubectl get ingress` 的 ADDRESS 为空，访问域名 404，但后端 Pod 一切正常、`kubectl get endpoints` 有内容。** 倒推原因：**只创建 Ingress 资源本身没有任何效果。**
   - **实验 B（pathType 不兼容 → 404）**：把 `path: /api, pathType: Prefix` 改成 `path: /api, pathType: Exact`，然后访问 `/api/health`。**现象：404（走默认后端）。** 倒推原因：**`Exact` 要求完全相等**，多一段就不匹配。再改成 `Prefix` 并访问 `/apiv2/x` → **依然 404**——**因为 Prefix 是按 `/` 分段匹配，不是字符串前缀**。**这一实验能让你彻底分清"分段前缀"和"字符串前缀"。**
   - **实验 C（base64 还原）**：`kubectl create secret generic demo --from-literal=password='S3cr3t!'` → `kubectl get secret demo -o jsonpath='{.data.password}' | base64 -d`。**现象：明文 `S3cr3t!`。** 倒推原因：**base64 是公开编码规则，没有密钥，不是加密。** 这一条是本篇最该亲手做的实验。
   - **实验 D（env 不热更新 / volume 会更新）**：按 3.3 节的三种注入方式建一个 Pod，`kubectl patch configmap` 改值，等 1~2 分钟后分别 `printenv` 和 `cat` 挂载目录。**现象：环境变量不变，目录里的文件变了。** 再用 `subPath` 挂单文件，**现象：文件也不变。** 倒推原因：**kubelet 同步投射内容，但 env 和 subPath 挂载不接收更新。**
3. **能画上下游吗？** —— 画出「Ingress 资源（etcd）→ Ingress Controller watch → 翻译成 nginx.conf → reload → Controller 的 Service（LoadBalancer/NodePort）→ 云 LB → 用户请求 → host/path 匹配 → upstream（Service ClusterIP）→ kube-proxy → Pod」这条链，并标出每一步失败会看到什么：**ADDRESS 为空（Controller 没接管）/ 404（路由没匹配上）/ 502（后端没端点）/ 证书错误（TLS 配错）**。配置侧画出「ConfigMap/Secret → kubelet 投射 → env（不更新）/ 目录卷（更新，有延迟）/ subPath 卷（不更新）」三条分支。

---

**下一篇**：[`L6-存储PV与PVC`](L6-存储PV与PVC.md)（**从 hostPath 到 StorageClass 的演进 · PV 与 PVC 的绑定 · 访问模式与回收策略 · StatefulSet 的 volumeClaimTemplates · CSI**） · **上一篇**：[`L4-Service与网络`](L4-Service与网络.md) · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md)
