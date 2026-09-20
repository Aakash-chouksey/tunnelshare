/* TunnelShare landing demo — front-end only. No backend calls, no fetch(). */
(function () {
  "use strict";

  var DEMO_LINK = "https://tunnelshare.example/s/a1b2c3d4";
  var DEMO_CODE = "482910";

  function $(id) { return document.getElementById(id); }

  function initUpload() {
    var btn = $("demo-upload-btn");
    var bar = $("demo-progress-bar");
    var out = $("demo-share-out");
    var link = $("demo-share-link");
    var code = $("demo-share-code");
    if (!btn || !bar) return;
    if (link) {
      link.textContent = DEMO_LINK;
      link.setAttribute("href", DEMO_LINK);
    }
    if (code) code.textContent = DEMO_CODE;

    var timer = null;
    btn.addEventListener("click", function () {
      if (timer) return;
      btn.disabled = true;
      btn.textContent = "Uploading…";
      if (out) out.style.display = "none";
      var pct = 0;
      bar.style.width = "0%";
      timer = setInterval(function () {
        pct += 4 + Math.random() * 9;
        if (pct >= 100) {
          pct = 100;
          clearInterval(timer);
          timer = null;
          btn.disabled = false;
          btn.textContent = "Upload another file";
          if (out) out.style.display = "block";
        }
        bar.style.width = pct.toFixed(0) + "%";
      }, 120);
    });
  }

  function initVerify() {
    var input = $("demo-code-input");
    var btn = $("demo-verify-btn");
    var msg = $("demo-verify-msg");
    if (!input || !btn) return;
    btn.addEventListener("click", function () {
      var val = input.value.replace(/\s+/g, "");
      if (msg) msg.classList.remove("ok", "err");
      if (!msg) return;
      if (val === DEMO_CODE) {
        msg.textContent = "Code accepted — demo_report.pdf would download now (auto-resume downloads supported).";
        msg.classList.add("ok");
      } else if (val.length !== 6) {
        msg.textContent = "Enter the 6-digit demo code shown on the left: 482910.";
        msg.classList.add("err");
      } else {
        msg.textContent = "Wrong code (demo). Hint: use 482910.";
        msg.classList.add("err");
      }
    });
  }

  function copyText(text, done) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
    } else {
      done(false);
    }
  }

  function initAgentCopy() {
    var btn = $("agent-copy-btn");
    var block = $("agent-prompt");
    if (!btn || !block) return;
    btn.addEventListener("click", function () {
      var text = block.textContent;
      function done(ok) {
        btn.textContent = ok ? "Copied!" : "Copy failed — select manually";
        btn.classList.add("copy-ok");
        setTimeout(function () {
          btn.textContent = "Copy prompt";
          btn.classList.remove("copy-ok");
        }, 2000);
      }
      copyText(text, done);
    });
  }

  function initHeroCopy() {
    var btn = $("hero-copy-btn");
    var cmd = $("hero-cmd");
    if (!btn || !cmd) return;
    btn.addEventListener("click", function () {
      copyText(cmd.textContent.trim(), function (ok) {
        btn.textContent = ok ? "Copied!" : "Copy failed";
        setTimeout(function () { btn.textContent = "Copy"; }, 2000);
      });
    });
  }

  function init() {
    initUpload();
    initVerify();
    initAgentCopy();
    initHeroCopy();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
