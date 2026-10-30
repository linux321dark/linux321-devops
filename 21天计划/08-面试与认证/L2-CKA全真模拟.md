# L2 · CKA 全真模拟（限时环境 + 12 题卷 + 复盘闭环）

> **本篇定位**：把 CKA 变成**可重复的训练流水线**：搭环境 → 限时做卷 → 判分 → 复盘 → 回到对应篇目补。
> **前置**：[`L1-CKA知识点速查.md`](L1-CKA知识点速查.md)（命令底座与排障地图）· [`../02-K8s核心/L2-K8s架构与对象模型.md`](../02-K8s核心/L2-K8s架构与对象模型.md)（对象模型）。
> **学完能做什么**：① 在自建多节点 kind 集群里**预置可复现的故障**；② 在限时内完成一份覆盖排障/存储/工作负载/维护的模拟卷；③ 用复盘表定位自己的薄弱知识域，并给出下一步刷题动作。
> **在 21 天里**：与 D18（CKA 技巧 + kubectl 速查）连着做，D18 打基础、本篇做限时全真。
> **环境/版本基准**：本地 K8s **v1.37.x**，kind 集群名 `day2`，节点 `day2-control-plane`（多节点配置为 `day2-worker-1/2/3`），镜像仓库前缀 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`。
> **写作纪律**：**本文所有环境搭建与故障预置均只给步骤，本篇写作过程中未执行任何 kubectl / docker / kind 命令，未拉取任何镜像，未创建或修改任何集群资源。**
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
CKA 全真模拟
│
├─ 1. 概念
│   ├─ 1.1 模拟要模拟什么（考的是路径，不是答案）
│   ├─ 1.2 三层训练环境：kind / kubeadm / 真机
│   ├─ 1.3 什么能练、什么练不了（能力边界表）
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 模拟环境搭建方案（只写步骤）
│   │   ├─ 2.1.1 多节点 kind 配置
│   │   ├─ 2.1.2 附加组件（metrics-server / ingress-nginx / Calico）
│   │   ├─ 2.1.3 预置故障脚本（故意写坏）
│   │   └─ 2.1.4 一键重置
│   ├─ 2.2 限时模拟的策略（先扫全卷，再排序）
│   ├─ 2.3 题型分类与配比（经验判断，非官方数据）
│   └─ 2.4 每类题的解题模板
│
├─ 3. 动手（完整模拟卷）
│   ├─ 3.1 考试须知与时间预算
│   ├─ 3.2 模拟卷题目表（12 题）
│   └─ 3.3 参考答案要点 + 评分点 + 常见失分
│
├─ 4. 逐字段表
│   ├─ 4.1 模拟卷题目总表
│   └─ 4.2 失分统计复盘表
│
├─ 5. 横向对比
│   ├─ 5.1 刷题 vs 动手实验 vs 真机练习
│   └─ 5.2 模拟环境的替代方案对比
│
├─ 6. 踩坑与排障（症状 → 状态 → 原因 → 修 → 防）
├─ 7. 参考来源
├─ 8. 自测三问
└─ 附：待验证清单 + 每日训练计划
```

---

## 1. 概念

### 1.1 模拟要模拟什么：考的是路径，不是答案

CKA 的题目形态可以概括成一句话：

> **给一个现象（可能只有一句话），要求你在集群里定位原因并修好，评分看的是"你做了什么"和"最终是否达到期望态"。**

这意味着模拟卷的判分**不能只看"Pod 是否 Running"**。一条常见的评分路径是：

| 评分关注点 | 合格的答案长什么样 | 不合格的答案长什么样 |
|---|---|---|
| **定位准确性** | 指出问题在哪个对象、哪个字段 | "重启一下就好了" |
| **动作正确性** | 命令参数正确、作用域正确 | 命令能跑但改错了对象 |
| **最终状态** | 期望态达成（副本数、端点、绑定状态） | 靠反复删除重建凑巧成功 |
| **副作用** | 没破坏无关资源 | 顺手改坏了别的东西 |

> **由此得出模拟训练的核心指标**：**"路径完整度"**，不是"做完没做完"。一道题你用非标准路径做对了，但说不出为什么这样改，下次换个现象还是不会 —— **那这道题等于没练。**

### 1.2 三层训练环境

| 层级 | 环境 | 能练什么 | 成本 | 适合阶段 |
|---|---|---|---|---|
| **L1 · kind 多节点** [S15] | 本地 Docker 里跑真集群 | 绝大多数 Pod/Service/网络/调度/存储题；**etcd 备份恢复受限** | 低（分钟级重建） | **D18-D19 主战场** |
| **L2 · kubeadm 虚拟机/云主机** | 一台装 kubeadm 的机器 | **etcd 备份恢复、静态 Pod、证书轮转、控制面排障** | 中 | **D19 必做一次** |
| **L3 · 真机/ACK** | 生产或托管集群 | 容量规划、真实流量行为、跨节点网络 | 高、有风险 | 有机会时做，**不要拿生产练手** |

**决策建议（你的实际情况）**：你的日常是 D18-D19 之间，**kind 多节点能覆盖 80% 以上的题型**，剩下 20%（etcd 恢复、证书、静态 Pod）**必须借一台 kubeadm 虚拟机**。这是本篇环境方案分两档的原因。

### 1.3 什么能练、什么练不了（能力边界表）

**这张表决定你的模拟卷该怎么判分** —— 在 kind 上"做不出来"不等于"不会"。

| 题型 | kind 多节点能否练 | 原因 | 替代练法 |
|---|---|---|---|
| Pod 各状态排障（Pending / CrashLoop / ImagePull / OOM） | ✅ 能 | 与生产同机制 | — |
| Service / Ingress / NetworkPolicy [S8][S9] | ✅ 能（**NetworkPolicy 需换 Calico**） | kind 默认 kindnet 不支持策略 | 先装 Calico |
| 调度约束（污点、亲和、资源不足） | ✅ 能 | **多节点配置下与生产同机制** | 必须多节点 |
| PV/PVC 绑定与挂载 | ⚠️ 部分 | kind 默认 StorageClass 是 `standard`（hostpath），**动态制备可用，但多挂载/拓扑约束受限** | 用 hostPath PV 练静态绑定 |
| HPA [S12] | ✅ 能（**先装 metrics-server**） | 与生产同机制 | — |
| **etcd 备份与恢复** | ❌ **基本不能** | kind 的 etcd 在**控制面容器内**，路径与静态 Pod 目录都不一样 | **必须借 kubeadm 机器** |
| **静态 Pod / 控制面组件排障** | ❌ 不能 | 同上 | kubeadm 机器 |
| **证书轮转与 CSR 完整流程** | ⚠️ 部分 | CSR 对象可建可批，**但 kubelet 轮转链路不完整** | kubeadm 机器 |
| **LoadBalancer 类型 Service 的真实 LB** | ❌ 不能 | **kind 没有云厂商控制器** | 理解原理即可，别背 LB 细节 |
| **真实流量与容量压测** | ❌ 不能 | 节点是容器，资源上限受宿主限制 | 用 kind 压测只验证"行为"，不验证"性能" |
| **云厂商特性**（ACK ALB Ingress、ACR 凭据） | ❌ 不能 | 无云环境 | 对照 `help.aliyun.com/zh/ack/user-guide/` 理解 |

> **重要提醒**：不要把"在 kind 上做不出来"记成"我这项不会"。**用 1.3 这张表给每次失败做归因分类**（A 类=真不会 / B 类=环境不支持 / C 类=命令写错）—— **只有 A 类才是要补的知识点**。这个分类直接决定第 4.2 节复盘表的字段设计。

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Simulation / Mock exam | 模拟卷 | 自制或第三方的限时练习卷 |
| Time budget | 时间预算 | 总时长与每题的分配 |
| Scoring rubric | 评分标准 | 判分依据，通常拆成多个得分点 |
| Fault injection | 故障注入 | 故意制造故障来练排障 |
| Golden path | 标准路径 | 官方/社区公认的解法 |
| Idempotent | 幂等 | 重复执行结果一致 |
| Cascade | 级联删除 | 删父对象时对子对象的影响 |
| Rehearsal | 预演 | 正式操作前的彩排 |
| Reset | 重置 | 把环境恢复到已知状态 |
| Kill switch | 急停 | 一次命令把整个环境推倒重来 |

---

## 2. 机制

### 2.1 模拟环境搭建方案（只写步骤，不在写作环境执行）

#### 2.1.1 多节点 kind 集群

```yaml
# cka-lab/kind-multinode.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    # 集群名 day2 → 节点名 day2-control-plane
  - role: worker
  - role: worker
  - role: worker
```

```bash
# 创建（不要与已有的 day2 单节点集群同名，会冲突）
kind create cluster --name cka-lab --config cka-lab/kind-multinode.yaml

# 确认 4 个节点
kubectl get nodes -o wide
# 期望：1 个 control-plane + 3 个 <none> 角色的 worker，STATUS 全 Ready
```

**为什么必须多节点（再说一遍，因为它决定整份卷的可行性）**：
- 单节点集群里，**"资源不足导致 Pending" 根本造不出来**（所有 Pod 都在同一台"机器"上，永远够）；
- **污点、亲和性、节点维护（drain）**这三类题会全部失效；
- 而这三类恰好是 CKA 的高频考点。**用单节点 kind 练出来的"会用 drain"是假的。**

> **命名建议**：**不要复用 `day2`**，用独立名字（如 `cka-lab`），避免与日常学习集群互相污染。本篇跨篇口径里的 `day2` 指的是学习集群；模拟环境是**隔离的另一个集群**。

#### 2.1.2 附加组件（三种，按需装）

| 组件 | 用来练什么 | 装它是因为 | 不装会怎样 |
|---|---|---|---|
| **metrics-server** | HPA 计算、Pod/节点资源用量 | **没有它 HPA 永远 `<unknown>/50%`** | HPA 相关题全废 |
| **Calico**（替换 kindnet） | NetworkPolicy | **kind 默认的 kindnet 不支持策略** | 策略题写了也不生效，会误判自己不会 |
| **ingress-nginx** | Ingress 路由 | Ingress 本身只是配置，**没有控制器就没人转发** | Ingress 题只能验证规则对象，不能验证访问 |

```bash
# ① metrics-server —— 官方 manifest（先下载到本地，无外网环境用本地文件）
kubectl apply -f metrics-server.yaml

# ② Calico —— 替换默认 CNI
kubectl apply -f calico.yaml
kubectl -n kube-system get pods -l k8s-app=calico-node
# 期望：全部 Running，等它 Ready 再往下做

# ③ ingress-nginx
kubectl apply -f ingress-nginx.yaml
kubectl -n ingress-nginx get pods
```

> **无外网考场提示**：**所有 manifest 提前下载到本地**。真实考试环境通常不保证外网连通，**依赖在线拉取 manifest 的习惯会直接烧掉整段答题时间**。这是第 6 节"考试环境无外网"这条坑的日常体现。

#### 2.1.3 预置故障脚本（故意写坏）

**设计原则**：故障脚本要**可重入、可一键清除、每个故障只对应一个考点**。

```bash
#!/usr/bin/env bash
# cka-lab/inject-faults.sh
# ⚠️ 本脚本为书面方案，未在写作环境执行；执行前确认当前 context 是 cka-lab
set -euo pipefail

NS="${NS:-cka-lab}"
IMG="registry.cn-hangzhou.aliyuncs.com/linux321-devops/day2-go:latest"

# ── 故障 1：Deployment 的 selector 与模板标签不一致 → apply 直接报错 ──
kubectl -n "$NS" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fault-selector
spec:
  replicas: 2
  selector:
    matchLabels:
      app: fault-selector
  template:
    metadata:
      labels:
        app: fault-selector-typo   # ← 故意不匹配
    spec:
      containers:
        - name: app
          image: $IMG
EOF

# ── 故障 2：Service 的 selector 指向不存在的标签 → endpoints 为空 → 访问 503 ──
kubectl -n "$NS" apply -f - <<EOF
apiVersion: v1
kind: Service
metadata:
  name: fault-svc
spec:
  selector:
    app: no-such-app          # ← 故意不匹配
  ports:
    - port: 80
      targetPort: 8080
EOF

# ── 故障 3：liveness 探针指向不存在的端口 → Running 0/1 → CrashLoopBackOff ──
kubectl -n "$NS" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fault-probe
spec:
  replicas: 1
  selector:
    matchLabels:
      app: fault-probe
  template:
    metadata:
      labels:
        app: fault-probe
    spec:
      containers:
        - name: app
          image: $IMG
          ports:
            - containerPort: 8080
          livenessProbe:
            httpGet:
              path: /healthz
              port: 9999        # ← 故意指错端口
EOF

# ── 故障 4：引用不存在的 ConfigMap → CreateContainerConfigError ──
kubectl -n "$NS" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fault-config
spec:
  replicas: 1
  selector:
    matchLabels:
      app: fault-config
  template:
    metadata:
      labels:
        app: fault-config
    spec:
      containers:
        - name: app
          image: $IMG
          envFrom:
            - configMapRef:
                name: missing-configmap   # ← 故意不存在
EOF

# ── 故障 5：PVC 容量超出 StorageClass 能提供的量 → PVC Pending ──
kubectl -n "$NS" apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: fault-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 500Gi        # ← 故意远超容量
EOF
```

**一键清除（急停开关）**：

```bash
#!/usr/bin/env bash
# cka-lab/reset-lab.sh
kubectl delete namespace cka-lab --wait=false
# 或者只删故障资源
kubectl -n cka-lab delete deploy,fault-svc,fault-probe,fault-config,pvc \
  --ignore-not-found
```

> **设计要点**：故障 1 是**"apply 就报错"**类（考"期望态写错"）；故障 2 是**"Pod 正常但服务不通"**类（考"应用正常 ≠ 流量能到"）；故障 3 是**"容器起不来"**类；故障 4 是**"配置缺失"**类；故障 5 是**"存储不绑定"**类。**五类正好对应 L1 第 4.2 节症状速查表的前五行。**

#### 2.1.4 一键重置（训练流水线的关键）

训练的价值在**反复做同一道题**，所以重置必须**快**（理想 ≤ 2 分钟）。

| 方案 | 重置耗时 | 适用 |
|---|---|---|
| 删命名空间重建 | 1-2 分钟 | 绝大多数题 |
| `kind delete cluster` 重建 | 3-5 分钟 | 环境被改坏时 |
| **保留集群，只删故障资源** | **秒级** | **推荐**：日常练题用这个 |

### 2.2 限时模拟的策略

**核心认知**：**限时考的从来不是"会不会"，而是"会不会在压力下选对第一步"。** 所以策略必须先解决"时间分配"，再解决"技术难度"。

#### 第一步：开卷后 10 分钟内，扫完全卷并分类

**不要从第一题开始做。** 开卷先做两件事：

```bash
# ① 确认环境与上下文（10 秒）
kubectl config current-context
kubectl get nodes
kubectl get pods -A

# ② 扫完全卷，给每题打标签
```

**分类打标签表**（自己在卷子上标）：

| 标签 | 含义 | 处理顺序 |
|---|---|---|
| **A · 会做** | 知道命令，知道考什么 | **立刻做**（这类题是送分题，别浪费） |
| **B · 会做但慢** | 知道怎么做但命令记不熟 | 做，但**记录耗时**（耗时长的说明要练命令） |
| **C · 不会但能查** | 知道该查哪个字段，但不确定命令 | 做，**允许查文档**，**记录查了什么** |
| **D · 完全不会** | 不知道从哪下手 | **跳过，做标记** |

#### 第二步：按"分值密度"排序做题

**不是按题号做，是按"单位时间能拿到的分数"做。**

| 优先级 | 题型特征 | 理由 |
|---|---|---|
| **1** | 单命令解决、验证快的题（改副本数、加标签、创建资源） | 验证快 = 反馈快 = 敢连续做 |
| **2** | 两三步的排障题（看 Events → 改字段 → 验证） | 中等投入 |
| **3** | 多步链路的题（etcd 备份、证书签发、多资源协作） | 投入大、易卡住，**留到最后** |
| **4** | D 类（完全不会） | **不要硬啃**，占时间的边际收益最低 |

> **一个可执行的规则**：**连续两题卡住超过 5 分钟 → 立刻跳过标记，继续下一题，最后再回来。** 沉没成本是限时考试最大的杀手。

#### 第三步：卡住时的固定动作（不是随便翻文档）

```
① 我现在在查什么？ → 说出目标（"我要知道 Pod 为什么没调度"）
② 查哪个字段？   → Events / status.conditions / spec
③ 用哪条命令？   → describe / get -o yaml / logs --previous
④ 还不清楚 → 查官方文档的哪一节？（写下来）
```

> **最后一步（写下来）是最有价值的**：复盘时你才知道**你缺的是知识还是命令**，两者的补法完全不同。**缺知识 → 回 L1 的对应知识域；缺命令 → 回 L1 的 4.1 速查表死记。**

#### 关于时间预算的说明

> **⚠️ 官方考试的总时长、题量与及格线以官方当前政策为准**（见 S1 官方认证页），**本篇不写具体数值**，也不按第三方页面的数字排练。
> **可迁移的做法**：把官方公布的总时长按比例分配到"扫卷时间 / A 类题 / B、C 类题 / D 类题"四个桶，**并用你的第一次模拟实测数据来校准这个比例**。**比例是你的经验，不是官方的规定。**

### 2.3 题型分类与配比

> **⚠️ 以下配比是经验判断（来自社区备考反馈与题目形态分析），不是官方公布的数据。官方知识域与配比以 S1 页面为准。**

| 题型 | 经验占比 | 典型问法 | 对应知识域 | 本篇题号 |
|---|---|---|---|---|
| **排障类** | **最大头** | "这个 Pod 为什么起不来""服务访问不通""节点不健康" | 工作负载 + 网络 + 存储 + 节点 | Q1, Q2, Q3, Q9, Q10 |
| **存储与配置** | 较重 | "让 Pod 用上持久化卷""把配置改成从 ConfigMap 读" | PV/PVC/SC + ConfigMap/Secret | Q4, Q5 |
| **工作负载与调度** | 中等 | "部署 3 副本""限制只能调度到某节点""改滚动策略" | Deployment/StatefulSet + 调度 | Q6, Q7 |
| **集群维护** | 中等 | "备份 etcd""节点维护""签发证书" | etcd + 节点管理 + 证书 | Q8, Q11, Q12 |
| **RBAC / 授权** | 较小但易失分 | "让某个 SA 能读某资源""这个操作允许吗" | RBAC | Q9 |
| **网络专项** | 中等 | "写 NetworkPolicy""配 Ingress 路由" | Service/Ingress/NP | Q2, Q10 |

> **练题配比建议**：按上表的顺序分配训练时间，**排障类至少占一半**。理由见 L1 第 1.3 节。

### 2.4 每类题的解题模板

#### 模板 A · 排障题（通用性最强）

```
① 看症状        → kubectl get <res> -n <ns>
                  确认状态列到底是什么（Pending? CrashLoop? 0/1? Unknown?）

② 定位层级      → 这是 Pod 层？节点层？还是集群层？
                  单点异常 → Pod 层；多点异常 → 节点层；大面积 → 集群层

③ 看状态        → kubectl describe <res> <name> -n <ns>
                  重点看 Events（最后 5 条）与 status.conditions

④ 看证据        → kubectl logs <name> -n <ns> --previous
                  只有"应用正常但业务不对"时才需要 exec 进去看

⑤ 验证假设      → 提出一个具体原因，先验证再改
                  例："Service selector 与 Pod 标签不匹配"
                  验证：kubectl get endpoints <svc> -n <ns>   （空 → 假设成立）

⑥ 改 + 验证     → 改完立刻重新 get / get endpoints / get pvc 看状态变化
```

> **为什么第 ⑤ 步必须单独列出来**：很多人是"想到什么改什么"。**先验证再改**能让你在改错时立刻知道假设不成立，而不是连改三个地方再回头查。

#### 模板 B · 存储题

```
① 先判 Pod 状态
   Pending            → 可能 PVC 没绑定
   ContainerCreating  → 可能挂载失败
   Running 但数据没落盘 → 可能挂载到了临时目录（emptyDir）

② 看 PVC → PV → StorageClass
   kubectl get pvc <p> -n <ns>
   kubectl describe pvc <p> -n <ns>      ← Events 写明原因
   kubectl get pv
   kubectl get storageclass

③ 四个原因逐个排
   ① 有没有匹配的 PV？          （PV 的 capacity/accessModes/claimRef 对不对）
   ② StorageClass 存在吗？      （dynamic provisioning 的前提）
   ③ accessModes 匹配吗？       （RWO 不能被两个 Pod 同时挂，RWX 才能）
   ④ 容量够吗？                 （PVC 请求 > SC 能力 → Pending）

④ 看 Events 里的原文
   "no persistent volumes available for this claim"
   "waiting for a volume to be created"
   "failed to allocate volume"  ← 这条要往下查 provisioner
```

> **高频失分点**：看到 PVC Pending 就去删 Pod。**PVC 的绑定是独立于 Pod 的生命周期** —— 删 Pod 不会让它绑定上。

#### 模板 C · 工作负载题

```
① 先判期望态对不对（这是最容易被跳过的第一步）
   - selector 与 template.labels 是否一致？
   - replicas 是否符合要求？
   - 镜像、command、args 是否对？
   - 滚动策略（maxSurge/maxUnavailable）是否满足题目？
   → 不一致就是"期望态写错"，改 yaml 即可，不需要查集群

② 再判协调是否卡住（期望态对了但没达成）
   - RS 出来了吗？      kubectl get rs -n <ns>
   - Pod 出来了吗？     kubectl get pods -n <ns>
   - 卡在哪一步？        describe pod 看 Events
     · 没 RS          → controller 没工作（看 controller-manager）
     · 有 RS 没 Pod   → RS 在建 Pod，卡在调度/拉镜像
     · 有 Pod 不 Ready → 卡在挂载/探针

③ 再判配置是否幂等（改完是否会被改回去）
   - 有没有别的控制器/HPA 在改 replicas？
   - GitOps（ArgoCD）会不会把手工改动 reconcile 回去？
```

> **为什么"判期望态"排第一**：**一半以上的"工作负载题"其实是"yaml 写错了"**，先查集群是浪费时间。**先看自己写的东西，再看集群。**

#### 模板 D · 集群维护题（etcd / 节点 / 证书）

```
etcd 类：
  备份 → 校验 → 异地存放    （三步缺一不可）
  恢复 → 停 etcd → 恢复数据目录 → 改静态 Pod 的 --data-dir → 重启
  ⚠️ kind 上练不了，必须借 kubeadm 机器

节点维护类：
  cordon → 看 PDB → drain（--ignore-daemonsets）→ 维护 → uncordon

证书类：
  生成 CSR → kubectl create -f csr.yaml → kubectl get csr
  → kubectl certificate approve → 取 .status.certificate
```

---

## 3. 动手：完整模拟卷

### 3.1 考试须知与时间预算

| 约定 | 内容 |
|---|---|
| 建议总时长 | **120 分钟**（本卷 12 题，**这是自定训练时长，不是官方考试时长**） |
| 允许查资料 | **只允许官方文档**（`kubernetes.io`）；实战中要练"查文档的速度" [S2] |
| 允许的操作 | 任意 `kubectl` 命令、查看任意 namespace 的资源 |
| 禁止 | 重装集群、删控制面、访问外网业务系统 |
| 判分 | 按 4.1 表逐题打分，**路径不达标的题即使最终状态对了也扣分** |

### 3.2 模拟卷题目表

| 题号 | 领域 | 分值（自定） | 题干 |
|---|---|---|---|
| Q1 | 排障·工作负载 | 8 | `day2-go` Deployment 有 3 个副本，但 `kubectl get pods` 只有 1 个 `Running`，另外 2 个是 `Pending`。请定位原因并修复。 |
| Q2 | 排障·网络 | 8 | `day2-go-svc` Service 已存在，Pod 都是 `Running`。从集群内访问 `day2-go-svc` 返回 503。请定位原因并修复。 |
| Q3 | 排障·工作负载 | 8 | 某 Deployment 的 Pod 在 `Running 0/1` 与 `CrashLoopBackOff` 之间反复。请定位原因并修复。 |
| Q4 | 存储 | 8 | 为 `day2-db` 创建一块 5Gi 的持久卷并让 Pod 用上，要求 Pod 重建后数据仍在。 |
| Q5 | 配置 | 6 | 把 `day2-go` 使用的配置从环境变量改为从 ConfigMap `day2-go-conf` 读取，且改 ConfigMap 后应用能读到新值。 |
| Q6 | 工作负载 | 6 | 把 `day2-go` 扩到 5 副本，并把滚动更新的 `maxUnavailable` 设为 1。 |
| Q7 | 调度 | 8 | 给 `day2-worker-2` 打上标签 `disktype=ssd` 并加污点 `dedicated=db:NoSchedule`；再让 `day2-db` 能调度到该节点并容忍这个污点。 |
| Q8 | 集群维护·etcd | 10 | 对集群的 etcd 做一次备份并验证快照可用。（本条在 kind 上**只做"能做什么/不能做什么"的说明**，实际命令在 kubeadm 环境练） |
| Q9 | RBAC | 6 | 命名空间 `day2-app` 里的 ServiceAccount `day2-ci` 需要能 `get`/`list` 本命名空间的 Pod。请创建最小权限的授权。 |
| Q10 | 网络·NetworkPolicy | 8 | 编写一条 NetworkPolicy：`day2-db` 只允许 `day2-app` 命名空间内带 `app=web` 标签的 Pod 访问其 5432 端口；并说明需要什么前提才生效。 |
| Q11 | 集群维护·节点 | 8 | 对 `day2-worker-1` 做节点维护：先让它不再接收新 Pod，再安全驱逐其上的 Pod。 |
| Q12 | 集群维护·证书 | 8 | 为一个名为 `my-service` 的客户端申请一张访问 apiserver 的证书，走完整流程并说明每一步的检查点。 |

### 3.3 参考答案要点 + 评分点 + 常见失分

---

**Q1 · `day2-go` 有 2 个 Pod Pending** [S3]

| 项 | 内容 |
|---|---|
| **考点** | 调度层排障：资源不足 / 亲和性 / 污点 / 卷绑定；`describe` 看 Events |
| **参考答案要点** | ① `kubectl get pods -n <ns>` 确认状态；② `kubectl describe pod <pending-pod> -n <ns>` 看 Events；③ Events 会直接给出原因：<br>· `Insufficient cpu` → 加 requests 或加节点<br>· `node(s) had taint ...` → 加 toleration<br>· `didn't match node selector` → 改 selector<br>· `persistentvolumeclaim ... not found` → 先修 PVC<br>④ 按对应原因改 Deployment（`kubectl edit` 或改 yaml 后 `apply`）；⑤ 验证 `kubectl get pods` 全部 Running |
| **评分点** | ① **必须先 describe 看 Events**（2 分）<br>② 说出根因且与 Events 原文一致（3 分）<br>③ 修复动作正确（2 分）<br>④ 验证最终态（1 分） |
| **常见失分** | ① **不 describe 直接改镜像**（没定位就动手）；② 看到 Pending 就加副本数（方向错）；③ 只改了一个 Pod 而不是改 Deployment（**副本会被控制器重建回来**）；④ 把"Pending"当"资源不够"，实际是污点 |

---

**Q2 · Service 访问 503，Pod 全 Running**

| 项 | 内容 |
|---|---|
| **考点** | Service 的 selector 与端点；"Pod 正常 ≠ 流量能到" |
| **参考答案要点** | ① `kubectl get endpoints day2-go-svc -n <ns>` → **返回空**；② `kubectl get pods --show-labels -n <ns>` 核对 Pod 的实际标签；③ 对比 Service 的 `spec.selector` 与 Pod 标签 → **发现不一致**（或标签写在了 Deployment 的 `spec.template.metadata.labels` 但 Service 匹配的是另一套）；④ 改 Service 的 selector（`kubectl edit svc`）或改 Deployment 的 template 标签（**注意改了 template 会触发滚动更新**）；⑤ 验证 `kubectl get endpoints` 有 IP，访问成功 |
| **评分点** | ① **先查 endpoints**（3 分，**这是本题的核心动作**）<br>② 找出标签不匹配（3 分）<br>③ 修复且选对改哪一侧（1 分）<br>④ 验证（1 分） |
| **常见失分** | ① 去查防火墙/网络（**网络层完全没问题**）；② 只 `kubectl get svc` 看到有 ClusterIP 就认为 Service 正常；③ 改完不验证 endpoints（**"改完了"不等于"生效了"**）；④ 改 Deployment 的 template 标签时没意识到会滚动重建 Pod |

---

**Q3 · Running 0/1 / CrashLoopBackOff 反复**

| 项 | 内容 |
|---|---|
| **考点** | 探针语义；`logs --previous`；liveness vs readiness |
| **参考答案要点** | ① `kubectl describe pod <pod> -n <ns>` → 看 Events 与探针配置；② 区分两种可能：<br>· **liveness** 探针失败 → 容器被**反复杀掉重启** → 状态 `CrashLoopBackOff`<br>· **readiness** 探针失败 → 只摘端点，**不重启**，状态会停在 `Running 0/1`<br>③ `kubectl logs <pod> --previous -n <ns>` 看崩溃前的输出（**这一步是分水岭**）；④ 常见根因：探针路径/端口写错、应用启动慢（应改用 `startupProbe`）、依赖服务未就绪、配置缺失导致启动即退出；⑤ 修复：改探针参数 / 加 `startupProbe` / 放宽 `initialDelaySeconds` |
| **评分点** | ① 用 `--previous` 拿到上一次崩溃的日志（3 分）<br>② 区分 liveness 与 readiness 的语义（2 分）<br>③ 指出探针写错或缺 startupProbe（3 分）<br>④ 修复并验证（2 分） |
| **常见失分** | ① **只看当前日志**（看到的全是"启动中"，没有崩溃现场）；② 把 readiness 失败当成"要重启"；③ 只会加 `initialDelaySeconds` 而不改 `startupProbe`（治标）；④ 不看 Events 就改参数 |

---

**Q4 · 5Gi 持久卷 + Pod 重建后数据仍在**

| 项 | 内容 |
|---|---|
| **考点** | PV/PVC/StorageClass；绑定与挂载；**持久性的真正条件** |
| **参考答案要点** | ① 查 `kubectl get storageclass` → 确认集群有可动态制备的 SC（kind 默认 `standard`，基于 hostPath）；② 建 PVC：`apiVersion: v1, kind: PersistentVolumeClaim, spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: 5Gi}}}`（**或指定 `storageClassName`**）；③ Deployment 的 `spec.template.spec.volumes` 声明 PVC，`spec.template.spec.containers[].volumeMounts` 挂到容器路径；④ 验证 `kubectl get pvc` → **STATUS 必须是 `Bound`**；⑤ **数据持久化的真正条件**：Pod 重建后落到**同一块卷**上 → PVC 不能被删、且 Pod 必须引用 PVC（**引用 `emptyDir` 或 `hostPath` 的临时目录就不是持久化**） |
| **评分点** | ① PVC 正确声明并绑定成功（3 分）<br>② 挂载路径与 volumeMounts 正确（2 分）<br>③ **指出"数据不丢"依赖 PVC 引用而非目录**（3 分，**这是本题的深水区**）<br>④ 验证 Bound + 写入数据 + 删 Pod 后数据仍在（2 分） |
| **常见失分** | ① 用 `emptyDir` 当持久化（**重启就没**）；② 以为 hostPath 天然持久（**节点换了数据就没了**）；③ 忘了 `volumeMounts`（Pod 卡在 `ContainerCreating`）；④ PVC Pending 时去删 Pod |

---

**Q5 · 配置改为 ConfigMap 读取，且能读到新值**

| 项 | 内容 |
|---|---|
| **考点** | ConfigMap；挂载方式；**配置更新的生效机制** |
| **参考答案要点** | ① 建 ConfigMap `day2-go-conf`；② 两种挂法：**环境变量**（`env.valueFrom.configMapKeyRef`）或**文件**（`volumes[].configMap` + `volumeMounts`）；③ **关键差异**：<br>· **环境变量方式：改 ConfigMap 后，Pod 必须重建才会拿到新值**（环境变量在容器启动时注入）<br>· **文件挂载方式：改 ConfigMap 后 kubelet 会自动更新文件**（有延迟，取决于 kubelet 的同步周期），**应用要自己重读文件**<br>④ 所以题目"改 ConfigMap 后应用能读到新值"的正解是：**用文件挂载 + 应用支持重读**，或者**改 ConfigMap 后重建 Pod** |
| **评分点** | ① ConfigMap 创建正确（2 分）<br>② 挂载方式正确（2 分）<br>③ **说清两种挂法的更新语义差异**（4 分，**核心考点**）<br>④ 给出"如何让应用读到新值"的具体动作（2 分） |
| **常见失分** | ① 以为改 ConfigMap 后 Pod 里立刻就有（**环境变量方式不成立**）；② 用 `envFrom` 但没意识到单 key 用 `env.valueFrom`；③ 忘了 `subPath` 挂载的**文件不会自动更新**（⚠️ 这一点需现场验证，见待验证清单） |

---

**Q6 · 扩到 5 副本 + maxUnavailable=1**

| 项 | 内容 |
|---|---|
| **考点** | `kubectl scale`；滚动更新策略字段 |
| **参考答案要点** | ① `kubectl scale deployment day2-go -n <ns> --replicas=5`（**或 `kubectl edit` 改 `spec.replicas`**）；② 改 `spec.strategy.rollingUpdate.maxUnavailable: 1`（`kubectl edit deployment day2-go`）；③ `spec.strategy.type` 默认是 `RollingUpdate`；④ **验证**：`kubectl rollout status deployment/day2-go -n <ns>`（**注意：这个命令超时不给原因，真出问题还要回去看 Pod**） |
| **评分点** | ① 副本数改对且验证（3 分）<br>② maxUnavailable 改对（3 分）<br>③ 知道 `maxUnavailable=1` 的含义（**更新时最多 1 个不可用**）（2 分）<br>④ 用 rollout status 或 get 验证（2 分） |
| **常见失分** | ① 只 scale 没改策略；② 改了 `maxSurge` 却改了 `maxUnavailable`（**两个字段含义不同，题目问哪个改哪个**）；③ 用了 `kubectl patch` 写错字段路径；④ **在有 HPA 的集群上直接 scale（会被 HPA 改回去）** |

---

**Q7 · 标签 + 污点 + 调度约束**

| 项 | 内容 |
|---|---|
| **考点** | 放置约束（nodeSelector/affinity）vs 驱逐约束（taint/toleration） |
| **参考答案要点** | ① 打标签：`kubectl label node day2-worker-2 disktype=ssd`（**已存在时加 `--overwrite`**）；② 加污点：`kubectl taint node day2-worker-2 dedicated=db:NoSchedule`（**effect 必须是题目要求的那个**，`NoSchedule` 不驱逐、`NoExecute` 会驱逐）；③ 让 Pod 能去该节点：**`nodeSelector: {disktype: ssd}`** 选它（硬约束）；④ 让 Pod 能容忍污点：**`tolerations: [{key: dedicated, operator: Equal, value: db, effect: NoSchedule}]`**；⑤ 验证：`kubectl get pods -o wide` 看 `NODE` 列；`kubectl describe node day2-worker-2` 看 `Taints` 与 `Labels` |
| **评分点** | ① label 与 taint 都加对（3 分）<br>② **nodeSelector 与 toleration 都配**（3 分，**只配一个 Pod 依然 Pending**）<br>③ 解释两套机制的不同职责（2 分）<br>④ 验证调度结果（2 分） |
| **常见失分** | ① **只加 toleration 不加 nodeSelector** → Pod 还是不上去（**toleration 只说"能忍"，不说"要去"**）；② taint 的 effect 写错；③ 标签加了但没 `--overwrite`；④ 认为 toleration 能"吸引"Pod |

---

**Q8 · etcd 备份与验证**

| 项 | 内容 |
|---|---|
| **考点** | **etcd 备份恢复是 CKA 的重点**；备份的正确性验证；备份不能与集群同生共死 |
| **参考答案要点** | ① `etcdctl snapshot save` —— 必须带 **`--endpoints` + `--cacert` + `--cert` + `--key`**；`ETCDCTL_API=3`；② **`snapshot status --write-out=table` 验证完整性**（**这一步是评分重点，不做等于没备份**）；③ **把快照拷到集群之外**（异机/对象存储）；④ **明确说出不该备份什么**：❌ 不该 tar 打包 etcd 的数据目录当热备（不是一致性快照）❌ 不该备份 `/var/lib/kubelet`（节点状态，与集群真相无关）❌ 不该只备份不校验 ⑤ 恢复流程：`snapshot restore` → 改静态 Pod 的 `--data-dir` → 重启；**`--name` / `--initial-cluster` 必须与原集群一致** |
| **评分点** | ① 四个参数齐全 + `ETCDCTL_API=3`（3 分）<br>② **主动执行并说明 snapshot status 校验**（3 分）<br>③ 说明异地存放（1 分）<br>④ **说清"不该备份什么"**（3 分）<br>⑤ 恢复流程与 `--data-dir` 改动（3 分） |
| **常见失分** | ① **只 save 不 status**；② 用 `tar` 备份数据目录；③ 忘了 `--endpoints`（默认连 127.0.0.1:2379 但不带证书会失败）；④ 快照留在 etcd 同一台机器；⑤ 说不出恢复时 `--data-dir` 要改 |
| **环境说明** | **kind 上无法原样练这题**（etcd 在控制面容器内）。**正确应答姿势**：说明命令 + 说明在 kind 上的差异 + 说明会用 kubeadm 环境演练。**这不是不会，是环境限制。** |

---

**Q9 · ServiceAccount 最小权限**

| 项 | 内容 |
|---|---|
| **考点** | RBAC 三件套：Role / RoleBinding / ServiceAccount；**命名空间级 vs 集群级** |
| **参考答案要点** | ① 建 Role（**命名空间级**，**不是 ClusterRole**）：`rules: [{apiGroups: [""], resources: ["pods"], verbs: ["get","list"]}]`；② 建 RoleBinding，把 `subjects` 指向 `kind: ServiceAccount, name: day2-ci, namespace: day2-app`（**ServiceAccount 跨命名空间引用必须写 namespace**）；③ 验证：`kubectl auth can-i list pods --as=system:serviceaccount:day2-app:day2-ci -n day2-app` → 期望 `yes`；④ 反向验证：同一命令去掉 `-n day2-app` → 期望 `no`（**证明权限没有溢出**） |
| **评分点** | ① 用 Role 而非 ClusterRole（2 分）<br>② verbs 精确为 get/list（**不给多余权限**）（2 分）<br>③ RoleBinding 的 subject 正确（含 namespace）（3 分）<br>④ 用 `kubectl auth can-i` 双向验证（3 分） |
| **常见失分** | ① 直接给 ClusterRole + ClusterRoleBinding（**权限过大，违背最小权限**）；② subject 里的 SA 忘了写 namespace（**会绑到错误命名空间的 SA**）；③ verbs 写 `*`；④ 不会用 `kubectl auth can-i --as` 验证 |

---

**Q10 · NetworkPolicy**

| 项 | 内容 |
|---|---|
| **考点** | NetworkPolicy 的语义；**跨命名空间选择器的写法**；**CNI 支持前提** |
| **参考答案要点** | ① `podSelector: {matchLabels: {app: db}}` 选中**被管控方**；② `policyTypes: [Ingress]`；③ `ingress[].from` 里要表达"**来自 day2-app 命名空间里带 app=web 标签的 Pod**" → 用 **`namespaceSelector` + `podSelector` 两个并列**（`podSelector` 空对象 + `namespaceSelector` 带 `kubernetes.io/metadata.name: day2-app`）；④ `ports: [{protocol: TCP, port: 5432}]`；⑤ **前提：集群的 CNI 必须支持 NetworkPolicy**（kind 默认的 kindnet **不支持**，要换 Calico；**策略对象能创建成功 ≠ 流量被拦**） |
| **评分点** | ① podSelector 选对被管控方（2 分）<br>② **跨 ns 用 namespaceSelector + podSelector 并列**（4 分，**核心考点**）<br>③ policyTypes 与 ports 正确（2 分）<br>④ **说出 CNI 前提**（2 分） |
| **常见失分** | ① 跨命名空间只写 `podSelector` 不写 `namespaceSelector`（**默认同命名空间**）；② 只写 `podSelector` 就以为跨 ns 了；③ 忘记 `policyTypes`（**默认会同时作用于两个方向**）；④ 不知道 CNI 前提，**策略没生效还以为自己写错了** |

---

**Q11 · 节点维护**

| 项 | 内容 |
|---|---|
| **考点** | cordon / drain 的分工；PDB；DaemonSet 与 emptyDir 的处理 |
| **参考答案要点** | ① `kubectl cordon day2-worker-1`（**先标记不可调度**）；② **看中断预算**：`kubectl get pdb -A`；③ `kubectl drain day2-worker-1 --ignore-daemonsets --delete-emptydir-data`；④ 解释每个参数：`--ignore-daemonsets` 跳过 DaemonSet 的 Pod（它们本来就不迁）；`--delete-emptydir-data` 允许删除用 emptyDir 的 Pod（**数据会丢**）；⑤ **drain 卡住通常是 PDB 在起作用**（`minAvailable` 不满足就会等）；⑥ 维护完 `kubectl uncordon day2-worker-1` |
| **评分点** | ① 先 cordon 再 drain（3 分）<br>② **知道要看 PDB**（3 分）<br>③ 参数选择正确且能解释（2 分）<br>④ 说明 drain 后 Pod 会被控制器重建到别的节点（2 分） |
| **常见失分** | ① 直接 drain 不 cordon（**新 Pod 立刻又调度上来**）；② 不看 PDB（**可能一次赶走同服务全部副本**）；③ 见到卡住就加 `--force`（**绕过保护**）；④ 忘了 `--ignore-daemonsets` 导致 drain 报错中止；⑤ 忘了最后的 uncordon |

---

**Q12 · CSR 完整流程**

| 项 | 内容 |
|---|---|
| **考点** | CSR 五步；检查点；`usages` 与 signer 的关系 |
| **参考答案要点** | ① 生成私钥与 CSR（`openssl genrsa` + `openssl req -new`）；② 提交：`CertificateSigningRequest` 对象，`spec.request` 是 **base64 编码的 PEM 内容**，`spec.usages` 写 `client auth`；③ 检查点 1：`kubectl get csr` → CONDITION 为 `Pending`；`kubectl describe csr <name>` 看请求内容与 usages；④ 审批：`kubectl certificate approve <name>`；⑤ 检查点 2：`kubectl get csr <name>` → CONDITION 为 `Approved`；⑥ 取证书：`kubectl get csr <name> -o jsonpath='{.status.certificate}'` 的输出经 `base64 -d` 解码后写入 `client.crt`；⑦ **检查点 3：`.status.certificate` 必须非空**（审批 ≠ 签发）；⑧ 用证书 + 私钥配置 kubeconfig 或挂载进 Pod 使用 |
| **评分点** | ① 五步完整（4 分）<br>② 说明 base64 与 usages（2 分）<br>③ **指出"审批后要查 .status.certificate 才算签发完成"**（2 分） |
| **常见失分** | ① 以为 approve 完就结束（**没取证书**）；② `spec.request` 直接贴 PEM 没 base64；③ usages 写错（`server auth` vs `client auth`）导致签名器拒绝；④ 不会 `base64 -d` 解出证书；⑤ 分不清"Pending / Approved / Denied"各代表什么 |

---

## 4. 逐字段表

### 4.1 模拟卷题目总表

| 题号 | 领域 | 分值 | 考点一句话 | 第一条应该敲的命令 | 核心检查点 | 常见失分（最高频） | **写错的后果** |
|---|---|---|---|---|---|---|---|
| Q1 | 排障·工作负载 | 8 | Pending 的调度层根因 | `kubectl describe pod` | Events 里 `FailedScheduling` 的原文 | 不 describe 直接改镜像 | 改镜像 → Pod 换成新镜像仍 Pending，**时间耗尽且根因未解** |
| Q2 | 排障·网络 | 8 | Service 端点为空 | `kubectl get endpoints` | endpoints 是否为空 | 去查防火墙/网络 | 改网络策略 → 端点仍为空，**动了不该动的生产配置** |
| Q3 | 排障·工作负载 | 8 | 探针与崩溃 | `kubectl logs --previous` | 崩溃前的最后几行 | 只看当前日志 | 直接重启 → 崩溃现场丢失，**反复重启掩盖根因** |
| Q4 | 存储 | 8 | PVC 绑定与真持久化 | `kubectl get storageclass` | PVC 变 `Bound` | 用 emptyDir 当持久化 | Pod 重建后数据全丢，**生产上等于删库** |
| Q5 | 配置 | 6 | ConfigMap 更新生效语义 | `kubectl get cm` | env 方式 vs 文件方式 | 以为 env 方式会热更新 | 改完 ConfigMap 以为生效 → **线上跑的还是旧配置** |
| Q6 | 工作负载 | 6 | 扩容与滚动策略 | `kubectl scale` | `spec.strategy` 改动 | 改错 maxSurge/maxUnavailable | 策略写反 → **滚动更新的可用性保证反了**，更新期可能全不可用 |
| Q7 | 调度 | 8 | 标签 + 污点 + 约束 | `kubectl label node` | Pod 的 NODE 列 | 只加 toleration 不加 nodeSelector | Pod 仍 Pending → **污点加了、目标节点选不中，白做** |
| Q8 | 维护·etcd | 10 | 备份 + 校验 + 恢复 | `etcdctl snapshot save` | `snapshot status` 通过 | 只 save 不 status | 快照损坏未察觉 → **真正需要恢复时才发现备份是废的** |
| Q9 | RBAC | 6 | 最小权限授权 | `kubectl auth can-i` | 越权检查返回 no | 用 ClusterRole 提权 | 给了集群级权限 → **违背最小权限，审计不合规** |
| Q10 | 网络 | 8 | NetworkPolicy 跨 ns | `kubectl get netpol` | CNI 是否支持 | 漏 namespaceSelector | 策略被当成跨 ns 生效 → **实际同 ns 全通，隔离没做** |
| Q11 | 维护·节点 | 8 | cordon/drain + PDB | `kubectl cordon` | PDB 是否阻塞 | 不 cordon 直接 drain | 新 Pod 又调度上来 → **维护中的节点继续接流量** |
| Q12 | 维护·证书 | 8 | CSR 五步 | `openssl genrsa` | `.status.certificate` 非空 | approve 后不取证书 | 以为签发完成 → **实际没有可用证书**，客户端连不上 |
| **合计** | — | **92** | — | — | — | — | — |

> **合计 92 分是本卷的自定分值体系**，用来做**相对比较**（这次比上次多拿几分），**与官方评分体系无关**。

### 4.2 失分统计复盘表

**每次模拟后填一行。这是本篇最有价值的一张表 —— 它把你的练习变成数据。**

| 题号 | 是否做出 | 用时(分钟) | **失分类型** | **归因分类** | 下一步动作 |
|---|---|---|---|---|---|
| （填） | 完成/部分/未完成 | | 具体扣在哪一步 | **A 真不会** / **B 环境不支持** / **C 命令记错** / **D 思路错** | 回 L1 哪一节 / 练哪条命令 |
| （填） | | | | | |
| （填） | | | | | |

**字段说明（逐字段）**：

| 字段 | 类型 | 必填 | 含义 | 填错的后果 |
|---|---|---|---|---|
| **是否做出** | enum | ✅ | 完成 / 部分 / 未完成 | 只记"对错"会丢掉"差在哪一步"的信息 |
| **用时** | int（分钟） | ✅ | 从开始这道题到结束 | **不记用时 = 看不见"慢"这个瓶颈**；CKA 里超时是常态 |
| **失分类型** | text | ✅ | 具体扣在哪个动作上（如"没查 endpoints，直接改了 Service 的 port"） | 写"排障不会"等于没写 —— **不可改进** |
| **归因分类** | enum | ✅ | A/B/C/D 四类 | **不分类 = 把环境限制当成知识缺口 = 瞎补** |
| **下一步动作** | text | ✅ | 具体到"回 L1 第 X 节"或"死记 Y 命令" | 写"多练"等于没写 |

**按知识域汇总（每 3 次模拟做一次）**：

| 知识域 | 题号 | 3 次模拟累计失分 | 占比 | 判定 | 行动 |
|---|---|---|---|---|---|
| 排障·工作负载 | Q1, Q3 | | | 薄弱/一般/掌握 | 回 L1 §4.2 症状表 + 重复注入故障 1/3 |
| 排障·网络 | Q2, Q10 | | | | 回 L1 §2.8 网络链路 |
| 存储 | Q4 | | | | 回 L1 §4.2 存储行 |
| 配置 | Q5 | | | | 回 L1 §2.4 / §4.1 |
| 工作负载与调度 | Q6, Q7 | | | | 回 L1 §2.4 声明式 vs 命令式 |
| 集群维护 | Q8, Q11, Q12 | | | | **必须借 kubeadm 机器练** |
| RBAC | Q9 | | | | 回 L1 §2.7 / §4.2 |

> **判读规则**：某一域三次累计失分占比 **> 30%** → 判定"薄弱"，**接下来一周的训练时间至少 40% 投在这个域**；**每次模拟都要统计总用时**，如果总用时超标但完成率也低 → **问题在"速度"不在"知识"**，解法是死记命令而不是补概念。

---

## 5. 横向对比

### 5.1 刷题 vs 动手实验 vs 真机练习

| 维度 | **刷题**（做题网站/题库） | **动手实验**（本篇模拟环境） | **真机练习**（kubeadm / 云主机 / 真集群） |
|---|---|---|---|
| 练什么 | **答案与命令记忆** | **排障路径**（症状→定位→证据→假设→验证） | **完整链路 + 环境真实度** |
| 反馈速度 | 快（对答案） | **中**（自己判断） | 慢（环境重） |
| 能否练"没思路"的情况 | ❌ 题库通常给你提示 | ✅ **会**（故障是真的） | ✅ 会 |
| 能否练 etcd 恢复 / 证书 / 静态 Pod | ❌ 基本不能 | ❌ 不能（kind 限制） | ✅ **能** |
| 成本 | 最低 | 低（分钟级重置） | 中高 |
| 典型陷阱 | **"看答案觉得我会"** | 环境问题被误判成知识问题 | 真实环境有干扰 |
| **正确用法** | **用来查漏**：确认某个命令/字段你会写 | **主战场**：反复注入同一个故障，练路径 | **补齐 kind 练不到的 20%** |
| 单独用的问题 | **假会**（看得懂 ≠ 敲得出） | 覆盖面不全 | 效率低 |

> **结论与配比建议（经验判断）**：
> - **动手实验 60%** —— 主战场，把故障 1-5 各做 5 遍，每遍计时；
> - **刷题 30%** —— 用来查"有没有连命令都记不起来的字段"；
> - **真机 10%** —— 但这 10% **必须覆盖 etcd 备份恢复 + CSR + 静态 Pod**，因为这是 kind 结构上练不到的。
>
> **为什么刷题单独用会"假会"**：题库给的是"题目 → 答案"，你走的是"答案 → 理解"。**真实考试给的是"现象 → 你自己走"**，中间那一步只有动手实验能补上。

### 5.2 模拟环境的替代方案对比

| 方案 | 优势 | 劣势 | 建议 |
|---|---|---|---|
| **自建 kind 多节点**（本篇主推） | 可控、可重置、免费、能造任意故障 | **etcd/静态 Pod/证书链不全** | **主力** |
| 第三方 CKA 模拟器（在线/本地） | 题目接近真实、判分客观 | 依赖外网（考场不可用）、**故障是脚本化的，不练真实定位** | 辅助，别当主战场 |
| 容器内嵌套集群（k3d / k3s in docker） | 启动快、体积小 | **与 kind 同样的限制**（控制面在容器内） | 备选 |
| **kubeadm 虚拟机** | **etcd/证书/静态 Pod 全练得到** | 准备成本高、快照恢复要重来 | **必做一次**（etcd 那题） |
| 云上 ACK / 免费试用集群 | 真实、含云 LB 与监控 | 花钱、**不能在生产上练手** | 有机会时做，**只做只读与无害操作** |

---

## 6. 踩坑与排障

> **本章所有表格统一五列**：你看到的（症状）| 状态 | 真实原因 | 怎么修 | 下次怎么防。

### 6.1 训练过程自身的坑

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| 命令都写对了，但每题都超时 | 全部超时 | **只背了命令，没练"选命令"** —— 一步一个 `kubectl get` 试错 | 固定用第 2.4 节的模板：**一次 describe 拿到 Events** | 每次计时做题，**记录每题用时** |
| 做完卷发现 Pod 是 Running，但路径全错 | 蒙对 | **靠反复删 Pod 凑出来的** | 判分时看"路径"不看"结果" | 复盘表加"失分类型"字段 |
| 某个题**每次都做不出来** | 反复失败 | 是 **B 类（环境不支持）**，不是不会 | 用第 1.3 节的能力边界表对照归因 | 复盘表强制填"归因分类" |
| 明明装了 metrics-server，HPA 还是 `<unknown>` | HPA 失效 | metrics-server 的 Pod 没 Ready，或它连不上 apiserver | `kubectl -n kube-system get pods -l k8s-app=metrics-server`；等它 Ready | 装完先验证组件状态，再做题 |
| NetworkPolicy 写了，流量照通 | 策略无效 | **kind 默认的 kindnet 不支持策略** | 换 Calico | 练策略题前先确认 CNI 类型 |
| 练到一半集群状态很乱，做不下去 | 环境脏 | 故障叠加、命名空间里堆了半成品 | 跑重置脚本 | **每次训练前先重置**，保持环境干净 |
| `kind create cluster` 报集群已存在 | 命令失败 | 同名集群已存在 | 换名字，或先删旧的 | 模拟环境用独立名（如 `cka-lab`），不与 `day2` 混用 |

### 6.2 命令写对但顺序错

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| 先删了 Pod，想让它重新调度到新节点 | Pod 没了 | **顺序反了**：应该先 cordon（阻止调度）+ drain（驱逐） | `cordon` → `drain` → 维护 → `uncordon` | 背标准三步 |
| 先 `kubectl apply` 一份改好的 yaml，想验证 selector 错在哪 | 直接报错 | **没先看现状**：不知道原 selector 是什么 | 先 `kubectl get deploy -o yaml` 看 `spec.selector` 与 `spec.template.metadata.labels` | 改期望态前先看实际态 |
| 先重启 Deployment 想解决 CrashLoop | 重启无效 | **根因在探针/配置/命令**，重启只是让它再崩一次 | 先 `logs --previous` | 先取证再动手 |
| 先 `kubectl delete pvc` 想解决 PVC Pending | PVC 被删 | **绑定问题不在 Pod 也不在 PVC 生命周期** | `describe pvc` 看 Events，逐个排四个原因 | 存储题先判 PVC 状态 |
| 先 scale 副本数再改滚动策略 | 顺序错但影响小 | 策略修改与扩容互不依赖，但**有 HPA 时会打架** | 先确认没有 HPA 在管这个 Deployment | scale 前 `kubectl get hpa -A` |

### 6.3 忘记 `-n` 指定命名空间

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `kubectl get pods` 显示空，但资源明明存在 | 空输出 | **资源在别的命名空间**，当前是 `default` | 补 `-n <ns>`；或 `kubectl get pods -A` | **每条命令前先问"它在哪个 ns"** |
| `kubectl logs <pod>` 报 pod 不存在 | 命令失败 | 同上 | 补 `-n` | 同上 |
| `kubectl auth can-i` 返回 `no`，但实际有权限 | 误判 | **在 `default` ns 里查的**，Role 是命名空间级的 | 补 `-n <ns>` | RBAC 题**必须带 `-n`** |
| `kubectl get events` 一条都没有 | 空输出 | Events 是**命名空间级**资源 | 补 `-n`；或 `kubectl get events -A` | 查事件记得带 `-A` 兜底 |

### 6.4 修改操作该用 `edit` 却用了 `apply` 覆盖

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| 改完 replicas，发现 `resources` / `labels` 没了 | 字段被抹 | **`apply` 提交了一份不完整的 yaml**，把没写的字段按 last-applied 规则抹掉了 | 从 Git / 备份恢复原字段；或用 `kubectl get -o yaml` 找回 | **改单个字段一律用 `edit` / `set` / `patch`** |
| `kubectl replace -f` 之后副本数变成 1 | 字段被抹 | **replace 是整体替换**，yaml 里没写的字段全部消失 | 同上 | **replace 前先 `kubectl get -o yaml` 存一份现状** |
| 改了 Deployment 的 template labels，所有 Pod 被重建 | 意外滚动 | **改 template 必然触发滚动更新** | 无害，但要预期 | 只想改 Service 的匹配就改 Service 侧 |
| `kubectl edit` 改完退出时按错键 | 改动丢失/损坏 | `vi` 操作不熟 | `Esc` → `:q!` 放弃；或 `:wq` 保存 | 练熟 `vi` 三键 |
| 手工改的字段被"改回去" | 反复 | **有 GitOps / HPA 在 reconcile** | `kubectl get hpa -A`；检查是否有 ArgoCD Application | 手工改动前先看有没有自动化的接管者 |

### 6.5 删除资源时忘了级联

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| 删了 Deployment，Pod 还留着 | 孤儿资源 | 默认 `--cascade=background`，本体先删、子资源异步删 | 等一会；或用 `--cascade=foreground` | 删除有依赖的资源**默认加 `--cascade=foreground`** |
| 删了 StatefulSet，PVC 还在 | 残留 | **PVC 默认不被级联删除**（这是设计：防数据丢失） | 手动删 PVC（**确认数据不要了**） | 知道这是**预期行为**不是 bug |
| 删了命名空间，Pod 卡在 Terminating | 等待 | 命名空间删除是异步的，需要所有资源清完 | `kubectl get ns` 看状态；`kubectl get pods -A` 看剩余 | 正常等待；急的话查 finalizer |
| 删 Pod 后 Deployment 立刻重建了一个 | 预期 | **Deployment 的职责就是维持副本数** | 改副本数或删 Deployment | 想知道"为什么又起来了"就去看控制器 |
| `kubectl delete pod` 没生效 | 命令失败 | Pod 由控制器管理，删了会被重建 | 要停掉得改期望态（scale 到 0 或删 Deployment） | **理解"删除"和"停止"是两件事** |

### 6.6 考试环境无外网

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `kubectl apply -f https://...` 卡住或超时 | 网络失败 | **考试环境不保证外网** | 所有 manifest **提前下载到本地** | 训练时也用本地文件，**养成习惯** |
| 查官方文档打不开 | 网络失败 | 同上 | 依赖记忆 + 本地速查表（`L1-CKA知识点速查.md`） | **考场前把 L1 第 4 节打印/离线备好** |
| `helm repo add` 拉不到仓库 | 网络失败 | 同上 | 提前 `helm pull` 到本地 | 依赖 chart 也要本地化 |
| 镜像拉不下来 | 网络失败 | 私有仓库需要凭据 + 网络可达 | 提前把镜像 `kind load docker-image` 导入节点 | 训练环境提前导入镜像 |
| 靠在线题库查命令 | 网络失败 | 同上 | 靠本地速查表 | **不把在线资源当唯一依赖** |

> **这条是本篇最"反直觉"的现实约束**：**你在自己机器上能 internet 刷题，考场里不能。** 所以训练环境应该**主动模拟无网**：把 manifest、镜像、文档全部本地化，**平时就按无网条件练**。

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `www.cncf.io/training/certification/cka/` | **CKA 认证页（知识域、政策的唯一权威来源）** | ✅ 2026-10-24 |
| S2 | 中文 | `kubernetes.io/zh-cn/docs/reference/kubectl/quick-reference/` | 速查命令、输出格式、选择器语法 | ✅ 2026-10-24 |
| S3 | 中文 | `kubernetes.io/zh-cn/docs/tasks/debug/debug-application/debug-pods/` | Pod 排障路径与状态判读 | ✅ 2026-10-24 |
| S4 | 中文 | `kubernetes.io/zh-cn/docs/tasks/debug/debug-cluster/` | 集群/节点排障、组件健康 | ✅ 2026-10-24 |
| S5 | 中文 | `kubernetes.io/zh-cn/docs/tasks/manage-kubernetes-objects/declarative-config/` | apply vs edit 的取舍、级联删除 | ✅ 2026-10-24 |
| S6 | 中文 | `kubernetes.io/zh-cn/docs/concepts/storage/persistent-volumes/` | PV/PVC/StorageClass、绑定与访问模式 | ✅ 2026-10-24 |
| S7 | 中文 | `kubernetes.io/zh-cn/docs/concepts/configuration/configmap/` | ConfigMap 挂载与更新语义 | ✅ 2026-10-24 |
| S8 | 中文 | `kubernetes.io/zh-cn/docs/concepts/services-networking/service/` | Service 类型与端点 | ✅ 2026-10-24 |
| S9 | 中文 | `kubernetes.io/zh-cn/docs/concepts/services-networking/ingress/` | Ingress 规则与后端 | ✅ 2026-10-24 |
| S10 | 中文 | `kubernetes.io/zh-cn/docs/concepts/scheduling-eviction/assign-pod-node/` | 节点选择、调度约束 | ✅ 2026-10-24 |
| S11 | 中文 | `kubernetes.io/zh-cn/docs/concepts/security/rbac-good-practices/` | RBAC 最小权限、`kubectl auth can-i` | ✅ 2026-10-24 |
| S12 | 中文 | `kubernetes.io/zh-cn/docs/concepts/workloads/controllers/deployment/` | 副本数、滚动更新策略 | ✅ 2026-10-24 |
| S13 | 中文 | `kubernetes.io/zh-cn/docs/concepts/workloads/pods/` | Pod 生命周期与探针 | ✅ 2026-10-24 |
| S14 | 中文 | `kubernetes.io/zh-cn/docs/concepts/cluster-administration/logging/` | 日志采集与检索 | ✅ 2026-10-24 |
| S15 | 中文 | `kind.sigs.k8s.io/docs/user/quick-start/` | **kind 多节点配置与本地集群** | ✅ 2026-10-24 |
| S16 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | 生产形态对照（LB、监控、私有仓库） | ✅ 2026-10-24 |
| S17 | 中文 | 中文题库与学习社区（**索引未提供单条 URL**） | 题型分布与配比的社区口径 | ⚠️ 单源待补（需补具体 URL） |
| S18 | 官方 | etcd 官方文档（**索引未收录**） | `etcdctl` 参数与恢复流程细节 | ⚠️ 单源待补（etcd 官方文档未进索引） |

> **来源纪律说明**：
> ① 本篇的**题量、时长、及格线、知识域权重**全部以 **S1 官方页面当前表述为准**；第 2.3 节的配比已明确标注为**经验判断**。
> ② Q8（etcd 备份恢复）的命令细节依赖 S18（缺失），已标 `⚠️ 单源待补`，且在第 3.3 节与第 6 节同步标注了"kind 环境限制"。
> ③ **本文所有 URL 均取自** [`../09-参考索引/README.md`](../09-参考索引/README.md) 中标 ✅ 的条目，**无自造深层链接**。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 一次模拟的输入是"一份 12 题的卷子 + 一个已知状态的多节点集群 + 一个计时器"，输出是三样东西：**得分（相对量）**、**每题用时**、**每题失分的具体动作与归因分类**。**第三样才是资产**，前两样只是副产品。
2. **能故意改坏再倒推吗？** —— 就是第 2.1.3 节的故障注入脚本：把 selector 写错 → `apply` 报错；把 Service selector 指向不存在的标签 → endpoints 空 → 503；把 liveness 端口改错 → `Running 0/1` → CrashLoop；引用不存在的 ConfigMap → `CreateContainerConfigError`；PVC 要 500Gi → Pending。**每个故障都要做两遍：第一遍自己定位，第二遍照着参考答案走一遍标准路径，对比自己漏了哪一步。**
3. **能画上下游吗？** —— 画出**训练闭环**：
   ```
   造故障(脚本) → 限时做题(计时) → 判分(路径+结果) → 复盘表(失分类型+归因)
        ↑                                                  │
        └──────────── 按归因回到 L1 对应章节 ←──────────────┘
   ```
   闭环断在哪一环，就补哪一环：**造故障不自动化 → 练不熟；不计时 → 练不出速度；复盘不填归因 → 瞎补。**

---

## 附 A：每日训练计划（21 天 → CKA 知识域）

**映射口径**（与 `../00-学习地图与前置依赖.md` 的三周划分一致）：

| 周次 | 日课阶段 | 对应 CKA 知识域 | 本周训练重点 | 周末验收 |
|---|---|---|---|---|
| **第 1 周** | 基础与核心对象 | 集群架构与安装 · 工作负载与调度 · 服务与网络 · 存储 · 配置 | **把对象认熟**：每类资源写一份 yaml 并 apply 成功；故障 1/3/4 各注入 3 遍 | 能不查文档写出 Deployment+Service+PVC+ConfigMap 四件套 |
| **第 2 周** | 交付与可观测 | 工作负载进阶 · 配置管理 · 监控与日志 · RBAC | **把"交付"练成流程**：多环境配置注入、滚动更新策略、RBAC 最小授权、故障 2/5 反复注入 | 完成一次"改配置 → 重建 → 验证生效 → 回滚"的完整闭环 |
| **第 3 周** | 综合与排障 | **排障（重点）** · 集群维护（etcd/节点/证书）· 网络专项 | **限时做卷**：第 1 周做单题限时，第 2 周做 6 题小卷，第 3 周做完整 12 题 | **完整卷在自定预算内完成 ≥ 80% 的分值，且复盘表填满** |

**每日固定节奏（与总计划的时间块一致）**：

| 时段 | 时长 | 做什么 |
|---|---|---|
| 晨间 0.5h | 30 分钟 | 读 L1 的一节（**按上次复盘的失分域选节**）+ 默写 5 条命令 |
| 上午 3h | 3 小时 | **注入一个故障 → 限时定位修复 → 记录用时与失分** |
| 下午 4h | 4 小时 | 隔天做**6 题小卷**；第 3 周做**完整 12 题卷** |
| 收尾 0.5h | 30 分钟 | 填复盘表 + 写"明天要补什么" |
| **18:00 后** | — | **不占用** |

**每周末的三个硬指标**（经验设定的自检线，非官方标准）：

| 指标 | 第 1 周 | 第 2 周 | 第 3 周 |
|---|---|---|---|
| 单题定位用时 | ≤ 10 分钟 | ≤ 7 分钟 | ≤ 5 分钟 |
| 复盘表填写完整率 | 100% | 100% | 100% |
| 累计"真不会"（A 类）条目 | ≤ 8 条 | ≤ 5 条 | **≤ 2 条** |

---

## 9. 面试最常考命令速查（按工具分类）

> **本节能做什么**： CKA 模拟卷考的是 K8s 单点操作，但**真去面试 DevOps / 配置管理岗，面试官会顺手考你一整套工具链的命令肌肉记忆**——kubectl 排障四件套、docker 发布、git 协作、helm 发布、redis/psql 排障、CI 提速。本节把这几类**面试最高频的命令**按工具归类，**每条配真实实例 + 面试常问场景**，考前过一遍就能直接背。

### ① kubectl：排障四件套 + 发布 / 节点维护

```bash
# 排障四件套（90% 的 K8s 故障卡在这四步）
kubectl get pods -o wide -n prod               # 1) 看状态：STATUS / NODE / RESTARTS 一眼分
kubectl describe pod <name> -n prod            # 2) 看 Events 与 Last State（Pending/Crash 真相入口）
kubectl logs <pod> -p --tail=100 -c <c>        # 3) 看崩溃前日志（关键 -p，否则看到的是重启后的空日志）
kubectl exec -it <pod> -- sh                   # 4) 进容器看环境变量 / 文件 / 网络

# 发布与回滚
kubectl rollout status deployment/web          # 看这次发布是否就绪
kubectl rollout history deployment/web         # 看历史版本
kubectl rollout undo deployment/web --to-revision=2   # 回滚到指定版本（救火第一招）

# 临时调试（镜像无 shell 时救命）
kubectl debug <pod> -it --image=busybox --target=<c>   # 加临时容器排障

# 节点维护（上线 / 重启 / 缩容前必做）
kubectl cordon <node>                          # 标记不可调度
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data  # 驱逐 Pod 准备维护
kubectl uncordon <node>                        # 维护完恢复调度
```

> **面试常问场景**："Pod 一直 Pending / CrashLoopBackOff 你怎么排？""发布翻车怎么救？""节点要重启你怎么安全下线？"——答案就是上面这套：先 `get` / `describe` / `logs -p` / `exec` 四件套定位，再 `rollout undo` 或 `cordon`→`drain`→`uncordon`。**能把这个顺序讲顺，K8s 排障就不丢分。**

### ② docker：构建 / 运行 / 进容器 / 清理

```bash
# 构建与运行
docker build -t registry.local/web:v1.2 .                 # 按 Dockerfile 构建并打 tag
docker run -d --name web -p 8080:80 -e ENV=prod web:v1.2  # 后台起容器，映射端口 / 注入环境变量
docker ps -a                                              # 看所有容器（含已退出的）

# 进容器与看日志
docker exec -it <cid> sh                                 # 进容器排障（有 bash 用 bash）
docker logs -f --tail=100 <cid>                          # 跟随看最后 100 行日志

# 清理（别让悬空镜像堆满磁盘）
docker system prune -f                                   # 清悬空镜像 / 停止容器 / 网络
docker image prune -a --filter "until=24h"               # 清 24 小时前未被使用的镜像
```

> **面试常问场景**："镜像太大怎么瘦身？""容器一直重启你查什么？""磁盘被 docker 占满怎么清？"——`docker build` 的 layer 缓存、多阶段构建，`docker logs -f`、`docker system prune` 是必背。能说出**多阶段构建减少体积**和**`prune` 释放磁盘**一般就够了。

### ③ git：协作 / 回退 / 救场

```bash
git merge --no-ff feature/x        # 保留分支历史的合并（面试偏好，能看出从哪来）
git rebase main                    # 把当前分支变基于最新 main（线性历史，慎用已推送分支）
git reset --soft HEAD~1            # 回退提交但保留改动到暂存区（改 commit message 用）
git reset --hard HEAD~1            # 硬回退并丢弃改动（慎用，会丢工作）
git cherry-pick <commit>           # 只挑某个提交到当前分支（热修常用）
git stash && git stash pop         # 临时存改动去切分支修紧急 bug
git reflog                         # 看 HEAD 变动历史（reset --hard 后还能找回提交）
```

> **面试常问场景**："rebase 和 merge 区别？""误 `reset --hard` 了怎么救？""线上热修一个提交怎么操作？"——核心答点：**rebase 改历史、merge 留痕迹**；**`reflog` 是后悔药**，`reset --hard` 后还能 `reflog` 找回来；热修用 `cherry-pick` 精准搬运。

### ④ helm：K8s 应用打包与发布

```bash
helm install web ./chart -n prod --set replicaCount=3   # 装一个 release（覆盖 values）
helm upgrade web ./chart -n prod --reuse-values         # 升级（保留旧 values）
helm rollback web 2                                     # 回滚到第 2 个版本（发布救火）
helm template ./chart -n prod                          # 本地渲染出 yaml（不真正部署，CI 校验用）
helm lint ./chart                                       # 静态检查 chart 语法与最佳实践
```

> **面试常问场景**："helm 怎么回滚？""怎么在不部署的情况下验证 chart 写对了？""chart 上线前怎么卡质量？"——`helm rollback` 是发布救火标准动作；`helm template` 把模板渲染成 yaml 给 Review/CI 用；`helm lint` 挡低级错误。能讲清 **release 版本化 = 天然支持回滚** 是加分点。

### ⑤ redis-cli：缓存排障与慢查询

```bash
redis-cli INFO memory              # 看内存占用、碎片率（内存打满排查第一入口）
redis-cli INFO stats               # 看命中率 / 总命令数（命中率低说明缓存设计有问题）
redis-cli --scan --pattern "sess:*"     # 游标遍历大 key（线上禁 KEYS *，会阻塞）
redis-cli TTL sess:abc123          # 看某个 key 剩余过期时间（缓存穿透 / 雪崩排查）
redis-cli SLOWLOG GET 10           # 取最近 10 条慢查询（定位耗时命令）
```

> **面试常问场景**："线上 redis 慢怎么查？""为什么不能用 KEYS *？""缓存命中率低怎么看？"——答点：**`--scan` 替代 `KEYS *` 避免阻塞**；`INFO memory` 看内存、`SLOWLOG` 看慢命令、`TTL` 看过期策略。能说出 **KEYS 会阻塞主线程、大集群必炸** 是基本功。

### ⑥ psql：PostgreSQL 排障

```bash
psql -d app -c "\d orders"                              # 看表结构（列 / 索引 / 约束）
psql -d app -c "EXPLAIN ANALYZE SELECT ..."             # 看真实执行计划（慢 SQL 第一入口）
psql -d app -c "SELECT * FROM pg_stat_activity WHERE state='active';"  # 看活跃连接（锁 / 长事务排查）
psql -d app -c "SELECT pid, wait_event_type FROM pg_stat_activity WHERE wait_event_type='Lock';"  # 看锁等待
pg_dump -Fc -d app -f app.dump                          # 逻辑备份（可配合 cron 做定时备份）
```

> **面试常问场景**："慢 SQL 怎么定位？""数据库连满 / 锁等待怎么查？""怎么做备份？"——答点：`EXPLAIN ANALYZE` 是慢查询定位标配；`pg_stat_activity` 看活跃连接与锁；`pg_dump` 做逻辑备份。能说清 **`EXPLAIN` 看预估、`EXPLAIN ANALYZE` 看真实执行** 区别是加分项。

### ⑦ CI：构建提速与远程触发

```bash
# Maven 模块化构建（只构建改动模块及其依赖，CI 提速关键）
mvn -pl module-a -am clean package    # -pl: 指定模块；-am: also-make 连同依赖模块一起构建

# Jenkins 远程触发 / 查状态
java -jar jenkins-cli.jar -s https://ci.x.com/ build job-a -p BRANCH=main -f   # 触发构建并跟随日志
java -jar jenkins-cli.jar -s https://ci.x.com/ console job-a 12                # 看某次构建控制台
```

> **面试常问场景**："大仓 monorepo 构建太慢怎么优化？""怎么在 pipeline 外触发 Jenkins 任务？"——答点：**`mvn -pl -am` 只构建受影响模块**，避免全量编译；jenkins-cli 适合在脚本 / 其他系统里驱动任务。能讲出 **-am 解决依赖链、避免漏编** 是实战经验体现。

---

## 附 B：待验证清单

| # | 待验证项 | 涉及章节 | 验证方法 |
|---|---|---|---|
| 1 | 官方当前的知识域名称、题量、总时长、及格线 | 2.2, 2.3, 3.1 | 打开 S1 官方页面核对（本篇刻意未写具体数值） |
| 2 | 官方知识域的实际配比 | 2.3 | 官方页面；本篇已标注为经验判断 |
| 3 | `etcdctl snapshot restore` 在 kubeadm 集群上的完整参数 | 2.1, Q8 | 在 kubeadm 集群演练并记录 `member list` 输出 |
| 4 | **ConfigMap 用 `subPath` 挂载时是否自动更新** | Q5 | 在 v1.37.x 上实测：改 ConfigMap 后容器内文件是否变化 |
| 5 | kind 中 kindnet → Calico 的替换步骤细节 | 2.1.2 | 本地演练一次，记录是否需要清理旧 CNI |
| 6 | `kubectl auth can-i` 在跨命名空间场景下的具体返回 | Q9 | 实测验证 |
| 7 | metrics-server 在 kind 上的就绪耗时与验证命令 | 2.1.2 | 实测记录 |

---

**上一篇**：[`L1-CKA知识点速查.md`](L1-CKA知识点速查.md) · **下一篇**：[`L2-CKA全真模拟.md`](L2-CKA全真模拟.md) · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
