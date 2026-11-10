/*
 * "PC Off" app: asks for confirmation, then has the helper shut the PC down (POST /shutdown) and
 * closes. The helper waits for the PC to go quiet and shows a TV notification "<PC> is off".
 *
 * Confirmation: the TV's own system popup (com.webos.notification/createAlert) when the TV allows
 * it; its "Turn off" button relaunches this app with {action: "shutdown"}. The same two buttons are
 * always on the app screen too, so the app works without the popup (or after Back closed it).
 * Back always exits.
 */
(function (TW) {
  "use strict";

  TW.log = function (msg) {
    try { console.log("[tvwake-off] " + msg); } catch (e) { /* ignore */ }
  };

  var KEY = { LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, ENTER: 13, BACK: 461, ESC: 27 };
  var CLOSE_AFTER_MS = 3000;

  var cfg = window.TW_CONFIG;
  var api = null;
  var appId = "";
  var st = { gen: 0, phase: "idle", buttons: [], focus: 0 };  // idle | ask | working | done | failed
  var LABELS = { off: "Turn off", cancel: "Cancel", retry: "Retry", exit: "Exit" };

  function $(id) { return document.getElementById(id); }
  function pcName() { return cfg.pcName || "the PC"; }

  function setBanner(text, kind) {
    var b = $("banner");
    b.textContent = text;
    b.className = "banner" + (kind ? " " + kind : "");
  }
  function showError(text) {
    $("error-text").textContent = text || "";
    $("error").hidden = !text;
  }
  function showButtons(ids, focus) {
    var box = $("buttons");
    box.innerHTML = "";
    st.buttons = ids;
    ids.forEach(function (id, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn";
      b.textContent = LABELS[id];
      b.addEventListener("click", function () { activate(id); });
      b.addEventListener("mouseover", function () { setFocus(i); });
      box.appendChild(b);
    });
    setFocus(focus || 0);
  }
  function setFocus(i) {
    var btns = $("buttons").querySelectorAll(".btn");
    if (!btns.length) return;
    st.focus = (i + btns.length) % btns.length;
    for (var k = 0; k < btns.length; k++) btns[k].classList.toggle("focused", k === st.focus);
    btns[st.focus].focus();
  }
  function activate(id) {
    if (id === "off") turnOff();
    else if (id === "retry") ask();
    else TW.exitApp();
  }
  function closeSoon() {
    var gen = st.gen;
    setTimeout(function () { if (gen === st.gen) TW.exitApp(); }, CLOSE_AFTER_MS);
  }
  function fail(text) {
    st.phase = "failed";
    setBanner("Could not turn off " + pcName(), "bad");
    showError(text);
    showButtons(["retry", "exit"]);
  }

  // The TV's own popup. Fails quietly (the on-screen buttons stay) if the TV does not allow it.
  function nativeConfirm() {
    if (!appId) return;
    var relaunch = function (action) {
      return { id: appId, params: { action: action } };
    };
    TW.luna("luna://com.webos.notification", "createAlert", {
      sourceId: appId,
      title: "Turn off " + pcName() + "?",
      message: "Unsaved progress in a running game is lost.",
      modal: true,
      buttons: [
        { label: LABELS.off, onclick: "luna://com.webos.applicationManager/launch", params: relaunch("shutdown") },
        { label: LABELS.cancel, onclick: "luna://com.webos.applicationManager/launch", params: relaunch("cancel") }
      ]
    }, 5000).then(function () {
      TW.log("system popup shown");
    }, function (e) {
      TW.log("system popup not available: " + (e && e.message));
    });
  }

  function ask() {
    var gen = ++st.gen;
    st.phase = "idle";
    showError("");
    showButtons([]);
    setBanner("Checking " + pcName() + "…");
    api.health().then(function () {
      return api.request("GET", "/pc");
    }).then(function (r) {
      if (gen !== st.gen) return;
      if (!r.up) {
        st.phase = "done";
        setBanner(pcName() + " is already off.", "good");
        showButtons(["exit"]);
        closeSoon();
        return;
      }
      st.phase = "ask";
      setBanner("Turn off " + pcName() + "? Unsaved progress in a running game is lost.");
      showButtons(["off", "cancel"], 1);  // Cancel focused: OK on the remote must not switch it off
      nativeConfirm();
    }, function (e) {
      if (gen === st.gen) fail(e.message || String(e));
    });
  }

  function turnOff() {
    if (st.phase === "working" || st.phase === "done") return;
    var gen = ++st.gen;
    st.phase = "working";
    showError("");
    showButtons(["exit"]);
    setBanner("Turning off " + pcName() + "…");
    // The helper only has to get the command to the PC (a few seconds). Windows then needs 30-60 s;
    // the helper watches that and shows a TV notification when the PC is really off.
    api.request("POST", "/shutdown", { timeoutMs: 35000 }).then(function (r) {
      if (gen !== st.gen) return;
      st.phase = "done";
      setBanner(r.state === "off" ? pcName() + " is already off." :
        pcName() + " is shutting down. The TV will show a notification when it is off.", "good");
      showButtons(["exit"]);
      closeSoon();
    }, function (e) {
      if (gen === st.gen) fail(e.message || String(e));
    });
  }

  function launchAction() {
    try {
      var p = window.PalmSystem && window.PalmSystem.launchParams;
      p = typeof p === "string" ? (p ? JSON.parse(p) : {}) : (p || {});
      return p.action || "";
    } catch (e) {
      return "";
    }
  }

  function onLaunch() {
    var action = launchAction();
    if (action === "shutdown" && st.phase === "ask") turnOff();
    else if (action === "cancel") { if (st.phase !== "working") TW.exitApp(); }
    else if (st.phase !== "working") ask();
  }

  document.addEventListener("keydown", function (e) {
    var k = e.keyCode;
    if (k === KEY.BACK || k === KEY.ESC) {
      e.preventDefault();
      TW.exitApp();  // once sent, the shutdown goes on without the app
      return;
    }
    if (!st.buttons.length) return;
    if (k === KEY.LEFT || k === KEY.UP) { e.preventDefault(); setFocus(st.focus - 1); }
    else if (k === KEY.RIGHT || k === KEY.DOWN) { e.preventDefault(); setFocus(st.focus + 1); }
    else if (k === KEY.ENTER) { e.preventDefault(); activate(st.buttons[st.focus]); }
  });

  // The popup's buttons and pressing the icon again both arrive here.
  document.addEventListener("webOSRelaunch", onLaunch);

  window.addEventListener("unhandledrejection", function (e) {
    e.preventDefault();
    fail(String(e.reason && e.reason.message || e.reason));
  });

  function boot() {
    if (!cfg) {
      setBanner("This app is not configured", "bad");
      showError("config.js is missing or broken. Rebuild it with scripts/tv-off-app.sh.");
      showButtons(["exit"]);
      return;
    }
    appId = cfg.appId || "";
    document.title = cfg.appTitle;
    $("title").textContent = cfg.appTitle;
    $("f-helper").textContent = "Helper " + cfg.helperUrl.replace(/^https?:\/\//, "");
    $("f-meta").textContent = "v" + cfg.version;
    api = new TW.Api(cfg.helperUrl, cfg.secret);
    onLaunch();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(window.TW = window.TW || {});
