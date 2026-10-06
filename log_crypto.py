"""polish.log 的落盘加密与解密（只用标准库，不新增任何依赖）。

为什么这么做
------------
日志里可能包含提示词、聊天上下文与模型返回原文，明文落盘不合适。本模块让
`polish.log` 里**只有密文**，同时提供同密钥的还原能力（见 `log_decrypt.py`
与 `main.py --decrypt-log`）。

构造（标准流加密 + 完整性校验，标准库即可实现）
--------------------------------------------
    PBKDF2-HMAC-SHA256(口令)  ->  enc_key(32B) + mac_key(32B)
    每条记录：随机 nonce(12B) -> HMAC-SHA256 计数器模式生成密钥流 -> 与明文异或
    校验    ：tag = HMAC-SHA256(mac_key, nonce || 密文) 取前 16 字节（encrypt-then-MAC）

文件格式（一行 = 一条完整日志记录，追加写，天然兼容日志尾部追加与滚动）
--------------------------------------------------------------
    WPENC1:<base64(nonce[12] || 密文 || tag[16])>

  记录内部的换行（如 `log.info("a\\nb")`）一并加密，解密后原样还原成多行文本。

安全边界（务必知悉）
------------------
解密口令硬编码在程序内（`DEFAULT_LOG_KEY`），它防的是「日志文件被别的程序、
别人直接打开看到明文」，**不是**防逆向：拿到 exe 的人理论上可以提取口令。
若需要真正对抗逆向，应改为让用户自行设置口令/密钥文件。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from pathlib import Path
from typing import Iterator, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# 密钥
# ---------------------------------------------------------------------------

#: 日志加密口令。要换密钥，只改这一处（解密端用 `-k/--key` 传同一个值即可）。
DEFAULT_LOG_KEY = "zhaomh666"

_SALT = b"wechat-ai-polish/polish.log/v1"
_ITERATIONS = 120_000
_NONCE_LEN = 12
_TAG_LEN = 16

#: 每条加密日志行都以它开头，用于识别格式与就地加密历史明文日志。
MARKER = "WPENC1:"

# 状态标记（`decrypt_lines` 逐行产出）
ST_ENC = "enc"        # 解密成功
ST_PLAIN = "plain"    # 本来就不是加密行（历史明文日志 / 人工追加的文本），原样透传
ST_BAD = "bad"        # 看起来是加密行但解不开（口令不对或内容被改）

_key_cache: dict = {}


def log_key() -> str:
    """当前使用的日志口令（单一出口，便于后续改成可配置）。"""
    return DEFAULT_LOG_KEY


def _derive(password: str) -> Tuple[bytes, bytes]:
    """由口令派生 (加密密钥, 校验密钥)；结果缓存，避免每行都跑一遍 PBKDF2。"""
    cached = _key_cache.get(password)
    if cached is None:
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _SALT, _ITERATIONS, dklen=64)
        cached = (dk[:32], dk[32:])
        _key_cache[password] = cached
    return cached


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-SHA256 计数器模式：nonce||counter -> 32 字节随机块，拼成所需长度的密钥流。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(enc_key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream))


# ---------------------------------------------------------------------------
# 单行（单条记录）加解密
# ---------------------------------------------------------------------------

def is_encrypted_line(line: str) -> bool:
    """该行是否为本模块产出的密文行。"""
    return line.lstrip().startswith(MARKER)


def encrypt_record(text: str, key: Optional[str] = None) -> str:
    """把一条日志记录加密成一行 ASCII 密文（含 MARKER 前缀）。"""
    enc_key, mac_key = _derive(key or log_key())
    data = text.encode("utf-8")
    nonce = os.urandom(_NONCE_LEN)
    cipher = _xor(data, _keystream(enc_key, nonce, len(data)))
    tag = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()[:_TAG_LEN]
    return MARKER + base64.b64encode(nonce + cipher + tag).decode("ascii")


def decrypt_record(line: str, key: Optional[str] = None) -> str:
    """解密一行密文，还原原始记录文本。

    不是本格式的行返回 ``None``（由调用方决定透传还是报错）；
    是密文但校验不过则抛 ``ValueError``（口令不对或内容被篡改）。
    """
    s = line.strip()
    if not s.startswith(MARKER):
        return None
    try:
        blob = base64.b64decode(s[len(MARKER):], validate=True)
    except Exception as exc:
        raise ValueError(f"密文不是合法 base64: {exc}") from exc
    if len(blob) < _NONCE_LEN + _TAG_LEN:
        raise ValueError("密文长度不足，文件可能被截断")
    nonce, cipher, tag = blob[:_NONCE_LEN], blob[_NONCE_LEN:-_TAG_LEN], blob[-_TAG_LEN:]
    enc_key, mac_key = _derive(key or log_key())
    expect = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()[:_TAG_LEN]
    if not hmac.compare_digest(tag, expect):
        raise ValueError("完整性校验失败：口令不对或日志被篡改")
    return _xor(cipher, _keystream(enc_key, nonce, len(cipher))).decode("utf-8")


# ---------------------------------------------------------------------------
# 文件级
# ---------------------------------------------------------------------------

def decrypt_lines(path: Union[str, Path],
                  key: Optional[str] = None) -> Iterator[Tuple[str, str]]:
    """逐行读取日志文件，产出 ``(状态, 文本)``。

    状态取值：``ST_ENC`` 解密成功 / ``ST_PLAIN`` 非加密行原样透传 / ``ST_BAD`` 解密失败
    （第二个元素为失败原因，便于排查是口令不对还是文件坏了）。
    """
    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.rstrip(b"\r\n").decode("utf-8", "replace")
            if not line.strip():
                # 空行不是加密记录（只可能来自升级前的明文日志里被折行的记录），原样透传
                yield (ST_PLAIN, "")
                continue
            if not is_encrypted_line(line):
                yield (ST_PLAIN, line)
                continue
            try:
                yield (ST_ENC, decrypt_record(line, key) or "")
            except ValueError as exc:
                yield (ST_BAD, str(exc))


def decrypt_file(src: Union[str, Path],
                 dst: Optional[Union[str, Path]] = None,
                 key: Optional[str] = None) -> dict:
    """把加密日志还原为明文文件（UTF-8）。

    ``dst`` 省略时输出到同目录的 ``<原名>.decrypted.log``。
    返回统计信息：``{"src","dst","enc","plain","bad","total"}``。
    """
    src = Path(src)
    if dst is None:
        dst = src.parent / (src.stem + ".decrypted" + src.suffix)
    dst = Path(dst)

    counts = {ST_ENC: 0, ST_PLAIN: 0, ST_BAD: 0}
    lines = []
    for status, text in decrypt_lines(src, key):
        counts[status] += 1
        lines.append(text)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        for text in lines:
            fh.write(text + "\n")

    return {
        "src": str(src),
        "dst": str(dst),
        "enc": counts[ST_ENC],
        "plain": counts[ST_PLAIN],
        "bad": counts[ST_BAD],
        "total": sum(counts.values()),
    }


def _looks_fully_encrypted(path: Path, tail_bytes: int = 8192) -> bool:
    """快速判断：文件首行与末段是否都已是密文行。

    日志只会在尾部追加，且每条记录都整行写入，所以「首行 + 尾部窗口」都是密文，
    就说明文件已经整体加密过，无需再全量扫描。
    """
    size = path.stat().st_size
    if size == 0:
        return True
    with open(path, "rb") as fh:
        head = fh.read(4096).decode("utf-8", "replace")
        for line in head.splitlines():
            if line.strip():
                if not is_encrypted_line(line):
                    return False
                break
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
        tail = fh.read().decode("utf-8", "replace")
    for line in reversed(tail.splitlines()):
        if line.strip():
            return is_encrypted_line(line)
    return True


def ensure_encrypted_file(path: Union[str, Path], key: Optional[str] = None) -> int:
    """把历史遗留的明文日志就地加密，保证磁盘上不留明文。

    程序启动、打开日志句柄**之前**调用；返回被加密的行数。
    实现为「写临时文件 + 原子替换」，因此不会出现半成品文件。
    任何异常都吞掉（返回 0）——日志加密失败绝不能导致程序起不来。
    """
    try:
        path = Path(path)
        if not path.exists() or path.stat().st_size == 0:
            return 0
        if _looks_fully_encrypted(path):
            return 0

        tmp = path.parent / (path.name + ".enc.tmp")
        changed = 0
        with open(path, "rb") as fin, open(tmp, "w", encoding="utf-8", newline="") as fout:
            for raw in fin:
                line = raw.rstrip(b"\r\n").decode("utf-8", "replace")
                if not line.strip() or is_encrypted_line(line):
                    fout.write(line + "\n")
                else:
                    fout.write(encrypt_record(line, key) + "\n")
                    changed += 1
        os.replace(tmp, path)          # 原子替换：要么全加密，要么保持原样
        return changed
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# logging Handler：日志写盘前逐条加密
# ---------------------------------------------------------------------------

class EncryptedFileHandler(logging.FileHandler):
    """与 ``logging.FileHandler`` 完全同构，只是每行落盘前先加密。

    一行 = 一条日志记录（记录内的换行一并加密），因此文件里不会出现明文，
    解密后仍能还原出「时间 级别 内容」的原始多行格式。
    """

    def __init__(self, filename, key: Optional[str] = None, mode: str = "a",
                 encoding: str = "utf-8", delay: bool = False, errors=None):
        super().__init__(filename, mode=mode, encoding=encoding, delay=delay, errors=errors)
        self.key = key or log_key()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = self.stream
            stream.write(encrypt_record(msg, self.key) + self.terminator)
            self.flush()
        except RecursionError:      # 与标准 Handler 一致：不吞掉递归错误
            raise
        except Exception:
            self.handleError(record)

    def reencrypt_pending(self, path: Optional[Union[str, Path]] = None) -> int:
        """临时让出文件句柄，把仍残留在磁盘上的明文行就地加密，再重新打开追加。

        用途：启动时刚终止了旧实例（旧实例可能在我们加密之后又追加了明文日志），
        此时自己正持有追加句柄，无法原子替换文件，必须先关再开。
        """
        target = path or self.baseFilename
        changed = 0
        try:
            self.close()                                  # 先放开句柄
            changed = ensure_encrypted_file(target, self.key)
        except Exception:
            changed = 0
        try:
            if self.stream is None:                       # 立刻重开，不依赖惰性重开
                self.stream = self._open()
        except Exception:
            pass
        return changed


def install_file_logging(path: Union[str, Path],
                         level: int = logging.INFO,
                         fmt: str = "%(asctime)s %(levelname)s %(message)s",
                         key: Optional[str] = None) -> EncryptedFileHandler:
    """挂上加密日志 Handler（等价于原来的 basicConfig(filename=...) 行为）。

    先把它加密历史明文日志，再以追加方式打开文件；根 logger 的级别与格式
    与改造前保持一致，调用方无需改动其它日志代码。
    """
    ensure_encrypted_file(path, key)
    handler = EncryptedFileHandler(path, key=key, encoding="utf-8")
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    return handler
