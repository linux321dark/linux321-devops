# L5 · Alertmanager 与 OpenTelemetry（告警治理 + 遥测标准）

> **本篇定位**：把三件事一次讲透——**告警管道怎么走**（Prometheus → Alertmanager → 通知渠道）、**告警怎么治理**（分组 / 抑制 / 静默 / 分级 / 可执行）、**遥测标准是什么**（OpenTelemetry：统一 SDK → Collector → 后端）。
> **前置**：`L1-Prometheus-数据模型与采集`（知道 TSDB 与告警规则）、`L2-PromQL-实战`（会写告警表达式）、`L3-Grafana-仪表盘与告警`（知道 Grafana Alerting 与 Contact point）。
> **学完能做什么**：说清 Prometheus 为什么必须配 Alertmanager；写出正确的 route 树并解释"第一个匹配生效"；用 `group_by` / `inhibit_rules` / `silence` 把一次告警风暴收敛成一条；分清告警的 labels 与 annotations 各自管什么；讲清 OpenTelemetry 的三支柱与 Collector 架构；解释 head sampling 与 tail sampling 的取舍。
> **在 21 天里**：D12（Prometheus + PromQL）、D13（Grafana）、D14（周整合 · 观测层）的告警与治理部分，以及"三支柱整合"这条学习地图主线的收口。
> **环境/版本基准**：Alertmanager 的 route / inhibit / 模板字段与默认值以 **官方 `prometheus.io/docs/alerting/latest/`** 为准；Grafana 侧以 **`grafana.com/docs/grafana/latest/alerting/`** 为准；OpenTelemetry 以 **`opentelemetry.io/docs/`** 为准（写作日期 2026-10-24，各路径实测 200）。**本篇未执行任何 alertmanager / prometheus / otelcol 命令**，示例为可复制的配置。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L5 Alertmanager 与 OpenTelemetry
│
├─ 1. 概念
│   ├─ 1.1 告警管道全景（两套并存）
│   ├─ 1.2 Alertmanager 的必要性：Prometheus 自己不做分组去重
│   ├─ 1.3 OpenTelemetry 是什么：厂商中立的遥测标准
│   ├─ 1.4 三支柱与信号类型
│   └─ 1.5 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 告警管道全景图（Prometheus → Alertmanager → 渠道）
│   ├─ 2.2 route 路由树与匹配顺序
│   ├─ 2.3 group_by / group_wait / group_interval / repeat_interval
│   ├─ 2.4 inhibit_rules：抑制
│   ├─ 2.5 silence：静默
│   ├─ 2.6 templates：模板化通知
│   ├─ 2.7 通知渠道
│   ├─ 2.8 告警的 labels vs annotations
│   ├─ 2.9 告警设计的四条纪律
│   ├─ 2.10 告警疲劳：告警系统失效的头号原因
│   ├─ 2.11 OpenTelemetry Collector 架构
│   ├─ 2.12 OTLP 协议
│   └─ 2.13 采样：head sampling vs tail sampling
│
├─ 3. 动手
│   ├─ 3.1 一份最小可用的 Alertmanager 配置
│   ├─ 3.2 造一次告警风暴，看分组与抑制的效果
│   ├─ 3.3 写一份可执行的告警规则（annotations 模板）
│   ├─ 3.4 Collector 的最小配置
│   ├─ 3.5 常用运维命令（按场景）
│   └─ 3.6 运维排障实战（场景串命令）
│
├─ 4. 逐字段表
│   ├─ 4.1 Alertmanager route / inhibit / silence 配置项表
│   ├─ 4.2 OTel Collector 组件表
│   └─ 4.3 Prometheus 告警规则字段表
│
├─ 5. 横向对比
│   ├─ 5.1 Prometheus Alerting vs Grafana Alerting
│   ├─ 5.2 Alertmanager vs 纯 Prometheus 告警
│   ├─ 5.3 OTel Collector vs 直接对接后端
│   └─ 5.4 head sampling vs tail sampling
│
├─ 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 → 怎么防）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 告警管道全景（两套并存）

**先看全景，再看细节。** 一条告警从"条件满足"到"人看到"，中间有 5 站：

```
                     ┌─────────────────────────────────────────────┐
 ① 规则求值           │ Prometheus (PrometheusRule)                 │
   （谁在算条件）      │  expr + for + labels + annotations          │
                     └──────────────┬──────────────────────────────┘
                                    │  HTTP POST /api/v2/alerts
                                    ▼
                     ┌─────────────────────────────────────────────┐
 ② 去重/分组/路由     │ Alertmanager                                │
                     │  route 树 → 分组 → 抑制 → 静默 → 模板        │
                     └──────────────┬──────────────────────────────┘
                                    │  按 receiver 分发
                                    ▼
                     ┌─────────────────────────────────────────────┐
 ③ 通知渠道           │ 钉钉 / 企业微信 / 邮件 / Webhook / PagerDuty │
                     └──────────────┬──────────────────────────────┘
                                    ▼
                     ┌─────────────────────────────────────────────┐
 ④ 人看到并响应       │ 值班群 / 电话 / 工单                        │
                     └─────────────────────────────────────────────┘
```

**Grafana Alerting 是第二条平行管道**：

```
 ① 规则求值    Grafana (Grafana-managed rule)  ←── 求值靠 Grafana 进程
                    ▼
 ② 通知管理    Grafana 内置 Alertmanager（notification policy + contact point + silence + inhibition）
                    ▼
 ③ 通知渠道    Contact point（钉钉/企微/Webhook/邮件/Slack/PagerDuty…）
```

> **官方原文（Grafana 的架构定位）**：**"Grafana Alerting is based on the Prometheus Alerting model, whose architecture decouples rule evaluation from notification handling."**
> **官方原文（Prometheus 侧的定位）**：**"Prometheus's alerting rules are good at figuring out what is broken *right now*, but they are not a fully-fledged notification solution. Another layer is needed to add summarization, notification rate limiting, silencing and alert dependencies on top of the simple alert definitions."**

**两套并存的统一治理原则（本篇的核心主张）**：

| 治理项 | 具体做法 |
|---|---|
| **明确分工边界** | **指标告警 → Prometheus Rule + Alertmanager；业务看板级/跨数据源告警 → Grafana Alerting**。写进文档，不靠默契 |
| **避免重复计算** | Grafana 告警规则**查 Prometheus 里已算好的 recording rule**，不重复跑复杂 PromQL |
| **统一通知出口** | 两套的通知**收到同一个值班群**，但**必须用 `source=prometheus` / `source=grafana` 标签区分**，否则收到重复告警时不知道该看哪 |
| **统一标签口径** | 两套都用同一套 `severity`（critical / warning / info）分级，路由才能共用 |
| **统一告警内容规范** | 都要有 `summary` / `description` / `runbook_url` |
| **只有一个"值班入口"** | 值班只盯一个渠道。**如果有两个群，值班就会两个都不盯** |

### 1.2 Alertmanager 的必要性

**官方原文（grouping 章节）**：

> **"Dozens or hundreds of instances of a service are running in your cluster when a network partition occurs. Half of your service instances can no longer reach the database. Alerting rules in Prometheus were configured to send an alert for each service instance if it cannot communicate with the database. As a result hundreds of alerts are sent to Alertmanager."**
>
> **"As a user, one only wants to get a single page while still being able to see exactly which service instances were affected."**

**这段话把整件事讲透了**：

| | Prometheus 自己 | Alertmanager |
|---|---|---|
| **收到什么** | **每个 alert 实例一条** | **一批 alert 实例** |
| **1 台机器 100 个错误会怎样** | **发 100 条告警** | **发 1 条通知**（含 100 个实例的信息） |
| **谁做去重** | ❌ 不做 | ✅ 按标签集去重 |
| **谁做分组** | ❌ 不做 | ✅ `group_by` |
| **谁做抑制** | ❌ 不做 | ✅ `inhibit_rules` |
| **谁做静默** | ❌ 不做 | ✅ `silence` |
| **谁做路由** | ❌ 只有全局 `alertmanagers` 列表 | ✅ **route 树** |

> **一句话**：**Prometheus 负责"判断出事了"，Alertmanager 负责"决定怎么告诉你"。** 没有 Alertmanager，Prometheus 的告警能力只是个"通知发送器"，不具备生产可用性。

**Alertmanager 的五个概念（官方原文的定义）**：

| 概念 | 官方定义 | 一句话 |
|---|---|---|
| **Grouping** | **"Grouping categorizes alerts of similar nature into a single notification."** | 把同类告警合成一条通知 |
| **Inhibition** | **"suppressing notifications for certain alerts if certain other alerts are already firing"** | 上游出事 → 压住下游噪声 |
| **Silences** | **"a straightforward way to simply mute alerts for a given time"** | 定时屏蔽 |
| **Client behavior** | 对客户端有特殊要求 | 高级用法才需要看 |
| **High Availability** | Alertmanager 支持集群 | 见 2.2 与 5.2 |

### 1.3 OpenTelemetry 是什么

**官方原文（concepts 页）**：**"The purpose of OpenTelemetry is to collect, process, and export signals."**

**一句话定义**：

> **OpenTelemetry 是一套厂商中立的遥测标准与实现：统一 SDK（应用埋点）→ Collector（接收/处理/导出）→ 后端（存储与查询）。**

**它解决的具体问题（这才是"为什么需要它"）**：

**没有 OTel 的时候**：

```
应用 A ──(埋 Prometheus client_exporter)──► Prometheus
应用 B ──(埋 Datadog agent)───────────► Datadog
应用 C ──(埋 Jaeger SDK)──────────────► Jaeger
应用 D ──(埋 SkyWalking SDK)──────────► SkyWalking
应用 E ──(埋 云厂商 SDK)──────────────► 云监控
```

**每换一个后端就要改一遍埋点、重新发版、重新验证。** 改了 5 个后端就是 5 倍改造量、5 套版本、5 套问题排查。

**有 OTel 的时候**：

```
应用 A/B/C/D/E ──(统一 OTel SDK)──► OTel Collector ──┬──► Prometheus
                                                    ├──► Tempo / Jaeger
                                                    ├──► Loki
                                                    └──► 云厂商 / SaaS 后端
```

**换后端 = 改 Collector 配置，不改一行应用代码。**

**官方对 Collector 存在理由的说明（architecture 页原文）**：

> **"Previously, the library did all the recording, collecting, sampling, and aggregation of traces, metrics, and logs... This pattern has several drawbacks"**——列举了四条：
> ① **每个语言的 exporter 都要重新实现一遍**（原生语言维护成本）；
> ② **某些语言（如 Ruby、PHP）难以在进程内做统计聚合**；
> ③ **开启导出要改库并重新发二进制**——"**especially difficult when an incident has occurred, and users want to use OpenTelemetry to investigate the issue right away**"（出事了想临时开 tracing，却要改代码重新发版，最需要的时候最不能用）；
> ④ **应用用户要自己负责配置和初始化 exporter**——"**error-prone**（容易配错凭据或资源标识）"，而且用户往往**不愿意"污染"自己的代码**。

**Collector 的两种部署形态（官方原文）**：

| 形态 | 官方描述 | 作用 |
|---|---|---|
| **Agent** | "runs as a daemon in the VM/container and can be deployed independent of the library" | **每个节点/容器一个**，本地收 SDK 数据，转发到 Gateway |
| **Gateway** | "can run as a gateway instance and receive spans and metrics exported by one or more agents or libraries" | **集群级一个**，汇聚、转换、分发到各后端 |

> **标准形态是 Agent + Gateway 两层**：Agent 负责"贴身收集 + 本地处理（脱敏、采样）"，Gateway 负责"汇聚 + 路由 + 写多个后端"。**这样即使 Gateway 短暂挂了，Agent 还能缓冲**。

### 1.4 三支柱与信号类型

**官方原文（signals 页）**：

> **"The purpose of OpenTelemetry is to collect, process, and export signals. Signals are system outputs that describe the underlying activity of the operating systems and applications running on a platform."**

**官方列出的信号**：

| 信号 | 官方定义 | 回答什么 |
|---|---|---|
| **Traces** | **"The path of a request through your application."** | **哪一环** |
| **Metrics** | **"A measurement captured at runtime."** | **有没有问题**（趋势、比例、分位） |
| **Logs** | **"A recording of an event."** | **为什么** |
| **Baggage** | **"Contextual information that is passed between signals."** | 跨信号传递的上下文 |
| **Profiles** | **"A recording of resource usage at the code level."** | 代码级资源占用 |

**（Events 官方标注为"开发中/提案阶段"）**

**OTel 的核心价值主张**：**三支柱用同一套 SDK、同一套 Collector、同一套 OTLP 协议。**

| 统一了什么 | 具体表现 |
|---|---|
| **同一套 SDK** | 同一个进程里同时开 trace、metric、log，**配置一套 endpoint** |
| **同一个 Collector** | **同一个 pipeline 家族、同一个配置模型**（receiver/processor/exporter） |
| **同一套协议** | OTLP，**gRPC 与 HTTP 双传输** |
| **同一套资源标识** | `resource attributes`（service.name、k8s.*）在三种信号里**语义一致**——**这是"指标→日志→链路"能串起来的基础** |

> **关键认知**：**"三支柱统一"不是指三者可以互相替代**，而是**指它们共用一套接入方式**。**指标回答"有没有问题"，日志回答"为什么"，链路回答"哪一环"——三者缺一不可，但接入方式应该统一。**

**为什么统一接入方式很重要（一个具体例子）**：

> 一次接口变慢：
> - 指标告警说"错误率从 0.1% 涨到 3%"（**有没有问题**）
> - 链路显示"80% 的耗时在 payment-service 的 DB 调用"（**哪一环**）
> - 日志显示"payment DB connection pool exhausted"（**为什么**）
>
> **如果三者是用三套不同的 SDK 埋的，trace_id 在日志里根本没有**——你没法把"这次慢"和"这条日志"对上。**统一的 resource attributes + trace_id 贯穿，是三支柱能联动的前提。**

### 1.5 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Alert | 告警 | 一个**告警实例**（label 集 + 状态） |
| Alert rule | 告警规则 | 定义"什么条件下算告警" |
| Firing / Pending / Inactive | 触发中 / 待触发 / 未触发 | 告警实例的三种状态 |
| Receiver | 接收器 | 通知的实际集成（钉钉、邮件…） |
| Route | 路由 | 决定告警去哪个 receiver 的规则树 |
| group_by | 分组标签 | 哪些告警算"同一组" |
| group_wait | 首次等待 | 新组成立后等多久发第一条 |
| group_interval | 组内间隔 | 同一组后续通知的间隔 |
| repeat_interval | 重复间隔 | 一直没恢复时多久重发一次 |
| Inhibition rule | 抑制规则 | 上游 firing 时压住下游 |
| Silence | 静默 | 定时屏蔽一批告警的通知 |
| Mute timing | 静默时段 | 周期性静默（如每天凌晨） |
| Template | 模板 | 通知消息的渲染模板（Go template） |
| OTLP | — | OpenTelemetry Protocol，OTel 的标准传输协议 |
| Receiver (OTel) | 接收器 | Collector 的入口组件 |
| Processor | 处理器 | Collector 的中间组件：转换 / 脱敏 / 采样 |
| Exporter | 导出器 | Collector 的出口组件 |
| Connector | 连接器 | 连接两条 pipeline 的桥（既是 exporter 又是 receiver） |
| Extension | 扩展 | 不接触遥测数据的辅助能力（健康检查、pprof、zpages） |
| Head sampling | 头部采样 | **早期**做采样决策（SDK 侧） |
| Tail sampling | 尾部采样 | **看完整个 trace** 再决定（Collector 侧） |
| TraceIdRatioBased | — | 按 trace ID 哈希比例采样，**保证同 trace 的 span 决策一致** |

---

## 2. 机制

### 2.1 告警管道全景图（Prometheus → Alertmanager → 渠道）

**Prometheus 侧的规则（官方原文）**：

```yaml
groups:
- name: example
  labels:
    team: myteam
  rules:
  - alert: HighRequestLatency
    expr: job:request_latency_seconds:mean5m{job="myjob"} > 0.5
    for: 10m
    keep_firing_for: 5m
    labels:
      severity: page
    annotations:
      summary: High request latency
```

**三个状态（官方原文）**：

| 状态 | 官方定义 |
|---|---|
| **Inactive** | 表达式不满足 |
| **Pending** | **"Elements that are active, but not firing yet, are in the pending state."** —— 条件已满足但 `for` 还没到 |
| **Firing** | `for` 时长已满足，正式触发 |

**官方原文解释 `for`**：

> **"The optional `for` clause causes Prometheus to wait for a certain duration between first encountering a new expression output vector element and counting an alert as firing for this element."**

**官方原文解释 `keep_firing_for`**：

> **"tells Prometheus to keep this alert firing for the specified duration after the firing condition was last met. This can be used to prevent situations such as **flapping alerts**, **false resolutions due to lack of data**"**

> **`keep_firing_for` 解决的正是"抖动型告警"**：条件刚满足又掉下去，`for` 会让它永远在 pending（**看起来像没触发**）；`keep_firing_for` 让它触发后再保持一段时间，避免"刚发完就恢复"的噪声通知。

**合成时间序列（官方原文）**：

> **"For pending and firing alerts, Prometheus also stores synthetic time series of the form `ALERTS{alertname="<alert name>", alertstate="<pending or firing>", <additional alert labels>}`."**

**这张合成序列是排障利器**（详见第 6 节）：**`ALERTS` 告诉你"现在到底有哪些告警在 pending/firing"**，`up` 告诉你"哪些 target 挂了"——**两条查询就能判断"是告警规则写错了还是真的挂了"**。

### 2.2 route 路由树与匹配顺序

**官方原文（configuration 页）**：

> **"Every alert enters the routing tree at the configured top-level route, which must match all alerts (i.e. not have any configured matchers)."**
>
> **"Its optional configuration parameters are inherited from its parent node if not set."**

**匹配规则（这是本节最关键的一句）**：

> **第一个匹配的子路由生效；`continue: true` 才会继续匹配兄弟节点。**

**官方默认值（原文照录）**：

```yaml
[ group_by: '[' <labelname>, ... ']' ]

[ continue: <boolean> | default = false ]

[ group_wait: <duration> | default = 30s ]

[ group_interval: <duration> | default = 5m ]

[ repeat_interval: <duration> | default = 4h ]

[ receiver: <string> ]

[ matchers:
  [ - <matcher> ... ]

mute_time_intervals:
  [ - <string> ...]
```

**两个必须记住的规则**：

| 规则 | 后果 |
|---|---|
| **根路由不能配 `matchers`** | 配了 → **有些告警匹配不到任何路由**，**被静默丢弃** |
| **子路由未设置的参数从父路由继承** | 子路由只写 `matchers` 就够了，其余全继承——**但这也意味着"继承来的 group_wait 可能是 30s"而你不知道** |

**`group_by: ['...']` 的特殊含义（官方原文）**：

> **"To aggregate by all possible labels use the special value '...' as the sole label name"**

`group_by: ['...']` = **按所有标签分组 = 不聚合，每条告警各自成组**（等于关掉分组）。

**官方给的 grouping 例子（原文照录）**：

> **"Thus one can configure Alertmanager to group alerts by their cluster and alertname so it sends a single compact notification."**

**route 树的标准形态**：

```yaml
route:
  receiver: default          # 兜底接收器（必须有）
  group_by: ['alertname', 'cluster']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    # ── 第一优先级：critical ──
    - matchers:
        - severity = "critical"
      receiver: oncall-critical
      group_wait: 10s        # critical 不等，立刻发
      repeat_interval: 30m
      continue: false

    # ── 第二优先级：warning ──
    - matchers:
        - severity = "warning"
      receiver: team-warning
      repeat_interval: 2h

    # ── 第三优先级：按 team 分流 ──
    - matchers:
        - team =~ "payments|settlement"
      receiver: team-payments
```

**匹配顺序的三条推论**：

| 推论 | 解释 |
|---|---|
| **路由是"先声明先匹配"，不是"最具体优先"** | **把 `team=payments` 写在 `severity=critical` 前面，则 payments 的 critical 告警会先命中 payments 路由，进不了 critical 通道**。**顺序即优先级** |
| **`continue: true` 会让一条告警进多个 receiver** | 代价是**通知量翻倍**——用之前先想清楚 |
| **根路由的 `receiver` 是兜底** | 任何没被子路由匹配到的告警都走它。**兜底 receiver 必须真的有人看** |

**官方原文关于路由树的一句总结**：

> **"Grouping of alerts, timing for the grouped notifications, and the receivers of those notifications are configured by a routing tree in the configuration file."**

### 2.3 group_by / group_wait / group_interval / repeat_interval

**这是本篇最该背下来的一组参数**，因为它们决定了"你半夜被叫醒几次"。

| 参数 | 官方默认值 | 含义（官方原文口径） | 取值建议与理由 |
|---|---|---|---|
| **`group_by`** | 无（默认按全部标签分组） | **按哪些标签把告警归为"同一组"** | **必须显式写**。推荐 `['alertname', 'cluster']` 或 `['alertname', 'service']`。**不要包含 `instance` / `pod` / `node`**——**包含了就等于不分组**（每个实例自成一组） |
| **`group_wait`** | **30s** | **新告警组成立后，等多久才发第一条通知** | 目的是**等更多同类告警进来再一起发**。<br>· **critical：10s～30s**（要快）<br>· **warning：1m～5m**（可以等，让抖动自己消失） |
| **`group_interval`** | **5m** | **同一组已发出第一条之后，后续通知之间的最小间隔** | 防止**同一组反复发**。<br>· **默认 5m 偏密**，production 常用 **10m～30m** |
| **`repeat_interval`** | **4h** | **一直没恢复时，多久重发一次** | 决定"问题持续 4 小时后你还收到几次"。<br>· **critical：30m～1h**（别让值班以为已经处理完了）<br>· **warning：4h～12h** |

**这四个参数的关系（一句话记住）**：

```
告警第一次满足条件
      │
      ├─ 等待 group_wait ──► 发第一条（含本组所有告警）
      │
      ├─ 再等 group_interval ──► 如果组内有新告警加入，发第二条
      │
      └─ 每隔 repeat_interval ──► 如果一直没恢复，重发（提醒还没解决）
```

**四个参数配错的后果（逐个）**：

| 参数配错 | 后果 |
|---|---|
| **`group_by` 里带了 `instance`** | **等于没分组**——100 个实例还是 100 条通知 |
| **`group_wait` 太短** | **抖动告警立刻发出去**，然后反复发——**告警疲劳的主要来源** |
| **`group_wait` 太长** | **真故障被延迟发现**（critical 场景下 30s 就是 30s 的事故） |
| **`group_interval` 太短** | **同一组反复刷屏** |
| **`repeat_interval` 太短** | **值班以为还在处理，实际是同一件事第 N 次** |
| **`repeat_interval` 太长** | **问题已经持续很久却没人再被提醒**——**"我以为已经处理了"** |

> **一条经验**：**`group_wait` 是"要不要发"的闸门，`group_interval` 是"发多密"的闸门，`repeat_interval` 是"多久再提一次"的闸门。** 三个闸门一起调，才能既不漏也不吵。

### 2.4 inhibit_rules（抑制）

**官方原文（Alertmanager 页）**：

> **"An alert is firing that informs that an entire cluster is not reachable. Alertmanager can be configured to mute all other alerts concerning this cluster if that particular alert is firing. This prevents notifications for hundreds or thousands of firing alerts that are unrelated to the actual issue."**

**官方原文（configuration 页的结构）**：

```yaml
name: <string>

target_matchers:
  [ - <matcher> ... ]

source_matchers:
  [ - <matcher> ... ]

[ equal: '[' <labelname>, ... ']' ]
```

**三个字段的含义**：

| 字段 | 官方含义 | 关键点 |
|---|---|---|
| **`source_matchers`** | **必须存在至少一个满足这些匹配器的源告警，抑制规则才生效** | **源（source）= 触发抑制的那个告警**，通常是"根因" |
| **`target_matchers`** | **满足这些匹配器的目标告警将被抑制** | **目标（target）= 被压住的那个**，通常是"衍生告警" |
| **`equal`** | **源告警和目标告警必须具有相同值的标签名称列表** | **不写 = 跨范围全压**（很危险） |

**官方原文的重要语义**：

> **"Both target and source alerts must have the same label values for the label names in the `equal` list."**
>
> **"Semantically, a missing label and a label with an empty value are the same thing."**

**最后这句的含义**：**如果 `equal` 里列的标签在源和目标上都"不存在"，抑制依然生效。** 这是个隐蔽的坑——**`equal: [cluster]` 但你的告警压根没打 cluster 标签 → 抑制照样生效，压掉了你不想压的东西**。

**标准抑制规则**：

```yaml
inhibit_rules:
  # ── 规则 1：集群级故障抑制该集群内的实例级告警 ──
  - source_matchers:
      - alertname = "ClusterDown"
      - severity = "critical"
    target_matchers:
      - severity =~ "warning|info"
    equal: ['cluster']

  # ── 规则 2：节点 down 抑制该节点上所有服务的告警 ──
  - source_matchers:
      - alertname = "NodeDown"
    target_matchers:
      - severity =~ "warning|critical"
    equal: ['cluster', 'node']

  # ── 规则 3：数据库故障抑制依赖它的服务告警 ──
  - source_matchers:
      - alertname = "DatabaseDown"
    target_matchers:
      - alertname =~ "ServiceUnavailable|HighErrorRate"
    equal: ['cluster', 'service']
```

**`equal` 的三种写法对比**：

| 写法 | 效果 | 风险 |
|---|---|---|
| `equal: ['cluster']` | **只在同一集群内抑制** | ✅ **推荐**：范围明确 |
| `equal: []`（不写） | **任何源告警 firing 就压住所有匹配的目标告警** | ⚠️ **危险**：一个集群的故障压掉全网告警 |
| `equal: ['cluster', 'service']` | 同集群**同服务**才抑制 | ✅ 精确 |

**Grafana 侧的对应概念**（官方 inhibition-rules 页）：

- 官方给的字段结构与 Alertmanager 一致：`name` / `source_matchers` / `target_matchers` / `equal`。
- **matcher 格式**：`label` / `type`（`=` `!=` `=~` `!~`）/ `value`。
- 官方示例（JSON 原文）：

```json
{
  "name": "critical-inhibits-warnings",
  "source_matchers": [{ "label": "severity", "type": "=", "value": "critical" }],
  "target_matchers": [{ "label": "severity", "type": "=", "value": "warning" }],
  "equal": ["cluster", "namespace"]
}
```

- **官方明确的限制（原文照录）**：
  - **"Available in Grafana 13 or higher."**
  - **"Inhibition rules are assigned to a specific Alertmanager and only suppress notifications for alerts managed by that Alertmanager."**
  - **"Inhibition rules are intended for compatibility with configurations imported from Prometheus Alertmanager or Mimir. They have no dedicated management UI in Grafana by design."**（**没有专门的 UI，只能用 API 管理**）
  - **"If not carefully configured, inhibition rules can silently suppress alerts and make issues harder to detect. Consider silences or mute timings for most suppression use cases."**（**官方自己都说：大多数抑制场景用 silence / mute timing 更合适**）
  - **API 是 beta**：`notifications.alerting.grafana.app` / `v1beta1` / `inhibitionrules`。

> **抑制 vs 静默（官方对照表）**：

| | Inhibition rule | Silence |
|---|---|---|
| **触发条件** | **匹配的源告警 firing 时** | **配置的时间窗口内** |
| **持续时间** | **只要源告警还在 firing** | **固定的起止时间** |
| **配置方式** | **持久化配置**（进 Git） | **每次手工创建** |

### 2.5 silence（静默）

**官方原文（Alertmanager 页）**：

> **"Silences are a straightforward way to simply mute alerts for a given time. A silence is configured based on matchers, just like the routing tree. Incoming alerts are checked whether they match all the equality or regular expression matchers of an active silence. If they do, no notifications will be sent out for that alert."**
>
> **"Silences are configured in the web interface of the Alertmanager."**

**关键语义**：**静默只挡"通知"，不挡"告警产生"。**（Grafana 侧官方原文同样明确："Silences stop notifications from being created for a specified time window but **do not interrupt alert evaluation**."）

**静默的字段**：

| 字段 | 含义 | 写错的后果 |
|---|---|---|
| **`matchers`** | 匹配哪些告警（和 route 树同一套匹配器语法） | 匹配太宽 → **把不想屏蔽的也屏蔽了** |
| **`startsAt`** | 生效时间 | — |
| **`endsAt`** | **失效时间** | **不设 → 告警永久静默**（本篇踩坑表必列） |
| **`createdBy`** | 创建者 | 事后没人知道是谁建的 |
| **`comment`** | 原因说明 | **事后没人知道为什么屏蔽** |

**Grafana 侧的官方事实（补充）**：

| 项 | 官方内容 |
|---|---|
| **过期静默保留** | **"Expired silences are automatically deleted after 5 days. This retention period is not configurable."** |
| **清理频率** | **每 15 分钟自动清理一次** |
| **数量上限** | `alertmanager_max_silences_count`（**默认 0 = 不限**，过期静默也计数） |
| **大小上限** | `alertmanager_max_silence_size_bytes`（**默认 0 = 不限**） |
| **能否手动删除** | **不能**。官方原文：**"Silences cannot be permanently deleted manually."** 只能 **Unsilence**（**立即结束**，这是唯一能提前结束的方式） |
| **规则级静默** | 用 `__alert_rule_uid__=<规则 UID>` 这个 label matcher 可只静默某条规则 |
| **静默链接** | 模板里可以用 `.Alert.SilenceURL` 直接给出一个"点此静默"的链接 |

> **"不能手动删除"这条官方原文很重要**：**它意味着一个忘记设 `endsAt` 的静默会一直生效**（直到过期被自动清理，但那是 5 天后的事）。**这是"告警只发一次就不发了"的一个经典根因。**

**Mute timing vs Silence（官方对照）**：

| | Mute timing | Silence |
|---|---|---|
| **建立方式** | **预先创建，然后挂到 notification policy 上** | **按 label 匹配** |
| **周期** | **可以用时间区间定义，周期性重复** | **固定起止时间** |

> **场景对应**：**"每天凌晨 2-5 点不发告警"→ mute timing（周期性）**；**"现在正在扩容，这 2 小时别吵我"→ silence（一次性）**。

### 2.6 templates：模板化通知

**官方原文（notifications 页）**：

> **"The notifications sent to receivers are constructed via templates. The Alertmanager comes with default templates, but they can also be customized."**
>
> **"The Alertmanager's notification templates are based on the Go templating system. Note that some fields are evaluated as text, and others as HTML which will affect escaping."**

**模板可用的数据结构（官方原文的表）**：

| 字段 | 类型 | 官方说明 |
|---|---|---|
| **`Receiver`** | string | 接收器名 |
| **`Status`** | string | **"Defined as firing if at least one alert is firing, otherwise resolved."** |
| **`Alerts`** | list | 本组的所有告警对象 |
| **`GroupLabels`** | KV | **这组告警按什么分组的** |
| **`CommonLabels`** | KV | **所有告警共有的 labels** |
| **`CommonAnnotations`** | KV | **所有告警共有的 annotations**（**用于较长的说明信息**） |
| **`RouteLabels`** | KV | 路由上挂的 labels，通常用 `routeLabels` 函数取 |
| **`ExternalURL`** | string | 回到 Alertmanager 的链接 |

**单个告警对象（Alert）可用的字段**：

| 字段 | 官方说明 |
|---|---|
| `Status` | firing 还是 resolved |
| `Labels` | 该告警的标签集 |
| `Annotations` | 该告警的注解集 |
| `StartsAt` | 开始 firing 的时间 |
| `EndsAt` | 结束时间（未恢复时用超时值） |
| `GeneratorURL` | **指向引发该告警的实体的回链**（Prometheus 会填 `http://prometheus:9090/graph?...`） |
| `Fingerprint` | 告警的唯一指纹 |

**`Alerts` 上还有两个过滤函数（官方原文）**：

- **`Alerts.Firing`** → 本组中正在 firing 的告警列表
- **`Alerts.Resolved`** → 本组中已恢复的告警列表

**KV 类型上的方法（官方原文）**：

| 方法 | 返回 |
|---|---|
| `SortedPairs` | 排序后的 key/value 对列表 |
| `Remove([]string)` | 去掉指定 key 后的副本 |
| `Names` | 所有 key |
| `Values` | 所有 value |

**常用模板函数（官方原文）**：

| 函数 | 用途 |
|---|---|
| `title` | 首字母大写 |
| `toUpper` / `toLower` | 大小写转换 |
| `trimSpace` | 去空格 |
| `humanizeDuration` | **把秒数变成"5m"这种人类可读格式** |
| `match` | 正则匹配（返回布尔） |
| `reReplaceAll` | 正则替换 |
| `safeHtml` / `safeUrl` | **标记为 HTML / URL，不做转义** |
| `tz` | **按指定时区格式化时间** |
| `urlUnescape` | URL 解码 |
| `toJson` | 转 JSON |
| `dict` / `list` / `join` | 构造数据结构 |
| `now` | 当前时间 |
| `since` | 距今多久 |

**标准告警模板（生产可直接用）**：

```
{{ define "alerts.default" }}
{{- if gt (len .Alerts) 0 -}}
[{{ .Status | toUpper }}{{ if eq .Status "firing" }}:{{ .Alerts | len }}{{ end }}] {{ .CommonLabels.alertname }}
{{- range $i, $a := .Alerts -}}
{{ if $i }}-------------------------------
{{ end }}
实例: {{ $a.Labels.instance | default "N/A" }}
摘要: {{ $a.Annotations.summary }}
详情: {{ $a.Annotations.description }}
处置手册: {{ $a.Annotations.runbook_url }}
开始于: {{ $a.StartsAt.Format "2006-01-02 15:04:05" }}
{{- end -}}
{{- end -}}
{{ end }}
```

**三个必须放进模板的字段**：

| 字段 | 为什么 |
|---|---|
| **`summary`** | 一句话说清是什么 |
| **`description`** | 影响谁 + 怎么办 |
| **`runbook_url`** | **点进去就是处置步骤**——**这是"告警可执行"的技术落点** |

**Grafana 侧的模板**（官方 template-notifications 页）：

- Grafana 提供 `default.title` 和 `default.message` 两个默认模板。
- 官方示例（原文照录）：

```go
{{ define "custom.alerts" -}}
{{ len .Alerts }} alert(s)
{{ range .Alerts -}}
  {{ template "alert.summary_and_description" . -}}
{{ end -}}
{{ end -}}
{{ define "alert.summary_and_description" }}
  Summary: {{.Annotations.summary}}
  Status: {{ .Status }}
  Description: {{.Annotations.description}}
{{ end -}}
```

- **官方明确的能力边界（原文照录）**：
  - **不能**加 HTML/CSS 改邮件外观；**不能**改 Slack/Teams 的消息结构（自定义卡片、adaptive card）；**不能**改传给 webhook 的数据结构（加新 JSON 字段、发改 XML）；**不能**改 webhook 的 HTTP 头（除配置里已定义的）；**不能**调图片的数量/大小/位置。
  - 官方建议：**不要在模板里硬加告警实例的额外信息**，而应该**用 annotations 或 labels 承载**，这样信息在 Grafana 的告警状态和历史里也可见。

> **这条建议很实在**：**信息写在 annotations 里 → 通知里能看到、Grafana 告警历史里也能看到、后续还能被查询。** 写在模板里 → 只有通知里有。

### 2.7 通知渠道

**Grafana 侧 contact point 集成清单**（官方 `manage-contact-points/` 页原文，完整）：

Alertmanager · AWS SNS · Cisco Webex Teams · **DingDing（钉钉）** · Discord · **Email** · Google Chat · Grafana IRM · Jira · Kafka REST Proxy · Line · Microsoft Teams · MQTT · Opsgenie · PagerDuty · Pushover · Sensu Go · Slack · Telegram · Threema Gateway · VictorOps · **Webhook** · **WeCom（企业微信）**

**Prometheus Alertmanager 侧的 receiver**（官方 configuration 页）：**官方明确说明有一部分集成 Grafana 有、Alertmanager 没有**，并指向 Alertmanager 的 receiver settings 章节。**国内场景 Alertmanager 常用的四个**：

| 渠道 | Alertmanager 怎么配 | 限制 |
|---|---|---|
| **钉钉（DingTalk）** | `webhook_configs` 指向 `https://oapi.dingtalk.com/robot/send?access_token=<token>` | **只发文本**（见下） |
| **企业微信（WeCom）** | `webhook_configs` 指向 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<key>` | 同上 |
| **邮件** | `email_configs`（需要 SMTP 配置） | **最正式、最不可抵赖**，但慢 |
| **通用 Webhook** | `webhook_configs` + `url` | **可带 payload 模板**（`send_resolved`、`http_config`） |

**钉钉/企业微信的硬限制（必须讲清）**：

> **钉钉机器人自定义 Webhook 的 `msgtype` 只有 `text` / `markdown` / `link` / `actionCard` 等有限几种**，且 `text` 有长度上限、markdown 有条数与标题限制。
> **Grafana 内置的 DingDing 集成只发文本消息**（标题 + 内容塞进一个 text 字段）。
> **结果：多条告警拼在一起会被截断。**

**复杂告警的正确做法：Webhook 中转**：

```
Alertmanager / Grafana
      │  webhook（完整 JSON payload：labels、annotations、status、alerts 全量）
      ▼
自建中转服务（几十行代码）
      │  解析 JSON → 取 CommonLabels / Annotations → 拼 markdown / actionCard
      ▼
钉钉 / 企业微信机器人
```

> **中转服务唯一的职责是"换格式"**——**不重试、不改路由、不改内容语义**。**只要 alerts 数组非空就发一条消息，把前 N 条告警列进去，其余折叠成"还有 X 条"**——**这一步就能解决截断问题**。

**分级通知的渠道搭配（生产常用形态）**：

| severity | 渠道 | 理由 |
|---|---|---|
| **critical** | **电话（PagerDuty / Opsgenie）+ 钉钉群** | 需要**把人叫醒**，不能只发消息 |
| **warning** | **钉钉群 / 企业微信群** | 工作时间处理即可 |
| **info** | **日报 / 邮件汇总** | 不需要即时打扰 |

> **关键认知**：**critical 告警不能只靠 IM 消息**。IM 消息可以被静音、被刷掉、被折叠。**"重要的事必须走能叫人起床的渠道"**——这是 Alertmanager 存在的核心理由之一。

### 2.8 告警的 labels vs annotations

**这是本篇要求"必须讲清"的一条区分，也是新手最常搞错的地方。**

**官方原文（Prometheus alerting_rules 页）**：

> **"The `labels` clause allows specifying a set of additional labels to be attached to the alert. Any existing conflicting labels will be overwritten. The label values can be templated."**
>
> **"The `annotations` clause specifies a set of **informational labels** that can be used to store **longer additional information** such as alert descriptions or runbook links. The annotation values can be templated."**

**一字之差，职责完全不同**：

| | **labels** | **annotations** |
|---|---|---|
| **本质** | **机器读的分类维度** | **人读的信息** |
| **用途 1** | **路由**：Alertmanager route 树按 label 匹配 | **不参与路由** |
| **用途 2** | **分组**：`group_by` 按 label 归组 | **不参与分组** |
| **用途 3** | **抑制**：`source_matchers` / `target_matchers` / `equal` 都用 label | **不参与抑制** |
| **用途 4** | **UI 展示**：告警列表按 label 筛选 | **通知正文内容** |
| **长度** | **短**（枚举值） | **长**（整段文字） |
| **能否模板化** | ✅ `{{ $labels.xxx }}` / `{{ $value }}` | ✅ 同上 |
| **示例** | `severity: page`、`team: payments`、`service: order-api` | `summary: 订单服务错误率 3.2%`、`description: …`、`runbook_url: https://…` |

**搞反的后果（这张表背下来）**：

| 搞错 | 后果 |
|---|---|
| **把 `summary` 写进 labels** | **路由匹配不上**（因为 route 树匹配的是 `severity`/`team`），**告警发不出去**——而且**不报错**，只是"没人收到" |
| **把 `severity` 写进 annotations** | **所有告警都匹配不到 severity 分支**，**全落到兜底 receiver**——**分级路由彻底失效**，所有人都收到所有告警 |
| **labels 里塞长文本** | **每个告警的 label 集变大**，Alertmanager 的内存与去重成本上升 |
| **annotations 里写关键维度** | **通知里看不到维度**；**UI 里按维度筛选不了** |

**标准写法**：

```yaml
- alert: OrderErrorRateHigh
  expr: |
    sum by (cluster, service) (
      rate(http_requests_total{service="order", status=~"5.."}[5m])
    )
    /
    sum by (cluster, service) (
      rate(http_requests_total{service="order"}[5m])
    )
    > 0.01
  for: 10m
  labels:
    severity: critical          # ← 路由用
    team: payments             # ← 路由用
    service: order-api         # ← 分组/抑制用
    cluster: prod-hz-a         # ← 抑制用（equal 里要出现）
  annotations:
    summary: "订单接口错误率 {{ $value | humanizePercentage }} 超过 1%"
    description: "集群 {{ $labels.cluster }} 的 order-api 错误率升高，影响下单链路。"
    runbook_url: "https://wiki.internal/runbook/order-error-rate"
```

> **注意上面这段 PromQL 的口径**（与本套资料 L2-PromQL-实战 一致）：**先 `sum by` 聚合再相除**（不是先除后 sum）；**`rate()` 窗口 5m**（Prometheus 默认 `scrape_interval: 15s` 时，窗口至少要有 4 个样本，5m 有约 20 个样本，充足）。

**`$labels` / `$value` / `$externalLabels`（官方原文）**：

| 变量 | 官方含义 |
|---|---|
| **`$labels`** | **告警实例的 label key/value 对** |
| **`$externalLabels`** | **配置的 external labels**（Prometheus 与外部系统通信时附加的标签） |
| **`$value`** | **告警实例的求值结果** |

### 2.9 告警设计的四条纪律

**这四条是本篇的"价值观"部分，也是面试最能体现经验的部分。**

#### 纪律一：告警必须可执行

**判据（三条全中才算合格）**：

| # | 要求 | 怎么检查 |
|---|---|---|
| 1 | **说清是什么**（summary） | 值班看标题就知道出了什么事，不用点进看板 |
| 2 | **说清影响谁 + 怎么办**（description） | **"影响下单链路"** + **"检查 payment-service 的 DB 连接池"** |
| 3 | **给出手册链接**（runbook_url） | **点开就是处置步骤**，不用问人 |

**反例**：

```yaml
annotations:
  summary: "CPU 高"      # ❌ 什么 CPU？哪个 Pod？为什么高？
```

**正例**：

```yaml
annotations:
  summary: "订单服务错误率 3.2%（阈值 1%）"
  description: "集群 prod-hz-a 的 order-api 5xx 错误率持续 10 分钟超过 1%，影响下单主链路。可能是最近发布的 v2.3.1 引发的。"
  runbook_url: "https://wiki.internal/runbook/order-error-rate"
```

> **为什么这条排第一**：**一条不可执行的告警，收到的人只有两个选择——忽略，或者每次都去问人。** 长期结果就是**告警疲劳**（2.10 节）。

#### 纪律二：必须有人负责

**每条告警必须能回答："这条告警出现时，谁要处理？"**

**实现方式**：**每条告警规则必须带 `team` 或 `owner` 标签**，且**这个 label 参与路由**。

```yaml
labels:
  severity: warning
  team: payments      # ← 路由按 team 分流
```

**判断是否落实了**：**把 route 树和规则列表并排看**——**每条规则的 `team` 都能在某个 receiver 里找到对应的处理方**。**有一条找不到，就是"没人负责"**。

> **"发了但没人管"比"没发"更糟**：**没发 = 大家都知道没监控；发了没人管 = 大家都以为有人在管。**

#### 纪律三：必须分级

**分级不是为了好看，是为了让不同的事走不同的通道**：

| severity | 判据 | 通道 | 响应要求 |
|---|---|---|---|
| **critical** | **用户可感知的影响**（下单失败、支付不可用） | **电话 + 钉钉** | **立即响应** |
| **warning** | **系统有风险但用户无感**（磁盘 80%、证书 7 天后过期） | 钉钉群 | 工作时间处理 |
| **info** | **仅记录**（扩容建议、证书已续期） | 日报 | 不打扰 |

**分级的判据必须是"用户影响"，不是"技术指标有多难看"**：

| 判据 | 例子 |
|---|---|
| ✅ **按用户影响** | "下单失败率 > 1%" → critical；"Pod 重启次数 > 3" → warning |
| ❌ **按技术指标** | "CPU > 80%" → critical；"内存 > 70%" → warning |

> **为什么不能按技术指标分级**：**CPU 80% 可能完全正常**（业务高峰）；**下单失败率 0.5% 可能已经致命**（虽然技术指标看起来很轻微）。**按用户影响分级，才知道"半夜要不要起床"。**

**分级必须贯穿全链**：

```
规则里打 severity  →  route 树按 severity 分流  →  不同的 group_wait / repeat_interval
                  →  不同的 contact point（电话 vs 群 vs 日报）
```

#### 纪律四：必须有恢复通知

**官方机制支持**：告警有 firing / resolved 两态，**`Alerts.Resolved`** 可以取出已恢复的告警。

**但"支持"不等于"会发"**——**很多团队只配了 firing 的通知，resolved 被静默了**。**这是最容易被忽略但影响很大的问题。**

**为什么必须有恢复通知**：

| 场景 | 没有恢复通知 | 有恢复通知 |
|---|---|---|
| **值班视角** | 不知道问题是否已解决，**不敢关闭工单** | **明确知道"已恢复"** |
| **抑制逻辑** | 上游告警一直 firing → **下游告警一直被压着** | 上游恢复 → 下游重新出现 → **真问题不被漏掉** |
| **告警风暴中** | **分不清哪条还在烧、哪条已经好了** | 一目了然 |
| **业务方** | 反复问"好了吗" | **自动回复"已恢复"** |

**配置要点**：

| 位置 | 配置 |
|---|---|
| **Alertmanager `webhook_configs`** | `send_resolved: true` |
| **Grafana contact point** | **取消勾选 "Disable resolved message"**（官方说明里这是一个可选设置） |
| **模板** | 模板里处理 `.Status`（firing / resolved），或用 `Alerts.Firing` / `Alerts.Resolved` 分别渲染 |

> **面试话术**：*"我们的告警都配了恢复通知。这不是为了好看——第一，值班要知道什么时候能关工单；第二，我们的抑制规则是'上游故障压下游'，如果上游不恢复，下游的告警会被一直压着，人看不到问题已经好了。"*

### 2.10 告警疲劳

**定义**：**告警疲劳 = 值班对告警逐渐脱敏，开始忽略甚至不看通知。**

**官方其实在 inhibition-rules 页也承认了这个风险**：

> **"If not carefully configured, inhibition rules can silently suppress alerts and make issues harder to detect."**

**告警疲劳的形成路径**：

```
① 误报多（阈值拍脑袋、抖动型告警）
        ↓
② 通知量超出处理能力（每分钟好几条）
        ↓
③ 值班开始"先看哪条"——按接收顺序扫，而不是按严重程度
        ↓
④ 群里消息刷屏 → **折叠通知 / 静音群 / 调成免打扰**
        ↓
⑤ 真告警被淹没 → **没看到**
        ↓
⑥ 事故
```

**"告警系统失效的头号原因"这个判断为什么成立**：

| 指标 | 正常 | 疲劳 |
|---|---|---|
| 每天告警条数 | 十几条（可处理） | 几百条（不可处理） |
| 误报率 | < 5% | **> 30%** |
| 值班响应时间 | 分钟级 | **小时级或不管** |
| 真事故漏看率 | 接近 0 | **显著上升** |

> **告警系统是"平时最闲、出事最忙"的系统**——**平时没人看它，出事时它是唯一的信息来源**。**如果平时就疲劳了，出事那天它等于不存在。**

**四条降噪手段（按优先级）**：

| 优先级 | 手段 | 治什么 | 配置位置 |
|---|---|---|---|
| **1** | **不写不该报的告警** | **误报本身** | 规则层：阈值取历史 P95×1.2；**删掉没有处置动作的告警** |
| **2** | **`group_by` 分组** | **同类告警刷屏** | route 层 |
| **3** | **`inhibit_rules` 抑制** | **根因引发的衍生告警** | route 层 |
| **4** | **`silence` / `mute timing`** | **已知的维护窗口** | 通知层 |

**"不写不该报的告警"是最有效的一条，但也是最难的一条**——**它要求业务方参与定阈值，而不是运维拍脑袋。**

**一个可以立刻执行的检查清单**：

```
□ 列出过去 7 天的所有告警，按条数排序
□ 找出 Top 10 高频告警，逐条问："这条收到后我要做什么？"
□   → 答不上来 → 删掉或降级为 warning
□   → 答得上来但做过了 → 加抑制规则
□ 检查每条告警是否有 severity 和 team
□ 检查每条告警的 annotations 是否有 summary / description / runbook_url
□ 检查是否有告警从未被处理过 → 它就是噪声源
```

### 2.11 OpenTelemetry Collector 架构

**官方原文（architecture 页）**：

> **"The OpenTelemetry Collector is an executable file that can receive telemetry, process it, and export it to multiple targets, such as observability backends."**
>
> **"Data receiving, processing, and exporting are done using pipelines. You can configure the Collector to have one or more pipelines."**
>
> **"Each pipeline includes the following: A set of receivers that collect the data. A series of optional processors that get the data from receivers and process it. A set of exporters which get the data from processors and send it outside the Collector."**

**官方原文（configuration 页）**：

> **"The structure of any Collector configuration file consists of four classes of pipeline components that access telemetry data: Receivers, Processors, Exporters, Connectors."**
>
> **"Besides pipeline components you can also configure extensions, which provide capabilities that can be added to the Collector, such as diagnostic tools. Extensions don't require direct access to telemetry data."**

**处理顺序**：

```
receiver ──► processor ──► processor ──► exporter
                 │
                 └──► 可以在这里丢弃数据（采样/过滤）
```

**官方原文（数据流细节）**：

> **"Data from all receivers is pushed to the first processor... A processor might also drop the data if it's sampling or filtering... Each exporter gets a copy of each data element."**

**pipeline 配置样例（官方原文）**：

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp, zipkin]
      processors: [memory_limiter]
      exporters: [otlp, zipkin]
```

**三种信号各一条 pipeline**（官方明确：pipeline 的类型是 traces / metrics / logs，**且组件必须支持该类型，否则配置加载时报 `pipeline.ErrSignalNotSupported`**）：

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, transform, batch]
      exporters: [otlp/1, otlp/2]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheusremotewrite]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [loki]
```

**官方原文的两个重要行为**：

| 行为 | 官方原文 | 含义 |
|---|---|---|
| **同一 receiver 可喂给多条 pipeline** | "the `otlp` receiver will send the same data to pipeline `traces` and to pipeline `traces/2`" | **一份数据可以走两条处理链**（如：一条全量给后端、一条脱敏后给低成本后端） |
| **但 fan-out 是同步的** | **"if one processor blocks the call, the other pipelines attached to this receiver are blocked... and the receiver itself stops processing and forwarding newly received data."** | ⚠️ **一条 pipeline 的 processor 卡住，会拖住所有共享该 receiver 的 pipeline**——**这是 Collector 单点风险的具体机制** |
| **同一 processor 在不同 pipeline 里是不同实例** | "each pipeline always gets its own instance of the processor... **the processors are never shared between pipelines**" | **同一 processor 名在不同 pipeline 里状态独立**——**做 tail sampling 时这点很关键** |
| **同一 pipeline 里不能重复引用同名 processor** | "The same name of the processor must not be referenced multiple times in the `processors` key of a single pipeline." | 配错 → 配置加载失败 |

**Connector（连接器）——官方原文**：

> **"Connectors join two pipelines, acting as both exporter and receiver. A connector consumes data as an exporter at the end of one pipeline and emits data as a receiver at the beginning of another pipeline. The data consumed and emitted may be of the same type or of different types."**

**官方给的例子**：从 trace 里生成 metric（`count` connector 数 span event）：

```yaml
receivers:
  foo:
exporters:
  bar:
connectors:
  count:
    spanevents:
      my.prod.event.count:
        description: The number of span events matching either condition.
        conditions:
          - 'attributes["env"] == "prod"'
          - 'name == "prodevent"'

service:
  pipelines:
    traces:
      receivers: [foo]
      exporters: [count]
    metrics:
      receivers: [count]
      exporters: [bar]
```

> **官方原文强调的点**：**"A processor can modify or filter data within a pipeline, but it cannot export traces from that pipeline as metrics in another pipeline. A connector can."** —— **processor 只能在 pipeline 内改数据，跨类型转换必须用 connector。**

**Extension（扩展）——官方原文**：

> **"Extensions are optional components that expand the capabilities of the Collector to accomplish tasks not directly involved with processing telemetry data."**

官方示例：

```yaml
extensions:
  health_check:
    endpoint: 0.0.0.0:13133
  pprof:
    endpoint: 0.0.0.0:1777
  zpages:
    endpoint: 0.0.0.0:55679

service:
  extensions: [health_check, pprof, zpages]
```

> **官方明确**：**"Configuring an extension doesn't enable it. Extensions are enabled within the service section."**（和 connector 一样）

**认证（官方原文）**：Collector 的认证**通过 extension 机制实现**——

| 位置 | 角色 | 官方示例 |
|---|---|---|
| **server authenticator** | 给 receiver 用，**认证进来的连接** | `oidc` extension 配在 receiver 的 `auth.authenticator` |
| **client authenticator** | 给 exporter 用，**给出去请求加认证信息** | `oauth2client` extension 配在 exporter 的 `auth.authenticator` |

**Processor 是"做转换 / 脱敏 / 采样的地方"——具体做什么**：

| 用途 | 典型 processor |
|---|---|
| **内存保护** | `memory_limiter`（**必须放最前**，官方文档与社区实践一致） |
| **批量** | `batch`（减少网络往返，**放最后**） |
| **脱敏** | `transform`（删属性、改属性名）、`redaction` |
| **采样** | `probabilistic_sampler`、`tail_sampling` |
| **补属性** | `resource`（给所有数据加 `k8s.*` 属性） |
| **改指标名/标签** | `metricstransform`（把 OTel 指标名转成 Prometheus 风格） |

### 2.12 OTLP 协议

**官方原文（specs 页，OTel 的规范总入口）**：OTel 的规范分为 **specification**（数据模型与 SDK 行为）、**protocols**（OTLP、Prometheus 兼容等）、**instrumentation**（各语言库）、**configuration** 等几个目录。

**OTLP 的两个关键设计点**：

| 设计点 | 说明 |
|---|---|
| **协议中立** | **数据模型是 OTel 自己的**（Resource / InstrumentationScope / Signal），**传输实现是 gRPC 或 HTTP**。**换传输不用换数据模型** |
| **双传输** | **gRPC（`4317` 端口，官方示例中 `endpoint: localhost:4317`）** 与 **HTTP（`4318` 端口，官方示例中 `endpoint: 0.0.0.0:4317` 为 gRPC 形式）**。**gRPC 性能好，HTTP 兼容性好（穿透代理容易）** |

**OTel 数据模型的三层结构（这是必须懂的）**：

```
Resource          ← 产生这个信号的实体（服务、主机、Pod、集群）
   └─ Attributes（k8s.namespace.name / service.name / cloud.region …）
      └─ InstrumentationScope   ← 产生信号的库/SDK（名字 + 版本）
         └─ Signal              ← trace / metric / log
```

**为什么这层结构重要**：

| 好处 | 说明 |
|---|---|
| **三种信号共享同一份 Resource** | **同一个进程的 trace 和 metric 带着完全相同的 `service.name`、`k8s.*`** → **关联天然成立，不需要手工对齐** |
| **InstrumentationScope 区分库** | 同一个服务用多个 SDK 时，能看出数据来自哪个库 |
| **Attributes 是一等公民** | 跨信号、跨后端**语义一致**——**这才是"厂商中立"的实质** |

**Collector 的 OTLP 接收与导出**：

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

exporters:
  otlp:
    endpoint: backend:4317
    tls:
      insecure: false
```

### 2.13 采样：head sampling vs tail sampling

**官方原文（sampling 页）**：

> **"Head sampling is a sampling technique used to make a sampling decision as early as possible. A decision to sample or drop a span or trace is not made by inspecting the trace as a whole."**
>
> **"The primary downside to head sampling is that it is not possible to make a sampling decision based on data in the entire trace. For example, **you cannot ensure that all traces with an error within them are sampled with head sampling alone**."**
>
> **"Tail sampling is where the decision to sample a trace takes place by considering all or most of the spans within the trace."**

**官方给 head sampling 的优势**：

> **"Easy to understand / Easy to configure / Efficient / Can be done at any point in the trace collection pipeline"**

**官方给 tail sampling 的能力**：

| 场景 | 官方原文 |
|---|---|
| **总是采样含错误的 trace** | "Always sampling traces that contain an error" |
| **按整体延迟采样** | "Sampling traces based on overall latency" |
| **按属性采样** | "Sampling traces based on the presence or value of specific attributes on one or more spans in a trace; for example, sampling more traces originating from a newly deployed service" |
| **按服务区分采样率** | "Applying different sampling rates to traces based on certain criteria, such as when traces only come from low-volume services versus high-volume services" |

**官方给 tail sampling 的三个劣势（原文照录，这段是面试加分点）**：

1. **"Tail sampling can be difficult to implement. Depending on the kind of sampling techniques available to you, it is not always a 'set and forget' kind of thing."**
2. **"Tail sampling can be difficult to operate. The component(s) that implement tail sampling must be **stateful systems that can accept and store a large amount of data**. Depending on traffic patterns, this can require **dozens or even hundreds of compute nodes**... Furthermore, a tail sampler might need to 'fall back' to less computationally intensive sampling techniques if it is unable to keep up." → **必须监控 tail-sampling 组件的资源**。
3. **"Tail samplers often end up as vendor-specific technology today."**

**官方给的组合用法**：

> **"For some systems, tail sampling might be used in conjunction with Head Sampling. For example, a set of services that produce an extremely high volume of trace data might first use head sampling to sample only a small percentage of traces, and then later in the telemetry pipeline use tail sampling to make more sophisticated sampling decisions before exporting to a backend."**

**官方给"什么时候该采样"的判据**：

| 该采样 | 不该采样 |
|---|---|
| **每秒 1000 条以上 trace** | 每秒只有几十条小 trace |
| **大部分 trace 是健康流量、变化很少** | 只用聚合数据，不需要原始数据 |
| **有明确的"异常"判据**（错误、高延迟） | **受法规约束禁止丢弃数据**（且无法把未采样的路由到低成本存储） |

**官方给的成本三视角**（很值得记）：

1. **直接成本**：采样本身的计算资源（如 tail sampling proxy）。
2. **间接工程成本**：维护采样逻辑的成本（涉及的应用、系统越多越贵）。
3. **间接机会成本**：**采样策略不当导致关键信息缺失的代价**。

> **官方结论**：**"it could be cheaper to allocate more resources for observability instead"** —— **如果算不清这笔账，宁可加机器，也别上一个坏掉的采样策略。**

**Collector 侧官方提供的采样 processor**：

| Processor | 类型 | 说明 |
|---|---|---|
| **Probabilistic Sampling Processor** | head 式 | 按比例随机丢弃 |
| **Tail Sampling Processor** | tail 式 | 按 policy（延迟 / 错误 / 属性 / 数值 / 概率）判断完整 trace |

---

## 3. 动手

> **本节约束**：不新建集群、不拉镜像、不创建任何资源。示例为配置与查询。

### 3.1 一份最小可用的 Alertmanager 配置

```yaml
# alertmanager.yml
global:
  resolve_timeout: 5m

route:
  receiver: default-team        # 兜底，必须有
  group_by: ['alertname', 'cluster']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - matchers:
        - severity = "critical"
      receiver: oncall
      group_wait: 10s
      group_interval: 5m
      repeat_interval: 30m
      continue: false

    - matchers:
        - severity = "warning"
      receiver: dev-team
      repeat_interval: 2h

    - matchers:
        - team = "payments"
      receiver: payments-team
      group_by: ['alertname']

receivers:
  - name: default-team
    webhook_configs:
      - url: http://dingtalk-webhook.default.svc:8080/notify
        send_resolved: true

  - name: oncall
    webhook_configs:
      - url: http://oncall-webhook.default.svc:8080/notify
        send_resolved: true

  - name: dev-team
    webhook_configs:
      - url: http://dingtalk-webhook.default.svc:8080/notify
        send_resolved: true

  - name: payments-team
    webhook_configs:
      - url: http://dingtalk-webhook.default.svc:8080/notify
        send_resolved: true

inhibit_rules:
  - source_matchers:
      - alertname = "ClusterDown"
    target_matchers:
      - severity =~ "warning|info"
    equal: ['cluster']

  - source_matchers:
      - alertname = "NodeDown"
    target_matchers:
      - severity =~ "warning"
    equal: ['cluster', 'node']

templates:
  - '/etc/alertmanager/templates/*.tmpl'
```

**看到什么算对**：

| 检查 | 期望 |
|---|---|
| 配置能加载 | `alertmanager` 启动日志里**没有 "error loading config"** |
| 路由树正确 | **一条 critical 告警走到 `oncall`**，一条 warning 走到 `dev-team` |
| 分组生效 | **同一个 alertname + cluster 的多条告警合成一条通知** |
| 抑制生效 | **ClusterDown firing 时，同 cluster 的 warning 告警不通知** |
| 恢复通知 | **告警恢复后收到 resolved 通知**（`send_resolved: true`） |

> **改配置后热加载**：官方原文——**"A configuration reload is triggered by sending a `SIGHUP` to the process or sending an HTTP POST request to the `/-/reload` endpoint."**
> **重要**：**配置格式错误时不会生效，只记一条错误日志**（官方原文："if the new configuration is not well-formed, the changes will not be applied and an error is logged"）。**所以改完配置必须去看日志确认加载成功**，不能假定。

### 3.2 造一次告警风暴，看分组与抑制的效果

**这是本篇最有说服力的验证动作。**

```
① 造 100 条同 alertname 的告警（不同 instance）
   ——生产上对应"一次网络分区，100 个实例全连不上数据库"

② 第一步：不配 group_by（用默认或 group_by: ['instance']）
   → 期望：收到 100 条通知

③ 第二步：配 group_by: ['alertname', 'cluster']
   → 期望：收到 1 条通知，里面列出 100 个实例

④ 第三步：再配 inhibit_rules（NodeDown 抑制同节点上的服务告警）
   → 期望：实例级告警不再通知，只剩 NodeDown 一条
```

**验证点**：

| 观察 | 结论 |
|---|---|
| 通知条数从 100 降到 1 | **`group_by` 生效** |
| 通知内容里能看到所有受影响的实例 | **信息没丢**（这正是官方说的"single page while still being able to see exactly which service instances were affected"） |
| 上游告警存在时下游不再发 | **`inhibit_rules` 生效** |
| 上游恢复后下游重新出现 | **抑制是"临时的"，不是永久的** |

### 3.3 写一份可执行的告警规则（annotations 模板）

```yaml
# prometheus-rules.yaml
groups:
  - name: business-slo
    interval: 30s
    rules:
      - alert: OrderErrorRateHigh
        expr: |
          sum by (cluster, service) (
            rate(http_requests_total{service="order", status=~"5.."}[5m])
          )
          /
          sum by (cluster, service) (
            rate(http_requests_total{service="order"}[5m])
          )
          > 0.01
        for: 10m
        keep_firing_for: 5m
        labels:
          severity: critical
          team: payments
          service: order-api
          cluster: prod-hz-a
        annotations:
          summary: "订单接口错误率 {{ $value | humanizePercentage }} 超过 1%"
          description: "集群 {{ $labels.cluster }} 的 order-api 5xx 错误率持续 10 分钟高于 1%，影响下单主链路。"
          runbook_url: "https://wiki.internal/runbook/order-error-rate"

      - alert: CertExpiringSoon
        expr: |
          (time() - ssl_certificate_expiry_timestamp_seconds) < 7 * 24 * 3600
        for: 1h
        labels:
          severity: warning
          team: infra
        annotations:
          summary: "{{ $labels.instance }} 的证书将在 7 天内过期"
          description: "证书剩余 {{ $value | humanizeDuration }}，请走 ACM/阿里云证书续期流程。"
          runbook_url: "https://wiki.internal/runbook/cert-renew"
```

**看到什么算对**：

| 检查 | 期望 |
|---|---|
| **规则的 `for` 与 `interval` 匹配** | `interval: 30s` + `for: 10m` → **约 20 次求值后才触发**（Prometheus 默认 `evaluation_interval: 15s`） |
| **`keep_firing_for` 抑制抖动** | 条件掉下去后**仍保持 firing 5 分钟** |
| **annotations 渲染正确** | `{{ $value }}`、`{{ $labels.cluster }}` **被替换成实际值**，不是原样输出 |
| **labels 齐全** | 每条都有 `severity` + `team`（路由与"有人负责"两个纪律同时满足） |

### 3.4 Collector 的最小配置

```yaml
# otel-collector.yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 1s
    limit_percentage: 80
  k8sattributes: {}
  transform:
    error_mode: ignore
    trace_statements:
      - delete_key(attributes, "user.email")
  batch:
    send_batch_size: 10000

exporters:
  otlp/tempo:
    endpoint: tempo:4317
  otlp/loki:
    endpoint: loki:4317
  prometheusremotewrite:
    endpoint: http://prometheus:9090/api/v1/write

extensions:
  health_check:
    endpoint: 0.0.0.0:13133
  pprof:
    endpoint: 0.0.0.0:1777

service:
  extensions: [health_check, pprof]
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, k8sattributes, transform, batch]
      exporters: [otlp/tempo]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, k8sattributes, batch]
      exporters: [prometheusremotewrite]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, k8sattributes, batch]
      exporters: [otlp/loki]
```

**看到什么算对**：

| 检查 | 期望 |
|---|---|
| **配置能加载** | Collector 日志**无 `ErrSignalNotSupported` 之类错误** |
| **health_check 生效** | `curl http://<collector>:13133` 返回健康状态 |
| **数据能出去** | 在 Tempo / Loki / Prometheus 里能查到 Collector 转发过来的数据 |
| **脱敏生效** | trace 里 `user.email` 属性**不再出现** |

> ⚠️ **`memory_limiter` 放最前、`batch` 放最后**——这是社区与官方文档一致的实践：**限流器必须第一个看到数据才能保护后面的组件；批处理器必须最后聚合才能减少网络往返**。
> ⚠️ **`transform` 里的 `error_mode: ignore` 很重要**：**默认的 `error_mode` 会在转换出错时丢弃整批数据**——**脱敏规则写错一条，就可能把一整批遥测干掉**。生产上用 `ignore` 更安全。

---

### 3.5 常用运维命令（按场景）

> **本节能做什么**：3.1–3.4 是"把告警管道和 Collector 配起来"，本节是**Alertmanager 配置校验、告警查看/静默、路由验证，以及 OTel Collector 健康检查**的运维命令——按场景归类，每条配真实例子。**告警排障的肌肉记忆就来自这一张表。**

### ① Alertmanager 配置校验（改完先过这一关）

```bash
amtool check-config /etc/alertmanager/alertmanager.yml     # 校验主配置（route / receiver / inhibit）
amtool config routes test --config.file /etc/alertmanager/alertmanager.yml \
  --label=severity=critical --label=service=order          # 拿一条假告警测它会走哪条路由
promtool check rules /etc/prometheus/rules/*.yml           # 顺带校验上游 Prometheus 规则
```

> **实战价值**：`amtool check-config` 能在 reload 前挡掉 route 写错、receiver 引用不存在这类事故；`amtool config routes test` 是**"这条告警到底发给谁"的模拟器**——面试被问"怎么验证路由配置对不对"就答这个。比 reload 后等真告警划算太多。

### ② 告警查看与静默（值班救火日常）

```bash
amtool alert query --alertmanager.url=http://localhost:9093        # 看当前 active 告警（含 label）
amtool silence add alertname=Watchdog --duration=2h --comment="维护窗口"   # 加静默（维护期免打扰）
amtool silence query --alertmanager.url=http://localhost:9093      # 看现有静默
amtool silence expire <silence-id>                                 # 提前结束静默
amtool alert query --inhibited                                     # 看被抑制的告警
```

> **实战价值**：`amtool silence add` 是**维护窗口的标准操作**——屏蔽已知抖动，避免告警疲劳（§2.10）；`amtool alert query` 直接看告警有没有进来、label 对不对，是"告警没收到"排查的第一入口。**别在 UI 点，命令行可脚本化、可进 runbook。**

### ③ 路由与分组验证（为什么没发给对的人）

```bash
amtool config routes --alertmanager.url=http://localhost:9093      # 看路由树
amtool config routes test --label=severity=critical --label=team=db   # 模拟某标签组合走到哪个 receiver
amtool config receivers --alertmanager.url=http://localhost:9093   # 看 receiver 列表
```

> **实战价值**：告警"发错人/没发"先查路由——`amtool config routes test` 用真实标签模拟，能直接告诉你它匹配到哪条 route、哪个 receiver。**路由树配错是告警投递失败的第一大原因**（§2.2 匹配顺序）。

### ④ Alertmanager API（不用 UI 也能查一切）

```bash
curl -s http://localhost:9093/api/v2/alerts | head -c 600        # 看当前告警（等价于 amtool alert query）
curl -s http://localhost:9093/api/v2/silences | head -c 600      # 看静默
curl -s http://localhost:9093/-/ready                            # 健康探针
curl -X POST http://localhost:9093/-/reload                      # 改完配置热加载（需 --web.enable-lifecycle）
```

> **实战价值**：`/api/v2/alerts` 和 `/api/v2/silences` 是 amtool 的底层接口，**脚本化、接自愈系统都靠它**；`/-/reload` 改配置不重启的唯一安全路径，但**必须启动时带 `--web.enable-lifecycle`**，否则 404（和 Prometheus 一样）。

### ⑤ OpenTelemetry Collector 运维（pipeline 健康检查）

```bash
otelcol --config=/etc/otel/config.yaml --dry-run     # 校验 pipeline 配置（components 引用、顺序）
curl -s http://localhost:13133/health/check          # 健康检查（默认 ppor/health_check 扩展）
curl -s http://localhost:8889/metrics | grep -E 'otelcol_(exporter|receiver)_'  # 自身指标：吞吐/报错
ss -ltnp | grep -E '4317|4318|8889|13133'             # OTLP gRPC/HTTP 与扩展端口
journalctl -u otelcol -n 100 --no-pager               # 看启动与 pipeline 报错
```

> **实战价值**：Collector 起不来，先看 `--dry-run` 校验 pipeline（receiver→processor→exporter 引用是否齐全、顺序是否 `memory_limiter` 在前 `batch` 在后，§3.4）；`/health/check` 接探针；`/metrics` 看 exporter 有没有在 flush、receiver 有没有在收。**OTel 排障第一原则是"先确认 pipeline 校验过"。**

### ⑥ 通用：进程、端口、配置校验

```bash
ps aux | grep -E 'alertmanager|otelcol' | grep -v grep   # 进程与启动参数
ss -ltnp | grep -E '9093|9094|13133|8889'                # AM 端口 + OTel 端口
amtool check-config /etc/alertmanager/alertmanager.yml   # AM 改动前的第一道闸
otelcol --config=/etc/otel/config.yaml --dry-run         # OTel 改动前的第一道闸
```

> **实战价值**：两个组件都有"配置校验优先于 reload"的纪律——`amtool check-config` 和 `otelcol --dry-run` 是各自的安全带，比救火便宜。

---

### 3.6 运维排障实战（场景串命令）

> **把 3.5 的命令用起来**：三个最典型的告警/链路故障，按"现象 → 敲的命令 → 怎么读"走一遍。**建议在本地 Prometheus + Alertmanager + OTel 测试链路上练。**

### 场景 A：Prometheus 触发了告警，但人没收到通知

```bash
amtool alert query --alertmanager.url=http://localhost:9093        # 告警进 AM 了没？label 对不对？
amtool config routes test --label=alertname=HighErrorRate --label=severity=critical  # 它该走哪条路由？
amtool silence query                                               # 是不是被静默了
curl -s http://localhost:9093/api/v2/silences | grep -i 'startsAt\|endsAt'  # 静默时间窗
# 典型：路由没匹配到 receiver / 被一条宽静默吞了 / inhibit_rules 抑制了
```

> **读法**：收不到通知，**先确认告警进没进 AM**（`amtool alert query`），再 `config routes test` 看它路由到哪、有没有被静默/抑制（§2.4 / 2.5）。90% 的"漏通知"是路由树配错或静默忘了清，不是邮件/webhook 挂了。

### 场景 B：告警风暴 / 同一条告警刷屏（没分组）

```bash
amtool alert query --alertmanager.url=http://localhost:9093 | wc -l        # 告警数量
amtool config routes --alertmanager.url=http://localhost:9093              # 看 group_by 配了没
curl -s http://localhost:9093/api/v2/alerts | grep -o '"fingerprint":"[a-f0-9]*"' | sort -u | wc -l  # 去重后多少条
# 典型：group_by 只按 alertname，没按 instance → 每台机器各发一封
```

> **读法**：告警刷屏 = 分组维度太粗或没分组（§2.3）。看 `group_by` 配了哪些标签——按 `instance`/`pod` 分才能把"同一类故障的不同实例"归成一封。**这是告警疲劳（§2.10）的直接制造者，也是面试高频题。**

### 场景 C：Collector 起不来 / 数据没出去（OTel pipeline）

```bash
otelcol --config=/etc/otel/config.yaml --dry-run        # 先校验 pipeline
journalctl -u otelcol -n 50 --no-pager                  # 看启动报错（component 引用错/顺序错）
curl -s http://localhost:8889/metrics | grep -E 'otelcol_exporter_sent|otelcol_exporter_send_failed'  # 发出去没
ss -ltnp | grep -E '4317|4318'                          # OTLP 端口在不在
# 典型：pipeline 里 receiver 名拼错 / memory_limiter 没放最前 / 后端地址不可达
```

> **读法**：Collector 没数据，**先看 `--dry-run` 校验 pipeline 引用是否齐全**；再看 `/metrics` 的 `exporter_sent` vs `exporter_send_failed` 确认"收了没、发出去没"。`memory_limiter` 没放最前会直接 OOM（§3.4 纪律），这是最常见的启动即崩原因。

> **三个场景的共同心法**：**告警问题先定位"在 AM 内还是 AM 外"**——`amtool alert query` 是分水岭；**OTel 问题先 `--dry-run` 校验 pipeline**。两者都印证"配置校验优先于 reload"的运维纪律。

---

## 4. 逐字段表

### 4.1 Alertmanager route / inhibit / silence 配置项表

**（一）route（路由）**

| 字段 | 类型 | 必填 | 默认 | 含义 | 写错的后果 |
|---|---|---|---|---|---|
| `receiver` | string | ✅（根路由） | — | 匹配后走哪个接收器 | 根路由不写 → **配置加载失败** |
| `group_by` | list[string] | | 继承父路由 | 按哪些标签分组；**`'...'` 表示按全部标签（= 不聚合）** | 带上 `instance` → **等于没分组** |
| `group_wait` | duration | | **30s** | 新组成立后等多久发第一条 | 太短 → 抖动告警刷屏；太长 → 真故障被延迟 |
| `group_interval` | duration | | **5m** | 同组后续通知的最小间隔 | 太短 → 反复刷屏 |
| `repeat_interval` | duration | | **4h** | 未恢复时多久重发 | 太短 → 同一件事反复响；太长 → 以为已处理 |
| `continue` | bool | | **false** | 匹配后是否继续匹配兄弟节点 | 设 true → **一条告警进多个 receiver，通知量翻倍** |
| `matchers` | list[matcher] | | 无 | 本路由的匹配条件 | **根路由配了 matchers → 部分告警匹配不到任何路由被丢弃** |
| `mute_time_intervals` | list[string] | | 无 | 本路由适用的静默时段名 | 引用了不存在的时段名 → **配置加载失败** |
| `routes` | list | | 无 | 子路由列表 | **顺序即优先级，先声明先匹配** |

**（二）inhibit_rules（抑制）**

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `name` | string | ✅ | 规则名 | 重名 → 覆盖/难排查 |
| `source_matchers` | list[matcher] | ✅ | **源告警**（触发抑制的那个，通常是根因） | 写反 → **该报的没报**（根因压住了自己） |
| `target_matchers` | list[matcher] | ✅ | **目标告警**（被压住的那个，通常是衍生告警） | 写得太宽 → **压掉了不该压的告警** |
| `equal` | list[string] | | 源与目标必须**同值**的标签名 | **不写 = 跨范围全压**（最危险）；**标签不存在时仍视为相等**（官方原文："a missing label and a label with an empty value are the same thing"） |

**matcher 的语法（官方）**：

| 字段 | 含义 |
|---|---|
| `name` | 标签名 |
| `value` | 匹配值 |
| `isRegex` / `=` `!=` `=~` `!~` | 匹配方式；**正则需全匹配** |
| `isEqual` | 等值 / 不等值 |

**（三）silence（静默）**

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `matchers` | list[matcher] | ✅ | 屏蔽哪些告警 | 太宽 → 屏蔽了不该屏蔽的 |
| `startsAt` | time | | 生效时间 | — |
| **`endsAt`** | time | ✅ | **失效时间** | **不设 → 告警被永久静默**（Grafana 侧官方明确"静默不能手动永久删除"） |
| `createdBy` | string | | 创建者 | 事后无人认领 |
| `comment` | string | | 原因 | **事后没人知道为什么屏蔽** |

**（四）receivers（接收器）**

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `name` | string | ✅ | 接收器名（route 引用它） | 名字对不上 → **route 加载失败** |
| `webhook_configs[].url` | string | ✅（用 webhook 时） | 目标地址 | 不可达 → **通知发不出**（不报错，只是没消息） |
| `webhook_configs[].send_resolved` | bool | | **是否发送恢复通知** | 不开 → **值班不知道何时能关工单** |
| `email_configs[].to` / `from` / `smarthost` | string | | 邮件目标 | 邮箱不存在 → 通知丢失 |
| `dingtalk_configs` / `wechat_configs` | — | | 部分版本/发行版提供的原生集成 | — |

### 4.2 OTel Collector 组件表

| 组件类别 | 组件 | 职责（官方口径） | 在 pipeline 中的位置 | 典型用途 | 配错的后果 |
|---|---|---|---|---|---|
| **Receiver** | `otlp` | 收 OTLP（gRPC/HTTP） | **第一站** | 接 SDK 上报 | 端口不通 → **数据进不来** |
| | `prometheus` | 抓 Prometheus 的 scrape 端点 | 第一站 | 收 Prometheus 指标 | — |
| | `hostmetrics` | 采集主机指标 | 第一站 | 主机层指标 | — |
| | `kafka` | 消费 Kafka | 第一站 | 已有日志/事件流 | — |
| | `filelog` | 收文件日志 | 第一站 | 替代 Promtail 的文件采集 | — |
| **Processor** | `memory_limiter` | **限制内存占用** | **必须靠前（第一个）** | **防 OOM** | 放太后 → **前面的组件已经把内存吃爆了** |
| | `batch` | 批量发送 | **靠后（最后）** | 减少网络往返 | 放太前 → 批处理没意义 |
| | `k8sattributes` | 补 Kubernetes 元数据 | 中间 | 给数据加 `k8s.*` 属性 | 关联不上 → **指标与链路对不上** |
| | `resource` | 给所有数据加 resource 属性 | 中间 | 统一打标 | — |
| | `transform` | **转换 / 删改属性（脱敏）** | 中间 | **删敏感字段、改属性名** | **默认 error_mode 下，出错会丢弃整批数据** |
| | `attributes` | 改/删属性 | 中间 | 通用属性处理 | — |
| | `probabilistic_sampler` | 按比例采样（head 式） | 中间 | 降量 | 采样率设太低 → **关键链路采不到** |
| | `tail_sampling` | **按完整 trace 判断是否保留（tail 式）** | 中间 | **"含错误的 trace 全留"** | **它有状态、吃内存、必须监控** |
| | `metricstransform` | 改指标名/标签 | 中间 | OTel 名 → Prometheus 风格 | 改错 → **Prometheus 侧查不到** |
| **Exporter** | `otlp` | 发 OTLP | **最后一站** | 发给 Tempo/Loki/其他 Collector | 目标不通 → **数据堆积在队列里，最终丢弃** |
| | `prometheusremotewrite` | Prometheus remote write | 最后一站 | 指标写 Prometheus | — |
| | `loki` | 直接写 Loki | 最后一站 | 日志写 Loki | — |
| | `debug` | **输出到日志** | 最后一站 | **调试用** | **生产开着 → 日志爆炸** |
| | `logging` | 同上（v0.86.0 之前的名字） | 最后一站 | — | — |
| **Connector** | `count` | 数 span event 生成 metric | **桥接两条 pipeline** | **trace → metric**（processor 做不到） | — |
| | `spanmetrics` | 由 span 生成延迟/调用量指标 | 桥接 | 快速出 RED 指标 | — |
| | `servicegraph` | 由 span 生成服务拓扑 | 桥接 | 服务依赖图 | — |
| | `forward` | 把一条 pipeline 转到另一条 | 桥接 | — | — |
| **Extension** | `health_check` | **健康检查端点**（官方示例 `0.0.0.0:13133`） | **service 段启用，不在 pipeline 里** | **存活探针** | 不配 → **K8s 无法做 liveness 探针** |
| | `pprof` | 性能剖析（官方示例 `0.0.0.0:1777`） | 同上 | 排查 Collector 自身 | **暴露到外网 = 泄露内部信息** |
| | `zpages` | 调试页（官方示例 `0.0.0.0:55679`） | 同上 | 看 pipeline 内部状态 | 同上 |
| | `oidc` | **server authenticator** | 同上 | **给 receiver 认证客户端** | 配错 → **连接被拒** |
| | `oauth2client` | **client authenticator** | 同上 | **给 exporter 加认证** | 同上 |

**（关键提醒，官方原文）**：**"Configuring a connector doesn't enable it. Connectors are enabled through pipelines within the service section."** / **"Configuring an extension doesn't enable it. Extensions are enabled within the service section."**
→ **写进 `connectors:` / `extensions:` 段 ≠ 生效。必须在 `service.pipelines` / `service.extensions` 里引用。**

### 4.3 Prometheus 告警规则字段表

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `groups[].name` | string | ✅ | 规则组名 | 重名 → 覆盖 |
| `groups[].interval` | duration | | **求值间隔**（不设用全局 `evaluation_interval`） | 太长 → **告警延迟** |
| `groups[].labels` | map | | **附加到本组所有告警的标签** | 覆盖规则级同名字段 |
| `groups[].rules[].alert` | string | ✅ | 告警名 | 重名 → 告警被合并，**看不出是哪个** |
| `groups[].rules[].expr` | string | ✅ | **PromQL 表达式** | 口径写错（先除后 sum、窗口样本不足）→ **数字是错的** |
| `groups[].rules[].for` | duration | | **条件满足多久才 firing** | 不写 → **第一次求值就 firing**（抖动全变真告警）；写太长 → **故障延迟发现** |
| `groups[].rules[].keep_firing_for` | duration | | **条件最后一次满足后再保持 firing 多久** | 不写 → **抖动型告警永远在 pending，看着像没触发** |
| `groups[].rules[].labels` | map | | **附加标签（决定路由/分组/抑制）** | **不写 severity → 全落兜底 receiver** |
| `groups[].rules[].annotations` | map | | **说明信息（决定通知内容）** | 不写 → **收到的告警没有可执行信息** |
| `groups[].rules[].record` | string | | **recording rule 名**（不是告警规则） | 写成告警规则 → 不会被当告警求值 |

**模板变量**：

| 变量 | 官方含义 |
|---|---|
| `{{ $labels.<labelname> }}` | **告警实例的 label 值** |
| `{{ $value }}` | **告警实例的求值结果** |
| `{{ $externalLabels }}` | **配置的 external labels** |

---

## 5. 横向对比

### 5.1 Prometheus Alerting vs Grafana Alerting

| 维度 | **Prometheus Alerting + Alertmanager** | **Grafana Alerting（GMA）** |
|---|---|---|
| **规则存哪** | Prometheus 的 `rule_files` | **Grafana 库**（可 provisioning 进 Git） |
| **能查什么** | **只有 Prometheus**（PromQL） | **任意数据源**（Prometheus / Loki / MySQL / ES…） |
| **表达式** | 单 PromQL | **多查询 + 表达式组合**（Reduce / Threshold / Math） |
| **求值进程** | **Prometheus** | **Grafana**（GMA） |
| **去重 / 分组 / 抑制 / 静默** | **Alertmanager** | Grafana 内置（policy + inhibition + silence + mute timing） |
| **通知渠道** | Alertmanager 的 receiver | **Contact point（20+ 集成）** |
| **模板** | Go template（`{{ .CommonLabels }}` 等） | Go template（`{{ .Labels }}` / `{{ .Annotations }}`） |
| **多集群** | **每个集群一套 Prometheus + Alertmanager** | 一个 Grafana 统一管（但 Grafana 成了单点） |
| **Grafana 挂了** | **不影响告警** | **GMA 告警静默** |
| **适合** | **指标告警、SLO、多集群统一治理** | **业务看板级、跨数据源告警** |

**选型结论（与 L3 篇一致）**：

> **指标类告警走 Prometheus + Alertmanager；业务语义类告警走 Grafana Alerting。**
>
> 判据三条：
> 1. **可用性**：Grafana 挂了 GMA 告警静默——**监控不能自己成为单点**。
> 2. **表达能力**：GMA 能查任意数据源、组合多个查询——**"订单量比上周同期低 30% 且支付成功率没下降"这种跨源判断，PromQL 做不到**。
> 3. **治理成熟度**：Alertmanager 的分组/抑制/静默/高可用更成熟，**尤其是多集群场景**。

**两套并存必须统一治理**（四条，见 1.1）——**尤其"统一通知出口 + 用 source 标签区分来源"**。

### 5.2 Alertmanager vs 纯 Prometheus 告警

**"纯 Prometheus 告警"= 不配 Alertmanager，Prometheus 直接把告警发到 notification channel（老版本的 `alertmanager` 直连，或第三方 exporter）。**

| 能力 | **纯 Prometheus（无 Alertmanager）** | **有 Alertmanager** |
|---|---|---|
| **告警实例级发送** | ✅（每实例一条） | ✅（内部仍每实例一条） |
| **通知去重** | ❌ | ✅ |
| **通知分组** | ❌ | ✅ `group_by` |
| **抑制（根因压衍生）** | ❌ | ✅ `inhibit_rules` |
| **静默** | ❌ | ✅ `silence` |
| **分级路由（不同 severity 走不同渠道）** | ❌（只有一个全局出口） | ✅ **route 树** |
| **通知模板** | ❌（只有固定格式） | ✅ Go template |
| **通知限流** | ❌ | ✅ |
| **告警数上限保护** | ❌ | ✅ `--alerts.per-alertname-limit` |
| **高可用** | ❌ | ✅ `--cluster-*`（**但 Prometheus 侧不能对 Alertmanager 做负载均衡**） |
| **集群故障时** | **通知风暴**（几百条） | **一条聚合通知** |

**官方原文（HA 那段，很重要）**：

> **"It's important not to load balance traffic between Prometheus and its Alertmanagers, but instead, point Prometheus to a list of all Alertmanagers."**

**这条必须记住**：**Prometheus → Alertmanager 是"客户端直连多个实例"而不是"经过负载均衡"**。因为每个 Alertmanager 实例都需要收到**全部**告警才能正确去重和分组——**过 LB 就只会有一半告警到某个实例，告警会被重复处理**。

**官方原文（告警上限保护）**：

> **"Alertmanager supports configuration to limit the number of active alerts per alertname... When the limit is reached any new alerts are dropped... This feature is useful when an unexpected high number of instances of the same alert are sent to Alertmanager."**
> 指标：**`alertmanager_alerts_limited_total`**（被上限丢弃的告警总数）。

> **这个指标是告警风暴的"报警器"**——**它涨了说明上游已经在刷屏了**，应该去查是哪条规则炸了。

### 5.3 OTel Collector vs 直接对接后端

| 维度 | **直接对接后端**（SDK 直发 Prometheus/Tempo/SaaS） | **经 Collector** |
|---|---|---|
| **换后端** | **改代码、重新发版** | **改 Collector 配置** |
| **加后端** | **每个后端一套 exporter** | **加一个 exporter 段** |
| **脱敏** | **在应用里做**（改代码） | **在 processor 里做**（改配置） |
| **采样** | **SDK 侧 head sampling** | **SDK head + Collector tail** |
| **补 Kubernetes 元数据** | 应用里拼 | **`k8sattributes` 自动补** |
| **故障影响面** | **后端挂了 → 应用侧受影响** | **Collector 挂了 → 遥测断，但应用不受影响**（除非 SDK 阻塞） |
| **额外资源成本** | **无** | **多一层部署与运维** |
| **语言相关** | **每种语言一套 exporter** | **SDK 只发 OTLP，Collector 统一处理** |
| **临时开启 tracing** | **要改代码发版** | **改 Collector 配置即可**（官方原文特别指出这是痛点） |

**官方原文列举的"直接对接"痛点**（4 条，本篇 1.3 节已引）——**其中第 ③ 条最实际**：

> **"To enable exporting OpenTelemetry spans, stats, or metrics, application users need to manually add library exporters and redeploy their binaries. This is especially difficult when an incident has occurred, and users want to use OpenTelemetry to investigate the issue right away."**

**选型结论**：

| 场景 | 选择 |
|---|---|
| **单后端、小规模、应用团队能改代码** | 直接对接（**少一层运维**） |
| **多后端（≥2）、需要脱敏/统一采样** | **Collector**（**改配置 vs 改代码发版，差距是数量级的**） |
| **需要从 trace 生成 metric（RED）** | **Collector + connector**（`spanmetrics`/`servicegraph`）——**processor 做不到跨类型转换** |
| **K8s 集群** | **Collector（Agent 模式）+ Gateway 两层** |

> **Agent + Gateway 两层的价值（官方原文支撑）**：**Agent 贴近应用收集 → 本地处理（脱敏、采样）→ 转发给 Gateway；Gateway 汇聚 → 路由到多个后端。** 这样**Gateway 挂了 Agent 还能缓冲**（当然缓冲满了还是会丢），且**本地脱敏在数据离开节点前就完成了**——**合规上更干净**。

### 5.4 head sampling vs tail sampling

| 维度 | **Head sampling（SDK 侧）** | **Tail sampling（Collector 侧）** |
|---|---|---|
| **决策时机** | **早期**——"as early as possible" | **看完整个 trace 之后** |
| **决策依据** | **trace ID + 目标比例**（不看 trace 内容） | **完整 trace 的所有/大部分 span** |
| **典型实现** | `TraceIdRatioBased` / `ParentBased` | Collector 的 `tail_sampling` processor |
| **一致性保证** | **整条 trace 要么全采要么全不采**（官方原文："ensures that whole traces are sampled - no missing spans"） | 同样按整条 trace 判断 |
| **能否按"含错误"采样** | ❌ **不可能**（决策时还不知道后面会不会报错） | ✅ **能**（官方原文明确列出） |
| **能否按"总延迟"采样** | ❌ | ✅ |
| **能否按 span 属性采样** | ❌ | ✅ |
| **能否按服务区分采样率** | 部分（SDK 配置层面） | ✅ |
| **优点（官方原文）** | **易理解、易配置、高效、管道任意位置都能做** | **采样能力强得多** |
| **缺点（官方原文）** | **无法基于整个 trace 的数据决策** | **① 实现难；② 运维难（有状态、可能需要几十上百个节点、跟不上时要降级）；③ 常常变成厂商特定技术** |
| **资源开销** | **极低**（SDK 侧一个哈希判断） | **高**（要缓存 trace 直到能判断） |
| **延迟影响** | **无** | **有**（要等 trace 结束或足够长才能决策） |
| **适用** | **流量巨大、大部分 trace 健康、只做趋势分析** | **必须保留错误/高延迟 trace、trace 价值高** |

**组合用法（官方原文的建议）**：

```
高流量服务 → head sampling 先留 1%（SDK 侧，保护管道不被压垮）
          → tail sampling 再按 policy 精细判断（Collector 侧）
```

**选型结论**：

| 场景 | 选择 |
|---|---|
| **每秒几千条以上 trace、大部分是健康流量** | **head sampling 为主**（官方原文说"1% 或更低的采样率常能非常准确地代表其余 99%"） |
| **"错误和高延迟的 trace 必须全留"** | **tail sampling**（**head 做不到**） |
| **既要控量又要保异常** | **head 先减 + tail 再选**（官方推荐的组合） |
| **受法规约束不能丢数据** | **不采样**（官方明确列为"不该采样"的情形） |

> **面试可讲的取舍**：
> "我们用 head sampling 在 SDK 侧先按比例降量，因为大部分 trace 是健康流量，没必要全留；但错误和高延迟的 trace 我们必须全留——这靠 head 做不到，head 做决策的时候还不知道后面会不会出错。所以 Collector 侧加了 tail sampling，按完整 trace 判断。代价是 tail sampler 是有状态的，要单独监控它的资源，官方也明确说了这点。"

---

## 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 | 怎么防）

**排障铁律**：**告警问题分两段查——"告警产生了没有"（规则侧）→ "通知发出了没有"（通知侧）**。
**判断方法**：查 Prometheus 的 `ALERTS` 合成序列——**有 `ALERTS{alertstate="firing"}` 说明规则侧正常，问题在通知侧**。

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **告警风暴**：一次几百条通知 | 规则在正常 firing | **没配 `group_by`，或 `group_by` 里带了 `instance`/`pod`** | 配 `group_by: ['alertname', 'cluster']` | **规则评审时检查 `group_by` 里有没有高区分度标签** |
| 告警风暴，但只有 critical 在刷 | — | **上游根因告警没配抑制**，衍生告警全发 | 配 `inhibit_rules`（源=集群/节点级，目标=实例/服务级） | **每次新增告警规则，问一句"它会不会是另一个告警的衍生？"** |
| **抑制配反了，该报的没报** | 告警在 firing，但没通知 | **`source_matchers` 和 `target_matchers` 写反了**（把根因写成 target） | 交换两者；用 `source_matchers` 指根因 | **抑制规则写完用两个实验验**：① 只有 target → 应通知；② source+target 都在 → target 应被压住 |
| **抑制压掉了跨集群的告警** | 告警在 firing | **`equal` 没写** → 任何 source firing 都压住所有 target | 加 `equal: ['cluster']` | **抑制规则一律写 `equal`**，不写就是全压 |
| 抑制不生效 | — | **`equal` 里的标签在告警上根本不存在** → 官方语义是"缺失标签 = 空值标签"，**两边都没有就视为相等**，行为反直觉 | 确认告警上真的有那个 label；或改 `equal` | **抑制规则用到的 label，必须在源和目标告警上都打上** |
| **告警只发了一次就不发了** | `ALERTS` 里还在 firing | **被 silence 了**（很可能 `endsAt` 没设或设到了很久以后） | 去 Silences 列表看生效中的静默；`Unsilence` 立即结束 | **创建静默必须设 `endsAt`**；官方明确"静默不能手动永久删除" |
| **告警恢复了但没收到恢复通知** | 告警已 resolved | **`send_resolved: false`**（或 Grafana 里勾了 "Disable resolved message"） | 打开 `send_resolved: true` | **恢复通知默认打开**，改配置时别顺手关掉 |
| **告警发到了没人看的群** | 通知正常 | **route 树没匹配到，走了兜底 receiver**；或**兜底 receiver 是个废弃群** | 用 `ALERTS` 查告警的 label，**逐条比对 route 的 matchers** | **route 树要有明确的兜底，且定期核对兜底是谁** |
| 告警被发到**多个**群 | 通知正常 | 某条路由写了 `continue: true` | 检查 `continue` | **`continue: true` 要有明确理由**，且记录在案 |
| **改了 route 配置但没生效** | — | **配置格式错误 → 官方行为是"不应用改动，只记一条错误日志"** | 看 Alertmanager 日志；用官方方式校验配置 | **改完必须去看日志确认加载成功**，不能假定 |
| **通知模板里引用不存在的标签** | 通知发出但字段是空的 | **`{{ $labels.nonexistent }}` 渲染为空**（不报错） | 确认告警上真的有该 label | **模板变量用 <code>&#124; default "N/A"</code> 兜底** |
| **模板里 `{{ $value }}` 显示成原始数字** | 通知发出 | value 是原始数值，没格式化 | 用 `humanizePercentage` / `humanizeDuration` | **模板统一用格式化函数** |
| **告警规则看起来没触发** | `ALERTS` 里只有 `pending` | **`for` 太长**，或者**条件在两次求值之间反复满足/不满足** | 看 `pending` 持续时间；调小 `for` | **抖动型规则配 `keep_firing_for`**，别只靠 `for` |
| 抖动型告警**永远不触发** | 一直 pending | **条件反复满足又掉下去**，`for` 永远攒不满 | 加 `keep_firing_for` | **抖动型规则一律配 `keep_firing_for`**（官方原文明确说它就是为这个设计的） |
| **告警恢复得太快（假恢复）** | 已 resolved | **采集断了（target down）→ 表达式没有结果 → 条件不满足** | 查 `up`；对关键指标用 `absent()` 或加 `or` 兜底 | **关键告警加上"数据缺失也要告警"** |
| **Prometheus 告警没有通知** | `ALERTS` 里 firing | **Prometheus 没配 `alertmanagers`**；或 `--alertmanager.url` 写错 | 检查 Prometheus 配置的 `alerting.alertmanagers` | **Alertmanager 地址用 Service DNS，不用 localhost** |
| **Prometheus 侧做了负载均衡** | 告警重复 | **官方明确不能 LB**——要指向**全部** Alertmanager 实例 | 改成直连列表 | **Prometheus → Alertmanager 是列表不是 LB** |
| **告警量被截断** | 部分告警没出现 | **触发了 `--alerts.per-alertname-limit`** | 查 `alertmanager_alerts_limited_total` 指标 | **监控这个指标**，它涨了就是告警风暴的信号 |
| **Prometheus → Alertmanager 用了 HTTPS 但证书不对** | 连接失败 | 自签证书 / CA 不匹配 | 配 `tls_config` 的 CA；或用 `insecure_skip_verify`（**生产不建议**） | **Alertmanager 之间用 mTLS 更稳** |
| **Grafana 告警规则不触发** | 规则状态 unknown/error | **Grafana-managed 规则的数据源选错**；或**求值靠 Grafana 进程，而 Grafana 挂了** | 看规则的 evaluation error；查 Grafana 可用性 | **Grafana 告警只用于业务类**，指标类走 Prometheus |
| **Grafana 里看不到 Prometheus 的告警** | — | **需要加一个 Alertmanager 类型的数据源** | 加 Alertmanager 数据源指向 Alertmanager | **"在 Grafana 统一看告警"必须先加这个数据源** |
| **Grafana 抑制规则配了没反应** | 告警在发 | **Grafana 13 以下不支持**（官方注明 "Available in Grafana 13 or higher"）；**且没有 UI，只能用 API 管理** | 升级版本；用 App Platform API 配 | **抑制规则配置走 API + GitOps，不要靠手点** |
| **Grafana 静默到期后还在屏蔽** | — | 官方：**过期静默 5 天后自动删除**，**清理每 15 分钟跑一次** | 等待或 `Unsilence` | 静默时间不要设太长 |
| **Grafana 通知模板改不出富格式** | 通知发出但格式受限 | **官方明确能力边界**：不能加 HTML/CSS、不能改 webhook 的数据结构与 HTTP 头、不能改 Slack/Teams 卡片 | **复杂格式走 Webhook 中转** | **需要富格式就自建中转**，别指望模板 |
| **Collector 内存爆了** | Collector OOM | **没有 `memory_limiter`，或它不在 pipeline 第一个** | 加 `memory_limiter` 并**放在最前** | **`memory_limiter` 是第一道闸门** |
| **Collector 内存爆了（配了 memory_limiter）** | — | **`tail_sampling` 吃内存**（要缓存 trace 直到能判断） | 降低采样策略复杂度；扩容；监控 tail sampler 资源 | **官方明确说 tail sampler 可能需要"几十甚至上百个计算节点"** |
| **Collector 的数据在某条 pipeline 卡住** | 其他 pipeline 也停 | **官方原文：fan-out 是同步的——"if one processor blocks the call, the other pipelines attached to this receiver are blocked... and the receiver itself stops processing"** | 找出阻塞的 processor（`pprof` / `zpages`） | **共享 receiver 的多条 pipeline 要隔离风险** |
| **Collector 启动报 `ErrSignalNotSupported`** | 启动失败 | **某组件不支持该 pipeline 的信号类型** | 检查该组件支持的信号类型 | 三条 pipeline 各配各的组件 |
| **Collector 里配了 connector / extension 但没生效** | — | **官方原文：配了不等于启用**——必须在 `service.pipelines` / `service.extensions` 里引用 | 补 `service` 段引用 | **记住这条官方原文** |
| **Collector 的 `transform` 让整批数据消失** | 数据量骤降 | **`transform` 默认 `error_mode` 会在出错时丢弃数据** | 设 `error_mode: ignore`；检查转换语句 | **脱敏规则用 `ignore`**，并做灰度 |
| **OTel 采到的 trace 里没有 k8s 元数据** | — | **缺 `k8sattributes` processor**，或 RBAC 没权限读 Pod | 加 processor；给 ServiceAccount 读 Pod 的权限 | **RBAC 权限在部署清单里就要写好** |
| **改后端时还要改应用代码** | — | **SDK 里直接配了后端 exporter，没走 Collector** | 改成只发 OTLP 到 Collector，后端在 Collector 里切 | **架构约定：应用只认 OTLP** |
| **head sampling 下"含错误的 trace 也被丢了"** | — | **官方明确：head sampling 做不到按 trace 内容决策** | 加 Collector 侧 tail sampling | **"错误必须全留"是硬需求 → 必须 tail sampling** |
| **tail sampling 内存/资源飙升** | — | tail sampler 有状态、要缓存大量数据 | 降策略复杂度；监控资源；必要时降级 | **官方要求监控 tail-sampling 组件的资源** |
| **采样后链路断裂（缺 span）** | trace 不完整 | **配置了不一致的采样策略**；或上游 SDK 与 Collector 采样率不同 | 统一采样决策（用 trace ID 比例采样保证一致性） | **同一 trace 的所有 span 必须做一致决策** |
| **tail sampling 延迟变高** | — | tail sampling **需要等 trace 结束或足够长才能决策** | 调 decision wait；或只对异步 span 采样 | **接受 tail sampling 的延迟代价** |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `prometheus.io/docs/alerting/latest/alertmanager/` | **分组 / 抑制 / 静默 / HA / 告警上限的官方定义** | ✅ 2026-10-24 |
| S2 | 官方 | `prometheus.io/docs/alerting/latest/configuration/` | **route 字段与默认值（group_wait=30s / group_interval=5m / repeat_interval=4h / continue=false）、inhibit_rule 结构、receiver** | ✅ 2026-10-24 |
| S3 | 官方 | `prometheus.io/docs/alerting/latest/notifications/` | **模板数据结构（Data / Alert / KV）、模板函数清单** | ✅ 2026-10-24 |
| S4 | 官方 | `prometheus.io/docs/prometheus/latest/configuration/alerting_rules/` | **告警规则字段、for / keep_firing_for 语义、ALERTS 合成序列、模板变量** | ✅ 2026-10-24 |
| S5 | 官方 | `prometheus.io/docs/prometheus/latest/configuration/recording_rules/` | recording rule 与预计算 | ✅ 2026-10-24 |
| S6 | 官方 | `prometheus.io/docs/prometheus/latest/getting_started/` | **默认 scrape_interval=15s / evaluation_interval=15s**（for 与 rate 窗口口径依据） | ✅ 2026-10-24 |
| S7 | 官方 | `grafana.com/docs/grafana/latest/alerting/` | Grafana Alerting 定位与 Prometheus 模型 | ✅ 2026-10-24 |
| S8 | 官方 | `grafana.com/docs/grafana/latest/alerting/alerting-rules/` | GMA / DMA 两类规则、DMA 弃用 | ✅ 2026-10-24 |
| S9 | 官方 | `grafana.com/docs/grafana/latest/alerting/configure-notifications/` | 通知策略、多 Alertmanager 资源隔离 | ✅ 2026-10-24 |
| S10 | 官方 | `grafana.com/docs/grafana/latest/alerting/configure-notifications/manage-contact-points/` | **Contact point 集成完整清单、protected fields、删除约束** | ✅ 2026-10-24 |
| S11 | 官方 | `grafana.com/docs/grafana/latest/alerting/configure-notifications/inhibition-rules/` | **抑制规则字段、matcher 格式、官方能力边界与"没有 UI"** | ✅ 2026-10-24 |
| S12 | 官方 | `grafana.com/docs/grafana/latest/alerting/configure-notifications/create-silence/` | **静默字段、5 天保留期、15 分钟清理、不能手动永久删除** | ✅ 2026-10-24 |
| S13 | 官方 | `grafana.com/docs/grafana/latest/alerting/configure-notifications/template-notifications/` | **模板语法、官方能力边界** | ✅ 2026-10-24 |
| S14 | 官方 | `opentelemetry.io/docs/concepts/` | OTel 核心概念与目标 | ✅ 2026-10-24 |
| S15 | 官方 | `opentelemetry.io/docs/concepts/signals/` | **三支柱信号类型（traces / metrics / logs / baggage / profiles）** | ✅ 2026-10-24 |
| S16 | 官方 | `opentelemetry.io/docs/concepts/sampling/` | **head / tail sampling 定义、优劣、组合用法、官方成本三视角** | ✅ 2026-10-24 |
| S17 | 官方 | `opentelemetry.io/docs/collector/architecture/` | **pipeline 定义、receiver/processor/exporter 职责、fan-out 同步阻塞、agent 与 gateway 形态** | ✅ 2026-10-24 |
| S18 | 官方 | `opentelemetry.io/docs/collector/configuration/` | **connector 与 extension 语义、"配了不等于启用"、认证扩展** | ✅ 2026-10-24 |
| S19 | 官方 | `opentelemetry.io/docs/collector/transforming-telemetry/` | processor 的转换与脱敏 | ✅ 2026-10-24 |
| S20 | 官方 | `opentelemetry.io/docs/specs/otlp/` | **OTLP 协议与数据模型规范入口** | ✅ 2026-10-24 |
| S21 | 官方 | `grafana.com/docs/loki/latest/alert/` | Loki Ruler 与日志告警（与本套 L4 篇衔接） | ✅ 2026-10-24 |
| S22 | 中文 | `developer.aliyun.com` · `cloud.tencent.com/developer` | 中文实践：钉钉/企微告警接入、告警分级与值班治理经验 | ✅ 2026-10-24 |
| S23 | 云厂商 | `help.aliyun.com/zh/managed-prometheus/` | 托管 Prometheus 的告警链路形态对照 | ✅ 2026-10-24 |

> **完整来源矩阵见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。**
> **官方无中文版**：`prometheus.io/docs/alerting/`、`grafana.com/docs/grafana/latest/alerting/`、`opentelemetry.io/docs/` **均无中文路径**，中文来源只能靠社区与云厂商文档。
> **⚠️ 单源待补**：① 钉钉 / 企业微信机器人的消息长度与条数上限——**Grafana、Alertmanager 官方文档均未给出数值**，本篇只写"有限制、会截断"这一事实（来自社区实践），**具体字节数 ⚠️ 待验证**；② Alertmanager 的默认监听端口——官方 configuration 页未照录 `web.listen-address` 默认值（需 `alertmanager -h` 查看），本篇正文未引用该数字。

---

## 8. 自测三问

1. **能说清输入输出吗？**
   **告警管道**：输入是**Prometheus 里的告警实例**（label 集 + annotations + 状态），输出是**一条（或几条）落到具体渠道的通知消息**。中间链路：Prometheus 求值规则 → `for` 满足后转 firing → POST 到 Alertmanager → **route 树匹配（第一个匹配生效）→ 按 `group_by` 分组 → `inhibit_rules` 抑制 → `silence` 静默 → 模板渲染 → 发给 receiver**。
   **OTel 管道**：输入是**应用发出的 OTLP 数据**（trace / metric / log），输出是**转换、脱敏、采样后的数据，送到多个后端**。中间链路：**SDK → OTLP → receiver → processor（脱敏/采样/补属性）→ exporter → 后端**。

2. **能故意改坏再倒推吗？** —— 五个实验：
   ① **把 `group_by` 从 `['alertname','cluster']` 改成 `['alertname','cluster','instance']`** → 造 100 条告警 → **通知从 1 条变成 100 条** → 倒推出"分组标签的区分度决定分组效果"。
   ② **把 `inhibit_rules` 的 `source_matchers` 和 `target_matchers` 写反** → 制造"集群级告警 + 100 条实例级告警" → **一条通知都不发**（根因被自己压住）→ 倒推出"source 是根因，target 是衍生"。
   ③ **创建一条 silence 但不设 `endsAt`** → 等告警持续 firing → **通知再也不发了**，去 Silences 列表发现它还在 → 倒推出"静默必须设 endsAt，且不能手动永久删除"。
   ④ **在告警规则的 `annotations` 里写 `{{ $labels.no_such_label }}`**，同时把 `severity` 误写进 annotations → **告警落到兜底 receiver，critical 和 warning 进同一个群** → 倒推出"labels 决定路由，annotations 只决定内容"。
   ⑤ **在 Collector 里把 `memory_limiter` 从 pipeline 第一个挪到最后** → 压力上来 → **Collector 内存飙升 / OOM** → 倒推出"限流器必须第一个看到数据"。

3. **能画上下游吗？**
   **告警管道**：
   ```
   PrometheusRule (expr + for + labels + annotations)
        │  HTTP POST /api/v2/alerts
        ▼
   Alertmanager ── route 树（第一个匹配生效，continue 决定是否走兄弟）
        │        ├─ group_by 分组 → group_wait → 首条 → group_interval → repeat_interval
        │        ├─ inhibit_rules（source firing → 压 target，equal 限定范围）
        │        └─ silence（matchers + 时间窗）
        ▼
   模板渲染（Go template：.CommonLabels / .CommonAnnotations / .Alerts / .Status）
        ▼
   receiver → 钉钉 / 企微 / 邮件 / Webhook / PagerDuty
   ```
   **OTel 管道**：
   ```
   应用 SDK（traces/metrics/logs 同一套 SDK）
        │  OTLP (gRPC 4317 / HTTP 4318)
        ▼
   Agent Collector（本地脱敏 + 采样）
        │  OTLP
        ▼
   Gateway Collector（receiver → processor → exporter）
        │                          ├─ Tempo（trace）
        ├─ 汇聚 + 路由             ├─ Prometheus remote write（metric）
        │                          └─ Loki（log）
        ▼
   connector（trace → metric 派生 RED 指标）
   ```
   **并在两图上标出**：
   - 告警管道：**"谁判断出事"（Prometheus）、"谁决定怎么告诉"（Alertmanager）、"告警的 labels 去哪 / annotations 写什么"**；
   - OTel 管道：**"processor 是脱敏与采样的地方"、"connector 才能跨信号类型"、"extension 不碰数据但提供健康检查"、"fan-out 是同步的（一条卡住全部卡住）"**。

---

**上一篇**：[`L4-Loki-日志管道.md`](L4-Loki-日志管道.md) · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
