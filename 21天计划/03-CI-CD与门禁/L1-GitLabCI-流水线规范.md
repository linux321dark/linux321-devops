# L1 · GitLab CI 流水线规范（`.gitlab-ci.yml` 从能跑到跑对）

> **本篇定位**：**不重复给一份能跑的 `.gitlab-ci.yml`**——D4 日课里已经给了。本篇讲清**这份 YAML 的每个关键字在干什么、写错会怎样、官方为什么推荐这么写**，并给出面试能复述的流水线设计方法论。
> **前置**：L0（YAML 语法）、L3（镜像仓库与供应链安全：镜像引用、digest、Trivy 扫描）。
> **学完能做什么**：独立写一套带门禁的 `.gitlab-ci.yml`；说清 `rules` 与 `only/except` 的取舍；用 `needs` 把串行流水线改成 DAG；讲清 variables 的作用域与优先级；解释"T1 压到 T2"的具体手段。
> **在 21 天里**：D4（GitLab CI 云原生化）的主体；D5（质量门禁）的流水线骨架；D18（安全合规）的扫描接门禁。
> **环境/版本基准**：GitLab CI 语法以 **GitLab 官方 `docs.gitlab.com`** 为准（**`only`/`except` 已官方标记为 deprecated**，见 5.1）；镜像仓库前缀 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`；本地 Docker **29.8.2**。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L1 GitLab CI 流水线规范
│
├─ 1. 概念
│   ├─ 1.1 CI / CD 三个阶段到底是什么
│   ├─ 1.2 没有 CI/CD 会怎样（真实事故）
│   ├─ 1.3 GitLab CI 的核心对象（pipeline / stage / job / runner）
│   └─ 1.4 术语表
│
├─ 2. 机制
│   ├─ 2.1 一次 push 到底发生了什么（完整时序）
│   ├─ 2.2 .gitlab-ci.yml 顶层结构（哪些是全局的、哪些是 job 的）
│   ├─ 2.3 stage 与 job 的关系（并行 vs 串行）
│   ├─ 2.4 rules 的求值语义（首个匹配即生效）
│   ├─ 2.5 needs 与 DAG：把串行改并行
│   ├─ 2.6 variables 的作用域层级与优先级
│   ├─ 2.7 protected / masked：两种密钥变量
│   ├─ 2.8 dotenv：job 之间传值的正规通道
│   ├─ 2.9 artifacts 与 reports
│   ├─ 2.10 cache 的 key 策略
│   ├─ 2.11 include 与 extends：两种复用方式
│   └─ 2.12 workflow：控制整条流水线跑不跑
│
├─ 3. 动手：一条完整流水线（build → test → scan → push → deploy）
│
├─ 4. 逐字段表
│   ├─ 4.1 job 关键字表（关键字 | 类型 | 作用 | 典型用法）
│   └─ 4.2 variables 作用域表
│
├─ 5. 横向对比
│   ├─ 5.1 rules vs only/except
│   ├─ 5.2 needs vs 依赖 stage 顺序
│   ├─ 5.3 when: manual vs when: on_failure
│   └─ 5.4 artifacts vs cache
│
├─ 6. 踩坑与排障（症状 | 状态 | 真实原因 | 怎么修 | 下次怎么防）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 CI / CD 三个阶段到底是什么

**先把名字里的缩写搞清楚**，因为很多人只会背缩写不懂含义：

| 缩写 | 全称 | 中文 | 做什么 | 失败意味着 |
|---|---|---|---|---|
| **CI** | Continuous Integration | **持续集成** | 每次提交都自动**构建 + 测试**，尽早发现代码写坏了 | 代码不能合入 |
| **CD**（交付） | Continuous Delivery | **持续交付** | 制品**随时处于可发布状态**，发布动作是"点一下" | 需要人工判断能不能发 |
| **CD**（部署） | Continuous Deployment | **持续部署** | 通过门禁就**自动发布**，无需人工干预 | 已经上线了，只能靠回滚 |

> **关键认知**：CD 有两个含义，**区别只在"最后一步要不要人点"**。
> - **持续交付**：包已经打好、随时能发，**但生产发布要人点一下**（`when: manual`）。
> - **持续部署**：包打好、通过门禁，**直接发**。
>
> **企业里绝大多数团队做的是"持续交付"**——因为生产发布是高风险动作，**人的判断在这一步是有价值的**。这一点在面试里说出来，比背"CI/CD 三个阶段"实在。

**三阶段在工程上的真正含义**：

| 阶段 | 核心问题 | 关键工程手段 | 谁的战场 |
|---|---|---|---|
| CI | **代码能不能合** | 自动化测试 + 质量门禁 + 快速反馈 | 研发 |
| CD（交付） | **能不能随时发** | 制品不可变 + 环境一致 + 一键回滚 | DevOps |
| CD（部署） | **要不要现在就发** | 渐进式发布 + 自动回滚 | SRE / 业务 |

> **为什么 21 天的学习路径里 CI/CD 排在 K8s 之后**——因为 **CD 的最后一步是"部署到 K8s"**。不懂 Deployment / Service / HPA，"部署"就只是一个按钮；懂了才知道那个按钮背后在发生什么（滚动更新、就绪探针、扩缩容）。

### 1.2 没有 CI/CD 会怎样（真实事故）

**反面场景比正面定义更有说服力**。看一条典型的"人工流水线"会怎么崩：

| 环节 | 人工做法 | 出事的样子 |
|---|---|---|
| **构建** | 每人本地 `mvn package` | **谁的机器上编出来的是哪个版本？说不清** |
| **测试** | 测完把 jar 传群里 | 上传的是**测之前那版还是测之后那版**？没人保证 |
| **交付** | `scp` 到服务器 | 服务器上跑着 3 个版本的 jar，**没人知道当前跑的是哪个** |
| **回滚** | 找老 jar 覆盖回去 | **老 jar 找不到了**（本地清过缓存）→ 回滚失败 |
| **审计** | 靠人记 | **"这个版本谁测的？"** ——查无对证 |

**这几条里任意一条的根因都是同一个：缺少"每次变更产生一个不可变、有版本、可追溯的制品"这条链。**

> **CI/CD 的本质不是"自动化"，是"把每次变更固化成一条可追溯的链"**。自动化只是手段。
> 面试这么答："CI/CD 的价值不在于省了多少人工时间，在于**它让'线上跑的到底是哪个版本'这个问题有确定答案**。我们之前每周要花时间排查'服务器上到底跑的是哪一版'，CI/CD 加上制品版本与 commit 绑定之后，这个问题消失了。"

### 1.3 GitLab CI 的核心对象

```
一次提交 (commit)
   │
   ▼
Pipeline（流水线）  ← 一次触发 = 一条 pipeline
   │
   ├─ Stage（阶段）   ← 顺序单位：build → test → deploy
   │   │
   │   ├─ Job（作业）  ← 任务单位：编译、跑单测、扫漏洞
   │   │    └─ Runner  ← 执行者（跑在你指定的机器/容器/Pod 里）
   │   └─ Job
   └─ Stage
```

**四个对象各管什么**：

| 对象 | 是什么 | 谁定义 | 关键约束 |
|---|---|---|---|
| **Pipeline** | 一次触发产生的完整执行单元 | GitLab 自动创建 | 由 `workflow` 决定**要不要创建** |
| **Stage** | 执行的**顺序单位** | `.gitlab-ci.yml` 的 `stages:` | **同一 stage 内并行；跨 stage 串行** |
| **Job** | 具体的**任务单位** | `.gitlab-ci.yml` 的顶层键 | 挂在某个 stage 下 |
| **Runner** | **实际干活的执行者** | 管理员在 UI/API 里注册 | 按 **tag** 匹配 job；决定 job 跑在哪、跑在什么隔离环境里 |

**默认 stage 顺序**（不写 `stages:` 时的内置默认值）：

```
.pre → build → test → deploy → .post
```

> **注意**：`.pre` 和 `.post` 是两个特殊阶段——**`.pre` 在所有 stage 之前，`.post` 在所有 stage 之后**（即使 `after_script` 失败，`.post` 也会跑）。**它们是给"全局前置准备"和"无论成败都要跑的清理"准备的。**
>
> **另一个默认行为**：**没有写 `stage:` 的 job 默认落在 `test` 阶段**。这个默认经常坑人——你以为自己写了个 build job，实际它在 test 阶段跑。

### 1.4 术语表

| 英文 | 中文 | 一句话 |
|---|---|---|
| pipeline | 流水线 | 一次触发产生的完整执行单元 |
| stage | 阶段 | 执行的顺序单位，同阶段并行 |
| job | 作业 | 一个具体的任务，有 `script` |
| runner | 执行器/执行者 | 真正跑 job 的进程（注册在 GitLab 上） |
| executor | 执行方式 | runner 用什么环境跑 job（shell / docker / kubernetes…） |
| tag | 标签 | runner 与 job 的匹配标记 |
| rules | 规则 | 决定 job 是否被创建、在什么条件下创建 |
| `only` / `except` | — | 旧的 job 触发条件写法（**已 deprecated**） |
| `needs` | 依赖 | 显式声明依赖，让 job 提前并行 |
| artifact | 制品 | job 产出并上传给后续 job 的文件 |
| cache | 缓存 | 加速重复 job 的文件，跨 job 复用 |
| `dotenv` | 点环境文件 | 以 key=value 文件形式向后续 job 传变量 |
| variable | 变量 | 流水线中的配置值与敏感信息 |
| masked variable | 掩码变量 | 日志里显示为 `[MASKED]` 的变量 |
| protected variable | 受保护变量 | 只在受保护分支/tag 上可用的变量 |
| report | 报告 | 制品的"带语义"形态（测试报告、覆盖率、SBOM…） |
| `when: manual` | 手动 | 流水线跑到这里停住，等人点 |
| `allow_failure` | 允许失败 | 该 job 失败不算流水线失败 |
| `interruptible` | 可中断 | 新提交到来时可否取消本 job |
| trigger | 触发 | 触发下游/子流水线 |
| pipeline as code | 流水线即代码 | `.gitlab-ci.yml` 跟代码一样走 Git 版本管理 |

---

## 2. 机制

### 2.1 一次 push 到底发生了什么

```
① git push
     │
② GitLab 收到提交 → 检查仓库根目录有没有 .gitlab-ci.yml
     │
     ├── 没有 → 不创建流水线（什么都不会发生）
     │
③ 有 → 先算 workflow:rules
     │      → 全部不匹配 → 流水线根本不被创建（不是"跑了个空流水线"）
     │      → 匹配 → 继续
     │
④ 按 stages / rules 展开出具体的 job 列表（DAG）
     │
⑤ 把 job 放进队列
     │
⑥ Runner 轮询领活（POST /api/v4/jobs/request）
     │      → 按 runner 的 tag 与 job 的 tag 匹配
     │
⑦ Runner 执行：
     │      拉取代码 → 恢复 cache → 跑 before_script → 跑 script
     │      → 上传 artifacts/reports → 跑 after_script
     │
⑧ 上报结果 → GitLab 画流水线图 → 触发通知（钉钉/企微/邮件）
```

**第 ③ 步是最容易被误解的一步**：

> **"流水线不存在"和"流水线存在但 job 是跳过状态"是两件不同的事。**
> - `workflow:rules` 全不匹配 → **整条流水线不存在**（UI 上根本没有这条流水线）
> - job 的 `rules` 不匹配 → **job 在流水线里显示为灰色"skipped"**
>
> **为什么这个区别重要**：如果你在排查"为什么 push 了没反应"，第一步就是看 `workflow:rules`——**很多团队在这里踩坑**：workflow 规则写得太严，MR 流水线根本没创建，开发者以为 CI 坏了。

### 2.2 `.gitlab-ci.yml` 的顶层结构

**先分清哪些是"全局的"、哪些是"job 的"**。这是本节最重要的概念，混淆它会写出乱七八糟的 YAML。

| 关键字 | 作用域 | 类型 | 作用 | 写错的后果 |
|---|---|---|---|---|
| `stages` | **全局** | 数组 | 定义阶段列表与顺序 | 阶段名写错 → job 报 `chosen stage does not exist`；漏定义阶段 → job 落入默认 `test` |
| `variables` | **全局** | 键值 | 整条流水线所有 job 的默认变量 | 变量名拼错 → 后面用 `$XXX` 拿到**空字符串**（不报错！最难查的一类 bug） |
| `workflow` | **全局** | 映射 | 控制整条流水线**是否创建**、是否自动取消 | 规则写太严 → **流水线根本不创建**，开发者看不到任何提示 |
| `include` | **全局** | 数组/字符串 | 从其他文件导入配置 | 路径写错 → **整个流水线创建失败**（配置阶段就报错） |
| `default` | **全局** | 映射 | 给所有 job 提供关键字默认值 | `default` **不与 job 级配置合并**——job 里写了这一个键，**这个键的默认值就完全不生效**（见 2.11） |
| `spec` | **全局** | 映射 | 声明该配置是 pipeline 还是 component（可 include） | 写成 `pipeline` 但被当 component include → 报错 |
| `image` | **job 级**（也可放 `default`） | 字符串 | job 跑在哪个镜像里 | **顶层直接写已 deprecated**，官方要求放进 `default` |
| `services` | **job 级**（也可放 `default`） | 数组 | 伴随 job 启动的辅助容器（如 dind、postgres） | 服务容器**用服务名做主机名**访问，写成 `localhost:5432` 必然连不上 |
| `before_script` | **job 级**（也可放 `default`） | 数组 | 每个 job 跑 `script` 之前的命令 | 写全局顶层已 deprecated；**在这里 `exit 1` 会直接终止整个 job** |
| `after_script` | **job 级**（也可放 `default`） | 数组 | job 结束后的清理命令 | 顶层写法已 deprecated；**默认不改变 job 的退出码**（要 `set -e` 配合） |
| `cache` | **job 级**（也可放 `default`） | 映射 | 声明要缓存的路径 | 顶层写法已 deprecated；`key` 与 `paths` 缺一 → **完全不缓存** |

> **官方明确的一条线（写本篇时以官方文档为准）**：
> **`image` / `services` / `cache` / `before_script` / `after_script` 直接写在顶层已 deprecated，官方要求改用 `default:` 包裹。**
> 同样，**`only` / `except` 已 deprecated，官方要求改用 `rules:`**（见 5.1）。
>
> **这不是"新语法好看"，是官方在给迁移留时间窗**——弃用 = 还能用，但会告警，且已排期移除。**新写的流水线一律用 `default` + `rules`。**

### 2.3 stage 与 job 的关系（并行 vs 串行）

**官方原话的两条**：

> “Jobs in the same stage run in parallel.”
> “Jobs in the next stage run after the jobs from the previous stage complete successfully.”

翻译成图：

```
stages: [build, test, deploy]

build阶段:  [build-a]  [build-b]  [build-c]     ← 三个并行
                │         │         │
                └────┬────┘         │          ← 全部成功后才进下一阶段
                     ▼              ▼
test阶段:   [test-a]  [test-b]                 ← 两个并行
                │         │
                └────┬────┘
                     ▼
deploy阶段: [deploy-prod]                       ← 串行
```

**三个必须记住的推论**：

| 推论 | 说明 |
|---|---|
| **默认是"全屏障"** | 下一阶段要等**上一阶段的所有 job 都成功**。**一个 job 失败，后面全部不跑** |
| **同阶段并行受 runner 数量限制** | 写了 10 个并行 job，但只有 2 个 runner → **实际是 2 批 5 个**。job 面板上显示的"等待"就是这个原因 |
| **stage 顺序是"默认值"，可以被 `needs` 打破** | 这就是 DAG（2.5） |

> **第 2 条是"流水线慢"最常被忽略的原因**：**你以为开了并行就一定快，实际上 runner 数量才是并行度上限**。一个 `concurrent = 1` 的 runner，跑 10 个并行 job 依然是串行。**这正是 L2 篇要展开的 Runner 话题。**

### 2.4 `rules` 的求值语义（首个匹配即生效）

`rules` 是一个**数组**，GitLab **自上而下逐条求值，命中第一条就停**。

```yaml
deploy-prod:
  stage: deploy
  script:
    - kubectl apply -f k8s/
  rules:
    - if: $CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/    # ① 只认语义化 tag
    - if: $CI_COMMIT_BRANCH == "main"              # ② main 分支也发
      when: manual                                 #    但要人工点
    - when: never                                   # ③ 其他一律不发
```

**求值过程**：

```
规则① 命中？ ──是──→ 用规则①的属性（when 默认 on_success）
   │否
   ▼
规则② 命中？ ──是──→ 用规则②的属性（when: manual）
   │否
   ▼
规则③ 命中？ ──是──→ when: never → 该 job 不被创建
```

**四条关键性质**：

| 性质 | 说明 |
|---|---|
| **首个匹配即生效** | 后面的规则**不再求值**。所以**规则的顺序有意义** |
| **不写 `if` 的规则是兜底** | 无条件匹配（除非前面已命中） |
| **`when: never` 表示"不创建这个 job"** | 不是"创建了但跳过"。**这是 `rules` 表达"排除"的方式** |
| **命中规则可以同时改多个属性** | `if` / `when` / `allow_failure` / `variables` / `needs` 都可写在同一条规则里 |

**`rules` 支持的条件类型**：

| 条件 | 用途 | 例 |
|---|---|---|
| `rules:if` | 变量表达式判断 | `$CI_COMMIT_BRANCH == "main"`、`$CI_PIPELINE_SOURCE == "merge_request_event"` |
| `rules:changes` | 文件变更判断（推送时对比 diff） | `paths: ["src/**/*"]` |
| `rules:exists` | 文件是否存在 | `paths: ["Dockerfile"]` |
| `when` | 何时跑（`on_success` / `manual` / `always` / `on_failure` / `never`） | 见 2.12 与 5.3 |
| `variables` | 命中时注入变量 | `variables: { DEPLOY_ENV: "prod" }` |
| `allow_failure` | 命中时是否允许失败 | `allow_failure: true` |

> **`rules:changes` 的重要限制**：**在非 push 的场景（如 tag、schedule、MR）下，changes 的判断可能不准**。官方文档也提示过"用 `only: changes` 可能出现 job 意外运行"的已知问题。**实践上：MR 场景判断变更文件，用 `rules:changes` 配 `compare_to` 明确基线。**

### 2.5 `needs` 与 DAG：把串行改并行

**`needs` 的官方定位**：让 job **忽略 stage 顺序，更早开始**。

**默认（全屏障）的时间线**：

```
时间 →
build-a  ████
build-b  ████████          ← 必须等 build 全部结束
build-c      ████
─────────────────────────────►
test-a                  ████
test-b                      ████
─────────────────────────────►
deploy                        ██
```

**加 `needs` 之后**：

```
时间 →
build-a  ████
build-b  ████████
build-c      ████
─────────────────────────────►
test-a              ████        ← 只等 build-a
test-b                  ██████  ← 只等 build-b
─────────────────────────────►
deploy                        ██
```

**`needs` 的四个关键性质**：

| 性质 | 说明 | 影响 |
|---|---|---|
| **它同时定义了"依赖"和"artifacts 来源"** | 用了 `needs` 之后，**默认不再下载所有前序 stage 的 artifacts**，只下载 `needs` 里列出的 job 的 | **这是最常见的翻车点**：以前靠"后面 stage 自动拿到前面 artifacts"的地方，改用 `needs` 后就拿不到了 |
| **`needs: []` 表示"不等任何人"** | 空数组 → 流水线一创建就跑 | 静态检查类 job 的标准写法 |
| **上限 50** | 一个 job 的 `needs` 数组最多 50 个 job（GitLab.com 限制；自建实例默认也是 50，管理员可改） | 超了 → **流水线创建失败** |
| **`needs:optional: true`** | 依赖的 job 可能不存在时不报错 | **配合 `rules` 用**：被 `rules` 排除的 job，用 `needs` 引它会导致**流水线创建失败** |

> **第 4 条实战价值极高**：
> ```yaml
> test:
>   needs:
>     - job: build-linux
>       optional: true      # build-linux 可能被 rules 排除
> ```
> **不加 `optional` 的话**——**MR 流水线里 build job 被 rules 排除了，test job 引着它 → 整条流水线创建失败**，报错信息还很难懂。

**`needs` 与 artifacts 的精确关系**（官方原文）：

> “When using the `needs` keyword, jobs can only download artifacts from the jobs defined in the `needs` configuration.”

配套开关：

```yaml
test:
  needs:
    - job: build-linux
      artifacts: true     # 下载（默认 true）
    - job: build-macos
      artifacts: false    # 只等它完成，不下载它的产物
```

> **`artifacts: false` 是省时间的实用技巧**：跨平台构建时，**Linux 的产物要给测试用，macOS 的产物只是"要等它编完"**（比如要等它出报告），但**不需要把 macOS 的二进制拖回 CI worker**——大产物下载是真实的时间成本。

**DAG 的正确用法（面试可讲）**：

| 场景 | 写法 | 效果 |
|---|---|---|
| **静态检查不等编译** | `lint: needs: []` | 5 秒出结果，**快速反馈** |
| **多服务并行构建** | 每个服务一个 build job，同 stage | 并行 |
| **测试按服务并行** | `test-a: needs: [build-a]` | **不等其他服务编译完** |
| **部署等所有构建** | `deploy: needs: [test-a, test-b, ...]` | 显式列出所有前置 |
| **门禁 job 独立** | `scan: needs: []`（或只依赖构建 job） | 漏洞扫描与测试并行 |

> **"T1 → T2"的第一段拆解就是这个**：把一条 `build → test → package → deploy` 的长链，按服务拆成多个并行 job，用 `needs` 表达"我只需要等谁"。**这叫把流水线从"链"改成"DAG"。**

### 2.6 variables 的作用域层级与优先级

**先说结论——官方给的优先级顺序（从高到低）**：

```
1. 执行策略里定义的变量（pipeline/scan execution policy）
2. 手动运行流水线时传的变量（manual job variables）
3. 流水线级变量（Run pipeline 页面 / schedule / API / 上游流水线传递）
4. 项目级变量
5. 群组级变量（同名的取最近的子群组）
6. 实例级变量
7. dotenv 报告带来的变量
8. .gitlab-ci.yml 文件里的变量（文件内部再分四档，见下）
9. 部署变量（environment 带的）
10. 预定义变量（CI_COMMIT_SHA 等）
```

**.gitlab-ci.yml 文件内部的四档（从高到低）**：

```
① job 里的 rules:variables
② job 里的 variables
③ workflow:rules:variables
④ 文件顶层的 variables
```

> **官方原则一句话**：
> “When a job runs, GitLab uses the value from the source with the highest precedence. The highest-precedence value that exists overrides all lower-precedence values.”
>
> **也就是：只要高优先级那层定义了这个变量，低优先级的全部被覆盖；高优先级没定义，才往下找。**

**几个容易搞错的点**：

| 点 | 说明 |
|---|---|
| **`workflow:rules:variables` 高于顶层 `variables`** | 但**低于 job 级 `variables`** |
| **`rules:variables` 只在该规则命中时生效** | 同一个 job 的不同 rules 可以给不同变量值 |
| **预定义变量优先级最低** | 所以你可以用 `variables:` 覆盖 `CI_COMMIT_SHA`——**但不建议**，会破坏工具链的预期 |
| **`secrets` 不在这套优先级里** | 它是 runner 在 job 运行时去外部密钥管理器取的 |
| **inputs 不是变量** | 流水线创建时就被替换掉了，运行时改不了 |

**常用预定义变量（这几个必须背）**：

| 变量 | 值 | 用途 |
|---|---|---|
| `$CI_COMMIT_SHA` | 完整 commit SHA | **镜像 tag 用它**（可追溯） |
| `$CI_COMMIT_SHORT_SHA` | 短 SHA（8 位） | tag 用它（可读性好） |
| `$CI_COMMIT_BRANCH` | 分支名 | `rules:if` 判断分支 |
| `$CI_COMMIT_TAG` | tag 名 | `rules:if` 判断 tag |
| `$CI_COMMIT_REF_SLUG` | 分支/tag 的 slug 形式 | **做 cache key**（不能含 `/`） |
| `$CI_COMMIT_REF_PROTECTED` | `"true"` / `"false"` | 判断是否受保护 ref |
| `$CI_PIPELINE_SOURCE` | `push` / `merge_request_event` / `schedule` / `web` / `trigger` | **判断流水线来源，最重要的分流变量** |
| `$CI_DEFAULT_BRANCH` | 默认分支名 | 判断"是不是主干" |
| `$CI_MERGE_REQUEST_ID` | MR ID | MR 场景专用 |
| `$CI_REGISTRY_IMAGE` | GitLab 内置 registry 的镜像路径 | **用内置 registry 就不用配任何凭据** |
| `$CI_COMMIT_REF_PROTECTED` | 见上 | 保护变量可用性判断 |

### 2.7 protected / masked：两种密钥变量

**这两个属性解决的是完全不同的两件事**，**必须分开理解**：

| | **masked（掩码）** | **protected（受保护）** |
|---|---|---|
| **解决什么** | **日志里不显示明文** | **只有受保护分支/tag 能用** |
| **官方定义** | "prevent its value from appearing in job logs" | "to be available only to pipelines that run on protected branches or protected tags" |
| **效果** | 日志里显示 `[MASKED]` | 非受保护分支上**这个变量根本不存在** |
| **对谁生效** | 所有人（谁看日志都看不到） | **只有受保护 ref 上的 pipeline** |
| **典型用途** | 任意分支都可能用到的通用密钥 | **生产密钥**（AK/SK、生产数据库密码） |

**masked 的三个硬性要求（官方原文）**：

> “The value of the variable must:
> - Be a single line with no spaces.
> - Be 8 characters or longer.
> - Not match the name of an existing predefined or custom CI/CD variable.”

| 要求 | 踩坑表现 |
|---|---|
| **单行、无空格** | 存多行私钥 → **保存时提示无法掩码**（就只能不掩码，或改用 file 类型变量） |
| **≥ 8 个字符** | 太短 → 存不了 masked |
| **不能与已有变量同名** | 与预定义变量重名 → 同样存不了 |

> **官方的一句重要警告（必须知道）**：
> “Masking a CI/CD variable is not a guaranteed way to prevent malicious users from accessing variable values.”
>
> **掩码只是"日志里看不见"，不是"拿不到"。** 一个有权限写 CI 配置的人，可以写 `printenv` 或把值写到文件里再取出来。**官方给的加固建议是用外部 secrets 与 file 类型变量**，让命令 `env` / `printenv` 根本打印不出来。
>
> **还有一个隐蔽的坑**：**如果 shell 给特殊字符加了转义（`\`），掩码会失效**。比如值是 `My[value]`，输出成 `My\[value\]` 就**不会被掩码**。这属于"掩码了但没完全掩住"，很难发现。

**protected 的配套要求**：

**受保护的分支/tag 是在 UI 里配的**（Settings → Repository → Protected branches）。这意味着：

| 组合 | 结果 |
|---|---|
| **protected + masked** | ✅ **生产密钥的标准配置**：只有受保护分支能用，且日志里看不到 |
| **只有 protected** | 变量只在受保护分支可用，但**日志里会显示明文** |
| **只有 masked** | 所有分支都能用，但日志里看不到。**MR 流水线也能拿到生产密钥** —— 危险 |

> **⚠️ 最危险的一种**：**只 masked 不 protected 的生产 AK/SK**。因为 **MR 流水线也能访问它**（`merge_request_event` 触发的流水线不是受保护 ref），**外部贡献者提一个 MR 就能在日志里（虽然看不到明文）通过脚本把它发到外网**。掩码挡得住眼睛，挡不住 curl。
>
> **所以规则是：生产密钥 = protected + masked + 只在生产 job 里用。**

### 2.8 `dotenv`：job 之间传值的正规通道

**问题**：build job 里编译出了版本号 / 镜像 digest，deploy job 要用。怎么办？

**错误做法**：写到一个共享目录 → **只在同一个 runner 主机上才有效**，换个 runner 就没了。

**正确做法**：`dotenv`。

```yaml
# build job：产出变量文件
build:
  stage: build
  script:
    - docker build -t $IMAGE:$CI_COMMIT_SHORT_SHA .
    - docker push $IMAGE:$CI_COMMIT_SHORT_SHA
    - DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' $IMAGE:$CI_COMMIT_SHORT_SHA)
    - echo "IMAGE_DIGEST=$DIGEST" >> build.env
  artifacts:
    reports:
      dotenv: build.env

# deploy job：直接用变量
deploy:
  stage: deploy
  script:
    - kubectl set image deployment/app app=$IMAGE@$IMAGE_DIGEST
```

**机制**：

| 环节 | 行为 |
|---|---|
| 上游 job 写 `.env` 文件（`KEY=VALUE` 每行一个） | 由 `artifacts:reports:dotenv` 指定 |
| GitLab 解析文件 | **只解析合法的 `KEY=VALUE` 行**，其他行忽略 |
| 下游 job 拿到变量 | **以 CI/CD 变量的形式注入**（不是文件） |
| **生效范围** | **只对 `dependencies` 或 `needs` 里列出的 job 生效**（见 2.6 优先级第 7 档） |
| **不能用在 `rules` 里** | 官方明确："You cannot use dotenv variables in `rules` sections." → **想按 dotenv 变量分流，只能靠 job 级 `variables` + 脚本里判断** |
| **同名变量取最后一个** | 同一个 dotenv 报告里变量名重复 → **最后一个值生效** |

**dotenv 文件的格式限制（官方原文）**：

| 限制 | 说明 |
|---|---|
| **只支持 UTF-8 编码** | 其他编码 → 上传被拒 |
| **不能有空行、不能有注释行**（以 `#` 开头） | 会被判为格式错误 |
| **变量名只能有 ASCII 字母、数字、下划线** | 其他字符 → 拒绝 |
| **不支持引号** | 单引号/双引号会**原样保留**，不能用来转义 |
| **值不能含换行或需要转义的特殊字符** | **多行值不支持**，GitLab 会拒绝该文件 |
| **首尾空格与换行会被 strip** | |
| **不能放敏感信息** | 官方明确：dotenv 文件内容**流水线用户可访问**；要限制访问用 `artifacts:access` |

> **"不能放敏感信息"这条要背下来**：**dotenv 不是加密通道**。你把 AK 写进 `build.env`，**任何能看流水线的人都能看到**。要传密钥，用 protected + masked 变量，或用 file 类型变量。

**dotenv 的限制（官方文档给出的数值）**：

| 限制 | 值 | 备注 |
|---|---|---|
| 单个 dotenv 文件最大大小 | **5 KB** | 官方文档写作 "Maximum file size 5 KB" |
| 默认最大继承变量数（**GitLab Self-Managed**） | **20** | 官方文档标注为 "Default maximum inherited variables on GitLab Self-Managed" |
| GitLab.com 各套餐上限 | **与套餐相关，不同套餐不同** | 官方文档指向 GitLab.com CI/CD 设置页；**本篇不写具体数字**（套餐会变） |
| 这两个限制项的引入版本 | **GitLab 17.1** | 官方 Plan limits API 文档标注 "Introduced in GitLab 17.1" |

> **面试不用背精确值，但要知道"dotenv 有大小和数量限制，不能拿来传大文件，也不能拿来传密钥"。**
> **注意 `needs` 上限同理**：官方 Plan limits 里 `ci_needs_size_limit` 默认 **50**，且**管理员可改**（可改成 100，或设成 0 彻底禁用 needs 依赖）。

**dotenv vs artifacts（关键区分）**：

| | **dotenv report** | **普通 artifact** |
|---|---|---|
| 传的东西 | **变量**（KEY=VALUE） | **文件**（任意内容） |
| 下游怎么用 | **像普通变量一样 `$MY_VAR`** | 需要 `dependencies`/`needs` 后**按路径读文件** |
| 适合 | 版本号、digest、分支名 | 二进制包、测试报告、覆盖率文件 |

> **面试这么答**："`.gitlab-ci.yml` 里 job 之间传值有两个正规通道：**`artifacts` 传文件、`dotenv` 传变量**。我们用 dotenv 把构建产物的镜像 digest 从 build job 传给 deploy job，**这样 deploy 用的是 digest 固定引用，而不是可能被覆盖的 tag**——正好接上供应链安全那一条。"

### 2.9 artifacts 与 reports

**两个概念必须分清**：

| | **artifacts（制品）** | **artifacts:reports（报告）** |
|---|---|---|
| **是什么** | job 产出的文件，**上传给后续 job / 供人下载** | 制品的**带语义形态**：GitLab 解析它并渲染到 UI |
| **典型内容** | 编译好的二进制、jar、tar 包 | junit XML、覆盖率文件、SBOM、SAST 报告 |
| **GitLab 怎么处理** | **原样存起来** | **解析后展示**：MR 面板、Tests 标签页、覆盖率图表 |
| **下载方式** | `needs` / `dependencies` 里列的 job | 同上 |
| **上传时机** | 默认 job 成功才上传（`when: on_failure` 可改） | **报告类制品无论成功失败都上传** |

**`reports` 的常见类型**：

| 类型 | 内容 | 渲染到哪 | 层级 |
|---|---|---|---|
| `junit` | JUnit XML | MR 的 Tests 面板、Pipeline 的 Tests 标签 | Free+ |
| `coverage_report` | Cobertura / JaCoCo 格式 | **MR diff 逐行覆盖率标注** | Free+ |
| `codequality` | 代码质量报告 | MR 质量报告 | Free+ |
| `dotenv` | `KEY=VALUE` 环境变量文件 | 不渲染，作为变量注入下游 | Free+ |
| `cyclonedx` | CycloneDX 格式的 SBOM | 安全面板 | **Ultimate** |
| `sarif` | SARIF 2.1.0 格式的安全发现 | 安全面板、漏洞看板 | 引入于 18.11（带 feature flag），19.1 默认开启，19.2 GA |
| `sast` / `dast` / `dependency_scanning` / `container_scanning` | 各扫描器报告 | 安全面板 | **Ultimate** |

> **注意 `sarif` 的版本演进**（官方原文）："Introduced in GitLab 18.11 with a feature flag named `sarif_ingestion`. Disabled by default. Enabled by default in GitLab 19.1. Generally available in GitLab 19.2."
> **这是"版本敏感内容必须标版本"的一个典型**——**你写 `sarif` 报告时，得知道对方的 GitLab 版本**。老版本上这个报告会静默不生效。

**artifacts 的三个关键参数**：

| 参数 | 作用 | 默认 | 写错的后果 |
|---|---|---|---|
| `paths` | 上传哪些路径 | — | **不写 `paths` 就不会上传任何东西** |
| `expire_in` | **多久后过期删除** | **未定义时用实例级默认值** | 过期后下游 job 拿不到 → `could not retrieve the needed artifacts` |
| `when` | 什么时候上传 | `on_success` | `on_failure` 让失败 job 也能上传产物（调试用） |

> **`expire_in` 的默认值不是固定的**——官方原文：“If the expiry time is not defined, it defaults to the instance wide setting.”，且 **GitLab.com 与自建实例的默认值可能不同**。**所以本篇不写具体天数**，需要时看自己实例的配置。

**artifacts 与 needs 的交互（最容易翻车的地方）**：

```
默认行为：后面的 stage 的 job 自动下载所有前面 stage 的 artifacts
用了 needs：只下载 needs 里列出的 job 的 artifacts
用了 dependencies：只下载 dependencies 里列出的 job 的 artifacts
```

> **三者互斥语义要分清**：
> - `needs` = **控制执行顺序 + 制品来源**
> - `dependencies` = **只控制制品来源，不控制顺序**
> - **官方明确建议：不要在同一个 job 里同时用 `needs` 和 `dependencies`**

### 2.10 cache 的 key 策略

**cache 解决的是什么问题**：**同一个 job 反复跑，每次都要重新下载依赖（npm install / mvn dependency:go-offline）**。cache 把这些文件存起来，下次命中就直接用。

**核心机制**：

```
job 开始
  ↓
按 cache:key 去缓存存储里找
  ↓
找到了 → 下载到 cache:paths 指定的路径（download）
  ↓
跑 job（可能会修改这些路径）
  ↓
按 policy 决定要不要上传回去（push / pull-push）
```

**`cache:key` 的四种写法**：

| 写法 | 形态 | key 怎么来 | 适合 |
|---|---|---|---|
| **静态字符串** | `key: "maven-cache"` | 固定 | 所有分支共用一份（**最容易缓存投毒**） |
| **变量拼接** | `key: "maven-$CI_COMMIT_REF_SLUG"` | 分支名 | 按分支隔离 |
| **按文件内容哈希** | `key: { files: [pom.xml] }` | **文件内容的 SHA** | ✅ **依赖文件变了才失效** |
| **按文件最近提交哈希** | `key: { files_commits: [pom.xml] }` | 该文件的最新 commit | 文件有提交就换 key（**哪怕内容没变**） |
| **前缀 + 哈希** | `key: { files: [pom.xml], prefix: $CI_JOB_NAME }` | 前缀 + 文件哈希 | 不同 job 同一份依赖也要分开存 |

**官方对 `key: files` 的限制**：

| 限制 | 值 |
|---|---|
| 最多几个文件/模式 | **10 个** |
| 能不能用 CI/CD 变量 | ❌ **不能**（因为要在创建流水线时就算出 key） |
| 文件不存在 | 忽略该文件 |
| **所有文件都不存在** | **回退到 `default` 这个 key** |

**`key: files_commits` 的限制**：

| 限制 | 值 |
|---|---|
| 最多几个文件/模式 | **2 个** |
| 与 `key: files` 能否同用 | ❌ **不能同时用在同一个 cache 配置里** |
| 行为 | **文件内容没变但有新提交，key 也会变** |

**`key` 的字符限制**：

| 限制 | 说明 |
|---|---|
| 不能含 `/` 或 `%2F` | 因为 key 会拼进 URL 路径 |
| 不能只含 `.`（无论几个） | 等价 URL 编码 `%2E` 也不允许 |
| **必须和 `paths` 一起用** | **只写 `key` 不写 `paths` → 完全不缓存**（静默失效，最难查） |
| 不同 job 用同一 key 但缓存不同路径 | **内容会互相覆盖** → 官方建议用不同 key |

> **★ 面试高频：为什么 cache key 要带依赖文件哈希？**
>
> **场景 A：key 只写分支名 → 缓存投毒**
> A 分支改了 `pom.xml` 升了依赖版本，缓存被覆盖；B 分支从旧依赖切回来，**拿到了 A 分支的新缓存** → "**本地能跑，CI 不能跑**"。这是 D4 日课场景 B 第 2 条记录的**真实事故**。
>
> **场景 B：key 写死 → 依赖升级不生效**
> `pom.xml` 升了版本，但 key 没变 → 命中旧缓存 → **CI 里跑的还是老依赖** → 依赖升级的 bug 根本没被测出来。
>
> **正解**：`key: { files: [pom.xml] }` —— **依赖清单变了，key 自动变，缓存自动失效；没变就命中。**

**`cache:policy`**：

| policy | 行为 | 什么时候用 |
|---|---|---|
| `pull` | 只下载，不上传 | **只读缓存**的 job（如纯测试） |
| `push` | 只上传，不下载 | 专门的"预热缓存"job |
| `pull-push` | 都做 | **默认** |

### 2.11 `include` 与 `extends`：两种复用方式

**这是本篇最容易被混淆的一对**，因为它们都能"复用配置"，但**复用的是什么完全不同**。

| | **`include`（包含）** | **`extends`（继承）** |
|---|---|---|
| **复用的是什么** | **一整个配置文件**（多个 job、多个关键字） | **一个 job 的若干字段**（从一个模板 job 复制） |
| **方向** | 把外部文件**拉进来**合并到当前配置 | 让当前 job **继承另一个 job 的配置** |
| **合并粒度** | **深合并（deep merge）** | **键覆盖**（同名键整个替换，不深合并） |
| **数组怎么办** | **数组不能合并，只能整体覆盖** | 同上 |
| **典型场景** | 抽公共 `before_script`、抽公共 job 定义到 `ci/common.yml` | 定义一个 `.base` 模板 job，所有 job 继承它的 `image`/`tags`/`before_script` |

**`include` 的四种来源**：

| 类型 | 写法 | 从哪来 |
|---|---|---|
| `local` | `include: [{ local: '/templates/x.yml' }]` | **同一个仓库**里的文件 |
| `project` | `include: [{ project: 'group/proj', ref: 'main', file: '/x.yml' }]` | **另一个 GitLab 项目**的指定分支 |
| `template` | `include: [{ template: 'Security/SAST.gitlab-ci.yml' }]` | **GitLab 官方提供的模板** |
| `remote` | `include: [{ remote: 'https://...' }]` | **任意 URL**（⚠️ 风险最高） |

**`include` 的合并规则（官方原文要点）**：

| 规则 | 说明 |
|---|---|
| **被包含的文件总是先被求值并合并，然后才合并主文件** | **不管 `include` 写在文件哪个位置** |
| **后包含的覆盖先包含的** | 参数重叠时，**最后一个包含的文件优先** |
| **嵌套 include 递归先合并** | |
| **合并是深合并**：两个都是 map → 逐层合并；一个不是 map → 用 B 的 | |
| **数组不能合并** | 只能整体替换 |
| **`rules` 是数组 → 不能合并，主文件优先** | 这条很关键 |
| **单个流水线最多 150 个 include**（嵌套也计入） | 超了 → 流水线创建失败 |

**`extends` 的合并规则**：

| 规则 | 说明 |
|---|---|
| **可以继承多个父 job** | `extends: [.base, .extra]` |
| **顺序：后面的父覆盖前面的父** | |
| **子 job 自己的配置覆盖父 job** | |
| **同名键整体替换，不深合并** | ⚠️ 这和 `include` 的深合并**不一样** |
| **`!reference` 可以做"局部覆盖"** | 保留父 job 的大部分，只换掉某一小段 |

```yaml
# 父模板
.base:
  image: maven:3.9-eclipse-temurin-17
  tags: [docker, linux]
  before_script:
    - mvn -v

# 子 job
build:
  extends: .base
  script:
    - mvn package -DskipTests
  before_script:      # ⚠️ 整体替换父的 before_script，不是追加！
    - echo "自定义前置"
```

> **`extends` 最容易踩的坑**：**同名数组是整体替换，不是追加**。上面例子里的 `before_script` **完全覆盖**了父 job 的 `mvn -v`，**不是两条都跑**。
>
> **需要追加怎么办**：GitLab 提供了 `!reference` 标签，**在父 job 里把某段配置抽成一个具名锚点**，子 job 用 `!reference [.base, before_script]` 引用它。

**`include` vs `extends` 选型**：

| 你要复用的东西 | 用哪个 |
|---|---|
| **一组完整的 job**（比如 5 个 job 定义） | `include` |
| **一个 job 的公共字段**（image / tags / before_script） | `extends` |
| **跨项目复用** | `include: project` |
| **跨仓库、跨团队统一模板** | `include: template` 或组件（component） |

### 2.12 `workflow`：控制整条流水线跑不跑

**`workflow` 与 job 的 `rules` 是两个层级**：

| | **job 的 `rules`** | **`workflow:rules`** |
|---|---|---|
| 决定 | **这个 job 要不要创建** | **整条 pipeline 要不要创建** |
| 位置 | job 内 | 顶层 |
| 不匹配时 | job 显示为 skipped（**流水线还在**） | **流水线根本不存在** |

**`workflow:rules` 的两条硬约束（官方原文）**：

| 约束 | 说明 |
|---|---|
| `when` **只能是 `always` 或 `never`** | 其他值（`manual` / `on_failure`）**在 workflow 层不合法** |
| **不支持 `start_in` / `allow_failure` / `needs`** | 现在写了不报错但**没有作用**，未来可能失败 → **官方建议不要写** |

**标准的 workflow 模板（生产推荐）**：

```yaml
workflow:
  rules:
    - if: $CI_COMMIT_TITLE =~ /-draft$/      # 草稿提交不跑任何流水线
      when: never
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"   # MR 流水线
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH        # 主干流水线
    - if: $CI_COMMIT_TAG                                  # tag 流水线
    - when: never                                        # 其余一律不跑
```

> **最后那条 `- when: never` 是"白名单"写法的核心**——**只要有一条规则匹配就创建流水线，没匹配就不创建。** 不写兜底规则的话，某些意外触发的流水线也会跑起来。
>
> **⚠️ 官方提示的一个坑**：
> “If your rules match both branch pipelines (other than the default branch) and merge request pipelines, duplicate pipelines can occur.”
> **也就是：同一个 commit 既推了分支又开了 MR → 跑两条流水线 → 资源翻倍。** 用 `workflow:rules` 时要显式排除掉这个组合。

**`workflow:auto_cancel`（自动取消）**：

| 配置 | 值 | 行为 |
|---|---|---|
| `on_new_commit` | `conservative` / `interruptible` / `none` | 新提交来了，旧流水线是否取消 |
| `on_job_failure` | `all` / `none` | 某个 job 失败后，是否取消整条流水线 |

> **`on_new_commit: interruptible`** 配合 job 的 `interruptible: true` 才能生效——**它只取消"可中断"的 job**。

---

## 3. 动手：一条完整流水线

> **本节给出的是"设计骨架 + 关键片段"，不是复制粘贴就跑的成品**——因为**具体镜像名、集群地址、变量名必须换成你自己的**。
> **注意**：写本篇时**没有执行任何 gitlab-runner 命令、没有登录或触发任何仓库**。

### 3.1 骨架总览

```
触发层   workflow:rules           ← 决定这条流水线跑不跑
   │
分流层   rules (每个 job)         ← 决定每个 job 跑不跑
   │
依赖层   needs (DAG)              ← 决定谁等谁
   │
执行层   image / services / tags  ← 决定在哪跑、用什么环境
   │
产出层   artifacts / reports      ← 决定产出什么、怎么传下去
   │
门禁层   退出码 / allow_failure    ← 决定什么算失败
```

### 3.2 完整示例

```yaml
# ============================================================
# .gitlab-ci.yml
# 口径：镜像仓库前缀 registry.cn-hangzhou.aliyuncs.com/linux321-devops/
#       镜像 tag 统一用 commit SHA（可追溯），生产用 digest（不可变）
# ============================================================

stages:
  - build
  - test
  - scan
  - publish
  - deploy

# ---- 全局默认（官方要求：image/services/cache/before_script 等放 default）----
default:
  image: docker:27-cli
  tags: [docker, linux, ci]        # ← runner tag 匹配，见 L2 篇
  services:
    - name: docker:27-dind
      alias: docker
  before_script:
    - echo "== job: $CI_JOB_NAME / pipeline: $CI_PIPELINE_ID =="
  variables:
    REGISTRY: "registry.cn-hangzhou.aliyuncs.com"
    IMAGE_NAME: "$REGISTRY/linux321-devops/day1-go-demo"
    IMAGE_TAG: "$CI_COMMIT_SHORT_SHA"

# ---- 整条流水线跑不跑 ----
workflow:
  rules:
    - if: $CI_COMMIT_TITLE =~ /-draft$/
      when: never
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
    - if: $CI_COMMIT_TAG
    - when: never
  auto_cancel:
    on_new_commit: interruptible

# ---- 静态检查：不等任何人 ----
lint:
  stage: build
  needs: []                          # ← 一创建就跑
  script:
    - echo "静态检查占位"
  allow_failure: true                # ← 静态检查失败不阻断（可选）

# ---- 构建镜像（多服务可并行，各自成 job）----
build:
  stage: build
  needs: []
  script:
    - docker build
        --label "org.opencontainers.image.revision=$CI_COMMIT_SHA"
        -t "$IMAGE_NAME:$IMAGE_TAG" .
    - docker push "$IMAGE_NAME:$IMAGE_TAG"
    # 把 digest 传给下游（dotenv 通道）
    - DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE_NAME:$IMAGE_TAG")
    - echo "IMAGE_DIGEST=$DIGEST" >> build.env
  artifacts:
    reports:
      dotenv: build.env
    expire_in: 1 day

# ---- 测试：只等 build，不等别的 ----
unit-test:
  stage: test
  needs:
    - job: build
      artifacts: true
  script:
    - echo "单测占位"
  artifacts:
    reports:
      junit: target/surefire-reports/TEST-*.xml
    expire_in: 1 week

# ---- 安全扫描：与测试并行 ----
scan:
  stage: scan
  image: aquasec/trivy:latest
  needs:
    - job: build
      artifacts: false          # ← 只等它完成，不下载镜像产物
  script:
    - trivy image --severity HIGH,CRITICAL --exit-code 1 "$IMAGE_NAME:$IMAGE_TAG"
    - trivy config --exit-code 1 ./k8s

# ---- 推送到正式仓库（只主干/tag）----
publish:
  stage: publish
  needs:
    - job: build
      artifacts: true
    - job: unit-test
    - job: scan
  script:
    - echo "$ACR_PASS" | docker login --password-stdin -u "$ACR_USER" "$REGISTRY"
    - docker push "$IMAGE_NAME:$IMAGE_TAG"
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
    - if: $CI_COMMIT_TAG
  allow_failure: false

# ---- 部署：dev 自动，prod 手动 ----
deploy-dev:
  stage: deploy
  needs:
    - job: publish
      artifacts: true
  script:
    - kubectl set image deployment/day1-go-demo
        app=$IMAGE_NAME@$IMAGE_DIGEST --namespace demo
  environment:
    name: dev
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH

deploy-prod:
  stage: deploy
  needs:
    - job: publish
      artifacts: true
  script:
    - kubectl set image deployment/day1-go-demo
        app=$IMAGE_NAME@$IMAGE_DIGEST --namespace prod
  environment:
    name: prod
  rules:
    - if: $CI_COMMIT_TAG
  when: manual                    # ← 人工点一下才发
```

### 3.3 每一步"看到什么算对"

| 步骤 | 预期现象 | 不对时看哪 |
|---|---|---|
| push 到 MR 分支 | 流水线页出现一条流水线，**只有 lint / build / unit-test / scan**，没有 publish / deploy | `workflow:rules` 没匹配上 |
| lint | **秒级就绿**（因为 `needs: []`） | 仍然等了很久 → `needs: []` 没生效 |
| build | 日志里出现 `docker push` 的 `digest: sha256:...` | 报 `unauthorized` → 见 L3 篇 6.1 |
| unit-test | MR 的 Tests 面板出现测试用例数与结果 | 面板空 → `junit` 路径写错 |
| scan | 漏洞多时**退出码非 0、流水线红** | 仍绿 → `--exit-code 1` 没加 |
| publish | 只在主干/tag 流水线里出现 | MR 流水线里也出现 → `rules` 写宽了 |
| deploy-prod | 流水线**停在这个 job 等点击** | 自动跑了 → `when: manual` 或 `rules` 没生效 |

---

## 3.4 常用运维命令面板（按场景）

> **本节能做什么**：3.2 是"一条完整流水线的写法"，本节是**每天值班 / 面试都会敲的 GitLab CI 运维排障命令**——按场景归类，每条配真实例子。**CI 排障的肌肉记忆就来自这一张表。**

### ① 语法预检（提交前先拦住低级错）

```bash
# 用 GitLab 官方的 CI Lint API 校验 .gitlab-ci.yml（不用真触发流水线）
curl --header "PRIVATE-TOKEN: <你的token>" \
     --form "content=<$(cat .gitlab-ci.yml)" \
     "https://gitlab.example.com/api/v4/ci/lint"
# 返回 { "valid": true/false, "errors": [...] }——push 前先跑一遍

# 本地用 gitlab-ci-yml 社区工具做静态检查（需 Node 环境）
npx gitlab-ci-yml-validator .gitlab-ci.yml     # 本地快速验结构
```

> **实战价值**：`ci/lint` 是"提交前最后一道语法闸"。很多团队把 lint 接进 pre-commit 钩子，**把 `yaml 缩进错 / stage 没定义 / needs 指向不存在的 job` 这类错在本地就拦掉**，而不是等流水线红。

### ② 流水线 / Job 生命周期管理

```bash
glab ci list                                   # 列出最近流水线
glab ci view <pipeline-id>                     # 看某条流水线的所有 job 与状态
glab ci status                                 # 当前分支最新流水线状态
glab ci retry <job-id>                         # 重试某个失败的 job（不重跑整条）
glab ci cancel <pipeline-id>                   # 取消一条正在跑的流水线
glab ci trace <job-id>                         # 实时跟某个 job 的日志（等价于网页点进去）
```

> **实战价值**：**面试常问"流水线卡住你怎么看"**——第一步永远是 `glab ci view` 看哪个 job 卡、状态是什么（pending / running / failed / canceled），再决定 retry 还是 cancel。重试单个 job 比重跑整条省时间，也避免把已经过的 stage 再跑一遍。

### ③ Runner 注册与排查

```bash
gitlab-runner register                          # 交互式注册（填 URL / token / executor）
gitlab-runner verify                            # 校验本机 runner 能不能连上 GitLab（心跳）
gitlab-runner status                            # 看 runner 进程在不在、是不是 active
gitlab-runner list                              # 列出本机注册过的 runner 及其状态
sudo gitlab-runner restart                      # runner 抽风时重启服务
sudo gitlab-runner uninstall && gitlab-runner install  # 服务注册坏了重建
```

> **实战价值**：`verify` 是判断"runner 离线还是 job 本身挂"的第一招——`verify` 通说明 runner↔GitLab 链路没问题，问题在 job 脚本；`verify` 不通说明 **token 过期 / 网络不通 / 服务没起**，去查 `/var/log/gitlab-runner/`。

### ④ 变量与密钥排查

```bash
glab variable list -p <project>                 # 看项目级 CI 变量有哪些（不显示值）
glab variable get CI_REGISTRY_TOKEN -p <project> # 查某个变量（值是否 masked 看不到明文）
# 在 job 日志里确认变量是否被正确注入：
echo "$CI_COMMIT_REF_NAME / $CI_PIPELINE_ID"     # 内置变量随手打印，排"环境判断错"
```

> **实战价值**：**"变量没生效"是 CI 里最隐蔽的错之一**。Protected 变量只在 protected 分支/tag 才注入，普通分支跑出来是空——`glab variable list` 先看作用域，再在 job 里 `echo` 内置变量定位是"没传"还是"传了但作用域不对"。

### ⑤ 排障：流水线卡 pending / runner 离线

```bash
glab ci view <pipeline-id>                      # 看卡住的 job：status=pending 说明在等 runner
gitlab-runner verify                            # runner 侧：能不能连上 GitLab
gitlab-runner status                            # runner 进程活不活
sudo systemctl status gitlab-runner             # 服务级状态（dead 就 restart）
# 网页 Admin → Runners 看 runner 是不是被 paused / 是不是 tag 不匹配
```

> **实战价值**：job 一直 `pending` 只有三种可能——**没有匹配的 runner（tag 不对）、runner 被 paused、runner 离线**。先 `verify`+`status` 在 runner 侧定位，再回网页看 tag/paused，别一上来就改 `.gitlab-ci.yml`。

---

## 3.5 GitLab CI 排障实战（3 个场景串命令）

> **把 3.4 的命令用起来**：三个最典型的 CI 故障，按"现象 → 敲的命令 → 怎么读"走一遍。**优先在测试项目上练，别在主干试。**

### 场景 A：流水线一直 pending（等不到 runner）

```bash
glab ci view <pipeline-id>                      # 现象：job 状态 pending，一直不动
gitlab-runner status                            # runner 进程 active 吗？
gitlab-runner verify                            # 能连上 GitLab 吗？报错就是 token/网络
# 网页 Admin → Runners：看该 runner 的 tag 与 job 的 tags 是否对得上
```

> **读法**：pending = 没人接单。`verify` 通但还 pending → **tag 不匹配或 runner 被 paused**（网页看）；`verify` 不通 → runner 离线/服务挂，去 `systemctl restart gitlab-runner`。

### 场景 B：job 失败，但不知道卡在哪

```bash
glab ci trace <job-id>                          # 实时跟日志，定位失败的那一行
glab ci retry <job-id>                          # 修完依赖/脚本后只重跑这一个 job
echo "$CI_COMMIT_REF_NAME"                      # 在 job 里打印内置变量，确认环境判断对不对
```

> **读法**：`trace` 看到底是测试挂、构建挂还是推送 401；**重试单个 job** 比重跑整条快，也避免把 lint/build 这种已经过的再跑一遍。

### 场景 C：`.gitlab-ci.yml` 推上去就报语法错

```bash
curl --header "PRIVATE-TOKEN: <token>" \
     --form "content=<$(cat .gitlab-ci.yml)" \
     "https://gitlab.example.com/api/v4/ci/lint"   # 提交前本地先 lint
npx gitlab-ci-yml-validator .gitlab-ci.yml         # 或社区工具静态校验
```

> **读法**：返回 `valid: false` 会带 `errors` 数组，常见是 **stage 没在 stages 里定义、needs 指向不存在的 job、yaml 缩进错**。本地 lint 一遍再推，比等流水线红省一轮往返。

> **三个场景的共同心法**：**先 `view`/`trace` 看清状态与日志 → 再 `verify`/`status` 分清"runner 问题还是 job 问题" → 最后才是改 yaml 或 retry**。

---

## 4. 逐字段表

### 4.1 job 关键字表

> **本表覆盖官方 `.gitlab-ci.yml` 作业关键字的常用子集**，按"是否会改变执行行为"分组。

| 关键字 | 类型 | 作用 | 典型用法 | 写错的后果 |
|---|---|---|---|---|
| `script` | 数组 | **job 的主体命令**，由 runner 执行 | `script: [ "mvn package" ]` | 写成字符串而非数组 → **GitLab 允许但可读性差**；命令返回非 0 → job 失败 |
| `stage` | 字符串 | job 所属阶段 | `stage: build` | 阶段名不在 `stages:` 里 → **`chosen stage does not exist`**；不写 → **落入默认 `test` 阶段** |
| `image` | 字符串 | job 跑在哪个容器镜像里 | `image: docker:27-cli` | 镜像里没有你要用的工具（如没装 kubectl）→ 命令 not found |
| `services` | 数组 | 伴随 job 启动的辅助容器 | `services: [{name: docker:27-dind, alias: docker}]` | **用服务名当主机名**（`docker:2375`），写 `localhost` 连不上 |
| `before_script` | 数组 | `script` 之前执行 | `before_script: [ "mvn -v" ]` | **这里 `exit 1` 会终止整个 job**（后面 script 不跑） |
| `after_script` | 数组 | job 结束后执行 | `after_script: [ "rm -rf tmp/" ]` | **默认不改变 job 退出码**——清理失败不会让 job 失败（也可能是你想要的） |
| `rules` | 数组 | **决定 job 是否被创建 + 命中时的属性** | `rules: [{if: '$CI_COMMIT_BRANCH == "main"'}]` | **规则顺序写反** → 命中的是你想跳过的分支；**引用了被排除的 job 且没加 `needs:optional`** → 流水线创建失败 |
| `needs` | 数组 | **显式依赖，打破 stage 串行** | `needs: [{job: build, artifacts: true}]` | **用了 `needs` 后只下载 needs 里的 artifacts** → 以前"自动下载全部"的逻辑失效；**超过 50 个** → 创建失败 |
| `dependencies` | 数组 | 只限制**制品来源**，不控制顺序 | `dependencies: [build]` | **与 `needs` 混用** → 官方明确不建议，行为易错 |
| `artifacts` | 映射 | 声明产出物 | `artifacts: {paths: [dist/], expire_in: 1 day}` | 不写 `paths` → **什么都不上传**；`expire_in` 太短 → 下游拿不到 |
| `artifacts:reports` | 映射 | 声明**带语义**的报告 | `reports: {junit: "*.xml"}` | 路径写错 → **UI 面板空白但不报错**（最难查的一类） |
| `cache` | 映射 | 声明缓存路径与 key | `cache: {key: {files: [pom.xml]}, paths: [.m2/]}` | **只写 key 不写 paths → 完全不缓存**（静默）；key 太宽 → **缓存投毒** |
| `when` | 字符串 | 何时跑 | `when: manual` / `never` / `always` / `on_failure` | `when: never` 放错层级 → job 被跳过；workflow 层用 `manual` → **不合法** |
| `allow_failure` | 布尔 | 该 job 失败是否算流水线失败 | `allow_failure: true` | **设了 `true` 就等于没有门禁**——扫描类 job 千万别随手设 true |
| `retry` | 映射/数字 | 失败后自动重试 | `retry: {max: 2, when: runner_system_failure}` | 无差别重试 → **掩盖真实 bug**，把偶发失败重跑成"绿的" |
| `timeout` | 字符串 | job 超时 | `timeout: 30m` | **不设 = 依赖项目级默认**；网络任务不设超时 → **卡死占着 runner** |
| `interruptible` | 布尔 | 新提交到来时可否取消本 job | `interruptible: true` | 与 `workflow:auto_cancel: on_new_commit: interruptible` **必须配合**，单设无效 |
| `parallel` | 数字/映射 | 一个 job 拆成多个并行实例 | `parallel: 3` 或 matrix | matrix 写法写错 → 变量引用不到；**并行度受 runner 数限制**（见 2.3） |
| `environment` | 字符串/映射 | 声明部署目标环境 | `environment: {name: prod}` | 环境名写错 → **部署记录散落在多个假环境**；配了 `url` 才能在 UI 点开 |
| `trigger` | 字符串/映射 | 触发下游/子流水线 | `trigger: {project: g/p, strategy: dependent}` | `strategy` 写错 → **父流水线不等下游**，父先绿了下游才红 |
| `extends` | 字符串/数组 | 继承其他 job 的配置 | `extends: .base` | **同名数组整体替换不是追加**（见 2.11） |
| `tags` | 数组 | **匹配 runner** | `tags: [docker, linux]` | **没有 runner 带这些 tag → job 一直 pending**（见 6.1） |
| `variables` | 映射 | job 级变量 | `variables: {DEPLOY_ENV: prod}` | 变量名拼错 → **取到空字符串，不报错** |
| `resource_group` | 字符串 | **限制同名资源的并发** | `resource_group: prod-db` | 不写 → **多个 job 同时操作同一资源**（如同时部署同一环境） |
| `coverage` | 字符串 | 从输出里正则提取覆盖率 | `coverage: '/Total.*?([0-9]+%)/'` | 正则不匹配 → **覆盖率显示 0%**（不是没覆盖，是没解析出来） |
| `inherit` | 映射 | 选择继承哪些 default | `inherit: {variables: false}` | 继承了不需要的变量 → **子 job 拿到了生产密钥** |
| `secrets` | 映射 | 从外部密钥管理器取密钥 | `secrets: {AK: {vault: ...}}` | 权限没配 → job 启动即失败 |
| `id_tokens` | 映射 | 申请 OIDC 身份 | `id_tokens: {CI_JOB_JWT_V2: {aud: ...}}` | aud 写错 → **OIDC 换取凭证失败**（keyless 签名要用） |

### 4.2 variables 作用域表

> **优先级从高到低**（官方口径，见 2.6）。

| 层级 | 定义位置 | 生效范围 | 典型用途 | 覆盖关系 / 写错的后果 |
|---|---|---|---|---|
| **执行策略变量** | pipeline / scan execution policy | **仅该策略添加的 job** | 合规扫描策略注入参数 | 最高优先级；**只覆盖策略加的 job**，其他 job 不受影响 |
| **手动运行变量** | Run pipeline 页面 / 手动触发 job 时填 | 该次触发的流水线 | 临时指定分支、开关 | 高于所有配置文件变量；**临时值不会持久** |
| **流水线级变量** | Run pipeline 页 / schedule / API / triggers API / push option / **上游流水线传递** | 该条流水线所有 job | 定时任务参数、下游传递 | 高于项目级；**上游传下来的会覆盖下游同名变量**（跨流水线时慎用同名） |
| **项目级变量** | Settings → CI/CD → Variables（项目） | 本项目所有流水线 | 项目自己的镜像仓库凭据 | 被流水线级覆盖；**项目之间不共享** |
| **群组级变量** | 群组 Settings → CI/CD → Variables | 群组下所有项目 | 群组统一的 registry 凭据 | 同名时**取最近的子群组**；比项目级低 |
| **实例级变量** | Admin Area | 整个 GitLab 实例 | 实例级共享凭据 | 最低的自定义层级；**能配实例级就意味着所有人可见** |
| **`dotenv` 报告变量** | 上游 job 的 `artifacts:reports:dotenv` | **仅 `dependencies`/`needs` 里列出的 job** | 传递 digest、版本号 | **只对列出的 job 生效**；**文件最大 5 KB、Self-Managed 默认最多继承 20 个变量**，超限会被拒 |
| **`.gitlab-ci.yml` 内（4 档）** | 见下方细分 | — | — | **文件内部也有优先级** |
| ├─ ① job 的 `rules:variables` | job 内 `rules` 的某条 | **该规则命中时该 job** | 分环境变量 | 最高（文件内）；**规则没命中就完全不存在** |
| ├─ ② job 的 `variables` | job 内 | 该 job | job 专属配置 | 覆盖 ③ 和 ④ |
| ├─ ③ `workflow:rules:variables` | 顶层 `workflow.rules` | **整条流水线** | 按流水线类型注入 | 覆盖顶层 ④；**被 job 级覆盖** |
| └─ ④ 顶层 `variables` | 顶层 | 所有 job | 全局默认配置 | 文件内最低 |
| **部署变量** | environment 配置 | 该环境的 job | 环境专属密钥 | 低于所有配置文件变量 |
| **预定义变量** | GitLab 自动提供 | 全部 | `$CI_COMMIT_SHA` 等 | **最低**（部分除外：`CI_ENVIRONMENT_ID` / `CI_ENVIRONMENT_SLUG` / `CI_ENVIRONMENT_URL` / `CI_PAGES_URL` **不可覆盖**） |

> **`secrets` 不在这张表里**——官方明确：runner 在 job 运行时才去外部密钥管理器取，**不参与这套优先级**。
> **`inputs` 也不是变量**——流水线创建时就被替换，运行时改不了。

---

## 5. 横向对比

### 5.1 `rules` vs `only` / `except`

**官方定性（原文）**：

> "`only` and `except` are deprecated. To control when to add jobs to pipelines, use `rules` instead."

> **中文来源（社区实践）普遍还在用 `only/except`，但官方已明确弃用并排期移除——新项目一律用 `rules`，以官方为准。**

| 维度 | `rules` | `only` / `except` |
|---|---|---|
| **官方状态** | ✅ **推荐** | ⚠️ **已 deprecated**（仍可用，有告警） |
| **匹配语义** | **自上而下，首个匹配即生效** | `only` 是白名单、`except` 是黑名单，**两个都要单独判断** |
| **表达"排除"** | `when: never`（一条规则） | 要单独写 `except` |
| **能在同一条规则里改属性** | ✅（`when` / `allow_failure` / `variables` / `needs` 一起写） | ❌ **不能** |
| **能判断变量** | ✅ `if: $X == "y"` | ⚠️ 旧写法有 `only:variables`，**也已 deprecated** |
| **能判断文件变更** | ✅ `changes:`（可指定基线 `compare_to`） | ⚠️ `only:changes` **也已 deprecated** |
| **能判断文件是否存在** | ✅ `exists:` | ❌ 没有 |
| **能配合 `workflow`** | ✅ 同一套语法 | ❌ |
| **可读性** | 顺序即优先级，**要仔细排** | 简单场景更直观 |

**同一个需求的两种写法**：

```yaml
# ❌ 旧写法：黑名单 + 白名单两套逻辑
deploy:
  script: deploy.sh
  only:
    - main
    - /^release-.*/
  except:
    - schedules

# ✅ 新写法：一条一条列清楚，第一条命中即生效
deploy:
  script: deploy.sh
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
    - if: $CI_COMMIT_BRANCH =~ /^release-.*/
      when: on_success
    - if: $CI_PIPELINE_SOURCE == "schedule"
      when: never
```

> **`only`/`except` 的一个隐蔽行为差异（官方明确写了）**：
> “If a job does not use `only`, `except`, or `rules`, then `only` is set to `branches` and `tags` by default.”
>
> **也就是说：什么都不写的 job，默认在分支和 tag 上跑。** 但**一旦你写了 `only` 或 `except`（哪怕只写 `except`），这个默认就被替换掉**。**同样的 job，一个写 `except: [schedules]`（会跑在几乎所有地方），一个写 `rules: [{if: ...}]`（可能一个都不跑）**——语义完全不同。

### 5.2 `needs` vs 依赖 stage 顺序

| 维度 | **默认 stage 顺序** | **`needs`** |
|---|---|---|
| **依赖粒度** | **stage 级**（整个阶段） | **job 级**（精确到每个 job） |
| **并行度** | 上一阶段**全部**成功才进下一阶段 | 只等 `needs` 里列出的那几个 |
| **等待什么** | 等**所有**前序 stage 的 job | 只等列出的 job |
| **artifacts 来源** | 自动下载**所有**前序 stage 的 artifacts | **只下载 `needs` 里的 artifacts** |
| **典型场景** | 强顺序流程（编译→打包→部署） | 多服务并行、按需触发 |
| **风险** | **一个慢 job 拖住整条线** | **漏写 `needs` → 上游没跑就跑了**；**漏列 → artifacts 拿不到** |

**选型依据（一句话）**：

> **"必须等所有人" → 用 stage；"我只需要等某几个" → 用 `needs`。"**

**DAG 改造前后的对比**（以多服务为例）：

```
# 改造前：所有服务编完 → 全部测试 → 全部打包
build-a ─┐
build-b ─┼─▶ test-a ─┐
build-c ─┘   test-b ─┼─▶ package ─▶ deploy

# 改造后：每个服务自己的测试跟着自己的构建走
build-a ─▶ test-a ─┐
build-b ─▶ test-b ─┼─▶ package ─▶ deploy
build-c ─▶ test-c ─┘
```

> **`build-c` 编译慢 10 分钟，改造前 `test-a` 只能干等；改造后 `test-a` 早就跑完了。**
> **这就是 D4 日课场景 B 第 1 条"流水线从 T1 降到 T2"的第一段拆解。**

### 5.3 `when: manual` vs `when: on_failure`

**这两个 `when` 常被搞混，但一个在"正常路径"上，一个在"异常路径"上。**

| 维度 | `when: manual` | `when: on_failure` |
|---|---|---|
| **何时执行** | **等人工点** | **前序失败时自动执行** |
| **正常情况下** | **不执行**（流水线停住等人） | **不执行** |
| **失败情况下** | — | **自动执行**（典型是清理/回滚） |
| **流水线状态** | 停住时显示"待手动执行"（blocking） | 跑完后按执行结果算 |
| **典型用途** | **生产发布审批** | **失败自动回滚 / 失败通知 / 收集诊断信息** |
| **与 `allow_failure` 配合** | 常配 `allow_failure: true`（**默认行为**），让人点按钮不算流水线失败 | 一般配 `allow_failure: true`（清理失败别掩盖原始失败） |

**典型用法**：

```yaml
# 正常路径：生产发布要人点
deploy-prod:
  stage: deploy
  script: kubectl apply -f prod/
  environment: { name: prod }
  when: manual
  rules:
    - if: $CI_COMMIT_TAG

# 异常路径：失败了自动收集诊断信息（不影响主流程结论）
collect-diagnostics:
  stage: deploy
  script:
    - kubectl describe deploy -n prod > diag.txt
    - kubectl logs -n prod --tail=200 > logs.txt
  artifacts:
    paths: [diag.txt, logs.txt]
    expire_in: 3 days
  when: on_failure
  allow_failure: true
```

> **`allow_failure` 的默认值要记牢**：**`when: manual` 的 job 默认就是 `allow_failure: true`**（因为"等人点"不是错误）。**但如果你希望"没点就等于失败"，必须显式写 `allow_failure: false`。**
>
> **面试这么答**："`when: manual` 是**正路**上的闸门——生产发布等审批；`when: on_failure` 是**岔路**上的处理——失败了自动回滚或抓诊断信息。**一个流水线里通常两者都有**。"

### 5.4 artifacts vs cache

**这两个都叫"文件"，但方向完全相反。这是本篇最容易搞混的一对。**

| 维度 | **artifacts（制品）** | **cache（缓存）** |
|---|---|---|
| **方向** | **向下游传**（上游 → 下游） | **跨 job 复用**（任何用到相同 key 的 job） |
| **用途** | **产出传递**：编译产物、测试报告、SBOM | **加速复用**：依赖目录、构建缓存 |
| **能不能被人工下载** | ✅ 能（UI 上有下载按钮） | ❌ 不能（内部机制） |
| **有没有有效期** | ✅ **有**（`expire_in`，到期删） | ✅ 有（缓存存储的保留策略） |
| **key 的作用** | 无（靠 `needs`/`dependencies` 关联） | ✅ **决定命中与否** |
| **命中失败的典型原因** | 制品过期 / 路径写错 / 权限不足 | **key 不匹配 / 路径不存在 / policy 是 pull-only** |
| **不写会怎样** | **什么都不传** | **每次全量重装依赖（慢，但不出错）** |

**一句话记法**：

> **artifacts 是"这次做的结果，要交给下游"；cache 是"上次的结果，给这次复用"。**
> **artifacts 传的是**产出**，cache 存的是**中间态**。**

**混用会出的错**：

| 错误 | 现象 |
|---|---|
| **把依赖目录当 artifacts 传** | 每次都上传几十 MB 的 `node_modules`，**慢且占存储**（该用 cache） |
| **把构建产物当 cache 存** | key 写什么都是"最新的"→ **测的和发的可能不是同一个东西**（该用 artifacts） |
| **artifacts 忘了 `expire_in`** | 用实例默认值（**各实例不同**）→ 过期后下游报 `could not retrieve the needed artifacts` |
| **cache key 只写分支名** | **缓存投毒**：A 分支的依赖被 B 分支复用（该用 `key: {files: [...]}`） |

---

## 6. 踩坑与排障

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **YAML 解析报错**（`mapping values are not allowed in this context` / `could not find expected ':'` / `bad indentation of a mapping entry`） | 流水线**根本没创建**，UI 上看不到任何东西 | ① **缩进用了 Tab**（YAML 只认空格）；② 某个值里有 `: `（冒号+空格）没加引号；③ `-` 列表项与父键的缩进层级不对 | 全程用**空格**；含 `: `、`#`、以 `-`/`?`/`{`/`[` 开头的值一律**加引号** | 提交前用 `yamllint` 或 GitLab 自带的 CI Lint（Web IDE → CI Lint）**先校验**；**养成"本地先过一遍 lint"的习惯** |
| **流水线存在，但 job 不执行**（灰色 skipped） | job 在流水线图里显示为 skipped | ① **`rules` 的 `if` 条件没匹配**（变量名拼错、字符串没引号）；② **规则顺序**导致先命中了 `when: never`；③ **job 引用的 `needs` 目标被 rules 排除了** | 逐条核对 `if` 里的变量（`$CI_COMMIT_BRANCH` vs `$CI_COMMIT_REF_NAME`）；**把 `when: never` 放最后**；给 `needs` 加 `optional: true` | **每条 `rules` 单独验证**：在 GitLab Web IDE 的 CI Lint 里输入变量值试算；**兜底规则永远写 `when: never` 放最后** |
| **job 一直 pending**（`This job is stuck because the project doesn't have any runners online assigned to it`） | job 状态 pending，不占资源也没输出 | **没有任何 runner 的 tag 与 job 的 `tags` 匹配**（tag 写错 / runner 没注册 / runner 离线） | 对照 runner 的 `config.toml` 里的 `tag_list` 与 job 的 `tags`；**两者是"子集"关系**——job 要的 tag runner 必须**全都有** | **tag 命名规范统一**（如 `docker` / `linux` / `ci` / `prod`）；**建 job 前先确认 runner 的 tag 列表**；生产用独立 tag 与 runner 池 |
| **缓存不命中，每次都重装依赖** | job 日志里没有 "Restoring cache" / "Successfully extracted cache" | ① **`key` 与 `paths` 只写了一个**（只写 key → **静默不缓存**）；② `paths` 路径在容器里**不存在**（`~/.m2` 在容器里是 `/root/.m2`）；③ 上游 job 的 `policy` 是 `pull`（只下不上）；④ **key 太精确**，每次都不一样 | 补齐 `key` + `paths`；**路径用容器内的绝对路径**；`policy: pull-push`；把 key 改成粗一点先验证链路 | **`key` 用 `key: { files: [pom.xml] }`**（依赖清单哈希）；**paths 一律写绝对路径**；首次接入时**故意改一次依赖文件**验证 key 会变 |
| **缓存命中了但内容是脏的**（"本地能跑，CI 不能跑"） | job 跑的是**旧依赖** | **key 太宽**（只写分支名或固定字符串）→ **A 分支的缓存被 B 分支复用** | 改用 `key: { files: [pom.xml] }` 或 `key: { files_commits: [pom.xml] }` | **缓存 key 必须绑定依赖清单的哈希**；**不同分支/不同服务用不同 prefix** |
| **下游 job 拿不到 artifacts**（`This job could not start because it could not retrieve the needed artifacts`） | 下游 job 启动失败，流水线红 | ① **artifacts 已过期**（`expire_in` 太短，或用的是实例默认值）；② **用了 `needs` 但没把上游列进去**（`needs` 一用就只下载列出的）；③ 上游 job 失败 → 没上传制品；④ `reports` 路径写错 → 文件不存在 | 延长 `expire_in`；把上游加进 `needs`；确认上游成功；用 `artifacts:when: always` 让失败也上传 | **artifacts 与 cache 分开设计**：制品用 `expire_in` 明确设（如 1 day / 1 week）；**别依赖实例默认值**；`needs` 写全 |
| **报告面板是空的**（Tests / Coverage / Security 面板没数据） | 流水线绿，但 UI 上没内容 | **报告路径写错**（如实际生成在 `target/surefire-reports/` 却写了 `*.xml` 在根目录）；或工具根本没生成那个文件 | 对照 job 工作目录用 `ls` 确认文件真实位置 | **报告路径用通配符**（`target/surefire-reports/TEST-*.xml`）；**在 script 里加一步 `ls -l` 打印产物** |
| **`$` 变量在 YAML 里被解析出问题** | 变量取到空值，或 YAML 报错 | ① **值里含 `: `（冒号+空格）没加引号** → YAML 当成新键；② **值以 `$` 开头且未加引号** → YAML 会当字符串处理，但**配合 `if:` 表达式时容易出意外**；③ **在 YAML 里写了 `!reference` / `!env` 之类的标签** | **含特殊字符的值一律加引号**：`if: '$CI_COMMIT_BRANCH == "main"'` | **规则里的表达式整体加单引号**；值里出现 `: `、`#`、`{`、`[`、`*`、`&`、`!`、`>`、竖线、`%`、`@` 时**一律加引号** |
| **CI 日志里泄漏了密钥** | 日志出现 AK/SK 明文 | ① **变量没设 masked**；② **脚本里 `echo $SECRET`**（掩码只挡"变量原样出现"，`echo` 出来仍会被替换，但如果做了 base64/拼接就挡不住）；③ **`printenv` / `env`** 打印全部环境变量；④ **变量值带转义字符**（`My[value]` → 输出成 `My\[value\]`）**导致掩码失效** | 变量设 **masked + protected**；脚本里**禁止 echo 密钥**；改用 **file 类型变量**或外部 secrets；**已经泄漏的凭据立刻吊销轮换** | **密钥只在受保护分支可用**（protected）；**日志里禁止出现密钥**；**把"密钥泄漏"当凭据泄漏处理——立即轮换**，不是改个日志格式了事 |
| **多分支同时提交，流水线互相覆盖** | 后一次 push 把前一次的构建产物冲掉 | **同一个 branch/tag 上有多条流水线在跑**，后一条的 artifacts 覆盖了前一条 | 用 `needs:pipeline` / `needs:project` 明确引用**具体流水线 ID**；或用 `resource_group` 串行化同 ref 的部署 job | **同 ref 的部署 job 加 `resource_group`**；跨流水线取制品时**显式指定 pipeline id** |
| **MR 流水线与分支流水线重复跑** | 同一个 commit 出现两条流水线 | `workflow:rules` 同时匹配了**分支**（非默认分支）和 **MR** 两种来源 | `workflow:rules` 里显式区分 `$CI_PIPELINE_SOURCE`；分支那条加 `if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH` | **写 `workflow:rules` 时把来源枚举清楚**；MR 用 `merge_request_event`，分支用 `CI_COMMIT_BRANCH` 判断 |
| **`environment` 部署记录散落一地** | UI 上出现一堆 `dev-1` `dev-2` 之类的环境 | `environment.name` 里写了变量（如 `$CI_COMMIT_BRANCH`）→ **每次提交都是一个新环境名** | 环境名用**固定值**（`dev` / `prod`），要区分就用 `environment:name` + `environment:url` | **环境名固定，变量放别处**；环境名一旦创建就是"部署记录"，不要当变量用 |
| **`when: manual` 的 job 点了还是不算失败 / 或者卡住整条流水线** | 流水线状态显示"blocked" | `when: manual` 的 job **默认 `allow_failure: true`**（不算失败，但会**阻塞**后续 stage） | 明确写 `allow_failure: false`（**要求人工确认才算通过**）或 `true`（**不管它**） | **生产发布 job 显式写 `allow_failure: false`**（把"人点了"变成"必须有人点"） |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `docs.gitlab.com/ci/` | **CI 总览**：pipeline / stage / job 概念、Runner 的角色、CI/CD 变量与 expressions、CI/CD component | ✅ 2026-10-24 |
| S2 | 官方 | **`docs.gitlab.com/ci/yaml/`** | **`.gitlab-ci.yml` 完整语法参考**：全局 / 头部 / 作业三类关键字全集、`stages` 默认值（`.pre`/`build`/`test`/`deploy`/`.post`）与"同阶段并行、跨阶段串行"语义、`workflow`（`rules` / `auto_cancel` / `when` 仅 `always`/`never` / 不支持 `start_in`·`allow_failure`·`needs`）、`rules`（`if`/`changes`/`exists`/`variables`/`allow_failure`）、`needs`（`needs:artifacts` / `needs:optional` / 上限 50）、`artifacts`（`paths`/`expire_in`/`reports`）、`cache`（`key` / `key:files` / `key:files_commits` / `key:prefix` / `policy` / `fallback_keys`）、`include`、`extends`、`when`、`retry`、`timeout`、`allow_failure`、`interruptible`、`parallel`、`trigger`、`environment`、`resource_group`、`tags` | ✅ 2026-10-24 |
| S3 | 官方 | `docs.gitlab.com/ci/yaml/deprecated_keywords/` | **`only` / `except` 已 deprecated**、`only:refs` / `only:variables` / `only:changes` 的弃用说明、**顶层 `image`/`services`/`cache`/`before_script`/`after_script` 已 deprecated（官方要求改用 `default`）**、**未写 `only`/`except`/`rules` 时默认 `only: branches + tags`** | ✅ 2026-10-24 |
| S4 | 官方 | **`docs.gitlab.com/ci/variables/`** | **变量优先级完整顺序（10 档 + `.gitlab-ci.yml` 文件内 4 档）、`protected` 与 `masked` 的官方定义与差异、masked 值的三个硬性要求（单行无空格 / ≥8 字符 / 不与已有变量同名）、"掩码不是防止恶意用户获取值的保证"、file 类型变量** | ✅ 2026-10-24 |
| S5 | 官方 | `docs.gitlab.com/ci/yaml/artifacts_reports.html` | **`artifacts:reports` 各类型**（`junit` / `coverage_report` / `codequality` / `dotenv` / `cyclonedx` / `sarif` / 安全类）、**报告制品无论 job 成败都上传**、`sarif` 的版本演进（18.11 feature flag → 19.1 默认开 → 19.2 GA） | ✅ 2026-10-24 |
| S6 | 官方 | `docs.gitlab.com/ci/yaml/includes/` | **`include` 的四种来源（local/project/template/remote）、深合并规则、数组不可合并、`rules` 数组主文件优先、150 个 include 上限** | ✅ 2026-10-24 |
| S7 | 官方 | **`docs.gitlab.com/ci/variables/dotenv_variables/`** | **dotenv 的格式限制（仅 UTF-8 / 不能有空行与注释 / 变量名仅 ASCII 字母数字下划线 / 不支持引号 / 不支持多行值）、大小与数量限制（文件最大 5 KB；Self-Managed 默认最多继承 20 个变量；GitLab.com 按套餐不同）、"不能在 `rules` 里使用 dotenv 变量"、"dotenv 文件不含敏感信息"** | ✅ 2026-10-24 |
| S8 | 官方 | **`docs.gitlab.com/runner/`** | **Runner 的定义与执行流程、术语表（Runner / runner configuration / runner manager / machine / executor / tags / concurrent jobs）、GitLab-hosted 与 Self-managed 两类部署方式、Runner 版本需与 GitLab 主次版本对齐** | ✅ 2026-10-24 |
| S9 | 官方 | **`docs.gitlab.com/runner/register/`** | **runner authentication token（`glrt-` 前缀）替代 registration token、registration token 与相关参数的弃用与移除排期（GitLab 20.0）、GitLab 17.0 起可禁用旧注册流程** | ✅ 2026-10-24 |
| S10 | 官方 | `docs.gitlab.com/api/plan_limits` | **实例级限制**：`ci_needs_size_limit` 默认 50、dotenv 大小与数量限制项（GitLab 17.1 引入）——**管理员可改，故本篇只标默认值不标"必然值"** | ✅ 2026-10-24 |
| S11 | 中文 | `cloud.tencent.com/developer` | **中文实践**：GitLab CI 流水线组织、缓存 key 设计与门禁实践案例 | ✅ 2026-10-24 |
| S12 | 中文 | `developer.aliyun.com` | **中文实践**：流水线即代码、制品与缓存的组织方式 | ✅ 2026-10-24 |
| S13 | 云厂商 | `help.aliyun.com/zh/acr/user-guide/` | **ACR 推送与镜像仓库凭据使用**（CI 里 `docker login` + `docker push` 的对象） | ✅ 2026-10-24 |
| S14 | 官方 | `trivy.dev/latest/docs/` | **Trivy 镜像漏洞扫描 / 配置扫描 / SBOM 生成与 `--exit-code` 门禁**（scan job 用法） | ✅ 2026-10-24 |

> **口径说明**：
> - 本篇引用的**所有 GitLab 行为均以 `docs.gitlab.com` 官方文档为准**（S1–S10）。中文社区来源（S11–S12）**仅用于建立中文术语直觉与实践案例，不作为"字段到底怎么写"的依据**——官方已明确 `only`/`except` 弃用而社区仍在广泛使用，**这类冲突一律以官方为准**。
> - **未编造任何默认值**：官方明确「`artifacts:expire_in` 未定义时用实例级设置」，且 **GitLab.com 与自建实例默认值可能不同**，因此本篇**不写具体天数**。`needs` 上限 50、`dotenv` 文件最大 5 KB / Self-Managed 默认最多继承 20 个变量，均为官方文档明写的数值，**并已标注各自的适用边界**。
> - **版本敏感项已标版本**：`sarif` 报告（18.11 → 19.1 → 19.2）、registration token 移除排期（GitLab 20.0）、dotenv 大小/数量限制项（GitLab 17.1 引入）。
> - **报错原文的来源标注**：`This job is stuck because the project doesn't have any runners online assigned to it`、`This job could not start because it could not retrieve the needed artifacts` 为**官方文档原文**（见 S2、S7）；**YAML 解析类报错**（`mapping values are not allowed in this context` / `could not find expected ':'` / `bad indentation of a mapping entry`）为 **Psych YAML 解析器的典型报错（来自官方 issue 与社区反馈，非本机实测）**。
> - 链接实测方法见 [`../09-参考索引/README.md`](../09-参考索引/README.md)（含 3 次重试脚本，**000 是网络抖动不是失效**）。

### 待验证清单

| # | 待验证项 | 原因 | 怎么验 |
|---|---|---|---|
| 1 | **本机/公司 GitLab 实例的 `artifacts:expire_in` 实例级默认值** | 官方明确"用实例级设置"，**各实例不同，不编数字** | 在目标实例的 Admin Area → CI/CD 设置里查默认值；或建一个不写 `expire_in` 的 job 观察 |
| 2 | **`ci_needs_size_limit` / dotenv 大小与数量限制在目标实例的实际值** | 官方给的是**默认套餐值**，自建实例可由管理员改 | 查目标实例的 Plan limits API（`GET /application/plan_limits`） |
| 3 | **`sarif` 报告在目标 GitLab 版本上是否生效** | 该特性**带 feature flag 且跨 18.11 / 19.1 / 19.2 三个版本** | 查目标实例版本号；19.1 之前需确认 `sarif_ingestion` feature flag 状态 |
| 4 | **目标实例是否已禁用 registration token** | GitLab 17.0 起管理员可禁用；禁用后用旧 token 注册会报 **410 Gone** | 查实例设置；或直接看目标环境现有 runner 的 `config.toml` 用的是哪种 token |
| 5 | **`rules:changes` 在目标实例的 MR 场景下的基线行为** | `changes` 的 diff 基线依赖推送类型，官方文档也提示过"可能意外运行" | 在目标实例用真实 MR 验证一次；必要时显式写 `compare_to` |
| 6 | **目标实例 GitLab.com / Self-Managed 的 dotenv 继承变量数上限** | 官方写明 **Self-Managed 默认 20**，**GitLab.com 按套餐不同**，本篇不编套餐数值 | 查目标实例是自建还是 GitLab.com；自建看 Plan limits，GitLab.com 看 CI/CD 设置页 |

> **单源知识点标注**：
> - **`only`/`except` 的具体弃用措辞与移除排期**——S3 为官方英文来源，**缺官方中文版**（GitLab 无完整中文文档站），**⚠️ 单源待补**。
> - **中文社区对 `rules` 的实践写法**——S11/S12 为**社区来源**，只作术语与实践参考，**不作为行为依据**，**⚠️ 单源待补**（缺官方中文对照）。
> - **中文社区对 `rules` 的实践写法**——S10/S11 为**社区来源**，只作术语与实践参考，**不作为行为依据**，**⚠️ 单源待补**（缺官方中文对照）。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入：一次 git push（触发事件 + commit + ref）；输出：一组 job 的执行结果 + artifacts/reports + 流水线状态。中间经过：`workflow:rules` 判定是否建流水线 → 按 `stages`/`rules` 展开 job DAG → 队列 → runner 按 tag 领活 → 拉代码/恢复 cache → `before_script` → `script` → 上传制品 → `after_script` → 上报结果。**关键因果**：**`rules` 决定 job 存不存在，`needs` 决定谁等谁，`tags` 决定谁来跑**——三者分别对应"跑不跑 / 什么时候跑 / 在哪跑"。

2. **能故意改坏再倒推吗？** —— 五个具体实验：
   - **实验 A（rules 顺序）**：把 `rules` 里的 `- when: never` 挪到**第一条** → **所有 job 全都不执行**（流水线存在但全灰）→ 倒推出"**首个匹配即生效，顺序就是优先级**"。
   - **实验 B（needs 断链）**：给 `unit-test` 加 `needs: [build]`，然后把 `build` 的 `rules` 改成在 MR 上不匹配 → **整条流水线创建失败**（不是 job 跳过，是流水线报错）→ 倒推出"**`needs` 引用的 job 被 rules 排除会导致创建失败，要加 `optional: true`**"。
   - **实验 C（artifacts 断链）**：把 `unit-test` 的 `needs` 改成 `[build]` 但**去掉 `artifacts: true`**，同时把 `junit` 文件放在 build 的 artifacts 里 → **面板空白**；再改成 `needs: []` → **报 `could not retrieve the needed artifacts`** → 倒推出"**用了 `needs` 就只下载列出的 artifacts**"。
   - **实验 D（cache 静默失效）**：把 `cache` 里的 `paths` 删掉只留 `key` → **日志里没有 "Restoring cache"，也没有报错** → 倒推出"**只写 key 不写 paths = 完全不缓存，且不报错**"。
   - **实验 E（掩码失效）**：建一个 masked 变量，值里带 `[`（如 `My[value]`），在 job 里 `echo $VAR` → **日志里显示的是 `My\[value\]`，没有变成 `[MASKED]`** → 倒推出"**掩码对带转义字符的值会失效**"。

3. **能画上下游吗？** —— 画出这条链并标出每一步的失败表现：
   ```
   git push
     → [workflow:rules]  ── 不匹配 → 流水线不存在（无任何提示）
     → [job rules]       ── 不匹配 → job skipped（灰色）
     → [needs/stages]    ── 引用不存在 → 流水线创建失败
     → [tags ↔ runner]   ── 无匹配 → job pending（无限等待）
     → [image/services]  ── 缺工具 → command not found
     → [cache 命中]      ── key/paths 不对 → 静默不缓存（变慢但不报错）
     → [script 退出码]   ── 非 0 → job 失败 → 阻断后续 stage（除非 allow_failure）
     → [artifacts 上传]  ── 路径错 → 上报为空
     → [artifacts 过期]  ── 下游报 could not retrieve the needed artifacts
     → [dotenv 传递]     ── 没在 needs/dependencies 里 → 下游拿不到变量
   ```

---

**下一篇**：[`L2-Runner与缓存制品`](L2-Runner与缓存制品.md)（**已写** · Runner 三组件、executor 类型、缓存与制品机制、DinD vs Docker socket、CI 构建推送镜像流水线） · **上一篇**：[`../01-基础篇/L3-镜像仓库与供应链安全`](../01-基础篇/L3-镜像仓库与供应链安全.md)（**已写** · 镜像引用与 digest、扫描与签名、供应链四道防线） · **上一级**：[`../00-学习地图与前置依赖`](../00-学习地图与前置依赖.md) · **关联日课**：[`第1周/第04天.md`](../第1周/第04天.md)（GitLab CI）· [`../01-基础篇/L3-镜像仓库与供应链安全.md`](../01-基础篇/L3-镜像仓库与供应链安全.md)（镜像引用与 digest）
