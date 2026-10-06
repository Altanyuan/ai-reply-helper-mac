#!/usr/bin/env python3
"""探针①：macOS 微信「能读到什么」能力盘点。

回答三个问题
------------
  1. 本机权限给没给（辅助功能 / 屏幕录制），以及授权后要不要重启
  2. 微信窗口能不能枚举到（owner 名字 / 标题 / 尺寸 / 层级 / 是否在屏）
  3. 微信的辅助功能(AX)树里到底有没有聊天文本
     —— 输出 role 直方图 + 样本文本，用来判断「AX 直读」这条路值不值得走

背景
----
同类项目（jev-chat-jarvis-mac）为微信选了「截图 + OCR」，为 QQ（Electron）
选了「AX 直读」。本探针就是要把这个判断在**你的机器、你的微信版本**上复核一遍，
避免我们照着别人的结论盲目砍掉一条路。

用法
----
    python3 probe/mac_capability_probe.py                    # 默认跑一遍
    python3 probe/mac_capability_probe.py --request-permissions
    python3 probe/mac_capability_probe.py --json /tmp/cap.json
    python3 probe/mac_capability_probe.py --all-windows      # 列全部窗口找微信叫什么

产物
----
    probe/probe_out/capability.json     机器可读报告

注意
----
未授予辅助功能权限时，AX 部分会标记为 skipped 并正常退出，不会报错。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _probe_lib as L  # noqa: E402

from collections import Counter  # noqa: E402


# ---------------------------------------------------------------------------
# AX 探测
# ---------------------------------------------------------------------------

def probe_ax_window(win, max_nodes: int, max_depth: int) -> dict:
    """遍历一个 AX 窗口，产出 role 直方图、文本样本、候选输入框。"""
    from ApplicationServices import kAXTitleAttribute

    roles = Counter()
    texts = []
    text_nodes = 0
    nodes = 0
    largest_area = None          # 最大的 AXTextArea —— 微信输入框的候选

    for el, _depth, role in L.ax_walk(win, max_nodes=max_nodes, max_depth=max_depth):
        nodes += 1
        roles[role] += 1
        if role in L.AX_TEXT_ROLES:
            t = L.ax_text(el)
            if t:
                text_nodes += 1
                if len(texts) < 40:
                    texts.append(t)
        if role == "AXTextArea":
            fr = L.ax_frame(el)
            if fr:
                area = fr[2] * fr[3]
                if largest_area is None or area > largest_area[0]:
                    largest_area = (area, fr)

    return {
        "title": L.ax_attr(win, kAXTitleAttribute) or "",
        "frame": L.ax_frame(win),
        "walked_nodes": nodes,
        "role_histogram": dict(roles.most_common(15)),
        "text_node_count": text_nodes,
        "sample_texts": texts[:25],
        "largest_text_area": (
            {"x": largest_area[1][0], "y": largest_area[1][1],
             "w": largest_area[1][2], "h": largest_area[1][3]}
            if largest_area else None
        ),
    }


def probe_ax(pid: int, max_windows: int = 4) -> dict:
    """对微信进程做 AX 探测。"""
    wins = L.ax_windows(pid)
    if not wins:
        return {"ok": False, "reason": "kAXWindows 为空：该进程未暴露 AX 窗口，或权限未生效"}
    out = []
    for i, win in enumerate(wins[:max_windows]):
        try:
            info = probe_ax_window(win, L.AX_MAX_NODES, L.AX_MAX_DEPTH)
        except Exception as e:
            info = {"error": f"{type(e).__name__}: {e}"}
        info["window_index"] = i
        out.append(info)
    return {"ok": True, "windows": out}


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def print_env(env: dict) -> None:
    L.hr("环境")
    print(f"  macOS        : {env['macos']}")
    print(f"  Python       : {env['python']}  ({env['machine']}"
          f"{'，正运行在 Rosetta 下' if env['rosetta'] else ''})")


def print_permissions(ax, sc) -> None:
    L.hr("权限")

    def mark(v):
        return "✓ 已授予" if v is True else ("✗ 未授予" if v is False else "? 无法检测（老系统）")

    print(f"  辅助功能（读窗口/填文字）: {mark(ax)}")
    print(f"  屏幕录制（截图 + OCR）    : {mark(sc)}")

    if ax is False or sc is False:
        print()
        print("  去这里开：系统设置 › 隐私与安全性 › 辅助功能 / 录屏与系统录音")
        print("  · 辅助功能：授权后**立即生效**，不用重启")
        print("  · 屏幕录制：授权后**必须退出并重开本程序**才生效（系统限制）")
        print("  · 本脚本可加 --request-permissions 触发系统授权框")


def print_windows(windows: list) -> None:
    L.hr("微信窗口")
    if not windows:
        print("  没有枚举到微信窗口。")
        print("  · 请确认微信已启动并登录、至少打开一个聊天窗口")
        print("  · 若不确定微信在系统里的名字，加 --all-windows 列出全部窗口")
        return
    print(f"  共 {len(windows)} 个：")
    for w in windows:
        flag = "在屏" if w["onscreen"] else "离屏"
        print(f"    wid={w['wid']:<10} layer={w['layer']:<3} {w['w']}x{w['h']}"
              f" @ ({w['x']},{w['y']})  [{flag}]"
              f"  owner={w['owner']!r}  title={w['title']!r}")


def print_ax(res: dict) -> None:
    L.hr("辅助功能(AX)树探测")
    if not res.get("ok"):
        print(f"  跳过：{res.get('reason')}")
        return
    for w in res["windows"]:
        print(f"  ── 窗口 #{w['window_index']}  title={w.get('title')!r}  frame={w.get('frame')}")
        if "error" in w:
            print(f"     遍历出错：{w['error']}")
            continue
        print(f"     遍历节点数={w['walked_nodes']}  含文本节点={w['text_node_count']}")
        print(f"     role 直方图（前 15）: {w['role_histogram']}")
        ta = w.get("largest_text_area")
        print(f"     最大 AXTextArea（输入框候选）: {ta}")
        if w["sample_texts"]:
            print("     样本文本（前 10 条）:")
            for t in w["sample_texts"][:10]:
                print(f"       · {t[:80]!r}")
        else:
            print("     样本文本：无 —— 该窗口的 AX 树里没有任何可读文本")


def judge(ax_trusted, sc_allowed, windows, ax_res) -> list:
    """给出结论与下一步建议（列表形式，逐条打印）。"""
    lines = []

    if not windows:
        lines.append("✗ 没枚举到微信窗口 —— 先让微信跑起来、开个聊天窗口，再跑一次。")
        return lines

    if ax_trusted is False:
        lines.append("· 辅助功能未授权 → 无法判断 AX 通道。授权后重跑本脚本一次。")
    elif ax_res.get("ok"):
        total_text = sum(w.get("text_node_count", 0) for w in ax_res["windows"])
        biggest = max((w.get("walked_nodes", 0) for w in ax_res["windows"]), default=0)
        if total_text >= 15:
            lines.append(f"✓ AX 树里有 {total_text} 个含文本节点 → **AX 直读通道可能可用**，"
                         f"值得再验证样本文本是否真的是聊天内容。")
        elif biggest > 50:
            lines.append(f"△ AX 树能遍历（{biggest} 节点）但含文本节点只有 {total_text} 个 → "
                         f"AX 通道大概率**读不到聊天消息**，应走截图 + OCR。")
        else:
            lines.append(f"✗ AX 树几乎是空的（最大 {biggest} 节点）→ 微信不向系统暴露控件，"
                         f"**确认走截图 + OCR**。")

    if sc_allowed is False:
        lines.append("· 屏幕录制未授权 → 截图会失败或得到小文件。授权后**退出重开**再跑。")
    elif sc_allowed is True:
        lines.append("✓ 屏幕录制已授权 → 可以直接跑探针②做端到端验证：")
    else:
        lines.append("? 屏幕录制状态无法检测（老系统）→ 直接跑探针②，看截图能不能出来。")

    lines.append("      python3 probe/mac_capture_probe.py --delay 5")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="macOS 微信能力盘点探针（权限 / 窗口 / AX 树）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--json", default=None, help="JSON 报告输出路径（默认 probe/probe_out/capability.json）")
    ap.add_argument("--request-permissions", action="store_true",
                    help="主动弹出系统授权框（辅助功能 + 屏幕录制）")
    ap.add_argument("--all-windows", action="store_true",
                    help="列出全部窗口（用于确认微信在系统里的 owner 名字）")
    ap.add_argument("--owner", action="append", default=None,
                    help="自定义微信 owner 名字（可重复；默认 WeChat/微信/Weixin）")
    args = ap.parse_args(argv)

    if not L.ensure_deps():
        return 2

    env = L.env_info()
    print_env(env)

    if args.request_permissions:
        L.hr("申请权限")
        print("  正在弹出系统授权框…")
        L.request_accessibility()
        sc = L.request_screen_capture()
        print(f"  屏幕录制申请返回：{sc}")
        print("  请到「系统设置 › 隐私与安全性」确认开关已打开；"
              "**屏幕录制授权后需要退出重开本进程**。")

    ax_trusted = L.accessibility_trusted()
    sc_allowed = L.screen_capture_allowed()
    print_permissions(ax_trusted, sc_allowed)

    owners = tuple(args.owner) if args.owner else L.WECHAT_OWNER_NAMES
    if args.all_windows:
        L.hr("全部窗口（供确认 owner 名字）")
        allw = L.list_windows(owners=None)
        seen = {}
        for w in allw:
            seen.setdefault(w["owner"], 0)
            seen[w["owner"]] += 1
        for owner, cnt in sorted(seen.items(), key=lambda x: -x[1])[:40]:
            print(f"    {owner!r}  ×{cnt}")
        print()
        print("  微信的 owner 通常是 'WeChat' 或 '微信'；"
              "若上面看不到，请把微信窗口切到前台再跑。")

    windows = L.list_windows(owners=owners)
    print_windows(windows)

    pids = sorted({w["pid"] for w in windows})
    ax_res = {"ok": False, "reason": "没有微信进程"}
    if pids:
        if ax_trusted is False:
            ax_res = {"ok": False, "reason": "辅助功能未授权"}
        else:
            ax_res = probe_ax(pids[0])
    print_ax(ax_res)

    L.hr("结论与下一步")
    for line in judge(ax_trusted, sc_allowed, windows, ax_res):
        print("  " + line)

    report = {
        "env": env,
        "permissions": {
            "accessibility_trusted": ax_trusted,
            "screen_capture_allowed": sc_allowed,
        },
        "wechat_windows": windows,
        "wechat_pids": pids,
        "ax_probe": ax_res,
    }
    out = args.json or (L.default_outdir() / "capability.json")
    L.hr("报告")
    print(f"  已写入：{L.save_json(out, report)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
