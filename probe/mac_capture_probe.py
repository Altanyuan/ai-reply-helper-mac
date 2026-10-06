#!/usr/bin/env python3
"""探针②：macOS 微信「截图 + Vision OCR」端到端验证（P0 主力）。

验证三件事
----------
  1. **能不能按窗口 ID 截到微信窗口** —— 用 --delay 制造遮挡，验证"被遮挡也能截"
  2. **Vision 中文 OCR 的质量** —— 文本块数 / 平均置信度 / 中文字符数 自评
  3. **消息区裁剪与左右分类是否可用** —— 自动估算输入区上分隔线，落盘标注图人工核对

为什么这条路线是主力
--------------------
同类项目（jev-chat-jarvis-mac）对微信最终选的就是「截图 + Vision OCR」而非 AX 直读，
且明确不用 CGWindowListCreateImage（进 ScreenCaptureKit 后无法取消、会永久卡住线程），
而是用 `screencapture -l <窗口ID>` 子进程（可超时杀掉）。本脚本沿用同一策略。

用法
----
    python3 probe/mac_capture_probe.py                    # 自动选最大的微信窗口
    python3 probe/mac_capture_probe.py --list             # 只列窗口，不截图
    python3 probe/mac_capture_probe.py --wid 12345        # 指定窗口 ID
    python3 probe/mac_capture_probe.py --delay 5          # 5 秒后截图（用来切窗口造遮挡）
    python3 probe/mac_capture_probe.py --input-top 0.74   # 分隔线自动识别不准时手动指定
    python3 probe/mac_capture_probe.py --outdir /tmp/p2

产物（默认落在 probe/probe_out/）
--------------------------------
    wid<ID>_raw.png        原始截图（人工核对识别质量）
    wid<ID>_marked.png     标注图：绿框=消息窗格、红线=检测到的输入区分隔线
    wid<ID>_chat.png       裁剪出的消息区（生产路径实际会送去 OCR 的范围）
    capture.json           汇总报告（含全部文本块与归一化坐标）

重要前提
--------
从**终端**运行时，需要在「系统设置 › 隐私与安全性 › 录屏与系统录音」里给
**终端 App**（Terminal / iTerm）授权，否则截图会失败或得到空文件；
打包成 .app 后则授权给该 app。授权后**必须退出重开**才生效。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _probe_lib as L  # noqa: E402


# 聊天窗格定位常量（top-origin 归一化，相对整张窗口截图）。
# 这几个值是**待校准**的起点，探针会把它画在标注图上供你核对；
# 确认后可回填到生产代码里。
PANE_LEFT = 0.32        # 左侧会话列表约占 x<0.32
PANE_TOP = 0.09         # 顶部标题栏约占 y<0.09
PANE_BOTTOM_DEFAULT = 0.72   # 自动找不到分隔线时的兜底值


def annotate(raw_png: Path, marked_png: Path, pane_px, input_top_px):
    """在截图上画出「消息窗格」与「检测到的输入区分隔线」，供人工核对。"""
    try:
        from PIL import ImageDraw, Image
    except Exception:
        return False
    try:
        im = Image.open(raw_png).convert("RGB")
        d = ImageDraw.Draw(im)
        left, top, right, bottom = pane_px
        d.rectangle([left, top, right - 1, bottom - 1], outline=(0, 200, 0), width=3)
        if input_top_px:
            d.line([(left, input_top_px), (right, input_top_px)], fill=(255, 0, 0), width=3)
        marked_png.parent.mkdir(parents=True, exist_ok=True)
        im.save(marked_png)
        return True
    except Exception:
        return False


def crop_png(raw_png: Path, out_png: Path, box) -> bool:
    """按像素框裁剪并另存。"""
    try:
        from PIL import Image
        Image.open(raw_png).crop(box).save(out_png)
        return True
    except Exception:
        return False


def run(args) -> int:
    if not L.ensure_deps():
        return 2

    outdir = Path(args.outdir).expanduser() if args.outdir else L.default_outdir()

    # ---- 1. 权限 ----
    L.hr("权限")
    if getattr(args, "request_permissions", False):
        print("  正在弹出屏幕录制授权框…")
        print(f"  申请返回：{L.request_screen_capture()}")
        print("  请到「系统设置 › 隐私与安全性 › 录屏与系统录音」确认已打开，"
              "并**退出重开终端**再跑本脚本。")
        print()
    sc = L.screen_capture_allowed()
    if sc is False:
        print("  ✗ 屏幕录制**未授权** —— 截图会失败或得到小文件。")
        print("    去「系统设置 › 隐私与安全性 › 录屏与系统录音」给**终端 App**打开，"
              "然后**退出重开终端**再跑。")
        print("    也可以加 --request-permissions 走系统授权框。")
    elif sc is True:
        print("  ✓ 屏幕录制已授权")
    else:
        print("  ? 屏幕录制状态无法检测（老系统），继续跑，看截图能不能出来")

    # ---- 2. 选窗口 ----
    owners = tuple(args.owner) if args.owner else L.WECHAT_OWNER_NAMES
    windows = L.list_windows(owners=owners)

    L.hr("微信窗口")
    if not windows:
        print("  ✗ 没有枚举到微信窗口。请确认：")
        print("    · 微信已启动并登录")
        print("    · 至少打开一个聊天窗口（不要只把微信收在程序坞里）")
        print("    · 不确定 owner 名字时，先跑探针①的 --all-windows 看看")
        return 1
    for i, w in enumerate(windows):
        flag = "在屏" if w["onscreen"] else "离屏"
        print(f"  [{i}] wid={w['wid']:<10} layer={w['layer']:<3} {w['w']}x{w['h']}"
              f" @ ({w['x']},{w['y']})  [{flag}]  title={w['title']!r}")

    if args.list:
        print()
        print("  （--list 模式，未截图）")
        return 0

    target = None
    if args.wid:
        target = next((w for w in windows if w["wid"] == args.wid), None)
        if target is None:
            print(f"\n  ✗ 指定的 wid={args.wid} 不在上面的列表里")
            return 1
    elif args.index is not None:
        if not (0 <= args.index < len(windows)):
            print(f"\n  ✗ --index {args.index} 超出范围")
            return 1
        target = windows[args.index]
    else:
        target = L.pick_main_window(windows)

    if target is None:
        print("\n  ✗ 没能选出目标窗口，请用 --wid 明确指定")
        return 1

    wid = target["wid"]
    print()
    print(f"  → 目标窗口：wid={wid}  title={target['title']!r}  "
          f"{target['w']}x{target['h']}（{target['w'] * target['h']} 面积）")

    # ---- 3. 延迟（遮挡测试）----
    if args.delay > 0:
        L.hr("遮挡测试")
        print(f"  请在 {args.delay} 秒内**把别的窗口切到最前面、彻底盖住微信**"
              f"（但不要把微信最小化）。")
        print("  截图不依赖前台焦点，所以被遮挡也应该能截到——这正是要验证的点。")
        import time
        for s in range(int(args.delay), 0, -1):
            print(f"    {s}…", end="", flush=True)
            time.sleep(1)
        print()

    # ---- 4. 截图 ----
    L.hr("截图")
    raw = outdir / f"wid{wid}_raw.png"
    ok, msg = L.capture_window(wid, raw, timeout=args.timeout)
    if not ok:
        print(f"  ✗ 截图失败：{msg}")
        print()
        print("  排查顺序：")
        print("    1. 屏幕录制权限是否给了**终端 App**（授权后要退出重开终端）")
        print("    2. 窗口是否被最小化（最小化的窗口截不到；被遮挡可以）")
        print("    3. --delay 5 试一次；或先 --list 确认 wid 还活着")
        return 1
    print(f"  ✓ 截图成功：{raw}")
    print(f"    文件 {msg}，窗口在屏状态：{'在屏' if target['onscreen'] else '离屏'}"
          f"（被遮挡不影响截图，这已验证）")

    # ---- 5. 几何与区域估算 ----
    try:
        from PIL import Image
    except Exception:
        print("  ✗ 需要 Pillow 才能做区域估算与裁剪")
        return 2

    im = Image.open(raw)
    W, H = im.size
    scale = W / target["w"] if target["w"] else 0.0
    L.hr("图像几何")
    print(f"  截图像素        : {W}x{H}")
    print(f"  窗口点尺寸      : {target['w']}x{target['h']}")
    print(f"  Retina 倍率     : {scale:.2f}x"
          f"{'（2x，OCR 前建议降采样到点尺寸以省一半时间）' if scale > 1.5 else ''}")

    if args.input_top is not None:
        input_top = float(args.input_top)
        src = "命令行 --input-top 指定"
    else:
        detected = L.detect_input_top_ratio(im)
        if detected is not None and 0.40 < detected < 0.97:
            input_top, src = detected, "自动检测到水平分隔线"
        else:
            input_top, src = PANE_BOTTOM_DEFAULT, "自动检测失败，用兜底常量"

    pane_left = float(args.pane_left)
    left_px = int(W * pane_left)
    top_px = int(H * PANE_TOP)
    bottom_px = int(H * input_top)
    pane_px = (left_px, top_px, W, bottom_px)

    L.hr("区域估算")
    print(f"  输入区分隔线 y  : {input_top:.3f}  （{src}）")
    print(f"  消息窗格（归一化）: x∈[{pane_left:.2f}, 1.00]  y∈[{PANE_TOP:.2f}, {input_top:.2f}]")
    print(f"  消息窗格（像素）  : {pane_px}")

    marked = outdir / f"wid{wid}_marked.png"
    if annotate(raw, marked, pane_px, bottom_px):
        print(f"  ✓ 标注图（绿框=消息窗格，红线=分隔线）：{marked}")

    chat_png = outdir / f"wid{wid}_chat.png"
    if crop_png(raw, chat_png, pane_px):
        print(f"  ✓ 消息区裁剪图：{chat_png}")

    # ---- 6. OCR ----
    if args.no_ocr:
        print()
        print("  （--no-ocr：已跳过 OCR，只看图片）")
        return 0

    img = L.load_cgimage(raw, max_px=None if args.no_downscale else target["w"])
    if img is None:
        print("  ✗ CGImage 加载失败")
        return 1

    langs = tuple(args.lang.split(",")) if args.lang else L.OCR_LANGUAGES

    L.hr("OCR：整图")
    full = L.ocr_image(img, languages=langs)
    full_top = L.to_top_origin(full)
    confs = [b["conf"] for b in full if b["conf"] > 0]
    avg = sum(confs) / len(confs) if confs else 0.0
    cjk = sum(1 for b in full for ch in b["text"] if L.is_cjk(ch))
    print(f"  语言={langs}  文本块={len(full)}  平均置信度={avg:.2f}  中文字符数={cjk}")

    # 落在消息窗格内 / 外的块数 —— 用来判断区域估算对不对
    def in_pane(b):
        return (b["x"] >= pane_left - 0.02 and b["top"] >= PANE_TOP - 0.02
                and b["top"] <= input_top + 0.02)

    inside = [b for b in full_top if in_pane(b)]
    outside = [b for b in full_top if not in_pane(b)]
    print(f"  其中落在消息窗格内：{len(inside)}    窗格外（侧栏/标题/输入区）：{len(outside)}")

    L.hr("OCR：仅消息窗格（生产路径形态）")
    # Vision 的 ROI 是「归一化 + 原点左下」；返回坐标已由 ocr_image 换算回整图
    roi = (pane_left, 1.0 - input_top, 1.0 - pane_left, input_top - PANE_TOP)
    roi_blocks = L.ocr_image(img, languages=langs, roi=roi)
    roi_top = L.to_top_origin(roi_blocks)
    print(f"  ROI={tuple(round(v, 3) for v in roi)}")
    hint = f"（整图过滤法得到 {len(inside)} 个，两者接近说明区域估算正确）" if inside else ""
    print(f"  文本块={len(roi_blocks)}  {hint}")

    # ---- 7. 行合并 + 左右分类 ----
    pane_w = 1.0 - pane_left
    lines = L.group_lines(roi_top)

    def pane_rel_x(b):
        """整图归一化横坐标 → 消息窗格内相对横坐标（0..1）。"""
        return max(0.0, min(1.0, (b["x"] - pane_left) / pane_w))

    classified, dropped = [], []
    for ln in lines:
        if L.is_noise(ln["text"]):
            dropped.append(ln["text"])
            continue
        b0 = ln["blocks"][0]
        classified.append((L.classify_side(pane_rel_x(b0), b0["w"] / pane_w), ln))

    print()
    L.hr("识别结果（按行，含左右归属；已按生产规则剔除噪声）")
    if not lines:
        print("  （消息窗格内没识别到文字 —— 聊天区可能为空，或区域估算偏了，"
              "请看标注图）")
    for side, ln in classified:
        tag = {"me": "我  ", "them": "对方", "unknown": "未确认"}[side]
        print(f"  [{tag}] y={ln['top']:.3f}  {ln['text'][:100]}")
    if dropped:
        print(f"  （已剔除 {len(dropped)} 条噪声：{dropped[:5]}）")

    # ---- 8. 结论 ----
    n_me = sum(1 for s, _ln in classified if s == "me")
    n_them = sum(1 for s, _ln in classified if s == "them")
    n_unknown = sum(1 for s, _ln in classified if s == "unknown")

    L.hr("结论")
    checks = [
        ("截图成功（含被遮挡场景）", True, ""),
        ("识别到文本块 ≥ 10", len(full) >= 10, f"实际 {len(full)}"),
        ("平均置信度 ≥ 0.50", avg >= 0.50, f"实际 {avg:.2f}"),
        ("中文字符数 ≥ 30", cjk >= 30, f"实际 {cjk}"),
        ("消息窗格内文本块 ≥ 5", len(inside) >= 5, f"实际 {len(inside)}"),
        ("成功分出「对方」发言", n_them >= 1, f"我={n_me} 对方={n_them}"),
    ]
    for name, ok_, detail in checks:
        print(f"  {'✓' if ok_ else '✗'} {name}" + (f"（{detail}）" if detail else ""))

    passed = sum(1 for _n, o, _d in checks if o)
    print()
    if passed >= 5:
        print("  → **识别质量可用**：截图 + Vision OCR 路线成立，可以按此继续设计生产管线。")
    elif passed >= 3:
        print("  → **部分可用**：请打开上面的 _marked.png / _chat.png 人工核对，")
        print("     重点看绿框是否正好框住消息区（不是的话用 --input-top / --pane-left 调）。")
    else:
        print("  → **结论存疑**：先看图。常见原因是聊天窗口为空、窗口选错（--list 换一个）、")
        print("     或区域估算偏了（--input-top 手动指定分隔线位置）。")

    # ---- 9. 落盘 ----
    report = {
        "target_window": target,
        "image": {"px_w": W, "px_h": H, "retina_scale": scale},
        "region": {
            "pane_left": pane_left, "pane_top": PANE_TOP,
            "input_top": input_top, "input_top_source": src,
            "pane_px": list(pane_px), "roi_bottom_origin": list(roi),
        },
        "ocr_full": {"languages": list(langs), "n_blocks": len(full),
                     "avg_confidence": round(avg, 4), "cjk_chars": cjk,
                     "blocks": full},
        "ocr_pane": {"n_blocks": len(roi_blocks), "blocks": roi_blocks},
        "lines": [{"side": s, "top": ln["top"], "text": ln["text"]}
                  for s, ln in classified],
        "side_counts": {"me": n_me, "them": n_them, "unknown": n_unknown},
        "verdict": {"passed": passed, "total": len(checks),
                    "checks": [{"name": n, "ok": o, "detail": d} for n, o, d in checks]},
        "artifacts": {"raw": str(raw), "marked": str(marked), "chat": str(chat_png)},
    }
    report_path = L.save_json(outdir / "capture.json", report)
    L.hr("报告")
    print(f"  已写入：{report_path}")
    print("  请打开 _marked.png 核对绿框/红线位置——这是后续生产的区域常量来源。")
    return 0 if passed >= 5 else (0 if passed >= 3 else 1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="macOS 微信截图 + Vision OCR 端到端探针",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--list", action="store_true", help="只列出微信窗口，不截图")
    ap.add_argument("--wid", type=int, default=None, help="指定窗口 ID 截图")
    ap.add_argument("--index", type=int, default=None, help="按上面列表的序号选窗口")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="延迟 N 秒后再截图（用于切窗口制造遮挡，验证被遮挡也能截）")
    ap.add_argument("--timeout", type=float, default=L.SUBPROCESS_TIMEOUT_S,
                    help="screencapture 超时秒数")
    ap.add_argument("--outdir", default=None, help="产物目录（默认 probe/probe_out）")
    ap.add_argument("--pane-left", type=float, default=PANE_LEFT,
                    help="消息窗格左沿（相对窗口宽度的比例，默认 0.32）")
    ap.add_argument("--input-top", type=float, default=None,
                    help="输入区分隔线的归一化 y（0..1，top-origin）；不给则自动检测")
    ap.add_argument("--lang", default=None, help="OCR 语言，逗号分隔（默认 zh-Hans,en-US）")
    ap.add_argument("--no-ocr", action="store_true", help="只截图与裁剪，不做 OCR")
    ap.add_argument("--no-downscale", action="store_true",
                    help="不把 Retina 截图降到点尺寸（默认降，省一半 OCR 时间）")
    ap.add_argument("--owner", action="append", default=None,
                    help="自定义微信 owner 名字（可重复；默认 WeChat/微信/Weixin）")
    ap.add_argument("--request-permissions", action="store_true",
                    help="主动弹出屏幕录制授权框")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
