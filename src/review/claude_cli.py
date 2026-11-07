# -*- coding: utf-8 -*-
"""claude_cli — 无头调用 claude code: design-exam 出题 (长任务) / judge-answer 判分 (短任务)

均为 cwd=项目根 的本地固定指令, 无交互能力, 故用 bypassPermissions 免去权限挂起;
调用成本约 $0.1/次 (glm 网关)。judge-answer 输出单一 JSON, 解析失败抛 RuntimeError。
Popen + start_new_session: 超时 killpg 连 claude 派生的工具孙进程一并回收,
避免孤儿进程继续写出题库 / 管道写端继承导致误报超时。
调用方: review/exam.py。
"""
import json
import os
import signal
import subprocess

from core.config import DESIGN_TIMEOUT, GRADING_TIMEOUT, PROJECT_DIR


def _run(prompt: str, timeout: int) -> str:
    """claude -p 无头运行, 返回 result 文本。超时/报错抛 RuntimeError。"""
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--permission-mode", "bypassPermissions"]
    p = subprocess.Popen(cmd, cwd=str(PROJECT_DIR), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)   # 整棵进程树
        except (ProcessLookupError, PermissionError):
            p.kill()
        p.wait(timeout=10)
        raise RuntimeError(f"claude 调用超时(>{timeout}s)")
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        tail = (err or out or "")[:300]
        raise RuntimeError(f"claude 输出解析失败: {tail}")
    if data.get("is_error"):
        raise RuntimeError(f"claude 调用出错: {str(data.get('result', ''))[:300]}")
    return data.get("result", "")


def design_exam() -> str:
    """触发 design-exam skill 分析 resource/article 新文章并出题入库 (resource/practice.db)。"""
    return _run("请使用 design-exam skill 分析新写的文章 "
                "(resource/article 目录), 把新文章的题目写入 resource/practice.db "
                "的 article_question 表",
                DESIGN_TIMEOUT)


def grade_table(table_name: str) -> str:
    """统一阅卷: 让 claude 按 judge-answer skill 读 grading 表逐题判分并回写 correct 列。
    表由后端预建 (id/question/answer/reply/correct), 判分结果直接落表, 无需解析输出。"""
    return _run(f"使用 judge-answer skill 阅卷。数据库: resource/practice.db, "
                f"表名: {table_name}。读取表中所有数据, 逐题判分并更新 correct 列。",
                GRADING_TIMEOUT)
