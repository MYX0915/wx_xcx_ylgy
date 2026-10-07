#!/usr/bin/env python3
"""Decrypt a captured seed response locally and fill map node types."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
from pathlib import Path


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7
        if shift >= 70:
            raise ValueError("invalid protobuf varint")
    raise ValueError("truncated protobuf varint")


def decode_seed_ack(data: bytes) -> tuple[int, list[int], str]:
    code = 0
    seeds: list[int] = []
    seed2 = ""
    pos = 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = read_varint(data, pos)
            if field == 1:
                code = value
            elif field == 2:
                seeds.append(value)
        elif wire == 2:
            size, pos = read_varint(data, pos)
            end = pos + size
            if end > len(data):
                raise ValueError("truncated protobuf field")
            if field == 2:
                while pos < end:
                    value, pos = read_varint(data, pos)
                    seeds.append(value)
            elif field == 3:
                seed2 = data[pos:end].decode("utf-8")
                pos = end
            else:
                pos = end
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        if pos > len(data):
            raise ValueError("truncated protobuf value")
    if code != 1:
        raise ValueError(f"seed response code is {code}, expected success (1)")
    if len(seeds) != 4 or any(value > 0xFFFFFFFF for value in seeds):
        raise ValueError(f"expected four uint32 seed values, got {seeds}")
    return code, seeds, seed2


def decrypt_ofb(ciphertext: bytes, key_text: str, iv_text: str) -> bytes:
    try:
        from Crypto.Cipher import AES
    except ImportError as exc:
        raise RuntimeError("PyCryptodome is required: python3 -m pip install pycryptodome") from exc

    key = key_text.encode("utf-8")
    iv = iv_text.encode("utf-8")
    if len(iv) != AES.block_size:
        raise ValueError(f"IV must be 16 UTF-8 bytes, got {len(iv)}")
    if len(key) not in (16, 24, 32):
        raise ValueError(f"AES key must be 16, 24, or 32 UTF-8 bytes, got {len(key)}")
    return AES.new(key, AES.MODE_OFB, iv=iv).decrypt(ciphertext)


def u32(value: int) -> int:
    return value & 0xFFFFFFFF


class XorShift128Plus:
    def __init__(self, seed: list[int]):
        if len(seed) != 4:
            raise ValueError("seed must contain four integers")
        self.state0u, self.state0l, self.state1u, self.state1l = map(u32, seed)

    def random(self) -> float:
        e, t, n, i = self.state0u, self.state0l, self.state1u, self.state1l
        low_sum = u32(i + t)
        high_sum = u32(n + e + (1 if i + t > 0xFFFFFFFF else 0))
        self.state0u, self.state0l = n, i

        x = u32((e << 23) | (t >> 9))
        e = u32(e ^ x)
        y = u32(t << 23)
        t = u32(t ^ y)
        high = u32((e ^ n) ^ (e >> 18) ^ (n >> 5))
        low = u32((t ^ i) ^ ((t >> 18) | ((e & 0x3FFFF) << 14)) ^ ((i >> 5) | ((n & 0x1F) << 27)))
        self.state1u, self.state1l = high, low
        return 2.3283064365386963e-10 * high_sum + 2220446049250313e-31 * (low_sum >> 12)


def assign_types(map_data: dict, seed: list[int]) -> dict:
    pool: list[int] = []
    for card_type, group_count in sorted(map_data["blockTypeData"].items(), key=lambda item: int(item[0])):
        pool.extend([int(card_type)] * (3 * int(group_count)))

    rng = XorShift128Plus(seed)
    rng.random()
    for index in range(len(pool) - 1, -1, -1):
        swap_index = int(rng.random() * (index + 1))
        pool[index], pool[swap_index] = pool[swap_index], pool[index]

    result = json.loads(json.dumps(map_data))
    for layer in sorted(result["levelData"], key=int):
        for node in result["levelData"][layer]:
            if int(node.get("type", 0)) == 0:
                if not pool:
                    raise ValueError("blockTypeData has fewer cards than untyped map nodes")
                node["type"] = pool.pop()
    if pool:
        raise ValueError(f"{len(pool)} card types were not assigned to map nodes")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", type=Path, required=True, help="decoded map_data JSON")
    parser.add_argument("--response", required=True, help="base64 map_info_ex_seed response")
    parser.add_argument("--output", type=Path, help="output JSON path")
    args = parser.parse_args()

    key = getpass.getpass("Paste encryptKey (input hidden): ")
    iv = getpass.getpass("Paste IV (input hidden): ")
    plaintext = decrypt_ofb(base64.b64decode(args.response, validate=True), key, iv)
    _, seed, seed2 = decode_seed_ack(plaintext)
    map_data = json.loads(args.map.read_text(encoding="utf-8"))
    result = assign_types(map_data, seed)

    output = args.output or args.map.with_name(args.map.stem.replace("-map_data", "") + "-map_data-with-types.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    count = sum(len(nodes) for nodes in result["levelData"].values())
    print(f"解密成功：seed={seed}, mapSeed2={seed2!r}")
    print(f"已为 {count} 个节点填入牌型：{output}")


if __name__ == "__main__":
    main()
