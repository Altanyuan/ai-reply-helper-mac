"""polish.log 解密工具：用相同口令把加密日志还原成明文。

日志由 `log_crypto.py` 加密后落盘（每行 `WPENC1:<base64>`），本工具负责还原。

命令行用法
----------
    python3 log_decrypt.py                     # 自动定位 polish.log，输出 polish.decrypted.log
    python3 log_decrypt.py 日志路径             # 指定要解密的日志文件
    python3 log_decrypt.py -o 输出路径           # 指定还原后的文件名
    python3 log_decrypt.py --stdout            # 不落盘，直接打印到控制台
    python3 log_decrypt.py -k zhaomh666        # 指定口令（默认就是 zhaomh666）

作为接口调用
------------
    import log_decrypt
    info = log_decrypt.decrypt_log_file()       # -> {"src","dst","enc","plain","bad","total"}

说明：本文件只依赖标准库与 `paths` / `log_crypto`，不 import 主程序的任何重依赖，
因此可以在没有任何第三方依赖、甚至没装 PyObjC 的环境里单独运行。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Union

import log_crypto
import paths


def default_log_file() -> Path:
    """默认日志路径：与主程序 `paths.log_path()` 完全一致（打包后即安装目录下的 polish.log）。"""
    return Path(paths.log_path())


def decrypt_log_file(src: Optional[Union[str, Path]] = None,
                     dst: Optional[Union[str, Path]] = None,
                     key: Optional[str] = None) -> dict:
    """把加密日志还原为明文文件，返回统计信息（供程序内部 / 第三方直接调用）。

    ``src`` 省略时用默认日志路径；``dst`` 省略时输出到同目录的
    ``<原名>.decrypted.log``（不会覆盖原日志）。
    """
    src = Path(src) if src else default_log_file()
    if not src.exists():
        raise FileNotFoundError(f"找不到日志文件: {src}")
    return log_crypto.decrypt_file(src, dst, key)


def decrypt_log_to_text(src: Optional[Union[str, Path]] = None,
                        key: Optional[str] = None) -> str:
    """把加密日志还原成一个字符串（不落盘）。解密失败的行会以提示行形式保留。"""
    src = Path(src) if src else default_log_file()
    if not src.exists():
        raise FileNotFoundError(f"找不到日志文件: {src}")
    out = []
    for status, text in log_crypto.decrypt_lines(src, key):
        if status == log_crypto.ST_BAD:
            out.append(f"[解密失败] {text}")
        else:
            out.append(text)
    return "\n".join(out) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="log_decrypt",
        description="解密 polish.log（WPENC1 格式）并还原为明文",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python log_decrypt.py\n"
               "  python log_decrypt.py -o D:\\plain.log\n"
               "  python log_decrypt.py --stdout\n",
    )
    p.add_argument("logfile", nargs="?", default=None,
                   help="要解密的日志文件（默认自动定位 polish.log）")
    p.add_argument("-o", "--out", default=None,
                   help="还原后的输出文件（默认 <日志文件>.decrypted.log）")
    p.add_argument("-k", "--key", default=None,
                   help=f"解密口令（默认 {log_crypto.DEFAULT_LOG_KEY}）")
    p.add_argument("--stdout", action="store_true",
                   help="不写文件，直接把还原内容打印到控制台")
    p.add_argument("--show-key", action="store_true",
                   help="仅打印当前默认口令，便于确认")
    return p


def main(argv=None) -> int:
    try:                                    # 统一按 UTF-8 输出，避免重定向到文件时乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = _build_parser().parse_args(argv)

    if args.show_key:
        print(log_crypto.DEFAULT_LOG_KEY)
        return 0

    src = Path(args.logfile) if args.logfile else default_log_file()
    if not src.exists():
        print(f"[错误] 找不到日志文件：{src}")
        print("       请确认程序已运行过，或用参数指定日志路径。")
        return 2

    if args.stdout:
        print(decrypt_log_to_text(src, args.key), end="")
        return 0

    try:
        info = decrypt_log_file(src, args.out, args.key)
    except Exception as exc:
        print(f"[错误] 解密失败：{exc}")
        return 1

    print(f"日志文件：{info['src']}")
    print(f"还原输出：{info['dst']}")
    print(f"记录条数：{info['total']}（解密成功 {info['enc']}，"
          f"原本明文 {info['plain']}，解密失败 {info['bad']}）")
    if info["plain"]:
        print("提示：存在非加密行，已按原样输出（多为程序升级前的历史明文日志）。")
    if info["bad"]:
        print("提示：有记录解密失败，通常是口令不一致或文件被修改过。")
    return 0 if info["bad"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
