#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
扫描系统层目录，生成 README 里的状态表片段 + 学习地图的篇目清单。
用法：cd 21天计划 && python3 00-状态表生成.py [--print]
输出 markdown 片段，直接粘进 README。
"""
import io, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
GROUPS = [
    ("01-基础篇", "Linux/Shell/YAML/网络 · Docker", "L0 前置 · L1 容器化 · L2 多阶段构建 · L3 仓库与供应链"),
    ("02-K8s核心", "K8s 架构·工作负载·网络·配置·存储·调度·安全·弹性·排障", "L1 上手 · L2 架构 · L3 部署 · L4 网络 · L5 入口与配置 · L6 存储 · L7 调度 · L8 弹性 · L9 安全 · L10 排障"),
    ("03-CI-CD与门禁", "GitLab CI · Runner · SonarQube · Trivy · 多环境", "L1 流水线规范 · L2 Runner 与缓存 · L3 质量门禁 · L4 多环境策略"),
    ("04-交付与GitOps", "Helm · Terraform · ArgoCD · Argo Rollouts", "L1 Helm · L2 TF 核心 · L3 TF 模块化与阿里云 · L4 ArgoCD 安装 · L5 策略与多集群 · L6 渐进式交付"),
    ("05-可观测性", "Prometheus · PromQL · Grafana · Loki · Alertmanager · OTel", "L1 Prometheus · L2 PromQL · L3 Grafana · L4 Loki · L5 告警与 OTel"),
    ("06-云原生中间件", "Operator 模式 · CRD · 中间件 Operator 选型", "L1 Operator 与 CRD · L2 中间件选型"),
    ("07-整合项目", "巨石应用云原生化 · GitOps+可观测闭环", "L1 巨石应用改造 · L2 双闭环整合"),
    ("08-面试与认证", "CKA 速查 · CKA 模拟 · 面试话术", "L1 CKA 速查 · L2 全真模拟 · L3 面试话术与 STAR"),
]


def scan(d):
    p = os.path.join(ROOT, d)
    if not os.path.isdir(p):
        return []
    out = []
    for f in os.listdir(p):
        if f.endswith(".md"):
            n = len(io.open(os.path.join(p, f), encoding="utf-8").read().split("\n"))
            out.append((f, n))
    # 按 L 编号数值排序（字符串排序会把 L10 排到 L1 之后，导致范围标注错误）
    out.sort(key=lambda x: (int(re.match(r"^L(\d+)", x[0]).group(1)) if re.match(r"^L(\d+)", x[0]) else 999, x[0]))
    return out


def main():
    total_lines = 0
    rows = []
    for d, scope, outline in GROUPS:
        files = scan(d)
        if not files:
            rows.append(f"| `{d}/` | {scope} | {outline} | 0 | ⬜ 待写 |")
            continue
        lines = sum(n for _, n in files)
        total_lines += lines
        first = os.path.basename(files[0][0]).split("-")[0]
        last = os.path.basename(files[-1][0]).split("-")[0]
        rng = f"{first}–{last}" if first != last else first
        rows.append(f"| `{d}/` | {scope} | {rng} | {len(files)} | ✅ {lines:,} 行 |")
    print("| 目录 | 覆盖组件 | 篇目 | 篇数 | 状态 |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(r)
    print(f"\n合计 {sum(len(scan(d)) for d, _, _ in GROUPS)} 篇 / {total_lines:,} 行")
    # 逐篇明细
    print("\n### 逐篇清单\n")
    for d, _, _ in GROUPS:
        files = scan(d)
        if not files:
            continue
        print(f"**`{d}/`**\n")
        for f, n in files:
            print(f"- `{f}` — {n} 行")
        print()


if __name__ == "__main__":
    main()
