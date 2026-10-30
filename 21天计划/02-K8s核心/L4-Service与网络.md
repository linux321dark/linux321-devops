# L4 · Service 与网络（会访问 → 会排障 → 说得清流量怎么走）

> **本篇定位**：把"服务之间怎么互相访问"讲透。**Service 到底解决了哪三个问题？selector 和 targetPort 写错分别是什么症状？kube-proxy 的转发规则在节点上是怎么落地的？集群 DNS 和 Docker 内置 DNS 是什么关系？源 IP 为什么会丢？**
> **前置**：[`L2-K8s架构与对象模型`](L2-K8s架构与对象模型.md)（组件分工、Pod IP 的概念）、[`L1-Docker-容器化基础`](../01-基础篇/L1-Docker-容器化基础.md)（Docker bridge 网络与内置 DNS 127.0.0.11）。
> **学完能做什么**：为一个 Deployment 写出正确的 Service；拿到一个 503 能在 1 分钟内定位到"selector 没匹配"还是"targetPort 写错"还是"Pod 没 Ready"；解释 Headless Service 为什么不给 VIP；说清 externalTrafficPolicy 与源 IP 保留的关系；画出一次请求从 DNS 到 Pod 的完整路径。
> **在 21 天里**：D3（网络与配置）的 Service 部分；D7 整合项目的服务间调用。
> **环境/版本基准**：本文字段默认值以本地 kind 集群 `kubectl v1.37.1` / `Server v1.37.0` 为准；官方中文文档对应 v1.37 版本页。生产 ACK 的网络实现（kube-proxy 模式、CNI 选型）可能不同，**字段语义以官方文档对应版本为准**。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的"三层追查" · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L4 Service 与网络
│
├─ 1. 概念
│   ├─ 1.1 一句话：Service 卖的是"稳定入口 + 负载均衡 + 服务发现"
│   ├─ 1.2 没有 Service 时的问题（Pod IP 直接访问的三宗罪）
│   ├─ 1.3 三个概念必须分清：Pod IP / ClusterIP / Service DNS 名
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 Service 的三段式：selector 选谁 → 端点清单 → 转发规则
│   ├─ 2.2 selector 与 targetPort 的两级匹配（写错分别什么症状）
│   ├─ 2.3 EndpointSlice：真正的"后端清单"
│   ├─ 2.4 kube-proxy：iptables vs IPVS vs eBPF
│   ├─ 2.5 四种类型 + ExternalName + Headless
│   ├─ 2.6 DNS 解析链路：容器名 vs 服务名
│   ├─ 2.7 sessionAffinity：客户端 IP 亲和
│   ├─ 2.8 externalTrafficPolicy / internalTrafficPolicy 与源 IP
│   └─ 2.9 关键参数与默认值表
│
├─ 3. 动手
│   ├─ 3.1 四种 Service 各建一个
│   ├─ 3.2 端点验证：endpoints 与 endpointslice
│   ├─ 3.3 DNS 解析实测
│   └─ 3.4 验证清单
│
├─ 4. 资源清单逐字段
│   └─ 4.1 Service（逐字段 + 写错的后果）
│
├─ 5. 横向对比
│   ├─ 5.1 ClusterIP vs NodePort vs LoadBalancer vs Ingress（谁在哪一层）
│   ├─ 5.2 Service vs EndpointSlice vs Pod 直连
│   └─ 5.3 为什么生产不用 NodePort
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

> **Service 是一个"把一组会变的 Pod 包装成一个不会变的访问入口"的 API 对象。它同时提供三件事：稳定的 IP/DNS 名字、四层负载均衡、以及基于标签的服务发现。**

这三件事必须分开看，因为它们的实现位置不同：

| 提供什么 | 靠什么实现 | 在哪一层 |
|---|---|---|
| **稳定入口** | 一个由集群分配的虚拟 IP（ClusterIP）+ 一个 DNS 名 | API 对象（Service）本身 |
| **负载均衡** | 节点上的转发规则 | **kube-proxy**（节点侧） |
| **服务发现** | `selector` → 端点清单（EndpointSlice） | **Service 控制器**（控制面） |

> **这个分层是本篇最重要的认知**：
> - **Service 控制器**（控制面）负责"现在有哪些 Pod 可以用" → 写进 EndpointSlice
> - **kube-proxy**（每个节点）负责"流量该转给谁" → 写进 iptables/IPVS 规则
> - **DNS 服务器**（CoreDNS）负责"这个名字对应哪个 IP" → 写进 DNS 记录
>
> **三者任何一个坏了，症状都不同。** 排障时先分清是哪一层坏了，比闷头改配置快十倍。

### 1.2 没有 Service 时的问题

假设你的服务有 3 个 Pod，IP 分别是 `10.1.1.5`、`10.1.2.7`、`10.1.3.9`。**直接用 Pod IP 调用会出三宗罪：**

| 问题 | 具体现象 | Service 怎么解决 |
|---|---|---|
| **① IP 会变** | Pod 重启/迁移后 IP 变了，调用方还指着旧 IP → 调用全失败 | 调用方只认 **Service 的 DNS 名或 ClusterIP**，后端随便换 |
| **② 不会负载均衡** | 调用方要自己维护 3 个 IP，还要自己做轮询 | kube-proxy 自动在多个就绪端点之间分发 |
| **③ 不知道谁活着** | 调用方连到正在启动/正在销毁的 Pod → 502 / 超时 | 端点清单只包含**就绪**的 Pod（readiness 探针说了算） |

> **再补一条 K8s 特有的**：Pod IP 是**集群内**地址，**从集群外根本路由不到**。Service 的 NodePort / LoadBalancer 类型补上了"从外面进来"这一段。
>
> **关键认知**：Service 的价值不是"多一个 IP"，而是**把"会变的后端"和"不变的前端"解耦**。**这个解耦是声明式世界的基本套路**——Deployment 解耦了"会变的版本"和"不变的副本数声明"，Service 解耦了"会变的 Pod"和"不变的访问入口"。

### 1.3 三个概念必须分清：Pod IP / ClusterIP / Service DNS 名

初学者最容易把这三者混着说。**面试必问**：

| 概念 | 是什么 | 谁分配 | 什么时候变 | 谁能访问 |
|---|---|---|---|---|
| **Pod IP** | Pod 网络里的真实地址 | CNI 插件 | **Pod 重建就变** | 集群内 |
| **ClusterIP** | Service 的**虚拟 IP**，不是真实网卡上的地址 | 集群从 IP 池分配 | **Service 不删就不变** | 集群内 |
| **Service DNS 名** | `<svc>.<ns>.svc.cluster.local` | CoreDNS | 不变 | 集群内（DNS 解析） |

**关键：ClusterIP 是"虚拟 IP"，没有对应的网卡。** 它是一个只存在于节点 iptables/IPVS 规则里的"入口地址"。所以：

- 你 `ping` 不通 ClusterIP —— **正常**，它不是一台机器
- 你 `curl` ClusterIP 能通 —— **正常**，那是转发规则在起作用
- ClusterIP **不做 TCP 握手**，它是纯转发（DNAT），所以不需要 ARP/ICMP 响应

> **面试这么答**："ClusterIP 是虚拟 IP，节点上没有这个地址，`arp` 也查不到。它是 kube-proxy 规则里的一个匹配条件。`ping` 不通是正常的，不代表 Service 有问题。**判断 Service 是否正常，要看端点清单和实际 curl，而不是 ping。**"

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Service | 服务 | 稳定访问入口 + 负载均衡 + 服务发现 |
| ClusterIP | 集群 IP | Service 的虚拟 IP，默认类型 |
| NodePort | 节点端口 | 在每个节点上开一个固定端口（30000-32767） |
| LoadBalancer | 负载均衡器类型 | 由云厂商分配外部 LB |
| Headless | 无头 Service | `clusterIP: None`，**不给 VIP**，DNS 直接返回 Pod IP |
| ExternalName | 外部名类型 | 把名字映射到集群外的 CNAME，**不代理流量** |
| `selector` | 选择算符 | 选出哪些 Pod 作为后端 |
| `port` | 服务端口 | Service 自己监听的端口 |
| `targetPort` | 目标端口 | 后端容器实际监听的端口 |
| `nodePort` | 节点端口 | 节点上对外暴露的端口 |
| EndpointSlice | 端点切片 | 后端 Pod 清单的现代表示 |
| Endpoints | 端点（已废弃） | 旧版后端清单 API，**已标记废弃** |
| kube-proxy | — | 节点上的网络代理，维护 Service 转发规则 |
| iptables / IPVS / eBPF | — | kube-proxy 的三种（或替代）转发实现 |
| CoreDNS | — | 集群 DNS 服务器，为 Service 提供名字解析 |
| `sessionAffinity` | 会话亲和 | `None` / `ClientIP` |
| `externalTrafficPolicy` | 外部流量策略 | `Cluster` / `Local`，决定外部流量怎么转、源 IP 保不保 |
| `internalTrafficPolicy` | 内部流量策略 | `Cluster` / `Local`，决定集群内部流量怎么转 |
| `publishNotReadyAddresses` | 发布未就绪地址 | 是否把未就绪的 Pod 也放进端点清单 |
| `healthCheckNodePort` | 健康检查节点端口 | `externalTrafficPolicy=Local` 时供 LB 探活的端口 |

---

## 2. 机制

### 2.1 Service 的三段式

一个 Service 从"你写了 20 行 yaml"到"流量真的转过去了"，中间经过三段：

```
┌─ 段①：Service 控制器（控制面）────────────────────────────────┐
│                                                                │
│  读 Service.spec.selector                                      │
│         │                                                      │
│         ▼  在集群里找 label 完全匹配的 Pod                       │
│  只挑「就绪（Ready=true）」的 Pod                                │
│         │                                                      │
│         ▼  写进 EndpointSlice                                  │
│  EndpointSlice{ addresses:[PodIP...], conditions:{ready:true} } │
└────────────────────────────────────────────────────────────────┘
                            │
                            │  （每个节点上的 kube-proxy watch 这个对象）
                            ▼
┌─ 段②：kube-proxy（每个节点）──────────────────────────────────┐
│                                                                │
│  watch EndpointSlice → 把 Service VIP + port 翻译成转发规则      │
│         │                                                      │
│         ▼  写进内核                                            │
│  iptables NAT 表 / IPVS 虚拟服务器 / eBPF 程序                   │
│                                                                │
│  收到发往 VIP:port 的包 → DNAT 成某个 PodIP:targetPort           │
└────────────────────────────────────────────────────────────────┘
                            │
                            │  （客户端要先把名字解析成 IP）
                            ▼
┌─ 段③：CoreDNS（集群 DNS）─────────────────────────────────────┐
│                                                                │
│  `<svc>.<ns>.svc.cluster.local` → ClusterIP（或 Headless 的 PodIP）│
│  Pod 的 /etc/resolv.conf 指向 CoreDNS                            │
└────────────────────────────────────────────────────────────────┘
```

**这个三段式就是排障地图**：

| 症状 | 坏在哪一段 | 怎么查 |
|---|---|---|
| DNS 解析失败（`could not resolve host`） | 段③ | `kubectl get svc` 看 Service 在不在；`nslookup` 测试；CoreDNS Pod 是否 Running |
| 能解析到 IP，但连不上 / 503 | 段② | `kubectl get endpoints` 看清单；`kubectl get endpointslice` 看 ready 条件 |
| 端点清单是空的 | 段① | selector 与 Pod label 对不上；或 Pod 没 Ready |
| 清单有东西但连上就 502 | 段② + 应用 | `targetPort` 与容器实际端口不一致；应用本身有问题 |

### 2.2 selector 与 targetPort 的两级匹配

这是本篇的核心，也是 503 排查的第一分叉点。**Service 找后端是两级匹配，各管各的：**

```
第一级：selector → 决定「哪些 Pod 是我的后端」
   匹配对象：Pod 的 metadata.labels
   匹配方式：等值集合匹配（selector 里写 app=x，就要 label 里恰好有 app=x）
   匹配失败的后果：端点清单为空 → 请求被丢弃 → 503 / connection refused
        │
        ▼
第二级：targetPort → 决定「转到这个 Pod 的哪个端口」
   匹配对象：Pod 容器实际监听的端口
   匹配方式：数字端口，或命名端口（对应 containerPort 的 name）
   匹配失败的后果：TCP 连上但立刻被拒 / 收到空响应 → connection refused / 502
```

**两级的症状对照表（背下来）**：

| 写错的东西 | 具体写法 | 端点清单 | 客户端看到的 | 底层原因 |
|---|---|---|---|---|
| **selector 写错** | `selector: {app: web}` 但 Pod label 是 `app: Web`（**大小写不同**） | **空的** | 503 / connection refused | **没有后端可选**，包被直接丢弃 |
| **selector 值不匹配** | `selector: {app: web}` 但 Pod label 是 `app: web, tier: api` | **不是空的**（selector 是子集匹配） | 正常 | 匹配成功，多标签不影响 |
| **targetPort 写错** | `targetPort: 8080` 但容器监听 `8081` | **有内容**（Pod 匹配上了） | **connection refused** / 502 | **连上了 Pod 但端口没人监听** |
| **Pod 没 Ready** | 容器起来了但就绪探针没过 | **空**（或 ready=false） | 503 | 探针没过就不进端点清单 |

> **最关键的一句区分**：
> **"selector 写错 → 端点清单是空的"；"targetPort 写错 → 端点清单是有的，但连不上"。**
> **这就是为什么排障第一步永远是 `kubectl get endpoints` / `kubectl get endpointslice` —— 它一刀切开了这两种情况。**

**命名端口这一招值得单独说：**

```yaml
# Deployment 侧
ports:
- name: http          # ← 给了名字
  containerPort: 8080

# Service 侧
ports:
- port: 80
  targetPort: http    # ← 用名字引用，而不是写死 8080
```

**好处**：以后容器端口从 8080 改成 9090，**只改 Deployment，Service 不用动**。这是"用名字而不是数字"的典型价值。

**但要注意**：命名端口要求 **Pod 模板里的 `ports[].name` 和 Service 的 `targetPort` 名字完全一致**，且**多端口时每个端口都必须命名**（官方原文："为 Service 使用多个端口时，必须为所有端口提供名称，以使它们无歧义"）。**名字对不上 → targetPort 解析不出来 → 连不上。**

### 2.3 EndpointSlice：真正的"后端清单"

**Service 控制器做的事，落地在 EndpointSlice 对象上。**

官方原文：

> 控制平面会自动为任何指定了 `selector` 的 Kubernetes Service 创建 EndpointSlice。这些 EndpointSlice 包含了对所有匹配 Service selector 的 Pod 的引用。

EndpointSlice 的结构：

```yaml
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: example-abc
  labels:
    kubernetes.io/service-name: example     # ← 靠这个标签关联回 Service
addressType: IPv4
ports:
- name: http
  protocol: TCP
  port: 80
endpoints:
- addresses:
  - "10.1.2.3"                              # ← Pod IP
  conditions:
    ready: true                             # ← 就绪与否，决定收不收流量
  hostname: pod-1
  nodeName: node-1                          # ← 拓扑信息
  zone: us-west2-a
```

**三个 conditions（这是本篇的必考细节）**：

| 条件 | 含义 | 谁在用 |
|---|---|---|
| `ready` | 等价于"serving 且非 terminating" | **kube-proxy 决定收不收流量主要看它** |
| `serving` | 端点当前在正常响应（Pod 的 Ready 条件） | 精细流量控制 |
| `terminating` | 端点正在被删除（Pod 收到删除时间戳后） | 滚动更新期间保证流量不丢 |

官方原文（terminating）：

> Service 代理通常会忽略 `terminating` 的端点，但如果所有可用端点都在 `terminating` 中，它们可能会把流量路由到同时满足 `serving` 和 `terminating` 的端点。（这有助于确保底层 Pod 滚动更新期间没有 Service 流量丢失。）

> **这就是"滚动更新时为什么偶尔会有一两个请求打到正在销毁的 Pod"的原理** —— 那是**有意的兜底**，不是 bug。**配合 `preStop` 和优雅停机一起看**（L3 的踩坑表里有这一条）。

**`kubectl get endpoints` 还是 `kubectl get endpointslice`？**

| | `endpoints`（旧 API） | `endpointslice`（新 API） |
|---|---|---|
| 状态 | **官方已标记废弃**（v1.33 起 mirror 特性废弃） | **推荐使用**（v1.21 起 Stable） |
| 容量 | **单个对象最多 1000 个后端**，超出截断并打注解 `endpoints.kubernetes.io/over-capacity: truncated` | **每个切片默认最多 100 个端点**，可配到最多 1000 |
| 一个 Service 几个对象 | 1 个 | **多个**（按 IP 族、协议、端口、Service 名组合拆分） |
| 现在还用吗 | 还能用，兼容老控制器 | **kube-proxy 现在以它为转发依据** |

> **官方原文**："EndpointSlices 充当 kube-proxy 在路由内部流量时的**真实来源**。"
>
> **实务建议**：**排障时优先看 endpointslice**（信息更全，有 ready/terminating/serving/zone），`kubectl get endpoints` 作为快速判断"有没有后端"的手段还能用。
>
> **注意"多个切片"这件事**：一个 Service 对应多个 EndpointSlice 时，`kubectl get endpointslice` 要靠标签 `kubernetes.io/service-name=<svc>` 找全。**只 `get endpointslice <name>` 看一个切片会漏。**

### 2.4 kube-proxy：iptables vs IPVS vs eBPF

kube-proxy 是**每个节点上跑的网络代理**，官方原文：

> kube-proxy 维护节点上的一些网络规则，这些网络规则会允许从集群内部或外部的网络会话与 Pod 进行网络通信。如果操作系统提供了可用的数据包过滤层，则 kube-proxy 会通过它来实现网络规则。否则，kube-proxy 仅做流量转发。

**三种实现（必须分清"谁是谁"）**：

| 实现 | 属于谁 | 工作方式 | 特点 | 什么时候用 |
|---|---|---|---|---|
| **iptables** | kube-proxy 内置模式 | 在内核 netfilter 的 NAT 表里插规则，匹配 VIP:port 做 DNAT | **通用、兼容性最好**；**规则数随 Service 数线性增长**，大集群规则多时性能下降；负载均衡接近轮询 | 默认；中小规模集群 |
| **IPVS** | kube-proxy 内置模式 | 用内核 `ip_vs` 模块建虚拟服务器表 | **O(1) 查表**，规模大了性能好；**调度算法丰富**（rr/wrr/lc/sh 等，属 IPVS 内核能力）；需要内核模块支持 | 大规模集群；**kube-proxy 启动前要装 `ip_vs` 模块** |
| **eBPF** | **不是 kube-proxy 的模式**，是 CNI 插件自带的替代实现（如 Cilium） | 用 eBPF 程序在内核里做转发 | 性能好、可编程、可观测性强 | CNI 选了 eBPF 方案时 |

> **一个必须讲清的架构点**（官方原文）：
> "一些网络插件为流量代理提供了自己的第三方实现。当你使用那种网络插件时，节点便不需要运行 kube-proxy。"
>
> **也就是说：eBPF 方案下 kube-proxy 根本不装。** 这是 Cilium 等 CNI 的设计：Service 转发下沉到 CNI 自己实现。
>
> **本地民企的真实情况**：ACK 托管版通常提供 **eBPF 模式**（Alibaba Cloud Container Service 有 eBPF 版）和经典 kube-proxy 模式两种；自建集群常见 iptables 或 IPVS。**面试能说清"kube-proxy 有哪几种模式、eBPF 那套根本不是 kube-proxy"就已经超过大多数候选人。**

**kube-proxy 在架构里的位置**（官方原文）：

> 每个节点都运行 kube-proxy 组件。你需要在每个节点上安装一个网络代理组件，以确保 Service API 和相关行为在你的集群网络上可用。

**和 CNI 的分工**：

```
CNI 插件（Calico / Flannel / Cilium）  → 负责「Pod 之间的网络连通」（Pod IP ↔ Pod IP）
kube-proxy                              → 负责「Service VIP 的转发」（VIP → 某个 Pod）
```

> **这是本篇最容易被问倒的一题**："Pod 能互相 ping 通但 Service VIP 不通" —— 这说明 **CNI 正常、kube-proxy 有问题**。反过来（Service 通但 Pod 间不通）说明 **CNI 有问题**。**分清这两层，排障效率翻倍。**

### 2.5 四种类型 + ExternalName + Headless

官方原文（Service 类型）：

> Service API 中的 `type` 字段被设计为**层层递进**的形式 - 每层都建立在前一层的基础上。

```
ClusterIP（基础，虚拟 IP）
   ├── NodePort      = ClusterIP + 在每个节点上开一个固定端口
   └── LoadBalancer  = NodePort（或 ClusterIP）+ 云厂商的外部 LB
ExternalName（特例：不代理流量，只做 DNS 映射）
Headless（特例：clusterIP=None，没有 VIP）
```

| 类型 | 有 VIP 吗 | 集群内能访问 | 集群外能访问 | 本质 | 典型用途 |
|---|---|---|---|---|---|
| **ClusterIP** | ✅ | ✅ | ❌ | 虚拟 IP + 集群内转发 | **内部服务互访（默认）** |
| **NodePort** | ✅ | ✅ | ✅（节点IP:30000+） | ClusterIP + 节点端口转发 | 裸金属、无云 LB、临时调试 |
| **LoadBalancer** | ✅ | ✅ | ✅（云厂商 SLB/ALB） | 请求云厂商创建外部 LB | **云上对外暴露** |
| **Headless**（`clusterIP: None`） | ❌ | ✅（用 Pod IP） | ❌ | **DNS 直接返回 Pod IP** | **StatefulSet 必需**、客户端自己负载均衡 |
| **ExternalName** | ❌ | 仅 DNS | 仅 DNS | **CNAME 映射到集群外** | 把集群内名字指向外部系统 |

**Headless 的官方定义（这一段很值得背）**：

> 无头 Service 不会获得集群 IP，kube-proxy 不会处理这类 Service，而且平台也不会为它们提供负载均衡或路由支持。无头 Service 允许客户端直接连接到它所偏好的任一 Pod。无头 Service 不使用虚拟 IP 地址和代理配置路由和数据包转发；相反，无头 Service 通过**内部 DNS 记录**报告各个 Pod 的端点 IP 地址。

> **所以"Headless 不给 VIP"不是缺陷，是设计**：**要负载均衡就用 ClusterIP，不要负载均衡、要直连 Pod 就用 Headless。**
>
> **StatefulSet 为什么必须配 Headless？** 因为 StatefulSet 的 Pod 需要**稳定的、可单独寻址的 DNS 名**（`web-0.web.default.svc.cluster.local`），而普通 ClusterIP 背后是一堆 Pod IP，客户端没法指定"我要连第 0 个"。**Headless 让每个 Pod 的 IP 直接暴露在 DNS 里。**（详见 [L6](L6-存储PV与PVC.md) 的 StatefulSet 部分。）

**ExternalName 的官方行为**：

> 类型为 ExternalName 的 Service 将 Service 映射到 DNS 名称……将集群的 DNS 服务器配置为返回具有该外部主机名值的 `CNAME` 记录。**集群不会为之创建任何类型代理。**

> **注意**：ExternalName 的 Service **不创建端点清单、不代理流量**，纯 DNS 层面跳转。官方还提醒："当查找主机 `my-service.prod.svc.cluster.local` 时，集群 DNS 服务返回 `CNAME` 记录，其值为 `my.database.example.com`。"

### 2.6 DNS 解析链路：容器名 vs 服务名

**这是 L1 Docker 那个实验的 K8s 对应版，也是面试高频。**

**先回顾 Docker（L1 已实测）**：

```
Docker bridge 网络：
  容器 A 的 /etc/resolv.conf → 127.0.0.11（Docker 内置 DNS）
  Docker 内置 DNS 解析：容器名 / 网络别名 → 容器 IP
  同 network 内可以 ping 通容器名
```

**K8s 的对应关系（完全同构，但换了名字和作用域）**：

| 维度 | Docker | Kubernetes |
|---|---|---|
| DNS 服务器地址 | `127.0.0.11`（容器内回环） | **CoreDNS 的 ClusterIP**（集群内一个真实服务） |
| DNS 服务器实现 | Docker 内置（每个 Docker daemon 一份） | **CoreDNS**（一个 Deployment，通常跑在 `kube-system`） |
| 名字解析对象 | **容器名 / 网络别名** | **Service 名**、Headless 的 Pod 名 |
| 名字作用域 | 同一个 Docker network 内 | **整个集群**（Service 按命名空间区分） |
| 写在哪 | 容器内 `/etc/resolv.conf` | Pod 内 `/etc/resolv.conf`（kubelet 注入） |
| 完整名字形式 | `容器名` | `<svc>.<ns>.svc.cluster.local` |
| 外部域名解析 | Docker 转发给宿主机的 DNS | **CoreDNS 转发给上游 DNS**（`forward` 插件） |

**K8s 的解析链路（从你敲下域名到拿到 IP）：**

```
① 你的 Pod 里执行 curl http://day1-go-svc.default.svc.cluster.local
        │
② 容器内 /etc/resolv.conf 写着：
      nameserver 10.96.0.10        ← CoreDNS 的 ClusterIP
      search default.svc.cluster.local svc.cluster.local cluster.local
      options ndots:5
        │
③ 容器里的解析器按 search 域依次拼接尝试：
      day1-go-svc.default.svc.cluster.local   ← 命中
        │
④ CoreDNS 收到查询，查自己的 Corefile 规则：
      cluster.local 的 service 插件 → 查 Service 对象
        │
⑤ 返回：day1-go-svc.default.svc.cluster.local.  30 IN A 10.96.123.45
        （10.96.123.45 = 这个 Service 的 ClusterIP）
        │
⑥ curl 拿到 10.96.123.45 → 发 TCP SYN
        │
⑦ 目标节点的 kube-proxy 规则匹配 VIP:port → DNAT → 某个就绪 Pod 的 IP:targetPort
```

**三种名字写法的实际含义**：

| 写法 | 解析成什么 | 什么时候用 |
|---|---|---|
| `day1-go-svc` | 当前命名空间的 Service 的 ClusterIP | **同命名空间内，最常用** |
| `day1-go-svc.other-ns` | `other-ns` 命名空间的 Service | **跨命名空间的简写** |
| `day1-go-svc.other-ns.svc.cluster.local` | 同上（全限定） | **跨集群/配置里写死时用** |

> **必须记住的坑**：**短名 `day1-go-svc` 只在当前命名空间有效。** 官方场景 B 里的第 3 条踩的就是这个：跨命名空间调不通，因为写了 `http://user-svc` 而不是全限定名。
>
> **为什么短名在同 ns 内有效？** 因为 `/etc/resolv.conf` 的 `search` 域里第一个就是 `<当前命名空间>.svc.cluster.local`。**而 search 域是 kubelet 按 Pod 所在命名空间注入的**，这就是"短名只在同 ns 生效"的底层原因。

**Headless 的 DNS 行为（和 ClusterIP 完全不同）**：

| | ClusterIP | Headless |
|---|---|---|
| DNS 返回 | **一个 A 记录**：VIP | **多个 A 记录**：所有就绪 Pod 的 IP |
| 谁做负载均衡 | kube-proxy（转发时选后端） | **客户端自己**（DNS 轮询返回不同 IP） |
| 未就绪的 Pod | 不在记录里 | **不在记录里**（除非 `publishNotReadyAddresses=true`） |

> **这就是"Headless 集群客户端负载均衡"的原理**：DNS 轮询把多个 Pod IP 轮着返回，客户端自己挑。**好处是客户端可以感知所有后端并自己做健康检查**（比如 Kafka 客户端要拿到全部 broker 列表）。
>
> **代价**：DNS 缓存会延迟变更感知。**Pod 扩缩容后客户端可能要等 DNS TTL 过期才知道。** 这是"Headless 不适合普通无状态服务"的原因。

### 2.7 sessionAffinity：客户端 IP 亲和

**默认是 `None`（官方原文："Must be ClientIP or None. Defaults to None."）。**

设成 `ClientIP` 后，**同一个源 IP 的连接会被尽量送到同一个后端 Pod**。

**关键细节：超时时间**。官方原文：

> `timeoutSeconds` specifies the seconds of ClientIP type session sticky time. The value must be >0 && <=86400(for 1 day) if ServiceAffinity == "ClientIP". **Default value is 10800(for 3 hours).**

| 项 | 值 |
|---|---|
| 取值 | `None`（默认）/ `ClientIP` |
| 默认粘滞时长 | **10800 秒（3 小时）** |
| 可配范围 | `> 0` 且 `≤ 86400`（1 天） |

**它的价值场景（很少，但一旦需要就是刚需）**：

| 场景 | 为什么需要 |
|---|---|
| **长连接 + 无状态不彻底** | 会话状态存在进程内存里，负载到别的 Pod 就丢 |
| TCP 长连接的负载不均 | 某些客户端只建一条连接，打在一个 Pod 上 |
| 灰度验证 | 希望某个测试来源固定命中某个版本 |

**它的代价（必须一起讲，否则就是不完整的答案）**：

1. **负载可能严重不均**。NAT 后面的大批用户共享一个源 IP → 全打到同一个 Pod。
2. **优雅摘除失效**。Pod 要下线时还挂着粘滞连接 → 客户端被"钉"在正在销毁的 Pod 上。
3. **它不是"会话保持"**。它只是"按源 IP 分配"，**应用层会话（登录态、购物车）仍然要靠共享存储或 token**。

> **面试这么答**："`sessionAffinity: ClientIP` 是**四层的 IP 粘滞**，不是应用层的会话保持。它默认粘 3 小时（`timeoutSeconds` 默认 10800）。它的真实代价是**负载不均和优雅摘除失效**——Pod 要下线时，客户端还粘在上面。所以我们只在'进程内存存会话'这种改不动的老系统上用，新系统一律用共享存储 + 无状态。"

### 2.8 externalTrafficPolicy / internalTrafficPolicy 与源 IP

这两个字段决定"**流量怎么落到后端**"，直接决定**客户端源 IP 保不保留**。

#### externalTrafficPolicy（外部流量策略）

官方原文：

> The default value, "Cluster", uses the standard behavior of routing to all endpoints evenly (possibly modified by topology and other features).

| 取值 | 转发行为 | 源 IP | 代价 |
|---|---|---|---|
| **`Cluster`**（默认） | 节点收到请求后，**可以转发到集群内任意节点的 Pod** | **通常被 SNAT 成节点 IP → 丢失** | 负载最均衡 |
| **`Local`** | **只转发到本节点上的 Pod** | **保留客户端真实源 IP** | 本节点没有该 Service 的 Pod 时**该节点不转发**；需要健康检查配合 |

**`Local` 的两个必须知道的细节**：

1. **它需要 `healthCheckNodePort` 配合**。官方 API 原文："healthCheckNodePort specifies the healthcheck nodePort for the service. **This only applies when type is set to LoadBalancer and externalTrafficPolicy is set to Local.**" —— 也就是**用 LoadBalancer 类型 + Local 策略**时，K8s 会自动分配一个节点端口专供云 LB 探活，**LB 通过这个端口判断"这个节点还有没有该 Service 的 Pod"**。

2. **本地无副本的节点会被 LB 摘掉**（靠上面那个健康检查）。所以 `Local` 不会造成请求失败，**前提是 LB 正确使用了这个健康检查端口**。**如果 LB 没配对，流量会打到没有后端的节点上。**

**为什么源 IP 会丢？** 因为 `Cluster` 模式下，节点 A 收到请求后可能把包转发给节点 B 上的 Pod。**跨节点转发必须改写源地址**（否则回包不知道发给谁），这个改写就是 SNAT → **你的应用看到的是节点 IP，不是真实客户端 IP**。

> **面试这么答**："`externalTrafficPolicy` 默认 `Cluster`，流量可能跨节点转发，跨节点就要 SNAT，**源 IP 就丢了**。改成 `Local` 后只打本节点 Pod，源 IP 保留。代价是必须配合 `healthCheckNodePort` 让 LB 摘掉没有副本的节点。**我们的业务需要拿到客户端真实 IP 做风控/审计，所以用 `Local`。**"

#### internalTrafficPolicy（内部流量策略）

官方原文的默认值同样是 `Cluster`。它管的是**集群内部**的访问路径：

| 取值 | 行为 | 用途 |
|---|---|---|
| **`Cluster`**（默认） | 内部流量可以转到任意节点的 Pod | 通用 |
| **`Local`** | **只转到本节点上的 Pod** | **数据面本地化**：避免跨节点/跨可用区的流量费用与延迟 |

> **`internalTrafficPolicy: Local` 的典型场景**：**DaemonSet 部署的日志采集/监控 agent**，它只处理本节点的 Pod 数据。配了 `Local` 之后，agent 到 Service 的请求只落在本机的 agent 上，**不会把本机数据发到别的节点去处理**——省带宽、省跨区流量费。
>
> **代价和 `externalTrafficPolicy: Local` 一样**：本节点没有副本就不转发。所以**DaemonSet 的每个节点都必须有副本**（DaemonSet 本来就是每节点一个，正好对上）。

#### 两个字段的对照

| 维度 | `externalTrafficPolicy` | `internalTrafficPolicy` |
|---|---|---|
| 管谁 | **从集群外进来**的流量 | **集群内部**的流量 |
| 默认 | `Cluster` | `Cluster` |
| `Local` 的效果 | 保留源 IP | 数据本地化，不跨节点 |
| 配合字段 | `healthCheckNodePort`（仅 LoadBalancer + Local 时自动分配） | 无专门健康检查端口 |
| 典型场景 | 需要真实客户端 IP 的业务 | DaemonSet 型 agent、费用优化 |

### 2.9 关键参数与默认值表

| 参数 | 默认 | 含义 | 实务要点 |
|---|---|---|---|
| `Service.type` | **ClusterIP** | 暴露方式 | 内部服务一律 ClusterIP；**每个 LB 都花钱** |
| `Service.clusterIP` | 自动分配 | 虚拟 IP | 写 `None` = Headless |
| `Service.ports[].port` | 必填 | Service 自己的端口 | — |
| `Service.ports[].targetPort` | **等于 `port`** | 后端容器端口 | 省略 = 恒等映射；**用命名端口更抗改** |
| `Service.ports[].protocol` | **TCP** | 协议 | 支持 TCP/UDP/SCTP |
| `Service.ports[].nodePort` | 自动分配（30000-32767） | 节点端口 | **端口有限、每节点都占** |
| `Service.sessionAffinity` | **None** | 会话亲和 | `ClientIP` 时默认粘 **10800s** |
| `Service.sessionAffinityConfig.clientIP.timeoutSeconds` | **10800** | 粘滞时长 | 范围 `>0 且 ≤86400` |
| `Service.externalTrafficPolicy` | **Cluster** | 外部流量怎么转 | 要源 IP 就 `Local` |
| `Service.internalTrafficPolicy` | **Cluster** | 内部流量怎么转 | DaemonSet agent 用 `Local` |
| `Service.publishNotReadyAddresses` | false（**API 文档未显式声明默认值**） | 未就绪的 Pod 也进端点清单 | 有状态服务启动期依赖时用 |
| `Service.healthCheckNodePort` | 自动分配（条件满足时） | LB 探活端口 | 仅 LoadBalancer + Local |
| `Service.spec.selector` | 无（可选） | 选后端 Pod | **不写就要手工维护 EndpointSlice** |
| `kube-proxy` 模式 | iptables（经典） | 转发规则实现 | 大集群考虑 IPVS / eBPF |
| EndpointSlice 每切片端点数 | 100（可配，最多 1000） | 切片容量 | 官方 EndpointSlice 文档 |

---

## 3. 动手

> 本节命令在 kind 集群（`day2`）上执行。**目的是亲手观察，不是让你改生产。**

### 3.1 四种 Service 各建一个

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: day2-web
  labels:
    app: day2-web
spec:
  replicas: 2
  selector:
    matchLabels:
      app: day2-web
  template:
    metadata:
      labels:
        app: day2-web
    spec:
      containers:
      - name: web
        image: registry.cn-hangzhou.aliyuncs.com/linux321-devops/day1-go-demo:v20261018
        imagePullPolicy: IfNotPresent
        ports:
        - name: http
          containerPort: 8080
        readinessProbe:
          httpGet:
            path: /healthz
            port: http          # ← 用命名端口
          periodSeconds: 5
          failureThreshold: 3
        resources:
          requests:
            cpu: "100m"
            memory: "128Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: day2-web
spec:
  type: ClusterIP
  selector:
    app: day2-web
  ports:
  - name: http
    protocol: TCP
    port: 80
    targetPort: http          # ← 命名端口引用
```

```bash
kubectl apply -f day2-web.yaml
```

**四种类型一次建齐**：

```yaml
# ① NodePort
apiVersion: v1
kind: Service
metadata:
  name: day2-web-nodeport
spec:
  type: NodePort
  selector:
    app: day2-web
  ports:
  - port: 80
    targetPort: http
    nodePort: 30080        # ← 手动指定，或让它自动分配
---
# ② LoadBalancer（本地 kind 无云厂商，状态会一直 Pending，这是正常的）
apiVersion: v1
kind: Service
metadata:
  name: day2-web-lb
spec:
  type: LoadBalancer
  selector:
    app: day2-web
  ports:
  - port: 80
    targetPort: http
---
# ③ Headless
apiVersion: v1
kind: Service
metadata:
  name: day2-web-headless
spec:
  clusterIP: None           # ← 这就是 Headless 的全部秘密
  selector:
    app: day2-web
  ports:
  - port: 80
    targetPort: http
---
# ④ ExternalName（指向集群外）
apiVersion: v1
kind: Service
metadata:
  name: day2-external-db
spec:
  type: ExternalName
  externalName: db.example.internal
```

```bash
kubectl apply -f services.yaml
kubectl get svc
```

**看到什么算对（关键观察点）**：

| Service | CLUSTER-IP 列 | EXTERNAL-IP 列 | PORT 列 |
|---|---|---|---|
| `day2-web` | 形如 `10.96.x.x` | `<none>` | `80/TCP` |
| `day2-web-nodeport` | 形如 `10.96.x.x` | `<none>` | `80:30080/TCP` |
| `day2-web-lb` | 形如 `10.96.x.x` | **`<pending>`（本地无云 LB）** | `80/TCP` |
| `day2-web-headless` | **`None`** | `<none>` | `80/TCP` |
| `day2-external-db` | `<none>` | `db.example.internal` | — |

> **`CLUSTER-IP` 显示 `None` 的那个就是 Headless。`EXTERNAL-IP` 显示域名的是 ExternalName。`PORT` 列显示 `80:30080/TCP` 的是 NodePort（冒号后面是节点端口）。**
>
> **本地 kind 里 LoadBalancer 会一直是 `pending`** —— 因为没有云厂商控制器去创建 LB。**这不是配置错误，是环境没有那个能力。** 生产 ACK 上会拿到真实 SLB/ALB 地址。

### 3.2 端点验证：endpoints 与 endpointslice

```bash
# ① 快速看有没有后端
kubectl get endpoints day2-web
kubectl get endpointslices -l kubernetes.io/service-name=day2-web
```

> **看到什么算对**：`endpoints` 的 `ENDPOINTS` 列应该有 **2 个 `IP:PORT`**（2 个副本）。**空的就是没匹配上 —— 立刻去查 selector。**

```bash
# ② 看详细的就绪状态（比 endpoints 信息全）
kubectl get endpointslice -l kubernetes.io/service-name=day2-web -o yaml
```

**要看的三个字段**：

```yaml
endpoints:
- addresses:
  - "10.244.0.5"          # ① Pod IP
  conditions:
    ready: true            # ② 是否就绪（决定收不收流量）
    serving: true
    terminating: false     # ③ 是否正在销毁
  nodeName: day2-control-plane
```

```bash
# ③ 反查：这些端点是哪些 Pod
kubectl get pods -o wide
# 看 Pod 的 IP 列，和 endpointslice 里的 addresses 对上
```

**反向验证 selector 匹配**（排障必做）：

```bash
# Service 认为它要选谁
kubectl get svc day2-web -o jsonpath='{.spec.selector}'; echo
# Pod 实际上有什么 label
kubectl get pods --show-labels -l app=day2-web
```

> **两个输出必须"对得上"**：`{"app":"day2-web"}` 和 Pod 的 `app=day2-web`。**对不上 → 端点清单空 → 503。**

### 3.3 DNS 解析实测

```bash
# ① 在 Pod 里解析 Service 名（需要一个能跑 nslookup/curl 的 Pod）
kubectl run netcheck --rm -it --restart=Never --image=busybox:1.28 -- sh

# ② 容器内执行：
cat /etc/resolv.conf
# nameserver 10.96.0.10        ← CoreDNS 的 ClusterIP
# search default.svc.cluster.local svc.cluster.local cluster.local
# options ndots:5

nslookup day2-web             # 短名：同 ns，命中 search 域的第一个
nslookup day2-web.default.svc.cluster.local   # 全限定
nslookup day2-web-headless    # Headless → 返回多个 Pod IP

wget -qO- --timeout=5 http://day2-web/   # 短名访问
```

> **验证点**：
> - `nslookup day2-web` → 返回**一个** ClusterIP
> - `nslookup day2-web-headless` → 返回**多个** Pod IP（**这是 Headless 的核心特征**）
> - `wget http://day2-web/` 能拿到应用响应 → **整条链路通了**（DNS → VIP → kube-proxy → Pod → 应用）

**跨命名空间测试**（这是第 6 节踩坑表的第 4 条）：

```bash
kubectl create ns other
# 在 other 里 nslookup day2-web → 失败（短名找不到）
# 在 other 里 nslookup day2-web.default → 成功
```

### 3.4 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| Service 有 VIP | `kubectl get svc day2-web` | CLUSTER-IP 不是 `None` |
| 后端有 Pod | `kubectl get endpoints day2-web` | **2 个 IP:PORT** |
| 端点都就绪 | `kubectl get endpointslice -l kubernetes.io/service-name=day2-web -o yaml` | `ready: true` |
| selector 对得上 | `kubectl get svc -o jsonpath='{.spec.selector}'` vs `kubectl get pods --show-labels` | 两边一致 |
| DNS 能解析 | Pod 内 `nslookup day2-web` | 返回 ClusterIP |
| Service 能访问 | Pod 内 `wget -qO- http://day2-web/` | 返回应用响应 |
| Headless 返多个 IP | `nslookup day2-web-headless` | **多个 Pod IP** |
| NodePort 在端口范围 | `kubectl get svc day2-web-nodeport` | 30000-32767 之间 |

---

## 4. 资源清单逐字段

### 4.1 Service

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: v1` | string | ✅ | API 组与版本 | 写 `apps/v1` → **no matches for kind** |
| `kind: Service` | string | ✅ | 对象类型 | 拼错 → apply 报错 |
| `metadata.name` | string | ✅ | Service 名，**必须是合法 RFC 1123 标签名** | 含下划线/大写 → apply 报错 |
| `metadata.namespace` | string | | 命名空间 | — |
| `spec.type` | enum | | **默认 ClusterIP**；`ClusterIP`/`NodePort`/`LoadBalancer`/`ExternalName` | 写 `LoadBalancer` 在每个服务上都用 → **每个都产生 LB 费用** |
| `spec.clusterIP` | string | | 集群 IP，通常自动分配；写 `None` = Headless | 写死某个 IP → 可能与已有 VIP 冲突 |
| `spec.externalName` | string | | 仅 `type: ExternalName` 时有效，**小写 RFC-1123 主机名** | 写成大写 → 校验失败；写成 IP 字符串 → **被当作 DNS 名解析，解析不了** |
| `spec.selector` | map | | 选后端 Pod（**等值匹配**） | 与 Pod label 不一致 → **端点清单空 → 503** |
| `spec.ports[]` | list | ✅ | 端口列表 | 空列表 → Service 没有任何监听端口 |
| `.port` | int | ✅ | Service 自己的端口 | — |
| `.targetPort` | int/str | | **默认等于 `port`**；可为命名端口 | 数字写错 → **连接被拒/502**；命名端口名字对不上 → 同上 |
| `.nodePort` | int | | 节点端口，系统从 30000-32767 分配 | 手动指定冲突值 → **apply 报错**；不指定且类型不需要 → 不分配 |
| `.protocol` | enum | | **默认 TCP**；支持 TCP/UDP/SCTP | 协议不匹配 → 无流量 |
| `.name` | string | | 端口名；**多端口时必填** | 命名端口与 containerPort 名字不一致 → targetPort 解析失败 |
| `.appProtocol` | string | | 应用协议提示（`kubernetes.io/h2c` 等） | 仅提示性，写错通常无影响 |
| `spec.sessionAffinity` | enum | | **默认 None**；`None`/`ClientIP` | 设 `ClientIP` 但没配 LB 健康检查 → 负载不均、摘除失效 |
| `spec.sessionAffinityConfig.clientIP.timeoutSeconds` | int | | **默认 10800**（3 小时）；`>0 且 ≤86400` | 超出范围 → apply 报错 |
| `spec.externalTrafficPolicy` | enum | | **默认 Cluster**；`Cluster`/`Local` | 要源 IP 却用默认 → **应用拿到的是节点 IP** |
| `spec.internalTrafficPolicy` | enum | | **默认 Cluster**；`Cluster`/`Local` | DaemonSet agent 用默认 → **数据被发到别的节点处理，白花跨区流量费** |
| `spec.healthCheckNodePort` | int | | LB 探活端口；**仅 LoadBalancer + Local 时自动分配** | 手动写错端口 → LB 探活失败 → 流量被误摘 |
| `spec.publishNotReadyAddresses` | bool | | 未就绪的 Pod 也进端点清单 | 误开 → **流量打到没准备好的 Pod → 502** |
| `spec.loadBalancerIP` | string | | 指定 LB IP；**v1.24 起已弃用** | 用它 → 未来可能被移除；**改用厂商注解** |
| `metadata.annotations` | map | | 厂商/控制器专用注解 | 键写错 → **不报错，注解不生效** |
| `status.loadBalancer.ingress[].ip` | — | — | **由控制器写回**，你不能手动填 | 手改 status 会被控制器覆盖 |

**Service 名字的特殊约束**（和一般对象不同）：

> Service 对象的名称必须是有效的 RFC 1123 标签名称。

**和 Deployment/Pod 的 DNS 子域名要求不同** —— **Service 名不能用点号，不能有大写字母。** 写 `my.service` 会被拒。

---

## 5. 横向对比

### 5.1 ClusterIP vs NodePort vs LoadBalancer vs Ingress（谁在哪一层）

**这张表是本篇的核心产出。四个东西不是并列关系，而是分属不同层：**

```
┌──────────────────────────────────────────────────────────────┐
│  第 4 层：入口路由                                            │
│  ┌──────────────┐        ┌──────────────────────────────┐   │
│  │  Ingress     │        │  LoadBalancer (Service 类型)  │   │
│  │  七层路由     │        │  四层，云厂商 LB              │   │
│  │  host/path   │        │                              │   │
│  │  需 Controller│        │  cloud-controller-manager    │   │
│  └──────┬───────┘        └──────────────┬───────────────┘   │
│         │                               │                   │
│  Ingress Controller                     │                   │
│  (nginx / ALB)——把规则翻译成              │                   │
│  nginx.conf / ALB 规则                    │                   │
│         │                               │                   │
├─────────┴───────────────────────────────┴───────────────────┤
│  第 3 层：Service（集群内的稳定入口 + 四层负载均衡）           │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  ClusterIP（默认）· NodePort · Headless              │   │
│  │  实体：API 对象 + kube-proxy 在节点上写的转发规则       │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                   │
├──────────────────────────┴───────────────────────────────────┤
│  第 2 层：Pod 网络（CNI 负责）                                │
│  Pod IP ↔ Pod IP                                            │
├──────────────────────────────────────────────────────────────┤
│  第 1 层：容器网络                                             │
│  容器 ↔ 容器（同 Pod 内 localhost）                            │
└──────────────────────────────────────────────────────────────┘
```

**四个方案逐个对比**：

| 维度 | **ClusterIP** | **NodePort** | **LoadBalancer** | **Ingress** |
|---|---|---|---|---|
| **属于哪一层** | Service 层（**实体对象**） | Service 层（**实体对象**） | Service 层（**实体对象**）+ 云 LB | **独立的路由配置对象**（不是 Service 类型） |
| **协议** | 四层（TCP/UDP/SCTP） | 四层 | 四层 | **七层（HTTP/HTTPS）** |
| **谁实现转发** | kube-proxy | kube-proxy | kube-proxy → 节点 → 云 LB | **Ingress Controller**（nginx / ALB） |
| **能不能按域名路由** | ❌ | ❌ | ❌ | ✅（**host 匹配**） |
| **能不能按路径路由** | ❌ | ❌ | ❌ | ✅（**path 匹配**） |
| **能不能 TLS 终结** | ❌ | ❌ | 云 LB 侧可 | ✅（**Controller 侧终结**） |
| **一个入口管几个服务** | 1 个 | 1 个 | 1 个 | **N 个**（多 host / 多 path） |
| **费用** | 无 | 无（但占节点端口） | **云厂商计费** | 通常 1 个 LB + 若干规则 |
| **生产建议** | ✅ **内部服务默认** | ⚠️ **只用于临时调试** | ✅ **云上四层入口** | ✅ **云上七层入口（首选）** |

> **一句话记住**："**ClusterIP / NodePort / LoadBalancer 是同一种东西的三档暴露强度，Ingress 是另一个维度的东西。**"
>
> **Ingress 是七层路由配置，LoadBalancer 是四层入口。** 它们**经常配合使用**：Ingress Controller 本身通过一个 LoadBalancer 或 NodePort 类型的服务暴露出去，Ingress 负责把七层规则分发给内部各个 ClusterIP Service。
>
> **具体链路（面试画图题的标准答案）**：
> ```
> 用户 → 域名 DNS → 云 SLB/ALB（四层，负责 TLS 或只做转发）
>      → Ingress Controller（nginx，七层：host/path 匹配）
>      → Service（ClusterIP，kube-proxy 选一个就绪 Pod）
>      → Pod
> ```

### 5.2 Service vs EndpointSlice vs Pod 直连

| 方案 | 稳定性 | 负载均衡 | 健康检查 | 什么时候用 |
|---|---|---|---|---|
| **直接用 Pod IP** | ❌ 重建就变 | ❌ 无 | ❌ 无 | **几乎不该用**（只有调试） |
| **Service + ClusterIP** | ✅ | ✅ kube-proxy 选就绪端点 | ✅ 靠就绪探针 | **内部服务互访（默认）** |
| **Service + Headless** | ✅ 名字稳定 | ❌ **客户端自己做** | ✅ 靠就绪探针（未就绪的不进 DNS） | StatefulSet、客户端要感知全部后端 |
| **Service + ExternalName** | ✅ | ❌ 不代理 | — | 指向集群外系统 |
| **手工维护 EndpointSlice** | ✅ | ✅ | 手工判断 | **Service 无 selector** 时把流量引到集群外后端 |

**"Service 没有 selector" 的官方用途**（这一条很多人不知道）：

> Services most commonly abstract access to Kubernetes Pods thanks to the selector, but when used with a corresponding set of EndpointSlices objects and without a selector, the Service can abstract other kinds of backends, **including ones that run outside the cluster**.

官方举的场景：

- 生产用外部数据库集群，测试环境用你自己的数据库
- 指向**另一个命名空间**或**另一个集群**的 Service
- 迁移期间，只把一部分后端放进 K8s

> **注意限制**：官方原文——"The Kubernetes API server does not allow proxying to endpoints that are not mapped to pods. Actions such as `kubectl port-forward service/<service-name> ...` where the service has no selector will fail due to this constraint."

### 5.3 为什么生产不用 NodePort

**这是本篇必须能正面回答的问题。** 理由按重要性排：

| # | 理由 | 具体后果 |
|---|---|---|
| **1** | **绕过入口层，安全能力全丢** | WAF、限流、证书、访问日志、灰度全在 Ingress/LB 上做。**NodePort 直连等于把这些全绕过去** |
| **2** | **节点端口资源有限且全局冲突** | 范围 30000-32767（**2768 个/节点**），全集群 Service 共用这个命名空间，**两个 Service 抢同一个端口就 apply 失败** |
| **3** | **每个节点都暴露一个入口** | 攻击面 = 节点数 × Service 数。**不是"暴露一个服务"，是"暴露 N 个入口"** |
| **4** | **默认丢源 IP** | `externalTrafficPolicy` 默认 `Cluster` → 源 IP 被 SNAT → **风控、审计、限流全部拿不到真实客户端 IP** |
| **5** | **负载不均 + 摘除不干净** | 流量先到节点再转发到别的节点，多一跳；Pod 下线时老连接还在 |
| **6** | **没有健康检查联动** | 云 LB 有主动探活、能自动摘除坏节点；**NodePort 只是一个端口转发，没人告诉你这个节点上的 Pod 全挂了** |

> **面试这么答（推荐这个结构）**："生产不用 NodePort 主要四层考虑：**① 入口治理**——WAF、限流、TLS、灰度都在 Ingress/云 LB 这一层，NodePort 等于绕过整套治理；**② 端口资源**——节点端口范围 30000-32767 且全局共享，多个 Service 容易撞；**③ 攻击面**——暴露的是每个节点而不是一个入口；**④ 可观测性**——默认 `externalTrafficPolicy: Cluster` 会把源 IP SNAT 掉，审计和限流拿不到真实客户端 IP。**NodePort 的正确定位是临时调试和裸金属环境**，测试环境可以用，生产入口应该统一走 Ingress 或 LoadBalancer。"

---

## 6. 踩坑与排障（症状 → 状态 → 原因）

**排障铁律：Service 问题永远先分清"哪一层坏了"（DNS / 端点清单 / 转发规则 / 应用）。**

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **访问 Service 返回 503，但 Pod 一切正常** | Pod `1/1 Running` | **Service 的 selector 没匹配上 Pod 的 label**（拼写/大小写/值不同）→ 端点清单为空 | `kubectl get endpoints <svc>` 看是否空；对比 `kubectl get svc -o jsonpath='{.spec.selector}'` 与 `kubectl get pods --show-labels` | **部署后必查 endpoints**；用同一份 label 定义（模板生成，别手抄） |
| 同上 | Pod `0/1`（没 Ready） | **就绪探针没过** → 未就绪的 Pod 不进端点清单 | `kubectl describe pod` 看 Probe 那一行；`logs` 看应用是否真能服务 | 就绪探针路径要真的反映"能接流量"；慢启动加 `startupProbe` |
| 同上 | Pod `Running` 但 `endpoints` 有 IP | **端点有了但后端服务异常**（应用崩、端口没监听） | `kubectl logs <pod>`；进 Pod `wget localhost:<port>` | 应用自身健康检查 + 容器内自测 |
| **连接被拒（connection refused）** | endpoints **有** IP | **`targetPort` 与容器实际端口不一致** → 连上 Pod 但没人监听 | 对比 `kubectl get svc -o jsonpath='{.spec.ports[*].targetPort}'` 与容器 `containerPort` / `ss -lntp` | **用命名端口**（`targetPort: http`）而不是写死数字 |
| 同上 | endpoints 有 IP | 命名端口**名字对不上** | `kubectl get pod -o jsonpath='{.spec.containers[*].ports[*].name}'` 对比 Service 的 targetPort | 命名端口两侧**严格同名字符串** |
| **`nslookup` 报 `could not resolve host`** | — | **跨命名空间用了短名**（`user-svc` 而不是 `user-svc.other-ns`） | 用 `user-svc.other-ns.svc.cluster.local` 或 `user-svc.other-ns` | 跨 ns 一律用 `<svc>.<ns>` 写法；写进配置文件时用全限定名 |
| 同上 | CoreDNS Pod `CrashLoopBackOff` | 集群 DNS 挂了 | `kubectl -n kube-system get pods -l k8s-app=kube-dns` | 部署后跑一次 DNS 自检 |
| **Headless Service 拿不到 IP** | `CLUSTER-IP` 显示 `None` | **Headless 故意不给 VIP**，它靠 DNS 返回 Pod IP | 别去 `curl <clusterIP>`；用 `nslookup <headless-svc>` 看是否返回多个 Pod IP | 需要 VIP 就别用 Headless；需要 VIP 又要 StatefulSet → **建两个 Service** |
| 同上 | DNS 返回**空** | **Headless 的 DNS 只返回就绪的 Pod**；Pod 还没 Ready | 看 Pod 的就绪状态；`publishNotReadyAddresses: true` 可让未就绪的也返回 | 有状态服务启动期依赖 → 显式开 `publishNotReadyAddresses` |
| **NodePort 端口冲突** | apply 报错 | 手动指定的 `nodePort` 被别的 Service 占了 | `kubectl get svc -A -o wide` 后按端口号找占用者；换一个 | 不手动指定，让系统分配 |
| **应用拿到的源 IP 是节点 IP** | — | `externalTrafficPolicy` 默认 `Cluster` → 跨节点转发要 SNAT | 改成 `externalTrafficPolicy: Local`（LoadBalancer/NodePort 场景） | 需要真实 IP 就显式写 `Local` 并配好 LB 健康检查 |
| **`internalTrafficPolicy: Local` 后部分节点请求失败** | — | **本节点没有该 Service 的 Pod** → 不转发 | 每个节点都要有副本（DaemonSet 天然满足）；或改回 `Cluster` | 用 `Local` 前先确认副本分布 |
| **LoadBalancer 一直 `pending`** | `EXTERNAL-IP: <pending>` | 环境里**没有云厂商控制器**去创建 LB（本地 kind / 裸机） | 本地改用 NodePort 或 port-forward；生产用 ACK 即可 | 本地环境不要用 LoadBalancer 做实验 |
| **客户端一直连到同一个 Pod（负载不均）** | — | `sessionAffinity: ClientIP` + 大量用户共用 NAT 出口 IP | 改回 `None`；或缩短 `timeoutSeconds` | 慎用 ClientIP；优先做应用无状态化 |
| **Pod 下线后客户端还连着它** | `Terminating` | ClientIP 粘滞（默认 10800s）**把客户端钉在老 Pod** | 调小 `timeoutSeconds`；或改 `None` | 有优雅停机需求的场景别开 ClientIP |
| **滚动更新时偶发 502** | — | 端点 `terminating` 但 `serving` 仍为 true，**流量兜底打到了正在销毁的 Pod** | 加 `preStop` + 足够的 `terminationGracePeriodSeconds`，让容器**先停止接新请求再退出** | 优雅停机三件套（exec 形式 ENTRYPOINT + preStop + grace period） |
| **`kubectl get endpoints` 只看到部分 Pod** | — | 单个 Endpoints 对象**最多存 1000 个后端**，超出被截断（注解 `over-capacity: truncated`） | **改看 `kubectl get endpointslice`**（一个 Service 对应多个切片） | 大规模服务一律用 endpointslice 排查 |
| **`kubectl get endpointslice` 漏了后端** | — | 一个 Service 对应**多个** EndpointSlice，只 `get` 一个当然漏 | 用标签找全：`kubectl get endpointslice -l kubernetes.io/service-name=<svc>` | 排查时永远带标签查 |
| **Service 名字 apply 报错** | — | Service 名必须是 **RFC 1123 标签**（不能有点号、不能大写） | 改成 `my-service` 这种形式 | 命名统一用中划线 |
| **改了 Service 的 selector，流量瞬间变了** | — | Service 的 selector **可以改**，改完端点清单立刻重算 | 谨慎修改；生产建议走 GitOps | selector 变更纳入变更评审 |
| **ClusterIP `ping` 不通** | — | **ClusterIP 是虚拟 IP，节点上没有这个地址**，ping 不通是正常的 | 改用 `curl`/`wget` 测 | 判断 Service 用 curl，不用 ping |

**503 排查的标准动作（背下来）**：

```bash
kubectl get svc <svc> -o yaml                # ① selector 和 targetPort 对不对
kubectl get endpoints <svc>                  # ② 端点清单空不空
kubectl get endpointslice -l kubernetes.io/service-name=<svc> -o yaml   # ③ ready 条件
kubectl get pods --show-labels               # ④ Pod 的真实 label
kubectl describe pod <pod> | grep -A5 Readiness   # ⑤ 就绪探针详情
kubectl exec -it <pod> -- ss -lntp           # ⑥ 容器里到底有没有在监听
```

> **①→②→③ 三步能把 90% 的 Service 问题定位掉。** 第 ② 步的输出直接二分：**空 = selector/Ready 问题；非空 = targetPort/应用问题。**

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/services-networking/service/` | 定义 Service、Service 类型（ClusterIP/NodePort/LoadBalancer/ExternalName）、无头服务、虚拟 IP 寻址机制、流量策略、会话的黏性、应用协议 | ✅ 2026-10-24 |
| S2 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/architecture/` | kube-proxy 职责、集群 DNS 插件、EndpointSlice 控制器、CNI 与 kube-proxy 的分工 | ✅ 2026-10-24 |
| S3 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/workloads/pods/` | Pod 联网、Pod IP 唯一性、Pod 内容器共享网络名字空间 | ✅ 2026-10-24 |
| S4 | 官方 | `kubernetes.io/docs/reference/using-api/` | Service v1 API 字段默认值交叉验证（type/clusterIP/sessionAffinity/externalTrafficPolicy/internalTrafficPolicy/targetPort/protocol/healthCheckNodePort/timeoutSeconds） | ✅ 2026-10-24 |
| S5 | 官方（中文） | `kubernetes.io/zh-cn/docs/reference/kubectl/quick-reference/` | `kubectl port-forward`（svc/deploy）、`kubectl expose`、`kubectl get`、`kubectl logs --previous`、`kubectl debug` | ✅ 2026-10-24 |
| S6 | 官方（中文） | `kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-pods/` | Pod 排查标准动作 | ✅ 2026-10-24 |
| S7 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/workloads/` | 工作负载控制器总览（StatefulSet 与 Headless Service 的关系） | ✅ 2026-10-24 |
| S8 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/workloads/controllers/deployment/` | 发布与端点就绪的关系（就绪探针 → available） | ✅ 2026-10-24 |
| S9 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 托管版的 LB/NodePort/eBPF 实践差异 | ✅ 2026-10-24 |
| S10 | 云厂商 | `cloud.tencent.com/document/product/457` | TKE 上 Service 与 CLB 的实践形态 | ✅ 2026-10-24 |

> **S4 的用法**：字段默认值以官方 **Service v1 API 参考** 为准（`using-api/` 页面是 API 参考的官方入口，本篇按 `using-api/` + "Service v1" 章节标注）。**本篇没有引用任何 09-参考索引之外的 URL。**
> **⚠️ 单源待补**：**EndpointSlice 的独立官方章节**（`EndpointSlices` 概念页，讲 100 端点/切片、1000 上限、`serving`/`terminating`/`ready` 三个 conditions、Endpoints 镜像特性废弃）在 09-参考索引中**没有对应条目**。本篇相关表述以 S1（Service 页的 EndpointSlice 描述）与 S2（架构页的 EndpointSlice 控制器）为依据，**知识点已标 `⚠️ 单源待补`**，建议后续把 EndpointSlice 章节补进索引。
> **⚠️ 待验证**：`publishNotReadyAddresses` 的**显式默认值**——Service v1 API 参考未写明默认 false，本篇按"未显式声明即不生效（false）"表述，**建议本地 `kubectl explain service.spec.publishNotReadyAddresses` 复核**。
> **⚠️ 待验证**：IPVS 的具体调度算法列表（rr/wrr/lc/sh 等）属 **IPVS 内核模块能力**，非 K8s 官方文档内容，本篇按内核能力表述。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入：一份 Service 清单（`type` / `selector` / `ports`）。输出：一个"名字稳定、能负载均衡、能剔除非就绪后端"的访问入口。中间经过：**Service 控制器**（selector → 筛出就绪 Pod → 写 EndpointSlice）→ **kube-proxy**（watch EndpointSlice → 写节点转发规则）→ **CoreDNS**（名字 → ClusterIP）。**三层各管一段，任一层坏了症状不同。**
2. **能故意改坏再倒推吗？** —— 四个可执行的破坏实验：
   - **实验 A（selector 写错 → 503）**：把 Service 的 selector 改成 `app: day2-web-typo`（故意拼错），`kubectl apply` 后 `kubectl get endpoints day2-web`。**现象：ENDPOINTS 列为空。** 倒推原因：selector 是等值匹配，拼错的标签匹配不到任何 Pod → 端点清单空 → 包被丢弃 → 503。**修法：改回正确 label。**
   - **实验 B（targetPort 写错 → 连接被拒）**：把 `targetPort` 从 `http` 改成 `9999`（不存在的端口），`kubectl get endpoints`。**现象：端点清单**依然有 2 个 IP**（因为 selector 是对的）。**倒推原因：两级匹配的第一级成功、第二级失败 —— Pod 匹配上了，但没人监听 9999 → connection refused。** **这一步做完你就彻底分清了 selector 错和 targetPort 错的症状差异。**
   - **实验 C（跨 ns 短名解析失败）**：`kubectl create ns other`，在 other 的 Pod 里 `nslookup day2-web` → 失败；再 `nslookup day2-web.default` → 成功。**倒推原因：`/etc/resolv.conf` 的 search 域由 kubelet 按 Pod 所在命名空间注入，短名只在本 ns 解析得到。**
   - **实验 D（Headless 不给 VIP）**：对 `day2-web-headless` 执行 `kubectl get svc` 看到 `CLUSTER-IP: None`，然后在 Pod 里 `nslookup day2-web-headless`。**现象：返回多个 Pod IP 而不是单个 VIP。** 倒推原因：Headless 不分配虚拟 IP、kube-proxy 不处理它，靠 DNS 记录端点 IP。**同时 `curl` 那个 ClusterIP 一定失败——因为它压根不存在。**
3. **能画上下游吗？** —— 画出「应用调 `http://day2-web.default.svc.cluster.local` → 容器内 resolv.conf 的 search 域补全 → CoreDNS 查到 ClusterIP → TCP SYN 到 VIP → 目标节点 kube-proxy 的 iptables/IPVS 规则匹配 VIP:port → DNAT 成某个就绪 PodIP:targetPort → Pod 收到请求」，并标出每一步失败会看到什么：**DNS 解析失败（段③）/ 503 或 connection refused（段②，规则层）/ 端点清单空（段①，控制器层）/ 连上但应用报错（应用层）**。

---

## 9. 常用运维命令（Service / 网络排障速查）

> **本节能做什么**：L4 讲的是 Service 怎么把流量导到 Pod，本节把**网络连通性排障的高频命令**按场景归类——端点（Endpoint）校验、端口转发临时连通、DNS 与网络策略核查，以及"Service 不通"的实战排障。**Endpoint 为空是 90% Service 故障的根因。**

### ① Service / Endpoint 端点校验（第一入口）

```bash
kubectl get svc -n prod -o wide                        # 看类型/ClusterIP/端口/外部 IP
kubectl describe svc web -n prod                       # 看 Selector / Endpoints / Type / 事件
kubectl get endpoints web -n prod                      # 看后端 Pod IP:端口 列表（最关键）
kubectl get endpointslices -n prod                     # v1.21+ 端点分片（大规模性能优化）
kubectl get pods -n prod -l app=web -o wide            # 交叉验证 selector 真能选中 Pod 且 Ready
```

> **实战价值**：Service 不通，**第一眼看 `endpoints` 是否为空**。空 = selector 没匹配到 Ready 的 Pod（label 拼错 / Pod 没就绪），和 apiserver 没关系。

### ② 端口转发与临时连通测试

```bash
kubectl port-forward svc/web 18080:80 -n prod          # 本地映射 Service（临时调试）
kubectl port-forward pod/<pod> 18080:80 -n prod        # 直连某个 Pod
kubectl port-forward deploy/web 18080:80 -n prod       # 直连 Deployment 选中的某个 Pod
kubectl exec -it <pod> -n prod -- curl -s http://web:80   # 进容器测服务间调用
```

> **实战价值**：`port-forward` 是"绕过 Ingress/SLB 直连应用本身"的分层定位工具——先确认应用本身能响应，再往上去查 Service/Ingress/网络策略。

### ③ DNS 与网络策略核查

```bash
kubectl run dnsutils --rm -it --image=registry.k8s.io/e2e-test-images/jessie-dnsutils:1.3 -n prod -- nslookup web.prod.svc.cluster.local
kubectl exec <pod> -n prod -- cat /etc/resolv.conf     # 看 Pod 内 DNS 配置（search 域）
kubectl get networkpolicy -n prod                      # 列出生效的网络策略
kubectl describe networkpolicy <np> -n prod            # 看入站/出站规则（是否被策略挡住）
```

> **实战价值**：服务间调用超时先分两层——`nslookup` 验 DNS 是否能解析，再看 NetworkPolicy 是否把流量挡了（很多"通不了"其实是策略把 Pod 隔离了）。

### ④ 排障实战：访问 Service 返回 503 / 连不上

```bash
kubectl get endpoints web -n prod                # 空 → 后端没有就绪 Pod
kubectl get pods -l app=web -n prod -o wide      # Pod Ready 吗？RESTARTS 异常吗？
kubectl describe svc web -n prod                 # Selector 与后端 Service 对得上吗？
# 现象：endpoints 空 + Pod 非 Ready → 修 Pod（探针/镜像）；endpoints 有但 503 → 应用层错误
```

> **读法**：503/连接拒绝按"端点 → Pod 就绪 → 应用"三层定位。Endpoint 空是 Service 层问题（selector/labels），Endpoint 有但报错是应用层问题，别混淆。

---

**下一篇**：[`L5-Ingress与配置管理`](L5-Ingress与配置管理.md)（**Ingress 只是一份路由配置 · pathType 三种取值 · 注解是厂商扩展 · ConfigMap 三种消费方式 · base64 不是加密**） · **上一篇**：[`L3-Deployment与滚动更新`](L3-Deployment与滚动更新.md) · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md)
