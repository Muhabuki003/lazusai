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

MARKUP_FIXES = {
    "index.html": HOME_FIXES,
    "LazusAI Site.dc.html": HOME_FIXES,
    "Login.dc.html": LOGIN_FIXES,
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


def patch_template(tpl: str, name: str) -> tuple[str, list[str]]:
    warnings = []

    for old, new in ASPECT_FIXES.items():
        tpl = tpl.replace(old, new)

    for desc, old, new in MARKUP_FIXES.get(name, []):
        if old in tpl:
            tpl = tpl.replace(old, new)
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
