#!/usr/bin/env python3
"""Create an instrumented copy of a WeChat mini-program seed package."""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path

from Crypto.Cipher import AES


MAGIC = b"V1MMWX"
AES_SALT = b"saltiest"
AES_IV = b"the iv: 16 bytes"
SEED_ASSIGNMENT = b"s.map_seed=e.mapSeed,s.map_seed_2=e.mapSeed2"
SEED_DUMP = (
    b'(function(d){var x=JSON.stringify(d);'
    b'console.log("__SHEEP_SEED_DUMP__",x);'
    b'try{wx.setStorageSync("__sheep_seed_dump_v1",x);'
    b'console.log("__SHEEP_SEED_STORAGE_OK__")}catch(t){'
    b'console.log("__SHEEP_SEED_STORAGE_ERROR__",String(t))}'
    b'try{wx.getFileSystemManager().writeFileSync(wx.env.USER_DATA_PATH+'
    b'"/sheep_seed_dump.json",x,"utf8");'
    b'console.log("__SHEEP_SEED_FILE_OK__")}catch(t){'
    b'console.log("__SHEEP_SEED_FILE_ERROR__",String(t))}})'
    b'({mapSeed:e.mapSeed,mapSeed2:e.mapSeed2});'
)


def _key(appid: str) -> tuple[bytes, int]:
    aes_key = hashlib.pbkdf2_hmac("sha1", appid.encode(), AES_SALT, 1000, 32)
    return aes_key, ord(appid[-2]) if len(appid) >= 2 else 0x66


def decrypt_package(data: bytes, appid: str) -> bytes:
    if not data.startswith(MAGIC):
        raise ValueError("Input is not a V1MMWX package")
    aes_key, xor_key = _key(appid)
    head = AES.new(aes_key, AES.MODE_CBC, AES_IV).decrypt(data[6:1030])[:1023]
    tail = bytes(value ^ xor_key for value in data[1030:])
    return head + tail


def encrypt_package(data: bytes, appid: str) -> bytes:
    aes_key, xor_key = _key(appid)
    head = data[:1023].ljust(1024, b"\0")
    encrypted_head = AES.new(aes_key, AES.MODE_CBC, AES_IV).encrypt(head)
    encrypted_tail = bytes(value ^ xor_key for value in data[1023:])
    return MAGIC + encrypted_head + encrypted_tail


def unpack_v2(data: bytes) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    if len(data) < 18 or data[0] != 0xBE or data[:4] == b"\xBE\xBA\x01\x00":
        raise ValueError("Only V2 wxapkg packages are supported")
    count = struct.unpack_from(">H", data, 16)[0]
    pos = 18
    records = []
    for _ in range(count):
        name_len = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        name = data[pos : pos + name_len]
        pos += name_len
        offset, size = struct.unpack_from(">II", data, pos)
        pos += 8
        if offset + size > len(data):
            raise ValueError("Package entry exceeds file bounds")
        records.append((name, data[offset : offset + size]))
    return data[:16], records


def pack_v2(prefix: bytes, records: list[tuple[bytes, bytes]]) -> bytes:
    table_size = sum(4 + len(name) + 8 for name, _ in records)
    offset = 18 + table_size
    table = bytearray()
    contents = bytearray()
    for name, content in records:
        table.extend(struct.pack(">I", len(name)))
        table.extend(name)
        table.extend(struct.pack(">II", offset, len(content)))
        contents.extend(content)
        offset += len(content)
    return prefix + struct.pack(">H", len(records)) + table + contents


def instrument(data: bytes) -> bytes:
    prefix, records = unpack_v2(data)
    target = b"subpackages/script-bundle/game.js"
    matches = [i for i, (name, _) in enumerate(records) if name.lstrip(b"/") == target]
    if len(matches) != 1:
        raise ValueError(f"Expected one {target.decode()} entry; found {len(matches)}")
    index = matches[0]
    name, source = records[index]
    if source.count(SEED_ASSIGNMENT) != 1:
        raise ValueError("Seed assignment did not match exactly once")
    records[index] = (name, source.replace(SEED_ASSIGNMENT, SEED_ASSIGNMENT + b"," + SEED_DUMP, 1))
    return pack_v2(prefix, records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("appid")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    original = args.package.read_bytes()
    plain = decrypt_package(original, args.appid)
    patched = encrypt_package(instrument(plain), args.appid)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patched)

    check = unpack_v2(decrypt_package(patched, args.appid))[1]
    if not any(name.lstrip(b"/") == b"subpackages/script-bundle/game.js" and SEED_DUMP in body for name, body in check):
        args.output.unlink(missing_ok=True)
        raise ValueError("Instrumented package failed round-trip verification")
    print(f"Created verified instrumented package: {args.output}")
    print(f"Original bytes: {len(original)}; instrumented bytes: {len(patched)}")


if __name__ == "__main__":
    main()
