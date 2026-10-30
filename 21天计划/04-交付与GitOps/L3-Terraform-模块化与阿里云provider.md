# L3 · Terraform 模块化与阿里云 provider（会写单资源 → 能建一整套架构）

> **本篇定位**：L2 讲了 Terraform 是什么、state 怎么管。本篇讲**怎么把它用在真实项目里**：把一套云资源封装成可复用模块，并用阿里云 provider 落地。
> **前置**：[`L2-Terraform-核心概念与生命周期`](L2-Terraform-核心概念与生命周期.md)（provider/resource/data source、HCL 语法、变量优先级、state 与 backend、生命周期命令）。
> **学完能做什么**：说清模块化解决什么问题；独立写一个含 variables/outputs 的网络模块并被根模块调用；用 `provider` + `alias` 管多账号多地域；把已在手工建的资源用 `terraform import` 纳管；给阿里云 provider 配好最小权限的 AK。
> **在 21 天里**：D9（Terraform 模块化）。D8 建立的 alicloud provider 认知在这里扩展成"模块 + 多账号 + 导入"的完整姿势。
> **环境/版本基准**：命令与字段语义以 **HashiCorp 官方文档站（developer.hashicorp.com/terraform）** 与 **`registry.terraform.io/providers/aliyun/alicloud/latest/docs`** 为准。alicloud provider 资源参数以官方 provider 文档 v1.188.0 分支的 `website/docs/r/*.html.markdown` 为准（该 provider 官方仓库文档与 registry 页同源）。**本次写作未执行任何 terraform 命令、未创建任何云资源**，输出片段均为官方文档示意。
> **配套方法**：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md) 的三态模型 · **来源规范**：[`../09-参考索引/README.md`](../09-参考索引/README.md)

---

## 0. 知识图谱

```
Terraform 模块化与阿里云 provider
│
├─ 1. 概念
│   ├─ 1.1 模块化解决什么问题（复用 / 标准化 / 团队约定）
│   ├─ 1.2 模块 vs 脚本 vs 单文件配置
│   └─ 1.3 术语表（中英对照）
│
├─ 2. 机制
│   ├─ 2.1 模块的三种调用方式（本地 / Registry / Git）
│   ├─ 2.2 模块目录约定
│   ├─ 2.3 子模块的 source / 变量 / 输出传递
│   ├─ 2.4 count / for_each / depends_on 在模块中的用法
│   ├─ 2.5 provider 配置与 alias（多账号多区域）
│   ├─ 2.6 alicloud provider 实战（认证方式 / 常用资源）
│   ├─ 2.7 资源导入实操（import 与 config-driven import）
│   ├─ 2.8 RAM 权限与最小权限
│   ├─ 2.9 敏感输出的处理
│   ├─ 2.10 模块版本与 Registry 发布
│   └─ 2.11 工作区与模块的组合
│
├─ 3. 动手：一个可复用的网络模块
│   ├─ 3.1 目录结构
│   ├─ 3.2 逐步命令
│   └─ 3.3 验证清单
│
├─ 4. 逐字段表
│   ├─ 4.1 provider 配置项表（alicloud）
│   └─ 4.2 模块调用参数表
│
├─ 5. 横向对比
│   ├─ 5.1 本地模块 vs Registry 模块
│   ├─ 5.2 count vs for_each
│   ├─ 5.3 AK/SK 环境变量 vs profile vs assume_role
│   └─ 5.4 Terraform 管云资源 vs Terraform 管 K8s（职责边界）
│
├─ 6. 踩坑与排障（症状 → 状态 → 原因）
│
├─ 7. 参考来源
│
└─ 8. 自测三问
```

---

## 1. 概念

### 1.1 模块化解决什么问题

先说结论：**模块化解决三件事 —— 复用、标准化、团队约定。** 少一件，模块化就白做。

| 要解决的问题 | 没有模块时是什么样 | 模块化之后 |
|---|---|---|
| **复用** | 三个环境各写一份 VPC 配置，改一处要改三处，改漏一处就出故障 | 一份模块，三处调用；改模块 = 改三处 |
| **标准化** | A 同学建的 VPC 有 3 个交换机，B 同学建的只有 1 个，命名也乱七八糟 | 模块的输入输出是**契约**：调用方只能给这几项参数，产出必然一致 |
| **团队约定** | "我们的 VPC 网段怎么规划？交换机怎么命名？" —— 每次都要问人 | 约定写进模块的 `variables.tf` 和 `validation`，**代码即文档** |

再看官方 Modules 文档的原话，它把模块化的动机说得很直接：

> "When you repeatedly provision collections of resources with similar configuration, such as networking resources for new development environments, you should write reusable modules to codify them. Modularizing and sharing configurations helps you **standardize how you provision infrastructure** and lets you **quickly and predictably provision** the resources you need."

官方 Developing Modules 页还给了模块该有的样子：

> "Modules are reusable Terraform configurations that you can call and configure from other configurations... A good module should **raise the level of abstraction** by describing a new concept in your architecture that is constructed from resource types offered by providers."

**"提升抽象层级"这句话是模块化的判定标准。** 举例：

| 写法 | 抽象层级 | 评价 |
|---|---|---|
| 模块里直接写 `alicloud_vpc` | 和写脚本没区别 | ❌ 没有抽象，只是把代码挪了个目录 |
| 模块叫 `network`，对外只暴露 `vpc_cidr` / `subnet_cidrs` / `vpc_id` | 描述"一张网络"这个概念 | ✅ 好的模块 |

**而且官方明确说了什么是"坏的模块"**：

> "**We do not recommend writing modules that are just thin wrappers around single other resource types.** If you have trouble finding a name for your module that isn't the same as the main resource type inside it, that may be a sign that your module is not creating any new abstraction and so the module is adding unnecessary complexity. **Just use the resource type directly in the calling module instead.**"

这条对面试很有用 —— **不是所有东西都该做成模块**。一个模块只包一个 `alicloud_vpc` 是负优化。

### 1.2 模块 vs 脚本 vs 单文件配置

| 方案 | 优点 | 缺点 | 适合 |
|---|---|---|---|
| **一个巨大的 `.tf` 文件** | 简单直观 | 没人能维护；改一处要读全文；无法复用 | 只有 1-2 个资源的小实验 |
| **shell 脚本调 API** | 灵活 | **不可复现**（脚本里有隐藏状态）、**不可审计**、错误处理靠自己写；API 一改脚本就废 | 一次性应急操作 |
| **Terraform 模块** | 可复用、可版本化、可评审、有状态管理 | **有学习成本**；state 需要远端管理 | **正式基础设施** |

> **关键差别不是"代码量"，是"是否有状态与契约"**。脚本不会记得"我建过什么"，模块会；脚本的参数靠约定，模块的参数是**类型化的输入接口**。

### 1.3 术语表（中英对照）

| 英文 | 中文 | 一句话 |
|---|---|---|
| Module | 模块 | 可复用的资源集合，有输入（variable）和输出（output） |
| Root Module | 根模块 | 顶层配置，`terraform plan` 在这里跑 |
| Child Module | 子模块 | 被 `module` 块调用的模块 |
| Source | 来源 | 模块代码从哪来（本地路径 / Registry / Git） |
| Version Constraint | 版本约束 | `"~> 1.2"`、`">= 1.0, < 2.0"` 等 |
| Alias | 别名 | 同一个 provider 的多份配置（多账号/多地域） |
| `providers` meta-argument | 提供者映射 | 把父模块的 provider 配置传给子模块 |
| Provider Configuration | 提供者配置 | `provider "alicloud" { ... }` 块 |
| Profile | 配置档 | 阿里云 CLI 共享凭据文件里的一个命名配置 |
| Assume Role | 角色扮演 | 用一个身份的凭据去换取另一个角色的临时凭据 |
| STS | 临时访问凭证服务 | 阿里云的 Security Token Service |
| Import | 导入 | 把已有资源纳入 Terraform 管理 |
| Registry | 模块注册表 | `registry.terraform.io`，公共/私有模块仓库 |
| Namespace | 命名空间 | Registry 里的组织标识（`alibaba/vpc/alicloud`） |

---

## 2. 机制

### 2.1 模块的三种调用方式

官方 Modules 文档列的来源分类是："Terraform can load modules from multiple sources, including **the local file system, a Terraform registry, and VCS repositories**."

#### ① 本地路径

```hcl
module "network" {
  source = "./modules/network"
}
```

- **必须以 `./` 或 `../` 开头**。以 `/` 开头会被识别为绝对路径。
- 官方明确建议**不要用绝对路径**："official documentation doesn't recommend using absolute filesystem paths, because they will bind your configuration to the file system layout of a particular computer."
- 相对路径是**相对于调用它的那个模块**解析的，不是相对于你当前 shell 的工作目录。这是最容易搞错的一点（见第 6 节）。

#### ② Terraform Registry

```hcl
module "vpc" {
  source  = "alibaba/vpc/alicloud"
  version = "~> 0.2"
}
```

格式是 `namespace/name/provider`：

| 来源类型 | source 写法 |
|---|---|
| 公共 Registry | `"<NAMESPACE>/<NAME>/<PROVIDER>"` |
| HCP Terraform 私有 Registry | `"app.terraform.io/<NAMESPACE>/<NAME>/<PROVIDER>"` |
| Terraform Enterprise 私有 Registry | `"<HOSTNAME>/<NAMESPACE>/<NAME>/<PROVIDER>"` |

**`version` 只对 Registry 模块有效**。官方原文："`version` is only available for modules listed in a registry." 本地路径模块**写了 version 会报错**。

**版本约束的语义**（官方 Modules 语法页）：

> "Terraform uses the **latest version** that satisfies the constraint from among the installed versions. If there is no installed version that satisfies the constraint, Terraform downloads the latest version that satisfies the constraint."

**修改 `source` 或 `version` 后必须重新 `terraform init`** —— 官方两处都单独强调了这一点。

| 约束写法 | 含义 |
|---|---|
| `"1.2.0"` | 精确等于 1.2.0 |
| `"~> 1.2"` | 允许 1.x（>=1.2, <2.0）—— **最常用** |
| `"~> 1.2.3"` | 允许 1.2.x（>=1.2.3, <1.3.0） |
| `">= 1.0, < 2.0"` | 显式区间 |

> **生产建议**：用 `~>` 而不是 `>=`。"`>= 5.0` with no upper bound" 意味着下一次 `terraform init -upgrade` 可能拉到一个大版本，变量名改了、plan 全变。

#### ③ Git 仓库

```hcl
module "vpc" {
  source = "git::https://github.com/example/terraform-alicloud-vpc.git?ref=v1.2.0"
}

# GitHub 简写
module "vpc" {
  source = "github.com/example/terraform-alicloud-vpc"
}

# SSH
module "vpc" {
  source = "git::ssh://git@example.com/terraform-alicloud-vpc.git"
}
```

**Git 来源的两个关键参数**（官方 Modules 语法页明确列出）：

| 参数 | 含义 | 官方细节 |
|---|---|---|
| `ref` | 分支名、完整或短 SHA-1、标签名 | **默认用仓库 HEAD 指向的默认分支** |
| `depth` | 浅克隆的历史深度 | **默认 1** |

**`depth` 的一个重要副作用**（官方原话）：用 `depth` 时 Terraform 会把 `ref` 传给 `git clone --branch`，因此 **`ref` 必须是远程仓库中已知的分支名或标签，不能用原始 commit ID**。

**包内子目录**（一个 git 仓库里放多个模块）：

```hcl
module "vpc" {
  source = "git::https://example.com/network.git//modules/vpc?ref=v1.2.0"
}
```

规则：**查询参数必须放在子目录段之后**（`?ref=` 在 `//modules/vpc` 后面）。Terraform 会解压整个包，但只从指定子目录读取模块。

### 2.2 模块目录约定

官方 Standard Module Structure 页给出的**最小推荐结构**：

```
minimal-module/
├── README.md
├── main.tf
├── variables.tf
└── outputs.tf
```

**完整结构**（官方给出）：

```
complete-module/
├── README.md
├── main.tf
├── variables.tf
├── outputs.tf
├── ...
├── modules/
│   ├── nestedA/
│   │   ├── README.md
│   │   ├── variables.tf
│   │   ├── main.tf
│   │   └── outputs.tf
│   └── nestedB/
│       └── ...
└── examples/
    ├── exampleA/
    │   └── main.tf
    └── exampleB/
        └── ...
```

**官方对每一项的说明**：

| 文件/目录 | 官方要求 | 写错的后果 |
|---|---|---|
| **根模块** | **唯一必需元素**。"Terraform files must exist in the root directory of the repository." | 没有根目录 `.tf` → 不是模块 |
| `README.md` | 根模块和任何嵌套模块都应有。文件名可为 `README` 或 `README.md`（后者按 markdown 处理） | 没有 README 的嵌套模块，官方认为"仅限内部使用" |
| `LICENSE` | 官方建议总是有，"even if it is not an open source license" | 公开模块无 license，很多组织不会采用 |
| `main.tf` | 最小模块的推荐文件名，**即使为空也建议存在**。是主要入口 | — |
| `variables.tf` | 变量声明 | — |
| `outputs.tf` | 输出声明 | — |
| `modules/` | 嵌套模块放这里，**用相对路径调用**（`./modules/xxx`），这样 Terraform 认为是同一个包的一部分，不会重复下载 | 用绝对/远程路径调用嵌套模块 → 重复下载、版本不一致 |
| `examples/` | 示例放根目录的 `examples/` 下。**示例里的 `source` 应该写成外部调用者会用的地址**（如 Registry 地址），不是相对路径 | 写成相对路径 → 用户复制示例后跑不起来 |
| 变量与输出**必须有 description** | 官方原话："All variables and outputs should have one or two sentence descriptions that explain their purpose. This is used for documentation." | 没写 → Registry 页面自动生成的文档是空的 |

> **关于 `versions.tf`**：官方标准结构清单里**没有列** `versions.tf`，但它确实是社区通用惯例 —— 把 `required_providers`（provider 来源与版本约束）单独放一个文件。**本篇采用这个惯例，但明确标注它是惯例而非官方标准结构的一部分。**

**为什么 `modules/` 里必须有 README.md**（官方原话）："Any nested module with a `README.md` is considered usable by an external user. If a README doesn't exist, it is considered **for internal use only**." 这是一个用文件存在性表达可见性的约定，很巧妙。

### 2.3 子模块的 source / 变量 / 输出传递

**完整的数据流**：

```
根模块                                子模块 modules/network
┌────────────────────────────┐      ┌──────────────────────────────┐
│ variable "vpc_cidr" {...}  │      │                              │
│        │                   │      │  variable "vpc_cidr" {       │
│        ▼                   │      │    type = string              │
│ module "network" {         │─────►│    validation { ... }        │
│   source = "./modules/network"│     │  }                           │
│   vpc_cidr = var.vpc_cidr  │      │                              │
│ }                          │      │  resource "alicloud_vpc" ...  │
│        ▲                   │      │                              │
│        │                   │      │  output "vpc_id" {           │
│ module.network.vpc_id      │◄─────│    value = alicloud_vpc.x.id │
│        ▼                   │      │  }                           │
│ resource "alicloud_ecs" {  │      └──────────────────────────────┘
│   vpc_id = module.network.vpc_id  │
│ }                          │      三个方向：
└────────────────────────────┘      ① 根 → 子：variable 传值
                                    ② 子 → 根：output 返回
                                    ③ 根 → 子：provider 配置（providers 映射）
```

**① 根 → 子：传变量**

```hcl
module "network" {
  source = "./modules/network"

  vpc_cidr        = var.vpc_cidr
  subnet_cidrs    = var.subnet_cidrs
  vpc_name_prefix = var.env
}
```

**子模块里必须声明同名 variable**，否则报错。

**② 子 → 根：取 output**

```hcl
resource "alicloud_instance" "web" {
  vswitch_id = module.network.vswitch_ids[0]
}
```

引用语法：`module.<标签>.<输出名>`。

**③ 根 → 子：传 provider 配置**（见 2.5）。

> **子模块的 `variable` 与根模块的 `variable` 是两套东西**。根模块的 `var.vpc_cidr` 是"整个项目对外的接口"；子模块的 `variable "vpc_cidr"` 是"这个模块对调用方的接口"。同名纯属约定，不是继承。

### 2.4 count / for_each / depends_on 在模块中的用法

官方 Modules 语法页说得很明确：`count` 和 `for_each` 在 `module` 块上**与 resource 块上完全一样**，且**互斥**。

```hcl
# 用 count 创建 N 个实例
module "network" {
  source = "./modules/network"
  count  = length(var.envs)

  vpc_cidr = var.cidrs[count.index]
}
```

- 引用：`module.network[0].vpc_id`
- 实例标识用 `count.index`。

```hcl
# 用 for_each 创建 N 个实例
module "network" {
  source = "./modules/network"

  for_each = var.envs   # map 或字符串集合

  vpc_cidr = each.value.cidr
}
```

- 引用：`module.network["dev"].vpc_id`
- 实例标识用 `each.key` / `each.value`。

> **官方对二者的选择建议**（Modules 语法页）：
> - `count`：实例**基本相同**（"nearly identical instances"）。
> - `for_each`：实例**参数不同**（"some instance arguments must have distinct values that can't be directly derived from an integer index"）。
>
> **这与 L2 篇里 resource 层的建议是一致的：`for_each` 更安全**，因为键是稳定的业务标识，集合增删不会让其他实例的地址漂移。

**`depends_on` 在模块块上**：

```hcl
module "app" {
  source = "./modules/app"

  vpc_id = module.network.vpc_id

  # 整个模块都要等某资源建好（模块内部没有引用它）
  depends_on = [alicloud_kms_key.main]
}
```

> **`depends_on` 在模块上的作用域是"整个模块"**，比在单个 resource 上写更粗。官方 Modules 语法页把 `depends_on` 列在 module 块的 meta-argument 里，但没展开。**建议优先在模块内部用引用建立依赖，模块级 `depends_on` 只在确实必要时用。**

### 2.5 provider 配置与 alias

#### 2.5.1 为什么需要 alias

一个 `provider` 块 = 一份连接配置。**多账号 / 多地域 / 多角色 → 需要多份配置 → 需要 alias。**

```hcl
# 默认配置
provider "alicloud" {
  region = "cn-hangzhou"
}

# 别名配置：另一个账号 / 另一个地域
provider "alicloud" {
  alias  = "bj"
  region = "cn-beijing"
}

provider "alicloud" {
  alias  = "staging"
  region = "cn-shanghai"
}
```

#### 2.5.2 在资源上用 alias

```hcl
resource "alicloud_vpc" "default" {
  vpc_name = "hangzhou-vpc"
  # 用默认 provider
}

resource "alicloud_vpc" "bj" {
  provider = alicloud.bj     # ← 指定用哪个
  vpc_name = "beijing-vpc"
}
```

官方 Resources 语法页对 `provider` 元参数的说明："The `provider` argument instructs Terraform to use an **alternate provider configuration** to provision the resource."

#### 2.5.3 传给子模块：providers 映射

子模块里的 `provider` 块默认是**自己声明的**，不会自动继承父模块的。要传过去，必须显式映射。

```hcl
provider "alicloud" {
  alias  = "bj"
  region = "cn-beijing"
}

module "bj_network" {
  source = "./modules/network"

  providers = {
    alicloud = alicloud.bj     # ← 子模块的 alicloud ← 父模块的 alicloud.bj
  }
}
```

官方 Modules 语法页给出的完整形式：

```hcl
providers = {
  "<provider-name-in-child-module>" = "<provider-name-from-parent-module>"
}
```

**方向不能反**：左边是**子模块**里的名字，右边是**父模块**里的配置。

**一个子模块用两个不同 provider 的例子**（官方原文示例）：

```hcl
provider "aws" {
  alias  = "usw1"
  region = "us-west-1"
}

provider "aws" {
  alias  = "usw2"
  region = "us-west-2"
}

module "tunnel" {
  source = "./tunnel"
  providers = {
    aws.src = aws.usw1
    aws.dst = aws.usw2
  }
}
```

子模块内部要声明两个带别名的 provider 块才能接收：

```hcl
# 子模块 modules/tunnel/main.tf
provider "aws" {
  alias  = "src"
  region = "us-west-1"
}

provider "aws" {
  alias  = "dst"
  region = "us-west-2"
}
```

> **注意 `providers` 映射的一个后果**：映射之后，**子模块自己声明的同 provider 默认配置会被绕过**（父模块显式指定了）。如果子模块还声明了不带 alias 的 `provider "alicloud"`，父模块不映射它时它就用自己的默认值 —— 这在多环境下容易出错。**约定：模块内部不要自己声明带凭据的 provider 块，凭据一律由父模块通过 `providers` 传入。**

### 2.6 alicloud provider 实战

#### 2.6.1 认证方式（三种，按场景选）

##### ① AK/SK 环境变量 —— CI 里最常用

```bash
export ALICLOUD_ACCESS_KEY="<AccessKey ID>"
export ALICLOUD_SECRET_KEY="<AccessKey Secret>"
# 用 STS 临时凭据时还需要：
export ALICLOUD_SECURITY_TOKEN="<token>"
```

provider 块里**什么都不写**（只写 region）：

```hcl
provider "alicloud" {
  region = "cn-hangzhou"
}
```

**为什么 CI 里最常用**：
- 凭据不进 Git、不进 `.tf`、不进 plan 文件。
- CI 的 secret 变量机制天然就是环境变量。
- 阿里云 OSS backend 文档里也明确说：`access_key` "supports environment variables `ALICLOUD_ACCESS_KEY` and `ALIBABA_CLOUD_ACCESS_KEY_ID` (Recommended)"，secret 同理。

> **必须警惕的坑：日志泄漏。** CI 里如果写 `echo $ALICLOUD_ACCESS_KEY` 调试，或者在 `set -x` 模式下跑，**密钥会完整打进 CI 日志**（很多 CI 的日志对所有人都可见，甚至对公开仓库可见）。
> **正确做法**：
> ```bash
> # 只打印是否存在，不打印值
> if [ -n "$ALICLOUD_ACCESS_KEY" ]; then echo "AK present"; else echo "AK missing"; fi
> ```
> 另外注意 Terraform 的 **plan 文件会包含明文敏感数据**（官方 Plan 文档："If your plan includes any sort of sensitive data, even if obscured in Terraform's terminal output, it will be **saved in cleartext in the plan file**"）—— **plan artifact 也要当密钥文件保管**。

##### ② profile —— 本地多账号

阿里云 provider 的 `profile` 参数（官方 provider 文档）：

> `profile` - (Optional, Available in 1.49.0+) The Alicloud profile name as set in the shared credentials file. It can also be sourced from the `ALICLOUD_PROFILE` environment variable.

配套的 `shared_credentials_file`：

> `shared_credentials_file` - (Optional, Available in 1.49.0+) This is the path to the shared credentials file. It can also be sourced from the `ALICLOUD_SHARED_CREDENTIALS_FILE` environment variable. **If this is not set and a profile is specified, `~/.aliyun/config.json` will be used.**

```hcl
provider "alicloud" {
  profile = "dev-account"
  region  = "cn-hangzhou"
}
```

**适合场景**：本地开发机上同时有"测试账号""生产账号"两套凭据，切账号只要改 `profile`。

> **注意**：profile 里的凭据是**长期 AK**，明文存在 `~/.aliyun/config.json` 里。**这个文件绝不能进 Git。**

##### ③ assume_role —— 生产推荐

用一套基础凭据去换取另一个角色的**临时**凭据。

官方 provider 文档给的参数：

| 参数 | 含义 | 官方说明 |
|---|---|---|
| `role_arn` | 要扮演的角色 ARN | "If ARN is set to an empty string, it does not perform role switching." 支持环境变量 `ALICLOUD_ASSUME_ROLE_ARN` |
| `policy` | 更严格的权限策略 | "You cannot use the passed policy to grant permissions that are in excess of those allowed by the access policy of the role that is being assumed." |
| `session_name` | 会话名 | **省略时传的是 `'terraform'`** |
| `session_expiration` | 会话有效期（秒） | OSS backend 文档：`Valid value range: [900-3600] seconds. Default to 3600` |

```hcl
provider "alicloud" {
  region = "cn-hangzhou"

  assume_role {
    role_arn        = "acs:ram::<账号ID>:role/terraform-deploy"
    session_name    = "terraform-ci"
    session_expiration = 3600
  }
}
```

**为什么生产推荐**（这是本节的核心判断）：

1. **不需要长期 AK**。CI 里只有"能扮演这个角色"的基础凭据，而这个基础凭据本身权限极小（只能 AssumeRole）。
2. **临时凭据自动过期**（默认 3600 秒），泄漏了也很快失效。
3. **权限可细分**：每个环境一个角色，角色策略里只写该环境能操作的资源。

> **阿里云官方帮助中心（Terraform 身份认证）的推荐顺序也是这个逻辑**：生产环境推荐使用"临时凭据类"方法 —— ECS 实例 RAM 角色、OIDC IdP 角色扮演、AssumeRole —— 理由是"Eliminates hard-coded long-lived AccessKeys, reducing key compromise risk. Limits permission duration through credential expiration. Support for fine-grained authorization."

**④ 补充：ECS 实例 RAM 角色**

provider 文档还有 `ecs_role_name` 参数：

> `ecs_role_name` - The RAM Role Name attached on an ECS instance for API operations. You can retrieve this from the 'Access Control' section of the Alibaba Cloud console.

**Terraform 跑在 ECS 上时**，可以完全不配 AK，让它走实例元数据拿临时凭据。

##### 认证优先级（官方 provider 文档口径）

provider 文档列出各参数"可来自哪个环境变量"：

| provider 参数 | 对应环境变量 |
|---|---|
| `access_key` | `ALICLOUD_ACCESS_KEY` |
| `secret_key` | `ALICLOUD_SECRET_KEY` |
| `security_token` | `ALICLOUD_SECURITY_TOKEN` |
| `region` | `ALICLOUD_REGION` |
| `account_id` | `ALICLOUD_ACCOUNT_ID` |
| `shared_credentials_file` | `ALICLOUD_SHARED_CREDENTIALS_FILE` |
| `profile` | `ALICLOUD_PROFILE` |
| `assume_role.role_arn` | `ALICLOUD_ASSUME_ROLE_ARN` |

> **一个容易忽略的细节**：官方文档里 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` 被标为 **(Recommended)**（OSS backend 文档与阿里云帮助中心都这么标），而 `ALICLOUD_ACCESS_KEY` / `ALICLOUD_SECRET_KEY` 是 provider 一直以来的老名字。**两套都支持**，但新写的东西优先用 `ALIBABA_CLOUD_*`（与阿里云 SDK v2 / 新版 CLI 一致），老脚本里的 `ALICLOUD_*` 继续有效。**这一点在跨团队协作时要统一，否则"我这儿能跑你那儿跑不了"。**

> **⚠️ 地域默认值提醒**：阿里云帮助中心的 Terraform 身份认证页面写明 —— "如果未在 provider 块或环境变量中指定 region，将**默认使用 cn-beijing**"。**这意味着你以为自己建的是本地资源，实际上建在北京了。** 排查"资源找不到"时先确认 region。（此为云厂商中文文档口径，建议同时用 `terraform plan` 输出里的地域与 provider 文档交叉验证。）

#### 2.6.2 常用资源类型

以下资源类型与参数来自 alicloud provider 官方文档（registry `latest/docs` 对应的 provider 仓库 `website/docs/r/` 目录）：

| 资源类型 | 作用 | 关键参数 | 导入 ID 格式 |
|---|---|---|---|
| `alicloud_vpc` | VPC 私有网络 | `vpc_name`（v1.119.0+ 新字段；旧字段 `name` 已废弃）、`cidr_block`（**可选，默认 `172.16.0.0/12`**）、`description`、`resource_group_id`、`tags`、`secondary_cidr_blocks`（v1.185.0+ 废弃）、`enable_ipv6`、`user_cidrs`、`dry_run` | `vpc-abc123456` |
| `alicloud_vswitch` | 交换机 | `vpc_id`（Required, ForceNew）、`cidr_block`（Required, ForceNew）、`zone_id`（Required, ForceNew, v1.119.0+；旧字段 `availability_zone` 已废弃）、`vswitch_name`（v1.119.0+；旧字段 `name` 已废弃）、`description`、`tags` | `vsw-abc123456` |
| `alicloud_security_group` | 安全组 | `name`、`description`（**ForceNew**）、`vpc_id`（**ForceNew**）、`security_group_type`（`normal`/`enterprise`）、`inner_access_policy`、`resource_group_id`、`tags` | `sg-abc123456` |
| `alicloud_instance` | ECS 实例 | `image_id`、`instance_type`、`vswitch_id`、`security_groups`、`internet_charge_type`、`system_disk_category`、`instance_name` | 见 provider 文档 |
| `alicloud_oss_bucket` | OSS Bucket | `bucket`（**可选，ForceNew**，省略则随机生成）、`acl`（**已废弃**，建议改用独立的 `alicloud_oss_bucket_acl`）、以及 `cors_rule`/`website`/`logging`/`referer_config`/`lifecycle_rule`/`versioning`/`server_side_encryption_rule` 等嵌套块 | `bucket-12345678`（用 bucket 名） |
| `alicloud_ram_user` | RAM 用户 | `name`（Required，1-64 字符，只能字母数字和 `-`、`.`、`_`，**不能以连字符开头**）、`display_name`（1-128 字符）、`mobile`、`email`、`comments`、`force` | `123456789xxx`（用户 ID） |
| `alicloud_ram_role` | RAM 角色 | 见 provider 文档 | **ID 或名称**（`my-role`） |
| `alicloud_ram_policy` | RAM 策略 | 见 provider 文档 | **ID 或名称**（`my-policy`） |

**必须注意的三个"版本陷阱"**（都是 provider 文档里明确标注的 Deprecated）：

| 废弃字段 | 废弃版本 | 替代 | 踩坑后果 |
|---|---|---|---|
| `alicloud_vpc.name` | v1.119.0+ | `vpc_name` | 老代码在 1.119.0+ 会打 deprecation 警告；将来版本会移除 |
| `alicloud_vswitch.name` | v1.119.0+ | `vswitch_name` | 同上 |
| `alicloud_vswitch.availability_zone` | v1.119.0+ | `zone_id` | 同上 |
| `alicloud_oss_bucket.acl` | 1.220.0+ | `alicloud_oss_bucket_acl` 独立资源 | **不改会出真问题**：官方明确说如果同时用独立子资源（如 `alicloud_oss_bucket_policy`）和 bucket 自身的属性，**必须在 bucket 上配 `lifecycle.ignore_changes`**，否则 Terraform 会反复检测到漂移并撤销子资源做的修改 |
| `alicloud_security_group.inner_access` | v1.55.3+ | `inner_access_policy` | 同类 |
| `alicloud_vpc.secondary_cidr_blocks` | v1.185.0+ | 新资源 `alicloud_vpc_ipv4_cidr_block` | 官方明确：**两者不能同时使用** |

> **给面试的版本对齐口径**：alicloud provider **1.119.0 是一次大版本字段重命名**（`name` → `vpc_name`/`vswitch_name`，`availability_zone` → `zone_id`）。写新代码一律用新字段名；读老代码时先看它锁的 provider 版本。

**数据源**（只读）常用：

| 数据源 | 用途 |
|---|---|
| `data "alicloud_images"` | 按 `name_regex` / `most_recent` / `owners` 查镜像 |
| `data "alicloud_instance_types"` | 按 `cpu_core_count` / `memory_size` 查可用规格 |
| `data "alicloud_zones"` | 查可用区 |
| `data "alicloud_regions"` | 查地域 |
| `data "terraform_remote_state"` | **读另一个 remote state 的 outputs**（跨 stack 传值） |

`data "terraform_remote_state"` 在 OSS backend 上的官方写法：

```hcl
data "terraform_remote_state" "network" {
  backend   = "oss"
  config    = {
    bucket = "remote-state-dns"
    key    = "terraform.tfstate"
    prefix = "mystate/state"
    region = "cn-beijing"
  }
  outputs   = {}
  workspace = "default"
}
```

> **注意 `alicloud_vpc.cidr_block` 是可选的，默认 `172.16.0.0/12`**（官方文档："The `cidr_block` is Optional and default value is `172.16.0.0/12` after v1.119.0+"）。**漏写不报错**，但你会拿到一个 172.16/12 的大网段 —— 和你想要的 10.x 完全不是一回事。**所以 VPC 的 `cidr_block` 必须显式写。**

### 2.7 资源导入实操

#### 2.7.1 命令行导入

格式（官方 Import 命令文档）：

```
terraform import [options] ADDRESS ID
```

```bash
# VPC
terraform import alicloud_vpc.example vpc-abc123456

# 交换机
terraform import alicloud_vswitch.example vsw-abc123456

# 安全组
terraform import alicloud_security_group.example sg-abc123456

# OSS bucket（用 bucket 名，不是 ID）
terraform import alicloud_oss_bucket.bucket bucket-12345678

# RAM 角色（ID 或名称都行）
terraform import alicloud_ram_role.example my-role

# RAM 策略（ID 或名称都行）
terraform import alicloud_ram_policy.example my-policy
```

**三个必须知道的细节**：

1. **地址里带 count / for_each 时要用实例地址**（官方示例）：
   ```bash
   terraform import 'alicloud_instance.baz[0]' i-abcd1234
   terraform import 'alicloud_instance.baz["example"]' i-abcd1234
   ```
   键里有引号时，**命令行上要转义**，否则被 shell 吃掉。

2. **同一个云资源只能 import 到一个地址**。官方警告：
   > "Terraform expects that each remote object it is managing will be bound to **only one** resource address... If you import the same object multiple times, Terraform may exhibit unwanted behavior."

3. **import 只写 state，不写配置**。import 之后你**必须**在 `.tf` 里补上对应的 `resource` 块，否则下次 plan 会说"要创建"（见第 6 节）。

#### 2.7.2 配置式导入（config-driven import）

官方现在推荐的方式，**因为它能把 import 自动化进 CI/CD 流水线**。

官方 Import 语言页的流程说明：

> "Importing unmanaged resources to your workspace requires an **`import` block that specifies the unique infrastructure resource ID to import**. The block also declares an address for the imported resource in state. **Additionally, you must create a destination `resource` block that matches the address** declared in the `import` block."

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

**关键点**：`import` 块和 `resource` 块**都要写**。官方还提供了一个省事路径：

> "Alternatively, you can write only the `import` block and run the `terraform plan` command with the **`-generate-config-out`** flag to generate the `resource` blocks."

**批量导入**：官方 Import 页提到可以"create queries to discover large sets of unmanaged resources to import them in bulk"，用 HCL 定义查询，把结果写进配置，再 `terraform apply` 一次性导入。

#### 2.7.3 import 与 moved 的对照（回顾 L2）

| | `import` | `moved` |
|---|---|---|
| 解决 | **已存在的外部资源**纳入管理 | **地址变更**不要重建 |
| 资源本身 | 没变，只是开始被管 | 没变，只是换了地址 |
| 方向 | 外部 → Terraform | 旧地址 → 新地址 |

### 2.8 RAM 权限与最小权限

**这一节是本篇最"生产"的部分，也是面试最爱追问的。**

#### 2.8.1 铁律：部署用的 AK 必须是受限子账号

```
❌ 用主账号 AK 跑 terraform
   → 权限无限大，一个 `rm -rf` 式的 for_each 错误或变量写错就能删掉整个账号的资源
   → AK 一旦泄漏 = 整个账号沦陷

✅ 用 RAM 子账号 + 精确授权的 Policy
   → 出事范围被限制在 policy 允许的资源类型和资源组内
   → AK 泄漏也只能做 policy 允许的事
```

#### 2.8.2 三层身份，从弱到强

| 层级 | 凭据类型 | 权限 | 有效期 | 适用 |
|---|---|---|---|---|
| ① 主账号 AK | 长期 AK | **全部** | 永久 | ❌ **永远不要用于自动化** |
| ② RAM 用户 AK | 长期 AK | policy 限定 | 永久（需手动轮换） | 本地开发、临时脚本 |
| ③ RAM 角色（AssumeRole / OIDC） | 临时 STS token | role policy 限定 | 默认 3600 秒 | **CI/CD 生产部署** |

#### 2.8.3 最小权限策略怎么配

**原则：只给"这次部署真正要操作的资源类型"，并尽量用 resource group 限定范围。**

一个典型的 Terraform 部署策略思路（不是可直接粘贴的完整 policy，字段以 RAM 官方文档为准）：

```
允许（Allow）:
  - vpc:CreateVpc / DeleteVpc / DescribeVpcs / ModifyVpcAttribute
  - vswitch:CreateVSwitch / DeleteVSwitch / DescribeVSwitches
  - ecs:CreateInstance / DeleteInstance / DescribeInstances / RunInstances
  - oss:PutBucket / DeleteBucket / GetBucket / ListObjects
  - ram:CreateUser / CreateRole / AttachPolicyToRole / DeleteUser
  - sts:GetCallerIdentity        ← 几乎总要加，provider 用来确认身份
拒绝（Deny）:
  - ram:*DeleteAccount*
  - ram:*CreateAccessKey*         ← 防止 Terraform 自己给自己开后门
  - ram:*AttachUserPolicy*
```

**三条实操要点**：

1. **`sts:GetCallerIdentity` 一定要给**。provider 需要它来确认"我是谁"、账号 ID 是多少。少了它，很多操作会失败且报错不直观。
2. **把"创建 IAM 权限"和"使用 IAM 权限"分开**。Terraform 可以创建 RAM 用户，但**不应该给 RAM 操作授予"能再授权"的权限**（否则配置里写错一次 policy，就可能把管理员权限授予任意角色）。
3. **在 resource group 里限定**。阿里云有资源组（Resource Group）概念，policy 里限定 `acs:bss:...:resource-group/xxx/*` 能把爆炸半径压到最小。

#### 2.8.4 权限不足的报错为什么不直观

**这是阿里云 API 的现实**：RAM 权限不足时，云 API 返回的错误信息往往不直接说"你没权限"，而是各种语义模糊的报错。**官方 provider 文档没有统一章节讲这个**，属于社区普遍反馈的现象（本篇标 `⚠️ 单源待补`，具体错误码以阿里云 API 错误码文档为准）。

**排查方法（不靠猜，靠对比）**：

```
① 先确认身份对不对
   provider 用 sts:GetCallerIdentity 拿到的是不是你以为的那个账号/角色
② 再确认策略挂上了没有
   在 RAM 控制台看：这个子用户/角色上有没有挂那个 Policy
③ 再确认策略内容对不对
   Action 拼写、大小写、资源 ARN 是否写全
④ 最后才怀疑是 provider 的问题
```

> **实战经验**：阿里云的 Terraform 报错里，权限问题经常伪装成"资源不存在"、"参数非法"、"配额超限"。**判断方法：如果同样的配置在控制台手工操作能成功、Terraform 就是失败 → 大概率是权限或 region 问题，不是配置语法问题。**

### 2.9 敏感输出的处理

模块化会放大敏感值的问题：**一个模块输出的密码，可能被三个模块引用，最后打印在根模块的 output 里。**

**处理原则**：

| 位置 | 做法 |
|---|---|
| 变量 | `sensitive = true`（遮蔽 CLI 显示，**值仍在 state**） |
| 输出 | `sensitive = true`（同上） |
| 传递 | **敏感标记会自动传播** —— 引用敏感变量的表达式**也会自动成为敏感值**，不需要手动再标 |
| state | **必须放加密的远端 backend**（这是唯一真正的防线） |
| 高敏感值 | `ephemeral = true`（Terraform v1.10+，不写入 state 和 plan 文件） |

**"敏感标记自动传播"这条很重要**（官方 Variables 文档）：

> "Any expression that refers to a sensitive value is also automatically sensitive."

所以模块里这样写是安全的：

```hcl
# 子模块
variable "db_password" {
  type      = string
  sensitive = true
}

output "db_password" {
  value     = var.db_password
  sensitive = true    # ← 必须写，否则子模块的 output 会明文
}
```

```hcl
# 根模块
module "db" {
  source = "./modules/db"
  db_password = var.db_password
}

output "db_password" {
  value     = module.db.db_password   # ← 已经是敏感值了
  sensitive = true                     # ← 但输出仍要显式标
}
```

> **⚠️ 一个反直觉的官方行为**：Output 命令文档明确说 —— **用 `-json` 或 `-raw` 时，敏感值以明文显示**；而且即使不加这些选项，`terraform output <NAME>` 按名字指定输出时 **Terraform 不会 redact**。
> 官方原文：
> > "Note that Terraform does not redact `sensitive` values when you specify the output by name"
> > "When using the `-json` or `-raw` command-line flags, Terraform displays `sensitive` values in plain text."
>
> **所以 CI 里千万不要把 `terraform output -json` 的原始输出打进公开日志。**

### 2.10 模块版本与 Registry 发布

#### 2.10.1 版本号怎么定（语义化版本）

| 变更类型 | 版本位 | 例子 | 对调用方的影响 |
|---|---|---|---|
| 修 bug，行为不变 | PATCH | 1.0.0 → 1.0.1 | 无感 |
| 加新可选变量 | MINOR | 1.0.0 → 1.1.0 | 无感（**前提：默认值不改变已有行为**） |
| 改/删/重命名必填变量 | MAJOR | 1.0.0 → 2.0.0 | **破坏性**，调用方必须改 |

**模块版本演进的一条铁律**（来自模块化实践，本篇标 `⚠️ 单源待补`——Terraform 官方文档未直接给出模块语义化版本细则，但 Registry 明确要求 release tag 必须是 semver）：

> **MAJOR 版本里，如果要改一个变量名，不能直接删。正确做法是：新增新变量名 → 老的保留并标记 deprecated → 等所有调用方迁移完 → 下个 MAJOR 再删。**
> **如果老的必填变量被删了，调用方升级 = 直接报错**（"Missing required argument"）。这就是"版本不兼容"的根源。

#### 2.10.2 发布到公共 Registry 的官方要求

官方 Publish 页列的四条硬性要求：

| 要求 | 具体内容 |
|---|---|
| **GitHub** | 模块必须在 GitHub 上，且是**公开仓库**（私有 Registry 可忽略此条） |
| **仓库命名** | 必须 `terraform-<PROVIDER>-<NAME>` 三段式，`<NAME>` 可含额外连字符。例：`terraform-google-vault`、`terraform-aws-ec2-instance` |
| **仓库描述** | GitHub 仓库描述会被用作模块的简短说明，应是一句话 |
| **标准模块结构** | 必须遵守 standard module structure，Registry 才能解析、生成文档、追踪子模块和 examples |
| **版本 tag** | release tag 必须是**语义化版本**，可带 `v` 前缀（`v1.0.4`、`0.9.2` 都行）。首次发布**至少要有一个 tag**。不像版本号的 tag 会被忽略 |

**发布流程**（官方）：

> "you can publish a public module by going to the Terraform Registry and clicking the **'Upload'** link in the top navigation." → 用 GitHub 账号登录 → 选择符合命名约定的仓库 → 点击 "Publish Module"。

**发新版本**：官方说明 —— "create and push a new tag with the proper format. The webhook will notify the registry of the new version and it will appear on the registry **usually in less than a minute**."

> **关于 `terraform login` / `terraform publish`**：官方 CLI 文档里的 `terraform login` 命令是**为 HCP Terraform / Terraform Enterprise 获取 API token 用的**（`terraform login [hostname]`，token 明文存到 `credentials.tfrc.json`），**不是给公共 Registry 发布模块用的**。公共 Registry 的官方发布路径是 **网页 Upload + GitHub OAuth + Git tag webhook**。
> **`terraform publish` 命令在当前官方 CLI 文档中已查不到对应页面（实测 404）**，因此本篇**不把它作为推荐路径**。若企业内部用私有 Registry 或自建 Registry，发布方式以该 Registry 自身文档为准。**这一条标 `⚠️ 待验证`。**

#### 2.10.3 私有 Registry

官方 Modules 页提到："HCP Terraform and Terraform Enterprise include a **private module registry** for sharing modules internally within your organization."

私有 Registry 的好处（有官方依据的）：

| 能力 | 说明 |
|---|---|
| 版本约束 | 和公共 Registry 一样用 `version = "..."` |
| 自动生成文档 | 同上 |
| **内部模块不发到公共 Registry** | 公司内部约定（网段规范、账号结构）不外泄 |
| **可复用"只有内部才知道"的基础设施** | 例如内部镜像仓库、专有云 endpoint |

**企业内部模块的推荐实践**（本篇基于官方 private registry 能力给出的推论）：

```
私有 Registry 放"平台级模块"（网络基座、账号骨架、日志接入）
公共 Registry 只放"已经可以对外"的东西
本地路径模块只放"正在开发、还没稳定"的
```

### 2.11 工作区与模块的组合

这两件事看起来无关，其实耦合很深。**因为 workspace 切的是 state，而模块里如果有资源，模块的资源就在当前 workspace 的 state 里。**

```
根模块（一份 .tf）
   │
   ├─ module "network"   ─┐
   └─ module "app"       ─┤
                          │
       ┌──────────────────┴──────────────────┐
       │  workspace: default → state-A      │
       │  workspace: dev     → state-B      │
       │  workspace: prod    → state-C      │
       └─────────────────────────────────────┘
```

**组合起来的三条实用结论**：

1. **同一个模块代码，在不同 workspace 下管的是不同的资源**。切 workspace 之后 `terraform plan` 看到的是**另一套现实**。所以切 workspace 后**第一件事是 `terraform plan`**，不是 `apply`。
2. **模块的 `source` 不会随 workspace 变**。`./modules/network` 永远是那份代码。**workspace 不隔离代码，只隔离状态。**
3. **所以 workspace 不能用来隔离"代码 + 凭据"**。官方明确警告："Workspaces are not appropriate for system decomposition or deployments requiring separate credentials and access controls."

> **正确的多环境姿势**（结合 L2 的结论）：

| 层次 | 隔离方式 | 理由 |
|---|---|---|
| **代码** | 不同分支 / 不同目录（每环境一个 root module 目录） | 环境差异体现在 tfvars / 代码，不是切 workspace |
| **状态** | 同一个远端 backend，**不同 key/prefix**（或不同 workspace） | backend 是命名空间隔离 |
| **凭据** | 不同 provider alias / 不同 profile / 不同 assume_role | 官方明确说 workspace 不提供凭据隔离 |

一个常见的清晰做法：

```
prod/
  main.tf        → module "network" { source = "../modules/network" }
  backend.tf     → backend "oss" { bucket=..., key="prod/network.tfstate" }
  prod.tfvars
staging/
  main.tf        → module "network" { source = "../modules/network" }
  backend.tf     → backend "oss" { bucket=..., key="staging/network.tfstate" }
  staging.tfvars
modules/
  network/       ← 唯一的一份实现
```

**这样模块只有一份，状态和凭据都按目录隔离。**

---

## 3. 动手

> **本节命令是教学示例。本次写作未执行 terraform、未创建任何云资源、未导入任何已有资源。** 照做时按"看到什么算对"逐条核对。

### 3.1 目录结构

```
tf-demo/
├── main.tf                 # 根模块：provider + 调用模块
├── backend.tf              # 远端 state
├── variables.tf
├── outputs.tf
├── terraform.tfvars
└── modules/
    └── network/
        ├── main.tf
        ├── variables.tf
        └── outputs.tf
```

`modules/network/main.tf`：

```hcl
resource "alicloud_vpc" "this" {
  vpc_name   = "${var.name_prefix}-vpc"
  cidr_block = var.vpc_cidr
  description = "由 Terraform 模块创建"
  tags = var.tags
}

resource "alicloud_vswitch" "this" {
  for_each = toset(var.subnet_cidrs)

  vpc_id     = alicloud_vpc.this.id
  cidr_block = each.value
  zone_id    = var.zone_id
  vswitch_name = "${var.name_prefix}-vsw-${each.key}"
  tags       = var.tags
}
```

> **这里用了 `for_each` 而不是 `count`** —— 因为交换机用 CIDR 标识更直观，且 CIDR 集合增删时不会让其他交换机的地址漂移（L2 篇的结论）。

`modules/network/variables.tf`：

```hcl
variable "name_prefix" {
  type        = string
  description = "资源名前缀，通常是 环境名-应用名"
}

variable "vpc_cidr" {
  type        = string
  description = "VPC 的 CIDR"

  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0))
    error_message = "vpc_cidr 必须是合法的 CIDR，例如 10.0.0.0/16。"
  }
}

variable "subnet_cidrs" {
  type        = list(string)
  description = "各交换机的 CIDR 列表"

  validation {
    condition     = length(var.subnet_cidrs) > 0
    error_message = "至少要提供一个交换机 CIDR。"
  }
}

variable "zone_id" {
  type        = string
  description = "交换机所在可用区 ID"
  default     = "cn-hangzhou-i"
}

variable "tags" {
  type        = map(string)
  description = "打给资源的标签"
  default     = {}
}
```

`modules/network/outputs.tf`：

```hcl
output "vpc_id" {
  description = "VPC 的 ID"
  value       = alicloud_vpc.this.id
}

output "vswitch_ids" {
  description = "各交换机的 ID，键为 CIDR"
  value       = { for k, v in alicloud_vswitch.this : k => v.id }
}
```

`main.tf`（根模块）：

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
  region = var.region
  # 凭据走环境变量 ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY
}

module "network" {
  source = "./modules/network"

  name_prefix = var.name_prefix
  vpc_cidr    = var.vpc_cidr
  subnet_cidrs = var.subnet_cidrs
  zone_id     = var.zone_id
  tags        = local.tags
}

locals {
  tags = {
    Env     = var.env
    Managed = "terraform"
  }
}
```

### 3.2 逐步命令

```bash
# ① 初始化（会下载 provider 和本地模块）
terraform init

# 看到什么算对：输出里有 "Terraform has been successfully initialized!"
#                "Initializing the local backend..."（本地模块会被复制到 .terraform/modules/）

# ② 校验
terraform validate
# 看到什么算对：Success! The configuration is valid.

# ③ 看模块最终选了什么
terraform providers
# 看到什么算对：列出 alicloud provider 及其版本约束

# ④ 预演
terraform plan
# 看到什么算对：
#   Plan: 2 to add, 0 to change, 0 to destroy.
#   # module.network.alicloud_vpc.this will be created
#   # module.network.alicloud_vswitch.this["10.0.1.0/24"] will be created

# ⑤ 执行
terraform apply

# ⑥ 验证模块输出能被根模块读到
terraform output vpc_id

# ⑦ 验证资源确实归 Terraform 管
terraform state list
# 看到什么算对：能看到 module.network.alicloud_vpc.this 等条目

# ⑧ 清理
terraform destroy
```

### 3.3 验证清单

| 检查 | 命令 | 期望 |
|---|---|---|
| 模块被正确加载 | `terraform init` 后看 `.terraform/modules/modules.json` | 有 `modules/network` 条目，`Source": "./modules/network"` |
| 依赖图正确 | `terraform graph -type=plan` | vswitch 指向 vpc（DOT 图里有边） |
| 模块输出可读 | `terraform output vpc_id` | 打印 VPC ID |
| 多实例寻址正确 | `terraform state list` | vswitch 显示为 `module.network.alicloud_vswitch.this["10.0.1.0/24"]` |
| 参数约束生效 | 把 `vpc_cidr` 改成 `"10.0.0.0"` 后 plan | 报 `Invalid value for input variable` 并显示你的 `error_message` |
| plan 无意外变更 | 再跑 `terraform plan` | `No changes.` |

### 3.4 导入实操（把已有的手工资源纳管）

```bash
# ① 先写好 resource 块（import 只写 state，不写配置）
#    在 main.tf 里写好 alicloud_vpc "example" 的完整定义

# ② 查看要导入的资源 ID（从阿里云控制台或 API 拿）
#    VPC 的 ID 形如 vpc-abc123456

# ③ 导入
terraform import alicloud_vpc.example vpc-abc123456

# ④ 立刻验证
terraform plan
# 看到什么算对：应该是 No changes. 或只有极小的差异。
#              如果还显示 "will be created" → 配置和现实不一致（见第 6 节）

# ⑤ 更省事的做法：只写 import 块，让 Terraform 生成 resource 块
#    import { to = alicloud_vpc.example  id = "vpc-abc123456" }
#    terraform plan -generate-config-out=generated.tf
```

---

### 3.5 常用运维命令（Terraform 模块与多环境，按场景）

> **本节能做什么**：3.1–3.4 是"把模块写出来、把存量资源纳管进来"，本节是**团队用模块 + 多环境时每天会敲的 Terraform 运维命令**——模块升级、按环境套值、状态搬迁、provider 锁定。配置管理岗做"基础设施即代码平台化"就靠这些。
> **环境基准**：本机实测 `terraform v1.9.x`，以 `alicloud` provider + 自研 module 为例。

#### ① 模块拉取与升级（init / get / providers lock）

```bash
terraform init                                  # 初始化并拉取 module 源（首次/改 source 后必跑）
terraform init -upgrade                         # 把 module 和 provider 升到版本约束允许的最新（升级依赖用）
terraform get -update                           # 只更新 module 缓存（不碰 provider/backend）
terraform providers lock -platform=linux_amd64 -platform=darwin_arm64  # 锁定多平台 provider 校验和
terraform providers schema -json                # 导出 provider 的 schema（给工具链/文档用）
```

> **实战价值**：`init -upgrade` 是"模块出新版本了，我要跟"的标准动作；但**生产前务必先 `plan` 看 diff**，模块升级经常偷偷改默认值。`providers lock` 在多架构 CI（mac 开发、linux 跑流水线）时是必需的，否则流水线报"checksum 不匹配"。

#### ② 多环境套值与定向执行（plan/apply -var-file / -target）

```bash
terraform plan -var-file=env/prod.tfvars        # 用 prod 环境变量文件跑 plan
terraform apply -var-file=env/staging.tfvars -auto-approve
terraform plan -var="instance_count=3"          # 命令行临时覆盖单个变量
terraform apply -target=module.net.vpc         # 只动某个 module 的资源（紧急修网用，慎用）
terraform plan -var-file=env/prod.tfvars -out=prod.tfplan  # 存 plan，apply 时吃同一份
```

> **实战价值**：`-var-file` 是"一套 module、N 套环境"的核心开关，环境差异全在 tfvars 里。`-target` 能救急（只修一个挂了的资源），但**长期用会破坏 state 一致性**，只当作临时手术刀。

#### ③ 状态与资源重构（state mv / rm / push / pull）

```bash
terraform state list                            # 看当前有哪些资源
terraform state mv module.old.vpc module.new.vpc  # 模块重构后搬 state，避免销毁重建
terraform state rm module.legacy.thing         # 把资源移出 state（保留云上真实资源）
terraform state push                            # 把本地 state 推回 remote backend
terraform state pull > local.tfstate            # 把 remote state 拉到本地备份/审计
terraform output -json                          # 导出全部 output 为 JSON（对接其他系统/CI）
```

> **实战价值**：**`state mv` 是模块重构的护身符**——你重命名了 module 路径，不 mv 就会"旧资源被销毁 + 新资源被创建"，生产上就是一次事故。`state pull` 定期备份 remote state 到 Git，是防御 backend 损坏的最后一道保险。

#### ④ 排障：模块校验与漂移

```bash
terraform validate -json                       # 机器可读的校验结果（CI 里当门禁）
terraform fmt -check -recursive                # 只检查格式不改动，CI 门禁用（不合规就红）
terraform plan -detailed-exitcode              # 0=无变更 1=错误 2=有变更
terraform refresh && terraform plan            # 漂移复原计划：先对齐真实云上再判断
```

> **实战价值**：`fmt -check` + `validate -json` 放进 PR 流水线，能在合入前挡住 90% 的模块低级错误。`-detailed-exitcode` 让 CI 精确区分"没变化（跳过 apply）"和"有漂移（要同步）"，是 GitOps 自动化的关键判断位。

---

## 4. 逐字段表

### 4.1 provider 配置项表（alicloud）

| 配置项 | 类型 | 必填 | 含义 | 环境变量 | 写错的后果 |
|---|---|---|---|---|---|
| `region` | string | 实际必填 | 地域 | `ALICLOUD_REGION` | **不写则默认 `cn-beijing`**（阿里云帮助中心口径）→ 资源建到北京去了 |
| `access_key` | string | 与 secret_key 同生共死 | AccessKey ID | `ALICLOUD_ACCESS_KEY` / `ALIBABA_CLOUD_ACCESS_KEY_ID` | 写错 → 认证失败，plan 阶段报错 |
| `secret_key` | string | 与 access_key 同生共死 | AccessKey Secret | `ALICLOUD_SECRET_KEY` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 同上；**写进 .tf 会进 state 和 plan 文件** |
| `security_token` | string | 用 STS 时必填 | 临时安全令牌 | `ALICLOUD_SECURITY_TOKEN` / `ALIBABA_CLOUD_SECURITY_TOKEN` | 缺失 → STS 凭据被拒 |
| `account_id` | string | | 账号 ID | `ALICLOUD_ACCOUNT_ID` | 部分服务（函数计算、路由器接口）连接需要 |
| `shared_credentials_file` | string | | 共享凭据文件路径 | `ALICLOUD_SHARED_CREDENTIALS_FILE` | 不写且指定 profile → 落到 `~/.aliyun/config.json` |
| `profile` | string | | 凭据文件里的配置名 | `ALICLOUD_PROFILE` | **profile 不存在 → 认证失败**（v1.49.0+ 支持） |
| `assume_role.role_arn` | string | 用 assume_role 时必填 | 要扮演的角色 ARN | `ALICLOUD_ASSUME_ROLE_ARN` | ARN 写错 → AssumeRole 失败；**设为空字符串则不做角色切换** |
| `assume_role.policy` | string | | 更严格的临时策略 | — | **不能超出角色原有权限**，否则 AssumeRole 失败 |
| `assume_role.session_name` | string | | 会话名 | `ALICLOUD_ASSUME_ROLE_SESSION_NAME` | 省略 → 传 `'terraform'` |
| `assume_role.session_expiration` | number | | 会话有效期（秒） | `ALICLOUD_ASSUME_ROLE_SESSION_EXPERSON_EXPIRATION` | **有效范围 [900-3600]，默认 3600**；超出范围报错 |
| `ecs_role_name` | string | | 绑在 ECS 上的 RAM 角色名 | — | 角色名错 → 拿不到实例元数据凭据 |
| `endpoints` | block | | 自定义 endpoint（专有云等） | — | 指向错 → API 调用失败 |
| `skip_region_validation` | bool | | 跳过静态地域校验 | — | 用了非公开地域时需要（v1.52.0+） |
| `configuration_source` | string | | 标记配置来源（便于 User-Agent 溯源） | `TF_APPEND_USER_AGENT` | 只影响 UA 头，无功能风险 |
| `protocol` | string | | HTTP / HTTPS | — | **默认 HTTPS**；改 HTTP → 凭据明文传输 |
| `client_read_timeout` | number | | 读超时（毫秒） | — | **默认 60000**；ECS 慢时可能被误杀 |
| `client_connect_timeout` | number | | 连接超时（毫秒） | — | **默认 60000** |
| `alias` | string | | provider 别名 | — | 引用时写错别名 → 找不到 provider 配置 |

> `assume_role` 的**旧写法**（`assume_role` 块形式）在 OSS backend 文档里已标 **Deprecated as of 1.1.0+**，官方推荐"flattening"成 `assume_role_role_arn` / `assume_role_policy` / `assume_role_session_name` / `assume_role_session_expiration` 四个平铺参数。**provider 块本身仍用 `assume_role { }` 嵌套块**（provider 文档口径），backend 块用平铺参数。

### 4.2 模块调用参数表

| 参数 | 类型 | 必填 | 含义 | 写错的后果 |
|---|---|---|---|---|
| `source` | string | ✅ | 模块来源：`"./path"` / `"ns/name/provider"` / `"git::https://...?ref=x"` | **必须是字面量字符串**，不能引用变量（除非该变量声明了 `const = true`）；非 `./`/`../` 开头的本地路径会被当绝对路径 |
| `version` | string | | 版本约束，**仅 Registry 模块可用** | 本地路径模块写了 → 报错；范围太宽（如 `>= 1.0` 无上界）→ 一次 `init -upgrade` 可能拉到大版本 |
| `providers` | map | | provider 映射：`{ 子模块名 = 父模块配置 }` | **方向写反** → 子模块拿到错误账号的凭据；不映射 → 子模块用自己的默认 provider（可能是空的） |
| `count` | number | | 创建 N 个实例（与 `for_each` 互斥） | 同时写 `for_each` → 报错 |
| `for_each` | map/set | | 创建 N 个实例（与 `count` 互斥） | 键必须是**字符串**且**plan 阶段已知** |
| `depends_on` | list | | 显式依赖整个模块 | 范围太粗 → 依赖图被拉宽 |
| `<模块自定义参数>` | 任意 | 取决于模块 | 传给子模块的 variable | 子模块没声明同名 variable → 报错 |
| `ignore_nested_deprecations` | bool | | 忽略嵌套模块的废弃告警 | — |

**官方对 `source` 和 `version` 的额外限制**（Modules 语法页）：

| 限制 | 官方原文要点 |
|---|---|
| `source` 必须是字面量 | "The value of `source` must be a literal string." 不支持模板序列和任意表达式 |
| `source` 可引用常量变量 | 引用的变量必须声明 `const = true` |
| 改 `source` 要 `init` | 官方明确要求 |
| 改 `version` 要 `init` | 官方明确要求 |

---

## 5. 横向对比

### 5.1 本地模块 vs Registry 模块

| 维度 | 本地路径模块 | Registry 模块 |
|---|---|---|
| **写法** | `source = "./modules/network"` | `source = "alibaba/vpc/alicloud"` |
| **版本控制** | 跟主仓库一起（相对路径） | Registry 独立版本；`version = "~> 1.2"` 约束 |
| **升级方式** | 改主仓代码即可，**立即生效** | 改 `version` 约束 + `terraform init`（或 `init -upgrade`） |
| **是否需要联网** | ❌ 不需要 | ✅ 需要（init 时下载） |
| **跨项目复用** | ❌ 要复制目录 | ✅ 直接引用 |
| **文档** | 自己写 README | **Registry 自动生成**（inputs/outputs 从 `description` 提取） |
| **发现问题** | 直接看代码 | 看 Registry 页面上的版本历史 |
| **适合** | **正在开发、频繁改**的模块；本项目内部模块 | **稳定的、公司级**基础模块；对外开源 |

**Registry 模块的一个隐藏成本（官方文档提到但常被忽略）**：Registry 上模块的**输入输出数量可能远超你以为的**。官方 Publish 页说 Registry 会 "parse submodules and examples in the module's source repository"。所以用别人家的模块前，**一定要先看它的 Inputs 列表**（没有 default 的是必填），再 `terraform plan` 看它到底要建什么。

**选型建议**：

| 情况 | 选 |
|---|---|
| 自己项目里，模块还在改 | **本地路径**（改起来快，不用发版） |
| 团队内多个项目要用，要版本 | **私有 Registry**（有版本约束 + 自动文档） |
| 稳定到可以开源 | 公共 Registry |
| 临时看一眼别人怎么写的 | Registry（只读参考，别直接抄进生产） |

### 5.2 count vs for_each（在模块上同样适用）

| 维度 | `count` | `for_each` |
|---|---|---|
| 实例标识 | **整数下标** `module.m[0]` | **字符串键** `module.m["dev"]` |
| 键的要求 | 数字，天然 plan 阶段已知 | **必须是字符串**且 **plan 阶段已知** |
| 集合类型 | 任意可计数的集合 | **只能是 map 或字符串集合** |
| 元素增删的影响 | **删中间元素 → 后面元素下标全部前移 → 全部重建** | **只影响该键对应的实例** |
| 适合 | 实例基本相同 | **实例参数不同**（官方建议） |
| 与其他资源互相引用 | `module.m[0].id` | `module.m["dev"].id` |

> **官方建议原文**（Modules 语法页）：
> "`count` ... Use the `count` argument when you want to create **nearly identical** instances. Use `for_each` when some instance arguments must have **distinct values that can't be directly derived from an integer index**."

> **结论：`for_each` 更安全。** 理由不是"性能"，是**地址稳定性** —— 集合增删时其他实例的地址不变，因此不会触发无谓的重建。**唯一例外**：你确实要创建 N 个完全相同的实例（比如 3 台一样的 ECS 做测试），`count` 更简洁。

### 5.3 AK/SK 环境变量 vs profile vs assume_role

| 维度 | AK/SK 环境变量 | profile | assume_role |
|---|---|---|---|
| **凭据类型** | 长期 AK | 长期 AK（存在 `~/.aliyun/config.json`） | **临时 STS token** |
| **有效期** | 永久 | 永久 | **默认 3600 秒** |
| **多身份切换** | 换环境变量 | **换 profile 名**（最方便） | 换 role_arn |
| **权限粒度** | 取决于那个 AK 绑的策略 | 同左 | **取决于 role 的策略**，且可用 `policy` 再收紧 |
| **泄漏后果** | 严重（长期有效） | 严重（长期有效） | **有限（很快过期）** |
| **CI 友好度** | ✅ 最好（CI secret 天然是环境变量） | ❌ 要先把凭据文件带进 CI | ✅ 好，但要先有 AssumeRole 的基础凭据 |
| **本机多账号** | 一般 | ✅ **最方便** | 可以但没必要 |
| **生产推荐** | 谨慎（要轮换） | ❌ 本地开发用 | ✅ **首选** |

**三个都用得上的姿势**（分层）：

```
本地开发机  → profile（切账号方便）
CI 环境     → 环境变量注入基础 AK（受限到只能 AssumeRole）
生产部署    → assume_role 换成真正的部署角色（临时凭据）
```

### 5.4 Terraform 管云资源 vs Terraform 管 K8s（职责边界）

**这是本篇最重要的判断题，也是面试高频。**

**先说结论：**

> **Terraform 应该管"云资源的拓扑与生命周期"（VPC、子网、ECS、RDS、OSS、RAM、SLB…），而"K8s 集群里的应用资源"应该交给 Helm + ArgoCD 管，不要用 Terraform 直接管 K8s 清单。**

**为什么**（这是本节的核心论证）：

| 维度 | Terraform 管 K8s 资源的问题 | Helm + ArgoCD 管的原因 |
|---|---|---|
| **状态双写** | K8s 资源既有 etcd 里的真实状态，又有 Terraform state 里的影子状态 | ArgoCD 只有一个真相源：**Git** |
| **漂移检测** | Terraform 要靠 refresh 发现漂移；ArgoCD **持续对比 Git 与集群**，天然是 GitOps 控制器 | ArgoCD 的设计目标就是持续拉平 |
| **回滚** | `terraform apply` 回滚 = 重新 apply 旧配置，**中间态不可控** | `argocd app rollback` = 切回某个历史 revision |
| **与现有 K8s 生态割裂** | Helm release、Ingress、CRD、operator 的状态 Terraform 都不认识 | Helm/ArgoCD 天然懂 Helm chart、CRD、operator |
| **谁在改** | 集群里 `kubectl edit` 改的东西，Terraform 下次 apply 会改回去 —— **和 GitOps 冲突** | GitOps 的纪律就是**集群里不能手改**（改了会被拉回） |
| **CI 语义** | Terraform 管 K8s 时，"Git 是唯一事实源"这条就不成立了 | 一致 |

**但要讲清楚边界上的灰区**（不装傻）：

| 场景 | 谁管 | 理由 |
|---|---|---|
| VPC / 子网 / 安全组 / SLB / RDS / OSS / RAM | **Terraform** | 这些是"集群外面的资源"，ArgoCD 管不了 |
| K8s 集群本身（ACK 托管集群、节点池） | **Terraform**（或云厂商控制台） | 同上 |
| **集群内的** Deployment / Service / Ingress / ConfigMap / Secret | **Helm + ArgoCD** | ArgoCD 的主场 |
| **集群内的** CRD、Operator | **Helm + ArgoCD** | ArgoCD 懂 CRD 和 health check |
| 集群需要的 DNS 记录、证书 | 视情况 | ArgoCD 的 DNS plugin 或 Terraform 均可，但**只能选一个**（否则双写） |

> **一句话记住**：**Terraform 管"云上有什么"，ArgoCD 管"集群里跑什么"。两者唯一的交集是 K8s 集群本身，而那应该由 Terraform 从外面建好。**

**一个反面案例**（真实存在的失败模式）：

```
① Terraform apply 创建 Deployment（连同 Service、Ingress）
② 有人在集群里 kubectl scale deploy --replicas=5（临时扩容救火）
③ Terraform 下次 apply → 把 replicas 改回 1
④ 或者：ArgoCD 也在管同一个 Deployment → 两套控制器互相打架
```

**正确做法**：集群内的资源**只让 ArgoCD 管**；临时扩容用 `kubectl scale` 之后**记得改 Git**，或者用 HPA（自动的，不需要人改）。

---

## 6. 踩坑与排障

> 下表报错/提示文字均为**官方文档、官方 issue 或社区中出现的典型文本**，标注了性质；本资料库未在环境实测，因此不写"实测输出"。

| 你看到的（症状） | 状态 | 真实原因 | 怎么修 | 下次怎么防 |
|---|---|---|---|---|
| `Error: No valid credential sources found for Alibaba Cloud Provider` 或类似认证失败 | plan 阶段就失败 | **provider 没有拿到凭据**：环境变量没设、profile 名不存在、或者在 CI 里 secret 没注入到这一步 | 本地 `export ALICLOUD_ACCESS_KEY=... ALICLOUD_SECRET_KEY=...`；CI 里检查 secret 的**变量名**和**注入范围**（是不是只注入了某些 job） | 在 CI 的 Terraform 步骤前加一条"凭据存在性检查"（只判断长度/是否为空，**不打印值**） |
| 资源建到了**意料之外的地域** | plan 显示的 region 不是你写的 | **region 没写**。阿里云帮助中心口径：provider 块和环境变量都没指定时**默认 `cn-beijing`** | 显式写 `region = "cn-hangzhou"`，或设 `ALICLOUD_REGION` | `region` 永远显式写，别依赖默认值；在 `terraform plan` 输出里核对地域 |
| `terraform init` 报 `Module not found` / `Failed to load module` | init 失败 | **相对路径写错**。`source` 的相对路径是**相对于调用它的模块**解析的，不是相对于你的 shell 当前目录 | 核对：根模块里写 `./modules/network` → 该目录必须在**根模块所在目录下**；嵌套模块里写 `./modules/x` → 相对于**那个嵌套模块目录** | 画一遍目录树再写 source；模块目录用 `terraform init` 后看 `.terraform/modules/modules.json` 里的 `Source` 字段核对 |
| `init` 报 `Module version constraint` / `Unsupported version` 或 plan 结果与预期完全不同 | init 或 plan 行为异常 | **模块版本不兼容**：① Registry 模块的 `version` 范围太宽，被拉到了大版本；② 大版本改了变量名/删了必填变量；③ 上游模块改了默认行为 | ① 把 `version` 收紧到 `~> x.y`；② 读该版本 changelog；③ 调 `version` 到旧版本再 `init` | **生产用 `~>` 不用 `>=`**；升级模块走 PR（改 version 约束 → plan 对比 → 合并），不直接在生产改 |
| 模块里必填变量报 `Missing required argument` | plan 报错 | 根模块调用时没传子模块的某个必填 variable；或上游大版本**改了变量名**（旧名失效、新名必填） | 看报错里的变量名；对照目标版本的 Inputs 列表补传 | 模块 README 里写清必填项；升级前先 `terraform plan` 在预发环境跑 |
| 改了 `for_each` 的键名，plan 显示资源被 destroy + create | plan 显示一批替换 | **资源地址变了**。`for_each` 实例地址是 `type.name["key"]`，键改名 = 换了身份 | 加 `moved` 块声明 `from`/`to`；或保持键不变 | **键用稳定的业务标识**（环境名、CIDR），改键前先加 `moved` |
| `terraform import` 成功，但 plan 仍显示 `will be created` | plan 要创建已存在的资源 | **配置和现实不一致**。常见三种原因：① 资源块的参数（`cidr_block`、`vpc_name`、tags）与现实不同 → Terraform 认为要"更新"或"替换"；② import 到了**错误的地址**（地址和 `resource` 块不匹配）；③ 用了 `count`/`for_each` 但 import 的地址没带实例键 | ① 逐字段对照现实与配置（先看 plan 的 diff 详情，是"替换"还是"更新"）；② 核对 import 的 ADDRESS 与 `resource` 块标签；③ 用带实例键的地址重新 import | import 后**立刻跑 plan**，diff 不为空就解决它，别放着；先 `terraform plan -refresh-only` 单独看漂移 |
| 报权限不足，但错误信息看起来跟权限无关（"资源不存在"/"参数非法"/"配额超限"） | 任意阶段失败 | **AK 权限不足**。阿里云 API 的错误码不直观，常伪装成别的语义 | 用 `sts:GetCallerIdentity` 确认当前身份；去 RAM 控制台确认策略已挂上且 Action 拼写正确；把策略里的 Action 逐条加上去定位是哪一条缺 | 部署用**受限 RAM 子账号 + AssumeRole**；把本篇 2.8.3 的最小权限清单作为起点；把 `sts:GetCallerIdentity` 写进策略 |
| plan 里出现大量"删除"操作 | plan 显示 destroy | **state 与现实严重不符**：state 里的资源在现实里已经不存在了（别人删了 / 换账号了 / 换 region 了） | **先别 apply**。用 `terraform plan -refresh-only` 看刷新后还剩什么；确认是"资源真没了"还是"我看错账号了" | apply 前**逐条读 diff 里的 destroy**，尤其确认不是"账号/region 变了"造成的误判 |
| `terraform init` 反复重新下载 provider / 提示 lock 文件变化 | init 输出提到 provider 版本变化 | `.terraform.lock.hcl` 记录了 provider 的版本与哈希。改 `required_providers` 的 `version` 约束 → 需要重新 init | 提交 `.terraform.lock.hcl` 到 Git；CI 里用 `terraform init -lockfile=readonly`（或保持一致）避免 CI 偷偷升级 provider | **provider 版本约束要严格**（`~>`），并且把 lock 文件进版本库 |
| CI 日志里出现完整的 AK/SK | 日志可见密钥 | ① 调试时 `echo $ALICLOUD_ACCESS_KEY`；② shell 开了 `set -x`；③ 把 `terraform output -json` 原始输出打进日志 | **立即轮换 AK**；改用"只判断是否存在"的检查；关闭 `set -x` 或给敏感命令加 `set +x` | CI 里给 Terraform 步骤加 `mask` 规则；**把 plan artifact 当密钥文件保管**（官方：plan 文件里的敏感数据是明文） |
| 用了 `alicloud_vpc` 但网段是 `172.16.0.0/12` | 资源建出来了但网段不对 | **`cidr_block` 是可选的，默认 `172.16.0.0/12`**（v1.119.0+）。漏写不报错 | 补上 `cidr_block`；已建的资源改 CIDR **会触发替换**（VPC CIDR 不可直接改），需重建 | **VPC 的 `cidr_block` 一律显式写**，并加 `validation` 校验格式 |
| 用 `alicloud_oss_bucket` + `alicloud_oss_bucket_policy` 时 policy 反复被还原 | plan 反复显示 policy 要改 | 官方明确：使用独立 OSS 子资源时，**必须在 `alicloud_oss_bucket` 上对相应属性配 `lifecycle.ignore_changes`**，否则 Terraform 会撤销子资源的修改 | 在 bucket 资源上加 `lifecycle { ignore_changes = [policy] }` | 组合使用 OSS 子资源前，先读 provider 该资源文档的 Import/注意事项段 |
| 模块里写了 `provider "alicloud" { access_key = ... }`，结果用了错误的账号 | 资源建到了别的账号 | 子模块**自己声明的 provider 配置**与父模块的 `providers` 映射是两回事；不映射时子模块用自己的 | 在父模块加 `providers = { alicloud = alicloud.<alias> }`；**并删掉模块内部的凭据声明** | 约定：**模块内部不写凭据**，凭据一律由父模块通过 `providers` 传入；模块内只写 `provider "alicloud" { region = var.region }` 这类非敏感项 |
| 切了 workspace 后 plan 出来的资源"全都不认识" | plan 显示大量 create | workspace 切的是 **state**，不是代码。切到新 workspace 就是一张白纸 | `terraform workspace select <name>` 后先 `terraform plan` 看清再建；确认这不是你想要的 | **不用 workspace 做环境隔离**（官方明确不建议）；改成"每环境一个目录 + 不同 backend key" |
| 模块在别人机器上跑不起来 | init 或 plan 报错 | ① 本地路径模块没提交进 Git；② `source` 用了绝对路径（官方明确不建议）；③ 模块目录里引用了模块外的相对路径 | 把 `modules/` 一起提交；改成 `./modules/x` 相对路径 | 模块做成**自包含**：`modules/xxx/` 里不引用 `../..`；发布成 Registry 模块后自然解决这个问题 |

---

## 7. 参考来源

| 编号 | 类型 | 文档 | 本篇用到的章节 | 访问状态 |
|---|---|---|---|---|
| S1 | 官方 | `developer.hashicorp.com/terraform/language/modules` | 模块总览、三种来源、root/child module 层级、开发/分发/使用三个阶段 | ✅ 2026-10-24 |
| S2 | 官方 | `developer.hashicorp.com/terraform/language/modules/develop` | 模块结构（variables/outputs/resources）、"好模块应提升抽象层级"、**不推荐只包单个资源的薄封装**、refactoring（`moved`）、Publishing modules | ✅ 2026-10-24 |
| S3 | 官方 | `developer.hashicorp.com/terraform/language/resources/syntax` | `provider` 元参数（alternate provider configuration）、`depends_on`、`lifecycle` | ✅ 2026-10-24 |
| S4 | 官方 | `developer.hashicorp.com/terraform/language/values/variables` | 变量定义与赋值优先级、敏感值语义（含 "still stores ... in the state"） | ✅ 2026-10-24 |
| S5 | 官方 | `developer.hashicorp.com/terraform/cli` | CLI 命令总入口（`init`/`plan`/`apply`/`import`/`output`/`login` 等子命令参考页为 S5 的子页） | ✅ 2026-10-24 |
| S6 | 官方 | `developer.hashicorp.com/terraform/language/state/workspaces` | workspace 定义、**官方警告"不适用于系统分解或需要独立凭据与访问控制的部署"**、支持多 workspace 的 backend 列表、`${terraform.workspace}` 插值 | ✅ 2026-10-24 |
| S7 | 官方 | `developer.hashicorp.com/terraform/language/backend/oss` | **OSS backend**：`access_key`/`secret_key`/`security_token`/`region`/`endpoint`/`bucket`（Required）/`prefix`/`key`/`tablestore_endpoint`/`tablestore_table`（**锁靠 TableStore，主键须为 `LockID` String**）/`encrypt`（**AES256**）/`acl`/`profile`/`shared_credentials_file`（**未指定时落到 `~/.aliyun/config.json`**）/`assume_role_*`（**`session_expiration` 取值范围 [900-3600]，默认 3600**）、`terraform_remote_state` 读远端 state | ✅ 2026-10-24 |
| S8 | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs` | alicloud provider 总体：provider 块参数（`access_key`/`secret_key`/`security_token`/`region`/`account_id`/`shared_credentials_file`/`profile`/`assume_role`/`ecs_role_name`/`endpoints`/`protocol`/超时）与数据源（`alicloud_images`/`alicloud_instance_types`/`alicloud_zones`/`alicloud_regions`） | ✅ 2026-10-24 |
| S8a | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs/resources/vpc` | `alicloud_vpc`：`vpc_name`（v1.119.0+）/ `name`（已废弃）、**`cidr_block` 可选且默认 `172.16.0.0/12`**、`secondary_cidr_blocks`（v1.185.0+ 废弃）、导入格式 `vpc-xxx` | ✅ 2026-10-24 |
| S8b | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs/resources/vswitch` | `alicloud_vswitch`：`vpc_id`/`cidr_block`/`zone_id` 均为 Required+ForceNew、`vswitch_name`（`name`/`availability_zone` 已废弃）、导入格式 `vsw-xxx` | ✅ 2026-10-24 |
| S8c | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs/resources/security_group` | `alicloud_security_group`：`description`/`vpc_id` ForceNew、`security_group_type`（normal/enterprise）、`inner_access_policy`（`inner_access` 已废弃）、导入格式 `sg-xxx` | ✅ 2026-10-24 |
| S8d | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs/resources/instance` | `alicloud_instance`：`image_id`/`instance_type`/`vswitch_id`/`security_groups`/`internet_charge_type`/`system_disk_category`/`instance_name`，以及 **VSwitch 与安全组必须同 VPC、VSwitchId 创建后不可改** 的官方 NOTE | ✅ 2026-10-24 |
| S8e | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs/resources/oss_bucket` | `alicloud_oss_bucket`：`bucket` 可选且 ForceNew、**`acl` 已废弃应改用独立 `alicloud_oss_bucket_acl`**、**使用独立子资源时必须在 bucket 上配 `lifecycle.ignore_changes`**、导入用 bucket 名 | ✅ 2026-10-24 |
| S8f | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs/resources/ram_user` | `alicloud_ram_user`：`name` 必填（1-64 字符、仅字母数字与 `-`/`.`/`_`、**不能以连字符开头**）、`display_name`/`mobile`/`email`/`comments`/`force`、导入用用户 ID | ✅ 2026-10-24 |
| S8g | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs/resources/ram_role` | `alicloud_ram_role`：导入**可用 ID 或名称** | ✅ 2026-10-24 |
| S8h | 官方 | `registry.terraform.io/providers/aliyun/alicloud/latest/docs/resources/ram_policy` | `alicloud_ram_policy`：导入**可用 ID 或名称** | ✅ 2026-10-24 |
| S9 | 中文/云厂商 | `help.aliyun.com/zh/terraform/terraform-authentication` + 阿里云开发者社区 | 阿里云 Terraform provider 的认证方式与优先级、**region 未指定时默认 `cn-beijing`**、生产环境推荐临时凭据（ECS RAM 角色 / OIDC / AssumeRole）、`ALIBABA_CLOUD_*` 与 `ALICLOUD_*` 两套环境变量名 | ✅ 2026-10-24 |
| S10 | 官方 | `developer.hashicorp.com/terraform/language/modules/develop/structure` | **标准模块目录结构**：根模块唯一必需、`README.md` 决定嵌套模块"对外可用 or 仅内部"、`modules/` 用相对路径调用、`examples/` 的 source 要写外部调用者会用的地址、变量与输出必须有 description | ✅ 2026-10-24 |
| S11 | 官方 | `developer.hashicorp.com/terraform/registry/modules/publish` | **发布到 Registry 的四条要求**（GitHub 公开仓、`terraform-<PROVIDER>-<NAME>` 命名、仓库描述、semver tag）、官方发布路径是网页 Upload + GitHub OAuth + tag webhook、新版本靠 tag webhook 自动上架 | ✅ 2026-10-24 |
| S12 | 官方 | `developer.hashicorp.com/terraform/language/modules/syntax` | **模块调用语法**：`source` 三类来源与 git `ref`/`depth`、`version` 仅 Registry 可用、`providers` 映射方向、模块上的 `count`/`for_each`、`source` 必须是字面量 | ✅ 2026-10-24 |
| S13 | 官方 | `developer.hashicorp.com/terraform/cli/commands/import` | `terraform import ADDRESS ID` 格式、模块/count/for_each 地址写法、**同一资源只能 import 到一个地址** | ✅ 2026-10-24 |
| S14 | 官方 | `developer.hashicorp.com/terraform/language/import` | **配置式导入 `import` 块**（config-driven import）、`import` 块 + `resource` 块必须同时写、`-generate-config-out` 自动生成配置 | ✅ 2026-10-24 |
| S15 | 官方 | `developer.hashicorp.com/terraform/cli/commands/login` | **`terraform login` 的真实语义是为 HCP Terraform / Terraform Enterprise 获取 API token**（明文存 `credentials.tfrc.json`），**不是公共 Registry 发布命令** | ✅ 2026-10-24 |
| S16 | 官方 | `developer.hashicorp.com/terraform/language/meta-arguments/for_each` | **键必须 plan 阶段已知**、键必须是字符串、不能用 impure 函数（uuid/bcrypt/timestamp）做键 | ✅ 2026-10-24 |
| S17 | 官方 | `developer.hashicorp.com/terraform/language/modules/develop/refactoring` | **`moved` 块（v1.1+）**：地址变更**不销毁资源**、可在 count/for_each 形态间迁移 | ✅ 2026-10-24 |

> **说明**：① Registry 公共模块的官方发布路径是"网页 Upload + GitHub OAuth + Git tag webhook"；**`terraform publish` 命令在当前官方 CLI 文档中查不到对应页面（实测 404）**，本篇不将其列为推荐路径，标 `⚠️ 待验证`。② `terraform login` 的官方语义见 S15 —— **为 HCP Terraform / Terraform Enterprise 获取 API token**，不是公共 Registry 发布命令。③ 本篇引用的 provider 资源文档页与 OSS backend 页已按 `09-参考索引/README.md` 的维护规则补录进索引（2026-10-24 实测 200）。
> **本篇的待验证清单**：① `terraform publish` 是否仍受支持；② 阿里云 API 权限不足时的具体错误码（官方 provider 文档无统一章节，属社区反馈）；③ 模块语义化版本细则（官方仅要求 tag 为 semver）。

---

## 8. 自测三问

1. **能说清输入输出吗？** —— 模块的输入是 `variable`（类型化的接口）+ 父模块传的 `providers` 映射；输出是 `output`（父模块用 `module.<标签>.<输出名>` 引用）。中间：根模块解析 → 加载子模块代码 → provider 认证（AK/profile/assume_role）→ 用子模块的变量渲染资源 → apply → state 记录 → output 回传父模块。
2. **能故意改坏再倒推吗？** —— 三个实验：① 把 `source = "./modules/network"` 改成 `"./modules/netwrok"`（拼错）→ `terraform init` 报 Module not found，**证明 source 路径错了**；② 把子模块 `variables.tf` 里的 `vpc_cidr` 的 `validation.condition` 改成 `false` → plan 报 `Invalid value for input variable` 并显示你的 error_message；③ 把根模块里 `providers = { alicloud = alicloud.bj }` 里的 `bj` 改成 `bjj` → init/plan 报找不到该 provider 配置，**证明 providers 映射的名字必须和 alias 对上**。
3. **能画上下游吗？** —— 画出「根模块 variable → module 块 → 子模块 variable → 子模块 resource → 子模块 output → 根模块 resource/output」的完整数据流，并在每条边上标出"这一环断了会看到什么"：变量没传 → Missing required argument；output 没声明 → 引用报错；providers 映射错 → 用错账号/空凭据；子模块没有 state → 父模块的 output 拿不到值。

---

**下一篇**：[`L4-ArgoCD-安装与同步`](L4-ArgoCD-安装与同步.md) · **上一篇**：[`L2-Terraform-核心概念与生命周期`](L2-Terraform-核心概念与生命周期.md)
