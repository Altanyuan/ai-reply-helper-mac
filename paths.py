"""统一路径管理：区分「只读资源」与「可写数据」。

为什么需要这个模块：
  1. 打包成单文件后，程序会先解压到临时目录（`sys._MEIPASS`）再运行，
     退出即删除。若把 config.json 写在 `__file__` 旁边，配置**每次都会丢失**。
  2. 程序所在目录不一定可写（从 .app 包内运行、放在只读卷上等），
     日志 / 锁文件写不进去会让启动直接崩溃。

因此：
  - 只读资源（1.webp / 2.webp 等）：跟随程序包，运行时从 `sys._MEIPASS` 读。
  - 可写数据（config.json / 单实例锁）：放到 `~/Library/Application Support/话术润色助手`。
  - 日志：放到 `~/Library/Logs/话术润色助手`（macOS 约定的日志位置）。

源码运行时（未打包）为保持向后兼容，可写数据与日志仍使用项目目录，
这样现有的 config.json 原地生效，升级后配置不会丢。

文件位置的速查（打包成 .app 后）：

    配置  ~/Library/Application Support/话术润色助手/config.json
    日志  ~/Library/Logs/话术润色助手/polish.log
"""
import os
import sys
from pathlib import Path

APP_NAME = "话术润色助手"


def is_frozen() -> bool:
    """是否已被打包（区别于源码运行）。"""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> Path:
    """只读资源目录：打包后为 sys._MEIPASS，源码运行时为脚本所在目录。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """可写数据目录（自动创建）。

    打包后：~/Library/Application Support/话术润色助手
            （避开程序目录只读与临时目录丢失两个坑）
    源码运行：项目目录，沿用既有的 config.json，行为不变。
    """
    if is_frozen():
        target = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        target = Path(__file__).resolve().parent
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return target


def config_path() -> Path:
    """配置文件路径。"""
    return data_dir() / "config.json"


def log_dir() -> Path:
    """日志目录（自动创建）。

    打包成 .app 后：`~/Library/Logs/话术润色助手`
      —— 不写进 App 包内部：那会污染包体、且重装或更新时随包一起被换掉；
         放到 macOS 约定的用户日志目录，Finder「前往文件夹」也直达。
    源码运行：项目目录（沿用既有行为，日志就在身边）。
    """
    if is_frozen():
        target = Path.home() / "Library" / "Logs" / APP_NAME
    else:
        target = Path(__file__).resolve().parent
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return target


def log_path() -> Path:
    """日志文件路径。"""
    return log_dir() / "polish.log"


def lock_path() -> Path:
    """单实例锁文件路径。"""
    return data_dir() / ".wechat_polish.lock"


def asset(name: str) -> Path:
    """定位资源文件（如 1.webp / 2.webp）。

    优先使用「程序同目录」的外部同名文件（方便用户替换动图皮肤），
    找不到再回落到打包进程序包的内部资源。
    """
    try:
        if is_frozen():
            external = Path(sys.executable).resolve().parent / name
        else:
            external = Path(__file__).resolve().parent / name
        if external.exists():
            return external
    except Exception:
        pass
    return resource_dir() / name
