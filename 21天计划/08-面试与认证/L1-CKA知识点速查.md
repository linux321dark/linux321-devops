# L1 · CKA 知识点速查（从"会查文档"到"限时能做对"）

> **本篇定位**：CKA 的**操作手册 + 排障地图**。不是知识点科普，是"考场上手指往哪敲"的对照表。
> **前置**：[`../02-K8s核心/L2-K8s架构与对象模型.md`](../02-K8s核心/L2-K8s架构与对象模型.md)（对象模型与三层协调）· [`../01-基础篇/L0-前置基础-Linux-Shell-YAML与网络.md`](../01-基础篇/L0-前置基础-Linux-Shell-YAML与网络.md)（Shell 与 YAML）。
> **学完能做什么**：① 30 秒内判断一个故障属于 Pod 层、节点层还是控制面层；② 限时条件下用 `kubectl` 完成"读状态 → 定位 → 改 → 验证"闭环；③ 说清 etcd 备份**该备份什么、不该备份什么**。
> **在 21 天里**：D18（`../第3周/第18天.md`）的主教材；D19 模拟卷的**命令底座**。
> **环境/版本基准**：本地 K8s **v1.37.x**，kind 集群名 `day2`，节点 `day2-control-plane`，镜像仓库前缀 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`。**本文所有命令为书面方案，未在本地执行**（写作规范第四节：禁止新建环境、禁止拉取镜像）。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
CKA 知识点速查
│
├─ 1. 概念
│   ├─ 1.1 CKA 是什么（认证的是操作能力，不是知识记忆）
│   ├─ 1.2 考什么：知识域划分（以官方当前政策为准）
│   ├─ 1.3 考纲里"排障"为什么是绝对大头
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 kubectl 的四维参数模型（资源/选择器/输出/作用域）
│   ├─ 2.2 输出格式的取舍（wide/json/yaml/jsonpath/custom-columns）
│   ├─ 2.3 --dry-run 的两种语义（client vs server）
│   ├─ 2.4 声明式 vs 命令式：为什么考试偏好声明式
│   ├─ 2.5 etcd 的位置、备份与恢复机制
│   ├─ 2.6 kubelet 与 apiserver 的排障入口
│   ├─ 2.7 证书体系与 CSR 签发流程
│   ├─ 2.8 网络链路：Service 转发 / DNS / NetworkPolicy / CNI
│   └─ 2.9 关键参数与默认值表
│
├─ 3. 动手（只写步骤，不在写作环境执行）
│   ├─ 3.1 多节点 kind 实验环境
│   ├─ 3.2 读状态四件套演练
│   ├─ 3.3 etcd 备份 → 校验 → 恢复演练
│   ├─ 3.4 CSR 签发演练
│   └─ 3.5 网络故障演练
│
├─ 4. 逐字段表
│   ├─ 4.1 kubectl 速查表（命令 | 用途 | 典型场景 | 写错的后果）
│   ├─ 4.2 排障症状速查表
│   ├─ 4.3 --dry-run 语义对照表
│   └─ 4.4 输出格式对照表
│
├─ 5. 横向对比
│   ├─ 5.1 get vs describe vs logs
│   ├─ 5.2 --selector vs --field-selector
│   ├─ 5.3 apply vs create vs replace vs edit
│   ├─ 5.4 delete vs drain
│   └─ 5.5 命令式 vs 声明式
│
├─ 6. 踩坑与排障（症状 → 状态 → 原因 → 修 → 防）
│   ├─ 6.1 考题常见误答
│   ├─ 6.2 kubectl 语法细节错
│   ├─ 6.3 etcd 备份与恢复命令易错
│   ├─ 6.4 证书过期排查路径
│   └─ 6.5 Node 问题与 Pod 问题分不清
│
├─ 7. 参考来源
├─ 8. 自测三问
└─ 附：待验证清单
```

---

## 1. 概念

### 1.1 CKA 是什么

> **CKA（Certified Kubernetes Administrator，Kubernetes 管理员认证）考的是"在限时环境里，用 kubectl 把一个具体的运维任务做完"的动手能力。**

这句话必须拆成三个可检验的部分，否则容易理解偏：

| 部分 | 含义 | 对练习方式的直接影响 |
|---|---|---|
| **限时** | 有总时间预算，超时即失败 | **必须练"在时间压力下选命令"**，而不是"把命令背熟" |
| **动手** | 操作题，不是选择/简答 | **必须练完整命令链**，练到不用想 |
| **具体运维任务** | 场景是真实的运维动作（部署、排障、备份、维护） | **必须练排障路径**，不是练概念定义 |

**推论（这是本篇存在的理由）**：
- 背概念几乎不加分 —— 概念题在 CKA 里的比重远低于操作题；
- 练"我问它答"的题库收益有限 —— **练"给场景找第一条命令"**才是有效的；
- 排障之所以占大头，是因为它同时考**读状态**（`get`/`describe`）、**判原因**（知识域）和**改状态**（`apply`/`edit`/`scale`）三段能力，一次题能覆盖多个考点。

### 1.2 考什么：知识域划分

**⚠️ 重要：本节的域名称与权重以官方认证页 `www.cncf.io/training/certification/cka/` 当前表述为准 [S1]。** 官方会调整知识域与配比，本篇不复述具体题量、总时长与及格线 —— 这类数值**以官方当前政策为准**，任何第三方页面（包括中文社区）的数字都可能滞后。

> 官方页面已实测可访问（见第 7 节 S1）。**考前请自己打开这一页核对一次**：这是唯一权威的域清单来源。

按通用知识域（**下面这份是"通用表述 + 经验判断的映射"，不是官方原文**）整理成考试与运维工作的对照：

| # | 知识域（通用表述） | 这个域在考什么 | 在运维日常对应什么 |
|---|---|---|---|
| 1 | **集群架构与安装** | 控制面组件职责、kubeadm 部署流程、etcd 与 CA 体系、版本升级路径 | 搭集群、升级控制面、接新节点 |
| 2 | **工作负载与调度** | Deployment/StatefulSet/DaemonSet/Job/CronJob、标签与选择器、requests/limits、QoS、亲和/反亲和、污点容忍 | 上线服务、扩缩容、腾资源 |
| 3 | **服务与网络** | Service 四类型、Ingress、NetworkPolicy、CoreDNS、DNS 链路、CNI | 服务暴露、路由、隔离 |
| 4 | **存储** | PV/PVC/StorageClass、绑定与回收、访问模式、卷与挂载 | 持久化、扩容、数据不丢 |
| 5 | **配置与密钥** | ConfigMap、Secret、挂载方式、更新生效方式 | 配置外置、密钥纳管 |
| 6 | **集群维护** | 节点管理（cordon/drain/label/taint）、etcd 备份恢复、升级、证书轮转、垃圾回收、备份策略 | 变更窗口、节点维护、灾难恢复 |
| 7 | **认证与授权** | RBAC（Role/ClusterRole/绑定）、ServiceAccount、`kubectl auth can-i`、准入控制基础 | 权限治理、最小权限 |
| 8 | **监控与日志** | metrics-server、指标获取、日志采集与检索基础 | 容量判断、故障取证 |
| 9 | **排障** | Pod 各类状态、日志与事件、节点与控制面故障、网络故障定位 | **值班日常** |
| 10 | **扩展与自定义** | CRD、CR 基本操作、Operator 概念、准入控制 | 接第三方组件、自研资源 |

> **经验判断（非官方数据）**：从社区备考反馈看，**排障类题目数量最多、且常以"给定现象 → 定位并修复"的形式出现**；其次是存储与配置、工作负载与调度、集群维护（含 etcd 备份恢复）。**这个排序不是官方公布的配比，写作"经验判断"而非"官方规定"。** 练题时按这个权重分配时间是合理的，但不能当考纲用。

### 1.3 考纲里"排障"为什么是绝对大头

三个结构性原因：

1. **一道排障题覆盖多个域**。一个"Pod 起不来"的题目，要用到工作负载、调度、存储、网络、镜像五个域的知识。**出题性价比最高。**
2. **它考的是路径而不是终点**。给定"Deployment 3 副本只有 1 个 Ready"，有二十条路可以走通（改 selector、改镜像、改 probe、加资源……）。**评分看的是"你有没有定位到真正的根因并修了它"**，不是"最终 Pod 是不是 Ready 了"。
3. **这是运维岗位真实能力的直接映射**。面试问"线上 Pod CrashLoop 你怎么查"，和考题几乎是同一件事。

> **由此推出本篇的编排逻辑**：第 4.2 节的「排障症状速查表」比第 4.1 节的命令表更重要。命令表是**字典**，症状表是**索引**。

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Certification | 认证 | CNCF 的 CKA / CKAD / CKS |
| Exam / Certification exam | 考试 | 限时、远程、实操 |
| Domain / Knowledge domain | 知识域 | 考纲的主题分类 |
| Control plane | 控制面 | apiserver / etcd / scheduler / controller-manager |
| Node | 节点 | 一台机器，承载 kubelet 与容器运行时 |
| Static Pod | 静态 Pod | 由 kubelet 直接管理、不由 apiserver 创建的 Pod（控制面组件本身） |
| kubelet | 节点代理 | watch apiserver → 让 Pod 真的跑起来 |
| kube-proxy | 节点代理 | 维护 Service 的转发规则 |
| CNI | 容器网络接口 | Pod 网络插件（Calico / Cilium / Flannel / kindnet） |
| etcdctl | etcd 命令行 | 备份、查状态、成员管理 |
| Snapshot | 快照 | etcd 的一致性备份 |
| CSR | 证书签名请求 | `certificates.k8s.io/v1` 的资源 |
| CA | 证书颁发机构 | 集群信任根 |
| Kubelet TLS bootstrap | kubelet 证书自举 | kubelet 首次启动自动申请证书 |
| PDB | PodDisruptionBudget | 限制自愿中断时最少可用副本 |
| Draining | 驱逐 | 把节点上可迁移的 Pod 迁走 |
| Cordon |  cordon | 把节点标记为不可调度 |
| Readiness / Liveness | 就绪 / 存活 | 两个探针，语义完全不同 |
| Endpoints / EndpointSlice | 端点 / 端点切片 | Service 后端 Pod 列表 |

---

## 2. 机制

### 2.1 kubectl 的四维参数模型

**所有 `kubectl` 命令都是四个维度自由组合出来的**。记这四维，命令就不需要背了：

```
kubectl [动作] [资源类型] [选择器] [输出/作用域]
   │        │          │          │
   │        │          │          └── -o / -n / --context / -l / --field-selector
   │        │          └── get / describe / logs / apply / create / edit / delete / scale / exec ...
   │        └── pods / deploy / svc / pv / pvc / crd / csr / node / ns ...
   └── 可选子命令：config / cluster-info / auth / explain / api-resources / top / wait / rollout / cp / debug
```

**组合示例（同一批 Pod，三种问法）**：

| 我想知道 | 命令 |
|---|---|
| 都在吗 | `kubectl get pods -n <ns>` |
| 只有我的 | `kubectl get pods -n <ns> -l app=day2-go` |
| 就这一个 | `kubectl get pod <name> -n <ns> --field-selector metadata.name=<name>` |
| 全部细节 | `kubectl get pods -n <ns> -o yaml` |

> **考场上的高频事故**：加了 `-l app=xxx` 后**输出为空**，就以为"Pod 没了"。实际是选择器没匹配上（标签打在了 Deployment 的 `spec.template.metadata.labels` 而不是 Pod 上，或 `metadata.labels` 与 `spec.selector` 不是同一套）。
> **对策**：选择器过滤出空结果时，**先去掉 `-l` 看全量**，再 `kubectl get pods --show-labels` 核对标签。这是一步操作、零成本。

### 2.2 输出格式的取舍

| 格式 | 什么时候用 | 优点 | 代价 |
|---|---|---|---|
| 默认表格 | 快速扫状态 | 快 | 看不到细节，看不到 `nodeName`（除非加 `-o wide`） |
| `-o wide` | 需要看 Pod 落在哪个节点、IP、端口 | 表格加列 | 仍然看不到完整字段 |
| `-o yaml` | 排查"到底配置成什么样了" | **完整、无损** | 长，需要 grep |
| `-o json` | 需要程序化处理 | 结构化 | 同 yaml，更长 |
| `-o jsonpath` | **只取一两个字段** | 极快、可直接喂脚本 | 语法反直觉，写错报错信息不友好 |
| `-o name` | 脚本里要资源标识 | 输出 `pod/foo` | 无信息量 |
| `-o custom-columns` | 自定义表格列 | 可读性好 | 列名写错就空列 |

**jsonpath 速记**：

```
{.items[0].metadata.name}          第 0 个 Pod 的名字
{.items[*].metadata.name}           所有 Pod 的名字（返回列表）
{.items[*].spec.nodeName}           所有 Pod 落在哪个节点
{.metadata.name}                    当前对象的名字
{..image}                           递归找所有 image 字段（最常用）
```

```bash
# 一次性拿到 Pod → 节点 的映射表（排障高频）
kubectl get pods -n default -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,RESTARTS:.status.containerStatuses[0].restartCount'
```

> **面试能这么答**："`-o yaml` 是排障的默认输出，因为它同时包含 `spec`（期望态）和 `status`（实际态），**一眼能看出期望和实际差在哪**；`-o jsonpath` 是取数用的，不是看状态用的。"

### 2.3 `--dry-run` 的两种语义

| 取值 | 行为 | 会不会连 apiserver | 典型用途 |
|---|---|---|---|
| `--dry-run=client` | **只做本地客户端校验**（结构、字段名、类型），不提交 | **不连** | 写 yaml 时的语法自检 |
| `--dry-run=server` | 请求发给 apiserver，**走完整认证/授权/准入**，但不落库 | 连 | 检查会不会被准入控制拒绝 |
| `--dry-run=none`（默认行为） | 真正写进去 | 连 | 正常操作 |

**两种最常考的用法**：

```bash
# ① 写 yaml 时的必备习惯：本地语法自检，不碰集群
kubectl apply -f deploy.yaml --dry-run=client

# ② 生成一份 YAML 模板（这是 --dry-run 最常见的实际用途）
kubectl create deployment day2-go --image=registry.cn-hangzhou.aliyuncs.com/linux321-devops/day2-go:latest --dry-run=client -o yaml > deploy.yaml
```

> **易错点**：`--dry-run=client` **不会**告诉你"集群里已有同名资源会冲突"、"这个 ServiceAccount 不存在"、"这个 quota 超了"。这些要 `--dry-run=server` 才验得出。
> **版本敏感**：旧式布尔写法 `--dry-run=true` 已弃用，新式取值是 `client|server|none`（`⚠️ 待验证`：本地 v1.37.1 是否仍兼容布尔写法未实测）。

### 2.4 声明式 vs 命令式：为什么考试偏好声明式

| 维度 | 命令式（`kubectl set image` / `kubectl scale`） | 声明式（`kubectl apply -f`） |
|---|---|---|
| 表达的是 | "把某一项改成 X" | "这个对象的期望态是这样" |
| 可重复执行 | 不幂等的部分会叠加（重复 scale） | **幂等**，重复 apply 结果一致 |
| 可审计 | 命令散在终端历史里 | **yaml 在 Git 里，有 diff、有 review** |
| 可回滚 | 需要自己记得改回去 | `kubectl rollout undo` / Git revert |
| 考场评分 | 部分题目明确要求 `edit`/`set`，部分要求 `apply` | **看题目问的是"改一个字段"还是"交付一份配置"** |

**判定规则（很实用）**：
- 题目说"**修改/调整某个字段**" → `kubectl edit`（或 `kubectl set ...`）——**它只改一个字段，不会把别的字段覆盖掉**；
- 题目说"**创建/交付一份资源**" → `kubectl apply -f`；
- 题目说"**把副本数改成 N**" → `kubectl scale`；
- 题目说"**回滚**" → `kubectl rollout undo deployment/<name>`。

> **为什么 `edit` 在"改一个字段"的场景优于 `apply`**：`apply` 是**字段所有权式合并**——你提供的 yaml 里没写的字段，如果之前是 `kubectl` 命令式改的，**会被保留或被抹掉**，取决于 `last-applied-configuration` 注解里存的历史。**考场里最容易丢分的情况就是：用 `apply` 提交一份只含 `image` 的 yaml，结果把别人设的 resources 或 labels 冲掉了。** 这就是第 5.3 节要展开的。

### 2.5 etcd 的位置、备份与恢复机制

**etcd 是什么**：集群的唯一真相库。**所有的期望态和实际态都在 etcd 里**。etcd 挂了 → apiserver 读写失败 → 集群冻结（但已经运行的 Pod 还活着，因为 kubelet 本地还有一份）。

```
kubectl
   │  所有写操作
   ▼
kube-apiserver ──读/写──► etcd（单点/集群，Raft 复制）
   ▲                            │
   │ watch 事件流               │ 落盘：/var/lib/etcd（成员数据目录）
   │
kubelet / scheduler / controller-manager
```

**备份的机制与正确姿势**：

| 环节 | 做什么 | 命令骨架 |
|---|---|---|
| ① 备份 | 对 etcd 做**一致性快照** | `etcdctl snapshot save <file> --endpoints=... --cacert=... --cert=... --key=...` |
| ② **校验** | 确认快照完整可用 | `etcdctl snapshot status <file> --write-out=table` |
| ③ 异地存放 | 拷贝到集群外的机器/对象存储 | `cp` / `oss cp` |
| ④ 恢复 | **停 etcd → 清空数据目录 → restore 到新目录 → 改静态 Pod 的 `--data-dir` → 重启** | `etcdctl snapshot restore <file> --data-dir=... --name=... --initial-cluster=... --initial-advertise-peer-urls=...` |

**备份什么 / 不备份什么（考试重点，也是生产重点）**：

| 该备份 | 不该备份（以及为什么） |
|---|---|
| **etcd 数据目录的快照**（`snapshot save` 产物） | ❌ 直接 tar 打包 etcd 的 WAL 数据目录做热备 —— **不是一致性快照**，恢复出来会坏；正确做法是 `snapshot save` |
| **etcd 证书与配置**（`/etc/kubernetes/pki/etcd/`）—— 但这是**配套材料**，不是备份本体 | ❌ 备份 `/var/lib/kubelet` —— 那是**节点状态**（Pod 清单、卷挂载），与集群配置真相无关，还巨大 |
| 快照的**校验输出**一起留档 | ❌ 只备份不校验 —— **没校验过的快照等于没备份** |
| 快照**离开集群机器**存放 | ❌ 快照和 etcd 放在同一台机器 —— 机器坏了快照一起没 |

> **一句话答题模板**："**备份的是 etcd 的一致性快照，不是数据目录；备份完必须 `snapshot status` 校验，并且要存到集群之外。**"

### 2.6 kubelet 与 apiserver 的排障入口

| 组件 | 排障入口 | 日志路径（kubeadm 部署） | 静态 Pod 目录 |
|---|---|---|---|
| **kube-apiserver** | `kubectl get --raw='/readyz?verbose'` 看健康检查明细；容器化部署看控制面 Pod 日志 | 控制面节点上 `/var/log/containers/` 下对应容器日志 | `/etc/kubernetes/manifests/` |
| **kube-controller-manager** | 看控制器是否在 reconcile（Deployment 的 RS 有没有建出来） | 同上 | `/etc/kubernetes/manifests/` |
| **kube-scheduler** | Pod 一直 Pending 且 Events 无 `FailedScheduling` → 调度器没工作 | 同上 | `/etc/kubernetes/manifests/` |
| **kubelet** | 节点侧服务日志 + 节点上容器日志 | `/var/log/kubelet.log`；容器日志 `/var/log/containers/`、`/var/log/pods/` | —（kubelet 不是静态 Pod） |
| **etcd** | `etcdctl endpoint health` / `endpoint status --cluster` / `alarm list` | `/var/log/etcd/` 或容器日志 | `/etc/kubernetes/manifests/` |

**控制面组件是"静态 Pod"这件事的实战意义**：
- 它们的 **YAML 放在 `/etc/kubernetes/manifests/`**，kubelet 直接读这个目录并保证它们一直跑；
- **改这个目录里的 yaml，改完 kubelet 会自动重建组件**（apiserver 改了会重启自己）；
- **恢复 etcd 时要改的就是这个目录里 etcd 静态 Pod 的 `--data-dir`**；
- 所以在控制面节点上 `ls /etc/kubernetes/manifests/` 是排查控制面故障的第一动作。

> **kind 的差异**（重要）：kind 里控制面组件跑在**容器内**，静态 Pod 目录在容器内部路径，宿主机上直接 `ls` 不到。**因此 etcd 备份/恢复这类题，必须在 kubeadm 部署的集群上练，或者在 kind 里通过 `docker exec` 进容器操作 —— 后者与考场的操作方式不同。** 详见第 3.1 节。

### 2.7 证书体系与 CSR 签发流程

**集群里谁给谁签证书**：

| 证书持有者 | 用途 | 签发者 | 到期后表现 |
|---|---|---|---|
| `kube-apiserver` | 对外提供 HTTPS | 集群 CA | 客户端全部连不上（`x509` 报错） |
| `kubelet` | 向 apiserver 证明身份 + 提供节点 HTTPS | 集群 CA（`kubernetes.io/kubelet-client`） | 节点上 Pod 状态不再更新，Node NotReady |
| `kube-controller-manager` | 给 kubelet 签证书（TLS bootstrap） | 集群 CA | 新节点加不进来、证书轮转失败 |
| `kube-proxy` | 访问 apiserver | 集群 CA | Service 转发异常 |
| `kubectl` 客户端 | 访问 apiserver | 集群 CA / 或你自己的 CA | `Unable to connect to the server: x509` |
| CSR（用户申请） | 外部系统要访问 apiserver | `kubernetes.io/kube-apiserver-client-kubelet` 等 signer | 走审批流程 |

**CSR 流程（考试高频，五步）**：

```
① 用户提交 CSR（kubectl create -f csr.yaml）
        │  spec.request = base64(PEM 编码的 CSR)
        ▼
② 查状态：kubectl get csr      → Pending
        │  kubectl describe csr <name>  看 Conditions 与请求的 usages
        ▼
③ 审批：kubectl certificate approve <name>
        │  （或由 controller-manager 自动审批特定 signer 的请求）
        ▼
④ 拿到签发结果：kubectl get csr <name> -o jsonpath='{.status.certificate}' | base64 -d
        ▼
⑤ 分发：把证书与私钥配好，写进 kubeconfig 或挂载进 Pod
```

> **考题常问的两个点**：
> ① `kubectl get csr` 显示 **Approved 但 status.certificate 为空** → 说明审批了但**还没签发**，检查 controller-manager 的签名器是否配置正确；
> ② `usages` 写错（比如想申请 client 证书却写了 `server auth`）→ **签名器会拒绝签发**，`status` 里能看到条件说明。

**kubelet 证书轮转**：
- kubelet 的**客户端证书**由 controller-manager 的 `csrsigning` 控制器自动签发并自动批准；
- kubelet 的**服务端证书**（`kubelet-serving`）同样由 controller-manager 签发，kubelet 自己申请；
- 前提是 kubelet 启动参数带 `--rotate-certificates=true`（kubeadm 默认开）且 controller-manager 配了 `--cluster-signing-cert-file` / `--cluster-signing-key-file`；
- **症状**：证书过期后 Node 变 NotReady，**Pod 还是 Running**（容器没死，是 kubelet 跟 apiserver 断了联系，状态不再上报）—— 这是第 6.5 节"Node 问题 vs Pod 问题分不清"的最典型案例。

### 2.8 网络链路：Service 转发 / DNS / NetworkPolicy / CNI

#### (1) Service 的转发路径

```
客户端 Pod
   │  访问 ClusterIP:port
   ▼
kube-proxy 在节点上维护转发规则（iptables / IPVS / eBPF）
   │  DNAT 到某个 PodIP:targetPort
   ▼
端点 Pod
```

**排查手段（考试与生产同款）**：

```bash
# ① 端点对不对（最常见根因：Service selector 没匹配上任何 Pod）
kubectl get endpoints <svc> -n <ns>
kubectl get endpointslice -n <ns> -l kubernetes.io/service-name=<svc>

# ② 转发规则在不在（kube-proxy 侧）
iptables -t nat -L KUBE-SERVICES -n | grep <svc-clusterip>
iptables-save -t nat | grep KUBE-SVC
# IPVS 模式则是：ipvsadm -Ln

# ③ kube-proxy 用的是哪种模式
kubectl -n kube-system get cm kube-proxy -o yaml
```

**判断表**：

| 现象 | 结论 |
|---|---|
| `kubectl get endpoints` **为空** | **Service 的 selector 没匹配到 Pod** —— 这时问题不在网络层，先去核对标签 |
| endpoints 有 IP，但访问不通 | 网络层问题：看转发规则、看 CNI、看目标 Pod 监听端口 |
| 转发规则里**没有**对应 ClusterIP | kube-proxy 没工作或规则被清掉 —— 看 kube-proxy 日志 |
| `endpoints` 里 IP 不是当前 Pod 的 IP | Pod 变了但 endpoints 没更新 —— kube-proxy 或 apiserver 通信问题 |

> **易错点**：`kubectl get endpoints` 在新版本会提示 `Endpoints` 资源已弃用（推荐 EndpointSlice），但**命令仍可用**，考试照用。

#### (2) DNS 解析链路

```
应用 Pod
  │ 读 /etc/resolv.conf 里的 nameserver（默认指向 kube-dns 的 ClusterIP:53）
  ▼
kube-dns Service（kube-system 命名空间）
  ▼
CoreDNS Deployment（kube-system）→ 读自己的 Corefile
  ▼
上游 DNS（/etc/resolv.conf 里的真实 DNS 服务器）
```

**故障时的检查顺序**：

```bash
# ① CoreDNS 自己活着吗
kubectl -n kube-system get deploy coredns
kubectl -n kube-system get pods -l k8s-app=kube-dns
kubectl -n kube-system logs deploy/coredns --tail=50

# ② kube-dns Service 有端点吗（没有端点 = 解析必挂）
kubectl -n kube-system get svc kube-dns
kubectl -n kube-system get endpoints kube-dns

# ③ 从 Pod 里实测解析（用临时 Pod，不进业务 Pod）
kubectl run dnscheck --rm -it --restart=Never --image=busybox -- nslookup kubernetes.default.svc.cluster.local
```

> **高频错答**：问"Pod 里 nslookup 域名失败"，直接答"DNS 服务器挂了"。**正确答案是先分清是 CoreDNS 挂了，还是 Pod 的 `/etc/resolv.conf` 指向错了（比如自定义了 `dnsConfig`），还是这个域名本来就不存在（命名空间写错）。**

#### (3) NetworkPolicy 与 CNI 的依赖关系

**这是本域最容易踩、也最容易得分的一条**：

> **NetworkPolicy 只是"声明"，真正执行的是 CNI 插件。** CNI 不支持策略 → 策略对象创建成功、`kubectl get networkpolicy` 正常显示，**但流量一点没被拦**。

| CNI | NetworkPolicy 支持 |
|---|---|
| Calico | 支持 |
| Cilium | 支持 |
| Flannel | **原生不支持** |
| **kind 默认的 kindnet** | **不支持** |

> **因此本节的动手实验（3.5）必须先装 Calico**，否则你会得到"策略写了没生效"的假象 —— 这个坑在考场上会直接吃掉一道题的分数。

**策略的语义要点**：

- `podSelector` 选中**被管控的 Pod**（流量入口方向）；
- `ingress` / `egress` 分别管入向 / 出向；
- `policyTypes` 不写时，默认规则会**同时作用于两个方向**（这是高频语义坑）；
- 一旦某个 Pod 被任意一条 Ingress 策略选中，**该方向上所有未显式放行的流量都被拒绝**（default-deny 效果）。

#### (4) CNI 相关的其他故障

| 症状 | 检查 |
|---|---|
| Pod 卡 `ContainerCreating`，事件提到网络 | CNI 插件 Pod 本身是否正常；`kubectl -n kube-system get pods -l k8s-app=calico-node` |
| Pod 之间不通（同节点 / 跨节点都测） | CNI 的 IP 分配与路由；跨节点不通 → 查 BGP/路由 |
| 刚创建 Pod 的 DNS 解析慢 | CoreDNS 尚未 Ready，Pod 卡在等待网络就绪 |

### 2.9 关键参数与默认值表

| 参数 | 默认 | 含义 | 实务要点 |
|---|---|---|---|
| `Deployment.progressDeadlineSeconds` | **600** | 多久判"这次发布失败" | **只表示等够了，不含任何诊断信息** |
| `Deployment.maxSurge` | 25% | 更新时最多多出几个 Pod | 1 副本时向上取整 = 1，会短暂跑 2 个 |
| `Deployment.maxUnavailable` | 25% | 更新时最多几个不可用 | 1 副本时向下取整 = 0，必须先起新再删老 |
| `Service.nodePort` 范围 | 30000-32767 | NodePort 占用区间 | 端口固定易冲突 |
| `HPA.syncPeriod` | 15s | HPA 计算周期 | 加上采集周期，扩容反应 1-2 分钟 |
| `HPA` 缩容窗口 | **300s** | 缩容前观察时长 | 扩 0s / 缩 300s，**故意不对称** |
| `HPA` 分母 | **`requests.cpu`** | 利用率计算基准 | 两个都不写 → `<unknown>/50%` |
| `HPA` 公式 | `ceil(当前副本数 × 当前利用率 / 目标利用率)` | 期望副本数 | 记住是 **ceil**，不是 round |
| `kubelet` 探针默认 | period 10s / failureThreshold 3 | | 连续失败 3 次才判失败 |
| `terminationGracePeriodSeconds` | **30s** | 收到删除信号后等多久强杀 | 配合 `preStop` 做优雅停机 |
| `PodDisruptionBudget.minAvailable` | 无默认 | 自愿中断时最少可用 | **drain 前必看** |
| `kubectl` 默认命名空间 | `default` | | **考场默认就有陷阱**，见第 6.2 节 |
| `kubectl` 默认超时 | 0（不超时） | | 挂死的 apiserver 会让你等满考试时间 |

---

## 3. 动手

> **⚠️ 本节所有命令均为书面步骤，本篇写作过程中未执行任何一条**（写作规范第四节：不新建环境、不拉取镜像、不修改用户已有资源）。你在自己机器上按步骤做即可。

### 3.1 实验环境准备：多节点 kind 集群

**为什么必须是多节点**：单节点 kind 集群里所有 Pod 挤在一台"机器"上，**调度类实验（污点、亲和性、资源不足、节点维护）全部失效**。而这几个恰好是高频考点。**必须用多节点配置。**

```yaml
# kind-multinode.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    # 本地集群名保持 day2，节点名即 day2-control-plane
  - role: worker
  - role: worker
  - role: worker
```

```bash
# 创建多节点集群
kind create cluster --name day2 --config kind-multinode.yaml

# 看到 4 个节点即成功（1 control-plane + 3 worker）
kubectl get nodes -o wide

# 验证节点角色
kubectl get nodes --show-labels | grep node-role.kubernetes.io
```

**看到什么算对**：

| 检查 | 命令 | 期望 |
|---|---|---|
| 节点齐了 | `kubectl get nodes` | 4 行，STATUS 全 `Ready` |
| 角色对 | `kubectl get nodes -o wide` | ROLES 列：`control-plane` × 1、`<none>` × 3 |
| 命名空间干净 | `kubectl get ns` | 只有系统命名空间 |

**kind 的四个必知限制**（每一条都会让某个题型失效）：

| # | 限制 | 影响哪些题 |
|---|---|---|
| 1 | **默认单节点**，要模拟多节点必须显式写多节点 config | 调度、资源不足、drain |
| 2 | **节点是容器**，没有你宿主机的 docker 凭据 | 私有镜像拉取（需 `kind load docker-image` 或在节点内配凭据） |
| 3 | **控制面组件在容器内**，`/etc/kubernetes/manifests/` 看不到 | etcd 备份恢复、静态 Pod 排查 |
| 4 | **没有云厂商组件**，Service type=LoadBalancer 不会真正有 LB | 四类 Service 的 LoadBalancer 行为 |

> **第 3 条是重点**：本篇 2.5 节的 etcd 备份恢复流程，**在 kind 上无法原样练习**。你需要一台装了 kubeadm 的虚拟机（或者一台云主机）才能完整演练。**这不是可以绕过的 —— 恢复流程是 CKA 的重点考点。**

### 3.2 读状态四件套演练

```bash
# ① 定性：整体状态
kubectl get pods -A

# ② 定位：某一个 Pod 的详情 + Events（最关键的一步）
kubectl describe pod <pod-name> -n <ns>

# ③ 取证：容器日志（含上一次崩溃）
kubectl logs <pod-name> -n <ns> --tail=100
kubectl logs <pod-name> -n <ns> --previous

# ④ 时间线：跨对象看事件顺序
kubectl get events -n <ns> --sort-by=.lastTimestamp
kubectl get events -n <ns> --field-selector involvedObject.name=<pod-name>
```

**输出格式练习（同一个对象四种看法）**：

```bash
kubectl get deploy day2-go -n default                  # 表格
kubectl get deploy day2-go -n default -o wide          # 加节点列
kubectl get deploy day2-go -n default -o yaml          # 完整（含 status）
kubectl get deploy day2-go -n default -o jsonpath='{.status.readyReplicas}'
kubectl get deploy day2-go -n default -o custom-columns='NAME:.metadata.name,READY:.status.readyReplicas,DESIRED:.spec.replicas'
```

**标签与选择器练习**：

```bash
kubectl get pods --show-labels                        # 看标签
kubectl get pods -l app=day2-go                      # 标签选择器
kubectl get pods --field-selector status.phase=Pending   # 字段选择器
kubectl get pods --field-selector spec.nodeName=day2-control-plane
```

### 3.3 etcd 备份 → 校验 → 恢复演练（需 kubeadm 环境）

```bash
# ① 备份（在控制面节点上执行）
export ETCDCTL_API=3
etcdctl snapshot save /backup/etcd-$(date +%F-%H%M).db \
  --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/healthcheck-client.crt \
  --key=/etc/kubernetes/pki/etcd/healthcheck-client.key

# ② 校验（这一步不能省）
etcdctl snapshot status /backup/etcd-$(date +%F-%H%M).db --write-out=table

# ③ 存到集群之外
#    （scp / rsync / 对象存储客户端，取决于你的环境）

# ④ 恢复（演练流程；生产上必须先在测试集群验证）
#    a. 停 etcd：kubectl -n kube-system scale deploy etcd --replicas=0  （或直接停静态 Pod）
#    b. 备份旧数据目录并清空：/var/lib/etcd
#    c. 恢复到新目录：
etcdctl snapshot restore /backup/etcd-xxx.db \
  --data-dir=/var/lib/etcd-restored \
  --name=<etcd-member-name> \
  --initial-cluster="<name>=https://<peer-url>:2380" \
  --initial-advertise-peer-urls="https://<peer-url>:2380"
#    d. 修改 /etc/kubernetes/manifests/etcd.yaml 里的 --data-dir 指向新目录
#    e. 重启 etcd 静态 Pod，确认 apiserver 恢复
```

> **⚠️ 版本/环境敏感**：`snapshot restore` 的 `--name` 与 `--initial-cluster` 必须与**原集群的集群名和成员名一致**，写错 etcd 起不来。**这一段参数在本地 kind 集群上无法原样验证**（kind 是单成员、路径不同），标 `⚠️ 待验证`，务必在 kubeadm 集群上练一遍。

### 3.4 CSR 签发演练

```bash
# ① 生成私钥与 CSR（示例：为一个组件申请 client 证书）
openssl genrsa -out app.key 2048
openssl req -new -key app.key -subj "/CN=my-service/O=system:masters" -out app.csr

# ② 提交 CSR
kubectl create -f - <<'EOF'
apiVersion: certificates.k8s.io/v1
kind: CertificateSigningRequest
metadata:
  name: my-service
spec:
  request: <把 app.csr 内容做 base64 后填这里>
  usages:
    - client auth
EOF

# ③ 查与审批
kubectl get csr
kubectl describe csr my-service
kubectl certificate approve my-service

# ④ 取回签发的证书
kubectl get csr my-service -o jsonpath='{.status.certificate}' | base64 -d > app.crt
```

**看到什么算对**：`kubectl get csr` 的 CONDITION 列从 `Pending` 变 `Approved`；`.status.certificate` 字段非空。

### 3.5 网络故障演练（先装支持策略的 CNI）

```bash
# ① 装 Calico（kind 默认的 kindnet 不支持 NetworkPolicy）
#    清单请从 Calico 官方发布渠道下载为本地文件 calico.yaml 后再 apply
kubectl apply -f calico.yaml
kubectl -n kube-system get pods -l k8s-app=calico-node    # 等到全部 Running
```

> ⚠️ **注意**：**不要在练习/考场里直接 apply 在线地址**。GitHub 系域名在国内部分网络不可直连（参考索引已实测记录此坑），真实考试环境也通常不保证外网。**正确做法：把 Calico 清单提前下载到本地**，再 `kubectl apply -f calico.yaml`。**这是"考场无外网"这条约束的日常体现。**

```bash
# ② 造两个 Pod：一个当客户端，一个当服务端
kubectl run db --image=registry.cn-hangzhou.aliyuncs.com/linux321-devops/day2-go:latest --labels app=db --restart=Never
kubectl run client --image=busybox --labels app=client --restart=Never -- sleep 3600

# ③ 写一条只允许 client → db 的策略
kubectl apply -f - <<'EOF'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-client-to-db
spec:
  podSelector:
    matchLabels:
      app: db
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: client
      ports:
        - protocol: TCP
          port: 8080
EOF

# ④ 三个方向实测
kubectl exec client -- sh -c 'nc -z -w 2 db 8080 && echo ALLOW || echo BLOCK'   # 同 ns 允许
kubectl exec client -- sh -c 'nc -z -w 2 db.default.svc.cluster.local 8080 && echo ALLOW || echo BLOCK'
```

**看到什么算对**：策略生效时，允许方向通、不允许方向被拒。**如果三个方向全通 → CNI 不支持策略（回看 2.8(3) 的表），不是策略写错了。**

---

## 4. 逐字段表

### 4.1 kubectl 速查表

| 命令 | 用途 | 典型场景 | 写错的后果 |
|---|---|---|---|
| `kubectl get <res> -n <ns>` | 列资源与状态 | 定性看整体 | 漏 `-n` → 看到的是 `default` 命名空间，**以为资源不存在** |
| `kubectl get <res> -o wide` | 加列（节点、IP、端口） | 查 Pod 落在哪台节点 | 不加 `-o wide` → 看不到 `nodeName` 列 |
| `kubectl get <res> -o yaml` | 完整对象（含 spec 与 status） | 对比期望态与实际态 | 看表格时把 `READY` 列与 `spec.replicas` 混淆 |
| `kubectl get <res> -o jsonpath='{...}'` | 精确取字段 | 脚本化取值 | 路径写错 → **空输出不报错**，误以为没数据 |
| `kubectl describe <res> <name>` | 详情 + **Events** | 排障定位卡点 | 只看 `get` 不看 Events → **看不到根因** [S5] |
| `kubectl logs <pod>` | 当前容器日志 | 看应用输出 | Pod 多容器时必须加 `-c <container>` |
| `kubectl logs <pod> --previous` | **上一次崩溃**的日志 | CrashLoopBackOff 的唯一线索 | 忘了加 → 看到的全是当前这次启动的日志，**没有崩溃现场** |
| `kubectl exec -it <pod> -- <cmd>` | 进容器 | 调试、查 DNS | 加了 `-it` 但目标 Pod 没 TTY → 报错 |
| `kubectl apply -f <file>` | 声明式创建/更新 | 交付配置 | 覆盖掉别人用命令式改的字段 |
| `kubectl create -f <file>` | 创建（已存在会报） | 一次性的新建 | 重复执行报 `AlreadyExists` |
| `kubectl edit <res> <name>` | 改单个字段 | 改副本数、镜像、标签 | 忘记保存就退出 → 改动丢失 |
| `kubectl scale <res> --replicas=N` | 改副本数 | 手动扩容 | 与 HPA 同时用 → **两边打架** |
| `kubectl delete <res> <name>` | 删资源 | 清理 | 忘加 `--cascade` → 孤儿 Pod 残留 |
| `kubectl delete <res> <name> --cascade=foreground` | 先删依赖再删本体 | 删 Deployment | 默认 `background` → 本体已删、Pod 还在 |
| `kubectl cordon <node>` | 标记不可调度 | 节点维护第一步 | 忘了 cordon 直接 drain → 有新 Pod 又调度上来 |
| `kubectl drain <node> --ignore-daemonsets` | 驱逐 Pod | 节点维护第二步 | 不加 `--ignore-daemonsets` → DaemonSet Pod 卡住 drain 不完 |
| `kubectl label node <n> k=v --overwrite` | 打标签 | 节点选择器用 | 不加 `--overwrite` → 标签已存在时报错 |
| `kubectl taint nodes <n> k=v:NoSchedule` | 加污点 | 隔离专用节点 | effect 写错（写成 `NoExecute`）→ 语义与题目不符 |
| `kubectl rollout status deploy/<n>` | 等发布完成 | 验证滚动更新 | **超时了也不告诉你原因**，必须去看 Pod |
| `kubectl rollout undo deploy/<n>` | 回滚 | 发布出问题 | 不加 `--to-revision` → 回到上一版而非指定版本 |
| `kubectl auth can-i <verb> <res> --as=<user>` | 查权限 | 排查 403 [S15] | 忘了加 `--as` → 查的是**你自己**的权限 |
| `kubectl api-resources` | 列出所有资源类型与短名 | 记不住资源名时 | 凭记忆写 kind → `no matches for kind` |
| `kubectl explain <res>.<field>` | 查字段语义 | 忘了某字段怎么写 | 凭印象写 → 字段名不存在或语义相反 |
| `kubectl get --raw='/readyz?verbose'` | apiserver 健康检查明细 | 控制面排障 | 只会用已废弃的 `componentstatuses` [S6] |
| `kubectl get events --sort-by=.lastTimestamp` | 事件时间线 | 跨对象看先后顺序 | 不排序 → 看到的是无序列表，**误判因果** |
| `kubectl get csr` | 看证书签名请求 | CSR 流程 | 不知道 `approve` 是独立子命令 |
| `kubectl certificate approve <name>` | 审批 CSR | CSR 流程 | 审批后没查 `.status.certificate` → **以为签发完成了** |
| `kubectl top pod/node` | 资源用量 | 找资源大户 | 没装 metrics-server → 报连接错误 |
| `kubectl diff -f <file>` | 对比"如果 apply 会变什么" | 变更前预演 | 直接 apply → 变更不可见、不可回退 |
| `kubectl compact` | **把 etcd 中对象的存储表示改写为更紧凑形式**（清理已终止 Pod 等），**不是"压缩 yaml 文件"** | 集群跑久了、etcd 膨胀时的收尾动作 | ⚠️ **版本敏感**：v1.37.x 是否默认可用未实测，先 `kubectl compact --help` 确认；误以为是文件压缩 → **白花时间找 gzip** |
| `kubectl wait --for=condition=ready pod/<n> --timeout=60s` | 等待就绪 | 脚本串行化 | 不用 wait → 后续步骤抢跑 |
| `kubectl debug node/<n> -it --image=busybox` | 临时容器进节点 | 排查节点网络 | 用法记错 → 命令报参数错误 |
| `kubectl cp <pod>:/path ./local` | 拷贝文件 | 取日志/配置 | 源路径写错 → 静默不产出文件 |

### 4.2 排障症状速查表

| 症状 | 第一条命令 | 看哪个字段 | 常见根因 | 写错/漏看的后果 |
|---|---|---|---|---|
| Pod `Pending` | `kubectl describe pod <p>` | Events 的 `FailedScheduling` | 资源不足 / 亲和不匹配 / 有污点无容忍 / PVC 未绑定 | 跳过 Events 直接改镜像 → **根因没解决，重建还是 Pending** |
| Pod `ImagePullBackOff` | `kubectl describe pod <p>` | Events 的 `Failed` 原因原文 | 镜像名/tag 错；私有仓库无凭据 | 反复删 Pod 重试 → **每次都重新拉，还是拉不到** |
| Pod `CrashLoopBackOff` | `kubectl logs <p> --previous` | 崩溃前的最后几行 | 应用启动即退出；配置缺失；命令写错 | 只看当前日志 → 看到的是"启动中"，**看不到崩溃原因** |
| Pod `CreateContainerConfigError` | `kubectl describe pod <p>` | Events 里的 configmap/secret 名 | 引用的 ConfigMap/Secret 不存在 | 去查应用 → 应用本身没问题，**方向错** |
| Pod `ContainerCreating` 卡住 | `kubectl describe pod <p>` | Events 里的挂载信息 | PVC 未绑定；镜像很大未拉完；挂载冲突 | 直接删 Pod → **重建后还是同一个挂载问题** |
| 访问 Service 503 | `kubectl get endpoints <svc>` | 端点列表是否为空 | **Service selector 与 Pod 标签不匹配** | 去查防火墙/网络 → **网络层完全没问题** |
| Ingress 404 | `kubectl describe ingress <i>` | 规则里的 host/path 与后端 | 后端 Service 名错；`pathType` 不匹配；后端 Service 无端点 | 去改 Ingress 注解 → 改完还是 404，**没查后端** |
| 节点 `NotReady` | `kubectl describe node <n>` | Conditions 的 `Ready` / `NetworkUnavailable` | kubelet 挂了；容器运行时挂了；磁盘满 | 去重启业务 Pod → **节点问题，重启业务毫无意义** |
| 节点 `NotReady` 但 Pod 还 Running | `kubectl -n kube-system get pods -o wide` | kubelet 证书状态 | **kubelet 证书过期**，无法上报状态 | 以为 Pod 正常 → **Pod 实际已失去管理** |
| DNS 解析失败 | `kubectl -n kube-system get pods -l k8s-app=kube-dns` | CoreDNS 是否 Running | CoreDNS 挂了；Pod 的 `dnsConfig` 指错；域名不存在 | 重启 CoreDNS → **重启解决不了域名写错** |
| apiserver 连不上 | `kubectl get --raw='/readyz?verbose'` | 健康检查明细 | 证书过期；apiserver 挂了 | 以为是网络问题 → **改网络配置毫无意义** |
| `apply` 报 `no matches for kind` | `kubectl api-resources` | 真实 kind 与 apiVersion | 版本写错/资源废弃 | 反复改 yaml → 改的一直是错的字段 |
| `apply` 报 selector 与模板标签不符 | `kubectl get deploy <d> -o yaml` | `spec.selector` vs `spec.template.metadata.labels` | 两处标签不一致 | 只改一处 → 改成第三种不一致 |
| etcd 写入超时 | `etcdctl endpoint status --cluster -w table` | leader / 版本 / 空间 | 磁盘满；成员不健康；空间配额 | 重启 apiserver → **etcd 问题，重启 apiserver 只是短暂恢复** |
| PVC 一直 `Pending` | `kubectl describe pvc <p>` | Events + `spec.accessModes` | 无匹配 PV；StorageClass 不存在；容量不足；RWO/RWX 不匹配 | 反复删 Pod → **PVC 的绑定问题与 Pod 无关** |
| HPA 一直 `<unknown>/50%` | `kubectl describe hpa <h>` | `ScalingActive` 与 Targets | **`requests.cpu` 没写** | 去调目标值 → **分母不存在，调多少都没用** |
| 认证请求 403 | `kubectl auth can-i <verb> <res> --as=<sa>` | 能否列出/操作 | RBAC 绑定缺失；ServiceAccount 没绑 Role | 去改 ClusterRole → **可能放开了本不该开的权限** |

### 4.3 `--dry-run` 语义对照表

| 场景 | 正确写法 | 会连集群吗 | 漏了什么 | **写错的后果** |
|---|---|---|---|---|
| 写 yaml 语法自检 | `kubectl apply -f x.yaml --dry-run=client` | 否 | 资源冲突、RBAC、准入 | 误以为"检查通过就一定能 apply 成功" → 真 apply 时被拒，浪费一个操作周期 |
| 生成 yaml 模板 | `kubectl create deploy <n> --image=<img> --dry-run=client -o yaml` | 否 | 同上 | 忘了 `-o yaml` → 屏幕上打的是表格，**存不成文件** |
| 验证能否被准入控制接受 | `kubectl apply -f x.yaml --dry-run=server` | 是 | **不会真的创建** | 以为 dry-run=server 就等于"建好了" → 资源根本不存在 |
| 变更前预演 | `kubectl diff -f x.yaml` | 是（只读） | diff 结果**为空也不代表一定是无变化**（字段默认值差异） | 把 diff 为空当作"无需变更" → 漏掉默认值层面的改动 |

### 4.4 输出格式对照表

| 需求 | 写法 | 注意 | **写错的后果** |
|---|---|---|---|
| 表格 + 节点列 | `-o wide` | 只有部分资源有 `wide` 扩展列 | 该资源没有 wide 列 → **输出与普通表格一样，白以为看到了节点** |
| 完整对象 | `-o yaml` | 长，配合 `grep`/`less` | 只看 yaml 的 `spec` 不看 `status` → **把期望态当实际态** |
| 取单个字段 | `-o jsonpath='{.status.readyReplicas}'` | **空输出不报错**，要自己判断 | 空输出当成"副本数为 0" → **误判服务异常** |
| 取列表 | `-o jsonpath='{.items[*].metadata.name}'` | 输出是空格分隔的字符串 | 直接当数组用 → 拿到一个字符串，脚本逻辑全错 |
| 自定义列 | `-o custom-columns='A:.a,B:.b'` | 列名写错 → **空列**（不报错） | 空列被当成"该字段为空" → **根因判断偏了** |
| 数组下标 | `.spec.containers[0].image` | 数组为空 → **报错**（越界） | 脚本直接中断 → 排障流程被打断 |
| 递归取值 | `-o jsonpath='{..image}'` | 常用，一次列出所有镜像 | 层级写错 → 输出混杂 → **看不出是哪个容器的镜像** |

### 4.5 高频命令场景化清单（题目问法 → 第一条命令 → 看什么）

> **用法**：考题或排障时，先在这张表里找到对应的问法，**直接照抄第一条命令**，再顺着"接着看什么"往下走。**这张表是索引，4.1 是字典。**

| # | 场景 / 题目问法 | **第一条命令** | 接着看什么 | **写错的后果** |
|---|---|---|---|---|
| 1 | **查某个 Pod 落在哪个节点** | `kubectl get pod <pod> -n <ns> -o wide` | 看 `NODE` 列；批量看用 `kubectl get pods -n <ns> -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName'` | 漏 `-o wide` → **看不到 NODE 列，只能逐个 describe 翻** |
| 2 | **查节点资源占用 / 谁太挤** | `kubectl top nodes`（需 metrics-server） | `kubectl describe node <n>` 看 `Allocatable` 与 `Allocated resources`；再用 `kubectl top pod -A --sort-by=cpu` 找大户 | 没装 metrics-server → 报连接错误，**误以为集群坏了** |
| 3 | **查某个 Pod 的所有事件** | `kubectl describe pod <pod> -n <ns>` | 看末尾 `Events` 段；只取事件用 `kubectl get events -n <ns> --field-selector involvedObject.name=<pod>` | 只 `get` 不 `describe` → **Events 是定位根因的唯一入口** |
| 4 | **跨对象看事件时间线** | `kubectl get events -n <ns> --sort-by=.lastTimestamp` | 找 `Warning` 与 `FailedScheduling`/`FailedMounting`/`Failed` 的先后 | 不排序 → 看到无序列表 → **误判因果关系** |
| 5 | **查 CRD 及其 CR** | `kubectl get crd` | 再 `kubectl get <crd-plural>`（复数形式从 `kubectl api-resources` 查）；字段用 `kubectl explain <crd>.spec` | 凭记忆写 CR 的 kind/plural → **查不到或报 no matches** |
| 6 | **查 Service 的后端端点** | `kubectl get endpoints <svc> -n <ns>` | 空 → selector 没匹配上；新版本也可用 `kubectl get endpointslice -n <ns> -l kubernetes.io/service-name=<svc>` | 只看 `get svc` 看到 ClusterIP 就认为正常 → **503 的根因被漏掉** |
| 7 | **查 Ingress** | `kubectl get ingress -n <ns>` | `kubectl describe ingress <ing> -n <ns>` 看规则与后端；再查后端 Service 的 endpoints | 只看 Ingress 对象 → **规则对但后端无端点，照样 404** |
| 8 | **查证书有效期** | `openssl x509 -in <crt路径> -noout -dates`（在控制面节点上） | CSR 流程查 `kubectl get csr` 与 `kubectl describe csr <name>` | 凭印象说"一年" → **记错有效期，到期才发现** |
| 9 | **查 etcd 健康** | `etcdctl endpoint health --endpoints=... --cacert=... --cert=... --key=...` | `etcdctl endpoint status --cluster -w table` 看 leader 与版本；`etcdctl alarm list` 看告警 | 只看 apiserver 正常就认为 etcd 正常 → **etcd 实际已不可写** |
| 10 | **查控制面组件状态** | `kubectl get --raw='/readyz?verbose'` | 看每个检查项的 ok/failed；容器化部署再 `kubectl -n kube-system get pods` | 只用已废弃的 `componentstatuses` → **拿不到明细** |
| 11 | **查某用户/SA 能不能做某操作** | `kubectl auth can-i <verb> <res> --as=<user或SA> -n <ns>` | 反向再查一次（去掉 `-n` 或换一个动作）确认权限没溢出 | 漏 `--as` → 查的是**你自己**的权限 → **结论完全无效** |
| 12 | **查 API 资源列表 / 短名** | `kubectl api-resources` | `kubectl api-versions` 看版本；字段语义用 `kubectl explain <res>.<field>` | 凭记忆写 kind → **no matches for kind** |
| 13 | **查某个节点上的 Pod** | `kubectl get pods -A --field-selector spec.nodeName=<node>` | 结合 `kubectl describe node <node>` 看 Conditions | 用标签过滤节点上的 Pod → **标签不在 Pod 上，查不到** |
| 14 | **查某个对象的完整配置** | `kubectl get <res> <name> -n <ns> -o yaml` | **spec 与 status 都看** —— 差在哪就是问题在哪 | 只看 spec → **把期望态当实际态** |
| 15 | **查某次变更前会怎样** | `kubectl diff -f <file>` | 输出为空 → 再用 `-o yaml` 对比字段默认值 | 直接 apply → **变更不可见、不可回退** |
| 16 | **查 Deployment 当前发布状态** | `kubectl rollout status deployment/<name> -n <ns>` | **这个命令超时不给原因** → 立刻回去 `describe pod` | 只等 rollout status → **等满时间也不知道卡在哪** |
| 17 | **查谁在用某个节点**（决定能不能 drain） | `kubectl get pods -A --field-selector spec.nodeName=<node>` | 再 `kubectl get pdb -A` 看中断预算 | 不看 PDB 就 drain → **同服务副本被一次赶光** |
| 18 | **查集群里有哪些自定义资源** | `kubectl get <plural> -A` | 先 `kubectl api-resources --api-group=<group>` 找对 group | 用错 api-group → **列不出自定义资源** |

---

## 5. 横向对比

### 5.1 `get` vs `describe` vs `logs`

| 维度 | `kubectl get` | `kubectl describe` | `kubectl logs` |
|---|---|---|---|
| 看什么 | **状态摘要** | **详情 + Events** | **容器标准输出** |
| 输出类型 | 表格 / 结构化 | 人读的长文本 | 文本流 |
| 关键信息 | STATUS、READY、RESTARTS、NODE | Conditions、Events、挂载、探针配置 | 应用自己的日志 |
| 典型问题 | "整体还活着吗" | "**卡在哪一步**" | "**应用自己说了什么**" |
| 何时用 | **第一步，永远先做** | **第二步，卡住时必做** | 第三步，看应用层 |
| 局限 | 看不出原因 | 不含应用日志 | **不含调度与挂载原因** |

**正确顺序（背下来）**：

```
get        →  先定性：Running / Pending / CrashLoop
  ↓ 有异常
describe   →  看 Events：FailedScheduling / Failed / FailedMounting
  ↓ 状态是 Running 但业务不对
logs       →  看应用输出；崩溃过就加 --previous
```

> **反模式**：一上来 `logs`。Pod 还没起来时 logs 报 `ContainerCreating`，什么也看不到。**先 `get` 再 `describe` 最后 `logs`。**

### 5.2 `--selector` vs `--field-selector`

| 维度 | `-l` / `--selector`（标签选择器） | `--field-selector`（字段选择器） |
|---|---|---|
| 匹配对象 | `metadata.labels` | **对象的具体字段**（如 `metadata.name`、`spec.nodeName`、`status.phase`） |
| 支持的语法 | `=`、`!=`、`in`、`notin`、`exists`、`!exists` | **只支持 `=` 和 `==`（以及 `!=`）** —— **不支持集合运算符** |
| 能按 Pod 状态过滤吗 | ❌ 不能（标签里没有 status） | ✅ `--field-selector status.phase=Pending` |
| 能按节点过滤吗 | ❌ 不能 | ✅ `--field-selector spec.nodeName=day2-control-plane` |
| 常见写法 | `kubectl get pods -l app=day2-go` | `kubectl get pods --field-selector status.phase=Failed` |

**高频错答**："用 `-l` 按运行状态过滤 Pod" —— **做不到**，状态不在标签里，必须用 `--field-selector`。

```bash
# ❌ 语法错误：字段选择器不支持 in
kubectl get pods --field-selector 'status.phase in (Pending,Failed)'

# ✅ 改成两次查，或用 label 的存在性
kubectl get pods --field-selector status.phase=Pending
kubectl get pods --field-selector status.phase=Failed
```

**能同时用两个吗**：**可以，而且经常要**。

```bash
# 在指定节点上、处于 Pending 状态、且属于某个应用的 Pod
kubectl get pods \
  --field-selector spec.nodeName=day2-control-plane,status.phase=Pending \
  -l app=day2-go
```

> **事件对象上用字段选择器**（排障高频）：
> `kubectl get events --field-selector involvedObject.name=<pod-name>` —— Events 的字段选择器支持 `involvedObject.name` / `involvedObject.kind` / `reason`，这是**只看某个对象的告警**的最快方式。

### 5.3 `apply` vs `create` vs `replace` vs `edit`

| 维度 | `apply` | `create` | `replace` | `edit` |
|---|---|---|---|---|
| 语义 | 声明期望态，自动 diff | 无条件创建 | **整体替换**（要求资源已存在） | 交互式改单个字段 |
| 幂等 | ✅ 重复执行结果一致 | ❌ 第二次报 `AlreadyExists` | ✅ 覆盖成给定的完整对象 | — |
| 对未在 yaml 中出现的字段 | **按 last-applied 规则保留或抹掉**（有坑） | 全是新值 | **全部抹掉**（只保留给定的） | **只动你改的那一个** |
| 典型场景 | 交付配置、GitOps | 首次创建 | 整体覆盖 | **改一个字段** |
| 主要风险 | 覆盖别人的命令式修改 | 重复执行报错 | **静默抹掉其他字段** | 保存失败、误改 |

**四者之间最容易出事的一条**：

> **`kubectl replace -f` 会把资源替换成 yaml 里写的那个样子，yaml 里没写的字段全部消失**（除非 apiserver 有默认值）。所以**永远不要用一个只写了 `image` 的 yaml 去 replace 一个 Deployment** —— 你的 replicas、resources、labels 全没了。
> **`kubectl edit` 只改你动的那一个字段**，是"改单个字段"场景的正解。

**判断口诀**：

| 题目要求 | 用哪个 |
|---|---|
| 创建一份完整资源 | `apply`（用 `create` 也行，但重复执行会报错） |
| 改一个字段（副本数/镜像/标签/注解） | `edit` 或 `set`（`kubectl set image deploy/x c=<img>`） |
| 改副本数 | `kubectl scale deploy/<n> --replicas=N` |
| **整体替换**（明确要求"把资源改成这个 yaml 的样子"） | `replace`（需要 `--force`） |
| 回滚 | `kubectl rollout undo deploy/<n> [--to-revision=N]` |

> **⚠️ 关于 `kubectl edit` 的考场注意**：`edit` 打开的是 `$EDITOR`（通常是 `vi`）。**考试环境里 `vi` 的按键操作是纯耗时点**——很多人在这里烧掉好几分钟。**能用 `kubectl set` / `kubectl patch` 解决的，就不要用 `edit`。** 若必须用，先把 `EDITOR` 设为 `sed` 风格的只改模式，或至少练熟 `vi` 的 `Esc` → `:wq`。

### 5.4 `delete` vs `drain`

| 维度 | `kubectl delete` | `kubectl drain` |
|---|---|---|
| 对象 | **资源**（Pod/Deployment/Node） | **节点上的 Pod** |
| 语义 | 从 etcd 里删掉这个对象 | 把 Pod **安全地迁走**（尊重 PDB、驱逐 DaemonSet 除外） |
| 会先 cordon 吗 | 不相关 | **是，drain 会自动 cordon** |
| 控制器会重建吗 | 会（Deployment 会立刻补新 Pod） | 会（重建到别的节点） |
| 典型场景 | 删掉不该存在的资源 | **节点维护：升级、关机、换硬件** |

**节点维护的标准三步（背这个）**：

```bash
# ① 标记不可调度（阻止新 Pod 进来）
kubectl cordon day2-worker

# ② 检查中断预算（不做这步是生产事故的常见原因）
kubectl get pdb -A
kubectl get pods -n <ns> --field-selector spec.nodeName=day2-worker

# ③ 驱逐（可迁移的 Pod 会被重建到别的节点）
kubectl drain day2-worker --ignore-daemonsets --delete-emptydir-data
#   --ignore-daemonsets    DaemonSet 的 Pod 不会自动迁走，跳过它们
#   --delete-emptydir-data 允许删除用 emptyDir 的 Pod（**删了数据就没了**）

# ④ 维护完回来
kubectl uncordon day2-worker
```

**drain 卡住不动的常见原因**：

| 卡住原因 | 表现 | 处理 |
|---|---|---|
| DaemonSet Pod 不迁 | drain 报错提示 | 加 `--ignore-daemonsets` |
| 有用 `emptyDir` 的 Pod | drain 报错提示 | 加 `--delete-emptydir-data`（**确认数据可丢**） |
| **PDB 拦住了** | drain 等待 | 查 `kubectl get pdb` —— **这是设计如此，不是 bug** |
| 有 unmanaged Pod（裸 Pod） | drain 报错 | 加 `--force`（**危险，裸 Pod 无人重建**） |

> **高分开场**："`drain` 之前必须看 PDB。PDB 的存在就是为了让你 drain 卡住 —— 那是它在保护你。绕过它（`--force`、改 PDB）等于自己拆掉保护。"

### 5.5 命令式 vs 声明式

| 场景 | 选哪个 | 理由 |
|---|---|---|
| 一次性调参、临时验证 | 命令式 | 快，可逆性靠记得改回去 |
| 交付给团队、审计、GitOps | **声明式** | 有 diff、可 review、可回滚 |
| 考试要求"创建某资源" | 声明式（`apply -f`） | 贴近生产做法 |
| 考试要求"改某字段" | 命令式（`edit`/`set`/`scale`） | 精确、不误伤其他字段 |
| 一次性排障改动 | 命令式 | 排障要的是**快**和**可逆** |

> **一句话原则**：**"交付用声明式，操作用命令式。"**

---

## 6. 踩坑与排障

> **本章所有表格统一五列**：你看到的（症状）| 状态 | 真实原因 | 怎么修 | 下次怎么防。

### 6.1 考题常见误答

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| 题目问"**为什么** Pod 没起来"，你答了一串"怎么做"的步骤 | 命令都对，但没回答问题 | **没区分"为什么"和"怎么做"** —— 前者要给出根因与依据，后者才给操作 | 改成三句：**现象 → 依据（Events 原文）→ 根因**，再补操作 | 读题时圈出疑问词；**"为什么"必须带证据句**（"Events 显示 `Insufficient cpu`"） |
| 题目问"etcd 出问题怎么排查"，你答了 apiserver 重启 | 方向错 | **把 etcd 与 apiserver 混为一谈**：apiserver 重启不能修 etcd 的磁盘满 | 改答 `etcdctl endpoint status --cluster -w table` → `endpoint health` → `alarm list` | 记一条映射：**etcd 存数据（问存储/备份），apiserver 收发（问认证/准入/日志）** |
| 题目问"Pod 起不来"，你查了节点 | 层级错 | **Pod 层问题查了 Node 层**：Pod Pending 优先看 Events 与调度，不是先看节点硬件 | 先 `describe pod`，看 Events 属于哪一类失败 | 建立**默认层级顺序**：Pod 自己的状态 → Events → Node → 控制面 |
| 题目要"备份 etcd"，你答 `tar czf etcd.tar.gz /var/lib/etcd` | 方向错 | **热备份数据目录不是一致性快照**，恢复出来会损坏 | 改答 `etcdctl snapshot save` + `snapshot status` 校验 | 记死一句：**备份用 snapshot save，不是 tar** |
| 题目要"让 Pod 只能被特定节点调度"，你答 `nodeSelector` 和 `taints` 都写上 | 超配 | nodeSelector 是**硬约束**，taint 是**让别人迁走**（除非写 toleration）；两套机制目的不同 | 看清题目要的是"限制放置"（selector/affinity）还是"保护节点"（taint） | 分清两类：**放置约束** vs **驱逐约束** |
| 题目问"怎么让 Pod 崩溃后自动重启"，你答 `readinessProbe` | 语义错 | **readiness 失败只摘端点，不重启**；重启是 liveness 的语义 | 改答 `livenessProbe`（并说明与 readiness 的区别） | 背死：**liveness → 重启；readiness → 摘端点** |

### 6.2 kubectl 语法细节错

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `error: the server doesn't have a resource type "svc"` | 命令报错 | **资源短名不认识**（或 API 组写错） | 用 `kubectl api-resources` 查真实短名与 kind | 记不住就查，**不要猜** |
| 查出来是空，但资源明明存在 | 空输出 | **漏了 `-n <命名空间>`**，资源在别的 ns | 补 `-n`；或用 `kubectl get <res> -A` 全局看 | 每条命令先想"资源在哪个 ns" |
| `-l app=day2-go` 查不到 | 空输出 | 标签不在 Pod 上，或值写错 | `kubectl get pods --show-labels` 核对；再看 Deployment 的 `spec.template.metadata.labels` | **选择器查空 → 第一反应是去掉选择器看全量** |
| `kubectl get pods --field-selector 'status.phase in (Pending,Failed)'` 报错 | 参数错误 | 字段选择器**不支持集合运算符** | 拆成两次查询 | 字段选择器只有 `=` / `!=` |
| `kubectl apply` 报 `metadata.labels: Invalid value` | 参数错误 | 标签值含非法字符（`/`、空格、大写等） | 改成合法标签值 | 标签值用小写短横线 |
| `kubectl get pods -o jsonpath='{.items[0].metadata.name}'` 什么都没输出 | 静默空 | 当前 ns 没有 Pod → `items` 为空数组 | 先确认有对象；空数组取 `[0]` 会报错或空 | jsonpath **空输出不报错**，一定要交叉验证 |
| `kubectl logs <pod>` 报 `pod has no containers` / 多容器报错 | 命令错 | Pod 有多个容器 | 加 `-c <容器名>` | 多容器 Pod 先 `describe` 看容器列表 |
| `kubectl exec -it <pod> -- sh` 卡住不动 | 交互等待 | 目标 Pod 没有 TTY 或命令在等输入 | 去掉 `-it`；或加 `-c` | 非交互场景不要带 `-it` |
| `kubectl delete deploy/x` 后 Pod 还在 | 现象 | 默认 `--cascade=background`，本体先删、子资源异步删 | 用 `--cascade=foreground` 等子资源删完 | 删除有依赖的资源，**默认加 foreground** |
| `kubectl edit` 打开后按错键，文件被改坏 | 误操作 | `vi` 操作不熟，误删了内容 | `Esc` → `:q!` 放弃修改 | 练熟 `Esc` → `:wq`（保存退出）与 `:q!`（放弃） |
| `kubectl apply -f x.yaml` 提示 `error: 0 rows matched` | 无匹配 | 文件里的资源在别的 ns，或 `--namespace` 覆盖了 yaml 里的 ns | 检查 yaml 的 `metadata.namespace` | **注意 `apply -f` 与 `--namespace` 的优先级：命令行的会覆盖 yaml 里的** |

### 6.3 etcd 备份与恢复命令易错

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `etcdctl snapshot save` 报连接被拒 | 命令失败 | 缺 `--endpoints`，或没走 https | 补 `--endpoints=https://127.0.0.1:2379` 与三个证书参数 | 记死四件套：`--endpoints` `--cacert` `--cert` `--key` |
| `etcdctl` 报 `unsupported protocol scheme` | 命令失败 | **v3 需要 `ETCDCTL_API=3`** 环境变量 | `export ETCDCTL_API=3` | 每次进新终端先 export |
| `snapshot save` 成功但**文件是空的/很小** | 快照异常 | 连错了端点（比如连到了另一个 etcd），或没指定 endpoints | `etcdctl endpoint status --cluster -w table` 确认连的是哪台 | **备份后立刻 `snapshot status` 看 revision 和大小** |
| `snapshot status` 报 `snapshot hash mismatch` | 快照损坏 | 拷贝过程不完整，或磁盘问题 | 重新备份并重新拷贝 | **校验不过的快照不能用来恢复** |
| 恢复后 etcd 起不来，日志报集群 ID 不匹配 | 恢复失败 | `--name` / `--initial-cluster` 与原集群不一致 | 从原集群的 `etcdctl member list` 取真实成员名与 peer URL | 备份时**同时记录成员名与 peer URL** |
| 恢复后 apiserver 报连不上 etcd | 恢复失败 | 静态 Pod 的 `--data-dir` 没改成新目录 | 改 `/etc/kubernetes/manifests/etcd.yaml` 的 `--data-dir` | 恢复流程固定四步：**停 etcd → 恢复 → 改 `--data-dir` → 重启** |
| 恢复成功但集群资源全空 | 恢复到了错的快照 | 备份的是**另一套集群**的快照 | 用 `kubectl get ns` 核对 | **每个集群独立命名备份文件** |
| 把快照存在 etcd 所在的同一台机器 | 隐患未爆发 | 备份与被备份对象同生共死 | 拷到集群外（异机 / 对象存储） | **备份的价值 = 能在你需要它时还在** |

### 6.4 证书过期排查路径

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `Unable to connect to the server: x509: certificate has expired or is not yet valid` | 连接失败 | 客户端或服务端证书过期 | 用 `openssl` 查各证书有效期，定位到具体文件后重新签发 | 定期查有效期；证书纳管台账 |
| `x509: certificate signed by unknown authority` | 连接失败 | **CA 不匹配**（客户端信任的 CA 与服务端签发者不是同一个） | 核对 `/etc/kubernetes/pki/ca.crt` 与 kubeconfig 里的 CA | 换 CA 时所有证书都要重签 |
| 节点 `NotReady`，但其上的 Pod 仍 `Running` | 状态不一致 | **kubelet 客户端证书过期** → kubelet 无法与 apiserver 通信，状态不再上报 | 重新签发 kubelet 客户端证书并重启 kubelet | **看 NotReady 节点先查 kubelet 与证书** |
| `kubectl get csr` 显示 `Approved`，但 `.status.certificate` 是空的 | 流程卡住 | 审批了但**签名器没签发** | 检查 controller-manager 的 `--cluster-signing-cert-file`/`--cluster-signing-key-file` | 审批后一定查 `.status.certificate` |
| CSR 一直 `Pending`，`describe` 里说 usages 不被允许 | 流程卡住 | 申请的 `usages` 与目标 signer 不匹配 | 改用正确的 usages（如 `client auth`） | 提交前先确认 signer 名称与 usages |
| kubelet 反复申请证书，日志报 `certificate signed by unknown authority` | 循环失败 | `--cluster-signing-*` 未配置，或 kubelet 未开 `--rotate-certificates` | 补齐 controller-manager 配置并开启 kubelet 自举 | 部署时把这两个参数写进配置管理 |
| 证书快到期但没过期 | 隐患 | **没有轮转机制**，靠人记得换 | 建立轮转或告警 | 加"证书剩余有效期"监控 |

### 6.5 Node 问题与 Pod 问题分不清

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| 业务 Pod 一直 `Pending`，你去看节点磁盘 | 排查方向错 | Pending 多为**调度层**问题：资源、亲和、污点、卷 | 先 `describe pod` 看 `FailedScheduling` 原文 | **先看 Pod 的 Events，再看节点** |
| 节点 `NotReady`，你去重启业务 Pod | 排查方向错 | 节点问题在 **kubelet / 容器运行时 / CNI / 证书** | `kubectl describe node` 看 Conditions；查节点上 kubelet 日志 | 记住判据：**节点问题表现为"多个 Pod 同时异常"** |
| 一个 Pod 起不来，同节点其他 Pod 正常 | 层级判定 | **Pod 层问题**（镜像、配置、探针、命令） | `describe` + `logs --previous` | **单点异常 → Pod 层；多点异常 → Node 层** |
| 多个节点上不同 Pod 同时起不来 | 层级判定 | **集群层问题**（apiserver、etcd、CoreDNS、镜像仓库） | 查控制面与 `kube-system` | **多点异常 → 往集群层查** |
| 节点磁盘满，Pod 表现为 `Evicted` | 症状跨层 | **节点资源问题表现为 Pod 状态** | `kubectl describe node` 看 `Allocatable` 与 `Conditions`；`df -h` | **看到 Evicted 先问"为什么被驱逐"** |
| 节点内存不足，Pod 表现为 `OOMKilled` | 症状跨层 | **容器内存超限**（节点层是诱因） | 看 `limits.memory` 与实际用量 | 分清 OOMKilled（容器超限）与 Evicted（节点资源不足） |
| 节点 `NotReady` 且 Pod 状态 `Unknown` | 状态组合 | **kubelet 失联，状态无法上报** | 查 kubelet 服务与证书 | **Unknown = 没人汇报状态，不是 Pod 自己的问题** |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `www.cncf.io/training/certification/cka/` | **CKA 认证页（知识域与政策的唯一权威来源）** | ✅ 2026-10-24 |
| S2 | 官方 | `kubernetes.io/docs/reference/using-api/` | 资源清单字段语义、API 契约 | ✅ 2026-10-24 |
| S3 | 官方 | `kubernetes.io/docs/concepts/overview/` | 概念总览、知识域映射 | ✅ 2026-10-24 |
| S4 | 中文 | `kubernetes.io/zh-cn/docs/reference/kubectl/quick-reference/` | **kubectl 速查（命令与参数）** | ✅ 2026-10-24 |
| S5 | 中文 | `kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-pods/` | **Pod 排障（状态、Events、日志）** | ✅ 2026-10-24 |
| S6 | 中文 | `kubernetes.io/zh-cn/docs/tasks/debug/debug-cluster/` | **集群排障（节点、控制面）** | ✅ 2026-10-24 |
| S7 | 中文 | `kubernetes.io/zh-cn/docs/tasks/manage-kubernetes-objects/declarative-config/` | 声明式配置、apply vs edit | ✅ 2026-10-24 |
| S8 | 中文 | `kubernetes.io/zh-cn/docs/concepts/architecture/` | 控制面组件、etcd 角色、kubelet 职责 | ✅ 2026-10-24 |
| S9 | 中文 | `kubernetes.io/zh-cn/docs/concepts/workloads/` | 工作负载控制器总览 | ✅ 2026-10-24 |
| S10 | 中文 | `kubernetes.io/zh-cn/docs/concepts/services-networking/service/` | Service 类型与端点语义 | ✅ 2026-10-24 |
| S11 | 中文 | `kubernetes.io/zh-cn/docs/concepts/services-networking/ingress/` | Ingress 规则与后端 | ✅ 2026-10-24 |
| S12 | 中文 | `kubernetes.io/zh-cn/docs/concepts/storage/persistent-volumes/` | PV/PVC 绑定与访问模式 | ✅ 2026-10-24 |
| S13 | 中文 | `kubernetes.io/zh-cn/docs/concepts/scheduling-eviction/assign-pod-node/` | 节点选择、调度约束 | ✅ 2026-10-24 |
| S14 | 中文 | `kubernetes.io/zh-cn/docs/concepts/configuration/configmap/` · `.../secret/` | 配置与密钥 | ✅ 2026-10-24 |
| S15 | 中文 | `kubernetes.io/zh-cn/docs/concepts/security/rbac-good-practices/` | RBAC 与授权排障 | ✅ 2026-10-24 |
| S16 | 中文 | `kubernetes.io/zh-cn/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/` | CRD 与 CR 操作 | ✅ 2026-10-24 |
| S17 | 中文 | `kubernetes.io/zh-cn/docs/concepts/cluster-administration/logging/` | 日志采集与检索 | ✅ 2026-10-24 |
| S18 | 中文 | `kind.sigs.k8s.io/docs/user/quick-start/` | **kind 本地集群（含多节点配置）** | ✅ 2026-10-24 |
| S19 | 中文 | `kubernetes.io/zh-cn/docs/concepts/configuration/manage-resources-containers/` | requests/limits、QoS | ✅ 2026-10-24 |
| S20 | 官方 | `kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/` | **HPA 公式、分母（`requests.cpu`）、同步与缩容窗口** | ✅ 2026-10-24 |
| S21 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 托管版与自建版差异（生产形态对照） | ✅ 2026-10-24 |
| S22 | 中文 | 中文题库与学习社区（**索引未提供单条 URL**） | 备考题型分布的社区口径 | ⚠️ 单源待补（需补具体 URL） |
| S23 | 官方 | etcd 官方文档（**索引未收录**） | `etcdctl` 参数与恢复流程细节 | ⚠️ 单源待补（etcd 官方文档未进索引） |

> **来源纪律说明**：
> ① CKA 的**知识域名称、配比、题量、总时长、及格线**一律以 **S1 官方页面当前表述为准**，本篇只给"通用知识域 + 明确标注的经验判断"。
> ② etcd 的 `snapshot restore` 参数细节（S23 缺失）仅在 kubeadm 场景下的 3.3 节出现，已在该处标 `⚠️ 待验证`。
> ③ **本文所有 URL 均取自** [`../09-参考索引/README.md`](../09-参考索引/README.md) 中标 ✅ 的条目，**无自造深层链接**。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— `kubectl get pods -o yaml` 的输入是"apiserver 上的 Pod 对象"，输出是"期望态 `spec` + 实际态 `status`"；`kubectl describe` 的输入是同一个对象，输出是"人读的摘要 + Events 时间线"；`kubectl logs` 的输入是"容器标准输出"，输出是"应用自己的话"。**三者看的是同一次故障的三个侧面，缺一层就定位不到根因。**
2. **能故意改坏再倒推吗？** —— 五个可执行的破坏实验（每条都会产生确定的、可观察的现象）：
   - ① 把 Deployment 的 `spec.template.metadata.labels` 改成 `app: wrong`（与 `spec.selector` 不一致）→ `apply` 直接报 selector 与模板标签不符；
   - ② 删掉容器的 `resources.requests.cpu` → 装上 metrics-server 后 HPA 显示 `<unknown>/50%`；
   - ③ 把 `livenessProbe.httpGet.port` 改成容器不监听的端口 → Pod 进入 `Running 0/1` → `CrashLoopBackOff`；
   - ④ 把 Service 的 `spec.selector.app` 改成一个不存在的值 → `kubectl get endpoints` 返回空 → 访问 503（**但 Pod 一切正常**）；
   - ⑤ 把 `image` 的 tag 改错 → `ImagePullBackOff`，Events 里出现 `Failed to pull image`。
   > **每个实验都用 `describe` 走一遍 Events**，把"看到的症状 → 状态 → Events 原文 → 根因"四步写下来。这就是排障路径的肌肉记忆。
3. **能画上下游吗？** —— 画出这条链，并在每个箭头旁标出"**这一步失败会看到什么**"：
   ```
   kubectl → apiserver（认证/授权/准入）→ etcd → controller 协调
          → scheduler 派活 → kubelet 拉镜像/建容器 → CNI 配网 → 探针 → Ready
   ```
   同时画出**观测链**：`kubectl get`（读 etcd 的 status）/ `describe`（读 Events）/ `logs`（读节点上的容器输出）—— **三根探针对应三个不同的真相来源**。

---

## 附：待验证清单

| # | 待验证项 | 涉及章节 | 验证方法 |
|---|---|---|---|
| 1 | 官方页面当前的知识域名称与配比 | 1.2 | 打开 S1 官方页面核对 |
| 2 | 官方当前的题量、总时长、及格线 | 1.2 | 打开 S1 官方页面核对（本篇刻意未写具体数值） |
| 3 | `kubectl compact` 在 v1.37.x 是否默认可用 | 2.9 / 4.1（资源压缩） | `kubectl compact --help`；该命令把 etcd 中对象的存储表示改写为更紧凑形式（清理已终止 Pod 等），**不是"压缩 yaml 文件"**。⚠️ 本篇未实测其行为与版本可用性 |
| 4 | `--dry-run=true` 布尔写法在 v1.37.x 是否仍兼容 | 2.3 | `kubectl apply --help` 看取值说明 |
| 5 | `snapshot restore` 的 `--name` / `--initial-cluster` 在 kubeadm 集群上的实际取值 | 3.3 | 在 kubeadm 集群上 `etcdctl member list` 取真实值 |
| 6 | kind 中控制面静态 Pod 目录的确切宿主机路径 | 2.6 | `docker inspect day2-control-plane` 查挂载；本篇只写"容器内"，未给具体宿主机路径 |
| 7 | `kubectl get endpoints` 的弃用提示文案 | 4.2 | 在 v1.37.x 上执行一次并记录原文（本篇只描述现象，未抄原文） |

---

**下一篇**：[`L2-CKA全真模拟.md`](L2-CKA全真模拟.md)（模拟环境 + 12 题全真卷 + 复盘表） · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
