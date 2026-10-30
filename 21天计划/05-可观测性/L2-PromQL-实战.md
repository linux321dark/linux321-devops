# L2 · PromQL 实战（从"能写出来"到"写对、写得快、跑得动"）

> **本篇定位**：整套资料里**最容易写出"看起来对、其实是错"的查询**的一篇。PromQL 的语法不难，坑在**类型**和**顺序**：窗口取小了没数据、比例先算后聚合、`by` 漏写导致维度错乱、histogram 分位数跨实例直接算——**这些都不会报错，只会给你一个错的数字**。读完这篇你会知道每个函数"为什么这么写"以及"顺序为什么不能换"。
> **前置**：`05-可观测性/L1-Prometheus-数据模型与采集`（**必读**：数据模型、指标类型、标签基数是本篇所有陷阱的根源）。
> **学完能做什么**：说清 instant query 与 range query 的适用边界；解释 range vector 与 instant vector 为什么不能混用；写出 p95 延迟、错误率、QPS、饱和度、重启次数、内存增长趋势这六类排障查询；用 recording rule 预计算复杂表达式；写出**带 `for` 的、会收敛的**告警规则。
> **在 21 天里**：D12（Prometheus + PromQL P1）的实战部分，D14（GitOps + 可观测闭环）的规则层。
> **环境/版本基准**：本文所有函数签名、聚合算子语义、运算符匹配规则以 **官方 `prometheus.io/docs/prometheus/latest/querying/`** 为准（写作日期 2026-10-24，各路径实测 200）。**本篇未启动 Prometheus 实例执行查询**，示例查询为可直接复制到表达式浏览器的写法，标注"官方标称"的默认值来自官方文档原文。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L2 PromQL 实战
│
├─ 1. 概念
│   ├─ 1.1 一句话：PromQL 是一个有类型的表达式语言
│   ├─ 1.2 instant query vs range query（各适合什么场景）
│   ├─ 1.3 四种数据类型（instant vector / range vector / scalar / string）
│   ├─ 1.4 为什么 PromQL 这么容易写错：它不报错，它给你错的数
│   └─ 1.5 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 选择器语法：四种匹配运算符 + __name__
│   ├─ 2.2 offset 与 @ 修饰符：查历史某一时刻
│   ├─ 2.3 范围向量 vs 瞬时向量：PromQL 最容易错的地方
│   ├─ 2.4 核心函数逐个讲透（20+ 个，配算例）
│   │   ├─ 2.4.1 rate / irate / increase：速率三兄弟
│   │   ├─ 2.4.2 rate 的窗口至少要 4 个样本（高频陷阱）
│   │   ├─ 2.4.3 counter 重置：rate 会自动处理
│   │   ├─ 2.4.4 *_over_time 家族：区间聚合
│   │   ├─ 2.4.5 absent / absent_over_time：缺失检测
│   │   ├─ 2.4.6 changes / deriv / predict_linear：变化与预测
│   │   ├─ 2.4.7 histogram_quantile：分位数的正确用法
│   │   ├─ 2.4.8 label_replace / label_join：标签改写
│   │   ├─ 2.4.9 topk / bottomk / count_values / sort / sort_desc
│   │   ├─ 2.4.10 vector / scalar：类型转换
│   │   └─ 2.4.11 and / or / unless：集合运算
│   ├─ 2.5 聚合算子：sum by vs sum without
│   ├─ 2.6 多实例聚合的正确写法（先求和再算比例）
│   ├─ 2.7 向量匹配与 group_left / group_right
│   ├─ 2.8 四个黄金信号：RED 与 USE
│   ├─ 2.9 典型排障查询模板（6 类）
│   ├─ 2.10 recording rules：预计算
│   └─ 2.11 alerting rules：for 的作用
│
├─ 3. 动手
│   ├─ 3.1 表达式浏览器的基本操作
│   ├─ 3.2 用 6 个排障查询走一遍
│   ├─ 3.3 验证清单
│   ├─ 3.4 常用运维命令（按场景）
│   └─ 3.5 运维排障实战（场景串命令）
│
├─ 4. 逐字段表
│   ├─ 4.1 函数表（函数 | 类型 | 语义 | 典型用途 | 陷阱）
│   └─ 4.2 排障查询模板表（场景 | 查询 | 读法）
│
├─ 5. 横向对比
│   ├─ 5.1 rate vs irate vs increase
│   ├─ 5.2 sum by vs sum without
│   ├─ 5.3 instant vs range
│   └─ 5.4 recording rules vs 每次看板实时算
│
├─ 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 → 下次怎么防）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 一句话：PromQL 是一个**有类型的**表达式语言

> **PromQL 是一个函数式查询语言，它把时间序列当数据流来算：选择 → 变换 → 聚合 → 比较。**

它的语法糖很多，但骨架只有四步：

```
选择器（选哪些序列）
   │  http_requests_total{job="api"}
   ▼
函数/算子（怎么变换）
   │  rate(...[5m])
   ▼
聚合（怎么合并）
   │  sum by (job) (...)
   ▼
比较/组合（怎么筛选）
   │  > 0.05
```

**"有类型"这三个字是本篇的全部关键**。PromQL 官方文档明确列了四种求值结果类型（`querying/basics/` 原文）：

> - **Instant vector** - a set of time series containing a single sample for each time series, all sharing the same timestamp
>   （瞬时向量：一组时间序列，每条含**一个**样本，共享同一时间戳）
> - **Range vector** - a set of time series containing a range of data points over time for each time series
>   （范围向量：一组时间序列，每条含**一段时间内**的数据点）
> - **Scalar** - a simple numeric floating point value
>   （标量：一个浮点数）
> - **String** - a simple string value; currently unused
>   （字符串：目前未使用）

**官方还明确了一个限制**：

> "For instant queries, any of the above data types are allowed as the root of the expression. **Range queries only support scalar-typed and instant-vector-typed expressions.**"
> （区间查询只支持标量类型和瞬时向量类型的表达式。）

### 1.2 instant query vs range query（各适合什么场景）

**官方对两者的定义（`querying/basics/` 原文）**：

> "When you send a query request to Prometheus, it can be an **instant query**, evaluated at one point in time, or a **range query** at equally-spaced steps between a start and an end time. **PromQL works exactly the same in each case; the range query is just like an instant query run multiple times at different timestamps.**"
> （区间查询"就像在多个时间戳上重复执行瞬时查询"。）

**HTTP API 的差异**（官方 `querying/api/`）：

| 维度 | instant query | range query |
|---|---|---|
| **端点** | `/api/v1/query` | `/api/v1/query_range` |
| **关键参数** | `query` + `time`（可选，缺省=服务器当前时间） | `query` + `start` + `end` + `step` |
| **返回类型** | `vector` / `scalar` / `string` | `matrix` |
| **结果结构** | `value: [unix_time, "sample_value"]` | `values: [[unix_time, "v"], ...]` |
| **UI 标签页** | **Table** 标签页 | **Graph** 标签页 |
| **官方示例** | `curl 'http://localhost:9090/api/v1/query?query=up&time=2015-07-01T20:10:51.781Z'` | `curl 'http://localhost:9090/api/v1/query_range?query=up&start=...&end=...&step=15s'` |

**各自适合什么场景（这是本节要答的问题）**：

| | **instant query（瞬时查询）** | **range query（区间查询）** |
|---|---|---|
| **回答的问题** | "**现在**是多少？" | "过去这段时间**怎么变的**？" |
| **典型用途** | ① **告警规则求值**（每 `evaluation_interval` 求一次当前值）② **API 探活/巡检脚本**（curl 拿当前值做判断）③ 排查时看"此刻"的瞬时状态 | ① **画图**（Grafana 面板）② **看趋势**（判断是尖峰还是持续劣化）③ **对比两个时间段** |
| **时间点数量** | 1 个 | `start` 到 `end` 之间按 `step` 等距 |
| **`step` 的作用** | 无 | **决定数据点密度**——`step` 越小点越多、越精确，但**服务端计算量线性增长** |
| **典型返回** | 一个数字 + 一组标签 | 一条时间序列上的多个点 |

> **实战含义（高频）**：
> **告警规则天生是 instant query**——它问的是"**此刻**这个条件成立吗"，
> 所以告警表达式最后一定要落到一个**当前值**上（`> 0.05`、`== 0`），
> 而不是留一个范围向量。
> **看板天生是 range query**——它要画的是"过去 1 小时的曲线"。
>
> **一个易错点**：**`rate(x[5m])` 在两种查询里都是瞬时向量**，
> 因为 `rate()` 把范围向量**压扁成了瞬时向量**（每条序列一个数）。
> 所以 `rate(x[5m])` 可以直接做 range query 的根表达式——
> 它在每个 `step` 时刻各算一次 5 分钟速率，画出来就是速率曲线。

### 1.3 四种数据类型：为什么"类型"是 PromQL 的第一道坎

**官方的四种类型，落到实际写查询上是这样对应的**：

| 类型 | 长得什么样 | 怎么产生 | 谁能当根表达式（range query） |
|---|---|---|---|
| **Instant vector**（瞬时向量） | `{metric="x", label="v"} 42` | 任何选择器、`rate()`、`sum()` 等函数的结果 | ✅ |
| **Range vector**（范围向量） | `{metric="x"} 42 43 44`（多个点） | **只有 `xxx[5m]` 这种带方括号的范围选择器** | ❌ |
| **Scalar**（标量） | `42` | 字面量、`scalar()`、`time()` | ✅ |
| **String**（字符串） | `"abc"` | 字面量（目前基本不用） | ✅（官方说 range query 支持 scalar 和 instant vector，string 未在允许列表中显式列出） |

**类型不匹配会发生什么**——官方文档在运算符页描述了行为（**注意：官方文档页未给出错误消息原文**，只描述行为与 annotation 等级）：

| 不匹配 | 官方描述的行为 | annotation 等级 |
|---|---|---|
| 聚合时混入 float 与 histogram 样本 | "An aggregation of a mix of both is **invalid**, resulting in the **removal of the corresponding vector element** from the output vector" | **warn** |
| scalar 与 histogram 做无效算术组合 | "there is **no result** and the corresponding element is removed from the resulting vector" | **info** |
| float 与 histogram 比较 | "Matches between a float sample and a histogram sample are **invalid**, and the corresponding element is removed" | **info** |

> **⚠️ 关键认知（官方文档行为描述）**：
> **PromQL 对类型不匹配的处理方式是"静默移除该元素 + 挂一个 annotation"，而不是干脆报错。**
> 这意味着：**一条写错的查询很可能"跑成功了、返回了数据、但数据是缺的"**。
> **这就是为什么必须自己知道每个函数的输入类型，而不能靠"跑一下看看"。**
>
> **典型报错（社区/官方 issue 常见形态，非本篇实测）**：
> - 范围向量直接参与二元运算：`parse error: unexpected range vector` 或 `binary expression must contain only scalar and instant vector types`
> - 聚合了范围向量：`parse error: unexpected range vector` / `vector cannot be aggregated with`
> - 这些是社区里最常见的 PromQL 报错形态；**具体文案随版本变化，以你本地 Prometheus 返回的原文为准**。

### 1.4 为什么 PromQL 这么容易写错：它不报错，它给你错的数

**这一节是本篇存在的理由。**

对比两种系统的失败模式：

| 系统 | 写错了会怎样 |
|---|---|
| **编译型 / 强类型语言**（Go、Rust、TypeScript） | **编译期报错**，根本跑不起来 |
| **PromQL** | **大部分写错都能跑通**，只是结果不对 |

**PromQL 的三类"静默错误"**（本篇会逐个拆解）：

| # | 静默错误 | 现象 | 本篇对应节 |
|---|---|---|---|
| 1 | **比例先算后聚合** | 错误率算出来比真实值大一截 | 2.6 |
| 2 | **`by` 漏写** | 维度错乱（返回几千条而不是一条） | 2.5 |
| 3 | **histogram 分位数跨实例直接算** | P99 算出来偏小 | 2.4.7 |

**共同的解药只有一个**：

> **写完查询，问自己三个问题**：
> ① **分子分母各自聚合到同一维度了吗？**（不能一个聚合一个不聚合）
> ② **`by` 写的维度是"我真正想要的输出粒度"吗？**（多一个维度 → 返回 N 条；少一个 → 丢信息）
> ③ **直方图分位数前面是不是 `sum by (le, ...)`？**（不是就错）

### 1.5 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| instant query | 瞬时查询 | 求**一个**时刻的值 |
| range query | 区间查询 | 在 start~end 之间按 step 求值 |
| step | 步长 | 区间查询的求值间隔 |
| instant vector | 瞬时向量 | 每条序列一个点 |
| range vector | 范围向量 | 每条序列一段区间内的多个点 |
| scalar | 标量 | 一个数 |
| selector | 选择器 | `metric{label="v"}` |
| matcher | 匹配器 | `=` `!=` `=~` `!~` 四种 |
| modifier | 修饰符 | `offset` / `@` / `bool` / `on` / `ignoring` / `group_left` / `group_right` |
| aggregation operator | 聚合算子 | `sum` `avg` `min` `max` `count` `stddev` `stdvar` `topk` `bottomk` `quantile` `count_values` `group` |
| vector matching | 向量匹配 | 左右两侧按标签配对的方式 |
| subquery | 子查询 | `<instant_query>[range:resolution]` |
| staleness | 陈旧标记 | 目标不再暴露某序列时打的标记 |
| lookback delta | 回溯窗口 | 取"最近样本"的默认时间范围，官方默认 **5m** |
| recording rule | 记录规则 | 预计算并落成新时间序列 |
| alerting rule | 告警规则 | 条件满足持续 N 分钟后产生告警 |

---

## 2. 机制

### 2.1 选择器语法：四种匹配运算符 + `__name__`

**官方对四种匹配运算符的定义（`querying/basics/` 原文）**：

| 运算符 | 官方定义 | 语义 |
|---|---|---|
| `=` | "Select labels that are **exactly equal** to the provided string." | 精确相等 |
| `!=` | "Select labels that are **not equal** to the provided string." | 精确不等 |
| `=~` | "Select labels that **regex-match** the provided string." | 正则匹配 |
| `!~` | "Select labels that **do not regex-match** the provided string." | 正则不匹配 |

**⚠️ 三个必须记住的官方细节**：

**① 正则是完全锚定的（fully anchored）**

官方原文：
> "**Regex matches are fully anchored.** A match of `env=~"foo"` is treated as `env=~"^foo$"`."

这意味着：

```
env=~"foo"       只匹配 "foo"，不匹配 "foobar"、不匹配 "myfoo"
env=~"foo.*"     匹配 "foobar"、"foobaz"，不匹配 "myfoo"
env=~".*foo.*"   匹配任何含 foo 的
```

**这是"匹配不到"类问题的头号原因**——不是标签值变了，是正则少写了 `.*`。

**② 空标签值的匹配器会同时选中"根本没这个标签"的序列**

官方原文：
> "Label matchers that match empty label values also select all time series that **do not have the specific label set at all**."

官方给的例子：

```
# 数据集：
http_requests_total
http_requests_total{replica="rep-a"}
http_requests_total{replica="rep-b"}
http_requests_total{environment="development"}

# http_requests_total{environment=""} 会匹配并返回：
http_requests_total
http_requests_total{replica="rep-a"}
http_requests_total{replica="rep-b"}

# 并排除：
http_requests_total{environment="development"}
```

> **实战含义**：`{environment=""}` 不是"找空标签"，而是"找**没有 environment 标签**的"。

**③ 同一标签可以写多个匹配器，但必须全部满足**

官方原文：
> "Multiple matchers can be used for the same label name; **they all must pass** for a result to be returned."

官方例子：`http_requests_total{replica!="rep-a",replica=~"rep.*"}` → 只返回 `{replica="rep-b"}`。

**`__name__`：用标签的方式匹配指标名**

官方原文：
> "Label matchers can also be applied to metric names by matching against the internal `__name__` label. For example, the expression `http_requests_total` is equivalent to `{__name__="http_requests_total"}`."

```promql
{__name__=~"job:.*"}        # 所有以 job: 开头的指标（recording rule 的产物）
{__name__=~".*_total"}      # 所有 counter
{__name__=~"http_.*_bucket"} # 所有 classic histogram 的桶
```

**⚠️ 一个必须知道的限制（官方原文）**：

> "The metric name must not be one of the keywords `bool`, `on`, `ignoring`, `group_left` and `group_right`. The following expression is illegal: `on{}`"

**解法（官方给出）**：
```promql
{__name__="on"}   # Good!
```

**⚠️ 另一条硬约束（官方原文）**：

> "Vector selectors must either specify a name or at least one label matcher that **does not match the empty string**. The following expression is illegal: `{job=~".*"}`"

```promql
{job=~".*"}              # Bad!  会匹配空 job → 非法
{job=~".+"}              # Good!
{job=~".*",method="get"} # Good!  有一个不匹配空的匹配器
```

**选择器写法的四条实战规则**：

| 规则 | 正例 | 反例（会踩坑） |
|---|---|---|
| 正则**总是**要想到锚定 | `{env=~"prod.*"}` | `{env=~"prod"}` 匹配不到 `prod-eu` |
| **用 `=~` 而不是多个 `=`** | `{code=~"5.."}` | `{code="500",code="503"}` —— 同名多匹配器是 AND，匹配不到 |
| **区分"没有标签"和"标签为空"** | `{environment=""}` 选中的是没设该标签的 | 想找"值为空串"的，用 `{environment=~"^$"}` |
| **优先用精确匹配** | `{job="api-server"}` | `{job=~".*api.*"}` 正则匹配更慢也更危险 |

**⚠️ 一个必须澄清的误解：PromQL 没有 `in` / `notin` 运算符**

很多中文教程和面试题里会写"`in` / `notin`"，**但 PromQL 官方并没有这两个运算符**。

**官方 `querying/operators/` 页列出的逻辑/集合二元运算符只有三个**（原文）：

> "These logical/set binary operators are only defined between instant vectors:
> - `and` (intersection)
> - `or` (union)
> - `unless` (complement)"

**`in` / `notin` 是 SQL、InfluxQL 一类语言里的语法，不是 PromQL 的**。
在 PromQL 里，"标签值属于某个集合"这件事有三种正确写法：

| 需求 | **PromQL 正确写法** | 说明 |
|---|---|---|
| **标签值在枚举集合内** | 用正则的 alternation（见下方代码块） | **首选**：一次匹配搞定 |
| **"在集合内"且要跨标签判断** | `x and on(label) y` | 用 `and` 做交集（但**要求两侧标签集按 `on()` 对齐**） |
| **"不在集合内"** | 用 `!~` 取反（见下方代码块） | 与 `=~` 配对使用 |

**枚举集合的两种写法（正则里的 alternation 竖线需要转义）**：

```promql
# 标签值属于 {200, 201, 204} 之一
http_requests_total{code=~"200\|201\|204"}

# 标签值不属于上面这个集合
http_requests_total{code!~"200\|201\|204"}
```

**两个必须知道的官方细节**：

| 细节 | 官方原文 | 实战影响 |
|---|---|---|
| **正则里的 alternation 要写在对的位置** | 官方示例用 `environment=~` 配三个环境名（见上方代码块），另加 `method!="GET"` | 因为**正则完全锚定**，`=~` 匹配的是**整串**而不是"包含" |
| **`and`/`or`/`unless` 用的是"完全相同的标签集"** | 官方原文："with **exactly matching label sets**" | 所以 `sum by (a) (x) and sum by (b) (y)`（a≠b）**匹配不上**，返回空 |

**官方给的运算符优先级（`querying/operators/` 原文，从高到低）**：

```
1. ^
2. *, /, %, atan2
3. +, -
4. ==, !=, <=, <, >=, >
5. and, unless
6. or
```

官方补充："Operators on the same precedence level are **left-associative**... however `^` is **right associative**."

> **这条优先级在写复杂表达式时经常被忽略**：
> `a + b * c` = `a + (b * c)`（乘法优先）；
> 但 `a == b and c == d` 里，**比较先于 `and`**，所以它等于 `(a == b) and (c == d)`——**这是对的**。
> **真正会踩坑的是 `or` 的优先级最低**：
> `x > 1 or y > 1` 没问题，但 `a > 1 and b > 1 or c > 1` 会被解析成
> `(a > 1 and b > 1) or c > 1`（`and` 高于 `or`）——**这通常正是你想要的**，
> 但**如果你以为它是从左到右，就会在复杂表达式里出错**。
> **保险做法：多层组合一律加括号。**

### 2.2 offset 与 `@` 修饰符：查历史某一时刻

**两个修饰符都能"把查询时间挪到过去"，但语义完全不同。**

| 维度 | `offset` | `@` |
|---|---|---|
| 参数形态 | **相对时长**：`offset 5m` / `offset 1w` / `offset -1w` | **Unix 时间戳**（浮点字面量）：`@ 1609746000` |
| 基准 | 相对**查询求值时刻** | **绝对**时间点 |
| 官方示例 | `http_requests_total offset 5m` | `http_requests_total @ 1609746000` |
| 能否用于区间查询 | ✅ | ✅ |
| 特殊值 | 无 | **`start()` / `end()`** —— 区间查询里解析为区间起止（所有 step 相同），瞬时查询里都解析为求值时刻 |

**官方对 `@` 的完整说明（`querying/basics/` 原文）**：

> "The `@` modifier allows changing the evaluation time for individual instant and range vectors in a query. The time supplied to the `@` modifier is a **Unix timestamp** and described with a float literal."

**⚠️ 官方反复强调的一条硬约束（写错就是语法错误）**：

> "Note that the `@` modifier **always needs to follow the selector immediately**, i.e. the following would be correct:
> `sum(http_requests_total{method="GET"} @ 1609746000)` // GOOD.
> While the following would be *incorrect*:
> `sum(http_requests_total{method="GET"}) @ 1609746000` // INVALID."

**`offset` 完全一样**（官方原文）：
> `sum(http_requests_total{method="GET"} offset 5m) // GOOD.`
> `sum(http_requests_total{method="GET"}) offset 5m // INVALID.`

> **记法**：**修饰符必须紧跟选择器**（贴着那个 `}` 或指标名），
> 写在聚合函数外面是**语法错误**。

**`@` 与 `offset` 的交互（官方原文）**：

> "It works with the `offset` modifier where **the offset is applied relative to the `@` modifier time**. The results are the same irrespective of the order of the modifiers."

```promql
http_requests_total @ 1609746000 offset 5m    # 与下面等价
http_requests_total offset 5m @ 1609746000
```

**offset 与 @ 的取舍（这是本节要答的问题）**：

| 场景 | 用哪个 | 理由 |
|---|---|---|
| "**一小时前**这个值是多少" | `offset 1h` | 相对表述，**可读性最好**，时间基准跟着查询时刻走 |
| "**发布那天的**某个具体时刻" | `@ 1609746000` | 绝对时间点，**可复现**——不管什么时候跑，结果都一样 |
| "**和昨天同一时刻**对比" | `offset 1d` 或 `offset -1d` | 官方特别说明：**负 offset 可以向未来看**（"this allows a query to look ahead of its evaluation time"） |
| "**这个时间窗**的速率" | `rate(x[5m] offset 1w)` | offset 对范围向量同样生效 |
| **做"同比/环比"对比** | 两个查询各带一个 `offset` | 例：`x offset 1h` vs `x` |

> **实战建议**：**日常排查用 `offset`**（一眼看懂），**做数据回溯/复现用 `@`**（结果可复现）。
> `@` 的另一个隐藏优势：**它是绝对锚点，写进 dashboard 的 annotation 或排查记录里，别人明天跑出来结果一样**；
> `offset` 是相对的，别人明天跑就变成"明天的一小时前"了。

**子查询（subquery）——把"聚合结果"再当序列用**：

官方语法：
> `<instant_query> '[' <range> ':' [<resolution>] ']' [ @ <float_literal> ] [ offset <float_literal> ]`

```promql
# 对"过去 1 小时的 5 分钟速率"再做 1 小时的平均 → 得到 1 小时粒度的平滑曲线
avg_over_time(rate(http_requests_total[5m])[1h:5m])
```

> **子查询的用途**：**给"已经聚合过的瞬时向量"加上时间维度**。
> 普通函数只能作用于范围向量，而 `rate()` 的结果是瞬时向量——
> 要对它做"区间平均"，必须用子查询。
> **分辨率（`[1h:5m]` 里的 `5m`）决定子查询的采样间隔**，越细越准也越慢。

### 2.3 范围向量 vs 瞬时向量：PromQL 最容易错的地方

**这一节请当成最高频考点准备。**

**两种向量的产生方式（官方语法）**：

| 类型 | 产生方式 | 例子 | 每条序列有几个点 |
|---|---|---|---|
| **Instant vector** | 裸选择器 | `http_requests_total` | **1** 个 |
| **Range vector** | 选择器**加方括号** | `http_requests_total[5m]` | **多个**（窗口内的所有样本） |

**官方对范围向量区间的精确定义（`querying/basics/` 原文）**：

> "The range is a **left-open and right-closed** interval, i.e. samples with timestamps coinciding with the **left boundary are excluded**, while samples coinciding with the **right boundary are included**."

```
        左边界(不含)              右边界(含)
             │                          │
  ───────────┼══════════════════════════┼──────────► 时间
             │◄───────── [5m] ─────────►│
```

**为什么这个区分如此重要**——三条硬规则：

| 规则 | 原因 |
|---|---|
| **① 只有范围向量能作为 `_over_time` 类函数和 `rate()` 的输入** | 这些函数需要"一段时间内的多个点"才能算 |
| **② 范围向量**不能**直接参与二元运算、比较运算、聚合** | 类型不匹配 → 官方行为是移除元素 + annotation（**不报明确错误**） |
| **③ 范围向量不能作为 range query 的根表达式** | 官方原文："Range queries only support scalar-typed and instant-vector-typed expressions." |

**最典型的混用错误（会静默出错或直接语法错）**：

```promql
# ❌ 错：rate() 的输入是范围向量 ✓，但这里把两个范围向量相加了
rate(x[5m]) / rate(y[5m])
```
↑ 这个其实是对的（rate 输出瞬时向量）。真正的错是：

```promql
# ❌ 错：两个范围向量直接相加
http_requests_total[5m] / http_requests_total[1h]
# ↑ 类型不匹配

# ❌ 错：对范围向量做 sum（聚合算子只接受瞬时向量）
sum(http_requests_total[5m])
# ↑ 类型不匹配

# ❌ 错：范围向量直接比较
http_requests_total[5m] > 100
# ↑ 类型不匹配
```

**正确做法：先压扁成瞬时向量**。

**"压扁"的三种手段**：

| 手段 | 例子 | 得到 |
|---|---|---|
| **用函数**（最常用） | `rate(x[5m])` / `avg_over_time(x[5m])` / `max_over_time(x[5m])` | 瞬时向量 |
| **用子查询**（对已聚合的结果再取时间维） | `avg_over_time(sum(x)[1h:5m])` | 瞬时向量 |
| **用 `@` + `offset`**（对比同一时刻的两个窗口） | `x offset 1h` | 瞬时向量（因为裸选择器就是瞬时向量） |

> **一句话记住**：
> **带方括号 = 范围向量（多个点）；不带方括号 = 瞬时向量（一个点）。**
> **函数的作用就是把范围向量压成瞬时向量。**

### 2.4 核心函数逐个讲透

#### 2.4.1 rate / irate / increase：速率三兄弟

**官方对三者的定义（`querying/functions/` 原文，逐字比对）**：

| 函数 | 官方定义 | 关键差异 |
|---|---|---|
| **`rate(v range-vector)`** | "calculates the **per-second average rate of increase** of the time series in the range vector. **Breaks in monotonicity (such as counter resets due to target restarts) are automatically adjusted for.** Also, the calculation **extrapolates to the ends of the time range**, allowing for missed scrapes or imperfect alignment of scrape cycles with the range's time period." | **区间平均** |
| **`irate(v range-vector)`** | "calculates the **per-second instant rate** of increase of the time series in the range vector. **This is based on the last two data points.**" | **区间内最后两个点的瞬时斜率** |
| **`increase(v range-vector)`** | "calculates the **increase** in the time series in the range vector. Breaks in monotonicity ... are automatically adjusted for. The increase is **extrapolated to cover the full time range** ... so that it is possible to get a **non-integer result** even if a counter increases only by integer increments." | **窗口内总增量** |

**官方对 `increase` 的定位（原文）**：

> "It is **syntactic sugar for `rate(v)` multiplied by the number of seconds** under the specified time range window, and should be used **primarily for human readability**. **Use `rate` in recording rules** so that increases are tracked consistently on a per-second basis."

**官方对 `rate` 的定位（原文）**：

> "`rate` should only be used with **counters** (for both floats and histograms). It is **best suited for alerting, and for graphing of slow-moving counters**."

**官方对 `irate` 的定位（原文，注意这段是官方明确写的告警建议）**：

> "`irate` should only be used when **graphing volatile, fast-moving counters**. **Use `rate` for alerts and slow-moving counters, as brief changes in the rate can reset the `FOR` clause and graphs consisting entirely of rare spikes are hard to read.**"

**⚠️ 官方给出的聚合顺序警告（三兄弟都有，必须背）**：

> "Note that when combining `rate()` (or `irate()`) with an **aggregation operator** (e.g. `sum()`) or a **function aggregating over time** (any function ending in `_over_time`), **always take a `rate()` first, then aggregate**. Otherwise `rate()` cannot detect **counter resets** when your target restarts."

**为什么这个顺序至关重要（用算例说明）**：

```
两个实例 A、B，都是 counter，每 15s 抓一次。
A 正常：0 → 100 → 200 → 300
B 在 t=30s 重启：0 → 0 → 50 → 100

❌ 错误写法：sum(rate(x[5m])) / sum(rate(y[5m]))
   —— 先 sum 再 rate？不对，这里是先 rate 后 sum，看似对。
   —— 真正的问题是：sum_over_time(x[5m]) / sum_over_time(y[5m])
      B 的重启归零被当成"少了 200"，分子被低估。

✅ 官方要求的顺序：先 rate（自动修正重置），再聚合
   sum(rate(x[5m])) / sum(rate(y[5m]))
```

**三兄弟的完整对比表**：

| 维度 | `rate` | `irate` | `increase` |
|---|---|---|---|
| **输出** | 每秒**平均**增长率 | 每秒**瞬时**增长率（最后两点） | 窗口内**总增量** |
| **对窗口内所有点的利用** | ✅ 全窗口 | ❌ **只用最后两个点** | ✅ 全窗口 |
| **平滑度** | **平滑** | **抖** | 平滑 |
| **对漏抓取/重启的鲁棒性** | **强**（区间平均 + 自动修正重置 + 外推） | **弱**（只看最后两点，重启恰好落在窗口尾部就炸） | 强 |
| **counter 重置** | **自动修正**（官方原文） | **自动修正**（官方原文） | **自动修正**（官方原文） |
| **适合告警** | ✅ **官方推荐** | ❌ **官方明确说会 reset FOR 条件** | ⚠️ 可读性好，但 recording rule 里官方要求用 rate |
| **适合画图** | ✅ 慢变 counter | ✅ **快变 counter**（官方原话） | ⚠️ 一般不直接画 |
| **必须是 counter** | ✅ | ✅ | ✅ |

> **官方建议直接背下来（面试答"rate 还是 irate"）**：
> "**告警用 `rate`，看板用 `irate`（或者两个都放上去对比）。**"
> 理由官方写得很清楚：`irate` 的短暂变化会**重置告警的 FOR 条件**（"brief changes in the rate can reset the `FOR` clause"），
> 而且全是尖峰时**图也不好读**（"graphs consisting entirely of rare spikes are hard to read"）。

**算例（把这三个数吃透）**：

```
假设 http_requests_total 每 15s 抓一次，5 分钟窗口内有 20 个样本：
  t=0s:   1000
  t=15s:  1050
  ...
  t=285s: 1900
  t=300s: 1950     （右边界含，所以这个点在 5m 窗口内）

increase(http_requests_total[5m])
  = (1950 - 1000) = 950
    官方说会外推到整个时间范围，所以可能是小数（如 950 或 949.7）

rate(http_requests_total[5m])
  = increase / 300秒 ≈ 3.17 次/秒
    （官方：increase 是 rate 乘以窗口秒数的语法糖，反推即得）

irate(http_requests_total[5m])
  = (1950 - 1900) / 15秒 = 3.33 次/秒
    （只看最后两个点）
```

#### 2.4.2 rate 的窗口至少要 4 个样本（高频陷阱）

**⚠️ 官方函数的适用条件（官方 `querying/functions/` 对 `deriv` 和 `predict_linear` 明确写了，对 `rate` 隐含）**：

> "The range vector **must have at least two float samples** in order to perform the calculation."（`deriv` / `predict_linear` 的原文）

**对 `rate` 而言，官方文档没有直接写"至少 4 个样本"这句话**，但**社区与生产实践的通行要求**是：

> **窗口至少覆盖 4 个抓取间隔**，才够平滑、才扛得住偶发漏抓。

**按不同抓取间隔换算（15s 是常见实践口径，⚠️ 官方配置参考标称全局默认是 `1m`）**：

| 抓取间隔 `scrape_interval` | **最小可用窗口（≥4 个样本）** | 常用窗口 |
|---|---|---|
| 15s | **60s** → 写 `[1m]` | `[5m]` |
| 30s | **2m** → 写 `[2m]` | `[5m]` |
| 1m（官方标称默认） | **4m** → 写 `[4m]` | `[5m]` |

> **这是常见实践，不是官方标准。** 官方文档明确写的是"至少两个样本"（针对 `deriv`/`predict_linear`）；
> "4 个样本"是**社区与生产实践的通行口径**，目的是让区间平均有足够样本、能容忍偶发漏抓。
> **写文档/面试时的诚实说法**："`rate` 的窗口至少要能覆盖 4 个抓取间隔（15s 抓取 → 至少 60s 窗口），
> 这是社区与生产实践的通行做法；官方文档只明确了'至少两个样本'这个下限。"

**窗口太小会怎样（官方没写，但工程上是确定的）**：

| 症状 | 原因 |
|---|---|
| **查询返回空** | 窗口内样本不足 → 无法计算 |
| **曲线锯齿剧烈** | 样本太少 → 每秒速率的估计方差极大 |
| **告警反复抖动** | 速率忽高忽低 → 阈值被反复穿越 |

**⚠️ 还有一个必须知道的官方机制：`lookback` 窗口**

官方 `querying/basics/` 原文：
> "Time series are only returned if their most recent sample is less than the **lookback period** ago. The lookback period is **5 minutes by default**, but can be set with the `--query.lookback-delta` flag or overridden on an individual query via the `lookback_delta` parameter."

> **这解释了一个常见困惑**：**为什么我查一个 5 分钟前的指标，它还在？**
> 因为 lookback 默认 5 分钟——只要最近 5 分钟内有样本，那一刻的值就能被取到。
> 反过来，**如果目标已经消失超过 5 分钟，那段数据就查不到了**（除非数据已被 remote write 到远端存储）。

#### 2.4.3 counter 重置：rate 会自动处理

**官方原文（三兄弟都有这句）**：

> "**Breaks in monotonicity (such as counter resets due to target restarts) are automatically adjusted for.**"

**"自动处理"具体是什么**：

```
counter 重置前：... 300
进程重启
counter 重置后：0 → 50 → 100

rate() 的处理：把下降的那一段当成"counter 归零了"，
              并把这个重置量**补回**去，
              所以 rate 算出来仍然是接近真实的正数，不会是负数。
```

**官方还提供了 `resets()` 函数专门用来数重置次数**：

> "`resets(v range-vector)` — For each input time series, returns the **number of counter resets** in the specified time range."

```promql
resets(http_requests_total[1h])    # 这一小时里重启了几次
```

**⚠️ 那"rate 出现负数"是怎么回事？（这是本节要答的问题）**

| 原因 | 判别方法 | 处理 |
|---|---|---|
| **① 对 gauge 用了 `rate`** | 官方明确："You should never take a `rate()` of a gauge." | 改用 `avg_over_time` / `max_over_time` / `delta` |
| **② 指标其实是 gauge 但命名成了 `_total`** | 看指标类型定义（`# TYPE xxx gauge`） | 改埋点类型 |
| **③ 真的发生了 counter 重置，但用的不是 rate**（比如裸差值 `x - x offset 5m`） | 看 `resets()` | 一律改用 `rate` / `increase` |

> **官方 `practices/instrumentation/` 的原话**：
> "**Raw counters are rarely useful. Use the `rate()` function to get the per-second rate at which they are increasing.**"
> （裸 counter 几乎没用。用 `rate()` 拿到每秒增长速率。）
> "**You should never take a `rate()` of a gauge.**"

**给"重启"做告警的正确姿势**（用 gauge 而不是 counter）：

```promql
# K8s 容器重启次数（counter）
increase(kube_pod_container_status_restarts_total[1h])   # 这一小时重启了几次

# 进程启动时间（gauge，Unix 时间戳）
time() - process_start_time_seconds
# 官方 practices/instrumentation 的原话：
# "If you want to track the amount of time since something happened,
#  export the Unix timestamp at which it happened - not the time since it happened."
```

#### 2.4.4 `*_over_time` 家族：区间聚合

**这一族的共同点**：输入是**范围向量**，输出是**瞬时向量**，即"把一段时间的数据压成一个数"。

| 函数 | 官方语义 | 关键限制 | 典型用途 |
|---|---|---|---|
| `avg_over_time(range-vector)` | 区间内所有值的**平均** | 官方："**区间内所有值的权重相同，即使它们在区间内的间隔并不相等**" | 平均延迟（**但用它代替分位数是错的**） |
| `sum_over_time(range-vector)` | 区间内所有值的**总和** | 同上，权重相同 | 区间内事件总数（gauge 场景） |
| `max_over_time(range-vector)` | 区间内**最大**值 | 官方："Histogram samples will be ignored"；纯 histogram 区间**静默移除** | 峰值内存、峰值队列长度 |
| `min_over_time(range-vector)` | 区间内**最小**值 | 同上 | 最低可用内存 |
| `count_over_time(range-vector)` | 区间内**样本数** | float 与 histogram 同等对待 | **健康度/保活判断** |
| `quantile_over_time(scalar, range-vector)` | 区间内 **φ-分位数**（0 ≤ φ ≤ 1） | 官方："Histogram samples will be ignored" | 单实例的延迟分位数 |
| `stddev_over_time(range-vector)` | 区间内**总体标准差** | 官方："population standard deviation"；只处理 float | 抖动程度 |
| `stdvar_over_time(range-vector)` | 区间内**总体方差** | 只处理 float | 同上 |
| `changes(range-vector)` | 区间内值**变化的次数** | float 与 histogram 交替计为一次变化 | 配置热更新次数、指标震荡次数 |
| `idelta(range-vector)` | **最后两个**样本之差 | **只适用于 gauge** | 瞬时增量 |
| `delta(range-vector)` | **首尾**样本之差 | **只适用于 gauge**；会外推到完整区间 | 区间净变化 |
| `resets(range-vector)` | 区间内 **counter 重置次数** | 只适用于 counter | 重启次数 |

**⚠️ `quantile_over_time` vs `histogram_quantile` 的区别（高频）**：

| | `quantile_over_time(0.99, x[5m])` | `histogram_quantile(0.99, sum by (le) (rate(x_bucket[5m])))` |
|---|---|---|
| **输入** | 区间内的**原始样本**（gauge 型延迟指标） | **classic histogram 的桶计数** |
| **能否跨实例聚合** | ❌ **不能**（每条序列各算各的） | ✅ **能**（先合并桶再算） |
| **精确度** | **精确**（真的把样本排了序） | **插值估算**（桶内线性插值） |
| **适用** | **单实例**、有原始延迟值 | **多副本服务**（生产默认选它） |

**⚠️ `avg_over_time` 的"等权重"陷阱（官方原文）**：

> "**区间内所有值的权重相同，即使它们在区间内的间隔并不相等。**"

意思是：如果某个样本因为漏抓取而"离邻居更远"，它**不会**因为时间跨度大而获得更大权重。
→ 这在抓取不均匀时会让平均值失真。**生产上更推荐 `histogram_quantile` + histogram**。

#### 2.4.5 `absent` / `absent_over_time`：缺失检测

**官方定义（`querying/functions/` 原文）**：

> `absent(v instant-vector)`:
> "如果输入向量中**存在任何元素**（任何 float 或 histogram 样本），则返回**空向量**；
> 如果输入向量**没有元素**，则返回一个**值为 1 的单元素向量**。"

> `absent_over_time(v range-vector)`:
> "如果 range vector 中**存在任何元素**，则返回空向量；**没有元素**则返回值为 1 的单元素向量。"

**官方还说明了标签推导行为（很实用）**：

> "`absent()` 会尝试从输入向量推导其单元素输出向量的标签。"

官方例子：
```promql
absent(nonexistent{job="myjob"})        # => {job="myjob"}
absent(sum(nonexistent{job="myjob"}))   # => {}   ← 标签丢了！
```

**典型用途（三个）**：

```promql
# ① 目标彻底消失（连 up 都没了）时的告警
absent(up{job="my-service"})

# ② 某个业务指标彻底没上报（服务活着但埋点坏了/被 relabel drop 了）
absent(http_requests_total{job="my-service"})

# ③ 一段时间内完全没有数据（比 ① 更宽容，允许短暂抖动）
absent_over_time(http_requests_total{job="my-service"}[10m])
```

> **② 是最有价值的一个**：它能抓到"`up=1` 但业务指标一个都没有"这种**静默失效**——
> 这正是 L1 里说的"`metric_relabel_configs` 把样本全 drop 了"的最典型症状。

#### 2.4.6 `changes` / `deriv` / `predict_linear`：变化与预测

**官方定义（逐字）**：

| 函数 | 官方定义 | 官方限制 |
|---|---|---|
| `changes(v range-vector)` | "返回其在指定时间范围内**值发生变化的次数**" | float 后跟 histogram 或反过来**计为一次变化**；counter histogram → gauge histogram **不计** |
| `deriv(v range-vector)` | "使用**简单线性回归**计算 range vector 中每个 float 时间序列的**每秒导数**" | **"range vector 必须至少有两个 float 样本"**；含 `+Inf`/`-Inf` → slope 和 offset 为 `NaN`；**只应用于 gauges** |
| `predict_linear(v range-vector, t scalar)` | "基于 range vector `v`，使用**简单线性回归**预测**从当前时刻起 `t` 秒后**的时间序列值" | **"range vector 必须至少有两个 float 样本"**；含 `+Inf`/`-Inf` → 预测值为 `NaN`；**只应用于 gauges** |

**三个典型用途**：

```promql
# ① 配置热更新了多少次
changes(process_config_last_reload_success_timestamp_seconds[1h])

# ② 磁盘空间每天增长多少 MB（gauge 的导数）
deriv(node_filesystem_avail_bytes{mountpoint="/data"}[6h]) * 86400 / 1024 / 1024

# ③ 预测 4 小时后磁盘会不会满（内存增长趋势的经典用法）
predict_linear(node_filesystem_avail_bytes{mountpoint="/data"}[6h], 4*3600) < 0
```

> **③ 是"内存/磁盘增长趋势"的标准告警写法**：
> "**用过去 6 小时的线性趋势外推 4 小时后**，如果预测值 < 0，说明 4 小时内会写满。"
> **注意它的前提**：线性外推**只对"稳定增长率"有效**。
> 如果业务有周期性（每天一个波峰），线性外推会**严重失真**——
> 这种场景应该用 `deriv` 看瞬时增长率，或者用更长的窗口 + 更保守的阈值。

#### 2.4.7 `histogram_quantile`：分位数的正确用法

**这一节是本篇最重要的单节。**

**官方定义（`querying/functions/` 原文）**：

> `histogram_quantile(φ scalar, b instant-vector)`:
> "计算 classic histogram 或 native histogram 的 φ-分位数，其中 0 ≤ φ ≤ 1。"

**官方对 classic histogram 输入的硬要求**：

> "`b` 中的 float samples 被视为一个或多个 classic histograms 各 bucket 的 observations 数量。
> **每个 float sample 必须带有 `le` 标签**。`le` 的值表示 bucket 的 **inclusive upper bound**。
> **没有 `le` 标签的 float samples 会被静默忽略。**"

**官方列出的 NaN 情形（这些是"静默出错"的高发区）**：

| 情形 | 官方结果 |
|---|---|
| `b` 中**少于两个桶** | **NaN** |
| **最高桶的 upper bound 不是 `+Inf`** | **NaN** |
| 分位数落在最高桶里 | 返回**第二高桶的 upper bound** |
| 最低桶 upper bound > 0 | 假设 lower limit 为 0，桶内**线性插值** |
| 最低桶 upper bound ≤ 0 | 落在最低桶的分位数返回该桶 upper bound |
| `b` 有 **0 个 observations** | **NaN** |
| φ < 0 | `-Inf` |
| φ > 1 | `+Inf` |

**⚠️ 官方还说明了"桶计数不单调"时的自动修复（以及那条 annotation）**：

> "`histogram_quantile` 会将相邻 bucket 间**极小的相对差异**视为浮点精度误差并忽略。
> 忽略阈值为两个 bucket counts 之和的 `1e-12`。如果调整后仍不单调，则将后续 bucket 提高到前序 bucket 的值，以强制单调。"
> 触发时的 annotation 原文：**`input to histogram_quantile needed to be fixed for monotonicity`**

> **这个 annotation 的意义**：它说明**你的桶数据有问题**（通常是两个不同 bucket 边界的 histogram 被混在一起聚合了），
> 应该去排查数据源，而不是忽略它。

**⭐ 正确用法（官方给的写法）**：

官方 `querying/functions/` 的示例原文：

```promql
# classic histogram，按 job 聚合时必须保留 le
histogram_quantile(
  0.9,
  sum by (job, le) (rate(http_request_duration_seconds_bucket[10m]))
)
```

官方 `practices/histograms/` 的示例：

```promql
# GOOD
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))
```

**❌ 错误用法（官方明确点名）**：

官方 `practices/histograms/` 的反面教材原文：

```promql
avg(http_request_duration_seconds{quantile="0.95"}) // BAD!
```

> **为什么"直接对多实例算"会错**——用算例说明：

```
三个实例的延迟分布：
  实例 A：1000 个请求，90% 在 10ms 内
  实例 B：10 个请求，全部 500ms
  实例 C：1000 个请求，90% 在 10ms 内

实例 A 的 P95 ≈ 10ms
实例 B 的 P95 ≈ 500ms
实例 C 的 P95 ≈ 10ms

❌ 错误：histogram_quantile(0.95, rate(x_bucket[5m]))     ← 每个实例各算各的
   结果：A=10ms, B=500ms, C=10ms  →  三条序列，你不知道总体 P95 是多少

❌ 更错误：avg(实例级 P95) = (10+500+10)/3 = 173ms
   ★ 这个数字既不是总体 P95，也不是任何实例的 P95 —— 统计上毫无意义

✅ 正确：histogram_quantile(0.95, sum by (le) (rate(x_bucket[5m])))
   先把所有实例的桶计数相加（2010 个请求的合并分布），
   再算 P95 → 得到总体真实的 P95
```

> **一句话记住**：
> **"先合并桶，再算分位数。"**
> `sum by (le, ...)` 里的 `le` **必须保留**——丢了 `le`，桶就混了，结果就是错的。
> **而 `by` 里还应该带上你想要的输出维度**（如 `job`、`service`），
> 否则你只能得到一个全局值，没法按服务下钻。

**⚠️ 关于 summary 类型**：

**Summary 不能用 `histogram_quantile`**（它没有 `_bucket`），
只有官方示例里那种"直接取 quantile 标签"的方式，而**跨实例聚合无意义**（见 2.4.7 开头）。
**所以多副本服务一律用 histogram。**

#### 2.4.8 `label_replace` / `label_join`：标签改写

**官方定义（`querying/functions/` 原文）**：

> `label_replace(v instant-vector, dst_label string, replacement string, src_label string, regex string)`:
> "用 `regex` 匹配 `src_label` 的值。**如果匹配**，`dst_label` 的值将是 `replacement` 的展开结果（`$1`、`$2` 引用捕获组，`$name` 引用命名捕获组），并保留输入的原始 labels。**如果正则不匹配，则原样返回该时间序列。**"

官方例子：
```promql
label_replace(up{job="api-server",service="a:c"}, "foo", "$1", "service", "(.*):.*")
# => service="a:c", foo="a"

label_replace(up{job="api-server",service="a:c"}, "foo", "$name", "service", "(?P<name>.*):(?P<version>.*)")
# => 命名捕获组
```

> `label_join(v instant-vector, dst_label string, separator string, src_label_1 string, src_label_2 string, ...)`:
> "用 `separator` 连接所有 `src_labels` 的值，返回带 `dst_label`（值为连接结果）的时间序列。`src_labels` 的数量不限。"

官方例子：
```promql
label_join(up{job="api-server",src1="a",src2="b",src3="c"}, "foo", ",", "src1", "src2", "src3")
# => foo="a,b,c"
```

**两个典型用途**：

```promql
# ① 从复合标签里拆出新维度（做看板下钻用）
label_replace(kube_pod_info{namespace="demo",pod="my-app-7d9f"}, "pod_name", "$1", "pod", "(.*)-(.*)")

# ② 把多个标签拼成一个可读的复合键
label_join(up{job="api",instance="i-1",pod="p-1"}, "target", "/", "job", "pod")
```

> **⚠️ 陷阱**：`label_replace` **不匹配时原样返回**（官方原文），
> 所以**它不会报错、也不会删掉任何东西**——
> 写错正则的结果是"什么都没变"，很容易被忽略。

#### 2.4.9 `topk` / `bottomk` / `count_values` / `sort` / `sort_desc`

**官方对 `topk` / `bottomk` 的定义（`querying/operators/` 原文）**：

> "`topk(k, v)` and `bottomk(k, v)` are **different from other aggregators** in that a **subset of `k` values** from the input samples, **including the original labels**, are returned in the result vector.
> `by` and `without` are **only used to bucket the input vector**."

> "If used in an instant query, `topk` and `bottomk` return series **ordered by value** in descending or ascending order, respectively.
> **No sorting applies to range queries.**"

**注意 `topk` 是聚合算子（不是函数）**，写在 `querying/operators/` 页而不是函数页。

**官方对 `count_values` 的定义**：

> "`count_values(l, v)` outputs **one time series per unique sample value** in `v`. Each series has an additional label, given by `l`, and **the label value is the unique sample value**. The value of each time series is the **number of times that sample value was present**."

官方例子：
```promql
count_values("version", build_version)
```

**官方对 `sort` / `sort_desc` 的定义（`querying/functions/` 原文）**：

> "`sort(v instant-vector)` — 按 float sample values **升序**排列。"
> "`sort_desc(v instant-vector)` — 行为与 `sort` 相同，但**降序**排列。"
> **两条共同的官方说明**：
> "**Histogram samples will be ignored.**"
> "**Only affects instant query's result order. Range query's result always has a fixed output order.**"

**这五个的典型用途**：

```promql
# ① 找出内存占用最高的 5 个 Pod
topk(5, sum by (pod) (container_memory_working_set_bytes{namespace="demo"}))

# ② 找出请求最少的 5 个实例（找异常/找空转实例）
bottomk(5, sum by (instance) (rate(http_requests_total[5m])))

# ③ 按版本统计实例数
count_values("version", build_info)

# ④ 把结果按值排序（做表格展示时）
sort_desc(sum by (pod) (container_memory_working_set_bytes))

# ⑤ 找出值最大的那条（topk(1, ...)）
topk(1, node_filesystem_avail_bytes)
```

> **⚠️ `sort` 的官方限制在实践中很关键**：
> "**Only affects instant query's result order. Range query's result always has a fixed output order.**"
> → **`sort_desc` 只能用在 instant query 上**（比如表格面板、`/api/v1/query`）。
> **画曲线图（range query）时用 `sort_desc` 是没有意义的**——每一步的输出顺序都是固定的。

#### 2.4.10 `vector` / `scalar`：类型转换

**官方定义（`querying/functions/` 原文）**：

> `vector(s scalar)`:
> "将标量 `s` 转换为一个 float sample，并返回**无标签的单元素 instant vector**。"

> `scalar(v instant-vector)`:
> "如果输入向量**恰好包含一个元素且该元素是 float sample**，则返回该 sample 的值作为 scalar；**否则返回 `NaN`**。"
> 官方还注明："**Histogram samples will be silently ignored.**"

**典型用途（三个）**：

```promql
# ① 把阈值变成向量，才能做"向量 vs 向量"的比较
vector(0) == 0
# ↑ 这条查询会返回所有值为 0 的序列（而不是布尔结果）

# ② 把唯一值转成标量，用于与数字比较
scalar(node_time_seconds) - time()    # 距离 1970 过了多少秒

# ③ 构造"始终为 1"的序列，用来做百分比基线
sum(rate(x[5m])) / sum(rate(y[5m])) * 100
```

> **⚠️ `scalar()` 的 NaN 陷阱**：
> 它要求**恰好一个元素**。查出来两条 → 返回 NaN；
> 查出来零条 → 返回 NaN。
> **NaN 参与比较永远不等于**，所以 `scalar(x) > 5` 会静默返回空结果，**不报错**。
> **排查技巧**：先 `count(x)` 确认是不是 1，再用 `scalar()`。

#### 2.4.11 `and` / `or` / `unless`：集合运算

**官方定义（`querying/operators/` 原文，注意它们是"逻辑/集合二元运算符"，不是函数）**：

> "These logical/set binary operators are **only defined between instant vectors**:
> `and` (intersection), `or` (union), `unless` (complement)"

| 运算符 | 官方定义 | 语义 |
|---|---|---|
| `and` | "`vector1 and vector2` results in a vector consisting of the elements of **vector1 for which there are elements in vector2 with exactly matching label sets**. Other elements are dropped. **The metric name and values are carried over from the left-hand side vector.**" | **交集** |
| `or` | "`vector1 or vector2` results in a vector that contains **all original elements of vector1** and additionally **all elements of vector2 which do not have matching label sets in vector1**." | **并集**（左侧优先） |
| `unless` | "`vector1 unless vector2` results in a vector consisting of the elements of **vector1 for which there are no elements in vector2 with exactly matching label sets**." | **左侧减去右侧** |

**⚠️ 关键规则（官方原文）**：

> "**Set operators match with all possible entries on either side by default.**"
> （集合运算符默认与另一侧**所有可能的条目**匹配。）

> **这意味着 `and`/`or`/`unless` 用的是"完全相同的标签集"**——
> **不能用 `on()` / `ignoring()` 改写匹配规则**（官方明确："Grouping modifiers can only be used for comparison, arithmetic, and trigonometric operators"）。

**典型用途（四个）**：

```promql
# ① 有流量但没报错的服务（两个都正常的服务）
sum by (service) (rate(http_requests_total[5m]))
  and
sum by (service) (rate(http_errors_total[5m]))

# ② 有流量但 up=0 的服务（矛盾状态，重点排查）
sum by (service) (rate(http_requests_total[5m]))
  and on() ...   ← 注意：不能用 on()，见下面的说明

# ③ 补齐缺失的标签值（左侧优先，右侧补充）
up or on() vector(0)

# ④ 找出"注册了但从未上报数据"的服务
count by (service) (service_registered_total)
  unless
count by (service) (service_heartbeat_total)
```

> **⚠️ `and` 的严格匹配在实际使用中经常"匹配不上"**：
> `sum by (service) (a)` 和 `sum by (service) (b)` 聚合后标签集是 `{service="x"}`，
> **能匹配上**；但 `up{job="x"}` 的标签集是 `{__name__="up", instance="...", job="x"}`，
> 和 `sum by (service) (b)` 的 `{service="x"}` **匹配不上**（标签集不同）。
> **解法**：**两侧用同样的聚合方式**，让标签集对齐——
> `sum by (service) (up) and sum by (service) (b)`。

### 2.5 聚合算子：`sum by` vs `sum without`

**官方对 `by` / `without` 的定义（`querying/operators/` 原文）**：

> "`without` **removes the listed labels** from the result vector, while **all other labels are preserved** in the output.
> `by` does the opposite and **drops labels that are not listed** in the `by` clause, **even if their label values are identical between all elements of the vector**."

**官方给的等价示例（这段话本身就是最好的教学）**：

> 如果指标 `memory_consumption_bytes` 按 `application`、`instance`、`group` 三个标签展开，
> 要"每应用每组、跨所有实例"求和：
> ```
> sum without (instance) (memory_consumption_bytes)
> ```
> **等价于**：
> ```
> sum by (application, group) (memory_consumption_bytes)
> ```

**官方还给出了最直接的写法**：

> "If we are just interested in the total memory consumption in **all** applications, we could simply write: `sum(memory_consumption_bytes)`"

**两种语法的完整形态（官方原文）**：

```
<aggr-op> [without|by (<label list>)] ([parameter,] <vector expression>)
```
或
```
<aggr-op>([parameter,] <vector expression>) [without|by (<label list>)]
```

> **官方说明**："These clauses may be used **before or after** the expression."（两种位置都合法。）

**`by` vs `without` 的选择依据（这是本节要答的问题）**：

| 场景 | 用哪个 | 理由 |
|---|---|---|
| **输出维度是已知的、固定的** | **`by`** | 明确列出要保留的维度，**新增标签不会污染输出** |
| **要聚合掉的维度已知，但想保留其余全部** | **`without`** | 维度多时更省事 |
| **生产环境的推荐做法** | **`without`**（官方 recording rule 实践页的建议） | 官方原文：**"Always specify a `without` clause with the labels you are aggregating away. This is to preserve all the other labels such as `job`, which will avoid conflicts and give you more useful metrics and alerts."** |

> **⚠️ 官方这条建议的深意**：
> 用 `by` 会**丢掉 `job`、`cluster` 这些你没有列进去的标签**。
> 两个不同集群的 Prometheus 里同名指标聚合后会**撞在一起**（这正是 L1 里说的 `many-to-many matching`）。
> **用 `without (instance)` 既聚合掉了实例维度，又保留了 `job`/`namespace`/`cluster`。**

**⚠️ `by` 漏写的后果（高频陷阱）**：

```promql
# ❌ 忘了 by：返回的是"每一个标签组合"一条序列，不是聚合结果
sum(rate(http_requests_total[5m]))
# → 返回几百条（每个 instance × method × code 一条）

# ❌ by 写漏了一个维度：维度错乱
sum by (service) (rate(http_requests_total[5m]))
# → 如果本来想要的是"按 service 和 method"，少了 method → 同一 service 的不同 method 被加在一起

# ✅ 正确：明确输出粒度
sum by (service, method) (rate(http_requests_total[5m]))
```

**判据**：

> **聚合后的结果条数 = 输入序列按 `by` 维度分组后的组数。**
> **如果你期望 1 条、却返回了 N 条 → `by` 漏写或写错。**
> **如果你期望 N 条、却返回了 1 条 → `by` 多写了维度。**

### 2.6 多实例聚合的正确写法（先求和再算比例）

**这一节是本篇第二重要的一节。**

**官方 recording rule 实践页的两条硬规则（`practices/rules/` 原文）**：

> "When aggregating up **ratios**, aggregate up the **numerator and denominator separately and then divide**."
> （聚合比值时，**分别聚合分子和分母，然后再相除**。）

> "**Do not take the average of a ratio or average of an average, as that is not statistically valid.**"
> （**不要对比值取平均，也不要对平均值取平均，这在统计上不成立**。）

**⭐ 正确写法（官方示例原文）**：

```promql
# ✅ 正确：先分别求和，再相除
sum(rate(http_errors_total[5m]))
  /
sum(rate(http_requests_total[5m]))
```

**❌ 错误写法（三种，每种错得不一样）**：

```promql
# ❌ 错误 1：先算比例再求和 —— 结果无意义
sum(
  rate(http_errors_total[5m]) / rate(http_requests_total[5m])
)
# → 每个实例各算一个错误率再加起来 → 结果可能 > 100% 或者远超真实错误率

# ❌ 错误 2：对比例取平均 —— 统计上不成立（官方明文禁止）
avg(
  rate(http_errors_total[5m]) / rate(http_requests_total[5m])
)
# → 小流量实例的错误率会和大流量实例等权，典型"长尾污染"

# ❌ 错误 3：分子分母聚合维度不一致 —— 向量匹配失败
sum(rate(http_errors_total[5m]))            # 聚合到 service
  /
sum by (method) (rate(http_requests_total[5m]))   # 聚合到 service+method
# → 标签集不同 → 匹配不上 → 返回空结果
```

**用算例说清"先算后聚合"错在哪**：

```
实例 A：10000 请求，100 个错误  → 错误率 1%
实例 B：10 请求，   5 个错误    → 错误率 50%

真实总体错误率 = (100+5) / (10000+10) = 105/10010 ≈ 1.05%

❌ 先算比例再求和：1% + 50% = 51%      ← 差了 50 倍
❌ 对比例取平均：(1% + 50%)/2 = 25.5%  ← 差了 25 倍
✅ 先求和再相除：105 / 10010 ≈ 1.05%   ← 正确
```

> **这个错误的危害是"看起来合理"**：51% 和 25.5% 都在 0-100% 之间，
> **不检查就发现不了**，但告警阈值是按真实错误率（比如 2%）设的 → **告警永远不触发**。
>
> **面试这么答**：
> "聚合比值时，**分子分母要分别聚合后再相除**。
> 因为比值是**非线性**的，先算比值再聚合等于给每个实例的比率加权，
> 而小流量实例的比率方差极大，会把整体结果带偏。
> 官方 recording rule 实践页明确写了：'不要对比值取平均，这在统计上不成立'。
> 我们的规则是**先 recording 成 `_per_` 的分子分母，再在下游相除**。"

### 2.7 向量匹配与 `group_left` / `group_right`

**官方对向量匹配的定义（`querying/operators/` 原文）**：

> "Operations between vectors attempt to find a **matching element in the right-hand side vector** for each entry in the left-hand side. There are two basic types of matching behavior: **One-to-one** and **Many-to-one/one-to-many**."

**匹配关键词**：

| 关键词 | 官方语义 |
|---|---|
| `on(<label list>)` | "**Only match on provided labels.**"（只按列出的标签匹配） |
| `ignoring(<label list>)` | "**Ignore provided labels** when matching."（忽略列出的标签来匹配） |

**默认行为**：**两侧标签集完全相同**才算一对一匹配（"Two entries match if they have the **exact same set of labels**"）。

**`group_left` / `group_right`（官方原文）**：

> "`group_left`: Allow **many-to-one** matching, where the **left vector has higher cardinality**.
> `group_right`: Allow **one-to-many** matching, where the **right vector has higher cardinality**."

> "Label lists can be provided to the group modifier which contain labels from the **'one'-side to be included in the result metrics**."

**官方还有一条重要提醒**：

> "*Many-to-one and one-to-many matchings are **advanced use cases that should be carefully considered**. Often a proper use of `ignoring(<labels>)` provides the desired outcome.*"
> （**多对一匹配是高级用例，需要仔细考虑。很多时候一个恰当的 `ignoring()` 就能达到目的。**）

**官方对 `on` 与 group modifier 的交互约束**：

> "For `on` a label can only appear in **one** of the lists. **Every time series of the result vector must be uniquely identifiable.**"
> （用 `on` 时，同一个标签不能同时出现在 `on(...)` 和 `group_left(...)` 里；结果的每条序列必须能唯一识别。）

**典型场景（"总量 ÷ 单实例 = 每实例平均"）**：

```promql
# 场景：集群总 QPS ÷ 实例数 = 每实例平均 QPS
# 左侧（总量）：{service="api"}
# 右侧（实例数）：{service="api", instance="a"}, {service="api", instance="b"}  ← 基数更高
# 右侧基数更高 → 用 group_right

sum by (service) (rate(http_requests_total[5m]))
  / on (service)
count by (service, instance) (up{job="api-service"})
  group_right
```

> **选 `group_left` 还是 `group_right` 的口诀**：
> **哪个是"多"，就用对应的那个词**——
> **左边多 → `group_left`；右边多 → `group_right`**。
> （`group_left` = "**left** side is the many side"。）

**⚠️ 官方明确说"先试 `ignoring()`"**：

```promql
# 通常这个就够了（官方推荐先试这个）
sum by (service) (rate(x[5m]))
  / ignoring (instance)
count by (service, instance) (up)
```

**指标名的一个官方细节（容易踩）**：

> "In any comparison binary operation involving vectors, providing the `bool` modifier ... **The metric name is dropped.**"
> "If the `bool` modifier is **not** provided, then the metric name from the **left side** is retained, with some exceptions:
> - If **`on` is used**, then the metric name is dropped.
> - If **`group_right` is used**, then the metric name from the **right side** is retained, to avoid collisions."

### 2.8 四个黄金信号：RED 与 USE

**这一节给的是"设计哪些指标"的方法论，不是函数。**

**RED（面向微服务 / 面向用户）**——来源：Tom Wilkie 提出的 RED 方法，中文社区翻译版在 `developer.aliyun.com/article/511487`：

| 维度 | 含义 | PromQL 模板（中文社区 `article/1436803` 给出的写法） |
|---|---|---|
| **Rate**（请求速率） | 每秒处理的请求数 | `sum(rate(request_duration_seconds_count{job="…"}[1m]))` |
| **Errors**（错误数/率） | 每秒失败的请求数 | `sum(rate(request_duration_seconds_count{job="…", status_code!~"2.."}[1m]))` |
| **Duration**（耗时） | 每个请求的耗时分布 | `histogram_quantile(0.99, sum by (le) (rate(request_duration_seconds_bucket{job="…"}[1m])))` |

**中文社区明确指出的一点**：

> "Duration 推荐使用 **50th/90th/99th percentile**，这些会更精确地反映用户真正关心的问题，同时可以结合 Average Duration 来作为参考。"

**USE（面向资源 / 面向机器）**——来源：Brendan Gregg，中文翻译在 `developer.aliyun.com/article/1172417`：

| 维度 | 含义 | 典型 PromQL |
|---|---|---|
| **Utilization**（利用率） | 资源被占用的比例 | `1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m]))` |
| **Saturation**（饱和度） | 资源排队/无法提供服务的时间 | 容器 CPU throttling、队列长度、磁盘 IO wait |
| **Errors**（错误） | 失败的次数/速率 | `sum by (namespace) (rate(container_oom_events_total[5m]))` |

**中文社区对 USE 的一句话概括**：

> "**USE 方法更侧重于监控的性能**，并以此为出发点寻找影响性能问题的根本原因以及其他系统的瓶颈。"

**四个黄金信号（Four Golden Signals）与 RED/USE 的关系**（中文社区 `article/1436803` 的原文）：

> "Four Golden Signals: **Latency**（响应延迟，和 Duration 类似）、**Traffic**（对你的系统有多大的需求，和 Rate 类似）、**Errors**、**Saturation**。基本上就是 **RED + Saturation**。"

> **中文社区的建议**：
> "建议**同时使用 RED 和 USE Method**，其中：
> **RED Method 关心你的用户以及他们有多快乐，而 USE Method 则是关心你的机器以及它们有多快乐。**"

**⭐ 两个框架的分工（这是本节要答的问题）**：

| | **RED** | **USE** |
|---|---|---|
| **面向** | **服务 / 用户** | **资源 / 机器** |
| **回答** | "**用户体验怎么样？**" | "**资源用得怎么样？**" |
| **用在什么对象** | 微服务、API、网关 | 节点、容器、数据库、中间件 |
| **典型告警** | 错误率 > 2%、P99 > 1s | CPU 饱和、内存逼近上限、磁盘将满 |

> **和 HPA 的口径对齐（跨篇一致）**：
> **HPA 用的 CPU 利用率 = 实际用量 ÷ `requests.cpu`**（不是 ÷ limits，也不是 ÷ 节点总量）。
> 在 Prometheus 里，节点 CPU 利用率是"实际用量 ÷ 节点总容量"，
> **而 HPA 的利用率是"Pod 实际用量 ÷ 该 Pod 的 `requests.cpu`"**——**两者口径不同，不要混用**。
> K8s 的 metrics-server 直接读 cAdvisor 的用量并除以 `requests.cpu`，
> Prometheus 侧的 `kube_pod_container_resource_requests` 可以拿到分母，两者相除即可对齐 HPA 口径：
>
> ```promql
> # 与 HPA 同口径的 CPU 利用率（实际用量 ÷ requests.cpu）
> sum by (pod) (rate(container_cpu_usage_seconds_total{container!=""}[5m]))
> /
> sum by (pod) (kube_pod_container_resource_requests{resource="cpu", unit="core"})
> ```
> **注意**：`requests.cpu` 为空时 HPA 会变成 `<unknown>/50%`（见 K8s 篇 L2），这类 Pod 的利用率**算不出来**。

> **阈值类结论必须标明（跨篇统一口径）**：
> **"阈值 = 历史 P95 × 1.2"是常见实践，不是标准。**
> D12 日课里记录的是这个口径，**它的价值在于"阈值有依据、可复盘"，而不是那个 1.2 这个数字本身**。
> 真实项目应该用**历史分位数 + 业务时段区分**来定阈值，并且**上线后按误报率回修**。

### 2.9 典型排障查询模板（6 类）

**这一节是"能背下来直接用"的部分。** 完整模板表见第 4.2 节。

**① p95 延迟（多副本服务）**：

```promql
histogram_quantile(0.95,
  sum by (le, service) (rate(http_request_duration_seconds_bucket[5m]))
)
```

**读法**：`le` 必须保留（桶的边界）；`service` 是输出粒度；`[5m]` 至少覆盖 4 个抓取周期。

**② 错误率**：

```promql
sum by (service) (rate(http_requests_total{code=~"5.."}[5m]))
  /
sum by (service) (rate(http_requests_total[5m]))
```

**读法**：**先分别求和再相除**（绝不能先算比例再求和）。

**③ QPS**：

```promql
sum by (service) (rate(http_requests_total[5m]))
```

**④ 饱和度（容器 CPU 被 throttle）**：

```promql
sum by (pod) (rate(container_cpu_cfs_throttled_seconds_total[5m]))
```

**读法**：这是**饱和度**的经典信号——被 cgroup 限流说明"想跑但跑不了"，比 CPU 使用率更接近真实的"不够用"。

**⑤ 重启次数**：

```promql
sum by (pod, namespace) (increase(kube_pod_container_status_restarts_total[1h]))
```

**读法**：`increase` 天然处理 counter 重置；返回 0 表示这一小时没重启。

**⑥ 内存增长趋势**：

```promql
# 瞬时增长率（每天增长多少 MB）
deriv(container_memory_working_set_bytes{namespace="demo"}[6h]) * 86400 / 1024 / 1024

# 4 小时后会不会写满
predict_linear(container_memory_working_set_bytes{namespace="demo"}[6h], 4*3600) < 0
```

**读法**：`deriv` / `predict_linear` **只适用于 gauge**；线性外推**只对稳定增长有效**。

### 2.10 recording rules：预计算

**官方定义（`configuration/recording_rules/` 原文）**：

> "Recording rules allow you to **precompute frequently needed or computationally expensive expressions** and save their result as a **new set of time series**. Querying the precomputed result will then often be **much faster** than executing the original expression every time it is needed. This is especially useful for **dashboards, which need to query the same expression repeatedly every time they refresh**."

**官方对规则组的定义**：

> "Rules within a group are run **sequentially** at a **regular interval**, with the **same evaluation time**."

**官方 `rule_group` 的字段（配置参考原文）**：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `name` | 必填 | 组名，**文件内唯一** |
| `interval` | `global.evaluation_interval`（**全局默认 1m**） | 该组的评估频率 |
| `limit` | **0（不限制）** | 该组产出的告警数/序列数上限。**超限时该规则产出的全部序列被丢弃**（告警规则的话，pending/firing/inactive 全部清空） |
| `query_offset` | `global.rule_query_offset` | 把该组的求值时刻往过去挪，**用于确保底层指标已经入库** |
| `labels` | 空 | 存储结果前附加/覆盖的标签 |
| `rules` | 必填 | 规则列表 |

**recording rule 的字段**：

| 字段 | 说明 |
|---|---|
| `record` | 输出的时间序列名，**必须是合法指标名** |
| `expr` | 要计算的 PromQL 表达式 |
| `labels` | 存储前附加/覆盖的标签 |

**官方给的两个重要机制**：

**① `query_offset` 是干什么的（官方原文）**：

> "This is useful to **ensure the underlying metrics have been received and stored in Prometheus**. Metric availability delays are **more likely to occur when Prometheus is running as a remote write target** due to the nature of distributed systems, but can also occur when there's anomalies with scraping and/or short evaluation intervals."

**② 评估太慢会跳过（官方原文）**：

> "If a rule group hasn't finished evaluating before its next evaluation is supposed to start (as defined by the `evaluation_interval`), **the next evaluation will be skipped**. Subsequent evaluations of the rule group will continue to be skipped until the initial evaluation either completes or times out. When this happens, there will be a **gap in the metric produced by the recording rule**. The **`rule_group_iterations_missed_total`** metric will be incremented for each missed iteration of the rule group."

> **这条官方机制直接对应"告警数据断档"的排查**：
> **看到 `rule_group_iterations_missed_total` 在涨 → 规则评估超时 → 预计算的指标有空洞。**

**官方命名规范（`practices/rules/` 原文）**：

> "Recording rules should be of the general form **`level:metric:operations`**.
> - `level` represents the **aggregation level and labels** of the rule output.
> - `metric` is the metric name and **should be unchanged other than stripping `_total` off counters when using `rate()` or `irate()`**.
> - `operations` is a list of operations that were applied to the metric, **newest operation first**."

官方还有两条：

> "To keep the operations clean, **`_sum` is omitted if there are other operations**, as `sum()`. Associative operations can be merged (for example `min_min` is the same as `min`)."
> "If there is **no obvious operation to use, use `sum`**. When taking a ratio by doing division, **separate the metrics using `_per_`** and call the operation `ratio`."

**官方给的完整示例（可以直接抄）**：

```yaml
groups:
  - name: example
    interval: 30s
    rules:
      # ① 实例级速率
      - record: instance_path:requests:rate5m
        expr: rate(requests_total{job="myjob"}[5m])

      # ② 聚合到 path 级
      - record: path:requests:rate5m
        expr: sum without (instance)(instance_path:requests:rate5m{job="myjob"})

      # ③ 实例级失败率（分子分母分开记）
      - record: instance_path:request_failures:rate5m
        expr: rate(request_failures_total{job="myjob"}[5m])

      - record: instance_path:request_failures_per_requests:ratio_rate5m
        expr: |2
            instance_path:request_failures:rate5m{job="myjob"}
          /
            instance_path:requests:rate5m{job="myjob"}

      # ④ 聚合到 job 级失败率（先分别聚合，再相除）
      - record: job:request_failures_per_requests:ratio_rate5m
        expr: |2
            sum without (instance, path)(instance_path:request_failures:rate5m{job="myjob"})
          /
            sum without (instance, path)(instance_path:requests:rate5m{job="myjob"})
```

> **看第 ④ 条官方示例，学到两件事**：
> ① **分子分母分别 `sum without` 之后再相除**（官方自己就是这么写的）
> ② **用 `without` 而不是 `by`**（官方原话："Always specify a `without` clause with the labels you are aggregating away. This is to preserve all the other labels such as `job`"）

**官方还有一条自查规则（很妙）**：

> "Notice that when aggregating that the labels in the `without` clause are **removed from the level of the output metric name** compared to the input metric names. **When there is no aggregation, the levels always match. If this is not the case a mistake has likely been made in the rules.**"

**语法检查工具（官方原文）**：

> "To quickly check whether a rule file is **syntactically correct without starting a Prometheus server**, you can use Prometheus's **`promtool`** command-line utility tool:
> `promtool check rules /path/to/example.rules.yml`"

> **这是 CI 里必做的一步**——规则文件的语法错误**不会让 Prometheus 启动失败**（除非配置校验拦下），
> 而是让**那一条规则静默不生效**。

### 2.11 alerting rules：`for` 的作用

**官方定义（`configuration/alerting_rules/` 原文）**：

> "Alerting rules allow you to define alert conditions based on Prometheus expression language expressions and to send notifications about firing alerts to an external service. **Whenever the alert expression results in one or more vector elements at a given point in time, the alert counts as active for these elements' label sets.**"

**官方对 `for` 的定义（这段是本节的核心）**：

> "The optional `for` clause causes Prometheus to **wait for a certain duration between first encountering a new expression output vector element and counting an alert as firing** for this element. In this case, Prometheus will check that the alert **continues to be active during each evaluation for 10 minutes before firing the alert**. Elements that are active, but not firing yet, are in the **pending** state. **Alerting rules without the `for` clause will become active on the first evaluation.**"

**官方给的完整示例**：

```yaml
groups:
- name: example
  labels:
    team: myteam
  rules:
  - alert: InstanceDown
    expr: up == 0
    for: 5m
    labels:
      severity: page
    annotations:
      summary: "Instance {{ $labels.instance }} down"
      description: "{{ $labels.instance }} of job {{ $labels.job }} has been down for more than 5 minutes."

  - alert: APIHighRequestLatency
    expr: api_http_request_latencies_second{quantile="0.5"} > 1
    for: 10m
    annotations:
      summary: "High request latency on {{ $labels.instance }}"
      description: "{{ $labels.instance }} has a median request latency above 1s (current value: {{ $value }}s)"
```

**官方对各字段的说明**：

| 字段 | 默认值 | 官方语义 |
|---|---|---|
| `alert` | 必填 | 告警名，**必须是合法标签值** |
| `expr` | 必填 | 求值表达式，**产出的所有序列都成为 pending/firing 告警** |
| `for` | **0s** | "Alerts are considered firing once they have been returned **for this long**." **没写 `for` 的规则在第一次求值就 active** |
| `keep_firing_for` | **0s** | "How long an alert will continue firing **after the condition that triggered it has cleared**." 官方说明它可以防止"**flapping alerts**"和"因数据缺失导致的错误恢复" |
| `labels` | 空 | 附加标签；**"Any existing conflicting labels will be overwritten"**；值可模板化 |
| `annotations` | 空 | 附加信息（描述、手册链接）；值可模板化 |

**官方对 `keep_firing_for` 的定位（原文）**：

> "This can be used to prevent situations such as **flapping alerts**, **false resolutions due to lack of data loss**, etc. Alerting rules without the `keep_firing_for` clause will **deactivate on the first evaluation** where the condition is not met."

**官方给的模板变量**：

| 变量 | 官方说明 |
|---|---|
| `$labels.<labelname>` | 告警实例的标签键值对 |
| `$externalLabels` | 配置的 external labels |
| `$value` | 告警实例的求值结果（**数值**） |

**官方说明告警状态是怎么被观察的**：

> "For pending and firing alerts, Prometheus also stores **synthetic time series** of the form `ALERTS{alertname="<alert name>", alertstate="<pending or firing>", <additional alert labels>}`. The sample value is set to `1` as long as the alert is in the indicated active state, and the series is **marked stale** when this is no longer the case."

**⭐ `for` 为什么是防抖的关键（这是本节要答的问题）**：

```
没有 for：
  t=0    表达式成立 → active → 立刻推送告警
  t=30s  表达式不成立 → inactive → 立刻推送"已恢复"
  t=60s  又成立 → 立刻再推送一次
  → 一个抖动周期产生 2 条通知，且值班的人根本来不及反应

有 for: 5m：
  t=0    表达式成立 → pending（不推送）
  t=1m   仍然成立 → pending
  ...
  t=5m   连续成立满 5 分钟 → firing → 推送
  → 只有"持续 5 分钟的真实故障"才会打扰人
```

> **面试这么答**：
> "`for` 的作用是**防抖动**。
> Prometheus 每 `evaluation_interval` 求值一次，如果条件成立就进入 pending，
> **必须连续成立满 `for` 指定的时长才升级为 firing 并推送通知**。
> 没有 `for` 的规则**第一次求值就 active**，一个瞬时抖动就会产生一条告警加一条恢复。
>
> 我们的经验是：**P0 告警用 `for: 5m` 起**（避免瞬时抖动），
> **但 '实例彻底不可用'（`up == 0`）这种确定性信号可以用更短的 `for` 甚至不用**。
> 另外 Prometheus 3.x 还有 `keep_firing_for`，
> 官方说明它专门用来防 **flapping alerts** 和"数据缺失导致的错误恢复"——
> 条件消失后还会继续 firing 一段时间，避免刚恢复就又挂。

> **⚠️ `for` 与 `irate` 的冲突（跨节关联）**：
> 官方在 `irate` 的说明里明确警告：
> "brief changes in the rate **can reset the `FOR` clause**"。
> → **所以告警表达式里不要用 `irate`**，否则速率的短暂回落会**把 `for` 计时清零**，
> 告警迟迟不触发。这正是官方说"告警用 `rate`"的原因。

---

## 3. 动手

> **动手前提**：本篇**不启动 Prometheus 实例**（遵守资料的数据纪律）。
> 下面是**可直接复制到表达式浏览器（`http://<prometheus>:9090/query`）** 的实验清单，每步写清"看到什么算对"。
> 部署路径见 [`../第2周/第12天.md`](../第2周/第12天.md)。

### 3.1 表达式浏览器的基本操作

| 操作 | 怎么做 | 看到什么算对 |
|---|---|---|
| **先看表格再画图** | 官方建议："always start building the query in the **tabular view** ... until the result set seems reasonable (**hundreds, not thousands, of time series at most**). Only when you have filtered or aggregated your data sufficiently, switch to graph mode." | **Table 页返回的序列数在几百以内** |
| **查原始指标名** | 先访问目标的 `/metrics`，或用 "Explore metrics" 菜单 | 找到确切的指标名和标签 |
| **看查询耗时** | 查询结果页会显示执行时间；API 可加 `stats=all` | 执行时间 < 1s |
| **查标签结构** | 在 Table 页看每一行的 label 集合 | 确认哪些标签存在、取值有哪些 |

> **官方这条建议要当成硬规则**：
> **先在 Table 页确认结果集合理，再切 Graph 页。**
> 直接在 Graph 页拉一个裸指标名，**服务端已经把全量序列算完了**——
> 这就是 L1 里说的"高基数指标导致查询超时"的成因。

### 3.2 用 6 个排障查询走一遍

**实验 1：p95 延迟**

```promql
histogram_quantile(0.95,
  sum by (le, service) (rate(http_request_duration_seconds_bucket[5m]))
)
```

**看到什么算对**：
- Table 页返回**按 `service` 分组的少数几条**，不是几千条
- 每条有 `service` 和 `le` 之外的维度（`le` 被聚合掉了）
- 数值落在**某个 bucket 边界附近**（classic histogram 是插值估算，落在桶内正常）

**故意写错一次**：
```promql
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))
```
**看到什么算对（错的样子）**：返回**每个实例一条**，数量 = 实例数。
→ 这就是"**直接对多实例算**"的样子。**它不报错，但答案是错的**。

**实验 2：错误率（先求和再相除）**

```promql
sum by (service) (rate(http_requests_total{code=~"5.."}[5m]))
  /
sum by (service) (rate(http_requests_total[5m]))
```

**对照实验**：
```promql
sum by (service) (
  rate(http_requests_total{code=~"5.."}[5m])
  /
  rate(http_requests_total[5m])
)
```

**看到什么算对（对比结论）**：
- 第一个查询：每个 service 一条，值在 0-1 之间，且**不同 service 之间可以直接比较**
- 第二个查询：**每个实例一条**（因为 `by (service)` 在外层，但 `rate` 的分母没聚合 → 实际上会因标签集不匹配而返回空或错乱）
- **再叠加第三种错误**：
  ```promql
  avg by (service) (
    rate(http_requests_total{code=~"5.."}[5m])
    /
    rate(http_requests_total[5m])
  )
  ```
  → **这个能跑通**，但值明显偏离第一个查询。**这就是最危险的那种错。**

**实验 3：QPS**

```promql
sum by (service) (rate(http_requests_total[5m]))
```

**看到什么算对**：每个 service 一条，值是个合理的小数（次/秒）。

**实验 4：CPU 饱和（throttling）**

```promql
sum by (pod, namespace) (rate(container_cpu_cfs_throttled_seconds_total[5m]))
```

**看到什么算对**：有值的 pod 说明"曾被限流"；全 0 说明没有限流（CPU 够用）。

**实验 5：重启次数**

```promql
sum by (pod, namespace) (increase(kube_pod_container_status_restarts_total[1h]))
```

**看到什么算对**：整数；不重启的 pod 是 0（**不是空**——空说明指标名写错了）。

**实验 6：内存增长趋势**

```promql
predict_linear(container_memory_working_set_bytes{namespace="demo"}[6h], 4*3600) < 0
```

**看到什么算对**：没有即将写满的 pod 时返回**空结果**（不是 0）；有风险的 pod 返回该 pod 的标签集。

### 3.3 验证清单

| 检查项 | 查询 | 期望 |
|---|---|---|
| **聚合粒度正确** | `count by (__name__) ({__name__=~"http_.*"})` | 数量在合理范围 |
| **没有裸指标直查** | 看板里搜 `http_requests_total`（不带任何函数） | **不应该有**——裸查会拉全量序列 |
| **比例都是先聚合后相除** | 检查所有带 `/` 的查询 | 分子分母的 `sum by` 维度**完全一致** |
| **直方图分位数都带 `le`** | 检查所有 `histogram_quantile` | 前一层一定有 `sum by (le, ...)` |
| **告警都有 `for`** | 检查所有 alerting rule | 除"实例彻底不可用"外都有 `for` |
| **规则语法正确** | `promtool check rules <file>` | 退出码 0 |

---

### 3.4 常用运维命令（按场景）

> **本节能做什么**：3.2 是"在浏览器里写查询"，本节是**不用 UI、纯命令行就能把 PromQL 跑起来、验规则、查元数据的运维命令**——值班和面试都考。**PromQL 排障的肌肉记忆就来自这一张表。**

### ① 用 API 跑 PromQL（命令行版表达式浏览器）

```bash
curl -s 'http://localhost:9090/api/v1/query?query=up'                                  # 瞬时查询
curl -s 'http://localhost:9090/api/v1/query_range?query=rate(http_requests_total[5m])&start=1700000000&end=1700003600&step=60'  # 区间查询
curl -s 'http://localhost:9090/api/v1/query?query=topk(5,rate(http_requests_total[5m]))' # 直接验证聚合/排序
```

> **实战价值**：`/api/v1/query` 和 `/query_range` 就是表达式浏览器的底层接口，**面试被问"不打开 UI 怎么验证一条 PromQL"就答这两个**。把复杂查询先在命令行跑通，再贴进看板，能少走很多弯路。

### ② 规则语法与单元测试（改完规则先过这一关）

```bash
promtool check rules /etc/prometheus/rules/*.yml        # 校验告警/记录规则语法（rate 窗口、for、labels）
promtool test rules /etc/prometheus/rules/tests/*.yml   # 用样本数据跑规则单元测试（CI 里必接）
promtool check config /etc/prometheus/prometheus.yml    # 顺带确认主配置也 OK
```

> **实战价值**：`promtool check rules` 能在提交前挡掉 `for` 写错、`rate` 窗口过短这类低级错误；`promtool test rules` 用历史样本验证"这条告警在事故当天会不会触发"——**这是把告警质量纳入 CI 的标准做法，面试高频考点**。

### ③ 即时元数据查询（排"查不到"的归属）

```bash
curl -s 'http://localhost:9090/api/v1/labels' | head -c 400                 # 看有哪些标签键
curl -s 'http://localhost:9090/api/v1/label/__name__/values' | head -c 400  # 看有哪些指标名（高基数排查入口）
curl -s 'http://localhost:9090/api/v1/series?match[]=up' | head -c 400       # 看某个 selector 命中的真实序列
curl -s 'http://localhost:9090/api/v1/query?query=count by (__name__)({__name__=~"http.+"})'  # 维度数
```

> **实战价值**：查询返回空，**先别改查询**——用 `/api/v1/series` 确认 selector 到底命没命中序列，用 `/label/__name__/values` 看指标名是不是拼错（大小写、复数）。空结果 90% 是"标签值/指标名写错"，不是 PromQL 写错。

### ④ 高基数与性能排查（查询慢的真凶）

```bash
curl -s 'http://localhost:9090/api/v1/label/__name__/values' | tr ',' '\n' | wc -l   # 指标名总数
promtool tsdb analyze /var/lib/prometheus/data 2>/dev/null | grep -iE 'top|cardinality' | head  # 找高基数指标
curl -s 'http://localhost:9090/api/v1/query?query=count by (__name__)({__name__!=""})'          # 全量 series 数
```

> **实战价值**：看板卡、查询超时，多半是某条查询扫了海量 series。**先定位高基数指标名**，再回到查询里加更精确的标签过滤或改用 `recording rules` 预计算（§2.10）——这也是面试"PromQL 怎么优化"的标准答法。

### ⑤ 告警 / 记录规则生效验证

```bash
curl -s 'http://localhost:9090/api/v1/rules' | head -c 600          # 看当前加载的规则、状态、上一次求值
curl -s 'http://localhost:9090/api/v1/alerts' | head -c 600        # 看当前 active 的告警（pending/firing）
curl -s 'http://localhost:9090/api/v1/query?query=ALERTS'          # 用 ALERTS 指标查历史告警状态
```

> **实战价值**：规则写进文件不等于生效——`/api/v1/rules` 看"加载了没、上次求值报错没"，`/api/v1/alerts` 看"到底 firing 了没"。**告警没来，先查 rules 的 `lastEvaluation` 和 `evaluationErrors`，别盯看板。**

### ⑥ 通用：端口、进程、配置校验

```bash
ss -ltnp | grep 9090                       # 端口
ps aux | grep prometheus | grep -v grep     # 进程与启动参数（--config.file、--web.enable-lifecycle）
promtool check config /etc/prometheus/prometheus.yml   # 任何改动前的第一道闸
```

> **实战价值**：`promtool check config` 是 Prometheus 运维的"安全带"，**改任何东西之前先跑它**，比 reload 后救火便宜得多。

---

### 3.5 运维排障实战（场景串命令）

> **把 3.4 的命令用起来**：三个最典型的 PromQL 故障，按"现象 → 敲的命令 → 怎么读"走一遍。**建议在表达式浏览器 + 命令行对照练，建立"同一个查询两种入口"的手感。**

### 场景 A：查询返回空结果（怀疑 PromQL 写错）

```bash
curl -s 'http://localhost:9090/api/v1/query?query=http_requests_total'     # 先裸查指标名
curl -s 'http://localhost:9090/api/v1/series?match[]=http_requests_total'  # 看命中的真实标签集
curl -s 'http://localhost:9090/api/v1/label/__name__/values' | grep -i http # 确认指标名拼对了
# 典型：写成 http_request_total（少 s）→ 空；或标签值写 'GET' 实际是 'get'
```

> **读法**：空结果**先查 selector 命没命中**，不是急着改函数。用 `/series` 看真实标签，`/label/__name__/values` 看指标名拼写。**PromQL 不报错，它给你空——这是它最坑的地方（§1.4），必须用 API 把"到底有没有这个序列"坐实。**

### 场景 B：rate 算出来是 0 或结果明显不对

```bash
curl -s 'http://localhost:9090/api/v1/query?query=rate(http_requests_total[1m])'   # 窗口太短
curl -s 'http://localhost:9090/api/v1/query?query=rate(http_requests_total[5m])'   # 加长到 4 个样本以上
curl -s 'http://localhost:9090/api/v1/query?query=increase(http_requests_total[5m])' # 对比 increase
# 典型：窗口 1m 但 scrape_interval 30s → 只有 2 个样本，rate 不稳；counter 刚重置导致断崖
```

> **读法**：`rate` 窗口至少要覆盖 **4 个 scrape 间隔**（§2.4.2）。窗口太短→样本不足→结果抖动或归零；counter 发生重置→`rate` 会自动处理（§2.4.3），但若用 `irate` 在重置边界会跳。**先加长窗口再下结论。**

### 场景 C：告警规则写好了，但事故当天没 firing

```bash
curl -s 'http://localhost:9090/api/v1/rules' | python3 -m json.tool | grep -A6 '"name":"HighErrorRate"'  # 看规则状态
curl -s 'http://localhost:9090/api/v1/alerts'                                                       # 看 active 告警
curl -s 'http://localhost:9090/api/v1/query?query=rate(http_errors_total[5m]) / rate(http_requests_total[5m]) > 0.05'  # 手动复算条件
# 典型：for: 10m 但事故只持续 3 分钟 → 永远 pending；或分子分母 sum by 维度不一致
```

> **读法**：告警不 firing，先 `/rules` 看 `evaluationErrors`（表达式本身报错）和 `lastEvaluation`（有没有在求值）；再手动跑条件确认"事故窗口里到底满不满足"。**`for` 设太长 + 故障太短 = 永远 pending**，这是实战最常见的"漏报"原因（对照 §2.11）。

> **三个场景的共同心法**：**先用 API 把"有没有这个序列 / 条件到底满不满足"坐实，再谈改查询**。PromQL 的坑不是语法报错，是"安静地给你错的数"——命令行 + 手动复算是最好的照妖镜。

---

## 4. 逐字段表

### 4.1 函数表

| 函数 | 类型（输入 → 输出） | 语义 | 典型用途 | 陷阱 |
|---|---|---|---|---|
| `rate(v range-vector)` | 范围向量 → **瞬时向量** | 每秒**平均**增长率；**自动修正 counter 重置**；外推到窗口两端 | **告警**、QPS、错误率 | ① 窗口太小（至少 4 个抓取间隔）② **必须先 rate 再聚合**，否则检测不到重置 ③ 只能用于 counter |
| `irate(v range-vector)` | 范围向量 → **瞬时向量** | 基于**最后两个数据点**的每秒瞬时增长率；自动修正重置 | **画快变 counter 的图** | ① **官方明确：告警别用**（短暂变化会 reset `FOR`）② 只用最后两点 → 极易抖 ③ 同样要先 irate 再聚合 |
| `increase(v range-vector)` | 范围向量 → **瞬时向量** | 窗口内**总增量**；= `rate(v) × 窗口秒数`；自动修正重置 | **人类可读**的"这段时间发生了几次" | ① recording rule 里官方要求用 `rate` ② 窗口太小会失真 |
| `avg_over_time(range-vector)` | 范围向量 → **瞬时向量** | 区间平均值；**所有值等权重**（不管间隔是否相等） | 平均延迟（**仅单实例参考**） | ① **等权重**——漏抓取会失真 ② **不能用它代替分位数** |
| `sum_over_time(range-vector)` | 范围向量 → **瞬时向量** | 区间总和；等权重 | gauge 的区间累计 | ① 对 counter 用它 = 拿累计值当增量（应改 `increase`） |
| `max_over_time(range-vector)` | 范围向量 → **瞬时向量** | 区间内最大值（只处理 float） | 峰值内存、峰值队列 | ① **histogram 样本被忽略**，纯 histogram 区间会**静默移除该序列** |
| `min_over_time(range-vector)` | 范围向量 → **瞬时向量** | 区间内最小值（只处理 float） | 最低可用内存 | 同上 |
| `count_over_time(range-vector)` | 范围向量 → **瞬时向量** | 区间内**样本数** | **健康度/保活判断** | ① 样本数受抓取间隔影响，不能直接当"请求数" |
| `quantile_over_time(scalar, range-vector)` | 标量 + 范围向量 → **瞬时向量** | 区间内 **φ-分位数**（0≤φ≤1）；只处理 float | **单实例**延迟分位数 | ① **不能跨实例聚合** ② histogram 样本被忽略 |
| `stddev_over_time(range-vector)` | 范围向量 → **瞬时向量** | 区间内**总体**标准差；只处理 float | 抖动程度 | ① 是**总体**标准差（不是样本标准差）② 只处理 float |
| `stdvar_over_time(range-vector)` | 范围向量 → **瞬时向量** | 区间内**总体**方差；只处理 float | 同上 | 同上 |
| `changes(v range-vector)` | 范围向量 → **瞬时向量** | 区间内值**变化次数** | 配置热更新次数、指标震荡 | ① 别用于 counter（会因正常增长一直变） |
| `idelta(v range-vector)` | 范围向量 → **瞬时向量** | **最后两个**样本之差；**只适用 gauge** | 瞬时增量 | ① 对 counter 用它 = 忽略重置 ② 只取最后两点 |
| `delta(v range-vector)` | 范围向量 → **瞬时向量** | **首尾**样本之差（外推到完整区间）；**只适用 gauge** | 区间净变化 | ① 对 counter 用它 = 重启后会得到大负数 ② 只适用 gauge |
| `resets(v range-vector)` | 范围向量 → **瞬时向量** | 区间内 **counter 重置次数** | 重启次数 | ① 只适用 counter |
| `deriv(v range-vector)` | 范围向量 → **瞬时向量** | **简单线性回归**求每秒导数；**至少 2 个 float 样本**；**只适用 gauge** | 资源增长速率 | ① 对 counter 用它 = 得到 0 ② 区间含 `±Inf` → `NaN` |
| `predict_linear(v range-vector, t scalar)` | 范围向量 + 标量 → **瞬时向量** | 线性回归预测**从当前起 `t` 秒后**的值；**至少 2 个 float 样本**；**只适用 gauge** | 磁盘/内存会不会满 | ① **线性假设**——有周期性时严重失真 ② 只适用 gauge ③ 至少 2 个样本 |
| `histogram_quantile(φ scalar, b instant-vector)` | 标量 + 瞬时向量 → **瞬时向量** | classic/native histogram 的 φ-分位数；**每个样本必须带 `le` 标签** | **p95/p99 延迟** | ① **必须先 `sum by (le, ...)` 聚合** ② 少于两个桶 → `NaN` ③ 最高桶不是 `+Inf` → `NaN` ④ **不能用于 summary** |
| `histogram_count(v instant-vector)` | 瞬时向量 → 瞬时向量 | native histogram 的观测数 | 平均值计算 | ① **只处理 native histogram**，float 样本被忽略 |
| `histogram_sum(v instant-vector)` | 瞬时向量 → 瞬时向量 | native histogram 的观测总和 | 平均值计算 | 同上 |
| `absent(v instant-vector)` | 瞬时向量 → 瞬时向量 | **有元素→空向量；无元素→值为 1 的单元素向量** | 目标/指标彻底缺失告警 | ① **会尝试推导标签**，聚合后标签会丢 ② 返回空向量时**不能直接和数字比较** |
| `absent_over_time(v range-vector)` | 范围向量 → 瞬时向量 | 区间内**无样本**→值为 1 | 容忍抖动的缺失检测 | 同上 |
| `label_replace(v, dst, replacement, src, regex)` | 瞬时向量 → 瞬时向量 | 正则匹配 `src` 的值，命中则写入 `dst` | 从复合标签拆新维度 | ① **不匹配时原样返回**（静默无效）② 正则**完全锚定** |
| `label_join(v, dst, separator, src...)` | 瞬时向量 → 瞬时向量 | 用分隔符连接多个标签值写入 `dst` | 拼复合键 | ① 源标签不存在时值为空串 |
| `vector(s scalar)` | 标量 → **瞬时向量** | 标量转成**无标签**的单元素瞬时向量 | 构造阈值向量 | ① **无标签** → 与带标签的向量比较时注意匹配 |
| `scalar(v instant-vector)` | 瞬时向量 → **标量** | **恰好一个** float 元素时返回值，**否则 NaN** | 取唯一值 | ① **多元素/空元素→NaN**，NaN 参与比较静默失败 |
| `sort(v instant-vector)` | 瞬时向量 → 瞬时向量 | 按值**升序**排列 | 表格展示 | ① **只影响 instant query 的顺序**，range query 顺序固定 ② histogram 样本被忽略 |
| `sort_desc(v instant-vector)` | 瞬时向量 → 瞬时向量 | 按值**降序**排列 | 找出最大的 | 同上 |
| `time()` | — → 标量 | 返回自 1970-01-01 UTC 起的秒数 | 算时间差 | ① **返回的是表达式求值时刻**，不是"现在" |
| `timestamp(v instant-vector)` | 瞬时向量 → 瞬时向量 | 返回每个样本的时间戳 | 取事件发生时刻 | ① 是**样本自带**的时间戳，不是抓取时刻 |
| `clamp(v, min, max)` | 瞬时向量 → 瞬时向量 | 把值限制在 `[min, max]` | 限幅 | ① `min > max` → **返回空向量** |
| `clamp_max(v, max)` / `clamp_min(v, min)` | 瞬时向量 → 瞬时向量 | 单侧限幅 | 同上 | ① NaN 边界 → NaN |
| `abs(v instant-vector)` | 瞬时向量 → 瞬时向量 | 取绝对值 | 处理负增长 | ① **会掩盖"负增长"这个信号本身** |
| `topk(k, v)` / `bottomk(k, v)` | **聚合算子**（不是函数） | 返回按值排序的 k 个元素，**保留原始标签**；`by`/`without` 只用于分桶 | 找出 Top N / Bottom N | ① **range query 里不排序** ② 只处理 float |
| `count_values(l, v)` | **聚合算子** | 每个**唯一取值**产出一条序列，标签 `l` = 该取值，值 = 出现次数 | 按版本/状态码统计 | ① 取值基数大时序列爆炸 |
| `sum` / `avg` / `min` / `max` / `count` / `stddev` / `stdvar` / `quantile` | **聚合算子** | 标准聚合 | 各种汇总 | ① **`by` 漏写→维度错乱** ② 混用 float 与 histogram → 元素被移除 + warn annotation |
| `and` / `or` / `unless` | **集合运算符** | 交集 / 并集（左侧优先）/ 左侧减右侧；**要求标签集完全相同** | 多条件组合、缺失检测 | ① **不能用 `on()`/`ignoring()`** ② 两侧必须用同样的聚合方式才能匹配上 |

### 4.2 排障查询模板表

| 场景 | 查询 | 读法 |
|---|---|---|
| **p95 延迟** | `histogram_quantile(0.95, sum by (le, service) (rate(http_request_duration_seconds_bucket[5m])))` | `le` 必须保留（桶边界）；`service` 是输出粒度；`[5m]` 至少 4 个抓取周期；结果是**插值估算** |
| **错误率** | `sum by (service) (rate(http_requests_total{code=~"5.."}[5m])) / sum by (service) (rate(http_requests_total[5m]))` | **分子分母必须先分别求和再相除**；两侧 `sum by` 的维度必须**完全一致** |
| **QPS** | `sum by (service) (rate(http_requests_total[5m]))` | 值 = 每秒请求数；对比历史看趋势 |
| **CPU 饱和度** | `sum by (pod) (rate(container_cpu_cfs_throttled_seconds_total[5m]))` | **被限流 = 想跑跑不了**，比 CPU 使用率更接近"真的不够用"；0 = 没有限流 |
| **内存使用** | `sum by (pod) (container_memory_working_set_bytes{namespace="demo"})` | `working_set` 是**真实占用**（不含可回收缓存），比 `container_memory_usage_bytes` 更准 |
| **内存增长趋势** | `deriv(container_memory_working_set_bytes[6h]) * 86400 / 1024 / 1024` | **每秒斜率 × 一天** = 每天增长多少 MB；只适用 gauge；线性假设 |
| **磁盘会不会满** | `predict_linear(node_filesystem_avail_bytes{mountpoint="/data"}[6h], 4*3600) < 0` | **用过去 6 小时趋势外推 4 小时**，预测值 < 0 就是会写满；有周期性的盘会失真 |
| **重启次数** | `sum by (pod) (increase(kube_pod_container_status_restarts_total[1h]))` | 整数；**0 = 没重启**（不是空）；`increase` 自动处理 counter 重置 |
| **进程存活时长** | `time() - process_start_time_seconds` | 官方推荐导出**时间戳**而不是"已过时长"；突然变小 = 刚重启 |
| **目标是否可达** | `up{job="my-service"}` | 1 = 抓取成功；0 = 抓取失败；**全 0 说明 Prometheus 或网络侧有问题** |
| **抓取成功率** | `avg_over_time(up[1h])` | 1.0 = 一小时全成功；0.99 = 约 36 分钟有失败 |
| **指标彻底缺失** | `absent(http_requests_total{job="my-service"})` | 有值 1 = **服务活着但指标没了**（埋点坏了或被 relabel drop） |
| **一段时间无数据** | `absent_over_time(http_requests_total{job="my-service"}[10m])` | 容忍短暂抖动；比 `absent` 更适合做告警 |
| **配置热更新次数** | `changes(process_config_last_reload_success_timestamp_seconds[1h])` | 值 = 一小时内变了多少次 |
| **Top N 内存 Pod** | `topk(5, sum by (pod) (container_memory_working_set_bytes{namespace="demo"}))` | 保留原始标签；**range query 里不排序**（只 instant query 有效） |
| **按版本统计实例数** | `count_values("version", build_info)` | 标签 `version` = 原指标的值；值 = 出现次数 |
| **找出有流量但报错的实例** | `sum by (instance) (rate(http_requests_total[5m])) and sum by (instance) (rate(http_errors_total[5m]))` | **两侧必须用同样的聚合方式**，否则标签集对不上、匹配不上 |
| **找出注册了但没心跳的** | `count by (service) (service_registered_total) unless count by (service) (service_heartbeat_total)` | `unless` 要求**标签集完全相同** |

---

## 5. 横向对比

### 5.1 rate vs irate vs increase

| 维度 | **`rate`** | **`irate`** | **`increase`** |
|---|---|---|---|
| **官方定位** | "**best suited for alerting**, and for graphing of slow-moving counters" | "should only be used when **graphing volatile, fast-moving counters**" | "should be used **primarily for human readability**"；"**Use `rate` in recording rules**" |
| **数学含义** | 窗口内首尾差 ÷ 窗口时长（外推） | **最后两点**之差 ÷ 两点间隔 | 窗口内首尾差（外推到完整窗口） |
| **用到几个点** | 全窗口 | **2 个** | 全窗口 |
| **平滑度** | **平滑** | **抖** | 平滑 |
| **对漏抓取/重启鲁棒** | **强** | **弱** | 强 |
| **counter 重置** | **自动修正** | **自动修正** | **自动修正** |
| **输出量纲** | 每秒 | 每秒 | 窗口内总量 |
| **关系** | `increase = rate × 窗口秒数` | — | — |
| **告警** | ✅ **用这个** | ❌ 官方说会 reset `FOR` | ⚠️ 可读性好，但规则里用 rate |
| **看板** | ✅ 慢变 counter | ✅ **快变 counter** | 一般不直接画 |

> **选型一句话**：
> **"要告警用 `rate`；要画剧烈波动的图用 `irate`；要跟人说'这一小时发生了多少次'用 `increase`。"**

### 5.2 `sum by` vs `sum without`

| 维度 | `sum by (...)` | `sum without (...)` |
|---|---|---|
| **官方定义** | "**drops labels that are not listed** in the `by` clause, **even if their label values are identical between all elements**" | "**removes the listed labels** from the result vector, while **all other labels are preserved**" |
| **保留什么** | **只有**列出的标签 | **除列出之外的全部** |
| **输出维度可预测性** | **强**——新增标签不会污染输出 | **弱**——上游加个标签，输出就多一维 |
| **官方等价关系** | `sum by (application, group) (x)` ≡ `sum without (instance) (x)`（当标签正好是这三个时） | 同左 |
| **官方 recording rule 推荐** | — | ✅ 官方原文："**Always specify a `without` clause** with the labels you are aggregating away. This is to preserve all the other labels such as `job`" |
| **典型场景** | 输出维度**固定已知**（如只看 service + method） | **生产默认**：想聚合掉 `instance` 但保留 `job`/`namespace`/`cluster` |

> **选型一句话**：
> **"输出维度固定、且希望新增标签不影响结果 → `by`；生产环境想保留 `job`/`cluster` 等上下文 → `without`。"**

### 5.3 instant vs range

| 维度 | **instant query** | **range query** |
|---|---|---|
| **官方定义** | "evaluated at one point in time" | "at equally-spaced steps between a start and an end time" |
| **本质** | 求一次 | "**the range query is just like an instant query run multiple times at different timestamps**" |
| **API** | `/api/v1/query`（参数 `query` + `time`） | `/api/v1/query_range`（参数 `query` + `start` + `end` + `step`） |
| **返回类型** | `vector` / `scalar` / `string` | `matrix` |
| **UI** | **Table** 页 | **Graph** 页 |
| **典型用途** | **告警求值**、API 巡检、查当前值 | **画图**、看趋势、做前后对比 |
| **`step` 的影响** | 无 | 决定数据点密度；**服务端计算量随点线性增长** |
| **`sort` / `topk` 排序** | ✅ **生效** | ❌ **不排序**（官方原文："No sorting applies to range queries"） |
| **成本** | 低 | **高**——每个 step 都要重算整个表达式 |

> **选型一句话**：
> **"问'现在怎样'用 instant；问'刚才那段时间怎么变的'用 range。告警天然是 instant，看板天然是 range。"**

### 5.4 recording rules vs 每次看板实时算

| 维度 | **recording rules（预计算）** | **看板实时算** |
|---|---|---|
| **官方定位** | "**precompute frequently needed or computationally expensive expressions** ... Querying the precomputed result will then often be **much faster**" | 每次面板刷新都执行完整表达式 |
| **计算时机** | **服务端按 `evaluation_interval` 定期算好并落成新时间序列** | **面板刷新时算** |
| **查询成本** | **低**（查一条已存好的序列） | **高**（每次都扫全量原始序列） |
| **数据延迟 | **最多一个 `evaluation_interval`** | **无额外延迟** |
| **可复用性** | ✅ **同一表达式被多个看板/告警共用** | ❌ 每个看板各算一遍 |
| **可观测性** | ✅ 可以直接 `prometheus_tsdb_*` 看到序列 | — |
| **副作用** | **规则评估超时会跳过 → 指标有空洞**（官方：`rule_group_iterations_missed_total`） | 无 |
| **存储成本** | 多一份序列 | 无 |
| **命名负担** | 必须遵守 `level:metric:operations` 规范 | 无 |

> **官方对"什么时候该用 recording rule"的判断（原文）**：
> "This is especially useful for **dashboards, which need to query the same expression repeatedly every time they refresh**."
>
> **还有一条来自 `querying/basics/` 的慢查询建议**：
> "If the expression still takes too long to graph ad-hoc, **pre-record it via a recording rule**."
> （如果表达式实时画图还是很慢，就用 recording rule 预录。）

> **选型一句话**：
> **"同一个复杂表达式被多处使用、或实时算已经慢了 → 上 recording rule；只是一次性排查、要求零延迟 → 直接算。"**
>
> **实战顺序**：先直接算 → 发现慢 → 上 recording rule → **告警规则也改成引用 recording rule**
> （这样告警和看板的口径天然一致，不会出现"看板一个数、告警另一个数"）。

---

## 6. 踩坑与排障（症状 → 状态 → 真实原因 → 怎么修 → 下次怎么防）

**排障铁律：先看结果**条数**对不对，再看数值对不对。**

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **`rate()` 返回空结果** | 查询无数据 | **窗口内样本不足**——窗口小于抓取间隔，或目标刚启动还没攒够样本 | 窗口至少 **4 个抓取间隔**（15s 抓取 → 至少 `[1m]`）；确认抓取间隔（查 `prometheus_target_interval_length_seconds`） | **窗口 ≥ 4× 抓取间隔写进规范**；新服务上线后先观察几轮再配告警 |
| **曲线锯齿剧烈 / 数值乱跳** | 有数据但抖 | 窗口太小，样本太少 → 速率估计方差大 | 放大窗口到 `[5m]`；或改用 `irate`（但只用于画图） | 告警用 `rate` 且窗口 ≥ 4× 抓取间隔 |
| **`rate()` 结果为负数** | 负值 | **对 gauge 用了 `rate`**（官方："You should never take a `rate()` of a gauge"）；或指标实际是 gauge 但命名成了 `_total` | 改用 `avg_over_time` / `max_over_time` / `delta`；核对 `# TYPE` 定义 | **埋点时对照官方规则选类型**；命名带 `_total` 的一定是 counter |
| **counter 重启后出现尖峰** | 单点尖峰 | 用裸差值（`x - x offset 5m`）而不是 `rate`/`increase` | 一律改用 `rate` / `increase`（官方：两者都会自动修正重置） | **禁止裸 counter 相减**；用 `resets()` 交叉验证 |
| **`histogram_quantile` 返回 `NaN`** | NaN | ① 桶**少于两个** ② **最高桶不是 `+Inf`** ③ 窗口内 0 个 observations ④ **直接对多实例算**（桶被混了） | ① 检查埋点的桶配置是否含 `+Inf` ② 前置 `sum by (le, ...)` ③ 放大窗口 | **埋点时必须带 `+Inf` 桶**；分位数查询写死"先 sum by (le) 再算" |
| **P99 比真实值偏小** | 数值偏小 | **直接对多实例算**（每个实例各算 P99，没合并） | `histogram_quantile(0.99, sum by (le, ...) (rate(x_bucket[5m])))` | **模板固化**：分位数查询一律带 `sum by (le, ...)` |
| **P99 出现 `input to histogram_quantile needed to be fixed for monotonicity` 提示** | annotation | **桶计数不单调**——通常是**两个不同 bucket 边界的 histogram 被混在一起聚合了** | 排查数据源：确认所有实例的桶边界**完全一致** | **统一桶边界**；改用 native histogram（官方：其 bucketing schema 保证永远可聚合） |
| **错误率算出来比真实值大很多** | 数值偏大 | **比例先算后聚合**（官方明文禁止："Do not take the average of a ratio"） | `sum(rate(err[5m])) / sum(rate(total[5m]))` | **分子分母分别 recording，再相除** |
| **错误率算出来是 0，告警不触发** | 恒为 0 | ① 错误码标签写错（如实际是 `status` 不是 `code`）② **分子分母聚合维度不一致** → 匹配失败返回空 ③ 被 `metric_relabel_configs` drop 了 | ① 核对标签名 ② 两侧 `sum by` 维度改成完全一致 ③ 查 relabel | **模板里固定标签名**；写完查一次返回条数 |
| **聚合结果条数比预期多得多** | 返回 N 条而非 1 条 | **`by` 漏写**（或写错）——返回的是每个标签组合一条 | 补全 `by (dim1, dim2)` | **写完先在 Table 页数条数**（官方建议） |
| **聚合结果只有 1 条，本该按服务下钻** | 返回 1 条 | **`by` 多写了维度**（把所有维度都聚合掉了） | 减少 `by` 里的维度 | 明确写出"输出粒度"再写 `by` |
| **两个指标相除返回空结果** | 无数据 | **标签集不匹配**（一边有 `instance` 一边没有） | 两侧用同样的 `sum by (...)`，或用 `on(...)` / `ignoring(...)` | **写除法前先确认两侧标签集一致**（官方建议先试 `ignoring()`） |
| **相除报 "many-to-many matching not allowed"** | 报错 | 两侧都在"多"的一侧（多个序列匹配多个序列） | 加 `group_left` / `group_right`（**记住哪边多用哪个词**）；或先 `sum` 掉多余维度 | **优先用 `ignoring()` 或先聚合**（官方原话："Often a proper use of `ignoring(<labels>)` provides the desired outcome"） |
| **range vector 和 instant vector 混用报错** | 语法/类型错误 | 把 `x[5m]`（范围向量）直接参与二元运算或聚合 | 先用函数压扁：`rate(x[5m])` / `avg_over_time(x[5m])`；或用子查询 `sum(x)[1h:5m]` | **记住口诀：带方括号是范围向量，函数负责压扁** |
| **高基数指标导致查询超时（503）** | 查询超时 | 表达式**扫了太多序列**——官方警告："a bare metric name selector like `api_http_requests_total` could expand to **thousands of time series**"，且"expressions that aggregate over many time series will generate load on the server **even if the output is only a small number of time series**" | ① 先加过滤条件 ② 再聚合 ③ 还是慢就上 recording rule ④ 用 `sample_limit` / `target_limit` 兜底 | **官方硬规则：先在 Table 页确认结果集合理再切 Graph 页** |
| **告警反复触发（抖动）** | 频繁 firing/resolved | **告警规则缺 `for`**——第一次求值就 active | 加 `for: 5m`（或按 severity 分级） | **每条告警都必须有 `for`**（除"实例彻底不可用"） |
| **告警迟迟不触发** | 一直 pending | ① 用了 `irate`（官方：短暂变化会 **reset the FOR clause**）② `for` 太长 ③ 表达式本身波动 | 改用 `rate`；调整 `for`；检查表达式的稳定性 | **告警只用 `rate`**，不用 `irate` |
| **告警恢复后又立刻重新触发** | flapping | 条件在阈值附近反复穿越 | 用 `keep_firing_for: 5m`（官方：专门防 flapping alerts 和"数据缺失导致的错误恢复"） | 抖动频繁的告警统一加 `keep_firing_for` |
| **recording rule 产出的指标有空洞** | 数据断档 | **规则评估超时被跳过**（官方机制）——`rule_group_iterations_missed_total` 递增 | 拆分规则组、缩小 `interval`、简化表达式 | **监控 `rule_group_iterations_missed_total`**；用 `promtool check rules` 做 CI 检查 |
| **改了规则文件没生效** | 无变化 | 规则文件语法错误 → **整份文件不被加载**；或没 reload | `promtool check rules <file>` 检查；`kill -s SIGHUP <PID>` 热加载（官方："The changes are only applied if **all** rule files are well-formed"） | **CI 里跑 `promtool check rules`** |
| **`scalar()` 得到 NaN** | NaN | 输入向量**不是恰好一个元素**（官方："otherwise returns `NaN`"） | 先 `count(x)` 确认；或改用 `avg`/`max` | 先确认唯一性再用 `scalar()` |
| **`sort_desc` 在图上没效果** | 顺序不变 | **官方限制**："Only affects instant query's result order. **Range query's result always has a fixed output order.**" | 改用表格面板（instant query）；或用 `topk` | 排序需求用 `topk` + 表格面板 |
| **`absent()` 返回空向量，告警不触发** | 空 | 目标**还在**（有元素 → 官方定义返回空向量） | 用 `absent_over_time(x[10m])` 容忍抖动 | 区分"彻底没了"和"暂时没了"两种告警 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `prometheus.io/docs/prometheus/latest/querying/basics/` | 四种数据类型、instant vs range、选择器与四种匹配器、正则锚定、空标签语义、`__name__`、`offset` / `@` 修饰符、子查询、staleness、lookback 5m、慢查询建议 | ✅ 2026-10-24 |
| S2 | 官方 | `prometheus.io/docs/prometheus/latest/querying/functions/` | 全部函数签名与官方注意事项（rate/irate/increase/`_over_time` 家族/histogram_quantile/label_replace/label_join/absent/changes/deriv/predict_linear/sort/scalar/vector/resets/delta/idelta） | ✅ 2026-10-24 |
| S3 | 官方 | `prometheus.io/docs/prometheus/latest/querying/operators/` | 二元运算符、集合运算符 `and`/`or`/`unless`、向量匹配 `on`/`ignoring`、`group_left`/`group_right`、聚合算子与 `by`/`without` 精确语义、`topk`/`bottomk`/`count_values`、指标名丢弃规则 | ✅ 2026-10-24 |
| S4 | 官方 | `prometheus.io/docs/prometheus/latest/configuration/recording_rules/` | recording / alerting rule 字段与默认值、`rule_group` 的 `interval`/`limit`/`query_offset`、`promtool check rules`、评估超时跳过的官方机制 | ✅ 2026-10-24 |
| S5 | 官方 | `prometheus.io/docs/prometheus/latest/configuration/alerting_rules/` | `for` / `keep_firing_for` 的官方语义、pending/firing 状态、模板变量、`ALERTS` 合成序列 | ✅ 2026-10-24 |
| S6 | 官方 | `prometheus.io/docs/practices/rules/` | recording rule 命名规范 `level:metric:operations`、**分子分母分别聚合再相除**、优先用 `without`、官方完整示例 | ✅ 2026-10-24 |
| S7 | 官方 | `prometheus.io/docs/practices/histograms/` | Histogram vs Summary、**坏例子 `avg(...{quantile="0.95"})` 与好例子**、聚合规则选择 | ✅ 2026-10-24 |
| S8 | 官方 | `prometheus.io/docs/practices/instrumentation/` | counter vs gauge 的选择规则、**"You should never take a `rate()` of a gauge"**、裸 counter 几乎没用、标签基数建议（<10）、时间戳而非"已过时长" | ✅ 2026-10-24 |
| S9 | 官方 | `prometheus.io/docs/prometheus/latest/querying/api/` | `/api/v1/query` 与 `/api/v1/query_range` 参数、resultType、错误码 400/422/503 | ✅ 2026-10-24 |
| S10 | 官方 | `prometheus.io/docs/prometheus/latest/configuration/configuration/` | `evaluation_interval` 默认 `1m`、`global.scrape_interval` 默认 `1m`（口径交叉验证） | ✅ 2026-10-24 |
| S11 | 官方 | `prometheus.io/docs/concepts/metric_types/` | Counter / Gauge / Histogram / Summary 语义（与 L1 口径一致） | ✅ 2026-10-24 |
| S12 | 官方 | `prometheus.io/docs/introduction/overview/` | PromQL 定位（对照用） | ✅ 2026-10-24 |
| S13 | 官方 | `prometheus.io/docs/alerting/latest/alertmanager/` | 告警分组/抑制/静默（`for` 之后的治理层） | ✅ 2026-10-24 |
| S14 | 中文 | `developer.aliyun.com/article/511487` | **RED 方法（Rate/Errors/Duration）** 与 Four Golden Signals 的关系 | ✅ 2026-10-24 |
| S15 | 中文 | `developer.aliyun.com/article/1436803` | **RED / USE / 四个黄金信号对照 + 三个 RED 指标的 PromQL 模板** | ✅ 2026-10-24 |
| S16 | 中文 | `developer.aliyun.com/article/1172417` | **USE 方法（Utilization/Saturation/Errors）** 的中文表述 | ✅ 2026-10-24 |
| S17 | 云厂商 | `help.aliyun.com/zh/prometheus/user-guide/alert-rule-description` | 托管 Prometheus 的报警规则说明 | ✅ 2026-10-24 |

> **完整来源矩阵见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。**
> **官方无中文版**：`prometheus.io/zh/docs/...` **404**（索引第三节实测坑 #2），PromQL 相关的**中文来源只能靠社区与云厂商文档**。
> **本篇新增的索引条目**：S3、S4、S6、S7、S8、S10、S14、S15、S16、S17（已按索引第五节维护规则回写索引表）。
> **⚠️ 待验证清单**：
> ① **`rate` 的"至少 4 个样本"**——官方函数页**只对 `deriv` / `predict_linear` 明确写了"至少两个 float 样本"**，
> **对 `rate` 未写明最小样本数**。"窗口 ≥ 4 个抓取间隔"是**社区与生产实践的通行口径**，本篇已明确标注为"常见实践，不是官方标准"。
> ② **PromQL 报错原文**——官方 `querying/operators/` 与 `querying/basics/` 页**只描述行为与 annotation 等级，未给出错误消息原文**。
> 本篇第 1.3 节与第 6 节出现的报错形态均标注为「**典型报错（社区/官方 issue 常见形态，非本篇实测）**」，**具体文案随版本变化，以本地 Prometheus 返回的原文为准**。
> ③ **K8s 侧的 `container_cpu_cfs_throttled_seconds_total`、`container_memory_working_set_bytes`、`kube_pod_container_resource_requests`、`kube_pod_container_status_restarts_total`**——
> 这些是 cAdvisor 与 kube-state-metrics 的指标名，本篇**未逐一核对**其官方字段定义
> （两个项目的 GitHub 仓库在本环境实测不可直连，索引第三节坑 #7），**⚠️ 待验证**；查询模板按社区通行写法给出。

---

## 8. 自测三问

1. **能说清输入输出吗？**
   **输入**：一批时间序列（按标签集合唯一标识）+ 一个时间点或一个时间区间。
   **中间链路**：选择器选出序列 → 函数把**范围向量压成瞬时向量**（或反过来用子查询补时间维）→ 聚合算子按维度合并 → 比较运算符筛选 → 返回瞬时向量 / 标量。
   **输出**：一个**瞬时向量**（每条序列一个数 + 一组标签）、一个**标量**、或一个**矩阵**（range query 的多条序列 × 多个时间点）。
   **关键一句**：**PromQL 的四种类型（instant vector / range vector / scalar / string）决定了一切合法性**，而**它对类型不匹配的处理是"静默移除元素 + 挂 annotation"，不是报错**——所以必须自己知道类型，不能靠跑一下看。

2. **能故意改坏再倒推吗？** —— 六个实验（每个都写清改什么、看到什么）：
   ① **把 `sum by (service) (rate(http_requests_total[5m]))` 的 `by (service)` 删掉** → Table 页返回**几百条**而不是一条 → 倒推出"**先在 Table 页数条数**"这条官方建议。
   ② **把 `sum(rate(a[5m])) / sum(rate(b[5m]))` 改成 `sum(rate(a[5m]) / rate(b[5m]))`** → 数值**明显偏大**（小流量实例的错误率被放大）→ 倒推出"**比例不能先算后聚合**"。
   ③ **把 `histogram_quantile(0.95, sum by (le) (rate(x_bucket[5m])))` 的 `sum by (le)` 删掉** → 返回**每个实例一条**的 P99，而不是总体 P99 → 倒推出"**先合并桶再算分位数**"。
   ④ **把 `rate(x[5m])` 的窗口改成 `[10s]`**（抓取间隔 15s）→ **返回空结果** → 倒推出"**窗口至少 4 个抓取间隔**"。
   ⑤ **把 `rate(x[5m])` 改成 `irate(x[5m])` 放进一条带 `for: 10m` 的告警** → 曲线锯齿剧烈，告警**迟迟不触发** → 倒推出"**官方说 irate 会 reset FOR 条件，所以告警用 rate**"。
   ⑥ **把告警规则里的 `for: 5m` 删掉** → 一个抖动周期产生"告警 + 恢复"两条通知 → 倒推出"**`for` 是防抖的关键**"。

3. **能画上下游吗？** —— 画出这条链，并在每个环节标出"这一步错了会看到什么"：

   ```
   原始时间序列（counter / gauge / histogram）
      │ 错 → 类型选错，后面全错（rate 对 gauge → 负数）
      ▼
   选择器 metric{label matcher}
      │ 错 → 正则少写 .* → 匹配不到；空标签语义搞反
      ▼
   范围向量 x[5m]  ←── 加 offset / @ 可查询历史时刻
      │ 错 → 窗口太小 → 无数据
      ▼
   函数压扁：rate / irate / increase / *_over_time / histogram_quantile
      │ 错 → 先聚合后 rate → 检测不到 counter 重置；分位数缺 sum by (le)
      ▼
   聚合算子 sum / avg / topk …（by 或 without）
      │ 错 → by 漏写 → 维度错乱；比例先算后聚合
      ▼
   集合运算 and / or / unless、向量匹配 on / ignoring / group_left
      │ 错 → 标签集不匹配 → 返回空；many-to-many 报错
      ▼
   比较过滤 > 0.05（可加 bool）
      │ 错 → 忘记 bool → 返回的是原值而不是 0/1
      ▼
   瞬时向量 / 标量
      │
      ├─→ recording rule（落成新序列，供看板与告警复用）
      └─→ alerting rule（for → pending → firing → Alertmanager）
   ```

---

**上一篇**：[`L1-Prometheus-数据模型与采集`](L1-Prometheus-数据模型与采集.md)（**本篇讲"数据怎么被问出来"，上一篇讲"数据怎么进来"**） · **同系列**：[`L3-Grafana-仪表盘与告警`](L3-Grafana-仪表盘与告警.md) · **下一级**：[`L4-Loki-日志管道`](L4-Loki-日志管道.md) · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md)
