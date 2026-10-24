# L2 · K8s 架构与对象模型（零基础 → 能讲清原理）

> **本篇定位**：整套资料的**中枢篇**。前面 L1 讲容器怎么跑起来，这里讲容器编排器怎么管它们。
> **前置**：L1 Docker（必须会写 Dockerfile、懂镜像与容器区别）、L0 YAML 语法。
> **学完能做什么**：说清 apiserver / etcd / scheduler / kubelet 各自干什么；能画出一次 `apply` 到 Pod Running 的完整链路；知道每种工作负载该选哪个。
> **在 21 天里**：D2（Deployment + HPA）、D3（网络）、D7（整合）都建立在本篇之上。
> **版本基准**：本文命令与字段以本地实测 `kubectl v1.37.1` / `Server v1.37.0`（kind 集群）为准。生产 ACK 版本与本地不同，**字段语义以官方文档对应版本为准**。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型是本篇的骨架。

---

## 0. 知识图谱

```
K8s 架构与对象模型
│
├─ 1. 概念
│   ├─ 1.1 一句话：声明期望 + 持续协调
│   ├─ 1.2 没有 K8s 时的问题
│   └─ 1.3 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 三大状态：期望态 / 实际态 / 协调
│   ├─ 2.2 组件全景：控制面 + 节点侧
│   ├─ 2.3 一次 apply 到 Running 的完整链路（9 步）
│   └─ 2.4 关键参数与默认值
│
├─ 3. 动手：从零起一个集群
│   ├─ 3.1 kind（本地零成本）
│   ├─ 3.2 第一个 Deployment
│   └─ 3.3 验证清单
│
├─ 4. 资源清单逐字段
│   ├─ 4.1 Deployment
│   ├─ 4.2 Service
│   └─ 4.3 label / selector 的匹配规则
│
├─ 5. 横向对比
│   ├─ 5.1 五种工作负载怎么选
│   └─ 5.2 四种 Service 类型怎么选
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

> **Kubernetes 是一个"你声明期望状态、它持续把实际状态拉向期望状态"的系统。**

这句话是全文的地基。K8s 不是"启动容器的脚本集合"，它是一个**控制循环系统**：

- 你只声明"我要 3 个副本"（期望态）
- 控制器（controller）不停比对"现在有 1 个"（实际态）
- 差了就动手：建 Pod、删 Pod、改配置
- 建完之后继续比对，直到一致

**它不关心你怎么实现，只关心最终一致。** 这也是为什么 K8s 里同一个结果有很多条路——只要你声明的期望态一样，控制器用什么方式达成由它自己决定。

### 1.2 没有 K8s 时的问题

拿你熟的场景对照（D1 那套裸机部署）：

| 裸机/虚机方式 | 痛点 | K8s 怎么解决 |
|---|---|---|
| 人工 SSH 上去装服务、启进程 | 挂了要人发现、人恢复 | 声明 3 副本，挂了自动重建 |
| 手工 keepalived / HAProxy 做 VIP | 脑裂、抢 VIP 打架 | Service 用 iptables/IPVS 做转发，天然无单点 |
| 新机器上线要改脚本、装依赖 | 环境漂移，同一服务两台机器不一样 | 镜像是不可变交付物，声明式拉起 |
| 扩容要人决策、人操作 | 分钟级甚至小时级 | 改一个数字 `replicas`，控制器自己干 |
| 配置散落在各机器的配置文件里 | 改一个参数要登 50 台机器 | ConfigMap 改一处，全量生效 |
| 不知道谁在跑什么版本 | 靠人记 | `kubectl get deploy` 一眼看到 |

> **关键认知**：K8s 解决的不是"跑得快"，是"**状态可预期**"。这决定了后面所有的设计取舍。

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Pod | 豆荚 | K8s 的最小调度单位，1 个或多个容器 + 共享网络与卷 |
| Deployment | 部署 | 管理一组**无状态** Pod 副本的控制器，负责滚动更新与回滚 |
| ReplicaSet | 副本集 | Deployment 底下的实际副本管理者，只管"保持 N 个副本" |
| StatefulSet | 有状态集 | 带**稳定网络标识与顺序**的 Pod 集合（MySQL/Kafka 用它） |
| DaemonSet | 守护集 | 每个节点跑一个副本（日志采集、监控 agent） |
| Job / CronJob | 任务/定时任务 | 跑完就结束 / 定时跑 |
| Service | 服务 | 一组 Pod 的**稳定访问入口** + 负载均衡 + 服务发现 |
| Ingress | 入口 | 七层 HTTP/HTTPS 路由规则（**本身不转发，是给 Controller 看的配置**） |
| ConfigMap | 配置映射 | 非敏感配置（key-value） |
| Secret | 密钥 | 敏感配置（**默认只是 base64，不是加密**） |
| PV / PVC | 持久卷 / 持久卷声明 | 存储的"提供方"与"使用方" |
| Namespace | 命名空间 | 资源隔离与分组（不是安全边界，是逻辑边界） |
| Node | 节点 | 一台机器（物理机或虚机），跑 kubelet |
| Cluster | 集群 | 一组节点 + 控制面 |
| Controller | 控制器 | 跑 reconcile 循环的程序 |
| Replica / Replicas | 副本 | 同一个模板起出的多个相同 Pod |
| scheduler | 调度器 | 决定 Pod 放哪个节点 |
| etcd | — | 集群的"唯一真相库"，存所有期望态 |
| kubelet | 节点代理 | 在每个节点上，负责"让 Pod 真的跑起来" |
| probe（探针） | 探针 | 定期问容器"你还活着吗/能接流量吗" |

---

## 2. 机制

### 2.1 三大状态（全文最重要的一张图）

```
        你写 yaml
           │
           ▼
    ┌──────────────┐   controller 不断比对   ┌──────────────┐
    │   期望态      │ ◄─────────────────────► │   实际态      │
    │  (spec)      │        差异 → 动手        │  (status)    │
    │  存在 etcd   │                          │  存在 etcd   │
    └──────────────┘                          └──────────────┘
           │                                        │
           │ apiserver 写入                          │ kubelet 执行
           ▼                                        ▼
    ┌────────────────────────────────────────────────────────┐
    │  真实运行态：节点上的容器进程、端口、文件、路由规则      │
    └────────────────────────────────────────────────────────┘
```

**三层排障就是顺着这张图走**：

| 现象 | 你在哪一层 | 怎么查 |
|---|---|---|
| `apply` 就报错 | 期望态写错了 | 报错信息里直接说了（selector 不匹配、字段名不存在） |
| `apply` 成功但 Pod 不起来 | 协调卡住了 | `describe` → **Events**，看卡在调度/拉镜像/探针哪一步 |
| Pod Running 但业务不对 | 真实运行态 | `logs` / `exec` 进去看 |

> 读到这里回头看授渔篇那三层追查——**同一件事，从 K8s 内部机制的角度再看一遍**。

### 2.2 组件全景

**控制面（Cluster 内，有高可用）**

| 组件 | 干什么 | 你怎么跟它打交道 |
|---|---|---|
| `kube-apiserver` | **唯一入口**。所有请求进来，它做认证→授权→准入→写 etcd | `kubectl` 的一切 |
| `etcd` | 存所有期望态和实际态，集群的唯一真相库 | 一般不直接碰 |
| `kube-scheduler` | 读未调度的 Pod，按条件挑一台节点 | 通过 `nodeSelector`/`affinity`/`tolerations` 影响它 |
| `kube-controller-manager` | 一堆控制器跑在这里（Deployment/RS/StatefulSet/Job/…） | 通过写资源间接影响 |
| `cloud-controller-manager` | 云厂商相关的控制器（LB、路由） | ACK/EKS 上才有 |

**节点侧（每台机器一个）**

| 组件 | 干什么 |
|---|---|
| `kubelet` | **关键角色**：watch 到"Pod 分配给我了"→ 拉镜像 → 创建容器 → 配网络 → 跑探针 → 回报状态 |
| 容器运行时 | containerd / CRI-O，真正跑容器进程 |
| CNI 插件 | Calico / Flannel / Cilium，负责 Pod 网络 |
| kube-proxy | 维护 Service 的转发规则（老版本用 iptables，新版可用 eBPF/IPVS） |

**一句话记住分工**：
> **apiserver 收单，etcd 记账，scheduler 派活，controller 催活，kubelet 干活。**

### 2.3 一次 `apply` 到 Pod Running 的完整链路

这是面试高频题（"你讲讲一个 Pod 是怎么起来的"），也是理解排障的最好方式：

| # | 发生在哪 | 做了什么 | 出问题时的现象 |
|---|---|---|---|
| 1 | kubectl → apiserver | yaml 转 JSON，`POST /apis/apps/v1/namespaces/default/deployments` | 网络/证书问题：`connection refused` |
| 2 | apiserver | **认证**（你是谁）→ **授权**（RBAC 允许吗）→ **准入控制**（Webhook/策略） | 401 / 403 |
| 3 | apiserver → etcd | 写入 Deployment 对象，返回 `201 Created` | etcd 不可用：`etcdserver: request timed out` |
| 4 | deployment-controller | watch 到变化，发现 `spec.replicas=1` 但实际 0 个 → **创建 ReplicaSet** | 资源里看不到 RS |
| 5 | replicaset-controller | 读 RS，发现缺 1 个 Pod → **创建 Pod 对象**写 etcd | `get pods` 空、Event 里 `FailedCreate` |
| 6 | kube-scheduler | watch 到未调度 Pod（`spec.nodeName` 为空）→ 过滤不满足的节点 → **选一台写回 nodeName** | Pod 一直 `Pending`，Event `FailedScheduling` |
| 7 | kubelet（该节点） | watch 到"Pod 归我"→ 通过 CRI 调 containerd **拉镜像** | `ImagePullBackOff` / `ErrImagePull` |
| 8 | kubelet | 创建容器 → 调 CNI 配网络 → 执行探针 | `CrashLoopBackOff` / `Running 0/1` |
| 9 | 各 controller | 读 Pod 的 status，聚合进 Deployment 的 `status`，**`rollout status` 就是读这里** | 进度停滞、`progress deadline exceeded` |

> **这张表就是排障地图**：第 7 步挂 → ImagePullBackOff；第 6 步挂 → Pending；第 8 步探针不过 → Running 0/1。**先定位卡在哪一步，再修那一步。**

### 2.4 关键参数与默认值（记住这些，面试高频）

| 参数 | 默认 | 含义 | 实务要点 |
|---|---|---|---|
| `Deployment.progressDeadlineSeconds` | **600** | 多久算"这次发布失败" | `rollout status` 就是干等这个时间，**它本身不排查任何问题** |
| `Deployment.maxSurge` | 25% | 更新时最多多出几个 Pod | 1 副本时 = 1（向上取整），所以**单副本发布会短暂跑 2 个** |
| `Deployment.maxUnavailable` | 25% | 更新时最多有几个不可用 | 1 副本时 = 0（向下取整），所以**必须先起新的再删老的** |
| `Service.clusterIP` | 自动分配 | 虚拟 IP | `None` = Headless，直接返 Pod IP |
| `Service.nodePort` 范围 | 30000-32767 | NodePort 类型占用 | 生产慎用，端口固定易冲突 |
| `HPA.syncPeriod` | 15s | HPA 多久算一次 | 加上采集周期，扩容反应 1-2 分钟 |
| `HPA.stabilizationWindowSeconds`（缩容） | **300s** | 缩容前要观察多久 | **故意不对称**：扩要快、缩要慢 |
| `HPA.stabilizationWindowSeconds`（扩容） | **0s** | 扩容无窗口 | 同上 |
| `kubelet` 探针默认 | period 10s / failure 3 次 | | 失败 3 次才判死 |
| `terminationGracePeriodSeconds` | **30s** | 收到删除信号后等多久再强杀 | 配合 `preStop` 做优雅停机 |

**QoS 三档**（调度与驱逐的依据）：

| QoS | 条件 | 驱逐顺序 |
|---|---|---|
| BestEffort | 没设任何 request/limit | **最先被驱逐** |
| Burstable | 有 request 但小于 limit，或只设了 limit | 中间 |
| Guaranteed | **所有容器的所有资源，requests == limits** | **最后被驱逐** |

> 生产上的应用（尤其核心服务）应当做到 **Guaranteed**——节点资源紧张时，你的 Pod 不会被第一个杀掉。

---

## 3. 动手

### 3.1 kind 起一个本地集群

**为什么用 kind**：不装 kubeadm、不搞证书、不耗钱，一条命令就有真集群。**它是学 K8s 唯一正确的起点**——先在本地跑通，生产那套（kubeadm 或 ACK）只是换了个"节点从哪来"。

```bash
# ① 创建集群（节点名默认 <cluster>-control-plane）
kind create cluster --name day2

# ② 确认控制面通
kubectl cluster-info --context kind-day2

# ③ 看节点（期望 STATUS=Ready，ROLES=control-plane）
kubectl get nodes
```

**看到什么算对**：
```
NAME                       STATUS   ROLES           AGE   VERSION
day2-control-plane         Ready    control-plane   30s   v1.37.0
```

**kind 的三个必知限制**（很多人栽在这）：
1. 集群是**单节点** → 所有 Pod 都挤在一台机器上，调度相关的实验（污点、亲和性）做不了
2. 节点是**容器** → 节点内没有你宿主机的 docker 凭据，拉私有镜像会 401（见第 6 节）
3. 组件镜像是预打包的，**版本随 kind 走**，不是你能随意切的

### 3.2 第一个 Deployment

```bash
mkdir -p ~/devops-day2 && cd ~/devops-day2
```

写 `go-demo.yaml`：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: day1-go
  labels:
    app: day1-go
spec:
  replicas: 1
  selector:
    matchLabels:
      app: day1-go
  template:
    metadata:
      labels:
        app: day1-go
    spec:
      containers:
      - name: day1-go
        image: registry.cn-hangzhou.aliyuncs.com/<你的仓库>/<你的镜像>:<tag>
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 8080
        resources:
          requests:
            cpu: "100m"
            memory: "128Mi"
          limits:
            cpu: "500m"
            memory: "256Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: day1-go-svc
spec:
  selector:
    app: day1-go
  ports:
  - port: 80
    targetPort: 8080
  type: ClusterIP
```

```bash
# ① 语法自检（不碰集群）—— 写 yaml 必备的习惯
kubectl apply -f go-demo.yaml --dry-run=client

# ② 实际创建
kubectl apply -f go-demo.yaml

# ③ 看状态
kubectl get pods -l app=day1-go

# ④ 看协调过程（看 Events，这是排障的固定动作）
kubectl describe pod -l app=day1-go
```

### 3.3 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| Pod 起来了 | `kubectl get pods -l app=day1-go` | `1/1 Running` |
| Service 挂上后端 | `kubectl get endpoints day1-go-svc` | 有 `IP:PORT`，**空的就是没匹配上** |
| Deployment 状态 | `kubectl get deploy day1-go -o yaml` 的 `status:` | `readyReplicas: 1` |
| Service 能通 | 见下方端口转发 | 返回应用响应 |

```bash
# Service 访问验证（ClusterIP 只能集群内访问，从宿主机要转发）
kubectl port-forward svc/day1-go-svc 18080:80
# 另开终端
curl http://127.0.0.1:18080/
```

> **为什么 Service 要单独验**：`kubectl get pods` 显示 Running **不代表流量能到**。Service 的 selector 写错 → endpoints 为空 → 访问 503，但 Pod 一切正常。**这是"应用正常但服务不通"最常见的原因。**

---

## 4. 资源清单逐字段

### 4.1 Deployment

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion: apps/v1` | string | ✅ | API 组与版本 | 写 `extensions/v1beta1` → **该版本早已废弃，报错** |
| `kind: Deployment` | string | ✅ | 对象类型 | 拼错 → `no matches for kind` |
| `metadata.name` | string | ✅ | 对象名 | 重复 → `AlreadyExists` |
| `spec.replicas` | int | | 期望副本数，**HPA 会改它** | — |
| `spec.selector.matchLabels` | map | ✅ | **选哪些 Pod 归我管** | 与 template 标签不一致 → `apply` 直接报错 |
| `spec.template.metadata.labels` | map | ✅ | 打给 Pod 的标签 | 必须与 selector **完全一致** |
| `spec.template.spec.containers[]` | list | ✅ | 容器定义 | — |
| `.name` | string | ✅ | 容器名 | 同一 Pod 内不可重名 |
| `.image` | string | ✅ | 镜像地址 | 私有仓库无凭据 → `ImagePullBackOff` |
| `.imagePullPolicy` | enum | | `IfNotPresent`(默认 for 非 latest) / `Always` / `Never` | `Always` + 私有仓库 → 每次都 401 |
| `.ports[].containerPort` | int | | **声明**容器监听端口（不限制） | 与 Service 的 targetPort 对不上 → 访问不通 |
| `.resources.requests.cpu` | Quantity | | **调度依据 + HPA 分母** | 漏写 → HPA `<unknown>/50%` |
| `.resources.limits.cpu` | Quantity | | 上限，超了被节流（**不杀容器**） | 太低 → 采集值顶到 limit → **分母是 requests 时利用率反而偏高、照样扩但每个新 Pod 同样被限死**；真正「有值但不扩」的是分母被撑大 |
| `.livenessProbe` | Probe | | 失败 → **重启容器** | 探针写错 → 容器反复重启 |
| `.readinessProbe` | Probe | | 失败 → **摘出 Service 端点，不重启** | 探针太严 → 一直 0/1，流量进不来 |
| `.startupProbe` | Probe | | 启动慢的应用用它，成功后才启用其他探针 | 不用 → 慢启动应用被 liveness 误杀 |

**关于 requests 与 limits 的精确规则**（本地 `kubectl explain` 实测原文）：

> Requests describes the minimum amount of compute resources required. **If Requests is omitted for a container, it defaults to Limits if that is explicitly specified**, otherwise to an implementation-defined value. Requests cannot exceed Limits.

翻译成可执行结论：
1. **两个都不写** → 用实现默认值，HPA 拿不到分母 → `<unknown>/50%`
2. **只写 limits** → requests 退化成 limits 的值 → HPA 能算，但**分母偏大、利用率永远偏低、几乎不扩**
3. **只写 requests** → 正常，**这是 HPA 场景的最小正确写法**
4. **requests > limits** → **非法**，apply 直接报错

> 面试能这么答，比"记得加 requests.cpu"高一个档：**"我们只写 requests 不写 limits 的场景是 HPA，limits 会把利用率压低导致不扩容；生产上为了 Guaranteed QoS 会两者写相等。"**

### 4.2 Service

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `spec.selector` | map | | 选后端 Pod | 不匹配 → endpoints 空 → 503 |
| `spec.ports[].port` | int | ✅ | Service 自己的端口 | — |
| `spec.ports[].targetPort` | int/str | | 后端容器的端口 | 写错 → 连不上容器 |
| `spec.type` | enum | | ClusterIP(默认)/NodePort/LoadBalancer/ExternalName | — |
| `spec.clusterIP: None` | string | | **Headless**：不给 VIP，直接返 Pod IP 列表 | StatefulSet 必须配它 |
| `metadata.annotations` | map | | 各种控制器专用注解 | 键写错 → 注解不生效，**不报错** |

**Service 的 DNS 名字**（跨命名空间访问必考）：

```
<service>.<namespace>.svc.cluster.local
例：day1-go-svc.default.svc.cluster.local
    短写：day1-go-svc（仅同 ns） / day1-go-svc.other-ns
```

### 4.3 label 与 selector 的匹配规则

| 规则 | 说明 |
|---|---|
| **集合相等** | selector 是**等值匹配**，不是"包含"。`env=prod` 只匹配 `env=prod`，不匹配 `env=prod,team=a` |
| **支持集合运算符** | `in`、`notin`、`exists`、`!exists` ⚠️ **别与 PromQL 混**：K8s label selector 有 `in`/`notin`，但 **PromQL 的集合运算符只有 `and`/`or`/`unless` 三个，没有 `in`/`notin`**（那是 SQL/InfluxQL 语法） |
| **Deployment 的 selector 创建后不可改** | 想改只能删了重建 —— **这是生产变更的雷区** |
| **Service 的 selector 可以改** | 但会导致流量瞬间重定向 |

```bash
# 列出所有带某标签的 Pod
kubectl get pods -l app=day1-go
kubectl get pods -l 'env in (prod,staging)'
kubectl get pods -l '!app'                    # 没有 app 标签的
```

---

## 5. 横向对比

### 5.1 五种工作负载怎么选

| 工作负载 | 提供什么 | 典型场景 | 关键字段 |
|---|---|---|---|
| **Deployment** | 多个**无状态**副本 + 滚动更新 | Web 服务、API | `replicas`、`strategy` |
| **StatefulSet** | 稳定主机名 + 稳定存储 + **有序启停** | MySQL、Kafka、ZooKeeper | `serviceName`、`volumeClaimTemplates` |
| **DaemonSet** | 每个节点一个副本 | 日志采集、监控 agent、CSI | `nodeSelector` |
| **Job** | 跑完就结束，**必须成功完成** | 数据迁移、备份 | `completions`、`backoffLimit` |
| **CronJob** | 按 cron 定时跑 | 定时备份、报表 | `schedule`、`concurrencyPolicy` |

**选型口诀**：**无状态选 Deployment，有状态选 StatefulSet，每台都要选 DaemonSet，跑一次选 Job，定时跑选 CronJob。**

**StatefulSet 与 Deployment 的三处关键差别**（面试高频）：

| 维度 | Deployment | StatefulSet |
|---|---|---|
| Pod 名 | 随机（`day1-go-798494c599-pk7dx`） | **有序**（`mysql-0`、`mysql-1`） |
| 存储 | 共享 PVC 或无 | **每 Pod 独占 PVC**（`volumeClaimTemplates`） |
| 启停顺序 | 随机 | **按序号逆序创建、正序销毁**，且前一个 Ready 才起下一个 |
| 网络 | 走 Service VIP | 需配 **Headless Service**（`clusterIP: None`）拿到稳定 DNS |

### 5.2 四种 Service 类型怎么选

| 类型 | 集群内 | 集群外 | 典型用途 | 代价 |
|---|---|---|---|---|
| **ClusterIP** | ✅ | ❌ | 内部服务互访（默认） | 外部进不来 |
| **NodePort** | ✅ | ✅（节点IP:30000+） | 裸机环境、无云 LB | 每节点占一个端口，**生产慎用** |
| **LoadBalancer** | ✅ | ✅（云厂商 SLB/ALB） | 云上对外暴露 | 依赖云厂商，会产生费用 |
| **Headless**（`clusterIP: None`） | ✅ | ❌ | 有状态服务直连 Pod、客户端负载均衡 | 无负载均衡、无 VIP |
| ExternalName | 仅 DNS | — | 指向集群外的 CNAME | 不代理流量 |

> **本地民企的真实选型**：对外走 **ACK 的 ALB Ingress**（七层）或 CLB（四层）；集群内一律 ClusterIP；**NodePort 只在测试环境用**。面试时能说清"为什么生产不用 NodePort"（端口固定易冲突、每节点暴露、无健康检查联动）就是加分项。

---

## 6. 踩坑与排障（症状 → 状态 → 原因）

**排障铁律：永远往下挖到第三层。** `rollout status` 的超时、`get pods` 的状态，都是第一层。

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `exceeded its progress deadline` | — | **只是"等够了没等到"，零信息量** | 别在这里浪费时间，去看 Pod | 记住这报错没有诊断价值 |
| `0 of 1 updated replicas are available` | `ImagePullBackOff` | 拉不到镜像：401 无凭据 / 404 名字或 tag 错 | `docker pull` 先验证；kind 用 `kind load docker-image` | CI 里加"镜像可拉通"的前置检查 |
| 同上 | `CrashLoopBackOff` | 容器起来就退出 | `kubectl logs <pod> --previous` 看上一次崩的日志 | 加 `startupProbe`；检查配置注入 |
| 同上 | `Pending` | 调度不上：资源不够 / 亲和性不匹配 / 有污点无容忍 | `describe` 看 `FailedScheduling` 的原文 | 配 requests、资源预留、污点容忍 |
| 同上 | `Running 0/1` | 探针没过（应用慢或端口错） | `describe` 看 Probe 那一行；`logs` 看应用是否监听 | 加 `startupProbe`；改端口 |
| 访问 503 | Pod 都正常 | **Service 的 selector 没匹配上** | `kubectl get endpoints <svc>`，空就是它 | 部署后必查 endpoints |
| `apply` 报 `selector does not match template labels` | — | Deployment 的 selector 与 template 标签不一致 | 两处改成一致 | 模板复用，别手抄 |
| `apply` 报 `no matches for kind` | — | apiVersion/kind 写错或版本废弃 | `kubectl api-resources` 查真实值 | 用 `kubectl explain` 确认 |
| HPA 永远 `<unknown>/50%` | — | **requests.cpu 没写**（或两个都没写） | 补 `requests.cpu` | CI 加 policy 校验必填字段 |
| HPA 有值但扩了没用 | — | `limits.cpu` 太小被 cgroup 节流（**不杀容器**），采集值顶到 limit，每个新 Pod 同样被限死 | 提高 limit；或改用 QPS 等业务指标 | 监控 throttling 指标 |
| Pod 反复重建 | `CrashLoopBackOff` | 探针太严，liveness 误杀 | 放宽 `initialDelay` 或改用 startupProbe | 慢启动应用一律加 startupProbe |

**排障标准动作（背下来）**：

```bash
kubectl get pods                        # 1. 定性
kubectl describe pod <name>             # 2. 看 Events 找卡点
kubectl logs <name> --previous          # 3. 崩溃才用
kubectl get events --sort-by=.lastTimestamp   # 4. 跨对象时间线
```

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | kubernetes.io/zh-cn/docs/concepts/ | 工作负载、Service、配置、存储 | 见 `09-参考索引/` |
| S2 | 官方 | kubernetes.io/docs/reference/using-api/ | 资源清单字段语义 | 见 `09-参考索引/` |
| S3 | 官方 | kubernetes.io/docs/tasks/manage-resources- | requests/limits 与 QoS | 见 `09-参考索引/` |
| S4 | 官方 | kubernetes.io/docs/concepts/cluster-administration/ | 组件架构、API 机制 | 见 `09-参考索引/` |
| S5 | 中文 | docs.docker.com/zh-cn/ | 镜像与容器基础（L1 衔接） | 见 `09-参考索引/` |
| S6 | 云厂商 | help.aliyun.com（ACK / ACR） | 托管版差异、ALB Ingress、ACR 拉取 | 见 `09-参考索引/` |

> **完整来源矩阵（含全部 14 个组件）见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。** 本篇只列用到的。
> **本篇的字段语义以 `kubectl explain` 实测输出为准**（本地 v1.37.1），官方文档用于交叉验证。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— `kubectl apply` 的输入是 yaml，输出是"集群里有一组满足期望态的 Pod"；中间经过 apiserver（认证授权准入）→ etcd（记账）→ controller（催活）→ scheduler（派活）→ kubelet（干活）。
2. **能故意改坏再倒推吗？** —— 三个实验：① 删掉 `requests.cpu` → 看 HPA 变 `<unknown>/50%`；② 把 `selector` 改成不匹配的标签 → 看 apply 报错；③ 把 `imagePullPolicy` 改成 `Always` → 看它开始反复拉镜像。**造过的错比看过的文档记得牢。**
3. **能画上下游吗？** —— 画出 `kubectl → apiserver → etcd → controller → scheduler → kubelet → containerd → Pod` 这条链，并在每两个环节之间标出"这一步失败会看到什么状态"。

---

**下一篇**：[`L3-Deployment与滚动更新`](L3-Deployment与滚动更新.md) · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md)
