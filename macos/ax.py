"""辅助功能（Accessibility / AX）读写原语。

两个前提与坑
------------
1. **微信是原生应用，不是 Electron**。Electron 应用（如 QQ）需要先设
   `AXManualAccessibility=True` 才会暴露控件树；微信没有这个开关，
   所以「按 DOM class 找控件」的路子在微信上行不通，只能按 **role + 几何**判断。
2. **微信窗口里有两个 AXTextArea**：左侧会话列表顶部的搜索框（很小）和底部的
   消息输入框（很大）。取「第一个命中」会把文字写进搜索框——必须取**面积最大
   且超过下限**的那个。这是本项目最容易踩、后果最严重的坑。
3. 所有 AX 读取都可能失败（未装 pyobjc / 未授权 / 节点消失），
   本模块统一走 `_as()` 与 `attr()`，**任何路径都不向上抛异常**。
"""

from __future__ import annotations

import logging

log = logging.getLogger("polish")

#: AX 遍历硬上限，避免离屏巨型子树吃光预算
MAX_NODES = 4000
MAX_DEPTH = 40

#: 输入框面积下限（平方点）。微信搜索框约 127x23≈2900，消息框约 643x129≈83000，
#: 用 10000 把两者干净分开。
MIN_INPUT_AREA = 10000

#: 视为「可能含文本」的 role
TEXT_ROLES = ("AXStaticText", "AXTextField", "AXTextArea", "AXHeading")

_AS = None
_AS_TRIED = False


def _as():
    """惰性导入 ApplicationServices；缺 pyobjc 时返回 None（调用方按不可用处理）。"""
    global _AS, _AS_TRIED
    if not _AS_TRIED:
        _AS_TRIED = True
        try:
            import ApplicationServices as _m
            _AS = _m
        except Exception as e:
            log.info(f"ApplicationServices 不可用（{type(e).__name__}）：AX 相关能力将降级")
            _AS = None
    return _AS


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------

def is_trusted():
    """辅助功能权限是否已授予。授权后**立即生效**，无需重启进程。

    返回 True / False / None（无法检测，例如没装 pyobjc）。
    """
    m = _as()
    if m is None:
        return None
    try:
        return bool(m.AXIsProcessTrusted())
    except Exception:
        return None


def request_trust():
    """弹出系统授权框申请辅助功能权限（异步，返回申请前的状态）。"""
    m = _as()
    if m is None:
        return None
    try:
        return bool(m.AXIsProcessTrustedWithOptions(
            {m.kAXTrustedCheckOptionPrompt: True}))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 基础读取
# ---------------------------------------------------------------------------

def app_element(pid: int):
    """取某进程的 AX 根元素；失败返回 None。"""
    m = _as()
    if m is None:
        return None
    try:
        return m.AXUIElementCreateApplication(int(pid))
    except Exception:
        return None


def attr(element, name):
    """读一个 AX 属性；失败返回 None。**本模块访问 AX 的唯一入口。**"""
    m = _as()
    if m is None or element is None:
        return None
    try:
        err, val = m.AXUIElementCopyAttributeValue(element, name, None)
        return val if err == 0 else None
    except Exception:
        return None


def walk(root, max_nodes: int = MAX_NODES, max_depth: int = MAX_DEPTH):
    """有界 DFS，产出 (element, depth, role)。"""
    m = _as()
    if m is None or root is None:
        return
    stack = [(root, 0)]
    seen = 0
    while stack:
        el, depth = stack.pop()
        if depth > max_depth or seen >= max_nodes:
            continue
        seen += 1
        yield el, depth, (attr(el, m.kAXRoleAttribute) or "")
        children = attr(el, m.kAXChildrenAttribute)
        if children:
            try:
                for ch in reversed(list(children)):
                    stack.append((ch, depth + 1))
            except Exception:
                pass


def text(element) -> str:
    """取节点文本：AXValue → AXTitle → AXDescription。"""
    m = _as()
    if m is None:
        return ""
    for nm in (m.kAXValueAttribute, m.kAXTitleAttribute, m.kAXDescriptionAttribute):
        v = attr(element, nm)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def value(element):
    """只取 AXValue（输入框的当前文本）。"""
    m = _as()
    if m is None:
        return None
    v = attr(element, m.kAXValueAttribute)
    return v if isinstance(v, str) else None


def role(element) -> str:
    m = _as()
    if m is None:
        return ""
    return attr(element, m.kAXRoleAttribute) or ""


def _value_get(raw, type_const):
    m = _as()
    if m is None:
        return None
    try:
        res = m.AXValueGetValue(raw, type_const, None)
    except Exception:
        return None
    # pyobjc 可能返回 (ok, value) 或直接返回值
    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], bool):
        return res[1] if res[0] else None
    return res


def _pair(v):
    if v is None:
        return None
    try:
        return float(v[0]), float(v[1])
    except Exception:
        pass
    for a, b in (("x", "y"), ("width", "height")):
        if hasattr(v, a) and hasattr(v, b):
            return float(getattr(v, a)), float(getattr(v, b))
    return None


def frame(element):
    """返回 (x, y, w, h)（左上原点、屏幕点坐标）；取不到返回 None。"""
    m = _as()
    if m is None:
        return None
    pt = _pair(_value_get(attr(element, m.kAXPositionAttribute), m.kAXValueCGPointType))
    sz = _pair(_value_get(attr(element, m.kAXSizeAttribute), m.kAXValueCGSizeType))
    if not pt or not sz:
        return None
    return pt[0], pt[1], sz[0], sz[1]


def windows_of(pid: int):
    """某进程的全部 AX 窗口；失败返回 []。"""
    m = _as()
    if m is None:
        return []
    app = app_element(pid)
    if app is None:
        return []
    wins = attr(app, m.kAXWindowsAttribute)
    try:
        return list(wins) if wins else []
    except Exception:
        return []


def focused_window_frame(pid: int):
    """前台焦点窗口的 (x, y, w, h)；取不到返回 None。"""
    m = _as()
    if m is None:
        return None
    app = app_element(pid)
    if app is None:
        return None
    win = attr(app, m.kAXFocusedWindowAttribute)
    return frame(win) if win is not None else None


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def set_value(element, new_text: str) -> bool:
    """直接写 AXValue（写入输入框的首选方式：不动剪贴板、不模拟按键）。"""
    m = _as()
    if m is None or element is None:
        return False
    try:
        return m.AXUIElementSetAttributeValue(element, m.kAXValueAttribute, new_text) == 0
    except Exception as e:
        log.warning(f"AX 写入失败: {e}")
        return False


def focus(element) -> bool:
    """把键盘焦点设到该元素。"""
    m = _as()
    if m is None or element is None:
        return False
    try:
        return m.AXUIElementSetAttributeValue(element, m.kAXFocusedAttribute, True) == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 定位输入框
# ---------------------------------------------------------------------------

def largest_text_area(root, min_area: float = MIN_INPUT_AREA):
    """在 root 子树里找面积最大、且超过 min_area 的 AXTextArea。

    这是「区分消息输入框 与 左侧搜索框」的关键：搜索框也很小但会先被命中，
    只取第一个就会把文字写进搜索框。返回 (element, frame, text) 或 None。
    """
    best = None
    for el, _depth, r in walk(root):
        if r != "AXTextArea":
            continue
        f = frame(el)
        if not f:
            continue
        area = f[2] * f[3]
        if area < min_area:
            continue
        if best is None or area > best[0]:
            best = (area, el, f)
    if best is None:
        return None
    return best[1], best[2], (value(best[1]) or "")


def find_input_box(pid: int, window_frame=None):
    """定位微信消息输入框，返回 (element, frame, current_text) 或 None。

    window_frame 给出时优先选与它相交的窗口（微信有多个 AX 窗口时避免选错）。
    """
    m = _as()
    if m is None:
        return None
    app = app_element(pid)
    if app is None:
        return None

    wins = windows_of(pid)
    if wins:
        # 优先焦点窗口，其余按「与目标窗口相交 → 面积大」排序
        focus = attr(app, m.kAXFocusedWindowAttribute)
        ordered = ([focus] if focus is not None else []) + [w for w in wins if w is not focus]
        if window_frame:
            wx, wy, ww, wh = window_frame

            def _intersects(w):
                f = frame(w)
                if not f:
                    return False
                return not (f[0] + f[2] < wx or wx + ww < f[0]
                            or f[1] + f[3] < wy or wy + wh < f[1])

            ordered.sort(key=lambda w: (not _intersects(w),
                                        -(frame(w) or (0, 0, 0, 0))[2]))
        for w in wins:
            if w is not focus:
                continue
            hit = largest_text_area(w)
            if hit:
                return hit
        for w in ordered:
            hit = largest_text_area(w)
            if hit:
                return hit
    return largest_text_area(app)
