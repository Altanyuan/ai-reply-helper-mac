"""macOS 回填：把选中的润色文本写进微信输入框。

实测结论（决定本模块的实现方式）
--------------------------------
微信 4.1（macOS，`xwechat_mac`）的**辅助功能树基本是空的**：整窗只有
6 个节点（3×AXButton + 2×AXGroup + 1×AXWindow），**没有任何 AXTextArea**，
0 个文本节点（见 `probe/mac_capability_probe.py` 的报告）。所以：

  · 用 AX `set_value` 写输入框 —— 在微信上**行不通**；
  · 只能走**键盘路径**：激活微信 → `Cmd+A` 全选 → 逐字输入（覆盖原草稿）。

AX 路径仍然保留：它对别的聊天应用、以及将来可能恢复无障碍的微信版本有效，
而且探测成本极低（空树只遍历 6 个节点）。**但绝不能依赖它**——
之前就写错过：只在「AX 发现有草稿」时才发 Cmd+A，AX 不通时就会把润色结果
**追加**在原草稿后面，而不是替换。现在 Cmd+A 无条件先发。

为什么不用剪贴板 + Cmd+V
------------------------
1. 会覆盖用户自己在用的剪贴板；
2. 粘贴无法「读回确认」，用户看不到到底写进去了没有。

永不发送回车 —— 聊天输入框里回车会**直接发送消息**，这是最危险的误操作。
"""

from __future__ import annotations

import logging
import os
import threading
import time

from macos import ax, keys, window

log = logging.getLogger("polish")

_FILL_LOCK = threading.Lock()

#: 重复点击守卫：1 秒内对同一段文本再次回填视为误双击，直接忽略
_LAST = {"t": 0.0, "text": ""}
_DUP_WINDOW_S = 1.0

#: 是否在回填前「点一下输入区」来抢键盘焦点。
#: 默认关闭：需要按截图算出的输入区位置去点击，一旦区域检测偏了，
#: 点到消息区里的链接会打开浏览器 —— 风险大于收益。
#: 遇到「激活微信后焦点没回到输入框」时可用它验证：
#:     WECHAT_AI_CLICK_INPUT=1 python3 main.py
CLICK_TO_FOCUS = os.getenv("WECHAT_AI_CLICK_INPUT", "0") == "1"

#: 在输入区内相对位置点击（避开顶部边框与底部工具栏）
_INPUT_CLICK_X = 0.55
_INPUT_CLICK_Y = 0.45


def _norm(s: str) -> str:
    return (s or "").strip().replace("\r\n", "\n")


def _duplicate_blocked(text: str) -> bool:
    now = time.time()
    if _LAST["text"] == text and now - _LAST["t"] < _DUP_WINDOW_S:
        return True
    _LAST["t"] = now
    _LAST["text"] = text
    return False


def _locate_input(ref):
    """用 AX 定位输入框，返回 (element, frame, current_text) 或 None。

    微信上**必然返回 None**（AX 树是空的）；保留它是为了别的应用与未来版本。
    """
    try:
        return ax.find_input_box(ref.pid, window_frame=ref.rect())
    except Exception as e:
        log.warning(f"AX 定位输入框失败: {e}")
        return None


# ---------------------------------------------------------------------------
# 键盘路径的辅助
# ---------------------------------------------------------------------------

def _read_input_via_clipboard(ref):
    """用 Cmd+A / Cmd+C 把输入框当前内容读到剪贴板并返回（读完还原剪贴板）。

    只读操作：不修改输入框内容（Cmd+A 只是选中），且会还原用户原剪贴板。
    返回 None 表示读不到（焦点不在输入框时可能读到别处的文本，故调用方需比对）。
    """
    import pyperclip
    try:
        original = pyperclip.paste()
    except Exception:
        original = ""
    sentinel = "__WECHAT_AI_EMPTY_SENTINEL__"
    try:
        pyperclip.copy(sentinel)
    except Exception:
        return None

    try:
        keys.select_all()
        time.sleep(0.05)
        keys.copy()
        time.sleep(0.12)
        text = pyperclip.paste()
    except Exception as e:
        log.warning(f"读取输入框失败: {e}")
        text = None
    finally:
        try:
            pyperclip.copy(original)
        except Exception:
            pass
    if text == sentinel:
        return ""
    return text


def _click_input_area(ref):
    """按截图估出的输入区位置点一下，把键盘焦点抢到输入框。返回是否点到。

    仅当 WECHAT_AI_CLICK_INPUT=1 时启用（默认关闭，见模块常量说明）。
    点击后会把鼠标移回原位，不长期占用光标。
    """
    try:
        import Quartz
        from macos import capture
    except Exception:
        return False

    png = None
    try:
        with _temp_png() as png:
            ok, why = capture.capture_png(ref.wid, png)
            if not ok:
                log.warning(f"抢焦点失败（截图不可用：{why}）")
                return False
            from PIL import Image
            im = Image.open(png)
            W, H = im.size
            input_top, src = capture.resolve_input_top(im)
            if not (0.40 < input_top < 0.97):
                log.warning(f"输入区位置不可信（{input_top:.3f}），跳过点击抢焦点")
                return False
            # 换算到屏幕「点」坐标：窗口宽高单位是点，截图是按像素来的，用比例规避倍率问题
            px = ref.x + ref.w * (capture.PANE_LEFT + (1 - capture.PANE_LEFT) * _INPUT_CLICK_X)
            py = ref.y + ref.h * (input_top + (1 - input_top) * _INPUT_CLICK_Y)
    except Exception as e:
        log.warning(f"计算输入区点击位置失败: {e}")
        return False

    try:
        old = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        for ev_type in (Quartz.kCGEventMouseMoved,):
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
                None, ev_type, (px, py), Quartz.kCGMouseButtonLeft))
        time.sleep(0.05)
        down = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDown, (px, py), Quartz.kCGMouseButtonLeft)
        up = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseUp, (px, py), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.04)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.10)
        # 把鼠标移回原位，避免长期占着用户的光标
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventMouseMoved, (old.x, old.y), Quartz.kCGMouseButtonLeft))
        log.info(f"已点击输入区抢焦点（比例 y={input_top:.3f}/{src}）")
        return True
    except Exception as e:
        log.warning(f"点击输入区失败: {e}")
        return False


class _temp_png:
    """with 语句用的临时 PNG 文件（退出即删）。"""

    def __enter__(self):
        import tempfile
        self._d = tempfile.TemporaryDirectory()
        from pathlib import Path
        self.path = Path(self._d.name) / "fill.png"
        return self.path

    def __exit__(self, *exc):
        try:
            self._d.cleanup()
        except Exception:
            pass
        return False


# ---------------------------------------------------------------------------
# 对外
# ---------------------------------------------------------------------------

def paste_back(text, target_window_id=None, delay=0.05) -> bool:
    """把 text 写进微信输入框（**覆盖**原草稿）。返回是否确认写入。

    `target_window_id` 是窗口 ID（wid），仅作「粘性」提示 —— 实际一律重新定位
    微信主窗口，因为触发来源可能是点浮动图标，那时前台并不是微信。
    """
    if not text:
        return False
    if _duplicate_blocked(text):
        log.info("1 秒内重复回填同一段文本，忽略（防双击）")
        return True

    with _FILL_LOCK:
        ref = window.find_wechat_control(target_window_id)
        if ref is None:
            log.warning("未定位到微信窗口，无法回填")
            return False

        # ---- 1) AX 直写（别的应用/未来版本可用；微信上必然跳过）----
        hit = _locate_input(ref)
        if hit is not None:
            el, _fr, before = hit
            if ax.set_value(el, text):
                after = ax.value(el) or ""
                if _norm(after) == _norm(text) or _norm(after).endswith(_norm(text)):
                    log.info("已通过 AX 写入并读回确认")
                    return True
                log.warning(f"AX 写入后读回不一致（写前 {len(before or '')} 字 / "
                            f"写后 {len(after)} 字），改用键盘路径")
            else:
                log.info("AX 写入被拒绝，改用键盘路径")
        else:
            log.info("该应用未暴露可写输入控件（微信 4.x 即如此），走键盘路径")

        # ---- 2) 键盘路径：激活 → （可选）点输入区抢焦点 → 全选 → 逐字输入 ----
        if not keys.AVAILABLE:
            log.error("键盘模拟不可用（pynput 未就绪或未授权「辅助功能」），无法回填")
            return False

        try:
            window.activate(ref)
            time.sleep(0.20)
            if CLICK_TO_FOCUS:
                _click_input_area(ref)

            # 无条件先全选：输入框里可能有用户的草稿，必须**替换**而不是追加。
            # （这正是之前写错的地方：只在 AX 报「有草稿」时才全选，
            #   而微信的 AX 永远是空的 → 结果变成追加。）
            keys.select_all()
            time.sleep(0.05)
            keys.type_text(text)
            time.sleep(max(0.15, delay))
        except Exception as e:
            log.warning(f"键盘回填失败: {e}")
            return False

        # ---- 3) 读回确认（用 Cmd+A/Cmd+C 读，读完还原剪贴板）----
        after = _read_input_via_clipboard(ref)
        if after is None:
            log.info("已尝试回填，但无法读回确认（剪贴板不可用），请自行核对输入框")
            return True
        if _norm(after) == _norm(text):
            log.info("已回填并读回确认")
            return True
        log.warning(f"回填后读回不一致：期望 {len(_norm(text))} 字，"
                    f"实际读到 {len(_norm(after))} 字。"
                    f"常见原因：激活微信后键盘焦点没回到输入框"
                    f"（可试 WECHAT_AI_CLICK_INPUT=1 用点击抢焦点），"
                    f"或热键触发时输入框本就没有焦点。")
        return False
