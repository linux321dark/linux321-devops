#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量把 21 篇日课的第四节改为"指向系统层教材"。
幂等：已插入过则更新内容块。
用法：cd 21天计划 && python3 00-日课指向生成.py
"""
import io, os, re

ROOT = os.path.dirname(os.path.abspath(__file__))

# D编号 -> (相对路径列表, 一句导读)
MAP = {
 1:  (["01-基础篇/L1-Docker-容器化基础.md", "01-基础篇/L2-Dockerfile与多阶段构建.md",
       "01-基础篇/L3-镜像仓库与供应链安全.md"],
      "容器与镜像的本质、分层与隔离三件套、你的两份 Dockerfile 逐行原理、镜像仓库与供应链"),
 2:  (["02-K8s核心/L1-K8s环境搭建与kubectl入门.md", "02-K8s核心/L2-K8s架构与对象模型.md",
       "02-K8s核心/L3-Deployment与滚动更新.md", "02-K8s核心/L8-弹性HPA.md"],
      "建集群三条路线、一次 apply 到 Running 的九步链路、滚动更新的默认值陷阱、HPA 的分母与延迟"),
 3:  (["02-K8s核心/L4-Service与网络.md", "02-K8s核心/L5-Ingress与配置管理.md"],
      "Service 四种类型与 selector 匹配、Ingress 为什么只是配置、Secret 的 base64 真相"),
 4:  (["03-CI-CD与门禁/L1-GitLabCI-流水线规范.md", "03-CI-CD与门禁/L2-Runner与缓存制品.md"],
      "`.gitlab-ci.yml` 规范、rules 与 needs、Runner 执行器、缓存与制品的分工"),
 5:  (["03-CI-CD与门禁/L3-质量门禁-SonarQube与Trivy.md", "03-CI-CD与门禁/L4-多环境策略与分支模型.md"],
      "质量门禁如何真正阻断流水线、SonarQube 与 Trivy 分工、多环境配置漂移"),
 6:  (["04-交付与GitOps/L1-Helm-模板化封装.md"],
      "Chart 结构、values 覆盖优先级、Release 的破坏性、Hooks 的坑"),
 7:  (["07-整合项目/L1-巨石应用云原生化.md"],
      "把前六天的内容串成一条完整改造路径，每阶段有验证方法与失败点"),
 8:  (["04-交付与GitOps/L2-Terraform-核心概念与生命周期.md"],
      "HCL 语法、生命周期命令、state 是什么与为什么必须远端管理"),
 9:  (["04-交付与GitOps/L3-Terraform-模块化与阿里云provider.md"],
      "模块三要素、provider alias、alicloud provider 实战、资源导入"),
 10: (["04-交付与GitOps/L4-ArgoCD-安装与同步.md"],
      "推模型 vs 拉模型、Application 的字段、同步三步 render→diff→apply"),
 11: (["04-交付与GitOps/L5-ArgoCD-策略与多集群.md"],
      "prune / selfHeal / sync-wave 的行为与风险、ApplicationSet 与多集群"),
 12: (["05-可观测性/L1-Prometheus-数据模型与采集.md"],
      "时间序列与标签模型、四种指标类型、exporter 机制、relabel 与 metric_relabel 的区别"),
 13: (["05-可观测性/L2-PromQL-实战.md", "05-可观测性/L3-Grafana-仪表盘与告警.md",
       "05-可观测性/L4-Loki-日志管道.md"],
      "PromQL 函数与常见坑、Grafana 变量与 provisioning、Loki 标签基数这个头号事故源"),
 14: (["07-整合项目/L2-GitOps与可观测闭环.md"],
      "把交付闭环与可观测闭环接成一个环，以及一次真实演练的设计"),
 15: (["04-交付与GitOps/L6-渐进式交付-ArgoRollouts.md"],
      "金丝雀 vs 蓝绿 vs 滚动、canary steps、Analysis 让灰度自动化"),
 16: (["06-云原生中间件/L1-Operator模式与CRD.md", "06-云原生中间件/L2-中间件Operator选型.md"],
      "Operator 本质、CRD 结构化 schema、什么时候该 Operator 什么时候不该"),
 17: (["02-K8s核心/L9-安全RBAC与合规.md", "01-基础篇/L3-镜像仓库与供应链安全.md"],
      "RBAC 最小权限、NetworkPolicy、Secret 加密与供应链四道防线、等保映射"),
 18: (["08-面试与认证/L1-CKA知识点速查.md"],
      "考纲知识域、kubectl 速查、etcd 与证书排障、模拟环境搭建"),
 19: (["08-面试与认证/L2-CKA全真模拟.md"],
      "限时策略、题型分类与配比、每类题的解题模板、完整模拟卷与复盘表"),
 20: (["08-面试与认证/L3-面试话术与STAR复盘.md"],
      "STAR 的详略分配、失败类问题怎么讲、你的九个真实素材逐条映射到 STAR"),
 21: ([], "本日不涉及新技术，专注投递与复盘"),
}

DAYS = {
 1: "第1周/第01天.md", 2: "第1周/第02天.md", 3: "第1周/第03天.md", 4: "第1周/第04天.md",
 5: "第1周/第05天.md", 6: "第1周/第06天.md", 7: "第1周/第07天.md",
 8: "第2周/第08天.md", 9: "第2周/第09天.md", 10: "第2周/第10天.md", 11: "第2周/第11天.md",
 12: "第2周/第12天.md", 13: "第2周/第13天.md", 14: "第2周/第14天.md",
 15: "第3周/第15天.md", 16: "第3周/第16天.md", 17: "第3周/第17天.md", 18: "第3周/第18天.md",
 19: "第3周/第19天.md", 20: "第3周/第20天.md", 21: "第3周/第21天.md",
}

MARK_START = "<!-- SYSTEM-LAYER-POINTER -->"
MARK_END = "<!-- /SYSTEM-LAYER-POINTER -->"


def build_block(d):
    files, desc = MAP[d]
    exist = [f for f in files if os.path.exists(os.path.join(ROOT, f))]
    if not exist:
        return None, []
    lines = []
    for f in exist:
        depth = f.count("/")
        rel = "../" * depth + f
        lines.append(f"> - [`{f.split('/')[-1]}`]({rel})")
    head = (f"> **本节是速查表，不是教材。** 想知道「{desc}」，先读：\n")
    tail = (">\n> 方法论：[`../00-授渔篇-怎么自己搞懂.md`](../00-授渔篇-怎么自己搞懂.md)"
            " · 来源规范：[`../09-参考索引/README.md`](../09-参考索引/README.md)\n")
    return head + "\n".join(lines) + "\n" + tail, exist


def main():
    changed, skipped = 0, 0
    for d, rel in sorted(DAYS.items()):
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        s = io.open(p, encoding="utf-8").read()
        block, exist = build_block(d)
        if block is None:
            print(f"  – D{d:<2} 目标教材尚未生成，跳过")
            skipped += 1
            continue
        newblock = MARK_START + "\n" + block + MARK_END
        if MARK_START in s:
            s = re.sub(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), newblock, s, flags=re.S)
            act = "更新"
        else:
            m4 = re.search(r"^## 四、.*$", s, re.M)
            if not m4:
                print(f"  ! D{d} 找不到 '## 四、' 锚点")
                continue
            pos = m4.end()
            s = s[:pos] + "\n\n" + newblock + s[pos:]
            act = "插入"
        io.open(p, "w", encoding="utf-8").write(s)
        print(f"  ✓ D{d:<2} {act}  指向 {len(exist)} 篇")
        changed += 1
    print(f"\n完成：{changed} 篇日课已更新，{skipped} 篇因目标教材未生成而跳过")


if __name__ == "__main__":
    main()
