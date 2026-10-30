# L2 · Dockerfile 与多阶段构建（为什么这么写 · A → B）

> **本篇定位**：**不重复给 Dockerfile 成品**——D1 日课里已经给了两份可用的。本篇逐行拆解它们**为什么这么写**，以及背后的构建机制。
> **前置**：L1（镜像分层、容器隔离、缓存与写时复制）。
> **学完能做什么**：解释 Dockerfile 每一层在干什么；读懂 `docker history` 定位体积；自己设计出更小更安全的镜像构建方案；讲清构建缓存的失效规则。
> **在 21 天里**：D1 的理论支撑；D2 的"镜像大小直接决定扩容速度"这条根因。
> **实例基准**：本文拆解的两份 Dockerfile 就是 D1 日课里那两份，**实测产物 A（Java）与 B（Go）**。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L2 Dockerfile 与多阶段构建
│
├─ 1. 概念
│   ├─ 1.1 Dockerfile 是什么
│   ├─ 1.2 三个真实收益（体积 / 攻击面 / 扩容速度）
│   └─ 1.3 实测对比：A vs B
│
├─ 2. 机制
│   ├─ 2.1 构建过程：一条指令 = 一层
│   ├─ 2.2 缓存机制与失效规则（最容易翻车的一节）
│   ├─ 2.3 多阶段是怎么实现的
│   ├─ 2.4 基础镜像选型：scratch / alpine / distroless / slim
│   └─ 2.5 ENTRYPOINT vs CMD 与 PID 1 信号传递
│
├─ 3. 动手：逐行拆解 D1 的两份 Dockerfile
│   ├─ 3.1 Java 多阶段（Spring Boot）
│   └─ 3.2 Go 多阶段 → scratch
│
├─ 4. Dockerfile 指令逐字段表
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

### 1.1 Dockerfile 是什么

> **一句话**：Dockerfile 是**镜像的构建说明书**——一串顺序执行的指令，每条指令产生一层。

它不是脚本（没有流程控制、没有变量回填），它是一份**声明式的构建配方**。执行它的是 BuildKit，产出的是一个分层镜像。

### 1.2 多阶段构建解决三个真问题

| 收益 | 解释 | 量化 |
|---|---|---|
| **① 体积** | 编译工具链（Maven/JDK/Go 编译器）**不该出现在运行镜像里** | 实测：**A → B** |
| **② 攻击面** | 运行时镜像里的每个二进制都是潜在漏洞入口。JRE 141MB + apk 装的 gnupg/openssl，**全是攻击面** | 删掉即减少 |
| **③ 扩容速度** | K8s 扩容要拉镜像，**层越大拉得越慢** | 这是 D2「扩容慢」四大原因之一 |

> **第三条最容易被忽略，但面试价值最高**："HPA 扩容生效要 1–2 分钟，时间花在哪？——**四段**：指标采集 15s + HPA 同步周期 + **调度与拉镜像**（这一步镜像越大越慢，A 和 V 差一个数量级）+ 应用就绪探针。**所以镜像瘦身不是洁癖，是直接影响弹性时效的工程动作。**"

### 1.3 实测对比（本机真实产物）

```
registry.cn-hangzhou.aliyuncs.com/linux321-devops/day1-java-demo:v20261018  A
registry.cn-hangzhou.aliyuncs.com/linux321-devops/day1-go-demo:v20261018   B
```

**用 `docker history` 归因（这 A 花在哪）**：

| 层 | 体积 | 内容 | 能不能去掉 |
|---|---|---|---|
| `ENTRYPOINT`/`EXPOSE`/`USER` | 0B | 元数据 | — |
| `COPY /app/target/*.jar app.jar` | **20.7MB** | 你的应用 | ❌ 必须要 |
| `WORKDIR /app` | 8.19kB | 工作目录 | 可忽略 |
| `RUN ... 下载 Temurin OpenJDK 17 JRE` | **141MB** | **Java 运行时** | ⚠️ 换更小的 JRE |
| `RUN ... apk add fontconfig gnupg ca-certificates ...` | **44MB** | **apk 装的包** | ⚠️ **这里最该砍** |
| `ENV` 层 | 0B | 环境变量 | — |

**Go 镜像的构成（同样 5 层，B）**：

| 层 | 体积 | 内容 |
|---|---|---|
| `CMD ["/bin/app"]` / `EXPOSE` / `USER 65532:65532` | 0B | 元数据 |
| `COPY /etc/ssl/certs/ca-certificates.crt /etc…` | **242kB** | 只拷了 CA 证书 |
| `COPY /bin/app /bin/app` | **8.39MB** | 只有你的二进制 |

> **对比结论**：
> Java 镜像的 **185MB（141+44）是运行时和工具链**，Go 镜像 **0MB**（因为运行时就是 8MB 的静态二进制）。
> **同样是 12 个月前的产物，Go 那份 B vs Java 那份 A，差 25 倍。**

---

## 2. 机制

### 2.1 构建过程：一条指令 = 一层

```dockerfile
FROM alpine:3.20          # 层 0：基础镜像（拉来的）
RUN apk add --no-cache tzdata   # 层 1：装包
COPY app.jar /app.jar     # 层 2：拷文件
CMD ["java","-jar","/app.jar"]  # 元数据（不占体积）
```

**每条指令的产物**：
- `RUN` / `COPY` / `ADD` / `WORKDIR` 等 → **文件系统层**（有体积）
- `CMD` / `ENTRYPOINT` / `ENV` / `EXPOSE` / `USER` / `LABEL` / `VOLUME` → **元数据层**（0B）

### 2.2 缓存机制与失效规则（最容易翻车的一节）

**规则只有一条**：

> **构建时逐层检查：如果这一层的「指令 + 相关文件内容」和上次完全相同，就复用缓存，不执行。**

**关键推论：缓存是"按顺序"的。** 某一层缓存失效，**它后面所有层全部失效**。

**所以顺序至关重要**：

```dockerfile
# ✅ 正确：依赖文件先拷，代码后拷
COPY pom.xml .
RUN mvn dependency:go-offline -q     # 只下依赖，改代码时这层不失效
COPY src ./src
RUN mvn package -DskipTests -q        # 这层只在依赖变化或代码变化时重跑

# ❌ 错误：代码先拷 → 改一行代码，下面所有层全部重跑
COPY src ./src
COPY pom.xml .
RUN mvn dependency:go-offline -q     # ← 每次改代码都要重新下依赖，慢到想砸键盘
```

**这就是 D1 那份 Java Dockerfile 的核心设计**（下面详拆）。

**会破坏缓存的动作（面试常问）**：

| 动作 | 为什么坏 |
|---|---|
| 依赖安装和源码拷贝**顺序颠倒** | 改代码 → 依赖层失效 → 重装依赖 |
| `ADD <url>` 下载远程文件 | 远程内容变了但 URL 没变，**Docker 不会重新下载**（缓存误判） |
| `RUN` 里同时装了包又改了代码 | 混在一起，无法拆分缓存 |
| 频繁 `ARG` 变化（时间戳、commit hash） | 每层都失效 |
| `apt-get update && apt-get install` 写在一层 | `update` 必须和 `install` 同层（否则 `update` 的索引过期）；但这层又会因前面变化而失效 |

> **实用优化**：把变化频率低的东西放前面（依赖、配置），变化频率高的放后面（源码）。**一句话：让"改一次代码只重跑最后一两层"。**

### 2.3 多阶段是怎么实现的

```dockerfile
FROM golang:1.26.0 AS build     # 阶段 1：编译环境
RUN go build -o /bin/app .

FROM scratch                     # 阶段 2：运行镜像
COPY --from=build /bin/app /bin/app
```

**机制**：
1. 多个 `FROM` 开启多个**独立构建阶段**，各自有自己的基础镜像和文件系统
2. `COPY --from=<阶段名>` **从指定阶段拷贝文件**（也可用 `--from=0` 引用第 0 个阶段，或 `--from=<镜像名>` 从其他镜像拷）
3. **只有最后一个阶段的结果会成为最终镜像**
4. **前面的阶段只用于构建，产物不进入最终镜像**

> **这正是瘦身的全部秘密**：编译环境（Maven + JDK 约 500MB）存在于构建过程，但**不在最终镜像里**。

### 2.4 基础镜像选型

| 基础镜像 | 体积 | 有 shell | 有包管理 | 能装包 | 适用 | 风险 |
|---|---|---|---|---|---|---|
| `scratch` | **0** | ❌ | ❌ | ❌ | **Go 静态编译** | 只有你 COPY 的东西，**连证书都没有**（要手动拷 CA） |
| `alpine` | ~5MB | ✅ | ✅ | ✅ | 通用，**需注意 musl vs glibc 兼容** | CGO 程序跑不起来（缺 glibc 动态库） |
| `debian-slim` | ~80MB | ✅ | ✅ | ✅ | 需要 glibc 的程序 | 体积大 |
| `distroless` | ~2MB | ❌ | ❌ | ❌ | **只给运行时的最小生产镜像** | 排查困难（没 shell） |
| `eclipse-temurin:17-jre-alpine` | 141MB | ✅ | ✅ | ✅ | Java 运行镜像 | 仍然偏大 |

> **注意 `scratch` 的坑**：它**连 `/etc/ssl/certs` 都没有** → 你的服务发 HTTPS 请求会报证书错误。**所以 D1 的 Go Dockerfile 专门 COPY 了 ca-certificates.crt**（实测 242kB）——这 242kB 就是整个镜像的"网络能力"。**这一行是新手最容易漏的，漏了就是线上 HTTPS 报 x509: certificate signed by unknown authority。**

### 2.5 ENTRYPOINT vs CMD 与 PID 1（面试高频）

| 写法 | 语义 | 能否被 `docker run` 覆盖 |
|---|---|---|
| `CMD ["a","b"]` | 容器默认执行的命令 | ✅ 能（`docker run img c` 会替换它） |
| `ENTRYPOINT ["a"]` + `CMD ["b"]` | 固定入口 + 默认参数 | ⚠️ `ENTRYPOINT` 不能被覆盖（会忽略），`CMD` 能被覆盖 |
| 只有 `ENTRYPOINT ["a"]` | 固定入口，无默认参数 | — |

**组合的实际效果**：

```dockerfile
ENTRYPOINT ["java"]      # 固定
CMD ["-jar", "/app.jar"] # 默认参数
```
```bash
docker run img                    # → java -jar /app.jar
docker run img -Xmx512m          # → java -Xmx512m（替换 CMD，保留 ENTRYPOINT）
```

**必须用 exec 形式（JSON 数组），不能用 shell 形式**：

```dockerfile
ENTRYPOINT ["java","-jar","/app.jar"]   # ✅ exec 形式
ENTRYPOINT java -jar /app.jar           # ❌ shell 形式
```

**原因（这是本节最值钱的一条）**：
- shell 形式 → Docker 起 `/bin/sh -c "java -jar ..."`，**sh 是 PID 1，java 是子进程**
- 容器停止时，K8s/Docker 发 **SIGTERM 给 PID 1（sh）**，**sh 不转发给子进程**
- java 收不到 SIGTERM → 一直等到 **30 秒 grace period** 耗尽 → 被 **SIGKILL 强杀**
- 结果：**正在处理的请求被硬切，用户看到 502；优雅停机逻辑完全不执行**

> **这正是 D2 场景 B 第 4 条"缩容把长连接打断"的机制**（`preStop` + `terminationGracePeriodSeconds` 要配合 exec 形式的 ENTRYPOINT 才有意义）。**用 exec 形式是 5 个字符的改动，救的是线上 502。**

---

## 3. 动手：逐行拆解 D1 的两份 Dockerfile

### 3.1 Java 多阶段（Spring Boot）

```dockerfile
FROM maven:3.9-eclipse-temurin-17 AS builder
WORKDIR /app
RUN mkdir -p /root/.m2 && printf '%s' \
  '<settings>...<url>https://maven.aliyun.com/repository/public</url>...</settings>' \
  > /root/.m2/settings.xml
COPY pom.xml .
RUN mvn dependency:go-offline -q
COPY src ./src
RUN mvn package -DskipTests -q
FROM eclipse-temurin:17-jre-alpine AS runtime
WORKDIR /app
COPY --from=builder /app/target/*.jar app.jar
USER 1000
EXPOSE 8080
ENTRYPOINT ["java","-jar","/app.jar"]
```

| 行 | 为什么这么写 | 写错了会怎样 |
|---|---|---|
| `FROM maven:... AS builder` | **构建阶段**用完整 Maven+JDK；`AS builder` 给阶段起名，后面才能 `--from=builder` | 不起名就用不了 `--from` |
| `RUN ... > /root/.m2/settings.xml` | **配阿里云 Maven 镜像**——国内直连 Maven 中央仓库极慢或超时 | 构建要等几十分钟甚至失败 |
| `COPY pom.xml .` | **只拷依赖清单**，不拷源码 | 见下 |
| `RUN mvn dependency:go-offline` | **单独一层预下载依赖**。放在 `COPY src` 之前 → 改代码时这层**缓存命中，不重下依赖** | 顺序颠倒 → 每次改代码重下全部依赖 |
| `COPY src ./src` | 源码放后面，**改代码只让最后两层失效** | — |
| `RUN mvn package -DskipTests -q` | 打包；`-DskipTests` 跳过测试（**测试该在 CI 单独跑，不在镜像构建里跑**）；`-q` 降噪 | 在镜像构建里跑测试 → 构建慢十倍 |
| `FROM ...jre-alpine AS runtime` | **第二阶段 = 运行时**。用 **JRE** 不是 JDK（省掉编译器，约 80MB） | 用 JDK 当运行时 → 白带一套编译器 |
| `COPY --from=builder /app/target/*.jar app.jar` | **只从构建阶段拷 jar**。用通配符 `*.jar` 是为了避开 `*.jar.original` 之类 | 路径写错 → 找不到文件 |
| `USER 1000` | **非 root 运行**。容器逃逸的第一道防线 | 默认 root → 容器被攻破即宿主 root |
| `EXPOSE 8080` | 声明端口（**只是文档性质**，不实际开端口） | — |
| `ENTRYPOINT ["java","-jar","/app.jar"]` | **exec 形式**，保证 java 是 PID 1，能收到 SIGTERM | shell 形式 → 优雅停机失效 |

**还能再瘦的三步（进阶）**：

```dockerfile
# ① apk 装的包要清索引（否则索引本身占体积）
RUN apk add --no-cache fontconfig ca-certificates && rm -rf /var/cache/apk/*
# ② 换 distroless 运行时（无 shell、无包管理、体积约 2MB）
FROM gcr.io/distroless/java17-debian12
# ③ 用 jlink 只保留用得上的 JDK 模块（可再省 30-40%）
RUN jlink --add-modules java.base,java.logging,java.net.http --strip-debug --no-header-files --no-man-pages --compress=2 --output /opt/jre
```

> **但要说清代价**：`distroless` **没有 shell**，出问题时你**进不去容器**——只能用 `kubectl debug` 起个临时 Pod 挂进去。**这是"可观测性 vs 可排障性"的权衡，不是纯收益。面试能讲出这层权衡，比会写 Dockerfile 高一个档。**

### 3.2 Go 多阶段 → scratch

```dockerfile
FROM golang:1.26.0 AS build
ENV GOPROXY=https://mirrors.aliyun.com/goproxy/,https://goproxy.cn,direct
ENV GOSUMDB=sum.golang.google.cn
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /bin/app .
FROM scratch
COPY --from=build /bin/app /bin/app
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
USER 65532:65532
EXPOSE 8080
CMD ["/bin/app"]
```

| 行 | 为什么这么写 | 写错了会怎样 |
|---|---|---|
| `ENV GOPROXY=...` | **Go 模块代理走国内**。默认 `proxy.golang.org` 国内极慢 | `go mod download` 卡几分钟 |
| `ENV GOSUMDB=sum.golang.google.cn` | **校验和数据库也走国内**。不改这个，即使 GOPROXY 走了国内，**校验仍会尝试连 sum.golang.org** → 一样卡死 | **这是最隐蔽的一个坑**（很多人只改了 GOPROXY 就以为完了） |
| `COPY go.mod go.sum ./` | **只拷依赖清单** | — |
| `RUN go mod download` | 单独一层预下载 → 改代码不重下 | 顺序颠倒 → 每次重建都重下 |
| `RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w"` | 三个关键参数，见下 | 见下 |
| `FROM scratch` | **零基础镜像**，最终镜像只有你 COPY 的东西 | — |
| `COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/` | **scratch 没有证书**，手动拷 CA。不拷 → **所有 HTTPS 请求报 x509 错误** | **线上才发现，最典型的"镜像能跑但接口全挂"** |
| `USER 65532:65532` | 非 root。`65532` 是常见非特权 UID（`nobody`） | 用 root → 安全风险 |
| `CMD ["/bin/app"]` | exec 形式 | shell 形式 → 信号问题 |

**三个 build 参数详解**：

| 参数 | 作用 | 不加会怎样 |
|---|---|---|
| `CGO_ENABLED=0` | **关掉 cgo，纯 Go 静态编译** | 动态链接 → 依赖 glibc → **`FROM scratch` 跑不起来**（缺 so 库） |
| `GOOS=linux` | 交叉编译目标平台 | 在 Mac 上构建出的是 darwin 二进制 → 容器里 exec format error |
| `-ldflags="-s -w"` | **`-s` 去符号表、`-w` 去 DWARF 调试信息** | 二进制大 25-30%（Go 二进制里调试信息占比很高） |

> **面试这么答**："Go 镜像能压到 V，核心是三件事：**多阶段**（编译环境不进产物）、**静态编译**（`CGO_ENABLED=0` 才能用 `scratch`）、**去调试信息**（`-ldflags="-s -w"`）。另外 `scratch` 没有 CA 证书，必须手动 COPY，否则 HTTPS 全挂——**这个坑是镜像能跑但接口全 502 的常见根因。**"

---

## 3.3 常用运维命令面板（按场景）

> **本节能做什么**：3.1–3.2 是逐行拆解 Dockerfile，本节是**构建镜像 / 调试分层 / 验证瘦身时每天会敲的命令**——按场景归类，每条配真实例子。**Dockerfile 调试的肌肉记忆就来自这一张张表。**

### ① 构建与缓存调试

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker build` | `docker build -t app:v1 -f Dockerfile .` | 标准构建，`.` 是构建上下文（别放根目录！） |
| `--no-cache` | `docker build --no-cache -t app:v1 .` | 全量重建，排查"改了代码镜像没变"（缓存命中错） |
| `--target` | `docker build --target builder -t app:dev .` | 多阶段只建到某阶段，单独验证编译是否通过 |
| `--build-arg` | `docker build --build-arg VER=1.2 .` | 传构建期变量，区分环境 |
| BuildKit 进度 | `docker build --progress=plain .` | 看每步详细输出，定位卡在哪条指令 |

> **实战价值**：`--no-cache` 是"缓存背锅"时的终极验证——全量重建后还错，就不是缓存问题；重建后正常，回头调 COPY 顺序（L2 §2.2）。

### ② 镜像分层分析

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker history` | `docker history --no-trunc app:v1` | 逐层看指令与体积，找"是谁把镜像撑大" |
| `dive` | `dive app:v1` | 交互式看每层新增 / 删除的文件，优化分层神器 |
| `docker inspect` | `docker inspect app:v1 \| grep -A20 GraphDriver` | 看层在宿主机的存储位置 |

### ③ 镜像体积 / 瘦身验证

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker images` | `docker images app --format '{{.Tag}} {{.Size}}'` | 对比单阶段 vs 多阶段体积差异 |
| 多阶段对比 | `docker build -f Dockerfile.old -t app:single . && docker build -t app:multi .` | 同代码两种写法量体积，用数据说话（面试加分） |
| 体积告警 | `docker images --format '{{.Size}}\t{{.Repository}}' \| sort -h` | 排序找异常大的镜像 |

### ④ 构建产物提取（多阶段拷产物）

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker create` + `cp` | `cid=$(docker create app:build); docker cp $cid:/dist/app ./; docker rm $cid` | 从构建阶段容器拷出二进制，验证产物正确 |
| `docker run --rm` | `docker run --rm app:runtime ls /app` | 起一下看运行时里到底有什么文件 |

### ⑤ 运行验证（ENTRYPOINT / CMD / PID 1）

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker run --rm` | `docker run --rm app:v1 /app --help` | 覆盖 CMD 传参数，验证可执行与参数解析 |
| `docker inspect` | `docker inspect app:v1 --format '{{.Config.Entrypoint}} {{.Config.Cmd}}'` | 确认 ENTRYPOINT/CMD 真的如你所写 |
| 信号测试 | `docker kill -s SIGTERM web` | 验证 PID1 能收到信号优雅退出（没 tini 会卡住） |

> **实战价值**：没 `exec` 或没 tini 时，容器内进程不是 PID1，收不到 `SIGTERM`，`docker stop` 会等 10 秒强杀——这是"停容器慢 / 优雅停机失效"的常见根因。

### ⑥ 本地 tag 与 registry 管理

| 命令 | 实例 | 实战价值 |
|---|---|---|
| `docker tag` | `docker tag app:v1 registry/app:v1` | 改名才能推到目标仓库 |
| `docker push` | `docker push registry/app:v1` | 推上去供别人拉 |
| `docker rmi` | `docker rmi registry/app:v1` | 推完清本地，省磁盘 |

### ⑦ .dockerignore 与构建上下文

| 命令 | 实例 | 实战价值 |
|---|---|---|
| 上下文大小 | `docker build --no-cache -t t . 2>&1 \| head` | 看 "Sending build context" 那行的大小 |
| 验证忽略生效 | `cat .dockerignore` | 确认 node_modules/.git 被忽略，否则上下文暴涨拖慢构建 |
| 误送排查 | `docker build --progress=plain .` | 看 COPY 是否把不该送的文件送进来了 |

### ⑧ 构建失败定位

| 命令 | 实例 | 实战价值 |
|---|---|---|
| 失败层复用 | `docker build ... 2>&1 \| grep -A3 'Step'` | 看卡在哪一步、报错原文 |
| 缓存命中判断 | `docker build .`（看输出 `Using cache`） | 确认哪些层命中缓存、哪些重跑 |
| 中途调试 | `docker run -it <上一步成功层id> sh` | 在某层基础上手动跑，复现失败 |

---

## 3.4 入门排障实战（3 个场景串命令）

> **把 3.3 的命令用起来**：三个最典型的构建故障，按"现象 → 敲的命令 → 怎么读"走一遍。**都在本地 Docker 上练。**

### 场景 A：改了代码，构建出来还是旧逻辑（缓存没生效）

```bash
docker build --no-cache -t app:test .   # 全量重建，验证是不是缓存问题
docker history app:test                  # 对比层
# 若 --no-cache 后正常 → 原 Dockerfile 把易变代码 COPY 放在了依赖安装之前
```

> **读法**：缓存失效规则是"某层变了，它之后所有层重跑"。把 `COPY 源码` 放在 `RUN 装依赖` 之前就会一改代码全量重装。把依赖安装前置、源码后置（L2 §2.2）。

### 场景 B：多阶段产物拷不过去（运行时里找不到文件）

```bash
docker create app:build > /tmp/cid && cid=$(cat /tmp/cid)
docker cp $cid:/dist/app ./app_local     # 看构建阶段到底有没有这个文件
docker rm $cid
docker run --rm app:runtime ls /app      # 看运行时里有没有
```

> **读法**：`--from=builder` 拷的路径和 `COPY` 源路径对不上，或编译产物没落到预期目录。先 create+copy 把中间层文件取出来核对。

### 场景 C：镜像体积没变小（以为用了多阶段其实没省）

```bash
docker images app            # 看体积有没有从 300MB 降到 12MB
docker history app:v1        # 看基础镜像是不是还是完整 JDK/Go 环境
# 常见根因：① 忘了多阶段，最终阶段还是 build 镜像；② 没用 distroless/scratch；③ 装了包没清索引
```

> **读法**：`history` 看最终阶段的基础镜像。没瘦下来八成是最终阶段没切到运行时镜像，或包管理索引没清（参考 L2 §3.1 的 `apk del` / `rm -rf`）。

> **三个场景的共同心法**：**先看 `history`/体积 → 再定位是缓存 / 路径 / 基础镜像问题 → 最后改 Dockerfile**。一切以 `docker history` 和 `docker images` 的数据为准。

---

## 4. Dockerfile 指令逐字段表

| 指令 | 作用 | 产生层 | 关键注意点 |
|---|---|---|---|
| `FROM` | 指定基础镜像，**开启新阶段** | ✅ | 用 `AS <name>` 给阶段命名；**尽量用具体版本号**，别用 `latest` |
| `RUN` | 执行命令 | ✅ | **优先 exec 形式** `RUN ["cmd","arg"]`；同一条 `RUN` 里做的改动在下一层就看不到 |
| `COPY` | 复制文件 | ✅ | **只复制需要的**；`.dockerignore` 排除掉 `.git` `node_modules` |
| `ADD` | 复制 + 可解压 + 可下 URL | ✅ | **优先用 COPY**。ADD 的自动解压行为常出意外 |
| `WORKDIR` | 设置工作目录 | ✅ | **用绝对路径**；相对路径基于上一个 WORKDIR，容易搞错 |
| `ENV` | 设环境变量 | 元数据 | **密钥别用 ENV**（会进镜像层，`docker history` 能看到）→ 用 Secret |
| `ARG` | 构建期变量（不落进最终镜像） | 元数据 | 只在构建期存在，**运行时不可见**；适合放版本号 |
| `CMD` | 默认命令 | 元数据 | exec 形式；**会被 `docker run` 覆盖** |
| `ENTRYPOINT` | 固定入口 | 元数据 | **exec 形式**（信号传递）；不可被 `docker run` 覆盖 |
| `EXPOSE` | 声明端口 | 元数据 | **只是声明**，不实际开放端口 |
| `USER` | 切换运行用户 | 元数据 | **放在 COPY 之后**，否则拷贝的文件属主不对；用数字 UID 更通用 |
| `VOLUME` | 声明数据卷 | 元数据 | 声明后该路径无法再写（Docker 自动卷） |
| `HEALTHCHECK` | 容器健康检查 | 元数据 | K8s 里**优先用 `livenessProbe`/`readinessProbe`**（K8s 自己会探） |
| `LABEL` | 元信息 | 元数据 | 记录版本、来源、许可证，**便于排查"这个镜像是哪来的"** |
| `SHELL` | 改默认 shell | 元数据 | 极少用 |

**关键区别**：

| | `ARG` | `ENV` |
|---|---|---|
| 生效时机 | 构建时 | 构建时 + 运行时 |
| 进镜像层 | ❌ 不进 | ✅ 进（`docker history` 可见） |
| 运行时可见 | ❌ | ✅ |
| 适合放 | 版本号、构建参数 | 运行时配置 |
| **不适合放** | **密钥** | **密钥**（`docker history` 一看就露） |

---

## 5. 横向对比

### 5.1 单阶段 vs 多阶段

| 维度 | 单阶段 | 多阶段 |
|---|---|---|
| 镜像体积 | 大（含编译工具链） | **小** |
| 构建可读性 | 差（编译和运行混一起） | **好**（阶段职责清晰） |
| 依赖安装 | 装完还在镜像里 | **只留在构建阶段** |
| 适用 | 脚本类、无编译依赖 | **所有需要编译的项目** |

### 5.2 基础镜像选型

| 镜像 | 体积 | shell | 排障 | 适用 |
|---|---|---|---|---|
| `scratch` | 0 | ❌ | 只能 `kubectl debug` | **Go/C 静态编译** |
| `alpine` | ~5MB | ✅ | 方便 | 通用首选（注意 musl） |
| `debian-slim` | ~80MB | ✅ | 方便 | 需要 glibc |
| `distroless` | ~2MB | ❌ | 难 | **生产最小运行时** |
| `ubuntu` | ~80MB | ✅ | 方便 | 调试用，**不上生产** |

### 5.3 `ENTRYPOINT` vs `CMD` vs shell 形式

| 方案 | `docker run img` | `docker run img c` | 收到 SIGTERM |
|---|---|---|---|
| `CMD ["a"]` | 跑 a | **跑 c（替换）** | ✅ |
| `ENTRYPOINT ["a"]` + `CMD ["b"]` | 跑 a b | 跑 a c | ✅ |
| `CMD a b`（shell） | sh -c "a b" | 替换 | ❌ **不转发给子进程** |
| `ENTRYPOINT a b`（shell） | sh -c "a b" | 忽略 | ❌ |

---

## 6. 踩坑表

| 现象 | 真实原因 | 怎么解 |
|---|---|---|
| **改一行代码，构建要几分钟** | 依赖层在源码层之后，缓存全失效 | `COPY` 依赖清单 → 预下载依赖 → 再 `COPY` 源码 |
| **scratch 镜像跑不起来** | 二进制是动态链接，缺 glibc | `CGO_ENABLED=0` 静态编译 |
| **scratch 镜像里所有 HTTPS 报错** | **没有 CA 证书** | `COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/` |
| **Go 构建卡在 `go mod download`** | 校验和库仍连 `sum.golang.org` | **同时**设 `GOPROXY` 和 `GOSUMDB` |
| **Mac 构建的镜像在 K8s 里 exec format error** | 架构不对（arm64 vs amd64） | `docker build --platform linux/amd64` |
| **容器停不下来，每次都等 30 秒** | shell 形式 ENTRYPOINT，信号不转发 | 改 exec 形式 `CMD ["a","b"]` |
| **`docker history` 看到密码** | 密钥用 `ENV`/`ARG` 写进层 | 用 K8s Secret + `RUN --mount=type=secret` |
| **构建时 `COPY . .` 把 `.git` 和依赖也拷进去** | 没有 `.dockerignore` | 写 `.dockerignore` |
| **镜像 700MB+** | 用了完整 JDK 当运行时 | 换 JRE / distroless |
| **多架构构建推上去后拉不下来** | buildx 默认带 provenance attestation，需 OCI 支持 | `--provenance=false --sbom=false`（D1 4 步就是这个） |
| **生产容器里 `kubectl exec` 进去没有 shell** | 用了 scratch/distroless | 准备 `kubectl debug` 方案，或开发环境用带 shell 的镜像 |

> **最后一条不是 bug 是取舍**：distroless 的价值就是"里面什么都没有"，包括 shell。**面试能讲出"我为了最小攻击面选了 distroless，所以排障改用 ephemeral container 挂 busybox 进去"，这比单纯说"我们用 distroless"实在得多。**

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `docs.docker.com/build/building/multi-stage/` | **多阶段构建** | ✅ 2026-10-24 |
| S2 | 官方 | `docs.docker.com/build/` | 镜像构建总览 | ✅ 2026-10-24 |
| S3 | 官方 | `docs.docker.com/get-started/` | 镜像与容器基础 | ✅ 2026-10-24 |
| S4 | **中文** | **`docker.c-cpp.com/build/building/multi-stage/`** | **多阶段构建（中文官方译）** | ✅ 2026-10-24 |
| S5 | **中文** | **`docker.c-cpp.com/build/building/packaging/`** | **打包最佳实践（中文）** | ✅ 2026-10-24 |
| S6 | **中文** | `docker.c-cpp.com/get-started/overview/` | 概念定义（中文） | ✅ 2026-10-24 |
| S7 | **中文** | `docker.c-cpp.com/manuals/` · `/reference/` | 指令参考（中文） | ✅ 2026-10-24 |
| S8 | 云厂商 | `help.aliyun.com/zh/acr/` | ACR 仓库与 buildx 推送 | ✅ 2026-10-24 |
| S9 | 云厂商 | `developer.aliyun.com/mirror/docker-ce/` | 阿里云镜像站 | ✅ 2026-10-24 |

> **本篇的体积数据（A / B）与分层明细全部来自本机 `docker history` 实测**，非文档转述。
> **中文来源以 `docker.c-cpp.com`（Docker 官方文档中译站）为主**——官方域名下无中文路径，详见 [`../09-参考索引/README.md`](../09-参考索引/README.md)。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入：Dockerfile + 上下文目录；输出：一个分层镜像。中间：逐层构建（可复用缓存）→ 多阶段筛选 → 最终只留最后阶段。
2. **能故意改坏再倒推吗？** —— 四个实验：① 把 `COPY go.mod go.sum` 挪到 `COPY . .` 之后 → 感受缓存失效的代价；② 删掉 `COPY ca-certificates` → 用 scratch 镜像发 HTTPS 请求看报错；③ 把 `ENTRYPOINT` 改成 shell 形式 → `docker stop` 观察是否要等 30 秒；④ `docker history` 看 `ENV` 里的密码是否可见。
3. **能画上下游吗？** —— 画出「Dockerfile → BuildKit 逐层构建（含缓存命中判断）→ 多阶段 → 最终镜像 → registry → K8s 节点拉取（决定扩容速度）」这条链，并标出每一步的耗时来源。

---

**下一篇**：[`L3-镜像仓库与供应链安全`](L3-镜像仓库与供应链安全.md)（**已写** · ACR / Harbor / Trivy 扫描 / SBOM / 签名 / 供应链四道防线） · **上一篇**：[`L1-Docker-容器化基础`](L1-Docker-容器化基础.md)（**已写** · 镜像分层与容器隔离） · 回到 [`00-学习地图与前置依赖`](../00-学习地图与前置依赖.md) · 关联日课：[`D1 Docker 多阶段构建`](../第1周/第01天.md)
