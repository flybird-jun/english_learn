# -*- coding: utf-8 -*-
"""fsrs_store — FSRS-6 复习调度: 新卡池 → 组卷 → 评分调度 → 统计

设计 (2026-11-09 定稿, 详见仓库根 fsrs_design.md):
  - 参数写死: 权重/步长/保持率是代码常量, 无 fsrs_params 表;
  - 卡片懒建: 新题以 article_question.used=0 为池, 首评判分后 apply_first_review()
    在短事务内建卡 (cards.id = article_question.id) 并翻 used=1;
  - JSON 往返: cards.card_json = py-fsrs Card.to_dict() 原样, from_dict 还原,
    不手工映射任何字段; due 经 json_extract 字典序比较 (UTC ISO 微秒串);
  - 流水: review_logs.log_json = review_card() 返回的 ReviewLog.to_dict() 原样。
调用方: review/exam.py (判分后应用) 与 webapp 路由 (组卷/统计)。
"""
import json
import threading
from datetime import date, datetime, timedelta, timezone

from fsrs import Card, Rating, Scheduler

from core.config import DEFAULT_RETENTION, EXAM_MAX_CARDS
from core.db import DB

# 官方 FSRS-6 默认 21 权重 (py-fsrs 6.3.2 / ts-fsrs v5.4.2 / fsrs-rs v6.6.2 一致)
DEFAULT_WEIGHTS = [0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194,
                   0.001, 1.8722, 0.1666, 0.796, 1.4835, 0.0614, 0.2629,
                   1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542]
DEFAULT_CONFIG = {                  # 分钟单位; fuzz 关闭保证 due 可复现
    "learning_steps": [1, 10], "relearning_steps": [10],
    "maximum_interval": 36500, "enable_fuzzing": False,
}

_sched = None          # Scheduler 缓存 (参数为常量, 进程内构造一次)
_sched_lock = threading.Lock()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def _parse(s):
    return datetime.fromisoformat(s) if s else None


def _count(sql: str, params=()) -> int:
    row = DB.queryone(sql, params)
    return row["n"] if row else 0


def scheduler() -> Scheduler:
    """按代码常量构造 Scheduler (进程内单例)。"""
    global _sched
    if _sched is not None:
        return _sched
    with _sched_lock:
        if _sched is not None:
            return _sched
        ls = tuple(timedelta(minutes=m) for m in DEFAULT_CONFIG["learning_steps"])
        rl = tuple(timedelta(minutes=m) for m in DEFAULT_CONFIG["relearning_steps"])
        _sched = Scheduler(parameters=DEFAULT_WEIGHTS,
                           desired_retention=DEFAULT_RETENTION,
                           learning_steps=ls, relearning_steps=rl,
                           maximum_interval=DEFAULT_CONFIG["maximum_interval"],
                           enable_fuzzing=DEFAULT_CONFIG["enable_fuzzing"])
        return _sched


def params_view() -> dict:
    """调度参数概览 (前端展示用, 来源=代码常量)。"""
    return {"weights": DEFAULT_WEIGHTS, "config": DEFAULT_CONFIG,
            "desired_retention": DEFAULT_RETENTION}


# ---------------------------------------------------------------------------
# 新卡池与到期队列
# ---------------------------------------------------------------------------
_POOL_NOT_IN = ("AND question NOT IN (SELECT q2.question FROM cards c2 "
                "JOIN article_question q2 ON q2.id=c2.id)")


def pool_count() -> int:
    """新卡池题数: used=0 且尚未建卡 (跨文章同题已建卡的行不重复出现)。"""
    return _count(
        f"SELECT COUNT(*) AS n FROM article_question WHERE used=0 {_POOL_NOT_IN}")


def due_cards(limit: int):
    """到期队列: due<=now, 按 due 升序。返回 [{card_id, question, answer}]。"""
    now = utc_iso(utc_now())
    return DB.query(
        "SELECT c.id AS card_id, q.question, q.answer FROM cards c "
        "JOIN article_question q ON q.id=c.id "
        "WHERE json_extract(c.card_json,'$.due')<=? "
        "ORDER BY json_extract(c.card_json,'$.due') LIMIT ?", (now, limit))


def due_count() -> int:
    now = utc_iso(utc_now())
    return _count(
        "SELECT COUNT(*) AS n FROM cards WHERE json_extract(card_json,'$.due')<=?",
        (now,))


# ---------------------------------------------------------------------------
# 评分: 读卡(JSON)→调度→写回(JSON)→插流水, 全程单事务 (原子)
# ---------------------------------------------------------------------------
def _apply(conn, card: Card, card_id: int, rating: int, now: datetime,
           is_new: bool) -> dict:
    """review_card + 落库共用体 (调用方需持有 DB.txn()): 卡片已存在则原地 UPDATE,
    新题首评则建卡 (cards.id=article_question.id) 并翻 used=1; 流水一律追加。"""
    post, log_rec = scheduler().review_card(card, Rating(rating), now)
    if is_new:
        conn.execute("INSERT INTO cards(id, card_json) VALUES (?,?)",
                     (card_id, json.dumps(post.to_dict(), ensure_ascii=False)))
        conn.execute("UPDATE article_question SET used=1 WHERE id=?", (card_id,))
    else:
        conn.execute("UPDATE cards SET card_json=? WHERE id=?",
                     (json.dumps(post.to_dict(), ensure_ascii=False), card_id))
    conn.execute("INSERT INTO review_logs(log_json) VALUES (?)",
                 (json.dumps(log_rec.to_dict(), ensure_ascii=False),))
    return {"state": int(post.state), "stability": post.stability,
            "difficulty": post.difficulty, "due": utc_iso(post.due),
            "interval": _fmt_interval((post.due - now).total_seconds() / 86400)}


def apply_result(card_id: int, rating: int) -> dict:
    """老卡评分调度 (判对→用户所选档, 判错→Again 由调用方折算)。"""
    if rating not in (1, 2, 3, 4):
        raise ValueError("rating 必须为 1-4")
    now = utc_now()
    with DB.txn() as conn:                   # RLock 全程持有: 读改写原子
        row = conn.execute("SELECT card_json FROM cards WHERE id=?", (card_id,)).fetchone()
        if row is None:
            raise LookupError("卡片不存在")
        return _apply(conn, Card.from_dict(json.loads(row["card_json"])),
                      card_id, rating, now, is_new=False)


def apply_first_review(aq_id: int, rating: int) -> dict:
    """新题首评: 短事务内建卡 (cards.id=article_question.id) + 翻 used=1 + 记流水。
    幂等: 已有卡 (重试窗口) 退化为普通调度, 不重复建卡。"""
    if rating not in (1, 2, 3, 4):
        raise ValueError("rating 必须为 1-4")
    now = utc_now()
    with DB.txn() as conn:
        q = conn.execute("SELECT question FROM article_question WHERE id=?",
                         (aq_id,)).fetchone()
        if q is None:
            raise LookupError("题目不存在")
        existing = conn.execute("SELECT card_json FROM cards WHERE id=?",
                                (aq_id,)).fetchone()
        if existing:                         # 卡已存在 (并发/重试): 直接调度
            return _apply(conn, Card.from_dict(json.loads(existing["card_json"])),
                          aq_id, rating, now, is_new=False)
        # card_id 用题目 id 而非 py-fsrs 随机 epoch 值: log_json.card_id 才能关联到卡
        return _apply(conn, Card(card_id=aq_id), aq_id, rating, now, is_new=True)


# ---------------------------------------------------------------------------
# 组卷: due 卡优先 (按到期升序), 新题按出题顺序补足, 总量 ≤ EXAM_MAX_CARDS
# ---------------------------------------------------------------------------
def build_session(limit: int | None = None) -> dict:
    """返回 {total, items}。items 携带答案/是否新题 (内部用),
    前端题目列表由调用方摘取 (card_id/question/preview), 不下发答案。"""
    limit = max(1, min(limit or EXAM_MAX_CARDS, EXAM_MAX_CARDS))
    due = due_cards(limit)
    items = [{"card_id": r["card_id"], "question": r["question"],
              "answer": r["answer"], "is_new": False} for r in due]
    n_new = limit - len(items)
    if n_new > 0:
        fresh = DB.query(
            "SELECT id AS card_id, question, answer FROM article_question "
            f"WHERE used=0 {_POOL_NOT_IN} ORDER BY id LIMIT ?", (n_new,))
        items += [{"card_id": r["card_id"], "question": r["question"],
                   "answer": r["answer"], "is_new": True} for r in fresh]
    return {"total": len(items), "items": items}


def preview(card_id: int) -> dict:
    """四档评分的下次间隔预览 (不落库), 键为字符串评分值便于前端取用。
    未建卡的题按空卡预览 (与首评建卡后的实际调度一致)。"""
    row = DB.queryone("SELECT card_json FROM cards WHERE id=?", (card_id,))
    base = Card.from_dict(json.loads(row["card_json"])) if row else Card()
    now = utc_now()
    out = {}
    for r in (1, 2, 3, 4):
        tmp = Card.from_dict(base.to_dict())
        nc, _ = scheduler().review_card(tmp, Rating(r), now)
        out[str(r)] = _fmt_interval((nc.due - now).total_seconds() / 86400)
    return out


def _fmt_interval(days: float) -> str:
    if days is None or days <= 0:
        return "now"
    if days < 1 / 1440:                 # <1 分钟
        return f"{max(1, round(days * 86400))}s"
    if days < 1:                        # 1 分钟~1 天
        return f"{round(days * 1440)}m"
    if days < 30:
        return f"{round(days, 1)}d"
    return f"{round(days / 30, 1)}mo"


# ---------------------------------------------------------------------------
# 统计 (首页仪表盘)
# ---------------------------------------------------------------------------
def _local_date(utc_s: str) -> str:
    dt = _parse(utc_s)
    return dt.astimezone().strftime("%Y-%m-%d") if dt else ""


def stats() -> dict:
    total = _count("SELECT COUNT(*) AS n FROM article_question")
    mastered = _count("SELECT COUNT(*) AS n FROM cards "
                      "WHERE json_extract(card_json,'$.state')=2")
    today = date.today().strftime("%Y-%m-%d")
    # 连续学习天数 (review_logs 按本地日期, 自今日/昨日向前连续)
    days = sorted({_local_date(r["t"]) for r in DB.query(
        "SELECT json_extract(log_json,'$.review_datetime') AS t FROM review_logs")},
        reverse=True)
    yest = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    streak = 0
    cur = today if today in days else (yest if yest in days else None)
    while cur and cur in days:
        streak += 1
        cur = (datetime.strptime(cur, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    today_reviews = sum(1 for r in DB.query(
        "SELECT json_extract(log_json,'$.review_datetime') AS t FROM review_logs")
        if _local_date(r["t"]) == today)
    return {
        "total": total, "mastered": mastered,
        "fresh": pool_count(), "due": due_count(),
        "progress": round(mastered / total * 100) if total else 0,
        "streak": streak,
        "today": {"reviews": today_reviews},
    }


def trend(days: int = 7) -> list:
    """近 N 天按日复习次数 [{date, count}] (单线), 缺失日补 0。"""
    cutoff = utc_iso(utc_now() - timedelta(days=days))
    rows = DB.query("SELECT json_extract(log_json,'$.review_datetime') AS t "
                    "FROM review_logs WHERE json_extract(log_json,'$.review_datetime')>=?",
                    (cutoff,))
    agg = {}
    for r in rows:
        d = _local_date(r["t"])
        agg[d] = agg.get(d, 0) + 1
    out = []
    for i in range(days - 1, -1, -1):
        d = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"date": d, "count": agg.get(d, 0)})
    return out


def pending_lists() -> dict:
    """首页两个列表: 待学习的知识点(新卡池按文章聚合) / 待复习(due 卡按文章聚合)。"""
    fmt = lambda rows: [{"article": r["article"],
                         "name": r["article"].rsplit(".", 1)[0],
                         "count": r["n"]} for r in rows]
    learn = DB.query(
        "SELECT article, COUNT(*) AS n FROM article_question "
        f"WHERE used=0 {_POOL_NOT_IN} "
        "GROUP BY article ORDER BY n DESC LIMIT 4")
    now = utc_iso(utc_now())
    review = DB.query(
        "SELECT q.article, COUNT(*) AS n FROM cards c "
        "JOIN article_question q ON q.id=c.id "
        "WHERE json_extract(c.card_json,'$.due')<=? "
        "GROUP BY q.article ORDER BY n DESC LIMIT 4", (now,))
    return {"learn": fmt(learn), "review": fmt(review)}
