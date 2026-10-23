# 使用手册

> 面向使用者的完整说明。只想快速跑起来，看 [README](../README.md) 就够了。

- [1. 安装](#1-安装)
- [2. 接数据源](#2-接数据源)
- [3. 启动方式](#3-启动方式)
- [4. 生成报告](#4-生成报告)
- [5. 配置领导名单](#5-配置领导名单)
- [6. 接在线大模型](#6-接在线大模型)
- [7. 命令行用法](#7-命令行用法)
- [8. 报告长什么样](#8-报告长什么样)
- [9. 文件与日志](#9-文件与日志)

---

## 1. 安装

需要 Python 3.10 或更高版本。

```bash
python -m pip install -e .
```

语音转写是可选功能，要用再装（首次运行会下载模型，约几百 MB）：

```bash
python -m pip install -e ".[asr]"
```

没装也不影响主流程：语音会降级成 `[语音]` 占位，不报错。

---

## 2. 接数据源

程序本身不生产数据，只读一个本地的消息库。两种后端任选其一。

### 2.1 SQLite 直读（推荐）

在项目根目录建一个 `.env`：

```bash
cp .env.example .env
```

填上解密库所在目录（指向包含 `db_storage` 之类表结构的目录）：

```bash
WX_DECRYPTED_DIR=C:/你的路径/output/wxid_xxx
```

只要这个目录有效，程序会自动走 SQLite 直读，文件里会有注释说明怎么确认。

### 2.2 chatlog HTTP

不配 `WX_DECRYPTED_DIR`，或目录无效时，会自动回落到 HTTP 模式，请求 `config/settings.yaml` 里的 `chatlog.api_base`（默认 `http://127.0.0.1:5030`）。这条路是给老用户兼容用的。

### 2.3 数据多久刷新一次

取决于你怎么产出数据：

- SQLite 直读读的是**某一刻的快照**。群里来了新消息，要先让数据源重新导出/刷新一次，再生成报告，否则看到的还是旧内容。
- 如果用的是某个会持续写库的工具，通常只要它还在跑就不用管。

仓库里的 `刷新数据.bat` 只是把你配置的刷新脚本包了一层，支持两种接法：

- 设置环境变量：`setx WX_REFRESH_SCRIPT "你的刷新脚本路径"`
- 或在项目根目录放一个 `local_refresh.bat`（已被 `.gitignore` 排除，不会进仓库）

两者都没有时，双击 `刷新数据.bat` 只会提示「跳过」，不做任何事。

---

## 3. 启动方式

### 3.1 一键启动（Windows）

| 脚本 | 作用 |
| --- | --- |
| `启动.bat` | 刷新数据（若已配置）+ 后台起控制台 + 打开浏览器 |
| `后台启动.bat` | 只起后台服务，不开浏览器；重复执行安全 |
| `停止服务.bat` | 停掉占用 5710 端口的控制台进程 |
| `刷新数据.bat` | 只跑一次数据刷新（`WX_REFRESH_SCRIPT` 不为空时） |

`启动.bat` 里的黑色窗口只是启动器，**跑完可以直接关掉**，服务在后台继续运行。后台输出在 `logs/console.out`。

> 前三个脚本会自动探测 `py -3` / `python` / 常见安装目录，不要求 Python 装在某个固定路径。

### 3.2 开机自启（可选）

```bash
python tools/make_startup_entry.py          # 写入 %APPDATA%\...\Startup\WeChatWorkHub.bat
python tools/make_startup_entry.py --remove # 删掉
```

### 3.3 直接跑命令

```bash
python main.py --serve
```

浏览器会打开 `http://127.0.0.1:5710`。控制台只监听 127.0.0.1，**不要暴露到公网**。

---

## 4. 生成报告

### 4.1 在网页里

1. **选群聊** —— 下拉列表来自数据源
2. **（可选）设置关注的人** —— 为这个群添加要重点关注的成员，他们的发言会打「领导」标记
3. **选日期** —— 今天 / 昨天 / 指定日期
4. **选模板** —— 领导简报 / 每日摘要 / 待办清单
5. 点 **生成工作报告**，等进度条走完

历史报告在下方列表里，可以直接预览、删除（删除有二次确认）。

### 4.2 在命令行里

```bash
python main.py --group "XX项目工作群"
python main.py --group "XX项目工作群" --date yesterday -f both --open
```

完整参数见 [README 的参数表](../README.md#命令行参数)。

---

## 5. 配置领导名单

编辑 `config/groups.yaml`：

```yaml
target_groups:
  - name: "XX项目工作群"
    enabled: true
    leaders:
      - name: "张总"
        id: "wxid_xxxx"      # 可选，比 name 更准
global_leaders:               # 所有群都生效
  - name: "陈总"
    id: ""
```

`name` 必须和数据源里的群显示名完全一致（含空格、emoji）。命中规则是 `id` 或 `name` 任一匹配，`id` 优先。

不配也能用，只是报告里不会打 `[领导]` 标记。

---

## 6. 接在线大模型

默认 `summarizer.provider: deepseek`。没配 Key 时会自动回落为离线模式（只生成待处理文件），不会报错、不会上传任何内容。

配置方式：

1. 在 `.env` 里填：

   ```bash
   DEEPSEEK_API_KEY=sk-你的密钥
   DEEPSEEK_BASE_URL=https://api.deepseek.com
   DEEPSEEK_TEXT_MODEL=deepseek-chat
   ```

2. 重启服务（`停止服务.bat` → `启动.bat`）

任何 OpenAI 兼容的 `/chat/completions` 服务都能接：改 `DEEPSEEK_BASE_URL`，或在 `config/settings.yaml` 里把 `summarizer.provider` 设成 `doubao` 并不填 `DEEPSEEK_*`。

> 想完全离线：把 `summarizer.provider` 改成 `file_output`。程序会把整理好的上下文 + 完整 prompt 写到 `runs/pending/`，你复制给任意 AI 助手用即可。

---

## 7. 命令行用法

```bash
python main.py --list-groups                     # 列出数据源里的群
python main.py --group "XX项目工作群"             # 今天
python main.py --group "XX项目工作群" -d yesterday
python main.py --group "XX项目工作群" -f both --open
python main.py --group "XX项目工作群" --leaders-only
python main.py --group "XX项目工作群" --dry-run   # 只跑链路不落盘
python main.py --serve                            # 控制台
```

安装后也可以用 `chatdigest` 代替 `python main.py`。

---

## 8. 报告长什么样

一份报告包含：

- **顶部统计**：消息总数、领导发言数、语音/图片数量、任务数、时间跨度
- **任务安排**：从总结里解析出的任务卡片，带优先级、负责人、截止时间、来源
- **总结**：模型输出（或离线模式下的待处理文件指引）
- **聊天记录**：按时间排列的完整消息流，领导发言高亮，「只看领导」可过滤

HTML 版支持深色模式、点击条目展开详情、复制总结为 Markdown。

---

## 9. 文件与日志

| 路径 | 内容 |
| --- | --- |
| `runs/` | 生成的报告（`.md` / `.html`） |
| `runs/pending/` | 离线模式产出的待处理文件与完整 prompt |
| `logs/hub.log` | 运行日志，滚动保留 5 份 |
| `logs/console.out` | 后台服务（无窗口模式）的控制台输出 |

这些目录都在 `.gitignore` 里，不会被提交。
