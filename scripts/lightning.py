"""Deterministic lightning energy and UFO removal for untouched initial boards."""

from collections import Counter
import time

if __package__:
    from .seed_map import XorShift128Plus
else:
    from seed_map import XorShift128Plus


VERSION = 1
LABELS = {'daily_ordinary': '普通局', 'world_ordinary': '大世界普通局',
          'lightning_ufo': '闪电飞碟局'}


def map_kind(game_map, mode):
    if mode not in ('daily', 'world'):
        raise ValueError('Unsupported game mode')
    if identify_mechanics(game_map)['kind'] == 'lightning_ufo':
        return 'lightning_ufo'
    return mode + '_ordinary'


def identify_mechanics(game_map):
    gold = game_map.get('goldBlockData')
    if gold is None:
        return {'kind': 'ordinary', 'version': VERSION}
    if not isinstance(gold, dict) or type(gold.get('collectCount')) is not int:
        raise ValueError('Invalid goldBlockData')
    threshold = gold['collectCount']
    if threshold == 0:
        return {'kind': 'ordinary', 'version': VERSION}
    blocks = gold.get('blockList')
    if (not 0 < threshold <= 500 or not isinstance(blocks, list) or len(blocks) != 1
            or not isinstance(blocks[0], dict)
            or type(blocks[0].get('val')) is not int or blocks[0]['val'] != 17):
        raise ValueError('Unsupported UFO collection configuration')
    return {'kind': 'lightning_ufo', 'version': VERSION,
            'energyType': 17, 'collectCount': threshold, 'initialEnergy': 4}


class LightningBoard:
    def __init__(self, game_map, cards):
        if __package__:
            from .solve_map import blocker_masks
        else:
            from solve_map import blocker_masks

        self.config = identify_mechanics(game_map)
        if self.config['kind'] != 'lightning_ufo':
            raise ValueError('Expected lightning UFO map')
        self.cards = cards
        self.rng = XorShift128Plus(game_map.get('_shuffleState', []))
        self.remaining = set(range(len(cards)))
        self.blockers = blocker_masks(cards)
        self.tray = []
        self.energy = {i: 4 for i, c in enumerate(cards) if c.type == 17}
        self.collected = 0
        self.used = False
        self.steps = []
        self.auto_removed = []
        self.auto_board_removed = 0
        # JS nested objects enumerate numeric layer, row, column keys in this order.
        self.board_order = sorted(self.remaining,
                                  key=lambda i: (cards[i].layer, cards[i].y, cards[i].x))
        if any(min(c.layer, c.x, c.y) < 0 for c in cards):
            raise ValueError('Unsupported negative UFO board coordinates')
        if (any(type(c.card_id) is not int or c.card_id < 0 for c in cards)
                or len({c.card_id for c in cards}) != len(cards)):
            raise ValueError('Duplicate numeric card ID')

    def visible(self):
        mask = sum(1 << i for i in self.remaining)
        return {i for i in self.remaining if not self.blockers[i] & mask}

    def choose(self, count, kind=None):
        available = [i for i in self.board_order if i in self.remaining]
        if kind is None:
            if not available:
                return [], 0
            kind = self.cards[available[int(self.rng.random() * len(available))]].type
            extra_draw = 1
        else:
            extra_draw = 0
        pool = [i for i in available if self.cards[i].type == kind]
        if len(pool) < count:
            raise ValueError('UFO cannot complete a triple from the remaining board')
        for index in range(len(pool) - 1, len(pool) - count - 1, -1):
            other = int(self.rng.random() * (index + 1))
            pool[index], pool[other] = pool[other], pool[index]
        return pool[-count:], count + extra_draw

    def collect(self):
        removed = self.tray[:]
        random_draws = 0
        held = Counter(self.cards[i].type for i in self.tray)
        for kind, count in held.items():
            selected, draws = self.choose(3 - count, kind)
            removed.extend(selected)
            random_draws += draws
        if not held:
            removed, random_draws = self.choose(3)
        self.auto_board_removed += len(self.remaining.intersection(removed))
        self.remaining.difference_update(removed)
        self.tray.clear()
        self.energy.clear()
        self.used = True
        self.collected -= self.config['collectCount']
        self.auto_removed.extend(removed)
        return {'removedCardIds': [self.cards[i].card_id for i in removed],
                'randomDraws': random_draws}

    def click(self, index):
        visible = self.visible()
        if index not in visible:
            raise ValueError('Missing or covered card in UFO plan')
        if len(self.tray) >= 7:
            raise ValueError('UFO tray already full')
        card = self.cards[index]
        before = [self.cards[i].type for i in self.tray]
        self.remaining.remove(index)
        if self.energy.pop(index, 0) > 0:
            self.collected += 1
        # Client decays currently lit cards before refreshing newly exposed cards.
        for i in visible - {index}:
            if self.energy.get(i, 0) > 0:
                self.energy[i] -= 1
        same = [j for j, i in enumerate(self.tray) if self.cards[i].type == card.type]
        self.tray.insert(same[-1] + 1 if same else len(self.tray), index)
        eliminated = len(same) == 2
        if eliminated:
            self.tray = [i for i in self.tray if self.cards[i].type != card.type]
        # Do not rely on the animation rescuing a full, non-matching tray.
        if len(self.tray) >= 7:
            raise ValueError('Seven cards remain without elimination')
        ufo = None
        if not self.used and self.collected >= self.config['collectCount']:
            ufo = self.collect()
        step = {'step': len(self.steps) + 1, 'id': card.id, 'cardId': card.card_id,
                'type': card.type, 'name': card.name, 'layer': card.layer,
                'x': card.x, 'y': card.y, 'trayBefore': before,
                'trayAfter': [self.cards[i].type for i in self.tray],
                'eliminated': eliminated, 'ufo': ufo,
                'energyCollected': self.collected, 'remainingCards': len(self.remaining)}
        self.steps.append(step)
        return step


def replay_lightning(game_map, cards, order):
    board = LightningBoard(game_map, cards)
    for index in order:
        board.click(index)
    if board.remaining or board.tray:
        raise ValueError('Incomplete UFO solution')
    return {'steps': board.steps, 'verified': True,
            'mechanicsVersion': VERSION, 'mapKind': 'lightning_ufo',
            'automaticRemovedCards': len(board.auto_removed),
            'automaticBoardRemovedCards': board.auto_board_removed,
            'ufoEvents': sum(step['ufo'] is not None for step in board.steps)}


def solve_lightning(game_map, cards, seconds):
    if __package__:
        from .solve_map import solve
    else:
        from solve_map import solve

    started = time.monotonic()
    result = solve(cards, seconds)
    if result['status'] != 'solved':
        return result
    board = LightningBoard(game_map, cards)
    order = []
    for index in result['order']:
        order.append(index)
        if board.click(index)['ufo'] is not None:
            break
    if board.used and board.remaining:
        remaining = sorted(board.remaining)
        budget = seconds - (time.monotonic() - started)
        if budget <= 0:
            return {'status': 'timeout', 'order': []}
        tail = solve([cards[i] for i in remaining], budget)
        if tail['status'] != 'solved':
            return {'status': tail['status'], 'order': []}
        order.extend(remaining[i] for i in tail['order'])
    verified = replay_lightning(game_map, cards, order)
    return {'status': 'solved', 'order': order, 'backend': 'lightning_ufo_replan',
            'elapsedSeconds': round(time.monotonic() - started, 3), **verified}
