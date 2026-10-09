from copy import deepcopy
import json
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from world_navigation import WorldReader, confirmed_move, execute, plan_description, verify_unchanged
from world_route import Navigator, WorldLayout, direction, distance, neighbors, ordinary, point
from world_snapshot import MovementPending


class VisibleLayout:
    def click_point(self, current, target):
        return (100, 100)


def state(current=(5, 5), blocked=(), events=()):
    nodes = [{'x': x, 'y': y, 'protocolY': 100-y,
              'province': 0 if (x, y) in blocked else 9,
              'discovery': 1 if (x, y) in events else 0,
              'giftStatusKnown': True, 'navigationBlock': None,
              'navigationCandidate': (x, y) != current and (x, y) not in blocked + events,
              'terrainMoveCandidate': (x, y) != current and (x, y) not in blocked + events}
             for x, y in [current] + neighbors(current)]
    return {'source': {'recordId': 1, 'recordUid': 'test'}, 'nodes': nodes,
            'current': {'x': current[0], 'y': current[1], 'protocolY': 100-current[1]},
            'movements': []}


def moved(before, target):
    after = state(target)
    after['movements'] = before['movements'] + [{'opcode': 1100, 'code': 0,
        'from': before['current'], 'target': after['current']}]
    return after


class RouteTests(unittest.TestCase):
    def test_plan_log_explains_expansion_and_fallback(self):
        self.assertIn('扩展 4 格', plan_description({'method': 'layout_lookahead', 'margin': 4}))
        for reason in ('search_limit', 'waypoint_blocked', 'unverified_next'):
            label = plan_description({'method': 'frontier', 'fallbackReason': reason})
            self.assertTrue(label.startswith('边界探索，'))
            self.assertNotIn('最短路', label)
        self.assertEqual(plan_description({'method': 'known_route'}), '已知区域最短路')

    def test_layout_only_simulation_reaches_goal_without_frontier_fallback(self):
        # Only the coordinate layout is real; all terrain in this test is synthetic and ordinary.
        layout = WorldLayout({'Width': 350, 'Height': 665}, 15600)
        nav = Navigator((12549, 6520), layout, 13600, 15600)
        current, route = (12559, 6530), []
        while current != nav.target and len(route) < 40:
            nav.update(state(current))
            target = nav.next_step(current)
            self.assertNotEqual(nav.plan['method'], 'frontier')
            self.assertIn(target, neighbors(current))
            self.assertIsNotNone(layout.click_point(current, target))
            current = target
            route.append(current)
        self.assertEqual(current, nav.target)
        self.assertEqual(len(route), 34)

    def test_captured_detour_expands_before_falling_back_to_exploration(self):
        sample = json.loads((Path(__file__).parent / 'fixtures' / 'world_navigation_detour_3129.json').read_text())
        layout = WorldLayout(sample['bounds'], sample['mapMaxY'])
        nav = Navigator(tuple(sample['target']), layout, sample['mapMaxX'], sample['mapMaxY'])
        start = tuple(sample['start'])
        nav.update({'current': dict(zip(('x', 'y'), start)), 'nodes': sample['nodes']})
        before = deepcopy(nav.nodes)
        target = nav.next_step(start)
        self.assertEqual(target, (12560, 6531))
        self.assertEqual(nav.plan['method'], 'layout_lookahead')
        self.assertEqual(nav.plan['searchMargins'], [2, 4])
        self.assertEqual(nav.plan['margin'], 4)
        self.assertTrue(ordinary(nav.nodes[target]))
        self.assertIsNotNone(layout.click_point(start, target))
        self.assertEqual(nav.nodes, before)

    def test_larger_layout_detours_use_later_expansion_levels(self):
        for gate, expected_margin in ((17, 8), (21, 12)):
            with self.subTest(gate=gate):
                class WallLayout(VisibleLayout):
                    def click_point(self, current, target):
                        crosses = (current[0] < 10) != (target[0] < 10)
                        if crosses and max(current[1], target[1]) < gate:
                            return None
                        return super().click_point(current, target)

                layout = WallLayout()
                nav = Navigator((12, 10), layout, 100, 100)
                nav.update(state((8, 10)))
                target = nav.next_step((8, 10))
                self.assertEqual(nav.plan['method'], 'layout_lookahead')
                self.assertEqual(nav.plan['margin'], expected_margin)
                self.assertTrue(ordinary(nav.nodes[target]))
                self.assertIsNotNone(layout.click_point((8, 10), target))

    def test_exhausted_expansion_budget_falls_back_without_unbounded_work(self):
        class VerticalLayout(VisibleLayout):
            calls = 0

            def click_point(self, current, target):
                self.calls += 1
                return (100, 100) if current[0] == target[0] else None

        layout = VerticalLayout()
        nav = Navigator((90, 90), layout, 100, 100)
        nav.update(state((20, 20)))
        target = nav.next_step((20, 20))
        self.assertEqual(nav.plan['method'], 'frontier')
        self.assertEqual(nav.plan['fallbackReason'], 'search_limit')
        self.assertEqual(nav.plan['searchMargins'], [2, 4, 8, 12])
        self.assertLessEqual(nav.plan['searchCells'], 3000)
        self.assertLess(layout.calls, 24100)
        self.assertEqual(target[0], 20)
        self.assertTrue(ordinary(nav.nodes[target]))

    def test_clipped_map_bounds_do_not_repeat_identical_searches(self):
        layout = VisibleLayout()
        layout.click_point = lambda a, b: (100, 100) if a[0] == b[0] else None
        nav = Navigator((4, 4), layout, 6, 6)
        nav.update(state((1, 2)))
        target = nav.next_step((1, 2))
        self.assertTrue(nav.in_bounds(target))
        self.assertEqual(nav.plan['method'], 'frontier')
        self.assertEqual(nav.plan['searchMargins'], [2])
        self.assertEqual(nav.plan['searchCells'], 36)

    def test_expansion_does_not_override_reward_or_unknown_status(self):
        sample = json.loads((Path(__file__).parent / 'fixtures' / 'world_navigation_detour_3129.json').read_text())
        for reason in ('gift_ownerless', 'gift_occupied', 'gift_unknown'):
            with self.subTest(reason=reason):
                layout = WorldLayout(sample['bounds'], sample['mapMaxY'])
                nav = Navigator(tuple(sample['target']), layout, sample['mapMaxX'], sample['mapMaxY'])
                nodes = deepcopy(sample['nodes'])
                blocked = next(n for n in nodes if point(n) == (12560, 6531))
                blocked['navigationBlock'] = reason
                blocked['giftStatusKnown'] = reason != 'gift_unknown'
                nav.update({'current': dict(zip(('x', 'y'), sample['start'])), 'nodes': nodes})
                target = nav.next_step(tuple(sample['start']))
                self.assertNotEqual(target, (12560, 6531))
                self.assertTrue(ordinary(nav.nodes[target]))

    def test_captured_route_avoids_exploration_round_trips(self):
        sample = json.loads((Path(__file__).parent / 'fixtures' / 'world_navigation_3129.json').read_text())
        world = {tuple(row[:2]): dict(zip(sample['nodeFields'], row)) for row in sample['nodes']}
        layout = WorldLayout(sample['bounds'], sample['mapMaxY'])
        nav = Navigator(tuple(sample['target']), layout, sample['mapMaxX'], sample['mapMaxY'])
        current, route = tuple(sample['start']), []
        while current != nav.target and len(route) < 46:
            surrounding = [current] + neighbors(current)
            self.assertTrue(all(p in world for p in surrounding), 'No captured neighborhood at this position')
            data = {'current': {'x': current[0], 'y': current[1]},
                    'nodes': [world[p] for p in surrounding]}
            nav.update(data)
            target = nav.next_step(current)
            self.assertIn(target, neighbors(current))
            self.assertTrue(ordinary(world[target]))
            self.assertIsNotNone(layout.click_point(current, target))
            current = target
            route.append(current)
        self.assertEqual(current, nav.target)
        self.assertEqual(len(route), 28)

    def test_lookahead_does_not_promote_unseen_cells_to_known_nodes(self):
        nav = Navigator((15, 5), VisibleLayout(), 100, 100)
        data = state()
        nav.update(data)
        before = deepcopy(nav.nodes)
        self.assertEqual(nav.next_step((5, 5)), (6, 5))
        self.assertEqual(nav.nodes, before)
        self.assertNotIn((7, 5), nav.nodes)

    def test_new_reward_on_predicted_route_is_avoided(self):
        nav = Navigator((15, 5), VisibleLayout(), 100, 100)
        nav.update(state())
        self.assertEqual(nav.next_step((5, 5)), (6, 5))
        data = state((6, 5))
        reward = next(n for n in data['nodes'] if point(n) == (7, 5))
        reward.update(navigationBlock='gift_ownerless', navigationCandidate=False)
        nav.update(data)
        target = nav.next_step((6, 5))
        self.assertNotEqual(target, (7, 5))
        self.assertIn(target, [point(n) for n in data['nodes'] if n['navigationCandidate']])

    def test_unknown_status_and_missing_neighbors_are_not_clickable(self):
        nav = Navigator((15, 5), VisibleLayout(), 100, 100)
        data = state()
        for node in data['nodes']:
            if point(node) != (5, 5):
                node['giftStatusKnown'] = False
        nav.update(data)
        with self.assertRaises(ValueError):
            nav.next_step((5, 5))
        nav = Navigator((15, 5), VisibleLayout(), 100, 100)
        data['nodes'] = data['nodes'][:1]
        nav.update(data)
        with self.assertRaises(ValueError):
            nav.next_step((5, 5))

    def test_distant_target_uses_bounded_layout_work(self):
        class CountingLayout(VisibleLayout):
            calls = 0

            def click_point(self, current, target):
                self.calls += 1
                return super().click_point(current, target)

        layout = CountingLayout()
        nav = Navigator((13599, 15600), layout, 13600, 15600)
        nav.update(state())
        self.assertEqual(nav.next_step((5, 5)), (6, 6))
        self.assertLess(layout.calls, 3000)

    def test_segmented_open_routes_reach_targets_in_all_directions(self):
        for start, goal in [((20, 20), (50, 35)), ((50, 35), (20, 20)),
                            ((20, 50), (50, 20)), ((50, 20), (20, 50)),
                            ((0, 1), (30, 1)), ((30, 1), (0, 1)),
                            ((0, 1), (0, 35)), ((99, 100), (80, 80))]:
            with self.subTest(start=start, goal=goal):
                nav = Navigator(goal, VisibleLayout(), 100, 100)
                current = start
                for _ in range(distance(start, goal)):
                    data = state(current)
                    data['nodes'] = [n for n in data['nodes'] if nav.in_bounds(point(n))]
                    nav.update(data)
                    target = nav.next_step(current)
                    self.assertIn(target, neighbors(current))
                    self.assertTrue(nav.in_bounds(target))
                    current = target
                self.assertEqual(current, goal)

    def test_detour_can_leave_the_lookahead_area(self):
        nav = Navigator((9, 5), VisibleLayout(), 100, 100)
        blocked = tuple((6, y) for y in range(1, 10))
        current, route = (5, 5), []
        for _ in range(40):
            if current == nav.target:
                break
            nav.update(state(current, blocked=blocked))
            target = nav.next_step(current)
            self.assertNotIn(target, blocked)
            self.assertIn(target, neighbors(current))
            current = target
            route.append(current)
        self.assertEqual(current, nav.target)
        self.assertTrue(any(y >= 10 for x, y in route))

    def test_occupied_tile_is_avoided_and_updates_restore_it(self):
        nav = Navigator((9, 5), VisibleLayout(), 100, 100)
        data = state()
        occupied = next(n for n in data['nodes'] if (n['x'], n['y']) == (6, 5))
        occupied.update(navigationBlock='gift_occupied', navigationCandidate=False)
        nav.update(data)
        self.assertNotEqual(nav.next_step((5, 5)), (6, 5))
        nav.update(state(), count_visit=False)
        self.assertEqual(nav.next_step((5, 5)), (6, 5))

    def test_reward_or_unknown_target_is_never_selected(self):
        for reason in ('gift_ownerless', 'gift_occupied', 'gift_unknown', 'gift_unknown_state'):
            nav = Navigator((6, 5), VisibleLayout(), 100, 100)
            data = state()
            target = next(n for n in data['nodes'] if (n['x'], n['y']) == (6, 5))
            target['navigationBlock'] = reason
            nav.update(data)
            with self.assertRaisesRegex(ValueError, '目标'):
                nav.next_step((5, 5))

    def test_direction_and_lower_bound(self):
        self.assertEqual(distance((5, 5), (11, 9)), 6)
        self.assertEqual(direction((5, 5), (11, 9)), '上右')
        self.assertEqual(direction((5, 5), (4, 4)), '下左')
        self.assertEqual(direction((5, 5), (5, 5)), '已到达')

    def test_remote_target_is_discovered_one_neighborhood_at_a_time(self):
        nav = Navigator((13, 10), VisibleLayout(), 100, 100)
        current, path = (5, 5), []
        self.assertNotIn(nav.target, neighbors(current))
        while current != nav.target and len(path) < 20:
            nav.update(state(current))
            next_target = nav.next_step(current)
            self.assertEqual(distance(current, next_target), 1)
            self.assertIn(next_target, nav.nodes)
            current = next_target
            path.append(current)
        self.assertEqual(current, (13, 10))
        self.assertEqual(len(path), 8)

    def test_detours_around_locked_and_event_tiles(self):
        nav = Navigator((9, 5), VisibleLayout(), 100, 100)
        blocked = tuple((6, y) for y in range(3, 8))
        events = ((7, 8),)
        current, path = (5, 5), []
        while current != nav.target and len(path) < 40:
            nav.update(state(current, blocked, events))
            current = nav.next_step(current)
            self.assertNotIn(current, blocked + events)
            path.append(current)
        self.assertEqual(current, nav.target)
        self.assertGreater(len(path), 4)

    def test_known_route_can_backtrack_out_of_dead_end(self):
        nav = Navigator((2, 5), VisibleLayout(), 100, 100)
        for p in ((5, 5), (6, 5), (7, 5)):
            nav.update(state(p))
        self.assertLess(nav.next_step((7, 5))[0], 7)

    def test_locked_goal_and_unreachable_frontier_stop(self):
        nav = Navigator((6, 5), VisibleLayout(), 100, 100)
        nav.update(state(blocked=((6, 5),)))
        with self.assertRaisesRegex(ValueError, '目标'):
            nav.next_step((5, 5))
        nav = Navigator((9, 9), VisibleLayout(), 100, 100)
        nav.update(state(blocked=tuple(neighbors((5, 5)))))
        with self.assertRaisesRegex(ValueError, '边界'):
            nav.next_step((5, 5))

    def test_offscreen_edges_are_not_used(self):
        layout = VisibleLayout()
        layout.click_point = lambda a, b: None
        nav = Navigator((7, 5), layout, 100, 100)
        nav.update(state())
        with self.assertRaises(ValueError):
            nav.next_step((5, 5))

    def test_bounds_and_already_arrived(self):
        for target in ((-1, 1), (100, 5), (5, 0), (5, 101)):
            with self.assertRaises(ValueError):
                Navigator(target, VisibleLayout(), 100, 100)
        nav = Navigator((5, 5), VisibleLayout(), 100, 100)
        nav.update(state())
        self.assertIsNone(nav.next_step((5, 5)))


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.layout = WorldLayout({'Width': 350, 'Height': 665}, 15600)

    def test_observed_island_centers(self):
        samples = [((12562, 6483), (12562, 6482), (254, 1138)),
                   ((12562, 6482), (12563, 6482), (628, 852)),
                   ((12563, 6482), (12562, 6483), (165, 166))]
        for current, target, screenshot_click in samples:
            result = self.layout.click_point(current, target)
            self.assertIsNotNone(result)
            self.assertLess(abs(result[0]*2 - screenshot_click[0]), 35)
            self.assertLess(abs(result[1]*2 - screenshot_click[1]), 35)

    def test_scaling_animation_delay_and_bad_aspect(self):
        large = WorldLayout({'Width': 700, 'Height': 1330}, 15600)
        a, b = (12562, 6483), (12562, 6482)
        self.assertEqual(large.click_point(a, b), tuple(v*2 for v in self.layout.click_point(a, b)))
        animation = 1.5 * math.dist(self.layout.position(a), self.layout.position(b)) / 900
        self.assertAlmostEqual(self.layout.settle_seconds(a, b), max(0.6, animation + 0.15))
        self.assertGreater(self.layout.settle_seconds(a, b), animation)
        with self.assertRaises(ValueError):
            WorldLayout({'Width': 350, 'Height': 500}, 15600)

    def test_short_hops_keep_client_click_throttle_margin(self):
        with patch.object(self.layout, 'position', return_value=(0, 0)):
            self.assertEqual(self.layout.settle_seconds((5, 5), (6, 5)), 0.6)


class ConfirmationTests(unittest.TestCase):
    def test_neighborhood_wait_overlaps_animation_without_negative_delay(self):
        after = state()
        reader = WorldReader(None, after['source'], lambda: None)
        for elapsed, remaining in ((0.4, 0.6), (1.5, 0)):
            with self.subTest(elapsed=elapsed), patch.object(
                    reader, 'wait_for_neighborhood', return_value=after) as neighborhood:
                with patch('world_navigation.time.monotonic', side_effect=[10, 10 + elapsed]):
                    with patch('world_navigation.controlled_wait') as wait:
                        with patch('world_navigation.read_current', return_value=after):
                            self.assertEqual(reader.settle_after_move(after, 1, 2), after)
                self.assertAlmostEqual(wait.call_args.args[0], remaining)
                self.assertEqual(neighborhood.call_count, 2)

    def test_position_changes_during_animation_stop(self):
        before = state()
        reader = WorldReader(None, before['source'], lambda: None)
        with patch('world_navigation.controlled_wait'):
            with patch('world_navigation.read_current', return_value=moved(before, (6, 5))):
                with self.assertRaisesRegex(ValueError, '变化'):
                    reader.settle_after_move(before, 1, 2)

    def test_delayed_gift_push_is_waited_for(self):
        before = state()
        missing = deepcopy(before)
        missing['nodes'][1]['giftStatusKnown'] = False
        reader = WorldReader(None, before['source'], lambda: None)
        with patch('world_navigation.read_current', return_value=before) as read:
            with patch('world_navigation.time.sleep'):
                self.assertEqual(reader.wait_for_neighborhood(missing, 1), before)
        self.assertEqual(read.call_count, 1)

    def test_missing_gift_status_times_out_and_new_occupancy_stops_preclick(self):
        before = state()
        missing = deepcopy(before)
        missing['nodes'][1]['giftStatusKnown'] = False
        reader = WorldReader(None, before['source'], lambda: None)
        with patch('world_navigation.read_current', return_value=missing):
            with patch('world_navigation.time.monotonic', side_effect=[0, 0, 2]):
                with patch('world_navigation.time.sleep'), self.assertRaisesRegex(TimeoutError, '状态'):
                    reader.wait_for_neighborhood(missing, 1)
        occupied = deepcopy(before)
        occupied['nodes'][1]['navigationBlock'] = 'gift_occupied'
        with self.assertRaises(ValueError):
            verify_unchanged(before, occupied)
        self.assertFalse(confirmed_move(before, occupied, (6, 5)))

    def test_waiting_and_success(self):
        before = state()
        self.assertFalse(confirmed_move(before, before, (6, 5)))
        self.assertTrue(confirmed_move(before, moved(before, (6, 5)), (6, 5)))

    def test_wrong_target_rejected_and_extra_moves_stop(self):
        before = state()
        with self.assertRaisesRegex(ValueError, '目标'):
            confirmed_move(before, moved(before, (5, 6)), (6, 5))
        after = moved(moved(before, (6, 5)), (7, 5))
        with self.assertRaisesRegex(ValueError, '额外'):
            confirmed_move(before, after, (6, 5))
        after = moved(before, (6, 5))
        after['movements'][-1]['code'] = 3
        with self.assertRaisesRegex(ValueError, 'code=3'):
            confirmed_move(before, after, (6, 5))

    def test_session_map_and_external_position_change_stop(self):
        before = state()
        for key, value in [('source', {'recordUid': 'other'}), ('current', {'x': 99}), ('nodes', [])]:
            after = deepcopy(before)
            after[key] = value
            with self.assertRaises(ValueError):
                verify_unchanged(before, after)

    def test_reader_waits_for_ack_without_second_click(self):
        before = state()
        reader = WorldReader(None, before['source'], lambda: None)
        sequence = [MovementPending('pending'), before, moved(before, (6, 5))]
        with patch('world_navigation.read_current', side_effect=sequence) as read:
            with patch('world_navigation.time.sleep'):
                result = reader.wait_for_move(before, (6, 5), 1)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(result['current']['x'], 6)

    def test_reader_timeout_and_disconnect_stop(self):
        before = state()
        reader = WorldReader(None, before['source'], lambda: None)
        with patch('world_navigation.read_current', return_value=before):
            with patch('world_navigation.time.monotonic', side_effect=[0, 0, 2]):
                with patch('world_navigation.time.sleep'):
                    with self.assertRaises(TimeoutError):
                        reader.wait_for_move(before, (6, 5), 1)
        with patch('world_navigation.read_current', side_effect=ValueError('closed')):
            with self.assertRaisesRegex(ValueError, 'closed'):
                reader.wait_for_move(before, (6, 5), 1)


class ExecutionTests(unittest.TestCase):
    def execute_simulated(self, target, limit=10):
        live = state()
        window = {'id': 1, 'pid': 1, 'bounds': {'X': 0, 'Y': 0, 'Width': 350, 'Height': 665},
                  'title': '羊了个羊'}
        layout = VisibleLayout()
        layout.click_point = lambda a, b: b
        layout.settle_seconds = lambda a, b: 2
        nav = Navigator(target, layout, 100, 100)
        nav.update(live)
        clicked, delays = [], []

        def fake_native(action, *args):
            nonlocal live
            if action == 'inspect':
                return {'windows': [window]}
            if action == 'pointer':
                return {'x': 0, 'y': 0}
            if action == 'click':
                dest = tuple(args[1:])
                clicked.append(dest)
                live = moved(live, dest)
            return {}

        progress = {'steps': 0}
        with TemporaryDirectory() as tmp, patch('world_navigation.native', side_effect=fake_native):
            with patch('world_navigation.read_current', side_effect=lambda *args: live):
                with patch('world_navigation.check_stop'), patch('world_navigation.controlled_wait',
                        side_effect=lambda seconds, guard: delays.append(seconds)), patch(
                        'world_navigation.time.monotonic', return_value=10):
                    execute(None, window, live, nav, Path(tmp),
                            SimpleNamespace(max_steps=limit, timeout=1), progress)
        return clicked, delays, progress

    def test_full_loop_refreshes_and_stops_at_remote_target(self):
        clicked, delays, progress = self.execute_simulated((9, 5))
        self.assertEqual(len(clicked), 4)
        self.assertEqual(clicked[-1], (9, 5))
        self.assertEqual(delays, [3, 2, 2, 2, 2])
        self.assertEqual(progress['steps'], 4)
        self.assertFalse(progress['inFlight'])
        self.assertEqual(progress['plan']['method'], 'known_route')

    def test_step_limit_stops_without_additional_clicks(self):
        with self.assertRaisesRegex(ValueError, '步上限'):
            self.execute_simulated((15, 5), limit=2)


if __name__ == '__main__':
    unittest.main()
