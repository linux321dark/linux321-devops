# L6 · 存储 PV 与 PVC（从 emptyDir 到云盘：每一步解决了上一步的什么问题）

> **本篇定位**：把存储这条线一次讲到底。**为什么不能一直用 emptyDir/hostPath？PV 和 PVC 为什么要分成两个对象？访问模式到底限制了谁？回收策略选错会丢数据吗？StatefulSet 为什么必须配 Headless Service？CSI 到底解决了什么问题？**
> **前置**：[`L2-K8s架构与对象模型`](L2-K8s架构与对象模型.md)（控制器与协调模型）、[`L1-Docker-容器化基础`](../01-基础篇/L1-Docker-容器化基础.md)（Docker volume / bind mount 的对应关系）、[`L4-Service与网络`](L4-Service与网络.md)（Headless Service 的行为）。
> **学完能做什么**：为一个有状态应用写出完整的 StorageClass + PVC + StatefulSet 组合；拿到一个 Pending 的 PVC 能在三步内判断是"没匹配到 StorageClass / 容量不够 / WaitForFirstConsumer 在等调度"；说清 PVC 删除后数据还在不在、取决于什么；解释 StatefulSet 每个 Pod 独占一块卷的机制。
> **在 21 天里**：D3（网络与配置）的存储部分；D7（整合项目）的有状态服务；D17（等保与 DevSecOps）的数据留存要求。
> **环境/版本基准**：本文字段默认值以本地 kind 集群 `kubectl v1.37.1` / `Server v1.37.0` 为准；官方中文文档对应 v1.37 版本页。**本地 kind 没有 CSI 驱动与云盘，动态制备无法真实验证——本篇的动态制备部分以官方文档为依据，并明确标注。**
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L6 存储 PV 与 PVC
│
├─ 1. 概念
│   ├─ 1.1 一句话：存储问题是"制备"与"使用"分离的问题
│   ├─ 1.2 演进动因：每一步解决了上一步的什么问题
│   ├─ 1.3 三层抽象：卷 / PV / PVC
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 emptyDir 与 hostPath：能用但不能上生产
│   ├─ 2.2 PV 与 PVC 的绑定机制
│   ├─ 2.3 静态制备 vs 动态制备
│   ├─ 2.4 访问模式：RWO / ROX / RWX / RWOP
│   ├─ 2.5 回收策略：Retain / Recycle / Delete 与数据风险
│   ├─ 2.6 volumeBindingMode：Immediate vs WaitForFirstConsumer
│   ├─ 2.7 StorageClass：动态供给的模板
│   ├─ 2.8 CSI：把厂商实现抽象成标准接口
│   ├─ 2.9 StatefulSet 的 volumeClaimTemplates 与 Headless Service
│   └─ 2.10 关键参数与默认值表
│
├─ 3. 动手
│   ├─ 3.1 emptyDir 与 hostPath 观察
│   ├─ 3.2 静态制备 PV + PVC 并绑定
│   ├─ 3.3 StorageClass 与动态制备
│   ├─ 3.4 访问模式与回收策略实测
│   ├─ 3.5 StatefulSet 每 Pod 一卷
│   └─ 3.6 验证清单
│
├─ 4. 资源清单逐字段
│   ├─ 4.1 PersistentVolume
│   ├─ 4.2 PersistentVolumeClaim
│   └─ 4.3 StorageClass
│
├─ 5. 横向对比
│   ├─ 5.1 emptyDir vs hostPath vs PVC vs 云盘
│   ├─ 5.2 Immediate vs WaitForFirstConsumer
│   └─ 5.3 Deployment vs StatefulSet 的存储模型
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

> **PersistentVolume 解决"存储从哪来"，PersistentVolumeClaim 解决"谁要用、要多大、什么权限"。两者分离，是为了让"提供存储"和"使用存储"的人可以不是同一个人、不是同一个团队、不是同一次变更。**

**官方原文（PersistentVolume 页）**：

> 存储的管理是一个与计算实例的管理**完全不同**的问题。PersistentVolume 子系统为用户和管理员提供了一组 API，**将存储如何制备的细节从其如何被使用中抽象出来**。为了实现这点，我们引入了两个新的 API 资源：PersistentVolume 和 PersistentVolumeClaim。

**这句话就是全篇的地基。** 拆开看：

| 角色 | 对象 | 官方定位 |
|---|---|---|
| **提供方**（管理员 / 存储团队） | **PersistentVolume（PV）** | "集群中的一块存储……**拥有独立于任何使用 PV 的 Pod 的生命周期**" |
| **使用方**（应用团队） | **PersistentVolumeClaim（PVC）** | "**表达的是用户对存储的请求**，概念上与 Pod 类似" |

**官方对两者作用域的对比（这段必须记住）**：

> 持久卷是**集群资源**，就像节点也是集群资源一样。

> PersistentVolumeClaim 是**命名空间作用域**的对象。

**所以：PV 是集群级的（像 Node），PVC 是命名空间级的（像 Pod）。** 这个"一个集群级 + 一个命名空间级"的组合，是 K8s 存储设计里最精妙的一步——**它让同一个 PV 可以被不同命名空间的不同团队申请使用，同时保留各自的配额与权限边界。**

### 1.2 演进动因：每一步解决了上一步的什么问题

**这一节是本篇的主线。存储不是一次设计出来的，是一层一层逼出来的。**

```
第 0 步：容器可写层（Docker 的做法）
   问题：容器一删，数据就没了
        ↓
第 1 步：emptyDir（Pod 级临时卷）
   解决：容器重建不丢数据；Pod 内多容器共享
   新问题：Pod 一删就没了
        ↓
第 2 步：hostPath（挂宿主机目录）
   解决：跨 Pod 重建也保留
   新问题：绑死节点、数据和节点耦合、不可移植、不安全
        ↓
第 3 步：PV / PVC（把存储抽象成 API 对象）
   解决：存储可以被"申请"，而不是被"挂路径"
   新问题：PV 要手工一个个建，太繁琐
        ↓
第 4 步：StorageClass（动态供给的模板）
   解决：PVC 一提交，控制器自动造 PV
   新问题：厂商实现千差万别，K8s 核心被撑大
        ↓
第 5 步：CSI（厂商实现的标准化抽象）
   解决：厂商驱动独立发布、独立升级，K8s 核心不塞厂商代码
```

**逐步展开（每一步说清"上一步的什么问题"）：**

#### 第 0 步 → 第 1 步：为什么需要 emptyDir

L1 已经讲过 Docker 的可写层：**容器删了数据没了，容器崩了重建也回到干净状态**（L1 原文："kubelet restarts the container with a clean state"）。

官方 Volumes 页对 emptyDir 的定位：

> 对于定义了 `emptyDir` 卷的 Pod，该卷在 Pod 被分配到节点时创建。……**当 Pod 因为任何原因从节点上被移除时，emptyDir 中的数据将被永久删除。**

**但有一个重要例外（很多人不知道）**：

> 容器崩溃**不会**导致 Pod 从节点上移除。**emptyDir 卷中的数据在容器崩溃时是安全的。**

**所以 emptyDir 精确地解决了"容器重启丢数据"和"Pod 内多容器共享"两件事，但没解决"Pod 重建/迁移丢数据"。**

**emptyDir 的典型用途（官方列的）**：
- scratch space（比如基于磁盘的归并排序）
- checkpointing（长计算崩溃后恢复）
- content-manager 容器拉文件、webserver 容器提供服务的模式

**`emptyDir.medium: Memory` 的坑（必须知道）**：

> 如果你把 `emptyDir.medium` 字段设为 `"Memory"`，Kubernetes 会为你挂载一个 tmpfs（内存支持的文件系统）。**虽然 tmpfs 非常快，但要注意：与磁盘不同，你写入的文件会计入写入它的容器的内存限制。**

> **换句话说：memory 型 emptyDir 占的是容器的内存配额，不是节点内存。** 设了 `medium: Memory` 又没给容器配 memory limit → **这个卷的大小不受限，可能把节点内存吃光。**

#### 第 1 步 → 第 2 步：为什么需要 hostPath

**emptyDir 的硬伤：Pod 生命周期一结束，数据就没了。**

而现实中很多数据需要活得比 Pod 长：
- 日志要归档
- 容器崩溃后要能捞回现场
- 某些中间件要缓存热点数据
- **同一个 Pod 的多次重建之间要保留数据**（比如正在跑的任务的检查点）

**hostPath 的解法**：直接挂宿主机上的目录。**数据活在节点上，与 Pod 生命周期解耦。**

**但 hostPath 带来四个新问题（这四个问题就是 PV/PVC 存在的理由）**：

| 问题 | 具体后果 |
|---|---|
| **① 和节点强耦合** | 数据在 `/data` 这个节点上。**Pod 调度到别的节点 → 数据"消失"**（挂的是空目录） |
| **② 不可移植** | Pod 定义里写死了路径，**换个集群/换个节点路径就不一样** |
| **③ 调度被破坏** | **同一个 Deployment 的两个副本可能被调度到不同节点 → 看到的数据不一致** |
| **④ 安全风险** | 容器拿到了对宿主目录的读写权限，**逃逸风险放大** |

> **hostPath 的正确定位**：**节点级的、确实需要访问宿主数据的场景**（日志采集 agent 读 `/var/log`、监控 agent 读 kubelet 目录、CSI 驱动本身）。**它是特权操作，不是普通应用的存储方案。**

#### 第 2 步 → 第 3 步：为什么需要 PV/PVC

**hostPath 的核心问题不是"不能持久"，而是"路径与节点绑定"。**

**K8s 的思路（这一步是整个设计的转折）**：

> **不要让 Pod 说"我要挂 /data"，而是让 Pod 说"我要一块 10Gi 的、ReadWriteOnce 的存储"。至于这块存储在哪、是什么设备、怎么实现——那不是应用该关心的事。**

于是拆成两个对象：

```
应用团队写 PVC（声明"要什么"）：
    apiVersion: v1
    kind: PersistentVolumeClaim
    spec:
      accessModes: [ReadWriteOnce]
      resources:
        requests:
          storage: 10Gi
      storageClassName: fast-ssd          # ← 我要哪个"档位"

存储团队/管理员准备 PV（提供"有什么"）：
    apiVersion: v1
    kind: PersistentVolume
    spec:
      capacity:
        storage: 10Gi
      accessModes: [ReadWriteOnce]
      storageClassName: fast-ssd
      nfs:                                 # ← 实际是 NFS
        server: 10.0.0.1
        path: /exports/data
```

**这一步解决了什么？**

| 解决的问题 | 说明 |
|---|---|
| **① 应用与实现解耦** | 应用只知道"10Gi 可写存储"，**换底层（NFS→Ceph→云盘）不改 Pod** |
| **② 权限边界** | **PV 集群级、PVC 命名空间级** → 应用团队在自己的 ns 里申请，**碰不到别的 ns 的卷** |
| **③ 配额可管** | ResourceQuota 可以限制命名空间能申请多少存储（见 L2 的配额口径） |
| **④ 可移植** | Pod 里只写 `claimName: my-pvc`，**换集群只要 PVC 语义不变就行** |

**官方原文（PVC 与 Pod 的类比，非常精准）**：

> PersistentVolumeClaim 表达的是用户对存储的请求，**概念上与 Pod 类似**。Pod 会耗用节点资源，而 PVC 申领会**耗用 PV 资源**。Pod 可以请求特定数量的资源（CPU 和内存）。同样 PVC 申领也可以请求特定的大小和访问模式。

**再引用一段把 StorageClass 的必要性讲透（这是官方原文）**：

> 尽管 PersistentVolumeClaim 允许用户消耗抽象的存储资源，常见的情况是针对不同的问题用户需要的是具有不同属性（如，性能）的 PersistentVolume 卷。集群管理员需要能够提供不同性质的 PersistentVolume，并且这些 PV 卷之间的差别不仅限于卷大小和访问模式，同时又不能将**卷是如何实现的这些细节暴露给用户**。为了满足这类需求，就有了**存储类（StorageClass）**资源。

#### 第 3 步 → 第 4 步：为什么需要 StorageClass

**有了 PV/PVC 之后，新的问题是"运维地狱"**：

```
① 运维要提前创建 100 个 PV（不同规格）
② 应用团队提 PVC
③ 运维手工去把 PVC 和 PV 对上（改 claimRef）
④ 应用等着
```

**手工制备（静态制备）在有状态服务少的时候还能忍，多了就崩。**

**StorageClass 的解法**：**把"制备规则"变成一个模板，让控制器自动造 PV。**

官方原文：

> 每个 StorageClass 都包含 `provisioner`、`parameters` 和 `reclaimPolicy` 字段，这些字段会在 **StorageClass 需要动态制备 PersistentVolume 以满足 PersistentVolumeClaim（PVC）** 时使用到。

**官方对类比的说法很妙**：

> Kubernetes 存储类的概念类似于一些其他存储系统设计中的**"配置文件"**。

#### 第 4 步 → 第 5 步：为什么需要 CSI

**CSI 之前，K8s 的存储是"树内（in-tree）"的**：每种存储（AWS EBS、Azure Disk、Ceph RBD、NFS、vSphere……）都在 K8s 主仓库里写一份卷插件代码。

**这带来三个问题**：

| 问题 | 后果 |
|---|---|
| **K8s 仓库被厂商代码撑大** | 核心仓里塞满了几十种存储的实现，**升级都要重新编译** |
| **厂商无法独立发版** | 改一个云盘的 bug，**要等 K8s 下个版本** |
| **多云支持爆炸** | 每加一个存储后端，K8s 核心就要加一份代码 |

**CSI（Container Storage Interface）的解法**：**把"如何创建/删除/挂载卷"定义成一个标准接口，厂商的驱动作为独立程序运行在集群里，K8s 只调用接口。**

官方原文（StorageClass 的 Provisioner 章节）：

> 你不限于指定此处列出的"内置"制备器（其名称前缀为 `kubernetes.io` 并打包在 Kubernetes 中）。**你还可以运行和指定外部制备器，这些独立的程序遵循由 Kubernetes 定义的规范。外部供应商的作者完全可以自由决定他们的代码保存于何处、打包方式、运行方式、使用的插件（包括 Flex）等。**

> **趋势已经很明确**（官方在多个存储章节标注的弃用信息）：

| 被弃用的树内驱动 | 弃用版本 | 移除版本 | 替代 |
|---|---|---|---|
| `awsElasticBlockStore` | v1.19 | **v1.27** | AWS EBS CSI 驱动 |
| `azureDisk` | v1.19 | **v1.27** | Azure Disk 第三方驱动 |
| `gcePersistentDisk` | — | — | GCE PD CSI 驱动 |
| Ceph RBD 内置驱动 | v1.28 | — | CephFS RBD CSI 驱动 |
| PortworxVolume | — | — | Portworx CSI 驱动 |
| vSphereVolume 内置 | — | — | vSphere CSI 驱动 + CSI 迁移 |

> **一句话记住**：**"树内插件时代结束了，现在所有存储后端都走 CSI。"** 这也是本篇必须讲 CSI 的原因——**它不是新概念，是已经完成的历史迁移**。

### 1.3 三层抽象：卷 / PV / PVC

| 层 | 对象 | 作用域 | 谁写的 | 生命周期 |
|---|---|---|---|---|
| **① 卷（Volume）** | `Pod.spec.volumes[]` | **Pod 级** | 应用开发者 | 跟随 Pod |
| **② 持久卷（PV）** | `PersistentVolume` | **集群级** | 管理员 / 控制器自动 | **独立于任何 Pod** |
| **③ 持久卷申领（PVC）** | `PersistentVolumeClaim` | **命名空间级** | 应用开发者 | 跟随申领 |

**官方原文（卷的生命周期对照）**：

> 临时卷类型的生命周期与特定的 Pod 绑定，但**持久卷的存在时间超过了任何单个 Pod 的生命周期**。当 Pod 不再存在时，Kubernetes 会销毁临时卷；然而，**Kubernetes 不会销毁持久卷**。

**Pod 与 PVC 的连接点**：

```yaml
# Pod 里只写 PVC 名字，不写路径、不写设备
volumes:
- name: data
  persistentVolumeClaim:
    claimName: my-pvc        # ← 唯一的连接点
```

**这个"只写名字"的设计是整个抽象的精髓**：**Pod 不关心底层是什么，甚至不关心在哪个节点上。**

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Volume | 卷 | Pod 内的一个目录，被挂载到容器里 |
| `emptyDir` | 空目录卷 | **Pod 生命周期内**的临时目录 |
| `hostPath` | 宿主路径卷 | 挂宿主机目录，**与节点强耦合** |
| PersistentVolume (PV) | 持久卷 | **集群级**的一块存储，独立于 Pod 存在 |
| PersistentVolumeClaim (PVC) | 持久卷申领 | **命名空间级**的存储请求 |
| StorageClass | 存储类 | 动态供给的模板（"配置文件"） |
| `provisioner` | 制备器 | 决定用哪个卷插件制备 PV |
| `parameters` | 参数 | 厂商特定的制备参数 |
| `reclaimPolicy` | 回收策略 | PV 释放后怎么办（Retain/Recycle/Delete） |
| `volumeBindingMode` | 卷绑定模式 | 何时绑定（Immediate/WaitForFirstConsumer） |
| `allowVolumeExpansion` | 允许扩容 | 是否支持在线扩容 |
| `accessModes` | 访问模式 | RWO/ROX/RWX/RWOP |
| `volumeName` | 卷名 | PVC 绑定的具体 PV |
| `claimRef` | 申领引用 | PV 上记录"我被哪个 PVC 绑了" |
| `volumeClaimTemplates` | 卷申领模板 | **StatefulSet 专用**：每个 Pod 一块独立卷 |
| `serviceName` | 治理服务 | StatefulSet 的**无头 Service** 名 |
| CSI | 容器存储接口 | **厂商驱动与 K8s 之间的标准接口** |
| CSIDriver | CSI 驱动对象 | 描述一个 CSI 驱动及其能力 |
| `volumeMode` | 卷模式 | Filesystem（文件系统）/ Block（原始块设备） |

---

## 2. 机制

### 2.1 emptyDir 与 hostPath：能用但不能上生产

**对照表（这张表是选型的基础）**：

| 维度 | **emptyDir** | **hostPath** |
|---|---|---|
| 数据位置 | 节点上的目录（默认介质）或 **tmpfs（内存）** | 宿主机任意路径 |
| 容器崩溃 | **✅ 数据保留** | ✅ 保留 |
| **Pod 重建** | ❌ **数据丢失** | ✅ 保留 |
| **Pod 迁移到别的节点** | ❌ 数据在新节点上是空的 | ❌ **数据"消失"**（新节点没这个路径） |
| 同 Pod 多容器共享 | ✅ | ✅ |
| 跨 Pod 共享 | ❌ | ✅（但绑死节点） |
| 依赖调度一致性 | — | **❌ 同 Deployment 的副本可能看到不同数据** |
| 安全性 | 高（隔离在 Pod 内） | **低**（容器获得宿主目录访问权） |
| 适用 | 临时文件、缓存、sidecar 共享、检查点 | **节点级 agent**（日志/监控采集）、CSI 驱动 |
| 上生产？ | ✅ 可以（临时数据场景） | ⚠️ **只用于特权/节点级场景** |

> **emptyDir 的 `medium` 与 `sizeLimit`**（官方原文）：

| 字段 | 效果 |
|---|---|
| `medium` 未设 | 存在节点默认介质上（磁盘/SSD/网络存储，取决于环境） |
| `medium: Memory` | **挂 tmpfs（内存文件系统）**，极快，但**占用容器的内存配额** |
| `sizeLimit` | 限制容量，**从节点的临时存储（ephemeral storage）分配** |

> **官方提醒**：如果节点的临时存储被其他来源（日志、镜像层）填满，`emptyDir` 可能在达到 `sizeLimit` 之前就**先没容量了**。

> **hostPath 的类型（`type` 字段）**：`Directory`（不存在会自动创建）/ `DirectoryOrCreate` / `File` / `FileOrCreate` / `Socket` 等。**`DirectoryOrCreate` 会自动创建目录**——⚠️ **在生产上用 hostPath 时通常要配 `type: Directory`（不自动创建），避免 kubelet 在宿主机上乱建目录**。

### 2.2 PV 与 PVC 的绑定机制

**官方对绑定过程的描述（这是本节的核心）**：

> 用户创建一个带有特定存储容量和特定访问模式需求的 PersistentVolumeClaim 对象；在动态制备场景下，这个 PVC 对象可能已经创建完毕。**控制平面中的控制回路监测新的 PVC 对象，寻找与之匹配的 PV 卷（如果可能的话），并将二者绑定到一起。** 如果为了新的 PVC 申领动态制备了 PV 卷，则控制回路总是将该 PV 卷绑定到这一 PVC 申领。

**绑定的四条规则**：

| 规则 | 说明 |
|---|---|
| **① 存储类要一致** | 只有 `storageClassName` 值与 PVC 相同的 PV 才能绑定 |
| **② 访问模式要兼容** | PV 的访问模式必须包含 PVC 请求的模式 |
| **③ 容量要够** | **PV 的容量必须 ≥ PVC 请求的容量** |
| **④ 一对一、排他** | 官方原文："**PVC 申领与 PV 卷之间的绑定是一种一对一的映射**，实现上使用 **ClaimRef** 来记述 PV 卷与 PVC 申领间的双向绑定关系" |

**官方原文（容量不足的具体例子）**：

> 如果找不到匹配的 PV 卷，**PVC 申领会无限期地处于未绑定状态**。当与之匹配的 PV 卷可用时，PVC 申领会被绑定。例如，**即使某集群上制备了很多 50 Gi 大小的 PV 卷，也无法与请求 100 Gi 大小的存储的 PVC 匹配。当新的 100 Gi PV 卷被加入到集群时，该 PVC 才有可能被绑定。**

**`ClaimRef` 的作用（双向绑定）**：

```
PV.claimRef = { apiVersion: v1, kind: PersistentVolumeClaim, name: my-pvc, namespace: default }
```

- **PV 侧**：`claimRef` 记录"我被谁绑了"→ 防止第二个 PVC 抢走这个 PV
- **PVC 侧**：`spec.volumeName` 记录"我绑的是哪个 PV"→ 供控制器找到要挂载的卷

**PV 的两种状态（`status.phase`）**：

| phase | 含义 | 能被新 PVC 绑定吗 |
|---|---|---|
| **Available** | 可用，未被绑定 | ✅ |
| **Bound** | 已绑定 | ❌ |
| **Released** | **原 PVC 已删，但 PV 还在**（取决于回收策略） | ❌ **需人工处理** |
| **Failed** | 绑定/挂载失败 | ❌ |

> **Released 这个状态是本篇第 6 节踩坑表的核心**（"误删 PVC 导致数据丢失"）。

**静态制备的手工绑定（预绑定）**：

官方原文：

> 通过在 PersistentVolumeClaim 中指定 `PersistentVolume`，你可以声明该特定 PV 与 PVC 之间的绑定关系。如果该 PersistentVolume 存在且未被通过其 `claimRef` 字段预留给 PersistentVolumeClaim，则该 PersistentVolume 会和该 PersistentVolumeClaim 绑定到一起。

> **注意**：官方同时说明"**绑定操作不会考虑某些卷匹配条件是否满足，包括节点亲和性等等**"——**预绑定是"强制配对"，控制面仍会检查存储类、访问模式和请求的存储尺寸是否合法**。

### 2.3 静态制备 vs 动态制备

| 维度 | **静态制备** | **动态制备** |
|---|---|---|
| 谁创建 PV | **管理员手工** | **控制器自动**（依据 StorageClass） |
| 什么时候 | 提前批量建好 | **PVC 提交时按需** |
| 适合 | 有特殊要求（复用已有存储、精确绑定、测试） | **绝大多数生产场景** |
| 运维成本 | **高**（要预判容量、提前建） | **低**（声明式） |
| 需要 StorageClass | 可选 | **必需** |
| 绑定时机 | 可预绑定 | 由 `volumeBindingMode` 决定 |

**官方原文（动态制备的触发条件）**：

> 如果管理员所创建的所有静态 PV 卷都无法与用户的 PersistentVolumeClaim 匹配，集群可以尝试为该 PVC 申领**动态制备**一个存储卷。这一制备操作是基于 StorageClass 来实现的：**PVC 申领必须请求某个存储类，同时集群管理员必须已经创建并配置了该类**，这样动态制备卷的动作才会发生。**如果 PVC 申领指定存储类为 `""`，则相当于为自身禁止使用动态制备的卷。**

> **关键推论**：`storageClassName: ""` = **明确拒绝动态制备**（只接受静态 PV）。这在"我要强制用预先准备好的、有特殊备份策略的卷"时是有用的。

**官方原文（准入控制器）**：

> 为了基于存储类完成动态的存储制备，集群管理员需要在 API 服务器上启用 **`DefaultStorageClass` 准入控制器**。

### 2.4 访问模式：RWO / ROX / RWX / RWOP

**官方原文（四个模式的定义）**：

| 模式 | 缩写 | 官方定义 | 关键限制 |
|---|---|---|---|
| **ReadWriteOnce** | **RWO** | 卷可以被**一个节点**以读写方式挂载 | "ReadWriteOnce 访问模式**仍然可以在同一节点上运行的多个 Pod 访问**（读取或写入）该卷" |
| **ReadOnlyMany** | **ROX** | 卷可以被**多个节点**以只读方式挂载 | 只读 |
| **ReadWriteMany** | **RWX** | 卷可以被**多个节点**以读写方式挂载 | **需要底层存储支持**（NFS/CephFS） |
| **ReadWriteOncePod** | **RWOP** | 卷可以被**单个 Pod** 以读写方式挂载 | **特性状态：GA since Kubernetes v1.29** |

**三个必须讲透的点：**

**① RWO 是"一个节点"，不是"一个 Pod"。**

官方原文明确："ReadWriteOnce 访问模式仍然可以在同一节点上运行的多个 Pod 访问（读取或写入）该卷。"

> **这个区别是本篇踩坑表的重要一条**：**RWO 的 PVC 被两个 Deployment 引用，如果两个 Pod 调度到同一节点 → 能跑；调度到不同节点 → 第二个 Pod 挂载失败。**
> **所以 RWO 的 PVC 不能给有多个副本的 Deployment 用（除非副本数=1 或强制同节点）。**

**② RWOP 是 v1.29 GA 的新选项，比 RWO 更严格。**

官方原文：

> 如果你想确保**整个集群中只有一个 Pod** 可以读取或写入该 PVC，请使用 ReadWriteOncePod 访问模式。

> **官方在 StatefulSet 文档里的推荐（值得注意）**："这个示例出于简化考虑使用了 `ReadWriteOnce` 访问模式。但**对于生产环境，Kubernetes 项目建议使用 `ReadWriteOncePod` 访问模式**。"
> **这是官方明确的生产建议，能说出来是加分项。**

**③ 访问模式不是写保护。** 官方原文：

> Kubernetes 使用卷访问模式来匹配 PersistentVolumeClaim 和 PersistentVolume。……**卷访问模式并不会在存储已经被挂载的情况下为其实施写保护。** 即使访问模式设置为 ReadWriteOnce、ReadOnlyMany 或 ReadWriteMany，**它们也不会对卷形成限制**。

> **重要提醒**：**每个卷同一时刻只能以一种访问模式挂载，即使该卷能够支持多种访问模式。**

**各存储后端支持哪些访问模式（官方表格，节选）**：

| 卷插件 | RWO | ROX | RWX | RWOP |
|---|---|---|---|---|
| CephFS | ✓ | ✓ | ✓ | - |
| NFS | ✓ | ✓ | ✓ | - |
| HostPath | ✓ | - | - | - |
| iSCSI | ✓ | ✓ | - | - |
| RBD | ✓ | ✓ | - | - |
| FC | ✓ | ✓ | - | - |
| **CSI** | **取决于驱动** | **取决于驱动** | **取决于驱动** | **取决于驱动** |
| AzureFile | ✓ | ✓ | ✓ | - |
| FlexVolume | ✓ | ✓ | 取决于驱动 | - |

> **CSI 那行全是"取决于驱动"——这是本篇必须讲清的一点**：**访问模式能不能用，最终由底层存储和 CSI 驱动决定，不是 K8s 说了算。**
> **典型的云盘只支持 RWO**（一块盘只能挂一个节点）；**要 RWX 必须用网络存储（NFS/CephFS/云厂商的 NAS 类产品）**。

**RWOP 的 CSI 版本要求（官方原文）**：

> `ReadWriteOncePod` 访问模式仅适用于 CSI 卷和 Kubernetes v1.22+。要使用此特性，你需要将以下 CSI 边车更新为下列或更高版本：
> - `csi-provisioner:v3.0.0+`
> - `csi-attacher:v3.3.0+`
> - `csi-resizer:v1.3.0+`

### 2.5 回收策略：Retain / Recycle / Delete 与数据风险

**官方原文（三种策略的定义）**：

| 策略 | 官方定义 | PVC 删除后 |
|---|---|---|
| **Retain** | 手动回收 | **PV 对象仍然存在**，对应的数据卷被视为"已释放（released）"。**由于卷上仍然存在这前一申领人的数据，该卷还不能用于其他申领** |
| **Delete** | 删除存储卷 | **PV 对象从 K8s 中移除，同时从外部基础设施中移除所关联的存储资产** |
| **Recycle** | 简单擦除 | **已被废弃**（官方警告："回收策略 `Recycle` 已被废弃。取而代之的建议方案是使用动态制备"） |

**Delete 的细节（官方原文）**：

> **动态制备的卷会继承其 StorageClass 中设置的回收策略，该策略默认为 `Delete`。**

> **⚠️ 数据风险的准确表述**：`Delete` 策略下，**PVC 一删，PV 对象和底层存储资产一起没了**。**数据是真的丢了。**

**Recycle 的准确表述（不要按老印象说）**：

官方原文：

> 回收策略 `Recycle` **已被废弃**。取而代之的建议方案是使用动态制备。
> 如果下层的卷插件支持，回收策略 `Recycle` 会在卷上执行一些基本的擦除（`rm -rf /thevolume/*`）操作，之后允许该卷用于新的 PVC 申领。

> **而且官方在 PersistentVolume 页给了一个版本相关的硬事实**：
> "对于 Kubernetes 1.37 来说，**只有 `nfs` 和 `hostPath` 卷类型支持回收（Recycle）**。"
> **所以 Recycle 在 v1.37 上基本等于"只有 NFS/hostPath 能用"，且已废弃——不要在新系统里用它。**

**Retain 的官方三步手工回收流程**：

> 1. **删除 PersistentVolume 对象。** 与之相关的、位于外部基础设施中的存储资产在 PV 删除之后**仍然存在**。
> 2. 根据情况，**手动清除**所关联的存储资产上的数据。
> 3. **手动删除**所关联的存储资产。

> **如果你希望重用该存储资产，可以基于存储资产的定义创建新的 PersistentVolume 卷对象。**

**回收策略的选型表**：

| 场景 | 推荐策略 | 理由 |
|---|---|---|
| **生产数据库** | **Retain** | **误删 PVC 不等于数据丢失**——这是最后一道防线 |
| 有云盘快照/备份兜底 | Delete | 有备份，删了能恢复 |
| 测试环境 / 临时卷 | Delete | 省存储成本 |
| **新系统** | **一律先 Retain** | **"数据丢失是不可逆事故"** |

> **一个必须讲清的事实**：`Retain` 策略下，**PVC 删掉后数据还在，但 PV 变成 `Released` 状态且"还不能用于其他申领"** —— **PV 不会自动回到 Available，也不会被清理，会一直占着集群里的 PV 名额。**
> **所以 Retain 的代价是：需要有人（通常是有自动化脚本的运维）去清理 Released 的 PV。** **这正是 GitOps / Operator 的用武之地——把 PV 清理也做成自动化。**

**回收策略的继承关系（要背）**：

```
StorageClass.reclaimPolicy  ──(动态制备)──►  PV.persistentVolumeReclaimPolicy
StorageClass 未指定        ──────────────►  默认 Delete
手工创建的 PV             ──────────────►  用创建时指定的策略（不继承）
```

### 2.6 volumeBindingMode：Immediate vs WaitForFirstConsumer

**官方原文（定义与默认值）**：

> `volumeBindingMode` 字段控制卷绑定和动态制备应该发生在什么时候。**当未设置时，默认使用 `Immediate` 模式。**

> `Immediate` 模式表示**一旦创建了 PersistentVolumeClaim 也就完成了卷绑定和动态制备**。对于由于拓扑限制而非集群所有节点可达的存储后端，PersistentVolume 会在**不知道 Pod 的调度要求**的情况下绑定或者制备。**这可能导致 Pod 无法被调度（unschedulable Pods）。**

> 集群管理员可以通过指定 `WaitForFirstConsumer` 模式来解决此问题。该模式将**延迟 PersistentVolume 的绑定和制备，直到使用该 PersistentVolumeClaim 的 Pod 被创建**。PersistentVolume 会根据 Pod 调度约束指定的拓扑来选择或制备。这些包括但不限于**资源需求、节点筛选器、Pod 亲和性和互斥性、以及污点和容忍度**。

**两种模式的对比（这张表是本节的核心）**：

| 维度 | **Immediate**（默认） | **WaitForFirstConsumer** |
|---|---|---|
| **绑定时机** | **PVC 一创建就绑定/制备** | **等第一个用它的 Pod 被创建后才绑定/制备** |
| **PVC 状态** | 立即变 `Bound`（如果有卷可用） | **一直 `Pending`，直到有 Pod 用它** |
| **拓扑感知** | ❌ **不知道 Pod 会去哪** → 可能造出**其他可用区的卷** | ✅ **按 Pod 的调度约束选卷** |
| **典型故障** | **Pod `Pending` + 事件提示 `volume node affinity conflict`** | 无（但 PVC 长时间 Pending 容易误判） |
| **对无拓扑约束的存储** | 够用 | 没必要 |
| **多可用区集群** | ⚠️ **危险** | ✅ **必须用** |
| **本地卷（local）** | ⚠️ 不适用 | ✅ **官方推荐**（动态制备不支持，只能延迟绑定） |

**多可用区为什么必须用 WaitForFirstConsumer（这是本节最值得讲的场景）**：

```
场景：集群跨 3 个可用区（us-east-2a / 2b / 2c），云盘只能在同区挂载

Immediate 模式：
  ① 应用提交 PVC（没写 nodeSelector）
  ② 控制器立刻制备卷 —— 随机选一个区，比如 2a
  ③ Pod 提交，带 nodeSelector: topology.kubernetes.io/zone=2c
  ④ 调度器：这个 Pod 要在 2c，但卷在 2a → 挂不上
  ⑤ Pod 永远 Pending，事件报 volume node affinity conflict
  ↑ 死锁：卷不会动，Pod 也不会动

WaitForFirstConsumer 模式：
  ① 应用提交 PVC → PVC 保持 Pending（这是正常的！）
  ② Pod 提交，带 nodeSelector: zone=2c → 调度器先把它调度到 2c
  ③ 控制器看到"第一个消费者来了，且它要求 2c" → 在 2c 制备卷
  ④ 挂载成功，Pod Running
```

> **官方原文也给了 WaitForFirstConsumer 的本地卷用法**：
> "在 Kubernetes 1.37 中，本地卷还不支持动态制备；然而还是需要创建 StorageClass 以延迟卷绑定，直到 Pod 被实际调度到合适的节点。"

**WaitForFirstConsumer 的官方警告（必须知道）**：

> 如果你选择使用 `WaitForFirstConsumer`，**请不要在 Pod 规约中使用 `nodeName` 来指定节点亲和性**。如果在这种情况下使用 `nodeName`，**Pod 将会绕过调度程序，PVC 将停留在 `pending` 状态**。

> **替代做法**（官方给的）：用 `nodeSelector` 指定 `kubernetes.io/hostname`。

**这个坑很多人栽**：`nodeName` 直接指定节点 = **绕过调度器** → **调度器不会去为它选卷** → PVC 永远等不到消费者 → 死锁。**用 `nodeSelector` 或 `affinity` 代替 `nodeName`。**

### 2.7 StorageClass：动态供给的模板

**官方定义（这一句很准）**：

> StorageClass 为管理员提供了描述存储**类**的方法。不同的类型可能会映射到不同的服务质量等级或备份策略，或是由集群管理员制定的任意策略。**Kubernetes 本身并不清楚各种类代表什么。**

**字段（官方原文列出三个核心字段）**：

> 每个 StorageClass 都包含 **`provisioner`、`parameters` 和 `reclaimPolicy`** 字段。

| 字段 | 含义 | 默认 |
|---|---|---|
| **`provisioner`** | 决定用哪个卷插件制备 PV。**该字段必须指定** | **必填** |
| `parameters` | 厂商特定的制备参数 | 取决于 provisioner |
| `reclaimPolicy` | 动态制备出来的 PV 用什么回收策略 | **Delete** |
| `volumeBindingMode` | 何时绑定 | **Immediate** |
| `allowVolumeExpansion` | 是否支持在线扩容 | false |
| `mountOptions` | 挂载选项（如 `discard`） | — |
| `allowedTopologies` | 限制制备卷的拓扑（可用区） | — |

**`provisioner` 的分类（官方原文）**：

> 你不限于指定此处列出的"内置"制备器（其名称前缀为 `kubernetes.io` 并打包在 Kubernetes 中）。**你还可以运行和指定外部制备器。**

| 类别 | 命名 | 例子 |
|---|---|---|
| **内置制备器** | `kubernetes.io/xxx` | `kubernetes.io/no-provisioner`（手动制备）、`kubernetes.io/local` |
| **外部制备器（CSI）** | `厂商域名/驱动名` | `ebs.csi.aws.com`、`diskplugin.csi.alibabacloud.com`、`csi.vsphere.vmware.com` |

**默认 StorageClass（官方原文）**：

> 当一个 PVC 没有指定 `storageClassName` 时，会使用默认的 StorageClass。

> 如果你在集群中的**多个** StorageClass 上将 `storageclass.kubernetes.io/is-default-class` 注解设置为 true，然后创建一个未设置 `storageClassName` 的 PVC，**Kubernetes 将使用最近创建的默认 StorageClass**。

> **官方建议**："你应该尝试在集群中**只将一个** StorageClass 标记为默认的存储类。"

**一个容易踩的坑（官方专门说明了）**：

> 如果你有一个现成的 PVC，其 `storageClassName` 为 `""`，而你配置了默认 StorageClass，**那么该 PVC 将不会被更新。**

> **换句话说：显式写 `storageClassName: ""` 的 PVC 会一直保持"不要默认类"，不会因为后来设置了默认 StorageClass 就被改。**

**`mountOptions` 的官方警告**：

> 如果卷插件不支持挂载选项但指定了挂载选项，**制备将会失败**。挂载选项在类和 PV 上都**不会被校验**。如果挂载选项无效，**PV 挂载失败**。

> **所以挂载选项写错了，症状是"制备成功但 Pod 挂不上"，而不是 apply 就报错。**

**扩容（官方原文）**：

> 当下层 StorageClass 的 `allowVolumeExpansion` 字段设置为 `true` 时，以下类型的卷支持卷扩展。

| 卷类型 | 卷扩展的 Kubernetes 版本要求 |
|---|---|
| **CSI** | **1.24** |
| Azure File | 1.11 |
| FlexVolume | 1.13 |
| Portworx | 1.11 |
| rbd | 1.11 |

> **官方提醒**：**此功能仅可用于扩容卷，不能用于缩小卷。**

### 2.8 CSI：把厂商实现抽象成标准接口

**这是本篇最后一个大机制，也是现代云上存储的全部现实。**

**CSI 要解决的问题（回到 1.2 节的演进）**：

```
【树内插件时代】
  K8s 仓库
   ├── pkg/volume/awsebs/     ← AWS 的代码
   ├── pkg/volume/azuredisk/  ← Azure 的代码
   ├── pkg/volume/cephrbd/    ← Ceph 的代码
   └── ...  → 几十个厂商，K8s 核心被撑大，厂商无法独立发版

【CSI 时代】
  K8s 仓库
   └── 只定义接口（gRPC: CreateVolume / DeleteVolume / ControllerPublishVolume / NodePublishVolume ...）
          │
          ├── CSI 驱动 A（独立进程，独立发版）→ 云盘厂商 A
          ├── CSI 驱动 B（独立进程，独立发版）→ 存储厂商 B
          └── CSI 驱动 C（独立进程，独立发版）→ 自建 Ceph
```

**CSI 驱动在集群里的组成（这套 sidecar 架构是面试常考点）**：

| 组件 | 角色 | 官方要求的版本（RWOP 场景） |
|---|---|---|
| **csi-provisioner** | **控制面 sidecar**：watch PVC → 创建/删除卷 | `v3.0.0+` |
| **csi-attacher** | **控制面 sidecar**：把卷挂到节点上（Attach/Detach） | `v3.3.0+` |
| **csi-resizer** | **控制面 sidecar**：处理卷扩容 | `v1.3.0+` |
| **csi-snapshotter** | 控制面 sidecar：卷快照 | — |
| **node-driver-registrar** | **节点 sidecar**：在 kubelet 里注册自己 | — |
| **csi-plugin（真正的驱动）** | **节点 sidecar**：真正执行 mount | — |

> **"控制面 sidecar"和"节点 sidecar"是 CSI 的关键结构**：
> - **控制面 sidecar** 跑在控制面节点上，**和 apiserver 说话**（创建卷、挂载卷）
> - **节点 sidecar** 跑在每个工作节点上，**和 kubelet 说话**（真正做 mount）
> **一个 CSI 驱动通常是一组 DaemonSet + Deployment。**

**CSI 相关的 K8s 对象**：

| 对象 | 作用 |
|---|---|
| **CSIDriver** | 描述一个 CSI 驱动的**能力**（是否支持挂载、是否需要 attach、是否支持扩容、是否支持 RWOP） |
| **CSINode** | 记录每个节点上装了哪些 CSI 驱动及其版本 |
| **VolumeAttachment** | 记录"卷 ↔ 节点"的挂载关系（Attach 阶段的状态） |
| **VolumeSnapshot / VolumeSnapshotClass** | 卷快照与恢复（**需要 CSI 驱动支持**） |
| **VolumeSnapshotContent** | 快照的实际存储对象 |

**CSI 带来的三个实际收益**：

| 收益 | 说明 |
|---|---|
| **① 厂商独立发版** | 驱动是独立容器，**升级驱动不用升级 K8s** |
| **② K8s 核心瘦身为接口** | 新增存储后端**不用改 K8s 代码** |
| **③ 一套代码多云** | 同一个应用在不同云上只改 StorageClass 的 `provisioner` 和 `parameters` |

**CSI 迁移（Migration）**：

> 官方在 StorageClass 文档里提到 "vSphereVolume CSI 迁移"（CSI migration）——**把老的树内插件平滑迁移到 CSI 驱动**，**存量卷不用重建**。这是 v1.25+ 的能力（⚠️ 具体细节以官方对应版本页为准）。

### 2.9 StatefulSet 的 volumeClaimTemplates 与 Headless Service

**这一节回答三个必考问题。**

#### 问题 ①：为什么 StatefulSet 必须配 Headless Service

**官方原文（限制章节）**：

> **StatefulSet 当前需要无头服务来负责 Pod 的网络标识。你需要负责创建此服务。**

**官方原文（稳定网络 ID）**：

> StatefulSet 中的每个 Pod 根据 StatefulSet 的名称和 Pod 的序号派生出它的主机名。组合主机名的格式为 `$(StatefulSet 名称)-$(序号)`。……StatefulSet 可以使用无头服务（Headless Service）控制它的 Pod 的网络域。管理域的这个 Service 的格式为：`$(Service 名称).$(名字空间).svc.cluster.local`……一旦每个 Pod 创建成功，就会得到一个匹配的 DNS 子域，格式为：`$(pod 名称).$(所属服务的 DNS 域名)`，其中所属 Service 由 StatefulSet 的 **`serviceName`** 域来设定。

**官方给的完整命名对照表（这张表值得背）**：

| 集群域名 | Service（ns/name） | StatefulSet（ns/name） | StatefulSet 域名 | Pod DNS | Pod 主机名 |
|---|---|---|---|---|---|
| cluster.local | default/nginx | default/web | nginx.default.svc.cluster.local | `web-{0..N-1}.nginx.default.svc.cluster.local` | `web-{0..N-1}` |
| foo | foo/nginx | foo/web | nginx.foo.svc.cluster.local | `web-{0..N-1}.nginx.foo.svc.cluster.local` | `web-{0..N-1}` |
| kube.local | foo/nginx | foo/web | nginx.foo.svc.kube.local | `web-{0..N-1}.nginx.foo.svc.kube.local` | `web-{0..N-1}` |

**所以完整答案是三层因果：**

```
① 每个 Pod 需要一个稳定、唯一的网络标识
   → 需要每个 Pod 有独立的 DNS 名（web-0.web.ns.svc.cluster.local）
        ↓
② 要让 DNS 返回每个 Pod 的独立 IP，就必须是无头 Service
   → Headless 的 DNS 返回多个 A 记录（每个 Pod 一个 IP），见 L4
        ↓
③ StatefulSet 的 spec.serviceName 就是指定"用哪个 Service 当治理服务"
   → StatefulSet 控制器通过它给 Pod 分配稳定域名
```

**官方的一个实战提醒（DNS 负缓存，很实用）**：

> 取决于集群域内部 DNS 的配置，**你有可能无法查询一个刚刚启动的 Pod 的 DNS 名称**。……**负缓存**（在 DNS 中较为常见）意味着之前失败的查询结果会被记录和重用至少若干秒钟，即使 Pod 已经正常运行了也是如此。

> **官方给的解法**：
> - 直接查询 Kubernetes API（比如，利用 watch 机制）而不是依赖于 DNS 查询
> - **缩短 Kubernetes DNS 驱动的缓存时长**（通常这意味着修改 CoreDNS 的 ConfigMap，**目前缓存时长为 30 秒**）

#### 问题 ②：volumeClaimTemplates 为什么能实现"每个 Pod 一块独立持久卷"

**官方原文（稳定的存储）**：

> 对于 StatefulSet 中定义的每个 VolumeClaimTemplate，**每个 Pod 接收到一个 PersistentVolumeClaim**。在上面的 nginx 示例中，每个 Pod 将会得到基于 StorageClass `my-storage-class` 制备的 1 GiB 的 PersistentVolume。如果没有指定 StorageClass，就会使用默认的 StorageClass。当一个 Pod 被调度（重新调度）到节点上时，它的 `volumeMounts` 会挂载与其 PersistentVolumeClaim 相关联的 PersistentVolume。

**官方原文（卷申领模板的生效条件）**：

> 你可以设置 `.spec.volumeClaimTemplates` 字段来创建 PersistentVolumeClaim。这将为 StatefulSet 提供稳定的存储，如果：
> - 为卷申领指定的 **StorageClass 配置使用动态制备**
> - 集群已包含具有正确 StorageClass 和足够可用存储空间的 PersistentVolume。

**机制拆解**：

```
StatefulSet.spec.volumeClaimTemplates:     ← 模板（不是实例）
  - metadata:
      name: www                              ← 卷名
    spec:
      accessModes: [ReadWriteOnce]
      resources:
        requests:
          storage: 1Gi

        ↓ StatefulSet 控制器按序号展开（每副本一份）

web-0  →  PVC "www-web-0"  →  PV-a  → 挂到 web-0
web-1  →  PVC "www-web-1"  →  PV-b  → 挂到 web-1
web-2  →  PVC "www-web-2"  →  PV-c  → 挂到 web-2
```

**PVC 的命名规则**：**`<volumeClaimTemplate 名字>-<StatefulSet 名字>-<序号>`**（如 `www-web-0`）。

**为什么这个设计解决了"Pod 重建丢数据"**：

| 机制 | 作用 |
|---|---|
| **Pod 名稳定**（`web-0`） | Pod 重建后**名字不变** |
| **PVC 名稳定**（`www-web-0`） | **PVC 不随 Pod 消失**（除非显式配删除策略） |
| **绑定关系稳定** | `web-0` 重建后，**还是去挂 `www-web-0` 那块卷** |
| **官方原文佐证** | "尽管 StatefulSet 中的单个 Pod 仍可能出现故障，但**持久的 Pod 标识符使得将现有卷与替换已失败 Pod 的新 Pod 相匹配变得更加容易**"——**这就是"有状态"的关键机制** |

> **这就是"有状态"的真正含义**：**不是"数据在 Pod 里"，而是"Pod 死了，卷还在，而且新 Pod 认得它"。**

#### 问题 ③：删除 StatefulSet 会删掉数据吗

**官方原文（限制）**：

> 删除或者扩缩 StatefulSet 并**不会**删除它关联的存储卷。**这样做是为了保证数据安全**，它通常比自动清除 StatefulSet 所有相关的资源更有价值。

**但有一个 v1.32 GA 的新选项要讲（`persistentVolumeClaimRetentionPolicy`）**：

**官方原文（GA since v1.32）**：

> 在 StatefulSet 的生命周期中，可选字段 `.spec.persistentVolumeClaimRetentionPolicy` 控制是否删除以及如何删除 PVC。……对于你可以配置的每个策略，你可以将值设置为 `Delete` 或 `Retain`。

| 策略 | 值 | 行为 |
|---|---|---|
| **`whenDeleted`** | `Retain`（默认）/ `Delete` | 删除 StatefulSet 时应用的卷保留行为 |
| **`whenScaled`** | `Retain`（默认）/ `Delete` | 副本数减少时应用的卷保留行为 |

**官方特别强调的边界（这条很多人不知道）**：

> 请记住，这些策略**仅**适用于由于 StatefulSet 被删除或被缩小而被删除的 Pod。例如，**如果与 StatefulSet 关联的 Pod 由于节点故障而失败，并且控制平面创建了替换 Pod，则 StatefulSet 保留现有的 PVC。现有卷不受影响，集群会将其附加到新 Pod 即将启动的节点上。**

> **一句话总结**：
> - **节点故障导致 Pod 重建 → 卷一定保留**（这是数据安全的底线）
> - **你主动删 StatefulSet / 缩容 → 默认也保留**，但可以显式改成 `Delete`

### 2.10 关键参数与默认值表

| 参数 | 默认 | 含义 | 实务要点 |
|---|---|---|---|
| `PersistentVolume.spec.capacity.storage` | 必填 | 容量 | **必须 ≥ PVC 请求量** |
| `PersistentVolume.spec.accessModes[]` | 必填 | 支持的模式 | **只影响绑定匹配，不做写保护** |
| `PersistentVolume.spec.persistentVolumeReclaimPolicy` | **`Retain`**（手工创建时） | 释放后怎么办 | **生产建议 Retain**；动态制备时继承 StorageClass |
| `PersistentVolume.spec.storageClassName` | 空 = 无类 | 归属的类 | **无类的 PV 只能绑无类 PVC** |
| `PersistentVolume.spec.claimRef` | 空 | 预绑定 | **用来做静态制备的手工配对** |
| `PersistentVolume.spec.volumeMode` | `Filesystem` | 文件系统 / 原始块 | **与 PVC 的 volumeMode 必须匹配** |
| `PersistentVolume.spec.mountOptions` | — | 挂载选项 | **不校验，写错在挂载时失败** |
| `PersistentVolume.spec.nodeAffinity` | — | 拓扑约束 | 静态制备时用来限定可用区 |
| `PersistentVolume.status.phase` | — | Available/Bound/Released/Failed | **`Released` 需要人工处理** |
| `PersistentVolumeClaim.spec.accessModes[]` | 必填 | 请求的模式 | **RWO ≠ 一个 Pod** |
| `PersistentVolumeClaim.spec.resources.requests.storage` | 必填 | 请求容量 | — |
| `PersistentVolumeClaim.spec.storageClassName` | 空 = 用默认类 | 请求的类 | **`""` = 禁止动态制备** |
| `PersistentVolumeClaim.spec.volumeName` | 空 = 自动匹配 | 预绑定指定 PV | **绑不上就一直 Pending** |
| `PersistentVolumeClaim.spec.volumeMode` | `Filesystem` | 文件系统 / 块 | **与 PV 必须一致** |
| `PersistentVolumeClaim.spec.selector` | — | 标签选择器 | **非空时不能动态制备** |
| `PersistentVolumeClaim.spec.dataSource` | — | 从快照/其他 PVC 克隆 | 恢复场景 |
| `PersistentVolumeClaim.status.phase` | — | Pending/Bound/Lost | **Pending 是排障起点** |
| `StorageClass.provisioner` | **必填** | 用哪个插件制备 | CSI 驱动名 |
| `StorageClass.parameters` | 厂商特定 | 制备参数 | **最多 512 个，总长 ≤ 256 KiB** |
| `StorageClass.reclaimPolicy` | **`Delete`** | 动态 PV 的回收策略 | **生产改 Retain** |
| `StorageClass.volumeBindingMode` | **`Immediate`** | 何时绑定 | **多可用区必须改 WaitForFirstConsumer** |
| `StorageClass.allowVolumeExpansion` | false | 是否可扩容 | **只支持扩，不支持缩** |
| `StorageClass.mountOptions` | — | 挂载选项 | **不支持会导致制备失败** |
| `StorageClass.allowedTopologies` | — | 限制制备拓扑 | 与 WaitForFirstConsumer 配合 |
| `StatefulSet.spec.volumeClaimTemplates[]` | — | 每 Pod 一卷的模板 | **需要动态制备的 StorageClass** |
| `StatefulSet.spec.serviceName` | 必填 | 治理 Service | **必须是 Headless** |
| `StatefulSet.spec.podManagementPolicy` | `OrderedReady` | 启停策略 | `Parallel` 放宽顺序保证 |
| `StatefulSet.spec.persistentVolumeClaimRetentionPolicy` | **`Retain`** | 删/缩容时删不删 PVC | v1.32 GA |
| `StatefulSet.spec.revisionHistoryLimit` | **10** | 保留多少修订版本 | 超出被 GC |
| `StatefulSet.spec.ordinals.start` | `nil`（从 0 开始） | 起始序号 | v1.31 GA |

---

## 3. 动手

> 本节命令在 kind 集群（`day2`）上执行。**⚠️ 本地 kind 没有 CSI 驱动与云盘，动态制备（StorageClass → 自动造 PV）无法真实验证；静态制备可以完整跑通。**

### 3.1 emptyDir 与 hostPath 观察

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: day2-vol-demo
spec:
  containers:
  - name: writer
    image: busybox:1.28
    command: ["sh", "-c", "echo hello > /data/a.txt; sleep 3600"]
    volumeMounts:
    - name: scratch
      mountPath: /data
  volumes:
  - name: scratch
    emptyDir: {}
```

```bash
kubectl apply -f vol-demo.yaml
kubectl exec day2-vol-demo -- cat /data/a.txt     # 期望：hello
```

**容器崩溃后数据还在（emptyDir 的关键性质）**：

```bash
kubectl exec day2-vol-demo -- sh -c 'kill 1'   # 杀掉容器主进程
kubectl get pod day2-vol-demo                  # 容器重启
kubectl exec day2-vol-demo -- cat /data/a.txt   # 期望：hello（还在！）
```

> **这一步验证了官方那句"容器崩溃不会导致 Pod 从节点上移除，emptyDir 中的数据在容器崩溃时是安全的"。**
> **但注意：删 Pod 之后数据就没了。**

### 3.2 静态制备 PV + PVC 并绑定

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: day2-pv-local
spec:
  capacity:
    storage: 1Gi
  accessModes:
  - ReadWriteOnce
  persistentVolumeReclaimPolicy: Retain      # ← 生产推荐
  storageClassName: manual
  hostPath:
    path: /tmp/day2-pv
    type: DirectoryOrCreate
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: day2-pvc-local
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
  storageClassName: manual
```

```bash
kubectl apply -f pv-demo.yaml

# ① 绑定结果
kubectl get pv
kubectl get pvc
```

**看到什么算对**：

| 检查 | 命令 | 期望 |
|---|---|---|
| **PV 已绑定** | `kubectl get pv` | STATUS = **Bound** |
| **PVC 已绑定** | `kubectl get pvc` | STATUS = **Bound** |
| **PVC 指向 PV** | `kubectl get pvc day2-pvc-local -o jsonpath='{.spec.volumeName}'` | = `day2-pv-local` |
| **PV 指向 PVC** | `kubectl get pv day2-pv-local -o jsonpath='{.spec.claimRef.name}'` | = `day2-pvc-local` |

**挂载到 Pod**：

```yaml
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: day2-pvc-local
```

```bash
kubectl exec -it <pod> -- ls -la /mnt/data
# 写一个文件
kubectl exec -it <pod> -- sh -c 'echo persisted > /mnt/data/test.txt'
```

**验证持久性（这是本节的重点实验）**：

```bash
# 删 Pod（模拟 Pod 重建）
kubectl delete pod <pod>
# 等新 Pod 起来
kubectl exec -it <new-pod> -- cat /mnt/data/test.txt
# 期望：persisted —— 数据还在
```

### 3.3 StorageClass 与动态制备

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: day2-sc-local
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: kubernetes.io/no-provisioner
reclaimPolicy: Retain
volumeBindingMode: WaitForFirstConsumer
```

```bash
kubectl apply -f sc.yaml
kubectl get storageclass
```

> **`provisioner: kubernetes.io/no-provisioner` 的含义**：**这个类不自动造卷**（本地 kind 没有真实 CSI 驱动时最常用）。它只提供"类"这个标签 + 绑定策略。
> **⚠️ 本地 kind 默认带一个 `standard` 类（provisioner 是 `rancher.io/local-path`），它能真的动态制备本地目录卷。** **`kubectl get storageclass` 看到的实际名字以本地环境为准。**

**动态制备（用本地默认类）**：

```bash
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: day2-pvc-dyn
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
  # 不写 storageClassName → 用默认类
EOF

kubectl get pvc day2-pvc-dyn
kubectl get pv        # 应该有新的 PV 被自动创建出来
```

**看到什么算对**：

| 检查 | 命令 | 期望 |
|---|---|---|
| **PVC 绑定成功** | `kubectl get pvc day2-pvc-dyn` | STATUS = **Bound** |
| **PV 被自动创建** | `kubectl get pv` | 多出一个 PV，**RECLAIM POLICY 继承 StorageClass 的 `Retain`** |
| **PV 属于该类** | `kubectl get pv -o custom-columns=NAME:.metadata.name,CLASS:.spec.storageClassName` | CLASS 与 StorageClass 名一致 |

### 3.4 访问模式与回收策略实测

**实验 A：RWO 的 PVC 被两个 Deployment 引用（跨节点冲突）**

> ⚠️ **本地 kind 是单节点集群，做不出跨节点冲突。** 这个实验需要多节点集群（minikube 多节点 / 多节点 ACK）。
> **能做的替代实验**：一个 Deployment 副本数为 2，引用同一个 RWO PVC → **两个 Pod 调度到同一节点（本地只有 1 个节点）→ 能跑**。**这恰好证明了官方那句"RWO 允许同节点上的多个 Pod 访问"。** **在多节点生产集群上，同样的配置会随机失败**——**这就是"能跑但不可靠"的典型陷阱**。

**实验 B：删除 PVC，观察 PV 的 phase 变化**

```bash
# 删 PVC
kubectl delete pvc day2-pvc-local

# 看 PV
kubectl get pv
```

**两种结果（取决于回收策略）**：

| 回收策略 | `kubectl get pv` 的结果 | 底层数据 |
|---|---|---|
| **Retain** | PV 还在，STATUS = **Released** | **还在**（hostPath 目录里的文件还在） |
| **Delete** | **PV 消失** | 随卷插件回收而删除 |

**这就是"PVC 删除后数据是否还在"的完整答案。**

```bash
# 验证 Retain 下数据确实还在
ls -la /tmp/day2-pv/       # 在节点上（或 kind 节点容器里）
```

**实验 C：清理 Released 的 PV（Retain 的必要后续动作）**

```bash
kubectl get pv                       # 找到 Released 的 PV
kubectl patch pv <pv-name> -p '{"spec":{"claimRef":null}}'   # 清掉 claimRef
# 删掉 hostPath 里的数据后再删 PV 对象
kubectl delete pv <pv-name>
```

> **官方 Retain 流程的三步（对照）**：① 删除 PV 对象（**底层存储资产仍在**）→ ② 手动清除数据 → ③ 手动删除存储资产。
> **上例的 `claimRef: null` 是为了让 PV 回到可被复用/删除的状态**，⚠️ **生产上这一步要配合 StorageClass 的人工确认，避免误删正在用的卷**。

### 3.5 StatefulSet 每 Pod 一卷

```yaml
apiVersion: v1
kind: Service
metadata:
  name: day2-db
spec:
  clusterIP: None            # ← Headless，StatefulSet 必需
  selector:
    app: day2-db
  ports:
  - port: 3306
    targetPort: 3306
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: day2-db
spec:
  serviceName: day2-db       # ← 指向上面那个 Headless Service
  replicas: 3
  selector:
    matchLabels:
      app: day2-db
  template:
    metadata:
      labels:
        app: day2-db
    spec:
      containers:
      - name: db
        image: busybox:1.28
        command: ["sh", "-c", "echo pod-$$ > /data/whoami; sleep 3600"]
        volumeMounts:
        - name: data
          mountPath: /data
      volumeClaimTemplates:
      - metadata:
          name: data
        spec:
          accessModes: [ReadWriteOnce]
          resources:
            requests:
              storage: 1Gi
          storageClassName: <你的默认类>
```

```bash
kubectl apply -f sts.yaml

# ① 三个 Pod，序号有序
kubectl get pods -l app=day2-db
# NAME          READY   STATUS    RESTARTS   AGE
# day2-db-0     1/1     Running   0          30s
# day2-db-1     1/1     Running   0          20s
# day2-db-2     1/1     Running   0          10s

# ② 三个独立的 PVC
kubectl get pvc
# NAME           STATUS   VOLUME      CAPACITY   ACCESS MODES
# data-day2-db-0 Bound    pvc-xxxx    1Gi        RWO
# data-day2-db-1 Bound    pvc-yyyy    1Gi        RWO
# data-day2-db-2 Bound    pvc-zzzz    1Gi        RWO

# ③ 每个 Pod 看到的内容不同（证明是三块独立卷）
kubectl exec day2-db-0 -- cat /data/whoami
kubectl exec day2-db-1 -- cat /data/whoami
# 三个 Pod 的内容各不相同

# ④ 稳定 DNS（Headless 的效果）
kubectl run netcheck --rm -it --restart=Never --image=busybox:1.28 -- \
  nslookup day2-db-0.day2-db.default.svc.cluster.local
```

**看到什么算对**：

| 检查 | 命令 | 期望 |
|---|---|---|
| **Pod 名字有序** | `kubectl get pods -l app=day2-db` | `day2-db-0/1/2` |
| **创建是串行的** | 创建过程中的时间戳 | **web-1 在 web-0 Ready 之后才创建** |
| **PVC 一 Pod 一个** | `kubectl get pvc` | **3 个 PVC，名字带序号** |
| **卷内容独立** | `kubectl exec` 各 Pod `cat /data/whoami` | **三个不同的值** |
| **稳定 DNS** | `nslookup day2-db-0.day2-db.default.svc.cluster.local` | 解析到该 Pod 的 IP |

> **第 4 条是本节最关键的验证**：**"每个 Pod 一块独立卷"不是设计意图的描述，而是能观测到的事实。**

### 3.6 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| emptyDir 跨容器崩溃保留 | 杀容器后 `cat /data/a.txt` | 还在 |
| emptyDir 跨 Pod 删除丢失 | 删 Pod 后重建 | **没了** |
| 静态 PV 绑定 | `kubectl get pv` | Bound |
| 双向绑定记录 | `kubectl get pvc -o jsonpath='{.spec.volumeName}'` | PV 名 |
| 动态制备生效 | `kubectl get pv` | 出现新 PV |
| PV 继承回收策略 | `kubectl get pv -o custom-columns=...:.spec.persistentVolumeReclaimPolicy` | 继承 StorageClass |
| Retain 后 PV 变 Released | 删 PVC 后 `kubectl get pv` | Released，**对象还在** |
| StatefulSet 每 Pod 一 PVC | `kubectl get pvc` | N 个带序号的 PVC |
| StatefulSet 稳定 DNS | `nslookup <pod>.<svc>.<ns>.svc.cluster.local` | 解析成功 |

---

## 4. 资源清单逐字段

### 4.1 PersistentVolume

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: v1` | string | ✅ | API 组与版本 | 写 `apps/v1` → no matches for kind |
| `kind: PersistentVolume` | string | ✅ | 对象类型 | 拼错 → apply 报错 |
| `metadata.name` | string | ✅ | **合法 DNS 子域名** | 重复 → AlreadyExists |
| `spec.capacity.storage` | Quantity | ✅ | 容量 | **小于 PVC 请求 → 绑不上，PVC 一直 Pending** |
| `spec.accessModes[]` | list | ✅ | 支持的访问模式 | **不含 PVC 请求的模式 → 绑不上** |
| `spec.persistentVolumeReclaimPolicy` | enum | | **`Retain`**（手工创建默认）/ `Delete` / `Recycle`（已废弃） | 设 `Delete` + 误删 PVC → **数据不可恢复** |
| `spec.storageClassName` | string | | 归属的类；**空 = 无类** | **类不一致 → 绑不上**（无类的 PV 只能绑无类 PVC） |
| `spec.volumeMode` | enum | | `Filesystem`（默认）/ `Block` | **与 PVC 不一致 → 绑不上**（官方有绑定矩阵） |
| `spec.claimRef` | ObjectReference | | 预绑定 / 绑定记录 | 手工设置错了 → **别的 PVC 绑不上** |
| `spec.volumeName`（在 PVC 上） | string | | — | — |
| `spec.mountOptions[]` | list | | 挂载选项 | **不校验；无效 → PV 挂载失败** |
| `spec.nodeAffinity` | NodeSelector | | 拓扑约束 | 与 Pod 调度约束冲突 → **Pod Pending** |
| `spec.persistentVolumeSource` | — | ✅ | 实际存储（NFS / CSI / hostPath / local…） | 字段名写错 → apply 报错 |
| `.hostPath.path` | string | | hostPath 类型的路径 | 节点上不存在 → Pod 卡在 ContainerCreating |
| `.hostPath.type` | enum | | `Directory`/`DirectoryOrCreate`/`File`… | `DirectoryOrCreate` → **kubelet 会在宿主机自动建目录** |
| `.csi.driver` / `.csi.volumeHandle` / `.csi.fsType` | string | | CSI 卷配置 | `volumeHandle` 错 → 挂不上 |
| `.nfs.server` / `.nfs.path` | string | | NFS 配置 | 路径不存在 → 挂载失败 |
| `spec.capacity`（块模式） | — | | 原始块设备 | `volumeMode: Block` 时用 `volumeDevices` 而非 `volumeMounts` |

**`volumeMode` 绑定矩阵（官方原文整理）**：

| PV volumeMode | PVC volumeMode | 结果 |
|---|---|---|
| 未指定 | 未指定 | 绑定 |
| 未指定 | `Block` | **不绑定** |
| 未指定 | `Filesystem` | 绑定 |
| `Block` | 未指定 | **不绑定** |
| `Block` | `Block` | 绑定 |
| `Block` | `Filesystem` | **不绑定** |
| `Filesystem` | 未指定 | 绑定 |
| `Filesystem` | `Block` | **不绑定** |
| `Filesystem` | `Filesystem` | 绑定 |

> **一句话**：**未指定视为 `Filesystem`。两边的 `volumeMode` 必须"兼容"（都是 Block，或至少一边是 Filesystem 而另一边未指定）。**

### 4.2 PersistentVolumeClaim

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: v1` | string | ✅ | API 组与版本 | — |
| `kind: PersistentVolumeClaim` | string | ✅ | 对象类型 | — |
| `metadata.name` | string | ✅ | **合法 DNS 子域名** | — |
| `metadata.namespace` | string | | **命名空间级** | **Pod 与 PVC 必须同 ns** |
| `spec.accessModes[]` | list | ✅ | 请求的访问模式 | 请求了 PV 不支持的 → **绑不上** |
| `spec.resources.requests.storage` | Quantity | ✅ | 请求容量 | 超过所有可用 PV → **Pending** |
| `spec.storageClassName` | string | | 请求的类；**空 = 用默认类**；**`""` = 禁止动态制备** | 类名不存在 → **Pending** |
| `spec.volumeName` | string | | 预绑定指定 PV | **PV 已被别的 PVC 绑 → Pending** |
| `spec.volumeMode` | enum | | `Filesystem` / `Block` | 与 PV 不兼容 → **不绑定** |
| `spec.selector` | LabelSelector | | 按标签选 PV | **非空时不能动态制备**（官方明确） |
| `spec.dataSource` | TypedLocalObjectReference | | 从快照/其他 PVC 克隆 | 源不存在 → **Pending** |
| `spec.dataSourceRef` | TypedObjectReference | | 同上（跨 ns 引用） | — |
| `status.phase` | enum | | `Pending` / `Bound` / `Lost` | **Pending = 排障起点** |
| `status.capacity` | Quantity | | 实际容量（控制器写） | 你不能手动改 |
| `status.accessModes[]` | list | | 实际模式（控制器写） | — |
| `status.conditions[]` | list | | 详细原因 | **排障必看** |

**PVC 的 namespace 限制（官方原文，重要）**：

> PersistentVolume 卷的绑定是排他性的。由于 PersistentVolumeClaim 是命名空间作用域的对象，**使用 "Many" 模式（`ROX`、`RWX`）来挂载申领的操作只能在同一名字空间内进行。**

### 4.3 StorageClass

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: storage.k8s.io/v1` | string | ✅ | API 组与版本 | 写 `v1` → no matches for kind |
| `kind: StorageClass` | string | ✅ | 对象类型 | — |
| `metadata.name` | string | ✅ | 类名（**用户请求时用它**） | 重名 → AlreadyExists |
| `metadata.annotations.storageclass.kubernetes.io/is-default-class` | string | | **默认类标记** | 多个都标 true → **用最近创建的那个**（官方明确） |
| `spec.provisioner` | string | ✅ | **用哪个卷插件制备**（官方：必须指定） | 驱动没装 → **PVC 一直 Pending** |
| `spec.parameters` | map | | 厂商特定参数 | 参数名错 → **制备失败或造出不想要的盘** |
| `spec.reclaimPolicy` | enum | | **`Delete`**（官方默认）/ `Retain` | 生产不改 Retain → **误删 PVC 即数据丢失** |
| `spec.volumeBindingMode` | enum | | **`Immediate`**（官方默认）/ `WaitForFirstConsumer` | 多可用区用 Immediate → **Pod Pending 死锁** |
| `spec.allowVolumeExpansion` | bool | | 是否支持扩容 | 改大 PVC 时报"不支持扩容" |
| `spec.mountOptions[]` | list | | 挂载选项 | **不校验；插件不支持 → 制备失败；选项无效 → 挂载失败** |
| `spec.allowedTopologies` | list | | 限制制备拓扑（可用区） | 与 Pod 的 zone 约束冲突 → **Pending** |

**StorageClass 的参数限制（官方原文）**：

> 一个 StorageClass 最多可以定义 **512 个参数**。这些参数对象的总长度不能超过 **256 KiB**，包括参数的键和值。

---

## 5. 横向对比

### 5.1 emptyDir vs hostPath vs PVC vs 云盘

| 维度 | **emptyDir** | **hostPath** | **PVC（抽象层）** | **云盘（底层实现）** |
|---|---|---|---|---|
| **是什么** | Pod 级临时目录 | 挂宿主机路径 | **存储请求对象** | CSI 驱动管理的块/文件存储 |
| **作用域** | Pod | 节点 | **命名空间** | 集群/账号 |
| **Pod 重建** | ❌ 丢 | ✅ 留 | ✅ **留** | ✅ 留 |
| **跨节点** | ❌ | ❌ **绑死节点** | ✅（取决于访问模式与拓扑） | ✅（RWX 才行） |
| **多副本共享** | ❌（同 Pod 内可） | ⚠️ **仅同节点** | ⚠️ **RWO 不行；RWX 行** | ✅（RWX） |
| **生命周期** | 随 Pod | 随节点 | **独立于 Pod** | 独立 |
| **回收** | 随 Pod 自动删 | **不回收（要自己清）** | 由 PV 的回收策略决定 | 同左 |
| **安全** | 高 | **低** | 中（取决于驱动与 etcd 加密） | 中 |
| **上生产** | ✅ 临时数据 | ⚠️ **仅节点级 agent** | ✅ **通用方案** | ✅ 云上首选 |
| **成本** | 节点本地 | 节点本地 | 取决于底层 | **按量计费** |

> **一句话选型**：
> - **临时/缓存/同 Pod 共享** → **emptyDir**
> - **应用要独占且性能要好** → **PVC + 云盘（RWO）**
> - **多副本共享读写** → **PVC + NAS/NFS/CephFS（RWX）**
> - **节点级 agent 读宿主文件** → **hostPath**
> - **有状态应用** → **StatefulSet + volumeClaimTemplates（每 Pod 一卷）**

### 5.2 Immediate vs WaitForFirstConsumer

| 维度 | **Immediate**（默认） | **WaitForFirstConsumer** |
|---|---|---|
| **绑定时机** | PVC 创建即绑定/制备 | **等第一个 Pod 用它** |
| **PVC 初始状态** | 立刻 Bound（若有卷） | **Pending（正常！）** |
| **拓扑感知** | ❌ | ✅ **按 Pod 的调度约束选卷** |
| **多可用区** | ⚠️ **可能造出跨区卷 → Pod Pending 死锁** | ✅ **必须用** |
| **本地卷（local）** | ❌ | ✅ **官方推荐** |
| **运维含义** | **PVC Pending 一定是故障** | **PVC Pending 可能只是"在等消费者"** |
| **排查影响** | 简单 | **必须看 Events 判断是"等消费者"还是"真故障"** |

> **这个对比最重要的运维含义**：
> **用了 `WaitForFirstConsumer` 之后，"PVC 是 Pending"从一个明确的故障信号变成了一个模糊信号。**
> **排障时必须看 PVC 的 Events**——如果事件是"等待第一个消费者"，那是**正常的**；如果事件是"没有匹配的 StorageClass"或"容量不足"，那才是故障。
> **这也是本篇第 6 节把"PVC Pending"拆成三条的原因。**

### 5.3 Deployment vs StatefulSet 的存储模型

| 维度 | **Deployment** | **StatefulSet** |
|---|---|---|
| **存储分配** | **所有副本共享同一个 PVC**（或都不用） | **每个 Pod 一块独立 PVC**（`volumeClaimTemplates`） |
| **PVC 命名** | 你自己起的名字 | **`<模板名>-<sts名>-<序号>`**（自动） |
| **Pod 与卷的关系** | **无保证**（谁都能挂上去） | **严格绑定**（`web-0` ↔ `www-web-0`） |
| **Pod 重建后** | 可能挂到同一块卷（**也可能不是**） | **一定挂回原来那块卷** |
| **缩容** | — | **默认保留 PVC**（`persistentVolumeClaimRetentionPolicy`） |
| **必须配 Headless** | ❌ | ✅ **必须** |
| **启动顺序** | 并行 | **串行**（`OrderedReady`），前一个 Ready 才起下一个 |
| **典型场景** | 无状态 Web | MySQL / Kafka / ZooKeeper / Elasticsearch |

> **一个必须讲清的实战陷阱**：
> **Deployment + 一个 RWO 的 PVC + 副本数 > 1** → **在多节点集群上会随机失败**（两个 Pod 调度到不同节点，第二个挂不上）。
> **这不是 bug，是 RWO 的定义**（一个节点只能挂一次）。
> **正确做法**：要么用 RWX 存储，要么用 StatefulSet（每 Pod 一卷），要么副本数=1。
> **"能跑但不可靠"是这类配置最危险的地方**——本地单节点测试完全正常，一上生产就偶发失败。

---

## 6. 踩坑与排障（症状 → 状态 → 原因）

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **PVC 一直 Pending** | `kubectl get pvc` → Pending | **① `storageClassName` 写的类不存在**（拼错 / 集群里没这个 StorageClass） | `kubectl get storageclass` 核对类名；确认 CSI 驱动已装 | 类名用变量统一管理；装驱动后验证 `kubectl get csidrivers` |
| 同上 | Pending | **② 容量不够**：所有可用 PV 的容量都 < PVC 请求 | 扩 PV 容量或降低 PVC 请求；确认有动态制备能力 | **预留容量余量**；PVC 请求量按实际用量 + 增长 |
| 同上 | Pending，**Events 显示"等待第一个消费者"** | **③ `WaitForFirstConsumer` 正常等待**（不是故障） | **确认已有 Pod 引用了这个 PVC**；没有就建 Pod | **看到 Pending 先看 Events**，别直接判故障 |
| 同上 | Pending，**Pod 一直 Pending** | **WaitForFirstConsumer + Pod 用 `nodeName`** → 绕过调度器 → 死锁 | **把 `nodeName` 换成 `nodeSelector`**（官方明确警告） | **永远不用 `nodeName` 做亲和** |
| 同上 | Pending，事件报 **volume node affinity conflict** | **Immediate 模式 + 多可用区**：卷在 A 区，Pod 要在 B 区 | 改 StorageClass 为 `WaitForFirstConsumer`；或用 `allowedTopologies` | **多可用区集群的 StorageClass 必须用 WaitForFirstConsumer** |
| 同上 | Pending | **PV 的访问模式不支持 PVC 请求的模式**（如 PVC 要 RWX，PV 只有 RWO） | 换支持该模式的存储类 | 提需求时把**访问模式**写清楚 |
| 同上 | Pending | **PVC 与 PV 的 `volumeMode` 不兼容** | 两边都设成 `Filesystem`（或都用 `Block`） | 用模板生成，两边一致 |
| **Pod 卡在 ContainerCreating** | `kubectl describe pod` → `FailedMount` | **PV 的 `nodeAffinity` 与 Pod 调度到的节点冲突** | 调 PV 的 nodeAffinity，或调 Pod 的调度约束 | 静态制备时就把拓扑约束写对 |
| 同上 | `FailedMount` | **底层存储不可达**（NFS server 挂了 / CSI 驱动没装 / 云盘 API 报错） | 看 describe 里的具体错误；`kubectl get csidrivers` 确认驱动 | 驱动健康纳入监控 |
| **Pod 反复重启 / 数据丢了** | Pod `CrashLoopBackOff` | **数据写在容器可写层**（没挂卷）或 **`emptyDir`** → 容器重建即丢 | 用 PVC 挂到 `/data`；有状态服务用 StatefulSet + volumeClaimTemplates | **"数据必须外挂"** 是硬规则 |
| **多副本 Deployment 用同一个 RWO PVC，偶发挂不上** | 一个 Pod 挂载失败，另一个正常 | **RWO = 一个节点**；两个 Pod 调度到不同节点 → 冲突 | 改 RWX 存储；或用 StatefulSet；或副本数=1 | **RWO 的 PVC 不能给多副本无状态 Deployment 用** |
| **误删 PVC，数据没了** | PV 消失，底层卷也消失 | **回收策略是 `Delete`**（StorageClass 默认值） | **数据不可恢复**；只能从备份/快照恢复 | **生产 StorageClass 一律改 `Retain`**；删除 PVC 前先确认 |
| **误删 PVC，PV 变 Released，数据还在** | `kubectl get pv` → Released | **回收策略是 `Retain`**（符合预期） | 人工清理：删 PV 对象 → 手动清数据 → 手动删存储资产；或清 `claimRef` 后复用 | **建一个清理 Released PV 的自动化**（脚本或 Operator） |
| **PV 一直 Released 占着名额** | Released | **Retain 的必然代价**（官方：数据还在，该卷还不能用于其他申领） | 人工或自动化清理 | **监控 Released PV 数量** |
| **删了 StatefulSet，数据也没了** | PVC 消失 | **配了 `persistentVolumeClaimRetentionPolicy.whenDeleted: Delete`** | — | **默认是 Retain**；显式设 Delete 前想清楚 |
| 同上 | PVC 还在 | **默认 Retain**（官方默认，且 v1.32 GA） | 正常行为 | 知道默认行为 |
| **StatefulSet 的 Pod 能起来，但 DNS 解析不了** | `nslookup` 失败 | **① 没建 Headless Service**（官方：需要你负责创建） | 建 `clusterIP: None` 的 Service 并让 `serviceName` 指它 | **StatefulSet + Headless Service 是一对** |
| 同上 | DNS 有负缓存 | **Pod 刚创建，DNS 负缓存还没过期**（官方：CoreDNS 缓存当前 30 秒） | 等 30 秒；或直接 watch API；或缩短 CoreDNS 缓存 | **有状态服务启动要预留 DNS 收敛时间** |
| **StorageClass 写了 mountOptions，制备失败** | PVC Pending / 事件报错 | **卷插件不支持该挂载选项**（官方：制备会失败） | 去掉 mountOptions | 换插件前先查它支持哪些选项 |
| **改了 PVC 的 size，没生效** | PVC 容量不变 | **`allowVolumeExpansion` 没开**，或驱动不支持 | 设 `allowVolumeExpansion: true`（CSI 需 v1.24+）；驱动侧也要支持 | 需要扩容的类**一开始就开**这个开关 |
| **想缩容卷** | — | **官方：只支持扩，不支持缩** | 只能新建卷 + 迁移数据 | 容量规划一次给足 |
| **`kubectl get endpoints` 之外还看到 EndpointSlice** | — | 正常，**EndpointSlice 是现在的转发依据** | — | 用 endpointslice 排查（信息更全） |
| **`kubectl port-forward svc/xxx` 失败** | — | **该 Service 没有 selector**（官方：不映射到 Pod 的端点不允许被代理） | 改用 EndpointSlice 指定真实后端 | selectorless Service 是高级用法 |
| **hostPath 的目录被 kubelet 自动创建了** | — | `hostPath.type: DirectoryOrCreate` | 生产改 `Directory` | **hostPath 慎用**，且要显式 type |

**PVC Pending 的三步排查（背下来）**：

```bash
① kubectl describe pvc <name>
   → 看 Events：
     · "waiting for first consumer"        → 正常，等 Pod
     · "no persistent volumes available"   → 没有匹配的 PV/类
     · "storageclass ... not found"        → 类名错或类不存在
     · "volume node affinity conflict"     → 拓扑冲突
        │
② kubectl get storageclass
   → 类在不在？provisioner 是谁？驱动装了吗？
        │
③ kubectl get pv
   → 有没有可用 PV？容量够吗？访问模式匹配吗？类一致吗？
   → PV 的 STATUS 是什么（Available / Released / Failed）？
```

> **第 ① 步的 Events 是核心**——**它直接把"等消费者"和"真故障"分开。**
> **第 ③ 步的 PV STATUS 也很关键**：**`Released` 的 PV 是"占用着但用不了"的僵尸**，是常见的隐性原因。

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/storage/persistent-volumes/` | PV/PVC 介绍、制备/绑定/使用/回收生命周期、静态与动态制备、PV 字段、访问模式四种定义与各卷插件支持表、回收策略 Retain/Recycle/Delete 与 Retain 三步手工回收、类与默认 StorageClass、原始块卷、卷快照与克隆 | ✅ 2026-10-24 |
| S2 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/storage/storage-classes/` | StorageClass 定义与"配置文件"类比、provisioner/parameters/reclaimPolicy 字段、默认 StorageClass 与注解、内置与外部制备器、回收策略默认 Delete、卷扩展、**卷绑定模式 Immediate/WaitForFirstConsumer 与默认值**、allowedTopologies、参数限制 512 个/256 KiB、树内驱动弃用清单 | ✅ 2026-10-24 |
| S3 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/storage/volumes/` | 卷的重要性与两种问题、卷的工作方式、**emptyDir 的生命周期与 medium/sizeLimit**、configMap/downwardAPI 卷、hostPath 类型、CSI 卷类型 | ✅ 2026-10-24 |
| S4 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/workloads/controllers/statefulset/` | StatefulSet 定义与适用场景、**限制（需要无头服务、删 StatefulSet 不删卷）**、组件与 volumeClaimTemplates、Pod 标识与稳定网络 ID、**DNS 命名对照表与负缓存说明**、稳定的存储、部署扩缩保证、OrderedReady/Parallel、滚动更新、强制回滚、ControllerRevision 与回滚、**persistentVolumeClaimRetentionPolicy（v1.32 GA）**、副本数与 HPA | ✅ 2026-10-24 |
| S5 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/workloads/controllers/deployment/` | Deployment 与 StatefulSet 在存储/身份上的对照（跨篇口径一致） | ✅ 2026-10-24 |
| S6 | 官方 | `kubernetes.io/docs/reference/using-api/` | PV / PVC / StorageClass API 字段语义交叉验证 | ✅ 2026-10-24 |
| S7 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/services-networking/service/` | 无头服务（Headless）的行为（多个 A 记录、无 VIP）——StatefulSet 必需的原因 | ✅ 2026-10-24 |
| S8 | 官方（中文） | `kubernetes.io/zh-cn/docs/concepts/architecture/` | 控制面组件与 EndpointSlice 控制器（与存储控制流程的关系） | ✅ 2026-10-24 |
| S9 | 官方（中文） | `kubernetes.io/zh-cn/docs/reference/kubectl/quick-reference/` | `kubectl get/describe/exec/delete/scale`、PVC/PV 的常用查询命令 | ✅ 2026-10-24 |
| S10 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 上的云盘 StorageClass 与 CSI 驱动实践差异 | ✅ 2026-10-24 |
| S11 | 云厂商 | `cloud.tencent.com/document/product/457` | TKE 上 CBS/CFS 的 StorageClass 实践形态 | ✅ 2026-10-24 |

> **本篇没有引用任何 09-参考索引之外的 URL。** S2（StorageClass）、S3（Volumes）、S4（StatefulSet）三条为 **2026-10-24 晚补**：经索引《四、链接校验方法》的带重试脚本实测，返回 **200**，已按索引第五节"维护规则"第 1 条同步回 `09-参考索引/README.md`。
> **⚠️ 单源待补**：
> 1. **CSI 概念页的独立官方章节**（`csi` 锚点、CSIDriver/VolumeAttachment/CSI 迁移的完整描述）——本篇的 CSI sidecar 架构（csi-provisioner / csi-attacher / csi-resizer / node-driver-registrar）与版本要求引用自 **S1（PersistentVolume 页）的 RWOP 章节原文**，**CSI 整体机制标 `⚠️ 单源待补`**，建议把 CSI 专页补进索引。
> 2. **CSI 迁移（CSI Migration）的完整说明**——S2 只在一处提到 "vSphereVolume CSI 迁移"，**迁移的机制细节标 `⚠️ 单源待补`**。
> **⚠️ 待验证**：
> 1. **本地 kind 的默认 StorageClass 名称**（常见为 `standard`，provisioner 为 `rancher.io/local-path`）——**取决于 kind 安装时的配置，本篇未实测**，第 3.3 节已注明"以本地环境为准"。
> 2. **CoreDNS 的缓存时长 30 秒**——引自 S4 的官方原文（"目前缓存时长为 30 秒"），**建议本地 `kubectl -n kube-system get cm coredns -o yaml` 复核**（不同集群可能改过）。
> 3. **多可用区下 `Immediate` 导致的 `volume node affinity conflict`**——机制描述来自 S2 的原文（"这可能导致 Pod 无法被调度"）与 S1 的拓扑说明，**具体的 Events 报错原文未在本机实测**（本地 kind 单节点，无法复现），按官方文档表述转述。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入：一份 PVC 清单（`accessModes` / `resources.requests.storage` / `storageClassName`）。输出：**一块与 Pod 生命周期解耦的持久存储**。中间经过：**控制面控制器**（扫描 PVC → 找匹配的 PV，或按 StorageClass 动态制备 → 用 `claimRef`/`volumeName` 建立一对一绑定）→ **CSI 驱动**（控制面 sidecar 创建并挂载卷，节点 sidecar 做实际 mount）→ **kubelet**（把卷挂进容器）→ **应用**（读写数据）。**存储的"制备"与"使用"被分成了两个对象、两种角色，这是整个设计的核心。**
2. **能故意改坏再倒推吗？** —— 四个可执行的破坏实验：
   - **实验 A（PVC Pending：类名错）**：写一个 PVC，`storageClassName: does-not-exist`，apply 后 `kubectl get pvc`。**现象：STATUS = Pending。** 再 `kubectl describe pvc`，**看 Events 里关于 storageclass 的具体说明**。倒推原因：**类不存在 → 没有制备器 → 永远等不到卷。** 改回正确类名 → 立刻 Bound。
   - **实验 B（回收策略决定数据生死）**：分别用 `Retain` 和 `Delete` 两个 StorageClass 各建一个 PVC 并绑定，然后 `kubectl delete pvc`。**现象（Retain）：`kubectl get pv` 里 PV 还在，STATUS = Released；现象（Delete）：PV 直接消失。** 再看底层目录的数据——**Retain 下数据还在，Delete 下数据没了**。**这个实验一次性回答了"PVC 删除后数据是否还在"。**
   - **实验 C（StatefulSet 每 Pod 一卷）**：按 3.5 节建一个 3 副本的 StatefulSet，然后 `kubectl get pvc` 看是否出现 3 个带序号的 PVC，再 `kubectl exec` 各 Pod `cat /data/whoami`。**现象：3 个 PVC，3 份不同内容。** 倒推原因：**`volumeClaimTemplates` 被控制器按序号展开成 N 个独立 PVC，PVC 名稳定 → Pod 重建后认回同一块卷。**
   - **实验 D（RWO 的多副本陷阱）**：一个 Deployment 副本数 2，引用同一个 RWO PVC，**加 `nodeSelector` 强行把两个 Pod 分到不同节点**（⚠️ 本地 kind 单节点做不到，**需多节点集群**）。**现象：两个 Pod 里必然有一个挂不上，报 Multi-Attach / node affinity 冲突。** 倒推原因：**RWO 是"一个节点"，两个 Pod 在不同节点就冲突。** **本地单节点能跑通恰恰是这类配置最危险的地方。**
3. **能画上下游吗？** —— 画出「应用提交 PVC → 控制面控制器 watch PVC → 匹配 StorageClass → (a) 找到现成 PV 直接绑定 claimRef / (b) 调 CSI provisioner 动态制备卷 → PV ↔ PVC 一对一绑定 → Pod 调度到节点 → CSI attacher 把卷 attach 到该节点 → kubelet 调 CSI 节点插件做 mount → 容器看到 /data → Pod 删除 → PVC 删除 → 按 reclaimPolicy 处理 PV（Retain: Released / Delete: 连卷一起删）」，并标出每一步失败会看到什么：**Pending（类/容量/模式不匹配）/ ContainerCreating + FailedMount（拓扑冲突或存储不可达）/ 数据丢失（Delete 策略）/ PV 滞留（Retain 无人清理）**。

---

**下一篇**：[`L4-Service与网络`](L4-Service与网络.md)（回看 Service 与 Headless） · **上一篇**：[`L5-Ingress与配置管理`](L5-Ingress与配置管理.md) · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md)
