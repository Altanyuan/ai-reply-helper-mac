"""单实例管理。

接管方式尽量温和：先给旧实例发 SIGTERM，让它有机会收尾（关闭窗口、释放全局
热键），2 秒内没退再 SIGKILL。**不直接 kill -9** —— 旧实例来不及清理会留下
锁文件与半开的窗口。

为什么要接管旧实例，而不是「检测到已有一个就自己退出」：
用户无论从哪个副本启动、旧进程是否卡死，重新打开都应该得到
「唯一一个、最新版」的实例，而不是「按了没反应」。
"""

from __future__ import annotations

import atexit
import fcntl
import logging
import os
import signal
import time

import paths

log = logging.getLogger("polish")

_LOCK_PATH = paths.lock_path()
_lock_fh = None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_locked_pid():
    try:
        if _LOCK_PATH.exists():
            return int(_LOCK_PATH.read_text().strip())
    except Exception:
        pass
    return None


def ensure_single_instance() -> None:
    """保证只有一个实例：检测到旧实例就温和终止它，然后自己持锁。"""
    global _lock_fh

    _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 先尝试拿到独占锁；拿不到说明有活着的实例
    try:
        fh = open(_LOCK_PATH, "a+")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        _lock_fh = fh
    except OSError:
        old_pid = _read_locked_pid()
        if old_pid and old_pid != os.getpid() and _pid_alive(old_pid):
            log.warning(f"检测到旧实例(PID={old_pid})，正在终止以启动新实例…")
            print(f"[AI润色助手] 发现旧实例(PID={old_pid})，已自动终止，启动新实例。")
            try:
                os.kill(old_pid, signal.SIGTERM)
                for _ in range(10):
                    if not _pid_alive(old_pid):
                        break
                    time.sleep(0.2)
                if _pid_alive(old_pid):
                    os.kill(old_pid, signal.SIGKILL)
                    time.sleep(0.2)
            except Exception as e:
                log.warning(f"终止旧实例失败: {e}")
        # 杀掉之后重新持锁
        try:
            fh = open(_LOCK_PATH, "a+")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fh.seek(0)
            fh.truncate()
            fh.write(str(os.getpid()))
            fh.flush()
            _lock_fh = fh
        except OSError as e:
            log.warning(f"仍无法取得单实例锁（{e}），本次不阻断启动")

    atexit.register(_release_lock)


def _release_lock() -> None:
    """退出时放开锁并删掉锁文件（不要删别人的）。"""
    global _lock_fh
    try:
        if _lock_fh is not None:
            fcntl.flock(_lock_fh, fcntl.LOCK_UN)
            _lock_fh.close()
            _lock_fh = None
        if _LOCK_PATH.exists() and _read_locked_pid() == os.getpid():
            _LOCK_PATH.unlink()
    except Exception:
        pass
