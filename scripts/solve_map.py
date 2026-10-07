#!/usr/bin/env python3
"""Find and independently replay a no-item solution for a typed map."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
import time


CELL_SIZE = 8
CAPACITY = 7


@dataclass(frozen=True)
class Card:
    id: str
    type: int
    x: int
    y: int
    layer: int
    card_id: int
    name: str | None


def load_cards(data: dict) -> list[Card]:
    cards = []
    seen = set()
    positions = set()
    for layer, nodes in data["levelData"].items():
        for node in nodes:
            required = ("type", "rolNum", "rowNum", "layerNum")
            if any(type(node.get(k)) is not int for k in required):
                raise ValueError("Card type and coordinates must be integers")
            if node["type"] <= 0 or node["layerNum"] != int(layer):
                raise ValueError("Unknown card type or inconsistent layer")
            if node.get("AreaType", 0) != 0 or node.get("metaType", 0) != 0:
                raise ValueError("Only initial boards with fixed, ordinary card types are supported")
            card = Card(node["id"], node["type"], node["rolNum"], node["rowNum"],
                        node["layerNum"], node.get("cardId", len(cards)), node.get("typeName"))
            pos = (card.layer, card.x, card.y)
            if card.id in seen or pos in positions:
                raise ValueError("Duplicate card ID or board position")
            seen.add(card.id)
            positions.add(pos)
            cards.append(card)
    if not cards or len(cards) > 500:
        raise ValueError("Expected between 1 and 500 cards")
    if any(count % 3 for count in Counter(c.type for c in cards).values()):
        raise ValueError("Each type count must be divisible by three for an empty initial tray")
    return cards


def blocker_masks(cards: list[Card]) -> list[int]:
    return [sum(1 << j for j, upper in enumerate(cards)
                if upper.layer > card.layer and abs(upper.x - card.x) < CELL_SIZE
                and abs(upper.y - card.y) < CELL_SIZE) for card in cards]


class SearchLimit(Exception):
    pass


class Solver:
    def __init__(self, cards: list[Card], seconds: float, seed: int = 0,
                 restart_nodes: int = 20000):
        self.cards = cards
        self.blockers = blocker_masks(cards)
        self.children = [[j for j, mask in enumerate(self.blockers) if mask & (1 << i)]
                         for i in range(len(cards))]
        self.child_masks = [sum(1 << j for j in children) for children in self.children]
        types = sorted({c.type for c in cards})
        self.types = [types.index(c.type) for c in cards]
        self.tray = [0] * len(types)
        self.dead = set()
        self.path = []
        self.rng = random.Random(seed)
        self.deadline = time.monotonic() + seconds
        self.restart_nodes = restart_nodes
        self.visited = 0
        self.attempt_nodes = 0
        self.attempts = 0
        self.best_depth = 0

    def solve(self) -> tuple[str, list[int]]:
        remaining = (1 << len(self.cards)) - 1
        available = sum(1 << i for i, mask in enumerate(self.blockers) if not mask)
        while time.monotonic() < self.deadline:
            self.attempts += 1
            self.attempt_nodes = 0
            self.path.clear()
            self.tray[:] = [0] * len(self.tray)
            try:
                if self.visit(remaining, available, 0):
                    return "solved", self.path[:]
                return "unsatisfiable", []
            except SearchLimit:
                continue
        return "timeout", []

    def candidates(self, remaining: int, available: int, occupied: int):
        indices = []
        counts = [0] * len(self.tray)
        while available:
            bit = available & -available
            i = bit.bit_length() - 1
            available ^= bit
            indices.append(i)
            counts[self.types[i]] += 1
        ranked = []
        equivalent = set()
        for i in indices:
            t = self.types[i]
            signature = (t, self.child_masks[i])
            if signature in equivalent:
                continue
            equivalent.add(signature)
            held = self.tray[t]
            if occupied == CAPACITY - 1 and held != 2:
                continue
            after = remaining ^ (1 << i)
            unlocked = sum(1 << j for j in self.children[i]
                           if after & (1 << j) and not self.blockers[j] & after)
            score = 1000 * (held == 2) + 100 * (counts[t] + held >= 3) + 15 * held
            score += 4 * bin(unlocked).count("1") + self.cards[i].layer * 0.25
            score += self.rng.random() * (2 if self.attempts == 1 else 30)
            ranked.append((score, i, unlocked))
        ranked.sort(reverse=True)
        return ranked

    def visit(self, remaining: int, available: int, occupied: int) -> bool:
        self.best_depth = max(self.best_depth, len(self.path))
        if not remaining:
            return occupied == 0
        if remaining in self.dead:
            return False
        if self.attempt_nodes >= self.restart_nodes or time.monotonic() >= self.deadline:
            raise SearchLimit
        self.visited += 1
        self.attempt_nodes += 1
        for _, i, unlocked in self.candidates(remaining, available, occupied):
            t = self.types[i]
            before = self.tray[t]
            self.tray[t] = (before + 1) % 3
            self.path.append(i)
            size = occupied - 2 if before == 2 else occupied + 1
            if self.visit(remaining ^ (1 << i), (available ^ (1 << i)) | unlocked, size):
                return True
            self.path.pop()
            self.tray[t] = before
        # With an empty initial tray, remaining cards determine tray counts modulo 3.
        if len(self.dead) < 500000:
            self.dead.add(remaining)
        return False


def replay(cards: list[Card], order: list[int]) -> dict:
    """Validate from coordinates and a literal tray; do not reuse the search graph."""
    remaining = set(range(len(cards)))
    tray = []
    steps = []
    peak = stable_peak = triples = 0
    for step, i in enumerate(order, 1):
        if i not in remaining:
            raise ValueError(f"Step {step}: missing or repeated card")
        card = cards[i]
        for j in remaining:
            upper = cards[j]
            if (upper.layer > card.layer and card.x - 8 < upper.x < card.x + 8
                    and card.y - 8 < upper.y < card.y + 8):
                raise ValueError(f"Step {step}: {card.id} is blocked by {upper.id}")
        if len(tray) >= CAPACITY:
            raise ValueError(f"Step {step}: tray already full")
        before = tray[:]
        matching = [j for j, kind in enumerate(tray) if kind == card.type]
        tray.insert(matching[-1] + 1 if matching else len(tray), card.type)
        peak = max(peak, len(tray))
        eliminated = tray.count(card.type) == 3
        if eliminated:
            tray = [t for t in tray if t != card.type]
            triples += 1
        if len(tray) >= CAPACITY:
            raise ValueError(f"Step {step}: seven cards remain without elimination")
        remaining.remove(i)
        stable_peak = max(stable_peak, len(tray))
        steps.append({"step": step, "id": card.id, "cardId": card.card_id,
                      "type": card.type, "name": card.name, "layer": card.layer,
                      "x": card.x, "y": card.y, "trayBefore": before,
                      "trayAfter": tray[:], "eliminated": eliminated})
    if remaining or tray:
        raise ValueError("Incomplete solution: board or tray is not empty")
    return {"steps": steps, "verified": True, "triples": triples,
            "peakBeforeElimination": peak, "peakAfterElimination": stable_peak}


def solve(cards: list[Card], seconds: float, seed: int = 0) -> dict:
    """Try cheap forward DFS first, then reverse search within the same budget."""
    if __package__:
        from .reverse_search import reverse_search
    else:
        from reverse_search import reverse_search
    started = time.monotonic()
    forward = Solver(cards, min(2.0, seconds / 4), seed)
    status, order = forward.solve()
    result = {'status': status, 'order': order, 'backend': 'forward_dfs',
              'visited': forward.visited, 'attempts': forward.attempts,
              'bestDepth': forward.best_depth}
    if status == 'timeout' and time.monotonic() - started < seconds:
        result = reverse_search(cards, seconds - (time.monotonic() - started))
        result['forwardVisited'] = forward.visited
        result['attempts'] = forward.attempts + 1
    if result['status'] == 'solved':
        result.update(replay(cards, result['order']))
    result['elapsedSeconds'] = round(time.monotonic() - started, 3)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("map", type=Path)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    raw = args.map.read_bytes()
    data = json.loads(raw)
    cards = load_cards(data)
    search = solve(cards, args.seconds, args.seed)
    status, order = search['status'], search['order']
    result = {"status": status, "mapPath": str(args.map.resolve()),
              "mapSha256": hashlib.sha256(raw).hexdigest(), "levelKey": data.get("levelKey"),
              "source": data.get("_source"), "capacity": CAPACITY, "cellSize": CELL_SIZE,
              "usesItems": False, **search, "operations": []}
    if status == "solved":
        result["operations"] = [cards[i].id for i in order]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items()
                      if k not in ("steps", "operations", "source", "order")}, ensure_ascii=False))
    raise SystemExit(0 if status == "solved" else 2)


if __name__ == "__main__":
    main()
