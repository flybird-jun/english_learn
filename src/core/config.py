# -*- coding: utf-8 -*-
"""config — 全局配置: 目录路径与业务常量

所有资源路径以仓库根 (PROJECT_DIR) 为锚点:
  resource/article/      英语文章 (design-exam 出题源)
  resource/audio/        音频缓存根 (words/ 划词 + full/ 全文 + replies/ 考试录音)
  resource/practice.db   SQLite 主库 (翻译缓存/笔记/FSRS/考试, 表结构见 core/db.py)
  resource/.models/      本地模型缓存 (faster-whisper)
其余常量: TTS 默认音色 / 各接口文本上限 / claude 调用超时 / FSRS 默认保持率。
"""
from pathlib import Path

# 本文件位于 src/core/, 向上两级到仓库根 (resource/ 所在目录)
PROJECT_DIR = Path(__file__).resolve().parents[2]
RESOURCE_DIR = PROJECT_DIR / "resource"
ARTICLE_DIR = RESOURCE_DIR / "article"
AUDIO_DIR = RESOURCE_DIR / "audio"           # 音频缓存根目录 (words/ + full/)
WORDS_DIR = AUDIO_DIR / "words"              # 划词/划句音频缓存
FULL_DIR = AUDIO_DIR / "full"                # 全文音频缓存 (mp3 + 各段起始秒 json)
DB_PATH = RESOURCE_DIR / "practice.db"

# 前端静态页: src/webapp/static/
WEB_DIR = Path(__file__).resolve().parents[1] / "webapp" / "static"

DEFAULT_VOICE = "en-US-EmmaNeural"
FALLBACK_VOICES = [
    "en-US-EmmaNeural", "en-US-AriaNeural", "en-US-GuyNeural",
    "en-US-ChristopherNeural", "en-GB-SoniaNeural", "en-AU-NatashaNeural",
]

TRANSLATE_MAX_CHARS = 2000     # 划选翻译文本上限
SAY_MAX_CHARS = 500            # 划词/划句朗读文本上限
NOTES_MAX_LIMIT = 1000         # /api/notes limit 夹取上限

REPLIES_DIR = AUDIO_DIR / "replies"                  # 考试录音存放 (按会话分子目录)
MODELS_DIR = RESOURCE_DIR / ".models"                # ASR 等本地模型缓存

EXAM_MAX_CARDS = 20            # 单场考试题量上限
DESIGN_TIMEOUT = 2400          # design-exam (claude 出题) 超时: 新文章多+模型自开校验耗时长
GRADING_TIMEOUT = 600          # judge-answer (claude 统一阅卷) 超时: 整场题一次判完
ASR_MAX_SECONDS = 120          # 录音时长上限 (防超长上传)
DEFAULT_RETENTION = 0.9        # FSRS 期望记忆保持率 (fsrs_store 构造 Scheduler 用)
