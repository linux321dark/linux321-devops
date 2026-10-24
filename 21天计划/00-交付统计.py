#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成最终交付统计：每篇行数、来源条数、表格数、踩坑表行数、覆盖组件。
用法：cd 21天计划 && python3 00-交付统计.py
"""
import io, os, re, collections

ROOT = os.path.dirname(os.path.abspath(__file__))
DIRS = ["01-基础篇", "02-K8s核心", "03-CI-CD与门禁", "04-交付与GitOps",
        "05-可观测性", "06-云原生中间件", "07-整合项目", "08-面试与认证"]


def stat(fp):
    s = io.open(fp, encoding="utf-8").read()
    lines = s.split("\n")
    # 来源条数：数「## 7. 参考来源」表里 | Sn | 开头的行
    src = 0
    m7 = re.search(r"^## 7\..*$", s, re.M)
    if m7:
        rest = s[m7.end():]
        m2 = re.search(r"^## ", rest, re.M)
        seg = rest[:m2.start()] if m2 else rest
        for l in seg.split("\n"):
            if re.match(r"^\|\s*S\d+\s*\|", l):
                src += 1
    tbl = 0
    inb = False
    for l in lines:
        if l.strip().startswith("|"):
            if not inb:
                inb = True
                tbl += 1
        else:
            inb = False
    # 踩坑表行数：第 6 段所有表格的数据行合计（不含表头与分隔行）
    pit = 0
    m = re.search(r"^## 6\..*$", s, re.M)
    if m:
        rest = s[m.end():]
        m2 = re.search(r"^## ", rest, re.M)
        seg = rest[:m2.start()] if m2 else rest
        for l in seg.split("\n"):
            t = l.strip()
            if not t.startswith("|"):
                continue
            cells = t.strip("|").split("|")
            if len(cells) < 2:
                continue
            if all(set(c.strip()) <= set("-: ") for c in cells):
                continue      # 分隔行
            pit += 1
    return len(lines), src, tbl, pit


def main():
    total_files = total_lines = total_src = 0
    print("| 目录 | 篇目 | 行数 | 来源条数 | 表格数 | 踩坑表行数 |")
    print("|---|---|---|---|---|---|")
    for d in DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        fs = sorted((f for f in os.listdir(base) if re.match(r"^L\d", f) and f.endswith(".md")),
                    key=lambda x: int(re.match(r"^L(\d+)", x).group(1)))
        if not fs:
            continue
        dl = ds = dt = dp = 0
        for f in fs:
            l, sr, t, p = stat(os.path.join(base, f))
            dl += l; ds += sr; dt += t; dp += p
        total_files += len(fs); total_lines += dl; total_src += ds
        print(f"| `{d}/` | {len(fs)} | {dl:,} | {ds} | {dt} | {dp} |")
    print(f"| **合计** | **{total_files}** | **{total_lines:,}** | **{total_src}** | | |")
    print("\n### 逐篇明细\n")
    for d in DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        fs = sorted((f for f in os.listdir(base) if re.match(r"^L\d", f) and f.endswith(".md")),
                    key=lambda x: int(re.match(r"^L(\d+)", x).group(1)))
        if not fs:
            continue
        print(f"**`{d}/`**\n")
        print("| 篇目 | 行数 | 来源 | 表格 | 踩坑表 |")
        print("|---|---|---|---|---|")
        for f in fs:
            l, sr, t, p = stat(os.path.join(base, f))
            print(f"| `{f}` | {l:,} | {sr} | {t} | {p} |")
        print()


if __name__ == "__main__":
    main()
