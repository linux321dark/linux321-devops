# L1 · K8s 环境搭建与 kubectl 入门（从零起一个能用的集群）

> **本篇定位**：**先能上手，再懂原理**。本篇只解决两件事：① 选一条建集群的路走通；② kubectl 用到能闭眼敲。
> **前置**：L0（Shell 与 YAML）。**不要求**先懂 K8s 架构——架构是下一篇 L2 的事。
> **学完能做什么**：独立用 kind 起集群、在多套环境间切换 context、用 kubectl 排查资源状态；**能说清 kind / kubeadm / ACK 三条路线的差别与各自适用场景**。
> **在 21 天里**：D2 之前的准备；D7/D14 的整合项目都跑在本地 kind 上。
> **环境基准**：本机实测 `kubectl v1.37.1`，kind 集群名 `day2`，节点 `day2-control-plane`（`kindest/node:v1.37.0`）。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L1 K8s 环境搭建与 kubectl 入门
│
├─ 1. 概念
│   ├─ 1.1 集群是什么
│   ├─ 1.2 三条建集群路线
│   └─ 1.3 kubectl 是什么 / 术语表
│
├─ 2. 机制
│   ├─ 2.1 kubectl 的通信链路与 kubeconfig
│   ├─ 2.2 context 切换
│   ├─ 2.3 声明式 vs 命令式
│   ├─ 2.4 命名空间
│   ├─ 2.5 kind 的原理（节点是容器）
│   ├─ 2.6 kubeadm 的流程
│   └─ 2.7 ACK 托管版：控制面谁负责
│
├─ 3. 动手
│   ├─ 3.1 kind 起集群（含多节点）
│   ├─ 3.2 kubectl 基础操作
│   ├─ 3.3 多环境 context 管理
│   └─ 3.4 输出格式与 jsonpath
│
├─ 4. 逐字段表
│
├─ 5. 横向对比：五条建集群路线
│
├─ 6. 踩坑表
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 集群是什么

> **一个 K8s 集群 = 一个控制面 + 若干节点 + 一份 etcd。**

- **控制面（Control Plane）**：负责"决策"。apiserver（唯一入口）、etcd（唯一真相库）、scheduler（派活）、controller-manager（催活）
- **节点（Node）**：负责"干活"。每台节点跑 kubelet + 容器运行时
- **你操作集群的方式**：**只有一个入口 —— apiserver**。kubectl、controller、web dashboard 全都走它

> **一个关键认知**：K8s 里**没有"SSH 到机器上"的传统运维路径**。你的一切操作都是**改期望态**，让控制器去实现。**这条认知是后面所有内容的前提。**

### 1.2 三条建集群路线

| 路线 | 控制面在哪 | 适合 | 成本 | 上手速度 |
|---|---|---|---|---|
| **kind** | 你的 Docker 容器里 | **本地学习、CI 临时环境** | 零 | **秒级** |
| **kubeadm** | 你自己管理的机器上 | 传统自建、生产自管 | 中 | 30 分钟起 |
| **ACK（阿里云托管版）** | **云厂商托管**，你只管节点 | **本地民企的真实选择** | 付费 | 分钟级开通 |

> **必须讲清的判断**：**本地民企 90% 用阿里云 ACK**（控制面托管、只付节点费）。所以：
> - **学原理 → 用 kind**（零成本、随便重建）
> - **做面试准备 → 要懂 ACK 形态**（弹性伸缩组、SLB/ALB 集成、托管版差异）
> - **真上手 → 用 ACK**，不要自己 kubeadm 搭生产集群（**自建控制面的运维负担对 10 几人团队是灾难**）
>
> **你简历写"生产 K8s 经验"时，面试官追问的一定是 ACK 形态，不是 kubeadm。**

### 1.3 kubectl 是什么

> **kubectl 是 apiserver 的 HTTP 客户端。** 它把你的 yaml 转成 API 请求发过去，本质上和 dashboard、controller 用的是同一套 API。

**这意味着**：
- kubectl 能做的，dashboard 都能做，反之亦然
- 你在 kubectl 里看到的字段，就是 API 的字段（`kubectl get -o yaml` 看到的就是 API 对象本身）
- **kubectl 的所有子命令都可以用 `kubectl get --raw` 或 `kubectl explain` 查证**

---

## 2. 机制

### 2.1 kubectl 的通信链路与 kubeconfig

```
你敲 kubectl
   │
   ▼
读 ~/.kube/config（kubeconfig）
   │  里面写着：连哪个集群、用什么身份、切到哪个命名空间
   ▼
HTTPS + 客户端证书（mTLS）
   │
   ▼
kube-apiserver  :6443
   │  认证（你是谁）→ 授权（RBAC 允许吗）→ 准入 → 写 etcd
   ▼
etcd
```

**kubeconfig 的三段结构**（这是本篇最重要的结构化知识）：

```yaml
apiVersion: v1
kind: Config
clusters:      # 集群：连哪台 apiserver，证书是什么
- name: kind-day2
  cluster:
    server: https://127.0.0.1:6443
    certificate-authority-data: <CA 证书>
users:         # 身份：我是谁，用什么凭据
- name: kind-day2-admin
  user:
    client-certificate-data: <客户端证书>
    client-key-data: <客户端私钥>
contexts:      # 上下文：集群 + 身份 + 命名空间的组合
- name: kind-day2
  context:
    cluster: kind-day2
    user: kind-day2-admin
    namespace: default
current-context: kind-day2
```

> **关键理解**：`context` = **集群 × 身份 × 命名空间** 的三元组。**切换 context 就是切这三样。**
> 多人共用一套集群时，每人各自的 kubeconfig 里 context 名字可以不同，但指向同一个 cluster + 同一个 user。

### 2.2 context 切换

```bash
kubectl config get-contexts      # 列出所有 context
kubectl config current-context   # 当前用的哪个
kubectl config use-context kind-day2   # 切换
kubectl config set-context --current --namespace=prod   # 只切命名空间
kubectl config set-context kind-day2 --namespace=prod    # 改某个 context 的默认 ns
```

> **实战价值**：多环境（本地 / 测试 / 生产）各配一个 context，**靠 context 切换而不是靠改 yaml**，可以避免"把生产当成测试操作"这类事故。**建议每个 context 名字里带上环境标识。**

### 2.3 声明式 vs 命令式

| 范式 | 命令 | 特点 | 风险 |
|---|---|---|---|
| **声明式** | `apply` | 声明期望态，控制器持续拉平 | 幂等，可重复执行，**可入 Git** |
| **命令式** | `create` / `edit` / `delete` / `patch` | 直接改一次 | 不可重复，**不在 Git 里，下次 apply 会被改回去** |

**必须理解的一条**：

```bash
kubectl apply -f x.yaml      # 声明
kubectl edit deploy foo      # 手改（不在 Git 里）
kubectl apply -f x.yaml      # 再执行声明 → 刚才手改的被覆盖！
```

> **这是 GitOps 存在的根本原因**，也是生产上"禁止直接 kubectl edit"的纪律来源。
> **面试这么答**："我们要求所有变更走 Git + apply，因为集群里的手工修改不在版本控制里，下一次同步就会被覆盖，等于制造了漂移。"

### 2.4 命名空间

- **作用**：资源分组 + RBAC 作用域 + 网络策略作用域
- **默认**：`default`、`kube-system`、`kube-public`
- **限制**：**不是安全边界**（不是虚拟机隔离），**不是多租户强隔离方案**

```bash
kubectl get ns
kubectl create ns prod
kubectl -n prod get pods
kubectl get pods -A          # 所有命名空间
```

> **最常见的两个坑**：
> ① 忘了 `-n`，资源建到了 `default`，然后在别的 ns 里找不到
> ② `kube-system` 里改东西会直接搞挂集群（**它跑的是 apiserver、scheduler、CoreDNS 等核心组件**）

### 2.5 kind 的原理

**kind = Kubernetes IN Docker**：每个"节点"是一个 Docker 容器，里面跑完整的 kubelet + containerd + 控制面组件。

```
┌─────────────────────────────────────────┐
│ 宿主机 Docker                              │
│  ┌───────────────────────────────────┐  │
│  │  day2-control-plane 容器           │  │
│  │  ├─ kube-apiserver                │  │
│  │  ├─ etcd                          │  │
│  │  ├─ kube-scheduler                │  │
│  │  ├─ kube-controller-manager       │  │
│  │  ├─ CoreDNS                       │  │
│  │  ├─ kubelet                       │  │
│  │  └─ containerd                    │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

**三个必知限制**（很多人在这里栽）：

1. **单节点**（默认）→ 所有 Pod 挤在一个节点上，**调度相关的实验（污点、亲和性、资源竞争）做不了**。要多节点得写多节点配置文件
2. **节点是容器** → 节点内**没有你宿主机的 docker 凭据**，拉私有镜像会 401（**这正是 D2 那个 `ImagePullBackOff` 的根因**）
3. **组件版本随 kind 走** → 不是你能随意切的；`kindest/node` 镜像的 tag 决定集群版本

### 2.6 kubeadm 的流程

```
1. 准备机器（所有节点都装 containerd、关闭 swap、配置内核模块）
2. 主节点：kubeadm init
   └─ 生成集群 CA、生成控制面证书、生成 kubelet kubeconfig
   └─ 初始化 etcd、拉起控制面静态 Pod、写 kubeadm-config
3. 工作节点：kubeadm join <主节点:6443> --token ... --discovery-token-ca-cert-hash ...
   └─ 下发 kubeconfig，节点注册
4. 装网络插件（Calico / Flannel）—— 不装节点会 NotReady
```

**关键认知**：**kubeadm 只负责"搭骨架"，装完集群还是空的**：
- 网络插件要自己装（**不装 → 节点一直 NotReady**）
- CoreDNS 靠网络插件才能工作
- Ingress Controller、metrics-server、存储插件全都要另外装
- **生产环境还要做**：控制面高可用（kubeadm + 至少 3 个控制面节点）、etcd 备份、证书轮转

### 2.7 ACK 托管版：控制面谁负责

| 组件 | ACK 托管版 | 自建 kubeadm |
|---|---|---|
| apiserver / etcd / scheduler / controller | **云厂商负责（托管）** | 你负责 |
| 节点池 | 你负责（但可用弹性伸缩组自动扩缩） | 你负责 |
| 集群升级 | **控制面由云厂商升级**，节点你升级 | 全自己 |
| etcd 备份 | **云厂商负责** | 你负责 |
| 可用区 | 支持多可用区 | 自己规划 |
| 费用 | **控制面费 + 节点费 + 流量费** | 机器费 + 人力 |

> **面试必问**："你在生产上用的是托管还是自建？为什么？"
> **高分答法**："我们用 ACK 托管版，理由是 10 几人团队没有精力运维控制面——托管版给了我们 etcd 高可用、跨可用区、控制面免运维，团队只需要管节点和工作负载。代价是要为托管控制面付费，而且必须学会用 ACK 的方式做节点伸缩（弹性伸缩组）和 ALB 集成。"

---

## 3. 动手

### 3.1 kind 起集群

```bash
# ① 起一个单节点集群
kind create cluster --name day2

# ② 确认控制面通
kubectl cluster-info --context kind-day2

# ③ 看节点
kubectl get nodes
# 期望：day2-control-plane   Ready   control-plane   30s   v1.37.0

# ④ 看内置组件（应该都在 kube-system）
kubectl get pods -n kube-system

# ⑤ 清理
kind delete cluster --name day2
```

**多节点集群（写配置文件，不用 kubectl 记）**：

```yaml
# kind-cluster.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
- role: worker
- role: worker
```

```bash
kind create cluster --name multi --config kind-cluster.yaml
kubectl get nodes          # 3 个节点
```

> **多节点才有的意义**：能验调度（污点、亲和性、资源竞争）、能验 HPA（Pod 分散到不同节点）、能验节点排障（drain 一个节点看 Pod 迁移）。

### 3.2 kubectl 基础操作

```bash
# 查
kubectl get pods
kubectl get pods -o wide            # 加节点信息
kubectl get pods -w                 # 持续观察
kubectl get pods --show-labels
kubectl get pods -l app=day1-go     # 按 label 过滤
kubectl describe pod <name>         # 详情 + Events
kubectl get all                     # 常用资源全看
kubectl api-resources               # 这个集群支持哪些资源类型
kubectl api-versions                # 支持哪些 API 版本

# 增删改
kubectl apply -f x.yaml
kubectl create deployment web --image=nginx
kubectl delete pod <name>
kubectl delete -f x.yaml
kubectl scale deployment/day1-go --replicas=3
kubectl rollout restart deployment/day1-go
kubectl exec -it <pod> -- sh
kubectl logs -f <pod>
kubectl port-forward svc/day1-go-svc 18080:80

# 上下文与命名空间
kubectl config get-contexts
kubectl config use-context kind-day2
kubectl get ns
kubectl -n kube-system get pods
```

### 3.3 多环境 context 管理

```bash
# 生成一个指向生产集群的 context（凭据从别处拿）
kubectl config set-cluster prod --server=https://<prod-api>:6443 --certificate-authority=<ca.crt> --embed-certs=true
kubectl config set-credentials prod-admin --client-certificate=<client.crt> --client-key=<client.key> --embed-certs=true
kubectl config set-context prod --cluster=prod --user=prod-admin --namespace=production
kubectl config use-context prod
```

> **纪律**：**给 context 名字里带环境前缀**（`kind-day2` / `test-hz` / `prod-hz`），`kubectl config get-contexts` 一眼就能看清自己在哪。**比每次 `kubectl --context=xxx` 手打安全得多。**

### 3.4 输出格式与 jsonpath

```bash
kubectl get pods -o json                     # 完整对象
kubectl get pods -o yaml                     # 完整对象（人读）
kubectl get pods -o wide                     # 常用列
kubectl get pods -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,IP:.status.podIP'
kubectl get pods -o jsonpath='{.items[0].metadata.name}'     # 取单个值
kubectl get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
kubectl get pods --no-headers | awk '{print $1}'             # 只要第一列
```

> **`-o wide` 里的 NODE 列是排障第一入口**：Pod 卡住先看它在哪个节点，问题多半在那个节点上。

---

## 4. 逐字段表

### 4.1 kubeconfig 关键字段

| 字段 | 含义 | 常见问题 |
|---|---|---|
| `clusters[].cluster.server` | apiserver 地址 | 端口非 6443、用了内网 IP 但不在同一网段 |
| `clusters[].cluster.certificate-authority-data` | CA 证书（base64） | 过期 → TLS 握手失败 |
| `users[].user.client-certificate-data` / `client-key-data` | 客户端证书与私钥 | 过期 → `Unauthorized`；**RBAC 拒绝是 `Forbidden`，两者要分清** |
| `users[].user.token` | 静态 token | 过期需换；**ServiceAccount token 是短期的** |
| `contexts[].context.cluster` / `.user` / `.namespace` | 三元组 | namespace 拼错 → 资源"消失" |
| `current-context` | 默认 context | 切错环境 → **操作到了生产** |

### 4.2 kubectl 通用参数

| 参数 | 作用 | 典型场景 |
|---|---|---|
| `-n <ns>` | 指定命名空间 | **最常用，也最常忘** |
| `-A` | 所有命名空间 | 跨 ns 查资源 |
| `-o wide/json/yaml/custom-columns/jsonpath` | 输出格式 | 脚本取字段用 jsonpath |
| `-l <selector>` | label 选择器 | 按标签批量操作 |
| `--field-selector` | 字段选择器 | 如 `spec.nodeName=k8s-node1`（**与 label 选择器是不同维度**） |
| `--dry-run=client` | 本地校验语法 | 提交前自查（**不做类型转换**） |
| `--dry-run=server` | 服务端完整校验 | 提交前最后一道（**能查类型错**） |
| `--all-namespaces` | 跨 ns | 配合 `-o wide` |
| `--ignore-not-found` | 不存在时不报错 | 脚本里做幂等删除 |
| `--force` / `--grace-period` | 强制删除 | **慎用，跳过优雅停机** |
| `--no-headers` | 不打表头 | 管道处理 |
| `--sort-by` | 排序 | `--sort-by=.metadata.creationTimestamp` |
| `-w` | watch 持续观察 | 等就绪、等状态变化 |
| `--context` | 指定 context | 脚本里**显式指定环境，别依赖 current-context** |

---

## 5. 横向对比：五条建集群路线

| 方案 | 控制面 | 节点形态 | 多节点 | 适合 | 限制 |
|---|---|---|---|---|---|
| **kind** | 容器内 | 容器 | ✅ 配置文件定义 | **本地学习、CI** | 节点是容器，**拉私有镜像要 kind load**；性能非真实 |
| **minikube** | 容器/虚机内 | 虚机 | ✅ | 本地开发、**想体验真实 kubelet 与网络** | 资源占用大、启动慢 |
| **k3s** | 轻量 | 真实机器/虚机 | ✅ | **边缘、ARM 设备、真实环境练习** | 与上游有差异，**生产用要谨慎** |
| **kubeadm** | 自建 | 真实机器 | ✅ | **传统自建生产** | **控制面高可用要自己搞**；全套运维负担 |
| **ACK / 托管版** | **云厂商托管** | 云 ECS | ✅ | **本地民企的真实选择** | 付费；节点与网络由云厂商约束 |

> **学习路径建议**（也是面试话术）：
> **kind 学会操作 → ACK 学会生产形态 → kubeadm 只需"知道它存在、能看懂它的流程"**。
> **别在 kubeadm 上花时间**，那是 2000 年代的运维话题，**面试不会问你 kubeadm 集群怎么搭**。

---

## 6. 踩坑表

| 现象 | 真实原因 | 怎么解 | 下次怎么防 |
|---|---|---|---|
| `error: current-context is not set` | kubeconfig 里没设默认 context | `kubectl config use-context <name>` | 建集群后立刻设 |
| `Unable to connect to the server: dial tcp ... connection refused` | apiserver 没起/端口不对/容器没跑 | `docker ps` 看 kind 容器 | kind 容器起完再操作 |
| `x509: certificate has expired` | CA 或客户端证书过期 | 重建集群或换凭据 | kind 集群别长期不用 |
| `You must be logged in to the server (Unauthorized)` | 认证失败（凭据/身份） | 查 kubeconfig 的 user | 401 与 403 分开看 |
| `deployments.apps "x" is forbidden: User "y" cannot list resource` | **认证过了但没权限（RBAC）** | `kubectl auth can-i --list` | 403 查 RBAC，不是证书 |
| `error: the server doesn't have a resource type "xxx"` | 资源名/版本错 | `kubectl api-resources` | 不背资源名，查 |
| 资源"消失" | **namespace 搞错** | `kubectl get pods -A` | 养成带 `-A` 的习惯 |
| 改完的 Deployment 又变回去 | `kubectl edit` 改的不在 Git 里，被 apply 覆盖 | 走 Git + apply | 生产禁 edit |
| `ImagePullBackOff` | 私有仓库无凭据 | `kind load docker-image` | **记住：节点是容器，不共享宿主 docker 凭据** |
| kind 集群里调度实验做不了 | 单节点 | 写多节点 kind 配置 | 需要多节点就用配置文件 |
| `kubectl delete pod` 后 Pod 一直 Terminating | 有 finalizer 或长连接未断 | `kubectl get pod -o yaml` 看 finalizers | 优雅停机配好 preStop |
| 集群 API 慢/超时 | 资源不足、etcd 压力 | `docker stats` 看 kind 容器 | kind 集群别塞太多东西 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `kind.sigs.k8s.io/docs/user/quick-start/` | **kind 安装与创建集群** | ✅ 2026-10-24 |
| S2 | 官方 | `kubernetes.io/zh-cn/docs/reference/kubectl/quick-reference/` | **kubectl 速查（含 dry-run、输出格式）** | ✅ 2026-10-24 |
| S3 | 官方 | `kubernetes.io/zh-cn/docs/concepts/architecture/` | 控制面与节点架构 | ✅ 2026-10-24 |
| S4 | 官方 | `kubernetes.io/zh-cn/docs/concepts/overview/` | 集群与对象总览 | ✅ 2026-10-24 |
| S5 | 官方 | `kubernetes.io/zh-cn/docs/tasks/manage-kubernetes-objects/declarative-config/` | **声明式 vs 命令式** | ✅ 2026-10-24 |
| S6 | 官方 | `kubernetes.io/zh-cn/docs/tasks/debug/debug-cluster/` | 集群级排障 | ✅ 2026-10-24 |
| S7 | 云厂商 | `help.aliyun.com/zh/ack/` | **ACK 托管版形态** | ✅ 2026-10-24 |
| S8 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 使用指南 | ✅ 2026-10-24 |

> **本篇的 kind 版本与集群信息为本地实测**（`kubectl v1.37.1`，集群 `day2`，节点镜像 `kindest/node:v1.37.0`）。
> **kubeadm 与 ACK 的流程描述来自公开共识与官方文档**，具体参数以对应版本的官方文档为准（标 `⚠️ 待验证` 的部分需按当期版本复核）。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入：kubectl 命令（本质是 API 请求）；输出：apiserver 写 etcd，controller 协调出实际态，kubectl 再把结果读回来。中间经过 kubeconfig（定身份/集群/命名空间）→ 认证 → 授权 → 准入 → etcd。
2. **能故意改坏再倒推吗？** —— 四个实验：① 切到不存在的 context → 看报错怎么变；② 删掉 kubeconfig 的 `current-context` → 看 "current-context is not set"；③ 在 `default` ns 建资源但不带 `-n` 去别的 ns 查 → 看"消失"；④ 用 `-n kube-system` 删一个 Pod → 看集群怎么反应（**这是危险实验，只在 kind 上做**）。
3. **能画上下游吗？** —— 画出「你 → kubectl → kubeconfig → apiserver → etcd → controller/scheduler → kubelet → 容器」这条链，并在每一段标出"这段挂了你会看到什么"（认证/授权/准入/连接/超时各对应不同报错）。

---

**下一篇**：[`L2-K8s架构与对象模型`](L2-K8s架构与对象模型.md)（**已写** · 组件分工 · 一次 apply 到 Running 的 9 步链路） · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md) · **关联日课**：[`D2 K8s Deployment + HPA`](../第1周/第02天.md)
