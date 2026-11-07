# -*- coding: utf-8 -*-
"""app — Web 服务装配与启动入口: FastAPI 实例 + 安全中间件 + 页面壳 + uvicorn

  - 页面: / 首页仪表盘 · /study 研读台 · /review 复习考试 (静态前端在 static/, 数据走 /api/*);
  - API 路由按页面拆分: routes_study / routes_review / routes_home;
  - Host 校验中间件: 仅回环监听时阻断 DNS rebinding; 录音上传体积预检;
  - main(): 目录初始化 / 合成残留清扫 / 自动开浏览器 / uvicorn.run。
"""
import argparse
import shutil
import threading
import webbrowser

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.config import (ARTICLE_DIR, ASR_MAX_SECONDS, AUDIO_DIR, DB_PATH,
                         FULL_DIR, REPLIES_DIR, WEB_DIR, WORDS_DIR)
from core.log import log
from webapp import routes_home, routes_review, routes_study

app = FastAPI(title="English Practice")
SERVER_LOOPBACK = {"host": "127.0.0.1"}     # main() 按 --host 更新; 非回环监听时关闭 Host 校验


class NoCacheStaticFiles(StaticFiles):
    """静态资源禁强缓存: 前端改动即时生效, 避免浏览器拿旧 JS 排查幽灵问题。"""
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


@app.middleware("http")
async def _host_guard(request, call_next):
    """仅本机回环监听时校验 Host 头, 阻断 DNS rebinding 类攻击。"""
    if SERVER_LOOPBACK["host"]:
        host = (request.headers.get("host") or "").strip()
        if host.startswith("["):            # [::1]:7788 形式
            host = host.split("]", 1)[0].lstrip("[")
        else:                               # 127.0.0.1:7788 / localhost / 裸 ::1
            host = host.split(":", 1)[0] or host
        if host not in ("127.0.0.1", "localhost", "::1"):
            return JSONResponse({"detail": "拒绝非本机 Host 请求"}, status_code=403)
    # 录音上传 Content-Length 预检: multipart 解析前拦截超大 body (解析本身会把流读完)
    if request.url.path == "/api/exam/answer":
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > ASR_MAX_SECONDS * 64 * 1024 + 65536:
            return JSONResponse({"detail": "录音过长"}, status_code=413)
    return await call_next(request)


# ---------------------------------------------------------------------------
# 页面壳 ( / 首页 · /study 研读台 · /review 复习考试 ), 数据接口在各 routes_*
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/study", response_class=HTMLResponse)
def study_page():
    return FileResponse(str(WEB_DIR / "study.html"))


@app.get("/review", response_class=HTMLResponse)
def review_page():
    return FileResponse(str(WEB_DIR / "review.html"))


app.include_router(routes_study.router)
app.include_router(routes_review.router)
app.include_router(routes_home.router)
app.mount("/static", NoCacheStaticFiles(directory=str(WEB_DIR)), name="static")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="英语研读台 — 划词翻译 · 笔记 · edge-tts 朗读")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    ap.add_argument("--port", type=int, default=7788, help="端口 (默认 7788)")
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = ap.parse_args()

    SERVER_LOOPBACK["host"] = args.host if args.host in ("127.0.0.1", "localhost", "::1") else ""

    for d in (ARTICLE_DIR, AUDIO_DIR, WORDS_DIR, FULL_DIR, REPLIES_DIR):
        d.mkdir(parents=True, exist_ok=True)
    for d in (AUDIO_DIR, WORDS_DIR, FULL_DIR):   # 清扫异常退出残留的合成临时文件
        for p in d.glob("*.part*"):
            p.unlink(missing_ok=True)

    url = f"http://{args.host}:{args.port}"
    print("=" * 62)
    print("  ⚡ 智学引擎 · AI 英语学习平台")
    print(f"  地址: {url}")
    print(f"  文章目录: {ARTICLE_DIR}")
    print(f"  音频缓存:   {AUDIO_DIR}")
    print(f"  数据库:   {DB_PATH}")
    print("=" * 62)

    if shutil.which("ffmpeg") is None:
        log("提示: 未检测到 ffmpeg (当前方案不需要, 可忽略)")

    if not args.no_browser:
        threading.Timer(1.5, _open_browser, (url,)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def _open_browser(url):
    try:
        webbrowser.open(url)
    except Exception:
        print(f"请手动打开: {url}")
