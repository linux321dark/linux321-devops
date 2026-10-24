# L0 · 前置基础：Linux / Shell / YAML / 网络（零基础起点）

> **本篇定位**：整套资料的**地板**。云原生所有工具最终都落在这三样上——命令在 shell 里跑、配置是 YAML、组件靠网络连。
> **前置**：无。这是第 0 级。
> **学完能做什么**：看懂 yaml 不报错；会用管道/重定向组合命令；能解释"域名解析到 IP 再连端口"这条链路。
> **在 21 天里**：D1 写 Dockerfile 前的 shell 基础、D2 的 yaml 与 kubectl，都建立在本篇。
> **本文所有报错原文均为本地实测**（`kubectl v1.37.1`，kind 集群），不是抄文档。

---

## 0. 知识图谱

```
L0 前置基础
│
├─ 1. Shell：命令怎么组合
│   ├─ 1.1 三种执行流：命令 / 管道 / 重定向
│   ├─ 1.2 管道与管道的"文本流"思维
│   ├─ 1.3 常用文本三剑客：grep / awk / sed
│   └─ 1.4 退出码、&& 与 ||
│
├─ 2. YAML：K8s 的唯一输入格式
│   ├─ 2.1 缩进即结构
│   ├─ 2.2 类型陷阱：数字、布尔、字符串
│   ├─ 2.3 多文档：---
│   ├─ 2.4 校验：client vs server 两种 dry-run
│   └─ 2.5 锚点与合并（进阶）
│
├─ 3. 网络：组件之间怎么连
│   ├─ 3.1 IP 与端口
│   ├─ 3.2 DNS：名字到 IP
│   ├─ 3.3 七层与四层
│   └─ 3.4 集群内 DNS（Service 名字怎么解析）
│
├─ 4. 动手验证
├─ 5. 横向对比
├─ 6. 踩坑表（实测报错原文）
├─ 7. 参考来源
└─ 8. 自测三问
```

---

## 1. Shell：命令怎么组合

### 1.1 三种执行流

| 形式 | 符号 | 干什么 |
|---|---|---|
| **顺序** | `cmd1; cmd2` | 都执行，不管成败 |
| **管道** | `cmd1 \| cmd2` | cmd1 的**标准输出**接到 cmd2 的**标准输入** |
| **重定向** | `cmd > file` / `cmd >> file` / `cmd < file` / `cmd 2> file` | 改变输入输出的去向 |

**三个标准流**（理解管道就靠这个）：

```
        ┌──────────┐
  0 ───►│  stdin   │  输入（默认：终端）
        ├──────────┤
  1 ───►│  stdout  │  正常输出（默认：终端）
        ├──────────┤
  2 ───►│  stderr  │  错误输出（默认：终端）
        └──────────┘
```

**管道只连 stdout**。所以 `cmd 2>&1` 的意思是"**把错误输出也重定向到 stdout**"，这样错误才能被管道下游接住——这个写法在容器里捞日志时天天用。

> 实战记忆点：`docker logs xxx 2>&1 | grep ERROR` 里的 `2>&1` 不写，`grep` 抓不到错误日志。

### 1.2 管道的"文本流"思维

**每个命令只干一件事，处理文本，吐出来给下一个。** 这不是比喻，是 Unix 设计的核心。

```
kubectl get pods -o json | jq '.items[].status.phase' | sort | uniq -c
     ↑ 取json          ↑ 取phase字段            ↑ 排序    ↑ 计数
```

读法：*先拿到全部 Pod 的 json → 从里面抽出 phase → 排序 → 统计每个出现了几次。*

**`sort | uniq -c` 是最高频的组合**，任何"统计出现次数"的需求都是它。

### 1.3 文本三剑客

| 工具 | 干什么 | 什么时候用 |
|---|---|---|
| `grep` | 按行**筛**（找包含某内容的行） | 找关键字、过滤噪音 |
| `awk` | 按列**拆**（列式处理，能算能筛） | 提取第 N 列、做统计 |
| `sed` | 按行**改**（替换/删除/插入） | 批量改文本 |

**awk 的核心概念**：`-F` 指定分隔符，`$1` 是第一列，`$0` 是整行。

```bash
# 打印第 3 列（空白分隔）
awk '{print $3}' file.txt
# 按冒号分隔，打印冒号后的内容
awk -F: '{print $2}' /etc/passwd
# 打印第 1 列且按数值大小过滤（第二列 > 100）
awk '$2 > 100 {print $1}' file.txt
# 求和
awk '{s+=$1} END {print s}' file.txt
```

> **`awk '$2 > 100'` 里的 `$2` 是数值比较还是字符串比较？** awk 会自动判断：字段看起来像数字就按数值比。**但 `"100abc"` 这种混合的会按字符串比**，这是坑。保险写法：`awk '$2+0 > 100'`（`+0` 强制转数字）。

### 1.4 退出码、&& 与 ||

**每个命令执行完都会返回一个数字（退出码）**：`0` = 成功，非 0 = 失败。

```bash
cmd1 && cmd2    # cmd1 成功（退出码 0）才执行 cmd2
cmd1 || cmd2    # cmd1 失败才执行 cmd2
cmd1 ; cmd2     # 都执行
```

**这个在脚本里是"短路保护"**：

```bash
# 先检查连通性，通了再删
kubectl get nodes && kubectl delete pod <name>
```

**`&&` 在 CI 流水线里更关键**：D4 的 GitLab CI 里，每个 stage 失败就要中止后面的步骤，靠的就是退出码。

> **面试可讲的点**：容器里 `ENTRYPOINT` 的 PID 1 语义、shell 形式 vs exec 形式（`CMD ["a","b"]` vs `CMD a b`）——**用 shell 形式会多一层 sh，让信号传递和 PID 1 收不到 SIGTERM**，这是 K8s 里优雅停机失败的一个常见根因。这条在 D1 讲 Dockerfile 时会用上。

---

## 2. YAML：K8s 的唯一输入格式

### 2.1 缩进即结构

YAML **不用括号**，靠缩进表达层级。规则只有三条，但错一个就全盘崩：

1. **同一层级的键，缩进必须一致**（空格数可以不同，但要对齐）
2. **只能用空格，不能用 Tab**（实测报错见第 6 节）
3. **`-` 表示列表项**，其后跟一个空格再写内容

```yaml
spec:
  replicas: 1
  template:
    spec:
      containers:
      - name: app        # ← 列表项，缩进与 containers 同级
        image: nginx
```

> **最容易错的点**：列表项 `- name` 的缩进应该**和它的父键同列**（`containers:` 和 `- name` 对齐），而不是多缩进一格。写成多一格 YAML 仍合法，但结构完全变了。

### 2.2 类型陷阱

YAML **不标类型，靠值的样子猜**。这带来 K8s 里最常见的报错：

| 写法 | 被解析成 | 后果 |
|---|---|---|
| `port: 8080` | 数字 int | ✅ 数字字段没问题 |
| `port: "8080"` | 字符串 | 数字字段（如 `ServicePort.port`）→ **apiserver 直接拒绝** |
| `enabled: true` | 布尔 | ✅ |
| `enabled: "true"` | 字符串 | 布尔字段 → 拒绝 |
| `bad: 08080` | 数字（前导 0 也会被当数字） | ❌ 报错 |

**实测报错原文**（`--dry-run=server`）：

```
Error from server (BadRequest): error when creating "types.yaml":
ConfigMap in version "v1" cannot be handled as a ConfigMap:
json: cannot unmarshal number into Go struct field ConfigMap.data of type string
```

> **⚠️ 关键结论，和很多人的认知相反**：
> **K8s 不会把数字自动转成字符串。** `ConfigMap.data` 的 schema 是 `map[string]string`，你写 `port: 8080`（数字），apiserver **直接拒绝**。
> 而 `kubectl apply --dry-run=client` **不会报错**——它只在本地做语法解析，**不做类型转换**。所以本地校验通过、真 apply 上去才炸。

### 2.3 多文档：`---`

一个文件里放多个 K8s 对象，用 `---` 分隔。`kubectl apply -f` 会全部处理。

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: demo
data:
  a: "1"
---
apiVersion: v1
kind: Service
metadata:
  name: demo-svc
spec:
  ports:
  - port: 80
```

**实测**：`kubectl apply -f multi-doc.yaml --dry-run=client` 输出
```
configmap/demo created (dry run)
service/demo-svc created (dry run)
```
→ **一个文件能创建多个对象，这就是 D2 里 `go-demo.yaml` 同时写 Deployment + Service 的原因。**

### 2.4 校验：两种 dry-run 的区别（**本篇最实用的知识点**）

| 命令 | 走不走 apiserver | 查什么 | 什么时候用 |
|---|---|---|---|
| `--dry-run=client` | **不走** | 语法、拼写、字段名是否存在（本地 schema） | **写完立刻自查**，秒回 |
| `--dry-run=server` | **走**（完整链路） | 类型转换、准入控制、冲突、业务校验 | **提交前最后一道**，能抓出 client 漏的类型错 |
| `--validate=strict`（默认） | — | 未知字段直接报错 | 保持默认 |
| `--validate=false` | — | 跳过未知字段检查 | 极少用，会掩盖拼写错误 |

**推荐工作流**：

```bash
kubectl apply -f x.yaml --dry-run=client    # 1. 秒查语法
kubectl apply -f x.yaml --dry-run=server    # 2. 查类型与准入
kubectl apply -f x.yaml                     # 3. 真提交
```

> **为什么这条值得记住**：它解释了为什么你 `kubectl get --dry-run` 一路绿灯，apply 却报 `cannot unmarshal`。**两个 dry-run 不是一回事，这是很多人踩坑的根源。**

### 2.5 锚点与合并（进阶，先会读就行）

```yaml
x-common: &common
  image: nginx:1.25
  resources:
    requests: { cpu: "100m", memory: "128Mi" }
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  template:
    spec:
      containers:
      - <<: *common          # ← 合并锚点
        name: web
```

**用途**：同一份配置里多个对象共享字段时避免重复。**注意**：K8s 对 `<<` 合并的支持是"客户端解析时展开"，服务端看到的是展开后的结果。

---

## 3. 网络：组件之间怎么连

### 3.1 IP 与端口

- **IP**：机器（或虚拟网卡）的地址
- **端口**：机器上进程的编号（0-65535）
- **一个进程 = IP + 端口** 的组合，定位到网络上唯一的一个服务

**容器为什么需要端口映射**：容器的网络是**独立的网络命名空间**，容器里的 8080 在宿主机上不是 8080。`ports` 声明 + CNI 插件的转发规则，把宿主机端口接到容器端口。

> 这就是为什么 K8s 里 Service 的 `targetPort` 必须和容器 `containerPort` 对得上——**它们是同一条链路的两端**。

### 3.2 DNS：名字 → IP

人记不住 IP，所以用名字。DNS 就是一个"名字查号台"。

**解析顺序（从近到远）**：
1. `/etc/hosts`
2. 本机 DNS 缓存
3. 主机名服务器（`/etc/resolv.conf` 里配的）
4. 根域名服务器 → 顶级域 → 权威 DNS

**排障第一步永远是 `dig` / `nslookup`，不是 ping**：
```bash
dig demo-svc.default.svc.cluster.local
nslookup demo-svc.default.svc.cluster.local
```
**ping 通但域名不通** → 是 DNS 问题；**域名通但连不上** → 是端口/进程问题。**这两个必须分开定位。**

### 3.3 七层 vs 四层

| 层 | 协议 | 能看什么 | 谁在用 |
|---|---|---|---|
| **四层（L4）** | TCP/UDP，只看 IP:端口 | 连接、字节数 | **Service / kube-proxy / CLB** |
| **七层（L7）** | HTTP，能看 URL、Header、Body | 方法、路径、状态码、Cookie | **Ingress / ALB** |

**为什么 K8s 要分两层**：
- Service 是**四层**的 → 快、便宜，但**不知道 URL**，不能按路径路由
- Ingress 是**七层**的 → 能按域名和路径路由，但要靠 Ingress Controller 真去实现

> **面试可讲的点**：本地民企对外暴露用 ACK 的 ALB Ingress（七层，挂在 SLB 后面），SLB 本身是四层。**"SLB 在前、ALB 在后"这个结构能在面试里画出来，是加分项。**

### 3.4 集群内 DNS：Service 名字怎么解析

K8s 集群里跑着 **CoreDNS**（kube-dns 的替代品），它给每个 Service 分配一个 DNS 名：

```
<service>.<namespace>.svc.cluster.local
```

**实测**（本机 kind 集群）：
```bash
kubectl get svc
# day1-go-svc   ClusterIP   10.96.165.172   80/TCP
```
→ 这个 Service 在集群内的稳定名字是 `day1-go-svc.default.svc.cluster.local`，短写 `day1-go-svc`（同命名空间内）。

**Headless Service（`clusterIP: None`）是例外**：它不给 VIP，DNS 直接返回**所有 Pod IP 的列表**。**有状态服务（StatefulSet）必须用 Headless**，因为它需要每个 Pod 有稳定、可单独寻址的 DNS 名（`mysql-0.mysql-headless.ns.svc.cluster.local`）。

---

## 4. 动手与速查

### 4.1 YAML 速查表（写错的后果都在这）

| 写法 | 解析成 | 合法性 | 写错的后果 |
|---|---|---|---|
| `port: 8080` | 数字 int | ✅ | 数字字段正确 |
| `port: "8080"` | 字符串 | ✅ 合法但类型错 | 数字字段（如 `ServicePort.port`）**apiserver 直接拒绝** |
| `enabled: true` | 布尔 | ✅ | 布尔字段正确 |
| `enabled: "true"` | 字符串 | ✅ 合法但类型错 | 布尔字段被拒绝 |
| `bad: 08080` | 数字（前导 0 也按数字解析） | ✅ 合法但易误 | **报错**，且不是你以为的字符串 |
| `data: {a: 1}` | 流式映射 | ✅ | 等价于换行写法，**冒号后必须有空格** |
| `key: value`（冒号后无空格） | **被当成一个字符串** | ✅ 但不是你要的 | **静默错误**，字段名变成 `key: value` |
| `a: 1` 写两遍 | 取**最后一个** | ✅ | **静默错误**，不报错（见第 6 段） |
| 用 Tab 缩进 | — | ❌ | 解析直接失败 |

> **最该记的一条**：**YAML 只有两种失败方式——要么当场报错，要么静默按错误的结构解析。** 后者更危险，因为 `apply` 会成功。

### 4.2 动手验证

### 4.1 shell 组合命令

```bash
# 统计 Pod 的 phase 分布（最高频组合）
kubectl get pods -o json | jq -r '.items[].status.phase' | sort | uniq -c

# 找出所有非 Running 的 Pod
kubectl get pods --no-headers | awk '$3 != "Running" {print $1, $3}'

# 按 CPU 排序看最占资源的 Pod
kubectl top pods --no-headers | sort -k2 -rn | head -5

# 从 yaml 里取镜像名
kubectl get deploy day1-go -o jsonpath='{.spec.template.spec.containers[*].image}'
```

### 4.2 yaml 校验三连

```bash
kubectl apply -f go-demo.yaml --dry-run=client
kubectl apply -f go-demo.yaml --dry-run=server
kubectl apply -f go-demo.yaml
```

### 4.3 DNS 验证

```bash
# 起一个临时 Pod 测集群内 DNS
kubectl run dns-test --rm -it --restart=Never --image=busybox:1.36 -- sh -c \
  'nslookup day1-go-svc.default.svc.cluster.local; nslookup kubernetes.default.svc.cluster.local'
```

> `kubernetes.default.svc.cluster.local` 指向 apiserver 的 Service，**在 Pod 里能解析它就说明集群 DNS 正常**——这是排查"Pod 里访问不了任何 Service"的第一步。

---

## 5. 横向对比

| 问题 | 方案 A | 方案 B | 怎么选 |
|---|---|---|---|
| 解析名字 | `/etc/hosts` 静态 | DNS 服务器 | **集群内必须用 DNS**，hosts 不会自动跟着 Service 变 |
| 暴露服务 | Service ClusterIP | Ingress | 内→外用 Ingress，集群内用 Service |
| 找问题 | `ping` | `dig`/`nslookup` | **先 dig 再 ping**，顺序反了会误判 |
| 文本处理 | `grep` | `awk` | 筛行用 grep，拆列/算数用 awk |
| yaml 校验 | `--dry-run=client` | `--dry-run=server` | **两个都跑**，client 快、server 准 |
| 改配置 | `kubectl edit`（改集群） | 改 yaml 再 `apply`（改代码） | **生产永远改 yaml**，edit 出来的改动下次 apply 就被覆盖 |

> 最后一行是**面试高频且实用**的：`kubectl edit` 改的东西不在 Git 里，下一次 CI/CD apply 会把它**改回去**。**生产变更必须走 yaml + apply（或者 GitOps），这是 GitOps 存在的根本原因。**

---

## 6. 踩坑表（报错原文均为本地实测）

| 你写的 | 报错原文（实测） | 原因 | 怎么防 |
|---|---|---|---|
| 用 Tab 缩进 | `error parsing xxx.yaml: error converting YAML to JSON: yaml: line 6: found character that cannot start any token` | YAML **禁止 Tab 缩进** | 编辑器设成空格、宽度 2 |
| ConfigMap 里写数字 | `Error from server (BadRequest): ... json: cannot unmarshal number into Go struct field ConfigMap.data of type string` | schema 是 `map[string]string`，**不自动转** | **字符串值一律加引号** |
| 端口写成字符串 | `... json: cannot unmarshal string into Go struct field ServicePort.spec.ports.port of type int32` | 数字字段要数字 | 端口、replicas、副本数都不加引号 |
| label 值含空格 | `metadata.labels: Invalid value: "My App": a valid label must be an empty string or consist of alphanumeric characters, '-', '_' or '.', and must start and end with an alphanumeric character` | label 有严格字符集 | label 只用小写字母、`-`、`_`、`.` |
| **同一个 key 写两遍** | **无报错**（`configmap/t4 created (server dry run)`） | YAML 解析器**取最后一个值**，K8s 不校验重复 key | **静默陷阱**：你以为 `a: "1"`，实际生效 `a: "2"`。写完扫一眼 |
| 列表项多缩进一格 | 无报错，**结构完全变样** | YAML 合法但层级不同 | `- ` 与父键对齐 |
| `kubectl get --dry-run` 绿灯但 apply 报错 | — | client 不做类型转换 | **提交前加一次 `--dry-run=server`** |

> **第 5 行是本篇最值得记住的一条**：所有其他错误都会报错，**只有重复 key 静默生效错的值**。这类错在生产上会以"配置怎么没生效"的形式出现，极难定位。

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | kubernetes.io/zh-cn/docs/tasks/ | kubectl 命令与 dry-run 用法 | 见 `../09-参考索引/README.md` |
| S2 | 官方 | kubernetes.io/docs/concepts/services-networking/dns/ | 集群 DNS 与 Service 命名 | 见 `../09-参考索引/README.md` |
| S3 | 中文 | docs.docker.com/zh-cn/ | 容器网络与端口映射（L1 衔接） | 见 `../09-参考索引/README.md` |
| S4 | 社区 | 阿里云开发者社区 / 腾讯云开发者社区 | CoreDNS 排障实践 | 见 `../09-参考索引/README.md` |

> **本文所有 YAML 报错原文为本地实测**，不依赖文档转述——这是本篇与网上教程最大的区别。
> 完整来源矩阵见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 管道把 stdout 接到 stdin；`2>&1` 把 stderr 并进 stdout；YAML 的缩进决定 JSON 的嵌套层级；DNS 把名字解析成 IP，端口定位到进程。
2. **能故意改坏再倒推吗？** —— 三个实验：① ConfigMap 里写数字 → 看 server dry-run 报 `cannot unmarshal number`；② **写两个相同的 key** → 看它安静地取了最后一个；③ 把 label 值改成带空格 → 看正则报错原文。**第二个实验最关键——它是唯一不报错的错误。**
3. **能画上下游吗？** —— 画出「浏览器 → DNS 查名字 → 得到 IP → 连端口 → Nginx/Ingress(L7) → Service VIP(L4) → kube-proxy 转发 → Pod 端口」这条链，并标出每一段失败会看到什么现象。

---

**下一篇**：[`L1-Docker-容器化基础`](L1-Docker-容器化基础.md) · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md)
