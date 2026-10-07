"""Cross-check reverse search against forward rules and exhaustive search."""

import json
from pathlib import Path
import random
import unittest

from scripts.reverse_search import reverse_search
from scripts.solve_map import Card, replay, solve
from test_solve_map import exhaustive


def make_cards(rows):
    return [Card(str(i), kind, x, y, layer, i, None)
            for i, (kind, x, y, layer) in enumerate(rows)]


class ReverseSearchTests(unittest.TestCase):
    def test_matches_exhaustive_search_with_four_and_five_types(self):
        rng = random.Random(47)
        outcomes = set()
        for count in (12, 15):
            for _ in range(20):
                kinds = list(range(1, count // 3 + 1)) * 3
                rng.shuffle(kinds)
                positions = rng.sample([(x, y, layer) for x in (0, 4, 8)
                                        for y in (0, 4, 8) for layer in (1, 2, 3)], count)
                cards = make_cards([(kind, *pos) for kind, pos in zip(kinds, positions)])
                expected = exhaustive(cards)
                outcomes.add(expected)
                result = reverse_search(cards, 5)
                self.assertEqual(result['status'], 'solved' if expected else 'unsatisfiable')
                if expected:
                    self.assertTrue(replay(cards, result['order'])['verified'])
        self.assertEqual(outcomes, {True, False})

    def test_seventh_card_can_complete_triple_in_forward_order(self):
        kinds = [1, 2, 3, 4, 5, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
        cards = make_cards([(kind, 0, 0, 15 - i) for i, kind in enumerate(kinds)])
        result = reverse_search(cards, 5)
        self.assertEqual(result['status'], 'solved')
        self.assertEqual(result['order'], list(range(15)))
        self.assertEqual(replay(cards, result['order'])['peakBeforeElimination'], 7)

    def test_seventh_unmatched_card_cannot_be_reversed(self):
        cards = make_cards([(i % 4 + 1, 0, 0, 12 - i) for i in range(12)])
        self.assertEqual(reverse_search(cards, 5)['status'], 'unsatisfiable')

    def test_pruned_frontier_is_not_proof_of_unsatisfiability(self):
        kinds = [3, 6, 1, 5, 6, 4, 6, 2, 5, 2, 1, 1, 5, 4, 3, 3, 2, 4]
        cards = make_cards([(kind, (i % 3) * 8, 0, i // 3)
                            for i, kind in enumerate(kinds)])
        result = reverse_search(cards, 5, width=1)
        self.assertEqual(result['status'], 'search_exhausted')
        result = reverse_search(cards, 5)
        self.assertEqual(result['status'], 'solved')
        self.assertTrue(replay(cards, result['order'])['verified'])

    def test_timeout_is_not_proof_of_unsatisfiability(self):
        cards = make_cards([(1, i * 8, 0, 1) for i in range(3)])
        self.assertEqual(reverse_search(cards, 0)['status'], 'timeout')

    def test_world_regression_has_complete_forward_solution(self):
        rows = json.loads((Path(__file__).parent / 'fixtures/world_255.json').read_text())
        cards = make_cards(rows)
        result = solve(cards, 60)
        self.assertEqual(result['status'], 'solved')
        verified = replay(cards, result['order'])
        self.assertEqual(len(verified['steps']), 255)
        self.assertEqual(verified['triples'], 85)
        self.assertLessEqual(verified['peakAfterElimination'], 6)


if __name__ == '__main__':
    unittest.main()
