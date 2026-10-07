#!/usr/bin/env python3
"""Read-only inspection of Reqable binary response bodies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAPTURES = ROOT / "captures"
OUTPUT = ROOT / "decoded"


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift >= 70:
            raise ValueError("invalid varint")
    raise ValueError("truncated varint")


def fields(data: bytes, depth: int = 0) -> list[dict]:
    result = []
    pos = 0
    while pos < len(data):
        key, pos = read_varint(data, pos)
        number, wire = key >> 3, key & 7
        item = {"field": number, "wire": wire}
        if wire == 0:
            item["value"], pos = read_varint(data, pos)
        elif wire == 1:
            item["value"] = data[pos : pos + 8].hex()
            pos += 8
        elif wire == 2:
            size, pos = read_varint(data, pos)
            value = data[pos : pos + size]
            pos += size
            item["size"] = size
            item["hex"] = value.hex()
            if value and all(32 <= c < 127 or c in (9, 10, 13) for c in value):
                item["text"] = value.decode("ascii", "replace")
            elif depth < 5:
                try:
                    nested = fields(value, depth + 1)
                    if nested:
                        item["message"] = nested
                except ValueError:
                    pass
        elif wire == 5:
            item["value"] = data[pos : pos + 4].hex()
            pos += 4
        else:
            item["unsupported"] = True
            break
        result.append(item)
    return result


def exported_body(path: Path) -> bytes:
    """Extract the body after Reqable's HTTP header block."""
    raw = path.read_bytes()
    marker = b"\r\n\r\n"
    return raw.split(marker, 1)[1] if marker in raw else raw


def matching(items: list[dict], number: int) -> list[dict]:
    return [item for item in items if item.get("field") == number]


def scalar(items: list[dict], number: int, default=None):
    item = next(iter(matching(items, number)), None)
    if item is None:
        return default
    return item.get("value", item.get("text", default))


def decode_map_file(path: Path) -> dict:
    raw = path.read_bytes()
    if len(raw) <= 21 or not raw.startswith(b".map"):
        raise ValueError(f"invalid map file header: {path.name}")

    root = fields(raw[21:])
    result = {
        "widthNum": scalar(root, 1, 0),
        "heightNum": scalar(root, 2, 0),
        "levelKey": scalar(root, 3, 0),
        "blockTypeData": {},
        "levelData": {},
        "layers": [],
        "operations": [],
    }

    for entry in matching(root, 4):
        message = entry.get("message", [])
        key = scalar(message, 1)
        if key is not None:
            result["blockTypeData"][str(key)] = scalar(message, 2, 0)

    for entry in matching(root, 5):
        message = entry.get("message", [])
        layer = scalar(message, 1)
        node_list = next(iter(matching(message, 2)), {}).get("message", [])
        nodes = []
        for node_entry in matching(node_list, 1):
            node = node_entry.get("message", [])
            nodes.append({
                "id": scalar(node, 1, ""),
                "type": scalar(node, 2, 0),
                "rolNum": scalar(node, 3, 0),
                "rowNum": scalar(node, 4, 0),
                "layerNum": scalar(node, 5, 0),
                "moldType": scalar(node, 6, 0),
                "blockNode": None,
            })
        if layer is not None:
            result["levelData"][str(layer)] = nodes

    result["layers"] = sorted(result["levelData"], key=int)
    return result


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    report = []
    for path in sorted(CAPTURES.glob("*-res-raw-body.reqable")):
        data = path.read_bytes()
        entry = {
            "file": path.name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        try:
            entry["fields"] = fields(data)
            entry["parse_error"] = None
        except ValueError as exc:
            entry["fields"] = []
            entry["parse_error"] = str(exc)
        report.append(entry)
    for path in sorted((CAPTURES / "exported").glob("*-response.txt")):
        data = exported_body(path)
        entry = {
            "file": str(path.relative_to(ROOT)),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        try:
            entry["fields"] = fields(data)
            entry["parse_error"] = None
        except ValueError as exc:
            entry["fields"] = []
            entry["parse_error"] = str(exc)
        report.append(entry)
    for path in sorted(CAPTURES.glob("*.map")):
        map_data = decode_map_file(path)
        output_path = OUTPUT / f"{path.stem}-map_data.json"
        output_path.write_text(
            json.dumps(map_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        node_count = sum(len(nodes) for nodes in map_data["levelData"].values())
        print(f"地图解码: {path.name}, {node_count} 个方块, 输出 {output_path.name}")
    (OUTPUT / "capture_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"分析完成: {len(report)} 份响应")
    for item in report:
        print(f"{item['file']}: {item['size']} bytes, {item['sha256'][:16]}")


if __name__ == "__main__":
    main()
