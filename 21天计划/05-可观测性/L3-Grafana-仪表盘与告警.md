# L3 · Grafana 仪表盘与告警（从"能看图"到"能治理"）

> **本篇定位**：整套资料里**最容易被误解的一个组件**。很多人以为 Grafana 是"画图的"，读完这篇你会知道：它其实是**查询代理 + 展示层 + 告警引擎**三合一，且**自己一个字节的业务数据都不存**。
> **前置**：`05-可观测性/L1-Prometheus-数据模型与采集`（知道 Prometheus 存指标）+ `L2-PromQL-实战`（会写 PromQL）。没读过这两篇，本篇的每个查询都会变成背命令。
> **学完能做什么**：说清 Grafana 和 Prometheus/Loki 的数据流向；独立用 yaml provisioning 把数据源和看板部署进集群；用模板变量做多集群切换和下钻；用 Transformations 把 Prometheus 的宽表变成能看的格式；讲清 Grafana Alerting 和 Prometheus Alerting 的选型依据。
> **在 21 天里**：D13（Grafana 大屏 + Loki 日志管道）的理论部分，D14（周整合）的观测层收口。
> **环境/版本基准**：本文所有 Grafana 字段语义以 **官方 `grafana.com/docs/grafana/latest/`** 为准（该文档站跟随版本滚动，本文写作日期 2026-10-24，核对时各路径均返回 200）。**本篇未在任何集群执行 Grafana 命令**，示例均为可复制的 yaml/查询，标注"官方标称"的默认值来自官方文档。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L3 Grafana 仪表盘与告警
│
├─ 1. 概念
│   ├─ 1.1 一句话定位：Grafana 不存数据，它只查询与展示
│   ├─ 1.2 没有 Grafana 时的问题（为什么需要它）
│   ├─ 1.3 Grafana 与 Prometheus/Loki 的分工边界
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 一次查询的完整链路（浏览器 → Grafana → 数据源 → 回程）
│   ├─ 2.2 数据源（Data Source）类型与各自适用场景
│   ├─ 2.3 Dashboard / Panel / Row 的对象模型
│   ├─ 2.4 面板类型对照（Time series / Stat / Gauge / Table / Heatmap / Logs …）
│   ├─ 2.5 变量与模板：$var / ${var} / 多值 / 下钻 / 多集群切换
│   ├─ 2.6 Repeat：按变量值自动重复面板
│   ├─ 2.7 Transformations：查询后处理（宽表 → 好画的格式）
│   ├─ 2.8 Units 与 Override：单位覆写与阈值着色
│   ├─ 2.9 Annotations：把"发布"和"指标抖动"关联起来
│   ├─ 2.10 权限模型：Viewer / Editor / Admin、组织与文件夹
│   ├─ 2.11 Provisioning：配置即代码（文件 provisioning vs API provisioning）
│   └─ 2.12 Grafana Alerting 与通知渠道
│
├─ 3. 动手
│   ├─ 3.1 本地起一个 Grafana（只读本地已有资源）
│   ├─ 3.2 加数据源 → 建变量 → 建面板（每步写清"看到什么算对"）
│   ├─ 3.3 用 provisioning 复现同一个看板
│   ├─ 3.4 常用运维命令（按场景）
│   └─ 3.5 运维排障实战（场景串命令）
│
├─ 4. 逐字段表
│   ├─ 4.1 Dashboard Provisioning yaml 关键字段表
│   ├─ 4.2 Panel 类型对照表
│   └─ 4.3 Grafana Alerting 关键字段表
│
├─ 5. 横向对比
│   ├─ 5.1 Grafana vs Prometheus 内置 UI
│   ├─ 5.2 Grafana Alerting vs Prometheus Alerting
│   ├─ 5.3 Dashboard as Code（Provisioning）vs 手工点击
│   └─ 5.4 模板变量 vs 固定过滤
│
├─ 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 → 怎么防）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 一句话定位

> **Grafana 是一个"查询代理 + 可视化 + 告警规则引擎"。它不存你的业务数据。**

这句话必须掰开说，因为它是本篇所有内容的根：

| 它**是**什么 | 它**不是**什么 |
|---|---|
| 一个**数据源连接器**（Data Source）：知道 Prometheus 在哪、Loki 在哪、ES 在哪 | ❌ 不是指标数据库 |
| 一个**查询代理**：浏览器发请求给 Grafana，Grafana 再去问后端 | ❌ 不缓存业务数据（缓存是可选加速功能，不是数据仓库） |
| 一个**渲染引擎**：把返回的时序/日志画成面板 | ❌ 不做数据聚合计算（聚合在 Prometheus 侧做） |
| 一个**告警规则引擎**：能定义规则、能算表达式、能发通知 | ❌ 不是"Prometheus 的替代品" |

**数据到底在哪**：

```
┌──────────────┐   存指标（TSDB）      ┌─────────────────────┐
│  Prometheus  │◄──────────────────────│                     │
└──────────────┘   存日志（chunk+index）│      Grafana        │
┌──────────────┐◄──────────────────────│   （不存业务数据）   │
│     Loki     │                       │                     │
└──────────────┘                       └─────────────────────┘
┌──────────────┐   存业务表            │                     │
│   ClickHouse │◄──────────────────────│                     │
│   / MySQL    │                       └─────────────────────┘
└──────────────┘
```

**这个区分带来三个直接后果**（记住这三条，面试能答）：

1. **Grafana 挂了不影响数据**。后端 Prometheus/Loki 的数据还在，重启 Grafana 立刻恢复。**反过来不成立**——Prometheus 挂了，Grafana 上所有面板全空。
2. **数据保留期由后端决定，不由 Grafana 决定**。Grafana 上没有任何"保存历史数据"的开关。
3. **告警挂在 Grafana 上 = 引入单点**。因为规则求值要靠 Grafana 进程活着。这就是为什么生产上"指标告警"必须走 Prometheus Rule + Alertmanager（详见 2.12 与第 5 节）。

### 1.2 没有 Grafana 时的问题

| 裸用 Prometheus / Loki 的痛点 | Grafana 怎么解决 |
|---|---|
| Prometheus 自带 UI 只能看单条 PromQL，看不了"多指标交叉对比" | 一个面板多查询，叠加画在一张图上 |
| 每个集群/每个 namespace 要手动改查询条件 | **模板变量**做下拉，切换即全图联动 |
| 业务方要看"我们服务的 QPS 是多少"，得让运维代查 | **Dashboard + 权限**，业务方自己看 |
| 值班时想"这个点为什么突然掉了" | **Annotations** 在时间轴上标出发布/变更事件，一眼对上 |
| 改了看板不知道改了啥，也无法回滚 | **Provisioning**：看板 json 进 Git，配置即代码 |
| 群发告警写死在一个 Webhook | **Contact point + Notification policy** 按 severity 分级路由 |

### 1.3 Grafana 与 Prometheus/Loki 的分工边界

这是最容易讲错的地方，用一张表钉死：

| 能力 | 归谁 | 为什么 |
|---|---|---|
| 采集指标 | Prometheus | 拉模型 + 服务发现 |
| 存储指标（保留期、压缩、降采样） | Prometheus | Grafana 无此概念 |
| 计算指标（rate / 比例 / p99） | Prometheus（PromQL） | 算在源头，查的时候只读结果，性能差 10 倍以上 |
| 存日志 | Loki | Grafana 不存日志 |
| 查询日志（LogQL） | Loki 执行，Grafana 展示 | Grafana 只是把 LogQL 发给 Loki |
| **画图** | **Grafana** | Prometheus 自带 UI 是"调试器"，不是"看板" |
| **多数据源混排** | **Grafana** | 一个面板同时放 Prometheus 指标 + Loki 日志 + MySQL 订单量 |
| **业务语义模板** | **Grafana** | 变量、Repeat、Transformations |
| **告警规则求值** | Prometheus **或** Grafana（两套，见 2.12） | — |
| **告警去重/分组/抑制/静默** | Alertmanager | Prometheus 自己不做 |
| **通知复盘** | Alertmanager 或 Grafana Contact point | 两套并存时要统一治理 |

> **关键认知**：Prometheus 自带 UI 是**给你自己调试 PromQL 用的**。生产值班大屏不能用它，因为没有变量、没有模板、没法按受众拆、权限极粗。

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Data Source | 数据源 | 指向 Prometheus/Loki/ES/MySQL 的连接配置 |
| Dashboard | 仪表盘 / 看板 | 一组面板的集合，是 Grafana 的核心组织单位 |
| Panel | 面板 | 一个查询 + 一种可视化 + 一组选项 |
| Row | 行 | 面板的折叠分组 |
| Variable | 变量（模板变量） | 可下拉切换的占位符，改值全图联动 |
| Templating | 模板化 | 变量这套机制的官方叫法 |
| Repeat | 重复 | 按变量值自动复制多个面板 |
| Transformation | 数据转换 / 后处理 | 查询返回后的数据整形，**不改后端数据** |
| Override | 覆写 | 针对单个 series/field 覆盖单位、颜色、阈值 |
| Annotation | 注解 | 在图上标注事件（发布、变更、故障） |
| Alert rule | 告警规则 | 定义"什么条件下算告警" |
| Contact point | 联络点 | 通知去哪（钉钉 / 邮件 / Webhook） |
| Notification policy | 通知策略 | 什么告警走哪个联络点、要不要延迟、要不要升级 |
| Provisioning | 预置 / 配置即代码 | 用 yaml 文件自动创建数据源与看板 |
| Explore | 探索 | 临时查数据的页面，不落看板 |
| Mixed | 混合数据源 | 一个面板里每个查询用不同数据源 |
| Dashboard data source | 看板数据源 | 复用另一个面板的查询结果 |
| Folder | 文件夹 | 看板的分组 + 权限载体 |
| Organization | 组织 | Grafana 的租户隔离单位 |
| Service account | 服务账号 | 给 CI / Provisioning 用的机器身份 |
| API key | API 密钥 | 旧版机器身份，现逐步被 Service account 取代 |

---

## 2. 机制

### 2.1 一次查询的完整链路

理解这条链路，才能解释"为什么面板空"这类问题出在哪一层。

```
① 浏览器打开面板
      │  带上时间范围 / 变量值 / 面板 ID
      ▼
② Grafana 解析 dashboard JSON
      │  找到 panel.targets[] → 变量插值 → 得到最终 PromQL
      ▼
③ 查数据源配置（type=prometheus, url, auth）
      │
      ▼
④ Grafana 代替浏览器去请求后端
      │  POST http://prometheus:9090/api/v1/query_range
      │  body: {"query": "sum(rate(http_requests_total[5m]))", "start":..., "step":...}
      ▼
⑤ 后端返回数据（或错误）
      │
      ▼
⑥ Grafana 做 Transformations（本地内存里，不回写后端）
      │
      ▼
⑦ 渲染 → 按 Units 格式化 → 按 Thresholds 着色 → 画图
```

**这张图的排障价值**（面试常问"面板空白怎么查"）：

| 现象 | 卡在哪一层 | 怎么查 |
|---|---|---|
| 面板显示 "No data" | ⑤ 后端确实返回空 | Explore 里跑同一条 PromQL |
| 面板显示红色错误块 | ④ 请求失败（URL/鉴权/后端挂了） | 看错误块里的原文；查 Grafana 日志 |
| Panel 都不见了 | ② dashboard JSON 解析失败 | Dashboard 设置 → JSON Model |
| 变量下拉是空的 | ③ 数据源连不上，或查询语法错 | 变量编辑页 → Run query 直接跑 |
| 图上有数据但格式不对 | ⑥⑦ Transformations / Units 写错 | 面板 → Table view 看原始 frame |

> **固定动作**：任何"面板没数据"，先在 **Explore** 里跑同一条 PromQL。**Explore 能出数、Grafana 面板不出 → 问题在 Grafana；Explore 也不出 → 问题在 PromQL 或后端。** 这一刀切下去，排查范围减半。

### 2.2 数据源（Data Source）类型与各自适用场景

Grafana 官方内置的核心数据源（按用途分组，官方 `datasources/` 页原文清单）：

| 用途分组 | 内置数据源 | 适用场景 | 关键注意 |
|---|---|---|---|
| **指标与时序** | Prometheus、Graphite、InfluxDB、OpenTSDB、AWS CloudWatch、Azure Monitor、Google Cloud Monitoring | K8s 指标（Prometheus）、云厂商托管指标（CloudWatch / Azure Monitor）、老式监控系统（Graphite / InfluxDB） | 云厂商数据源有 API 拉取限流，查询要控制时间范围 |
| **日志** | Loki、Elasticsearch | K8s 集群日志（Loki）、需要全文检索的日志（ES） | Loki 走 LogQL，ES 走 Lucene，**同一面板不能混写两种语法** |
| **链路** | Tempo、Jaeger、Zipkin | 分布式调用链 | 可配 trace-to-logs / trace-to-metrics 关联 |
| **性能剖析** | Pyroscope | CPU 火焰图 | — |
| **SQL 数据库** | MySQL、PostgreSQL、MSSQL | 订单量、用户数、审计流水等**业务数据** | **这是 Grafana 混合能力的最大价值场景** |
| **告警** | Alertmanager | 把外部 Alertmanager 挂进 Grafana 统一看 | 只能看，不能配 |
| **测试调试** | TestData | 造假数据验证面板 | 生产禁用 |

**三种特殊数据源**（官方单独列出，很实用）：

| 特殊数据源 | 作用 | 关键行为 |
|---|---|---|
| **Grafana（内置 `-- Grafana --`）** | 生成随机游走数据、列文件、查 Grafana 自身的注解与告警 | 面板调试用；Annotation 默认就用它 |
| **Mixed** | **一个面板里每个查询用不同数据源** | 各查询**并行**执行，一个失败其他照常返回，失败的那个报错并标出数据源名 |
| **Dashboard** | **复用同一看板里另一个面板的查询结果** | 只能整份引用，不能把 A 面板结果当 B 面板的输入；整形要靠 Transformations |

> **Mixed 的两个限制要记牢**（官方原文）：
> ① **第一个查询用的是"你选择 Mixed 之前"的那个数据源**；② **已存在的查询不能改成 Mixed**。
> 这意味着：**Mixed 要在一开始就选好**，事后调整会踩坑。

**面试怎么答"你用什么数据源"**：
> "指标走 Prometheus，日志走 Loki，链路走 Tempo。但**业务数据走 MySQL 直连**——订单量、支付成功率这类指标如果让应用团队自己埋 Prometheus 指标，跨团队对齐成本很高；直接从业务库查更准。Grafana 的价值就在这：它能把**技术指标和业务指标放在同一张图上对比**，这是纯监控工具做不到的。"

### 2.3 Dashboard / Panel / Row 的对象模型

| 对象 | 官方定义 | 关键约束 |
|---|---|---|
| **Dashboard** | "a set of one or more panels, organized and arranged into one or more rows or tabs" | 面板可以有多个查询（A/B/C…），共享 dashboard 级变量与时间范围 |
| **Panel** | "the container that displays the visualization and provides various controls" | 一个 panel = 查询 + 可视化类型 + 选项 + Transformations |
| **Row** | 面板的折叠行 | 可整行重复（Repeat by rows） |
| **Folder** | 看板的分组，**同时是权限载体** | 看板**继承文件夹权限**；文件夹最多嵌套 **4 层**（官方标称） |

**变量作用域**：Dashboard 级变量对整个看板的所有面板可见；面板级变量（Panel → Options → Variable）只对该面板及它的重复副本生效。

**时间范围也是 dashboard 级的**：所有面板共享，除非面板自己覆盖。**这是"同一个看板不同面板数据对不上"的常见原因**——有人给单个面板设了独立时间范围。

### 2.4 面板类型对照

官方 `panels-visualizations/visualizations/` 页的完整清单，按用途分组：

| 分组 | 面板类型 | 官方定位 | 典型用法 |
|---|---|---|---|
| **图表类** | Time series | "the default and main graph visualization"，**支持告警** | QPS、错误率、p99 趋势 |
| | State timeline | 状态随时间的变化 | Pod 状态流转、发布阶段 |
| | Status history | 周期性的状态变化 | 定时任务成败 |
| | Bar chart | 任意分类数据 | 各接口调用量对比 |
| | Histogram | 值分布 | 延迟直方图 |
| | Heatmap | 二维幅度 | **Prometheus 桶（`le`）的自然形态**、请求耗时×时段 |
| | Pie chart | 占比 | 流量来源占比 |
| | Candlestick | 金融价格数据 | 汇率、股价 |
| | Gauge | 单个值离阈值的距离 | **单值 KPI**，适合大屏顶部 |
| | Trend | x 轴是序数而非时间 | 排名变化 |
| | XY chart | 任意 x/y | 散点关联分析 |
| **数字类** | Stat | 大数字 + 可选 sparkline | **大屏顶部 KPI**（当前 QPS、当前错误率） |
| | Bar gauge | 横向/纵向条形仪表 | 多实例健康度排行 |
| **日志与图** | Logs | 日志主视图 | **Loki 数据源的主力面板** |
| | Node graph | 有向图/网络 | 服务调用拓扑 |
| | Traces | 链路主视图 | Tempo/Jaeger |
| | Flame graph | Profiling 主视图 | Pyroscope |
| **布局与地理** | Canvas | 静态+动态布局元素 | 大屏装饰与排版 |
| | Geomap | 地理空间数据 | 多机房/多地域分布 |
| **组件类** | Table | **唯一**的表格可视化 | 明细列表、Top N |
| | Dashboard list | 列出看板 | 门户首页 |
| | Alert list | 列出告警 | **值班大屏的告警面板** |
| | Annotations list | 列出注解 | 发布历史 |
| | Text | markdown / html | 面板写说明文字 |
| | News | RSS | 大屏资讯 |

> **选型口诀**：**趋势用 Time series，单值用 Stat，比阈值用 Gauge，明细用 Table，分布用 Histogram/Heatmap，日志用 Logs，拓扑用 Node graph，告警用 Alert list。**

### 2.5 变量与模板：$var / ${var} / 多值 / 下钻 / 多集群切换

**变量类型**（官方 variables 页的表格）：

| 类型 | 何时用 | 值从哪来 | 典型场景 |
|---|---|---|---|
| **Query** | 值列表来自数据源查询 | `label_values(...)` / `query_result(...)` | **namespace、集群、pod 列表** |
| **Custom** | 手工定义固定列表 | 你自己填 | 固定几个环境名 |
| **Constant** | 全看板复用的常量，用户不能改 | 你填一个值 | 生产环境的 `env=prod` |
| **Data source** | 用户要在多个数据源实例间切 | 数据源清单 | 多集群 Grafana 联邦 |
| **Text box** | 用户自由输入 | 用户输入 | 自定义过滤条件 |
| **Interval** | 用户改时间聚合间隔 | 预设列表 | 5m / 1h / 1d |
| **Filters** | 看板级 key/value 过滤 | 用户选 | **支持的数据源**（部分 SQL 类） |
| **Switch** | 在两个值之间切换 | 两个预设值 | prod / staging 快切 |

还有一批**全局变量**（Grafana 内置，不能自己建）：当前时间范围、dashboard 名、组织、登录用户等。

**语法**：

| 写法 | 含义 | 注意 |
|---|---|---|
| `$var` | 插值 | **后面紧跟字母/数字/下划线时会连着一起替换**，容易踩坑 |
| `${var}` | 显式插值 | **推荐在拼接字符串时用**，边界清晰 |
| `${var:format}` | 带格式的插值 | 例如 `${var:raw}` |
| `$__all` | Query 变量的"全部"选项 | 配合 multi-value 使用 |

**多值变量（Multi-value）**：勾上后用户可以同时选多个值，PromQL 里要**用正则匹配**而不是等值匹配：

```promql
# 单值变量
sum(rate(http_requests_total{namespace="$ns"}[5m]))

# 多值变量（Grafana 会把多值拼成 {a|b} 这种形式，必须用 =~
sum(rate(http_requests_total{namespace=~"$ns"}[5m]))
```

> **这是变量最常见的坑**：变量改成 multi-value 后，PromQL 里的 `=` 不改，永远只匹配到第一个值或匹配不到。

**URL 同步**：变量值会写进 URL，格式是 `var-<变量名>=值`（官方原文）。可以用 `skipUrlSync: true` 关掉——**常量变量默认就是 `skipUrlSync: true`**（官方原文，因为它的值固定，本就不该被 URL 改）。

**下钻（drilldown）三种做法**：

| 做法 | 怎么配 | 适合 |
|---|---|---|
| **变量链**（推荐） | 变量 B 的查询里引用变量 A：`label_values(kube_pod_info{namespace="$ns"}, pod)` | 集群 → namespace → pod 逐层收窄 |
| **面板下钻链接** | 面板上配 Data link，URL 里带 `var-x=$value` | 从总览图点某个点跳到详情页 |
| **URL 参数直传** | 直接在 URL 上改 `var-ns=prod` 分享 | 微信/钉钉里甩一个带上下文的链接给别人 |

**多集群切换**（面试高频）：

```promql
# 变量 d 是数据源变量（Data source 类型），多集群各建一个 Prometheus 数据源
sum(rate(http_requests_total[5m]))
# 查询本身不写数据源，由面板选择的 $d 决定
```

配好 `$d`（Data source 类型变量）+ 一个多集群聚合数据源（Mimir / Thanos / VictoriaMetrics），就能在一个面板里切集群。**前提是各集群的指标名与标签集一致**，否则切过去一片空白。

### 2.6 Repeat：按变量值自动重复面板

**Repeat** 让一个面板按变量的每个值自动复制一份。

| Repeat 方式 | 官方含义 | 用途 |
|---|---|---|
| 按**变量**重复 | 对变量的每个取值生成一个面板副本 | **每个 namespace 一个 QPS 图**、每个集群一张盘 |
| 按**行**重复 | 对每个 Row 重复整行 | 每租户一整组面板 |
| 最小间隔 | 相邻副本的最小渲染间隔 | 变量取值很多时防止页面卡死 |

**Repeat 与变量的区别**（很多人搞混）：

| | 变量 | Repeat |
|---|---|---|
| 作用范围 | 改**值**，影响所有引用它的面板 | 生成**多个面板副本**，每个副本里变量的值不同 |
| 结果 | 一个面板，多种数据 | N 个面板，一份模板 |

**正确姿势**：变量定义一个"要重复的对象列表"（如 `pod`），面板里用 `{{$pod}}` 引用，Grafana 会为每个 pod 生成一个面板并在标题里显示 pod 名。

### 2.7 Transformations：查询后处理

官方对 Transformation 的定义（原文）：**"a powerful way to manipulate data returned by a query before the system applies a visualization."**

关键理解：**Transformation 只在 Grafana 内存里做，不改后端数据**。所以它**不会**减轻 Prometheus 的查询压力（**Recording Rule 才能**）。

官方列了 **34 种** Transformation（`transform-data` 页清单），下面按"解决什么问题"归类，只讲高频的：

| Transformation | 解决什么问题 | 关键行为与坑 |
|---|---|---|
| **Reduce** | 把多序列压成一个汇总值 | 两种模式：`Series to rows`（每字段一行）/ `Reduce fields`（保留 frame 结构压成一个值）。**时间字段会被移除**。压完只剩一个值，后续就只能在单值上玩 |
| **Labels to fields** | **把时序的 labels 变成表格列**（Prometheus → Grafana 的关键转换） | `Columns` 模式：label 键成列名；`Rows` 模式：每个 series 一行。**内部是"提取 labels + merge"两个 transformation 合并，merge 步骤不可关闭** |
| **Rows to fields** | 每行变一个字段 | 需指定"字段名来源"和"字段值来源"。**每个字段可单独配 Min/Max/Unit/Thresholds** |
| **Rename by regex** | 批量改名 / 去掉前缀后缀 | **正则只作用于匹配部分**，替换支持反向引用。Grafana v9.0.0 起支持 `//g` 全局模式 |
| **Add field from calculation** | 用别的字段算出新字段 | 支持二元、一元、累计、窗口函数、行索引。一次只加一个字段 |
| **Filter fields by name** | 按名字删字段 | 正则匹配到的字段**即使手动取消勾选也保留**；可用 `${变量名}` 让字段显示跟着变量走 |
| **Organize fields by name** | 重命名 / 排序 / 隐藏字段 | **只适用于单查询面板**（多查询要用 Join by field） |
| **Join by field** | 多查询结果合并成一张表 | Inner join（只留全匹配的）/ Outer join（含不匹配的行）。**多序列转宽表的正解** |
| **Group by** | 按字段分组并算统计 | 可对每组算均值、计数、最后值 |
| **Sort by** | 排序 | 可反向 |
| **Extract fields** | 从某个字段里抠结构化内容 | JSON / Key+value / RegExp / Auto |
| **Convert field type** | 改字段类型 | 转 Time 时按 Moment.js 格式解析，**秒级时间戳会被当成 1970 年的值** |

**"Prometheus 宽表看不清"的标准解法**（这是本节最该记住的）：

Prometheus 返回的数据天然是**宽表**（每个 series 一行、每列一个时间点）。Grafana 的 Table 面板处理这种数据很别扭。三种解法：

| 解法 | 怎么做 | 适合 |
|---|---|---|
| **Labels to fields** | labels 变列 | Top N 明细、实例对比表 |
| **Join by field / Merge** | 多个查询合并 | 多服务并排对比 |
| **Heatmap + Prepare time series** | 把桶数据转成"时间×桶"矩阵 | 延迟分布（`le` 桶天然二维） |
| **换面板类型** | 直接用 Time series 多 series | 趋势对比（**最省事，通常就对**） |

> **判断口诀**：先问"我要看的是趋势还是明细"。**看趋势 → Time series 多 series，不够就上 Transformation；看明细 → Table + Labels to fields。** 大多数人做错是因为在 Table 里硬塞宽表。

### 2.8 Units 与 Override：单位覆写与阈值着色

**Standard options（标准选项）**：作用于**所有字段**，包括 Unit、Min、Max、Decimals、Display name、Color scheme、No value、Value mappings、Thresholds、Links。

**Unit（单位）**：官方说明单位选好后**应用于除时间外的所有字段**。自定义单位支持特殊语法：

| 语法 | 含义 | 例 |
|---|---|---|
| `suffix:<suffix>` | 值后面加后缀 | `suffix:req/s` |
| `prefix:<prefix>` | 值前面加前缀 | `prefix:¥` |
| `time:<format>` | 日期时间格式 | `time:YYYY-MM-DD` |
| `si:<base scale><unit>` | 自定义 SI 单位 | `si: mF` |
| `count:<unit>` | 自定义计数单位 | `count:次` |
| `currency:<unit>` | 货币 | `currency:CNY` |
| `currency:financial:<unit>` | 完整金额不缩写 | `currency:financial:$` |

**两个单位坑**（官方原文）：
1. **时间格式单位期望输入是"毫秒级 Unix 时间戳"**。如果数据源给的是秒，**会被解析成 1970 年 1 月 1 日附近**。修正办法：加一个 `Add field from calculation` transformation，模式选 **Binary operation**，做 **×1000**。
2. Grafana 会**自动按量级缩放单位**（0.14kW 显示成 140W，3000kW 显示成 3MW）。想固定显示就要自定义单位。

**Thresholds（阈值）**：官方默认值（原文照录）：

| 默认项 | 值 |
|---|---|
| 阈值 | **80 = red** |
| Base | **green** |
| Mode | **Absolute** |
| Show thresholds | **Off**（部分可视化） |

两种模式：**Absolute**（绝对值）与 **Percentage**（相对 min/max 的百分比）。Base 代表负无穷，默认绿色。

**Override（覆写）**：Standard options 是"一刀切"给所有字段；**Override 是给单个 series/field 单独设**——不同单位、单独颜色、单独阈值、单独小数位。

**典型的 Stat 面板正确做法**：主显示字段用 Override 单独设阈值（比如错误率 > 1% 红），而不是给整个面板设一个阈值。

### 2.9 Annotations：把"发布"和"指标抖动"关联起来

**这是 Grafana 最被低估、但对排障价值最高的功能。**

官方定义（原文）：**"Annotations provide a way to mark points on a visualization with rich events. They are visualized as vertical lines and icons on all graph panels."**

三种创建方式：

| 方式 | 数据存在哪 | 怎么用 |
|---|---|---|
| 面板内置注解查询（默认） | **存在 Grafana 库里** | 点面板上的数据点 → Add annotation → 写描述和标签 |
| HTTP API | 同上 | CI/CD 在发布时调 API 自动打点 |
| **Annotation query** | **从数据源查**（Prometheus、Loki、MySQL…） | 用一个数据源当"事件库"，比如查一张 `deploy_events` 表 |

支持的图表类型（官方）：**Time series、State timeline、Candlestick**。

**内置注解查询的两个关键行为**：
- 默认使用 **`-- Grafana --`** 特殊数据源，**只有它支持手工创建注解**。
- **"Save As" 复制看板后，新看板 ID 不同，原看板的注解不会自动跟过来**——除非你新建一个 Annotation query 并**按 tag 过滤**。官方原文明说了这个限制。
- **启用 tag 过滤的外部共享看板有信息泄露风险**：官方警告——这类查询会返回**组织内所有看板**的匹配注解，即使那些看板没有对外共享。这是官方设计行为，共享看板前要审一遍 tag。

**工程价值（这才是重点）**：

```
发布流水线 ──► 调 Grafana Annotation API ──► 打 tag="deploy" 的注解
                                                 │
                                                 ▼
              业务面板上出现一条竖线，位置=发布时间
                                                 │
                                                 ▼
     "错误率在 14:32 开始涨" —— 竖线就在 14:30 的发布点上
```

**没有 Annotations 时**，值班只能看到"14:32 突然掉了"，得去翻发布记录；**有了它**，一眼对上。**这就是"发布"和"指标抖动"关联起来的关键手段**，也是本篇最该在面试里讲的一个点。

**三种打点方式选型**：

| 方式 | 谁来打 | 适合 |
|---|---|---|
| 手工（点面板加注解） | 值班/研发 | 临时标记"我刚才重启了这个 pod" |
| **CI/CD 打**（Annotation API） | 流水线 | **发布、扩缩容、配置变更——这是主线** |
| Annotation query（查数据源） | 数据库/监控系统 | 已有发布记录系统，不想重复维护一份 |

### 2.10 权限模型

**Grafana OSS 的五个基础角色**（官方原文清单）：

| 角色 | 能做什么 | 不能做什么 |
|---|---|---|
| **Grafana administrator** | 管理整个实例：用户、数据源、许可、全部组织 | — |
| **Organization administrator** | 管理本组织：成员、数据源、文件夹、看板 | 管不了实例级设置和其他组织 |
| **Editor** | 建/改看板、建文件夹、改面板告警 | **不能增删数据源** |
| **Viewer** | 只读看板、查 Explore | 不能改任何东西 |
| **None** | 明确无权限 | — |

> **官方原文**：**"Only users with the organization administrator role can add or remove data sources."**（只有组织管理员能增删数据源）

**默认权限的一个坑**（官方原文）：**"By default, any user in an organization can query any data source in that organization."** —— 也就是说，**默认情况下组织里任何人都能查所有数据源**。Grafana Enterprise / Cloud 才能配 data source permissions 做细粒度限制。**OSS 版要隔离，只能靠"不同组织"或"数据源侧鉴权"**。

**文件夹与权限继承**：
- 看板**继承文件夹权限**；文件夹权限可授给组织角色、团队、用户。
- **RBAC**（Enterprise / Cloud 特性）提供 fixed roles 和 custom roles；OSS 只有基础角色。
- **权限传播的两条规则**（官方原文，容易踩）：
  1. **修改一个基础角色不会传播到其他基础角色**——你给 Viewer 加了权限，Editor 和 Admin **不会**跟着变。
  2. **新增权限会自动加进"已被修改过的"基础角色**——你以为删掉的功能，新的权限会被自动加回来。**用 custom role 才会完全可控**。
- **RBAC 限制**（官方原文）：**名为 `General` 或 `general` 的文件夹不能用 RBAC 管理权限**（开启 RBAC 时该文件夹权限设置会被忽略）。
- **恢复已删除看板的三个限制**（官方原文）：**权限不恢复、继承目标文件夹权限、版本历史重置为 version 1**。

**Service account 与 API key**：CI/CD 与 provisioning 用**机器身份**（Service account 是新方式，API key 是旧方式，官方正在引导迁移）。**不要用 admin 的 API key 做 CI**——一旦泄露等于整个 Grafana 沦陷。

### 2.11 Provisioning：配置即代码

**官方原文**：**"Grafana has an active provisioning system that uses configuration files. You can define data sources and dashboards using files that can be version controlled, making GitOps more natural."**

**两条路**：

| 方式 | 怎么用 | 适合 |
|---|---|---|
| **文件 provisioning** | 把 yaml 放进 Grafana 的 `provisioning/` 目录，启动时读取 | **生产主流**——文件进 Git，天然 GitOps |
| **API provisioning** | 调 Grafana HTTP API 写入 | 动态生成、需要按环境改内容时 |

**目录结构**（官方 `provisioning/` 页原文）：

| 目录 | 放什么 |
|---|---|
| `provisioning/datasources/` | 数据源 yaml |
| `provisioning/dashboards/` | 看板 provider yaml（看板 json 放 provider 指定的 `options.path`） |
| `provisioning/plugins/` | 插件 app 配置 |
| `provisioning/alerting/` | Grafana Alerting 规则 |
| `provisioning/access-control/` | 权限相关 |

**文件 provisioning 的关键行为**（官方原文）：
- 数据源 yaml 里的 `datasources` 列表，**启动时新增或更新**；已存在就**重新配置成文件里的样子**。
- `deleteDatasources` 列表里的数据源**在新增/更新之前被删除**。
- 数据源 yaml 根级加 `prune: true` → **文件里删掉的数据源也会被自动删除**；**整个 provisioning 文件删掉，provisioned 数据源也会被删**。
- 看板 provider：`type: file` 时，Grafana 启动时扫描 `options.path`，**更新或创建**找到的所有看板；`updateIntervalSeconds` 控制**多久扫一次变更**。
- `allowUiUpdates`（官方示例值 `false`）：**是否允许在 UI 里改 provisioned 的看板**。设 `false` 就等于**锁死 UI 改动**——这是"禁止人在生产看板上乱改"的技术手段。
- `foldersFromFilesStructure: true` 时**必须取消 `folder` 和 `folderUid`**，用磁盘目录结构生成 Grafana 文件夹；**嵌套深度上限 4 层**。
- 看板文件可以是普通 dashboard JSON（外层包 `dashboard` / `folderUid` / `overwrite`），也可以是 **Kubernetes 资源格式**（`kind: Dashboard`、`apiVersion: dashboard.grafana.app/v1`）。官方原文：**"You *must* use the Kubernetes resource format to provision dashboards v2 / dynamic dashboards."**
- **不设 `folder` 字段 → 看板装在根目录**（官方原文）。

**Provisioning 的三个致命细节**：

| 细节 | 后果 |
|---|---|
| **provider 的 `options.path` 指向的目录必须真的存在且 Grafana 进程可读** | 静默不加载，UI 里看不到看板，**不报错** |
| **provisioned 数据源默认 `editable: false`** | UI 里改不了——这是保护，但也意味着**要改必须改文件** |
| **Grafana 重启后手工改的 dashboard 会丢** | 这是本篇踩坑表的第一条 |

### 2.12 Grafana Alerting 与通知渠道

**Grafana Alerting 的架构**（官方原文）：**"Grafana Alerting is based on the Prometheus Alerting model, whose architecture decouples rule evaluation from notification handling."** —— **规则求值与通知处理解耦**。

**两类告警规则**（官方 `alerting-rules/` 页原文）：

| 类型 | 官方定位 | 规则存在哪 | 现状 |
|---|---|---|---|
| **Grafana-managed（GMA）** | **"the recommended option"**，可查后端数据源（含 Prometheus 类），功能更丰富 | **Grafana 库里** | **新栈默认走这套** |
| **Data source-managed（DMA）** | 支持 Prometheus 类数据源（Mimir、Loki、Prometheus），规则存在数据源自己那 | 数据源里 | **Grafana 预置的 Loki/Prometheus 的 DMA 已 deprecated**，新栈不再 provision；自建 Mimir/Loki/Alertmanager 数据源仍可用 |

**Grafana Alerting vs Prometheus Alerting 的能力差异**（这是本篇的核心对比，第 5 节详表）：

| 维度 | Grafana Alerting | Prometheus Alerting |
|---|---|---|
| 能查什么 | **任意数据源**（Prometheus + Loki + MySQL + ES…） | **只有 Prometheus**（PromQL） |
| 表达式能力 | **多查询 + 表达式组合 + Reduce/Threshold/Math** | 单 PromQL 表达式 |
| 规则存哪 | Grafana 数据库（可 provisioning） | Prometheus 的 rule_files |
| 求值靠谁 | **Grafana 进程**（GMA） | Prometheus 进程 |
| 通知渠道 | Contact point（钉钉/企微/Webhook/邮件/Slack/PagerDuty…） | 全部交给 Alertmanager |
| 分组/抑制/静默 | 有（notification policy + inhibition rules + silences） | 靠外部 Alertmanager |
| **Grafana 挂了会怎样** | **告警静默**（GMA 靠 Grafana 求值） | Prometheus 挂了告警也停，但 Grafana 挂了不影响 |

**Contact point（联络点）官方完整清单**（`manage-contact-points/` 页原文）：

Alertmanager · AWS SNS · Cisco Webex Teams · **DingDing（钉钉）** · Discord · **Email** · Google Chat · Grafana IRM · Jira · Kafka REST Proxy · Line · Microsoft Teams · MQTT · Opsgenie · PagerDuty · Pushover · Sensu Go · Slack · Telegram · Threema Gateway · VictorOps · **Webhook** · **WeCom（企业微信）**

**四个关键约束**（官方原文）：

1. **一个 contact point 可以含多个 integration**，告警会发给其中每一个。也可以**配一个都没有的 contact point——那就不发任何通知**。
2. **Contact point 绑定到某一个 Alertmanager，不能跨 Alertmanager 复用**。多个 Alertmanager 时各自的 contact point、模板、策略、静默都是**互相独立**的。默认用**内置 Alertmanager**。
3. **Protected fields（受保护字段）**：Webhook / PagerDuty / Opsgenie 等的**目标 URL 属于受保护字段**，普通编辑权限的用户**改不了**（防止把通知重定向到攻击者服务器泄露 token）。只有拥有 `alert.notifications.receivers.protected:write` 这个 RBAC action 的用户能改。
4. **正在被 notification policy 引用的 contact point 不能删**——必须先改策略或删策略。

**关于钉钉/企业微信的实际限制**（重要）：

| 渠道 | 能力 | 关键限制 |
|---|---|---|
| **钉钉（DingDing）** | 内置集成，直接填机器人 Webhook URL | **钉钉机器人自定义 Webhook 的 `msgtype` 只有 `text`（文本）/ `markdown` / `link` / `actionCard` 等有限几种，且 `text` 有长度上限、markdown 有条数与标题限制**。**Grafana 内置的 DingDing 集成只发文本消息**——告警标题 + 内容塞进一个 text 字段。**多条告警拼在一起会被截断** |
| **企业微信（WeCom）** | 内置集成，填机器人 Webhook URL | 企业微信机器人的 markdown 消息也有**条数上限（官方 4096 字节量级）与字段限制** |
| **Webhook** | **完全自定义 JSON** | **复杂告警（表格、多维度、多链接、@人）必须走 Webhook 中转**：自己写一个小服务把 Grafana 的 JSON payload 转成钉钉/企微能接受的富格式 |

> **这正是本篇踩坑表里"钉钉机器人消息被截断"的根因**，也是面试里能讲出"深浅"的一个点：不是"Grafana 支持钉钉"，而是"**Grafana 内置集成能发文本，要发富格式得自己中转**"。

**通知模板（Notification template）**：每个 integration 都可以选"预定义消息 / 自定义消息 / 通知模板"。模板里可以用 `{{ .Labels }}`、`{{ .Annotations }}` 之类的变量。**告警的 labels 决定它去哪（路由），annotations 决定通知里写什么** —— 这条区分在第 5 节和 L5 篇展开。

---

## 3. 动手

> **本节约束**：不新建集群、不拉镜像。示例是**可复制的配置与查询**，需要你在自己的环境里执行。

### 3.1 本地起一个 Grafana（只读本地已有资源）

如果你本地/集群里已经有 Grafana，直接用；没有的话最省事的是 Docker（**只读本地已有镜像，不拉新镜像**）：

```bash
# 官方文档给的命令形态（若本地已有 grafana/grafana 镜像可去掉拉取动作）
docker run -d -p 3000:3000 --name=grafana \
  --volume grafana-storage:/var/lib/grafana \
  grafana/grafana
```

**看到什么算对**：浏览器打开 `http://localhost:3000`，出现登录页。**默认管理员账号是 `admin` / `admin`（官方标称）——第一件事是改密码**，这是硬规矩。

> ⚠️ **端口说明**：官方 Docker 文档用的是 `-p 3000:3000`（宿主机 3000 → 容器 3000）。但在 Helm/K8s 里，Grafana 的 **Service 端口通常是 80，容器端口 3000**——所以第 13 天日课里写的是 `kubectl port-forward svc/grafana -n monitoring 3000:80`（本地 3000 → Service 80）。**两个 3000 含义不同，别混。**

### 3.2 加数据源 → 建变量 → 建面板

**第 1 步：加 Prometheus 数据源**

`Connections → Data sources → Add new data source`：

| 字段 | 集群内填什么 | 说明 |
|---|---|---|
| Name | `Prometheus` | 面板里引用的名字 |
| Type | Prometheus | — |
| URL | `http://prom-kube-prometheus-stack-prometheus:9090` | **集群内 DNS，不带协议后面的路径** |
| Access | `Server`（proxy） | **默认就是 Server**；浏览器直连=Direct，跨域和安全都不行 |
| Basic auth / TLS | 按需 | 托管 Prometheus 常要开 TLS skip verify |

**看到什么算对**：点 **Save & Test**，出现 **"Successfully queried the Prometheus API"**。**这一步不通过，后面全是白搭**——这是本篇第一条排障铁律。

**第 2 步：建一个 namespace 变量**

`Dashboard settings → Variables → Add variable`：

| 字段 | 值 | 说明 |
|---|---|---|
| Name | `ns` | 引用时写 `$ns` 或 `${ns}` |
| Type | **Query** | 值从数据源查 |
| Data source | Prometheus | — |
| Query | `label_values(kube_namespace_created, namespace)` | **官方文档给出的标准写法** |
| Multi-value | 勾上 | 多集群/多 namespace 切换必备 |
| Include All | 勾上 | 多值时 "All" = `.*` |
| Refresh | `On Time Range Change` | 时间范围变了就刷新选项 |

**看到什么算对**：看板顶部出现下拉框，列出集群里真实存在的 namespace。**列表空 → 变量查询写错或数据源没通**（在变量编辑页有 **Run query** 按钮，直接跑）。

**第 3 步：建一个业务面板**

```
Type     : Time series
Data source : Prometheus
Query A  : sum(rate(http_requests_total{namespace=~"$ns"}[5m]))
Query B  : sum(rate(http_requests_total{namespace=~"$ns",status=~"5.."}[5m]))
           ÷ sum(rate(http_requests_total{namespace=~"$ns"}[5m]))
Unit     : percentunit
```

**看到什么算对**：两条线——QPS 一条、错误率一条，**错误率那条在 0 附近**。

> ⚠️ **PromQL 口径与本套资料 L2-PromQL-实战 保持一致**：
> - **`rate()` 窗口至少 4 个样本**（Prometheus 默认 `scrape_interval: 15s` 时，**至少 1m，推荐 5m**；窗口太短会因样本不足返回空）。
> - **算比例先 sum 再除**：`sum(rate(错误数[5m])) / sum(rate(总数[5m]))`，**不要** `sum(rate(错误数[5m]) / rate(总数[5m]))`（先除后 sum 是错的）。
> - **`histogram_quantile` 先聚合再算**：`histogram_quantile(0.99, sum by (le) (rate(..._bucket[5m])))`，漏掉 `sum by (le)` 会得到错误的分位数。

**第 4 步：加一个 Stat 面板 + 阈值着色**

```
Type     : Stat
Query    : sum(rate(http_requests_total{namespace=~"$ns"}[5m]))
Unit     : req/s（或自定义 suffix:req/s）
Thresholds: Base=green, 100=orange, 500=red   ← 官方默认是 Base=green、80=red
Override : 对主值字段单独设 thresholds
```

**看到什么算对**：大数字随流量变色。

### 3.3 用 provisioning 复现同一个看板

**目录结构**：

```
grafana/
├── provisioning/
│   ├── datasources/
│   │   └── datasources.yaml
│   └── dashboards/
│       ├── provider.yaml
│       └── dashboards/
│           └── business.json
```

**`provisioning/datasources/datasources.yaml`**：

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prom-kube-prometheus-stack-prometheus:9090
    isDefault: true
    editable: false
    jsonData:
      httpMethod: POST
      timeInterval: 15s
```

**`provisioning/dashboards/provider.yaml`**：

```yaml
apiVersion: 1

providers:
  - name: 'business'
    orgId: 1
    folder: '业务看板'
    folderUid: business
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: false
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: false
```

**看板 json 的最小骨架**（普通 dashboard JSON 格式）：

```json
{
  "dashboard": {
    "uid": "biz-overview",
    "title": "业务总览",
    "tags": ["business"],
    "timezone": "browser",
    "schemaVersion": 39,
    "version": 1,
    "refresh": "30s",
    "templating": {
      "list": [
        {
          "name": "ns",
          "type": "query",
          "datasource": { "type": "prometheus", "uid": "prometheus" },
          "query": "label_values(kube_namespace_created, namespace)",
          "multi": true,
          "includeAll": true,
          "refresh": 2
        }
      ]
    },
    "panels": [
      {
        "id": 1,
        "type": "timeseries",
        "title": "QPS",
        "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
        "targets": [
          {
            "refId": "A",
            "datasource": { "type": "prometheus", "uid": "prometheus" },
            "expr": "sum(rate(http_requests_total{namespace=~\"$ns\"}[5m]))",
            "legendFormat": "QPS"
          }
        ],
        "fieldConfig": {
          "defaults": {
            "unit": "reqps",
            "thresholds": {
              "mode": "absolute",
              "steps": [
                { "color": "green", "value": null },
                { "color": "red", "value": 80 }
              ]
            }
          },
          "overrides": []
        }
      }
    ]
  },
  "overwrite": true
}
```

**挂载到容器/K8s**：

```yaml
# K8s Deployment 片段
volumeMounts:
  - name: grafana-provisioning
    mountPath: /etc/grafana/provisioning
  - name: grafana-dashboards
    mountPath: /var/lib/grafana/dashboards
volumes:
  - name: grafana-provisioning
    configMap:
      name: grafana-provisioning
  - name: grafana-dashboards
    configMap:
      name: grafana-dashboards
```

**看到什么算对**（验收清单）：

| 检查 | 怎么验 | 期望 |
|---|---|---|
| 数据源被 provision | `Connections → Data sources` | Prometheus **出现在列表里**，且是 Default |
| 看板被 provision | 打开看板列表 | "业务总览" 出现，**在"业务看板"文件夹下** |
| 变量生效 | 切换 namespace 下拉 | **所有面板跟着变** |
| UI 改不动 | 编辑看板试图保存 | **提示不可编辑**（因为 `allowUiUpdates: false`） |
| **文件即真相** | 改本地 yaml → 等 `updateIntervalSeconds` → 刷新 UI | **看板跟着变** |

> **最后一条是 provisioning 的真正价值**：**改看板不需要登录 Grafana，不需要点 UI，不需要"记得同步给同事"。** 这才是配置即代码。

---

### 3.4 常用运维命令（按场景）

> **本节能做什么**：3.1–3.3 是"把看板建出来"，本节是**Grafana 本身挂了、看板不显示、插件丢了、密码忘了时**的日常运维命令——按场景归类，每条配真实例子。**Grafana 排障的肌肉记忆就来自这一张表。**

### ① CLI 运维（grafana-cli：装插件、改密码、备份）

```bash
grafana-cli plugins list-remote | grep -i mysql        # 看远程仓库有哪些插件可装
grafana-cli plugins install grafana-piechart-panel      # 装插件（装完要重启 grafana）
grafana-cli plugins ls                                 # 看本地已装插件
grafana-cli admin reset-admin-password 'NewPass123'    # 忘 admin 密码时重置（救火第一招）
grafana-cli backup create                              # 备份（部分版本/企业版支持）
```

> **实战价值**：`grafana-cli admin reset-admin-password` 是**忘了 admin 密码唯一的官方解法**，比改数据库安全；插件装完必须重启 Grafana 进程才生效——很多人"装了不显示"就是没重启。

### ② 数据库排障（Grafana 的真相在 grafana.db 这个 sqlite 里）

```bash
sqlite3 /var/lib/grafana/grafana.db ".tables"                                  # 看库里有哪些表（dashboard / user / session ...）
sqlite3 /var/lib/grafana/grafana.db "SELECT slug,title FROM dashboard;"        # 查看板有没有写进库
sqlite3 /var/lib/grafana/grafana.db "SELECT login,is_admin FROM user;"          # 查用户/权限
ls -lh /var/lib/grafana/grafana.db                                             # 库文件在不在、多大
```

> **实战价值**：Grafana 是**有状态**的——看板、用户、数据源权限全在 `grafana.db`（sqlite）。**迁移/恢复就是拷这个文件**；看板"建了找不到"先看 dashboard 表里有没有行。这是 §2.11 provisioning 之外另一个真相源。

### ③ API 调用（不用 UI 也能查/建一切）

```bash
curl -s http://localhost:3000/api/health                                                  # 健康检查（探针用）
curl -s -u admin:admin http://localhost:3000/api/datasources                              # 列数据源（确认数据源连得上没）
curl -s -u admin:admin http://localhost:3000/api/dashboards/uid/<UID>                    # 取某个看板 JSON
curl -s -u admin:admin -X POST -H 'Content-Type: application/json' \
  --data-binary @dash.json http://localhost:3000/api/dashboards/db                        # 用 API 导入看板（配置即代码）
curl -s -u admin:admin http://localhost:3000/api/org/users                              # 看组织内用户
```

> **实战价值**：`/api/health` 比 ping 端口更可靠——它确认应用真的起来了；`/api/datasources` 能确认"数据源配置在不在、URL 对不对"，是看板不显示数据时第一道排查。**用 API 导入看板 = provisioning 的另一种形态，面试常问"怎么不点 UI 批量建看板"。**

### ④ 数据源与 provisioning 校验

```bash
curl -s -u admin:admin http://localhost:3000/api/datasources | grep -o '"name":"[^"]*"'   # 列已配数据源名
curl -s -u admin:admin http://localhost:3000/api/datasources/name/Prometheus             # 按名查单个数据源
ls /etc/grafana/provisioning/dashboards/ /etc/grafana/provisioning/datasources/          # provisioning 文件在不在
grafana-server -config /etc/grafana/grafana.ini -check-config                            # 校验 grafana.ini
```

> **实战价值**：provisioning 看板不出现，**先确认 yaml 文件在 provisioning 目录里、且 `updateIntervalSeconds` 之后刷新了 UI**（§3.3）。`grafana-server -check-config` 能在启动前挡掉 ini 语法错。

### ⑤ 进程、端口、日志（最外层排障）

```bash
systemctl status grafana-server          # 服务状态（起没起、有没有自动重启）
systemctl restart grafana-server          # 装完插件/改完 ini 后重启
ss -ltnp | grep 3000                      # 端口在不在
journalctl -u grafana-server -n 100 --no-pager   # 看最近 100 行日志（插件加载失败、DB 锁都在这里）
```

> **实战价值**：Grafana 起不来，十有八九在日志里——`journalctl -u grafana-server` 看"DB 锁 / 插件不兼容 / 端口被占"。**日志是 Grafana 排障的最后真相，但往往是第一入口。**

### ⑥ 通用：配置与备份纪律

```bash
grep -E '^\[|path|http_port' /etc/grafana/grafana.ini | head    # 看关键配置（端口、路径、DB 位置）
cp /var/lib/grafana/grafana.db /backup/grafana-$(date +%F).db   # 备份数据库（有状态组件必做）
grafana-cli backup create                                       # 或用官方备份命令
```

> **实战价值**：Grafana 是有状态服务，**升级/迁移前先 `cp grafana.db`**。面试被问"Grafana 怎么备份"——答"拷 grafana.db + 导出 provisioning 文件"，不是"重新点一遍 UI"。

---

### 3.5 运维排障实战（场景串命令）

> **把 3.4 的命令用起来**：三个最典型的 Grafana 故障，按"现象 → 敲的命令 → 怎么读"走一遍。**都建议在本地 / 测试 Grafana 上练，别动生产。**

### 场景 A：看板打开了，但面板全是 "No data"

```bash
curl -s -u admin:admin http://localhost:3000/api/datasources | grep -o '"name":"[^"]*"'   # 确认数据源在
sqlite3 /var/lib/grafana/grafana.db "SELECT url FROM datasource;"                         # 看数据源指向的 URL
curl -s http://<数据源URL>/api/v1/query?query=up                                         # 直连后端验数据在不在
# 典型：数据源 URL 写错 / 选了错的数据源类型 / 时间范围不匹配
```

> **读法**：面板 "No data" **先确认数据源配置对不对**（`/api/datasources` + 数据库查 url），再直连后端（`curl` 打 Prometheus/Loki）确认数据本身有。Grafana 不存数据，它只是代理——"没数据"的锅 90% 在后端或数据源配置。

### 场景 B：装了插件，看板里选不到 / Grafana 起不来

```bash
grafana-cli plugins ls                          # 确认插件确实装上了
journalctl -u grafana-server -n 50 --no-pager   # 看是不是插件版本不兼容导致启动失败
systemctl restart grafana-server                # 装完必须重启
ls /var/lib/grafana/plugins/                    # 插件文件在不在磁盘上
```

> **读法**：Grafana 启动失败且日志里有 plugin 相关报错，**多半是插件版本与 Grafana 大版本不兼容**——卸掉重装匹配版本。装完不显示，先看 `plugins ls` 在不在、再确认重启过没。

### 场景 C：忘了 admin 密码 / grafana.db 损坏

```bash
grafana-cli admin reset-admin-password 'NewPass123'   # 重置密码（首选，不动库）
sqlite3 /var/lib/grafana/grafana.db ".tables"         # 若重置仍失败，查库是否损坏
cp /backup/grafana-$(date +%F).db /var/lib/grafana/grafana.db   # 库坏了从备份恢复（会丢备份后的改动）
systemctl restart grafana-server
```

> **读法**：密码忘 → `grafana-cli admin reset-admin-password`（官方解法，不要手改库）；库损坏 → 从备份的 `grafana.db` 恢复，**这是为什么要备份 grafana.db（见 ⑥）**。两者都印证了"Grafana 是有状态的，真相在 grafana.db"。

> **三个场景的共同心法**：**Grafana 不存数据，先分清"锅在后端/数据源"还是"锅在 Grafana 自身"**。分清了，A 是后端问题、B/C 是 Grafana 自身问题——定位快一倍。

---

## 4. 逐字段表

### 4.1 Dashboard Provisioning yaml 关键字段表

**（一）数据源 provisioning（`provisioning/datasources/*.yaml`）**

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion` | int | ✅ | 配置版本，官方示例恒为 `1` | 写别的 → **整个文件不加载，静默无数据源** |
| `datasources[]` | list | ✅ | 数据源列表 | 缺这个键 → 文件里什么都不生效 |
| `.name` | string | ✅ | 面板引用名 | **改名字 = 所有引用它的面板断链** |
| `.type` | string | ✅ | 数据源类型（`prometheus`/`loki`/`elasticsearch`/`mysql`…） | 拼错 → **该数据源不出现** |
| `.access` | enum | ✅ | `proxy`（Server）/ `direct`（Browser） | 设成 direct → **浏览器直连，跨域失败 + 凭据暴露给前端** |
| `.url` | string | ✅ | 后端地址含端口 | 写错 → **Save&Test 失败**，面板全空 |
| `.uid` | string | | 稳定 UID，不指定则 Grafana 生成 | **不指定 → 每次 provision UID 可能变**，看板 json 里的 `datasource.uid` 引用断链 |
| `.orgId` | int | | 组织 ID，**默认 1** | 写到别的 org → 目标组织看不到 |
| `.isDefault` | bool | | 该组织唯一的默认数据源 | **多个数据源都设 true → 行为不确定** |
| `.editable` | bool | | 是否允许 UI 修改 | `false` = **UI 里改不了**（这是保护） |
| `.basicAuth` / `.basicAuthUser` | bool/string | | 基本认证 | 与后端实际要求不符 → 401 |
| `.withCredentials` | bool | | 转发前端凭据 | 配错 → 后端收到错误凭据 |
| `.jsonData` | map | | 转 JSON 存的配置（如 `httpMethod`、`timeInterval`） | 键名错 → **静默不生效**（比如 `timeInterval` 写错，查询间隔用默认） |
| `.secureJsonData` | map | | **加密存储**的敏感项（密码、token） | 写进 `jsonData` → **明文躺在仓库里** |
| `deleteDatasources[]` | list | | 启动时先删这些数据源 | **误删 → 正在用的数据源消失，所有面板断链** |
| `prune` | bool | | 根级。文件里删掉的数据源也自动删 | 设 true 后**删 provisioning 文件 = 删数据源** |
| `httpHeaderName1` / `httpHeaderValue1` | string | | 自定义 HTTP 头（值放 secureJsonData） | 名字写错 → **后端收到空 Authorization** |

**（二）看板 provider provisioning（`provisioning/dashboards/*.yaml`）**

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion` | int | ✅ | 配置版本 | 写错 → 文件不加载 |
| `providers[]` | list | ✅ | provider 列表 | 缺 → 无看板被加载 |
| `.name` | string | ✅ | provider 唯一名 | 重复 → **后一个覆盖前一个** |
| `.type` | enum | | provider 类型，**默认 `file`** | 写错类型 → **路径被忽略** |
| `.folder` | string | | 看板所在文件夹名 | **不设 → 看板装到根目录**（官方原文） |
| `.folderUid` | string | | 文件夹 UID，未指定则自动生成 | 与 `foldersFromFilesStructure` 同时设 → **冲突**（官方要求此时必须取消这两个字段） |
| `.orgId` | int | | 组织 ID，**默认 1** | 写到别的 org → 目标组织看不到 |
| `.disableDeletion` | bool | | 禁止删除看板 | 误设 true → **从文件删看板也删不掉** |
| `.updateIntervalSeconds` | int | | 多久扫描一次变更（官方示例 10 / 30） | 设得太大 → **改了文件要等很久才生效**，误以为没生效 |
| `.allowUiUpdates` | bool | | 允许 UI 改 provisioned 看板（官方示例 false） | 设 true → **人在 UI 上改了，文件还是旧的，下次扫描被覆盖**（改了就白改） |
| `options.path` | string | ✅（`type: file` 时） | 看板 json 所在目录 | **目录不存在 → 静默不加载，不报错** |
| `options.foldersFromFilesStructure` | bool | | 用磁盘目录结构生成 Grafana 文件夹 | 设 true 时**必须取消 `folder`/`folderUid`**；嵌套上限 4 层 |

**（三）看板文件本身的两个格式**

| 格式 | 外层结构 | 什么时候用 |
|---|---|---|
| **普通 dashboard JSON** | `{ "dashboard": {...}, "folderUid": "...", "overwrite": true }` | 常规看板（`overwrite: true` 让重复导入能覆盖） |
| **Kubernetes 资源格式** | `kind: Dashboard` / `apiVersion: dashboard.grafana.app/v1` / `metadata.name` / `spec.title` / `spec.panels[]` | **官方原文：provision dashboards v2 / dynamic dashboards 必须用这个格式** |

### 4.2 Panel 类型对照表

| Panel 类型 | 数据形态 | 必填选项 | 关键选项 | 最容易踩的坑 |
|---|---|---|---|---|
| **Time series** | 时序（多序列） | 无 | Unit、Thresholds、Legend、Draw style（线/面积/柱） | **序列太多挤成一团** → 用 Transformation 过滤或换 Heatmap |
| **Stat** | 单值（可带 sparkline） | 无 | Unit、Thresholds、Color scheme、Value mappings、**Override** | **No value 默认显示 `-`**，不是 0 —— 误以为"没数据=0" |
| **Gauge** | 单值 | 无 | Min、Max、Thresholds、Show threshold labels | **没有 Min/Max 时按数据自动算**，面板会随数据跳动，视觉极差 |
| **Bar gauge** | 单值或多值 | 无 | Display mode、Min、Max、Thresholds | 多值时**颜色按值不按位置**，容易误读 |
| **Table** | 表格 | 无 | Columns、Cell display、**Sort by**、**Transformations** | **Prometheus 宽表直接塞进来会看不懂** → 用 Labels to fields |
| **Heatmap** | 二维矩阵 | 无 | Bucket / Y axis、Legend、**Prepare time series** | **必须先把数据转成"桶×时间"矩阵**，否则是空的 |
| **Logs** | 日志行 | 无 | 显示顺序、换行、详情列 | **必须有 Loki/ES 数据源**，Prometheus 面板选 Logs 没意义 |
| **Alert list** | 告警列表 | 无 | Show annotations、Alert name | **只能列 Grafana 自己能看到的告警**，Prometheus 的告警要靠 Alertmanager 数据源 |
| **Node graph** | 有向图 | 无 | Node、Edge 字段映射 | **需要数据源返回能组成图的字段**，普通时序不行 |
| **State timeline** | 离散状态 | 无 | Value mappings（把数值映射成状态名） | **不配 Value mappings 就是一堆数字**，看不出状态 |
| **Status history** | 周期状态 | 无 | 同上 | 同上 |
| **Pie chart** | 占比 | 无 | Value mappings、Legend、Reduce（只取几个） | **序列太多 pie 会糊** → 先 Reduce |
| **Bar chart** | 分类数据 | 无 | Stacking、Sort by | 与 Table 选错：**比较多个类目用 Bar chart，明细用 Table** |
| **Histogram** | 值分布 | 无 | Bucket size、Min/Max | **bucket 设太宽看不出分布** |
| **Text** | 静态 | 无 | Markdown / HTML 模式 | **Markdown 里放链接不生效 → 要开 HTML 模式** |
| **Alert list / Annotations list / Dashboard list / News** | 组件类 | 无 | 数量、样式 | 属于**门户页**组件，不是数据可视化 |

**Panel 通用必查项（不管什么类型）**：

| 检查 | 为什么 |
|---|---|
| **Unit 设了吗** | 不设 → `1234567` 显示成 `1234567`，不知道是字节还是毫秒 |
| **Thresholds 设了吗** | 不设 → 一条线永远是绿的，**看不出异常** |
| **Legend 格式设了吗** | 默认 `{{label}}` → 图例是 `instance="10.0.0.1:8080"`，太长 |
| **No value 想清楚了吗** | 默认显示 `-`，**分不清"无数据"和"值为 0"** |
| **Override 用了吗** | 一个面板里两个量纲不同的字段（QPS 和错误率）必须用 Override 分开设单位 |

### 4.3 Grafana Alerting 关键字段表

| 字段 | 位置 | 类型 | 含义 | 写错的后果 |
|---|---|---|---|---|
| **Query（查询）** | 告警规则 | list | 一个或多个查询 + 表达式（Reduce / Threshold / Math） | **查错数据源 → 规则永远不满足或永远满足** |
| **Condition（条件）** | 告警规则 | 引用 | 哪个查询结果满足什么条件才触发 | 条件引用了错误的 refId → **不触发或误触发** |
| **Labels** | 告警规则 | map | **决定路由与分组**（severity、team） | **不加 severity → 所有告警进同一个通知策略** |
| **Annotations** | 告警规则 | map | **决定通知内容**（summary、description、runbook_url） | 不写 → **收到告警不知道该干什么**（告警疲劳的头号来源） |
| **Evaluation group（评估组）** | 规则所属 | — | 决定**求值间隔**与重试策略 | 间隔太长 → 告警延迟到分钟级 |
| **Contact point** | 通知侧 | 引用 | 通知发去哪 | — |
| **Notification policy（通知策略）** | 通知侧 | 树 | 按 label 匹配决定走哪个 contact point、要不要延迟 | **策略树里没匹配到 → 走 default**（可能 default 是个没人看的群） |
| **Inhibition rules（抑制规则）** | 通知侧 | list | 上游告警 firing 时抑制下游告警 | **配反了 → 该报的没报** |
| **Silences（静默）** | 通知侧 | list | 临时屏蔽，到期自动恢复 | **忘记设 endsAt → 告警永久静默** |
| **Notification template** | contact point 内 | 模板 | 自定义消息格式 | 引用不存在的标签 → **渲染成空** |

> **labels vs annotations 这条区分必须讲清**：**labels 决定"告警去哪"（路由、分组、抑制匹配），annotations 决定"通知里写什么"**。写反了的后果是：把 `summary` 写进 labels → 路由匹配不上，告警发不出去；把 `severity` 写进 annotations → **所有告警都进同一个通知策略**，分级失效。

---

## 5. 横向对比

### 5.1 Grafana vs Prometheus 内置 UI

| 维度 | Prometheus 内置 UI | Grafana |
|---|---|---|
| **定位** | **调试器**：验证 PromQL、看 target 健康 | **生产看板**：给值班、业务方、领导看 |
| **查询能力** | 单表达式为主 | 一个面板多查询、可做 Transformation |
| **时间范围** | 固定几档 | 完全自定义、URL 可分享 |
| **模板变量** | **没有** | **有**（8 种类型） |
| **跨数据源** | **没有**（只有自己的数据） | **有**（Prometheus + Loki + MySQL 同图） |
| **面板类型** | Graph 表 | 20+ 种可视化 |
| **权限** | 几乎没有 | 组织 / 文件夹 / 看板三级 + RBAC（企业版） |
| **注解** | 无 | **有**（发布事件打点） |
| **告警** | 有 Rules 页面 | 有完整 Grafana Alerting |
| **适合谁** | 写 PromQL 的 SRE | **所有看数据的人** |

**选型结论**：**两者不是替代关系**。Prometheus UI 是你写规则时的"编译器"，Grafana 是交付给别人的"产物"。**生产上禁用在 Prometheus UI 上做值班看板**——它没有变量、没有权限、没法人工打注解。

### 5.2 Grafana Alerting vs Prometheus Alerting

| 维度 | Prometheus Alerting + Alertmanager | Grafana Alerting (GMA) |
|---|---|---|
| **规则存哪** | Prometheus 的 `rule_files` | Grafana 数据库（可 provisioning 到 Git） |
| **能查的数据源** | **只有 Prometheus** | **任意数据源**（Prometheus / Loki / MySQL / ES…） |
| **表达式** | PromQL | PromQL + 多查询 + Reduce/Threshold/Math 组合 |
| **求值进程** | Prometheus | **Grafana** |
| **分组/抑制/静默** | **Alertmanager**（成熟、强大） | Grafana 内置（policy + inhibition + silence） |
| **通知渠道** | Alertmanager 的 receiver（钉钉 webhook / 邮件 / PagerDuty…） | **Contact point（20+ 种集成，钉钉/企微原生）** |
| **高可用** | Alertmanager 支持集群（`--cluster-*`）；Prometheus 侧要指向**全部** Alertmanager 列表，**不能做负载均衡** | 依赖 Grafana 自身部署形态 |
| **Grafana 挂了** | **不影响告警**（这是最大优势） | **GMA 告警静默** |
| **适合的场景** | **指标告警、SLO 体系、多集群统一** | **业务看板级告警、跨数据源关联、需要富格式通知** |

**选型结论（本篇最重要的判断）**：

> **指标类告警走 Prometheus Alerting + Alertmanager；业务看板级、跨数据源的告警走 Grafana Alerting。**
>
> 理由有三条，每条都能落到具体事故：
> 1. **可用性**：Grafana 挂了 GMA 告警就静默了——**监控不能自己成为单点**。指标告警是最不能丢的，因为它对应的是"服务还活着吗"。
> 2. **SLO 体系**：SLO 告警要在 Prometheus 侧统一计算（recording rule 预聚合、多窗口多燃烧率），规则天然属于 Prometheus。Grafana 侧的 SLO 看板是**消费**这些 recording rule 的结果，不是**定义**它们的地方。
> 3. **通知能力**：Grafana 的 Contact point 对钉钉/企微的支持更直接；Prometheus 侧要走 Alertmanager 的 webhook_configs。
>
> **但两套并存必须统一治理**，否则会出现"同一件事两个系统各发一遍"的告警风暴。**治理办法**：① 明确分工边界（指标 → Prometheus，业务 → Grafana），写进文档；② Grafana 告警规则查的数据源用**同一套 recording rule**，不重复计算；③ 通知都收到**同一个值班群**，但用 `source=grafana` / `source=prometheus` 标签区分来源。

**面试怎么答**：
> "我们指标告警走 Prometheus Rule + Alertmanager，因为 Grafana 挂了看板会没，但告警不能跟着没——那是把监控变成单点。Grafana 侧的告警只用来做**业务语义**的判断，比如订单量突降 50%、某个租户的接口成功率掉了，这两类需要跨 Prometheus 和 MySQL 才能算。两套的边界写进了文档，而且 Grafana 告警查的是 Prometheus 里预计算好的 recording rule，不重复算。"

### 5.3 Dashboard as Code（Provisioning）vs 手工点击

| 维度 | 手工在 UI 上点 | Provisioning（配置即代码） |
|---|---|---|
| **改一次看板要多久** | 改完还要**记住怎么告诉同事** | 改文件 → commit → 自动生效 |
| **能不能回滚** | **不能**（Grafana 只存版本快照，不等于版本控制） | `git revert` |
| **能不能 code review** | **不能** | **能**（PR 里讨论"这个面板要不要给业务方看"） |
| **多环境复制** | 手工重建一遍 | 同一份模板改几个变量 |
| **会不会被人改乱** | **会**（生产看板被人随手拖了下图） | `allowUiUpdates: false` **锁死** |
| **审计** | **无迹可查** | git log 就是审计日志 |
| **上手成本** | 零 | 要先设计目录结构 |
| **适合场景** | 一次性排查用的临时看板 | **生产看板（必须）** |

**关键结论**：**生产看板必须走 provisioning**，理由不是"先进"，而是**最后一行**——**UI 上改的东西没人知道，改了也回不去**。这跟 K8s 里"禁止 kubectl edit 线上资源"是同一条纪律。

**中间路线（也推荐）**：**UI 上做原型 → 导出 JSON → 存进 Git → 之后只用 provisioning 部署**。日常调试用 UI 随手做（临时看板不 provision），交付用的进 Git。

### 5.4 模板变量 vs 固定过滤

| 维度 | 固定过滤（写死 `namespace="prod"`） | 模板变量 |
|---|---|---|
| **多环境/多集群** | **每套环境复制一份看板**，N 份维护 | **一份看板 + 一个下拉** |
| **权限隔离** | 所有人看到同一份 | **按登录用户过滤变量选项**（可做数据源级权限） |
| **分享链接** | 只能分享默认视图 | **URL 里带 `var-ns=prod`，点开就是那一屏** |
| **下钻** | 做不到 | **变量链**可逐层收窄 |
| **复杂度** | 零 | 有学习成本（多值要 `=~`、变量引用顺序有依赖） |
| **出错方式** | 忘了切环境，**看了另一个环境的数据还浑然不觉** | **变量取不到值 → PromQL 变成 `namespace=""` → 面板空** |

**选型结论**：**只要这块数据存在"多个取值维度"（环境、集群、namespace、实例），就必须用变量**。固定过滤唯一的适用场景是**单租户单集群的固定看板**。

> **变量的依赖顺序**是另一个坑：**变量 B 的查询里引用变量 A**，那么 A 必须在 B 之前定义，改 A 的值会触发 B 重新查询。**顺序错了 → B 永远拿不到值。**

---

## 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 → 怎么防）

**排障铁律**：面板问题**永远从 Explore 开始**。Explore 出数、面板不出 → 问题在 Grafana 侧；Explore 也不出 → 问题在 PromQL 或后端。

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| 面板显示红色错误块，写着连接被拒 / 超时 | Grafana 活着 | **数据源 URL 错**：多写了路径、少写端口、用了 localhost（容器里的 localhost 是自己不是 Prometheus） | `Connections → Data sources → Save & Test`，改成集群内 DNS 全名 | 加数据源后**必须点 Save & Test 通过再配面板** |
| 面板显示红色错误块，401 / 403 | 数据源配置对 | **鉴权错**：托管 Prometheus 要开 TLS skip verify、要 basic auth 或 token | 补 `basicAuth` / `tlsAuthWithCACert` | 数据源密码放 `secureJsonData`，**不要明文进 Git** |
| 面板全是 "No data"，Explore 也没数 | 查询层 | **PromQL 错**（指标名不存在、时间范围不含数据、label 名对不上） | 在 Prometheus 的表达式浏览器里跑同一条；用 `/api/v1/label/__name__/values` 查真实指标名 | **先在 Prometheus 跑通再贴进 Grafana** |
| 面板全是 "No data"，Explore **有**数 | 变量/转换层 | **变量取到空值**：变量查询写错、或多值变量在 PromQL 里用了 `=` 而不是 `=~` | 变量编辑页 Run query；把 `=` 改成 `=~` | 多值变量**一律用 `=~`** |
| 变量下拉是空的 | 变量层 | 变量查询用了 `label_values(...)` 但数据源选错；或指标名不对 | 换数据源；改成 `label_values(<存在的指标>, <标签>)` | 变量查询用**长期存在的稳定指标**（如 `kube_namespace_created`） |
| 某面板有数据、某面板没有，但 PromQL 一样 | 面板层 | **两个面板不在同一个 dashboard**（变量作用域不同），或某个面板设了独立时间范围 | 确认两个面板在同一个 dashboard；检查面板级时间覆盖 | 统一走 dashboard 级时间范围 |
| 图上数据比实际时间"偏移"几个小时 | 渲染层 | **时区错位**：容器默认 UTC，看板用 UTC，浏览器是 CST（或反过来） | Dashboard settings → Timezone 选 **Browser** | **看板上统一设 `timezone: browser`**，别用默认 |
| **Grafana 重启后，手工改的 dashboard 没了** | — | **这个 dashboard 是 provisioned 的**，`allowUiUpdates: false` + `updateIntervalSeconds` 扫描时**用文件内容覆盖了** | 改 provisioning 里的 json 文件；或临时把 `allowUiUpdates` 设 true 改完再改回 | **生产看板一律 provisioning，UI 改动只走 git** |
| 新加的 dashboard 在 UI 里看不到 | provisioning 层 | **`options.path` 指向的目录不存在 / 不可读 / 文件名不是 `.json`** —— **静默不加载，不报错** | 检查挂载路径与文件权限；看 Grafana 日志里 provisioning 段 | CI 加一步"**provision 后 curl API 确认看板存在**" |
| 看板改了文件但界面不变 | provisioning 层 | `updateIntervalSeconds` 设得太大，等不到下一轮扫描 | 等一轮，或临时调小 | 改完看板**主动在 UI 点 Refresh** 再判断 |
| 改了 provisioning 里的数据源，UI 里还是旧的 | provisioning 层 | **数据源 `uid` 没固定**，重新生成后**看板 json 里的 `datasource.uid` 引用断链** | 显式写 `uid:`，并同步看板 json 的引用 | **数据源一律显式写 `uid`**，用名字引用 |
| 告警只发一次就不发了 | 告警层 | 告警**已 resolved**（条件不再满足），或**被静默（silence）** | 看告警的历史状态；查有没有生效中的 silence | 告警恢复必须发 resolved 通知；**silence 一定要设 endsAt** |
| 告警风暴，一次几百条 | 告警层 | **没配 group_by / 没配抑制**：每个实例一条 | Alertmanager 侧配 `group_by` + `inhibit_rules`；Grafana 侧靠 notification policy 的 group | 告警**按用户影响**写，不按实例写 |
| 钉钉/企业微信消息被截断 | 通知层 | **内置集成只发文本消息**，多条告警拼一起超长 | 改用 **Webhook 中转**，自己转成 markdown / actionCard | 复杂告警一律走 Webhook 中转 |
| 钉钉机器人根本没收到 | 通知层 | 钉钉机器人**安全设置**（关键词 / 签名 / IP 白名单）把消息挡了 | 检查机器人安全设置；消息里必须含关键词 | 配置钉钉机器人时**关键词要和告警模板里的固定词对上** |
| 通知发到了没人看的群 | 路由层 | notification policy 的 default 分支指到了废弃群；或标签没匹配上，走了 default | 逐级检查 policy 树的匹配顺序 | **notification policy 要有明确的 default，且定期核对** |
| Time series 里 Prometheus 的宽表挤成一团 | 渲染层 | Prometheus 返回的是宽表，Time series 按多序列画，序列一多就糊 | ① 用 Transformation 过滤/聚合；② 换 Heatmap + Prepare time series；③ 加聚合再画 | **先问"我要看趋势还是明细"**，别硬塞 |
| Table 面板里的 Prometheus 数据看不懂 | 转换层 | 宽表 + 标签没变成列 | 加 **Labels to fields** transformation | Prometheus → Table 一律配 Labels to fields |
| 图例是一长串 `instance="10.0.0.1:8080"` | 渲染层 | 没设 legend 格式 | Legend format 填 `{{pod}}` 或 `{{instance}}` | 建面板时顺手设 legend |
| 数值单位显示成 `1234567` | 渲染层 | **Unit 没设** | 设 Unit；自定义用 `suffix:req/s` / `currency:CNY` | 建面板模板时把 Unit 当必填项 |
| 时间戳全显示成 1970 年 | 渲染层 | 数据源给的是**秒级时间戳**，Grafana 的时间格式单位期望**毫秒** | 加 **Add field from calculation**（Binary operation，×1000） | 统一在数据源侧输出毫秒；或在 transformation 里固定乘 1000 |
| 告警面板（Alert list）是空的 | 集成层 | Prometheus 的告警在 Alertmanager 里，**Grafana 默认只看得到自己的告警** | 加一个 **Alertmanager 类型数据源**指向 Alertmanager | 需要在 Grafana 看 Prometheus 告警，就配 Alertmanager 数据源 |
| 装了数据源但普通用户看不到 | 权限层 | **OSS 默认组织内任何人都能查任何数据源**；若做了隐藏是权限/组织配置问题 | 检查组织成员与角色 | 敏感数据源放独立组织，或在后端做鉴权 |
| 编辑 provisioned 看板存不进去 | 权限层 | `allowUiUpdates: false`（**这是预期行为**） | 改 provisioning 文件 | **这是保护不是 bug**，别去改配置绕过 |
| 看板 JSON 导入报格式错误 | 格式层 | 导出的 json 外层结构不对（缺 `dashboard` 包裹） | 用 Dashboard → Export → "Export for sharing externally" 或直接用 API 取 `dashboard` 对象 | 统一用 `{"dashboard": {...}, "overwrite": true}` 格式 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `grafana.com/docs/grafana/latest/` | 总览、Grafana 作为可视化层的定位 | ✅ 2026-10-24 |
| S2 | 官方 | `grafana.com/docs/grafana/latest/getting-started/` | 入门主线 | ✅ 2026-10-24 |
| S3 | 官方 | `grafana.com/docs/grafana/latest/dashboards/` | Dashboard / Panel / Row 对象模型原文 | ✅ 2026-10-24 |
| S4 | 官方 | `grafana.com/docs/grafana/latest/datasources/` | 数据源类型清单、Mixed / Dashboard / Grafana 三种特殊数据源 | ✅ 2026-10-24 |
| S5 | 官方 | `grafana.com/docs/grafana/latest/administration/provisioning/` | provisioning 目录结构、datasources / providers / plugins yaml 字段原文 | ✅ 2026-10-24 |
| S6 | 官方 | `grafana.com/docs/grafana/latest/dashboards/variables/` | 变量类型、URL 同步 `var-<name>`、`skipUrlSync` | ✅ 2026-10-24 |
| S7 | 官方 | `grafana.com/docs/grafana/latest/panels-visualizations/visualizations/` | 面板类型完整清单 | ✅ 2026-10-24 |
| S8 | 官方 | `grafana.com/docs/grafana/latest/panels-visualizations/configure-standard-options/` | Unit 与自定义单位语法、时间戳毫秒陷阱 | ✅ 2026-10-24 |
| S9 | 官方 | `grafana.com/docs/grafana/latest/panels-visualizations/configure-thresholds/` | 阈值默认值（Base=green / 80=red / Absolute） | ✅ 2026-10-24 |
| S10 | 官方 | `grafana.com/docs/grafana/latest/panels/transform-data/` | Transformation 清单与行为（Reduce / Labels to fields / Rename by regex 等） | ✅ 2026-10-24 |
| S11 | 官方 | `grafana.com/docs/grafana/latest/dashboards/build-dashboards/annotate-visualizations/` | Annotation 三种方式、Save As 限制、tag 泄露警告 | ✅ 2026-10-24 |
| S12 | 官方 | `grafana.com/docs/grafana/latest/enterprise/access-control/` | 基础角色、RBAC 权限传播规则、`General` 文件夹限制 | ✅ 2026-10-24 |
| S13 | 官方 | `grafana.com/docs/grafana/latest/alerting/` | Grafana Alerting 定位与 GMA/DMA 两类规则 | ✅ 2026-10-24 |
| S14 | 官方 | `grafana.com/docs/grafana/latest/alerting/alerting-rules/` | Grafana-managed vs Data source-managed、DMA 弃用公告 | ✅ 2026-10-24 |
| S15 | 官方 | `grafana.com/docs/grafana/latest/alerting/configure-notifications/manage-contact-points/` | Contact point 集成清单、protected fields、删除约束 | ✅ 2026-10-24 |
| S16 | 官方 | `grafana.com/docs/grafana/latest/alerting/configure-notifications/` | 通知策略、多 Alertmanager 资源隔离 | ✅ 2026-10-24 |
| S17 | 官方 | `grafana.com/docs/grafana/latest/setup-grafana/installation/docker/` | Docker 安装、端口 3000、默认 SQLite 与数据卷 | ✅ 2026-10-24 |
| S18 | 官方 | `prometheus.io/docs/alerting/latest/alertmanager/` | 分组 / 抑制 / 静默 / 高可用语义（对照用） | ✅ 2026-10-24 |
| S19 | 官方 | `prometheus.io/docs/prometheus/latest/getting_started/` | Prometheus 默认 scrape_interval=15s（rate 窗口口径依据） | ✅ 2026-10-24 |
| S20 | 中文 | `developer.aliyun.com` · `cloud.tencent.com/developer` | 中文实践、托管 Grafana 与钉钉机器人接入经验 | ✅ 2026-10-24 |
| S21 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/observe-metrics/` | ACK 托管 Prometheus + Grafana 的生产形态 | ✅ 2026-10-24 |

> **完整来源矩阵见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。**
> **官方无中文版**：`grafana.com/docs/grafana/latest/` 下**没有中文路径**（与 Prometheus / Loki 同理），中文来源只能靠社区与云厂商文档。
> **⚠️ 单源待补**：钉钉机器人 `msgtype` 的具体长度上限、企业微信 markdown 消息的字节上限，官方 Grafana 文档未给出数值，本篇只写"有限制、会截断"这一事实（来自社区实践），**具体字节数 ⚠️ 待验证**。

---

## 8. 自测三问

1. **能说清输入输出吗？**
   输入是**面板的查询配置 + 时间范围 + 变量取值**，输出是**一张渲染好的图**。中间链路是：Grafana 解析 dashboard JSON → 变量插值 → 查数据源配置 → **代替浏览器**去问后端 → 后端返回数据 → Grafana 在内存里做 Transformations → 按 Unit 格式化、按 Thresholds 着色 → 渲染。
   **关键一句**：数据全程不在 Grafana 落地，它只做"查询代理 + 整形 + 渲染"。

2. **能故意改坏再倒推吗？** —— 五个实验（每个都写清改什么、看到什么）：
   ① **把数据源 URL 改成 `http://localhost:9090`** → 点 Save & Test → **看到连接被拒**（容器里 localhost 是 Grafana 自己）→ 倒推出"URL 必须是后端的集群内 DNS"。
   ② **把变量改成 multi-value，PromQL 里的 `namespace="$ns"` 不动** → 面板 **No data** → 倒推出"多值必须用 `=~`"。
   ③ **把看板的 `timezone` 从 `browser` 改成 `utc`** → 看图上的时间点 → **和你的浏览器对不上** → 倒推出时区必须统一。
   ④ **把 `provisioning/dashboards/provider.yaml` 的 `options.path` 改成一个不存在的目录** → 重启 Grafana → **UI 里看板消失了，且没有任何报错** → 倒推出"路径写错是静默失败"。
   ⑤ **在 provisioned 看板的 UI 里改个标题并保存** → 保存被拒 → 或者你把 `allowUiUpdates` 改成 true → 改完 → 等一次扫描 → **你的修改被文件内容覆盖了** → 倒推出"文件才是真相源"。

3. **能画上下游吗？**
   画成：
   ```
   浏览器 ──HTTP──► Grafana ──HTTP──► Prometheus（指标）
                            ├──HTTP──► Loki（日志）
                            ├──SQL───► MySQL（业务数据）
                            └──HTTP──► ES（全文检索日志）
   ```
   然后在每条箭头上标出：**谁存数据、谁算聚合、谁画图、谁发通知**。
   **并标出两个单点**：**Grafana 挂了 → 面板全空、但后端数据还在；Grafana 挂了 → GMA 告警静默、但 Prometheus Rule 的告警不受影响**。

---

**下一篇**：[`L4-Loki-日志管道.md`](L4-Loki-日志管道.md)（Grafana 看得见的另一半数据） · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
