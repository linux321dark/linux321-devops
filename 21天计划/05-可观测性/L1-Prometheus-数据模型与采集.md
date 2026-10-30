# L1 · Prometheus 数据模型与采集（从"知道有这个东西"到"能独立搭一套"）

> **本篇定位**：整套资料里**最容易被用错的基础组件**。很多人以为 Prometheus 就是"一个存数字的数据库"，读完这篇你会知道：**它是一个"定时去别人家敲门要数据"的拉取系统**——它自己不生产数据，你没暴露的指标它永远看不到。
> **前置**：`02-K8s核心/L2-K8s架构与对象模型`（懂 Pod / Service / 标签选择器）、`01-基础篇/L0-前置基础-Linux-Shell-YAML与网络`（懂 HTTP 请求与端口）。
> **学完能做什么**：说清 Prometheus 为什么是拉模型而不是推模型；给一个服务设计出不会爆炸的指标标签；写出一份能在 K8s 里自动发现 Pod 的 `scrape_config`；**讲清 `relabel_configs` 与 `metric_relabel_configs` 的区别**（这是本篇最高频考点）；解释为什么 Prometheus 单机会成为瓶颈、什么时候需要 Thanos/Mimir。
> **在 21 天里**：D12（Prometheus + PromQL P1）的理论部分，D14（GitOps + 可观测闭环）的观测层底座。
> **环境/版本基准**：本文所有字段语义与默认值以 **官方 `prometheus.io/docs/prometheus/latest/`** 为准（该文档站随版本滚动，本文写作日期 2026-10-24，核对时各路径均返回 200）。**本篇未在任何集群执行 Prometheus / kubectl / docker 命令**，示例均为可复制的 yaml 与查询，标注"官方标称"的数值均来自官方文档原文。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L1 Prometheus 数据模型与采集
│
├─ 1. 概念
│   ├─ 1.1 一句话定位：它不生产数据，它只是"定时去敲门"
│   ├─ 1.2 可观测性三支柱：指标 / 日志 / 链路各解决什么问题
│   ├─ 1.3 为什么不能只做指标（三支柱缺一的具体事故）
│   ├─ 1.4 Pull 模型 vs 推模型
│   └─ 1.5 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 数据模型：时间序列 = 指标名 + 标签集 + 样本
│   ├─ 2.2 标签是 Prometheus 的核心，也是头号运维事故源
│   ├─ 2.3 四种指标类型：counter / gauge / histogram / summary
│   ├─ 2.4 exporter 机制：把各种系统的指标翻译成 Prometheus 格式
│   ├─ 2.5 scrape 机制：一次抓取的完整生命周期
│   ├─ 2.6 服务发现：static / file_sd / kubernetes_sd
│   ├─ 2.7 relabel_configs 与 metric_relabel_configs 的区别（高频考点）
│   ├─ 2.8 keep / drop 等常用 relabel 动作
│   ├─ 2.9 TSDB 存储：WAL、时间分块、retention、compaction
│   ├─ 2.10 remote write：把数据交给远端存储
│   ├─ 2.11 为什么 Prometheus 单机有上限（→ Thanos / Mimir）
│   ├─ 2.12 Prometheus Operator 的角色与 ServiceMonitor vs PodMonitor
│   └─ 2.13 关键参数与默认值表
│
├─ 3. 动手
│   ├─ 3.1 最小可运行配置：先抓自己
│   ├─ 3.2 加一个静态目标 + 验证 up
│   ├─ 3.3 K8s 场景：kubernetes_sd 抓 Pod（每步写清"看到什么算对"）
│   ├─ 3.4 验证清单（收口）
│   ├─ 3.5 常用运维命令（按场景）
│   └─ 3.6 运维排障实战（场景串命令）
│
├─ 4. 逐字段表
│   ├─ 4.1 scrape_config 关键字段表
│   └─ 4.2 指标类型对照表（类型 | 语义 | 能否重置 | 典型用途 | 错误用法）
│
├─ 5. 横向对比
│   ├─ 5.1 Pull vs Push
│   ├─ 5.2 Prometheus vs Zabbix
│   └─ 5.3 Prometheus vs OpenTelemetry Collector
│
├─ 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 → 下次怎么防）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 一句话定位

> **Prometheus 是一个"定时去目标暴露的 HTTP 接口拉取指标、在本地存成时间序列、并用 PromQL 查询"的监控与告警工具包。**

这句话里三个动词定义了整个系统：

| 动词 | 含义 | 推论 |
|---|---|---|
| **定时去拉** | 每个 target 按 `scrape_interval` 被独立发起一次 HTTP 请求 | Prometheus **不生产数据**，你没暴露的指标它永远看不到 |
| **本地存** | 采样点写进本机 TSDB（本地磁盘） | 存储能力受单机磁盘与单机内存限制 |
| **用 PromQL 查** | 所有查询在服务端算完再返回 | 查询写法决定了能不能扛住高基数 |

> **最关键的一句认知**：Prometheus 的世界里，**"监控"= "目标暴露了指标" + "Prometheus 抓到了"**。
> 中间任何一环断了，你的指标就是空的。**"Prometheus 没数据"十有八九不是 Prometheus 坏了，是目标没暴露、或者暴露路径写错了、或者被 relabel drop 掉了。**

### 1.2 可观测性三支柱：指标 / 日志 / 链路各解决什么问题

官方对 Prometheus 的定位是"systems monitoring and alerting toolkit"——**它只管指标这一根柱子**。三支柱的分工必须先分清，否则会出现"指标做了全套，一出事还是查不出原因"。

| 支柱 | 英文 | 数据形态 | 回答什么问题 | 典型技术栈 | 成本特征 |
|---|---|---|---|---|---|
| **指标** | Metrics | 数值时间序列 | **有没有问题？**（趋势、比例、超阈值） | Prometheus + Grafana | 低：聚合数据，可长期存储 |
| **日志** | Logs | 文本行 + 标签 | **为什么有问题？**（具体错误内容） | Loki / ELK | 高：原始数据，存储贵 |
| **链路** | Traces | 跨服务调用链 + 耗时 | **问题在哪一环？**（哪一跳慢） | Jaeger / Tempo / OTel | 中高：采样存储，需上下文传播 |

**一个典型的线上故障，三支柱是怎么接力查的**：

```
用户反馈"下单接口很慢"
   │
   ├─ 指标（Prometheus）：p99 延迟从 80ms 涨到 2.4s，错误率从 0.1% 涨到 3%
   │     → 确认：是真实劣化，不是用户错觉；劣化从 10:05 开始
   │
   ├─ 链路（Trace）：10:07 的 trace 显示 order-service → inventory-service
   │     这一跳耗时 2.1s，其余各跳正常
   │     → 锁定：问题在 inventory-service 内部，不在网关
   │
   └─ 日志（Loki）：inventory-service 10:05 起大量 "connection pool exhausted"
   │     → 找到根因：连接池被打满，DB 慢查询导致连接不释放
```

### 1.3 为什么不能只做指标（三支柱缺一的具体事故）

| 只做了 | 会发生什么 | 缺的那根柱子补的是什么 |
|---|---|---|
| **只做指标** | 知道"订单服务错误率涨了"，但**不知道是哪个用户、哪个请求、哪句 SQL 出错**。最后靠"上机器 `tail -f` 日志"——容器环境下日志随 Pod 销毁，**证据没了** | **日志**：错误的具体内容与上下文 |
| **只做日志** | 有了海量日志，但**没有"错误率从 0.1% 到 3%"这种可告警的量**。没法配告警，因为日志没有稳定的数值口径 | **指标**：可阈值、可聚合、可告警 |
| **只做链路** | 知道慢在哪一跳，但**没有全局容量视图**。也没法回答"上周和这周相比流量涨了多少" | **指标**：趋势、容量、SLO |
| **只做指标且用平均值** | 平均延迟 120ms 看着正常，实际上 1% 的请求是 8s。**平均值把长尾抹平了** | 指标内部要用 **histogram 分位数**而不是平均值 |

> **面试能这么答，比"我用了 Prometheus 和 Grafana"高一档**：
> "我的排查顺序是**指标发现异常 → Trace 定位到哪一跳 → 日志看根因**。
> 指标回答'有没有问题'，成本低、可长期存，适合做告警；
> 日志回答'为什么'，但成本高、容器里易丢，只在定位时用；
> Trace 回答'在哪一环'，需要采样和上下文传播。
> **只做指标是典型的'知道出事了但修不好'**——我们上线前会检查这三样是不是都有，
> 而不是等出事才发现没法查。"

### 1.4 Pull 模型 vs 推模型

这是 Prometheus 最核心的架构选择，也是它和 Zabbix、StatsD 系最大的区别。

```
        ┌──────────── 拉取（Pull）：Prometheus 主动去问        ────────────┐
        │                                                            │
        │   Prometheus ──── GET /metrics ────▶ 目标应用 / exporter      │
        │        ▲                                                    │
        │        └──────────── 推入（Push）：目标主动塞给网关 ─────────┘
        │                    │
        │              Pushgateway ────▶ Prometheus 去拉 Pushgateway
        │
        └────────────────────────────────────────────────────────────┘
```

| 维度 | **拉（Pull）** | **推（Push）** |
|---|---|---|
| 谁发起 | Prometheus（中心） | 目标（边缘） |
| 目标不可达时 | **抓不到，`up=0` 本身就是一个信号** | 目标连不上网关，**静默丢失，中心端什么都不知道** |
| 目标生命周期 | **目标被删了，自动停止抓，自动打 stale 标记** | 目标被删了，**除非它主动发"我下线了"，否则中心端一直留着旧数据** |
| 新实例加入 | 改配置即可，**中心端控制节奏** | 每个新实例都要自己知道往哪推 |
| 中心端压力 | 中心要主动发起 N×M 次请求 | 中心被动接收 |
| 适合场景 | 目标数量可控、需要感知"目标是否活着" | 目标在 NAT/防火墙后、生命周期极短、批处理任务 |

**Prometheus 官方对推模型的态度**（原文）：官方在 `overview` 页明确写 "pushing time series is supported via an intermediary gateway"——**支持，但只是"支持"**，推的落地形态是 **Pushgateway**（一个中转站），Prometheus 仍然从 Pushgateway **拉取**。

> **关键理解**：Prometheus 的世界观里**没有"目标直连中心"这条路**。
> 推模型在 Prometheus 生态里的正确实现是：**目标 → Pushgateway → Prometheus 拉 Pushgateway**。
> 这条路适合 **短生命周期任务**（Job 跑完就退出，抓不到了），
> **不适合**常规服务——服务直接用 exporter 暴露 `/metrics` 就行。

### 1.5 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Prometheus | — | 监控与告警工具包，本体是一个 Go 写的单进程服务 |
| time series | 时间序列 | 一串带时间戳的样本，**由指标名 + 标签集唯一标识** |
| sample | 样本 | 一次观测：一个 float 值 + 一个毫秒精度时间戳 |
| label | 标签 | 键值对，是 Prometheus 的**维度**，也是**头号事故源** |
| metric name | 指标名 | 如 `http_requests_total` |
| scrape | 抓取 | Prometheus 主动发起的**一次独立 HTTP 请求** |
| scrape interval | 抓取间隔 | 多久抓一次，官方全局默认 **1m** |
| scrape timeout | 抓取超时 | 单次抓取的超时，官方全局默认 **10s**，**不能大于 scrape interval** |
| target | 目标 | 被抓取的地址，如 `10.0.0.1:9100/metrics` |
| job | 任务 | 一组配置的抓取目标，抓来的指标自动带 `job="<job_name>"` 标签 |
| instance | 实例 | 一个目标地址对应的标签，值为 `host:port` |
| service discovery (SD) | 服务发现 | 自动发现目标地址的机制 |
| relabeling | 重标记 | 在**抓取前**或**入库前**改写/筛选标签与目标 |
| exporter | 导出器 | 把某个系统的指标翻译成 Prometheus 格式的小程序 |
| TSDB | 时序数据库 | Prometheus 的本地存储引擎 |
| WAL | 预写日志 | Write-Ahead Log，崩溃恢复用的写入日志 |
| retention | 保留期 | 数据保留多久，超期自动删 |
| compaction | 压缩合并 | 把小块合并成大块，减小索引开销 |
| remote write | 远程写入 | 把采样点写到远端存储系统 |
| recording rule | 记录规则 | 预计算复杂查询，结果存成新的时间序列 |
| alerting rule | 告警规则 | 表达式满足条件时产生告警 |
| Alertmanager | 告警管理器 | 负责告警路由、分组、抑制、静默的独立组件 |
| cardinality | 基数 | **标签取值组合的总数**，Prometheus 头号运维风险指标 |

---

## 2. 机制

### 2.1 数据模型：时间序列 = 指标名 + 标签集 + 样本

官方原文（`concepts/data_model/`）：

> "Every time series is uniquely identified by its metric name and optional key-value pairs called labels."
> （每条时间序列由它的指标名和可选的键值对（标签）唯一标识。）

```
一条时间序列 = 指标名 + 标签集（label set）

  http_requests_total{method="POST", handler="/orders", instance="10.0.0.1:8080"}
  └──── 指标名 ────┘└──────────── 标签集 ───────────────────────────┘
                            │
                            └─ 这三个标签的组合，全集群唯一标识这一条序列

样本（sample）= 一个 float64 值 + 一个毫秒精度时间戳
```

**两条必须记住的模型规则**（官方原文）：

1. **"The change of any label's value, including adding or removing labels, will create a new time series."**
   （任何一个标签值的变化，包括增加或删除标签，都会创建一条新的时间序列。）
   → **标签是"分裂器"**：加一个标签 = 序列数乘以该标签的取值数。

2. **"Labels with an empty label value are considered equivalent to labels that do not exist."**
   （空标签值的标签，等价于这个标签不存在。）
   → 官方举例：`http_requests_total{environment=""}` 会匹配到**根本没设 `environment` 标签**的那些序列。

**命名规范（官方 SHOULD 级建议）**：

| 对象 | 官方建议的正则 | 说明 |
|---|---|---|
| 指标名 | `[a-zA-Z_:][a-zA-Z0-9_:]*` | 超出这个集合在 PromQL 里需要引号包裹 |
| 标签名 | `[a-zA-Z_][a-zA-Z0-9_]*` | 同上 |
| 标签值 | 任意 UTF-8 | — |

**一条硬约束**：

> **Label names beginning with `__` (two underscores) MUST be reserved for internal Prometheus use.**
> （以 `__` 开头的标签名必须保留给 Prometheus 内部使用。）

这意味着 `__name__`、`__address__`、`__meta_kubernetes_pod_label_app` 这类标签你**只能读、不能自己造**——
而这些 `__meta_*` 标签恰恰是后面 relabel 的原料。

> **官方特别提醒（值得记住）**：**冒号 `:` 是保留给用户自定义 recording rule 用的**，
> exporter 和业务埋点**不应该**在指标名里用冒号。官方原话：
> "Colons (':') are reserved for user-defined recording rules. They SHOULD NOT be used by exporters or direct instrumentation."

### 2.2 标签是 Prometheus 的核心，也是头号运维事故源

**这一节请当成面试高频题准备。**

指标名是**有限**的（你能起出来的名字就那么几个），但**标签组合是无限的**。这决定了 Prometheus 的容量瓶颈**不在指标数量，而在标签组合数**。

**基数（cardinality）怎么算**：把所有指标的"标签取值组合数"加起来。

假设一个服务有 3 个实例，指标 `http_requests_total` 带 3 个标签：

| 标签 | 取值数 | 贡献的序列数 |
|---|---|---|
| `method` | GET / POST / PUT / DELETE = 4 | — |
| `status` | 200 / 400 / 404 / 500 = 4 | — |
| `instance` | 3 个 Pod = 3 | — |
| **合计** | 4 × 4 × 3 = **48** | 48 条序列 |

现在往标签里加一个 `user_id`：假设日活 10 万用户 → **4 × 4 × 3 × 100000 = 4,800,000 条序列**。
再加一个 `order_id`（每天 100 万单）→ 直接到 **十亿级**。

**这在工程上意味着什么**（官方在 `storage` 页给出了容量公式）：

> Prometheus stores an average of only 1-2 bytes per sample.
> ```
> needed_disk_space = retention_time_seconds * ingested_samples_per_second * bytes_per_sample
> ```

单样本只有 1-2 字节，**听起来很省**——但问题不在磁盘，在**内存**：
Prometheus 必须在内存里维护每条序列的状态（head chunk、索引），**序列数一多，内存先炸**。

**标签设计的硬规则（这三条请背下来）**：

| 规则 | 允许 | 禁止 | 理由 |
|---|---|---|---|
| **只用低基数枚举** | `service`、`method`、`status`、`code`、`namespace`、`pod`、`container` | — | 取值集合有限且可枚举 |
| **绝不把无界 ID 放进标签** | — | `user_id`、`order_id`、`trace_id`、`session_id`、`手机号`、`URL 全路径`、`原始 SQL` | 每个值都是一个新序列，直接把基数打爆 |
| **无界维度怎么办** | 用**日志**（Loki/ELK）查；或用 trace ID 做关联；或把 ID 做 **hash + 截断** 后放日志而不是指标 | — | 日志能按需检索，指标不能 |

> **真实事故形态**（本套资料的 D12 日课里记录过这个案例）：
> "给每个请求打上 `user_id` 标签，指标基数爆炸，一天写 200GB。"
> **正确做法**：标签只能是低基数维度（service、endpoint、status、method），
> 用户 ID、订单号、URL 全路径**绝对不能进标签**。
>
> **面试这么答**："核心原则是 label 必须是低基数枚举。
> `service` / `status` / `method` / `endpoint` 可以，`user_id` / `order_id` / 完整 URL 绝对不能。
> 高基数信息我用日志或 trace 去查，不放进指标。
> 我见过有人把 URL 全路径当 label，一天写了 200G。"

**怎么发现基数问题**（三条可执行的检查）：

| 检查 | 怎么做 | 看到什么算异常 |
|---|---|---|
| 总量体检 | `prometheus_tsdb_head_series` | 序列总数突然阶跃（而不是缓慢增长）= 有人加了高基数标签 |
| 找元凶 | `count by (__name__) ({__name__!=""})` | 某个指标名序列数占比异常高 |
| 找维度 | 在 Prometheus UI 的 "Explore metrics" 里点开该指标的 label 列表 | 看到 `user_id` / `request_id` 这类标签就是元凶 |

### 2.3 四种指标类型：counter / gauge / histogram / summary

**官方 `concepts/metric_types/` 的定义（逐条对照）**：

| 类型 | 官方定义 | 能否重置 | 语义 | 典型用途 | **错误用法（反面教材）** |
|---|---|---|---|---|---|
| **Counter** | "a cumulative metric that represents a single **monotonically increasing counter** whose value can only increase or **be reset to zero on restart**" | **能**（重启归零） | 只增不减的累计量 | 请求总数、任务完成数、错误总数 | **用来表示当前进程数**——它会变，官方原文点名："Do not use a counter to expose a value that can decrease." |
| **Gauge** | "a metric that represents a single numerical value that can **arbitrarily go up and down**" | **能**（且随时能） | 瞬时值 | 温度、当前内存占用、**并发请求数**、队列长度、CPU 使用率 | **用来记累计量**——只能靠相减才能得到增量，计算方式痛苦且易错 |
| **Histogram** | "records observations by **counting them in configurable buckets**. It also provides a **sum** of all observed values" | 桶计数是 counter（能重置） | 分布 | 请求延迟、响应大小 | **不配桶就用**——默认桶区间可能完全覆盖不到你的真实延迟范围，分位数算出来是错的 |
| **Summary** | "samples observations... calculates configurable **quantiles over a sliding time window**" | 计数与和是 counter（能重置） | 客户端预计算分位数 | 延迟分位数、请求大小分位数 | **跨实例聚合**——官方原话："aggregating the precomputed quantiles from a summary **rarely makes sense**... averaging the quantiles yields **statistically nonsensical values**" |

**Counter vs Gauge 的选择逻辑（一句话）**：

```
问自己：这个值会不会自己往下走？
  会  → Gauge（内存用量、连接数、队列长度、温度）
  不会 → Counter（累计请求数、累计错误数）
```

**Counter 的命名约定**：官方在 `data_model` 页说明——
> "By convention, time series containing float counters have a name ending on `_total` to help with the distinction."

所以 `http_requests_total` 是 counter，**名字本身携带类型信息**。

**Histogram 的三个序列（classic 形态）**：

```
基名 http_request_duration_seconds 会产生三条时间序列：

  http_request_duration_seconds_bucket{le="0.1"}    ← 累积计数（le = inclusive upper bound）
  http_request_duration_seconds_bucket{le="0.5"}
  http_request_duration_seconds_bucket{le="+Inf"}    ← 必须有 +Inf 桶
  http_request_duration_seconds_sum                  ← 所有观测值之和
  http_request_duration_seconds_count                ← 观测总次数（= bucket{le="+Inf"}）
```

**两个关键约束（官方原文，直接决定 L2 里的写法）**：

1. **"Remember, however, that these buckets are cumulative"**
   （记住，这些桶是**累积**的：每个桶统计的是**小于等于**该上界的观测数。）
   → `le="0.1"` 的桶里已经包含了 `le="0.05"` 的量。**做差分才能得到区间分布**。

2. **"If the lowest bucket's upper bound is greater than 0, then the lower limit is assumed to be 0"**
   + **最高桶的 le 必须是 `+Inf`**，否则 `histogram_quantile` 返回 `NaN`（官方明确写了这条）。

**Summary 的形态**：

```
基名 http_request_duration_seconds 会产生：

  http_request_duration_seconds{quantile="0.5"}   ← 客户端预计算的分位数
  http_request_duration_seconds{quantile="0.99"}
  http_request_duration_seconds_sum
  http_request_duration_seconds_count
```

**Histogram vs Summary 的选型（官方 `practices/histograms/` 的原话）**：

> "**you cannot aggregate quantiles** (e.g. to calculate the total 90th percentile latency for a service backed by multiple replicated workers)."
> "If your service runs replicated with a number of instances... you want to aggregate everything into an overall 95th percentile. However, aggregating the precomputed quantiles from a summary **rarely makes sense**."

官方给出的**坏例子**（请记住这一行，反例面试常问）：

```promql
avg(http_request_duration_seconds{quantile="0.95"}) // BAD!
```

官方给的**好例子**：

```promql
# classic histogram
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m]))) // GOOD.
```

> **结论（面试用）**：
> **多副本服务一律用 Histogram，不用 Summary。**
> 原因是 Summary 的分位数在客户端算好，跨实例没法合并——
> 你把 3 个实例的 P95 求平均，得到的数字**在统计上没有意义**（它既不是总体 P95，也不是任何实例的 P95）。
> Histogram 存的是分布本身，可以先合并桶再算分位数，结果有意义。

> **补充（v3 起的现代选择）**：官方 `metric_types` 页提到 **native histogram**（原生直方图）——
> 它以**单个复合样本**存储（count + sum + 动态桶），不需要手工配桶边界，**网络传输是原子的**
> （classic histogram 的各条序列是分开传的，可能部分传输）。
> 官方原话："If the instrumentation library you are using supports native histograms
> (currently this is the case for Go and Java), you should probably **prefer native histograms over classic histograms**."
> **但**：native histogram 需要 Prometheus 3.x + 客户端库支持 + 存储开销更高，
> 存量系统绝大多数仍是 classic histogram。**本篇与 L2 以 classic histogram 为主讲**，
> 因为这是你现在在生产里能查到的形态。

### 2.4 exporter 机制

**官方对 exporter 的定位**（`introduction/overview/` 原文）：

> "special-purpose **exporters** for services like HAProxy, StatsD, Graphite, etc."

**核心认知**：

> **Prometheus 不采集业务指标。Prometheus 只采集"暴露出来的指标"。**

这句话必须掰开揉碎理解：

```
你的业务代码
   │
   │  ① 埋点（用 client library 暴露 counter / gauge / histogram）
   │     或者
   │  ①' 你不埋点，改为部署一个 exporter 去翻译别人的数据
   ▼
HTTP 端点 /metrics  ←── Prometheus **主动 GET** 这个端点
   │
   │  返回纯文本，格式是：
   │  # HELP http_requests_total Total HTTP requests
   │  # TYPE http_requests_total counter
   │  http_requests_total{method="GET",code="200"} 1027
   ▼
Prometheus 解析 → 入库
```

**三类"指标来源"，责任完全不同**：

| 来源 | 谁负责暴露 | Prometheus 的角色 | 你要做什么 |
|---|---|---|---|
| **业务指标**（QPS、延迟、订单量） | **你的代码**（埋点 + client library） | 只负责拉 | 写埋点、选对指标类型、控制标签基数 |
| **中间件指标**（MySQL、Redis、Kafka） | **exporter**（`mysqld_exporter` 等） | 只负责拉 | 部署 exporter、配好连接参数 |
| **系统指标**（CPU、内存、磁盘） | **node_exporter** | 只负责拉 | 部署 node_exporter（通常 DaemonSet） |
| **K8s 对象指标**（Pod 状态、副本数、重启次数） | **kube-state-metrics** | 只负责拉 | 部署 kube-state-metrics |
| **容器指标**（容器 CPU / 内存 / 网络） | **cAdvisor**（kube-prometheus-stack 里内置） | 只负责拉 | 用现成 Chart 即可 |

> **这一节的实战含义**：**"Prometheus 里查不到我的业务 QPS"不是 Prometheus 的问题**，
> 是你**没有埋点**，或者埋了但没暴露到 `/metrics`。
> Prometheus **没有能力凭空产生业务指标**。

**Kubernetes 生态的三个核心 exporter（你要认得）**：

| Exporter | 暴露什么 | 典型指标名 | 谁部署 |
|---|---|---|---|
| **node_exporter** | 主机层：CPU、内存、磁盘、网络、load | `node_cpu_seconds_total`、`node_memory_MemAvailable_bytes` | 每节点一个 → **DaemonSet** |
| **kube-state-metrics** | K8s 对象状态：Pod/Pod/Deployment/Job 的**期望状态与实际状态** | `kube_pod_status_phase`、`kube_deployment_status_replicas_available`、`kube_pod_container_status_restarts_total` | 一个集群一个 → **Deployment** |
| **cAdvisor** | 容器层：每个容器的 CPU/内存/网络 | `container_memory_working_set_bytes`、`container_cpu_usage_seconds_total` | 由 kube-prometheus-stack 自动带上 |

> **kube-state-metrics 的定位要说准**：它暴露的是**K8s 对象的状态**（"声明 3 个副本，现在有 2 个 ready"），
> **不是**容器的资源用量。容器用量是 cAdvisor 的活。
> **分不清这两个，排障时会在"Deployment 副本没起来"和"容器 CPU 跑满"之间找错方向。**

**Exporter 的一个"副作用"要知道**：exporter 暴露的指标是**别人的指标**，
它的标签维度是 exporter 定的，**你改不了**。所以 `node_cpu_seconds_total` 有 `cpu="0"`、`mode="idle"` 这类标签，
而 `kube_pod_status_phase` 有 `phase="Pending"` 这类标签——**基数天然就被 exporter 设计好了**。
**这也是为什么"业务指标自己埋点"反而要更小心**：埋点时你自己决定标签，失控风险在你手里。

### 2.5 scrape 机制：一次抓取的完整生命周期

**这一节是本篇的技术核心之一，面试常问"Prometheus 是怎么抓数据的"。**

```
Prometheus 主循环（每 scrape_interval 触发一次）
   │
   ├─ ① 服务发现：算出本轮的目标集合
   │     static_configs / file_sd / kubernetes_sd → 一组 target 地址
   │     ★ 目标集合每次都会重算 —— 这是"Pod 重建后自动跟上"的原因
   │
   ├─ ② relabel_configs（目标重标记）★ 第一道闸门
   │     对每个候选目标：改写标签 / 决定"这个目标要不要抓"
   │     ★★ 官方原文："Labels starting with `__` will be removed from the label set
   │        after target relabeling is completed."
   │        （以 __ 开头的标签在目标重标记完成后会被移除）
   │
   ├─ ③ 发 HTTP GET：GET http://<address><metrics_path>
   │     协议 = scheme（http/https）默认 http，路径 = metrics_path 默认 /metrics
   │     ★★ **每一次抓取都是一次独立的、完整的 HTTP 请求，不是长连接、不是流式订阅**
   │        这一次请求失败（超时/连接拒绝/非 200）→ 本轮该目标 up=0
   │
   ├─ ④ 解析响应体：识别 # HELP / # TYPE 与样本行
   │     解析失败 → 本轮抓取失败 → up=0
   │
   ├─ ⑤ metric_relabel_configs（指标重标记）★ 第二道闸门
   │     对抓回来的每一个样本：改标签 / 丢样本
   │     ★★ 这一步发生在**写库之前**，所以这里的 drop 是"真丢"，省磁盘
   │
   ├─ ⑥ 写入 TSDB：追加到 head chunk，落 WAL
   │
   └─ ⑦ 更新 up / scrape_duration_seconds / scrape_samples_scraped 等自监控指标
```

**第 ② 步和第 ⑤ 步的区别，是本篇最高频考点**（下一节单独展开）。

**抓取是独立请求，不是长连接——为什么这件事重要**：

| 特性 | 独立 HTTP 请求（Prometheus 的做法） | 长连接 / 推送流 |
|---|---|---|
| 目标存活检测 | **请求失败就是失败信号**（`up=0`） | 连接断了才知道，可能已经丢数据 |
| 目标中途加入 | 下个周期自然带上 | 需要额外的注册/握手机制 |
| 目标中途退出 | 下个周期自然消失 | 需要主动通知下线 |
| 抓取压力 | 集中在周期边界（会有**抓取尖峰**） | 平滑 |
| Prometheus 被重启 | 无影响，重启后重新抓 | 断连，需要目标侧重连 |

> **一个实践推论**：Prometheus 的抓取是"周期脉冲"式的。
> 1000 个 target、15s 间隔 → 平均每秒约 67 个请求，但它们**都挤在周期边界**。
> 这就是为什么大集群里要给 Prometheus 配**分片（sharding）**，也是为什么抓取本身会给被监控端带来压力。

**抓取超时的官方规则**（`configuration/configuration/` 原文）：

> `scrape_timeout`: "How long until a scrape request times out. **It cannot be greater than the scrape interval.**"
> 默认值：**10s**（全局 `scrape_interval` 默认 **1m**）

```
约束：scrape_timeout ≤ scrape_interval
```

**如果目标很慢怎么办**？两个方向：

| 做法 | 效果 | 代价 |
|---|---|---|
| 调大 `scrape_timeout`（比如 30s） | 慢目标能抓到了 | **必须同时调大 `scrape_interval`**（否则违反约束，配置校验直接失败） |
| 调大 `scrape_interval`（比如 30s） | 降低压力、给抓取留时间 | **数据变稀疏**，`rate()` 窗口要跟着放大（见 L2） |

**target 的 up / down 状态到底是什么意思（高频考点）**：

| 状态 | 含义 | 典型原因 |
|---|---|---|
| **UP** | 本轮抓取**成功**——HTTP 请求成功、响应体**能解析** | 正常 |
| **DOWN** | 本轮抓取**失败** | 连接被拒 / DNS 解析失败 / **超时** / 状态码非 200 / **响应体解析失败**（哪怕 HTTP 200，但 `/metrics` 返回的是 HTML 错误页） |

**`up` 这个指标本身**：

```promql
up{job="my-service"}   # 1 = 最近一次抓取成功，0 = 失败
```

**它的三个用法**：

| 用法 | 查询 | 用途 |
|---|---|---|
| 告警"目标挂了" | `up == 0` | 最基础的可用性告警 |
| 算"抓取成功率" | `avg_over_time(up[1h])` | 1.0 = 一小时全成功；0.99 = 约 36 分钟有失败 |
| **区分"服务挂了" vs "Prometheus 挂了"** | `up == 0` **没有**告警 → **Prometheus 自己或网络出问题** | **这是最容易被忽略的排查起点** |

> **排障铁律（背下来）**：
> **"Prometheus 里什么都查不到"时，第一个要看的不是业务指标，是 `up`。**
> - `up` 全是 0 → **Prometheus 侧或网络侧的问题**（Prometheus 挂了、DNS 挂了、防火墙挡了、配置写错了）
> - `up` 全是 1 但业务指标没有 → **目标没暴露该指标，或被 `metric_relabel_configs` drop 了，或标签对不上**
>
> 这一条能省掉大量"以为是业务问题、其实是监控自己坏了"的排查时间。

### 2.6 服务发现：static / file_sd / kubernetes_sd

**Prometheus 支持的服务发现（SD）**有很多种（`configuration/configuration/` 列了十几种：static、file、http、https、DNS、Kubernetes、Consul、EC2、GCE、OpenStack、Docker Swarm 等），
但**日常只需要掌握三种**：

| SD 类型 | 配置项 | 目标来源 | 适合场景 |
|---|---|---|---|
| **静态** | `static_configs` | yaml 里写死的地址列表 | 本地实验、目标固定不变、外部托管的监控端点 |
| **文件** | `file_sd_configs` | 指定的文件（JSON/YAML）里的目标列表，**Prometheus 定期重读** | 目标列表由别的系统生成（CMDB、Ansible、Terraform） |
| **Kubernetes** | `kubernetes_sd_configs` | **K8s API**（list/watch） | **K8s 场景必用** |

**静态发现（最简单）**：

```yaml
scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:8080', 'localhost:8081']
        labels:
          group: 'production'
      - targets: ['localhost:8082']
        labels:
          group: 'canary'
```

**官方入门文档对 `static_configs` 的说明**（`getting_started/` 原文）：
> "The `job` name is added as a label `job=<job_name>` to any timeseries scraped from this config."
> （`job_name` 会作为 `job=<job_name>` 标签加到该配置抓到的所有时间序列上。）

**文件发现**：

```yaml
scrape_configs:
  - job_name: 'file-sd'
    file_sd_configs:
      - files:
          - /etc/prometheus/targets/*.json
        refresh_interval: 5m      # 官方默认 5m
```

> **file_sd 的价值在于"谁生成目标列表"**：
> CMDB / Ansible / Terraform 负责把"该监控谁"这件事算出来，写成 JSON 丢进目录，Prometheus 定期重读。
> **Prometheus 自己不做决策**，这在企业环境里很重要——**监控目标清单应该和 CMDB 同源**。

**Kubernetes 服务发现（K8s 场景必用）**：

```yaml
scrape_configs:
  - job_name: 'kubernetes-pods'
    kubernetes_sd_configs:
      - role: pod            # 必填
        namespaces:
          names: [demo, monitoring]
    relabel_configs:
      # 只保留真正在跑的容器（phase=Running）
      - source_labels: [__meta_kubernetes_pod_phase]
        action: keep
        regex: Running
      # 只保留有 annotation prometheus.io/scrape=true 的 Pod
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: "true"
      # 覆盖目标地址：Pod IP + 容器端口
      - source_labels: [__address__, __meta_kubernetes_pod_container_port_number]
        action: replace
        separator: ":"
        target_label: __address__
        replacement: "[1]:[2]"
        # ↑ 这里 [1] 是 __address__ 的第一个捕获组（Pod IP），[2] 是端口号
      # 把 Pod 标签复制成指标标签（K8s SD 的经典用法）
      - source_labels: [__meta_kubernetes_pod_label_app]
        target_label: app
        action: replace
```

**`role` 的六个取值（官方原文）**：

> "The Kubernetes role of entities that should be discovered. One of **endpoints, endpointslice, service, pod, node, or ingress**."

| role | 发现什么 | 典型用途 | 产出的 meta 标签示例 |
|---|---|---|---|
| **pod** | Pod | 抓 Pod 上运行的容器（应用自埋点） | `__meta_kubernetes_pod_name`、`__meta_kubernetes_pod_ip`、`__meta_kubernetes_pod_container_port_number`、`__meta_kubernetes_pod_label_*` |
| **service** | Service | 抓 Service 后端（**生产最常用**） | `__meta_kubernetes_service_name`、`__meta_kubernetes_service_label_*`、`__meta_kubernetes_service_type` |
| **endpoints** | Endpoints 对象及其关联 Pod | 抓 Service 后端（老写法，官方现在推荐 endpointslice） | `__meta_kubernetes_endpointslice_*` / `__meta_kubernetes_endpoints_*` |
| **endpointslice** | EndpointSlice 对象及其关联 Pod | **抓 Service 后端的推荐方式**（K8s 1.21+ 默认） | `__meta_kubernetes_endpointslice_address_target_kind` 等 |
| **node** | Node | 抓 node_exporter（每节点一个） | `__meta_kubernetes_node_name`、`__meta_kubernetes_node_label_*` |
| **ingress** | Ingress | 抓 Ingress 控制器自身指标 | `__meta_kubernetes_ingress_path`、`__meta_kubernetes_ingress_scheme` |

**用 role 抓 node_exporter 的标准写法**：

```yaml
scrape_configs:
  - job_name: 'node'
    kubernetes_sd_configs:
      - role: node
    relabel_configs:
      # 把 __address__ 从 API Server 地址换成节点地址:端口
      - source_labels: [__address__]
        action: replace
        target_label: __address__
        regex: (.+)(:9100)
        replacement: ${1}:9100
      - source_labels: [__meta_kubernetes_node_name]
        target_label: kubernetes_node
        action: replace
```

**K8s SD 的两个必知细节**：

| 细节 | 说明 |
|---|---|
| **`api_server` / `kubeconfig_file` 留空即可** | 官方原文："If left empty, Prometheus is assumed to run inside of the cluster and will discover API servers automatically and use the pod's CA certificate and bearer token file at `/var/run/secrets/kubernetes.io/serviceaccount/`." —— **在集群里跑就用 ServiceAccount，不用配 kubeconfig** |
| **RBAC 要给够** | Prometheus 需要 list/watch 相关资源。官方原文还提醒：`attach_metadata` 打开 node/deployment 等附加元数据会**需要额外的 list/watch 权限** |

**namespace 过滤的两种写法**（官方 `namespaces` 字段）：

```yaml
kubernetes_sd_configs:
  - role: pod
    namespaces:
      own_namespace: true          # 只看 Prometheus 自己所在 ns
      names: [demo, monitoring]    # 或者指定 ns 列表
```
> 不写 `namespaces` = **所有命名空间都发现**（官方原文："If omitted, all namespaces are used."）。
> **这是采集面失控的常见原因**——一个 label selector 写漏了，就抓了全集群。

### 2.7 relabel_configs 与 metric_relabel_configs 的区别（高频考点）

**这一节请当成面试必答题准备。**

**官方配置文档的定位（原文）**：

| 配置项 | 官方描述 |
|---|---|
| `relabel_configs` | "Target relabeling configurations **applied to targets before scraping**"（抓取**前**作用于目标） |
| `metric_relabel_configs` | "Metric relabeling configurations **applied to samples before ingestion**"（**入库前**作用于样本） |

**一句话记法**：

> **`relabel_configs` 决定"抓谁"，`metric_relabel_configs` 决定"留谁"。**
>
> **前者作用在"目标"这个对象上（抓不抓这个地址），后者作用在"样本"这个对象上（这条样本要不要进库）。**

**详细对照表**：

| 维度 | `relabel_configs` | `metric_relabel_configs` |
|---|---|---|
| **作用对象** | target（目标地址 + 目标标签） | sample（抓回来的每一条样本） |
| **执行时机** | **抓取之前** | **抓取之后、写库之前** |
| **能做什么** | ① 改写目标标签 ② **丢弃目标**（`keep`/`drop`） | ① 改写样本标签 ② **丢弃样本**（`keep`/`drop`） |
| **能否影响"抓不抓"** | **能**——drop 掉目标 = 这次请求根本不发出 | **不能**——样本已经抓回来了，drop 只是不写库 |
| **能删掉 `__` 开头的标签吗** | **能**（因为它在目标重标记阶段操作） | **不能**——`__` 标签在目标重标记后就已被移除 |
| **典型用途** | 从 `__meta_*` 里提取 K8s 元数据；过滤掉不该抓的 Pod | 删掉高基数标签（如 `user_id`）；只保留需要的指标名 |
| **写错的后果** | 目标被 drop → **该目标一个指标都没有**（且 `up` 也没有，因为根本没抓） | 样本被 drop → **指标缺失但 `up=1`**（抓到了，只是没留） |

**这段对照表直接给出两个排障场景的判据**：

| 现象 | 判据 | 结论 |
|---|---|---|
| 某个服务的指标**完全消失**，连 `up` 都查不到 | `relabel_configs` 把目标 drop 了 | 去查 relabel 的 `keep`/`drop` 规则 |
| 某个服务的 `up=1`（抓到了）但**某个指标名查不到** | `metric_relabel_configs` 把样本 drop 了 | 去查 metric relabel 的 `keep`/`drop` 规则 |

> **这是本篇最实用的一条排障判据**：
> **"`up` 有没有"是区分"目标层问题"和"样本层问题"的分水岭。**

**官方对 relabel 机制本身的说明（`configuration/configuration/` 原文）**：

> "The `source_labels` tells the rule what labels to fetch from the series. **Any labels which do not exist get a blank value ("").** Their content is concatenated using the configured `separator` and matched against the configured regular expression for the replace, keep, and drop actions."

**必记的三个默认参数**：

| 参数 | 官方默认值 | 说明 |
|---|---|---|
| `separator` | **`;`** | 多个 source_labels 值之间的连接符 |
| `regex` | **`(.*)`** | 匹配用；官方说明 "The regex is **anchored on both ends**"（两端锚定） |
| `replacement` | **`$1`** | 替换模板，捕获组用 `$1`、`$2` |

**⚠️ 锚定是最容易踩的坑**：

```
regex: "(.*):.*"     ✅ 官方示例，能匹配 "a:c"
regex: "a.*"         ❌ 不会匹配 "a:c"！
```

因为官方明确说正则**两端锚定**（等价于 `^(?:a.*)$`）。官方给出的绕法是：
> "To un-anchor the regex, use `.*<regex>.*`."

**为什么 relabel 是"双闸门"**（这张图值得记住）：

```
服务发现产出的候选目标
        │
        ▼
┌───────────────────────────────────┐
│ 闸门 1：relabel_configs           │  ← 作用在"目标"上
│   keep: 只留符合条件的             │     决定：这个地址要不要 GET？
│   drop: 剔掉不符合条件的            │     副作用：被 drop 的目标连 up 都没有
│   replace: 改写地址/标签           │
└───────────────────────────────────┘
        │  通过的才开始抓
        ▼
   HTTP GET /metrics  →  拿到 N 条样本
        │
        ▼
┌───────────────────────────────────┐
│ 闸门 2：metric_relabel_configs    │  ← 作用在"样本"上
│   keep/drop: 丢弃不需要的样本      │     决定：这条样本要不要写库？
│   replace: 改写标签（如删高基数）  │     副作用：抓都抓了，只是没存
└───────────────────────────────────┘
        │
        ▼
     写入 TSDB
```

### 2.8 keep / drop 等常用 relabel 动作

**官方 `relabel_action` 全清单（`configuration/configuration/` 原文）**：

| action | 官方语义 | 作用阶段 | 典型用途 |
|---|---|---|---|
| `replace` | "Match `regex` against the concatenated `source_labels`. Then, set `target_label` to `replacement`..." | 两者都可 | **改标签 / 改地址**（最常用） |
| `keep` | "**Drop targets for which `regex` does not match** the concatenated `source_labels`." | 两者都可 | **白名单过滤** |
| `drop` | "**Drop targets for which `regex` matches**..." | 两者都可 | **黑名单过滤** |
| `hashmod` | "Set `target_label` to the `modulus` of a hash of the concatenated `source_labels`." | 两者都可 | **把目标打散成 N 份**（做 Prometheus 分片时用） |
| `labelmap` | "Match `regex` against **all source label names**... copy the values of the matching labels to label names given by `replacement`" | 两者都可 | 按名字批量搬运标签（如 `__meta_*` → 正式标签） |
| `labeldrop` | "Match `regex` against all label names. Any label that matches will be removed from the set of labels." | 两者都可 | **删掉高基数标签**（如 `user_id`） |
| `labelkeep` | 只保留匹配 `regex` 的标签 | 两者都可 | 白名单式保留标签 |
| `lowercase` | 把拼接后的值转小写 | 两者都可 | 归一化取值 |
| `uppercase` | 把拼接后的值转大写 | 两者都可 | 归一化取值 |
| `keepequal` | "Drop targets for which the concatenated `source_labels` **do not match** `target_label`." | 两者都可 | 只保留值等于目标标签的 |
| `dropequal` | "Drop targets for which the concatenated `source_labels` **do match** `target_label`." | 两者都可 | 剔除与目标标签同值的 |

> **注意 `keep` 和 `drop` 的方向是相反的**，这是高频记错点：
> - `keep`：**不匹配** regex 的**丢掉**（留下匹配的）
> - `drop`：**匹配** regex 的**丢掉**（留下不匹配的）
>
> 记法：**`keep` = keep matching ones（留下匹配的）；`drop` = drop matching ones（丢掉匹配的）。**

**三个高频实战模板**：

**① K8s 场景：从 Pod annotation 里取端口**（官方 kube-prometheus 生态的标准套路）

```yaml
relabel_configs:
  - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_port]
    action: replace
    target_label: __address__
    regex: (.+)(?::\d+)?;(\d+)
    replacement: $1:$2
    # ↑ $1 = Pod IP（来自 __address__ 的第一个捕获组由前面步骤写入），$2 = 端口号
```

**② 删掉高基数标签（防基数爆炸的最后一道防线）**：

```yaml
metric_relabel_configs:
  - action: labeldrop
    regex: "user_id|order_id|request_id|session_id|trace_id"
    # ★★★ 这条规则能救一整台 Prometheus，务必放在 metric_relabel_configs 里
```

**③ 排除自己这个 target（避免"自己监控自己"的循环）**：

```yaml
relabel_configs:
  - source_labels: [__address__]
    regex: 'localhost:9090'
    action: drop
```

> **⚠️ 官方硬性约束（踩坑必记）**：
> **"Labels starting with `__` will be removed from the label set after target relabeling is completed."**
> （以 `__` 开头的标签在**目标重标记完成后会被移除**。）
> 这意味着：
> - **想写 `__address__`（目标地址），必须在 `relabel_configs` 里写**（目标阶段），写进 `metric_relabel_configs` 无效
> - **想在指标上保留 `__meta_*` 信息，也必须在 `relabel_configs` 里显式搬运**成普通标签名（如 `target_label: app`）
> - `__name__` 是特殊的（它是指标名本身），不能被删

### 2.9 TSDB 存储：WAL、时间分块、retention、compaction

**官方 `storage` 页把本地存储讲得很细，这一段是面试"Prometheus 怎么存数据"的标准答案。**

**磁盘布局（官方原文的结构）**：

```
./data
├── 01BKGV7JBM69T2G1BGBGM6KB12          ← 一个 block 目录（已完成的 2 小时块）
│   └── meta.json
├── 01BKGTZQ1SYQJTR4PB43C8PD98
│   ├── chunks                             ← 样本数据（segment 文件，每个最大 5V）
│   │   └── 000001
│   ├── tombstones                         ← 删除记录（延迟删除标记）
│   ├── index                              ← 索引：指标名+标签 → 时间序列 的映射
│   └── meta.json                          ← 块元信息
├── chunks_head                            ← 当前正在写的 head（内存 + mmap）
│   └── 000001
└── wal                                    ← 预写日志（Write-Ahead Log）
    ├── 000000002
    └── checkpoint.00000001
       └── 00000000
```

**四个关键机制**：

| 机制 | 官方原文（要点） | 为什么重要 |
|---|---|---|
| **按时间分块** | "Ingested samples are grouped into **two-hour blocks**." | 查询时只需加载相关的块，不用扫全量 |
| **WAL（预写日志）** | "The current block for incoming samples is kept **in memory and is not fully persisted**. It is secured against crashes by a **write-ahead log (WAL) that can be replayed when the Prometheus server restarts**." WAL 文件存在 `wal` 目录，**128MB 一段**，**至少保留 3 个 WAL 文件** | **崩溃恢复靠它**。但 WAL 只是"防崩溃"，**不是"防丢失"**（见下文重启丢数据） |
| **Compaction（压缩合并）** | "The initial two-hour blocks are eventually compacted into longer blocks in the background. Compaction will create larger blocks containing data spanning up to **10% of the retention time, or 31 days, whichever is smaller**." | 块太小 → 索引文件太多 → 查询慢；块太大 → 查询要加载过多数据 |
| **Retention（保留期）** | 见下 | 直接决定磁盘占用 |

**retention 的两个参数（官方 `storage.tsdb.*` 参数）**：

| 参数 | 官方默认值 | 官方说明 |
|---|---|---|
| `--storage.tsdb.path` | `data/` | 数据目录 |
| `--storage.tsdb.retention.time` | **15d** | 官方原文："If neither this flag nor `storage.tsdb.retention.size` is set, the retention time defaults to `15d`." |
| `--storage.tsdb.retention.size` | **0（禁用）** | 存储块最大字节数，最老的数据先被删；单位 B/KB/MB/GB/TB/PB/EB，**按 2 的幂**（1KB = 1024B） |
| `--storage.tsdb.wal-compression` | **2.20.0 起默认开启** | 官方原文：WAL 体积**大约可减半**，CPU 额外开销很小。**注意**：开启后降级到 2.11.0 以下需删 WAL |

> **⚠️ retention 的两个官方细节（容易漏）**：
> 1. **"If both time and size retention policies are specified, whichever triggers first will be used."**
>    （两个都配时，**先触发的那个生效**。）
> 2. **"Expired block cleanup happens in the background. It may take **up to two hours** to remove expired blocks. Blocks must be **fully expired** before they are removed."**
>    （过期块的后台清理**最多要两小时**才删完，而且必须**整块完全过期**才删。）
>    → **实际磁盘占用会略高于你配的 retention**，这是正常现象，不是 bug。

**磁盘容量的官方公式（背下来，面试能直接用）**：

```
needed_disk_space = retention_time_seconds × ingested_samples_per_second × bytes_per_sample
```

官方补充："Prometheus stores an average of only **1-2 bytes per sample**."

**官方给的降盘手段（顺序有讲究）**：

> "To lower the rate of ingested samples, you can either **reduce the number of time series you scrape**... or you can **increase the scrape interval**. However, **reducing the number of series is likely more effective**, due to compression of samples within a series."

> **关键判断**：**降基数比降频率有效得多**。
> 序列数减半 → 磁盘和内存都减半；抓取间隔从 15s 变 30s → 磁盘减半但**告警精度和 `rate()` 窗口都要跟着改**。
> **所以第一动作永远是砍基数。**

**官方给的 retention.size 取值建议**：

> "we recommend setting the retention size to, at most, **80-85% of your allocated Prometheus disk space**. The remaining 15-20% buffer covers the temporary extra space required by in-progress compactions."

**为什么必须留 15-20% 缓冲**（官方原文的机制说明）：
> "Because both the source blocks and the new compacted block must coexist on disk, on-disk size can briefly exceed `storage.tsdb.retention.size`; the excess is released when the next retention cleanup removes the source blocks."

> **一句话记住**：**compaction 期间新旧块同时在盘上**，所以磁盘会**瞬时超过**你配的 retention.size。

**⚠️ 文件系统的一条硬警告（官方 CAUTION）**：

> "**Non-POSIX compliant filesystems are not supported** for Prometheus' local storage as unrecoverable corruptions may happen. **NFS filesystems (including AWS's EFS) are not supported.** ... It is strongly recommended to use a **local filesystem** for reliability."

→ **在 K8s 里给 Prometheus 挂 PVC 时，不要用 NFS 类存储**。用云盘的本地块存储（Local PV / EBS / 阿里云 SSD）。

**备份的正确方式（官方原文）**：

> "**Snapshots are recommended for backups.** Backups made without snapshots run the risk of losing data that was recorded since the last TSDB block was created, which typically happens **every two hours**, covering the last three hours of samples."

→ **不基于快照的备份，最多可能丢 2-3 小时的数据**。

### 2.10 remote write：把数据交给远端存储

**官方 `storage` 页的定位（原文）**：

> "Prometheus's local storage is limited to a **single node's scalability and durability**. Instead of trying to solve clustered storage in Prometheus itself, Prometheus offers a set of interfaces that allow integrating with remote storage systems."

**remote write 的语义**：

```
Prometheus 本地
   │  ──── remote write（把样本异步写到远端）────▶ 远端存储
   │                                              （Thanos Receive / Cortex / Mimir / VictoriaMetrics / 云厂商托管）
   │
   └── 本地仍然保留一份（受 retention 限制）
```

**配置形态（官方 `remote_write` 配置块）**：

```yaml
remote_write:
  - url: "http://thanos-receive:19291/api/v1/receive"
    # 队列与重试由远端接收端和 Prometheus 的 WAL 机制共同保证
```

> **remote write 的关键性质**（决定架构选择）：
> - **异步**的：写远端失败不影响本地入库，Prometheus 会重试
> - **不删本地数据**：本地照样按 retention 过期
> - **远端是"最终一致"的**：网络抖动时会出现本地有、远端没有的空洞
> - **remote read 是另一回事**：从远端读回数据，用于把历史数据补齐到查询层

**remote write 写到哪里（这就是"为什么需要 Thanos/Mimir"的答案）**：

| 方案 | 定位 | 一句话 |
|---|---|---|
| **Thanos**（Sidecar / Receive / Query） | 开源横向扩展方案 | Sidecar 挂到每个 Prometheus 旁边，把各集群的块拼成一个全局视图 |
| **Cortex / Mimir** | Grafana 系的分布式存储 | 多租户、长期存储、水平扩展 |
| **VictoriaMetrics** | 性能优先的替代实现 | 单机就能扛很大规模，**但生态与配置兼容性不如 Prometheus** |
| **云厂商托管 Prometheus**（阿里云 / 腾讯云） | 免运维 | 详见 `help.aliyun.com/zh/prometheus/` |

### 2.11 为什么 Prometheus 单机有上限（→ Thanos / Mimir）

**这一节是面试"Prometheus 有什么局限"的答案，必须讲得具体。**

**官方明说的局限（`storage` 页原文）**：

> "Note that a limitation of local storage is that it is **not clustered or replicated**. Thus, it is **not arbitrarily scalable or durable** in the face of drive or node outages and **should be managed like any other single node database**."

拆开就是三条硬约束：

| 约束 | 官方原文 | 后果 |
|---|---|---|
| **① 单机存储，不集群** | "not clustered" | 磁盘容量 = 单机容量上限；**加机器没用**（除非用 Thanos 拼） |
| **② 不复制，节点挂了数据可能丢** | "not replicated" | **高可用必须靠上层**（Sidecar 多副本 + 对象存储） |
| **③ 要像单节点数据库一样运维** | "should be managed like any other single node database" | 备份、扩容、容量规划都得自己做 |

**再叠加一条来自架构本身的限制**：

| 限制 | 说明 | 何时会撞上 |
|---|---|---|
| **查询能力受单机内存/CPU 限制** | Prometheus 要在内存里维护全部 head 序列；查询并发一高就 OOM / 超时 | 序列数百万级，或看板多人高频刷新 |
| **抓取目标数有上限** | 目标多到一定程度，抓取本身打满网络与 CPU | 万级 target |
| **单机多租户不行** | 没有租户隔离，A 团队的高基数指标会拖垮 B 团队的查询 | 公司里多个团队共用一套 Prometheus |

**Thanos / Mimir 分别解决什么（官方 `thanos.io` 与 Prometheus 官方定位）**：

| 能力 | Thanos | Cortex / Mimir |
|---|---|---|
| **跨集群全局查询** | ✅ Sidecar 把各 Prometheus 的块上传到对象存储，Query 层合并 | ✅ 原生支持多租户 |
| **长期存储** | ✅ 对象存储（S3/OSS） | ✅ 对象存储 |
| **不丢数据** | ✅ 多副本 + 对象存储 | ✅ |
| **水平扩展** | ✅ Query 层可水平扩展 | ✅ 原生分布式 |
| **多租户隔离** | 弱 | ✅ 强 |
| **部署复杂度** | 中（Sidecar + Query + Store） | 高（完整分布式组件） |

> **面试这么答**：
> "Prometheus 是**单机自治**的——官方明确说它的本地存储**不集群、不复制**，
> 所以它的存储与查询能力都受单机限制，要'像管理一个单节点数据库'那样运维它。
> 典型撞墙场景有三个：**集群大了序列数上百万、单机内存扛不住**；
> **要跨集群看同一个业务的全景**；**要多团队共用且相互不能影响**。
> 这时候的解法不是硬扛 Prometheus，而是分层：
> **Prometheus 继续做采集与本地短期存储**（这是它最擅长的），
> **Thanos Sidecar + 对象存储做长期存储和全局查询**，
> **需要多租户隔离就上 Cortex/Mimir**。
> **关键认知：让 Prometheus 做它擅长的（拉、存短期、算规则），把扩展性交给专门的系统。**"

### 2.12 Prometheus Operator 的角色与 ServiceMonitor vs PodMonitor

**Operator 的官方定位（`prometheus-operator.dev` 原文）**：

> "Prometheus Operator is a **Kubernetes Operator** that provides **Kubernetes native deployment and management** of Prometheus and related monitoring components."

它提供的 CRD 清单（官方原文）：
> Prometheus · Alertmanager · ThanosRuler · **ServiceMonitor** · **PodMonitor** · Probe · PrometheusRule · AlertmanagerConfig · PrometheusAgent · ScrapeConfig

**它解决的核心问题（官方 Goals 原文）**：

> "**Configuration Abstraction and Validation** — Instead of learning and manually writing Prometheus Relabeling rules (which can be time consuming), you can simply use **Kubernetes Label Selectors**. `ServiceMonitor`, `PodMonitor` and `Probe` custom resources provide this abstraction."

> **一句话定位**：**Prometheus Operator 把"配置 Prometheus"这件事变成了"写 K8s 资源"。**
>
> **没有 Operator 时**：你改 `prometheus.yml` → 通过 ConfigMap 挂进去 → 改完还要重启/热加载
> **有 Operator 时**：你写一个 `ServiceMonitor` CR → Operator 自动生成对应的 scrape 配置并热加载
>
> **这就是"配置即代码"落到监控上的形态**：**监控目标清单和业务部署清单一样，走 Git、走 PR、被人 review。**

**Operator 的三大能力（官方原文归纳）**：

| 能力 | 说明 |
|---|---|
| **Kubernetes Custom Resources** | 用 CR 部署和管理 Prometheus、Alertmanager 及相关组件 |
| **Simplified Deployment Configuration** | 版本、持久化、保留策略、副本数都能从原生 K8s 资源里配 |
| **Prometheus Target Configuration** | "Automatically generate monitoring target configurations based on familiar Kubernetes label queries; **no need to learn a Prometheus specific configuration language**" |

**ServiceMonitor vs PodMonitor（高频考点）**：

| 维度 | **ServiceMonitor** | **PodMonitor** |
|---|---|---|
| **官方定义** | "defines how Prometheus and PrometheusAgent can scrape metrics from a group of **services**" | "defines how Prometheus and PrometheusAgent can scrape metrics from a group of **pods**" |
| **选择对象** | **Service / Endpoints**（靠 `selector` 选 Endpoints 对象） | **Pod**（靠 `selector` 选 Pod 对象） |
| **抓的端口从哪来** | `endpoints[].port`（**Service 的端口名**）或 `targetPort` | `podMetricsEndpoints[].port` / `targetPort`（**容器的端口**） |
| **官方给的关键字段** | `endpoints`（Endpoint 列表）、`selector`（选 Endpoints）、`targetLabels`（从 Service 搬标签）、`podTargetLabels`（从 Pod 搬标签）、`jobLabel` | `podMetricsEndpoints`（PodMetricsEndpoint 列表）、`selector`（选 Pod）、`podTargetLabels`、`jobLabel` |
| **典型场景** | 抓一个 **Service 后面的所有副本**（HTTP 服务） | 抓 **Pod 上的某个指定容器端口**（非标准端口、多个端口的 Pod、一个 Pod 跑多容器） |
| **ServiceMonitor 的官方限制** | "Service targets that **aren't backed by Pods** (for instance manually managed Endpoints or EndpointSlices) can only be scraped when the endpoint selects the service port with **`port` instead of `targetPort`** and **`filterRunning` is set to `false`**" | 无此限制 |
| **选型口诀** | **"对外提供服务 → ServiceMonitor"** | **"Pod 上有特殊端口 → PodMonitor"** |

> **为什么需要 PodMonitor**（一句话）：
> ServiceMonitor 抓的是 **Service 定义的端口**。
> 如果一个 Pod 上跑着 3 个容器、暴露 3 个端口，Service 只映射了其中 1 个——
> 另外两个端口 ServiceMonitor **抓不到**，这时候就得用 PodMonitor。
>
> **官方还提到第三种选择**：非 Pod 支撑的目标（手工维护的 Endpoints / EndpointSlice），
> 除了 ServiceMonitor 的限制写法外，**"you can also consider using the `ScrapeConfig` CRD"**。

**`jobLabel` 的默认行为（官方原文，写配置时容易踩）**：

| 字段 | 官方说明 |
|---|---|
| ServiceMonitor 的 `jobLabel` | "If this field is **empty or if the label doesn't exist** for the given Service, the `job` label of the metrics **defaults to the name of the associated Kubernetes Service**." |
| PodMonitor 的 `jobLabel` | "If the value of this field is empty, the `job` label of the metrics **defaults to the namespace and name of the PodMonitor object** (e.g. `<namespace>/<name>`)." |

> **⚠️ 这是一个高频的"指标对不上"根因**：
> ServiceMonitor 的 job 名默认是 **Service 名**，PodMonitor 的 job 名默认是 **`<ns>/<PodMonitor 名>`**。
> 两者命名规则**不一样**！你的看板里如果用 `job="my-svc"` 过滤，
> 换到 PodMonitor 上就会**一条都查不到**——因为 job 值变成了 `monitoring/my-pod-monitor` 这种形态。

**Prometheus CR 上的选择器机制（官方原文）**：

> "Prometheus and PrometheusAgent objects **select ServiceMonitor objects using label and namespace selectors**."

```yaml
apiVersion: monitoring.coreos.com/v1
kind: Prometheus
metadata:
  name: k8s
  namespace: monitoring
spec:
  serviceMonitorSelector:
    matchLabels: { release: k8s }    # 只认带这个标签的 ServiceMonitor
  serviceMonitorNamespaceSelector: {}  # 空 = 所有 ns
  podMonitorSelector:
    matchLabels: { release: k8s }
```

> **运维要点**：`serviceMonitorSelector` 不写 = **不选任何 ServiceMonitor**（全都不抓）；
> 写 `{}` = **选全部**。这两个语义完全不同，**写错就是"什么都没抓"或"什么都抓了"**。

### 2.13 关键参数与默认值表（官方标称）

| 参数 | 官方默认值 | 来源 | 实务要点 |
|---|---|---|---|
| `global.scrape_interval` | **1m** | 配置参考 | 官方入门教程的示例里写的是 `15s`（**示例值，不是默认值**）——**这里有个官方文档内部的不一致**：配置参考标称 `1m`，教程示例用 `15s`。**以配置参考的 `1m` 为准**，实际生产里 15s/30s 是常见实践 |
| `global.scrape_timeout` | **10s** | 配置参考 | **不能大于 scrape_interval**，否则配置校验失败 |
| `global.evaluation_interval` | **1m** | 配置参考 | 规则评估频率；`rate()` 窗口要 ≥ 4 个抓取周期（见 L2） |
| `global.body_size_limit` | **0**（不限制） | 配置参考 | 设了之后响应体超限 → **整次抓取失败** |
| `global.sample_limit` | **0**（不限制） | 配置参考 | 超限 → 整次抓取失败 |
| `global.target_limit` | **0**（不限制） | 配置参考 | 超限 → 目标被标记失败且不抓 |
| `global.keep_dropped_targets` | **0**（不限制） | 配置参考 | 保留被 relabel 丢弃的目标数（便于排查） |
| `global.external_labels` | 空 | 配置参考 | 与外部系统通信时附加的标签；**已有同名标签时不覆盖** |
| `scrape_config.job_name` | **必填、全局唯一** | 配置参考 | 会作为 `job` 标签加到该 job 的所有指标上 |
| `scrape_config.metrics_path` | `/metrics` | 配置参考 | exporter 的暴露路径 |
| `scrape_config.scheme` | `http` | 配置参考 | 可改 `https` |
| `scrape_config.honor_labels` | **false** | 配置参考 | false 时冲突标签被改名为 `exported_<label>`；true 时**目标自己的标签优先** |
| `scrape_config.honor_timestamps` | **true** | 配置参考 | false = 忽略目标提供的时间戳，用抓取时间 |
| `scrape_config.follow_redirects` | **true** | 配置参考 | 属于 http_config |
| `scrape_config.enable_http2` | **true** | 配置参考 | 属于 http_config |
| `relabel_config.separator` | **`;`** | 配置参考 | 多 source_labels 的连接符 |
| `relabel_config.regex` | **`(.*)`** | 配置参考 | **两端锚定** |
| `relabel_config.replacement` | **`$1`** | 配置参考 | 捕获组用 `$1`/`$2` |
| `relabel_config.action` | **`replace`** | 配置参考 | 全部取值见 2.8 节 |
| `file_sd_config.refresh_interval` | **5m** | 配置参考 | 重读目标文件的间隔 |
| `kubernetes_sd_config.role` | **必填** | 配置参考 | endpoints/endpointslice/service/pod/node/ingress |
| `kubernetes_sd_config.namespace` | 全部 ns | 配置参考 | 不写 = 所有命名空间 |
| `--storage.tsdb.path` | `data/` | 存储 | 数据目录 |
| `--storage.tsdb.retention.time` | **15d** | 存储 | **仅当 time 和 size 都未设置时生效** |
| `--storage.tsdb.retention.size` | **0（禁用）** | 存储 | 与 time 同时配时**先触发者生效** |
| `--storage.tsdb.wal-compression` | **2.20.0 起默认开** | 存储 | WAL 体积约减半 |
| 查询回溯窗口（lookback） | **5m** | 查询基础 | `--query.lookback-delta` 可改；**过时数据在这个窗口后不再返回** |

> **⚠️ 关于 `scrape_interval` 的官方文档不一致（必须知道，否则会跟人对不上）**：
> 官方**配置参考**（`configuration/configuration/`）明确标称 `global.scrape_interval` **默认 `1m`**；
> 官方**入门教程**（`getting_started/`）的示例 yaml 里写的是 `scrape_interval: 15s`，注释写 "By default, scrape targets every 15 seconds"。
> **两个都是官方页面，但入门教程这句注释是针对它那份示例文件的，不是全局默认值**。
> **以配置参考的 `1m` 为准**。生产中 15s/30s 是常见实践（kube-prometheus-stack 生态普遍用 30s），
> **但这是常见实践，不是官方标准**。

---

## 3. 动手

> **动手前提**：本篇**不执行**任何 Prometheus / docker / kubectl 命令（遵守资料的数据纪律：不新建环境、不拉镜像）。
> 下面是**可复制到你自己环境**的最小实验清单，每步写清"看到什么算对"。
> 参照 D12 日课的部署路径：[`../第2周/第12天.md`](../第2周/第12天.md)。

### 3.1 最小可运行配置：先抓自己

**为什么先抓自己**：Prometheus 自带 `/metrics`，不需要装任何东西就能跑通"配置 → 抓取 → 入库 → 查询"全链路。

**官方入门文档给的最小配置**（`getting_started/` 原文的 `prometheus.yml`）：

```yaml
global:
  scrape_interval:     15s   # 官方示例里的值（注意：官方配置参考标称默认是 1m）
  evaluation_interval: 15s
  external_labels:
    monitor: 'codelab-monitor'

rule_files:
  - 'prometheus.rules.yml'

scrape_configs:
  - job_name: 'prometheus'
    scrape_interval: 5s
    static_configs:
      - targets: ['localhost:9090']
```

**跑起来（官方路径）**：

```bash
# ① 启动（默认把数据存在 ./data）
./prometheus --config.file=prometheus.yml
```

**看到什么算对**：
- 终端日志出现 `Server is ready to receive web requests.`
- 浏览器打开 `http://localhost:9090/targets`，`prometheus` job 下的 `localhost:9090` 状态是 **UP**
- 打开 `http://localhost:9090/metrics`，能看到 Prometheus 自己暴露的指标

**最关键的一条自检查询**：

```promql
prometheus_target_interval_length_seconds
```

**看到什么算对**：返回若干条序列，每条有 `quantile` 标签。
官方文档的说明："these labels designate different latency percentiles and target group intervals"——**这个指标反映的是"实际两次抓取之间的间隔"**，是验证抓取节奏是否正常的利器。

### 3.2 加一个静态目标 + 验证 up

**官方入门文档的做法**（起 3 个 node_exporter，其中 1 个算 canary）：

```yaml
scrape_configs:
  - job_name: 'node'
    scrape_interval: 5s
    static_configs:
      - targets: ['localhost:8080', 'localhost:8081']
        labels:
          group: 'production'
      - targets: ['localhost:8082']
        labels:
          group: 'canary'
```

**验证清单（每条都写清"看到什么算对"）**：

| # | 检查 | 怎么看 | 期望 |
|---|---|---|---|
| 1 | target 状态 | `http://localhost:9090/targets` | 三个 endpoint 都是 **UP** |
| 2 | 指标名 | 先访问 `http://localhost:8080/metrics` 看原始指标名 | 能看到 `node_cpu_seconds_total` |
| 3 | 入库验证 | 查 `node_cpu_seconds_total` | 返回**每 CPU × 每 mode × 每 instance** 一条序列 |
| 4 | 标签是否带上 | 看返回结果的标签集 | `node_cpu_seconds_total{cpu="0",group="production",instance="localhost:8080",job="node",mode="idle"}` |
| 5 | **up 语义验证** | 查 `up{job="node"}` | 返回 3 条，值都是 `1` |

> **第 5 条是本节的重点**：官方入门文档就是用 `up` 来做这件事的——
> "To check whether Prometheus is scraping the new targets, open `http://localhost:9090/targets`"，
> 并指出 "If an endpoint shows `DOWN`, make sure the corresponding Node Exporter is still running on that port."

**故意搞坏一次（这是最有价值的实验）**：

```yaml
# 故意写错端口
static_configs:
  - targets: ['localhost:9999']    # 根本没有服务在监听
```

**看到什么算对**：
- `/targets` 里这个 target 状态变成 **DOWN**
- `up{job="node", instance="localhost:9999"}` 的值变成 **0**
- 错误信息是连接被拒（connection refused）

> **这个实验的意义**：把"`up=0`"和"抓取失败"在脑子里绑定起来。
> 以后生产上看到 `up=0`，你就知道**该去查网络/端口/路径，而不是去查业务**。

### 3.3 K8s 场景：kubernetes_sd 抓 Pod

**这一步对应 D12 日课里 kube-prometheus-stack 的实际形态。**

**最小 K8s 版 `prometheus.yml`**（只抓指定 ns 里带注解的 Pod）：

```yaml
global:
  scrape_interval: 30s
  evaluation_interval: 30s

scrape_configs:
  - job_name: 'k8s-pods'
    kubernetes_sd_configs:
      - role: pod
        namespaces:
          names: [demo]
    relabel_configs:
      # ① 只要 Running 的 Pod
      - source_labels: [__meta_kubernetes_pod_phase]
        action: keep
        regex: Running
      # ② 只要带 prometheus.io/scrape=true 注解的 Pod
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: "true"
      # ③ 端口从注解取；没注解就用 Pod 声明的第一个容器端口
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_port, __meta_kubernetes_pod_container_port_number]
        action: replace
        separator: ":"
        regex: ([^:]+)(?::\d+)?;(\d+)
        replacement: $1:$2
        target_label: __address__
      # ④ 把 Pod 标签搬成指标标签
      - source_labels: [__meta_kubernetes_pod_label_app]
        target_label: app
        action: replace
      - source_labels: [__meta_kubernetes_pod_label_team]
        target_label: team
        action: replace
```

**⚠️ 第 ③ 步的顺序有讲究**：`__address__` 此时是 `PodIP:port` 形态（默认端口 80 之类），
注解里的端口要覆盖它，所以 `separator: ";"` 让两个 source_labels 用 `;` 拼接，
再用 `([^:]+)(?::\d+)?;(\d+)` 拆开重组。这是 kube-prometheus 生态的标准套路。

**看到什么算对（逐条）**：

| # | 检查 | 期望 |
|---|---|---|
| 1 | `/targets` 页面 | 出现 `k8s-pods` job，实例是 PodIP:容器端口 |
| 2 | 目标数量 | **Pod 重建后自动跟着变**（这是 K8s SD 相对静态配置的最大价值） |
| 3 | `up{job="k8s-pods"}` | 每个 Pod 一条，全是 1 |
| 4 | `count by (app) (up{job="k8s-pods"})` | 按 app 分组数 Pod（**标签搬运成功**的证据） |
| 5 | 指标上有 `app` / `team` 标签 | 任意查一条业务指标，标签里有这两个 |

**第 4 条是验证 relabel 是否生效的最快方式**：
`count by (app) (up)` 能告诉你"标签搬运有没有成"，比逐个指标点开看快得多。

### 3.4 验证清单（收口）

| 检查项 | 查询 / 动作 | 期望 |
|---|---|---|
| Prometheus 抓到了自己 | `up{job="prometheus"}` | `1` |
| 目标数合理 | `count(up)` | 与实际目标数一致 |
| 没有高基数元凶 | `count by (__name__) ({__name__!=""})` | 集中在少数几个指标名上，无数量级跳变 |
| 标签维度干净 | 挑一个指标看标签列表 | **没有** `user_id` / `request_id` / 完整 URL |
| 服务发现生效 | Pod 扩缩容后看 `/targets` | 目标数跟着变 |

---

### 3.5 常用运维命令（按场景）

> **本节能做什么**：3.1–3.4 是"把采集跑起来"，本节是**每天值班 / 面试都会被问到的 Prometheus 运维命令**——按场景归类，每条配真实例子。**Prometheus 排障的肌肉记忆就来自这一张表。**

### ① 配置与规则语法校验（改完先过这一关，别直接 reload）

```bash
promtool check config /etc/prometheus/prometheus.yml          # 校验主配置（含 scrape 语法、relabel 写法）
promtool check rules /etc/prometheus/rules/*.yml              # 校验告警/记录规则（rate 窗口、for 语法）
promtool query instant http://localhost:9090 'up'            # 顺手验一下引擎能不能算
```

> **实战价值**：`promtool check config` 能在 reload 前挡掉 90% 的"改坏"——**很多生产事故是先改了 prometheus.yml 再 reload，结果整个实例起不来**。面试官常问"你怎么做配置变更管控"，高分答法是"所有改动走 Git + `promtool check config` 校验 + 灰度 reload"。

### ② 用 API 验端点与查数据（PromQL 实战的命令行版）

```bash
curl -s 'http://localhost:9090/api/v1/query?query=up'                                   # 直接打查询 API，返回 JSON
curl -s 'http://localhost:9090/api/v1/query?query=rate(http_requests_total[5m])'        # 跟表达式浏览器同一套引擎
curl -s 'http://localhost:9090/-/ready'                                                  # 健康探针（k8s readiness 用它）
curl -s 'http://localhost:9090/-/healthy'
curl -s 'http://localhost:9090/api/v1/status/config' | head -c 300                      # 看当前生效配置（确认 reload 成功）
curl -s 'http://localhost:9090/api/v1/status/flags'   | head -c 300                      # 看启动参数
```

> **实战价值**：`/api/v1/query` 是表达式浏览器的底层接口，**面试让你"不用 UI 怎么查 PromQL"就靠它**；`/status/config` 能确认"我改的配置到底 reload 进去没有"——改了不生效时第一反应看这里。

### ③ 抓取目标排障（up 指标是最该盯的仪表盘）

```bash
curl -s 'http://localhost:9090/api/v1/query?query=up'                 # up==0 的 target 就是没抓到
curl -s 'http://localhost:9090/api/v1/targets' | head -c 800         # 看每个 target 的 lastError / lastScrape
kubectl -n monitoring get endpoints prometheus-operators-kubelet      # 配合 kubernetes_sd 时验 endpoint 在不在
# file_sd 热加载：改了 json 文件，Prometheus 自动重读，不用 reload
curl -s 'http://localhost:9090/api/v1/targets?state=active' | grep -o '"health":"[a-z]*"' | sort | uniq -c
```

> **实战价值**：`up{job="x"} == 0` 是抓取失败的总开关；`/api/v1/targets` 里的 `lastError` 直接告诉你"连不上 / 超时 / 401 / 证书错"。**别猜，看 lastError。** 这是 §2.5 scrape 生命周期出问题时唯一的真相入口。

### ④ TSDB 与存储状态（内存打满、磁盘爆的源头）

```bash
curl -s 'http://localhost:9090/api/v1/status/tsdb' | head -c 600   # 看 headSeries / chunkCount / 内存占用
promtool tsdb list /var/lib/prometheus/data                        # 看 block 列表、时间范围、样本数
promtool tsdb analyze /var/lib/prometheus/data                     # 分析高基数指标（面试加分项）
du -sh /var/lib/prometheus/*                                       # 磁盘占用（retention 没生效会爆盘）
ls /var/lib/prometheus/wal                                         # WAL 目录（崩溃恢复靠它）
```

> **实战价值**：`status/tsdb` 的 `headSeries` 直接反映内存压力——**标签基数爆炸时 headSeries 会疯涨，OOM 前先看这个数**；`promtool tsdb analyze` 是定位"哪个指标名 cardinality 最大"的官方工具，面试被问"怎么排查 Prometheus 内存高"必答这一条。

### ⑤ exporter 与黑盒探测（指标抓取失败排查）

```bash
curl -s http://localhost:9100/metrics | grep -E '^node_cpu|^node_memory' | head   # 直连 node_exporter 验它自己有没有数据
curl -s http://localhost:9100/metrics | wc -l                                     # 抓不到时先确认 exporter 在不在
curl -s 'http://localhost:9115/probe?target=https://example.com&module=http_2xx'  # 黑盒探测：直接打 probe 接口看结果
curl -s 'http://localhost:9090/api/v1/query?query=probe_success'                 # blackbox 的探测成败指标
```

> **实战价值**：exporter 挂了，Prometheus 那头只会看到 `up==0`，**真因要去 exporter 自己的 `/metrics` 看**；黑盒探测失败优先用 `probe_success` 和 `probe_*` 指标定位是 DNS / TLS / 超时哪一段。

### ⑥ 通用：进程、端口、重载

```bash
ps aux | grep prometheus | grep -v grep          # 确认进程在、看启动参数（--config.file / --storage.tsdb.retention）
ss -ltnp | grep 9090                              # 端口在不在
curl -X POST http://localhost:9090/-/reload      # 改完配置热加载（需启动时开 --web.enable-lifecycle）
kill -HUP $(pidof prometheus)                    # 等价 reload 的另一条路
```

> **实战价值**：`/-/reload` 是改配置后不重启实例的唯一安全路径；但**必须启动时带 `--web.enable-lifecycle`**，否则 404——很多人第一次 reload 就卡在这。

---

### 3.6 运维排障实战（场景串命令）

> **把 3.5 的命令用起来**：三个最典型的 Prometheus 故障，按"现象 → 敲的命令 → 怎么读"走一遍。**都建议在有 Prometheus 的 kind / 测试环境练，别在真集群试。**

### 场景 A：某个 target 的 up 一直是 0（抓取失败）

```bash
curl -s 'http://localhost:9090/api/v1/query?query=up{job="myjob"}'   # 现象：value 是 0
curl -s 'http://localhost:9090/api/v1/targets' | python3 -m json.tool | grep -A3 '"scrapePool":"myjob"'  # 看 lastError
# 典型 lastError：context deadline exceeded（超时）/ connection refused（exporter 没起）/ 401（需 bearer token）
```

> **读法**：`up==0` 只说明"没抓到"，**真因在 `/targets` 的 `lastError`**。超时先查网络/端口，401 查 `authorization` 配置，证书错查 `tls_config`。**别改 Prometheus 配置去猜，先看 lastError。**

### 场景 B：Prometheus 内存持续涨、最后 OOM（标签基数爆炸）

```bash
curl -s 'http://localhost:9090/api/v1/status/tsdb' | grep -o '"headSeries":[0-9]*'   # 看 series 数
promtool tsdb analyze /var/lib/prometheus/data 2>/dev/null | grep -E 'Series|Label' | head   # 找罪魁指标
curl -s 'http://localhost:9090/api/v1/query?query=count by (__name__)({__name__=~".+"})'  # 全量 series 数
```

> **读法**：`headSeries` 涨得离谱（百万级还往上）基本就是高基数——回到 §2.2，问题八成在 `user_id`/`request_id`/`url` 这类无界标签；`promtool tsdb analyze` 会直接列出 top 高基数指标名，回去加 `metric_relabel_configs` 的 `drop` 动作（§2.7 / 2.8）。

### 场景 C：改了 prometheus.yml 但配置不生效 / reload 报错

```bash
promtool check config /etc/prometheus/prometheus.yml    # 先本地校验，把语法错挡在 reload 之前
curl -X POST http://localhost:9090/-/reload             # 校验过了再 reload
curl -s 'http://localhost:9090/api/v1/status/config' | head -c 300   # 看生效的配置是不是新内容
# 若 reload 返回 404：说明启动时没开 --web.enable-lifecycle
ps aux | grep prometheus | grep -- '--web.enable-lifecycle'   # 确认这个参数在不在
```

> **读法**：改了不生效，先 `promtool check config` 确认语法没问题，再 `reload`，最后用 `/status/config` 比对"线上跑的"和"你以为改的"是不是一份。**reload 404 一定是缺 `--web.enable-lifecycle`**，不是配置问题。

> **三个场景的共同心法**：**先看状态（up / headSeries / config）→ 再看错误（lastError / analyze / status）→ 最后才是改**。90% 的 Prometheus 排障卡在"没看 targets 的 lastError 就乱改 relabel"。

---

## 4. 逐字段表

### 4.1 scrape_config 关键字段表

| 字段 | 类型 | 必填 | 官方默认值 | 含义 | **写错的后果** |
|---|---|---|---|---|---|
| `job_name` | string | ✅ | 无（全局唯一） | 任务名，会作为 `job` 标签加到该 job 的所有指标上 | 重复 → 配置校验失败；写成中文 → 看板按 `job` 过滤时对不上 |
| `scrape_interval` | duration | | `global.scrape_interval`（全局 `1m`） | 本 job 的抓取间隔 | **小于 `scrape_timeout` → 校验失败**；调太小 → 序列数与写入量暴涨 |
| `scrape_timeout` | duration | | `global.scrape_timeout`（全局 `10s`） | 单次抓取超时 | **大于 `scrape_interval` → 校验失败**；过小 → 慢目标全 DOWN |
| `scrape_protocols` | list | | 依 `scrape_native_histograms` 而定 | 与目标协商的抓取协议（`PrometheusProto` / `OpenMetricsText0.0.1` / `OpenMetricsText1.0.0` / `PrometheusText0.0.4` / `PrometheusText1.0.0`） | 协议不匹配 → 解析失败 → `up=0` |
| `honor_labels` | bool | | `false` | 目标自带的标签与服务端标签冲突时谁优先 | `false` 时冲突标签被改名成 `exported_instance` / `exported_job`，**你的看板按 `instance` 过滤就空了** |
| `honor_timestamps` | bool | | `true` | 是否采用目标暴露的时间戳 | `false` = 用抓取时刻；目标时间戳不准时会导致数据点错位 |
| `metrics_path` | path | | `/metrics` | 抓取路径 | 写成 `/metric`（漏 s）→ 404 → `up=0`；目标其实暴露在 `/actuator/prometheus` → 全查不到 |
| `scheme` | string | | `http` | 请求协议（`http`/`https`） | 目标只支持 https 而写成 http → `up=0` |
| `params` | map | | 空 | 附加到 URL 的查询参数 | 传错参数 → 目标返回 400 → `up=0` |
| `body_size_limit` | size | | `0`（不限） | 未压缩响应体上限 | 超限 → **整次抓取失败**（`up=0`），不是截断 |
| `sample_limit` | int | | `0`（不限） | 单次抓取允许的样本数上限 | **超限 → 整次抓取失败**（`up=0`）——**这个字段是防基数爆炸的兜底** |
| `target_limit` | int | | `0`（不限） | 本 scrape config 接受的目标数上限 | 超限 → 目标被标记失败且**不执行抓取** |
| `relabel_configs` | list | | 空 | **抓取前**的目标重标记（决定"抓谁"） | 规则写错 → 目标被 drop → **连 `up` 都没有** |
| `metric_relabel_configs` | list | | 空 | **入库前**的样本重标记（决定"留谁"） | 规则写错 → 样本被 drop → **`up=1` 但指标缺失** |
| `follow_redirects` | bool | | `true` | 是否跟随 3xx | 目标重定向到别处而不跟随 → `up=0` |
| `enable_http2` | bool | | `true` | 是否启用 HTTP/2 | 老旧目标不支持 → 抓取失败 |
| `tls_config` | struct | | 空 | TLS 配置（`ca`/`cert`/`key`/`server_name`/`insecure_skip_verify`） | `insecure_skip_verify` 开着 → 安全风险；证书域名不匹配 → `up=0` |
| `proxy_url` / `no_proxy` | string | | 空 | 代理设置 | 代理配错 → 所有外网目标全 DOWN |
| `static_configs` | list | | 空 | 静态目标列表 | 地址写错 → `up=0` |
| `file_sd_configs` | list | | 空 | 文件目标发现（`files` + `refresh_interval`，默认 `5m`） | 文件格式错 → 该文件的目标全丢；路径不匹配 → 静默无目标 |
| `kubernetes_sd_configs` | list | | 空 | K8s 服务发现（`role` 必填） | `role` 写错 → 配置校验失败；`namespace` 不写 → **抓全集群所有 ns** |
| `rule_files` | list | | 空 | 规则文件路径（顶层，不在 scrape_config 里） | 路径写错 → 规则**静默不加载**，告警不触发 |

### 4.2 指标类型对照表

| 类型 | 语义（官方定义） | 能否重置 | 典型用途 | **错误用法（反面教材）** |
|---|---|---|---|---|
| **Counter** | "a cumulative metric that represents a single **monotonically increasing counter** whose value can only increase or be reset to zero on restart" | **能**（重启归零） | 请求总数、任务完成数、错误总数、重试次数 | ① 用来表示"当前进程数/当前连接数"（**会下降，必须用 Gauge**）；② 直接查它的值当速率（**必须先 `rate()`**）；③ 名字不带 `_total`（丢掉了类型线索） |
| **Gauge** | "a metric that represents a single numerical value that can **arbitrarily go up and down**" | **能**（随时） | 温度、当前内存用量、**并发请求数**、队列长度、CPU 使用率、Pod 副本数 | ① 用来记累计量（**只能用 Gauge 的差值算增量**）；② 对它用 `rate()`（`rate` 只适用于 counter，官方明确 "rate should only be used with counters"） |
| **Histogram** | "records observations by **counting them in configurable buckets**. It also provides a **sum** of all observed values" | 桶计数是 counter（**能**） | 请求延迟分布、响应大小分布、Apdex | ① **桶边界不覆盖真实值域**（分位数算出来是错的）；② 跨实例**先算分位数再聚合**（官方明确 "rarely makes sense"）；③ **没有 `le="+Inf"` 桶**（`histogram_quantile` 返回 `NaN`）；④ 用 `avg` 代替分位数（**长尾被抹平**） |
| **Summary** | "samples observations... calculates configurable **quantiles over a sliding time window**" | 计数与和是 counter（**能**） | 单实例、**不需要跨实例聚合**时的精确分位数 | ① **跨实例求平均分位数**（统计上无意义）；② 想要"事后改分位数/改窗口"（**客户端已算好，改不了**）；③ 以为它比 Histogram 便宜——**它是常数开销但分辨率固定** |

---

## 5. 横向对比

### 5.1 Pull vs Push

| 维度 | **Pull（Prometheus 默认）** | **Push（→ Pushgateway）** |
|---|---|---|
| 谁发起 | Prometheus 主动 GET `/metrics` | 目标主动 POST 到 Pushgateway，**Prometheus 再去拉 Pushgateway** |
| 目标不可达 | **`up=0`，本身就是告警信号** | 目标连不上网关 → **静默丢数据，中心端无感** |
| 目标下线 | 自动停止抓 + 打 stale 标记 | **除非目标主动发下线信号，否则中心端留着旧数据** |
| 新实例加入 | 服务发现自动带上 | 每个新实例要自己知道往哪推 |
| 中心端节奏 | 中心控制（`scrape_interval`） | 边缘决定 |
| 适用对象 | **常驻服务、节点、中间件** | **短生命周期任务**（Job 跑完就退出） |
| 官方立场 | 主流路径 | 官方支持，但落地形态是"**推给网关、再由 Prometheus 拉**" |

> **选型依据（一句话）**：
> **"目标活得比抓取周期久 → Pull；目标活不过一个抓取周期 → Push（Pushgateway）。"**
> 典型需要 Pushgateway 的是 **Kubernetes Job / 定时备份 / 一次性数据处理**——
> 它们跑完就退出，Prometheus 下次去抓时人已经没了。

### 5.2 Prometheus vs Zabbix

| 维度 | **Zabbix** | **Prometheus** |
|---|---|---|
| **采集模型** | **主动式**：Zabbix Server / Proxy **主动去轮询 agent 的 10050 端口** | **被动式（目标暴露）**：Prometheus 去 GET 目标的 `/metrics` |
| **谁装在目标上** | **必须装 Agent**（zabbix-agent） | **通常不装**——应用自己暴露 `/metrics`，或装 exporter |
| **目标地址变化** | 靠"自动发现规则 + 主机资产表"更新 | **K8s SD 自动跟随**（Pod 重建 IP 变也不用管） |
| **指标类型** | 数值型为主（可做趋势），**无原生分位数** | counter / gauge / **histogram / summary**，**原生支持 P95/P99** |
| **标签维度** | **"主机 + 监控项"**（模板 + 监控项，维度较固定） | **任意 label 组合**（维度灵活，**但基数失控是头号风险**） |
| **存储** | **Server 端集中存**（可配 MySQL/PostgreSQL） | **单机本地 TSDB**（横向扩展靠 Thanos/Mimir） |
| **告警治理** | 触发器 + 依赖关系（在 Server 里配） | **Alertmanager** 独立组件：分组 / 抑制 / 静默 / 路由 |
| **适合对象** | 主机、网络设备、硬件、中间件 | **容器、K8s 对象、微服务、业务指标** |

> **⚠️ 这里有一个必须说清的用词差别**（面试容易被问倒）：
> **Zabbix 是"agent 拉取、主动式"**——Zabbix Server 主动去连 agent；
> **Prometheus 是"目标暴露、被动式"**——目标把指标挂在 HTTP 端点上等 Prometheus 来取。
> **两者都是"中心主动"，但主动的方向相反**：
> Zabbix 主动的是**Zabbix 的协议（agent 协议）**，Prometheus 主动的是**标准的 HTTP GET**。
> 说"Prometheus 是推模型"是**错的**（那是 Pushgateway 场景）；
> 说"Zabbix 是被动式"也是**错的**（Zabbix Server 是主动去连的）。

> **真实的生产形态（本地民企常见的三件套）**：
> **Zabbix 看主机 + Prometheus 看容器 + 夜莺（Nightingale）统一告警治理。**
> D12 日课里记录的结论："**Zabbix 不是被淘汰，是边界收缩到主机与硬件层。**"
> 只会 Zabbix 面试官会认为你只懂传统运维。

### 5.3 Prometheus vs OpenTelemetry Collector

**这两个东西经常被搞混，说清楚它们是"上下游"关系。**

| 维度 | **OpenTelemetry Collector** | **Prometheus** |
|---|---|---|
| **是什么** | **厂商中立的遥测数据采集层**（pipeline：receiver → processor → exporter） | **指标存储与查询系统**（TSDB + PromQL） |
| **处理什么** | **metrics + logs + traces 三样都收** | **只有 metrics** |
| **部署形态** | **每个节点/每个 Pod 一个 agent**（DaemonSet / sidecar） | **一个或多个中心服务** |
| **核心能力** | 采样、批处理、脱敏、**格式转换**（OTLP ↔ Prometheus 等）、多后端 fan-out | 存储、时间序列查询（PromQL）、规则计算、告警产生 |
| **是否长期存储** | **不存**（是管道，不是数据库） | **存**（本地 TSDB，或 remote write 到远端） |
| **典型组合** | **Collector 采三支柱 → 指标转成 Prometheus 格式 → remote write 到 Prometheus** | — |

```
OpenTelemetry SDK (应用埋点)
   │  OTLP 协议
   ▼
OpenTelemetry Collector  ← 采样 / 脱敏 / 批处理 / 格式转换
   │  Prometheus exposition format
   ▼
Prometheus (remote write)  ← 存储 + PromQL 查询 + 规则
   │
   ▼
Grafana / Alertmanager
```

> **一句话区分（面试直接用）**：
> "**OTel Collector 是采集层，Prometheus 是指标存储与查询系统。**
> Collector 负责'把数据凑齐、处理好、送到该去的地方'，
> Prometheus 负责'把指标存下来、让人用 PromQL 查出来'。
> 它们不是竞品，**Collector 的 exporter 有一个就叫 `prometheus`，
> 也可以直接用 remote write 把指标写进 Prometheus**。"

> **补充（工具链的演进）**：OTel Collector 的出现是为了**解决厂商锁定**——
> 同一个应用换监控后端（Prometheus → Datadog）不用改埋点代码。
> Prometheus 生态则通过 **OpenTelemetry 的 Prometheus exporter + OTLP 接收端** 与之对接
> （Prometheus 3.x 的配置里有 `otlp` 配置块，专门做 OTLP 指标接收与标签提升）。

---

## 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 → 下次怎么防）

**排障铁律：先看 `up`，再定位是"目标层"还是"样本层"，最后才查业务。**

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **target 显示 DOWN** | `up=0` | 四类原因：① 连接被拒（端口错/服务没起）② DNS 解析失败 ③ **抓取超时**（目标太慢）④ **HTTP 200 但响应体解析失败**（`/metrics` 返回的是 HTML 错误页） | ① `curl` 目标地址看连不连得上 ② 查 DNS ③ 调大 `scrape_timeout`（**同时调大 `scrape_interval`**） ④ 直接访问 `/metrics` 看返回的是不是 Prometheus 格式 | 部署后必做"目标清单逐个 `curl` 一遍"；exporter 统一用标准 `/metrics` 路径 |
| **抓取超时 / target 间歇性 DOWN** | `up` 在 0/1 之间跳 | 目标响应慢于 `scrape_timeout`（默认 10s）。**常见于 exporter 抓数据库、目标负载高时** | 调大 `scrape_timeout` + `scrape_interval`；或让 exporter 用缓存/异步方式取数 | **超时必须 ≤ interval**，改一个就要改另一个；给 exporter 设"只读从库" |
| **指标完全查不到，连 `up` 都没有** | target 不存在 | **被 `relabel_configs` 的 `keep`/`drop` 规则 drop 了**（目标层问题） | 查 `relabel_configs` 的 `keep` 条件；用 `keep_dropped_targets` 保留被 drop 的目标便于排查 | **改 relabel 后必须验证 `count(up)` 的数量符合预期**；把 relabel 规则纳入 code review |
| **`up=1` 但某个指标名查不到** | 抓到了但样本被丢 | **被 `metric_relabel_configs` drop 了**（样本层问题） | 查 metric relabel 的 `keep` 规则 | **记住判据：`up` 有 = 样本层问题，`up` 无 = 目标层问题** |
| **指标基数爆炸，磁盘/内存暴涨** | `prometheus_tsdb_head_series` 阶跃 | **标签里混进了无界 ID**（`user_id` / `order_id` / 完整 URL / trace_id） | ① 立刻加 `metric_relabel_configs` 的 `labeldrop` 删掉高基数标签 ② 业务侧改埋点 ③ 用 `sample_limit` 做兜底 | **标签只用低基数枚举**；CI 里加"指标名与标签名白名单"检查；给 `sample_limit` 设上限 |
| **counter 重置后 rate 出现负数或尖峰** | `rate()` 结果异常 | 进程重启导致 counter 归零。**官方说明：`rate()` / `irate()` / `increase()` 会"automatically adjusted for"单调性中断**——**正常的 rate 不会负**，出现负数通常是**用法错了**（对 gauge 用了 `rate`） | ① 确认是不是对 gauge 用了 `rate`（`rate` 只适用于 counter）② 用 `resets(v[5m])` 确认确实在重启 ③ 重启导致的短暂尖峰在 `increase` 上更明显 | 用 `rate` 而不是裸差值；把 Pod 重启次数也纳入监控（`kube_pod_container_status_restarts_total`） |
| **relabel 写错，指标被全 drop** | `up=0` 或指标空 | `keep` 的正则写反 / `drop` 的正则太宽；**最常见的是正则两端锚定**（官方："The regex is anchored on both ends"）——写 `a.*` 匹配不到 `a:c` | 用 `.*` 包住：官方给的办法是 "To un-anchor the regex, use `.*<regex>.*`" | **改完 relabel 立刻验 `count(up)`**；正则一律用 `(.*)` 风格显式锚定 |
| **磁盘打满，Prometheus 崩了** | 磁盘 100% | ① 序列数暴涨 ② retention 太大 ③ compaction 期间新旧块并存（官方明说会**瞬时超过** `retention.size`） | ① 砍基数（**最有效**，官方原话："reducing the number of series is likely more effective"）② 调小 `--storage.tsdb.retention.time` ③ `--storage.tsdb.retention.size` 设成磁盘的 **80-85%**（官方建议值） | **磁盘预留 15-20% 缓冲**；把 `prometheus_tsdb_head_series` 和磁盘占用做成告警 |
| **Prometheus 重启丢数据** | 时间序列有空洞 | ① **WAL 只防崩溃不防长时间丢失**：官方原文——"Backups made without snapshots run the risk of losing data that was recorded since the last TSDB block was created, which typically happens **every two hours**" ② **没配 remote write** → 本地数据过期就真没了 | ① 配 **remote write** 到 Thanos/Mimir/对象存储做长期存储 ② 备份用**快照**（官方："Snapshots are recommended for backups"）③ 用 PVC 做本地持久化 | **监控数据必须双写**：本地短期 + 远端长期 |
| **PVC 用 NFS，Prometheus 起不来或数据损坏** | 启动失败 / 报损坏 | 官方 **CAUTION**："**Non-POSIX compliant filesystems are not supported**... **NFS filesystems (including AWS's EFS) are not supported**" | 换成云盘本地块存储（Local PV / EBS / 阿里云 SSD） | **PV 的 storageClass 选块存储，不选 NFS** |
| **`up` 全是 0，但业务明明活着** | `up=0` | **不是业务问题**——是 Prometheus 侧或网络侧：Prometheus 自己挂了 / DNS 挂了 / 防火墙 / ServiceMonitor 的 selector 写错 / Operator 的 `serviceMonitorSelector` 没匹配上 | ① 先查 Prometheus 自己的 Pod ② 查 Operator 的 `Prometheus` CR 的 `serviceMonitorSelector` ③ 用 `curl` 从 Prometheus 容器内测目标连通性 | **告警规则里加一条"Prometheus 自身不可用"的告警**（否则会陷入"没监控就没告警"的死循环） |
| **Operator 装了但一个 target 都没有** | target 为空 | ① `serviceMonitorSelector` 写错（不写=不选，写 `{}`=全选，**语义完全不同**）② ServiceMonitor 的 selector 与 Service 标签不匹配 ③ ServiceMonitor 所在 ns 不在 `serviceMonitorNamespaceSelector` 范围内 | 逐层核对：Prometheus CR → ServiceMonitor selector → ServiceMonitor 自己的 selector | **在 CRD 里给 ServiceMonitor 打统一标签**（如 `release: k8s`）便于选择器管理 |
| **抓取间隔调小了，磁盘和内存暴涨** | 序列数/写入量暴涨 | 抓取间隔减半 → **样本数翻倍**（序列数不变但样本数翻倍）；官方给的降盘手段之一就是 "increase the scrape interval" | 权衡告警精度与成本；优先砍序列数 | **改 `scrape_interval` 前后估算一下 `needed_disk_space`** |
| **`honor_labels` 配错，指标标签对不上** | 标签变成 `exported_instance` | `honor_labels: false`（默认）时，目标自带的冲突标签会被改名成 `exported_<label>` | 要保留目标标签就设 `honor_labels: true`；但要清楚它会影响 federation/Pushgateway 场景 | **明确约定"标签由谁负责"**：要么服务端给，要么目标给，别混 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `prometheus.io/docs/introduction/overview/` | 定位、pull 模型、exporter 概念、"when it does not fit" | ✅ 2026-10-24 |
| S2 | 官方 | `prometheus.io/docs/concepts/data_model/` | 时间序列定义、标签唯一标识、样本组成、命名正则、`__` 保留、冒号保留 | ✅ 2026-10-24 |
| S3 | 官方 | `prometheus.io/docs/concepts/metric_types/` | Counter / Gauge / Histogram / Summary 语义、classic 与 native histogram、`_total` 约定 | ✅ 2026-10-24 |
| S4 | 官方 | `prometheus.io/docs/practices/histograms/` | Histogram vs Summary 选型、聚合分位数的坏例子与好例子 | ✅ 2026-10-24 |
| S5 | 官方 | `prometheus.io/docs/prometheus/latest/getting_started/` | 最小配置、`static_configs`、`job` 标签、targets 页面 UP/DOWN、recording rule 基础 | ✅ 2026-10-24 |
| S6 | 官方 | `prometheus.io/docs/prometheus/latest/configuration/configuration/` | `global`/`scrape_config` 全字段与默认值、`relabel_action` 全清单、kubernetes_sd role、file_sd 默认值 | ✅ 2026-10-24 |
| S7 | 官方 | `prometheus.io/docs/prometheus/latest/storage/` | TSDB 磁盘布局、WAL、compaction、retention、容量公式、80-85% 建议、NFS 警告 | ✅ 2026-10-24 |
| S8 | 官方 | `prometheus.io/docs/prometheus/latest/querying/basics/` | lookback 5m、staleness 语义、慢查询与过载 | ✅ 2026-10-24 |
| S9 | 官方 | `prometheus.io/docs/prometheus/latest/querying/api/` | `/api/v1/query` 与 `/api/v1/query_range` 参数、resultType、错误码 | ✅ 2026-10-24 |
| S10 | 官方 | `prometheus.io/docs/guides/node-exporter/` | node_exporter 作为 exporter 的形态 | ✅ 2026-10-24 |
| S11 | 官方 | `prometheus.io/docs/practices/naming/` | 指标与标签命名规范 | ✅ 2026-10-24 |
| S12 | 官方 | `prometheus-operator.dev/docs/getting-started/introduction/` | Operator 定位、CRD 清单、"用 Label Selector 代替 relabel" | ✅ 2026-10-24 |
| S13 | 官方 | `prometheus-operator.dev/docs/api-reference/api/` | ServiceMonitor / PodMonitor 的 spec 字段与 `jobLabel` 默认行为 | ✅ 2026-10-24 |
| S14 | 官方 | `prometheus-operator.dev/docs/platform/` | Operator 平台页（监控 / Thanos / 分片 / 存储 / 高可用） | ✅ 2026-10-24 |
| S15 | 官方 | `thanos.io/tip/thanos/getting-started.md/` | Thanos 作为单机上限的解决方案 | ✅ 2026-10-24 |
| S16 | 官方 | `prometheus.io/docs/alerting/latest/alertmanager/` | 告警路由/抑制/静默（与 L2 告警规则对照） | ✅ 2026-10-24 |
| S17 | 官方 | `zabbix.com/documentation/current/` | Zabbix 主动轮询模型（横向对比用） | ✅ 2026-10-24 |
| S18 | 云厂商 | `help.aliyun.com/zh/prometheus/` | 阿里云可观测监控 Prometheus 版（托管形态） | ✅ 2026-10-24 |
| S19 | 云厂商 | `help.aliyun.com/zh/prometheus/user-guide/exporter-integration` | Exporter 接入实践 | ✅ 2026-10-24 |
| S20 | 云厂商 | `cloud.tencent.com/document/product/1416` | 腾讯云 Prometheus 托管服务（生产形态对照） | ✅ 2026-10-24 |
| S21 | 中文 | `developer.aliyun.com` · `cloud.tencent.com/developer` | 中文实践、卡片爆炸与告警风暴的真实案例 | ✅ 2026-10-24 |

> **完整来源矩阵见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。**
> **官方无中文版**：`prometheus.io/zh/docs/...` **404**（索引第三节实测坑 #2），Prometheus 中文来源只能靠社区与云厂商文档。
> **本篇新增的索引条目**：S2、S3、S4、S6、S7、S8、S9、S10、S11、S12、S13、S14、S15、S17、S18、S19、S20（已按索引第五节维护规则回写索引表）。
> **⚠️ 单源待补**：
> ① **K8s SD 的 relabel 实战模板**（`__meta_kubernetes_pod_annotation_prometheus_io_port` 拼接写法）——
> 官方 `configuration/configuration/` 只列出了 `__meta_*` 标签的**定义**，没有给出组合模板；
> 模板形态来自 kube-prometheus 生态的通行实践（社区来源 S21），**字段语义以官方 S6 为准**。
> ② **cAdvisor 与 kube-state-metrics 的指标名清单**——
> 两个项目的官网（`github.com/cadvisor/cadvisor`、`github.com/kubernetes/kube-state-metrics`）在本环境实测不可直连（索引第三节坑 #7），
> 本篇只引用了官方 `getting_started` 里出现的 `node_cpu_seconds_total` 作为实测样例，**其余指标名未逐一核对，⚠️ 待验证**。

---

## 8. 自测三问

1. **能说清输入输出吗？**
   **输入**：一批目标（由服务发现算出）+ 一份 `scrape_config` + 一份规则文件。
   **中间链路**：服务发现算出候选目标 → **`relabel_configs` 决定抓谁** → 逐个发**独立的 HTTP GET** → 解析响应体 → **`metric_relabel_configs` 决定留谁** → 写 TSDB（落 WAL）→ 按 `evaluation_interval` 评估规则。
   **输出**：一批可被 PromQL 查询的时间序列 + 一组告警。
   **关键一句**：**Prometheus 不生产数据——它只有"暴露出来的指标"这一种输入。**

2. **能故意改坏再倒推吗？** —— 五个实验（每个都写清改什么、看到什么）：
   ① **把 `relabel_configs` 里加一条 `action: drop` 且 `source_labels: [__meta_kubernetes_pod_phase]`** → `/targets` 里 Pod 目标消失，**连 `up` 都查不到** → 倒推出"目标层 vs 样本层"的判据。
   ② **把 `metric_relabel_configs` 里加一条 `action: drop` 且 `regex: ".*"`** → `up` 还是 1，但**所有指标都没了** → 倒推出"抓到了但没留"是样本层问题。
   ③ **把 `metrics_path` 改成 `/metric`（漏一个 s）** → target 变 **DOWN**，`up=0` → 倒推出路径写错的典型症状。
   ④ **把 `scrape_timeout` 改成 `30s` 而 `scrape_interval` 保持 `15s`** → Prometheus **启动即报错退出**（官方约束：timeout 不能大于 interval）→ 倒推出这条硬约束。
   ⑤ **给一个业务指标加上 `user_id` 标签** → 查 `count by (__name__) ({__name__!=""})` 看到该指标序列数暴涨 → 再加一条 `metric_relabel_configs` 的 `labeldrop` → 序列数回落、`up` 不变 → 倒推出"高基数是标签问题、labeldrop 是最后一道防线"。

3. **能画上下游吗？** ——
   画出这条链，并在每个环节标出"这一步坏了会看到什么"：

   ```
   服务发现(static/file_sd/k8s_sd)
      │ 坏 → target 列表为空 → 什么指标都没有
      ▼
   relabel_configs（抓谁）
      │ 坏 → 目标被 drop → up 也没有
      ▼
   HTTP GET /metrics（独立请求，每次一个）
      │ 坏 → up=0；响应体非 Prometheus 格式 → up=0
      ▼
   解析（# HELP/# TYPE/样本行）
      │ 坏 → up=0
      ▼
   metric_relabel_configs（留谁）
      │ 坏 → up=1 但指标缺失
      ▼
   TSDB 写入（chunks_head + WAL）
      │ 坏 → 磁盘打满 / 重启后丢未落块的数据
      ▼
   规则评估（recording / alerting）
      │ 坏 → 看板查不到预计算指标 / 告警不触发
      ▼
   Prometheus API → Grafana / Alertmanager / remote write
   ```

---

**下一篇**：[`L2-PromQL-实战`](L2-PromQL-实战.md)（**本篇讲清了"数据怎么进来"，下一篇讲"数据怎么被问出来"**） · **同系列**：[`L3-Grafana-仪表盘与告警`](L3-Grafana-仪表盘与告警.md) · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md)
