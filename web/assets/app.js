/* ===========================================================================
   Chat Digest · 控制台交互
   ---------------------------------------------------------------------------
   逐条对应 UI 交互提示词库：
     01-07 按钮弹出菜单        → 卡片右上角 ··· 菜单，点击外部原路收回
     01-08 卡片弹出详情        → 点击报告卡片由原位放大，关闭反向缩回
     01-13 分类标签展开面板     → 群选择下拉面板
     01-14 日期弹出日历        → 从日期按钮下方展开
     02-01 点击位置扩散填充     → 主按钮从点击处扩散
     02-03 按钮变进度条        → 运行中按钮原地化为进度条
     02-04 滑动完成操作        → 清理全部报告
     02-06 一个按钮给另一个让位  → 删除二次确认
     03-11 物理弹簧交错弹入     → 列表逐抖动入场
     03-12 骨架屏流光加载      → 首次加载
     04-01 按压回弹            → 所有可交互元素的按压手感
     04-02 弹性开关            → 深色 / 只看领导
     04-07 数字缓停            → Hero 与卡片指标计数
     05-02 Toast 柔和进场      → 操作反馈
     05-03 提示堆叠让位        → 多条提示互不遮挡
     05-04 撤销进度条          → Toast 倒计时条
     07-18 分类联动滚动        → 左侧群分类与右侧列表滚动互锁
   =========================================================================== */
(function () {
  'use strict';

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  var state = {
    groups: [], runs: [], templates: [],
    group: '', date: 'today', template: 'leader_brief',
    leadersOnly: false, taskId: '', polling: null,
    // 领导设置面板的临时态
    leadersDraft: [], groupsConfig: { target_groups: [], global_leaders: [] }
  };

  /* ------------------------------------------------------------------ API */
  function api(path, opts) {
    return fetch(path, opts || {}).then(function (r) {
      // 后端错误响应也带 JSON 体（含可读的 error 文案），把它透出来，
      // 否则界面上只会显示一句没有信息量的 "HTTP 409"。
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) {
          var err = new Error(body.error || ('HTTP ' + r.status));
          err.status = r.status;
          err.body = body;
          throw err;
        }
        return body;
      });
    });
  }

  /* ------------------------------------------------------- 数字缓停 04-07 */
  function countTo(el, target) {
    var from = parseFloat(el.textContent.replace(/[^\d.]/g, '')) || 0;
    if (target === from || reduce) { el.textContent = target; return; }
    var dur = 900, start = null;
    function ease(t) { return t === 1 ? 1 : 1 - Math.pow(2, -10 * t); }
    function frame(ts) {
      if (start === null) start = ts;
      var p = Math.min((ts - start) / dur, 1);
      el.textContent = Math.round(from + (target - from) * ease(p));
      if (p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  /* ------------------------------------------------------- Toast 05-02/03/04 */
  var toastHost = $('#toastHost');
  function toast(text, opts) {
    opts = opts || {};
    var el = document.createElement('div');
    el.className = 'toast';
    if (opts.kind) el.setAttribute('data-kind', opts.kind);
    el.style.position = 'relative';
    el.style.overflow = 'hidden';

    var label = document.createElement('span');
    label.textContent = text;
    el.appendChild(label);

    var seconds = opts.seconds || 3.2;
    var undone = false;
    if (opts.action) {
      var act = document.createElement('button');
      act.className = 'toast-act';
      act.textContent = opts.action.label;
      act.addEventListener('click', function () {
        undone = true;
        if (opts.action.run) opts.action.run();
        dismiss();
      });
      el.appendChild(act);
    }

    var prog = document.createElement('span');
    prog.className = 'toast-prog';
    prog.style.width = '100%';
    el.appendChild(prog);

    toastHost.appendChild(el);
    requestAnimationFrame(function () {
      el.classList.add('is-on');
      if (!reduce) {
        prog.style.transition = 'width ' + seconds + 's linear';
        requestAnimationFrame(function () { prog.style.width = '0%'; });
      }
    });

    var timer = setTimeout(dismiss, seconds * 1000);
    function dismiss() {
      clearTimeout(timer);
      el.classList.remove('is-on');
      el.classList.add('is-leaving');
      setTimeout(function () { if (el.parentNode) el.remove(); }, 360);
    }
    return { dismiss: dismiss, isUndone: function () { return undone; } };
  }

  /* --------------------------------------------- 滚动吸顶 / 回到顶部 */
  function initScrollChrome() {
    var toTop = $('#toTop');
    var ticking = false;
    function update() {
      ticking = false;
      var y = window.scrollY || 0;
      document.body.classList.toggle('is-scrolled', y > 28);
      toTop.classList.toggle('is-on', y > 420);
    }
    // 用 rAF 合并同一帧内多次 scroll 事件，避免高频 class 抖动
    function onScroll() {
      if (!ticking) { ticking = true; requestAnimationFrame(update); }
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    toTop.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: reduce ? 'auto' : 'smooth' });
    });
    update();
  }

  /* ------------------------------------------------- 主题弹性开关 04-02 */
  function initTheme() {
    var input = $('#themeSwitch');
    var saved = localStorage.getItem('chatdigest-theme') || 'light';
    document.documentElement.setAttribute('data-theme', saved);
    input.checked = saved === 'dark';
    input.addEventListener('change', function () {
      var next = input.checked ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('chatdigest-theme', next);
    });
  }

  /* ------------------------------------------------------ 弹出面板通用 */
  function initPopovers() {
    var open = null;
    var pairs = [
      ['#groupBtn', '#groupPop'],
      ['#dateBtn', '#datePop'],
      ['#tplBtn', '#tplPop']
    ];

    function closeAll() {
      pairs.forEach(function (p) {
        var btn = $(p[0]), pop = $(p[1]);
        if (btn) btn.setAttribute('aria-expanded', 'false');
        if (pop) pop.hidden = true;
      });
      open = null;
    }

    pairs.forEach(function (p) {
      var btn = $(p[0]), pop = $(p[1]);
      if (!btn || !pop) return;
      btn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var willOpen = pop.hidden;
        closeAll();
        if (willOpen) {
          pop.hidden = false;
          btn.setAttribute('aria-expanded', 'true');
          open = [btn, pop];
          if (pop.id === 'datePop') renderCalendar();
          var search = $('#groupSearch');
          if (pop.id === 'groupPop' && search) setTimeout(function () { search.focus(); }, 30);
        }
      });
      pop.addEventListener('click', function (ev) { ev.stopPropagation(); });
    });

    document.addEventListener('click', closeAll);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeAll(); });
  }

  /* --------------------------------------------------- 群列表 01-13 */
  function renderGroupList(keyword) {
    var list = $('#groupList');
    list.innerHTML = '';
    var kw = (keyword || '').trim().toLowerCase();
    var items = state.groups.filter(function (g) {
      return !kw || g.name.toLowerCase().indexOf(kw) >= 0;
    });
    if (!items.length) {
      var li = document.createElement('li');
      li.className = 'pop-item';
      li.style.color = 'var(--ink-3)';
      li.textContent = '没有匹配的群聊';
      list.appendChild(li);
      return;
    }
    items.forEach(function (g) {
      var li = document.createElement('li');
      li.className = 'pop-item' + (g.name === state.group ? ' is-active' : '');
      var dot = document.createElement('span');
      dot.style.cssText = 'width:6px;height:6px;border-radius:50%;flex:none;background:' +
        (g.configured ? 'var(--ok)' : 'var(--ink-3)');
      var name = document.createElement('span');
      name.textContent = g.name;
      name.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
      var tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = g.configured ? '已关注 ' + g.leader_count + ' 人' : '未设关注人';
      li.appendChild(dot); li.appendChild(name); li.appendChild(tag);
      li.addEventListener('click', function () {
        state.group = g.name;
        $('#groupLabel').textContent = g.name;
        $('#groupPop').hidden = true;
        $('#groupBtn').setAttribute('aria-expanded', 'false');
        renderGroupList($('#groupSearch').value);
        if (!g.configured) {
          toast('这个群还没设置关注的人，点「设置关注的人」添加后才有领导标记', {
            kind: 'err',
            seconds: 6,
            action: { label: '去设置', run: function () { openLeaders(); } }
          });
        }
      });
      list.appendChild(li);
    });
  }

  /* --------------------------------------------------- 日期日历 01-14 */
  var calCursor = new Date();
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function iso(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }

  function renderCalendar() {
    $('#calMonth').textContent = calCursor.getFullYear() + '-' + pad(calCursor.getMonth() + 1);
    var grid = $('#calGrid');
    grid.innerHTML = '';
    var first = new Date(calCursor.getFullYear(), calCursor.getMonth(), 1);
    var offset = (first.getDay() + 6) % 7;          // 周一为一周起点
    var today = iso(new Date());
    var selected = state.date === 'today' ? today : (state.date === 'yesterday' ? '' : state.date);

    var cursor = new Date(first);
    cursor.setDate(first.getDate() - offset);
    for (var i = 0; i < 42; i++) {
      var cellDate = new Date(cursor);
      var cell = document.createElement('button');
      cell.className = 'cal-cell';
      cell.textContent = cellDate.getDate();
      if (cellDate.getMonth() !== calCursor.getMonth()) cell.classList.add('is-out');
      if (iso(cellDate) === today) cell.classList.add('is-today');
      if (iso(cellDate) === selected) cell.classList.add('is-active');
      (function (d) {
        cell.addEventListener('click', function () {
          state.date = iso(d);
          $('#dateLabel').textContent = iso(d);
          $('#datePop').hidden = true;
          $('#dateBtn').setAttribute('aria-expanded', 'false');
        });
      })(cellDate);
      grid.appendChild(cell);
      cursor.setDate(cursor.getDate() + 1);
    }
  }

  function initCalendar() {
    $$('.cal-nav').forEach(function (btn) {
      btn.addEventListener('click', function () {
        calCursor.setMonth(calCursor.getMonth() + parseInt(btn.getAttribute('data-step'), 10));
        renderCalendar();
      });
    });
    $$('.cal-quick button').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var q = btn.getAttribute('data-quick');
        state.date = q;
        $('#dateLabel').textContent = q === 'today' ? '今天' : '昨天';
        $('#datePop').hidden = true;
        $('#dateBtn').setAttribute('aria-expanded', 'false');
      });
    });
  }

  /* --------------------------------------------------- 模板列表 */
  function renderTemplateList() {
    var list = $('#tplList');
    list.innerHTML = '';
    (state.templates.length ? state.templates : ['leader_brief']).forEach(function (name) {
      var li = document.createElement('li');
      li.className = 'pop-item' + (name === state.template ? ' is-active' : '');
      li.textContent = name;
      li.addEventListener('click', function () {
        state.template = name;
        $('#tplLabel').textContent = name;
        $('#tplPop').hidden = true;
        $('#tplBtn').setAttribute('aria-expanded', 'false');
      });
      list.appendChild(li);
    });
  }

  /* ---------------------------------------------------- 主按钮 02-01/02-03 */
  var runBtn = $('#runBtn');
  function setHero(task) {
    var bar = $('#runBar');
    bar.hidden = false;
    $('#runFill').style.width = task.percent + '%';
    $('#runStage').textContent = task.stage + (task.detail ? ' · ' + task.detail : '');
    if (task.percent >= 100) {
      setTimeout(function () { bar.hidden = true; }, 900);
    }
  }

  function finishRun() {
    runBtn.disabled = false;
    $('.btn-label', runBtn).textContent = '生成工作报告';
    if (state.polling) { clearInterval(state.polling); state.polling = null; }
  }

  function pollTask(id) {
    if (state.polling) clearInterval(state.polling);
    state.polling = setInterval(function () {
      api('/api/task?id=' + id).then(function (data) {
        var task = data.task;
        if (!task) return finishRun();
        setHero(task);
        if (task.result) {
          var stats = task.result.stats || {};
          countTo($('#heroNum'), stats.total || 0);
          countTo($('#statLeader'), stats.leader_count || 0);
          countTo($('#statVoice'), (stats.counts || {}).voice || 0);
          countTo($('#statImage'), ((stats.counts || {}).image || 0) + ((stats.counts || {}).article || 0));
          $('#heroSub').textContent = (task.result.group || '') + ' · ' + (task.result.date || '') + ' · 耗时 ' + task.result.elapsed + 's';
        }
        if (task.status === 'done') {
          finishRun();
          toast('报告已生成，共 ' + ((task.result && task.result.total) || 0) + ' 条消息');
          if (task.result && task.result.warnings && task.result.warnings.length) {
            toast(task.result.warnings[0], { kind: 'err', seconds: 6 });
          }
          loadRuns(true);
        } else if (task.status === 'error' || task.status === 'timeout') {
          // timeout 是服务端软止损：不处理的话这里会一直轮询下去
          finishRun();
          toast(task.error || '运行失败', { kind: 'err', seconds: 7 });
        }
      }).catch(function () { /* 轮询失败不打断，下轮再试 */ });
    }, 700);
  }

  function initRun() {
    runBtn.addEventListener('click', function (ev) {
      if (!state.group) {
        toast('先选一个目标群聊', { kind: 'err' });
        $('#groupBtn').focus();
        return;
      }
      // 02-01：从点击位置扩散
      var rect = runBtn.getBoundingClientRect();
      var ripple = document.createElement('span');
      ripple.className = 'ripple';
      var size = Math.max(rect.width, rect.height) * 2.2;
      ripple.style.width = ripple.style.height = size + 'px';
      ripple.style.left = (ev.clientX - rect.left) + 'px';
      ripple.style.top = (ev.clientY - rect.top) + 'px';
      runBtn.appendChild(ripple);
      setTimeout(function () { ripple.remove(); }, 600);

      runBtn.disabled = true;
      $('.btn-label', runBtn).textContent = '正在处理…';

      api('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          group: state.group,
          date: state.date,
          leaders_only: $('#leadersOnly').checked,
          template: state.template,
          format: 'both'
        })
      }).then(function (data) {
        state.taskId = data.task_id;
        setHero({ percent: 3, stage: '已排队', detail: state.group });
        pollTask(data.task_id);
      }).catch(function (err) {
        // 409 = 已有任务在跑：直接接上它的进度条，
        // 比把用户的好心点击当成一次「启动失败」要合理得多。
        if (err.status === 409 && err.body && err.body.task_id) {
          state.taskId = err.body.task_id;
          setHero({ percent: 3, stage: '已接入运行中的任务', detail: state.group });
          pollTask(err.body.task_id);
          toast('已有报告正在生成，已接入它的进度');
          return;
        }
        finishRun();
        toast('启动失败：' + err.message, { kind: 'err', seconds: 6 });
      });
    });
  }

  /* ------------------------------------------------ 清理全部：滑动确认 02-04 */
  function initWipe() {
    var track = $('.slider-track', $('#wipeSlider'));
    var knob = $('#wipeKnob');
    var fill = $('#wipeFill');
    var dragging = false, startX = 0, dx = 0;
    var max = 0;

    function setX(x) {
      dx = Math.max(0, Math.min(x, max));
      knob.style.transform = 'translateX(' + dx + 'px)';
      fill.style.width = (dx + 34) + 'px';
    }
    function reset(animated) {
      if (!animated) knob.style.transition = 'none';
      dx = 0; knob.style.transform = 'translateX(0)'; fill.style.width = '0px';
      if (!animated) requestAnimationFrame(function () { knob.style.transition = ''; });
    }

    function down(ev) {
      dragging = true;
      max = track.clientWidth - 38;
      startX = (ev.touches ? ev.touches[0].clientX : ev.clientX) - dx;
      track.classList.add('is-drag');
    }
    function move(ev) {
      if (!dragging) return;
      var x = (ev.touches ? ev.touches[0].clientX : ev.clientX) - startX;
      setX(x);
      if (dx >= max - 4) { end(true); }
    }
    function end(completed) {
      if (!dragging) return;
      dragging = false;
      track.classList.remove('is-drag');
      if (completed) {
        doWipe();
        reset(true);
      } else if (dx < max * 0.72) {
        reset(true);
      } else { reset(true); }
    }

    track.addEventListener('mousedown', down);
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', function () { end(false); });
    track.addEventListener('touchstart', down, { passive: true });
    window.addEventListener('touchmove', move, { passive: true });
    window.addEventListener('touchend', function () { end(false); });
  }

  function doWipe() {
    var before = state.runs.length;
    api('/api/runs', { method: 'DELETE' }).then(function () {
      toast('已清理 ' + before + ' 份报告', {
        seconds: 6,
        action: { label: '说错了', run: function () { toast('清理不可撤销，抱歉 :)', { kind: 'err' }); } }
      });
      loadRuns(true);
      countTo($('#heroNum'), 0);
      $('#heroSub').textContent = '报告已清空，随时可以再跑一次';
    }).catch(function (e) { toast('清理失败：' + e.message, { kind: 'err' }); });
  }

  /* ------------------------------------------------- 渲染报告列表 03-11/03-12 */
  function skeleton(n) {
    var host = $('#runsStream');
    host.innerHTML = '';
    for (var i = 0; i < n; i++) {
      var el = document.createElement('div');
      el.className = 'skeleton';
      host.appendChild(el);
    }
  }

  function ago(mtime) {
    var diff = Math.floor(Date.now() / 1000) - mtime;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    var d = new Date(mtime * 1000);
    return (d.getMonth() + 1) + '月' + d.getDate() + '日';
  }

  function renderRuns(runs, animate) {
    var host = $('#runsStream');
    host.innerHTML = '';
    if (!runs.length) {
      var empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = '还没有报告。选好群聊点「生成工作报告」，第一次结果就出现在这里。';
      host.appendChild(empty);
      $('#runsMeta').textContent = '共 0 份';
      renderRail([]);
      return;
    }

    // 按群分组，便于「分类联动滚动」（07-18）
    var order = [];
    var byGroup = {};
    runs.forEach(function (r) {
      if (!byGroup[r.group]) { byGroup[r.group] = []; order.push(r.group); }
      byGroup[r.group].push(r);
    });

    var index = 0;
    order.forEach(function (groupName) {
      var head = document.createElement('div');
      head.className = 'group-head';
      head.id = 'grp-' + order.indexOf(groupName);
      head.setAttribute('data-group', groupName);
      var dot = document.createElement('span');
      dot.className = 'gdot';
      var title = document.createElement('span');
      title.textContent = groupName;
      var count = document.createElement('span');
      count.style.cssText = 'margin-left:auto;font-weight:400;color:var(--ink-3)';
      count.textContent = byGroup[groupName].length + ' 份';
      head.appendChild(dot); head.appendChild(title); head.appendChild(count);
      host.appendChild(head);

      byGroup[groupName].forEach(function (run) {
        host.appendChild(buildCard(run, animate ? index : -1));
        index++;
      });
    });

    $('#runsMeta').textContent = '共 ' + runs.length + ' 份 · ' + order.length + ' 个群';
    renderRail(order.map(function (name) { return { name: name, count: byGroup[name].length }; }));
  }

  function buildCard(run, index) {
    var card = document.createElement('div');
    card.className = 'run-card';
    card.setAttribute('data-md', run.md);
    if (index >= 0) {
      card.classList.add('is-enter');
      card.style.animationDelay = Math.min(index, 18) * 34 + 'ms';
    }

    var top = document.createElement('div');
    top.className = 'run-top';
    var g = document.createElement('span'); g.className = 'run-group'; g.textContent = run.group;
    var d = document.createElement('span'); d.className = 'run-date'; d.textContent = run.date;
    top.appendChild(g); top.appendChild(d);

    var more = document.createElement('button');
    more.className = 'run-more';
    more.textContent = '···';
    more.setAttribute('aria-label', '更多操作');
    top.appendChild(more);

    var metrics = document.createElement('div');
    metrics.className = 'run-metrics';
    [['消息', run.total, false], ['领导发言', run.leader, true], ['大小', (run.size / 1024).toFixed(1) + 'KB', false]]
      .forEach(function (m) {
        var wrap = document.createElement('div');
        wrap.className = 'metric' + (m[2] ? ' is-accent' : '');
        var val = document.createElement('span'); val.className = 'metric-val'; val.textContent = m[1];
        var key = document.createElement('span'); key.className = 'metric-key'; key.textContent = m[0];
        wrap.appendChild(val); wrap.appendChild(key);
        metrics.appendChild(wrap);
      });

    var foot = document.createElement('div');
    foot.className = 'run-foot';
    var badge = document.createElement('span');
    badge.className = 'run-badge';
    badge.textContent = 'Markdown';
    foot.appendChild(badge);
    if (run.html) {
      var badge2 = document.createElement('span');
      badge2.className = 'run-badge is-html';
      badge2.textContent = 'HTML';
      foot.appendChild(badge2);
    }
    var time = document.createElement('span');
    time.style.marginLeft = 'auto';
    time.textContent = ago(run.mtime);
    foot.appendChild(time);

    card.appendChild(top); card.appendChild(metrics); card.appendChild(foot);

    card.addEventListener('click', function (ev) {
      if (ev.target === more || more.contains(ev.target)) return;
      openDetail(card, run);
    });
    more.addEventListener('click', function (ev) {
      ev.stopPropagation();
      openMenu(more, run, card);
    });
    return card;
  }

  /* ------------------------------------------ 按钮弹出菜单 01-07 / 02-06 */
  function openMenu(anchor, run, card) {
    var existing = $('.pop-menu');
    if (existing) { existing.remove(); return; }

    var menu = document.createElement('div');
    menu.className = 'pop pop-menu';
    menu.style.cssText = 'position:fixed;z-index:90;min-width:170px';

    function item(label, danger, fn) {
      var el = document.createElement('div');
      el.className = 'pop-item';
      el.textContent = label;
      if (danger) el.style.color = 'var(--err)';
      el.addEventListener('click', function (ev) {
        ev.stopPropagation();
        fn(el);
      });
      menu.appendChild(el);
      return el;
    }

    if (run.html) {
      item('打开 HTML 报告', false, function () {
        window.open('/reports/' + encodeURIComponent(run.html), '_blank');
        close();
      });
    }
    item('复制 Markdown', false, function () {
      api('/api/report?md=' + encodeURIComponent(run.md)).then(function (data) {
        var ta = document.createElement('textarea');
        ta.value = data.markdown; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); toast('已复制到剪贴板'); }
        catch (e) { toast('复制失败', { kind: 'err' }); }
        ta.remove();
      });
      close();
    });

    // 02-06：点了删除后，菜单内其余项让位，同一位置再点一次才真正执行
    item('删除这份报告', true, function (el) {
      if (el.getAttribute('data-armed') !== '1') {
        el.setAttribute('data-armed', '1');
        $$('.pop-item', menu).forEach(function (n) { n.style.display = n === el ? '' : 'none'; });
        el.textContent = '再点一次确认删除';
        el.style.fontWeight = '700';
        return;
      }
      api('/api/report?md=' + encodeURIComponent(run.md), { method: 'DELETE' })
        .then(function () {
          toast('已删除 · ' + run.group + ' ' + run.date, { seconds: 5 });
          loadRuns(false);
        })
        .catch(function (e) { toast('删除失败：' + e.message, { kind: 'err' }); });
      close();
    });

    document.body.appendChild(menu);
    var rect = anchor.getBoundingClientRect();
    var top = rect.bottom + 6;
    var left = Math.min(rect.left, window.innerWidth - menu.offsetWidth - 12);
    menu.style.top = top + 'px';
    menu.style.left = Math.max(12, left) + 'px';

    function close() { menu.remove(); document.removeEventListener('click', close); }
    setTimeout(function () { document.addEventListener('click', close); }, 10);
  }

  /* ---------------------------------------------- 卡片弹出详情 01-08 */
  var modalRoot = $('#modalRoot');
  var modalCard = $('.modal-card', modalRoot);
  var origin = null;

  function openDetail(card, run) {
    origin = card;
    $('#modalTitle').textContent = run.group + ' · ' + run.date;
    $('#modalBody').textContent = '加载中…';
    var openBtn = $('#modalOpenHtml');
    if (run.html) {
      openBtn.hidden = false;
      openBtn.onclick = function () { window.open('/reports/' + encodeURIComponent(run.html), '_blank'); };
    } else {
      openBtn.hidden = true;
    }

    api('/api/report?md=' + encodeURIComponent(run.md)).then(function (data) {
      $('#modalBody').textContent = data.markdown;
      $('#modalCopy').onclick = function () {
        var ta = document.createElement('textarea');
        ta.value = data.markdown; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); toast('已复制 Markdown'); }
        catch (e) { toast('复制失败', { kind: 'err' }); }
        ta.remove();
      };
    }).catch(function (e) { $('#modalBody').textContent = '加载失败：' + e.message; });

    modalRoot.hidden = false;
    var from = card.getBoundingClientRect();
    var to = modalCard.getBoundingClientRect();
    var dx = from.left - to.left, dy = from.top - to.top;
    var sx = from.width / to.width, sy = from.height / to.height;

    requestAnimationFrame(function () {
      $('.scrim', modalRoot).classList.add('is-on');
      if (!reduce) {
        modalCard.animate(
          [
            { transform: 'translate(' + dx + 'px,' + dy + 'px) scale(' + sx + ',' + sy + ')', opacity: 0.3, borderRadius: '18px' },
            { transform: 'none', opacity: 1, borderRadius: '18px' }
          ],
          { duration: 420, easing: 'cubic-bezier(.2,1.12,.32,1)', fill: 'both' }
        );
      }
    });
  }

  function closeModal() {
    var scrim = $('.scrim', modalRoot);
    scrim.classList.remove('is-on');
    function done() { modalRoot.hidden = true; }
    if (origin && !reduce) {
      var from = modalCard.getBoundingClientRect();
      var t = origin.getBoundingClientRect();
      var dx = t.left - from.left, dy = t.top - from.top;
      var sx = t.width / from.width, sy = t.height / from.height;
      var anim = modalCard.animate(
        [
          { transform: 'none', opacity: 1 },
          { transform: 'translate(' + dx + 'px,' + dy + 'px) scale(' + sx + ',' + sy + ')', opacity: 0.2 }
        ],
        { duration: 320, easing: 'cubic-bezier(.4,0,.6,1)', fill: 'both' }
      );
      anim.onfinish = done; anim.oncancel = done; origin = null;
    } else { done(); }
  }

  function initModal() {
    $('.scrim', modalRoot).addEventListener('click', closeModal);
    $('.modal-x', modalRoot).addEventListener('click', closeModal);
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !modalRoot.hidden) closeModal(); });
  }

  /* ------------------------------------------- 领导/关注人设置面板 */
  var leadersRoot = $('#leadersRoot');
  function renderLeadersList() {
    var list = $('#leadersList');
    list.innerHTML = '';
    if (!state.leadersDraft.length) {
      var empty = document.createElement('li');
      empty.className = 'leaders-empty';
      empty.textContent = '还没有关注的人，输入昵称点「添加」';
      list.appendChild(empty);
      $('#leadersCount').textContent = '未添加任何人';
      return;
    }
    state.leadersDraft.forEach(function (leader, i) {
      var li = document.createElement('li');
      li.className = 'leaders-item';
      var avatar = document.createElement('span');
      avatar.className = 'avatar';
      avatar.textContent = (leader.name || '?').slice(0, 1).toUpperCase();
      var name = document.createElement('span');
      name.className = 'leaders-item-name';
      name.textContent = leader.name;
      var rm = document.createElement('button');
      rm.className = 'leaders-item-rm';
      rm.textContent = '✕';
      rm.setAttribute('aria-label', '移除 ' + leader.name);
      rm.addEventListener('click', function () {
        state.leadersDraft.splice(i, 1);
        renderLeadersList();
      });
      li.appendChild(avatar); li.appendChild(name); li.appendChild(rm);
      list.appendChild(li);
    });
    $('#leadersCount').textContent = '已关注 ' + state.leadersDraft.length + ' 人';
  }

  function openLeaders() {
    if (!state.group) { toast('先选择一个目标群聊', { kind: 'err' }); return; }
    $('#leadersGroup').textContent = state.group;
    // 从原始配置里找到当前群已有的领导
    var cfg = state.groupsConfig.target_groups || [];
    var hit = cfg.filter(function (g) { return g.name === state.group; })[0];
    state.leadersDraft = (hit && hit.leaders ? hit.leaders : []).map(function (l) {
      return { name: l.name || '', id: l.id || '' };
    });
    renderLeadersList();
    leadersRoot.hidden = false;
    $('.scrim', leadersRoot).classList.add('is-on');
    setTimeout(function () { $('#leaderName').focus(); }, 60);
  }

  function closeLeaders() {
    $('.scrim', leadersRoot).classList.remove('is-on');
    leadersRoot.hidden = true;
  }

  function initLeaders() {
    $('#leadersBtn').addEventListener('click', openLeaders);
    $('#leadersClose').addEventListener('click', closeLeaders);
    $('.scrim', leadersRoot).addEventListener('click', closeLeaders);
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !leadersRoot.hidden) closeLeaders();
    });

    function addLeader() {
      var input = $('#leaderName');
      var name = input.value.trim();
      if (!name) { toast('请输入昵称', { kind: 'err' }); input.focus(); return; }
      if (state.leadersDraft.some(function (l) { return l.name === name; })) {
        toast('这个人已经在列表里了', { kind: 'err' }); input.value = ''; input.focus(); return;
      }
      state.leadersDraft.push({ name: name, id: '' });
      input.value = '';
      renderLeadersList();
      input.focus();
    }
    $('#leaderAdd').addEventListener('click', addLeader);
    $('#leaderName').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); addLeader(); }
    });

    $('#leadersSave').addEventListener('click', function () {
      api('/api/groups/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group: state.group, leaders: state.leadersDraft })
      }).then(function (data) {
        toast('已保存：关注 ' + data.leader_count + ' 人');
        // 刷新群列表里的「领导 N 人」标注
        loadGroups();
        closeLeaders();
      }).catch(function (e) { toast('保存失败：' + e.message, { kind: 'err' }); });
    });
  }

  /* --------------------------------- 左侧群分类联动滚动 07-18 */
  var railItems = [];
  function renderRail(groups) {
    var list = $('#railList');
    list.innerHTML = '<li class="rail-bar" aria-hidden="true"></li>';
    railItems = [];
    if (!groups.length) return;

    groups.forEach(function (g) {
      var li = document.createElement('li');
      li.className = 'rail-item';
      var link = document.createElement('button');
      link.className = 'rail-link';
      var dot = document.createElement('span');
      dot.className = 'rail-dot';
      var name = document.createElement('span');
      name.textContent = g.name;
      name.style.cssText = 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
      var count = document.createElement('span');
      count.className = 'rail-count';
      count.textContent = g.count;
      link.appendChild(dot); link.appendChild(name); link.appendChild(count);
      link.addEventListener('click', function () {
        var target = $('.group-head[data-group="' + g.name.replace(/"/g, '') + '"]');
        if (!target) return;
        window.scrollTo({ top: target.getBoundingClientRect().top + window.scrollY - 96, behavior: reduce ? 'auto' : 'smooth' });
      });
      li.appendChild(link);
      list.appendChild(li);
      railItems.push(li);
    });

    if ('IntersectionObserver' in window) {
      var io = new IntersectionObserver(function () {
        var heads = $$('.group-head');
        var best = null, bestTop = Infinity;
        heads.forEach(function (h) {
          var top = h.getBoundingClientRect().top;
          if (top < 240 && top > -40 && Math.abs(top - 120) < bestTop) { bestTop = Math.abs(top - 120); best = h; }
          if (top <= 130) best = h;
        });
        var idx = best ? heads.indexOf(best) : -1;
        activate(idx);
      }, { rootMargin: '-90px 0px -55% 0px', threshold: [0, 0.1, 0.5] });
      $$('.group-head').forEach(function (h) { io.observe(h); });
    }
  }

  function activate(index) {
    var bar = $('.rail-bar', $('#railList'));
    railItems.forEach(function (li, i) { li.classList.toggle('is-active', i === index); });
    if (!bar || index < 0 || !railItems[index]) return;
    bar.style.height = railItems[index].offsetHeight + 'px';
    bar.style.transform = 'translateY(' + railItems[index].offsetTop + 'px)';
  }

  /* ----------------------------------------------------------- 数据加载 */
  function loadGroups() {
    return api('/api/groups').then(function (data) {
      state.groups = data.groups || [];
      renderGroupList('');
      if (!state.group && state.groups.length) {
        var first = state.groups.filter(function (g) { return g.configured; })[0] || state.groups[0];
        state.group = first.name;
        $('#groupLabel').textContent = first.name;
        renderGroupList('');
      }
    }).catch(function (e) { toast('群聊列表加载失败：' + e.message, { kind: 'err' }); });
  }

  function loadGroupsConfig() {
    return api('/api/groups/config').then(function (data) {
      state.groupsConfig = data || { target_groups: [], global_leaders: [] };
    }).catch(function () { /* 配置读取失败不影响主流程 */ });
  }

  function loadRuns(withSkeleton) {
    if (withSkeleton) skeleton(4);
    return api('/api/runs').then(function (data) {
      var runs = data.runs || [];
      var prev = state.runs.map(function (r) { return r.md; });
      state.runs = runs;
      renderRuns(runs, true);
      countTo($('#statRuns'), runs.length);
      if (runs.length && runs[0].total) countTo($('#heroNum'), runs[0].total);
      if (prev.length && runs.length > prev.length) {
        var fresh = runs.filter(function (r) { return prev.indexOf(r.md) < 0; });
        if (fresh.length) toast('新增 ' + fresh.length + ' 份报告已置顶');
      }
    }).catch(function (e) {
      $('#runsStream').innerHTML = '<div class="empty">列表加载失败：' + e.message + '</div>';
    });
  }

  function loadStatus() {
    return api('/api/status').then(function (data) {
      var el = $('#status');
      el.setAttribute('data-state', data.chatlog_online ? 'online' : 'offline');
      $('.status-text', el).textContent = data.chatlog_online ? 'chatlog 已连接' : 'chatlog 未启动';
      state.templates = data.templates || [];
      renderTemplateList();
      $('#footPath').textContent = '输出目录 ' + (data.version ? 'runs/' : '');
    }).catch(function () {
      var el = $('#status');
      el.setAttribute('data-state', 'offline');
      $('.status-text', el).textContent = '服务离线';
    });
  }

  function resumeTask() {
    api('/api/task').then(function (data) {
      if (!data.task || data.task.status !== 'running') return;
      state.taskId = data.task.id;
      $('.btn-label', runBtn).textContent = '正在处理…';
      runBtn.disabled = true;
      pollTask(data.task.id);
    }).catch(function () { /* 忽略：没有进行中的任务 */ });
  }

  /* ---------------------------------------------------------------- 启动 */
  function boot() {
    initTheme();
    initScrollChrome();
    initPopovers();
    initCalendar();
    initRun();
    initWipe();
    initModal();
    initLeaders();

    $('#groupSearch').addEventListener('input', function (e) { renderGroupList(e.target.value); });
    $('#leadersOnly').addEventListener('change', function (e) {
      toast(e.target.checked ? '已开启：只让领导发言进入总结' : '已关闭：全部发言进入总结');
    });

    $('#dateLabel').textContent = '今天';
    loadStatus();
    loadGroups();
    loadGroupsConfig();
    loadRuns(true);
    resumeTask();

    setInterval(loadStatus, 20000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
