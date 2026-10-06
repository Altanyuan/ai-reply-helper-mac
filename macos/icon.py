"""macOS 原生悬浮图标：PyObjC `NSPanel` + 自绘 NSView。

为什么不用 tkinter 做这个窗口
------------------------------
tkinter 做不出「背景透明」的悬浮窗（macOS 26 + Tk 8.6 实测）：

  · 不透明窗口 + 深色底 → 能显示，但图标被一个方块托着（用户不要这个）
  · `-transparent` + `systemTransparent` 底色 → **连内容一起不绘制**，
    整个图标消失（不管 Label 用不用 alpha 帧、有没有强制重绘）
  · alpha 通道本身是好的（Label 底设成纯红时，机器人的透明处正确透出红色），
    所以问题出在 Tk 的窗口透明实现上

而 AppKit 做这件事是本职：`NSPanel` + `clearColor` 背景 + 带 alpha 的 NSImage，
背景天然全透明。参考项目（jev-chat-jarvis-mac）也是这条路。

与 Tk 共存
----------
弹窗（ui.py）仍然用 tkinter，所以主线程跑的还是 Tk 的 mainloop —— 在 macOS 上
Tk 的 mainloop 本身就在驱动 Cocoa 事件循环，因此 NSPanel 可以并存（实测通过）。
本模块的所有方法都必须在**主线程**调用（AppKit 要求）。
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger("polish")

try:
    import objc
    import AppKit
    import Foundation
    AVAILABLE = True
except Exception as _e:            # 没装 pyobjc 时整体降级
    objc = AppKit = Foundation = None
    AVAILABLE = False
    log.info(f"AppKit 不可用（{_e}），悬浮图标将跳过")

#: 位移超过该点数才算「拖动」，否则算「点击」
DRAG_THRESHOLD = 4
#: 默认停靠时距屏幕边缘的留白
EDGE_MARGIN = 24


def pil_to_nsimage(pil_image):
    """PIL 图 → NSImage（保留 alpha）。"""
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    data = buf.getvalue()
    nsdata = Foundation.NSData.dataWithBytes_length_(data, len(data))
    return AppKit.NSImage.alloc().initWithData_(nsdata)


if AVAILABLE:

    class _IconView(AppKit.NSView):
        """只负责画一张图，并把鼠标事件转给 owner。

        用自绘 View 而不是 NSImageView：拖动/点击/右键都要自己处理，
        自绘更直观，也省掉一层控件。
        """

        def initWithOwner_(self, owner):
            self = objc.super(_IconView, self).initWithFrame_(
                Foundation.NSMakeRect(0, 0, 1, 1))
            if self is None:
                return None
            self._owner = owner
            self._image = None
            return self

        # 透明窗口里的 View 也必须声明非不透明，否则底色会是黑的
        def isOpaque(self):
            return False

        def setIconImage_(self, image):
            self._image = image
            self.setNeedsDisplay_(True)

        def drawRect_(self, rect):
            if self._image is not None:
                self._image.drawInRect_(self.bounds())

        # ---- 鼠标 ----
        def mouseDown_(self, event):
            self._owner.mouse_down(event)

        def mouseDragged_(self, event):
            self._owner.mouse_drag(event)

        def mouseUp_(self, event):
            self._owner.mouse_up(event)

        def rightMouseDown_(self, event):
            self._owner.right_down(event)


    class _MenuTarget(AppKit.NSObject):
        """右键菜单的动作接收者。"""

        def initWithCallbacks_(self, callbacks):
            self = objc.super(_MenuTarget, self).init()
            if self is None:
                return None
            self._cb = callbacks
            return self

        def doTrigger_(self, sender):
            self._cb["trigger"]()

        def doSettings_(self, sender):
            self._cb["settings"]()

        def doQuit_(self, sender):
            self._cb["quit"]()


class FloatingIcon:
    """原生透明悬浮图标。

    坐标约定：对外一律用**左上角原点**的屏幕坐标（与 config.json 里 tray_pos
    的历史格式一致，也与 tkinter/截图坐标一致）；内部与 AppKit 的**左下角原点**
    互转，避免踩坐标系反了的坑。
    """

    def __init__(self, size=220, pos=None, on_move=None, on_click=None,
                 on_settings=None, on_quit=None):
        self.size = int(size)
        self._pos = pos
        self._on_move = on_move
        self._on_click = on_click
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._panel = None
        self._view = None
        self._menu = None
        self._menu_target = None
        self._drag = None

    # ---------- 屏幕坐标换算 ----------
    @staticmethod
    def _screen_height():
        return AppKit.NSScreen.mainScreen().frame().size.height

    def _visible_rect_top_left(self):
        """主屏可视区（已排除菜单栏与 Dock），返回 (left, top, w, h) 左上角坐标。"""
        screen = AppKit.NSScreen.mainScreen()
        vf = screen.visibleFrame()
        sh = screen.frame().size.height
        left = vf.origin.x
        top = sh - (vf.origin.y + vf.size.height)
        return left, top, vf.size.width, vf.size.height

    def _clamp_top_left(self, x, y):
        left, top, w, h = self._visible_rect_top_left()
        max_x, max_y = left + w - self.size, top + h - self.size
        x = left if max_x < left else max(left, min(int(x), max_x))
        y = top if max_y < top else max(top, min(int(y), max_y))
        return int(x), int(y)

    def current_top_left(self):
        """当前窗口位置的左上角坐标。"""
        f = self._panel.frame()
        return int(f.origin.x), int(self._screen_height() - (f.origin.y + f.size.height))

    def move_to_top_left(self, x, y):
        x, y = self._clamp_top_left(x, y)
        y_bl = self._screen_height() - y - self.size
        self._panel.setFrameOrigin_(Foundation.NSMakePoint(x, y_bl))

    # ---------- 生命周期 ----------
    def show(self):
        """创建并显示面板。必须在主线程调用。"""
        if not AVAILABLE:
            return False
        if self._panel is not None:
            return True

        sh = self._screen_height()
        left, top, w, h = self._visible_rect_top_left()
        # 默认停靠：可视区右下角（menu bar / Dock 已被 visibleFrame 排除）
        x, y = left + w - self.size - EDGE_MARGIN, top + h - self.size - EDGE_MARGIN
        if self._pos:
            try:
                x, y = int(self._pos[0]), int(self._pos[1])
            except Exception:
                log.info(f"tray_pos 无效（{self._pos!r}），复位到右下角")
            x, y = self._clamp_top_left(x, y)

        rect = Foundation.NSMakeRect(x, sh - y - self.size, self.size, self.size)
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        if panel is None:
            log.warning("NSPanel 创建失败")
            return False

        # 透明背景的关键三件套
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setLevel_(AppKit.NSFloatingWindowLevel)     # 盖在普通窗口之上
        panel.setIgnoresMouseEvents_(False)
        try:
            panel.setBecomesKeyOnlyIfNeeded_(True)        # 不抢键盘焦点
        except Exception:
            pass
        try:
            # 在所有 Space 都显示，且不随切换 Space 移动
            panel.setCollectionBehavior_(
                AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                | AppKit.NSWindowCollectionBehaviorStationary)
        except Exception:
            pass

        view = _IconView.alloc().initWithOwner_(self)
        panel.setContentView_(view)
        panel.orderFrontRegardless()                      # 显示但不激活本应用

        self._panel, self._view = panel, view
        self._build_menu()
        log.info(f"悬浮图标（原生 NSPanel）已显示：位置={self.current_top_left()} "
                 f"尺寸={self.size} 不透明={panel.isOpaque()}")
        return True

    def _build_menu(self):
        self._menu_target = _MenuTarget.alloc().initWithCallbacks_({
            "trigger": lambda: self._on_click and self._on_click(),
            "settings": lambda: self._on_settings and self._on_settings(),
            "quit": lambda: self._on_quit and self._on_quit(),
        })
        menu = AppKit.NSMenu.alloc().init()
        for title, sel in (("立即润色", "doTrigger:"),
                           ("打开设置…", "doSettings:"),
                           ("退出", "doQuit:")):
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, sel, "")
            item.setTarget_(self._menu_target)
            menu.addItem_(item)
        self._menu = menu

    def set_image(self, nsimage):
        """换一帧（主线程）。"""
        if self._view is not None:
            self._view.setIconImage_(nsimage)

    def close(self):
        if self._panel is not None:
            try:
                self._panel.orderOut_(None)
                self._panel.close()
            except Exception:
                pass
        self._panel = self._view = None

    # ---------- 鼠标事件（由 _IconView 回调）----------
    def mouse_down(self, event):
        f = self._panel.frame()
        p = self._panel.convertPointToScreen_(event.locationInWindow())
        self._drag = {"sx": p.x, "sy": p.y,
                      "ox": f.origin.x, "oy": f.origin.y, "moved": False}

    def mouse_drag(self, event):
        if not self._drag:
            return
        p = self._panel.convertPointToScreen_(event.locationInWindow())
        dx, dy = p.x - self._drag["sx"], p.y - self._drag["sy"]
        if not self._drag["moved"] and (abs(dx) > DRAG_THRESHOLD or abs(dy) > DRAG_THRESHOLD):
            self._drag["moved"] = True
        if self._drag["moved"]:
            f = self._panel.frame()
            x, y_bl = self._drag["ox"] + dx, self._drag["oy"] + dy
            # 用左上角坐标夹进可视区，再换回左下角
            x_tl = x
            y_tl = self._screen_height() - (y_bl + f.size.height)
            x_tl, y_tl = self._clamp_top_left(x_tl, y_tl)
            self._panel.setFrameOrigin_(
                Foundation.NSMakePoint(x_tl, self._screen_height() - y_tl - self.size))

    def mouse_up(self, event):
        moved = bool(self._drag and self._drag["moved"])
        self._drag = None
        if moved:
            x, y = self.current_top_left()
            self._pos = (x, y)
            if self._on_move:
                try:
                    self._on_move(x, y)
                except Exception as e:
                    log.warning(f"保存悬浮图标位置失败（下次启动会复位）: {e}")
        elif self._on_click:
            try:
                self._on_click()
            except Exception as e:
                log.warning(f"点击悬浮图标触发的回调失败: {e}")

    def right_down(self, event):
        if self._menu is not None:
            AppKit.NSMenu.popUpContextMenu_withEvent_forView_(
                self._menu, event, self._view)
