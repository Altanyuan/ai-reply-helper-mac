"""《使用说明-给朋友.md》的加载与解析（供设置页「使用说明」页签渲染）。

为什么要单独一个模块
--------------------
程序内展示的说明与分发的文档是**同一份 md**（打包进 exe；程序目录放同名文件可覆盖），
所以更新说明只需改文档，不用动界面代码 —— 界面只负责把解析结果映射成字体与缩进。
解析逻辑独立出来后也能单独跑单元测试，不必拉起 Tk。

支持的语法（够用就好的 markdown 子集）
------------------------------------
    # / ## / ###    标题（## 同时作为左侧目录）
    - 开头          无序列表（渲染成 ·）
    1. 开头         有序列表（保留编号）
    > 开头          引用块（含 ⚠️ 的会以警示色显示）
    ---             分隔线（渲染成一段留白）
    **粗体**、`行内代码`、*斜体*、<网址>   行内标记
    缩进续行         并入上一条（保持原文的换行结构，不做 markdown 软换行合并）
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

import paths

#: 程序内展示的说明文档（与分发给使用者的那份完全同一文件）
DOC_NAME = "使用说明-给朋友.md"

# 块类型
H1, H2, H3, P, LI, OLI, QUOTE, SEP = "h1", "h2", "h3", "p", "li", "oli", "quote", "sep"

#: 行内标记：**粗体** / *斜体* / `代码` / <自动链接>
_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\*([^*\n]+)\*|`([^`]+)`|<(https?://[^>]+)>")
_DASH_RE = re.compile(r"^-{3,}\s*$")
_OLI_RE = re.compile(r"^(\d+)[.、]\s+")
_BULLET_RE = re.compile(r"^[-*·]\s+")


def doc_path(path=None) -> Path:
    """说明文档路径：程序目录的同名文件优先，否则用打包进 exe 的那份。"""
    return Path(path) if path else paths.asset(DOC_NAME)


def load_markdown(path=None) -> str:
    """读取说明文档原文；读不到时返回空串（界面据此给出缺文件的提示）。"""
    try:
        return doc_path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def parse_blocks(text: str) -> List[Tuple[str, str]]:
    """把 markdown 文本解析成 ``[(类型, 文本), ...]``。

    缩进续行会并入上一条，因此「原文怎么换行、界面就怎么换行」，
    引用块里的多行提示不会被挤成一整段。
    """
    blocks: List[Tuple[str, str]] = []

    def _push(kind: str, content: str):
        blocks.append((kind, content))

    def _append(chunk: str):
        """续行：并入上一条（没有上一条时按普通段落处理）。"""
        if blocks:
            kind, prev = blocks[-1]
            blocks[-1] = (kind, prev + "\n" + chunk)
        else:
            _push(P, chunk)

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if raw[:1].isspace():                       # 缩进续行
            _append(stripped)
            continue
        if _DASH_RE.match(stripped):
            _push(SEP, "")
            continue
        if stripped.startswith("### "):
            _push(H3, stripped[4:].strip())
        elif stripped.startswith("## "):
            _push(H2, stripped[3:].strip())
        elif stripped.startswith("# "):
            _push(H1, stripped[2:].strip())
        elif stripped.startswith(">"):
            content = stripped[1:].strip()
            if blocks and blocks[-1][0] == QUOTE:    # 连续引用合并成一块
                blocks[-1] = (QUOTE, blocks[-1][1] + "\n" + content)
            else:
                _push(QUOTE, content)
        elif _BULLET_RE.match(stripped):
            _push(LI, _BULLET_RE.sub("", stripped, count=1))
        elif _OLI_RE.match(stripped):
            _push(OLI, stripped)                     # 编号保留，界面不再加前缀
        else:
            _push(P, stripped)
    return blocks


_CACHE = {}     # (路径, mtime, 大小) -> blocks（只缓存最近一次，避免无限增长）


def load_blocks(path=None) -> List[Tuple[str, str]]:
    """读取并解析说明文档；文件缺失/读取失败时返回空列表。"""
    p = doc_path(path)
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except Exception:
        return []
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    blocks = parse_blocks(load_markdown(p))
    _CACHE.clear()
    _CACHE[key] = blocks
    return blocks


def inline_parts(text: str) -> List[Tuple[str, Tuple[str, ...]]]:
    """拆分行内标记，返回 ``[(文本, 标签元组), ...]``。

    标签元组可能同时含 ``"bold"`` 与 ``"code"``（如 ``**`config.json`**``），
    由界面自行组合成对应字体；嵌套的粗体里还能再包代码。
    """
    return _split(text, ())


def _split(text: str, tags: Tuple[str, ...]) -> List[Tuple[str, Tuple[str, ...]]]:
    out: List[Tuple[str, Tuple[str, ...]]] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], tags))
        if m.group(1) is not None:                  # **粗体**
            out.extend(_split(m.group(1), tags + ("bold",)))
        elif m.group(2) is not None:                # *斜体*
            out.extend(_split(m.group(2), tags + ("em",)))
        elif m.group(3) is not None:                # `代码`
            out.append((m.group(3), tags + ("code",)))
        else:                                       # <网址>
            out.append((m.group(4), tags + ("code",)))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], tags))
    return out


def plain_text(text: str) -> str:
    """去掉行内标记，得到纯文本（用于左侧目录按钮等不支持富文本的地方）。"""
    return "".join(seg for seg, _tags in inline_parts(text))


def sections(blocks: List[Tuple[str, str]]) -> List[Tuple[str, int]]:
    """取出二级标题作为目录：``[(标题纯文本, 块下标), ...]``。"""
    return [(plain_text(t), i) for i, (kind, t) in enumerate(blocks) if kind == H2]


def plain_document(blocks: List[Tuple[str, str]]) -> str:
    """把块还原成不带 markdown 标记的纯文本（供「复制全文」等用途）。"""
    lines = []
    for kind, content in blocks:
        if kind == SEP:
            lines.append("")
            continue
        text = plain_text(content)
        if kind == LI:
            text = "· " + text
        lines.append(text)
    return "\n".join(lines)
