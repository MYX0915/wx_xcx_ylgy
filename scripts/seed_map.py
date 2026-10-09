"""Recover a captured seed using verified OFB plaintext and reproduce card shuffling."""

from __future__ import annotations

import base64
from collections import Counter
from copy import deepcopy
import json
import math
from urllib.parse import quote


def read_varint(data, pos):
    value = 0
    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError('Truncated seed protobuf')
        byte = data[pos]
        pos += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, pos
    raise ValueError('Invalid seed varint')


def decode_seed_ack(data):
    code, seeds, seed2, pos = None, [], None, 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        if tag in (8, 16):
            value, pos = read_varint(data, pos)
            if tag == 8:
                if code is not None:
                    raise ValueError('Duplicate seed status')
                code = value
            else:
                seeds.append(value)
        elif tag in (18, 26):
            size, pos = read_varint(data, pos)
            end = pos + size
            if end > len(data):
                raise ValueError('Truncated seed field')
            if tag == 18:
                packed, at = data[pos:end], 0
                while at < len(packed):
                    value, at = read_varint(packed, at)
                    seeds.append(value)
            else:
                if seed2 is not None:
                    raise ValueError('Duplicate seed identifier')
                seed2 = data[pos:end].decode('utf-8')
            pos = end
        else:
            raise ValueError('Unknown seed response field')
    if (code != 1 or len(seeds) != 4 or not any(seeds)
            or any(value > 0xFFFFFFFF for value in seeds) or not seed2):
        raise ValueError('Invalid decrypted seed response')
    return seeds, seed2


def settlement_prefix(form):
    # Client reportData property order, before optional fields are appended.
    strings = {'map_seed_2', 'version', 'play_info'}
    keys = ('rank_state', 'rank_time', 'map_seed_2', 'version',
            'play_info', 'removed', 'rand_times')
    values = {}
    for key in keys:
        value = form[key] if key in strings else json.loads(form[key])
        if key not in strings and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError('Invalid settlement numeric field')
        values[key] = value
    prefix = json.dumps(values, ensure_ascii=False, separators=(',', ':'))[:-1]
    return quote(prefix, safe="~!*'()-._").encode('ascii')


def recover_seed(initial_seed, request, ciphertext, settlement):
    if request['encryptKeyVersion'] != settlement['encrypt_key_version']:
        raise ValueError('Encryption versions differ')
    known = settlement_prefix(settlement)
    encrypted = base64.b64decode(settlement['encrypt_data'], validate=True)
    if len(encrypted) < len(known) or len(known) < len(ciphertext):
        raise ValueError('Insufficient known settlement plaintext')
    stream = bytes(a ^ b for a, b in zip(known, encrypted))
    frame = base64.b64decode(request['info'], validate=True)
    seed_text = initial_seed.encode('utf-8')
    if not 0 < len(seed_text) < 128 or frame[:3] != b'\x01\x75\xbf':
        raise ValueError('Unsupported seed request frame')
    expected = bytes((10, len(seed_text))) + seed_text
    # Bind this stream to this start request, not just a reusable key version.
    if len(frame) != len(expected) + 3 or bytes(a ^ b for a, b in zip(frame[3:], stream)) != expected:
        raise ValueError('Seed request does not match this game or encryption stream')
    return decode_seed_ack(bytes(a ^ b for a, b in zip(ciphertext, stream)))


def u32(value):
    return value & 0xFFFFFFFF


class XorShift128Plus:
    def __init__(self, seed):
        if (len(seed) != 4 or not any(seed)
                or any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF for value in seed)):
            raise ValueError('Expected four nonzero-state uint32 seed words')
        self.state0u, self.state0l, self.state1u, self.state1l = seed

    def snapshot(self):
        return [self.state0u, self.state0l, self.state1u, self.state1l]

    def random(self):
        e, t, n, i = self.state0u, self.state0l, self.state1u, self.state1l
        low_sum = u32(i + t)
        high_sum = u32(n + e + (1 if i + t > 0xFFFFFFFF else 0))
        self.state0u, self.state0l = n, i
        e = u32(e ^ u32((e << 23) | (t >> 9)))
        t = u32(t ^ u32(t << 23))
        self.state1u = u32((e ^ n) ^ (e >> 18) ^ (n >> 5))
        self.state1l = u32((t ^ i) ^ ((t >> 18) | ((e & 0x3FFFF) << 14))
                          ^ ((i >> 5) | ((n & 0x1F) << 27)))
        return 2.3283064365386963e-10 * high_sum + 2220446049250313e-31 * (low_sum >> 12)


def static_type_counts(game_map):
    counts = Counter()
    for kind, groups in game_map['blockTypeData'].items():
        if int(kind) <= 0 or type(groups) is not int or groups < 0:
            raise ValueError('Invalid static type counts')
        counts[int(kind)] += 3 * groups
    nodes = [node for layer in game_map['levelData'].values() for node in layer]
    fixed = Counter()
    untyped = 0
    for node in nodes:
        kind = node.get('type', 0)
        if type(kind) is not int or kind not in (0, 17):
            raise ValueError(f'Unsupported pretyped static node: {kind}')
        if kind:
            fixed[kind] += 1
        else:
            untyped += 1
    if not 0 < len(nodes) <= 500:
        raise ValueError('Unexpected static card count')
    if untyped != sum(counts.values()):
        raise ValueError(f'Static untyped node count differs: expected {sum(counts.values())}, got {untyped}')
    if fixed[17] % 3:
        raise ValueError('Fixed lightning cards must form triples')
    return counts + fixed


def assign_types(game_map, seed):
    static_type_counts(game_map)
    pool = []
    for kind, groups in sorted(game_map['blockTypeData'].items(), key=lambda pair: int(pair[0])):
        pool.extend([int(kind)] * (3 * groups))
    rng = XorShift128Plus(seed)
    rng.random()
    for index in range(len(pool) - 1, -1, -1):
        other = int(rng.random() * (index + 1))
        pool[index], pool[other] = pool[other], pool[index]
    result = deepcopy(game_map)
    card_id = 0
    for layer in sorted(result['levelData'], key=int):
        for node in result['levelData'][layer]:
            # Fixed lightning cards do not consume an entry in the shuffled pool.
            if node.get('type', 0) == 0:
                node['type'] = pool.pop()
            node['cardId'] = card_id
            card_id += 1
    if pool:
        raise ValueError('Static map has fewer nodes than its type counts')
    result['_shuffleState'] = rng.snapshot()
    return result
