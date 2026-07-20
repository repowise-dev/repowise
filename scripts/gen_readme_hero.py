"""Generate the README composite hero SVG, in both themes.

    python scripts/gen_readme_hero.py light   ->  .github/assets/one-index.svg
    python scripts/gen_readme_hero.py dark    ->  .github/assets/one-index-dark.svg

Colours come from the resolved tokens in docs/design/theme-tokens.md; keep the two
in sync when those change. The README picks a variant with <picture> +
prefers-color-scheme.

Companion recipe for the dashboard GIFs under .github/assets/dashboard/ (ffmpeg
only, no extra dependency). README media is kept under ~2MB per asset because the
repo has no git-lfs, so every byte is permanent clone weight:

    ffmpeg -i in.gif -vf "fps=11,scale=1100:-1:flags=lanczos,split[a][b];      [a]palettegen=max_colors=112[p];[b][p]paletteuse=dither=bayer:bayer_scale=3"       -loop 0 out.gif
"""
import math, random

import os
import sys
THEME = sys.argv[1] if len(sys.argv) > 1 else "light"

if THEME == "light":
    # paper theme, resolved tokens from docs/design/theme-tokens.md
    BG_ROOT, SURFACE, INSET = "#FBF6F1", "#FFFFFF", "#F4EAE1"
    BORDER, BORDER_S = "rgba(88,67,108,.12)", "rgba(88,67,108,.18)"
    T_PRI, T_SEC, T_TER = "#241B2C", "#5E5360", "#8C7F88"
    ACCENT, FILL, PLUM = "#A16215", "#F59520", "#58436C"
    SUCCESS, WARNING, ERROR = "#1D8155", "#9A6614", "#B23A2E"
    HEAT_MID1, HEAT_MID2 = "#6B9A3D", "#C4762A"
    HAIR_XF, HAIR_F, HAIR_M, HAIR_S = ("rgba(88,67,108,.05)", "rgba(88,67,108,.15)",
                                       "rgba(88,67,108,.20)", "rgba(88,67,108,.30)")
    DOCLINE = "rgba(88,67,108,.26)"
    TRUST_BG, TRUST_BD = "rgba(29,129,85,.09)", "rgba(29,129,85,.22)"
    SAVE_BG, SAVE_BD = "rgba(245,149,32,.10)", "rgba(245,149,32,.28)"
    PILL_BG = "rgba(29,129,85,.12)"
    COMM = ["#C0641A","#58436C","#B23A2E","#6B7A3D","#B06A86","#4A5D7A",
            "#A8821F","#8A7A66","#7A2F4A","#B85A38","#2F6B66","#5E5360"]
    COMM_SOFT = ["#F7A94D","#826AA0","#CF6A55","#90A05E","#CF93AB","#71849F",
                 "#C9A544","#A89882","#9E5570","#EBA585","#558F89","#84778A"]
else:
    # plum-tinted charcoal, same token table
    BG_ROOT, SURFACE, INSET = "#17131D", "#211B29", "#110D17"
    BORDER, BORDER_S = "rgba(213,197,232,.10)", "rgba(213,197,232,.18)"
    T_PRI, T_SEC, T_TER = "#EEEAF4", "#A79DB3", "#786F84"
    ACCENT, FILL, PLUM = "#F59520", "#F59520", "#A98FC4"
    SUCCESS, WARNING, ERROR = "#34D399", "#F2A03D", "#E06A5A"
    HEAT_MID1, HEAT_MID2 = "#8FCB6B", "#EE8A50"
    HAIR_XF, HAIR_F, HAIR_M, HAIR_S = ("rgba(213,197,232,.05)", "rgba(213,197,232,.15)",
                                       "rgba(213,197,232,.20)", "rgba(213,197,232,.30)")
    DOCLINE = "rgba(213,197,232,.24)"
    TRUST_BG, TRUST_BD = "rgba(52,211,153,.10)", "rgba(52,211,153,.26)"
    SAVE_BG, SAVE_BD = "rgba(245,149,32,.12)", "rgba(245,149,32,.32)"
    PILL_BG = "rgba(52,211,153,.14)"
    COMM = ["#F59520","#A98FC4","#E06A5A","#A9BB6F","#D795B1","#8FA3C0",
            "#D9B04A","#B8A68E","#C4708F","#EBA585","#6FB3AB","#A79DB3"]
    COMM_SOFT = ["#C97A1A","#7D659C","#B04C3E","#7E8F4E","#A86A87","#66799A",
                 "#A8821F","#8A7A66","#94506A","#C4734F","#4A8780","#786F84"]

W, H = 1600, 812
PAD, GAP = 36, 18
R = 14  # card radius

out = []
def add(s): out.append(s)

def esc(s):
    return s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def card(x, y, w, h, label, kicker):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{R}" fill="{SURFACE}" stroke="{BORDER}" stroke-width="1"/>')
    add(f'<text x="{x+22}" y="{y+31}" class="lbl">{esc(label)}</text>')
    add(f'<text x="{x+w-22}" y="{y+31}" class="kick" text-anchor="end">{esc(kicker)}</text>')

def outcome(x, y, w, text):
    add(f'<text x="{x+22}" y="{y}" class="out">{esc(text)}</text>')

rng = random.Random(7)

# ============ header ======================================================
add(f'<rect width="{W}" height="{H}" fill="{BG_ROOT}"/>')
add(f'<text x="{PAD}" y="{50}" class="h1">One index. Everything your agent and your team keep asking for.</text>')
add(f'<text x="{PAD}" y="{80}" class="sub">Index once, in seconds, no LLM required. Every answer below is already computed.</text>')

TOP = 104
# ============ A: Code Health (large) ======================================
ax, ay, aw, ah = PAD, TOP, 748, 352
card(ax, ay, aw, ah, "CODE HEALTH", "25 markers · zero LLM · <30s")

# KPI chips
kpis = [("Defect risk", "7.5", "/10", WARNING), ("Maintainability", "8.6", "/10", SUCCESS), ("Perf risks", "268", "", PLUM)]
kx = ax + 22
for name, val, unit, col in kpis:
    add(f'<rect x="{kx}" y="{ay+46}" width="146" height="58" rx="9" fill="{INSET}" stroke="{BORDER}"/>')
    add(f'<text x="{kx+12}" y="{ay+66}" class="chiplbl">{esc(name)}</text>')
    add(f'<text x="{kx+12}" y="{ay+93}" class="chipval" fill="{col}">{val}<tspan class="chipunit">{unit}</tspan></text>')
    kx += 156

# trust banner
add(f'<rect x="{ax+22}" y="{ay+116}" width="{aw-44}" height="34" rx="8" fill="{TRUST_BG}" stroke="{TRUST_BD}"/>')
add(f'<circle cx="{ax+40}" cy="{ay+133}" r="4.5" fill="{SUCCESS}"/>')
add(f'<text x="{ax+54}" y="{ay+138}" class="trust">17 of the 20 lowest-health files were bug-fixed in the last 6 months, <tspan font-weight="700">3.63× the 23% baseline</tspan></text>')

# circle-packed health galaxy
gx, gy, gw, gh = ax + 22, ay + 162, 452, 170
add(f'<rect x="{gx}" y="{gy}" width="{gw}" height="{gh}" rx="9" fill="{INSET}"/>')
add(f'<clipPath id="galaxy"><rect x="{gx}" y="{gy}" width="{gw}" height="{gh}" rx="9"/></clipPath>')
add(f'<g clip-path="url(#galaxy)">')
# cluster centers
centers = [(gx+92, gy+80, 62), (gx+218, gy+62, 50), (gx+318, gy+104, 44),
           (gx+392, gy+52, 32), (gx+150, gy+140, 30)]
HEAT = [SUCCESS, HEAT_MID1, WARNING, HEAT_MID2, ERROR]
placed = []
for ci, (cx, cy, cr) in enumerate(centers):
    add(f'<circle cx="{cx}" cy="{cy}" r="{cr}" fill="{HAIR_XF}"/>')
    n = int(cr * 1.5)
    for _ in range(n):
        for _try in range(40):
            a = rng.uniform(0, math.tau); d = cr * math.sqrt(rng.uniform(0, 1)) * 0.92
            px, py = cx + math.cos(a) * d, cy + math.sin(a) * d
            r = rng.choice([2.2, 2.6, 3.1, 3.6, 4.4, 5.4])
            if all((px-qx)**2 + (py-qy)**2 > (r+qr+1.3)**2 for qx, qy, qr in placed):
                placed.append((px, py, r)); break
        else:
            continue
        # heat skews unhealthy toward cluster cores
        t = d / cr
        idx = min(4, max(0, int((1 - t) * 4.2 + rng.uniform(-1.1, 1.1))))
        op = 0.55 + 0.45 * (1 - t)
        add(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r}" fill="{HEAT[idx]}" opacity="{op:.2f}"/>')
add('</g>')
add(f'<text x="{gx+12}" y="{gy+gh-12}" class="cap">2,802 files scored</text>')

# findings list
fx = ax + 490
findings = [("CHANGE ENTROPY", "−3.0", ERROR), ("NESTED COMPLEXITY", "−2.4", ERROR),
            ("CHURN RISK", "−1.8", WARNING), ("CO-CHANGE SCATTER", "−1.5", WARNING),
            ("UNTESTED HOTSPOT", "−1.2", WARNING)]
add(f'<text x="{fx}" y="{gy+14}" class="cap">OPEN FINDINGS</text>')
fy = gy + 34
for name, delta, col in findings:
    add(f'<rect x="{fx}" y="{fy}" width="236" height="24" rx="6" fill="{INSET}"/>')
    add(f'<rect x="{fx}" y="{fy}" width="3" height="24" rx="1.5" fill="{col}"/>')
    add(f'<text x="{fx+13}" y="{fy+16}" class="find">{esc(name)}</text>')
    add(f'<text x="{fx+226}" y="{fy+16}" class="find" fill="{col}" text-anchor="end" font-weight="700">{delta}</text>')
    fy += 28

# ============ B: Graph ====================================================
bx, by, bw, bh = ax + aw + GAP, TOP, 380, 352
card(bx, by, bw, bh, "DEPENDENCY GRAPH", "16 languages")
add(f'<rect x="{bx+18}" y="{by+46}" width="{bw-36}" height="240" rx="9" fill="{INSET}"/>')
add(f'<clipPath id="gclip"><rect x="{bx+18}" y="{by+46}" width="{bw-36}" height="240" rx="9"/></clipPath>')
add('<g clip-path="url(#gclip)">')
# force-ish layout: 4 communities
gcent = [(bx+110, by+120), (bx+250, by+108), (bx+180, by+218), (bx+296, by+206)]
nodes = []
for ci, (cx, cy) in enumerate(gcent):
    hub_r = 9
    nodes.append((cx, cy, hub_r, COMM[ci], ci, True))
    for _ in range(9):
        a = rng.uniform(0, math.tau); d = rng.uniform(22, 58)
        nodes.append((cx + math.cos(a)*d, cy + math.sin(a)*d*0.8, rng.uniform(2.6, 4.4), COMM_SOFT[ci], ci, False))
# edges: leaf -> its hub, plus a few cross links
for nx, ny, nr, col, ci, is_hub in nodes:
    if is_hub: continue
    hx, hy = gcent[ci]
    add(f'<line x1="{nx:.1f}" y1="{ny:.1f}" x2="{hx}" y2="{hy}" stroke="{HAIR_F}" stroke-width="1"/>')
for i in range(len(gcent)):
    for j in range(i+1, len(gcent)):
        if rng.random() < 0.75:
            add(f'<line x1="{gcent[i][0]}" y1="{gcent[i][1]}" x2="{gcent[j][0]}" y2="{gcent[j][1]}" stroke="{HAIR_S}" stroke-width="1.6"/>')
for nx, ny, nr, col, ci, is_hub in nodes:
    sw = f' stroke="{SURFACE}" stroke-width="1.5"' if is_hub else ''
    add(f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="{nr:.1f}" fill="{col}"{sw}/>')
add('</g>')
add(f'<text x="{bx+30}" y="{by+276}" class="cap">28,126 nodes · 11 communities</text>')
outcome(bx, by+bh-42, bw, "Who calls this? What breaks if")
add(f'<text x="{bx+22}" y="{by+bh-22}" class="out">I change it?</text>')

# ============ C: Git ======================================================
cx0, cy0, cw, ch = bx + bw + GAP, TOP, W - PAD - (bx + bw + GAP), 352
card(cx0, cy0, cw, ch, "GIT HISTORY", "1,050 commits")
add(f'<rect x="{cx0+18}" y="{cy0+46}" width="{cw-36}" height="240" rx="9" fill="{INSET}"/>')
# commit-risk histogram
bars = [6, 11, 19, 28, 41, 52, 63, 58, 47, 36, 27, 19, 13, 9, 6, 4]
bw_ = (cw - 36 - 24) / len(bars)
base = cy0 + 46 + 176
for i, v in enumerate(bars):
    hgt = v * 2.3
    col = SUCCESS if i < 6 else (WARNING if i < 11 else ERROR)
    add(f'<rect x="{cx0+30+i*bw_:.1f}" y="{base-hgt:.1f}" width="{bw_-3:.1f}" height="{hgt:.1f}" rx="2.5" fill="{col}" opacity="0.82"/>')
add(f'<line x1="{cx0+30}" y1="{base+1}" x2="{cx0+cw-30}" y2="{base+1}" stroke="{HAIR_M}"/>')
add(f'<text x="{cx0+30}" y="{base+22}" class="cap">low risk</text>')
add(f'<text x="{cx0+cw-30}" y="{base+22}" class="cap" text-anchor="end">high risk</text>')
add(f'<text x="{cx0+30}" y="{cy0+72}" class="cap">CHANGE-RISK DISTRIBUTION</text>')
add(f'<text x="{cx0+30}" y="{cy0+276}" class="cap">hotspots · ownership · co-change · bus factor</text>')
outcome(cx0, cy0+ch-42, cw, "Which of these 40 files is")
add(f'<text x="{cx0+22}" y="{cy0+ch-22}" class="out">actually dangerous?</text>')

# ============ row 2 =======================================================
ROW2 = TOP + 352 + GAP
RH = H - ROW2 - PAD

# D: Docs
dx, dy, dw = PAD, ROW2, 496
card(dx, dy, dw, RH, "GENERATED DOCS", "1,274 pages")
add(f'<rect x="{dx+18}" y="{dy+46}" width="{dw-36}" height="176" rx="9" fill="{INSET}"/>')
lines = [(0.62,1),(0.92,0),(0.86,0),(0.71,0),(0.44,2),(0.88,0),(0.79,0),(0.55,0)]
ly = dy + 66
for frac, kind in lines:
    if kind == 1:
        add(f'<rect x="{dx+34}" y="{ly-4}" width="{(dw-68)*frac:.0f}" height="11" rx="3" fill="{PLUM}" opacity=".85"/>')
        ly += 22
    elif kind == 2:
        add(f'<rect x="{dx+34}" y="{ly-3}" width="{(dw-68)*frac:.0f}" height="9" rx="3" fill="{ACCENT}" opacity=".55"/>')
        ly += 20
    else:
        add(f'<rect x="{dx+34}" y="{ly}" width="{(dw-68)*frac:.0f}" height="6" rx="3" fill="{DOCLINE}"/>')
        ly += 16
add(f'<rect x="{dx+dw-116}" y="{dy+56}" width="86" height="20" rx="10" fill="{PILL_BG}"/>')
add(f'<text x="{dx+dw-73}" y="{dy+70}" class="pill" fill="{SUCCESS}" text-anchor="middle">fresh · v6</text>')
outcome(dx, dy+RH-46, dw, "Why does auth work this way?")
add(f'<text x="{dx+22}" y="{dy+RH-25}" class="outsub">Answered from the wiki, not from 12 file reads.</text>')

# E: Decisions
ex, ey, ew = dx + dw + GAP, ROW2, 496
card(ex, ey, ew, RH, "ARCHITECTURAL DECISIONS", "mined from 8 sources")
add(f'<rect x="{ex+18}" y="{ey+46}" width="{ew-36}" height="176" rx="9" fill="{INSET}"/>')
add(f'<text x="{ex+34}" y="{ey+74}" class="dec">Rebuild the KG skeleton only when the</text>')
add(f'<text x="{ex+34}" y="{ey+94}" class="dec">graph fingerprint changes</text>')
add(f'<rect x="{ex+34}" y="{ey+108}" width="{ew-104}" height="1" fill="{HAIR_F}"/>')
badges = [("Verified quote", SUCCESS), ("supersedes #418", PLUM), ("governs 24 files", ACCENT)]
bxp = ex + 34
for txt, col in badges:
    wpx = 9 + len(txt) * 6.6
    add(f'<rect x="{bxp}" y="{ey+122}" width="{wpx:.0f}" height="21" rx="10.5" fill="{SURFACE}" stroke="{BORDER_S}"/>')
    add(f'<text x="{bxp+wpx/2:.0f}" y="{ey+136}" class="pill" fill="{col}" text-anchor="middle">{esc(txt)}</text>')
    bxp += wpx + 8
add(f'<text x="{ex+34}" y="{ey+186}" class="cap">Delivered back at session start, and the moment</text>')
add(f'<text x="{ex+34}" y="{ey+202}" class="cap">your agent edits a file the decision governs.</text>')
outcome(ex, ey+RH-46, ew, "Don’t re-litigate what we settled.")
add(f'<text x="{ex+22}" y="{ey+RH-25}" class="outsub">Captured nowhere else.</text>')

# F: Agent surface
fx0, fy0 = ex + ew + GAP, ROW2
fw = W - PAD - fx0
card(fx0, fy0, fw, RH, "SERVED TO YOUR AGENT", "10 MCP tools")
tools = ["get_overview", "get_answer", "get_context", "get_symbol", "search_codebase",
         "get_risk", "get_change_risk", "get_why", "get_dead_code", "get_health"]
tx, ty = fx0 + 20, fy0 + 54
for i, t in enumerate(tools):
    wpx = 12 + len(t) * 6.9
    if tx + wpx > fx0 + fw - 20:
        tx = fx0 + 20; ty += 30
    add(f'<rect x="{tx:.0f}" y="{ty}" width="{wpx:.0f}" height="24" rx="6" fill="{INSET}" stroke="{BORDER}"/>')
    add(f'<text x="{tx+wpx/2:.0f}" y="{ty+16}" class="mono" text-anchor="middle">{esc(t)}</text>')
    tx += wpx + 8
# savings strip
sy = fy0 + 176
add(f'<text x="{fx0+20}" y="{fy0+162}" class="cap">Every response carries index age, indexed commit, and a staleness warning.</text>')
add(f'<rect x="{fx0+20}" y="{sy}" width="{fw-40}" height="46" rx="9" fill="{SAVE_BG}" stroke="{SAVE_BD}"/>')
add(f'<text x="{fx0+34}" y="{sy+20}" class="chipval" fill="{ACCENT}" font-size="19">−96%<tspan class="chipunit" fill="{T_SEC}"> context tokens</tspan></text>')
add(f'<text x="{fx0+34}" y="{sy+38}" class="cap">−70% tool calls · −89% file reads · answer quality at parity</text>')
outcome(fx0, fy0+RH-46, fw, "Claude Code, Codex, Cursor, VS Code.")
add(f'<text x="{fx0+22}" y="{fy0+RH-25}" class="outsub">Plus hooks, so it arrives unasked.</text>')

# ============ assemble ====================================================
style = f"""
  text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Helvetica, Arial, sans-serif; }}
  .h1   {{ font-size: 30px; font-weight: 700; fill: {T_PRI}; letter-spacing: -.4px; }}
  .sub  {{ font-size: 15px; fill: {T_SEC}; }}
  .lbl  {{ font-size: 11.5px; font-weight: 700; fill: {T_PRI}; letter-spacing: 1.1px; }}
  .kick {{ font-size: 11.5px; fill: {T_TER}; letter-spacing: .3px; }}
  .cap  {{ font-size: 11px; fill: {T_TER}; letter-spacing: .3px; }}
  .out  {{ font-size: 15.5px; font-weight: 650; fill: {T_PRI}; }}
  .outsub {{ font-size: 12.5px; fill: {T_SEC}; }}
  .chiplbl {{ font-size: 10.5px; fill: {T_SEC}; letter-spacing: .5px; text-transform: uppercase; }}
  .chipval {{ font-size: 24px; font-weight: 700; }}
  .chipunit {{ font-size: 13px; font-weight: 500; fill: {T_TER}; }}
  .trust {{ font-size: 12.5px; fill: {T_PRI}; }}
  .find {{ font-size: 10.5px; fill: {T_SEC}; letter-spacing: .4px; }}
  .dec  {{ font-size: 14px; font-weight: 600; fill: {T_PRI}; }}
  .pill {{ font-size: 10.5px; font-weight: 600; }}
  .mono {{ font-size: 11.5px; fill: {T_SEC}; font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace; }}
"""
svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
       f'role="img" aria-label="repowise: one index producing code health, a dependency graph, git history, generated docs, architectural decisions, and ten MCP tools">'
       f'<style>{style}</style>' + "".join(out) + '</svg>')

suffix = "" if THEME == "light" else "-dark"
path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    ".github", "assets", f"one-index{suffix}.svg")
with open(path, "w", encoding="utf-8") as f:
    f.write(svg)
print("wrote", path, len(svg), "bytes")
