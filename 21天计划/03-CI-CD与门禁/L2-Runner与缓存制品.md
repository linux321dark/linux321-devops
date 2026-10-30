# L2 · Runner 与缓存制品（谁在跑、跑在哪、产物怎么传）

> **本篇定位**：`.gitlab-ci.yml` 写的是"要做什么"，**Runner 决定"谁来做、在哪做"**。本篇讲清 Runner 的三个组件、executor 类型选择、注册与认证的新旧令牌、缓存与制品两套机制、DinD 与 Docker socket 的安全取舍，以及"在 CI 里构建并推送镜像"的完整标准流水线。
> **前置**：L1（`.gitlab-ci.yml` 的 stage/job/rules/needs/artifacts/cache）、L3（镜像引用、digest、Trivy 扫描）。
> **学完能做什么**：说清"为什么不能直接用 Shell 跑流水线"；独立注册一个 runner 并配好 tag；选对 executor 类型；解释 cache 与 artifacts 的区别；写出一条带门禁的"构建→测试→扫描→推送→部署"流水线；把镜像 tag 从 `latest` 换成 commit SHA。
> **在 21 天里**：D4（GitLab CI 云原生化）的"runner 验证"环节；D5（质量门禁）的扫描接入门禁。
> **环境/版本基准**：Runner 语义以 **GitLab 官方 `docs.gitlab.com`** 为准（**registration token 已弃用**，改用 `glrt-` 前缀的 authentication token，见 2.5）；镜像仓库前缀 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`；本地 Docker **29.8.2**。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L2 Runner 与缓存制品
│
├─ 1. 概念
│   ├─ 1.1 Runner 是什么（GitLab 与"干活的机器"之间那道桥）
│   ├─ 1.2 为什么不能直接用 Shell 跑流水线（三个真实污染场景）
│   ├─ 1.3 三个核心组件：Coordinator / Executor / Cache 助手
│   └─ 1.4 术语表
│
├─ 2. 机制
│   ├─ 2.1 Runner 的执行流程（四个阶段）
│   ├─ 2.2 executor 类型全景与选型
│   ├─ 2.3 docker executor vs kubernetes executor（最关键的一组对比）
│   ├─ 2.4 Runner 的注册与认证（新旧令牌）
│   ├─ 2.5 runner tag 的匹配机制与"抢同一个 runner"问题
│   ├─ 2.6 缓存机制：key 相同才命中
│   ├─ 2.7 artifacts 机制：有有效期，会过期
│   ├─ 2.8 DinD vs Docker socket：安全与速度的取舍
│   ├─ 2.9 标准流水线：build → test → scan → push → deploy
│   ├─ 2.10 CI 里的 buildx 多架构与缓存
│   └─ 2.11 产物可追溯：tag 用 commit SHA 而不是 latest
│
├─ 3. 动手：在 CI 里构建并推送镜像
│
├─ 4. 逐字段表
│   ├─ 4.1 runner 关键配置表（配置项 | 类型 | 作用域 | 作用 | 写错的后果）
│   └─ 4.2 job 阶段表（阶段 | 干什么 | 依赖 | 失败意味着 | 产物）
│
├─ 5. 横向对比
│   ├─ 5.1 Shell vs Docker vs Kubernetes executor
│   ├─ 5.2 缓存 vs 制品
│   └─ 5.3 DinD vs Docker socket
│
├─ 6. 踩坑与排障（症状 | 状态 | 真实原因 | 怎么修 | 下次怎么防）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 Runner 是什么

> **官方定义**：GitLab Runner 是**与 GitLab CI/CD 配合、在流水线里执行 job 的应用程序**。

**它站在 GitLab 和"干活的机器"之间**：

```
        GitLab 服务器                        Runner（你控制的机器）
   ┌────────────────────┐              ┌──────────────────────────┐
   │  ① 解析 .gitlab-ci  │   ② 领活    │  ③ 准备环境              │
   │     .yml 生成 job  │ ──────────► │     拉代码 / 恢复缓存     │
   │                    │              │  ④ 执行 script           │
   │  ⑤ 展示结果/存制品 │ ◄────────── │  ⑤ 上传产物 / 回传状态   │
   └────────────────────┘              └──────────────────────────┘
         "大脑"                              "手脚"
```

**关键认知：GitLab 本身不执行你的 job。**

GitLab 做的事只有三件：**解析 YAML → 排队 → 展示结果**。**真正跑 `mvn package`、`docker build`、`kubectl apply` 的是 runner。** 这解释了几件事：

| 现象 | 解释 |
|---|---|
| **为什么自建 GitLab 也要单独装 runner** | GitLab 是编排者，不是执行者 |
| **为什么 job 会一直 pending** | **GitLab 有 job，但没人领活** → runner 没注册、离线、或 tag 不匹配 |
| **为什么"流水线卡住"有时候不是 GitLab 的锅** | 是 runner 侧的问题（机器满了、磁盘满了、网络断了） |
| **为什么 runner 是安全边界** | **job 里的代码在 runner 上执行**，runner 被攻破 = 你的机器失守 |

### 1.2 为什么不能直接用 Shell 跑流水线

**"直接用 Shell 跑"指的是**：在一台机器上写个脚本，顺序执行 `mvn package`、`docker build`、`kubectl apply`，串起来当流水线用。

**这在小规模时能跑，但有三个必炸的场景**：

| 场景 | 具体表现 | 根因 |
|---|---|---|
| **① 执行环境不隔离** | job 3 装的依赖影响了 job 4；某个 job 改了系统 Python 版本，后面全崩 | **所有 job 共享同一个 shell 环境的文件系统** |
| **② 依赖不干净** | A 项目要 Node 16，B 项目要 Node 20，**只能装一个**。装了 A 的版本，B 的构建莫名其妙失败 | **依赖装在全局，谁都能改，谁都受影响** |
| **③ 不同 job 互相污染** | job 1 留下 `/tmp/build` 里的半成品文件，job 2 恰好读到 → **构建出错误的产物，而且不报错** | **工作目录与临时文件是共享的** |

**再补三个"用 Shell 就做不到"的能力**：

| 能力 | 用 Shell | 用 Runner |
|---|---|---|
| **并行** | 脚本里 `&` 后台跑，**失败收不回来**，日志全混在一起 | **每个 job 独立环境，天然并行，日志分离** |
| **缓存与制品** | 自己写 `tar` 传到某处 | **GitLab 托管的 cache/artifacts，有生命周期、有 API** |
| **权限与审计** | 脚本以你的 SSH 用户跑，**权限全开** | **token 认证 + protected 变量 + 每次执行的完整记录** |

> **面试这么答**："直接用 Shell 串脚本的致命问题是**执行环境不隔离、依赖不干净、不同 job 互相污染**——最隐蔽的是第三种：前一个 job 留下的半成品文件被后一个 job 读到，**构建出错误产物但不报错**。Runner 解决的是**每个 job 一个干净环境 + 统一调度 + 缓存制品托管 + 权限审计**这四件事。**流水线规范化的本质是让每次构建都是可复现的。**"

### 1.3 三个核心组件

**Runner 内部有三个角色**（这是本篇最重要的机制性认知）：

```
┌──────────────────────────────────────────────────────┐
│  Runner Manager（runner manager）                     │
│  进程：读 config.toml，并发跑多个 runner configuration │
└───────────────┬──────────────────────────────────────┘
                │
    ┌───────────┼────────────────┐
    ▼           ▼                ▼
┌────────┐ ┌─────────┐   ┌──────────────────┐
│Coordinator│ │Executor │   │  Cache 助手       │
│协调器    │ │执行器   │   │  缓存上传/下载     │
│        │ │        │   │                  │
│向 GitLab│ │真正跑   │   │ 只处理 cache 这一  │
│领活/派活│ │job 的   │   │ 类"重复劳动"       │
│        │ │命令    │   │                  │
└────────┘ └─────────┘   └──────────────────┘
```

| 组件 | 干什么 | 关键点 |
|---|---|---|
| **Coordinator（协调器）** | **向 GitLab 请求 job**（长轮询），拿到 job 后交给 Executor | **一个 runner 可以并发领多个 job**（受 `concurrent` / `limit` / `request_concurrency` 控制） |
| **Executor（执行器）** | **真正执行 job 的命令**，按 executor 类型决定在什么环境里执行 | **executor 类型 = "在哪跑"的答案**（Shell / Docker / Kubernetes / SSH…） |
| **Cache 助手** | 专门处理缓存的上传与下载 | **它只处理 cache，不处理 artifacts**——这是 2.6 与 2.7 那个经典混淆的技术根源 |

> **为什么"三个组件"这个划分值得记**：因为它**解释了三个不同的问题各自归谁**。
> - **"job 没人领"** → Coordinator 的问题（tag 不匹配、runner 离线、`request_concurrency` 太小）
> - **"job 跑不起来/环境不对"** → Executor 的问题（executor 类型选错、镜像不对、权限不足）
> - **"缓存不命中"** → Cache 助手 + `cache:key` 策略的问题（**注意不是 artifacts 的问题**）

### 1.4 术语表

| 英文 | 中文 | 一句话 |
|---|---|---|
| runner | 执行器（实例） | 注册到 GitLab 上、能领 job 的程序 |
| runner manager | runner 管理进程 | 读 `config.toml`、并发跑多个 runner configuration 的进程 |
| runner configuration | runner 配置项 | `config.toml` 里的一个 `[[runners]]` 段，UI 上显示为一个 runner |
| executor | 执行方式 | runner 用什么环境跑 job（Shell / Docker / Kubernetes…） |
| machine | 机器 | runner 实际干活的机器或 Pod（可能是 autoscaler 动态创建的） |
| tag | 标签 | runner 与 job 的匹配标记 |
| `run_untagged` | 跑无标签任务 | 该 runner 是否也接不带 `tags` 的 job |
| protected runner | 受保护 runner | 只在受保护分支/tag 上跑 |
| `concurrent` | 全局并发 | 所有 runner 加起来最多同时跑多少 job |
| `limit` | 单 runner 并发 | 这个 runner configuration 最多同时跑多少 job |
| `request_concurrency` | 请求并发 | 同时向 GitLab 请求新 job 的数量 |
| `check_interval` | 轮询间隔 | 请求新 job 的间隔，默认 **3 秒** |
| DinD | Docker-in-Docker | 在容器里再跑一个 Docker daemon |
| helper image | 助手镜像 | 跑 clone / cache / artifacts 操作的内部镜像 |
| `pull_policy` | 拉取策略 | 每次建容器前是否重新拉镜像（`always` / `if-not-present` / `never`） |
| `cache_dir` | 缓存目录 | 宿主机上存放缓存的根目录 |
| `builds_dir` | 构建目录 | 宿主机上存放 job 工作区的根目录 |
| distributed cache | 分布式缓存 | 上传到远端存储、多个 runner 共享的缓存 |
| ephemeral runner | 临时 runner | 每次 job 用完即销毁（隔离最好，成本最高） |

---

## 2. 机制

### 2.1 Runner 的执行流程（四个阶段）

**官方把一次 job 的执行分成四段**（docker / kubernetes executor 都是这个结构）：

```
Prepare（准备）
  └─ 创建容器 / Pod，拉取镜像，建网络与卷，启动 services
      ↓
Pre-job（作业前）
  └─ clone 代码 → 恢复 cache → 下载前序 artifacts
      ★ 这一段在 helper 容器里跑
      ↓
Job（作业）
  └─ 在 build image 里执行用户定义的 script
      ★ 这一段在 job 指定的镜像里跑
      ↓
Post-job（作业后）
  └─ 创建/上传 cache → 上传 artifacts
      ★ 这一段也在 helper 容器里跑
```

**这个四段结构解释了几件很实用的事**：

| 现象 | 解释 |
|---|---|
| **为什么 `docker.sock` 挂载后能在 job 里直接 `docker build`** | 因为 job 阶段跑在 build 容器里，容器里的 `docker` CLI 通过 socket 连的是**宿主机的 daemon** |
| **为什么 clone 失败报的是 git 相关错误而不是 script 错误** | clone 在 **Pre-job** 阶段，**根本没进你的 script** |
| **为什么 cache 恢复失败不会让 job 失败** | cache 是"锦上添花"，**恢复失败会跳过并继续** |
| **为什么 artifacts 上传失败会让 job 失败** | 上传在 Post-job，**失败会反映到 job 状态** |

> **"clone / cache / artifacts 都在 helper 容器里"这个细节很重要**：它解释了**为什么你的 job 镜像里不需要装 git**（helper 负责 clone），也解释了**为什么 `docker.sock` 挂载后 helper 容器也能用 docker CLI**（同一个 Pod/主机）。

### 2.2 executor 类型全景与选型

| executor | job 跑在哪 | 隔离强度 | 资源开销 | 典型场景 |
|---|---|---|---|---|
| **`shell`** | **runner 主机上直接跑**（以 runner 用户的权限） | ❌ **最弱** | 最低 | 本地调试、需要宿主工具（特定 SDK） |
| **`docker`** | **每个 job 一个容器**（同一台宿主机） | ✅ 中 | 中 | **最常用**：通用 CI、需要固定工具链 |
| **`kubernetes`** | **每个 job 一个独立 Pod** | ✅ **强** | 中高（Pod 调度开销） | **跑在 K8s 集群里，弹性、与生产同构** |
| **`ssh`** | **远程机器**（SSH 过去执行） | ⚠️ 弱（默认无严格 host key 检查） | 低 | 必须在特定物理机上跑（嵌入式设备、专有硬件） |
| **`docker+machine` / `docker-autoscaler`** | **动态创建的 VM**（用完即弃） | ✅ 强（每 job 一台 VM） | **高** | **不可信代码、需要强隔离、弹性扩容** |
| **`custom`** | 自定义（自己实现 executor 接口） | 取决于实现 | 取决于实现 | 特殊环境（自研 PaaS） |
| **`parallels`** | Parallels 虚拟机 | ✅ **最强**（全系统虚拟化） | 高 | Windows 场景、隔离要求极高 |

**官方对安全性的直接表述（要点）**：

| executor | 官方风险定性 |
|---|---|
| `shell` | **"High-security risks exist to your runner host and network"** —— job 以 runner 用户权限运行，**能偷其他项目的代码**；**只用于可信构建** |
| `docker` | **"Docker can be considered safe when running in non-privileged mode"** —— 非特权模式下算安全；**privileged 容器拥有宿主机全部 root 权限** |
| `kubernetes` | 天然是 Pod 隔离，但仍需配 RBAC 限制 runner 的 ServiceAccount |
| `ssh` | ** susceptible to MITM** —— 因为缺少严格 host key 检查 |
| `parallels` | **"the safest possible option"** —— 全系统虚拟化，隔离所有外设与共享目录 |

> **`shell` executor 为什么危险**：它以 **runner 进程的权限**执行。**同一个 runner 上跑的项目 A 的代码，能读到项目 B 的源码**（都在同一个文件系统的 `builds_dir` 里）。**所以"shell executor 只用于自己信任的私有项目"是硬约束，不是建议。**

### 2.3 `docker` executor vs `kubernetes` executor

**这是本篇最需要讲透的一组对比**，因为它直接决定"你的 CI 环境和生产环境有多像"。

| 维度 | `docker` executor | `kubernetes` executor |
|---|---|---|
| **job 的隔离单元** | 一个**容器**（同一台宿主机） | 一个 **Pod**（集群里调度） |
| **Pod 结构** | build 容器 + helper 容器 + service 容器 | **build 容器 + helper 容器 + service sidecar**（同一个 Pod 内） |
| **service 的网络** | 同容器网络 | **同 Pod，共享 `localhost`**，可用 DNS 名互相访问 |
| **能否弹性扩缩** | ❌ 宿主机上最多跑 `concurrent` 个 | ✅ **集群层面扩 runner 副本**，Pod 调度到哪算哪 |
| **资源限制怎么配** | `cpu_shares` / `cpus` / `memory` / `shm_size`（Docker 层面） | **`cpu_limit` / `cpu_request` / `memory_limit` / `memory_request` / `pod_*`**（K8s 层面） |
| **job 能否覆盖资源** | ❌ | ✅ **job 可用 `KUBERNETES_*` 变量覆盖**，且管理员能用 `*_overwrite_max_allowed` **设上限** |
| **需要 RBAC 吗** | ❌ 不需要 | ✅ **需要**（Pod 的 create/list/attach/exec/logs/delete + secrets + serviceaccounts…） |
| **每 job 独立 namespace** | ❌ | ✅ `namespace_per_job = true`（**需要 ClusterRole**） |
| **PVC / 持久卷** | 靠 `volumes` 挂宿主目录 | ✅ **原生 `volumes`（hostPath / PVC）**，还可用 `pod_spec` 创建临时 PVC |
| **与生产同构性** | ⚠️ 中等（容器 ≠ Pod） | ✅ **高（Pod + K8s 资源模型 + ServiceAccount）** |
| **额外风险** | **privileged / docker.sock 逃逸** | **RBAC 配太宽 → runner 被利用可横向操作集群** |

**"更接近生产运行形态"这句话具体指什么**：

| 生产里跑的 | docker executor 里的对应物 | kubernetes executor 里的对应物 |
|---|---|---|
| Pod | 容器（**少了 ServiceAccount、探针、多容器协同**） | **Pod（结构一致）** |
| `resources.limits` | `memory` / `cpus`（**语义不完全等价**） | `memory_limit` / `cpu_limit`（**就是 K8s 的 limits**） |
| ServiceAccount + RBAC | ❌ **没有** | ✅ **有** |
| NetworkPolicy | ❌ | ✅ **集群里有就生效** |
| PVC | 挂宿主目录（**不是 PVC**） | ✅ **可以挂 PVC** |

> **这是选型时最实在的判断依据**：
> **如果你的生产跑在 K8s 上，且你的 job 需要 ServiceAccount 权限、需要按 namespace 隔离、需要拉 PVC 上的配置或数据——那 kubernetes executor 明显更合适。**
> **因为 docker executor 里根本没有 ServiceAccount 这个概念**，你的 job 里跑 `kubectl` 时用的是**你自己给的 kubeconfig**，权限边界完全在 job 内部，**GitLab 管不到**。

**kubernetes executor 的四个阶段与 RBAC 要点**：

| 要点 | 说明 |
|---|---|
| **ServiceAccount 权限是硬要求** | 必须能在目标 namespace 里 **create / list / attach** Pod（还要 `pods/exec`、`pods/log`、必要时 `secrets`、`serviceaccounts`、`services`） |
| **`namespace_per_job = true` 要集群级权限** | namespace 级 `Role` **无法授予跨 namespace 权限** → 必须 `ClusterRole` + `ClusterRoleBinding` |
| **helper 容器的内存别给太小** | 官方建议：**有缓存和 artifacts 的负载至少 `250 MiB`**；无缓存无制品的基础负载可能只需 `128–200 MiB` |
| **Pod 级资源配置有版本要求** | Pod-level resource specifications 在 **K8s v1.32 引入（alpha）**，**v1.34 升级为 beta** |

> **本机 K8s 是 v1.37.x**，**远超 v1.34** → **Pod 级资源配置（`pod_cpu_limit` / `pod_memory_limit` 等）可以用**。这个跨篇口径要保持一致。

### 2.4 Runner 的注册与认证（新旧令牌）

**先说结论：registration token 已被官方弃用。**

| 令牌类型 | 状态（官方口径） | 前缀 | 用途 |
|---|---|---|---|
| **runner authentication token** | ✅ **当前推荐** | **`glrt-`** | 注册 runner；**在 UI 或 API 里创建 runner 后拿到** |
| **runner registration token** | ⚠️ **已弃用**，**排期在 GitLab 20.0 移除** | 无固定前缀 | 旧流程：在实例/群组/项目设置里取一个"注册令牌" |

**关键时间线（官方原文口径）**：

| 事实 | 说明 |
|---|---|
| **GitLab 15.6 起弃用** | registration token 与相关注册参数被标记弃用 |
| **GitLab 16.2 起提供"兼容旧流程"** | 如果把 authentication token 传给了 `--registration-token` 参数，会走 legacy-compatible 分支，**并忽略一批命令行配置项** |
| **GitLab 17.0 起可被禁用** | 管理员/群组 owner 可以关闭旧的注册流程 |
| **禁用后用旧 token 注册会报 `410 Gone`** | 官方原文：`runner registration disallowed` |
| **移除排期：GitLab 20.0** | |
| **GitLab Runner 15.0 起** | 注册请求格式变更，**runner 版本必须与 GitLab 版本匹配** |

**新流程 vs 旧流程的实际差别**：

| | 旧（registration token） | 新（authentication token） |
|---|---|---|
| **配置在哪做** | **注册时在命令行传**（`--tag-list`、`--run-untagged`、`--locked`、`--access-level`…） | **在 UI / API 里创建 runner 时就配好** |
| **注册命令** | `gitlab-runner register --registration-token <token> --executor docker --tag-list "..."` | `gitlab-runner register --token <glrt-...> --executor docker` |
| **同一 token 能否多台机器复用** | 能 | ✅ **能**（新流程的**唯一 system ID** 让你复用同一份配置） |
| **归属与审计** | 弱 | ✅ **保留了 ownership 记录** |
| **被克隆的 runner** | 会领到同样的 job（**攻击面**） | 有 system ID 区分 |

> **面试这么答**："Runner 注册这块有个坑：**registration token 已经被官方弃用，排期 GitLab 20.0 移除**，GitLab 17.0 起管理员还能直接禁用。所以现在应该走 **UI/API 创建 runner → 拿到 `glrt-` 前缀的 authentication token → `gitlab-runner register --token`** 这条路。**新流程更重要的区别是：runner 的 tag、是否跑无标签任务、是否受保护，这些配置在 UI 里创建 runner 时就定好，注册命令只带 token**——因为 legacy 兼容分支会**忽略掉一批命令行参数**，你以为配了其实没生效。"

### 2.5 runner tag 的匹配机制与"抢同一个 runner"

**匹配规则**：

```
job 的 tags ⊆ runner 的 tag_list   →  这个 runner 可以领这个 job
```

> **这条规则的口径说明**：官方术语表把 tags 定义为"**determine which jobs they can execute**"，官方 `register` 页面也明确 `--tag-list` 与 `--run-untagged` 是 runner 的配置项。**但"job 声明多个 tag 时 runner 必须全部具备"这一子集语义，官方上述页面未逐字展开**——本篇按 GitLab Runner 的通行语义表述，**标 `⚠️ 单源待补`**（见文末待验证清单）。**实践上可以按"runner 要有 job 的全部 tag"来设计，这是最安全的假设。**

**加上两个开关**：

| 开关 | 作用 |
|---|---|
| **`run_untagged`** | runner 是否**也接不带 `tags` 的 job**。带标签的 runner 默认只接标签匹配的 job；`run_untagged = true` 才额外接无标签 job |
| **protected runner** | **只跑受保护分支/tag 上的 job**（不接收不可信分支的代码） |

**"所有 job 抢同一个 runner"这个问题怎么来的**：

**场景**：一个 runner，`tag_list = ["docker"]`，`run_untagged = true`，`concurrent` 很大。

```
所有项目、所有分支、所有 job → 全部命中这个 runner
   ↓
结果：
  ① 队列被别人的项目占满，你的 job 一直 pending
  ② 你的 runner 磁盘/内存被别人的构建搞爆
  ③ 安全问题：别人的代码在你的机器上跑
```

**解法（按有效性排序）**：

| 做法 | 效果 | 说明 |
|---|---|---|
| **① 给 runner 配明确的 tag，job 也用同样的 tag** | ✅ 最有效 | runner 有 `prod` tag → 只有写了 `tags: [prod]` 的 job 才归它 |
| **② `run_untagged = false`** | ✅ 有效 | **带标签的 runner 不再接无标签 job** → 别人随便写的 job 抢不走 |
| **③ 拆 runner 池**（ci / prod / 大镜像构建各一个） | ✅ **生产必备** | 不同用途的资源与安全等级不同 |
| **④ 限制 `concurrent` 与 `limit`** | ⚠️ 缓解 | 限制并发但不解决"抢"的问题 |
| **⑤ 用 protected runner 跑生产 job** | ✅ 安全必备 | **不可信分支的代码跑不到生产 runner 上** |
| **⑥ 按项目/群组建 runner**（project / group runner） | ✅ 有效 | **别的项目根本看不见它** |

**并发控制的三个层次（官方配置）**：

| 配置 | 作用域 | 默认值 | 说明 |
|---|---|---|---|
| **`concurrent`** | **全局**（所有 runner 加起来） | — | **总并发上限**。**配成 `0` 是非法的 → runner 以 critical error 退出** |
| **`limit`** | 单个 `[[runners]]` | **`0` = 不限制** | 这个 runner configuration 最多同时跑几个 job |
| **`request_concurrency`** | 单个 `[[runners]]` | **`1`** | **同时向 GitLab 请求新 job 的数量**。**1 意味着长轮询期间可能有请求瓶颈** → 高流量 runner 要调大 |

**还有一个轮询参数**：

| 配置 | 默认值 | 说明 |
|---|---|---|
| **`check_interval`** | **3 秒** | 请求新 job 的间隔。**多个 `[[runners]]` 共享这个间隔**：两个 runner + `check_interval = 10` → **每 5 秒轮流请求一个** |

> **★ 一个容易踩的性能坑**：**你以为你有 5 个 runner 就是 5 倍吞吐，实际上 `check_interval` 是共享的**。官方原文的意思是：多个 runner configuration 共享同一个轮询间隔，**实际是按 runner 数量均分这个间隔**。所以 **5 个 runner + `check_interval = 10` → 每个 runner 每 10 秒才轮询一次（合计每 2 秒一个请求）**。**runner 数量少 + `check_interval` 大 = 领活延迟高。**
>
> **面试这么答**："Runner 并发有三个层次：`concurrent` 是全局上限、`limit` 是单个 runner 的、`request_concurrency` 是领活请求的并发。另外 `check_interval` 默认 3 秒，但**它在多个 runner configuration 之间是共享的**——所以 runner 少的时候，光调大 `check_interval` 只会让领活更慢；要么加 runner，要么调小间隔。"

### 2.6 缓存机制：key 相同才命中

**缓存的两层结构（必须分清）**：

```
① job 的 cache:key + cache:paths        ← 决定"缓存什么、怎么匹配"
        ↓
② Runner 侧的存储实现                    ← 决定"缓存存在哪"
   ├─ 本地卷缓存（local volume cache）    ← Docker volume 或宿主目录
   └─ 分布式缓存（distributed cache）      ← 上传到远端存储，多 runner 共享
```

**docker executor 的本地缓存机制**：

| 项 | 行为 |
|---|---|
| **容器内路径** | 缓存位于容器内的 **`/cache`** |
| **默认存放位置** | **Docker volume**（若未配置 `cache_dir`） |
| **配了 `cache_dir` 后** | 存到**宿主机的 `cache_dir` 目录下** |
| **命名格式（Runner 18.4.0 起）** | `runner-<runner-id-hash>-cache-<md5-of-path><protection>`；`<protection>` 在受保护分支上是 `-protected`，否则为空 |
| **与 `builds_dir` 的关系** | `/builds` 是工作区，`/cache` 是缓存，**两个目录要各自挂载** |

**关键：`disable_cache` 只关掉本地那层**。官方明确：它**只是阻止创建那个存放构建临时文件的容器**，**不会关闭配置为分布式缓存模式时的缓存**。

**命中规则**：

```
job 开始
  ↓
用 cache:key 去缓存存储里找
  ├─ key 相同 → 下载到 cache:paths 指定的路径（download）
  └─ key 不同 → 没有缓存，直接跑（job 依然成功，只是慢）
  ↓
job 跑完
  ↓
按 cache:policy 决定要不要上传回去（push / pull / pull-push）
```

| `policy` | 下载 | 上传 | 用在哪 |
|---|---|---|---|
| `pull` | ✅ | ❌ | 只读的 job（纯测试） |
| `push` | ❌ | ✅ | 专门预热缓存的 job |
| `pull-push` | ✅ | ✅ | **默认** |

**cache 的三个关键性质**：

| 性质 | 说明 |
|---|---|
| **key 相同就命中，跨流水线也共享** | 官方明确："使用相同 key 的作业共享同一个缓存，**即使位于不同流水线**" |
| **key 与 paths 必须同时写** | **只写 key 不写 paths → 完全不缓存，且不报错**（静默失效） |
| **缓存恢复失败不阻塞 job** | 缓存是加速器，不是必需品 |

**key 策略（本篇最重要的一条）**：

```yaml
# ❌ 太宽：不同分支共用一份缓存 → 缓存投毒
cache:
  key: "maven-cache"
  paths: [.m2/repository]

# ✅ 推荐：key 绑定依赖清单的内容哈希
cache:
  key:
    files:
      - pom.xml
    prefix: $CI_JOB_NAME
  paths: [.m2/repository]
```

**为什么"key 相同就命中"这件事是隐患**：因为**不同分支、不同项目、不同 job 都可能撞上同一个 key**。一旦撞上，**A 的缓存被 B 复用**——这就是 D4 日课记录的"缓存投毒"事故（"本地能跑 CI 不能跑"）。

> **★ 面试高频：CI 里缓存和制品该怎么分工？**
> - **缓存 = 加速**（依赖目录、编译中间产物）。**key 绑定依赖清单哈希**，改依赖才失效。
> - **制品 = 传递**（编译好的二进制、测试报告、SBOM、**镜像本身**）。**只构建一次，下游全部复用**。
> - **两者最关键的区别**：**缓存是"上次的结果给这次用"，制品是"这次的结果给下游用"**。
> - **制品的载体是镜像**（推到 ACR），而不是传 tar 包——因为**镜像有 digest，能被 K8s 引用、能被扫描、能被追溯**。

### 2.7 artifacts 机制：有有效期，会过期

**artifacts 的生命周期**：

```
job 跑完
  ↓
Post-job 阶段：上传 artifacts 到 GitLab 存储
  ↓
GitLab 存着，有 expire_in（默认用实例级设置）
  ↓
到期 → 自动删除（默认按小时粒度清理）
  ↓
下游 job 再来取 → 没了 → 报错
```

**三个关键机制**：

| 机制 | 行为 |
|---|---|
| **上传时机** | 默认 **job 成功才上传**（`artifacts:when` 可改成 `on_failure` / `always`） |
| **报告类制品** | **无论 job 成功失败都上传**（`artifacts:reports` 的特殊待遇） |
| **有效期** | **`expire_in` 未定义时用实例级设置**；可设 `never` 永不删除 |

**下游怎么取**：

| 方式 | 语义 |
|---|---|
| **默认** | 后续 stage 的 job **自动下载所有前面 stage 的 artifacts** |
| **`needs`** | **只下载 `needs` 里列出的 job 的 artifacts** |
| **`dependencies`** | 只下载 `dependencies` 里列出的 job 的 artifacts（**不控制执行顺序**） |

> **官方明确：不要在同一个 job 里同时用 `needs` 和 `dependencies`。**

**官方排障条目（原文）**：job 启动失败并返回 `This job could not start because it could not retrieve the needed artifacts`，可能原因包括：**依赖的 artifacts 不存在 / artifacts 已过期 / 权限不足**。

**"artifacts 会过期"这件事的工程后果**：

| 场景 | 后果 |
|---|---|
| **长流水线跨越较长时间** | 上游的 artifacts 可能在下游跑之前就过期了 |
| **失败后隔天重跑** | 昨天的 artifacts 已经删了 → 今天的 job 拿不到 |
| **把 artifacts 当"长期存档"** | ❌ **artifacts 不是归档系统**，有有效期就该有归档方案 |

> **★ 这条是本篇最值钱的一条经验**：
> **镜像产物不要用 artifacts 传，用镜像仓库传。**
> 理由有三条，每条都能落到具体后果：
> 1. **artifacts 会过期** → 跨天的重跑会拿不到
> 2. **artifacts 传的是"文件"** → 下游要自己认文件名、验完整性
> 3. **镜像有 digest** → 下游引用 `@sha256:...` 是**不可变**的，**还能被扫描（Trivy）、能验签（cosign）、能被追溯（label）**
>
> **面试这么答**："我们把镜像当制品、用镜像仓库传，而不是用 GitLab artifacts 传 tar 包。原因有三个：artifacts 有有效期会过期；镜像有 digest 是不可变引用，而 tar 包没有版本概念只能靠文件名；更关键的是**镜像能被 Trivy 扫描、能被 cosign 验签、能带 label 追溯**——这三个能力 artifacts 给不了。**artifacts 我们只用来传测试报告和覆盖率这类"给人和给面板看"的产物。**"

### 2.8 DinD vs Docker socket：安全与速度的取舍

**先说清楚两种做法在做什么**：

```
方式 A：DinD（Docker-in-Docker）
─────────────────────────────────────────────
job 容器里跑 docker CLI
        │ 通过 TCP（TLS）连到同 Pod/同网络里的 dind 容器
        ▼
   dind 容器里的 Docker daemon  ← 这个 daemon 是"job 自己的"
        │
        ▼
   dind 容器自己的存储

方式 B：挂载 Docker socket
─────────────────────────────────────────────
job 容器里跑 docker CLI
        │ 通过 /var/run/docker.sock 连到宿主机的 daemon
        ▼
   宿主机的 Docker daemon  ← 这个 daemon 是"宿主的"
        │
        ▼
   宿主机的镜像与容器

⚠️ 关键：在 Docker executor 下，方式 B 的"宿主机"= runner 所在的机器
   在 Kubernetes executor 下，方式 B 的"宿主机"= runner Pod 所在节点的 Docker daemon
```

| 维度 | **DinD** | **Docker socket 挂载** |
|---|---|---|
| **daemon 归属** | **job 自己的**（容器内嵌套） | **宿主的**（共享） |
| **镜像存储** | dind 容器内（**用完即弃**） | **宿主机的镜像库**（**跨 job 累积**） |
| **速度** | ❌ **慢**（多一层 daemon + 网络往返 + 存储层） | ✅ **快**（直连本地 socket，镜像已在本地） |
| **资源占用** | ❌ **高**（多跑一个 daemon 进程） | ✅ 低 |
| **镜像拉取次数** | 每次 job **从零开始拉** | ✅ **本地已有就不拉**（`if-not-present`） |
| **隔离性** | ✅ **好**（job 看不到别人的镜像与容器） | ❌ **差**（能看到并操作宿主机上所有容器与镜像） |
| **权限要求** | **`privileged = true`**（dind 需要） | 需要能访问 socket（**通常也是 `privileged` 或挂宿主 socket**） |
| **逃逸风险** | ✅ 较低（**破坏 dind 容器 ≠ 破坏宿主**） | ❌ **高**（**拿到 daemon 就等于拿到宿主 root**） |
| **国内拉镜像** | ❌ **每次都要重新拉 → 极慢**（除非给 dind 配加速器） | ✅ 拉过一次就复用 |

**官方对 privileged 的定性（必须背）**：

> "Privileged containers in Docker have all the root capabilities of the host VM."
> "It is **not advised** to run containers in privileged mode, or with the `--pid=host` flag."
> "When privileged mode is enabled, a user running a CI/CD job could gain full root access to the runner's host system, permission to mount and detach volumes, and run nested containers."

**官方给的加固方案（这是加分项）**：

```toml
[runners.docker]
  services_privileged = true
  allowed_privileged_services = [
    "docker.io/library/docker:*-dind-rootless",
    "docker.io/library/docker:dind-rootless",
    "docker:*-dind-rootless",
    "docker:dind-rootless"
  ]
```

**然后在 CI 里用 rootless DinD**：

```yaml
image: docker:git
services:
  - docker:dind-rootless
```

> **这个方案的价值**：**只有白名单里的 rootless DinD service 能用 privileged，其他所有 job 和 service 都以非特权模式运行**。**这比"整个 runner 开 privileged"安全一个数量级。**
> 官方的另一条配套建议：**只允许 job 跑在受保护分支上**（`access-level = ref_protected`）。

**为什么 DinD 在 K8s runner 上常起不来**：DinD 需要 `privileged: true`，而**生产集群的策略（PSP / PSA / OPA）通常禁止特权容器**。这不是配置问题，是**安全策略问题**——解法是换构建方式（Kaniko / BuildKit rootless / 有特权的小集群），而不是去申请放开生产集群的特权。

**选型结论**：

| 场景 | 选什么 | 为什么 |
|---|---|---|
| **自己信任的代码、要快** | **socket 挂载** | 快、镜像复用多、配置简单 |
| **跑不可信代码 / 多人共用 runner** | **DinD（rootless）** | 隔离 |
| **既要快又要安全** | **socket 挂载 + `allowed_images` 白名单 + `allowed_services` 白名单 + dedicated runner** | **把"能做什么"限制在白名单里** |
| **K8s 集群上的 runner** | **socket 挂载（挂节点 socket）或 Kaniko** | DinD 要特权，**生产集群一般不给** |

### 2.9 标准流水线：build → test → scan → push → deploy

**顺序不是随意的，每一步的依赖关系都有理由**：

```
build ──┬─→ test ──┐
        │          │
        └─→ scan ──┴─→ push ──→ deploy
```

| 阶段 | 干什么 | 为什么在这个位置 | 失败意味着 | 产物 |
|---|---|---|---|---|
| **build** | 构建镜像（**不推送**） | 最先做，产出后续一切依赖的东西 | **代码或构建配置有问题** | 本地镜像（**只在 runner 上**） |
| **test** | 跑单测/集成测试 | **与 scan 并行**（互不依赖） | **功能有问题** | 测试报告（junit） |
| **scan** | Trivy 漏洞扫描 + 配置扫描 | **与 test 并行**；**必须在 push 之前** | **有漏洞 / 配置不安全** | 扫描结果 + SBOM |
| **push** | 推送镜像到仓库 | **必须等 test 和 scan 都过** | **推送失败**（凭据/网络/权限） | **镜像（在仓库里，带 digest）** |
| **deploy** | 更新 K8s Deployment | **最后**，且生产要人工点 | **部署失败**（kubectl/权限/清单错误） | 部署记录（environment） |

**三条设计原则**：

| 原则 | 理由 |
|---|---|
| **① push 必须在 scan 之后** | **不能让有高危漏洞的镜像进入仓库**——进了仓库就可能被别的项目拉走 |
| **② build 只构建一次，后续复用同一份** | ① 避免重复构建（**时间**）；② **保证"测的就是发的"**（**一致性**） |
| **③ deploy 用 digest 而不是 tag** | 接上 L3 的"不可变"原则——**tag 会被覆盖** |

**★ 为什么"只构建一次"这么重要**（D4 日课场景 B 第 1 条的核心）：

```
❌ 每个阶段各构建一次：
   test 阶段构建一次 → scan 阶段又构建一次 → push 阶段再构建一次
   → 三次构建可能产出三个不同的镜像（时间戳、依赖漂移都可能造成差异）
   → "我扫的是 A，发的是 B" —— 门禁形同虚设

✅ 构建一次，之后传递"同一个东西"：
   build → 得到 digest
   test / scan 都用这个 digest
   push 推的就是这个 digest
   deploy 引用这个 digest
```

> **这正是"制品不可变"这句话的工程含义**：**中间阶段传递的必须是 digest，不是"再构建一次"。**

**每个阶段的失败含义（面试要能一条条说清）**：

| 阶段失败 | 真实含义 | 该找谁 |
|---|---|---|
| **build 失败** | 代码编译不过 / Dockerfile 写错 / 依赖拉不到 | 写代码的人 |
| **test 失败** | 功能回归 | 写代码的人 |
| **scan 失败** | **引入了有 CVE 的依赖，或配置不安全** | 写代码的人（但**可能是基线没定好**，见 L1 篇踩坑表） |
| **push 失败** | **凭据过期 / 网络不通 / 仓库权限不足** | **运维 / 平台团队**（**不是代码问题**） |
| **deploy 失败** | kubectl 报错 / 清单错误 / 集群权限不足 | 运维 / 平台团队 |

> **★ 这张表是"运维岗面试高频"的核心**：
> **"流水线红了，是谁的锅？"** 这个问题考的不是会不会写 YAML，是**你懂不懂每一环的失败语义**。
> **推不动镜像 ≠ 代码有问题**——push 失败 90% 是**凭据过期**或**网络问题**，这属于运维责任范围。
> **面试这么答**："我们把流水线按失败归属分了三段：**build/test/scan 失败是研发的锅**（代码或依赖）；**push/deploy 失败是运维的锅**（凭据、网络、集群权限）。分清这个，值班时才知道该找谁——**push 失败去翻代码是浪费时间**。"

### 2.10 CI 里的 buildx 多架构与缓存

**多架构构建在 CI 里的特殊难点**：

| 难点 | 原因 | 解法 |
|---|---|---|
| **构建极慢** | 每个架构都要完整走一遍编译；**arm64 在 x86 机器上要模拟执行** | 用原生 arm64 runner（**kubernetes executor 有天然优势**——集群里什么架构都有） |
| **拉基础镜像慢** | 每个架构的基础镜像都要单独拉；**国内直连 Docker Hub 极慢** | **配 `registry-mirrors`**（本机实测拉 alpine 超时就是这个场景，见 L3 篇 2.3） |
| **层缓存不共享** | 不同架构的层 digest 不同 → **cache key 不能跨架构复用** | 用 `cache-to`/`cache-from` 按架构分开存 |
| **多架构产物不能 load 到本地** | 本地镜像存储一次只能有一种平台 | **CI 里必须 `--push`** |

**CI 里的多架构构建片段**：

```yaml
build:
  stage: build
  script:
    - docker buildx create --use --name multiarch
    - docker buildx build
        --platform linux/amd64,linux/arm64
        --provenance=false --sbom=false
        --cache-from type=registry,ref=$IMAGE_NAME:buildcache-amd64
        --cache-to   type=registry,ref=$IMAGE_NAME:buildcache-amd64,mode=max
        -t "$IMAGE_NAME:$CI_COMMIT_SHORT_SHA" --push .
```

**三个参数的用意**：

| 参数 | 用意 |
|---|---|
| **`--provenance=false --sbom=false`** | **收敛多架构索引的结构**，避免旧 registry / 旧 containerd 解析不了 attestation 条目（见 L3 篇 2.5） |
| **`--cache-from` / `--cache-to`** | **把构建层缓存推到 registry**，下个 job / 下个架构能复用 → **这是 CI 里 buildx 提速的关键** |
| **`mode=max`** | 缓存**所有中间层**（不只是最后一层）→ **缓存命中后改代码只需重跑最后一层** |

> **注意 `--cache-to type=registry` 需要目标仓库支持 OCI 制品推送**。写本篇时**未做实测**（不执行任何推送动作），**落地前需在目标 ACR 上验证一次**。

### 2.11 产物可追溯：tag 用 commit SHA 而不是 `latest`

**`latest` 的三个致命问题**：

| 问题 | 后果 |
|---|---|
| **可被覆盖** | 同名再 push 即改写 → **线上跑的可能是任何人推的内容** |
| **不表达版本** | 三个不同时间的镜像都叫 `latest` → **无法区分、无法回滚** |
| **不与源码绑定** | 看到 `latest` **不知道对应哪次提交** → **审计断链** |

**正解：tag 用 commit SHA**：

```bash
IMAGE_TAG="$CI_COMMIT_SHORT_SHA"     # 例如 a1b2c3d4
docker build -t "$IMAGE_NAME:$IMAGE_TAG" .
docker push "$IMAGE_NAME:$IMAGE_TAG"
```

**这样得到三个能力**：

| 能力 | 怎么来的 |
|---|---|
| **可追溯** | tag 就是 commit SHA → **反查源码 commit 是 O(1)** |
| **可回滚** | 回滚就是把 Deployment 的 image 改回上一个 SHA |
| **不可被意外覆盖** | **要覆盖必须真的推同名 SHA**（需要显式操作，且会留审计记录） |

**再往上加一层：生产用 digest**。

| 层级 | 写法 | 人可读 | 不可变 | 用在哪 |
|---|---|---|---|---|
| **tag** | `$IMAGE:$(git rev-parse --short HEAD)` | ✅ | ❌ | **CI 内部传递、构建缓存** |
| **digest** | `$IMAGE@sha256:<64位>` | ❌ | ✅ | **生产部署** |

> **这两层不是二选一，是分工**（详见 L3 篇 2.4 与 4.2）：
> **tag 给人和给日志看，digest 给机器和生产用。**

**再加三个 label，让可追溯落到镜像内部**：

```bash
docker build \
  --label "org.opencontainers.image.source=https://gitee.com/<仓库地址>" \
  --label "org.opencontainers.image.revision=$CI_COMMIT_SHA" \
  --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t "$IMAGE_NAME:$IMAGE_TAG" .
```

> **用 OCI 标准 label 而不是自定义 key**——**别的工具（Trivy、Scout、GitOps）才能自动识别**。这是"可追溯"从口号变成机器可读事实的关键一步。

---

## 3. 动手：在 CI 里构建并推送镜像

> **本节给的是"设计 + 关键片段"**。写本篇时**没有执行任何 gitlab-runner 命令、没有登录或推送任何仓库、没有创建任何容器或集群资源**。
> **注意**：涉及真实推送的步骤请在自己的环境按"看到什么算对"逐条验证。

### 3.1 准备：注册一个 runner（认证 token 流程）

```bash
# ① 先在 GitLab UI 建 runner：Settings → CI/CD → Runners → New project runner
#    勾选 Tags（假设填 ci、docker、linux）、勾选 "Run untagged jobs" = 不勾
#    勾选 "Protected" = 生产 runner 才勾
#    创建后复制 authentication token（glrt- 开头）

# ② 注册（非交互）
gitlab-runner register \
  --non-interactive \
  --url "https://<你的GitLab地址>" \
  --token "glrt-<你的token>" \
  --executor "docker" \
  --docker-image "docker:27-cli" \
  --docker-pull-policy "if-not-present" \
  --description "ci-docker-runner"

# ③ 看注册结果（config.toml）
cat /etc/gitlab-runner/config.toml
```

**看到什么算对**：
- ② **不报 `410 Gone`** → 说明用的是新令牌且实例没禁用旧流程
- ③ `config.toml` 里有 `[[runners]]` 段，`token` 是一串 `glrt-...`
- **UI 的 Runners 页面里这个 runner 状态是 online**（说明长轮询连上了）

> **★ 关键检查点**：用 `--token`（authentication token）注册时，**tag、run_untagged、protected 这些都在 UI 里配好了**。**如果你在命令行里传了 `--tag-list` 之类，legacy 兼容分支会忽略它们**——**这是"配了但不生效"的经典坑。**

### 3.2 配置 tag（避免"抢同一个 runner"）

```toml
# /etc/gitlab-runner/config.toml
concurrent = 4          # 全局并发上限（不能是 0，否则 runner 以 critical error 退出）

[[runners]]
  name = "ci-docker-runner"
  url = "https://<你的GitLab地址>"
  token = "glrt-<你的token>"
  executor = "docker"
  tag_list = ["ci", "docker", "linux"]
  run_untagged = false     # ← 不接无标签 job，避免被别人抢
  limit = 2                # ← 单 runner 最多同时跑 2 个
  request_concurrency = 2  # ← 同时领活的请求数（默认 1，高流量要调大）

  [runners.docker]
    image = "docker:27-cli"
    pull_policy = "if-not-present"
    privileged = false
    cache_dir = "/var/cache/gitlab-runner"
    volumes = ["/cache"]
```

**对应的 job**：

```yaml
build:
  stage: build
  tags: [docker, linux]     # ← 与 runner 的 tag_list 有交集即可
  script:
    - docker version
```

**看到什么算对**：
- **tag 不匹配时**：job 一直 pending（见 6.1）
- **tag 匹配时**：秒级开始跑

### 3.3 标准流水线（build → test → scan → push → deploy）

```yaml
stages: [build, test, scan, publish, deploy]

variables:
  REGISTRY: "registry.cn-hangzhou.aliyuncs.com"
  IMAGE_NAME: "$REGISTRY/linux321-devops/day1-go-demo"
  IMAGE_TAG: "$CI_COMMIT_SHORT_SHA"          # ← 不用 latest

default:
  tags: [docker, linux]
  before_script:
    - echo "job=$CI_JOB_NAME commit=$CI_COMMIT_SHA"

# ---- ① build：只构建，不推送 ----
build:
  stage: build
  image: docker:27-cli
  services:
    - name: docker:27-dind
      alias: docker
  script:
    - docker buildx create --use --name multiarch
    - docker buildx build
        --platform linux/amd64,linux/arm64
        --provenance=false --sbom=false
        --cache-from type=registry,ref=$IMAGE_NAME:buildcache-amd64
        --cache-to   type=registry,ref=$IMAGE_NAME:buildcache-amd64,mode=max
        -t "$IMAGE_NAME:$IMAGE_TAG" --push .
    # 把 digest 传下去（dotenv 通道）
    - DIGEST=$(docker buildx imagetools inspect "$IMAGE_NAME:$IMAGE_TAG" | head -1)
    - echo "IMAGE_DIGEST=$DIGEST" >> build.env
  artifacts:
    reports:
      dotenv: build.env
    expire_in: 1 day

# ---- ② test：与 scan 并行 ----
unit-test:
  stage: test
  image: maven:3.9-eclipse-temurin-17
  needs: [build]
  script:
    - echo "单测占位"
  artifacts:
    reports:
      junit: target/surefire-reports/TEST-*.xml
    expire_in: 1 week

# ---- ③ scan：门禁，退出码非 0 就红 ----
scan:
  stage: scan
  image: aquasec/trivy:latest
  needs: [build]
  script:
    - trivy image --severity HIGH,CRITICAL --exit-code 1 "$IMAGE_NAME:$IMAGE_TAG"
    - trivy config --exit-code 1 ./k8s

# ---- ④ push：只在主干/tag，且用 digest 引用 ----
publish:
  stage: publish
  needs: [build, unit-test, scan]
  script:
    - echo "$ACR_PASS" | docker login --password-stdin -u "$ACR_USER" "$REGISTRY"
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
    - if: $CI_COMMIT_TAG

# ---- ⑤ deploy：dev 自动，prod 手动 ----
deploy-prod:
  stage: deploy
  image: bitnami/kubectl:latest
  needs: [publish]
  script:
    - kubectl set image deployment/day1-go-demo
        app=$IMAGE_NAME@$IMAGE_DIGEST --namespace prod
  environment:
    name: prod
  when: manual
  rules:
    - if: $CI_COMMIT_TAG
```

**每一步"看到什么算对"**：

| 阶段 | 预期现象 |
|---|---|
| **build** | 日志里出现 `docker buildx build` 的多架构输出；**`--push` 的 digest 出现**；`build.env` 生成 |
| **unit-test** | 与 scan **同时开始**（不是排队）→ 证明 `needs` 的 DAG 生效 |
| **scan** | 漏洞多时**退出码非 0、job 红** |
| **publish** | 只在主干/tag 流水线出现；**`docker login` 不报 unauthorized** |
| **deploy-prod** | 流水线**停住等人点**；点完后 `kubectl set image` 用的是 `@sha256:` 引用 |

> **注意 build 这一步已经 `--push` 了** —— 这是一个**刻意的简化**（把构建和推送合并），目的是让 scan 能扫到**真正要推的那个镜像**。
> **严格做法是：build 不推，scan 扫完再 push。** 两种都可以，但**必须保证"扫的和发的是同一个 digest"**。

### 3.4 用 cache 加速（验证 key 策略）

```yaml
build:
  stage: build
  cache:
    key:
      files:
        - pom.xml
      prefix: $CI_JOB_NAME
    paths:
      - .m2/repository
    policy: pull-push
  script:
    - mvn -v
```

**验证 key 策略是否正确（三个实验）**：

| 实验 | 操作 | 预期日志 |
|---|---|---|
| **① 首次** | 第一次跑 | **没有 "Restoring cache"**（本来就没缓存），跑完日志里有 "Successfully extracted cache" |
| **② 不改依赖** | 再跑一次 | **有 "Restoring cache"** → 命中 |
| **③ 改依赖** | 改一下 `pom.xml` 再跑 | **没有 "Restoring cache"** → **key 变了**（这正是我们要的） |

> **实验 ③ 是关键**：如果改了 `pom.xml` **还能命中缓存**，说明你的 key 没绑定依赖清单 → **缓存投毒风险**。

---

## 3.5 常用运维命令面板（按场景）

> **本节能做什么**：3.3 是"一条标准构建流水线"，本节是**每天值班 / 面试都会敲的构建与制品运维命令**——Runner 排查、Docker 构建、Maven 多模块、Harbor 清理都在这。**CI 构建侧的肌肉记忆就来自这一张表。**

### ① Runner 注册与节点离线处理

```bash
gitlab-runner register                          # 交互注册（URL / token / executor / tag）
gitlab-runner verify                            # 校验 runner 能连 GitLab（心跳）
gitlab-runner status                            # 进程在不在、是否 active
gitlab-runner list                              # 本机所有 runner 及状态
sudo gitlab-runner restart                      # 节点抽风先重启服务
# Docker executor 的 job 容器卡住时清场：
docker ps -a | grep runner-                     # 看 runner 拉起来的临时构建容器
docker rm -f $(docker ps -a -q -f name=runner-) # 强清卡死的构建容器（慎用）
```

> **实战价值**：`verify` 通说明 runner↔GitLab 链路正常，问题在 job；不通就是 **token 过期 / 网络 / 服务挂**。`docker rm -f` 清的是卡死的构建容器——当 job 异常退出没清理时，这些容器会吃满磁盘，是"runner 节点磁盘爆"的常见元凶。

### ② Docker 镜像构建运维

```bash
docker build -t registry/app:$(git rev-parse --short HEAD) .   # 用 commit SHA 打 tag（可追溯，别用 latest）
docker build --target runtime -t app:prod .                    # 多阶段构建：只取最终阶段
docker build --cache-from app:prev -t app:new .                # 拉上一版镜像当缓存层，加速 CI 构建
docker pull registry/app:tag                                   # 拉镜像
docker push registry/app:tag                                   # 推镜像
docker images prune -f                                         # 清悬空镜像（<none>）
docker builder prune -f                                        # 清构建缓存（buildkit cache）
docker system df                                               # 看镜像/容器/缓存各占多少空间
```

> **实战价值**：**多阶段构建是面试必考点**——把编译环境和运行环境分开，最终镜像只有 runtime 层，体积能小一个数量级。`docker system df` 是判断"节点磁盘是不是被镜像堆爆"的第一步；CI 节点要定时 `prune`，否则跑几十条流水线就满盘。

### ③ Maven 多模块构建

```bash
mvn clean package                              # 全量构建+打包（最常用）
mvn clean package -pl user-service -am         # 只构建 user-service 及其依赖模块（-am = also make）
mvn clean package -DskipTests                  # 跳过测试（CI 里测试另起 job 时省时间）
mvn dependency:tree                            # 看依赖树，定位依赖冲突/重复引入
mvn -o clean package                           # 离线模式（构建机无外网或代理故障时）
mvn help:effective-pom                         # 看最终生效的 pom（被 parent/profile 覆盖后长啥样）
```

> **实战价值**：**`-pl -am` 是多模块项目的救星**——只构建改动的那一个模块及其上游依赖，CI 时间从几十分钟降到几分钟。`dependency:tree` 定位 `NoSuchMethodError` / 版本冲突的根因（同一个包被两个版本引入）。**面试常问"大仓里怎么只构建改动的模块"。**

### ④ 制品仓库 Harbor（登录 / 打标 / 清理）

```bash
docker login registry.example.com -u <user>    # 登录 Harbor
docker logout registry.example.com             # 退出（CI 结束要 logout，别留凭据）
docker tag app:local registry.example.com/lib/app:1.2.3   # 打 Harbor 全路径 tag
docker push registry.example.com/lib/app:1.2.3            # 推到项目仓库
# 清理策略（Harbor 自身不会自动删旧镜像）：
# 网页 → 项目 → 标签保留策略：按数量/天数保留最近 N 个，老的自动清理
# 或手动删：网页勾选过期 tag；底层再跑 GC 回收空间
docker exec -it harbor-core gc                                  # Harbor 垃圾回收（删未被引用的层）
```

> **实战价值**：**Harbor 不会因为你 push 了新镜像就自动删旧的**——不配保留策略，registry 磁盘迟早爆。标签用 **语义化版本 + commit SHA**，配合 Harbor 的"标签保留策略"做自动清理，是生产标配。`gc` 是回收被删 tag 后真正占着的存储层，删了 tag 不 gc 空间不释放。

### ⑤ Jenkins（另一主流 CI 引擎的运维命令）

```bash
java -jar jenkins-cli.jar -s http://jenkins:8080/ help        # 看 CLI 支持哪些命令
java -jar jenkins-cli.jar -s http://jenkins:8080/ list-jobs   # 列出所有 job
java -jar jenkins-cli.jar -s http://jenkins:8080/ console <job> # 拿某次构建的控制台输出
# Groovy 控制台（网页 → Manage Jenkins → Script Console）做批量运维：
#   println Jenkins.instance.nodes*.name          # 列出所有节点
#   Jenkins.instance.getNode("agent-1").toComputer().offline  # 看节点是否离线
tail -f /var/log/jenkins/jenkins.log                            # 排障第一现场
```

> **实战价值**：Jenkins CLI + Groovy console 是**不用点网页就能批量管 job 和节点**的利器——比如批量改 job 配置、下线故障节点。`/var/log/jenkins/jenkins.log` 是插件报错、节点掉线的第一现场；**节点离线**先看日志里是不是 agent 连不上 master（JNLP 端口/凭据），再看 agent 机器本身是不是磁盘满/进程死。

---

## 3.6 构建与制品排障实战（3 个场景串命令）

> **把 3.5 的命令用起来**：三个最典型的构建侧故障，按"现象 → 敲的命令 → 怎么读"走一遍。**在测试 runner / 测试仓库上练。**

### 场景 A：runner 节点离线，job 全 pending

```bash
gitlab-runner status                            # 现象：runner 进程不在 / 非 active
gitlab-runner verify                            # 能连 GitLab 吗？报错=token/网络
sudo systemctl restart gitlab-runner            # 重启服务
docker system df                                # 顺手看是不是磁盘爆导致 runner 起不来
```

> **读法**：`verify` 不通先重启服务；重启还不行看磁盘（`docker system df`）——**构建容器没清会吃满盘，runner 直接起不来**。Jenkins 侧则看 `/var/log/jenkins/jenkins.log` 里 agent 掉线的原因。

### 场景 B：镜像越堆越多，节点磁盘 100%

```bash
docker system df                                # 现象：镜像/缓存占用爆表
docker images prune -f                          # 清悬空镜像
docker builder prune -f                         # 清 buildkit 构建缓存
# 查是谁在产镜像：
docker images --format '{{.Repository}} {{.Size}}' | sort -k2 -h
```

> **读法**：CI 节点必须**定时 prune**（写进 cron 或放在流水线收尾）。`docker system df` 先确认是镜像还是缓存占大头，再针对性清；清完不释放空间还要查是不是有容器还引用着这些层。

### 场景 C：多模块大仓构建太慢 / 依赖冲突

```bash
mvn clean package -pl user-service -am -DskipTests   # 只构建改动模块+依赖
mvn dependency:tree | grep conflict                 # 看冲突的依赖
mvn help:effective-pom > eff.xml                    # 导出最终 pom 排查被覆盖的配置
```

> **读法**：全量构建慢 → 用 `-pl -am` 缩小范围；`NoSuchMethodError` 类报错 → `dependency:tree` 找同包多版本；CI 里测试另起 job 时加 `-DskipTests` 省一遍测试时间。

> **三个场景的共同心法**：**先 `status`/`verify`/`df` 看清"是节点挂了还是磁盘爆了还是构建本身慢" → 再对症下药（重启 / prune / 缩小构建范围）→ 最后才是改流水线配置。**

---

## 4. 逐字段表

### 4.1 runner 关键配置表

> **本表覆盖 `config.toml` 里最常改的几个配置**，作用域分为"全局"、"单个 `[[runners]]`"、"`[runners.docker]`"三档。

| 配置项 | 类型 | 作用域 | 作用 | 写错的后果 |
|---|---|---|---|---|
| **`executor`** | 字符串 | `[[runners]]` | **决定 job 在哪跑**：`shell` / `docker` / `kubernetes` / `ssh` / `docker+machine` / `custom` | 写成 `dockers` 之类 → **runner 启动失败**；用 `shell` 跑不可信代码 → **能偷其他项目源码** |
| **`tag_list`** | 数组 | `[[runners]]` | **这个 runner 认哪些 tag**，job 的 `tags` 与它有交集才会被分配 | 写错 → **job 一直 pending**（没人领） |
| **`run_untagged`** | 布尔 | `[[runners]]` | **是否也接不带 `tags` 的 job** | 默认为 `true` 的话 → **别人的无标签 job 会来抢你的 runner** |
| **`concurrent`** | 整数 | **全局** | 所有 runner 加起来的**总并发上限** | **配成 `0` → runner 以 critical error 退出**；配太小 → 排队久 |
| **`limit`** | 整数 | `[[runners]]` | **这个 runner 最多同时跑几个 job** | 默认 `0` = **不限制**；不限制 → 一个大项目把 runner 占满 |
| **`request_concurrency`** | 整数 | `[[runners]]` | **同时向 GitLab 请求新 job 的数量** | 默认 `1` → **长轮询期间有领活瓶颈**；高流量场景要调大 |
| **`check_interval`** | 秒 | 全局 | 请求新 job 的间隔，**默认 3 秒** | 调大 → **领活延迟高**；**多 runner 共享这个间隔**，runner 少时更要小心 |
| **`token`** | 字符串 | `[[runners]]` | **runner authentication token（`glrt-` 前缀）**；**也支持 `$VAR` 展开** | 用了旧的 registration token 且实例已禁用 → **报 `410 Gone - runner registration disallowed`** |
| **`url`** | URL | `[[runners]]` | GitLab 实例地址；**支持 `$VAR` 展开** | **写成 `https://x/ci`（带 `/ci` 后缀）** → 可能干扰 Git URL 改写，submodule 认证会失败 |
| **`environment`** | 数组 | `[[runners]]` | **追加或覆盖环境变量**（给所有 job） | 变量名写错 → **覆盖掉 runner 自己的关键变量** |
| **`image`** | 字符串 | `[runners.docker]` | **默认的 build 镜像**（`.gitlab-ci.yml` 没写 `image` 时用这个） | 镜像里没有你要的工具 → `command not found` |
| **`volumes`** | 数组 | `[runners.docker]` | **挂载到 build 容器的卷**；**`/cache` 必须在里面，否则缓存不持久** | 漏了 `/cache` → **缓存每次都丢**（跨 job 不复用） |
| **`cache_dir`** | 路径 | `[runners.docker]` | **缓存存在宿主机的哪个目录下** | 配了 `cache_dir` 但**没在 `volumes` 里挂 `/cache`** → 缓存写不到那儿 |
| **`privileged`** | 布尔 | `[runners.docker]` | **是否给容器特权**（DinD 需要） | **默认 `false`；开成 `true` → job 拿到宿主 root 权限** |
| **`allowed_images`** | 数组 | `[runners.docker]` | **允许 job 使用的镜像白名单** | 不配 = **允许所有镜像**（`["**"]`）；配了但写错 → job 启动失败 |
| **`allowed_services`** | 数组 | `[runners.docker]` | **允许的 service 镜像白名单** | 同上 |
| **`allowed_privileged_services`** | 数组 | `[runners.docker]` | **允许 privileged 的 service 白名单** | 不配 → **任何 service 都能特权运行**；配了 rootless DinD 白名单 → **安全得多** |
| **`services_privileged`** | 布尔 | `[runners.docker]` | service 是否继承 `privileged` | 不配则继承 → **`privileged = true` 会同时给 build 和所有 service** |
| **`pull_policy`** | 字符串/数组 | `[runners.docker]` | **建容器前是否重新拉镜像**：`always`（默认）/ `if-not-present` / `never` | 用 `if-not-present` 且 runner 多人共用 → **别人能用到你的私有镜像**（官方明确警告） |
| **`allowed_pull_policies`** | 数组 | `[runners.docker]` | **允许 job 指定的拉取策略** | 两个列表无交集 → **job 失败** |
| **`namespace`** | 字符串 | `[runners.kubernetes]` | **job Pod 建在哪个 namespace** | 写错 → **Pod 创建失败**；权限不足 → 同样失败 |
| **`namespace_per_job`** | 布尔 | `[runners.kubernetes]` | **每个 job 一个独立 namespace** | 开启后**必须给 ClusterRole + ClusterRoleBinding**（namespace 级 Role 不够） |
| **`cpu_limit` / `memory_limit`** | 字符串 | `[runners.kubernetes]` | **build 容器的资源上限** | 不配 → **job 可以吃满节点资源** |
| **`helper_memory_limit`** | 字符串 | `[runners.kubernetes]` | **helper 容器的内存上限** | **官方建议有缓存和 artifacts 的负载至少 `250Mi`** → 给太小 → **上传缓存/制品时 OOM** |
| **`output_limit`** | 整数 | `[[runners]]` | **构建日志大小上限（KB）**，默认 **4096** | 日志超限 → **日志被截断**，排查时看不到关键报错 |
| **`builds_dir`** | 路径 | `[[runners]]` | **job 工作区的宿主机根目录** | 磁盘满了 → **所有 job 失败** |

### 4.2 job 阶段表

> **本表描述"一条云原生 CI 流水线"的典型阶段**，每一步的失败归属是面试重点（见 2.9）。

| 阶段 | 干什么 | 依赖谁 | 失败意味着 | 产物 | 归属 | 写错的后果 |
|---|---|---|---|---|---|---|
| **lint / 静态检查** | 代码规范、敏感信息扫描 | **无**（`needs: []`） | 代码风格或规范问题（**通常 `allow_failure: true`**） | 报告 | 研发 | 不写 `needs: []` → **要等 build 完才跑，失去"秒级反馈"的意义** |
| **build** | 构建镜像（多架构 + 层缓存） | 无 | **编译失败 / Dockerfile 错 / 依赖拉不到** | **镜像 + digest** | 研发 | **tag 用 `latest`** → 后面全部环节拿到的都是可变指针，**无法回滚** |
| **test** | 单测 / 集成测试 | build（并行于 scan） | **功能回归** | **junit 报告 + 覆盖率** | 研发 | `reports:junit` 路径写错 → **面板空白但不报错**（最难查） |
| **scan** | Trivy 漏洞扫描 + 配置扫描 | build（并行于 test） | **引入有 CVE 的依赖，或配置不安全** | 扫描结果 + **SBOM** | 研发（**但可能是基线没定好**） | **漏 `--exit-code 1`** → **有漏洞但流水线照样绿**（门禁形同虚设） |
| **publish** | 推送到镜像仓库 | build + test + scan | **凭据过期 / 网络不通 / 仓库权限不足** | **镜像（在仓库里，带 digest）** | **运维 / 平台** | **放在 scan 之前** → **高危镜像进了仓库**，可能被别的项目拉走 |
| **deploy-dev** | 更新 dev 环境 | publish | kubectl 报错 / 清单错误 | 部署记录（environment=dev） | 运维 | **用 tag 而不是 digest** → 同一 Deployment 的不同 Pod **可能跑着不同代码且不报错** |
| **deploy-prod** | 更新生产环境 | publish | 同上 | 部署记录（environment=prod） | 运维（**且需人工审批**） | **漏 `when: manual`** → **MR 合并即发生产**（误发布事故） |
| **cleanup** | 清理临时资源 | — | — | — | 运维（**`when: always` 保证一定跑**） | 不写 `when: always` → **前面任一 job 失败，清理就不跑**（临时资源堆积） |

**三条读表要点**：

| 要点 | 说明 |
|---|---|
| **前三个阶段是研发的锅，后两个是运维的锅** | **"流水线红了找谁"** 是面试高频。**push 失败去翻代码是浪费时间** |
| **scan 与 test 可以并行** | 它们互不依赖 → **用 `needs` 表达**，这是 DAG 化的直接收益 |
| **publish 必须等 scan** | **不能让高危镜像进仓库**——进了仓库就可能被别的项目拉走 |

---

## 5. 横向对比

### 5.1 Shell vs Docker vs Kubernetes executor

| 维度 | **`shell`** | **`docker`** | **`kubernetes`** |
|---|---|---|---|
| **隔离单元** | ❌ **无隔离**，直接跑在 runner 主机上 | ✅ 一个**容器** | ✅ 一个 **Pod**（更接近生产） |
| **作业权限** | **runner 用户的权限**（很高） | 容器内用户（可设非 root） | Pod 的 securityContext + **ServiceAccount** |
| **能读到别的项目代码吗** | ⚠️ **能**（同一 `builds_dir`） | ❌ 不能 | ❌ 不能 |
| **依赖隔离** | ❌ 装在全局，**互相影响** | ✅ 容器隔离 | ✅ Pod 隔离 |
| **多架构支持** | ❌ 只能本机架构 | ⚠️ 需 buildx 模拟（慢） | ✅ **集群里有什么架构就用什么架构**（原生） |
| **弹性** | ❌ 受单机限制 | ⚠️ 受单机 `concurrent` 限制 | ✅ **集群层面扩副本** |
| **资源限制** | ❌ **基本没有** | `cpu_shares` / `memory` / `shm_size` | **`requests` / `limits`（就是 K8s 语义）** |
| **ServiceAccount / RBAC** | ❌ 无此概念 | ❌ 无此概念 | ✅ **有**（runner 的 SA 权限可精确管控） |
| **PVC / 持久卷** | 只能挂宿主目录 | 挂宿主目录 / Docker volume | ✅ **hostPath / PVC / 临时 PVC** |
| **启动开销** | 最低 | 中（起容器） | **较高**（Pod 调度 + 拉镜像） |
| **与生产同构性** | ❌ 差 | ⚠️ 中 | ✅ **高** |
| **官方安全定性** | **"High-security risks"**，仅可信构建 | **非特权模式下"considered safe"** | 依赖 RBAC 配置质量 |
| **典型用途** | **本地调试**、需要宿主特定工具 | **通用 CI（最常用）** | **跑在 K8s 上、需要弹性与生产同构** |

> **选型一句话**：
> - **自己信得过 + 要快 + 要用宿主工具** → `shell`（但**只用于自己信任的项目**）
> - **通用 CI、要干净环境** → `docker`（**默认选择**）
> - **跑在 K8s、要弹性、要 ServiceAccount、要 PVC** → `kubernetes`
> - **跑不可信代码、要强隔离** → `docker+machine` / autoscaler（每 job 一台 VM）

### 5.2 缓存 vs 制品

| 维度 | **cache（缓存）** | **artifacts（制品）** |
|---|---|---|
| **方向** | **跨 job 复用**（同 key 的任何 job） | **向下游传递**（`needs` / `dependencies`） |
| **用途** | **加速**：依赖目录、中间产物 | **交付**：二进制、测试报告、SBOM |
| **匹配方式** | **`cache:key` 相同即命中**（**跨流水线也共享**） | **`needs` / `dependencies` 显式声明** |
| **能不能人工下载** | ❌ 不能 | ✅ 能（UI 上有下载按钮） |
| **有效期** | 有（缓存存储的保留策略） | ✅ **有（`expire_in`，未定义时用实例级设置，可设 `never`）** |
| **不写会怎样** | **每次全量重装依赖**（慢，但**不出错**） | **什么都不传**（下游拿不到，静默失败） |
| **"用错"的典型后果** | **缓存投毒**（key 太宽 → A 的依赖被 B 复用） | **artifacts 过期** → 下游报 `could not retrieve the needed artifacts` |
| **该放什么** | `~/.m2/repository`、`node_modules`、`~/.cache/pip` | **测试报告、覆盖率、SBOM、诊断日志** |
| **不该放什么** | **构建产物**（该用镜像仓库） | **依赖目录**（该用 cache；传一遍几十 MB） |

**一句话记法**：

> **缓存是"上次的结果给这次用"；制品是"这次的结果给下游用"。**
> **缓存靠 key 匹配；制品靠 needs 关联。**
> **缓存错了会"静默变脏"；制品错了会"明确报错"。**

**★ 关键的第三种选择：镜像仓库**

| | 缓存 | artifacts | **镜像仓库** |
|---|---|---|---|
| **传什么** | 依赖目录 | 任意文件 | **镜像（分层 + digest）** |
| **下游怎么用** | 目录直接用 | 按路径读文件 | **`image: repo@sha256:...`** |
| **不可变** | ❌ | ❌ | ✅ **digest** |
| **可扫描** | ❌ | ❌ | ✅ **Trivy** |
| **可验签** | ❌ | ❌ | ✅ **cosign** |
| **可追溯** | ❌ | ❌ | ✅ **label + digest** |
| **有效期** | 缓存策略 | `expire_in` | **由你控制（永不自动删）** |

> **生产镜像必须走镜像仓库**，理由见 2.7。

### 5.3 DinD vs Docker socket

| 维度 | **DinD** | **Docker socket 挂载** |
|---|---|---|
| **daemon 归属** | **job 自己的**（嵌套在容器里） | **宿主的**（共享） |
| **镜像存储** | dind 容器内，**用完即弃** | **宿主机镜像库，跨 job 累积** |
| **速度** | ❌ 慢（多一层 daemon + 网络往返） | ✅ **快**（直连 socket） |
| **资源占用** | ❌ 高（多一个 daemon 进程） | ✅ 低 |
| **国内拉镜像** | ❌ **每次重拉 → 极慢** | ✅ 拉过就复用 |
| **隔离性** | ✅ **好**（看不到别人的容器与镜像） | ❌ **差**（能操作宿主机上所有容器） |
| **权限需求** | **`privileged = true`** | 能访问 socket（通常也要特权） |
| **逃逸风险** | ✅ 较低（**破坏 dind ≠ 破坏宿主**） | ❌ **高**（**拿到 daemon ≈ 宿主 root**） |
| **官方定性** | rootless DinD + 白名单是官方推荐做法 | **"not advised"**（指 privileged）；socket 模式官方未直接背书但同样有风险 |
| **K8s 集群可用性** | ⚠️ **常被集群策略禁止**（要特权） | ✅ 通常可用 |
| **适合** | 不可信代码、多人共用 | 自己信任的代码、要快 |

**选型决策树**：

```
这个 job 的代码可信吗？
├─ 不可信（外部 PR / 多人共用 runner）
│   └─ rootless DinD（services_privileged + allowed_privileged_services 白名单）
│      └─ 集群策略禁止特权？
│         └─ 换 Kaniko / BuildKit rootless（不需要 daemon）
└─ 可信（自己项目的流水线）
    └─ 要快？
       ├─ 是 → socket 挂载（+ allowed_images / allowed_services 白名单）
       └─ 否 → DinD（隔离优先）
```

> **★ 面试这么答**："DinD 和挂 socket 的差别，本质是**'job 用的是谁的 Docker daemon'**。**DinD 是 job 自己的 daemon**，慢、费资源、但隔离——破坏 dind 容器不等于破坏宿主。**挂 socket 用的是宿主的 daemon**，快、镜像复用多，**但拿到 daemon 就等于拿到宿主 root**，官方明确说 privileged 容器拥有宿主机全部 root 能力。**我们的做法是：可信代码用 socket 挂载并配镜像白名单；不可信代码用 rootless DinD 并把 privileged 限定在白名单 service 上；K8s 集群里如果策略禁特权，就换 Kaniko，而不是去申请放开生产集群的特权。**"

---

## 6. 踩坑与排障

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **job 一直 pending**（`This job is stuck because the project doesn't have any runners online assigned to it`） | job 状态 pending，**不占资源、无输出**，GitLab 界面显示 "pending" | **① 没有 runner 带匹配的 tag**（最常见）；② **runner 离线**（进程挂了 / 网络断了 / 令牌失效）；③ **runner 明明在但 `run_untagged` 不匹配**；④ runner 的 `concurrent` 或 `limit` 已被占满 | ① 对照 `config.toml` 的 `tag_list` 与 job 的 `tags`——**job 的 tag 必须是 runner tag 的子集**；② 看 runner 所在机器 `systemctl status gitlab-runner` 与 UI 上的 online 状态；③ 带 tag 的 runner **默认不接无标签 job**，给 job 补上 tag；④ 调大 `concurrent` / `limit` | **tag 命名规范统一**（`docker` / `linux` / `ci` / `prod`）；**建 job 前先确认 runner 的 tag 列表**；**生产 runner 单独一个 tag 池**；**监控 runner 在线状态** |
| **job pending 且 UI 显示 runner 有很多空闲槽位** | 同上 | **`check_interval` 太大或 `request_concurrency` 太小** → **领活请求慢**，job 排在队列里但还没被领走 | 调小 `check_interval`、调大 `request_concurrency`（默认 1） | **runner 数量少时不要调大 `check_interval`**（多 runner 共享这个间隔，实际是均分的） |
| **缓存不命中，构建很慢**（日志里没有 "Restoring cache"） | job 正常跑完，只是慢 | ① **只写了 `key` 没写 `paths`** → **静默不缓存**；② `paths` 路径在容器里**不存在**（`~/.m2` 在容器里是 `/root/.m2`）；③ **runner 没挂 `/cache`**（`volumes` 里漏了）→ 缓存容器不持久；④ `policy: pull` → 只下不上，**下一个 job 拿不到**；⑤ key 太精确（含分支名+时间戳） | 补齐 `key` + `paths`；**paths 用容器内的绝对路径**；`volumes` 里加 `/cache`；`policy: pull-push` | **key 用 `key: { files: [pom.xml] }`**；**paths 一律绝对路径**；**新接入缓存时先跑"改一次依赖文件看 key 是否变"的验证**（见 3.4） |
| **缓存命中了但内容是脏的**（"本地能跑 CI 不能跑"） | job 跑的是**旧依赖**，且**不报错** | **key 太宽**（只有分支名或固定字符串）→ **A 分支的缓存被 B 分支复用** | 改用 `key: { files: [pom.xml] }` 或 `key: { files_commits: [pom.xml] }`；加 `prefix: $CI_JOB_NAME` 区分 job | **缓存 key 必须绑定依赖清单的哈希**；**不同分支/服务用不同 prefix** |
| **下游 job 拿不到 artifacts**（`This job could not start because it could not retrieve the needed artifacts`） | 下游 job **启动失败**，流水线红 | ① **artifacts 已过期**（`expire_in` 太短或用实例默认值）；② **用了 `needs` 但没把上游列进去**；③ 上游 job 失败 → 没上传；④ `reports` 路径写错 → 文件不存在 | 延长 `expire_in`；把上游加进 `needs`；`artifacts:when: always` 让失败也上传；用 `ls` 确认文件真实位置 | **artifacts 与 cache 分开设计**；**`expire_in` 明确设**（别依赖实例默认值）；**镜像产物走镜像仓库而不是 artifacts**（见 2.7） |
| **线上跑的是 `latest`，回滚找不到版本** | Deployment 里 `image: .../app:latest`；历史版本全部丢失 | **tag 可变 + 没有版本历史**；`latest` 不表达版本 | 回滚只能靠**重新构建**（内容可能已经变了）→ **回滚失败** | **tag 用 `$CI_COMMIT_SHORT_SHA`**；**生产用 digest 固定**；**镜像打 OCI 标准 label**（source / revision / created） |
| **镜像 tag 用 `latest` 导致无法回滚** | 同上，多一个具体表现 | **tag 是可变指针**（见 L3 篇 2.4） | **改用 commit SHA 作 tag + digest 作部署引用** | **CI 里禁止推 `latest`**（可以在 `rules` 里挡掉，或在仓库侧配置保护）；**`deploy` job 用 `@sha256:`** |
| **CI 里拉镜像极慢**（DinD 场景尤其明显） | job 在 `docker pull` 阶段卡很久 | **① 国内直连 Docker Hub 慢**（本机实测拉 alpine 超时）；② **DinD 每次 job 都是全新 daemon → 没有任何本地镜像缓存**；③ 基础镜像没有加速器 | ① 在 runner 的 Docker daemon 配置里加 **`registry-mirrors`**（L3 篇 2.3）；② **改用 socket 挂载模式**（复用宿主镜像）；③ 内部镜像统一走 ACR | **runner 主机配好加速器**；**业务镜像一律推 ACR 不依赖 Docker Hub**；**选 executor 时把"镜像拉取成本"算进去** |
| **CI 里 `docker login` 报 unauthorized** | push/pull 阶段失败 | ① **登录的 registry 域名与 push 的目标不一致**；② **凭据过期/被吊销**；③ 用了 `-p` 明文参数（**进进程表、可能进日志**）；④ 变量没设 masked → **日志里泄漏了凭据** | ① 核对域名完全一致；② **换新的访问凭证重新登录**；③ 改用 `--password-stdin`；④ 变量设 **masked + protected** | **registry 域名抽成一个变量**（`$REGISTRY`），登录与 push 共用；**凭据用 masked 变量**；**口令轮换制度**（泄露即吊销） |
| **`shell` executor 上跑的项目互相污染** | 另一个项目的构建莫名失败 | **同一台机器、同一 `builds_dir`、同一个 runner 用户** → **A 的代码能读到 B 的源码**；依赖装在全局 | 拆 runner / 换 docker executor | **`shell` executor 只用于自己信任的项目**；**生产用 docker / kubernetes executor** |
| **privileged 容器被挖到宿主 root** | 安全事件 | **`privileged = true` 的容器拥有宿主机全部 root 能力**；**DinD service 也继承了 privileged** | 官方加固方案：**`services_privileged` + `allowed_privileged_services` 白名单**（只允许 rootless DinD）；**生产 runner 只跑受保护分支** | **默认 `privileged = false`**；**不可信代码用 rootless DinD**；**K8s runner 用专用节点 + 网络隔离** |
| **`docker.sock` 挂载导致 runner 失守** | 安全事件 | **挂 socket 等于把宿主 daemon 的全部权限给了 job** → job 可以操作宿主机上**所有**容器与镜像 | 换 DinD / rootless；或加镜像白名单 + dedicated runner | **socket 模式只用于可信项目**；**加 `allowed_images` / `allowed_services`**；**runner 主机不保留 `~/.docker/config.json`**（官方明确：共享 runner 上不要以 runner 用户 `docker login`） |
| **日志被截断，看不到关键报错** | job 日志尾部缺失 | **`output_limit` 默认 4096 KB**，超限被截断 | 调大 `output_limit`；或在脚本里自己精简输出（**别 `set -x` 打印整个构建**） | **CI 里避免 `set -x`**（会打印展开后的密钥）；**需要调试时单独开一个 job** |
| **`config.toml` 改完不生效** | runner 行为没变 | 多数改动**不需要重启**（runner 每 **3 秒**检查一次配置并重载）；但**改 `listen_address` 或 `[session_server]` 需要重启** | 普通改动等几秒；改上述项执行 `gitlab-runner restart` | **改配置前用 `gitlab-runner verify` 校验**（注意：**校验器只输出 info 级消息，不能保证发现所有问题**） |
| **runner 起不来，报 critical error** | 进程启动即退出 | **`concurrent = 0` 是非法的** → runner 以 critical error 退出；或 `executor` 写错 | 把 `concurrent` 设为 ≥1；核对 `executor` 取值 | **`concurrent` 从不设 0**；**改完配置先 `verify` 再 reload** |
| **同一 ref 上多条流水线互相覆盖产物** | 后一次 push 冲掉了前一次的制品 | **同 branch/tag 上有多条流水线在跑** → artifacts 互相覆盖 | 用 `needs:pipeline` / `needs:project` 指定**具体流水线 ID**；同 ref 的部署 job 加 `resource_group` 串行化 | **同 ref 的部署 job 加 `resource_group`** |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | **`docs.gitlab.com/runner/`** | **Runner 的定义与执行流程、术语表（Runner / runner configuration / runner manager / machine / executor / tags / concurrent jobs）、GitLab-hosted 与 Self-managed 两类部署方式、Runner 版本需与 GitLab 主次版本对齐、"Runner 每 3 秒检查配置并重载"** | ✅ 2026-10-24 |
| S2 | 官方 | **`docs.gitlab.com/runner/configuration/advanced-configuration/`** | **`config.toml` 位置（root → `/etc/gitlab-runner/`，非 root → `~/.gitlab-runner/`）、`[[runners]]` 关键键与默认值（`concurrent` / `limit` / `request_concurrency`=1 / `check_interval`=3 / `output_limit`=4096 / `runtime_environment_ttl`=168h / `url` 不带 `/ci` 后缀 / `url` 与 `token` 支持 `$VAR` 展开）、`[runners.docker]` 各项（`privileged`=false / `pull_policy`=always / `cpu_shares`=1024 / `shm_size`=300000 / `services_limit`=-1 / `wait_for_services_timeout`=30 / `volume_keep`=false / `allowed_images` 默认放行全部）、`allowed_docker_credential_helpers` 默认不执行作业提供的 helper** | ✅ 2026-10-24 |
| S3 | 官方 | **`docs.gitlab.com/runner/register/`** | **runner authentication token（`glrt-` 前缀）替代 registration token、registration token 与相关参数的弃用与移除排期（GitLab 20.0）、GitLab 17.0 起可禁用旧流程并报 `410 Gone`、GitLab Runner 15.0 起注册请求格式变更、同一 token 可在多台机器复用** | ✅ 2026-10-24 |
| S4 | 官方 | **`docs.gitlab.com/runner/security/`** | **`shell` executor 高风险定性、`docker` executor 非特权模式下"considered safe"、privileged 容器拥有宿主全部 root 能力、"not advised to run containers in privileged mode"、`allowed_docker_credential_helpers` 的作用、共享 runner 上不要以 runner 用户 `docker login`、私有镜像配 `if-not-present` 的越权风险、rootless DinD 白名单加固方案、网络分段建议** | ✅ 2026-10-24 |
| S5 | 官方 | **`docs.gitlab.com/runner/executors/docker/`** | **四阶段执行流程（Prepare / Pre-job / Job / Post-job）、helper image 与 helper 容器职责、镜像优先级（`.gitlab-ci.yml` 覆盖 `config.toml`）、`/cache` 与 `/builds` 路径、volumes 与 `cache_dir` 的三种缓存落位方式、缓存卷命名格式（Runner 18.4.0 起）、`pull_policy` 三种取值与默认 `always`、多策略回退与 `allowed_pull_policies` 取交集、DinD 需 `privileged = true`、rootless DinD 白名单配置** | ✅ 2026-10-24 |
| S6 | 官方 | **`docs.gitlab.com/runner/executors/kubernetes/`** | **每 job 一个独立 Pod（build / helper / service sidecar）、四阶段流程、ServiceAccount 所需 RBAC（pods create/list/attach/exec/log、secrets、serviceaccounts 等）、`namespace_per_job` 需 ClusterRole、资源限制（`cpu_limit` / `memory_limit` / `pod_*` / `helper_*`）、`namespace_overwrite_allowed` 正则、`volumes` 与临时 PVC、Pod 级资源配置的版本要求（K8s v1.32 alpha → v1.34 beta）、helper 内存建议（≥250Mi / 128–200Mi）** | ✅ 2026-10-24 |
| S7 | 官方 | `docs.gitlab.com/ci/yaml/`（章节：**cache / artifacts**）、`docs.gitlab.com/ci/yaml/artifacts_reports.html` | **cache key 命中规则、`key:files` / `key:files_commits` / `key:prefix` 的限制、`policy`（pull/push/pull-push）、artifacts `expire_in` 未定义时用实例级设置、报告制品无论成败都上传、`needs` 与 artifacts 下载范围的关系** | ✅ 2026-10-24 |
| S8 | 官方 | `docs.gitlab.com/ci/yaml/`（章节：**artifacts troubleshooting**） | **artifacts 过期导致下游报 `could not retrieve the needed artifacts` 的官方排障条目** | ✅ 2026-10-24 |
| S9 | 官方 | `docs.gitlab.com/ci/`（章节：**Get started with GitLab CI/CD**） | **CI/CD 总览、pipeline / stage / job 概念、Runner 的角色、CI/CD 变量与 expressions** | ✅ 2026-10-24 |
| S10 | 官方 | `docs.docker.com/registry/` | **镜像仓库与 digest 引用的基本语义**（本篇 2.11 的 digest 固定引用） | ✅ 2026-10-24 |
| S11 | 官方 | **`docs.docker.com/build/building/multi-stage/`** | **多阶段构建与层缓存**（本篇 2.10 的 buildx 缓存参数依据） | ✅ 2026-10-24 |
| S12 | 中文 | `docker.c-cpp.com/build/building/packaging/` | **镜像打包与推送最佳实践（中文官方译）**——tag 与可追溯性 | ✅ 2026-10-24 |
| S13 | 中文 | `docker.c-cpp.com/engine/` | **Docker 守护进程配置（`registry-mirrors`，中文官方译）**——CI 里国内拉镜像慢的解法 | ✅ 2026-10-24 |
| S14 | 云厂商 | `help.aliyun.com/zh/acr/user-guide/` | **ACR 推送、镜像仓库凭据与命名空间**（CI 里 push 的对象） | ✅ 2026-10-24 |
| S15 | 官方 | `trivy.dev/latest/docs/` | **Trivy 镜像漏洞扫描 / 配置扫描 / SBOM 生成与 `--exit-code` 门禁**（scan job 用法） | ✅ 2026-10-24 |
| S16 | 中文 | `cloud.tencent.com/developer` | **中文实践**：CI 流水线阶段划分、runner 池化与缓存实践案例 | ✅ 2026-10-24 |
| S17 | 中文 | `developer.aliyun.com` | **中文实践**：云上 CI/CD 形态与镜像交付链路 | ✅ 2026-10-24 |

> **口径说明**：
> - 本篇引用的**所有 Runner 行为均以 `docs.gitlab.com` 官方文档为准**（S1–S9、S11、S15）。中文社区来源（S16–S17）**仅用于建立中文术语直觉与实践案例，不作为"配置项怎么写"的依据**。
> - **未编造任何数值**：所有出现的数字（`concurrent` 不能为 0、`limit` 默认 0=不限制、`request_concurrency` 默认 1、`check_interval` 默认 3 秒、`output_limit` 默认 4096 KB、`runtime_environment_ttl` 默认 168h、`pull_policy` 默认 `always`、`cpu_shares` 默认 1024、`shm_size` 默认 300000、`services_limit` 默认 -1、`wait_for_services_timeout` 默认 30、`volume_keep` 默认 false、helper 内存建议 ≥250Mi / 128–200Mi、缓存卷命名格式自 Runner 18.4.0 起）**均为官方文档明写的值或官方给出的建议值**，并已标注各自的适用边界。
> - **版本敏感项已标版本**：registration token 移除排期（GitLab 20.0）、旧注册流程可禁用（GitLab 17.0）、注册请求格式变更（Runner 15.0）、缓存卷命名格式（Runner 18.4.0）、Pod 级资源配置（K8s v1.32 alpha → v1.34 beta）。**本机 K8s 为 v1.37.x，高于 v1.34，故 Pod 级资源配置可用。**
> - **本地口径与 L3 篇一致**：仓库前缀 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`、本地 Docker 29.8.2；**本机实测拉取 `alpine:3.20` 超时**这一事实引自 L1 实测记录（本篇 2.8 与 6.1 引用同一结论）。
> - **报错原文的来源标注**：`This job is stuck because the project doesn't have any runners online assigned to it`、`This job could not start because it could not retrieve the needed artifacts`、`410 Gone - runner registration disallowed` 为**官方文档原文**（见 S1、S3、S8）；**未在本机复现的报错一律不写具体原文**（如 DinD 连不上时的 `Cannot connect to the Docker daemon at tcp://docker:2375` 属**官方 Docker executor 页面给出的典型报错**，本篇未引用）。
> - 链接实测方法见 [`../09-参考索引/README.md`](../09-参考索引/README.md)（含 3 次重试脚本，**000 是网络抖动不是失效**）。

### 待验证清单

| # | 待验证项 | 原因 | 怎么验 |
|---|---|---|---|
| 1 | **`--cache-to type=registry` 在目标 ACR 上是否可用** | BuildKit 的 registry cache 需要目标仓库支持 OCI 制品推送；**本篇未实测**（不执行任何推送动作） | 在测试仓库跑一次 buildx 带 `--cache-to type=registry` 的构建，看是否成功 |
| 2 | **目标 GitLab 实例的 `artifacts:expire_in` 实例级默认值** | 官方明确"用实例级设置"，**各实例不同，不编数字** | 查目标实例的 Admin Area → CI/CD 设置 |
| 3 | **目标实例是否已禁用 registration token** | GitLab 17.0 起管理员可禁用；禁用后用旧 token 注册会报 `410 Gone` | 查实例设置；或看现有 runner 的 `config.toml` 用的是哪种 token |
| 4 | **目标 runner 集群是否允许 privileged 容器** | 直接决定 DinD 能不能用；**本篇未实测** | 问集群管理员 PSA/OPA 策略；或用一个最小 privileged Pod 试跑 |
| 5 | **`buildx imagetools inspect` 输出的首行是否为 digest** | 本篇 3.3 的示例用它取 digest，**具体输出格式未实测** | 在本地对已推送的多架构镜像跑一次，看输出格式并调整提取命令 |
| 6 | **kubernetes executor 的 `resource_class` 映射规则** | 官方 Kubernetes executor 页面**未列出** `resource_class` 配置项 | 以目标 GitLab 版本的官方页面为准；本篇**未采用**该配置项 |
| 7 | **job 多 tag 时 runner 的匹配子集语义** | 官方术语表只说 tags "determine which jobs they can execute"，**未逐字展开子集规则** | 以目标 GitLab 版本官方页面为准；**实践上按"runner 必须具备 job 的全部 tag"设计（最安全的假设）** |

> **单源知识点标注**：
> - **Kubernetes executor 的 `resource_class` 行为**——官方页面**未列出**，本篇**已刻意不写该配置项**，**⚠️ 单源待补**。
> - **`docker.executor` 的分布式缓存（distributed cache）后端配置**——官方 Docker executor 页面只说明了本地卷缓存与"pre-job 恢复 / post-job 创建"的行为，**未展开远端后端配置**；本篇因此**只写机制与选型，不写后端配置项**，**⚠️ 单源待补**。
> - **runner tag 的子集匹配语义**——官方页面未逐字展开，本篇按通行语义表述并已加口径说明，**⚠️ 单源待补**。
> - **中文社区的 runner 池化实践**——S16/S17 为**社区来源**，只作术语与实践参考，**不作为行为依据**，**⚠️ 单源待补**（缺官方中文对照）。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入：一条 `.gitlab-ci.yml`（声明了 job、image、tags、cache、artifacts）+ 一个注册好的 runner（`config.toml` 声明了 executor、tag_list、并发与挂载）；输出：每个 job 的一组产物（镜像、报告、缓存）+ 一条可追溯的执行记录。中间经过：**Coordinator 向 GitLab 领活 → Executor 按 executor 类型准备环境（Prepare）→ helper 容器 clone + 恢复 cache + 下载 artifacts（Pre-job）→ build 容器执行 script（Job）→ helper 容器上传 cache 与 artifacts（Post-job）→ Coordinator 回传状态**。

2. **能故意改坏再倒推吗？** —— 五个具体实验：
   - **实验 A（tag 匹配）**：把 job 的 `tags: [docker]` 改成 `tags: [gpu]` → **job 一直 pending，UI 提示没有在线 runner** → 倒推出"**tag 必须是 runner tag_list 的子集**"。
   - **实验 B（缓存静默失效）**：把 `cache` 里的 `paths` 整段删掉，只留 `key` → **日志里既没有 "Restoring cache" 也没有报错，job 照样绿** → 倒推出"**只写 key 不写 paths = 完全不缓存，且不报错**"。
   - **实验 C（缓存投毒）**：把 `key: { files: [pom.xml] }` 改成 `key: "maven"` → **改了 `pom.xml` 之后日志里仍然显示 "Restoring cache"，跑的是旧依赖** → 倒推出"**key 必须绑定依赖清单的哈希**"。
   - **实验 D（artifacts 断链）**：给上游 job 设 `expire_in: 1 minute`，等它过期后再跑下游 → **下游报 `This job could not start because it could not retrieve the needed artifacts`** → 倒推出"**artifacts 有有效期，会过期**"。
   - **实验 E（产物可追溯）**：把 `IMAGE_TAG` 从 `$CI_COMMIT_SHORT_SHA` 改成 `latest`，推两次不同内容 → **两次的 tag 相同、digest 不同；`docker manifest inspect` 看到的 digest 变了** → 倒推出"**tag 是可变指针，digest 才是不可变引用**"。

3. **能画上下游吗？** —— 画出这条链并标出每一步的失败表现：
   ```
   .gitlab-ci.yml ─┐
   config.toml ────┤
                  ▼
        Coordinator（领活：tag 匹配？runner 在线？并发够？）
                  │  失败 → job pending
                  ▼
        Executor（Prepare：拉镜像 / 建容器 / 建 Pod）
                  │  失败 → executor preparation failed
                  ▼
        helper（Pre-job：clone → 恢复 cache → 下载 artifacts）
                  │  失败 → clone 报错 / cache 跳过 / artifacts 报错
                  ▼
        build 容器（Job：跑 script）
                  │  失败 → 退出码非 0 → job 失败
                  ▼
        helper（Post-job：上传 cache → 上传 artifacts/reports）
                  │  失败 → artifacts 上传失败 → job 失败
                  ▼
        Coordinator 回传状态 → GitLab 展示 → 触发下游 job
                  │
                  └─→ 下游 job 通过 needs 拿 artifacts + dotenv 变量
                        │
                        └─→ push 到 registry（拿到 digest）
                              │
                              └─→ deploy（用 @sha256: 引用）
   ```
   **每一步的失败表现**：pending（无匹配 runner）→ executor preparation failed（镜像/权限/RBAC）→ clone 报错（凭据/网络）→ cache 静默跳过（key/paths 不对）→ script 退出码非 0 → artifacts 上传失败 → 下游 `could not retrieve the needed artifacts` → push `unauthorized` → deploy kubectl 报错。

---

**下一篇**：[`L3-质量门禁-SonarQube与Trivy`](L3-质量门禁-SonarQube与Trivy.md)（**已写** · 质量门禁与漏洞阻断）· [`L4-多环境策略与分支模型`](L4-多环境策略与分支模型.md)（**已写** · 多环境与分支模型） · **上一篇**：[`L1-GitLabCI-流水线规范`](L1-GitLabCI-流水线规范.md)（**已写** · `.gitlab-ci.yml` 逐字段、rules/needs/DAG、流水线即代码） · **上一级**：[`../00-学习地图与前置依赖`](../00-学习地图与前置依赖.md) · **关联日课**：[`第1周/第04天.md`](../第1周/第04天.md)（GitLab CI）· [`第1周/第05天.md`](../第1周/第05天.md)（门禁）· [`../01-基础篇/L3-镜像仓库与供应链安全.md`](../01-基础篇/L3-镜像仓库与供应链安全.md)（镜像引用与 digest）
