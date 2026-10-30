# L4 · Loki 日志管道（从"能查日志"到"日志管道不出事"）

> **本篇定位**：把 `kubectl logs` 这种"现捞"升级成**集中式、可回溯、可关联**的日志管道。Loki 是 Grafana 生态里最像 Prometheus 的日志系统——**用标签查日志**。但它有一条**头号纪律**：**标签基数必须控制**。本篇会把这条纪律讲透，因为它决定你上线的是一条日志管道还是一颗定时炸弹。
> **前置**：`L3-Grafana-仪表盘与告警`（知道数据源/看板/变量怎么用）+ `L1-Prometheus-数据模型与采集`（理解"标签"这个概念在 Prometheus 里意味着什么）。
> **学完能做什么**：说清日志与指标的本质差异如何决定 Loki 的设计；画出 Loki 的写入链路与查询链路；写出正确的 LogQL（流选择器 + 行过滤 + 解析器 + 格式化）；判断该用 Loki 还是 Elasticsearch；独立设计一套标签方案并守住基数；把 Promtail 迁到 Grafana Alloy。
> **在 21 天里**：D13（Grafana 大屏 + Loki 日志管道）的日志侧，D16（可观测性·日志，日志统一采集是 4/12 JD 的硬要求）的理论底座。
> **环境/版本基准**：Loki 组件名、部署模式、标签规则、多租户语义以 **官方 `grafana.com/docs/loki/latest/`** 为准（写作日期 2026-10-24，各路径实测 200）。**Promtail 的 EOL 公告**取自官方 `send-data/promtail/` 页。**本篇未在任何集群执行 Loki / Promtail / Alloy 命令**，示例为可复制的配置与查询。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L4 Loki 日志管道
│
├─ 1. 概念
│   ├─ 1.1 日志与指标的本质差异（低基数 vs 高基数）
│   ├─ 1.2 这一条差异如何决定 Loki 的全部设计取舍
│   ├─ 1.3 Loki 的定位：像 Prometheus 一样用标签查日志
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 组件全景：distributor / ingester / querier / query-frontend / scheduler / ruler / compactor / index-gateway
│   ├─ 2.2 写入链路 7 步 与 查询链路 9 步
│   ├─ 2.3 日志流与标签模型：stream 是什么，标签为什么决定查询性能
│   ├─ 2.4 LogQL 语法：流选择器 + 行过滤 + 标签过滤 + 解析器 + 格式化
│   ├─ 2.5 行过滤操作符 |= != |~ !~ 的区别
│   ├─ 2.6 解析器：json / logfmt / regexp / pattern / unpack
│   ├─ 2.7 label_format 与 line_format
│   ├─ 2.8 推送方式：Alloy（Promtail 已 EOL）与 SDK 直推
│   ├─ 2.9 部署模式：monolith / simple-scalable / microservices
│   ├─ 2.10 存储：chunk 与 index，为什么 Loki 2.x 起索引在对象存储里
│   ├─ 2.11 多租户：X-Scope-OrgID
│   ├─ 2.12 与 Grafana 的集成
│   ├─ 2.13 Loki Ruler：日志告警
│   └─ 2.14 标签基数纪律（头号事故源）
│
├─ 3. 动手
│   ├─ 3.1 起一套 Loki（monolith）+ 本地数据源
│   ├─ 3.2 设计标签方案（先设计，再采集）
│   ├─ 3.3 写 LogQL：三级过滤 + 解析 + 格式化
│   ├─ 3.4 验证管道通不通（删 Pod 仍能查到）
│   ├─ 3.5 常用运维命令（按场景）
│   └─ 3.6 运维排障实战（场景串命令）
│
├─ 4. 逐字段表
│   ├─ 4.1 LogQL 过滤操作符表
│   ├─ 4.2 Loki 部署模式对照表
│   └─ 4.3 Loki 配置关键项表（limits_config / ruler / storage）
│
├─ 5. 横向对比
│   ├─ 5.1 Loki vs Elasticsearch vs Fluentd vs Fluent Bit
│   ├─ 5.2 Promtail vs Alloy vs SDK 直推
│   ├─ 5.3 Loki vs Prometheus（索引方式的差别）
│   └─ 5.4 Loki vs Elasticsearch 的场景分工
│
├─ 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 → 怎么防）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 日志与指标的本质差异

**这一节是全篇的地基。** Loki 的所有设计、所有取舍、所有事故，都从这一条差异推出来。

| 维度 | 指标（Prometheus） | 日志（Loki / ES） |
|---|---|---|
| **一条数据的含义** | 一个**数值**（counter / gauge / histogram bucket） | 一行**原始文本** |
| **同一时刻会有多少条** | **有限**：指标名 × 标签组合 × 实例数，通常**几百到几千** | **海量**：一次请求可能打 5 行日志，一个集群一天**亿行** |
| **维度（标签）的取值** | **低基数**：`method` ∈ {GET, POST}、`status` ∈ {200, 500} | **高基数**：request_id、user_id、trace_id、URL 全路径——**几乎每个值都不同** |
| **能否预先聚合** | **能**（counter 求和、histogram 求分位） | **不能**（每行都是独立事实，聚合了就丢失上下文） |
| **存储成本** | 低（一条时间序列压缩后很小） | 高（原始文本 × 亿行） |
| **典型保留期** | 长（数月～数年） | 短（7～30 天） |
| **回答什么问题** | **有没有问题**（趋势、比例、分位） | **为什么有问题**（具体哪一行、什么参数、什么异常） |

**"低基数 vs 高基数"是分水岭**：

```
指标：{method="GET", status="200"}      ← 标签组合有限，可枚举
日志：{trace_id="a3f9...", user_id="8821"} ← 标签组合几乎无限
```

**推论（这一条能直接答面试题）**：

> **如果把 request_id 打成日志的标签，每条日志都会变成一个独立的"流"，索引里会多出和日志条数一样多的条目——索引体积会追平甚至超过日志本身，查询性能崩塌。**
>
> **所以 Loki 只索引标签，不索引日志正文；标签必须是低基数维度；高基数字段留在正文里，查询时用解析器现场提取。**

### 1.2 这一条差异如何决定 Loki 的全部设计取舍

| 设计选择 | 直接原因（回到 1.1 的差异） |
|---|---|
| **只索引标签，不索引正文** | 正文字符串太多，**建全文倒排索引的成本远高于存储成本**；Loki 选择"标签定位 chunk → 读 chunk → 扫描" |
| **日志按"流"组织** | 同一组标签的日志**在时间上连续、内容相似**，压缩率高；按流存 chunk，压缩效率远高于按行存 |
| **查询先收敛标签，再扫正文** | 标签基数低 → 索引小 → **能秒级定位到 chunk**；正文扫描交给 chunk 内的高效过滤（<code>&#124;=</code> 走子串匹配） |
| **用对象存储而不是 ES 集群** | chunk 是**不可变的、顺序追加的**，天然适合对象存储（S3/OSS）；**索引也是**（index shipper） |
| **提供 `structured metadata`** | 高基数字段（trace_id、user_id）**既不想进索引、又想能查** → 存成每条日志的附属元数据，**不建索引但可过滤** |
| **日志保留期短、指标保留期长** | 成本与体积的自然结果 |

**这条因果链讲清楚，Loki 就不再是"一个轻量版 ES"，而是"**为 Kubernetes 场景重新设计的日志系统**"。**

### 1.3 Loki 的定位

> **官方原文**：**"Grafana Loki is a multi-tenant system; requests and data for tenant A are isolated from tenant B."**
> **官方原文**：**"Loki indexes the timestamp and labels, but not the rest of the log line."**

一句话定位：

> **Loki 是一个"用标签查日志"的系统——把日志按标签流组织，只索引标签与时间戳，正文存在对象存储里，查询时先按标签定位再在 chunk 内过滤。**

**三个特征**：

| 特征 | 好处 | 代价 |
|---|---|---|
| **只索引标签** | 存储成本低、索引小、写入快 | **不做全文检索**——想在正文里搜任意字符串，只能"标签收敛 + 扫正文"，**范围大了就慢** |
| **对象存储做后端** | 便宜、无限扩展、容量不紧张 | **写入有延迟**（要落盘才能查到）；**依赖对象存储可用性** |
| **多租户隔离** | 天然支持 SaaS / 多业务线隔离 | **每个请求必须带租户头**，忘了就 401 |

**Loki 不是什么**（必须说清，否则面试会被问倒）：

- ❌ **不是全文检索引擎**。要在日志正文里做任意关键词的**全局**检索、需要相关性打分、需要复杂聚合分析 → **用 Elasticsearch**。
- ❌ **不是指标的替代品**。指标该在 Prometheus。
- ❌ **不是审计留存系统**。等保三级要"调三个月前的操作记录"——Loki 的保留期设计（短期、成本优化）与审计需求相反，**审计留痕要用 ES 或对象存储 + WORM**。

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Log stream | 日志流 | **一组共享完全相同标签集的日志**——Loki 的组织单位 |
| Label | 标签 | 描述日志来源的 **key-value**，**只索引标签**是 Loki 的核心特征 |
| Label cardinality | 标签基数 | **唯一标签组合的数量**——Loki 的头号性能命门 |
| Chunk | 分块 | 一段时间内、一个流的一批日志的**压缩容器**（不可变） |
| Index | 索引 | **目录**：记录每个流的标签集，指向具体 chunk |
| Index shipper | 索引推送机制 | 把索引文件也写进对象存储的适配器（**Loki 2.0 起 GA**） |
| Structured metadata | 结构化元数据 | 挂在**每条**日志上的高基数字段，**不进索引但可过滤** |
| Distributor | 分发器 | 写路径第一站：校验、限流、按一致性哈希路由到 ingester |
| Ingester | 接收器 | 内存里聚合成 chunk，**定期 flush 到对象存储**；查询时提供内存中的近期数据 |
| Querier | 查询器 | 执行 LogQL；同时查 ingester 内存与对象存储，并**去重** |
| Query frontend | 查询前端 | 拆查询、排队、缓存、跨租户公平调度（**无状态**） |
| Query scheduler | 查询调度器 | 更高级的排队与**每租户独立队列**（**无状态**） |
| Ruler | 规则器 | 评估 recording rule 与 alerting rule（**日志告警的来源**） |
| Compactor | 压缩器 | 合并索引文件、执行保留与删除策略（**通常单实例**） |
| Index gateway | 索引网关 | 提供**索引元数据查询**（官方组件名；不是"Indexer"） |
| Pattern ingester | 模式接收器 | 可选，检测并聚合日志模式（**默认关闭**） |
| Tenant | 租户 | 由 `X-Scope-OrgID` 头标识的隔离单位 |
| LogQL | — | Loki 的查询语言，**基于 PromQL** |
| Pipeline stage | 管道阶段 | <code>&#124;=</code> 行过滤 → <code>&#124; json</code> 解析 → <code>&#124; line_format</code> 格式化 |
| Promtail | — | 旧的日志采集 agent，**已于 2026-03-02 EOL** |
| Grafana Alloy | — | Grafana 当前的**日志采集与处理 agent**，**Promtail 的继任者** |
| Push API | 推送 API | Loki 的写入 HTTP 接口（`/loki/api/v1/push`） |

---

## 2. 机制

### 2.1 组件全景

**官方原文**：**"Grafana Loki has a microservices-based architecture... The Grafana Loki design compiles the code for all components into a single binary or Docker image. The `-target` command-line flag controls which component(s) that binary will behave as."**

**关键认知**：**Loki 是"微服务架构 + 单二进制"的混合体**——所有组件编译进同一个二进制，**用 `-target` 参数决定这个进程扮演哪些角色**。这解释了为什么 K8s 里改个 `target` 就能从单体切成分布式。

| 组件 | 职责（官方原文要点） | 在哪条链路上 | 有状态吗 |
|---|---|---|---|
| **Distributor** | **"responsible for handling incoming push requests from clients. It's the first step in the write path"** | 写 | 无状态，**可独立扩缩容** |
| **Ingester** | **"responsible for persisting data and shipping it to long-term storage... and returning recently ingested, in-memory log data for queries"** | 写 + 读 | **有状态**（内存 chunk + WAL） |
| **Querier** | **"responsible for executing Log Query Language (LogQL) queries"** | 读 | 无状态 |
| **Query frontend** | **"an optional service providing the querier's API endpoints and can be used to accelerate the read path"** | 读 | **无状态**，官方建议多副本（通常两个即可） |
| **Query scheduler** | **"an optional service providing more advanced queuing functionality than the query frontend"** | 读 | **无状态**，每租户独立队列 |
| **Ruler** | **"manages and evaluates rule and/or alert expressions"** | 后台 | 有状态（规则分配环） |
| **Compactor** | **"used by 'shipper stores' to compact the multiple index files produced by the ingesters and shipped to object storage into single index files per day and tenant"** | 后台 | 有状态（任务） |
| **Index gateway** | **"responsible for handling and serving metadata queries"** | 读 | 有状态（索引分片） |
| **Pattern ingester** | **"scans the logs to detect and aggregate patterns"** | 后台 | 有状态，**默认禁用** |
| **Bloom planner/builder/gateway** | 过滤加速（实验特性） | 读 | **官方标注为实验性，不提供 SLA** |

**Distributor 的六件事**（官方原文拆解）：

1. **验证数据**：标签是否是合法的 Prometheus label；时间戳是否过旧/过新；日志行长度。
2. **预处理**：**对标签做规范化与排序**，使标签集有确定性的哈希结果。
3. **租户级限流**：集群级租户限额**除以当前活跃的 Distributor 数量**。
4. **一致性哈希路由**：用 **tenant ID + label set** 计算哈希，按 replication factor 选多个 ingester。
5. **等待写入确认**：达到 quorum 才返回。官方原文：**"A quorum is defined as `floor( replication_factor / 2 ) + 1`."**
6. **无状态**：**"A load balancer must sit in front of the distributor to properly balance incoming traffic to them."**

> **distributor 前面必须有负载均衡器**——因为限流是"集群总额额 ÷ 活跃 distributor 数"，有 LB 才能算得对，而且能防止验证流量压垮关键 ingester。

**Ingester 的可靠性设计**（官方原文）：

> **"Loki is usually configured with a Write Ahead Log ... which can be *replayed* on restart as well as with a `replication_factor` (usually 3) of each log to mitigate this risk."**

- **WAL**（预写日志）→ 进程挂了重启能**重放**，不丢数据。
- **replication_factor 通常为 3** → 每份日志写 3 份，配合 quorum 容忍 1 个 ingester 挂掉。
- **进程异常退出时，尚未 flush 的数据可能丢失**（WAL 未开启时）——**这是 Loki 唯一的硬性数据丢失窗口**。
- Ingester 状态机：`PENDING` → `JOINING` → `ACTIVE` → `LEAVING` → `UNHEALTHY`。
- **Handoff 已弃用**（官方原文："Handoff is deprecated behavior mainly used in stateless deployments of ingesters, which is discouraged."）。

**Compactor 的部署建议**（官方原文）：**"In a Loki deployment, the compactor service is usually run as a single instance."** —— 因为它做的是"合并索引 + 执行删除"，多实例会重复劳动。

### 2.2 写入链路 7 步 与 查询链路 9 步

**写入链路（官方架构页原文逐条）**：

```
① distributor 收到 HTTP POST（streams + log lines）
② distributor 对每个 stream 做哈希 → 一致性哈希环 → 定位 ingester
③ distributor 把 stream 发给对应 ingester 及其副本（按 replication factor）
④ ingester 收到 stream，为其创建 chunk 或追加到已有 chunk
     （chunk 对「租户 + 标签集」唯一）
⑤ ingester 确认写入
⑥ distributor 等待多数（quorum）ingester 确认
⑦ 达到 quorum → 返回 2xx；否则返回 4xx / 5xx
```

**查询链路（官方架构页原文逐条）**：

```
① query frontend 收到 HTTP GET（LogQL）
② query frontend 把查询拆成子查询，交给 query scheduler
③ querier 从 scheduler 拉子查询
④ querier 把查询发给所有 ingester（内存数据）
⑤ ingester 返回匹配的内存数据
⑥ querier 对对象存储做惰性加载（若内存数据不足），在存储上跑同一个查询
⑦ querier 遍历所有收到的数据并去重，把子查询结果返回给 query frontend
⑧ query frontend 等所有子查询完成
⑨ query frontend 合并各子查询结果，返回给客户端
```

**这张图直接给出三条排障结论**：

| 现象 | 卡在哪 | 怎么查 |
|---|---|---|
| **写入报 5xx** | ⑥⑦ quorum 没达成 | 看 ingester 是否 `ACTIVE`；`replication_factor` 是否配了奇数；网络是否通 |
| **写入 2xx 但查不到** | chunk 还在 ingester 内存里未 flush，或查询时间窗不对 | 查 chunk 保留时间；拉长时间窗；看对象存储里有没有 chunk 文件 |
| **查询慢** | ② 拆得太碎 / ④ 扫的 ingester 太多 / ⑥ 扫对象存储太多 | **先缩小标签选择器**（最有效）；再看 query frontend 的排队指标 |

**Querier 的去重规则（官方原文）**：**"The querier internally deduplicates data that has the same nanosecond timestamp, label set, and log message."** —— 因为一份日志有多个副本，去重键是 **(纳秒时间戳, 标签集, 日志内容)** 三元组。

**Querier 的查询顺序（官方原文）**：**"Queriers query all ingesters for in-memory data before falling back to running the same query against the backend store."** —— **先查内存（快），不够再查对象存储（慢）**。

### 2.3 日志流与标签模型

**官方原文**：**"A set of log messages which shares all the labels above would be called a log stream."**

**stream = 标签集完全相同的一组日志。** 这是 Loki 的组织单位，也是**索引条目**的单位。

**标签怎么决定查询性能**：

```
标签基数 100      →  索引 100 条    →  查询命中 1 条流  →  扫 1 个 chunk   →  快
标签基数 10 万    →  索引 10 万条   →  查询命中 5000 条流 →  扫 5000 个 chunk →  慢到超时
```

**官方原文（关键警告）**：

> **"High cardinality causes Loki to build a huge index and to flush thousands of tiny chunks to the object store. Loki performs very poorly when your labels have high cardinality. If not accounted for, high cardinality will significantly reduce the performance and cost-effectiveness of Loki."**

**官方给的标签设计原则（原文照录，逐条）**：

| 官方原文 | 含义 |
|---|---|
| **"DO use fewer labels, aim to have 10 - 15 labels at a maximum."** | **最多 10-15 个标签** |
| **"DO be as specific with your labels you can be, the less searching that Loki has to do, the faster your result is returned."** | **标签越具体，扫的东西越少** |
| **"DO create labels with long-lived values, not unbounded values... we want something that has a stable set of values over time – even if there are a lot of them."** | **取值集合要稳定**；**"If just one label value changes, this creates a new stream."** |
| **"DO create labels based on terms that your users will actually be querying on."** | **按用户真实会查的维度设计** |
| **"DON'T create labels for very specific searches (for example, user ID or customer ID) or seldom used searches."** | **禁止把 user_id / customer_id 打成标签** |

**"one label value changes, this creates a new stream"** 这句话是**基数爆炸的机制解释**：

> 一个标签的取值集合是**稳定的**，不代表流的数量稳定。**每个"标签组合"都是一个独立的流、独立的 chunk。**
> 官方给的例子（原文）：日志行带 `job="apache", env="dev", action="GET", status_code="200"` 这样的标签 → **4 行日志变成 4 个独立的流，开始填 4 个独立的 chunk**。
> 官方接着算：**"if we set a label for `ip`... You can quickly have thousands or tens of thousands of streams."**

**默认标签（`service_name`）**：

Loki 会尝试自动填充 `service_name`。官方给的查找顺序（原文照录，取第一个非空值）：

```
service → app → application → app_name → name → app_kubernetes_io_name
→ container → container_name → k8s_container_name → component → workload
→ job → k8s_job_name
```

**都没有 → 填 `unknown_service`。** 这个顺序可以通过 `limits_config` 里的 `discover_service_name` 改。

> **踩坑预警**：Helm 版 Loki 装好后，日志的标签常常叫 **`app_kubernetes_io_name`** 而不是 `app`——因为它来自 Kubernetes 标签 `app.kubernetes.io/name`（点转下划线）。**这不是 bug，是默认行为**。查日志前先确认标签真实名字（第 6 节有专门一条）。

**OTel 默认标签**：用 Alloy 或 OTel Collector 作客户端时，Loki 会把一部分 OTel resource attributes 默认存成 label（点换成下划线），包括 `cloud.region`、`container.name`、`deployment.environment.name`、`k8s.namespace.name`、`k8s.pod.name`、`service.name` 等。官方原文警告：

> **"Because of the potential for high cardinality, `k8s.pod.name` and `service.instance.id` are no longer recommended as default labels."** —— 官方建议新用户**把这两个从 index label 改成 structured metadata**。官方也说明：**"Because Loki has a default limit of 15 index labels"**。

**标签 vs 结构化元数据（怎么选）**：

| | Label（索引标签） | Structured metadata |
|---|---|---|
| **建索引** | **是** | **否** |
| **能作为流选择器** | **能**（`{app="x"}`） | **不能** |
| **能在管道里过滤** | 能 | **能**（需显式引用） |
| **取值基数要求** | **必须低** | **可以高** |
| **典型用途** | namespace、app、env、level | trace_id、request_id、user_id、pod 完整名 |

> **决策规则一句话**：**"这个值你会不会拿它做 `{}` 里的等值匹配？会 → label；不会但要用它过滤 → structured metadata；只是想在日志里看到 → 留在正文。"**

**写入顺序的约束（官方原文）**：

> **"Entries in a given log stream (identified by a given set of label names & values) must be ingested in order, within the default two hour time window. If you try to send entries that are too old for a given log stream, Loki will respond with the error `too far behind`."**

**官方给的解法**：**不同延迟的日志用不同标签拆成不同流**。例如不要只用 `{environment="production"}`，而是拆成 `{environment="production", app="slow_app"}` 和 `{environment="production", app="fast_app"}`，各管各的顺序。

### 2.4 LogQL 语法

**官方原文**：

> **"LogQL queries are in the following format: `{ log stream selector } | log pipeline`"**
> **"The log stream selector is mandatory while the log pipeline is optional."**

```
{ 流选择器 }  |  行过滤  |  标签过滤  |  解析器  |  格式化
   必需        可选      可选       可选      可选
```

**（一）流选择器**（官方原文的四个操作符）：

| 操作符 | 含义 |
|---|---|
| `=` | 标签**恰好等于** |
| `!=` | 标签**不等于** |
| `=~` | 标签**正则匹配** |
| `!~` | 标签**不正则匹配** |

```logql
{service_name="nginx", status="500"}
{namespace=~"prod|staging"}
{app!="debug-tool"}
```

**（二）行过滤（line filter）**——过滤**日志正文**：

| 操作符 | 官方含义 |
|---|---|
| <code>&#124;=</code> | 日志行**包含字符串** |
| `!=` | 日志行**不包含字符串** |
| <code>&#124;~</code> | 日志行**包含正则匹配** |
| `!~` | 日志行**不包含正则匹配** |

```logql
{app="x"} |= "error"
{app="x"} != "healthcheck"
{app="x"} |~ "ERROR|FATAL"
{app="x"} !~ "GET /health"
```

**（三）标签过滤（label filter）**——过滤**解析出来的字段**（必须在解析器之后）：

| 操作符 | 官方含义 |
|---|---|
| `==` 或 `=` | 相等 |
| `!=` | 不等 |
| `>` `>=` `<` `<=` | 数值比较 |
| `and` / `or` | 串联多个条件 |

```logql
{app="x"} | json | level="error" | duration_ms > 1000
{app="x"} | json | status >= 500 and method="POST"
```

**（四）解析器（parser）**——官方列出 5 种：

| 解析器 | 用途 |
|---|---|
| **json** | 日志行是 JSON 对象 |
| **logfmt** | 日志行是 `key=value` 形式 |
| **pattern** | 用 `<label>` 占位符提取固定结构 |
| **regexp** | 用正则提取（可带命名捕获组） |
| **unpack** | 提取 JSON 里的嵌套对象 |

**（五）格式化**：

| 函数 | 作用 |
|---|---|
| `line_format` | **改写日志行本身**（`{{.field}}` 引用字段） |
| `label_format` | **改写标签**（给查询结果加/改标签） |

**官方原文（重要）**：**"Format expressions do not change the underlying source data, only the results that are returned by the query."** —— **格式化只是改显示，不改存储的数据**。

**两类查询**（官方原文）：**Log queries**（返回日志行）与 **Metric queries**（从日志算出数值）。

```logql
# Log query
{app="myapp"} |= "ERROR" != "healthcheck"

# Metric query：算出错误日志速率
sum by (app) (rate({namespace="prod"} |= "ERROR" [5m]))
```

**Metric query 的函数族**（Loki 官方 alert 页给了完整例子）：

| 函数 | 用途 |
|---|---|
| `rate({...}[5m])` | 日志速率（每秒行数） |
| `count_over_time({...}[5m])` | 窗口内命中条数 |
| `sum by (...)` | 聚合 |

**完整示例（官方 alert 页原文给的）**：

```logql
sum(rate({app="foo", env="production"} |= "error" [5m])) by (job)
/ sum(rate({app="foo", env="production"}[5m])) by (job)
> 0.05
```

### 2.5 行过滤操作符 |= != |~ !~ 的区别

**这是本篇最该背下来的一张表。**

| 操作符 | 语义 | 底层 | 性能 | 用在哪 |
|---|---|---|---|---|
| <code>&#124;=</code> | 包含（子串） | **字面量匹配** | **快** | 已知确切关键词：<code>&#124;= "error"</code> |
| `!=` | 不包含（子串） | 字面量匹配 | 快 | 排除噪声：`!= "healthcheck"` |
| <code>&#124;~</code> | 正则匹配 | **正则引擎** | **慢**（比子串慢） | 模式匹配：<code>&#124;~ "ERROR&#124;FATAL"</code> |
| `!~` | 正则不匹配 | 正则引擎 | 慢 | 排除模式：`!~ "GET /health"` |

**关键认知（为什么 `|=` 更快）**：

| | <code>&#124;=</code>（子串） | <code>&#124;~</code>（正则） |
|---|---|---|
| **处理方式** | 固定模式扫描 | **要构造/执行正则状态机** |
| **转义负担** | 无 | **正则有元字符，关键词里有 `.` `*` `(` 要转义** |
| **结论** | **能用 <code>&#124;=</code> 就别用 <code>&#124;~</code>** | 只在真需要模式时才用 |

> **踩坑预警**：**`|= "error."` 里的 `.` 在子串匹配里就是字面点**（因为不是正则）；但写成 `|~ "error."` 时 **`.` 匹配任意字符**——`errorX`、`error1` 都会被匹配上。**从 `|=` 切到 `|~` 时，语义悄悄变了。**

**组合顺序很重要**：**多个行过滤器是从左到右依次应用的**，`|~` 放在 `|=` 之后能缩小后续扫描范围。**便宜的过滤器放前面**。

### 2.6 解析器

| 解析器 | 语法 | 例子 | 适用 |
|---|---|---|---|
| **json** | <code>&#124; json</code> | `{"level":"error","msg":"x","code":500}` | 应用打 JSON 日志（**最常见**） |
| **logfmt** | <code>&#124; logfmt</code> | `level=error msg="x" code=500` | Go 生态、`logrus` 文本格式 |
| **regexp** | <code>&#124; regexp "..."</code> | 带命名捕获 `(?<level>\d+)` | 非结构化文本 |
| **pattern** | <code>&#124; pattern "<ip> - <method> <uri>"</code> | nginx access log 固定格式 | **结构固定但非 KV 的日志** |
| **unpack** | <code>&#124; unpack</code> | 提取 JSON 里的嵌套对象 | 嵌套 JSON |

**pattern 解析器示例（nginx access log 官方社区常用形态）**：

```logql
{container="nginx"}
| pattern "<ip> - <user> [<_>] \"<method> <uri> <_>\" <status> <bytes> \"<referer>\" \"<agent>\""
| status >= 500
```

**解析后的字段怎么用**：

```logql
# 解析出字段后可以过滤
{app="x"} | json | level="error"

# 解析后可以重命名/改写行
{app="x"} | json | line_format "{{.level}} {{.msg}}"

# 解析后可以算指标
sum by (level) (count_over_time({app="x"} | json | level="error" [5m]))
```

> **注意**：`level="error"` 是**解析后的字段过滤**，不是标签过滤。它**不缩小流选择范围**（仍然要扫 `{app="x"}` 的所有流），只是把结果筛掉。**性能上，流选择器（标签）> 行过滤 > 字段过滤**。**能用标签收窄的，绝不要用字段过滤替代。**

### 2.7 label_format 与 line_format

| 函数 | 改什么 | 语法 | 影响查询吗 |
|---|---|---|---|
| `line_format` | **改日志行的显示** | <code>&#124; line_format "{{.level}} {{.msg}}"</code> | 只改**返回结果** |
| `label_format` | **改/加标签** | <code>&#124; label_format new_label="{{.msg}}"</code> | 只改**返回结果** |

**官方原文的例子**（把一条丑陋的 nginx 日志变成可读的）：

```logql
{...} | json | line_format "➡️ {{.request_method}} {{.request_uri}} with HTTP status: {{.status}} "
```

**关键区分**：`line_format` **不改数据本身**，只是**显示时重新拼装**。**同一批日志在不同查询里可以显示成不同样子**，源数据不变。

**`label_format` 的用途**：在查询结果上**派生一个新维度**，用于 group by 或做进一步过滤。**但要注意：它不改底层数据的标签**，所以它**不能用来"假装"缩小流选择范围**。

### 2.8 推送方式：Alloy（Promtail 已 EOL）与 SDK 直推

**这是本篇时效性最强的一节。**

**官方原文（Promtail 页的警告块，照录）**：

> **"Promtail is end of life (EOL) as of March 2, 2026. Commercial support has ended. No future support or updates will be provided. All future feature development will occur in Grafana Alloy."**
> **"Note that the deprecation of Promtail does NOT include the lambda-promtail client."**
> **"If you are currently using Promtail, you must migrate to Alloy or another supported client."**

**官方还提供了迁移工具**：**"The Alloy migration documentation includes a migration tool for converting your Promtail configuration to an Alloy configuration with a single command."**

**三种推送方式对比**：

| 方式 | 怎么工作 | 优点 | 缺点 | 现状 |
|---|---|---|---|---|
| **Promtail** | DaemonSet 常驻每个节点，**读容器日志文件**并推送 | 集群里**应用零改造** | **已 EOL（2026-03-02）** | **只能用于存量迁移** |
| **Grafana Alloy** | 官方当前推荐（Promtail 的继任者） | **配置模型更现代**（River/Alloy 语法）、支持更多信号与处理能力 | **配置要从 Promtail 语法迁移** | **官方推荐** |
| **SDK / 应用直推** | 应用用库**直接调 Loki Push API** | **不需要采集 agent**、**日志里有完整上下文**（trace_id、用户信息） | **要改代码、每种语言一套 SDK** | **现代做法** |

**SDK 直推的两种形态**：

| 形态 | 特点 |
|---|---|
| **用 OTel SDK 推** | 走 **OTLP** 协议推给 Loki（Loki 支持 OTLP 端点），**统一到 OpenTelemetry 体系**（见 L5 篇） |
| **用各语言原生 Logging SDK 推** | 如 Java logback 的 `LokiAppender`、Go 的 `loki-go-client` |

**"SDK 直推才是现代做法"的理由（面试可讲）**：

| 维度 | Agent 采集（Promtail/Alloy） | SDK 直推 |
|---|---|---|
| **日志上下文** | 只有 stdout 里有的东西 | **可以带 trace_id、用户、请求参数等结构化字段** |
| **应用改造** | 零 | 要加依赖 |
| **多集群/多后端** | 一份采集配置要改多遍 | **SDK 里配多个 exporter** |
| **与 OTel 统一** | 采集端是厂商特定（Alloy 是 Grafana 特定） | **完全中立** |

> **现实口径**：**国内大多数团队是 Alloy（继承 Promtail 习惯）做采集，应用侧只在需要结构化上下文时额外用 SDK 推一部分。** 全量 SDK 直推是更彻底但改造量更大的方案。

### 2.9 部署模式

**官方原文（components 页）**：Loki 支持 **all（单体）** / **read、write、backend（简单可扩展）** / **各组件独立（微服务）** 三种。

**（二）simple-scalable 的三组划分**：

| 组 | 组件 | 负责 |
|---|---|---|
| **write** | Distributor、Ingester | 写入链路 |
| **read** | Querier、Query frontend、Query scheduler | 查询链路 |
| **backend** | Compactor、Ruler、Pattern ingester | 后台任务（索引合并、保留删除、规则评估、模式检测） |

**各模式的官方描述**（详见 4.2 对照表）：

- **Monolith（`all`）**：所有组件在一个进程。**部署运维最简单**；组件共享进程，**无法完全独立扩缩容**。适合开发、测试、小规模、资源受限环境。
- **Simple-scalable**：**写、读、后台任务相互隔离**，可分别扩缩容。**生产环境的常见折中**。
- **Microservices**：每个组件独立进程、独立扩缩容、故障隔离最好；但**部署、配置、发现和监控最复杂**。适合大规模、高负载、要求严格资源隔离的生产环境。

**在 K8s 上的实际口径**：

| 场景 | 推荐模式 | 理由 |
|---|---|---|
| **本地学习 / 测试** | **monolith（`all`）** | 一个 Deployment 就完事 |
| **中小生产（绝大多数团队）** | **simple-scalable** | **写压力与查压力天然分离**（大促时查询暴涨不会拖垮写入） |
| **超大规模 / 多集群** | **microservices** | 各组件独立扩缩容 + 故障隔离 |

**为什么 simple-scalable 是 K8s 上的默认答案**（这是面试要讲出来的取舍）：

> **写入和查询的负载曲线完全不同。** 写入量取决于业务流量（相对平稳）；查询量取决于**故障时的排查行为**——**平时没人查，一出事所有人同时查**。这叫"**惊群效应**"。
> **如果写入和查询在同一个进程组里，一次大范围排障就能把 ingester 的内存和 CPU 打满，进而拖垮写入——排障把生产打挂。**
> **simple-scalable 的 write / read 分组就是为了隔离这个风险。**

### 2.10 存储：chunk 与 index

**官方原文（architecture 页）**：

> **"Grafana Loki stores all data in a single object storage backend... This mode uses an adapter called **index shipper** (or short **shipper**) to store index (TSDB or BoltDB) files the same way we store chunk files in object storage. This mode of operation became generally available with Loki 2.0"**

**官方原文（storage 页）**：

> **"Grafana Loki needs to store two different types of data: **chunks** and **indexes**."**
> **"The **index** stores each stream's label set and links them to the individual chunks."**

| 数据类型 | 是什么 | 存在哪 | 官方定位 |
|---|---|---|---|
| **Chunk** | 一段时间内、一个流的日志的压缩容器（**不可变**） | **对象存储** | 数据主体 |
| **Index** | **目录**：每个流的标签集 → chunk 引用 | **对象存储**（Loki 2.0 起，index shipper） | 定位数据 |
| **Bloom blocks** | 过滤加速结构 | 对象存储 | **实验特性**（Accelerated Search） |

**两种索引格式**（官方原文）：

| 格式 | 官方说明 | 状态 |
|---|---|---|
| **TSDB** | Prometheus 维护者开发的时间序列索引格式，**可扩展**，新存储特性只能用它 | **✅ 推荐（Loki 2.8+）** |
| **BoltDB** | Go 的低层事务型 KV 存储 | **⚠️ 已弃用**（Loki 2.0–2.7.x 用它） |

**已弃用的索引/块存储**：Amazon DynamoDB、Google Bigtable、Apache Cassandra（索引与块存储都弃用）、BoltDB 单机（**集群模式下不可用**）。

**块存储支持情况**（官方原文分类）：

| 分类 | 后端 |
|---|---|
| **✅ 支持且推荐** | Amazon S3、Google Cloud Storage、Microsoft Azure Blob、IBM Cloud Object Storage、Baidu Object Storage、**Alibaba Object Storage Service (OSS)** |
| **⚠️ 支持但不建议生产用** | Filesystem、**S3 兼容存储（如 MinIO）** |
| **❌ 已弃用** | Amazon DynamoDB、Google Bigtable、Apache Cassandra |

> **国内选型**：**阿里云 OSS** 在官方推荐列表里（对国内团队是好消息）。但注意官方把 **MinIO 列为"支持但不建议生产用"**——用 MinIO 要评估清楚。

**S3 兼容存储需要的权限**（官方原文，OSS 同理）：`s3:ListBucket`、`s3:PutObject`、`s3:GetObject`、`s3:DeleteObject`（**跑 boltdb-shipper 的 compactor 时需要**）。

**对象存储权限是 Loki 上生产最常见的"写入失败"原因**——见第 6 节。

**Chunk 的内部结构**（官方架构页给了完整格式，这里只讲要点）：chunk 由 **header**（magic number / version / encoding）、**blocks**（每块一条日志 + 校验和）、**metas**（条目数、最小最大时间戳、偏移与长度）、**structured metadata**（标签名值，非重复字符串）、**footer** 组成。**`mint`/`maxt` 记录这个 chunk 的最小/最大 Unix 纳秒时间戳**——查询时靠它裁剪。

### 2.11 多租户

**官方原文（multi-tenancy 页）**：

> **"Grafana Loki is a multi-tenant system; requests and data for tenant A are isolated from tenant B. Requests to the Loki API should include an HTTP header (`X-Scope-OrgID`) that identifies the tenant for the request."**
> **"Loki defaults to running in multi-tenant mode. Multi-tenant mode is set in the configuration with `auth_enabled: true`, or with the equivalent command line flag `-auth.enabled` (default `true`)."**

| 场景 | `auth_enabled` | 行为 |
|---|---|---|
| **默认** | `true` | **请求必须带 `X-Scope-OrgID`**，否则 **401 + `no org id`** |
| **单租户** | `false` | 不需要该头，**租户 ID 固定为 `fake`**（会出现在索引和 chunk 里） |

**租户 ID 的规则**（官方原文）：

| 规则 | 内容 |
|---|---|
| **长度** | **不超过 150 字节**；官方建议 **"20 bytes is usually enough"** |
| **允许字符** | 字母数字，以及 `!` `-` `_` `.` `*` `'` `(` `)` |
| **禁止** | `.` 和 `..`（安全原因，官方原文单列警告）；**禁止冒号 `:`** |

**冒号这条要特别注意（官方警告原文）**：

> **"Do not include a colon (`:`) in the `X-Scope-OrgID` header. A colon is not a valid tenant ID character. Most Loki components stop reading the tenant ID at the first colon and ignore the rest of the value, and the ignored part is not checked against the rules on this page. Other components read the whole header value instead, so a header that contains a colon can produce inconsistent results."**

**不同组件读法不一致 → 同样的头在不同组件表现不同**。这是最阴险的一类坑。

**跨租户查询**（官方原文）：

| 项 | 内容 |
|---|---|
| **开关** | `querier.multi_tenant_queries_enabled: true` / `-querier.multi-tenant-queries-enabled`（**默认 false**） |
| **多租户头格式** | <code>X-Scope-OrgID: A&#124;B</code>（**竖线分隔**） |
| **只支持查询端点** | `GET /loki/api/v1/tail`、`POST /loki/api/v1/push`、`POST /otlp/v1/logs` **传多个租户会返回 HTTP 400** |
| **`__tenant_id__` 标签** | 查询跨多租户时，Loki 会给结果**加上**这个标签；**单租户查询时不会出现**。若日志流里本来就有 `__tenant_id__`，会被加上 `original_` 前缀 |

**官方给的跨租户过滤示例**：

```logql
{app="foo", __tenant_id__=~"a.+"} | logfmt
```

**官方明确说不支持的写法**：

```logql
{app="foo"} | __tenant_id__="1" | logfmt    # ❌ 不支持
```

> **注意**：`__tenant_id__` 是**查询结果里的虚拟标签**，**不能作为管道阶段的过滤条件**。

### 2.12 与 Grafana 的集成

**加 Loki 数据源**（官方 `datasources/loki/` 路径）：

| 字段 | 集群内填什么 | 说明 |
|---|---|---|
| Name | `Loki` | — |
| Type | Loki | — |
| URL | `http://loki-loki-stack-gateway:3100`（或你部署的 gateway / query-frontend 的 Service） | **端口 3100 是 Loki 的默认 HTTP 端口**（官方示例中的 `url = "http://localhost:3100/loki/api/v1/push"` 即为证） |
| Access | **Server (proxy)** | Loki 的 Push API 同样建议走 proxy |
| **Tenant ID (X-Scope-OrgID)** | 多租户时**必须填** | **不填 → 查不到数据** |

**多租户下最常见的"查出来是空的"**：**Grafana 的 Loki 数据源里 Tenant ID 填了（写端用了 A），查询端没填或填了 B → 看到的是 B 的数据，自然是空的。**

**集成后能干什么**：

| 能力 | 说明 |
|---|---|
| **Logs 面板** | Grafana 里主力日志视图 |
| **Explore 面板** | 临时查日志、调 LogQL |
| **Logs Drilldown** | 自动生成初始可视化（官方 logs 页提到） |
| **指标 → 日志跳转** | 在指标告警里挂上 Loki 查询链接，点进去直接是那个时间窗的日志 |
| **Grafana Alerting 查 Loki** | GMA 规则可以直接查 Loki 数据源（Grafana-managed 规则支持） |

**"指标 → 日志"跳转怎么配**（这是排障链路的关键一环）：

```
业务面板（QPS/错误率）→ 点告警 → 通知里带一条链接：
  http://grafana/explore?left={"datasource":"Loki","queries":[{"expr":"{app=\"$app\"} |= \"ERROR\"","refId":"A"}]}
                                                  ↑ 时间范围自动带上当前窗口
```

**告警通知里同时带"看板链接"和"日志链接"**——值班点哪个都有现场，这是缩短 MTTR 的具体做法。

### 2.13 Loki Ruler：日志告警

**官方原文**：

> **"Grafana Loki includes a component called the ruler. The ruler is responsible for continually evaluating a set of configurable queries and performing an action based on the result."**

**关键点**：**Loki 的告警规则与 Prometheus 兼容，但表达式用 LogQL**（官方原文："Loki alerting rules are exactly the same, except they use LogQL for their expressions."）。

**官方给的告警规则示例**：

```yaml
groups:
  - name: should_fire
    rules:
      - alert: HighPercentageError
        expr: |
          sum(rate({app="foo", env="production"} |= "error" [5m])) by (job)
            /
          sum(rate({app="foo", env="production"}[5m])) by (job)
            > 0.05
        for: 10m
        labels:
          severity: page
        annotations:
          summary: High request latency
```

**官方给的"凭据泄露检测"例子**（说明日志告警的独特价值——**有些事件在指标里根本没有**）：

```yaml
- name: credentials_leak
  rules:
    - alert: http-credentials-leaked
      annotations:
        message: "{{ $labels.job }} is leaking http basic auth credentials."
      expr: 'sum by (cluster, job, pod) (count_over_time({namespace="prod"} |~ "http(s?)://(\\w+):(\\w+)@" [5m]) > 0)'
      for: 10m
      labels:
        severity: critical
```

**Loki Ruler 怎么把告警发出去**（官方原文）：**Ruler 把 firing 的告警发到 `ruler.alertmanager_url` 指向的 Alertmanager**。

```yaml
ruler:
  alertmanager_url: http://alertmanager-operated:9093
  enable_alertmanager_v2: true   # 官方注明：Loki 3.2.0 起默认为 true
  enable_api: true
  enable_sharding: true
  rule_path: /tmp/rules          # 官方标称默认 /rules
  storage:
    gcs:
      bucket_name: <LOKI_RULES_BUCKET>
```

**Recording rule 与 remote_write**（官方原文）：

> **"A recording rule pre-computes frequently used or computationally expensive queries, and saves the results as a new time series metric."**

```yaml
name: NginxRules
interval: 1m
rules:
  - record: nginx:requests:rate1m
    expr: |
      sum(
        rate({container="nginx"}[1m])
      )
    labels:
      cluster: "us-central1"
```

**官方原文（关键路径区分）**：**recording rule 的输出走 remote-write；alerting rule 的 firing 告警走 Alertmanager。这是两条完全不同的路径。**

官方列出的 remote-write 兼容后端：**Prometheus `>= v2.25.0`、Grafana Mimir、Thanos Receiver**。

```yaml
ruler:
  remote_write:
    enabled: true
    clients:
      prometheus:
        url: http://localhost:9090/api/v1/write
```

**官方对多租户 remote_write 的说明（原文）**：

> **"By default, Loki adds the `X-Scope-OrgID` header using the tenant ID of the recording rules that produced the samples."**
> **"Do not set `X-Scope-OrgID` manually under `headers`; Loki strips that header at runtime before sending remote-write requests."**

**Ruler 的其他官方事实**：

| 项 | 内容 |
|---|---|
| **规则存储后端** | `azure` / `gcs` / `s3` / `swift` / `cos` / `local` |
| **多 Ruler 时** | **必须配同一个后端**（官方原文："configure all Rulers to use the same backend"） |
| **local 存储** | **只读**，不支持通过 Ruler API 增删规则 |
| **分片** | 需要 `ruler.enable_api: true` + 配置 ruler ring + `ruler.enable_sharding: true` |
| **分片特性** | **"Unlike ingesters, Rulers do not hand over responsibility: all rules are re-sharded randomly every time a Ruler is added to or removed from the ring."** |
| **规则评估状态** | **不依赖 Prometheus**，Loki 用一个**小的内存存储**做懒加载（仅为重新调度/分片时加载历史评估） |
| **规则数量限制** | `limit` 字段，**默认 0 = 不限制**；超限时**丢弃所有 recording samples、清除该规则的全部告警**，并把 rule health 置为 `err` |

### 2.14 标签基数纪律（头号事故源）

**这一节单独拎出来，因为它是 Loki 的第一事故源。**

**禁止打成标签的字段（按危险程度排序）**：

| 字段 | 为什么绝对不能 | 替代方案 |
|---|---|---|
| **`trace_id`** | **每个请求一个值** → 每个请求一条独立流 | **structured metadata**，或留在正文用 <code>&#124; json</code> 提取 |
| **`request_id` / `span_id`** | 同上 | 同上 |
| **`user_id` / `customer_id` / `tenant_id`** | 用户数上万 → 上万条流 | structured metadata |
| **完整 URL 路径** | `/user/8821/order/99321` 几乎无限 | **提取路由模板**（`/user/{id}/order/{id}`）再打成标签 |
| **错误信息原文** | 每个异常都不同 | 留正文；需要聚合就用**错误码**做标签 |
| **时间戳 / 毫秒数** | 每条都不同 | **绝不可能做标签** |
| **pod 完整名** | Pod 每次重建名字变 → **持续产生新流**（官方也警告 `k8s.pod.name` 不再推荐做默认标签） | 用 **workload / deployment 名**；pod 名放 structured metadata |

**官方给的"用 ip 做标签会怎样"的算术（原文）**：

> 4 个常见 action × 4 个常见 status code = **16 条流、16 个独立 chunk**。
> **"Now multiply this by every user if we use a label for `ip`. You can quickly have thousands or tens of thousands of streams."**

**基数控制的四条实操纪律**：

| 纪律 | 具体做法 |
|---|---|
| **1. 先设计标签，再写采集配置** | **标签方案要在采集配置之前定**。采集配置是"发现标签"的地方——**每加一个 `stage.labels`，就多一维索引** |
| **2. 上限 10-15 个** | 官方原文的 DO。**超了就要砍** |
| **3. 只为"用户真的会查的维度"建标签** | 官方原文的 DO。**问自己：这个维度会以 `{}` 等值匹配出现过吗？** |
| **4. 高基数字段一律走 structured metadata** | 需要过滤但不能做流选择器的字段 |

**采集端配置里最危险的三行**（Alloy / Promtail 的 `stage.labels`）：

```
stage.labels { values = { "trace_id" = "trace_id" } }   # ❌ 每个请求一个新流
stage.labels { values = { "level" = "level" } }          # ✅ 取值有限
stage.labels { values = { "url" = "url" } }             # ❌ 路径几乎无限
```

> **只要一行** `stage.labels` 把高基数字段提成 label，**Loki 的索引体积和 chunk 数量就会暴涨**，而且**不会立刻报错**——它会慢慢变慢、慢慢涨存储、慢慢撑爆内存。**这是最阴险的一类事故**：没有告警，只有"一切都还好，只是越来越慢"。

**怎么监控基数**（而不是等它爆）：

| 指标 | 看什么 |
|---|---|
| `loki_distributor_bytes_received_total` | 写入量 |
| `loki_ingester_memory_chunks` | **内存里的 chunk 数——基数爆炸最早在这里冒头** |
| `loki_index_request_duration_seconds` | **索引查询耗时——基数爆炸最直接的信号** |
| `loki_ingester_chunk_size_bytes` | chunk 变小 = **流被切碎 = 基数高**（官方描述的现象："flush thousands of tiny chunks"） |

**另一个官方信号**：**如果日志流里某个标签值一直在变（pod 名、request_id），你会看到大量 chunk 被创建但每个都很小**。

---

## 3. 动手

> **本节约束**：不新建集群、不拉镜像、不创建任何容器资源。示例是配置与查询，需要你在自己的环境执行。

### 3.1 起一套 Loki（monolith）+ 本地数据源

**部署形态（Helm，官方 grafana/loki chart）**：

```bash
# 说明：本命令仅作形态示例，镜像请用你自己的镜像源
helm install loki grafana/loki \
  -n monitoring --create-namespace \
  --set deploymentMode=SingleBinary \
  --set loki.persistence.enabled=true \
  --set loki.persistence.size=20Gi
```

**看到什么算对**：
- Pod `Running`，日志里出现 `Loki started` 之类字样；
- `curl http://<loki>:3100/ready` 返回 `ready`；
- `curl http://<loki>:3100/loki/api/v1/labels` 返回 label 列表。

> ⚠️ **端口说明**：Loki 默认 HTTP 端口 **3100**（官方文档示例中的 `url = "http://localhost:3100/loki/api/v1/push"` 即为证）。**多租户模式下这个接口不带 `X-Scope-OrgID` 会返回 401 `no org id`**——这是**预期行为**，不是故障。

### 3.2 设计标签方案（先设计，再采集）

**一张表把方案定下来**（这就是要进 Git 的东西）：

| 字段 | 打成 label？ | 理由 |
|---|---|---|
| `namespace` | ✅ | 低基数，**最常查的维度** |
| `app` / `service_name` | ✅ | 低基数 |
| `env` | ✅ | 低基数 |
| `level` | ✅ | 取值 {DEBUG, INFO, WARN, ERROR} |
| `container` | ✅ | 取值有限 |
| `workload` / `deployment` | ✅ | **用 workload 不用 pod 名** |
| `trace_id` | ❌ | 高基数 → **structured metadata** |
| `request_id` | ❌ | 高基数 → structured metadata |
| `user_id` | ❌ | 高基数 → structured metadata |
| `url` | ❌ | 路径几乎无限 → 提取路由模板 |
| `error_message` | ❌ | 每条不同 → 留正文 |
| `pod` | ⚠️ | **官方不推荐做默认标签**（Pod 重建即产生新流）→ structured metadata |

**采集端配置（Alloy 语法示意）**：

```alloy
loki.source.file "app_logs" {
  targets    = local.file_match.app_logs.targets
  forward_to = [loki.process.add_labels.receiver]
}

loki.process "add_labels" {
  // 只把「取值有限」的字段提成 label
  stage.logfmt {
    mapping = { "extracted_level" = "level" }
  }
  stage.labels {
    values = {
      "level" = "extracted_level",
    }
  }
  // trace_id / request_id 绝不能出现在这里
  forward_to = [loki.write.local_loki.receiver]
}

loki.write "local_loki" {
  endpoint {
    url = "http://loki:3100/loki/api/v1/push"
  }
}
```

**看到什么算对**：在 Grafana 里查 `{}`（或 Loki Explore 里查 `{namespace="xxx"}`）能列出**实际存在的 label 名单**。**如果发现 `trace_id` 出现在 label 列表里 → 立刻停，改配置。**

> **验证手段**：`curl -H "X-Scope-OrgID: <tenant>" http://<loki>:3100/loki/api/v1/labels` —— **这个接口返回的就是索引里真实的 label 名单**，是基数体检的第一站。

### 3.3 写 LogQL：三级过滤 + 解析 + 格式化

**第一级：标签收敛（最快，必须放最前）**

```logql
{namespace="prod", app="order-service"}
```

**第二级：行过滤（子串优先）**

```logql
{namespace="prod", app="order-service"} |= "ERROR" != "healthcheck"
```

**第三级：解析 + 字段过滤**

```logql
{namespace="prod", app="order-service"} |= "ERROR" | json | level="error" | duration_ms > 1000
```

**第四级：格式化输出**

```logql
{namespace="prod", app="order-service"} |= "ERROR" | json | line_format "{{.level}} {{.msg}} trace={{.trace_id}}"
```

**第五级（换个维度看）：算指标**

```logql
sum by (app) (rate({namespace="prod"} |= "ERROR" [5m]))
```

**看到什么算对**：

| 步骤 | 期望 |
|---|---|
| 只写 `{namespace="prod"}` | **能列出该租户下所有日志**（不为空） |
| 加 <code>&#124;= "ERROR"</code> | 结果**明显变少** |
| 加 <code>&#124; json &#124; level="error"</code> | 结果**再变少**，且能看出是 JSON 解析生效了 |
| 加 `line_format` | **每行只剩格式化后的内容** |

> **如果第一级就查不到 → 问题在采集端或租户头，不在 LogQL。** 用 `curl /loki/api/v1/labels` 先确认标签真实存在。

### 3.4 验证管道通不通（删 Pod 仍能查到）

**这是本篇最重要的一个验收动作**（也是 D13 / D16 日课的硬指标）：

```
① 记下一个 Pod 名：kubectl get pod -o wide | grep order-service
② 在 Loki 里查：{namespace="prod", app="order-service"} —— 确认能看到它的日志
③ 删掉这个 Pod：kubectl delete pod <pod-name>
④ 等新 Pod 起来（kubectl get pods 确认 Running）
⑤ 再在 Loki 里查同一条 LogQL —— 时间窗要覆盖删除前的时间
⑥ 判据：
   ✅ 还能查到旧 Pod 的日志（且 label 里能看到旧 pod 名）
      → 说明日志已落到外部存储，管道通了
   ❌ 查不到
      → 说明还在依赖容器本地日志，管道没通
```

**注意第 ⑤ 步的时间窗**：Loki 的查询有时间范围，**默认只查最近 1 小时**。**删 Pod 后要查旧日志，必须把时间范围往前拨**，否则会误判成"没查出来"。

**这个动作为什么重要**：它区分了"装了个 Loki"和"建了条日志管道"。**没有这一步，"日志已脱离容器生命周期"这句话就是空话。**

---

### 3.5 常用运维命令（按场景）

> **本节能做什么**：3.1–3.4 是"把日志管道建起来"，本节是**日常查日志、排采集端、查标签、验组件健康**的 Loki 运维命令——按场景归类，每条配真实例子。**Loki 排障的肌肉记忆就来自这一张表。**

### ① 用 logcli 查日志（命令行版的 Explore）

```bash
logcli query '{namespace="prod",app="order-service"}' --limit=100            # 查最近日志
logcli query '{namespace="prod"} |= "error"' --since=1h                      # 行过滤：含 error 的
logcli query '{namespace="prod"}' --tail                                      # 持续跟随（类似 tail -f）
logcli query '{namespace="prod"}' --stats                                     # 看查询统计（扫描量/匹配行数）
logcli labels                                                                 # 看有哪些流标签
logcli labels namespace                                                       # 看某个标签有哪些值
```

> **实战价值**：`logcli` 就是 Loki 的 `kubectl logs`——**不打开 Grafana 也能查日志，排障和写脚本都靠它**。`--stats` 能暴露"这次查询扫了多少 chunk"，是查慢的根因入口（§2.14 标签基数）。

### ② Loki API 验端点（标签排查的底层接口）

```bash
curl -s 'http://localhost:3100/loki/api/v1/labels' | head -c 400               # 看有哪些索引标签（流标签）
curl -s 'http://localhost:3100/loki/api/v1/label/namespace/values' | head -c 400  # 看 namespace 标签有哪些值
curl -s 'http://localhost:3100/loki/api/v1/query_range?query={namespace="prod"}&limit=10' | head -c 600  # 直接打查询 API
curl -s 'http://localhost:3100/ready' ; curl -s 'http://localhost:3100/live'  # 健康探针
```

> **实战价值**：`/loki/api/v1/labels` 是**标签排查的第一入口**——§3.4 说过"第一级查不到先确认标签真实存在"。查询报 "no streams match" 时，先打这个接口看标签值拼对了没，比调 LogQL 快。

### ③ 标签排查与高基数（Loki 的头号事故源）

```bash
curl -s 'http://localhost:3100/loki/api/v1/labels'                  # 看流标签集合（应保持低基数）
logcli query '{namespace="prod"}' --stats --quiet 2>&1 | grep -i stream   # 看这次查匹配了多少流
# 高基数症状：查询慢、ingester 内存涨 → 回到 §2.3 / 2.14 收紧标签
```

> **实战价值**：Loki 的查询性能**完全由标签基数决定**（§2.3）。`/loki/api/v1/labels` 里出现 `user_id`/`trace_id`/`ip` 这类高基标签，就是事故前兆——用 `--stats` 看流数量能提前发现。这是面试"Loki 为什么慢"的必答点。

### ④ 采集端排障（Promtail / Alloy 没推上来）

```bash
curl -s http://localhost:9080/targets          # Promtail 的 targets 接口：看哪些日志路径在被采集
curl -s http://localhost:12345/debug/metrics/prometheus   # Alloy 暴露的自身指标
ps aux | grep -E 'promtail|alloy' | grep -v grep          # 进程在不在
# position 文件：记录读到哪了，卡住时清它重新追（注意会重复采集）
ls -lh /var/lib/promtail/positions.yml
```

> **实战价值**：日志查不到，**先分清是"采集端没推"还是"查询端写错"**。Promtail 的 `/targets` 空 = 路径/服务发现没匹配到；position 文件卡住会导致"新日志不进 Loki"，清掉重追即可（会重复，需评估）。

### ⑤ 组件与存储状态（distributor / ingester / querier）

```bash
curl -s http://localhost:3100/ready            # 各组件都 ready 才算活
curl -s http://localhost:3100/loki/api/v1/status/metrics  # 内部指标（ingester 内存、chunk 数）
curl -s http://localhost:3100/loki/api/v1/tenant/usage   # 租户用量（多租户场景）
ss -ltnp | grep 3100                           # 端口
```

> **实战价值**：Loki 多组件部署（§2.9 simple-scalable / microservices）时，某个 ingester 不 ready 会导致写入失败——`/ready` 是健康总闸，k8s 探针就接它；`/status/metrics` 看 ingester 内存压力。

### ⑥ 通用：端口、进程、配置校验

```bash
ss -ltnp | grep -E '3100|9080|9095'     # Loki / Promtail / memberlist 端口
ps aux | grep -E 'loki|promtail|alloy' | grep -v grep
loki -config.file=/etc/loki/loki-config.yaml -print-config-stderr   # 打印生效配置确认
```

> **实战价值**：Loki 配置改完不生效，用 `-print-config-stderr` 看"实际加载的是哪份、参数是什么"；组件端口不通先 `ss` 看进程活没活。**先确认进程在，再谈查日志。**

---

### 3.6 运维排障实战（场景串命令）

> **把 3.5 的命令用起来**：三个最典型的 Loki 故障，按"现象 → 敲的命令 → 怎么读"走一遍。**用 §3.4 的"删 Pod 仍能查到"动作当验收基准，别在真集群试。**

### 场景 A：LogQL 查询返回 "no streams match selector"（查不到）

```bash
curl -s 'http://localhost:3100/loki/api/v1/labels'                   # 看有哪些流标签
curl -s 'http://localhost:3100/loki/api/v1/label/namespace/values'  # 看 namespace 标签的真实值
logcli query '{namespace="prod"}' --limit=5                          # 先用最粗的 selector 试
# 典型：namespace 写错大小写 / 用了非索引标签当流选择器 / 多租户漏了 X-Scope-OrgID 头
```

> **读法**："no streams match"**先确认标签真实存在且值拼对**——`/loki/api/v1/labels` + 对应标签的 values 接口是真相。多租户场景漏 `X-Scope-OrgID` 头也会全空（§2.11）。**别急着改 LogQL，先确认 selector 命不命中。**

### 场景 B：采集端没把日志推上来（Promtail / Alloy 侧）

```bash
curl -s http://localhost:9080/targets          # Promtail targets：看路径有没有被匹配
ps aux | grep promtail | grep -v grep          # 进程在不在
ls -lh /var/lib/promtail/positions.yml         # position 卡住没
curl -s 'http://localhost:3100/loki/api/v1/query_range?query={namespace="prod"}&limit=5'  # Loki 端有没有数据
# 典型：targets 为空 = 路径/label 没匹配；positions 卡住 = 新日志不追
```

> **读法**：Loki 端查不到，**先去采集端 `/targets` 看有没有目标**——空就说明服务发现/路径没配好；进程在但 position 卡住，清 position 重追。区分"采集端没推"还是"查询端写错"是排 Loki 的第一原则。

### 场景 C：查询特别慢 / ingester 内存打满（高基数爆炸）

```bash
logcli query '{namespace="prod"}' --stats            # 看这次查询扫了多少流/chunk
curl -s 'http://localhost:3100/loki/api/v1/labels'   # 看流标签里有没有高基标签
curl -s 'http://localhost:3100/loki/api/v1/status/metrics' | grep -i 'ingester.*memory\|chunks'  # ingester 压力
# 典型：流标签里混入了 user_id/trace_id → 流数量爆炸
```

> **读法**：慢 + 内存涨 = 高基数（§2.14）。`--stats` 看流数量，若几十万级就是标签爆炸；`/loki/api/v1/labels` 揪出罪魁标签，回到采集端收紧标签（用 `pipeline_stages` 把高基字段改成行内容而非标签）。**Loki 慢几乎只有这一个根因。**

> **三个场景的共同心法**：**先分清"采集端没推"还是"查询端写错"，再谈具体命令。** Loki 排障 90% 的时间花在"标签"上——查不到看标签、慢也看标签。

---

## 4. 逐字段表

### 4.1 LogQL 过滤操作符表

**（一）流选择器（标签匹配）**

| 操作符 | 类型 | 语义 | 官方示例 | 写错的后果 |
|---|---|---|---|---|
| `=` | 精确等值 | 标签**恰好等于** | `{app="x"}` | 值写错 → **返回空**（不报错） |
| `!=` | 精确不等 | 标签**不等于** | `{app!="x"}` | **会带上所有其他 app**，可能远多于预期 |
| `=~` | 正则匹配 | 标签**正则匹配**（**需全匹配**） | <code>{app=~"a&#124;b"}</code> | **正则默认全匹配**：`=~"a"` **不匹配** `abc` |
| `!~` | 正则不匹配 | 标签**不正则匹配** | <code>{app!~"a&#124;b"}</code> | 同上，全匹配语义 |

**（二）行过滤（正文匹配）**

| 操作符 | 语义 | 底层 | 性能 | 写错的后果 |
|---|---|---|---|---|
| <code>&#124;=</code> | 日志行**包含字符串** | 字面量 | **快** | 写正则元字符（`.` `*` `(`）**不会被当正则**，反而安全 |
| `!=` | 日志行**不包含字符串** | 字面量 | 快 | **排除逻辑写反** → 把噪声留下、把正事排掉 |
| <code>&#124;~</code> | 日志行**包含正则匹配** | 正则 | **慢** | **元字符不转义 → 匹配范围意外变大**（`.` 匹配任意字符） |
| `!~` | 日志行**不包含正则匹配** | 正则 | 慢 | 同上 |

**（三）标签过滤（解析后的字段）**

| 操作符 | 语义 | 适用字段 | 写错的后果 |
|---|---|---|---|
| `==` / `=` | 相等 | 字符串、数值 | 字符串字段忘了加引号 → **解析失败** |
| `!=` | 不等 | 同上 | 同上 |
| `>` `>=` | 大于 / 大于等于 | **数值** | 对字符串用 `>` → **无结果** |
| `<` `<=` | 小于 / 小于等于 | 数值 | 同上 |
| `and` | 两条件都满足 | 组合 | — |
| `or` | 任一满足 | 组合 | 优先级：**`and` 高于 `or`**，混用要加括号 |

**（四）五个管道阶段的性能排序（决定查询快慢）**

| 阶段 | 收窄能力 | 性能 | 建议位置 |
|---|---|---|---|
| **流选择器 `{...}`** | **最强制**（不命中就完全不扫） | **最快** | **永远放最前** |
| **行过滤 <code>&#124;=</code> `!=`** | 中（子串扫描） | 快 | 紧跟流选择器 |
| **行过滤 <code>&#124;~</code> `!~`** | 中（正则扫描） | **慢** | 放在 <code>&#124;=</code> 之后（先便宜的过滤） |
| **解析器 <code>&#124; json</code>** | 弱（不减少扫描量，只增加解析开销） | 中 | 需要字段时才加 |
| **字段过滤 <code>&#124; level="x"</code>** | **弱**（筛结果，不筛扫描范围） | 中 | 最后 |
| **格式化 `line_format` / `label_format`** | 无（只改显示） | 中 | 最后 |

> **核心认知**：**字段过滤（`| level="error"`）不会让 Loki 少扫数据**——它是在结果集上筛。**真正能提速的是流选择器。** 所以"查询太慢"的第一解法永远是**把标签选择器收窄**，而不是加更多过滤条件。

**（五）日志查询 vs 指标查询**

| 类型 | 返回 | 典型函数 | 用途 |
|---|---|---|---|
| **Log query** | 日志行 | — | 排障、看现场 |
| **Metric query** | 数值时序 | `rate()` / `count_over_time()` / `sum by ()` | **告警规则、画图** |

### 4.2 Loki 部署模式对照表

| 维度 | **Monolith（`all`）** | **Simple Scalable** | **Microservices** |
|---|---|---|---|
| **官方描述** | "single binary" mode with target `all` | 逻辑分组，targets `read`/`write`/`backend` | 每个组件独立运行 |
| **包含组件** | Distributor、Ingester、Query Frontend、Query Scheduler、Querier、Compactor、Ruler、Pattern Ingester | write：Distributor+Ingester；read：Querier+Query Frontend+Query Scheduler；backend：Compactor+Ruler+Pattern Ingester | 上述全部 + **Index Gateway** + Bloom 三件套（实验） |
| **进程数** | **1** | **3**（按 target 分组） | **N**（每组件一个） |
| **扩缩容粒度** | **不能**（整体扩） | **按组扩** | **按组件扩** |
| **资源隔离** | **弱** | **写/读/后台任务隔离** | **最强** |
| **写读隔离（惊群效应）** | ❌ 查询暴涨会挤占写入 | ✅ **有隔离** | ✅ 有隔离 |
| **部署复杂度** | **最低** | 中 | **最高** |
| **配置发现** | 进程内 | 组内 + 组间需配服务地址 | **依赖服务发现 / Ring / 静态地址** |
| **官方建议场景** | 开发、测试、小规模、资源受限 | **常规生产部署** | 大规模、高负载、要求严格资源隔离的生产 |
| **K8s 上的典型选型** | **本地学习 / 测试** | **中小生产（默认答案）** | 超大规模 / 多集群 |
| **一句话** | **一个 Pod 搞定** | **写读分开三组** | **每个组件一个 Deployment** |

**（附）组件状态与关键参数**

| 组件 | 有状态 | 扩缩容 | 关键事实（官方） |
|---|---|---|---|
| Distributor | 否 | ✅ 可独立扩 | **前面必须挂 LB**；限流 = 集群额度 ÷ 活跃实例数 |
| Ingester | **是** | ✅ | WAL 重放；`replication_factor` **通常 3**；quorum = `floor(rf/2)+1`；状态机 PENDING→JOINING→ACTIVE→LEAVING→UNHEALTHY；**Handoff 已弃用** |
| Querier | 否 | ✅ | **先查内存再查存储**；按 (纳秒时间戳, 标签集, 日志内容) 去重 |
| Query frontend | 否（无状态） | ✅ | 官方建议多副本（通常两个即可）；拆分、排队、缓存、负缓存 |
| Query scheduler | 否（无状态） | ✅ | **每租户独立队列**保证跨租户公平 |
| Ruler | 是 | ✅ | 规则分片用环；**加/减 Ruler 时规则随机重新分片**；状态存内存 |
| Compactor | 是 | **通常单实例** | 每天每租户合并成一个索引文件；执行保留与删除 |
| Index gateway | 是 | ✅ | `simple` 模式或 `ring` 模式（按租户分片） |
| Pattern ingester | 是 | — | **默认禁用**；为 Logs Drilldown 的 Patterns 提供数据 |

### 4.3 Loki 配置关键项表

| 配置项 | 类型 | 默认 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `auth_enabled` | bool | **true**（官方标称） | 是否多租户 | 设 false → 租户 ID 恒为 `fake`；**设 true 但请求不带头 → 401 `no org id`** |
| `server.http_listen_port` | int | **3100**（官方示例体现） | HTTP 端口 | 端口对不上 → 连不上 |
| `common.storage.filesystem.chunks_directory` | path | — | 本地块存储目录 | 目录不可写 → **写入失败** |
| `common.storage.s3.bucketname` | string | — | S3/OSS 桶名 | 桶不存在 / 无权限 → **写入 5xx** |
| `common.storage.config` | map | — | 索引存储配置 | 配错 → **索引写不进去，查询查不到历史数据** |
| `limits_config.max_label_names_per_series` | int | **官方默认上限 15 个索引标签** | 标签数量上限 | 超限 → **日志被拒或被截标签** |
| `limits_config.discover_service_name` | list | 官方给的查找顺序 | `service_name` 自动填充来源 | 顺序不含你的标签 → 全是 `unknown_service` |
| `limits_config.reject_old_samples` / `max_chunk_age` | duration | 官方默认窗口 **2 小时**（写入顺序窗口） | 拒绝过旧日志 | 离线补日志超窗 → **报 `too far behind`** |
| `limits_config.retention_period` | duration | 依配置 | 日志保留期 | 不设 → **节点磁盘被日志吃满** |
| `ingester.wal` | 目录 | — | 预写日志 | 不开 → **进程崩溃丢内存数据** |
| `ingester.replication_factor` | int | **通常 3** | 副本数 | 配偶数 → **quorum 计算浪费**；配 1 → **无冗余** |
| `distributor.replication_factor` | int | — | 写入副本数 | 同上 |
| `querier.multi_tenant_queries_enabled` | bool | **false**（官方标称） | 跨租户查询 | 设 true 但查询头只给一个租户 → 行为不符预期 |
| `ruler.alertmanager_url` | string | — | 告警发往的 Alertmanager | 写错 → **告警发不出去**（日志告警静默） |
| `ruler.storage` | map | — | 规则文件存储 | 多 Ruler **必须配同一后端** |
| `ruler.rule_path` | string | 官方标称默认 `/rules` | 规则评估的临时目录 | 目录不可写 → 规则评估失败 |
| `ruler.enable_api` | bool | false | 启用 Ruler API | 分片必需 |
| `ruler.enable_sharding` | bool | false | 规则分片 | 多 Ruler 不开 → 重复评估 |
| `limits_config.ruler_max_rules_per_rule_group` / `limit` | int | **默认 0 = 不限制** | 规则组内告警/样本上限 | 超限 → **丢弃全部样本、清除全部告警、rule health=err** |

---

## 5. 横向对比

### 5.1 Loki vs Elasticsearch vs Fluentd vs Fluent Bit

**先分清两组**（很多人把这四个混成一类）：**Loki 和 ES 是「日志存储与查询系统」；Fluentd 和 Fluent Bit 是「日志采集与转发代理」。** 后两者**不解决存储与查询**，它们通常把日志送到前者。

| 维度 | **Loki** | **Elasticsearch** | **Fluentd** | **Fluent Bit** |
|---|---|---|---|---|
| **角色** | 存储 + 查询 | 存储 + 查询 | **采集 + 转发** | **采集 + 转发** |
| **索引方式** | **只索引标签**，正文不建索引 | **全文倒排索引** | — | — |
| **查询语言** | LogQL | Lucene / ES&#124;QL | — | — |
| **全文检索** | **弱**（标签收敛后扫正文） | **强**（相关性、聚合、复杂分析） | — | — |
| **存储成本** | **低** | **高**（索引膨胀） | — | — |
| **部署复杂度** | **低**（几个组件 + 对象存储） | **高**（分片、副本、ILM、调优） | 中 | **低**（单二进制） |
| **典型资源** | 几 GB ~ 几十 GB 内存（看规模） | **吃内存**，常需专用节点 | 中 | **极低**，可每节点一个 |
| **与 K8s 契合度** | **高**（DaemonSet 采集 + 标签天然对应 K8s 标签） | 中 | 高 | **高** |
| **典型场景** | **K8s 集群日志、排障为主** | **合规审计、全文检索、业务分析** | 统一日志路由层 | 边缘/轻量采集 |

**⚠️ 一个必须澄清的事实**：**Loki 里"全文检索弱"不是"不能搜关键字"**——`|= "关键词"` 是能搜的。**弱在哪**：

| | Loki | ES |
|---|---|---|
| **搜一个已知关键词** | ✅ <code>&#124;= "keyword"</code> 很快 | ✅ 快 |
| **搜一个模糊模式** | ✅ <code>&#124;~ "regex"</code> 慢 | ✅ 快 |
| **跨全部日志做全局检索**（不限时间、不限标签） | ❌ **慢到不可用**（要扫所有 chunk） | ✅ 快 |
| **相关性排序 / 分词 / 高亮** | ❌ 没有 | ✅ 有 |
| **按任意字段做复杂聚合分析** | ❌ 弱 | ✅ 强 |
| **查三个月前的审计记录并做全文审计** | ❌ 保留期与能力都不匹配 | ✅ |

**选型结论**：

> **Loki 承接运维排障，ES 承接审计与全文检索。** 金融/受监管行业常见架构是**两边都上**——Loki 供 SRE 快速排障，ES 供审计留痕。**这不是重复建设，是两种不同的查询能力。**

**（Fluentd vs Fluent Bit）**：

| | Fluentd | Fluent Bit |
|---|---|---|
| **语言/形态** | Ruby，**插件生态丰富** | C，**单二进制、极轻** |
| **插件数量** | **上百个** | 较少（几十个） |
| **性能** | 中 | **高**（C 实现） |
| **内存占用** | 高 | **低**（常驻几十 MB 量级） |
| **典型角色** | **中心化路由层**：汇聚多源 → 加工 → 转发多个后端 | **节点级采集 agent**：每节点一个 → 发给 Fluentd 或 Loki |
| **Loki 场景** | 不用（Alloy 更现代） | **可用**，但官方推荐 Alloy |

> **现在做 Loki 采集，官方路径是 Grafana Alloy**（Promtail EOL），**不是 Fluent Bit**。Fluent Bit 的价值在"要同时发往多个异构后端（ES + Kafka + 云厂商）"的场景。

### 5.2 Promtail vs Alloy vs SDK 直推

| 维度 | **Promtail** | **Grafana Alloy** | **SDK 直推** |
|---|---|---|---|
| **状态** | **已 EOL（2026-03-02）** | **官方当前推荐** | 现代做法 |
| **配置语言** | YAML（`scrape_configs` / `pipeline_stages`） | **Alloy / River**（组件化） | 代码 |
| **采集方式** | 读容器日志文件 | 读文件 / 收 OTLP / 收 syslog 等 | 应用直接推 |
| **处理能力** | pipeline_stages（正则、json、labels…） | **更多信号**（含 metrics、traces）、更丰富处理 | 业务代码里就带完整上下文 |
| **日志上下文** | 只有 stdout 里的 | stdout + 采集侧能拿到的 | **可带 trace_id / 用户 / 参数** |
| **多后端** | 一个 Loki | 一个或多个后端 | **SDK 里配多个 exporter** |
| **改造量** | 零（但已停止维护） | 需迁移配置 | **要改代码、加依赖** |
| **官方迁移工具** | — | **有**（单命令转换 Promtail 配置） | — |
| **多集群** | 每集群一份采集配置 | 每集群一份 | SDK 统一配置 |

**选型结论**：

| 场景 | 选择 |
|---|---|
| **存量 Promtail 生产环境** | **立刻排期迁 Alloy**（EOL 已过，**不再有任何修复与安全更新**） |
| **新项目、K8s 集群日志** | **Alloy**（官方推荐，与 Grafana 生态一体） |
| **需要日志携带完整业务上下文**（用户 ID、订单号、trace 关联） | **SDK 直推**（或 SDK + Alloy 混合） |
| **要同时发往多个异构后端**（ES + Kafka + 云监控） | **Fluent Bit / Fluentd**（或 Alloy 也能多导出） |
| **Lambda / 无服务器** | **lambda-promtail**（**官方明确说明 Promtail 的弃用不包含它**） |

### 5.3 Loki vs Prometheus（索引方式的差别）

**这是最容易被问、也最能体现深度的一组对比。**

| 维度 | **Prometheus** | **Loki** |
|---|---|---|
| **存什么** | 数值时间序列 | 日志文本行 |
| **数据模型** | `(metric_name, {label_set})` → 若干样本 | `(tenant_id, {label_set})` → 一串日志行 |
| **索引什么** | **所有标签 + 全部样本值**（每条时间序列都存） | **只索引标签 + 时间戳**，正文不建索引 |
| **正文怎么处理** | 不存在正文 | **压缩后存 chunk**，查询时扫描 |
| **高基数字段** | **标签就是高基数的重灾区**（`user_id` 做标签 → TSDB 爆炸） | 同样重灾区，但**可以用 structured metadata 规避** |
| **聚合能力** | **强**（PromQL 全套聚合、函数、histogram 分位） | **弱**（只有日志侧的 rate / count_over_time 等少数） |
| **分位数** | **原生支持**（histogram_quantile） | ❌ 不支持（除非自己算分桶） |
| **采样** | 支持（Prometheus 侧的 `scrape` 限流） | 采集侧可采；**存储侧一般不采样** |
| **保留期** | **长**（数月～数年） | **短**（7～30 天） |
| **成本量级** | 低 | **低**（约为 ES 的 1/5–1/10 是社区常见说法，**官方未给出数字，仅作量级参考 ⚠️ 待验证**） |
| **回答的问题** | **有没有问题**（趋势、比例、分位） | **为什么有问题**（具体那一行） |

**根本差别（一句话）**：

> **Prometheus 存"数字"，所以它必须保存每个数值、才能算比例和分位；Loki 存"文本"，所以它只保存目录（标签）指向的压缩块、查询时再扫描。**
> **这决定了：Prometheus 的查询力强、成本随样本数线性增长；Loki 的查询力弱、成本随字节数增长。**

**"指标 vs 日志"该怎么分工（面试标准答法）**：

| | 指标 | 日志 |
|---|---|---|
| **回答** | 有没有问题 | 为什么有问题 |
| **成本** | 低 | 高 |
| **保留** | 长 | 短 |
| **适合** | 趋势、比例、p99、告警 | 排障现场、上下文 |

**排障顺序**：**指标发现异常 → 链路定位到哪一环 → 日志找根因**。Loki 是最后一环，不是第一环。

### 5.4 Loki vs Elasticsearch 的场景分工（决策树）

```
需要做「任意关键词的全局全文检索」吗？          ← 等保审计、全文审计检索
  是 → ES
  否 ↓
需要「相关性排序 / 分词 / 复杂聚合分析」吗？
  是 → ES
  否 ↓
日志来源主要是 K8s 集群、量级大、保留期短、要求低成本？
  是 → Loki
  否 → 都可以，看团队运维能力
```

**具体场景分工表**：

| 场景 | 选谁 | 为什么 |
|---|---|---|
| K8s 应用日志排障 | **Loki** | 标签天然对应 K8s 元数据，成本低 |
| 微服务调用链的日志 | **Loki**（+ Tempo 链路） | 配合 trace_id 做 structured metadata |
| **等保三级：调三个月前的操作记录** | **ES** | **Loki 的保留期与审计需求相反** |
| **合规审计 + 全文审计检索** | **ES** | 需要全文索引 + 长保留 + 防篡改 |
| 业务分析（按字段做多维聚合） | **ES** | Loki 聚合能力弱 |
| 成本敏感、日志量大、只做排障 | **Loki** | 对象存储 + 只索引标签 |
| 日志要带完整业务上下文 | **SDK 直推 Loki** 或 **ES** | — |

> **标准结论（可直接背）**：
> "**K8s 集群日志用 Loki 省成本，合规审计与全文检索场景用 ELK。** 两边都上时，**Loki 承接运维排障，ES 承接审计留痕**。这不是重复建设——它们回答的是不同的问题。"

---

## 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 → 怎么防）

**排障铁律**：**先分层**——**采集端（有没有日志进来）→ 存储端（进来的是不是被拒了）→ 查询端（标签对不对、租户对不对）**。

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| Grafana 里查 Loki **一片空白** | 写入可能正常 | **租户不匹配**：采集端用 `X-Scope-OrgID: A`，Grafana 数据源填了 B 或没填 | 数据源里填**与采集端一致的 Tenant ID**；用 `curl -H "X-Scope-OrgID: A" .../labels` 逐个试 | **租户 ID 写进配置中心**，两边引用同一个变量 |
| Grafana 里查 Loki **一片空白** | 写入正常 | **标签名对不上**：查的是 `{app="x"}`，实际标签叫 `app_kubernetes_io_name` | 先 `curl .../labels` 看真实 label 名单，再改查询 | **采集后先看一次 label 名单**再写查询 |
| Grafana 里查 Loki **一片空白** | 写入可能正常 | **查询时间窗太窄**（Loki 默认只查最近 1 小时） | 把时间范围往前拨 | 排查固定动作：**先扩大到 24h 再判断** |
| Loki 写入返回 **401 + `no org id`** | — | **多租户模式下请求没带 `X-Scope-OrgID`**（`auth_enabled` 默认 true） | 给请求加头；或设 `auth_enabled: false` 走单租户 | 部署时**显式决定单租户还是多租户**，写进文档 |
| Loki 写入返回 **5xx**，日志里有对象存储报错 | ingester 活着 | **桶不存在 / 权限不足 / 区域端点错** | 核对桶名、区域、endpoint；补 `s3:ListBucket` `s3:PutObject` `s3:GetObject`（OSS 权限同理） | **部署前先验证桶可写**（用最小权限跑一次 push） |
| Loki 写入返回 **5xx**，报 `too far behind` | — | **日志时间戳太旧**，超出写入顺序窗口（默认 2 小时） | 调 `max_chunk_age`；或接受"离线补日志超窗"这个事实 | 离线补日志提前知道这个限制 |
| 查询返回 `context deadline exceeded` / 超时 | — | **标签选择器太宽**，要扫的 chunk 太多 | **收窄流选择器**（加标签、加时间窗）；把 <code>&#124;~</code> 换成 <code>&#124;=</code>；用 query frontend 拆分 | **查询慢第一解法永远是收窄标签**，不是加过滤 |
| 查询超时，且**指标显示 `loki_ingester_memory_chunks` 暴涨** | — | **标签基数爆炸**：某个高基数字段被做成了标签 | 查 label 名单，**找出那个每条都变的标签**；改用 structured metadata | **采集配置里 `stage.labels` 逐行 review**，只保留取值有限的字段 |
| 查询变慢，且 **chunk 数量暴涨但每个都很小** | — | **基数爆炸的典型征兆**（官方描述："flush thousands of tiny chunks"） | 同上 | 监控 `loki_ingester_memory_chunks` 与 `loki_index_request_duration_seconds` |
| `loki_index_request_duration_seconds` 明显变长 | — | **索引变大**（基数上升的直接信号） | 定位高基数标签 | 同上 |
| 节点磁盘被日志吃满 | — | **没设保留期**（`limits_config.retention_period`） | 设保留期；Compactor 执行删除 | **日志盘必须设保留期**，否则反过来搞垮集群 |
| 写入量突然暴增 | — | 某服务开始打 **DEBUG 级日志**；或**标签基数上升导致 chunk 变多** | 查日志级别配置；看 chunk 数变化 | 采集侧做**日志级别过滤**（只收 INFO 以上） |
| **Promtail 升级后行为异常 / 官方不再修 bug** | — | **Promtail 已于 2026-03-02 EOL**（官方公告） | **迁移到 Grafana Alloy**（官方提供单命令迁移工具） | **在 EOL 日期前排期迁移**；新项目直接上 Alloy |
| Alloy 配置写完**不生效** | — | **组件图没接上**（`forward_to` 链断了）；或**写到了本地文件而不是容器 stdout** | 检查 `forward_to` 是否一路连到 `loki.write`；确认应用确实打 stdout | Alloy 配置加 `loki.write` 的 `endpoint.url` 输出验证 |
| 用 <code>&#124; json</code> 后字段全是空的 | — | 日志行**不是 JSON**（可能是 logfmt 或纯文本） | 换成 <code>&#124; logfmt</code> 或 <code>&#124; pattern</code>；先 `curl` 看一行原始日志到底长什么样 | **采集后先看一行真实日志**，再决定解析器 |
| <code>&#124; json</code> 后某些字段类型不对（如 `code` 是字符串） | — | JSON 里的值本身是字符串；或**日志里混了多种格式** | 用 <code>&#124; json &#124; code = 500</code> 做数值比较（解析器会尝试转换）；统一应用日志格式 | **应用侧统一日志格式**是根治办法 |
| <code>&#124;~ "error."</code> 匹配出 `errorX` | — | **正则里 `.` 匹配任意字符**（从 <code>&#124;=</code> 切到 <code>&#124;~</code> 时语义变了） | 转义成 `error\.` | **<code>&#124;=</code> 与 <code>&#124;~</code> 混用时逐个检查元字符** |
| `=~ "app"` 匹配不到 `app-frontend` | — | **Loki 的正则默认全匹配**（不像 PromQL 的部分匹配） | 写成 `=~ "app.*"` | **记住：Loki 正则是全匹配语义** |
| 跨租户查询返回 400 | — | **写端点不支持多租户**：`/loki/api/v1/push`、`/otlp/v1/logs`、`/loki/api/v1/tail` **只支持单租户** | 写操作去掉多租户头 | 官方原文列明这三个端点的限制 |
| `X-Scope-OrgID` 带冒号后**行为不一致** | — | **冒号不是合法租户字符**；不同组件读法不同（有的读到第一个冒号就停，有的读全值） | 去掉冒号 | 租户 ID 只用 `0-9a-zA-Z` 与 `! - _ . * ' ( )` |
| Ruler 规则**评估了但告警发不出去** | 规则 firing | **`ruler.alertmanager_url` 配错或 Alertmanager 不可达** | 核对 URL；看 Ruler 日志 | 规则与告警链路要**一起做端到端验证**（造一条真告警看是否收到） |
| Recording rule 的结果**在 Prometheus 里看不到** | — | **recording rule 走 remote-write，不是走 Alertmanager**；remote-write 没配或没开 | 配 `ruler.remote_write.enabled: true` 与客户端 URL | **记住两条路径不同**（官方明确区分） |
| 多 Ruler 实例**规则被重复评估** | — | **没开 `ruler.enable_sharding` + 没配 ring** | 开分片并配 `ruler.ring.kvstore` | 多 Ruler 部署清单里写明这三个配置项 |
| 规则组超过限制后**告警全消失** | — | 触发 `limit`：**丢弃全部 recording samples、清除该规则的全部告警**、rule health 置 `err` | 调大 limit 或拆分规则组 | 监控 rule health（`err` 是明确信号） |
| 规则状态在重启后**丢了历史** | — | 官方说明：**Loki Ruler 与 Prometheus 解耦**，评估状态存在**内存存储**里，只用于懒加载 | 接受这个特性；**真正的告警状态以 Alertmanager 为准** | 排查历史看 Alertmanager，不要指望 Loki Ruler |
| Pod 删了**日志查不到** | — | **日志还在容器本地**，采集管道没通 | 检查采集端日志、Prometheus/Alloy 是否在跑、标签是否匹配 | **验收必须做"删 Pod 仍能查"这一步**（见 3.4） |
| Pod 删了**日志查得到，但 label 里没有旧 Pod 名** | 管道通 | **标签里没打 pod 名**（官方也不推荐把 `k8s.pod.name` 做默认标签） | 用 `workload` 维度区分；Pod 名放 structured metadata | 标签方案评审时明确这一点 |
| 查 Loki 时**浏览器很卡** | — | **一次查询返回行数太多**（Grafana 会一次性拉大量日志行） | 加行过滤条件；用 `limit`；缩小时间窗 | 大范围日志查询走 LogCLI 分页，不在 Grafana 里裸查 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `grafana.com/docs/loki/latest/` | Loki 总览与定位 | ✅ 2026-10-24 |
| S2 | 官方 | `grafana.com/docs/loki/latest/get-started/` | 入门主线 | ✅ 2026-10-24 |
| S3 | 官方 | `grafana.com/docs/loki/latest/get-started/components/` | **组件职责、三种部署模式、quorum、replication_factor、Compactor 单实例** | ✅ 2026-10-24 |
| S4 | 官方 | `grafana.com/docs/loki/latest/get-started/architecture/` | **写入 7 步 / 查询 9 步、index shipper、chunk 与 index 格式、单二进制 `-target`** | ✅ 2026-10-24 |
| S5 | 官方 | `grafana.com/docs/loki/latest/get-started/labels/` | **标签设计原则（DO/DON'T）、service_name 发现顺序、OTel 默认标签、15 标签上限、写入顺序窗口** | ✅ 2026-10-24 |
| S6 | 官方 | `grafana.com/docs/loki/latest/query/` | **LogQL 语法、流选择器操作符、行过滤、解析器、格式化** | ✅ 2026-10-24 |
| S7 | 官方 | `grafana.com/docs/loki/latest/send-data/` | 推送方式总览 | ✅ 2026-10-24 |
| S8 | 官方 | `grafana.com/docs/loki/latest/send-data/promtail/` | **Promtail EOL 公告（2026-03-02）与 Alloy 迁移工具** | ✅ 2026-10-24 |
| S9 | 官方 | `grafana.com/docs/loki/latest/send-data/otel/` | OTel 客户端推 Loki | ✅ 2026-10-24 |
| S10 | 官方 | `grafana.com/docs/loki/latest/operations/storage/` | **chunk 与 index、索引存储格式、对象存储支持矩阵、S3 权限清单** | ✅ 2026-10-24 |
| S11 | 官方 | `grafana.com/docs/loki/latest/operations/multi-tenancy/` | **X-Scope-OrgID、租户 ID 规则、401 `no org id`、跨租户查询限制** | ✅ 2026-10-24 |
| S12 | 官方 | `grafana.com/docs/loki/latest/alert/` | **Loki Ruler、LogQL 告警规则、remote_write 与 Alertmanager 两条路径、规则限制** | ✅ 2026-10-24 |
| S13 | 官方 | `grafana.com/docs/grafana/latest/datasources/loki/` | Grafana 加 Loki 数据源 | ✅ 2026-10-24 |
| S14 | 官方 | `grafana.com/docs/grafana/latest/dashboards/` | Grafana 侧 Panels / Logs 面板与看板组织（衔接 L3） | ✅ 2026-10-24 |
| S15 | 官方 | `prometheus.io/docs/prometheus/latest/querying/basics/` | PromQL 基础（与 LogQL 的同源关系） | ✅ 2026-10-24 |
| S16 | 中文 | `developer.aliyun.com` · `cloud.tencent.com/developer` | 中文实践：Helm 版标签命名差异、Promtail→Alloy 迁移踩坑 | ✅ 2026-10-24 |
| S17 | 云厂商 | `help.aliyun.com/zh/sls/` | 阿里云日志服务（SLS）作为 ES 路线的对照形态 | ✅ 2026-10-24 |
| S18 | 中文/社区 | `docker.c-cpp.com`（Docker 中文网） | 容器日志与 stdout 采集行为的对照参考 | ✅ 2026-10-24 |

> **完整来源矩阵见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。**
> **官方无中文版**：`grafana.com/docs/loki/latest/` 下**没有中文路径**，中文来源只能靠社区与云厂商文档。
> **⚠️ 单源待补**：① "Loki 存储成本约为 ES 的 1/5–1/10"——**官方未给出该数字**，本篇只作**量级参考**并已标注 ⚠️ 待验证；② 钉钉/企微机器人消息长度上限——官方 Grafana 文档未给数值。

---

## 8. 自测三问

1. **能说清输入输出吗？**
   输入是**一行日志 + 一组标签**（以及租户头），输出是**查询命中的日志行或由日志算出的数值序列**。中间链路：客户端（Alloy / SDK）→ **distributor**（校验、限流、哈希路由）→ **ingester**（内存聚合成 chunk、定期 flush 到对象存储）→ 索引经 **index shipper** 写入对象存储。查询时：**query frontend** 拆查询 → **scheduler** 排队 → **querier** 先问 ingester 内存、再查对象存储、**去重** → frontend 合并返回。
   **关键一句**：**Loki 只索引标签与时间戳，正文不建索引；高基数字段必须留在正文或 structured metadata。**

2. **能故意改坏再倒推吗？** —— 五个实验：
   ① **在采集配置里加一行 `stage.labels { values = { "trace_id" = "trace_id" } }`** → 灌一批日志 → 查 `curl .../labels` → **看到 `trace_id` 出现在标签名单里** → 再看 `loki_ingester_memory_chunks` **暴涨、chunk 变小变多** → 倒推出"高基数字段不能做标签"。
   ② **把 `X-Scope-OrgID` 从请求里去掉**（`auth_enabled: true`）→ push → **返回 401 `no org id`** → 倒推出"多租户必须带租户头"。
   ③ **把查询从 `{namespace="prod"}` 改成 `{namespace="prod"} | json | level="error"`** → **加上字段过滤后条数变少，但扫描量不变** → 倒推出"字段过滤只筛结果、不筛扫描范围；提速靠收窄流选择器"。
   ④ **把 `|= "error"` 改成 `|~ "error."`** → **匹配出 `errorX`、`error1`** → 倒推出"正则里 `.` 匹配任意字符"。
   ⑤ **把 `=~ "app"` 改成 `=~ "app.*"`** → 原本匹配不到 `app-frontend`，现在匹配到了 → 倒推出"**Loki 的正则默认全匹配**"。

3. **能画上下游吗？**
   ```
   应用 stdout ──► Alloy/SDK ──► distributor ──► ingester ──► 对象存储（chunk）
                                              │                 │
                                              └─► WAL（崩溃重放） └─► index（shipper）
                                                                    │
   浏览器/Grafana ──► query frontend ──► scheduler ──► querier ◄─────┘
                                                      │  ↑
                                                      └──┴─ ingester 内存（近期数据）
   ```
   并在两条链路上标出：**谁是写入的第一站（distributor）、谁是数据落地的地方（对象存储）、谁做去重（querier）、谁做索引合并与保留删除（compactor）、谁评估日志告警（ruler）、告警最后发给谁（Alertmanager）**。
   **再标出两个关键约束**：**标签基数决定索引大小与查询速度**；**写入端点不支持多租户头**。

---

**上一篇**：[`L3-Grafana-仪表盘与告警.md`](L3-Grafana-仪表盘与告警.md) · **下一篇**：[`L5-Alertmanager与OpenTelemetry.md`](L5-Alertmanager与OpenTelemetry.md) · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
