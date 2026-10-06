"""微信窗口定位（Quartz 窗口元数据）。

用 Quartz 枚举窗口的**元信息**（不抓像素，所以极快、不会卡），
拿 `kCGWindowNumber` 作为窗口标识（`wid`）。

两个务必注意的点
----------------
1. **owner 名字必须精确匹配**，不能用子串包含 —— 「微信读书」「微信输入法」
   「企业微信」的 owner 里都带"微信"，子串匹配会把它们的窗口当微信截走。
2. window id 会随窗口重建而变化，所以要**支持传入上一个 id 做「粘性」选择**，
   避免多窗口等大时每帧乱跳。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("polish")


@dataclass
class WindowRef:
    """一个被定位到的窗口。

    坐标是**全局屏幕坐标（点）**，原点在主屏左上角，与 Quartz / AX 一致。
    """

    wid: int = 0          # CGWindowNumber，窗口标识
    pid: int = 0          # 所属进程
    title: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    owner: str = ""       # 所属应用名（kCGWindowOwnerName）

    @property
    def area(self) -> int:
        """窗口面积（点²），用于在多个候选窗口里挑主窗口。"""
        return int(self.w * self.h)

    def rect(self):
        """(x, y, w, h)：与 AX 的窗口 frame 直接可比。"""
        return self.x, self.y, self.w, self.h

    def as_dict(self) -> dict:
        return {"wid": self.wid, "pid": self.pid, "title": self.title,
                "x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "owner": self.owner}

#: 微信的 owner 名 / bundle id（精确匹配）
WECHAT_OWNER_NAMES = ("WeChat", "微信", "Weixin")
WECHAT_BUNDLE_IDS = ("com.tencent.xinWeChat",)

#: 有效聊天窗口的最小尺寸（点）。太小的多半是浮层/偏好设置。
MIN_WIN_W = 400
MIN_WIN_H = 350


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

def list_windows(owners=WECHAT_OWNER_NAMES, on_screen_only: bool = False):
    """枚举窗口，按面积降序返回 WindowRef 列表。

    owners=None 时返回全部窗口（用于排查「微信在系统里叫什么名字」）。
    on_screen_only=False 用 kCGWindowListOptionAll，窗口被遮挡、不在前台也能枚举到。
    """
    try:
        import Quartz
        opts = (Quartz.kCGWindowListOptionOnScreenOnly if on_screen_only
                else Quartz.kCGWindowListOptionAll)
        opts |= Quartz.kCGWindowListExcludeDesktopElements
        raw = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
    except Exception as e:
        # 缺 pyobjc 或系统调用异常 → 当作"没有窗口"，让上层走可读的降级提示
        log.warning(f"枚举窗口失败（可能是缺少 pyobjc）：{e}")
        return []

    out = []
    for w in raw:
        owner = w.get("kCGWindowOwnerName") or ""
        if owners is not None and owner not in owners:      # 精确匹配
            continue
        b = w.get("kCGWindowBounds") or {}
        try:
            geom = (int(b.get("X", 0)), int(b.get("Y", 0)),
                    int(b.get("Width", 0)), int(b.get("Height", 0)))
        except Exception:
            geom = (0, 0, 0, 0)
        out.append(WindowRef(
            wid=int(w.get("kCGWindowNumber") or 0),
            pid=int(w.get("kCGWindowOwnerPID") or 0),
            title=(w.get("kCGWindowName") or ""),
            x=geom[0], y=geom[1], w=geom[2], h=geom[3],
            owner=owner,
        ))
    out.sort(key=lambda r: r.area, reverse=True)
    return out


def pick_main(windows, previous_wid=None):
    """挑主窗口：优先 layer==0 且够大的；再优先「粘住」上一次的 window id。

    layer!=0 的多是浮动面板；面积过小的多是偏好设置窗口。
    粘性是为了避免用户同时开了主窗口+独立聊天窗口且尺寸接近时反复横跳。
    """
    candidates = [w for w in windows if w.w >= MIN_WIN_W and w.h >= MIN_WIN_H]
    if not candidates:
        candidates = windows
    if not candidates:
        return None

    if previous_wid:
        for w in candidates:
            if w.wid == previous_wid:
                return w

    # 优先「标题不是应用自身名」的（微信主窗口标题常为空或"微信"，
    # 独立聊天窗口标题是联系人名）；其次比面积。
    def rank(w):
        self_named = w.title in ("", "微信", "WeChat", "Weixin")
        return (0 if self_named else 1, w.area)

    return max(candidates, key=rank)


def find_wechat_control(previous_wid=None):
    """定位微信主窗口，返回 WindowRef 或 None。"""
    wins = list_windows()
    return pick_main(wins, previous_wid)


def find_wechat_window_id(previous_wid=None):
    """微信主窗口的窗口 ID（int）或 None。"""
    w = find_wechat_control(previous_wid)
    return w.wid if w else None


# ---------------------------------------------------------------------------
# 进程
# ---------------------------------------------------------------------------

def running_app(bundle_ids=WECHAT_BUNDLE_IDS):
    """按 bundle id 找微信的 NSRunningApplication；找不到返回 None。"""
    try:
        import AppKit
    except Exception:
        return None
    for bid in bundle_ids:
        try:
            apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(bid)
            if apps and len(apps) > 0:
                return apps[0]
        except Exception:
            pass
    # 兜底：按显示名遍历（少数安装方式 bundle id 不同）
    try:
        for a in (AppKit.NSWorkspace.sharedWorkspace().runningApplications() or []):
            if (a.localizedName() or "") in WECHAT_OWNER_NAMES:
                return a
    except Exception:
        pass
    return None


def is_wechat_running() -> bool:
    """微信是否在运行。

    先看有没有窗口（能截图/读上下文的前提），没有窗口时再看进程，
    这样能区分「没装/没开」与「开了但缩起来/没开聊天窗口」两种提示文案。
    """
    if list_windows():
        return True
    return running_app() is not None


def get_foreground_window():
    """当前最前面那个应用的主窗口（WindowRef）或 None。

    CGWindowListCopyWindowInfo 按前→后排序，取第一个 layer==0 的即最前面窗口。
    """
    try:
        import Quartz
        opts = (Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements)
        raw = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
    except Exception:
        return None
    for w in raw:
        if int(w.get("kCGWindowLayer") or 0) != 0:
            continue
        b = w.get("kCGWindowBounds") or {}
        return WindowRef(
            wid=int(w.get("kCGWindowNumber") or 0),
            pid=int(w.get("kCGWindowOwnerPID") or 0),
            title=(w.get("kCGWindowName") or ""),
            x=int(b.get("X", 0)), y=int(b.get("Y", 0)),
            w=int(b.get("Width", 0)), h=int(b.get("Height", 0)),
            owner=(w.get("kCGWindowOwnerName") or ""),
        )
    return None


# ---------------------------------------------------------------------------
# 激活 / 还原
# ---------------------------------------------------------------------------

def activate(ref=None, pid=None) -> bool:
    """把微信提到前台（回填前需要）。

    用 NSRunningApplication 激活即可：同一用户会话内系统会允许，
    不需要绕过前台锁定那种做法。
    """
    app = None
    if ref is not None:
        try:
            import AppKit
            app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
                int(ref.pid))
        except Exception:
            app = None
    if app is None and pid:
        try:
            import AppKit
            app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        except Exception:
            app = None
    if app is None:
        app = running_app()
    if app is None:
        return False
    try:
        import AppKit
        opts = getattr(AppKit, "NSApplicationActivateIgnoringOtherApps", 1 << 1)
        return bool(app.activateWithOptions_(opts))
    except Exception as e:
        log.warning(f"激活微信失败: {e}")
        return False


def unminimize(pid: int) -> bool:
    """把被最小化的窗口还原，**不抢焦点**。

    最小化的窗口截不到图，所以读上下文前必须先还原——但**不能 activate**
    （那会抢走用户正在打字的焦点、打断输入法）。
    """
    from macos import ax
    if not pid:
        return False
    m = ax._as()
    if m is None:
        return False
    ok = False
    for win in ax.windows_of(pid):
        try:
            if m.AXUIElementSetAttributeValue(win, m.kAXMinimizedAttribute, False) == 0:
                ok = True
        except Exception:
            pass
    return ok
