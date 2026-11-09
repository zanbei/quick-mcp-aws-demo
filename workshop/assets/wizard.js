(function () {
  var steps = Array.prototype.slice.call(document.querySelectorAll('.step'));
  if (!steps.length) return;

  var subList = document.getElementById('substeps');
  var backBtn = document.getElementById('nav-back');
  var nextBtn = document.getElementById('nav-next');
  var bar = document.getElementById('progress-bar');
  var topcrumb = document.getElementById('crumb-step');

  // module links for cross-file rollover: data-prev-file / data-next-file on <body>
  var prevFile = document.body.getAttribute('data-prev-file') || '';
  var nextFile = document.body.getAttribute('data-next-file') || '';
  var modName = document.body.getAttribute('data-module') || '';

  // build sidebar substeps
  steps.forEach(function (s, i) {
    var a = document.createElement('a');
    a.href = '#step-' + (i + 1);
    a.innerHTML = '<span class="dot"></span><span>' + (s.getAttribute('data-title') || ('步骤 ' + (i + 1))) + '</span>';
    a.addEventListener('click', function (e) { e.preventDefault(); go(i); });
    var li = document.createElement('li');
    li.appendChild(a);
    if (subList) subList.appendChild(li);
  });
  var subLinks = subList ? Array.prototype.slice.call(subList.querySelectorAll('a')) : [];

  function clamp(i) { return Math.max(0, Math.min(steps.length - 1, i)); }

  function go(i, push) {
    i = clamp(i);
    steps.forEach(function (s, k) { s.classList.toggle('active', k === i); });
    subLinks.forEach(function (a, k) {
      a.classList.toggle('cur', k === i);
      a.classList.toggle('done', k < i);
    });
    if (bar) bar.style.width = ((i + 1) / steps.length * 100) + '%';
    if (topcrumb) topcrumb.textContent = '步骤 ' + (i + 1) + ' / ' + steps.length +
      (steps[i].getAttribute('data-title') ? ' · ' + steps[i].getAttribute('data-title') : '');

    // back button
    if (backBtn) {
      if (i === 0 && prevFile) {
        backBtn.className = 'pager-link';
        backBtn.innerHTML = '<span class="lbl">上一模块</span><div class="ttl">← ' + (document.body.getAttribute('data-prev-name') || '上一页') + '</div>';
        backBtn.onclick = function () { location.href = prevFile + '?step=last'; };
      } else if (i === 0) {
        backBtn.className = 'pager-link disabled';
        backBtn.innerHTML = '<span class="lbl">上一步</span><div class="ttl">—</div>';
        backBtn.onclick = null;
      } else {
        backBtn.className = 'pager-link';
        backBtn.innerHTML = '<span class="lbl">上一步</span><div class="ttl">← ' + (steps[i - 1].getAttribute('data-title') || ('步骤 ' + i)) + '</div>';
        backBtn.onclick = function () { go(i - 1, true); };
      }
    }
    // next button
    if (nextBtn) {
      if (i === steps.length - 1 && nextFile) {
        nextBtn.className = 'pager-link next primary';
        nextBtn.innerHTML = '<span class="lbl">下一模块</span><div class="ttl">' + (document.body.getAttribute('data-next-name') || '下一页') + ' →</div>';
        nextBtn.onclick = function () { location.href = nextFile; };
      } else if (i === steps.length - 1) {
        nextBtn.className = 'pager-link next disabled';
        nextBtn.innerHTML = '<span class="lbl">完成</span><div class="ttl">🎉 本模块结束</div>';
        nextBtn.onclick = null;
      } else {
        nextBtn.className = 'pager-link next primary';
        nextBtn.innerHTML = '<span class="lbl">下一步</span><div class="ttl">' + (steps[i + 1].getAttribute('data-title') || ('步骤 ' + (i + 2))) + ' →</div>';
        nextBtn.onclick = function () { go(i + 1, true); };
      }
    }
    if (push !== false) history.replaceState(null, '', '#step-' + (i + 1));
    document.querySelector('.content').scrollTop = 0;
    window.scrollTo(0, 0);
  }

  // initial step: ?step=last, or #step-N
  var start = 0;
  if (/[?&]step=last/.test(location.search)) start = steps.length - 1;
  var m = location.hash.match(/#step-(\d+)/);
  if (m) start = clamp(parseInt(m[1], 10) - 1);
  go(start, false);

  // keyboard arrows
  document.addEventListener('keydown', function (e) {
    if (e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
    var cur = steps.findIndex(function (s) { return s.classList.contains('active'); });
    if (e.key === 'ArrowRight') { if (cur < steps.length - 1) go(cur + 1); }
    if (e.key === 'ArrowLeft') { if (cur > 0) go(cur - 1); }
  });
})();
