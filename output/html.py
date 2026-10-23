"""HTML 报告渲染。

把 Markdown 报告升级成一个可以直接在浏览器里「读」的页面。
交互设计逐条对应 `UI交互提示词库`：

| 提示词 | 落地位置 |
| --- | --- |
| 07补充 · 大标题收缩吸顶 + 毛玻璃渐显 | 顶部导航随滚动收缩 |
| 07-4 · 大色块网格 | Hero 主数字块 + 四宫格副指标 |
| 04-15 · 动态弥散光晕边框 | Hero 主卡缓慢旋转的渐变描边 |
| 04-7 · 数字缓停 | 所有统计数字 Odometer 缓停计数 |
| 07-18 · 分类联动滚动 | 左侧目录随滚动高亮，点击滚动定位 |
| 03-11 · 物理弹簧交错弹入 | 聊天流逐条错峰入场（22ms 递增） |
| 05-9 · 未读分割线不位移 | 「领导发言」浮标吸顶但不推挤内容 |
| 01-8 · 卡片弹出详情 | 点击消息条目由原位放大为居中面板，原路收回 |
| 04-2 · 弹性开关 | 深色模式开关（滑块拉伸再回压） |
| 04-1 · 按压回弹 | 卡片/按钮按下缩放回弹 |
| 05-2 · Toast 柔和进场 | 复制操作反馈 |

考虑到这是要长期归档的静态产物，渲染不依赖任何外部 CDN、JS 框架或字体文件，
双击即可离线打开。
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Dict, Iterable, List, Optional

from collector.models import Message
from core.log import get_logger
from core.stats import format_duration
from output.markdown import safe_filename, strip_summary_heading
from output.md_lite import extract_task_cards, extract_tasks, md_to_html

logger = get_logger("output_html")

# --------------------------------------------------------------------------- #
# 样式
# --------------------------------------------------------------------------- #
_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#F4F4F1;--surface:#FFFFFF;--surface-2:#FAFAF8;--surface-3:#F0F0ED;
  --ink:#141414;--ink-2:#5C5C58;--ink-3:#9A9A95;
  --line:rgba(20,20,20,.09);--line-strong:rgba(20,20,20,.17);
  --accent:#FF5C28;--accent-soft:rgba(255,92,40,.12);
  --radius:18px;--radius-sm:12px;
  --shadow-sm:0 1px 2px rgba(20,20,20,.05),0 6px 18px rgba(20,20,20,.05);
  --shadow-lg:0 30px 70px -20px rgba(20,20,20,.3);
  --dur:.34s;
  --spring:cubic-bezier(.22,1.35,.36,1);
  --out:cubic-bezier(.16,1,.3,1);
}
[data-theme="dark"]{
  --bg:#0F0F0F;--surface:#171717;--surface-2:#1D1D1D;--surface-3:#232323;
  --ink:#F4F4F2;--ink-2:#A9A9A4;--ink-3:#6E6E69;
  --line:rgba(255,255,255,.10);--line-strong:rgba(255,255,255,.20);
  --accent:#FF7A45;--accent-soft:rgba(255,122,69,.16);
  --shadow-sm:0 1px 2px rgba(0,0,0,.5);
  --shadow-lg:0 30px 80px -24px rgba(0,0,0,.8);
}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:15px;line-height:1.7;
  -webkit-font-smoothing:antialiased;
  transition:background var(--dur) var(--out),color var(--dur) var(--out);
}
a{color:inherit}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}

/* ---------- 顶部导航：标题收缩吸顶（纯 transform，不重排不闪） ---------- */
.nav{
  position:sticky;top:0;z-index:60;
  display:flex;align-items:center;gap:14px;
  padding:18px 28px;margin-bottom:8px;
  background:var(--bg);
  box-shadow:0 0 0 0 transparent;
  transition:box-shadow .3s var(--out);
}
body.is-scrolled .nav{
  box-shadow:0 1px 0 var(--line),0 8px 24px -18px rgba(0,0,0,.28);
}
.nav-mark{
  width:34px;height:34px;border-radius:11px;flex:none;
  background:var(--ink);color:var(--bg);
  display:grid;place-items:center;font-weight:800;font-size:13px;letter-spacing:.5px;
  transition:transform .34s var(--spring);
}
body.is-scrolled .nav-mark{transform:scale(.85)}
.nav-titles{min-width:0}
.nav-title{
  margin:0;font-weight:800;letter-spacing:-.02em;line-height:1.15;
  font-size:22px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  transform-origin:left center;
  transition:transform .34s var(--out);
}
body.is-scrolled .nav-title{transform:scale(.76)}
.nav-sub{
  color:var(--ink-3);font-size:12.5px;line-height:1.4;
  max-height:24px;opacity:1;overflow:hidden;
  transition:max-height .3s var(--out),opacity .24s var(--out);
}
body.is-scrolled .nav-sub{max-height:0;opacity:0}
.nav-spacer{flex:1}
.nav-date{font-size:12.5px;color:var(--ink-3);font-variant-numeric:tabular-nums}

/* 弹性开关（04-2） */
.switch{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--ink-2);white-space:nowrap}
.switch-input{position:absolute;opacity:0;pointer-events:none}
.switch-track{
  width:42px;height:24px;border-radius:999px;background:var(--surface-3);
  border:1px solid var(--line);position:relative;cursor:pointer;
  transition:background .3s var(--out);
}
.switch-thumb{
  position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;
  background:var(--ink);
  transition:transform .42s var(--spring),width .42s var(--spring),border-radius .42s var(--spring);
}
.switch-input:checked + .switch-track{background:var(--accent-soft)}
.switch-input:checked + .switch-track .switch-thumb{
  transform:translateX(18px);background:var(--accent);width:20px;border-radius:8px;
}
.switch-input:active + .switch-track .switch-thumb{width:26px}

.wrap{max-width:1180px;margin:0 auto;padding:0 28px 96px}

/* ---------- Hero：大色块网格（07-4） ---------- */
.hero{display:grid;grid-template-columns:1.15fr 1fr;gap:16px;margin:14px 0 30px}
@media(max-width:860px){.hero{grid-template-columns:1fr}}
.hero-card{
  position:relative;border-radius:var(--radius);padding:26px 28px;
  background:var(--ink);color:var(--bg);overflow:hidden;isolation:isolate;
  box-shadow:var(--shadow-sm);
}
.hero-card::before{
  content:"";position:absolute;inset:-140%;z-index:-1;
  background:conic-gradient(from 0deg,transparent 0 62%,var(--accent) 76%,transparent 88% 100%);
  animation:spin 18s linear infinite;opacity:.5;
}
.hero-card::after{
  content:"";position:absolute;inset:1px;border-radius:calc(var(--radius) - 1px);
  background:var(--ink);z-index:-1;
}
@keyframes spin{to{transform:rotate(1turn)}}
.hero-kicker{font-size:12px;letter-spacing:.16em;text-transform:uppercase;opacity:.62}
.hero-num{
  font-size:74px;line-height:1;font-weight:800;letter-spacing:-.045em;
  font-variant-numeric:tabular-nums;margin:8px 0 6px;
}
.hero-sub{font-size:13px;opacity:.66;line-height:1.6}
.hero-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.stat{
  background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-sm);
  padding:16px 18px;box-shadow:var(--shadow-sm);
  display:flex;flex-direction:column;justify-content:center;gap:2px;
}
.stat-val{font-size:30px;font-weight:800;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.stat-key{font-size:12px;color:var(--ink-3)}
.stat.is-accent .stat-val{color:var(--accent)}

/* ---------- 主体布局：目录联动滚动（07-18） ---------- */
.layout{display:grid;grid-template-columns:188px 1fr;gap:34px;align-items:start}
@media(max-width:900px){.layout{grid-template-columns:1fr}.toc{display:none}}
.toc{position:sticky;top:96px}
.toc-title{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-3);margin:0 0 12px}
.toc-list{position:relative;list-style:none;margin:0;padding:0;border-left:2px solid var(--line)}
.toc-item{}
.toc-link{
  display:block;padding:7px 0 7px 14px;font-size:13.5px;color:var(--ink-2);
  text-decoration:none;transition:color .2s var(--out),transform .3s var(--spring);
}
.toc-link:hover{color:var(--ink)}
.toc-item.is-active .toc-link{color:var(--ink);font-weight:700}
.toc-bar{
  position:absolute;left:-2px;width:2px;background:var(--ink);border-radius:2px;
  transition:transform .42s var(--spring),height .42s var(--spring);
}

/* ---------- 区块 ---------- */
.section{scroll-margin-top:96px;margin-bottom:38px}
.section-head{
  display:flex;align-items:baseline;gap:12px;margin:0 0 14px;
  padding-bottom:10px;border-bottom:1px solid var(--line);
}
.section-title{margin:0;font-size:19px;font-weight:800;letter-spacing:-.02em}
.section-note{margin:0;font-size:12.5px;color:var(--ink-3)}
.panel{
  background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:24px 26px;box-shadow:var(--shadow-sm);
}

/* ---------- Markdown 内容 ---------- */
.md-h{margin:22px 0 10px;letter-spacing:-.02em;line-height:1.35}
.md-h:first-child{margin-top:0}
h2.md-h{font-size:18px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3.md-h{font-size:15.5px}
h4.md-h{font-size:14px;color:var(--ink-2)}
.md-h + *{margin-top:0}
p{margin:0 0 10px}
.md-hr{border:0;border-top:1px solid var(--line);margin:20px 0}
.md-quote{
  margin:0 0 12px;padding:10px 14px;border-radius:var(--radius-sm);
  background:var(--surface-2);border-left:3px solid var(--accent);
}
.md-quote p:last-child{margin:0}
.md-list{margin:0 0 12px;padding-left:20px}
.md-list li{margin:3px 0}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em;
  background:var(--surface-3);padding:2px 5px;border-radius:5px}
pre.code{
  background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius-sm);
  padding:14px 16px;overflow:auto;margin:0 0 12px;
}
pre.code code{background:none;padding:0}
.empty{color:var(--ink-3)}
.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:var(--radius-sm);margin:0 0 14px}
.md-table{border-collapse:collapse;width:100%;font-size:13.5px}
.md-table th,.md-table td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
.md-table th{background:var(--surface-2);font-weight:700;font-size:12.5px;color:var(--ink-2)}
.md-table tr:last-child td{border-bottom:0}

/* 任务行 */
.task-list{list-style:none;margin:0 0 14px;padding:0}
.task{
  display:flex;align-items:flex-start;gap:10px;padding:9px 0;
  border-bottom:1px dashed var(--line);
}
.task:last-child{border-bottom:0}
.task-box{
  width:16px;height:16px;margin-top:4px;flex:none;border-radius:5px;
  border:1.5px solid var(--line-strong);
}
.task.is-done .task-title{text-decoration:line-through;color:var(--ink-3)}
.task.is-done .task-box{background:var(--ink);border-color:var(--ink);position:relative}
.task.is-done .task-box::after{
  content:"";position:absolute;left:5px;top:2px;width:4px;height:8px;
  border:1.5px solid var(--bg);border-top:0;border-left:0;transform:rotate(40deg);
}
.task-title{flex:1;min-width:0}
.task-meta{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.chip{
  font-size:11.5px;padding:2px 8px;border-radius:999px;white-space:nowrap;
  background:var(--surface-3);color:var(--ink-2);
}
.chip-pri{background:var(--accent-soft);color:var(--accent);font-weight:700}
.chip-due{background:var(--ink);color:var(--bg);font-variant-numeric:tabular-nums}

/* ---------- 任务卡片网格：每张卡片一个任务（明亮卡片式） ---------- */
.task-grid{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;
  margin:0 0 14px;
}
.task-card{
  position:relative;display:flex;flex-direction:column;gap:8px;
  background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px 16px 14px;box-shadow:var(--shadow-sm);
  overflow:hidden;transition:transform .22s var(--spring),box-shadow .22s var(--out),border-color .22s var(--out);
}
.task-card::before{
  content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--ink-3);
}
.task-card[data-pri="紧急"]::before{background:var(--err)}
.task-card[data-pri="重要"]::before{background:var(--accent)}
.task-card[data-pri="高"]::before{background:var(--err)}
.task-card[data-pri="中"]::before{background:var(--warn,#D9822B)}
.task-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-lg);border-color:var(--line-strong)}
.task-card.is-done{opacity:.62}
.task-card-top{display:flex;align-items:center;gap:8px;padding-left:8px}
.task-card-pri{
  font-size:11px;font-weight:800;letter-spacing:.04em;padding:2px 9px;border-radius:999px;
  background:var(--surface-3);color:var(--ink-2);
}
.task-card-pri[data-pri="紧急"]{background:rgba(211,60,60,.12);color:var(--err)}
.task-card-pri[data-pri="重要"]{background:var(--accent-soft);color:var(--accent)}
.task-card-title{
  font-weight:700;font-size:14.5px;line-height:1.5;letter-spacing:-.01em;padding-left:8px;
  word-break:break-word;
}
.task-card.is-done .task-card-title{text-decoration:line-through;color:var(--ink-3)}
.task-card-meta{display:flex;flex-direction:column;gap:5px;padding-left:8px;margin-top:auto}
.task-card-row{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--ink-2)}
.task-card-row .ic{width:15px;text-align:center;color:var(--ink-3);font-size:11px;flex:none}
.task-card-row .val{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.task-card-row .val.is-owner{font-weight:700;color:var(--ink)}
.task-card-row .val.is-due{color:var(--ink-2);font-variant-numeric:tabular-nums}
.task-card-row .val.is-src{color:var(--ink-3);font-size:11.5px}
.task-card-empty{
  padding:26px;text-align:center;color:var(--ink-3);font-size:13px;
  border:1px dashed var(--line-strong);border-radius:var(--radius);
}

/* ---------- 过滤芯片：指示条连续滑动（06-4 变体） ---------- */
.filter{
  position:relative;display:inline-flex;gap:2px;padding:3px;
  background:var(--surface-3);border-radius:999px;margin-bottom:14px;
}
.filter-bar{
  position:absolute;top:3px;bottom:3px;left:0;width:0;border-radius:999px;
  background:var(--surface);box-shadow:var(--shadow-sm);
  transition:transform .42s var(--spring),width .42s var(--spring);
}
.filter-btn{
  position:relative;z-index:1;padding:6px 16px;font-size:13px;border-radius:999px;
  color:var(--ink-2);transition:color .24s var(--out),transform .18s var(--out);
}
.filter-btn:active{transform:scale(.96)}
.filter-btn[aria-selected="true"]{color:var(--ink);font-weight:700}

/* ---------- 聊天流：错峰弹入（03-11） ---------- */
.stream{position:relative}
.msg{
  display:grid;grid-template-columns:38px 1fr;gap:12px;
  padding:12px 14px;border-radius:var(--radius-sm);
  border:1px solid transparent;
  transition:background .24s var(--out),transform .2s var(--out),border-color .24s var(--out);
  cursor:pointer;
}
.msg:hover{background:var(--surface);border-color:var(--line)}
.msg:active{transform:scale(.992)}
.msg + .msg{border-top:1px solid var(--line)}
.msg.is-enter{animation:rowIn .52s var(--spring) both}
@keyframes rowIn{from{opacity:0;transform:translateY(14px) scale(.986)}to{opacity:1;transform:none}}
.msg.is-leader{background:var(--surface);border-color:var(--line)}
.avatar{
  width:38px;height:38px;border-radius:12px;display:grid;place-items:center;
  background:var(--surface-3);color:var(--ink-2);font-weight:700;font-size:14px;
}
.msg.is-leader .avatar{background:var(--ink);color:var(--bg)}
.msg-head{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.msg-name{font-weight:700;font-size:14px}
.leader-tag{
  font-size:11px;padding:1px 7px;border-radius:999px;
  background:var(--accent-soft);color:var(--accent);font-weight:700;
}
.msg-time{font-size:11.5px;color:var(--ink-3);font-variant-numeric:tabular-nums;margin-left:auto}
.msg-body{margin:2px 0 0;color:var(--ink-2);white-space:pre-wrap;word-break:break-word}
.msg.is-leader .msg-body{color:var(--ink)}
.msg-note{margin:6px 0 0;font-size:11.5px;color:var(--ink-3)}
.msg-type{
  font-size:11px;color:var(--ink-3);border:1px solid var(--line);
  padding:0 6px;border-radius:5px;
}

/* 领导发言浮标：吸顶但不推挤（05-9） */
.leader-pin{
  position:sticky;top:88px;z-index:20;display:flex;align-items:center;gap:8px;
  margin:0 0 8px;padding:7px 12px;border-radius:999px;
  background:var(--ink);color:var(--bg);font-size:12px;font-weight:700;
  box-shadow:var(--shadow-sm);width:max-content;
  opacity:0;transform:translateY(-8px) scale(.94);pointer-events:none;
  transition:opacity .3s var(--out),transform .42s var(--spring);
}
.leader-pin.is-on{opacity:1;transform:none}

/* ---------- 卡片弹出详情（01-8） ---------- */
.modal-root{position:fixed;inset:0;z-index:100;display:grid;place-items:center;padding:24px}
.modal-root[hidden]{display:none}
.scrim{
  position:absolute;inset:0;background:rgba(10,10,10,.42);
  backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);
  opacity:0;transition:opacity .34s var(--out);
}
.scrim.is-on{opacity:1}
.modal-card{
  position:relative;width:min(620px,100%);max-height:78vh;overflow:auto;
  background:var(--surface);border-radius:var(--radius);
  box-shadow:var(--shadow-lg);padding:22px 24px;
  transform-origin:top left;
}
.modal-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.modal-title{font-weight:800;font-size:15px}
.modal-x{
  margin-left:auto;width:30px;height:30px;border-radius:9px;
  display:grid;place-items:center;background:var(--surface-3);
  transition:transform .2s var(--spring),background .2s var(--out);
}
.modal-x:hover{background:var(--surface-2)}
.modal-x:active{transform:scale(.9)}
.modal-body{white-space:pre-wrap;word-break:break-word;color:var(--ink-2);margin:0}
.kv{display:grid;grid-template-columns:70px 1fr;gap:6px 12px;font-size:12.5px;margin:14px 0 0}
.kv dt{color:var(--ink-3)}
.kv dd{margin:0;color:var(--ink-2);word-break:break-all}

/* ---------- Toast（05-2） ---------- */
.toast-host{
  position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:120;
  display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none;
}
.toast{
  padding:9px 16px;border-radius:999px;background:var(--ink);color:var(--bg);
  font-size:13px;box-shadow:var(--shadow-lg);
  opacity:0;transform:translateY(12px) scale(.96);
  transition:opacity .3s var(--out),transform .42s var(--spring);
}
.toast.is-on{opacity:1;transform:none}

/* ---------- 回到顶部 ---------- */
.to-top{
  position:fixed;right:24px;bottom:28px;width:42px;height:42px;border-radius:50%;
  background:var(--surface);border:1px solid var(--line);box-shadow:var(--shadow-sm);
  display:grid;place-items:center;z-index:80;
  opacity:0;transform:translateY(14px) scale(.8);pointer-events:none;
  transition:opacity .3s var(--out),transform .42s var(--spring);
}
.to-top.is-on{opacity:1;transform:none;pointer-events:auto}
.to-top:active{transform:scale(.86)}
.to-top:hover{background:var(--surface-2)}

.foot{
  margin-top:40px;padding-top:18px;border-top:1px solid var(--line);
  font-size:12px;color:var(--ink-3);display:flex;gap:14px;flex-wrap:wrap;
}
.copy-btn{
  padding:5px 12px;border-radius:999px;border:1px solid var(--line);
  background:var(--surface);font-size:12px;color:var(--ink-2);
  transition:transform .2s var(--spring),background .2s var(--out);
}
.copy-btn:hover{background:var(--surface-2)}
.copy-btn:active{transform:scale(.94)}

@media(prefers-reduced-motion:reduce){
  *{animation-duration:.01ms !important;transition-duration:.01ms !important}
}
"""

# --------------------------------------------------------------------------- #
# 脚本
# --------------------------------------------------------------------------- #
_JS = """
(function(){
  'use strict';
  var payload = {};
  try{ payload = JSON.parse(document.getElementById('chatdigest-payload').textContent || '{}'); }catch(e){}
  var msgs = payload.messages || [];
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- 数字缓停计数（04-7） ---- */
  function easeOutExpo(t){ return t===1 ? 1 : 1 - Math.pow(2, -10*t); }
  function countUp(el){
    var target = parseFloat(el.getAttribute('data-count')||'0');
    if(!target){ el.textContent='0'; return; }
    var dur = reduce ? 0 : 900, start = null;
    function frame(ts){
      if(start===null) start = ts;
      var p = Math.min((ts-start)/dur, 1);
      var v = Math.round(target * easeOutExpo(p));
      el.textContent = v >= 10000 ? Math.round(v/1000)+'k' : v;
      if(p < 1) requestAnimationFrame(frame);
    }
    if(dur===0){ el.textContent = target; return; }
    requestAnimationFrame(frame);
  }
  function initCounters(){
    document.querySelectorAll('[data-count]').forEach(function(el){
      el.textContent = '0';
      countUp(el);
    });
  }

  /* ---- 滚动收缩吸顶（rAF 节流 + 迟滞，避免边界抖动） ---- */
  function initScrollChrome(){
    var toTop = document.getElementById('toTop');
    var ticking = false;
    var scrolled = false;
    function update(){
      ticking = false;
      var y = window.scrollY || 0;
      // 迟滞：向下超过 24px 才收缩，向上回到 8px 以下才展开，避免临界点反复切换
      if (y > 24 && !scrolled) scrolled = true;
      else if (y < 8 && scrolled) scrolled = false;
      document.body.classList.toggle('is-scrolled', scrolled);
      if(toTop) toTop.classList.toggle('is-on', y > 420);
    }
    function onScroll(){
      if(!ticking){ ticking = true; requestAnimationFrame(update); }
    }
    window.addEventListener('scroll', onScroll, {passive:true});
    update();
  }

  /* ---- 目录联动滚动（07-18） ---- */
  function initToc(){
    var list = document.getElementById('tocList');
    if(!list) return;
    var links = Array.prototype.slice.call(list.querySelectorAll('.toc-link'));
    var items = Array.prototype.slice.call(list.querySelectorAll('.toc-item'));
    var bar = list.querySelector('.toc-bar');
    var targets = links.map(function(a){ return document.querySelector(a.getAttribute('href')); });

    function moveBar(index){
      if(!bar || !items[index]) return;
      var item = items[index];
      bar.style.height = item.offsetHeight + 'px';
      bar.style.transform = 'translateY(' + item.offsetTop + 'px)';
    }
    function activate(index){
      items.forEach(function(it,i){ it.classList.toggle('is-active', i===index); });
      moveBar(index);
    }
    links.forEach(function(link, i){
      link.addEventListener('click', function(ev){
        ev.preventDefault();
        var t = targets[i];
        if(!t) return;
        window.scrollTo({top: t.getBoundingClientRect().top + window.scrollY - 90, behavior: reduce?'auto':'smooth'});
        activate(i);
      });
    });

    if('IntersectionObserver' in window){
      var visible = new Set();
      var io = new IntersectionObserver(function(entries){
        entries.forEach(function(e){ e.isIntersecting ? visible.add(e.target) : visible.delete(e.target); });
        var best = -1, bestTop = Infinity;
        targets.forEach(function(t,i){
          if(!t || !visible.has(t)) return;
          var top = Math.abs(t.getBoundingClientRect().top - 110);
          if(top < bestTop){ bestTop = top; best = i; }
        });
        if(best >= 0) activate(best);
      }, {rootMargin:'-80px 0px -60% 0px', threshold:[0,0.05,0.3]});
      targets.forEach(function(t){ if(t) io.observe(t); });
    }
    window.addEventListener('resize', function(){
      var idx = items.findIndex(function(it){ return it.classList.contains('is-active'); });
      if(idx>=0) moveBar(idx);
    });
    if(targets[0] && getComputedStyle(list).display !== 'none') activate(0);
  }

  /* ---- 列表错峰弹入（03-11） ---- */
  function initStagger(){
    var rows = Array.prototype.slice.call(document.querySelectorAll('.msg'));
    function reveal(node){
      node.style.animationDelay = (parseInt(node.getAttribute('data-i')||'0',10) * 22) + 'ms';
      node.classList.add('is-enter');
    }
    if(reduce || !('IntersectionObserver' in window)){ rows.forEach(reveal); return; }
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(e.isIntersecting){ reveal(e.target); io.unobserve(e.target); }
      });
    }, {rootMargin:'0px 0px -8% 0px', threshold:0.01});
    rows.forEach(function(r){ io.observe(r); });
  }

  /* ---- 领导发言浮标（05-9）：吸顶但不推挤内容 ---- */
  function initLeaderPin(){
    var pin = document.getElementById('leaderPin');
    if(!pin) return;
    if(!('IntersectionObserver' in window)) return;
    var leaders = Array.prototype.slice.call(document.querySelectorAll('.msg.is-leader'));
    if(!leaders.length) return;
    var io = new IntersectionObserver(function(entries){
      var on = entries.some(function(e){ return e.isIntersecting; });
      pin.classList.toggle('is-on', on);
    }, {rootMargin:'-120px 0px -62% 0px'});
    leaders.forEach(function(n){ io.observe(n); });
  }

  /* ---- 过滤：只看领导 ---- */
  function initFilter(){
    var host = document.getElementById('filterHost');
    if(!host) return;
    var bar = host.querySelector('.filter-bar');
    var btns = Array.prototype.slice.call(host.querySelectorAll('.filter-btn'));
    function moveTo(btn){
      if(!bar) return;
      bar.style.width = btn.offsetWidth + 'px';
      bar.style.transform = 'translateX(' + (btn.offsetLeft - 3) + 'px)';
    }
    function apply(mode){
      btns.forEach(function(b){
        b.setAttribute('aria-selected', String(b.getAttribute('data-mode')===mode));
      });
      var active = btns.filter(function(b){ return b.getAttribute('aria-selected')==='true'; })[0] || btns[0];
      moveTo(active);
      document.querySelectorAll('.msg').forEach(function(row){
        row.style.display = (mode==='leader' && !row.classList.contains('is-leader')) ? 'none' : '';
      });
    }
    btns.forEach(function(b){ b.addEventListener('click', function(){ apply(b.getAttribute('data-mode')); }); });
    apply('all');
    window.addEventListener('resize', function(){
      var active = btns.filter(function(b){ return b.getAttribute('aria-selected')==='true'; })[0];
      if(active) moveTo(active);
    });
  }

  /* ---- 卡片弹出详情（01-8）：原位放大 / 原路收回 ---- */
  function initModal(){
    var root = document.getElementById('modalRoot');
    if(!root) return;
    var scrim = root.querySelector('.scrim');
    var card = root.querySelector('.modal-card');
    var title = root.querySelector('.modal-title');
    var body = root.querySelector('.modal-body');
    var kv = root.querySelector('.kv');
    var origin = null;

    function open(row){
      var data = msgs[parseInt(row.getAttribute('data-idx')||'0',10)];
      if(!data) return;
      origin = row;
      title.textContent = data.sender + ' · ' + data.time;
      body.textContent = data.content || '（该消息没有文字内容）';
      kv.innerHTML = '';
      [['类型', data.typeLabel], ['时间', data.fullTime], ['备注', data.note], ['原始', data.raw]]
        .forEach(function(pair){
          if(!pair[1]) return;
          var dt = document.createElement('dt'); dt.textContent = pair[0];
          var dd = document.createElement('dd'); dd.textContent = pair[1];
          kv.appendChild(dt); kv.appendChild(dd);
        });

      var from = row.getBoundingClientRect();
      root.hidden = false;
      var to = card.getBoundingClientRect();
      var dx = from.left - to.left, dy = from.top - to.top;
      var sx = from.width / to.width, sy = from.height / to.height;

      requestAnimationFrame(function(){
        scrim.classList.add('is-on');
        if(!reduce){
          card.animate(
            [
              {transform:'translate('+dx+'px,'+dy+'px) scale('+sx+','+sy+')', opacity:0.35, borderRadius:'12px'},
              {transform:'none', opacity:1, borderRadius:'18px'}
            ],
            {duration:420, easing:'cubic-bezier(.2,1.12,.32,1)', fill:'both'}
          );
        }
      });
    }

    function close(){
      var from = card.getBoundingClientRect();
      scrim.classList.remove('is-on');
      var done = function(){ root.hidden = true; card.style.opacity=''; };
      if(origin && !reduce){
        var t = origin.getBoundingClientRect();
        var dx = t.left - from.left, dy = t.top - from.top;
        var sx = t.width / from.width, sy = t.height / from.height;
        var anim = card.animate(
          [
            {transform:'none', opacity:1, borderRadius:'18px'},
            {transform:'translate('+dx+'px,'+dy+'px) scale('+sx+','+sy+')', opacity:0.25, borderRadius:'12px'}
          ],
          {duration:320, easing:'cubic-bezier(.4,0,.6,1)', fill:'both'}
        );
        anim.onfinish = done; anim.oncancel = done; origin = null;
      } else { done(); }
    }

    document.querySelectorAll('.msg').forEach(function(row){
      row.addEventListener('click', function(){ open(row); });
    });
    scrim.addEventListener('click', close);
    root.querySelector('.modal-x').addEventListener('click', close);
    document.addEventListener('keydown', function(e){ if(e.key === 'Escape' && !root.hidden) close(); });
  }

  /* ---- 主题弹性开关（04-2） ---- */
  function initTheme(){
    var root = document.documentElement;
    var input = document.getElementById('themeSwitch');
    if(!input) return;
    function current(){ return root.getAttribute('data-theme') || 'light'; }
    input.checked = current() === 'dark';
    input.addEventListener('change', function(){
      root.setAttribute('data-theme', input.checked ? 'dark' : 'light');
    });
  }

  /* ---- Toast（05-2） ---- */
  var toastTimer = null;
  function toast(text){
    var host = document.getElementById('toastHost');
    if(!host) return;
    var el = document.createElement('div');
    el.className = 'toast';
    el.textContent = text;
    host.appendChild(el);
    requestAnimationFrame(function(){ el.classList.add('is-on'); });
    setTimeout(function(){
      el.classList.remove('is-on');
      setTimeout(function(){ el.remove(); }, 380);
    }, 1900);
  }

  function initCopy(){
    var btn = document.getElementById('copySummary');
    if(!btn) return;
    btn.addEventListener('click', function(){
      var text = payload.summary_md || '';
      if(!text){ toast('没有可复制的总结内容'); return; }
      var ta = document.createElement('textarea');
      ta.value = text; ta.style.position='fixed'; ta.style.opacity='0';
      document.body.appendChild(ta); ta.select();
      try{ document.execCommand('copy'); toast('总结已复制到剪贴板'); }
      catch(e){ toast('复制失败，请手动选择文本'); }
      ta.remove();
    });
  }

  function initTop(){
    var btn = document.getElementById('toTop');
    if(!btn) return;
    btn.addEventListener('click', function(){
      window.scrollTo({top:0, behavior: reduce ? 'auto' : 'smooth'});
    });
  }

  function boot(){
    initTheme(); initScrollChrome(); initCounters(); initToc();
    initStagger(); initLeaderPin(); initFilter(); initModal(); initCopy(); initTop();
  }
  if(document.readyState === 'loading'){ document.addEventListener('DOMContentLoaded', boot); }
  else { boot(); }
})();
"""

# --------------------------------------------------------------------------- #
# 页面骨架
# --------------------------------------------------------------------------- #
_SKELETON = Template(
    """<!doctype html>
<html lang="zh-CN" data-theme="$THEME">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="Chat Digest">
<title>$TITLE</title>
<style>$CSS</style>
</head>
<body>
<header class="nav">
  <div class="nav-mark">W</div>
  <div class="nav-titles">
    <h1 class="nav-title">$GROUP</h1>
    <div class="nav-sub">$SUB</div>
  </div>
  <div class="nav-spacer"></div>
  <div class="nav-date">$DATE</div>
  <label class="switch">
    <input class="switch-input" type="checkbox" id="themeSwitch">
    <span class="switch-track"><span class="switch-thumb"></span></span>
    <span>深色</span>
  </label>
</header>

<main class="wrap">
$HERO
<div class="layout">
  <nav class="toc">
    <p class="toc-title">目录</p>
    <ul class="toc-list" id="tocList">
      $TOC_ITEMS
    </ul>
  </nav>
  <div class="content">
$CONTENT
  </div>
</div>
$FOOTER
</main>

<div class="modal-root" id="modalRoot" hidden>
  <div class="scrim"></div>
  <article class="modal-card" role="dialog" aria-modal="true">
    <div class="modal-head">
      <span class="modal-title"></span>
      <button class="modal-x" aria-label="关闭">✕</button>
    </div>
    <p class="modal-body"></p>
    <dl class="kv"></dl>
  </article>
</div>

<div class="toast-host" id="toastHost"></div>
<button class="to-top" id="toTop" aria-label="回到顶部">↑</button>

<script id="chatdigest-payload" type="application/json">$PAYLOAD</script>
<script>$JS</script>
</body>
</html>
"""
)


# --------------------------------------------------------------------------- #
# 片段构造
# --------------------------------------------------------------------------- #
def _e(text: Any) -> str:
    """HTML 转义。"""
    return html.escape(str(text if text is not None else ""), quote=False)


def _sections() -> List[Dict[str, str]]:
    """返回目录项定义。"""
    return [
        {"id": "sec-overview", "label": "概览"},
        {"id": "sec-tasks", "label": "任务"},
        {"id": "sec-summary", "label": "总结"},
        {"id": "sec-chat", "label": "聊天记录"},
    ]


def _build_toc() -> str:
    """构造目录列表 HTML。"""
    bar = '<li class="toc-bar" aria-hidden="true"></li>'
    items = "\n".join(
        f'<li class="toc-item"><a class="toc-link" href="#{s["id"]}">{_e(s["label"])}</a></li>'
        for s in _sections()
    )
    return bar + "\n" + items


def _build_hero(stats: Dict[str, Any], group_name: str, date: str) -> str:
    """构造 Hero 区：主数字大色块 + 四宫格副指标（07-4）。"""
    counts = stats.get("counts", {}) or {}
    tasks = stats.get("_tasks", 0)
    total = stats.get("total", 0)
    sub = (
        f"跨度 {format_duration(stats.get('span_minutes', 0))} · "
        f"{(stats.get('top_speakers') or [{}])[0].get('name', '—')} 等 "
        f"{min(len(stats.get('top_speakers') or []), 99) or 1} 人参与"
    )
    cells = [
        ("领导发言", stats.get("leader_count", 0), True),
        ("语音转写", counts.get("voice", 0), False),
        ("图片", counts.get("image", 0), False),
        ("待办任务", tasks, False),
    ]
    grid = "\n".join(
        f'<div class="stat{" is-accent" if accent else ""}">'
        f'<span class="stat-val" data-count="{value}">{value}</span>'
        f'<span class="stat-key">{_e(label)}</span>'
        "</div>"
        for label, value, accent in cells
    )
    return f"""<section class="hero" id="sec-overview">
  <div class="hero-card">
    <div class="hero-kicker">消息总数</div>
    <div class="hero-num" data-count="{total}">{total}</div>
    <div class="hero-sub">{_e(group_name)} · {_e(date)}<br>{_e(sub)}</div>
  </div>
  <div class="hero-grid">{grid}</div>
</section>"""


def _build_task_cards(summary_markdown: str) -> str:
    """把总结里的任务行渲染成「每张卡片一个任务」的明亮卡片网格。"""
    cards = extract_task_cards(summary_markdown)
    if not cards:
        return '<div class="task-card-empty">本次总结没有产出明确的任务事项。</div>'

    def _icon(tag: str) -> str:
        return {"owner": "人", "due": "时", "source": "源"}.get(tag, "·")

    items: List[str] = []
    for card in cards:
        pri = card.get("priority") or "常规"
        pri_cls = pri if pri in ("紧急", "重要", "高", "中") else "常规"
        rows: List[str] = []
        if card.get("owner"):
            rows.append(
                f'<div class="task-card-row"><span class="ic">{_icon("owner")}</span>'
                f'<span class="val is-owner">{_e(card["owner"])}</span></div>'
            )
        if card.get("due"):
            rows.append(
                f'<div class="task-card-row"><span class="ic">{_icon("due")}</span>'
                f'<span class="val is-due">{_e(card["due"])}</span></div>'
            )
        if card.get("source"):
            rows.append(
                f'<div class="task-card-row"><span class="ic">{_icon("source")}</span>'
                f'<span class="val is-src">{_e(card["source"])}</span></div>'
            )
        meta = f'<div class="task-card-meta">{"".join(rows)}</div>' if rows else ""
        done_cls = " is-done" if card.get("done") else ""
        items.append(
            f'<article class="task-card{done_cls}" data-pri="{_e(pri_cls)}">'
            f'<div class="task-card-top"><span class="task-card-pri" data-pri="{_e(pri_cls)}">{_e(pri)}</span></div>'
            f'<div class="task-card-title">{_e(card.get("title") or "")}</div>'
            f"{meta}"
            "</article>"
        )
    return f'<div class="task-grid">{"".join(items)}</div>'


def _build_message_rows(messages: Iterable[Message]) -> tuple:
    """构造聊天流的 HTML 与 JSON 数据。

    Args:
        messages: 已加工、已排序的消息列表。

    Returns:
        `(rows_html, payload_list)`
    """
    rows: List[str] = []
    payload: List[Dict[str, Any]] = []
    pin_inserted = False

    for index, msg in enumerate(messages):
        leader_class = " is-leader" if msg.is_leader else ""
        initial = (msg.sender_name or "?").strip()[:1].upper() or "?"
        note_html = f'<p class="msg-note">{_e(msg.note)}</p>' if msg.note else ""
        type_html = (
            f'<span class="msg-type">{_e(msg.type_label)}</span>'
            if msg.msg_type not in ("text",)
            else ""
        )
        leader_tag = '<span class="leader-tag">领导</span>' if msg.is_leader else ""

        # 第一条领导发言之前插一根吸顶浮标（05-9：位置固定，不推挤内容）
        pin_html = ""
        if msg.is_leader and not pin_inserted:
            pin_inserted = True
            pin_html = (
                '<div class="leader-pin" id="leaderPin">'
                f"▲ 领导发言 · {_e(msg.sender_name)}</div>"
            )

        rows.append(
            f"""{pin_html}<div class="msg{leader_class}" data-idx="{index}" data-i="{index}">
  <div class="avatar">{_e(initial)}</div>
  <div>
    <div class="msg-head">
      <span class="msg-name">{_e(msg.sender_name)}</span>{leader_tag}{type_html}
      <span class="msg-time">{_e(msg.time_text)}</span>
    </div>
    <p class="msg-body">{_e(msg.content)}</p>{note_html}
  </div>
</div>"""
        )
        payload.append(
            {
                "sender": msg.sender_name,
                "time": msg.time_text,
                "fullTime": msg.full_time_text,
                "typeLabel": msg.type_label,
                "content": msg.content,
                "raw": msg.raw_content,
                "note": msg.note,
                "isLeader": msg.is_leader,
            }
        )

    if not rows:
        rows.append('<p class="empty">本次没有采集到任何消息。</p>')
    return "\n".join(rows), payload


def _build_content(
    summary_html: str,
    summary_markdown: str,
    messages: Iterable[Message],
    stats: Dict[str, Any],
) -> tuple:
    """构造右侧内容区。

    Returns:
        `(content_html, payload_list)`
    """
    total = stats.get("total", 0)
    leader_count = stats.get("leader_count", 0)
    task_count = stats.get("_tasks", 0)

    filter_html = """<div class="filter" id="filterHost" role="tablist">
  <span class="filter-bar" aria-hidden="true"></span>
  <button class="filter-btn" role="tab" data-mode="all" aria-selected="true">全部</button>
  <button class="filter-btn" role="tab" data-mode="leader" aria-selected="false">只看领导</button>
</div>"""

    rows_html, payload = _build_message_rows(messages)
    task_cards = _build_task_cards(summary_markdown)

    content = f"""<section class="section" id="sec-tasks">
  <div class="section-head">
    <h2 class="section-title">任务安排</h2>
    <p class="section-note">共 {task_count} 项 · 按优先级从左到右、从上到下</p>
  </div>
  {task_cards}
</section>

<section class="section" id="sec-summary">
  <div class="section-head">
    <h2 class="section-title">总结</h2>
    <p class="section-note">从 {total} 条消息中提炼</p>
  </div>
  <div class="panel">{summary_html}</div>
</section>

<section class="section" id="sec-chat">
  <div class="section-head">
    <h2 class="section-title">聊天记录</h2>
    <p class="section-note">{total} 条 · 其中领导发言 {leader_count} 条 · 点击任意一条看细节</p>
  </div>
  {filter_html}
  <div class="stream">
{rows_html}
  </div>
</section>"""
    return content, payload


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def render_report(
    summary_markdown: str,
    messages: Iterable[Message],
    group_name: str,
    date: str,
    stats: Optional[Dict[str, Any]] = None,
    theme: str = "light",
    title: Optional[str] = None,
) -> str:
    """渲染完整 HTML 报告。

    Args:
        summary_markdown: 总结区块的 Markdown 原文。
        messages: 已加工的消息列表。
        group_name: 群名。
        date: 日期字符串。
        stats: core.stats.summarize_messages 的输出。
        theme: `light` 或 `dark`。
        title: 页面标题，默认自动生成。

    Returns:
        完整 HTML 字符串（单文件，无外部依赖）。
    """
    stats = dict(stats or {})
    messages = list(messages)
    summary_markdown = summary_markdown or ""

    # 任务数：直接统计总结里 ☐ / ☑ 行，供 Hero 展示
    stats["_tasks"] = len(extract_tasks(summary_markdown))

    # 页面已有「总结」小节标题，正文里自带的同名标题要剥掉，否则会重复。
    # 任务行也跳过：它们已单独渲染成上方的「任务卡片网格」，避免重复展示。
    summary_html = md_to_html(strip_summary_heading(summary_markdown), skip_tasks=True)
    hero = _build_hero(stats, group_name, date)
    content, payload = _build_content(summary_html, summary_markdown, messages, stats)

    safe_payload = json.dumps(
        {"messages": payload, "summary_md": summary_markdown}, ensure_ascii=False
    ).replace("</", "<\\/").replace("<", "\\u003c")

    leader_hint = (
        f"领导发言 {stats.get('leader_count', 0)} / 消息总数 {stats.get('total', 0)}"
    )
    footer = f"""<footer class="foot">
  <span>Chat Digest · 本地生成 · {datetime.now():%Y-%m-%d %H:%M}</span>
  <span>{leader_hint}</span>
  <span style="flex:1"></span>
  <button class="copy-btn" id="copySummary">复制总结 Markdown</button>
</footer>"""

    return _SKELETON.safe_substitute(
        THEME="dark" if theme == "dark" else "light",
        TITLE=_e(title or f"{group_name} · {date} 工作简报"),
        GROUP=_e(group_name),
        SUB=_e(leader_hint),
        DATE=_e(date),
        HERO=hero,
        TOC_ITEMS=_build_toc(),
        CONTENT=content,
        FOOTER=footer,
        PAYLOAD=safe_payload,
        CSS=_CSS,
        JS=_JS,
    )


def write_html(
    summary_markdown: str,
    messages: Iterable[Message],
    group_name: str,
    date: str,
    output_dir: Path | str = "./runs",
    stats: Optional[Dict[str, Any]] = None,
    theme: str = "light",
    suffix: str = "",
) -> Path:
    """写入 HTML 报告并返回路径。

    Args:
        summary_markdown: 总结 Markdown 原文。
        messages: 消息列表。
        group_name: 群名。
        date: 日期字符串。
        output_dir: 输出目录。
        stats: 统计信息。
        theme: 主题。
        suffix: 文件名后缀。

    Returns:
        写入成功的 Path。
    """
    directory = Path(str(output_dir)).expanduser()
    directory.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%H%M%S") if suffix == "auto" else suffix
    filename = f"{date}_{safe_filename(group_name)}{('_' + stamp) if stamp else ''}.html"
    target = directory / filename
    target.write_text(
        render_report(summary_markdown, messages, group_name, date, stats, theme),
        encoding="utf-8",
    )
    logger.info("HTML 报告已写入：%s", target)
    return target
