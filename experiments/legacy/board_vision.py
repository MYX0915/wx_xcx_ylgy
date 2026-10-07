"""Conservative geometry and sprite checks for the calibrated daily board."""

from collections import defaultdict

import numpy as np
from PIL import Image

from solve_map import blocker_masks


def face_boxes(image):
    pixels = np.asarray(image.convert('RGB'))
    red, green, blue = pixels[:, :, 0], pixels[:, :, 1], pixels[:, :, 2]
    mask = (red > 235) & (green > 245) & (blue > 185) & (blue < 225)
    height, width = mask.shape
    boxes = []
    for yy, xx in np.argwhere(mask):
        y, x = int(yy), int(xx)
        if not mask[y, x]:
            continue
        queue = [(x, y)]
        mask[y, x] = False
        left = right = x
        top = bottom = y
        size = 0
        while queue:
            a, b = queue.pop()
            left, right = min(left, a), max(right, a)
            top, bottom = min(top, b), max(bottom, b)
            size += 1
            for nx, ny in ((a-1, b), (a+1, b), (a, b-1), (a, b+1)):
                if 0 <= nx < width and 0 <= ny < height and mask[ny, nx]:
                    mask[ny, nx] = False
                    queue.append((nx, ny))
        w, h = right-left+1, bottom-top+1
        if (size > 0.001 * width**2 and 0.06*width < w < 0.16*width
                and 0.75 < h/w < 1.3):
            boxes.append((left, top, right+1, bottom+1))
    return boxes


def center(box):
    a, b, c, d = box
    return ((a+c)/2, (b+d)/2)


def row_order(boxes, tolerance):
    rows = []
    for box in sorted(boxes, key=lambda b: center(b)[1]):
        if not rows or center(box)[1]-center(rows[-1][0])[1] > tolerance:
            rows.append([])
        rows[-1].append(box)
    return [b for row in rows for b in sorted(row, key=lambda b: center(b)[0])]


def available_cards(cards, remaining):
    masks = blocker_masks(cards)
    bits = sum(1 << i for i in remaining)
    return [cards[i] for i in remaining if not masks[i] & bits]


def calibrate(image, cards):
    available = sorted(available_cards(cards, set(range(len(cards)))), key=lambda c: (c.y, c.x))
    boxes = [b for b in face_boxes(image) if center(b)[1] < image.height*0.74]
    boxes = row_order(boxes, image.width*0.005)
    if len(boxes) != len(available) or len(boxes) < 6:
        raise ValueError(f'Initial board differs: {len(boxes)} visible faces, expected {len(available)}')
    model = np.array([(c.x, c.y) for c in available])
    observed = np.array([center(b) for b in boxes])
    coefficients = []
    for axis in (0, 1):
        design = np.column_stack((model[:, axis], np.ones(len(model))))
        slope, offset = np.linalg.lstsq(design, observed[:, axis], rcond=None)[0]
        if slope <= 0 or np.max(np.abs(design @ [slope, offset]-observed[:, axis])) > image.width*0.004:
            raise ValueError('Board coordinates cannot be calibrated reliably')
        coefficients.append([float(slope), float(offset)])
    if abs(coefficients[0][0]/coefficients[1][0]-1) > 0.03:
        raise ValueError('Inconsistent board scale')
    return {'size': list(image.size), 'x': coefficients[0], 'y': coefficients[1]}


def card_point(card, calibration):
    return (card.x*calibration['x'][0]+calibration['x'][1],
            card.y*calibration['y'][0]+calibration['y'][1])


def patch(image, box):
    x, y = center(box)
    side = (box[2]-box[0])*0.94
    crop = image.crop((x-side/2, y-side/2, x+side/2, y+side/2)).convert('RGB')
    return np.asarray(crop.resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float32)/255


def sprite_distance(a, b):
    return float(np.mean(np.abs(a-b)))


def match_geometry(image, cards, remaining, calibration):
    if list(image.size) != calibration['size']:
        raise ValueError('Window screenshot size changed')
    boxes = face_boxes(image)
    board = [b for b in boxes if center(b)[1] < image.height*0.74]
    tray = sorted([b for b in boxes if image.height*0.74 <= center(b)[1] < image.height*0.89],
                  key=lambda b: center(b)[0])
    available = available_cards(cards, remaining)
    if len(board) != len(available):
        raise ValueError(f'Visible board count differs: observed {len(board)}, expected {len(available)}')
    matched = []
    for card in available:
        point = card_point(card, calibration)
        near = [b for b in board if np.linalg.norm(np.array(center(b))-point) < image.width*0.007]
        if len(near) != 1:
            raise ValueError('Missing visible card: ' + card.id)
        board.remove(near[0])
        matched.append((card, near[0]))
    return matched, tray


def seed_templates(image, cards):
    calibration = calibrate(image, cards)
    matched, tray = match_geometry(image, cards, set(range(len(cards))), calibration)
    if tray:
        raise ValueError('Reference screenshot tray is not empty')
    bank = defaultdict(list)
    for card, box in matched:
        bank[card.type].append(patch(image, box))
    return dict(bank)


def verify_screen(image, cards, remaining, tray_types, calibration, bank, learn=False):
    matched, tray_boxes = match_geometry(image, cards, remaining, calibration)
    if len(tray_boxes) != len(tray_types):
        raise ValueError(f'Tray count differs: observed {len(tray_boxes)}, expected {len(tray_types)}')
    pending = defaultdict(list)
    known = 0
    checks = ([(c.type, b, 0.10, c.id) for c, b in matched]
              + [(t, b, 0.14, f'tray#{i+1}') for i, (t, b) in enumerate(zip(tray_types, tray_boxes))])
    for kind, box, tolerance, label in checks:
        sample = patch(image, box)
        scores = {t: min(sprite_distance(sample, reference) for reference in refs)
                  for t, refs in bank.items()}
        if kind not in bank:
            pending[kind].append(sample)
            continue
        ranked = sorted((distance, card_type) for card_type, distance in scores.items())
        best_distance, best_type = ranked[0]
        clear_match = (best_type == kind and best_distance <= 0.16
                       and (len(ranked) == 1 or ranked[1][0] - best_distance >= 0.05))
        if best_type != kind or (scores[kind] > tolerance and not clear_match):
            second = ranked[1] if len(ranked) > 1 else (None, None)
            raise ValueError(
                f'Card image differs at {label}: expected type {kind}, '
                f'best type {best_type} ({best_distance:.3f}), '
                f'next type {second[1]} ({second[0]:.3f})'
            )
        known += 1
    if pending:
        if not learn or known < 5:
            raise ValueError('Not enough verified context to learn a newly exposed type')
        for kind, samples in pending.items():
            if any(sprite_distance(samples[0], s) > 0.1 for s in samples):
                raise ValueError('Newly exposed same-type images differ')
            if min(sprite_distance(samples[0], ref) for refs in bank.values() for ref in refs) < 0.035:
                raise ValueError('Unknown type unexpectedly matches a known sprite')
            bank[kind] = samples
    return {'available': len(matched), 'trayCount': len(tray_boxes), 'knownChecks': known}


def save_templates(bank, path):
    arrays = {f'{kind}_{i}': ref for kind, refs in bank.items() for i, ref in enumerate(refs)}
    np.savez_compressed(path, **arrays)


def load_templates(path):
    bank = defaultdict(list)
    with np.load(path, allow_pickle=False) as data:
        for key in data.files:
            bank[int(key.split('_')[0])].append(data[key])
    return dict(bank)
