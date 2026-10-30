# L8 · 弹性与 HPA（会算、会调、会排障）

> **本篇定位**：L7 讲清了"资源怎么分"，本篇讲清"**负载涨了资源怎么自动加、负载降了怎么自动减**"。HPA 是 K8s 里最容易被配错、也最容易被误判的机制——**它失败时不报错，只是安静地不干活**。本篇的目标是让你遇到"不扩容"时，**3 分钟内定位到断链的哪一环**。
> **前置**：L7 调度与 QoS（**必须理解 requests 是 HPA 的分母**，见 2.3）、L2 架构（知道 HPA 控制器跑在 controller-manager 里）。
> **学完能做什么**：① 画出 HPA 从 cAdvisor 到 Deployment.replicas 的完整 7 环链路，并说出每环缺失时的报错；② 手算扩容公式并解释为什么结果要向上取整；③ 解释"只写 limits 为什么 HPA 永远不扩"；④ 配置 `behavior` 让缩容更保守；⑤ 讲清 HPA 与 cluster-autoscaler 的两级联动关系；⑥ 用 PDB 保护缩容期的可用副本。
> **在 21 天里**：D2（Deployment + HPA）、D6（弹性与伸缩）。
> **环境/版本基准**：本地 kind 集群 `day2`（`kubectl v1.37.1` / `Server v1.37.0`）。**HPA 默认行为（缩容窗口 300s、扩容窗口 0s）以官方 `autoscaling/v2` API 参考为准**；本篇**未执行任何 kubectl 命令改动集群**，所有 yaml 为可自行验证的最小样例。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L8 弹性与 HPA
│
├─ 1. 概念
│   ├─ 1.1 一句话：HPA 是"会算的控制器"，不是"会监控的监控"
│   ├─ 1.2 没有弹性时的问题
│   └─ 1.3 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 HPA 的完整工作链路（7 环，每环缺失时的报错）
│   ├─ 2.2 第一环的真相：cAdvisor → kubelet /stats → metrics-server
│   ├─ 2.3 利用率的分母：requests 的三种写法与后果
│   ├─ 2.4 扩容公式与算例
│   ├─ 2.5 autoscaling/v2 的五种指标类型
│   ├─ 2.6 behavior 字段：scaleUp / scaleDown 的默认值与"故意不对称"
│   ├─ 2.7 kubectl top 与 HPA TARGETS 的关系
│   ├─ 2.8 经典陷阱：limits 太小 → 节流 → 扩了也没用（不是不扩）
│   ├─ 2.9 HPA 只管 Pod，不管节点：两级联动
│   ├─ 2.10 扩容延迟的四段耗时拆解
│   ├─ 2.11 PodDisruptionBudget：给缩容加一道保险
│   └─ 2.12 关键参数与默认值
│
├─ 3. 动手：跑通一个 CPU HPA
│   ├─ 3.1 前置检查：metrics-server 在不在
│   ├─ 3.2 最小 HPA
│   ├─ 3.3 制造负载并观察
│   └─ 3.4 验证清单
│
├─ 4. 资源清单逐字段
│   ├─ 4.1 HorizontalPodAutoscaler spec
│   ├─ 4.2 behavior / HPAScalingRules / HPAScalingPolicy
│   └─ 4.3 PodDisruptionBudget
│
├─ 5. 横向对比
│   ├─ 5.1 HPA vs VPA vs cluster-autoscaler
│   ├─ 5.2 CPU 指标 vs QPS 指标
│   └─ 5.3 Prometheus Adapter 在哪一环
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

> **HPA 是一个"会算的控制器"：它每隔 15 秒问一次"现在该有几个副本"，然后改 `Deployment.spec.replicas`。**

官方原文（Horizontal Pod Autoscaling） [S1]：

> Kubernetes implements horizontal pod autoscaling as a control loop that runs intermittently (it is not a continuous process). **The interval is set by the `--horizontal-pod-autoscaler-sync-period` parameter to the `kube-controller-manager` (and the default interval is 15 seconds).**

> Once during each period, the controller manager queries the resource utilization against the metrics specified in each HorizontalPodAutoscaler definition.

这句话里有三个关键信息，**每一个都是排障时的抓手**：

| 官方原文里的信息 | 排障含义 |
|---|---|
| *"control loop that runs intermittently (it is not a continuous process)"* | **它不是实时监控**。它每 15 秒算一次，中间有延迟。**"负载刚上去就不扩"不一定是 bug。** |
| *"--horizontal-pod-autoscaler-sync-period ... default interval is 15 seconds"* | **第一个延迟源：15 秒**。指标已经变了，HPA 最少也要等 15 秒才知道。 |
| *"queries the resource utilization against the metrics specified in each HPA definition"* | **它只查 HPA 里声明的那种指标**。没声明的指标它一概不看。 |

> **关键认知**：HPA 不是"监控告警系统"，是"**算副本数的控制器**"。它的输出只有一个数字：`replicas`。**它不负责发现"不够了"，它只负责回答"现在该是几个"。**

### 1.2 没有弹性时的问题

| 裸机/固定副本方式 | 痛点 | HPA 怎么解决 |
|---|---|---|
| 固定 3 个副本，压测时 CPU 打满 100% | 用户排队，只能加机器（分钟级甚至小时级） | 按利用率自动加副本（秒级决策） |
| 固定 10 个副本，低峰期 7 个闲着 | 机器钱白花 | 按利用率自动缩（但缩容故意很慢，见 2.6） |
| 半夜流量突增，扩容时新 Pod 拉镜像慢 | 用户已经流失了 | 提前预留 `minReplicas` 做"热备" |
| 半夜定时任务跑一下，副本数全天不变 | 手动改 yaml，凌晨 3 点改错了 | 让副本数跟着指标走，人不用醒着 |

> **关键认知**：弹性的价值不只是"省钱"，更是"**故障发生时用户不用等**"。**扩容速度是用户体验的一部分**——见 2.10 的四段耗时拆解。

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| HPA | 水平 Pod 自动扩缩容 | 调**副本数**（横向） |
| VPA | 垂直 Pod 自动扩缩容 | 调**单个 Pod 的资源规格**（纵向） |
| horizontal / vertical | 水平 / 纵向 | 加副本 / 加单机资源 |
| `metrics-server` | 指标服务器 | 采集并聚合 CPU/内存指标的集群插件 |
| cAdvisor | 容器资源采集器 | 每个节点上的 agent，采集容器用量 |
| `metrics.k8s.io` | 指标 API 组 | metrics-server 通过 APIService 注册进 apiserver 的**聚合 API** |
| APIService | API 聚合服务 | 让 `metrics.k8s.io/v1beta1` 挂在 apiserver 上 |
| utilization | 利用率 | **当前用量 ÷ requests**（资源指标下） |
| `averageUtilization` | 平均利用率 | 目标百分比，如 `60` = 60% |
| `averageValue` | 平均值 | 目标绝对值，如 `100m` |
| `stabilizationWindowSeconds` | 稳定窗口 | 在窗口内**取历史推荐值的最高/最保守值** |
| `selectPolicy` | 策略选择 | 多条 policy 之间取 `Max` 还是 `Min` |
| `policies` | 伸缩速率策略 | 限制"15 秒内最多加几个" |
| `scaleDown` | 缩容策略组 | 单独配置缩容的窗口与速率 |
| `scaleUp` | 扩容策略组 | 单独配置扩容的窗口与速率 |
| `flapping` | 抖动 | 副本数在阈值附近反复伸缩 |
| PDB | Pod 中断预算 | 保证自愿中断时可用副本数不低于阈值 |
| cluster-autoscaler | 集群自动扩缩容 | **加/减节点**，不是加/减 Pod |
| `minReplicas` | 最小副本数 | 默认 **1** |
| `maxReplicas` | 最大副本数 | **必填**，HPA 的天花板 |
| `tolerance` | 容差 | 比例在容差内**不做任何伸缩**，默认 **10%** |

---

## 2. 机制

### 2.1 HPA 的完整工作链路（7 环，每环缺失时的报错）

这是本篇的**主干图**。HPA 之所以难排障，是因为它是一条**串联链路**——**任何一环断了，后面全部不工作，而且只有第一环会明确报错**。

```
   ┌─ ① 节点上的 cAdvisor ────────────────────────────────────────┐
   │  采集每个容器的 CPU / 内存用量                                │
   │  ✗ 缺失 → 无任何指标，HPA 不知道                             │
   └──────────────────────────┬───────────────────────────────────┘
                              ▼
   ┌─ ② kubelet 暴露 /stats/summary（Summary API） ───────────────┐
   │  把节点上所有容器的用量汇总                                    │
   │  ✗ 缺失 → cAdvisor 数据传不出去                               │
   └──────────────────────────┬───────────────────────────────────┘
                              ▼
   ┌─ ③ metrics-server 聚合 ─────────────────────────────────────┐
   │  遍历节点 → GET /metrics/resource → 写内存缓存                 │
   │  每 15 秒采集一次（官方原话："collecting metrics every 15 seconds"）│
   │  ✗ 缺失/崩溃 → ④ 拿不到数据                                   │
   └──────────────────────────┬───────────────────────────────────┘
                              ▼
   ┌─ ④ APIService 注册进 apiserver（metrics.k8s.io） ────────────┐
   │  `kubectl get apiservice v1beta1.metrics.k8s.io`              │
   │  ✗ 不可用 → kubectl top 报 "Metrics API not available"        │
   └──────────────────────────┬───────────────────────────────────┘
                              ▼
   ┌─ ⑤ HPA 控制器读 metrics.k8s.io ─────────────────────────────┐
   │  controller-manager 每 15 秒一次（sync period）                │
   │  ✗ 指标缺失 → HPA status 显示 <unknown>/50%                    │
   └──────────────────────────┬───────────────────────────────────┘
                              ▼
   ┌─ ⑥ 算目标副本数 ─────────────────────────────────────────────┐
   │  ceil(当前副本数 × 当前利用率 / 目标利用率)                     │
   │  ✗ 分母缺失（没写 requests.cpu）→ 分母为 0 → <unknown>         │
   └──────────────────────────┬───────────────────────────────────┘
                              ▼
   ┌─ ⑦ 改 Deployment.spec.replicas ──────────────────────────────┐
   │  Deployment controller 看到 replicas 变了 → 建/删 Pod          │
   │  ✗ 节点没余量 → 新 Pod 全 Pending（这是最"像"HPA 坏了的一种）  │
   └──────────────────────────────────────────────────────────────┘
```

**这张图怎么用来排障（从下往上查最快）**：

| 查什么 | 命令 | 结果说明什么 |
|---|---|---|
| ① 指标通不通 | `kubectl top pods` | **报错 = 断在 ①~④**（metrics-server 侧） |
| ② HPA 能不能算 | `kubectl describe hpa <name>` 看 Events | 有 `FailedGetResourceMetric` → 断在 ⑤ |
| ③ 分母有没有 | `kubectl describe hpa <name>` 看 `ScalingActive` 的 Reason | Reason 是 `FailedGetResourceMetric` 且 Message 提到 missing request → **断在 ⑥（没写 requests.cpu）** |
| ④ 副本数改没改 | `kubectl get deploy` 的 `REPLICAS` vs HPA 的 `REPLICAS` 列 | 改了就说明 ①~⑥ 全通，问题在 ⑦（节点没余量） |

> **必须背下来的一句口诀**：**`kubectl top` 有输出 → 指标链路通；`kubectl top` 报错 → 别再查 HPA 了，去查 metrics-server。**
> 因为 `kubectl top` 和 HPA **读的是同一个数据源**（`metrics.k8s.io`）。这是最省时间的一步。

### 2.2 第一环的真相：cAdvisor → kubelet /stats → metrics-server

官方对 metrics-server 的定位（官方 HPA 演练文档） [S2]：

> The Kubernetes Metrics Server collects resource metrics from the **kubelets** in your cluster, and exposes those metrics through the **Kubernetes API**, using an **APIService** to add new kinds of resource that represent metric readings.

> These resources do not change names from cluster to cluster, and should always be available, **as long as the `metrics.k8s.io` API is available**.

metrics-server 官方仓库 [S3]：

> Metrics Server collects resource metrics from Kubelets and exposes them in Kubernetes apiserver through Metrics API...

> Metrics Server requests `GET /metrics/resource` from the Kubelet → Kubelet returns memory and cpu data for each pod → Metrics Server updates its in-memory cache to store memory and cpu data for each pod.

> Fast autoscaling, **collecting metrics every 15 seconds**.

> Kubelet certificate needs to be signed by cluster Certificate Authority (**or disable certificate validation by passing `--kubelet-insecure-tls` to Metrics Server**)

> `--kubelet-insecure-tls` - Do not verify the CA of serving certificates presented by Kubelets. **For testing purposes only.**

官方 HPA 文档对 metrics-server 的定位 [S1]：

> The `metrics.k8s.io` API is usually provided by an add-on named Metrics Server, **which needs to be launched separately**.

> **三个必须知道的工程事实**：

| 事实 | 来源 | 实践含义 |
|---|---|---|
| metrics-server 是**独立安装的插件**，不是控制面自带 | [S1] | kind / kubeadm 自建集群默认**没有**，托管版（ACK/EKS/GKE）一般自带 |
| 采集周期 **15 秒** | [S3] | 指标本身就有 15 秒延迟，这是扩容延迟的第一段（见 2.10） |
| kubelet 证书需要被 CA 签名，否则要 `--kubelet-insecure-tls` | [S3] | 自建集群常见故障点；**但该参数官方明确说 "For testing purposes only"，生产必须正确配 CA** |

### 2.3 利用率的分母：requests 的三种写法与后果

**官方原文** [S1]：

> For per-pod resource metrics (like CPU), the controller fetches the metrics from the resource metrics API for each Pod targeted by the HorizontalPodAutoscaler. Then, **if a target utilization value is set, the controller calculates the utilization value as a percentage of the equivalent resource request on the containers in each Pod**.

> **Utilization is the ratio between the current usage of resource to the requested resources of the pod.**

官方 HPA 演练文档也强调 [S2]：

> When defining the pod specification the resource requests like `cpu` and `memory` **should be specified**. This is used to determine the resource utilization and used by the HPA controller to scale the target up or down.

**三种写法的后果（本节的题眼）**：

| 写法 | HPA 分母 | 结果 | 原理 |
|---|---|---|---|
| **两个都不写**（requests 和 limits 都没写） | **无分母** | `kubectl get hpa` 的 TARGETS 显示 **`<unknown>/50%`**（或 `<unknown>`），**永远不扩** | 官方 HPA 演练文档明确说 requests "should be specified" [S2]；没有 request 就没有百分比的分母 |
| **只写 `limits.cpu`** | **requests 退化为 limits**（准入阶段复制） | **能算，但利用率永远偏低，几乎不扩** | 官方中文原文：*"如果你为某个资源指定了限制值，但不指定请求值……那么 Kubernetes 会复制你所指定的限制值，将其用作资源的请求值。"* [S4] → 分母 = limits = **你的上限**，而实际用量永远达不到上限 → 利用率永远 < 目标值 |
| **只写 `requests.cpu`**（**HPA 场景的最小正确写法**） | requests | **正常** | 分母就是你要的"配额" |
| **requests 与 limits 都写且相等** | requests | 正常，但**容易被节流陷阱坑到**（见 2.8） | Guaranteed QoS，节点紧张时最后被驱逐 |

**"只写 limits"为什么几乎不扩——用数字讲清**：

```
目标：averageUtilization = 50（即 50%）

写法 A：requests.cpu = 100m, limits.cpu = 500m
  → 分母 = 100m
  → 容器实际跑满 100m 时，利用率 = 100/100 = 100% > 50% → ✅ 会扩

写法 B：只写 limits.cpu = 100m（requests 被复制成 100m）
  → 分母 = 100m
  → 容器实际跑满 100m 时，利用率 = 100% > 50% → ✅ 会扩（看起来一样）

写法 C：只写 limits.cpu = 1000m，而容器业务只需要 100m 就够
  → 分母 = 1000m（被复制过来的）
  → 容器实际只用 100m，利用率 = 100/1000 = 10% << 50% → ❌ 永远不扩
```

> **注意 C 这一行才是真正的坑**：不是"只写 limits"本身错，而是"**只写 limits 且这个 limit 远大于实际用量**"错。**HPA 的分母是你承诺给调度器的"配额"**——如果你承诺了 10 核却只用 1 核，HPA 诚实地报告"利用率只有 10%"，然后**什么也不做**。
>
> **HPA 场景的正确姿势**：**只写 requests，不写 limits**（或 limits 远大于 requests 且你接受节流风险）。**生产上要 Guaranteed QoS 时**，用 **HPA 只看 CPU、QoS 用内存**的组合：CPU `requests=100m/limits=100m`，内存 `requests=128Mi/limits=128Mi`——这样 CPU 利用率分母准确，也没有节流问题。

### 2.4 扩容公式与算例

**官方原文（公式）** [S1]：

> ```
> desiredReplicas = ceil⌈currentReplicas×currentMetricValue / desiredMetricValue⌉
> ```

**本资料库的统一口径**（与 L2 第五节一致）：

```
期望副本数 = ceil(当前副本数 × 当前利用率 / 目标利用率)
```

> From the most basic perspective, the HorizontalPodAutoscaler controller operates on the ratio between desired metric value and current metric value... The currentMetricValue / desiredMetricValue ratio is then calculated, using the remaining pods not set aside or discarded from above.

> After factoring in the not-yet-ready pods and missing metrics, the controller recalculates the usage ratio. **If the new ratio reverses the scale direction, or is within the tolerance, the controller doesn't take any scaling action.**

> **The control plane skips any scaling action if the ratio is sufficiently close to 1.0 (within a configurable tolerance, 0.1 by default).**

**算例（三个，逐级递进）**：

**算例 1：常规扩容**

```
当前副本数 currentReplicas = 3
当前 CPU 利用率 currentMetricValue = 80%
目标利用率 desiredMetricValue = 50%

desiredReplicas = ceil(3 × 80 / 50) = ceil(4.8) = 5
```

> **为什么必须向上取整**：**副本数是整数，且宁多勿少**。如果用向下取整 `floor(4.8) = 4`，负载还是 80%，下一个周期算出来还是 4.8，还是 4——**永远停在 4，永远达不到目标**。向上取整保证**每一步都能真正降低负载**。

**算例 2：边界——刚好等于目标，不扩**

```
currentReplicas = 4
currentMetricValue = 50%
desiredMetricValue = 50%

比值 = 4 × 50 / 50 = 4.0
比值 = 1.0 → 落在容差内（默认 tolerance = 0.1，即 1.0 ± 0.1）→ ❌ 不做任何伸缩
```

> **容差（tolerance）是防抖的第一道闸**。官方原文：*"the controller skips any scaling action if the ratio is sufficiently close to 1.0 (within a configurable tolerance, 0.1 by default)"* [S1]。
> **含义**：比值在 **0.9 ~ 1.1** 之间都不动。**这解释了为什么"利用率 55%、目标 50%"不会扩容**——55/50 = 1.1，正好在容差边界上。

**算例 3：多指标取最大**

官方原文 [S1]：

> If multiple metrics are specified in a HorizontalPodAutoscaler, this calculation is done for each metric, and then **the largest of the desired replica counts is chosen**.

```
metrics:
  - type: Resource
    resource: {name: cpu, target: {type: Utilization, averageUtilization: 50}}
  - type: Resource
    resource: {name: memory, target: {type: Utilization, averageUtilization: 80}}

当前 3 个副本：
  CPU   利用率 80% → ceil(3 × 80/50) = ceil(4.8) = 5
  内存 利用率 60% → ceil(3 × 60/80) = ceil(2.25) = 3

取最大值 → desiredReplicas = 5
```

> **多指标是"取最大"，不是"取平均"**。所以**加一个指标只会让 HPA 更激进，不会更保守**。

**官方原文（指标缺失时的保守处理）** [S1]：

> If a particular Pod is missing metrics, it is set aside for later; Pods with missing metrics will be used to adjust the final scaling amount.

> **If there were any missing metrics, the control plane recomputes the average more conservatively, assuming those pods were consuming 100% of the desired value in case of a scale down, and 0% in case of a scale up.** This dampens the magnitude of any potential scale.

> **这就是"有 Pod 不 Ready 时 HPA 缩不下去"的原理**：缺失指标的 Pod 在**缩容**时被假设为"用满了 100%"，于是平均值被抬高，缩容被抑制。**这是保护机制，不是 bug。**

### 2.5 autoscaling/v2 的五种指标类型

**官方 `autoscaling/v2` API 参考原文** [S5]：

> `type` is the type of metric source. It should be one of **"ContainerResource", "External", "Object", "Pods" or "Resource"**, each mapping to a matching field in the object.

官方 HPA 文档对每种类型的说明 [S1]：

| 类型 | 官方原文要点 | 典型 yaml | 需要什么组件 |
|---|---|---|---|
| **`Resource`** | *"Any HPA target can be scaled based on the resource usage of the pods in the scaling target."* / *"Utilization is the ratio between the current usage of resource to the requested resources of the pod."* | `resource: {name: cpu, target: {type: Utilization, averageUtilization: 60}}` | **metrics-server** |
| **`ContainerResource`** | *"the HPA can track the resource usage of **individual containers** across a set of Pods"*；官方注明 **ContainerResource 自 Kubernetes v1.30 起为稳定功能** | `containerResource: {name: cpu, container: app, target: {...}}` | metrics-server |
| **`Pods`** | *"how to scale on a metric describing each pod in the current scale target (for example, transactions-processed-per-second). The values will be averaged together before being compared to the target value."* —— **注意：用的是原始值，不是百分比** | `pods: {metric: {name: qps}, target: {type: AverageValue, averageValue: "100"}}` | **自定义指标 API**（Prometheus Adapter / KEDA） |
| **`Object`** | *"For object metrics and external metrics, a single metric is fetched, which describes the object in question."* | `object: {describedObject: {...}, metric: {name: qps}, target: {...}}` | 自定义指标 API |
| **`External`** | 同上，且 *"this metric is compared to the target value, to produce a ratio as above"* | `external: {metric: {name: queue_depth}, target: {type: Value, value: "30"}}` | 外部指标 API（Prometheus Adapter 等） |

**`MetricTarget` 的三个 type（官方原文）** [S5]：

| type | 官方原文 | 适用 |
|---|---|---|
| `Utilization` | *"the target value of the average of the resource metric across all relevant pods, represented as a **percentage of the requested value** of the resource for the pods. **Currently only valid for Resource metric source type**"* | 仅 `Resource` / `ContainerResource` |
| `AverageValue` | *"the target value of the average of the metric across all relevant pods (as a quantity)"* | `Pods` / `Object` / `External` |
| `Value` | *"the target value of the metric (as a quantity)"* | `Object` / `External`（**单个**对象，不是平均） |

**默认值（官方原文）** [S5]：

> `metrics` ... **If not set, the default metric will be set to 80% average CPU utilization.**

> `minReplicas` is the lower limit for the number of replicas to which the autoscaler can scale down. **It defaults to 1 pod.** minReplicas is allowed to be 0 if the alpha feature gate `HPAScaleToZero` is enabled and at least one Object or External metric is configured.

> `maxReplicas` is the upper limit for the number of replicas to which the autoscaler can scale up. **It cannot be less that minReplicas.**

**官方 HPA 文档关于 API 版本的说明** [S1]：

> The current stable version can be found in the `autoscaling/v2` API version which includes support for scaling on memory and custom metrics.

### 2.6 behavior 字段：scaleUp / scaleDown 的默认值与"故意不对称"

**官方 `autoscaling/v2` API 参考原文（`HPAScalingRules`）** [S5]：

> `stabilizationWindowSeconds` is the number of seconds for which past recommendations should be considered while scaling up or scaling down. **StabilizationWindowSeconds must be greater than or equal to zero and less than or equal to 3600 (one hour). If not set, use the default values:**
> - **For scale up: 0 (i.e. no stabilization is done).**
> - **For scale down: 300 (i.e. the stabilization window is 300 seconds long).**

> `policies` is a list of potential scaling polices which can be used during scaling. **If not set, use the default values:**
> - **For scale up: allow doubling the number of pods, or an absolute change of 4 pods in a 15s window.**
> - **For scale down: allow all pods to be removed in a 15s window.**

> `selectPolicy` is used to specify which policy should be used. **If not set, the default value Max is used.**

> `tolerance` is the tolerance on the ratio between the current and desired metric value under which no updates are made to the desired number of replicas (e.g. 0.01 for 1%). **Must be greater than or equal to zero. If not set, the default cluster-wide tolerance is applied (by default 10%).**

**`HorizontalPodAutoscalerBehavior` 层的原文** [S5]：

> `scaleDown` is scaling policy for scaling Down. **If not set, the default value is to allow to scale down to minReplicas pods, with a 300 second stabilization window (i.e., the highest recommendation for the last 300sec is used).**

> `scaleUp` is scaling policy for scaling Up. **If not set, the default value is the higher of:**
> - *increase no more than **4 pods per 60 seconds***
> - *double the number of pods **per 60 seconds***
> **No stabilization is used.**

**把默认值汇总成一张表**：

| 参数 | **扩容（scaleUp）** | **缩容（scaleDown）** |
|---|---|---|
| `stabilizationWindowSeconds` | **0**（不做稳定化） | **300**（5 分钟） |
| `policies` 默认速率 | **15 秒窗口内：翻倍 或 +4 个** | **15 秒窗口内：可以全删** |
| `selectPolicy` | **Max**（多条 policy 取最激进的那条） | **Max** |
| `tolerance` | **10%**（集群级默认） | 10% |
| `minReplicas` / `maxReplicas` | maxReplicas **必填** | minReplicas 默认 **1** |

> **⚠️ 一处官方文档内部不一致（必须知道，否则会在面试/评审时说错）**：
> - `HPAScalingRules.policies` 的描述说扩容默认是 **"4 pods in a 15s window"**（15 秒窗口）；
> - `HorizontalPodAutoscalerBehavior.scaleUp` 的描述说扩容默认是 **"4 pods per 60 seconds"**（60 秒窗口）。
> - **两处都在同一页官方 API 参考里** [S5]。
> - **本篇按 `HPAScalingRules.policies` 的说法（15 秒窗口）陈述，并标注 `⚠️ 待验证`**：建议用 `kubectl explain hpa.spec.behavior.scaleUp.policies` 在本集群版本上确认，或直接用显式配置覆盖默认值（见下方 yaml），**不要依赖这个默认值的具体窗口长度**。

**为什么"故意不对称"（面试高频）**：

| 方向 | 决策 | 代价 | 理由 |
|---|---|---|---|
| **扩容** | **快**（0 稳定化窗口 + 15 秒最多翻倍） | 资源成本可能虚高 | **扩容错了的代价远大于缩容错了**。多出来的副本只是多花钱，扩容太慢的代价是**用户流失**。 |
| **缩容** | **慢**（300 秒稳定窗口 + 取窗口内最高推荐值） | 回收资源慢，账单多付几分钟 | **缩容太快的代价是"刚扩完又缩掉"，来回抖动（flapping），比多花钱糟糕得多** |

> **官方原文（控制器级参数，与 behavior 层一致）** [S1]：
> > Finally, right before HPA scales the target, the scale recommendation is recorded. The controller considers all recommendations within a configurable window **choosing the highest recommendation from within that window**. You can configure this value using the `--horizontal-pod-autoscaler-downscale-stabilization` command line option, **which defaults to 5 minutes**. This means that **scaledowns will occur gradually, smoothing out the impact of rapidly fluctuating metric values**.

> **注意"取最高推荐值"的语义**：缩容窗口内，控制器会回看 300 秒里**所有**推荐值，**取其中最高的**（= 最保守 = 副本最多的那个）。所以**缩容一定是在负载持续下降 5 分钟之后才发生**。

**生产推荐配置（把"慢缩"做得更保守）**：

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: web
  minReplicas: 3          # 留 3 个热备，节点/镜像冷启动时也不至于 502
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 60
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0      # 扩容不加窗口
      selectPolicy: Max
      policies:
      - type: Percent
        value: 100                      # 每 15 秒最多翻倍
        periodSeconds: 15
      - type: Pods
        value: 4                        # 或每 15 秒最多加 4 个
        periodSeconds: 15
    scaleDown:
      stabilizationWindowSeconds: 600   # 比默认 300 更保守
      selectPolicy: Min                 # 取最保守的 policy
      policies:
      - type: Pods
        value: 1                        # 每 60 秒最多减 1 个
        periodSeconds: 60
```

> **`selectPolicy: Max` vs `Min` 的含义**：官方定义 `selectPolicy` 用于"指定使用哪条 policy" [S5]。`Max` = **取最激进的**（扩容想快就选 Max，policy 之间取允许变化量最大的）；`Min` = **取最保守的**（缩容想慢就选 Min，policy 之间取允许变化量最小的）。**扩容用 Max、缩容用 Min，是生产上的常见组合**——本篇标 `⚠️ 待验证`（官方该页只明确了默认是 Max，未逐字解释 Max/Min 的取舍语义）。

### 2.7 kubectl top 与 HPA TARGETS 的关系

**官方 HPA 演练文档给出的输出格式** [S2]：

```
NAME         REFERENCE                     TARGET      MINPODS   MAXPODS   REPLICAS   AGE
php-apache   Deployment/php-apache/scale   305% / 50%  1         10        1          3m
```

官方对该列的解释 [S2]：

> Please note that the current CPU consumption is 0% as there are no clients sending requests to the server (**the `TARGET` column shows the average across all the Pods controlled by the corresponding deployment**).

> The second, `ScalingActive`, indicates whether or not the HPA is enabled (i.e. the replica count of the target is not zero) and **is able to calculate desired scales. When it is `False`, it generally indicates problems with fetching metrics.**

官方 `kubectl describe hpa` 的 Conditions 输出格式 [S2]：

```
Conditions:
  Type                  Status  Reason                  Message
  ----                  ------  ------                  -------
  AbleToScale           True    ReadyForNewScale        the last scale time was sufficiently old as to warrant a new scale
  ScalingActive         True    ValidMetricFound        the HPA was able to successfully calculate a replica count from pods metric http_requests
  ScalingLimited        False   DesiredWithinRange      the desired replica count is within the acceptable range
```

**三条命令的关系（一张表）**：

| 命令 | 数据源 | 回答什么 | 与 HPA 的关系 |
|---|---|---|---|
| `kubectl top pods` | `metrics.k8s.io` | **当前**实测用量（cores / bytes） | **同一个数据源**。`top` 有数 → 指标链路通；`top` 报错 → 断在 ①~④，HPA 必然不工作 |
| `kubectl get hpa` | HPA 的 `status.currentMetrics` | HPA **上次算的时候**看到的利用率 / 目标 | TARGETS 的分子**就是** HPA 算公式用的分子 |
| `kubectl describe hpa` | HPA 的 status + Events | **为什么不算 / 为什么没改** | 定位断链点的唯一入口 |

**`kubectl top` 与 HPA TARGETS 的两个常见不一致（要能解释）**：

| 现象 | 解释 |
|---|---|
| `kubectl top pod` 显示 80m，HPA TARGETS 显示 45% | **正常**。TARGETS 的分子是"所有目标 Pod 的平均用量 ÷ requests"，而且是**15 秒前的旧值**。top 是**瞬时值**，HPA 是**周期采样的滞后值** |
| `kubectl top pod` 显示 90% 的 CPU（即 90m），但 requests 是 100m，TARGETS 显示 45% | **正常但要警惕**：`top` 显示的是**容器实际用量的某个采样点**；如果容器被 **cgroup 节流**（limits 太小），`top` 读到的就是**被压住之后的值**，而 HPA 用的也是这个被压住的值——**两者一致地错**。见 2.8 |

### 2.8 经典陷阱：limits 太小 → 节流 → 扩了也没用（不是「有值但不扩」）

这是本篇**最值钱的一节**，因为它解释了"**明明 CPU 打满了，HPA 就是不动**"。

**官方原文（中文，管理容器资源）** [S4]：

> `cpu` 限制通过 CPU 节流机制（CPU Throttling）来强制执行。当某容器接近其 `cpu` 限制值时，内核会基于容器的限制值来限制其对 CPU 的访问。因此，`cpu` 限制是内核强制执行的一个硬性限制。容器不得使用超出其 `cpu` 限制所指定的 CPU 核数。

> **CPU 限制定义的是容器可使用的 CPU 时间的硬性上限。在每个调度周期（时间片）期间，Linux 内核检查是否已经超出该限制；内核在允许该 CGroup 恢复执行之前会等待。**

> **每个容器可能被允许也可能不被允许使用超过其 CPU 限制值的处理时间。但是，容器运行时不会由于 CPU 使用率过高而杀死 Pod 或容器。**

**因果链**：

```
limits.cpu = 50m，requests.cpu = 50m（Guaranteed），目标 averageUtilization = 60%
        │
        ▼
应用实际需要 200m CPU
        │
        ▼
cgroup 把容器节流在 50m 上限（内核在每个调度周期检查，超了就挂起等待）
        │
        ▼
cAdvisor / metrics-server 采集到的是**被节流后**的真实用量 = 50m
        │
        ▼
利用率 = 50m ÷ 50m = 100% ... 咦，这个例子反而会扩
        │
        ▼
换个例子：limits.cpu = 200m，requests.cpu = 100m，目标 60%
        应用实际需要 1000m CPU
        ▼
被节流在 200m，采集到 200m
        ▼
利用率 = 200m ÷ 100m = 200% > 60% → 会扩（但扩了也没用，因为每个 Pod 都被限死在 200m）
        │
        ▼
再换：limits.cpu = 50m，requests.cpu = 500m（**limits < requests，非法**，被 API 拒绝）
        │
        ▼
真正会踩的组合：limits.cpu = 50m，只写 limits 不写 requests
        → requests 被复制成 50m → 分母 50m
        → 容器被节流在 50m，实际业务极慢
        → 利用率 = 50/50 = 100% → 会扩，但**扩出来还是 50m**，业务还是慢
```

> **关键结论（这是最反直觉、也最重要的一条）**：
> **节流不会让利用率"偏低"，它让利用率"虚高"。** 真正让利用率**偏低**导致不扩容的，是**"只写 limits 且 limits 远大于实际用量"**（分母被撑大，见 2.3 写法 C）。
>
> **所以"limits 太小导致 cgroup 节流、利用率算出来偏低而不扩"这个说法需要修正**：官方文档明确 CPU limit 是**硬性上限**且**不会杀死容器** [S4]，被节流的容器采集到的用量就是它的上限值。**分母是 requests 时，节流会让利用率偏高（更容易扩）；分母是 limits（只写 limits 的退化情形）时，节流会让利用率顶到 100%（也更容易扩）。**
>
> **那"有值但不扩"到底怎么发生的？** 只有这几种：
> 1. **分母被撑大**（只写 limits 且 limits ≫ 实际用量）→ 利用率永远低于目标。
> 2. **命中容差**（比值在 0.9~1.1）→ 官方明确跳过 [S1]。
> 3. **已达 `maxReplicas`** → HPA 的 `ScalingLimited` 条件会告诉你。
> 4. **指标根本没采到**（metrics-server 挂了 / APIService 不可用）→ TARGETS 显示 `<unknown>`。
> 5. **有 Pod 不 Ready** → 官方明确此时**缩容被抑制** [S1]。
> 6. **`selectPolicy` + `policies` 把扩容速率限死** → 每次只加 1 个，看起来像"不扩"。
>
> **但"limits 太小"确实是个真问题，只是它造成的是"**扩容了也没用**"（业务还是慢、用户还是在流失），而不是"不扩容"。** 排查时要把这两件事分开：**HPA 动没动**（看 REPLICAS 列）**和**扩完够不够用**（看 top 与用户体感）。

> **给面试用的准确表述**：*"limits 太小会让容器被 cgroup 节流，容器运行时不会因为 CPU 超限被杀，但应用会明显变慢。如果 HPA 用的是 requests 做分母，节流后的实测用量反而会让利用率偏高、触发扩容——**扩了也没用，因为每个新 Pod 同样被限死**。真正让 HPA '有值但不扩'的是分母被撑大（只写 limits 而 limits 远大于实际用量）、命中 10% 容差、已达 maxReplicas、或指标没采到。**"*

### 2.9 HPA 只管 Pod，不管节点：两级联动

**这是本篇第二个高频面试题。**

**官方原文（HPA 文档）** [S1]：

> `scaleTargetRef` points to the target resource to scale, and is used to the pods for which metrics should be collected, **as well as to actually change the replica count**.

**HPA 做的事**：改 `Deployment.spec.replicas` → Deployment controller 创建新 Pod → 调度器给新 Pod 找节点。
**HPA 不做的事**：**它不会创建任何节点**。

**因此，当集群里没有足够空闲资源时，HPA 扩出来的 Pod 会全部卡在 `Pending`。**

```
        ┌──────────────┐         ┌──────────────┐
        │     HPA      │  改     │  Deployment  │  建 Pod
        │ (副本数)      │ ──────▶ │              │ ──────┐
        └──────────────┘         └──────────────┘       │
                                                          ▼
        ┌──────────────┐         ┌──────────────┐   ┌──────────┐
        │ cluster-     │  加节点  │   kube-      │   │ 新 Pod   │
        │ autoscaler   │ ◀─────── │  scheduler   │◀──│          │
        │ (节点数)      │  Pending │              │   │ Pending! │
        └──────────────┘         └──────────────┘   └──────────┘
```

**两级弹性的职责边界**：

| 级别 | 组件 | 扩什么 | 决策依据 | 冷启动代价 |
|---|---|---|---|---|
| **第一级** | **HPA** | **Pod 副本数** | `metrics.k8s.io` 的利用率 / 业务指标 | **秒级**（新 Pod 复用已有节点） |
| **第二级** | **cluster-autoscaler** | **节点数** | **未调度 Pod 的数量**（Pending Pod） | **分钟级**（要开机、装系统、配网络、加入集群） |
| **纵轴**（正交） | **VPA** | **单个 Pod 的 requests/limits** | 资源使用历史 | 需重启 Pod 才生效 |

**cluster-autoscaler 的工作方式（与 HPA 完全不同）**：

| 维度 | HPA | cluster-autoscaler |
|---|---|---|
| **触发信号** | **指标**（利用率、QPS） | **未调度的 Pod 数量**（Pending） |
| **动作** | 改 `replicas` | 调云厂商 API 加节点 / 删节点 |
| **扩容延迟** | 秒级（复用节点） | **分钟级**（新节点要完整初始化） |
| **缩容** | 按 `behavior.scaleDown` | 按节点利用率 + **PDB** 保护 |
| **K8s 官方组件？** | ✅ 内置（controller-manager） | ❌ **独立项目**（`kubernetes/autoscaler` 仓库） |

> **一句话记住**：**HPA 负责"Pod 不够"，cluster-autoscaler 负责"节点不够"。两者通过 Pending Pod 这一个信号串起来。**
>
> **生产实践（阿里云 ACK 的形态）**：ACK 的 **ACK 弹性伸缩组**承担的是"节点不够"这一级（对应开源的 cluster-autoscaler），**HPA 承担"Pod 不够"这一级**。两者**必须都配**，只配一个必然出问题：
> - 只配 HPA → 扩出来全 Pending（没节点）
> - 只配弹性伸缩 → 指标涨了但 Pod 数不动（没人改 replicas）
>
> **扩容的完整时间预算**（这是选型的关键）：
> ```
> HPA 发现（≤15s）+ 决策（≤15s）+ 新节点开机与初始化（分钟级）+ Pod 拉镜像（秒~分钟级）+ 就绪探针通过
> ```
> **结论：HPA 只能救"Pod 不够"，救不了"节点不够"。** 这就是为什么生产上 `minReplicas` 要留热备、新集群要提前准备节点池余量。

### 2.10 扩容延迟的四段耗时拆解

用户抱怨"扩容太慢"时，**必须把延迟拆开**，否则无法定位。

| 段 | 耗时来源 | 典型量级 | 官方依据 | 怎么压缩 |
|---|---|---|---|---|
| **① 指标采集延迟** | metrics-server 采集周期 | **15 秒** | metrics-server 官方：*"collecting metrics every 15 seconds"* [S3] | 调 metrics-server 的采集周期（**生产不建议调太短**，metrics-server 本身压力大） |
| **② HPA 同步延迟** | controller-manager 的 sync period | **15 秒** | 官方：*"--horizontal-pod-autoscaler-sync-period ... default interval is 15 seconds"* [S1] | 改 controller-manager 启动参数（**集群级，影响面大**） |
| **③ 调度 + 拉镜像** | 调度决策 + 容器镜像拉取 + 容器启动 | 秒级 ~ **分钟级** | 镜像大小、仓库速度、节点是否已有镜像 | **预拉镜像**（`imagePullPolicy: IfNotPresent` + 节点预热）、就近拉取、**提前留 `minReplicas` 热备** |
| **④ 就绪探针通过** | `readinessProbe` 的 `initialDelaySeconds` + `periodSeconds` × `failureThreshold` | **秒级 ~ 分钟级** | kubelet 探针默认：period 10s / failure 3 次（L2 实测口径） | **慢启动应用加 `startupProbe`**，别用 liveness 硬扛 |

**从"用户请求进来"到"新 Pod 接流量"的完整时间线**：

```
t=0s     用户请求进来，负载开始上升
         ↓
t=0~15s  ① metrics-server 采集周期内（最多等 15s 才看到新用量）
         ↓
t=15~30s ② HPA 同步周期内（最多再等 15s 才算一次）
         ↓
t≈30s    ③ HPA 算出 desiredReplicas → 改 Deployment.replicas
         ↓
t≈30s+   Deployment controller 建 Pod → 调度器找节点
         │   ⚠️ 没有节点余量 → 这里就卡住（Pending）→ 触发 cluster-autoscaler → 分钟级
         ↓
t≈35s+   kubelet 拉镜像（第一次拉要下载，可能几十秒到几分钟）
         ↓
t≈40s+   容器启动 → readinessProbe 通过 → Service 端点加入 → 开始接流量
```

> **最容易被忽略的第 ④ 段**：很多"扩容慢"的投诉其实是**探针配得太保守**。一个 Java 应用冷启动 30 秒，却配了 `initialDelaySeconds: 5` + liveness 探针 → 容器起来就被杀 → 永远起不来。
>
> **正确姿势**：**慢启动应用一律加 `startupProbe`**，让 liveness/readiness 在 startupProbe 成功后才开始工作。

### 2.11 PodDisruptionBudget：给缩容加一道保险

**官方原文（Pod 中断，中文/英文）** [S6]：

> As an application owner, you can create a PodDisruptionBudget (PDB) for each application. **A PDB limits the number of Pods of a replicated application that are down simultaneously from voluntary disruptions.**

> **Not all voluntary disruptions are constrained by PodDisruption Budgets. For example, deleting deployments or pods bypasses Pod Disruption Budgets.**

> Cluster managers and hosting providers **should use tools which respect PodDisruptionBudgets by calling the Eviction API instead of directly deleting pods or deployments.**

**PDB 与 HPA 缩容的关系（本节题眼）**：

| 维度 | 说明 |
|---|---|
| **PDB 管的是"自愿中断"（voluntary disruption）** | 节点维护（`kubectl drain`）、集群缩容、集群-autoscaler 删节点 |
| **PDB 不管"非自愿中断"** | 节点宕机、OOM Kill、容器崩溃 → **PDB 完全不管** |
| **HPA 直接删 Pod（改 replicas）** | HPA 的缩容是**改 replicas**，**不走 Eviction API** → **PDB 不会阻止 HPA 缩容** |
| **cluster-autoscaler 缩容节点** | 它是"删除节点"，属于自愿中断范畴 → **PDB 会被尊重**，节点上有 PDB 保护的 Pod 时，它不会删那个节点 |
| **人工 `kubectl delete pod`** | 官方明确：*"deleting deployments or pods **bypasses** Pod Disruption Budgets"* → **绕过 PDB** |

**官方 PDB API 参考原文** [S7]：

> `minAvailable` indicates that an eviction is allowed if at least "minAvailable" pods selected by "selector" will still be available after the eviction, i.e. even in the absence of the evicted pod. So for example **you can prevent all voluntary evolutions by specifying "100%"**.

> `maxUnavailable` indicates that an eviction is allowed if at most "maxUnavailable" pods selected by "selector" are unavailable after the eviction... **This is a mutually exclusive setting with "minAvailable".**

> `selector` is a label query over pods whose evictions are managed by the disruption budget. **A null selector will match no pods, while an empty ({}) selector will select all pods within the namespace.**

**PDB 的 status 字段（官方原文）** [S7]：

| 字段 | 官方原文 | 读法 |
|---|---|---|
| `currentHealthy` | *"current number of healthy pods"* | 当前健康数 |
| `desiredHealthy` | *"minimum desired number of healthy pods"* | 由 `minAvailable`/`maxUnavailable` 推导 |
| `disruptionsAllowed` | *"Number of pod disruptions that are currently allowed."* | **= 还能被驱逐几个**。**这个值是 0 就说明"现在不能驱逐任何副本"** |
| `expectedPods` | *"total number of pods counted by this disruption budget"* | 预算覆盖的 Pod 总数 |

> **用法要点**：
> - **`minAvailable` 和 `maxUnavailable` 互斥**，只能写一个。
> - **`minAvailable` 支持百分比**（写 `"100%"` = 一个都不许动）。
> - **`selector` 写 `{}`（空）= 选中整个命名空间的 Pod**；**`null` = 一个都不选**（这个区别很坑）。
> - **PDB 最主要的用途不是防 HPA 缩容，而是防"节点维护 / 集群缩容 / 集群-autoscaler 删节点"时把服务打挂。**
> - **配了 PDB 但没生效**的常见原因：① selector 写错，没选中 Pod；② 用的工具是 `delete` 而不是 `drain`（`drain` 走 Eviction API）；③ PDB 的 `disruptionsAllowed` 本来就是 0（因为 `minAvailable` 设得太满）。

### 2.12 关键参数与默认值

| 参数 | 默认 | 含义 | 出处 |
|---|---|---|---|
| `--horizontal-pod-autoscaler-sync-period` | **15 秒** | HPA 控制循环周期 | [S1] |
| metrics-server 采集周期 | **15 秒** | 采集并缓存的间隔 | [S3] |
| `stabilizationWindowSeconds`（扩容） | **0** | 不做稳定化 | [S5] |
| `stabilizationWindowSeconds`（缩容） | **300 秒** | 窗口内取最高推荐值 | [S5] |
| `--horizontal-pod-autoscaler-downscale-stabilization` | **5 分钟** | 控制器级缩容稳定参数，与 behavior 默认一致 | [S1] |
| `selectPolicy` | **Max** | 多条 policy 的取舍 | [S5] |
| `tolerance` | **10%**（集群级） | 比值在 1.0±0.1 内不伸缩 | [S1] / [S5] |
| 扩容默认 policy | **15 秒窗口内翻倍或 +4 个**（⚠️ 官方两处表述不一致，见 2.6） | 扩容速率上限 | [S5] |
| 缩容默认 policy | **15 秒窗口内可全删** | 缩容速率上限 | [S5] |
| `minReplicas` | **1** | 最小副本数 | [S5] |
| `maxReplicas` | **必填** | 最大副本数；不能小于 `minReplicas` | [S5] |
| `metrics`（未设置时） | **80% 平均 CPU 利用率** | 默认指标 | [S5] |
| `stabilizationWindowSeconds` 取值范围 | **0 ~ 3600 秒** | 官方明确的上界 | [S5] |
| `HPAScalingPolicy.periodSeconds` 取值范围 | **> 0 且 ≤ 1800 秒（30 分钟）** | 官方明确的上界 | [S5] |
| `HPAScalingPolicy.value` | **必须 > 0** | 允许的变化量 | [S5] |
| `minDomains`（拓扑分布） | **1** | 合格拓扑域最小数量 | [S8] |

---

## 3. 动手

> **前置提醒**：kind 集群 `day2` 是**单节点**集群。HPA 扩容后新 Pod 会调度不上（节点资源不够）——**这恰好是 2.9 讲的"级联失效"的真实演示**，值得亲手做一次。

### 3.1 前置检查：metrics-server 在不在

```bash
# ① 指标 API 是否注册（这是最准的判断）
kubectl get apiservice v1beta1.metrics.k8s.io

# ② 直接问指标 API 有没有数据
kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes

# ③ 最快的试金石
kubectl top nodes
kubectl top pods
```

**看到什么算对**：

| 命令 | 期望 | 不对说明什么 |
|---|---|---|
| `kubectl get apiservice v1beta1.metrics.k8s.io` | 存在，`AVAILABLE` 为 **True** | 不存在 → **metrics-server 没装**（自建集群常见） |
| `kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes` | 返回 JSON，含 `usage` | 报错 → 断在 ①~④ |
| `kubectl top nodes` | 打印各节点 CPU/MEMORY | 报 `Metrics API not available` → **断在 ①~④，HPA 必然不工作** |

> **kind 集群默认没有 metrics-server**。需要装的话用官方发布清单（这是官方 README 给的命令） [S3]：
> ```bash
> kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
> ```
> **本篇不代你执行**，因为它会改动集群。装完等 60–90 秒（采集有冷启动）再验证。

### 3.2 最小 HPA

先建一个**带 requests.cpu 的** Deployment（**没有 requests 就别做这个实验了**，直接跳到 3.4 看失败现象）：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
      - name: web
        image: nginx
        resources:
          requests:
            cpu: "100m"
            memory: "128Mi"
        ports:
        - containerPort: 80
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: web
  minReplicas: 1
  maxReplicas: 5
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 50
```

```bash
kubectl apply -f hpa-demo.yaml
sleep 60          # 等 metrics-server 出第一批数据
kubectl get hpa
```

**看到什么算对**：

| 现象 | 期望 |
|---|---|
| `kubectl get hpa` 的 TARGETS 列 | 类似 `0% / 50%`（nginx 空闲，CPU 接近 0） |
| `kubectl describe hpa web-hpa` 的 Conditions | `AbleToScale: True ReadyForNewScale`、`ScalingActive: True ValidMetricFound` |
| Events | **空**（没事件 = 没报错 = 健康） |

> **Conditions 是判断 HPA 是否"活着"的核心**。官方对 `ScalingActive` 的解释 [S2]：*"indicates whether or not the HPA is enabled ... and is able to calculate desired scales. **When it is `False`, it generally indicates problems with fetching metrics.**"
>
> **`ScalingActive: False` → 去 Events 看 `FailedGetResourceMetric` → 断在 ⑤**。

### 3.3 制造负载并观察

```bash
# 终端 A：持续压 CPU（nginx 默认不忙，用 busybox 更直观）
kubectl run load --image=busybox:1.36 --restart=Never -- sh -c 'while true; do echo x; done'

# 终端 B：观察
kubectl get hpa --watch
kubectl get pods -w
```

**看到什么算对（时间线）**：

| 时刻 | 现象 |
|---|---|
| t=0 | 压测开始，`kubectl top pod web` 的 CPU 开始上升 |
| t ≤ 15s | metrics-server 采到新值 |
| t ≤ 30s | HPA 算出 `desiredReplicas > current`，改 `Deployment.replicas` |
| t ≈ 30s+ | **新 Pod 被创建**（`kubectl get pods` 数量增加） |
| t ≈ 30s+ | **在 kind 单节点上，新 Pod 很可能 `Pending`**（节点 CPU 装不下 2×100m？不，100m 很够——但 kind 单节点的 allocatable 是够的，通常能起来） |

> **kind 单节点的真实限制**：节点资源是够的（100m 很小），所以 HPA 通常能扩起来。**但如果你把 requests 调大到超过节点 allocatable，就会看到"扩出来全 Pending"**——**这就是 2.9 说的级联失效**，值得亲手做一次。

**收尾观察缩容**：

```bash
# 停掉压测
kubectl delete pod load
kubectl get hpa --watch
```

**看到什么算对**：**副本数在 5 分钟后才开始往下掉**（缩容稳定窗口 300 秒）。**这个"迟迟不缩"不是 bug，是设计。**

### 3.4 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| 指标 API 注册 | `kubectl get apiservice v1beta1.metrics.k8s.io` | `AVAILABLE=True` |
| 指标可读 | `kubectl top pods` | 有输出（**这是 HPA 能不能工作的先决条件**） |
| HPA 目标可读 | `kubectl get hpa` | TARGETS 显示 `xx% / 50%`，**不是 `<unknown>`** |
| HPA 条件健康 | `kubectl describe hpa` | `AbleToScale=True`、`ScalingActive=True` |
| 分母存在 | `kubectl get deploy web -o yaml` 看 `resources.requests.cpu` | **必须非空** |
| 扩容生效 | `kubectl get deploy web` 的 `REPLICAS` | 增长 |
| 缩容生效 | 同上 | **约 5 分钟后**下降 |
| 节点够用 | `kubectl describe node` 看 `Allocatable` 与 `Allocated resources` | 有余量，否则新 Pod Pending |

---

## 4. 资源清单逐字段

### 4.1 HorizontalPodAutoscaler spec

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: autoscaling/v2` | string | ✅ | API 组与版本 | 写 `autoscaling/v1` → **只能用 CPU 指标**，自定义指标字段直接被拒 |
| `kind: HorizontalPodAutoscaler` | string | ✅ | 对象类型 | — |
| `metadata.name` | string | ✅ | HPA 名字 | 重名 → `AlreadyExists` |
| `spec.scaleTargetRef.apiVersion` | string | ✅ | 目标工作负载的 API 版本 | 写错 → **HPA 找不到目标**，Events 里报找不到 scale 子资源 |
| `spec.scaleTargetRef.kind` | string | ✅ | 目标类型（`Deployment` / `StatefulSet` / `ReplicaSet` / `ReplicationController`） | 写 `Pod` → **无 scale 子资源**，HPA 不工作 |
| `spec.scaleTargetRef.name` | string | ✅ | 目标名字 | 写错 → 同上 |
| `spec.minReplicas` | int | | 最小副本数，**默认 1** | 设 0 → 需开启 `HPAScaleToZero` **且**配置 Object/External 指标 |
| `spec.maxReplicas` | int | ✅ | 最大副本数 | **不写 → API 校验失败**；小于 `minReplicas` → 报错 |
| `spec.metrics[]` | []MetricSpec | | 指标列表（**多指标取最大**） | **不写 → 默认 80% 平均 CPU 利用率** |
| `spec.metrics[].type` | enum | ✅ | `Resource` / `ContainerResource` / `Pods` / `Object` / `External` | 写错 → API 拒绝（官方明确只允许这五种） |
| `spec.metrics[].resource.name` | string | | 资源名（`cpu` / `memory`） | 写 `cpus` → 指标取不到 |
| `spec.metrics[].resource.target.type` | enum | ✅ | `Utilization` / `AverageValue` / `Value` | `Utilization` **只对 Resource 类型有效** |
| `spec.metrics[].resource.target.averageUtilization` | int | | 目标百分比（**分母是 requests**） | 分母不存在 → `<unknown>/50%` |
| `spec.metrics[].resource.target.averageValue` | Quantity | | 目标绝对值 | 用错类型 → 语义错但不报错 |
| `spec.metrics[].containerResource.container` | string | | 容器名（`ContainerResource` 专用） | 写错容器名 → 取不到指标 |
| `spec.metrics[].pods.metric.name` | string | | 自定义指标名（**需要自定义指标 API**） | 没有 Adapter → **指标取不到** |
| `spec.metrics[].pods.target.type` | enum | ✅ | `AverageValue`（**Pods 类型用原始值，不用百分比**） | 写成 `Utilization` → **语义错**（Pods 类型无 requests 分母概念） |
| `spec.metrics[].external.metric.name` | string | | 外部指标名 | 同上 |
| `spec.metrics[].object.describedObject` | object | | 描述的对象（kind/name/apiVersion） | 写错 → 取不到 |
| `spec.behavior` | object | | 伸缩行为（见 4.2） | 不写 → 用默认值（扩 0s / 缩 300s） |
| `status.currentReplicas` | int | | HPA 认为的当前副本数 | 只读 |
| `status.desiredReplicas` | int | | HPA 算出来的目标副本数 | **和 `currentReplicas` 不一致 = 正在等下一个周期或被限流** |
| `status.currentMetrics[]` | array | | 最近的指标值（**`<unknown>` 就是这里**） | — |
| `status.conditions[]` | array | | `AbleToScale` / `ScalingActive` / `ScalingLimited` | `ScalingActive: False` → **取指标失败** |

### 4.2 behavior / HPAScalingRules / HPAScalingPolicy

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `spec.behavior.scaleUp` | object | | 扩容策略组 | 不写 → 用默认 |
| `spec.behavior.scaleDown` | object | | 缩容策略组 | 不写 → 用默认（300s 窗口） |
| `*.stabilizationWindowSeconds` | int | | 稳定窗口秒数，**取值 0 ~ 3600** | 超过 3600 → **API 拒绝**；设 0（扩容）= 不做稳定化 |
| `*.selectPolicy` | enum | | `Max` / `Min` | 写错枚举 → API 拒绝 |
| `*.policies[]` | []HPAScalingPolicy | | 速率策略列表 | 不写 → 用默认（扩 15s 翻倍或 +4；缩 15s 可全删） |
| `policies[].type` | enum | ✅ | `Percent`（按当前副本百分比）/ `Pods`（按绝对个数） | 写错 → API 拒绝 |
| `policies[].value` | int | ✅ | 允许的变化量，**必须 > 0** | 写 0 → API 拒绝 |
| `policies[].periodSeconds` | int | ✅ | 窗口秒数，**> 0 且 ≤ 1800** | 超过 1800 → API 拒绝 |
| `policies[].type: Percent` | — | | 按**当前副本数的百分比** | 副本少时百分比粒度粗（1 个 Pod 时 100% = 加 1 个） |
| `policies[].type: Pods` | — | | 按**绝对个数** | 副本多时太慢 |

**三种 policy 的取舍（面试点）**：

| 写法 | 效果 | 适合 |
|---|---|---|
| `Percent: 100` / 15s | 每 15 秒最多翻倍 | **副本少（< 10）**，追求弹性速度 |
| `Pods: 4` / 15s | 每 15 秒最多加 4 个 | 副本中等，**防止一次加太多打爆节点** |
| `Pods: 1` / 60s | 每分钟最多减 1 个 | **缩容极保守**（金融/核心服务） |

> **注意 `Percent` 的分母是"当前副本数"** —— 1 个副本时 `Percent: 100` 就是"加 1 个"，10 个副本时是"加 10 个"。**所以固定 `Pods` 在小副本场景更可预测。**

### 4.3 PodDisruptionBudget

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: policy/v1` | string | ✅ | API 组与版本 | `policy/v1beta1` → 旧版本，已废弃 |
| `kind: PodDisruptionBudget` | string | ✅ | 对象类型 | — |
| `metadata.name` | string | ✅ | PDB 名字 | — |
| `spec.minAvailable` | int/str | | 驱逐后至少保留多少可用 Pod（**支持百分比**） | 与 `maxUnavailable` **同时写 → API 拒绝** |
| `spec.maxUnavailable` | int/str | | 驱逐后最多允许多少不可用（**支持百分比**） | 同上 |
| `spec.selector.matchLabels` | map | | 选哪些 Pod | **写成 `null` → 一个都不选**；写成 `{}` → 整个命名空间全选 |
| `spec.unhealthyPodEvictionPolicy` | enum | | 驱逐不健康 Pod 的策略 | 影响不健康 Pod 能否被快速清掉 |
| `status.currentHealthy` | int | | 当前健康数 | 只读 |
| `status.desiredHealthy` | int | | 期望最少健康数 | 只读 |
| `status.disruptionsAllowed` | int | | **当前还允许驱逐几个** | **= 0 表示现在不能驱逐**（不是"PDB 没生效"） |
| `status.expectedPods` | int | | 预算覆盖的 Pod 总数 | 只读 |

---

## 5. 横向对比

### 5.1 HPA vs VPA vs cluster-autoscaler

**三个"自动伸缩"经常被混为一谈。它们改的东西完全不同，层级也不同。**

| 维度 | **HPA** | **VPA** | **cluster-autoscaler** |
|---|---|---|---|
| **中文名** | 水平 Pod 自动扩缩容 | 垂直 Pod 自动扩缩容 | 集群自动扩缩容 |
| **改什么** | **副本数**（加/减 Pod） | **单个 Pod 的 requests/limits** | **节点数**（加/减机器） |
| **改哪个字段** | `Deployment.spec.replicas` | 容器 `resources.requests` / `limits` | 云厂商的节点池 API |
| **决策依据** | `metrics.k8s.io` 的利用率 / 业务指标 | 资源使用历史（推荐值） | **未调度 Pod 的数量**（Pending）+ 节点利用率 |
| **是不是 K8s 官方组件** | ✅ **是**（controller-manager 内置） | ❌ 独立项目（`kubernetes/autoscaler` 的 VPA 组件） | ❌ **独立项目**（`kubernetes/autoscaler`） |
| **生效延迟** | **秒级** | 需要重启 Pod（推荐模式） | **分钟级**（新节点要初始化） |
| **缩容的保护** | `behavior.scaleDown`（**不受 PDB 约束**） | 不涉及 | **受 PDB 约束**（走 Eviction API） |
| **典型事故** | 扩出来全 Pending（没节点） | 改了 requests → **调度不上** 或 **HPA 分母变化导致抖动** | 缩节点太激进 → 副本重新调度、缓存冷启动 |
| **本库对应篇目** | 本篇 | 本篇 5.1 说明 | 本篇 2.9 说明 |

**三层弹性的关系（一句话）**：

> **VPA 管"每个 Pod 有多大"，HPA 管"有几个 Pod"，cluster-autoscaler 管"有几台机器"。**
> **三层必须协同配置**：VPA 调大 requests → 装箱变紧 → HPA 扩出来的 Pod 更容易 Pending → 需要 cluster-autoscaler 兜底。

> **VPA 与 HPA 同时用同一个指标（CPU）时的经典冲突**（面试高频）：
> VPA 推荐把 requests 调大 → **HPA 的分母（requests）跟着变大** → 利用率计算下降 → HPA 不扩容 → 但 VPA 继续把 requests 调大 → **恶性循环**。
> **官方建议**：**VPA 和 HPA 不要用同一个指标做决策**（例如 VPA 用 CPU/内存，HPA 用 QPS 这类业务指标）。本篇标 `⚠️ 待验证`（该建议出自 VPA 项目文档，本篇未引用已实测的官方页面，见待验证清单）。

### 5.2 CPU 指标 vs QPS 指标

| 维度 | **CPU 利用率（Resource 指标）** | **QPS / 业务指标（Pods / External 指标）** |
|---|---|---|
| **代表什么** | **资源**用得怎么样 | **业务**要处理多少请求 |
| **分母** | **requests.cpu**（百分比） | **绝对值**（如 `averageValue: "100"` 表示 100 QPS/副本） |
| **数据源** | metrics-server（**集群自带度最高**） | **自定义指标 API**（Prometheus Adapter / KEDA） |
| **部署复杂度** | **低**（装 metrics-server 即可） | **高**（要装 Adapter + 埋点 + 暴露指标） |
| **与业务的相关性** | **间接**：CPU 高不一定忙（可能在 GC、可能在加密） | **直接**：QPS 就是用户请求数 |
| **多语言友好度** | 一般（要能反映负载的 CPU 消耗） | **最好**：不管什么语言，QPS 都是同一个口径 |
| **受 requests 影响** | ✅ **强依赖**：requests 改一个数，利用率全变 | ❌ 不受影响 |
| **受 limits 节流影响** | ✅ 受影响 | ❌ 不受影响 |
| **典型问题** | "requests 填多少合适"；"CPU 高但 QPS 没涨" | "指标埋点链路太长"；"Adapter 挂了 HPA 就瞎了" |
| **官方是否支持** | ✅ **内置**，`metrics.k8s.io` 就有 | ⚠️ 需要 **API 聚合层**（APIService）提供 `custom.metrics.k8s.io` / `external.metrics.k8s.io` |

**"替代指标" vs "业务真相"（本节的题眼）**：

| | **替代指标（surrogate metric）** | **业务真相（business truth）** |
|---|---|---|
| **是什么** | CPU、内存等**基础设施指标** | QPS、队列长度、并发数、p99 延迟等**业务指标** |
| **例子** | CPU 利用率 60% | QPS 达到 1000/副本 |
| **优点** | **零额外成本**（metrics-server 就够）、无埋点、跨语言统一 | **直接反映用户感受**、不受资源规格影响 |
| **缺点** | **需要翻译**（多少 QPS 对应多少 CPU？换语言/换机型就变了） | **要额外基建**（埋点 + 存储 + Adapter） |
| **什么时候用** | 快速起步、CPU 密集且 CPU 与负载强相关 | **生产系统的正解**（尤其是 CPU 与负载不相关时） |

**什么情况下 CPU 指标会"骗人"（必须能举例）**：

| 场景 | CPU 高但业务正常 | CPU 低但业务异常 |
|---|---|---|
| **GC / 内存压力** | Java 应用在 Full GC，CPU 打满但吞吐正常 | — |
| **加密 / 序列化** | CPU 消耗在非业务逻辑上 | — |
| **线程池自旋** | 线程在空转等下游，CPU 高但下游才是瓶颈 | — |
| **冷启动** | 预热阶段 CPU 高 | **缓存命中率低 → CPU 低但响应慢**（CPU 反而更低） |
| **IO 密集** | — | **CPU 只有 5%，但 QPS 上不去**（瓶颈在网络/磁盘） |

> **一句话**：**CPU 是"资源真相"，QPS 是"业务真相"。资源真相受部署形态影响，业务真相不受。** 生产上两者都配：CPU 做兜底（保证不会被打死），QPS 做主决策（保证用户体验）。

### 5.3 Prometheus Adapter 在哪一环

**它是"③ 之后、⑤ 之前"的那一层。**

```
③ metrics-server  ──采集──▶  ④ metrics.k8s.io （内置资源指标：CPU/内存）
                                    │
                                    ├──（另一条路）──▶  Prometheus 抓取应用的 /metrics
                                    │                        │
                                    │                        ▼
                                    │              Prometheus Adapter
                                    │              （把 Prometheus 的查询结果
                                    │                转成 K8s 自定义指标 API）
                                    │                        │
                                    │                        ▼
                                    │            ⑤ custom.metrics.k8s.io
                                    │               external.metrics.k8s.io
                                    │                        │
                                    └────────────────────────┴──▶ HPA 控制器读
```

**各层的职责对照**：

| 层 | 提供什么 | 谁实现 |
|---|---|---|
| metrics-server | `metrics.k8s.io` —— **CPU / 内存**等**资源指标** | K8s 社区（独立项目） |
| Prometheus | 存储 + 查询**应用自己暴露的指标**（QPS、队列长度…） | Prometheus 社区 |
| **Prometheus Adapter** | 把 Prometheus 的查询结果**翻译成 K8s API 格式** | `kubernetes-sigs/prometheus-adapter` |
| APIService 聚合层 | 把 `custom.metrics.k8s.io` / `external.metrics.k8s.io` **挂到 apiserver 上** | kube-apiserver |
| HPA 控制器 | 读这些 API → 算副本数 → 改 replicas | kube-controller-manager 内置 |

**用 `Pods` 类型的 HPA 走 Prometheus Adapter，完整 yaml 长这样**：

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: web-qps-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: web
  minReplicas: 2
  maxReplicas: 20
  metrics:
  - type: Pods
    pods:
      metric:
        name: http_requests_per_second      # 需与 Adapter 暴露的指标名一致
      target:
        type: AverageValue
        averageValue: "100"                # 每个 Pod 平均 100 QPS
```

> **踩坑点**：`Pods` 类型用的是**原始值**，不是百分比。**`averageValue: "100"` 的含义是"每个 Pod 平均承担 100 QPS"**，而不是"100%"。**算例**：3 个 Pod、实测总 QPS 600 → 平均 200 → `ceil(200/100) = 2`?? 不对——公式是 `ceil(currentReplicas × currentMetricValue / desiredMetricValue)`，currentMetricValue 是**每 Pod 平均值**，所以 `ceil(3 × 200 / 100) = 6`。**结果正确。**
>
> **另一个必须知道的点**：`Pods` 类型的指标**由 Adapter 暴露给 apiserver**，HPA 通过 `custom.metrics.k8s.io` 读取。**Adapter 挂了，HPA 的 Events 就会出现 `FailedGetPodsMetric` 或 `FailedGetExternalMetric`**，而 `kubectl top` **依然正常**（它读的是 metrics-server 的 `metrics.k8s.io`）。**这就是"top 正常但 HPA 不动"的一个常见解释。**

---

## 6. 踩坑与排障（症状 → 状态 → 真实原因）

**排障铁律：从下往上查链路。`kubectl top` 是分水岭。**

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `kubectl get hpa` 的 TARGETS 显示 **`<unknown>/50%`**，REPLICAS 不动 | HPA 正常，指标取不到或分母缺失 | 两种可能：① **`requests.cpu` 没写** → 没有分母；② **metrics-server 挂了 / APIService 不可用** → 取不到指标 | ① `kubectl describe hpa` 看 `ScalingActive` 的 Reason：提到 **missing request** → 补 `resources.requests.cpu`；② `kubectl top pods` 报错 → 去查 metrics-server（`kubectl get apiservice v1beta1.metrics.k8s.io`） | **CI 校验必填 `resources.requests.cpu`**；集群装 metrics-server 并加监控 |
| `kubectl top pods` 报 `Metrics API not available` | metrics-server 侧 | **① metrics-server 没装**（kind/kubeadm 默认没有）；② 装了但 `CrashLoopBackOff`（**kubelet 证书是自签的** → x509 报错）；③ APIService 未注册/不可用 | 查 `kubectl get pods -n kube-system -l k8s-app=metrics-server` 与其日志；自签证书场景**测试环境**可加 `--kubelet-insecure-tls`（**官方明确仅供测试**），生产必须正确配 CA | 部署清单纳入基线；给 metrics-server 配证书与健康检查；加告警 |
| **TARGETS 有值（如 20%/50%）但不扩容** | HPA 正常 | 分母被撑大：**只写 `limits` 没写 `requests`** → requests 被复制成 limits → 实际用量远低于 limits → 利用率永远低于目标 | 补 `requests.cpu`；或把 limits 调到贴近真实用量 | **requests 与 limits 分开定义**；上线前用 `kubectl top` 对照 requests 校准 |
| **TARGETS 显示 55%，目标 50%，不扩容** | HPA 正常 | **命中容差**：官方默认 tolerance=10%，比值 1.1 落在容差边界内 → 跳过伸缩 | 需要更灵敏就在 `behavior` 里显式配 `tolerance`（如 `0.05`） | 记住 10% 这个默认；不要把"不扩"直接等同于"故障" |
| HPA 不扩容，`describe hpa` 显示 `ScalingLimited: True` | HPA 正常 | **已达 `maxReplicas`**（Reason 可能是 `TooManyReplicas`） | 提高 `maxReplicas`；同时检查节点够不够加 | `maxReplicas` 按容量规划；配 cluster-autoscaler 兜底 |
| **扩出来的 Pod 全部 `Pending`** | 新 Pod `Pending` + `FailedScheduling` | **节点没余量**——HPA 只改 Pod 数，不加节点（见 2.9） | 装/调 cluster-autoscaler（ACK 用弹性伸缩组）；或加节点；或临时提 `minReplicas` 提前占位 | **两级联动一起配**；节点池留 30%+ 余量；给 Deployment 加 PDB 保护 |
| HPA 缩容卡住，副本数一直不降 | HPA 正常 | ① **缩容稳定窗口 300 秒**（默认）；② 有 Pod 不 Ready → 官方规则下缩容被抑制；③ 命中容差 | ① 等 5 分钟；② 查 Pod 的 Ready 状态与探针；③ 看 `behavior.scaleDown.tolerance` | 缩容慢是**设计**，不是 bug；要快就显式调 `stabilizationWindowSeconds` |
| **副本数来回抖动（flapping）** | 副本数在阈值附近反复 | 指标在目标附近震荡 + **容差太小**（或为 0）+ 缩容窗口太短 | 调大 `tolerance`（如 0.2）；拉长 `scaleDown.stabilizationWindowSeconds`；给应用加缓存/连接池降低抖动 | 监控副本数变化率；HPA 配 `behavior` 而不是用默认值 |
| `kubectl top` 显示 CPU 很高，HPA 却不扩 | 正常 | **不是 HPA 的问题**：CPU 高但 requests 也高 → 利用率其实不高；或容器被**节流**（limits 太小）→ 实测值被压住 | 对照 `kubectl top` 与 `kubectl describe pod` 的 requests，算真实利用率；调高 limits 或调低 requests | 监控 `container_cpu_cfs_throttled` 类指标；**记住 CPU limit 只会节流、不会杀容器** |
| HPA 扩了但**业务还是慢** | 正常 | **瓶颈不在副本数**：节点资源不足 / 下游依赖慢 / limits 节流 / 缓存冷启动 | 先看新 Pod 是否 Ready、是否真接流量（`kubectl get endpoints`） | 扩容 ≠ 解决瓶颈；容量规划要算"每副本能扛多少 QPS" |
| `kubectl auth can-i` / RBAC 报错导致 HPA 读不到指标 | — | metrics-server 的 ServiceAccount 缺 `nodes/metrics` 权限，或被 NetworkPolicy 挡住访问 kubelet 10250 | 查 metrics-server 权限与集群 NetworkPolicy | 准入策略里给 metrics-server 开特例 |
| HPA 对象创建就失败（不是 Pending） | 对象没创建 | `maxReplicas` 缺失；`metrics[].resource.target.type: Utilization` 用在了 `Pods` 类型上；`stabilizationWindowSeconds > 3600` | 按 4.1 / 4.2 的"写错的后果"列逐项核对 | 写完先 `kubectl apply --dry-run=client` |
| PodDisruptionBudget 配了但节点还是被删了 | — | ① 用的工具是 `delete` 而不是 `drain`（**官方明确 delete 绕过 PDB**）；② PDB 的 `selector` 写错（`null` = 不选任何 Pod）；③ `disruptionsAllowed` 本来就是 0 | 改用 `kubectl drain`；核对 selector 与 `status.disruptionsAllowed` | 节点维护流程统一用 `drain`；PDB 的 selector 与 Deployment 的 `spec.selector` 对齐 |
| 用 QPS 指标后 `kubectl top` 正常但 HPA 不动 | HPA 正常 | **Prometheus Adapter 没装/挂了**，或**指标名对不上** | 查 `kubectl get apiservice v1.custom.metrics.k8s.io`；查 Adapter 日志；核对指标名 | 用 `custom.metrics.k8s.io` 的 API 直接查一次指标 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/` | HPA 控制循环与 15 秒同步周期、扩容公式、容差 10%、利用率=用量/requests、指标缺失的保守处理、多指标取最大、五种指标类型 | ✅ 2026-10-24 |
| S2 | 官方 | `kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale-walkthrough/` | `kubectl get hpa` 输出格式与 TARGETS 列含义、`describe hpa` 的 Conditions 与 Events、metrics-server 前置要求 | ✅ 2026-10-24 |
| S3 | 官方 | `github.com/kubernetes-sigs/metrics-server` | metrics-server 的采集链路（GET /metrics/resource）、15 秒采集周期、内存缓存、`--kubelet-insecure-tls`（仅测试）、安装清单 | ✅ 2026-10-24 |
| S4 | 中文 | `kubernetes.io/zh-cn/docs/concepts/configuration/manage-resources-containers/` | 未指定 request 时复制 limit、CPU 节流机制与"不会杀死容器"的原文 | ✅ 2026-10-24 |
| S5 | 官方 | `kubernetes.io/docs/reference/kubernetes-api/autoscaling/horizontal-pod-autoscaler-v2/` | spec 字段、五种 metrics 类型、MetricTarget、behavior/HPAScalingRules/HPAScalingPolicy **全部默认值与取值范围** | ✅ 2026-10-24 |
| S6 | 官方 | `kubernetes.io/docs/concepts/workloads/pods/disruptions/` | PDB 的定义与"只管自愿中断"、`delete` 绕过 PDB、必须走 Eviction API | ✅ 2026-10-24 |
| S7 | 官方 | `kubernetes.io/docs/reference/kubernetes-api/policy/pod-disruption-budget-v1/` | `minAvailable`/`maxUnavailable` 语义与互斥、`selector` 的 null vs 空、status 四个字段 | ✅ 2026-10-24 |
| S8 | 官方 | `kubernetes.io/docs/reference/kubernetes-api/workload-resources/pod-v1/` | `minDomains` 默认值等 PodSpec 字段 | ✅ 2026-10-24 |
| S9 | 中文 | `kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-pods/` | 排障动作（describe / logs / exec） | ✅ 2026-10-24 |
| S10 | 官方 | `kubernetes.io/docs/reference/using-api/` | 资源清单字段语义交叉验证 | ✅ 2026-10-24 |
| S11 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/configure-hpa-for-a-deployment` | ACK 上配置 HPA 的实践形态 | ✅ 2026-10-24 |
| S12 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/cluster-autoscaler` | ACK 集群自动扩缩容（节点级）形态 | ✅ 2026-10-24 |
| S13 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/use-an-elastic-scaling-group-to-scale-a-node-pool` | ACK 弹性伸缩组（节点级）与 HPA 的两级关系 | ✅ 2026-10-24 |
| S14 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 托管版弹性实践总入口 | ✅ 2026-10-24 |
| S15 | 官方 | `github.com/kubernetes/autoscaler` | VPA 与 cluster-autoscaler 的项目归属（非官方内置） | ✅ 2026-10-24 |
| S16 | 官方 | `github.com/kubernetes-sigs/prometheus-adapter` | Prometheus Adapter 的项目归属与职责 | ✅ 2026-10-24 |

> **完整来源矩阵（含全部 15 个组件）见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。** 本篇只列用到的。
> **链接实测方法见** [`../09-参考索引/README.md`](../09-参考索引/README.md)（含 3 次重试脚本，**000 是网络抖动不是失效**）。本篇全部 URL 于 2026-10-24 用该脚本逐条实测。

### 待验证清单

| # | 待验证项 | 原因 | 怎么验 |
|---|---|---|---|
| 1 | **扩容默认 policy 的窗口是 15 秒还是 60 秒** | 官方 [S5] 同一页两处表述不一致：`HPAScalingRules.policies` 写 "15s window"，`HorizontalPodAutoscalerBehavior.scaleUp` 写 "per 60 seconds" | `kubectl explain hpa.spec.behavior.scaleUp.policies` 看本集群版本 Description；或直接显式配置 `policies` 覆盖默认值 |
| 2 | **`selectPolicy: Max` / `Min` 的精确取舍语义** | 官方 [S5] 只明确"默认 Max"，未逐字解释 Max/Min 如何在多条 policy 间取舍 | 查 `kubectl explain hpa.spec.behavior.scaleUp.selectPolicy` 的本版本 Description |
| 3 | **只写 `limits` 时 HPA 分母是否确实等于 limits** | 理论上由准入阶段的"复制 limit 到 request"保证，但官方文档未直接给这个组合的结论 | 建只写 `limits.cpu` 的 Deployment + HPA，看 TARGETS 分母 |
| 4 | **VPA 与 HPA 同指标冲突的官方建议原文** | 该建议出自 VPA 项目文档，本篇**未引用已实测的 VPA 官方页面** | 补 `github.com/kubernetes/autoscaler` 下的 VPA README 到来源索引后核对 |
| 5 | **ACK 弹性伸缩组与 HPA 的具体联动参数**（是否需要额外的 `maxPods` 类配置） | ACK 文档为前端渲染页面，**本篇未逐字核对其参数名** | 在 ACK 控制台弹性伸缩组配置页对照实际参数 |

> **单源知识点标注**：
> - **VPA 的推荐算法与生效模式**（`Auto` / `Recreate` / `InPlaceOrRecreate`） —— **⚠️ 单源待补**（索引未收录 VPA 专项页面）
> - **Prometheus Adapter 的 CRD 配置细节**（`resources` 规则怎么写、指标名怎么映射） —— **⚠️ 单源待补**（本篇只引了项目归属，未展开配置语法）

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入是"目标工作负载 + 一组指标定义"，输出是"**改 `Deployment.spec.replicas` 这一个数字**"。中间经过 7 环：cAdvisor 采集 → kubelet 暴露 → metrics-server 聚合 → APIService 注册 → HPA 控制器读 `metrics.k8s.io` → 按 `ceil(当前副本数 × 当前利用率 / 目标利用率)` 算 → 改 replicas。**每一环断了，现象都是"不扩"，但只有前四环会明确报错。**
2. **能故意改坏再倒推吗？** —— 五个实验：① 删掉 `resources.requests.cpu` → 看 TARGETS 变 `<unknown>/50%`；② 把 `requests.cpu` 改成 `1`（配合 `limits.cpu: "1"`）→ 看利用率变极低、**不扩容**；③ 把 `averageUtilization` 从 50 改成 51 → 看**命中 10% 容差**时的行为边界；④ 设 `maxReplicas: 1` → 看 `ScalingLimited: True`；⑤ 把 Deployment 的 `replicas` 手工改成 10（超过 `maxReplicas`）→ 看 HPA 把它拉回上限。**第 ② 个实验是全篇最重要的一个**——它把"分母"这件事刻进肌肉记忆。
3. **能画上下游吗？** —— 画出 `容器真实用量` → `cAdvisor` → `kubelet /metrics/resource` → `metrics-server 内存缓存` → `APIService: metrics.k8s.io` → `HPA 控制器` → `Deployment.spec.replicas` → `Deployment controller` → `新 Pod` → `调度器` → `节点`，并在每一段标出"这一环断了会看到什么"。然后在旁边画出**平行的第二条链**：Prometheus → Prometheus Adapter → `custom.metrics.k8s.io` → HPA，并标出"这条链断了时 `kubectl top` 依然正常"。

---

## 9. 常用运维命令（HPA 排障速查）

> **本节能做什么**：L8 讲 HPA 怎么算副本，本节把**弹性扩缩容日常排障命令**按场景归类——HPA 状态与指标核查、扩缩边界调整、ScalingLimited/AbleToScale 异常判读，以及"TARGETS 显示 `<unknown>`"实战排障。**HPA 的分母就是 Pod 的 `resources.requests`，分子来自 metrics-server。**

### ① HPA 状态与指标核查

```bash
kubectl get hpa -n prod -o wide                     # 看当前/目标副本、TARGETS、MIN/MAX
kubectl describe hpa web -n prod                    # 看 Metrics / Targets / Conditions / Events
kubectl get hpa web -n prod -o yaml                 # 看完整 spec 与 status
kubectl top pods -n prod                            # 真实用量（HPA 算比例的分子来源）
```

> **实战价值**：`kubectl top` 正常但 HPA 不扩 → 多半是 Deployment 没设 `resources.requests.cpu`（分母缺失）；`top` 本身 `<unknown>` → metrics-server 没装或异常，那 HPA 一定算不了。

### ② 扩缩边界与触发验证

```bash
kubectl patch hpa web -n prod -p '{"spec":{"maxReplicas":10}}'     # 调上限（scale 子命令部分版本支持）
kubectl patch hpa web -n prod -p '{"spec":{"minReplicas":2}}'      # 调下限
kubectl get hpa web -n prod -w                                  # watch 扩缩过程
# 制造负载验证：kubectl exec 进 Pod 跑压测，观察 TARGETS 与副本变化
```

> **实战价值**：调边界用 `patch` 比改 yaml 快；`-w` 能实时看到副本从 N 变到 M 的全过程，验证 HPA 是否真在工作，而不是只看静态配置。

### ③ 异常与告警场景判读

```bash
kubectl describe hpa web -n prod | grep -iA3 Condition
# ScalingLimited=True   → 触到 min/max 边界（再涨/再降都被卡）
# AbleToScale=False     → 缺 metrics 或目标 Deployment 不可用（如 replicas 为 0）
# ScalingActive=False   → 取不到指标（分母缺失/指标后端断）
kubectl get apiservice v1beta1.metrics.k8s.io        # metrics-server 聚合 API 是否 Available
```

> **实战价值**：HPA 的 Conditions 是"为什么不扩/不缩"的完整说明书。`ScalingLimited` 不是故障（只是到边界），`AbleToScale=False`/`ScalingActive=False` 才是真问题，分别指向目标不可用和指标断流。

### ④ 排障实战：HPA TARGETS 显示 `<unknown>`

```bash
kubectl top pods -n prod                    # 若也是 <unknown> → metrics-server 没装/挂了
kubectl get apiservice v1beta1.metrics.k8s.io   # 看聚合 API 状态
kubectl -n kube-system get pods | grep metrics-server
# 若 top 正常但 HPA <unknown> → 目标 Deployment 未设 resources.requests.cpu
kubectl get deploy web -n prod -o jsonpath='{.spec.template.spec.containers[0].resources.requests}'
```

> **读法**：`<unknown>` 两步定位——先看 `kubectl top`：也 unknown 是 metrics-server 链路断了；top 正常但 HPA unknown，是目标工作负载的 `requests` 缺失（HPA 没有分母，无法算利用率）。

---

**上一篇**：[`L7-调度与QoS与资源管理.md`](L7-调度与QoS与资源管理.md) · **下一篇**：[`L9-安全RBAC与合规.md`](L9-安全RBAC与合规.md) · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
