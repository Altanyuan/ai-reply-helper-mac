"""键盘模拟（pynput 封装）。

**修饰键是 Cmd 而不是 Ctrl**：全选 / 复制 / 粘贴一律用
`{Cmd}a` / `{Cmd}c` / `{Cmd}v`。

为什么用 pynput 而不是自己拼 CGEvent
------------------------------------
`CGEventKeyboardSetUnicodeString` 在 pyobjc 里传 Python str 需要手工准备 UniChar
缓冲区，容易出错；pynput 已经正确处理了 macOS 的 Unicode 输入（拼音输入法兼容、
代理对不拆），而且它本来就是本项目的依赖，不引入新东西。

注意：发送键盘事件需要「辅助功能」权限。
"""

from __future__ import annotations

import contextlib
import logging

log = logging.getLogger("polish")

try:
    from pynput.keyboard import Controller, Key
    _kb = Controller()
    _CMD = Key.cmd
    AVAILABLE = True
except Exception:                     # 无显示/无权限环境下降级
    _kb = None
    _CMD = None
    AVAILABLE = False


# ---------------------------------------------------------------------------
# 关键修复：把 pynput 的键盘布局查询「挪到主线程 + 缓存」
#
# 不这么做的话，打包成 .app 双击启动后 2~3 秒必然闪退。完整链条（实测 macOS 26）：
#
#     pynput.keyboard.Listener._run()        ← 跑在 pynput 自己的监听线程
#       → keycode_context()
#       → TISCopyCurrentKeyboardInputSource   （经 ctypes 调用）
#       → macOS 内部 TSMCurrentKeyboardInputSourceRefCreate
#       → _HaveOnlyOneKeyboardInputSource
#       → islGetInputSourceListWithAdditions  ← **要求主队列**
#       → dispatch_assert_queue 断言失败 → SIGTRAP，进程当场被杀
#
# 崩溃报告长这样：
#     EXC_BREAKPOINT / SIGTRAP
#     libdispatch:_dispatch_assert_queue_fail
#     HIToolbox:TSMCurrentKeyboardInputSourceRefCreate
#     Python:thread_run
#
# `keycode_context()` 返回的 `layout_data` 是**一份 bytes 副本**，可以安全地跨线程
# 复用；子线程本来也没有「必须重新查询」的理由。所以在主线程先取一次，
# 再把两处 `keycode_context` 换成返回缓存的版本。
#
# ⚠️ 必须换**两个**模块：`pynput.keyboard._darwin` 是 `from ... import keycode_context`
# 按值绑定的，只改 `pynput._util.darwin` 里的那一个不起作用。
# ---------------------------------------------------------------------------

_LAYOUT_PRIMED = False


def prime_keyboard_layout() -> bool:
    """在主线程预取键盘布局并打上缓存补丁；幂等，可重复调用。

    返回是否成功打上补丁（失败时不改变原行为，只是仍有可能闪退）。
    """
    global _LAYOUT_PRIMED
    if _LAYOUT_PRIMED:
        return True
    try:
        from pynput._util import darwin as _d
        import pynput.keyboard._darwin as _kd
    except Exception as e:            # 非 macOS 或缺依赖：不影响其它功能
        log.warning(f"键盘布局预取跳过（{type(e).__name__}: {e}）")
        return False
    try:
        with _d.keycode_context() as ctx:      # 主线程调用：安全
            cached = ctx
    except Exception as e:
        log.warning(f"键盘布局预取失败（仍可运行，但热键可能闪退）: {e}")
        return False
    if not cached or cached[1] is None:
        # 取不到布局数据：不打卡片，保持原行为，避免把后续调用弄坏
        log.warning("没取到键盘布局数据，跳过补丁")
        return False

    @contextlib.contextmanager
    def _cached_context():
        yield cached

    _d.keycode_context = _cached_context
    _kd.keycode_context = _cached_context
    # 校验补丁真的生效了：将来 pynput 改名时这里会立刻暴露，而不是静默闪退
    ok = (_d.keycode_context is _cached_context
          and getattr(_kd, "keycode_context", None) is _cached_context)
    _LAYOUT_PRIMED = ok
    if ok:
        log.info("已在主线程预取键盘布局并缓存（规避 pynput 子线程调 TSM 导致的 SIGTRAP）")
    else:
        log.warning("键盘布局缓存补丁未生效，pynput 版本可能有变化")
    return ok


def _tap(ch: str) -> None:
    if not AVAILABLE:
        return
    _kb.press(ch)
    _kb.release(ch)


def _with_cmd(ch: str) -> None:
    if not AVAILABLE:
        return
    with _kb.pressed(_CMD):
        _tap(ch)


def select_all() -> None:
    """Cmd+A"""
    _with_cmd("a")


def copy() -> None:
    """Cmd+C"""
    _with_cmd("c")


def paste() -> None:
    """Cmd+V"""
    _with_cmd("v")


def type_text(text: str) -> None:
    """逐字输入文本（走键盘事件，不碰剪贴板）。绝不发送回车/换行。

    换行与制表符替换成空格：聊天输入框里回车会**直接发送消息**，太危险。
    """
    if not AVAILABLE or not text:
        return
    safe = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    try:
        _kb.type(safe)
    except Exception as e:
        log.warning(f"模拟键盘输入失败: {e}")


def layout_cache_ready() -> bool:
    """键盘布局缓存是否已就绪（供启动日志显示）。"""
    return _LAYOUT_PRIMED


# import 时就在主线程把补丁打好：这样即使某个调用方忘了显式调用，监听线程也不会
# 踩到 TSM 那个主线程断言。失败时只记日志、不抛异常，不影响其它功能。
# （此刻日志处理器可能还没装好，所以 main.py 里会再报一次状态。）
prime_keyboard_layout()
