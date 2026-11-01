#!/usr/bin/env python3
"""
SAFARI RUN
==========
A zebra is grazing peacefully on the savanna... until a lion shows up.
Run, switch lanes and jump to survive. Hit anything and the lion catches you!

Controls
--------
  UP / W      move to the lane further away
  DOWN / S    move to the lane closer to you
  SPACE       jump (low obstacles only - tall ones need a lane change!)
  P           pause
  ESC         quit

Everything (zebra, lion, obstacles, scenery) is drawn with code.
No image files needed.  Only library required: pygame
"""
import math
import os
import random
import sys
from array import array

try:
    import pygame
except ImportError:
    print("pygame is missing.  Install it with:  pip install pygame")
    sys.exit(1)

# ----------------------------------------------------------------------------
# constants
# ----------------------------------------------------------------------------
W, H = 1100, 620
FPS = 60
HORIZON = 300
LANE_Y = [418, 486, 556]          # where feet touch the ground in each lane
LANE_S = [0.84, 0.97, 1.12]       # perspective scale of each lane
ZX = 560                          # zebra's fixed x position on screen
LION_PLAY_X = ZX - 340
LION_START_X = 175
GRAVITY = 2400.0
JUMP_V = 760.0                    # apex ~120px: clears low obstacles, not tall ones
TILE = 2400                       # world length of repeating ground decoration
BEST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "safari_best.txt")


def lerp(a, b, t):
    return a + (b - a) * t


def clamp(x, a, b):
    return max(a, min(b, x))


def lerpc(c1, c2, t):
    return tuple(int(lerp(a, b, t)) for a, b in zip(c1, c2))


def approach(v, target, step):
    if v < target:
        return min(v + step, target)
    return max(v - step, target)


def bez(p0, p1, p2, n=10):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
        pts.append((x, y))
    return pts


def scale_at(y):
    return clamp(0.84 + (y - 418) * (0.28 / 138.0), 0.55, 1.3)


def lane_pos(f):
    """Interpolated (y, scale) for a fractional lane index."""
    f = clamp(f, 0, 2)
    i = min(int(f), 1)
    t = f - i
    return lerp(LANE_Y[i], LANE_Y[i + 1], t), lerp(LANE_S[i], LANE_S[i + 1], t)


# ----------------------------------------------------------------------------
# smooth vector canvas: we draw big (3x) then shrink => anti-aliased sprites
# ----------------------------------------------------------------------------
class Cv:
    def __init__(self, w, h, ox, oy, S=3):
        self.w, self.h, self.ox, self.oy, self.S = w, h, ox, oy, S
        self.surf = pygame.Surface((w * S, h * S), pygame.SRCALPHA)

    def p(self, pt):
        return ((pt[0] + self.ox) * self.S, (pt[1] + self.oy) * self.S)

    def poly(self, col, pts):
        pygame.draw.polygon(self.surf, col, [self.p(q) for q in pts])

    def circ(self, col, x, y, r):
        pygame.draw.circle(self.surf, col, self.p((x, y)), max(1, r * self.S))

    def ell(self, col, cx, cy, rx, ry, ang=0.0, n=30):
        ca, sa = math.cos(ang), math.sin(ang)
        pts = []
        for i in range(n):
            t = 2 * math.pi * i / n
            x, y = rx * math.cos(t), ry * math.sin(t)
            pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
        self.poly(col, pts)

    def line(self, col, a, b, w1, w2=None):
        if w2 is None:
            w2 = w1
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L, dx / L
        self.poly(col, [(a[0] + nx * w1 / 2, a[1] + ny * w1 / 2),
                        (b[0] + nx * w2 / 2, b[1] + ny * w2 / 2),
                        (b[0] - nx * w2 / 2, b[1] - ny * w2 / 2),
                        (a[0] - nx * w1 / 2, a[1] - ny * w1 / 2)])
        self.circ(col, a[0], a[1], w1 / 2)
        self.circ(col, b[0], b[1], w2 / 2)

    def curve(self, col, pts, w1, w2):
        n = len(pts) - 1
        for i in range(n):
            self.line(col, pts[i], pts[i + 1], lerp(w1, w2, i / n), lerp(w1, w2, (i + 1) / n))

    def out(self):
        return pygame.transform.smoothscale(self.surf, (self.w, self.h))


def band(cv, col, c, d, half, thick):
    cv.line(col, (c[0] - d[0] * half, c[1] - d[1] * half),
            (c[0] + d[0] * half, c[1] + d[1] * half), thick)


def leg_points(hip, au, al, ul=30, ll=30):
    knee = (hip[0] + ul * math.sin(au), hip[1] + ul * math.cos(au))
    foot = (knee[0] + ll * math.sin(al), knee[1] + ll * math.cos(al))
    return knee, foot


def gallop_legs(ph, amp_f=0.95, amp_h=0.9, bend=1.2):
    def leg(p, amp):
        a = math.sin(p) * amp
        lift = max(0.0, math.cos(p))
        return (a, a - bend * lift)
    return [leg(ph, amp_f), leg(ph + 0.5, amp_f),
            leg(ph + math.pi * 0.95, amp_h), leg(ph + math.pi * 0.95 + 0.5, amp_h)]


# ----------------------------------------------------------------------------
# THE ZEBRA
# ----------------------------------------------------------------------------
Z_W, Z_H, Z_OX, Z_OY = 270, 210, 140, 165
Z_WHITE, Z_FAR, Z_DARK, Z_HOOF = (248, 248, 243), (206, 206, 212), (28, 26, 32), (48, 40, 40)
Z_HEAD = [(-8, -9), (6, -11), (22, -9), (34, -7), (41, -3), (42, 3), (38, 8), (24, 9), (8, 10), (-8, 8)]


def zebra_pose(kind, k):
    P = dict(head_t=0.5, tail=0.4, ear=0.0, jaw=0.0, wide=0.0, dy=0.0,
             legs=[(0.0, 0.0)] * 4)
    if kind == "graze":
        s = k / 40.0 * 2 * math.pi
        lift = max(0.0, math.sin(s - 1.2)) ** 2
        P["head_t"] = 2.25 - lift * 1.15 + 0.035 * math.sin(k * 1.4)
        P["tail"] = 0.3 + 0.3 * math.sin(s * 3)
        P["ear"] = 0.4 * max(0.0, math.sin(k * 0.55)) ** 6
        P["legs"] = [(0.14, 0.14), (-0.12, -0.12), (-0.06, -0.06), (0.1, 0.1)]
    elif kind == "alert":
        P["head_t"] = 0.28
        P["ear"] = -0.45
        P["wide"] = 1.0
        P["tail"] = 0.12 + 0.05 * math.sin(k)
        P["legs"] = [(0.3, -0.9), (-0.05, -0.05), (-0.1, -0.1), (0.1, 0.1)]
        P["dy"] = -3
    elif kind == "run":
        ph = k / 24.0 * 2 * math.pi
        P["legs"] = gallop_legs(ph)
        P["head_t"] = 0.95 + 0.1 * math.sin(2 * ph)
        P["ear"] = 0.55
        P["tail"] = 1.25 + 0.2 * math.sin(ph * 2)
        P["dy"] = -4 * abs(math.cos(ph))
        P["wide"] = 0.4
    elif kind == "jump":
        if k == 0:
            P["legs"] = [(1.15, 0.95), (0.95, 0.65), (-1.0, -0.7), (-1.25, -0.95)]
            P["head_t"] = 0.6
        else:
            P["legs"] = [(0.55, 0.3), (0.35, 0.1), (-0.5, -0.2), (-0.7, -0.4)]
            P["head_t"] = 1.2
        P["ear"] = 0.6
        P["tail"] = 1.35
        P["dy"] = -5
        P["wide"] = 0.6
    elif kind == "stumble":
        P["legs"] = [(0.95, 0.95), (0.75, 0.75), (-0.55, -0.25), (-0.35, 0.0)]
        P["head_t"] = 1.1
        P["jaw"] = 0.35
        P["wide"] = 1.0
        P["ear"] = 0.7
        P["tail"] = 1.5
        P["dy"] = -2
    elif kind == "bitten":
        s = k / 12.0 * 2 * math.pi
        P["legs"] = [(0.7 + 0.35 * math.sin(s), 0.5), (0.45 + 0.35 * math.sin(s + 1), 0.3),
                     (-0.7 + 0.35 * math.sin(s + 2), -0.5), (-0.9 + 0.35 * math.sin(s + 3), -0.7)]
        P["head_t"] = 0.1 + 0.05 * math.sin(s * 2)
        P["jaw"] = 1.0
        P["wide"] = 1.0
        P["ear"] = 0.95
        P["tail"] = 3.0
        P["dy"] = -6
    return P


def build_zebra(P):
    dy = P["dy"]
    base = Cv(Z_W, Z_H, Z_OX, Z_OY)
    dec = Cv(Z_W, Z_H, Z_OX, Z_OY)
    base.ell((0, 0, 0, 70), 0, 2, 58, 7)
    hips = [(40, -60 + dy), (30, -60 + dy), (-38, -60 + dy), (-48, -60 + dy)]   # nearF farF nearH farH
    legs = []
    for hip, (au, al) in zip(hips, P["legs"]):
        knee, foot = leg_points(hip, au, al)
        legs.append((hip, knee, foot))

    def draw_leg(cv, col, L):
        hip, knee, foot = L
        cv.line(col, hip, knee, 18, 11)
        cv.line(col, knee, foot, 11, 8)

    # far legs
    for i in (1, 3):
        draw_leg(base, Z_FAR, legs[i])
    # body
    base.ell(Z_WHITE, -8, -80 + dy, 60, 28)
    base.ell(Z_WHITE, -40, -80 + dy, 28, 28)
    base.ell(Z_WHITE, 30, -80 + dy, 28, 28)
    # neck
    t = P["head_t"]
    B = (34, -92 + dy)
    NL = 46
    dn = (math.sin(t), -math.cos(t))
    E = (B[0] + dn[0] * NL, B[1] + dn[1] * NL)
    base.line(Z_WHITE, B, E, 30, 17)
    # head
    h = t + 1.0
    d = (math.sin(h), -math.cos(h))
    ang = math.atan2(d[1], d[0])

    def hl(u, v):
        return (E[0] + d[0] * u - d[1] * v, E[1] + d[1] * u + d[0] * v)

    base.poly(Z_WHITE, [hl(*q) for q in Z_HEAD])
    # near legs
    for i in (0, 2):
        draw_leg(base, Z_WHITE, legs[i])

    # ---- stripes & shading on a separate layer, then masked by the body
    top, bot = -112 + dy, -40 + dy
    for x in range(-62, 60, 10):
        sx = x
        dec.poly(Z_DARK, [(sx - 2, top), (sx + 3.5, top), (sx + 1.5, (top + bot) / 2),
                          (sx - 5, bot), (sx - 9.5, bot), (sx - 6, (top + bot) / 2)])
    dec.ell((40, 30, 70, 55), 8, -55 + dy, 64, 9)
    for L in legs:
        hip, knee, foot = L
        for a, b in ((hip, knee), (knee, foot)):
            ln = math.hypot(b[0] - a[0], b[1] - a[1]) or 1
            dd = ((b[0] - a[0]) / ln, (b[1] - a[1]) / ln)
            perp = (-dd[1], dd[0])
            for s in (0.28, 0.62, 0.92):
                c = (lerp(a[0], b[0], s), lerp(a[1], b[1], s))
                band(dec, Z_DARK, c, perp, 9, 3.0)
    for i in range(7):
        s = 0.06 + i * 0.15
        c = (B[0] + dn[0] * NL * s, B[1] + dn[1] * NL * s)
        pr = (-dn[1], dn[0])
        ca, sa = math.cos(0.45), math.sin(0.45)
        pr2 = (pr[0] * ca - pr[1] * sa, pr[0] * sa + pr[1] * ca)
        band(dec, Z_DARK, c, pr2, 17, 3.3)
    for u in (4, 14, 24):
        band(dec, Z_DARK, hl(u, -4), (-d[1], d[0]), 6.5, 2.8)
    dec.surf.blit(base.surf, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    base.surf.blit(dec.surf, (0, 0))

    # ---- details drawn on top
    # hooves
    for L in legs:
        hip, knee, foot = L
        dd = (foot[0] - knee[0], foot[1] - knee[1])
        ln = math.hypot(*dd) or 1
        dd = (dd[0] / ln, dd[1] / ln)
        p1 = (foot[0] - dd[0] * 3, foot[1] - dd[1] * 3)
        base.line(Z_HOOF, p1, (foot[0] + dd[0] * 3, foot[1] + dd[1] * 3), 9.5, 9)
    # tail
    tb = (-62, -88 + dy)
    a = P["tail"]
    tip = (tb[0] - 44 * math.sin(a), tb[1] + 44 * math.cos(a))
    mid = (tb[0] - 24 * math.sin(a * 0.6), tb[1] + 24 * math.cos(a * 0.6))
    base.curve(Z_WHITE, bez(tb, mid, tip, 8), 8, 4)
    base.curve((90, 86, 92), bez(tb, mid, tip, 8)[5:], 3.5, 4)
    base.ell(Z_DARK, tip[0], tip[1], 6, 11, math.atan2(tip[1] - mid[1], tip[0] - mid[0]) + math.pi / 2)
    # mane
    for i in range(10):
        s = i / 9.0
        wd = lerp(30, 17, s) / 2
        nb = (-math.cos(t), -math.sin(t))
        pos = (B[0] + dn[0] * NL * s + nb[0] * wd, B[1] + dn[1] * NL * s + nb[1] * wd)
        tp = (pos[0] + nb[0] * 8 - dn[0] * 2, pos[1] + nb[1] * 8 - dn[1] * 2)
        base.poly(Z_DARK, [(pos[0] - dn[0] * 3.5, pos[1] - dn[1] * 3.5), tp,
                           (pos[0] + dn[0] * 3.5, pos[1] + dn[1] * 3.5)])
    # ears
    ear = P["ear"]
    for e, col in ((1, (214, 214, 220)), (0, Z_WHITE)):
        u0 = -2 - 4 * e
        v0 = -8 + 2 * e
        b1, b2 = hl(u0 - 5, v0), hl(u0 + 4, v0 - 1)
        tp = hl(u0 - 4 - 21 * math.sin(0.5 + ear), v0 - 6 - 21 * math.cos(0.5 + ear))
        base.poly(col, [b1, tp, b2])
        mid1 = (lerp(b1[0], tp[0], 0.35), lerp(b1[1], tp[1], 0.35))
        mid2 = (lerp(b2[0], tp[0], 0.35), lerp(b2[1], tp[1], 0.35))
        base.poly((70, 56, 64), [mid1, tp, mid2])
    # forelock
    base.poly(Z_DARK, [hl(-4, -9), hl(-12, -16), hl(4, -12), hl(10, -11)])
    # muzzle
    base.ell((74, 66, 72), *hl(37, 1), 8.5, 9, ang)
    base.circ((20, 18, 22), *hl(41, -2), 1.6)
    # mouth
    if P["jaw"] > 0.05:
        j = P["jaw"]
        m = hl(33, 6)
        base.ell((120, 30, 40), m[0], m[1], 8, 3 + 8 * j, ang + 0.4)
        base.ell((230, 110, 130), *hl(32, 8 + 2 * j), 4, 2 + 3 * j, ang)
        base.poly((255, 255, 255), [hl(36, 1), hl(39, 1), hl(37.5, 5)])
    else:
        base.line((40, 34, 40), hl(30, 6), hl(40, 6.5), 1.4)
    # eye
    ep = hl(15, -3)
    wide = P["wide"]
    if wide > 0.5:
        base.circ((255, 255, 255), *ep, 5.6)
        base.circ((20, 16, 20), ep[0] + d[0] * 0.8, ep[1] + d[1] * 0.8, 2.8)
        base.circ((255, 255, 255), ep[0] + 1, ep[1] - 1, 0.9)
    else:
        base.ell((22, 16, 20), ep[0], ep[1], 4, 3.4, ang)
        base.circ((255, 255, 255), ep[0] + 1.3, ep[1] - 1.2, 1.1)
    return (base.out(), Z_OX, Z_OY)


# ----------------------------------------------------------------------------
# THE LION
# ----------------------------------------------------------------------------
L_W, L_H, L_OX, L_OY = 330, 200, 185, 170
TAN, TAN_FAR, BELLY = (216, 162, 88), (176, 124, 64), (240, 208, 150)


def lion_pose(kind, k, jawq=0):
    P = dict(dy=0.0, legs=[(0.0, 0.0)] * 4, tail=0.7, jaw=0.0, head=(0.0, 0.0))
    if kind == "crouch":
        P["dy"] = 24
        P["legs"] = [(1.1, -0.3), (0.9, -0.3), (-1.0, 0.2), (-1.1, 0.3)]
        P["tail"] = 0.3 + 0.35 * math.sin(k * 0.8)
        P["head"] = (10, -14)
        P["jaw"] = 0.0
    elif kind == "roar":
        P["dy"] = -2
        P["legs"] = [(0.2, 0.2), (-0.1, -0.1), (-0.25, -0.25), (0.1, 0.1)]
        P["head"] = (6, -18 + (k % 2) * 2)
        P["jaw"] = 0.95
        P["tail"] = 0.9
    elif kind == "run":
        ph = k / 24.0 * 2 * math.pi
        P["legs"] = gallop_legs(ph, 1.05, 1.0, 0.95)
        P["dy"] = -5 * abs(math.cos(ph))
        P["head"] = (4, -4 + 3 * math.sin(ph * 2))
        P["jaw"] = 0.25 + 0.12 * math.sin(ph * 2)
        P["tail"] = 0.9 + 0.2 * math.sin(ph * 2)
    elif kind == "lunge":
        P["dy"] = -14
        P["legs"] = [(1.25, 1.05), (1.0, 0.85), (-1.1, -0.9), (-1.35, -1.05)]
        P["head"] = (10, -4)
        P["jaw"] = jawq / 8.0
        P["tail"] = 0.6
    elif kind == "bite":
        P["dy"] = -2
        P["legs"] = [(0.7, 0.55), (0.35, 0.25), (-0.45, -0.25), (-0.2, 0.0)]
        P["head"] = (12 + 3 * math.sin(k * 1.1), -6 + 3 * math.cos(k * 1.3))
        P["jaw"] = 0.06
        P["tail"] = 0.9 + 0.3 * math.sin(k)
    return P


def lion_head(cv, hx, hy, jaw, blink=False):
    SP_D, SP_L = (112, 58, 22), (150, 84, 32)
    for i in range(24):
        a = 2 * math.pi * i / 24
        r1 = 41 + (i % 2) * 6
        pts = [(hx + math.cos(a - 0.14) * 26, hy + math.sin(a - 0.14) * 26),
               (hx + math.cos(a) * r1, hy + math.sin(a) * r1),
               (hx + math.cos(a + 0.14) * 26, hy + math.sin(a + 0.14) * 26)]
        cv.poly(SP_D if i % 2 else SP_L, pts)
    cv.circ((150, 84, 32), hx, hy, 33)
    cv.circ((184, 112, 46), hx - 2, hy - 2, 27)
    # ears
    cv.circ(TAN, hx - 10, hy - 27, 8.5)
    cv.circ((200, 120, 110), hx - 9, hy - 26, 4.5)
    # face
    cv.ell(TAN, hx + 8, hy + 2, 22, 21)
    cv.ell((244, 214, 160), hx + 24, hy + 9, 16, 12)
    # mouth
    H_ = (hx + 14, hy + 14)
    U = (hx + 38, hy + 9)
    L0 = (hx + 36, hy + 15)
    ja = jaw * 0.95
    ca, sa = math.cos(ja), math.sin(ja)
    rv = (L0[0] - H_[0], L0[1] - H_[1])
    Lr = (H_[0] + rv[0] * ca - rv[1] * sa, H_[1] + rv[0] * sa + rv[1] * ca)
    if jaw > 0.08:
        cv.poly((96, 22, 30), [H_, U, Lr])
        cv.ell((226, 104, 120), (H_[0] + Lr[0]) / 2 + 3, (H_[1] + Lr[1]) / 2 - 2, 9, 3.5 + 4 * jaw, ja * 0.6)
        cv.line((244, 214, 160), H_, Lr, 8, 6)
        cv.poly((255, 255, 255), [(U[0] - 9, U[1] - 1), (U[0] - 3, U[1] - 1), (U[0] - 6, U[1] + 9)])
        cv.poly((255, 255, 255), [(Lr[0] - 8, Lr[1] + 1), (Lr[0] - 3, Lr[1] + 1), (Lr[0] - 5.5, Lr[1] - 8)])
    else:
        cv.line((120, 70, 50), (hx + 18, hy + 15), (hx + 37, hy + 14), 1.6)
    # nose
    cv.ell((100, 54, 56), hx + 38, hy + 4, 5.5, 4)
    for dxm, dym in ((26, 8), (30, 11), (24, 12)):
        cv.circ((150, 100, 80), hx + dxm, hy + dym, 0.9)
    # eye + angry brow
    if blink:
        cv.line((60, 30, 10), (hx + 9, hy - 6), (hx + 19, hy - 6), 2)
    else:
        cv.ell((244, 214, 70), hx + 14, hy - 6, 5.5, 4.2)
        cv.ell((20, 10, 6), hx + 15.5, hy - 6, 1.7, 3.6)
        cv.circ((255, 255, 255), hx + 13.5, hy - 7.5, 0.9)
    cv.line((90, 50, 20), (hx + 5, hy - 15), (hx + 23, hy - 8), 3.4, 2.2)


def build_lion(P):
    dy = P["dy"]
    base = Cv(L_W, L_H, L_OX, L_OY)
    dec = Cv(L_W, L_H, L_OX, L_OY)
    base.ell((0, 0, 0, 80), -4, 2, 78, 8)
    hips = [(38, -58 + dy), (28, -58 + dy), (-46, -58 + dy), (-58, -58 + dy)]
    legs = []
    for hip, (au, al) in zip(hips, P["legs"]):
        knee, foot = leg_points(hip, au, al, 29, 29)
        legs.append((hip, knee, foot))

    def draw_leg(col, L):
        hip, knee, foot = L
        base.line(col, hip, knee, 24, 16)
        base.line(col, knee, foot, 16, 13)

    # tail (behind everything)
    tb = (-68, -80 + dy)
    a = P["tail"]
    tip = (tb[0] - 58 * math.cos(a), tb[1] - 58 * math.sin(a) + 14)
    mid = (tb[0] - 32 * math.cos(a * 0.5), tb[1] - 32 * math.sin(a * 0.5) + 12)
    base.curve(TAN_FAR, bez(tb, mid, tip, 8), 8, 5)
    base.ell((112, 60, 24), tip[0], tip[1], 9, 12, math.atan2(tip[1] - mid[1], tip[0] - mid[0]) + math.pi / 2)
    for i in (1, 3):
        draw_leg(TAN_FAR, legs[i])
    base.ell(TAN, -8, -76 + dy, 64, 29)
    base.ell(TAN, -44, -72 + dy, 30, 31)
    base.ell(TAN, 34, -74 + dy, 31, 32)
    hx, hy = 88 + P["head"][0], -90 + dy + P["head"][1]
    base.line(TAN, (40, -84 + dy), (hx - 6, hy + 4), 44, 38)
    for i in (0, 2):
        draw_leg(TAN, legs[i])
    # shading
    dec.ell(BELLY, -4, -56 + dy, 52, 9)
    dec.ell((150, 100, 46, 90), -48, -72 + dy, 22, 26)
    dec.ell((150, 100, 46, 70), 32, -70 + dy, 16, 22)
    dec.ell((150, 100, 46, 60), -6, -100 + dy, 50, 6)
    dec.surf.blit(base.surf, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    base.surf.blit(dec.surf, (0, 0))
    # paws
    for L in legs:
        hip, knee, foot = L
        col = (236, 196, 136) if L in (legs[0], legs[2]) else (200, 156, 98)
        base.ell(col, foot[0] + 3, foot[1] - 1, 10, 6.5)
        for tx in (0, 4, 8):
            base.line((150, 100, 50), (foot[0] + tx - 1, foot[1] + 2), (foot[0] + tx, foot[1] - 3), 0.9)
    lion_head(base, hx, hy, P["jaw"])
    return (base.out(), L_OX, L_OY)


# ----------------------------------------------------------------------------
# OBSTACLES  (each returns (surface, ox, oy, half_width, height))
# ----------------------------------------------------------------------------
def mk_log():
    c = Cv(170, 90, 85, 80)
    c.ell((0, 0, 0, 70), 0, 2, 64, 8)
    c.poly((112, 72, 38), [(-58, -40), (48, -40), (48, -2), (-58, -2)])
    c.ell((100, 64, 32), -58, -21, 9, 19)
    c.poly((142, 98, 54), [(-58, -40), (48, -40), (48, -31), (-58, -31)])
    c.poly((84, 52, 26), [(-58, -10), (48, -10), (48, -2), (-58, -2)])
    for x, y, l in [(-42, -26, 30), (-8, -19, 34), (18, -29, 22), (-32, -12, 26), (10, -13, 20)]:
        c.line((76, 46, 22), (x, y), (x + l, y + 1), 2.2)
    c.poly((100, 64, 34), [(-20, -40), (-11, -40), (-7, -55), (-22, -53)])
    c.ell((178, 134, 82), -14, -54, 7.5, 3)
    c.ell((88, 130, 58), -36, -40, 15, 4.5)
    c.ell((108, 152, 68), -32, -42, 8, 2.6)
    c.ell((190, 146, 90), 48, -21, 10.5, 19)
    c.ell((160, 114, 64), 48, -21, 7.5, 14)
    c.ell((190, 146, 90), 48, -21, 5, 9.5)
    c.ell((150, 104, 56), 48, -21, 2.4, 4.8)
    return c.out(), 85, 80, 52, 42


def mk_rock():
    c = Cv(160, 90, 80, 80)
    c.ell((0, 0, 0, 70), 0, 2, 58, 8)
    c.poly((108, 108, 116), [(-58, 0), (-54, -13), (-42, -22), (-28, -18), (-26, 0)])
    c.poly((126, 126, 132), [(-36, 0), (-42, -20), (-27, -42), (0, -50), (26, -42), (42, -20), (38, 0)])
    c.poly((158, 158, 164), [(-42, -20), (-27, -42), (0, -50), (4, -30), (-18, -22)])
    c.poly((96, 96, 106), [(38, 0), (42, -20), (26, -42), (8, -26), (14, -4)])
    c.poly((100, 100, 108), [(26, 0), (36, -10), (52, -8), (56, 0)])
    c.line((70, 70, 80), (-6, -44), (-12, -26), 1.6)
    c.line((70, 70, 80), (-12, -26), (-4, -14), 1.6)
    c.ell((92, 138, 60), -30, -2, 9, 3.5)
    c.ell((92, 138, 60), 44, -1, 8, 3)
    return c.out(), 80, 80, 44, 48


def mk_thorn():
    c = Cv(170, 100, 85, 88)
    c.ell((0, 0, 0, 70), 0, 2, 58, 8)
    for x0, y0, x1, y1 in [(-30, -4, -44, -34), (-6, -4, -4, -50), (20, -4, 36, -40), (4, -4, 18, -26)]:
        c.line((90, 60, 34), (x0, y0), (x1, y1), 5, 3)
    blobs = [(-34, -26, 20), (-8, -36, 24), (22, -28, 22), (0, -18, 24), (-22, -14, 16), (34, -14, 15)]
    for x, y, r in blobs:
        c.circ((52, 86, 38), x, y, r)
    for x, y, r in blobs:
        c.circ((78, 120, 52), x - 2, y - 3, r * 0.78)
    for x, y, r in blobs[:4]:
        c.circ((108, 152, 70), x - 4, y - 7, r * 0.42)
    for i in range(30):
        a = random.Random(i).uniform(0, math.tau)
        r = random.Random(i + 99).uniform(10, 30)
        cx, cy = math.cos(a) * r * 1.4 - 2, -24 + math.sin(a) * r * 0.9
        c.poly((240, 232, 196), [(cx - 1.6, cy), (cx + 1.6, cy), (cx + math.cos(a) * 5, cy + math.sin(a) * 5)])
    for x, y in [(-14, -30), (14, -34), (-26, -14), (28, -22)]:
        c.circ((200, 40, 56), x, y, 2.6)
    return c.out(), 85, 88, 46, 58


def mk_mound():
    c = Cv(150, 180, 75, 168)
    c.ell((0, 0, 0, 70), 2, 2, 52, 8)
    sil = [(-46, 0), (-34, -40), (-24, -86), (-11, -130), (0, -154), (10, -132), (21, -90), (34, -46), (48, 0)]
    c.poly((168, 104, 60), sil)
    c.poly((198, 134, 84), [(-46, 0), (-34, -40), (-24, -86), (-11, -130), (0, -154), (-2, -80), (-14, -2)])
    c.poly((128, 74, 42), [(48, 0), (34, -46), (21, -90), (10, -132), (0, -154), (2, -80), (16, -2)])
    for y in range(-24, -150, -19):
        w = lerp(40, 4, (-y) / 154.0)
        c.line((112, 66, 36), (-w, y), (w, y + 3), 2.6)
    for x, y, r in [(-20, -60, 7), (14, -40, 6), (-8, -110, 5), (22, -86, 5), (-26, -22, 7), (30, -12, 6)]:
        c.ell((72, 40, 24), x, y, r, r * 0.6)
    c.ell((92, 140, 58), -42, -1, 10, 4)
    c.ell((92, 140, 58), 44, -1, 9, 3.5)
    return c.out(), 75, 168, 34, 150


def mk_stump():
    c = Cv(170, 190, 85, 178)
    c.ell((0, 0, 0, 70), 0, 2, 64, 9)
    for x0, y0, x1, y1, w in [(-46, -88, -76, -118, 11), (36, -108, 66, -132, 10), (-38, -112, -58, -150, 7)]:
        c.line((106, 82, 60), (x0, y0), (x1, y1), w, w * 0.6)
    c.poly((130, 104, 78), [(-52, 0), (-40, -30), (-34, -80), (-30, -130), (-30, -142), (-18, -152), (-11, -137),
                            (-2, -160), (8, -140), (17, -154), (28, -135), (33, -128), (36, -80), (44, -30), (56, 0)])
    c.poly((156, 128, 100), [(-52, 0), (-40, -30), (-34, -80), (-30, -130), (-30, -142), (-18, -152), (-12, -80), (-18, -2)])
    c.poly((98, 76, 54), [(56, 0), (44, -30), (36, -80), (33, -128), (28, -135), (17, -154), (14, -80), (24, -2)])
    for x, y0, y1 in [(-24, -140, -4), (-6, -150, -6), (8, -140, -4), (22, -130, -4)]:
        c.line((92, 70, 50), (x, y0), (x + 2, y1), 2)
    c.ell((52, 36, 28), 2, -80, 10, 18)
    c.ell((30, 20, 16), 3, -78, 7, 14)
    c.poly((130, 104, 78), [(-58, 0), (-40, -20), (-36, 0)])
    c.poly((130, 104, 78), [(60, 0), (44, -20), (40, 0)])
    return c.out(), 85, 178, 38, 154


def mk_fruit():
    c = Cv(50, 50, 25, 25, S=4)
    for r, a in ((22, 40), (17, 70)):
        c.circ((255, 220, 90, a), 0, 0, r)
    c.circ((226, 120, 24), 0, 0, 12)
    c.circ((250, 170, 44), -1.5, -1.5, 9.5)
    c.circ((255, 214, 120), -4, -4, 3.4)
    c.line((70, 110, 40), (0, -11), (3, -17), 2)
    c.ell((92, 150, 52), 7, -15, 6, 3, -0.5)
    return c.out()


def mk_acacia(seed):
    rng = random.Random(seed)
    c = Cv(260, 180, 130, 172)
    tcol, c1, c2 = (62, 48, 34), (62, 80, 40), (80, 102, 50)
    c.curve(tcol, bez((0, 0), (-6, -50), (-4 + rng.uniform(-8, 8), -92), 10), 12, 6)
    c.line(tcol, (-4, -88), (-48, -112), 6, 3)
    c.line(tcol, (-4, -88), (46, -118), 6, 3)
    c.line(tcol, (-4, -88), (0, -130), 5, 3)
    for cx, cy, rx, ry in [(-52, -112, 62, 15), (50, -120, 72, 16), (0, -134, 54, 13), (-22, -122, 50, 12)]:
        c.ell(c1, cx, cy, rx, ry)
    for cx, cy, rx, ry in [(-52, -118, 54, 9), (50, -126, 64, 10), (0, -140, 46, 8)]:
        c.ell(c2, cx, cy, rx, ry)
    return c.out(), 130, 172


def mk_giraffe():
    c = Cv(100, 150, 50, 140)
    col = (120, 112, 92)
    c.ell(col, 0, -62, 22, 11)
    for x in (-14, -7, 9, 16):
        c.line(col, (x, -58), (x + 1, 0), 4, 2.5)
    c.line(col, (14, -66), (30, -118), 8, 5)
    c.ell(col, 35, -122, 9, 5, -0.4)
    c.line(col, (-20, -66), (-28, -48), 2, 1.5)
    c.poly(col, [(30, -126), (28, -134), (32, -127)])
    return c.out(), 50, 140


def mk_tuft(seed):
    rng = random.Random(seed)
    c = Cv(60, 40, 30, 36)
    for i in range(11):
        x0 = rng.uniform(-14, 14)
        hgt = rng.uniform(14, 32)
        lean = rng.uniform(-9, 9)
        col = lerpc((120, 130, 50), (178, 170, 70), rng.random())
        c.poly(col, [(x0 - 1.8, 0), (x0 + 1.8, 0), (x0 + lean, -hgt)])
    return c.out(), 30, 36


def mk_bush():
    c = Cv(300, 130, 150, 118)
    c.ell((0, 0, 0, 60), 0, 4, 120, 10)
    rng = random.Random(7)
    for i in range(70):
        x = rng.uniform(-110, 110)
        hgt = rng.uniform(40, 82) * (1 - abs(x) / 230)
        lean = rng.uniform(-14, 14)
        col = lerpc((84, 108, 42), (150, 156, 62), rng.random())
        c.poly(col, [(x - 3.5, 2), (x + 3.5, 2), (x + lean, -hgt)])
    for cx, cy, r in [(-70, -26, 26), (-30, -34, 30), (20, -30, 28), (68, -24, 24), (100, -14, 18)]:
        c.circ((58, 88, 40), cx, cy, r)
        c.circ((80, 118, 52), cx - 3, cy - 4, r * 0.78)
        c.circ((104, 144, 66), cx - 6, cy - 9, r * 0.38)
    return c.out(), 150, 118


# ----------------------------------------------------------------------------
# sounds (tiny synthesizer - optional, silently skipped if no audio device)
# ----------------------------------------------------------------------------
def synth(f0, f1, dur, vol=0.35, noise=0.0, square=False):
    rate = 22050
    n = int(rate * dur)
    buf = array("h")
    ph = 0.0
    for i in range(n):
        t = i / n
        ph += 2 * math.pi * (f0 + (f1 - f0) * t) / rate
        s = math.sin(ph)
        if square:
            s = 1.0 if s > 0 else -1.0
        s = s * (1 - noise) + random.uniform(-1, 1) * noise
        env = min(1.0, t * 25) * (1 - t) ** 1.4
        buf.append(int(s * env * vol * 32767))
    return pygame.mixer.Sound(buffer=buf.tobytes())


# ----------------------------------------------------------------------------
# sky palettes (time of day)
# ----------------------------------------------------------------------------
KEYS = [
    (0.00, (120, 160, 225), (255, 214, 160), (255, 170, 90, 26)),
    (0.18, (66, 150, 240), (180, 224, 252), (0, 0, 0, 0)),
    (0.42, (88, 70, 150), (255, 148, 80), (255, 110, 50, 44)),
    (0.55, (12, 18, 52), (74, 60, 112), (6, 10, 50, 118)),
    (0.88, (12, 18, 52), (74, 60, 112), (6, 10, 50, 118)),
    (1.00, (120, 160, 225), (255, 214, 160), (255, 170, 90, 26)),
]


def sky_at(c):
    for i in range(len(KEYS) - 1):
        a, b = KEYS[i], KEYS[i + 1]
        if a[0] <= c <= b[0]:
            t = (c - a[0]) / (b[0] - a[0])
            t = t * t * (3 - 2 * t)
            return lerpc(a[1], b[1], t), lerpc(a[2], b[2], t), lerpc(a[3], b[3], t)
    return KEYS[0][1], KEYS[0][2], KEYS[0][3]


# ----------------------------------------------------------------------------
# THE GAME
# ----------------------------------------------------------------------------
class Game:
    def __init__(self, screen=None):
        self.screen = screen or pygame.display.set_mode((W, H))
        self.canvas = pygame.Surface((W, H))
        self.fonts = {}
        self.best = self.load_best()
        self.sounds = {}
        self.init_sounds()
        self.load_assets()
        self.light = pygame.Surface((W, H), pygame.SRCALPHA)
        self.reset()

    # ----- setup -----------------------------------------------------------
    def font(self, size):
        if size not in self.fonts:
            f = None
            for name in ("arialrounded", "trebuchetms", "verdana", "arial"):
                path = pygame.font.match_font(name, bold=True)
                if path:
                    f = pygame.font.Font(path, size)
                    break
            self.fonts[size] = f or pygame.font.Font(None, int(size * 1.2))
        return self.fonts[size]

    def load_best(self):
        try:
            with open(BEST_FILE) as fh:
                return int(fh.read().strip())
        except Exception:
            return 0

    def save_best(self):
        try:
            with open(BEST_FILE, "w") as fh:
                fh.write(str(self.best))
        except Exception:
            pass

    def init_sounds(self):
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(22050, -16, 1, 512)
            self.sounds = {
                "jump": synth(260, 520, 0.16, 0.25),
                "fruit": synth(780, 1250, 0.12, 0.22, square=True),
                "lane": synth(300, 200, 0.06, 0.15, noise=0.4),
                "roar": synth(150, 60, 0.9, 0.45, noise=0.55),
                "chomp": synth(220, 70, 0.22, 0.5, noise=0.7),
                "yelp": synth(500, 1100, 0.35, 0.3, square=True),
                "alert": synth(900, 1200, 0.15, 0.25),
            }
        except Exception:
            self.sounds = {}

    def play(self, name):
        s = self.sounds.get(name)
        if s:
            s.play()

    def progress(self, msg, frac):
        self.canvas.fill((24, 20, 16))
        self.text(msg, 34, W // 2, H // 2 - 14, (255, 235, 190))
        pygame.draw.rect(self.canvas, (70, 60, 50), (W // 2 - 200, H // 2 + 24, 400, 14), border_radius=7)
        pygame.draw.rect(self.canvas, (240, 190, 70), (W // 2 - 200, H // 2 + 24, int(400 * frac), 14), border_radius=7)
        self.screen.blit(self.canvas, (0, 0))
        pygame.display.flip()
        pygame.event.pump()

    def load_assets(self):
        self.zebra = {}
        self.lion = {}
        jobs = []
        for k in range(40):
            jobs.append(("z", "graze", k, 0))
        jobs.append(("z", "alert", 0, 0))
        jobs.append(("z", "alert", 1, 0))
        for k in range(24):
            jobs.append(("z", "run", k, 0))
        jobs += [("z", "jump", 0, 0), ("z", "jump", 1, 0), ("z", "stumble", 0, 0)]
        for k in range(12):
            jobs.append(("z", "bitten", k, 0))
        for k in range(6):
            jobs.append(("l", "crouch", k, 0))
        for k in range(2):
            jobs.append(("l", "roar", k, 0))
        for k in range(24):
            jobs.append(("l", "run", k, 0))
        for q in range(9):
            jobs.append(("l", "lunge", 0, q))
        for k in range(12):
            jobs.append(("l", "bite", k, 0))
        n = len(jobs)
        for i, (who, kind, k, q) in enumerate(jobs):
            if i % 6 == 0:
                self.progress("Waking up the savanna...", i / n)
            if who == "z":
                self.zebra[(kind, k)] = build_zebra(zebra_pose(kind, k))
            else:
                self.lion[(kind, k, q)] = build_lion(lion_pose(kind, k, q))
        self.obst = {
            "log": mk_log(), "rock": mk_rock(), "thorn": mk_thorn(),
            "mound": mk_mound(), "stump": mk_stump(),
        }
        self.fruit_img = mk_fruit()
        self.acacias = [mk_acacia(s) for s in (1, 2, 3)]
        self.giraffe = mk_giraffe()
        self.tufts = [mk_tuft(s) for s in range(4)]
        self.bush_img = mk_bush()
        self.obst_cache = {}
        self.build_background()

    def build_background(self):
        rng = random.Random(3)
        # ground
        gh = H - HORIZON
        self.ground = pygame.Surface((W, gh))
        for y in range(gh):
            t = y / gh
            pygame.draw.line(self.ground, lerpc((190, 164, 88), (136, 104, 54), t ** 0.8), (0, y), (W, y))
        for i in range(2400):
            x, y = rng.randrange(W), rng.randrange(gh)
            sh = rng.choice((-16, -10, 10, 14))
            c = self.ground.get_at((x, y))
            self.ground.set_at((x, y), (clamp(c[0] + sh, 0, 255), clamp(c[1] + sh, 0, 255), clamp(c[2] + sh, 0, 255)))
        lane_surf = pygame.Surface((W, gh), pygame.SRCALPHA)
        for ly, ls in zip(LANE_Y, LANE_S):
            pygame.draw.ellipse(lane_surf, (96, 66, 34, 54), (-100, ly - HORIZON - 20 * ls, W + 200, 38 * ls))
        self.ground.blit(lane_surf, (0, 0))
        # mountains (periodic)
        self.mtn = []
        for (col, base, amp, seed) in (((176, 178, 206), 54, 40, 0), ((148, 158, 160), 34, 30, 1)):
            r = random.Random(seed)
            tw = 1600
            surf = pygame.Surface((tw, 140), pygame.SRCALPHA)
            ph = [r.uniform(0, 6) for _ in range(3)]
            pts = [(0, 140)]
            for x in range(0, tw + 1, 8):
                a = 2 * math.pi * x / tw
                hgt = base + amp * 0.5 * math.sin(a + ph[0]) + amp * 0.3 * math.sin(3 * a + ph[1]) + amp * 0.18 * math.sin(7 * a + ph[2])
                pts.append((x, 140 - hgt))
            pts.append((tw, 140))
            pygame.draw.polygon(surf, col, pts)
            self.mtn.append(surf)
        # far scenery: acacias and giraffes placed on a 2400 tile
        self.far_items = []
        for i in range(7):
            self.far_items.append(("tree", rng.uniform(0, 2400), rng.choice((0, 1, 2)), rng.uniform(0.38, 0.62)))
        for x in (300, 1250, 1900):
            self.far_items.append(("giraffe", x, 0, 0.42))
        # clouds
        self.clouds = []
        for i in range(7):
            cs = pygame.Surface((220, 80), pygame.SRCALPHA)
            for j in range(7):
                pygame.draw.ellipse(cs, (255, 255, 255, 120), (rng.randint(0, 120), rng.randint(14, 36), rng.randint(60, 100), rng.randint(24, 40)))
            self.clouds.append([cs, rng.uniform(0, W + 300), rng.uniform(30, 160), rng.uniform(5, 14)])
        # stars
        self.stars = [(rng.randrange(W), rng.randrange(HORIZON), rng.random()) for _ in range(90)]
        # ground decoration
        self.decos = []
        for i in range(110):
            ry = rng.uniform(HORIZON + 14, H - 6)
            self.decos.append([rng.choice(("tuft", "tuft", "tuft", "pebble", "flower")), rng.uniform(0, TILE), ry, rng.randrange(4)])
        self.decos.sort(key=lambda d: d[2])
        # foreground grass blades
        self.fg = []
        for i in range(60):
            self.fg.append((rng.uniform(0, 1500), rng.uniform(26, 56), rng.uniform(-8, 8), rng.random()))

    # ----- state -------------------------------------------------------------
    def reset(self, quick=False):
        self.state = "play" if quick else "title"
        self.gt = 0.0          # animation clock
        self.ti = 0.0          # intro timeline
        self.tp = 0.0          # time in play
        self.dist = 0.0
        self.speed = 330.0 if quick else 0.0
        self.anim = 0.0
        self.lane = 1
        self.lane_f = 1.0
        self.z = 0.0
        self.vz = 0.0
        self.air = False
        self.score_f = 0
        self.fruits_got = 0
        self.obstacles = []
        self.fruits = []
        self.particles = []
        self.texts = []
        self.since = 700.0
        self.shake = 0.0
        self.shake_t = 0.0
        self.tb = 0.0
        self.hit = None
        self.bush_x = 0.0      # world distance of the ambush bush
        self.lion_x = LION_PLAY_X if quick else LION_START_X
        self.lion_f = 1.0
        self.lion_phase = 0.0
        self.paused = False
        self.new_best = False
        self.alert_played = False
        self.roar_played = False
        self.clock_c = 0.02 + (random.random() * 0.0 if not quick else random.random() * 0.1)
        self.birds = [dict(x=random.uniform(100, 1000), y=random.uniform(50, 170), vx=random.uniform(-14, -5),
                           vy=0.0, ph=random.random() * 6) for _ in range(6)]
        self.scared = False
        self.stars_fx = []

    def start_intro(self):
        self.state = "intro"
        self.ti = 0.0

    # ----- input -------------------------------------------------------------
    def key(self, k):
        if k == pygame.K_ESCAPE:
            pygame.quit()
            sys.exit()
        if k == pygame.K_p and self.state == "play":
            self.paused = not self.paused
            return
        if self.state == "title":
            if k in (pygame.K_SPACE, pygame.K_RETURN):
                self.start_intro()
        elif self.state == "intro":
            if k in (pygame.K_SPACE, pygame.K_RETURN) and self.ti < 5.0:
                self.ti = 5.0           # skip to the chase
        elif self.state == "play" and not self.paused:
            if k in (pygame.K_UP, pygame.K_w):
                if self.lane > 0:
                    self.lane -= 1
                    self.play("lane")
            elif k in (pygame.K_DOWN, pygame.K_s):
                if self.lane < 2:
                    self.lane += 1
                    self.play("lane")
            elif k in (pygame.K_SPACE,):
                if not self.air:
                    self.vz = JUMP_V
                    self.air = True
                    self.play("jump")
                    self.dust(6)
        elif self.state == "over":
            if k in (pygame.K_SPACE, pygame.K_RETURN) and self.tb > 3.2:
                self.reset(quick=True)

    # ----- helpers -------------------------------------------------------------
    def dust(self, n, vmul=1.0):
        yf, s = lane_pos(self.lane_f)
        for _ in range(n):
            self.particles.append(dict(kind="dust", x=ZX - 40 + random.uniform(-10, 10), y=yf - 4 * s,
                                       vx=-random.uniform(60, 180) * vmul - self.speed * 0.2, vy=-random.uniform(10, 70),
                                       life=random.uniform(0.35, 0.7), max=0.7, r=random.uniform(5, 11)))

    def sparkle(self, x, y, n=8, col=(255, 224, 90)):
        for _ in range(n):
            a = random.uniform(0, math.tau)
            sp = random.uniform(80, 240)
            self.particles.append(dict(kind="star", x=x, y=y, vx=math.cos(a) * sp, vy=math.sin(a) * sp - 60,
                                       life=random.uniform(0.4, 0.8), max=0.8, r=random.uniform(4, 8), col=col,
                                       rot=random.uniform(0, 3)))

    def add_text(self, txt, x, y, size=40, col=(255, 240, 120), life=1.2, rot=0.0, vy=-30):
        self.texts.append(dict(txt=txt, x=x, y=y, size=size, col=col, life=life, max=life, rot=rot, vy=vy))

    # ----- update -------------------------------------------------------------
    def update(self, dt):
        if self.paused:
            return
        self.gt += dt
        self.clock_c = (self.clock_c + dt / 110.0) % 1.0 if self.state in ("play", "bite") else self.clock_c
        self.shake = max(0.0, self.shake - dt * 30)
        for b in self.birds:
            b["ph"] += dt * 9
            if self.scared:
                b["vx"] += 260 * dt
                b["vy"] -= 120 * dt
            else:
                b["vy"] = math.sin(b["ph"] * 0.2) * 9
            b["x"] += b["vx"] * dt - self.speed * 0.05 * dt
            b["y"] += b["vy"] * dt
            if b["x"] < -40 and not self.scared:
                b["x"] = W + 40
            if self.scared and (b["x"] > W + 60 or b["y"] < -60):
                b["x"] = -50
                b["y"] = random.uniform(60, 170)
                b["vx"] = random.uniform(-14, -5)
                b["vy"] = 0
        if self.state == "title":
            return self.update_particles(dt)
        if self.state == "intro":
            self.update_intro(dt)
        elif self.state == "play":
            self.update_play(dt)
        elif self.state in ("bite", "over"):
            self.update_bite(dt)
        self.update_particles(dt)

    def update_intro(self, dt):
        self.ti += dt
        ti = self.ti
        if ti >= 3.2 and not self.alert_played:
            self.alert_played = True
            self.play("alert")
            self.add_text("!", ZX + 70, lane_pos(1)[0] - 200, 90, (255, 80, 60), 1.3, vy=-10)
        if ti >= 4.3 and not self.roar_played:
            self.roar_played = True
            self.play("roar")
            self.shake = 14
            self.scared = True
            self.add_text("ROAAAAR!", 250, 250, 64, (255, 200, 70), 1.4, rot=8, vy=-14)
        if ti >= 5.3:
            self.speed = approach(self.speed, 430.0, 400 * dt)
            self.lion_x = approach(self.lion_x, LION_PLAY_X, 70 * dt)
            self.dist += self.speed * dt
            self.anim += dt * (10 + self.speed * 0.008)
            self.dust_timer = getattr(self, "dust_timer", 0) - dt
            if self.dust_timer <= 0:
                self.dust(1)
                self.dust_timer = 0.06
        if ti >= 6.6:
            self.state = "play"
            self.tp = 0.0
            self.add_text("RUN!", W // 2, 210, 84, (255, 236, 120), 1.2, vy=-20)

    def spawn_row(self):
        types_low = ["log", "rock", "thorn"]
        types_tall = ["mound", "stump"]
        r = random.random()
        dx = 800.0
        lanes = [0, 1, 2]
        row = []
        diff = clamp(self.tp / 60.0, 0, 1)
        if r < 0.40:
            ln = random.choice(lanes)
            t = random.choice(types_low + types_tall)
            row.append((t, ln))
        elif r < 0.78 - 0.1 * diff:
            a, b = random.sample(lanes, 2)
            row.append((random.choice(types_low + types_tall), a))
            row.append((random.choice(types_low + types_tall), b))
        elif r < 0.9:
            a, b = random.sample(lanes, 2)
            row.append((random.choice(types_tall), a))
            row.append((random.choice(types_tall), b))
        else:
            for ln in lanes:
                row.append((random.choice(types_low), ln))
        for t, ln in row:
            hw, h = self.obst[t][3], self.obst[t][4]
            self.obstacles.append(dict(type=t, lane=ln, dx=dx, hw=hw, h=h))
        used = {ln for _, ln in row}
        free = [ln for ln in lanes if ln not in used]
        if free and random.random() < 0.55:
            ln = random.choice(free)
            for i in range(5):
                self.fruits.append(dict(lane=ln, dx=dx - 120 + i * 62, h=38, got=False))
        elif len(row) == 1 and row[0][0] in types_low and random.random() < 0.6:
            ln = row[0][1]
            for i in range(5):
                u = i / 4.0
                self.fruits.append(dict(lane=ln, dx=dx - 120 + i * 60, h=40 + math.sin(u * math.pi) * 78, got=False))

    def update_play(self, dt):
        self.tp += dt
        # speed ramps up
        target = min(930.0, 430.0 + self.tp * 8.5)
        self.speed = approach(self.speed, target, 220 * dt)
        self.dist += self.speed * dt
        self.anim += dt * (10 + self.speed * 0.008)
        # lane movement
        self.lane_f = approach(self.lane_f, float(self.lane), 7.0 * dt)
        # jump physics
        if self.air:
            self.vz -= GRAVITY * dt
            self.z += self.vz * dt
            if self.z <= 0:
                self.z = 0.0
                self.vz = 0.0
                self.air = False
                self.dust(5)
        # lion follows
        gap = 330 - min(50, self.tp * 0.8)
        self.lion_x = lerp(self.lion_x, ZX - gap + 10 * math.sin(self.tp * 2.2), min(1, dt * 3))
        self.lion_f = approach(self.lion_f, self.lane_f, 2.4 * dt)
        self.lion_phase += dt * (10.5 + self.speed * 0.008)
        # dust
        self.dust_timer = getattr(self, "dust_timer", 0) - dt
        if self.dust_timer <= 0 and not self.air:
            self.dust(1)
            self.dust_timer = 0.07
        # spawn
        self.since -= self.speed * dt
        if self.since <= 0:
            self.spawn_row()
            self.since = self.speed * random.uniform(0.85, 1.35) + 170
        for o in self.obstacles:
            o["dx"] -= self.speed * dt
        for f in self.fruits:
            f["dx"] -= self.speed * dt
        self.obstacles = [o for o in self.obstacles if o["dx"] > -760]
        self.fruits = [f for f in self.fruits if f["dx"] > -760 and not f["got"]]
        # collisions
        for o in self.obstacles:
            if abs(self.lane_f - o["lane"]) < 0.5 and abs(o["dx"] - 8) < o["hw"] + 34 and self.z < o["h"] - 10:
                self.start_bite(o)
                return
        for f in self.fruits:
            if abs(self.lane_f - f["lane"]) < 0.55 and abs(f["dx"]) < 46 and f["h"] - 75 < self.z < f["h"] + 45:
                f["got"] = True
                self.fruits_got += 1
                self.play("fruit")
                yf, s = lane_pos(self.lane_f)
                self.sparkle(ZX + 30, yf - (f["h"] + 20) * s, 7)
                self.add_text("+10", ZX + 40, yf - 170 * s, 30, (255, 236, 120), 0.8)
        self.score_f = int(self.dist / 45) + self.fruits_got * 10

    def start_bite(self, o):
        self.state = "bite"
        self.tb = 0.0
        self.hit = o
        self.lion_start = self.lion_x
        self.lane = int(round(self.lane_f))
        self.chomped = False
        self.play("roar")
        self.score_f = int(self.dist / 45) + self.fruits_got * 10
        if self.score_f > self.best:
            self.best = self.score_f
            self.new_best = True
            self.save_best()
        self.shake = 8

    def update_bite(self, dt):
        self.tb += dt
        tb = self.tb
        self.speed = approach(self.speed, 0.0, 2600 * dt)
        self.dist += self.speed * dt
        for o in self.obstacles:
            o["dx"] -= self.speed * dt
        for f in self.fruits:
            f["dx"] -= self.speed * dt
        self.lane_f = approach(self.lane_f, round(self.lane_f), 4 * dt)
        self.lion_f = approach(self.lion_f, self.lane_f, 6 * dt)
        target = ZX - 142
        if tb < 0.45:
            t = clamp(tb / 0.45, 0, 1)
            e = 1 - (1 - t) ** 3
            self.lion_x = lerp(self.lion_start, target, e)
        else:
            self.lion_x = target
        # zebra hop from the bite
        if tb >= 0.45:
            if not self.chomped:
                self.chomped = True
                self.play("chomp")
                self.play("yelp")
                self.shake = 22
                yf, s = lane_pos(self.lane_f)
                self.sparkle(ZX - 60, yf - 85 * s, 14, (255, 250, 200))
                self.sparkle(ZX - 60, yf - 85 * s, 8, (255, 170, 60))
                self.add_text("CHOMP!", ZX - 150, yf - 190 * s, 70, (255, 120, 60), 1.8, rot=-8, vy=-12)
                self.add_text("YEEOWWW!!", ZX + 90, yf - 250 * s, 52, (255, 255, 255), 2.0, rot=7, vy=-10)
                self.air = True
                self.vz = 520.0
            if self.air:
                self.vz -= GRAVITY * dt * 0.8
                self.z += self.vz * dt
                if self.z <= 0:
                    self.z = 0.0
                    self.vz = 0.0
                    self.air = False
        if tb > 2.0 and self.state == "bite":
            self.state = "over"

    def update_particles(self, dt):
        for p in self.particles:
            p["life"] -= dt
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            if p["kind"] == "star":
                p["vy"] += 500 * dt
                p["rot"] = p.get("rot", 0) + dt * 8
            else:
                p["vy"] -= 20 * dt
        self.particles = [p for p in self.particles if p["life"] > 0]
        for t in self.texts:
            t["life"] -= dt
            t["y"] += t["vy"] * dt
        self.texts = [t for t in self.texts if t["life"] > 0]

    # ----- drawing: text -------------------------------------------------------
    def text(self, s, size, x, y, col=(255, 255, 255), outline=(40, 24, 10), anchor="center", rot=0.0, alpha=255, surf=None):
        surf = surf or self.canvas
        f = self.font(size)
        ow = max(2, size // 14)
        base = f.render(s, True, col)
        out = f.render(s, True, outline)
        w, h = base.get_size()
        comp = pygame.Surface((w + ow * 2, h + ow * 2), pygame.SRCALPHA)
        for ox in range(-ow, ow + 1):
            for oy in range(-ow, ow + 1):
                if ox * ox + oy * oy <= ow * ow + 1:
                    comp.blit(out, (ow + ox, ow + oy))
        comp.blit(base, (ow, ow))
        if rot:
            comp = pygame.transform.rotate(comp, rot)
        if alpha < 255:
            comp.set_alpha(alpha)
        r = comp.get_rect()
        if anchor == "center":
            r.center = (x, y)
        elif anchor == "left":
            r.midleft = (x, y)
        else:
            r.midright = (x, y)
        surf.blit(comp, r)

    # ----- drawing: scene --------------------------------------------------------
    def draw_sky(self, cv):
        top, bot, ov = sky_at(self.clock_c)
        self.cur_overlay = ov
        for y in range(0, HORIZON + 40, 4):
            t = clamp(y / (HORIZON + 10), 0, 1)
            pygame.draw.rect(cv, lerpc(top, bot, t ** 1.2), (0, y, W, 4))
        c = self.clock_c
        night = clamp((c - 0.46) / 0.1, 0, 1) * clamp((0.94 - c) / 0.08, 0, 1)
        if night > 0.01:
            for x, y, b in self.stars:
                a = int(255 * night * (0.4 + 0.6 * abs(math.sin(self.gt * (0.8 + b) + b * 9))))
                pygame.draw.circle(cv, (a, a, min(255, a + 20)), (x, y), 1 + (b > 0.85))
        # sun / moon
        if c < 0.5:
            p = c / 0.5
            sx, sy = 100 + p * (W - 200), HORIZON + 30 - math.sin(math.pi * p) * (HORIZON - 70)
            for r, a in ((120, 26), (80, 40), (52, 70)):
                gs = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
                pygame.draw.circle(gs, (255, 236, 170, a), (r, r), r)
                cv.blit(gs, (sx - r, sy - r))
            pygame.draw.circle(cv, (255, 246, 210), (int(sx), int(sy)), 34)
        else:
            p = (c - 0.5) / 0.5
            sx, sy = 100 + p * (W - 200), HORIZON + 30 - math.sin(math.pi * p) * (HORIZON - 70)
            gs = pygame.Surface((160, 160), pygame.SRCALPHA)
            pygame.draw.circle(gs, (210, 220, 255, 36), (80, 80), 80)
            cv.blit(gs, (sx - 80, sy - 80))
            pygame.draw.circle(cv, (236, 238, 250), (int(sx), int(sy)), 26)
            pygame.draw.circle(cv, (210, 214, 232), (int(sx) - 8, int(sy) - 5), 6)
            pygame.draw.circle(cv, (210, 214, 232), (int(sx) + 9, int(sy) + 7), 4)
        # clouds
        for cl in self.clouds:
            x = (cl[1] - self.gt * cl[3] - self.dist * 0.02) % (W + 300) - 220
            cv.blit(cl[0], (x, cl[2]))

    def draw_far(self, cv):
        d = self.dist
        for i, (surf, f) in enumerate(zip(self.mtn, (0.03, 0.08))):
            off = int(d * f) % 1600
            y = HORIZON - (110 if i == 0 else 70)
            cv.blit(surf, (-off, y))
            cv.blit(surf, (1600 - off, y))
        # trees & giraffes
        items = sorted(self.far_items, key=lambda it: it[3])
        for kind, x, v, sc in items:
            f = 0.14 + sc * 0.3
            sx = ((x - d * f) % 2400) - 300
            if kind == "tree":
                surf, ox, oy = self.acacias[v]
            else:
                surf, ox, oy = self.giraffe
            w, h = surf.get_size()
            ss = pygame.transform.smoothscale(surf, (int(w * sc * 1.25), int(h * sc * 1.25)))
            cv.blit(ss, (sx - ox * sc * 1.25, HORIZON + 6 - oy * sc * 1.25))

    def draw_ground(self, cv):
        cv.blit(self.ground, (0, HORIZON))
        d = self.dist
        for kind, rx, ry, v in self.decos:
            s = scale_at(ry)
            dx = ((rx - d + 1100) % TILE) - 1100
            sx = ZX + dx * s
            if sx < -60 or sx > W + 60:
                continue
            if kind == "tuft":
                surf, ox, oy = self.tufts[v]
                w, h = surf.get_size()
                sc = s * 0.8
                cv.blit(pygame.transform.smoothscale(surf, (max(2, int(w * sc)), max(2, int(h * sc)))), (sx - ox * sc, ry - oy * sc))
            elif kind == "pebble":
                pygame.draw.ellipse(cv, (150, 140, 120), (sx - 5 * s, ry - 3 * s, 10 * s, 6 * s))
                pygame.draw.ellipse(cv, (178, 168, 146), (sx - 4 * s, ry - 3 * s, 6 * s, 3 * s))
            else:
                pygame.draw.circle(cv, (250, 240, 250), (int(sx), int(ry)), max(1, int(3 * s)))
                pygame.draw.circle(cv, (240, 190, 60), (int(sx), int(ry)), max(1, int(1.4 * s)))

    def draw_sprite(self, cv, spr, x, y, s, alpha=255):
        surf, ox, oy = spr
        w, h = surf.get_size()
        sw, sh = max(2, int(w * s)), max(2, int(h * s))
        img = pygame.transform.smoothscale(surf, (sw, sh)) if abs(s - 1) > 0.01 else surf
        if alpha < 255:
            img = img.copy()
            img.set_alpha(alpha)
        cv.blit(img, (x - ox * s, y - oy * s))

    def obst_sprite(self, t, lane):
        key = (t, lane)
        if key not in self.obst_cache:
            surf, ox, oy, hw, h = self.obst[t]
            s = LANE_S[lane]
            w, hh = surf.get_size()
            self.obst_cache[key] = (pygame.transform.smoothscale(surf, (int(w * s), int(hh * s))), ox * s, oy * s)
        return self.obst_cache[key]

    def zebra_sprite(self):
        st = self.state
        if st in ("title", "intro"):
            ti = self.ti
            if st == "intro" and ti >= 5.3:
                return self.zebra[("run", int(self.anim / (2 * math.pi) * 24) % 24)]
            if st == "intro" and ti >= 3.2:
                return self.zebra[("alert", int(ti * 12) % 2)]
            return self.zebra[("graze", int(self.gt * 10) % 40)]
        if st == "play":
            if self.air:
                return self.zebra[("jump", 0 if self.vz > 0 else 1)]
            return self.zebra[("run", int(self.anim / (2 * math.pi) * 24) % 24)]
        if st in ("bite", "over"):
            if self.tb < 0.45:
                return self.zebra[("stumble", 0)]
            return self.zebra[("bitten", int(self.tb * 18) % 12)]
        return self.zebra[("graze", 0)]

    def lion_sprite(self):
        st = self.state
        if st in ("title", "intro"):
            ti = self.ti
            if ti < 4.3:
                return self.lion[("crouch", int(self.gt * 6) % 6, 0)]
            if ti < 5.3:
                return self.lion[("roar", int(self.gt * 14) % 2, 0)]
            return self.lion[("run", int(self.anim / (2 * math.pi) * 24) % 24, 0)]
        if st == "play":
            return self.lion[("run", int(self.lion_phase / (2 * math.pi) * 24) % 24, 0)]
        if self.tb < 0.45:
            return self.lion[("lunge", 0, int(clamp(self.tb / 0.35, 0, 1) * 8))]
        return self.lion[("bite", int(self.tb * 20) % 12, 0)]

    def draw_scene(self, cv):
        self.draw_sky(cv)
        self.draw_far(cv)
        self.draw_ground(cv)
        ents = []
        zy, zs = lane_pos(self.lane_f)
        ly, ls = lane_pos(self.lion_f)
        for o in self.obstacles:
            ents.append((LANE_Y[o["lane"]], 0, ("ob", o)))
        for f in self.fruits:
            ents.append((LANE_Y[f["lane"]] + 1, 0, ("fr", f)))
        ents.append((zy + 0.5, 1, ("z", None)))
        show_lion = not (self.state in ("title",) or (self.state == "intro" and self.ti < 1.6))
        if show_lion:
            ents.append((ly + 6, 2, ("l", None)))
        if self.state in ("title", "intro"):
            by = LANE_Y[1] + 30
            ents.append((by if self.state != "intro" or self.ti < 5.3 else by, 3, ("bush", None)))
        ents.sort(key=lambda e: (e[0], e[1]))
        for y, _, (kind, obj) in ents:
            if kind == "ob":
                s = LANE_S[obj["lane"]]
                x = ZX + obj["dx"] * s
                if -200 < x < W + 200:
                    self.draw_sprite(cv, self.obst_sprite(obj["type"], obj["lane"]), x, y, 1.0)
            elif kind == "fr":
                s = LANE_S[obj["lane"]]
                x = ZX + obj["dx"] * s
                if -60 < x < W + 60:
                    bob = math.sin(self.gt * 5 + obj["dx"] * 0.02) * 4
                    self.draw_sprite(cv, (self.fruit_img, 25, 25), x, y - obj["h"] * s + bob, s * 0.95)
            elif kind == "z":
                sh = pygame.Surface((int(130 * zs), int(20 * zs)), pygame.SRCALPHA)
                k = clamp(1 - self.z / 260.0, 0.4, 1)
                pygame.draw.ellipse(sh, (0, 0, 0, int(90 * k)), sh.get_rect())
                cv.blit(sh, (ZX - sh.get_width() // 2, zy - sh.get_height() // 2))
                self.draw_sprite(cv, self.zebra_sprite(), ZX, zy - self.z * zs, zs)
            elif kind == "l":
                lx = self.lion_x
                self.draw_sprite(cv, self.lion_sprite(), lx, ly, ls * 1.04)
            elif kind == "bush":
                bx = LION_START_X - 10 - self.dist * 1.1
                if bx > -260:
                    self.draw_sprite(cv, self.bush_img, bx + (random.uniform(-3, 3) if (self.state == "intro" and 1.6 < self.ti < 4.3) else 0), y, 1.28)
        # foreground grass
        fy = H + 4
        for x, hgt, lean, v in self.fg:
            sx = ((x - self.dist * 1.7) % 1500) - 150
            if -20 < sx < W + 20:
                col = lerpc((96, 112, 44), (150, 150, 60), v)
                pygame.draw.polygon(cv, col, [(sx - 4, fy), (sx + 4, fy), (sx + lean, fy - hgt)])
        # birds
        for b in self.birds:
            wing = math.sin(b["ph"]) * 7
            pygame.draw.lines(cv, (40, 36, 40), False, [(b["x"] - 9, b["y"] - wing), (b["x"], b["y"]), (b["x"] + 9, b["y"] - wing)], 2)
        # particles
        for p in self.particles:
            a = clamp(p["life"] / p["max"], 0, 1)
            if p["kind"] == "dust":
                r = int(p["r"] * (1.4 - a * 0.6))
                if r > 0:
                    ds = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
                    pygame.draw.circle(ds, (206, 176, 124, int(150 * a)), (r, r), r)
                    cv.blit(ds, (p["x"] - r, p["y"] - r))
            else:
                r = p["r"] * (0.5 + a)
                rot = p.get("rot", 0)
                pts = []
                for i in range(8):
                    rr = r if i % 2 == 0 else r * 0.4
                    ang = rot + i * math.pi / 4
                    pts.append((p["x"] + math.cos(ang) * rr, p["y"] + math.sin(ang) * rr))
                pygame.draw.polygon(cv, p["col"], pts)
        # lighting overlay
        ov = self.cur_overlay
        if ov[3] > 0:
            self.light.fill(ov)
            cv.blit(self.light, (0, 0))
        for t in self.texts:
            a = clamp(t["life"] / t["max"] * 2.5, 0, 1)
            pop = 1 + 0.25 * max(0, 1 - (t["max"] - t["life"]) * 6)
            self.text(t["txt"], int(t["size"] * pop), t["x"], t["y"], t["col"], rot=t["rot"], alpha=int(255 * a))

    # ----- drawing: HUD / screens --------------------------------------------------
    def pill(self, x, y, w, h, alpha=140):
        s = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.rect(s, (30, 20, 10, alpha), (0, 0, w, h), border_radius=h // 2)
        self.canvas.blit(s, (x, y))

    def draw_hud(self):
        cv = self.canvas
        if self.state in ("play", "bite", "over"):
            self.pill(16, 14, 210, 50)
            self.text(f"{self.score_f if self.state != 'play' else self.score_f}", 34, 36, 40, (255, 240, 200), anchor="left")
            cv.blit(pygame.transform.smoothscale(self.fruit_img, (36, 36)), (160, 22))
            self.text(f"{self.fruits_got}", 26, 204, 40, (255, 230, 150), anchor="right")
            self.pill(W - 196, 14, 180, 50)
            self.text(f"BEST {max(self.best, self.score_f)}", 26, W - 106, 40, (255, 230, 150))
        if self.state == "title":
            self.text("SAFARI RUN", 96, W // 2, 150, (255, 224, 110), outline=(70, 34, 10))
            self.text("a zebra, a lion, and a very bad day", 28, W // 2, 214, (255, 248, 230))
            if int(self.gt * 2) % 2 == 0:
                self.text("Press SPACE to start", 38, W // 2, 276, (255, 255, 255))
            self.pill(W // 2 - 330, H - 70, 660, 46, 150)
            self.text("UP / DOWN: change lane     SPACE: jump     P: pause", 24, W // 2, H - 47, (255, 240, 210))
        elif self.state == "intro":
            cap = None
            if self.ti < 3.2:
                cap = "A calm morning on the savanna..."
            elif self.ti < 4.3:
                cap = "Wait... something is in the grass."
            elif self.ti < 5.3:
                cap = "Oh no."
            if cap:
                self.pill(W // 2 - 300, H - 78, 600, 50, 150)
                self.text(cap, 30, W // 2, H - 53, (255, 244, 220))
            if self.ti < 5.0:
                self.text("SPACE to skip", 20, W - 90, H - 20, (255, 255, 255), outline=(40, 30, 20))
        elif self.state == "play" and self.tp < 6:
            a = int(255 * clamp((6 - self.tp) / 1.2, 0, 1))
            self.pill(W // 2 - 330, H - 70, 660, 46, int(150 * a / 255))
            self.text("UP / DOWN: change lane   SPACE: jump (tall things need a lane change!)", 21, W // 2, H - 47, (255, 240, 210), alpha=a)
        if self.paused:
            self.light.fill((0, 0, 0, 120))
            cv.blit(self.light, (0, 0))
            self.text("PAUSED", 90, W // 2, H // 2 - 20, (255, 240, 200))
            self.text("press P to continue", 30, W // 2, H // 2 + 50, (255, 255, 255))
        if self.state == "over" and self.tb > 2.0:
            a = clamp((self.tb - 2.0) * 2.5, 0, 1)
            panel = pygame.Surface((560, 300), pygame.SRCALPHA)
            pygame.draw.rect(panel, (30, 18, 8, int(215 * a)), (0, 0, 560, 300), border_radius=28)
            pygame.draw.rect(panel, (255, 214, 110, int(255 * a)), (0, 0, 560, 300), 4, border_radius=28)
            cv.blit(panel, (W // 2 - 280, 120))
            self.text("LUNCH TIME!", 66, W // 2, 178, (255, 150, 70), alpha=int(255 * a))
            self.text(f"Score  {self.score_f}", 44, W // 2, 246, (255, 244, 220), alpha=int(255 * a))
            self.text(f"Fruit  {self.fruits_got}      Best  {self.best}", 30, W // 2, 296, (255, 224, 150), alpha=int(255 * a))
            if self.new_best:
                self.text("NEW BEST!", 30, W // 2, 336, (140, 255, 160), alpha=int(255 * a))
            if self.tb > 3.2 and int(self.gt * 2) % 2 == 0:
                self.text("Press SPACE to run again", 30, W // 2, 384, (255, 255, 255))

    def draw(self):
        self.draw_scene(self.canvas)
        self.draw_hud()
        ox = oy = 0
        if self.shake > 0.3:
            ox = int(random.uniform(-1, 1) * self.shake)
            oy = int(random.uniform(-1, 1) * self.shake)
        self.screen.fill((0, 0, 0))
        self.screen.blit(self.canvas, (ox, oy))


def main():
    pygame.mixer.pre_init(22050, -16, 1, 512)
    pygame.init()
    pygame.display.set_caption("Safari Run")
    screen = pygame.display.set_mode((W, H))
    game = Game(screen)
    clock = pygame.time.Clock()
    while True:
        dt = min(clock.tick(FPS) / 1000.0, 1 / 30.0)
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            if e.type == pygame.KEYDOWN:
                game.key(e.key)
        game.update(dt)
        game.draw()
        pygame.display.flip()


if __name__ == "__main__":
    main()