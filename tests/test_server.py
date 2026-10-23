"""控制台集成校验：接口可用性 + 安全边界 + 长连接复用。

前置：先 `python main.py --serve` 起服务，再跑本文件。

    python tests/test_server.py                          # 默认 127.0.0.1:5710
    CHAT_DIGEST_URL=http://127.0.0.1:5800 python tests/test_server.py
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import urllib.request
from urllib.error import HTTPError
from urllib.parse import quote

BASE = os.environ.get("CHAT_DIGEST_URL", "http://127.0.0.1:5710").rstrip("/")
PROXY_OFF = urllib.request.ProxyHandler({})  # 本地服务不该走系统代理
_opener = urllib.request.build_opener(PROXY_OFF)

ok_count = 0
fail_count = 0


def _count_time_wait() -> int:
    """统计目标端口上的 TIME_WAIT 连接数。

    每新建一条 TCP 连接，断开后都会留下一条 TIME_WAIT，若服务真的复用了连接，
    这个数字应当保持稳定，而不是随请求量线性增长。
    """
    port = ":" + BASE.rsplit(":", 1)[-1]
    try:
        # Windows 的 netstat 输出是 GBK，用 replace 容错以免遇到中文列标题崩掉
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        ).stdout or ""
    except Exception:  # noqa: BLE001
        return -1
    return sum(1 for line in out.splitlines() if port in line and "TIME_WAIT" in line)


def check(label: str, condition: bool, detail: str = "") -> None:
    global ok_count, fail_count
    if condition:
        ok_count += 1
        print(f"  ✓ {label}")
    else:
        fail_count += 1
        print(f"  ✗ {label}  {detail}")


def get(path: str):
    with _opener.open(BASE + path, timeout=10) as resp:
        return resp.status, resp.read()


def request(method: str, path: str, data: bytes = None, headers: dict = None):
    req = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with _opener.open(req, timeout=10) as resp:
        return resp.status, resp.read()


def maybe(method, path, data=None, headers=None):
    """能拿 HTTPError 就返回它，不抛。"""
    try:
        return request(method, path, data, headers)
    except HTTPError as exc:
        return exc.code, exc.read()


# 服务没起来时优雅跳过：既方便本地忘记起服务，也能直接进 CI 跑而不红。
try:
    get("/api/status")
except Exception as exc:  # noqa: BLE001
    print(f"\n[i] 服务未运行（{BASE}），跳过集成校验。")
    print(f"    原因：{exc!r}")
    print("    先执行 `python main.py --serve`，再跑本文件。\n")
    sys.exit(0)

print("\n[1] 基础接口")
for path, want_key in [
    ("/", None),
    ("/api/status", "chatlog_online"),
    ("/api/groups", "groups"),
    ("/api/runs", "runs"),
    ("/api/task", None),
    ("/assets/style.css", None),
    ("/assets/app.js", None),
]:
    try:
        status, body = get(path)
        payload = json.loads(body) if body[:1] in (b"{", b"[") else None
        good = status == 200 and (want_key is None or (payload and want_key in payload))
        check(f"GET {path} -> {status}", good, f"缺字段 {want_key}" if not good else "")
    except Exception as exc:  # noqa: BLE001
        check(f"GET {path}", False, str(exc)[:80])

print("\n[2] 安全边界 —— 路径穿越必须拦掉")
for bad in [
    "/reports/..%2f..%2f..%2fconfig%2fsettings.yaml",
    "/reports/../../../chat-digest/config/groups.yaml",
    "/assets/../server.py",
    "/reports/nonexistent.html",
]:
    try:
        status, _ = get(bad)
        check(f"拒绝 {bad[:44]}", status == 404, f"却返回了 {status}")
    except HTTPError as exc:
        check(f"拒绝 {bad[:44]}", exc.code == 404, f"却返回了 {exc.code}")

print("\n[3] 畸形请求不能打挂服务")
status, _ = maybe("POST", "/api/run", b"this-is-not-json", {"Content-Type": "application/json"})
check("非法 JSON 体 -> 400/409 而非 500", status in (400, 409), f"得到 {status}")
status, body = maybe("DELETE", "/api/report?md=" + quote("不存在的文件.md"))
check("删不存在的文件 -> 404", status == 404, f"得到 {status}")
try:
    get("/api/status")  # 服务还活着
    check("畸形请求后服务仍存活", True)
except Exception as exc:  # noqa: BLE001
    check("畸形请求后服务仍存活", False, str(exc)[:60])

print("\n[4] 并发保护：重复点「生成」应被拒绝而不是并行开跑")
first = maybe("POST", "/api/run", json.dumps(
    {"group": "并发保护验证群", "date": "today", "format": "markdown"}
).encode(), {"Content-Type": "application/json"})
check("首个任务可启动", first[0] in (200, 409), f"得到 {first[0]}")
second = maybe("POST", "/api/run", json.dumps(
    {"group": "并发保护验证群", "date": "today", "format": "markdown"}
).encode(), {"Content-Type": "application/json"})
code, payload = second[0], json.loads(second[1] or b"{}")
check("重复触发返回 409 忙", code == 409 and payload.get("busy") is True,
      f"code={code} body={str(payload)[:80]}")

print("\n[5] 长连接复用：一条 TCP 连接应该能承载连续多次请求")
before_tw = _count_time_wait()
reused_ok = True
used_sockets = set()
try:
    host = BASE.split("://", 1)[-1].rsplit(":", 1)[0]
    conn = http.client.HTTPConnection(host, int(BASE.rsplit(":", 1)[-1]), timeout=10)
    for i in range(25):
        conn.request("GET", "/api/status")
        resp = conn.getresponse()
        used_sockets.add(id(conn.sock))
        body = resp.read()
        if resp.status != 200 or not body:
            reused_ok = False
            break
    conn.close()
except Exception as exc:  # noqa: BLE001
    reused_ok = False
    print(f"     异常：{exc!r}")

check("25 次请求复用同一条连接", reused_ok and len(used_sockets) == 1,
      f"用到 {len(used_sockets)} 条 socket")

time.sleep(1.0)
after_tw = _count_time_wait()
check("TIME_WAIT 未随请求增长", after_tw - before_tw <= 2,
      f"before={before_tw} after={after_tw}（差 {after_tw - before_tw}）")

print(f"\n{'=' * 46}\n 通过 {ok_count} 项，失败 {fail_count} 项\n{'=' * 46}\n")
sys.exit(1 if fail_count else 0)
