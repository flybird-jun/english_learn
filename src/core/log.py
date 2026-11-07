# -*- coding: utf-8 -*-
"""log — 通用日志: 带时间戳的 stdout 打印, 服务端各模块共用 (无文件落盘)"""
from datetime import datetime


def log(*a):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]", *a, flush=True)
