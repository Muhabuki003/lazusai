#!/usr/bin/env python3
"""Patch the exported front-end bundles in frontend/ so they render correctly
on every screen size.

The pages in frontend/ are design-tool exports: each file carries the real
markup JSON-encoded inside a <script type="__bundler/template"> tag, which the
inline runtime decodes and swaps in as the live document. Editing that markup by
hand is impractical, so this script decodes the template, applies the fixes
below, and re-encodes it.

Re-run this after every re-export from the design tool. It is idempotent, and it
reports any fix whose target markup it can no longer find.

What it fixes
-------------

1. Frozen desktop dimensions. The exporter bakes the canvas size of some
   elements into their style attribute, so they keep a desktop width on a phone.
   The login page was the worst hit -- its card, form, heading, nav bar and page
   shell all carried fixed px sizes, and the nav's horizontal padding had been
   written as clamp(90px, 2.4vw, 26px), whose minimum exceeds its maximum and so
   resolves to a flat 90px on every screen. The home page hero was frozen at
   1315x850 with overflow:hidden, clipping it on any narrow screen. These
   declarations are stripped so the layouts size themselves again.

2. Image slots no longer crop. <image-slot> covers its container and clips the
   overflow, so a container whose aspect ratio differs from the source image
   silently cuts off the edges. The "Owner alerts" and "On the customer's phone"
   figures lost ~6% of their height (the top and bottom rows of chat bubbles)
   and the dashboard shot lost ~8%. Each container's aspect-ratio is set to its
   image's true ratio, so the image fits exactly at any width.

3. Unfilled image slots collapse. Three slots ship with no image at all and
   rendered as dashed grey boxes labelled "Drop image ->" on the live site. A
   slot with no src attribute now hides the figure that frames it, and reappears
   on its own as soon as the slot is filled and re-exported.

4. Dashboard header stops wrapping mid-word. At phone widths the logo, client
   label and three controls could not share one 68px row, so "<- Site" and
   "Sign out" broke across two lines. The header now wraps as two tidy rows.

5. Horizontal scroll regions read as scrollable. The dashboard tab strip gets a
   faded right edge instead of looking clipped, and the plan comparison table
   drops its 620px minimum on small screens so it fits the viewport instead of
   needing a sideways drag.

6. Two invalid declarations on the login card: `background: r` (not a colour, so
   the card's glass tint never rendered) and a `backdrop-filter` blur of 0px
   sitting next to a -webkit- prefix asking for 30px.

This replaces an earlier injected stylesheet that fought the same symptoms with
selectors matching hardcoded pixel values.
"""

import json
import pathlib
import re
import sys

FRONTEND = pathlib.Path(__file__).resolve().parent.parent / "frontend"

TEMPLATE_RE = re.compile(
    r'(<script type="__bundler/template">\s*)(.*?)(\s*</script>)', re.S
)

STYLE_ID = "lz-responsive"

# Containers whose aspect ratio does not match the image dropped into them, so
# <image-slot> crops to fill. Values on the right are the images' true ratios.
ASPECT_FIXES = {
    "aspect-ratio:16/8.4": "aspect-ratio:1200/682",  # dashboard shot, 1200x682
    "aspect-ratio:4/3": "aspect-ratio:5/4",  # owner + customer figures, 1200x960
}

# Literal edits, per page. Each is (description, old, new). A fix whose `old`
# is absent is reported rather than skipped silently, so a re-export that moves
# this markup is noticed instead of quietly shipping unpatched.
HOME_FIXES = [
    (
        "hero section frozen at 1315x850",
        "border-bottom: 1px solid #ededea; width: 1315px; height: 850px",
        "border-bottom: 1px solid #ededea",
    ),
]

# The login page's nav and shell are flex items with `margin: 0 auto`. An auto
# cross-axis margin defeats the default align-self:stretch, so they shrink to
# their content once the frozen width is gone -- hence `width: 100%` alongside
# the max-width rather than simply dropping the width.
LOGIN_FIXES = [
    (
        "empty top footer frozen at 1642px",
        "clamp(18px,2.6vw,32px); width: 1642px; height: 32px",
        "clamp(18px,2.6vw,32px)",
    ),
    (
        "nav bar frozen at 1574x55",
        "rgba(255,255,255,.7); width: 1574px; height: 55px",
        "rgba(255,255,255,.7); width: 100%",
    ),
    (
        "nav padding clamp() with min above max",
        "padding: 8px clamp(90px,2.4vw,26px)",
        "padding: 8px clamp(18px,2.4vw,26px)",
    ),
    (
        "nav gap applies 70px between wrapped rows",
        "align-items: center; gap: 70px; flex-wrap: wrap",
        "align-items: center; gap: 12px 70px; flex-wrap: wrap",
    ),
    (
        "page shell frozen at 702px wide",
        "max-width: 1320px; width: 702px; margin: 0 auto",
        "max-width: 702px; width: 100%; margin: 0 auto",
    ),
    (
        "page shell frozen at 825px tall",
        "gap: clamp(18px,2.4vw,26px); height: 825px",
        "gap: clamp(18px,2.4vw,26px)",
    ),
    (
        "sign-in card frozen at 627x744",
        "rgba(255,255,255,.8); width: 627px; height: 744px",
        "rgba(255,255,255,.8)",
    ),
    (
        "form column frozen at 443x561",
        '<div data-form="1" style="width: 443px; height: 561px">',
        '<div data-form="1">',
    ),
    (
        "heading frozen at 446x28",
        "color: #0f1620; width: 446px; height: 28px",
        "color: #0f1620",
    ),
    (
        "card background is an invalid colour",
        "background: r;",
        "background: rgba(255,255,255,.55);",
    ),
    (
        "card backdrop blur zeroed out",
        "backdrop-filter: blur(0px) saturate(98%)",
        "backdrop-filter: blur(30px) saturate(180%)",
    ),
]

# The dashboard's Calendar and Bookings panels are desktop layouts wrapped in
# overflow-x:auto -- a 880px seven-column week grid and a 760px seven-column
# table. On a phone that means a sideways drag over content that reads as cut
# off, so below 720px each swaps to a layout built for the width: the calendar
# becomes a week strip plus a single-day agenda, and the bookings table becomes
# one card per booking. Desktop rendering is untouched.
DASHBOARD_MOBILE_JS = r"""  panelWidth() {
    // Width a panel actually gets. Measured off <main>, which is always
    // visible -- an inactive panel is display:none and would measure 0.
    // clientWidth includes padding, and each panel sits behind a 1px border.
    const main = document.querySelector('main');
    if (!main) return window.innerWidth;
    const cs = getComputedStyle(main);
    return main.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 2;
  }

  responsive() {
    // Swap on the real fit rather than a guessed viewport width, so each panel
    // changes over exactly when its desktop layout would start scrolling
    // sideways. Re-render only on a crossing, so ordinary resizing (or a mobile
    // URL bar collapsing) does not rebuild the DOM.
    let cal = this.panelWidth() < CAL_MIN, bk = this.panelWidth() < BK_MIN;
    window.addEventListener('resize', () => {
      const w = this.panelWidth(), c = w < CAL_MIN, k = w < BK_MIN;
      if (c !== cal) { cal = c; this.renderCal(); }
      if (k !== bk) { bk = k; this.renderBookings(); }
    });
  }

  renderCalMobile(grid) {
    const e = this.esc, days = this.weekDates();
    const DOW = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];
    const todayKey = new Date().toDateString();
    const title = document.querySelector('[data-caltitle]');
    if (title) title.textContent = days[0].toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' – ' + days[6].toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });

    // Sample data only covers the current week, matching the desktop grid.
    const live = this.weekOffset === 0;
    const forDay = i => live ? BOOKINGS.filter(b => b.off === i).sort((a, b) => a.start.localeCompare(b.start)) : [];
    if (this.calDay == null) {
      const t = days.findIndex(d => d.toDateString() === todayKey);
      this.calDay = t === -1 ? 0 : t;
    }
    const sel = this.calDay;

    let html = '<div style="display:grid;grid-template-columns:repeat(7,1fr);gap:3px;padding:12px;border-bottom:1px solid #e6e6e2">';
    days.forEach((d, i) => {
      const isToday = d.toDateString() === todayKey, on = i === sel, n = forDay(i).length;
      html += '<button data-calday="' + i + '" style="display:flex;flex-direction:column;align-items:center;gap:3px;padding:8px 0 7px;border:1px solid ' + (on ? '#e8312a' : 'transparent') + ';background:' + (on ? '#fdecea' : 'transparent') + ';cursor:pointer;transition:background .2s,border-color .2s">'
        + '<span style="font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:.1em;color:' + (isToday ? '#e8312a' : '#b3b3ae') + '">' + DOW[i] + '</span>'
        + '<span style="font-size:14px;font-weight:600;color:' + (isToday ? '#e8312a' : '#0d0d0d') + '">' + d.getDate() + '</span>'
        + '<span style="width:5px;height:5px;border-radius:50%;background:' + (n ? '#e8312a' : 'transparent') + '"></span>'
        + '</button>';
    });
    html += '</div>';

    const list = forDay(sel);
    if (!list.length) {
      html += '<div style="padding:34px 20px;text-align:center;color:#7d7d7d;font-size:14px">Nothing booked on ' + e(days[sel].toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })) + '.</div>';
    } else {
      list.forEach(b => {
        const skin = b.status === 'completed' ? 'background:#f0f0ee;border-color:#c8c8c2' : b.status === 'pending' ? 'background:#fff8e8;border-color:#d98a00' : 'background:#fdecea;border-color:#e8312a';
        html += '<button data-block="' + b.id + '" style="display:flex;gap:12px;width:100%;text-align:left;padding:13px 16px;border:0;border-bottom:1px solid #ededea;background:none;cursor:pointer">'
          + '<span style="flex-shrink:0;width:52px;font-family:\'JetBrains Mono\',monospace;font-size:11.5px;color:#5f5f5f;padding-top:6px">' + e(b.start) + '<br><span style="color:#b3b3ae">' + e(b.end) + '</span></span>'
          + '<span style="flex:1;min-width:0;border-left:3px solid;' + skin + ';padding:8px 11px">'
          + '<span style="display:block;font-size:14px;font-weight:600">' + e(b.service) + '</span>'
          + '<span style="display:block;font-size:13px;color:#5f5f5f;margin-top:2px">' + e(b.name) + ' · ' + e(b.staff) + '</span>'
          + '</span></button>';
      });
    }

    grid.style.display = 'block';
    grid.style.minWidth = '0';
    grid.innerHTML = html;
    grid.querySelectorAll('[data-calday]').forEach(el => el.addEventListener('click', () => {
      this.calDay = +el.dataset.calday;
      this.renderCal();
    }));
    grid.querySelectorAll('[data-block]').forEach(el => el.addEventListener('click', () => this.openBooking(el.dataset.block)));
  }

  renderBookingsMobile(box) {
    const e = this.esc;
    const filter = (document.querySelector('[data-bkfilter]') || {}).value || 'upcoming';
    const todayIdx = (new Date().getDay() + 6) % 7;
    let rows = BOOKINGS.slice();
    if (filter === 'today') rows = rows.filter(b => b.off === Math.min(todayIdx, 5));
    else if (filter === 'upcoming') rows = rows.filter(b => b.status !== 'completed');
    rows.sort((a, b) => (a.off - b.off) || a.start.localeCompare(b.start));
    const monday = this.thisMonday();
    const dateOf = off => { const d = new Date(monday); d.setDate(d.getDate() + off); return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }); };
    const fmt = s => { const [h, m] = s.split(':').map(Number); return ((h % 12) || 12) + ':' + String(m).padStart(2, '0') + (h < 12 ? ' AM' : ' PM'); };
    const chip = 'display:inline-block;padding:4px 10px;font-family:\'JetBrains Mono\',monospace;font-size:10px;letter-spacing:.1em;text-transform:uppercase;';

    box.style.minWidth = '0';
    if (!rows.length) {
      box.innerHTML = '<div style="padding:36px;text-align:center;color:#7d7d7d;font-size:14px">No bookings in this view.</div>';
      return;
    }
    // One card per booking: seven table columns do not survive a phone.
    box.innerHTML = rows.map(b =>
      '<div style="padding:16px;border-bottom:1px solid #ededea">'
      + '<div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px">'
      + '<span style="font-size:14px;font-weight:600">' + e(dateOf(b.off)) + '</span>'
      + '<span style="font-family:\'JetBrains Mono\',monospace;font-size:11.5px;color:#7d7d7d;text-align:right">' + e(fmt(b.start)) + ' – ' + e(fmt(b.end)) + '</span>'
      + '</div>'
      + '<div style="margin-top:9px;font-size:14.5px">' + e(b.service) + ' <span style="font-family:\'JetBrains Mono\',monospace;font-size:12px;color:#7d7d7d">$' + b.price + '</span></div>'
      + '<div style="margin-top:4px;font-size:13.5px;color:#5f5f5f">' + e(b.name) + ' · <span style="font-family:\'JetBrains Mono\',monospace;font-size:12px">' + e(b.phone) + '</span></div>'
      + '<div style="margin-top:3px;font-size:13px;color:#7d7d7d">' + e(b.staff) + '</div>'
      + '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:13px">'
      + '<span style="' + chip + STATUS_STYLE[b.status] + '">' + e(b.status) + '</span>'
      + '<span style="' + chip + (PAY_STYLE[b.pay] || '') + '">' + e(b.pay) + '</span>'
      + '<span style="flex:1"></span>'
      + '<button data-open="' + b.id + '" style="padding:8px 15px;border:1px solid #d6d6d2;background:#fff;font-size:12.5px;font-weight:500;color:#0d0d0d;cursor:pointer">Open</button>'
      + '</div></div>').join('');
    box.querySelectorAll('[data-open]').forEach(btn => btn.addEventListener('click', () => this.openBooking(btn.dataset.open)));
  }

"""

# These four insert around anchors that survive the edit, so each carries a
# sentinel (4th field) marking work already done -- without it a second run
# would insert the methods and branches twice.
DASHBOARD_FIXES = [
    (
        "mobile renderers not yet defined on the component",
        "  renderCal() {",
        DASHBOARD_MOBILE_JS + "  renderCal() {",
        "  panelWidth() {",
    ),
    (
        "layout minimums not declared",
        "const STATUS_STYLE = {",
        "// Width each desktop layout needs before it starts scrolling sideways;\n"
        "// below these the panel renders its stacked mobile layout instead.\n"
        "const CAL_MIN = 880, BK_MIN = 760;\n\n"
        "const STATUS_STYLE = {",
        "const CAL_MIN = 880",
    ),
    (
        "resize handling not wired into mount",
        "    this.controls();\n  }",
        "    this.controls();\n    this.responsive();\n  }",
        "    this.responsive();",
    ),
    (
        "calendar has no mobile branch",
        "    const grid = document.querySelector('[data-calgrid]');\n"
        "    if (!grid) return;",
        "    const grid = document.querySelector('[data-calgrid]');\n"
        "    if (!grid) return;\n"
        "    if (this.panelWidth() < CAL_MIN) return this.renderCalMobile(grid);\n"
        "    grid.style.display = 'grid';\n"
        "    grid.style.minWidth = CAL_MIN + 'px';",
        "return this.renderCalMobile(grid);",
    ),
    (
        "bookings have no mobile branch",
        "    const box = document.querySelector('[data-bktable]');\n"
        "    if (!box) return;",
        "    const box = document.querySelector('[data-bktable]');\n"
        "    if (!box) return;\n"
        "    if (this.panelWidth() < BK_MIN) return this.renderBookingsMobile(box);\n"
        "    box.style.minWidth = BK_MIN + 'px';",
        "return this.renderBookingsMobile(box);",
    ),
]

# LazusAI provisions a dedicated iMessage number; it does not take over a number
# the business already has. These edits drop the copy that promised otherwise.
# The matching FAQ entry is removed outright by FAQ_REMOVALS below.
HOME_COPY_FIXES = [
    (
        "hero checklist claims it works with an existing number",
        "WORKS WITH YOUR NUMBER",
        "DEDICATED iMESSAGE NUMBER",
    ),
    (
        "setup step titled 'Connect your number'",
        ">Connect your number</h3>",
        ">We set up your number</h3>",
    ),
    (
        "setup step offers to keep an existing number",
        "Keep your existing business number or get a new one. "
        "We handle the iMessage setup end to end.",
        "We provision a dedicated iMessage number for your business "
        "and handle the setup end to end.",
    ),
]

PRICING_COPY_FIXES = [
    (
        "cancellation answer promises you keep your number",
        "month-to-month. You keep your number, and your conversation history "
        "and leads export in one click.",
        "month-to-month. Your conversation history and leads export in "
        "one click.",
    ),
]

# FAQ entries to drop entirely, keyed by a phrase in the question.
FAQ_REMOVALS = {
    "index.html": ["Does it work with my existing phone number?"],
    "LazusAI Site.dc.html": ["Does it work with my existing phone number?"],
}

MARKUP_FIXES = {
    "index.html": HOME_FIXES + HOME_COPY_FIXES,
    "LazusAI Site.dc.html": HOME_FIXES + HOME_COPY_FIXES,
    "Login.dc.html": LOGIN_FIXES,
    "Pricing.dc.html": PRICING_COPY_FIXES,
    "Dashboard.dc.html": DASHBOARD_FIXES,
}

RESPONSIVE_CSS = """
/* Injected by scripts/fix-frontend-responsive.py -- see that file for rationale. */

/* Decorative glows and the marquee ticker deliberately run past the viewport.
   Clip rather than hide: overflow-x:hidden would make body a scroll container
   and break the dashboard's position:sticky header. */
html, body { overflow-x: clip; }

img, svg, video, canvas { max-width: 100%; }

/* An image slot with no src is one nobody has filled in. Hide the figure that
   frames it rather than shipping a dashed "Drop image" placeholder to visitors.
   Two shapes are collapsed: a section that exists only to hold a figure, and a
   reveal-wrapper holding a figure inside a larger section. */
section:has(> div > div > image-slot:not([src])),
[data-rv]:has(> div > image-slot:not([src])) {
  display: none !important;
}

/* Long unbroken strings (emails, URLs, phone numbers) must not widen a column. */
h1, h2, h3, h4, p, td, th, li { overflow-wrap: break-word; }

@media (max-width: 720px) {
  /* Dashboard header: the logo group and the control group each stay on one
     line, and the control group drops to its own row rather than letting
     button labels break mid-word. */
  [data-header] > div:first-child {
    height: auto !important;
    flex-wrap: wrap;
    row-gap: 10px;
    padding-top: 11px;
    padding-bottom: 11px;
  }
  [data-header] a, [data-header] button, [data-clientlabel] { white-space: nowrap; }
  [data-header] > div:first-child > div:last-child { gap: 8px !important; }
  [data-header] > div:first-child > div:last-child > * {
    padding-left: 12px !important;
    padding-right: 12px !important;
    font-size: 13px !important;
  }

  /* The tab strip scrolls sideways; hide the scrollbar and fade the right edge
     so it reads as scrollable rather than as clipped content. */
  [data-tabs] { scrollbar-width: none; -ms-overflow-style: none; }
  [data-tabs]::-webkit-scrollbar { width: 0; height: 0; display: none; }
  div:has(> [data-tabs]) { position: relative; }
  div:has(> [data-tabs])::after {
    content: "";
    position: absolute;
    top: 0;
    bottom: 0;
    right: 0;
    width: 34px;
    pointer-events: none;
    background: linear-gradient(90deg, rgba(255,255,255,0), rgba(255,255,255,.92));
  }

  /* The plan comparison table holds a 620px minimum for desktop legibility.
     On a phone that turns into a sideways drag, so let it fit instead. */
  [data-compare], [data-compare] table { min-width: 0 !important; }
  [data-compare] td, [data-compare] th { padding: 11px 9px !important; }
}
"""


def strip_old_injector(page: str) -> str:
    """Remove the previous mobile-fix injector appended to the runtime script."""
    start = page.find("/*lazusai-injector*/")
    if start == -1:
        return page
    end = page.find("</script>", start)
    if end == -1:
        return page
    # Leave the runtime script itself intact; drop only the appended IIFE.
    return page[:start].rstrip().rstrip(";") + "\n" + page[end:]


def remove_faq(tpl: str, question: str) -> str:
    """Drop the whole <div data-faq> accordion entry asking `question`.

    Entries are siblings of identical shape, so an entry runs from its own
    opening tag to the next one. The last entry has no following sibling, so
    fall back to the end of its containing div.
    """
    starts = [m.start() for m in re.finditer(r'<div data-faq="1"', tpl)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else None
        if question not in tpl[start : end if end is not None else len(tpl)]:
            continue
        if end is None:
            end = tpl.index("</div>", tpl.index(question)) + len("</div>")
        return tpl[:start] + tpl[end:]
    return tpl


def patch_template(tpl: str, name: str) -> tuple[str, list[str]]:
    warnings = []

    for old, new in ASPECT_FIXES.items():
        tpl = tpl.replace(old, new)

    for question in FAQ_REMOVALS.get(name, []):
        if question in tpl:
            tpl = remove_faq(tpl, question)
            if question in tpl:
                warnings.append(f"could not remove FAQ entry: {question}")

    for fix in MARKUP_FIXES.get(name, []):
        desc, old, new = fix[0], fix[1], fix[2]
        sentinel = fix[3] if len(fix) > 3 else None
        if sentinel and sentinel in tpl:
            continue
        if old in tpl:
            tpl = tpl.replace(old, new, 1)
        elif new not in tpl:
            warnings.append(desc)

    style_tag = f'<style id="{STYLE_ID}">{RESPONSIVE_CSS}</style>'
    tpl = re.sub(r'<style id="%s">.*?</style>\s*' % STYLE_ID, "", tpl, flags=re.S)
    if "</helmet>" in tpl:
        tpl = tpl.replace("</helmet>", style_tag + "\n</helmet>", 1)
    elif "</head>" in tpl:
        tpl = tpl.replace("</head>", style_tag + "\n</head>", 1)
    else:
        raise SystemExit(f"{name}: no <helmet> or <head> to inject into")

    return tpl, warnings


def encode_template(tpl: str) -> str:
    """JSON-encode, then escape '/' in closing tags.

    A literal '</script>' inside the script body would end the tag early and
    break the page, which is why the exporter writes '<\\u002Fscript>'.
    """
    return json.dumps(tpl, ensure_ascii=False).replace("</", "<\\u002F")


def main() -> int:
    files = sorted(FRONTEND.glob("*.html"))
    if not files:
        print(f"no pages found in {FRONTEND}", file=sys.stderr)
        return 1

    stale = False
    for path in files:
        page = path.read_text(encoding="utf-8")
        match = TEMPLATE_RE.search(page)
        if not match:
            print(f"  skip {path.name}: no bundler template")
            continue

        patched, warnings = patch_template(json.loads(match.group(2)), path.name)
        page = strip_old_injector(page)

        # Re-locate the template: strip_old_injector shifts offsets.
        match = TEMPLATE_RE.search(page)
        page = page[: match.start(2)] + encode_template(patched) + page[match.end(2) :]
        path.write_text(page, encoding="utf-8")

        print(f"  patched {path.name}")
        for w in warnings:
            stale = True
            print(f"      ! target not found: {w}")

    if stale:
        print("\nSome fixes found no target -- the export likely changed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
