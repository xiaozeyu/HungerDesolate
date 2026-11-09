from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR, MSO_AUTO_SIZE
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement

# ---------- palette ----------
PAPER = "F4F2ED"
PAPER_2 = "FBFAF7"
MUTED_PAPER = "F0EDE7"
INK = "1D1B17"
MUTED = "6D685F"
FAINT = "9A9489"
BORDER = "E2DDD2"
GREEN = "365443"
GREEN_2 = "E5ECE6"
RED = "E5484D"
RED_DARK = "7A2521"
DARK = "0B0B0A"
DARK_2 = "171614"
TERM = "E8E4DA"
AMBER = "C9A227"
WHITE = "FFFFFF"

FONT_SANS = "PingFang SC"
FONT_SERIF = "Songti SC"
FONT_MONO = "Menlo"

SW, SH = 13.333, 7.5


def rgb(hexstr):
    return RGBColor.from_string(hexstr)


def set_run_font(run, name):
    run.font.name = name
    rPr = run._r.get_or_add_rPr()
    ea = rPr.find(qn("a:ea"))
    if ea is None:
        ea = OxmlElement("a:ea")
        rPr.append(ea)
    ea.set("typeface", name)


def add_text(slide, x, y, w, h, text, size=11, color=INK, bold=False,
             font=FONT_SANS, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
             line_spacing=1.12, space_after=0, margins=(0, 0, 0, 0), wrap=True):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = wrap
    tf.auto_size = MSO_AUTO_SIZE.NONE
    tf.margin_left = Inches(margins[0])
    tf.margin_right = Inches(margins[1])
    tf.margin_top = Inches(margins[2])
    tf.margin_bottom = Inches(margins[3])
    tf.vertical_anchor = anchor
    lines = text.split("\n") if text else [""]
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = line_spacing
        p.space_before = Pt(0)
        p.space_after = Pt(space_after)
        r = p.add_run()
        r.text = line
        set_run_font(r, font)
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = rgb(color)
    return box


def strip_style(shape):
    sp = shape._element
    old = sp.find(qn("p:style"))
    if old is not None:
        sp.remove(old)
    # Neutral style: explicitly disable theme effects so LibreOffice does not
    # fall back to a default drop shadow for autoshapes.
    style = OxmlElement("p:style")
    for tag, idx in (("a:lnRef", "0"), ("a:fillRef", "0"), ("a:effectRef", "0")):
        el = OxmlElement(tag)
        el.set("idx", idx)
        clr = OxmlElement("a:schemeClr")
        clr.set("val", "accent1")
        el.append(clr)
        style.append(el)
    font = OxmlElement("a:fontRef")
    font.set("idx", "minor")
    clr = OxmlElement("a:schemeClr")
    clr.set("val", "lt1")
    font.append(clr)
    style.append(font)
    spPr = sp.find(qn("p:spPr"))
    if spPr is not None:
        spPr.addnext(style)
    else:
        sp.append(style)
    shape.shadow.inherit = False


def add_rect(slide, x, y, w, h, fill=None, line=None, lw=1, radius=None, dash=None):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius is not None else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    if radius is not None:
        try:
            shp.adjustments[0] = radius
        except Exception:
            pass
    strip_style(shp)
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = rgb(line)
        shp.line.width = Pt(lw)
        if dash:
            shp.line.dash_style = dash
    else:
        shp.line.fill.background()
    return shp


def add_line(slide, x1, y1, x2, y2, color=INK, lw=1, dash=None):
    ln = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    ln.line.color.rgb = rgb(color)
    ln.line.width = Pt(lw)
    if dash:
        ln.line.dash_style = dash
    return ln


def add_circle(slide, x, y, d, fill=None, line=None, lw=1):
    shp = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    strip_style(shp)
    if fill:
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
    else:
        shp.fill.background()
    if line:
        shp.line.color.rgb = rgb(line)
        shp.line.width = Pt(lw)
    else:
        shp.line.fill.background()
    return shp


def add_arrow(slide, x, y, w=0.34, h=0.24, color=RED):
    shp = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    strip_style(shp)
    shp.fill.solid()
    shp.fill.fore_color.rgb = rgb(color)
    shp.line.fill.background()
    return shp


def add_chip(slide, x, y, w, h, text, fill, color, size=8, line=None):
    add_rect(slide, x, y, w, h, fill=fill, line=line, lw=0.8, radius=0.5)
    add_text(slide, x, y, w, h, text, size=size, color=color, font=FONT_MONO,
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)


def add_bg(slide, prs, color):
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    bg.shadow.inherit = False
    bg.fill.solid()
    bg.fill.fore_color.rgb = rgb(color)
    bg.line.fill.background()
    return bg


def add_header(slide, kicker, title, dark=False, tc=None):
    add_text(slide, 0.7, 0.42, 8.6, 0.25, kicker, size=8.5,
             color=RED if dark else GREEN, bold=True, font=FONT_MONO)
    add_text(slide, 0.7, 0.68, 11.2, 0.62, title, size=25,
             color=PAPER if dark else INK, bold=False, font=FONT_SANS)
    if tc:
        add_text(slide, 10.35, 0.42, 2.25, 0.25, tc, size=8.5,
                 color=FAINT if dark else MUTED, font=FONT_MONO, align=PP_ALIGN.RIGHT)


def add_footer(slide, idx, dark=False):
    add_text(slide, 0.7, 7.12, 5.0, 0.2, "荒野中枢 · 介绍片分镜 v1", size=7.5,
             color=FAINT if dark else MUTED, font=FONT_MONO)
    add_text(slide, 11.55, 7.12, 1.05, 0.2, f"{idx:02d}/18", size=7.5,
             color=FAINT if dark else MUTED, font=FONT_MONO, align=PP_ALIGN.RIGHT)


def add_notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text


def new_slide(prs, bg=PAPER, idx=None, dark=False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, prs, bg)
    if idx:
        add_footer(slide, idx, dark=dark)
    return slide


prs = Presentation()
prs.slide_width = Inches(SW)
prs.slide_height = Inches(SH)

# =====================================================================
# 01 cover
# =====================================================================
s = new_slide(prs, DARK, None, dark=True)
add_rect(s, 0.28, 0.28, 12.77, 6.94, fill=None, line=RED, lw=1.6)
add_text(s, 0.72, 0.55, 7.0, 0.25, "荒野中枢  /  产品介绍片", size=9, color=TERM, font=FONT_MONO)
add_text(s, 9.7, 0.55, 3.0, 0.25, "STORYBOARD  /  V1", size=9, color=RED, font=FONT_MONO, align=PP_ALIGN.RIGHT)
add_text(s, 0.9, 1.55, 8.4, 0.95, "荒野中枢", size=54, color=PAPER, bold=False, font=FONT_SANS)
add_text(s, 0.95, 2.62, 8.6, 0.45, "90 秒产品介绍片 · 分镜故事板", size=22, color=PAPER, font=FONT_SANS)
add_text(s, 0.95, 3.18, 8.2, 0.35, "从连环报错，到几次点击。", size=15, color=FAINT, font=FONT_SANS)
add_line(s, 0.95, 3.85, 7.9, 3.85, color=RED, lw=1.2)
add_text(s, 0.95, 4.05, 7.5, 0.3, "16:9  /  1920×1080  /  30fps  /  中文硬字幕", size=9.5, color=TERM, font=FONT_MONO)
add_text(s, 0.95, 4.38, 7.5, 0.3, "情景演绎 · 演示数据 · 1.0.5 Bate", size=9.5, color=FAINT, font=FONT_MONO)
add_text(s, 9.55, 1.35, 3.2, 1.5, "90″", size=92, color=RED, bold=True, font=FONT_MONO, align=PP_ALIGN.RIGHT)
add_text(s, 9.6, 2.92, 3.1, 0.25, "SECONDS", size=9, color=FAINT, font=FONT_MONO, align=PP_ALIGN.RIGHT)
add_circle(s, 11.55, 5.35, 0.88, fill=None, line=PAPER, lw=1.2)
add_text(s, 11.55, 5.35, 0.88, 0.88, "荒", size=20, color=PAPER, font=FONT_SERIF,
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
add_notes(s, "封面。红框是全片的核心视觉母题：它先是报错边框，最后变成产品窗口。封面右侧的 90″ 是片长，不要做成夸张的营销数字。")

# =====================================================================
# 02 structure
# =====================================================================
s = new_slide(prs, PAPER, 2)
add_header(s, "STRUCTURE  /  90 SECONDS", "先抑后扬：27 秒痛点，3 秒转折，60 秒解决", tc="00:00–01:30")
# timeline
tx, ty, tw, th = 0.7, 2.35, 11.9, 0.34
add_rect(s, tx, ty, tw, th, fill=MUTED_PAPER, line=None, radius=0.15)
x = tx
segs = [
    (27, DARK, "痛点 27s"),
    (3, RED, "3s"),
    (45, GREEN, "解决 45s"),
    (5, FAINT, "5s"),
    (10, INK, "收束 10s"),
]
for secs, color, label in segs:
    w = tw * secs / 90
    add_rect(s, x, ty, w, th, fill=color, line=None, radius=0.12)
    if w > 0.45:
        add_text(s, x, ty, w, th, label, size=8, color=PAPER, font=FONT_MONO,
                 align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    x += w
# tick labels
add_text(s, tx, ty - 0.3, 1.0, 0.22, "00:00", size=7.5, color=MUTED, font=FONT_MONO)
add_text(s, tx + tw*27/90 - 0.45, ty - 0.3, 0.9, 0.22, "00:27", size=7.5, color=RED, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_text(s, tx + tw*30/90 - 0.45, ty + 0.4, 0.9, 0.22, "00:30", size=7.5, color=MUTED, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_text(s, tx + tw*75/90 - 0.45, ty - 0.3, 0.9, 0.22, "01:15", size=7.5, color=MUTED, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_text(s, tx + tw*80/90 - 0.45, ty + 0.4, 0.9, 0.22, "01:20", size=7.5, color=MUTED, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_text(s, tx + tw - 0.5, ty - 0.3, 0.55, 0.22, "01:30", size=7.5, color=MUTED, font=FONT_MONO, align=PP_ALIGN.RIGHT)
# cards
cards = [
    ("01", "00:00–00:27", "痛点", "深夜、终端、7 次报错；抖动与红框不断升级，直到所有声音抽空。"),
    ("02", "00:27–00:30", "转折", "红框收缩、断裂、展开，变成应用窗口；颜色从红过渡到品牌绿。"),
    ("03", "00:30–01:15", "解决", "添加主机 → 指纹/登录 → 环境 → 新建 → 启动 → 世界配置。"),
    ("04", "01:15–01:20", "能力", "环境、MOD、备份、诊断五格快剪，证明不止能开服。"),
    ("05", "01:20–01:30", "收束", "终端残影消失，品牌语与尾版；把折腾留给它。"),
]
for i, (num, tc, title, desc) in enumerate(cards):
    cx = 0.7 + i * 2.4
    add_rect(s, cx, 3.45, 2.25, 2.35, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.04)
    add_rect(s, cx, 3.45, 2.25, 0.06, fill=RED if i == 0 else (GREEN if i in (2, 4) else INK), line=None)
    add_text(s, cx + 0.16, 3.6, 0.5, 0.2, num, size=7.5, color=FAINT, font=FONT_MONO)
    add_text(s, cx + 0.6, 3.6, 1.5, 0.2, tc, size=7.2, color=MUTED, font=FONT_MONO)
    add_text(s, cx + 0.16, 3.88, 1.95, 0.3, title, size=13, color=INK, bold=True)
    add_text(s, cx + 0.16, 4.25, 1.95, 1.3, desc, size=8.2, color=MUTED, line_spacing=1.25)
# key beats
add_text(s, 0.7, 6.25, 2.0, 0.22, "关键节拍", size=7.5, color=GREEN, bold=True, font=FONT_MONO)
beats = [
    ("00:07", "第一次报错"),
    ("00:24", "静音"),
    ("00:27", "转场"),
    ("00:53", "新建专服"),
    ("01:20", "收束"),
]
for i, (tc, label) in enumerate(beats):
    bx = 1.75 + i * 2.22
    add_text(s, bx, 6.25, 2.0, 0.22, tc, size=8.5, color=RED if i < 3 else GREEN, font=FONT_MONO)
    add_text(s, bx + 0.55, 6.25, 1.5, 0.22, label, size=8.5, color=MUTED, font=FONT_SANS)
add_notes(s, "结构总览。前 27 秒只做一件事：让观众想起自己折腾专服的夜晚。00:24–00:27 的静音是情绪转折，不能省。00:27 的转场是全片唯一的大转场。")

# =====================================================================
# 03 look
# =====================================================================
s = new_slide(prs, PAPER, 3)
add_header(s, "LOOK  /  CONTRAST", "冷黑终端与暖纸界面，靠一个红框连接", tc="视觉基调")
# left terminal
add_rect(s, 0.7, 1.85, 5.75, 3.45, fill=DARK, line=RED, lw=1.1, radius=0.04)
add_rect(s, 0.7, 1.85, 5.75, 0.32, fill=DARK_2, line=None, radius=0.04)
for j, c in enumerate([RED, AMBER, GREEN]):
    add_circle(s, 0.88 + j * 0.18, 1.96, 0.09, fill=c, line=None)
add_text(s, 1.5, 1.92, 4.5, 0.2, "terminal — 痛点", size=7.5, color=FAINT, font=FONT_MONO)
term_lines = [
    ("root@ubuntu:~# ", TERM, "docker compose up -d", AMBER),
    ("-bash: docker: command not found", RED, "", RED),
    ("root@ubuntu:~# ", TERM, "docker ps", AMBER),
    ("permission denied while trying to connect", TERM, "", TERM),
    ("to the Docker daemon socket", TERM, "", TERM),
    ("root@ubuntu:~# ", TERM, "docker compose config", AMBER),
    ("yaml: line 18: did not find expected key", RED, "", RED),
]
yy = 2.33
for prefix, pc, cmd, cc in term_lines:
    if cmd:
        add_text(s, 0.95, yy, 1.4, 0.18, prefix, size=8.5, color=pc, font=FONT_MONO, wrap=False)
        add_text(s, 2.18, yy, 4.3, 0.18, cmd, size=8.5, color=cc, font=FONT_MONO, wrap=False)
    else:
        add_text(s, 0.95, yy, 5.3, 0.18, prefix, size=8.5, color=pc, font=FONT_MONO, wrap=False)
    yy += 0.31
add_chip(s, 0.95, 4.78, 1.45, 0.26, "红框 = 失控", "3A1A18", RED, 7.5, line=RED_DARK)
# right app
add_rect(s, 6.85, 1.85, 5.75, 3.45, fill=PAPER_2, line=BORDER, lw=0.9, radius=0.04)
add_rect(s, 6.85, 1.85, 5.75, 0.32, fill=MUTED_PAPER, line=None, radius=0.04)
for j, c in enumerate([BORDER, BORDER, BORDER]):
    add_circle(s, 7.03 + j * 0.18, 1.96, 0.09, fill=c, line=None)
add_text(s, 7.65, 1.92, 4.5, 0.2, "app — 掌控", size=7.5, color=MUTED, font=FONT_MONO)
add_rect(s, 6.85, 2.17, 1.15, 3.13, fill=MUTED_PAPER, line=None)
add_circle(s, 7.03, 2.36, 0.26, fill=None, line=INK, lw=0.8)
add_text(s, 7.03, 2.36, 0.26, 0.26, "荒", size=6, color=INK, font=FONT_SERIF, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
add_text(s, 7.38, 2.39, 0.5, 0.18, "荒野", size=6, color=INK)
for j in range(5):
    add_rect(s, 7.02, 2.85 + j * 0.28, 0.78, 0.09, fill=BORDER if j else GREEN, line=None, radius=0.2)
add_text(s, 8.18, 2.28, 2.0, 0.22, "专服列表", size=11, color=INK, bold=True)
add_chip(s, 10.95, 2.24, 1.25, 0.26, "新建专服", GREEN, PAPER, 7.2)
for j in range(3):
    add_rect(s, 8.18, 2.65 + j * 0.5, 4.12, 0.38, fill=PAPER, line=BORDER, lw=0.6, radius=0.05)
    add_text(s, 8.3, 2.75 + j * 0.5, 1.7, 0.18, ["周末生存服", "森林长期服", "测试服"][j], size=7.2, color=INK)
    add_chip(s, 11.55, 2.72 + j * 0.5, 0.55, 0.2, ["运行中", "已停止", "运行中"][j], GREEN_2 if j != 1 else MUTED_PAPER, GREEN if j != 1 else MUTED, 6.2)
add_chip(s, 10.85, 4.78, 1.55, 0.26, "纸色 = 掌控", GREEN_2, GREEN, 7.5, line=GREEN)
# palette
add_text(s, 0.7, 5.62, 2.0, 0.22, "PALETTE", size=7.5, color=MUTED, bold=True, font=FONT_MONO)
swatches = [(RED, "E5484D"), (DARK, "0B0B0A"), (PAPER, "F4F2ED"), (GREEN, "365443"), (INK, "1D1B17")]
for i, (c, hexv) in enumerate(swatches):
    add_rect(s, 0.7 + i * 1.35, 5.9, 1.15, 0.48, fill=c, line=BORDER if c == PAPER else None, lw=0.6, radius=0.03)
    add_text(s, 0.7 + i * 1.35, 6.42, 1.15, 0.2, hexv, size=7, color=MUTED, font=FONT_MONO)
add_text(s, 7.55, 5.92, 5.0, 0.55, "字体：PingFang SC / Songti SC 标题\n等宽：Menlo；UI 数字与 ID 一律等宽", size=8.2, color=MUTED, line_spacing=1.35)
add_notes(s, "视觉对比。前半段越冷、越红，后半段越暖、越克制。转场不是换个背景，而是让红框这个母题换一种身份继续存在。")

# =====================================================================
# 04 S01
# =====================================================================
s = new_slide(prs, DARK, 4, dark=True)
add_rect(s, 0.38, 0.38, 12.57, 6.74, fill=None, line="2E2A24", lw=0.7)
add_header(s, "S01  /  00:00–00:04", "开场：用一个问题把观众拉进来", dark=True, tc="00:00:00:00")
add_text(s, 1.05, 2.18, 11.1, 0.35, "屏幕文字", size=7.5, color=FAINT, font=FONT_MONO)
add_text(s, 1.05, 2.48, 11.1, 1.3, "为了开一个 DST 专服，\n你折腾了多久？", size=38, color=PAPER, line_spacing=1.2)
add_rect(s, 6.63, 3.35, 0.16, 0.5, fill=RED, line=None)
add_text(s, 1.05, 5.15, 5.3, 0.3, "固定镜头 / 黑场 / 光标 1 秒闪烁", size=8.5, color=FAINT, font=FONT_MONO)
add_text(s, 8.05, 5.15, 4.2, 0.3, "音乐：单音钢琴 + 低频脉冲", size=8.5, color=FAINT, font=FONT_MONO, align=PP_ALIGN.RIGHT)
add_text(s, 1.05, 5.5, 11.0, 0.3, "“久？”落下的一帧硬切到终端。", size=8.5, color=RED, font=FONT_MONO)
add_notes(s, "S01：4 秒。不要加花哨开场动画，黑场 + 光标 + 两行字足够。标题用宋体或思源宋体，字距略宽。")

# =====================================================================
# 05 S02
# =====================================================================
s = new_slide(prs, DARK, 5, dark=True)
add_header(s, "S02  /  00:04–00:07", "连接：一切从 SSH 开始", dark=True, tc="00:04–00:07")
add_rect(s, 1.0, 1.9, 11.3, 3.5, fill=DARK, line="303238", lw=0.9, radius=0.03)
add_rect(s, 1.0, 1.9, 11.3, 0.34, fill=DARK_2, line=None, radius=0.03)
for j, c in enumerate([RED, AMBER, GREEN]):
    add_circle(s, 1.18 + j * 0.18, 2.02, 0.09, fill=c, line=None)
add_text(s, 1.85, 1.98, 4.0, 0.2, "终端 — 标题栏已模糊", size=7.5, color=FAINT, font=FONT_MONO)
add_text(s, 8.9, 1.98, 3.2, 0.2, "假 IP · 203.0.113.24", size=7.5, color=RED, font=FONT_MONO, align=PP_ALIGN.RIGHT)
lines = [
    ("root@ubuntu:~# ", "ssh root@203.0.113.24", AMBER),
    ("Welcome to Ubuntu 24.04.1 LTS (GNU/Linux 6.8.0-45-generic x86_64)", "", TERM),
    ("Last login: Thu Nov  6 23:41:02 2026 from 198.51.100.8", "", FAINT),
    ("root@ubuntu:~# ", "", TERM),
]
yy = 2.48
for prefix, cmd, c in lines:
    if cmd:
        add_text(s, 1.25, yy, 1.45, 0.2, prefix, size=9.5, color=TERM, font=FONT_MONO, wrap=False)
        add_text(s, 2.55, yy, 9.2, 0.2, cmd, size=9.5, color=c, font=FONT_MONO, wrap=False)
    else:
        add_text(s, 1.25, yy, 10.6, 0.2, prefix, size=9.5, color=c, font=FONT_MONO, wrap=False)
    yy += 0.38
add_line(s, 1.25, 2.72, 8.0, 2.72, color=RED, lw=0.8, dash=MSO_LINE_DASH_STYLE.DASH)
add_text(s, 1.0, 5.65, 11.2, 0.3, "镜头 100→104% 慢推 / 连接成功提示音 / 音乐 pulse 进入", size=8.5, color=FAINT, font=FONT_MONO)
add_text(s, 1.0, 6.0, 11.2, 0.3, "不出现真实服务器地址、登录用户或密钥路径。", size=8.5, color=RED, font=FONT_MONO)
add_notes(s, "S02：真实终端录制，60fps。标题栏和任何主机名信息都要模糊。IP 使用 203.0.113.24 文档保留地址。")

# =====================================================================
# 06 S03
# =====================================================================
s = new_slide(prs, DARK, 6, dark=True)
add_header(s, "S03  /  00:07–00:24", "七次报错，七次抖动", dark=True, tc="FAIL 01 → 07")
errors = [
    ("E1", "00:07.0", "docker compose up -d", "command not found", "环境没装", "FAIL 01"),
    ("E2", "00:09.4", "docker ps", "permission denied", "权限不够", "FAIL 02"),
    ("E3", "00:11.8", "docker compose up -d", "port already allocated", "端口被占", "FAIL 03"),
    ("E4", "00:14.2", "docker compose config", "yaml: did not find key", "配置写错", "FAIL 04"),
    ("E5", "00:16.6", "./start_server.sh", "No such file or directory", "脚本没了", "FAIL 05"),
    ("E6", "00:19.0", "steamcmd +login", "command not found", "依赖缺失", "FAIL 06"),
    ("E7", "00:21.4", "docker compose logs -f", "restarting / error", "无限重启", "FAIL 07"),
]
for i, (eid, tc, cmd, err, label, fail) in enumerate(errors):
    if i < 4:
        cx, cy = 0.7 + i * 3.03, 1.72
    else:
        cx, cy = 0.7 + (i - 4) * 3.03, 3.4
    add_rect(s, cx, cy, 2.85, 1.52, fill=DARK_2, line="2E2A24", lw=0.7, radius=0.03)
    add_rect(s, cx, cy, 2.85, 0.05, fill=RED, line=None)
    add_text(s, cx + 0.14, cy + 0.13, 0.35, 0.18, eid, size=8, color=RED, bold=True, font=FONT_MONO)
    add_text(s, cx + 0.52, cy + 0.13, 1.0, 0.18, tc, size=7.2, color=FAINT, font=FONT_MONO)
    add_text(s, cx + 0.14, cy + 0.38, 2.55, 0.22, cmd, size=7.4, color=AMBER, font=FONT_MONO)
    add_text(s, cx + 0.14, cy + 0.64, 2.55, 0.22, err, size=7.6, color=TERM, font=FONT_MONO)
    add_text(s, cx + 0.14, cy + 0.98, 1.6, 0.3, label, size=12, color=RED, bold=True)
    add_text(s, cx + 1.9, cy + 1.02, 0.8, 0.18, fail, size=7.2, color=RED, font=FONT_MONO, align=PP_ALIGN.RIGHT)
# summary card
add_rect(s, 9.79, 3.4, 2.85, 1.52, fill="1D0F0E", line=RED, lw=1.0, radius=0.03)
add_text(s, 9.95, 3.55, 2.5, 0.45, "12px+", size=25, color=RED, bold=True, font=FONT_MONO)
add_text(s, 9.95, 4.05, 2.5, 0.2, "抖动 / 红框 100%", size=7.5, color=TERM, font=FONT_MONO)
add_text(s, 9.95, 4.35, 2.5, 0.35, "三连抖 + 全部音效叠加", size=7.8, color=FAINT, font=FONT_MONO)
add_text(s, 0.7, 5.3, 11.9, 0.25, "节奏：2.4s / 次 → 最后一次 2.6s 三连抖", size=9, color=TERM, font=FONT_MONO)
add_text(s, 0.7, 5.65, 11.9, 0.25, "抖动 6→12px  /  红框 40→100%  /  错误音按 D–F–A 递进", size=9, color=RED, font=FONT_MONO)
add_text(s, 0.7, 6.0, 11.9, 0.25, "每一条都完整出现：命令 → 报错 → 抖动 + 红框 + 音效 → 中文标签。", size=9, color=FAINT, font=FONT_MONO)
add_notes(s, "S03：全片最重要的节奏段。7 条报错不要平均用力：E1 轻，E7 重。中文标签是给普通观众看的，英文报错不要加字幕翻译。E7 的三连抖之后立刻静音。")

# =====================================================================
# 07 S04
# =====================================================================
s = new_slide(prs, DARK, None, dark=True)
add_rect(s, 0.3, 0.3, 12.73, 6.9, fill=None, line=RED, lw=1.4)
add_text(s, 0.72, 0.55, 5.0, 0.25, "S04  /  00:24–00:27", size=8.5, color=RED, font=FONT_MONO)
add_text(s, 9.7, 0.55, 3.0, 0.25, "FAIL 07", size=8.5, color=RED, font=FONT_MONO, align=PP_ALIGN.RIGHT)
add_text(s, 1.0, 2.05, 10.8, 0.4, "折腾了半天，专服还是没开起来。", size=20, color=FAINT, font=FONT_SANS)
add_text(s, 1.0, 2.6, 10.8, 0.9, "问题不在你。", size=54, color=PAPER, font=FONT_SERIF)
add_line(s, 1.0, 3.62, 6.4, 3.62, color=RED, lw=1.2)
add_text(s, 1.0, 5.0, 6.5, 0.3, "所有声音切断 / 耳鸣 + 低频 drone / 00:26.6 红框开始收缩", size=8.5, color=FAINT, font=FONT_MONO)
add_text(s, 1.0, 5.38, 6.5, 0.3, "字幕更小、更轻；不要用感叹号。", size=8.5, color=RED, font=FONT_MONO)
add_text(s, 8.6, 4.3, 3.8, 0.3, "转场触发器：一声轻键盘", size=8, color="5A1F1F", font=FONT_MONO, align=PP_ALIGN.RIGHT)
add_notes(s, "S04：3 秒。这是全片情绪最低点，也是观众被理解的时刻。不要配煽情音乐，只要耳鸣和心跳。00:26.6 红框开始收缩，接 S05。")

# =====================================================================
# 08 S05
# =====================================================================
s = new_slide(prs, PAPER, 8)
add_header(s, "S05  /  00:27–00:30", "转场：红框不是消失，是变成产品", tc="00:27–00:30")
# frames
frames = [0.7, 3.75, 6.8, 9.85]
for fx in frames:
    add_rect(s, fx, 1.95, 2.55, 2.4, fill=DARK, line="2E2A24", lw=0.7, radius=0.02)
# step 1
add_rect(s, 0.82, 2.07, 2.31, 2.16, fill=None, line=RED, lw=2.2)
# step 2
add_rect(s, 3.87, 3.13, 2.31, 0.035, fill=RED, line=None)
# step 3
for fx, fy, dx, dy in [
    (6.92, 2.07, 1, 1), (9.33, 2.07, -1, 1),
    (6.92, 4.35, 1, -1), (9.33, 4.35, -1, -1),
]:
    add_line(s, fx, fy, fx + 0.32*dx, fy, color=RED, lw=2.0)
    add_line(s, fx, fy, fx, fy + 0.32*dy, color=RED, lw=2.0)
# step 4
add_rect(s, 10.02, 2.15, 2.2, 2.0, fill=PAPER, line=GREEN, lw=1.6, radius=0.02)
add_circle(s, 10.9, 2.48, 0.42, fill=None, line=GREEN, lw=0.8)
add_text(s, 10.9, 2.48, 0.42, 0.42, "荒", size=11, color=GREEN, font=FONT_SERIF, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
add_text(s, 10.45, 3.05, 1.4, 0.2, "荒野中枢", size=8, color=INK, bold=True, align=PP_ALIGN.CENTER)
add_text(s, 10.45, 3.3, 1.4, 0.2, "DST 专服控制台", size=6.5, color=MUTED, align=PP_ALIGN.CENTER)
add_text(s, 10.45, 3.62, 1.4, 0.2, "把部署，变成点击。", size=6.5, color=GREEN, align=PP_ALIGN.CENTER)
# arrows
for ax in [3.33, 6.38, 9.43]:
    add_arrow(s, ax, 3.02, 0.3, 0.22, RED)
# labels
labels = [("01 收缩", "0.6s"), ("02 断裂", "0.6s"), ("03 展开", "0.8s"), ("04 换色", "1.0s")]
for i, (t1, t2) in enumerate(labels):
    add_text(s, frames[i], 4.48, 2.55, 0.22, t1, size=9.5, color=INK, bold=True, align=PP_ALIGN.CENTER)
    add_text(s, frames[i], 4.72, 2.55, 0.2, t2, size=8, color=MUTED, font=FONT_MONO, align=PP_ALIGN.CENTER)
# color transition
add_text(s, 0.7, 5.2, 2.5, 0.22, "边框颜色", size=7.5, color=MUTED, bold=True, font=FONT_MONO)
add_rect(s, 0.7, 5.48, 3.7, 0.26, fill=RED, line=None, radius=0.2)
add_rect(s, 4.55, 5.48, 3.7, 0.26, fill="8A8578", line=None, radius=0.2)
add_rect(s, 8.4, 5.48, 3.7, 0.26, fill=GREEN, line=None, radius=0.2)
add_text(s, 0.7, 5.78, 3.5, 0.2, "#E5484D", size=7.2, color=MUTED, font=FONT_MONO)
add_text(s, 4.55, 5.78, 3.5, 0.2, "#8A8578", size=7.2, color=MUTED, font=FONT_MONO)
add_text(s, 8.4, 5.78, 3.5, 0.2, "#365443", size=7.2, color=MUTED, font=FONT_MONO)
add_text(s, 0.7, 6.25, 11.9, 0.3, "纸色从窗口中心向外擦除；镜头 100→96%；音效：reverse riser → impact → whoosh → 轻铃。", size=8.5, color=MUTED, font=FONT_MONO)
add_notes(s, "S05：全片唯一的大转场，建议在 AE 做 3 秒预合成。关键不是炫技，而是让观众认出：这就是刚才那个红框。四条边变成窗口四角，颜色从红换到绿，产品就出现了。")

# =====================================================================
# 09 S06
# =====================================================================
s = new_slide(prs, PAPER, 9)
add_header(s, "S06  /  00:30–00:34", "亮相：一个窗口，五个动作", tc="00:30–00:34")
# shadow
add_rect(s, 0.88, 2.05, 8.15, 4.42, fill="D8D2C6", line=None, radius=0.03)
# app window
add_rect(s, 0.8, 1.97, 8.15, 4.42, fill=PAPER_2, line=BORDER, lw=0.9, radius=0.03)
# sidebar
add_rect(s, 0.8, 1.97, 1.55, 4.42, fill=MUTED_PAPER, line=None, radius=0.03)
add_circle(s, 0.98, 2.15, 0.28, fill=None, line=INK, lw=0.8)
add_text(s, 0.98, 2.15, 0.28, 0.28, "荒", size=6.5, color=INK, font=FONT_SERIF, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
add_text(s, 1.33, 2.18, 0.8, 0.18, "荒野中枢", size=6.5, color=INK, bold=True)
add_text(s, 1.33, 2.35, 0.8, 0.16, "专服控制台", size=5.5, color=MUTED)
for j, label in enumerate(["主机列表", "专服列表", "MOD 库", "Release", "主机环境"]):
    add_text(s, 0.98, 2.8 + j * 0.34, 1.2, 0.2, label, size=6.2, color=GREEN if j == 1 else MUTED)
    if j == 1:
        add_rect(s, 0.9, 2.78 + j * 0.34, 1.35, 0.24, fill=GREEN_2, line=None, radius=0.15)
# main
add_text(s, 2.6, 2.15, 3.5, 0.2, "森林服  /  专服列表", size=6.2, color=MUTED, font=FONT_MONO)
add_text(s, 2.6, 2.4, 3.0, 0.35, "专服列表", size=14, color=INK, bold=True)
add_chip(s, 7.35, 2.42, 1.3, 0.3, "新建专服", GREEN, PAPER, 6.8)
# table
add_rect(s, 2.6, 2.95, 6.05, 0.28, fill=MUTED_PAPER, line=None, radius=0.02)
for j, h in enumerate(["名称", "模式", "洞穴", "状态", "玩家"]):
    add_text(s, 2.7 + j * 1.2, 3.01, 1.0, 0.18, h, size=5.8, color=MUTED, font=FONT_MONO)
rows = [("周末生存服", "生存", "启用", "运行中", "8/12"), ("森林长期服", "无尽", "启用", "已停止", "—"), ("测试服", "创造", "关闭", "运行中", "2/8")]
for i, row in enumerate(rows):
    y = 3.32 + i * 0.42
    add_text(s, 2.7, y, 1.5, 0.2, row[0], size=6.5, color=INK, bold=True)
    for j, v in enumerate(row[1:], start=1):
        add_text(s, 2.7 + j * 1.2, y, 0.9, 0.2, v, size=6.2, color=GREEN if v == "运行中" else MUTED)
    add_line(s, 2.6, y + 0.3, 8.65, y + 0.3, color=BORDER, lw=0.5)
add_text(s, 2.6, 5.55, 5.0, 0.2, "任务在侧栏底部继续；切页不中断。", size=6.2, color=FAINT, font=FONT_MONO)
# steps
add_text(s, 9.35, 1.97, 3.0, 0.25, "五个动作", size=8, color=GREEN, bold=True, font=FONT_MONO)
steps = [
    ("01", "添加主机", "填一次连接信息"),
    ("02", "确认连接", "指纹 + 登录验证"),
    ("03", "检查环境", "Docker、目录、权限"),
    ("04", "新建专服", "版本、洞穴、启动策略"),
    ("05", "启动", "分片、端口、玩家"),
]
for i, (n, t, d) in enumerate(steps):
    y = 2.33 + i * 0.72
    active = i == 0
    add_rect(s, 9.35, y, 3.3, 0.6, fill=GREEN_2 if active else PAPER_2, line=GREEN if active else BORDER, lw=0.7, radius=0.05)
    add_text(s, 9.48, y + 0.12, 0.4, 0.2, n, size=7, color=GREEN if active else FAINT, font=FONT_MONO)
    add_text(s, 9.88, y + 0.08, 1.4, 0.2, t, size=8.2, color=INK, bold=True)
    add_text(s, 9.88, y + 0.29, 2.5, 0.18, d, size=6.2, color=MUTED)
add_text(s, 9.35, 6.03, 3.3, 0.2, "UI 为示意；成片使用真实录屏。", size=6.5, color=FAINT, font=FONT_MONO)
add_notes(s, "S06：产品第一次完整出现。窗口缩放 90%，周围留白，让 UI 呼吸。右侧五步是观众的心理地图，后面每一步都要能对上。")

# =====================================================================
# 10 S07-S08
# =====================================================================
s = new_slide(prs, PAPER, 10)
add_header(s, "S07–S08  /  00:34–00:44", "连接：填一次，确认一次", tc="00:34–00:44")
# left form
add_rect(s, 0.7, 1.8, 5.9, 4.72, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 0.92, 1.98, 4.0, 0.28, "添加 Linux 主机", size=14, color=INK, bold=True)
add_text(s, 5.9, 2.02, 0.4, 0.2, "×", size=12, color=MUTED, align=PP_ALIGN.RIGHT)
fields = [("显示名称", "森林服"), ("主机地址", "203.0.113.24"), ("SSH 端口", "22"), ("用户名", "root"), ("认证方式", "密码"), ("密码", "••••••••")]
for i, (label, value) in enumerate(fields):
    y = 2.45 + i * 0.53
    add_text(s, 0.92, y, 1.1, 0.18, label, size=7, color=MUTED)
    add_rect(s, 2.05, y - 0.02, 4.1, 0.36, fill=PAPER, line=BORDER, lw=0.6, radius=0.03)
    add_text(s, 2.15, y + 0.06, 3.8, 0.2, value, size=8, color=INK, font=FONT_MONO if i in (1, 2, 3) else FONT_SANS)
add_chip(s, 0.92, 5.85, 1.55, 0.24, "密码只显示掩码", PAPER, RED, 6.8, line=RED)
add_chip(s, 5.2, 5.85, 1.1, 0.28, "保存主机", GREEN, PAPER, 7.2)
# right fingerprint
add_rect(s, 6.9, 1.8, 5.7, 4.72, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 7.12, 1.98, 4.2, 0.28, "确认 SSH 主机指纹", size=14, color=INK, bold=True)
add_text(s, 7.12, 2.35, 4.5, 0.2, "森林服 · 203.0.113.24:22", size=7.2, color=MUTED, font=FONT_MONO)
add_rect(s, 7.12, 2.65, 5.2, 1.0, fill=MUTED_PAPER, line=BORDER, lw=0.6, radius=0.03)
add_text(s, 7.24, 2.78, 4.9, 0.2, "新指纹 · ED25519", size=6.8, color=MUTED, font=FONT_MONO)
add_text(s, 7.24, 3.02, 4.9, 0.2, "SHA256:8f3c 2b71 9ad4 0e6c 5f18", size=7.2, color=INK, font=FONT_MONO)
add_text(s, 7.24, 3.24, 4.9, 0.2, "SHA256:8f3c 2b71 9ad4 0e6c 5f18", size=7.2, color=INK, font=FONT_MONO)
add_chip(s, 10.05, 3.78, 1.55, 0.24, "假指纹", PAPER, RED, 6.8, line=RED)
add_rect(s, 7.12, 3.95, 0.22, 0.22, fill=PAPER, line=BORDER, lw=0.8, radius=0.1)
add_text(s, 7.45, 3.95, 4.5, 0.22, "我已通过可信渠道核对上述主机指纹", size=7.2, color=INK)
add_chip(s, 10.0, 4.4, 2.2, 0.3, "确认指纹", GREEN, PAPER, 7.2)
add_text(s, 7.12, 4.9, 5.1, 0.2, "确认后，「验证登录」按钮解锁。", size=7, color=MUTED)
add_rect(s, 7.12, 5.2, 5.2, 0.55, fill=GREEN_2, line=None, radius=0.03)
add_text(s, 7.28, 5.3, 4.8, 0.2, "登录通过 · 主机未初始化", size=8, color=GREEN, bold=True)
add_text(s, 7.28, 5.5, 4.8, 0.2, "进入主机环境 →", size=6.5, color=GREEN, font=FONT_MONO)
add_text(s, 0.7, 6.75, 11.9, 0.2, "不展示真实 IP / 密钥 / 密码；指纹用假数据。", size=8, color=MUTED, font=FONT_MONO)
add_notes(s, "S07–S08：10 秒讲完“连接”。表单只展示关键字段，不要逐字录入。指纹对话框给 0.3 秒特写，让观众看到“确认”这个动作。登录成功后直接点进环境。")

# =====================================================================
# 11 S09
# =====================================================================
s = new_slide(prs, PAPER, 11)
add_header(s, "S09  /  00:44–00:53", "环境：把隐性问题一次查清", tc="00:44–00:53")
# left page
add_rect(s, 0.7, 1.8, 7.35, 4.72, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 0.92, 1.98, 3.0, 0.28, "主机环境", size=14, color=INK, bold=True)
add_chip(s, 6.35, 2.02, 1.05, 0.26, "检查通过", GREEN_2, GREEN, 6.8)
metrics = [("系统", "Ubuntu 24.04"), ("内存", "6.2 / 8 GB"), ("Docker", "可用"), ("目录", "可写")]
for i, (label, value) in enumerate(metrics):
    x = 0.92 + i * 1.78
    add_rect(s, x, 2.48, 1.66, 0.85, fill=PAPER, line=BORDER, lw=0.6, radius=0.03)
    add_text(s, x + 0.1, 2.57, 1.4, 0.18, label, size=6.5, color=MUTED)
    add_text(s, x + 0.1, 2.8, 1.45, 0.2, value, size=7.6, color=GREEN if i >= 2 else INK, bold=True)
checks = [
    ("主机身份", "已绑定 hostId"),
    ("Docker Engine", "版本 27.x · 可访问"),
    ("Docker Compose", "v2.x · 可用"),
    ("管理目录", "/opt/hunger-desolate · 权限通过"),
]
for i, (label, value) in enumerate(checks):
    y = 3.6 + i * 0.43
    add_circle(s, 0.95, y + 0.02, 0.16, fill=GREEN_2, line=None)
    add_text(s, 0.95, y + 0.02, 0.16, 0.16, "✓", size=6.5, color=GREEN, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    add_text(s, 1.2, y, 1.5, 0.2, label, size=7.2, color=INK, bold=True)
    add_text(s, 2.7, y, 4.8, 0.2, value, size=7, color=MUTED, font=FONT_MONO)
add_chip(s, 0.92, 5.95, 1.25, 0.3, "初始化", GREEN, PAPER, 7.2)
add_chip(s, 2.3, 5.95, 1.45, 0.3, "安装 Docker", PAPER, GREEN, 7.2, line=GREEN)
# right task
add_rect(s, 8.35, 1.8, 4.25, 4.72, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 8.57, 1.98, 3.5, 0.28, "初始化管理目录  ·  00:50", size=11, color=INK, bold=True)
add_text(s, 8.57, 2.3, 3.5, 0.2, "任务 · 4 个阶段", size=6.8, color=MUTED, font=FONT_MONO)
stages = ["检查主机", "准备目录", "安装 Docker", "验证环境"]
for i, st in enumerate(stages):
    y = 2.7 + i * 0.55
    add_circle(s, 8.6, y, 0.2, fill=GREEN, line=None)
    add_text(s, 8.6, y, 0.2, 0.2, "✓", size=7, color=PAPER, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    add_text(s, 8.92, y - 0.01, 3.2, 0.2, st, size=7.5, color=INK)
    add_text(s, 8.92, y + 0.18, 3.2, 0.18, "完成", size=6, color=GREEN, font=FONT_MONO)
add_rect(s, 8.57, 5.0, 3.8, 0.16, fill=GREEN_2, line=None, radius=0.2)
add_rect(s, 8.57, 5.0, 3.8, 0.16, fill=GREEN, line=None, radius=0.2)
add_text(s, 8.57, 5.25, 3.8, 0.2, "100% · 检查通过", size=7, color=GREEN, font=FONT_MONO)
add_text(s, 0.7, 6.75, 11.9, 0.2, "首次使用需准备 Release；片中不展开构建，保留“基础验证通过”状态。", size=8, color=MUTED, font=FONT_MONO)
add_notes(s, "S09：环境页信息很多，只保留“系统 / 内存 / Docker / 目录”四项和一个检查列表。Docker 安装按演示机实际情况插入 2 秒加速镜头，不要伪造不存在的步骤。")

# =====================================================================
# 12 S10
# =====================================================================
s = new_slide(prs, PAPER, 12)
add_header(s, "S10  /  00:53–01:02", "新建：名称、版本、洞穴", tc="00:53–01:02")
# left dialog
add_rect(s, 0.7, 1.8, 6.3, 4.72, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 0.92, 1.98, 4.5, 0.28, "新建专服", size=14, color=INK, bold=True)
add_text(s, 5.9, 2.02, 0.5, 0.2, "×", size=12, color=MUTED, align=PP_ALIGN.RIGHT)
add_text(s, 0.92, 2.42, 1.2, 0.18, "专服名称", size=7, color=MUTED)
add_rect(s, 2.05, 2.37, 4.25, 0.36, fill=PAPER, line=BORDER, lw=0.6, radius=0.03)
add_text(s, 2.15, 2.45, 3.9, 0.2, "周末生存服", size=8, color=INK)
add_text(s, 0.92, 3.0, 1.2, 0.18, "DST Release", size=7, color=MUTED)
add_rect(s, 2.05, 2.95, 4.25, 0.36, fill=PAPER, line=BORDER, lw=0.6, radius=0.03)
add_text(s, 2.15, 3.03, 3.1, 0.2, "DST 6xxxx · 基础验证通过", size=7.6, color=INK, font=FONT_MONO)
add_text(s, 5.55, 3.03, 0.5, 0.2, "⌄", size=9, color=MUTED)
add_text(s, 0.92, 3.58, 1.2, 0.18, "启动策略", size=7, color=MUTED)
add_rect(s, 2.05, 3.53, 0.22, 0.22, fill=GREEN, line=None, radius=0.1)
add_text(s, 2.05, 3.53, 0.22, 0.22, "✓", size=6, color=PAPER, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
add_text(s, 2.35, 3.54, 3.8, 0.2, "主机启动时自动拉起容器", size=7.2, color=INK)
add_text(s, 0.92, 4.08, 1.2, 0.18, "洞穴", size=7, color=MUTED)
add_rect(s, 2.05, 4.03, 0.22, 0.22, fill=GREEN, line=None, radius=0.1)
add_text(s, 2.05, 4.03, 0.22, 0.22, "✓", size=6, color=PAPER, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
add_text(s, 2.35, 4.04, 3.8, 0.2, "启用 Caves 分片", size=7.2, color=INK)
add_text(s, 0.92, 4.65, 5.2, 0.3, "创建后会自动检查目录、Compose 与端口。", size=6.8, color=MUTED)
add_chip(s, 0.92, 5.75, 1.45, 0.32, "创建专服", GREEN, PAPER, 7.4)
# right task
add_rect(s, 7.4, 1.8, 5.2, 2.9, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 7.62, 1.98, 3.5, 0.28, "创建容器中  ·  00:58", size=11, color=INK, bold=True)
add_chip(s, 10.95, 2.0, 1.4, 0.25, "进行中", "F3E7D3", "8B5D18", 6.8)
add_rect(s, 7.62, 2.48, 4.7, 0.16, fill=MUTED_PAPER, line=None, radius=0.2)
add_rect(s, 7.62, 2.48, 3.0, 0.16, fill=GREEN, line=None, radius=0.2)
add_text(s, 7.62, 2.72, 4.7, 0.2, "65% · 启动容器", size=6.8, color=GREEN, font=FONT_MONO)
stages = ["检查目标", "准备目录", "写入 Compose", "启动容器", "验证端口"]
for i, st in enumerate(stages):
    y = 3.12 + i * 0.26
    done = i < 3
    add_circle(s, 7.64, y, 0.12, fill=GREEN if done else MUTED_PAPER, line=None)
    add_text(s, 7.86, y - 0.03, 3.5, 0.18, st, size=6.5, color=INK if done else MUTED)
add_rect(s, 7.4, 4.95, 5.2, 1.55, fill=GREEN_2, line=None, radius=0.03)
add_text(s, 7.62, 5.15, 4.0, 0.3, "容器已验证  ·  01:01", size=12, color=GREEN, bold=True)
add_text(s, 7.62, 5.53, 4.6, 0.35, "可进入世界配置；列表已出现新专服。", size=7.2, color=MUTED)
add_text(s, 0.7, 6.75, 11.9, 0.2, "进度 200–400% 加速；结果停留 1.2s。", size=8, color=MUTED, font=FONT_MONO)
add_notes(s, "S10：新建弹窗是信息最密的一屏，只保留名称、Release、启动策略、洞穴四项。创建进度要看到阶段变化，最后“容器已验证”给足 1.5 秒。")

# =====================================================================
# 13 S11
# =====================================================================
s = new_slide(prs, PAPER, 13)
add_header(s, "S11  /  01:02–01:07", "启动：分片、端口、玩家", tc="01:02–01:07")
# summary
add_rect(s, 0.7, 1.8, 11.9, 0.86, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
stats = [("运行状态", "运行中", GREEN), ("就绪分片", "2 / 2", GREEN), ("玩家", "8 / 12", INK), ("DST 版本", "6xxxx", INK)]
for i, (label, value, color) in enumerate(stats):
    x = 0.9 + i * 2.95
    add_text(s, x, 1.95, 1.5, 0.18, label, size=6.5, color=MUTED)
    add_text(s, x, 2.16, 2.2, 0.28, value, size=13, color=color, bold=True)
    if i < 3:
        add_line(s, x + 2.6, 1.95, x + 2.6, 2.5, color=BORDER, lw=0.6)
# shards
for i, (name, port) in enumerate([("Master", "10999"), ("Caves", "11000")]):
    x = 0.7 + i * 3.05
    add_rect(s, x, 2.95, 2.9, 2.25, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
    add_text(s, x + 0.18, 3.1, 1.0, 0.2, name, size=8, color=MUTED, font=FONT_MONO)
    add_chip(s, x + 2.02, 3.08, 0.7, 0.22, "已就绪", GREEN_2, GREEN, 6)
    add_text(s, x + 0.18, 3.42, 2.5, 0.24, "容器 · 运行中", size=9, color=INK, bold=True)
    facts = [("进程", "已启动"), ("UDP", port), ("版本", "6xxxx")]
    for j, (k, v) in enumerate(facts):
        add_text(s, x + 0.18, 3.82 + j * 0.32, 0.7, 0.18, k, size=6.5, color=MUTED)
        add_text(s, x + 0.9, 3.82 + j * 0.32, 1.7, 0.18, v, size=6.8, color=GREEN if j < 2 else INK, font=FONT_MONO)
# port table
add_rect(s, 6.85, 2.95, 5.75, 2.25, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 7.05, 3.1, 2.5, 0.2, "UDP 端口", size=8, color=INK, bold=True)
for j, h in enumerate(["分片", "宿主机", "监听", "发布"]):
    add_text(s, 7.05 + j * 1.35, 3.42, 1.2, 0.18, h, size=6, color=MUTED, font=FONT_MONO)
ports = [("Master", "10999", "是", "是"), ("Caves", "11000", "是", "是")]
for i, row in enumerate(ports):
    y = 3.7 + i * 0.38
    for j, v in enumerate(row):
        add_text(s, 7.05 + j * 1.35, y, 1.2, 0.18, v, size=6.6, color=GREEN if j >= 2 else INK, font=FONT_MONO)
add_text(s, 7.05, 4.55, 5.2, 0.2, "端口就绪后再显示“运行中”。", size=6.4, color=MUTED)
# action
add_chip(s, 10.65, 5.55, 1.5, 0.32, "启动专服", GREEN, PAPER, 7.4)
add_text(s, 8.8, 5.6, 1.75, 0.2, "启动并验证中…", size=7, color=MUTED, font=FONT_MONO, align=PP_ALIGN.RIGHT)
add_text(s, 0.7, 6.75, 11.9, 0.2, "结果停留 2s；状态、端口、玩家必须来自真实采集。", size=8, color=MUTED, font=FONT_MONO)
add_notes(s, "S11：启动镜头是全片的“爽点”。状态从启动中到运行中，分片从灰到绿，端口逐行亮起。不要用假数据跳过验证。")

# =====================================================================
# 14 S12
# =====================================================================
s = new_slide(prs, PAPER, 14)
add_header(s, "S12  /  01:07–01:15", "配置：改完保存，直接应用", tc="01:07–01:15")
add_rect(s, 0.7, 1.8, 7.2, 4.72, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 0.92, 1.98, 2.0, 0.28, "世界配置", size=14, color=INK, bold=True)
add_text(s, 2.9, 2.04, 0.9, 0.2, "基本设置", size=7, color=GREEN, bold=True)
add_text(s, 3.95, 2.04, 0.7, 0.2, "世界", size=7, color=MUTED)
add_line(s, 2.9, 2.27, 3.8, 2.27, color=GREEN, lw=1.2)
rows = [("世界名称", "周末生存"), ("游戏模式", "生存"), ("最大玩家", "12"), ("语言", "中文")]
for i, (label, value) in enumerate(rows):
    y = 2.52 + i * 0.48
    add_text(s, 0.92, y, 1.1, 0.18, label, size=7, color=MUTED)
    add_rect(s, 2.15, y - 0.03, 4.95, 0.34, fill=PAPER, line=BORDER, lw=0.6, radius=0.03)
    add_text(s, 2.25, y + 0.05, 4.5, 0.2, value, size=7.6, color=INK)
add_text(s, 0.92, 4.55, 1.6, 0.2, "联机凭据", size=7, color=MUTED, bold=True)
add_rect(s, 2.15, 4.5, 4.95, 0.34, fill=MUTED_PAPER, line=BORDER, lw=0.6, radius=0.03)
add_text(s, 2.25, 4.58, 4.5, 0.2, "Token 已配置 · 留空保持当前值", size=7, color=MUTED)
add_rect(s, 2.15, 4.95, 4.95, 0.34, fill=MUTED_PAPER, line=BORDER, lw=0.6, radius=0.03)
add_text(s, 2.25, 5.03, 4.5, 0.2, "服务器密码 · 留空保持当前值", size=7, color=MUTED)
add_chip(s, 0.92, 5.85, 1.55, 0.3, "保存并应用", GREEN, PAPER, 7.2)
add_text(s, 2.65, 5.86, 4.4, 0.2, "保存期望配置 → 检查 → 部署 → 验证", size=6.4, color=MUTED, font=FONT_MONO)
# right success
add_rect(s, 8.2, 1.8, 4.4, 4.72, fill=GREEN_2, line=None, radius=0.03)
add_text(s, 8.45, 2.08, 3.8, 0.3, "世界配置已部署", size=15, color=GREEN, bold=True)
add_text(s, 8.45, 2.48, 3.8, 0.5, "已重启原运行分片；\n隔离运行验证仍待完成。", size=7.4, color=MUTED, line_spacing=1.35)
steps = [("保存期望", "完成"), ("检查配置", "完成"), ("部署分片", "完成"), ("运行验证", "待完成")]
for i, (t, st) in enumerate(steps):
    y = 3.3 + i * 0.5
    add_circle(s, 8.45, y, 0.16, fill=GREEN if st == "完成" else PAPER, line=None if st == "完成" else GREEN, lw=0.8)
    add_text(s, 8.72, y - 0.02, 2.5, 0.18, t, size=7.2, color=INK)
    add_text(s, 11.25, y - 0.02, 1.1, 0.18, st, size=6.4, color=GREEN if st == "完成" else MUTED, font=FONT_MONO, align=PP_ALIGN.RIGHT)
add_text(s, 8.45, 5.65, 3.7, 0.35, "Token / 密码全程掩码。", size=6.8, color=GREEN, font=FONT_MONO)
add_text(s, 0.7, 6.75, 11.9, 0.2, "Master/Caves 切换 0.5s 特写；保存成功但验证未完成时，如实显示待完成。", size=8, color=MUTED, font=FONT_MONO)
add_notes(s, "S12：世界配置是“部署”之后的第二层价值，证明不是只能开服。只改一个世界规则，不要把所有选项都点一遍。成功卡片要保留“隔离运行验证仍待完成”的真实状态。")

# =====================================================================
# 15 S13
# =====================================================================
s = new_slide(prs, PAPER, 15)
add_header(s, "S13  /  01:15–01:20", "能力：常用的，都在这里", tc="01:15–01:20")
tiles = [
    ("01", "主机环境", "Docker、目录、权限一次查清"),
    ("02", "MOD 库", "Workshop 链接与合集导入"),
    ("03", "专服 MOD", "批量启用、配置与应用"),
    ("04", "备份", "创建、恢复、保护点"),
    ("05", "诊断", "分片日志与失败任务"),
]
for i, (n, title, desc) in enumerate(tiles):
    x = 0.7 + i * 2.45
    add_rect(s, x, 1.9, 2.3, 3.55, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
    add_text(s, x + 0.16, 2.08, 0.5, 0.2, n, size=7, color=FAINT, font=FONT_MONO)
    # geometric icon
    cx, cy = x + 1.15, 2.65
    if i == 0:
        add_circle(s, cx - 0.22, cy - 0.22, 0.44, fill=None, line=GREEN, lw=1.2)
        add_circle(s, cx - 0.08, cy - 0.08, 0.16, fill=GREEN, line=None)
    elif i == 1:
        add_rect(s, cx - 0.24, cy - 0.24, 0.48, 0.48, fill=None, line=GREEN, lw=1.2, radius=0.08)
        add_line(s, cx - 0.16, cy, cx + 0.16, cy, color=GREEN, lw=1.2)
    elif i == 2:
        add_rect(s, cx - 0.26, cy - 0.18, 0.22, 0.22, fill=GREEN, line=None, radius=0.05)
        add_rect(s, cx + 0.04, cy + 0.0, 0.22, 0.22, fill=None, line=GREEN, lw=1.2, radius=0.05)
    elif i == 3:
        add_rect(s, cx - 0.25, cy - 0.25, 0.5, 0.5, fill=GREEN_2, line=None, radius=0.05)
        add_text(s, cx - 0.25, cy - 0.12, 0.5, 0.2, "↓", size=12, color=GREEN, align=PP_ALIGN.CENTER)
    else:
        add_line(s, cx - 0.26, cy - 0.18, cx + 0.26, cy - 0.18, color=GREEN, lw=1.2)
        add_line(s, cx - 0.26, cy, cx + 0.26, cy, color=GREEN, lw=1.2)
        add_line(s, cx - 0.26, cy + 0.18, cx + 0.1, cy + 0.18, color=GREEN, lw=1.2)
    add_text(s, x + 0.16, 3.25, 1.98, 0.28, title, size=12, color=INK, bold=True)
    add_text(s, x + 0.16, 3.62, 1.95, 0.75, desc, size=8, color=MUTED, line_spacing=1.3)
add_text(s, 0.7, 5.75, 11.9, 0.2, "每格 0.8–1s，按音乐节拍切；60s 版保留 3 格。", size=8, color=MUTED, font=FONT_MONO)
add_text(s, 0.7, 6.1, 11.9, 0.4, "能力段不要平均用力：环境与 MOD 给 1s，备份与诊断各 0.8s，最后落在“诊断 → 无失败任务”的安心感。", size=8, color=FAINT, line_spacing=1.3)
add_notes(s, "S13：5 格快剪，每格一个动作，不要塞满功能列表。字幕只保留“环境 · MOD · 备份 · 诊断”，不要逐格打字。")

# =====================================================================
# 16 S14-S15
# =====================================================================
s = new_slide(prs, DARK, 16, dark=True)
add_header(s, "S14–S15  /  01:20–01:30", "收束：把折腾留给它", dark=True, tc="01:20–01:30")
add_text(s, 0.8, 2.0, 4.2, 1.3, "把折腾\n留给它，\n把游戏\n留给你们。", size=28, color=PAPER, font=FONT_SERIF, line_spacing=1.25)
add_text(s, 0.82, 5.15, 4.2, 0.25, "终端残影被吸走 / 音乐抽简 / 尾版 4s", size=8, color=FAINT, font=FONT_MONO)
# end card
add_rect(s, 5.55, 0.95, 6.95, 5.55, fill="1A1815", line=None, radius=0.03)
add_rect(s, 5.7, 1.1, 6.65, 5.25, fill=PAPER, line=None, radius=0.02)
add_circle(s, 8.35, 1.55, 0.95, fill=None, line=GREEN, lw=1.2)
add_text(s, 8.35, 1.55, 0.95, 0.95, "荒", size=24, color=GREEN, font=FONT_SERIF, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
add_text(s, 5.95, 2.75, 6.15, 0.45, "荒野中枢", size=26, color=INK, align=PP_ALIGN.CENTER)
add_text(s, 5.95, 3.2, 6.15, 0.25, "DST 专服控制台", size=9, color=MUTED, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_line(s, 7.4, 3.58, 10.65, 3.58, color=BORDER, lw=0.8)
add_text(s, 5.95, 3.72, 6.15, 0.5, "从连上主机，到开一局。", size=12, color=GREEN, align=PP_ALIGN.CENTER)
add_text(s, 5.95, 4.45, 6.15, 0.25, "Windows / macOS  ·  v1.0.5 Bate", size=8, color=MUTED, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_text(s, 5.95, 4.95, 6.15, 0.35, "现在，开一局。", size=15, color=INK, bold=True, align=PP_ALIGN.CENTER)
add_text(s, 5.95, 5.72, 6.15, 0.2, "画面为演示数据 · 情景演绎", size=6.5, color=FAINT, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_text(s, 5.95, 5.98, 6.15, 0.2, "下载地址 / 二维码位置留空", size=6.5, color=FAINT, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_notes(s, "S14–S15：收束不要煽情。终端残影被吸走是一个小幽默，暗示“以前的折腾结束了”。尾版留白，行动语只留一句。")

# =====================================================================
# 17 delivery
# =====================================================================
s = new_slide(prs, PAPER, 17)
add_header(s, "DELIVERY", "一套素材，三个版本", tc="90s / 60s / 30s")
# 90s
add_rect(s, 0.7, 1.85, 3.75, 4.4, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 0.9, 2.0, 2.0, 0.3, "90s  ·  16:9", size=12, color=INK, bold=True)
add_rect(s, 0.9, 2.4, 3.35, 1.85, fill=DARK, line=None, radius=0.02)
add_text(s, 1.55, 2.95, 2.0, 0.4, "90″", size=30, color=RED, bold=True, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_text(s, 0.9, 4.4, 3.35, 0.2, "0:00 → 0:27 → 0:30 → 1:15 → 1:30", size=6.2, color=MUTED, font=FONT_MONO)
for i, (t, c) in enumerate([("完整情绪曲线", "主发布"), ("官网首屏", "B 站 / YouTube"), ("可裁 60s / 竖版", "母版")]):
    add_text(s, 0.9, 4.75 + i * 0.32, 3.2, 0.2, f"· {t}", size=7, color=MUTED)
# 60s
add_rect(s, 4.75, 1.85, 3.75, 4.4, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 4.95, 2.0, 2.0, 0.3, "60s  ·  16:9", size=12, color=INK, bold=True)
add_rect(s, 4.95, 2.4, 3.35, 1.85, fill=DARK, line=None, radius=0.02)
add_text(s, 5.6, 2.95, 2.0, 0.4, "60″", size=30, color=RED, bold=True, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_text(s, 4.95, 4.4, 3.35, 0.2, "0:00 → 0:18 → 0:24 → 0:48 → 1:00", size=6.2, color=MUTED, font=FONT_MONO)
for i, t in enumerate(["5 次报错，保留转场", "连接 + 新建 + 启动", "3 格能力快剪"]):
    add_text(s, 4.95, 4.75 + i * 0.32, 3.2, 0.2, f"· {t}", size=7, color=MUTED)
# 30s vertical
add_rect(s, 8.8, 1.85, 3.8, 4.4, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
add_text(s, 9.0, 2.0, 2.5, 0.3, "30s  ·  9:16", size=12, color=INK, bold=True)
add_rect(s, 10.18, 2.4, 1.05, 1.87, fill=DARK, line=None, radius=0.02)
add_text(s, 10.18, 3.05, 1.05, 0.4, "30″", size=24, color=RED, bold=True, font=FONT_MONO, align=PP_ALIGN.CENTER)
add_text(s, 9.0, 4.4, 3.35, 0.2, "0:00 → 0:05 → 0:08 → 0:17 → 0:23 → 0:30", size=5.8, color=MUTED, font=FONT_MONO)
for i, t in enumerate(["3 次报错", "局部放大，不硬裁", "抖音 / 小红书 / Shorts"]):
    add_text(s, 9.0, 4.75 + i * 0.32, 3.3, 0.2, f"· {t}", size=7, color=MUTED)
add_text(s, 0.7, 6.45, 11.9, 0.25, "母版 ProRes 422 / H.264 20 Mbps / 无字幕干净版 / SRT / 封面 3 张", size=8, color=MUTED, font=FONT_MONO)
add_notes(s, "交付三个版本。90s 是主版，60s 用于社媒和官网首屏，30s 竖版单独录一套局部构图，不要直接裁 16:9。")

# =====================================================================
# 18 checklist
# =====================================================================
s = new_slide(prs, PAPER, 18)
add_header(s, "BEFORE RECORDING", "开拍前，先确认这些", tc="PRODUCTION CHECK")
cols = [
    ("01  录制", [
        "亮色主题 / 1480×920 / 100% 缩放",
        "2560×1440 或 3024×1964 / 60fps",
        "隐藏 Dock、通知、输入法候选框",
        "每步录 2 遍，结果多停 2 秒",
        "鼠标慢速、点击前悬停 0.3s",
    ]),
    ("02  声音", [
        "BGM / SFX / 字体均有商用授权",
        "错误音原创合成，不采样系统音",
        "整体 -14 LUFS / True Peak ≤ -1",
        "错误音峰值 -8 dBFS，不刺耳",
        "结尾留 1.5s 淡出",
    ]),
    ("03  合规", [
        "无真实 IP / Token / 密码 / 私钥",
        "无竞品 logo、真实终端标题栏",
        "片尾标注“情景演绎”",
        "不把“几步”说成“一键”",
        "版本号与 package.json 一致",
    ]),
]
for i, (title, items) in enumerate(cols):
    x = 0.7 + i * 4.15
    add_rect(s, x, 1.85, 3.95, 4.1, fill=PAPER_2, line=BORDER, lw=0.8, radius=0.03)
    add_text(s, x + 0.22, 2.02, 3.4, 0.3, title, size=11, color=GREEN if i < 2 else RED, bold=True)
    for j, item in enumerate(items):
        y = 2.5 + j * 0.55
        add_rect(s, x + 0.22, y + 0.02, 0.16, 0.16, fill=None, line=BORDER, lw=0.8, radius=0.05)
        add_text(s, x + 0.5, y - 0.01, 3.25, 0.42, item, size=7.4, color=MUTED, line_spacing=1.25)
add_text(s, 0.7, 6.25, 11.9, 0.25, "下一步：录屏 → 粗剪 → 动效 → 混音 → 三版导出", size=9.5, color=INK, bold=True, font=FONT_MONO)
add_text(s, 0.7, 6.6, 11.9, 0.25, "先把 S03 的 7 条报错录齐，再录应用五步；前者决定情绪，后者决定可信度。", size=8, color=MUTED)
add_notes(s, "制作检查清单。优先录 S03 的 7 条报错和应用五步；这两组素材决定成片成败。所有演示数据、IP、指纹、Token 都要提前准备成假数据。")

out = "docs/intro-video-storyboard.pptx"
prs.save(out)
print("saved", out, "slides", len(prs.slides))
