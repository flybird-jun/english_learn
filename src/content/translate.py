# -*- coding: utf-8 -*-
"""translate — 划词/划句翻译与单词词典 (全部免费公共源, 依次回退)

翻译: google gtx → MyMemory (分段) → Lingva, 全失败抛 RuntimeError;
词典: 有道 jsonapi (词性分组的词义列表) 为主源, dictionaryapi.dev 兜底音标。
无本地状态; 词典结果由调用方 (webapp/routes_study.py) 持久缓存 words_dict 表,
翻译由前端 JS 会话缓存 (2026-11-09 设计: 服务端不缓存翻译)。
"""
import re

import httpx
from urllib.parse import quote


def _mm_chunk(text: str, limit=420):
    parts, cur = [], ""
    for seg in re.split(r"(?<=[.!?;])\s+", text):
        if len(cur) + len(seg) + 1 > limit and cur:
            parts.append(cur)
            cur = seg
        else:
            cur = f"{cur} {seg}".strip()
    if cur:
        parts.append(cur)
    return parts or [text]


def translate_text(text: str):
    """返回 (译文, 来源)。全部失败时抛 RuntimeError。"""
    text = re.sub(r"[—–]", " ", text).strip()
    errors = []

    # 1) Google translate (gtx 公共接口)
    try:
        r = httpx.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text},
            timeout=8.0,
        )
        data = r.json()
        out = "".join(seg[0] for seg in (data[0] or []) if seg and seg[0])
        if out.strip():
            return out.strip(), "google"
    except Exception as e:
        errors.append(f"google:{e.__class__.__name__}")

    # 2) MyMemory (免费, 国内可达)
    try:
        parts = _mm_chunk(text)
        outs = []
        for part in parts:
            r = httpx.get(
                "https://api.mymemory.translated.net/get",
                params={"q": part, "langpair": "en|zh-CN"},
                timeout=8.0,
            )
            d = r.json()
            if d.get("responseStatus") == 200 and d.get("responseData", {}).get("translatedText"):
                outs.append(d["responseData"]["translatedText"])
            else:
                break
        # 任一分段失败即视为整个源失败, 避免缓存截断的半截译文
        if len(outs) == len(parts) and outs:
            out = " ".join(outs).strip()
            if out:
                return out, "mymemory"
        errors.append("mymemory:incomplete")
    except Exception as e:
        errors.append(f"mymemory:{e.__class__.__name__}")

    # 3) Lingva 公共实例
    try:
        r = httpx.get(
            f"https://lingva.ml/api/v1/en/zh-CN/{quote(text)}",
            headers={"Accept": "application/json"}, timeout=8.0,
        )
        r.raise_for_status()
        out = (r.json().get("translation") or "").strip()
        if out:
            return out, "lingva"
    except Exception as e:
        errors.append(f"lingva:{e.__class__.__name__}")

    raise RuntimeError("所有翻译源均失败: " + "; ".join(errors))


_POS_RE = re.compile(r"^([a-zA-Z]+\.\s*|【[^】]*】\s*|<[^>]+>\s*)")


def _sense_items(trs, max_items: int = 6):
    """把有道 trs 解析成去重的词义条目列表。

    trs 每项是一条长串, 分号分隔多个子义项(如 week 的十来个"周"义), 且
    剥掉（…）用法括注后子义项会互相重复 —— 因此: 按分号拆条、去括注、
    组内去重、词性前缀顺延, 再跨义项组轮转取条目, 保证多词性单词各词性
    都有露出。"""
    groups = []
    for t in trs or []:
        for tr in t.get("tr") or []:
            i = (tr.get("l") or {}).get("i")
            if isinstance(i, list):
                i = "".join(map(str, i))
            if not (isinstance(i, str) and i.strip()):
                continue
            m = _POS_RE.match(i.strip())
            pos = m.group(1).strip() if m else ""
            body = i.strip()[m.end():] if m else i.strip()
            items, seen = [], set()
            for part in re.split(r"[；;]", body):
                part = re.sub(r"[（(][^）)]*[)）]", "", part)
                part = re.sub(r"\s+", " ", part).strip(" ，,；;。")
                if not part:
                    continue
                key = part.lower()
                if key in seen:
                    continue
                seen.add(key)
                if len(part) > 40:
                    part = part[:40] + "…"
                items.append(f"{pos} {part}".strip() if pos else part)
            if items:
                groups.append(items)
    out, seen, idx = [], set(), 0
    while len(out) < max_items:
        progressed = False
        for g in groups:
            if idx < len(g):
                progressed = True
                key = g[idx].lower()
                if key not in seen:
                    seen.add(key)
                    out.append(g[idx])
                    if len(out) >= max_items:
                        break
        if not progressed:
            break
        idx += 1
    return out


def _norm_word(w: str) -> str:
    """排版字符归一化: 弯引号→直引号, 各类连字符/破折号→半角连字符。"""
    for a, b in (("’", "'"), ("‘", "'"), ("‐", "-"), ("‑", "-"),
                 ("‒", "-"), ("–", "-"), ("—", "-")):
        w = w.replace(a, b)
    return w


def _youdao_dict(word: str):
    """有道词典查询: 返回 (音标, 词义列表) 或 None。
    注意必须用老端点 jsonapi —— 新端点 jsonapi_s 已反爬, 会随机返回无关词条。"""
    word = _norm_word(word)
    try:
        r = httpx.get(
            "https://dict.youdao.com/jsonapi",
            params={"dictno": "", "q": word},
            timeout=6.0,
        )
        w = (r.json().get("ec") or {}).get("word")
        w = w[0] if isinstance(w, list) and w else (w if isinstance(w, dict) else {})
        # 反爬漏网校验: 返回词条与查询词不符时整体作废 (两侧归一化后比对)
        rp = w.get("return-phrase")
        rp = "".join(map(str, rp["l"]["i"])) if isinstance(rp, dict) and isinstance(rp.get("l", {}).get("i"), list) \
            else (rp.get("l", {}).get("i") if isinstance(rp, dict) else rp)
        if not rp or _norm_word(str(rp)).lower() != word.lower():
            return None
        ph = (w.get("usphone") or w.get("ukphone") or "").strip() or None
        return ph, _sense_items(w.get("trs"))
    except Exception:
        return None


def fetch_dict(word: str):
    """单词词典查询: {'phonetic': '/ipa/' 或 None, 'senses': [词义…]}。
    有道为主源(国内可达), dictionaryapi.dev 兜底音标; 全失败返回 None。"""
    word = word.lower()
    got = _youdao_dict(word)
    phon = got[0] if got else None
    senses = got[1] if got else []
    if not phon:
        phon = _dictionaryapi_phonetic(word)
    if not phon and not senses:
        return None
    return {"phonetic": f"/{phon.strip('/')}/" if phon else None, "senses": senses}


def _dictionaryapi_phonetic(word: str):
    try:
        r = httpx.get(
            f"https://api.dictionaryapi.dev/api/v2/entries/en/{quote(word)}",
            timeout=6.0,
        )
        if r.status_code != 200:
            return None
        for entry in r.json():
            ph = (entry.get("phonetic") or "").strip()
            if ph:
                return ph
            for p in entry.get("phonetics") or []:
                ph = (p.get("text") or "").strip()
                if ph:
                    return ph
    except Exception:
        return None
    return None
