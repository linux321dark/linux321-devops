# L4 · ArgoCD 安装与同步（装起来 → 看懂 diff → 会同步）

---

### 3.5 常用运维命令（Argo CD 日常运维 + GitOps 排障，按场景）

> **本节能做什么**：3.1–3.4 是"把 Argo CD 装上、把 Application 建起来同步成功"，本节是**每天值班敲的 Argo CD CLI 命令 + GitOps 排障套路**——从看状态、手动同步、回滚到集群/仓库管理，以及另一条 GitOps 路线 Flux 的对照命令。配置管理 / DevOps 岗的 GitOps 肌肉记忆就来自这一张表。
> **环境基准**：本机实测 `argocd v2.11.x` + `flux v2.3.x`。CLI 默认已 `argocd login` 到控制面。

#### ① Application 日常运维（app list / get / sync / logs / diff / rollback）

```bash
argocd app list                                  # 看所有 Application 的同步/健康状态（值班第一眼）
argocd app get guestbook                         # 看单个 app 的树状资源、同步状态、健康
argocd app sync guestbook                        # 手动触发一次同步（Git 已改但没自动同步时）
argocd app sync guestbook --prune                # 同步并清理 Git 里已删除的孤儿资源
argocd app diff guestbook                        # 预览 Git 期望 vs 集群实际会改什么（不动手）
argocd app logs guestbook                        # 看 Argo CD 对这个 app 的同步日志
argocd app rollback guestbook <REVISION>         # 回滚到某次 Git 提交对应的版本（救火第一招）
argocd app terminate-op guestbook                # 卡死的同步操作强制终止
```

> **实战价值**：`app list` 是值班时一眼看全集群哪些 app 飘红；`diff` 在手动同步前先确认"到底会变什么"，比盲 `sync` 安全；`rollback` 到历史 Git revision 是 GitOps 相比手工 kubectl 的最大优势——**每次发布都有 Git commit 可追溯、可回退**。

#### ② 集群与仓库管理（cluster / repo）

```bash
argocd cluster add <kubecontext>                 # 把另一个 kube-context 注册进 Argo CD
argocd cluster list                              # 看已纳管的所有集群
argocd cluster get <CLUSTER_URL>                 # 看某个集群的详细信息/状态
argocd repo add https://github.com/xxx/gitops.git --username xxx --password xxx   # 加 Git 仓库凭证
argocd repo list                                 # 看已配置的仓库及其连接状态
argocd repo get <REPO_URL>                       # 看单个仓库的认证/健康检查
argocd app create ... --dest-server <CLUSTER_URL> # 把 app 部署到指定集群（多集群关键）
```

> **实战价值**：`cluster add` + `app --dest-server` 是多集群 GitOps 的标准动作；`repo list` 里仓库若 `Connection Status: Failed`，所有指向它的 app 都会 `Unknown`，是"为什么突然全飘红"的第一排查点。

#### ③ GitOps 排障（同步失败 / drift 检测）

```bash
argocd app get guestbook -o yaml                 # 拿完整状态 YAML，看 status.operation.message 报错
argocd app logs guestbook --since 1h             # 看最近 1 小时的同步日志
kubectl -n guestbook scale deploy guestbook --replicas=5   # 故意手改副本数，制造 drift
argocd app get guestbook                         # 状态变 OutOfSync（drift 被检出）
argocd app sync guestbook --force                # 强制把 Git 期望推回集群（selfHeal 关闭时手动纠正）
argocd app wait guestbook --health               # 等 app 健康，常用于流水线里卡同步完成
```

> **实战价值**：同步失败先看 `app get -o yaml` 的 `status.operation.message`，九成错误（镜像拉不到、RBAC 不足、字段非法）写在那里，别盲猜。drift 是 GitOps 的命门：集群被手改后，要么 `selfHeal` 自动纠回 Git 期望，要么靠 `app sync --force` 手动纠——这正是"声明即真相"的体现，面试必问。

#### ④ 对照：Flux 路线（flux get / check / reconcile / suspend / resume）

```bash
flux get kustomizations                         # 看所有 Kustomization 的就绪/最后应用时间
flux get sources git                            # 看 GitRepository 源同步状态
flux check                                      # 一键自检：控制器/CRD/网络连通是否都健康
flux reconcile kustomization my-app --with-source   # 手动触发一次调和（等价于 Argo 的 sync）
flux suspend kustomization my-app               # 暂停自动调和（维护/排障时）
flux resume kustomization my-app                # 恢复自动调和
```

> **实战价值**：Flux 与 Argo CD 是 GitOps 两大实现，面试常让对比。Flux 走"控制器自动 reconcile"模型，`suspend`/`resume` 是临时冻结节点的手段；`flux check` 是排查"为什么都不动了"的入口。同一套 Git 仓库，两套引擎命令风格不同，但核心都是"Git 期望 ↔ 集群实际"的自动闭环。

> **本篇定位**：GitOps 交付篇的第一篇。前面 L1/L2/L3 把 Helm 和 Terraform 的能力铺好了，这篇讲**怎么让集群自己从 Git 里拿配置并对齐**。
> **前置**：L2 K8s 架构与对象模型（必须理解 CRD、controller、期望态/实际态三态模型）、L1 Docker（镜像地址与拉取）、本目录 [`L1-Helm-模板化封装`](L1-Helm-模板化封装.md)（Helm 渲染出来的就是 ArgoCD 要同步的东西）。
> **学完能做什么**：讲清 GitOps 的"拉模型"和传统 CI/CD "推模型"的安全差异；说清 Application / AppProject / Repository / Cluster / ApplicationSet 五个对象各管什么；在 kind 集群装起 ArgoCD、添加私有仓库、建 Application、**看懂 diff 再同步**；解释 Healthy / Progressing / Degraded / Missing / Suspended / Unknown 六个健康状态。
> **在 21 天里**：D10（ArgoCD 安装与同步）。D11 的漂移 / SelfHeal / Prune / 多集群策略建立在本文的 Application 概念之上。
> **环境/版本基准**：命令、字段与行为以 **Argo CD 官方文档站（argo-cd.readthedocs.io/en/stable）** 为准，写作时对照的文档分支为 `stable`。**本次写作未安装 ArgoCD、未创建任何 Application、未连接任何集群**；所有输出片段均为官方文档中的示意输出。集群版本口径与本套资料统一：**本地 K8s v1.37.x**，kind 集群名 `day2`，镜像仓库前缀 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
ArgoCD 安装与同步
│
├─ 1. 概念
│   ├─ 1.1 GitOps 是什么：Git 是唯一事实源
│   ├─ 1.2 推模型 vs 拉模型（与传统 CI/CD 的根本差异）
│   ├─ 1.3 五个核心对象各管什么
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 Application 的 spec 关键字段
│   ├─ 2.2 安装：namespace 与 CRD 的先后关系
│   ├─ 2.3 三个核心组件的职责分工
│   ├─ 2.4 添加仓库：凭据、SSH key、insecure
│   ├─ 2.5 同步流程：render → diff → apply
│   ├─ 2.6 健康评估：六个状态 + health.lua
│   ├─ 2.7 自动同步 vs 手动同步
│   ├─ 2.8 Healthy ≠ 业务可用
│   └─ 2.9 权限与 RBAC
│
├─ 3. 动手：kind 集群里装一套 ArgoCD
│   ├─ 3.1 安装
│   ├─ 3.2 登录与添加仓库
│   ├─ 3.3 建 Application → 看 diff → 同步
│   └─ 3.4 验证清单
│
├─ 4. 逐字段表
│   ├─ 4.1 Application spec 关键字段表
│   └─ 4.2 常用 CLI 命令表
│
├─ 5. 横向对比
│   ├─ 5.1 ArgoCD vs Flux vs Jenkins X
│   ├─ 5.2 ArgoCD vs Helm
│   └─ 5.3 两种 source 类型：同步目录 vs 渲染后的 manifest
│
├─ 6. 踩坑与排障（症状 → 状态 → 原因）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 GitOps 是什么：Git 是唯一事实源

官方对 ArgoCD 的定义只有一句：

> "Argo CD is a **declarative, GitOps continuous delivery tool for Kubernetes**."

拆开看这个定义里的三个词，每个都是技术要点：

| 词 | 含义 | 推论 |
|---|---|---|
| **declarative**（声明式） | 你描述目标状态，不是操作步骤 | 描述"这个 Deployment 要 3 副本"，不是"先删再起" |
| **GitOps** | **Git 是应用状态的唯一事实源（single source of truth）** | 集群里的一切最终由 Git 决定 |
| **continuous delivery** | 持续交付 | **不是"发布时执行一次"，是"一直在对比一直在拉平"** |

**GitOps 的完整主张**（这是本篇的地基）：

```
   ┌────────────────────────────────────────────────┐
   │  Git 仓库                                       │
   │  ├── apps/          ← 应用的期望状态（K8s 清单） │
   │  ├── charts/        ← Helm Chart                │
   │  └── gitops/        ← Application 定义           │
   └───────────────────┬────────────────────────────┘
                       │  ① 拉取（pull）
                       ▼
   ┌────────────────────────────────────────────────┐
   │  ArgoCD Controller                              │
   │  ② render：把 Git 内容渲染成最终 manifest       │
   │  ③ diff：与集群实际状态对比                      │
   │  ④ apply：有差异就同步                          │
   │  ⑤ 持续循环（reconcile）                         │
   └───────────────────┬────────────────────────────┘
                       │  写
                       ▼
   ┌────────────────────────────────────────────────┐
   │  Kubernetes 集群                                 │
   │  Deployment / Service / Ingress / ...            │
   └────────────────────────────────────────────────┘

   人只改 Git。绝不直接改集群。
```

**三条核心主张**（面试直接答这三条）：

1. **Git 是唯一事实源**：集群的实际状态不写在任何地方，只由 Git 推导出来。
2. **声明期望状态**：Git 里存的是"要什么"，不是"怎么做"。
3. **持续拉平（reconcile）**：controller 一直在对比，发现差异就纠正。

### 1.2 推模型 vs 拉模型（与传统 CI/CD 的根本差异）

**这一节是本篇最重要的内容，也是面试必问。**

#### 传统 CI/CD：推模型（push）

```
开发者 ──git push──► GitLab ──CI 构建──► 镜像仓库
                                        │
                                   流水线拿着 K8s 凭据
                                        │
                                        ▼ 主动推
                              kubectl apply / helm upgrade
                                        │
                                        ▼
                                  目标集群
```

**问题出在最后两步**：

| 问题 | 后果 |
|---|---|
| **流水线要持有集群凭据** | CI 系统成了"能操作生产集群的高权限系统"。凭据泄漏 = 生产沦陷；CI 变成必须重点防护的资产 |
| **凭据是"写权限"** | 有写权限就能改任何东西。集群里除了 CI，谁都改不了 |
| **流水线只能"在触发时"动作** | 有人在集群里手动改了东西？**流水线不知道**。直到下一次有人提交，才可能"顺便"覆盖掉 |
| **多集群要 N 份凭据** | 20 个集群 = 20 套凭据要分发和轮换 |
| **审计靠日志** | "谁改了这个生产配置"要看 CI 日志，日志会过期、会丢 |

#### GitOps：拉模型（pull）

```
开发者 ──git push──► Git 仓库（唯一事实源）
                           │
                           │  ② ArgoCD 用自己的凭据去拉
                           ▼
                     ArgoCD Controller
                           │
        ③ render → ④ diff → ⑤ apply
                           │
                           ▼
                       目标集群

  集群凭据只存在 ArgoCD 一处（每个目标集群一份）
  人不直接操作集群，只改 Git
```

| 维度 | 推模型（CI/CD） | **拉模型（GitOps）** |
|---|---|---|
| 谁发起变更 | CI 流水线**推** | ArgoCD **拉** |
| 集群凭据在哪 | **分散在 CI 系统**（可能 N 份） | **集中在 ArgoCD**（每个目标集群一份 ServiceAccount） |
| 凭据权限 | 写权限（apply/delete） | 写权限（同样需要，但**只有一处**） |
| **漂移检测** | ❌ **CI 不知道有人手改了集群** | ✅ **ArgoCD 一直在对比，自动发现并纠正** |
| 谁能改生产 | CI + 拿到凭据的人 | **只有 Git 的提交者**（改集群会被自动改回去） |
| 权限撤销 | 要去 N 个地方撤 | 撤一个 ServiceAccount 绑定即可 |
| 审计 | CI 日志 | **Git commit**（谁改的、什么时候改的、为什么改，全在） |
| 多集群 | 流水线要 N 套凭据 + N 份部署逻辑 | ArgoCD 一次加 N 个集群，配置复用 |

> **"自动检测漂移"是拉模型的根本优势，也是 ArgoCD 与传统 CD 工具最本质的区别。** 因为 ArgoCD 的工作方式不是"发布"，而是"**持续对比并拉平**"—— 只要现实偏离了 Git，它就会发现。
>
> **注意：ArgoCD 并不是"没有 CI"**。CI 依然要做：跑测试、构建镜像、扫漏洞、推镜像。**CI 管"制品从哪来"，ArgoCD 管"集群里应该是什么"**。两者是上下游，不是二选一。

### 1.3 五个核心对象各管什么

ArgoCD 的对象模型可以类比成"文件夹 / 文件 / 钥匙 / 目标"：

| 对象 | CRD / 类型 | 它管什么 | 一句话 |
|---|---|---|---|
| **Application** | `Application` | **一个应用**：从哪个仓库的哪个路径渲染、部署到哪个集群的哪个命名空间、用什么同步策略 | **核心对象**，你 90% 的时间在操作它 |
| **AppProject** | `AppProject` | **一组应用的边界**：能用哪些仓库、能部署到哪些集群/命名空间、能用哪些资源类型 | 权限与隔离的边界 |
| **Repository** | `Secret`（`argocd.argoproj.io/secret-type: repository`） | **一个 Git 仓库的连接凭据** | 私有仓库要在这配 SSH key 或 token |
| **Cluster** | `Secret`（`argocd.argoproj.io/secret-type: cluster`） | **一个目标集群的连接凭据** | 集群外的集群要在这注册 |
| **ApplicationSet** | `ApplicationSet` | **用模板批量生成 Application** | 多集群 / 多环境时用它 |
| **Settings** | 各种 ConfigMap（`argocd-cm`、`argocd-rbac-cm`、`argocd-cmd-params-cm`…） | **全局配置**：diff 忽略规则、健康检查脚本、RBAC 策略、命令参数 | 系统级调优入口 |

**它们的关系**（这张图要能画出来）：

```
                    ┌──────────────┐
                    │  AppProject  │  允许的仓库 / 集群 / 命名空间
                    └──────┬───────┘
                           │ 归属
                           ▼
  ┌──────────────┐   ┌──────────────┐        ┌──────────────┐
  │  Repository  │──►│  Application │───────►│    Cluster   │
  │  （凭据）     │   │              │  部署到 │   （凭据）    │
  └──────────────┘   └──────┬───────┘        └──────────────┘
        │                     │
        │ 从这里取清单          │ 渲染模板
        ▼                     ▼
   ┌─────────┐        ┌──────────────┐
   │   Git   │        │ ApplicationSet│──► 生成 N 个 Application
   └─────────┘        └──────────────┘
```

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| GitOps | — | 以 Git 为唯一事实源的运维模式 |
| Application | 应用 | ArgoCD 的核心 CR，声明"这个应用是什么" |
| AppProject | 项目 | 一组应用的权限与边界 |
| Repo Server | 仓库服务器 | ArgoCD 组件：**clone Git + 渲染 manifest** |
| Application Controller | 应用控制器 | ArgoCD 组件：**对比状态 + 执行同步** |
| API Server | API 服务器 | ArgoCD 组件：**提供 gRPC API + Web UI** |
| Sync Status | 同步状态 | `Synced` / `OutOfSync` / `Unknown` —— "集群和 Git 一致吗" |
| Health Status | 健康状态 | `Healthy` / `Progressing` / `Degraded` / `Missing` / `Suspended` / `Unknown` —— "跑起来正常吗" |
| Diff | 差异 | Git 渲染结果 vs 集群实际状态的对比 |
| Render | 渲染 | 把 Helm/Kustomize/Jsonnet 变成最终 K8s manifest |
| Sync Wave | 同步波次 | 用注解控制资源应用的先后顺序 |
| Hook | 钩子 | 插在同步流程某个阶段执行的资源（通常是 Job） |
| Prune | 剪枝 | 删除 Git 里已经没有、但集群里还存在的资源 |
| Self-Heal | 自愈 | 集群被手改后自动改回 Git 的状态 |
| Drift | 漂移 | 实际状态偏离 Git |
| Insecure | 不安全模式 | 跳过 TLS/SSH 证书校验（**慎用**） |
| CRD | 自定义资源定义 | ArgoCD 的 Application 等都是 CRD，**必须先于 ArgoCD 本体安装** |

---

## 2. 机制

### 2.1 Application 的 spec 关键字段

官方 Application Specification 给出的完整结构里，最常用的是这几块：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: guestbook
  namespace: argocd          # ← Application 自己通常放在 argocd 命名空间
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default           # ← 属于哪个 AppProject

  source:                    # ← 单 source 写法
    repoURL: https://github.com/argoproj/argo-cd-example-apps.git
    targetRevision: HEAD     # ← 分支/标签/commit
    path: guestbook          # ← 仓库里的哪一段
    directory:
      recurse: true         # ← 是否递归子目录

  destination:
    server: https://kubernetes.default.svc   # ← 目标集群 API 地址
    namespace: guestbook                      # ← 目标命名空间

  syncPolicy:
    automated:
      enabled: true
      prune: true
      selfHeal: true
      allowEmpty: false
    syncOptions:
      - CreateNamespace=true
    retry:
      limit: 5
      backoff:
        duration: 5s
        factor: 2
        maxDuration: 3m

  ignoreDifferences:
    - group: apps
      kind: Deployment
      jsonPointers:
        - /spec/replicas

  revisionHistoryLimit: 10
```

**几个必须知道的口径**：

| 字段 | 官方说明 | 注意 |
|---|---|---|
| `spec.source.repoURL` | 可以指向 **Helm chart 仓库**或 **git 仓库** | 两个是不同的 source 类型（见 5.3） |
| `spec.source.targetRevision` | "For Helm, this refers to the **chart version**" | Helm 源时它是 chart 版本号，不是分支 |
| `spec.source.path` | "This has **no meaning for Helm charts** pulled directly from a Helm repo instead of git" | Helm repo 源时 path 无效 |
| `spec.destination.server` | 集群 API 地址 | 部署到 ArgoCD 自己所在的集群用 `https://kubernetes.default.svc` |
| `spec.destination.namespace` | "The namespace will only be set for namespace-scoped resources that have **not set a value** for `.metadata.namespace`" | 清单里写死了 namespace 的资源不受它影响 |
| `metadata.finalizers` | `resources-finalizer.argocd.argoproj.io` | **只有想让它级联删除时才加**。官方注释写得很清楚："Add this finalizer ONLY if you want these to cascade delete" |
| `spec.syncPolicy.automated` | 官方注释："automated sync by default **retries failed attempts 5 times** with following delays between attempts ( **5s, 10s, 20s, 40s, 80s** )" | 这五个退避值是官方标称 |
| `spec.syncPolicy.retry.limit` | "number of failed sync attempt retries; **unlimited number of attempts if less than 0**" | |
| `spec.syncPolicy.retry.backoff` | `duration` / `factor` / `maxDuration` | 默认值如上（5s / 2 / 3m） |
| `spec.revisionHistoryLimit` | "limits the number of items kept in the application's revision history"；"**Setting this to zero will store no history**" | 官方示例值 10。**这是回滚能回多远的上限** |

**`destination` 的两种写法**：官方给了 `server`（集群 API URL）和 `name`（集群名）两种，注释说 `name: in-cluster`。**但项目匹配时官方明确只认 `server`**：

> "for clusters always provide **server**, the name is not used for matching"

> **所以：AppProject 的 `destinations` 规则里必须写 `server`，写 `name` 不生效。** 这是个高频坑。

### 2.2 安装：namespace 与 CRD 的先后关系

官方 Getting Started 给出的安装命令：

```bash
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

**注意命令里没有单独的 CRD 安装步骤** —— 因为 `install.yaml` 里已经包含了 CRD。**但执行的顺序不可颠倒**，这正是官方为什么要加那两个 flag：

**官方对 `--server-side --force-conflicts` 的解释**：

> "The `--server-side` flag is required because some Argo CD CRDs (like ApplicationSet) exceed the **262KB annotation size limit** imposed by client-side `kubectl apply`. Server-side apply avoids this limitation by not storing the `last-applied-configuration` annotation."

> "The `--force-conflicts` flag allows the apply operation to take ownership of fields that may have been previously managed by other tools (such as Helm or a previous `kubectl apply`). This is safe for **fresh installs** and **necessary for upgrades**."

**所以安装的三个要点**：

| 要点 | 说明 |
|---|---|
| **① namespace 先建** | `kubectl create namespace argocd` |
| **② CRD 必须在 ArgoCD 本体之前就位** | `install.yaml` 里 CRD 在前、`Deployment` 在后，kubectl 按顺序 apply。**但如果你拆开装（先装 CRD 再装本体），必须先 CRD 后本体** —— 否则 ArgoCD 的 Pod 起来后 watch 不到 `Application` 这个 CRD，API 会报 `no matches for kind "Application"` |
| **③ 一定要用 `--server-side --force-conflicts`** | 客户端 apply 会因为 CRD 太大直接失败 |

> **升级时的官方提醒**（很实用）：
> "Note that any custom modifications you've made to fields that are **defined in the Argo CD manifests** (like `affinity`, `env`, or `probes`) will be **overwritten**. However, fields **not specified in the manifests** (like `resources` limits/requests or `tolerations`) will be preserved."
>
> **所以：改 ArgoCD 的 `affinity`/`env`/`probes` 要改 manifest 源（GitOps 管理自己的 ArgoCD），不要直接 kubectl patch。** 改 `resources`/`tolerations` 这类 manifest 里没写的字段则是安全的。

**生产建议**：官方说 "Using a **pinned version** (like `v3.2.0`) is recommended for production." —— 生产用**固定版本**，不要用 `stable` 分支的 latest。

### 2.3 三个核心组件的职责分工

ArgoCD 的 Deployment 不少，但真正干活的是三个：

| 组件 | 干什么 | 类比 |
|---|---|---|
| **argocd-server**（API Server） | 提供 **gRPC API** 和 **Web UI**；处理登录、RBAC 鉴权；接收同步请求 | 前台 / 接待 |
| **argocd-repo-server** | **clone Git 仓库**、**渲染 manifest**（Helm/Kustomize/Jsonnet）、**缓存**结果 | 后厨 / 仓库管理员 |
| **argocd-application-controller** | **对比 Git 与集群状态**、**执行同步**、**持续 reconcile** | 巡检员 /  reconciler |

**数据流**：

```
       ┌──────────────────┐
       │  argocd-server   │  ← UI / CLI / CI 调它
       │   (API + UI)     │
       └────────┬─────────┘
                │ gRPC
                ▼
       ┌──────────────────┐
       │ argocd-repo-     │  ← clone + render + 缓存
       │     server       │
       └────────┬─────────┘
                │ 渲染后的 manifest
                ▼
       ┌──────────────────┐
       │ application-     │  ← 对比 + apply + reconcile
       │   controller     │
       └────────┬─────────┘
                │ 读写
                ▼
            K8s 集群
```

**注意 repo-server 和 controller 的分工很多人搞混**：

| 问题 | 归谁管 |
|---|---|
| "Git 拉不下来" | **repo-server**（凭据、网络、证书） |
| "manifest 渲染不出来"（Helm 模板语法错） | **repo-server** |
| "资源没被创建" | **controller**（同步逻辑） |
| "集群里的和 Git 不一致" | **controller**（diff + 拉平） |
| "UI 打不开" | **server** |

**其他组件**（了解即可）：

| 组件 | 作用 | 备注 |
|---|---|---|
| `argocd-redis` | 缓存 | 官方 Getting Started 提到："This default installation for Redis is using password authentication. The Redis password is stored in Kubernetes secret **`argocd-redis`** with key **`auth`**" |
| `argocd-dex-server` | 内置 SSO（OIDC provider） | 完整 SSO 场景才需要 |
| `argocd-notifications-controller` | 通知（Slack/钉钉/webhook） | 可选 |
| `argocd-applicationset-controller` | **批量生成 Application** | 装 ApplicationSet 就需要它 |

> **一个重要的官方安全提示**（ApplicationSet 文档）：
> "The set of tools provided by the ApplicationSet controller may also be used to allow developers (**without access to the Argo CD namespace**) to independently create Applications without cluster-administrator intervention. **Warning: Be aware of the security implications** before allowing developers to create Applications via ApplicationSets."

### 2.4 添加仓库：凭据、SSH key、insecure

**仓库凭据存在哪**：官方 Projects 文档说明得很清楚 —— "Repository and Cluster can be **project-scoped**. In declarative configuration, they are both stored as Kubernetes **`Secret`**."

一个 Repository Secret 长这样：

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: argocd-example-apps
  labels:
    argocd.argoproj.io/secret-type: repository
type: Opaque
stringData:
  project: my-project1
  name: argocd-example-apps
  url: https://github.com/argoproj/argo-cd-example-apps.git
  username: ****
  password: ****
```

**CLI 添加仓库**（官方 `argocd repo add` 参考页的用法）：

```bash
# 公有仓库
argocd repo add https://github.com/argoproj/argo-cd-example-apps.git

# 私有仓库 —— HTTPS + 用户名密码
argocd repo add https://git.example.com/repos/repo --username git --password secret

# 私有仓库 —— SSH key
argocd repo add git@git.example.com:repos/repo --ssh-private-key-path ~/.ssh/id_rsa

# SSH 非默认端口
argocd repo add ssh://git@git.example.com:2222/repos/repo --ssh-private-key-path ~/.ssh/id_rsa

# Helm 仓库（必须带 --name）
argocd repo add https://charts.helm.sh/stable --type helm --name stable

# OCI 仓库
argocd repo add oci://helm-oci-registry.cn-zhangjiakou.cr.aliyuncs.com --type oci --name stable --username test --password test
```

**`argocd repo add` 的关键 flag**（官方选项列表）：

| flag | 用途 | 风险 |
|---|---|---|
| `--username` / `--password` | HTTPS Basic Auth | 密码明文存在 Secret 里 |
| `--ssh-private-key-path` | SSH 私钥路径 | **私钥必须先存在于 ArgoCD 能读到的 Pod 文件系统里** |
| `--tls-client-cert-path` / `--tls-client-cert-key-path` | 客户端证书 | PEM 格式 |
| `--insecure-skip-server-verification` | **跳过服务端证书与 host key 校验** | ⚠️ **高危**：等于接受中间人攻击 |
| `--insecure-ignore-host-key` | 关闭 SSH strict host key checking | ⚠️ 官方已标 **deprecated** |
| `--enable-lfs` | 开启 Git LFS | |
| `--enable-oci` | Helm OCI 仓库 | 仅对 `--type helm` 有效 |
| `--project` | 设为 project-scoped | |
| `--type` | `git`（默认）/ `oci` / `helm` | |
| `--name` | 仓库名，**`helm` 类型必填** | 漏了 → 报错 |
| `--upsert` | 同名仓库已存在时覆盖 | |

> **`--insecure-skip-server-verification` 到底意味着什么**：官方 flag 描述是 "disables server certificate and host key checks"。**这等于 ArgoCD 不再验证 Git 服务器的身份** —— 任何能劫持网络的人都可以把自己伪装成 Git 服务器，拿到你的私有仓库内容。**生产环境绝对不要用**。正确做法是把 CA 证书装进 ArgoCD 的信任链。
>
> **私有仓库凭据优先级**（本篇的实操建议）：
> ```
> SSH key（部署机 → 只读 deploy key）  >  HTTPS + token（最小权限 PAT）  >  HTTPS + 用户名密码
> ```
> **绝不用个人账号的密码**；能用只读 deploy key 就不要用有写权限的 token。

### 2.5 同步流程：render → diff → apply

**这是本篇最需要画出来的流程。**

```
Git 仓库
   │
   │  ① repo-server: clone（带凭据）
   ▼
   │  ② repo-server: render
   │     ├─ 纯 YAML 目录 → 直接读
   │     ├─ Helm chart  → helm template
   │     ├─ Kustomize  → kustomize build
   │     └─ Jsonnet / plugin → 各自渲染
   ▼
期望 manifest（desired state）
   │
   │  ③ controller: 与集群实际状态对比（diff）
   ▼
   ┌─────────────────────────┐
   │  有差异吗？              │
   │   无 → 不动，继续轮询     │
   │   有 ↓                    │
   └─────────────────────────┘
   │
   │  ④ controller: apply
   │     ├─ 手工同步：UI/CLI 点一下
   │     └─ 自动同步：automated.enabled = true
   ▼
集群实际状态（live state）
   │
   └──→ 回到 ③，循环（reconcile）
```

**三步各自的失败表现**（排障地图）：

| 步骤 | 失败时 | 典型原因 |
|---|---|---|
| **① clone** | Application 报错拉不到仓库；UI 显示 `Failed to load target state` | 凭据错 / 网络不通 / 证书问题 / 仓库地址错 |
| **② render** | 报错渲染失败；`argocd app manifests` 也出不来 | Helm 模板语法错 / values 缺参数 / Kustomize 引用了不存在的 base |
| **③ diff** | 显示 `OutOfSync`，列出差异 | **这是正常的，不是错误** —— 就是"有变更待同步" |
| **④ apply** | 同步失败，Application 状态 `OperationFailed` | RBAC 权限不足 / CRD 缺失 / 资源被别的控制器占用 / hook 失败 |

> **官方对 diff 的一个重要提醒**（Diffing Customization 文档）：
> "It is possible for an application to be `OutOfSync` **even immediately after a successful Sync operation**."
> 官方列的原因包括：manifest 里有 K8s 不认识的字段被丢弃、sync 时禁用了 prune、**mutating webhook 在提交后修改了对象**、Helm 模板用了 `randAlphaNum` 这类每次渲染都不同的函数、HPA controller 会重排 `spec.metrics`。
> **所以"同步完还是 OutOfSync"不一定是 bug，要先看 diff 内容。**

**人应该看 diff 再同步** —— 这是本篇的核心纪律：

```bash
# ① 先看最终会渲染出什么（不碰集群）
argocd app manifests guestbook

# ② 看和集群实际的差异
argocd app diff guestbook

# ③ 确认没问题再同步
argocd app sync guestbook
```

**UI 上的对应操作**：Application 详情页 → 点应用名进详情 → **Diff** 标签页看差异 → 确认 → 点 **SYNC**。

### 2.6 健康评估：六个状态 + health.lua

#### 2.6.1 六个健康状态

官方 Resource Health 文档给出了内置检查和自定义检查可返回的状态：

| 状态 | 官方含义 | 典型原因 |
|---|---|---|
| **Healthy** | 资源健康 | 达到期望状态 |
| **Progressing** | "not healthy yet but **still making progress** and might be healthy soon" | 正在滚动更新、Pod 还在启动 |
| **Degraded** | "the resource is **degraded**" | Pod CrashLoop、副本不够、Job 失败 |
| **Suspended** | "the resource is **suspended** and waiting for some external event to resume it" | `Job.spec.suspend=true`、CronJob 被暂停 |
| **Missing** | Git 里有、集群里没有 | 还没同步 / 被手工删了 |
| **Unknown** | 健康状况无法判断 | 状态字段缺失、CRD 没有健康检查 |

> **官方明确：自定义健康检查脚本可以返回的是前四个** —— `Healthy` / `Progressing` / `Degraded` / `Suspended`。`Missing` 和 `Unknown` 由 ArgoCD 自身判定。

#### 2.6.2 内置健康检查规则（背这张表）

官方 Resource Health 页逐类型列出的判定条件：

| 资源类型 | 判定为 Healthy 的条件 |
|---|---|
| **Deployment / ReplicaSet / StatefulSet / DaemonSet** | ① `observedGeneration` **等于**期望的 `generation`；② **已更新副本数等于期望副本数** |
| **Service**（type=LoadBalancer） | `status.loadBalancer.ingress` 列表**非空**，且至少有一个带 hostname 或 IP 的条目 |
| **Ingress** | `status.loadBalancer.ingress` 列表非空，且至少有一个带 hostname 或 IP 的条目 |
| **CronJob** | 上一次调度的 Job **失败 → Degraded**；**正在运行 → Progressing**；否则 Healthy |
| **Job** | `job.spec.suspended` 为 `true` → Job 及其所属 Application 标记为 **Suspended** |
| **PersistentVolumeClaim** | `status.phase` 为 **`Bound`** |

> **这张表能解释很多"为什么显示 Healthy 但实际不对"的情况**：
> - Deployment 的 Healthy **只看副本数和 generation，不看 Pod 的业务健康**（不跑探针）→ 应用 500 错误它也不知道。
> - ClusterIP 类型的 Service **没有 health check**（只有 LoadBalancer 有）→ Service 显示什么状态？它不在内置检查列表里 → 走默认路径。

#### 2.6.3 health.lua：ArgoCD 怎么知道资源健不健康

**ArgoCD 的健康检查靠资源内置的 Lua 脚本**（官方称 "health.lua"）。

**机制**：

```
ArgoCD 拿到集群里资源的 live object
        │
        ▼
按 GVK 找健康检查脚本
  ├─ 内置的（Deployment/StatefulSet/Service/...）
  └─ 自定义的（argocd-cm 里配的）
        │
        ▼
脚本执行，返回 { status, message }
        │
        ▼
Application 的整体健康 = 所有子资源健康里"最差"的那个
```

**自定义健康检查怎么配**（官方 `argocd-cm` 的 `resource.customizations.health.<group>_<kind>` 键格式）：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-cm
  namespace: argocd
data:
  resource.customizations.health.cert-manager.io_Certificate: |
    hs = {}

    if obj.status ~= nil then
      if obj.status.conditions ~= nil then
        for i, condition in ipairs(obj.status.conditions) do
          if condition.type == "Ready" and condition.status == "False" then
            hs.status = "Degraded"
            hs.message = condition.message
            return hs
          end
          if condition.type == "Ready" and condition.status == "True" then
            hs.status = "Healthy"
            hs.message = condition.message
            return hs
          end
        end
      end
    end

    hs.status = "Progressing"
    hs.message = "Waiting for certificate"
    return hs
```

**规则要点**：

| 规则 | 官方说明 |
|---|---|
| `obj` 是全局变量 | 包含当前待检查的资源 |
| 必须返回对象 | 含 `status` 字段 + 可选 `message` |
| 键名格式 | `resource.customizations.health.<group>_<kind>`，group 与 kind 用**下划线**连接 |
| 默认返回值 | "By default health typically returns `Progressing` status" |
| **安全措施** | "access to the **standard Lua libraries** will be **disabled by default**"，需要时用 `resource.customizations.useOpenLibs.<group>_<kind>: "true"` 打开 |
| 通配符 | **只能在 `resource.customizations` 键下用**（如 `"*.aws.crossplane.io/*"`）。`resource.customizations.health.<group>_<kind>` 形式**不支持通配符**，因为 Kubernetes ConfigMap 的 key 不支持 `*` |

> **官方特别提到的坑**：**`argoproj.io/Application` 这个 CRD 的健康检查在 ArgoCD 1.8 被移除了**（官方注明 "see #3781"）。如果你用 **app-of-apps 模式**（用一个父 Application 管一堆子 Application）并依赖 sync wave 排序，**需要手动把 Application 的健康检查加回来**（官方在 Resource Health 页给了完整的 Lua 脚本）。

### 2.7 自动同步 vs 手动同步

**这是本篇要讲清、下一篇要展开的边界。** 本篇只讲机制和取舍，策略细节留给 [`L5-ArgoCD-策略与多集群`](L5-ArgoCD-策略与多集群.md)。

| 维度 | 手动同步 | 自动同步 |
|---|---|---|
| **配置** | `syncPolicy.automated` 不启用 | `syncPolicy.automated.enabled: true` |
| **触发时机** | 人点 SYNC | ArgoCD 检测到差异自动执行 |
| **谁承担风险** | 人 | 策略配置 |
| **适合** | 生产、需要评审的应用 | 开发/测试环境；或已成熟的应用 |
| **审计** | 人在操作，有工单 | Git commit 即审计 |

**自动同步的三个子开关**（官方 Application Specification 的注释原文）：

```yaml
syncPolicy:
  automated:
    enabled: true      # Enables automated syncing of the application ( true by default ).
    prune: true        # Specifies if resources should be pruned during auto-syncing ( false by default ).
    selfHeal: true     # Specifies if partial app sync should be executed when resources are changed only in
                       # target Kubernetes cluster and no git change detected ( false by default ).
    allowEmpty: false  # Allows deleting all application resources during automatic syncing ( false by default ).
```

**这四个默认值是本节最重要的信息**：

| 开关 | 默认 | 含义 |
|---|---|---|
| `enabled` | `true` | 官方注释写的是 "true by default"（在 `automated` 块存在时） |
| **`prune`** | **`false`** | **自动同步时默认不删除** Git 里已移除的资源 |
| **`selfHeal`** | **`false`** | **集群被手改后默认不自动改回** |
| **`allowEmpty`** | **`false`** | **默认不允许**自动同步删光所有资源 |

> **`allowEmpty: false` 是一道重要的安全闸**：如果 Git 里的清单被误删空了，开启自动同步会把**所有资源删掉**。`allowEmpty: false` 让这种同步**被拒绝**。
>
> **`prune: false` 的后果要理解清楚**：Git 里删掉一个 Service，开了自动同步但 `prune=false` → **Service 会留在集群里**。这看起来"更安全"，但结果是集群里堆了一堆 Git 里已经不存在的资源（**孤儿资源**），而且 ArgoCD 会一直显示 `OutOfSync`（因为它期望这个资源不存在但它还在）。
>
> **`selfHeal: false` 的后果**：有人在集群里 `kubectl edit` 改了 Deployment 的副本数 → **ArgoCD 不会自动改回来**。这正是本篇第 6 节最后一条踩坑表的**核心**。

**同步失败的重试**：官方注释给出了自动同步的默认退避 —— "retries failed attempts **5 times** with following delays between attempts ( **5s, 10s, 20s, 40s, 80s** )"。这个行为可以用 `spec.syncPolicy.retry` 调整（`limit` / `backoff.duration` / `backoff.factor` / `backoff.maxDuration`）。

### 2.8 Healthy ≠ 业务可用

**这是本篇最容易被误解、也最值得单独讲的一点。**

官方对 Deployment 的健康判定只有两条：`observedGeneration == generation` 且 `updatedReplicas == replicas`。**它不看探针、不看业务返回码、不看日志。**

```
        ArgoCD 说 Healthy 的真正含义
┌──────────────────────────────────────────────────────────┐
│  "这个 Deployment 的期望副本数已经全部更新完成，           │
│   并且 controller 已经观察到了当前这一代 spec"            │
└──────────────────────────────────────────────────────────┘
                        ↓ 它不代表
   ✗ 应用能正常响应请求
   ✗ 数据库连得上
   ✗ 业务逻辑正确
   ✗ 接口不返回 500
```

**四种"ArgoCD 说 Healthy 但业务不通"的情况**：

| 情况 | 为什么 ArgoCD 看不到 | 怎么查 |
|---|---|---|
| **探针配得太宽松** | ArgoCD 不跑探针，Deployment 的副本数到了就算 Healthy | `kubectl describe deploy` 看 probe 配置；直接访问服务 |
| **应用进程活着但业务报错** | 进程在跑、副本数够 → Healthy | 看应用日志 / 监控的 5xx 率 |
| **Service selector 写错** | Service 不是 LoadBalancer 类型 → **没有内置健康检查**；而且 Service 健康与后端连通性无关 | `kubectl get endpoints` 是否为空（对照 L2 K8s 篇的排障方法） |
| **依赖的下游挂了** | ArgoCD 只看自己管的资源 | 检查依赖服务的健康 |

> **正确的心智模型**：
> ```
> Sync Status 回答的是：「集群和 Git 一致吗？」   → 部署对不对
> Health Status 回答的是：「资源本身状态正常吗？」 → 跑起来没
> 业务可用性   回答的是：「用户能拿到服务吗？」   → 只有监控能回答
> ```
> **三者正交。** 所以生产上 **ArgoCD 的健康状态必须和业务监控（Prometheus）一起看**，不能只看 ArgoCD 显示绿色就放心。

### 2.9 权限与 RBAC

#### 2.9.1 内置 RBAC

官方 RBAC 文档的结构：

| 项 | 官方说明 |
|---|---|
| **唯一内置用户** | `admin`，超级用户，不受限访问 |
| **内置角色** | `role:readonly`（对所有资源只读）、`role:admin`（对所有资源不受限） |
| **策略存放** | `argocd-rbac-cm` ConfigMap 的 `policy.csv` |
| **项目级策略** | `AppProject.spec.roles` |
| **前提条件** | "RBAC requires **SSO configuration or local users** setup" —— **没配 SSO/本地用户时 RBAC 不生效** |

**`policy.csv` 的两种行**：

```csv
# g 行：把主体绑到角色
g, <user/group>, <role>
# p 行：给主体授权
p, <role/user/group>, <resource>, <action>, <object>, <effect>
```

**官方给的完整示例**：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: argocd-rbac-cm
  namespace: argocd
data:
  policy.csv: |
    p, my-org:team-alpha, applications, sync, my-project/*, allow
    g, my-org:team-beta, role:admin
    g, user@example.org, role:admin
    g, admin, role:admin
    g, role:admin, role:readonly
  policy.default: role:readonly
  scopes: '[groups, email]'
```

**几个必须知道的规则**：

| 规则 | 官方说明 |
|---|---|
| **角色继承** | `g, role:admin, role:readonly` → 拿到 admin 的人自动有 readonly 的权限 |
| **组必须先绑角色** | "If you want to **directly grant policies to a group, you must first assign that group to a role**"，否则 `p, <group>, ...` 不生效 |
| **`policy.default`** | 所有已认证用户至少获得该角色的权限，且**默认策略授予的权限无法被 `deny` 阻止** |
| **匿名访问** | 由 `argocd-cm` 的 `users.anonymous.enabled` 控制；官方建议此时设 `policy.default: role:unauthenticated` |
| **评估顺序** | 先评估默认策略；若默认策略已给出 allow/deny 就直接生效；否则依次评估用户、用户所属各组。**`deny` 优先于 `allow`** |
| **策略组合** | 额外键 `policy.<任意字符串>.csv` 会被拼到主 `policy.csv` 下面，按键名排序 |
| **`scopes` 默认值** | 省略时是 `[groups]` |

**资源与动作对照表**（官方 RBAC 页给的完整表格）：

| 资源 | get | create | update | delete | sync | action | override | invoke |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `applications` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| `applicationsets` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `clusters` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `projects` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `repositories` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `accounts` | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `certificates` | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `gpgkeys` | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| `logs` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `exec` | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `extensions` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

**应用级策略的对象格式**（官方）：

```csv
# 一般格式
p, <subject>, applications, <action>, <app-project>/<app-name>, <effect>

# 开启 "application in any namespace" 后
p, <subject>, applications, <action>, <app-project>/<app-ns>/<app-name>, <effect>
```

#### 2.9.2 SSO 集成

官方在 ArgoCD 总览页把 "SSO Integration (OIDC, OAuth2, LDAP, SAML 2.0, GitHub, GitLab, Microsoft, LinkedIn)" 列为特性之一。

**SSO 的工作方式**：

```
用户 ──浏览器──► ArgoCD（argocd-server）
                    │
                    │ 未登录 → 重定向到 SSO provider
                    ▼
              Dex（argocd-dex-server，内置 OIDC provider）
                    │ 用 provider 的 connector（GitHub/Google/LDAP/...）
                    ▼
                 provider 认证成功
                    │
                    │ 返回 id_token（含 groups / email 等 claim）
                    ▼
              ArgoCD 按 scopes 提取身份与组
                    │
                    ▼
              匹配 policy.csv 的 g 行 → 得到角色
```

**`scopes` 控制提取什么**（官方）：

```yaml
data:
  scopes: '[groups, email]'
```

- 用户名来自 token 的 `federated_claims.user_id` 字段。
- 组来自 OIDC provider 在配置的 scopes 下返回的值（如 `groups`、`roles`）。

**一个必须知道的官方警告**（本地用户与 SSO 的歧义）：

> "if local user `sally` is assigned to `role:admin`, and if an SSO user has a scope which happens to be named `sally`, that SSO user **will also be assigned `role:admin`**."
>
> 官方建议：**同时用本地用户和 SSO 时，给本地用户直接分配策略，不要分配角色**：
> ```
> p, my-local-user, *, *, *, allow      # ✅ 直接授权
> g, my-local-user, role:admin          # ❌ 会有歧义
> ```

**验证 RBAC 配置**（官方提供工具）：

```bash
argocd admin settings rbac validate    # 校验策略语法与可理解性
argocd admin settings rbac can         # 测试某角色/主体能否执行某操作
```

**这两个命令在 CI 里很有用** —— 改完 `policy.csv` 先 validate 再 apply。

#### 2.9.3 AppProject：项目级边界

官方 Projects 文档给出的 `AppProject` 关键字段：

| 字段 | 作用 | 匹配规则 |
|---|---|---|
| `spec.sourceRepos` | 允许的 Git 源仓库 | 支持 `!` 前缀的拒绝规则；`!*` 是**无效规则**（官方："doesn't make any sense to disallow everything"） |
| `spec.destinations` | 允许的集群 + 命名空间 | 字段是 `server` 和 `namespace`；**匹配时始终用 `server`，cluster name 不用于匹配** |
| `spec.clusterResourceWhitelist` | 允许部署的**集群级**资源（白名单） | 条目字段：`group`、`kind`、`name` |
| `spec.namespaceResourceWhitelist` | 允许部署的**命名空间级**资源（白名单） | 同上 |
| `spec.clusterResourceBlacklist` / `spec.namespaceResourceBlacklist` | 拒绝部署的资源（黑名单） | **命名空间级资源用黑名单限制，集群级资源用白名单限制**（官方原话："namespaced-scoped resources are restricted via a deny list, whereas cluster-scoped resources are restricted via an allow list"） |
| `spec.roles` | 项目级 RBAC 角色 | 策略格式必须遵守 `proj:<project-name>:<role-name>` |
| `spec.sourceNamespaces` | 允许的源命名空间 | |
| `spec.syncWindows` | 允许同步的时间窗口 | 可被子项目继承 |
| `spec.destinationServiceAccounts` | 允许使用的 ServiceAccount | 可被子项目继承 |
| `spec.permitOnlyProjectScopedClusters` | 只允许同步到同项目的 cluster-scoped cluster | |

**官方关于 sourceRepos 匹配的完整规则**：

> 1. 至少一个允许规则（不以 `!` 开头）允许该源；
> 2. **且**没有任何拒绝规则（以 `!` 开头）拒绝该源。

**destinations 的匹配规则完全一样。**

---

## 3. 动手

> **本节命令是教学示例。本次写作未安装 ArgoCD、未创建 Application、未连接任何集群。** 照做时按"看到什么算对"逐条核对。
> **本地环境口径**：kind 集群名 `day2`，节点 `day2-control-plane`，K8s v1.37.x，镜像前缀 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`。

### 3.1 安装

```bash
# ① 建命名空间
kubectl create namespace argocd

# ② 安装（必须带 --server-side --force-conflicts）
kubectl apply -n argocd --server-side --force-conflicts \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# ③ 等 Pod 就绪
kubectl get pods -n argocd -w

# 看到什么算对：所有 Pod 都是 Running，READY 列显示 1/1
#   argocd-application-controller-xxx   1/1  Running
#   argocd-repo-server-xxx               1/1  Running
#   argocd-server-xxx                    1/1  Running
#   argocd-redis-xxx                     1/1  Running

# ④ 确认 CRD 已就位（这一步很关键）
kubectl get crd | grep argoproj.io
# 看到什么算对：至少能看到 applications.argoproj.io、appprojects.argoproj.io
```

### 3.2 登录与添加仓库

```bash
# ① 取初始 admin 密码
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo

# 看到什么算对：打印出一串随机密码（不是空）

# ② 端口转发访问 UI / API
kubectl port-forward svc/argocd-server -n argocd 8080:443
# 浏览器打开 https://localhost:8080，用 admin + 上面的密码登录

# ③ CLI 登录（也可以用 --core 直连 K8s，不经过 API server）
argocd login localhost:8080 --username admin --password '<上一步的密码>' --insecure
# 或者：
# argocd login --core

# ④ 添加仓库（公有）
argocd repo add https://github.com/argoproj/argo-cd-example-apps.git

# ⑤ 添加私有仓库（SSH 只读 deploy key）
argocd repo add git@github.com:your-org/your-app.git \
  --ssh-private-key-path ~/.ssh/argocd_deploy_key

# 看到什么算对：argocd repo list 里出现该仓库，TYPE=git，STATUS=Successful
```

> **官方提醒**：初始密码存放在 `argocd-initial-admin-secret` 这个 Secret 的 `password` 字段里（明文）。**改完密码后官方建议删掉它**："The secret serves no other purpose than to store the initially generated password in clear and can safely be deleted at any time. It will be re-created on demand by Argo CD if a new admin password must be re-generated."

### 3.3 建 Application → 看 diff → 同步

```bash
# ① 建 Application（手动同步）
argocd app create guestbook \
  --repo https://github.com/argoproj/argo-cd-example-apps.git \
  --path guestbook \
  --dest-server https://kubernetes.default.svc \
  --dest-namespace default \
  --project default

# ② 看状态 —— 官方文档里的原始输出（第一次看应该是这个形态）
argocd app get guestbook
```

官方 Getting Started 给出的**新建后未同步**时的输出长这样（这是文档里的示意输出，不是本资料库实测）：

```
Name:               guestbook
Server:             https://kubernetes.default.svc
Namespace:          default
URL:                https://10.97.164.88/applications/guestbook
Repo:               https://github.com/argoproj/argo-cd-example-apps.git
Target:
Path:               guestbook
Sync Policy:        <none>
Sync Status:        OutOfSync from  (1ff8a67)
Health Status:      Missing

GROUP  KIND        NAMESPACE  NAME          STATUS     HEALTH
apps   Deployment  default    guestbook-ui  OutOfSync  Missing
       Service     default    guestbook-ui  OutOfSync  Missing
```

> **注意这个输出里的两个关键信息**：
> - `Sync Status: OutOfSync` —— 因为还没同步，**这是正常的**。
> - `Health Status: Missing` —— 因为资源还不存在。官方原话："The application status is initially in `OutOfSync` state since the application has yet to be deployed, and **no Kubernetes resources have been created**."

```bash
# ③ 看最终渲染出来的 manifest（不碰集群）
argocd app manifests guestbook

# ④ 看 diff（这一步是纪律：人必须看）
argocd app diff guestbook

# ⑤ 同步
argocd app sync guestbook
# 官方说明："This command retrieves the manifests from the repository and performs a `kubectl apply` of the manifests."

# ⑥ 看结果
argocd app get guestbook
# 看到什么算对：Sync Status: Synced
#              Health Status: Healthy
#              资源列表里 STATUS=Synced、HEALTH=Healthy

# ⑦ 集群里验证
kubectl get deploy,svc -n default
kubectl get endpoints guestbook-ui -n default
# 看到什么算对：endpoints 非空（对照 L2 K8s 篇：endpoints 空 = selector 没匹配上）
```

### 3.4 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| CRD 已注册 | `kubectl get crd` 后筛 `argoproj.io` | 有 `applications.argoproj.io` 等 |
| 组件就绪 | `kubectl get pods -n argocd` | 全部 `1/1 Running` |
| 能登录 | `argocd app list` | 返回应用列表（空列表也算成功） |
| 仓库可用 | `argocd repo list` | `STATUS=Successful` |
| 渲染正常 | `argocd app manifests <app>` | 输出完整的 YAML 清单 |
| **diff 可读** | `argocd app diff <app>` | 能看出"要创建什么" |
| 同步成功 | `argocd app get <app>` | `Sync Status: Synced` |
| 健康正常 | `argocd app get <app>` | `Health Status: Healthy` |
| 资源真的在 | `kubectl get deploy -n <ns>` | 副本数达到期望 |
| 后端有端点 | `kubectl get endpoints <svc> -n <ns>` | 非空 |

---

## 4. 逐字段表

### 4.1 Application spec 关键字段表

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: argoproj.io/v1alpha1` | string | ✅ | API 组与版本 | 写错 → `no matches for kind "Application"` |
| `kind: Application` | string | ✅ | 资源类型 | 同上 |
| `metadata.name` | string | ✅ | 应用名 | 同名 → AlreadyExists |
| `metadata.namespace` | string | | Application 自己所在命名空间，通常是 `argocd` | 放错 → ArgoCD 看不到它（watch 范围问题） |
| `metadata.finalizers` | list | | 级联删除标记 | 加了 `resources-finalizer.argocd.argoproj.io` → 删 Application 会连带删资源；**官方说"ONLY if you want these to cascade delete"** |
| `spec.project` | string | | 属于哪个 AppProject | 指向不存在的 project → Application 被拒绝；不写 → 落到 `default` |
| `spec.source.repoURL` | string | ✅ | Git 仓库或 Helm 仓库地址 | 写错 → repo-server 拉不到 → `Failed to load target state` |
| `spec.source.targetRevision` | string | | 分支 / 标签 / commit；**Helm 源时是 chart 版本** | 写错分支 → 拉不到或拉到旧版本 |
| `spec.source.path` | string | | 仓库内的路径 | **Helm repo 源时无效**；路径不存在 → 渲染出空 → 同步后资源全被 prune |
| `spec.source.directory.recurse` | bool | | 是否递归子目录 | 不开 → 子目录里的 yaml 不被发现 |
| `spec.source.helm.valueFiles` | list | | Helm values 文件列表（相对 path） | 路径写错 → Helm 用默认值 → 资源参数不对（**不报错**） |
| `spec.source.helm.parameters` | list | | Helm `--set` 风格的参数 | `forceString: true` 缺失 → "true" 变成布尔，YAML 渲染成 `true` 而不是字符串 `"true"` |
| `spec.source.helm.version` | string | | Helm 二进制版本 v2/v3 | 与 chart 要求不符 → 渲染失败 |
| `spec.source.kustomize.*` | block | | Kustomize 版本、namePrefix、images 等 | `version` 必须在 `argocd-cm` 里配过，否则不生效 |
| `spec.sources` | list | | 多源写法（取代单 `source`） | 与 `source` 混用 → 行为不确定 |
| `spec.destination.server` | string | ✅ | 目标集群 API 地址 | 写错 → 同步到别的集群或连不上；**项目匹配只认 server** |
| `spec.destination.namespace` | string | | 目标命名空间 | **只对没写 `.metadata.namespace` 的命名空间级资源生效** |
| `spec.syncPolicy.automated.enabled` | bool | | 自动同步开关 | 开了就没人能拦变更了 |
| `spec.syncPolicy.automated.prune` | bool | | 自动同步时是否删除 Git 里已移除的资源 | **默认 false**；开了才有 prune 语义 |
| `spec.syncPolicy.automated.selfHeal` | bool | | 集群被手改后是否自动改回 | **默认 false**；不开启 → 手改永久保留（且 ArgoCD 显示 OutOfSync） |
| `spec.syncPolicy.automated.allowEmpty` | bool | | 是否允许自动同步删光所有资源 | **默认 false**；设 true 时 Git 清单被清空 = 删光集群资源 |
| `spec.syncPolicy.syncOptions` | list | | 同步选项（`CreateNamespace=true`、`PrunePropagationPolicy=foreground`、`Replace=true`、`ServerSideApply=true`、`ApplyOutOfSyncOnly=true`、`FailOnSharedResource=true` 等） | `Replace=true` 有破坏性（官方警告"might lead to resources having to be recreated"）；**`Replace=true` 优先于 `ServerSideApply=true`** |
| `spec.syncPolicy.retry.limit` | number | | 失败重试次数 | **小于 0 表示无限重试** |
| `spec.syncPolicy.retry.backoff` | block | | 退避策略：`duration` / `factor` / `maxDuration` | 间隔太短 → 重试时资源还没就绪 → 全部失败 |
| `spec.syncPolicy.managedNamespaceMetadata` | block | | 自动创建的命名空间上的 labels/annotations | **仅在 `CreateNamespace=true` 时有效**，否则是 no-op |
| `spec.ignoreDifferences` | list | | diff 时忽略的差异（`group`/`kind`/`jsonPointers`/`jqPathExpressions`/`managedFieldsManagers`） | **默认只在 diff 阶段生效**；同步阶段要生效需加 `RespectIgnoreDifferences=true` |
| `spec.revisionHistoryLimit` | number | | 保留多少条历史版本 | **设为 0 则不存历史 → 无法回滚**；调大会占更多存储 |
| `status.sync.status` | enum | | `Synced` / `OutOfSync` / `Unknown` | 只读 |
| `status.health.status` | enum | | 六个健康状态 | 只读 |

### 4.2 常用 CLI 命令表

**全局命令**：

| 命令 | 用途 | 备注 |
|---|---|---|
| `argocd login [hostname]` | 登录 ArgoCD API server | 不带 hostname 默认 `app.terraform.io`… 准确说：官方文档指 `terraform login`，ArgoCD 的 `argocd login` 需给 ArgoCD 地址 |
| `argocd login --core` | 直接对着 K8s API 操作，**不经过 API server** | 适合排障：API server 挂了还能用 |
| `argocd logout` | 退出登录 | |
| `argocd version` | 版本信息 | |
| `argocd account update-password` | 改密码 | |
| `argocd admin initial-password -n argocd` | 取初始 admin 密码 | |
| `argocd admin settings rbac validate` | 校验 RBAC 策略 | CI 里好用 |
| `argocd admin settings rbac can` | 测试某主体能否执行某操作 | |

**`argocd app` 子命令**（官方 `argocd app` 参考页的 SEE ALSO 完整列表）：

| 命令 | 用途 | 关键 flag / 说明 |
|---|---|---|
| `argocd app list` | 列出所有 Application | |
| `argocd app get <app>` | 看应用详情（含 Sync/Health 状态、资源树） | |
| `argocd app describe <app>` | 详细描述 | |
| `argocd app create <app>` | 创建 Application | `--repo` / `--path` / `--dest-server` / `--dest-namespace` / `--project` |
| `argocd app delete <app>` | 删除 Application | ⚠️ 加了 finalizer 会**级联删除集群资源** |
| `argocd app edit <app>` | 编辑 | |
| `argocd app set <app>` | 设置参数 | 例：`argocd app set guestbook --sync-option ApplyOutOfSyncOnly=true` |
| `argocd app unset <app>` | 取消参数 | |
| `argocd app patch <app>` | 打 patch | |
| `argocd app sync <app>` | **同步** | `--revision` / `--prune` / `--dry-run` |
| `argocd app diff <app>` | **看差异** | 本篇强调：同步前必看 |
| `argocd app manifests <app>` | 打印渲染后的 manifest | 排查渲染问题的第一手段 |
| `argocd app resources <app>` | 列出应用管理的资源 | |
| `argocd app get-resource <app>` | 看某个资源的 live manifest | |
| `argocd app patch-resource <app>` | 改某个资源 | |
| `argocd app delete-resource <app>` | 删某个资源 | |
| `argocd app history <app>` | 部署历史 | 回滚的依据 |
| `argocd app rollback <app>` | **回滚**到历史版本（可给 ID，省略则回上一个） | |
| `argocd app wait <app>` | 等到 Synced + Healthy | CI 里很有用 |
| `argocd app logs <app>` | 看应用 Pod 日志 | |
| `argocd app actions <app>` | 管理资源动作 | |
| `argocd app confirm-deletion <app>` | 确认删除 / prune | 配合 `Prune=confirm` 使用 |
| `argocd app add-source` / `remove-source` | 多源应用的源增删 | |
| `argocd app terminate-op <app>` | 终止正在进行的操作 | |

**其他对象**：

| 命令 | 用途 |
|---|---|
| `argocd repo add` / `list` / `remove` / `update` | 管理仓库凭据 |
| `argocd project create` / `list` / `delete` / `update` | 管理 AppProject |
| `argocd cluster add` / `list` / `remove` | 注册目标集群 |
| `argocd account list` / `update-password` | 管理本地用户 |
| `argocd version` | 版本 |

**`argocd app` 的通用连接 flag**（排障时很有用）：

| flag | 用途 |
|---|---|
| `--core` | 直连 K8s，绕过 API server |
| `--grpc-web` | API server 在代理后面（不支持 HTTP/2）时用 |
| `--port-forward` / `--port-forward-namespace` | 自动端口转发到 API server |
| `--insecure` | 跳过服务端证书与域名校验 |
| `--plaintext` | 用明文 HTTP |
| `--auth-token` | 用 token 而不是密码（CI 场景） |
| `--loglevel` | `debug` / `info` / `warn` / `error` |
| `--request-timeout` | 单请求超时（默认 `0` = 不超时） |

---

## 5. 横向对比

### 5.1 ArgoCD vs Flux vs Jenkins X

| 维度 | **ArgoCD** | Flux | Jenkins X |
|---|---|---|---|
| **形态** | 独立的 CD 控制器（CRD 驱动） | 一组 K8s Operator（多个 CRD） | Jenkins + GitOps 插件（把 Jenkins 流水线当引擎） |
| **核心对象** | `Application` / `AppProject` / `ApplicationSet` | `GitRepository` / `Kustomization` / `HelmRelease` / `HelmChart` | Jenkins Job + Pipeline + GitOps Agent |
| **工作单元** | 一个 Application = 一个应用 | 一个 `Kustomization` / `HelmRelease` = 一个应用 | 一个 Job = 一个应用 |
| **模板化** | **ApplicationSet**（内置 CRD，生成 Application） | Flux 自带多种 controller（Kustomization/HelmRelease…） | Job DSL / JCasC |
| **UI** | **有完整 Web UI**（资源树、diff、日志、终端） | **没有官方 UI**（用 Flux Dashboard 之类的第三方） | Jenkins UI |
| **多集群** | 内置（Cluster Secret + ApplicationSet） | 内置（每集群装 Flux） | 靠插件 |
| **与 CI 的关系** | **完全解耦**（纯拉模型） | **完全解耦** | **强耦合**（GitOps 由 Jenkins 触发，仍是推模型的一种变体） |
| **学习曲线** | 中（概念多但概念少） | 中（组件多、CRD 多） | 高（Jenkins 生态庞大） |
| **适合** | 想要 UI、要直观看到 diff 和资源树的团队 | 喜欢 GitOps Operator 风格、想要各功能独立控制的团队 | 已有 Jenkins、想保留现有流水线体系 |

> **ArgoCD vs Flux 的核心差别（一句话）**：
> **ArgoCD 是"一个控制器管所有事 + 一个 UI 给你看"，Flux 是"一组各管一件事的 Operator"。** 前者简单直观，后者灵活但组件多。
>
> **Jenkins X 的定位不同**：它不是纯粹的 GitOps 工具，而是"把 Jenkins X 当 CI/CD 平台，其中 GitOps 部分用 Tekton/Argo 做"。**如果团队已经在用 Jenkins，Jenkins X 的迁移成本最低；但如果从零开始，ArgoCD + 一个纯 CI（GitLab CI）通常更清爽。**

> **本小节的 Flux / Jenkins X 部分标 `⚠️ 单源待补`** —— 本资料库 `09-参考索引/README.md` 中目前没有 Flux 与 Jenkins X 的官方链接条目，此处为通识性对比，**具体行为以各自官方文档为准**。

### 5.2 ArgoCD vs Helm

**这两个不是竞争关系，是上下游 + 不同层次。**

| 维度 | Helm | ArgoCD |
|---|---|---|
| **它是什么** | **模板渲染器 + 打包格式**（Chart） | **持续交付控制器**（GitOps） |
| **解决什么** | "把一堆 yaml + 参数渲染成最终 manifest" | "让集群的实际状态持续等于 Git 里的期望状态" |
| **谁执行渲染** | `helm template`（本地）或 ArgoCD 的 repo-server（远程） | 调 Helm 或 Kustomize 渲染 |
| **一次性 vs 持续** | `helm upgrade` 是**一次性**动作 | **持续** reconcile |
| **漂移检测** | ❌ `helm upgrade` 之后没人再管了 | ✅ **一直在对比** |
| **回滚** | `helm rollback <release> <revision>` | `argocd app rollback`（切回 Git 历史版本） |
| **谁的状态说了算** | Helm release 的 release secret | **Git** |

**正确的组合姿势**：

```
Git 仓库
 ├── charts/myservice/       ← Helm Chart（模板 + values）
 └── gitops/myservice.yaml   ← Application 定义
                                  │
                                  ▼
                         ArgoCD repo-server
                                  │  helm template + values
                                  ▼
                          最终 manifest
                                  │
                                  ▼
                         diff → apply → 持续 reconcile
                                  │
                                  ▼
                              集群
```

**官方 ArgoCD 总览页列出的支持的源类型**：

> "Kubernetes manifests can be specified in several ways: **kustomize** applications, **helm** charts, **jsonnet** files, **Plain directory of YAML/json manifests**, Any custom config management tool configured as a **config management plugin**"

**为什么 ArgoCD 里用 Helm 比在 CI 里用 Helm 好**（这是面试的加分回答）：

| 维度 | CI 里 `helm upgrade` | ArgoCD 里用 Helm 源 |
|---|---|---|
| 渲染在哪 | CI Runner 上 | **ArgoCD repo-server 上**（集群内） |
| 渲染结果可见性 | CI 日志里看 | `argocd app manifests` 直接看，**UI 上还能 diff** |
| 参数配置 | `helm upgrade --set ...`（命令行，评审时看不见） | **values 文件进 Git**，diff 看得到 |
| 集群凭据 | CI 持有 | **只有 ArgoCD 持有** |
| 漂移 | 发现不了 | 自动纠正 |

### 5.3 两种 source 类型：同步目录 vs 渲染后的 manifest

**Application 的 `spec.source` 有两种本质不同的形态**，这是本节的核心。

| 维度 | **① 目录型（Directory source）** | **② 渲染型（Helm / Kustomize / Jsonnet / plugin）** |
|---|---|---|
| **Git 里存什么** | **最终的 K8s manifest**（含 Deployment/Service 的完整 yaml） | **模板 + 参数**（Chart / kustomization.yaml） |
| **谁渲染** | 无需渲染，直接读 | **ArgoCD repo-server**（`helm template` / `kustomize build`） |
| **ArgoCD 里要配什么** | `spec.source.path` + `directory` | `spec.source.path` + `helm` / `kustomize` 块 |
| **参数在哪** | **就在 yaml 里**（改参数 = 改 yaml） | **独立的 values 文件**（`spec.source.helm.valueFiles`） |
| **典型目录结构** | `apps/myservice/deployment.yaml` | `charts/myservice/` + `values/` |
| **谁写这个 yaml** | 人（或者别的工具生成） | **Helm 模板引擎**（`<br>` `{{ .Values.x }}`） |
| **优点** | **所见即所得**，diff 直观，无隐式逻辑 | **参数化复用**，同一份模板多环境 |
| **缺点** | 多环境要复制多份 yaml，重复 | 渲染有隐式逻辑，diff 看到的是"渲染结果" |

**官方 Application Specification 里对应的字段**（这组区分是官方明确的）：

```yaml
# ① 目录型
source:
  repoURL: https://github.com/org/repo.git
  path: apps/myservice
  directory:
    recurse: true
    include: '*.yaml'
    exclude: 'config.yaml'

# ② Helm 型（Git 里的 chart）
source:
  repoURL: https://github.com/org/repo.git
  path: charts/myservice
  targetRevision: HEAD
  helm:
    valueFiles:
      - values-prod.yaml
    values: |            # 或直接内联 values（优先于 valueFiles）
      replicaCount: 3

# ② Helm 型（直接指 Helm 仓库）
source:
  repoURL: https://charts.helm.sh/stable
  chart: chart-name      # ← 设这个时 path 无意义
  targetRevision: 1.2.3  # ← 这时它是 chart 版本
```

**官方对 `chart` 字段的注释（很关键）**：

> "Set this when pulling directly from a Helm repo. **DO NOT set for git-hosted Helm charts.**"

**以及 `path` 在 Helm 仓库场景下**：

> "This has **no meaning for Helm charts** pulled directly from a Helm repo instead of git."

**选型建议**：

| 场景 | 选哪种 |
|---|---|
| 简单的、每个环境就一两个参数的内部工具 | **目录型**（少一层渲染，排查直观） |
| 同一个应用要部署到 5 个环境，参数差 10 个 | **Helm 型**（values 文件分离） |
| 需要 base/overlay 组合 | **Kustomize 型** |
| 已有 Helm chart 要复用 | **Helm 型**（ArgoCD 直接渲染它） |

> **一个常见误解**："用了 Helm 就不能看 diff 了。" **不对。** ArgoCD 的 diff 是**渲染后的结果 vs 集群实际状态** —— 你看到的是最终 manifest 的差异，**完全可读**。你看不到的是"模板本身的变化"（改了 `{{if}}` 但结果没变 → diff 为空 → **不会同步，也不需要同步**，因为现实没变）。
>
> **这就是渲染型 source 的一个真实风险**：模板改了但渲染结果一样 → ArgoCD 认为"无差异" → **不同步**。这不是 bug（现实确实没变），但如果你**期望**某个副作用（比如改了 `{{if}}` 触发了一个 hook Job 的名字变化），就要小心。**对有副作用的场景，用 `helm template` 在本地先渲染一次对比。**

---

## 6. 踩坑与排障

> 下表报错/提示文字均为**官方文档、官方 issue 或社区中出现的典型文本**，标注了性质；本资料库未在环境实测，因此不写"实测输出"。

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `kubectl apply` 装 ArgoCD 报 annotation too long / 请求体过大 | apply 失败 | **客户端 apply 有 262KB 的 annotation 大小限制**，而 ArgoCD 的 CRD（尤其 ApplicationSet）超过了这个限制 | 加 `--server-side --force-conflicts`（官方明确要求这两个 flag） | 固定用官方那一条命令，别自己简化 |
| `no matches for kind "Application"` / `resource mapping not found` | kubectl 报错 | **CRD 没装或没装全**（常见于手动分步安装时把 CRD 放在 ArgoCD 本体之后） | 用 `kubectl get crd` 确认 `argoproj.io` 相关 CRD 是否存在；重装 `install.yaml`（CRD 在文件前部） | 安装顺序固定：namespace → CRD → 本体；用官方 `install.yaml` 一次装完 |
| Application 一直 `Progressing`，资源没起来 | `Progressing` | ① 镜像拉不下来（私有仓库无凭据 / tag 不存在 / 架构不匹配）；② 探针没过；③ 调度不上 | `kubectl describe pod` 看 Events；对照 L2 K8s 篇的"排障标准动作"四步 | 镜像地址用 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/` 前缀；同步前先确认镜像可拉通 |
| Application 显示 `Unknown` / `Failed to load target state` | 应用报错 | **repo-server 连不上仓库**：凭据错、SSH key 没进 Pod、网络不通、证书不受信 | `argocd repo list` 看 STATUS；`kubectl -n argocd logs deploy/argocd-repo-server` | 私库用**只读 deploy key**；**不用 `--insecure-skip-server-verification`**；把 CA 装进信任链 |
| 私有仓库一直拉不下来，日志报 `host key verification failed` | repo-server 报错 | **SSH host key 未信任** 或私钥格式/权限不对 | 把 Git 服务器的 host key 加进 ArgoCD 容器；私钥放 Secret 并确保权限 | 提前把 host key 与私钥都做成 Secret；**别用 `--insecure-ignore-host-key`**（官方已标 deprecated） |
| 同步报 `namespace "xxx" not found` | 同步失败 | **目标命名空间不存在**。ArgoCD **默认不创建命名空间** | 手工 `kubectl create ns xxx`；或给 Application 加 `syncOptions: [CreateNamespace=true]` | 生产统一用 `CreateNamespace=true` + `managedNamespaceMetadata` 打标签 |
| 同步后资源与预期不一致 / 部分资源没建 | 部分成功 | **hook 失败**：PreSync hook 失败会**中断整个同步流程**（官方："If any of them fails the whole sync process will stop and will be marked as failed"）；或者 CRD 缺失导致 CR 资源创建失败 | `argocd app get <app>` 看 Operation 的 phase 和 message；查失败 hook 的 Pod 日志 | DB 迁移这类操作放 PreSync hook 并配 `hook-delete-policy: HookSucceeded`；上 CRD 之前先同步 CRD（用 sync-wave 或独立 Application） |
| 资源被删了又建 / prune 顺序不对 | 同步异常 | **prune 的顺序与创建顺序相反**（官方："During pruning, the wave order is **reversed** from the creation order. Resources in higher waves are pruned **first**"）；默认 prune 用 **foreground** 删除策略 | 给资源分 sync-wave，让依赖方 wave 更大；同步前看 diff 里的 prune 列表 | 用 `PruneLast=true` 让 prune 作为最后一个隐式 wave 执行 |
| 同步成功但 Application 仍是 `OutOfSync` | `OutOfSync` | 官方列的原因：manifest 有 K8s 不认的字段被丢弃、**mutating webhook 改了对象**、Helm 模板用了 `randAlphaNum` 这类每次都变的函数、**HPA controller 重排 `spec.metrics`** | 看 diff 内容定位是哪一类；固定随机值（用 `lookup` 或固定 seed）；HPA 的 metrics 顺序按 controller 偏好写 | 用 `ignoreDifferences` 精确忽略（`jsonPointers` 或 `managedFieldsManagers`）；**不要全局忽略** |
| **UI 显示 Healthy，但用户访问不通** | `Healthy` | **ArgoCD 的健康检查只看副本数和 generation，不看业务可用性**（内置 Deployment 检查就两条）。另外 ClusterIP 类型的 Service **没有内置健康检查** | 直接访问服务验证；查 `kubectl get endpoints` 是否有后端；看应用日志和业务监控 | **把 ArgoCD 健康状态和 Prometheus 业务指标一起看**；部署后必查 endpoints；用 `kubectl port-forward` 做一次真实访问验证 |
| **在集群里 `kubectl edit` 改了资源，过一会儿又被改回去了** | 又变回原样 | **SelfHeal 在起作用**（`syncPolicy.automated.selfHeal: true`）。这正是 GitOps 的核心机制 | 想保留手改 → ① 把它写进 Git；② 或关闭 selfHeal | **这就是 GitOps 的纪律：集群里不手改，要改就改 Git。** 用 `argocd app diff` 确认改动已被 Git 覆盖 |
| **在集群里 `kubectl edit` 改了资源，一直没被改回去** | 手改保留 | **`selfHeal` 默认是 `false`** —— 官方注释："false by default"。没开 selfHeal，ArgoCD 不会自动纠正 | 二选一：① 开 `selfHeal: true`（让 Git 说了算）；② 关掉自动同步，改手工模式 + 定期 review diff | **明确写下你们团队的策略**：集群内直改到底算不算数。**默认应该算"必须回写 Git"** |
| Git 里删了资源，集群里还在 | 资源未删 | **`prune` 默认 `false`** —— 自动同步不删 Git 里已移除的资源 | 手工同步时选 prune；或开 `automated.prune: true` | **生产慎开自动 prune**：先在预发验证；给关键资源加 `Prune=false` 注解 |
| Git 清单被误清空，自动同步后资源全被删 | 资源消失 | **`allowEmpty` 默认 `false`** 是保护闸；如果你把它设成了 `true`，同步会**删光所有资源** | 立刻回滚 Git（`argocd app rollback`） | **永远不要把 `allowEmpty` 设成 `true`**；给生产 Application 加同步窗口 `syncWindows` |
| 应用显示 `OutOfSync` + `Missing`，但资源其实在集群里 | `Missing` | 资源被手工删了（ArgoCD 发现 Git 里有、集群里没有） | 直接同步（会重建）；或 `argocd app sync` | 查是谁删的 —— 这类"误删"是 GitOps 要解决的核心问题 |
| `argocd app sync` 报权限错误 / 资源创建失败 | 同步失败 | **ArgoCD 在目标集群的 ServiceAccount 权限不足**（`argocd-manager` 绑的角色不够） | `argocd cluster add` 会安装 `argocd-manager` ServiceAccount 并绑 admin 级 ClusterRole；可按官方说明收窄权限（但 `get`/`list`/`watch` 必须是 cluster-scope） | 注册集群时权限一次给足；不要手删那个 ClusterRoleBinding |
| Webhook 没触发同步 | 应用没及时更新 | 仓库没配 webhook，或公网入口不通 | 在 ArgoCD UI 配 webhook（Settings → Webhook）；或用定时轮询兜底 | 有公网入口就配 webhook（能秒级触发）；纯内网就接受轮询延迟 |
| 渲染结果与本地 `helm template` 不一样 | diff 异常 | **repo-server 里的 Helm 版本 / values 文件与本地不同**；或 `valuesObject` 与 `values` 优先级不同 | `argocd app manifests <app>` 看实际渲染结果；核对 `spec.source.helm.version` | **用 `argocd app manifests` 作为唯一真相**，不要用本地渲染结果对比；把 Helm 版本写进 `spec.source.helm.version` |
| 私有仓库用 HTTPS 拉不到，日志报 401 | repo-server 报错 | 凭据是**个人账号密码**或 token 权限不足 / 已过期；或用了 `--force-http-basic-auth` 与仓库不兼容 | 换用最小权限 PAT 或 deploy key；核对 `--username` | 用只读凭据；定期轮换；**绝不用个人主账号密码** |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `argo-cd.readthedocs.io/en/stable/` | 总览：Argo CD 定义、Why Argo CD（声明式 + 版本控制 + 自动化 + 可审计）、How it works（Git 为 source of truth、支持的 manifest 类型：kustomize/helm/jsonnet/plain-YAML/config management plugin）、Architecture（持续对比 live state 与 target state，偏离即 `OutOfSync`）、Features（含 SSO 集成列表、多集群、Rollback、Health status analysis、漂移检测、PreSync/Sync/PostSync hooks） | ✅ 2026-10-24 |
| S2 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/sync-options/` | 同步选项（资源级注解 `argocd.argoproj.io/sync-options` 与 `spec.syncPolicy.syncOptions`）、`Prune=false`/`Prune=confirm`/`PruneLast=true`/`PrunePropagationPolicy`（background/foreground/orphan）、`Replace=true` 与 `ServerSideApply=true` 的行为与**优先级**、`Delete=false`/`Delete=confirm`、`ApplyOutOfSyncOnly=true`、`FailOnSharedResource=true`、`RespectIgnoreDifferences=true` | ✅ 2026-10-24 |
| S3 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/application-specification/`（Application Specification Reference） | Application 完整字段：`spec.project`/`source`（repoURL/path/targetRevision/directory/helm/kustomize/plugin）/ `sources` / `destination`（server/namespace）/ `syncPolicy.automated`（enabled/prune/selfHeal/allowEmpty 及其**默认 false** 说明）/ `syncOptions` / `retry`（limit 与 backoff，默认 5 次、5s/10s/20s/40s/80s 退避）/ `ignoreDifferences` / `revisionHistoryLimit` / `finalizers` / `managedNamespaceMetadata` | ✅ 2026-10-24 |
| S4 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd_app/`（argocd app 命令参考） | `argocd app` 全部子命令清单（list/get/create/delete/edit/set/unset/patch/sync/diff/manifests/resources/get-resource/patch-resource/delete-resource/history/rollback/wait/logs/actions/confirm-deletion/add-source/remove-source/terminate-op）、通用连接 flag（`--core`/`--grpc-web`/`--port-forward`/`--insecure`/`--auth-token`/`--request-timeout`） | ✅ 2026-10-24 |
| S5 | 官方 | `argo-cd.readthedocs.io/en/stable/operator-manual/health/`（Resource Health） | 内置健康检查规则（Deployment/ReplicaSet/StatefulSet/DaemonSet、Service LoadBalancer、Ingress、CronJob、Job suspended、PVC Bound）、自定义健康检查 Lua 脚本格式（`obj` 全局变量、返回 `hs.status`/`hs.message`）、`argocd-cm` 的 `resource.customizations.health.<group>_<kind>` 键格式、useOpenLibs 安全开关、通配符限制、**argoproj.io/Application 健康检查自 1.8 被移除**（app-of-apps 需手动恢复） | ✅ 2026-10-24 |
| S6 | 官方 | `argo-cd.readthedocs.io/en/stable/getting_started/`（Getting Started） | 安装命令与 `--server-side --force-conflicts` 的官方理由（**262KB annotation 限制**）、CRD 先于本体、namespace 创建、`argocd-initial-admin-secret` 与 `argocd admin initial-password`、Redis 密码在 `argocd-redis` Secret 的 `auth` 键、端口转发与 LoadBalancer/Ingress 三种访问方式、`argocd cluster add` 会安装 `argocd-manager` ServiceAccount 并绑 admin 级 ClusterRole（get/list/watch 必须 cluster-scope）、**新建后未同步时的 `argocd app get` 官方输出**（`Sync Status: OutOfSync` / `Health Status: Missing`） | ✅ 2026-10-24 |
| S7 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/projects/`（Projects） | AppProject 字段（`sourceRepos`/`destinations`/`clusterResourceWhitelist`/`namespaceResourceWhitelist`/`namespaceResourceBlacklist`/`roles`/`syncWindows`/`sourceNamespaces`/`destinationServiceAccounts`/`permitOnlyProjectScopedClusters`）、**允许+拒绝规则的匹配逻辑**与 `!*` 无效规则、**"集群匹配始终用 server，name 不用于匹配"**、**"命名空间级资源用黑名单、集群级资源用白名单"**、Repository/Cluster 以 Secret 存储及其字段（url/type/username/password/project/name、server/config/bearerToken/tlsClientConfig） | ✅ 2026-10-24 |
| S8 | 官方 | `argo-cd.readthedocs.io/en/stable/operator-manual/rbac/`（RBAC Configuration） | 内置角色（`role:readonly`/`role:admin`）、`argocd-rbac-cm` 的 `policy.csv` 语法（`g`/`p` 行）、角色继承、组必须先绑角色、`policy.default` 及其不可 deny、匿名访问、`scopes`（默认 `[groups]`）、资源与动作对照表、应用级策略对象格式、`policy.<name>.csv` 组合、评估顺序（deny 优先）、本地用户与 SSO 的歧义警告、`argocd admin settings rbac validate`/`can` | ✅ 2026-10-24 |
| S9 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/`（Sync Phases and Waves） | Hook 类型（PreSync/Sync/Skip/PostSync/SyncFail/PreDelete/PostDelete）、`argocd.argoproj.io/hook` 与 `argocd.argoproj.io/hook-delete-policy`（HookSucceeded/HookFailed/BeforeHookCreation）、`argocd.argoproj.io/sync-wave`（含负值、**wave 间延迟 2 秒，可用 `ARGOCD_SYNC_WAVE_DELAY` 调整**）、排序优先级（phase → wave → kind → name）、**prune 时 wave 顺序反转**、hook 失败会中断整个同步 | ✅ 2026-10-24 |
| S10 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/diffing/`（Diffing Customization） | **"应用可能在同步成功后仍是 OutOfSync"** 的官方原因清单（多余字段、禁用 prune、mutating webhook、Helm `randAlphaNum`、HPA 重排 metrics）、`ignoreDifferences` 的 jsonPointers/jqPathExpressions/managedFieldsManagers 用法、系统级 `resource.customizations.ignoreDifferences`、`resource.compareoptions` 的 `ignoreResourceStatusField` | ✅ 2026-10-24 |
| S11 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/commands/argocd_repo_add/`（argocd repo add 命令参考） | `argocd repo add` 的用法与 flag：`--type`（git/oci/helm）、`--name`（helm 必填）、`--username`/`--password`、`--ssh-private-key-path`、`--tls-client-cert-path`/`--tls-client-cert-key-path`、`--insecure-skip-server-verification`、`--insecure-ignore-host-key`（**官方标 deprecated**）、`--enable-lfs`、`--enable-oci`、`--project`、`--upsert` | ✅ 2026-10-24 |
| S12 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/application-set/`（Generating Applications with ApplicationSet） | ApplicationSet controller 的定位（自动生成 Application、支持多集群与多租户）、CRD `apiVersion: argoproj.io/v1alpha1` / `kind: ApplicationSet`、list/cluster/git generator、`goTemplate` 与 `goTemplateOptions`、**官方安全提示**（开放给开发者创建 Application 前要评估安全影响） | ✅ 2026-10-24 |
| S13 | 官方 | `kubernetes.io/zh-cn/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/` | CRD 的概念与安装（ArgoCD 的 Application/AppProject 都是 CRD，因此 CRD 必须先于 ArgoCD 本体存在） | ✅ 2026-10-24 |
| S14 | 中文/云厂商 | `developer.aliyun.com` · `cloud.tencent.com/developer` · `help.aliyun.com/zh/`（容器云 / CODAS 文档） | ArgoCD 的中文实践与托管形态、阿里云/腾讯云的 ArgoCD 落地形态 | ✅ 2026-10-24 |

> **说明**：① 本篇引用的 ArgoCD 文档均位于 `argo-cd.readthedocs.io/en/stable/` 之下（`user-guide/*`、`operator-manual/*`），**已按 `09-参考索引/README.md` 的维护规则补录进索引（2026-10-24 逐条 curl 实测 200）**，来源表不再只写父页面。② **Flux 与 Jenkins X 的对比**为本资料库索引内无对应官方条目的通识性对比，已在正文标 `⚠️ 单源待补`。③ 本篇未实测：安装、Application 创建、同步输出片段均来自官方文档示意。

**待验证清单**（本篇明确不确定的点，均已标注在正文对应位置）：

| # | 待验证项 | 现状 | 怎么验 | 影响 |
|---|---|---|---|---|
| 1 | `Flux` / `Jenkins X` 的官方行为 | 标 `⚠️ 单源待补` —— 本资料库索引无这两家的官方条目，5.1 节只做通识性对比 | 补进索引后按各自官方文档核对 | 只影响 5.1 节的对比表，不影响 ArgoCD 本身的任何结论 |
| 2 | 本地 ArgoCD 实际版本 | **未实测**（本次写作未安装 ArgoCD），全文以官方文档站 `stable` 分支为准 | `argocd version` | 影响"某特性在哪个版本引入"这类判断（如 `argoproj.io/Application` 健康检查自 **1.8** 被移除、`PostDelete` hook **v2.10+**） |
| 3 | `syncPolicy.automated.enabled` 的默认值 | 官方 Application Specification 的注释写 "true by default"，但**这指的是 `automated` 块存在时的行为**；未写 `automated` 块时必然是手动同步 | 建一个不带 `automated` 块的 Application 观察 | 影响"新建 Application 是不是自动同步"这一认知 |
| 4 | `PrunePropagationPolicy` 未显式指定时的默认行为 | 本篇只写官方文档明确列出的 "By default, extraneous resources get pruned using the **foreground** deletion policy"，**不外推到其他版本** | 对照所用版本的 sync-options 页 | 影响 prune 时的删除时序 |
| 5 | `argocd app get` 的实际输出格式 | 正文引用的是**官方 Getting Started 文档中的示意输出**（`Sync Status: OutOfSync` / `Health Status: Missing`），不是本地实测 | 建一个 Application 后 `argocd app get` | 仅影响输出格式预期，不影响状态语义 |

---

## 8. 自测三问

1. **能说清输入输出吗？** —— ArgoCD 的输入是 **Git 仓库里的清单**（+ Application 里的 source/destination/syncPolicy 配置）；输出是**集群里的实际资源状态持续等于 Git 渲染出的期望状态**。中间经过：repo-server（clone → render → 缓存）→ controller（diff live vs desired → apply → 持续 reconcile）。
2. **能故意改坏再倒推吗？** —— 三个实验：① 删掉 `install.yaml` 里的 CRD 部分（只装本体）→ 建 Application 报 `no matches for kind "Application"`，**证明 CRD 必须先于 ArgoCD 本体**；② 把 `spec.destination.namespace` 写成一个不存在的 ns → 同步报 `namespace not found`，**证明 ArgoCD 默认不建命名空间**（要加 `CreateNamespace=true`）；③ 在集群里 `kubectl edit deployment` 改副本数 → 观察 selfHeal 开启时被改回、关闭时保留且应用变 `OutOfSync`，**证明 selfHeal 默认 false 且它是漂移纠正的核心开关**。
3. **能画上下游吗？** —— 画出 `Git → repo-server(clone/render) → desired manifest → controller(diff) → apply → 集群 → (回到 diff)` 这个闭环，并在每条边上标出"这一环断了会看到什么"：clone 断 → `Failed to load target state`；render 断 → `argocd app manifests` 也出不来；diff 断 → 一直 `OutOfSync`；apply 断 → `OperationFailed`；集群里手改 → 有 selfHeal 则被拉回、无 selfHeal 则漂移留存。

---

**下一篇**：[`L5-ArgoCD-策略与多集群`](L5-ArgoCD-策略与多集群.md)（漂移 / SelfHeal / Prune / ApplicationSet / 多集群） · **上一篇**：[`L3-Terraform-模块化与阿里云provider`](L3-Terraform-模块化与阿里云provider.md)
