"""Rule boundary and exhaustive small-board checks for solve_map."""

from collections import deque
import random
import unittest

from solve_map import Card, Solver, blocker_masks, load_cards, replay


def card(i, kind, x=0, y=0, layer=1):
    return Card(str(i), kind, x, y, layer, i, None)


def exhaustive(cards):
    queue = deque([(tuple(range(len(cards))), ())])
    seen = set(queue)
    while queue:
        remaining, tray = queue.popleft()
        if not remaining:
            return not tray
        for i in remaining:
            c = cards[i]
            if any(cards[j].layer > c.layer and abs(cards[j].x-c.x) < 8
                   and abs(cards[j].y-c.y) < 8 for j in remaining):
                continue
            next_tray = list(tray) + [c.type]
            if next_tray.count(c.type) == 3:
                next_tray = [t for t in next_tray if t != c.type]
            if len(next_tray) >= 7:
                continue
            state = (tuple(j for j in remaining if j != i), tuple(sorted(next_tray)))
            if state not in seen:
                seen.add(state)
                queue.append(state)
    return False


class SolverTests(unittest.TestCase):
    def test_overlap_strict_edges_all_higher_layers(self):
        cs = [card(0, 1), card(1, 1, x=8, layer=2),
              card(2, 1, y=8, layer=2), card(3, 1, x=7, y=7, layer=5)]
        self.assertEqual(blocker_masks(cs)[0], 1 << 3)

    def test_seventh_card_can_complete_triple(self):
        types = [1, 2, 3, 4, 5, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
        cs = [card(i, t, x=i * 8) for i, t in enumerate(types)]
        result = replay(cs, list(range(len(cs))))
        self.assertEqual(result['peakBeforeElimination'], 7)
        self.assertEqual(result['peakAfterElimination'], 6)
        self.assertEqual(result['triples'], 5)

    def test_seventh_unmatched_card_loses(self):
        cs = [card(i, i + 1, x=i * 8) for i in range(7)]
        with self.assertRaisesRegex(ValueError, 'seven cards'):
            replay(cs, list(range(7)))

    def test_tray_groups_matching_cards(self):
        cs = [card(i, t, x=i * 8) for i, t in enumerate([1, 2, 1, 1, 2, 2])]
        result = replay(cs, list(range(6)))
        self.assertEqual(result['steps'][2]['trayAfter'], [1, 1, 2])
        self.assertEqual(result['steps'][3]['trayAfter'], [2])

    def test_replay_rejects_blocked_duplicate_and_incomplete(self):
        cs = [card(i, 1, layer=i + 1) for i in range(3)]
        for order in ([0, 1, 2], [2, 2, 0], [2]):
            with self.assertRaises(ValueError):
                replay(cs, order)

    def test_forced_dead_end(self):
        cs = [card(i, i % 7 + 1, layer=21-i) for i in range(21)]
        self.assertEqual(Solver(cs, 2).solve()[0], 'unsatisfiable')

    def test_timeout_is_not_unsatisfiable(self):
        cs = [card(i, 1, x=i * 8) for i in range(3)]
        self.assertEqual(Solver(cs, 0).solve()[0], 'timeout')

    def test_rejects_unknown_types_and_dynamic_cards(self):
        node = dict(id='a', type=0, rolNum=0, rowNum=0, layerNum=1)
        with self.assertRaises(ValueError):
            load_cards({'levelData': {'1': [node]}})
        node.update(type=1, metaType=2)
        with self.assertRaises(ValueError):
            load_cards({'levelData': {'1': [node]}})

    def test_matches_exhaustive_search_on_small_boards(self):
        rng = random.Random(42)
        for attempt in range(20):
            types = [1, 2, 3] * 3
            rng.shuffle(types)
            positions = rng.sample([(x, y, l) for x in (0, 4, 8)
                                    for y in (0, 4, 8) for l in (1, 2, 3)], 9)
            cs = [card(i, t, *pos) for i, (t, pos) in enumerate(zip(types, positions))]
            expected = exhaustive(cs)
            status, order = Solver(cs, 5, restart_nodes=100000).solve()
            self.assertEqual(status, 'solved' if expected else 'unsatisfiable', attempt)
            if expected:
                self.assertTrue(replay(cs, order)['verified'])


if __name__ == '__main__':
    unittest.main()
