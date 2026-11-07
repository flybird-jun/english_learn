# -*- coding: utf-8 -*-
"""routes_review — 复习考试路由: 出题准备 · 组卷 · 录音作答 · 现场评分 · 进度 · 结果

  /api/review/prepare(/status)   触发/轮询 claude design-exam 出题 (后台编排见 review/exam.py)
  /api/review/queue /session     到期题量+FSRS 参数 / 组卷 (due 卡优先, 新题补足)
  /api/exam/answer               收录音落盘后立即返回 (ASR/阅卷由后台并行)
  /api/exam/finish /state        结束会话并起统一阅卷 / 会话进度轮询
  /api/exam/rate /results        记录现场评分 / 逐题详情 (答案+ASR+判分+FSRS)
  /audio/reply/{fname}           考试录音回放

会话态驻留内存 (review/exam.py), 服务重启后会话失效。
"""
import re
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.config import ASR_MAX_SECONDS, EXAM_MAX_CARDS, REPLIES_DIR
from review import exam, fsrs_store

router = APIRouter()


class RateReq(BaseModel):
    session_id: int
    card_id: int
    rating: int                # 1=Again 2=Hard 3=Good 4=Easy


@router.post("/api/review/prepare")
def api_review_prepare():
    """触发准备: 若空闲则起后台任务 (design-exam 可耗时数分钟), 立即返回状态。"""
    return exam.start_prepare()


@router.get("/api/review/prepare/status")
def api_review_prepare_status():
    return exam.prepare_status()


@router.get("/api/review/queue")
def api_review_queue():
    """复习页信息: 到期题量 + FSRS 参数概览。"""
    return {"due": fsrs_store.due_count(), "params": fsrs_store.params_view()}


@router.post("/api/review/session")
def api_review_session(limit: int = EXAM_MAX_CARDS):
    """建考试会话 (题目不带答案; 答案只在结束后 results 返回)。"""
    s = exam.start_session(limit)
    if s["session_id"] is None:
        raise HTTPException(400, "没有到期的题目, 稍后再复习或先分析新文章")
    return s


@router.post("/api/exam/finish")
def api_exam_finish(session_id: int):
    try:
        first = exam.finish_session(session_id)
    except LookupError:
        raise HTTPException(404, "会话不存在")
    # 全部答完 → 后台统一阅卷 (结果页轮询 /api/exam/state 等 verdict);
    # 仅首次收卷时触发, 防 finish 重试起双 worker
    if first:
        exam.start_grading(session_id)
    return {"ok": True}


@router.get("/api/exam/state")
def api_exam_state(session_id: int):
    try:
        return exam.session_state(session_id)
    except LookupError:
        raise HTTPException(404, "会话不存在")


@router.post("/api/exam/answer")
async def api_exam_answer(session_id: int = Form(...), card_id: int = Form(...),
                          audio: UploadFile = File(None)):
    """收录音: 落盘并登记作答后立即返回, ASR/判分由后台 worker 并行处理。"""
    try:
        exam.get_session(session_id)
    except LookupError:
        raise HTTPException(404, "会话不存在")
    if not exam.has_item(session_id, card_id):
        raise HTTPException(400, "题目不属于本会话")

    fname = None
    audio_path = None
    if audio is not None and audio.filename:
        ext = Path(audio.filename).suffix.lower()
        if ext not in (".webm", ".ogg", ".wav", ".mp3", ".m4a", ".mp4"):
            raise HTTPException(400, f"不支持的音频格式: {ext}")
        fname = f"{session_id}_{card_id}_{uuid4().hex[:8]}{ext}"
        REPLIES_DIR.mkdir(parents=True, exist_ok=True)
        dest = REPLIES_DIR / fname
        cap = ASR_MAX_SECONDS * 64 * 1024        # opus≈24kbps, 120s 上限给到 8MB 余量
        size = 0
        try:
            with dest.open("wb") as f:
                while chunk := await audio.read(256 * 1024):
                    size += len(chunk)
                    if size > cap:
                        raise HTTPException(413, "录音过长")
                    f.write(chunk)
        except HTTPException:
            dest.unlink(missing_ok=True)
            raise
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(500, f"录音保存失败: {e}")
        if dest.stat().st_size < 1024:           # MediaRecorder 空文件兜底
            dest.unlink(missing_ok=True)
            fname = None
        else:
            audio_path = dest

    try:
        exam.submit_answer(session_id, card_id, audio_path)
    except ValueError as e:            # 重复作答/会话已收卷: 撤掉刚落的录音
        if fname:
            (REPLIES_DIR / fname).unlink(missing_ok=True)
        raise HTTPException(409, str(e) or "本题已作答")
    except LookupError:                # 会话/题目态变化: 同样撤录音
        if fname:
            (REPLIES_DIR / fname).unlink(missing_ok=True)
        raise HTTPException(400, "题目不属于本会话")
    return {"reply_id": card_id, "audio": fname}


@router.post("/api/exam/rate")
def api_exam_rate(req: RateReq):
    """记录用户在题目界面选择的评分 (仅记录; FSRS 在统一阅卷后条件应用:
    判对→按所选档计入, 判错/未答→按 Again 计入)。"""
    if req.rating not in (1, 2, 3, 4):
        raise HTTPException(400, "rating 必须为 1-4")
    try:
        exam.set_rate(req.session_id, req.card_id, req.rating)
    except LookupError:
        raise HTTPException(404, "未找到本题作答记录")
    return {"ok": True}


@router.get("/api/exam/results")
def api_exam_results(session_id: int):
    """考试结束后的逐题详情: 正确答案/ASR 文字/录音回放/TTS/判分/FSRS 结果。"""
    try:
        return exam.results(session_id)
    except LookupError:
        raise HTTPException(404, "会话不存在")


@router.get("/audio/reply/{fname}")
def api_audio_reply(fname: str):
    if not re.fullmatch(r"[\w.-]{1,80}\.(webm|ogg|wav|mp3|m4a|mp4)", fname):
        raise HTTPException(400, "非法文件名")
    path = REPLIES_DIR / fname
    if not path.is_file():
        raise HTTPException(404, "录音不存在")
    mime = {".webm": "audio/webm", ".ogg": "audio/ogg", ".wav": "audio/wav",
            ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "video/mp4"}
    return FileResponse(str(path), media_type=mime.get(path.suffix.lower(),
                                                       "application/octet-stream"))
