# L2 · GitOps 与可观测闭环（交付闭环 × 观测闭环接在一起）

> **本篇定位**：这是**第二个收口项目**。第一篇（[`L1-巨石应用云原生化.md`](L1-巨石应用云原生化.md)）讲的是**改造路径**——从现状到云原生分八个阶段走；本篇讲的是**闭环**——改造完成之后，"交付"和"可观测"这两条线怎么接成一个**能自己转起来的回路**。
> **前置**：[`L1-巨石应用云原生化.md`](L1-巨石应用云原生化.md) · [`../第2周/第10天.md`](../第2周/第10天.md)（ArgoCD 同步） · [`../第2周/第11天.md`](../第2周/第11天.md)（SelfHeal / Prune） · [`../第2周/第12天.md`](../第2周/第12天.md)（Prometheus / Alertmanager） · [`../第2周/第13天.md`](../第2周/第13天.md)（Grafana / Loki）
> **学完能做什么**：画出完整的交付 + 观测闭环图；说清 GitOps 的三个一致性和可观测的三个一致性；执行一次"告警 → 定位 → 改 Git → 自动同步 → 恢复"的完整动作链；设计一次最小混沌演练并出复盘。
> **在 21 天里**：D10–D11（ArgoCD）+ D12–D13（Prometheus / Grafana / Loki / Alertmanager）+ D14（周整合②）+ D15（渐进发布接指标门禁）。**本篇是把它们接成一个环，而不是六个工具的清单。**
> **环境/版本基准**：本地 K8s **v1.37.x**（kind 集群 `day2`）；镜像仓库 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`；Prometheus 与 ArgoCD 均为**拉模型**（Prometheus 主动 scrape、ArgoCD 主动 pull Git）。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L2 GitOps 与可观测闭环
│
├─ 1. 概念
│   ├─ 1.1 什么叫"闭环"（不是把工具串起来，是让回路自己转）
│   ├─ 1.2 为什么要闭环（工具清单 vs 闭环，差在哪）
│   ├─ 1.3 术语表（中英对照）
│   └─ 1.4 本篇项目设定（沿用 L1 的 order-api）
│
├─ 2. 机制
│   ├─ 2.1 架构全景：一条从代码到告警再回到代码的环
│   ├─ 2.2 GitOps 闭环的三个关键一致性
│   ├─ 2.3 可观测闭环的三个关键一致性
│   ├─ 2.4 两个闭环的接缝在哪里（版本标签）
│   └─ 2.5 关键参数与默认值
│
├─ 3. 动手
│   ├─ 3.1 搭闭环：八个组件一次装齐
│   ├─ 3.2 从告警到修复的标准动作链（十步，每步对应工具）
│   ├─ 3.3 把"发布"和"指标"绑在一起（版本标签埋点）
│   ├─ 3.4 Grafana Annotation：把发布事件叠到看板上
│   ├─ 3.5 最小混沌演练：设计、执行、复盘
│   ├─ 3.6 成本与收益闭环（FinOps 视角与其边界）
│   ├─ 3.7 端到端排障与运维命令速查（按全链路环节）
│   └─ 3.8 端到端排障实战（三场景串命令）
│
├─ 4. 逐字段表
│   ├─ 4.1 闭环环节 × 工具 × 产物 × 责任人 × 验证方式
│   └─ 4.2 版本标签与告警标签逐字段表
│
├─ 5. 横向对比
│   ├─ 5.1 推模型 CI/CD vs 拉模型 GitOps
│   ├─ 5.2 纯监控 vs 可观测闭环
│   ├─ 5.3 全量上 vs 分步上（推荐分步）
│   └─ 5.4 三个闭环的成熟度分级
│
├─ 6. 踩坑与排障
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 什么叫"闭环"（不是把工具串起来，是让回路自己转）

> **一句话**：闭环 = **有人改了东西 → 系统发现问题 → 人被通知 → 人改了 Git → 系统自动把改动落到集群 → 系统确认恢复了**。这条路径的**每一段都不需要人手动搬运信息**。

区分两件很容易被混为一谈的事：

| | 工具清单 | 闭环 |
|---|---|---|
| 形态 | ArgoCD + Prometheus + Grafana + Alertmanager 都装了 | 告警能指出**是哪个应用的哪个版本**出了事，人改 Git 后系统自动落地并确认恢复 |
| 判定标准 | "每个工具都能打开" | **"从收到告警到业务恢复，全程不需要从控制台复制粘贴"** |
| 断点在哪 | 不知道 | **知道每个接缝在哪、断了会怎样** |
| 谁能用 | 只有搭这套的人 | 值班的人按手册就能处理 |

> **本篇的写作立场**：**"闭环"这个词最容易变成口号。** 所以本篇每讲一个组件，都必须同时讲清两件事：**① 它从上游接什么 ② 它往下游送什么**。接不上或者送不到，这一环就是断的——**断在哪里，要能说出来。**

### 1.2 为什么要闭环（工具清单 vs 闭环，差在哪）

用三条真实事故来说明差别：

| 事故 | 工具清单的做法 | 闭环的做法 |
|---|---|---|
| **发布后错误率涨了 3%** | 值班问"哪个服务？"→ 翻 Grafana → 逐个面板看 → 猜是刚才那次发布 → 翻发布记录 | 告警消息里直接带 `app=order-api` `version=<commit SHA>` → 点开 Grafana 面板，**新版本和旧版本的错误率并排** → 结论：是新版本 |
| **集群被手改，参数错了** | 没人知道，直到业务出问题才发现和 Git 不一样 | ArgoCD 检测到 `OutOfSync` → **SelfHeal 自动拉回** → 若持续 `OutOfSync` 超 10 分钟则告警 → 知道有人绕过了流程 |
| **半夜服务变慢** | 收到 CPU 告警 → 不知道是流量涨了还是代码变慢 → 反复查 | 告警带 version 标签 → 对比该版本的 P99 与上一版 → 看 Grafana Annotation 上**最近一次发布的时间点** → 判断是不是发布引起 |

**结论**：**闭环不是"多装了几个工具"，而是"信息能在系统内部自动传递"。** 一句话概括本篇的价值：**闭环让"定位"这一步从"人的能力"变成"系统的输出"。**

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| GitOps | — | Git 是唯一事实源，控制器主动拉取并拉平（**拉模型**） |
| Pull-based | 拉模型 | 集群内的控制器主动去拉期望态（Jenkins 推 = push 模型） |
| Sync | 同步 | ArgoCD 把 Git 里的期望态应用到集群 |
| Drift | 漂移 | 集群实际态与 Git 声明不一致 |
| SelfHeal | 自愈 | 漂移被自动还原回 Git 的样子 |
| Prune | 剪枝 | Git 里删掉的资源，集群里也删掉 |
| AppProject | 应用项目 | 限定 Application 能碰哪些 repo / namespace / 集群资源 |
| Annotation | 标注 | Grafana 打在时间轴上的事件标记（如"发布"） |
| SLI | 服务等级指标 | 用户能感知的量（成功率 / 延迟 / 吞吐） |
| SLO | 服务等级目标 | SLI 的目标值（如"成功率 ≥ 99.9%"） |
| RED | — | Rate / Errors / Duration，应用层三指标 |
| USE | — | Utilization / Saturation / Errors，资源层三指标 |
| Cardinality | 标签基数 | 指标/日志标签的取值组合数，**爆炸即 OOM** |
| Scrape | 抓取 | Prometheus 主动拉取目标 `/metrics`（**拉模型**） |
| ServiceMonitor | 服务监控 | Prometheus Operator 的 CR，声明"抓谁、带什么标签" |
| PrometheusRule | 监控规则 | Prometheus Operator 的 CR，声明告警规则 |
| Inhibition | 抑制 | 上游故障时抑制下游衍生告警 |
| Silencing | 静默 | 临时屏蔽某条告警（如计划内维护） |
| MTTR | 平均恢复时间 | 从故障发生到业务恢复的平均时长 |
| Chaos / 演练 | 混沌演练 | 主动制造故障，验证系统与人的响应能力 |

### 1.4 本篇项目设定（沿用 L1 的 `order-api`）

不重新设定。沿用 [`L1-巨石应用云原生化.md`](L1-巨石应用云原生化.md) 的三个组件与仓库结构：

| 组件 | 技术栈 | 在闭环里的角色 |
|---|---|---|
| `order-api` | Spring Boot 单体 | **被交付的标的**——所有闭环最终都作用在它身上 |
| `settle-job` | 同镜像的 CronJob | **定时扰动源**——它让闭环有"不是发布引起的变更"这种干扰项 |
| `report-tool` | Go 工具服务 | **另一个被观测对象**——用来验证"标签体系能不能区分不同应用" |

**闭环的物理载体（GitOps 仓库）**：

```
gitops/                       # ← 唯一事实源
├── charts/
│   └── order-api/             # Chart：结构
│       ├── values-dev.yaml
│       └── values-prod.yaml   # 环境差异收敛在这里
├── argocd/
│   ├── appproject.yaml        # 权限边界
│   └── application.yaml       # 同步策略
├── observability/
│   ├── servicemonitor.yaml
│   ├── prometheus/rules/order-api.yaml
│   ├── grafana/dashboards/business.json
│   └── loki/promtail.yaml
└── release-events/            # 发布事件（供 Annotation 使用）
    └── 2026-10-24-order-api-v2.yaml
```

> **注意这个仓库结构的关键点**：**Chart、ArgoCD 配置、监控规则、看板 JSON 全部在同一个仓库。** 这不是偷懒，是**让"一次发布"能同时改代码版本、监控规则、看板"**成为可能——**发布事件和被观测对象在同一棵 commit 树里，天然对齐。**

---

## 2. 机制

### 2.1 架构全景：一条从代码到告警再回到代码的环

```
┌──────────────────────── 交付闭环（拉模型） ────────────────────────┐
│                                                                      │
│  研发 ──MR──▶ 代码仓库 ──CI(检查/单测/构建/扫描)──▶ 镜像仓库        │
│   ▲              │                                    │              │
│   │              │ values 里写 tag                    │              │
│   │              ▼                                    ▼              │
│   │        GitOps 仓库（唯一事实源）◀────── CI 写回 tag              │
│   │              │                                    │              │
│   │              │ ArgoCD 探测 diff → 同步            │              │
│   │              ▼                                    ▼              │
│   │        ArgoCD（集群内，拉模型）──────────▶ K8s 集群实际态        │
│   │              │                                    │              │
│   │              │ SelfHeal 拉平漂移                 │              │
│   └──────────────┘                                    │              │
└──────────────────────────────────────────────────────┼──────────────┘
                                                       │
┌──────────────────────── 观测闭环（拉模型） ──────────┼──────────────┐
│                                                      ▼              │
│   K8s 实际态 ──scrape──▶ Prometheus ◀──scrape── 中间件 exporter      │
│                              │                                       │
│                              │ PromQL                                │
│                              ▼                                       │
│                          Grafana ◀── Loki（Promtail 采集 stdout）    │
│                              │        ▲                              │
│                              │        │                              │
│                              ▼        │                              │
│                      PrometheusRule ──▶ Alertmanager ──▶ 通知（人）   │
│                              ▲                                       │
│                              │                                       │
│                              └── Grafana Annotation（发布事件）        │
└──────────────────────────────────────────────────────────────────────┘
                                                       │
                            人看到告警 ──定位──▶ 改 Git ──PR──▶ 合并
                                                       │
                                              （回到环的起点）
```

#### 逐环说明表（这张表就是"闭环检查表"）

| # | 环节 | 工具 | 从上游接什么 | 往下游送什么 | 断了会怎样 |
|---|---|---|---|---|---|
| 1 | 代码变更 | GitLab / Git | 开发者 MR | commit + 变更集 | 变更没有唯一入口，后面全是补丁 |
| 2 | 制品构建 | GitLab CI | commit | **镜像（tag = commit SHA）** | 镜像不可识别，指标对不上版本 |
| 3 | 质量门禁 | SonarQube + Trivy | 镜像 | 放行 / 阻断 | 高危制品进生产 |
| 4 | 事实源 | GitOps 仓库 | Chart + values + 监控规则 | **期望态** | 期望态没有唯一来源 → 两个控制器抢 |
| 5 | 同步 | ArgoCD（拉模型） | Git 期望态 | 集群实际态 | 集群与 Git 不一致（漂移） |
| 6 | 纠偏 | ArgoCD SelfHeal | 漂移检测 | 还原 | 漂移长期存在，无人知晓 |
| 7 | 采集 | Prometheus（拉模型） | 集群指标 | 时序数据 | **监控盲区**（新服务没被采到） |
| 8 | 存储查询 | Grafana | 时序数据 | 看板 / Annotation | 只有"有数据"没有"能读懂" |
| 9 | 日志 | Promtail → Loki | 容器 stdout | 可按时间窗检索的日志 | 只能在 `kubectl logs` 里捞，Pod 一删就没 |
| 10 | 判定 | PrometheusRule | 时序数据 | 告警实例 | 只有原始指标，没有"这算不算事" |
| 11 | 路由 | Alertmanager | 告警实例 | **通知到人** | 告警发了没人认领 |
| 12 | 处置 | 人 | 通知 | **改 Git** | **闭环在此断掉** |
| 13 | 确认 | ArgoCD + Prometheus | 新期望态 + 新指标 | 恢复证据 | 不知道是真恢复还是"告警自己消了" |

> **第 12 行是整个闭环的咽喉。** 前 11 个环节都是机器做的，**只有第 12 环需要人**。所以闭环设计的所有努力，都是为了让**第 12 环的人**能做得更快更准——这决定了指标、日志、版本标签、告警内容这四样东西的设计取舍。

### 2.2 GitOps 闭环的三个关键一致性

**这三个"一致性"是判断一个团队是不是真的在做 GitOps 的试金石**——不是看有没有装 ArgoCD。

#### 一致性 ① 期望态在 Git，实际态在集群，控制器负责拉平

| 角色 | 在哪 | 谁写 | 谁能改 |
|---|---|---|---|
| **期望态** | Git 仓库（Chart / Application / values） | 人通过 PR | **只有 PR 能改** |
| **实际态** | K8s 集群 | 控制器 | 人**不应该**改 |
| **拉平** | ArgoCD 的 reconcile 循环 | 控制器 | — |

**这条一致性的三个可验证判据**：

```bash
# 判据 1：App 状态是 Synced
argocd app get order-api-prod        # SYNCED

# 判据 2：集群里没有 Git 之外的手工改动
kubectl get deployment order-api -n prod -o yaml   # 与 Git 里渲染的完全一致

# 判据 3：制造一次漂移，它能被自动拉回
kubectl scale deployment/order-api -n prod --replicas=6
argocd app get order-api-prod        # OutOfSync
argocd app sync order-api-prod       # SelfHeal 拉回
kubectl get deployment order-api -n prod --replicas  # 回到 Git 的值
```

**破坏这条一致性的典型做法**（都是团队里天天发生的）：

| 做法 | 为什么破坏一致性 | 正确做法 |
|---|---|---|
| CI 里 `kubectl set image` 直接推集群 | 集群实际态被 CI 改了，Git 没改 → 下次同步被改回去 | CI 只写回 Git 的 values |
| 运维紧急 `kubectl edit` 热修 | 修完忘了 → 漂移长期存在 | 热修也走 PR（哪怕加急） |
| 手工 `kubectl apply` 一份改过的 yaml | 集群里有 Git 里没有的东西 | 要么补进 Git，要么接受被 SelfHeal 还原 |
| 用 `argocd app set --sync-policy none` 后忘记恢复 | **同步永久停了**，Git 不再是事实源 | 关之前必须登记，改完立刻恢复并补 PR |

> **"人改集群 = 制造漂移，下次同步被改回去"** 这句话要理解到骨头里：**GitOps 不是"Git 优先"，是"Git 唯一"。** 集群里任何不在 Git 里的东西，都是**临时的、会被擦掉的**。

#### 一致性 ② PR 评审是变更的唯一入口

| 变更类型 | 必须走 PR？ | 为什么 |
|---|---|---|
| 改 Chart 模板 / values | ✅ 必须 | 影响所有环境 |
| 改镜像 tag | ✅ 必须（由 CI 自动提 PR） | **这是最容易被绕过的一条**——CI 直接推集群 |
| 改监控规则 / 告警阈值 | ✅ 必须 | 阈值改错 = 告警风暴或盲区 |
| 改 Grafana 看板 JSON | ✅ 必须 | 否则"谁把看板改乱了"查不到 |
| 改 Loki 采集配置 | ✅ 必须 | **改错就是基数爆炸 / 磁盘写满** |
| 紧急热修 | ✅ 必须（**加急 PR**） | 走"紧急通道"，不是走"绕过通道" |
| 扩容（应急） | ⚠️ 例外 | 副本数归 HPA 独占；人工扩容必须同步改 Git，否则被拉回 |

**PR 评审要看的四件事**（这是"评审"不是"点合并"）：

| 看什么 | 怎么看 | 不合格的样子 |
|---|---|---|
| **改动范围** | `git diff --stat` | 一个 PR 改了 12 个文件、跨 3 个模块 → 拆 |
| **是否有非预期文件** | 看 diff 里的文件清单 | 顺手把 `values-prod.yaml` 的副本数也改了 → **会跟 HPA 打架** |
| **是否动了密钥** | 检索 `password` / `secret` / `key` | ConfigMap 里出现密码 → 红线 |
| **是否影响可观测** | 改了服务名 / 端口 / 标签？ | 改了 `app` 标签 → **告警路由和看板筛选同时失效** |

> **"改 `app` 标签会同时打掉告警路由和看板筛选"** 是一个很容易被忽略的**耦合点**。指标、告警路由、Grafana 变量三处都按标签筛选，**改一处不改另两处 = 静默失效**（不报错，只是数据不见了）。这是"可观测闭环"里最隐蔽的一类断点。

#### 一致性 ③ 控制器负责拉平（而不是人负责保持一致）

| 方案 | 谁保持一致 | 人的角色 | 问题 |
|---|---|---|---|
| 手工 kubectl | 人 | 唯一的执行者 | 人会忘、会累、会在交接时断 |
| CI push + 定期巡检 | 半自动 | 偶尔巡检 | 漂移存在的时间不可控 |
| **ArgoCD 拉模型 + SelfHeal** | **控制器** | **写 PR 的人** | 人不用管"保持一致"，只管"声明一致" |

**SelfHeal 的分级策略**（不是全开或全关）：

| 环境 / 资源 | Auto-sync | SelfHeal | Prune | 理由 |
|---|---|---|---|---|
| dev / test，全部资源 | ✅ | ✅ | ✅ | 坏了不影响业务，快最重要 |
| prod，无状态应用 | ✅ | ✅ | ✅（只对无状态资源） | 漂移是隐性故障源 |
| prod，**有状态中间件 / CRD / PV** | ✅ | ✅ | ❌ **加 `Prune=false`** | **Prune 会真删数据** |
| prod，**核心应用** | ✅ | ✅ | ⚠️ 按资源逐个开 | 误删的爆炸半径太大 |

**逃生通道必须提前定好**（没有逃生通道的 SelfHeal 是给自己埋雷）：

| 步骤 | 动作 | 约束 |
|---|---|---|
| 1 | 优先走**加急 PR** | 几分钟能合，不算绕过 |
| 2 | 实在来不及 → `argocd app set --sync-policy none` | **必须在群里登记**：谁、何时、为什么、改了什么 |
| 3 | 手工修 + **立刻补 PR** | 补完立刻恢复同步 |
| 4 | 复盘时问"为什么当时必须手改" | **答不出来 = 流水线有瓶颈，要改流水线，不是改流程** |

> **"SelfHeal 防的是遗忘，不是防人。"** 人要知道自己在绕什么，绕完必须补回去。**这句话是 GitOps 落地的核心共识**——把它讲出来，比讲十个 API 字段有用。

### 2.3 可观测闭环的三个关键一致性

#### 一致性 ① 指标 / 日志 / 告警要能关联到具体版本

**这是三个一致性里最重要的一条**，也是最常被漏掉的。

| 关联层次 | 需要的信息 | 落地方式 | 缺了会怎样 |
|---|---|---|---|
| **指标 → 版本** | 指标上带 `version` 标签 | Pod template 加 `version: {{ .Values.image.tag }}` | **"指标劣化了"和"刚发布的那次变更"对不上** → 只能靠猜 |
| **日志 → 版本** | 日志里带版本字段 | 应用启动日志打版本；或采集侧从 Pod 标签注入 | 查日志时不知道是哪个版本的代码 |
| **告警 → 版本** | 告警标签带 `version` | PrometheusRule 里用 `by(version)` 聚合 + 标签透传 | 告警只说"错误率高"，**不知道是哪次发布引起的** |
| **发布事件 → 版本** | 发布事件带版本与时间 | 发布时写一个 Annotation 事件 | 看板上看不出"什么时候发的" |

**版本标签的三个维度**（与 L1 一致）：

| 标签 | 取值 | 作用 | 谁打上 |
|---|---|---|---|
| `app` | `order-api` | 选服务 | Chart 模板 |
| `version` | commit SHA 或语义化版本 | **关联发布** | Chart 模板（同一处） |
| `env` | `dev` / `prod` | 隔离环境 | Chart 的 values |

**验证方式（这是本篇最重要的一条验证）**：

```promql
# 期望：返回多个 version 值
sum by(version) (rate(http_requests_total{app="order-api"}[5m]))
```

**如果只返回一个值（比如全是 `latest`），说明版本标签没打——整个"关联发布"的能力就不存在。**

> **一个能立刻拉开差距的判据**：面试时被问"你怎么知道这次发布导致了指标劣化"，能**在 30 秒内说出 PromQL 并解释它返回什么**，比讲十分钟监控体系都有用。

#### 一致性 ② 告警必须能回溯到对应应用

| 要求 | 实现 | 检查方法 |
|---|---|---|
| **告警必须带 `app` 标签** | PrometheusRule 里 `labels: { app: order-api }` | `kubectl get prometheusrule -A -o yaml` 或在 Prometheus UI 看告警的 Labels |
| **告警必须带 `env` 标签** | 同上，`env: prod` | 路由规则按 env 分组 |
| **告警内容必须能定位到人** | `annotations.description` 写影响面；`runbook_url` 给手册 | 点开告警消息，不用再问人 |
| **告警必须有归属** | Alertmanager 按 `app` / `team` 标签路由到对应群 | Alertmanager 配置里的 `matchers` |
| **必须有升级机制** | 15 分钟未响应升级到负责人 | Alertmanager 的路由与静默配置 |

**Alertmanager 的三个治理手段（必须同时用）**：

| 手段 | 解决什么 | 关键字段 |
|---|---|---|
| **分组（group_by）** | 一万个实例同时告警 → 合成一条 | `route.group_by` |
| **抑制（inhibit）** | 主机挂了 → 抑制它上面所有服务的衍生告警 | `inhibit_rules`（source 匹配 + target 匹配） |
| **静默（silence）** | 计划内维护时临时屏蔽 | `silences`（有到期时间） |

**告警分级与路由**：

| 级别 | 判据 | 通知方式 | 响应时限 | 例子 |
|---|---|---|---|---|
| **P0 / critical** | 用户直接可感知的功能不可用 | **电话**（不是钉钉） | 立即 | 下单接口 5xx > 1% |
| **P1 / warning** | 功能受损但有绕行 | 钉钉群（按服务分群） | 15 分钟 | P99 延迟超阈值 |
| **P2 / info** | 需要人看，但不紧急 | 日报 / 周报 | 次日 | 副本长期贴着 maxReplicas |

> **"P0 打电话而不是发钉钉"** 是一条真实经验：**群里消息会被刷走，电话不会。** 真正影响用户的告警如果只发到群里，等于没告警。

#### 一致性 ③ 发布事件要能叠加到看板上（Grafana Annotation）

**没有 Annotation 的看板，只能看到"指标涨了"，看不到"什么时候发过版"。**

| 做法 | 实现 | 效果 |
|---|---|---|
| **推荐：发布时写 Annotation 事件** | 发布流水线产出一个事件文件 → ArgoCD 或 CI 推给 Grafana Annotation API | 看板时间轴上出现竖线标记，鼠标悬停显示版本与时间 |
| 备选：用 GitOps 仓库里的发布事件文件 + provisioning | 事件文件进 Git，与 Chart 同 commit | **时间点与发布内容严格对齐**，且**可审计** |
| 手工在 Grafana UI 上加 | 手工操作 | ❌ **不可审计、容易忘、谁加的查不到** |

**发布事件文件的样子**：

```yaml
# gitops/release-events/2026-10-24-order-api-v2.yaml
kind: AnnotationEvent
app: order-api
version: v20261024-abc1234
env: prod
time: 2026-10-24T14:30:00+08:00
text: |
  发布 order-api v20261024-abc1234
  commit: abc1234
  触发人: <研发姓名>
  流水线: pipeline #12345
  变更类型: feature
```

| 字段 | 含义 | 缺了会怎样 |
|---|---|---|
| `time` | 发布时刻 | **时间轴对不上，"发布后 10 分钟"这个判断做不了** |
| `version` | 发布的版本 | 和指标对不上 |
| `text` | 人读的说明（触发人、pipeline） | 出了问题不知道找谁、不知道哪次流水线 |
| `env` | 环境 | dev 的发布标记跑到 prod 看板上 |

**看板上 Annotation 的实际效果**（这是本篇的核心收益）：

```
错误率曲线
    │
 8% │                          ╭──╮
    │                    ╭─────╯  ╰────╮   ← 这段抬升
 4% │              ╭─────╯              ╰────
    │        ╭─────╯
 1% │────────╯
    └────────┬──────────┬──────────┬──────────┬──▶ 时间
             │          │          │          │
          Annotation: 发布 v2   ← 抬升起点紧跟发布
```

**没有 Annotation 时**，同一个曲线你只能猜"是不是刚才那次发布"。**有了它，抬升起点和发布标记垂直对齐，一眼就是结论。**

### 2.4 两个闭环的接缝在哪里（版本标签）

**这是本篇最需要单独讲的一节**——交付闭环和观测闭环不是"两个都装上"，它们**通过一个具体的字段接在一起**。

```
交付闭环                                         观测闭环
─────────                                         ─────────
Chart 渲染 Pod template.labels                    Prometheus 抓取时
  app: order-api            ◀──────────────────▶   relabel 把 Pod label
  version: v20261024-abc                           复制成指标 label
  env: prod                                       │
     │                                                ▼
     └──── Git（唯一事实源）────▶ ArgoCD 同步 ────▶ Pod 带 label
                                                      │
                                                      ▼
                                            指标带 version 标签
                                                      │
                             ┌────────────────────────┼──────────┐
                             ▼                        ▼          ▼
                        PrometheusRule          Grafana 面板    Loki
                        by(version) 聚合        按 version 筛选   注入版本
                             │                        │
                             ▼                        ▼
                          Alertmanager            Annotation 叠加
                             │                        │
                             └───────▶ 人 ◀──────────┘
                                        │
                                        ▼
                                    改 Git（tag）
```

**接缝的具体位置有四处**，每一处都可能断：

| # | 接缝 | 怎么接的 | 断了的表现 | 怎么验证 |
|---|---|---|---|---|
| 1 | **Chart 模板 → Pod label** | `podTemplate.labels` 里写 `version` | 指标只有 `latest` 一个值 | `helm template` 输出里查 labels |
| 2 | **Pod label → 指标 label** | Prometheus 的 relabel 把 Pod label 复制到指标 | 指标里没有 `version` 标签 | Prometheus UI 查某指标的 Labels 列表 |
| 3 | **指标 label → 告警标签** | PrometheusRule 里 `by(version)` 聚合 + 标签透传 | 告警不带 version | 触发一次告警看 Labels |
| 4 | **发布事件 → Annotation** | 发布流水线产出事件 → 推到 Grafana | 看板上没有发布标记 | 看板上有没有竖线 |

> **接缝 2 最隐蔽**：K8s 自带的 `kube-state-metrics` / `cAdvisor` 指标**默认不带 Pod 的自定义标签**，必须写 `relabel_configs` 把它复制过去。**不写这一步，`version` 标签就永远到不了指标上**——Pod 上明明有标签，Prometheus 里查不到。

**relabel 配置的形状**（不是照抄，是让你知道"要在这里接"）：

```yaml
# ServiceMonitor 的 relabel 段：把 Pod label 复制成指标 label
relabelings:
- sourceLabels: [__meta_kubernetes_pod_label_version]
  targetLabel: version
- sourceLabels: [__meta_kubernetes_pod_label_app]
  targetLabel: app
- sourceLabels: [__meta_kubernetes_pod_label_env]
  targetLabel: env
```

| 源标签 | 说明 |
|---|---|
| `__meta_kubernetes_pod_label_version` | **带双下划线前缀**的是"服务发现阶段"的元标签，**relabel 只能从这类标签取值** |
| `targetLabel: version` | 目标指标标签名 |

> **最常见的错**：写成 `sourceLabels: [version]`（去掉前缀）→ **不报错，就是不生效**。因为服务发现阶段还没采集到 `version` 这个元标签。这个错的特点是**静默**——Prometheus 不报错，指标就是不出现。

### 2.5 关键参数与默认值

| 参数 | 默认 / 口径 | 出处 | 写错的后果 |
|---|---|---|---|
| ArgoCD `automated.selfHeal` | 默认 **false**（要显式开） | [`../第2周/第11天.md`](../第2周/第11天.md) | 不开 → 漂移长期存在，无人知晓 |
| ArgoCD `automated.prune` | 默认 **false** | 同上 | 不开 → 孤儿资源堆积；**开错 → 删库** |
| ArgoCD `syncOptions: PruneLast` | 不配则剪枝按序执行 | 同上 | 资源被提前删掉 → 同步过程中断 |
| ArgoCD `ApplyOutOfSyncOnly` | 默认 false | 同上 | 开启后某些"必须重新 apply 才生效"的变更会漏 |
| Prometheus 采集模型 | **拉模型**，按 `scrape_interval` 周期 | [`../第2周/第12天.md`](../第2周/第12天.md) | 与 Zabbix 推模型混淆 → 讲不清"服务死了怎么发现" |
| metrics-server `--metric-resolution` | **15s** | [`../第1周/第02天.md`](../第1周/第02天.md) | 采集粒度决定 HPA 反应速度 |
| Alertmanager `group_wait` | **30s** | [`../第2周/第12天.md`](../第2周/第12天.md) | 太短 → 抖动即成组，噪声大 |
| Alertmanager `group_interval` | **5m** | 同上 | 太短 → 重复通知 |
| Alertmanager `repeat_interval` | **1h** | 同上 | 太短 → 同一问题反复推 → 被静音 |
| HPA 缩容稳定窗口 | **300s** | [`../第1周/第02天.md`](../第1周/第02天.md) | 缩容抖动 |
| `Deployment.progressDeadlineSeconds` | **600** | [`../02-K8s核心/L2-K8s架构与对象模型.md`](../02-K8s核心/L2-K8s架构与对象模型.md) | `rollout status` 超时——**零信息量** |
| 探针语义 | **liveness 失败→重启；readiness 失败→只摘端点** | 同上 | 混淆 → 假成功上线 或 误杀正常 Pod |
| Grafana 告警位置 | **生产告警一律走 PrometheusRule + Alertmanager** | [`../第2周/第13天.md`](../第2周/第13天.md) | 告警挂 Grafana → **Grafana 挂了告警静默**（最危险的单点） |
| Loki 索引方式 | **只索引 label，不索引正文** | 同上 | 存储成本约为 ES 的 1/5–1/10（量级说法，非精确成本承诺） |
| Loki label 基数 | **只放低基数维度**（app/ns/pod/level） | 同上 | **高基数进 label → OOM / 磁盘写满** |
| 滚动更新 `maxSurge`/`maxUnavailable` | 25% / 25% | [`../02-K8s核心/L2-K8s架构与对象模型.md`](../02-K8s核心/L2-K8s架构与对象模型.md) | 1 副本时 surge=1、unavailable=0 |

---

## 3. 动手

### 3.1 搭闭环：八个组件一次装齐

**前提**：本地 kind 集群 `day2` 已在运行；GitOps 仓库已建好（见 1.4）。

#### 步骤 1：Prometheus Operator（用 kube-prometheus-stack）

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install prom prometheus-community/kube-prometheus-stack -n monitoring --create-namespace \
  --set prometheus.prometheusSpec.image.registry=registry.cn-hangzhou.aliyuncs.com \
  --set prometheus.prometheusSpec.image.repository=google_containers/prometheus
kubectl rollout status deployment/prom-kube-prometheus-stack-prometheus -n monitoring --timeout=180s
```

**看到什么算对**：`-n monitoring` 下 Pod 全部 Running；`kubectl get servicemonitor -A` 有一堆条目（Operator 在工作）。

> **注意：装的是 Operator（`kube-prometheus-stack`），不是裸 Prometheus。** 差别在后面：ServiceMonitor / PrometheusRule / Grafana 看板都是 CR，**进 Git、进 ArgoCD、同步**。裸 Prometheus 的配置文件是挂载的 ConfigMap，改起来是另一套机制。

#### 步骤 2：ServiceMonitor —— **接缝 2 在这里**

```yaml
# observability/servicemonitor.yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: order-api
  namespace: monitoring
  labels:
    release: prom          # ← 关键：匹配 kube-prometheus-stack 的 serviceMonitorSelector
spec:
  namespaceSelector:
    matchNames: [prod]     # 或者用 label 选择器覆盖多环境
  selector:
    matchLabels:
      app: order-api
  endpoints:
  - port: metrics
    path: /actuator/prometheus
    interval: 15s
    relabelings:            # ← 接缝 2：把 Pod label 复制成指标 label
    - sourceLabels: [__meta_kubernetes_pod_label_version]
      targetLabel: version
    - sourceLabels: [__meta_kubernetes_pod_label_app]
      targetLabel: app
    - sourceLabels: [__meta_kubernetes_pod_label_env]
      targetLabel: env
```

| 字段 | 含义 | 写错的后果 |
|---|---|---|
| `metadata.labels.release: prom` | **必须匹配 kube-prometheus-stack 的 `serviceMonitorSelector`** | **不匹配 → ServiceMonitor 存在但 Prometheus 不读它 → 指标里啥都没有，且不报错** |
| `namespaceSelector.matchNames` | 抓哪个命名空间 | 新环境上线后没监控（**D14 场景 B 第 3 条**） |
| `selector.matchLabels` | 选哪个 Service | 选错 → 抓不到 |
| `endpoints[].relabelings` | **Pod label → 指标 label** | 漏了 → **指标里没有 `version` 标签，接缝断** |

#### 步骤 3：给 Pod 打上版本标签（接缝 1）

```yaml
# charts/order-api/templates/_helpers.tpl
{{- define "order-api.labels" -}}
app: order-api
version: {{ .Values.image.tag | quote }}
env: {{ .Values.env | default "prod" | quote }}
{{- end -}}
```

```yaml
# charts/order-api/templates/deployment.yaml
metadata:
  labels:
    {{- include "order-api.labels" . | nindent 4 }}
```

**验证**：

```bash
helm template order-api charts/order-api -f values-prod.yaml | grep -A4 'labels:'
# 期望看到 app / version / env 三个标签
```

#### 步骤 4：ArgoCD Application + AppProject

```yaml
# gitops/argocd/appproject.yaml
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: prod
  namespace: argocd
spec:
  sourceRepos: ["https://git.example.com/<org>/gitops.git"]
  destinations:
  - server: "*"
    namespace: "prod"
  clusterResourceWhitelist:
  - group: "*"
    kind: "*"
  namespaceResourceWhitelist:
  - group: "*"
    kind: "*"
```

| 字段 | 含义 | 写错的后果 |
|---|---|---|
| `sourceRepos` | 允许从哪些仓库拉 | 配太宽 → 任意仓库的 Application 都能同步进来 |
| `destinations` | 允许部署到哪 | **不限定 namespace → 一个 App 能同时管 dev 和 prod** |
| `clusterResourceWhitelist` | 允许操作集群级资源 | 太宽 → 一个 App 能删 CRD |

#### 步骤 5：Alertmanager 路由（分组 + 抑制 + 分级）

```yaml
# observability/alertmanager/alertmanager.yaml
route:
  receiver: default
  group_by: [alertname, app]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 1h
  routes:
  - matchers: [ 'severity="critical"' ]
    receiver: oncall-phone
    group_wait: 10s
    repeat_interval: 30m
  - matchers: [ 'severity="warning"' ]
    receiver: team-dingtalk
inhibit_rules:
- source_matchers: [ 'severity="critical"' ]
  target_matchers: [ 'severity="warning"' ]
  equal: [app]
```

| 配置 | 作用 | 不配的后果 |
|---|---|---|
| `group_by: [alertname, app]` | 同类同应用合成一条 | 一个节点故障 → 几百条告警 |
| `group_wait: 30s` | 等 30s 看是否还会来更多 | 太短 → 抖动被切成多条 |
| `inhibit_rules` + `equal: [app]` | **critical 存在时抑制同 app 的 warning** | 根因告警 + 一堆衍生告警一起推 |
| `repeat_interval: 1h` | 同一问题 1 小时才重复推 | 30s 一次 → 手机被刷爆 → 被静音 |

#### 步骤 6：PrometheusRule（告警规则）

```yaml
# observability/prometheus/rules/order-api.yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: order-api
  namespace: monitoring
  labels:
    release: prom
spec:
  groups:
  - name: order-api.slo
    rules:
    - alert: OrderApiHighErrorRate
      expr: |
        sum by(app, version) (rate(http_requests_total{app="order-api",status=~"5.."}[5m]))
          / sum by(app, version) (rate(http_requests_total{app="order-api"}[5m])) > 0.01
      for: 5m
      labels:
        severity: critical
        app: order-api
        env: prod
      annotations:
        summary: "order-api 错误率超过 1%"
        description: "版本 {{ $labels.version }} 错误率超阈值，影响下单主链路"
        runbook_url: "https://wiki.example.com/runbook/order-api-error-rate"
```

| 字段 | 作用 | 写错的后果 |
|---|---|---|
| `labels.release: prom` | **必须匹配 Prometheus 的 `ruleSelector`** | 不匹配 → **规则存在但不被加载，且不报错**（沉默的告警缺失） |
| `expr` 里 `by(app, version)` | **按版本聚合** | 不带 version → 无法定位到发布 |
| `labels.app` / `labels.env` | **告警能回溯到应用** | 不带 → 进大群没人认领 |
| `for: 5m` | 持续 5 分钟才触发 | 不写 → 抖动即告警 |
| `annotations.description` 里的 `{{ $labels.version }}` | **把版本渲染进告警正文** | 不写 → 告警只说"错误率高" |
| `runbook_url` | 处理手册 | 没有 → 每次都现查，MTTR 拉长 |

#### 步骤 7：Grafana 数据源 + 看板（JSON 进 Git）

```bash
helm install grafana grafana/grafana -n monitoring \
  --set adminPassword=<改掉默认> --set service.type=ClusterIP
kubectl port-forward svc/grafana -n monitoring 3000:80 &
# 数据源 URL：http://prom-kube-prometheus-stack-prometheus:9090
```

**看板的四个受众**（沿用 D13 的分法，但补上"发版观察"这一块闭环专用看板）：

| 看板 | 受众 | 核心内容 | 刷新 | **闭环里的作用** |
|---|---|---|---|---|
| 值班大屏 | 值班 | 告警列表、红绿状态 | 10–30s | **收到告警后第一眼看的** |
| 业务健康 | 技术负责人 | QPS、错误率、P99 | 1m | **判断"这次发布好不好"** |
| **发版观察** | 研发 | 发布前后错误率对比、Pod 重启数、**Annotation** | 10s | **决定要不要回滚** |
| 容量成本 | 运维 / 财务 | 节点利用率、requests 使用率 | 1h | **FinOps 归集** |

#### 步骤 8：Loki 日志管道

```bash
helm repo add grafana https://grafana.github.io/helm-charts && helm repo update
helm install loki grafana/loki-stack -n monitoring \
  --set loki.persistence.enabled=true \
  --set loki.persistence.size=10Gi \
  --set promtail.enabled=true \
  --set grafana.enabled=false
```

**验收标准（这是硬指标，不能用"装上了"交差）**：

```bash
# 删一个 order-api 的 Pod，等新 Pod 起来后，在 Grafana 里查：
#   {app="order-api"}，时间窗覆盖删除前
# 期望：还能查到已删除 Pod 的历史日志（label 里能看到旧 pod 名）
# 查不到 = 还在依赖容器本地日志，管道没通
```

#### 步骤 9：把 8 个组件接进 GitOps

```bash
# ① 全部文件进 GitOps 仓库的对应目录
# ② Application 的 path 指向 charts/order-api（Helm 渲染）
#    ServiceMonitor / PrometheusRule 用同一 Application 的多源，或单独 Application
# ③ 验证同步
argocd app get order-api-prod          # SYNCED + HEALTHY
argocd app get observability-prod      # 监控配置也由 Git 管
```

> **这一步是闭环的关键分水岭**：**监控规则、告警阈值、看板 JSON 都进 Git**，意味着**改一个告警阈值也要走 PR、也要有历史、也要能被回滚**。**很多团队的监控配置躺在 Grafana UI 里——这是"配置漂移"在观测侧的翻版。**

### 3.2 从告警到修复的标准动作链（十步，每步对应工具）

**这是本篇最实用的一段**——它应该被打印出来贴在值班位。

| 步 | 动作 | 工具 / 命令 | 判断标准 | 时间目标 |
|---|---|---|---|---|
| **1** | **告警触发** | Alertmanager → 钉钉 / 电话 | 消息含 `app` / `env` / `version` / `runbook` | — |
| **2** | **认领** | 钉钉群按服务分群 + 值班表 | 有人应答；15 分钟无人应答 → 升级 | **≤ 1 分钟** |
| **3** | **看 Grafana 定位** | 值班大屏 / 业务健康看板 | 找到是哪个 app、哪个 version、哪个面板 | **≤ 2 分钟** |
| **4** | **看 Prometheus 指标** | `sum by(version)(rate(...))` | 确认是哪个版本开始劣化；看是否只影响部分 Pod | **≤ 2 分钟** |
| **5** | **看 Loki 日志** | `{app="order-api"}` + 时间窗 | 找到具体错误行；**确认 Pod 已删也能查到** | **≤ 3 分钟** |
| **6** | **判定归因** | 四选一 | **配置 / 代码 / 资源 / 依赖** | **≤ 3 分钟** |
| **7** | **改 Git** | 本地改 values / Chart → PR | PR 里写清"为什么改、改哪个版本" | **≤ 5 分钟** |
| **8** | **合并** | PR 评审 | 至少一人评审；生产走 `when: manual` | **≤ 3 分钟** |
| **9** | **ArgoCD 自动同步** | `argocd app get` → `kubectl get pods` | Synced + Healthy；**新 Pod 带新 version 标签** | **自动，秒级到分钟级** |
| **10** | **观察恢复 + 关告警** | Grafana + Prometheus | 错误率回落；**告警自动 resolved** | **≤ 10 分钟** |

**第 6 步的"四选一"是整条链最关键的分叉**，给一张判定表：

| 归因 | 典型信号 | 判定方法 | 改哪里 |
|---|---|---|---|
| **配置** | 只有新版本受影响；老版本正常；改动前后有明确分界 | 看 version 标签：**故障起点 == 新版本上线时间** | 改 **values**（配置层） |
| **代码** | 新版本引入的路径才报错；日志里有新的异常栈 | 看日志里的新错误类型 / 新堆栈 | 改 **代码 + 镜像 tag** |
| **资源** | 所有版本都受影响；指标显示 requests 不足 / 限流 / OOM | 看 `kube_pod_container_resource_*`、OOMKilled 次数、throttling 指标 | 改 **requests/limits**（资源层） |
| **依赖** | 本服务指标正常，但下游响应时间涨 | 看依赖的 Service 与外部依赖指标；本服务日志出现连接超时 | 改 **依赖侧**（不是本服务） |

> **这张表的价值在于"用证据排除"**：第 4 步的 `by(version)` 一出来，"配置 vs 代码"基本就定了（**新版本才出问题 → 配置或代码；老版本也出问题 → 资源或依赖**）。**这就是版本标签最值钱的地方——它把四选一变成了二选一。**

**第 9 步和第 10 步之间有一个必须做的检查**：

```bash
# 确认新 Pod 真的带上了新版本标签
kubectl get pods -l app=order-api -o custom-columns=\
NAME:.metadata.name,VERSION:.metadata.labels.version,READY:.status.containerStatuses[0].ready
```

**如果 version 没变**（还是旧 tag），说明**改动没生效**——是 ArgoCD 没同步、或者 tag 传错。**"发布成功"不等于"版本变了"，必须验。**

### 3.3 把"发布"和"指标"绑在一起（版本标签埋点）

**这一节是本篇的"接口文档"**——四层埋点，一处都不能少。

| 层 | 埋点位置 | 具体做法 | 验证 |
|---|---|---|---|
| **1. Pod 层** | `podTemplate.metadata.labels` | Chart 的 `_helpers.tpl` 里统一生成 `app` / `version` / `env` | `helm template` 输出里查 labels |
| **2. 采集层** | ServiceMonitor 的 `relabelings` | 把 `__meta_kubernetes_pod_label_version` 复制到 `targetLabel: version` | Prometheus UI 查指标的 Labels 列表 |
| **3. 规则层** | PrometheusRule 的 `expr` | `sum by(app, version)(...)`；`annotations` 里用 `{{ $labels.version }}` | 触发一次告警看正文 |
| **4. 展示层** | Grafana 面板 | 变量 `$version` 用 `label_values(http_requests_total, version)`；发布事件做 Annotation | 看板上能切版本；时间轴有发布竖线 |

**Kustomize 场景的标签覆盖**（另一种落地方式，运维改第三方 Chart 时用）：

```yaml
# overlays/prod/kustomization.yaml
labels:
- pairs:
    version: v20261024-abc1234
    env: prod
  includeSelectors: false      # ← 关键：不要改 selector
```

| `includeSelectors` | 含义 | 写错的后果 |
|---|---|---|
| `true`（默认行为需注意） | **把标签也加进 selector** | **改了 Deployment 的 selector → 与 template 标签不再匹配 → `selector does not match template labels`，apply 直接报错** |
| `false` | 只加到 template 上，不动 selector | 安全 |

> **这是 Kustomize 的一个高频坑**：`labels` 默认会同时作用于 selector 和 template，**对 Deployment 来说等于改了不可变的 selector**。**记住：改标签时 `includeSelectors: false`。**

### 3.4 Grafana Annotation：把发布事件叠到看板上

**三种实现方式**（推荐程度从高到低）：

| 方式 | 怎么做 | 优点 | 缺点 |
|---|---|---|---|
| **A. 发布流水线写事件文件 → 推 Annotation API** | CI 里 curl Grafana Annotation API | 实时、和发布严格同步 | 依赖 Grafana 的 API 凭据（在 CI 变量里） |
| **B. 事件文件进 GitOps 仓库 + 定期同步** | 发布时生成 `release-events/*.yaml`，一个 Job 定期推 | **和发布内容同一 commit → 严格对齐 + 可审计** | 有延迟（取决于同步周期） |
| **C. 从 ArgoCD 同步历史推断** | 用 `argocd app history` 的时间点生成 | 无需额外产物 | 粒度粗（只有同步时刻，不是发布时刻） |

**A 方式的实现（CI 侧）**：

```bash
# .gitlab-ci.yml 的 deploy_gitops 之后
- |
  curl -s -X POST "$GRAFANA_URL/api/annotations" \
    -H "Authorization: Bearer $GRAFANA_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
      \"dashboardUID\": \"$DASHBOARD_UID\",
      \"time\": $(date +%s000),
      \"tags\": [\"order-api\", \"$CI_COMMIT_SHORT_SHA\", \"prod\"],
      \"text\": \"发布 $CI_COMMIT_SHORT_SHA by $GITLAB_USER_LOGIN\"
    }"
```

| 字段 | 说明 | 写错的后果 |
|---|---|---|
| `dashboardUID` | 打在哪个看板上 | 错 UID → 看板上看不到 |
| `time` | **毫秒时间戳** | 用秒 → 时间点错到 1970 年附近，**看板上完全看不出来** |
| `tags` | 带 `app` / `version` | 不带 → **没法按版本筛选事件** |
| `text` | 人读的说明 | 只有时间戳 → 知道"发过"但不知道"发了什么" |

> **`time` 用毫秒还是秒是最容易错的一个字段**——错了不报错，只是标记跑到 1970 年，看板上像"没有标记"。**所以验证方式必须是"看板上能不能看见"，不能是"API 返回 200"。**

### 3.5 最小混沌演练：设计、执行、复盘

**这一节是本篇的"实战科目"**——闭环建好了不代表它能转，**必须演练一次**。

#### 3.5.1 演练设计（最小版本）

**目标**：验证"告警 → 定位 → 修复 → 同步 → 恢复"这条链**真的能通**，而不是"看图说话"。

| 项 | 设计 |
|---|---|
| **演练目标** | 验证第 3.2 节十步链路的每一步都能走通；**测量总耗时** |
| **故障类型** | 三选一（难度递增）：① 改坏配置 ② 打满 CPU ③ 断掉外部依赖 |
| **爆炸半径** | **限 `order-api` 一个应用、一个命名空间** |
| **通知范围** | 提前通知：值班群 + 研发 + **至少一个不知情的第三方**（用来验证告警是否会被误判为故障） |
| **时间窗口** | **工作时间**，不要在半夜（半夜做的演练，值班人员状态和判断力都不真实） |
| **中止条件** | 超过 30 分钟未恢复 → 立即中止演练、恢复现场 |
| **观察员** | 至少一人**只记录不参与**：记录每一步用时和卡点 |

#### 3.5.2 三种故障的具体做法

| 故障 | 怎么制造 | 期望的告警 | 期望的处置路径 | 难度 |
|---|---|---|---|---|
| **① 改坏配置** | 在 GitOps 仓库把 `values-prod.yaml` 的某个超时改成一个离谱的值（如连接超时 1ms）→ 提 PR 合并 → ArgoCD 自动同步 | `OrderApiHighErrorRate`（critical） | 版本标签**直接指出是这次发布** → 改回 Git → 再次同步 | 低（最容易成功） |
| **② 打满 CPU** | 在 Pod 里跑一个死循环占满 CPU（**注意 limit 足够高，否则是限流不是打满**） | 是 `OrderApiHighErrorRate` 或 `HighCpuUsage` | 指标显示 CPU 打满 → 判定"资源"归因 → 扩容或加 limit | 中 |
| **③ 断掉外部依赖** | 把依赖的 Service selector 改到一个空标签集（**或临时把外部依赖的 Endpoint 摘掉**） | 本服务错误率涨 + 依赖超时日志 | **判定"依赖"归因**（本服务代码和版本都没变）→ 恢复依赖 | 高（容易误判成代码问题） |

> **故障 ③ 是最有价值的**：它验证的是**归因能力**——版本标签在这种情况下**帮不上忙**（版本没变），必须靠日志里的连接超时和依赖指标来判断。**只演练故障 ① 的团队，其实只验证了"发布流程能回滚"，没验证"排障能力"。**

#### 3.5.3 演练清单（打印出来逐条打勾）

```markdown
## 演练记录（复制模板填写）

演练编号：CHAOS-YYYYMMDD-01
演练类型：① 改坏配置 / ② 打满 CPU / ③ 断掉外部依赖
参与人：值班 ___ / 操作 ___ / 观察员 ___
起止时间：__ : __ 至 __ : __

### 执行前（准备）
- [ ] 已提前通知：值班群 / 研发 / 至少一个第三方
- [ ] 已确认爆炸半径：一个应用、一个命名空间
- [ ] 已准备中止方案：___（写清怎么一键恢复）
- [ ] 已记录当前基线：错误率 ___ / 副本数 ___ / version ___

### 执行
- [ ] T0 制造故障
- [ ] T1 告警送达（记录送达时间）
- [ ] T2 认领（记录认领时间与人）
- [ ] T3 定位到 app + version（记录定位依据）
- [ ] T4 判定归因（配置/代码/资源/依赖）
- [ ] T5 改 Git 并提 PR
- [ ] T6 合并
- [ ] T7 ArgoCD 同步完成（记录同步耗时）
- [ ] T8 新 Pod 带上了正确 version 标签（**必须验**）
- [ ] T9 指标恢复
- [ ] T10 告警自动 resolved

### 时间统计
告警送达耗时：__ 分钟
认领耗时：__ 分钟
定位耗时：__ 分钟
修复到同步耗时：__ 分钟
恢复总耗时：__ 分钟

### 结论
- 链路是否全程走通：是 / 否，卡在第 __ 步
- 最慢的一步是第 __ 步，用时 __ 分钟
- 告警内容是否够用：够 / 不够，缺 __
- version 标签是否帮上忙：帮上 / 没帮上，原因是 __
```

#### 3.5.4 复盘模板

| 复盘项 | 要回答的问题 | 产出 |
|---|---|---|
| **链路完整性** | 十步每一步都走通了吗？断在哪？ | 断点清单 |
| **告警质量** | 告警消息够不够定位？缺什么字段？ | 告警模板改进项 |
| **归因正确性** | 归因对了吗？依据是什么？ | 判定表补充 |
| **时间分布** | 哪一步最慢？慢在工具还是人？ | 优化项 |
| **误判风险** | 有没有人把演练当成真实故障？别人知道吗？ | **通知流程改进** |
| **复发可能** | 下次同样的故障，还能走通吗？ | 是否要加自动化处置 |
| **动作项** | 每条都要有**负责人 + 期限** | 任务清单 |

> **演练复盘里最值钱的一条不是"演练成功"，是"演练中有多少人以为是真故障"。** 如果一个人都没被骗过，说明告警和真实故障太像（或者大家事先知道太多）；如果所有人都被骗过且处置正确，说明**告警质量过关**。**"演练没提前通知导致误判为故障"** 是本篇必须点名的踩坑——不通知会造成两种后果：**要么被当成真故障处理（浪费人力、可能做错处置）**，**要么被当成演练而不认真对待（演练失效）**。**正确做法是：通知范围包含第三方（他们不知情），但明确告知"核心响应人这是演练"。**

### 3.6 成本与收益闭环（FinOps 视角与其边界）

**这一节要说清一件事：闭环不只是为了"稳定"，也能用来"省钱"——但能力是有边界的。**

#### 3.6.1 能闭环的四类成本信号

| 信号 | 怎么从闭环里拿到 | 能得出什么结论 |
|---|---|---|
| **资源利用率** | Prometheus 的 `kube_pod_container_resource_requests` / `usage` 对比 | **requests 虚高多少** → 资源画像 |
| **每副本承载量** | `QPS / 副本数` | **一个副本扛多少** → 副本数可以怎么降 |
| **闲置资源** | `requests` 长期为 0、或长时间无流量但仍占 requests | **哪些服务可以缩容 / 下线** |
| **按标签的成本归集** | 云厂商账单按 `env` / `app` / `team` 标签聚合 | **哪个团队、哪个环境花了多少** |

#### 3.6.2 三个必须说清的边界（**这是本节最重要的部分**）

| 边界 | 说明 | 后果 |
|---|---|---|
| **① 需要 exporter / 埋点支持** | **大多数指标要靠 exporter 暴露**（node-exporter、cAdvisor、kube-state-metrics、中间件各自的 exporter）。**没有 exporter，Prometheus 里就没有这条信号** | **"想用 Prometheus 做 FinOps"的前提是先部署一整套 exporter——这是独立的一笔工作量，不是装个 Prometheus 就有** |
| **② 业务层指标要自己埋** | QPS、订单量、每核承载量这些**不是基础设施给的**，要在应用里埋点并暴露 `/metrics` | **"每副本承载量"这类最有价值的 FinOps 指标，恰恰是最难拿到的** |
| **③ 自动归集需要云厂商侧打通** | 账单按标签聚合依赖云厂商支持资源标签 + 成本视图 | **本地 kind 上做不出这个闭环**——必须上 ACK 短跑验证 |

> **这一段必须讲，否则就是吹牛。** 真实情况是：**大多数团队做不到全自动 FinOps 闭环**，能做的是"采集到指标 → 人工看 → 人工判断"。**能说清"哪一步还差什么"的，比声称"我们做了自动化成本归集"更可信。**

#### 3.6.3 逐步可落地的四级路径

| 级别 | 能力 | 需要什么 | 团队规模适配 |
|---|---|---|---|
| **L1** | 看得到利用率 | Prometheus + kube-state-metrics | **任何团队**（最低成本，先做这个） |
| **L2** | 看得懂业务量 | 应用埋点 `/metrics`（QPS / 延迟 / 错误率） | 小团队可做（就几个服务） |
| **L3** | 归得到人 | 云厂商资源标签 + 账单视图 | 需要云厂商账号权限 |
| **L4** | 自动优化 | KEDA / HPA 基于业务指标 + 定时伸缩策略 | **需要人审慎设置护栏** |

> **推荐落地顺序：L1 → L2 → L3，L4 慎重。** **L4 的风险在于"自动优化出错时的爆炸半径"**——一个写错的自动伸缩策略在业务高峰把副本缩到 0。**建议 L4 只在非核心服务上开，且必须配 `minReplicas` 地板和 PDB。**

---

### 3.7 端到端排障与运维命令速查（闭环运行期）

> **本节能做什么**：3.1–3.6 讲的是"怎么把交付闭环和可观测闭环接起来"，本节是"闭环转起来之后，**每天值班 / 面试都会敲的端到端排障命令**"——按全链路环节归类（代码提交 → CI 构建 → 镜像推 Harbor → ArgoCD 同步 → K8s 运行 → 监控告警），每条配真实例子。闭环类系统的排障难点是"链路长 + 静默失败多"，这一张表就是运行期的肌肉记忆。

#### ① 代码提交与 CI 构建排障（链路最前端）

```bash
git rev-parse HEAD                              # 取当前 commit SHA（= 镜像 tag，全链路排障锚点）
git log --oneline -10                           # 看最近提交，确认本次提交在列
git status                                      # 看是否被 .gitignore 漏掉关键文件导致 CI 没触发
glab ci status                                  # GitLab CLI 看本次流水线状态（红/绿）
glab ci get-log <job-id>                        # 看某个 Job 日志（无 CLI 就用网页）
gitlab-runner verify                            # 私有 Runner 是否在线（流水线一直 Pending 先查这个）
kubectl get pods -n gitlab-runner               # Runner 以 Pod 形式跑时的状态
```

> **实战价值**：链路排障第一原则——**先用 `git rev-parse HEAD` 拿到 SHA，它就是贯穿 CI / Harbor / ArgoCD / K8s 的锚点**。流水线 Pending 不一定是代码问题，八成是 Runner 掉线或配额满；先看 `glab ci status` 再决定往哪段查。

#### ② 镜像推 Harbor / ACR 排障（制品这一环）

```bash
docker images | grep order-api                 # 确认本地镜像与 tag（tag 必须 = commit SHA）
docker tag order-api:<sha> registry.cn-hangzhou.aliyuncs.com/linux321-devops/order-api:<sha>
docker push registry.cn-hangzhou.aliyuncs.com/linux321-devops/order-api:<sha>
docker buildx build --platform linux/amd64 -t <repo>:<sha> --push .   # 跨平台构建直推
docker logout registry.cn-hangzhou.aliyuncs.com && docker login ...    # 401 先清登录态重登
cat ~/.docker/config.json | grep auth          # 看 ACR 凭据是否还在（401 先查这个）
crane digest <repo>:<sha>                      # 看 Harbor/ACR 里这个 SHA 的 digest 是否真存在
skopeo inspect docker://<repo>:<sha>           # 不拉全镜像只查 manifest（比对 size/config）
```

> **实战价值**：`ImagePullBackOff` 在整合项目里 80% 是"tag 不对 / 凭据过期 / 架构不匹配"三选一。`crane digest` 能在不拉镜像的前提下确认制品到底进没进仓库——**比盲目重推省十分钟**。架构坑：Mac 上是 arm64，生产节点是 amd64，必须 `buildx --platform linux/amd64`。

#### ③ ArgoCD 同步排障（GitOps 拉模型）

```bash
argocd app list                                # 看所有 App 的 SYNC / HEALTH 状态
argocd app get order-api                       # 看单个 App 的资源树与同步详情
argocd app sync order-api                      # 手动触发同步（PR 合并后没自动拉时）
argocd app diff order-api                      # 预览"Git 期望态 vs 集群实际态"差异（漂移检测第一招）
argocd app history order-api                   # 看部署历史（回滚用）
argocd app rollback order-api 3                # 回滚到历史版本 3
argocd app manifests order-api                 # 看 ArgoCD 最终会 apply 的渲染后清单
kubectl get app -n argocd order-api -o yaml    # 直接看 App CR（OUT_OF_SYNC 字段）
```

> **实战价值**：`Synced` 但 `OutOfSync` 反复横跳 = 有人在集群里手改（漂移）；`Synced` 但 `Not Healthy` = 应用起了但探针 / 依赖没就绪。**ArgoCD 排障先 `app get` 看状态，再 `app diff` 看差异，别一上来就手动 `kubectl`。**

#### ④ K8s 运行态排障（集群这一环）

```bash
kubectl get pods -n prod -o wide -l app=order-api     # 看 Pod 落在哪节点 / IP（NODE 列第一入口）
kubectl rollout status deploy/order-api -n prod       # 发布是否就绪
kubectl describe deploy/order-api -n prod             # 看新 ReplicaSet 为什么起不来
kubectl logs -f deploy/order-api -n prod --tail=100   # 跟应用日志
kubectl logs deploy/order-api -n prod -p --tail=50    # 看崩溃前日志（CrashLoop 必用 -p）
kubectl get events -n prod --sort-by=.lastTimestamp   # 看集群事件（OOMKilled / FailedScheduling）
kubectl top pods -n prod                             # 看资源占用（HPA 是否真生效）
kubectl get hpa -n prod                             # 看分母是否为 <unknown>（没写 requests）
```

> **实战价值**：整合项目里 `CrashLoopBackOff` 多半是"新镜像配置读不到 Secret"或"依赖的 Redis / DB 没起来"。**`logs -p` 拿崩溃瞬间日志，`describe` 的 Last State 拿退出码，两个一起看才准。** HPA 显示 `<unknown>` 就是忘了写 `resources.requests`，副本永远不扩。

#### ⑤ 监控告警排障（观测闭环这一环）

```bash
kubectl get pods -n monitoring | grep -E 'prometheus|grafana|loki|alertmanager'   # 组件是否活着
kubectl port-forward -n monitoring svc/prometheus 9090:9090   # 本地起 Prometheus 界面查 PromQL
promtool check rules /etc/rules/*.yaml             # 校验 PrometheusRule 语法（CI 门禁用）
kubectl get prometheusrule -n prod -o yaml         # 看告警规则是否加载
kubectl get servicemonitor -n prod                 # 看抓取目标是否注册（没它就没指标）
amtool alert query                                 # 查 Alertmanager 当前活跃告警
amtool silence add alertname=OrderErrorRate --duration=1h   # 维护窗口静默告警
kubectl logs -n monitoring deploy/loki -f          # Loki 卡住先查它自己
```

> **实战价值**："没告警"不一定是没问题——先 `amtool alert query` 确认告警真的发出来了，还是 Prometheus 根本没抓到指标（`ServiceMonitor` 没注册）。**观测闭环排障要分清"数据没采到"和"告警没路由"两件事。** `promtool` 把规则语法错误挡在 CI 阶段，比上线后发现强。

#### ⑥ 环境差异比对与配置漂移检测（面试高频）

```bash
# 配置漂移：Git 期望态 vs 集群实际态
argocd app diff order-api                          # ArgoCD 视角的漂移（最权威）
kubectl diff -f manifests/ -n prod                 # 本地清单 vs 集群实际态
# 环境差异：测试 vs 生产（逐字段比对）
kubectl get deploy/order-api -n test -o yaml > /tmp/test.yaml
kubectl get deploy/order-api -n prod -o yaml > /tmp/prod.yaml
diff /tmp/test.yaml /tmp/prod.yaml                 # 全量 diff
# 只比关键维度（副本数 / 镜像 / 资源 / 环境变量）
kubectl get deploy/order-api -n test -o jsonpath='{.spec.replicas}'; echo
kubectl get deploy/order-api -n prod -o jsonpath='{.spec.replicas}'; echo
kubectl get deploy/order-api -n prod -o jsonpath='{.spec.template.spec.containers[0].image}'; echo
# ConfigMap / Secret 漂移
kubectl get cm/order-api-config -n prod -o yaml    # 比对配置是否和 Git 里的一致
```

> **实战价值**：**"测试能过、生产挂"几乎都源于环境差异**——副本数、资源 limit、环境变量、镜像 tag 某一项没对齐。`diff` 两环境的 yaml 是最快定位法；`argocd app diff` 则直接告诉你"集群被手改了多少"。**面试能现场说出"怎么比对两环境差异、怎么检测漂移"，就是配置管理岗的加分项。**

#### ⑦ 回滚全链路（应用 + DB + 缓存）

```bash
# ① 应用层回滚（GitOps 托管的应用）
argocd app rollback order-api <last-good>          # ArgoCD 回滚到上一个健康版本
# 或直接 kubectl（非 GitOps 托管时）
kubectl rollout undo deploy/order-api -n prod
# ② DB 回滚（仅当本次发布含不兼容 schema 变更，且迁移脚本向下兼容、已提前备好）
kubectl exec -n prod deploy/order-api -- \
  flyway migrate -url=$DB_URL -user=$DB_USER -password=$DB_PWD -locations=filesystem:/db/rollback
# ③ 缓存处理（清脏缓存，避免读到旧结构；仅当缓存可安全重建）
kubectl exec -n prod deploy/redis -- redis-cli flushall
# ④ 回滚后验证三连
kubectl rollout status deploy/order-api -n prod
curl -s https://order.example.com/healthz          # 业务健康
argocd app get order-api                           # 确认 Healthy 且 Synced
# ⑤ 一致性校验：应用版本标签与 DB schema 版本对齐
kubectl get deploy/order-api -n prod -o jsonpath='{.spec.template.metadata.labels.version}'; echo
```

> **实战价值**：**回滚顺序 = 应用先回、依赖后回、缓存最后清**；DB 回滚是最后手段——优先用"向前兼容迁移"避免回滚数据（数据回滚的爆炸半径远大于应用）。**缓存能重建就 flush，不能重建就靠版本键隔离。** 这条顺序是面试里"发布翻车怎么救"的标准答案。

---

### 3.8 端到端排障实战（三个闭环场景串命令）

> **把 3.7 的命令用起来**：三个闭环运行期最典型的故障，按"现象 → 敲的命令 → 怎么读"走一遍。重点练"告警 → 定位 → 改 Git → 自动同步 → 恢复"这条标准动作链（见 3.2）。**都在 kind 集群 `day2` 上练，别在生产试。**

#### 场景 A：告警响了但定位不到哪次发布（版本标签断了）

```bash
amtool alert query                                 # ① 确认告警真发出来了（不是静默/路由错了）
kubectl get prometheusrule -n prod -o yaml | grep -A5 OrderErrorRate   # ② 看规则在不在
sum by(version) (rate(http_requests_total{app="order-api"}[5m]))       # ③ PromQL 看有几个 version
# 现象：只返回 1 个值（全 latest）→ 版本标签断了，指标对不上发布
kubectl get pods -n prod -l app=order-api --show-labels   # ④ 看 Pod 上有没有 version label
kubectl get servicemonitor -n prod -o yaml | grep sourceLabels   # ⑤ 看 relabel 是不是从 __meta_* 取
```

> **读法**：**闭环排障第一坑是"静默失败"**——告警没出来，往往不是没问题，而是版本标签断了导致指标查不到版本维度。`sum by(version)` 只返回一个值，就是 3.2 / 3.3 讲的"标签基数"问题：**Pod label 没打 version，或 ServiceMonitor 的 relabel 没从 `__meta_kubernetes_pod_label_version` 取。** 先 `amtool` 确认告警真发出，再顺着标签查，别在指标层面瞎猜。

#### 场景 B：改了 Git 但没自动同步（闭环断在 ArgoCD / Webhook）

```bash
argocd app get order-api                           # ① 看 SYNC 状态：OutOfSync 且不自动同步
argocd app diff order-api                          # ② 看 Git 期望态 vs 集群实际态差异
git -C gitops-repo log --oneline -5                # ③ 确认 PR 真的合并进 GitOps 仓库
argocd app sync order-api                          # ④ 手动补同步（Webhook 没触发时）
argocd app wait order-api --health                 # ⑤ 等 Healthy
kubectl get app -n argocd order-api -o yaml | grep -E 'operation|syncPolicy'   # ⑥ 看自动同步开关
```

> **读法**：闭环断在"Git 改了但集群没动"——先确认 **GitOps 仓库的 PR 真合并了**（`git log`），再看 **ArgoCD 是否开了自动同步**（`syncPolicy.automated`）。没开就 `app sync` 手动补；开了还不同步，查 Webhook 是否打到 ArgoCD 或网络是否通。**闭环的核心纪律：Git 是唯一事实源，一切变更走 Git，集群被手改会被 SelfHeal 拉回（这正是 3.2 强调的）。**

#### 场景 C：发布引发劣化要全链路回滚（应用 + DB + 缓存，并叠 Annotation）

```bash
# ① 告警确认劣化（错误率 / 延迟突增）
amtool alert query
# ② 先止血：应用回滚
argocd app rollback order-api <last-good>
# ③ 判 DB 是否需回滚（含不兼容 schema 变更才回滚，否则跳过）
kubectl get cm/order-api-config -n prod -o yaml | grep DB_SCHEMA_VERSION
# ④ 清脏缓存（仅可重建时）
kubectl exec -n prod deploy/redis -- redis-cli flushall
# ⑤ 回滚后三连验证
kubectl rollout status deploy/order-api -n prod
curl -s https://order.example.com/healthz
amtool alert query                                 # 确认告警已解除
# ⑥ 把这次发布/回滚事件叠到 Grafana 时间轴（闭环复盘用，见 3.4）
curl -X POST http://grafana:3000/api/annotations -H "Content-Type: application/json" \
  -d '{"text":"rollback order-api to <last-good>","tags":["release","rollback"],"time":'<ms'>'
```

> **读法**：**闭环里回滚不是终点，是复盘的起点**——回滚后要把事件写成 Grafana Annotation（3.4 讲的接缝④），下次看板上一眼能看到"哪次发布引发了劣化、哪次回滚救了场"。回滚顺序仍是"应用先回、依赖后回、缓存最后清"，DB 能不回就不回。**面试加分点：能说出"闭环排障 = 告警 → 定位 → 改 Git → 同步 → 恢复 → 标注"，并把六步和对应工具对上，就是可观测岗的高分答案。**

> **三个场景的共同心法**：① **闭环排障先确认"数据 / 告警真发了没有"**，静默失败比报错更危险；② **用 commit SHA 与 version 标签做锚点**，把"哪次发布"钉死；③ **回滚优先于修，标注闭环**——止血后别忘了在 Grafana 留痕，让下一次排障有迹可循。整合项目的排障，比单工具难在"链路长 + 反馈环"，把 3.7 每段命令练熟，面试时画得出"代码 → CI → Harbor → ArgoCD → K8s → 监控 → 人 → 改 Git"这条带反馈的环，就稳了。

---

## 4. 逐字段表

### 4.1 闭环环节 × 工具 × 产物 × 责任人 × 验证方式

| # | 闭环环节 | 工具 | 产物（进 Git 的文件） | 责任人 | 验证方式 | 断了的表现 |
|---|---|---|---|---|---|---|
| 1 | 代码变更 | GitLab | 代码仓库 | 研发 | MR 有评审记录 | 变更没有唯一入口 |
| 2 | 制品构建 | GitLab CI | 镜像（tag = commit SHA） | 研发 / 平台 | `docker manifest inspect` 看架构与 digest | 镜像不可识别，指标对不上版本 |
| 3 | 质量门禁 | SonarQube + Trivy | 扫描报告（artifact） | 研发 / 安全 | **故意造漏洞 → 流水线必须红** | 高危制品进生产 |
| 4 | 事实源 | Git | `charts/` `values-*.yaml` | 平台 / 研发 | `git log` 能看出谁改的 | 期望态无唯一来源 |
| 5 | 同步 | ArgoCD | `argocd/application.yaml` | 平台 | `argocd app get` → SYNCED | 集群与 Git 不一致 |
| 6 | 纠偏 | ArgoCD SelfHeal | 同上（`selfHeal: true`） | 平台 | `kubectl scale` 后被拉回 | 漂移长期存在 |
| 7 | 采集 | Prometheus Operator | `servicemonitor.yaml` | 平台 / SRE | Prometheus UI 查 `up` | **监控盲区**（新服务没被采到） |
| 7b | **标签传递** | ServiceMonitor `relabelings` | 同上 | 平台 | 指标 Labels 里有 `version` | **接缝断：指标对不上版本** |
| 8 | 可视化 | Grafana | `dashboards/*.json`（provisioning） | 平台 / SRE | 面板出数；`kubectl get cm` 里能看到 JSON | 只有数据没人能读懂 |
| 9 | 日志 | Promtail → Loki | `loki/promtail.yaml` | 平台 / SRE | **删 Pod 后仍能查到历史日志** | 只能现捞，Pod 一删日志就没 |
| 10 | 判定 | PrometheusRule | `prometheus/rules/*.yaml` | SRE / 研发 | Prometheus UI 的 Rules 页能看到规则 | 只有原始指标，没有"这算不算事" |
| 10b | **告警带版本** | PrometheusRule 的 `by(app, version)` | 同上 | SRE | 触发后看告警 Labels 与正文 | **告警说"错了"但不知道"哪次发布"** |
| 11 | 路由 | Alertmanager | `alertmanager/alertmanager.yaml` | SRE | 同时触发多条 → 应被合并 | 告警风暴 / 没人认领 |
| 12 | 通知 | 钉钉 / 电话 | 值班表 + 路由规则 | SRE / 值班 | 演练时确认送达 | 告警发了没人接 |
| 13 | 处置 | 人 | **PR**（改 values / 代码） | 值班 / 研发 | PR 有评审记录 | **闭环在此断掉** |
| 14 | 发布事件 | Grafana Annotation | `release-events/*.yaml` | 研发 | 看板上出现发布竖线 | 看板上只有指标，看不出何时发过版 |
| 15 | 成本归集 | Prometheus + 云账单 | 标签规范文档 | 平台 / FinOps | 账单能按 `env` / `app` 聚合 | **钱花在哪不知道** |

### 4.2 版本标签与告警标签逐字段表

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `podTemplate.metadata.labels.app` | string | ✅ | 应用标识 | 告警无法定位应用；看板筛选失效 |
| `podTemplate.metadata.labels.version` | string | ✅ | **版本标识（commit SHA / 语义化版本）** | **指标无法关联发布；告警说"错"不知"哪次发布"** |
| `podTemplate.metadata.labels.env` | string | ✅ | 环境标识 | dev 的指标混进 prod 看板 |
| `ServiceMonitor.metadata.labels.release` | string | ✅ | **匹配 Prometheus 的 serviceMonitorSelector** | **不匹配 → ServiceMonitor 存在但不被读取 → 指标为空且不报错** |
| `ServiceMonitor.spec.namespaceSelector.matchNames` | list | ✅ | 抓哪些命名空间 | **新环境上线后没监控** |
| `ServiceMonitor.spec.endpoints[].relabelings[].sourceLabels` | list | ✅ | `__meta_kubernetes_pod_label_*` | 写成 `version`（去掉前缀）→ **静默不生效** |
| `ServiceMonitor.spec.endpoints[].relabelings[].targetLabel` | string | ✅ | 指标上的标签名 | 写错名字 → 指标里没有这个标签 |
| `ServiceMonitor.spec.endpoints[].interval` | duration | | 采集间隔（**默认 15s 量级**） | 太长 → 告警慢；太短 → 存储压力 |
| `PrometheusRule.metadata.labels.release` | string | ✅ | **匹配 Prometheus 的 ruleSelector** | **规则存在但不加载 → 沉默的告警缺失** |
| `PrometheusRule.spec.groups[].rules[].expr` | string | ✅ | PromQL | 语法错 → 规则不加载；`by(version)` 漏了 → 无法定位发布 |
| `PrometheusRule...rules[].for` | duration | ✅ | 持续多久才触发 | 不写 → 抖动即告警；写太长 → 真问题被延后 |
| `PrometheusRule...rules[].labels.app` | string | ✅ | **告警归属应用** | 告警进大群没人认领 |
| `PrometheusRule...rules[].labels.severity` | string | ✅ | 分级（critical / warning / info） | **不分级 → 所有告警一个待遇 → 真正严重的被淹没** |
| `PrometheusRule...rules[].annotations.summary` | string | ✅ | 一句话是什么 | 收到告警不知道是什么事 |
| `PrometheusRule...rules[].annotations.description` | string | ✅ | 影响谁 + 哪个版本 | **只说"CPU 高"，不知道影响哪个应用** |
| `PrometheusRule...rules[].annotations.runbook_url` | string | ✅ | 处理手册 | 每次现查，MTTR 拉长 |
| `Application.spec.syncPolicy.automated.selfHeal` | bool | ✅ | 自动还原漂移 | 漂移长期存在 |
| `Application.spec.syncPolicy.automated.prune` | bool | ⚠️ 分级 | 清理孤儿资源 | **开在有状态资源上 = 删数据** |
| `Application.spec.syncOptions` | list | | `PruneLast=true` 等 | 资源被提前删 → 同步中断 |
| `AppProject.spec.sourceRepos` | list | ✅ | 允许的仓库 | 太宽 → 任意仓库可同步进来 |
| `AppProject.spec.destinations` | list | ✅ | 允许的命名空间 | **不限定 → 一个 App 同时管 dev 和 prod** |
| `AppProject.spec.clusterResourceWhitelist` | list | ✅ | 允许的集群级资源 | 太宽 → 能删 CRD |

---

## 5. 横向对比

### 5.1 推模型 CI/CD vs 拉模型 GitOps

| 维度 | 推模型（Jenkins / CI 里 `kubectl apply`） | 拉模型（ArgoCD） |
|---|---|---|
| **谁持有集群凭证** | **CI 服务器**，权限大 | **集群内的 ArgoCD**；CI 无集群权限 |
| **CI 被攻破的后果** | **生产集群沦陷** | 最多改一个 Git 仓库，需 PR 才生效 |
| **集群被手改后** | 无人知晓，漂移长期存在 | 控制器持续对比，SelfHeal 自动还原 |
| **回滚** | 重跑旧流水线，**可能重建错版本** | `git revert` 或选历史 revision，一键同步 |
| **变更记录在哪** | CI 日志（易丢、易过期） | **Git commit 历史（永久、可审计）** |
| **生产发布审计** | 靠日志拼凑 | **Git PR + 审批记录，等保直接可用** |
| **同步延迟** | 流水线跑完即生效（快） | 轮询周期或 webhook 触发（略慢） |
| **谁能改生产** | 有 kubeconfig 的人都能 | **只有能提 PR 的人** |
| **多集群** | 每套一套脚本 | 一个 ArgoCD 管多集群 |
| **本地开发体验** | 直接 `kubectl apply` 试 | 改 Git → 等同步（**慢，但保证一致**） |
| **适合** | 单人 / 小团队 / 快速试验 | **多人协作、需要审计、需要防漂移** |

> **注意最后一行不是"拉模型更好"**。**小团队、一个人维护两三个服务时，推模型其实更省事。** GitOps 的价值来自**协作规模和审计需求**，不是来自"更先进"。

**面试这么答**："GitOps 和传统 CI/CD 的核心差别是**谁持有集群写权限**。推模型下 CI 服务器握着生产 kubeconfig，CI 被攻破就是生产沦陷；拉模型下集群里的 ArgoCD 拿着 Git 读权限，CI 只有 Git 写权限，而且还得走 PR。第二个差别是**审计**：推模型的变更记录在 CI 日志里，容易过期；GitOps 的变更记录就是 commit 历史，等保查变更直接导，不用拼日志。"

### 5.2 纯监控 vs 可观测闭环

| 维度 | 纯监控（装了 Prometheus + Grafana） | 可观测闭环 |
|---|---|---|
| **回答什么问题** | "有什么指标" | **"是不是这次发布引起的"** |
| **指标里有没有版本** | 通常没有（只有 `instance` / `pod`） | **有 `app` / `version` / `env`** |
| **告警里有没有归属** | 通常只有 `severity` | **有 `app` / `env` / `version` + runbook** |
| **能不能直接开始修** | 不能，要先自己查"是哪个服务" | **能：告警 → 看板 → 日志，三跳定位** |
| **发布与指标的关系** | 靠人记 | **Annotation 叠加在时间轴上** |
| **配置管理** | 监控配置躺在 Grafana UI 里 | **监控规则 / 看板 JSON 进 Git，可评审可回滚** |
| **故障后的恢复** | 靠人发通知 + 人执行 | 人只改 Git，**系统自动同步** |
| **成熟度判据** | "Grafana 上能看到图" | **"收到告警到业务恢复，全程不需要从控制台复制粘贴"** |

> **"纯监控"和"闭环"的分界点只有一句话**：**纯监控是"人能查到"，闭环是"系统能自转"。** 一个装了 Prometheus 但指标里没有版本标签的团队，**在能力上等同于没装监控**——因为它回答不了"是不是这次发布引起的"。

### 5.3 全量上 vs 分步上（推荐分步）

**推荐的分步顺序**（每一步都能独立产生价值）：

| 步 | 上什么 | 产出价值 | 需要的前置 | 成本 | 风险 |
|---|---|---|---|---|---|
| **第 1 步** | **Prometheus 采集 + Grafana 看板** | **看得见**：知道服务在跑、指标长什么样 | 应用暴露 `/metrics`；装 exporter | **低** | 低（只读，不改业务） |
| **第 2 步** | **版本标签 + PrometheusRule + Alertmanager** | **看得懂 + 叫得醒**：告警能定位到应用和版本 | 第 1 步 + 标签改造 | 中 | 中（**告警定太严会被静音**） |
| **第 3 步** | **Loki 日志管道** | **查得到根因**：删 Pod 后仍能查日志 | 第 1 步 | 中 | 中（**基数爆炸 / 磁盘写满**） |
| **第 4 步** | **ArgoCD 自动同步 + SelfHeal** | **回得去**：Git 改了自动生效，集群不漂移 | Chart 化 + 权限收敛 | 中高 | **中高**（写入口收窄，团队要适应） |
| **第 5 步** | **Annotation + 渐进发布接指标门禁** | **发得稳**：灰度自动判定中止 | 第 2 步 + 第 4 步 | 高 | 中 |

**为什么是这个顺序**：

| 理由 | 说明 |
|---|---|
| **先能看到，才谈得上判定** | 没有指标，第 2 步的告警阈值只能拍脑袋，**必然误报** |
| **先能定位，再谈自动修复** | 第 4 步的自动同步依赖"人能改对 Git"；人不会定位 → 改错 Git → 自动同步把错误放大 |
| **先有告警，再上自动发布门禁** | 渐进发布的 AnalysisTemplate 读的就是第 2 步建的指标 |
| **风险递增** | 前三步都是"只读/加观测"，几乎不影响业务；第 4 步开始改写入口，**风险台阶在这一步** |

> **不建议的做法**：**先上 ArgoCD 再补监控。** 理由很实在：**自动同步会放大错误的变更**。如果此时连"变更是否有效"都看不到，一次同步失败或一个错误的 values 改动，会以你察觉不到的速度扩散到所有副本。

> **另一个不建议**：**先开自动扩缩再补监控**。理由是 D2 场景 B 里那条：**HPA 扩了但没人知道**——你需要监控来确认扩容是否真的缓解了负载，否则就是在盲调。

### 5.4 三个闭环的成熟度分级

| 级别 | 交付闭环 | 观测闭环 | **合起来是什么** | 怎么判断自己在哪级 |
|---|---|---|---|---|
| **L0** | 手工 `kubectl apply` | 装了 Grafana 看图 | **无闭环** | 集群里有一堆不在 Git 里的东西 |
| **L1** | CI 推镜像 + 手工部署 | Prometheus 采集 + 看板 | **有工具，没回路** | 指标里没有 `version` 标签 |
| **L2** | Helm Chart + 多环境 values | 版本标签 + 规则 + 告警 | **单边闭环**（观测能叫醒人） | 告警不带 app/version |
| **L3** | ArgoCD 拉模型 + SelfHeal | Annotation + 日志管道 | **双边闭环**（改 Git 自动生效） | 集群写权限还在人手 |
| **L4** | 灰度门禁 + 自动回滚 | 自动归因 + 演练常态化 | **自转闭环**（人只做决策） | 从未演练过就以为闭环了 |

**10 几人团队的现实目标**：**稳定到 L2，认真做 L3。** L4 依赖 L3 的成熟度，跳级做 L4 的一定会翻车。

---

## 6. 踩坑与排障

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **发布后指标劣化，但说不清是不是这次发布引起的** | 指标里 `version` 只有一个值 | **版本标签没打**（或打在了错误的层） | ① Chart 模板 `podTemplate.labels` 加 `version`；② ServiceMonitor 的 `relabelings` 把 Pod label 复制成指标 label | 标签集中在 `_helpers.tpl` 一处定义；**每次发布后必查 `sum by(version)(...)` 是否返回多个值** |
| **Pod 上明明有 version 标签，Prometheus 里查不到** | 指标 Labels 里没有 version | **relabel 的 sourceLabels 写错**：应写 `__meta_kubernetes_pod_label_version`，写成了 `version` | 改正 sourceLabels 前缀 | **这是静默失败**：改完必须去 Prometheus UI 看指标的 Labels 列表确认 |
| **ServiceMonitor 建了但 Prometheus 一个指标都没采到** | Prometheus 无数据 | **`metadata.labels.release` 没匹配上 `serviceMonitorSelector`** | 改成与 kube-prometheus-stack 一致的 label | 装完先 `kubectl get servicemonitor -A` 看有没有被选中；用 `promtool` 或 Prometheus UI 验证 |
| **PrometheusRule 建了但告警永远不触发** | 规则不生效 | **`labels.release` 没匹配 `ruleSelector`**（规则被创建但不加载） | 对齐 label | 加规则后**必须在 Prometheus 的 Rules 页看到它**，不能只看 `kubectl get` 成功 |
| **告警一晚上几百条，手机被静音** | 告警风暴 | 没分组没抑制 | `group_by: [alertname, app]` + `inhibit_rules`（critical 抑制同 app 的 warning）+ `repeat_interval` 拉长 | severity 分级路由；只推 main 分支失败与生产发布失败，其余进日报 |
| **告警发出去了但没人认领** | 无人响应 | 全推一个大群，没有归属 | 按 `app` / `team` 标签分群 + 值班表 + **升级规则** | **告警 label 必须带 `app` 和 `env`**；值班表进 Git |
| **Grafana 挂了，告警也静默** | 无通知 | 告警挂在 Grafana Panel 上 | 迁到 PrometheusRule + Alertmanager | **生产告警一律走 Alertmanager**，Grafana 只做可视化 |
| **改了 Git，集群没变** | `OutOfSync` 长期存在 | repo URL / 分支 / path 写错；或 RBAC 不足 | `argocd app diff` 看差异 | Application 定义进 Git；变更走 PR |
| **手工改了集群，过一会儿被改回去** | 参数莫名还原 | **SelfHeal 把漂移拉回了 Git 的样子**（这是设计行为） | 热修走 PR；实在不行先 `argocd app set --sync-policy none` 并**立刻登记** | 集群 RBAC 收口；季度审计"谁有写权限" |
| **ArgoCD 和 CI 互相覆盖，副本数来回抖** | 反复 reconcile | **CI 里 `kubectl set image` + ArgoCD 同步** | CI 只写回 Git 的 values；集群写入权只给 ArgoCD | **PR 评审要查 diff 里有没有意外的集群操作** |
| **副本数被 Helm 重置，HPA 又调回去** | 反复抖 | 两个控制器抢同一字段 | replicas 归 HPA 独占，Chart 里不设 `replicas` | Chart 模板写死这个约定 |
| **Loki 内存打爆 / 节点盘被日志吃满** | OOM / 磁盘满 | **把 traceId、requestId、userId、完整 URL 提成 label** | label 只放低基数（app/ns/pod/level），高基数字段留正文用 LogQL 的 json 解析器取 | 采集配置评审时专门查"哪些字段被提成了 label" |
| **Loki 里查不到已删 Pod 的日志** | 无数据 | 日志还在容器本地；或 label 名不对（Helm 版常是 `app_kubernetes_io_name`） | 查 `curl localhost:3100/loki/api/v1/labels` 确认真实 label 名；确认应用打 stdout | **验收标准写死"删 Pod 后仍能查到"** |
| **日志平台成了第二个事故源** | 集群故障 | Loki 默认存本地且不设 retention，吃满节点磁盘 | 设 persistence + `retention_period`；按级别分级保留 | **日志盘写满 → 节点故障 → 集群故障**，这条链要写进应急预案 |
| **看板上只有指标，看不出什么时候发过版** | 无标记 | 没有 Annotation | 发布时写事件（CI 推 API 或事件文件进 Git） | 事件文件带 `time`（**毫秒**）/ `version` / `text` |
| **Annotation 看不见（API 返回 200 但看板上没有）** | 无标记 | **`time` 用了秒而不是毫秒** → 标记跑到 1970 年 | 改成毫秒时间戳 | **验证必须是"看板上能看见"，不能是"API 返回 200"** |
| **改了 `app` 标签，告警和看板同时失效** | 无数据 | 指标、告警路由、Grafana 变量三处都按标签筛选，**只改一处 = 静默失效** | 三处一起改；改完逐个验证 | 标签改名走 PR；评审时把"标签变更"当**破坏性变更**对待 |
| **演练被误判为真实故障** | 混乱 | **演练没提前通知**（或通知范围不当） | 提前通知值班群 + 研发 + **至少一个不知情的第三方**；明确"核心响应人知道这是演练" | **演练前必须通知，且通知范围要包含一个不知情方**，否则测不出告警质量 |
| **演练告警质量没人验证** | — | 所有人都事先知道是演练 → 处置流程被"演"过去了 | 让第三方参与并记录他的第一反应 | 观察员只记录不参与 |
| **闭环建好了但没人用** | 告警被忽略 | **闭环没有落到"人"的动作上**——告警不告诉你该干什么 | 告警带 `runbook_url`；告警消息直接给看板链接 | 每次告警后问"这条告警够不够让我直接开始修"，不够就改模板 |
| **闭环转不起来（改 Git 后没人合并、没人看告警）** | 全部停滞 | **闭环没有责任人**——没有人的绩效或职责挂在上面 | 明确每个环节的 owner；值班表进 Git | 每个闭环环节都要有名字，不能都是"运维" |
| **Grafana 面板全空** | 无数据 | PromQL 对但数据源 / 时间窗 / label 不对 | 先在 Explore 单跑 PromQL 验证，再贴面板 | 社区 Dashboard 最常见的问题是指标名版本不匹配 |
| **Kustomize 加标签后 apply 报错** | `selector does not match template labels` | `labels` 默认同时改了 selector 和 template | `includeSelectors: false` | 标签覆盖一律带这个参数 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `argo-cd.readthedocs.io/en/stable/` | ArgoCD 总览、Application / AppProject、拉模型原理 | ✅ 2026-10-24 |
| S2 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/sync-options/` | 同步策略：Auto-sync / SelfHeal / Prune / PruneLast / ApplyOutOfSyncOnly | ✅ 2026-10-24 |
| S3 | 官方 | `prometheus.io/docs/introduction/overview/` | 拉模型数据模型与架构、目标发现 | ✅ 2026-10-24 |
| S4 | 官方 | `prometheus.io/docs/prometheus/latest/getting_started/` | 入门与配置 | ✅ 2026-10-24 |
| S5 | 官方 | `prometheus.io/docs/prometheus/latest/querying/basics/` | PromQL 选择器、`by(...)` 聚合 | ✅ 2026-10-24 |
| S6 | 官方 | `prometheus.io/docs/prometheus/latest/querying/functions/` | `rate()` / `histogram_quantile()` 等函数 | ✅ 2026-10-24 |
| S7 | 官方 | `prometheus.io/docs/alerting/latest/alertmanager/` | 路由、分组、抑制、静默 | ✅ 2026-10-24 |
| S8 | 官方 | `grafana.com/docs/grafana/latest/` | 总览 | ✅ 2026-10-24 |
| S9 | 官方 | `grafana.com/docs/grafana/latest/getting-started/` | 入门与数据源 | ✅ 2026-10-24 |
| S10 | 官方 | `grafana.com/docs/grafana/latest/dashboards/` | 仪表盘、provisioning、告警位置 | ✅ 2026-10-24 |
| S11 | 官方 | `grafana.com/docs/loki/latest/` | Loki 总览与只索引 label 的设计 | ✅ 2026-10-24 |
| S12 | 官方 | `grafana.com/docs/loki/latest/get-started/` | Loki 入门 | ✅ 2026-10-24 |
| S13 | 官方 | `grafana.com/docs/loki/latest/get-started/components/` | Loki 组件与部署模式 | ✅ 2026-10-24 |
| S14 | 官方 | `kubernetes.io/zh-cn/docs/concepts/workloads/controllers/deployment/` | Deployment 滚动更新与 `maxSurge`/`maxUnavailable` | ✅ 2026-10-24 |
| S15 | 官方 | `kubernetes.io/zh-cn/docs/concepts/workloads/pods/` | Pod 生命周期与探针语义 | ✅ 2026-10-24 |
| S16 | 官方 | `kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-pods/` | Pod 排障、Events 读取 | ✅ 2026-10-24 |
| S17 | 官方 | `kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/` | HPA 行为与缩容窗口 | ✅ 2026-10-24 |
| S18 | 官方 | `kubernetes.io/zh-cn/docs/concepts/extend-kubernetes/operator/` | Operator 模式（ServiceMonitor / PrometheusRule 背后的模式） | ✅ 2026-10-24 |
| S19 | 官方 | `argoproj.github.io/argo-rollouts/` | AnalysisTemplate 接 Prometheus（渐进发布门禁） | ✅ 2026-10-24 |
| S20 | 官方 | `helm.sh/zh/docs/chart_template_guide/` | Chart 模板与 labels 辅助模板（中文） | ✅ 2026-10-24 |
| S21 | 官方 | `helm.sh/docs/topics/charts/` | Chart 结构与版本管理 | ✅ 2026-10-24 |
| S22 | 官方 | `docs.gitlab.com/ci/yaml/` | `rules` / `artifacts` / `when: manual` | ✅ 2026-10-24 |
| S23 | 官方 | `kubernetes.io/zh-cn/docs/concepts/cluster-administration/logging/` | 集群日志架构（日志外置的理由） | ✅ 2026-10-24 |
| S24 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 上的可观测形态与托管 Grafana | ✅ 2026-10-24 |
| S25 | 官方 | `kubernetes.io/docs/reference/using-api/` | 资源清单字段语义交叉验证 | ✅ 2026-10-24 |

> **本篇不给成本数字**：FinOps 部分给的是"信号来源 + 能力边界 + 落地分级"，**具体节省比例必须用本项目的账单实测**（算法见 [`L1-巨石应用云原生化.md`](L1-巨石应用云原生化.md) 3.10 节）。
> **完整来源矩阵（含全部 14 个组件）见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。**

---

## 8. 自测三问

1. **能说清输入输出吗？**
   - **输入**：一个已经容器化并部署到 K8s 的应用（有 Chart、有 GitOps 仓库）。
   - **输出**：一个**能自己转的闭环**——代码变更 → 制品 → Git → 自动同步 → 指标/日志/告警 → 人 → 改 Git → 自动同步 → 恢复。
   - **中间**：交付闭环（ArgoCD 拉模型 + SelfHeal）+ 观测闭环（Prometheus 拉模型 + Grafana + Loki + Alertmanager），**两者的接缝是版本标签**（四处埋点：Pod label → relabel → 规则聚合 → 告警标签）。
   - **再进一步**：输入"我要知道这次发布是不是导致了劣化"，输出是**一条 PromQL + 一句判据**（`sum by(version)(rate(...))` 返回多个 version 值）+ **一条归因决策表**（配置 / 代码 / 资源 / 依赖）。

2. **能故意改坏再倒推吗？** —— 六个可执行实验（**在 kind 集群 `day2` 上做**）：

   | # | 改什么 | 会看到什么 | 倒推出的机制 |
   |---|---|---|---|
   | ① | 从 Chart 模板里删掉 `version` 标签 | `sum by(version)(rate(...))` 只返回**一个值**（全 `latest`）→ "指标劣化对不上发布" | 版本标签必须同时打在 **Pod label** 和**指标 label** 两个地方 |
   | ② | 把 ServiceMonitor 的 `sourceLabels` 从 `__meta_kubernetes_pod_label_version` 改成 `version` | **不报错，但指标里还是没有 `version`** | relabel 只能从**服务发现阶段的 `__meta_*` 元标签**取值；这是**静默失败** |
   | ③ | 在 ArgoCD 开着的环境里 `kubectl scale` | App 变 `OutOfSync`，随后被 SelfHeal 拉回 | Git 是唯一事实源；**手改集群 = 制造漂移，下次同步被改回去** |
   | ④ | 一次触发 50 个实例的告警 | 手机收到 50 条 | Alertmanager 的 `group_by` / `inhibit_rules` / `repeat_interval` 三个手段 |
   | ⑤ | 在 Promtail 配置里把 `traceId` 提成 label | Loki 内存上涨 / 节点盘被吃满 | **标签基数爆炸**；label 只放低基数维度 |
   | ⑥ | 在 Grafana Annotation 里把 `time` 写成秒（不是毫秒） | **API 返回 200，但看板上找不到标记**（跑到 1970 年去了） | 时间戳单位错了是**静默失败**，验证必须"看板上看得见" |

   > **六个实验的共同点**：**四个是静默失败**（②④⑤⑥）——不报错、不告警、只是"数据不对"。**"静默失败"是闭环类系统最危险的失败模式**，所以本篇反复强调"验证要看结果，不能看 API 返回码"。

3. **能画上下游吗？** —— 画这条**带反馈的环**，并标出每处的接缝：

```
代码 ──CI──▶ 镜像 ──▶ GitOps仓库(期望态) ──ArgoCD拉──▶ 集群(实际态)
  ▲                  │                        │              │
  │                  │                   SelfHeal 拉平     │ scrape
  │                  │                                       ▼
  │                  │                                 Prometheus ◀──Loki
  │                  │                                   │      ▲
  │                  │                                   ▼      │ stdout
  │                  │                              PrometheusRule  │
  │                  │                                   │      Promtail
  │                  │                                   ▼
  │                  │                              Alertmanager
  │                  │                                   │
  │                  │                                   ▼
  └──── PR ←──── 人 ←── 归因(配置/代码/资源/依赖) ←────────┘
                    ▲
                    └── Grafana Annotation（发布事件叠加在时间轴上）
```

**并在图上标出四处接缝**（这是本篇最需要记住的东西）：

| 接缝 | 位置 | 断了会怎样 |
|---|---|---|
| **① Chart → Pod label** | `_helpers.tpl` | 指标没有版本维度 |
| **② Pod label → 指标 label** | ServiceMonitor 的 relabelings | 指标查不到版本（**静默**） |
| **③ 指标 → 告警** | PrometheusRule 的 `by(app, version)` | 告警说"错"不说"哪次发布" |
| **④ 发布 → Annotation** | 发布事件文件 / API | 看板上看不出何时发过版 |

---

**上一篇**：[`L1-巨石应用云原生化.md`](L1-巨石应用云原生化.md) · **关联中间件篇**：[`../06-云原生中间件/L2-中间件Operator选型.md`](../06-云原生中间件/L2-中间件Operator选型.md) · 回到 [`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
