# -*- coding: utf-8 -*-
"""routes_home — 首页仪表盘路由: 学习进度 / 趋势 / 待办列表 / FSRS 参数

数据层在 review/fsrs_store.py (stats/trend/pending_lists/params_view)。
"""
from fastapi import APIRouter

from review import fsrs_store

router = APIRouter()


@router.get("/api/stats")
def api_stats():
    """首页仪表盘: 进度/趋势/待办/FSRS 参数 (全部真实数据)。"""
    return {"stats": fsrs_store.stats(),
            "trend7": fsrs_store.trend(7),
            "trend30": fsrs_store.trend(30),
            "pending": fsrs_store.pending_lists(),
            "params": fsrs_store.params_view()}
