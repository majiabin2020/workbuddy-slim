#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workbuddy-slim 体检扫描（只读，不改任何文件）

扫描 ~/.workbuddy 全局配置与当前工作区记忆，输出 markdown 体检报告到 stdout。
纯标准库，无网络请求。Python 3.9+。

用法：
    python3 slim_scan.py                 # 报告打印到 stdout
    python3 slim_scan.py --out 报告.md   # 同时写入文件
    python3 slim_scan.py --workspace /path/to/ws   # 指定工作区（默认当前目录）
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

RED, YELLOW, GREEN, GRAY = "🔴", "🟡", "🟢", "⚪"
RULE_FILE_YELLOW = 8 * 1024      # 单个规则文件 8KB
RULE_FILE_RED = 100 * 1024       # 单个规则文件 100KB
RULE_TOTAL_YELLOW = 40 * 1024    # 规则总量 40KB
RULE_TOTAL_RED = 120 * 1024      # 规则总量 120KB
WS_MEM_LIMIT = 3000              # 工作区 MEMORY.md 字数上限（约定）
USER_MEM_LIMIT = 4000            # 用户级 MEMORY.md 字数上限
SKILL_YELLOW, SKILL_RED = 20, 40
MCP_YELLOW = 6
LOG_DISTILL_DAYS = 30


def human(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


def char_count(p: Path) -> int:
    try:
        return len(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return -1


def section_rules(home: Path):
    rows, total = [], 0
    rules_dir = home / ".workbuddy" / "rules"
    if not rules_dir.is_dir():
        return rows, total, f"{GRAY} 未发现 rules 目录（新装用户，此项免检）"
    files = sorted([p for p in rules_dir.iterdir() if p.is_file() and not p.is_symlink()],
                   key=lambda p: p.stat().st_size, reverse=True)
    for p in files:
        size = p.stat().st_size
        total += size
        mark = RED if size > RULE_FILE_RED else (YELLOW if size > RULE_FILE_YELLOW else GREEN)
        rows.append((mark, f"`{p.name}` — {human(size)}"))
    if rules_dir.is_dir():
        syms = [p.name for p in rules_dir.iterdir() if p.is_symlink()]
        if syms:
            rows.append((GRAY, f"软链接文件 {len(syms)} 个（{', '.join(syms[:3])}…）不参与体积统计，也**不可移动**（可能被 dotfiles/同步工具引用）"))
    total_mark = RED if total > RULE_TOTAL_RED else (YELLOW if total > RULE_TOTAL_YELLOW else GREEN)
    verdict = f"{total_mark} 规则总量 {human(total)}"
    return rows, total, verdict


def section_memory(home: Path, ws: Path):
    rows = []
    # 用户级记忆（user-<uuid>-personal/MEMORY.md 可能不止一个，全部统计）
    user_dir = home / ".workbuddy"
    user_mems = sorted(user_dir.glob("user-*-personal/MEMORY.md"))
    for p in user_mems:
        n = char_count(p)
        mark = RED if n > USER_MEM_LIMIT * 2 else (YELLOW if n > USER_MEM_LIMIT else GREEN)
        rows.append((mark, f"用户级 `MEMORY.md` — {n} 字（上限 {USER_MEM_LIMIT}）"))
    # 工作区记忆
    ws_mem_dir = ws / ".workbuddy" / "memory"
    if ws_mem_dir.is_dir():
        ws_mem = ws_mem_dir / "MEMORY.md"
        if ws_mem.is_file():
            n = char_count(ws_mem)
            mark = RED if n > WS_MEM_LIMIT * 2 else (YELLOW if n > WS_MEM_LIMIT else GREEN)
            rows.append((mark, f"工作区 `MEMORY.md` — {n} 字（上限 {WS_MEM_LIMIT}）"))
        # 30 天以上旧日志
        today = date.today()
        cutoff = today - timedelta(days=LOG_DISTILL_DAYS)
        pat = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        old = []
        for f in ws_mem_dir.glob("*.md"):
            if pat.match(f.stem):
                try:
                    if date.fromisoformat(f.stem) < cutoff:
                        old.append(f)
                except ValueError:
                    pass
        if old:
            mark = RED if len(old) > 20 else YELLOW
            rows.append((mark, f"超过 {LOG_DISTILL_DAYS} 天未蒸馏的旧日志 **{len(old)} 个**（最早 {min(f.name for f in old)}），应蒸馏合并进 MEMORY.md 后归档"))
        else:
            rows.append((GREEN, f"无 {LOG_DISTILL_DAYS} 天以上未蒸馏旧日志"))
        arch = ws_mem_dir / "archive"
        if arch.is_dir():
            rows.append((GRAY, f"归档区已有 {len(list(arch.glob('*.md')))} 个旧日志（memory/archive/，正常）"))
    else:
        rows.append((GRAY, "当前目录不是 WorkBuddy 工作区（无 .workbuddy/memory/），工作区记忆项跳过"))
    return rows


def section_skills(home: Path, ws: Path):
    rows = []
    for label, base in [("用户级", home / ".workbuddy" / "skills"),
                        ("项目级", ws / ".workbuddy" / "skills")]:
        if base.is_dir():
            items = [p for p in base.iterdir() if p.is_dir() or p.is_symlink()]
            mark = RED if len(items) > SKILL_RED else (YELLOW if len(items) > SKILL_YELLOW else GREEN)
            rows.append((mark, f"{label}技能 **{len(items)} 个**（{base}）"))
    if not rows:
        rows.append((GRAY, "未发现已安装技能目录"))
    return rows


def section_mcp(home: Path):
    rows = []
    mcp_file = home / ".workbuddy" / "mcp.json"
    if not mcp_file.is_file():
        return [(GRAY, "未发现自定义 mcp.json（只有内置连接器，此项免检）")]
    try:
        data = json.loads(mcp_file.read_text(encoding="utf-8", errors="replace"))
        servers = list(data.get("mcpServers", {}).keys())
        mark = YELLOW if len(servers) > MCP_YELLOW else GREEN
        rows.append((mark, f"自定义 MCP **{len(servers)} 个**：{', '.join(servers) if servers else '（空）'}"))
        if "playwright" in servers:
            rows.append((YELLOW, "`playwright` 与 WorkBuddy 内置浏览器控制功能重复，可考虑移除（改前备份 mcp.json）"))
    except (json.JSONDecodeError, OSError) as e:
        rows.append((RED, f"mcp.json 解析失败：{e}（请检查该文件格式）"))
    return rows


def main():
    ap = argparse.ArgumentParser(description="workbuddy-slim 只读体检")
    ap.add_argument("--workspace", default=".", help="工作区根目录（默认当前目录）")
    ap.add_argument("--out", default=None, help="报告输出文件路径（可选）")
    args = ap.parse_args()

    home = Path.home()
    ws = Path(args.workspace).resolve()
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # ── ① 规则 ──
    rule_rows, rule_total, rule_verdict = section_rules(home)
    lines.append(f"# WorkBuddy 瘦身体检报告（{now}）\n")
    lines.append("> 只读扫描生成，未改动任何文件。字数为字符数，体积为磁盘字节。\n")
    lines.append(f"## ① 全局规则（rules 目录，每轮自动注入）\n")
    lines.append(f"**结论：{rule_verdict}**\n")
    for mark, txt in rule_rows:
        lines.append(f"- {mark} {txt}")
    lines.append("")

    # ── ② 记忆与日志 ──
    lines.append("## ② 记忆与工作日志\n")
    mem_rows = section_memory(home, ws)
    for mark, txt in mem_rows:
        lines.append(f"- {mark} {txt}")
    lines.append("")

    # ── ③ 技能 ──
    lines.append("## ③ 已安装技能\n")
    for mark, txt in section_skills(home, ws):
        lines.append(f"- {mark} {txt}")
    lines.append("")

    # ── ④ 连接器 ──
    lines.append("## ④ 自定义连接器（MCP）\n")
    for mark, txt in section_mcp(home):
        lines.append(f"- {mark} {txt}")
    lines.append("")

    # ── ⑤ 会话卫生（恒显示） ──
    lines.append("## ⑤ 会话卫生（习惯项，恒显示）\n")
    lines.append("- 🟡 新任务尽量开新会话，别在一个会话里串多件事；")
    lines.append("- 🟡 长会话及时压缩历史（上下文越滚越大，单任务越跑越慢）；")
    lines.append("- 🟡 轻量问题用轻量模式/快档位模型。\n")

    # ── 总结 ──
    mem_chars = 0
    for p in home.glob(".workbuddy/user-*-personal/MEMORY.md"):
        n = char_count(p)
        if n > 0:
            mem_chars += n
    ws_mem = ws / ".workbuddy" / "memory" / "MEMORY.md"
    if ws_mem.is_file():
        n = char_count(ws_mem)
        if n > 0:
            mem_chars += n
    lines.append("## 总结\n")
    lines.append(f"- 每轮固定注入的磁盘行李估算：规则 {human(rule_total)} + 记忆约 {mem_chars} 字 ≈ **{human(rule_total + mem_chars * 3)}**（中文按 3 字节/字折算）")
    lines.append("- 瘦身收益大头通常依次是：超重规则 → 旧日志蒸馏 → 技能/连接器精简。")
    lines.append("- 瘦身动作请在本技能第二阶段逐项确认后执行，**永不自动删除**。\n")

    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"[报告已写入 {args.out}]", file=sys.stderr)


if __name__ == "__main__":
    main()
