# L9 · 安全、RBAC 与合规（懂原理 · 会自查 · 会对等保）

> **本篇定位**：K8s 的安全不是"一个功能"，是**三道闸门串在一起**：**认证（你是谁）→ 授权（你能干什么）→ 准入（这个对象允许存在吗）**。本篇把这三道闸门讲透，然后落到生产上真正要配的东西：ServiceAccount、RBAC、NetworkPolicy、Pod Security Admission、Secret 加密、镜像供应链，最后用**等保 2.0 的控制项视角**把整篇串一遍。
> **前置**：L2 架构与对象模型（必须知道 apiserver 的处理链是"认证 → 授权 → 准入 → 写 etcd"）、L7 调度与 QoS（**命名空间隔离的强弱**在 2.6 会展开）。
> **学完能做什么**：① 说清认证/授权/准入三者各自拦什么、报错长什么样；② 解释"每个 Pod 为什么都需要一个 ServiceAccount"以及默认 SA 的风险；③ 用 `kubectl auth can-i --list` 给自己的集群做一次权限自查；④ 写出"默认拒绝 + 允许同 namespace + 允许 DNS"的 NetworkPolicy 三件套；⑤ 说清 etcd 里的 Secret 到底是什么形态、怎么开静态加密；⑥ 用等保 2.0 的控制项分类把 K8s 的安全能力对上去。
> **在 21 天里**：D8（安全与合规）、D9（生产化治理）。
> **环境/版本基准**：本地 kind 集群 `day2`（`kubectl v1.37.1` / `Server v1.37.0`）。**Pod Security Admission 自 v1.25 起为 Stable**；本文所有默认值与字段语义以官方文档标注为准并注明出处。**本篇未执行任何 kubectl 命令改动集群**，所有 yaml 为可自行验证的最小样例。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L9 安全、RBAC 与合规
│
├─ 1. 概念
│   ├─ 1.1 一句话：K8s 安全是三道闸门，不是一个开关
│   ├─ 1.2 容器化带来的新攻击面
│   └─ 1.3 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 apiserver 的请求处理链：认证 → 授权 → 准入 → 持久化
│   ├─ 2.2 ServiceAccount 是什么、为什么每个 Pod 都需要
│   ├─ 2.3 Role / ClusterRole / RoleBinding / ClusterRoleBinding
│   ├─ 2.4 RBAC 最佳实践：默认拒绝 + 最小权限
│   ├─ 2.5 为什么 system:serviceaccount:default:default 很危险
│   ├─ 2.6 权限自查：kubectl auth can-i --list
│   ├─ 2.7 NetworkPolicy：默认全互通，白名单要显式开启
│   ├─ 2.8 Pod Security Admission 与三层 securityContext
│   ├─ 2.9 Secret 的真相：etcd 里是 base64 明文
│   ├─ 2.10 Secret 静态加密：EncryptionConfiguration + KMS
│   ├─ 2.11 镜像供应链安全：签名验证 + 扫描 + SBOM
│   └─ 2.12 关键参数与默认值
│
├─ 3. 动手：给自己的集群做一次安全体检
│   ├─ 3.1 第一步：看看自己的权限有多大
│   ├─ 3.2 第二步：看默认 ServiceAccount 的暴露面
│   ├─ 3.3 第三步：NetworkPolicy 三件套
│   └─ 3.4 验证清单
│
├─ 4. 资源清单逐字段
│   ├─ 4.1 Role / ClusterRole / Binding
│   ├─ 4.2 NetworkPolicy
│   └─ 4.3 securityContext
│
├─ 5. 横向对比
│   ├─ 5.1 RBAC vs Webhook 鉴权（准入）
│   ├─ 5.2 NetworkPolicy vs 云安全组
│   └─ 5.3 三道闸门速查
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

> **K8s 的安全模型是三道闸门，任何一个请求必须依次通过三道**：**认证（Authentication）回答"你是谁"，授权（Authorization）回答"你能做什么"，准入（Admission）回答"这个对象允许被创建吗"。**

L2 的 9 步链路里，第 2 步写的是"apiserver：**认证**（你是谁）→ **授权**（RBAC 允许吗）→ **准入控制**（Webhook/策略）"[S1]。本篇把这三步展开。

**三道闸门的分工（本篇的总纲）**：

| 闸门 | 回答的问题 | 由谁实现 | 拦不住什么 |
|---|---|---|---|
| **认证 Authentication** | **你是谁？** | 证书 / Token / OIDC / 云厂商身份 | 认证通过 ≠ 你有权限 |
| **授权 Authorization** | **你能做什么？** | **RBAC**（默认）、Webhook、Node | 授权通过 ≠ 对象合规 |
| **准入 Admission** | **这个对象允许存在吗？** | **Pod Security Admission**、ValidatingWebhook、MutatingWebhook、LimitRange、ResourceQuota | 准入通过 ≠ 运行期安全 |

> **一个必须刻进脑子的事实**：**RBAC 本身不做"准入"**。RBAC 只回答"这个主体能不能对这类对象做这个动作"。**"这个 Pod 用了特权容器"这种判断，RBAC 管不了——那是准入控制器的活。**
> **这是本篇最重要的一句**：**RBAC 管"人/服务账号能干什么"，准入管"对象长什么样"。两者正交，缺一不可。**

### 1.2 容器化带来的新攻击面

| 新攻击面 | 具体风险 | 对应的 K8s 机制 | 本篇章节 |
|---|---|---|---|
| **一个 Pod 就是一个可被利用的进程** | 逃逸 → 拿到节点 root | Pod Security Admission（禁特权容器）、`securityContext` | 2.8 |
| **API Server 暴露在集群内** | 拿到 ServiceAccount Token 就能调 API | ServiceAccount + RBAC 最小权限 | 2.2 / 2.4 |
| **Secret 是所有 Pod 都能看到的** | 数据库密码泄露 | RBAC + Secret 静态加密 + 不自动挂载 Token | 2.4 / 2.9 / 2.10 |
| **Pod 之间默认互通** | 一个 Pod 被攻破 → 横向移动到全网 | **NetworkPolicy** | 2.7 |
| **镜像来自不可信仓库** | 供应链投毒 | 签名验证 + Trivy 扫描 + SBOM | 2.11 |
| **命名空间被误当作安全边界** | 跨 namespace 越权 | RBAC（namespace 级）+ NetworkPolicy（跨 ns 需显式放行） | 2.3 / 2.6 |
| **审计日志缺失** | 事后无法追责 | apiserver 审计日志 + `kubectl auth can-i` 自查 | 2.6 |

> **官方原文（RBAC 最佳实践，中文）** [S3]：
> > **命名空间应该用于隔离不同的信任级别或不同租户所需的资源。遵循最小特权原则并分配最小权限集仍被认为是最佳实践，但命名空间内的边界概念应视为比较弱。**
>
> **"命名空间是逻辑边界，不是安全边界"** —— 这句话是本篇 2.7 的前提。

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Authentication | 认证 | **你是谁**（证书 / Token / OIDC） |
| Authorization | 授权 | **你能做什么**（RBAC / Webhook） |
| Admission | 准入 | **这个对象允许存在吗**（策略 / Webhook） |
| Admission controller | 准入控制器 | 在对象写入 etcd **之前**检查/改写它的插件 |
| `ServiceAccount` | 服务账号 | **Pod 的身份**，非人类账号 |
| `default` SA | 默认服务账号 | **每个命名空间自动创建**的那一个 |
| `RBAC` | 基于角色的访问控制 | K8s 的默认授权机制 |
| `Role` | 角色（命名空间级） | 一组 `PolicyRule`，**只在一个命名空间内生效** |
| `ClusterRole` | 集群角色 | 一组 `PolicyRule`，**可被绑到任意命名空间** |
| `RoleBinding` | 角色绑定（命名空间级） | 把 Role/ClusterRole 绑给主体，**权限只在绑定的那个命名空间内** |
| `ClusterRoleBinding` | 集群角色绑定 | 把 ClusterRole 绑给主体，**全集群生效** |
| `PolicyRule` | 策略规则 | `apiGroups` + `resources` + `verbs`（+ `resourceNames`） |
| `subjects` | 主体 | 被授权者：`User` / `Group` / `ServiceAccount` |
| `roleRef` | 角色引用 | Binding 指向的 Role/ClusterRole（**创建后不可改**） |
| `aggregationRule` | 聚合规则 | 让 ClusterRole 的规则**由控制器自动合并**生成 |
| `escalate` / `bind` / `impersonate` | 提权 / 绑定 / 伪装 | 三个**特殊动词**，是 RBAC 提权防护的关键 |
| `NetworkPolicy` | 网络策略 | **显式开启**的 Pod 间通信白名单 |
| `podSelector` | Pod 选择器 | 网络策略的作用对象 |
| `namespaceSelector` | 命名空间选择器 | 放行哪些命名空间 |
| `ingress` / `egress` | 入向 / 出向 | 网络策略的两个方向 |
| PSA | Pod 安全准入 | 内置准入控制器，按命名空间强制 Pod 安全等级 |
| `privileged` / `baseline` / `restricted` | 特权 / 基线 / 受限 | Pod 安全标准的三档（**累进**） |
| `securityContext` | 安全上下文 | 容器/进程的安全属性配置 |
| `EncryptionConfiguration` | 加密配置 | apiserver 侧的**静态加密**配置文件 |
| KMS | 密钥管理服务 | 存 KEK 的外部服务，配合信封加密 |
| DEK / KEK | 数据密钥 / 密钥加密密钥 | 信封加密的两层密钥 |
| SBOM | 软件物料清单 | 镜像里有哪些包、什么版本的清单 |

---

## 2. 机制

### 2.1 apiserver 的请求处理链：认证 → 授权 → 准入 → 持久化

```
   kubectl / 客户端
        │  HTTPS 请求 + 身份凭据（证书 / Bearer Token / OIDC）
        ▼
┌──────────────────────────────────────────────────────────┐
│ ① 认证 Authentication                                    │
│    你是谁？                                               │
│    · 校验证书签名 / Token 签名 / OIDC Token 有效期          │
│    · 把身份映射成用户名 / 组 / ServiceAccount              │
│    · 失败 → HTTP 401 Unauthorized                          │
└───────────────┬──────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────┐
│ ② 授权 Authorization                                      │
│    你能做什么？                                            │
│    · RBAC 查 Role/ClusterRole × Binding × PolicyRule      │
│    · 失败 → HTTP 403 Forbidden                             │
│    · 注意：Node 授权、Webhook 授权也在这一层                │
└───────────────┬──────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────┐
│ ③ 准入 Admission                                          │
│    这个对象允许存在吗？                                    │
│    · MutatingWebhook：先改写对象                          │
│    · ValidatingWebhook：再校验对象（不合格 → 拒绝）        │
│    · 内置控制器：PodSecurity / LimitRange / ResourceQuota   │
│    · 失败 → HTTP 403 Forbidden（含准入控制器的原因说明）    │
└───────────────┬──────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────┐
│ ④ 持久化 → etcd                                           │
└──────────────────────────────────────────────────────────┘
```

**两道闸门都返回 403 —— 这是本篇最大的排障坑**：

| 阶段 | 典型报错 | 怎么区分 |
|---|---|---|
| **② 授权（RBAC）** | `Error from server (Forbidden): <resource> is forbidden: User "xxx" cannot get resource "yyy" in API group "zzz" in the namespace "nnn"` | 报错里有 **`User "..."`** 和 **`cannot ... resource`** |
| **③ 准入（Quota / PSA / Webhook）** | `Error from server (Forbidden): exceeded quota: <name>, requested: ..., used: ..., limited: ...` | 报错里有 **`exceeded quota`** 或 **`violates PodSecurity`** 或 **`admission webhook ... denied the request`** |
| **③ 准入（LimitRange）** | `Pod "xxx" is invalid: spec.containers[0].resources.requests: Invalid value: "700m": must be less than or equal to cpu limit` | 报错里有 **`is invalid`** + 字段路径 |

> **403 的两种来源必须能分辨**：**授权失败 = "你不能做这事"**；**准入失败 = "这事本身不合规"**。前者要改 RBAC，后者要改对象或策略。

### 2.2 ServiceAccount 是什么、为什么每个 Pod 都需要

**官方定义（Service Accounts，英文）** [S4]：

> A service account is a type of **non-human account** that, in Kubernetes, provides a **distinct identity** in a Kubernetes cluster. Application Pods, system components, and entities inside and outside the cluster can use a specific ServiceAccount's credentials to identify as that ServiceAccount. This identity is useful in various situations, **including authenticating to the API server** or implementing identity-based security policies.

> When you deploy a Pod in a namespace, and you don't manually assign a ServiceAccount to the Pod, **Kubernetes assigns the `default` ServiceAccount for that namespace to the Pod**.

> **When you create a cluster, Kubernetes automatically creates a ServiceAccount object named `default` for every namespace in your cluster.**

> If you delete the `default` ServiceAccount object in a namespace, the control plane replaces it with a new one.

> To assign a ServiceAccount to a Pod, you set the `spec.serviceAccountName` field in the Pod specification. Kubernetes then automatically provides the credentials for that ServiceAccount to the Pod. **In v1.22 and later, Kubernetes gets a short-lived, automatically rotating token using the `TokenRequest` API and mounts the token as a projected volume.**

> To prevent Kubernetes from automatically injecting credentials for a specified ServiceAccount or the `default` ServiceAccount, **set the `automountServiceAccountToken` field in your Pod specification to `false`**.

> When a client that is acting as a ServiceAccount tries to communicate with the Kubernetes API server, the client includes an **`Authorization: Bearer <token>`** header with the HTTP request. The API server checks the validity of that bearer token as follows:
> 1. Checks the token signature.
> 2. Checks whether the token has expired.
> 3. Checks whether object references in the token claims are currently valid.
> 4. Checks whether the token is currently valid.
> 5. Checks the audience claims.

**"为什么每个 Pod 都需要一个 ServiceAccount"——完整的因果链**：

```
Pod 要跟 apiserver 说话（比如读自己的 ConfigMap、查自己的状态）
        │
        ▼
apiserver 要知道"你是谁"  →  必须有身份
        │
        ▼
Pod 的身份 = ServiceAccount（唯一的一等公民）
        │
        ▼
没有 SA？→ 分配该命名空间的 default SA
        │
        ▼
kubelet 把该 SA 的 Token 挂进 Pod（默认路径 /var/run/secrets/kubernetes.io/serviceaccount/）
        │
        ▼
Pod 里的进程用这个 Token 调 apiserver → 被 RBAC 判定能干什么
```

> **官方 API 参考对 ServiceAccount 的定义（最简洁的版本）** [S5]：
> > ServiceAccount binds together: * a name, understood by users, and perhaps by peripheral systems, for an identity * a principal that can be authenticated and authorized * a set of secrets

**Token 的现代形态（v1.22+ 的三个关键变化）**：

| 变化 | 旧（v1.21 及以前） | 新（v1.22+） |
|---|---|---|
| **形态** | 长期有效的 Secret（`kubernetes.io/service-account-token`） | **短期自动轮转的 Token**（TokenRequest API） |
| **有效期** | 长期有效（直到 Secret 被删） | **短生命周期**，kubelet 自动轮转 |
| **挂载方式** | Secret volume | **projected volume**（投射卷） |
| **创建时机** | 创建 SA 时就自动建 Secret | **Pod 创建时才签发**，与 Pod 生命周期绑定 |

**这意味着什么（安全收益）**：

1. **Token 泄露的窗口变小**——短生命周期，泄露的 Token 很快失效。
2. **Token 与 Pod 绑定**——官方原文：*"The TokenRequest API produces **bound tokens** for a ServiceAccount. This binding is linked to the lifetime of the client, such as a Pod, that is acting as that ServiceAccount."* [S4] → **Pod 删了，Token 就失效了**。
3. **不需要给每个命名空间手动创建 SA Secret**。

**官方 API 参考对 ServiceAccount 对象字段的说明** [S5]：
> ServiceAccount binds together: * a name... * a principal that can be authenticated and authorized * a set of secrets

### 2.3 Role / ClusterRole / RoleBinding / ClusterRoleBinding

**四个对象的官方定义（逐字）**：

| 对象 | 官方原文 | 作用域 |
|---|---|---|
| **Role** | *"Role is a **namespaced**, logical grouping of PolicyRules that can be referenced as a unit by a RoleBinding."* [S6] | **命名空间级** |
| **ClusterRole** | *"ClusterRole is a **cluster level**, logical grouping of PolicyRules that can be referenced as a unit by a RoleBinding or ClusterRoleBinding."* [S7] | **集群级对象**，但绑给谁、给多大范围由 Binding 决定 |
| **RoleBinding** | *"A **RoleBinding** grants permissions **within a specific namespace** whereas a ClusterRoleBinding grants that access cluster-wide."* [S8] | 命名空间级 |
| **ClusterRoleBinding** | 同上 | 集群级 |

**最关键的一条规则（很多人不知道）**：

官方原文 [S8]：

> A RoleBinding may reference any Role in the same namespace. **Alternatively, a RoleBinding can reference a ClusterRole and bind that ClusterRole to the namespace of the RoleBinding.** If you want to bind a ClusterRole to all the namespaces in your cluster, you use a ClusterRoleBinding.

> **A RoleBinding can also reference a ClusterRole to grant the permissions defined in that ClusterRole to resources inside the RoleBinding's namespace.** This kind of reference lets you define a set of common roles across your cluster, then reuse them within multiple namespaces.

> For instance, even though the following RoleBinding refers to a ClusterRole, "dave" (the subject, **case sensitive**) will only be able to read Secrets in the "development" namespace, because the RoleBinding's namespace (in its metadata) is "development".

> **The namespace of the RoleBinding determines where the permissions are granted. This only grants permissions within the "development" namespace.**

> **一句话**：**"权限的大小"由被引用的 Role/ClusterRole 决定；"权限的范围"由 Binding 的类型和所在命名空间决定。**
> **所以"ClusterRole 权限大"是个误解——一个 ClusterRole 被 RoleBinding 引用后，权限只在那个命名空间内生效。**

**`PolicyRule` 的字段（官方 API 参考逐字）** [S9]：

> PolicyRule holds information that describes a policy rule, **but does not contain information about who the rule applies to or which namespace the rule applies to.**

| 字段 | 官方原文 | 读法 |
|---|---|---|
| `apiGroups` | *"apiGroups is the name of the APIGroup that contains the resources. If multiple API groups are specified, any action requested against one of the enumerated resources in any API group will be allowed. **"" represents the core API group** and "*" represents all API groups."* | `""` = 核心组（Pod/Service/ConfigMap），`apps` = 工作负载组，`*` = 全部 |
| `resources` | *"resources is a list of resources this rule applies to. **'\*' represents all resources.**"* | 支持子资源：`pods/log`、`pods/exec`、`deployments/scale` |
| `verbs` | *"verbs is a list of Verbs that apply to **ALL** the ResourceKinds contained in this rule. **'\*' represents all verbs.**"* | `get`/`list`/`watch`/`create`/`update`/`patch`/`delete`/`deletecollection` |
| `resourceNames` | *"resourceNames is an optional **white list** of names that the rule applies to. **An empty set means that everything is allowed.**"* | 精确到具体对象名 |
| `nonResourceURLs` | *"nonResourceURLs is a set of partial urls that a user should have access to. \*s are allowed, but only as the full, final step in the path. **Since non-resource URLs are not namespaced, this field is only applicable for ClusterRoles referenced from a ClusterRoleBinding.** Rules can either apply to API resources (such as "pods" or "secrets") or non-resource URL paths (such as "/api"), **but not both.**"* | 只能配在 ClusterRole + ClusterRoleBinding 上 |

**官方对通配符的说明** [S8]：

> Rather than referring to individual `resources`, `apiGroups`, and `verbs`, you can use the wildcard `*` symbol to refer to all such objects.

**`resourceNames` 的两个限制（官方原文，极易踩坑）** [S8]：

> **If you restrict `list` or `watch` by `resourceName`, clients must include a `metadata.name` field selector in their `list` or `watch` request (that matches the specified `resourceName`) in order to be authorized.** For example: `kubectl get configmaps --field-selector=metadata.name=my-configmap`

> **You cannot restrict `deletecollection` or top-level `create` requests by resource name.** For `create`, this limitation is because the name of the new object may not be known at authorization time. However, the create limitation applies only to top-level resources, **not subresources. For example, you can use the `resourceNames` field with `pods/exec`.**

**`roleRef` 不可变（官方原文）** [S8]：

> After you create a binding, **you cannot change the Role or ClusterRole that it refers to.** If you try to change a binding's `roleRef`, you get a validation error. If you do want to change the `roleRef` for a binding, you need to remove the binding object and create a replacement.

> There are two reasons for this restriction:
> 1. Making `roleRef` immutable allows granting someone `update` permission on an existing binding object, so that they can manage the list of subjects, **without being able to change the role that is granted to those subjects.**
> 2. A binding to a different role is a fundamentally different binding. Requiring a binding to be deleted/recreated in order to change the `roleRef` ensures **the full list of subjects in the binding is intended to be granted the new role**.

**聚合 ClusterRole（官方原文）** [S7]：

> `aggregationRule` is an optional field that describes how to build the Rules for this ClusterRole. **If AggregationRule is set, then the Rules are controller managed and direct changes to Rules will be stomped by the controller.**

> `clusterRoleSelectors` holds a list of selectors which will be used to find ClusterRoles and create the rules. **If any of the selectors match, then the ClusterRole's permissions will be added.**

**四个对象的组合关系图**：

```
                   ┌──────────────────────┐
                   │      Role            │  命名空间级
                   │  （一组 PolicyRule）  │
                   └──────────┬───────────┘
                              │ 被引用
                              ▼
   ┌──────────────┐      ┌──────────────┐
   │ ClusterRole  │─────▶│ RoleBinding  │  命名空间级绑定
   │ （集群级对象）│      │  （限定 ns） │
   └──────┬───────┘      └──────────────┘
          │
          │ 被引用
          ▼
   ┌──────────────────────┐
   │ ClusterRoleBinding   │  集群级绑定
   └──────────────────────┘
          │
          ▼
   subjects: User / Group / ServiceAccount
```

### 2.4 RBAC 最佳实践：默认拒绝 + 最小权限

**官方 RBAC 最佳实践（中文）逐条原文** [S3]：

**① 最小特权**：

> 理想情况下，分配给用户和服务帐户的 RBAC 权限应该是最小的。仅应使用操作明确需要的权限，虽然每个集群会有所不同，但可以应用的一些常规规则：
> - **尽可能在命名空间级别分配权限。授予用户在特定命名空间中的权限时使用 RoleBinding 而不是 ClusterRoleBinding。**
> - **尽可能避免通过通配符设置权限，尤其是对所有资源的权限。** 由于 Kubernetes 是一个可扩展的系统，因此通过通配符来授予访问权限不仅会授予集群中当前的所有对象类型，还包含**所有未来被创建的所有对象类型**。
> - **管理员不应使用 `cluster-admin` 账号，除非特别需要。** 为低特权帐户提供伪装权限可以避免意外修改集群资源。
> - **避免将用户添加到 `system:masters` 组。** 任何属于此组成员的用户都会**绕过所有 RBAC 权限检查**，始终具有不受限制的超级用户访问权限，并且**不能通过删除 `RoleBinding` 或 `ClusterRoleBinding` 来取消其权限**。顺便说一句，如果集群使用 Webhook 鉴权，此组的成员身份也会绕过该 Webhook。

**② 最大限度地减少特权令牌的分发**：

> 理想情况下，不应为 Pod 分配具有强大权限的服务帐户。如果工作负载需要比较大的权限，请考虑以下做法：
> - **限制运行此类 Pod 的节点数量。**
> - **避免将此类 Pod 与不可信任或公开的 Pod 在一起运行。** 考虑使用污点和容忍度、节点亲和性或 Pod 反亲和性确保 Pod 不会与不可信或不太受信任的 Pod 一起运行。

**③ 加固**：

> 审查默认提供的 RBAC 权限为安全加固提供了机会。一般来说，不应该更改 `system:` 帐户的某些权限，有一些方式来强化现有集群的权限：
> - **审查 `system:unauthenticated` 组的绑定，并在可能的情况下将其删除**，因为这会给所有能够访问 API 服务器的人以网络级别的权限。
> - **通过设置 `automountServiceAccountToken: false` 来避免服务账号令牌的默认自动挂载。** 此参数可覆盖 Pod 服务账号设置，而需要服务账号令牌的工作负载仍可以挂载。

**④ 定期检查**：

> 定期检查 Kubernetes RBAC 设置是否有冗余条目和提权可能性是至关重要的。**如果攻击者能够创建与已删除用户同名的用户账号，他们可以自动继承已删除用户的所有权限**，尤其是分配给该用户的权限。

**官方列出的提权风险清单（本篇必背）** [S3]：

| 风险 | 官方原文要点 | 为什么危险 |
|---|---|---|
| **列举 Secret** | *"若允许对 Secrets 执行 `get` 访问，用户就获得了访问 Secret 内容的能力。同样需要注意的是：**`list` 和 `watch` 访问也会授权用户获取 Secret 的内容**。例如，当返回 List 响应时（例如，通过 `kubectl get secrets -A -o yaml`），响应包含所有 Secret 的内容。"* | **很多人只防 `get`，忘了 `list` 也能拿到全部内容** |
| **创建工作负载** | *"在一个命名空间中创建工作负载的权限**隐式地授予了对该命名空间中许多其他资源的访问权限**，例如可以挂载在 Pod 中的 Secret、ConfigMap 和 PersistentVolume。**此外，由于 Pod 可以被任何服务账号运行，因此授予创建工作负载的权限也会隐式地授予该命名空间中任何服务账号的 API 访问级别。**"* | **能建 Deployment ≈ 能用该 ns 里所有 SA 的权限** |
| **创建 PV** | *"如果允许某人或某个应用创建任意的 PersistentVolume，则该访问权限包括创建 `hostPath` 卷，这意味着 Pod 将可以访问对应节点上的下层主机文件系统。"* | 等于拿到节点文件系统 |
| **访问 Node 的 `proxy` 子资源** | *"有权访问 Node 对象的 `nodes/proxy` 子资源的用户有权访问 kubelet API，这允许他们在有权访问的节点上的所有 Pod 上执行命令。**此访问绕过审计日志记录和准入控制**……**`get` `nodes/proxy` 权限并非只读权限**。"* | **一个 `get` 就能 exec 进别人 Pod** |
| **`escalate` 动词** | *"通常，RBAC 系统会阻止用户创建比他所拥有的更多权限的 `ClusterRole`。而 `escalate` 动词是个例外。"* | 绕过"不能创建超出自己权限的角色"保护 |
| **`bind` 动词** | *"授予此权限的用户可以绕过 Kubernetes 对权限提升的内置保护，用户可以创建并绑定尚不具有的权限的角色。"* | **可以把 `cluster-admin` 绑给自己** |
| **`impersonate` 动词** | *"此动词允许用户伪装并获得集群中其他用户的权限。"* | 变成别人 |
| **控制准入 Webhook** | *"可以控制 `validatingwebhookconfigurations` 或 `mutatingwebhookconfigurations` 的用户可以控制能读取任何允许进入集群的对象的 webhook。"* | **可以自己写个 webhook 把所有请求放行或全部拒绝** |
| **修改命名空间标签** | *"可以对命名空间对象执行 `patch` 操作的用户……可以修改命名空间的标签。在使用 Pod 安全准入的集群中，**这可能允许用户将命名空间配置为比管理员预期更宽松的策略**。对于使用 NetworkPolicy 的集群，用户所设置的标签可能间接导致对某些本不应被允许访问的服务的访问权限被开放。"* | **改一个标签，PSA 和 NetworkPolicy 的策略就形同虚设** |

**"默认拒绝"在 K8s 里怎么实现（这是本节的关键）**：

> **K8s 的 RBAC 是"白名单"模型**：**没有任何 Binding 授权 = 没有任何权限**。这本身就是"默认拒绝"。
> **但要注意一个例外**：`system:unauthenticated` 组有默认绑定（授予有限的只读权限），官方建议"**在可能的情况下将其删除**" [S3]。
>
> **生产上的"默认拒绝"实操**：
> 1. **不创建任何 ClusterRoleBinding**（除了必要的系统组件）——这样命名空间里就是默认拒绝。
> 2. **每个业务工作负载建一个专属 SA**，不要用 `default`。
> 3. **每个 SA 只绑一个 RoleBinding**，Role 写在它自己的命名空间里。
> 4. **不写通配符**：`resources: ["*"]` / `verbs: ["*"]` 一律不写（官方明确反对 [S3]）。
> 5. **不给 Secret 的 `list`/`watch`**（官方明确 `list` 也能拿到内容 [S3]）。

### 2.5 为什么 `system:serviceaccount:default:default` 很危险

**这个身份串的含义**：`system:serviceaccount:<命名空间>:<服务账号名>`。所以 `system:serviceaccount:default:default` = **default 命名空间里的 default 服务账号**。

**危险的四层原因**：

| # | 原因 | 官方依据 |
|---|---|---|
| **① 它是"人人都有"的默认身份** | 官方：*"When you deploy a Pod in a namespace, and you don't manually assign a ServiceAccount to the Pod, **Kubernetes assigns the `default` ServiceAccount** for that namespace to the Pod."* [S4] | [S4] |
| **② 每个命名空间都有一个** | 官方：*"**Kubernetes automatically creates a ServiceAccount object named `default` for every namespace** in your cluster."* [S4] | [S4] |
| **③ 删了还会被重建** | 官方：*"If you delete the `default` ServiceAccount object in a namespace, the control plane replaces it with a new one."* [S4] | [S4] |
| **④ Token 默认自动挂载进 Pod** | 官方：*"By default, Kubernetes provides the Pod with the credentials for an assigned ServiceAccount."* [S4]；官方加固建议：`automountServiceAccountToken: false` [S3] | [S4] [S3] |

**风险的具体形态**：

```
场景：某个开发者为了图省事，写了一个 Deployment 忘了指定 serviceAccountName
        │
        ▼
Pod 用 default SA 跑起来，Token 自动挂进容器
        │
        ▼
攻击者拿到这个 Pod 的 shell（哪怕只是一个业务漏洞）
        │
        ▼
读 /var/run/secrets/kubernetes.io/serviceaccount/token
        │
        ▼
用这个 Token 调 apiserver
        │
        ▼
如果 default SA 被绑了任何 ClusterRoleBinding → 权限是集群级的
        │
        ▼
横向移动到全集群
```

**官方原文（关于"能建工作负载"的隐含授权）** [S3]：

> 由于 Pod 可以被任何服务账号运行，因此授予创建工作负载的权限也会隐式地授予该命名空间中任何服务账号的 API 访问级别。

> **这条原文把风险链闭合了**：**default SA 本身可能权限不大，但"任何人都能用它建 Pod"这件事，让 default SA 的权限变成了"公开的权限"。**

**生产上的三条硬规矩**：

1. **每个工作负载一个专属 SA**（`spec.serviceAccountName` 显式指定）。
2. **不用 ServiceAccount 就不挂 Token**（`automountServiceAccountToken: false`，或 Pod 级 `spec.automountServiceAccountToken: false`）。
3. **`default` SA 不绑任何 ClusterRoleBinding**；如果非要用，给它绑一个"什么权限都没有"的 Role。

### 2.6 权限自查：`kubectl auth can-i --list`

**这是本篇最实用的一条命令。**

**用法（官方 kubectl 速查里的 `kubectl auth` 命令族）** [S10]：

```bash
# ① 我是谁？（身份 + 组）
kubectl auth whoami

# ② 我能做什么？（列出全部权限）—— 最常用
kubectl auth can-i --list

# ③ 我能不能做某件具体的事？
kubectl auth can-i get pods
kubectl auth can-i create deployments -n prod
kubectl auth can-i list secrets -n prod

# ④ 不行的话，为什么不行？
kubectl auth can-i get secrets -n prod
# 输出里会告诉你被哪条 Role 拒绝了
```

**`--list` 的输出怎么读（关键）**：

```
# 授权不足时的输出形态（官方 kubectl 行为）
No RBAC policy matched
```

**输出里的三个部分**：

| 部分 | 含义 | 怎么看 |
|---|---|---|
| **资源列表** | 你被授权的资源与动词 | **看到 `*` 就危险了**——说明有通配符权限 |
| **`SelfSubjectRulesReview`** | apiserver 返回的"你的权限全表" | **这是 apiserver 算出来的，不是客户端本地判断** |
| **`No RBAC policy matched`** | **你什么权限都没有** | **这就是"默认拒绝"的样子** |

> **注意 `--list` 的一个重要特性**：它返回的是 **SelfSubjectRulesReview**，**apiserver 会把"所有匹配你身份的 Role/ClusterRole 的规则合并"**再返回给你。
> **但它有一个已知的表达问题**：**它无法区分"你没有权限"和"你被某个 ResourceQuota / 准入策略挡住了"**——**它只反映 RBAC 层**。**所以 `--list` 显示 `yes` 不代表你的操作一定能成功**（还要过配额和准入）。

**权限自查的标准动作清单（背下来）**：

```bash
# ① 我是不是用了过大的身份？
kubectl auth whoami
kubectl auth can-i --list -n default

# ② 我是不是 cluster-admin？（危险信号）
kubectl auth can-i --list | head -20
# 出现 resources 为 "*"、verbs 为 "*" → 立刻收敛

# ③ default SA 绑了什么？
kubectl get clusterrolebinding -o json | grep -A3 'system:serviceaccount:default:default'

# ④ 有没有漏网的通配符授权？
kubectl get clusterrole -o name | while read r; do \
  kubectl get "$r" -o jsonpath='{.metadata.name}{" "}{.rules}{"\n"}' 2>/dev/null; \
done | grep '"\*"'

# ⑤ 哪些 ServiceAccount 能读写 Secret？
kubectl get clusterrolebinding -o jsonpath='{range .items[*]}{.subjects[*].name}{" "}{.roleRef.name}{"\n"}{end}' \
  | grep -E 'secret|admin'
```

> **第 ③ 步和第 ④ 步是安全自查的"两个必查项"**：**默认 SA 绑了什么** + **有没有通配符**。

### 2.7 NetworkPolicy：默认全互通，白名单要显式开启

**官方原文（Network Policies，英文）** [S11]：

> **By default, a pod is non-isolated for egress; all outbound connections are allowed.**

> **By default, a pod is non-isolated for ingress; all inbound connections are allowed.**

> **By default, if no policies exist in a namespace, then all ingress and egress traffic is allowed to and from pods in that namespace.**

> **Network policies do not conflict; they are additive.** If any policy or policies apply to a given pod for a given direction, **the connections allowed in that direction from that pod is the union of what the applicable policies allow. Thus, order of evaluation does not affect the policy result.**

> When a pod is isolated for egress, the only allowed connections from the pod are those allowed by the `egress` list of some NetworkPolicy that applies to the pod for egress.
>
> When a pod is isolated for ingress, the only allowed connections into the pod are those from **the pod's node** and those allowed by the `ingress` list of some NetworkPolicy that applies to the pod for ingress.

> **For a connection from a source pod to a destination pod to be allowed, both the egress policy on the source pod and the ingress policy on the destination pod need to allow the connection. If either side does not allow the connection, it will not happen.**

> **Your cluster must use a network plugin that supports NetworkPolicy enforcement.**

> **Creating a NetworkPolicy resource without a controller that implements it will have no effect.**

> `podSelector`: Each NetworkPolicy includes a `podSelector` which selects the grouping of pods to which the policy applies. **An empty `podSelector` selects all pods in the namespace.**

> **podSelector**: This selects particular Pods **in the same namespace** as the NetworkPolicy which should be allowed as ingress sources or egress destinations.
>
> **namespaceSelector**: This selects particular namespaces for which all Pods should be allowed as ingress sources or egress destinations.
>
> **namespaceSelector** *and* **podSelector**: A single `to`/`from` entry that specifies both `namespaceSelector` and `podSelector` selects particular Pods within particular namespaces.

**四条必须记住的规则**：

| # | 规则 | 官方原文 | 实践含义 |
|---|---|---|---|
| **① 默认全通** | *"By default, a pod is non-isolated for ingress; all inbound connections are allowed."* | **NetworkPolicy 是"显式开启"的**——不写策略 = 不隔离 | **新建集群默认是不安全的**，必须主动配 |
| **② 策略是"加法"的** | *"Network policies do not conflict; they are additive... the connections allowed ... is the **union** of what the applicable policies allow."* | **多策略取并集，不是取交集** | **写一条"允许"的策略不会抵消另一条"允许"的策略**；想收紧只能靠"所有相关策略都不允许" |
| **③ 双向都要放行** | *"both the egress policy on the source pod and the ingress policy on the destination pod need to allow the connection."* | **源 Pod 的 egress + 目标 Pod 的 ingress，缺一不可** | **排查"连不上"必须两边都查** |
| **④ 必须有插件支持** | *"Network policies are implemented by the network plugin... **Creating a NetworkPolicy resource without a controller that implements it will have no effect.**"* | **策略对象建了 ≠ 生效** | **Calico / Cilium 支持；Flannel 不支持** |

**"加法"语义的直接后果（很多人搞反）**：

```
Policy A：允许 X → Y
Policy B：允许 X → Z

结果：X 可以访问 Y **和** Z
```

> **"加法"意味着：NetworkPolicy 只能通过"给所有相关 Pod 都套上限制"来收紧，不能通过"加一条更严格的规则"来收紧。** 所以"默认拒绝"的实现方式是：**在每个命名空间里放一条"选中所有 Pod、什么都不允许"的策略**，再用更细的策略显式放行需要的东西。

**"pod 的节点"这条特殊放行（官方原文）** [S11]：

> When a pod is isolated for ingress, the only allowed connections into the pod are those **from the pod's node** and those allowed by the `ingress` list...

> **注意**：即使你写了"默认拒绝 ingress"，**同节点上的 Pod 仍然能访问它**（因为 kubelet 的探针、健康检查、镜像拉取等要走节点本地路径）。**这条是官方明确的行为，不是漏洞。**

**官方 API 参考对 NetworkPolicy 字段的补充说明** [S12]：

> `podSelector` selects the pods to which this NetworkPolicy object applies. ... **An empty selector matches all pods in the policy's namespace.** Multiple network policies can select the same set of pods. In this case, **the ingress rules for each are combined additively.** **This field is optional. If it is not specified, it defaults to an empty selector.**

> `policyTypes` is a list of rule types that the NetworkPolicy relates to. Valid options are ["Ingress"], ["Egress"], or ["Ingress", "Egress"]. **If this field is not specified, it will default based on the existence of ingress or egress rules**; policies that contain an egress section are assumed to affect egress, and all policies (whether or not they contain an ingress section) are assumed to affect ingress. **If you want to write an egress-only policy, you must explicitly specify `policyTypes [ "Egress" ]`.** Likewise, if you want to write a policy that specifies that no egress is allowed, you must specify a `policyTypes` value that include "Egress" (since such a policy would not include an egress section and would otherwise default to just [ "Ingress" ]).

> `ingress` is a list of ingress rules to be applied to the selected pods. Traffic is allowed to a pod if there are no NetworkPolicies selecting the pod (and cluster policy otherwise allows the traffic), OR if the traffic source is the pod's local node, OR if the traffic matches at least one ingress rule across all of the NetworkPolicy objects whose podSelector matches the pod. **If this field is empty then this NetworkPolicy does not allow any traffic (and serves solely to ensure that the pods it selects are isolated by default)**

> `egress` is a list of egress rules to be applied to the selected pods. ... **If this field is empty then this NetworkPolicy limits all outgoing traffic**

> `from` is a list of sources which should be able to access the pods selected for this rule. Items in this list are combined using a logical OR operation. **If this field is empty or missing, this rule matches all sources (traffic not restricted by source).** If this field is present and contains at least one item, this rule allows traffic only if the traffic matches at least one item in the from list.

> `ports` is a list of ports which should be made accessible on the pods selected for this rule. Each item in this list is combined using a logical OR. **If this field is empty or missing, this rule matches all ports (traffic not restricted by port).**

> `protocol` represents the protocol (TCP, UDP, or SCTP) which traffic must match. **If not specified, this field defaults to TCP.**

> `endPort` indicates that the range of ports from port to endPort if set, inclusive, should be allowed by the policy. **This field cannot be defined if the port field is not defined or if the port field is defined as a named (string) port.** The endPort must be equal or greater than port.

### 2.8 Pod Security Admission 与三层 securityContext

**官方原文（Pod Security Admission）** [S13]：

> The Kubernetes **Pod Security Standards** define different isolation levels for Pods. These standards let you define how you want to restrict the behavior of pods in a clear, consistent fashion.

> Kubernetes offers a built-in *Pod Security* **admission controller** to enforce the Pod Security Standards. **Pod security restrictions are applied at the namespace level when pods are created.**

> **Feature state: Stable since Kubernetes v1.25**

**三个等级（官方定义）** [S14]：

| Profile | 官方描述 |
|---|---|
| **Privileged** | *"Unrestricted policy, providing the widest possible level of permissions. **This policy allows for known privilege escalations.**"* |
| **Baseline** | *"Minimally restrictive policy which prevents known privilege escalations. **Allows the default (minimally specified) Pod configuration.**"* |
| **Restricted** | *"Heavily restricted policy, following current Pod hardening best practices."* |

> **官方强调这三个策略是"累进（cumulative）"的**：Restricted ⊃ Baseline ⊃ Privileged 的全部要求。

**三种模式与命名空间标签（官方原文）** [S13]：

| Mode | 官方描述 |
|---|---|
| **enforce** | *"**Policy violations will cause the pod to be rejected.**"* |
| **audit** | *"Policy violations will trigger the addition of an audit annotation to the event recorded in the audit log, but are otherwise allowed."* |
| **warn** | *"Policy violations will trigger a user-facing warning, but are otherwise allowed."* |

```yaml
# 官方给的标签格式
pod-security.kubernetes.io/<MODE>: <LEVEL>          # MODE = enforce|audit|warn
pod-security.kubernetes.io/<MODE>-version: <VERSION> # 可选，钉住小版本
```

> **VERSION 官方说明**：*"VERSION must be a valid Kubernetes minor version, or `latest`."* [S13]
> **生产建议**：**用 `-version` 钉住**（如 `v1.37`），不要用 `latest`——**`latest` 会随集群升级变化，导致某次升级后原本能跑的 Pod 突然被拒**。

**一个极易踩的坑（官方原文）** [S13]：

> Pods are often created indirectly, by creating a workload object such as a Deployment or Job. ... **To help catch violations early, the audit and warning modes are applied to the workload resources. However, enforce mode is not applied to workload resources, only to the resulting pod objects.**

> **即：`enforce` 只拦最终的 Pod，不拦 Deployment 对象本身。** 所以"apply Deployment 成功了，但 Pod 起不来"——**去 Events 里看 Pod 被拒绝的报错**。

**豁免机制（官方原文）** [S13]：

> You can define *exemptions* from pod security enforcement... Exemptions must be explicitly enumerated. Requests meeting exemption criteria are **ignored** by the Admission Controller (all `enforce`, `audit` and `warn` behaviors are skipped). Exemption dimensions include: **Usernames, RuntimeClassNames, Namespaces.**

> **Caution**: Most pods are created indirectly by a controller in response to a workload resource, meaning that **exempting an end user will only exempt them from enforcement when creating pods directly, but not when creating a workload resource. Controller service accounts (such as `system:serviceaccount:kube-system:replicaset-controller`) should generally not be exempted**, as doing so would implicitly exempt any user that can create the corresponding workload resource.

**三层 securityContext 的具体要求（官方 Pod Security Standards 中文原文）** [S14]：

| 层 | 控制项 | 限制字段 | 允许值 |
|---|---|---|---|
| **Baseline** | Host Namespaces（禁共享宿主命名空间） | `spec.hostNetwork` / `spec.hostPID` / `spec.hostIPC` | 未定义/nil 或 `false` |
| **Baseline** | Privileged Containers（禁特权容器） | `spec.containers[*].securityContext.privileged`（含 initContainers、ephemeralContainers） | 未定义/nil 或 `false` |
| **Baseline** | HostPath Volumes（禁 hostPath） | `spec.volumes[*].hostPath` | 未定义/nil（**即完全不允许**） |
| **Baseline** | Capabilities（限制 add） | `spec.containers[*].securityContext.capabilities.add` | 只允许 `AUDIT_WRITE`/`CHOWN`/`DAC_OVERRIDE`/`FOWNER`/`FSETID`/`KILL`/`MKNOD`/`NET_BIND_SERVICE`/`SETFCAP`/`SETGID`/`SETPCAP`/`SETUID`/`SYS_CHROOT` |
| **Baseline** | Seccomp（禁 Unconfined） | `spec.securityContext.seccompProfile.type` | `RuntimeDefault` / `Localhost` |
| **Baseline** | Host Ports | `spec.containers[*].ports[*].hostPort` | 未定义/nil 或 `0`（**内建 PSA 控制器不支持"已知列表"**） |
| **Restricted** | **Volume Types**（限定卷类型） | `spec.volumes[*]` | 每条目必须设置其一：`configMap` / `csi` / `downwardAPI` / `emptyDir` / `ephemeral` / `persistentVolumeClaim` / `projected` / `secret` |
| **Restricted** | **Privilege Escalation** | `spec.containers[*].securityContext.allowPrivilegeEscalation` | **`false`** |
| **Restricted** | **Running as Non-root** | `spec.securityContext.runAsNonRoot` / `spec.containers[*].securityContext.runAsNonRoot` | **`true`**（Pod 级设了 true 时，容器级可为 nil） |
| **Restricted** | **非 root 用户**（v1.23+） | `runAsUser` | **所有非零值** 或 `undefined/null`（**不允许 0**） |
| **Restricted** | **Seccomp**（必须显式设） | `spec.securityContext.seccompProfile.type` | **`RuntimeDefault`** / `Localhost`（**禁止 Unconfined**） |
| **Restricted** | **Capabilities**（v1.22+） | `capabilities.drop` / `capabilities.add` | `drop` 必须含 **`ALL`**；`add` 只允许 **`NET_BIND_SERVICE`** 或 nil |

> **Restricted 里最容易漏的三条**：
> 1. **`runAsNonRoot: true` 但 `runAsUser: 0`** → **违规**（Restricted 的"非 root 用户"项明确说 `runAsUser` 不能是 0）。
> 2. **只 `drop: [ALL]` 但没 `add: [NET_BIND_SERVICE]`** → 应用监听 80 端口会失败（低端口需要该 capability）。
> 3. **用了 `hostPath` 卷** → **Restricted 直接不允许**（Baseline 就不允许，Restricted 只允许白名单里的 8 种卷类型）。
>
> **满足 Restricted 的最小 yaml**：
> ```yaml
> spec:
>   securityContext:
>     runAsNonRoot: true
>     seccompProfile:
>       type: RuntimeDefault
>   containers:
>   - name: app
>     securityContext:
>       allowPrivilegeEscalation: false
>       capabilities:
>         drop: ["ALL"]
>         # 需要监听 80 端口时再加：add: ["NET_BIND_SERVICE"]
> ```
> **注意：`securityContext` 可以放在 Pod 级（`spec.securityContext`）或容器级（`spec.containers[].securityContext`）。Pod 级是"继承给所有容器"，容器级是"覆盖"。** 官方 PSA 的判定逻辑是"Pod 级满足时，容器级可为 nil"。

### 2.9 Secret 的真相：etcd 里是 base64 明文

**官方原文（Secret，中文）** [S15]：

> `data` 字段中所有键值都必须是 **base64 编码**的字符串。如果不希望执行这种 base64 字符串的转换操作，你可以选择设置 `stringData` 字段，其中可以使用任何字符串作为其取值。

> **默认情况下，Kubernetes Secret 未加密地存储在 API 服务器的底层数据存储（etcd）中。任何拥有 API 访问权限的人都可以检索或修改 Secret，任何有权访问 etcd 的人也可以。此外，任何有权限在命名空间中创建 Pod 的人都可以使用该访问权限读取该命名空间中的任何 Secret；这包括间接访问，例如创建 Deployment 的能力。**

> （关于 TLS Secret）值为 base64 编码，这样会掩盖它们，但**不会提供任何有用的机密性级别**。

> （关于 Docker 配置 Secret）`auths` 值是 base64 编码的，其内容被屏蔽但未被加密。**任何能够读取该 Secret 的人都可以了解镜像库的访问令牌。**

> **为了安全地使用 Secret，请至少执行以下步骤：**
> 1. 为 Secret **启用静态加密**。
> 2. **以最小特权访问 Secret** 并启用或配置 RBAC 规则。
> 3. **限制 Secret 对特定容器的访问**。
> 4. 考虑使用**外部 Secret 存储驱动**。

> **三个必须分清的概念**：

| 概念 | 是什么 | 保护强度 |
|---|---|---|
| **base64 编码** | **编码**，不是加密。任何人拿到字符串都能 `base64 -d` 还原 | **零** |
| **静态加密（encryption at rest）** | etcd 里存的是**密文**，只有 apiserver 能解密 | **高**（前提是密钥管好了） |
| **传输加密（TLS）** | 网络上传输的是密文 | **中**（防中间人） |

> **本篇必须记住的一句话**：**"Secret 只是 base64，不是加密"** —— **任何人只要能 `kubectl get secret` 就能看到明文**。
> **etcd 层面的加密默认是关闭的**（见 2.10）。

### 2.10 Secret 静态加密：EncryptionConfiguration + KMS

**官方原文（Encrypting Confidential Data at Rest）** [S16]：

> **By default, the API server stores plain-text representations of resources into etcd, with no at-rest encryption.**

> The `identity` provider is the default if you do not specify otherwise. **The `identity` provider does not encrypt stored data and provides *no* additional confidentiality protection.**

> If you are running the kube-apiserver **without** the `--encryption-provider-config` command line argument, you do not have encryption at rest enabled. If you are running the kube-apiserver **with** the `--encryption-provider-config` command line argument, and the file that it references specifies the `identity` provider as the first encryption provider in the list, then you do not have at-rest encryption enabled.

**四种 provider 的官方对比表**：

| Provider | 算法 | 强度 | 速度 | 密钥长度 | 备注 |
|---|---|---|---|---|---|
| `identity` | **不加密** | **无** | — | — | **默认**，不提供任何机密性保护 |
| `aescbc` | AES-CBC + PKCS#7 | **弱** | 快 | 16/24/32 字节 | 官方明确：*"Not recommended due to CBC's vulnerability to padding oracle attacks. Key material accessible from control plane host."* |
| `secretbox` | XSalsa20 + Poly1305 | **强** | 更快 | 32 字节 | *"Uses relatively new encryption technologies that may not be considered acceptable in environments that require high levels of review. Key material accessible from control plane host."* |
| **`kms` v2** | **信封加密** | **最强** | 快 | 32 字节 | *"Uses envelope encryption scheme with DEK per API server... A good choice if using a third party tool for key management. **Available as stable from Kubernetes v1.29.**"* |

**KMS v2 的信封加密流程（官方原文）** [S16]：

> The KMS provider uses *envelope encryption*: Kubernetes encrypts resources using a data key, and then encrypts that data key using the managed encryption service. **Kubernetes generates a unique data key for each resource.** The API server stores an encrypted version of the data key in etcd alongside the ciphertext; when reading the resource, the apiserver calls the managed encryption service and provides both the ciphertext and the (encrypted) data key. Within the managed encryption service, the provider use a *key encryption key* to decipher the data key, deciphers the data key, and finally recovers the plain text. **Communication between the control plane and the KMS requires in-transit protection, such as TLS.**

**配置文件结构（官方原文）** [S16]：

```yaml
---
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
      - configmaps
      - pandas.awesome.bears.example
    providers:
      - aescbc:
          keys:
            - name: key1
              # See the following text for more details about the secret value
              secret: <BASE 64 ENCODED SECRET>
      - identity: {} # this fallback allows reading unencrypted secrets;
                     # for example, during initial migration
```

**四个关键规则（官方原文）** [S16]：

> Each `resources` array item is a separate config and contains a complete configuration. ... Each provider supports multiple keys - **the keys are tried in order for decryption, and if the provider is the first provider, the first key is used for encryption.**

> Only one provider type may be specified per entry (`identity` or `aescbc` may be provided, but not both in the same item). **The first provider in the list is used to encrypt resources written into the storage.** When reading resources from storage, each provider that matches the stored data attempts in order to decrypt the data. If no provider can read the stored data due to a mismatch in format or secret key, an error is returned which prevents clients from accessing that resource.

> **`EncryptionConfiguration` supports the use of wildcards** to specify the resources that should be encrypted. Use `'*.<group>'` to encrypt all resources within a group ... or `'*.*'` to encrypt all resources. **Use of wildcards that overlap within the same resource list or across multiple entries are not allowed** since part of the configuration would be ineffective.

> Resources written as-is without encryption. When set as the first provider, **the resource will be decrypted as new values are written. Existing encrypted resources are *not* automatically overwritten with the plaintext data.**

**开启加密的三个步骤（官方原文归纳）** [S16]：

1. **生成密钥**（`openssl rand -base64 32`）
2. **写 `EncryptionConfiguration` 文件**，挂载给 kube-apiserver
3. **3. Restart your API server.**（官方原文：*"You will need to mount the new encryption config file to the `kube-apiserver` static pod."*）

**三个必须知道的运维要点**：

| 要点 | 说明 |
|---|---|
| **重启 apiserver 才会生效** | 官方原文：*"3. Restart your API server."* [S16] |
| **存量 Secret 不会自动变成密文** | 官方原文：*"Existing encrypted resources are **not** automatically overwritten with the plaintext data."* → 需要**重新写入**所有 Secret（官方有 "Ensure all secrets are encrypted" 章节） |
| **必须保留 `identity` 作为兜底**（迁移期） | 官方注释：*"this fallback allows reading unencrypted secrets; for example, during initial migration"* → **迁移完成后可以移除**，但**移除前必须确保所有 Secret 都已重写** |
| **KMS 需要保护传输通道** | 官方原文：*"Communication between the control plane and the KMS requires in-transit protection, such as TLS."* [S16] |

> **生产选型建议**：
> - **自建集群**：用 `secretbox` 或 `aescbc`（密钥自己管好），成本最低。
> - **云上托管集群（ACK/EKS 等）**：**优先用云厂商的 KMS**（ACK 集成 KMS/凭据管家），用 `kms` v2 provider——**密钥不落盘到控制面主机**，且支持 KEK 轮换。
> - **注意**：`aescbc` 官方明确标注"不推荐"（padding oracle 风险），**新集群直接选 `secretbox` 或 `kms`**。

### 2.11 镜像供应链安全：签名验证 + 扫描 + SBOM

**这一节讲的是"**镜像进入集群之前**"的安全**——这是等保和审计的高频检查项。

**三层防线**：

```
第 1 层：来源可信    →  镜像签名 + 准入时验证签名（只允许可信来源）
第 2 层：内容无漏洞  →  Trivy 扫描镜像（ CVE 扫描）
第 3 层：内容可追溯  →  SBOM（知道镜像里到底有什么）
```

**① 签名验证（准入阶段拦截）**

| 环节 | 做法 |
|---|---|
| **CI 侧签名** | 用 cosign / Notary 对镜像摘要（digest）签名，私钥放在 CI 的密钥管理服务里 |
| **Registry 侧存储** | 签名与镜像一起存（OCI referrers 或 Registry 的 tag） |
| **K8s 侧验证** | 部署 **ValidatingWebhook**（如 sigstore 的 policy-controller / kyverno），在 Pod 创建时**校验镜像签名**，验不过就拒绝 |
| **必须用 digest** | 校验必须基于 `image@sha256:...`，**不能基于 tag**（tag 可变，digest 不可变） |

> **本篇的诚实说明**：**签名工具链的具体 CLI 与 K8s 集成方式，本库索引未收录对应的官方文档**（见 7 节待验证清单）。本节只写**机制层面的通用流程**，**具体命令以工具官方文档为准**。

**② 漏洞扫描（Trivy）**

| 能力 | 说明 |
|---|---|
| **镜像扫描** | 扫镜像里的操作系统包与应用依赖，找出已知 CVE |
| **配置扫描** | 扫 K8s yaml / Dockerfile，找不安全配置（如特权容器、root 用户） |
| **SBOM 生成** | 生成软件物料清单（SPDX / CycloneDX 格式） |
| **门禁接入** | 官方文档给出了 `trivy` 的**退出码语义**——**发现高危漏洞时退出码非 0**，可以直接用作 CI 门禁 |

> **Trivy 官方文档已在本库索引中收录** [S17]：*"镜像/配置扫描、SBOM"*。**具体漏洞库条数与更新频率随时间变化，本篇不编数字。**

**③ SBOM（软件物料清单）**

| 为什么需要 | 说明 |
|---|---|
| **出事时能立刻回答"我受影响吗"** | 某个 CVE 爆出来时，扫一遍 SBOM 就知道哪些镜像里有这个包 |
| **审计要求** | 等保/行业审计普遍要求"能列出每个交付物的成分" |
| **许可证合规** | SBOM 里也能看到每个组件的许可证 |

> **本库中 L3《镜像仓库与供应链安全》已经详细写过 Trivy / SBOM / cosign 的机制与门禁接入**（见 [`../01-基础篇/L3-镜像仓库与供应链安全.md`](../01-基础篇/L3-镜像仓库与供应链安全.md)）。**本篇只讲"K8s 侧怎么把它接进准入链"这一层，不重复展开。**

**④ 在 K8s 侧的三个落点**：

| 落点 | 用什么 | 拦截什么 |
|---|---|---|
| **准入验证签名** | ValidatingWebhook（sigstore policy-controller / kyverno） | 未签名 / 签名不可信的镜像 |
| **准入校验白名单** | ValidatingWebhook / PSA | 不在白名单仓库的镜像 |
| **CI 门禁** | Trivy 退出码 | 高危 CVE 直接让流水线失败，**镜像根本不会被推上去** |

> **顺序很重要**：**CI 门禁（Trivy）应该在前**——**漏洞在 CI 阶段就拦住，成本最低**；**签名验证在准入阶段，是最后一道防线**（防止有人绕过 CI 直接推镜像）。

### 2.12 关键参数与默认值

| 参数 | 默认 | 含义 | 出处 |
|---|---|---|---|
| ServiceAccount Token 挂载路径 | `/var/run/secrets/kubernetes.io/serviceaccount/` | 容器内 Token 与 CA 证书的位置 | 官方 [S4] 描述的投射卷机制 |
| Token 形态（v1.22+） | **短期、自动轮转、投射卷** | TokenRequest API 签发，与 Pod 生命周期绑定 | [S4] |
| `automountServiceAccountToken` | **自动挂载**（不写即挂） | 设为 `false` 阻止自动注入凭据 | [S4] |
| `default` ServiceAccount | **每个命名空间自动创建一个** | 删了会被控制面重建 | [S4] |
| API Token 校验步骤 | **5 步**：签名 / 过期 / 对象引用有效 / 当前有效 / audience | apiserver 的 bearer token 校验 | [S4] |
| PSA 特性状态 | **Stable since v1.25** | 内置准入控制器 | [S13] |
| PSA 模式 | `enforce` / `audit` / `warn` | 违规时拒绝 / 记审计 / 只告警 | [S13] |
| PSA 标签 | `pod-security.kubernetes.io/<MODE>: <LEVEL>` + `-version` | 命名空间级强制 | [S13] |
| PSA 等级 | `privileged` / `baseline` / `restricted`（**累进**） | 三档隔离级别 | [S13] [S14] |
| PSA `enforce` 的作用域 | **只作用于最终的 Pod 对象**，不作用于 workload 对象 | 解释"apply 成功但 Pod 起不来" | [S13] |
| `roleRef` 可变性 | **创建后不可改** | 改必须删了重建 | [S8] |
| `resourceNames` 空集 | **等于全部允许** | 不写 = 不限制 | [S9] |
| `nonResourceURLs` 适用范围 | **仅 ClusterRole + ClusterRoleBinding** | 因为非资源 URL 不分命名空间 | [S9] |
| NetworkPolicy 默认状态 | **全部放行**（ingress + egress 都 non-isolated） | 不写策略 = 不隔离 | [S11] |
| NetworkPolicy 组合语义 | **加法（并集）** | 多策略取并集，顺序不影响结果 | [S11] |
| NetworkPolicy 跨 Pod 放行条件 | **源 egress + 目标 ingress 都要允许** | 单边放行无效 | [S11] |
| NetworkPolicy `protocol` 默认 | **TCP** | 不写协议即 TCP | [S12] |
| NetworkPolicy `policyTypes` 默认 | **按有无 ingress/egress 规则推断** | 纯 egress 策略必须**显式**写 `["Egress"]` | [S12] |
| 静态加密默认 provider | **`identity`（不加密）** | 不配 `--encryption-provider-config` = 无静态加密 | [S16] |
| `kms` v2 provider 状态 | **Stable since v1.29** | 信封加密 | [S16] |
| `secretbox` / `aescbc` 密钥长度 | 32 字节 / 16·24·32 字节 | 自管密钥方案 | [S16] |
| `EncryptionConfiguration` API 版本 | `apiserver.config.k8s.io/v1` | 配置文件版本 | [S16] |

---

## 3. 动手

> **前置提醒**：本节所有命令都是**只读**或**在你自己 namespace 里创建对象**，不会影响集群其他用户。**本篇不代为执行。**

### 3.1 第一步：看看自己的权限有多大

```bash
# ① 我是谁？
kubectl auth whoami

# ② 我能做什么？（全表）
kubectl auth can-i --list

# ③ 定向问几个高危动作
kubectl auth can-i list secrets -n default
kubectl auth can-i get pods/exec -n default
kubectl auth can-i create pods -n default
kubectl auth can-i impersonate users
kubectl auth can-i bind clusterroles
kubectl auth can-i escalate clusterroles
```

**看到什么算对（判读表）**：

| 输出 | 含义 | 该做什么 |
|---|---|---|
| `yes` | 你有这个权限 | 对高危动词（`impersonate`/`bind`/`escalate`）**必须是 `no`** |
| `no` | 你没有这个权限 | 正常 |
| `No RBAC policy matched` | **你一条规则都没匹配上** = 默认拒绝 | 正常（说明集群收敛得好） |
| `--list` 里出现 `*` | **有通配符权限** | **立刻收敛**（官方明确反对 [S3]） |

**自查集群里最危险的三处**：

```bash
# ① default SA 绑了什么？
kubectl get clusterrolebinding -o jsonpath='{range .items[*]}{.metadata.name}{" => "}{.subjects[*].name}{" => "}{.roleRef.name}{"\n"}{end}' \
  | grep 'system:serviceaccount:default:default'

# ② 有没有 cluster-admin 被绑给了不该绑的主体？
kubectl get clusterrolebinding cluster-admin -o yaml

# ③ system:masters 组里有谁？
kubectl get clusterrolebinding -o jsonpath='{range .items[*]}{.subjects[*].name}{"\n"}{end}' | grep system:masters
```

> **第 ③ 条特别重要**：官方明确 `system:masters` 组成员**绕过所有 RBAC 检查**，**而且删 Binding 也撤不掉权限** [S3]。

### 3.2 第二步：看默认 ServiceAccount 的暴露面

```bash
# ① 有没有 Pod 在用 default SA？
kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"/"}{.metadata.name}{"  sa="}{.spec.serviceAccountName}{"  automount="}{.spec.automountServiceAccountToken}{"\n"}{end}'

# ② default SA 现在绑了什么？
kubectl get rolebinding,clusterrolebinding -n default -o yaml

# ③ 自动挂载了 Token 的 Pod 数
kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"\n"}{end}' | sort | uniq -c
```

**看到什么算对**：

| 检查 | 期望 |
|---|---|
| `spec.serviceAccountName` | 业务 Pod 应显示**专属名字**，而不是 `default` |
| `automountServiceAccountToken` | 不需要访问 API 的 Pod 应显式为 `false` |
| default SA 的绑定 | **不应该有 ClusterRoleBinding 指向它** |

### 3.3 第三步：NetworkPolicy 三件套

在**你自己的测试命名空间**里做（**别在生产命名空间直接试**）：

```bash
kubectl create namespace netpol-demo
```

**① 默认拒绝（入向 + 出向全封）**：

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny-all
  namespace: netpol-demo
spec:
  podSelector: {}          # 空选择器 = 选中本命名空间所有 Pod
  policyTypes:
  - Ingress
  - Egress
  # 不写 ingress/egress = 不允许任何流量
```

**② 允许同 namespace 互访**：

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-same-namespace
  namespace: netpol-demo
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - podSelector: {}      # 同命名空间的 Pod（peer 的 podSelector 只在同 ns 生效）
  egress:
  - to:
    - podSelector: {}
```

**③ 允许 DNS（K8s 里最容易被忘掉的一条）**：

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns
  namespace: netpol-demo
spec:
  podSelector: {}
  policyTypes:
  - Egress
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
    ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
```

> **为什么必须单独放行 DNS**：**CoreDNS 是 `kube-system` 命名空间里的 Deployment**。默认拒绝 egress 之后，**所有 Pod 都解析不了域名** → Service 访问全挂 → **现象是"Pod 都 Running，但访问 Service 超时"**。这是网络策略最经典的一个坑。
>
> **官方关于 `namespaceSelector` 的原文** [S11]：*"namespaceSelector: This selects particular namespaces for which **all Pods** should be allowed as ingress sources or egress destinations."* —— **跨命名空间必须用 `namespaceSelector`，且要靠命名空间的 `kubernetes.io/metadata.name` 标签**（这是 K8s 自动给每个命名空间打的标签）。
>
> **DNS 放行的端口**：UDP 53 和 TCP 53 **都要放**——UDP 53 是常规查询，**TCP 53 是响应包超过 UDP 长度时的回退通道**（DNS over TCP）。

**验证**：

```bash
# 看策略是否被 CNI 接管（不同 CNI 命令不同）
kubectl get networkpolicy -n netpol-demo
kubectl describe networkpolicy default-deny-all -n netpol-demo

# 实测：在 ns 里跑一个 Pod，试着解析和访问
kubectl run test --image=busybox:1.36 -n netpol-demo --restart=Never -- sleep 3600
```

**看到什么算对**：

| 现象 | 期望 |
|---|---|
| 默认拒绝策略下，Pod 内 `nslookup kubernetes.default` | **失败**（DNS 被封） |
| 加上 allow-dns 后再试 | **成功** |
| 同 namespace 两个 Pod 互访 | 默认拒绝时**失败**，加上 allow-same-namespace 后**成功** |
| `kubectl get networkpolicy` | 三条策略都在 |

> **重要前提**（官方原文）[S11]：*"Your cluster must use a network plugin that supports NetworkPolicy enforcement... **Creating a NetworkPolicy resource without a controller that implements it will have no effect.**"*
> **kind 默认的 CNI 是 kindnet，官方没有声明它支持 NetworkPolicy**——**在 kind 上做这个实验可能看到"策略建了但流量照样通"**。**这不是策略写错了，是插件不支持。** 生产上用 Calico / Cilium。

### 3.4 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| 权限自查 | `kubectl auth can-i --list` | 无意外的 `*` |
| 高危动词 | `kubectl auth can-i impersonate users` | `no` |
| default SA 无绑定 | 见 3.1 第 ① 条 | 无输出 |
| Pod 不用 default SA | 见 3.2 第 ① 条 | 全部是专属 SA |
| 不需 API 的不挂 Token | 同上，`automount=false` | 已显式关闭 |
| 命名空间 PSA | `kubectl get ns <ns> --show-labels` | 有 `pod-security.kubernetes.io/enforce: restricted` |
| NetworkPolicy 生效 | 3.3 的连通性实验 | 默认拒绝时不通 |
| Secret 静态加密 | `kubectl get --raw /healthz`（或问管理员） | apiserver 启动参数含 `--encryption-provider-config` |

---

## 4. 资源清单逐字段

### 4.1 Role / ClusterRole / Binding

**Role / ClusterRole 的 `rules[]`（PolicyRule）**：

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiGroups` | []string | ✅ | 资源所在的 API 组；`""` = **核心组**（Pod/Service/ConfigMap/Secret），`apps` = Deployment 等，`*` = 全部 | 写 `"apps"` 却想管 Pod → **授权无效**（不报错） |
| `resources` | []string | ✅ | 资源名；支持子资源 `pods/log`、`pods/exec`、`deployments/scale`；`*` = 全部 | 写 `"pod"`（少个 s）→ **授权无效**（不报错） |
| `verbs` | []string | ✅ | 动作：`get`/`list`/`watch`/`create`/`update`/`patch`/`delete`/`deletecollection`；`*` = 全部 | 漏了 `watch` → **kubectl 持续 watch 会 403** |
| `resourceNames` | []string | | 对象名白名单；**空集 = 全部允许** | 限制 `list`/`watch` 时客户端**必须带 `metadata.name` field selector**，否则 403 |
| `nonResourceURLs` | []string | | 非资源 URL（如 `/api`、`/healthz`） | **只能配在 ClusterRole + ClusterRoleBinding**（非资源 URL 不分命名空间）；**与 `apiGroups`/`resources` 互斥**（"not both"） |

**Binding 的字段**：

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `RoleBinding.metadata.namespace` | string | ✅ | **权限生效的范围** | 写错 ns → 权限授到了别的命名空间 |
| `RoleBinding.roleRef.kind` | enum | ✅ | `Role` 或 `ClusterRole` | 写 `ClusterRoleBinding` → API 拒绝 |
| `RoleBinding.roleRef.name` | string | ✅ | 被引用的角色名 | 写错 → 授权**静默无效**（不报错） |
| `RoleBinding.roleRef.apiGroup` | string | ✅ | `rbac.authorization.k8s.io` | 写错 → API 拒绝 |
| `RoleBinding.roleRef`（可变性） | — | | **创建后不可改** | 试图修改 → **validation error**；必须删了重建 |
| `RoleBinding.subjects[].kind` | enum | ✅ | `User` / `Group` / `ServiceAccount` | 写错 → API 拒绝 |
| `RoleBinding.subjects[].name` | string | ✅ | 主体名（**大小写敏感**） | 名字大小写错 → **静默不生效**（官方原文：*"case sensitive"* [S8]） |
| `RoleBinding.subjects[].namespace` | string | | **仅 `ServiceAccount` 需要**（SA 是命名空间级的） | 漏写 → 找不到 SA，**静默不生效** |
| `RoleBinding.subjects[].apiGroup` | string | | `ServiceAccount` 主体填 `""` | 填 `rbac.authorization.k8s.io` → API 拒绝 |
| `ClusterRoleBinding.metadata`（无 namespace） | — | | **集群级**，不能有 namespace | 写了 namespace → API 拒绝 |
| `ClusterRole.aggregationRule.clusterRoleSelectors` | []LabelSelector | | 标签选择器，**匹配到的 ClusterRole 的权限会被加进来** | 选择器太宽 → 权限被意外放大 |
| `ClusterRole.aggregationRule`（一旦设置） | — | | **`rules` 由控制器管理，手工改会被覆盖** | 手改 rules → **改完就被冲掉**（官方原文：*"direct changes to Rules will be stomped by the controller"* [S7]） |

**ServiceAccount 的字段**：

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: v1` | string | ✅ | 核心组 | — |
| `kind: ServiceAccount` | string | ✅ | 对象类型 | — |
| `metadata.name` | string | ✅ | SA 名（**`system:serviceaccount:<ns>:<name>`**） | 与 Pod 的 `serviceAccountName` 不匹配 → **Pod 创建失败** |
| `metadata.namespace` | string | ✅ | 所属命名空间 | 写错 → SA 找不到 |
| `automountServiceAccountToken` | bool | | 该 SA 是否自动挂 Token | **不写 = 自动挂载**；设为 false → 容器内没有 Token |
| `imagePullSecrets` | []ObjectReference | | 该 SA 关联的镜像仓库凭据 | 漏了 → **私有仓库拉镜像 401** |
| `secrets` | []ObjectReference | | 关联的 Secret（v1.22+ 后**基本不再自动创建**） | — |

### 4.2 NetworkPolicy

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: networking.k8s.io/v1` | string | ✅ | API 组与版本 | `extensions/v1beta1` → 版本废弃 |
| `kind: NetworkPolicy` | string | ✅ | 对象类型 | — |
| `metadata.namespace` | string | ✅ | 策略所在命名空间 | 放错 ns → **管不到目标 Pod** |
| `spec.podSelector` | LabelSelector | | **作用对象**；**空 `{}` = 本 ns 全部 Pod**；不写 = 默认为空选择器 | 写具体标签 → 只管带该标签的 Pod；**标签写错 → 策略"建了但没作用"** |
| `spec.policyTypes` | []string | | `Ingress` / `Egress` / 两者 | **不写按有无规则推断**；纯 egress 策略**必须显式写 `["Egress"]`** |
| `spec.ingress[]` | []NetworkPolicyIngressRule | | 入向规则 | **留空 = 不允许任何入向**（这是"默认拒绝"的写法） |
| `spec.ingress[].from[]` | []NetworkPolicyPeer | | 来源；**留空/缺失 = 匹配所有来源**（不限制来源） | 以为留空是"禁止"→ **实际是"全部允许"**（方向反了） |
| `spec.ingress[].ports[]` | []NetworkPolicyPort | | 端口；**留空 = 匹配所有端口** | 同上 |
| `spec.egress[]` | []NetworkPolicyEgressRule | | 出向规则 | **留空 = 限制所有出向** |
| `spec.egress[].to[]` | []NetworkPolicyPeer | | 目的地；**留空 = 匹配所有目的地** | 同上 |
| `from[].podSelector` | LabelSelector | | 同命名空间的 Pod | peer 的 `podSelector` **只在本 ns 生效** |
| `from[].namespaceSelector` | LabelSelector | | 命名空间（用 `kubernetes.io/metadata.name` 标签匹配） | 漏写 → **跨 ns 放行失效** |
| `from[].ipBlock.cidr` | string | | IP 段 | 写错网段 → 不生效 |
| `ports[].protocol` | enum | | `TCP` / `UDP` / `SCTP`；**默认 TCP** | 不写 → 只放行 TCP |
| `ports[].port` | int/str | | 端口号或命名端口 | 写错 → 不生效 |
| `ports[].endPort` | int | | 端口范围结束（含） | **port 未定义或为命名端口时不能设 endPort**；`endPort < port` → 拒绝 |

### 4.3 securityContext

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `spec.securityContext.runAsNonRoot` | bool | | **Pod 级**：必须以非 root 运行 | 设 true 但 `runAsUser: 0` → **Restricted 判违规** |
| `spec.securityContext.runAsUser` | int | | 运行的 UID | 设 0 → **Restricted 判违规** |
| `spec.securityContext.fsGroup` | int | | 卷的组属主 | — |
| `spec.securityContext.seccompProfile.type` | enum | | `RuntimeDefault` / `Localhost` / `Unconfined` | 设 `Unconfined` → **Baseline/Restricted 都违规** |
| `spec.securityContext.sysctls[].name` | string | | 内核参数 | 不在"安全子集"里 → **违规** |
| `spec.containers[].securityContext.privileged` | bool | | 特权容器 | 设 true → **Baseline/Restricted 都违规** |
| `spec.containers[].securityContext.allowPrivilegeEscalation` | bool | | 允许提权 | 设 true → **Restricted 违规**（必须 false） |
| `spec.containers[].securityContext.capabilities.drop` | []string | | 丢弃的 Linux capability | 不含 `ALL` → **Restricted 违规** |
| `spec.containers[].securityContext.capabilities.add` | []string | | 添加的 capability | 加了 `NET_BIND_SERVICE` 以外 → **Restricted 违规** |
| `spec.containers[].securityContext.readOnlyRootFilesystem` | bool | | 根文件系统只读 | 设 true → 应用写 `/tmp` 失败（**要配 emptyDir**） |
| `spec.containers[].securityContext.runAsNonRoot` | bool | | 容器级非 root | Pod 级已设 true 时可为 nil |
| `spec.volumes[*].hostPath` | — | | 宿主路径挂载 | **Baseline 直接禁止**（`hostPath` 必须是 nil） |
| `spec.hostNetwork`、`spec.hostPID`、`spec.hostIPC` | bool | | 共享宿主命名空间 | 设 true → **Baseline/Restricted 违规** |
| `spec.containers[].ports[*].hostPort` | int | | 宿主端口 | 非 0 且不在白名单 → **Baseline 违规** |

---

## 5. 横向对比

### 5.1 RBAC vs Webhook 鉴权（准入）

**这两个东西经常被叫"鉴权"，但它们在请求链的**不同位置**，解决的问题也不同**。

| 维度 | **RBAC** | **Webhook 鉴权** |
|---|---|---|
| **在链条的哪一步** | **② 授权** | **③ 准入** |
| **回答的问题** | "**这个主体**能不能对**这类对象**做**这个动作**" | "**这个对象**的内容**合不合规**" |
| **判定依据** | `PolicyRule`（apiGroups + resources + verbs + resourceNames） | **你自己写的代码**（任意逻辑） |
| **粒度** | **主体 × 资源 × 动作** | **任意**（可以看 spec 内容、可以查外部系统） |
| **能不能看对象内容** | **不能**（授权时只看请求的元信息，不看 spec） | **能**（MutatingWebhook 甚至能改） |
| **内置还是扩展** | **内置**（默认授权器） | **扩展**（装 webhook 才生效） |
| **典型用途** | "这个 ServiceAccount 只能读本 ns 的 ConfigMap" | "这个镜像必须来自白名单仓库" / "这个 Pod 必须有资源限制" |
| **拒绝时的报错** | `User "xxx" cannot xxx resource "yyy"` | `admission webhook "xxx" denied the request: <你的自定义消息>` |
| **能否被绕过** | `system:masters` 组**能绕过**（官方明确 [S3]） | 取决于你的实现 |

> **一句话区分**：**RBAC 管"谁"，Webhook 管"什么"。**
>
> **典型误用**：想实现"只允许用特定镜像"，却去配 RBAC。**RBAC 做不到**——它不看 `spec.containers[].image`。**这必须用 ValidatingWebhook（准入）**。

**两者配合的典型生产组合**：

| 层 | 谁来做 |
|---|---|
| **谁能读哪个命名空间的 Secret** | **RBAC**（Role + RoleBinding，namespace 级） |
| **只能用白名单镜像** | **ValidatingWebhook**（准入） |
| **Pod 必须有 requests/limits** | **LimitRange**（准入，内置） |
| **命名空间总资源不能超** | **ResourceQuota**（准入，内置） |
| **Pod 必须符合安全基线** | **Pod Security Admission**（准入，内置） |
| **Secret 落盘必须加密** | **EncryptionConfiguration**（apiserver 侧，不在链条上） |

### 5.2 NetworkPolicy vs 云安全组

| 维度 | **NetworkPolicy** | **云安全组 / 网络 ACL** |
|---|---|---|
| **在哪一层生效** | **集群内**（Pod 网络平面） | **云网络层**（VPC / 子网 / 网卡） |
| **作用对象** | **Pod（按标签选择）** | **网卡 / 实例 / 子网**（按 IP / 网段） |
| **谁能改** | **集群管理员 / 命名空间管理员** | **云账号管理员**（一般开发改不了） |
| **粒度** | **按标签**，Pod 重建后自动继承 | **按 IP**，Pod 换 IP 后规则失效 |
| **能否跨集群用** | ❌ 只在本集群 | ✅ 跨集群、跨 VPC 都能用 |
| **能否限制出向到外部** | ✅（`egress`） | ✅ |
| **能否限制"访问某个外部域名"** | ❌ **只能按 IP/端口**（DNS 解析后的 IP） | ❌ 同样只能按 IP |
| **默认状态** | **默认全放行**（要显式开启） | **云厂商各有默认（通常 deny all + 放行内网）** |
| **生效前提** | **CNI 插件必须支持** | 云网络就绪 |
| **审计归属** | K8s 管理员 | 云安全团队 |

**两者的正确关系（生产实操）**：

```
云安全组（外层）：控制"这个集群/节点池能和外部哪些网段通信"
      │  通常是粗粒度的：允许 VPC 内互通 + 允许访问必要的外部服务
      ▼
NetworkPolicy（内层）：控制"集群内哪个 Pod 能和哪个 Pod 通信"
      │  细粒度：按标签、按命名空间、按端口
      ▼
应用层：应用自己的鉴权（用户/租户隔离）
```

> **必须讲清的边界**：**NetworkPolicy 解决不了"访问某个外部域名"的问题**——它只看 IP 和端口。**如果一个外部服务的 IP 段很大或会变，NetworkPolicy 很难精确控制**，这种场景要靠云安全组或出口代理。
>
> **另一个必须知道的限制**：**NetworkPolicy 的"默认拒绝"只管"Pod 之间"和"Pod 到外部 IP"**，**DNS 解析本身走的是 kube-dns 的 Service IP**（ClusterIP），**所以"拒绝所有 egress"会连 DNS 一起封掉**（见 3.3）。

### 5.3 三道闸门速查

| 你要实现的需求 | 用哪个闸门 | 用什么对象 |
|---|---|---|
| 某个 SA 只能读本 ns 的 ConfigMap | **② 授权** | Role + RoleBinding |
| 某个 CI 账号只能建 Deployment，不能删 | **② 授权** | Role（`verbs: [create, get, list, watch]`） |
| 禁止使用特权容器 | **③ 准入** | PSA `baseline`+ |
| 禁止用 hostPath 卷 | **③ 准入** | PSA `baseline`+ |
| 限制命名空间总内存 | **③ 准入** | ResourceQuota |
| 给没写 requests 的 Pod 补默认值 | **③ 准入** | LimitRange |
| 只允许从白名单仓库拉镜像 | **③ 准入** | ValidatingWebhook |
| 强制打标签 | **③ 准入** | MutatingWebhook |
| Pod 之间按标签隔离 | **集群网络层** | NetworkPolicy |
| Secret 在 etcd 里存密文 | **apiserver 配置** | EncryptionConfiguration |
| 节点/云网络的边界 | **云网络层** | 云安全组 |

---

## 6. 踩坑与排障（症状 → 状态 → 真实原因）

**排障铁律：先分清 403 的来源（授权 vs 准入），再动手。**

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `apply` 报 `Forbidden: <资源> is forbidden: User "xxx" cannot <动词> resource "yyy" in API group "zzz"` | 对象没创建 | **② 授权层拒绝**：RBAC 没给这个主体这个权限 | `kubectl auth can-i <动词> <资源> -n <ns>` 确认；找管理员加 RoleBinding | 用**专属 SA + 最小权限 Role**，不用共享账号；CI 账号按项目分 |
| `apply` 报 `Forbidden: exceeded quota: <name>, requested: X, used: Y, limited: Z` | 对象没创建 | **③ 准入层拒绝**：**ResourceQuota** 超了 | 找管理员加配额，或清理该 ns 的资源 | 独立 ns + 独立配额；CI 里跑 `kubectl describe quota` 提前发现 |
| `apply` 报 `Forbidden: violates PodSecurity ...` | 对象没创建 | **③ 准入层拒绝**：**Pod Security Admission** 拦了 | 按报错里的字段改（`privileged` / `hostPath` / `runAsUser` 等）；临时可先降到 `warn` 观察 | 命名空间打 `pod-security.kubernetes.io/enforce: restricted` + `-version: v1.37` |
| `apply` 报 `Forbidden: admission webhook "xxx" denied the request: <消息>` | 对象没创建 | **③ 准入层拒绝**：**ValidatingWebhook** 拒了（镜像签名、资源限制等） | 按自定义消息改；若是自建 webhook，查它的日志 | 准入策略变更先在 `audit` 模式跑一段 |
| `Pod "xxx" is invalid: spec.containers[0].resources.requests: Invalid value: "700m": must be less than or equal to cpu limit` | 对象没创建 | **③ 准入层拒绝**：**LimitRange** 的默认 limit 小于你写的 request | 同时写 requests 和 limits；或调大 LimitRange 的 `default` | 同一 ns 只留一个 LimitRange；改后重跑一次 apply |
| `apply` 成功，但 Pod 起不来，Events 里显示被准入拒绝 | Pod 没创建 | **PSA 的 `enforce` 只作用于最终 Pod，不作用于 Deployment** | 看 Pod 的 Events 里的拒绝原因；改 Deployment 的 `template.spec` | 所有安全配置写在 **Pod template** 里（不是 Deployment 顶层） |
| `kubectl auth can-i --list` 显示 `No RBAC policy matched` | 正常 | **默认拒绝生效了**（这正是想要的） | — | 保持 |
| `kubectl auth can-i --list` 里出现 `resources: ["*"]` | — | **有通配符授权**（官方明确反对 [S3]） | 收敛成具体资源 + 具体动词 | CI 里加扫描：**禁止 `*`** |
| `kubectl auth can-i list secrets` 返回 `yes` | — | **`list` 也能读到 Secret 全部内容**（官方明确 [S3]） | 去掉 `list`/`watch`；改用 `resourceNames` 精确到具体 Secret | Secret 的授权**永远不要给 `list`/`watch`** |
| `kubectl get secret xxx -o yaml` 看到的是 **base64** 明文 | 正常 | **Secret 只是 base64，不是加密**（官方明确 [S15]） | 启用静态加密（见 2.10）；最小权限访问 | 查权限时用 `-o jsonpath` 只取需要的字段，**别把整个 Secret 打到日志里** |
| `etcd` 备份里能看到 Secret 明文 | 正常 | **默认 `identity` provider，不加密**（官方明确 [S16]） | 配 `EncryptionConfiguration` + 重启 apiserver + 重写存量 Secret | 开启后纳入基线检查 |
| **NetworkPolicy 配了但流量照样通** | 正常 | **① CNI 插件不支持 NetworkPolicy**（官方明确：*"Creating a NetworkPolicy resource without a controller that implements it will have no effect."* [S11]）；**② 策略选错了 Pod**（`podSelector` 标签不对）；**③ 有另一条策略"放行"了它**（加法语义） | ① 换 Calico/Cilium；② `kubectl get pod --show-labels` 核对；③ `kubectl get networkpolicy -A` 把同 ns 的全看一遍 | 生产用支持 NetworkPolicy 的 CNI；**建立"同 ns 多策略"的评审规则** |
| **加了默认拒绝后，Pod 都 Running 但访问 Service 超时** | 正常 | **DNS 被封了**——CoreDNS 在 `kube-system`，egress 全封后解析不了 | 单独加一条允许到 `kube-system` 的 UDP/TCP 53 的 egress 规则 | **默认拒绝三件套是标配**：默认拒绝 + 同 ns 放行 + DNS 放行 |
| **加了默认拒绝后，Pod 连不上数据库/外部服务** | 正常 | egress 被封 | 按需放行外部网段（`ipBlock.cidr` + 端口） | 把 egress 白名单纳入变更评审 |
| **Pod 里 `cat /var/run/secrets/kubernetes.io/serviceaccount/token` 存在** | 正常 | **默认自动挂载**（官方 [S4]） | 不需要就设 `automountServiceAccountToken: false`（Pod 级或 SA 级） | 通用模板里默认关掉，需要的单独开 |
| **拿到某个 Pod 的 Token 后能读全集群 Secret** | — | **该 Pod 用的 SA 被绑了 ClusterRoleBinding**（或 SA 有 `nodes/proxy` 之类的权限） | 查 `kubectl get clusterrolebinding -o jsonpath=...` 找到绑定；改成 RoleBinding + namespace 级 Role | **ClusterRoleBinding 不绑业务 SA**；`nodes/proxy` 的 `get` 权限视为高危 |
| **改了 ClusterRole 的 rules 但不生效** | — | 该 ClusterRole 设了 **`aggregationRule`** → `rules` **由控制器管理，手工改会被覆盖**（官方明确 [S7]） | 改被聚合的源 ClusterRole；或去掉 `aggregationRule` | 聚合角色用标签管理，不要手改 `rules` |
| **改了 Binding 的 `roleRef` 报错** | 变更失败 | **`roleRef` 创建后不可改**（官方明确 [S8]） | 删掉 Binding 重建；或用 `kubectl auth reconcile` | 权限变更走"删了重建"的流程 |
| **RoleBinding 写了 ClusterRole 但没生效** | 正常 | ① `roleRef.name` 拼错 → **静默无效**；② `subjects[].name` 大小写错（**官方明确 case sensitive**）；③ SA 主体漏了 `subjects[].namespace` | 逐字核对；`kubectl get rolebinding -o yaml` 看渲染结果 | SA 主体**永远带 namespace** |
| **`kubectl auth can-i` 说 yes，但操作仍然 403** | — | **RBAC 通过了，但被准入层拦了**（配额 / PSA / Webhook）——`can-i` **只反映 RBAC 层** | 看真实报错里的准入原因 | 记住 `can-i` 的边界 |
| **改了命名空间的 PSA 标签后策略没变** | — | ① 标签写错（`pod-security.kubernetes.io/enforce` vs `enforce-version`）；② PSA **只对新创建的 Pod 生效**，已存在的 Pod 不受影响 | 核对标签拼写；重建 Pod 验证 | PSA 标签变更走"滚动重建"流程 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 中文 | `kubernetes.io/zh-cn/docs/concepts/architecture/` | apiserver 处理链：认证 → 授权 → 准入、组件架构（控制面职责划分） | ✅ 2026-10-24 |
| S2 | 官方 | `kubernetes.io/docs/reference/using-api/` | 资源清单字段语义交叉验证 | ✅ 2026-10-24 |
| S3 | 中文 | `kubernetes.io/zh-cn/docs/concepts/security/rbac-good-practices/` | **RBAC 最佳实践全文**：最小特权、通配符、cluster-admin、system:masters、特权令牌分发、加固（automountServiceAccountToken / system:unauthenticated）、定期检查、**9 类提权风险** | ✅ 2026-10-24 |
| S4 | 官方 | `kubernetes.io/docs/concepts/security/service-accounts/` | ServiceAccount 定义、default SA 自动创建与重建、TokenRequest/投射卷/自动轮转、automountServiceAccountToken、**Token 5 步校验** | ✅ 2026-10-24 |
| S5 | 官方 | `kubernetes.io/docs/reference/kubernetes-api/core/service-account-v1/` | ServiceAccount 对象的"名字 + 主体 + 密钥集合"定义 | ✅ 2026-10-24 |
| S6 | 官方 | `kubernetes.io/docs/reference/kubernetes-api/authorization-resources/role-v1/` | Role 是 namespaced 的 PolicyRule 集合 | ✅ 2026-10-24 |
| S7 | 官方 | `kubernetes.io/docs/reference/kubernetes-api/authorization-resources/cluster-role-v1/` | ClusterRole 是 cluster level 的 PolicyRule 集合、**aggregationRule 与 clusterRoleSelectors** | ✅ 2026-10-24 |
| S8 | 官方 | `kubernetes.io/docs/reference/access-authn-authz/rbac/` | RoleBinding vs ClusterRoleBinding 作用域、**RoleBinding 引用 ClusterRole 只在该 ns 生效**、通配符、**resourceNames 对 list/watch 与 create 的限制**、**roleRef 不可变的两条理由** | ✅ 2026-10-24 |
| S9 | 官方 | `kubernetes.io/docs/reference/kubernetes-api/definitions/policy-rule-v1-rbac/` | **PolicyRule 五字段逐字定义**（apiGroups / resources / resourceNames / verbs / nonResourceURLs） | ✅ 2026-10-24 |
| S10 | 中文 | `kubernetes.io/zh-cn/docs/reference/kubectl/quick-reference/` | kubectl 命令速查（`kubectl auth` 命令族） | ✅ 2026-10-24 |
| S11 | 官方 | `kubernetes.io/docs/concepts/services-networking/network-policies/` | **默认全通的三处原文**、**加法语义**、**双向放行条件**、**CNI 必须支持**、podSelector / namespaceSelector 语义 | ✅ 2026-10-24 |
| S12 | 官方 | `kubernetes.io/docs/reference/kubernetes-api/networking/network-policy-v1/` | NetworkPolicySpec 逐字段、policyTypes 默认推断规则、ingress/egress 空值语义、protocol 默认 TCP、endPort 限制 | ✅ 2026-10-24 |
| S13 | 官方 | `kubernetes.io/docs/concepts/security/pod-security-admission/` | PSA **Stable since v1.25**、三模式、命名空间标签格式、**enforce 只作用于最终 Pod**、豁免维度与警告、指标 | ✅ 2026-10-24 |
| S14 | 中文 | `kubernetes.io/zh-cn/docs/concepts/security/pod-security-standards/` | **三档定义（累进）**、Baseline 全部控制项与允许值、**Restricted 的卷类型白名单 / allowPrivilegeEscalation=false / runAsNonRoot / runAsUser≠0 / seccomp / capabilities drop ALL + add NET_BIND_SERVICE** | ✅ 2026-10-24 |
| S15 | 中文 | `kubernetes.io/zh-cn/docs/concepts/configuration/secret/` | **Secret 默认未加密存于 etcd**、base64 非加密、**能建 Pod ≈ 能读 Secret**、安全使用 Secret 的 4 条建议 | ✅ 2026-10-24 |
| S16 | 官方 | `kubernetes.io/docs/tasks/administer-cluster/encrypt-data/` | **默认明文存储**、identity provider 的"无任何保护"、四种 provider 对比表、**kms v2 信封加密流程**、EncryptionConfiguration 示例、**重启 apiserver**、存量资源不会自动重写 | ✅ 2026-10-24 |
| S17 | 官方 | `trivy.dev/latest/docs/` | Trivy 镜像/配置扫描与 SBOM 生成（门禁接入见 L3） | ✅ 2026-10-24 |
| S18 | 官方 | `kubernetes.io/docs/tasks/configure-pod-container/security-context/` | securityContext 的容器级与 Pod 级字段 | ✅ 2026-10-24 |
| S19 | 官方 | `kubernetes.io/docs/tasks/configure-pod-container/enforce-standards-namespace-labels/` | 用命名空间标签强制 PSA 的用法 | ✅ 2026-10-24 |
| S20 | 官方 | `kubernetes.io/docs/concepts/workloads/pods/` | Pod 生命周期与 Pod 对象字段（安全相关字段的上下文） | ✅ 2026-10-24 |
| S21 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/rbac` | ACK 上 RBAC 的实践形态 | ✅ 2026-10-24 |
| S22 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/use-a-network-policy-to-restrict-ingress-and-egress-traffic` | ACK 上 NetworkPolicy 的实践形态 | ✅ 2026-10-24 |
| S23 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/encrypt-secrets-in-etcd` | ACK 上 etcd Secret 加密的实践形态 | ✅ 2026-10-24 |
| S24 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/use-pod-security-admission-to-enforce-the-pod-security-standards` | ACK 上 PSA 的实践形态 | ✅ 2026-10-24 |
| S25 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/comply-with-the-classified-protection-requirements-of-the-cybersecurity-classified-protection-regulation-2-0` | ACK 上等保 2.0 合规的实践形态 | ✅ 2026-10-24 |
| S26 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 托管版安全实践总入口 | ✅ 2026-10-24 |

> **完整来源矩阵（含全部 15 个组件）见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。** 本篇只列用到的。
> **链接实测方法见** [`../09-参考索引/README.md`](../09-参考索引/README.md)（含 3 次重试脚本，**000 是网络抖动不是失效**）。本篇全部 URL 于 2026-10-24 用该脚本逐条实测。

### 待验证清单

| # | 待验证项 | 原因 | 怎么验 |
|---|---|---|---|
| 1 | **等保 2.0 各控制项（GB/T 22239-2019）与 K8s 能力的逐条对应关系** | **等保标准本身不是 K8s 文档**，本库索引未收录国标原文；本篇 2.x 的对应关系是**基于机制层面的工程映射**，不是标准条文引用 | 补国标原文到来源索引后逐条核对；或以云厂商的等保合规白皮书为准（该页面为前端渲染，**本篇未逐字核对其内容**） |
| 2 | **镜像签名验证（cosign / Notary）的具体 CLI 与 K8s 准入集成方式** | 本库索引**未收录 cosign / Notary / sigstore 的官方文档**（见 L3 待验证清单第 2 项） | 补 sigstore 官方文档后核对 `cosign sign --keyless` / `cosign verify` 与 policy-controller 的配置 |
| 3 | **Trivy 漏洞库的具体条数与更新频率** | 随时间变化，**不编数字** | 以 `trivy.dev/latest/docs/` 官方说明为准 |
| 4 | **`kubectl auth can-i --list` 对 `SelfSubjectRulesReview` 的展示限制** | 官方文档未明确说明该命令在**被准入策略限制**时的完整表现 | 在测试集群构造一个被 PSA 拒绝的 ns，对比 `can-i` 与真实报错 |
| 5 | **kind 默认 CNI（kindnet）是否支持 NetworkPolicy** | kind 官方未明确声明；**3.3 的实验结果可能与生产不同** | `kubectl get networkpolicy` 后实测连通性；或查 kind 官方文档 |
| 6 | **PSA 在 `enforce` 模式下对 `ephemeralContainers` 的判定时机** | 官方 [S13] 列出了豁免维度，但未说明临时容器是否在创建时被拦 | 在 restricted 命名空间里用 `kubectl debug` 加临时容器实测 |

> **单源知识点标注**：
> - **等保 2.0 具体控制项条文** —— **⚠️ 单源待补**（未收录国标原文）
> - **镜像签名工具链的 CLI 细节** —— **⚠️ 单源待补**（未收录 sigstore 官方文档）
> - **云厂商 ACK 的等保合规白皮书内容** —— **⚠️ 单源待补**（页面为前端渲染，本篇仅引用其作为入口，**未逐字核对其内容**）

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入是一个 HTTP 请求（带身份凭据）+ 一个对象 + 集群的策略配置；输出是"对象被写入 etcd"或"某个 HTTP 状态码的拒绝"。中间三道闸门：**认证（你是谁）→ 授权（RBAC 说你能不能做）→ 准入（PSA / LimitRange / Quota / Webhook 说这个对象合不合规）**。**RBAC 管"谁"，准入管"什么"——这是全篇的题眼。**
2. **能故意改坏再倒推吗？** —— 五个实验：① 建一个 Role 只写 `verbs: [get]`（不给 `list`），然后 `kubectl get cm` → 看是否正常但 `kubectl get cm -A` 报 403（这解释了 `list` 与 `get` 的区别）；② 给命名空间打 `pod-security.kubernetes.io/enforce: restricted` 标签，然后 apply 一个带 `hostPath` 的 Deployment → 看 Deployment **apply 成功**但 Pod 被拒（**验证"enforce 只拦最终 Pod"**）；③ 建一个 `podSelector` 标签写错的 NetworkPolicy → 看"策略建了但流量没变化"（**验证选择器匹配**）；④ 用 `kubectl auth can-i --list` 找自己的 `*` 权限；⑤ 把 `roleRef` 改一下 → 看 validation error。**实验 ② 最能说明"RBAC 与准入口径不同"。**
3. **能画上下游吗？** —— 画出 `客户端 → apiserver → ①认证 → ②授权(RBAC: Role/ClusterRole × Binding × PolicyRule) → ③准入(PSA/LimitRange/ResourceQuota/ValidatingWebhook/MutatingWebhook) → etcd`，并在 ② 旁标出"User \"xxx\" cannot ... → 403"，在 ③ 旁标出"exceeded quota / violates PodSecurity → 403"。然后画出**第二条平行链**：ServiceAccount → Token（投射卷）→ Pod → apiserver → RBAC，并在上面标出"`system:serviceaccount:default:default` 的四条风险"与"第三道闸门 NetworkPolicy 在网络平面而不是 API 平面"。

---

**上一篇**：[`L8-弹性HPA.md`](L8-弹性HPA.md) · **下一篇**：[`L10-排障方法论.md`](L10-排障方法论.md) · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md) · **相关**：[`../01-基础篇/L3-镜像仓库与供应链安全.md`](../01-基础篇/L3-镜像仓库与供应链安全.md)（镜像供应链）
