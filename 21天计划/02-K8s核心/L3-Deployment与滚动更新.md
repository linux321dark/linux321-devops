# L3 · Deployment 与滚动更新（会发布 → 会回滚 → 说得清为什么卡住）

> **本篇定位**：把"改个镜像"这件小事拆到机制层。**为什么 Deployment 下面还有 ReplicaSet？为什么单副本发布会短暂跑 2 个 Pod？回滚到底回滚了什么？`kubectl edit` 改的东西为什么会凭空消失？**
> **前置**：[`L2-K8s架构与对象模型`](L2-K8s架构与对象模型.md)（必须理解"期望态 / 实际态 / 协调"这套模型，否则本篇的控制器行为讲不通）。
> **学完能做什么**：不看文档写出一份带就绪探针的 Deployment 清单；解释 `rollout status` 卡住时该看哪三个地方；知道回滚的真实机制是"改 Pod 模板"而不是"恢复旧 Pod"；能说清 `kubectl edit` 被 apply 覆盖的原因并引出 GitOps。
> **在 21 天里**：D2（Deployment + HPA）的发布语义部分；D7（整合项目）的上线章节。
> **环境/版本基准**：本文字段默认值以本地 kind 集群 `kubectl v1.37.1` / `Server v1.37.0` 为准；官方中文文档对应 v1.37 版本页。生产 ACK 版本不同，**字段语义以官方文档对应版本为准**。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的"三层追查"是本篇排障路径的骨架 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L3 Deployment 与滚动更新
│
├─ 1. 概念
│   ├─ 1.1 一句话：Deployment 卖的是"发布策略"，不是"进程守护"
│   ├─ 1.2 没有 Deployment 时的问题
│   ├─ 1.3 三层结构：Deployment → ReplicaSet → Pod 为什么是三层
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 三层结构的职责切分（谁管什么）
│   ├─ 2.2 pod-template-hash：版本隔离的关键标签
│   ├─ 2.3 滚动更新的两条策略：RollingUpdate vs Recreate
│   ├─ 2.4 maxSurge / maxUnavailable 的取整规则（重点）
│   ├─ 2.5 replicas=1 时两个默认值到底算出了什么（算给你看）
│   ├─ 2.6 就绪探针如何决定"新副本可用"
│   ├─ 2.7 回滚的真实机制：改 Pod 模板
│   ├─ 2.8 为什么 kubectl edit 会被下次 apply 覆盖
│   └─ 2.9 关键参数与默认值表
│
├─ 3. 动手
│   ├─ 3.1 一个"能发布"的 Deployment（含就绪探针）
│   ├─ 3.2 观察一次完整的滚动更新
│   ├─ 3.3 演练：回滚 / 重启 / 暂停恢复
│   └─ 3.4 验证清单
│
├─ 4. 资源清单逐字段
│   └─ 4.1 Deployment（逐字段 + 写错的后果）
│
├─ 5. 横向对比
│   ├─ 5.1 Deployment / StatefulSet / DaemonSet / Job 的更新语义差别
│   └─ 5.2 滚动更新 vs 蓝绿 vs 金丝雀：什么时候换工具
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

> **Deployment 是一个"声明式发布控制器"：你给它一个 Pod 模板和副本数，它负责把"换版本"这件事拆成"起新的 → 等新的好 → 删旧的"若干小步，并且每一步都尽量不让你掉可用容量。**

这句话里三个词是重点，面试时逐个展开：

| 关键词 | 含义 | 对应的机制 |
|---|---|---|
| **声明式** | 你不告诉它"怎么换"，你告诉它"最后要变成什么样" | 期望态 / 实际态持续比对 |
| **发布控制器** | 它的核心能力是"版本切换"，不只是"维持副本数" | ReplicaSet + revision 历史 |
| **不让你掉可用容量** | 它宁可慢，也不轻易把可用 Pod 数降到目标以下 | `maxUnavailable` 约束 |

> **对比 ReplicaSet**：ReplicaSet 只保证"此刻有 N 个 Pod"，Deployment 保证的是"**在从旧版本切到新版本的过程中**，可用 Pod 数不随意塌陷"。**这多出来的那一部分能力，就是发布。**

### 1.2 没有 Deployment 时的问题

裸机 / 脚本部署一个服务，发版时你要自己处理什么？

| 裸机发版要处理的事 | 手工做会怎样 | Deployment 怎么做 |
|---|---|---|
| 什么时候停旧进程 | 停早了 → 502；停晚了 → 资源浪费 | `maxUnavailable` 决定最多同时停几个 |
| 新进程什么时候算"能用" | 你得自己探测端口 | 就绪探针（readinessProbe）说了算 |
| 新版本起不来怎么办 | 已经停掉了旧的 → 只能回滚 | 旧的先留着，等新的 ready 再换 |
| 出问题怎么退回去 | 翻备份、记命令 | `rollout undo`，改回旧 Pod 模板 |
| 谁在跑哪个版本 | 靠人记 | `pod-template-hash` + `rollout history` |
| 多个实例怎么换 | 一台一台手动换 | 新旧 ReplicaSet 交替扩缩 |

> **关键认知**：Deployment 解决的不是"多开几个进程"，而是"**如何把一批正在服务的进程换掉，同时不让服务断**"。**这是它和 ReplicaSet 的根本差别。**

### 1.3 三层结构：Deployment → ReplicaSet → Pod 为什么是三层

这是本篇第一个高频面试题。先看职责切分，再讲为什么不能压成两层。

```
        ┌──────────────────────────────────────┐
        │  Deployment  「发布策略层」           │
        │  · 持有 Pod 模板（唯一真相）          │
        │  · 决定新旧版本如何交替              │
        │  · 记录 revision 历史、决定回滚      │
        │  · 管 minReadySeconds / progressDeadline │
        └───────────────┬──────────────────────┘
                        │ 1 : N（按 pod-template-hash 分裂）
        ┌───────────────┴──────────────────────┐
        │  ReplicaSet   「版本层 / 副本层」      │
        │  · 每个 RS 固定一个模板哈希          │
        │  · 只保证"我这一版的 Pod 保持 N 个" │
        │  · 不认识"版本先后"，不知道谁该先删  │
        └───────────────┬──────────────────────┘
                        │ 1 : N
        ┌───────────────┴──────────────────────┐
        │  Pod          「实例层」               │
        │  · 真正跑容器的最小单位              │
        │  · IP 会变，名字会变，生命周期受 RS 管 │
        └──────────────────────────────────────┘
```

**如果只有 Deployment → Pod（压成两层），会缺什么？**

缺的是**"版本身份"**。设想一个只有两层的系统，你把镜像从 v1 改成 v2：

1. 新 Pod 起好了，怎么知道哪些 Pod 是 v2、哪些是 v1？
2. 怎么实现"先起 3 个新的、确认好了再删 3 个旧的"？
3. 怎么实现"回滚到 v1"？

答案是：**给每个 Pod 打一个"来自哪个模板版本"的标签**。这个标签就是 `pod-template-hash`。有了它，Pod 就可以按版本分组，分组后自然需要一个"组"的对象来管 —— 那就是 ReplicaSet。

**所以三层不是设计者画出来的漂亮结构，而是"版本身份"这个需求逼出来的：**

```
Deployment 持有"唯一的当前模板"
        │  模板哈希一变
        ▼
ReplicaSet 是"模板某个哈希版本"的执行体（旧的留着做回滚，新的负责替换）
        │  每个 RS 按自己的副本数
        ▼
Pod 是 RS 名 + 随机后缀的具体实例
```

**一句话记住**：**Deployment 管"要不要换版本、怎么换"；ReplicaSet 管"这一版要保持几个"；Pod 是"这一个实例"。**

**这三层还有一个好处：职责可独立演进。** ReplicaSet 的语义非常稳定（"保持 N 个副本"），多年来语义稳定；Deployment 承载了所有发布相关的复杂度（策略、历史、回滚）。**新需求都加在 Deployment 层，不会动到底层。**

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Deployment | 部署 | 管理一组**无状态** Pod 副本的控制器，负责滚动更新与回滚 |
| ReplicaSet | 副本集 | 只管"保持 N 个副本"的控制器，Deployment 的下层 |
| Replica | 副本 | 由同一模板起出的一个 Pod |
| Pod Template | Pod 模板 | `spec.template`，**Deployment 里唯一决定"版本"的东西** |
| `pod-template-hash` | 模板哈希标签 | Deployment 控制器自动加的标签，区分版本 |
| Revision | 修订版本号 | 每次模板变更产生一个新的 revision（`deployment.kubernetes.io/revision` 注解） |
| RollingUpdate | 滚动更新 | 默认策略，新旧交替替换 |
| Recreate | 重建 | 先全删再全建，**中间有空窗** |
| `maxSurge` | 最大峰值 | 更新时最多能多出几个 Pod |
| `maxUnavailable` | 最大不可用 | 更新时最多能有几个 Pod 不可用 |
| `minReadySeconds` | 最短就绪秒数 | 新 Pod 连续 Ready 多久才算"可用" |
| `progressDeadlineSeconds` | 进度期限 | 多久没进展就算"这次发布失败"（默认 600） |
| `revisionHistoryLimit` | 修订历史上限 | 保留多少个旧 ReplicaSet 供回滚（默认 10） |
| `paused` | 暂停 | 模板改了也不触发新上线 |
| available | 可用 | Pod Ready 且持续 Ready 满 `minReadySeconds` |
| readinessProbe | 就绪探针 | 决定"能不能进流量"，失败**不重启** |
| livenessProbe | 存活探针 | 决定"要不要重启"，失败**重启容器** |

---

## 2. 机制

### 2.1 三层结构的职责切分（谁管什么）

| 层 | 拥有的字段 | 不管的事 | 挂了会怎样 |
|---|---|---|---|
| **Deployment** | `replicas`、`strategy`、`minReadySeconds`、`progressDeadlineSeconds`、`revisionHistoryLimit`、`paused`、`template` | 不直接创建 Pod | 发布不推进，但老 Pod 继续跑 |
| **ReplicaSet** | `replicas`、`selector`（含 hash）、`template`（冻结的那一份） | 不管新旧版本先后顺序 | 该版本副本数对不上，被 Deployment 重新拉起 |
| **Pod** | `spec.containers`、探针、资源、卷 | 不管自己属于哪个版本 | kubelet 按探针重启或重建 |

**关键点：Pod 自己不知道自己属于哪个版本。** 它只是带着 `pod-template-hash` 标签的一个实例。是 ReplicaSet 通过 selector 认领它，是 Deployment 通过操作不同的 ReplicaSet 来完成版本切换。

### 2.2 pod-template-hash：版本隔离的关键标签

Deployment 控制器会自动给每个 ReplicaSet（和它创建的 Pod）打上一个标签：

```
pod-template-hash: 75675f5897
```

官方原文：

> 该标签可确保 Deployment 的子 ReplicaSet 不重叠。标签是通过对 ReplicaSet 的 `PodTemplate` 进行哈希处理。

**这个标签解决的是"两个控制器抢同一批 Pod"的问题。** 机制是这样的：

```
Deployment.spec.selector = { app: nginx }              ← 用户的 selector（不变）

旧 RS 的 selector = { app: nginx, pod-template-hash: 2035384211 }
新 RS 的 selector = { app: nginx, pod-template-hash: 1564180365 }
```

- 旧 RS 只认带 `hash=2035384211` 的 Pod
- 新 RS 只认带 `hash=1564180365` 的 Pod
- **两个 RS 的 selector 不重叠 → 不会互相删对方的 Pod**

> **面试这么答**："Deployment 下面挂 ReplicaSet 而不是直接挂 Pod，是因为要区分版本。`pod-template-hash` 是版本身份证，它让新旧两个 ReplicaSet 的 selector 不重叠，所以滚动更新时两个控制器不会抢同一批 Pod。"

**怎么看到它**：

```bash
kubectl get rs
# NAME                   DESIRED   CURRENT   READY   AGE
# nginx-deploy-1564180365   3         3         3     5m
# nginx-deploy-2035384211   0         0         0     12m

kubectl get pods --show-labels
# nginx-deploy-75675f5897-7ci7o   1/1  Running  0  18s  app=nginx,pod-template-hash=75675f5897
```

> **注意 `kubectl get rs` 的两行 NAME 前缀一样、后缀不同** —— 后缀就是模板哈希。**看 RS 名字就知道有几个版本在跑。**

### 2.3 滚动更新的两条策略

**`spec.strategy.type` 只有两个值：`RollingUpdate`（默认）和 `Recreate`。**

| 策略 | 行为 | 可用性 | 适合 |
|---|---|---|---|
| **RollingUpdate** | 逐步缩减旧 RS、扩容新 RS | 基本不中断（受 `maxUnavailable` 约束） | 无状态服务，**默认选它** |
| **Recreate** | **在创建新 Pod 之前，所有现有 Pod 会被杀死** | **必然有空窗** | 有状态、有文件锁依赖、无法并行运行两个版本的服务 |

官方原文（Recreate）：

> 如果 `.spec.strategy.type==Recreate`，在创建新 Pod 之前，所有现有的 Pod 会被杀死。

> **面试怎么答**："`Recreate` 不是'更安全的滚动更新'，它是'先全拆再全装'。所有副本同时消失，如果你的服务有 3 个副本，Recreate 期间可用容量是 **0**。它存在的唯一理由是：新旧版本**不能同时运行**（比如独占资源、文件锁、同一端口的独占绑定）。**无状态服务用 Recreate 就是自己制造故障。**"

### 2.4 maxSurge / maxUnavailable 的取整规则（重点）

这是本篇最容易算错、面试最常被追问的地方。**两个字段的取整方向是相反的。**

| 字段 | 含义 | 百分比取整方向 | 默认值 |
|---|---|---|---|
| `maxUnavailable` | 更新过程中**不可用** Pod 数的上限 | **去除小数部分（向下取整）** | 25% |
| `maxSurge` | 更新过程中**超出期望副本数**的 Pod 数上限 | **向上取整** | 25% |

官方原文：

> `.spec.strategy.rollingUpdate.maxUnavailable`……该值可以是绝对数字（例如，5），也可以是所需 Pod 的百分比（例如，10%）。**百分比值会转换成绝对数并去除小数部分。**

> `.spec.strategy.rollingUpdate.maxSurge`……该值可以是绝对数（例如，5）或所需 Pod 的百分比……**百分比值会通过向上取整转换为绝对数。**

**两条互斥约束**（官方原文）：

> 如果 `.spec.strategy.rollingUpdate.maxSurge` 为 0，则此值（`maxUnavailable`）不能为 0。

> 如果 `MaxUnavailable` 为 0，则此值（`maxSurge`）不能为 0。

**换句话说：`maxSurge` 和 `maxUnavailable` 不能同时为 0。**（同时为 0 意味着发布期间既不能多也不能少 = 永远发不出去。）

### 2.5 replicas=1 时两个默认值到底算出了什么（算给你看）

这是 team-lead 特别点名要讲透的点。**直接代入算：**

**场景：`replicas: 1`（默认值 maxSurge=25%、maxUnavailable=25%）**

```
maxSurge     = ceil(1 × 25%)  = ceil(0.25) = 1   ← 向上取整
maxUnavailable = floor(1 × 25%) = floor(0.25) = 0  ← 去除小数部分
```

**结论：**

| 约束 | 算出来的值 | 意味着 |
|---|---|---|
| `maxUnavailable = 0` | **一个都不能不可用** | 新 Pod 没 ready 之前，**旧 Pod 一个都不许删** |
| `maxSurge = 1` | **最多能多出 1 个** | 可以先起 1 个新的，和旧的并存 |

**于是实际发布过程是：**

```
时刻  Pod 数  状态
 T0      1     旧版 v1 Running            ← 起点
 T1      2     v1 + v2 并存              ← 先起新的（surge=1）
 T2      2     v2 通过就绪探针           ← 新的确认可用
 T3      1     只剩 v2 Running            ← 再删旧的（unavailable=0 保证此时才删）
```

> **所以答案是：单副本发布期会短暂跑 2 个 Pod。**
> **而且顺序必然是"先起新的、再删旧的"**——因为 `maxUnavailable=0` 意味着"旧的一个都不能在新的没就绪之前动"。

**这个结论有三个直接后果**（都能在面试里当加分项）：

1. **集群必须有余量。** 单副本发布时峰值是 2 个 Pod。如果节点刚好塞满（资源配额、节点资源打满），新的 Pod 起不来 → **发布卡死**，而老版本还在跑（所以服务没断，但版本永远换不上去）。
2. **如果你的服务是"独占端口 / 独占文件 / 独占外部资源"的，两个版本不能并存**，那 `replicas=1` + `maxSurge=1` 的默认行为对你就是错的，要改 `Recreate`。
3. **发布期的资源峰值是 2 倍。** 单副本 + 512Mi 的 requests，实际发布时要 1Gi 的余量。**容量规划要按这个算。**

**再看 `replicas: 4` 的对照**（同一个默认值，不同结果）：

```
maxSurge      = ceil(4 × 25%)  = 1
maxUnavailable = floor(4 × 25%) = 1
```

官方文档对此有精确描述：

> 当 Deployment 设置为 4 个副本时，Pod 的个数会介于 3 和 5 之间。

**所以副本数不同，取整结果也不同 → 发布期的资源画像也不同。** 这就是为什么"我们服务是 3 副本"这种细节在容量规划时要算数。

**给你一张速查表**（按默认 25%/25%）：

| replicas | maxSurge（向上取整） | maxUnavailable（向下取整） | 发布期 Pod 数区间 |
|---|---|---|---|
| 1 | 1 | 0 | 1 ~ 2 |
| 2 | 1 | 0 | 1 ~ 3 |
| 3 | 1 | 0 | 2 ~ 4 |
| 4 | 1 | 1 | 3 ~ 5 |
| 5 | 2 | 1 | 3 ~ 7 |
| 8 | 2 | 2 | 6 ~ 10 |
| 10 | 3 | 2 | 7 ~ 13 |

> **注意 2 副本那一行**：`maxUnavailable = floor(0.5) = 0`，所以**2 副本也是"零中断"发布**。而 3 副本 `floor(0.75) = 0` 也是。**只有到 4 副本才开始出现"可以先停 1 个"**。这就是"为什么生产建议至少 3 副本"的一个技术理由。

### 2.6 就绪探针如何决定"新副本可用"

滚动更新的推进**不是靠时间，而是靠状态**：

```
新 RS 扩到 1
    │
    ▼
新 Pod 被创建 → 调度 → 拉镜像 → 容器启动
    │
    ▼
readinessProbe 通过？  ──否──► 一直是 0/1，不算 available，**发布卡在这**
    │ 是
    ▼
持续 Ready 满 minReadySeconds？ ──否──► 还不算 available
    │ 是
    ▼
计入 availableReplicas → Deployment 允许把旧 RS 缩容
```

**Deployment 的 `available` 定义（官方）**：

> `.spec.minReadySeconds` 是一个可选字段，用于指定新创建的 Pod 在没有任意容器崩溃情况下的最小就绪时间，**只有超出这个时间 Pod 才被视为可用**。默认值为 0。

**所以"新 Pod Running 了" ≠ "新 Pod 可用了"。** 两者之间隔着就绪探针。

**这一节要背下来的关键区分**（和 L2 的探针口径完全一致）：

| 探针 | 失败后果 | 在滚动更新里的角色 |
|---|---|---|
| **readinessProbe** | **摘出 Service 端点，不重启** | **决定新 Pod 什么时候算 available**，进而决定旧 Pod 什么时候能被删 |
| **livenessProbe** | **重启容器** | 与发布推进无关；探针太严会在发布中途把新 Pod 反复重启，直接把发布卡死 |
| **startupProbe** | 启动成功前屏蔽其他探针 | 慢启动应用用它；没有它，liveness 会在应用还没起来时就误杀 |

> **最经典的发布卡死**：新版本的 liveness 探针间隔太短、初始延迟不够 → 新 Pod 起来 → 还没初始化完 → liveness 失败 → 被重启 → 永远起不来 → `newReplicaSetAvailable` 永远为 false → **发布一直卡着，而老版本还在正常服务**。**这就是第 6 节"发布卡在 newReplicaReady"那条的根因。**

### 2.7 回滚的真实机制：改 Pod 模板

**很多人以为回滚是"把旧 Pod 变回来"。不是。**

**真实机制（三步）：**

```
① 找到目标 revision 的 ReplicaSet
        │  （它的 Pod 模板里存着旧版本的完整 spec）
        ▼
② 把那个模板复制回 Deployment 的 spec.template
        │
        ▼
③ 模板变了 → 哈希变了 → Deployment 控制器创建"第三个" ReplicaSet
        │
        ▼
   滚动更新走一遍：起新的 → 等就绪 → 删当前的
```

官方原文（回滚章节）：

> 如果 Deployment 的当前状态不稳定，回滚到较早的 Deployment 版本。**每次回滚都会更新 Deployment 的修订版本。**

> **"每次回滚都会更新修订版本"这句话是本篇的题眼。** 它说明：**回滚不是"时间倒流"，而是"再发布一次旧版本"。** 回滚之后，revision 号会**继续往上涨**（而不是退回去），并且会多出一个新的 ReplicaSet。

**所以你会看到这样的现象**（很多人在面试里被问到过）：

```bash
kubectl rollout history deployment/nginx
# REVISION  CHANGE-CAUSE
# 3         <none>
# 2         <none>
# 1         <none>

kubectl rollout undo deployment/nginx
# deployment.apps/nginx rolled back

kubectl rollout history deployment/nginx
# REVISION  CHANGE-CAUSE
# 4         <none>     ← 新增了一条！不是回到 2
# 3         <none>
# 2         <none>
# 1         <none>
```

**为什么这么设计？** 因为回滚本身也是一次**有风险的操作**（旧版本同样存在 bug、旧版本与新数据格式不兼容）。**把它当成一次正常的发布来对待，才能享受同样的就绪检查、进度监控和"回滚失败再回滚"的能力。**

> **面试这么答**："回滚不是恢复旧 Pod，是**把旧 ReplicaSet 的 Pod 模板写回 Deployment**，然后走一遍新的滚动更新。所以回滚会产生新的 revision、新的 ReplicaSet。这样做的好处是回滚过程同样受就绪探针和进度期限保护——回滚失败你能立刻发现，而不是留下一堆坏 Pod。"

### 2.8 为什么 kubectl edit 会被下次 apply 覆盖

这是本篇第二个高频面试题，**它把 K8s 和 GitOps 串起来了。**

**先看机制。** `kubectl apply` 靠三份数据算差异：

| 数据 | 存放位置 | 说明 |
|---|---|---|
| 配置文件内容 | 你的本地 yaml / Git 仓库 | **期望态的源头** |
| 现时配置 | etcd 里的对象 | 集群当前实际长什么样 |
| `last-applied-configuration` | 对象上的**注解** | 上一次 apply 时的配置文件内容 |

官方原文（声明式管理）：

> `kubectl apply` 命令会使用当前的配置文件、现时配置以及现时配置中保存的 `last-applied-configuration` 注解内容来计算补丁更新内容。

官方对合并补丁的计算步骤：

> 1. 计算要删除的字段，即在 `last-applied-configuration` 中存在但在配置文件中不再存在的字段。
> 2. 计算要添加或设置的字段，即在配置文件中存在但其取值与现时配置不同的字段。
> 3. 设置 `last-applied-configuration` 注解的内容，使之与配置文件匹配。

**于是 `kubectl edit` 的下场就清楚了：**

```
① 你 kubectl edit deployment/x，把 image 改成 v2
   → 直接改"现时配置"（etcd）
   → 但 "配置文件内容"（yaml / Git）里还是 v1
   → last-applied-configuration 注解里也还是 v1

② 任何人（或 CI）执行一次 kubectl apply -f x.yaml
   → 第 2 步：配置文件里 image=v1，现时配置里 image=v2
   → 两者取值不同 → **把 image 设置回 v1**
   → 同时第 3 步把 last-applied-configuration 也刷新成 v1

结果：你 edit 的修改被无声地抹掉了。
```

**官方文档明确警告过这种混用不受支持：**

> 将 `kubectl apply` 与指令式对象配置命令 `kubectl create` 或 `kubectl replace` 混合使用是不受支持的。

**但更值得讲的是"哪些字段会被覆盖、哪些不会"** —— 官方文档给了精确示例：

| 字段 | `kubectl edit` 改了会被 apply 覆盖吗 | 为什么 |
|---|---|---|
| `image`（配置文件里有） | **会** | 第 2 步："配置文件中存在但其取值与现时配置不同" → 设回文件里的值 |
| `minReadySeconds`（配置文件里删掉了） | **会** | 第 1 步：last-applied 里有、配置文件里没了 → **从现时配置中删除该字段** |
| `replicas`（配置文件里没写） | **不会** | 配置文件里没有这个字段 → 不在第 1、2 步的管辖范围 |

> **这就是那个反直觉现象的解释**：官方文档里 `kubectl scale` 改的 `replicas: 2` 在后续 apply 后**依然保留**，而你 `edit` 改的 `image` 被冲掉。**区别在于"这个字段在不在你的配置文件里"。**

**这引出 GitOps。** GitOps 的核心命题就是：

> **集群的实际状态应该由 Git 仓库里的声明唯一决定，任何"现场修改"都是漂移（drift），都应被自动纠正。**

ArgoCD 就是这个思想的落地：**Git 是唯一真相源，集群偏离 Git 就自动同步回去。** 如果你的流程里允许 `kubectl edit`，那 ArgoCD 会把它当成漂移**改回去**——而这恰恰是最让人困惑的现象：**"我明明手动改了，为什么又被改回去了？"**

> **面试这么答（这条很能体现工程思维）**："`kubectl edit` 改的是现时配置，不是期望态。`kubectl apply` 会用配置文件 + `last-applied-configuration` 算三路 diff，把你在配置文件里声明过的字段一律设回文件里的值。所以现场手改会被下次 apply 静默覆盖——**这不是 bug，是声明式系统的正确行为**。生产上的正解是把变更写进 Git 走 CI/CD，或者上 ArgoCD 让漂移自动收敛。**手工改集群这个动作本身，就是把系统的期望态搞成分裂状态。**"

### 2.9 关键参数与默认值表

| 参数 | 默认 | 含义 | 实务要点 |
|---|---|---|---|
| `spec.replicas` | **1** | 期望副本数 | **HPA 管理时不要写死**，否则 apply 会覆盖 HPA 的决策 |
| `spec.strategy.type` | `RollingUpdate` | 更新策略 | 有状态/独占资源才用 `Recreate` |
| `spec.strategy.rollingUpdate.maxSurge` | **25%**（向上取整） | 最多多出几个 Pod | **算资源余量要按它算** |
| `spec.strategy.rollingUpdate.maxUnavailable` | **25%**（去除小数部分） | 最多几个不可用 | `replicas=1` 时 = 0 → 零中断发布 |
| `spec.minReadySeconds` | **0** | 新 Pod 连续 Ready 多久算可用 | 设 5~10 能过滤"刚起就崩"的抖动 |
| `spec.progressDeadlineSeconds` | **600** | 多久没进展算发布失败 | `rollout status` 等的就是它，**超时本身零信息量** |
| `spec.revisionHistoryLimit` | **10** | 保留多少个旧 RS | 设 0 → **无法回滚** |
| `spec.paused` | `false` | 暂停上线 | 模板改了也不触发新版本 |
| `spec.selector` | 必填 | 选哪些 Pod | **创建后不可改** |
| `spec.template` | 必填 | Pod 模板 | **改这里 = 改版本 = 触发发布** |

**`replicas` 与 HPA 的冲突（官方原文）：**

> 如果一个 HorizontalPodAutoscaler（或者其他执行水平扩缩操作的类似 API）在管理 Deployment 的扩缩，则**不要设置 `.spec.replicas`**。

> **实际影响**：如果你在 yaml 里写了 `replicas: 3`，HPA 扩到 8，之后任何人执行 `kubectl apply -f` 都会**把它打回 3**——HPA 下一次同步再拉回 8。**"副本数反复横跳"就是这么来的。** 详见 L2 的 HPA 口径。

---

## 3. 动手

> 本节所有命令都在 kind 集群（`day2`）上跑。**本文的意图是让你亲手观察，不是让你改集群。**

### 3.1 一个"能发布"的 Deployment（含就绪探针）

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
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  minReadySeconds: 5
  progressDeadlineSeconds: 600
  revisionHistoryLimit: 5
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
        - containerPort: 8080
          name: http
        readinessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 3
          periodSeconds: 5
          failureThreshold: 3
        livenessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 15
          periodSeconds: 10
          failureThreshold: 3
        resources:
          requests:
            cpu: "100m"
            memory: "128Mi"
          limits:
            cpu: "500m"
            memory: "256Mi"
```

**逐条说明为什么这么写**（这些不是模板，是判断依据）：

| 配置 | 理由 |
|---|---|
| `maxUnavailable: 0` | 显式声明零中断。**注意：replicas=2 时默认值算出来本来就是 0，写出来是为了让读清单的人一眼看懂意图**，也为了防止以后副本数变了语义跟着变 |
| `maxSurge: 1` | 显式。replicas=2 时默认也是 1，同理 |
| `minReadySeconds: 5` | 新 Pod 起来后要稳定 5 秒才算可用，**过滤掉"起来就崩"的抖动** |
| `progressDeadlineSeconds: 600` | 显式写出来 = 知道发布超时会等 10 分钟 |
| `revisionHistoryLimit: 5` | 默认 10 也行；写小一点说明**只关心最近几个版本** |
| `readinessProbe.initialDelaySeconds: 3` | 应用启动要时间，**这个值必须大于应用真实启动耗时** |
| `livenessProbe.initialDelaySeconds: 15` | **比 readiness 大**，保证启动期不会被 liveness 误杀 |
| 显式 `resources.requests` | 调度依据 + HPA 分母（L2 口径） |

> **探针路径 `/healthz` 是示例**。你的应用没有这个路径就用能反映"真的能服务"的路径 —— **readiness 探针必须真的代表"能接流量"**，返回 200 就放行、返回非 200 就摘端点。**写一个永远返回 200 的探针等于没写。**

```bash
# ① 语法自检（不碰集群）
kubectl apply -f day2-web.yaml --dry-run=client

# ② 创建
kubectl apply -f day2-web.yaml

# ③ 看两个 RS（此时只有一个）
kubectl get rs -l app=day2-web
```

### 3.2 观察一次完整的滚动更新

```bash
# ① 触发一次发布（改镜像 tag）
kubectl set image deployment/day2-web web=registry.cn-hangzhou.aliyuncs.com/linux321-devops/day1-go-demo:new

# ② 另一个终端盯着（-w 自动刷新）
kubectl get rs -l app=day2-web -w
```

**你会看到（这是本篇最重要的一次观察）：**

```
NAME                   DESIRED   CURRENT   READY   AGE
day2-web-7f9c8b6d4f      2         2         2     3m      ← 旧 RS（先存在）
day2-web-65d4f7a9c2      1         1         0     5s      ← 新 RS 刚起，READY=0
day2-web-65d4f7a9c2      2         2         1     20s     ← 新 RS 扩到 2，还差一个 ready
day2-web-7f9c8b6d4f      1         1         1     25s     ← 旧 RS 缩到 1（因为 maxUnavailable=0 先确认新的好了）
day2-web-7f9c8b6d4f      0         0         0     40s     ← 旧 RS 归零
```

> **这就是 2.5 节算出来的过程的真实观测**：新 RS 先扩（surge），等 `READY` 变成 2 之后旧 RS 才开始缩。**顺序就是 `maxUnavailable=0` 的直接体现。**

```bash
# ③ 看发布进度
kubectl rollout status deployment/day2-web
# deployment "day2-web" successfully rolled out

# ④ 看事件时间线（每一步都有记录）
kubectl describe deployment day2-web | sed -n '/Events/,$p'
```

官方文档给出的事件序列就是这个形状：

```
Normal  ScalingReplicaSet  Scaled up replica set nginx-deployment-2035384211 to 3
Normal  ScalingReplicaSet  Scaled up replica set nginx-deployment-1564180365 to 1
Normal  ScalingReplicaSet  Scaled down replica set nginx-deployment-2035384211 to 2
Normal  ScalingReplicaSet  Scaled up replica set nginx-deployment-1564180365 to 2
Normal  ScalingReplicaSet  Scaled down replica set nginx-deployment-2035384211 to 1
Normal  ScalingReplicaSet  Scaled up replica set nginx-deployment-1564180365 to 3
Normal  ScalingReplicaSet  Scaled down replica set nginx-deployment-2035384211 to 0
```

> **读法：up 和 down 交替出现，且 down 永远跟在 up 后面。** 这就是滚动更新的"节拍"。

```bash
# ⑤ 看 revision 历史
kubectl rollout history deployment/day2-web
# REVISION  CHANGE-CAUSE
# 2         <none>
# 1         <none>
```

### 3.3 演练：回滚 / 重启 / 暂停恢复

```bash
# ① 回滚到上一个版本
kubectl rollout undo deployment/day2-web

# ② 回滚到指定 revision
kubectl rollout undo deployment/day2-web --to-revision=2

# ③ 看历史（注意 revision 会继续往上加，不回退）
kubectl rollout history deployment/day2-web

# ④ 重启：不改版本，但让所有 Pod 重建（改的是 pod-template-hash）
kubectl rollout restart deployment/day2-web

# ⑤ 暂停：改模板但不触发上线
kubectl rollout pause deployment/day2-web
# 此时改 Deployment 的 template → 不会触发新版本
kubectl rollout resume deployment/day2-web   # 恢复后才会推进
```

**六个 rollout 子命令的语义对照表**：

| 命令 | 语义 | 改了什么 | 会不会触发发布 |
|---|---|---|---|
| `rollout status` | **看**发布进度，等它完成 | 不改任何东西 | 否 |
| `rollout history` | **看**revision 列表 | 不改任何东西 | 否 |
| `rollout undo` | **回滚**到上一个 / 指定 revision | 把旧模板写回 `spec.template` | **会**（走一次新发布） |
| `rollout restart` | **重建 Pod**，版本不变 | 改 Pod 模板里的一个注解字段 → hash 变 | **会**（但版本没变） |
| `rollout pause` | **暂停**上线推进 | 置 `spec.paused=true` | 之后改模板**不**触发 |
| `rollout resume` | **恢复**上线推进 | 置 `spec.paused=false` | 恢复后才推进 |

> **`rollout restart` 的原理值得单独说**：它不改镜像，只在 Pod 模板里加一个"重启时间戳"注解 → 模板哈希变了 → 控制器以为是新版本 → 建新 RS、滚动替换。**效果是"所有 Pod 换一批新的，但版本一样"。** 典型用途：改了 JVM 参数需要重启才生效、节点内核升级后要重建 Pod、怀疑有内存泄漏想重来一遍。

> **`pause` 的官方定义**："暂停的 Deployment 和未暂停的 Deployment 的唯一区别是，Deployment 处于暂停状态时，**PodTemplateSpec 的任何修改都不会触发新的上线**。" 典型用法：**一次改多个字段，改完再 resume 一次性发布**，而不是每改一个字段就发一次。

### 3.4 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| 两个版本能共存 | `kubectl get rs -l app=day2-web` | 发布中会看到两个 RS |
| 新 Pod 会 ready | `kubectl get pods -l app=day2-web` | `2/2 Running` |
| 发布能完成 | `kubectl rollout status deployment/day2-web` | `successfully rolled out` |
| 有版本记录 | `kubectl rollout history deployment/day2-web` | 至少 2 条 REVISION |
| 旧的 RS 被回收 | `kubectl get rs -l app=day2-web` | 旧 RS 的 DESIRED=0 |
| 回收策略生效 | `kubectl get rs -l app=day2-web` | 超过 `revisionHistoryLimit` 的旧 RS **消失** |

> **最后一条是 `revisionHistoryLimit` 的实际作用**：不是"留着不删"，而是"**超出上限的会被垃圾回收**"。官方原文："其余的 ReplicaSet 将在后台被垃圾回收。默认情况下，此值为 10。" **所以设成 0 意味着旧 RS 立刻被回收 → 无法回滚。**

---

## 4. 资源清单逐字段

### 4.1 Deployment

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: apps/v1` | string | ✅ | API 组与版本 | 写 `extensions/v1beta1` → **已废弃，apply 报错** |
| `kind: Deployment` | string | ✅ | 对象类型 | 拼错 → `no matches for kind` |
| `metadata.name` | string | ✅ | 对象名 | 重复 → `AlreadyExists` |
| `metadata.labels` | map | | 自己的标签 | — |
| `metadata.annotations` | map | | 控制器专用注解 | 键写错 → **不报错，注解不生效** |
| `spec.replicas` | int | | 期望副本数，**默认 1** | 写了固定值 → **HPA 扩缩被 apply 覆盖** |
| `spec.selector.matchLabels` | map | ✅ | 选哪些 Pod 归我管 | 与 template 标签不一致 → **apply 直接报错**；**创建后不可改** |
| `spec.template` | PodTemplateSpec | ✅ | **Pod 模板 = 版本本体** | **改这里 = 触发发布**；写错 → Pod 起不来 |
| `spec.template.metadata.labels` | map | ✅ | 打给 Pod 的标签 | 必须与 selector **完全一致**（selector 是等值匹配） |
| `spec.template.spec.containers[]` | list | ✅ | 容器定义 | — |
| `.name` | string | ✅ | 容器名 | 同一 Pod 内不可重名；`rollout set image` 靠它定位 |
| `.image` | string | ✅ | 镜像地址 | 私有仓库无凭据 → `ImagePullBackOff` |
| `.imagePullPolicy` | enum | | `IfNotPresent`（非 latest 默认）/ `Always` / `Never` | `Always` + 私有仓库 → 每次都 401 |
| `.ports[].containerPort` | int | | **声明**容器监听端口（不限制） | 与 Service 的 targetPort 对不上 → 访问不通 |
| `.ports[].name` | string | | 端口名 | 多端口时**必填** |
| `.readinessProbe` | Probe | | 失败 → **摘端点，不重启** | 太严 → 一直 0/1 → **发布卡在 newReplicaReady** |
| `.livenessProbe` | Probe | | 失败 → **重启容器** | 太严 → 新 Pod 反复重启 → **发布卡死** |
| `.startupProbe` | Probe | | 启动成功前屏蔽其他探针 | 不用 → 慢启动应用被 liveness 误杀 |
| `.resources.requests.cpu` | Quantity | | **调度依据 + HPA 分母** | 漏写 → HPA `<unknown>/50%` |
| `.resources.limits.cpu` | Quantity | | 上限，超了被节流（**不杀容器**） | 太小 → 采集值顶到 limit，**HPA 会扩但每个新 Pod 同样被限死**，扩了没用 |
| `.terminationGracePeriodSeconds` | int | | **默认 30** | 不配 `preStop` → 长连接被硬切 → 502 |
| `.strategy.type` | enum | | `RollingUpdate`(默认) / `Recreate` | `Recreate` → **发布期可用副本 0** |
| `.strategy.rollingUpdate.maxSurge` | int/str | | **默认 25%**（向上取整） | 设 0 而 maxUnavailable 也 0 → **apply 报错** |
| `.strategy.rollingUpdate.maxUnavailable` | int/str | | **默认 25%**（去除小数） | 设 0 而 maxSurge 也 0 → **apply 报错** |
| `.minReadySeconds` | int | | **默认 0**，新 Pod 连续 Ready 多久算可用 | 设得比应用启动时间长 → 发布极慢 |
| `.progressDeadlineSeconds` | int | | **默认 600** | 设得比正常发布时长短 → **误判发布失败** |
| `.revisionHistoryLimit` | int | | **默认 10**，保留多少旧 RS | 设 0 → **无法回滚** |
| `.paused` | bool | | **默认 false** | 忘了 resume → 改了模板不生效 |

**互斥与约束（apply 会直接拒绝的情况）**：

| 约束 | 违反时的报错 |
|---|---|
| `maxSurge` 与 `maxUnavailable` 不能同时为 0 | `Invalid value: 0: may not be 0 when maxSurge is 0`（官方 API 校验报错原文） |
| `progressDeadlineSeconds` 必须 > `minReadySeconds` | `Invalid value: ...: must be greater than minReadySeconds`（官方 API 校验报错原文） |
| `selector` 与 `template.metadata.labels` 必须匹配 | `selector does not match template labels` |
| `requests` 不能大于 `limits` | `Invalid value: ...: must be less than or equal to cpu limit` |

> **这四条是"apply 阶段就被挡住"的错误** —— 属于最好的错误，因为集群根本没被改动。**写完 yaml 先 `--dry-run=client` 跑一遍。**

---

## 5. 横向对比

### 5.1 Deployment / StatefulSet / DaemonSet / Job 的更新语义差别

这四种工作负载**最本质的区别不是"跑几个"，而是"换版本时怎么换"**：

| 维度 | **Deployment** | **StatefulSet** | **DaemonSet** | **Job** |
|---|---|---|---|---|
| 提供什么 | N 个**等价**副本 | N 个**有身份**的副本 | **每个节点**一个 | 跑到完成就结束 |
| Pod 名字 | 随机（`web-7f9c8b6d4f-abcde`） | **有序**（`mysql-0`、`mysql-1`） | `<ds>-<rand>` | `<job>-<rand>` |
| 更新触发 | 改 Pod 模板 | 改 Pod 模板 | 改 Pod 模板 | **改模板要删了重建** |
| 更新顺序 | **随机/并行** | **逆序（从大到小）**，前一个 Ready 才动下一个 | **按节点逐个**（默认 `maxUnavailable=1`） | 不适用 |
| 更新期间新旧并存 | ✅（默认） | ✅ | ✅ | — |
| 存储 | 共享 PVC 或无 | **每 Pod 一块独立 PVC**（`volumeClaimTemplates`） | 通常用 hostPath 或 DaemonSet 自己的卷 | 临时卷 |
| 回滚 | `rollout undo`（有 revision 历史） | 改模板回退（**且需处理数据兼容性**） | 改模板回退 | **只能重跑** |
| 名字与身份 | 随便换 | **必须稳定**（下游按名字寻址） | 随节点 | 无所谓 |
| 典型场景 | Web、API | MySQL、Kafka、ZooKeeper | 日志采集、监控 agent、CSI 驱动 | 数据迁移、备份、一次性任务 |

**"更新语义"的差异，一句话各自总结**：

- **Deployment**：**并行替换，顺序无所谓**，因为副本之间是等价的。
- **StatefulSet**：**串行替换，顺序有意义**（`mysql-2` → `mysql-1` → `mysql-0`，因为主从角色跟序号绑定，**必须从非主库开始换**）。官方语义：StatefulSet 按**逆序**更新，即序号从大到小依次删除并重建，且**等待前一个 Pod 就绪后再继续**。
- **DaemonSet**：**逐节点替换**，因为每个节点一个，节点是"位置"不是"副本"，**换的顺序由节点顺序决定，且不能同时换太多**。
- **Job**：**没有"更新"这个概念**。Job 的 Pod 模板**创建后不可改**——想用新配置跑，得**新建一个 Job**。这是很多人踩的坑（改 Job 的 image 报 `spec.template: Forbidden: job updates may not change fields other than ...`）。

> **这张表的用法**：面试被问"Deployment 和 StatefulSet 有什么区别"，不要只答"一个有状态一个无状态"（太浅）。**答"更新顺序 + 身份稳定性 + 存储独占"三条**，并补一句"Job 模板不可改，要改就新建 Job"，立刻拉开差距。

### 5.2 滚动更新 vs 蓝绿 vs 金丝雀：什么时候换工具

| 方案 | 机制 | 发布期额外资源 | 中断 | 能否快速回滚 | 适用 |
|---|---|---|---|---|---|
| **RollingUpdate** | 新旧交替替换 | 约 `maxSurge` 个 | 无 | 可以（但要等一轮） | **绝大多数无状态服务** |
| **Recreate** | 全删再全建 | 0 | **有** | 慢 | 独占资源 |
| **蓝绿（Blue-Green）** | 备一套全量的，绿的好了切流量 | **100%**（要养两套） | 切流瞬间有 | **极快（切回去即可）** | 大版本、数据库迁移类 |
| **金丝雀（Canary）** | 先给 1% / 5% 流量，观察后逐步放大 | 少量 | 无 | 快 | **风险高、要灰度** |

**选型依据（这是面试真正想听的）**：

| 场景 | 选谁 | 为什么 |
|---|---|---|
| 普通业务迭代（每周发版） | **RollingUpdate** | 资源开销小，够用 |
| 数据库结构变更 / 大版本不兼容 | **蓝绿** | 需要"随时切回"，且新旧能并存 |
| 高风险改动 / 首次上线的核心链路 | **金丝雀** | 需要"小流量验证" |
| 超过 K8s 原生能力（按权重、按 header 灰度） | **Argo Rollouts** | 官方 Deployment 不支持按流量比例灰度 |

> **K8s 原生 Deployment 的局限要说清**：它**只能按"副本数"灰度，不能按"流量比例"灰度**。10 副本时 `maxSurge=1` 大约等于 10% 流量，但这只是近似，**不精确**。要精确按流量灰度（1% → 5% → 20% → 100%），需要 Argo Rollouts 或服务网格。**这是 K8s 原生能力和开源增强方案的边界，面试能讲清这一点说明你真用过。**

---

## 6. 踩坑与排障（症状 → 状态 → 原因）

**排障铁律：`rollout status` 的超时是**第一层**现象，**没有诊断价值**。真正的信息在 `describe` 的 Events 和 Pod 的状态里。**

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| 发布时 Pod 数短暂变成 2 | 都 `Running` | **`replicas=1` + 默认 `maxSurge=25%` → 向上取整为 1**，必须先起新的再删旧的 | **不是故障**，是设计行为；若不想并存，改 `strategy: Recreate` 或显式 `maxSurge: 0` | 容量规划按"发布期峰值 = replicas + maxSurge"算余量 |
| `rollout status` 超时 | — | **"等够了没等到"，零信息量** | 别在这儿耗时间，直接 `kubectl describe deploy` 看 Events | 记住这条报错没有诊断价值 |
| `0 of 1 updated replicas are available` | `Pending` | 新 Pod **调度不上**：节点资源不足 / 亲和性不匹配 / 有污点无容忍 | `describe pod` 看 `FailedScheduling` 原文；`kubectl describe node` 看资源占用 | 发布前检查节点余量；给 Deployment 配 `PodDisruptionBudget` |
| 同上 | `ImagePullBackOff` | 拉不到镜像：401 无凭据 / 404 名字或 tag 错 | `docker pull` 先验证；kind 用 `kind load docker-image` | CI 里加"镜像可拉通"的前置检查 |
| 同上 | `CrashLoopBackOff` | 新版本起不来（旧版本正常 → **问题在新版本**） | `kubectl logs <newpod> --previous` 看上一次崩的日志 | 发版前跑一次新版本镜像的冒烟测试 |
| 同上 | `Running 0/1` | **就绪探针没过**：路径错 / 端口错 / 应用慢 / 探针太严 | `describe pod` 看 Probe 那一行；`logs` 看应用是否监听 | 探针路径要真的反映"能服务"；用 `startupProbe` 兜住慢启动 |
| 同上 | `Running 1/1` 但 `newReplicaSetAvailable` 一直 false | **`minReadySeconds` 设得比应用稳定所需时间长** | 调小 `minReadySeconds` | 探针 + `minReadySeconds` 一起设计 |
| 同上 | 新 Pod `RESTARTS` 持续增加 | **liveness 探针太严**，在应用初始化完成前就误杀 | 调大 `livenessProbe.initialDelaySeconds`，或加 `startupProbe` | 慢启动应用一律加 `startupProbe` |
| `kubectl edit` 改的东西又变回去了 | — | **apply 的三路 diff 把它设回了配置文件里的值** | 改 Git 里的 yaml → 走 CI/CD；或用 ArgoCD | **禁止线上 `kubectl edit`**，用 `kubectl patch` 前先想清楚属主 |
| `kubectl scale` 改的副本数 apply 后还在 | — | 配置文件里**没写** `replicas` → 不在 apply 的管辖范围 | — | 这不是 bug，是 apply 的正确行为；**但也因此 replicas 属于"隐式声明"，容易被忽略** |
| 副本数反复横跳（HPA 场景） | — | yaml 里写了 `replicas`，apply 覆盖 HPA 的决策 | **yaml 里删掉 `replicas`** | HPA 管理的 Deployment 不设 `replicas` |
| `rollout undo` 后 revision 号反而涨了 | — | **回滚是"改模板触发新发布"**，不是时间倒流 | 正常现象 | 知道这个机制才不会以为回滚坏了 |
| 旧 ReplicaSet 莫名消失 | — | 超过 `revisionHistoryLimit` 的旧 RS **被垃圾回收**了 | 调大 `revisionHistoryLimit` | 需要长期保留回滚点就设大一些（**设 0 = 无法回滚**） |
| `apply` 报 `may not be 0 when maxSurge is 0` | — | `maxSurge` 与 `maxUnavailable` 同时为 0 | 至少给一个非 0 | 记住这两个字段的互斥关系 |
| `apply` 报 `must be greater than minReadySeconds` | — | `progressDeadlineSeconds` ≤ `minReadySeconds` | 调大 `progressDeadlineSeconds` | 两者一起设计 |
| `apply` 报 `selector does not match template labels` | — | 两处标签不一致 | 两处改成一致 | 模板复用，别手抄 |
| `Recreate` 发布期间服务 502 | — | **Recreate 期间可用副本为 0**，这是它的定义 | 改 `RollingUpdate`；或加 `preStop` 缩短空窗 | 无状态服务**永远不要用 Recreate** |
| 发布时 502 / 连接被拒 | `Terminating` | **没有 `preStop` + 优雅停机**：SIGTERM 发出后应用还在处理请求，但 kube-proxy 已把流量摘走 → 存量请求被硬切 | 加 `preStop: sleep 15` + `terminationGracePeriodSeconds: 60` | 优雅停机三件套：exec 形式 ENTRYPOINT + preStop + 足够 grace period |
| 发布时新旧版本行为不一致 | — | **配置没跟着镜像走**：ConfigMap 改了但 env 方式注入 → 没生效 | 见 [L5](L5-Ingress与配置管理.md) 的"env 不热更新" | 配置更新和镜像发布要配套设计 |

**发布卡住的固定排查路径（背下来，按顺序走）**：

```
① kubectl get deploy <name>
   → 看 READY / UP-TO-DATE / AVAILABLE 三列，不一致就是卡了
        │
② kubectl describe deploy <name>
   → Events 段：看 ScalingReplicaSet 的最后一次动作
   → Conditions 段：Progressing=False + reason=ProgressDeadlineExceeded → 已超时
        │
③ kubectl get rs -l <selector> -o wide
   → 新 RS 的 DESIRED 是多少？READY 是多少？差在哪
        │
④ kubectl get pods -l <selector> --show-labels
   → 新 Pod 是什么状态（Pending / ImagePullBackOff / CrashLoop / 0-1）？
        │
⑤ kubectl describe pod <new-pod>
   → Events 最后一条：FailedScheduling / FailedMount / Unhealthy / BackOff
        │
⑥ kubectl logs <new-pod> --previous
   → 崩溃就看上一次日志
        │
⑦ 逐条对照 2.6 节的推进链路：调度 → 拉镜像 → 启动 → 就绪探针 → minReadySeconds
   → 卡在哪一环，就修那一环
```

> **最后一步是本篇的核心方法**：**发布推进是一条链，每一环有每一环的失败状态。** 定位 = 找到断点，不是猜。

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/workloads/controllers/deployment/` | 更新 Deployment、滚动更新、maxUnavailable/maxSurge 取整规则、进度期限、最短就绪时间、paused、清理策略、pod-template-hash、回滚 | ✅ 2026-10-24 |
| S2 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/workloads/` | 工作负载控制器总览（Deployment / ReplicaSet / StatefulSet / DaemonSet / Job 的定位） | ✅ 2026-10-24 |
| S3 | 官方（中文） | `kubernetes.io/zh-cn/docs/reference/kubectl/quick-reference/` | `kubectl rollout status/history/undo/restart`、`kubectl set image`、`kubectl scale`、`kubectl logs --previous` | ✅ 2026-10-24 |
| S4 | 官方（中文） | `kubernetes.io/zh-cn/docs/tasks/manage-kubernetes-objects/declarative-config/` | 声明式管理、`last-applied-configuration` 注解、apply 合并补丁计算、字段属主切换 | ✅ 2026-10-24 |
| S5 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/architecture/` | kube-controller-manager 与各控制器职责、kube-proxy 职责、集群 DNS 插件 | ✅ 2026-10-24 |
| S6 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/workloads/pods/` | Pod 生命周期与探针语义（liveness/readiness/startup 的区别） | ✅ 2026-10-24 |
| S7 | 官方 | `kubernetes.io/docs/reference/using-api/` | Deployment API 字段语义交叉验证 | ✅ 2026-10-24 |
| S8 | 官方（中文） | `kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/` | HPA 与 `spec.replicas` 的关系（跨篇口径一致） | ✅ 2026-10-24 |
| S9 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 托管集群发布实践差异 | ✅ 2026-10-24 |
| S10 | 社区/工具 | `argo-cd.readthedocs.io/en/stable/user-guide/sync-options/` | SelfHeal 与"现场修改被回滚"的 GitOps 解释 | ✅ 2026-10-24 |

> **S9 / S10 的具体小节名以页面内导航为准**（索引中这两条标 ✅ 的是站点入口级 URL）。
> **本篇的字段默认值以官方中文文档 v1.37 版本页 + `kubectl explain` 交叉验证为准**（本地 v1.37.1）。
> **⚠️ 单源待补**：`progressDeadlineSeconds` 的**中文字幕**有一处笔误（中文页写作"默认 600 毫秒内持续重试"，英文原意是 **600 秒**），本篇以 `600 秒`（与 L2 口径一致）；`kubectl rollout pause/resume` 在 S3 页面未见独立条目，本篇按 kubectl CLI 实际子命令集描述，**建议本地 `kubectl rollout --help` 复核**。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入：`spec.template`（版本本体）+ `spec.replicas`（期望副本数）+ `strategy`（怎么换）。输出：一个"新版本已就绪、旧版本已下线"的稳态集群。中间经过：Deployment 控制器比对模板哈希 → 变了就建新 ReplicaSet → ReplicaSet 建 Pod → kubelet 让 Pod 真跑起来并跑探针 → 就绪且满足 `minReadySeconds` → 才计入 available → 旧 ReplicaSet 才允许缩容。
2. **能故意改坏再倒推吗？** —— 四个可执行的破坏实验：
   - **实验 A（验证取整规则）**：把 Deployment 改成 `replicas: 1`，显式写 `maxSurge: 25%` + `maxUnavailable: 25%`，然后 `kubectl rollout restart deployment/<name>`，在另一个终端 `kubectl get rs -w`。**你会看到发布过程中短暂出现 2 个 Pod，且新 RS 先扩、旧 RS 后缩**——这就是 2.5 节算出来的结果被实测验证。
   - **实验 B（验证发布卡死）**：给容器配一个 `readinessProbe` 指向**不存在的路径**（如 `/no-such-path`），再触发发布。**现象：新 Pod `Running 0/1`，`rollout status` 一直不返回，`kubectl get deploy` 的 AVAILABLE 少于期望。** 倒推原因：就绪探针不过 → 不计入 available → 旧 Pod 一个都不许删（`maxUnavailable=0`）→ 发布永远推不动。**修法**：改回正确路径。
   - **实验 C（验证 edit 被覆盖）**：`kubectl edit deployment/<name>` 把镜像 tag 改掉，`kubectl get deploy -o yaml` 确认现时配置已变；然后 `kubectl apply -f <你原来的 yaml>`，再 `kubectl get deploy -o yaml`。**现象：镜像 tag 变回了 yaml 里的值。** 倒推原因：apply 的第 2 步"配置文件中存在但其取值与现时配置不同 → 设为配置文件里的值"。
   - **实验 D（验证回滚机制）**：`kubectl rollout history deployment/<name>` 记下 REVISION 号 → 触发一次新发布 → `kubectl rollout undo deployment/<name>` → 再次 `kubectl rollout history`。**现象：REVISION 号继续往上涨，多出一条新记录，而不是回到原来的号。** 倒推原因：回滚是"改 Pod 模板触发新发布"。
3. **能画上下游吗？** —— 画出「改 template → 控制器算哈希 → 建新 RS（带 pod-template-hash）→ RS 建 Pod → scheduler 派活 → kubelet 拉镜像建容器 → readinessProbe 通过 + minReadySeconds 满足 → 计入 available → 旧 RS 缩容 → 旧 Pod 收到 SIGTERM → preStop → 退出」这条链，并在**每一环之间标出"这一步失败会看到什么状态"**（Pending / ImagePullBackOff / CrashLoopBackOff / Running 0-1 / Progressing=False）。

---

## 9. 常用运维命令（Pod / Deployment 排障速查）

> **本节能做什么**：L3 讲的是滚动更新机制，本节把**Deployment 生命周期里每天都会敲的排障命令**按场景归类——发布状态与回滚、在线改镜像与扩缩容、深度 describe/logs、以及"更新卡住"的实战排障。**这是 DevOps 岗值班最高频的一组命令。**

### ① 发布状态与回滚（救火核心）

```bash
kubectl rollout status deployment/web -n prod            # 看这次发布是否就绪
kubectl rollout history deployment/web -n prod           # 看历史版本（REVISION 只增不减）
kubectl rollout history deployment/web --revision=3 -n prod   # 看某次 revision 改了啥
kubectl rollout undo deployment/web -n prod              # 回滚到上一版（先恢复再查因）
kubectl rollout undo deployment/web --to-revision=2 -n prod  # 回滚到指定版本
kubectl rollout pause deployment/web -n prod             # 暂停发布（金丝雀：先放一小部分）
kubectl rollout resume deployment/web -n prod            # 确认无误后继续
```

> **实战价值**：发布翻车**第一反应是 `rollout undo` 先恢复服务**，再去查根因，而不是改 yaml 重发；`pause`+`resume` 是实现"先发 1 个 Pod 观察"的轻量金丝雀手段。

### ② 在线改镜像与扩缩容

```bash
kubectl set image deployment/web web=nginx:1.27 -n prod  # 改容器镜像（等价于改 template，触发滚动）
kubectl scale deployment/web --replicas=5 -n prod        # 临时扩/缩副本
kubectl rollout restart deployment/web -n prod           # 配置没变但要重启（滚动重启所有 Pod）
kubectl get deploy web -n prod -o jsonpath='{.spec.strategy}'   # 看当前更新策略
```

> **实战价值**：`set image` 是触发滚动更新最干净的方式，比手改 yaml 更贴合"声明期望态"；`rollout restart` 在 ConfigMap 已更新但 Pod 不重载时常用（配合滚动重启让新挂载生效）。

### ③ 深度排障（describe / logs / 新旧 RS）

```bash
kubectl describe deployment web -n prod       # 看 Conditions / 新旧 ReplicaSet / 事件
kubectl describe pod <pod> -n prod            # Events + 容器状态 + Restart Count + 退出码
kubectl logs <pod> -n prod -c web --tail=200 -f     # 跟随当前容器日志
kubectl logs <pod> -n prod -p -c web          # 看上一个崩溃容器日志（关键，-p 不能省）
kubectl get rs -n prod -l app=web             # 看新旧 ReplicaSet 副本分布（卡住时对比）
```

> **实战价值**：`CrashLoopBackOff` **必须用 `-p`**，否则看到的是重启后的空日志，永远找不到崩溃原因；`describe deployment` 的新旧 RS 副本数是判断"卡在滚动哪一步"的最快入口。

### ④ 排障实战：滚动更新卡在 Progressing

```bash
kubectl rollout status deployment/web -n prod         # 现象：一直 Progressing，不变成 Complete
kubectl describe deployment web -n prod | tail -20    # 看 Conditions 的 message
kubectl get pods -l app=web -n prod -o wide           # 新 RS 的 Pod 是否 Ready
# 典型：NewReplicaSet 0/1 Ready → ReadinessProbe 不过 → describe pod 看 Events（镜像拉不到/探针端口错）
```

> **读法**：滚动更新卡住基本是"新 Pod 起不来"。先看 `rollout status` 卡在哪，再看新 RS 的 Pod 状态，`describe pod` 的 Events 会直接给原因（ImagePullBackOff / Unhealthy / OOMKilled）。

---

**下一篇**：[`L4-Service与网络`](L4-Service与网络.md)（**Stable access entry · Four Service types · selector and targetPort · kube-proxy · DNS resolution**） · **上一篇**：[`L2-K8s架构与对象模型`](L2-K8s架构与对象模型.md) · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md)
