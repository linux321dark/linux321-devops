# L1 · Operator 模式与 CRD（把运维经验写成控制器）

> **本篇定位**：本套资料里第一篇**讲"怎么扩展 Kubernetes 本身"**的文档。前面的 L1/L2 讲的是"怎么用 K8s 提供的能力"，本篇讲"**当 K8s 内置能力不够时，怎么把领域运维知识沉淀成 API + 控制器**"。同时给出一个务实的判断标准：**大多数团队根本不该写 Operator**。
> **前置**：L2 K8s 架构与对象模型（期望态/实际态/协调三态模型、控制器职责）、L1 Helm（模板化封装）、L0 YAML 语法。
> **学完能做什么**：① 能说清 Operator 与 Controller 的区别（"Operator 是打包成 K8s 部署的控制器"）；② 能解释为什么 MySQL/Redis/Kafka 不能简单用 Deployment 部署；③ 能写出一个合规的 CRD（含结构化 schema、status/scale 子资源、printerColumns）；④ 能说清 reconcile 循环与 K8s 内置控制器为何完全同构；⑤ 能判断**该不该**写 Operator，并给出选型依据。
> **在 21 天里**：D16（中间件 Operator 日）。
> **环境/版本基准**：CRD 相关行为以 `apiextensions.k8s.io/v1` 为准（本地 K8s v1.37.x，kind 集群 `day2`）；官方中文文档 CRD 页要求**服务端版本不低于 1.16**。**`v1beta1` 已在 K8s 1.22 被移除，新建 CRD 一律用 `v1`。**
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
Operator 模式与 CRD
│
├─ 1. 概念
│   ├─ 1.1 一句话：把运维领域知识写成控制器，用 CRD 声明式管理有状态软件
│   ├─ 1.2 为什么中间件不能简单用 Deployment（四个 Deployment 表达不了的东西）
│   ├─ 1.3 CRD 是什么：扩展 API 而不改 K8s 本身
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 Operator 的三件套：CRD + Controller + RBAC
│   ├─ 2.2 reconcile 循环：与 K8s 内置控制器完全同构
│   ├─ 2.3 CRD 的结构：group/names/scope/versions
│   ├─ 2.4 结构化 schema：为什么必须写、写了会裁剪什么
│   ├─ 2.5 子资源：status 与 scale
│   ├─ 2.6 printerColumns：让 kubectl get 可读
│   ├─ 2.7 conversion：多版本的版本转换 webhook
│   ├─ 2.8 controller-runtime / Kubebuilder 提供什么
│   └─ 2.9 Helm vs Operator：两个不同层次
│
├─ 3. 动手：写一个最小 CRD + 观察
├─ 4. 逐字段表
│   ├─ 4.1 CRD spec 关键字段表
│   └─ 4.2 controller-runtime 核心概念表
├─ 5. 横向对比
│   ├─ 5.1 Helm vs Operator vs StatefulSet vs 云托管服务
│   ├─ 5.2 自研 Operator vs 开源 Operator
│   └─ 5.3 CRD vs CR
├─ 6. 踩坑与排障
├─ 7. 参考来源
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 一句话定义

> **Operator = 把某个软件的运维领域知识写成一个控制器，用一个自定义资源（CR）声明式地管理这个软件。**

官方中文文档的定义原文照录：

> "Operator 是 Kubernetes 的扩展软件，它利用定制资源管理应用及其组件。Operator 遵循 Kubernetes 的理念，特别是在控制器方面。"

这句话里有三个关键词，**每一个都对应一个工程含义**：

| 关键词 | 含义 | 落地的东西 |
|---|---|---|
| **扩展软件** | 它是**跑在集群里的一个 Deployment**（或 StatefulSet），不是集群内置组件 | Operator 的部署形态就是一个普通工作负载 |
| **利用定制资源管理** | 用户不直接操作 Pod，而是操作一个 **CR**（如 `SampleDB`） | **CRD + CR** |
| **遵循控制器理念** | 它跑的是 **reconcile 循环**：比对期望态与实际态，动手拉齐 | **Controller** |

**再精确一点**（官方原文）：

> "Operator 是 Kubernetes API 的客户端，充当自定义资源的控制器。"

> "Operator 的核心是告诉 API 服务器，**如何使现实与代码里配置的资源匹配**。"

**最后这半句是全文的钥匙**：Operator 不"部署"软件，它**让现实与配置匹配**。这和 Deployment 做的事在机制上完全一样，区别只在于**它匹配的对象和规则复杂得多**。

### 1.2 为什么中间件不能简单用 Deployment

官方文档给的 SampleDB 例子，把这件事讲得最清楚：

> "如果添加新的 SampleDB，Operator 将设置 PersistentVolumeClaims 以提供持久化的数据库存储，设置 StatefulSet 以运行 SampleDB，并设置 Job 来处理初始配置。"
>
> "**如果你删除它，Operator 将建立快照，然后确保 StatefulSet 和 Volume 已被删除。**"

**请注意最后那句——删除时先建快照。**这就是 Deployment 做不到的事。

**Deployment 表达不了的四件事**：

| # | 需求 | 具体是什么 | Deployment 能不能表达 |
|---|---|---|---|
| **1** | **有状态、有身份** | 主从架构里谁是主、谁是从、Pod 序号决定谁是 leader | ❌ Deployment 的 Pod 是**无身份的随机名**，随时可被删重建 |
| **2** | **备份与恢复** | 删库前必须先做快照；恢复时要按顺序拉起 | ❌ Deployment 只有"删 Pod"，没有"删之前先备份"这个语义 |
| **3** | **主从切换（failover）** | 主库挂了要把某个从库**提升**为主，并改写客户端连接指向 | ❌ Deployment 只会"再起一个一样的"，**不知道哪个该当主** |
| **4** | **扩缩容时的数据搬迁** | 从 3 副本扩到 5 副本，要**先迁数据再上副本**；缩容要**先迁走数据再下线** | ❌ Deployment 扩缩容是"直接起/杀 Pod"，**不知道数据在哪** |

**再加两条"运维语义"**：

| # | 需求 | 说明 |
|---|---|---|
| **5** | **版本升级的中间步骤** | 数据库从 v11 升到 v12 不是换个镜像就行，中间可能要跑 `pg_upgrade`、改参数、等迁移完成。官方原文明确提到："代码将检查数据库是否正运行在旧版本上，如果是，则创建 Job 对象为你升级数据库" |
| **6** | **对外暴露一个稳定的"有状态服务"** | 官方原文提到："发布一个 Service，要求不支持 Kubernetes API 的应用也能发现它" |

> **关键认知**：StatefulSet 解决了 **#1（稳定身份 + 稳定存储 + 有序启停）**，但**没有解决 #2 到 #6**。**StatefulSet 是"有序的 Pod 集合"，Operator 是"懂这个软件的运维系统"。**
>
> **面试能这么答**："StatefulSet 解决了'有状态 Pod 怎么有序起来'，但它不知道 MySQL 主从怎么切、备份怎么做、升级要走哪几步。**Operator 补的就是这层领域知识**——它本质上是一个用 Go/Python/Java 写的、跑在集群里的运维脚本平台，只不过被做成了声明式 API。"

**用 Deployment 硬上的典型翻车剧本**：

```
# 一个"能跑起来"的 MySQL on Deployment
Deployment(mysql) + Service(mysql) + PVC(shared)

# 三个月后：
① 三副本共用一个 PVC → 数据竞争，数据损坏
② 主库挂了 → 三个副本互相脑裂，都认为自己是主
③ 升版本 → 直接换镜像 → 数据目录版本不兼容 → 起不来
④ 要备份 → 没人知道该怎么备份，只能进容器 mysqldump
⑤ 要扩容 → 直接加副本 → 新副本没有数据
```

**这五个问题没有一个是"多起几个 Pod"能解决的**——它们需要**懂 MySQL 的代码**。

### 1.3 CRD 是什么

**CRD（CustomResourceDefinition）= 告诉 apiserver"世界上还有这么一种资源"。**

官方原文：

> "当你创建新的 CustomResourceDefinition（CRD）时，Kubernetes API 服务器会为你所指定的每个版本生成一个新的 RESTful 资源路径。"

官方文档对 CRD 的一句话定位：

> "本页展示如何使用 CustomResourceDefinition 将定制资源（Custom Resource）安装到 Kubernetes API 上。"

**关键认知：CRD 扩展的是 API，不是 K8s 本身。**

| | 扩展 K8s 核心代码 | 用 CRD 扩展 API |
|---|---|---|
| **需要改什么** | 改 apiserver、重新编译、发新版本 | **什么都不改**，apply 一个 CRD 对象 |
| **发版周期** | 跟随 K8s 大版本（半年一次） | **随时** |
| **谁批准** | K8s 社区 | 你自己 |
| **能力边界** | 无限制 | 受 apiserver 的 CRD 能力约束（schema 校验、剪裁、版本转换、子资源） |

**CRD 与 CR 的关系（这一条必须掰开）**：

| | **CRD（类型定义）** | **CR（实例）** |
|---|---|---|
| **是什么** | `CustomResourceDefinition` 对象 | 你用这个类型创建的实例 |
| **类比** | **Java 的 class** | **new 出来的对象** |
| **数量** | 一个类型一个 CRD（集群级） | **可以有无数个** |
| **谁写** | Operator 作者写 CRD | 用户/运维写 CR |
| **作用域** | **集群级、无命名空间**（官方原文："CustomResourceDefinitions 本身是无名字空间的，可在所有名字空间中访问"） | 由 CRD 的 `spec.scope` 决定 |

**CRD 名字有强制格式**（官方原文）：

> "名字必需与下面的 spec 字段匹配，并且格式为 **`<名称的复数形式>.<组名>`**。"

所以 `group: stable.example.com` + `plural: crontabs` → CRD 名必须是 `crontabs.stable.example.com`。

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| **Operator** | 操作器/运维器 | 打包成 K8s 部署的控制器，用 CR 管理一个软件 |
| **Custom Resource (CR)** | 定制资源 | 用户声明的对象，如 `CronTab`、`SampleDB` |
| **CustomResourceDefinition (CRD)** | 定制资源定义 | **类型的定义**，让 apiserver 认识一种新资源 |
| **Reconcile / Reconciliation** | 协调 / 对账 | 期望态与实际态比对并拉齐的循环 |
| **Controller** | 控制器 | 跑 reconcile 循环的程序 |
| **Status Subresource** | 状态子资源 | 独立的 `/status` 端点，让状态更新不覆盖 spec |
| **Scale Subresource** | 扩缩容子资源 | 暴露 `/scale` 端点，让 CR 能接 HPA/`kubectl scale` |
| **Structural Schema** | 结构化模式 | `openAPIV3Schema`，`v1` 中**必需** |
| **Pruning / 字段剪裁** | 剪裁 | **Schema 里没定义的字段在保存前被删除** |
| **Preserve Unknown Fields** | 保留未知字段 | `x-kubernetes-preserve-unknown-fields: true`，关闭剪裁 |
| **Printer Columns** | 打印列 | `additionalPrinterColumns`，定制 `kubectl get` 的输出列 |
| **Conversion Webhook** | 版本转换 webhook | 把不同 API 版本的对象互相转换 |
| **Served / Storage Version** | 提供版本 / 存储版本 | `served` 决定能否通过该版本访问；`storage` 决定 etcd 里存哪个版本 |
| **Level-Based vs Edge-Based** | 水平触发 vs 边缘触发 | Kubebuilder 书的说法：水平式是"看当前状态，缺什么补什么" |
| **Finalizer** | 终结器 | 删除前的异步回调钩子（CR 也支持） |
| **controller-runtime** | controller 运行时库 | `sigs.k8s.io/controller-runtime`，Go 生态的 Operator 框架 |
| **Kubebuilder** | 脚手架 | 用 `controller-runtime` 生成 Operator 项目的脚手架工具 |

---

## 2. 机制

### 2.1 Operator 的三件套

一个能跑的 Operator，最少需要三样东西：

```
┌─────────────────────────────────────────────────────────┐
│  ① CRD（apiextensions.k8s.io/v1）                        │
│     定义 API：类型长什么样、有哪些字段、什么作用域           │
│     → 让 apiserver 认识 CronTab 这种资源                    │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  ② Controller（跑在集群里的 Deployment）                  │
│     逻辑：watch CR → 算出该做什么 → 动手 → 回写 status      │
│     → 真正执行"运维知识"的地方                            │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  ③ RBAC（ServiceAccount + Role/ClusterRole + Binding）    │
│     权限：Controller 能 create/delete 哪些资源              │
│     → 没有它，Controller 连 PVC 都创建不出来                 │
└─────────────────────────────────────────────────────────┘
```

**三者的关系是"缺一不可"**：

| 缺了谁 | 现象 |
|---|---|
| **缺 CRD** | `kubectl get crontab` → `no matches for kind` / `the server doesn't have a resource type` |
| **缺 Controller** | CR 能创建成功，但**什么都不会发生**（没有 Deployment/StatefulSet 被创建） |
| **缺 RBAC** | Controller 日志里一片 `forbidden: cannot create resource "persistentvolumeclaims"` |

> **第三条是新手最容易忘的**：CRD 和 CR 创建都成功、UI 里也看得到 CR，**但 Operator 什么都不做**——因为它没有权限。**排障第一步永远是看 Operator 的日志，而不是看 CR。**

**"Operator 可以用任何语言写"**（官方原文）：

> "你还可以使用**任何支持 Kubernetes API 客户端的语言或运行时**来实现 Operator（即控制器）。"

官方页面列出的实现库（原文照录，按字母序）：

| 语言/生态 | 项目 |
|---|---|
| Java | **Java Operator SDK** |
| Python | **Kopf (Kubernetes Operator Pythonic Framework)** |
| Rust | **kube-rs** |
| .NET | **KubeOps (.NET operator SDK)** |
| Go | **Operator Framework (OSDK)**、**kubebuilder** |
| 通用/其他 | **Charmed Operator Framework**、**Mast**、**Metacontroller**（可与 Webhook 结合）、**shell-operator** |

> **Go 是事实标准**（Kubernetes 自身就是 Go 写的），生态最完整；**Python 的 Kopf 在小团队里上手最快**。

### 2.2 reconcile 循环：与 K8s 内置控制器完全同构

**这是本篇最重要的一段**。官方 Operator 文档说 Operator "遵循 Kubernetes 的理念，**特别是在控制器方面**"。

**把它和 L2 的三态模型对齐**：

```
        你写 CR（期望态）
           │
           ▼
    ┌──────────────┐   controller 不断比对   ┌──────────────┐
    │   期望态      │ ◄─────────────────────► │   实际态      │
    │  (CR.spec)   │        差异 → 动手        │ (集群里真实   │
    │  存在 etcd   │                          │  的 PV/STS/Job)│
    └──────────────┘                          └──────────────┘
           │                                        │
           │ apiserver 写入                          │ controller 执行
           ▼                                        ▼
    ┌────────────────────────────────────────────────────────┐
    │  真实运行态：PVC、StatefulSet、Job、备份快照              │
    └────────────────────────────────────────────────────────┘
```

**逐字对照内置控制器**：

| 角色 | K8s 内置的 deployment-controller | 你写的 Operator controller |
|---|---|---|
| **监听谁** | Deployment | **你的 CR** |
| **期望态** | `spec.replicas` / `spec.template` | **`spec.*`（你自己定义）** |
| **实际态** | 实际的 Pod 数量与版本 | **实际的 PVC/StatefulSet/Job/数据状态** |
| **协调动作** | 建/删 Pod | **建 PVC、建 StatefulSet、建 Job、做快照、切主** |
| **状态回写** | `status.replicas` / `status.conditions` | **`status.*`（你自己定义）** |
| **水平触发** | ✅ | ✅（Kubebuilder 称之为 **Level-Based**） |

> **Kubebuilder 书里明确点出了这个分类**（原文照录）：
> - "**Level-Based vs Edge-Based APIs**" 是它讲 API 设计原则的章节之一。
> - Level-Based（水平式）："看当前状态，**缺什么补什么**"，不依赖事件序列。
> - Edge-Based（边缘式）："收到这个事件，就做这个动作"。
>
> **Operator 必须是 Level-Based 的**——这是它能被"重启后自动恢复"的前提。Kubebuilder 书把它列为 API 设计原则之一：**"Facilitation of adaptive / self-healing APIs that continuously respond to changes in the system state without user intervention."**（促成能自适应/自愈的 API，无需人工干预即可持续响应系统状态变化）

**为什么 Level-Based 是硬要求**：

```
场景：Operator Pod 重启了，期间 CR 变过 5 次

❌ Edge-Based（事件驱动）：
   事件丢了 → 状态永久不一致 → 必须人工介入

✅ Level-Based（水平式）：
   重启后重新读一遍 CR 的期望态和集群的实际态
   → 发现差异 → 补齐 → 一致
```

**一次 reconcile 的标准骨架**（Go / controller-runtime 视角）：

```go
func (r *CronTabReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    // ① 读期望态：按 req 拿到那个 CR
    var ct CronTab
    if err := r.Get(ctx, req.NamespacedName, &ct); err != nil {
        // 找不到通常意味着被删了 —— 不当作错误
        return ctrl.Result{}, client.IgnoreNotFound(err)
    }

    // ② 读实际态：集群里现在有什么
    var sts StatefulSet
    err := r.Get(ctx, ..., &sts)

    // ③ 求差
    if sts.Spec.Replicas != ct.Spec.Replicas { ... }

    // ④ 动手
    if err := r.Create(ctx, newPVC(...)); err != nil { return ctrl.Result{}, err }

    // ⑤ 回写状态
    ct.Status.Phase = "Ready"
    if err := r.Status().Update(ctx, &ct); err != nil { ... }

    // ⑥ 决定要不要再查一次
    return ctrl.Result{RequeueAfter: 30 * time.Second}, nil
}
```

**三个必须记住的工程细节**：

| 细节 | 说明 | 写错的后果 |
|---|---|---|
| **NotFound 要忽略** | CR 被删了，`Get` 返回 NotFound 是**正常流程** | 当成 error 返回 → **日志刷满 error，控制器被认为不健康** |
| **不要在 Reconcile 里做长耗时操作** | 备份、大数据迁移是分钟级的 | **阻塞整个 worker 队列**，其他 CR 全卡住 |
| **必须能重入（idempotent）** | 同一个 CR 可能被 reconcile 多次 | 非幂等 → **重复创建资源**（比如建了两个 PVC） |

**长耗时操作的标准做法**：Operator 不自己干，而是**创建一个 Job**，然后在后续 reconcile 里检查 Job 状态：

```
Reconcile #1: 发现没备份 → 创建 Job（记到 status 里）→ return
Reconcile #2: watch 到 Job 完成 → 继续下一步
```

> **这正是官方 SampleDB 例子描述的模式**："设置 Job 来处理初始配置"、"创建 Job 对象为你升级数据库"。**Job 是 Operator 处理长耗时动作的标准载体**——因为 Job 有完成状态、有重试语义、有日志。

### 2.3 CRD 的结构：四个必需块

一个 CRD 的骨架长这样（字段名全部来自官方 CRD 文档）：

```yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: crontabs.stable.example.com       # ← 必须是 <plural>.<group>
spec:
  group: stable.example.com                # ← API 组
  scope: Namespaced                        # ← Namespaced | Cluster
  names:
    plural: crontabs                       # ← 复数形式，用于 URL
    singular: crontab                      # ← 单数形式，命令行别名
    kind: CronTab                          # ← CamelCase，清单里用这个
    shortNames:
      - ct                                 # ← kubectl 短别名
  versions:
    - name: v1
      served: true                         # ← 是否通过 API 提供
      storage: true                        # ← 是否作为 etcd 存储版本
      schema:
        openAPIV3Schema:                   # ← v1 中必需
          type: object
          properties:
            spec:
              type: object
              properties:
                cronSpec:
                  type: string
                  pattern: '^(\d+|\*)(/\d+)?(\s+(\d+|\*)(/\d+)?){4}$'
                replicas:
                  type: integer
                  minimum: 1
                  maximum: 10
      subresources:
        status: {}
        scale:
          specReplicasPath: .spec.replicas
          statusReplicasPath: .status.replicas
          labelSelectorPath: .status.labelSelector
      additionalPrinterColumns:
        - name: Spec
          type: string
          description: The cron spec defining the interval a CronJob is run
          jsonPath: .spec.cronSpec
        - name: Replicas
          type: integer
          description: The number of jobs launched by the CronJob
          jsonPath: .spec.replicas
        - name: Age
          type: date
          jsonPath: .metadata.creationTimestamp
```

**逐块说明**：

| 块 | 作用 | 官方原文依据 |
|---|---|---|
| `spec.group` | API 组，URL 是 `/apis/<group>/<version>/...` | 官方注释："组名称，用于 REST API" |
| `spec.names.plural` | 复数形式，**用于 REST API URL 和资源名** | 官方注释照录 |
| `spec.names.kind` | 通常单数 CamelCase，**清单里用这个** | 官方注释照录 |
| `spec.names.shortNames` | kubectl 短别名（如 `ct`） | 官方注释照录 |
| `spec.scope` | `Namespaced` 或 `Cluster` | 官方原文照录 |
| `spec.versions[].served` | 是否通过 API Server 提供该版本 | 官方原文照录 |
| `spec.versions[].storage` | **其中一个且只有一个**必须是存储版本 | 官方原文照录 |
| `spec.versions[].schema.openAPIV3Schema` | 结构化 OpenAPI v3 Schema | **v1 中必需** |
| `spec.versions[].subresources` | 启用 `status` / `scale` 子资源 | 官方原文照录 |
| `spec.versions[].additionalPrinterColumns` | `kubectl get` 的输出列 | 官方原文照录 |

**`served` 与 `storage` 的区别（必须理解）**：

| | `served` | `storage` |
|---|---|---|
| **含义** | 这个版本还能不能通过 API 访问 | etcd 里存的是这个版本吗 |
| **可以几个 true** | **多个**（多版本共存） | **恰好一个** |
| **用途** | 灰度迁移：先同时提供 v1alpha1 和 v1，让客户端慢慢升级 | 决定落盘的版本 |

**版本演进的标准路径**：

```
v1alpha1 (served=true, storage=true)     ← 起点
   ↓ 加 v1beta1
v1alpha1 (served=true,  storage=false)
v1beta1  (served=true,  storage=true)
   ↓ 加 v1
v1alpha1 (served=false, storage=false)   ← 停止提供，但已存的 CR 仍在
v1beta1  (served=true,  storage=false)
v1        (served=true,  storage=true)
```

> ⚠️ **`served=false` 不等于对象被删除**——etcd 里存的还是 storage 版本的对象，apiserver 负责在读取时转换。

### 2.4 结构化 schema：为什么必须写

**官方原文**（这句话是硬约束）：

> "在 `apiextensions.k8s.io/v1` 版本中，CustomResourceDefinition 的这一结构化模式定义是**必需的**。在 CustomResourceDefinition 的 beta 版本中，结构化模式定义是可选的。"

**schema 承担三件事**（官方原文）：

| 作用 | 官方原文 | 不写会怎样 |
|---|---|---|
| **合法性检查** | "你可以设置一个模式（Schema），在创建和更新定制对象时，这一模式会被用来对对象内容进行合法性检查" | 什么都传，**后端拿到什么就是什么** |
| **字段剪裁** | "如果你指定了 API 服务器所无法识别的字段，则该未知字段会在保存资源之前被**剪裁（Pruned）掉（删除）**" | **未知的字段被悄悄删掉** |
| **客户端发现** | `kubectl explain`、IDE 补全、OpenAPI 文档 | 没人知道这个 CR 有哪些字段 |

**剪裁的官方示例**：

```yaml
spec:
  cronSpec: "* * * * */5"
  image: my-awesome-cron-image
  someRandomField: 42        # ← Schema 里没有这个字段
```

**`someRandomField` 不会保存到 etcd。**

> **这是本篇最反直觉的一条**：**"写错的字段不会报错，只会被静默丢弃。"**
>
> **什么场景会踩**：① 你在 CRD 里加了一个新字段但忘了重新 apply CRD → **用户填的值被静默剪掉**；② 你在 CR 里写了 `replica`（少个 s）而不是 `replicas` → **不报错，就是不生效**；③ 你把 CRD 升级到 v1 但忘了补 schema → **整个 spec 里的字段全被剪掉**。

**剪裁怎么关掉**（官方原文）：

```yaml
type: object
properties:
  json:
    x-kubernetes-preserve-unknown-fields: true
```

这样 `json` 字段可以保存任意 JSON 内容而不被剪裁。

**另外两个结构化扩展**（官方原文）：

| 扩展 | 用途 | 官方示例 |
|---|---|---|
| `x-kubernetes-int-or-string: true` | 同时允许整数和字符串（**该节点不受"必须有 type"的限制**） | `foo: {x-kubernetes-int-or-string: true}` |
| `x-kubernetes-embedded-resource: true` | 在 CR 里嵌入完整的 K8s 对象 | `foo: {apiVersion: v1, kind: Pod, spec: {...}}`。**设置后 `apiVersion`、`kind`、`metadata` 都隐式设定并隐式做合法性验证** |

**结构化 schema 的硬性规则**（官方原文列出违反后的现象）：

| 规则 | 违反后的现象 |
|---|---|
| 对象根、对象属性、数组元素**必须设置非空的 `type`**（除非用 `x-kubernetes-int-or-string` 或 `x-kubernetes-preserve-unknown-fields`） | CRD 的 **`NonStructural` 状况**中会包含报告信息 |
| 在 `allOf`/`anyOf`/`oneOf`/`not` 上下文内，**不设置** `description`、`type`、`default`、`additionalProperties`、`nullable` | 同上 |
| 如果设置了 `metadata`，**只能对 `metadata.name` 和 `metadata.generateName` 设置约束** | 同上 |

> **`NonStructural` 状况是 CRD 的健康检查项**。CRD 变成 NonStructural 时，**apiserver 会拒绝新的 CR 写入**（存量还在）。所以"CRD apply 成功但 CR 创建失败"，**第一件事是 `kubectl get crd <name> -o yaml` 看 `status.conditions`**。

### 2.5 子资源：status 与 scale

#### status 子资源

**它解决什么问题**：**让"改状态"和"改配置"走两条路，互不覆盖。**

官方原文照录：

> - 对 `/status` 的 `PUT` 请求**只检查 `status` 内容**；
> - 对主资源的 `PUT`、`POST`、`PATCH` 请求**会忽略对 `status` 内容的改变**；
> - 除非变更针对 `.metadata` 或 `.status`，否则 `.metadata.generation` 会增加。

**为什么必须有它**（不用的后果）：

```
没有 status 子资源时：
  用户改 spec（改副本数）
     ↓
  Controller 同时在写 status（写健康状态）
     ↓
  两者用同一个 endpoint 写整对象
     ↓
  ⚠️ 两边互相覆盖：Controller 拿着缓存里的旧 spec 写回去 → 用户刚改的副本数被冲掉
```

**CRD 侧的配置**：

```yaml
versions:
  - name: v1
    served: true
    storage: true
    schema:
      openAPIV3Schema:
        type: object
        properties:
          spec:
            type: object
            properties:
              replicas:
                type: integer
          status:
            type: object
            properties:
              replicas:
                type: integer
              labelSelector:
                type: string
    subresources:
      status: {}
```

**配了之后会多出这个端点**（官方原文照录）：

```
/apis/stable.example.com/v1/namespaces/*/crontabs/status
```

#### scale 子资源

**它解决什么问题**：**让你的 CR 能接 HPA 和 `kubectl scale`。**

官方原文：

> "`/scale` 使用 `autoscaling/v1.Scale`。"

| 字段 | 映射到 | 约束（官方原文） |
|---|---|---|
| `specReplicasPath` | `scale.spec.replicas` | **必填**；只能用在 `.spec` 下的 JSON 路径；只能带句点的路径；**路径下没值时，对 `/scale` 执行 GET 会返回错误** |
| `statusReplicasPath` | `scale.status.replicas` | **必填**；只能用在 `.status` 下；只能带句点的路径；**路径下没值时副本状态默认是 `0`** |
| `labelSelectorPath` | `Scale.Status.Selector` | **可选**；**必须设置后才能供 HPA 和 VPA 使用**；可在 `.status` 或 `.spec` 下；路径不存在时 Selector 默认为空字符串；目标字段必须是**字符串** |

**配了之后**（官方原文照录）：

```
kubectl scale --replicas=5 crontabs/my-new-cron-object
```

会把 CR 的 `.spec.replicas` 设为 `5`。

**这一条和本套资料的统一口径直接相关**：本套资料讲 HPA 时说**"HPA 分母是 `requests.cpu`"**——那是**对有 Pod 模板的工作负载**说的。而**当 HPA 的目标是一个 CR 时**，HPA 走的是 `/scale` 子资源：

| 维度 | HPA target 是 Deployment | HPA target 是你的 CR |
|---|---|---|
| **扩缩容走哪条路** | 改 `spec.replicas` | **改 `/scale` 的 `spec.replicas`，最终落到 `specReplicasPath` 指定的字段** |
| **读当前副本数** | 读 Deployment 的 `status.replicas` | 读 `statusReplicasPath` 指定的字段 |
| **选 Pod** | 读 Deployment 的 selector | 读 `labelSelectorPath` 指定的字段 |
| **分母** | `requests.cpu`（省略时退化为 limits，两者都不写则无分母） | **同样是 Pod 的 `requests.cpu`** —— 分母逻辑不变，但**"Pod"从哪来变成了 Operator 的责任** |

> **这里有一个真实的设计约束**：`labelSelectorPath` 缺失或返回空 → **HPA 选不到 Pod** → **HPA 永远算不出利用率**。这跟 L2 里"漏写 `requests.cpu` → HPA 显示 `<unknown>/50%`"是同一类问题：**一个字段缺失导致 HPA 静默失效，不报错**。
>
> **面试能这么答**："CR 想接 HPA，光有 `scale` 子资源不够，`labelSelectorPath` 必须指向一个**真实反映 Pod 选择器的字符串字段**——Operator 得在 status 里维护它。这和 Deployment 里 `requests.cpu` 漏写导致 HPA 失效是同一类问题：**HPA 的输入字段缺失时，它不会报错，只会安静地不工作。**"

### 2.6 printerColumns：让 `kubectl get` 可读

**默认情况下**，一个自定义资源在 `kubectl get` 里只有 `NAME` 和 `AGE` 两列——**完全看不出状态**。printerColumns 就是补这个。

```yaml
      additionalPrinterColumns:
        - name: Spec
          type: string
          description: The cron spec defining the interval a CronJob is run
          jsonPath: .spec.cronSpec
        - name: Replicas
          type: integer
          description: The number of jobs launched by the CronJob
          jsonPath: .spec.replicas
        - name: Age
          type: date
          jsonPath: .metadata.creationTimestamp
```

官方给出的效果（原文照录）：

```
NAME                 SPEC        REPLICAS   AGE
my-new-cron-object   * * * * *   1          7s
```

| 字段 | 含义 | 官方原文 |
|---|---|---|
| `name` | 输出列名称 | — |
| `type` | 列的数据类型 | 支持 `integer`/`number`/`string`/`boolean`/`date`；**值与列类型不匹配时该值会被忽略** |
| `description` | 列说明 | — |
| `jsonPath` | 取值的 JSONPath | — |
| `format` | 显示格式 | 支持 `int32`/`int64`/`float`/`double`/`byte`/`date`/`date-time`/`password` |
| `priority` | 显示优先级 | **`0` 在标准视图显示；`> 0` 只在宽视图（`-o wide`）显示** |

> **`NAME` 列是隐含的，不需要额外定义**（官方原文）。
>
> **实战建议**：把 **`phase` / `ready` / `message` 做成默认列**。因为运维排查的第一条命令永远是 `kubectl get <cr>`，**默认列里没有状态，就等于每次都要加 `-o yaml`**。

### 2.7 conversion：多版本的版本转换 webhook

**问题**：CRD 有 `v1alpha1` 和 `v1` 两个版本时，**用户用 `v1` 提交的请求，etcd 里存的是 storage 版本**（可能是 `v1alpha1`）。**读的时候又要转回来**。这个转换谁做？

**答**：`spec.conversion` 指定策略，通常是 `Webhook`。

```yaml
spec:
  conversion:
    strategy: Webhook
    webhook:
      conversionReviewVersions:
        - v1
      clientConfig:
        service:
          namespace: default
          name: example-webhook-service
          path: /convert
        caBundle: <CA_CERTIFICATE>
```

| 字段 | 含义 |
|---|---|
| `spec.conversion.strategy` | 转换策略，通常是 `Webhook`（单个版本时不需要转换，可省略） |
| `spec.conversion.webhook.conversionReviewVersions` | Webhook 支持的 `ConversionReview` API 版本 |
| `spec.conversion.webhook.clientConfig.service` | Webhook Service 的命名空间、名称、路径 |
| `spec.conversion.webhook.clientConfig.caBundle` | 用于验证 Webhook TLS 证书的 CA 证书 |

**⚠️ 关于这一节的重要说明**：官方中文 CRD 页面在"提供 CRD 的多个版本"一节**只写了一句**"详细信息可参阅 CustomResourceDefinition 的版本管理"（该链接指向的页面在中文站当前路径下**返回 404**），**没有展开 `spec.conversion` 的字段表**。上表的字段名与结构来自 **Kubebuilder/controller-runtime 生态的通用实践**，**⚠️ 属于单源待补**，落地时请以你所用版本的 `kubectl explain crd.spec.conversion` 输出为准。

**但这不影响本篇的主线**：**结论是"多版本 CRD 的转换是一个独立的服务（webhook）"**——这意味着：

> **写 Operator 的真实成本，往往不在 reconcile 逻辑，而在"你要维护几个 API 版本、转换规则、以及一个不能挂的 webhook"。** 这也是第 5 节"什么时候不该写 Operator"的核心论据之一。

### 2.8 controller-runtime / Kubebuilder 提供什么

**Kubebuilder 是什么**（官方 Kubebuilder 书原文照录）：

> "Kubernetes APIs provide consistent and well-defined endpoints for objects adhering to a consistent and rich structure."
>
> "**Users of Kubernetes** will develop a deeper understanding of Kubernetes through learning the fundamental concepts behind how APIs are designed and implemented. This book will teach readers how to develop their own Kubernetes APIs and the principles from which the core Kubernetes APIs are designed."

它列出的"核心 Kubernetes API 设计原则"（原文照录的章节名）：

- The structure of Kubernetes APIs and Resources
- API versioning semantics
- Self-healing
- Garbage Collection and Finalizers
- Declarative vs Imperative APIs
- **Level-Based vs Edge-Based APIs**
- Resources vs Subresources

**而 Operator 开发者的收获**（原文照录）：

- How to batch multiple events into a single reconciliation call
- How to configure periodic reconciliation

> **Kubebuilder 是一本"教你像 Kubernetes 团队一样设计 API"的书，同时提供脚手架工具。**

**Kubebuilder 工具链提供的东西**：

| 提供 | 说明 |
|---|---|
| **项目脚手架** | `kubebuilder init` + `kubebuilder create api` 生成项目骨架 |
| **CRD 自动生成** | **Go 结构体 + kubebuilder 注解 → 生成 CRD yaml**（`make manifests`）。**这是它最大的价值：schema 和 CRD 不会失配** |
| **Controller 骨架** | 生成 Reconcile 函数框架、Manager 启动代码 |
| **Webhook 脚手架** | 生成 admission webhook 的骨架 |
| **EnvTest** | 用 `etcd` + `kube-apiserver` 二进制做**本地集成测试**，不用真集群 |
| **controller-runtime 依赖** | 直接引入 `sigs.k8s.io/controller-runtime` |

**Kubebuilder 书的两个设计原则，和本篇直接相关**：

| 原则 | 含义 | 对写 Operator 的影响 |
|---|---|---|
| **Level-Based vs Edge-Based** | 水平式（看状态补齐）vs 边缘式（按事件动作） | **Operator 必须 Level-Based**，否则重启后无法自愈 |
| **Self-healing** | "持续响应系统状态变化，无需人工干预" | **这正是 Operator 存在的理由**——它把运维脚本的"if A then B"变成了"永远检查 A 是否还成立" |

### 2.9 Helm vs Operator：两个不同层次

**这是最容易混淆的地方，必须掰开**：

| | **Helm** | **Operator** |
|---|---|---|
| **是什么** | **模板渲染 + 升级工具** | **跑在集群里的控制器** |
| **工作方式** | 渲染 yaml → `kubectl apply` → **结束** | **常驻进程**，持续对账 |
| **产物** | 一组 K8s 资源 | **一个新的 API 类型（CRD）+ 一个控制器** |
| **谁在改** | **人触发**（`helm upgrade`） | **控制器自己**（reconcile） |
| **能不能"自己发现坏了"** | ❌ **不能**。你不 `helm upgrade`，它就永远不动 | ✅ **能**。这是 Operator 的核心价值 |
| **处理状态漂移** | ❌ 没有 | ✅ **有**（这就是 Operator 的 reconcile） |
| **适合** | **无状态应用的部署与配置** | **有状态软件的运维生命周期** |

> **关键认知（一句话）**：
>
> **Helm 管的是"**无状态应用**怎么部署上去"；Operator 管的是"**有状态软件**怎么被运维"。
>
> **两者不是替代关系，是不同层次。**

**一个具体例子说明为什么 Helm 不够**：

```
你用 Helm 部署了 MySQL：
  helm upgrade my-db --set image=mysql:8.0.32

三个月后数据库要升到 8.4：
  helm upgrade my-db --set image=mysql:8.4.0
  → Pod 起来了 → 数据目录格式不兼容 → CrashLoopBackOff
  → 数据没升级 → 数据回滚不了
```

**Operator 会怎么做**（对照官方 SampleDB 描述）：

```
Operator 看到 CR 里 desiredVersion: 8.4，集群里实际是 8.0.32
  → 先检查"是不是需要先备份" → 建 Job 做快照
  → 检查"这个版本需要什么前置步骤" → 建 Job 跑 pg_upgrade
  → 滚动更新 StatefulSet
  → 观察新版本健康
  → 更新 status.version = 8.4
```

**这就是"领域知识"的含义**：**版本升级不是换个镜像，而是一个有前置条件、有中间步骤、有成功判定的过程。**

---

## 3. 动手

> **本节操作都在你自己的测试集群上做。** 本篇写作过程中未在你的环境实测，所有"看到什么算对"是判定标准，不是粘贴的终端输出。

### 3.1 一个最小的 CRD

用官方 CRD 文档里的 CronTab 例子（它是最小但完整的）：

```yaml
# resourcedefinition.yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: crontabs.stable.example.com
spec:
  group: stable.example.com
  scope: Namespaced
  names:
    plural: crontabs
    singular: crontab
    kind: CronTab
    shortNames:
      - ct
  versions:
    - name: v1
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              properties:
                cronSpec:
                  type: string
                image:
                  type: string
                replicas:
                  type: integer
      additionalPrinterColumns:
        - name: Spec
          type: string
          description: The cron spec defining the interval a CronJob is run
          jsonPath: .spec.cronSpec
        - name: Replicas
          type: integer
          description: The number of jobs launched by the CronJob
          jsonPath: .spec.replicas
        - name: Age
          type: date
          jsonPath: .metadata.creationTimestamp
```

### 3.2 创建与观察

```bash
# ① 应用 CRD
kubectl apply -f resourcedefinition.yaml

# ② 观察"建立端点"这个过程（官方原文：可能需要几秒钟）
kubectl get crd crontabs.stable.example.com -o jsonpath='{.status.conditions}'

# ③ 看 CRD 提供的 API 路径（从 API 资源列表里找）
kubectl api-resources | grep crontab
```

**看到什么算对**：
- `status.conditions` 里出现 `Established: True`（官方原文提到要监测这个状况变为 true）
- `kubectl api-resources` 里能看到 `crontabs`（以及短名 `ct`）

### 3.3 创建一个 CR

```yaml
# my-crontab.yaml
apiVersion: stable.example.com/v1
kind: CronTab
metadata:
  name: my-new-cron-object
spec:
  cronSpec: "* * * * */5"
  image: my-awesome-cron-image
```

```bash
kubectl apply -f my-crontab.yaml
kubectl get crontab
kubectl get ct -o yaml          # 官方原文：单数、复数、短名都可以用
```

**看到什么算对**：
```
NAME                 SPEC        REPLICAS   AGE
my-new-cron-object   * * * * */5   1          7s
```

### 3.4 破坏实验一：schema 写错 → 字段被静默剪裁

```yaml
apiVersion: stable.example.com/v1
kind: CronTab
metadata:
  name: my-pruned-object
spec:
  cronSpec: "* * * * */5"
  image: my-awesome-cron-image
  someRandomField: 42        # ← Schema 里没有
```

```bash
kubectl apply -f pruned.yaml
kubectl get crontab my-pruned-object -o yaml
```

**看到什么算对**：
- **apply 成功，不报错**
- `kubectl get -o yaml` 里**看不到 `someRandomField`** —— 它被剪裁掉了

> **这一条是本篇最重要的实验**：它证明了"**CRD 的 schema 是权威的，CR 里写了但 schema 里没有的字段会静默消失**"。

### 3.5 破坏实验二：CRD 装错顺序 → CR 创建失败

```bash
# 先删掉 CRD
kubectl delete -f resourcedefinition.yaml

# 此时再尝试创建 CR
kubectl apply -f my-crontab.yaml
```

**看到什么算对**：
- 报错信息指向**资源类型不存在**（形如 `no matches for kind "CronTab" in version "stable.example.com/v1"` 或 `the server could not find the requested resource`）
- **官方原文还提醒了一件事**："当你删除某 CustomResourceDefinition 时，服务器会卸载其 RESTful API 端点，并**删除服务器上存储的所有定制对象**" —— **删 CRD = 删全部 CR**

> ⚠️ **这是生产上极其危险的命令**。**删 CRD 会连带删掉所有 CR**，Operator 随后会认为"用户不要这个软件了"，从而**把 PVC、StatefulSet 这些真实资源也一并清掉**（因为 Operator 的删除逻辑通常是"CR 没了 → 删掉我建的那些"）。
>
> **删 CRD 之前必须先删 CR。**

### 3.6 验证清单

| 检查项 | 怎么看 | 期望 |
|---|---|---|
| CRD 已建立 | `kubectl get crd` 的 `status.conditions` | `Established: True` |
| API 已注册 | `kubectl api-resources` | 能看到 `crontabs` |
| 短名可用 | `kubectl get ct` | 能列出 |
| printerColumns 生效 | `kubectl get crontab` | 出现 SPEC / REPLICAS / AGE 列 |
| schema 生效 | 写一个非法值（如 `replicas: 100` 超过 maximum） | **被拒绝** |
| 剪裁生效 | 写一个 schema 里没有的字段 | **apply 成功但字段消失** |
| CRD 非结构化 | `kubectl get crd <name> -o yaml` 的 `status.conditions` | 不出现 `NonStructural` |

---

## 4. 逐字段表

### 4.1 CRD spec 关键字段表

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: apiextensions.k8s.io/v1` | string | ✅ | CRD 的 API 版本 | 用 `v1beta1` → **K8s 1.22+ 已移除该端点，直接报错** |
| `kind: CustomResourceDefinition` | string | ✅ | 资源类型 | 拼错 → `no matches for kind` |
| `metadata.name` | string | ✅ | **必须是 `<plural>.<group>`** | 不匹配 → **CRD 创建失败**（官方明确要求） |
| `spec.group` | string | ✅ | API 组，URL 是 `/apis/<group>/<version>/` | 与 `metadata.name` 不一致 → 创建失败 |
| `spec.scope` | enum | ✅ | `Namespaced` / `Cluster` | 写 `Cluster`（拼错）→ 校验失败；选错 → **CR 落在错误的作用域** |
| `spec.names.plural` | string | ✅ | 复数形式，**用于 URL** | 与 `metadata.name` 不一致 → 创建失败 |
| `spec.names.singular` | string | | 单数形式，命令行别名 | 缺失 → `kubectl get <singular>` 不可用 |
| `spec.names.kind` | string | ✅ | 通常单数 CamelCase，清单里用它 | 写成复数 → 清单 `kind` 对不上，**CR 创建失败** |
| `spec.names.shortNames[]` | list | | kubectl 短别名 | 冲突 → **另一个 CRD 的短名被抢**（kubectl 报错） |
| `spec.names.listKind` | string | | 列表类型名 | 一般不写 |
| `spec.names.categories[]` | list | | 归入 `kubectl get all` 等分组 | — |
| `spec.versions[].name` | string | ✅ | 版本名 | — |
| `spec.versions[].served` | bool | ✅ | 是否通过 API 提供该版本 | 全为 false → **CRD 不可用**；留旧的 true → **旧客户端仍能写老版本** |
| `spec.versions[].storage` | bool | ✅ | 是否作为 etcd 存储版本 | **多于一个 true → 校验失败**；**没有 true → 校验失败** |
| `spec.versions[].schema.openAPIV3Schema` | object | ✅ | **结构化 Schema（v1 中必需）** | **缺失 → CRD 创建失败**；字段不全 → **未声明字段被静默剪裁** |
| `spec.versions[].schema.openAPIV3Schema.properties.spec` | object | | 期望态字段定义 | 写错字段名 → 用户填的值被剪掉 |
| `spec.versions[].schema.openAPIV3Schema.properties.status` | object | | 状态字段定义 | 不定义 → **status 里的字段被剪裁，状态永远为空** |
| `spec.versions[].subresources.status` | object | | 启用 `/status` 端点 | **不启用 → Controller 写 status 会覆盖用户的 spec 改动** |
| `spec.versions[].subresources.scale.specReplicasPath` | string | | 副本数期望值所在 JSON 路径 | 路径不存在 → **`/scale` 的 GET 直接报错** |
| `spec.versions[].subresources.scale.statusReplicasPath` | string | | 副本数实际值所在 JSON 路径 | 路径不存在 → **副本状态默认 0，HPA 认为副本为 0** |
| `spec.versions[].subresources.scale.labelSelectorPath` | string | | Pod 选择器所在 JSON 路径 | **缺失 → HPA/VPA 无法使用**；**返回空 → HPA 选不到 Pod，静默不扩不缩** |
| `spec.versions[].additionalPrinterColumns[]` | list | | `kubectl get` 输出列 | `jsonPath` 写错 → **该列显示空**（不报错） |
| `spec.versions[].additionalPrinterColumns[].type` | enum | | `integer`/`number`/`string`/`boolean`/`date` | 与实际值类型不符 → **该值被忽略（列显示空）** |
| `spec.versions[].additionalPrinterColumns[].priority` | int | | 0 = 标准视图；>0 = 仅 `-o wide` | 设成 1 → **`kubectl get` 看不到这一列** |
| `spec.conversion.strategy` | enum | | 多版本时的转换策略 | 配 `Webhook` 但没部署 webhook → **所有跨版本读写全挂** |
| `spec.conversion.webhook.conversionReviewVersions` | list | | Webhook 支持的 ConversionReview 版本 | 与 Webhook 实现不匹配 → **转换请求失败** |
| `spec.conversion.webhook.clientConfig.service` | object | | Webhook Service 定位 | Service 名/命名空间错 → **转换请求连不上** |
| `spec.conversion.webhook.clientConfig.caBundle` | string | | 校验 Webhook 证书的 CA | 证书不匹配 → **TLS 校验失败，转换请求报错** |
| `spec.preserveUnknownFields` | bool | | 旧版开关 | **在 `v1` 中不适用**；旧 CRD 保留它 → **裁剪不生效** |

### 4.2 controller-runtime 核心概念表

| 概念 | 类型/入口 | 作用 | 用错的后果 |
|---|---|---|---|
| **Reconciler** | 接口，实现 `Reconcile(ctx, req) (Result, error)` | **你写业务逻辑的地方**：读期望态 → 读实际态 → 求差 → 动手 → 回写 status | 写成命令式（收到事件就做一步）→ **重启后无法自愈**（违反 Level-Based） |
| **Client** | `client.Client` | 读写 K8s 资源的统一接口（Get/List/Create/Update/Delete/Patch + `Status()`） | 用它直接改 CR 的 spec → **绕过 status 子资源隔离** |
| **Manager** | `ctrl.NewManager` | **进程的总管**：启动缓存（informer）、注册 Controller、Leader Election、优雅退出 | 忘记配 Leader Election → **多副本 Operator 打架**（两个进程同时改同一 CR） |
| **Scheme** | `runtime.Scheme` | **类型注册表**：告诉 client-runtime"这个 Go 类型对应哪个 GVK" | 新增类型忘注册 → **`no kind is registered for the type`**，客户端直接 panic/报错 |
| **Controller** | `builder.ControllerManagedBy(mgr).For(&v1.CronTab{}).Complete(r)` | 把 Reconciler 接到事件源上 | `For()` 指错类型 → **watch 错对象，永远不触发** |
| **Cache / Informer** | Manager 内置 | 缓存 API 读，避免每次打 apiserver | 直接用 `mgr.GetAPIReader()` 读 → 绕过缓存拿到强一致数据（**性能差，但有时是必要的**） |
| **Requeue / RequeueAfter** | `ctrl.Result{RequeueAfter: 30*time.Second}` | 让控制器**过一会儿再来对一次账** | **不 Requeue → 外部漂移永远发现不了**（Level-Based 失效） |
| **Leader Election** | Manager 配置 | 保证同一时刻只有一个副本在 reconcile | 不开 → **多副本重复执行副作用**（建两个 PVC） |
| **OwnerReference** | `controllerutil.SetControllerReference` | 让被创建的子资源**随 CR 一起被垃圾回收** | 不设 → **删 CR 后 PVC/StatefulSet 残留**（孤儿资源） |
| **Finalizer** | CR 的 `metadata.finalizers` | **删除前的异步回调**（官方 CRD 文档明确支持） | 不设 → 删 CR 时 Operator **来不及做清理**（快照没建、PVC 没删） |
| **Event Recorder** | `mgr.GetEventRecorderFor("name")` | 往 K8s Event 里写记录 | 不写 → **排障时看不到任何线索**（Events 是空的） |
| **EnvTest** | `sigs.k8s.io/controller-runtime/pkg/envtest` | 本地起 etcd + apiserver 做集成测试 | 不用 → **只能靠真集群测，改一行就要 apply 一次** |

**reconcile 的返回值语义（必须记住）**：

| 返回 | 含义 | 后果 |
|---|---|---|
| `(ctrl.Result{}, nil)` | **处理完了，不用再来** | 事件驱动为主时没问题；**但外部漂移（有人手工改了集群）就再也发现不了** |
| `(ctrl.Result{Requeue: true}, nil)` | **立刻再对一次** | 高频事件下可能**忙循环** |
| `(ctrl.Result{RequeueAfter: 30*time.Second}, nil)` | **30 秒后再对一次** | **推荐的兜底**：既不忙循环，又能发现外部漂移 |
| `(ctrl.Result{}, err)` | 出错了 | **控制器会按指数退避重试**；**err 必须 `client.IgnoreNotFound` 过滤掉 NotFound**，否则 CR 删除后日志刷 error |

> **官方 Operator 文档里那句"Operator 的核心是告诉 API 服务器，如何使现实与代码里配置的资源匹配"，落到代码上就是这两条**：
> ① **RequeueAfter**（周期性重新对账）② **Leader Election**（保证只有一个在对账）。
> **少了第一条，Operator 就退化成"事件触发的脚本"；少了第二条，多副本就会重复动手。**

---

## 5. 横向对比

### 5.1 Helm vs Operator vs StatefulSet vs 云托管服务

**这是本篇最重要的选型表。** 四个方案解决的是**不同层次**的问题：

| 维度 | **Helm** | **Operator** | **StatefulSet** | **云托管服务** |
|---|---|---|---|---|
| **管什么** | **无状态应用的部署与配置** | **有状态软件的完整运维生命周期** | **有序的、有身份的 Pod 集合** | **厂商托管的整个服务** |
| **产物** | 一组 K8s 资源 | **CRD + CR + 控制器** | StatefulSet + PVC + Headless Service | 一个云端实例 + 一个 K8s 里的连接信息 |
| **谁来发现"坏了"** | **没人**（要人触发 upgrade） | **控制器自己** | **kubelet**（重启容器）+ **StatefulSet 控制器**（补副本） | **厂商** |
| **能做主从切换** | ❌ | ✅ **这是 Operator 的核心能力之一** | ❌ | ✅ |
| **能做备份/恢复流程** | ❌ | ✅ | ❌ | ✅ |
| **能做版本升级的中间步骤** | ❌ | ✅ | ❌ | ✅ |
| **扩缩容时搬数据** | ❌ | ✅ | ❌（**StatefulSet 不搬数据**） | ✅ |
| **成本** | **最低** | **最高**（要写、要测、要运维控制器） | 低 | **按量付费，但省掉运维** |
| **学习曲线** | 低 | **高**（要学 CRD + Go/controller-runtime） | 中 | 低（但被厂商绑定） |
| **数据归属** | 自己 | 自己 | 自己 | **在厂商手里** |
| **适合** | 业务应用、工具类服务 | **自研中间件、特殊化运维** | **有状态但运维简单的组件**（如单机 Kafka、有状态应用） | **绝大多数通用中间件** |

**选型决策树（按顺序问）**：

```
① 云厂商有托管服务吗？
   └─ 有，且能满足数据合规/成本/性能要求 → 【云托管服务】
      （RDS、Redis 云版、托管 Kafka、托管 MySQL）
   └─ 没有 / 不合规 / 成本太高 → 继续

② 这个中间件有成熟的社区 Operator 吗？
   └─ 有 → 【开源 Operator】
      （Prometheus Operator、Strimzi、ECK、CloudNativePG、Redis Operator…）
   └─ 没有 → 继续

③ 它的运维逻辑复杂到通用抽象表达不了吗？
   （需要主从切换？需要备份编排？需要版本升级中间步骤？需要数据搬迁？）
   └─ 是，且团队有 Go/Controller 开发能力 → 【自研 Operator】
   └─ 否 → 【Helm】（能 helm 搞定就别写 Operator）

④ 团队有 10 几人吗？
   └─ 有的人手和意愿长期维护一个控制器吗？
      └─ 没有 → 【托管服务】或【现成 Operator】，别自研
```

> **给本地民企的直接结论（面试可讲）**：
>
> **"10 几人团队的绝大多数中间件应该选现成 Operator 或托管服务，不该自研 Operator。"**
>
> **理由有三层**：
> ① **自研 Operator 的真实成本不在写代码，在长期维护**——CRD 版本兼容、控制器升级、多版本共存、webhook 可用性、CRD 数量膨胀，**都是长期负债**；
> ② **Operator 本身也是需要被观测和备份的组件**——它挂了，中间件就没人管了；**你没有多一套人力，就不要多一套组件**；
> ③ **通用中间件的运维知识已经有人写过了**——Prometheus、Strimzi、ECK、CloudNativePG 这些项目的存在，就是为了让你不写。
>
> **真正值得自研 Operator 的场景只有一个**：**你的运维逻辑是业务特有的、通用抽象表达不了的**，而且**这个软件是你们的核心资产**。

### 5.2 自研 Operator vs 开源 Operator

| 维度 | **自研 Operator** | **开源 Operator** |
|---|---|---|
| **初始开发成本** | **高**（CRD + 控制器 + 测试 + 打包发布） | **低**（装上就能用） |
| **领域知识来源** | **你自己写** | **社区已经写好并被大量验证** |
| **长期维护成本** | **高**（**你自己扛**：K8s 版本升级、CRD 版本、webhook…） | **中**（社区维护，但你得跟进升级） |
| **版本升级风险** | **你自己扛**：新 K8s 版本 API 变化 → 你的控制器可能不兼容 | **社区扛**，但**你仍要验证** |
| **可控性** | **完全可控**（改一行逻辑就改） | 受限于上游 |
| **社区支持** | **没有**（只有你自己） | 有 issue、PR、文档 |
| **踩坑可查性** | **只能查自己的日志** | **能搜到别人踩过的坑** |
| **适合** | **业务特有运维逻辑** | **通用中间件**（绝大多数场景） |

**常见开源 Operator（按中间件）**：

| 中间件 | 社区 Operator | 备注 |
|---|---|---|
| **Prometheus** | Prometheus Operator（kube-prometheus-stack） | 用 CR 管理 Prometheus/Alertmanager/ServiceMonitor |
| **Kafka** | **Strimzi** | 管 Kafka Broker、KRaft/ZooKeeper、Topic |
| **Elasticsearch / OpenSearch** | **ECK**（Elastic Cloud on Kubernetes） | 管 ES/Kibana/Apm |
| **PostgreSQL** | **CloudNativePG** | 管 PG 集群、主从切换、备份 |
| **Redis** | Redis Operator（OT-CONTAINER-KIT / spotahome 等） | 管 Redis 主从、Sentinel、Cluster |

> ⚠️ **上述项目名来自社区通用认知，`09-参考索引/README.md` 中未收录这些项目各自的官方文档链接（`⚠️ 单源待补`）**。落地前请以各项目官方仓库的 README 与 release note 为准，**特别要确认它支持的 K8s 版本范围**。

> **决策建议**：**先用开源 Operator，在它不够用的地方写 CR 扩展，而不是从零自研。**（Operator 通常允许在同一 CRD 上叠加自定义字段。）

### 5.3 CRD vs CR

| 维度 | **CRD** | **CR** |
|---|---|---|
| **是什么** | **类型定义**（Java 的 class） | **实例**（new 出来的对象） |
| **Kind** | `CustomResourceDefinition` | 你自己定义的（如 `CronTab`） |
| **apiVersion** | `apiextensions.k8s.io/v1` | `<group>/<version>`（如 `stable.example.com/v1`） |
| **作用域** | **集群级、无命名空间** | 由 CRD 的 `spec.scope` 决定 |
| **数量** | 一个类型一个 | **无数个** |
| **谁写** | **Operator 作者** | **使用方**（运维/开发） |
| **谁改** | 升级 Operator 时改 | 日常增删改 |
| **装它的动作** | `kubectl apply` CRD | `kubectl apply` CR |
| **删它的后果** | **所有 CR 一起被删**（官方原文明确） | 只删这一个 |
| **版本演进** | 加版本、改 schema、加字段 | 通常不改（改了就触发 Operator 动作） |

**一句话记住**：

> **CRD 是"菜单"，CR 是"点单"。** 装了 CRD 才有菜单；点了单（创建 CR），Operator 才会去做菜。
> **而且：删菜单（CRD）会把所有点单（CR）一起删掉——这个操作在生产上极其危险。**

---

## 6. 踩坑与排障

> **报错原文说明**：本篇未在你的环境实测，下表中的错误文本标注为「典型报错（来自官方 issue / 社区）」的，是公开讨论中反复出现的形态；标注「官方文档描述」的，是 K8s 官方中文文档中明确写出的行为。**不臆造具体版本号与行号。**

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **CRD 装错顺序：CR 创建失败** | `kubectl apply` CR 时报资源类型不存在（典型报错：**`no matches for kind "CronTab" in version "stable.example.com/v1"`** 或 **`the server could not find the requested resource`**） | **CRD 还没装好就创建 CR**。官方原文特别提醒：**"创建端点的操作可能需要几秒钟"**——CRD apply 成功 ≠ 端点已就绪 | 先 `kubectl apply` CRD，等 `status.conditions` 里 `Established: True`；再创建 CR；用 `kubectl wait --for=condition=Established crd/<name>` 等就绪 | **安装顺序写进 Helm chart 的 hooks**（CRD 放 `crds/` 目录或 pre-install hook）；**CR 与 CRD 分开安装时，CR 的安装脚本必须先 wait CRD** |
| **CRD 装错顺序（更隐蔽的一种）：CRD 后装导致已有 CR 全丢** | CR 全部消失 | 官方原文：**"当你删除某 CustomResourceDefinition 时，服务器会卸载其 RESTful API 端点，并删除服务器上存储的所有定制对象"** | **删 CRD 前必须先删 CR**；恢复靠 Operator 的 finalizer 逻辑或备份 | **`kubectl delete crd` 在生产上列为高危命令**；CRD 的升级走 `apply`（原地更新），**不用 delete + apply** |
| **schema 写错导致字段被裁剪** | `kubectl apply` **成功、不报错**；但 `kubectl get -o yaml` 里**看不到刚写的字段** | 官方原文：**"如果你指定了 API 服务器所无法识别的字段，则该未知字段会在保存资源之前被剪裁（Pruned）掉（删除）"**。典型原因：① CRD 里漏了这个字段；② CRD 升级到 `v1` 后忘了补 `openAPIV3Schema`；③ **字段名拼错**（`replica` vs `replicas`） | 补齐 CRD 的 schema 并重新 apply；用 `kubectl explain <cr>.spec` 核对真实字段名 | **CRD schema 与 Go 结构体用 Kubebuilder 生成（`make manifests`），不手写**；给 CRD 的变更加 CI 校验（对比 git diff 里的 schema 变化） |
| **status subresource 缺失导致状态不更新** | ① `status` 里的字段永远是空的；② **用户改的 spec 偶尔被"改回去"** | ① CRD 没配 `subresources.status` → **`/status` 端点不存在**，Controller 写 status 时走主资源端点，**整对象覆盖**；② 或者 Controller 用 `Update` 而不是 `Status().Update()` | CRD 加 `subresources: {status: {}}`；Controller 改用 `r.Status().Update(ctx, obj)` | **写 Controller 时区分"改配置"和"改状态"两条路径**；CRD 模板里默认带上 status 子资源 |
| **Operator 未给 RBAC 权限** | CR 创建成功、**Operator 什么都不做**；Operator 日志里一片拒绝（典型报错：**`forbidden: cannot create resource "persistentvolumeclaims" in API group "" in the namespace "xxx"`**；或 `cannot list/watch`） | Controller 的 ServiceAccount 没有对应 RBAC。**这是"CR 在、Pod 不在"的第一嫌疑** | 给 Operator 的 ServiceAccount 加 Role/ClusterRole（+ Binding）；用 `kubectl auth can-i create persistentvolumeclaims --as=system:serviceaccount:<ns>:<sa>` 验证 | **RBAC 权限用 `+kubebuilder:rbac` 注解写在代码旁，由 `make manifests` 生成**，不手写 YAML；CI 里加权限校验 |
| **Operator 升级破坏存量 CR** | 升级 Operator 后：① 老 CR 字段读不出来；② Operator 反序列化老 CR 失败；③ CRD 升级时新 schema 把老字段剪掉了 | ① **CRD 升级改了 schema，老对象里的未知字段被剪裁**（这就是"字段剪裁"在升级场景的表现）；② Controller 代码改了结构体，老 CR 反序列化失败；③ **多版本没配 conversion**，老版本对象读不出来 | ① **CRD 升级前先备份 CR**（`kubectl get cr -o yaml > backup.yaml`）；② Controller 升级要**保留向后兼容的字段**（新字段用指针 + omitempty）；③ **多版本共存期间必须配 `spec.conversion`** | **CRD 升级流程固定为：备份 → apply 新 CRD（保留旧版本 served=true）→ 升级 Controller → 验证老 CR → 再下掉旧版本**；**永远不要一步到位** |
| **CRD 数量过多导致 etcd 膨胀与 API 变慢** | ① `etcd` 磁盘占用告警；② `kubectl get` 明显变慢；③ API Server 内存涨；④ 大规模 list 变慢 | 每装一个 Operator 就会装一堆 CRD（一个 Operator 可能带 3-5 个 CRD + 若干 CR）；**CRD 本身也存在 etcd 里**，加上大量 CR 实例，**etcd 的 key 数量和总量都在涨**；**apiserver 的 discovery 文档随 CRD 数量线性增长**，每个请求都要处理更大的 API 列表 | ① **合并 Operator**：能用同一个 Operator 管的不要装两套；② **清理不用的 Operator**（连同它的 CRD 和 CR）；③ 用 etcd 的 defrag 回收空间；④ 大规模集群考虑 **api-server 的 aggregated discovery** | **建立"Operator 清单"**：每个 Operator 记 CRD 数量、CR 实例数、负责人；**新装 Operator 要过评审**（"它带来几个 CRD？"）；**etcd 容量和 CRD 数量一起监控** |
| **删 CRD 时把真实数据也删了** | PVC、StatefulSet 一起消失 | ① 删 CRD → 所有 CR 消失 → **Operator 看到 CR 没了 → 按删除逻辑清掉它建的资源**；② CR 没有 finalizer，Operator 来不及做清理 | **先删 CR（让 Operator 走正常的清理流程）→ 确认清理完成 → 再删 CRD**；PVC 提前打上保留标记 | **CR 上必须带 finalizer**；**生产中间件的 PVC 回收策略设成 `Retain`** |
| **CR 没有 finalizer，删 CR 时资源残留** | CR 删了，但 PVC/StatefulSet 还在（孤儿资源） | 没有 finalizer → apiserver 立即删除对象，**Operator 收不到"删除前"的钩子** | Controller 实现 finalizer 逻辑：在删除前做清理（快照、删子资源），完成后移除 finalizer | **CRD 模板默认带 finalizer**；Operator 启动时检查已有 CR 是否有 finalizer |
| **Operator 忘记设 OwnerReference，删 CR 后子资源残留** | 同上 | 子资源没有 owner 引用 → **垃圾回收器不知道该删谁** | 用 `controllerutil.SetControllerReference(owner, obj, scheme)` 设置 | **所有 Operator 创建的资源都必须设 owner**；这是 controller-runtime 的标准做法 |
| **多副本 Operator 重复执行副作用** | 建出**两个** PVC / 两条记录；日志里同一个 CR 被两个 Pod 处理 | 没开 **Leader Election**，两个副本同时 reconcile | Manager 配 Leader Election（默认开启，确认配置未被改） | 部署时**副本数 > 1 必须确认 Leader Election 生效**；观察日志里的 leader 切换记录 |
| **Operator 忘了 RequeueAfter，外部漂移永远发现不了** | 有人手工改了集群里 Operator 建的资源，**Operator 一直不纠正** | Reconcile 只靠事件驱动；`CR` 没变 → 没有事件 → **永远不再对账** | 在 Reconcile 末尾返回 `ctrl.Result{RequeueAfter: 30*time.Second}`（或更长） | **把"周期性重新对账"写成硬性约定**；这是 Level-Based 的必要条件 |
| **CRD 变成 `NonStructural`** | CR 创建被拒绝；`kubectl get crd` 的 `status.conditions` 里有 `NonStructural` 报告 | schema 违反了结构化规则（官方原文列举的四条） | 按官方规则修 schema：补 `type`、去掉 `allOf` 上下文里的 `description`/`type`/`default` 等、`metadata` 只约束 `name`/`generateName` | **用 Kubebuilder 生成 schema**，别手写；CI 里加 `kubectl get crd -o yaml` 检查无非结构化状况 |
| **HPA 挂在 CR 上但不扩不缩** | HPA 事件里目标为 `<unknown>` 或指标正常但**副本数不动** | `labelSelectorPath` 未设置 → **官方原文："必须设置后才能供 HPA 和 VPA 使用"**；或 `statusReplicasPath` 路径不存在 → **副本状态默认为 0**；或 `specReplicasPath` 路径不存在 → **GET /scale 直接报错** | 补齐三个路径字段；Operator 在 status 里**真实维护** `replicas` 和 `labelSelector` | **CR 接 HPA 必须三件套齐全**；照抄本套资料的 HPA 口径：**分母永远是 Pod 的 `requests.cpu`**（省略时退化为 limits，两者都不写则无分母）——**CR 的 Pod 模板里 `requests.cpu` 同样不能漏** |
| **Operator 的长耗时操作（备份/迁移）卡住整个控制器** | 其他 CR 长时间没被处理 | 在 Reconcile 里同步做分钟级的操作 → **阻塞 worker 队列** | 改成"建 Job → 后续 reconcile 检查 Job 状态" | **Reconcile 只做秒级的编排动作**；耗时活一律交给 Job |
| **Operator 自己成了单点** | 中间件无人管理 | Controller 是单个 Deployment，挂了就没人对账 | 副本数 ≥ 2 + Leader Election；监控它的健康；**给它配告警** | **Operator 是生产依赖**，要像对待核心业务一样监控它；**它自己也要被 GitOps 管理**（ArgoCD 同步它的 Deployment 与 CRD） |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/extend-kubernetes/operator/` | **Operator 模式全篇**：初衷、SampleDB 示例、Operator 示例清单、部署 Operator、使用 Operator、编写自己的 Operator（第三方库清单） | ✅ 2026-10-24 |
| S2 | 官方（中文） | `kubernetes.io/zh-cn/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/` | **CRD 全篇**：准备开始、创建 CRD、创建定制对象、删除 CRD、设置结构化的模式、字段剪裁、IntOrString、RawExtension、多版本、Finalizers、合法性检查、默认值、额外的打印列、字段选择算符、子资源（status/scale）、分类 | ✅ 2026-10-24 |
| S3 | 官方 | `book.kubebuilder.io/introduction` | Kubebuilder 是什么、面向谁、Kubernetes API 设计原则（Level-Based vs Edge-Based、Self-healing、GC 与 Finalizers、Resources vs Subresources）、Operator 开发者的收获 | ✅ 2026-10-24 |
| S4 | 官方 | `kubernetes.io/docs/reference/using-api/` | CRD 字段语义交叉验证（资源定义、状态/子资源） | ✅ 2026-10-24 |
| S5 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/architecture/` | 控制器模式与 reconcile 语义（Operator 与内置控制器同构的对照） | ✅ 2026-10-24 |
| S6 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/workloads/controllers/deployment/` | Deployment 与 StatefulSet 的能力边界对照 | ✅ 2026-10-24 |
| S7 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/security/rbac-good-practices/` | Operator 的 ServiceAccount 权限最小化 | ✅ 2026-10-24 |
| S8 | 官方（中文） | `helm.sh/zh/docs/intro/quickstart/` | Helm 的定位（渲染与安装，区别于 Operator 的持续对账） | ✅ 2026-10-24 |
| S9 | 官方（中文） | `helm.sh/zh/docs/topics/charts/` | Helm Chart 结构（与 CRD 的层次对比） | ✅ 2026-10-24 |
| S10 | 官方（中文） | `www.cncf.io/training/certification/cka/` | CKA 考点中的 CRD / Operator 相关知识点 | ✅ 2026-10-24 |
| S11 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 托管版与自建版的差异（Operator 自建 vs 托管形态） | ✅ 2026-10-24 |
| S12 | 中文 | `developer.aliyun.com` | Operator / 中间件云原生化的中文实践 | ✅ 2026-10-24 |
| S13 | 中文 | `cloud.tencent.com/developer` | Operator 落地与自研成本的中文讨论 | ✅ 2026-10-24 |

> **待验证清单**（本篇明确不确定的点，均标注在正文对应位置）：
> 1. **`spec.conversion` 的完整字段表** —— 官方中文 CRD 页面在"提供 CRD 的多个版本"一节只写了一句"详细信息可参阅 CustomResourceDefinition 的版本管理"，而该链接指向的中文路径当前 **404**（实测）。本篇 2.7 节给出的 conversion 字段结构来自 **Kubebuilder/controller-runtime 生态通用实践**，属 **`⚠️ 单源待补`**，落地时请以 `kubectl explain crd.spec.conversion` 的实际输出为准。
> 2. **社区 Operator 各自的官方文档链接**（Prometheus Operator / Strimzi / ECK / CloudNativePG / Redis Operator）—— `09-参考索引/README.md` 未收录，标 **`⚠️ 单源待补`**；本篇只给项目名，未给 URL。
> 3. **CRD 版本管理页（多版本与转换）的官方章节** —— 同上，中文路径当前 404，标 **`⚠️ 单源待补`**。
> 4. 第 6 节的报错文本均为**公开讨论中的典型形态**，本篇未在你的环境实测，请以实际事件原文为准。
>
> **非索引内 URL：0**（全部 URL 取自 [`../09-参考索引/README.md`](../09-参考索引/README.md) 中标 ✅ 的条目）。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入是"一个 CR（声明期望态）+ CRD（声明这个类型长什么样）"；输出是"集群里一组被拉齐的真实资源（PVC / StatefulSet / Job / 备份快照），以及 CR 的 `status` 反映出的实际状态"。中间是 **Controller 的 reconcile 循环**：watch CR → 读实际态 → 求差 → 动手 → 通过 `/status` 子资源回写 → 定期 Requeue 再对账。**Operator 只是"打包成 K8s 部署的控制器"，它的动作全是普通 K8s API 调用。**
2. **能故意改坏再倒推吗？** —— 五个破坏实验：
   - **① 在 CR 里写一个 schema 里没有的字段 `someRandomField: 42`** → apply 成功、不报错，但 `kubectl get -o yaml` 里**看不到这个字段** → 倒推出"**字段剪裁**"这条规则，以及"CRD 的 schema 是权威的"。
   - **② 删掉 CRD，再创建 CR** → 报 `no matches for kind` → 倒推出**安装顺序约束**，并亲眼验证"**删 CRD = 删所有 CR**"（官方原文）。
   - **③ 删掉 CRD 里的 `subresources.status`** → 让 Controller 用 `Update` 而非 `Status().Update()` 写状态 → 观察"用户改的 spec 被 Controller 冲掉" → 倒推出 status 子资源的作用。
   - **④ 删掉 Operator 的 RBAC 里的 `persistentvolumeclaims` 创建权限** → CR 创建成功但 PVC 一直不出现，日志里一片 `forbidden` → 倒推出"**RBAC 缺失的第一个现象是'CR 在、Pod 不在'**"。
   - **⑤ 把 CRD 的 `openAPIV3Schema` 里 `spec.replicas` 改名成 `spec.replica`** → 用户填 `replicas: 5` → 观察值被静默剪掉、CR 里没有这个字段 → 倒推出"**改 CRD schema 是破坏性操作，必须走备份 → 保留旧版本 → 验证 → 再下线的流程**"。
3. **能画上下游吗？** —— 画出 `用户写 CR（期望态） → apiserver（按 CRD 的 schema 校验 + 剪裁） → etcd → Controller watch → reconcile（读实际态 → 求差 → 建 PVC/StatefulSet/Job → 写 status） → 真实运行态`，并在每个环节标出"这一步失败会看到什么"：CRD 没装 → 资源类型不存在；schema 缺字段 → 字段静默消失；RBAC 缺权限 → `forbidden`；Controller 没起 → **CR 在但什么都没发生**；RequeueAfter 没配 → **外部漂移永远不纠正**。**然后在图旁边并排画上 Deployment 的链路，指出一一对应关系——这就是"Operator 与内置控制器同构"的证据。**

---

**下一篇**：[`L2-中间件Operator选型.md`](L2-中间件Operator选型.md)（**已完成**：中间件上 K8s 的四种形态对照、Operator 生态落地方向与选型决策、备份恢复与数据迁移路径、什么时候不该上 K8s） · **上一篇**：[`../04-交付与GitOps/L6-渐进式交付-ArgoRollouts.md`](../04-交付与GitOps/L6-渐进式交付-ArgoRollouts.md) · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md) · **相关**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md)
