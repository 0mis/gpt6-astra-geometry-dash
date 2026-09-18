"""Local collision estimates using native rectangle and circle observations.

These helpers never write game state. The original engine remains authoritative.
"""
import math


def shape(obj):
    radius = obj.get('radius', 0)
    if radius > 0:
        x, y = obj['x'], obj['y']
        return (x-radius, y-radius, 2*radius, 2*radius, x, y, radius)
    return (*obj['rect'], 0.0, 0.0, 0.0)


def clearance(x, y, extent, obstacle):
    rx, ry, rw, rh, cx, cy, radius = obstacle
    if x+extent <= rx or x-extent >= rx+rw:
        return math.inf
    if radius > 0:
        dx, dy = max(abs(x-cx)-extent, 0), max(abs(y-cy)-extent, 0)
        return math.hypot(dx, dy)-radius
    return max(y-extent-(ry+rh), ry-(y+extent))


def vertical_interval(x, extent, obstacle):
    rx, ry, rw, rh, cx, cy, radius = obstacle
    if x+extent <= rx or x-extent >= rx+rw:
        return None
    if radius > 0:
        dx = max(abs(x-cx)-extent, 0)
        reach = math.sqrt(max(0, radius*radius-dx*dx))+extent
        return cy-reach, cy+reach
    return ry-extent, ry+rh+extent
