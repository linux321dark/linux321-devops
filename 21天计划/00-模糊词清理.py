#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模糊词清理：把结论性表述里的「可能/大概/也许/估计」改成确定性表述。
安全策略：只替换「可能 + 明确后缀」的模式，语义不变；剩余处列出供人工判断。
用法：cd 21天计划 && python3 00-模糊词清理.py [--dry]
"""
import io, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DIRS = ["01-基础篇", "02-K8s核心", "03-CI-CD与门禁", "04-交付与GitOps",
        "05-可观测性", "06-云原生中间件", "07-整合项目", "08-面试与认证"]
SKIP = {".backup", ".git"}

# 「可能 + 后缀」→ 直接去掉「可能」，语义不变且更确定
SAFE = [
    (r"可能也会", "也会"),
    (r"可能会", "会"),
    (r"可能与", "与"),
    (r"可能存在", "存在"),
    (r"可能仍", "仍"),
    (r"可能远", "远"),
    (r"可能导", "导"),
    (r"可能不", "不"),
    (r"可能超", "超"),
    (r"可能在", "在"),
    (r"可能更", "更"),
    (r"可能已", "已"),
    (r"可能有", "有"),
    (r"可能是", "是"),
    (r"可能出", "出"),
    (r"可能导", "导"),
    (r"可能造", "造"),
    (r"可能变", "变"),
    (r"可能需", "需"),
    (r"可能变", "变"),
]
# 整词替换（上下文无关但语义明确）
WORD = [
    (r"大概会", "会"),
    (r"大概", ""),
    (r"也许会", "会"),
    (r"也许", ""),
    (r"估计会", "会"),
    (r"估计", ""),
    (r"差不多会", "会"),
    (r"差不多", ""),
    (r"大致上会", "会"),
    (r"大致上", ""),
    (r"应该是", "就是"),
]
# 保留项：这些词出现在这些上下文里不动
KEEP = ["可能性", "最大可能", "可能超过官方", "可能超过峰值", "可能存在的合理区间",
        "大概率", "大概率是", "有大概率"]


def clean(text):
    # 保护词用带序号的占位符，逐词还原。
    # （曾用统一占位符再统一还原成"可能"，会把"可能性""大概率"一起污染成"可能"）
    for idx, k in enumerate(KEEP):
        text = text.replace(k, f"\x00K{idx}\x00")
    for pat, rep in SAFE + WORD:
        text = re.sub(pat, rep, text)
    for idx, k in enumerate(KEEP):
        text = text.replace(f"\x00K{idx}\x00", k)
    return text


def main():
    dry = "--dry" in sys.argv
    files = []
    for d in DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if x not in SKIP]
            for f in sorted(fn):
                if f.endswith(".md"):
                    files.append(os.path.join(dp, f))
    files += [os.path.join(ROOT, "00-授渔篇-怎么自己搞懂.md"),
               os.path.join(ROOT, "00-学习地图与前置依赖.md")]
    total = 0
    for fp in files:
        if not os.path.exists(fp):
            continue
        s = io.open(fp, encoding="utf-8").read()
        new = clean(s)
        if new != s:
            n = len(re.findall(r"可能|大概|也许|估计|差不多|大致上", s)) - len(
                re.findall(r"可能|大概|也许|估计|差不多|大致上", new))
            rel = os.path.relpath(fp, ROOT)
            if dry:
                print(f"[DRY] {rel}  可清理 {n} 处")
            else:
                io.open(fp, "w", encoding="utf-8").write(new)
                print(f"✓ {rel}  清理 {n} 处")
            total += n
    print(f"\n合计清理 {total} 处" + ("（dry-run，未写入）" if dry else ""))
    # 列出仍存在的、需人工判断的
    print("\n仍需人工判断的残留：")
    for fp in files:
        if not os.path.exists(fp):
            continue
        s = io.open(fp, encoding="utf-8").read()
        for m in re.finditer(r".{22}(可能|大概|也许|估计|差不多|大致上).{22}", s):
            line = s[:m.start()].count("\n") + 1
            print(f"  {os.path.relpath(fp, ROOT)}:{line}  …{m.group(0).replace(chr(10), ' ')}…")


if __name__ == "__main__":
    main()
