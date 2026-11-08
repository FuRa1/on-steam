/*
 * TV app controller: runs the wake flow and draws the stage list.
 *
 * Safety rules for a TV app:
 * - every network/Luna call has a timeout, and the whole flow has a deadline;
 * - any unexpected error shows a readable screen with Retry / Exit (never a blank screen);
 * - Back always exits; the remote never gets "stuck";
 * - polling stops when the app is in the background;
 * - pressing the app icon again (webOSRelaunch) restarts the flow if it is not running.
 */
(function (TW) {
  "use strict";

  TW.log = function (msg) {
    try { console.log("[tvwake] " + msg); } catch (e) { /* ignore */ }
  };

  var KEY = { LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, ENTER: 13, BACK: 461, ESC: 27 };
  var CANCELLED = { cancelled: true };
  var BUTTONS = {
    retry: "Retry",
    again: "Start again",
    reload: "Reload app",
    exit: "Exit"
  };

  var cfg = window.TW_CONFIG;
  var api = null;
  var app = {
    gen: 0,          // bumped to cancel a running flow
    phase: "idle",   // idle | running | paused | failed | done
    stages: [],
    buttons: [],
    focus: 0,
    errorTimes: []
  };

  function $(id) { return document.getElementById(id); }

  // ---------------------------------------------------------------- stages

  function stageDefs() {
    var title = cfg.streamAppTitle;
    return [
      { id: "helper", title: "Connect to the helper" },
      { id: "pc_up", title: "Is the PC already on?" },
      { id: "wake", title: "Wake the PC" },
      { id: "boot", title: "Wait for the PC to start" },
      { id: "stream", title: "Wait for the " + title + " server" },
      { id: "launch", title: "Start " + title }
    ];
  }

  function resetStages() {
    app.stages = stageDefs().map(function (d) {
      return { id: d.id, title: d.title, state: "pending", message: "", elapsed: null, since: null };
    });
    var list = $("stages");
    list.innerHTML = "";
    app.stages.forEach(function (s) {
      var li = document.createElement("li");
      li.id = "stage-" + s.id;
      li.className = "stage pending";
      li.innerHTML =
        '<span class="icon" aria-hidden="true"></span>' +
        '<span class="text"><span class="title"></span><span class="msg"></span></span>' +
        '<span class="time"></span>';
      li.querySelector(".title").textContent = s.title;
      list.appendChild(li);
    });
  }

  function stage(id) {
    for (var i = 0; i < app.stages.length; i++) if (app.stages[i].id === id) return app.stages[i];
    return null;
  }

  function setStage(id, state, message, elapsed) {
    var s = stage(id);
    if (!s) return;
    if (state === "running" && s.state !== "running") s.started = Date.now();
    if (state !== "running" && s.state === "running" && elapsed === undefined) {
      elapsed = (Date.now() - s.started) / 1000;
    }
    s.state = state;
    if (message !== undefined) s.message = message;
    if (elapsed !== undefined && elapsed !== null) {
      s.elapsed = elapsed;
      s.since = Date.now();
    }
    drawStage(s);
  }

  function drawStage(s) {
    var li = $("stage-" + s.id);
    if (!li) return;
    li.className = "stage " + s.state;
    li.querySelector(".msg").textContent = s.message || "";
    li.querySelector(".time").textContent = formatElapsed(s);
  }

  function formatElapsed(s) {
    var secs = null;
    if (s.state === "running") {
      if (s.elapsed !== null && s.since) secs = s.elapsed + (Date.now() - s.since) / 1000;
      else if (s.started) secs = (Date.now() - s.started) / 1000;
    } else if (s.state === "ok" || s.state === "failed") {
      secs = s.elapsed;
    }
    if (secs === null || isNaN(secs)) return "";
    secs = Math.max(0, Math.floor(secs));
    return secs < 60 ? secs + " s" : Math.floor(secs / 60) + ":" + ("0" + (secs % 60)).slice(-2);
  }

  function currentStageId() {
    for (var i = 0; i < app.stages.length; i++) if (app.stages[i].state === "running") return app.stages[i].id;
    return null;
  }

  function applyJob(job) {
    (job.stages || []).forEach(function (js) {
      var s = stage(js.id);
      if (!s) return;
      if (js.state === "running" && s.state !== "running") s.started = Date.now();
      s.state = js.state;
      s.message = js.message || "";
      s.elapsed = js.elapsed_s;
      s.since = Date.now();
      drawStage(s);
    });
  }

  // ---------------------------------------------------------------- screen

  function setBanner(text, kind) {
    var b = $("banner");
    b.textContent = text;
    b.className = "banner" + (kind ? " " + kind : "");
  }

  function showError(text) {
    $("error-text").textContent = text || "";
    $("error").hidden = !text;
  }

  function showButtons(ids) {
    var box = $("buttons");
    box.innerHTML = "";
    app.buttons = ids;
    ids.forEach(function (id, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn";
      b.textContent = BUTTONS[id];
      b.addEventListener("click", function () { activate(id); });
      b.addEventListener("mouseover", function () { setFocus(i); }); // Magic Remote pointer
      box.appendChild(b);
    });
    setFocus(0);
  }

  function setFocus(i) {
    var btns = $("buttons").querySelectorAll(".btn");
    if (!btns.length) return;
    app.focus = (i + btns.length) % btns.length;
    for (var k = 0; k < btns.length; k++) btns[k].classList.toggle("focused", k === app.focus);
    btns[app.focus].focus();
  }

  function activate(id) {
    if (id === "retry" || id === "again") start();
    else if (id === "reload") window.location.reload();
    else if (id === "exit") TW.exitApp();
  }

  function setFooter() {
    $("f-helper").textContent = "Helper " + cfg.helperUrl.replace(/^https?:\/\//, "");
    $("f-meta").textContent = "v" + cfg.version + " · " + TW.cryptoImpl;
  }

  function showDevmode(d) {
    var el = $("f-devmode");
    if (!d || typeof d.remaining_s !== "number") {
      el.textContent = "Dev mode: unknown";
      return;
    }
    var hours = d.remaining_s / 3600;
    var text = hours >= 48 ? "Dev mode: " + Math.floor(hours / 24) + " days left" : "Dev mode: " + Math.floor(hours) + " h left";
    var warn = hours < 72;
    if (d.last_result && d.last_result.ok === false) {
      text += " · renewal failing";
      warn = true;
    }
    el.textContent = text;
    el.className = warn ? "warn" : "";
  }

  // ---------------------------------------------------------------- flow

  function check(gen) {
    if (gen !== app.gen) throw CANCELLED;
  }

  function sleep(ms, gen) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); }).then(function () { check(gen); });
  }

  function fail(stageId, message) {
    if (stageId) setStage(stageId, "failed", message);
    app.stages.forEach(function (s) {
      if (s.state === "pending" || s.state === "running") {
        s.state = s.state === "running" ? "failed" : "skipped";
        drawStage(s);
      }
    });
    app.phase = "failed";
    setBanner("Something needs attention", "bad");
    showError(message);
    showButtons(["retry", "exit"]);
  }

  function start() {
    var gen = ++app.gen;
    app.phase = "running";
    resetStages();
    showError("");
    showButtons([]);
    setBanner("Getting your PC ready…");
    flow(gen).catch(function (e) {
      if (e === CANCELLED || gen !== app.gen) return;
      if (e instanceof TW.ApiError) fail(currentStageId(), e.message);
      else crash(e);
    });
  }

  function flow(gen) {
    var host = cfg.helperUrl.replace(/^https?:\/\//, "");
    var jobId = null;
    var deadline = 0;
    var netErrors = 0;
    var rejoins = 0;

    function connect(attempt) {
      return api.health().catch(function (e) {
        check(gen);
        if (attempt >= 2) throw e;
        return sleep(1500, gen).then(function () { return connect(attempt + 1); });
      });
    }

    function poll() {
      return sleep(cfg.pollMs, gen).then(function () {
        if (Date.now() > deadline) {
          fail(currentStageId() || "stream",
            "This is taking much longer than expected (" + cfg.maxWaitS + " s). Check the PC, then press Retry.");
          return false;
        }
        return api.job(jobId).then(function (job) {
          check(gen);
          netErrors = 0;
          applyJob(job);
          if (job.state === "ready") return true;
          if (job.state === "failed") {
            fail(null, job.error || "The helper reported a failure.");
            return false;
          }
          return poll();
        }, function (e) {
          check(gen);
          if (e.kind === "notfound" && rejoins < 2) {
            rejoins++; // the helper restarted and forgot the job: start/join a new one
            return api.wake().then(function (r) { check(gen); jobId = r.job_id; return poll(); });
          }
          if ((e.kind === "network" || e.kind === "timeout") && ++netErrors < cfg.maxNetErrors) return poll();
          if (e.kind === "network" || e.kind === "timeout") {
            fail(currentStageId(), "Lost the connection to the helper (" + host + "). " + e.message);
            return false;
          }
          throw e;
        });
      });
    }

    setStage("helper", "running", "Contacting " + host + "…");
    return connect(0).then(function (health) {
      check(gen);
      setStage("helper", "ok", health.mock ? "Connected — MOCK MODE, no real PC is used." : "Connected.");
      setFooter();
      api.devmode().then(showDevmode, function () { showDevmode(null); });
      return api.wake();
    }, function (e) {
      check(gen);
      fail("helper", "The helper (" + host + ") is offline or unreachable. Is it powered on and connected? " +
        "Details: " + e.message);
      throw CANCELLED;
    }).then(function (r) {
      check(gen);
      jobId = r.job_id;
      deadline = Date.now() + cfg.maxWaitS * 1000;
      return poll();
    }).then(function (ready) {
      check(gen);
      setFooter();
      if (!ready) return;
      setStage("launch", "running", "Starting " + cfg.streamAppTitle + "…");
      return TW.launchApp(cfg.streamAppId, cfg.streamAppParams).then(function () {
        check(gen);
        setStage("launch", "ok", "Started.");
        app.phase = "done";
        setBanner("Have fun!", "good");
        if (cfg.closeAfterLaunch) {
          // Close ourselves so returning from the streaming app does not land here again.
          setTimeout(function () {
            if (app.gen === gen && app.phase === "done") TW.exitApp();
          }, 3000);
        } else {
          showButtons(["again", "exit"]);
        }
      }, function (e) {
        check(gen);
        fail("launch", "Could not start " + cfg.streamAppTitle + " (app id " + cfg.streamAppId + "): " +
          e.message + ". Is it installed on this TV?");
      });
    });
  }

  // ---------------------------------------------------------------- crash handling

  function crash(err) {
    var msg = err && err.message ? err.message : String(err);
    TW.log("crash: " + msg + (err && err.stack ? "\n" + err.stack : ""));
    var now = Date.now();
    app.errorTimes = app.errorTimes.filter(function (t) { return now - t < 10000; });
    app.errorTimes.push(now);
    app.gen++; // stop whatever was running
    app.phase = "failed";
    try {
      setBanner("The app hit an unexpected error", "bad");
      showError(msg);
      // In an error storm, offer only a full reload or exit.
      showButtons(app.errorTimes.length > 3 ? ["reload", "exit"] : ["retry", "exit"]);
    } catch (e) {
      // DOM is unusable: last resort, plain text.
      document.body.textContent = "Unexpected error: " + msg + " — press Back to exit.";
    }
  }

  window.addEventListener("error", function (e) {
    crash(e.error || e.message || "unknown error");
  });
  window.addEventListener("unhandledrejection", function (e) {
    if (e.reason === CANCELLED) { e.preventDefault(); return; }
    e.preventDefault();
    crash(e.reason);
  });

  // ---------------------------------------------------------------- input & lifecycle

  document.addEventListener("keydown", function (e) {
    var k = e.keyCode;
    if (k === KEY.BACK || k === KEY.ESC) {
      e.preventDefault();
      TW.exitApp(); // the helper keeps waking the PC; that is harmless
      return;
    }
    if (!app.buttons.length) return;
    if (k === KEY.LEFT || k === KEY.UP) { e.preventDefault(); setFocus(app.focus - 1); }
    else if (k === KEY.RIGHT || k === KEY.DOWN) { e.preventDefault(); setFocus(app.focus + 1); }
    else if (k === KEY.ENTER) { e.preventDefault(); activate(app.buttons[app.focus]); }
  });

  // Pressing the app icon while the app is already open.
  document.addEventListener("webOSRelaunch", function () {
    if (app.phase !== "running") start();
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (app.phase === "running") {
        app.gen++; // stop polling in the background
        app.phase = "paused";
      }
    } else if (app.phase === "paused") {
      start(); // the helper joins the job that is still running
    }
  });

  // Live elapsed-time counters.
  setInterval(function () {
    if (document.hidden) return;
    app.stages.forEach(function (s) {
      if (s.state === "running") drawStage(s);
    });
  }, 500);

  // ---------------------------------------------------------------- boot

  function boot() {
    var early = window.__twEarlyErrors || [];
    if (!cfg) {
      app.phase = "failed";
      setBanner("This app is not configured", "bad");
      showError("config.js is missing or broken. Rebuild the app with scripts/build-tv.sh and reinstall it." +
        (early.length ? " (" + early[0] + ")" : ""));
      showButtons(["exit"]);
      return;
    }
    cfg.pollMs = cfg.pollMs || 1500;
    cfg.maxWaitS = cfg.maxWaitS || 300;
    cfg.maxNetErrors = cfg.maxNetErrors || 8;
    document.title = cfg.appTitle;
    $("title").textContent = cfg.appTitle;
    api = new TW.Api(cfg.helperUrl, cfg.secret);
    setFooter();
    start();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(window.TW = window.TW || {});
