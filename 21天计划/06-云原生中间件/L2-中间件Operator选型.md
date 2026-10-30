# L2 · 中间件 Operator 选型（裸 StatefulSet / Operator 化 / Helm / 云托管，怎么选）

> **本篇定位**：中间件上 K8s 有**四种形态**，不是"上 K8s"这一个选项。本篇把四种形态的**能力边界与运维成本**摊开对比，把主流中间件 Operator 生态讲清楚（它们各自解决什么问题），给出一套**10 几人团队能落地**的选型路径与**备份恢复**通用方法。
> **前置**：[`L1-Operator模式与CRD.md`](L1-Operator模式与CRD.md)（**Operator 模式原理**：CRD + Controller + reconcile、Operator 与 Controller 的区别、**大多数团队不该写 Operator** 的判断标准） · [`../02-K8s核心/L2-K8s架构与对象模型.md`](../02-K8s核心/L2-K8s架构与对象模型.md)（StatefulSet 三要素、存储与调度） · [`../第3周/第16天.md`](../第3周/第16天.md)（Operator 实操入门） · [`../07-整合项目/L1-巨石应用云原生化.md`](../07-整合项目/L1-巨石应用云原生化.md)（改造路径中的阶段 8）
> **学完能做什么**：讲清中间件上 K8s 的四种形态各自能做什么、运维成本多少；说清 Operator 化相比"跑起来"多做了哪些事；给一个中间件做选型决策并说出取舍；设计一次**含恢复演练**的备份方案；判断某个中间件**该不该上 K8s**。
> **在 21 天里**：D16（中间件云原生化）的**深化篇**——D16 教"怎么装"，本篇教"**该不该装、装完怎么运维、出事怎么退**"。对应整合项目 L1 的**阶段 8**。
> **环境/版本基准**：本地 K8s **v1.37.x**（kind 集群 `day2`）。文中涉及的 Operator 版本以各项目官方文档为准，**本篇不引用具体版本号作为"推荐装这个版本"的结论**（版本兼容性必须查各自兼容矩阵）。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L2 中间件 Operator 选型
│
├─ 1. 概念
│   ├─ 1.1 中间件为什么是最难上 K8s 的一类
│   ├─ 1.2 四种形态：裸 StatefulSet / Operator 化 / Helm 部署 / 云托管
│   ├─ 1.3 术语表（中英对照）
│   └─ 1.4 "Operator 化"到底多做了什么（从跑到能运维）
│
├─ 2. 机制
│   ├─ 2.1 Operator 的工作方式：CRD + Controller + reconcile
│   ├─ 2.2 四种形态的运维成本模型
│   ├─ 2.3 有状态中间件的三个硬约束
│   ├─ 2.4 备份与恢复的通用方法（三条路线）
│   └─ 2.5 从裸部署迁到 Operator 的路径
│
├─ 3. 动手
│   ├─ 3.1 装一个 Redis Operator 并验证三件事
│   ├─ 3.2 数据迁移到 Operator 的四步
│   ├─ 3.3 做一次恢复演练（备份不做演练等于没备份）
│   ├─ 3.4 Operator 升级的兼容矩阵检查
│   └─ 3.5 什么时候干脆不该上 K8s
│
├─ 4. 逐字段表
│   ├─ 4.1 四种形态对照表（能力 / 成本 / 适用 / 风险）
│   ├─ 4.2 常见 Operator 对照表
│   └─ 4.3 CR 关键字段逐字段表
│
├─ 5. 横向对比
│   ├─ 5.1 Operator vs StatefulSet vs 托管服务
│   ├─ 5.2 Operator vs 自己写 CronJob 备份
│   └─ 5.3 阿里云托管数据库 vs 自建 Operator
│
├─ 6. 踩坑与排障
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 中间件为什么是最难上 K8s 的一类

无状态应用挂了重建就行，中间件不行。它有**三个硬约束**，K8s 的原生对象只解决了一部分：

| 约束 | 具体表现 | K8s 原生能解决到什么程度 |
|---|---|---|
| **数据不能丢** | Pod 重建后本地数据消失 | ✅ `PVC` + `volumeClaimTemplates` 让存储独立于 Pod 生命周期 |
| **身份不能变** | 集群成员要互相认识（Kafka broker id、ES 节点名、Redis 主从关系） | ✅ StatefulSet 的**有序命名**（`xxx-0` / `xxx-1`）+ Headless Service 给稳定 DNS |
| **启动顺序有讲究** | 主节点先起、从节点后加入；成员变更要按序 | ⚠️ StatefulSet 保证**有序启停**，但**"谁是主"这件事它不知道** |
| **故障要能自动处理** | 主库挂了要选主、副本落后要重建、存储挂了要换盘 | ❌ **完全不知道**——这是 Operator 的地盘 |

> **关键认知**：**StatefulSet 解决的是"有状态地运行"，Operator 解决的是"有状态地运维"。** 前者是 K8s 给的，后者是中间件厂商或社区给的。**把 StatefulSet 当 Operator 用，就会遇到 D2 场景 B 那种问题——Pod 重建了，但"谁是新主库"没人回答。**

### 1.2 四种形态：裸 StatefulSet / Operator 化 / Helm 部署 / 云托管

**这是本篇最核心的一张表**（4.1 有完整版，这里先给结论）：

| 形态 | 一句话 | 你要写多少东西 | 出事谁处理 |
|---|---|---|---|
| **① 裸 StatefulSet** | 自己写 yaml，把中间件跑起来 | **最多**：存储、探针、副本、选主全要自己写 | **你**（半夜也得是你） |
| **② Operator 化** | 声明一个 CR，控制器替你运维 | **最少**：写一个 CR + values | **控制器**（大部分自动） |
| **③ Helm 部署** | 用现成 Chart 渲染出 StatefulSet | **中等**：Chart 是现成的，但**运维逻辑还是你的** | **你** |
| **④ 云厂商托管** | 买一个托管实例，K8s 里只放连接串 | **最少**：连接串 + 网络策略 | **云厂商** |

> **③ Helm 和 ① 的关系必须讲清**：**Helm 是"打包与参数化"的技术，StatefulSet 是"运行形态"。** Helm Chart 装的中间件**照样是裸 StatefulSet**——它帮你把 yaml 复用起来，但**主从切换、扩缩容数据搬迁、版本升级编排这些运维动作，Chart 一个都不会替你做**。**这是很多人"我装了 Helm 就算 Operator 化了"的误解来源。**

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| CRD | 自定义资源定义 | 让 K8s 认识一种新资源（`kind: Redis`） |
| CR | 自定义资源 | CRD 的一个实例（"我要一个 3 主 3 从的 Redis"） |
| Controller / Operator | 控制器 | 跑 reconcile 循环、把实际态拉向期望态的程序 |
| reconcile | 协调（拉平） | 控制器反复比对期望态与实际态并动手 |
| StatefulSet | 有状态工作负载 | 稳定网络标识 + 稳定存储 + 有序启停 |
| Headless Service | 无头 Service | `clusterIP: None`，直接返回 Pod IP，用于有状态服务 DNS |
| `volumeClaimTemplates` | 卷声明模板 | StatefulSet 里为每个 Pod 自动创建的 PVC |
| PVC | 持久卷声明 | 存储的"使用方"；**PVC ≠ 备份** |
| AccessMode | 访问模式 | `ReadWriteOnce` / `ReadWriteMany` / `ReadWriteOncePod` 等，决定 PVC 能否跨节点挂载 |
| StorageClass | 存储类 | 描述"这一类 PVC 用什么存储"（云盘 / NAS / 本地盘） |
| `reclaimPolicy` | 回收策略 | `Delete` / `Retain`——**决定 PVC 删除后数据还在不在** |
| PDB | PodDisruptionBudget | 限制自愿中断时同时不可用的 Pod 数 |
| Leader Election | 领导者选举 | 多个 Operator 副本里选一个干活，避免打架 |
| Orchestration | 编排 | Operator 负责的"滚动升级""加副本"这类多步动作 |
| RPO | 恢复点目标 | 能接受丢多少数据（**时间维度**） |
| RTO | 恢复时间目标 | 能接受多久恢复（**时间维度**） |
| Pre-restore | 预恢复 | 把备份还原到临时位置，校验无误后再切读写 |
| Blue-Green 数据切换 | — | 数据层不能"蓝绿"，必须"切读写"而不是"切流量" |

### 1.4 "Operator 化"到底多做了什么（从跑到能运维）

**这是本篇的核心价值判断。** 一句话：

> **裸部署解决的是"跑起来"，Operator 解决的是"能运维"。中间的差距，就是半夜要不要起床。**

具体拆成六件事（每一件都是真实的运维动作）：

| # | 能力 | 裸 StatefulSet 里你要自己做什么 | Operator 怎么做 | 不做会怎样 |
|---|---|---|---|---|
| **1** | **备份与恢复** | 自己写 CronJob 跑 dump、写到对象存储、**自己验证能恢复** | Operator 提供备份 CR / 定时备份机制 | 有备份文件但恢复失败，**数据真的没了** |
| **2** | **主从切换 / 选主** | 半夜登进 Pod 敲 `slaveof` / `REPLICAOF`，还要把旧主重新同步 | 控制器检测到主故障 → 自动提升从库 → 重建旧主 → 触发全量同步 | 主挂了没人管 → **服务不可用直到人工介入** |
| **3** | **扩缩容时的数据搬迁** | 加副本时要自己规划：新节点怎么加入、怎么追数据、旧副本什么时候能退 | 控制器编排：加 Pod → 追数据 → 校验一致 → 才算就绪 | 加副本直接起 → **新副本数据不全 → 读到的数据是错的**（比不加更危险） |
| **4** | **版本升级编排** | 自己改镜像 tag → 手动按顺序重启 → 观察 → 出问题手动回滚 | 控制器按兼容矩阵编排升级顺序，失败自动中止 | 升级顺序错 → **新旧版本协议不兼容 → 集群不可用** |
| **5** | **监控指标内置** | 自己写 ServiceMonitor、写 exporter 部署、配 relabel | Operator 自带 ServiceMonitor，指标标签与 CR 标签对齐 | **指标有但没标签** → 看板按版本筛选失效 |
| **6** | **故障自愈** | 自己写守护逻辑或干脆不写 | 控制器持续 reconcile，Pod 被驱逐/存储异常会自愈 | **"跑起来了"但三天后就烂掉了** |

**第 3 条最容易被低估**。它是 Operator 相对裸部署**最大的增量价值**：

```
裸 StatefulSet 扩容一个副本：
  ① 改 replicas: 3 → 4
  ② K8s 起第 4 个 Pod
  ③ 第 4 个 Pod 启动，发现自己数据落后
  ④ ??? 没人告诉它该等谁、追谁的位点
  → 结果：要么它一直"追不上"永不 Ready，要么它"假装就绪"但数据是空的
```

**Operator 扩容一个副本：**
```
  ① 改 CR 的 replicas: 3 → 4
  ② 控制器创建第 4 个 Pod，但**不把它标为就绪**
  ③ 控制器等待它完成初始同步 / 加入集群
  ④ 数据一致后，控制器才把它纳入服务成员
  ⑤ 老副本此时才允许退出
```

> **这就是"Operator 化"的实质：它把 DBA 的处置经验写成了代码。** 而这段经验，**10 几人团队里往往没有 DBA**。

---

## 2. 机制

### 2.1 Operator 的工作方式：CRD + Controller + reconcile

```
用户写一个 CR（声明"我要什么"）
        │
        ▼
   CRD（让 apiserver 认识这个新 kind）
        │
        ▼
   Operator 控制器（Deployment，跑在集群里）
        │  watch 到 CR 变化
        ▼
   reconcile 循环：
     ① 读 CR 的 spec（期望态）
     ② 读实际的 StatefulSet / Pod / PVC（实际态）
     ③ 比对差异
     ④ 动手（建 Pod / 改配置 / 触发切主 / 编排升级）
     ⑤ 写 status（实际态回传给 CR）
        │
        ▼
   用户 kubectl get <kind> 看 status（和 Deployment 的用法一模一样）
```

| 机制 | 说明 | 与 K8s 原生控制器的关系 |
|---|---|---|
| **CRD** | 扩展 apiserver 的资源类型 | 类似内置的 `Deployment`，只是 schema 是自定义的 |
| **Controller** | 一个 Deployment 跑的业务逻辑 | 类似 `deployment-controller`，只是逻辑是中间件领域的 |
| **reconcile** | 持续比对期望态与实际态 | **和 K8s 原生控制器完全一样的模式** |
| **status 子资源** | CR 也有 `status`，反映实际态 | `kubectl get redis -o yaml` 能看到真实状态 |
| **finalizer** | 删除 CR 前先做清理 | 删 CR 时先做数据清理或拒绝删除 |
| **Leader Election** | 多副本里只让一个干活 | 避免两个控制器同时改同一份资源 |

> **"Operator 就是把领域知识写成控制器"** —— 这句话是理解 Operator 的钥匙。K8s 原生控制器管的是**通用编排**（起几个 Pod、滚动更新），Operator 管的是**领域编排**（谁当主、数据怎么同步、升级顺序是什么）。
>
> **机制细节（CRD schema 怎么写、reconcile 循环为什么与内置控制器同构、status / scale 子资源、printerColumns、Operator 与 Controller 的确切区别）见 [`L1-Operator模式与CRD.md`](L1-Operator模式与CRD.md)。** 本篇只取其中对**选型决策**有用的那一条结论：**Operator 能力强不代表该用它**——L1 给出的判断标准是"大多数团队根本不该写 Operator"，这条直接支撑本篇 3.5 节"什么时候干脆不该上 K8s"的结论。

### 2.2 四种形态的运维成本模型

**这张表是选型的核心依据。** "成本"不是指钱，是**"出事时你要投入的人力与时间"**。

| 成本维度 | ① 裸 StatefulSet | ② Operator 化 | ③ Helm 部署 | ④ 云托管 |
|---|---|---|---|---|
| **首次部署工作量** | 高（yaml 全手写） | **低**（写一个 CR） | 低（Chart 现成） | **最低**（控制台/一个连接串） |
| **日常运维工作量** | **高** | **低** | **高**（Chart 不替你运维） | **最低** |
| **升级中间件版本** | 手工编排，**易出错** | 控制器编排，失败自动中止 | 改 Chart values + 手工验证 | 控制台升级 / 托管侧负责 |
| **主从切换 / 选主** | **手工，半夜操作** | 自动 | **手工** | 自动 |
| **扩容时的数据搬迁** | **要自己写** | 控制器编排 | **要自己写** | 自动 |
| **备份恢复** | 自己写 CronJob | Operator 提供机制 | 自己写 CronJob | 托管侧提供（**看具体产品**） |
| **监控指标** | 自己写 ServiceMonitor | **内置** | 自己写 | 云监控 |
| **故障自愈** | 无（或者自己写守护） | **有** | 无 | 有 |
| **故障时的信息来源** | 你自己的经验 | 控制器 status + 事件 | 你自己的经验 | 厂商工单 + 控制台 |
| **出问题时谁能救** | **只有懂这个中间件的人** | 懂 Operator 的人（通常更少） | 懂中间件的人 | **厂商** |
| **学习成本** | 中（K8s + 中间件） | **高**（CR schema + Operator 概念 + 中间件） | 低 | **最低** |
| **版本锁定风险** | 无 | **有**（Operator 与中间件版本要匹配） | 无 | **有**（托管侧支持的版本有限） |
| **数据自主权** | **完全自主** | 完全自主 | 完全自主 | **在厂商手里** |
| **迁走的成本** | 低 | 中 | 低 | **高**（数据导出、格式转换） |

> **表里最刺眼的一行是"出问题时谁能救"**：裸部署和 Helm 都写"**懂这个中间件的人**"，Operator 写"**懂 Operator 的人**"。**在 10 几人团队里，"懂 Operator 的人"通常比"懂中间件的人"更稀缺**——这是选型时必须算进去的真实成本，而不是"Operator 更先进所以更好"。

### 2.3 有状态中间件的三个硬约束（选型时逐条对表）

| 硬约束 | 具体要求 | 选型时要问的问题 | 答不上来的后果 |
|---|---|---|---|
| **① 存储访问模式** | 中间件需要什么 `accessModes`？ | 能不能用 `ReadWriteOnce`（绝大多数可以）？还是必须 `ReadWriteMany`？ | **PVC 一直 Pending**——因为存储类不支持 RWX |
| **② 数据是否需要跨节点** | Pod 漂移后数据还在不在？ | StorageClass 是**网络存储**（云盘/NAS）还是**本地盘**？ | **本地盘 + Pod 漂移 = 数据不见了**（节点上的盘没跟着走） |
| **③ 备份的目标位置** | 备份存哪？ | 对象存储（OSS/S3）还是 PVC？ | **备份和被备份的中间件在同一块盘上 = 一起没了** |

**② 是最容易踩的一条**：

| StorageClass 类型 | Pod 漂移后 | 适合 |
|---|---|---|
| **本地盘（hostPath / local）** | **数据在新节点上不存在** | 单副本、且明确不迁移的开发环境 |
| **网络存储（云盘 / NAS）** | 数据还在 | **生产的所有有状态中间件** |

**再补一条 `reclaimPolicy`**：

| `reclaimPolicy` | 行为 | 生产建议 |
|---|---|---|
| `Delete` | PVC 删除时**连数据一起删** | 有备份且能接受时可用 |
| **`Retain`** | PVC 删除后**数据保留**，需手工清理 | **生产推荐**——防止误删 PVC 直接丢数据 |

> **组合起来是一条完整的选型检查单**：**访问模式对不对 × 存储是不是网络盘 × 回收策略是不是 Retain × 备份有没有落到对象存储**。四条里错一条，事故就可能以"数据丢失"的形式出现，而**这类事故不可回滚**。

### 2.4 备份与恢复的通用方法（三条路线）

**这一节是本篇最实用的部分**，因为**"备份不做演练等于没备份"**——但绝大多数团队的备份只有"生成"没有"恢复"。

#### 三条路线对比

| 路线 | 怎么做 | 优点 | 缺点 | 适合 |
|---|---|---|---|---|
| **A. Operator 提供的备份机制** | 声明一个备份 CR / 开启定时备份 | **和 Operator 版本兼容、知道该备份什么**、恢复流程是现成的 | **依赖 Operator 支持该功能**；备份策略仍要自己设计 | **有 Operator 且支持备份时，首选** |
| **B. 自己写 CronJob 备份** | CronJob 调中间件自带的 dump 工具 → 推到对象存储 | 灵活、能自定义保留策略 | **要自己保证一致性**（dump 时正在写入怎么办）、**要自己写恢复流程**、**要自己做校验** | 没有 Operator 备份功能时 |
| **C. 底层存储快照** | 对 PVC 的底层卷做快照 | **快、一致性由存储层保证**（卷级快照是崩溃一致的）、**恢复快** | **不是应用一致**（可能恢复到某个中间状态）；跨集群/跨区迁移要额外工具 | **数据量大、要求 RTO 短**；通常与 A/B 组合使用 |

#### 备份方案要回答的四个问题

| 问题 | 口径 | 为什么 |
|---|---|---|
| **RPO（能接受丢多少）** | 时间维度，如"最多丢 5 分钟" | 决定备份频率：**每小时备份 = RPO 1 小时** |
| **RTO（能接受多久恢复）** | 时间维度，如"30 分钟内恢复" | 决定恢复方案：**快照恢复快，逻辑 dump 慢** |
| **保留期** | 按级别分：最近 24 小时每小时、每天一份保留 N 天 | 决定存储成本与"能恢复到多久前" |
| **校验方式** | **定期真的恢复一次** | **这是最关键的一条，见下** |

#### 恢复演练：三种恢复方式

| 方式 | 怎么做 | 适合 | 注意 |
|---|---|---|---|
| **原地恢复（in-place）** | 用备份覆盖回原位置 | 快速止损 | **风险最高**：恢复失败会把现有数据也弄坏 → **恢复前必须先给当前数据做一次快照** |
| **恢复到临时实例（pre-restore）** | 备份还原到**另一个实例/目录**，校验无误后再切读写 | **生产默认推荐** | 多一份存储成本；切换步骤要写进 runbook |
| **校验式恢复（restore-to-verify）** | 定期把备份恢复到临时位置，**只验证数据可用，不切流量** | **日常演练用这个** | 不影响生产 |

> **"恢复演练"的标准动作（不是"跑一下备份脚本"）**：
> 1. 从**对象存储**取一份**真实的生产备份**（不是刚生成的测试备份）
> 2. 恢复到**临时实例**（不同的 Pod / 不同的 PVC）
> 3. **校验**：条数 / 关键业务数据抽样 / 关键接口能读
> 4. **记录恢复耗时**（这是你 RTO 的真实值，不是推测值）
> 5. **销毁临时实例**并清理临时存储
> 6. 把结果写进演练记录，**并对比上次的 RTO 变化**
>
> **第 4 步产出的数字才是真正的 RTO。** 没有这个数字，你的 RTO 只是写在文档里的一个愿望。

### 2.5 从裸部署迁到 Operator 的路径

**四个阶段，每阶段都可独立验证**（这与 L1 的分阶段思路一致）：

```
阶段 ①  存量清单与风险评估
          │  哪些中间件？数据多大？RPO/RTO 要求？能不能承受一次迁移动荡？
          ▼
阶段 ②  备份并验证可恢复              ← 【最关键的一步】
          │  用现有方式做一次完整备份，并真的恢复到一个测试环境验证
          ▼
阶段 ③  在 Operator 里声明新实例（不动老实例）
          │  同一套数据导入新实例，跑双写或只读校验
          │  ← 【此时是两个实例并存，回退成本最低】
          ▼
阶段 ④  校验数据一致 → 切读写 → 停老实例
          │
          ▼
阶段 ⑤  老实例保留一个观察期（只读），期满再清理
```

| 阶段 | 成功判据 | 失败点 | 回退方案 |
|---|---|---|---|
| ① 清单 | 每个中间件有：数据量、RPO/RTO、依赖方、可承受中断时长 | **漏了依赖方** → 切读写时下游连错地址 | 补齐再动 |
| ② 备份验证 | **在测试环境成功恢复并通过数据校验**；记录恢复耗时 | **只备份不验证** → 恢复时才发现备份是坏的 | 换备份方式重来 |
| ③ 新实例声明 | Operator 里新实例 Ready，**数据导入完成** | **CRD 与既有 StatefulSet 冲突**（见第 6 节） | 删 CR，不动老实例 |
| ④ 切读写 | 数据比对一致（条数 + 抽样 + 关键业务校验） | **切换时双写不一致** → 数据分叉 | 切回老实例（此时老实例还在） |
| ⑤ 观察期 | 观察期内老实例只读保留，**不删** | 观察期内就删 → 出问题无退路 | — |

> **阶段 ③ 的"双实例并存"是整个迁移最重要的设计**：**它把"不可逆的切换"变成了"可逆的切换"。** 阶段 ⑤ 的"观察期再删"同理。**很多迁移失败，不是因为技术问题，是因为删得太早。**

---

## 3. 动手

### 3.1 装一个 Redis Operator 并验证三件事

> **说明**：本节命令来自 [`../第3周/第16天.md`](../第3周/第16天.md) 已验证的 SOP。**不同 Redis Operator 项目的 CRD 与字段不同**，以各自官方文档为准；本节演示的是"**装完之后要验证什么**"，这三点对任何 Operator 都适用。

```bash
helm repo add redis-operator https://ot-container-kit.github.io/helm-charts
helm install redis redis-operator/redis-operator -n middleware --create-namespace
kubectl get pods -n middleware
```

**声明一个 CR**（字段名以该项目文档为准）：

```yaml
apiVersion: redis.redis.opstreelabs.org/v1beta2
kind: Redis
metadata:
  name: redis-standalone
  namespace: middleware
spec:
  kubernetesConfig:
    image: redis:7.4.1-alpine
  storage:
    volumeClaimTemplate:
      spec:
        resources:
          requests:
            storage: 1Gi
```

**必须验证的三件事（这是"装了 Operator"和"Operator 在工作"的区别）**：

| # | 验证什么 | 怎么验 | 期望 | 不对说明什么 |
|---|---|---|---|---|
| **1** | **数据持久**（PVC 生效） | `kubectl get pvc -n middleware` | `Bound`，且**看 StorageClass 是不是网络盘** | 是本地盘 → Pod 漂移会丢数据 |
| **2** | **删 Pod 后重建，数据还在** | `kubectl delete pod <redis-pod>` → 等新 Pod Ready → 查数据 | 数据还在 | 不在 → **PVC 没绑上，或 `reclaimPolicy` 有问题** |
| **3** | **CR 的 status 反映了实际态** | `kubectl get redis redis-standalone -o yaml` 的 `status` 段 | 有就绪状态、成员列表、副本数 | status 是空的 → **控制器没在 reconcile，或权限不足** |

**验证第 3 点的命令**：

```bash
kubectl get redis redis-standalone -n middleware -o jsonpath='{.status}' | head -20
kubectl describe redis redis-standalone -n middleware | sed -n '/Events/,$p'
```

> **第 3 点是 Operator 与裸部署最直观的区别**：裸 StatefulSet 的"实际态"要看 Pod 和 PVC 拼，**Operator 把中间件层面的真实状态（谁是主、几个从、数据是否同步）直接写进了 CR 的 status**。**这是排障时的第一现场**——不用 exec 进 Pod 敲命令。

### 3.2 数据迁移到 Operator 的四步

对应 2.5 的阶段 ②③④：

```bash
# ① 备份老实例（逻辑 dump）
kubectl exec -n middleware <old-redis-pod> -- redis-cli BGSAVE
# 等 rdb 生成完成（看老实例的持久化配置决定落点）

# ② 把备份文件取到临时位置
kubectl cp -n middleware <old-redis-pod>:/data/dump.rdb ./dump.rdb

# ③ 导入新 Operator 实例（**先在老实例只读或停写窗口内做**）
kubectl cp ./dump.rdb -n middleware <new-redis-pod>:/data/dump.rdb
# 按 Operator 文档触发加载 / 重启实例

# ④ 校验数据一致
kubectl exec -n middleware <new-redis-pod> -- redis-cli DBSIZE
kubectl exec -n middleware <old-redis-pod> -- redis-cli DBSIZE
# 两者一致 → 再抽样比对关键 key 的值

# ⑤ 切读写：改应用连接串到新实例（走配置变更 → PR → GitOps 同步）
```

| 步 | 关键点 | 失败点 |
|---|---|---|
| ① 备份 | **必须在停写窗口或用一致性手段**（如 `BGSAVE` + 等完成） | 备份期间有写入 → 备份出来的数据是个时间点的混合体 |
| ② 取文件 | 用 `kubectl cp`，别用 `kubectl exec cat >`（大文件会截断） | 文件损坏 → 导入失败 |
| ③ 导入 | **按 Operator 文档走**（有的 Operator 提供 import 钩子，有的要手动） | 不知道要重启 → 数据没加载 |
| ④ 校验 | **不能只看条数**：条数一致但值不一致更可怕 | 只对条数 → 上了之后才发现数据错乱 |
| ⑤ 切读写 | **走 Git 配置变更**，不要手改 ConfigMap | 手改 → SelfHeal 拉回 → 连接串被打回 |

### 3.3 做一次恢复演练（备份不做演练等于没备份）

**这是本篇最该被执行的一条。** 具体动作（对应 2.4 的六步）：

```bash
# ① 从对象存储取一份真实的生产备份（不是刚生成的）
ossutil cp oss://<bucket>/<path>/dump-20261024.rdb ./verify.rdb

# ② 恢复到临时实例（不同 Pod / 不同 PVC，不动生产）
#    用 Operator 声明一个临时实例，或起一个独立的 Pod 挂空目录
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: redis-verify
  namespace: middleware
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 1Gi
  # storageClassName: <你的网络存储类>
EOF

# ③ 校验
kubectl exec -n middleware <verify-pod> -- redis-cli DBSIZE
#    再抽样：取若干已知 key 比对值

# ④ 记录恢复耗时（这是真实 RTO）
#    从 ① 的开始到 ③ 的通过，掐表

# ⑤ 清理临时实例与临时存储
kubectl delete pvc redis-verify -n middleware
```

**恢复演练记录模板**：

| 项 | 填什么 |
|---|---|
| 演练日期 / 执行人 | — |
| 备份来源 | 对象存储路径 + **备份生成时间** |
| 数据量 | 条数 / 文件大小 |
| **恢复耗时** | **从取文件到校验通过的实际分钟数 → 这是真实 RTO** |
| 校验方式 | 条数比对 / 抽样 key 比对 / 业务接口读校验 |
| **RPO 实测** | 备份生成时间 vs 最新写入时间之差 |
| 结论 | 满足 RPO/RTO 目标？ |
| 发现的问题 | 存储慢？权限不足？Operator 恢复流程要手工介入？ |
| 动作项 | 负责人 + 期限 |

> **"恢复耗时"这一个数字，比任何"我们有备份"的声明都有价值。** 它是**唯一能证明备份可用的证据**——等保测评里"数据备份恢复"控制点要的也正是这个：**不是"有备份策略"，是"能拿出一次恢复演练记录"**。

### 3.4 Operator 升级的兼容矩阵检查

**Operator 升级是"装完不是一劳永逸"的最直接体现**——它本身就是需要被运维的组件。

```bash
# ① 看清当前 Operator 与中间件版本
kubectl get deploy -n redis-operator -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl get redis -A -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.kubernetesConfig.image}{"\n"}{end}'

# ② 查 CRD 版本
kubectl get crd | grep -i redis

# ③ 升级前必做（顺序不能错）
#    a. 备份 + **验证可恢复**（见 3.3）
#    b. 查兼容矩阵：Operator 版本 ↔ K8s 版本 ↔ 中间件版本
#    c. 在非生产环境先升一遍
#    d. 生产 Operator 与中间件版本**都钉死**（不要跟 latest）
```

| 升级前必查项 | 为什么 | 漏了会怎样 |
|---|---|---|
| **Operator ↔ K8s 版本兼容** | 新 Operator 可能用了新 API | **Operator 起不来 → 集群里所有该类型的 CR 全部失去控制器**（服务还在，但没有人在管了） |
| **Operator ↔ 中间件版本兼容** | 控制器要调中间件的 CLI / API | 调不通 → 控制器 reconcile 失败 → **CR 的 status 不更新** |
| **CRD 版本（v1beta1 → v1）** | API 组迁移 | 旧的 CR 不被新版本识别 → **Operator 认为资源不存在 → 可能触发重建** |
| **备份可恢复** | 升级失败要能退 | 升级把数据搞坏 → **没备份就是数据永久丢失** |
| **多版本共存** | 升级期间新旧 Operator 并存 | 两个控制器同时改同一份资源 → 打架 |

> **"CRD 版本不兼容"这一条最隐蔽**：K8s 里 CRD 的 `apiVersion` 从 `v1beta1` 迁到 `v1`，**旧 CR 对象不会自动迁移**。如果新 Operator 只认 `v1`，旧 CR 就成了"孤儿资源"——`kubectl get` 可能还能看到，但**控制器不认它，不做任何 reconcile**。**这意味着"升级成功"的假象下，实例已经没人管了。**

### 3.5 什么时候干脆不该上 K8s

**这一节是本篇最"反潮流"但最重要的部分。**

**有状态中间件上 K8s 的失败案例极多**，因为它们有一个共同特征：**数据层的事故不可回滚**。应用层回滚 = 版本退回去，数据层回滚 = 数据已经变了。

| 场景 | 该不该上 K8s | 理由 |
|---|---|---|
| **团队 10 几人，无专职 SRE / DBA** | **不该自建，用云托管** | 数据层故障需要的是**厂商的值班团队和恢复工具链**，不是 K8s 的自愈能力 |
| **中间件是核心数据存储（数据库）** | **优先云托管** | 数据库的备份恢复、主从切换、版本升级是**厂商做了几十年的专门工程** |
| **只想做"多环境一键拉起"的中间件（Redis 缓存、ES 日志）** | **可以上 Operator** | 无状态倾向强、数据可重建、价值明确 |
| **有合规要求必须数据自持（金融/信创）** | **可以上，但要配齐人** | 必须有懂 Operator 的人 + 完整的恢复演练 + 异地备份 |
| **中间件要对外售卖 / 多租户** | **谨慎** | 多租户隔离、计费、配额在 K8s 上要自己解决 |
| **团队已经在自建 Operator** | 慎重评估 | **自研 Operator 的隐性成本最高**（见 5.3） |

**"团队规模决定上限"这个判断，要说清逻辑**：

```
中间件上 K8s 的收益 = 弹性 + 自愈 + 声明式交付
中间件上 K8s 的成本 = 出事时你能救回来

成本随团队规模上升，因为"救回来"需要：
  ① 有人半夜能被叫醒并判断问题在哪      ← 需要值班机制
  ② 有人知道该执行什么恢复步骤        ← 需要领域知识
  ③ 有人验证过恢复流程真的能跑通      ← 需要演练机制
  ④ 有人持续跟进 Operator 与中间件的版本兼容  ← 需要维护机制

10 几人团队：① 勉强（轮流值班）、② 稀缺、③ 做不到、④ 做不到
→ 结论：这个团队做"自建中间件"的期望收益 < 期望成本 → 用云托管
```

> **面试这么答**（这是本篇最能体现判断力的一段）：
> "中间件上 K8s 我不一刀切。**判断标准是出事的时候我们能不能救回来**——不是技术上能不能，是团队能力和值班机制上能不能。10 几人团队没有专职 SRE 和 DBA，凌晨主库挂了，判断'是磁盘问题还是复制问题''能不能在存储层面回滚'这种判断需要积累，不是看两篇文章能有的。**所以我的建议是：数据库用云托管，日志和缓存这类数据可重建的用 Operator 化部署。** 这不是保守，是算过期望成本。"

### 3.6 常用运维命令面板（Redis + PostgreSQL）

> **本节能做什么**：3.1–3.5 讲的是"怎么把中间件跑起来、怎么选型"，本节补的是**你真正值班 / 面试被追问时会敲的日常运维命令**——Redis 用 `redis-cli`、PostgreSQL 用 `psql` 与系统视图。每条配真实例子与"实战价值"。**这俩库是你面试的重点栈，务必练到能闭眼敲。**
>
> **重要前提**：下面命令默认你已连上实例。`redis-cli` 连远程用 `redis-cli -h <host> -p <port> -a <pass>`（密码建议走 `-a` 或 `REDISCLI_AUTH` 环境变量，避免明文落在命令行历史）；`psql` 连远程用 `psql -h <host> -p <port> -U <user> -d <db>`。生产连法（连接串、SSL、连接池）在配置管理篇讲，本段只列命令。

#### ① Redis · 实例健康与容量（INFO 分段解读）

```bash
redis-cli INFO                       # 全量：server/clients/memory/persistence/stats/replication/cpu/keyspace
redis-cli INFO memory                # 只看内存段：used_memory / used_memory_rss / mem_fragmentation_ratio
redis-cli INFO clients               # 客户端：connected_clients / blocked_clients / rejected_connections
redis-cli INFO stats                 # 统计：total_commands_processed / instantaneous_ops_per_sec / evicted_keys
redis-cli INFO server                # 版本/运行模式/运行天数：redis_version / uptime_in_days / redis_mode
redis-cli INFO keyspace              # 各 DB 的 key 数与 expired 数：db0:keys=1200,expires=300,avg_ttl=...
redis-cli DBSIZE                     # 当前 DB 的 key 总数（比 INFO keyspace 更轻量）
```

> **实战价值**：`INFO memory` 的 `mem_fragmentation_ratio`（RSS/used）>1.5 说明碎片高，考虑 `activedefrag` 或重启；`INFO stats` 的 `evicted_keys`/`expired_keys` 突增往往意味着内存淘汰或集中过期在拉低命中率；`INFO replication` 配合 ⑦ 看主从。面试常问"怎么判断 Redis 内存够不够"——就是这一组。

#### ② Redis · key 探查（KEYS 危险，用 SCAN）

```bash
redis-cli KEYS "*"                              # ⚠️ 生产禁用！单线程阻塞，key 多时直接卡死全实例
redis-cli --scan --count 100                    # ✅ 游标迭代，不阻塞：默认每次 100 条
redis-cli --scan --pattern "order:*" --count 500   # 按模式安全扫描
redis-cli TYPE order:1001                       # key 是什么类型（string/hash/list/set/zset）
redis-cli TTL order:1001                         # 剩余过期秒数（-1 永不过期，-2 已不存在）
redis-cli PTTL order:1001                        # 毫秒级剩余时间
redis-cli EXPIRE order:1001 3600                 # 设 1 小时过期（发布时给缓存键加版本+过期，天然失效）
```

> **实战价值**：`KEYS *` 在百万 key 实例上能卡几十秒，期间所有请求超时——**这是生产事故级操作**，面试必须主动说"生产用 SCAN 不用 KEYS"。`EXPIRE` 配合发布时的"缓存键版本化"（如 `order:v2:1001`）是缓存与发布解耦的常用手法：新版本写新前缀，旧前缀靠 TTL 自然失效，无需批量删。

#### ③ Redis · 基础读写与慢查询

```bash
redis-cli SET session:u1 "token-abc" EX 1800   # 写并设 30 分钟过期
redis-cli GET session:u1                         # 读
redis-cli SLOWLOG GET 10                         # 最近 10 条慢命令（时长/时间戳/命令）
redis-cli SLOWLOG LEN                           # 当前慢日志条数
redis-cli SLOWLOG RESET                         # 清空（排查前后对比用）
redis-cli CONFIG GET slowlog-log-slower-than    # 慢阈值（微秒，默认 10000 = 10ms）
```

> **实战价值**：`SLOWLOG` 是定位"某个操作突然变慢"的第一手证据——看到大 key 的 `HGETALL`/`SMEMBERS` 或 `KEYS` 就能解释延迟毛刺。面试被问"Redis 偶尔卡一下怎么查"→ `SLOWLOG GET` + `INFO stats` 的 `latest_fork_usec`（持久化阻塞）。

#### ④ Redis · 内存淘汰与配置热改

```bash
redis-cli CONFIG GET maxmemory             # 当前内存上限（0=不限制，危险）
redis-cli CONFIG GET maxmemory-policy     # 淘汰策略：noeviction/allkeys-lru/volatile-lru/...
redis-cli CONFIG SET maxmemory 4gb        # 热改上限（重启失效，需同步改配置文件）
redis-cli CONFIG SET maxmemory-policy allkeys-lru   # 热改策略（立即生效，重启失效）
redis-cli CONFIG REWRITE                  # 把当前 CONFIG SET 的结果写回配置文件（持久化热改）
```

> **实战价值**：`maxmemory-policy` 选错会误删数据——`volatile-lru` 只在带 TTL 的 key 里淘汰，没设过期的 key 永远不淘汰，内存打满后写请求直接报 `OOM command not allowed`；缓存场景一般用 `allkeys-lru`。面试要能说清各策略差异及"为什么缓存用 LRU 而不是 noeviction"。**`CONFIG SET` 只改运行时，一定记得 `CONFIG REWRITE` 或改配置文件，否则重启即丢。**

#### ⑤ Redis · 持久化与危险命令（慎用）

```bash
redis-cli BGSAVE               # 后台落 RDB（不阻塞），看 INFO persistence 的 rdb_last_bgsave_status
redis-cli BGREWRITEAOF         # 后台重写 AOF，压缩命令日志
redis-cli LASTSAVE             # 上次 RDB 成功的时间戳
redis-cli MONITOR              # ⚠️ 实时打印所有命令流，极其影响性能，仅临时抓现场，用完立刻 Ctrl-C
```

> **实战价值**：`BGSAVE`/`BGREWRITEAOF` 是"手动做一份检查点"的手段，配合备份演练（见 3.3）用；`MONITOR` 在高 QPS 实例上会让 Redis 额外开销飙升，**生产只在万不得已排障时开几秒**，这是面试体现"知道危险边界"的细节。

#### ⑥ Redis · 命中率计算（面试高频）

```bash
redis-cli INFO stats | grep -E "keyspace_hits|keyspace_misses"
# 命中率 = keyspace_hits / (keyspace_hits + keyspace_misses)
# 例：hits=99000, misses=1000 → 命中率 = 99000/100000 = 99%
```

> **实战价值**：命中率掉通常意味着：① 缓存被大量淘汰（内存不够/策略错，回看 ④）；② 集中过期（TTL 设成同一时刻，回看 ②）；③ key 设计粒度太粗或雪崩。这是"缓存到底有没有用"的量化指标，面试官常让你现场算。

#### ⑦ Redis · 主从 / 哨兵 / 集群状态核查

```bash
redis-cli INFO replication   # role:master/slave、connected_slaves、slaveN:...、master_repl_offset
redis-cli ROLE               # 返回角色+复制偏移+从节点列表（更结构化）
redis-cli INFO sentinel       # 哨兵模式下的哨兵状态（若以 sentinel 运行）
redis-cli CLUSTER INFO        # 集群模式：cluster_state:ok / cluster_slots_assigned / cluster_known_nodes
redis-cli CLUSTER NODES       # 每个节点的 id/role/槽位/连通状态
```

> **实战价值**：主从延迟看 `master_repl_offset` 与从节点 `slave_repl_offset` 的差值；集群 `cluster_state:fail` 说明有槽位不可用（部分 key 写不进去）。面试被问"主从切换后应用连不上"→ 检查客户端是否直连 Pod IP 而非走 Service/哨兵/集群代理（见第 6 节踩坑）。

#### ⑧ PostgreSQL · 库表结构探查（psql 元命令）

```bash
psql -c "\l"                      # 列出所有数据库（含大小、owner）
psql -c "\dn"                     # 列出 schema
psql -c "\dt"                     # 当前库所有表
psql -c "\d+ orders"              # 表的字段、类型、索引、约束、表大小（排表结构问题第一入口）
psql -c "\d index_orders_user_id" # 看某个索引定义
psql -c "\dv"                     # 视图列表
```

> **实战价值**：`\d+` 是"表为什么慢/为什么锁"的起点——一眼看到缺索引、看到行数估算（`reltuples`）、看到表膨胀（`\d+` 的 `Size` 与 `pgstattuple` 对比）。面试常从"这张表怎么排查"开场。

#### ⑨ PostgreSQL · 执行计划（EXPLAIN ANALYZE）

```bash
psql -c "EXPLAIN ANALYZE SELECT * FROM orders WHERE user_id = 123;"
# 看：是否走索引（Index Scan vs Seq Scan）、actual rows vs estimated rows、是否 Sort/Hash 占大头
psql -c "EXPLAIN (ANALYZE, BUFFERS) SELECT ..."   # 连缓冲命中一起看，定位是否读磁盘
```

> **实战价值**：`Seq Scan` 出现在大表上 = 缺索引或统计信息过期；`estimated rows` 与 `actual rows` 差几个数量级 = 该 `ANALYZE` 了（见 ⑫）。这是 SQL 性能排障的核心命令，面试必考。

#### ⑩ PostgreSQL · 实时会话与锁（pg_stat_activity）

```bash
# 当前所有连接 + 正在执行的 SQL + 状态
psql -c "SELECT pid, state, wait_event_type, query FROM pg_stat_activity WHERE state IS NOT NULL;"
# 长事务（占用连接、可能挡 VACUUM）
psql -c "SELECT pid, now()-xact_start AS dur, query FROM pg_stat_activity WHERE xact_start IS NOT NULL ORDER BY dur DESC;"
# 锁等待链：谁被谁堵着
psql -c "SELECT blocked.pid AS blocked_pid, blocking.pid AS blocking_pid, blocked.query
FROM pg_locks bl JOIN pg_stat_activity blocked ON bl.pid=blocked.pid
JOIN pg_locks kl ON bl.locktype=kl.locktype AND bl.obj=kl.obj AND bl.pid<>kl.pid
JOIN pg_stat_activity blocking ON kl.pid=blocking.pid WHERE NOT bl.granted;"
# 连接数水位
psql -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY state;"
```

> **实战价值**：`pg_stat_activity` 是 PG 排障的总入口——慢查询、长事务、连接打满（`max_connections` 耗尽）、锁等待都从这儿看。`state='idle in transaction'` 长时间存在 = 应用没提交事务，会挡 VACUUM 并占连接。面试被问"数据库突然慢/连不上"→ 先看 `pg_stat_activity`。

#### ⑪ PostgreSQL · 慢查询源头（pg_stat_statements）

```bash
# 最耗时的 SQL Top10（需提前 CREATE EXTENSION pg_stat_statements; 并配 shared_preload_libraries）
psql -c "SELECT query, calls, total_exec_time, mean_exec_time
FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;"
# 重置统计（对比优化前后）
psql -c "SELECT pg_stat_statements_reset();"
```

> **实战价值**：`pg_stat_statements` 把"哪些 SQL 最费资源"量化出来，是做索引优化、SQL 改写前必须先拿的证据。面试答"怎么定位慢查询"→ `pg_stat_statements` 排 Top，再 `EXPLAIN ANALYZE` 看单条。

#### ⑫ PostgreSQL · 清理与分析（VACUUM / ANALYZE）

```bash
psql -c "VACUUM (VERBOSE, ANALYZE) orders;"   # 回收死元组 + 更新统计信息
psql -c "ANALYZE orders;"                      # 只更新统计信息（让 planner 选对执行计划）
psql -c "VACUUM FULL orders;"                  # ⚠️ 锁表重写，仅维护窗口用，生产慎用
# 看表膨胀情况
psql -c "SELECT relname, n_dead_tup, n_live_tup FROM pg_stat_user_tables ORDER BY n_dead_tup DESC;"
```

> **实战价值**：MVCC 下 UPDATE/DELETE 产生死元组，`VACUUM` 不回收会被撑大、拖慢顺序扫描；统计信息过期会让 planner 选错计划（呼应 ⑨）。`autovacuum` 开着也要监控它有没有跟上写入。面试常问"表越来越大/查询变慢但数据没多"→ 死元组 + 未 ANALYZE。

#### ⑬ PostgreSQL · 备份与恢复演练（pg_dump / pg_basebackup）

```bash
pg_dump -h <host> -U <user> -d appdb -F c -f appdb.dump    # 逻辑备份（自定义格式，可单表恢复）
pg_dump -h <host> -U <user> -d appdb -t orders -f orders.sql   # 只备份单表
pg_basebackup -h <primary> -D /backup/base -Ft -z -P          # 物理全量备份（PITR 基础）
psql -c "\l"                       # 恢复前先确认目标库
pg_restore -h <host> -U <user> -d appdb -t orders appdb.dump   # 从 dump 恢复单表
```

> **实战价值**：`pg_dump` 是逻辑备份（可跨版本、可单表），`pg_basebackup` 是物理备份（PITR 的基）——**备份不做恢复演练等于没备份**（呼应 3.3）。面试答"你们怎么备份 PG"→ 物理全量 + WAL 归档（见 ⑭）+ 定期恢复演练。

#### ⑭ PostgreSQL · WAL 归档与流复制延迟（pg_stat_replication）

```bash
# 流复制从库延迟（主库上执行）
psql -c "SELECT application_name, state,
  pg_wal_lsn_diff(sent_lsn, replay_lsn) AS replay_lag_bytes,
  write_lag, replay_lag FROM pg_stat_replication;"
# WAL 归档是否跟上
psql -c "SELECT * FROM pg_stat_archiver;"     # 看 archived_count / last_failed_time
# 当前 WAL 写入位置
psql -c "SELECT pg_current_wal_lsn();"
```

> **实战价值**：`pg_stat_replication` 的 `replay_lag` 是"从库落后主库多少"的量化指标——从库用来扛读流量时落后太多会读到旧数据。WAL 归档失败会让 PITR 链断，**归档必须监控**。这是主备架构面试的高频点。

#### ⑮ PostgreSQL · 角色与权限（\du / GRANT）

```bash
psql -c "\du"                       # 列出角色与属性（超级用户/建库/建角色）
psql -c "\dp"                       # 列出表级权限（谁对谁有什么权限）
psql -c "GRANT SELECT ON orders TO app_ro;"     # 授权（最小权限原则）
psql -c "REVOKE DELETE ON orders FROM app_rw;"   # 回收
psql -c "SELECT rolname, rolcanlogin, rolsuper FROM pg_roles;"
```

> **实战价值**：应用账号应遵循最小权限——写库给 `INSERT/UPDATE/DELETE`、只读账号只 `SELECT`，禁止应用用超级用户。**连接串（含账号密码）走配置中心，不进镜像、不进 Git**（配置管理篇详述）。面试被问"数据库账号怎么管"→ 按应用分角色 + 最小权限 + 连接串入配置中心。

#### ⑯ PostgreSQL · 连接池思路（PgBouncer）与 PITR 概念

```bash
# PgBouncer 不在数据库内，是独立连接池；常用运维是看/改其配置与状态
psql -h 127.0.0.1 -p 6432 -U <user> -d pgbouncer -c "SHOW POOLS;"   # 看各库连接池占用
psql -h 127.0.0.1 -p 6432 -U <user> -d pgbouncer -c "SHOW STATS;"   # 请求/命中统计
# PITR（时间点恢复）概念命令：基于 basebackup + 连续 WAL 恢复到任一时间点
#   pg_basebackup 做基 → archive_command 持续归档 WAL → 恢复时指定 target_time
#   recovery_target_time = '2026-10-29 14:00:00'  （具体语法随版本，恢复前务必演练）
```

> **实战价值**：**连接池解决"应用短连接把 PG 的 `max_connections` 打满"**——PgBouncer 用 `transaction` 池模式把大量前端连接复用成少量后端连接，是 PG 高并发标配。PITR 是"误删/误改后能回到出事前一刻"的能力，建立在 ⑬ 物理备份 + ⑭ WAL 归档之上。**这两块（池配置、PITR 恢复演练）在配置管理与恢复演练篇细讲，本段只给命令与边界。**

#### ⑰ 发布相关：缓存键版本化失效（命令级）

```bash
# 发布新版本时，旧版缓存前缀整体失效（不阻塞实例的安全删法）
redis-cli --scan --pattern "order:v1:*" --count 500 | xargs -L 100 redis-cli DEL
# 更稳妥：先确认数量再删
redis-cli --scan --pattern "order:v1:*" --count 500 | wc -l
# 连接串/迁移工具（Flyway/Liquibase）属于发布流水线，不在本段展开；
# 其原则是 schema 迁移随发布走、连接串入配置中心——命令层你只需确认：
psql -c "\dt"        # 发布后确认迁移生成的表/列已落地
psql -c "SELECT version FROM flyway_schema_history ORDER BY installed_rank DESC LIMIT 5;"  # 若用 Flyway，查迁移版本
```

> **实战价值**：`--scan | xargs DEL` 是"按前缀批量失效"的标准安全写法（绝不用 `KEYS` 取全量再删）。版本化前缀（`order:v1`→`order:v2`）让新旧发布共存期间互不影响，回滚也只需切前缀。连接串与迁移编排在配置管理/CI 篇，本段不展开。

---

## 4. 逐字段表

### 4.1 四种形态对照表（能力 / 成本 / 适用 / 风险）

| 维度 | ① 裸 StatefulSet | ② Operator 化 | ③ Helm 部署 | ④ 云厂商托管服务 |
|---|---|---|---|---|
| **部署形态** | 手写 yaml（StatefulSet + Headless Service + PVC + ServiceMonitor） | 声明一个 CR，控制器生成并管理底层资源 | 现成 Chart 渲染成 StatefulSet | 集群外实例，K8s 里只有连接串 |
| **核心能力** | **"跑起来"** | **"跑起来 + 能运维"**（备份/切主/扩缩容搬迁/升级编排/内置指标/自愈） | **"跑起来"**（Chart 只做模板与参数化） | **"跑起来 + 厂商负责运维"** |
| **日常运维成本** | **高** | **低** | **高** | **最低** |
| **首次部署成本** | 高 | 低 | 低 | 最低 |
| **升级中间件版本** | 手工，易出错 | 控制器编排，失败可中止 | 改 values + 手工验证 | 托管侧负责（**看产品**） |
| **主从切换 / 选主** | **手工** | 自动 | **手工** | 自动 |
| **扩缩容数据搬迁** | **要自己写** | 控制器编排 | **要自己写** | 自动 |
| **备份恢复** | 自己写 CronJob | **Operator 提供机制**（支持与否看项目） | 自己写 CronJob | 托管侧提供（**看产品**） |
| **内置监控指标** | 无 | **有**（ServiceMonitor 随 CR 生成） | 无 | 云监控 |
| **故障自愈** | 无 | **有** | 无 | 有 |
| **适用对象** | 有强定制需求、团队有领域专家的单一组件 | **数据可重建或可迁移的中间件**（缓存、日志、队列） | 团队要统一管理多套清单、但不需要深运维 | **数据库等核心有状态组件**（尤其小团队） |
| **团队要求** | 懂 K8s + 懂中间件 | **懂 Operator + 懂中间件**（门槛最高） | 懂 K8s + Helm | 懂网络与账号权限即可 |
| **主要风险** | 半夜手工操作，数据层不可回滚 | **Operator 与中间件版本不兼容**；社区项目维护节奏；CRD 版本迁移 | **误以为 Helm = Operator**（运维逻辑其实没有） | **数据在厂商手里**；版本受限；规格与跨可用区流量可能反而更贵 |
| **成本性质** | 人力成本高 | 人力成本中、社区维护成本有 | 人力成本中 | **规格费 + 跨可用区流量费 + 可能的实例规格溢价** |
| **迁走成本** | 低 | 中 | 低 | **高**（数据导出与格式转换） |
| **版本锁定风险** | 无 | 中 | 无 | **高** |
| **数据自主权** | 完全自主 | 完全自主 | 完全自主 | 在厂商手里 |

### 4.2 常见 Operator 对照表

> **说明**：以下都是**公开的社区或厂商开源项目**，本篇只讲**它们各自解决什么问题**与**选型时要注意什么**。具体版本、兼容性矩阵、CRD 字段以各项目官方文档为准。**本篇不提供这些项目的 URL（索引中未收录），需要时请查阅各项目官方站点**——这是数据纪律，不是省略。

| 项目 | 管理的中间件 | 核心能力（它替你做了什么） | 团队成本 | 选型注意 |
|---|---|---|---|---|
| **Prometheus Operator / kube-prometheus-stack** | Prometheus、Alertmanager、Grafana 及一批 exporter | **CR 化配置管理**：ServiceMonitor（抓什么）、PrometheusRule（告警规则）、PodMonitor、Grafana 看板声明；**自动生成抓取配置并热加载**，不用改配置文件重启 | **低**（多数团队已在用） | **label 匹配不上就不生效且不报错**（`release` label 要与 `serviceMonitorSelector` 一致）；CRD 较多，卸载要连带清 CRD |
| **Strimzi** | Kafka（Apache Kafka） | **管理 Kafka 集群全生命周期**：broker 滚动升级、**分区重分配**、topic 管理、CRD 化的 TLS 认证配置、**与 Operator 绑定的监控** | **中**（Kafka 本身就有复杂度） | **副本因子与 `min.insync.replicas` 仍要自己配对**（Operator 不替你保证数据不丢）；升级跨大版本要按它的升级路径走 |
| **ECK（Elastic Cloud on Kubernetes）** | Elasticsearch / Kibana / APM Server | **编排 ES 集群**：按角色分节点组、**安全的滚动升级**、**快照与恢复**（对接对象存储）、**Pod 伸缩与数据搬迁**、内置监控 | **中**（ES 本身就重） | ES 对宿主机内核参数有要求（如 `vm.max_map_count`）；资源紧张时需关 mmap；**快照≠备份**，恢复仍要演练 |
| **Redis Operator** | Redis（standalone / 主从 / 哨兵 / Cluster） | **主从与哨兵拓扑管理**、**故障时自动切主**、扩缩容时新旧主从同步编排、配置与密码管理、内置监控 | **中低**（社区项目多，选型要看维护状态） | **社区有多个不同项目，CRD 与能力差别很大**；**数据没声明成它管的类型 = 它不会管**（见第 6 节） |
| **CloudNativePG** | PostgreSQL | **主备切换**、**自动故障转移**、**逻辑/物理备份与 PITR（时间点恢复）**、**流复制与只读副本**、滚动升级编排、内置监控 | **中**（PostgreSQL 运维有深度） | **备份与 PITR 必须自己验证可恢复**；跨可用区拓扑要与云盘/网络盘能力匹配；升级要注意主备切换窗口 |

> **这张表的读法**：**前三个（Prometheus Operator / Strimzi / ECK）解决的是"编排复杂度"，后两个（Redis Operator / CloudNativePG）解决的是"数据层故障处理"。**
>
> **越往右，团队成本越高，但省下的半夜时间也越多。** 这就是本篇反复出现的那个权衡：**Operator 化买的是"半夜不用起床"，代价是"白天的学习成本 + 版本维护成本"。**

### 4.3 CR 关键字段逐字段表

> **字段名因项目而异**，本表列的是**跨项目通用的一类字段**（以"某 Redis Operator"为例说明字段语义），**不是任何具体项目的 schema**。

| 字段（语义） | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion` | string | ✅ | **CRD 的 API 组与版本** | 写错 → `no matches for kind` |
| `kind` | string | ✅ | 资源类型（如 `Redis`） | 拼错 → 同上 |
| `metadata.name` | string | ✅ | 实例名 | 重复 → `AlreadyExists` |
| `metadata.namespace` | string | ✅ | 命名空间 | 与应用不同 ns → **网络策略或 Service 选择器对不上 → 连不上** |
| `spec.<replicas>` | int | ✅ | 期望副本数 | **Operator 会据此编排数据搬迁**；写大 → 数据追不上 → 一直不就绪 |
| `spec.<image>` | string | ✅ | 中间件镜像与版本 | 用 `latest` → **Operator 与中间件版本组合不可控** |
| `spec.<storage>.volumeClaimTemplate` | object | | PVC 模板 | 不写 → 数据随 Pod 消失；**本地盘 → Pod 漂移丢数据** |
| `spec.<storage>...accessModes` | list | | 存储访问模式 | 写 `ReadWriteMany` 但存储类不支持 → **PVC 一直 Pending** |
| `spec.<storage>...resources.requests.storage` | Quantity | ✅ | 容量 | 太小 → 磁盘满 → **中间件写失败** |
| `spec.<config>` | object | | 中间件配置 | **把配置写死在这里 = 每次改配置要改 CR**（应尽量外置到 ConfigMap） |
| `spec.<resources>` | object | | 容器资源声明 | 不写 → BestEffort → **节点紧张时第一个被驱逐** |
| `spec.<backup>` | object | | 备份配置（若支持） | 备份周期过长 → **RPO 达不到要求** |
| `status` | object | — | **实际态**：就绪状态、成员列表、主从角色、数据同步进度 | **status 是排障第一现场**；不更新 → 控制器出问题 |
| `metadata.finalizers` | list | — | 删除前的清理钩子 | 配错 → **CR 卡在 Terminating 删不掉** |

---

## 5. 横向对比

### 5.1 Operator vs StatefulSet vs 托管服务

| 维度 | Operator 化 | 裸 StatefulSet | 云托管服务 |
|---|---|---|---|
| **"跑起来"这件事** | ✅ | ✅ | ✅ |
| **主从切换自动化** | ✅ | ❌ 手工 | ✅ |
| **扩缩容数据搬迁** | ✅ | ❌ 自己写 | ✅ |
| **版本升级编排** | ✅ | ❌ 手工 | ✅（看产品） |
| **备份机制** | ✅（看项目） | ❌ 自己写 CronJob | ✅（看产品） |
| **内置监控指标** | ✅ | ❌ 自己写 ServiceMonitor | 云监控 |
| **故障自愈** | ✅ | ❌ | ✅ |
| **数据自主权** | ✅ 完全自主 | ✅ 完全自主 | ❌ 在厂商手里 |
| **版本自由度** | 受 Operator 支持矩阵限制 | **完全自由** | 受厂商支持矩阵限制 |
| **学习成本** | 高 | 中 | **低** |
| **出事谁能救** | 懂 Operator 的人 | 懂中间件的人 | **厂商** |
| **成本结构** | 人力 + 节点资源 | 人力 + 节点资源 | **规格费 + 流量费（更贵）** |
| **适合** | 数据可重建/可迁移、团队有能力 | 强定制、团队有领域专家 | **数据库等核心组件；小团队默认选它** |

**决策树（可以直接用）**：

```
中间件是什么？
├─ 核心数据库（数据不可重建）
│   └─ 团队有 DBA + 值班机制？
│       ├─ 有 → Operator 化（自建/云厂商 Operator 均可）
│       └─ 没有 → 【云托管】（不要自建）
├─ 消息队列 / 搜索引擎 / 缓存
│   └─ 数据可重建 或 有完整备份恢复能力？
│       ├─ 有 → 【Operator 化】（收益最大）
│       └─ 没有 → 先云托管，或先用裸 StatefulSet + 强备份
└─ 日志 / 监控类
    └─ 【Operator 化】（Prometheus Operator / ECK）
```

### 5.2 Operator vs 自己写 CronJob 备份

| 维度 | Operator 提供的备份 | 自己写 CronJob 备份 |
|---|---|---|
| **一致性** | ✅ **由 Operator 保证**（知道什么时候能安全做快照/导出） | ❌ **要自己解决**——dump 时正在写入怎么办？ |
| **恢复流程** | ✅ 现成的 | ❌ **完全自己写**——备份文件在，恢复步骤也得自己写 |
| **保留策略** | ✅ 声明式 | 自己写逻辑 |
| **备份内容知道多少** | ✅ **知道中间件该备份什么**（不用自己搞清 RDB/AOF 的差异） | 要自己搞清 |
| **与版本兼容** | ✅ 随 Operator 一起测 | 自己负责 |
| **灵活性** | 受 Operator 支持范围限制 | ✅ **完全自由**（要跨集群备份、要特殊加密、要自定义上传逻辑） |
| **验证恢复** | ⚠️ **仍然要自己演练** | ⚠️ **同样要自己演练** |
| **实现成本** | 低（声明 CR） | 中（要写 dump、上传、清理、失败重试、通知） |
| **隐性成本** | 版本兼容风险 | **静默失败风险**：备份脚本跑了但备份是空的，**没人发现** |

**两者不互斥——最常见的组合是**：

```
CronJob 做**定期逻辑备份**（跨集群、跨账号、成本可控）
    +
存储快照做**兜底**（RTO 短，防逻辑损坏）
    +
定期**恢复到临时实例验证**（这是两者都不提供的，必须自己加）
```

> **必须说清的一条**：**Operator 提供了备份机制 ≠ 备份有效。** 备份机制只保证"按计划执行了"，**不保证"恢复得出来"**。**恢复演练仍然是必选项**——这是本篇最想让读者记住的一句话。

### 5.3 阿里云托管数据库 vs 自建 Operator

**这一节是给"本地民企"这个具体场景的。**

| 维度 | 阿里云托管数据库（RDS 等） | 自建 Operator（如 CloudNativePG 自建 PG） |
|---|---|---|
| **备份恢复能力** | ✅ **厂商级**：自动备份、时间点恢复、跨区域备份、**恢复流程经过大规模验证** | ⚠️ **自己保证**：Operator 提供机制，**恢复流程要自己演练** |
| **主从切换 / 高可用** | ✅ 托管的 HA 架构，切换由厂商负责 | ✅ Operator 也能做，**但依赖存储与网络能力** |
| **故障时的救援方** | **厂商工程师 + 工单** | **你自己**（半夜） |
| **版本升级** | 托管窗口内由厂商做 | 自己编排，**跨大版本要按 Operator 的升级路径** |
| **规格与成本** | ⚠️ **可能比自建贵**：托管实例规格溢价 + **跨可用区流量费** + 可能的存储类型溢价 | 节点成本按量，**但要自己算上运维人力与故障损失** |
| **跨可用区流量费** | ⚠️ **主备跨 AZ 时按流量计费**，大流量场景这是一笔实打实的成本 | 自建同样有跨 AZ 流量成本（云盘/NAS 跨 AZ 访问） |
| **数据自主权 / 迁走成本** | ❌ **高**：导出、格式转换、可能的性能差异 | ✅ 完全自主 |
| **合规要求** | ⚠️ 部分场景不满足"数据自持" | ✅ 满足 |
| **绑定风险** | ⚠️ 绑厂商 | ✅ 无 |
| **适合** | **绝大多数业务**；尤其是没有 DBA 的团队 | 有强合规/定制要求，或已有成熟 Operator 运维能力的团队 |

**"托管服务反而更贵"这个反直觉点，必须讲清**：

| 成本项 | 说明 |
|---|---|
| **规格溢价** | 托管实例的同等规格通常比裸 ECS 贵（**含管理、备份、HA 架构的价值**） |
| **跨可用区流量费** | **主备跨 AZ 同步走网络，按流量计费**；流量大时这笔钱可观 |
| **存储类型溢价** | 高性能云盘 / 本地 SSD 在托管产品里通常没有折扣 |
| **规格下限** | 托管产品最小规格往往比裸机高（为了塞下管控组件），**小负载场景浪费明显** |
| **隐性成本** | 备份空间、参数组、日志服务可能单独计费 |

> **但同时也要说清**：**自建 Operator 省下的钱，很快会被一次故障的损失吃掉。** 数据库故障的代价不是"运维人力"，是**业务停机 + 数据风险 + 恢复时间**。
>
> **正确的对比口径是**：
> ```
> 托管总成本 = 规格费 + 跨 AZ 流量费 + 备份费 − 故障损失期望值
> 自建总成本 = 节点成本 + 存储成本 + 跨 AZ 流量 + 运维人力 + 故障损失期望值
> ```
> **差别几乎全在"故障损失期望值"和"运维人力"上。** 10 几人团队，这两项算给托管，**几乎总是划算的**。

**一个折中方案（很多团队的正解）**：

```
核心数据库      → 云托管（把风险交给厂商）
缓存 / 日志 / 队列 → Operator 化（收益明确、风险可控）
自研业务服务     → 自己的 K8s 工作负载
```

**这个组合的好处是**：**把"必须懂"的东西（数据库）交给厂商，把"值得自己投"的东西（缓存与日志的弹性与声明式）留给自己。**

---

## 6. 踩坑与排障

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **Operator 装完了，中间件行为还是不对** | CR 创建了但状态异常 | **数据没声明成它管的类型**——原来的 StatefulSet 还在跑，Operator 创建了自己的实例，两者抢同一个 Service / 端口 | 确认谁在提供服务：查 Service 的 selector 指向谁；**把旧资源下线或迁走**（不要两边都留） | **迁移前先确认"谁是唯一的服务提供者"**；存量资源与新 CR 不能并存抢流量 |
| **CRD 与既有 StatefulSet 资源冲突** | apply 报错或资源互相覆盖 | 同名资源 / 同名 Service / selector 撞车；CRD 的 `apiVersion` 与存量资源不匹配 | 先把存量资源改名或下线，确认无引用后再声明 CR | **Operator 的 namespace 与应用分开**；**迁移走 2.5 的五阶段**，不直接覆盖 |
| **备份能生成，恢复失败** | 恢复时数据不全 / 恢复不出来 | **只做了备份没做恢复演练**；或备份期间有写入，dump 是个混合时间点；或恢复步骤本身没跑过 | **立刻做一次恢复到临时实例的验证**（见 3.3）；找出具体失败步骤并补进 runbook | **把"恢复演练"写进运维日历**（比如每月一次），记录真实 RTO |
| **恢复时把现有数据也弄坏了** | 生产数据损坏 | **原地恢复**且没有先给当前数据做快照 | 恢复前**必须先快照当前数据**；改为恢复到临时实例再切读写 | 用 **pre-restore 模式**；runbook 第一步永远是"先快照" |
| **Operator 升级把数据搞坏** | 数据异常 / 实例异常 | 升级前没备份、没演练；**Operator 与中间件版本不兼容**；**CRD 版本迁移导致旧 CR 成了孤儿资源** | 回滚 Operator 到旧版本；用备份恢复数据；**查清是版本不兼容还是 CRD 迁移问题** | **升级前必做**：备份 + 验证可恢复 + 查兼容矩阵 + 非生产先升；**Operator 与中间件版本都钉死** |
| **CR 删不掉，卡在 Terminating** | 资源无法删除 | **finalizer 里的清理逻辑卡住**（比如等某个 Pod 退不出、等存储释放） | 查 Operator 日志与该资源的 Events；必要时**先删子资源**再删 CR | finalizer 的清理逻辑要有超时；**删除前确认备份已生成** |
| **PVC 一直 Pending** | `Pending` | **存储类不支持所需的 `accessModes`**（最常见：要求 RWX 但只有 RWO）；或容量不足；或 `storageClassName` 写错 | `kubectl describe pvc` 看 Events；确认存储类支持的访问模式 | **选型阶段就查清存储类能力**（见 2.3） |
| **Pod 重建后数据没了** | 数据丢失 | **用了本地盘 StorageClass**，Pod 漂移到新节点后数据不在 | 换网络存储；**从备份恢复**（这会痛，但这就是没做对的地方） | **生产有状态中间件一律用网络存储**；`reclaimPolicy: Retain` |
| **PV 显示 Released，数据却还在** | 资源未回收 | `reclaimPolicy: Retain` → PVC 删了但 PV 与数据保留 | 手工确认数据后再清理 PV | `Retain` 时要配套"PV 巡检"任务，否则孤儿 PV 堆积 |
| **加副本后新副本数据是空的** | 副本不一致 | **裸 StatefulSet 扩容没有数据搬迁逻辑**——新 Pod 起来了但没加入复制组 | 用 Operator 化（它会编排同步）；或手工把新节点纳入复制组并等追平 | **扩容必须走 Operator 或走有数据搬迁的流程**，不能直接改 replicas |
| **把中间件当无状态应用，用 Deployment 部署** | Pod 名字随机 / 数据丢失 | **用了 Deployment 而不是 StatefulSet** | 改成 StatefulSet（+ Headless Service + `volumeClaimTemplates`）；数据从备份恢复 | **上 K8s 前先查：这个中间件的状态约束是什么**（2.3 的三条硬约束） |
| **Redis 主从切换后应用连不上** | 连接失败 | 应用连的是**具体 Pod IP**而不是 Service；或没配哨兵/代理 | 客户端走 **Service 或 Sentinel/Cluster 代理**，不直连 Pod | 客户端配置用 Service 名；**契约测试里加一条"主切换后仍可写"** |
| **Kafka 脑裂 / 消息丢失** | 数据不一致 | `replication.factor=1`；`min.insync.replicas` 没配；`unclean.leader.election` 开着 | **至少 3 broker + RF=3 + `min.insync.replicas=2` + 关闭 unclean 选举** | 这几项在 CR 的配置里显式写出来，**不要依赖默认值** |
| **ES 集群变黄 / 变红** | 集群状态异常 | 黄 = 副本未分配；红 = 主分片缺失 | `_cluster/health` → `_cluster/allocation/explain` → 看磁盘水位与节点状态 | 磁盘水位告警必须有；**升级路径要按 Operator 文档走** |
| **托管服务反而比自建贵** | 账单超预期 | **规格溢价 + 跨可用区流量费 + 存储类型溢价 + 规格下限** | 换更合适的规格档；**确认跨 AZ 拓扑是不是必须的**；评估流量费占比 | **上托管前先算清四项**（规格/流量/存储/备份），并和自建的运维人力成本对比 |
| **Operator 社区项目版本停更 / 与 K8s 不兼容** | 升级失败 | 项目维护节奏跟不上 K8s 版本 | 换活跃的项目或转云托管；**生产环境不要用没有维护的社区项目** | 选型时查**最近一次发版时间与 K8s 兼容矩阵**；**别在生产用 `latest`** |
| **Operator 多副本互相打架** | 状态反复变化 | **没启用 Leader Election**，两个副本同时 reconcile | 启用领导者选举；或把副本数设为 1 | 装完先确认 `spec.replicas` 与 Leader Election 配置 |
| **监控指标有了但没标签** | 看板按版本筛选失效 | Operator 生成的 ServiceMonitor 与你的 Prometheus `serviceMonitorSelector` 不匹配 | 检查 `metadata.labels` 与 selector 匹配 | 装完先 `kubectl get servicemonitor -A` 确认被选中 |
| **备份 CronJob 一直成功，但备份文件是空的** | 静默失败 | dump 命令失败但 CronJob 的退出码是 0；或挂载路径不对 | 加**备份后校验**（检查文件大小 / 条数），不合格就告警 | **备份任务必须自带校验**，不能只看 CronJob 状态 |
| **团队根本没人能半夜救中间件** | — | **组织问题，不是技术问题** | **改用云托管**，或建立值班机制 | 选型时把"值班机制"当硬约束——**没人值班就不能自建有状态中间件** |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `kubernetes.io/zh-cn/docs/concepts/extend-kubernetes/operator/` | **Operator 模式**：CRD + Controller + reconcile，与自定义控制器的关系 | ✅ 2026-10-24 |
| S2 | 官方 | `kubernetes.io/zh-cn/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/` | CRD 定义、版本转换（**v1beta1 → v1 迁移的坑**） | ✅ 2026-10-24 |
| S3 | 官方 | `book.kubebuilder.io/introduction` | controller-runtime 开发（**自研 Operator 的隐性成本来源**） | ✅ 2026-10-24 |
| S4 | 官方 | `kubernetes.io/zh-cn/docs/concepts/workloads/pods/` | Pod 生命周期、探针 | ✅ 2026-10-24 |
| S5 | 官方 | `kubernetes.io/zh-cn/docs/concepts/storage/persistent-volumes/` | **PV / PVC / StorageClass / accessModes / reclaimPolicy** | ✅ 2026-10-24 |
| S6 | 官方 | `kubernetes.io/zh-cn/docs/concepts/workloads/` | **StatefulSet 的三要素**（稳定标识 / 稳定存储 / 有序启停） | ✅ 2026-10-24 |
| S7 | 官方 | `kubernetes.io/zh-cn/docs/concepts/scheduling-eviction/assign-pod-node/` | 调度与节点亲和（有状态服务的落点约束） | ✅ 2026-10-24 |
| S8 | 官方 | `kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-pods/` | Pod 排障、PVC Pending 的排查路径 | ✅ 2026-10-24 |
| S9 | 官方 | `kubernetes.io/zh-cn/docs/concepts/configuration/configmap/` · `.../secret/` | 配置外置与密钥（**配置不要写死在 CR 里**） | ✅ 2026-10-24 |
| S10 | 官方 | `kubernetes.io/zh-cn/docs/concepts/cluster-administration/logging/` | 集群日志（日志管道与运维负担） | ✅ 2026-10-24 |
| S11 | 官方 | `kubernetes.io/zh-cn/docs/concepts/configuration/manage-resources-containers/` | requests/limits 与 QoS（**中间件容器也要声明资源**） | ✅ 2026-10-24 |
| S12 | 官方 | `prometheus.io/docs/introduction/overview/` | 拉模型与 exporter（**指标要靠 exporter 才有**） | ✅ 2026-10-24 |
| S13 | 官方 | `helm.sh/docs/topics/charts/` | Chart 结构（**Helm 部署 ≠ Operator 化**） | ✅ 2026-10-24 |
| S14 | 官方 | `helm.sh/zh/docs/chart_template_guide/` | Chart 模板与 values（中文） | ✅ 2026-10-24 |
| S15 | 官方 | `kubernetes.io/zh-cn/docs/concepts/security/rbac-good-practices/` | Operator 所需的最小权限（**Operator 权限过大 = 风险**） | ✅ 2026-10-24 |
| S16 | 官方 | `kubernetes.io/docs/reference/using-api/` | 资源清单字段语义交叉验证 | ✅ 2026-10-24 |
| S17 | 官方 | `kubernetes.io/zh-cn/docs/tasks/manage-kubernetes-objects/declarative-config/` | 声明式配置（**"声明式"的语义来源**） | ✅ 2026-10-24 |
| S18 | 官方 | `kubernetes.io/zh-cn/docs/concepts/policy/resource-quotas/` | 配额与限流 | ✅ 2026-10-24 |
| S19 | 云厂商 | `help.aliyun.com/zh/ack/` · `help.aliyun.com/zh/ack/user-guide/` | ACK 上的托管服务形态与成本构成 | ✅ 2026-10-24 |
| S20 | 云厂商 | `help.aliyun.com/zh/` + 阿里云开发者社区 | 阿里云托管数据库的规格与计费口径 | ✅ 2026-10-24 |
| S21 | 官方 | `www.cncf.io/training/certification/cka/` | CKA 考纲中的存储与排障部分（**验证自测水平**） | ✅ 2026-10-24 |
| S22 | 官方 | `kubernetes.io/zh-cn/docs/concepts/services-networking/service/` | Headless Service（`clusterIP: None`）与稳定 DNS | ✅ 2026-10-24 |

> **本篇的四个主流中间件 Operator（Prometheus Operator / Strimzi / ECK / Redis Operator / CloudNativePG）是公开开源项目**，本篇只讲**它们各自解决什么问题**，**不引用它们的具体版本号、CRD 字段名或未在索引中验证的 URL**。需要落地时，请查阅各项目官方文档并核对兼容矩阵。
> **完整来源矩阵（含全部 14 个组件）见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。**

---

## 8. 自测三问

1. **能说清输入输出吗？**
   - **输入**：一个中间件（Redis / Kafka / ES / PostgreSQL）+ 一组约束（数据量、可用性要求、RPO/RTO、团队规模与值班能力）。
   - **输出**：一个**选型结论**（裸 StatefulSet / Operator 化 / Helm / 云托管）+ 落地方案（存储、备份、监控、升级路径、迁移步骤）+ **明确的"不该上 K8s"的判断**（如果适用）。
   - **中间**：先过 2.3 的三条硬约束（存储访问模式 / 网络盘 / 备份目标位置）→ 再用 4.1 的运维成本模型算期望收益与期望成本 → 再用 3.5 的"团队能不能半夜救回来"做最终裁决 → 落地按 2.5 的五阶段迁移。
   - **再进一步**：输入"我们只有 8 个人，想把 MySQL 搬上 K8s"，输出是**"不建议自建，用云托管"** + 理由 + 成本对比的**算法**（而不是一个数字）。

2. **能故意改坏再倒推吗？** —— 六个可执行实验（**在 kind 集群 `day2` 上做，**不要在有真实数据的实例上做**）：

   | # | 改什么 | 会看到什么 | 倒推出的机制 |
   |---|---|---|---|
   | ① | 把 Operator 部署的 `replicas` 从 3 改成 5 | 新 Pod 起来了但**长时间不就绪**（在等数据同步） | Operator 扩容要**编排数据搬迁**，数据没追平就不算就绪——裸 StatefulSet 不会有这个等待 |
   | ② | 把 PVC 的 `storageClassName` 改成一个只支持 RWO 的类，但声明 `ReadWriteMany` | **PVC 一直 `Pending`**，Events 里报存储类不支持该访问模式 | 访问模式必须与存储类能力匹配 |
   | ③ | 把 StorageClass 换成本地盘，然后删掉 Pod 换个节点重建 | **数据不见了** | 本地盘不随 Pod 漂移；生产有状态中间件必须用网络存储 |
   | ④ | 删掉 Operator 的 `metadata.labels.release` 匹配项（或改 `serviceMonitorSelector`） | ServiceMonitor 存在但 **Prometheus 一个指标都没采到**，且**不报错** | 选择器不匹配 = 静默失效——这是 Operator 生态里最常见的"以为装上了" |
   | ⑤ | 只做备份、**不做恢复**，然后模拟"需要恢复" | 才发现备份文件是空的 / 恢复步骤跑不通 | **备份不做演练等于没备份**——恢复演练是必选项 |
   | ⑥ | 把中间件用 `Deployment` 而不是 `StatefulSet` 部署 | Pod 名字随机、多个 Pod 抢同一份数据或端口 | 有状态中间件必须用 StatefulSet + Headless Service + `volumeClaimTemplates` |

   > **六个实验里有四个是静默失败**（③④⑤⑥）：不报错、不告警，只是"数据不对"或"指标没有"。**数据层的静默失败代价远高于应用层**——应用层可以回滚，数据层不能。

3. **能画上下游吗？** —— 画出**一个中间件从 CR 到数据落盘再到可被读取**的完整链路，并标出每个接缝的失败表现：

```
用户写 CR（声明：3 主 3 从、备份策略、存储规格）
   │
   ▼
CRD（apiserver 认识新 kind）
   │  失败：apiVersion/kind 写错 → no matches for kind
   ▼
Operator Controller（Deployment，Leader Election 选主）
   │  失败：与 K8s 版本不兼容 → Operator 起不来
   │         → CR 成为孤儿资源，无人 reconcile（静默！）
   │  失败：无 RBAC 权限 → reconcile 失败，status 不更新
   ▼
reconcile：比对 CR.spec（期望态） vs 实际 StatefulSet/Pod/PVC
   │  失败：CRD 版本迁移（v1beta1→v1）→ 旧 CR 不被识别 → 孤儿资源
   ▼
生成/维护底层资源：
   ├─ StatefulSet（有序命名 xxx-0/xxx-1/xxx-2）
   │    失败：StorageClass 不支持访问模式 → PVC Pending → Pod 起不来
   ├─ Headless Service（clusterIP: None，稳定 DNS）
   ├─ PVC（volumeClaimTemplates，每 Pod 一个）
   │    失败：本地盘 → Pod 漂移后数据丢失
   └─ ServiceMonitor（内置监控指标）
        失败：release label 不匹配 selector → 无指标且不报错
   │
   ▼
中间件进程内：数据落盘 → 复制/选主 → 对外提供
   │  失败：副本因子 / min.insync.replicas 没配 → 脑裂或丢消息
   ▼
备份（Operator 机制 / CronJob / 存储快照，三选一或组合）
   │  失败：只备份不恢复演练 → 恢复时才发现是坏的
   ▼
恢复演练（恢复到临时实例 → 校验 → 记录真实 RTO）
   │
   ▼
Operator 升级（兼容性矩阵 → 非生产先升 → 生产钉版本）
   │  失败：CRD 版本迁移 / Operator 与中间件不兼容 → 数据出问题
   ▼
（若迁到 Operator）存量资源下线 → 观察期只读保留 → 到期清理
```

> **这张图和 [`../07-整合项目/L1-巨石应用云原生化.md`](../07-整合项目/L1-巨石应用云原生化.md) 的阶段 8 是同一件事的两个视角**：L1 从"改造路径"看（阶段 8 在第几位、依赖什么），本篇从"**这个组件本身要运维哪些东西**"看。**能把两张图对上，才算真懂中间件上 K8s 这件事。**

---

**关联篇目**：[`L1-Operator模式与CRD.md`](L1-Operator模式与CRD.md)（**上一级**：Operator 模式原理与 CRD 写法——本篇是它的"**该不该用、用哪个**"的落地决策篇） · [`../07-整合项目/L1-巨石应用云原生化.md`](../07-整合项目/L1-巨石应用云原生化.md)（阶段 8 的位置） · [`../07-整合项目/L2-GitOps与可观测闭环.md`](../07-整合项目/L2-GitOps与可观测闭环.md)（中间件也应在 GitOps 里声明与观测） · [`../第3周/第16天.md`](../第3周/第16天.md)（Operator 实操入门） · 回到 [`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
