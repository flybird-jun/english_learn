# -*- coding: utf-8 -*-
"""db — SQLite 持久化: 单库单文件 (resource/practice.db) 收纳全部业务表

表 (5 张):
  article_question  出题队列 (design-exam skill 直接写); used=0 即"新卡池"
  cards             FSRS 调度状态, article_question 的 1:1 扩展 (首评建卡时诞生);
                    card_json = py-fsrs Card.to_dict() 原样落盘, from_dict 无损往返
  review_logs       复习流水 (只追加); log_json = scheduler.review_card() 返回的
                    ReviewLog.to_dict() 原样落盘
  notes             笔记 (同文章同文本唯一, 前端按文本搜索定位)
  words_dict        单词词典缓存 (划词先查这里, 未命中才联网)

对外暴露线程安全的单例 DB (exec/query/queryone/txn) 与 now_str()。
card_json.due / review_datetime 为 UTC ISO 微秒串, 等宽同格式可字典序比较
(json_extract 后比较仍成立); notes.created_at 等展示类时间为本地 naive now_str()。
"""
import sqlite3
import threading
from datetime import datetime

from core.config import DB_PATH


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path: str = str(DB_PATH)):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            # foreign_keys 必须在无未提交事务时设置才生效 (PRAGMA 在事务内是 no-op),
            # 故紧跟 connect, 先于任何 DDL
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS article_question (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                article  TEXT NOT NULL,
                question TEXT NOT NULL,
                answer   TEXT NOT NULL,
                used     INTEGER NOT NULL DEFAULT 0,
                UNIQUE(article, question)
            );
            -- FSRS 调度状态: id 即 article_question.id (首评建卡时诞生, 1:1)
            CREATE TABLE IF NOT EXISTS cards (
                id        INTEGER PRIMARY KEY REFERENCES article_question(id),
                card_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_logs (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                log_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id  TEXT NOT NULL,
                text        TEXT NOT NULL,
                note        TEXT NOT NULL DEFAULT '',
                translation TEXT,
                phonetic    TEXT,
                created_at  TEXT NOT NULL,
                UNIQUE(article_id, text)
            );
            CREATE TABLE IF NOT EXISTS words_dict (
                word     TEXT PRIMARY KEY,   -- 小写归一化
                phonetic TEXT,
                senses   TEXT                -- 词义 JSON 串 (词性前缀+词义)
            );
            """)
            self.conn.commit()

    def exec(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def query(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def queryone(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def txn(self):
        """显式事务上下文: 多条语句一个 commit (如建卡+翻 used+插日志必须原子)。"""
        return _Txn(self)


class _Txn:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        return self.db.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.db.conn.commit()
            else:
                self.db.conn.rollback()
        finally:
            self.db.lock.release()
        return False


DB = Database()
