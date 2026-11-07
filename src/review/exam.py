# -*- coding: utf-8 -*-
"""exam — 复习考试后台编排: 出题准备 → 内存会话 → 录音转写 → AI 统一阅卷 → FSRS 计入

HTTP 路由在 webapp/routes_review.py, 本模块承载全部后台工作。
会话态 (题目/作答/判分/评分) 全部驻留内存, 不落库 —— 持久事实只有 FSRS 调度
(cards/review_logs) 与出题队列 (article_question); 服务重启会话即失效, 重考即可。
  - prepare 状态机: 空闲时起后台线程跑 claude design-exam 出题 (used=0 入新卡池),
    前端轮询 prepare_status() 取进度 (claude 调用可耗时数分钟);
  - 作答阶段: 收录音落盘后 submit_answer() 入池并行 ASR 转写
    (与用户答下一题并行, 只转写不判分);
  - 统一阅卷: finish 后 start_grading() 按 3 题一块建临时表, 并发调 claude
    judge-answer 判分, 每块完成即写 verdict 并条件应用 FSRS (增量进度可见):
    新题此刻首评建卡 (短事务, cards.id=article_question.id), 老卡直接调度;
    finish 时末题 ASR 可能未完成 → 多轮重试吸收迟到的转写结果。
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.config import ARTICLE_DIR
from core.db import DB, now_str
from core.log import log
from speech import asr
from review import claude_cli, fsrs_store

_PREP = {"phase": "idle", "message": "", "result": None}   # design-exam 准备状态
_PREP_LOCK = threading.Lock()      # prepare 的 check-then-act 原子化 (防双 claude 并发)
_ANSWER_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="exam")
_GRADING_POOL = ThreadPoolExecutor(max_workers=5, thread_name_prefix="grade")

# 内存会话注册表: sid -> {"id", "created_at", "finished_at", "items": [...]}
# item: {card_id, question, answer, is_new, submitted, audio, reply, verdict,
#        judged_at, rated, fsrs_rating, fsrs_applied}
_SESSIONS = {}
_SESS_LOCK = threading.Lock()      # 会话注册表与 item 变更的全局锁 (单用户, 粒度足够)
_SID = [0]


def _next_sid() -> int:
    with _SESS_LOCK:
        _SID[0] += 1
        return _SID[0]


def _find_item(sid: int, card_id: int):
    """取会话中的一题 (调用方需持有 _SESS_LOCK; 会话/题目不存在返回 None)。"""
    s = _SESSIONS.get(sid)
    if s is None:
        return None
    return next((x for x in s["items"] if x["card_id"] == card_id), None)


# ---------------------------------------------------------------------------
# 出题准备 (claude design-exam): 状态机
# ---------------------------------------------------------------------------
def start_prepare() -> dict:
    """触发准备: 若空闲则起后台任务 (design-exam 可耗时数分钟), 立即返回状态。"""
    with _PREP_LOCK:
        if _PREP["phase"] in ("idle", "error", "ready"):
            _PREP.update(phase="analyzing", message="claude code 分析新文章中...",
                         result=None)
            threading.Thread(target=_prepare_impl, daemon=True).start()
    return {"phase": _PREP["phase"], "message": _PREP["message"]}


def prepare_status() -> dict:
    """轮询准备进度: {phase: idle/analyzing/ready/error, message, result}"""
    return {"phase": _PREP["phase"], "message": _PREP["message"],
            "result": _PREP["result"]}


def _has_new_articles() -> bool:
    """出题表是否覆盖了 resource/article 下所有文章 (design-exam 按文件名去重)。"""
    names = sorted(p.name for p in ARTICLE_DIR.iterdir()
                   if p.suffix in (".md", ".txt") and p.is_file())
    if not names:
        return False
    rows = {r["article"] for r in DB.query("SELECT DISTINCT article FROM article_question")}
    return any(n not in rows for n in names)


def _prepare_impl():
    """后台线程: claude design-exam 出题 (直接落 article_question, used=0 即入池)。"""
    if not _has_new_articles():
        _PREP.update(phase="analyzing", message="无新文章, 跳过分析")
    else:
        try:
            claude_cli.design_exam()
        except Exception as e:
            _PREP.update(phase="error", message=str(e), result=None)
            return
    row = DB.queryone("SELECT COUNT(*) AS n FROM article_question WHERE used=0")
    _PREP.update(phase="ready", message="", result={"pool": row["n"] if row else 0})


# ---------------------------------------------------------------------------
# 内存会话: 组卷 / 作答 / 进度 / 结果
# ---------------------------------------------------------------------------
def start_session(limit: int | None = None) -> dict:
    """组卷并登记会话。返回 {session_id, total, questions} (题目不带答案)。"""
    built = fsrs_store.build_session(limit)
    if not built["total"]:
        return {"session_id": None, "total": 0, "questions": []}
    sid = _next_sid()
    items = [{"card_id": it["card_id"], "question": it["question"],
              "answer": it["answer"], "is_new": it["is_new"],
              "submitted": False, "audio": None, "reply": None,
              "verdict": None, "judged_at": None, "rated": None,
              "fsrs_rating": None, "fsrs_applied": False} for it in built["items"]]
    questions = [{"card_id": it["card_id"], "question": it["question"],
                  "preview": fsrs_store.preview(it["card_id"])} for it in items]
    with _SESS_LOCK:
        _SESSIONS[sid] = {"id": sid, "created_at": now_str(),
                          "finished_at": None, "items": items}
    return {"session_id": sid, "total": len(items), "questions": questions}


def get_session(sid: int) -> dict:
    s = _SESSIONS.get(sid)
    if s is None:
        raise LookupError("会话不存在")
    return s


def has_item(sid: int, card_id: int) -> bool:
    with _SESS_LOCK:
        return _find_item(sid, card_id) is not None


def submit_answer(sid: int, card_id: int, audio_path=None):
    """登记作答 (幂等防重: 重复提交抛 ValueError → 路由转 409);
    带录音则立即丢进 ASR 池并行转写。"""
    with _SESS_LOCK:
        it = _find_item(sid, card_id)
        if it is None:
            raise LookupError("题目不属于本会话")
        if get_session(sid)["finished_at"] is not None:
            raise ValueError("会话已收卷")
        if it["submitted"]:
            raise ValueError("本题已作答")
        it["submitted"] = True
        it["audio"] = audio_path.name if audio_path else None
    if audio_path:
        _ANSWER_POOL.submit(_asr_worker, sid, card_id, audio_path)


def _asr_worker(sid: int, card_id: int, audio_path):
    """作答阶段只做 ASR 转写; 失败直接写错误结论 (跳过 AI 阅卷)。"""
    def on_fail(msg):
        def mut(it):
            it["reply"] = ""
            it["verdict"] = {"correct": False, "error": msg}
            it["judged_at"] = now_str()
        return mut
    try:
        text = asr.transcribe(audio_path)
    except Exception as e:
        log("ASR 失败:", repr(e))
        _mutate(sid, card_id, on_fail(f"语音识别失败: {e}"))
        return

    def ok(it):
        it["reply"] = text
    _mutate(sid, card_id, ok)


def _mutate(sid: int, card_id: int, mut):
    """带锁修改单题 (mut(item)); 会话已消失 (重启) 时静默忽略残留线程。"""
    with _SESS_LOCK:
        it = _find_item(sid, card_id)
        if it is not None:
            mut(it)


def set_rate(sid: int, card_id: int, rating: int):
    """记录用户现场评分 (仅记录; FSRS 在统一阅卷后条件应用)。"""
    with _SESS_LOCK:
        it = _find_item(sid, card_id)
        if it is None:
            raise LookupError("未找到本题作答记录")
        it["rated"] = rating


def finish_session(sid: int) -> bool:
    """收卷 (单向, 幂等)。返回是否本次调用完成首次收卷 —— 仅首次时调用方应起阅卷,
    防止 finish 重复触发 (双击/网络重试) 造成双 worker 互踩临时表与重复调度。"""
    with _SESS_LOCK:
        s = get_session(sid)
        if s["finished_at"] is None:
            s["finished_at"] = now_str()
            return True
        return False


def session_state(sid: int) -> dict:
    """会话进度: 各题作答/判分/评分状态 (前端轮询并行分析进度用)。"""
    with _SESS_LOCK:
        s = get_session(sid)
        items = [{"card_id": it["card_id"],
                  "answered": it["reply"] is not None,
                  "judged": it["verdict"] is not None,
                  "rated": it["rated"]} for it in s["items"]]
        finished_at = s["finished_at"]
    return {"session_id": sid, "total": len(items), "finished_at": finished_at,
            "answered": sum(1 for i in items if i["answered"]),
            "judged": sum(1 for i in items if i["judged"]),
            "rated": sum(1 for i in items if i["rated"]),
            "items": items}


def results(sid: int) -> dict:
    """考试结束后的逐题详情: 题目/正确答案/ASR 文字/录音/判分/FSRS 应用结果。
    仅内存态, 服务重启后不可查 (设计取舍)。"""
    with _SESS_LOCK:
        s = get_session(sid)
        snap = [dict(it) for it in s["items"]]
        finished_at = s["finished_at"]
    out = []
    for it in snap:
        row = DB.queryone("SELECT card_json FROM cards WHERE id=?", (it["card_id"],))
        card_info = None
        if row:
            cj = json.loads(row["card_json"])
            card_info = {"state": cj.get("state"), "stability": cj.get("stability"),
                         "difficulty": cj.get("difficulty"), "due": cj.get("due")}
        out.append({
            "card_id": it["card_id"], "question": it["question"],
            "answer": it["answer"], "reply": it["reply"],
            "audio": f"/audio/reply/{it['audio']}" if it["audio"] else None,
            "verdict": it["verdict"], "rated": it["rated"],
            "fsrs_rating": it["fsrs_rating"], "fsrs_applied": it["fsrs_applied"],
            "answered": it["reply"] is not None, "card": card_info,
        })
    return {"session_id": sid, "total": len(out), "finished_at": finished_at,
            "items": out}


# ---------------------------------------------------------------------------
# 统一阅卷: 临时表分块 → 并发 claude 判分 → 写 verdict → 条件应用 FSRS
# ---------------------------------------------------------------------------
def _grade_chunk(table: str, rows: list) -> dict:
    """单块阅卷: 建临时表 → claude 按 judge-answer skill 判分回写 → 读回。{card_id: correct}"""
    DB.exec(f"DROP TABLE IF EXISTS {table}")
    DB.exec(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, question TEXT, "
            "answer TEXT, reply TEXT, correct BOOLEAN DEFAULT FALSE)")
    with DB.txn() as conn:
        conn.executemany(
            f"INSERT INTO {table} (id, question, answer, reply) VALUES (?,?,?,?)",
            [(r["id"], r["question"], r["answer"], r["reply"]) for r in rows])
    try:
        claude_cli.grade_table(table)
        graded = {r["id"]: r["correct"] for r in DB.query(f"SELECT id, correct FROM {table}")}
    finally:
        try:
            DB.exec(f"DROP TABLE IF EXISTS {table}")
        except Exception:
            pass
    out = {}
    for r in rows:
        c = graded.get(r["id"], 0)                     # claude 漏判的行兜底为错误
        out[r["id"]] = bool(c) if isinstance(c, (int, bool)) else \
            str(c).strip().lower() in ("true", "1", "yes")
    return out


def _mark_verdict(sid: int, card_id: int, verdict: dict):
    def mut(it):
        it["verdict"] = verdict
        it["judged_at"] = now_str()
    _mutate(sid, card_id, mut)


def _apply_fsrs_one(sid: int, card_id: int):
    """阅卷后条件应用 FSRS: 判对→用户所选档 (未选默认 Good), 判错→统一 Again;
    跳过 (未录音) 与 ASR 失败的题不进阅卷、不计入调度 (卡片留在原 due/新卡池)。
    新题在此刻首评建卡 (短事务), 老卡原地调度。"""
    with _SESS_LOCK:
        it = _find_item(sid, card_id)
        snap = dict(it) if it else None
    if snap is None:
        return
    correct = bool((snap["verdict"] or {}).get("correct", False))
    rated = snap["rated"]
    rating = (rated if rated in (1, 2, 3, 4) else 3) if correct else 1
    try:
        if snap["is_new"]:
            r = fsrs_store.apply_first_review(card_id, rating)
        else:
            r = fsrs_store.apply_result(card_id, rating)

        def applied(x):
            x["fsrs_rating"] = rating
            x["fsrs_applied"] = True
        _mutate(sid, card_id, applied)
        log(f"FSRS 计入: card#{card_id} rating={rating} 下次间隔 {r['interval']}")
    except Exception as e:
        log("FSRS 应用失败:", repr(e))


def _grading_worker(sid: int):
    """统一阅卷: 待判题按 3 题一块建多个临时表, 5 路并发 claude 判分; 每块完成即写
    verdict 并条件应用 FSRS (增量进度可见)。finish 时最后一题的 ASR 可能尚未完成 →
    多轮重试吸收迟到的转写结果。"""
    total = 0
    try:
        for attempt in range(5):
            with _SESS_LOCK:
                s = _SESSIONS.get(sid)
                items = list(s["items"]) if s else []
                rows = [{"id": it["card_id"], "question": it["question"],
                         "answer": it["answer"], "reply": it["reply"]}
                        for it in items
                        if it["submitted"] and it["reply"] is not None
                        and it["verdict"] is None]
                # 已提交且带录音但转写未回的题: ASR 仍在途, 不能收摊 (否则永久漏判)
                in_flight = any(it["submitted"] and it["reply"] is None
                                and it["verdict"] is None and it["audio"]
                                for it in items)
            if not rows:
                if not in_flight or attempt >= 4:
                    break
                time.sleep(2)              # 等迟到转写, 进入下一轮
                continue
            if attempt:
                log(f"阅卷重试 round{attempt + 1}: session#{sid} {len(rows)} 题 (吸收迟到转写)")
            chunks = [rows[i:i + 3] for i in range(0, len(rows), 3)]
            futs = {_GRADING_POOL.submit(
                _grade_chunk, f"grading_{sid}_{attempt}_{i}", ch): ch
                for i, ch in enumerate(chunks)}
            for fut in as_completed(futs):
                ch = futs[fut]
                try:
                    verdicts = fut.result()
                except Exception as e:
                    log("阅卷块失败:", repr(e))
                    for r in ch:
                        _mark_verdict(sid, r["id"], {"error": f"AI 阅卷失败: {e}"})
                    continue
                for r in ch:
                    _mark_verdict(sid, r["id"], {"correct": verdicts[r["id"]]})
                    _apply_fsrs_one(sid, r["id"])
                total += len(ch)
        if total:
            log(f"阅卷完成: session#{sid} 共 {total} 题")
    except Exception as e:
        log("统一阅卷失败:", repr(e))
        with _SESS_LOCK:
            s = _SESSIONS.get(sid)
            if s:
                for it in s["items"]:
                    if it["submitted"] and it["reply"] is not None \
                            and it["verdict"] is None:
                        it["verdict"] = {"correct": False,
                                         "error": f"AI 阅卷失败: {e}"}
                        it["judged_at"] = now_str()


_GRADING = set()                   # 在途阅卷会话 (防 finish 重试起双 worker 互踩)


def start_grading(sid: int):
    """finish 后起后台统一阅卷线程 (结果页轮询 /api/exam/state 等 verdict)。
    同会话在途时忽略重复触发。"""
    with _SESS_LOCK:
        if sid in _GRADING:
            return
        _GRADING.add(sid)

    def run():
        try:
            _grading_worker(sid)
        finally:
            with _SESS_LOCK:
                _GRADING.discard(sid)

    threading.Thread(target=run, daemon=True).start()
