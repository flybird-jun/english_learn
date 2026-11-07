# -*- coding: utf-8 -*-
"""articles — 文章加载与分词

读取 resource/article 下的 .md/.txt 文章: 清洗 markdown 标记 → 按空行/标题切段
→ 每段切词 (tokens), 供前端逐词交互 (划词翻译/笔记定位/全文朗读对齐)。
对外: list_articles / get_article_path / load_paragraphs。
"""
import re
from pathlib import Path

from core.config import ARTICLE_DIR

_md_head = re.compile(r"^\s{0,3}#{1,6}\s*")
_md_inline = re.compile(r"[`*_]{1,3}")


def clean_md_line(line: str) -> str:
    return _md_inline.sub("", _md_head.sub("", line)).strip()


def load_paragraphs(text: str):
    """把文章文本切成段落。返回 [{idx, text, is_title, tokens}]"""
    paras = []
    # 标题行强制独立成段(即使后面没有空行, 如 markdown 标题直接跟正文)
    text = re.sub(r"(?m)^(\s{0,3}#{1,6}\s+.*)$", r"\n\1\n", text)
    for block in re.split(r"\n\s*\n", text.strip()):
        if not block.strip():
            continue
        is_title = bool(re.match(r"^\s{0,3}#{1,6}\s", block))
        lines = [clean_md_line(l) for l in block.strip().splitlines()]
        content = " ".join(l for l in lines if l).strip()
        if not content:
            continue
        idx = len(paras)
        paras.append({
            "idx": idx, "text": content, "is_title": is_title,
            "tokens": re.findall(r"\S+", content),
        })
    return paras


def get_article_path(aid: str) -> Path:
    for ext in (".md", ".txt"):
        p = ARTICLE_DIR / (aid + ext)
        if p.is_file():
            return p
    raise FileNotFoundError(aid)


def list_articles():
    arts = []
    files = sorted(list(ARTICLE_DIR.glob("*.md")) + list(ARTICLE_DIR.glob("*.txt")))
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        paras = load_paragraphs(text)
        title = paras[0]["text"] if (paras and paras[0]["is_title"]) else f.stem
        arts.append({"id": f.stem, "title": title})
    return arts


def article_title(aid: str) -> str:
    for a in list_articles():
        if a["id"] == aid:
            return a["title"]
    return aid
