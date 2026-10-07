#!/usr/bin/env python3
"""LAZUSAI "next pass": unlock the fixed-width/centred desktop layout and add a
freely-draggable, iMessage-styled AI chat widget to the hero, wired to the
LOCAL model endpoint (never a cloud/DeepSeek route).

Run this AFTER scripts/fix-frontend-responsive.py (that older pass decodes the
same template and strips the old injector; this pass must be last).

What this pass does
-------------------

A. Width unlock (markup level, all pages). The exporter bakes the design-canvas
   size into style attributes, so on a big monitor every page renders as a
   ~1320px centred column with white gutters. This pass:
     * drops the home hero's frozen `width:1315px; height:850px`;
     * raises every frame cap (`max-width:1320px` / `1440px`) so the frame grows
       into the screen and anchors left instead of centring;
     * un-freezes the Login shell/nav/footer/card/form boxes.
   The horizontal gutter is grown at runtime by the injected stylesheet below
   (clamp(20px,4.5vw,120px) on the frames), so content breathes on a 2560px
   monitor instead of hugging the edge.

B. Chat widget. The repo already carries one injected stylesheet per page
   (`<style id="lazusai-mobile-fix">`, appended by the `/*lazusai-injector*/`
   IIFE in the bundler runtime). This pass EXTENDS that same stylesheet and the
   same IIFE rather than adding a second one: the style element now carries the
   desktop-unlock CSS + the widget CSS + the original phone CSS.

   The widget is appended to the home hero, is docked to the hero's right side
   on load, and can be dragged anywhere by its header (pointer events, so mouse
   and touch both work). It posts to the LOCAL endpoint
       https://local-ai.bookistudios.com/chat
   with {"site":"lazusai","message":...,"session":...} -> {"ok":true,"reply":...}
   and on any failure shows a friendly line. It never falls back to a paid model
   (there is no second endpoint in the file).

Idempotent: re-running replaces both the injected block and the markup edits.
"""

import json
import pathlib
import re
import sys

FRONTEND = pathlib.Path(__file__).resolve().parent.parent / "frontend"

TEMPLATE_RE = re.compile(
    r'(<script type="__bundler/template">\s*)(.*?)(\s*</script>)', re.S
)

INJECTOR_RE = re.compile(r"/\*lazusai-injector\*/.*?(?=/\*lazusai-a11y\*/)", re.S)

# --------------------------------------------------------------------------- #
# A. markup width unlock
# --------------------------------------------------------------------------- #

# Frame caps: raised to "none" so the frame is full-bleed, and the auto-margin
# is dropped so it anchors left instead of floating as a narrow centred column.
FRAME_FIXES = [
    ("nav/main/footer cap", "max-width:1320px;margin:0 auto", "max-width:none;margin:0"),
    ("dashboard frame cap", "max-width:1440px;margin:0 auto", "max-width:none;margin:0"),
]

HOME_FIXES = [
    (
        "hero frozen at 1315x850",
        "border-bottom: 1px solid #ededea; width: 1315px; height: 850px",
        "border-bottom: 1px solid #ededea",
    ),
]

LOGIN_FIXES = [
    ("empty top footer frozen at 1642x32",
     "clamp(18px,2.6vw,32px); width: 1642px; height: 32px",
     "clamp(18px,2.6vw,32px)"),
    ("nav bar frozen at 1574x55",
     "width: 1574px; height: 55px", "width: 100%"),
    ("nav padding clamp() min above max",
     "padding: 8px clamp(90px,2.4vw,26px)", "padding: 8px clamp(20px,4vw,80px)"),
    ("nav gap applies 70px between wrapped rows",
     "align-items: center; gap: 70px; flex-wrap: wrap",
     "align-items: center; gap: 12px 70px; flex-wrap: wrap"),
    ("nav frame cap",
     "max-width: 1320px; margin: 0 auto; padding: 8px clamp(20px,4vw,80px)",
     "max-width: none; margin: 0; padding: 8px clamp(20px,4vw,80px)"),
    ("page shell frozen at 702px wide",
     "flex: 1; max-width: 1320px; width: 702px; margin: 0 auto",
     "flex: 1; max-width: none; width: 100%; margin: 0"),
    ("page shell frozen at 825px tall",
     "gap: clamp(18px,2.4vw,26px); height: 825px",
     "gap: clamp(18px,2.4vw,26px)"),
    ("sign-in card frozen at 627x744",
     "width: 627px; height: 744px", "width: 100%; max-width: 627px"),
    ("form column frozen at 443x561",
     '<div data-form="1" style="width: 443px; height: 561px">',
     '<div data-form="1">'),
    ("heading frozen at 446x28",
     "color: #0f1620; width: 446px; height: 28px", "color: #0f1620"),
    ("card background is an invalid colour", "background: r;",
     "background: rgba(255,255,255,.55);"),
    ("card backdrop blur zeroed out",
     "backdrop-filter: blur(0px) saturate(98%)",
     "backdrop-filter: blur(30px) saturate(180%)"),
]

MARKUP_FIXES = {
    "index.html": HOME_FIXES,
    "LazusAI Site.dc.html": HOME_FIXES,
    "Login.dc.html": LOGIN_FIXES,
}

# --------------------------------------------------------------------------- #
# B. injected stylesheet (extended, not replaced)
# --------------------------------------------------------------------------- #

# The phone rules already shipped by the previous pass. Kept verbatim so the
# mobile behaviour is unchanged.
MOBILE_CSS = """@media (max-width: 640px) {
  body { overflow-x: hidden !important; }
  section, header, footer, main { box-sizing: border-box !important; }
  section[style*="width: 1"], section[style*="width: 2"] { width: 100% !important; max-width: 100% !important; }
  section[style*="height: 4"], section[style*="height: 5"], section[style*="height: 6"],
  section[style*="height: 7"], section[style*="height: 8"], section[style*="height: 9"] {
    height: auto !important; min-height: 0 !important;
  }
  [style*="width: 1642px"], [style*="width: 1320px"], [style*="width: 1315px"],
  [style*="width: 702px"], [style*="width: 627px"], [style*="width: 446px"],
  [style*="width: 443px"] {
    width: 100% !important; max-width: 100% !important;
  }
  [style*="max-width: 400px"] { max-width: 100% !important; }
  [style*="width: 443px"][style*="height: 561px"] { height: auto !important; }
  h1, h2, h3, p { white-space: normal !important; overflow-wrap: break-word !important; width: auto !important; max-width: 100% !important; }
  table { width: 100% !important; max-width: 100% !important; }
  [style*="min-width: 6"], [style*="min-width: 7"], [style*="min-width: 8"], [style*="min-width: 9"] { min-width: 0 !important; }
  td, th { white-space: normal !important; overflow-wrap: break-word !important; }
}"""

# Desktop width unlock: the runtime safety net that mirrors the markup edits and
# grows the frames' horizontal gutter so a full-bleed layout does not hug the
# screen edge on a 2560px monitor.
DESKTOP_CSS = """
/* --- LAZUSAI desktop width unlock (added by fix-frontend-fullwidth-chat.py) --- */
@media (min-width: 641px) {
  main, main[data-frame], [data-nav] > div, [data-header] > div, footer > div, [data-shell] {
    max-width: none !important;
    margin-left: 0 !important;
    margin-right: 0 !important;
  }
  /* grow into the extra width instead of leaving the design's 32px edge gap */
  main, main[data-frame] {
    padding-left: clamp(20px, 4.5vw, 120px) !important;
    padding-right: clamp(20px, 4.5vw, 120px) !important;
  }
  [data-nav] > div, [data-header] > div, footer > div {
    padding-left: clamp(20px, 4.5vw, 120px) !important;
    padding-right: clamp(20px, 4.5vw, 120px) !important;
  }
  [data-shell] {
    padding-left: clamp(20px, 4.5vw, 80px) !important;
    padding-right: clamp(20px, 4.5vw, 80px) !important;
  }
  /* the home hero is a frozen canvas box */
  section[style*="width: 1315px"] { width: 100% !important; height: auto !important; }
  [style*="width: 1642px"], [style*="width: 1574px"], [style*="width: 702px"] { width: 100% !important; }
  [style*="height: 825px"], [style*="height: 850px"], [style*="height: 744px"],
  [style*="height: 561px"] { height: auto !important; }
  [style*="max-width: 1320px"], [style*="max-width:1320px"],
  [style*="max-width: 1440px"] { max-width: none !important; }
}
"""

WIDGET_CSS = """
/* --- LAZUSAI iMessage-style hero chat widget (added by fix-frontend-fullwidth-chat.py) --- */
#lzc-root{position:fixed;z-index:260;left:0;top:0;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
#lzc-card{width:min(372px,calc(100vw - 28px));background:#fff;border-radius:18px;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 26px 66px -22px rgba(13,13,13,.46),0 2px 10px rgba(13,13,13,.12);border:1px solid rgba(13,13,13,.08);transition:opacity .24s ease,transform .26s cubic-bezier(.2,.8,.2,1);transform-origin:100% 0}
#lzc-root.is-closed #lzc-card{opacity:0;transform:translateY(-10px) scale(.96);pointer-events:none}
#lzc-launch{position:absolute;left:0;top:0;display:inline-flex;align-items:center;gap:9px;padding:12px 18px;border-radius:999px;border:0;background:#0B93F6;color:#fff;font-family:inherit;font-size:14.5px;font-weight:600;cursor:pointer;box-shadow:0 16px 40px -14px rgba(11,147,246,.72);transition:opacity .24s ease,transform .26s cubic-bezier(.2,.8,.2,1);-webkit-tap-highlight-color:transparent}
#lzc-launch svg{width:18px;height:18px;flex:none}
#lzc-root:not(.is-closed) #lzc-launch{opacity:0;transform:translateY(10px) scale(.94);pointer-events:none}
#lzc-card:focus-within{border-color:rgba(11,147,246,.5)}
#lzc-head{position:relative;display:flex;align-items:center;padding:9px 12px;background:rgba(247,247,249,.96);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);border-bottom:1px solid #d8d8dc;cursor:grab;touch-action:none;user-select:none;-webkit-user-select:none}
#lzc-head.is-dragging{cursor:grabbing}
#lzc-ava{width:34px;height:34px;border-radius:50%;background:linear-gradient(160deg,#0B93F6,#0a72c8);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px;flex:none}
#lzc-title{flex:1;min-width:0;text-align:center;line-height:1.15}
#lzc-title b{display:block;font-size:14.5px;font-weight:600;color:#0d0d0d}
#lzc-title span{display:block;font-size:11.5px;color:#8a8a8e}
#lzc-close{margin-left:auto;width:30px;height:30px;flex:none;border:0;border-radius:50%;background:transparent;color:#8a8a8e;font-size:19px;line-height:1;cursor:pointer;display:flex;align-items:center;justify-content:center}
#lzc-close:hover{background:rgba(13,13,13,.06);color:#0d0d0d}
#lzc-log{background:#fff;padding:14px 12px 10px;overflow-y:auto;max-height:min(48vh,340px);display:flex;flex-direction:column;gap:7px;scrollbar-width:thin}
#lzc-log::-webkit-scrollbar{width:8px}
#lzc-log::-webkit-scrollbar-thumb{background:rgba(13,13,13,.16);border-radius:8px}
.lzc-b{max-width:80%;padding:8px 13px;font-size:15px;line-height:1.35;overflow-wrap:anywhere;white-space:pre-wrap;animation:lzc-pop .22s ease}
.lzc-in{align-self:flex-start;background:#E9E9EB;color:#0d0d0d;border-radius:18px 18px 18px 5px}
.lzc-out{align-self:flex-end;background:#0B93F6;color:#fff;border-radius:18px 18px 5px 18px}
.lzc-sys{align-self:center;background:#F2F2F7;color:#6b6b70;font-size:12.5px;border-radius:12px;padding:5px 11px;max-width:92%;text-align:center;animation:lzc-pop .22s ease}
.lzc-typing{align-self:flex-start;background:#E9E9EB;border-radius:18px 18px 18px 5px;padding:11px 14px;display:flex;gap:5px;align-items:center}
.lzc-typing i{width:7px;height:7px;border-radius:50%;background:#9a9a9e;display:block;animation:lzc-dot 1.2s infinite ease-in-out}
.lzc-typing i:nth-child(2){animation-delay:.18s}
.lzc-typing i:nth-child(3){animation-delay:.36s}
.lzc-chips{display:flex;flex-wrap:wrap;gap:6px;padding:2px 12px 10px;background:#fff}
.lzc-chips button{font:inherit;font-size:12.5px;padding:7px 12px;border:1px solid #d0d0d4;border-radius:999px;background:#fff;color:#0d0d0d;cursor:pointer}
.lzc-chips button:hover{border-color:#0B93F6;color:#0B93F6}
#lzc-form{display:flex;gap:8px;align-items:center;padding:9px 10px 10px;border-top:1px solid #d8d8dc;background:#fff}
#lzc-input{flex:1;min-width:0;border:1px solid #d0d0d4;border-radius:999px;padding:9px 14px;font-family:inherit;font-size:15px;color:#0d0d0d;background:#fff;outline:none}
#lzc-input::placeholder{color:#9a9a9e}
#lzc-input:focus{border-color:#0B93F6;box-shadow:0 0 0 3px rgba(11,147,246,.18)}
#lzc-send{width:34px;height:34px;flex:none;border:0;border-radius:50%;background:#0B93F6;color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer}
#lzc-send:disabled{opacity:.4;cursor:default}
#lzc-send svg{width:19px;height:19px}
#lzc-foot{padding:0 12px 9px;font-size:10.5px;letter-spacing:.02em;color:#a0a0a5;text-align:center;background:#fff}
.lzc-sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
#lzc-root *:focus-visible{outline:3px solid #0a84ff;outline-offset:2px;border-radius:6px}
@keyframes lzc-pop{from{opacity:0;transform:translateY(6px) scale(.98)}to{opacity:1;transform:none}}
@keyframes lzc-dot{0%,60%,100%{opacity:.35;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}
@media (prefers-reduced-motion: reduce){.lzc-b,.lzc-sys{animation:none}.lzc-typing i{animation:none;opacity:.6}}
@media (max-width: 640px){#lzc-launch{position:fixed;left:auto;right:14px;bottom:14px;top:auto}}
"""

WIDGET_JS = r"""
;(function () {
  "use strict";
  if (window.__lzc_boot) return;
  window.__lzc_boot = 1;

  var API = "https://local-ai.bookistudios.com/chat";
  var SITE = "lazusai";
  var GREETING = "Hi \uD83D\uDC4B I'm the LazusAI receptionist. Ask me anything \u2014 or tell me what your business does and I'll show you how I'd answer your customers.";
  var FAIL = "Sorry \u2014 I couldn't reach the assistant just now. Please try again in a moment, or hit Book a demo and we'll reply personally.";
  var CHIPS = ["Can you book appointments?", "How fast do you reply?", "What does it cost?"];

  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  var sid = lsGet("lzc_sid");
  if (!sid) { sid = "s" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36); lsSet("lzc_sid", sid); }

  var HEART = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.9 8.9 0 0 1-3.9-.9L3 21l1.9-4.9A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5Z"/></svg>';
  var SENDICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7Z"/></svg>';

  function el(t, c, x) { var n = document.createElement(t); if (c) n.className = c; if (x != null) n.textContent = x; return n; }

  function findHero() {
    var hs = document.querySelectorAll("h1"), i;
    for (i = 0; i < hs.length; i++) {
      if ((hs[i].textContent || "").indexOf("Never miss") > -1) {
        var s = hs[i].closest ? hs[i].closest("section") : null;
        if (s) return s;
      }
    }
    return null;
  }

  window.__lzc = { open: true, mounted: false, placed: false, requests: 0, latencyMs: null, lastReply: null, lastError: null, api: API, send: function (t) { submit(t); } };

  var root, card, log, form, input, send, launch, chips, heroEl, userMoved = false;

  function say(role, text) {
    var m = el("div", "lzc-b lzc-" + role, text);
    log.appendChild(m); log.scrollTop = log.scrollHeight; return m;
  }

  function build() {
    if (document.getElementById("lzc-root")) return true;
    var hero = findHero();
    if (!hero) return false;

    root = el("div"); root.id = "lzc-root";
    card = el("div"); card.id = "lzc-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "false");
    card.setAttribute("aria-label", "LazusAI AI receptionist chat");

    var head = el("div"); head.id = "lzc-head"; head.title = "Drag to move";
    var ava = el("div", null, "L"); ava.id = "lzc-ava"; ava.setAttribute("aria-hidden", "true");
    var ttl = el("div"); ttl.id = "lzc-title";
    ttl.appendChild(el("b", null, "LazusAI"));
    ttl.appendChild(el("span", null, "AI receptionist \u00b7 replies in seconds"));
    var closeB = el("button", null, "\u00d7"); closeB.id = "lzc-close"; closeB.type = "button"; closeB.setAttribute("aria-label", "Close chat");
    head.appendChild(ava); head.appendChild(ttl); head.appendChild(closeB);

    log = el("div"); log.id = "lzc-log";
    log.setAttribute("role", "log");
    log.setAttribute("aria-live", "polite");
    log.setAttribute("aria-relevant", "additions");
    log.tabIndex = -1;

    chips = el("div", "lzc-chips");

    form = el("form"); form.id = "lzc-form";
    var lbl = el("label", "lzc-sr", "Message the LazusAI receptionist"); lbl.setAttribute("for", "lzc-input");
    input = el("input"); input.id = "lzc-input"; input.type = "text"; input.autocomplete = "off"; input.maxLength = 600; input.placeholder = "iMessage";
    input.setAttribute("aria-label", "Message the LazusAI receptionist");
    send = el("button"); send.id = "lzc-send"; send.type = "submit"; send.setAttribute("aria-label", "Send message"); send.innerHTML = SENDICON;
    form.appendChild(lbl); form.appendChild(input); form.appendChild(send);

    var foot = el("div"); foot.id = "lzc-foot"; foot.textContent = "Runs on our own local model \u00b7 never a call center";

    card.appendChild(head); card.appendChild(log); card.appendChild(chips); card.appendChild(form); card.appendChild(foot);

    launch = el("button"); launch.id = "lzc-launch"; launch.type = "button";
    launch.setAttribute("aria-expanded", "true");
    launch.setAttribute("aria-controls", "lzc-card");
    launch.setAttribute("aria-label", "Open the LazusAI receptionist chat");
    launch.innerHTML = HEART + "<span>Ask LazusAI</span>";

    root.appendChild(card); root.appendChild(launch);
    document.body.appendChild(root);

    heroEl = hero;
    place();
    say("in", GREETING);
    renderChips(CHIPS);
    /* A 372px card covers most of a phone, so it starts collapsed there and the
       reader taps the launcher to open it. */
    if (window.innerWidth <= 640) setOpen(false);
    window.__lzc.mounted = true;
    return true;
  }

  /* Dock to the right-hand side of the hero. The hero's rect is only reliable
     once the runtime has finished laying the page out, so this runs again a
     couple of times shortly after mount -- unless the visitor has already
     dragged the card somewhere. */
  function place() {
    if (userMoved || !heroEl) return;
    if (window.innerWidth <= 640) {
      /* phone: the card opens as a sheet just above the bottom-right launcher */
      var ch = root.offsetHeight || 470;
      setPos(6, Math.max(10, window.innerHeight - ch - 12));
      return;
    }
    var w = Math.min(372, window.innerWidth - 28);
    var gutter = Math.max(24, Math.min(80, window.innerWidth * 0.03));
    var left = window.innerWidth - w - gutter;
    var top = 150;
    var hr = heroEl.getBoundingClientRect();
    if (hr && hr.width > 100) {
      top = Math.round(Math.max(84, hr.top + 96));
    }
    var saved = lsGet("lzc_pos");
    if (saved) {
      try {
        var p = JSON.parse(saved);
        if (typeof p.l === "number" && typeof p.t === "number") { left = p.l; top = p.t; }
      } catch (e) {}
    }
    setPos(left, top);
  }

  function setPos(l, t) {
    var w = root.offsetWidth || 372;
    l = Math.max(6, Math.min(l, Math.max(6, window.innerWidth - Math.min(w, window.innerWidth) - 6)));
    t = Math.max(6, Math.min(t, Math.max(6, window.innerHeight - 60)));
    root.style.left = Math.round(l) + "px";
    root.style.top = Math.round(t) + "px";
  }

  function renderChips(items) {
    chips.innerHTML = "";
    (items || []).forEach(function (q) {
      var b = el("button", null, q); b.type = "button";
      b.addEventListener("click", function () { submit(q); });
      chips.appendChild(b);
    });
    chips.style.display = (items && items.length) ? "flex" : "none";
  }

  function isOpen() { return !root.classList.contains("is-closed"); }

  function setOpen(open) {
    root.classList.toggle("is-closed", !open);
    launch.setAttribute("aria-expanded", open ? "true" : "false");
    window.__lzc.open = open;
    if (open) { place(); try { input.focus({ preventScroll: true }); } catch (e) {} }
    else { try { launch.focus({ preventScroll: true }); } catch (e) {} }
  }

  var busy = false;

  function submit(text) {
    text = (text != null ? text : input.value || "").trim();
    if (!text || busy) return;
    input.value = "";
    busy = true; send.disabled = true;
    renderChips(null);
    say("out", text);

    var typing = el("div", "lzc-b lzc-typing");
    typing.innerHTML = "<i></i><i></i><i></i>";
    log.appendChild(typing); log.scrollTop = log.scrollHeight;

    var t0 = Date.now();
    window.__lzc.requests += 1;
    window.__lzc.lastError = null;

    var ctrl = ("AbortController" in window) ? new AbortController() : null;
    var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, 60000) : null;

    fetch(API, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site: SITE, message: text, session: sid }),
      signal: ctrl ? ctrl.signal : undefined
    })
      .then(function (r) {
        return r.json().catch(function () { return { ok: false }; }).then(function (j) { j._status = r.status; return j; });
      })
      .then(function (j) {
        if (typing.parentNode) typing.remove();
        window.__lzc.latencyMs = Date.now() - t0;
        if (j && j.ok && j.reply) {
          window.__lzc.lastReply = j.reply;
          say("in", j.reply);
        } else {
          window.__lzc.lastError = (j && j.error) || ("http-" + ((j && j._status) || 0));
          say("in", FAIL);
        }
      })
      .catch(function (e) {
        if (typing.parentNode) typing.remove();
        window.__lzc.latencyMs = Date.now() - t0;
        window.__lzc.lastError = (e && e.name) || "network";
        say("in", FAIL);
      })
      .then(function () {
        if (timer) clearTimeout(timer);
        busy = false; send.disabled = false;
        try { input.focus({ preventScroll: true }); } catch (e2) {}
      });
  }

  /* ------------------------------------------------------------------ drag */
  var dragging = false, start = null;
  function onDown(e) {
    if (e.target.closest && e.target.closest("#lzc-close")) return;
    dragging = true; userMoved = true;
    start = { x: e.clientX, y: e.clientY, l: root.offsetLeft, t: root.offsetTop };
    var head = e.currentTarget;
    head.classList.add("is-dragging");
    try { head.setPointerCapture(e.pointerId); } catch (er) {}
    e.preventDefault();
  }
  function onMove(e) {
    if (!dragging) return;
    setPos(start.l + (e.clientX - start.x), start.t + (e.clientY - start.y));
    e.preventDefault();
  }
  function onUp() {
    if (!dragging) return;
    dragging = false;
    var head = document.getElementById("lzc-head");
    if (head) head.classList.remove("is-dragging");
    lsSet("lzc_pos", JSON.stringify({ l: root.offsetLeft, t: root.offsetTop }));
  }

  function wire() {
    document.getElementById("lzc-head").addEventListener("pointerdown", onDown);
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
    document.getElementById("lzc-close").addEventListener("click", function () { setOpen(false); });
    launch.addEventListener("click", function () { setOpen(!isOpen()); });
    form.addEventListener("submit", function (e) { e.preventDefault(); submit(); });
    document.addEventListener("keydown", function (e) {
      if ((e.key === "Escape" || e.key === "Esc") && isOpen()) { e.preventDefault(); setOpen(false); }
    });
    window.addEventListener("resize", function () {
      if (!userMoved) { place(); return; }
      setPos(root.offsetLeft, root.offsetTop);
    }, { passive: true });
  }

  var built = false;
  function boot() {
    if (built) return;
    if (!document.body) return;
    if (build()) {
      wire(); built = true;
      setTimeout(function () { place(); }, 1400);
      setTimeout(function () { place(); window.__lzc.placed = true; }, 3000);
    }
  }
  var tries = 0;
  var iv = setInterval(function () {
    boot();
    if (built || ++tries > 120) clearInterval(iv);
  }, 400);
  setTimeout(boot, 1500);
})();
"""


def js_string(text: str) -> str:
    """Encode CSS/JS as a JS string literal (keeps </script> from closing the tag)."""
    return json.dumps(text, ensure_ascii=False).replace("</", "<\\u002F")


def build_injector() -> str:
    css = WIDGET_CSS + DESKTOP_CSS + MOBILE_CSS
    return (
        "/*lazusai-injector*/\n"
        ";(function () {\n"
        "  var CSS = " + js_string(css) + ";\n"
        "  var inserted = false;\n"
        "  function inject() {\n"
        "    if (inserted) return true;\n"
        "    if (!document.querySelector('footer')) return false;\n"
        "    var s = document.createElement('style');\n"
        "    s.id = 'lazusai-mobile-fix';\n"
        "    s.textContent = CSS;\n"
        "    (document.head || document.documentElement).appendChild(s);\n"
        "    inserted = true;\n"
        "    return true;\n"
        "  }\n"
        "  var tries = 0;\n"
        "  var iv = setInterval(function () { if (inject() || ++tries > 80) clearInterval(iv); }, 500);\n"
        "  setTimeout(inject, 2000);\n"
        "})();\n"
        "/*lazusai-chat-widget*/\n"
        + WIDGET_JS
        + "\n"
    )


def patch_template(tpl: str, name: str):
    warnings = []
    fixes = list(FRAME_FIXES)
    if name in ("index.html", "LazusAI Site.dc.html"):
        fixes += HOME_FIXES
    if name == "Login.dc.html":
        fixes += [(d, o, n) for d, o, n in LOGIN_FIXES]
    for desc, old, new in fixes:
        if old in tpl:
            tpl = tpl.replace(old, new)
        elif new not in tpl:
            warnings.append(desc)
    return tpl, warnings


def main() -> int:
    files = sorted(FRONTEND.glob("*.html"))
    stale = False
    for path in files:
        page = path.read_text(encoding="utf-8")
        changed = []

        m = TEMPLATE_RE.search(page)
        if not m:
            print(f"  skip {path.name}: no bundler template")
            continue
        tpl, warnings = patch_template(json.loads(m.group(2)), path.name)
        page = page[: m.start(2)] + json.dumps(tpl, ensure_ascii=False).replace("</", "<\\u002F") + page[m.end(2):]
        if tpl != json.loads(m.group(2)):
            changed.append("markup")

        new_inj = build_injector()
        if INJECTOR_RE.search(page):
            page = INJECTOR_RE.sub(lambda _: new_inj.rstrip("\n"), page, count=1)
            changed.append("injector")

        path.write_text(page, encoding="utf-8")
        print(f"  patched {path.name}: {', '.join(changed) or 'no change'}")
        for w in warnings:
            stale = True
            print(f"      ! target not found: {w}")

    if stale:
        print("\nSome fixes found no target -- the export likely changed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
