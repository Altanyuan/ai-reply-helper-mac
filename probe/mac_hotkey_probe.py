#!/usr/bin/env python3
"""探针③：macOS 全局热键诊断。

干什么
------
起一个 pynput 键盘监听，把**你按下的每一个键原样打印出来**，并判断它
「能不能匹配成目标热键」。用来回答那个最难猜的问题：

    程序在跑、权限也给了，为什么按热键没反应？

三个常见原因，这个脚本能一次分辨清楚：
  1. **事件根本没到**（辅助功能权限没给/没生效）→ 按任何键都打印不出东西；
  2. **到了但字符不对**：macOS 的 Option(alt) 是「组合字符」修饰键，
     Option+P 的字符是 `π` 而不是 `p`，于是 `ctrl+alt+p` 永远匹配不上；
  3. 到了、字符也对 → 那就是热键字符串本身的问题（比如写成了 `<p>`）。

用法
----
    python3 probe/mac_hotkey_probe.py                    # 监听 20 秒
    python3 probe/mac_hotkey_probe.py --seconds 30
    python3 probe/mac_hotkey_probe.py --combo ctrl+alt+p # 指定要验证的组合

运行后：**按下你要用的热键**（例如 Ctrl+Alt+P），再随便按几个字母做对照。
脚本会把「实际收到的组合」和「配置里的组合」摆在一起对比，并给出结论。

产物
----
    probe/probe_out/hotkey.json   本次监听到的全部按键事件
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _probe_lib as L  # noqa: E402

#: 视为「修饰键」的键（pynput 在 macOS 上左右修饰键会分开报，这里都归一到同一类）
MOD_ALIASES = {
    "ctrl": ("ctrl", "ctrl_l", "ctrl_r"),
    "alt": ("alt", "alt_l", "alt_r", "alt_gr"),      # macOS 的 Option
    "shift": ("shift", "shift_l", "shift_r"),
    "cmd": ("cmd", "cmd_l", "cmd_r"),
}


def _mod_kind(key):
    """该键属于哪一类修饰键；不是修饰键返回 None。"""
    name = getattr(key, "name", None)
    if not name:
        return None
    for kind, names in MOD_ALIASES.items():
        if name in names:
            return kind
    return None


def _char_of(key):
    """取按键代表的字符（普通键才有）。"""
    ch = getattr(key, "char", None)
    if isinstance(ch, str) and ch:
        return ch
    name = getattr(key, "name", None)
    return name or repr(key)


def _normalize_combo(held, key):
    """把「当前按住的修饰键 + 这个键」拼成 `ctrl+alt+p` 这样的可比较字符串。"""
    parts = [k for k in ("ctrl", "alt", "shift", "cmd") if k in held]
    parts.append(_char_of(key))
    return "+".join(parts)


def run(args) -> int:
    if not L.ensure_deps():
        return 2
    try:
        from pynput import keyboard
    except Exception as e:
        print(f"✗ pynput 不可用：{e}")
        return 2

    target = (args.combo or "ctrl+alt+p").strip().lower()

    L.hr("环境")
    print(f"  辅助功能权限: {'✓ 已授予' if L.accessibility_trusted() else '✗ 未授予'}"
          f"  （未授予时**收不到任何按键**，先去系统设置里勾上）")
    print(f"  要验证的热键: {target!r}")
    print(f"  监听时长    : {args.seconds} 秒")

    L.hr("请现在按下热键")
    print(f"  1) 先按你要用的热键（例如 Ctrl+Alt+P）")
    print(f"  2) 再单独按几个字母做对照（例如 p、a、f8）")
    print(f"  3) 等 {args.seconds} 秒自动结束")
    print()

    events = []
    seen = []
    held = set()

    def on_press(key):
        kind = _mod_kind(key)
        if kind:
            held.add(kind)
            events.append({"t": round(time.time(), 3), "type": "press",
                           "key": repr(key), "mod": kind})
            print(f"  [按下] {repr(key):28} (修饰键 {kind})")
            return
        combo = _normalize_combo(held, key)
        seen.append(combo)
        events.append({"t": round(time.time(), 3), "type": "press",
                       "key": repr(key), "char": _char_of(key), "combo": combo})
        flag = "  ← 匹配目标热键!" if combo == target else ""
        print(f"  [按下] {repr(key):28} 解析为组合 {combo!r}{flag}")

    def on_release(key):
        kind = _mod_kind(key)
        if kind:
            held.discard(kind)
        events.append({"t": round(time.time(), 3), "type": "release",
                       "key": repr(key)})

    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    deadline = time.time() + args.seconds
    try:
        while time.time() < deadline:
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n  （提前结束）")
    listener.stop()

    # ---------- 结论 ----------
    L.hr("结论")
    print(f"  收到按键事件总数: {len(events)}")
    if not events:
        print()
        print("  ✗ **一个按键都没收到** —— 事件根本没到本进程。")
        print("    最常见原因：本进程没有「辅助功能」权限，或授权后没重启终端。")
        print("    · 去 系统设置 › 隐私与安全性 › 辅助功能，勾选你启动本脚本用的程序")
        print("      （终端 / iTerm / 或 CodeBuddy 这类 IDE）")
        print("    · 辅助功能授权**立即生效**，但**必须重开终端**才能被新进程继承")
        print("    · 然后重跑本脚本确认")
    elif target in seen:
        print()
        print(f"  ✓ **目标热键 {target!r} 能被正确识别** —— 热键本身没问题。")
        print("    若正式程序里仍无反应，问题不在按键识别，而在别的环节（看 polish.log）。")
    else:
        print()
        print(f"  ✗ 收到了按键，但**没有一次解析成 {target!r}**。实际收到的组合：")
        for c in dict.fromkeys(seen):
            print(f"      · {c!r}")
        print()
        chars = {c.split("+")[-1] for c in seen}
        if any(len(ch) == 1 and ord(ch) > 127 for ch in chars):
            print("  → 很像 macOS 的「Option 组合字符」问题：")
            print("    Option(alt) 不是普通修饰键，它会参与字符组合，")
            print("    例如 Option+P 的字符是 'π' 而不是 'p'，所以热键永远匹配不上。")
            print("    **解决办法：把热键换成功能键**，在 config.json 里改成：")
            print('        "hotkey": "f8"')
        elif "cmd" in target or "alt" in target:
            print("  → 修饰键组合与配置不一致，请按下面实际收到的组合去改 config.json。")
        else:
            print("  → 请把上面实际收到的组合填进 config.json 的 hotkey。")

    out = args.json or (L.default_outdir() / "hotkey.json")
    report = {
        "target": target,
        "accessibility_trusted": L.accessibility_trusted(),
        "combos_seen": list(dict.fromkeys(seen)),
        "matched": target in seen,
        "events": events,
    }
    L.hr("报告")
    print(f"  已写入：{L.save_json(out, report)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="macOS 全局热键诊断探针")
    ap.add_argument("--seconds", type=float, default=20.0, help="监听多少秒（默认 20）")
    ap.add_argument("--combo", default="ctrl+alt+p", help="要验证的热键（默认 ctrl+alt+p）")
    ap.add_argument("--json", default=None, help="JSON 报告路径")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
