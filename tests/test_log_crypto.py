"""日志加密/解密的单元测试（纯本地，不联网）。

覆盖：加解密往返、磁盘上无明文、历史明文日志就地加密、错误口令/篡改能识别、
解密工具把非加密行原样透传。
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import log_crypto
import log_decrypt


def _records(path):
    """读回日志文件里所有解密后的记录文本。"""
    out = []
    for status, text in log_crypto.decrypt_lines(path):
        assert status == log_crypto.ST_ENC, (status, text)
        out.append(text)
    return out


# ---------------------------------------------------------------------------
# 单条记录
# ---------------------------------------------------------------------------

def test_default_key_is_expected():
    # 密钥写死在一处；改动会导致已有日志解不开，所以这里做个哨兵
    assert log_crypto.DEFAULT_LOG_KEY == "zhaomh666"


def test_roundtrip_keeps_text():
    text = "2026-09-26 10:00:00 INFO 中文日志 with ASCII & 换行\n第二行\t制表符"
    line = log_crypto.encrypt_record(text)
    assert line.startswith(log_crypto.MARKER)
    assert log_crypto.decrypt_record(line) == text


def test_ciphertext_hides_plaintext():
    line = log_crypto.encrypt_record("用户草稿：请把密钥发我")
    assert "用户草稿" not in line
    assert "密钥" not in line
    assert line.isascii()          # 全 ASCII，落盘不涉及编码问题


def test_same_text_encrypts_differently():
    a = log_crypto.encrypt_record("重复内容")
    b = log_crypto.encrypt_record("重复内容")
    assert a != b                  # 随机 nonce，避免可比较的密文


def test_wrong_key_rejected():
    line = log_crypto.encrypt_record("秘密", key="zhaomh666")
    try:
        log_crypto.decrypt_record(line, key="other-key")
    except ValueError as exc:
        assert "校验失败" in str(exc)
    else:
        raise AssertionError("错误口令必须解密失败")


def test_tampered_line_rejected():
    line = log_crypto.encrypt_record("原始内容")
    body = line[len(log_crypto.MARKER):]
    flipped = ("A" if body[0] != "A" else "B") + body[1:]
    try:
        log_crypto.decrypt_record(log_crypto.MARKER + flipped)
    except ValueError:
        pass
    else:
        raise AssertionError("被改动的密文必须校验失败")


def test_non_encrypted_line_returns_none():
    assert log_crypto.decrypt_record("2026-09-26 老格式明文行") is None


# ---------------------------------------------------------------------------
# Handler：写盘即密文
# ---------------------------------------------------------------------------

def _make_logger(name, handler):
    logger = logging.getLogger(name)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def test_handler_writes_only_ciphertext(tmp_path):
    path = tmp_path / "polish.log"
    handler = log_crypto.EncryptedFileHandler(path, key="zhaomh666")
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = _make_logger("t_enc", handler)

    logger.info("用户草稿：明天上午开会")
    logger.info("多行记录：\n第二行内容")
    handler.close()

    raw = path.read_text(encoding="utf-8")
    assert "用户草稿" not in raw and "第二行" not in raw      # 磁盘上没有明文
    assert all(line.startswith(log_crypto.MARKER) for line in raw.strip().splitlines())
    assert _records(path) == ["INFO 用户草稿：明天上午开会", "INFO 多行记录：\n第二行内容"]


def test_handler_reencrypt_pending_keeps_writing(tmp_path):
    """接管旧实例后要「关句柄 -> 清明文 -> 重开」，之后必须还能继续写。"""
    path = tmp_path / "polish.log"
    path.write_text("旧实例写的明文行\n", encoding="utf-8")   # 模拟旧实例残留的明文

    handler = log_crypto.EncryptedFileHandler(path, key="zhaomh666")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = _make_logger("t_pending", handler)
    logger.info("新实例第一条")

    changed = handler.reencrypt_pending()
    assert changed == 1                                        # 明文行被就地加密

    logger.info("重开之后仍能写入")
    handler.close()

    raw = path.read_text(encoding="utf-8")
    assert "旧实例写的明文行" not in raw
    assert "新实例第一条" not in raw
    assert _records(path) == ["旧实例写的明文行", "新实例第一条", "重开之后仍能写入"]


def test_install_file_logging_matches_old_behavior(tmp_path):
    """等价于原 basicConfig(filename=...)：根 logger 得到一个文件 Handler。"""
    path = tmp_path / "polish.log"
    root = logging.getLogger()
    before = list(root.handlers)
    handler = log_crypto.install_file_logging(path, fmt="%(message)s")
    try:
        logging.getLogger("polish").info("启动完成")
        assert root.level == logging.INFO
        assert handler in root.handlers
        handler.flush()
        assert _records(path) == ["启动完成"]
    finally:
        root.removeHandler(handler)
        handler.close()
        for h in before:                 # 还原现场，别影响别的测试
            if h not in root.handlers:
                root.addHandler(h)


# ---------------------------------------------------------------------------
# 历史明文日志就地加密 / 解密工具
# ---------------------------------------------------------------------------

def test_ensure_encrypted_file_migrates_plaintext(tmp_path):
    path = tmp_path / "polish.log"
    path.write_text("老明文第一行\n老明文第二行\n", encoding="utf-8")

    assert log_crypto.ensure_encrypted_file(path) == 2
    raw = path.read_text(encoding="utf-8")
    assert "老明文" not in raw
    assert _records(path) == ["老明文第一行", "老明文第二行"]

    # 幂等：再来一次不重复加密、不改动内容
    assert log_crypto.ensure_encrypted_file(path) == 0
    assert path.read_text(encoding="utf-8") == raw


def test_ensure_encrypted_file_on_missing_or_empty(tmp_path):
    missing = tmp_path / "nope.log"
    assert log_crypto.ensure_encrypted_file(missing) == 0
    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    assert log_crypto.ensure_encrypted_file(empty) == 0
    assert empty.read_text(encoding="utf-8") == ""


def test_decrypt_file_restores_plaintext(tmp_path):
    src = tmp_path / "polish.log"
    with open(src, "w", encoding="utf-8") as fh:
        for text in ["第一条", "第二条 中文", "第三行\n内部换行"]:
            fh.write(log_crypto.encrypt_record(text) + "\n")

    info = log_decrypt.decrypt_log_file(src)
    assert info["enc"] == 3 and info["bad"] == 0 and info["total"] == 3
    assert os.path.basename(info["dst"]) == "polish.decrypted.log"
    assert open(info["dst"], encoding="utf-8").read() == "第一条\n第二条 中文\n第三行\n内部换行\n"


def test_decrypt_tool_passes_plaintext_lines_through(tmp_path):
    """升级前的老日志是明文：解密工具应原样输出而不是报错或丢内容。"""
    src = tmp_path / "polish.log"
    src.write_text("历史明文行\n" + log_crypto.encrypt_record("加密行") + "\n", encoding="utf-8")

    info = log_decrypt.decrypt_log_file(src)
    assert info["plain"] == 1 and info["enc"] == 1 and info["bad"] == 0
    assert open(info["dst"], encoding="utf-8").read() == "历史明文行\n加密行\n"


def test_blank_lines_are_not_counted_as_decrypted(tmp_path):
    """老明文日志里被折行的空行不算「解密成功」，但必须原样保留。"""
    src = tmp_path / "polish.log"
    src.write_text("明文第一行\n\n明文第三行\n", encoding="utf-8")

    info = log_decrypt.decrypt_log_file(src)
    assert info["enc"] == 0 and info["plain"] == 3 and info["bad"] == 0
    assert open(info["dst"], encoding="utf-8").read() == "明文第一行\n\n明文第三行\n"


def test_decrypt_to_text_and_missing_file(tmp_path):
    src = tmp_path / "polish.log"
    src.write_text(log_crypto.encrypt_record("只看文本") + "\n", encoding="utf-8")
    assert log_decrypt.decrypt_log_to_text(src) == "只看文本\n"

    try:
        log_decrypt.decrypt_log_file(tmp_path / "不存在.log")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("日志不存在时应报 FileNotFoundError")


if __name__ == "__main__":
    # 极简运行器：直跑本文件即可执行全部用例，不依赖 pytest。
    #
    # 兼容 pytest 风格的夹具：用例若声明了 `tmp_path` 形参（Path），
    # 就为它建一个临时目录并在结束时清理。项目当初是按 pytest 夹具写的，
    # 但运行方式是 `python3 tests/test_xxx.py`；没有这段支持的话，
    # 这类用例会被当成「缺参数」直接报错，等于从未被执行过。
    import inspect
    import tempfile
    from pathlib import Path

    def _call(fn):
        if not inspect.signature(fn).parameters:
            return fn()
        with tempfile.TemporaryDirectory() as d:
            return fn(Path(d))

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            _call(fn)
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
