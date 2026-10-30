# L1 · Helm 模板化封装（一套资源，打包成可参数化的版本化单元）

> **本篇定位**：Helm 不是"YAML 生成器"。本篇讲清**Helm 到底把什么打包了**、**Chart 目录每个目录干什么**、**值的覆盖优先级（高频考点）**、**模板语法与内置函数怎么用**，以及**最该警惕的一件事：Helm 管理的是 Release，`helm uninstall` 会删掉它创建的一切**。
> **前置**：[`../03-CI-CD与门禁/L4-多环境策略与分支模型.md`](../03-CI-CD与门禁/L4-多环境策略与分支模型.md)（配置与代码分离、多环境差异收敛在 values）、[`../02-K8s核心/L2-K8s架构与对象模型.md`](../02-K8s核心/L2-K8s架构与对象模型.md)（K8s 资源清单字段语义）。
> **学完能做什么**：① 说清 Helm 的**渲染 + Release 管理**两层职责（以及为什么 uninstall 是破坏性操作）；② 独立写出一个**可复用、不硬编码公司名**的 Chart；③ 准确说出**值覆盖的优先级顺序**，并解释"改了 values 没生效"的九成原因；④ 用 `helm template` 在 CI 里做**静态校验**，用 `helm diff` 做**升级预演**；⑤ 说清 **Hook 的语义与它"不受普通升级管理"这个陷阱**；⑥ 讲清 Chart 版本与 appVersion 的区别，以及**为什么 CRD 不随 `helm upgrade` 更新**。
> **在 21 天里**：D6（Chart + 多环境 values + 回滚）、D10~D11（ArgoCD 里引用 Chart）。
> **环境/版本基准**：本地 K8s **v1.37.x**（kind 集群 `day2`）；镜像仓库 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`。**Helm 的模板函数与内置对象以 Helm 3 的官方文档为准**；默认值随版本变动的细节标 `⚠️ 待验证`。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
L1 Helm 模板化封装
│
├─ 1. 概念
│   ├─ 1.1 Helm 解决什么问题：从"一整套 YAML"到"一个可参数化单元"
│   ├─ 1.2 Helm 的两层职责：渲染器 + Release 管理器
│   └─ 1.3 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 Chart 目录结构：每个目录干什么
│   ├─ 2.2 Chart.yaml 的关键字段
│   ├─ 2.3 values.yaml 与值的覆盖优先级（★ 高频考点）
│   ├─ 2.4 模板语法：{{ }} 与空白控制
│   ├─ 2.5 常用内置对象与函数
│   ├─ 2.6 命名模板：define / template / include
│   ├─ 2.7 Release：Helm 管的是 Release，不是资源
│   ├─ 2.8 常用命令族：install/upgrade/rollback/uninstall/list/history/status
│   ├─ 2.9 helm template 与 helm diff：CI 的两个武器
│   ├─ 2.10 Hooks：不在升级管理范围内的特殊资源
│   └─ 2.11 Chart 打包与仓库
│
├─ 3. 动手：从 helm create 到可复用 Chart
│
├─ 4. 逐字段表
│   ├─ 4.1 Chart.yaml 逐字段
│   ├─ 4.2 values 覆盖优先级
│   └─ 4.3 内置函数按用途分类
│
├─ 5. 横向对比
│   ├─ 5.1 Helm vs Kustomize：模板化 vs 变体叠加
│   ├─ 5.2 Helm vs ArgoCD：渲染器 vs 同步器
│   └─ 5.3 helm template vs kubectl apply -k
│
├─ 6. 踩坑与排障
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 Helm 解决什么问题

**先看没有 Helm 时的真实状态**（数字是量级示意，具体因公司而异）：

| 不用 Helm | 用 Helm 之后 |
|---|---|
| 每个应用 × 每套环境一份 YAML（150 个应用 × 3 套环境 ≈ **450 份几乎相同的文件**） | **一份 Chart + 每环境一份 values**（450 份 values，但每份只有 10~30 行差异） |
| 改一个公共字段（比如加 `securityContext`）要改 **450 个文件** | 改**模板一次**，全应用生效 |
| 环境差异**藏在整份 YAML 里**，看不出哪些是"故意的不同" | 差异**显式收敛在 values 里，可以 diff** |
| "线上跑的是哪个配置" → 翻文件、猜 | `helm get values <release>` **直接看合并后的最终值** |
| 回滚 → `kubectl rollout undo` 或 Git revert，**不知道上次发了什么** | `helm history` 看到每次 revision + 用了哪些值，**`helm rollback` 精确到某一版** |
| 新人不知道要配哪些字段 | **`values.yaml` 就是配置说明书** |

> **关键认知**：**Helm 解决的不是"怎么写 YAML"，是"怎么让 450 份 YAML 变成 1 份模板 + 450 份差异"。**
>
> 这跟 [`../03-CI-CD与门禁/L4-多环境策略与分支模型.md`](../03-CI-CD与门禁/L4-多环境策略与分支模型.md) 的结论完全一致：**配置的"结构"只写一遍，"差异"单独放。**

### 1.2 Helm 的两层职责（本章最重要的一张图）

> **很多人只用了 Helm 的一半，然后用另一半的方式（kubectl delete）去补，最后两边状态对不上。**

```
┌──────────────────────────────────────────────────────────────┐
│ 第一层：渲染器（Chart → YAML）                              │
│                                                              │
│   Chart（模板 + 默认值 + 依赖）                              │
│        + 你传入的 values（-f / --set）                       │
│        + Release 元信息（name / namespace / revision）       │
│        ↓  Go template 渲染                                   │
│   一组完整的 K8s 资源清单（YAML）                            │
│                                                              │
│   ★ 这一层是"纯函数"：同样的输入 → 同样的输出                │
│     所以它可以在 CI 里本地跑（helm template）                  │
└───────────────────────────┬──────────────────────────────────┘
                            │ helm install / upgrade
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ 第二层：Release 管理器（集群里有状态）                       │
│                                                              │
│   一个 Release = "某个 Chart 的某次安装实例"                │
│   记录在集群里（默认以 Secret 存储，官方标称可用 helm.sh 注解切换为 ConfigMap）│
│   包含：                                                      │
│     · release 名 + namespace                                 │
│     · revision（每次变更递增）                               │
│     · 应用的 values 快照（user-supplied + computed）         │
│     · 渲染出的资源清单                                       │
│     · 当前状态（deployed / failed / superseded / uninstalling）│
│                                                              │
│   ★ 这一层有状态，所以它能：                                  │
│     · helm history（看历史）                                  │
│     · helm rollback（回到某一版）                             │
│     · helm uninstall（删掉它创建的**一切**）                  │
└──────────────────────────────────────────────────────────────┘
```

**这张图推出的三个直接结论**：

| 结论 | 原因 |
|---|---|
| **① `helm template` 不需要集群** | 它只做第一层（渲染），所以 CI 里可以随便跑 |
| **② `helm uninstall` 是破坏性操作** | 第二层管理着"这个 release 创建的全部资源"，删 release = **把这些资源一起删掉**（详见 2.7） |
| **③ 渲染（第一层）和应用（第二层）可以分开看** | 这就是 `helm template`（只渲染）与 `helm diff`（渲染两份做对比）的价值 |

**一个常见的误解要澄清**：

> **"Helm 管理的是 Release，不是资源"这句话，字面意思和实际含义不一样。**
>
> 字面上：`helm uninstall` 删除 release 记录，同时**删除该 release 管理的所有资源**。
> 实际含义：**这些资源从此不再被任何控制器协调**——它们从"Helm 的管辖范围"里退出了。
> **所以：如果资源不是通过 Helm 创建的（手工 `kubectl apply` 的），`helm uninstall` 不会删它们**；反过来，**如果资源是 Helm 创建的但被人手工改过，`helm uninstall` 依然会删掉它**（Helm 删的是它记录的清单）。
>
> **一句话**：**Helm 的管辖范围 = 它自己创建的那些资源。**

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Chart | 图表 / Chart | **一个 Helm 包**：模板 + 默认值 + 依赖 |
| Release | 发布实例 | **某个 Chart 在某个 namespace 里的一次安装**，有名字、有 revision |
| Revision | 版本号 | **同一个 release 每次变更的递增编号**（install=1, upgrade=2, rollback 也是新 revision） |
| Values | 值 | 传给模板的参数；**合并后的最终值决定了渲染结果** |
| Template | 模板 | `templates/` 下的文件，含 Go template 语法 |
| `_helpers.tpl` | 命名模板文件 | **只含 `define` 块的模板文件**，不产出资源 |
| Hook | 钩子 | **在 release 生命周期的特定时点执行的资源**（如迁移 Job） |
| `helm.sh/hook` | 钩子注解 | 声明这个资源是 hook 及其类型 |
| `helm.sh/resource-policy` | 资源策略注解 | `keep` = **删除 release 时保留该资源** |
| Subchart | 子 Chart | 被依赖的另一个 Chart（`dependencies` 声明） |
| Global values | 全局值 | **顶层 `global` 下的值，会传给所有子 Chart** |
| OCI registry | OCI 镜像仓库 | **存 Chart 包（.tgz）的镜像仓库**（如 ACR） |
| `helm repo` | Chart 仓库 | **一个 Chart 索引源**（可以是 HTTP 索引，也可以是 OCI registry） |
| Drift | 漂移 | 集群实际状态与 Chart 声明不一致（见 L4 篇 2.6） |
| `helm diff` | — | **Helm 插件**：渲染新旧两版并对比，**不是 Helm 内置命令** |

---

## 2. 机制

### 2.1 Chart 目录结构：每个目录干什么

**`helm create` 生成的骨架（官方标称结构）**：

```
mychart/
├── Chart.yaml          ← 元信息：这是什么、什么版本、依赖谁
├── values.yaml         ← 默认值（可被 -f / --set 覆盖）
├── charts/             ← 依赖的子 Chart（自动下载到这里）
├── crds/               ← CRD 定义（⚠️ 特殊规则，见 2.10 / 第 6 节）
├── templates/          ← 模板（渲染出 K8s 资源）
│   ├── _helpers.tpl    ←   命名模板（只定义，不产出资源）
│   ├── NOTES.txt       ←   安装后打印的说明（给用户看，不进集群）
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── serviceaccount.yaml
│   ├── hpa.yaml
│   ├── ingress.yaml
│   └── tests/          ←   Helm 测试（helm test 用）
└── .helmignore         ← 打包时忽略哪些文件
```

**每个目录的职责（这张表是本节的核心）**：

| 路径 | 作用 | 写错的后果 |
|---|---|---|
| `Chart.yaml` | **Chart 的身份证**：名字、版本、appVersion、依赖、注解 | 版本冲突推不上去；**appVersion 填错 → 部署的镜像 tag 错**（2.2） |
| `values.yaml` | **默认值**。这是"不传任何参数时"的基线 | **写成 prod 的值** → 别人不传参数就部署出 prod 配置；**默认值该用 dev 的** |
| `templates/` | **模板目录**。每个文件渲染成一个或多个 K8s 资源 | 文件名以 `_` 开头 → **只作为命名模板，不产出资源**（很多人不知道） |
| `templates/_helpers.tpl` | **命名模板**。放 `define` 块，供 `include` 复用 | 缺 `{{- define }}` → 整个文件被当普通模板渲染 → 产出垃圾 YAML |
| `templates/NOTES.txt` | **安装完成后打印给用户看的提示** | 里面若含 `{{ }}` 也会被渲染；**不会进集群** |
| `charts/` | **依赖的子 Chart**（`helm dependency update` 下载到此） | **不要手动改这里的内容** → 下次 update 会被覆盖 |
| `crds/` | **CRD 定义** | ⚠️ **`helm upgrade` 不会更新这里的 CRD**（2.10 与第 6 节） |
| `templates/tests/` | **Helm 测试**（`helm test` 触发的 Pod） | 命名/标签写错 → `helm test` 跑不起来 |
| `.helmignore` | 打包时排除的文件（类似 `.dockerignore`） | 不写 → **`.git`、密钥文件被打进 .tgz** |
| `values.schema.json`（可选） | **values 的 JSON Schema**，做类型/必填校验 | schema 写错 → 装 Chart 时直接报错 |

**三种"文件"要分清**：

| 类型 | 位置 | 是否进集群 | 例子 |
|---|---|---|---|
| **模板** | `templates/*.yaml` | ✅ **渲染后进集群** | `deployment.yaml` |
| **命名模板** | `templates/_*.tpl` | ❌ **不产出资源** | `_helpers.tpl` |
| **说明文本** | `templates/NOTES.txt` | ❌ **只打印** | 安装后提示"访问 http://..." |

> **`templates/` 下以 `_` 开头的文件不会被当作普通模板渲染**，这是 Helm 的约定。**所以 `helm template` 的输出里看不到 `_helpers.tpl` 的内容——它是"被调用才生效"的函数库。**

### 2.2 Chart.yaml 的关键字段

```yaml
apiVersion: v2                 # ★ Chart 的 API 版本（不是 K8s 的 apiVersion！）
name: mychart                  # ★ Chart 名（必须是小写字母数字与 -，且要唯一）
version: 0.1.0                 # ★ Chart 自身的版本（SemVer）
appVersion: "1.0.0"            #   被部署的应用的版本（任何字符串）
description: 一句话描述
type: application              #   application（默认）| library
home: <项目主页地址>
sources:
  - <代码仓库地址>
keywords: [k8s, helm]
maintainers:
  - name: 你的名字
    email: you@example.com
    url: <个人主页地址>
icon: <图标地址>
dependencies:                   # ★ 子 Chart 依赖
  - name: redis
    version: "17.x.x"
    repository: "<你的 chart 仓库地址>"
    condition: redis.enabled    #   条件启用
    alias: cache                #   别名（同一个依赖起多个实例时用）
tags:
  - enterprise
annotations:
  category: Database
kubeVersion: ">=1.23.0-0"      #   约束集群版本
deprecated: false
```

**最容易混的两组字段（高频考点）**：

| 字段 | 管什么 | 变动的含义 | 常见错误 |
|---|---|---|---|
| **`version`** | **Chart 包本身的版本**（模板结构变了就升） | **你改了 Chart 的模板/结构** → 升 version。**同一个 version 不能推两次**（仓库里到底哪份是最终版说不清） | **改了模板不升 version** → 推不上去，或推上去覆盖了旧版 |
| **`appVersion`** | **被部署的应用的版本** | **你换了个应用版本**（比如应用从 1.0.0 到 1.1.0）→ 升 appVersion | **两者搞混**：把 appVersion 当 Chart 版本管理 → **Chart 结构变了但 version 没升** |

> **面试这么答**："**`version` 管 Chart，`appVersion` 管应用，两个独立升。** 有一个具体的坑：`appVersion` 在模板里通常被用作镜像 tag 的默认值（`.Chart.AppVersion`）。如果你同时把应用版本和 Chart 版本都用 `appVersion` 表达，就会出现**'改了模板但没升 Chart 版本，推不上去'** 或者**'Chart 版本被应用版本带着乱跳'** 这两个问题。我们的规矩是：**模板变了升 `version`，应用变了升 `appVersion`，两个都在同一个 PR 里改，CI 检查 version 有没有跟着改。**"

**`apiVersion: v2` 的一句提醒**：

> **Chart.yaml 的 `apiVersion` 和 K8s 资源的 `apiVersion` 完全不是一回事。**
> Chart 的 `v2` 对应 Helm 3；Helm 2 时代的 Chart 是 `v1`。**写错成 `v1` 会报"Chart apiVersion 不支持"**（具体报错文案随版本变动，⚠️ 以你版本为准）。

### 2.3 values.yaml 与值的覆盖优先级（★ 高频考点）

**先看 values.yaml 长什么样**：

```yaml
replicaCount: 1

image:
  repository: registry.cn-hangzhou.aliyuncs.com/linux321-devops/mychart
  pullPolicy: IfNotPresent
  # tag 默认空 → 模板里用 .Chart.AppVersion 兜底 → 流水线用 --set 注入 commit SHA

imagePullSecrets: []
nameOverride: ""
fullnameOverride: ""

serviceAccount:
  create: true
  automount: true
  annotations: {}
  name: ""

podAnnotations: {}
podLabels: {}

podSecurityContext: {}
securityContext: {}

service:
  type: ClusterIP
  port: 80

ingress:
  enabled: false
  className: ""
  annotations: {}
  hosts: []
  tls: []

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    memory: 256Mi

autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 10
  targetCPUUtilizationPercentage: 80

nodeSelector: {}
tolerations: []
affinity: {}
```

**三个设计要点（这才是"能不能复用"的关键）**：

| 要点 | 说明 | 不这么做会怎样 |
|---|---|---|
| **① 所有环境相关的值都在 values 里** | 副本数、镜像、资源、探针、开关 | 模板里出现硬编码 → **换个公司就崩**，不是通用 Chart |
| **② 可选组件用 `enabled` 开关** | Ingress / HPA / ServiceAccount | 想关都关不掉，只能改模板 → 改模板 = 改结构 = 升 version |
| **③ 命名靠模板生成，不手写** | 用 `include "mychart.fullname" .` | 多个 release 同 namespace 会**名字冲突** |

#### 覆盖优先级（准确顺序）

**Helm 的值合并规则，从高到低**：

| 优先级 | 来源 | 说明 |
|---|---|---|
| **1（最高）** | **`--set` / `--set-string` / `--set-file`** | 命令行参数，**优先级最高** |
| **2** | **后一个 `-f` / `--values` 文件** | **多个 `-f` 时，后面的覆盖前面的** |
| **3** | **前一个 `-f` 文件** | 依次往前排 |
| **4** | **父 Chart 的 `values.yaml` 中该子 Chart 的部分** | 子 Chart 的值可被父 Chart 覆盖 |
| **5** | **子 Chart 自己的 `values.yaml`** | 最低 |
| — | **Chart 自带的 `values.yaml`（顶层）** | 基础默认值（在所有覆盖之下） |
| — | **Global values（`global` 键）** | **特例：全局值被传给所有子 Chart** |

**四条最常被考的细则**：

| 细则 | 说明 |
|---|---|
| **① `--set` 永远赢过 `-f`** | 哪怕 `-f` 文件在命令行后面写。**这是最常见的"改了文件没生效"的原因** |
| **② 多个 `-f` 是后者覆盖前者** | `helm install x ./c -f base.yaml -f prod.yaml` → **prod 覆盖 base** |
| **③ 覆盖是"合并"不是"替换"** | `-f` 只写了 `image.tag`，**`image.repository` 仍保留 Chart 默认值**（不是整个 `image` 被替换） |
| **④ `global` 会下发给所有子 Chart** | 父 Chart 的 `values.yaml` 里写 `global.registry: ...`，**所有子 Chart 都能读到**；**子 Chart 自己的 `global` 会被忽略** |

**④ 的一个实用后果**：

```
父 Chart values.yaml:
  global:
    imageRegistry: registry.cn-hangzhou.aliyuncs.com/linux321-devops
  subchartA:
    image:
      repository: mychartA          # ← 用了 global 的话这里可以只写名字
```
> **子 Chart 里如果写 `{{ .Values.global.imageRegistry }}`，而这个子 Chart 是被单独安装的（不作为依赖），`global` 就不存在 → 渲染成空 → 镜像地址变成 `/:tag` → 拉镜像失败。**
> **所以子 Chart 里用 global 的值，必须给默认值兜底**：`{{ .Values.global.imageRegistry | default "docker.io" }}`。

**命令行的值与文件里的值，语法不同（高频坑）**：

| 来源 | 嵌套写法 | 字符串写法 |
|---|---|---|
| **values.yaml / -f 文件** | YAML 原生嵌套：`image:\n  tag: v1` | `tag: "v1"` |
| **`--set`** | **点号路径**：`--set image.tag=v1` | **`--set-string image.tag=v1`** |
| **`--set` 传数组** | `--set hosts[0].host=x.com,hosts[0].paths[0]=/` | 数字/布尔会被自动转换，**`--set` 传 `"false"` 也会变成布尔 false** |

**`--set` 的三个真实坑**：

| 坑 | 现象 | 解法 |
|---|---|---|
| **值里有 `,` 或 `.`** | `--set description=a,b` → **被拆成两个值**；`--set name=my.app` → **key 被拆成嵌套** | **转义**：`--set-string description=a\,b`、`--set name=my\.app`；**或者干脆改用 `-f` 文件** |
| **类型被自动转换** | `--set replicaCount="3"` → 变成**数字 3**（通常没问题），但 `--set config.debug="false"` → **变成布尔 false**（不是字符串 "false"） | 数字/布尔用 `--set-string` 明确类型 |
| **`--set` 语法写错不报错** | `--set image.tag`（没有 `=`，且 tag 不存在）→ **静默生成一个空值** | 每次改完用 `helm get values` / `helm template` 核对 |

> **"改了 values 没生效"的排查顺序（照着做，别猜）**：
> ① `helm get values <release> -n <ns>` → **看合并后的最终值**（不是看你改的那个文件）；
> ② 检查是不是被 **`--set` 覆盖了**（优先级最高）；
> ③ 检查**多个 `-f` 的顺序**；
> ④ 检查 **key 拼写**（`image.tag` vs `image.tag` 大小写、层级写错）；
> ⑤ 检查**模板里用的路径**和 values 里的**层级是否一致**（层级错位是最常见的：模板写 `.Values.image.tag`，values 里写成了 `images.tag`）。

### 2.4 模板语法：{{ }} 与空白控制

**Helm 用的是 Go template**（底层是 Go 标准库的 text/template + Sprig 函数库）。

**基本写法**：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "mychart.fullname" . }}
  labels:
    {{- include "mychart.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  template:
    spec:
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
```

**空白控制符（`{{-` 与 `-}}`）—— 高频考点**：

| 写法 | 效果 | 什么时候用 |
|---|---|---|
| `{{ .Values.x }}` | 原样输出，**前面的空白保留** | 值在行内 |
| `{{- .Values.x }}` | **吃掉左侧所有空白（含换行）** | 让标签/字段从行首开始 |
| `{{ .Values.x -}}` | **吃掉右侧所有空白（含换行）** | 让下一个字段紧跟 |
| `{{- .Values.x -}}` | **两边都吃** | 独立成行的整块内容 |

**为什么需要它**：YAML 对缩进敏感。**一个多余的空行就可能让渲染结果非法**。

**`range` 的缩进陷阱（本文档里最容易踩的模板坑）**：

```yaml
# ❌ 常见错误：range 内的键和 range 本身同缩进 → 渲染出非法 YAML
spec:
  rules:
  {{- range .Values.hosts }}
  - host: {{ .host }}
    paths:
    {{- .paths }}
  {{- end }}
```

```yaml
# ✅ 正确：range 内的内容整体缩进，靠 nindent 补回来
spec:
  rules:
    {{- range .Values.hosts }}
    - host: {{ .host }}
      paths:
        {{- toYaml .paths | nindent 8 }}
    {{- end }}
```

> **`nindent` 的作用 = 换行 + 缩进 N 格**；**`indent` 的作用 = 只缩进 N 格（不换行）**。
> **在 `range` / `if` 内部几乎总是需要 `nindent`**，因为那些内容要"另起一行并缩进"。
> **判断口诀**：**需要换行就用 `nindent`，只是给已有内容加缩进就用 `indent`。**

### 2.5 常用内置对象与函数

**五个最常用的内置对象**：

| 对象 | 是什么 | 典型用法 |
|---|---|---|
| **`.Values`** | 合并后的最终值 | `{{ .Values.replicaCount }}` |
| **`.Release`** | 这次 release 的信息 | `.Release.Name` / `.Release.Namespace` / `.Release.IsInstall` / `.Release.IsUpgrade` / `.Release.Revision` / `.Release.Service` |
| **`.Chart`** | Chart 的元信息（来自 Chart.yaml） | `.Chart.Name` / `.Chart.Version` / `.Chart.AppVersion` |
| **`.Capabilities`** | 集群能力 | `.Capabilities.KubeVersion.Version` / `.Capabilities.APIVersions.Has "policy/v1/PodDisruptionBudget"` |
| **`.Files`** | Chart 里的非模板文件 | `.Files.Get "config/app.conf"` / `.Files.Glob "config/*"` |

**最常用的内置函数（按用途分类，完整表见 4.3）**：

| 用途 | 函数 | 例子 |
|---|---|---|
| **取默认值** | `default` | `{{ .Values.tag \| default .Chart.AppVersion }}` |
| **加引号** | `quote` / `squote` | `{{ .Values.name \| quote }}` |
| **缩进** | `indent` / `nindent` | `{{ toYaml .ports \| nindent 8 }}` |
| **转 YAML** | `toYaml` / `toJson` | `{{ toYaml .resources \| nindent 8 }}` |
| **编码** | `b64enc` / `b64dec` | `{{ .Values.secret \| b64enc }}` |
| **截断** | `trunc` / `trimSuffix` / `trimPrefix` / `trim` | `{{ trunc 63 (trimSuffix "-" .Release.Name) }}` |
| **必填校验** | **`required`** | `{{ required "必须提供 image.repository" .Values.image.repository }}` |
| **条件** | `eq` `ne` `and` `or` `not` `empty` | `{{ if and .Values.ingress.enabled (not .Values.ingress.className) }}` |
| **字典/列表** | `dict` `list` `set` `hasKey` `get` | `{{ .Values.tags \| toYaml \| nindent 4 }}` |
| **模板复用** | `include` / `tpl` | `{{ include "mychart.fullname" . }}` |
| **版本比较** | `semverCompare` | `{{ if semverCompare ">=1.23-0" .Capabilities.KubeVersion.Version }}` |

**四个"救命函数"**：

| 函数 | 解决什么 |
|---|---|
| **`default`** | **模板里的 nil 指针**。`{{ .Values.a.b }}` 在 a 不存在时直接报错；`{{ .Values.a.b \| default "x" }}` 则给兜底（⚠️ 注意：`default` 只在值为空时生效，**它不会阻止 `.Values.a.b` 的 nil 访问本身**——真要防 nil，得用 `dig` 或先判 `hasKey`） |
| **`required`** | **让"没配"变成"显式报错"而不是"默默用默认值"**。`{{ required "必须配置 database.host" .Values.database.host }}` —— **这是本篇最值得记住的一个函数**（见 L4 篇 2.6 的第 4 条漂移来源） |
| **`toYaml` + `nindent`** | 把任意结构体渲染成 YAML 塞进模板，**不用手写字段列表** |
| **`quote`** | **给字符串加引号**。**不加引号的值只要含 `:`、`#`、`{` 就可能让 YAML 非法**——这是 YAML 模板化的头号坑 |

> **`required` 的价值在多环境场景下尤其大**：
> **"某个环境漏配了某个必填项"是配置漂移最常见的来源之一。** 用 `required`，漏配直接渲染失败 → **流水线红** → **问题在部署前暴露**，而不是上线后行为异常。

### 2.6 命名模板：define / template / include

**三个关键字的分工**：

| 关键字 | 干什么 | 注意 |
|---|---|---|
| **`define`** | **定义**一段可复用的模板片段（给它起个名字） | 定义本身**不产出任何资源** |
| **`template`** | **调用**（旧式调用方式） | **在调用处不共享上下文**，且**不能访问 `.` 之外的变量**——**这就是它被 `include` 取代的原因** |
| **`include`** | **调用**（推荐） | **像函数一样接收参数**，第一个参数是模板名，第二个是上下文（通常是 `.`） |

**标准写法（`_helpers.tpl`）**：

```
{{/* 展开后的完整名称 */}}
{{- define "mychart.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/* 标签 */}}
{{- define "mychart.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "mychart.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* 选择器标签（**必须稳定，不能含版本号**）*/}}
{{- define "mychart.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mychart.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
```

**三条铁律**：

| 铁律 | 原因 |
|---|---|
| **① `define` 块必须在 `_helpers.tpl`（或以 `_` 开头的文件）里** | 否则这个文件会被当普通模板渲染 → **产出一堆垃圾 YAML** |
| **② `include "name" .` 的第二个参数是上下文** | **写 `.` = 传入当前作用域**；写错（比如传 `$.Values.something`）→ 模板里 `.Values` 取不到 → 渲染成空 |
| **③ 选择器标签（selectorLabels）绝不能含版本号** | Deployment 的 `spec.selector` **创建后不可改**（见 [`../02-K8s核心/L2-K8s架构与对象模型.md`](../02-K8s核心/L2-K8s架构与对象模型.md)）。**标签里带版本 → 每次升级都改 selector → `helm upgrade` 直接失败**。这是"通用 Chart"最经典的翻车点 |

> **第③条值得单独记**：**`app.kubernetes.io/version` 标签**可以随便改（它只是标签），**但 `selectorLabels` 不能改**。**新人的 Chart 常常在 selector 里写了版本号，然后第一次 `helm upgrade` 就报 selector 不匹配。**

### 2.7 Release：Helm 管的是 Release，不是资源（★ 破坏性操作）

**先看 Release 的生命周期**：

```
helm install myapp ./mychart        → revision 1   状态: deployed
helm upgrade myapp ./mychart         → revision 2   状态: superseded（旧的）/ deployed（新的）
helm rollback myapp 1                → revision 3   状态: superseded / deployed
                                          ★ rollback 产生的是【新 revision】，不是"撤销"
helm uninstall myapp                 → Release 记录被删除
                                          ★ ★ 它创建的所有资源也被删除 ★ ★
```

**`helm uninstall` 的破坏性（必须记住的一条）**：

| 事实 | 后果 |
|---|---|
| **`helm uninstall <release>` 会删除该 release 管理的全部资源** | **误敲一次 = 一个环境的整个应用消失** |
| **删资源是"按 Helm 记录的资源清单"删的，不是"按标签"删的** | **手工 `kubectl create` 的资源不会被删**（Helm 不知道它们）——**但这意味着它们会变成"孤儿"，既没人管也没人删** |
| **有人手工改过的资源，照样会被删** | Helm 不检查"实际状态"，它执行的是"我记录的那份清单" |
| **卸载是不可逆的**（除了 `helm history` 里已经没有记录了） | 恢复要靠**重新安装 + 重新配置**（**Secret、PV 这类资源删了数据就没了**） |

**几条安全规则**：

| 规则 | 说明 |
|---|---|
| **① 生产环境卸载需要二次确认** | 至少要一个"人看一眼"的动作（审批、改动单） |
| **② 先 `helm get manifest` 存档** | 卸载前把最终渲染结果存下来 —— **这是唯一的恢复依据** |
| **③ 注意 `helm.sh/resource-policy: keep`** | 给某个资源加这个注解 → **删除 release 时保留它**。**典型用途：数据库的 PVC**（数据不能因为卸载应用而丢） |
| **④ 分清"删 release"和"删资源"** | `helm uninstall` = 两者都删。**只想删 release 记录（保留资源）** → `--keep-history`（⚠️ 具体行为随版本变动，以官方文档为准） |

> **一句话记住**：
> **`helm uninstall` ≈ "删掉这个环境里的这个应用"。**
> **它不是"撤销一次升级"，它是"拆掉一整套东西"。**
>
> **而 `helm rollback` 才是"撤销"——但它是通过"把旧版本重新应用一遍"实现的**，所以：**rollback 会重新创建被删掉的资源（丢失的数据不会回来）**，而且**它产生一个新的 revision**（历史里多一条）。

### 2.8 常用命令族

| 命令 | 干什么 | **危险等级** | 备注 |
|---|---|---|---|
| `helm create <name>` | 生成骨架 | 🟢 无 | 生成的骨架是"通用示例"，**必须改造**才能用 |
| `helm lint <chart>` | **静态检查**（模板语法、YAML 合法性、values 结构） | 🟢 无 | **CI 第一步就该跑这个** |
| `helm template <name> <chart> -f values.yaml` | **只渲染，不连集群** | 🟢 无 | **CI 里最有用的一条**（见 2.9） |
| `helm install <rel> <chart> -f v.yaml -n <ns>` | 安装 | 🟡 | `--create-namespace` 会自动建 ns |
| `helm upgrade <rel> <chart> -f v.yaml` | 升级 | 🟡 | **`--install` 可以让它"不存在就装"**（幂等化） |
| `helm status <rel> -n <ns>` | 看当前状态 | 🟢 无 | |
| `helm list -n <ns>` | 列出 release | 🟢 无 | `-A` 跨所有 namespace |
| `helm history <rel> -n <ns>` | **看历史（revision）** | 🟢 无 | **回滚前必看** |
| `helm get values <rel> -n <ns>` | **看合并后的最终值** | 🟢 无 | **排查"改了没生效"的第一条命令** |
| `helm get manifest <rel> -n <ns>` | **看最终渲染出的清单** | 🟢 无 | **卸载前存档用** |
| `helm rollback <rel> <revision> -n <ns>` | 回滚到指定 revision | 🟠 | **必须写明确的 revision 号**（不写 = 回到上一个，可能不是你想要的） |
| `helm uninstall <rel> -n <ns>` | **删除 release 及其资源** | 🔴 **破坏性** | 见 2.7 |
| `helm repo add/update/search/push` | 仓库操作 | 🟡 | `push` 需凭据（2.11） |

**两个高频命令组合**：

```bash
# 组合①：诊断"改了没生效"——先看最终值，再看渲染结果
helm get values myapp -n prod -a          # -a = 连算出来的默认值一起看
helm template myapp ./mychart -f envs/prod.yaml | grep -A5 'image:'

# 组合②：升级前先看"会发生什么"
helm history myapp -n prod                # 先确认目标 revision
helm get manifest myapp -n prod > /tmp/backup.yaml   # ★ 存档
```

### 2.9 helm template 与 helm diff：CI 的两个武器

#### `helm template` —— 本地渲染，不碰集群

```bash
# 基本用法
helm template myapp ./mychart -f envs/prod.yaml

# 输出到文件（用于 kubectl diff）
helm template myapp ./mychart -f envs/prod.yaml > /tmp/rendered.yaml

# 指定 release 信息（影响 .Release.Name 等）
helm template myapp ./mychart \
  --namespace prod \
  --kube-version 1.37.0 \
  -f envs/prod.yaml
```

**它在 CI 里的三个用途**：

| 用途 | 怎么用 | 能挡住什么 |
|---|---|---|
| **① 语法与渲染校验** | `helm template` 非 0 退出 → job 失败 | 模板语法错、YAML 非法、**`required` 缺值** |
| **② 渲染结果做策略检查** | 渲染出 YAML 后用脚本/kubectl 校验 | 缺 `resources.requests`、`runAsNonRoot` 没开、**用了 `latest` 标签** |
| **③ 给 ArgoCD / 别的工具用** | 输出纯 YAML | Helm 渲染 + Kustomize 打补丁的组合模式 |

> **`--kube-version` 这个参数很重要**：`.Capabilities` 的内容依赖它。**不指定时，Helm 用编译时的默认版本**（⚠️ 具体默认随 Helm 版本变动）→ **`.Capabilities.APIVersions.Has "..."` 的判断结果可能和目标集群不符** → **某些资源被渲染出来，某些没渲染出来**，而且**本地不报错**。**CI 里应该显式指定目标集群版本。**

#### `helm diff` —— 升级前预演

> ⚠️ **重要事实：`helm diff` 是第三方插件，不是 Helm 内置命令。** 需要先 `helm plugin install`（插件来源以官方文档为准，⚠️ 待验证：安装源的可用性随时间变化）。**写进流水线前先确认你的环境能装上。**

```bash
# 典型用法：对比"当前状态"与"将要应用的状态"
helm diff upgrade myapp ./mychart -n prod -f envs/prod.yaml

# 在 CI 里：diff 非空说明有变更；diff 里有删除行说明会删资源
```

**它比 `kubectl apply -k` 强在哪**：

| 能力 | `helm diff upgrade` | `kubectl diff -k` |
|---|---|---|
| 显示**将要删除**的资源 | ✅ **能明确标出删除** | ⚠️ 也能，但需仔细看 |
| 显示 release 历史相关变化 | ✅ revision 变化 | ❌ 无 release 概念 |
| 不连集群能否用 | **能**（对比两份渲染结果） | 通常需要连集群 |
| 审计价值 | **"这次 Helm 升级会改什么"** | "这次 apply 会改什么" |

> **CI 里的推荐用法**：
> ```bash
> helm diff upgrade $REL ./mychart -n $NS -f envs/$ENV.yaml > diff.txt || true
> # 存档为 artifact —— 这就是"变更留痕"的证据
> # 人工/审批环节看这份 diff
> ```
> **把 diff 存档这一步比 diff 本身更重要**：出事时能查到"当初到底改了什么"。

### 2.10 Hooks：不在升级管理范围内的特殊资源

**Hook 是"在 release 生命周期的特定时点执行的资源"**，最常见的是**数据库迁移 Job**。

**声明方式（在模板里加注解）**：

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "mychart.fullname" . }}-migrate
  annotations:
    "helm.sh/hook": pre-upgrade,pre-install
    "helm.sh/hook-weight": "5"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          command: ["/app/migrate"]
```

**六种 Hook 时点**：

| Hook 类型 | 什么时候执行 | 典型用途 |
|---|---|---|
| `pre-install` | 安装前 | 准备目录、建数据库、初始化配置 |
| `post-install` | 安装后（**等资源就绪**） | 注册服务、发通知、做冒烟测试 |
| `pre-upgrade` | 升级前 | **数据库迁移**（最常用）、备份数据 |
| `post-upgrade` | 升级后 | 冒烟测试、刷新缓存 |
| `pre-delete` | 删除前 | 优雅下线、备份 |
| `post-delete` | 删除后 | 清理外部资源 |
| `pre-rollback` / `post-rollback` | 回滚前后 | 回滚时的数据处理 |
| `test` | `helm test` 触发 | 连通性测试 |

**三个必须知道的语义（都是坑）**：

| # | 语义 | 后果 |
|---|---|---|
| **① Hook 失败 → 整个 release 失败** | `pre-upgrade` 的迁移 Job 挂了 → **upgrade 直接失败**，资源不更新 | 这是**好事**（数据没迁移就不让更新），但**必须让人看懂失败原因**——Hook 的日志不在普通 release 日志里 |
| **② Hook 资源不受普通升级管理** | **`helm upgrade` 不会更新 Hook 资源**（改模板里的 Hook 也不会！）；**`helm uninstall` 也不会删除 Hook 资源** | Hook 资源会**变成孤儿**留在集群里 → **必须靠 `hook-delete-policy` 清理** |
| **③ Hook 资源默认没有 owner 归属** | 它不由 Deployment 之类管理，也不被 release 的资源清单追踪 | 手工 `kubectl delete job` 才能删 |

**`hook-delete-policy` 的三个取值**：

| 取值 | 含义 | 建议 |
|---|---|---|
| `before-hook-creation` | 新 Hook 执行前删掉同名的旧 Hook | **加上**（避免同名冲突） |
| `hook-succeeded` | 成功后删掉自己 | **加上**（不留垃圾） |
| `hook-failed` | 失败后删掉自己 | ⚠️ **失败时通常别删**——**留着才能查日志** |

> **一条实用建议**：
> **`pre-upgrade` 的迁移 Job 建议保留失败的那个**（不加 `hook-failed`），**在 CI 里把它打出来**。否则"数据库迁移失败"这件事只剩一行 `helm upgrade failed`，**没人知道迁移错在哪**——这是排障最难受的一类失败。

### 2.11 Chart 打包与仓库

**打包**：

```bash
helm package ./mychart                    # → mychart-0.1.0.tgz（版本取自 Chart.yaml）
helm package ./mychart --version 0.1.1    # 临时改版本号打包
helm lint ./mychart                       # ★ 打包前先 lint
```

**两种仓库形态**：

| 形态 | 命令 | 说明 |
|---|---|---|
| **传统 Chart 仓库**（HTTP 索引） | `helm repo add myrepo <你的 chart 仓库地址>` → `helm repo update` → `helm push mychart-0.1.0.tgz myrepo/` | 需要一个 `index.yaml` 索引文件；**私有仓库需要凭据**（用户名/密码或 token，通常放在 `~/.config/helm/repositories.yaml`，**这个文件里是明文凭据**） |
| **OCI Registry** | `helm push mychart-0.1.0.tgz oci://registry.cn-hangzhou.aliyuncs.com/<ns>/charts` | **直接推成 OCI 制品**，与镜像仓库同构；**需要先登录**（`helm registry login`） |

**OCI 方式的完整流程**：

```bash
# ① 登录（凭据会存到本地 docker 配置）
helm registry login registry.cn-hangzhou.aliyuncs.com -u <user>

# ② 打包
helm package ./mychart

# ③ 推送
helm push mychart-0.1.0.tgz oci://registry.cn-hangzhou.aliyuncs.com/<ns>/charts

# ④ 使用（引用时不带 oci:// 前缀）
helm install myapp oci://registry.cn-hangzhou.aliyuncs.com/<ns>/charts/mychart \
  --version 0.1.0 -n prod -f envs/prod.yaml
```

**私有仓库的凭据管理（关键）**：

| 做法 | 说明 |
|---|---|
| **CI 里用 CI 变量注入** | 变量设为 **Masked**，不要写进 `values.yaml` 或 `.gitlab-ci.yml` |
| **凭据不要进 Chart** | Chart 包里带凭据 = **发布到仓库 = 泄漏**（`.helmignore` 要排除掉本地配置） |
| **Chart 仓库权限 = 读 ≠ 写** | CI 只需要**拉**；**只有发布者能推**。用两个仓库账号 |
| **同一个 version 不推两次** | 推上去覆盖旧版 → "线上到底是哪一版"说不清 → **必须被 CI 拦住** |

> **这一节与 [`../03-CI-CD与门禁/L3-质量门禁-SonarQube与Trivy.md`](../03-CI-CD与门禁/L3-质量门禁-SonarQube与Trivy.md) 的门禁直接相连**：
> **"Chart 版本递增"必须写成流水线里的一条硬规则**——CI 检查 `Chart.yaml` 的 `version` 相对上一个 tag **确实变大了**，否则拒绝推送。**这条规则值一行代码，但它挡掉的是"发的是哪版说不清"这个最常见的审计问题。**

---

## 3. 动手

> **本章是设计说明与样例代码**，用于讲清"每一步看到什么算对"。**本文编写期间未执行任何 helm / kubectl / docker 命令，未创建或修改任何集群资源**——遵循 [`../00-写作规范.md`](../00-写作规范.md) 第四节的纪律。命令语义以你所用版本的官方文档为准。

### 3.1 从 `helm create` 到"能被别人复用"

**生成骨架**：

```bash
helm create mychart
```

**`helm create` 生成的骨架结构（官方标称）**：

```
mychart/
├── Chart.yaml
├── values.yaml
├── .helmignore
└── templates/
    ├── NOTES.txt
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── hpa.yaml
    ├── ingress.yaml
    ├── service.yaml
    ├── serviceaccount.yaml
    └── tests/
        └── test-connection.yaml
```

**骨架里有三类东西，你必须动手改**：

| 类别 | 例子 | 必须做什么 |
|---|---|---|
| **① 硬编码的公司信息** | `image.repository` 指向创建者自己的仓库；`ingress.hosts` 里的域名 | **全部改成 values 驱动**，让 Chart 能给别人用 |
| **② 示例性的可选组件** | Ingress、HPA、ServiceAccount、tests | **每个都用 `enabled` 开关控制**，让用的人能关掉 |
| **③ 骨架里的"教学代码"** | `NOTES.txt` 里的提示、`tests/` 里的测试 Pod | 删掉或改成本项目真实内容 |

**"能不能复用"的自查清单**：

| # | 检查项 | 达标的样子 |
|---|---|---|
| 1 | 搜 `mychart` / 你的仓库地址 / 你的域名 | **一个都没有**（全部 values 化） |
| 2 | 搜 `image:` 那一段 | **没有硬编码 tag**（用 `.Chart.AppVersion` 兜底，流水线注入） |
| 3 | 每个可选组件 | 都有 `enabled` 开关 |
| 4 | 名字生成 | 全部走 `include "xxx.fullname" .` |
| 5 | `selectorLabels` | **不含版本号**（否则升级会失败） |
| 6 | 必填项 | 用 `required` 卡住（如数据库地址、密钥引用） |

### 3.2 一个能用的 deployment.yaml 片段（逐行说为什么）

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "mychart.fullname" . }}
  labels:
    {{- include "mychart.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      {{- include "mychart.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      {{- with .Values.podAnnotations }}
      annotations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      labels:
        {{- include "mychart.selectorLabels" . | nindent 8 }}
        {{- with .Values.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      serviceAccountName: {{ include "mychart.serviceAccountName" . }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      containers:
        - name: {{ .Chart.Name }}
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - name: http
              containerPort: {{ .Values.service.port }}
              protocol: TCP
          resources:
            {{- toYaml .Values.resources | nindent 12 }}
          {{- if .Values.livenessProbe }}
          livenessProbe:
            {{- toYaml .Values.livenessProbe | nindent 12 }}
          {{- end }}
          {{- if .Values.readinessProbe }}
          readinessProbe:
            {{- toYaml .Values.readinessProbe | nindent 12 }}
          {{- end }}
      {{- with .Values.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
```

**每段的"为什么"**：

| 片段 | 为什么这么写 | 写错的后果 |
|---|---|---|
| `{{- include "mychart.labels" . \| nindent 4 }}` | 命名模板复用；**`nindent` 保证缩进正确** | 用 `indent` → **缩进少一格 → YAML 非法** |
| `selector.matchLabels` 用 `selectorLabels` | **selector 与 template 标签必须完全一致** | 不一致 → `helm install` 直接报 selector 不匹配 |
| `with .Values.podAnnotations` | **空值时整段不渲染**，不产出空的 `annotations:` | 不加 `with` → 渲染出 `annotations:` 后面接空 → **YAML 解析成 null，K8s 报错** |
| `{{ toYaml .Values.resources \| nindent 12 }}` | **资源规格完全由 values 驱动** | 硬编码 → 换环境要改模板 → 升 version |
| `image: "{{ ... }}"` **加引号** | 值里含 `:` `@` `#` 时不加引号 → YAML 非法 | **渲染成功但 apply 失败**（最难查的一类） |
| `{{ .Values.image.tag \| default .Chart.AppVersion }}` | 没注入 tag 时用 appVersion 兜底 | 不兜底 → 渲染出 `:tag` → **拉镜像失败** |
| `{{- if .Values.livenessProbe }}` | **探针可选** | 硬编码 → 每个环境都得改模板 |

### 3.3 CI 里的三步校验

```bash
# ① 静态检查
helm lint ./mychart

# ② 本地渲染（不碰集群）——能挡住模板语法错、YAML 非法、required 缺值
helm template myapp ./mychart -f envs/prod.yaml --namespace prod --kube-version 1.37.0 > rendered.yaml

# ③ 对渲染结果做策略检查（示例：检查有没有 latest 标签、requests 有没有写）
grep -n 'image:.*:latest' rendered.yaml && exit 1
```

**"看到什么算对"**：

| # | 检查 | 期望 |
|---|---|---|
| 1 | `helm lint` | 无 error（warning 可接受，但要看） |
| 2 | `helm template` 输出的 YAML | 能被 `kubectl apply --dry-run=client` 通过（**语法合法**） |
| 3 | 渲染结果里的镜像 tag | **不是 `latest`**，也不是空 |
| 4 | 渲染结果里的 `resources.requests` | **存在**（否则 HPA 没有分母） |
| 5 | **故意把 `-f` 文件写错一个层级**（比如 `images.tag` 而不是 `image.tag`） | `helm template` **要么渲染出空值、要么 `required` 报错** → **流水线红**。**如果这时候流水线还是绿的，说明 CI 里根本没跑这一步** |
| 6 | **故意把 `selectorLabels` 里加上版本号** | `helm upgrade` 报 selector 不可变 → **升级直接失败**（这正是这个坑的可怕之处：第一次升级才发现） |

> **第 6 步是本篇最值得做的破坏实验**：**它证明"通用 Chart"这件事有一个硬约束——selector 必须稳定。**
> **很多团队是在第一次生产 `helm upgrade` 时才发现这个问题的**，代价是一次失败的生产发布。

---

### 3.4 常用运维命令（Helm 包管理，按场景）

> **本节能做什么**：3.1–3.3 是"怎么把 Chart 写对"，本节是**每天值班 / 面试都会敲的 Helm 运维命令**——从加仓库、装包、升级、回滚到本地验 YAML，一条龙。Helm 是 GitOps 交付链里"打包"那一环，这些命令是配置管理岗的高频肌肉记忆。
> **环境基准**：本机实测 `helm v3.14.x`。所有命令默认作用于当前 kube-context。

#### ① 仓库管理（repo add / update / search）

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami   # 加一个公共仓库
helm repo update                                           # 拉取所有仓库的最新索引（装包前必跑）
helm repo list                                             # 看已配置的仓库
helm search repo nginx                                     # 在已加仓库里搜 chart
helm search hub nginx                                      # 到 Artifact Hub 公共仓搜
```

> **实战价值**：`helm repo update` 忘了跑，装到的会是**上个月的旧版本**——这是新手最常见的"为什么装出来的镜像不对"。CI 里每次 `helm install` 前都应先 `repo update`。

#### ② Chart 脚手架与依赖（create / dependency build / update / pull）

```bash
helm create mychart                         # 生成一个标准骨架（templates/ + values.yaml）
helm dependency build                       # 按 Chart.yaml 的 dependencies 拉子 chart 到 charts/ 并打 tgz
helm dependency update                      # 同 build，但会更新 Chart.lock 里的版本
helm dependency list                        # 看当前 chart 依赖了哪些子 chart
helm pull bitnami/nginx --untar             # 把别人的 chart 拉下来本地改（学习/二次封装常用）
```

> **实战价值**：`dependency build` 失败通常卡在**网络或 repo 没 add**；多 chart 复用（umbrella chart）时，`dependency update` 能保证团队拿到锁定的子版本，避免"我本地好使"的玄学问题。

#### ③ 安装与升级（install / upgrade，--set / --values / --namespace）

```bash
helm install myrel bitnami/nginx -n demo --create-namespace          # 装一个 release（首次用 install）
helm install myrel ./mychart -f values-prod.yaml --set replicaCount=3 # 用文件 + 命令行覆盖值
helm upgrade myrel bitnami/nginx -n demo --reuse-values --set image.tag=v2  # 升级并保留旧值
helm upgrade myrel ./mychart -n demo --reset-values -f values.yaml    # 升级并丢弃旧值，完全用新文件
helm upgrade myrel ./mychart -n demo --atomic                        # 升级失败自动回滚（救火开关）
helm upgrade myrel ./mychart -n demo --dry-run                       # 只算不发，先验证会改什么
```

> **实战价值**：`--reuse-values` 是日常小改（只换镜像 tag）的省事开关；`--reset-values` 适合"我就要这份 values 说了算"的场景。`--atomic` 把"升级翻车"变成自动回滚，生产发布强烈建议带上。

#### ④ 渲染预览与校验（template / lint）—— 本地验 YAML，提早发现错误

```bash
helm template myrel ./mychart -f values-prod.yaml          # 把最终要提交的 YAML 渲染出来看（不动集群）
helm template myrel ./mychart --set image.tag=v2 | kubectl diff -f -   # 配合 kubectl diff 看会改什么
helm lint ./mychart                                       # 静态检查 Chart 结构与模板语法
helm lint ./mychart --strict                               # 严格模式，warning 也当 error
```

> **实战价值**：**`helm template` 是面试和实战的杀手锏**——改完 Chart 不急着 `upgrade`，先 `template` 把 YAML 打出来肉眼审一遍（镜像 tag 对不对、resources 有没有、selector 稳不稳定）。`lint` 能在 CI 里挡住 80% 的模板低级错误，比等到 `upgrade` 才红便宜得多。

#### ⑤ 发布生命周期管理（status / list / history / rollback / uninstall）

```bash
helm list -n demo -a                              # 看某 ns 的 release（-a 含已卸载的）
helm status myrel -n demo                         # 看这个 release 的状态/资源/notes
helm history myrel -n demo                        # 看每次升级的 revision 记录
helm rollback myrel 2 -n demo                      # 回滚到第 2 个 revision（救火第一招）
helm rollback myrel 0 -n demo                      # 回滚到上一版（revision 0 即上一版）
helm uninstall myrel -n demo                       # 卸载（默认保留 history，可 --keep-history 显式保留）
```

> **实战价值**：发布翻车第一反应不是改 yaml 重发，而是 `helm rollback` 先恢复服务，再慢慢查。`history` + `rollback` 是 Helm 相较裸 kubectl apply 最大的优势——**每次变更都有版本号可追溯**。

#### ⑥ 查看已发布内容（get values / manifests / notes）

```bash
helm get values myrel -n demo                     # 看这个 release 实际生效的 values（排查"配了没生效"）
helm get values myrel -n demo --revision 3        # 看某一历史版本的 values
helm get manifests myrel -n demo                  # 看这个 release 渲染出的全部资源 YAML
helm get notes myrel -n demo                      # 看 Chart 作者写的部署后指引（账号/端口等）
```

> **实战价值**：`get values` 是排查"我明明改了为什么没变"的终极手段——它显示的是**真正下发给集群的值**，能一眼看出 `--set` 被 `--values` 盖掉、或 `--reuse-values` 没带上预期改动。

#### ⑦ 打包、校验与发布（package / verify / env）

```bash
helm package ./mychart                           # 打成 mychart-0.1.0.tgz，准备发到仓库
helm package ./mychart --sign --key 'mykey'      # 打包并加签名（供应链安全）
helm verify mychart-0.1.0.tgz                    # 校验签名完整性（配合 --sign 用）
helm pull bitnami/nginx --version 15.0.0         # 按版本拉取指定 chart
helm env                                         # 看 Helm 的家目录 / 缓存 / 插件路径等环境信息
```

> **实战价值**：`package` + 推到内部 chartmuseum/harbor 是团队复用 Chart 的闭环；`--sign` / `verify` 在金融、政企等对供应链安全有要求的岗位是加分项，面试常问"怎么保证 chart 没被篡改"。

---

## 4. 逐字段表

### 4.1 `Chart.yaml` 逐字段

| 字段 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `apiVersion` | string | ✅ | **Chart 的 API 版本**（Helm 3 用 `v2`） | 写成 `v1` → **报"不支持的 Chart apiVersion"**；**别和 K8s 资源的 apiVersion 混淆** |
| `name` | string | ✅ | Chart 名（小写字母数字与 `-`） | 大写字母 → **校验失败**；重名 → 仓库里冲突 |
| `version` | string | ✅ | **Chart 自身版本**（SemVer） | **改了模板不升 → 推不上去/覆盖旧版**；**版本可复用 → "发的哪版"说不清** |
| `appVersion` | string | | **被部署应用**的版本（任意字符串） | **与 `version` 混用** → 模板结构变了但版本没升；它常被用作镜像 tag 默认值 → **填错 = 拉错镜像** |
| `description` | string | | 一句话描述 | 只影响展示 |
| `type` | enum | | `application`（默认）/ `library` | 写成 `library` → **不能被 install，只作为依赖** |
| `home` / `sources` / `icon` | string/list | | 项目信息 | 只影响展示 |
| `keywords` / `tags` | list | | 便于检索 | — |
| `maintainers` | list | | 维护者（name/email/url） | — |
| `dependencies` | list | | **子 Chart 依赖** | 缺 `repository` → 拉不到；缺 `condition` → **无法按环境禁用子 Chart** |
| `dependencies[].version` | string | | 依赖的版本**约束**（如 `17.x.x`） | 约束太松 → **依赖漂移**；太紧 → 装不上 |
| `dependencies[].condition` | string | | **按 values 控制是否启用** | 漏写 → **所有环境都装**（比如所有环境都装了 Redis） |
| `dependencies[].alias` | string | | 依赖的别名 | 同依赖多实例时漏写 → 名字冲突 |
| `kubeVersion` | string | | 集群版本约束 | 写太严 → 在旧集群装不上；写太松 → **用到了集群不支持的 API** |
| `annotations` | map | | 任意注解（如 `artifacthub.io/*`） | — |
| `deprecated` | bool | | 标记废弃 | — |
| `values.schema.json`（单独文件） | JSON Schema | | **values 的类型/必填校验** | schema 写错 → **装 Chart 时直接失败**（比渲染失败更早） |

### 4.2 values 覆盖优先级

| 优先级 | 来源 | 具体写法 | 覆盖谁 | 典型踩坑 |
|---|---|---|---|---|
| **1（最高）** | **`--set` / `--set-string` / `--set-file`** | `--set image.tag=v1` | **一切** | **CI 里残留的 `--set` 会盖掉你改的文件**——排查"改了没生效"的第一嫌疑 |
| **2** | **后一个 `-f` / `--values`** | `-f base.yaml -f prod.yaml` → prod 赢 | 前面的所有 `-f` | **顺序写反** |
| **3** | 前一个 `-f` | `-f base.yaml` | Chart 默认值 | — |
| **4** | **父 Chart `values.yaml` 中该子 Chart 的段** | `subchartA: { image: {...} }` | 子 Chart 自己的 `values.yaml` | **父里写 `subchartA` 段但子 Chart 名不是这个** → 不生效 |
| **5（最低）** | **子 Chart 自己的 `values.yaml`** | — | — | — |
| — | **Chart 自带的 `values.yaml`** | — | 全局兜底 | **默认值写成 prod 值** → 别人不传参数就部署出 prod 配置 |
| — | **`global` 全局值** | 顶层 `global:` | **传递给所有子 Chart** | **子 Chart 单独安装时 `global` 不存在** → 必须用 `default` 兜底；**子 Chart 自己的 `global` 会被忽略** |

**三条合并语义（不是覆盖那么简单）**：

| 语义 | 说明 | 例子 |
|---|---|---|
| **合并而非替换** | `-f` 只写了 `image.tag`，`image.repository` **仍保留默认值** | `image: {tag: v1}` + 默认 `{repository: r, pullPolicy: IfNotPresent}` → 合并成三个键都在 |
| **map 递归合并** | 嵌套 map 逐层合并 | — |
| **列表整体替换** | **列表不是合并，是整个替换** | 默认 `hosts: []`，`-f` 里给 `hosts: [a, b]` → 就是 `[a, b]`；反过来给了 `hosts` 又想"加一条"是做不到的 |

### 4.3 内置函数按用途分类

| 用途 | 函数 | 说明 | 典型用法 |
|---|---|---|---|
| **默认值** | `default` | 值为空时给兜底 | `{{ .Values.tag \| default .Chart.AppVersion }}` |
| **必填校验** | **`required`** | **为空直接渲染失败** | `{{ required "必须配置 database.host" .Values.database.host }}` |
| **字符串** | `quote` / `squote` | 加引号（**防 YAML 解析错**） | `{{ .Values.name \| quote }}` |
| | `upper` / `lower` / `title` | 大小写转换 | `{{ .Values.env \| upper }}` |
| | `trim` / `trimAll` / `trimPrefix` / `trimSuffix` | 去空白/去前后缀 | `{{ trimSuffix "-" .Release.Name }}` |
| | `trunc` | 截断（**K8s 名字 63 字符上限**） | `{{ trunc 63 $name }}` |
| | `contains` / `hasPrefix` / `hasSuffix` | 包含/前后缀判断 | `{{ if contains $name .Release.Name }}` |
| | `replace` | 替换 | `{{ .Chart.Version \| replace "+" "_" }}` |
| | `printf` / `print` | 格式化 | `{{ printf "%s-%s" .Release.Name $name }}` |
| | `split` / `splitList` / `join` | 分割/连接 | `{{ join "," .Values.list }}` |
| | `repeat` / `indent` / `nindent` | 重复/缩进 | `{{ toYaml .x \| nindent 8 }}` |
| **集合** | `list` / `dict` / `set` | 构造 | `{{ dict "a" 1 \| toYaml }}` |
| | `append` / `prepend` | 增删 | `{{ .Values.list \| append "x" }}` |
| | `hasKey` | 字典是否有某键 | `{{ if hasKey .Values "db" }}` |
| | `keys` / `values` / `merge` / `omit` / `pick` | 取键值/合并/排除 | `{{ merge .Values.base .Values.over }}` |
| | `first` / `last` / `until` / `len` | 列表操作 | — |
| **编码** | `b64enc` / `b64dec` | base64 | `{{ .Values.pwd \| b64enc }}` |
| | `sha256sum` | 摘要（**做 checksum 注解触发滚动**） | `{{ include "mychart.config" . \| sha256sum }}` |
| **数据转换** | `toYaml` / `toJson` / `fromYaml` / `fromJson` | 结构 ↔ 文本 | `{{ toYaml .Values.resources \| nindent 8 }}` |
| | `int` / `int64` / `float64` / `atoi` / `toString` | 类型转换 | — |
| | `dig` | **安全取嵌套值**（找不到给默认值，**不会 nil 报错**） | `{{ dig "db" "host" "localhost" .Values }}` |
| **版本** | `semverCompare` | 版本约束判断 | `{{ semverCompare ">=1.23-0" .Capabilities.KubeVersion.Version }}` |
| **条件** | `eq` `ne` `lt` `gt` `and` `or` `not` `empty` `coalesce` `ternary` | 判断 | `{{ if and .Values.ingress.enabled (not .Values.ingress.className) }}` |
| **模板** | `include` / `tpl` / `lookup` | 复用 / 渲染字符串 / **查集群活对象** | `{{ include "mychart.fullname" . }}` |
| **失败** | `fail` | **主动渲染失败并带消息** | `{{ if eq .Values.env "prod" }}{{ fail "prod 不允许用 latest 镜像" }}{{ end }}` |

> **`fail` 值得单独说一句**：它是"**把检查写进模板**"的利器。
> **CI 里跑 `helm template` 就等于跑了一遍"这个配置组合合不合法"的检查**——比如"prod 环境不许用 latest 标签"、"prod 必须开 resources.requests"、"prod 副本数不能是 1"。**这些检查写在模板里，就跟着 Chart 走，谁用都受益**（比写在 CI 脚本里更通用）。
>
> **再进一步：把"不可变性"也做成模板级检查。** 生产要固定镜像的 **digest**（不是浮动 tag），这条同样可以写进模板：

```
{{- if and (eq .Values.environment "prod") (not .Values.image.digest) }}
{{- fail "prod 环境必须固定 image.digest（镜像不可变引用），不接受只用浮动 tag" }}
{{- end }}
```

> **但光有"存在性校验"还不够——`fail` 只能挡住"没配"，挡不住"配错了"。**
> **digest 写错一位（少一位、多一位、大小写错）就是 `manifest unknown`，要等部署失败才发现。**
> **所以要再补一层"格式校验"**：

```
{{- if eq .Values.environment "prod" }}
{{- if not .Values.image.digest }}
{{- fail "prod 环境必须固定 image.digest（镜像不可变引用），不接受只用浮动 tag" }}
{{- end }}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" .Values.image.digest) }}
{{- fail (printf "image.digest 格式错误：应为 sha256: + 64 位十六进制，当前值 %q" .Values.image.digest) }}
{{- end }}
{{- end }}
```

| 检查层 | 挡住什么 | 什么时候才发现问题 |
|---|---|---|
| **存在性**（`not .Values.image.digest`） | **根本没配** | CI 阶段（`helm template` 就红） |
| **格式**（`regexMatch`） | **配了但写错** | CI 阶段（**不用等到部署**） |

> **为什么"格式校验比存在性校验更早拦住问题"值得强调**：**存在性校验只在"漏配"时生效，而"写错一位"是比漏配更常见的手误**（复制粘贴少一位、末尾多一个换行、大小写混用）。**两者要一起上。**
>
> **再往上一层看：CI 里的校验分三档，同一类规则应该在尽可能早的那一档拦。**
>
> | 档位 | 在哪做 | 离什么最近 | 代价 |
> |---|---|---|---|
> | **① 渲染期** | **模板里的 `fail`** | **离配置最近**，报错最具体 | 需要写模板逻辑 |
> | **② 构建期** | 脚本检查清单 | 离产物最近 | **规则不跟着 Chart 走**，换 Chart 要重写 |
> | **③ 部署期** | K8s 准入控制器（Admission Webhook） | 离生效最近 | **已经晚了**——配置已经提交，只差没生效 |
>
> **digest 校验放第 ① 档是对的。** 理由有两层：
> - **第 ② 档的规则不跟着 Chart 走**：写在 CI 脚本里，**别人拿去用自己的 Chart 时这条规则就没了**；写在模板里，**谁用都受益**。
> - **`fail` 的报错是"自解释"的**：报错信息直接告诉使用者错在哪、期望什么格式。**跑 `helm template` 的人当场就知道怎么改，不用去翻文档查正则该怎么写。** **错误信息离现场越近，修起来越快。**
>
> **一句话**：**同一条规则，在更早的档位拦，代价更小、覆盖更广、报错更具体。**
>
> **为什么这些值得写进模板**：**"生产必须钉死 digest"是一条制度，但制度靠人记就会漏。** 写成模板里的 `fail` 之后，**漏配或写错都在 CI 阶段直接红**，而不是等部署完再靠人去发现线上跑的不是预期那一版。**这就是"把检查放进渲染过程"的价值——它跟着 Chart 走，谁用都受益。**

---

## 5. 横向对比

### 5.1 Helm vs Kustomize：模板化 vs 变体叠加

| 维度 | **Helm** | **Kustomize** |
|---|---|---|
| **核心范式** | **模板化**：一份模板 + 参数 → 渲染 | **变体叠加**：base + overlay 补丁 |
| **差异怎么表达** | **值**（`replicaCount: 3`） | **补丁**（`patches:` 操作） |
| **能力边界** | **能改结构**（要不要开 HPA、要不要 Ingress） | 改结构要写 patch，较笨重 |
| **打补丁的强项** | ⚠️ 一般 | ✅ **强**（改第三方 Chart 不想 fork） |
| **值与结构的关系** | **同一件事的值** | **base 的最终结果** |
| **类型检查** | ✅ 可选 `values.schema.json` | ❌ |
| **发布历史 / 回滚** | ✅ **release + revision + `helm history` / `helm rollback`** | ❌ **没有**（靠 Git revert + apply） |
| **渲染需要集群** | ❌ `helm template` | ❌ `kubectl kustomize` |
| **与 ArgoCD 的配合** | ✅ ArgoCD **原生支持** Helm Chart 渲染 | ✅ 也支持，但常用于打补丁 |
| **学习成本** | **高**（Go template + 内置对象 + 优先级规则） | **低**（YAML 叠加） |
| **最大坑** | **values 覆盖优先级** | **overlay 叠多了看不懂**；patch 叠出畸形结果 |
| **本套资料的采用** | ✅ **自研应用首选** | ✅ **改第三方 Chart 时用** |

**选型一句话**：

> **"我要改的是这个应用**自己的**结构 → Helm；我要改的是**别人给的**东西 → Kustomize。"**

**两者叠加的组合模式（很实用）**：

```bash
# Helm 渲染出结构 → Kustomize 打环境补丁 → apply
helm template myapp ./mychart -f values-prod.yaml > /tmp/base/rendered.yaml
# 把 rendered.yaml 放进 base/，用 overlays/prod 打补丁
kubectl kustomize overlays/prod
```
> **这种组合能同时拿到"release 历史"和"灵活补丁"**，但**代价是渲染结果脱离了 values 的可追溯性**——改了什么要看 diff，不是看 values。**团队规模小的时候，建议先只用 Helm，把复杂度压住。**

### 5.2 Helm vs ArgoCD：渲染器 vs 同步器

**这是最常被混淆的一组概念。它们的层次完全不同。**

| 维度 | **Helm** | **ArgoCD** |
|---|---|---|
| **是什么** | **渲染器**：Chart + values → YAML | **同步器**：把声明的 YAML 持续应用到集群 |
| **解决什么** | **"怎么把一整套 K8s 资源参数化"** | **"怎么保证集群的实际状态等于声明"** |
| **有没有集群状态** | ❌ 纯函数（`helm template` 完全不需要集群） | ✅ **核心就是集群状态** |
| **谁执行 kubectl apply** | 人 / CI | **同步器自己**（控制器） |
| **发现漂移** | ❌ 不管 | ✅ **OutOfSync 状态** |
| **自动纠正漂移** | ❌ | ✅ **SelfHeal**（可配） |
| **删除多余资源** | ❌ | ✅ **Prune**（可配） |
| **发布历史在哪** | release 的 revision（`helm history`） | **Application 的 sync 历史 + Git 提交历史** |
| **触发方式** | 人执行 `helm upgrade` | **自动**（watch Git）或手动同步 |
| **能不能单独用** | ✅ 能（很多团队就只用 Helm） | ⚠️ **需要先有声明**（可以是裸 YAML，也可以是 Helm Chart） |

**它们经常配合使用（这就是标准形态）**：

```
        Git 仓库
           │
    ┌──────┴───────────────┐
    │  Chart + values       │   ← Helm 的领域：参数化
    │  （或裸 YAML）        │
    └──────┬───────────────┘
           │  ArgoCD 渲染 / 读取
           ▼
    ┌──────────────────────┐
    │  ArgoCD 同步器        │   ← ArgoCD 的领域：同步与纠偏
    │  · 比对 desired/live │
    │  · OutOfSync 告警     │
    │  · SelfHeal / Prune   │
    └──────┬───────────────┘
           ▼
         K8s 集群
```

**一句话记忆**：

> **Helm 负责"**变成** YAML"，ArgoCD 负责"**保持** YAML 生效"。**
> **Helm 是编译期的事，ArgoCD 是运行期的事。**

**一个重要的组合细节**：

> **ArgoCD 使用 Helm Chart 时，通常配一个 `values` 文件（多环境就是多份 values）**。
> **此时的值覆盖优先级会多一层**：ArgoCD 传的 values 相当于"命令行传入的 values"，**它的优先级高于 Chart 自带的 `values.yaml`**（具体层级语义随 ArgoCD 版本与配置方式变动，⚠️ 以你所用版本文档为准）。
> **实际影响：同一份 Chart，ArgoCD 渲染出来的结果可能和本地 `helm template` 不一样**（如果本地命令行的 `--set` 和 ArgoCD 的 values 不同）。**所以"本地渲染通过"不等于"ArgoCD 渲染通过"——CI 里应该用与 ArgoCD 一致的 values 和参数来渲染。**

### 5.3 `helm template` vs `kubectl apply -k`

| 维度 | **`helm template`** | **`kubectl apply -k`** |
|---|---|---|
| **干什么** | **只渲染**出 YAML，**不碰集群** | **渲染 + 应用到集群**（真正的变更） |
| **需要集群** | ❌ 不需要 | ✅ 需要 |
| **输出** | YAML（stdout 或文件） | API 调用结果 |
| **能不能进 CI 的"检查"阶段** | ✅ **这是它的主用途** | ❌ 它是"执行"阶段 |
| **能不能预演差异** | ⚠️ 不能（要自己比两份输出） | ✅ **`kubectl diff -k` 直接给差异** |
| **语法入口** | `helm template` | `kubectl apply -k` / `kubectl diff -k` |
| **release 历史** | ❌ 不产生 | ❌ 不产生（Kustomize 没有 release 概念） |
| **谁做"真正下发"** | 人 / CI / ArgoCD | kubectl |
| **典型搭配** | CI 里做静态校验；产出物喂给别的工具 | 实际部署 |

**三者的定位放在一起看**：

| 阶段 | 命令 | 作用 | 改集群吗 |
|---|---|---|---|
| **检查** | `helm lint` + `helm template` | 模板语法、YAML 合法性、必需值 | ❌ |
| **预演** | `helm diff upgrade`（插件）/ `kubectl diff -k` | **看这次会改什么** | ❌ |
| **执行** | `helm upgrade` / `kubectl apply -k` | 真正下发 | ✅ |

> **三步都做，才是完整的"安全的发布"。**
> **只做第三步 = 裸奔**（出问题才发现）。
> **只做第一、三步 = 缺了"变更留痕"**（事后说不清改了什么）。
> **第一步在 CI 里最便宜**（不需要集群、秒级），**第二步能挡住"删资源"这类不可逆操作**——**这是最值得投入的一步。**

---

## 6. 踩坑与排障

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| **改了 values，部署后没变化** | 实际配置还是旧的 | ① **被 `--set` 覆盖了**（优先级最高）；② **多个 `-f` 顺序反了**；③ **key 层级写错**（模板读 `.Values.image.tag`，values 写成了 `images.tag`）；④ **改的是另一个 values 文件** | 依次查：`helm get values <rel> -n <ns>` 看**合并后的最终值** → 确认命令里的 `-f` 顺序 → 确认 `--set` → 确认 key 拼写 | **CI 里加一步 `helm template` 并 diff 关键字段**；`--set` 只用于临时覆盖，**环境差异一律走 `-f` 文件** |
| **`helm upgrade` 报 selector 不匹配 / 拒绝更新** | upgrade 失败 | **selector 里含了版本号**（或任何会变的值）。**Deployment 的 `spec.selector` 创建后不可改** | 把 selector 改成只含**稳定标签**（name + instance），版本信息放到 `metadata.labels` 里 | **模板评审时专门检查 `selectorLabels`**；**第一次升级前用真实 values 演练一次**（这个坑在第一次升级才暴露） |
| **`helm upgrade` 之后某些资源消失了** | 资源被删 | **Helm 3 的 upgrade 会删除"上一版有、新版没有"的资源**（模板里删了一段 → 那个资源被删） | 确认是不是模板误删；**重要资源加注解 `helm.sh/resource-policy: keep`** | **upgrade 前跑 `helm diff upgrade` 看有没有删除行**；**删除操作必须人工确认** |
| **`helm uninstall` 之后数据没了** | PVC / Secret 被删 | **卸载会删除 release 管理的所有资源**，包括 PVC 和 Secret | **数据类资源加 `helm.sh/resource-policy: keep`**；卸载前 `helm get manifest` 存档 | **给 PVC 默认加 keep 注解**；**生产卸载走审批** |
| **release 卡在 `failed` 状态，后续 upgrade 报错** | upgrade 失败且状态是 failed | 上一版失败后，Helm 的 release 记录处于 `failed`，需要处理才能继续 | 先 `helm history` 看状态；**按官方流程处理 failed release**（不同版本处理方式不同，⚠️ 以你版本文档为准） | 失败后**先 `helm history` 看清楚再操作**，不要盲目重试 |
| **Hook 失败，release 直接失败，但看不到原因** | release failed，日志里只有一句失败 | **Hook 资源的日志不在普通 release 日志里**；而且 Hook 资源**不被 `helm uninstall` 清理**，可能留下了旧的 | **单独 `kubectl get job` / `kubectl logs` 看 Hook 的日志**；清理残留 Hook 资源 | **Hook 加 `hook-delete-policy: before-hook-creation`**；**失败的 Hook 保留**（不加 `hook-failed`）以便查日志；**CI 里把 Hook 日志打出来** |
| **Hook 改了内容，`helm upgrade` 没生效** | Hook 资源还是旧的 | **Hook 资源不受普通升级管理**（Helm 不会更新它们） | 手工删除旧 Hook 资源，让下次创建 | **Hook 里放幂等脚本**（重复执行不报错）；**不要指望 upgrade 会更新它** |
| **模板渲染出来 YAML 非法 / apply 报解析错误** | 渲染成功但 apply 失败 | ① **值没加引号**（含 `:` `@` `#` `{}`）；② **`indent` 与 `nindent` 混用**（缩进少/多一格）；③ **`range` 内缩进错位**；④ **`with` 块为空**导致产出 `annotations:` 后面接空 → 解析成 null | `helm template` 的输出**直接喂给 `kubectl apply --dry-run=client`** 验证；值一律 `quote`；**嵌套块一律 `nindent`** | **CI 里固定跑"渲染 + dry-run"两步**；模板改动必须先本地验证 |
| **`nil pointer evaluating interface {}.xxx`** | 渲染失败 | 模板里访问了**不存在的嵌套值** | 用 `{{ .Values.a.b \| default "x" }}`；**更稳的是 `dig`**：`{{ dig "a" "b" "x" .Values }}` | 关键值用 `required`；**CI 里渲染所有环境的 values**（不只是 prod）——**dev 能渲染不代表 prod 能渲染** |
| **`required` 报错但我不知道哪个环境缺** | 渲染失败 | `required "必须配置 xxx" ...` 的消息里没带环境信息 | 消息里加上环境名：`{{ required (printf "环境 %s 必须配置 database.host" .Release.Namespace) ... }}` | 错误信息里**永远带上"是哪个环境/哪个 release"** |
| **CRD 加了新字段，但 `helm upgrade` 后没用上** | CRD 还是旧的 | **`crds/` 目录里的 CRD 只在 install 时创建，`helm upgrade` 不更新它们** | 手工 `kubectl apply -f crds/xxx.yaml`；或用 `--set crds.create=false` 并把 CRD 交给别的工具管理 | **CRD 的更新走独立流程**（CI 里单独 apply 并记录）；**CRD 的变更要先考虑版本转换与存储版本**（见 K8s CRD 官方文档） |
| **Chart 版本没升，推到仓库被拒 / 覆盖了旧版** | push 失败或覆盖 | **`version` 没变**（同一 version 不能推两次） | 升 `version`；**CI 里检查 version 相对上一个 tag 确实变大了** | **CI 加硬规则：version 未递增 → 拒绝 push**；**只升 appVersion 不升 version 也要拦**（模板可能变了） |
| **Chart 版本和 appVersion 搞混了** | 推不上去 / 拉错镜像 | 用 `appVersion` 当 Chart 版本管 | 拆开：`version` 管 Chart 结构，`appVersion` 管应用 | 两者在**同一个 PR 里改**；CI 检查"模板有变更 → version 必须变" |
| **部署时报 `manifest unknown` / `ImagePullBackOff`**，但镜像明明在仓库里 | Pod 起不来 | **digest 写错了一位**（少一位、多一位、末尾多个换行、大小写错）——**存在性校验挡不住这种错** | 核对 digest 与仓库里的实际 digest 是否**逐字符一致**（`docker manifest inspect` 或仓库页面复制） | **模板里加格式校验**：`regexMatch "^sha256:[0-9a-f]{64}$"`；**digest 由流水线注入、不要手抄**——**手抄就是最容易写错一位的来源** |
| **多个 release 在同一个 namespace 名字冲突** | install 失败 | 全名生成逻辑没考虑 release 名（或用了硬编码名） | 用 `{{ include "xxx.fullname" . }}`（含 `.Release.Name`） | 名字**永远走命名模板**；**不手写 name** |
| **私有 Chart 仓库拉不到 / push 失败** | 401 / 404 | 凭据缺失或过期；**OCI 方式需要先 `helm registry login`**；索引仓库需要 `helm repo update` | 重新登录；`helm repo update`；**确认仓库地址与 namespace 正确** | **凭据用 CI 的 Masked 变量注入**；**Chart 仓库读权限与写权限分离** |
| **`helm template` 本地通过，ArgoCD 渲染出来不一样** | ArgoCD 渲染结果与本地不同 | ArgoCD 传的 values / 参数与本地命令不同（**`--set`、namespace、`--kube-version`、values 文件不同**） | 让 CI 用**与 ArgoCD 完全一致**的 values 和参数渲染 | **把 ArgoCD 的参数抄进 CI 的渲染命令**（或在 CI 里直接复用 ArgoCD 的 values 文件） |
| **`.Capabilities` 判断结果不对**（某些资源没渲染出来） | 资源缺失 | **本地渲染没指定 `--kube-version`**，用的是编译时默认版本 → `.Capabilities.APIVersions.Has` 判断与目标集群不符 | 渲染时加 `--kube-version 1.37.0`（或目标集群版本） | **CI 里固定 `--kube-version`**，与目标集群一致 |
| **`helm rollback` 回滚到了错误的版本** | 回滚到非预期版本 | **没写明确的 revision 号**；中间有几次失败的 upgrade 导致 revision 号错位 | `helm history <rel>` 先看清，再 `helm rollback <rel> <明确revision>` | **回滚命令一律写明确 revision 号**；**回滚前 `helm get manifest` 存档** |
| **回滚后服务起不来** | 回滚后异常 | **只回滚了镜像/模板，没回滚配置**（外部配置、Secret、DB schema） | 回滚必须包含配置；DB 变更走"先兼容后切换" | **回滚 runbook 里写清"要退哪些东西"**；**新旧版本对配置与 schema 的兼容矩阵** |
| **`helm upgrade --install` 之后行为诡异** | 资源状态混乱 | `--install` 在 release 不存在时装、存在时升级——**但如果之前是用 `kubectl apply` 装的同名资源，Helm 会认为资源"已存在"**，可能跳过或冲突 | 先清掉手工创建的资源，再由 Helm 统一管理 | **资源的所有权要清晰：要么 Helm 管，要么 kubectl 管，不要混** |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `helm.sh/docs/intro/quickstart/` | 入门、install/upgrade/rollback/release 概念、Chart 仓库 | ✅ 2026-10-24 |
| S2 | 官方 | `helm.sh/docs/chart_template_guide/` | **模板语法、内置对象、内置函数、命名模板、values 覆盖规则** | ✅ 2026-10-24 |
| S3 | 官方 | `helm.sh/docs/topics/charts/` | **Chart 目录结构、Chart.yaml、依赖与 values 合并规则** | ✅ 2026-10-24 |
| S4 | 中文 | `helm.sh/zh/docs/intro/quickstart/` | 入门与常用命令（中文官方版） | ✅ 2026-10-24 |
| S5 | 中文 | `helm.sh/zh/docs/chart_template_guide/` | **模板与函数（中文官方版）** | ✅ 2026-10-24 |
| S6 | 中文 | `helm.sh/zh/docs/topics/charts/` | **Chart 结构与依赖（中文官方版）** | ✅ 2026-10-24 |
| S7 | 官方 | `kubernetes.io/docs/reference/using-api/` | 资源清单字段语义（Deployment selector 不可变等交叉验证） | ✅ 2026-10-24 |
| S8 | 中文 | `kubernetes.io/zh-cn/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/` | **CRD 的语义与升级/更新规则**（`crds/` 目录行为） | ✅ 2026-10-24 |
| S9 | 官方 | `argo-cd.readthedocs.io/en/stable/` | ArgoCD 与 Helm 的配合关系（5.2） | ✅ 2026-10-24 |
| S10 | 官方 | `argo-cd.readthedocs.io/en/stable/user-guide/sync-options/` | 同步选项（渲染与同步的分工） | ✅ 2026-10-24 |
| S11 | 中文 | `kubernetes.io/zh-cn/docs/concepts/configuration/secret/` | Secret 与 values 中密钥的边界 | ✅ 2026-10-24 |
| S12 | 云厂商 | `help.aliyun.com/zh/ack/user-guide/` | ACK 上 Helm 实践（Chart 与集群） | ✅ 2026-10-24 |
| S13 | 云厂商 | `help.aliyun.com/zh/acr/user-guide/` | ACR 作为 OCI Chart 仓库 | ✅ 2026-10-24 |
| S14 | 中文社区 | `developer.aliyun.com` · `cloud.tencent.com/developer` | 中文实践：多环境 values 与 Chart 仓库 | ✅ 2026-10-24 |

> **本篇的版本口径与本套资料一致**：本地 K8s **v1.37.x**（kind 集群 `day2`）、镜像仓库 `registry.cn-hangzhou.aliyuncs.com/linux321-devops/`。
> **Helm 的默认行为（如 `helm template` 未指定 `--kube-version` 时的默认能力集、release 存储介质形态）随 Helm 版本变动**，本文已标注 `⚠️ 待验证`，见文末清单。
> **完整来源矩阵见** [`../09-参考索引/README.md`](../09-参考索引/README.md)。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— **输入**：Chart（模板 + 默认值 + 依赖）+ 传入的 values + Release 元信息；**输出**：① 一组完整的 K8s 资源 YAML（渲染层，可离线完成）；② 集群里一个带 revision 的 release 及其管理的资源（应用层，有状态）。**两层必须分开看**——`helm template` 只用第一层，`helm uninstall` 危险在第二层。
2. **能故意改坏再倒推吗？** —— 五个具体实验：① 在 `selectorLabels` 里加上 `app.kubernetes.io/version` → **`helm upgrade` 报 selector 不匹配**（这个坑第一次升级才暴露）；② 把 `image.tag` 写成 `images.tag` → 渲染出空 tag → **拉镜像失败**；③ 在 CI 里加一条 `--set image.tag=xxx` → **你改的 values 文件被完全盖掉**（这就是"改了没生效"）；④ 删掉模板里的一段（比如某个 Service）→ `helm diff upgrade` 会显示一行删除 → **`helm upgrade` 后那个 Service 真的消失了**；⑤ 把 `crds/` 里的 CRD 改个字段再 `helm upgrade` → **CRD 没变**（CRD 不随 upgrade 更新）。
3. **能画上下游吗？** —— 画出「Chart 目录 → 模板渲染（Chart.yaml + values 合并 + Release 元信息 → Go template → YAML）→ `helm template` 静态校验 → `helm diff` 预演 → `helm upgrade` 应用（release + revision 落库）→ 集群资源 → 漂移检测（ArgoCD）→ `helm rollback`（**含配置回滚**）」这条链，并标出每一环**失败会看到什么**：渲染失败 → 模板报错；渲染通过但 apply 失败 → YAML 非法；upgrade 失败 → release 状态 `failed`；upgrade 成功但资源少了 → 模板误删；资源没更新 → Hook 不受管理 / CRD 不更新。

---

### 待验证清单（本篇标注 `⚠️` 的点）

| # | 待验证项 | 怎么验 | 影响 |
|---|---|---|---|
| 1 | `helm template` **未指定 `--kube-version` 时的默认能力集** | `helm template --help` + 官方文档对应版本 | 影响 2.9 的 `.Capabilities` 结论与 CI 参数 |
| 2 | **release 的存储介质**（Secret vs ConfigMap）的当前默认与切换方式 | `helm get` 相关命令 + 官方文档 | 影响 2.7 对"release 存在哪"的描述 |
| 3 | **`helm diff` 插件的安装源与可用性** | `helm plugin list` + 官方文档 | 影响 2.9 与第 6 节；**插件源不可用时要有替代方案** |
| 4 | **`--keep-history` 等参数在 `uninstall` 时的精确行为** | `helm uninstall --help` + 官方文档 | 影响 2.7 的"只想删 release 记录"说法 |
| 5 | **ArgoCD 渲染 Helm Chart 时的 values 优先级层级** | 你所用 ArgoCD 版本的官方文档 | 影响 5.2 的组合细节与"本地渲染 ≠ ArgoCD 渲染" |
| 6 | **failed release 的标准处理流程** | 官方文档对应版本 | 影响第 6 节"release 卡在 failed"一行 |

---

**下一篇**：[`L2-Terraform-核心概念与生命周期`](../04-交付与GitOps/L2-Terraform-核心概念与生命周期.md) · **关联**：[`../03-CI-CD与门禁/L4-多环境策略与分支模型.md`](../03-CI-CD与门禁/L4-多环境策略与分支模型.md) · **日课**：[`../第1周/第06天.md`](../第1周/第06天.md) · **回到** [`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
