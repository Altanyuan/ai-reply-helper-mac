"""macOS 悬浮图标 + 主线程 UI 宿主。

职责拆分（2026-09 重构）
------------------------
* **悬浮图标** → `macos/icon.py` 的原生 `NSPanel`（背景可以真正全透明）。
  之前用 tkinter 做，试遍了 `-transparent` / `systemTransparent` / 各种重绘，
  在 macOS 26 + Tk 8.6 上要么方块底、要么整个图标消失，**做不到透明**。
* **弹窗宿主** → 仍然是 tkinter，但**窗口隐藏**（`withdraw()`），只提供
  Tk 解释器与 mainloop：`ui.py` 的选择窗/错误窗需要它当 master。
  所以本类既是「图标」也是 `run_ui()` 的宿主。

线程模型（未变）
----------------
* **PIL 解码**（耗 CPU、不碰 Tk/AppKit）→ 后台线程；
* **PIL → NSImage**（AppKit 要求主线程）→ 回主线程分批转换；
* 所有 AppKit 调用都发生在主线程的 Tk timer 回调里。

坐标约定
--------
对外一律用**左上角原点**（与 config.json 的 tray_pos、与截图坐标一致）；
与 AppKit 左下角原点的换算收在 `icon.py` 里。
"""

from __future__ import annotations

import logging
import os
import queue
import threading

try:
    import tkinter as tk
    TK_AVAILABLE = True
except Exception:              # 某些 Python 发行版不带 tkinter
    tk = None
    TK_AVAILABLE = False

from macos import icon

log = logging.getLogger("polish")

POLL_MS = 60                 # 指令轮询间隔
SAMPLING = 3                 # 抽帧：每 N 帧保留 1 帧（241 帧 → 81 帧）
NSIMAGE_BATCH = 12           # 每次回主线程转换多少帧 NSImage

#: Tk 是否达到 8.6。低于 8.6 时**弹窗**（ui.py）会画不出来（内容空白），
#: 而 Apple 自带的 3.9 就是 Tk 8.5，所以启动时要明确提示换 Python。
TK_OK = bool(tk is not None and getattr(tk, "TkVersion", 0) >= 8.6)
#: 兼容旧名
SUPPORTS_ALPHA = TK_OK


def tk_warning() -> str:
    """Tk 版本过低时的告警文案（只影响弹窗，不影响悬浮图标）；正常时返回空串。"""
    if tk is None:
        return ("· 当前 Python 没有 tkinter，「优化后语句」弹窗无法显示。\n"
                "    请用 python.org 的 Python 3.12+ 重跑 run-mac.command。")
    if TK_OK:
        return ""
    import platform as _p
    return (
        f"· 你的 Tk 版本是 {getattr(tk, 'TkVersion', '?')}"
        f"（Apple 自带、已废弃；macOS {_p.mac_ver()[0]}），"
        "它在新版 macOS 上**无法渲染控件内容**：\n"
        "    「优化后语句」弹窗与设置页会是空白（悬浮图标已改用原生实现，不受影响）。\n"
        "    **解决办法**：装一个自带 Tk 8.6 的 Python，删掉 .venv 后重跑 run-mac.command\n"
        "      · 推荐 python.org 下载 Python 3.12+（安装包自带 Tk 8.6.13）\n"
        "      · 或 Homebrew：brew install python@3.12"
    )


def _load_pil_frames(path, size, flatten_bg=None):
    """纯 PIL 解码动图 → (frames[PIL.Image], durations[ms])。

    **不碰 Tk / AppKit**，所以可以安全地跑在后台线程里。

    flatten_bg 现在基本用不到（原生图标支持 alpha），保留参数是为了兼容
    「需要把帧合成到纯色底」的极端场景。
    """
    from PIL import Image

    def one(im_):
        fr = im_.convert("RGBA").resize((size, size), Image.LANCZOS)
        if flatten_bg is None:
            return fr
        base = Image.new("RGB", (size, size), flatten_bg)
        base.paste(fr, (0, 0), fr)
        return base

    frames, durs = [], []
    try:
        im = Image.open(path)
    except Exception as e:
        log.warning(f"打开动图失败 {path}: {e}")
        return [], []
    try:
        n = getattr(im, "n_frames", 1)
        for i in range(0, n, SAMPLING):
            im.seek(i)
            frames.append(one(im))
            durs.append(int(im.info.get("duration", 40) or 40) * SAMPLING)
    except Exception as e:
        log.warning(f"解码动图失败 {path}: {e}")
    if not frames:                      # 静态图兜底：至少给一帧
        try:
            im.seek(0)
            frames = [one(im)]
            durs = [100]
        except Exception:
            pass
    return frames, durs


class TrayWidget:
    """悬浮图标 + 主线程 UI 宿主。公开方法可从任意线程调用（只入队）。"""

    def __init__(self, idle_path, active_path, size=220, pos=None, on_move=None):
        self.idle_path = idle_path
        self.active_path = active_path
        self.size = int(size)
        self._cmd = queue.Queue()
        self._ready = {}            # name -> (nsimage_frames, durs)，仅主线程读写
        self._loading = set()       # 已在后台解码中的 name
        self._frames, self._durs = [], []
        self._idx = 0
        self._want = "idle"
        self._anim_gen = 0
        self._root = None
        self._icon = None
        self._command = None
        self._settings_command = None
        self._pos = pos
        self._on_move = on_move

    # ---------- 主线程：建 UI 宿主 + 图标，然后跑 mainloop ----------
    def run(self):
        try:
            root = tk.Tk()
        except Exception as e:
            print(f"[tray] 无法创建 Tk 宿主，跳过界面: {e}")
            return
        self._root = root
        # Tk 窗口本身**不显示**：只当 Tk 解释器与 mainloop 的宿主，
        # 悬浮图标交给原生 NSPanel，弹窗（ui.py）作为 Toplevel 挂在它下面。
        root.withdraw()

        self._icon = icon.FloatingIcon(
            size=self.size, pos=self._pos,
            on_move=self._on_move,
            on_click=self._trigger,
            on_settings=self._open_settings,
            on_quit=self.stop,
        )
        if self._icon.show():
            self._set_state("idle")
            self._start_loading("active")     # 预解码 active，首次按热键不用等
        else:
            log.warning("原生悬浮图标不可用（缺 pyobjc？）——热键仍可正常使用")

        root.after(POLL_MS, self._poll)
        root.mainloop()

    def _trigger(self):
        if self._command:
            try:
                self._command()
            except Exception as e:
                log.warning(f"触发润色失败: {e}")

    def _open_settings(self):
        if self._settings_command:
            try:
                self._settings_command()
            except Exception as e:
                log.warning(f"打开设置页失败: {e}")

    # ---------- 动图加载（后台解码 + 主线程转 NSImage）----------
    def _name_of(self, which):
        return self.idle_path if which == "idle" else self.active_path

    def _start_loading(self, which):
        name = self._name_of(which)
        if name in self._loading or name in self._ready:
            return
        self._loading.add(name)

        def worker():
            frames, durs = _load_pil_frames(name, self.size)
            self._cmd.put(("frames", (name, frames, durs)))

        threading.Thread(target=worker, daemon=True,
                         name=f"tray-load-{which}").start()

    def _install_frames(self, name, pil_frames, durs):
        """主线程：PIL 帧 → NSImage（分批，避免一次性卡住界面）。"""
        if not pil_frames or not icon.AVAILABLE:
            self._loading.discard(name)
            return
        images, idx = [], 0

        def step():
            nonlocal idx
            for _ in range(NSIMAGE_BATCH):
                if idx >= len(pil_frames):
                    break
                try:
                    images.append(icon.pil_to_nsimage(pil_frames[idx]))
                except Exception as e:
                    log.warning(f"帧转换失败（跳过该帧）: {e}")
                    images.append(None)
                idx += 1
            if idx < len(pil_frames):
                self._root.after(1, step)
                return
            self._ready[name] = (images, durs)
            self._loading.discard(name)
            if self._name_of(self._want) == name:
                self._apply(self._want)

        step()

    def _apply(self, which):
        name = self._name_of(which)
        entry = self._ready.get(name)
        if not entry:
            self._start_loading(which)
            return
        self._frames, self._durs = entry
        self._idx = 0
        self._anim_gen += 1
        if self._frames and self._icon is not None:
            self._icon.set_image(self._frames[0])
        self._animate(self._anim_gen)

    def _set_state(self, which):
        """切换 idle / active。帧没就绪就先记下来，等解码完成自动应用。"""
        self._want = which
        self._apply(which)

    def _animate(self, gen):
        # 代际号作废旧动画链：否则连续切状态会有多条 after 链同时改图
        if gen != self._anim_gen:
            return
        if not self._frames or self._root is None:
            return
        self._idx = (self._idx + 1) % len(self._frames)
        img = self._frames[self._idx]
        if img is not None and self._icon is not None:
            self._icon.set_image(img)
        delay = self._durs[self._idx] if self._idx < len(self._durs) else 40
        self._root.after(max(20, delay), lambda: self._animate(gen))

    # ---------- 主线程轮询：处理其它线程的请求 ----------
    def _poll(self):
        root = self._root
        try:
            while True:
                cmd, payload = self._cmd.get_nowait()
                if cmd == "idle":
                    self._set_state("idle")
                elif cmd == "active":
                    self._set_state("active")
                elif cmd == "frames":
                    self._install_frames(*payload)
                elif cmd == "ui":
                    try:
                        payload()
                    except Exception as e:
                        log.warning(f"主线程执行 UI 回调失败: {e}")
                elif cmd == "quit":
                    if self._icon is not None:
                        self._icon.close()
                    try:
                        root.destroy()
                    except Exception:
                        pass
                    return
        except queue.Empty:
            pass
        root.after(POLL_MS, self._poll)

    # ---------- 对外接口：可从任意线程调用（仅入队）----------
    def show_idle(self):
        self._cmd.put(("idle", None))

    def show_active(self):
        self._cmd.put(("active", None))

    def set_command(self, fn):
        """点击悬浮图标时的回调（与按热键等价）。"""
        self._command = fn

    def set_settings_command(self, fn):
        """右键菜单「打开设置…」的回调。"""
        self._settings_command = fn

    def run_ui(self, fn):
        """在主线程（Tk 解释器）里安全执行 UI 构建函数。"""
        self._cmd.put(("ui", fn))

    def get_root(self):
        """返回隐藏的 Tk 根，供弹窗作为父窗口（Toplevel）使用。"""
        return self._root

    def stop(self):
        self._cmd.put(("quit", None))


def make_tray(idle_path, active_path, size=220, pos=None, on_move=None):
    """工厂：动图缺失或初始化失败时返回 None（不影响主流程）。"""
    if not TK_AVAILABLE:
        print("[tray] 当前 Python 没有 tkinter，跳过界面（热键仍可用）。")
        return None

    warn = tk_warning()
    if warn:
        print("[tray] ⚠️  弹窗可能显示为空白，原因：")
        print("    " + warn)
        log.warning("Tk 版本过低（%s），弹窗可能无法正常渲染",
                    getattr(tk, "TkVersion", "?"))
    if not icon.AVAILABLE:
        print("[tray] ⚠️  pyobjc 不可用，悬浮图标将不显示（弹窗与热键正常）。")

    if not (idle_path and os.path.exists(idle_path) and os.path.exists(active_path)):
        print(f"[tray] 动图缺失（{idle_path} / {active_path}），跳过悬浮图标。")
        return None
    try:
        return TrayWidget(idle_path, active_path, size=size, pos=pos, on_move=on_move)
    except Exception as e:
        log.warning(f"创建悬浮图标失败（跳过）: {e}")
        return None
