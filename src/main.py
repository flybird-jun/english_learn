#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — 智学引擎 (AI 英语学习平台) 启动入口

用法:
  .venv/bin/python src/main.py [--host 127.0.0.1] [--port 7788] [--no-browser]
  依赖清单见 requirements.txt; 未安装时 Python 会直接抛 ImportError, 请先:
  pip install -r requirements.txt

模块结构 (src/, 均为无 __init__.py 的命名空间包):
  core/    基础设施: 配置常量 / SQLite 持久化 / 日志
  content/ 学习内容: 文章解析分词 / 划词翻译与词典
  speech/  语音: edge-tts 朗读 / faster-whisper 语音识别
  review/  复习考试: FSRS-6 调度 / 考试后台编排 / claude 无头出题与阅卷
  webapp/  Web 服务: FastAPI 路由 (按页面拆分) + 静态前端 static/

页面: / 首页仪表盘 · /study 研读台 (划词翻译/笔记/朗读) · /review 复习考试 (FSRS)
"""
from webapp.app import main

if __name__ == "__main__":
    main()
