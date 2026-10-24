#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
最终收尾：一键完成
  1. 全量验收（表格/死链/过期导航/骨架/来源表/模糊词）
  2. 21 篇日课的指向块刷新（幂等）
  3. README 状态表刷新
  4. 学习地图「完整篇目索引」状态刷新
用法：cd 21天计划 && python3 00-最终收尾.py
"""
import io, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3"


def run(script, *args):
    return subprocess.run([PY, script, *args], capture_output=True, text=True, cwd=ROOT)


def refresh_readme():
    p = os.path.join(ROOT, "README.md")
    s = io.open(p, encoding="utf-8").read()
    out = run("00-状态表生成.py").stdout
    newtab = out.split("### 逐篇清单")[0].strip()
    i = s.find("| 目录 | 覆盖组件 |")
    if i < 0:
        return "未找到状态表，未改动"
    j = s.find("\n\n", i)
    s = s[:i] + newtab + s[j:]
    io.open(p, "w", encoding="utf-8").write(s)
    return newtab


def main():
    print("=" * 72)
    print("① 全量验收")
    print("=" * 72)
    r = run("00-验收脚本.py")
    print(r.stdout[-3000:])
    tail = r.stdout.strip().split("\n")[-1]

    print()
    print("=" * 72)
    print("② 21 篇日课指向刷新")
    print("=" * 72)
    r2 = run("00-日课指向生成.py")
    print(r2.stdout.strip().split("\n")[-1])

    print()
    print("=" * 72)
    print("③ README 状态表刷新")
    print("=" * 72)
    tab = refresh_readme()
    print(tab)

    print()
    print("=" * 72)
    print(f"收尾完成 · {tail}")
    print("=" * 72)


if __name__ == "__main__":
    main()
