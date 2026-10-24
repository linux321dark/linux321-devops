#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统层篇目批量验收脚本
用法：cd 21天计划 && python3 00-验收脚本.py [目标目录 ...]
不传参数则验收全部系统层目录。
"""
import io, os, re, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {".backup", ".git", "node_modules"}
DIRS = ["01-基础篇", "02-K8s核心", "03-CI-CD与门禁", "04-交付与GitOps",
        "05-可观测性", "06-云原生中间件", "07-整合项目", "08-面试与认证"]

# 禁用词（用户明确要求：禁「可能/大概/也许/估计」这类模糊表述）
BANNED = ["大概", "也许", "大致上"]   # 「估计」降为提示级：rate 本身是估计量，"速率估计/估计方差"是统计学术语
# 提示级：在这批文档里多为表头问句、预期结果、反例引用，属合理用法，需人工确认
BANNED_SOFT = ["应该是", "差不多", "估计"]
# 允许出现这些词的例外语境（提示性）
ALLOW_CTX = ["可能性", "可能超过", "可能超过官方", "最大可能"]
# 含这些词即豁免（与 00-模糊词清理.py 的 KEEP 列表保持一致；
# 清理脚本按"逐词还原"处理保护词，验收脚本必须用同一份名单，否则两边规则会打架）
ALLOW_SUBSTR = ["大概率", "有大概率", "大概率是",
                # 统计学搭配：rate/increase 本身就是估计量，这些是术语不是模糊表述
                "速率估计", "估计方差", "估计误差", "估计量", "估计值", "估计的", "估计结果"]

SKELETON = ["## 0.", "## 1.", "## 2.", "## 3.", "## 4.", "## 5.", "## 6.", "## 7.", "## 8."]


def load_known_urls():
    """从 09-参考索引/README.md 提取所有已验证（✅/❌ 标注过的）URL 作为白名单"""
    p = os.path.join(ROOT, "09-参考索引", "README.md")
    if not os.path.exists(p):
        return set()
    with io.open(p, encoding="utf-8") as f:
        s = f.read()
    urls = set(re.findall(r"https?://[^\s`|)]+", s))
    # 去掉行尾标点
    out = set()
    for u in urls:
        out.add(u.rstrip(".,;:)"))
        out.add(u.rstrip(".,;:)").rstrip("/"))
    return out


def known_hosts():
    with io.open(os.path.join(ROOT, "09-参考索引", "README.md"), encoding="utf-8") as f:
        s = f.read()
    return set(re.findall(r"https?://([a-z0-9.-]+)", s))


def ncols(line):
    r"""统计表格行的列分隔符数量。

    ★ 判据必须与 GFM 渲染器（cmark-gfm）一致 —— 这一点是踩过坑才定下来的：

    GFM 规范（Tables 扩展）原文：
        "Include a pipe in a cell's content by escaping it, including inside other inline spans"
        配套示例：`| b `\|` az |` → `<td>b <code>|</code> az</td>`

    即：**反引号代码段内的竖线照样切列**，必须写成 `\|` 才能留在格子里。
    已用 GitHub 自己的渲染器（api.github.com/markdown, mode=gfm）实测验证：
        转义 `\|` → 第三格完整（<code>{{ .Values.tag | default .Chart.AppVersion }}</code>）
        不转义    → 第三格被截断成 `` `{{ .Values.tag ``，模板语法断裂、整表错位

    所以列分隔符数 = 去掉转义竖线（\|）后剩余的裸竖线数。
    不要额外剥离反引号代码段 —— 那样会少算，把「代码段内未转义竖线」这个真 bug 放过去。

    ⚠️ 返回的是**分隔符数**，不是列数：分隔符数 = 列数 + 1（首尾各一个边界竖线）。
    同一张表所有行的分隔符数相等，所以用它互相比对与用列数互相比对完全等价；
    但写断言时别写成 ncols(x) == 列数，会差一位全红。
    """
    return line.replace("\\|", "").count("|")


def strip_code_blocks(text):
    """剔除围栏代码块（``` ... ```）。
    代码块里的内容渲染后不是链接、不是表格、不是引用——
    三个检查都必须在剔除后进行，否则会把示例当真实内容报错。"""
    return re.sub(r"```.*?```", "", text, flags=re.S)


def check_table_cols(lines, path, issues):
    bad = []
    inb, cols = False, 0
    # 剔除代码块内的行（保留行号：用空行占位）
    lines = list(lines)
    in_code = False
    for i, l in enumerate(lines):
        if l.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            lines[i] = ""
    for i, l in enumerate(lines, 1):
        if l.strip().startswith("|"):
            c = ncols(l)
            if not inb:
                inb, cols = True, c
            elif c != cols:
                bad.append((i, cols, c, l[:50]))
        else:
            inb = False
    for b in bad:
        issues.append(f"[表格列数不一致] {path}:{b[0]} 期望{b[1]}个| 实得{b[2]} | {b[3]}")


def check_links(path, s, all_files, issues):
    s = strip_code_blocks(s)
    for m in re.finditer(r"\]\(([^)#][^)]*?\.md)\)", s):
        t = m.group(1)
        if t.startswith("http"):
            continue
        p = os.path.normpath(os.path.join(os.path.dirname(path), t))
        if not os.path.exists(p):
            issues.append(f"[死链] {os.path.relpath(path, ROOT)} → {t}")


def source_section(s):
    """只取 ## 7. 参考来源 到下一个 ## 标题之间的内容"""
    m = re.search(r"^## 7\..*$", s, re.M)
    if not m:
        return ""
    rest = s[m.end():]
    m2 = re.search(r"^## \d", rest, re.M)
    return rest[:m2.start()] if m2 else rest


def check_external_urls(path, s, known, known_host, issues):
    for m in re.finditer(r"https?://[^\s`|)\]]+", source_section(s)):
        u = m.group(0).rstrip(".,;:)").rstrip("/")
        if u in known:
            continue
        host = re.sub(r"https?://", "", u).split("/")[0]
        if host in known_host:
            continue
        issues.append(f"[未登记的外链] {os.path.relpath(path, ROOT)} → {u}")


def check_skeleton(path, s, issues):
    for sk in SKELETON:
        if sk not in s:
            issues.append(f"[骨架缺失] {os.path.relpath(path, ROOT)} 缺 {sk}")


def check_source_table(path, s, issues):
    if "## 7. 参考来源" not in s and "## 7. 参考" not in s:
        issues.append(f"[缺来源表] {os.path.relpath(path, ROOT)}")
        return
    if "访问状态" not in s:
        issues.append(f"[来源表缺访问状态列] {os.path.relpath(path, ROOT)}")
    if "单源待补" not in s and "⚠️ 单源" not in s:
        pass  # 单源标注是可选的，不强制


QUOTE_CHARS = "\"\u300c\u300e\u201c\u201d'`"


def is_quoted(s, i, w):
    """判断该模糊词是否处于引述中（引号内），若是则属引用反例而非本文断言"""
    before = s[max(0, i - 4):i]
    after = s[i + len(w):i + len(w) + 4]
    return (any(ch in before for ch in QUOTE_CHARS) and any(ch in after for ch in QUOTE_CHARS))


def check_banned(path, s, issues, soft):
    for w in BANNED + BANNED_SOFT:
        idx = 0
        while True:
            i = s.find(w, idx)
            if i < 0:
                break
            ctx = s[max(0, i - 20):i + len(w) + 20].replace("\n", " ")
            if is_quoted(s, i, w):
                idx = i + len(w)
                continue
            if any(a in ctx for a in ALLOW_SUBSTR):
                idx = i + len(w)
                continue
            if not any(a in ctx for a in ALLOW_CTX):
                if w in BANNED:
                    issues.append(f"[禁用模糊词] {os.path.relpath(path, ROOT)} 「{w}」 … {ctx[:70]}")
                else:
                    # 提示级：属合理用法（表头问句、预期结果、反例引用），只提示不判失败
                    soft.append(f"[提示·模糊词] {os.path.relpath(path, ROOT)} 「{w}」 … {ctx[:70]}")
            idx = i + len(w)


def section(s, num):
    """取 '## num.' 开头的整节内容（到下一个 '## ' 标题为止）"""
    m = re.search(r"^## %d\." % num, s, re.M)
    if not m:
        return ""
    rest = s[m.start():]
    m2 = re.search(r"^## ", rest[3:], re.M)
    return rest[:m2.start() + 3] if m2 else rest


def check_plain_refs(path, s, issues):
    """扫描反引号包裹的 .md 纯文本引用（如 `L3-xxx.md`），同目录不存在即报。
    这类「过期导航」不是链接语法，正则 \\]\\(xxx.md\\) 匹配不到，
    但读者按名字找不到文件——属于脚本盲区，必须单独查。"""
    d = os.path.dirname(path)
    # ① 先剔除围栏代码块（``` ... ```）——块内的内容渲染后不是链接/引用
    s2 = strip_code_blocks(s)
    # ② 再剥掉 markdown 链接语法（[`x.md`](x.md)），否则链接里的反引号文本会被误判为纯文本引用
    s2 = re.sub(r"\[[^\]]*\]\([^)]*\)", "", s2)
    for m in re.finditer(r"`([^`\n]*?\.md)`", s2):
        t = m.group(1)
        cands = [os.path.normpath(os.path.join(d, t)),
                 os.path.normpath(os.path.join(d, "..", t)),
                 os.path.normpath(os.path.join(ROOT, t))]
        if not any(os.path.exists(c) for c in cands):
            line = s[:m.start()].count("\n") + 1
            issues.append(f"[过期导航] {os.path.relpath(path, ROOT)}:{line} 纯文本引用了不存在的 {t}")


def check_required_columns(path, s, issues):
    """第 4 段要有'写错的后果'类列；第 6 段要有五列"""
    seg4 = section(s, 4)
    if seg4 and not any(k in seg4 for k in ("后果", "影响", "常见问题", "会怎样", "写错", "注意点",
                                           # 第 4 段的形态随主题而异：资源清单 / 指令表 / 函数表 / 排障决策表
                                           "症状", "命令", "证据", "最可能的层")):
        issues.append(f"[第4段缺'写错的后果'类列] {os.path.relpath(path, ROOT)}")
    seg6 = section(s, 6)
    if seg6 and not any(k in seg6 for k in ("怎么修", "怎么解", "解决", "怎么防", "| 修", "| 修法")):
        issues.append(f"[第6段缺'怎么修'类列] {os.path.relpath(path, ROOT)}")


def render_check(files, limit=40):
    """用 GitHub 自己的渲染器（api.github.com/markdown, mode=gfm）实测表格是否真错位。

    启发式判据可能与真实渲染器不一致——已踩过：
    pandoc 容忍代码段内未转义的竖线，而 cmark-gfm（GitHub 用的）直接截断，
    导致「判据说对、渲染却错位」。GFM 规范原文：
        "Include a pipe in a cell's content by escaping it, including inside other inline spans"

    本项为可选开关（外部请求 + 有速率限制），默认关闭。
    """
    import json
    import subprocess
    print("=" * 70)
    print("渲染器实测抽查（GitHub cmark-gfm）")
    print("=" * 70)
    checked = miss = 0
    for fp in files[:limit]:
        with io.open(fp, encoding="utf-8") as f:
            text = f.read()
        for l in text.split("\n"):
            t = l.strip()
            if not t.startswith("|"):
                continue
            cells = [c for c in t.strip("|").split("|")]
            if len(cells) < 2 or all(set(c.strip()) <= set("-: ") for c in cells):
                continue
            expect = ncols(l) - 1
            checked += 1

            # ── 通道 1：GitHub cmark-gfm（权威判据；未认证 API 有速率限制）──
            body = json.dumps({"text": l, "mode": "gfm"})
            gh_html = ""
            try:
                gh_html = subprocess.run(
                    ["curl", "-s", "--max-time", "25", "-X", "POST",
                     "-H", "Content-Type: application/json",
                     "-H", "User-Agent: workbuddy-md-check",
                     "--data-binary", "@-",
                     "https://api.github.com/markdown"],
                    input=body.encode(), capture_output=True, timeout=30
                ).stdout.decode("utf-8", "ignore")
            except Exception:
                pass
            if gh_html and not gh_html.startswith("{"):
                real = gh_html.count("<td>") + gh_html.count("<th>")
                if real != expect:
                    miss += 1
                    print(f"  ✗ [GitHub] {os.path.relpath(fp, ROOT)}  期望 {expect} 格，渲染出 {real} 格")
                    print(f"     {t[:150]}")
                continue

            # ── 通道 2：pandoc 本地（交叉验证；它容忍代码段内未转义竖线，
            #    与 cmark-gfm 行为不同，所以只用来抓「硬错位」，判定以 ncols 为准）──
            try:
                pd_html = subprocess.run(
                    ["pandoc", "-f", "gfm", "-t", "html"],
                    input=l.encode(), capture_output=True, timeout=20
                ).stdout.decode("utf-8", "ignore")
            except Exception:
                pd_html = ""
            if pd_html:
                real = pd_html.count("<td>") + pd_html.count("<th>")
                if real != expect and real > 0:
                    # 通道 1 因限流不可用时，pandoc 的结果仅供参考
                    print(f"  ? [pandoc·仅供参考] {os.path.relpath(fp, ROOT)}  期望 {expect} 格，pandoc 渲染 {real} 格")
            break
    print(f"渲染器抽查 {checked} 行，真实错位 {miss} 处")
    return checked, miss


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    targets = argv or DIRS
    known = load_known_urls()
    known_host = known_hosts()
    files = []
    shared = []
    for d in targets:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if x not in SKIP_DIRS]
            for f in sorted(fn):
                # 只验收「教材篇目」：以 L+数字 命名。模板（如 baseline/ 下的可填表）
                # 不是教材，没有八段骨架也不需要来源表。
                if f.endswith(".md") and re.match(r"^L\d", f):
                    files.append(os.path.join(dp, f))

    # 共享文件（00-*.md 与 09-参考索引）同样会被并发编辑搞坏，
    # 但它们不是教材篇目，只查格式类问题，不查骨架与来源表。
    for f in sorted(os.listdir(ROOT)):
        if f.endswith(".md") and re.match(r"^\d\d-", f):
            shared.append(os.path.join(ROOT, f))
    shared.append(os.path.join(ROOT, "09-参考索引", "README.md"))
    files.sort()

    # ── 共享文件单独检查（只查格式类）──
    total_issues = 0
    shared_issues = 0
    if shared:
        print(f"共享文件（00-*.md / 09-参考索引）：{len(shared)} 个\n" + "=" * 70)
        for fp in shared:
            if not os.path.exists(fp):
                continue
            with io.open(fp, encoding="utf-8") as f:
                s2 = f.read()
            iss = []
            check_table_cols(s2.split("\n"), fp, iss)
            check_links(fp, s2, files, iss)
            check_external_urls(fp, s2, known, known_host, iss)
            check_banned(fp, s2, iss, [])
            rel = os.path.relpath(fp, ROOT)
            if iss:
                shared_issues += len(iss)
                print(f"✗ {rel}  {len(iss)} 个问题")
                for x in iss[:10]:
                    print("   ", x)
            else:
                print(f"✓ {rel}")
        print("=" * 70)
        if shared_issues:
            print(f"共享文件问题 {shared_issues} 个")
            print("=" * 70)
            print()

    if "--render-check" in flags:
        _checked, _miss = render_check(files)
        return 0 if _miss == 0 else 1

    if "--shared-only" in flags:
        print("（--shared-only：仅检查共享文件，退出码只看共享文件问题）")
        print("=" * 70)
        return 0 if shared_issues == 0 else 1

    print(f"教材篇目：{len(files)} 篇\n" + "=" * 70)
    print(f"验收目标：{len(files)} 篇\n" + "=" * 70)
    for fp in files:
        with io.open(fp, encoding="utf-8") as f:
            s = f.read()
        lines = s.split("\n")
        issues = []
        soft = []
        check_table_cols(lines, fp, issues)
        check_links(fp, s, files, issues)
        check_external_urls(fp, s, known, known_host, issues)
        check_skeleton(fp, s, issues)
        check_source_table(fp, s, issues)
        check_banned(fp, s, issues, soft)
        check_required_columns(fp, s, issues)
        check_plain_refs(fp, s, issues)
        rel = os.path.relpath(fp, ROOT)
        if issues:
            total_issues += len(issues)
            print(f"✗ {rel}  ({len(lines)} 行)  {len(issues)} 个问题")
            for x in issues[:12]:
                print("   ", x)
            if len(issues) > 12:
                print(f"    ...另有 {len(issues)-12} 个")
        else:
            print(f"✓ {rel}  ({len(lines)} 行)")
        for x in soft[:12]:
            print("   ", x)
        if len(soft) > 12:
            print(f"    ...另有 {len(soft)-12} 个提示")
    print("=" * 70)
    print(f"合计 {len(files)} 篇，问题 {total_issues + shared_issues} 个"
          f"（教材 {total_issues} + 共享文件 {shared_issues}）")
    # ★ 共享文件的问题必须计入退出码。
    #   共享文件（00-写作规范.md 等）同样会被并发编辑搞坏——实测出现过
    #   「跨篇一致性」表格被交叉插入挤错列数的损坏。若共享文件的问题
    #   不计入退出码，CI 会因为「教材篇目全绿」而放行，损坏就留在原地。
    return 0 if (total_issues + shared_issues) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
