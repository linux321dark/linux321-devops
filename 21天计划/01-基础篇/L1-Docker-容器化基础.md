# L1 · Docker 容器化基础（零基础 → 能讲清隔离与分层）

> **本篇定位**：从"容器是什么"到"能独立拉镜像、起容器、配网络挂存储、排常见故障"。**不假设你懂 Linux 内核。**
> **前置**：L0 的 Shell 与 YAML 基础（会管道、看得懂 yaml）。
> **学完能做什么**：说清镜像与容器的区别、容器隔离靠哪三个内核机制；独立完成镜像与容器操作；配好容器网络与存储；看懂 `docker history` 里的分层。
> **在 21 天里**：D1（Docker 多阶段构建达生产级）的基础，D2 的 kind 集群跑在 Docker 容器里。
> **环境基准**：本文命令输出均为本机实测（Docker **29.8.2**，macOS + Docker Desktop）。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L1 Docker 容器化基础
│
├─ 1. 概念
│   ├─ 1.1 容器是什么（chroot + namespace + cgroup）
│   ├─ 1.2 镜像 vs 容器：一句话分清
│   ├─ 1.3 为什么不用虚拟机
│   └─ 1.4 术语表
│
├─ 2. 机制
│   ├─ 2.1 镜像的分层结构与联合文件系统
│   ├─ 2.2 写时复制：为什么容器启动是秒级
│   ├─ 2.3 隔离三件套：namespace / cgroup / overlayfs
│   ├─ 2.4 网络：veth + bridge + iptables
│   └─ 2.5 存储：volume / bind mount / tmpfs
│
├─ 3. 动手
│   ├─ 3.1 镜像操作
│   ├─ 3.2 容器操作
│   ├─ 3.3 网络实验
│   └─ 3.4 存储实验
│
├─ 4. 关键命令逐个解释（不只给命令）
│
├─ 5. 横向对比
│
├─ 6. 踩坑表
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 容器是什么

> **官方定义（中文）**：容器是在主机上运行的**沙盒进程**，与该主机上运行的所有其他进程隔离。这种隔离利用了 **Linux 内核的 namespace 和 cgroup**——这些功能在 Linux 中已经存在很长时间了。

把它拆成三件你已经有的东西：

| 机制 | 解决什么 | 类比 |
|---|---|---|
| **chroot + 联合文件系统** | 容器看到的是自己的"根目录"，不是宿主机的 | 给进程一个假房子 |
| **namespace（命名空间）** | 进程看不到别人的进程、网卡、挂载点、主机名 | 给它一副独立眼镜 |
| **cgroup（控制组）** | 限制这个进程能用多少 CPU/内存 | 给它一个资源额度 |

> **关键认知**：容器**不是虚拟机**。它没有模拟硬件、没有独立内核，**跑的就是宿主机的内核**。所以"容器比虚拟机轻"根本原因是：**它不需要跑第二份操作系统**。
> 推论：**Linux 容器只能跑在 Linux 内核上**。Mac/Windows 上的 Docker Desktop 实际起了一台 Linux 虚机（Docker Desktop 的那个 VM），容器跑在那台虚机里。

### 1.2 镜像 vs 容器：一句话

> **镜像是模板（只读），容器是镜像的运行实例（叠加一层可写层）。**

```
镜像 Image          = 只读分层模板（可分享、可版本化）
  ↓ docker run
容器 Container     = 镜像 + 顶部可写层（读写）
  ↓ 删除容器
可写层丢弃          → 镜像完好无损
```

**这个结构解释了三件事**：
1. **为什么容器删了数据没了、镜像还在** —— 数据在容器的可写层
2. **为什么一个镜像能起 N 个容器** —— 共享底层只读层，各自一份可写层
3. **为什么容器里的改动不会污染镜像** —— 写操作落在可写层

### 1.3 为什么不用虚拟机

| 维度 | 虚拟机 | 容器 |
|---|---|---|
| 启动时间 | **分钟级**（要启动 OS） | **秒级**（只是起一个进程） |
| 体积 | GB 级（完整 OS） | MB 级（只含应用 + 依赖） |
| 隔离强度 | **强**（独立内核 + Hypervisor） | 弱（共享内核） |
| 密度 | 每台几十个 | 每台几百上千个 |
| 典型场景 | 强隔离、多 OS、基础设施 | 应用交付、微服务、弹性伸缩 |

**面试怎么答**："容器和虚拟机的本质差别是**要不要跑第二份操作系统**。容器共享宿主机内核，所以秒级启动、MB 级体积；但共享内核意味着隔离性弱于虚拟机。我们生产上用容器做应用交付，虚拟机保留给需要强隔离和多 OS 兼容的场景。"

### 1.4 术语表

| 英文 | 中文 | 一句话 |
|---|---|---|
| image | 镜像 | 只读模板，含应用 + 依赖 + 配置 |
| container | 容器 | 镜像的运行实例 |
| registry | 镜像仓库 | 存镜像的服务（Docker Hub / ACR / Harbor） |
| Dockerfile | 镜像构建描述文件 | 一步步怎么做出这个镜像 |
| layer | 层 | 镜像的每一层，只读、可缓存、可共享 |
| tag | 标签 | 镜像的版本标识（`v1` `latest`） |
| digest | 摘要 | 镜像内容的唯一哈希（**tag 可变，digest 不可变**） |
| volume | 数据卷 | Docker 管理的持久化存储 |
| bind mount | 绑定挂载 | 把宿主机某个目录直接挂进容器 |
| bridge | 桥接网络 | Docker 默认网络，容器间可互通、对外需端口映射 |
| cgroup | 控制组 | 限制资源用量的内核机制 |
| namespace | 命名空间 | 隔离进程/网络/挂载视图的内核机制 |
| overlayfs | 联合文件系统 | 把多层叠加成一个视图 |

---

## 2. 机制

### 2.1 镜像的分层结构

镜像 = **一串只读层（layer）+ 一份 manifest**。

```
manifest（描述这个镜像由哪些层组成、怎么组装）
├── Layer 1  基础系统（alpine 的根文件系统）        ← 所有基于它的镜像共享
├── Layer 2  运行时（JRE / 依赖库）
├── Layer 3  应用代码
└── Layer 4  配置（ENV/CMD/ENTRYPOINT）           ← 0 字节元数据层
```

**层的三条性质**：
1. **只读**——层一旦生成不可修改
2. **可缓存**——构建时内容没变就复用
3. **可共享**——多个镜像可共用底层（相同基础镜像时省磁盘）

> **这解释了 K8s 里"镜像越大拉得越慢"**：节点要逐层下载并解压，层数多、每层大，扩容就慢。**这是 D2 里"HPA 扩容慢"四大原因之一的根因**（另三个：指标采集 15s、HPA 同步周期、应用就绪探针）。

### 2.2 写时复制（Copy-on-Write）

容器启动时，Docker 在镜像层之上叠一个**可写层**：

```
可写层 ← 你的修改写这里
─────────────
只读层 1（基础系统）
只读层 2（运行时）
只读层 3（应用）
```

- **读**：从上往下找，命中即返回
- **写**：**复制到可写层再改**，原只读层不动

> **关键认知**：**容器改一个文件，可写层会多一份完整拷贝**。所以"往容器里写 1GB 数据"，磁盘实际增长远超 1GB（叠加覆盖）。
> **推论**：生产上不要往容器里写数据，要写 volume——**容器是可丢的，数据不该跟着容器一起丢**。

### 2.3 隔离三件套

| namespace 类型 | 隔离什么 | 效果 |
|---|---|---|
| `pid` | 进程号 | 容器内主进程是 PID 1，看不到宿主机的进程 |
| `net` | 网卡、IP、端口、路由 | 容器有自己的网络栈 |
| `mnt` | 挂载点 | 容器看到自己的根文件系统 |
| `uts` | 主机名 | 容器有独立 hostname |
| `ipc` | 共享内存、信号量 | 容器间不共享 IPC |
| `user` | UID/GID 映射 | 容器内 root ≠ 宿主机 root |

**cgroup** 管资源：CPU 份额/上限、内存上限、磁盘 IO、进程数上限。

> **面试加分点**："容器默认**不做 cgroup 限制**，也就是它可以用满宿主机 CPU 和内存直到被 OOM Killer 干掉。生产上必须给每个容器配 `resources.requests` 和 `limits`——**K8s 里不给 requests，调度器就不知道这个 Pod 有多大，调度结果会失控；不给 limits，节点内存耗尽会触发 OOM Kill，连累邻居 Pod。**"
> （这个 `limits` 的后果正是 D2 场景 B 第 6 条"HPA 不扩"的根因。）

### 2.4 网络：veth + bridge + iptables

默认的 **bridge** 网络是怎么把流量串起来的：

```
容器 A (172.17.0.2)                容器 B (172.17.0.3)
   eth0 ← veth pair → vethXXX ←┐
                                 │  docker0 (网桥，宿主上的虚拟网卡)
   宿主机 iptables DNAT 规则 ←────┘  把宿主机 :8080 改写成 172.17.0.2:8080
```

四步：
1. 每个容器起一个 **veth pair**，一端进容器当 `eth0`，另一端插在宿主机的 **docker0 网桥**上
2. 容器拿到网段 IP（默认 `172.17.0.0/16`）
3. **Docker 内置 DNS**（`127.0.0.11`）把容器名解析成 IP ——**所以同网络内可以用容器名互相访问**
4. 容器端口要对外，宿主用 **iptables DNAT** 把 `:宿主机端口` 改写成 `:容器IP:容器端口`

**三种网络模式**：

| 模式 | 容器能否互通 | 能否访问外网 | 能否被外网访问 | 用途 |
|---|---|---|---|---|
| `bridge`（默认） | ✅ 同网段可 | ✅ | ❌（需 `-p` 映射） | 常规应用 |
| `host` | ✅（直接共享宿主网络） | ✅ | ✅（**占宿主端口**） | 极致性能/抓包排查 |
| `none` | ❌ | ❌ | ❌ | 纯计算任务 |

> **K8s 里的对应关系**（别混淆，这是面试高频）：
> - Docker `bridge` ≈ K8s **Pod 网络**（CNI 插件，如 Calico）
> - Docker `host` ≈ K8s **`hostNetwork: true`**（Pod 直接用宿主网络，**用于抓包、DaemonSet 采集**）
> - K8s **Service** 又是另一层：**Pod IP 之上再做一次负载均衡**（kube-proxy 维护 iptables/IPVS）
>
> **三者的关系**：Pod IP 是"容器级"，Service VIP 是"集群级负载均衡"。**K8s 里没有 Docker 的端口映射 `-p` 那一层**，Service 替代了它。

### 2.5 存储：三种挂载

| 方式 | 数据在哪 | 容器删了 | 适合 |
|---|---|---|---|
| **volume**（数据卷） | Docker 管理的目录（Linux 在 `/var/lib/docker/volumes/`） | **保留** | 数据库数据、需持久化的 |
| **bind mount**（绑定挂载） | **宿主机任意路径** | 保留 | 挂代码、挂配置文件、挂日志目录 |
| **tmpfs** | 内存 | 保留（直到容器停） | 临时文件、加密密钥 |

> **K8s 里的对应**（这题必考）：
> - Docker volume ≈ K8s **PVC/PV**
> - Docker bind mount ≈ K8s **hostPath**（或直接挂 ConfigMap/Secret）
> - K8s 还有一类 Docker 没有的：**emptyDir**（Pod 生命周期内的临时卷，容器重建清空）——常用于 sidecar 共享临时数据
>
> **面试可讲的升级**："K8s 的 ConfigMap/Secret 底层就是投影到容器里的**只读文件**，它比 docker volume 更贴应用：应用不需要改代码，读 `/etc/config/app.yaml` 就行。"

---

## 3. 动手

### 3.1 镜像操作

```bash
docker version                          # 客户端+服务端版本（实测 29.8.2）
docker images                           # 本地镜像列表
docker pull alpine:3.20                 # 拉取
docker images --format "{{.Repository}}:{{.Tag}}  {{.Size}}"
docker rmi alpine:3.20                  # 删除镜像
docker image inspect alpine:3.20        # 看完整元数据（含分层、Env、Cmd）
docker history <image>                   # 看每一层是谁做的、多大
docker system df                        # 看空间占用
docker system prune -a                  # 清理（⚠️ 慎用，会删所有未使用镜像）
```

**本机实测输出**（用来建立"多小算小"的直觉）：

```
registry.cn-hangzhou.aliyuncs.com/linux321-devops/day1-java-demo:v20261018  A
registry.cn-hangzhou.aliyuncs.com/linux321-devops/day1-go-demo:v20261018   B
kindest/node:<none>                                                          1.3GB
hello-world:latest                                                          22.6kB
```

> **注意最后一行的量级差**：hello-world 22.6kB vs kind 节点 1.3GB。**镜像大小的差异，直接决定 K8s 扩容时拉镜像的耗时。**

### 3.2 容器操作

```bash
docker run -d --name web -p 8080:80 nginx:alpine     # 后台起 + 端口映射
docker ps                                             # 看运行中
docker ps -a                                          # 含已停止
docker logs -f web                                   # 跟日志
docker exec -it web sh                               # 进容器
docker exec web env                                  # 不进， 直接跑命令（CI 里用）
docker stop web && docker start web                   # 停 / 起
docker restart web
docker inspect web                                    # 完整元数据（IP、挂载、端口）
docker stats --no-stream                             # 实时资源占用
docker rm -f web                                     # 删
```

**`run` 的常用参数（每一个都有理由）**：

| 参数 | 作用 | 什么时候用 |
|---|---|---|
| `-d` | 后台运行 | 服务类容器（否则占着终端） |
| `--name` | 指定名字 | **不指定就是随机名，脚本里没法引用** |
| `-p 8080:80` | 宿主 8080 → 容器 80 | 需要外部访问 |
| `-e KEY=VAL` | 设环境变量 | 配置注入（**不要把密码写进镜像**） |
| `--rm` | 退出即自动删除 | 一次性任务（跑完就干净） |
| `-it` | 交互式终端 | 调试进容器 |
| `--network` | 指定网络 | 容器间互访要同网络 |
| `-v` | 挂载 | 数据持久化 |
| `--memory` `--cpus` | 资源限制 | 生产必配 |

### 3.3 网络实验

```bash
# ① 建一个自定义网络（默认 bridge 网络下容器名也能互通，但自定义网络更清晰）
docker network create lab-net

# ② 起两个容器进同一网络
docker run -d --name lab-a --network lab-net alpine:3.20 sh -c "sleep 300"
docker run -d --name lab-b --network lab-net alpine:3.20 sh -c "sleep 300"

# ③ 用【容器名】互相访问（靠 Docker 内置 DNS）
docker exec lab-a ping -c 2 lab-b

# ④ 看容器 IP
docker inspect lab-b --format '{{.NetworkSettings.Networks.lab-net.IPAddress}}'

# ⑤ 端口映射验证
docker run -d --name web -p 8080:80 nginx:alpine
curl http://127.0.0.1:8080/

# ⑥ 清理
docker rm -f lab-a lab-b && docker network rm lab-net
```

> **验证点 ③ 是本篇最重要的实验**：容器名能 ping 通，说明你理解了"**容器有自己的 DNS**"。**K8s 里 Service 的 DNS 是同一套机制**（kube-dns/CoreDNS 给 Service 分配名字），只是实现者不同。

### 3.4 存储实验

```bash
# ① 命名卷
docker volume create lab-vol
docker run --rm -v lab-vol:/data alpine:3.20 sh -c 'echo hi > /data/t.txt; ls -la /data'

# ② 看卷在宿主机的位置
docker volume inspect lab-vol --format '{{.Mountpoint}}'

# ③ 绑定挂载（挂当前目录）
echo "hello" > ./demo.txt
docker run --rm -v "$(pwd)":/work alpine:3.20 cat /work/demo.txt

# 清理
docker volume rm lab-vol
```

> **验证点**：绑定挂载后**在宿主机改文件，容器里立刻能看到**（同一份 inode）。这就是**开发时挂源码进容器做热更新**的原理。

---

## 3.5 常用运维命令面板（按场景）

> **本节能做什么**：3.1–3.4 是镜像 / 容器 / 网络 / 存储的"基础操作"，本节是**每天值班都会敲的 Docker 运维排障命令**——按场景归类，每条配真实例子。**Docker 运维的肌肉记忆就来自这一张张表。**

### ① 镜像管理

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker images` | `docker images --format '{{.Repository}} {{.Size}}'` | 看本地镜像与体积，清理前先盘点 |
| `docker rmi` | `docker rmi $(docker images -f dangling=true -q)` | 删悬空镜像，回收磁盘空间 |
| `docker tag` | `docker tag app:v1 registry.cn-hz/aliyun/app:v1` | 改名打 tag 才能推到目标仓库 |
| `docker history` | `docker history --no-trunc app:v1` | 看每一层怎么来的、占多大，定位"镜像为什么这么大" |
| `docker inspect` | `docker inspect app:v1 \| grep -i created` | 看镜像元数据（构建时间、架构） |

> **实战价值**：`docker history` 是判断镜像膨胀的第一入口——哪条指令加了 200MB 一眼可见，比盲猜快得多。

### ② 容器生命周期

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker ps` | `docker ps -a --filter status=exited` | 看所有容器（含已退出），`-a` 是排障第一步 |
| `docker run` | `docker run -d -p 8080:80 --name web nginx` | 起容器并映射端口，最常用起手式 |
| `docker stop/start` | `docker stop web && docker start web` | 优雅停 / 启，发布轮换用 |
| `docker rm` | `docker rm -f web` | 强删容器，`-f` 先 stop 再删 |
| `docker rename` | `docker rename old new` | 改容器名，避免冲突 |

### ③ 容器排障（最高频）

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker logs` | `docker logs --tail=100 -f web` | 持续看日志，`-f` 跟随，`--tail` 只看末尾 |
| `docker exec` | `docker exec -it web sh` | 进容器排障，有 bash 用 bash |
| `docker inspect` | `docker inspect web --format '{{.State.ExitCode}}'` | 看退出码 / 状态 / IP，排"容器为什么崩" |
| `docker stats` | `docker stats web` | 实时看容器 CPU / 内存 / IO 占用 |
| `docker top` | `docker top web` | 看容器内跑了哪些进程 |

> **实战价值**：容器起不来时**必须看 ExitCode**——137=OOM（加内存 limit），126/127=命令找不到或权限问题，非 0=应用自身报错。别猜，看退出码。

### ④ 资源与清理（别让死容器堆满磁盘）

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker system df` | `docker system df` | 看镜像 / 容器 / 卷占了多少空间 |
| `docker system prune` | `docker system prune -a --volumes` | 一键清悬空资源，**`-a` 连未用镜像一起删，慎用** |
| `docker volume prune` | `docker volume prune` | 清无人使用的卷，回收磁盘 |
| 批量清容器 | `docker rm -f $(docker ps -aq)` | 测试机重置用，生产禁手滑 |

### ⑤ 镜像构建

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker build` | `docker build -t app:v1 -f Dockerfile .` | 标准构建，`-f` 指定文件，`.` 是上下文 |
| `--no-cache` | `docker build --no-cache -t app:v1 .` | 强制全量重建，排查缓存导致的"旧代码还在" |
| `--target` | `docker build --target builder -t app:dev .` | 多阶段只构建到某阶段，调试中间层 |
| `buildx` | `docker buildx build --platform linux/arm64 -t app:v1 .` | 跨架构构建（L3 细讲） |

### ⑥ 网络与端口排查

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker network ls` | `docker network ls` | 看有哪些网络 |
| `docker port` | `docker port web` | 看容器端口映射关系 |
| `docker network inspect` | `docker network inspect bridge` | 看网络里有哪些容器、IP 段 |
| 进容器测连通 | `docker exec web curl -s http://other:80` | 容器间能不能通，直接在里面测 |

### ⑦ 存储挂载排查

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker volume inspect` | `docker inspect lab-vol` | 看卷在宿主机的真实路径 |
| 绑定挂载权限 | `docker run -v $(pwd):/app app` | 权限不对先查属主 / SELinux，不是镜像问题 |

### ⑧ 容器与宿主机互拷文件

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker cp` 出 | `docker cp web:/var/log/app.log ./` | 把容器内日志拷出来分析 |
| `docker cp` 入 | `docker cp ./fix.sh web:/tmp/` | 拷修复脚本进容器临时救火 |

---

## 3.6 入门排障实战（3 个场景串命令）

> **把 3.5 的命令用起来**：三个最典型的入门故障，按"现象 → 敲的命令 → 怎么读"走一遍。**都在本地 Docker 上练，别在真集群试。**

### 场景 A：容器一直退出 / Restarting（起不来）

```bash
docker ps -a                                                 # 现象：STATUS=Exited(1) 或 Restarting
docker logs --tail=50 web                                    # 看退出前日志，找报错
docker inspect web --format '{{.State.ExitCode}} {{.State.Error}}'   # 看退出码
# Exit 137 = OOM（加内存）；非 0 = 应用自身报错（看日志）
```

> **读法**：`docker ps -a` 拿到退出码，`logs` 看崩溃原因。137=OOM，126/127=命令找不到或权限，1=应用报错。别猜，看 ExitCode。

### 场景 B：镜像体积异常大（构建没瘦身）

```bash
docker images app            # 看体积
docker history app:v1        # 逐层看哪层暴涨
docker build --no-cache -t app:test .   # 怀疑缓存作祟时全量重建对比
```

> **读法**：`history` 直接告诉你"哪条指令加了 200MB"。多半是忘了多阶段（L2 解决）或装了包没清索引。

### 场景 C：容器间网络不通（A 访问不了 B）

```bash
docker network ls                       # 确认两容器在同一网络
docker network inspect mynet            # 看各自 IP
docker exec a curl -s http://b:80       # 进容器直连，绕过 DNS 先验应用
docker exec a nslookup b                # 再看 DNS 通不通
```

> **读法**：默认 bridge 下容器名互通需同自定义网络；先端口直连验应用本身，再验 DNS，分层定位。

> **三个场景的共同心法**：**先 `ps`/`logs` 看状态与日志 → 再 `inspect`/history 看原因 → 最后才是修**。和 K8s 篇一致：证据优先。

---

## 4. 关键命令逐个解释

### 4.1 `docker run` 的执行顺序（很多人不知道）

`docker run` 其实是**一条命令干六件事**：

```
docker run -d --name web -p 8080:80 nginx:alpine
   │
   ├─ 1. 若本地无镜像 →  pull
   ├─ 2. 创建容器（可写层 + 配置）
   ├─ 3. 分配 IP、配置 DNS、设置网络
   ├─ 4. 创建挂载点、挂载卷
   ├─ 5. 分配资源（cgroup）
   ├─ 6. 执行 ENTRYPOINT/CMD  ← 进程成为容器 PID 1
```

**PID 1 这件事必须记住**：容器里的第一个进程就是 PID 1，它**不会自动处理 SIGTERM**。K8s 删除 Pod 时发 SIGTERM，如果你的应用是 shell 形式启动的，shell 不转发信号给子进程 → **应用收不到 SIGTERM → 等 30 秒 grace period 到了被 SIGKILL 强杀** → 请求被硬切，用户看到 502。

> 这就是 D2 场景 B 第 4 条"缩容把长连接打断"的机制。正确解法见 L2 的 ENTRYPOINT/CMD 章节。

### 4.2 `docker inspect` 看什么

```bash
docker inspect <name> --format '{{.State.Status}}'          # 状态
docker inspect <name> --format '{{.Networks.IPAddress}}'
docker inspect <name> --format '{{json .Config.Env}}'        # 环境变量
docker inspect <name> --format '{{.Mounts}}'                 # 挂载
docker inspect <name> --format '{{.State.ExitCode}}'        # 退出码
```

> **退出码是排障金矿**：容器启动后立刻退出，`ExitCode=0` 说明是"正常执行完退出"（命令写错），`ExitCode=1/2` 说明程序报错。`docker logs` 有时是空的（因为 stdout 没内容），**这时 `ExitCode` 能告诉你方向**。

### 4.3 `docker history` 看懂分层

**本机对 Java 业务镜像的实测输出**（节选，完整见 L2）：

```
0B      ENTRYPOINT ["java" "-jar" "/app/app.jar"]
0B      EXPOSE [8080/tcp]
0B      USER 1000
20.7MB  COPY /app/target/*.jar app.jar
8.19kB  WORKDIR /app
141MB   RUN ... 下载 Temurin OpenJDK 17 JRE ...
44MB    RUN ... apk add fontconfig gnupg ca-certificates ...
```

**读法**：
- `0B` 的是**元数据层**（CMD/ENV/EXPOSE/USER/WORKDIR/LABEL）——不占空间
- 有体积的是**文件层**
- **从上往下是构建顺序的逆序**（最后构建的在上）
- `141MB` 是 JRE、`44MB` 是 apk 装的包、`20.7MB` 是你的 jar —— **这三项就是 A 的全部来源**

> **这直接告诉你瘦身该动哪里**：不是删你的应用（20.7MB），而是**换更小的运行时 + 精简基础镜像**。这就是 L2 多阶段构建要解决的问题。

---

## 5. 横向对比

### 5.1 存储方式

| 维度 | volume | bind mount | tmpfs |
|---|---|---|---|
| 位置 | Docker 管理（Linux `/var/lib/docker/volumes/`） | 宿主机任意路径 | 内存 |
| 跨宿主机迁移 | ✅ 可迁移 | ❌ 绑死路径 | — |
| 容器删了 | **数据保留** | 保留 | 保留 |
| 性能 | 磁盘 | 磁盘 | **内存，最快** |
| 权限 | Docker 管的 uid | 宿主真实权限 | — |
| 典型用途 | DB 数据 | 开发挂代码、挂配置 | 临时文件、密钥 |

### 5.2 网络模式

| 维度 | bridge | host | none |
|---|---|---|---|
| 隔离 | 有独立网络栈 | **完全共享** | 只有 loopback |
| 端口 | 需 `-p` 映射 | **直接占宿主端口** | 无 |
| 性能 | 有 NAT 开销 | **最高** | — |
| 安全性 | 较好 | **差**（暴露宿主所有服务） | 高 |
| 何时用 | 默认 | 抓包/高性能 | 纯计算 |

### 5.3 容器 vs 虚机 vs Pod

| 维度 | Docker 容器 | 虚拟机 | K8s Pod |
|---|---|---|---|
| 谁调度 | 你手动 `docker run` | 你手动开虚机 | **kube-scheduler 自动** |
| 网络单位 | 容器 IP | 虚机 IP | **Pod IP** |
| 服务发现 | Docker 内置 DNS（容器名） | 自己做 | **Service + CoreDNS** |
| 负载均衡 | 靠 `-p` 映射 | 自己做 | **kube-proxy（Service VIP）** |
| 自愈 | 无（挂了就是挂了） | 无 | **kubelet 重启容器** |
| 扩缩容 | 手动 | 手动 | **HPA 自动** |

> **这张表就是 D2 要回答的问题**："容器解决了什么、K8s 又解决了什么"——**K8s = 容器 + 调度 + 自愈 + 弹性 + 服务发现**。面试能把这句话讲清楚，比背一堆 Pod 字段有用。

---

## 6. 踩坑表

| 现象 | 真实原因 | 怎么解 | 下次怎么防 |
|---|---|---|---|
| `docker pull` 卡住 / 超时 | **国内直连 Docker Hub 慢**（本机实测拉 `alpine:3.20` 超时） | 配镜像加速器（阿里云个人版加速器地址） | 提前配好 `/etc/docker/daemon.json` 的 `registry-mirrors` |
| `docker pull` 报 `toomanyrequests` | Docker Hub 匿名拉取限流 | 登录账号 / 用 ACR 等私有仓库 | 开发机统一走国内仓库 |
| `imagePullBackOff`（K8s 里） | 私有仓库没凭据 | `kind load` 或建 imagePullSecret | 见 L2 / D2 文档 |
| 容器起不来，`docker logs` 是空的 | 程序 stdout 没输出，或直接退出 | 看 `docker inspect` 的 `State.ExitCode` | 退出码非 0 = 程序错，= 0 = 命令写错 |
| 容器一直 `Restarting` | 健康检查失败 / 入口命令错 | `docker logs --tail 50` | 入口命令用 exec 形式 `CMD ["app"]` |
| 容器内 `ping` 不通另一个容器 | 不在同一网络 | `--network` 相同网络名 | 建自定义网络，别用默认 bridge |
| 改了容器里的文件，重启就没了 | 写在容器可写层 | 用 volume / bind mount | **数据一律外挂** |
| 磁盘越来越满 | 悬空镜像/停止的容器/构建缓存 | `docker system df` → `docker system prune` | 定期清理，CI 里加清理步骤 |
| Mac 上 `exec format error` | 拉了 amd64 镜像，Mac 是 arm64 | 指定平台：`docker build --platform linux/amd64` | 生产统一 amd64；开发机注意架构 |
| 容器内 `127.0.0.1` 连不到自己起的服务 | 容器内是**容器自己**的 loopback | 用容器名或容器 IP | 容器间用名字 |
| 端口映射失败但容器正常 | 宿主端口被占 | `lsof -i :8080` 查占用 | 换端口或先杀占用进程 |

> **最后一条很多人栽过**：容器里的服务监听 `127.0.0.1` 时，**从宿主机 `curl localhost:8080` 是通的，但容器之间访问不到**。因为服务只绑了 loopback。**服务应该监听 `0.0.0.0`**，这是 K8s 里 Service 访问 Pod 失败的一个常见根因。

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `docs.docker.com/get-started/` | 容器与镜像概念 | ✅ 2026-10-24 |
| S2 | 官方 | `docs.docker.com/engine/network/` | 网络模式与实现 | ✅ 2026-10-24 |
| S3 | 官方 | `docs.docker.com/engine/storage/` | 存储与卷 | ✅ 2026-10-24 |
| S4 | 官方 | `docs.docker.com/compose/` | Compose | ✅ 2026-10-24 |
| S5 | 官方 | `docs.docker.com/registry/` | 镜像仓库 | ✅ 2026-10-24 |
| S6 | **中文** | **`docker.c-cpp.com/get-started/overview/`** | **中文入门（概念定义原文）** | ✅ 2026-10-24 |
| S7 | **中文** | **`docker.c-cpp.com/guides/get-started/`** | **官方入门教程（中文）** | ✅ 2026-10-24 |
| S8 | **中文** | **`docker.c-cpp.com/manuals/`** · **`/reference/`** | **手册与 CLI 参考（中文）** | ✅ 2026-10-24 |
| S9 | **中文** | `docker.c-cpp.com/engine/install/` | 安装（中文） | ✅ 2026-10-24 |
| S10 | **中文** | `docker.c-cpp.com/compose/gettingstarted/` | Compose 快速上手（中文） | ✅ 2026-10-24 |
| S11 | 云厂商 | `help.aliyun.com/zh/acr/` | ACR 私有仓库与镜像加速 | ✅ 2026-10-24 |
| S12 | 云厂商 | `developer.aliyun.com/mirror/docker-ce/` | 阿里云软件镜像站 | ✅ 2026-10-24 |

> **S6 是本篇容器定义段落的直接来源**（"容器是在主机上运行的沙盒进程……利用 Linux 内核的 namespace 和 cgroup"），**中文原文出自 Docker 官方文档中译**。
> **链接实测方法见** [`../09-参考索引/README.md`](../09-参考索引/README.md)（含重试脚本，**000 是网络抖动不是失效**）。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入：镜像 + 运行时配置；输出：一个隔离的沙盒进程 + 一个可写层。中间经过：拉镜像 → 建容器 → 配网络/挂载/资源 → 执行 ENTRYPOINT。
2. **能故意改坏再倒推吗？** —— 三个实验：① 容器里 `ping` 另一个容器不通 → 网络没在同一网段；② 容器里写文件、重启后没了 → 写在可写层；③ `docker inspect` 看 `ExitCode` 分别为 0 和 1 → 区分"命令写错"和"程序报错"。
3. **能画上下游吗？** —— 画出「docker run → pull → 建容器 → veth 接入 docker0 → iptables DNAT → 宿主端口 → 容器端口」这条链，并标出每一步失败会看到什么现象。

---

**下一篇**：[`L2-Dockerfile与多阶段构建`](L2-Dockerfile与多阶段构建.md)（**已写** · 镜像瘦身 A → B 的真实路径） · **上一级**：[`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md)
