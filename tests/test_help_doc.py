"""说明文档解析的单元测试（纯本地，不进入 Tk，不联网）。

锁住的是：设置页「使用说明」页签依赖的解析结果 —— 结构、行内标记与目录，
以及打包后真正渲染的那份 md 本身能解析出免责声明等关键章节。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import help_doc

SAMPLE = """\
# 标题

正文一行。

## 一、章节

- 列表项 **加粗** 与 `代码`
1. 有序项
   续行内容
> ⚠️ 重要提示
> 第二行提示

---

### 小标题

*斜体* 与 <https://example.com/x>
"""


def test_parse_blocks_kinds():
    blocks = help_doc.parse_blocks(SAMPLE)
    kinds = [k for k, _ in blocks]
    assert kinds == [help_doc.H1, help_doc.P, help_doc.H2, help_doc.LI,
                     help_doc.OLI, help_doc.QUOTE, help_doc.SEP, help_doc.H3,
                     help_doc.P]


def test_parse_blocks_contents():
    got = {}
    blocks = help_doc.parse_blocks(SAMPLE)
    for k, t in blocks:
        got.setdefault(k, []).append(t)

    assert got[help_doc.H1] == ["标题"]
    assert got[help_doc.H2] == ["一、章节"]
    assert got[help_doc.H3] == ["小标题"]
    # 列表去掉「- 」前缀，编号列表保留编号
    assert got[help_doc.LI] == ["列表项 **加粗** 与 `代码`"]
    assert got[help_doc.OLI] == ["1. 有序项\n续行内容"]
    # 连续引用合成一块，缩进续行并入上一条（保持原文换行结构）
    assert got[help_doc.QUOTE] == ["⚠️ 重要提示\n第二行提示"]


def test_inline_parts():
    assert help_doc.inline_parts("纯文本") == [("纯文本", ())]
    assert help_doc.inline_parts("**粗** 与 `码`") == [
        ("粗", ("bold",)), (" 与 ", ()), ("码", ("code",))]
    # 粗体里可以再包代码，标签叠加
    assert help_doc.inline_parts("**`config.json`**") == [
        ("config.json", ("bold", "code"))]
    # 自动链接去掉尖括号，按代码样式显示
    assert help_doc.inline_parts("<https://a.cn/b>") == [
        ("https://a.cn/b", ("code",))]
    # 斜体
    assert help_doc.inline_parts("*备注*") == [("备注", ("em",))]


def test_plain_text_strips_markers():
    assert help_doc.plain_text("**加粗** 与 `代码` 与 *斜体*") == "加粗 与 代码 与 斜体"


def test_plain_document_keeps_bullets():
    text = help_doc.plain_document(help_doc.parse_blocks(SAMPLE))
    assert "· 列表项 加粗 与 代码" in text      # 无序项补回「· 」
    assert "1. 有序项" in text                 # 有序项保留编号
    assert "**" not in text and "`" not in text


def test_sections_are_h2_with_index():
    blocks = help_doc.parse_blocks(SAMPLE)
    secs = help_doc.sections(blocks)
    assert secs == [("一、章节", 2)]
    kind, _text = blocks[secs[0][1]]
    assert kind == help_doc.H2


def test_missing_file_is_not_fatal(tmp_path):
    missing = tmp_path / "nope.md"
    assert help_doc.load_markdown(missing) == ""
    assert help_doc.load_blocks(missing) == []
    assert help_doc.sections([]) == []


def test_load_blocks_from_file(tmp_path):
    p = tmp_path / "doc.md"
    p.write_text(SAMPLE, encoding="utf-8")
    blocks = help_doc.load_blocks(p)
    assert [k for k, _ in blocks] == [k for k, _ in help_doc.parse_blocks(SAMPLE)]
    assert help_doc.load_blocks(p) == blocks       # 命中缓存，结果一致


# ---------------------------------------------------------------------------
# 随程序分发的那份真实文档：结构必须完整（页签目录、免责声明都靠它）
# ---------------------------------------------------------------------------

def test_real_document_parses():
    blocks = help_doc.load_blocks()
    assert blocks, "未找到使用说明文档（使用说明-给朋友.md）"

    sections = [t for t, _ in help_doc.sections(blocks)]
    for expected in ("一、安装与首次启动", "二、首次使用：填写 API Key（只需一次）",
                     "三、日常怎么用", "四、常见问题", "五、需要联网吗？",
                     "六、免责声明与使用边界（重要）"):
        assert expected in sections, f"缺少章节：{expected}"

    joined = "\n".join(t for _k, t in blocks)
    # 免责声明三要素：OCR 取聊天记录并作为上下文发送、建议不要用于敏感信息、不承担责任
    assert "OCR" in joined
    assert "作为上下文" in joined and "发送给你自己" in joined
    assert "建议不要使用本功能" in joined
    assert "本软件及作者不承担任何责任" in joined


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
