"""微信聊天内容读取：截图 + Vision OCR。

管线
----
    定位微信窗口 → 还原（若最小化）→ screencapture -l<wid> 截图
      → 定位消息区（固定比例 + 动态输入区分隔线）
      → Vision OCR（只扫消息区，省时）
      → 合并成行、按几何判左右 → 丢掉时间戳/系统提示等噪声

三个关键约束
------------
1. **不用 CGWindowListCreateImage**。macOS 进入 ScreenCaptureKit 后它无法被取消，
   一旦卡住会永久挂起 Python 线程；改用 `screencapture -l <窗口ID>` 子进程，
   可以超时杀掉，而且**窗口被遮挡/不在前台也能截**（不抢焦点，不打断用户打字）。
2. **微信的界面文字只能靠 OCR**。微信是原生应用且不暴露可读的消息控件树
   （同类项目实测后为微信选择了截图方案），所以这里没有 UIA 那条快路径。
3. **左右归属靠几何**（气泡对齐）而不是颜色，这样浅色/深色主题都成立。
   判不准的一律标 unknown，绝不臆测——否则会把对方的话当成自己的话去回复。
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from macos import ax, keys, ocr, window

log = logging.getLogger("polish")


@dataclass
class Message:
    """从窗口截图里识别出来的一条聊天消息。"""

    text: str
    side: str = "unknown"     # me / them / unknown
    top: float = 0.0          # 归一化纵坐标（top-origin），用于排序
    x: float = 0.0            # 消息窗格内的归一化横坐标，用于判左右

# --- 消息区定位常量（top-origin 归一化，相对整张窗口截图）---
# 这几个值由 probe/mac_capture_probe.py 实测校准；探针会把结果画在标注图上，
# 若你的微信版本布局不同，改这里即可（或先用探针确认再改）。
PANE_LEFT = 0.32            # 左侧会话列表约占 x<0.32
PANE_TOP = 0.09             # 顶部标题栏约占 y<0.09
PANE_BOTTOM_DEFAULT = 0.78  # 自动找不到分隔线时的兜底（实测微信 4.1 macOS：0.778）

CAPTURE_TIMEOUT_S = 5.0
MIN_PNG_BYTES = 1000

#: 拼给大模型的上下文长度上限（字符）
CONTEXT_CHARS = 4000


# ---------------------------------------------------------------------------
# 截图
# ---------------------------------------------------------------------------

def capture_png(wid: int, out_path, timeout: float = CAPTURE_TIMEOUT_S):
    """按窗口 ID 截图。返回 (是否成功, 说明)。"""
    out = Path(out_path)
    try:
        p = subprocess.run(
            ["screencapture", "-x", "-o", "-l", str(wid), str(out)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"截图超时（>{timeout}s）"
    except OSError as e:
        return False, f"无法执行 screencapture：{e}"
    if p.returncode != 0:
        # 实测最常见的失败就是没给「屏幕录制」权限，此时 stderr 是
        # "could not create image from window"（退出码 1）。
        # 这里把它翻译成用户能照着做的指引，否则日志里只有一串退出码。
        err = (p.stderr or "").strip().splitlines()
        detail = err[0][:140] if err else f"退出码 {p.returncode}"
        if "could not create image" in (p.stderr or "") or p.returncode == 1:
            return False, (f"screencapture 失败：{detail}"
                           f"（未授权「屏幕录制」：系统设置 › 隐私与安全性 › "
                           f"录屏与系统录音，授权后必须退出重开本程序）")
        return False, f"screencapture 失败：{detail}"
    if not out.exists() or out.stat().st_size < MIN_PNG_BYTES:
        return False, ("截图文件缺失或过小 —— 通常也是缺「屏幕录制」权限，"
                       "或该窗口当前不可渲染（例如已最小化）")
    return True, "ok"


# ---------------------------------------------------------------------------
# 消息区定位
# ---------------------------------------------------------------------------

def detect_input_top_ratio(pil_img, x_min: float = PANE_LEFT,
                           min_run: float = 0.62, min_delta: int = 12,
                           search_lo: float = 0.45, search_hi: float = 0.96):
    """找输入区上分隔线，返回 top-origin 归一化 y；找不到返回 None。

    输入区顶部通常是横跨右侧窗格的一条长水平线。缩到宽 640 灰度后，
    在 y∈[search_lo, search_hi] 从下往上找第一条「相邻行灰度差达阈值
    的像素占右侧窗格 ≥ min_run」的行。
    """
    try:
        g = pil_img.convert("L")
        W, H = g.size
        if W < 80 or H < 80:
            return None
        if W > 640:
            g = g.resize((640, max(1, int(H * 640 / W))))
            W, H = g.size
        px = g.load()
    except Exception:
        return None

    x0 = int(W * x_min)
    span = max(1, W - x0)
    y_lo, y_hi = max(1, int(H * search_lo)), min(H - 1, int(H * search_hi))
    for y in range(y_hi, y_lo, -1):
        run = 0
        for x in range(x0, W):
            if abs(px[x, y] - px[x, y - 1]) >= min_delta:
                run += 1
        if run / span >= min_run:
            return y / H
    return None


def resolve_input_top(pil_img, override=None):
    """返回 (input_top, 来源说明)。"""
    if override is not None:
        return float(override), "配置指定"
    detected = detect_input_top_ratio(pil_img)
    if detected is not None and 0.40 < detected < 0.97:
        return detected, "自动检测"
    return PANE_BOTTOM_DEFAULT, "兜底常量"


# ---------------------------------------------------------------------------
# 噪声过滤与左右判定
# ---------------------------------------------------------------------------

_NOISE_PATTERNS = (
    r"^\d{1,2}:\d{2}$",                       # 12:34
    r"^\d{1,2}月\d{1,2}日",                    # 3月5日
    r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}",       # 2026-09-26
    r"^(昨天|今天|前天|星期[一二三四五六日天])\s*\d{0,2}:?\d{0,2}$",
    r"^\d{4}年\d{1,2}月\d{1,2}日",
    # 文件消息下方的大小标注（实测 "285.0M" / "6.1M"）。
    # 刻意收得很紧：必须有小数点或带 B 后缀，避免把「5G」这类正常短消息误删。
    r"^\d+\.\d\s*[KMGTP]B?$",
    r"^\d+\s*[KMGTP]B$",
)

_NOISE_KEYWORDS = (
    "对方正在输入", "以下为新消息", "查看更多消息", "撤回了一条消息",
    "退出了群聊", "加入了群聊", "邀请你加入了群聊", "以上是打招呼的内容",
)

def _strip_decor(text: str) -> str:
    """剥掉两端的装饰性字符（标点 / 符号 / 空白），只留实义内容。

    为什么要做成「剥两端任意非文字字符」而不是列一个符号表：OCR 会把文件气泡上的
    小图标读成 `*` `•` `…` `⋯` 等五花八门的符号，实测就出现过 `…微信电脑版`。
    逐个列符号永远列不全，按「是否文字」判断才稳。
    """
    i, j = 0, len(text)
    while i < j and not (text[i].isalnum() or _is_cjk(text[i])):
        i += 1
    while j > i and not (text[j - 1].isalnum() or _is_cjk(text[j - 1])):
        j -= 1
    return text[i:j]


def _is_noise(text: str) -> bool:
    """该行是不是时间戳 / 系统提示之类的噪声（不该送进模型）。"""
    import re
    s = text.strip()
    if not s:
        return True
    for kw in _NOISE_KEYWORDS:
        if kw in s:
            return True
    for pat in _NOISE_PATTERNS:
        if re.match(pat, s):
            return True
    # 文件消息下方的「微信电脑版」系统提示（实测 OCR 读成 "*•微信电脑版"、
    # "…微信电脑版"）。只在**剥掉装饰后整行就等于它**时才丢，
    # 避免误删提到它的正常消息。
    if _strip_decor(s) == "微信电脑版":
        return True
    return False


def _is_cjk(ch: str) -> bool:
    return "\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def _smart_join(parts) -> str:
    """同一行的碎块拼接：中日韩字符之间不插空格，其余按空格分隔。"""
    out = ""
    for p in parts:
        if not out:
            out = p
        elif _is_cjk(out[-1]) or _is_cjk(p[0]):
            out += p
        else:
            out += " " + p
    return out


def _group_lines(blocks, y_tol: float = 0.014):
    """按纵向位置把 OCR 碎块合并成行，返回从上到下的 [{top, text, blocks}]。"""
    if not blocks:
        return []
    bs = sorted(blocks, key=lambda b: (b["top"], b["x"]))
    lines = []
    for b in bs:
        hit = None
        for ln in lines:
            if abs(ln["top"] - b["top"]) <= y_tol:
                hit = ln
                break
        if hit is None:
            lines.append({"top": b["top"], "blocks": [b]})
        else:
            hit["blocks"].append(b)
    for ln in lines:
        ln["blocks"].sort(key=lambda b: b["x"])
        ln["text"] = _smart_join([x["text"] for x in ln["blocks"]])
    lines.sort(key=lambda ln: ln["top"])
    return lines


#: 左对齐判定：文字左沿贴近消息窗格左侧（对方气泡紧跟在左侧头像后面）
LEFT_START_MAX = 0.22
#: 右对齐判定：文字右沿贴近消息窗格右侧（自己的气泡右对齐）
RIGHT_END_MIN = 0.70


def classify_side(x: float, w: float) -> str:
    """按几何位置判 me / them / unknown（x/w 均为**消息窗格内**归一化坐标）。

    为什么用「对齐」而不是「居中/偏移」：微信里对方气泡左对齐（紧跟左侧头像），
    自己的气泡右对齐。所以看的是**文字框贴哪一边**，而不是它的绝对位置。

    为什么左对齐优先判定：一条很长的对方消息会横跨整个窗格，右沿也会很靠右，
    若不先判左对齐就会把它误认成「我」说的话 —— 那是会写错话术方向的错误。

    判不准返回 unknown（保留在上下文里但不作为回复目标），宁可不标也不臆测。
    """
    right = x + w
    if x <= LEFT_START_MAX:
        return "them"
    if right >= RIGHT_END_MIN:
        return "me"
    return "unknown"


def _to_messages(blocks, pane_left: float, n: int):
    """OCR 块 → Message 列表（取最近 n 条，按页面从上到下顺序）。"""
    pane_w = max(1e-6, 1.0 - pane_left)
    out = []
    for ln in _group_lines(blocks):
        text = ln["text"].strip()
        if _is_noise(text):
            continue
        b0 = ln["blocks"][0]
        rel_x = max(0.0, min(1.0, (b0["x"] - pane_left) / pane_w))
        side = classify_side(rel_x, b0["w"] / pane_w)
        out.append(Message(text=text, side=side, top=ln["top"], x=rel_x))
    return out[-n:] if n > 0 else out


# ---------------------------------------------------------------------------
# 对外：读上下文
# ---------------------------------------------------------------------------

def read_messages(window_id=None, n=5, keep_png: str = None, verbose: bool = False):
    """读最近 n 条聊天消息。返回 (messages, meta)。任何失败都降级为空列表。"""
    ref = window.find_wechat_control(window_id)
    if ref is None:
        return [], {"ok": False, "error": "未定位到微信窗口"}

    # 最小化的窗口截不到；先无焦点还原（不抢用户当前操作）
    try:
        window.unminimize(ref.pid)
    except Exception:
        pass

    tmpdir = None
    try:
        if keep_png:
            png = Path(keep_png)
            png.parent.mkdir(parents=True, exist_ok=True)
        else:
            tmpdir = tempfile.TemporaryDirectory()
            png = Path(tmpdir.name) / "chat.png"

        ok, why = capture_png(ref.wid, png)
        if not ok:
            return [], {"ok": False, "error": why, "window": ref.as_dict()}

        # 先读图（内存驻留），随后临时文件即可删除
        cg = _load_cgimage(png, max_px=ref.w)
        if cg is None:
            return [], {"ok": False, "error": "截图解码失败"}

        W, H, input_top, src, roi, pane_px = _geometry(png, ref)
        if not ocr.available():
            return [], {"ok": False, "error": "Vision OCR 不可用",
                        "window": ref.as_dict(), "png": str(png)}

        blocks = ocr.recognize(cg, roi=roi)
        msgs = _to_messages(blocks, PANE_LEFT, int(n))

        meta = {
            "ok": bool(msgs),
            "window": ref.as_dict(),
            "image_px": [W, H],
            "input_top": round(input_top, 4),
            "input_top_source": src,
            "pane_px": list(pane_px),
            "n_blocks": len(blocks),
            "n_messages": len(msgs),
            # 临时截图会在本函数返回前删除，只有调用方显式要留盘时才给路径
            "png": (str(png) if keep_png else ""),
        }
        if not msgs:
            meta["error"] = "消息区内没识别到文字（聊天区可能为空，或区域常量需校准）"
        if verbose:
            log.info(f"macOS 读取上下文: {meta}")
        return msgs, meta
    except Exception as e:
        log.warning(f"读取微信上下文失败（已降级为空）: {e}")
        return [], {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if tmpdir is not None:
            try:
                tmpdir.cleanup()
            except Exception:
                pass


def _load_cgimage(path, max_px=None):
    """PNG → 内存驻留 CGImage（可选降采样到点尺寸，把 OCR 成本减半）。"""
    try:
        import Quartz
        from Foundation import NSData
    except Exception as e:
        log.warning(f"加载截图失败（缺少 pyobjc，{type(e).__name__}）")
        return None
    try:
        data = NSData.dataWithContentsOfFile_(str(path))
        if data is None:
            return None
        src = Quartz.CGImageSourceCreateWithData(data, None)
        if src is None:
            return None
        if max_px:
            opts = {
                Quartz.kCGImageSourceCreateThumbnailFromImageAlways: True,
                Quartz.kCGImageSourceThumbnailMaxPixelSize: int(max_px),
            }
            thumb = Quartz.CGImageSourceCreateThumbnailAtIndex(src, 0, opts)
            if thumb is not None:
                return thumb
        return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    except Exception as e:
        log.warning(f"加载截图失败: {e}")
        return None


def _geometry(png_path, ref):
    """算消息区几何：返回 (W, H, input_top, 来源, roi_左下原点, pane_px)。"""
    from PIL import Image
    im = Image.open(png_path)
    W, H = im.size
    input_top, src = resolve_input_top(im)
    left_px, top_px = int(W * PANE_LEFT), int(H * PANE_TOP)
    bottom_px = int(H * input_top)
    pane_px = (left_px, top_px, W, bottom_px)
    # Vision 的 ROI 是归一化 + 原点左下
    roi = (PANE_LEFT, 1.0 - input_top, 1.0 - PANE_LEFT, input_top - PANE_TOP)
    return W, H, input_top, src, roi, pane_px


def format_context(messages) -> str:
    """把消息列表拼成给大模型的上下文文本（带说话人标签）。"""
    if not messages:
        return ""
    label = {"me": "我", "them": "对方", "unknown": "方向未确认"}
    parts = [f"{label.get(m.side, '方向未确认')}：{m.text}" for m in messages]
    text = "\n".join(parts)
    if len(text) > CONTEXT_CHARS:
        text = "…（前文已截断）\n" + text[-CONTEXT_CHARS:]
    return text


#: 上一次读上下文失败的原因（成功时清空）。
#: 存在的理由：失败原因以前只写进日志，而日志是加密的、用户翻不动 ——
#: 结果用户只看到「没有读到聊天上下文」，却不知道是缺权限、窗口没开还是截图失败。
_LAST_ERROR = ""


def last_context_error() -> str:
    """上一次读取聊天上下文失败的原因；成功时为 ""。"""
    return _LAST_ERROR


def read_wechat_recent(n=5, use_ocr=True, window_id=None) -> str:
    """读取最近的聊天内容，拼成给大模型的上下文文本。

    只有「截图 + OCR」一条路（微信不暴露可读的 AX 消息树）；
    `use_ocr=False` 时直接返回空字符串，即无上下文润色。
    失败原因同时写入日志与 `last_context_error()`（供界面显示给用户）。
    """
    global _LAST_ERROR
    if not use_ocr:
        _LAST_ERROR = "已按设置关闭上下文读取（config.json 的 ocr_context = false）"
        log.info("ocr_context 已关闭，不读上下文（只做无上下文润色）")
        return ""
    msgs, meta = read_messages(window_id=window_id, n=n)
    if not msgs:
        why = (meta or {}).get("error") or "没能从窗口截图里识别出消息"
        _LAST_ERROR = why
        log.info(f"未读取到聊天上下文：{why}")
        return ""
    _LAST_ERROR = ""
    log.info(f"读取到 {len(msgs)} 条聊天上下文"
             f"（Vision OCR，窗口={meta['window']['wid']}，"
             f"分隔线={meta['input_top']:.3f}/{meta['input_top_source']}）")
    return format_context(msgs)


# ---------------------------------------------------------------------------
# 对外：读输入框草稿
# ---------------------------------------------------------------------------

def read_wechat_input():
    """直接读微信消息输入框当前文本。没有内容/定位失败返回 None。"""
    ref = window.find_wechat_control()
    if ref is None:
        return None
    try:
        hit = ax.find_input_box(ref.pid, window_frame=ref.rect())
        if hit is None:
            return None
        _el, _fr, txt = hit
        if txt and txt.strip():
            return txt
        return None
    except Exception as e:
        log.warning(f"读取微信输入框失败: {e}")
        return None


def capture_draft() -> str:
    """捕获输入框草稿：优先 AX 直读；失败再退回 Cmd+A / Cmd+C。

    关键安全约束：**检测不到微信就完全不碰键盘**，
    否则 Cmd+A/Cmd+C 会打到用户当前正在编辑的其它窗口上，破坏用户内容。
    """
    txt = read_wechat_input()
    if txt and txt.strip():
        return txt.strip()

    ref = window.find_wechat_control()
    if ref is None:
        log.info("capture_draft: 未检测到微信窗口，跳过抓取")
        return ""

    import pyperclip
    try:
        original = pyperclip.paste()
    except Exception:
        original = ""
    sentinel = "__WECHAT_AI_EMPTY_SENTINEL__"
    try:
        pyperclip.copy(sentinel)
    except Exception:
        pass

    try:
        window.activate(ref)
        time.sleep(0.15)
        hit = ax.find_input_box(ref.pid, window_frame=ref.rect())
        if hit is not None:
            ax.focus(hit[0])
            time.sleep(0.05)
        keys.select_all()
        time.sleep(0.05)
        keys.copy()
        time.sleep(0.15)
    except Exception as e:
        log.warning(f"捕获草稿时失败: {e}")

    try:
        text = pyperclip.paste()
    except Exception:
        text = ""
    if text == sentinel:
        text = ""
    try:
        pyperclip.copy(original)
    except Exception:
        pass
    return text.strip() if text else ""
