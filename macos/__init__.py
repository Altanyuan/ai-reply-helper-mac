"""macOS 系统集成层。

本程序**只支持 macOS**。所有系统 API 都收敛在这个包里，业务模块
（`main.py` / `ai_client.py` / `ui.py` …）不直接调用任何系统框架：

    window.py     微信窗口定位（Quartz 窗口元数据）
    capture.py    截图 + Vision OCR 读取聊天上下文
    ocr.py        Vision 框架 OCR
    ax.py         辅助功能（Accessibility）：读写输入框
    keys.py       按键与修饰键（Cmd）
    paste.py      把选中的版本回填到微信输入框
    tray.py       悬浮图标宿主（同时持有主线程 Tk 解释器）
    icon.py       原生 NSPanel 悬浮图标
    instance.py   单实例（flock）

依赖的系统框架（通过 PyObjC）
----------------------------
    Quartz / ApplicationServices / Vision / AppKit / Foundation

两项系统权限
------------
缺权限的表现是「按了没反应」，所以启动时会把缺哪一项明确写进日志与终端：

* **屏幕录制**：截图必需。授权后**必须退出重开本程序**才生效（系统限制）。
* **辅助功能**：读输入框草稿、回填、监听全局热键必需。授权后**立即生效**。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

# 导入顺序：先把叶子模块装好，避免子模块互相 `from macos import x` 时属性还没
# 绑定（Python 会兜底去 import 子模块，但显式更稳）。
from macos import ax, keys, ocr, window              # noqa: F401
from macos import capture, paste, tray               # noqa: F401
from macos.instance import ensure_single_instance    # noqa: F401

log = logging.getLogger("polish")

#: 建议默认热键（config.json 里的 hotkey 优先）
DEFAULT_HOTKEY = "ctrl+alt+p"

#: 打印给用户的「怎么打开设置页」说明
SETTINGS_HINT = (
    "右键点击右下角悬浮图标 → 「打开设置…」；也可按「鼠标中键」"
    "（有中键的鼠标）打开设置页（默认「软件作者」页）"
)

#: 能力自述：说明本项目读上下文只有「截图 + OCR」一条通路
CAPABILITIES_TEXT = (
    "上下文读取=截图（screencapture -l 按窗口 ID）+ Vision 中文 OCR + 几何判左右；"
    "需要屏幕录制与辅助功能权限；"
    "微信不暴露可读的控件树，只能走图像识别，存在固有误差"
)


def describe() -> str:
    """一行能力摘要，启动时写日志用。"""
    return CAPABILITIES_TEXT


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

def run_event_loop(tray_obj):
    """主线程常驻。

    tkinter 在 macOS 上必须由主线程跑 mainloop，所以有悬浮动图时由它接管；
    没有（动图缺失）时退化为纯等待，保证进程不退出、热键继续有效。
    """
    if tray_obj is not None:
        tray_obj.run()
        return
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# 目标窗口
# ---------------------------------------------------------------------------

def find_wechat_control(previous_wid=None):
    return window.find_wechat_control(previous_wid)


def find_wechat_window_id(previous_wid=None):
    return window.find_wechat_window_id(previous_wid)


def is_wechat_running():
    return window.is_wechat_running()


def get_foreground_window():
    return window.get_foreground_window()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def read_wechat_recent(n=5, use_ocr=True, window_id=None):
    return capture.read_wechat_recent(n=n, use_ocr=use_ocr, window_id=window_id)


def read_wechat_input():
    return capture.read_wechat_input()


def capture_draft():
    return capture.capture_draft()


def last_context_error() -> str:
    """上一次读取聊天上下文失败的原因；成功时为空串。

    给界面用：把「为什么读不到」直接显示给用户。否则用户只能看到
    「没读到聊天上下文」这种含糊提示，真正的原因（缺权限 / 窗口没开 /
    截图失败）只躺在加密日志里，等于没说。
    """
    return capture.last_context_error()


# ---------------------------------------------------------------------------
# 回填
# ---------------------------------------------------------------------------

def paste_back(text, target_window_id=None, delay=0.05):
    return paste.paste_back(text, target_window_id=target_window_id, delay=delay)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def make_tray(idle_path, active_path, size=220, pos=None, on_move=None):
    return tray.make_tray(idle_path, active_path, size=size, pos=pos, on_move=on_move)


# ---------------------------------------------------------------------------
# 状态 / 权限（启动时写日志，便于排查「按了没反应」）
# ---------------------------------------------------------------------------

def ocr_status_text() -> str:
    return ocr.status_text()


def hotkey_ready() -> bool:
    """全局热键是否真的会生效。

    必须单独提供这个判断：缺「辅助功能」权限时 pynput **不会抛异常**，
    只在日志里留一行 "This process is not trusted!"，然后收不到任何按键 ——
    用户看到的是「程序启动了但按热键没反应」，属于最难排查的一类问题。
    """
    return ax.is_trusted() is not False


#: 依赖模块 → pip 包名（用于给出可直接复制的安装命令）
_DEP_MODULES = {
    "objc": "pyobjc-framework-Cocoa",
    "Foundation": "pyobjc-framework-Cocoa",
    "AppKit": "pyobjc-framework-Cocoa",
    "Quartz": "pyobjc-framework-Quartz",
    "Vision": "pyobjc-framework-Vision",
    "ApplicationServices": "pyobjc-framework-ApplicationServices",
}


def missing_deps() -> list:
    """缺哪些运行依赖（正常安装后应为空）。"""
    out = []
    for m in _DEP_MODULES:
        try:
            __import__(m)
        except Exception:
            out.append(m)
    return out


def deps_hint() -> str:
    """缺依赖时给出可复制的安装命令。"""
    miss = missing_deps()
    if not miss:
        return ""
    pkgs = sorted({_DEP_MODULES[m] for m in miss})
    return ("缺少运行依赖：" + ", ".join(miss) + "\n"
            "  安装：python3 -m pip install " + " ".join(pkgs))


def permission_report() -> dict:
    """权限现状。任何一项都可能是 None（无法检测），调用方需容忍。

    两项都是**实时向系统查询**，没有任何缓存：
    * `AXIsProcessTrusted()` 在用户打开开关后**立即**返回 True（无需重启）；
    * `CGPreflightScreenCaptureAccess()` 的结果在**进程启动时**就固定了，
      所以「屏幕录制」授权后必须退出重开本程序才会变成 True。

    也就是说，如果本函数说没授权，那就是系统真的还没给 —— 不是我们读旧值。
    """
    rep = {"accessibility": ax.is_trusted(), "screen_recording": None}
    try:
        import Quartz
        rep["screen_recording"] = bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        pass
    return rep


def transient_volume() -> str | None:
    """程序若跑在只读卷（DMG 安装包等）上，返回该卷的挂载点；否则返回 None。

    为什么必须拦住这种情况（这是本项目最坑的一处，用户完全无从察觉）：
    **DMG 是只读卷，macOS 无法把系统权限稳定地记在上面。** 用户双击 DMG 里的
    图标就能启动程序，看起来一切正常，于是直接在 DMG 里授权 —— 但那个授权条目
    绑在 `/Volumes/话术润色助手 2` 这类临时路径上；一旦弹出映像或重新挂载
    （卷名会自动加序号：话术润色助手 → 话术润色助手 1 → 2），路径就变了，
    新挂载的这份不再匹配旧授权。用户看到的是「我明明授权了，程序还说没授权」，
    没有任何线索指向「你是在安装包里运行的」。

    判据只用「**App 所在的文件系统是否只读**」，实测足够干净：

    ================================  ==========
    位置                               只读？
    ================================  ==========
    /Applications（正常安装）           否  ← 放行
    ~/Applications、dist/（构建产物）   否  ← 放行
    DMG 挂载点（不论挂到哪）            是  ← 拦截
    ================================  ==========

    为什么不用「路径是否以 /Volumes/ 开头」：那样**换个挂载点就失效**（我自己
    把 DMG 挂到 /tmp 测过，判据直接漏掉）；而且外接硬盘也在 /Volumes/ 下却可写，
    按前缀判会误拦。只读标志不依赖挂载路径，外接硬盘也不会被误伤。

    保险：若只读卷恰好就是启动卷本身（macOS 11+ 的启动卷是密封只读的），
    仍然放行 —— 宁可漏拦，也不要把正常安装挡在门外。
    """
    try:
        exe = Path(sys.executable).resolve()
    except Exception:
        return None

    # 判断对象取 .app 包本身（源码运行时没有 .app，退回可执行文件所在目录）
    target = exe.parent
    for parent in exe.parents:
        if parent.suffix == ".app":
            target = parent
            break

    try:
        if not (os.statvfs(target).f_flag & os.ST_RDONLY):
            return None                     # 可写 → 正常安装，放行
    except Exception:
        return None

    # 只读：向上找到挂载点，用于在提示里告诉用户「你运行的是哪个卷」
    probe = target
    try:
        while not os.path.ismount(probe):
            parent = probe.parent
            if parent == probe:
                break
            probe = parent
        if Path(probe).samefile("/"):       # 启动卷只读属正常，不拦
            return None
    except Exception:
        pass
    return str(probe)


def settings_list_name() -> str:
    """本程序在「系统设置 › 隐私与安全性」列表里显示的名字。

    取的是 **.app 的文件名**（去掉 `.app`），**不是** Info.plist 里的
    `CFBundleName`：两者不一致时，用户会照着界面上的中文名去列表里找，
    结果一个都找不到（本项目踩过这个坑，所以特意把 `.app` 也命名成中文）。

    引导文案一律用本函数返回的名字，不要写死 —— 源码运行时权限其实记在
    「终端」名下，写死中文名会误导。
    """
    try:
        exe = Path(sys.executable).resolve()
    except Exception:
        return "话术润色助手"
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent.stem
    # 源码运行时，权限记在「终端 / Python」名下，本程序名不适用
    return "终端（或你的终端 App）"


#: 「去授权」按钮要跳转的系统设置面板。
#: 新版 macOS 用 settings.PrivacySecurity.extension，旧版是 preference.security，
#: 按顺序试，取第一个能打开的（实测 macOS 26 两个都能用）。
_SETTINGS_PANES = {
    "accessibility": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_Accessibility",
        "x-apple.systempreferences:com.apple.preference.security"
        "?Privacy_Accessibility",
    ),
    "screen_recording": (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
        "?Privacy_ScreenCapture",
        "x-apple.systempreferences:com.apple.preference.security"
        "?Privacy_ScreenCapture",
    ),
}


def open_settings_pane(kind: str) -> bool:
    """打开「系统设置 › 隐私与安全性」里对应的授权面板。

    给权限引导对话框上的「去授权」按钮用：让用户少走几步，
    直接落到要勾选的那一页。
    """
    for url in _SETTINGS_PANES.get(kind, ()):
        try:
            rc = subprocess.run(["open", url], timeout=5,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL).returncode
            if rc == 0:
                return True
        except Exception:
            continue
    return False


def startup_issues() -> list:
    """启动自检的结构化结果。

    每项形如 ``{"kind": ..., "text": ...}``：

    * ``kind`` 为 ``"accessibility"`` / ``"screen_recording"`` 时，
      调用方知道该显示哪个「去授权」按钮；
    * ``kind`` 为 ``"other"``（缺依赖、Tk 过低等）时只展示文本。

    为什么要结构化：打包成 .app 双击启动时**没有终端**，自检结果必须用对话框
    呈现给用户；对话框既要能判断按钮状态，也要能原样展示非权限类问题，不能丢信息。
    """
    out = []
    name = settings_list_name()      # 列表里显示的名字（=.app 文件名），别用中文名
    dh = deps_hint()
    if dh:
        out.append({"kind": "other", "text": "· " + dh})
    try:
        tw = tray.tk_warning()          # 该文案自带「· 」前缀
        if tw:
            out.append({"kind": "other", "text": tw})
    except Exception:
        pass
    try:
        rep = permission_report()
    except Exception:
        rep = {}
    if rep.get("accessibility") is False:
        out.append({
            "kind": "accessibility",
            "text": f"· 「辅助功能」未授权：读草稿 / 回填 / 全局热键会失效。"
                    f"去 系统设置 › 隐私与安全性 › 辅助功能，把列表里 "
                    f"「{name}」右侧的开关打开（立即生效，不用重启）",
        })
    if rep.get("screen_recording") is False:
        out.append({
            "kind": "screen_recording",
            "text": f"· 「屏幕录制」未授权：读聊天上下文会失败。"
                    f"去 系统设置 › 隐私与安全性 › 录屏与系统录音，把列表里 "
                    f"「{name}」右侧的开关打开，"
                    f"然后必须退出并重开本程序才生效",
        })
    return out


def permission_hint() -> str:
    """启动自检的可读文本（终端打印与写日志用）。

    统一放在这里，是为了让启动时有**一个**清晰的地方告诉用户
    「为什么界面/热键没反应」，而不是让用户去猜。
    """
    return "\n".join(i["text"] for i in startup_issues())


def request_permissions() -> dict:
    """主动弹系统授权框（辅助功能 + 屏幕录制）。"""
    out = {"accessibility": ax.request_trust(), "screen_recording": None}
    try:
        import Quartz
        out["screen_recording"] = bool(Quartz.CGRequestScreenCaptureAccess())
    except Exception:
        pass
    return out
