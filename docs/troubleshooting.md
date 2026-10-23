# 排错手册

先看日志，90% 的问题能直接定位：

- `logs/hub.log` —— 运行日志（滚动保留 5 份）
- `logs/console.out` —— 后台服务（无窗口模式）的控制台输出

---

## 连不上数据源

现象：

```
✗ 连不上数据源（后端：ChatlogClient）
  1) SQLite 直读：在 .env 里设置 WX_DECRYPTED_DIR=你的解密目录
  2) chatlog HTTP：http://127.0.0.1:5030（已停更，仅兼容保留）
  3) 只想先看效果：python tools/demo_seed.py
```

按顺序检查：

1. `.env` 里 `WX_DECRYPTED_DIR` 是否填了、路径是否存在
2. 该目录下是否有可读的数据库文件（`*.db`）
3. 走 HTTP 模式时，`http://127.0.0.1:5030/health` 是否返回正常
4. 数据源是否真的产出了数据（有些工具只在导出时才写库）

想先排除程序本身的问题：`python tools/demo_seed.py --sample-summary`，能出报告就说明代码没问题，是数据源的事。

---

## 群列表是空的

- 数据源里确实没有群（新账号 / 没同步历史）
- 群名匹配不到：`--group` 的值必须和列表里显示的一模一样，包括空格和 emoji
- 先跑 `python main.py --list-groups` 看看真实名字

---

## 语音没转写 / 图片没描述

这两种都是**可降级**的：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 语音显示 `[语音]` | 没装 `faster-whisper` | `pip install -e ".[asr]"` |
| 语音显示 `[语音]` 但装了依赖 | 原始流是 SILK，转码失败 | 装 `ffmpeg`，或配置 `asr.silk_decoder` |
| 图片显示 `[图片]` | `vision.provider` 是 `dummy` | 改成 `doubao` 并配置密钥（会产生费用） |
| 长语音被跳过 | 超过 `asr.max_duration`（默认 300 秒） | 调大该值 |

降级不影响文字部分，报告照样能出。

---

## 公众号文章抓不到正文

会降级成「链接 + 标题」，报告里能看到原链接。常见原因：

- 文章被删 / 需要登录 / 有反爬
- 请求超时：调大 `article.timeout` 或 `article.retry`

---

## 报告里缺某天的记录

先确认数据源里有没有那天的数据。SQLite 直读看的是快照，微信没缓存的消息导不出来。有些情况需要先在微信里翻到那天的聊天，再刷新数据源。

---

## 端口 5710 被占用

```bash
netstat -ano | findstr ":5710"
```

- 如果占用者是本程序：双击 `停止服务.bat`
- 如果被别的程序占了：改 `config/settings.yaml` 里的 `web.port`

---

## 「生成报告」点了没反应 / 一直转圈

- 同时点了两次：第二次会返回 409，前端会接上已在运行的那个任务，不会重复跑
- 超过 15 分钟：任务会被标记为软超时，前端停止转圈。检查 `logs/hub.log` 里卡在哪一步
- 常见卡点是大模型接口超时：调大 `summarizer` 相关超时，或先切回 `file_output` 验证链路

---

## 后台服务起不来

1. `logs/console.out` 里有没有 traceback
2. `python main.py --serve` 前台跑一次，能看到完整报错
3. 检查 `python` 是否在 PATH：`where python`
4. 无窗口运行依赖 `pythonw.exe`，它和 `python.exe` 在同一个目录

---

## 关掉黑窗口，服务就停了吗？

不会。服务是用 `pythonw.exe` 独立起的进程，窗口只是启动器。

确认服务还活着：浏览器打开 `http://127.0.0.1:5710`，或看 `logs/console.out` 最后几行。

要停：`停止服务.bat`。

---

## 报告里出现两个「总结」标题

已在渲染层修掉（总结正文自带的标题会被剥离）。如果又出现，说明改到了 `output/markdown.py` 或 `output/html.py` 的标题处理逻辑，跑一下 `python tests/test_regression.py` 确认。

---

## 完全跑不动，想确认代码本身有没有坏

```bash
python -m compileall -q .
python tests/test_regression.py
python tools/demo_seed.py --sample-summary
```

三条都过，说明代码是好的，问题在配置或数据源。
