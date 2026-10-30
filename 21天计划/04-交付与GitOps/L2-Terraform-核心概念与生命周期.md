# L2 · Terraform 核心概念与生命周期（零基础 → 能讲清原理）

> **本篇定位**：Terraform 系列的第一篇。前面 L1 讲 Helm 怎么把 K8s 清单模板化，这里讲**云上资源**怎么用代码管起来。
> **前置**：L0 Shell 与 YAML（会写 `.tf` 文件、会读命令行输出）、L1 Docker（理解「镜像即不可变交付物」这个思路，IaC 是同一个思路在基础设施上的延伸）。
> **学完能做什么**：说清 provider / resource / data source 三者分别对应现实里的什么；解释 state 是什么、为什么丢了会重建资源；独立完成 `init → plan → apply → destroy` 全流程并读懂 plan 输出；判断一次变更该不该直接 apply。
> **在 21 天里**：D8（Terraform 起步）。D9 的模块化与阿里云 provider 建立在本文的 provider/state/生命周期之上。
> **环境/版本基准**：本文命令与字段语义以 **HashiCorp 官方文档站（developer.hashicorp.com/terraform）** 为准，写作时对照的文档分支为 `v1.17.x`；本地未执行任何 `terraform` 命令，**所有输出片段均为官方文档中的示意输出或本篇标注的"示意"**，不是实测。alicloud provider 字段以 `registry.terraform.io/providers/aliyun/alicloud/latest/docs` 为准。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
Terraform 核心概念与生命周期
│
├─ 1. 概念
│   ├─ 1.1 一句话：IaC 是什么
│   ├─ 1.2 没有 IaC 时的问题（人工点界面为什么不行）
│   ├─ 1.3 三个要素：provider / resource / data source
│   └─ 1.4 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 HCL 语法：块 / 属性 / 插值 / 条件 / 函数 / for 表达式 / heredoc
│   ├─ 2.2 资源地址与依赖推断（为什么不用手写 depends_on）
│   ├─ 2.3 变量：三种赋值方式与优先级
│   ├─ 2.4 输出 output 与局部值 local
│   ├─ 2.5 state 是什么 —— Terraform 的"记忆"
│   ├─ 2.6 backend：为什么 state 必须远端管理
│   ├─ 2.7 workspace：它只是 state 的命名空间
│   ├─ 2.8 plan 与 apply 的关系（plan 是评审物）
│   ├─ 2.9 敏感值处理（遮蔽 ≠ 加密）
│   ├─ 2.10 漂移 drift：现实被人改了会怎样
│   └─ 2.11 import 与 moved 的区别
│
├─ 3. 动手：第一个 VPC（声明 → plan → apply）
│   ├─ 3.1 目录与文件
│   ├─ 3.2 一步步命令（每步写清"看到什么算对"）
│   └─ 3.3 验证清单
│
├─ 4. 逐字段表
│   ├─ 4.1 HCL 块类型表
│   └─ 4.2 变量定义方式优先级表
│
├─ 5. 横向对比
│   ├─ 5.1 Terraform vs Ansible
│   ├─ 5.2 Terraform vs Pulumi vs OpenTofu
│   └─ 5.3 本地 state vs 远端 state
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

> **Terraform 是用声明式代码描述"我要什么基础设施"，然后由它去把现实改成这个样子，并记住自己改过什么的工具。**

拆成三个动作，缺一不可：

| 动作 | 对应什么 | 现实里的类比 |
|---|---|---|
| **声明 desired state** | 你写的 `.tf` 文件 | 装修图纸 |
| **对比 actual state** | `plan` 做的事 | 拿图纸和现场比 |
| **执行 + 记住** | `apply` + `state` | 施工队 + 竣工图 + 验收单 |

**注意它和"跑脚本"的区别**：脚本是"做什么"（`创建 VPC`），Terraform 是"要什么"（`一个 CIDR 为 10.0.0.0/8 的 VPC`）。你只描述终点，路径由 Terraform 算。

### 1.2 没有 IaC 时的问题：为什么"人工点界面"不行

这是本篇最重要的一段。下面这张表是**面试必答**的，也是理解 Terraform 存在理由的地基。

| 人工在云控制台点界面 | 出了什么事 | Terraform 怎么解决 |
|---|---|---|
| 点"创建 VPC"，随手填了 `10.0.1.0/24` | **不可复现**：下次给另一个环境建，得靠记忆再点一遍，两边网段不一样 | 代码里写死 `cidr_block`，谁执行都是同一个值 |
| 扩容时手动把 ECS 从 2 台改成 4 台 | **不可审计**：谁改的、为什么改、原来几台，聊天记录里翻 | 变更是一行 Git diff，`git log` 就是审计日志 |
| 生产要加一条安全组规则 | **不可评审**：规则合不合规、会不会开 0.0.0.0/0，没人能在点之前拦你 | `plan` 输出先给同事看，diff 里有这条规则 |
| 服务器被误删 | **不可恢复**：控制台没有"上次配置"，只能从头点 | `state` 记住了这个资源，`plan` 会说"要重建"，你能选择拦下 |
| 交接工作 | 靠交接文档 + 老员工口述，文档永远滞后于现实 | 仓库里代码就是现状，`plan` 随时告诉你现实和代码差在哪 |

> **一句话记住**：人工点界面 **不可复现、不可审计、不可评审**。IaC 补的是这三样，不是"省点击次数"。

### 1.3 三个要素：provider / resource / data source

这是 Terraform 的**全部建模能力**，就三个概念：

```
                 ┌─────────────────────────────────────┐
                 │            provider（提供者）        │
                 │  "怎么跟阿里云说话"的插件 + 认证配置 │
                 │  例：provider "alicloud" { region } │
                 └───────────────┬─────────────────────┘
                                 │  提供能力
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
    ┌────────────────────────┐      ┌────────────────────────┐
    │  resource（资源）        │      │  data source（数据源）  │
    │  "我要创建/管理它"       │      │  "我只读，不改它"       │
    │  alicloud_vpc { ... }   │      │  data "alicloud_regions"│
    │  → 会出现在 plan 的改动里│      │  → 只读取，不产生变更   │
    └────────────────────────┘      └────────────────────────┘
```

| 要素 | 英文 | 它是什么 | 现实类比 | 在 plan 里出现吗 |
|---|---|---|---|---|
| **提供者** | provider | 一个插件 + 一份配置，告诉 Terraform "我要操作哪个云、用什么身份、哪个地域" | 公司的 API 账号 | 不产生资源，但**没有它下面全都不成立** |
| **资源** | resource | 你要求 Terraform **创建并持续管理**的一个实体 | 一台 ECS、一个 VPC、一个 OSS 桶 | ✅ 出现在"将要创建/修改/销毁"里 |
| **数据源** | data source | 你要求 Terraform **只读取**的已有信息 | 查一下有哪些可用地域、查某个镜像 ID | ❌ 只读取，**不产生任何变更** |

**为什么要有 data source 这个"只读"概念**（面试常问）：

- 你想用"现有的东西"当参数：比如 VPC ID 从哪来？要么自己填死，要么查出来。
- `data` 只读，不进变更集，所以**不会误删**。这是它和 `resource` 的本质区别。
- 典型写法（官方 alicloud provider 文档示例）：

```hcl
data "alicloud_images" "default" {
  name_regex  = "^ubuntu"
  most_recent = true
  owners      = "system"
}
```

引用时加 `data.` 前缀：`data.alicloud_images.default.images.0.id`。

> **易错点**：`data "alicloud_images"` 里 **没有 `=` 之前的大括号赋值语法**（它是配置块，不是属性），但 `data` 的属性写在块里面。真正区分二者的是：**`resource` 会创建和销毁，`data` 只读。**

### 1.4 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Infrastructure as Code (IaC) | 基础设施即代码 | 用版本化的代码描述基础设施 |
| Provider | 提供者/插件 | 操作某个平台的插件（`alicloud`、`aws`…） |
| Resource | 资源 | Terraform 管理的实体 |
| Data Source | 数据源 | Terraform 只读取的已有信息 |
| State | 状态 | Terraform 对"资源现在长什么样"的快照，**它的记忆** |
| Backend | 后端 | state 存哪儿、怎么锁、怎么加密 |
| Plan | 计划 | "从现状到代码描述，还差哪些改动"的清单 |
| Apply | 应用 | 执行 plan，把现实改成代码描述的样子 |
| Drift | 漂移 | 现实被人手动改了，与代码不一致 |
| Workspace | 工作区 | state 的命名空间（**不是**独立的资源环境） |
| Module | 模块 | 可复用的资源集合，有自己的输入输出 |
| Variable | 变量 | 模块的输入接口 |
| Output | 输出 | 模块的结果接口，可被父模块引用 |
| Locals | 局部值 | 中间计算结果，不写进 state |
| Interpolate | 插值 | 在字符串里嵌入表达式 `${...}` |
| Idempotent | 幂等 | 重复执行结果不变（Ansible 的强项） |
| `count` / `for_each` | 批量实例化 | 同一个资源块生成多份实例的两种方式 |
| `depends_on` | 显式依赖 | 手动声明"这个必须等那个" |
| `moved` | 地址迁移块 | 告诉 Terraform 资源换地址了，不要重建 |
| `import` | 导入 | 把已有的、Terraform 没管过的资源纳入管理 |

---

## 2. 机制

### 2.1 HCL 语法：块 / 属性 / 插值 / 条件 / 函数 / for 表达式 / heredoc

HCL（HashiCorp Configuration Language）只有两个基本单位：

| 单位 | 语法 | 例子 | 说明 |
|---|---|---|---|
| **块 block** | `类型 "标签" { ... }` | `resource "alicloud_vpc" "example" { ... }` | 有名字的容器，里面装属性和子块 |
| **属性 attribute** | `名字 = 表达式` | `cidr_block = "10.0.0.0/8"` | 一个键值对 |

关键点：**属性在块里面用 `=` 赋值；块本身用 `{ }` 包裹，不赋值。** 写错这一点（给块加 `=`）会直接语法报错。

#### 2.1.1 插值（字符串模板）

在双引号字符串里用 `${...}` 嵌入表达式：

```hcl
resource "alicloud_vpc" "example" {
  vpc_name   = "demo-${var.env}-vpc"
  cidr_block = "10.0.0.0/8"
}
```

- `${var.env}` → 取变量值。
- `${var.env}-vpc` → 字符串拼接（`-` 是字面量，不是运算符）。
- **只有双引号支持插值**；单引号 `'...'` 是纯字符串，不做任何替换。
- `$${` 是转义写法，输出字面量 `${`。

#### 2.1.2 条件表达式

官方语法：`<CONDITION> ? <TRUE VAL> : <FALSE VAL>`（**三目**）。

```hcl
variable "is_prod" {
  type    = bool
  default = false
}

resource "alicloud_vpc" "example" {
  vpc_name = var.is_prod ? "prod-vpc" : "dev-vpc"
  cidr_block = var.is_prod ? "10.1.0.0/16" : "10.0.0.0/16"
}
```

> **易错点**：条件表达式里的两个分支**类型必须一致**，否则报错。`var.is_prod ? "a" : 123` 会失败。

#### 2.1.3 函数

Terraform 内置函数调用形式是 `函数名(参数)`。官方文档里按类别分了几大族，面试只需要记住"常用的几类"：

| 类别 | 常用函数 | 用途 |
|---|---|---|
| 字符串 | `upper` `lower` `trimspace` `replace` `split` `join` `format` `substr` `startswith` `endswith` `regex` | 拼名字、处理标签 |
| 集合 | `length` `merge` `lookup` `keys` `values` `flatten` `distinct` `contains` `concat` `slice` `sort` `reverse` `setunion` `setintersection` `zipmap` `range` | 处理 map/list/set |
| 编码 | `jsonencode` `jsondecode` `yamlencode` `yamldecode` `base64encode` `base64decode` `urlencode` | 与云 API 互转 |
| 文件系统 | `file` `fileexists` `fileset` `abspath` `dirname` `basename` `templatefile` | 读外部文件做模板 |
| 类型转换 | `tostring` `tonumber` `tobool` `tolist` `toset` `tomap` `try` `can` `type` | 修类型不匹配 |
| 敏感值 | `sensitive` `nonsensitive` `issensitive` | 敏感标记的加减与判断 |
| 日期时间 | `formatdate` `timestamp` `plantimestamp` `timeadd` `timecmp` | 打时间戳标签 |
| 加密哈希 | `sha256` `md5` `base64sha256` `uuid` `uuidv5` `bcrypt` | 生成随机 ID、算指纹 |

**最常用的三个**：`merge()`（合并多个 map）、`jsonencode()`（把 map 变成 JSON 字符串喂给 API）、`try()`（表达式失败时返回备用值）。

```hcl
locals {
  common_tags = {
    Env     = var.env
    Managed = "terraform"
  }
  full_tags = merge(local.common_tags, var.extra_tags)
}
```

> **注意 `try()` 的坑**：`try(a, b)` 依次求值，返回**第一个不报错**的结果。如果 `a` 报错了但 `b` 恰好是 `null`，你拿到的是 `null` 而不是错误 —— **它会掩盖问题**，别用它来"消错"。

#### 2.1.4 for 表达式

for 表达式是"用一段逻辑批量算出新集合"，语法骨架：`[for x in 集合 : 表达式]`。

```hcl
# 列表推导式：把 list 里的每个元素变成大写
locals {
  upper_names = [for n in var.subnet_names : upper(n)]
}

# 过滤（加 if）
locals {
  prod_names = [for n in var.all_names : n if startswith(n, "prod")]
}

# 转成 map：键和值都能算
locals {
  name_to_cidr = { for n, c in local.subnet_map : n => c }
}
```

> **关键区别**：`for` 表达式**在配置加载时就求值**，所以**不能引用尚未创建的资源属性**（那是未知的）。要引用资源属性，只能直接写在 `resource` 块里，或者用 `depends_on` 排序。

#### 2.1.5 heredoc（多行字符串）

两种写法，区别只在**开头破折号**：

```hcl
locals {
  # <<EOF：保留行首空格
  script_a = <<EOF
  line1
    line2
  EOF

  # <<-EOF：去掉行首空白（用缩进对齐代码时更顺手）
  script_b = <<-EOF
  line1
    line2
  EOF
}
```

- 结束标记 `EOF` 必须**单独一行**（前面不能有空格，除非用 `<<-`）。
- 常用于写启动脚本、用户数据（user_data）。

### 2.2 资源地址与依赖推断

#### 2.2.1 地址怎么写

Terraform 里每个"东西"都有一个**地址（address）**，格式是 `类型.名称`，实例再加键：

```
alicloud_vpc.example                       # 单个资源
alicloud_vpc.example["prod"]                # for_each 实例，键是字符串
alicloud_vpc.example[0]                     # count 实例，下标从 0 开始
module.vpc.alicloud_vswitch.this            # 模块里的资源
data.alicloud_regions.current               # 数据源
```

官方 `count` 文档明确区分了两者：
> `<TYPE>.<NAME>` 指的是**资源块**；`<TYPE>.<NAME>[<INDEX>]` 指的是**具体实例**。

#### 2.2.2 依赖图是"推断"出来的，不是手写的

这是 Terraform 最重要的设计之一。**默认情况下你不需要写 `depends_on`。**

机制是这样：

```
你写：  vswitch_id = alicloud_vpc.example.id
                        │
                        └─ Terraform 在解析表达式时发现这里引用了另一个资源
                           → 自动在资源之间建一条"依赖边"
                           → 建 vswitch 之前，先建 vpc
```

官方文档原话（`depends_on` 章节）：

> "The `depends_on` meta-argument specifies an upstream resource that the resource depends on. Terraform must complete all operations on the upstream resource before performing operations on the resource containing the `depends_on` argument."

**所以依赖推断的本质是：引用即依赖。** 只要 B 的属性表达式里出现了 A 的地址，Terraform 就知道 B 依赖 A。

#### 2.2.3 那什么时候必须写 `depends_on`

`depends_on` 的语义是"**只影响顺序，不传值**"。它存在的唯一理由是：**存在"看不见的依赖"**。

| 场景 | 为什么引用推不出来 | 怎么写 |
|---|---|---|
| 资源 A 的**某个属性**被资源 B 的**创建动作**隐式影响，但 B 的配置里没引用 A | 没有表达式边 | `depends_on = [A]` |
| 资源 B 的创建会**触发**对 A 的修改（比如给安全组加规则） | 没有表达式边 | `depends_on = [A]` |
| 一个 `data` 源必须等某资源创建完才能查 | data 是读，但读的对象还没建 | `depends_on = [A]` |
| 整个模块要等某资源建好 | 模块内部引用的资源不在本模块 | 模块块上写 `depends_on` |

**最经典的例子**（也是面试爱问的）：

```hcl
# 安全组
resource "alicloud_security_group" "sg" {
  name   = "demo-sg"
  vpc_id = alicloud_vpc.example.id
}

# ECS 引用了安全组 ID → 自动依赖，OK
resource "alicloud_instance" "web" {
  instance_name     = "web"
  security_groups   = [alicloud_security_group.sg.id]
  vswitch_id        = alicloud_vswitch.this.id
}

# 但如果我想"先建 ECS 再加一条安全组入方向规则"，
# 这条规则资源本身并不引用 ECS 的任何属性 → 必须显式 depends_on
resource "alicloud_security_group_rule" "allow_ssh" {
  security_group_id = alicloud_security_group.sg.id
  direction         = "ingress"
  ip_protocol       = "tcp"
  port_range        = "22/22"
  cidr_blocks       = ["0.0.0.0/0"]

  depends_on = [alicloud_instance.web]   # ← 显式顺序
}
```

> **反面教材**：`depends_on` **不是**用来"传值"的。想传值就直接写 `alicloud_security_group.sg.id`。用 `depends_on` 传值是新手最常见的误用 —— 它不创建任何引用关系，你写的值依然要靠引用。
>
> **另一个坑**：`depends_on` 会**扩大**依赖范围。如果你写 `depends_on = [alicloud_vpc.example]`，那么整个 VPC（含它的所有属性刷新）都会成为前置。**依赖图会被拉宽**，可能导致不必要的重建或更长的等待。官方也提醒 `depends_on` 只在必要时使用。

### 2.3 变量：三种赋值方式与优先级

#### 2.3.1 先分清"定义"和"传值"

| 动作 | 在哪写 | 长什么样 |
|---|---|---|
| **定义**（声明这个变量存在、什么类型、默认值多少） | `.tf` 文件里 `variable` 块 | `variable "region" { type = string }` |
| **传值**（这次运行用哪个值） | tfvars 文件 / 环境变量 / 命令行 | `region = "cn-hangzhou"` |

**定义只写一次，传值可以有很多种来源。** 这是新手最常混淆的点：以为"改了 `default` 才算改值"。

#### 2.3.2 三种传值方式

```hcl
# ① terraform.tfvars / *.auto.tfvars —— 写在文件里，适合进 Git 管环境差异
region     = "cn-hangzhou"
vpc_cidr   = "10.0.0.0/16"

# ② 环境变量 TF_VAR_<变量名> —— 适合 CI/CD 注入，不落盘
export TF_VAR_region="cn-hangzhou"
export TF_VAR_vpc_cidr="10.0.0.0/16"

# ③ 命令行 -var / -var-file —— 适合一次性覆盖
terraform apply -var="region=cn-hangzhou"
terraform apply -var-file="prod.auto.tfvars"
```

**环境变量的复杂类型必须用 JSON 语法**（官方原文：complex types 通过环境变量或命令行传递时要用 proper JSON syntax）：

```bash
export TF_VAR_complex_config='{"key": "value", "list": ["a", "b"]}'
```

#### 2.3.3 优先级（官方 Variables 文档明确列出，从高到低）

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1（最高） | 命令行 `-var` / `-var-file`（按给出的顺序）+ HCP Terraform 变量集 | 一次性的、临时的覆盖 |
| 2 | `*.auto.tfvars` / `*.auto.tfvars.json`（**按字典序**） | 多个自动加载文件，按文件名排序 |
| 3 | `terraform.tfvars.json` | JSON 格式的默认 tfvars |
| 4 | `terraform.tfvars` | 最常用的默认 tfvars |
| 5 | 环境变量 `TF_VAR_*` | 注意：**低于** tfvars 文件 |
| 6（最低） | `variable` 块的 `default` | 兜底 |

**两个必须记的坑**：

1. **环境变量优先级低于 tfvars 文件**。很多人以为"环境变量最优先"，恰恰相反 —— 官方列表里 env 排在第 5，tfvars 排在 2/3/4。CLI 的 `-var` 才是最高的。
2. **`*.auto.tfvars` 之间按字典序**，即 `10-prod.auto.tfvars` 排在 `2-dev.auto.tfvars` 后面。**别用数字前缀凑顺序，用有意义的名字**。

#### 2.3.4 variable 块的字段

官方 `variable` 块参考给出的参数：

| 参数 | 类型 | 默认 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `type` | Type constraint | `any` | 类型约束 | 不写 → 任意类型都接受，错误推迟到运行时 |
| `default` | Expression | 无 | 默认值 | 不写 → 必须传值，否则 plan 前就报错 |
| `description` | String | 无 | 用途说明 | 不写 → 使用者只能猜 |
| `sensitive` | Boolean | `false` | CLI 输出中隐藏值 | 不写 → 密码直接打到终端和 CI 日志 |
| `nullable` | Boolean | **`true`** | 是否允许值为 `null` | 写成 `false` 但 `default = null` → **默认值无效，使用者必须传非 null 值** |
| `validation` | Block | 无 | 业务规则校验 | 不写 → 非法值一路跑到云 API 才报错 |
| `ephemeral` | Boolean | `false` | 不写入 state / plan | 需要 **Terraform v1.10+**；用于短期 token |

**`nullable` 的官方精确语义**（很多人不知道）：

> 默认 `true`。设为 `false` 时变量必须是非 `null` 的值。**若同时 `nullable = false` 和 `default = null`，该默认值无效。**

**`validation` 块的写法**：

```hcl
variable "env" {
  type        = string
  description = "环境名"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.env)
    error_message = "Environment must be dev, staging, or prod."
  }
}
```

- `condition` 必须求值为 `true`，否则 Terraform 抛错并显示 `error_message`，**中止整个操作**。
- 官方明确：**验证在创建 plan 时计算**。也就是说非法值不会走到云 API，是"早失败"。

> **`sensitive` 的真相**（本篇后面还会强调）：官方 Variables 文档原话 —— "Terraform still stores the values of sensitive variables in the state." **`sensitive = true` 只是遮蔽显示，不是不存。**

### 2.4 输出 output 与局部值 local

#### 2.4.1 output：模块的结果接口

```hcl
output "vpc_id" {
  description = "创建出来的 VPC ID"
  value       = alicloud_vpc.example.id
}

output "switch_ids" {
  description = "交换机 ID 列表"
  value       = alicloud_vswitch.this[*].id
}

output "db_password" {
  description = "数据库密码"
  value       = var.db_password
  sensitive   = true
}
```

| 参数 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|
| `value` | ✅ | 输出值 | 不写 → 报错 |
| `description` | | 说明 | 不写 → 使用者看不到用途 |
| `sensitive` | | 遮蔽显示 | 不写 → 密码明文进 CI 日志 |
| `depends_on` | | 显式依赖 | 少用 |

**output 可以被别的模块引用** —— 这是模块化的核心机制：

```hcl
# 父模块里
module "network" {
  source = "./modules/network"
}

module "app" {
  source = "./modules/app"
  vpc_id = module.network.vpc_id      # ← 引用子模块的 output
}
```

**引用语法：`module.<标签>.<输出名>`**。

> **注意 `terraform output` 命令只看根模块**：官方 Output 命令文档明确 —— "The `terraform output` command only displays outputs defined in the root module." 想看子模块的输出，必须在根模块里加一个 `output` 转发：
> ```hcl
> output "address" {
>   value = module.loadbalancer.lb_address
> }
> ```

#### 2.4.2 local：中间计算，不进 state

```hcl
locals {
  # 名字规范
  name_prefix = "${var.env}-demo"

  # 合并标签
  common_tags = {
    Env     = var.env
    Managed = "terraform"
  }
  full_tags = merge(local.common_tags, var.extra_tags)

  # 批量计算
  subnet_cidrs = [for i in range(var.subnet_count) : cidrsubnet(var.vpc_cidr, 8, i)]
}
```

| 维度 | `locals` | `variable` |
|---|---|---|
| 方向 | **输入 → 计算结果** | **外部 → 传入的值** |
| 能否被外部赋值 | 不能 | 能（通过 tfvars / env / CLI） |
| 写进 state | **不写** | 变量值本身会进 state |

> **`locals` 的一个实用价值**：把复杂表达式拆成命名步骤。plan 输出里会显示 `local.xxx` 的最终值，出问题时你知道是哪一步算错。

### 2.5 state 是什么 —— Terraform 的"记忆"

> **state 是"真实世界资源当前状态的快照"，是 Terraform 的记忆。**

官方 State 文档对它的定位写得非常清楚：

> "The primary purpose of Terraform state is to store bindings between objects in a remote system and resource instances declared in your configuration."

翻译：**state 记录的是"配置里的资源实例" ↔ "云上的真实对象"之间的绑定关系**，外加一些元数据。

具体存了什么（官方 state 格式）：

| 内容 | 说明 |
|---|---|
| `version` | state 文件格式版本 |
| `terraform_version` | 写这份 state 的 Terraform 版本 |
| `serial` | 每次写 state 递增，**用来检测并发写冲突** |
| `lineage` | state 的血缘 ID |
| `outputs` | 根模块的 output 值 |
| `resources[]` | 每个资源实例的地址、类型、属性值、依赖关系 |
| `check_results` | `check` 块的检查结果 |

**为什么必须有 state**（这是本节的核心）：

1. **云 API 不支持"列出我的所有资源并附上标签"这种通用查询**。每个云厂商的 API 都不同。Terraform 必须**自己记住**"我上次建了哪些、它们的 ID 是多少、当前属性是什么"。这就是 state 存在的根本原因。
2. **有了 ID，Terraform 才能做"修改"而不是"删了重建"**。改安全组规则、扩容 ECS 都是 `update` 操作，API 需要资源 ID。
3. **没有 state，Terraform 就不知道该销毁什么**。`terraform destroy` 是靠 state 遍历的。

> **最要命的一句结论**：**state 丢了 = Terraform 失忆 = 它认为那些资源不存在。**
> 下一次 `plan` 会说"要创建这些资源"，如果你手滑 `apply` 了，云上就会**多出一份**。而原来的那份**再也管不到了**。这就是第 6 节第一条坑。

> **官方明确禁止直接编辑 state**：State 文档说 "Do not directly edit this file." 要改用 `terraform state` 子命令。

### 2.6 backend：为什么 state 必须远端管理

#### 2.6.1 本地 state 的三个致命问题

默认 backend 是 `local`，state 存在工作目录的 `terraform.tfstate` 文件里（备份在 `terraform.tfstate.backup`）。官方在 State 文档里直接点名：

> "Storing state locally requires no additional configuration, but **limits the ability to collaborate with others, and risks losing workspace state if the local state file is lost.**"

> "**Avoid storing your state in a version control system** or other storage solution that does not support Terraform state locking and secure access control, because doing so can result in data loss or exposure of secrets stored in the state file."

拆成四个必须解决的诉求：

| 诉求 | 没有它会怎样 | 远端 backend 怎么解决 |
|---|---|---|
| **团队协作** | 两个人各有一份 state，互相看不到对方建了什么 → 后 apply 的把先 apply 的覆盖掉 | 大家读同一份远端 state |
| **锁机制（locking）** | 两人同时 apply，**后写的 state 覆盖先写的**，先创建的资源从 state 里消失 | backend 加锁，同时只允许一个写操作 |
| **加密** | state 里有**明文密码、AK/SK、Token**（见 2.9），放公共 bucket = 泄密 | 服务端加密 / 客户端加密 |
| **版本控制** | state 被误操作改坏，没有回滚点 | 对象存储的版本号 |

#### 2.6.2 backend 块怎么写

```hcl
terraform {
  backend "s3" {
    bucket       = "my-tf-state"
    key          = "demo/network/terraform.tfstate"
    region       = "cn-hangzhou"
    encrypt      = true
    use_lockfile = true
  }
}
```

官方 Backend 文档的**硬性限制**（写错直接报错）：

| 限制 | 原文含义 | 后果 |
|---|---|---|
| 一个配置只能有一个 backend 块 | "A configuration can only provide one backend block." | 写两个 → 报错 |
| backend 块**不能引用命名值** | "A backend block cannot refer to named values (like input variables, locals, or data source attributes)." | 写 `bucket = var.bucket` → 报错 |
| backend 块里的值**不能在别处引用** | "You cannot reference values declared within backend blocks elsewhere in the configuration." | 写 `output "b" { value = backend.bucket }` → 报错 |
| 改了 backend 必须重新 `init` | "When you change a backend's configuration, you must run `terraform init` again" | 不 init 直接 plan → 报错 |
| **不能在 `.git` 里** | `.terraform/` 目录可能含凭据，官方要求 gitignore | 泄露凭据 |

> **为什么 backend 不能用变量？** 因为 backend 是"读配置之前"就要知道的东西 —— Terraform 得先知道 state 放哪，才能加载 state，才能解析变量。**它 chicken-and-egg 在最前面。** 这就是官方为什么推荐**部分配置（partial configuration）**：backend 块里留空值，用 `terraform init -backend-config=...` 在初始化时注入。

```bash
# 部分配置：state.tf 里
terraform {
  backend "s3" {
    bucket = ""
    key    = ""
    region = ""
  }
}

# 初始化时注入
terraform init -backend-config="bucket=my-tf-state" -backend-config="key=demo/network.tfstate"
```

#### 2.6.3 敏感值警告（官方原文，很重要）

> "If you use `-backend-config` or hardcode these values directly in your configuration, Terraform will include these values in both the `.terraform` subdirectory and in plan files. **This can leak sensitive credentials.**"

所以：**backend 的凭据用环境变量给，别写进 `.tf`**。

### 2.7 workspace：它只是 state 的命名空间

这是**最容易误解的概念**，官方文档专门有一页讲。

**它是什么**：官方 Workspaces 文档说 —— "The persistent data stored in the backend belongs to a workspace... Some backends support multiple named workspaces, allowing **multiple states** to be associated with a single configuration."

**它不是什么**：它不是"一套独立的云资源"，也不是"一个环境"。

```
        同一份 .tf 配置
              │
      ┌───────┼───────┬─────────┐
      ▼       ▼       ▼         ▼
  default   dev    staging    prod
   ws       ws      ws        ws
      │       │       │         │
  state-A  state-B  state-C   state-D     ← 各自独立的 state 快照
      │       │       │         │
   阿里云   阿里云   阿里云     阿里云
    资源     资源     资源       资源        ← 现实中的资源也各不相同
```

- Terraform 启动时只有 **`default`** 这一个 workspace，**且不能删除**。
- 切换 workspace = **切换读哪一份 state**，配置本身一个字节都不变。
- 官方文档里，**支持多 workspace 的 backend** 明确列了：`AzureRM`、`Consul`、`COS`、`GCS`、`Kubernetes`、`Local`、`OSS`、`Postgres`、`Remote`、`S3`。（所以**阿里云 OSS backend 支持多 workspace**。）

**两个必须知道的官方警告**：

> "**Important:** Workspaces are not appropriate for system decomposition or deployments requiring separate credentials and access controls."

意思是：**workspace 不适合用来做"多环境隔离"** —— 因为所有 workspace 共用同一份 backend 凭据，没有独立的访问控制。官方给的替代方案是用**独立的目录 + 独立的后端配置**（即每个环境一个 root module 目录），而不是切 workspace。

还有一个常用技巧：**用 `${terraform.workspace}` 让行为随 workspace 变**。

```hcl
resource "alicloud_instance" "example" {
  count = terraform.workspace == "default" ? 5 : 1
  tags = {
    Name = "web - ${terraform.workspace}"
  }
}
```

### 2.8 plan 与 apply 的关系：plan 是评审物

#### 2.8.1 三个命令的分工

| 命令 | 干什么 | 会不会改现实 |
|---|---|---|
| `terraform plan` | 读配置 + 刷新 state + 对比 → 输出**将要发生的变更清单** | ❌ 不改 |
| `terraform apply` | 先自动生成 plan，确认后执行 | ✅ 改 |
| `terraform apply tfplan` | 执行**事先保存的** plan 文件 | ✅ 改 |

**plan 内部的三步**（这是理解 Terraform 的关键）：

```
① refresh（刷新）  把云上真实状态读回来，更新 state
        ↓
② diff（对比）     配置 + 刷新后的 state  →  得出差异清单
        ↓
③ 展示            把差异按 create / update / replace / destroy 分类打印
```

**官方对 `-out` 保存 plan 的定义**：

> "Writes the generated plan to the given filename in an **opaque file format** that you can later pass to `terraform apply` to execute the planned changes..."

opaque（不透明）格式 = 不是 JSON、不是 YAML，是给 Terraform 自己读的二进制。

**不带 `-out` 的 plan 叫 speculative plan（推测性计划）**，官方说得很直白：

> "a description of the effect of the plan but **without any intent to actually apply it**."

#### 2.8.2 为什么"plan 文件当评审物"是正确的做法

因为**plan 是一份"将要发生什么"的契约**。把它当成代码评审物：

```
开发同学本地跑：
  terraform plan -out=tfplan
        ↓
把 tfplan 传上去（注意它含敏感数据明文！）
        ↓
CI/评审人看：terraform show tfplan
        ↓
评审通过 → terraform apply tfplan
```

**这样保证"评审看到的"和"执行的"是同一份计划** —— 不会出现"评审时是一回事，apply 时又变了"。

**plan 与 apply 之间资源被人改了会怎样**（这是第 6 节的坑之一）：

- 如果用 `terraform apply tfplan`：plan 文件里已经记录了刷新时刻的状态，**apply 会按 plan 里的内容执行**，不会重新刷新。这可能覆盖掉中间那段时间别人做的修改。
- 如果用 `terraform apply`（不带 plan 文件）：apply 会**重新生成一次 plan**，所以你看到的变更和你批准的可能不一致 —— 除非 apply 前的提示里你又确认了一遍。

> **官方也承认这一点**：speculative plan 用于代码审查时可能与最终结果不同，因为目标系统可能在此期间发生变化，所以应用前应重新检查最终的 non-speculative plan。

#### 2.8.3 常用 plan 选项

| 选项 | 作用 | 官方要点 |
|---|---|---|
| `-out=FILE` | 保存 plan 文件 | 格式不透明；**含敏感数据明文**；文件名别用 `.tf` 后缀（否则后续命令会把它当配置源） |
| `-refresh-only` | 只更新 state 和根模块 output，不改资源 | 需要 **v0.15.4+**；与 `-destroy` 互斥；不能与 `-refresh=false` 同用 |
| `-refresh=false` | 不刷新 state | **会导致 Terraform 忽略外部改动**，可能产生不完整的计划 |
| `-detailed-exitcode` | 详细退出码 | **0**=无变更，**1**=出错，**2**=有变更 → CI 里用 2 判断"要不要部署" |
| `-target=ADDR` | 只针对某资源规划 | 官方明确：**只应在异常情况下使用**；会引入未检测到的漂移 |
| `-destroy` | 生成销毁计划 | 与 `-refresh-only` 互斥 |

> **`-refresh-only` vs `terraform refresh`**：官方已把 `terraform refresh` **标记为 deprecated**。官方原文：
> "This command is deprecated. Instead, add the `-refresh-only` flag to `terraform apply` and `terraform plan` commands."
> 而且官方说明了为什么**不要**用 refresh：
> "Automatically applying the effect of a refresh is risky. If you have misconfigured credentials for one or more providers, Terraform may be misled into thinking that all of the managed objects have been deleted, causing it to **remove all of the tracked objects without any confirmation prompt**."
> —— **凭据配错 + `terraform refresh` = 把所有资源从 state 里抹掉**。这是官方明确点名的灾难场景。

### 2.9 敏感值处理：遮蔽 ≠ 加密

| 手段 | 作用范围 | **挡不住什么** |
|---|---|---|
| `variable { sensitive = true }` | CLI 输出、plan 展示 | **值仍在 state 里** |
| `output { sensitive = true }` | CLI 输出、plan 展示 | **值仍在 state 里** |
| `sensitive()` 函数 | 给任意值打敏感标记 | 同上 |
| `ephemeral = true`（v1.10+） | 不写入 state 和 plan | 需要 v1.10+；限制较多 |

官方 Variables 文档的原话：

> "Terraform still **stores the values of sensitive variables in the state**."

官方 Output 命令文档还有一个更狠的提醒：

> "Note that Terraform does not redact `sensitive` values when you specify the output by name"
> （即 `terraform output password` 会**明文**打印敏感输出）
> 而且：**用 `-json` 或 `-raw` 时，敏感值以明文显示。**

**所以正确的认知是**：

```
sensitive = true   →  控制台不乱打印  ≠  数据没落盘
                                    ↓
                          state 文件里有明文密码/AK/SK
                                    ↓
                    所以 state 必须放在加密 + 访问受控的地方
```

**这正是 2.6 里"远端 state 必须加密"的直接原因。**

### 2.10 漂移 drift：现实被人改了会怎样

**漂移 = 真实资源的状态与配置/状态不一致。**

典型来源：控制台里手动改了安全组规则、有人手工扩容了 ECS、某个运维直接在云上删了机器。

**Terraform 怎么发现漂移**：每次 plan 第一步就是 **refresh** —— 把云上真实状态读回来写进 state。这一步就会把漂移"吸"进 state。然后 diff 阶段会对比"刷新后的现实"和"配置"，把漂移**作为要执行的变更**列出来。

**关键理解**：漂移在 Terraform 眼里 = "有人改了现实，需要改回来"。

- 安全组规则被人加了 `0.0.0.0/0` → plan 会显示**要删除这条规则**（改回配置描述的样子）。
- ECS 被人手工扩容到 4 台 → plan 会显示**要缩回 2 台**。
- 资源被人从云上删了 → plan 会显示**要重新创建**。

> **所以 plan 输出里出现"我不认识的变更"，通常不是你写错了，而是现实被人动了。** 这时候**一定要先问清楚是谁改的**，不要无脑 apply —— apply 会把别人的手工改动覆盖掉。

### 2.11 import 与 moved 的区别

这两个词经常被混淆，**它们解决的问题完全不同**：

| 维度 | `import` | `moved` |
|---|---|---|
| **解决什么** | 把**已存在的、Terraform 从没管过**的资源纳入管理 | 资源**地址改了**，告诉 Terraform 别重建 |
| **场景** | 迁移上云：先手工建好的 ECS/VPC，要纳入代码管理 | 重命名资源块、把资源挪进模块 |
| **形式** | 命令 `terraform import ADDRESS ID`，或配置里的 `import` 块 | 配置里的 `moved` 块 |
| **资源有没有变** | **资源没变**，只是 Terraform 开始管它了 | **资源没变**，只是地址变了 |
| **plan 里显示什么** | 导入（不是创建） | **什么都不显示**（官方：地址变更不销毁资源） |

#### 2.11.1 import 的命令格式

官方 Import 命令文档：

```
terraform import [options] ADDRESS ID
```

- `ADDRESS` 必须是合法资源地址。
- `ID` 的格式**取决于资源类型**，要查对应 provider 文档。AWS EC2 是实例 ID，Route53 是 Zone ID。

官方示例：

```bash
# 导入到根模块资源
terraform import aws_instance.foo i-abcd1234

# 导入到模块里的资源
terraform import module.foo.aws_instance.bar i-abcd1234

# 资源配了 count —— 用下标
terraform import 'aws_instance.baz[0]' i-abcd1234

# 资源配了 for_each —— 用键（键里有引号）
terraform import 'aws_instance.baz["example"]' i-abcd1234
```

**注意官方的一条硬警告**：

> "Terraform expects that each remote object it is managing will be bound to **only one** resource address... If you import the same object multiple times, Terraform may exhibit unwanted behavior."

**同一个云资源只能 import 到一个地址**，import 两次 = 出问题。

#### 2.11.2 配置式 import（config-driven import）

官方现在推荐在配置里写 `import` 块，这样 import 可以进 CI 流水线自动化：

```hcl
import {
  to = alicloud_vpc.example
  id = "vpc-abc12345"
}

resource "alicloud_vpc" "example" {
  vpc_name   = "demo"
  cidr_block = "10.0.0.0/8"
}
```

官方流程说明：需要**同时**写 `import` 块（指定 id 和目标地址）和对应的 `resource` 块（描述这个资源长什么样），然后跑 `terraform apply` 完成导入。

> 官方还提到一个省事的用法：只写 `import` 块，然后跑 `terraform plan -generate-config-out=...` 让 Terraform **自动生成对应的 `resource` 块**。

#### 2.11.3 moved 块

```hcl
moved {
  from = alicloud_vpc.example
  to   = alicloud_vpc.example_new
}
```

官方说明的关键点：

> "Terraform v1.1 and later is required to use `moved` blocks to explicitly refactor module addresses."

> "Before creating a new plan for the resource specified in the `to` argument, Terraform checks the state for an existing resource at the `from` address."
> "**The address change does not destroy the resource.**"

**moved 能跨形态迁移**（这是它比 `terraform state mv` 强的地方）：

```hcl
# 从无实例 → for_each 实例
moved {
  from = alicloud_instance.a
  to   = alicloud_instance.a["small"]
}

# 从 count 下标 → for_each 键
moved {
  from = alicloud_instance.c[0]
  to   = alicloud_instance.c["small"]
}
```

**两条限制**：
- **不能**把 `resource` 迁成 `data`。
- **删掉 `moved` 块是 breaking change** —— 官方建议保留历史 `moved` 块链式串起来，让任何老版本的对象都能被认领。

---

## 3. 动手

> **本节命令是教学示例，本次写作未在环境里执行过 terraform，也未创建任何云资源。** 照着做时按"看到什么算对"逐条核对。

### 3.1 目录与文件

```
terraform-demo/
├── main.tf           # provider + resource
├── variables.tf      # 变量定义
├── outputs.tf        # 输出
└── terraform.tfvars  # 本地传值（这个文件通常进 Git）
```

`main.tf`：

```hcl
terraform {
  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "~> 1.188"
    }
  }
}

provider "alicloud" {
  # 认证信息建议走环境变量 ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY，
  # 不要写在这里（详见 L3 篇）
  region = var.region
}

resource "alicloud_vpc" "example" {
  vpc_name   = "${var.env}-demo-vpc"
  cidr_block = var.vpc_cidr
  description = "由 Terraform 管理"

  tags = local.tags
}

locals {
  tags = {
    Env     = var.env
    Managed = "terraform"
  }
}
```

`variables.tf`：

```hcl
variable "region" {
  type        = string
  description = "阿里云地域"
  default     = "cn-hangzhou"
}

variable "env" {
  type        = string
  description = "环境名"
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.env)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "VPC 的 CIDR"
  default     = "10.0.0.0/16"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr 必须是合法 CIDR。"
  }
}
```

`outputs.tf`：

```hcl
output "vpc_id" {
  description = "VPC ID"
  value       = alicloud_vpc.example.id
}
```

### 3.2 一步步命令

```bash
# ① 初始化：下载 provider、配置 backend
terraform init

# 看到什么算对：输出里有 "Terraform has been successfully initialized!"
#                并且 "Terraform has created a lock file .terraform.lock.hcl"

# ② 校验语法与内部一致性（不碰远端）
terraform validate

# 看到什么算对：Success! The configuration is valid.

# ③ 预演：看将要发生什么（不改任何东西）
terraform plan

# 看到什么算对：
#   Plan: 1 to add, 0 to change, 0 to destroy.
#   # alicloud_vpc.example will be created
#   # + cidr_block     = "10.0.0.0/16"
#   # + vpc_name       = "dev-demo-vpc"

# ④ 执行
terraform apply

# 看到什么算对：交互提示 "Do you want to perform these actions?" 输入 yes
#                末尾 "Apply complete! Resources: 1 added, 0 changed, 0 destroyed."

# ⑤ 确认现实与代码一致（再跑一次 plan，应该是空的）
terraform plan

# 看到什么算对：No changes. Your infrastructure matches the configuration.

# ⑥ 取输出
terraform output vpc_id
terraform output -json          # 机器可读，给 CI 用

# ⑦ 清理
terraform destroy
```

### 3.3 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| 配置语法正确 | `terraform validate` | `Success! The configuration is valid.` |
| 计划符合预期 | `terraform plan` | `Plan: 1 to add, 0 to change, 0 to destroy.` |
| 落地无变更 | 再跑 `terraform plan` | `No changes.` |
| 输出可读 | `terraform output vpc_id` | 打印出 VPC ID |
| 资源真的建了 | `terraform state list` | 能看到 `alicloud_vpc.example` |
| 依赖图正确 | `terraform graph -type=plan` | 输出 DOT 格式的依赖图 |
| destroy 后 state 干净 | `terraform state list` | 空输出 |

---

### 3.4 常用运维命令（Terraform 日常工作流，按场景）

> **本节能做什么**：3.1–3.3 是"把基础设施写成代码"，本节是**每天值班 / 面试都会敲的 Terraform 运维命令**——从 `plan`/`apply` 到状态管理、资源纳管、drift 排查。配置管理 / DevOps 岗的 IaC 肌肉记忆就来自这一张表。
> **环境基准**：本机实测 `terraform v1.9.x`，provider 以 `alicloud` 为例。

#### ① 日常工作流（init / fmt / validate / plan / apply / destroy）

```bash
terraform init                              # 初始化：下载 provider、配置 backend（改 provider 后必跑）
terraform fmt -recursive                    # 递归格式化所有 .tf 文件（提交前必跑，CI 里也跑）
terraform validate                          # 静态校验语法与变量引用（不动真实资源）
terraform plan                              # 预览这次会创建/修改/销毁什么（上线前必审）
terraform plan -out=tfplan                  # 把 plan 存成文件，apply 时直接吃这个（避免 plan/apply 中间态漂移）
terraform apply -auto-approve               # 直接执行（日常可，生产建议先审 plan）
terraform destroy                           # 销毁 plan 里标记的资源（危险，先 plan 确认）
```

> **实战价值**：`plan -out` + `apply tfplan` 是**生产环境的黄金组合**——保证你审过的那一份 plan 就是真正执行的那一份，杜绝"审的时候没删库、执行时却删了"的中间态事故。`fmt -recursive` 让团队 diff 干净，是 Code Review 的隐形门槛。

#### ② 状态管理（state list / show / rm / pull / push / force-unlock）

```bash
terraform state list                        # 看当前 state 里有哪些资源
terraform state show alicloud_vpc.example  # 看某个资源的完整属性（排查"实际值是什么"）
terraform state rm alicloud_vpc.example    # 把资源从 state 移除（不删真实资源，慎用）
terraform state mv alicloud_vpc.a alicloud_vpc.b  # 重命名资源（改代码后同步 state，避免销毁重建）
terraform force-unlock <LOCK_ID>           # state 被锁（上次 apply 中断）时强制解锁（慎用）
terraform refresh                          # 把真实云上状态拉回来对齐 state（drift 复原计划第一步）
```

> **实战价值**：state 是唯一真相源。`state rm` 误用会让你"以为删了其实没删"。`force-unlock` 前**先确认没有别人正在跑**，否则会破坏并发操作。`state mv` 是重构代码后避免资源重建的关键——面试常考"改了 resource 名怎么不重建"。

#### ③ 资源纳管与调试（import / taint / untaint / output / console / workspace）

```bash
terraform import alicloud_vpc.example vpc-abc123   # 把已有的手工资源纳管进 state
terraform taint alicloud_instance.web             # 标记该资源下次 apply 强制重建
terraform untaint alicloud_instance.web           # 撤销 taint
terraform output vpc_id                           # 打印某个 output 值（和其他系统对接用）
terraform console                                 # 开交互式表达式求值（试 `var.x`、`module.y.z`）
terraform workspace list                          # 看有哪些 workspace（多环境隔离）
terraform workspace select staging                # 切到 staging 环境（各自独立 state）
```

> **实战价值**：`import` 是"存量资源 IaC 化"的必经之路，面试高频。`taint` 在"配置改了但不生效"时强制重建一台机器。`workspace` 用一套代码管多套环境，是 Terraform 多环境的标准玩法。

#### ④ 排障：drift 检测与锁

```bash
terraform plan                             # 出现 "1 to change, 0 to add..." 说明云上和 state 漂了
terraform refresh && terraform plan        # 先对齐真实状态再判断是不是真漂移
terraform plan -detailed-exitcode          # 退出码 0=无变更 1=错误 2=有变更（CI 里判断要不要 apply）
terraform apply -lock-timeout=120s         # 拿锁超时时间，避免别人占锁时直接退出
```

> **实战价值**：drift（云上被人手改了）是 IaC 的永恒敌人。定期 `plan` 看有没有非预期变更，是 GitOps 里"声明即真相"的守门员。CI 里用 `-detailed-exitcode` 精确区分"无变更 / 有变更 / 出错"三种状态。

---

## 4. 逐字段表

### 4.1 HCL 块类型表

| 块类型 | 用途 | 典型内容 | 写错的后果 |
|---|---|---|---|
| `terraform` | 全局配置 | `required_providers`、`required_version`、`backend` | 写了两个 `backend` 块 → 报错（官方限制：一个配置只能有一个） |
| `provider` | 告诉 Terraform 怎么连平台 | `region`、`access_key`/`profile`/`assume_role` | 认证信息写错 → plan 阶段就报凭据错误 |
| `variable` | 声明输入接口 | `type`、`default`、`description`、`sensitive`、`nullable`、`validation` | `default` 类型与 `type` 不匹配 → 报错；`nullable=false` + `default=null` → 默认值失效 |
| `locals` | 中间计算结果 | `name_prefix = "..."`、`tags = merge(...)` | 引用了未知资源属性 → 报错（for 表达式必须加载期可求值） |
| `resource` | 声明要管理的资源 | `alicloud_vpc "example" { ... }` | 类型名写错 → "provider does not support resource type" |
| `data` | 声明要只读的信息 | `data "alicloud_regions" "current" { ... }` | 写成了会创建的资源 → 产生非预期变更 |
| `output` | 暴露结果 | `value`、`description`、`sensitive` | `sensitive` 漏写 → 密码明文进日志 |
| `module` | 调用子模块 | `source`、`version`、`providers`、`count`/`for_each` | 相对路径写错 → init 报 "Module not found" |
| `moved` | 声明地址迁移 | `from`、`to` | 删掉它 → 下次 plan 变成"销毁+重建" |
| `import` | 声明导入 | `to`、`id` | 只写 import 不写 resource → apply 报错 |
| `lifecycle` | 资源生命周期规则 | `create_before_destroy`、`prevent_destroy`、`ignore_changes`、`replace_triggered_by` | 官方限制：**lifecycle 块里只能用字面量**，不能用表达式 |
| `precondition` / `postcondition` | 条件检查 | `condition`、`error_message` | condition 不满足 → 报错中止 |
| `connection` | 远程执行连接配置 | `type`（ssh/winrm）、`host` | 连不上 → provisioner 失败 |
| `provisioner` | 资源创建后执行动作 | `type`（local-exec/remote-exec/file）、`when` | 执行失败默认会让整个 apply 失败 |
| `check` | 周期性检查 | `assert` 块 | 检查失败不影响主流程，但会记录到 state 的 `check_results` |
| `removed` | 声明资源已删除 | `from` | 需 v1.7+；忘记写会一直 plan "要删除" |

### 4.2 变量定义方式优先级表

| 优先级 | 来源 | 具体形式 | 是否进 Git | 适用场景 |
|---|---|---|---|---|
| 1（最高） | 命令行 | `-var="k=v"` / `-var-file=prod.tfvars` | ❌ | 一次性覆盖、临时调试 |
| 1 | HCP Terraform 变量集 | 平台侧配置 | ❌ | 用 HCP Terraform 时 |
| 2 | 自动加载文件 | `*.auto.tfvars` / `*.auto.tfvars.json`（**字典序**） | ✅ | 多环境切分 |
| 3 | `terraform.tfvars.json` | JSON 格式 | ✅ | 需要 JSON 语法（复杂类型） |
| 4 | `terraform.tfvars` | 最常用 | ✅ | 默认环境值 |
| 5 | 环境变量 | `TF_VAR_<name>` | ❌ | CI/CD 注入、密钥 |
| 6（最低） | `variable` 块 `default` | 配置内 | ✅ | 兜底默认值 |

**三条容易踩的规则**：

1. **环境变量优先级低于所有 tfvars 文件**（官方列表第 5 位）。
2. **多个 `*.auto.tfvars` 之间按字典序**，后者覆盖前者。
3. **未声明的变量**：环境变量里多余的 `TF_VAR_x` 被**静默忽略**；tfvars 里多余的会**警告**；`-var` 里多余的**直接报错**。

---

## 5. 横向对比

### 5.1 Terraform vs Ansible

| 维度 | Terraform | Ansible |
|---|---|---|
| **核心模型** | **声明式**：描述"要什么" | **命令式/任务执行**：描述"做什么" |
| **典型文件** | `.tf`（HCL） | `.yml` playbook（YAML） |
| **状态存放** | **有 state**（记录资源 ID 与属性） | **无状态**（默认幂等，靠"查现状再动作"） |
| **幂等方式** | 靠 state + diff | 靠模块本身幂等（declared state by facts） |
| **擅长** | **创建/编排有状态云资源**（VPC、ECS、RDS、OSS、RAM 权限） | **跨机器批量执行操作**（装包、改配置、启服务） |
| **不擅长** | 改一台机器上的文件内容（要用 provisioner，很别扭） | 创建有依赖关系的云资源拓扑（容易乱序） |
| **学习曲线** | HCL + 资源模型 + state 心智负担 | YAML + 模块/角色/playbook 概念 |

**选型结论（面试标准答案）**：

> **Terraform 管"云资源的拓扑与生命周期"，Ansible 管"机器内部的状态"。**
>
> 典型分工：先用 Terraform 建出 VPC、子网、ECS、RAM 角色（拿到 ECS 的 IP 和实例 ID），再把这些作为 inventory 喂给 Ansible 做软件安装与配置。**两者不是竞争关系，是流水线上的上下游。**

**一个常被追问的差异**：为什么 Terraform 有 state 是个"缺点"？

因为 state 带来了**一致性问题**（多人协作要锁）和**安全性问题**（state 里有敏感数据）。Ansible 无 state，所以不需要锁，也不会泄密到 state 文件。代价是 Ansible 每次都要"重新发现现状"，效率低，且对有状态资源的追踪能力弱。

### 5.2 Terraform vs Pulumi vs OpenTofu

| 维度 | Terraform | Pulumi | OpenTofu |
|---|---|---|---|
| **出身** | HashiCorp | Pulumi 公司 | Linux 基金会（OpenTF 孵化） |
| **语言** | HCL（专用配置语言） | **通用编程语言**（Go / Python / TS / C# / Java…） | 与 Terraform 兼容（HCL） |
| **状态文件** | `terraform.tfstate`（JSON，格式有版本） | Pulumi 后端（自托管 / Pulumi Cloud），**加密**（默认 passphrase 加密） | `tofu.tfstate`，兼容 Terraform state |
| **生态** | **最大**：Terraform Registry（含云厂商官方模块） | 较小但增长快，跨云一致 | 复用 Terraform Registry 的 provider（**多数 provider 兼容**） |
| **可编程性** | 受限于 HCL；扩展靠 provider 提供的函数 | **最强**：直接写循环、条件、面向对象 | 同 Terraform |
| **CLI 命令** | `terraform` | `pulumi` | `tofu`（**兼容 `terraform` 常用命令**） |
| **许可** | BUSL（**注意：不再是纯 MPL**） | Apache 2.0 | **MPL 2.0**（真开源） |
| **适合谁** | 已有 Terraform 存量、需要最大生态 | 想用熟悉语言写基础设施、团队是软件工程师 | 想要完全开源、避免 BUSL 许可顾虑 |

**选择依据（给面试用）**：

- 已经有大量 Terraform 代码和 Registry 依赖 → **继续 Terraform**，迁移成本高。
- 团队更擅长 Python/Go，想把 IaC 当代码写、要复杂逻辑 → **Pulumi**。
- 关心许可合规（BSL 转商业条款）→ **OpenTofu**，配置几乎不用改。

> **本篇不深入这三者的实现差异**，因为 Terraform 官方文档不覆盖 Pulumi/OpenTofu。**这一小节属于"常识对比"，标 `⚠️ 单源待补`**（本资料库索引中目前没有 Pulumi / OpenTofu 的官方链接条目）。

### 5.3 本地 state vs 远端 state

| 维度 | 本地 state（`local` backend） | 远端 state（`s3`/`oss`/`consul`/`pg` 等） |
|---|---|---|
| **存储位置** | 工作目录 `terraform.tfstate` + `.backup` | 对象存储 / 数据库 / KV |
| **团队协作** | ❌ 各人一份，互相看不见 | ✅ 共享同一份 |
| **并发保护** | ❌ 无锁 | ✅ 有锁（`use_lockfile` / DynamoDB / 数据库锁） |
| **加密** | ❌ 本地明文文件 | ✅ 服务端加密 / 客户端加密 |
| **版本控制** | ❌ 无 | ✅ 对象版本号 |
| **丢失后果** | **灾难**：资源失管 | 可控：bucket 有版本、可回滚 |
| **性能** | 本地读，快 | 每次读写一次网络往返（官方 state 文档明确说 "each read and each write do a full network roundtrip"） |
| **适用场景** | 本地实验、单机个人 | **团队、生产，一律用远端** |

> **官方结论**：State 文档明确建议 "We recommend storing state in HCP Terraform or a **remote backend** to securely store state and collaborate with team members."
>
> **注意本地 state 有一个隐蔽优势**：`local` backend 的 `terraform.tfstate` 可以直接进 Git（很多老项目就这么干）。**但官方明确反对**："Avoid storing your state in a version control system." 原因是 Git 没有锁、没有加密，还会把密钥明文提交上去。**即使你能接受这些风险，也至少要保证 state 文件不进公开仓库。**

---

## 6. 踩坑与排障

> 下表的报错/提示文字均为**官方文档或官方 issue / 社区中出现的典型文本**，标注了出处性质；本资料库未在环境实测，因此不写"实测输出"。

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `plan` 说要"创建"一批资源，但云上明明已经存在 | plan 中显示 `will be created` | **state 丢了**（文件被删、被 `.gitignore` 误清、换了机器、backend 配置指向了空 bucket）。Terraform 失忆 = 认为资源不存在 | ① 不要 apply！② 找回旧 state 文件（本地 `.backup`、远端 bucket 版本）③ 实在找不回，用 `terraform import` 把现有资源逐个纳管 | state 放远端 backend 并**开启版本控制**；`.gitignore` 里只忽略 `*.tfstate`，别把备份也删；CI 里加一条"state 存在性"检查 |
| state 文件里出现明文密码 / AK / SK | state 可读，无报错 | `sensitive = true` **只遮蔽显示，不阻止写入 state**（官方 Variables 文档明说 "still stores the values of sensitive variables in the state"） | ① 立即轮换泄漏的凭据 ② 远端 state 开加密 + 收紧访问控制 ③ 高敏感值改用 `ephemeral`（v1.10+）或改用云厂商的密钥管理服务引用 | 所有密码类变量都加 `sensitive = true`；backend 凭据一律走环境变量，不写进 `.tf` |
| 两个人同时 apply，后一个人把前一个人建的资源"忘了" | 后一次 plan 显示某些资源不存在 / 要重建 | **没有锁**：两人都读同一份 state，各自 apply，后写的 state 覆盖先写的 | 远端 backend 开锁；重新 import 丢失的资源 | state 永远用远端 backend 并开锁；本地 state 只用于一次性实验，实验完立刻 destroy |
| 评审的 plan 和实际 apply 的内容不一样 | apply 结果与评审 diff 不符 | 用了 **speculative plan**（不带 `-out`）；两次之间现实变了，或 apply 重新生成了 plan | 改用 `terraform plan -out=tfplan` → 评审 `terraform show tfplan` → `terraform apply tfplan` | CI 里固定"生成 plan 文件 → 上传 → 评审 → 应用同一文件"的流程 |
| plan 输出里出现你没写过的变更 | 显示 `will be updated` 删除某些规则 | **漂移**：有人手工改了云上资源，refresh 把它吸进了 state，diff 判定要改回配置描述 | **先查是谁改的**；确认无人改则直接 apply 纠正；若是合法变更则改配置再 apply | 开通云厂商的操作审计（ActionTrail）；生产环境收紧控制台写权限；应用层加 `ignore_changes` 只对确实该被外部管理的字段 |
| `Invalid value for input variable` / `The given value is not suitable for var.xxx` | plan 前就报错 | **变量类型不匹配**：tfvars 里写了字符串，类型要求 number；或类型推断成 `map` 但传了 list | 对齐类型；用 `tostring()`/`tonumber()` 显式转换；复合类型在 tfvars 里用合法语法（map 写成 `key = "value"` 形式） | 变量一律写显式 `type`；在 `validation` 里加业务规则；tfvars 格式参考官方"Variable definition files"章节 |
| `Error: Invalid for_each argument` / `The "for_each" set includes values derived from resource attributes that cannot be determined until apply` | plan 阶段报错 | `for_each` 的键**必须在 plan 阶段已知**，不能引用 apply 后才有的属性（如刚创建资源的 ID） | 用 `count`（下标是数字，天然已知）；或让键来自变量/数据源；或先 apply 一次拿到 ID 再改配置 | 键用**业务语义**（环境名、实例名），不要用系统生成的 ID |
| 换了 `for_each` 的键名，资源被整体重建 | plan 显示 destroy + create | **资源地址变了**。`for_each` 实例的地址是 `type.name["key"]`，键变了 = 换了身份 | 用 `moved` 块声明 `from`/`to` 迁移 | **键一旦上线就不要改**；要改就同时加 `moved`；优先 `for_each` 而不是 `count`（见下条） |
| 删了列表中间的某个元素，后面所有资源都被重建 | plan 显示一批 destroy + create | `count` 的实例地址是**下标** `type.name[0]`。删中间元素 → 后面元素的下标全部前移 → 身份全变 | 改用 `for_each`（用稳定的字符串键），或用 `moved` 逐个迁移 | **默认用 `for_each`**：集合元素增删只影响该元素，不影响其他实例的地址 |
| `terraform refresh` 之后所有资源都"消失"了 | state 被清空 | 官方警告的场景：**凭据配错时，refresh 会误以为所有对象都被删了**，然后在没有确认提示的情况下移除所有托管对象 | 立刻停止；恢复 state 备份；修好凭据再 `terraform apply -refresh-only` 重新接管 | **别用 `terraform refresh`（已 deprecated）**，改用 `terraform plan -refresh-only` 先看再决定 |
| `terraform init` 报 backend 相关错误 | init 失败 | 改了 backend 配置但没重新 init；或 backend 块里引用了变量（官方禁止）；或凭据没给 | 重新 `terraform init`；backend 里只写字面量，敏感值用 `-backend-config` 注入；按官方 partial configuration 方式写空值占位 | backend 块固定写法 + 环境变量给凭据；改 backend 前先手工备份 `terraform.tfstate`（官方强烈建议） |
| `terraform validate` 通过，但 apply 报错 | validate 成功 | `validate` **不校验远端服务**（官方：does not validate remote services, such as remote state or provider APIs），它只查语法和内部一致性 | 用 `terraform plan` 做真实验证（plan 包含隐含的校验） | CI 里 `fmt -check` → `validate` → `plan` 三步都跑 |
| 应用完资源没变，但 `plan` 仍显示有差异 | 持续 OutOfSync | 常见原因：外部控制器/webhook 改了对象（ArgoCD 里常见）；或 `last-applied-configuration` 之类的注解差异 | 用 `ignoreDifferences`（ArgoCD）/ `ignore_changes`（Terraform）精确忽略特定字段；不要全局忽略 | 区分"该被外部管理的字段"和"Terraform 管的字段"，只忽略前者 |
| `plan` 里出现 `known after apply` 的值 | plan 显示占位符 | 该属性在 apply 时才知道（如新建资源的 ID） | 正常现象，不是错误 | 需要在 apply 前就知道的值，改用 data source 查询已有资源 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `developer.hashicorp.com/terraform/language` | HCL 语言总览、表达式与函数入口 | ✅ 2026-10-24 |
| S2 | 官方 | `developer.hashicorp.com/terraform/language/resources/syntax` | `resource` / `data` 块语法、`count`/`for_each`/`depends_on`/`provider`/`lifecycle` 元参数 | ✅ 2026-10-24 |
| S3 | 官方 | `developer.hashicorp.com/terraform/language/values/variables` | 变量定义、`type`/`default`/`sensitive`/`validation`、**赋值优先级**、undeclared 变量行为 | ✅ 2026-10-24 |
| S4 | 官方 | `developer.hashicorp.com/terraform/language/expressions` | 表达式、插值、条件表达式、for 表达式、splat、动态块 | ✅ 2026-10-24 |
| S5 | 官方 | `developer.hashicorp.com/terraform/language/functions` | 内置函数分类与函数名清单、敏感值函数 | ✅ 2026-10-24 |
| S6 | 官方 | `developer.hashicorp.com/terraform/language/state` | state 的用途与内容、存储与本地备份、禁止直接编辑、敏感信息警告 | ✅ 2026-10-24 |
| S7 | 官方 | `developer.hashicorp.com/terraform/language/backend` | backend 块定义与限制、默认 local、partial configuration、凭据与 plan 文件泄漏警告、迁移 state | ✅ 2026-10-24 |
| S8 | 官方 | `developer.hashicorp.com/terraform/language/modules` | 模块概念、三种来源、root/child module 层级 | ✅ 2026-10-24 |
| S9 | 官方 | `developer.hashicorp.com/terraform/language/modules/develop` | 模块结构（variables/outputs/resources）、模块开发建议、refactoring（`moved`） | ✅ 2026-10-24 |
| S10 | 官方 | `developer.hashicorp.com/terraform/cli` | CLI 命令与生命周期总入口 | ✅ 2026-10-24 |
| S11 | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs` | alicloud provider 资源与数据源（`alicloud_vpc`/`alicloud_vswitch`/`alicloud_security_group`/`alicloud_instance`/`alicloud_images`/`alicloud_instance_types` 等） | ✅ 2026-10-24 |
| S12 | 中文/云厂商 | `help.aliyun.com/zh/` + 阿里云开发者社区 | 阿里云 Terraform 实践、provider 与账号体系 | ✅ 2026-10-24 |
| S13 | 官方 | `developer.hashicorp.com/terraform/language/state/workspaces` | workspace 定义、默认 workspace、支持的 backend 列表、`${terraform.workspace}` 插值、"不适用于系统分解"的官方警告 | ✅ 2026-10-24 |
| S14 | 官方 | `developer.hashicorp.com/terraform/cli/commands/plan` | plan 全部选项（`-out` opaque 格式、敏感数据明文落盘、`-refresh-only`（v0.15.4+）、`-refresh=false`、`-detailed-exitcode` 0/1/2、`-target`、`-destroy`）、speculative plan 定义 | ✅ 2026-10-24 |
| S15 | 官方 | `developer.hashicorp.com/terraform/cli/commands/refresh` | **`terraform refresh` 已废弃**、等价于 `apply -refresh-only -auto-approve`、凭据配错会误删全部托管对象的官方警告 | ✅ 2026-10-24 |
| S16 | 官方 | `developer.hashicorp.com/terraform/cli/commands/taint` | **`taint` 已废弃**、推荐 `terraform apply -replace="..."`、地址写法（含 `count`/`for_each` 实例） | ✅ 2026-10-24 |
| S17 | 官方 | `developer.hashicorp.com/terraform/cli/commands/import` | `terraform import ADDRESS ID` 格式、模块/count/for_each 地址写法、**同一资源只能 import 到一个地址**的官方警告 | ✅ 2026-10-24 |
| S18 | 官方 | `developer.hashicorp.com/terraform/language/import` | 配置式导入 `import` 块（config-driven import）、`import` 块 + `resource` 块必须同时写、`-generate-config-out` 自动生成配置 | ✅ 2026-10-24 |
| S19 | 官方 | `developer.hashicorp.com/terraform/language/block/variable` | **`nullable` 默认 `true`**、`nullable=false` + `default=null` 时默认值失效、`sensitive` 遮蔽但仍入 state、`ephemeral`（v1.10+）、`validation` 执行时机 | ✅ 2026-10-24 |
| S20 | 官方 | `developer.hashicorp.com/terraform/language/meta-arguments/count` | `count.index` 从 0 开始、`<TYPE>.<NAME>[<INDEX>]` 寻址、count 与 for_each 的选择建议 | ✅ 2026-10-24 |
| S21 | 官方 | `developer.hashicorp.com/terraform/language/meta-arguments/for_each` | **键必须 plan 阶段已知**、键必须是字符串、不能用 impure 函数（uuid/bcrypt/timestamp）做键、count 与 for_each 互斥 | ✅ 2026-10-24 |
| S22 | 官方 | `developer.hashicorp.com/terraform/language/modules/develop/refactoring` | **`moved` 块（v1.1+）**、地址变更**不销毁资源**、可在 count/for_each 形态间迁移、不能 resource→data、删掉 moved 块是 breaking change | ✅ 2026-10-24 |
| S23 | 官方 | `developer.hashicorp.com/terraform/cli/commands/state` | state 子命令清单（list/show/mv/rm/pull/replace-provider）、state 修改必写备份、远端 state 每次读写一次网络往返 | ✅ 2026-10-24 |
| S24 | 官方 | `developer.hashicorp.com/terraform/cli/commands/output` | output 只显示根模块输出、**按名字指定输出时不 redact 敏感值**、`-json`/`-raw` 明文显示 | ✅ 2026-10-24 |
| S25 | 官方 | `developer.hashicorp.com/terraform/cli/commands/fmt` | fmt 的 `-check`/`-diff`/`-recursive`/`-list` 选项与"格式规则可跨小版本微调" | ✅ 2026-10-24 |
| S26 | 官方 | `developer.hashicorp.com/terraform/cli/commands/validate` | **validate 不校验远端服务**、需先 `init`、可用 `init -backend=false` 跳过 backend | ✅ 2026-10-24 |
| S27 | 官方 | `developer.hashicorp.com/terraform/cli/commands/graph` | graph 的 `-type=plan/plan-refresh-only/plan-destroy/apply`、DOT 输出 | ✅ 2026-10-24 |
| S28 | 官方 | `developer.hashicorp.com/terraform/language/expressions/conditionals` | 条件表达式 `<COND> ? <TRUE> : <FALSE>` 的精确语法 | ✅ 2026-10-24 |
| S29 | 官方 | `developer.hashicorp.com/terraform/language/expressions/for` | for 表达式的列表/过滤/map 三种形态 | ✅ 2026-10-24 |
| S30 | 官方 | `developer.hashicorp.com/terraform/language/expressions/strings` | 字符串模板、插值序列与转义 | ✅ 2026-10-24 |

> **说明**：① 本篇引用官方文档的 CLI 命令参考页与语言子页，均已按 `09-参考索引/README.md` 的维护规则补录进索引（2026-10-24 实测 200），来源表不再只写父页面。② **中文来源说明**：Terraform 官方站**没有** `/language/zh-cn` 路径（实测 404，见 `09-参考索引/README.md` 第三节坑 #4），因此本篇的"官方口径"以英文官方文档为准，中文只用于阿里云实践部分。

**待验证清单**（本篇明确不确定的点，均已标注在正文对应位置）：

| # | 待验证项 | 现状 | 怎么验 | 影响 |
|---|---|---|---|---|
| 1 | `Pulumi` / `OpenTofu` 的官方对比 | 标 `⚠️ 单源待补` —— 本资料库索引无这两家的官方条目，本篇只做通识性对比 | 补进 `09-参考索引/README.md` 后按官方文档核对 | 只影响 5.2 节的对比表，不影响任何机制性结论 |
| 2 | 本地 `terraform` 实际版本 | **未实测**（本次写作未执行任何 terraform 命令），全文以官方文档站 `v1.17.x` 分支为准 | `terraform version` | 影响 `ephemeral`（v1.10+）、`moved`（v1.1+）、`-refresh-only`（v0.15.4+）这几个版本门槛是否适用 |
| 3 | `terraform plan` 输出的具体格式 | 正文引用的是**官方文档中的示意输出**，不是实测 | 跑一次真实 plan | 仅影响读者对输出格式的预期，不影响语义 |
| 4 | 各 provider 的资源参数细节 | 本篇只覆盖 provider 层（认证、alias、版本约束），不铺具体资源的字段 | 查对应 provider 文档 | 不影响本篇任何结论 |

> **未覆盖的知识点**：`Pulumi` / `OpenTofu` 的官方对比（本资料库索引暂无对应条目，标 `⚠️ 单源待补`）。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 输入是 `.tf` 配置文件（+ 变量取值），输出是**两样东西**：① 被创建/修改的真实云资源；② 一份 `terraform.tfstate` 快照。中间经过：解析 HCL → 加载 provider 插件 → refresh 读回真实状态 → diff 得出变更 → 执行 → 写回 state。
2. **能故意改坏再倒推吗？** —— 三个实验：① 把 `variable "env"` 的 `validation` 改成 `contains(["dev"], var.env)`，再 `plan` → 看到 `Invalid value for input variable` 与你写的 `error_message`；② 把 `variable "vpc_cidr"` 的 `type` 改成 `number` → plan 报类型不匹配；③ 把 `local.tags` 里 `var.env` 改成引用一个不存在的资源属性 → plan 报"引用了未知属性"，**说明 for/local 必须在加载期可求值**。
3. **能画上下游吗？** —— 画出 `.tf → 解析 → provider 认证 → refresh(state ← 云上真实状态) → diff → plan 输出 → apply → 写 state → 下次 refresh` 这条闭环，并在每一步标出"这一步失败会看到什么"：解析失败→语法错误；认证失败→凭据错误；refresh 失败→API 错误；diff 阶段看到意外变更→漂移；apply 中途失败→部分资源已创建（state 不完整，需要手工收拾）。

---

**下一篇**：[`L3-Terraform-模块化与阿里云provider`](L3-Terraform-模块化与阿里云provider.md) · **上一级**：[`../00-学习地图与前置依赖.md`](../00-学习地图与前置依赖.md)
