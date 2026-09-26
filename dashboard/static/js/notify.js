/* ============================================================
   نظام الإشعارات الداخلية — بديل confirm/prompt/alert من المتصفح
   توست + مودال تأكيد + مودال إدخال، كلها داخل صفحة التطبيق.
   ============================================================ */
window.AppNotify = (function () {
  "use strict";

  var host = null;

  function ensureHost() {
    if (host && host.parentNode) return host;
    host = document.createElement("div");
    host.className = "nd-host";
    host.setAttribute("aria-live", "polite");
    document.body.appendChild(host);
    return host;
  }

  var ICONS = {
    success: '<svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg>',
    error: '<svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    warn: '<svg viewBox="0 0 24 24"><path d="M12 3 2 21h20L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>',
    message: '<svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  };

  function toast(message, type, ms) {
    type = type || "message";
    if (["success", "error", "warn", "message"].indexOf(type) < 0) type = "message";
    ensureHost();
    var el = document.createElement("div");
    el.className = "nd-toast nd-" + type;
    var ic = document.createElement("span");
    ic.className = "nd-ic";
    ic.innerHTML = ICONS[type];
    var tx = document.createElement("span");
    tx.className = "nd-tx";
    tx.textContent = message;
    var x = document.createElement("button");
    x.type = "button";
    x.className = "nd-x";
    x.title = "إغلاق";
    x.textContent = "✕";
    el.appendChild(ic);
    el.appendChild(tx);
    el.appendChild(x);
    host.appendChild(el);

    var closed = false;
    function close() {
      if (closed) return;
      closed = true;
      el.classList.add("nd-out");
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 280);
    }
    x.addEventListener("click", close);
    if (ms !== 0) setTimeout(close, ms || 4500);
    return el;
  }

  function escapeHtml(text) {
    var d = document.createElement("div");
    d.textContent = text == null ? "" : String(text);
    return d.innerHTML;
  }

  function modal(opts) {
    opts = opts || {};
    var isPrompt = !!opts.input;
    return new Promise(function (resolve) {
      ensureHost();
      var mask = document.createElement("div");
      mask.className = "nd-mask";
      mask.setAttribute("role", "dialog");
      mask.setAttribute("aria-modal", "true");

      var body = document.createElement("div");
      body.className = "nd-modal";
      if (opts.title) {
        var t = document.createElement("div");
        t.className = "nd-modal-title";
        t.textContent = opts.title;
        body.appendChild(t);
      }
      if (opts.message) {
        var m = document.createElement("div");
        m.className = "nd-modal-body";
        m.textContent = opts.message;
        body.appendChild(m);
      }
      var inputEl = null;
      if (isPrompt) {
        inputEl = document.createElement("input");
        inputEl.type = "text";
        inputEl.className = "nd-modal-input";
        inputEl.placeholder = opts.placeholder || "";
        if (opts.value) inputEl.value = opts.value;
        body.appendChild(inputEl);
      }
      var actions = document.createElement("div");
      actions.className = "nd-modal-actions";
      var ok = document.createElement("button");
      ok.type = "button";
      ok.className = "nd-btn nd-ok" + (opts.danger ? " danger" : "");
      ok.textContent = opts.okText || (isPrompt ? "حفظ" : "تأكيد");
      var cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "nd-btn";
      cancel.textContent = opts.cancelText || "إلغاء";
      actions.appendChild(cancel);
      actions.appendChild(ok);
      body.appendChild(actions);
      mask.appendChild(body);
      document.body.appendChild(mask);

      function close(result) {
        mask.remove();
        document.removeEventListener("keydown", onKey, true);
        resolve(result);
      }
      function onKey(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          close(isPrompt ? null : false);
        }
        if (e.key === "Enter" && isPrompt && inputEl) {
          e.preventDefault();
          close(inputEl.value || null);
        }
      }
      ok.addEventListener("click", function () {
        close(isPrompt ? (inputEl.value || null) : true);
      });
      cancel.addEventListener("click", function () {
        close(isPrompt ? null : false);
      });
      mask.addEventListener("click", function (e) {
        if (e.target === mask) close(isPrompt ? null : false);
      });
      document.addEventListener("keydown", onKey, true);
      if (inputEl) {
        inputEl.focus();
        try { inputEl.select(); } catch (err) {}
      } else {
        ok.focus();
      }
    });
  }

  function confirm(message, opts) {
    opts = opts || {};
    if (typeof message === "object") {
      opts = message;
    } else {
      opts.message = message;
    }
    return modal(opts);
  }

  function prompt(message, opts) {
    opts = opts || {};
    if (typeof message === "object") {
      opts = message;
    } else {
      opts.message = message;
    }
    opts.input = true;
    return modal(opts);
  }

  /* تحويل رسائل الفلاش المخدومة إلى توست داخل التطبيق */
  function convertFlashes() {
    var items = document.querySelectorAll(".flash-wrap .flash, .login-card .flash");
    Array.prototype.forEach.call(items, function (el) {
      var text = (el.textContent || "").trim();
      if (!text) return;
      var cls = el.className || "";
      var type = "message";
      if (cls.indexOf("success") >= 0) type = "success";
      else if (cls.indexOf("error") >= 0) type = "error";
      else if (cls.indexOf("warn") >= 0) type = "warn";
      toast(text, type);
      el.remove();
    });
  }

  /* معالج النماذج ذات data-confirm — تأكيد داخلي قبل الإرسال */
  function handleSubmit(e) {
    var target = e.target;
    var form = target && target.closest ? target.closest("form") : null;
    if (!form) return;
    if (form.closest("#batch-bar")) return;          // شريط الإجراءات الجماعية له منطقه الخاص
    if (form.classList.contains("q-reject-form")) return; // هو أيضاً
    var msg = form.getAttribute("data-confirm");
    if (!msg) return;
    e.preventDefault();
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }
    confirm({
      message: msg,
      danger: form.hasAttribute("data-danger"),
      okText: "نعم، متابعة",
    }).then(function (ok) {
      if (ok) {
        form.setAttribute("data-nd-confirmed", "1");
        form.submit();
      }
    });
  }

  function init() {
    convertFlashes();
    document.addEventListener("submit", handleSubmit);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  return {
    toast: toast,
    confirm: confirm,
    prompt: prompt,
    convertFlashes: convertFlashes,
  };
})();