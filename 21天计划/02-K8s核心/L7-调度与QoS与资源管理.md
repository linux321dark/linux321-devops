# L7 · 调度、QoS 与资源管理（懂原理 · 会排障）

> **本篇定位**：L2 讲清了"谁在干活"，本篇讲清"**活干在哪台机器上、按什么规矩分配、抢不过怎么办**"。
> **前置**：L2 K8s 架构与对象模型（必须知道 apiserver / scheduler / kubelet 的分工、一次 apply 到 Running 的 9 步）、L0 YAML 语法。
> **学完能做什么**：① 能画出 kube-scheduler 的"过滤 → 打分"两阶段并说出每一步失败会看到什么；② 能在 30 秒内判断一个 Pending 的 Pod 是"资源不够 / 亲和性不匹配 / 污点没容忍"；③ 能说清 requests 与 limits 在调度、HPA、QoS、驱逐四个场景里的**不同**作用；④ 能独立配出"专机专用"的污点+亲和性组合；⑤ 能解释为什么一个没写 requests 的容器能把邻居拖死。
> **在 21 天里**：D2（Deployment + HPA）、D6（弹性与调度）都建立在本篇之上；L8 的 HPA 是本篇第 2.6 节的延伸。
> **环境/版本基准**：本文命令与字段以本地实测 `kubectl v1.37.1` / `Server v1.37.0`（kind 集群 `day2`，节点 `day2-control-plane`）为准。**所有默认值均以官方文档标注为准并注明出处**；本文**未执行任何 docker/kubectl 命令去改动集群**，所有 yaml 为"可自行验证"的最小样例。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L7 调度、QoS 与资源管理
│
├─ 1. 概念
│   ├─ 1.1 一句话：调度器 = 资源的"分配器 + 排序器"
│   ├─ 1.2 没有调度器时的问题
│   └─ 1.3 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 kube-scheduler 的两阶段：Filter → Score → Bind
│   ├─ 2.2 从"未调度 Pod"到"绑定到节点的 Pod"：一条链路 6 步
│   ├─ 2.3 选节点的四把钥匙：nodeSelector / nodeAffinity / podAffinity / taint
│   ├─ 2.4 taint 与 toleration：三种 effect 的差别（NoSchedule / PreferNoSchedule / NoExecute）
│   ├─ 2.5 为什么"专机专用"是 taint + 亲和性的组合拳
│   ├─ 2.6 requests 与 limits 的真实含义（四个场景各管一件事）
│   ├─ 2.7 QoS 三档：判定条件与驱逐顺序
│   ├─ 2.8 节点资源不足时，谁先被驱逐
│   ├─ 2.9 不写任何限制的容器：默认无限制 → 拖死邻居
│   ├─ 2.10 LimitRange vs ResourceQuota：两个不同的层次
│   ├─ 2.11 PriorityClass 与抢占（preemption）
│   ├─ 2.12 拓扑分布约束：把副本摊开到多个故障域
│   └─ 2.13 关键参数与默认值
│
├─ 3. 动手：一个 Deployment 挂上四种调度约束
│   ├─ 3.1 准备：看清节点标签与污点
│   ├─ 3.2 场景一：资源不足 → FailedScheduling
│   ├─ 3.3 场景二：污点没容忍 → Pending
│   ├─ 3.4 场景三：亲和性写反 → 永远调度不上
│   └─ 3.5 验证清单
│
├─ 4. 资源清单逐字段
│   ├─ 4.1 PodSpec 里的调度字段（requests/limits/nodeSelector/affinity/tolerations/topologySpreadConstraints/priorityClassName）
│   ├─ 4.2 LimitRange 与 ResourceQuota
│   └─ 4.3 PriorityClass
│
├─ 5. 横向对比
│   ├─ 5.1 nodeSelector vs nodeAffinity vs taint/toleration
│   ├─ 5.2 LimitRange vs ResourceQuota
│   └─ 5.3 三种"选节点"手段的组合策略
│
├─ 6. 踩坑与排障（症状 → 状态 → 真实原因）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 一句话定义

> **kube-scheduler 的工作是：把一个"还没分配节点的 Pod"，放到"当前最适合它的节点"上。**

这句话拆开是三个动作，对应官方文档里明确的两阶段模型 [S1]：

1. **过滤（Filtering）**——**筛掉**所有不满足硬性条件的节点，剩下的叫 **feasible nodes（可行节点）**。官方原文：*"The filtering step finds the set of Nodes where it's feasible to schedule the Pod... If the list is empty, that Pod isn't (yet) schedulable."*
2. **打分（Scoring）**——**排序**剩下的可行节点，分最高的胜出。官方原文：*"In the scoring step, the scheduler ranks the remaining nodes to choose the most suitable Pod placement."*
3. **绑定（Binding）**——把决策写回 apiserver。官方原文：*"the scheduler then notifies the API server about this decision in a process called binding."*

> **关键认知**：过滤是**布尔**的（要么在可行集里，要么不在），打分是**排序**的（谁分高谁上）。**两者失败的现象完全不同**——过滤失败 → Pod 一直 `Pending`；打分失败 → 不会失败，只会"选得不够理想"。**这个区分是排障时最先要问的问题。**

### 1.2 没有调度器时的问题

| 裸机/虚机方式 | 痛点 | 调度器怎么解决 |
|---|---|---|
| 人工 `ssh` + 脚本部署，服务挤在一台机器上 | 一台挂了全线挂 | 按 requests 装箱，副本分散到多节点 |
| 有人手动 `kill` 掉一个进程，容器不自动重建 | 挂了要人发现、人恢复 | Deployment 保证 N 个副本，缺一个补一个 |
| 关键服务跑在大机器上，测试服务跑在同一台 | 互相抢资源，互相拖垮 | QoS 分档 + 污点隔离 + 拓扑分散 |
| 一个 Pod 内存泄漏，吃光整台机器内存 | **连累邻居**，无辜的 Pod 被 OOM Kill | requests + limits + QoS 驱逐顺序 |
| 集群只剩 10% 空闲内存时，新服务还往里塞 | 新服务也起不来 | 装箱按 requests 算，不按实际用量算 |

> **关键认知**：调度解决的不是"跑得快"，是"**在有限资源里，按规矩分，并且分得公平**"。而"规矩"写在 Pod spec 的那几个字段里——**这四个字段是本篇的主线**。

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| scheduler | 调度器 | 决定 Pod 放哪个节点 |
| filter / predicate | 过滤 / 断言 | 筛掉不满足硬约束的节点 |
| score / priority | 打分 / 优先级 | 给可行节点排序 |
| feasible node | 可行节点 | 通过了全部过滤条件的节点 |
| binding | 绑定 | 把 Pod 与节点关联起来的写操作 |
| `nodeSelector` | 节点选择器 | 最简单的硬约束：按节点标签全等匹配 |
| `nodeAffinity` | 节点亲和性 | 同样按节点标签，但支持逻辑表达式 + 软偏好 |
| `podAffinity` / `podAntiAffinity` | Pod 亲和性 / 反亲和性 | 按**已在运行的 Pod 的标签**决定去哪 |
| `taint` | 污点 | 节点上的"排斥标记"，让不匹配的 Pod 走开 |
| `toleration` | 容忍度 | Pod 上的"豁免声明"，允许进污点节点 |
| `NoSchedule` | 不调度 | 污点效果：不让新 Pod 进来，**不驱逐已在跑的** |
| `PreferNoSchedule` | 尽量不调度 | 污点效果：**软性**偏好，调度器尽量绕开 |
| `NoExecute` | 不执行 | 污点效果：**连已在跑的也驱逐**，且带 `tolerationSeconds` 宽限期 |
| `requests` | 请求量 | **调度装箱依据** + 计费依据 + HPA 分母 + QoS 判定依据 |
| `limits` | 限制量 | **硬上限**，CPU 超了被节流，内存超了被 OOM Kill |
| QoS class | 服务质量等级 | `Guaranteed` / `Burstable` / `BestEffort`，决定被驱逐的先后 |
| eviction | 驱逐 | kubelet 主动杀掉 Pod 以回收节点资源 |
| OOM Kill | 内存溢出被杀 | **内核**杀的，cgroup 内存超限，与 eviction 不是一回事 |
| `LimitRange` | 限制范围 | 命名空间级：**单个对象**的默认值与上下限 |
| `ResourceQuota` | 资源配额 | 命名空间级：**总量**上限 |
| `PriorityClass` | 优先级类 | 给 Pod 优先级数字，高优先级可以抢占低优先级 |
| preemption | 抢占 | 腾出资源给高优先级 Pod，牺牲低优先级 Pod |
| `topologySpreadConstraints` | 拓扑分布约束 | 把副本摊开到不同可用区/节点，避免全挤一个机房 |

---

## 2. 机制

### 2.1 kube-scheduler 的两阶段：Filter → Score → Bind

kube-scheduler 是控制面组件，官方原文：*"kube-scheduler is the default scheduler for Kubernetes and runs as part of the control plane"* [S1]。

```
                      ┌──────────────────────────────────────┐
   未调度的 Pod        │        kube-scheduler               │      节点列表
  (spec.nodeName 为空) ─▶  ① Filter（硬约束）              ─▶  可行节点集
                      │     · 资源够不够（requests）        │      （可能为空）
                      │     · 污点能不能忍（tolerations）    │
                      │     · nodeSelector/nodeAffinity     │
                      │     · podAffinity 硬约束            │
                      │     · 端口/卷/PVC 绑定是否可行       │
                      └──────────────┬───────────────────────┘
                                     │ 可行集非空才继续
                                     ▼
                      ┌──────────────────────────────────────┐
                      │        ② Score（软偏好）             │
                      │     · 节点亲和性 preferred 的权重和   │
                      │     · Pod 亲和/反亲和 preferred 的权重│
                      │     · 拓扑分布偏差（spread）          │
                      │     · 资源均衡、最少请求等默认打分     │
                      └──────────────┬───────────────────────┘
                                     │ 取最高分（同分随机）
                                     ▼
                      ┌──────────────────────────────────────┐
                      │        ③ Bind                       │
                      │     · 向 apiserver 提交绑定          │
                      │     · Pod 的 spec.nodeName 被写上    │
                      │     · kubelet watch 到 → 开始拉镜像   │
                      └──────────────────────────────────────┘
```

**每一阶段失败会看到什么**（这张表就是排障地图）：

| 失败点 | Pod 状态 | Events 里的典型 Reason | 你会看到什么 |
|---|---|---|---|
| ① Filter 全部不通过 | `Pending` | `FailedScheduling` | `0/3 nodes are available: 3 Insufficient cpu.` |
| ① 污点不匹配 | `Pending` | `FailedScheduling` | `node(s) had untolerated taint {xxx}` |
| ① 亲和性不匹配 | `Pending` | `FailedScheduling` | `node(s) didn't match Pod's node affinity/selector` |
| ② 打分阶段 | **不会失败** | 无 | 只会"选得不够理想"，不报错 |
| ③ Bind 之后 | `Pending` | `FailedScheduling` / `FailedMount` | 已绑定但卷还没挂上 |

> **排查口诀**：**看到 `Pending` 先看 `FailedScheduling` 的 Message**——那句话里直接写着"是哪个条件不满足、几个节点不满足"。**不要猜。**

**关于调度插件（Scheduling Profiles）**：官方把调度过程抽象成一组插件阶段：`QueueSort`、`Filter`、`Score`、`Bind`、`Reserve`、`Permit` 等，可以用 `Scheduling Profiles` 配置不同 profile 给不同调度器 [S1]。生产上偶尔会看到 `spec.schedulerName: custom-scheduler`——**调度器按名字匹配**，名字对不上就永远 `Pending`。

### 2.2 从"未调度 Pod"到"绑定到节点的 Pod"：一条链路 6 步

| # | 在哪发生 | 做了什么 | 失败现象 |
|---|---|---|---|
| 1 | 控制器写 etcd | Pod 对象创建，`spec.nodeName` 为空 | 看不到 Pod |
| 2 | kube-scheduler watch 到 | 从队列取出 Pod，进入 Filter | — |
| 3 | Filter | 逐节点跑过滤插件，收集可行节点 | 全部被筛掉 → `Pending` + `FailedScheduling` |
| 4 | Score | 对可行节点打分排序，取最高 | 无失败（只有"选择偏差"） |
| 5 | Bind | 向 apiserver 提交绑定，**写 `spec.nodeName`** | 仍 `Pending` |
| 6 | kubelet watch 到 | 发现"这 Pod 归我了" → 拉镜像 → 建容器 | `ImagePullBackOff` / `CrashLoopBackOff` |

> **注意第 5 步的语义**：`spec.nodeName` 被写上，**这个 Pod 就"属于"那台节点了**。官方文档明确：*"A given Pod (as defined by a UID) is never 'rescheduled' to a different node"* [S3]。所以节点故障时不是"把 Pod 搬走"，而是**删掉它、由控制器重新创建一个新 Pod**（新 UID）。

### 2.3 选节点的四把钥匙：nodeSelector / nodeAffinity / podAffinity / taint

这四个字段经常被混为一谈，其实**问的是四个不同的问题**：

| 字段 | 问的问题 | 依据 | 硬/软 | 失败现象 |
|---|---|---|---|---|
| `nodeSelector` | "节点上有没有**这些标签**？" | 节点标签 | **只有硬**（全等 AND） | `Pending` + `FailedScheduling` |
| `nodeAffinity` | "节点标签**满足我的表达式**吗？" | 节点标签 | `required*` 硬 / `preferred*` 软 | 硬失败 → `Pending`；软失败 → 不报错 |
| `podAffinity` | "**同域的邻居**里有没有我要的 Pod？" | **其他 Pod 的标签** | `required*` 硬 / `preferred*` 软 | 硬失败 → `Pending` |
| `taint` + `toleration` | "这个节点**拒不接收**我的 Pod 吗？" | 节点污点 vs Pod 容忍 | 三种 effect 分别对应三种强度 | `Pending` |

**官方原文（assign-pod-node，中文）** [S2]：

> `nodeSelector` 只能选择拥有所有指定标签的节点。亲和性、反亲和性为你提供对选择逻辑的更强控制能力。

> `requiredDuringSchedulingIgnoredDuringExecution`：调度器只有在规则被满足的时候才能执行调度。此功能类似于 `nodeSelector`，但其语法表达能力更强。

> `preferredDuringSchedulingIgnoredDuringExecution`：调度器会尝试寻找满足对应规则的节点。如果找不到匹配的节点，调度器仍然会调度该 Pod。

> `weight` 字段取值范围是 **1 到 100**。当调度器找到能够满足 Pod 的其他调度请求的节点时，调度器会遍历节点满足的所有的偏好性规则，并将对应表达式的 `weight` 值加和。最终的加和值会添加到该节点的其他优先级函数的评分之上。

**组合规则（官方原文）** [S2]：

> 如果你同时指定了 `nodeSelector` 和 `nodeAffinity`，**两者**必须都要满足，才能将 Pod 调度到候选节点上。

> 如果你在与 nodeAffinity 类型关联的 nodeSelectorTerms 中指定多个条件，只要其中一个 `nodeSelectorTerms` 满足（各个条件按逻辑或操作组合）的话，Pod 就可以被调度到节点上。

> 如果你在与 `nodeSelectorTerms` 相关的单个 `matchExpressions` 字段中指定多个表达式，则只有当所有表达式都满足（各表达式按逻辑与操作组合）时，Pod 才能被调度到节点上。

**podAffinity 的语义（官方原文）** [S2]：

> Pod 间亲和性与反亲和性使你可以基于**已经在节点上运行的 Pod 的标签**来约束 Pod 可以调度到的节点，而不是基于节点本身的标签。

> 硬约束（节点过滤）：`podAffinity.requiredDuringSchedulingIgnoredDuringExecution` 和 `podAntiAffinity.requiredDuringSchedulingIgnoredDuringExecution`：调度器基于现有 Pod，确保新 Pod 被分配到满足这些必需的亲和性和反亲和性规则的节点上。

> 软约束（评分）：`podAffinity.preferredDuringSchedulingIgnoredDuringExecution` 和 `podAntiAffinity.preferredDuringSchedulingIgnoredDuringExecution`：调度器根据节点满足这些优选的亲和性和反亲和性规则的程度来评分，以优化 Pod 的放置。

> **这就是 podAffinity 硬约束落在 Filter、软约束落在 Score 的官方依据。**

### 2.4 taint 与 toleration：三种 effect 的差别

官方定义（taint-and-toleration，中文） [S3]：

> **污点（Taint）** 则相反——它使节点能够排斥一类特定的 Pod。
>
> **容忍度（Toleration）** 是应用于 Pod 上的。容忍度允许调度器调度带有对应污点的 Pod。**容忍度允许调度但并不保证调度**：作为其功能的一部分，调度器也会评估其他参数。

三种 effect 的**官方原文对照表** [S3]：

| Effect | 官方描述 | 影响**新** Pod 调度 | 驱逐**已在跑**的 Pod | 典型用途 |
|---|---|---|---|---|
| `NoSchedule` | *"No new Pods will be scheduled on the tainted node unless they have a matching toleration. Pods currently running on the node are **not** evicted."* | **阻止** | **不驱逐** | 专机专用、GPU 节点、保留节点给系统组件 |
| `PreferNoSchedule` | *"'preference' or 'soft' version of `NoSchedule`. The control plane will *try* to avoid placing a Pod that does not tolerate the taint on the node, but it is not guaranteed."* | **尽量避开**，但保证不了 | 不驱逐 | "尽量不要来，但如果没别的选择也行" |
| `NoExecute` | *"Pods that do not tolerate the taint are evicted **immediately**"* | **阻止** | **立即驱逐** | 节点要下线、要维护、硬件坏了 |

`NoExecute` 的完整规则（官方原文） [S3]：

> - Pods that do not tolerate the taint are evicted immediately
> - Pods that tolerate the taint **without** specifying `tolerationSeconds` in their toleration specification remain bound forever
> - Pods that tolerate the taint **with** a specified `tolerationSeconds` remain bound for the specified amount of time. After that time elapses, the node lifecycle controller evicts the Pods from the node.

**运维命令与格式**（官方原文） [S3]：

```bash
# 打污点：<key>=<value>:<effect>
kubectl taint nodes node1 key1=value1:NoSchedule

# 移除污点：末尾加一个减号
kubectl taint nodes node1 key1=value1:NoSchedule-
```

> **两个易错点**：
> 1. **移除是加 `-`，不是 `delete`**（`delete` 是删整个 node 对象）。
> 2. **带数值比较的 toleration 要求 key 的值是合法有符号 64 位整数**，官方原文：*"Both the toleration and taint values must be valid signed 64-bit integers (zero leading numbers (e.g., "0550") are not allowed)."* [S3] —— 所以 `"0550"` 这种带前导零的值会让匹配失败。

### 2.5 为什么"专机专用"是 taint + 亲和性的组合拳

这是本篇最容易答错的一问。**只用污点是不够的**——官方文档专门讲了这件事（taint-and-toleration，中文，"专用节点"段） [S3]：

> **专用节点**：如果想将某些节点专门分配给特定的一组用户使用，你可以给这些节点添加一个污点（即 `kubectl taint nodes nodename dedicated=groupName:NoSchedule`），然后给这组用户的 Pod 添加一个相对应的容忍度……**拥有上述容忍度的 Pod 就能够被调度到上述专用节点，同时也能够被调度到集群中的其它节点。** 如果你希望这些 Pod 只能被调度到上述专用节点，那么你还需要给这些专用节点另外添加一个和上述污点类似的 label（例如：`dedicated=groupName`），同时还要在上述准入控制器中给 Pod 增加节点亲和性要求，要求上述 Pod 只能被调度到添加了 `dedicated=groupName` 标签的节点上。

**翻译成机制语言**（这两句是本篇的题眼）：

| 需求 | 手段 | 为什么 |
|---|---|---|
| **专用节点别被普通 Pod 占了** | 节点打 `taint: dedicated=groupA:NoSchedule` | 普通 Pod 没有对应 toleration → 被过滤掉 |
| **我的 Pod 必须去专用节点** | 节点打 `label: dedicated=groupA` + Pod 写 `nodeAffinity`（required）匹配该 label | 只有亲和性能做"必须去" |
| **我的 Pod 也能去普通节点（可选）** | 只写 toleration，不写 required 亲和性 | toleration 只"允许"，不"强制" |

> **一句话记忆**：**污点 = 节点说"不欢迎"；容忍度 = Pod 说"我可以进"；亲和性 = Pod 说"我只能进这儿"。**
> **所以"专机专用"的标准做法是：污点（挡住别人）+ 标签 + required 亲和性（锁定自己）三者一起上。**

### 2.6 requests 与 limits 的真实含义

**很多人以为 requests 和 limits 就俩字段，实际上它们在四个场景里扮演四种角色。** 这是本节的核心表：

| 场景 | requests 起作用 | limits 起作用 | 官方依据 |
|---|---|---|---|
| **① 调度装箱** | ✅ **唯一依据**：*"Pod scheduling is based on requests"*，调度器保证**各容器 requests 之和**不超过节点可用量 | ❌ 不参与调度 | [S4] |
| **② 计费** | ✅ 云厂商按 requests 计费（ACK 等托管服务的常规口径） | 通常不参与 | 云厂商口径，以 ACK 控制台为准 |
| **③ HPA 利用率分母** | ✅ **分母是 requests**（省略时退化为 limits） | ❌ 不是分母 | [S5]（L8 展开） |
| **④ QoS 分档 + 驱逐** | ✅ 参与判定 | ✅ 参与判定 | [S6] |
| **⑤ 运行时封顶** | ❌ | ✅ **硬上限**：CPU 超了被节流、内存超了被 OOM Kill | [S5] |

**requests 与 limits 的四种组合（官方原文）**：

| 写法 | 调度看到什么 | 运行上限 | HPA 分母 | 典型后果 |
|---|---|---|---|---|
| **两个都不写** | 按实现默认值 | 无上限 | **拿不到分母** → `<unknown>/50%` | BestEffort，最先被驱逐 |
| **只写 limits** | **requests 退化成 limits 的值** | limits | 分母 = limits → **利用率永远偏低，几乎不扩** | 官方原文：*"如果你为某个资源指定了限制值，但不指定请求值……那么 Kubernetes 会复制你所指定的限制值，将其用作资源的请求值。"* [S5] |
| **只写 requests** | 按 requests | **无上限** | 分母 = requests ✅ 正常 | Burstable |
| **requests == limits** | 按 requests | limits | 分母 = requests ✅ 正常 | **Guaranteed**，最后被驱逐 |
| **requests > limits** | — | — | — | **非法**，apply 直接报错 |

> **官方原文（中文，管理容器资源）** [S5]：
> > 如果你为某个资源指定了限制值，但不指定请求值，并且没有应用某种准入时机制为该资源设置默认请求值，那么 Kubernetes 会复制你所指定的限制值，将其用作资源的请求值。
>
> > `cpu` 限制通过 CPU 节流机制（CPU Throttling）来强制执行。当某容器接近其 `cpu` 限制值时，内核会基于容器的限制值来限制其对 CPU 的访问。因此，`cpu` 限制是内核强制执行的一个硬性限制。
>
> > **CPU 限制定义的是容器可使用的 CPU 时间的硬性上限。在每个调度周期（时间片）期间，Linux 内核检查是否已经超出该限制；内核在允许该 CGroup 恢复执行之前会等待。**
>
> > （反面）*容器运行时不会由于 CPU 使用率过高而杀死 Pod 或容器。*

> **注意最后那句**：CPU 超限**不会**杀容器，只会被**节流（变慢）**。节流会让采集到的用量**顶到 limit 值**——所以它是「**扩了也没用**」的根源（每个新 Pod 同样被限死），**不是「有值但不扩」的原因**；后者是分母被撑大。详见 L8 §2.8。

**官方原文（中文，分配 CPU 资源）** [S4]：

> 如果你没有为容器指定 CPU 限制，则会发生以下情况之一：
> - **容器在可以使用的 CPU 资源上没有上限。因而可以使用所在节点上所有的可用 CPU 资源。**
> - 容器在具有默认 CPU 限制的名字空间中运行，系统会自动为容器设置默认限制。集群管理员可以使用 LimitRange 指定 CPU 限制的默认值。

### 2.7 QoS 三档：判定条件与驱逐顺序

**QoS 类在 Pod 创建时确定，并在 Pod 的整个生命周期内保持不变**——官方原文：*"QoS 类在 Pod 创建时确定，并在 Pod 的整个生命周期内保持不变。如果你之后尝试进行一次原地资源调整，且该调整会导致 QoS 类发生变化，则该调整请求会在准入阶段被拒绝。"* [S6]

**三档的判定条件（官方中文原文）** [S6]：

| QoS | 官方判据 | 资源行为 |
|---|---|---|
| **Guaranteed** | - 每个容器必须有**内存** limit 和 request，两者**都必须大于零**且**相等**<br>- 每个容器必须有 **CPU** limit 和 request，两者**都必须大于零**且**相等** | *"这些 Pod 不可以获得超出其指定 limit 的资源。"* |
| **Burstable** | - **不满足** Guaranteed 的判据<br>- 且 Pod 中至少一个容器有内存或 CPU 的 request **或** limit | *"如果未指定 limit，则默认为其 limit 等于 Node 容量，这允许 Pod 在资源可用时灵活地增加其资源。"* |
| **BestEffort** | 不满足 Guaranteed **也不满足** Burstable（所有容器都没有 memory/CPU 的 limit **也没有** request） | *"BestEffort QoS 类中的 Pod 可以使用未专门分配给其他 QoS 类中的 Pod 的节点资源。"* |

> **判定要点（极易错）**：
> - Guaranteed 的判据是**"每个容器"**都满足，不是"Pod 层面满足"。**一个 Pod 里有一个 sidecar 没写 limits，这个 Pod 就不是 Guaranteed。**
> - **只写 request 不写 limit → Burstable**（不是 Guaranteed）。
> - **只写 limit 不写 request → Burstable**（requests 自动等于 limits，但 **limit ≠ request 的判据是"相等"**，所以只有当 limit==request 时才算 Guaranteed；若只写 limit，requests 被复制为 limit，此时**两者相等 → 实际上会落入 Guaranteed**——这一点官方文档没有明说，本篇按 [S6] + [S5] 的组合推断，标 `⚠️ 待验证`，实测以 `kubectl describe pod` 的 QoS Class 字段为准）。
> - **Pod 级别资源（Pod-level resources）**：官方标注 Beta since v1.34、默认启用，Pod 级 request/limit 相等也是 Guaranteed 判据之一 [S6]。

**驱逐顺序（官方中文原文，最重要的一句）** [S6]：

> **当一个 Node 耗尽资源时，Kubernetes 将首先驱逐在该 Node 上运行的 `BestEffort` Pod，然后是 `Burstable` Pod，最后是 `Guaranteed` Pod。当这种驱逐是由于资源压力时，只有超出资源请求的 Pod 才是被驱逐的候选对象。**

### 2.8 节点资源不足时，谁先被驱逐

把上面的顺序和"谁先"这个问题放在一起，答案是三层：

| 层次 | 顺序 | 说明 |
|---|---|---|
| **第 1 层：QoS 档位** | BestEffort → Burstable → Guaranteed | 官方明确 [S6] |
| **第 2 层：档内挑谁** | 只有**超出自己 requests** 的 Pod 才是候选 | 官方明确 [S6]：*"只有超出资源请求的 Pod 才是被驱逐的候选对象"* |
| **第 3 层：什么时候开始驱逐** | kubelet 监控内存/磁盘/inode，达到**驱逐阈值**就驱逐 | 官方 [S7]：kubelet 报告 `MemoryPressure` / `DiskPressure` / `PIDPressure` 条件 |

**kubelet 的默认硬驱逐阈值（官方英文原文）** [S7]：

> - `memory.available<100Mi` (Linux nodes)
> - `memory.available<500Mi` (Windows nodes)
> - `nodefs.available<10%`
> - `imagefs.available<15%`
> - `nodefs.inodesFree<5%` (Linux nodes)
> - `imagefs.inodesFree<5%` (Linux nodes)

> **注意区分两个完全不同的"死法"**（这是本篇的高频面试题）：
>
> | | **OOM Kill** | **节点压力驱逐（eviction）** |
> |---|---|---|
> | 触发者 | **Linux 内核 / cgroup** | **kubelet** |
> | 判据 | 容器内存超过自己的 `limits.memory` | 节点整体可用内存低于驱逐阈值 |
> | 后果 | 只有**这个容器**被杀重启（Pod 可能还在，容器重启） | **整个 Pod** 被终止，控制器会重建 |
> | 与 QoS 的关系 | **无关**（谁超谁死） | **有关**（按 QoS 顺序挑） |
> | 节点上其他 Pod | **不受影响** | **受影响**（BestEffort 先死） |
>
> **一句话**：OOMKilled 是"你自找的"，eviction 是"节点撑不住，按等级排队"。

### 2.9 不写任何限制的容器：默认无限制 → 拖死邻居

这是本篇最值得记住的**因果链**：

```
不写 resources.requests / resources.limits
        │
        ├─▶ QoS 判据：所有容器都没有 memory/CPU 的 limit 也没有 request
        │      └─▶ QoS = BestEffort  【官方 S6】
        │
        ├─▶ 官方原文：「容器在可以使用的 CPU 资源上没有上限。因而可以使用所在节点上所有的可用 CPU 资源。」【S8】
        │      └─▶ CPU：可以吃光整台机器的 CPU（节流机制不存在，因为没有 limit）
        │
        ├─▶ 内存：无 limit 就没有 cgroup 内存上限
        │      └─▶ 官方原文（管理容器资源）：「如果你没有设置内存限制，Pod 的内存消耗将没有上限，并且可能会用掉节点上的所有可用内存。」【S7】
        │      └─▶ 节点内存耗尽 → 触发 memory.available 驱逐阈值 → kubelet 开始驱逐
        │
        ├─▶ 驱逐顺序：BestEffort 最先死  【S6】
        │      └─▶ 这个"没设限的邻居"确实会第一个被驱逐
        │      └─▶ **但它的内存已经把节点吃满了，在它被驱逐之前，同节点的正常业务已经受影响**
        │
        └─▶ 内存不足时内核 OOM killer 出手
               └─▶ OOM Kill 的是**占用最多的进程**，不一定是肇事者
                      └─▶ 结果：**受害的是 innocent 的邻居**
```

**官方原文（管理容器资源，中文）** [S5]：

> 如果你没有为 `emptyDir` 卷指定 `sizeLimit`，该卷就会消耗 Pod 的内存，卷的用量上限为 Pod 的内存限制（`Pod.spec.containers[].resources.limits.memory`）。**如果你没有设置内存限制，Pod 的内存消耗将没有上限，并且可能会用掉节点上的所有可用内存。**

> Kubernetes 基于资源请求（`Pod.spec.containers[].resources.requests`）调度 Pod，并且在决定另一个 Pod 是否适合调度到某个给定的节点上时，**不会考虑超出请求的内存用量**。这可能导致拒绝服务，并使得操作系统需要处理内存不足（OOM）的情况。

> **注意最后一句的严重性**：调度器装箱只看 requests。**一个 requests=0 的 Pod 可以被调度到任何节点上，哪怕这个节点只剩 10Mi 可用内存。**它进去之后膨胀 → 节点内存耗尽 → **无辜的邻居被 OOM Kill 或被驱逐**。

> **生产结论（三条硬规矩）**：
> 1. **生产业务容器必须写 `requests`**——不写就没有调度保护，也没有计费依据，也没有 HPA 分母。
> 2. **内存必须写 `limits`**——CPU 不写 limit 只是节流（慢），内存不写 limit 是**整台机器的内存炸弹**。
> 3. **想要 Guaranteed 就 requests == limits**（每个容器都写），这样节点紧张时你的 Pod 最后才被牺牲。

### 2.10 LimitRange vs ResourceQuota：两个不同的层次

**官方原文（中文，限制范围）** [S8]：

> LimitRange 是限制命名空间内可为每个**适用的对象类别**（例如 Pod 或 PersistentVolumeClaim）指定的资源分配量（限制和请求）的策略对象。

> **Kubernetes 默认情况下，集群上的容器运行使用的计算资源没有限制。** 使用 Kubernetes 资源配额，管理员可以在一个指定的命名空间内限制集群资源的使用与创建。

**官方原文（英文，Limit Ranges）** [S9]：

> A LimitRange is a policy to constrain the resource allocations (limits and requests) that you can specify for each applicable object kind (such as Pod or PersistentVolumeClaim) in a namespace.
>
> A *LimitRange* provides constraints that can:
> - Enforce minimum and maximum compute resources usage per Pod or Container in a namespace.
> - Enforce minimum and maximum storage request per PersistentVolumeClaim in a namespace.
> - Enforce a ratio between request and limit for a resource in a namespace.
> - Set default request/limit for compute resources in a namespace and automatically inject them to Containers at runtime.

> - If you attempt to create or update an object (Pod or PersistentVolumeClaim) that violates a LimitRange constraint, your request to the API server will fail with an HTTP status code **`403 Forbidden`** and a message explaining the constraint that has been violated.
> - LimitRange validations occur only at **Pod admission stage**, not on running Pods. If you add or modify a LimitRange, the Pods that already exist in that namespace continue unchanged.
> - If two or more LimitRange objects exist in the namespace, it is **not deterministic** which default value will be applied.

**官方原文（英文，Resource Quotas）** [S10]：

> A resource quota, defined by a ResourceQuota object, provides constraints that limit aggregate resource consumption **per namespace**. A ResourceQuota can also limit the quantity of objects that can be created in a namespace by API kind...
>
> If creating or updating a resource violates a quota constraint, the control plane **rejects that request with HTTP status code `403 Forbidden`**. The error includes a message explaining the constraint that would have been violated.
>
> Neither contention nor changes to quota will affect already created resources.

**两者的层次差别（本节的题眼）**：

| 维度 | **LimitRange** | **ResourceQuota** |
|---|---|---|
| 管的对象 | **单个** Pod / Container / PVC | **整个命名空间的总量** |
| 管什么 | 值的**形状**：默认值、上下限、比例 | 值的**总和**：最多能花多少 |
| 典型问题 | "你的容器不能申请超过 2 核 / 内存不能低于 128Mi" | "这个命名空间所有 Pod 加起来最多 100 核 / 200Gi" |
| 拒绝方式 | `403 Forbidden`（准入阶段拒绝） | `403 Forbidden`（准入阶段拒绝） |
| 作用时机 | 只在 **Pod 准入阶段**，**已运行的 Pod 不受影响** | 同上，**已创建的资源不受影响** |
| 典型报错 | `must be less than or equal to cpu limit` / 低于 min 被拒 | `exceeded quota: <name>, requested: ..., used: ..., limited: ...` |
| 类比 | **个人报销单的上限**（单张发票不能超过） | **部门月度预算**（这个月总共不能超过） |

> **两者的关系（官方交叉引用）**：*"If you add a LimitRange in a namespace that applies to compute-related resources such as `cpu` and `memory`, you must specify requests or limits for those values. Otherwise, the system may reject Pod creation."* [S9]
> ——也就是说，**配额强制要求写 requests/limits，LimitRange 负责把没写的补上**。**生产上的标准组合是：LimitRange 给默认值 + ResourceQuota 卡总量。**
>
> **一个必须知道的坑（官方原文）** [S9]：
> > A LimitRange does **not** check the consistency of the default values it applies. This means that a default value for the *limit* that is set by LimitRange may be less than the *request* value specified for the container in the spec... **If that happens, the final Pod will not be schedulable.**
> >
> > 官方给的报错原文：
> > ```
> > Pod "example-conflict-with-limitrange-cpu" is invalid: spec.containers[0].resources.requests: Invalid value: "700m": must be less than or equal to cpu limit
> > ```
> > **即：LimitRange 的 default 小于你写的 request → Pod 创建时就被拒，报错里直接告诉你 request 超过 limit。**

### 2.11 PriorityClass 与抢占（preemption）

**官方原文（Pod 优先级与抢占，英文）** [S11]：

> A PriorityClass is a non-namespaced object that defines a mapping from a priority class name to the integer value of the priority... **The higher the value, the higher the priority.**
>
> A PriorityClass object can have any 32-bit integer value smaller than or equal to 1 billion. This means that the range of values for a PriorityClass object is from **-2147483648 to 1000000000 inclusive**.
>
> Kubernetes already ships with two PriorityClasses: `system-cluster-critical` and `system-node-critical`. **For Kubernetes v1.37 their priority values are 2000000000 for `system-cluster-critical` and 2000001000 for `system-node-critical`.**

> If a Pod cannot be scheduled, the scheduler tries to **preempt (evict) lower priority Pods** to make scheduling of the pending Pod possible.
>
> Preemption logic tries to find a Node where removal of one or more Pods with lower priority than P would enable P to be scheduled on that Node. If such a Node is found, one or more lower priority Pods get evicted from the Node. After the Pods are gone, P can be scheduled on the Node.

> When Pod priority is enabled, the scheduler orders pending Pods by their priority and a pending Pod is placed ahead of other pending Pods with lower priority in the scheduling queue. As a result, the higher priority Pod may be scheduled sooner... **If such Pod cannot be scheduled, the scheduler will continue and try to schedule other lower priority Pods.**

> Pods with `preemptionPolicy: Never` will be placed in the scheduling queue ahead of lower-priority pods, but they **cannot preempt** other pods. A non-preempting pod waiting to be scheduled will stay in the scheduling queue, until sufficient resources are free, and it can be scheduled.
>
> **`preemptionPolicy` defaults to `PreemptLowerPriority`**, which will allow pods of that PriorityClass to preempt lower-priority pods.

**三个必须讲清的细节**：

| 事实 | 官方原文依据 | 实践含义 |
|---|---|---|
| **抢占是"最后手段"，只在节点级** | *"scheduler does not perform cross-node preemption"* [S11] | 抢占只在**同一台节点**上腾位置，不会为了一个 Pod 把另一个节点的 Pod 赶走 |
| **高优先级不保证高优先级一定调度上** | *"If such Pod cannot be scheduled, the scheduler will continue and try to schedule other lower priority Pods."* [S11] | 高优先级只是**排前面**，不是"必须给它腾地方" |
| **抢占 ≠ QoS** | QoS 文档明确：*"kube-scheduler 在选择要抢占的 Pod 时不考虑 QoS 类"* [S6] | **调度期看优先级，运行期看 QoS**。两个机制在不同阶段生效 |
| **`preemptionPolicy: Never` 的 Pod 仍可被抢占** | *"Non-preempting pods may still be preempted by other, high-priority pods."* [S11] | "我不抢别人"≠"别人不能抢我" |

> **生产建议**：系统组件（`kube-system` 里那些）用 `system-cluster-critical`（值 2000000000），保证节点资源紧张时它们不被抢占；业务 Pod 自定义一个**低于系统级**的值（比如 100000），并用 `preemptionPolicy: Never` 明确"我只排队、不踢人"，避免业务之间的互相踩踏。

### 2.12 拓扑分布约束：把副本摊开到多个故障域

**官方原文（中文，拓扑分布约束）** [S12]：

> 你可以使用**拓扑分布约束（Topology Spread Constraints）**来控制 Pod 在集群内故障域之间的分布，例如区域（Region）、可用区（Zone）、节点和其他用户自定义拓扑域。这样做有助于实现高可用并提升资源利用率。

> - **whenUnsatisfiable** 指示如果 Pod 不满足分布约束时如何处理：
>   - **`DoNotSchedule`（默认）**告诉调度器不要调度。
>   - **`ScheduleAnyway`** 告诉调度器仍然继续调度，只是根据如何能将偏差最小化来对节点进行排序。
>
> - **nodeAffinityPolicy** 表示我们在计算 Pod 拓扑分布偏差时将如何处理 Pod 的 nodeAffinity/nodeSelector。选项为：
>   - **Honor**：只有与 nodeAffinity/nodeSelector 匹配的节点才会包括到计算中。
>   - **Ignore**：nodeAffinity/nodeSelector 被忽略。所有节点均包括到计算中。
>   **如果此值为 nil，此行为等同于 Honor 策略。**
>
> - **nodeTaintsPolicy** 表示我们在计算 Pod 拓扑分布偏差时将如何处理节点污点……**如果此值为 null，此行为等同于 Ignore 策略。**

**内置默认约束（官方英文，GA since v1.24）** [S12]：

```yaml
defaultConstraints:
- maxSkew: 3
  topologyKey: "kubernetes.io/hostname"
  whenUnsatisfiable: ScheduleAnyway
- maxSkew: 5
  topologyKey: "topology.kubernetes.io/zone"
  whenUnsatisfiable: ScheduleAnyway
```

> **默认约束的两个要点**：① **全是 `ScheduleAnyway`**，所以**默认不会因为分布不均而拒绝调度**（它只影响打分排序）；② 官方提醒：*"对于分布约束中所指定的拓扑键而言，`PodTopologySpread` 插件不会为不包含这些拓扑键的节点评分"* —— **节点没打 `kubernetes.io/hostname` / `topology.kubernetes.io/zone` 标签 → 被忽略**。
>
> **生产建议**：想要"强均衡"（少一个可用区就拒绝新副本），显式写 `whenUnsatisfiable: DoNotSchedule` 的约束，别指望默认约束。

**"幽灵 Pod"陷阱（官方原文）** [S12]：

> 与自身的 labelSelector 不匹配的 Pod 会产生"幽灵 Pod"……这类 Pod 可能会持续堆积在同一个拓扑域上……**请确保 Pod 的标签与分布约束中的 `labelSelector` 相匹配。通常，Pod 应当能够匹配到它自己定义的拓扑分布约束选择算符。**

### 2.13 关键参数与默认值

| 参数 | 默认 | 含义 | 出处 |
|---|---|---|---|
| 调度亲和性 `weight` 范围 | **1–100** | 软偏好的权重 | [S2] |
| `nodeAffinityPolicy`（拓扑分布） | **Honor**（nil 等同 Honor） | 计算分布时是否遵守 nodeAffinity | [S12] |
| `nodeTaintsPolicy`（拓扑分布） | **Ignore**（null 等同 Ignore） | 计算分布时是否考虑污点 | [S12] |
| `whenUnsatisfiable`（拓扑分布） | **DoNotSchedule** | 分布不满足时是否拒绝调度 | [S12] |
| `minDomains`（拓扑分布） | **1** | 合格拓扑域最小数量 | [S13] |
| 内置默认分布约束 | `maxSkew:3/hostname` + `maxSkew:5/zone`，均 `ScheduleAnyway` | GA since v1.24 | [S12] |
| PriorityClass 取值范围 | **-2147483648 ~ 1000000000** | 32 位有符号整数，上限 10 亿 | [S11] |
| `system-cluster-critical` | **2000000000** | v1.37 官方值 | [S11] |
| `system-node-critical` | **2000001000** | v1.37 官方值 | [S11] |
| `preemptionPolicy` | **PreemptLowerPriority** | 是否允许抢占低优先级 Pod | [S11] |
| kubelet 硬驱逐阈值（Linux） | `memory.available<100Mi`、`nodefs.available<10%`、`imagefs.available<15%`、`nodefs.inodesFree<5%`、`imagefs.inodesFree<5%` | 触发节点压力驱逐 | [S7] |
| 污点移除语法 | 末尾加 `-` | `kubectl taint nodes node1 key1=value1:NoSchedule-` | [S3] |
| `QoS Class` 判定时点 | **Pod 创建时确定，终生不变** | 改资源导致 QoS 变化 → 准入阶段被拒 | [S6] |

---

## 3. 动手

> **前置提醒（来自 L2 的实测结论）**：kind 集群 `day2` 是**单节点**，节点名 `day2-control-plane`，且**控制面节点自带污点** `node-role.kubernetes.io/control-plane:NoSchedule`。所以本节的"资源不足"和"污点"场景在单节点 kind 上**天然可复现**，而"多节点亲和性"场景需要先扩节点。

### 3.1 准备：先看清节点长什么样

```bash
# ① 看节点：注意 STATUS、ROLES、TAINTS 三列
kubectl get nodes -o wide

# ② 看节点标签（做 nodeSelector/亲和性之前必须做这一步）
kubectl get nodes --show-labels

# ③ 看节点污点（做 toleration 之前必须做这一步）
kubectl describe node day2-control-plane | sed -n '/Taints/,/Unschedulable/p'
```

**看到什么算对**：

| 检查 | 期望 |
|---|---|
| `kubectl get nodes` | `day2-control-plane  Ready  control-plane  <age>  v1.37.0` |
| `--show-labels` | 能看到 `kubernetes.io/hostname=day2-control-plane`、`node-role.kubernetes.io/control-plane=`（**值可能为空**） |
| `describe node` 的 Taints | 有 `node-role.kubernetes.io/control-plane:NoSchedule`（kind 默认打） |

> **为什么必须先看标签**：**调度失败最常见的原因就是"标签拼错了"**。凭记忆写 `nodeSelector` 而不看节点真实标签，是最低效的排障方式。

### 3.2 场景一：资源不足 → FailedScheduling

写 `demo-oversize.yaml`：

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: demo-oversize
spec:
  containers:
  - name: big
    image: registry.k8s.io/pause:3.10
    resources:
      requests:
        cpu: "100"        # 远超任何节点容量
        memory: "8Gi"
```

```bash
kubectl apply -f demo-oversize.yaml
kubectl get pod demo-oversize
```

**看到什么算对**：

| 现象 | 期望 |
|---|---|
| `kubectl get pod` | `demo-oversize  0/1  Pending  0  <age>` |
| `kubectl describe pod demo-oversize` 的 Events | `Reason: FailedScheduling`，Message 是官方给出的原文格式 [S4]：`No nodes are available that match all of the following predicates:: Insufficient cpu (3).`（节点数不同则数字不同） |
| Pod **不会**自己变 Running | 一直 Pending |

> **关键理解**：调度器按 **requests** 判断，不是按实际用量。你这台机器 CPU 明明很闲，只要没有**一台**节点能同时满足这个 request，它就调度不上。

### 3.3 场景二：污点没容忍 → Pending

在 kind 上，控制面节点默认带 `node-role.kubernetes.io/control-plane:NoSchedule` 污点。写一个**不带** toleration 的普通 Pod：

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: demo-no-toleration
spec:
  containers:
  - name: app
    image: registry.k8s.io/pause:3.10
```

（**注意：这个 Pod 没有 `nodeSelector`，也没有 `tolerations`，也没有 `resources.requests`** —— 三个坑一次凑齐。）

**看到什么算对**：

| 现象 | 期望 |
|---|---|
| `kubectl get pod` | `demo-no-toleration  0/1  Pending` |
| `describe` 的 Events | `FailedScheduling`，Message 里出现 **`had taint {node-role.kubernetes.io/control-plane:NoSchedule}`** 或 `untolerated taint` 类描述 |
| 这条 Pod 的 QoS | `kubectl describe pod` 的 `QoS Class:` 显示 **`BestEffort`**（因为没写任何 requests/limits） |

**加上容忍度再试**：

```yaml
spec:
  tolerations:
  - key: node-role.kubernetes.io/control-plane
    operator: Equal
    value: ""
    effect: NoSchedule
```

**看到什么算对**：Pod 变成 `Running`（`pause` 镜像不退出）。**这一条对比就把"污点机制"讲透了**：同一个 Pod，只加 5 行 tolerations，状态从 Pending 变 Running。

> **再进一步**：只加 toleration 的 Pod **也能被调度到没有污点的普通节点上**——toleration 只"允许进"，不"必须进"。要"必须进"，还得加 `nodeAffinity`（见 3.4）。

### 3.4 场景三：亲和性写反 → 永远调度不上

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: demo-bad-affinity
spec:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: dedicated
            operator: In
            values:
            - groupA        # 节点上根本没有这个标签
```

**看到什么算对**：

| 现象 | 期望 |
|---|---|
| `kubectl get pod` | 一直 `Pending` |
| `describe` 的 Events | `FailedScheduling`，Message 含 **`didn't match Pod's node affinity/selector`** 类描述 |

**正确写法（两种等价）**：

```yaml
# 写法 A：nodeSelector（最简单，全等匹配）
spec:
  nodeSelector:
    dedicated: groupA

# 写法 B：nodeAffinity required（支持逻辑表达式）
spec:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
        - matchExpressions:
          - key: dedicated
            operator: In
            values: ["groupA"]
```

> **"写反"的两种典型形态**（都会导致永远 Pending）：
> 1. **标签值写错**：`values: ["groupA"]` 但节点上是 `groupa` / `group-a` / 压根没这个标签。**大小写敏感、不做模糊匹配。**
> 2. **operator 用错**：`operator: In` 配 `values` 是对的；写成 `operator: Exists` 就变成"节点上有**任意**这个 key 的标签都算匹配"，语义完全变了。

### 3.5 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| 节点就绪 | `kubectl get nodes` | `Ready` |
| 节点标签已知 | `kubectl get nodes --show-labels` | 抄下真实标签再写 selector |
| 节点污点已知 | `kubectl describe node <n>`（看 Taints 段） | 抄下真实污点再写 toleration |
| 资源不足的报错 | `kubectl describe pod demo-oversize` | Events 有 `FailedScheduling` + `Insufficient cpu` |
| 污点生效 | 加 toleration 后 Pod 变 `Running` | 同 Pod，仅差 tolerations |
| QoS 判定 | `kubectl describe pod <p>`（看 QoS Class 字段） | 无 requests/limits → `BestEffort` |

---

## 4. 资源清单逐字段

### 4.1 PodSpec 里的调度字段

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `spec.containers[].resources.requests.cpu` | Quantity | | **调度装箱依据 + 计费依据 + HPA 分母** | 漏写 → HPA `<unknown>/50%`；调度不设防 → 被邻居拖死 |
| `spec.containers[].resources.requests.memory` | Quantity | | 同上 | 漏写 → BestEffort，最先被驱逐；节点内存被吃光时连累邻居 |
| `spec.containers[].resources.limits.cpu` | Quantity | | CPU 硬上限，超了**节流**（不杀容器） | 太小 → 容器变慢、采集值顶到 limit → **HPA 会扩但每个新 Pod 同样被限死，扩了没用** |
| `spec.containers[].resources.limits.memory` | Quantity | | 内存硬上限，超了 **OOMKilled** | 不写 → 无上限，可能吃光节点内存 |
| `spec.containers[].resources.requests` > `limits` | — | | **非法组合** | apply 直接报错 |
| `spec.nodeSelector` | map | | 节点标签**全等**匹配（AND） | 标签名/值写错 → 永远 `Pending` |
| `spec.affinity.nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution` | []NodeSelectorTerm | | **硬**约束，表达式匹配节点标签 | 写错 → 永远 `Pending`；不匹配时**永不重试到成功**（除非节点标签变了） |
| `spec.affinity.nodeAffinity.preferredDuringSchedulingIgnoredDuringExecution` | []PreferredSchedulingTerm | | **软**偏好，`weight` 1–100 | 写错 → **不报错**，只是打分不生效（最难发现的错误） |
| `spec.affinity.podAffinity.requiredDuringSchedulingIgnoredDuringExecution` | []PodAffinityTerm | | **硬**约束：必须和已存在的某类 Pod 同域 | 写错 → 永远 `Pending`；`labelSelector` 选错对象是最常见原因 |
| `spec.affinity.podAntiAffinity.requiredDuringSchedulingIgnoredDuringExecution` | []PodAffinityTerm | | **硬**约束：必须**不**和某类 Pod 同域 | 写错 → 副本全挤在一台机器上（如果写反了 selector） |
| `spec.affinity.podAffinity/AntiAffinity.preferredDuringScheduling*` | []WeightedPodAffinityTerm | | **软**偏好 | 同上，不报错只不生效 |
| `spec.tolerations[].key` | string | | 容忍的污点 key | 写错 → 等于没写 |
| `spec.tolerations[].operator` | enum | | `Exists` / `Equal`（默认 `Equal`） | 配 `Exists` 时不写 `value`；配 `Equal` 时必须写 `value` |
| `spec.tolerations[].value` | string | | 容忍的污点 value | **数值比较时必须是合法有符号 64 位整数，前导零不允许**（`"0550"` 非法） |
| `spec.tolerations[].effect` | enum | | `NoSchedule` / `PreferNoSchedule` / `NoExecute` | 写 `NoExecute` → **已在跑的 Pod 也会被驱逐**，且需配 `tolerationSeconds` |
| `spec.tolerations[].tolerationSeconds` | int | | **仅对 `NoExecute` 生效**的宽限期 | 配在 `NoSchedule` 上 → **完全无效**（不报错） |
| `spec.tolerations[].operator: Exists`（空 value） | — | | 容忍**所有**该 key 的污点 | 范围太宽 → 意外进了本不该进的节点 |
| `spec.topologySpreadConstraints[].maxSkew` | int | ✅ | 允许的最大不均衡度，**必须 > 0** | 写 0 → API 拒绝 |
| `spec.topologySpreadConstraints[].topologyKey` | string | ✅ | 节点标签 key，作为拓扑域划分依据 | 节点缺这个标签 → **该节点被忽略**，新 Pod 也调度不上去 |
| `spec.topologySpreadConstraints[].whenUnsatisfiable` | enum | ✅ | `DoNotSchedule`（默认）/ `ScheduleAnyway` | 设 `DoNotSchedule` 后域数不足 → 拒绝调度 |
| `spec.topologySpreadConstraints[].labelSelector` | LabelSelector | | 统计哪些 Pod 的分布 | 不匹配自身标签 → **"幽灵 Pod"**，分布计算失真 |
| `spec.topologySpreadConstraints[].minDomains` | int | | 合格拓扑域最小数量，默认 **1** | — |
| `spec.topologySpreadConstraints[].nodeAffinityPolicy` | enum | | `Honor`（默认）/ `Ignore` | — |
| `spec.topologySpreadConstraints[].nodeTaintsPolicy` | enum | | `Honor` / `Ignore`（**默认 Ignore**） | — |
| `spec.priorityClassName` | string | | 指定 PriorityClass 名字 | 名字不存在 → **Pod 无法创建**（不是 Pending，是准入失败）；值太高 → 会踢掉别人的 Pod |
| `spec.priority` | int | | 直接指定优先级数值（可被 `priorityClassName` 覆盖） | 与 `priorityClassName` 同时给 → **以 priorityClassName 为准** |
| `spec.preemptionPolicy` | enum | | `PreemptLowerPriority`（默认）/ `Never` | 写 `Never` → 只排队不抢人，但**自己仍可被别人抢** |
| `spec.schedulerName` | string | | 指定调度器名字 | 名字对不上 → **永远 Pending**，且 Events 可能为空 |
| `spec.nodeName` | string | | **绕过调度器**直接指定节点 | 官方明确这是"高级场景"；*"If the assigned Node is too loaded, bypassing the scheduler may result in Pod failure."* [S2] |

> **两个"不报错但没生效"的字段**（最难排查）：`preferredDuringScheduling*`（软偏好写错不报错）、`tolerationSeconds` 配在非 `NoExecute` effect 上（无效）。
> **两个"直接报错"的字段**：`requests > limits`、`priorityClassName` 指向不存在的 PriorityClass。

### 4.2 LimitRange 与 ResourceQuota

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| LimitRange `spec.limits[].type` | enum | ✅ | `Container` / `Pod` / `PersistentVolumeClaim` | 选错类型 → 约束作用在错误的对象上 |
| LimitRange `spec.limits[].default` | map | | **默认 limit**（未写 limit 时注入） | 默认 limit < 你写的 request → **Pod 不可调度**，报错 `must be less than or equal to cpu limit` |
| LimitRange `spec.limits[].defaultRequest` | map | | **默认 request** | 同上 |
| LimitRange `spec.limits[].min` | map | | 每个对象的**最小** request/limit | 低于 min → `403 Forbidden` |
| LimitRange `spec.limits[].max` | map | | 每个对象的**最大** request/limit | 高于 max → `403 Forbidden` |
| LimitRange `spec.limits[].maxLimitRequestRatio` | map | | request/limit 的**比例**约束 | 比例不合法 → `403 Forbidden` |
| ResourceQuota `spec.hard` | map | ✅ | 各项资源的**总上限** | 超过 → `403 Forbidden` + `exceeded quota` |
| ResourceQuota `spec.scopes` | []string | | 作用范围（如 `BestEffort` / `NotBestEffort` / `Terminating` / `NotTerminating` / `PriorityClass`） | 范围选错 → **该放的被拒、不该放的放过** |
| ResourceQuota `spec.scopeSelector` | object | | 进一步按 PriorityClass 等维度筛选 | 同上 |
| ResourceQuota `spec.hard.count/<resource>` | Quantity | | 对象**个数**上限（Pod/Service/ConfigMap…） | 超过 → `403 Forbidden` |

> **两者同时存在时的官方行为** [S9]：
> - LimitRange 的**默认值会先被注入**，再由 ResourceQuota 检查总量。
> - *"If two or more LimitRange objects exist in the namespace, it is not deterministic which default value will be applied."* → **一个命名空间只放一个 LimitRange**，否则默认值不确定。
> - *"LimitRange validations occur only at Pod admission stage, not on running Pods."* → 改 LimitRange **不会**影响已运行的 Pod。
> - *"Neither contention nor changes to quota will affect already created resources."* → **改 ResourceQuota 也不会**影响已创建的 Pod（只影响新建）。

### 4.3 PriorityClass

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: scheduling.k8s.io/v1` | string | ✅ | API 组与版本 | 写 `v1beta1` → 版本问题 |
| `kind: PriorityClass` | string | ✅ | 对象类型 | — |
| `metadata.name` | string | ✅ | **类名**，Pod 用 `priorityClassName` 引用 | 重名 → `AlreadyExists` |
| `value` | int32 | ✅ | 优先级数值，范围 **-2147483648 ~ 1000000000** | 超过 10 亿 → API 拒绝 |
| `globalDefault` | bool | | 是否作为**未指定 priorityClassName** 时的全局默认 | 设为 true → 所有没指定 priority 的 Pod 都用它（慎用） |
| `description` | string | | 人类可读说明 | — |
| `preemptionPolicy` | enum | | `PreemptLowerPriority`（默认）/ `Never` | 设为 `Never` 的类，**该类所有 Pod 都不抢占** |

---

## 5. 横向对比

### 5.1 nodeSelector vs nodeAffinity vs taint/toleration

| 维度 | **nodeSelector** | **nodeAffinity** | **taint + toleration** |
|---|---|---|---|
| **方向** | Pod → 节点（我要去哪） | Pod → 节点（我要去哪） | 节点 → Pod（你别来 / 你可以来） |
| **依据** | 节点标签 | 节点标签 | 节点的污点 vs Pod 的容忍 |
| **表达能力** | 只有全等 AND | `In`/`NotIn`/`Exists`/`DoesNotExist`/`Gt`/`Lt`，terms 之间 OR、expressions 之间 AND | key/value/effect 三元组匹配 |
| **硬约束** | ✅ 天然硬 | ✅ `required*` 硬 / ✅ `preferred*` 软（带 weight） | ✅ 三档强度（`NoSchedule` / `PreferNoSchedule` / `NoExecute`） |
| **能否强制"必须去"** | ✅ | ✅ | ❌ **不能**——toleration 只"允许"，官方原文明确："拥有上述容忍度的 Pod 就能够被调度到上述专用节点，**同时也能够被调度到集群中的其它节点**" [S3] |
| **能否排斥别人** | ❌ | ❌ | ✅ **这是污点独有的能力** |
| **能否驱逐已运行的 Pod** | ❌ | ❌ | ✅ **仅 `NoExecute`** |
| **失败现象** | `Pending` + `FailedScheduling` | 硬约束失败 → `Pending`；软约束失败 → **不报错** | `Pending` + `FailedScheduling`；`NoExecute` 还会驱逐在跑的 |
| **典型用途** | 简单硬绑定（`disk=ssd`） | 复杂逻辑（多可用区择一） | **专机专用**、GPU 节点、保留资源给系统组件 |

**选择依据（三句话）**：

1. **只是想"去有某个标签的节点"** → 用 `nodeSelector`。简单够用，不要一上来就写 affinity。
2. **需要"多条件择一"或"软偏好"** → 用 `nodeAffinity`。
3. **需要"这个节点不欢迎别人"** → **必须用 taint**。这是 nodeSelector 和 affinity 都做不到的。

> **面试高分回答**：*"nodeSelector 和 nodeAffinity 都是 Pod 单方面表达'我想去哪'，是**拉力**；taint 是节点单方面表达'你别来'，是**斥力**，配 toleration 才是'我可以进'。专机专用必须**拉力 + 斥力一起上**：节点打污点挡住别人，再打标签 + required 亲和性把自己钉死。"*

### 5.2 LimitRange vs ResourceQuota

| 维度 | **LimitRange** | **ResourceQuota** |
|---|---|---|
| 层次 | **单对象**级（每个 Pod / 每个容器） | **命名空间总量**级 |
| 管什么 | **值的形状**（默认 / 最小 / 最大 / 比例） | **值的总和**（最多能花多少） |
| 类比 | 报销单上限：单张不能超 5000 | 部门预算：整个月不能超 10 万 |
| 拒绝时机 | Pod 准入阶段 | Pod 准入阶段 |
| 拒绝方式 | **`403 Forbidden`** | **`403 Forbidden`** |
| 对已有 Pod 的影响 | **无**（官方明确） | **无**（官方明确） |
| 谁能改 | 命名空间管理员 / 集群管理员 | 集群管理员（通常） |
| 组合关系 | **给默认值**，让配额能算得清 | **卡总量**，防止一个命名空间吃满集群 |

**生产标准组合（三层）**：

```
第 1 层：LimitRange   → 保证每个 Pod 的 requests/limits 不为空、不过分、不颠倒
第 2 层：ResourceQuota → 保证这个命名空间的总消耗不超过预算
第 3 层：QoS（requests==limits）→ 保证节点紧张时你的 Pod 最后才被驱逐
```

> **注意顺序**：**没有 LimitRange 的默认值，ResourceQuota 强制要求的 `requests`/`limits` 就没法自动补齐**，用户写的 Pod 会因为"没写 requests"被配额拒掉（官方原文：*"If quotas are enabled in a namespace for resource such as `cpu` and `memory`, users must specify requests or limits for those values when they define a Pod; otherwise, the quota system may reject pod creation."* [S10]）。

### 5.3 三种"选节点"手段的组合策略

| 场景 | nodeSelector/affinity | taint/toleration | 为什么 |
|---|---|---|---|
| **普通无状态服务** | 不用 | 不用 | 让调度器自由选，资源利用率最高 |
| **磁盘类型要求**（ssd/nvme） | `nodeSelector: disk=ssd` | 不用 | 只要求"去哪"，不需要排斥别人 |
| **专机专用**（GPU 节点给训练任务） | required 亲和性匹配 `dedicated=groupA` | 节点打 `dedicated=groupA:NoSchedule` + Pod 加 toleration | **两者都要**：污点挡住别人，亲和性锁定自己 |
| **保留资源给系统组件** | 不用 | 节点打 `reserved=system:NoSchedule` | 只需排斥业务 Pod，系统组件自带 toleration |
| **同一服务的副本必须分散** | `podAntiAffinity`（required 或 preferred） | 不用 | 防"副本全挤一台" |
| **副本跨可用区分散** | 不用 | 不用 | 用 `topologySpreadConstraints` |
| **节点维护中允许短暂腾挪** | 不用 | `PreferNoSchedule` | 尽量别来，但没别的选择时也放行 |

---

## 6. 踩坑与排障（症状 → 状态 → 真实原因）

**排障铁律：`Pending` 一律先看 `FailedScheduling` 的 Message 原文。** 那句话里已经写明了"几个节点、因为哪个条件"。

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| Pod 一直不起来，`kubectl get pods` 显示 `0/1 Pending` | `Pending` | **资源不足**：所有节点的可分配量都放不下这个 Pod 的 **requests**（不是实际用量） | `describe` 看 `FailedScheduling` 里的 `Insufficient cpu` / `Insufficient memory`；调小 requests 或加节点 | requests 按实测基线的 1.2–1.5 倍填，不要拍脑袋 |
| Pod 一直 `Pending`，Message 含 `had taint {...}` | `Pending` | **节点有污点，Pod 没有对应 toleration** | `describe node` 抄下污点 → 加 `tolerations`（key/operator/value/effect 四项对齐） | 节点入池脚本统一打污点；工作负载模板统一带 toleration |
| Pod 一直 `Pending`，Message 含 `didn't match Pod's node affinity/selector` | `Pending` | **亲和性写反/写错**：标签 key 拼错、value 大小写不符、或 `operator` 用错 | `kubectl get nodes --show-labels` 打印真实标签，逐字对照 yaml | 先看标签再写 selector；写完先 `--dry-run=client` |
| Pod 一直 `Pending`，**Events 里有失败记录但都很久以前** | `Pending` | **历史残留**：之前失败过，现在的条件已满足但调度器没重试，或事件未刷新 | 看 `LastTimestamp` 和 `Count`——`Count` 大但时间久 = 旧问题；时间新 = 当前问题 | 排障先看时间戳，不要看 Events 数量就下结论 |
| 某个容器把整台机器内存吃光，**邻居 Pod 被 OOM Kill** | 邻居 `OOMKilled`；肇事者可能还 `Running` | 肇事容器**没写 `limits.memory`** → 无上限；调度器装箱只看 requests → 它能被塞进任何节点 | 给肇事者加 `limits.memory` + `requests.memory`；给节点加内存预留 | **CI 校验必填 `resources.limits.memory`**；LimitRange 设默认 |
| 节点上别的 Pod 莫名其妙被驱逐（`Evicted`） | 邻居 `Evicted` | 节点**内存/磁盘**到驱逐阈值 → kubelet 按 **QoS 顺序**驱逐：BestEffort → Burstable → Guaranteed | `describe node` 看 `MemoryPressure`/`DiskPressure` 条件；给节点加内存或清理磁盘 | 业务 Pod 做到 **Guaranteed**（requests==limits） |
| 自己写的 Pod 被拒，apply 报 `403 Forbidden` | 对象根本没创建 | **ResourceQuota 超限**（命名空间总量用完） | 报错里有 `exceeded quota` 与 requested/used/limited；找管理员加额度或清理 | 用独立命名空间 + 独立配额；CI 里跑 `kubectl describe quota` |
| 自己写的 Pod 被拒，报 `must be less than or equal to cpu limit` | 对象没创建 | **LimitRange 的默认 limit 小于你写的 request**（LimitRange 不检查自己注入值的一致性，官方明确） | 把 LimitRange 的 `default` 调大，或在 Pod 里同时写 requests 和 limits | 同一命名空间只留一个 LimitRange；改 LimitRange 后重跑一次 apply 验证 |
| `apply` 报 `PriorityClass "xxx" not found` | 对象没创建 | `priorityClassName` 指向不存在的 PriorityClass | 先 `kubectl get priorityclass` 看有哪些 | 建命名空间时一并下发 PriorityClass |
| `kubectl describe pod` 显示 QoS 是 `BestEffort`，但你明明写了 limits | `BestEffort` | 该 Pod 里**至少有一个容器**没写（Guaranteed 要求**每个容器**都满足） | 逐个容器核对（尤其 sidecar） | 所有容器统一走同一份资源模板 |
| HPA 永远 `<unknown>/50%` | 正常 | **`requests.cpu` 没写** | 补 `requests.cpu` | CI policy 校验必填字段 |
| HPA **扩了但业务还是慢** | 正常 | `limits.cpu` 太小 → cgroup **节流** → 每个新 Pod 同样被限死 → **扩了没用** | 提高 limit；或改用 QPS 等业务指标 | 监控 `container_cpu_cfs_throttled` 类指标 |
| Pod 被**别人踢掉**了（自己的高优先级业务被踢） | 突然消失 | 你或别人的 PriorityClass 值过高，**触发了抢占**（`preemptionPolicy: PreemptLowerPriority`） | 检查 `kubectl get priorityclass` 的值分布 | 业务优先级值远低于系统级（2000000000） |
| `spec.schedulerName` 指的名字调度器不存在 | `Pending`（Events 可能为空） | 自定义调度器没装或名字拼错 | `kubectl get pods -n kube-system` 确认；或去掉该字段 | 用默认调度器，别乱改 schedulerName |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/` | kube-scheduler 两阶段（Filtering / Scoring / Binding）、feasible node、插件阶段 | ✅ 2026-10-24 |
| S2 | 中文 | `kubernetes.io/zh-cn/docs/concepts/scheduling-eviction/assign-pod-node/` | nodeSelector / nodeAffinity / podAffinity 硬软约束、weight 1–100、组合规则、nodeName 旁路 | ✅ 2026-10-24 |
| S3 | 中文 | `kubernetes.io/zh-cn/docs/concepts/scheduling-eviction/taint-and-toleration/` | 污点/容忍度定义、三种 effect 原文、专用节点（污点+label+亲和性）、移除语法 | ✅ 2026-10-24 |
| S4 | 中文 | `kubernetes.io/zh-cn/docs/tasks/configure-pod-container/assign-cpu-resource/` | "Pod scheduling is based on requests"、Pod 级 request 为容器之和、不指定 limit 的后果 | ✅ 2026-10-24 |
| S5 | 中文 | `kubernetes.io/zh-cn/docs/concepts/configuration/manage-resources-containers/` | requests/limits 语义、指定 limit 未指定 request 时复制 limit、CPU 节流机制、空内存上限风险 | ✅ 2026-10-24 |
| S6 | 中文 | `kubernetes.io/zh-cn/docs/concepts/workloads/pods/pod-qos/` | QoS 三档判据原文、驱逐顺序原文、QoS 创建时固定、调度器抢占不看 QoS | ✅ 2026-10-24 |
| S7 | 官方 | `kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/` | 节点压力驱逐、MemoryPressure/DiskPressure/PIDPressure、默认硬驱逐阈值 | ✅ 2026-10-24 |
| S8 | 中文 | `kubernetes.io/zh-cn/docs/concepts/policy/limit-range/` | LimitRange 概念与约束项 | ✅ 2026-10-24 |
| S9 | 官方 | `kubernetes.io/docs/concepts/policy/limit-range/` | LimitRange 约束能力、403 Forbidden、仅准入阶段生效、多 LimitRange 不确定、一致性不检查 | ✅ 2026-10-24 |
| S10 | 官方 | `kubernetes.io/docs/concepts/policy/resource-quotas/` | ResourceQuota 命名空间级总量、403 Forbidden、已创建资源不受影响、配额要求写 requests | ✅ 2026-10-24 |
| S11 | 官方 | `kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/` | PriorityClass 取值范围、系统类取值、抢占语义、不跨节点、preemptionPolicy 默认值 | ✅ 2026-10-24 |
| S12 | 中文 | `kubernetes.io/zh-cn/docs/concepts/scheduling-eviction/topology-spread-constraints/` | 拓扑分布约束字段语义、默认约束、nodeAffinityPolicy/nodeTaintsPolicy 默认值、幽灵 Pod | ✅ 2026-10-24 |
| S13 | 官方 | `kubernetes.io/docs/reference/kubernetes-api/workload-resources/pod-v1/` | PodSpec / TopologySpreadConstraint 字段与默认值（minDomains=1 等） | ✅ 2026-10-24 |
| S14 | 官方 | `kubernetes.io/docs/reference/using-api/` | 资源清单字段语义交叉验证 | ✅ 2026-10-24 |
| S15 | 中文 | `kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-pods/` | Pod 排障动作（describe / logs / exec） | ✅ 2026-10-24 |
| S16 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 托管版的调度与配额实践差异 | ✅ 2026-10-24 |
| S17 | 云厂商 | `cloud.tencent.com/document/product/457` | TKE 托管版对照 | ✅ 2026-10-24 |

> **完整来源矩阵（含全部 15 个组件）见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。** 本篇只列用到的。
> **链接实测方法见** [`../09-参考索引/README.md`](../09-参考索引/README.md)（含 3 次重试脚本，**000 是网络抖动不是失效**）。本篇全部 URL 于 2026-10-24 用该脚本逐条实测。

### 待验证清单

| # | 待验证项 | 原因 | 怎么验 |
|---|---|---|---|
| 1 | **只写 `limits`（不写 requests）时 Pod 的 QoS 到底是不是 `Guaranteed`** | 官方 [S6] 的 Guaranteed 判据是"request 与 limit 相等"，而 [S7] 说"未指定 request 时会复制 limit"——两者组合的**直接结论官方没有明写** | 在测试集群建一个只写 `limits` 的 Pod，`kubectl describe pod` 看 `QoS Class` 字段的实测值 |
| 2 | **HPA 分母在"只写 limits"时是否真的退化为 limits** | 官方 [S5] 的"复制 limit 到 request"发生在**准入阶段**，HPA 读的是 Pod 实际的 requests；理论上等价，但需实测确认 | 建只写 `limits.cpu` 的 Deployment + HPA，看 `kubectl get hpa` 的 TARGETS 分母 |
| 3 | **kind 单节点集群上 `node-role.kubernetes.io/control-plane` 污点的 value 是否为空字符串** | 不同 kind 版本行为可能不同 | `kubectl describe node day2-control-plane` 看 Taints 段实测 |
| 4 | **拓扑分布约束 `minDomains` 在 v1.37 的具体行为** | 官方 [S15] 只给了默认值 1，未说明与 `maxSkew` 的交互细节 | `kubectl explain pod.spec.topologySpreadConstraints --recursive` 看本集群版本的 Description |
| 5 | **ACK 托管版是否对 PriorityClass 值有额外限制** | 官方 [S13] 是开源 K8s 的规则，托管版可能有额外策略 | 以 ACK 控制台的 PriorityClass 管理页说明为准 |

> **单源知识点标注**：
> - **拓扑分布约束的 `minDomains` 完整语义** —— **⚠️ 单源待补**（仅有 API 参考的默认值说明，缺中文概念页的完整描述）
> - **ACK 托管版专属差异**（如 PriorityClass 限制、调度器插件白名单） —— **⚠️ 单源待补**（仅引用了 ACK 用户指南入口，未逐项核对）

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入是一个 `spec.nodeName` 为空的 Pod + 集群节点列表；输出是"这个 Pod 被绑定到某台具体节点"。中间经过 **Filter（筛出可行节点）→ Score（打分排序）→ Bind（写回 apiserver）**。Filter 失败 → `Pending` + `FailedScheduling`；Score 永不失败，只影响选择质量；Bind 之后交给 kubelet。
2. **能故意改坏再倒推吗？** —— 四个实验：① 把 `requests.cpu` 改成 `"100"`（远超节点容量）→ 看 `FailedScheduling` 里出现 `Insufficient cpu`；② 给节点打一个污点却不给 Pod toleration → 看 Pod 从 Running 变 Pending（加回 toleration 又变 Running）；③ 把 `nodeSelector` 的 value 改错一个字母 → 看 `didn't match Pod's node affinity/selector`；④ 把 `limits.cpu` 删掉只留 `requests.cpu` → 看 `describe` 里 QoS 从 `Guaranteed` 变 `Burstable`，再全删 → 变 `BestEffort`（**这一条把 QoS 判据刻进肌肉记忆**）。
3. **能画上下游吗？** —— 画出 `Pod(spec.nodeName=空)` → `kube-scheduler` → **Filter（resources.requests / nodeSelector / nodeAffinity / podAffinity硬约束 / taint-toleration）** → **Score（affinity preferred 权重 / podAffinity preferred / topologySpread）** → `Bind` → `spec.nodeName` → `kubelet` → 容器。并且在 Filter 旁标出"失败 = Pending + FailedScheduling"，在容器旁标出"内存超 limit = OOMKilled"、"节点内存不足 = 按 BestEffort→Burstable→Guaranteed 驱逐"。

---

**下一篇**：[`L8-弹性HPA.md`](L8-弹性HPA.md)（本篇 2.6 节的 requests/limits 分母问题，在那里展开成完整链路） · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md) · **上一篇**：[`L2-K8s架构与对象模型.md`](L2-K8s架构与对象模型.md)
