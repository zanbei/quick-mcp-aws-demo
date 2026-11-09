document.querySelectorAll('.copy').forEach(function (btn) {
  btn.setAttribute('aria-label', '复制该代码块');
  btn.addEventListener('click', function () {
    var code = btn.parentElement.querySelector('pre code');
    var text = code ? code.innerText : '';
    function done(ok) {
      var old = btn.textContent;
      btn.textContent = ok ? '已复制' : '复制失败';
      setTimeout(function () { btn.textContent = old; }, 1500);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
    } else {
      try {
        var ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        done(document.execCommand('copy')); document.body.removeChild(ta);
      } catch (e) { done(false); }
    }
  });
});
