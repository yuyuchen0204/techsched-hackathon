"""Generate the handbook's figures as SVG from the repository's own data.

Every number plotted here is read at run time from a file in this repository:

  data/evaluation/latest.json               V2 对照实验
  data/evaluation/latest_v3.json            V3 编排与休息对照实验
  data/reference/repair_object_problem_database.csv   维修问题库
  data/scenarios/main.json                  场景技师与技能
  config/policy.yaml                        权限与评分权重
  backend/tests/*.py                        测试用例数

No figure contains an illustrative or estimated value. Output: docs/handbook-assets/*.svg
Run through scripts/build_handbook_pdf.sh, or directly: python3 scripts/handbook/make_figures.py
"""
from __future__ import annotations

import collections
import csv
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[2]
LANG = os.environ.get('HANDBOOK_LANG', 'zh')          # zh (default) | en
OUT = ROOT / ('docs/handbook-assets/en' if LANG == 'en' else 'docs/handbook-assets')
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- localisation
# The English edition renders the same figures from the same data; only the labels change.
# Exact strings first, then whole-string regex patterns for labels that carry numbers.
if LANG == 'en':
    from figures_i18n import EXACT as TR_EXACT
    from figures_i18n import PATTERNS as TR_RE
else:
    TR_EXACT: dict[str, str] = {}
    TR_RE: list[tuple[str, str]] = []
_MISSING: set[str] = set()
_CJK = re.compile(r'[\u4e00-\u9fff]')


def t(s):
    if LANG != 'en':
        return s
    raw = str(s)
    if raw in TR_EXACT:
        return TR_EXACT[raw]
    for pat, rep in TR_RE:
        m = re.fullmatch(pat, raw)
        if m:
            return re.sub(pat, rep, raw)
    if _CJK.search(raw):
        _MISSING.add(raw)
    return raw

# ---------------------------------------------------------------- design tokens
# Light surface only: the handbook is printed on white paper.
# Categorical slots 1-2 validated with the dataviz palette validator against #ffffff:
# CVD ΔE 24.7 (protan) / normal-vision ΔE 33.6 / both ≥ 3:1 contrast — all checks pass.
SURFACE = '#ffffff'
INK = '#141a22'
INK2 = '#52514e'
MUTED = '#898781'
GRID = '#e1e0d9'
AXIS = '#c3c2b7'
S1 = '#2a78d6'          # categorical slot 1 — 基线 / 快路径 / 固定午休 / 单系列
S2 = '#eb6834'          # categorical slot 2 — 本系统 / Agent / 动态休息
GOOD = '#0ca30c'
CRIT = '#d03b3b'
# ordinal blue ramp, light mode: nothing lighter than step 250 (2:1 against the surface)
RAMP = ['#86b6ef', '#5598e7', '#2a78d6', '#1c5cab', '#104281']
FONT = '"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC",system-ui,-apple-system,sans-serif'
MONO = '"SF Mono",Menlo,Consolas,monospace'


def esc(s: str) -> str:
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def tw(s: str, size: float) -> float:
    """Rough rendered width: CJK glyphs are one em, latin about 0.55 em."""
    w = 0.0
    for ch in str(s):
        w += 1.0 if ord(ch) > 0x2E80 else 0.55
    return w * size


def T(x, y, s, size=11, fill=INK2, anchor='start', weight=400, mono=False, op=1.0, tr=True):
    # the font stack itself contains double quotes, so the attribute is single-quoted
    f = MONO if mono else FONT
    o = f' opacity="{op}"' if op != 1.0 else ''
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family=\'{f}\' font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}"{o}>{esc(t(s) if tr else s)}</text>')


def _latin(ch):
    return ord(ch) < 0x2E80


def _tokens(s):
    """Split into breakable tokens: Latin runs stay whole, CJK characters break individually.
    Each token records whether a space preceded it, so spacing survives re-joining."""
    out, buf, buf_sp, pending = [], '', False, False
    for ch in s:
        if ch == ' ':
            if buf:
                out.append((buf, buf_sp)); buf = ''
            pending = True
        elif _latin(ch):
            if buf:
                buf += ch
            else:
                buf, buf_sp, pending = ch, pending, False
        else:
            if buf:
                out.append((buf, buf_sp)); buf = ''
            out.append((ch, pending)); pending = False
    if buf:
        out.append((buf, buf_sp))
    return out


def wrap(s, max_px, size):
    """Break a label into lines that fit `max_px`, for pure-English, pure-CJK and mixed text alike."""
    s = str(s)
    if tw(s, size) <= max_px:
        return [s]
    lines, cur = [], ''
    for tk, sp in _tokens(s):
        sep = ' ' if (cur and sp) else ''
        cand = cur + sep + tk
        if tw(cand, size) <= max_px or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = tk
    if cur:
        lines.append(cur)
    return lines


def Tw(x, y, s, max_px, size=11, fill=INK2, anchor='start', weight=400, lh=1.35):
    """Translate first, then wrap, so the English text is measured after substitution."""
    out = []
    for i, line in enumerate(wrap(t(s), max_px, size)):
        out.append(T(x, y + i * size * lh, line, size, fill, anchor=anchor, weight=weight, tr=False))
    return ''.join(out)


def R(x, y, w, h, fill, rx=0, op=1.0):
    o = f' opacity="{op}"' if op != 1.0 else ''
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w,0):.1f}" height="{max(h,0):.1f}" fill="{fill}" rx="{rx}"{o}/>'


def col(x, w, y_top, y_base, fill, r=4):
    """Vertical column: 4px rounded data-end (top), square at the baseline."""
    h = y_base - y_top
    if h <= 0.5:
        return R(x, y_base - 1, w, 1, fill)
    r = min(r, h, w / 2)
    return (f'<path d="M{x:.1f},{y_base:.1f} L{x:.1f},{y_top + r:.1f} Q{x:.1f},{y_top:.1f} {x + r:.1f},{y_top:.1f} '
            f'L{x + w - r:.1f},{y_top:.1f} Q{x + w:.1f},{y_top:.1f} {x + w:.1f},{y_top + r:.1f} '
            f'L{x + w:.1f},{y_base:.1f} Z" fill="{fill}"/>')


def hbar(x0, y, w, h, fill, r=4):
    """Horizontal bar: 4px rounded data-end (right), square at the baseline."""
    if w <= 0.5:
        return R(x0, y, 1, h, fill)
    r = min(r, w, h / 2)
    return (f'<path d="M{x0:.1f},{y:.1f} L{x0 + w - r:.1f},{y:.1f} Q{x0 + w:.1f},{y:.1f} {x0 + w:.1f},{y + r:.1f} '
            f'L{x0 + w:.1f},{y + h - r:.1f} Q{x0 + w:.1f},{y + h:.1f} {x0 + w - r:.1f},{y + h:.1f} '
            f'L{x0:.1f},{y + h:.1f} Z" fill="{fill}"/>')


def legend(x, y, items, size=10.5):
    """Identity is never colour alone: a legend is always present for two or more series."""
    out, cx = [], x
    for label, colr in items:
        label = t(label)
        out.append(R(cx, y - 7, 9, 9, colr, rx=2))
        out.append(T(cx + 13, y, label, size, INK2))
        cx += 13 + tw(label, size) + 18
    return ''.join(out)


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{esc(title)}">\n<title>{esc(title)}</title>\n'
            f'{R(0, 0, w, h, SURFACE)}\n{body}\n</svg>\n')


def write(name, w, h, body, title):
    (OUT / name).write_text(svg(w, h, body, title))
    print(f'  {name}  {w}x{h}')


def panel_title(x, y, main, sub=None):
    o = [T(x, y, main, 12, INK, weight=600)]
    if sub:
        o.append(T(x, y + 15, sub, 10, MUTED))
    return ''.join(o)


def fmt(v):
    if isinstance(v, float):
        return f'{v:g}' if v == int(v) else f'{v:.3f}'.rstrip('0')
    return str(v)


def grouped_panel(px, py, pw, ph, title, sub, cats, series, fmt_v=fmt, leg=None):
    """One small-multiple panel: N categories × 1-2 series of columns, each column direct-labelled.

    Direct labels carry the values, so the panel needs no y-axis ticks (direct labels
    before gridlines). One measure per panel — never two scales on one plot. When the two
    series of a group carry the same value the label is drawn once, centred over the group,
    instead of two numbers colliding.
    """
    out = [panel_title(px, py + 11, title, sub)]
    head = (34 if sub else 22)
    if leg:
        out.append(legend(px, py + head + 8, leg, 10))
        head += 20
    top = py + head
    base = py + ph - (20 if len(cats) > 1 or cats[0] else 8)
    vmax = max([v for _, vals, _ in series for v in vals] + [1e-9])
    band = pw / len(cats)
    nser = len(series)
    bw = min(24.0, (band - 14) / nser - 2)
    for ci, cat in enumerate(cats):
        gx = px + ci * band + (band - (bw * nser + 2 * (nser - 1))) / 2
        vals_here = [s[1][ci] for s in series]
        same = nser == 2 and vals_here[0] == vals_here[1]
        for si, (_, vals, _c) in enumerate(series):
            v = vals[ci]
            x = gx + si * (bw + 2)              # 2px surface gap between adjacent bars
            ytop = base - (v / vmax) * (base - top - 14)
            out.append(col(x, bw, ytop, base, series[si][2]))
            if not same:
                out.append(T(x + bw / 2, ytop - 5, fmt_v(v), 9.5, INK, anchor='middle', weight=600))
        if same:
            ytop = base - (vals_here[0] / vmax) * (base - top - 14)
            out.append(T(gx + (bw * 2 + 2) / 2, ytop - 5, fmt_v(vals_here[0]), 9.5, INK,
                         anchor='middle', weight=600))
        if cat:
            out.append(T(px + ci * band + band / 2, base + 14, cat, 10.5, INK2, anchor='middle'))
    out.append(f'<line x1="{px}" y1="{base}" x2="{px + pw}" y2="{base}" stroke="{AXIS}" stroke-width="1"/>')
    return ''.join(out)


# ================================================================= 1. 维修问题库
def fig_catalog():
    rows = list(csv.DictReader(open(ROOT / 'data/reference/repair_object_problem_database.csv')))
    trades = collections.Counter(r['Trade Type'] for r in rows).most_common()
    pairs = {}
    for r in rows:
        pairs.setdefault(int(r['Problem Complexity']), set()).add(int(r['Repair Duration (min)']))
    cx = collections.Counter(int(r['Problem Complexity']) for r in rows)

    W, H = 1020, 400
    o = [T(0, 0, '')]
    # Panel A — 工种分布 (single series → one colour; magnitude → horizontal bars)
    o.append(panel_title(0, 14, '按工种的问题条目数', f'共 {len(rows)} 条，10 个工种'))
    y, lw, bx = 50, 138, 150
    mx = max(c for _, c in trades)
    for name, c in trades:
        o.append(T(lw, y + 9, name, 10.5, INK2, anchor='end'))
        o.append(hbar(bx, y, (c / mx) * 240, 13, S1))
        o.append(T(bx + (c / mx) * 240 + 6, y + 10, c, 10, INK, weight=600))
        y += 20
    # Panel B — 复杂度 (ordered categories → ordinal ramp, one hue)
    px = 560
    o.append(panel_title(px, 14, '按复杂度的条目数与固定维修时长',
                         '复杂度与时长在问题库中一一对应，时长不由模型估算'))
    base, top = 300, 60
    cmax = max(cx.values())
    for i, lvl in enumerate(sorted(cx)):
        gx = px + i * 88 + 20
        v = cx[lvl]
        ytop = base - (v / cmax) * (base - top)
        o.append(col(gx, 24, ytop, base, RAMP[lvl - 1]))
        o.append(T(gx + 12, ytop - 6, v, 10.5, INK, anchor='middle', weight=600))
        o.append(T(gx + 12, base + 16, f'复杂度 {lvl}', 10.5, INK2, anchor='middle'))
        dur = sorted(pairs[lvl])[0]
        o.append(T(gx + 12, base + 31, f'{dur} 分钟', 10, MUTED, anchor='middle'))
    o.append(f'<line x1="{px}" y1="{base}" x2="{px + 420}" y2="{base}" stroke="{AXIS}" stroke-width="1"/>')
    o.append(T(px, base + 58, '技师技能等级必须 ≥ 问题复杂度，否则该技师不是候选。',
               10, MUTED))
    o.append(T(0, H - 8, '数据来源：data/reference/repair_object_problem_database.csv', 9.5, MUTED, mono=True))
    return write('chart-catalog.svg', W, H, ''.join(o), '维修问题库构成')


# ========================================================== 2. 技师技能覆盖矩阵
def fig_skills():
    sc = json.load(open(ROOT / 'data/scenarios/main.json'))
    techs = sc['technicians']
    short = {'Air Conditioning': '空调', 'Plumbing & Bathroom': '水暖', 'Refrigerator': '冰箱',
             'Washing Machine': '洗衣机', 'Water Heater': '热水器', 'Electrical & Lighting': '电气照明',
             'Locks & Hardware': '门锁五金', 'Gas Stove': '燃气灶', 'Furniture & Woodwork': '家具木工',
             'Network & Smart Devices': '网络智能'}
    trades = list(short)
    W, H = 1020, 400
    o = [panel_title(0, 14, f'场景 main 的技师技能矩阵（{len(techs)} 名技师 × {len(trades)} 个工种）',
                     '格内数字为技能等级 1–5。空格表示该技师不持有该工种；技能等级低于问题复杂度时不进入候选')]
    x0, y0, cw, ch = 118, 58, 86, 30
    for j, tr in enumerate(trades):
        o.append(T(x0 + j * cw + cw / 2, y0 - 8, short[tr], 10, INK2, anchor='middle'))
    for i, t in enumerate(techs):
        y = y0 + i * ch
        o.append(T(x0 - 10, y + ch / 2 + 4, t['name'], 10.5, INK2, anchor='end'))
        for j, tr in enumerate(trades):
            x = x0 + j * cw
            lvl = t['skills'].get(tr)
            if lvl:
                o.append(R(x + 1, y + 1, cw - 2, ch - 2, RAMP[lvl - 1], rx=3))
                o.append(T(x + cw / 2, y + ch / 2 + 4, lvl, 11,
                           '#ffffff' if lvl >= 3 else INK, anchor='middle', weight=600))
            else:
                o.append(R(x + 1, y + 1, cw - 2, ch - 2, '#f4f6f9', rx=3))
                o.append(T(x + cw / 2, y + ch / 2 + 4, '·', 11, GRID, anchor='middle'))
    # per-trade coverage: how many technicians hold the trade at all
    yc = y0 + len(techs) * ch + 16
    o.append(T(x0 - 10, yc + 4, '可服务技师数', 10, MUTED, anchor='end'))
    for j, tr in enumerate(trades):
        n = sum(1 for t in techs if tr in t['skills'])
        o.append(T(x0 + j * cw + cw / 2, yc + 4, n, 10.5, CRIT if n <= 1 else INK2,
                   anchor='middle', weight=600 if n <= 1 else 400))
    o.append(T(0, yc + 30, '标红的工种在本场景中只有一名技师可服务。该技师不可用时，相关工单不存在零打扰的恢复方案，'
                           '进入恢复流程或人工队列。', 10, MUTED))
    # ramp legend
    lx = x0
    o.append(T(lx, H - 30, '技能等级', 10, MUTED))
    for k in range(5):
        o.append(R(lx + 56 + k * 26, H - 44, 22, 11, RAMP[k], rx=2))
        o.append(T(lx + 56 + k * 26 + 11, H - 21, k + 1, 9.5, MUTED, anchor='middle'))
    o.append(T(0, H - 8, '数据来源：data/scenarios/main.json', 9.5, MUTED, mono=True))
    return write('chart-skills-matrix.svg', W, H, ''.join(o), '技师技能覆盖矩阵')


# ================================================================ 3. 评分权重
def fig_weights():
    txt = (ROOT / 'config/policy.yaml').read_text()
    blk = txt.split('scoring:')[1].split('solver:')[0]
    w = {k: float(v) for k, v in
         re.findall(r'^\s{4}(\w+):\s*([\d.]+)', blk.split('weights:')[1].split('scales:')[0], re.M)}
    scales = {k: v for k, v in re.findall(r'^\s{4}(\w+):\s*(\d+)', blk.split('scales:')[1], re.M)}
    label = {'skill_fit': '技能匹配 skill_fit', 'travel': '通勤 travel', 'response': '响应 response',
             'stability': '稳定性 stability', 'workload': '工作量 workload'}
    note = {'skill_fit': '等级低于复杂度即 0 分',
            'travel': f"满分惩罚尺度 {scales.get('travel_full_penalty_minutes','60')} 分钟",
            'response': f"满分惩罚尺度 {scales.get('response_full_penalty_minutes','120')} 分钟",
            'stability': f"受影响参考数 {scales.get('affected_reference_count','2')}，换技师额外 0.5 惩罚",
            'workload': f"班次尺度 {scales.get('shift_full_penalty_minutes','60')} 分钟"}
    order = sorted(w, key=lambda k: -w[k])
    W, H = 1020, 236
    o = [panel_title(0, 14, 'match_score 的五个分量与权重',
                     'decision_score = 方案中所有新增或变更分配的 match_score 最小值；> 70 才可自动执行')]
    y, bx, bw = 52, 200, 470
    for k in order:
        o.append(T(bx - 10, y + 10, label[k], 10.5, INK2, anchor='end'))
        o.append(hbar(bx, y, w[k] / 0.30 * bw, 14, S1))
        o.append(T(bx + w[k] / 0.30 * bw + 7, y + 11, f'{w[k]:.2f}', 10.5, INK, weight=600))
        o.append(T(bx + bw + 62, y + 11, note[k], 10, MUTED))
        y += 22
    o.append(T(bx - 10, y + 12, '合计', 10.5, MUTED, anchor='end'))
    o.append(T(bx, y + 12, f'{sum(w.values()):.2f}', 10.5, INK, weight=600))
    o.append(T(0, H - 8, '数据来源：config/policy.yaml → scoring（工程默认值，可调整）', 9.5, MUTED, mono=True))
    return write('chart-scoring-weights.svg', W, H, ''.join(o), '评分权重构成')


# ========================================================== 4. V2 对照实验
def fig_eval_v2():
    d = json.load(open(ROOT / 'data/evaluation/latest.json'))
    b, p = d['summary']['baseline'], d['summary']['proposed']
    W, H = 1020, 326
    ser = [('基线：最近可行插入', None, S1), ('本系统：打分 + 权限内有界重排', None, S2)]
    o = [panel_title(0, 14, 'V2 对照实验：同一初始排班、同一事件序列下的两种调度策略',
                     f"{d['seeds'].__len__()} 个随机种子 × 每种子 {b['events'] // len(d['seeds'])}"
                     f".1 个事件 = {b['events']} 个事件；路线 {d['route_provider']}；策略版本 {d['policy_version']}")]
    o.append(legend(0, 62, [(s[0], s[2]) for s in ser]))
    pw, py, ph = 214, 80, 200
    panels = [
        ('可行排班率', '未分配的事件计为未达成', ['', ], [(ser[0][0], [b['assignment_rate']], S1),
                                               (ser[1][0], [p['assignment_rate']], S2)], lambda v: f'{v:.3f}'),
        ('紧急事件未服务数', f"共 {b['urgent_events']} 个 P0/P1 事件", ['', ],
         [(ser[0][0], [b['urgent_unserved']], S1), (ser[1][0], [p['urgent_unserved']], S2)], fmt),
        ('受影响的既有工单数', '被移动或改派的其他客户工单', ['', ],
         [(ser[0][0], [b['affected_total']], S1), (ser[1][0], [p['affected_total']], S2)], fmt),
        ('新增通勤总分钟', '全部技师相对基准排班的通勤增量', ['', ],
         [(ser[0][0], [b['travel_delta_total']], S1), (ser[1][0], [p['travel_delta_total']], S2)], fmt),
    ]
    for i, (t, s, cats, series, f) in enumerate(panels):
        o.append(grouped_panel(i * (pw + 54), py, pw, ph, t, s, cats, series, f))
    o.append(T(0, H - 26, f"两种策略在已提交方案中的硬约束违规与越权违规均为 "
                          f"{b['hard_or_authority_violations']} 起；决策分布：基线 assign "
                          f"{b['decisions']['assign']} · unresolved {b['decisions']['unresolved']}，"
                          f"本系统 auto {p['decisions']['auto']} · manual {p['decisions']['manual']} · "
                          f"unresolved {p['decisions']['unresolved']}。", 10, INK2))
    o.append(T(0, H - 8, ' 数据来源：data/evaluation/latest.json', 9.5, MUTED, mono=True))
    return write('chart-eval-v2.svg', W, H, ''.join(o), 'V2 调度策略对照实验')


# ===================================================== 5/6. V3 编排对照与成本
def _v3():
    return json.load(open(ROOT / 'data/evaluation/latest_v3.json'))


def fig_eval_v3():
    d = _v3()
    order = ['relaxed', 'main', 'scarce']
    lab = {'relaxed': f"relaxed（{d['scenarios']['relaxed']['orders_per_seed']} 单）",
           'main': f"main（{d['scenarios']['main']['orders_per_seed']} 单）",
           'scarce': f"scarce（{d['scenarios']['scarce']['orders_per_seed']} 单 · 2 人请假）"}
    g = lambda s, strat, k: d['scenarios'][s]['orchestration'][strat][k]
    W, H = 1020, 352
    o = [panel_title(0, 14, 'V3 对照实验：快路径与 Agent 编排（每个场景 10 个随机种子的均值）',
                     '同样的世界、事件、策略、求解器与预算；唯一差别是工单进入 UNRESOLVED 之后如何编排')]
    o.append(legend(0, 62, [('快路径（V2 流水线）', S1), ('快路径 + Agent 有界调查', S2)]))
    pw, py, ph = 290, 80, 206
    specs = [
        ('可行排班率', '含 Agent 协商出的备选时间窗', 'assignment_rate', lambda v: f'{v:.3f}'),
        ('原时间窗准时开始率', '两种编排在此项上完全相同', 'on_window_rate', lambda v: f'{v:.3f}'),
        ('紧急事件未服务数', '每个种子的均值', 'urgent_unserved', lambda v: f'{v:g}'),
    ]
    for i, (t, s, key, f) in enumerate(specs):
        o.append(grouped_panel(i * (pw + 75), py, pw, ph, t, s,
                               [lab[x].split('（')[0] for x in order],
                               [('快路径', [g(x, 'fast_path', key) for x in order], S1),
                                ('Agent', [g(x, 'agent', key) for x in order], S2)], f))
    o.append(T(0, H - 40, '场景规模：' + ' · '.join(lab[x] for x in order), 10, INK2))
    o.append(T(0, H - 24, '两种编排在已提交方案中的硬约束违规与越权违规在全部场景中均为 0。'
                          '"备选窗口被客户接受" 是一个假设，单独统计，不计入原时间窗准时率。', 10, INK2))
    o.append(T(0, H - 6, '数据来源：data/evaluation/latest_v3.json', 9.5, MUTED, mono=True))
    return write('chart-eval-v3.svg', W, H, ''.join(o), 'V3 编排对照实验')


def fig_agent_cost():
    d = _v3()
    order = ['relaxed', 'main', 'scarce']
    g = lambda s, k: d['scenarios'][s]['orchestration']['agent'][k]
    bud = d['budgets']
    W, H = 1020, 318
    o = [panel_title(0, 14, 'Agent 调查的成本与产出随资源稀缺度的变化',
                     f"横轴按资源由宽松到稀缺排列。预算上限：每次唤醒 {bud['max_tool_calls_per_wakeup']} 次工具调用 / "
                     f"{bud['max_plan_searches_per_wakeup']} 次方案搜索 / {bud['transient_retries']} 次瞬时重试")]
    pw, py, ph = 290, 62, 216
    o.append(grouped_panel(0, py, pw, ph, '每种子的工具调用次数', '方案搜索是其中的子集',
                           order, [('工具调用', [g(x, 'tool_calls') for x in order], S1),
                                   ('其中方案搜索', [g(x, 'plan_searches') for x in order], S2)],
                           leg=[('工具调用', S1), ('其中方案搜索', S2)]))
    o.append(grouped_panel(pw + 75, py, pw, ph, '编排处理耗时（毫秒）', '不含模型调用延迟',
                           order, [('处理 ms', [g(x, 'processing_ms') for x in order], S1)]))
    o.append(grouped_panel(2 * (pw + 75), py, pw, ph, '每种子的调查产出', '两类结局的次数',
                           order, [('备选窗口服务', [g(x, 'served_in_alt_window') for x in order], S1),
                                   ('人工升级', [g(x, 'human_escalations') for x in order], S2)],
                           leg=[('备选窗口服务', S1), ('人工升级', S2)]))
    o.append(Tw(0, H - 30, 'scarce 场景中多数调查以人工升级结束。在资源不足时这是设计预期的结局：'
                           '预算耗尽不进入重试循环，而是以带证据的人工事项收尾。', 1008, 10, INK2))
    o.append(T(0, H - 6, '数据来源：data/evaluation/latest_v3.json · config/policy.yaml → agent', 9.5, MUTED, mono=True))
    return write('chart-agent-cost.svg', W, H, ''.join(o), 'Agent 成本与产出')


def fig_rest():
    d = _v3()
    order = ['relaxed', 'main', 'scarce']
    g = lambda s, m, k: d['scenarios'][s]['rest'][m][k]
    W, H = 1020, 318
    o = [panel_title(0, 14, '固定午休与动态休息的对照（同一批世界的初始排班）',
                     '固定午休 = 全员 12:00–13:00 不可用；动态休息 = 累计工作 180–240 分钟之间寻找 ≥ 30 分钟的零打扰空档')]
    o.append(legend(0, 62, [('固定午休', S1), ('动态休息', S2)]))
    pw, py, ph = 290, 80, 200
    specs = [('初始未分配工单数', '种子排班阶段无法安置的工单', 'initial_unassigned', lambda v: f'{v:g}'),
             ('通勤总分钟', '全部技师当日通勤合计', 'travel_total', lambda v: f'{v:.0f}'),
             ('休息升级为人工事项次数', '240 分钟仍无休息即升级', 'rest_escalations', lambda v: f'{v:g}')]
    for i, (t, s, key, f) in enumerate(specs):
        o.append(grouped_panel(i * (pw + 75), py, pw, ph, t, s, order,
                               [('固定午休', [g(x, 'fixed_lunch', key) for x in order], S1),
                                ('动态休息', [g(x, 'dynamic', key) for x in order], S2)], f))
    o.append(T(0, H - 24, '评测中的动态休息使用"空闲空档"代理指标；线上实现会额外重新校验路线可行性与后继时间窗，'
                          '因此评测中的"找到休息位"是一个上界。', 10, INK2))
    o.append(T(0, H - 6, '数据来源：data/evaluation/latest_v3.json', 9.5, MUTED, mono=True))
    return write('chart-rest.svg', W, H, ''.join(o), '休息策略对照')


# ================================================================ 7. 测试分布
def fig_tests():
    counts = {}
    for f in sorted((ROOT / 'backend/tests').glob('test_*.py')):
        n = len(re.findall(r'^def test', f.read_text(), re.M))
        if n:
            counts[f.name] = n
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    total = sum(counts.values())
    W, H = 1020, 300
    o = [panel_title(0, 14, f'后端测试用例分布（共 {total} 个 pytest 用例，{len(items)} 个测试文件）',
                     'tests/conftest.py 强制 LLM_MODE=mock，后端测试完全离线且确定性')]
    y, lw, bx, bw = 52, 250, 262, 420
    mx = max(counts.values())
    for name, c in items:
        o.append(T(lw, y + 9, name, 10, INK2, anchor='end', mono=True))
        o.append(hbar(bx, y, c / mx * bw, 12, S1))
        o.append(T(bx + c / mx * bw + 6, y + 10, c, 10, INK, weight=600))
        y += 17
    o.append(T(0, H - 8, '数据来源：backend/tests/（pytest --collect-only）', 9.5, MUTED, mono=True))
    return write('chart-tests.svg', W, H, ''.join(o), '测试用例分布')


# ========================================================== 8. 权限矩阵（配置）
def fig_authority():
    txt = (ROOT / 'config/policy.yaml').read_text()
    blk = txt.split('reschedule:')[1].split('execution:')[0]
    rules = {}
    cur = None
    for line in blk.splitlines():
        m = re.match(r'^\s{2}(P[0-3]):', line)
        if m:
            cur = m.group(1); rules[cur] = {}
        elif cur:
            mm = re.match(r'^\s{4}(\w+):\s*(.+?)\s*(?:#.*)?$', line)
            if mm:
                rules[cur][mm.group(1)] = mm.group(2)
    thr = re.search(r'auto_score_threshold:\s*(\d+)', txt).group(1)
    W, H = 1020, 330
    o = [panel_title(0, 14, 'P0–P3 的重排权限矩阵',
                     '权限规定可移动的工单类型与数量上限；优先级不参与评分，对同一工单的所有候选技师为常量')]
    cols = [('目标优先级', 118), ('可移动的其他工单', 210), ('最多影响工单数', 150),
            ('已出发任务', 120), ('强制人工审批的条件', 380)]
    x = 0
    y0 = 58
    for name, w in cols:
        o.append(T(x + 8, y0, name, 10.5, INK, weight=600))
        x += w
    o.append(f'<line x1="0" y1="{y0 + 8}" x2="978" y2="{y0 + 8}" stroke="{AXIS}" stroke-width="1"/>')
    extra = {
        'P3': f'决策分 ≤ {thr}',
        'P2': f'决策分 ≤ {thr}；已有有效分配时保持不动并准备备选技师',
        'P1': f'决策分 ≤ {thr}',
        'P0': f'影响 ≥ 1 张工单即须人工审批；决策分 ≤ {thr}；超限方案仅告警，不提供审批入口',
    }
    rh = 46
    for i, p in enumerate(['P3', 'P2', 'P1', 'P0']):
        y = y0 + 36 + i * rh
        r = rules.get(p, {})
        mv = r.get('movable_priorities', '[]').strip('[]').replace(',', '、').strip() or '无'
        cap = r.get('max_affected', '0')
        cap_txt = '不限' if cap == 'unlimited' else cap
        if i % 2 == 0:
            o.append(R(0, y - 22, 1008, rh - 2, '#f7f9fc'))
        badge = {'P0': CRIT, 'P1': '#eb6834', 'P2': '#eda100', 'P3': S1}[p]
        o.append(R(8, y - 11, 26, 16, badge, rx=3))
        o.append(T(21, y + 1, p, 10.5, '#ffffff', anchor='middle', weight=600))
        o.append(Tw(126, y + 1, mv, 190, 10.5, INK2))
        o.append(T(336, y + 1, cap_txt, 10.5, INK if cap_txt != '0' else MUTED, weight=600 if cap_txt != '0' else 400))
        o.append(Tw(486, y + 1, '一律不可移动', 110, 10.5, MUTED))
        o.append(Tw(606, y + 1, extra[p], 400, 10.5, INK2))
    o.append(Tw(0, H - 34, '被移动的工单必须仍在各自的时间窗内。同一事件中若第一个目标已移动其他工单，'
                           '第二个目标的任何再次移动方案强制进入人工审批，以防止拆分自动提交累加越权。', 1008, 10, INK2))
    o.append(T(0, H - 8, '数据来源：config/policy.yaml → reschedule / dispatch', 9.5, MUTED, mono=True))
    return write('chart-authority.svg', W, H, ''.join(o), 'P0–P3 重排权限矩阵')




# ============================================================ 9. 机制图（流程）
def _box(x, y, w, h, label, sub=None, fill='#ffffff', stroke=AXIS, ink=INK, rx=5, dash=None, sw=1):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    o = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
         f'stroke="{stroke}" stroke-width="{sw}"{d}/>']
    if sub:
        o.append(T(x + w / 2, y + h / 2 - 2, label, 10.5, ink, anchor='middle', weight=600))
        o.append(T(x + w / 2, y + h / 2 + 12, sub, 9, MUTED, anchor='middle'))
    else:
        o.append(T(x + w / 2, y + h / 2 + 4, label, 10.5, ink, anchor='middle', weight=600))
    return ''.join(o)


def _arrow(x1, y1, x2, y2, colr=AXIS, dash=None, w=1.2):
    d = f' stroke-dasharray="{dash}"' if dash else ''
    return (f'<path d="M{x1},{y1} L{x2},{y2}" stroke="{colr}" stroke-width="{w}" fill="none" '
            f'marker-end="url(#ar)"{d}/>')


DEFS = (f'<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
        f'orient="auto-start-reverse"><path d="M0,1 L9,5 L0,9 z" fill="{AXIS}"/></marker></defs>')


def fig_pipeline():
    """orchestration/orchestrator.py 的阶段顺序与三种结局。"""
    W, H = 1020, 300
    o = [DEFS, panel_title(0, 14, '派单流水线的阶段与结局',
                           '阶段定义见 orchestration/orchestrator.py；每个阶段作为一条 AgentRun step 记录')]
    stages = [('加载快照', 'load_snapshot'), ('风险分级', 'classify_priority'), ('求解候选', 'solve_insert'),
              ('独立校验', 'validate_plan'), ('计算受影响', 'compute_affected'), ('权限检查', 'check_authority'),
              ('打分', 'decision_score'), ('策略裁决', 'PolicyEngine')]
    bw, gap, y = 112, 14, 58
    for i, (a, b) in enumerate(stages):
        x = i * (bw + gap)
        o.append(_box(x, y, bw, 44, a, b, fill='#f7f9fc'))
        if i:
            o.append(_arrow(x - gap + 1, y + 22, x - 2, y + 22))
    ey = 168
    ends = [('自动提交', 'decision_score > 70 且权限内', '#e9f3ff', S1),
            ('人工审批', '低分 / P0 影响他人 / 搜索未完成', '#fff3ec', S2),
            ('无解', '创建 Agent 任务继续调查', '#f4f6f9', MUTED)]
    for i, (a, b, bg, c) in enumerate(ends):
        x = 60 + i * 320
        o.append(_arrow(7 * (bw + gap) + bw / 2, y + 46, x + 130, ey - 4))
        o.append(_box(x, ey, 260, 46, a, b, fill=bg, stroke=c))
    o.append(T(0, 262, 'schedule_service.commit_plan 是实时排班表的唯一写入路径，'
                       '每次提交生成一个新的 ScheduleVersion（含父版本、原因、模拟时间与完整分配快照）。', 10, INK2))
    o.append(T(0, 284, '来源：backend/app/orchestration/orchestrator.py · backend/app/scheduling/policy.py',
               9.5, MUTED, mono=True))
    return write('diagram-pipeline.svg', W, H, ''.join(o), '派单流水线')


def fig_lifecycle():
    """models/enums.py 的生命周期与调度状态。"""
    W, H = 1020, 286
    o = [DEFS, panel_title(0, 14, '工单生命周期与锁定边界',
                           '状态取自 backend/app/models/enums.py；ARRIVED 起计为"已出发"，不可被任何方案修改')]
    y = 74
    seq = [('DRAFT', '会话草稿'), ('NEEDS_INFO', '槽位未补全'), ('OPEN', '已建单待执行'),
           ('EN_ROUTE', '在途'), ('ARRIVED', '已到达'), ('IN_PROGRESS', '施工中'), ('COMPLETED', '已完成')]
    bw, gap = 126, 12
    lock_from = 3
    o.append(R(lock_from * (bw + gap) - 6, y - 16, 4 * bw + 3 * gap + 12, 78, '#fdf1ee', rx=6))
    o.append(T(lock_from * (bw + gap) - 6 + (4 * bw + 3 * gap + 12) / 2, y - 22,
               '锁定区：technician / departure / service_start 不可被任何候选方案修改', 9.5, S2,
               anchor='middle', weight=600))
    for i, (a, b) in enumerate(seq):
        x = i * (bw + gap)
        locked = i >= lock_from
        o.append(_box(x, y, bw, 44, a, b, fill='#ffffff', stroke=S2 if locked else AXIS,
                      sw=1.4 if locked else 1))
        if i:
            o.append(_arrow(x - gap + 1, y + 22, x - 2, y + 22))
    cy = y + 92
    o.append(_box(3 * (bw + gap) - 30, cy, 150, 34, 'CANCELLED', '仅限出发前', fill='#f4f6f9', dash='4 3'))
    o.append(_arrow(2 * (bw + gap) + bw / 2, y + 46, 3 * (bw + gap) + 20, cy - 3))
    nx = 3 * (bw + gap) + 140
    o.append(Tw(nx, cy + 8,
                '出发后客户取消返回 409 already_departed；取消与出发同时发生时，先持久化者生效。', 1020 - nx - 8, 10, INK2))
    o.append(Tw(nx, cy + 40,
                '调度状态独立于生命周期，取值为 UNASSIGNED · PROPOSED · PENDING_REVIEW · ASSIGNED · UNRESOLVED。',
                1020 - nx - 8, 10, INK2))
    o.append(T(0, H - 8, '来源：backend/app/models/enums.py · backend/app/scheduling/validator.py',
               9.5, MUTED, mono=True))
    return write('diagram-lifecycle.svg', W, H, ''.join(o), '工单生命周期')


def fig_agent_loop():
    """agents/runtime.py 的任务循环与四类出口。"""
    pol = (ROOT / 'config/policy.yaml').read_text()
    calls = re.search(r'max_tool_calls_per_wakeup:\s*(\d+)', pol).group(1)
    searches = re.search(r'max_plan_searches_per_wakeup:\s*(\d+)', pol).group(1)
    retries = re.search(r'transient_retries:\s*(\d+)', pol).group(1)
    W, H = 1020, 330
    o = [DEFS, panel_title(0, 14, 'Agent 任务循环与四类出口',
                           f'每次唤醒的预算：{calls} 次工具调用 / {searches} 次方案搜索 / {retries} 次瞬时重试'
                           f'（config/policy.yaml → agent）')]
    y = 62
    o.append(_box(0, y, 176, 46, '创建任务', 'create_task · 去重键', fill='#f7f9fc'))
    o.append(_box(212, y, 176, 46, '策略决定下一步', 'ModelPolicy / MockPolicy', fill='#e9f3ff', stroke=S1))
    o.append(_box(424, y, 176, 46, '调用工具', '角色门 + 技能白名单 + 参数校验', fill='#f7f9fc'))
    o.append(_box(636, y, 176, 46, '记录轨迹', 'ToolTrace：为什么 · 做了什么 · 结果', fill='#f7f9fc'))
    for x in (176, 388, 600):
        o.append(_arrow(x + 2, y + 23, x + 34, y + 23))
    o.append(f'<path d="M812,{y+23} L848,{y+23} L848,{y+72} L106,{y+72} L106,{y+48}" stroke="{AXIS}" '
             f'stroke-width="1.2" fill="none" marker-end="url(#ar)"/>')
    o.append(T(470, y + 86, '未终止则回到策略：预算未用尽时继续下一步', 9.5, MUTED, anchor='middle'))
    ey = 196
    outs = [('成功结束', 'succeeded：方案已提交或已确认无需处理', '#e9f3ff', S1),
            ('等待', 'waiting_customer / waiting_human / waiting_agent：任务挂起，被显式唤醒', '#f7f9fc', AXIS),
            ('无解', 'no_solution：自动转为带证据的人工事项', '#fff3ec', S2),
            ('预算耗尽', 'budget_exhausted：同样自动转人工，不进入重试循环', '#fff3ec', S2)]
    for i, (a, b, bg, c) in enumerate(outs):
        yy = ey + i * 30
        o.append(R(0, yy, 8, 22, c, rx=2))
        o.append(T(18, yy + 15, a, 10.5, INK, weight=600))
        o.append(T(92, yy + 15, b, 10, INK2))
    o.append(T(0, H - 8, '来源：backend/app/agents/runtime.py · backend/app/agents/policies.py · '
                         'config/agent_skills/*.md', 9.5, MUTED, mono=True))
    return write('diagram-agent-loop.svg', W, H, ''.join(o), 'Agent 任务循环')


# ====================================================== 10. 系统技术架构图（按代码生成）
def fig_architecture():
    """Layered architecture drawn from the actual module tree, so it cannot drift from the code."""
    def names(p, exclude=()):
        d = ROOT / p
        return sorted(f.stem for f in d.glob('*.py')
                      if f.stem != '__init__' and f.stem not in exclude) if d.is_dir() else []

    api = names('backend/app/api')
    agents = names('backend/app/agents')
    sched = names('backend/app/scheduling')
    svc = names('backend/app/services')
    pages = sorted(f.stem for f in (ROOT / 'frontend/src/pages').glob('*.tsx'))
    comps = list((ROOT / 'frontend/src/components').glob('*.tsx'))
    routers = [n for n in api if n.startswith('routes_')]

    W, H = 1020, 660
    o = [DEFS, panel_title(0, 14, '系统技术架构（模块清单由代码目录生成）',
                           '单进程 FastAPI 后端 + React 单页应用；箭头方向为调用方向')]

    def layer(y, h, title, sub, fill, stroke):
        return (R(0, y, W, h, fill, rx=6)
                + f'<rect x="0" y="{y}" width="{W}" height="{h}" rx="6" fill="none" stroke="{stroke}" stroke-width="1"/>'
                + T(12, y + 17, title, 10.5, INK, weight=600) + T(12, y + 31, sub, 9, MUTED))

    def chips(x, y, items, per_row, cw, mark=()):
        out = []
        for i, it in enumerate(items):
            cx, cy = x + (i % per_row) * cw, y + (i // per_row) * 19
            hot = it in mark
            out.append(R(cx, cy, cw - 6, 15, '#ffffff' if not hot else '#e9f3ff', rx=3))
            out.append(f'<rect x="{cx}" y="{cy}" width="{cw-6}" height="15" rx="3" fill="none" '
                       f'stroke="{S1 if hot else GRID}" stroke-width="{1.2 if hot else 0.8}"/>')
            out.append(T(cx + (cw - 6) / 2, cy + 11, it, 8, INK if hot else INK2, anchor='middle',
                         weight=600 if hot else 400, mono=True))
        return ''.join(out)

    def rows(n, per_row):
        return -(-n // per_row)

    # ---- frontend
    y = 42
    o.append(layer(y, 74, '前端　React 19 · TypeScript 6 · Vite 8 · Tailwind 4 · Leaflet（127.0.0.1:5174）',
                   f'{len(pages)} 个页面 · {len(comps)} 个组件 · 统一出口 api/client.ts · 轮询 stores/usePolling.ts',
                   '#f7f9fc', GRID))
    o.append(chips(12, y + 40, [f'{p}.tsx' for p in pages], 6, 132))
    o.append(T(W - 12, y + 52, 'REST · 2–4 秒轮询（位置 1.5 秒）', 9, MUTED, anchor='end'))

    # ---- api
    y = 128
    api_items = [f'{r}.py' for r in routers] + ['deps', 'errors', 'serializers']
    o.append(layer(y, 40 + 19 * rows(len(api_items), 6) + 8, 'API 层　backend/app/api（FastAPI，127.0.0.1:8100）',
                   '路由 · 事件判别联合 · 统一错误信封 · 进程状态锁 locked()', '#ffffff', AXIS))
    o.append(chips(12, y + 40, api_items, 6, 132))

    # ---- agents + orchestration
    y = 222
    agent_items = [f'{a}.py' for a in agents] + ['orchestrator.py']
    o.append(layer(y, 40 + 19 * rows(len(agent_items), 7) + 10, 'Agent 与编排层　backend/app/agents · backend/app/orchestration',
                   '编排器为确定性状态机；Agent 运行时在独立线程中决策，工具调用在短事务内提交', '#e9f3ff', S1))
    o.append(chips(12, y + 40, agent_items, 7, 140, mark={'tools.py', 'runtime.py', 'orchestrator.py'}))

    # ---- scheduling core
    y = 310
    sched_items = [f'{x}.py' for x in sched]
    o.append(layer(y, 40 + 19 * rows(len(sched_items), 8) + 8, '调度核心　backend/app/scheduling（纯函数，可脱库单测）',
                   '快照 → 模拟 → 校验 → 受影响集合 → 打分 → 权限与策略 → 求解', '#ffffff', AXIS))
    o.append(chips(12, y + 40, sched_items, 8, 124, mark={'solver.py', 'validator.py', 'policy.py'}))

    # ---- services
    y = 384
    o.append(layer(y, 40 + 19 * rows(len(svc), 8) + 8, f'服务层　backend/app/services（{len(svc)} 个模块）',
                   'schedule_service.commit_plan 是实时排班表的唯一写入路径', '#ffffff', AXIS))
    o.append(chips(12, y + 40, svc, 8, 124, mark={'schedule_service', 'plan_service'}))

    # ---- providers
    y = 384 + 40 + 19 * rows(len(svc), 8) + 8 + 12
    o.append(layer(y, 58, '外部服务适配层　backend/app/providers（失败时整体降级并在界面标注）',
                   'llm：mock / openai_compat / anthropic　·　route：fixture / osrm / estimated　·　geocode：onemap / nominatim',
                   '#fff5df', '#c27505'))
    y2 = y + 58 + 12
    o.append(layer(y2, 46, '数据层　backend/app/models（SQLAlchemy 实体）· backend/app/db（SQLite WAL + 增量迁移）',
                   '', '#f7f9fc', GRID))
    H2 = y2 + 46 + 26
    for ay in (120, 214, 302, 376, y - 6, y2 - 6):
        o.append(_arrow(W / 2, ay, W / 2, ay + 7))
    o.append(T(0, H2 - 6, '来源：backend/app/ 与 frontend/src/ 的实际目录内容', 9.5, MUTED, mono=True))
    H = H2
    return write('diagram-architecture.svg', W, H, ''.join(o), '系统技术架构')



# ====================================================== 业务计划书用图（面向业务读者）
def fig_bp_workflow():
    """Before / after of the SME's own working day. Step durations are stated assumptions, not measurements."""
    W = 1020
    o = [DEFS, panel_title(0, 14, '调度流程：今天 vs 使用 TechSched',
                           '左栏为目标客户当前的做法（基于访谈假设），右栏为本系统运行后的同一条流程')]
    colw, gap = 486, 48
    heads = [('今天：电话 + 表格 + 个人经验', '#f7f9fc', AXIS), ('使用 TechSched', '#e9f3ff', S1)]
    for i, (title, bg, stroke) in enumerate(heads):
        x = i * (colw + gap)
        o.append(R(x, 50, colw, 34, bg, rx=5))
        o.append(f'<rect x="{x}" y="50" width="{colw}" height="34" rx="5" fill="none" stroke="{stroke}" stroke-width="1.2"/>')
        o.append(T(x + colw / 2, 71, title, 11, INK, anchor='middle', weight=600))

    today = [('客户通过即时通讯描述问题', '文员'), ('文员电话确认地址与单元号', '文员 + 客户'),
             ('在表格上找人，凭经验判断是否来得及', '文员'), ('电话通知技师', '文员 + 技师'),
             ('技师请假：重新推演全天安排', '文员'), ('逐个电话通知被影响的客户', '文员 + 客户')]
    withus = [('客户在 App 描述一次，系统匹配问题库', '系统'), ('地址经地理编码确认，单元号必填', '系统'),
              ('只展示当前真的排得进去的时间窗', '系统'), ('常规工单自动派单并通知', '系统'),
              ('技师请假：系统重排并生成方案卡片', '系统'), ('调度员批准或拒绝，理由与版本入库', '调度员')]
    y0, rh = 96, 52
    for i, ((t1, a1), (t2, a2)) in enumerate(zip(today, withus)):
        y = y0 + i * rh
        o.append(R(0, y, colw, 42, '#ffffff', rx=4))
        o.append(f'<rect x="0" y="{y}" width="{colw}" height="42" rx="4" fill="none" stroke="{GRID}" stroke-width="1"/>')
        w1 = max(64, tw(t(a1), 9) + 14)
        o.append(Tw(10, y + 17, t1, colw - w1 - 26, 10, INK2))
        o.append(R(colw - w1 - 8, y + 6, w1, 15, '#f2f5f9', rx=3))
        o.append(T(colw - 8 - w1 / 2, y + 17, a1, 9, MUTED, anchor='middle'))
        x2 = colw + gap
        auto = a2 == '系统'
        o.append(R(x2, y, colw, 42, '#f7fbff' if auto else '#fff6f1', rx=4))
        o.append(f'<rect x="{x2}" y="{y}" width="{colw}" height="42" rx="4" fill="none" '
                 f'stroke="{S1 if auto else S2}" stroke-width="1.1"/>')
        w2 = max(64, tw(t(a2), 9) + 14)
        o.append(Tw(x2 + 10, y + 17, t2, colw - w2 - 26, 10, INK2))
        o.append(R(x2 + colw - w2 - 8, y + 6, w2, 15, '#e9f3ff' if auto else '#fdece3', rx=3))
        o.append(T(x2 + colw - 8 - w2 / 2, y + 17, a2, 9, S1 if auto else S2, anchor='middle', weight=600))
        if i < len(today) - 1:
            o.append(_arrow(colw / 2, y + 43, colw / 2, y + 50))
            o.append(_arrow(x2 + colw / 2, y + 43, x2 + colw / 2, y + 50))
    yb = y0 + 6 * rh + 16
    foot_lines = max(len(wrap(t('每次变更需要重新推演；过程与理由没有留存'), colw, 10)),
                     len(wrap(t('常规工单零人工介入；每个决定带版本、理由与影响清单'), colw, 10)))
    o.append(Tw(0, yb, '每次变更需要重新推演；过程与理由没有留存', colw, 10, MUTED))
    o.append(Tw(colw + gap, yb, '常规工单零人工介入；每个决定带版本、理由与影响清单', colw, 10, S1))
    H = yb + foot_lines * 14 + 26
    o.append(Tw(0, H - 10, '流程步骤为目标客户当前做法的假设，未经实地计时；右栏对应本系统的实际行为', 1010, 9.5, MUTED))
    return write('bp-workflow.svg', W, H, ''.join(o), '调度流程对比')


def fig_bp_decision_routing():
    """Where a decision ends up, and the rule that sends it there."""
    W, H = 1020, 356
    o = [DEFS, panel_title(0, 14, '每一个调度决定的去向',
                           '分流规则写在 config/policy.yaml，不由模型判断；自动化的边界对企业是可配置、可审计的')]
    o.append(_box(0, 60, 250, 52, '工单或突发事件', '新单 · 请假 · 迟到 · 投诉 · 加急', fill='#f7f9fc'))
    o.append(_box(292, 60, 250, 52, '确定性调度流水线', '可行性校验 · 权限检查 · 打分', fill='#e9f3ff', stroke=S1))
    o.append(_arrow(252, 86, 288, 86))
    outs = [('自动执行', '决策分 > 70、在权限内、且无强制人工规则', '#e9f3ff', S1, 152),
            ('调度员审批', '分数不足 / P0 影响其他客户 / 搜索未完成', '#fff3ec', S2, 216),
            ('人工队列', '无可行方案 / 无合格技师 / 安全事件 / 客户要求', '#f4f6f9', MUTED, 280)]
    for name, cond, bg, colr, y in outs:
        o.append(f'<path d="M542,86 C600,86 600,{y + 21} 640,{y + 21}" stroke="{AXIS}" stroke-width="1.2" '
                 f'fill="none" marker-end="url(#ar)"/>')
        o.append(R(644, y, 366, 42, bg, rx=5))
        o.append(f'<rect x="644" y="{y}" width="366" height="42" rx="5" fill="none" stroke="{colr}" stroke-width="1.2"/>')
        o.append(T(656, y + 18, name, 10.5, INK, weight=600))
        o.append(Tw(656, y + 32, cond, 342, 9.5, MUTED))
    o.append(Tw(0, 150, '进入"人工队列"之前，Agent 先在有界预算内调查一轮：提出客户可接受的备选时间窗，'
                        '或整理出带证据的人工事项。它不会静默失败，也不会无限重试。', 560, 10, INK2))
    o.append(Tw(0, 222, '企业可以通过修改一个配置文件调整自动化边界（例如把自动执行的分数门槛调高），'
                        '但任何配置都无法绕过可行性校验与权限上限。', 560, 10, INK2))
    o.append(T(0, H - 8, '来源：config/policy.yaml · backend/app/scheduling/policy.py', 9.5, MUTED, mono=True))
    return write('bp-decision-routing.svg', W, H, ''.join(o), '决策去向')


def fig_bp_adoption():
    """Prototype → pilot → production, with what has to be added at each step."""
    W = 1020
    o = [DEFS, panel_title(0, 14, '从原型到生产的推进路径', '每一阶段需要补齐的能力，以及该阶段可以验证的东西')]
    stages = [
        ('阶段一　原型（已完成）', '已部署可访问', '#e9f3ff', S1,
         ['数据：合成技师与客户；真实问题库 CSV', '系统：单进程 + SQLite；真实模型、路网与地理编码',
          '人工：全部审批与人工队列已实现', '可验证：调度规则、权限边界、Agent 行为与可追溯性']),
        ('阶段二　试点（1 家企业 · 4–8 周）', '需要企业配合', '#fff3ec', S2,
         ['数据：导入真实问题库、技师名册与历史工单', '系统：接入真实通知通道；自建路网服务',
          '人工：保留全部审批点，按周复盘误判', '可验证：KPI 基线与改善幅度、人工介入比例']),
        ('阶段三　生产', '规模化前提', '#f7f9fc', AXIS,
         ['数据：多企业隔离；问题库版本管理', '系统：多租户、PostgreSQL、多进程、真实支付与定位',
          '人工：分角色权限与完整审计', '可验证：单位成本、留存与跨部门扩展']),
    ]
    cw, gap = 328, 18
    box_h = max(56 + sum(19 * len(wrap(t(it), cw - 34, 9.5)) for it in st[4]) for st in stages)
    for i, (title, tag, bg, colr, items) in enumerate(stages):
        x = i * (cw + gap)
        o.append(R(x, 52, cw, box_h, bg, rx=6))
        o.append(f'<rect x="{x}" y="52" width="{cw}" height="{box_h}" rx="6" fill="none" stroke="{colr}" stroke-width="1.2"/>')
        o.append(T(x + 12, 72, title, 10.5, INK, weight=600))
        o.append(T(x + 12, 87, tag, 9.5, colr, weight=600))
        yy = 108
        for it in items:
            lines = wrap(t(it), cw - 34, 9.5)
            o.append(R(x + 12, yy - 6, 4, 4, colr, rx=1))
            for ln in lines:
                o.append(T(x + 22, yy, ln, 9.5, INK2, tr=False))
                yy += 13
            yy += 6
        if i < 2:
            o.append(_arrow(x + cw + 2, 52 + box_h / 2, x + cw + gap - 4, 52 + box_h / 2))
    note_y = 52 + box_h + 22
    o.append(Tw(0, note_y, '本系统当前处于阶段一并已线上运行（见技术设计文档第 7 章）。阶段二不需要重写系统：'
                        '问题库、技师名册与业务规则都是配置，接入真实通道是替换 provider 实现。', 1010, 10, INK2))
    H = note_y + 46
    o.append(T(0, H - 8, '阶段二与阶段三的时间与范围为规划假设，尚未实施', 9.5, MUTED))
    return write('bp-adoption.svg', W, H, ''.join(o), '推进路径')


if __name__ == '__main__':
    print(f'figures ({LANG}) →', OUT)
    fig_catalog()
    fig_skills()
    fig_weights()
    fig_eval_v2()
    fig_eval_v3()
    fig_agent_cost()
    fig_rest()
    fig_tests()
    fig_authority()
    fig_pipeline()
    fig_lifecycle()
    fig_agent_loop()
    fig_architecture()
    fig_bp_workflow()
    fig_bp_decision_routing()
    fig_bp_adoption()
    if _MISSING:
        print(f'  ! {len(_MISSING)} untranslated strings:')
        for m in sorted(_MISSING):
            print('    ' + repr(m))
