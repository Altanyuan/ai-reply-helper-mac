"""macOS 端 P0 可行性探针 —— 共用工具库。

设计原则
--------
1. **零耦合**：只依赖 pyobjc 各子框架 + Pillow，不 import 本项目任何模块，
   可以单独把 `probe/` 整个目录拷到别的机器上跑。
2. **不崩**：所有系统调用都包了异常，失败一律返回 None / False / 空列表，
   由调用方决定怎么报告。探针本身绝不允许因为某个 API 不支持就整个挂掉。
3. **双份输出**：控制台给人看（中文、分节、可读），JSON 给后续脚本消费。

为什么截图用 `screencapture` 子进程而不是 Quartz 的 CGWindowListCreateImage
------------------------------------------------------------------
macOS 进入 ScreenCaptureKit 时代后，Python 线程**无法取消** CGWindowListCreateImage，
一旦底层卡住就会永久挂起（实测会让整个 UI 线程死掉）。独立子进程可以被
`subprocess.run(timeout=...)` 杀掉，且可复用工作线程。参见同类项目的踩坑注释。
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 微信窗口归属白名单。**必须精确匹配**，不能做子串包含 ——
#: 「微信读书」「微信输入法」「企业微信」的 owner 名字里都带"微信"，
#: 用子串匹配会把它们的窗口当微信截走（同类项目踩过这个坑，有专门回归测试）。
WECHAT_OWNER_NAMES = ("WeChat", "微信", "Weixin")
WECHAT_BUNDLE_IDS = ("com.tencent.xinWeChat", "com.tencent.xinWeChat.debug")

#: Vision OCR 语言：中文优先，英文兜底（数字/链接/英文名需要）。
OCR_LANGUAGES = ("zh-Hans", "en-US")

#: screencapture 子进程超时（秒）。
SUBPROCESS_TIMEOUT_S = 5.0

#: 小于这个字节数的 PNG 视为抓取失败（通常是没给屏幕录制权限）。
MIN_PNG_BYTES = 1000

#: AX 遍历硬上限，防止离屏巨型控件树吃光预算。
AX_MAX_NODES = 8000
AX_MAX_DEPTH = 40

#: 视为「可能含文本」的 AX role。
AX_TEXT_ROLES = ("AXStaticText", "AXTextField", "AXTextArea", "AXHeading")

_DEP_MODULES = {
    "objc": "pyobjc-framework-Cocoa",
    "Foundation": "pyobjc-framework-Cocoa",
    "AppKit": "pyobjc-framework-Cocoa",
    "Quartz": "pyobjc-framework-Quartz",
    "Vision": "pyobjc-framework-Vision",
    "ApplicationServices": "pyobjc-framework-ApplicationServices",
    "PIL": "Pillow",
}


# ---------------------------------------------------------------------------
# 依赖自检 / 环境信息
# ---------------------------------------------------------------------------

def missing_deps() -> list:
    """返回缺失的模块名列表。"""
    out = []
    for mod in _DEP_MODULES:
        try:
            __import__(mod)
        except Exception:
            out.append(mod)
    return out


def ensure_deps() -> bool:
    """依赖自检；缺失时打印可复制的安装命令并返回 False。"""
    missing = missing_deps()
    if not missing:
        return True
    pkgs = sorted({_DEP_MODULES[m] for m in missing})
    req = Path(__file__).resolve().parent.parent / "requirements.txt"
    print("✗ 缺少依赖：" + ", ".join(missing))
    print()
    print("  一条命令装齐（推荐）：")
    print(f'      python3 -m pip install -r "{req}"')
    print()
    print("  或只装缺的：")
    print(f'      python3 -m pip install {" ".join(pkgs)}')
    print()
    return False


def env_info() -> dict:
    """本机环境信息（macOS 版本 / Python / 架构 / 是否在 Rosetta 下）。"""
    import platform

    info = {
        "macos": platform.mac_ver()[0],
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "rosetta": False,
    }
    try:
        r = subprocess.run(
            ["sysctl", "-n", "sysctl.proc_translated"],
            capture_output=True, text=True, timeout=2,
        )
        info["rosetta"] = r.stdout.strip() == "1"
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------

def accessibility_trusted():
    """辅助功能权限是否已授予（实时生效，无需重启进程）。"""
    from ApplicationServices import AXIsProcessTrusted
    try:
        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def request_accessibility():
    """弹系统授权框申请辅助功能权限。返回申请前的状态（授权是异步的）。"""
    from ApplicationServices import (
        AXIsProcessTrustedWithOptions,
        kAXTrustedCheckOptionPrompt,
    )
    try:
        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    except Exception:
        return None


def screen_capture_allowed():
    """屏幕录制权限是否已授予。老系统无此 API 时返回 None（视为不拦）。"""
    import Quartz
    try:
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def request_screen_capture():
    """申请屏幕录制权限。**授权后必须退出并重开本进程才生效**（系统限制）。"""
    import Quartz
    try:
        return bool(Quartz.CGRequestScreenCaptureAccess())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 窗口枚举
# ---------------------------------------------------------------------------

def list_windows(owners=WECHAT_OWNER_NAMES, on_screen_only: bool = False) -> list:
    """枚举窗口，返回按面积降序的字典列表。

    owners=None 时返回全部窗口（用于第一次查看微信到底叫什么名字）。
    on_screen_only=False 用 kCGWindowListOptionAll，这样窗口被其它程序遮挡、
    甚至最小化时也能枚举到（遮挡是本探针要重点验证的场景之一）。
    """
    import Quartz

    opts = (Quartz.kCGWindowListOptionOnScreenOnly if on_screen_only
            else Quartz.kCGWindowListOptionAll)
    opts |= Quartz.kCGWindowListExcludeDesktopElements

    raw = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
    out = []
    for w in raw:
        owner = w.get("kCGWindowOwnerName") or ""
        if owners is not None and owner not in owners:      # 精确匹配，不用 in
            continue
        b = w.get("kCGWindowBounds") or {}
        try:
            geom = (int(b.get("X", 0)), int(b.get("Y", 0)),
                    int(b.get("Width", 0)), int(b.get("Height", 0)))
        except Exception:
            geom = (0, 0, 0, 0)
        out.append({
            "wid": int(w.get("kCGWindowNumber") or 0),
            "pid": int(w.get("kCGWindowOwnerPID") or 0),
            "owner": owner,
            "title": w.get("kCGWindowName") or "",
            "layer": int(w.get("kCGWindowLayer") or 0),
            "onscreen": bool(w.get("kCGWindowIsOnscreen", False)),
            "x": geom[0], "y": geom[1], "w": geom[2], "h": geom[3],
        })
    out.sort(key=lambda r: r["w"] * r["h"], reverse=True)
    return out


def pick_main_window(windows: list):
    """从候选里挑「主窗口」：优先 layer==0（正常窗口层级）、面积最大者。

    微信在 Mac 上可能同时存在：主窗口、独立聊天窗口、偏好设置窗口、
    以及若干 layer!=0 的辅助面板。这一条启发式够探针用。
    """
    normal = [w for w in windows if w["layer"] == 0 and w["w"] > 200 and w["h"] > 200]
    pool = normal or windows
    return pool[0] if pool else None


# ---------------------------------------------------------------------------
# 截图
# ---------------------------------------------------------------------------

def capture_window(wid: int, out_path, timeout: float = SUBPROCESS_TIMEOUT_S):
    """按窗口 ID 截图为 PNG。返回 (是否成功, 说明文字)。

    用子进程而不是 Quartz API：① 可被超时杀掉，不会永久挂起线程；
    ② 窗口被遮挡、不在前台时同样能截到（不需要把窗口提到前台，
    因此不会打断用户正在做的事）。
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run(
            ["screencapture", "-x", "-o", "-l", str(wid), str(out)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"截图超时（>{timeout}s）——窗口可能已失效"
    except OSError as e:
        return False, f"无法执行 screencapture：{e}"

    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        return False, f"screencapture 退出码 {p.returncode}：{err[0][:160] if err else '无错误输出'}"
    if not out.exists():
        return False, "截图文件未生成"
    size = out.stat().st_size
    if size < MIN_PNG_BYTES:
        return False, (f"截图文件过小（{size} 字节）——通常是**没有屏幕录制权限**，"
                       f"或该窗口当前不可渲染")
    return True, f"{size / 1024:.0f} KB"


# ---------------------------------------------------------------------------
# 图像
# ---------------------------------------------------------------------------

def load_cgimage(path, max_px: int = None):
    """把 PNG 读成内存驻留的 CGImage（临时文件删掉后像素仍可用）。

    max_px 传入时用缩略图接口降采样：Retina 截出来是 2x 像素，
    降到「点尺寸」能把 OCR 成本直接砍半（同类项目的做法）。
    """
    import Quartz
    from Foundation import NSData

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
        img = Quartz.CGImageSourceCreateThumbnailAtIndex(src, 0, opts)
        if img is not None:
            return img
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def cgimage_size(img):
    """返回 (width, height) 像素。"""
    import Quartz
    try:
        return int(Quartz.CGImageGetWidth(img)), int(Quartz.CGImageGetHeight(img))
    except Exception:
        return 0, 0


def rect_parts(rect):
    """兼容 pyobjc 把 CGRect 返回成 ((x,y),(w,h)) 或带 .origin/.size 的对象。"""
    if rect is None:
        return None
    try:
        (x, y), (w, h) = rect
        return float(x), float(y), float(w), float(h)
    except Exception:
        pass
    o, s = getattr(rect, "origin", None), getattr(rect, "size", None)
    if o is not None and s is not None:
        return float(o.x), float(o.y), float(s.width), float(s.height)
    try:
        vals = list(rect)
        return float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3])
    except Exception:
        return None


def detect_input_top_ratio(pil_img, x_min: float = 0.32,
                           min_run: float = 0.62, min_delta: int = 12,
                           search_lo: float = 0.45, search_hi: float = 0.96):
    """在截图里找「输入区上分隔线」，返回 top-origin 归一化 y（0..1）。

    思路：输入区顶部通常是一条横跨右侧窗格的长水平线。把图缩到宽 640 的灰度，
    在 y∈[search_lo, search_hi] 范围内**从下往上**找第一条「相邻行灰度差
    ≥ min_delta 的像素占右侧窗格 ≥ min_run」的行。

    找不到返回 None（调用方应回落到固定比例常量）。
    """
    try:
        g = pil_img.convert("L")
    except Exception:
        return None
    W, H = g.size
    if W < 80 or H < 80:
        return None
    if W > 640:
        g = g.resize((640, max(1, int(H * 640 / W))))
        W, H = g.size
    try:
        px = g.load()
    except Exception:
        return None

    x0 = int(W * x_min)
    span = max(1, W - x0)
    y_lo = max(1, int(H * search_lo))
    y_hi = min(H - 1, int(H * search_hi))
    for y in range(y_hi, y_lo, -1):
        run = 0
        for x in range(x0, W):
            if abs(px[x, y] - px[x, y - 1]) >= min_delta:
                run += 1
        if run / span >= min_run:
            return y / H
    return None


# ---------------------------------------------------------------------------
# Vision OCR
# ---------------------------------------------------------------------------

def ocr_image(cgimage, languages=OCR_LANGUAGES, roi=None) -> list:
    """对 CGImage 跑 Vision OCR，返回文本块列表。

    每块：{"text", "conf", "x", "y", "w", "h"}，坐标是**归一化、原点左下**
    （Vision 的原生约定）。roi 为归一化 (x, y, w, h)（同样原点左下）时只识别该区域，
    返回的坐标会被换算回**整图**归一化坐标（Vision 只给相对 ROI 的框，这是个常见坑）。
    """
    import Vision

    blocks = []

    def _handler(request, error):
        if error is not None:
            return
        for obs in (request.results() or []):
            try:
                cands = obs.topCandidates_(1)
            except Exception:
                continue
            if not cands:
                continue
            cand = cands[0]
            text = str(cand.string() or "").strip()
            if not text:
                continue
            try:
                conf = float(cand.confidence())
            except Exception:
                conf = 0.0
            parts = rect_parts(obs.boundingBox())
            if parts is None:
                continue
            x, y, w, h = parts
            if roi:
                rx, ry, rw, rh = roi
                x, y, w, h = rx + x * rw, ry + y * rh, w * rw, h * rh
            blocks.append({"text": text, "conf": conf, "x": x, "y": y, "w": w, "h": h})

    req = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(_handler)
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    try:
        req.setRecognitionLanguages_(list(languages))
    except Exception:
        pass
    try:
        req.setUsesLanguageCorrection_(True)
    except Exception:
        pass
    if roi:
        import Quartz
        try:
            req.setRegionOfInterest_(Quartz.CGRectMake(*roi))
        except Exception:
            pass

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cgimage, None)
    try:
        handler.performRequests_error_([req], None)
    except Exception:
        pass
    return blocks


def to_top_origin(blocks: list) -> list:
    """把归一化坐标从「原点左下」转成「原点在上」，并按 top-origin 补 top/right 字段。"""
    out = []
    for b in blocks:
        nb = dict(b)
        nb["top"] = 1.0 - b["y"] - b["h"]      # 盒子**上沿**到顶部的距离
        nb["right"] = b["x"] + b["w"]
        out.append(nb)
    return out


# ---------------------------------------------------------------------------
# 文本行合并（把 OCR 碎块还原成可读的行，供人眼核对识别质量）
# ---------------------------------------------------------------------------

def is_cjk(ch: str) -> bool:
    """该字符是否属于中日韩文字（含全角标点）。"""
    return "\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def smart_join(parts: list) -> str:
    """拼接同一行的碎块：中日韩字符之间不插空格，其余按空格分隔。"""
    out = ""
    for p in parts:
        if not out:
            out = p
            continue
        if is_cjk(out[-1]) or is_cjk(p[0]):
            out += p
        else:
            out += " " + p
    return out


def group_lines(blocks: list, y_tol: float = 0.014) -> list:
    """按纵向位置把文本块合并成行，返回 [{"top", "text", "blocks"}]（从上到下）。"""
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
            lines.append({"top": b["top"], "text": "", "blocks": [b]})
        else:
            hit["blocks"].append(b)
    for ln in lines:
        ln["blocks"].sort(key=lambda b: b["x"])
        ln["text"] = smart_join([x["text"] for x in ln["blocks"]])
    lines.sort(key=lambda ln: ln["top"])
    return lines


#: 与 macos/capture.py 保持一致：左对齐=对方，右对齐=我
LEFT_START_MAX = 0.22
RIGHT_END_MIN = 0.70


#: 噪声规则与 macos/capture.py 的 _is_noise / _NOISE_PATTERNS **保持同步**：
#: 探针要用和生产一样的过滤，否则校准出来的区域常量与真实效果对不上。
NOISE_PATTERNS = (
    r"^\d{1,2}:\d{2}$",
    r"^\d{1,2}月\d{1,2}日",
    r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}",
    r"^(昨天|今天|前天|星期[一二三四五六日天])\s*\d{0,2}:?\d{0,2}$",
    r"^\d{4}年\d{1,2}月\d{1,2}日",
    r"^\d+\.\d\s*[KMGTP]B?$",       # 文件大小 6.1M / 285.0MB
    r"^\d+\s*[KMGTP]B$",            # 6MB
)

NOISE_KEYWORDS = (
    "对方正在输入", "以下为新消息", "查看更多消息", "撤回了一条消息",
    "退出了群聊", "加入了群聊", "邀请你加入了群聊", "以上是打招呼的内容",
)

def strip_decor(text: str) -> str:
    """剥掉两端装饰性字符（标点/符号/空白），只留实义内容。"""
    i, j = 0, len(text)
    while i < j and not (text[i].isalnum() or is_cjk(text[i])):
        i += 1
    while j > i and not (text[j - 1].isalnum() or is_cjk(text[j - 1])):
        j -= 1
    return text[i:j]


def is_noise(text: str) -> bool:
    """时间戳 / 系统提示 / 文件大小等噪声行判定（与生产代码同规则）。"""
    import re
    s = (text or "").strip()
    if not s:
        return True
    if any(kw in s for kw in NOISE_KEYWORDS):
        return True
    if any(re.match(p, s) for p in NOISE_PATTERNS):
        return True
    if strip_decor(s) == "微信电脑版":
        return True
    return False


def classify_side(x: float, w: float) -> str:
    """按几何位置判「我 / 对方 / 未确认」。

    坐标应为**聊天窗格内**的归一化横坐标（窗格左沿为 0、右沿为 1）。
    判据是「文字框贴哪一边」：对方左对齐、自己右对齐。左对齐优先判定，
    否则一条很长的对方消息会被误认成自己说的。
    判不准的一律返回 unknown —— 宁可标未确认，也不要把对方的话当自己的话回复。
    """
    right = x + w
    if x <= LEFT_START_MAX:
        return "them"
    if right >= RIGHT_END_MIN:
        return "me"
    return "unknown"


# ---------------------------------------------------------------------------
# AX（辅助功能）读取
# ---------------------------------------------------------------------------

def ax_attr(element, name):
    """读一个 AX 属性；失败返回 None。**这是全库访问 AX 的唯一入口，永不抛异常。**"""
    from ApplicationServices import AXUIElementCopyAttributeValue
    try:
        err, val = AXUIElementCopyAttributeValue(element, name, None)
        return val if err == 0 else None
    except Exception:
        return None


def ax_walk(root, max_nodes: int = AX_MAX_NODES, max_depth: int = AX_MAX_DEPTH):
    """有界 DFS，产出 (element, depth, role)。带硬上限，避免离屏巨型树吃光预算。"""
    from ApplicationServices import kAXChildrenAttribute, kAXRoleAttribute

    stack = [(root, 0)]
    seen = 0
    while stack:
        el, depth = stack.pop()
        if depth > max_depth or seen >= max_nodes:
            continue
        seen += 1
        yield el, depth, (ax_attr(el, kAXRoleAttribute) or "")
        children = ax_attr(el, kAXChildrenAttribute)
        if children:
            try:
                for ch in reversed(list(children)):
                    stack.append((ch, depth + 1))
            except Exception:
                pass


def ax_text(element) -> str:
    """取节点的文本：AXValue → AXTitle → AXDescription，第一个非空者。"""
    from ApplicationServices import (
        kAXDescriptionAttribute, kAXTitleAttribute, kAXValueAttribute,
    )
    for name in (kAXValueAttribute, kAXTitleAttribute, kAXDescriptionAttribute):
        v = ax_attr(element, name)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _ax_get_value(raw, type_const):
    """调 AXValueGetValue；pyobjc 可能返回 (ok, value) 或直接返回值。"""
    from ApplicationServices import AXValueGetValue
    try:
        res = AXValueGetValue(raw, type_const, None)
    except Exception:
        return None
    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], bool):
        return res[1] if res[0] else None
    return res


def _num_pair(v):
    """从 CGPoint / CGSize 里取 (a, b)，兼容元组与属性访问。"""
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


def ax_frame(element):
    """返回 (x, y, w, h)（左上原点、屏幕点坐标）；取不到返回 None。"""
    from ApplicationServices import (
        kAXPositionAttribute, kAXSizeAttribute,
        kAXValueCGPointType, kAXValueCGSizeType,
    )
    pt = _num_pair(_ax_get_value(ax_attr(element, kAXPositionAttribute), kAXValueCGPointType))
    sz = _num_pair(_ax_get_value(ax_attr(element, kAXSizeAttribute), kAXValueCGSizeType))
    if not pt or not sz:
        return None
    return pt[0], pt[1], sz[0], sz[1]


def ax_windows(pid: int):
    """取某进程的全部 AX 窗口；失败返回 None。"""
    from ApplicationServices import AXUIElementCreateApplication, kAXWindowsAttribute
    try:
        app = AXUIElementCreateApplication(pid)
    except Exception:
        return None
    if app is None:
        return None
    wins = ax_attr(app, kAXWindowsAttribute)
    try:
        return list(wins) if wins else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def hr(title: str = "") -> None:
    """打印分节标题。"""
    if title:
        print()
        print("─" * 4 + f" {title} " + "─" * max(0, 60 - len(title)))
    else:
        print("─" * 64)


def save_json(path, obj) -> str:
    """写 JSON 报告（UTF-8、中文不转义），返回实际路径。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


def default_outdir() -> Path:
    """默认产物目录：probe/probe_out/。"""
    return Path(__file__).resolve().parent / "probe_out"


def role_histogram(elements) -> dict:
    """统计 role 频次（元素为 (el, depth, role) 三元组的可迭代对象）。"""
    c = Counter()
    for _el, _d, role in elements:
        c[role] += 1
    return dict(c.most_common(20))
