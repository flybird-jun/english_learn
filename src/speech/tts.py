# -*- coding: utf-8 -*-
"""tts — edge-tts 文本转语音 + 文件缓存

缓存策略(需求: 先查缓存, 命中直接播放, 不经过 edge-tts):
  - 单词音频:      resource/audio/words/<voice>/<词>.mp3 (可读名, 不适合命名时退回 hash)
  - 划句音频:      不落盘, /api/say_stream 流式合成边播边传
  - 全文音频:      resource/audio/full/<sha1(voice|全文)>.mp3 (+ .json 各段起始秒)
同文本+同音色只合成一次。全文合成顺带解析 WordBoundary 把各段起始秒对齐给前端高亮。
调用方: webapp/routes_study.py。
"""
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from core.config import FULL_DIR, WORDS_DIR


def audio_hash(voice: str, text: str) -> str:
    return hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()


async def _synthesize(text: str, voice: str, path: Path):
    import edge_tts
    tmp = path.with_suffix(".part.mp3")
    await edge_tts.Communicate(text, voice).save(str(tmp))
    os.replace(tmp, path)


def synth_cached(text: str, voice: str, path: Path) -> bool:
    """确保 path 存在: 命中缓存直接返回, 否则 edge-tts 合成。
    返回 True 表示发生了合成, False 表示命中缓存。"""
    if path.is_file() and path.stat().st_size > 0:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(_synthesize(text, voice, path))
    return True


def snippet_audio(text: str, voice: str):
    """单词音频缓存: resource/audio/words/<voice>/<词>.mp3 (可读文件名);
    清洗后为空或超长(截断会让不同长文本同名互踩缓存)时退回 hash 命名。
    返回 (url, cached)"""
    safe = re.sub(r"\s+", " ", text).strip().lower()
    safe = re.sub(r"[^\w' -]", "", safe).strip("- ")
    safe = re.sub(r"\s+", "_", safe)
    if safe and len(safe) <= 60:
        path = WORDS_DIR / voice / f"{safe}.mp3"
        cached = not synth_cached(text, voice, path)
        return f"/audio/words/{quote(voice)}/{quote(path.name)}", cached
    h = audio_hash(voice, text)
    cached = not synth_cached(text, voice, WORDS_DIR / f"{h}.mp3")
    return f"/audio/words/{h}.mp3", cached


async def stream_snippet(text: str, voice: str):
    """划句音频: 流式合成, 边合成边回传, 不落盘。"""
    import edge_tts
    com = edge_tts.Communicate(text, voice)
    async for ev in com.stream():
        if ev["type"] == "audio":
            yield ev["data"]


# ---------------------------------------------------------------------------
# 全文音频: edge-tts 原生流式合成(stream), 边合成边回传并落盘, 二次播放直读缓存
# ---------------------------------------------------------------------------
_TICKS_PER_SEC = 10_000_000        # WordBoundary offset 单位: 100ns ticks
_norm_re = re.compile(r"[^\w']+")


def _norm(w: str) -> str:
    """词对齐用归一化: 小写 + 去掉标点/空白(保留词内的 ')"""
    return _norm_re.sub("", w.lower())


async def full_audio_stream(text: str, voice: str, dest: Path, starts: list):
    """流式合成全文音频 (async generator, 逐块产出 mp3 字节)。

    - 用 edge-tts 原生 stream(): 音频块随合成进度实时产出, 客户端即到即播;
    - 同时写入 dest 旁的唯一 .part 临时文件, 完成后原子改名落盘;
      客户端中途断开时丢弃半成品, 下次播放重新合成;
    - 顺带解析 WordBoundary: 以词游标对齐到段落, 把每段起始秒写进 starts
      (下标与段落序号对应, 未对齐的段为 null)。服务端按 starts 落
      <hash>.json, 前端在播放全文时据此高亮当前段落。
    """
    import edge_tts
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{uuid4().hex[:8]}.part")
    # 词游标: 各段落文本按空白切词后摊平, 记录每个词所属段落
    para_words = [[w for w in (_norm(t) for t in re.findall(r"\S+", blk)) if w]
                  for blk in text.split("\n\n")]
    flat = [(pi, w) for pi, ws in enumerate(para_words) for w in ws]
    f, ok, tmp_js = None, False, None
    try:
        f = open(tmp, "wb")
        com = edge_tts.Communicate(text, voice, boundary="WordBoundary")
        ci = 0
        async for ev in com.stream():
            if ev["type"] == "audio":
                f.write(ev["data"])
                yield ev["data"]
                continue
            w, t = _norm(ev.get("text") or ""), ev.get("offset")
            # 游标耗尽后忽略剩余事件(em-dash 等会被合成器拆词, 事件数可多于词条数)
            if t is None or not w or not flat or ci >= len(flat):
                continue
            pi = flat[ci][0]
            if flat[ci][1] == w:                       # 命中游标: 正常推进
                ci += 1
            else:                                      # 合成器分词与切词有出入: 小窗前扫对齐
                for j in range(ci + 1, min(ci + 9, len(flat))):
                    if flat[j][1] == w:
                        ci, pi = j + 1, flat[j][0]
                        break
            if pi < len(starts) and starts[pi] is None:
                starts[pi] = t / _TICKS_PER_SEC
        if starts and starts[0] is None:
            starts[0] = 0.0                            # 首段从 0 秒起高亮
        f.close()
        f = None
        os.replace(tmp, dest)                          # mp3 落盘
        js = dest.with_suffix(".json")
        tmp_js = dest.with_name(f"{dest.stem}.json.{uuid4().hex[:8]}.part")
        tmp_js.write_text(json.dumps({"starts": starts}), encoding="utf-8")
        os.replace(tmp_js, js)
        tmp_js = None
        ok = True
    finally:
        if f is not None:
            f.close()
        if not ok:
            tmp.unlink(missing_ok=True)
            if tmp_js is not None:
                tmp_js.unlink(missing_ok=True)


def load_full_starts(voice: str, text: str):
    """返回已缓存全文音频的各段起始秒; mp3+json 不齐全时返回 None。"""
    h = audio_hash(voice, text)
    mp3, js = FULL_DIR / f"{h}.mp3", FULL_DIR / f"{h}.json"
    if not (mp3.is_file() and js.is_file()):
        return None
    try:
        return json.loads(js.read_text(encoding="utf-8"))["starts"]
    except Exception:
        return None
