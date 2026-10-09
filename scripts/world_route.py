"""Plan one visible, known world-map hop at a time."""

from collections import Counter
import math

import networkx as nx

from seed_map import XorShift128Plus, u32


LOOKAHEAD_MARGINS = (2, 4, 8, 12)
LOOKAHEAD_CELL_BUDGET = 3000


def point(node):
    return node['x'], node['y']


def distance(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def direction(a, b):
    x, y = b[0] - a[0], b[1] - a[1]
    return ('上' if y > 0 else '下' if y < 0 else '') + ('右' if x > 0 else '左' if x < 0 else '') or '已到达'


def neighbors(p):
    return [(p[0] + x, p[1] + y) for x in (-1, 0, 1) for y in (-1, 0, 1) if x or y]


def ordinary(node):
    return (node['province'] > 0 and node['discovery'] == 0
            and node.get('giftStatusKnown') is True and node.get('navigationBlock') is None)


class WorldLayout:
    """Version-500 layout; positions are in window-relative macOS points."""

    def __init__(self, bounds, max_y):
        self.width, self.height = bounds['Width'], bounds['Height']
        if abs(self.width / 350 - self.height / 665) > 0.02:
            raise ValueError('导航仅支持等比例的 350x665 游戏窗口')
        self.ui_scale = self.width / 350
        self.scale = self.width / 750
        self.top = 44 * self.ui_scale
        self.center = (self.width / 2, (self.top + self.height) / 2)
        virtual_height = (self.height - self.top) / self.scale
        self.grid_height = 400 * min((virtual_height - 400) / 750, 1.3)
        self.max_y = max_y

    def position(self, p):
        x, y = p[0], self.max_y - p[1]
        rng = XorShift128Plus([u32(1000*x + 589667220), u32(2000*y + 4047304004),
                              u32(4000*y + 3333988324), u32(3000*x + 62666966)])
        for _ in range(y % 10):
            rng.random()
        dx = (-100, -50, 0, 50, 100)[int(rng.random() * 5)]
        available = self.grid_height - 200
        dy = -available / 2 + math.floor(rng.random() * available)
        return 400*x + dx, -self.grid_height*y + dy

    def click_point(self, current, target):
        a, b = self.position(current), self.position(target)
        x = self.center[0] + (b[0] - a[0])*self.scale
        y = self.center[1] - (b[1] - a[1])*self.scale
        # Exclude title, navigation controls and the viewport edges.
        left, right, top, bottom = (v*self.ui_scale for v in (18, 332, 85, 585))
        px, py = min(right, max(left, x)), min(bottom, max(top, y))
        if math.hypot(px-x, py-y) > 24*self.ui_scale:
            return None
        if px > 270*self.ui_scale and py > 510*self.ui_scale:
            return None
        return px, py

    def settle_seconds(self, current, target):
        a, b = self.position(current), self.position(target)
        return max(0.6, 1.5 * math.dist(a, b) / 900 + 0.15)


class Navigator:
    def __init__(self, target, layout, max_x, max_y):
        self.target, self.layout = target, layout
        self.max_x, self.max_y = max_x, max_y
        if not self.in_bounds(target):
            raise ValueError('目标坐标超出服务器返回的地图范围')
        self.nodes, self.visits = {}, Counter()
        self.plan = {}

    def in_bounds(self, p):
        return 0 <= p[0] < self.max_x and 0 < p[1] <= self.max_y

    def update(self, state, count_visit=True):
        self.nodes.update((point(n), n) for n in state['nodes'])
        if count_visit:
            self.visits[point(state['current'])] += 1

    def _graph(self, positions):
        graph = nx.DiGraph()
        graph.add_nodes_from(positions)
        for a in graph:
            adjacent = sorted(neighbors(a), key=lambda p: (distance(p, self.target),
                              abs(p[0]-self.target[0]) + abs(p[1]-self.target[1]), p))
            for b in adjacent:
                if b in graph and self.layout.click_point(a, b) is not None:
                    graph.add_edge(a, b)
        return graph

    def _lookahead(self, current):
        # Unknown cells describe possible layout only, never permission to click.
        span = max(1, distance(current, self.target))
        scale = min(1, 12 / span)
        waypoint = tuple(a + round((b-a)*scale) for a, b in zip(current, self.target))
        self.plan = {'searchMargins': [], 'searchCells': 0, 'fallbackReason': 'search_limit'}
        if waypoint in self.nodes and not ordinary(self.nodes[waypoint]):
            self.plan['fallbackReason'] = 'waypoint_blocked'
            return None
        previous_bounds = None
        for margin in LOOKAHEAD_MARGINS:
            left = max(0, min(current[0], waypoint[0])-margin)
            right = min(self.max_x-1, max(current[0], waypoint[0])+margin)
            bottom = max(1, min(current[1], waypoint[1])-margin)
            top = min(self.max_y, max(current[1], waypoint[1])+margin)
            bounds = left, right, bottom, top
            if bounds == previous_bounds:
                continue
            cells = (right-left+1) * (top-bottom+1)
            if self.plan['searchCells'] + cells > LOOKAHEAD_CELL_BUDGET:
                break
            previous_bounds = bounds
            self.plan['searchMargins'].append(margin)
            self.plan['searchCells'] += cells
            graph = self._graph((x, y) for x in range(left, right+1) for y in range(bottom, top+1)
                                if (x, y) not in self.nodes or ordinary(self.nodes[x, y]))
            try:
                path = nx.astar_path(graph, current, waypoint, heuristic=distance,
                                     weight=lambda a, b, edge: 1 + 2*self.visits[b])
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            if len(path) < 2 or path[1] not in self.nodes or not ordinary(self.nodes[path[1]]):
                self.plan['fallbackReason'] = 'unverified_next'
                return None
            self.plan.pop('fallbackReason')
            self.plan.update(method='layout_lookahead', waypoint=list(waypoint), margin=margin,
                             unknownAhead=sum(p not in self.nodes for p in path))
            return path[1]
        return None

    def next_step(self, current):
        self.plan = {}
        if current == self.target:
            return None
        if self.target in self.nodes and not ordinary(self.nodes[self.target]):
            raise ValueError('目标地块被锁定、含事件/奖励/挑战或状态未知，导航不会自动进入')
        graph = self._graph(p for p, n in self.nodes.items() if ordinary(n))
        if current not in graph:
            raise ValueError('当前位置不支持普通地块导航')
        paths = nx.single_source_shortest_path(graph, current)
        if self.target in paths:
            self.plan = {'method': 'known_route', 'remainingSteps': len(paths[self.target])-1}
            return paths[self.target][1]
        target = self._lookahead(current)
        if target is not None:
            return target
        frontiers = [p for p in paths if p != current and any(
            self.in_bounds(n) and n not in self.nodes for n in neighbors(p))]
        if not frontiers:
            raise ValueError('已知区域没有可达目标或可探索边界；停止，不猜测未知地块')
        frontier = min(frontiers, key=lambda p: (len(paths[p])-1 + distance(p, self.target)
                                                + 2*self.visits[p], distance(p, self.target),
                                                abs(p[0]-self.target[0]) + abs(p[1]-self.target[1]), p))
        self.plan.update(method='frontier', waypoint=list(frontier))
        return paths[frontier][1]
