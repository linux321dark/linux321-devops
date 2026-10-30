# L5 · ArgoCD 策略与多集群（从"能同步"到"敢放手"）

> **本篇定位**：L4 解决"ArgoCD 装起来、把应用同步出去"，本篇解决**"同步出去之后怎么办"**——Git 被改了怎么办、集群被人手工改了怎么办、Git 里删掉一个资源会不会真的删、3 套环境 × 5 个集群怎么管得过来。
> **前置**：L4 ArgoCD 安装与同步（Application / Project / 仓库接入）、L2 K8s 架构与对象模型（期望态/实际态/协调三态模型）。
> **学完能做什么**：① 能逐条讲清 automated / prune / selfHeal / allowEmpty 四个开关"开了会怎样、不开会怎样"；② 能说清漂移检测为什么是 GitOps 相对传统 CI/CD 的核心增量；③ 能用 ApplicationSet + AppProject + RBAC 设计一套多集群、多团队的边界；④ 能用 sync-wave 解决 CRD 必须先于 CR 的死锁；⑤ 能解释 prune 误删后的真实回滚路径。
> **在 21 天里**：D11（ArgoCD 策略日）。
> **环境/版本基准**：本文所有 ArgoCD 行为以 `argo-cd.readthedocs.io/en/stable/`（stable 分支，即 v2.x 稳定版）为准；本地 K8s 为 v1.37.x（kind 集群 `day2`）。**ArgoCD 的字段与默认值随版本演进，遇到不一致以官方 stable 页为准。**
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
ArgoCD 策略与多集群
│
├─ 1. 概念
│   ├─ 1.1 一句话：ArgoCD 是"以 Git 为唯一真相"的持续对账员
│   ├─ 1.2 没有 GitOps 时的问题（CI/CD 推模型 vs GitOps 拉模型）
│   └─ 1.3 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 核心组件与各自的活（server / repo-server / application-controller / redis）
│   ├─ 2.2 漂移检测（drift detection）：GitOps 的核心增量
│   ├─ 2.3 同步策略四维：automated / prune / selfHeal / allowEmpty
│   │   ├─ 2.3.1 automated —— 谁来点那个"同步"按钮
│   │   ├─ 2.3.2 prune —— 孤儿资源（orphan）怎么处理
│   │   ├─ 2.3.3 selfHeal —— 人的临时修改怎么办
│   │   └─ 2.3.4 allowEmpty —— 空目录应用能不能同步
│   ├─ 2.4 Sync Waves：同步顺序与 CRD→CR 的死锁
│   ├─ 2.5 Sync Options：逐个开关到底改了什么
│   ├─ 2.6 Hooks：PreSync / Sync / SyncFail / PostSync
│   ├─ 2.7 ApplicationSet：generator 类型与规模化
│   ├─ 2.8 多集群管理：cluster secret 与注册方式
│   ├─ 2.9 AppProject：源仓库/目标集群/命名空间/资源白名单
│   ├─ 2.10 RBAC 与多租户
│   ├─ 2.11 多集群同步的顺序与失败隔离
│   └─ 2.12 回滚机制：history 与 rollback 的真实行为
│
├─ 3. 动手：四个开关的最小可运行实验
├─ 4. 逐字段表
│   ├─ 4.1 同步策略与注解对照表
│   └─ 4.2 ApplicationSet generator 表
├─ 5. 横向对比
├─ 6. 踩坑与排障
├─ 7. 参考来源
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 一句话定义

> **ArgoCD 不负责"部署"，它负责"对账"——把 Git 里的期望态和集群里的实际态反复比对，然后决定要不要拉齐。**

这句话是全文的地基，它直接推出两个结论：

1. **ArgoCD 天然是持续运行的**，不是 CI 里跑完就结束的一次性任务。**不部署它，集群就会慢慢偏离 Git。**
2. **"拉齐"有两个方向**：把集群改成 Git 的样子（sync / selfHeal），以及把 Git 以外的东西删掉（prune）。**方向不同，风险完全不同**——前者会覆盖人的操作，后者会真的删数据。

把它和 L2 的三态模型对上号：

| L2 的三态 | ArgoCD 里的对应物 |
|---|---|
| **期望态** | Git 仓库里的 yaml（ArgoCD 渲染后的 manifest） |
| **实际态** | 集群里 live 的资源对象 |
| **协调** | `application-controller` 的 reconcile 循环 + repo-server 渲染 |
| **状态回写** | Application 的 `status`（`Synced` / `OutOfSync` / `Unknown`） |

> ArgoCD 不是"另一个 Deployment controller"，它**不创建 Pod**，它创建/修改的是 Deployment、Service、CR 这些**工作负载对象**。真正起 Pod 的还是 K8s 内置控制器。**这是 GitOps 最重要的一层间接性**（详见 L6：ArgoCD 管的可以是 Rollout 这个 CR，渐进逻辑由 rollouts-controller 执行）。

### 1.2 没有 GitOps 时的问题

传统 CI/CD 是**推模型（push）**：CI 跑完 → `kubectl apply` → 结束。它有四个洞：

| 问题 | 传统 CI/CD 的表现 | GitOps（拉模型）的解法 |
|---|---|---|
| **谁改了集群，谁说了算** | CI 推完就撤，集群里的状态从此失控 | Git 是唯一真相源，controller 持续对账 |
| **配置漂移无人知晓** | 有人 `kubectl edit` 改了副本数，CI 不知道、下次发布又被覆盖 | **漂移检测**发现 `OutOfSync` 并告警/自愈 |
| **"回滚"要靠 CI 的历史** | CI 记录的是流水线产物，产物过期就没了 | ArgoCD 每次同步都有 **history**，`rollback` 直接把某次快照重新 apply |
| **多集群靠脚本循环** | 一套 shell 循环 `for c in cluster1 cluster2` | ApplicationSet + cluster secret，声明式展开 |

**但要诚实**：GitOps 不是免费的。它把"部署"变成了"持续对账"，代价是：

- **你必须有仓库权限模型**（谁能改 Git ≈ 谁能改生产）
- **你必须接受"手工修改会被覆盖"**（这正是 selfHeal 的争议点，见 2.3.3）
- **你必须有 HA**（ArgoCD 挂了，谁来兜底？见第 6 节"ArgoCD 成为单点"）

> 面试能这么答会高一个档：**"GitOps 的核心不是自动化部署，是把'生产环境当前状态'这个问题变成可随时回答的问题——它靠的是持续对账，不是靠 CI 跑得更多。"**

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Application | 应用 | ArgoCD 的 CR，描述"哪个仓库的哪个路径 → 同步到哪个集群哪个命名空间" |
| AppProject | 项目 | 一组边界规则：允许用哪些源仓库、可以同步到哪些集群/命名空间、可以碰哪些资源类型 |
| **Desired State** | 期望态 | Git 仓库里渲染出来的 manifest |
| **Live State** | 实际态 | 集群里当前真实存在的资源 |
| **Drift / Drift Detection** | 漂移 / 漂移检测 | 期望态与实际态不一致；检测动作 |
| **Prune / Pruning** | 修剪 / 剪枝 | 删除"集群里有、Git 里没有"的资源（孤儿资源） |
| **Self Heal / SelfHeal** | 自愈 | 把被人手工改过的资源**改回** Git 的样子 |
| **Orphan Resource** | 孤儿资源 | 集群里有、但已不被任何 Application 纳管的资源 |
| **Sync Wave** | 同步波次 | 用注解定义的应用内同步顺序，数字小的先来 |
| **Sync Option** | 同步选项 | 应用级或资源级的同步行为开关 |
| **Hook** | 钩子 | 在同步流程的特定节点插入的资源（最典型是 Job） |
| **ApplicationSet** | 应用集 | 用 generator 批量生成 Application 的 CR |
| **Generator** | 生成器 | ApplicationSet 的数据来源（list / clusters / git / matrix） |
| **Multi-cluster** | 多集群 | 一个 ArgoCD 管理多个目标集群 |
| **Cluster Secret** | 集群密钥 | 存放目标集群地址与访问凭证的 Secret |
| **History / Rollback** | 历史 / 回滚 | 每次同步的快照与"把某个快照重新 apply" |
| **OutOfSync** | 不同步 | ArgoCD 判定期望态 ≠ 实际态 |
| **Repo Server** | 仓库服务器 | ArgoCD 组件，clone 仓库并渲染 manifest |
| **Application Controller** | 应用控制器 | ArgoCD 组件，负责对账与同步编排 |

---

## 2. 机制

### 2.1 核心组件与各自的活

ArgoCD 不是单体，它是几个职责分离的组件：

| 组件 | 干什么 | 你怎么配置它 | 挂了会怎样 |
|---|---|---|---|
| `argocd-server` | Web UI / API / CLI 后端；负责认证、RBAC 判定、把同步请求转成操作 | `argocd-cm`、ConfigMap | **你点不了 UI、CLI 调不动**（已排队的同步由 controller 执行，不受影响） |
| `argocd-repo-server` | clone Git 仓库、渲染 Helm/Kustomize/Jsonnet、算 diff | `argocd-repo-server` 的 Deployment 参数 | **所有同步都停摆**（渲染不出 manifest） |
| `argocd-application-controller` | **对账 + 编排同步**：周期性把 live state 与 desired state 比对，决定同步/自愈/剪枝 | `argocd-cm` 里的参数 | **不自动同步了**（手动点 Sync 也会失败，因为它要渲染） |
| `argocd-dex-server` | SSO（可选） | — | 登录不了（若用 Dex） |
| Redis | 缓存仓库内容、会话、集群状态 | 官方推荐**开启 HA 模式**（Sentinel） | 缓存失效、集群列表乱跳 |
| `argocd-notifications-controller` | 触发 Webhook/邮件通知 | `argocd-notifications-cm` | 不发告警，**不影响同步本身** |

**这条链路要背下来**：`argocd-server` 收到你的同步请求 → 让 `repo-server` 渲染出 desired state → `application-controller` 按 wave/hook 编排，把 live state 改成 desired state → 回写 Application 的 `status`。

### 2.2 漂移检测（drift detection）——GitOps 的核心增量

**机制**：`application-controller` 持续做两件事：

1. **拉取 desired state**：通过 `repo-server` 拿到 Git 里的 manifest（带缓存，缓存失效或仓库有变更时重新拉取）。
2. **读取 live state**：通过 K8s API 读集群里实际的资源对象。
3. **求差（diff）**：比较两者，得出 `Synced` 或 `OutOfSync`。

对账的触发有两条路径：

| 路径 | 触发条件 | 延迟量级 |
|---|---|---|
| **事件驱动** | watch 到资源变化（有人改了、控制器改了） | **秒级** |
| **定时兜底** | 周期性全量重算（`application-controller` 的 `--app-resync` 参数控制） | 官方标称默认 **180 秒**（3 分钟）⚠️ 具体默认值以你版本 `argocd-application-controller --help` 为准 |

**关键区分（很多人搞混）**：

| 概念 | 回答的问题 | 谁的活 |
|---|---|---|
| **漂移检测** | "集群现在的状态，和 Git 里的定义还一致吗？" | application-controller（**只报告，不动手**） |
| **同步 / 自愈** | "不一致，那要不要拉齐？拉齐的动作是什么？" | 需要 `automated` 打开（**动手**） |

> **一句话**：漂移检测是"发现"，selfHeal 是"动手改"。**只开漂移检测不开 selfHeal，ArgoCD 会在 UI 上把应用标成红色 `OutOfSync` 并可配告警，但集群里的偏差会一直留着——这在很多团队是刻意的选择**（第 5 节横向对比会展开）。

**为什么这是 GitOps 相对传统 CI/CD 的核心价值**：CI 只在"有人触发"时看一眼世界；GitOps 里的对账员**一直在看**。它把"生产环境到底现在是什么状态"从一个**需要人工排查的问题**变成了**一个可以在 UI 上直接读到的字段**。

### 2.3 同步策略四维

这是本篇的核心。Application 的 `spec.syncPolicy.automated` 下有**三个子开关**（加上 automated 本身共四维），官方 stable 文档（User Guide → Sync Options）给出的字段形态：

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: guestbook
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/argoproj/argocd-example-apps.git
    path: guestbook
    targetRevision: HEAD
  destination:
    server: https://kubernetes.default.svc
    namespace: guestbook
  syncPolicy:
    automated:          # ← 维度 1
      prune: true       # ← 维度 2
      selfHeal: true    # ← 维度 3
      allowEmpty: true  # ← 维度 4
    syncOptions:
      - CreateNamespace=true
      - PrunePropagationPolicy=foreground
```

#### 2.3.1 automated —— 谁来点那个"同步"按钮

| | 关 | 开 |
|---|---|---|
| **谁触发同步** | 人在 UI/CLI 点 `Sync`，或被 webhook 触发 | **application-controller 自动执行**（Git 一有变更就排队同步） |
| **Git 改了会怎样** | UI 上变成 `OutOfSync`，**集群不动**，等人点 | 自动同步，集群跟上 |
| **适合** | 生产环境需要人确认的变更 | 基础配置、非核心服务、明确的自动策略 |
| **风险** | 改了 Git 忘记点 → 集群和 Git 不一致，且**没人知道**（除非配了告警） | 自动同步 = **自动执行 Git 里的任何改动**，Git 权限就是生产权限 |

**关键细节**：`automated` 打开后，**任何**推到该应用源码分支的改动都会被同步，**没有"审核后再生效"这一步**。所以生产是否开 automated，取决于**你的 Git 保护分支 + CODEOWNERS + 审批流**靠不靠得住。**没有审批的仓库上开 automated，等于把生产写权限发给了所有能推分支的人。**

#### 2.3.2 prune —— 孤儿资源（orphan）怎么处理

**孤儿资源的定义**：集群里存在、且被这个 Application 追踪（带 tracking 注解/标签），但**在 Git 的期望态里已经不存在**的资源。典型场景：某次提交把某个 ConfigMap 删了。

| | 关 | 开 |
|---|---|---|
| **Git 里删了一个资源定义** | 集群里**那个资源还在**，永远不被清理（变成孤儿） | **同步时集群里那个资源被真的删掉** |
| **多花的钱** | 孤儿 Service/LoadBalancer 继续计费；孤儿 Pod 占资源 | 干净 |
| **风险** | 资源泄漏，可能被误当成"还在提供服务" | **误删生产资源**（详见 2.3.2 后半段与第 6 节） |

**prune 的四层安全网**（官方 stable 文档给出的组合）：

| 措施 | 作用 |
|---|---|
| `automated.prune: true` | 打开自动剪枝 |
| `PrunePropagationPolicy=foreground` | 删除时**等待子资源被彻底删除**再继续（避免删父后子还在） |
| `PruneLast=true` | **把剪枝放到最后一步**，在所有其他资源同步成功且健康之后才执行 |
| `DisableDeletion=true` | **彻底禁止删除**（用于"只允许创建/更新，不允许删"的场景） |

> **`PrunePropagationPolicy` 的三个取值**：`foreground`（级联删并等待完成）、`background`（发出删除请求后不等）、`orphan`（只删自己，把子资源 orphans 留下）。**未显式指定时的默认删除传播行为随 ArgoCD 版本演进，⚠️ 待验证（以你所用版本的 official sync-options 页为准）**——本篇不臆断默认值。

**prune 误删后的真实回滚路径**（很多人以为能一键恢复）：

1. `argocd app history <app>` 找到误删之前的 revision ID
2. `argocd app rollback <app> <revision>` → ArgoCD **重新 apply 那次同步的 manifest**
3. 资源**被重新创建**了

**但要认清三件事**：
- **数据没回来**。如果被删的资源背后有 PVC，且 PVC 也被一起删了（StatefulSet 的 volumeClaimTemplates 场景下 `foreground` 级联删会带走 PVC），**数据是真没了**。ArgoCD 回滚只恢复"资源定义"，不恢复"数据"。
- **Git 里仍然是删掉的状态**。你 rollback 之后，如果 automated 还开着、或者有人再点一次 Sync，**ArgoCD 会再次把它剪掉**。**正确的收尾是先在 Git 里 revert 掉那个删除提交，再 rollback**。
- **回滚是"再执行一次部署"，不是"时间机器"**。中间如果有人已经手工改了别的资源，rollback 会把它们一起打回那次的快照。

#### 2.3.3 selfHeal —— 人的临时修改怎么办

**selfHeal 做的事**：当 ArgoCD 发现 live state 与 desired state 不一致、且**这个不一致不是 ArgoCD 自己造成的**（典型：有人 `kubectl edit` 或 `kubectl scale`），它会把资源**改回 Git 的样子**。

| | 关 | 开 |
|---|---|---|
| **有人手工改了 Deployment 副本数** | 改动**保留**，但 Application 显示 `OutOfSync` | 改动**被自动撤销**，副本数回到 Git 的值 |
| **紧急排障时临时扩容救火** | ✅ 扩容生效，事后自己改回来 | ❌ **扩容被自动打回**，救火失败 |
| **临时改 ConfigMap 调参数** | ✅ 临时参数生效 | ❌ 参数被还原，**问题复现** |
| **ArgoCD 自身造成的差异** | — | 不受影响（ArgoCD 只对自己造成的差异做"认为已完成"处理，不触发自愈回滚） |

**取舍的本质**：selfHeal 是在赌 **"Git 里的东西永远比人手工改的更对"**。

- 这个赌在**声明式、配置驱动的资源**上（Deployment 副本数、镜像版本、ConfigMap 内容）基本成立——这些确实该由 Git 说了算。
- 这个赌在**临时运维动作**上会翻车。典型翻车场景：
  1. 线上 P0，故障定位到"连接池打满"，运维 `kubectl scale deploy xxx --replicas=10`
  2. ArgoCD 的对账周期到（**秒级事件驱动或最长 180 秒定时兜底**）
  3. selfHeal 把副本数改回 2 → **流量继续打过来，故障扩大**
  4. 有人紧急 `kubectl annotate` 或删掉 Application 的 tracking 标签来阻止自愈 → 又制造了新的漂移

**工程上的折中方案**（面试可讲）：

| 方案 | 做法 |
|---|---|
| **分级开** | 基础服务开 selfHeal；核心生产服务不开，只开"漂移告警" |
| **临时豁免** | 需要临时改的时候，先给资源加 `argocd.argoproj.io/compare-options: IgnoreExtraneous` 或直接**移除 tracking 标签**，改完再恢复 |
| **改 Git 不改集群** | 正确的临时变更路径是"改 Git → 提交 → 让 ArgoCD 同步"，而不是直接改集群 |

> **面试高分答法**："selfHeal 的取舍本质上是一个**权威归属**问题。我认为它应该按资源类型分级：配置类资源（副本数、镜像、ConfigMap）开 selfHeal，因为这些确实只有 Git 一个真相源；而涉及故障处置的临时变更，走'改 Git'或者'临时移除 tracking 标注'的流程，比赌自愈更稳。"

#### 2.3.4 allowEmpty —— 空目录应用能不能同步

**场景**：Git 仓库里某个目录被**删空了**（或者你把 path 指错了，指向一个空目录）。

| | 关 | 开 |
|---|---|---|
| **Git 里资源数为 0** | ArgoCD **拒绝同步**并在 UI 上报错 | **允许同步**，同时触发 prune，把上一版部署的资源全部剪掉 |
| **保护作用** | ✅ 防止"路径写错 / 分支切错"导致误删全站 | ❌ **一次误配就把整个环境清空** |
| **典型用法** | 默认开（安全） | 确实要"下线整个应用"的场景，或该应用本身就是"空壳" |

> **强烈建议**：`allowEmpty` 是四个开关里**唯一一个"关掉反而更安全"的**。绝大多数生产环境**不要开**。它的存在意义是给"故意清空"留个出口，而不是日常配置。

**四个开关的组合决策树**（把上面四节压成一张可执行的表）：

| 你的诉求 | automated | prune | selfHeal | allowEmpty |
|---|---|---|---|---|
| **生产核心服务**（要人确认） | ❌ 关 | ⚠️ 可开但配 `PruneLast` + `foreground` | ❌ 关（只告警） | ❌ 关 |
| **生产非核心服务**（全自动） | ✅ 开 | ✅ 开（配安全网） | ✅ 开 | ❌ 关 |
| **开发/测试环境** | ✅ 开 | ✅ 开 | ✅ 开 | ⚠️ 酌情 |
| **准备下线的应用** | ✅ 开 | ✅ 开 | ❌ 关 | ✅ 开（且**先确认 Git 是故意清空的**） |

### 2.4 Sync Waves：同步顺序与 CRD→CR 的死锁

**问题**：ArgoCD 同步一个目录里的资源时，默认**按字母序串行**创建。这意味着如果目录里有：

```
crds/xxx.yaml          ← CustomResourceDefinition
crs/xxx-instance.yaml  ← 基于该 CRD 的 CustomResource
```

**字母序 `crds/` 排在 `crs/` 前面，运气好；但只要目录名换成 `a-crds/` 和 `z-crs/` 之外的组合，或者你把文件平铺在一个目录里**，`crd-instance` 就可能先被创建 → 集群报 `no matches for kind`。

更本质的问题是：**CRD 的建立需要 apiserver 完成注册与 schema 生效，这是一个有延迟的过程**，不是"apply 返回 200 就立刻能用"。

**解法：sync wave**。给资源加注解：

```yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: examples.example.com
  annotations:
    argocd.argoproj.io/sync-wave: "-1"    # 先
---
apiVersion: example.com/v1
kind: Widget
metadata:
  name: my-widget
  annotations:
    argocd.argoproj.io/sync-wave: "0"      # 后
```

**机制**（官方 stable 文档 User Guide → Sync Phases / Sync Waves）：

| 规则 | 说明 |
|---|---|
| **默认波次 = 0** | 不写注解就是 0 |
| **数字小的先同步** | 支持负数（负数在 0 之前） |
| **同一波次内的资源并发应用** | 所以**同一个 wave 里不要放有依赖关系的两个资源** |
| **跨波次有等待** | 后一波次在前一波次的资源 **Healthy** 之后才开始 |
| **Hook 资源也参与波次排序** | Hook 与普通资源在同一 wave 内的先后另有规则，见 2.6 |

**"健康"是硬门槛**：这不是"apply 成功"就行。Deployment 要 `availableReplicas` 满足、Pod 要 Ready、Job 要 Succeeded。**这意味着一波里有一个资源不健康，后面所有波次全部卡住**——这是 2.4 配错时最典型的现象（详见第 6 节"sync-wave 配错导致应用卡住"）。

> **生产上的通用分波约定**（可直接抄）：
>
> | wave | 内容 |
> |---|---|
> | `-2` | Namespace（如果开了 `CreateNamespace` 通常不需要） |
> | `-1` | CRD |
> | `0` | 基础资源：ConfigMap / Secret / ServiceAccount / RBAC |
> | `1` | ConfigMap / Secret / Service / Ingress |
> | `2` | Deployment / StatefulSet |
> | `3` | PreSync / Sync Hook（Job） |
> | `4` | PostSync Hook（冒烟测试） |

### 2.5 Sync Options：逐个开关到底改了什么

Sync options 有**两个层级**：

| 层级 | 写法 | 作用范围 |
|---|---|---|
| **应用级** | `spec.syncPolicy.syncOptions` 列表 | 整个 Application |
| **资源级** | 资源注解 `argocd.argoproj.io/sync-options` | 单个资源（**优先级更高**） |

官方 stable 文档（User Guide → Sync Options）列出的常用项：

| 选项 | 作用 | 不开 / 开的差别 |
|---|---|---|
| `CreateNamespace=true` | **目标命名空间不存在时自动创建** | 关：同步直接失败（命名空间不存在）；开：先建 ns 再同步 |
| `PrunePropagationPolicy=foreground`（或 `background` / `orphan`） | 删除时的传播策略 | `foreground` 等子资源删干净；`background` 不等；`orphan` 留子资源 |
| `PruneLast=true` | 剪枝放到最后 | 关：剪枝与其他资源同步交织；开：其他资源都健康后才剪 |
| `RespectIgnoreDifferences=true` | 同步时**尊重 ignoreDifferences** | 关：被 ignore 的差异仍在某些操作里被覆盖；开：被忽略的字段保持现状 |
| `Replace=true` | **强制替换而非原地更新**（先删后建） | 关：走正常的 patch/update；开：删掉重建（用于不可原地改的字段） |
| `ServerSideApply=true` | 用 **SSA（服务端 apply）** 而不是客户端 apply | 关：客户端三方合并；开：由 apiserver 持有字段归属（冲突时以 SSA 语义裁决） |
| `ApplyOutOfSyncOnly=true` | **只 apply 处于 OutOfSync 的资源** | 关：每次同步都提交全部资源；开：跳过已同步的（大规模应用更快） |
| `Validate=true` | 同步前用 K8s OpenAPI **校验 manifest** | 关：不合规的 manifest 到 apiserver 才报错；开：提前失败，错误信息更清楚 |
| `FailOnSharedResourceWarning=true` | 资源被多个 Application 共享时**直接失败** | 关：只给 warning；开：同步中断 |
| `DisableDeletion=true` | **彻底禁止删除任何资源** | 关：允许 prune；开：任何删除都被拦下（"只增不减"应用） |

**`Replace=true` 的典型用途**：某些资源（如部分 StatefulSet、某些 CRD 的实例）的字段**不能原地修改**（immutable）。不写 `Replace=true` 时，同步会卡在 `field is immutable`；写了之后 ArgoCD 会删掉重建。

> ⚠️ **`Replace=true` 有数据代价**：它会真的删除并重建 Pod。**对有状态应用（StatefulSet + PVC）要格外小心**——Pod 重建不等于数据重建，但如果 PVC 策略是 `Delete`，**数据会没**。

**`RespectIgnoreDifferences=true` 的含义要精确**：`ignoreDifferences` 定义的是"哪些字段的差异**不算差异**"。比如某字段被 HPA 或云厂商控制器持续改写：

```yaml
spec:
  ignoreDifferences:
    - group: apps
      kind: Deployment
      name: my-app
      jqPathExpressions:
        - .spec.replicas
```

**为什么需要 `RespectIgnoreDifferences=true`**：默认情况下 ArgoCD 同步时用的是"渲染出来的完整对象"去 apply，被 ignore 的字段可能被**顺手覆盖回去**（比如把 HPA 刚调上去的 replicas 打回 Git 里的值）。打开它之后，被 ignore 的字段在同步时**保持现状**。

### 2.6 Hooks：在同步流程的四个节点插任务

**Hook 的核心特征**：**hook 资源不参与普通同步**。

| 特性 | 普通资源 | Hook 资源 |
|---|---|---|
| **平时会被 diff / 被剪枝吗** | 会 | **不会**（不作为期望态的一部分被对账） |
| **什么时候被创建** | 每次同步都 apply | **只在它所属的 hook 阶段被创建** |
| **创建完会被删吗** | 不会（是常驻对象） | 取决于 `argocd.argoproj.io/hook-delete-policy` |
| **典型 kind** | Deployment / Service / CR | **Job**（绝大多数 hook 是 Job） |

**四种 hook 与各自的用途**（官方 stable 文档 User Guide → Resource Hooks）：

| Hook | 触发时机 | 典型用途 | 失败后果 |
|---|---|---|---|
| **PreSync** | **任何资源同步之前** | 数据库迁移、备份、预热缓存、准备外部依赖 | **整个同步被阻断**（不会同步任何业务资源） |
| **Sync** | 同步过程中，**与普通资源一起按波次走** | 同步过程中的中间校验（注意它**不是**"所有资源同步完之后"） | 同步失败 |
| **SyncFail** | **同步失败时** | 回滚脚本、发告警、收集现场 | 不阻断（已经失败了） |
| **PostSync** | **所有资源同步成功且健康之后** | 冒烟测试、通知下游、触发缓存刷新 | **会把整个 Application 标成失败**（虽然资源都好了） |

**Hook 的两个注解**：

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrate
  annotations:
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded,BeforeHookCreation
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: my-registry/db-migrate:v1.8.2
```

| 注解 | 取值 | 含义 |
|---|---|---|
| `argocd.argoproj.io/hook` | `PreSync` / `Sync` / `SyncFail` / `PostSync` | 挂在哪个阶段 |
| `argocd.argoproj.io/hook-delete-policy` | `HookSucceeded` / `HookFailed` / `BeforeHookCreation` | **什么时候删掉这个 hook 资源**（Job 留着会占资源、也会在 `kubectl get job` 里堆垃圾） |

**必须理解的失败传播规则**（决定了第 6 节的排障）：

- **PreSync 失败 → 整个同步终止**，一个业务资源都不会被改。**这是最安全的失败模式**（宁可什么都不做，也别半套）。
- **PostSync 失败 → Application 被标为失败**（`Operation Failed`），但**资源其实已经全部更新成功**。这时候很多人会误以为"发布失败了要回滚"，其实**数据已经是新的**，回滚反而制造混乱。**PostSync 适合放"告警型"任务，不适合放"必须成功"的任务。**
- **同一个 hook 阶段的多个 hook**：它们**按波次顺序**执行，同波次并发。**不要把有先后依赖的两个 hook 放同一波次**。

### 2.7 ApplicationSet：把"复制粘贴 N 个 Application"变成声明

**问题**：3 个环境 × 5 个集群 = 15 个 Application。手工维护 = 噩梦（改一个配置要改 15 处，还容易漏）。

**解法**：ApplicationSet（`argoproj.io/v1alpha1`）用一个 **generator（生成器）** 产生参数矩阵，用 **template** 把参数填进 Application 模板。

```yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: guestbook-appset
  namespace: argocd
spec:
  generators:
    - list:
        elements:
          - cluster: prod-hz
            env: prod
          - cluster: staging-hz
            env: staging
  template:
    metadata:
      name: 'guestbook-{{cluster}}'
      labels:
        app.kubernetes.io/part-of: guestbook
    spec:
      project: default
      source:
        repoURL: https://github.com/argoproj/argocd-example-apps.git
        path: 'guestbook-{{env}}'
        targetRevision: HEAD
      destination:
        server: 'https://{{cluster}}.example.internal'
        namespace: 'guestbook-{{env}}'
```

**它的工作方式**：

```
ApplicationSet
   │  generator 产出参数列表：[{cluster: prod-hz, env: prod}, {cluster: staging-hz, env: staging}, ...]
   ▼
template + 参数渲染 → 一堆 Application 对象
   │
   ▼
ArgoCD 逐个对账、同步（每个 Application 相互独立）
```

**generator 的四种主要类型**（详见 4.2 的完整表）：

| 类型 | 数据从哪来 | 典型用途 |
|---|---|---|
| `list` | **写死在 YAML 里**的元素列表 | 环境数量固定、最直白 |
| `clusters` | **ArgoCD 已注册的集群**（cluster secret） | 多集群：一次铺开到所有集群 |
| `git` | **Git 仓库里的文件/目录** | 每个子目录一个应用（monorepo） |
| `matrix` | **两个 generator 的笛卡尔积** | 环境 × 集群 的组合爆炸 |

**ApplicationSet 与手写多个 Application 的对比（这是面试常问）**：

| 维度 | 手写多个 Application | ApplicationSet |
|---|---|---|
| **新增一个环境** | 复制粘贴一个 YAML | **改 generator 的 list，加一行** |
| **配置一致性** | 靠人肉复制，**极易漂移** | 模板唯一，**结构上不可能漂移** |
| **参数化** | 只能整份复制 | `{{key}}` 模板插值 |
| **删除一个应用** | 手动删 Application（**资源变成孤儿**） | 改 generator，**ApplicationSet 会自动 prune 它** |
| **复杂度** | 简单直接 | 需要学 generator 语义与模板插值 |
| **调试** | 一眼看得懂 | 出了问题要看 "生成的 Application" 而不是 ApplicationSet 本身 |

> **ApplicationSet 有一个很多人不知道的自动清理行为**：当 generator 不再产出某个 Application 时，ApplicationSet 会**删除**那个 Application（并且默认会 prune 它管理的资源）。**这意味着"手滑删了一行 list"就可能把一整套环境删掉**。官方 stable 文档（User Guide → ApplicationSet）明确说明这一行为，用之前必须确认 `spec.preserveResourcesOnDeletion` 之类的保护设置或严格的仓库评审。

### 2.8 多集群管理

**ArgoCD 管理多集群有三种拓扑**，选择标准是**信任边界**：

| 拓扑 | 描述 | 适合 |
|---|---|---|
| **单 ArgoCD 管多集群** | 一个 ArgoCD 实例 + N 个 cluster secret | 一个团队管多个集群，**信任域一致** |
| **每集群一个 ArgoCD** | 每个目标集群一套 ArgoCD（cluster-in-cluster） | **信任域不同**（不同业务线、不同安全等级） |
| **集中管控（hub-spoke）** | 中心 ArgoCD 管多个集群的**元数据**，各集群跑 agent | 强合规、边缘集群 |

**集群注册（cluster secret）**：一个集群就是一个带特定标签的 Secret：

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: prod-hz-cluster
  labels:
    argocd.argoproj.io/secret-type: cluster     # ← 这个标签是识别标志
stringData:
  name: prod-hz
  server: https://<apiserver-endpoint>
  config: |
    {
      "bearerToken": "<token>",
      "tlsClientConfig": { "insecure": false, "caData": "<base64-ca>" }
    }
```

| 字段 | 含义 | 写错的后果 |
|---|---|---|
| `metadata.labels["argocd.argoproj.io/secret-type"]` | **= `cluster`** | 标签写错 → ArgoCD **认不出这是个集群**，UI 里看不到，clusters generator 也不产出 |
| `stringData.name` | 集群的**显示名** | 与 Application 的 `destination.name` 不匹配 → 同步报"集群未找到" |
| `stringData.server` | **apiserver 地址** | 写错 → 连不上，所有该集群的应用卡住 |
| `stringData.config` | JSON 字符串：bearerToken / tlsClientConfig | JSON 格式错 → 集群显示为不可用 |

**`destination` 的两种写法**（容易搞混）：

```yaml
destination:
  server: https://<apiserver-endpoint>   # 用地址
  # name: prod-hz                        # 或用注册时的名字（二选一）
  namespace: guestbook
```

> **注意**：一个 Application 的 `destination` **只能指向一个集群**。要"同一份配置同步到多个集群"，用 ApplicationSet 的 **clusters generator**，不要写一个 Application 指向 N 个集群。

**安全边界（面试重点）**：

一个 ArgoCD 实例拿到某个集群的凭证后，**它的权限就是那个凭证的权限**。在多集群场景下：

| 风险 | 说明 | 对策 |
|---|---|---|
| **凭证泄露的爆炸半径** | 一个 cluster secret 泄露 = 一个集群沦陷 | secret 存 K8s Secret 并开启 etcd 加密；token 用最小权限的 ServiceAccount |
| **一个 ArgoCD 管所有集群 = 单点** | ArgoCD 整体故障 = 所有集群都失去自动同步 | 见第 6 节"ArgoCD 自身成为单点" |
| **多团队共用一个 ArgoCD** | 团队 A 的 Application 可能碰到团队 B 的资源 | **AppProject 划边界 + ArgoCD RBAC 划权限**（2.9 / 2.10） |
| **仓库权限 = 生产权限** | 能改 Git 就能改生产（开了 automated 时） | 保护分支 + CODEOWNERS + 审批 |

### 2.9 AppProject：边界规则

`AppProject`（`argoproj.io/v1alpha1`）是 ArgoCD 的**准入控制**：它限定了"这个项目里的 Application 可以从哪些源仓库来、可以同步到哪些集群/命名空间、可以创建/管理哪些资源类型"。

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AppProject
metadata:
  name: team-payments
  namespace: argocd
spec:
  description: '支付团队：只允许自己的仓库与命名空间'
  sourceRepos:
    - 'https://github.com/mycorp/payments.git'
  destinations:
    - server: 'https://prod-hz.example.internal'
      namespace: 'payments-*'
    - server: 'https://staging-hz.example.internal'
      namespace: 'payments-*'
  clusterResourceWhitelist:
    - group: '*'
      kind: '*'
  namespaceResourceWhitelist:
    - group: '*'
      kind: '*'
  roles:
    - name: payments-readonly
      policies:
        - p, proj:team-payments:team-payments-readonly, get, guestbook, apps/*/guestbook-prod, allow
      jwtTokens:
        - token: <jwt>
```

**四个维度（官方 stable 文档 User Guide → Projects）**：

| 维度 | 字段 | 语义 | 越界的表现 |
|---|---|---|---|
| **源仓库** | `spec.sourceRepos` | 允许从哪些 Git 仓库取 manifest | 仓库不在列表 → 同步失败（仓库被判定为不允许） |
| **目标** | `spec.destinations` | 允许同步到哪些 **集群 + 命名空间**（namespace 支持通配） | 目标不在列表 → 同步失败 |
| **集群级资源白/黑名单** | `spec.clusterResourceWhitelist` / `clusterResourceBlacklist` | 允许/禁止管理 **集群级** 资源（Namespace / CRD / PV / ClusterRole 等） | 黑名单里的资源被 prune 或被拒绝同步 |
| **命名空间级资源白/黑名单** | `spec.namespaceResourceWhitelist` / `namespaceResourceBlacklist` | 允许/禁止管理**命名空间内**的资源（Deployment / Service 等） | 同上 |

**白名单与黑名单的优先关系**（官方文档明确说明）：**黑名单优先于白名单**。即一个资源既在白名单又在黑名单 → **被拒**。这是设计上的"默认拒绝"倾向。

**`spec.roles` 与 JWT**：给外部系统（CI 流水线、另一个平台的发布机器人）发一个**受限的 API 令牌**，让它只能对指定项目里的指定 Application 做指定操作（get / sync / create 等）。这是 ArgoCD 内部的多租户凭证机制。

> **实战提醒**：官方安装默认会带一个 `default` 项目，它的 `sourceRepos` 和 `destinations` 常常是 `*`（全放行）。**这等于没有边界**。上生产前第一件事就是**收紧 `default` 项目，或者给每个团队建独立 Project 并禁止用 `default`**。

### 2.10 RBAC 与多租户

ArgoCD 里有**两层 RBAC**，别搞混：

| 层 | 作用在 | 配置位置 | 管什么 |
|---|---|---|---|
| **ArgoCD 自身的 RBAC** | **人/令牌对 ArgoCD API 的操作** | `argocd-rbac-cm` ConfigMap（policy.csv）、内置角色（admin / readonly / edit）、Project 的 `roles` + JWT | "这个人能不能看到/同步这个 Application" |
| **K8s 的 RBAC** | **ArgoCD 的 ServiceAccount 对集群的权限** | Kubernetes `Role` / `ClusterRole` / `RoleBinding` | "ArgoCD 能不能 create/delete Deployment、能不能读 Secret" |

**第二层是很多事故的根源**：ArgoCD 的 ServiceAccount 权限不够 → 同步到一半失败；权限太大 → 一个 ArgoCD 就能删整个集群的资源。**官方部署清单默认给的是较宽松的 ClusterRole**，多集群场景下应当**按目标集群收敛成 namespace 级 Role**。

**多租户的三种隔离方案**：

| 方案 | 做法 | 隔离强度 | 成本 | 适合 |
|---|---|---|---|---|
| **一个 ArgoCD + Project 隔离** | 每团队一个 AppProject + ArgoCD RBAC 角色；K8s 侧收紧到 namespace 级 Role | **弱**（同一个 ArgoCD 进程、同一个 Git 仓库信任域） | 低 | 同一团队内部多小组 |
| **一个 ArgoCD + 每团队独立仓库 + 独立 cluster secret 权限** | 同上，但仓库与集群凭证都按团队分开 | 中 | 低 | 团队规模不大、信任度较高 |
| **每团队一套 ArgoCD** | 各自部署、各自的仓库、各自的集群凭证 | **强**（进程级隔离） | **高**（每套都要 HA） | 不同业务线 / 不同安全等级 |

> **给本地民企的建议（面试可讲）**：**10 几人团队通常不值得为每个团队部署一套 ArgoCD**——运维成本（HA、升级、监控）会超过收益。更现实的是：**一套 ArgoCD + 按团队划 AppProject + 仓库保护分支 + ArgoCD RBAC 角色**。只有当**合规要求物理隔离**时才上多实例。

### 2.11 多集群同步的顺序与失败隔离

这是最容易想当然的地方。**答案是：Application 之间完全独立。**

| 问题 | 答案 |
|---|---|
| **一个集群同步失败，会影响其他集群吗？** | **不会。**每个 Application 是独立的同步单元，application-controller 逐个处理，某个失败只影响它自己 |
| **多个集群之间有先后顺序吗？** | **默认没有。**ApplicationSet 生成的应用会**并发**被同步，谁先谁完不保证 |
| **sync-wave 能跨 Application 排序吗？** | **不能。**sync-wave 是**单个 Application 内部**的排序机制 |
| **那怎么保证"先灰度集群 A，全绿了再上集群 B"？** | **用 Git 的推进顺序**：先只把 A 的配置合进主干并等它全绿，再合 B。**顺序控制应该放在 Git/CI 层，不是 ArgoCD 层** |
| **ApplicationSet 的生成顺序有保证吗？** | generator 产出后 Application 一次性被创建，**后续的同步由 controller 并发处理** |

**失败隔离的边界（重要）**：

- ✅ **同步失败是隔离的**：A 集群的 Deployment 起不来，不会阻止 B 集群同步。
- ❌ **但 ArgoCD 组件故障不是隔离的**：repo-server 挂了 → **所有集群**都同步不了。controller 挂了 → **所有集群**都不自动同步。
- ❌ **ArgoCD 实例本身是所有集群的共同依赖**：这就是第 6 节"ArgoCD 成为单点"。

> **面试答法**："多集群的**同步失败是隔离的**，但**控制面故障不是隔离的**——repo-server 或 application-controller 挂掉会影响所有集群。所以多集群场景必须做 HA，而且不能把 ArgoCD 的可用性寄托在"它是运维平台的一部分"上。"

### 2.12 回滚机制：history 与 rollback 的真实行为

**ArgoCD 每次同步都会在 Application 的 history 里留一条记录**（revision ID + 时间 + 同步结果 + 涉及的 resources）。

```
$ argocd app history guestbook
ID                  DATE               REVISION  STATUS      MESSAGE
6f7d1c8b4b7d        2026-10-24 10:12   abc1234    Succeeded  successfully synced
a3e9f2c1d0b5        2026-10-24 09:40   def5678    Succeeded  successfully synced
b1c2d3e4f5a6        2026-10-23 18:05   0123456    Failed     failed to create deployment
```

**rollback 的真实语义**：

| 事实 | 说明 |
|---|---|
| **rollback = 重新 apply 那次 revision 的 manifest** | 不是"把集群恢复到那一刻"，而是"再执行一次那次的部署动作" |
| **数据不回来** | PVC 里的数据不会被恢复 |
| **Git 不变** | **Git 仍然是删除后的状态**。开了 automated 的情况下，下一次对账会**再次 prune 掉** |
| **正确的收尾顺序** | ① 先在 Git 里 revert 掉那个问题提交 → ② 等它同步完、全绿 → ③ 此时问题已解决，不需要 rollback |
| **什么时候才真的需要 rollback** | **Git 里的代码本身有问题**（不是配置误删），你需要先在 Git revert，同时把集群**立即**拉回上一个可用版本 |

**"立即"这一点是 rollback 的真正价值**：Git 的 revert → push → ArgoCD 同步这条链路要走完对账周期（秒级到 180 秒兜底）。**rollback 是"我不想等这个周期"**的应急通道。**但它和 automated/selfHeal 会在后续对账时打架**——所以要么在 rollback 期间暂停 automated，要么在 Git 里先 revert。

---

## 3. 动手

> **本节的操作都在你自己的集群上做，且必须用测试 Application，不要用生产。** 本篇写作过程中未在你的环境实测，所有"看到什么算对"是判定标准，不是粘贴的终端输出。

### 3.1 准备：一个可反复实验的最小应用

用官方 stable 文档里的 guestbook 示例（`argoproj/argocd-example-apps` 仓库，path 为 `guestbook`）作为实验对象。它的好处是**只有 Deployment + Service**，出问题时变量少。

```yaml
# guestbook-app.yaml（Application 最小形态）
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: guestbook
  namespace: argocd
  finalizers:
    - resources-finalizer.argocd.argoproj.io    # ← 见 3.4 的说明
spec:
  project: default
  source:
    repoURL: https://github.com/argoproj/argocd-example-apps.git
    path: guestbook
    targetRevision: HEAD
  destination:
    server: https://kubernetes.default.svc
    namespace: guestbook
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

**看到什么算对**：
- UI 的 Applications 列表里出现 `guestbook`，状态列最终是 **Synced**
- `kubectl get ns guestbook` 存在（因为 `CreateNamespace=true`）
- `kubectl -n guestbook get deploy,svc` 能看到 Deployment 和 Service

### 3.2 实验一：漂移检测 + selfHeal（对应 2.2 / 2.3.3）

```bash
# ① 记录当前副本数
kubectl -n guestbook get deploy guestbook -o jsonpath='{.spec.replicas}'

# ② 手工改集群（模拟有人手工操作）
kubectl -n guestbook scale deploy guestbook --replicas=5

# ③ 看 ArgoCD 的判定
#    UI: guestbook 变成 OutOfSync，diff 面板显示 replicas: 1 → 5
```

**看到什么算对**：
- **selfHeal 开**：几秒到几分钟内副本数**自动回到 1**，UI 回到 Synced。事件面板能看到 ArgoCD 的自愈动作。
- **selfHeal 关**：副本数**停在 5**，UI 一直显示 `OutOfSync`。**这正是"开了漂移检测没开自愈"的样子。**

### 3.3 实验二：prune 与孤儿资源（对应 2.3.2）

```bash
# ① 先在 Git 上删掉 guestbook 里的 Service 定义（提交到目标分支）
# ② 观察两种行为
```

**看到什么算对**：
- **prune 开**：同步后 `kubectl -n guestbook get svc` 里那个 Service **消失**（真的被删了）。UI 事件里能看到 prune 的记录。
- **prune 关**：Service **还在**，UI 提示该资源"orphaned"。

> **做完这个实验一定要恢复**：要么 `git revert` 那个删除提交，要么把 Application 的 `finalizers` 里的 `resources-finalizer.argocd.argoproj.io` 去掉再删 Application（否则 ArgoCD 会连同资源一起删）。**这个 finalizer 的存在就是为了"删 Application 时同步删资源"——它本身也是一个需要被团队明确规定的行为**（生产上是否加这个 finalizer，是一次明确的取舍，不该默认继承）。

### 3.4 实验三：sync-wave 解决 CRD → CR 顺序（对应 2.4）

```yaml
# 故意把 CR 的 wave 设成比 CRD 小，模拟配错
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: widgets.example.com
  annotations:
    argocd.argoproj.io/sync-wave: "0"     # ❌ 配错：CRD 应该在 CR 之前
---
apiVersion: example.com/v1
kind: Widget
metadata:
  name: my-widget
  annotations:
    argocd.argoproj.io/sync-wave: "-1"    # ❌ 配错：CR 反而更早
spec:
  size: 3
```

**看到什么算对**：
- 同步**失败**，Application 的 Events 里能看到创建 CR 时的资源类型不存在的错误（CRD 尚未注册）。
- 把 CRD 改成 `-1`、CR 改成 `0` 后再同步 → 成功。**顺序由 wave 决定，不是由文件名决定。**

### 3.5 验证清单

| 检查项 | 怎么看 | 期望 |
|---|---|---|
| Application 状态 | UI / `argocd app get guestbook` | `Sync Status: Synced` |
| 命名空间 | `kubectl get ns guestbook` | 存在（`CreateNamespace=true`） |
| 漂移能被检出 | `kubectl -n guestbook scale deploy guestbook --replicas=5` | UI 变 `OutOfSync` |
| 自愈生效 | 等几秒再看副本数 | 回到 1（selfHeal 开） |
| prune 生效 | Git 里删掉一个资源再同步 | 该资源从集群消失（prune 开） |
| wave 顺序 | 把 CR 的 wave 改小 | 同步失败，报错与资源类型相关 |
| history 存在 | `argocd app history guestbook` | 每次同步一条记录 |

---

### 3.6 常用运维命令（Argo CD 多集群 / 策略 / 排障，按场景）

> **本节能做什么**：3.1–3.5 是"漂移检测、prune、sync-wave、多集群策略"，本节是**带策略与多集群场景下每天敲的 Argo CD 运维命令**——从 Project 权限边界、多集群分发、回滚到典型排障。这是配置管理岗在"管一堆 app、跨好几个集群"时的实战面板。
> **环境基准**：本机实测 `argocd v2.11.x`，已 `argocd login` 到控制面。

#### ① 多集群与 context（cluster / context / app 分发）

```bash
argocd cluster list                              # 看所有已注册集群及当前选中
argocd cluster add <kubecontext> --name prod     # 注册生产集群并起别名
argocd context                                   # 看 CLI 当前指向哪个 Argo CD 服务/账号
argocd app create web --repo ... --path prod --dest-server https://k8s.prod --dest-namespace web
argocd app list -o wide                          # 宽表，含目标集群列（一眼看 app 落在哪）
argocd app get web --show-params                 # 看 app 实际生效的参数（排查"配了没生效"）
```

> **实战价值**：多集群下 `app list -o wide` 能避免"以为改了 A 集群其实动的是 B 集群"。`--dest-server` 写错集群是生产事故高发区——CI 里用集群别名而非裸 URL，可读性更高也更不易错。

#### ② Application 运维与回滚（get / sync / diff / rollback / history）

```bash
argocd app get web                               # 看树状资源 + 同步/健康状态
argocd app sync web --strategy apply            # 指定同步策略（apply 或 hooks）
argocd app sync web --prune --force             # 清理孤儿资源并强制纠 drift
argocd app diff web                             # 预览 Git 期望 vs 集群实际（手动同步前必看）
argocd app history web                          # 看每次同步的 revision 记录
argocd app rollback web <REVISION>              # 回滚到某次 Git 提交（救火第一招）
```

> **实战价值**：带策略的 app 回滚要确认 `prune`/`selfHeal` 行为——回滚到旧 Git revision 时，若新版本才有的资源默认会被 prune 掉，这是"回滚后为什么少资源"的根因，面试常考。

#### ③ 权限与策略（proj / rbac / account）

```bash
argocd proj list                                 # 看所有 AppProject 及其源/集群/命名空间白名单
argocd proj get default                         # 看某个 Project 的权限边界
argocd proj role list default                   # 看 Project 下的 RBAC 角色
argocd account list                             # 看本地账号
argocd rbac can <user> get apps '*' --project default   # 校验某用户在某 Project 有没有某权限
argocd proj allow-cluster-resource default '*' '*'      # 放宽 Project 可管的资源范围（谨慎）
```

> **实战价值**：`rbac can` 是排"为什么这个用户看不到那个 app"的利器，把"没权限"和"资源不存在"一眼分开（类比 kubectl 的 `auth can-i`）。Project 是 Argo CD 多租户的隔离边界，面试常问"怎么让 A 团队只能动 A 的 namespace"。

#### ④ 排障：OutOfSync / orphan / 同步卡死

```bash
argocd app get web -o yaml | grep -A5 status.sync   # 看同步状态细节与报错信息
argocd app logs web --since 30m                    # 最近 30 分钟同步日志
argocd app terminate-op web                        # 卡死的同步操作强制终止
argocd app sync web --resource hook                # 只重跑 hook（如 PreSync Job 失败）
kubectl -n web scale deploy web --replicas=9       # 制造 drift，验证 selfHeal / 检出
argocd app wait web --operation                    # 等当前操作完成，流水线里卡同步
```

> **实战价值**：多集群下"同步卡死"常因目标集群网络不通或凭证失效——先看 `cluster list` 里该集群 `Status` 是否 `Successful`。孤儿资源（Git 删了但集群还在）靠 `--prune` 清；drift 靠 `selfHeal` 或 `sync --force` 纠。排障心法：**先看状态 → 再看 operation message / logs → 最后才是修**。

---

## 4. 逐字段表

### 4.1 同步策略与注解对照表

**Application 侧（`spec.syncPolicy`）**

| 字段 / 注解 | 类型 | 必填 | 作用 | 开启后的行为 | 风险 / 写错的后果 |
|---|---|---|---|---|---|
| `spec.syncPolicy.automated` | object | | **自动同步总开关** | controller 主动把 Git 的变更同步到集群 | **Git 权限 = 生产写权限**；没有审批流时慎开 |
| `spec.syncPolicy.automated.prune` | bool | | **自动剪枝** | 同步时删除"Git 里已无"的追踪中资源 | **误删生产资源**；被删对象的数据不可恢复 |
| `spec.syncPolicy.automated.selfHeal` | bool | | **自动自愈** | 把被手工改动的资源改回 Git 的定义 | **覆盖紧急手工变更**（临时扩容/改参数被抹掉） |
| `spec.syncPolicy.automated.allowEmpty` | bool | | **允许同步空期望态** | 允许 desired state 为空，并触发 prune 清场 | **一次路径写错就清空整个环境**；建议保持关闭 |
| `spec.syncPolicy.syncOptions[]` | list | | 应用级同步选项 | 见 4.1 的 Sync Options 子表 | 选项组合不当会静默改变行为 |
| `spec.syncPolicy.retry.limit` | int | | 失败重试次数 | 同步失败后自动重试 | 重试掩盖真实错误，排障时看日志更久 |
| `spec.syncPolicy.retry.backoff` | duration | | 重试退避间隔 | 重试间隔递增 | — |
| `spec.syncPolicy.managedNamespaceMetadata` | object | | 自动创建 ns 时打的标签/注解 | — | 标签错误 → 资源策略（如 NetworkPolicy）不生效 |
| `spec.ignoreDifferences[]` | list | | 定义"不算差异"的字段 | 匹配到的差异不触发同步/自愈 | 写太宽 → **真实配置变更被忽略，永远同步不下去** |
| `spec.project` | string | | 归属的 AppProject | 受该 Project 的源仓库/目标/资源白名单约束 | 目标不在 destinations → 同步直接失败 |
| `spec.destination.server` | string | | 目标集群 apiserver 地址 | — | 与 cluster secret 的 `server` 不匹配 → 连不上 |
| `spec.destination.namespace` | string | | 目标命名空间 | — | 命名空间不存在且未开 `CreateNamespace` → 同步失败 |
| `spec.source.repoURL` | string | ✅ | manifest 源仓库 | — | 不在 Project 的 `sourceRepos` 里 → 同步失败 |
| `spec.source.path` | string | | 源仓库内路径 | — | 路径写错 → 可能触发空目录（配了 allowEmpty 就危险） |
| `spec.source.targetRevision` | string | | 分支 / tag / commit | — | 指向已删分支 → 渲染失败 |

**资源侧注解**

| 注解 | 作用 | 开启后的行为 | 风险 / 写错的后果 |
|---|---|---|---|
| `argocd.argoproj.io/sync-wave` | 定义同步波次 | 数字小的先同步；跨波次需等前一波**健康** | **配错导致应用卡住**；同波次内有依赖 → 竞态 |
| `argocd.argoproj.io/sync-options` | 资源级同步选项 | 覆盖应用级同名选项 | 覆盖掉应用级的安全网（如在单个资源上关掉 `PruneLast`） |
| `argocd.argoproj.io/hook` | 挂到同步流程的某个阶段 | 该资源只在该阶段被创建 | **hook 失败会阻断同步**；阶段选错 → 执行时机不对 |
| `argocd.argoproj.io/hook-delete-policy` | 决定何时删 hook 资源 | 按策略删掉 Job 等 | 不设 → Job 堆积；`BeforeHookCreation` 缺失 → 重复任务名冲突 |
| `argocd.argoproj.io/compare-options` | 比较行为选项（如 `IgnoreExtraneous`） | 忽略额外字段 | 忽略太多 → 真实漂移检测不到 |
| `argocd.argoproj.io/managed-by` | 标识由哪个 ArgoCD 实例管理 | 多实例并存时按实例过滤 | 写错 → **该实例管不到这个 Application**，同步不发生 |
| `argocd.argoproj.io/tracking-id` | 自定义资源追踪标识 | 用于跨 Application 识别同一资源 | 冲突 → 两个应用互相抢资源，报 shared resource 警告 |
| `argocd.argoproj.io/instance` | **旧版**追踪标签（新版本用 `app.kubernetes.io/instance`） | 资源归属标记 | 用错 → **prune 把别人的资源删掉** |

**Sync Options 逐项**

| 选项 | 作用 | 开启后的行为 | 风险 / 写错的后果 |
|---|---|---|---|
| `CreateNamespace=true` | 自动创建目标命名空间 | ns 不存在时先建再同步 | 会创建**同名 ns**，与已有 ns 语义冲突 |
| `PrunePropagationPolicy=foreground` | 级联删并等待 | 删除父资源时等子资源删完 | **删除慢**，可能触发同步超时 |
| `PrunePropagationPolicy=background` | 发出删除即返回 | 不等待子资源 | 子资源可能短暂残留 |
| `PrunePropagationPolicy=orphan` | 只删自己 | 子资源被 orphans 留下 | **遗留孤儿资源**，可能继续占资源/计费 |
| `PruneLast=true` | 剪枝放到最后 | 其他资源健康后才剪 | 其他资源不健康 → **剪枝也永远不会执行**（垃圾留着） |
| `RespectIgnoreDifferences=true` | 同步时尊重 ignoreDifferences | 被忽略字段保持现状 | 忽略过宽 → 配置永远漂移 |
| `Replace=true` | 删了重建而非原地改 | 强制替换资源 | **Pod 重启、数据风险**（StatefulSet + Delete 策略 PVC 慎用） |
| `ServerSideApply=true` | 用 SSA | 由 apiserver 维护字段归属 | 冲突语义变化，老版本集群不支持 |
| `ApplyOutOfSyncOnly=true` | 只提交不同步的资源 | 同步更快 | 首次同步/资源被删时的行为需验证 |
| `Validate=true` | 同步前校验 manifest | 提前失败 | 校验器过严会挡住合法资源 |
| `FailOnSharedResourceWarning=true` | 资源被共享时失败 | 同步中断 | 会把"警告"变成"故障"，需要提前规划资源归属 |
| `DisableDeletion=true` | 禁止任何删除 | prune 与 Replace 都被拦下 | 资源无法下线，成为遗留 |

### 4.2 ApplicationSet generator 表

| generator | 数据来源 | 参数（template 里可用） | 典型用法 | 写错的后果 |
|---|---|---|---|---|
| `list` | YAML 里写死的元素列表 | 元素里的每个 key | 环境数量固定、配置各不相同 | 列表写错 → 生成的 Application 参数全错 |
| `clusters` | **ArgoCD 已注册的集群**（cluster secret） | `name`、`server`、集群上的自定义键 | **一次铺开到所有集群** | cluster secret 标签写错 → **生成 0 个应用**（静默失败） |
| `git` | Git 仓库里的**目录**（每个目录一个元素）或**文件**（JSON/YAML 数组） | 目录名 / 文件内容里的字段 | monorepo：每个子目录一个应用 | 路径/格式不对 → 生成失败或生成空应用 |
| `matrix` | **两个 generator 的笛卡尔积** | 两个 generator 参数的合并 | **环境 × 集群**的组合爆炸 | 参数名冲突 → 渲染出错误的值 |
| `merge` | 多个 generator 的参数合并（非笛卡尔积） | 合并后的键 | 固定项 + 变量项 | 合并策略配错 → 参数被意外覆盖 |
| `pullrequest` | SCM provider 的 PR | PR 相关字段 | PR 预览环境 | 需要配置 SCM provider，否则不可用 |
| `plugin` | 外部插件进程 | 插件返回的键 | 自定义数据源 | 插件不可用 → 生成器失败，**所有应用停止更新** |

**ApplicationSet 的其他关键字段**

| 字段 | 作用 | 写错的后果 |
|---|---|---|
| `spec.template` | Application 模板（支持 `{{key}}` 插值） | 插值键名不存在 → 渲染出空值，**不报错** |
| `spec.goTemplate` | 启用 Go template（`{{ .key }}`） | 与 `{{key}}` 混用 → 渲染结果为空 |
| `spec.strategy.type` | `Apply` / `CreateOnly` / `CreateThenDelete` / `Replace` | `Replace` 会先删后建，**资源被重建** |
| `spec.preserveResourcesOnDeletion` | 删 ApplicationSet 时是否保留已生成应用的资源 | 关闭 → **删 ApplicationSet 会连带删生产资源** |
| `spec.syncPolicy` | 生成的 Application 的同步策略 | 继承不当 → 全量自动同步 |

---

## 5. 横向对比

### 5.1 auto-prune 开 vs 关

| 维度 | prune 关 | prune 开 |
|---|---|---|
| **Git 删资源后的集群状态** | 孤儿资源永久留存 | 集群与 Git 一致 |
| **数据安全** | ✅ 不会因误删丢数据 | ❌ **误删即丢**（含 PVC 场景） |
| **成本** | 孤儿 LB/公网 IP 继续计费；孤儿 Pod 占配额 | 无泄漏 |
| **对账状态** | ArgoCD 可能长期显示 `OutOfSync` / 提示 orphan | 干净 |
| **适合** | **核心生产库、有状态中间件、团队 Git 流程不成熟** | 无状态服务、开发测试环境、Git 流程成熟的生产 |

> **务实建议**：**分级处理**。无状态服务开 prune；**有状态/有数据的服务关 prune，改用"人工确认 + 分步删除"**。同时用 `PruneLast=true` + `PrunePropagationPolicy=foreground` 把删除放在最后一步、且等到其他资源健康之后。

### 5.2 selfHeal 开 vs 关

| 维度 | selfHeal 关 | selfHeal 开 |
|---|---|---|
| **漂移处理** | 只报告（`OutOfSync`），靠人处理 | 自动改回 Git |
| **适合"配置即真相"的资源** | 差（配置漂移会一直存在） | ✅ 优 |
| **适合"临时运维动作"的资源** | ✅ 优（临时改动能生效） | ❌ 差（**临时改被抹掉**） |
| **应急能力** | 保留 | **削弱**（救火动作被撤销） |
| **推荐的折中** | 核心生产关 + 配漂移告警 | 基础服务开 |

> **一句话**：selfHeal 是"**用运维自由度换配置一致性**"的交易。**团队越依赖临时改集群，越不该开 selfHeal。**

### 5.3 ArgoCD 独占一个集群 vs 多实例共用

| 维度 | 一套 ArgoCD 管全部 | 每集群一套 ArgoCD（cluster-in-cluster） |
|---|---|---|
| **故障爆炸半径** | **全挂**（repo-server / controller 故障影响所有集群） | 单集群故障只影响自己 |
| **资源成本** | 低（一套控制面） | 高（每套都要 HA、监控、升级） |
| **升级节奏** | 一次升级影响所有集群 | 可分批升级，**风险可控** |
| **凭证管理** | 一个实例持有所有集群凭证，**集中风险** | 分散，每个实例只管一个集群 |
| **合规隔离** | 弱（同信任域） | 强 |
| **适合** | 单一团队、单一信任域 | 多业务线 / 不同安全等级 / 强合规 |

### 5.4 ApplicationSet vs 手写多个 Application

| 维度 | 手写 N 个 Application | ApplicationSet |
|---|---|---|
| **新增环境** | 复制粘贴 | generator 加一行 |
| **一致性** | 人肉保证，**迟早漂移** | 模板唯一，**结构上不漂移** |
| **参数化** | 整份复制 | `{{key}}` 插值 |
| **清理** | 手动删 Application，**资源留成孤儿** | 改 generator，**连带 prune** |
| **调试难度** | 低 | 中（要看"生成的 Application"） |
| **适用规模** | ≤ 5 个 | 大量、多维度组合 |

---

## 6. 踩坑与排障

> **报错原文说明**：本篇未在你的环境实测，下表中的错误文本标注为「典型报错（来自官方 issue / 社区）」的，是公开讨论中反复出现的形态；标注「官方文档描述」的，是 ArgoCD 官方 stable 文档中明确写出的行为。**不臆造具体版本号与行号。**

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| 生产资源被 ArgoCD 删了 | `kubectl get` 查不到该对象；ArgoCD 事件里有 prune 记录 | **Git 里删掉了该资源定义 + 开了 `automated.prune`** | ① 立刻 `git revert` 那个提交；② `argocd app history` 找到删之前的 revision，`argocd app rollback <app> <rev>` 重建资源；③ **注意数据不一定回来**（PVC 被删则真丢） | 生产默认**关 prune**；必须开时配 `PruneLast=true` + `foreground`；Git 删除走"先标 deprecated 再删"两阶段 |
| 临时扩容后副本数自己掉回去了 | ArgoCD 事件里有 self-heal 记录；UI 一度 `OutOfSync` | **selfHeal 开启**，把 `kubectl scale` 改回去了 | ① 应急：给该资源临时移除 tracking 标签/改用 `compare-options` 豁免；② 正规做法：**改 Git**（把 replicas 改大并提交） | 核心服务关 selfHeal，只做漂移告警；把"临时变更"规范化到 Git 流程 |
| 应用一直 `OutOfSync`，同步后没变化 | Events 里同步成功但 diff 仍在 | `ignoreDifferences` 写得太宽，**真实差异被当成"不算差异"** | 检查 `spec.ignoreDifferences` 的 `jqPathExpressions` / `jsonPointers` 是否过宽 | 区分"确实该忽略的字段"（如 HPA 写的 replicas）与"必须一致的字段"，逐条列，不要用大范围 `.*` |
| 应用卡在某个波次不往下走 | UI 显示同步中；某波次资源健康状态为 `Progressing` 或 `Degraded`（**这两个是 ArgoCD 的资源健康状态值，非报错原文**） | **sync-wave 跨波次要等前一波次 Healthy**；前一波有资源不健康 | 找出停在第几波（`argocd app get --show-operation`），看该波次资源的健康状态 | 同一 wave 内不放有依赖的资源；给每个波次写清楚"健康判据" |
| 同步失败：事件里报资源类型不存在（典型报错：**`no matches for kind`**） | Application 同步事件里报资源类型不存在 | **CRD 的 wave 比 CR 小**（或 CRD 根本没同步） | 给 CRD 加 `argocd.argoproj.io/sync-wave: "-1"`，CR 用 `0` | CRD 与 CR **永远分波次**；CRD 单独一个仓库/目录时也要显式声明 wave |
| PreSync 的 Job 失败 → 一个业务资源都没更新 | Application 的同步操作状态为 `Operation Failed`（**ArgoCD 操作状态值，非报错原文**），事件指出 hook 失败 | **PreSync 失败会阻断整个同步**（这是设计如此） | 看 Job 日志：`kubectl -n <ns> logs job/<job-name>` | PreSync 只放"必须成功且失败即停"的动作；用 `hook-delete-policy: HookFailed` 保留现场 |
| PostSync 失败 → 应用显示失败，但资源已是新版 | 资源确实已更新，Application 的同步操作状态却是 `Failed`（**ArgoCD 操作状态值，非报错原文**） | **PostSync 在资源全部同步成功之后执行**，它失败会把整体标失败 | 区分"发布失败"与"后置校验失败"；必要时改用 Sync 阶段的 Job | 后置冒烟测试用 `PostSync` + 单独告警；**别用它做"必须成功"的门禁** |
| Hook 的 Job 一直存在 | `kubectl get job` 里堆了一堆旧 Job | 没设 `argocd.argoproj.io/hook-delete-policy` | 补上三个取值：`HookSucceeded`、`HookFailed`、`BeforeHookCreation` | hook 模板**统一带上** delete-policy 注解 |
| ApplicationSet 不生成应用 / 生成 0 个 | UI 里 ApplicationSet 状态正常但没有子 Application | `clusters` generator 依赖的 cluster secret **标签不是 `argocd.argoproj.io/secret-type: cluster`** | 检查 Secret 的标签；检查 Secret 所在命名空间 | 注册集群的脚本里加**标签断言** |
| ApplicationSet 里出现重名 Application | 两个 ApplicationSet 生成了同名 Application | 模板的 `metadata.name` 在不同 ApplicationSet 间**撞名** | 名字里带上 ApplicationSet 名字前缀或环境维度 | 模板命名规范：`{{appsetname}}-{{cluster}}-{{env}}`；加 CI 检查 |
| 删了 ApplicationSet，**生产资源一起没了** | 资源被 prune 掉 | ApplicationSet **自动清理**不再生成的 Application，且 Application 上的 finalizer 会连带删资源 | 从 Git 恢复定义再同步 | 删除前**先禁用 ApplicationSet 的 finalizer**；删 generator 条目前先确认 |
| ArgoCD 挂了一个，所有集群都不同步 | 所有 Application 卡在 `OutOfSync` 不动 | **repo-server 或 application-controller 故障**（这是全局单点） | 查这两个 Deployment 的 Pod 状态与日志 | 见下一行"HA 方案" |
| **ArgoCD 自身成为单点** | 同上 | repo-server 单副本、controller 单副本、Redis 单点、**Git 仓库单点** | ① repo-server **多副本**；② controller **多副本 + leader election**；③ Redis 开 **HA(Sentinel)** 模式；④ **Git 仓库多副本**（GitHub Enterprise 多节点 / 镜像仓库多副本），避免单一 Git 服务故障导致全部瘫痪 | 把 **ArgoCD 的可用性**当成生产依赖来监控；repo 与 controller 都要有副本数告警 |
| 多集群之间同步顺序不可控 | A、B 集群同时开始更新 | Application 之间**没有顺序保证**，sync-wave 只在单个 Application 内有效 | 顺序控制放到 **Git/CI 层**：先合 A、全绿再合 B | 不要指望 ArgoCD 做跨集群编排 |
| rollback 之后资源又被删了 | rollback 后过一会儿资源消失 | Git 里仍是删除状态，**automated 再次触发 prune** | 先 revert Git，再 rollback；rollback 期间**暂停 automated** | 明确"回滚"的两步流程（Git revert + 集群回退） |
| 同步报"资源被多个 Application 共享" | 同步被 warning（默认）或中断（开了 `FailOnSharedResourceWarning`） | 两个 Application 的 tracking 标识相同 | 用 `argocd.argoproj.io/tracking-id` 区分，或把共享资源拆到独立 Application | 资源归属在设计阶段就要定，不要两个应用都管一个对象 |
| 应用"一直同步中" | 操作状态长时间 `Running` | 某个资源的健康检查不通过（wait/健康评估），**后续波次被卡** | `argocd app get <app> --show-operation` 看卡在哪个资源 | 给 CRD 补 `additionalPrinterColumns`/健康检查；自定义资源要实现 ArgoCD health check |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `argo-cd.readthedocs.io/en/stable/` | 概览、Application/AppProject/ApplicationSet、Sync Phases、Resource Hooks、Projects、Cluster Management、User Guide 各节 | ✅ 2026-10-24 |
| S2 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/sync-options/` | **同步策略（Prune / SelfHeal / Sync Options 全表）** | ✅ 2026-10-24 |
| S3 | 官方 | `kubernetes.io/docs/reference/using-api/` | 期望态/实际态与资源定义字段语义交叉验证 | ✅ 2026-10-24 |
| S4 | 中文 | `kubernetes.io/zh-cn/docs/concepts/extend-kubernetes/operator/` | 控制器/协调循环模型（与 ArgoCD controller 对照） | ✅ 2026-10-24 |
| S5 | 中文 | `kubernetes.io/zh-cn/docs/tasks/manage-kubernetes-objects/declarative-config/` | 声明式配置与 Git 作为期望态来源 | ✅ 2026-10-24 |
| S6 | 中文 | `kubernetes.io/zh-cn/docs/concepts/security/rbac-good-practices/` | K8s 侧 RBAC 最佳实践（ArgoCD ServiceAccount 收敛） | ✅ 2026-10-24 |
| S7 | 中文 | `kubernetes.io/zh-cn/docs/concepts/workloads/controllers/deployment/` | Deployment 健康判据（wave 跨波次等待 Healthy） | ✅ 2026-10-24 |
| S8 | 中文 | `developer.aliyun.com` | ArgoCD / ACK 上的中文实践与踩坑 | ✅ 2026-10-24 |
| S9 | 中文 | `cloud.tencent.com/developer` | ArgoCD 多集群与 ApplicationSet 中文实践 | ✅ 2026-10-24 |
| S10 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 托管版上的 ArgoCD 形态与集群接入 | ✅ 2026-10-24 |
| S11 | 云厂商 | `cloud.tencent.com/document/product/457` | TKE 上多集群纳管的托管形态 | ✅ 2026-10-24 |

> **待验证清单**（本篇明确不确定的点，均标注在正文对应位置）：
> 1. `PrunePropagationPolicy` 在**未显式指定时**的默认传播行为（随 ArgoCD 版本演进）——需查你所用版本的 official sync-options 页。
> 2. `application-controller` 的 `--app-resync` 默认值（本文写"官方标称默认 180 秒"）——需以你版本 `argocd-application-controller --help` 实测为准。
> 3. 第 6 节的报错文本均为**公开讨论中的典型形态**，本篇未在你的环境实测，请以实际事件原文为准。
>
> **非索引内 URL：0**（全部 URL 取自 [`../09-参考索引/README.md`](../09-参考索引/README.md) 中标 ✅ 的条目）。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入是"Git 仓库的一个路径"；输出是"集群里一组与期望态一致的资源"。中间是 `repo-server` 渲染出 desired state → `application-controller` 读取 live state → 求 diff → 按 sync-wave / hook 编排同步 → 回写 Application 的 `status`（Synced / OutOfSync）。**关键一句：ArgoCD 不创建 Pod，它创建工作负载对象，Pod 由 K8s 内置控制器创建。**
2. **能故意改坏再倒推吗？** —— 四个破坏实验：
   - **① 把 `automated.prune` 设成 true，然后在 Git 里删掉 guestbook 的 Service 定义** → 观察集群里 Service 真的消失 → 倒推出"prune = 删除 Git 里没有的追踪中资源"，再 `argocd app history` + `rollback` 重建，并**意识到数据没回来**。
   - **② 手工 `kubectl scale` 改副本数，再等 30 秒** → 副本数被改回 → 倒推出 selfHeal 的机制与代价（临时救火动作被撤销）。
   - **③ 把 CRD 的 `sync-wave` 从 `-1` 改成 `0`，同时把 CR 的改成 `-1`** → 同步报 `no matches for kind` → 倒推出 wave 决定顺序、且 CRD 注册有延迟。
   - **④ 把 PreSync 的 Job 镜像改成不存在的 tag** → 同步被完全阻断、业务资源一个没动 → 倒推出 hook 的失败传播规则。
3. **能画上下游吗？** —— 画出 `Git 仓库 → repo-server（渲染） → application-controller（对账：desired vs live） → K8s API（apply/prune） → 内置控制器 → Pod` 这条链，并在每个环节标出"这一步挂了会看到什么"：repo-server 挂 → 所有应用渲染失败；controller 挂 → 不自动同步；apiserver 拒绝 → 该资源 `Failed` 但其余继续（**同步失败是隔离的，组件故障不是隔离的**）。

---

**下一篇**：[`L6-渐进式交付-ArgoRollouts.md`](L6-渐进式交付-ArgoRollouts.md)（同步只是起点，本篇讲的是"怎么在 ArgoCD 之上做金丝雀/蓝绿"） · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md) · **相关**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md)
