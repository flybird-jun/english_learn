# -*- coding: utf-8 -*-
"""routes_study — 研读台路由: 文章 · 划词翻译/词典 · 笔记 · 朗读 · 音色列表

  /api/articles /api/article/{aid}            文章列表与分段正文
  /api/translate                              划句翻译 (无服务端缓存, 前端 JS 会话缓存)
  /api/dict                                   单词词典 (words_dict 持久缓存, 划词先查库)
  /api/notes (GET/POST/DELETE)                笔记 CRUD (同文章同文本 upsert, 前端按文本搜索定位)
  /api/say /api/say_stream                    单词朗读 (words 缓存) / 划句流式合成
  /api/article_audio /api/article_audio_starts 全文音频流式合成落盘 + 段落起始秒
  /audio/words/...                            词音频文件回放
  /api/voices                                 edge-tts 音色列表 (后台预载+缓存)
"""
import asyncio
import json
import re
import threading
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from content.articles import get_article_path, list_articles, load_paragraphs
from content.translate import fetch_dict, translate_text
from core.config import (DEFAULT_VOICE, FALLBACK_VOICES, FULL_DIR,
                         NOTES_MAX_LIMIT, SAY_MAX_CHARS, TRANSLATE_MAX_CHARS,
                         WORDS_DIR)
from core.db import DB, now_str
from core.log import log
from speech import tts

router = APIRouter()


class TranslateReq(BaseModel):
    text: str


class NoteReq(BaseModel):
    article_id: str
    text: str
    note: str = ""
    translation: str = ""
    phonetic: str = ""


class SayReq(BaseModel):
    text: str
    voice: str = DEFAULT_VOICE


# ---------------------------------------------------------------------------
# 文章
# ---------------------------------------------------------------------------
@router.get("/api/articles")
def api_articles():
    return list_articles()


@router.get("/api/article/{aid}")
def api_article(aid: str):
    try:
        p = get_article_path(aid)
    except FileNotFoundError:
        raise HTTPException(404, "文章不存在")
    paras = load_paragraphs(p.read_text(encoding="utf-8", errors="replace"))
    title = paras[0]["text"] if (paras and paras[0]["is_title"]) else aid
    return {"id": aid, "title": title, "paras": paras}


# ---------------------------------------------------------------------------
# 翻译与笔记
# ---------------------------------------------------------------------------
@router.post("/api/translate")
def api_translate(req: TranslateReq):
    """划句翻译。无服务端缓存 (前端 JS 按 text 会话缓存); 划词走 /api/dict + words_dict。"""
    text = re.sub(r"\s+", " ", req.text).strip()
    if not text:
        raise HTTPException(400, "文本为空")
    if len(text) > TRANSLATE_MAX_CHARS:
        raise HTTPException(400, f"文本过长(上限 {TRANSLATE_MAX_CHARS} 字符)")
    try:
        trans, _source = translate_text(text)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"translation": trans}


@router.get("/api/dict")
def api_dict(text: str):
    """单词词典: 音标 + 多条词义。持久缓存于 words_dict 表; 查不到返回空值, 前端优雅降级。"""
    word = text.strip()
    if re.search(r"\s", word) or not word or len(word) > 40:
        return {"phonetic": None, "senses": []}
    key = word.lower()
    row = DB.queryone("SELECT phonetic, senses FROM words_dict WHERE word=?", (key,))
    if row:
        return {"phonetic": row["phonetic"],
                "senses": json.loads(row["senses"] or "[]")}
    d = fetch_dict(key)
    if d:
        # 词义为空可能是源站瞬时故障(仅兜底音标成功), 缓存会让空结果永久化, 只回不存
        if d["senses"]:
            DB.exec("INSERT OR REPLACE INTO words_dict(word, phonetic, senses) VALUES (?,?,?)",
                    (key, d["phonetic"], json.dumps(d["senses"], ensure_ascii=False)))
        return d
    return {"phonetic": None, "senses": []}


@router.get("/api/notes")
def api_list_notes(limit: int = 300):
    limit = max(1, min(limit, NOTES_MAX_LIMIT))     # 防负数/超大值
    return DB.query(
        "SELECT * FROM notes ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))


@router.post("/api/notes")
def api_add_note(req: NoteReq):
    """加入笔记: 同文章同文本视为同一条, 已存在则用非空字段覆盖 (可先存原文后补翻译)。"""
    text = re.sub(r"\s+", " ", req.text).strip()
    note = req.note.strip()
    trans = re.sub(r"\s+", " ", req.translation).strip()
    phon = req.phonetic.strip()
    # 只要求原文: 翻译未返回时也可先收进笔记, 之后同词再 ＋ 经 upsert 补全
    if not text:
        raise HTTPException(400, "文本为空")
    row = DB.queryone(
        "SELECT id, note, translation, phonetic FROM notes WHERE article_id=? AND text=?",
        (req.article_id, text))
    if row:                         # 已在笔记: 只用非空字段覆盖
        DB.exec("UPDATE notes SET note=?, translation=?, phonetic=? WHERE id=?",
                (note or row["note"], trans or row["translation"],
                 phon or row["phonetic"], row["id"]))
        return {"id": row["id"]}
    try:
        cur = DB.exec(
            "INSERT INTO notes(article_id, text, note, translation, phonetic, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (req.article_id, text, note, trans, phon, now_str()))
        return {"id": cur.lastrowid}
    except Exception:               # 并发同文本撞 UNIQUE: 退化为更新
        row = DB.queryone(
            "SELECT id FROM notes WHERE article_id=? AND text=?", (req.article_id, text))
        if row is None:
            raise HTTPException(500, "笔记写入失败")
        DB.exec("UPDATE notes SET note=?, translation=?, phonetic=? WHERE id=?",
                (note, trans, phon, row["id"]))
        return {"id": row["id"]}


@router.delete("/api/notes/{rid}")
def api_del_note(rid: int):
    DB.exec("DELETE FROM notes WHERE id=?", (rid,))
    return {"ok": True}


# ---------------------------------------------------------------------------
# 音频: 单词朗读 (words 缓存, 可读名) + 划句朗读 (流式, 不落盘)
# ---------------------------------------------------------------------------
@router.post("/api/say")
def api_say(req: SayReq):
    """朗读: 无空白视为单词 → words 缓存(可读名); 含空白视为句子 → 流式播放不落盘"""
    text = re.sub(r"\s+", " ", req.text).strip()
    if not text:
        raise HTTPException(400, "文本为空")
    if len(text) > SAY_MAX_CHARS:
        raise HTTPException(400, f"片段过长(上限 {SAY_MAX_CHARS} 字符)")
    if re.search(r"\s", text):              # 划句: 流式播放, 不保存
        return {"url": f"/api/say_stream?text={quote(text)}&voice={quote(req.voice)}",
                "cached": False}
    try:
        url, cached = tts.snippet_audio(text, req.voice)
    except Exception as e:                  # 非法/已下线音色等: 转 502 而非 500
        raise HTTPException(502, f"音频合成失败: {e}")
    return {"url": url, "cached": cached}


@router.get("/api/say_stream")
async def api_say_stream(text: str, voice: str = DEFAULT_VOICE):
    """划句音频流: 边合成边回传, 不落盘。"""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise HTTPException(400, "文本为空")
    if len(text) > SAY_MAX_CHARS:
        raise HTTPException(400, f"片段过长(上限 {SAY_MAX_CHARS} 字符)")
    gen = tts.stream_snippet(text, voice)
    try:
        first = await gen.__anext__()       # 先取首块, 合成出错时仍能以 502 应答
    except StopAsyncIteration:
        raise HTTPException(502, "未收到音频数据")
    except Exception as e:
        raise HTTPException(502, f"音频合成失败: {e}")

    async def _pipe():
        yield first
        async for chunk in gen:
            yield chunk

    return StreamingResponse(_pipe(), media_type="audio/mpeg")


# ---------------------------------------------------------------------------
# 全文音频 (播放全文): edge-tts 原生流式合成, 边合成边回传并落盘; 命中缓存直接回文件
# ---------------------------------------------------------------------------
_FULL_STREAMS = {}      # sha1(voice|全文) -> {"starts": [...], "done": bool}


def _article_full_text(aid: str):
    """(整篇文章的合成文本, 段落数)。文本各段落含标题, 以空行连接; 文章不存在/为空时抛 404。"""
    try:
        p = get_article_path(aid)
    except FileNotFoundError:
        raise HTTPException(404, "文章不存在")
    paras = load_paragraphs(p.read_text(encoding="utf-8", errors="replace"))
    if not paras:
        raise HTTPException(404, "文章为空")
    return "\n\n".join(x["text"] for x in paras), len(paras)


@router.get("/api/article_audio")
async def api_article_audio(article_id: str, voice: str = DEFAULT_VOICE):
    text, nparas = _article_full_text(article_id)
    h = tts.audio_hash(voice, text)
    mp3 = FULL_DIR / f"{h}.mp3"
    if tts.load_full_starts(voice, text) is not None:      # mp3+json 均在: 直接播缓存
        # no-cache: URL 不含内容哈希, 防浏览器启发式缓存在改稿后回放旧音频
        return FileResponse(str(mp3), media_type="audio/mpeg",
                            headers={"Cache-Control": "no-cache"})
    reg = _FULL_STREAMS.get(h)
    if reg is None or reg["done"]:     # 并发同 hash 时复用在途注册项, 避免 starts 进度重置
        reg = {"starts": [None] * nparas, "done": False}   # 按段落数预填, 合成中渐次补齐
        _FULL_STREAMS[h] = reg
    gen = tts.full_audio_stream(text, voice, mp3, reg["starts"])
    try:
        first = await gen.__anext__()      # 先取首块, 合成出错时仍能以 502 应答
    except StopAsyncIteration:
        _FULL_STREAMS.pop(h, None)
        raise HTTPException(502, "未收到音频数据")
    except Exception as e:
        log("全文音频合成失败:", repr(e))
        _FULL_STREAMS.pop(h, None)
        raise HTTPException(502, f"音频合成失败: {e}")

    async def _pipe():
        ok = False
        try:
            yield first
            async for chunk in gen:
                yield chunk
            ok = True
        finally:
            reg["done"] = ok               # 客户端中途断开时保持未完成, 缓存已弃置

    return StreamingResponse(_pipe(), media_type="audio/mpeg")


@router.get("/api/article_audio_starts")
def api_full_starts(article_id: str, voice: str = DEFAULT_VOICE):
    """全文音频里各段落的起始秒。流式合成期间渐次补齐, 供前端高亮当前段落。"""
    text, _ = _article_full_text(article_id)
    h = tts.audio_hash(voice, text)
    reg = _FULL_STREAMS.get(h)
    if reg is not None and not reg["done"]:
        disk = tts.load_full_starts(voice, text)   # 并发/孤儿流可能已落盘完整缓存
        if disk is not None:                       # 以磁盘为准, 免被残缺注册项遮蔽
            reg = _FULL_STREAMS[h] = {"starts": disk, "done": True}
    if reg is None:
        starts = tts.load_full_starts(voice, text)
        if starts is None:
            return {"starts": [], "complete": False}
        reg = _FULL_STREAMS[h] = {"starts": starts, "done": True}
    return {"starts": reg["starts"], "complete": reg["done"]}


@router.get("/audio/words/{voice}/{fname}")
def api_audio_word_v(voice: str, fname: str):
    if not re.fullmatch(r"[\w-]{1,60}", voice) or not re.fullmatch(r"[\w' -]{1,64}\.mp3", fname):
        raise HTTPException(400, "非法文件名")
    path = WORDS_DIR / voice / fname
    if not path.is_file():
        raise HTTPException(404, "音频不存在")
    return FileResponse(str(path), media_type="audio/mpeg")


@router.get("/audio/words/{fname}")
def api_audio_word(fname: str):
    return _audio_file(WORDS_DIR, fname)


def _audio_file(d, fname: str):
    fname = fname.strip()
    if not re.fullmatch(r"[0-9a-f]{40}\.mp3", fname):
        raise HTTPException(400, "非法文件名")
    path = d / fname
    if not path.is_file():
        raise HTTPException(404, "音频不存在")
    return FileResponse(str(path), media_type="audio/mpeg")


# ---------------------------------------------------------------------------
# 音色列表: 启动时后台预载(微软接口较慢), 结果缓存
# ---------------------------------------------------------------------------
_VOICES = {"names": None, "loading": False}


def _load_voices():
    try:
        import edge_tts
        try:
            voices = asyncio.run(edge_tts.list_voices())
        except AttributeError:
            voices = asyncio.run(edge_tts.VoicesManager.create()).find()
        names = sorted({v["ShortName"] for v in voices
                        if str(v.get("Locale", "")).startswith("en")})
        if names:
            _VOICES["names"] = names
            log(f"音色列表加载完成: {len(names)} 个")
    except Exception as e:
        log("音色列表加载失败(使用内置列表):", e)
    finally:
        _VOICES["loading"] = False


@router.get("/api/voices")
def api_voices():
    if _VOICES["names"]:
        return _VOICES["names"]
    if not _VOICES["loading"]:
        _VOICES["loading"] = True
        threading.Thread(target=_load_voices, daemon=True).start()
    return FALLBACK_VOICES
