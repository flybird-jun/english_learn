# -*- coding: utf-8 -*-
"""asr — faster-whisper 语音识别: 录音文件 (webm/opus/wav/...) → 英文文本

模型懒加载单例 (首调约 7s 加载 + 自动从 HF 下载, 已缓存则秒级);
faster-whisper 经 PyAV 自带 ffmpeg 解码, 不依赖系统 ffmpeg,
浏览器 MediaRecorder 产出的 webm/opus 可直接转写。CPU int8, 短音频 1~3s 出文。
对外仅 transcribe(path); 调用方为 review/exam.py 的作答转写 worker。
"""
import threading

from core.config import MODELS_DIR

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from faster_whisper import WhisperModel
                _model = WhisperModel(
                    "base.en", device="cpu", compute_type="int8",
                    download_root=str(MODELS_DIR / "faster-whisper"))
    return _model


def transcribe(path) -> str:
    """转写音频为英文文本。无语音/静音段返回空串; 解码失败抛异常由调用方处理。"""
    m = _get_model()
    segs, _info = m.transcribe(str(path), language="en",
                               condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs).strip()
