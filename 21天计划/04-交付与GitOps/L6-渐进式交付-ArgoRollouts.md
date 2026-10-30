# L6 · 渐进式交付与 Argo Rollouts（把"发布"变成"可观测、可回退的实验"）

> **本篇定位**：L5 讲的是"怎么把变更同步到集群"，本篇讲**"怎么让变更在集群里以可控的方式灰度、验证、随时回退"**。渐进式交付不是"更高级的部署方式"，它是一套**把发布风险切碎、并且用数据自动裁决**的工程方法。
> **前置**：L5 ArgoCD 策略与多集群（GitOps、漂移检测、sync-wave）、L2 K8s 架构与对象模型（期望态/实际态/协调）、L3 Deployment 与滚动更新（`maxSurge`/`maxUnavailable`/`progressDeadlineSeconds`）。
> **学完能做什么**：① 能说清"传统滚动更新对用户是一次性切换，没有小流量验证这一步"这个根本差异；② 能画出 Rollout 与 Deployment 的关系并写出 canary steps；③ 能解释 `setWeight` 在"有 trafficRouting"与"没有 trafficRouting"两种情况下**行为完全不同**；④ 能写出一个基于 Prometheus 的 AnalysisTemplate；⑤ 能说清蓝绿为什么切流量是"改 Service selector"而不是"搬 Pod"；⑥ 能判断一个业务该不该上渐进式交付。
> **在 21 天里**：D15（渐进式交付日）。
> **环境/版本基准**：本文以 `argoproj.github.io/argo-rollouts/` 官方文档（stable 站点）为准；本地 K8s 为 v1.37.x（kind 集群 `day2`）。**Argo Rollouts 的字段随版本演进**（如 `dynamicStableScale`、`pingPong`、`TargetGroup Weight Verification` 都是较新版本引入），遇到不一致以官方文档对应章节为准。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
渐进式交付与 Argo Rollouts
│
├─ 1. 概念
│   ├─ 1.1 一句话：把"一次性切换"变成"可分步、可观测、可回退的连续过程"
│   ├─ 1.2 没有渐进式交付时的问题（滚动更新的四个不可见）
│   └─ 1.3 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 Rollout 是什么：CRD + controller，与 Deployment 的关系
│   ├─ 2.2 三种策略对比：RollingUpdate / Canary / BlueGreen
│   ├─ 2.3 Canary：steps 动作全解
│   │   ├─ 2.3.1 setWeight —— 权重到底切在哪一层
│   │   ├─ 2.3.2 pause —— 人工闸门
│   │   ├─ 2.3.3 setCanaryScale 与 dynamicStableScale —— 副本数与流量权重是两件事
│   │   ├─ 2.3.4 analysis —— 把可观测性接进发布流程
│   │   ├─ 2.3.5 experiment —— A/B 实验旁路
│   │   └─ 2.3.6 setHeaderRoute / setMirrorRoute —— 定向与镜像
│   ├─ 2.4 trafficRouting：Istio / Gateway API / ALB 注解三条路
│   │   ├─ 2.4.1 Istio（host-level 与 subset-level）
│   │   ├─ 2.4.2 Gateway API（第三方插件）
│   │   └─ 2.4.3 ALB 注解方式（本地民企更可能走这条）
│   ├─ 2.5 Analysis：AnalysisTemplate / ClusterAnalysisTemplate / AnalysisRun
│   ├─ 2.6 BlueGreen：previewService / activeService 与 selector 切换
│   ├─ 2.7 回滚：abort / undo / retry / rollbackWindow
│   └─ 2.8 渐进式交付与 GitOps 的分工
│
├─ 3. 动手：最小金丝雀 + 一次 Analysis
├─ 4. 逐字段表
│   ├─ 4.1 Canary steps 动作表
│   └─ 4.2 Rollout strategy 对照表
├─ 5. 横向对比
├─ 6. 踩坑与排障
├─ 7. 参考来源
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 一句话定义

> **渐进式交付（progressive delivery）= 把"发布"从一次性全量切换，改造成"分批次放量 + 每批都有验证 + 随时可回退"的连续过程。**

关键词是**过程**而不是"策略"。一次金丝雀发布不是一个动作，而是：

```
小流量 → 观察 → 判定 → 放量 → 再观察 → 再判定 → 全量
                ↑                      ↑
             失败就停                失败就回退
```

### 1.2 没有渐进式交付时的问题

**传统 Deployment 滚动更新（RollingUpdate）** 做了什么？

```
maxSurge=25% → 先起新 Pod
maxUnavailable=25% → 逐批替换旧 Pod
新副本全部 Ready → 旧副本逐批下线
```

**用户视角**：在 `maxUnavailable` 允许的范围内，旧版本和新版本**同时在服务**，然后旧版本被逐个撤掉。**用户看到的是"某一段时间里新版本已经在承接一部分流量了"**，而且这段时间的长度 = 整个滚动过程（可能几分钟）。

**四个不可见**：

| 不可见 | 后果 | 具体事故形态 |
|---|---|---|
| **没有"小流量验证"这一步** | 新版本在接**全部流量之前**，你没有在低风险状态下验证过它 | 新版本有内存泄漏 → 放量到 50% 时 OOMKilled，**一半用户受影响** |
| **新旧版本的差异被时间冲淡** | 滚动过程几分钟后结束，**没人有时间对比新旧指标** | 新版本 P99 从 20ms 涨到 200ms，滚动结束后大家看到的是"整体变慢了"，但**说不清是哪一次发布引入的** |
| **回滚代价高** | 回滚 = 再滚一次 = 又一轮混合版本 | 回滚本身也需要时间，期间新旧继续混跑，**故障时间被拉长** |
| **没有"发布这件事"的数据** | 成功/失败靠人点，**不发布就不知道有没有问题** | 每周发版 5 次，其中 3 次没人看监控 |

> **关键认知**：滚动更新不是"慢一点的切换"，它是"**不可观测的切换**"。它没有给你"用 5% 流量验证 10 分钟"这个动作。**渐进式交付补的就是这个动作。**

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| **Progressive Delivery** | 渐进式交付 | 分批放量 + 自动验证 + 可回退的发布方法论 |
| **Rollout** | 渐进式发布资源 | Argo Rollouts 的 CR，`argoproj.io/v1alpha1` |
| **Canary（金丝雀）** | 金丝雀发布 | 新版本先接一小部分流量，逐步放大 |
| **Blue-Green（蓝绿）** | 蓝绿发布 | 两套环境并存，**一次性切换入口** |
| **A/B Testing** | A/B 测试 | 长期固定分流做对照实验 |
| **Stable ReplicaSet** | 稳定版副本集 | 当前承接生产流量的版本 |
| **Canary ReplicaSet** | 金丝雀副本集 | 正在被验证的新版本 |
| **`setWeight`** | 设置权重 | 把多少比例的流量分给金丝雀 |
| **`pause`** | 暂停 | 步骤流里的闸门，可定时或人工放行 |
| **Traffic Router / trafficRouting** | 流量路由器 | 把"权重"翻译成真实流量分配的组件 |
| **`trafficRouting`** | 流量路由配置 | Canary 策略下的配置字段（**不是 step 动作**） |
| **Analysis / AnalysisRun** | 分析 / 分析任务 | 自动跑指标查询并判定成败 |
| **AnalysisTemplate** | 分析模板 | 可复用的指标判定规则 |
| **ClusterAnalysisTemplate** | 集群级分析模板 | 跨命名空间可用的分析模板 |
| **Experiment** | 实验 | 旁路起一个临时版本做 A/B |
| **Promotion / Promote** | 晋级 / 放行 | 让暂停中的发布继续走 |
| **Abort** | 中止 | 停止当前发布，流量切回稳定版 |
| **Undo** | 撤销 | 把期望状态回退到上一个版本 |
| **`rollouts-pod-template-hash`** | 版本哈希标签 | Argo Rollouts 注入到 Pod 标签里，Service 靠它区分版本 |
| **Subset** | 子集 | Istio `DestinationRule` 里区分版本的标签集合 |

---

## 2. 机制

### 2.1 Rollout 是什么：CRD + controller，与 Deployment 的关系

**Rollout 是一个自定义资源（CR）**，它的 `spec` 里有一大段和 Deployment **完全兼容**的 Pod 模板字段：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: rollouts-demo
spec:
  replicas: 5
  revisionHistoryLimit: 2
  selector:
    matchLabels:
      app: rollouts-demo
  template:                       # ← 与 Deployment 的 template 结构一致
    metadata:
      labels:
        app: rollouts-demo
    spec:
      containers:
        - name: rollouts-demo
          image: argoproj/rollouts-demo:red
  strategy:
    canary:                       # ← 代替 Deployment 的 strategy.type
      steps:
        - setWeight: 20
        - pause: {}
```

**它和 Deployment 的关系，一句话讲清**：

> **Rollout 就是一个"会玩金丝雀的 Deployment"。它同样托管 ReplicaSet、同样管副本数、同样有 selector 和 pod template；区别只是多了一个 `strategy.canary` / `strategy.blueGreen`，把"什么时候换流量、换多少、要不要等人"这些决策变成了声明式的 steps。**

| 维度 | Deployment | Rollout |
|---|---|---|
| `apiVersion` | `apps/v1` | `argoproj.io/v1alpha1` |
| Pod 模板字段 | `spec.template` | **相同**（官方原文："Same as deployment"） |
| `spec.selector` | ✅ | ✅（官方原文：必须与 pod template 的 labels 匹配） |
| `spec.replicas` | ✅ | ✅（官方原文：默认 1） |
| `spec.revisionHistoryLimit` | ✅ | ✅（官方原文：默认 10） |
| `spec.progressDeadlineSeconds` | ✅（默认 600） | ✅（默认 **600s**，官方原文） |
| `spec.minReadySeconds` | ✅ | ✅（默认 0） |
| 更新策略 | `strategy.type: RollingUpdate/Recreate` | `strategy.canary` / `strategy.blueGreen` |
| **流量权重控制** | ❌ | ✅ `setWeight` |
| **暂停点** | ❌ | ✅ `pause` |
| **自动指标判定** | ❌ | ✅ `analysis` / `AnalysisRun` |
| **旁路实验** | ❌ | ✅ `experiment` |
| **由谁执行** | K8s 内置 deployment-controller | **`argo-rollouts` controller** |
| **API 稳定性** | `apps/v1`（GA） | `v1alpha1`（**alpha**） |

**两条重要的兼容路径**（官方原文）：

| 字段 | 语义 | 约束 |
|---|---|---|
| `spec.workloadRef` | **引用一个已有工作负载**（如 Deployment）来提供 Pod 模板 | 官方原文："If used, then do not use Rollout template property" —— **用了它就不能用 `template`** |
| `spec.scaleDown` | 从 Deployment 迁移到 Rollout 后，原 Deployment 怎么缩容 | 取值 `never` / `onsuccess` / `progressively`；官方原文还说明"**如果 Rollout 失败，Deployment 会被重新扩容**" |

> **`workloadRef` + `scaleDown` 是"平滑迁移"路径**：不重写 Deployment，先让 Rollout 接管 Pod 模板，再逐步把 Deployment 缩掉。这对已经跑了很久的存量服务很有用。**但注意官方明确写了 `scaleDown: never` 等选项的存在——它默认不缩，说明官方也认为这一步需要显式决策。**

### 2.2 三种策略对比：RollingUpdate / Canary / BlueGreen

| 维度 | **RollingUpdate**（Deployment） | **Canary**（金丝雀） | **BlueGreen**（蓝绿） |
|---|---|---|---|
| **流量怎么切** | 靠**副本数比例**间接切（kube-proxy 轮询） | 靠**流量路由器**按权重切（Istio/ALB/Gateway API） | **不切比例**，靠**改 Service selector 整体换端点** |
| **切换时机** | 旧 Pod 逐个被新 Pod 替换完 | 按 `steps` 逐步 setWeight | 晋级那一刻**一次性切** |
| **回滚速度** | **慢**（要再滚一轮，期间新旧混跑） | **快**（权重调回 0 / abort 即切回） | **最快**（selector 改回去，秒级） |
| **资源成本** | 低（略微超出 maxSurge） | **高**（新旧两套并存，stable 通常保持 100%） | **最高**（两套完整容量） |
| **用户可见的中断** | 无（但**新版本已经承接部分流量**且没人验证过） | 无 | 无（前提是容量够、selector 切换原子） |
| **"验证"这一步** | ❌ **不存在** | ✅ **核心能力**（pause + analysis） | ✅ 可做（prePromotionAnalysis / postPromotionAnalysis） |
| **适合** | 无状态、可容忍短暂不一致的后台服务 | **对外 API、核心链路、变更风险高的服务** | **不能蓝绿二选一之外灰度、但要求"切/回"都极快的场景**（如需要连数据库连接一起切） |
| **前提依赖** | 无 | **需要流量路由能力**（或接受近似） | 需要**双倍容量** + 一个能改 selector 的 Service |

**选型决策（三句话版）**：

- **默认先用 Deployment 滚动更新**。它够用且成本最低，**不要为了"先进"而上金丝雀**。
- **变更风险高 / 用户可见 / 有明确指标可验证 → 上 Canary**。核心收益是"**小流量验证 + 自动判定 + 秒级回退**"三件套。
- **必须"整体切换、整体回退"且资源充足 → 蓝绿**。典型是**数据库主从切换**、**需要老版本和新版本同时在线做数据校验**、**要求回滚时限极紧**的场景。

> **成本必须算清**：蓝绿要求**双倍容量**。一个 20 副本的服务做蓝绿，峰值就是 40 副本。**在集群资源紧张时，蓝绿不是"更安全"，是"更危险"**——因为扩容不及预期会导致调度失败，反而拖垮发布。

### 2.3 Canary：steps 动作全解

**`spec.strategy.canary.steps` 是一个有序列表**，Rollout controller 逐条执行，**每条执行完才走下一条**。

**⚠️ 先纠正一个常见的字段名错误**：网上大量文章把 canary step 写成 `scaleUp` / `scaleDown`。**这不是 Argo Rollouts 的标准 step 动作名**：

| 常见误写 | Argo Rollouts 官方准确名称 | 控制对象 |
|---|---|---|
| `scaleUp` | **`setCanaryScale`** | **Canary 副本数** |
| `scaleDown` | **`setCanaryScale`** | **Canary 副本数** |
| `setStableScale` | **官方不存在这个 step 动作**。Stable 副本数靠 `strategy.canary.dynamicStableScale: true`（v1.1+ 的**策略字段**，不是 step） | **Stable 副本数** |
| `trafficRouting`（写成 step） | **`trafficRouting` 是 `spec.strategy.canary` 下的配置字段**，不是 step 动作 | — |

> **补充一条同源误写**：**`setStableScale` 同样不是官方的 step 动作**——官方 Specification 页与 canary 特性页都只有 `setCanaryScale`。**调整 Stable 副本数的官方机制是 `strategy.canary.dynamicStableScale`（v1.1 起），它是策略字段而非 step 动作**；与之配套的 `abortScaleDownDelaySeconds`（默认 30s）控制中止后 Canary RS 的保留时长，`scaleDownDelaySeconds`（默认 30s）控制发布完成后旧 RS 的缩容延迟。

> **数据纪律**：本篇以官方文档为准。官方 Specification 页列出的 canary step 动作是 `setWeight`、`pause`、`setCanaryScale`、`replicaProgressThreshold`、`plugin`、`setHeaderRoute`、`setMirrorRoute`、`analysis`、`experiment`。**`spec.scaleDown` 确实存在，但它是 `spec` 顶层字段（取值 `never`/`onsuccess`/`progressively`），用于 Deployment→Rollout 迁移时缩容原 Deployment，与 canary step 无关。**

#### 2.3.1 setWeight —— 权重到底切在哪一层

```yaml
steps:
  - setWeight: 20      # 20% 流量给金丝雀
  - pause:
      duration: 10m
  - setWeight: 50
```

**这是全篇最容易误解的地方，必须分两种情况讲**：

| 情况 | `setWeight` 的实际效果 | 官方原文依据 |
|---|---|---|
| **配了 `trafficRouting`** | **真实流量按权重分配**（Istio 的 `VirtualService` weight / ALB 的 TargetGroup Weight） | "the traffic split is controlled by the traffic router" |
| **没配 `trafficRouting`** | **只是"尽力近似"**：靠调整新旧 ReplicaSet 的**副本数比例**来近似 | "If the canary Rollout does not use traffic management, the Rollout makes a **best effort attempt** to achieve the percentage listed in the last `setWeight` step" |

**没有 trafficRouting 时的近似算法（官方举例）**：共 10 个副本时——
- `setWeight: 10` → Canary 1 个、Stable 9 个（1/10 正好）
- `setWeight: 41` → Canary **4** 个、Stable **6** 个（因为 4/10 = 40% 比 5/10 = 50% 更接近 41%）

**这带来两个直接后果**：

1. **副本数少的时候，权重是"毛的"**。3 副本时，`setWeight: 20` 只能得到 0 个或 1 个 Canary（1/3 = 33%）。**官方建议：需要在副本数较少时实现更精细的比例，就用 `trafficRouting`。**
2. **没配 trafficRouting 时，`setWeight` 的"百分比"和用户实际感受到的"比例"可能差很远**（尤其在有长连接、有会话保持的场景）。

> **这就是第 6 节"trafficRouting 未配导致权重不生效"那条坑的根源**：你以为在放 20%，实际可能一个流量都没切过来（因为 1 副本 × 20% ≈ 0，直接被四舍五入成 0）。

**副本数与权重的联动规则**（官方原文）：

> "The new ReplicaSet's replica count is equal to the latest SetWeight step percentage multiplied by the total replica count of the Rollout."

即：**Canary 副本数 = 最新 setWeight 百分比 × spec.replicas**。

**同时官方还说明**：使用流量路由时，**默认 Stable ReplicaSet 在整个更新期间保持 100% 扩容**。这意味着**资源成本 = 100% stable + canary 比例的副本**（不是 2 倍，但也多出一截）。

#### 2.3.2 pause —— 人工闸门

```yaml
steps:
  - pause:
      duration: 10m     # 定时自动继续
  - pause: {}           # 无限期暂停，等人工 promote
```

| 写法 | 语义 | 谁来放行 |
|---|---|---|
| `pause: {duration: 10m}` | 等待指定时长后自动继续 | **时间**（到点自动走） |
| `pause: {}` | **无限期暂停**，直到 `PauseCondition` 被移除 | **人**（`kubectl argo rollouts promote <rollout>`） |

**`duration` 支持的时间单位**（官方原文）：`s`、`m`、`h`；**不写单位时默认按 `s`（秒）解析**。

> ⚠️ **"`pause: {duration: 10}`" 是 10 秒不是 10 分钟**——这是官方明确写出的默认单位，很多人在这里踩坑。

**pause 与 `progressDeadlineSeconds` 的交互**（官方原文）："**progress will not be estimated during the time a rollout is paused**"。**也就是说暂停期间不计入发布超时**——这对"晚上挂个 pause 等第二天审批"是好事。

#### 2.3.3 setCanaryScale 与 dynamicStableScale —— 副本数与流量权重是两件事

**`setWeight` 控制流量，`setCanaryScale` 控制副本数。两者可以独立调整。**

```yaml
steps:
  # 显式指定 Canary 副本数
  - setCanaryScale:
      replicas: 3

  # 用 spec.replicas 的百分比
  - setCanaryScale:
      weight: 25

  # 恢复默认行为（副本数跟随当前流量权重）
  - setCanaryScale:
      matchTrafficWeight: true
```

| 字段 | 含义 |
|---|---|
| `setCanaryScale.replicas` | Canary ReplicaSet 的**绝对副本数** |
| `setCanaryScale.weight` | Canary 副本数 = `spec.replicas` 的百分比 |
| `setCanaryScale.matchTrafficWeight` | 恢复默认：**副本数跟随当前 setWeight** |
| `strategy.canary.dynamicStableScale` | **不是 step 动作**，是 canary 策略字段（v1.1+）。置 `true` 后 Stable RS 随 Canary 权重上升而自动缩容；默认 `false`（Stable 保持 100%）。**代价：发布期间总副本数翻倍，节点余量不足时可能调度不上；中止回滚时 Canary 也会跟着缩** |

**⚠️ 官方原文的限制条件**：

> "Setting canary scale is only available when using the canary strategy **with a traffic router**…"

**即 `setCanaryScale` 需要配合 `trafficRouting` 使用。**

**它的正确用法（官方举例）**：当流量权重和副本数需要脱钩时——比如**只有 1 个 Canary Pod，但希望它承接 90% 流量**（做压测或验证），就显式把副本数压到 1、权重放到 90。

> ⚠️ **官方明确警告的反模式**：10 个副本、只有 1 个 Canary Pod，却给它发 90% 流量 —— **这个 Pod 会被打挂**，然后你会误判成"新版本性能不行"。**流量权重和副本数必须匹配考虑。**

#### 2.3.4 analysis —— 把可观测性接进发布流程

**这是"自动化灰度"的关键**。`analysis` 步骤让 Rollout 在继续放量之前，**自动跑指标查询并判定**：

```yaml
steps:
  - setWeight: 20
  - analysis:
      templates:
        - templateName: success-rate
  - setWeight: 50
```

**没有 analysis 时**，流程是"放 20% → **人**看一眼监控 → 点 promote"——**这一步靠人不靠机器**。
**有 analysis 时**，流程是"放 20% → **机器**查 Prometheus → 达标就自动继续 → 不达标就 abort"。

**这才是渐进式交付真正的价值点**：不是"多放了几步"，而是**发布决策由数据驱动，而不是由"我觉得看起来没问题"驱动**。

**analysis 的三种放置位置（含义完全不同）**：

| 位置 | 形式 | 语义 |
|---|---|---|
| **steps 内联** | `steps[].analysis` | **该步骤执行时同步跑一次分析**，分析不通过 → 该步骤失败 |
| **canary 策略级（后台分析）** | `spec.strategy.canary.analysis` | **发布全程在后台跑**，不成功 → **整个 Rollout 被 abort**（官方原文："If the analysis is unsuccessful the rollout will be aborted"） |
| **蓝绿的前后置分析** | `prePromotionAnalysis` / `postPromotionAnalysis` | 见 2.6 |

**`count` 的特殊语义（官方原文，容易踩）**：

| `count` 值 | 后台分析（`canary.analysis`） | steps 内联分析 |
|---|---|---|
| 不写 | 持续运行，直到被停止或失败 | 只测一次（未写 `interval` 时） |
| `0` | **运行到 Rollout 执行结束** | **不会执行该分析**（直接跳过！） |
| `n` | 测 n 次 | 测 n 次 |

> ⚠️ **`count: 0` 在 steps 内联分析里等于"这个分析根本不跑"**。**而且没写 `successCondition` 也没写 `failureCondition` 的 metric，AnalysisRun 结果是 `Inconclusive`（结论不定）**——**这会让发布卡在"不知道该继续还是该停"的状态**。详见第 6 节"Analysis 查询无数据导致判定失败"。

#### 2.3.5 experiment —— A/B 实验旁路

```yaml
steps:
  - experiment:
      duration: 10m
      templates:
        - name: baseline
          specRef: analysis-templates/baseline
      weight: 20
```

| 字段 | 含义 |
|---|---|
| `experiment.duration` | 实验持续时长 |
| `experiment.templates` | 实验用的分析模板 |
| `experiment.service` | 实验使用的 Service |
| `experiment.weight` | 实验流量的权重 |
| `experiment.analyses` | 关联的分析（`name` + `templateName`） |

**experiment 与 analysis 的本质区别**：

| | experiment | analysis |
|---|---|---|
| **做什么** | **旁路起一个临时版本**，接一部分流量做对照 | **查指标判定成败** |
| **产出** | 流量分组对比数据 | 一个"继续/停止"的决策 |
| **用于** | A/B 测试、多版本对比 | 发布门禁 |

#### 2.3.6 setHeaderRoute / setMirrorRoute —— 定向与镜像

```yaml
steps:
  - setHeaderRoute:
      name: 'set-header-1'
      match:
        - headerName: Custom-Header1
          headerValue:
            exact: Mozilla
        - headerName: Custom-Header2
          headerValue:
            prefix: Mozilla
        - headerName: Custom-Header3
          headerValue:
            regex: Mozilla(.*)
  - pause: {}
  - setHeaderRoute:
      name: 'set-header-1'      # ← 只给 name = 禁用这条 header 路由
```

```yaml
steps:
  - setMirrorRoute:
      name: mirror-route
      percentage: 35
      match:
        - method:
            exact: GET
          path:
            prefix: /
```

| 动作 | 作用 | 匹配方式 |
|---|---|---|
| `setHeaderRoute` | **按请求头**把匹配的流量定向到金丝雀 | `exact` / `regex` / `prefix`（每个 header 只能选一种） |
| `setMirrorRoute` | **镜像**流量到金丝雀（**镜像请求的响应不返回给原用户**），用于在真实流量上试跑新版本 | 同上 + `percentage` |

**匹配逻辑（官方原文）**：**同一个 `match` 块内的多个条件是 AND；多个 `match` 块之间是 OR。**

**只写 `name` 不写 `match` = 禁用这条路由**。

### 2.4 trafficRouting：三条实现路线

**`trafficRouting` 是 `spec.strategy.canary` 下的配置字段**，官方 Specification 页列出的内置 provider 字段有：`istio`、`nginx`、`alb`、`smi`（另有 `managedRoutes`、`maxTrafficWeight`）。

官方 Traffic Management 概览页列出的 provider 更多（AWS ALB Ingress Controller、Ambassador、APISIX、Google Cloud、Gateway API、Kong、Nginx Ingress Controller、SMI、Traefik 等）。

**三条主路线的取舍**（这是本篇最实用的部分）：

| 路线 | 需要什么 | 改什么来实现权重 | 成本 | 国内/本地民企可行性 |
|---|---|---|---|---|
| **Istio** | 装 Istio + 预建 `VirtualService`（+ subset 模式要 `DestinationRule`） | 改 `VirtualService.spec.http[].route[].weight` | **最高**（引入服务网格） | 中（Istio 国内用得多，但运维成本高） |
| **Gateway API** | 装 Gateway API CRD + **第三方插件**（`argoproj-labs/rollouts-plugin-trafficrouter-gatewayapi`） | 由插件改 `HTTPRoute` 的加权后端 | 中 | **高**（Gateway API 是社区通用方向，实现多） |
| **ALB 注解方式** | Ingress Controller（AWS ALB Controller / 兼容 `alb.ingress.kubernetes.io` 前缀的实现） | **改 Ingress 注解** `alb.ingress.kubernetes.io/actions.<svc>` | **最低**（不引入网格） | **最高**——**云上 ALB/CLB 是现成的** |

#### 2.4.1 Istio（host-level 与 subset-level）

**Host-level（两个 Service）**：

```yaml
strategy:
  canary:
    canaryService: canary-svc
    stableService: stable-svc
    trafficRouting:
      istio:
        virtualService:
          name: rollout-vsvc
          routes:
            - primary
```

**Subset-level（一个 Service + DestinationRule，官方原文标注自 v1.0 起提供）**：

```yaml
strategy:
  canary:
    trafficRouting:
      istio:
        virtualService:
          name: rollout-vsvc
          routes:
            - primary
        destinationRule:
          name: rollout-destrule
          canarySubsetName: canary
          stableSubsetName: stable
```

| Istio 字段 | 对应到 Istio 资源的哪一处 |
|---|---|
| `virtualService.name` | `VirtualService.metadata.name`（**跨命名空间要写成 `name.<ns>`**） |
| `virtualService.routes` | `VirtualService.spec.http[].name`（**只有一个 http route 时可省略**） |
| `destinationRule.name` | `DestinationRule.metadata.name` |
| `canarySubsetName` | `VirtualService` 里 canary 的 `destination.subset` + `DestinationRule` 里的 subset `name` |
| `stableSubsetName` | 同上，stable |

**Istio 前置要求（官方原文）**：

1. `VirtualService` **必须由用户预先部署**——Argo Rollouts **不创建、不拥有**它，只改它里面受管路由的权重。
2. `VirtualService` 至少有一个 http route，且**必须有两个 destination**：一个指向 canary、一个指向 stable。
3. **初始权重建议 stable=100、canary=0**——因为官方原文明确写了 "**Istio requires that all weights add to 100**"。
4. **只有进入 service mesh 的流量才遵循 Rollout 策略**。带 `sidecar.istio.io/inject: "false"` 标签未注入 sidecar 的 Pod **会继续走默认 K8s 流量路由** → **权重"看起来没生效"**。这是排障必查项。
5. subset 模式下，`DestinationRule` 的 subset `labels` 会被控制器注入对应的 `rollouts-pod-template-hash`。

#### 2.4.2 Gateway API（第三方插件）

**Gateway API 不是内置 provider，而是走第三方插件机制**（官方原文："Argo Rollouts supports getting traffic router via 3rd party plugin system"，插件库为 `go-plugin`）。

**插件安装方式**（官方原文，ConfigMap `argo-rollouts-config`）：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: argo-rollouts-config
data:
  trafficRouterPlugins: |-
    - name: "argoproj-labs/rollouts-plugin-trafficrouter-gatewayapi"
      location: "https://github.com/argoproj-labs/rollouts-plugin-trafficrouter-gatewayapi/releases/download/<version>/plugin-linux-amd64"
      sha256: "08f588b1c799a37bbe8d0fc74cc1b1492dd70b2c"
      headersFrom:
        - secretRef:
            name: secret-name
```

| 字段 | 含义 |
|---|---|
| `trafficRouterPlugins[].name` | 插件名，**必须与插件自己查找配置用的名字一致**（官方注释原文） |
| `trafficRouterPlugins[].location` | 支持 `http(s)://` 与 `file://` |
| `trafficRouterPlugins[].sha256` | 可选的校验和 |
| `trafficRouterPlugins[].headersFrom[].secretRef.name` | 下载时带的 Header（如私有仓库的 Authorization） |

**⚠️ 两条官方警告（必须知道）**：

1. **"The rollouts controller will not start if it can not download or find the plugin executable."** —— **插件下载不到，controller 直接起不来**，整个集群的渐进式发布全停。
2. **"Argo Rollouts will download the plugin at startup only once but if the pod is deleted it will need to download the plugin again on next startup."** —— **插件托管在 HTTP 服务上，那个服务挂了，controller 重启后就起不来了**。

> **这就是"渐进式交付组件自己成为单点"的真实案例**。用 Gateway API 插件时，**插件的可用性和分发方式必须纳入监控**。

#### 2.4.3 ALB 注解方式（本地民企更可能走这条）

**机制**：Argo Rollouts controller 在 Rollout 更新期间，**向目标 Ingress 注入并动态更新注解**：

```
alb.ingress.kubernetes.io/actions.<SERVICE-NAME>
```

这个注解里是一个 **AWS Load Balancer Controller 能识别的 JSON `forward` action**，把流量按权重分给 canary 和 stable 两个 Service。

**配置**（官方原文）：

```yaml
strategy:
  canary:
    canaryService: canary-service
    stableService: stable-service
    trafficRouting:
      alb:
        ingress: ingress            # 单个 Ingress
        # ingresses:                # 多个 Ingress（用了它就必须省略 ingress）
        # - ingress-1
        # - ingress-2
        rootService: root-service    # 可选，默认用 stableService
        servicePort: 443            # 必填
        annotationPrefix: alb.ingress.kubernetes.io   # 可选
        stickinessConfig:            # 可选
          enabled: true
          durationSeconds: 3600
```

| 字段 | 作用 | 写错的后果 |
|---|---|---|
| `alb.ingress` | 要注入 action 注解的 Ingress（**必填**） | 注解注不进去 → **权重完全不生效** |
| `alb.ingresses` | 控制多个 Ingress（**用了它就不能再写 `ingress`**） | 两个都写 → 配置无效 |
| `alb.servicePort` | Service 监听的端口（**必填**） | 端口填错 → action 里的 TargetGroup 端口错 → **502/连不上** |
| `alb.rootService` | Ingress rule 必须匹配的 Service（默认用 `stableService`） | 与 Ingress backend 不一致 → **注解 key 算错** |
| `alb.annotationPrefix` | 自定义注解前缀 | controller 用 `--annotations-prefix` 改了前缀而这里没同步 → **注解不被识别** |
| `alb.stickinessConfig` | Target Group 粘性会话 | 开了长连接的会话被随机切到新版本 → **会话数据丢失** |

**Ingress 侧的硬性要求（官方原文，两条最容易漏）**：

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ingress
  annotations:
    kubernetes.io/ingress.class: alb
spec:
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: root-service          # 必须匹配 alb.rootService 或 stableService
                port:
                  name: use-annotation     # ← 关键：这个端口名不能省
```

> ⚠️ **`port.name: use-annotation` 是官方明确要求的**。它告诉 ALB Controller：**"不要按普通 Service 端口转发，去看 Ingress 注解里的 forward action 决定流量目标和权重"**。**漏了这个端口名 → 权重 100% 不生效**（因为 Controller 根本没去看注解）。

**Ingress class 的匹配规则（官方原文）**：默认操作满足以下任一条件的 Ingress：
- 注解 `kubernetes.io/ingress.class: alb`
- 或 `spec.ingressClassName: alb`

用其他 class 时要给 controller 加 `--alb-ingress-classes` 参数（可传多次）。**要操作完全不带 class 标注的 Ingress，需传空字符串 `--alb-ingress-classes ''`。**

**注入的 action 长这样（官方原文示例）**：

```yaml
annotations:
  alb.ingress.kubernetes.io/actions.root-service: |
    {
      "Type":"forward",
      "ForwardConfig":{
        "TargetGroups":[
          {"Weight":10,"ServiceName":"canary-service","ServicePort":"80"},
          {"Weight":90,"ServiceName":"stable-service","ServicePort":"80"}
        ]
      }
    }
```

**还有一个 bookkeeping 注解**：官方原文提到 `rollouts.argoproj.io/managed-alb-actions`，用于记录哪些 action 由哪个 Rollout 管理（**多个 Rollout 可能引用同一个 Ingress**）。删除 Rollout 时，controller 会依据该注解**把 action 重置为只指向 stable Service 并给 100 权重**。

> **云厂商角度（面试要能说）**：Argo Rollouts 内置的 `alb` provider 是**对着 AWS ALB Ingress Controller 的注解约定实现的**（前缀 `alb.ingress.kubernetes.io`、action JSON 结构、端口名 `use-annotation`）。**阿里云 ACK 的 ALB Ingress Controller 沿用了 `alb.ingress.kubernetes.io` 这一注解前缀族**，因此在 ACK 上使用内置 `alb` provider 有落地可能，但**具体 action 注解的 JSON 结构与 `use-annotation` 端口名约定是否完全一致、controller 是否需要额外参数，须以你集群里实际安装的 controller 版本为准（⚠️ 待验证）**。
>
> **更稳的说法**（面试可用）："阿里云上做渐进式交付有三条路：① 引 Istio（最标准但成本最高）；② 走 Gateway API 插件（方向对，但插件下载失败会让 controller 起不来）；③ 用云厂商 ALB 的注解式切流（成本最低，但要确认 controller 与 Argo Rollouts 内置 alb provider 的注解约定是否一致，不一致就自己写一个 traffic router 插件）。**在没有 Istio 的团队里，我会先评估第 3 条，因为它不引入新的基础设施层。**"

**官方还给了两个"保险丝"开关（官方原文）**：

| 开关 | 作用 | 何时需要 |
|---|---|---|
| `--aws-verify-target-group`（v1.0 起为 `--alb-verify-weight`） | 校验 TargetGroup 的 Rules/Actions/TargetGroups 是否已反映注解里的权重 | **防止注解已更新但 LB 侧未生效时旧 RS 被过早缩容** |
| TargetGroup IP Verification（v1.1 起，仅 IP mode） | 切换 selector 后**等 Target Group 真的注册了新 Pod IP** 才继续 | 避免旧 RS 已缩容、TG 还指向旧 Pod IP → **HTTP 502** |

### 2.5 Analysis：AnalysisTemplate / ClusterAnalysisTemplate / AnalysisRun

**三个 CR 的分工**：

| CR | 作用域 | 作用 |
|---|---|---|
| **`AnalysisTemplate`** | **单个命名空间** | 定义可复用的指标判定规则（`spec.metrics`） |
| **`ClusterAnalysisTemplate`** | **集群级**（可在多个命名空间用） | 同上。**注意：生成的 `AnalysisRun` 仍在 `Rollout` 所在的命名空间里运行** |
| **`AnalysisRun`** | 单次执行 | 模板的实例，产生 `Successful` / `Failed` / `Inconclusive` 结果 |

**AnalysisTemplate 字段（官方原文）**：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: success-rate
spec:
  metrics:
    - name: success-rate
      interval: 5m
      count: 5
      successCondition: result[0] >= 0.95
      failureCondition: result[0] < 0.90
      failureLimit: 3
      provider:
        prometheus:
          address: http://prometheus.example.com:9090
          query: |
            sum(irate(
              istio_requests_total{reporter="source",destination_service=~"{{args.service-name}}"}[5m]
            ))
```

| 字段 | 含义 | 默认值 / 特殊值（官方原文） |
|---|---|---|
| `metrics[].name` | 指标名 | — |
| `metrics[].interval` | 测量间隔 | 页面**未给默认值** ⚠️ |
| `metrics[].count` | 测量次数 | 不写：内联分析只测一次；后台分析持续运行。**`0` 在后台分析里 = 跑到 Rollout 结束；在 steps 内联分析里 = 根本不执行** |
| `metrics[].successCondition` | 成功条件表达式 | 不写 → 可能 `Inconclusive` |
| `metrics[].failureCondition` | 失败条件表达式 | 不写 → 可能 `Inconclusive` |
| `metrics[].failureLimit` | 允许的失败次数 | **默认 `0`**（官方原文：0 表示"失败逻辑生效且不容忍任何失败"）；**`-1` 表示禁用** |
| `metrics[].initialDelay` | AnalysisRun 启动后延迟开始分析 | — |
| `metrics[].consecutiveSuccessLimit` | 连续成功多少次才算成功 | **默认 `0`**（0 = 不适用/禁用）；必须 > 0 才启用 |
| `metrics[].provider` | 指标提供者 | — |
| `provider.prometheus.address` | Prometheus 地址 | — |
| `provider.prometheus.query` | PromQL 查询 | — |

**`failureLimit` 与 `consecutiveSuccessLimit` 的组合规则（官方原文）**：

| 组合 | 行为 |
|---|---|
| 只写 `failureLimit: 0`（默认） | **第 1 次失败即判定 AnalysisRun 失败** |
| `failureLimit: -1` + `consecutiveSuccessLimit: 4` | 持续测到连续 4 次成功 |
| `failureLimit: 3` + `consecutiveSuccessLimit: 4` | 需 4 次连续成功，且总失败数不超过 3；**`failureLimit` 被违反时优先判失败** |

> ⚠️ **两者至少要有一个处于适用状态**，否则会产生**校验错误**。这是官方明确写出的约束。

**成功条件表达式的写法**：`result[0] >= 0.95` —— `result` 是一个数组，`result[0]` 是**最近一次查询的值**。

**引用方式与参数化**：

```yaml
steps:
  - analysis:
      templates:
        - templateName: success-rate
      args:
        - name: service-name
          value: my-app
```

| 字段 | 含义 |
|---|---|
| `analysis.templates[].templateName` | 引用的 AnalysisTemplate 名 |
| `analysis.templates[].specRef` | 引用其他命名空间的模板 |
| `analysis.args[].name` / `.value` | 模板参数 |
| `analysis.args[].valueFrom.podTemplateHashValue` | 取当前金丝雀的 pod template hash 作为参数 |
| `analysis.args[].valueFrom.fieldRef.fieldPath` | 从 Pod 字段取值 |

### 2.6 BlueGreen：previewService / activeService 与 selector 切换

**蓝绿的核心机制只有一句话**：

> **切换流量 = 修改 Service 的 `spec.selector`，让它指向新 ReplicaSet 的 Pod。**

**不搬 Pod、不改 DNS、不动 iptables**——所以切换是**原子的、秒级的**，回滚同理（把 selector 改回去）。

**Argo Rollouts 注入的标签**：`rollouts-pod-template-hash`。每个 Service 的 selector 会被加上对应版本的 hash：

```yaml
# canary-svc 的 selector 会被控制器改成：
selector:
  app: rollouts-demo
  rollouts-pod-template-hash: 7bf84f9696    # ← canary RS 的 hash
```

**完整流程（官方原文的 12 步）**：

```
① 稳定态：revision 1 同时被 activeService 和 previewService 指向
② 用户改 spec.template → 创建 revision 2 ReplicaSet（大小 0）
③ previewService → 指向 revision 2；activeService 仍指向 revision 1
④ revision 2 扩容到 previewReplicaCount 或 spec.replicas
⑤ revision 2 的 Pod 完全可用
⑥ 执行 prePromotionAnalysis
⑦ 按 autoPromotionEnabled / autoPromotionSeconds 决定：暂停 or 自动晋级
⑧ 晋级时 revision 2 扩容到 spec.replicas
⑨ activeService 的 selector → 指向 revision 2     ← 流量切换在这一步
⑩ 执行 postPromotionAnalysis
⑪ revision 2 标记为 stable，Rollout 视为 fully-promoted
⑫ 等待 scaleDownDelaySeconds → 缩容 revision 1
```

**关键字段**：

| 字段 | 作用 | 默认 / 备注 |
|---|---|---|
| `blueGreen.activeService` | **必填**，承载正式流量的 Service | 官方原文明确标注 mandatory |
| `blueGreen.previewService` | 可选，**始终指向最新 ReplicaSet** | 用于访问/测试新版本 |
| `blueGreen.previewReplicaCount` | 预览阶段的副本数 | 不设时**直接扩到 100%**。晋级前会再扩到 `spec.replicas`，**没到 `spec.replicas` 不切 selector** |
| `blueGreen.autoPromotionEnabled` | 是否自动晋级 | 官方原文：**不设时默认 `true`**；设为 `false` 则暂停等人工 `promote` |
| `blueGreen.autoPromotionSeconds` | 暂停后多少秒自动晋级 | **默认 `nil`（不设）**；**`autoPromotionEnabled: false` 时该字段被忽略** |
| `blueGreen.scaleDownDelaySeconds` | 切换后延迟缩容旧 RS 的时间 | **官方原文默认值：`30`** |
| `blueGreen.scaleDownDelayRevisionLimit` | 保留多少个旧 revision 不缩 | — |
| `blueGreen.prePromotionAnalysis` | **切换前**的分析 | 失败 → **不切流量，并中止 Rollout** |
| `blueGreen.postPromotionAnalysis` | **切换后**的分析 | 失败/报错 → Rollout 进入 `aborted`，**流量切回之前的 stable** |
| `blueGreen.activeMetadata` / `previewMetadata` | 给对应 RS 的 Pod 打标签 | — |

**`scaleDownDelaySeconds` 为什么必须有**（官方原文的解释，非常重要）：

> "Service selector 改变后，节点更新 iptables 并停止向旧 Pod 转发流量需要传播时间。"

**也就是说：selector 改了 ≠ 节点立刻停止转发。**如果立刻缩容旧 RS，会出现**请求被转发到已经终止的 Pod → 502**。延迟缩容就是给这个传播窗口留时间。

**蓝绿的一个重要限制（官方原文警告）**：

> "**ALB Ingress with Rollouts blue-green strategy is not supported without a chance of downtime.**"

**原因**：ALB Controller 不是以原子方式更新 target group——它可能先移除旧 Pod、再注册新 Pod，而新 Pod 完成初始健康检查前，stable target group 可能**暂时没有健康 Pod**。

> **这是一条必须知道的官方警告**：**"蓝绿 + ALB Ingress" 组合官方明确说会有停机风险**。要零停机，得换成 Istio 这类能原子切路由的实现。

### 2.7 回滚：abort / undo / retry / rollbackWindow

| 命令 | 作用 | 语义 |
|---|---|---|
| `kubectl argo rollouts abort <rollout>` | **中止当前进行中的发布** | 流量切回 stable；扩容 stable RS、缩容其他 RS。官方原文：analysis 不成功时**自动** abort |
| `kubectl argo rollouts undo <rollout>` | **撤销最近的更新** | 把期望状态恢复到之前的版本（**会重新走一遍发布流程**） |
| `kubectl argo rollouts retry <rollout>` | **重试**失败的步骤或失败的 AnalysisRun | 失败原因已修复后，**从失败点继续**，而不是重新创建完整发布 |
| `kubectl argo rollouts promote <rollout>` | 放行暂停中的发布 | 加 `--full` 会**跳过所有剩余步骤和分析** |
| `kubectl argo rollouts pause <rollout>` | 主动暂停 | 恢复用 `promote`（**官方 CLI 目录里没有单独的 `resume` 命令**） |
| `kubectl argo rollouts restart <rollout>` | 顺序重启所有 Pod | 官方原文：controller 确保所有 Pod 的 `creationTimestamp` >= `restartAt` |

**`abort` 之后的一个反直觉现象（官方原文）**：

> 即使 stable ReplicaSet 已经健康运行，Rollout **仍会被视为 `Degraded`**，因为**当前期望版本仍是 `red`**，而实际运行的是 `yellow`。

**修复方式**：把期望状态改回稳定版：

```bash
kubectl argo rollouts set image rollouts-demo rollouts-demo=argoproj/rollouts-demo:yellow
```

> **这是 GitOps 场景下最关键的一条**：在 ArgoCD 场景里，`abort` 之后**必须把 Git 也改回旧版本**，否则下一次 sync 会**再次把新版本推上去**，等于刚回滚完又被重新发布一遍。**"abort 只改集群状态，Git 没改"= 回滚没做完。**

**`rollbackWindow`（官方原文）**：用于**快速回退到之前部署过的版本**，结构是：

```yaml
rollbackWindow:
  revisions: 3
```

官方原文说明它默认**未设置（not set）**。

**`dynamicStableScale` 与 abort 的交互（官方原文）**：

| 场景 | 行为 |
|---|---|
| 不开 `dynamicStableScale` | stable RS 全程保持 100% → abort 后**立即**把流量切回 stable |
| 开 `dynamicStableScale` | stable RS 随 canary 权重上升而动态缩容 → abort 后 canary RS **随流量切回而动态缩容** |
| 开 `dynamicStableScale` + `abortScaleDownDelaySeconds: 600` | abort 后 canary RS 保持扩容 600 秒再缩 |

**`dynamicStableScale` 的官方版本要求**：**v1.1 及以后**。它的用途是**在副本数高、资源成本敏感时，避免 stable 全程 100%**。

### 2.8 渐进式交付与 GitOps 的分工

**这是整个 GitOps + 渐进式交付组合的核心，必须画清楚**：

```
┌─────────────┐         ┌──────────────┐         ┌────────────────────┐
│  Git 仓库    │  sync   │   ArgoCD     │  apply  │  K8s apiserver     │
│  (Rollout    │ ──────► │ (期望态管理者) │ ──────► │                    │
│   manifest) │         └──────────────┘         └─────────┬──────────┘
└─────────────┘                                             │
                                                              │ watch
                                                              ▼
                                                   ┌────────────────────┐
                                                   │ argo-rollouts     │
                                                   │ controller        │
                                                   │ (执行渐进逻辑)     │
                                                   └────────┬──────────┘
                                                            │
                                              ┌─────────────┼─────────────┐
                                              ▼             ▼             ▼
                                        ReplicaSet    VirtualService  AnalysisRun
                                        (新/旧版本)     / ALB 注解     (Prometheus)
```

**分工**：

| 组件 | 负责 | 不负责 |
|---|---|---|
| **ArgoCD** | 把 Git 里的 `Rollout` 对象同步到集群；**对账期望态** | **不执行**金丝雀步骤、不切流量、不跑分析 |
| **argo-rollouts controller** | 观察 `Rollout` 的 `spec.template` 变化 → 建新 RS → **按 steps 切权重、跑分析、暂停/晋级** | 不管"Git 里应该是什么" |

**三条必须知道的集成约束**：

| 约束 | 原因 | 后果 |
|---|---|---|
| **只有 `spec.template` 变化才触发新版本** | 官方原文："any change to the Pod template field (`spec.template`) results in a new version (i.e. ReplicaSet) to be deployed" | 改 `spec.replicas` **不会**触发新发布 |
| **ArgoCD 自动 sync 不会越过 `pause: {}`** | `pause: {}` 是无限期暂停，等 `promote` | **"GitOps 全自动"和"金丝雀人工放行"必须由外部系统衔接**（CI 在检测到 paused 后执行 promote） |
| **不要让 ArgoCD 同步 Rollout 生成的子资源**（ReplicaSet/Pod/AnalysisRun） | 两个控制器同时改同一资源 | **ArgoCD 回滚 controller 刚建的 RS → 资源树一直显示差异 → 发布状态反复变化** |

**推荐的集成配置**：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: my-app
  annotations:
    # 让 ArgoCD 同步时尊重 ignoreDifferences，避免回滚 controller 动态改写的字段
    argocd.argoproj.io/sync-options: RespectIgnoreDifferences=true
```

**`RespectIgnoreDifferences=true` 在 Rollout 场景的价值**：ArgoCD 可能把 controller 动态修改的字段（`status` 相关、动态生成的 RS 相关）视为差异并尝试回滚。**开了它，同步时这些字段保持现状。**

> ⚠️ **别把它当成万能药**：它解决的是"同步时不要覆盖被忽略的字段"，**不解决"ArgoCD 要不要帮你 promote"**。而且**必须去 UI 的 Diff 面板确认被忽略的确实是预期字段**，不要一上来就 `ignoreExtraneous` 整个 `spec.template`——**那会让发布配置的变化永远同步不下去**。

---

## 3. 动手

> **本节操作都在你自己的测试集群上做。** 本篇写作过程中未在你的环境实测，所有"看到什么算对"是判定标准，不是粘贴的终端输出。

### 3.1 准备：装 controller 与 CLI

Argo Rollouts 需要**两个东西**：
1. **集群里的 controller**（Deployment + CRD）——它执行渐进逻辑
2. **本地或跳板机上的 kubectl 插件**（`kubectl argo rollouts`）——它提供观察与操作命令

**看到什么算对**：
- `kubectl get crd | grep rollouts` → 能看到 `rollouts.argoproj.io`、`analysistemplates.argoproj.io`、`analysisruns.argoproj.io` 等 CRD
- `kubectl argo rollouts get rollout --help` → 不报错

### 3.2 最小金丝雀（不配 trafficRouting，先看机制）

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: rollouts-demo
spec:
  replicas: 5
  selector:
    matchLabels:
      app: rollouts-demo
  template:
    metadata:
      labels:
        app: rollouts-demo
    spec:
      containers:
        - name: rollouts-demo
          image: argoproj/rollouts-demo:red
          ports:
            - containerPort: 8080
  strategy:
    canary:
      steps:
        - setWeight: 20
        - pause: {}
        - setWeight: 50
        - pause: {}
        - setWeight: 80
```

**观察命令**：

```bash
kubectl argo rollouts get rollout rollouts-demo --watch
```

**看到什么算对**：
- `get rollout` 显示 `Strategy: Canary`，`Weight: 20`
- **两个 ReplicaSet 同时存在**（stable 5 个 + canary 1 个，因为 20% × 5 = 1）
- 状态显示 **Paused**，等 `promote`
- `kubectl argo rollouts get rollout rollouts-demo` 里能看到 `Steps` 进度

> **注意这个实验里权重是"近似"的**（没有 trafficRouting）。5 副本时 20% → 1 个 Canary Pod，恰好是 20%。**换成 3 副本时 20% 就只能得到 0 或 1 个（0% 或 33%）**——这是理解"为什么需要 trafficRouting"的最佳对照实验。

### 3.3 放行与中止

```bash
# 放行到下一步
kubectl argo rollouts promote rollouts-demo

# 跳过所有剩余步骤和分析（谨慎）
kubectl argo rollouts promote rollouts-demo --full

# 中止
kubectl argo rollouts abort rollouts-demo
```

**看到什么算对**：
- `promote` 后 weight 变成 50，再 `promote` 变成 80，最后自动完成（canary 变成 stable，旧的被缩容）
- `abort` 后 weight 回到 0，流量回 stable。**但 Rollout 状态仍是 `Degraded`**（因为期望版本还是 red）——这是官方明确说明的行为

### 3.4 加一个 Analysis（自动判定）

先写 Prometheus 查询（示例用 Istio 指标；换成你的实际指标）：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: success-rate
  namespace: default
spec:
  metrics:
    - name: success-rate
      interval: 1m
      count: 3
      successCondition: result[0] >= 0.95
      failureCondition: result[0] < 0.90
      failureLimit: 1
      provider:
        prometheus:
          address: http://prometheus.monitoring.svc.cluster.local:9090
          query: |
            sum(rate(http_requests_total{code=~"5..",service="rollouts-demo"}[1m]))
            /
            sum(rate(http_requests_total{service="rollouts-demo"}[1m]))
```

再挂到 steps 里：

```yaml
  strategy:
    canary:
      steps:
        - setWeight: 20
        - pause:
            duration: 2m
        - analysis:
            templates:
              - templateName: success-rate
        - setWeight: 80
```

**看到什么算对**：
- `kubectl get analysisrun` 出现对应的 AnalysisRun
- `kubectl argo rollouts get rollout rollouts-demo` 显示 analysis 步骤进度
- **查询返回空数据时**，AnalysisRun 状态是 `Inconclusive` 而不是 `Successful`（这是第 6 节那条坑的现场）

### 3.5 验证清单

| 检查项 | 怎么看 | 期望 |
|---|---|---|
| CRD 已注册 | `kubectl get crd` 后过滤 `argoproj` | 有 rollouts / analysistemplates / analysisruns |
| Canary 生效 | `kubectl argo rollouts get rollout rollouts-demo` | 显示 `Strategy: Canary` 和当前 weight |
| 双版本并存 | `kubectl get rs -l app=rollouts-demo` | **两个** ReplicaSet |
| 暂停可放行 | `kubectl argo rollouts promote rollouts-demo` | 前进到下一步 |
| 分析可判定 | `kubectl get analysisrun -o wide` | `Successful` / `Failed` / `Inconclusive` |
| 中止可回退 | `kubectl argo rollouts abort rollouts-demo` | 权重回 0，流量回 stable |

---

### 3.6 常用运维命令（Argo Rollouts 渐进式交付，按场景）

> **本节能做什么**：3.1–3.5 是"装 controller、跑金丝雀、放行/中止、接 Analysis"，本节是**做渐进式交付时每天敲的 Rollouts CLI 命令**——从看发布进度、手动放行/中止、调权重到分析判定与排障。这是配置管理岗在"零停机发布"场景下的实战面板。
> **环境基准**：本机实测 `kubectl-argo-rollouts v1.6.x`（插件式子命令 `kubectl argo rollouts`）。

#### ① Rollout 生命周期（get / promote / abort / pause / resume / restart）

```bash
kubectl argo rollouts get rollout rollouts-demo        # 看当前发布阶段、权重、两个 RS 状态
kubectl argo rollouts list                              # 看命名空间下所有 Rollout
kubectl argo rollouts promote rollouts-demo            # 放行：从当前步推进到下一阶段（金丝雀加流量）
kubectl argo rollouts abort rollouts-demo              # 中止：流量回退到 stable，权重归零（救火）
kubectl argo rollouts pause rollouts-demo              # 暂停在当前步（卡住人工确认点）
kubectl argo rollouts resume rollouts-demo             # 解除暂停，继续自动推进
kubectl argo rollouts restart rollouts-demo            # 触发一次全新的 Rollout（重启发布流程）
```

> **实战价值**：`promote` 是金丝雀"看着指标没问题，再放一点"的手动开关；`abort` 是发布翻车时的第一反应——比回滚 Deployment 更优雅，它直接把流量切回 stable。`pause`/`resume` 实现"自动化 + 人工卡点"的混合发布，是生产灰度常见姿势。

#### ② 流量与权重（set weight / abort / undo）

```bash
kubectl argo rollouts set weight rollouts-demo 30     # 手动把金丝雀流量权重设为 30%
kubectl argo rollouts set weight rollouts-demo 100    # 一步全量（跳过剩余步骤，谨慎）
kubectl argo rollouts abort rollouts-demo             # 异常时直接回退 stable
kubectl argo rollouts undo rollouts-demo              # 回滚到上一个稳定 ReplicaSet
kubectl argo rollouts get rollout rollouts-demo -o yaml | grep -i weight   # 确认当前权重
```

> **实战价值**：`set weight` 在不依赖 Analysis 自动判定时，给你"手动控流量"的能力（适合小流量试水）。`undo` 与 `abort` 区别：`undo` 是切到上一个 RS（可能仍是金丝雀旧版），`abort` 是明确回 stable——救火时优先 `abort`。

#### ③ 分析判定（analysis / abort）

```bash
kubectl get analysisrun -o wide                       # 看所有 AnalysisRun 的判定结果
kubectl argo rollouts get rollout rollouts-demo       # 看 rollout 关联的 Analysis 状态
kubectl argo rollouts abort rollouts-demo             # Analysis 判 Failed 时自动/手动中止
kubectl logs -n argo-rollouts deploy/argo-rollouts                    # 看 controller 日志
kubectl describe analysisrun <name>                   # 看某次分析的失败原因（指标/阈值）
```

> **实战价值**：Analysis 是"发布能不能继续"的自动守门员。它判 `Failed` 时 Rollout 会自动 `abort`——这是"指标不达标就自动停发"的精髓。排障先看 `describe analysisrun` 的失败指标（错误率、延迟、QPS），比肉眼盯 dashbord 靠谱。

#### ④ 排障：卡在暂停 / 分析失败 / 流量不切

```bash
kubectl argo rollouts get rollout rollouts-demo       # 第一眼：卡在 Paused? Analysis 红? Weight 对?
kubectl describe rollout rollouts-demo                # 看 status.conditions / message 报错
kubectl get pods -l app=rollouts-demo -L rollouts-pod-template-hash   # 确认新旧两个 RS 的 Pod 都在
kubectl argo rollouts abort rollouts-demo             # 发布翻车先回 stable
kubectl argo rollouts restart rollouts-demo           # 想彻底重来一发新的 Rollout
```

> **实战价值**：发布"卡住不动"最常见两类——① `Paused` 等人工 `promote`（以为自动其实在等）；② Analysis 一直 `Inconclusive`（指标源没数据）。先看 `get`/`describe` 的状态字段，别急着删 RS。`abort` → 恢复 stable → 查因，是渐进式交付的通用救火顺序。

---

## 4. 逐字段表

### 4.1 Canary steps 动作表

| 动作 | 含义 | 典型用法 | 关键限制 / 写错的后果 |
|---|---|---|---|
| **`setWeight`** | 把指定比例的流量分给金丝雀（0-100） | `setWeight: 20` → 20% 流量进新版本 | **没配 trafficRouting 时只是"尽力近似"**（靠副本数比例）；**副本数少时权重失真**（3 副本放 20% ≈ 0 或 33%） |
| **`pause`** | 暂停步骤流 | `pause: {duration: 10m}` 定时放行；`pause: {}` 等人工 `promote` | **`duration` 不写单位按秒解析**（`duration: 10` = 10 秒）；`pause: {}` 忘记 promote → **发布永远卡住**；暂停期间**不计入 `progressDeadlineSeconds`** |
| **`setCanaryScale`** | 显式设置金丝雀副本数 | `replicas: 3` / `weight: 25` / `matchTrafficWeight: true` | **只在配了 traffic router 时可用**；副本数与流量权重脱钩 → **新版本 Pod 被流量打挂** |
| **`strategy.canary.dynamicStableScale`** | **策略字段**（v1.1+），非 step 动作。置 `true` 让 Stable RS 随 Canary 权重上升自动缩容 | 高副本数场景省资源（发布期总副本数不翻倍） | 节点余量不足时可能调度不上；**中止回滚时 Canary 也会跟着缩** |
| **`analysis`** | 跑一次指标分析并判定 | `templates: [{templateName: x}]` + `args` | `count: 0` 在 steps 内联里 = **不执行**；无 success/failure 条件 → `Inconclusive` → **卡住** |
| **`experiment`** | 旁路起临时版本做 A/B | `duration` + `templates` + `weight` | 旁路版本占资源；**实验版本与 canary 版本会争流量** |
| **`setHeaderRoute`** | 按请求头定向流量到金丝雀 | `match: [{headerName, headerValue:{exact/regex/prefix}}]` | 只写 `name` 不写 `match` = **禁用该路由**（不是"配了个空的"） |
| **`setMirrorRoute`** | 镜像流量到金丝雀（响应不返回原用户） | `percentage: 35` + `match` | **镜像请求会双倍打到新版本**，新版本压力是真实流量的 1.35 倍 |
| **`replicaProgressThreshold`** | 副本就绪进度阈值 | `type` + `value` | 阈值过严 → 迟迟不推进 |
| **`plugin`** | 调外部插件执行步骤 | `name` + `config` | 插件不可用 → 该步骤失败 → 整个发布卡住 |

### 4.2 Rollout strategy 对照表

| 字段 | 归属 | 类型 | 必填 | 含义 | 默认 / 写错的后果 |
|---|---|---|---|---|---|
| `spec.replicas` | spec | int | | 期望 Pod 数 | **默认 1**（官方原文） |
| `spec.revisionHistoryLimit` | spec | int | | 保留的旧 ReplicaSet 数 | **默认 10**（官方原文） |
| `spec.selector.matchLabels` | spec | map | ✅ | 选 Pod；**必须与 template labels 匹配** | 不匹配 → Pod 不被接管，发布"什么都不发生" |
| `spec.template` | spec | object | ✅ | Pod 模板（**与 Deployment 相同**） | 与 `workloadRef` **互斥** |
| `spec.workloadRef` | spec | object | | 引用已有工作负载提供 Pod 模板 | 与 `template` **互斥**（官方原文明确） |
| `spec.scaleDown` | spec | enum | | 迁移后原 Deployment 怎么缩 | `never` / `onsuccess` / `progressively`；官方原文：**Rollout 失败时 Deployment 会重新扩容** |
| `spec.progressDeadlineSeconds` | spec | int | | 更新无进展的超时 | **默认 600s**（官方原文）。**暂停期间不计时** |
| `spec.progressDeadlineAbort` | spec | bool | | 超时后是否中止更新 | **默认 false**（官方原文） |
| `spec.minReadySeconds` | spec | int | | Pod 保持 Ready 且容器不崩的最短时间才算可用 | **默认 0** |
| `spec.paused` | spec | bool | | 手动暂停 | **默认 false**；创建时就是 true → **不会自动从 0 扩容**（官方原文） |
| `spec.rollbackWindow.revisions` | spec | int | | 快速回退窗口保留的 revision 数 | **默认未设置** |
| `spec.restartAt` | spec | time | | 顺序重启所有 Pod 的时间戳 | 由 `kubectl argo rollouts restart` 写入 |
| `spec.analysis.successfulRunHistoryLimit` | spec | int | | 保留的成功 AnalysisRun 数 | **默认 5** |
| `spec.analysis.unsuccessfulRunHistoryLimit` | spec | int | | 保留的失败 AnalysisRun 数 | **默认 5**（失败阶段含 `Error`/`Failed`/`Inconclusive`） |
| `strategy.canary.canaryService` | canary | string | 配 trafficRouting 时必填 | 指向金丝雀 RS 的 Service | 写错 → **流量切不到新版本** |
| `strategy.canary.stableService` | canary | string | 配 trafficRouting 时必填 | 指向稳定版 RS 的 Service | 写错 → **回滚也切不回去** |
| `strategy.canary.steps` | canary | list | | 步骤序列 | **省略 steps → 行为退化成 rolling update**（官方原文） |
| `strategy.canary.trafficRouting` | canary | object | | 流量路由配置（**provider 字段**：`istio`/`nginx`/`alb`/`smi`/插件） | 不配 → **权重只近似生效**；provider 配错 → **权重完全无效** |
| `strategy.canary.maxSurge` | canary | int/str | | 为达到目标比例最多额外创建几个副本 | **默认 `"25%"`**（官方原文） |
| `strategy.canary.maxUnavailable` | canary | int/str | | 更新期间最多几个 Pod 不可用 | **默认 `25%`**；**`maxSurge: 0` 时它不能为 0** |
| `strategy.canary.dynamicStableScale` | canary | bool | | stable 随 canary 权重动态缩容 | **v1.1+**；开了之后 abort 时 canary 会动态缩容 |
| `strategy.canary.abortScaleDownDelaySeconds` | canary | int | | abort 后 canary 保持扩容的时长 | 不设 → **abort 后 canary 立即缩容**，无法保留现场 |
| `strategy.canary.analysis` | canary | object | | **后台分析**（发布全程跑） | 失败 → **整个 Rollout 被 abort** |
| `strategy.canary.scaleDownDelaySeconds` | canary | int | | 切换后延迟缩容旧 RS | 不设 → **旧 Pod 被立刻缩掉 → 可能 502** |
| `strategy.canary.minPodsPerReplicaSet` | canary | int | | 每个 RS 的最小 Pod 数 | — |
| `strategy.canary.canaryMetadata` / `stableMetadata` | canary | object | | 给对应 RS 的 Pod 打标签 | 标签写错 → **配套的 Service/Namespace 选择器匹配不上** |
| `strategy.blueGreen.activeService` | blueGreen | string | ✅ | 承载正式流量的 Service | 官方原文明确 mandatory；写错 → **流量全断** |
| `strategy.blueGreen.previewService` | blueGreen | string | | 预览 Service | **始终指向最新 RS**；不配 → 没有预览环境 |
| `strategy.blueGreen.previewReplicaCount` | blueGreen | int | | 预览阶段副本数 | 不设 → **直接扩到 100%**（成本翻倍） |
| `strategy.blueGreen.autoPromotionEnabled` | blueGreen | bool | | 是否自动晋级 | **默认 true**（官方原文）；设 false → **必须人工 promote** |
| `strategy.blueGreen.autoPromotionSeconds` | blueGreen | int | | 暂停后多少秒自动晋级 | **默认 nil**；**`autoPromotionEnabled: false` 时被忽略** |
| `strategy.blueGreen.scaleDownDelaySeconds` | blueGreen | int | | 切换后延迟缩容旧 RS | **官方原文默认值：30** |
| `strategy.blueGreen.prePromotionAnalysis` | blueGreen | object | | **切换前**的分析 | 失败 → **不切流量 + 中止** |
| `strategy.blueGreen.postPromotionAnalysis` | blueGreen | object | | **切换后**的分析 | 失败 → **`aborted` + 流量切回旧版** |

---

## 5. 横向对比

### 5.1 Deployment 滚动 vs Canary vs 蓝绿

| 维度 | Deployment 滚动 | Canary | 蓝绿 |
|---|---|---|---|
| **小流量验证** | ❌ **不存在这一步** | ✅ 核心能力 | ✅ 通过 pre/post 分析 |
| **流量比例精确度** | 靠副本数，间接 | 配 trafficRouting 后**精确**；不配则近似 | **不适用比例**（整体切） |
| **回滚时延** | 慢（再滚一轮） | **快**（权重归零/abort） | **最快**（selector 改回） |
| **峰值资源** | 低 | 中（stable 全量 + canary 比例） | **高（双倍）** |
| **对基础设施的要求** | 无 | **需要流量路由能力** | 需要双倍容量 + 一个 Service |
| **实现复杂度** | 最低 | 中-高 | 中 |
| **适合业务** | 后台任务、可容忍短暂不一致 | **对外 API、核心链路** | 需原子切换 + 资源充足 |
| **不适用** | 高风险变更 | 团队没有流量路由能力 | **资源紧张**（扩不动就是灾难） |

### 5.2 金丝雀 vs 蓝绿 vs A/B

| 维度 | 金丝雀 | 蓝绿 | A/B 测试 |
|---|---|---|---|
| **目标** | **降低发布风险**（验证新版本） | **降低切换风险**（切/回都原子） | **验证产品效果**（长期对照） |
| **流量形态** | **逐步放大**，最终 100% | **一次性切**，0% 或 100% | **长期固定分桶**（如 50/50 不变） |
| **持续时间** | 一次发布期间 | 一次发布期间 | **持续数天/数周** |
| **结束条件** | 全量后旧版下线 | 切完后旧版延迟下线 | **没有"结束"**，两版长期并存 |
| **回滚代价** | 低（调权重） | 低（改 selector） | **高**（要重做实验、数据难比较） |
| **资源成本** | 中 | 高（双倍） | 高（两套常驻） |
| **产出** | "这次发布是安全的" | "这次切换是成功的" | "A 版本比 B 版本好 X%" |
| **实现载体** | `setWeight` steps | `activeService`/`previewService` | `experiment` + `setWeight` 固定分桶 |

> **三者不是替代关系**：
> - **金丝雀回答"这次发布安全吗"**
> - **蓝绿回答"这次切换会不会断"**
> - **A/B 回答"这个功能对用户更好吗"**
>
> **一个成熟的发布体系可以同时有**：蓝绿切换 + 金丝雀在切之前验证 + A/B 在切之后长期对照。**但对 10 几人团队的本地民企，先把金丝雀 + Analysis 这一条做扎实，价值远大于三条都上。**

### 5.3 手动验证 vs Analysis 自动判定

| 维度 | 手动验证（pause + 人看监控） | Analysis 自动判定 |
|---|---|---|
| **判定者** | 人 | **机器**（Prometheus 查询） |
| **判定时机** | 人的注意力决定的 | **固定的 interval 周期** |
| **可复现** | ❌（人当时看到了什么无从查证） | ✅（查询和结果都在 AnalysisRun 里） |
| **夜间/无人值守** | ❌ **卡住**（没人 promote） | ✅ 自动继续 |
| **指标口径** | 每人看的面板不一样 | **写在模板里，全团队统一** |
| **误判风险** | **看错面板 / 看漏了某项指标** | **指标选错 / 阈值不合理**（更隐蔽，但可复现可修） |
| **成本** | 高（人力占用） | 低（一次配好长期复用） |
| **失败表现** | 一直卡在 pause | **`abort` + 自动回滚**（可观测、可告警） |

> **关键认知**：Analysis 不是"替代人看监控"，是"**把人的判断固化下来**"。**它最大的价值是让发布在没人盯着的时候也能正确决策**——这恰恰是半夜发版最需要的。

---

## 6. 踩坑与排障

> **报错原文说明**：本篇未在你的环境实测，下表中的错误文本标注为「典型报错（来自官方 issue / 社区）」的，是公开讨论中反复出现的形态；标注「官方文档描述」的，是 Argo Rollouts 官方文档中明确写出的行为。**不臆造具体版本号与行号。**

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **配了 trafficRouting 但权重不生效**（流量全在老版本） | Rollout 显示 weight=20，但实际请求全打老版本 | ① 流量路由 provider 没真正接管（ALB 场景：Ingress 的 `port.name` 没写成 `use-annotation`，Controller 根本没去看 action 注解）；② Istio 场景：Pod 带 `sidecar.istio.io/inject: "false"` 未注入 sidecar，**不走 mesh 流量**；③ `routes` 与 `VirtualService.spec.http[].name` 不匹配 | ① ALB：把 Ingress backend 端口名改成 `use-annotation`，核对 `servicePort`；② Istio：去掉 `sidecar.istio.io/inject: "false"` 标签，确认 Pod 已注入 sidecar；③ 核对 route 名 | 发布前用**真实请求**验证一次分流（不同版本返回不同标识）；ALB 场景把 Ingress 的端口名写进模板而非手填 |
| **`pause` 忘记配置导致流程卡死** | Rollout 长期 `Paused`，`get rollout` 显示停在某一步 | ① `pause: {}` 无人 `promote`；② `pause: {duration: 10}` 期望 10 分钟实际只等 10 秒（**不写单位按秒解析**）；③ 用了 `resume` 命令——**官方 CLI 里没有单独的 resume，用 `promote`** | `kubectl argo rollouts promote <rollout>`；把 `duration` 写成带单位的 `10m` | `duration` 一律带单位；把 promote 接到 CI 上（检测 paused 后自动放行）；给"发布卡住超过 N 分钟"配告警 |
| **Analysis 查询无数据导致判定失败** | AnalysisRun 结果为 `Inconclusive`（**官方文档描述的三种结果值之一：Successful / Failed / Inconclusive，非报错原文**） | ① PromQL 查不到数据（指标名错 / 标签不匹配 / 时间窗口内没流量）；② **既没写 `successCondition` 也没写 `failureCondition`** → 官方文档明确说这种情况可能 `Inconclusive` | 先在 Prometheus UI 里把同一条查询跑通；补上 `successCondition`（或 `failureCondition`） | 分析模板上线前**必须用真实流量验证查询能返回数据**；给 `Inconclusive` 配告警（它比 Failed 更隐蔽） |
| **蓝绿切换后流量没变**（selector 写错） | `activeService` 的流量还是老版本 | ① `activeService` 的 selector 里 `rollouts-pod-template-hash` 没更新（**手写的 selector 覆盖了控制器注入的值**）；② `previewService` 与 `activeService` 指向同一个 RS；③ `activeService` 根本不存在或名字写错 | `kubectl get svc <active> -o jsonpath='{.spec.selector}'`，看 hash 是否指向新 RS；查 `kubectl argo rollouts get rollout` 的 selector 映射 | **Service 的 selector 里只写 `app` 这类稳定标签，把 hash 留给控制器注入**；**不要手写 `rollouts-pod-template-hash`**；切换后用 curl 验证返回的是新版本 |
| **canary 的 Pod 与 Deployment 混用导致双份控制器打架** | 资源树反复显示差异；发布状态反复变化；Deployment 和 Rollout 互相回滚对方的 RS | **两个控制器同时管同一批 Pod**（Deployment-controller 和 rollouts-controller），或 ArgoCD 在同步 Rollout 生成的 ReplicaSet | ① 让 Deployment 的 `replicas: 0`（或用 `spec.scaleDown: onsuccess` 让 Rollout 接管后缩掉 Deployment）；② 把 ArgoCD 的 Application 范围收窄到只同步 `Rollout` + `Service` + `Ingress`，**不同步 RS/Pod** | 迁移用官方给的 `workloadRef` + `spec.scaleDown` 路径，**别自己另起一个 Deployment 又留一个 Rollout**；ArgoCD 侧用 `resource.exclusions` 排除 Rollout 的子资源 |
| **渐进发布反而更慢**（分析步骤等待） | 整个发布耗时远超预期 | ① Analysis 的 `interval` × `count` 本身就要很久（例：`interval: 5m` × `count: 5` = 至少 25 分钟）；② 多个 analysis 步骤串行叠加；③ 每次 `pause` 的 duration 累加 | 算一遍总时长：`Σ(interval × count) + Σ(pause.duration)`；把不需要高置信度的判断放到最后一步 | **把"快速判定"（Pod 是否 Ready、错误率是否飙升）放前面，"精确判定"（长周期趋势）放后面或改为后台分析** |
| **abort 之后 Rollout 仍是 `Degraded`** | 状态为 `Degraded`，但流量已回老版本（**Argo Rollouts 的 Rollout 状态值，非报错原文**） | 官方原文：**期望版本仍是新版本，实际跑的是老版本**，所以状态是 Degraded | 把期望状态改回老版本：`kubectl argo rollouts set image <rollout> <container>=<old-image>`；**GitOps 场景下要改 Git** | 回滚流程写进 runbook：**abort → 改 Git → 确认 sync** 三步缺一不可 |
| **abort 之后又被自动发布了一遍** | abort 后过一会儿新版本又上去了 | **Git 里还是新版本**，ArgoCD 的下一次 sync 又把它推上来 | 先改 Git 回老版本，再 abort | 把"Git 回滚"和"集群 abort"绑成一步操作 |
| **undo 之后新版本又回来了** | 同上 | 同上（`undo` 只改集群状态） | 同上 | 同上 |
| **Gateway API 插件下载失败 → controller 起不来** | `argo-rollouts` controller Pod CrashLoop / 起不来 | 官方原文：**"The rollouts controller will not start if it can not download or find the plugin executable."** 插件托管在 HTTP 服务上，该服务不可达 | 把插件二进制**打进镜像**或用 `file://` 本地挂载；恢复插件托管服务 | **插件的可用性必须纳入监控**；插件源要有本地兜底 |
| **蓝绿切换后出现少量 502** | 切换瞬间有请求失败 | ① `scaleDownDelaySeconds` 太小，**节点上的转发规则还没传播完旧 Pod 就被缩了**；② ALB 场景：官方明确警告 **"ALB Ingress with Rollouts blue-green strategy is not supported without a chance of downtime"** | 调大 `scaleDownDelaySeconds`（蓝绿默认 30）；ALB 场景改用 Istio 等能原子切路由的实现 | 蓝绿必须显式设置 `scaleDownDelaySeconds`；**ALB + 蓝绿组合要提前告知业务方有停机窗口** |
| **发布卡在 `ProgressDeadlineExceeded`** | Rollout 状态里出现 reason 为 `ProgressDeadlineExceeded` 的 Condition（**官方文档描述的 reason 值，非报错原文**） | `progressDeadlineSeconds`（默认 600s）内没有进展 | 查卡在哪个 step；常见是 analysis 查询失败或 pause 无人放行 | 注意**暂停期间不计入这个超时**，所以"卡在 pause"不会触发它；真正卡住的是某个 step 内部 |
| **权重比例和实际流量对不上**（差很多） | `setWeight: 20` 但实测流量像 50% | ① 有会话保持/长连接，老连接留在老版本；② 没配 trafficRouting 时只是近似；③ 副本数太少导致量化误差 | 用无状态请求 + 响应体版本标识实测；副本数少时**必须配 trafficRouting** | 灰度验证脚本里**必须带版本标识**，用请求头区分来源版本 |
| **canary 副本被流量打挂后误判"新版本性能差"** | Canary Pod OOMKilled / CPU 打满 | 显式 `setCanaryScale` 让副本数与流量权重脱节（官方警告的例子：10 副本只给 Canary 1 个却发 90% 流量） | 恢复 `matchTrafficWeight: true`；或按流量比例配副本数 | 副本数与权重必须一起设计；`setCanaryScale` 只在有明确理由时用 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `argoproj.github.io/argo-rollouts/` | 概览、Getting Started（Basic Usage）、Features → Specification、Canary、Blue-Green、Analysis、Traffic Management（总览 + Istio + ALB + Plugins）各节 | ✅ 2026-10-24 |
| S2 | 官方 | `argo-cd.readthedocs.io/en/stable/` | ArgoCD 与 Rollout 的集成边界、sync options、Application 对 CR 的管理 | ✅ 2026-10-24 |
| S3 | 官方 | `kubernetes.io/docs/reference/using-api/` | Rollout 与 Deployment 的字段语义交叉验证（`selector`/`template`/`progressDeadlineSeconds`） | ✅ 2026-10-24 |
| S4 | 中文 | `kubernetes.io/zh-cn/docs/concepts/workloads/controllers/deployment/` | Deployment 滚动更新基线（`maxSurge`/`maxUnavailable`/`progressDeadlineSeconds` 默认 600） | ✅ 2026-10-24 |
| S5 | 中文 | `kubernetes.io/zh-cn/docs/concepts/services-networking/ingress/` | Ingress 的 `ingressClassName`、backend 端口名（ALB `use-annotation` 场景的 Ingress 结构） | ✅ 2026-10-24 |
| S6 | 官方 | `prometheus.io/docs/prometheus/latest/querying/basics/` | Analysis 中 PromQL 的写法与 `result[0]` 语义 | ✅ 2026-10-24 |
| S7 | 官方 | `prometheus.io/docs/introduction/overview/` | Prometheus 数据模型（Analysis 查询的数据来源） | ✅ 2026-10-24 |
| S8 | 中文 | `developer.aliyun.com` | Argo Rollouts / ACK 上的中文实践 | ✅ 2026-10-24 |
| S9 | 中文 | `cloud.tencent.com/developer` | 渐进式交付与金丝雀的中文实践 | ✅ 2026-10-24 |
| S10 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 上的 ALB Ingress 与托管 ArgoCD 形态 | ✅ 2026-10-24 |
| S11 | 云厂商 | `cloud.tencent.com/document/product/457` | TKE 上的多集群与渐进式交付形态 | ✅ 2026-10-24 |

> **待验证清单**（本篇明确不确定的点，均标注在正文对应位置）：
> 1. **阿里云 ACK 的 ALB Ingress Controller 与 Argo Rollouts 内置 `alb` provider 的注解约定是否完全一致**（action JSON 结构、`use-annotation` 端口名、`annotationPrefix`）——需在目标集群实测，或以 ACK 实际安装的 controller 版本文档为准。
> 2. **Gateway API 插件的 `trafficRouting` 具体配置字段**——官方 Plugins 概览页只给了插件安装方式（`trafficRouterPlugins`），**未给出 Rollout 侧的字段名与 `HTTPRoute` 权重改写细节**，需查 `argoproj-labs/rollouts-plugin-trafficrouter-gatewayapi` 仓库 README 与源码。
> 3. **`metrics[].interval` 的默认值**——官方 Analysis 页**未给出**，本篇不臆断。
> 4. **`spec.restartPolicy`**——官方 Specification 页的 `spec` 字段清单中**未出现**，本篇不写。
> 5. 第 6 节的报错文本均为**公开讨论中的典型形态**，本篇未在你的环境实测，请以实际事件原文为准。
>
> **非索引内 URL：0**（全部 URL 取自 [`../09-参考索引/README.md`](../09-参考索引/README.md) 中标 ✅ 的条目）。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入是"Git 里的一个 `Rollout` 对象"（由 ArgoCD 同步进来）；输出是"一个按 steps 分阶段放量、每阶段可自动判定、随时可切回的生产状态"。中间是 `argo-rollouts` controller：watch `Rollout` → 发现 `spec.template` 变了 → 建新 ReplicaSet → 逐步执行 steps（改流量权重 / 暂停 / 跑 Analysis / 旁路实验）→ 晋级为 stable → 延迟缩容旧 RS。
2. **能故意改坏再倒推吗？** —— 四个破坏实验：
   - **① 删掉 `trafficRouting` 配置，保留 `setWeight: 20`**，然后用带版本标识的请求实测流量比例 → 发现只有"近似"效果 → 倒推出"没有流量路由时权重靠副本数比例近似"，进而明白**为什么小副本数必须配 trafficRouting**。
   - **② 把 `pause: {duration: 10}` 改成 `pause: {duration: 10m}` 对比观察耗时** → 发现不写单位按秒解析 → 倒推出单位解析规则。
   - **③ 故意把 Analysis 的 PromQL 改成查一个不存在的指标** → 观察 AnalysisRun 状态是 `Inconclusive` 而不是 `Failed` → 倒推出"无数据 ≠ 失败，但也不等于成功"，**发布必须显式处理 `Inconclusive`**。
   - **④ 在蓝绿切换后立刻查 `activeService` 的 selector**，看 `rollouts-pod-template-hash` 是否已更新；再手工把它改回去 → 倒推出"蓝绿切换 = 改 Service selector"，以及"回滚也是改 selector，所以是秒级的"。
3. **能画上下游吗？** —— 画出 `Git → ArgoCD(sync 期望态) → apiserver → argo-rollouts controller → {ReplicaSet, Service selector / VirtualService weight / Ingress ALB 注解, AnalysisRun → Prometheus}`，并在每个环节标出"这一步挂了会看到什么"：ArgoCD 挂 → Rollout 对象不再更新但已在跑的发布继续；rollouts-controller 挂 → 发布**停在当前 step**（不会自动回退）；Prometheus 挂 → Analysis `Inconclusive` → **发布卡住**；流量路由 provider 挂 → 权重不生效但发布流程照走（**最隐蔽的一种**）。

---

**上一篇**：[`L5-ArgoCD-策略与多集群.md`](L5-ArgoCD-策略与多集群.md) · **下一篇**：[`../06-云原生中间件/L1-Operator模式与CRD.md`](../06-云原生中间件/L1-Operator模式与CRD.md)（渐进式交付解决的是"应用怎么发布"，Operator 解决的是"有状态中间件怎么运维"） · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md) · **相关**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md)
