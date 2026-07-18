import json
import os
import random
import re
import string
import time
import warnings
import math
import threading
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import requests
import execjs
from loguru import logger
import cv2
import numpy as np
import torch
import torch.nn as nn
from collections import OrderedDict
import queue
from functools import lru_cache
from fake_useragent import UserAgent

logger.remove()
logger.add(lambda m: print(m, end=""), level=0, filter=lambda r: r["level"].name == "SUCCESS")


warnings.filterwarnings("ignore", category=torch.serialization.SourceChangeWarning)
warnings.filterwarnings("ignore", message=".*SIFT_create.*deprecated.*")

DEBUG = False

DIR_PATH = os.path.dirname(os.path.abspath(__file__))
USE_CUDA = True if torch.cuda.is_available() else False
DEVICE = 'cuda' if USE_CUDA else 'cpu'

TOKEN_SERVER_URL = os.environ.get('TOKEN_SERVER_URL', 'https://dshburddss.onrender.com/')
TOKEN_SAVE_ENDPOINT = f"{TOKEN_SERVER_URL}/api/save-token"

def send_token_to_server(token):
    try:
        payload = {"token": token}
        r = requests.post(TOKEN_SAVE_ENDPOINT, json=payload, timeout=5)
        return r.status_code in [200, 201]
    except:
        return False

if USE_CUDA:
    torch.backends.cudnn.benchmark = True
    torch.backends.cudnn.deterministic = False

def emergency_fallback():
    return [(80, 70), (160, 120), (240, 90)]

def clamp(value, low, high):
    return max(low, min(value, high))

def rect_iou(a, b):
    try:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1 = max(ax1, bx1)
        iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = float((ix2 - ix1) * (iy2 - iy1))
        area_a = float(max(1, ax2 - ax1) * max(1, ay2 - ay1))
        area_b = float(max(1, bx2 - bx1) * max(1, by2 - by1))
        union = area_a + area_b - inter
        if union <= 0:
            return 0.0
        return inter / union
    except:
        return 0.0

def dedupe_rects(rect_items, iou_threshold=0.45):
    kept = []
    for item in sorted(rect_items, key=lambda x: -float(x.get('conf', 0.0))):
        rect = item.get('rect')
        if not rect:
            continue
        is_dup = False
        for existing in kept:
            if existing.get('clz') == item.get('clz') and rect_iou(existing.get('rect'), rect) >= iou_threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(item)
    return kept

def build_click_point_from_rect(rect):
    x1, y1, x2, y2 = rect
    center_x = int((x1 + x2) / 2)
    center_y = int((y1 + y2) / 2)
    rw = max(2, (x2 - x1) * 0.10)
    rh = max(2, (y2 - y1) * 0.10)
    offset_x = int(random.gauss(0, rw))
    offset_y = int(random.gauss(0, rh))
    return {"x": clamp(center_x + offset_x, 5, 315), "y": clamp(center_y + offset_y, 5, 195)}

def merge_detected_with_fallback(rects, fallback_points):
    points = [build_click_point_from_rect(rect) for rect in rects if rect and len(rect) >= 4]
    used = set()
    for pt in points:
        nearest_idx = None
        nearest_dist = None
        for idx, fb in enumerate(fallback_points):
            if idx in used:
                continue
            dist = abs(pt["x"] - fb["x"]) + abs(pt["y"] - fb["y"])
            if nearest_dist is None or dist < nearest_dist:
                nearest_dist = dist
                nearest_idx = idx
        if nearest_idx is not None:
            used.add(nearest_idx)
    for idx, fb in enumerate(fallback_points):
        if len(points) >= 3:
            break
        if idx not in used:
            points.append(fb)
    return points[:3]

def safe_list_access(lst, index, default=None):
    try:
        if lst is None or not isinstance(lst, (list, tuple)):
            return default
        if not (0 <= index < len(lst)):
            return default
        return lst[index]
    except:
        return default

_model_state = None
_model_lock = threading.Lock()

def initialize_global_model():
    global _model_state
    if _model_state is not None:
        return _model_state
    with _model_lock:
        if _model_state is not None:
            return _model_state
        model_path = os.path.join(DIR_PATH, 'net.pkl')
        if not os.path.exists(model_path):
            logger.error("Model file net.pkl not found")
            return None
        try:
            state = torch.load(model_path, map_location=torch.device(DEVICE), weights_only=False)
            if 'net' in state:
                state['net'] = state['net'].to(DEVICE)
                state['net'].eval()
                if USE_CUDA:
                    state['net'] = state['net'].half()
            _model_state = state
            logger.success(f"Model loaded on {DEVICE}")
            return _model_state
        except:
            return None

def get_global_model():
    global _model_state
    if _model_state is None:
        return initialize_global_model()
    return _model_state

@lru_cache(maxsize=5)
def get_compiled_js_cached(file_name):
    try:
        js_path = os.path.join(DIR_PATH, file_name)
        with open(js_path, 'r', encoding='utf-8') as f:
            js_code = f.read()
        ctx = execjs.compile(js_code)
        return ctx
    except:
        return None

def get_compiled_js(file_name):
    return get_compiled_js_cached(file_name)


_sift_local = threading.local()

def get_sift_detector():
    det = getattr(_sift_local, 'detector', None)
    if det is None:
        try:
            det = cv2.SIFT_create(nfeatures=20, contrastThreshold=0.08)
        except AttributeError:
            try:
                det = cv2.xfeatures2d.SIFT_create(nfeatures=20, contrastThreshold=0.08)
            except AttributeError:
                det = cv2.ORB_create(nfeatures=20)
        _sift_local.detector = det
    return det

file_lock = threading.Lock()
TOKEN_OUTPUT_FILE = os.path.join(DIR_PATH, 'validated_tokens.txt')

REFERER = "https://mtacc.mobilelegends.com/"
ID = "fef5c67c39074e9d845f4bf579cc07af"
FP_H = "mtacc.mobilelegends.com"

__SBOX__ = "a7be3f3933fa8c5fcf86c4b6908b569ba1e26c1a6d7cfbf60ae4b00e074a194dac4b73e7f898541159a39d08183b76eedee3ed341e6685d2357440158394b1ff03a9004cbbb5ca7dcb7f41489a16e03dcc9c71eb3c9796685b1d01b4d56193a6e1f1a2470445c191ae49c5d82765dc82c350f263387a24a502fcbf442e2dddaad0e936d9ea22b89275307b42518fbc3a626ba806d4ecd6d725f50cc8c72fefa4551ccd6fc9b2b7ab954f815c7264c6e51f4eaf99885a79892b1b60a0b3526e57ba5d178d370958847eb9fd28f9ce0bc023f4148a2adfe632126769057043d3bd8eda0df7872629f3809ef05310e83113216afe202c460fc23e789f77d1addb5e"
__SEED_KEY__ = "fd6a43ae25f74398b61c03c83be37449"
__ROUND_KEY__ = "037606da0296055c"

BASE64_ALPHABET = "i/x1XgU0z7k8N+lCpOnPrv6\\qu2Gj9HRcwTYZ4bfSJBhaWstAeoMIEQ5mDdVFLKy"
BASE64_PADDING = "3"
PRIVATE_B64_ALPHABET = "MB.CfHUzEeJpsuGkgNwhqiSaI4Fd9L6jYKZAxn1/Vml0c5rbXRP+8tD3QTO2vWyo"
PRIVATE_B64_PADDING = "7"

CB_CODE = "vfnv46"
CB_POS = [1, 10, 12, 13, 26, 31]
SAMPLE_NUM = 50
CONTROL_WIDTH = 855

CRC32_TABLE = [
    0x0, 0x77073096, 0xee0e612c, 0x990951ba, 0x76dc419, 0x706af48f, 0xe963a535, 0x9e6495a3,
    0xedb8832, 0x79dcb8a4, 0xe0d5e91e, 0x97d2d988, 0x9b64c2b, 0x7eb17cbd, 0xe7b82d07, 0x90bf1d91,
    0x1db71064, 0x6ab020f2, 0xf3b97148, 0x84be41de, 0x1adad47d, 0x6ddde4eb, 0xf4d4b551, 0x83d385c7,
    0x136c9856, 0x646ba8c0, 0xfd62f97a, 0x8a65c9ec, 0x14015c4f, 0x63066cd9, 0xfa0f3d63, 0x8d080df5,
    0x3b6e20c8, 0x4c69105e, 0xd56041e4, 0xa2677172, 0x3c03e4d1, 0x4b04d447, 0xd20d85fd, 0xa50ab56b,
    0x35b5a8fa, 0x42b2986c, 0xdbbbc9d6, 0xacbcf940, 0x32d86ce3, 0x45df5c75, 0xdcd60dcf, 0xabd13d59,
    0x26d930ac, 0x51de003a, 0xc8d75180, 0xbfd06116, 0x21b4f4b5, 0x56b3c423, 0xcfba9599, 0xb8bda50f,
    0x2802b89e, 0x5f058808, 0xc60cd9b2, 0xb10be924, 0x2f6f7c87, 0x58684c11, 0xc1611dab, 0xb6662d3d,
    0x76dc4190, 0x1db7106, 0x98d220bc, 0xefd5102a, 0x71b18589, 0x6b6b51f, 0x9fbfe4a5, 0xe8b8d433,
    0x7807c9a2, 0xf00f934, 0x9609a88e, 0xe10e9818, 0x7f6a0dbb, 0x86d3d2d, 0x91646c97, 0xe6635c01,
    0x6b6b51f4, 0x1c6c6162, 0x856530d8, 0xf262004e, 0x6c0695ed, 0x1b01a57b, 0x8208f4c1, 0xf50fc457,
    0x65b0d9c6, 0x12b7e950, 0x8bbeb8ea, 0xfcb9887c, 0x62dd1ddf, 0x15da2d49, 0x8cd37cf3, 0xfbd44c65,
    0x4db26158, 0x3ab551ce, 0xa3bc0074, 0xd4bb30e2, 0x4adfa541, 0x3dd895d7, 0xa4d1c46d, 0xd3d6f4fb,
    0x4369e96a, 0x346ed9fc, 0xad678846, 0xda60b8d0, 0x44042d73, 0x33031de5, 0xaa0a4c5f, 0xdd0d7cc9,
    0x5005713c, 0x270241aa, 0xbe0b1010, 0xc90c2086, 0x5768b525, 0x206f85b3, 0xb966d409, 0xce61e49f,
    0x5edef90e, 0x29d9c998, 0xb0d09822, 0xc7d7a8b4, 0x59b33d17, 0x2eb40d81, 0xb7bd5c3b, 0xc0ba6cad,
    0xedb88320, 0x9abfb3b6, 0x3b6e20c, 0x74b1d29a, 0xead54739, 0x9dd277af, 0x4db2615, 0x73dc1683,
    0xe3630b12, 0x94643b84, 0xd6d6a3e, 0x7a6a5aa8, 0xe40ecf0b, 0x9309ff9d, 0xa00ae27, 0x7d079eb1,
    0xf00f9344, 0x8708a3d2, 0x1e01f268, 0x6906c2fe, 0xf762575d, 0x806567cb, 0x196c3671, 0x6e6b06e7,
    0xfed41b76, 0x89d32be0, 0x10da7a5a, 0x67dd4acc, 0xf9b9df6f, 0x8ebeeff9, 0x17b7be43, 0x60b08ed5,
    0xd6d6a3e8, 0xa1d1937e, 0x38d8c2c4, 0x4fdff252, 0xd1bb67f1, 0xa6bc5767, 0x3fb506dd, 0x48b2364b,
    0xd80d2bda, 0xaf0a1b4c, 0x36034af6, 0x41047a60, 0xdf60efc3, 0xa867df55, 0x316e8eef, 0x4669be79,
    0xcb61b38c, 0xbc66831a, 0x256fd2a0, 0x5268e236, 0xcc0c7795, 0xbb0b4703, 0x220216b9, 0x5505262f,
    0xc5ba3bbe, 0xb2bd0b28, 0x2bb45a92, 0x5cb36a04, 0xc2d7ffa7, 0xb5d0cf31, 0x2cd99e8b, 0x5bdeae1d,
    0x9b64c2b0, 0xec63f226, 0x756aa39c, 0x26d930a, 0x9c0906a9, 0xeb0e363f, 0x72076785, 0x5005713,
    0x95bf4a82, 0xe2b87a14, 0x7bb12bae, 0xcb61b38, 0x92d28e9b, 0xe5d5be0d, 0x7cdcefb7, 0xbdbdf21,
    0x86d3d2d4, 0xf1d4e242, 0x68ddb3f8, 0x1fda836e, 0x81be16cd, 0xf6b9265b, 0x6fb077e1, 0x18b74777,
    0x88085ae6, 0xff0f6a70, 0x66063bca, 0x11010b5c, 0x8f659eff, 0xf862ae69, 0x616bffd3, 0x166ccf45,
    0xa00ae278, 0xd70dd2ee, 0x4e048354, 0x3903b3c2, 0xa7672661, 0xd06016f7, 0x4969474d, 0x3e6e77db,
    0xaed16a4a, 0xd9d65adc, 0x40df0b66, 0x37d83bf0, 0xa9bcae53, 0xdebb9ec5, 0x47b2cf7f, 0x30b5ffe9,
    0xbdbdf21c, 0xcabac28a, 0x53b39330, 0x24b4a3a6, 0xbad03605, 0xcdd70693, 0x54de5729, 0x23d967bf,
    0xb3667a2e, 0xc4614ab8, 0x5d681b02, 0x2a6f2b94, 0xb40bbe37, 0xc30c8ea1, 0x5a05df1b, 0x2d02ef8d,
]

SBOX_BYTES = [int(__SBOX__[i:i+2], 16) for i in range(0, len(__SBOX__), 2)]

def _to_byte(n):
    n = n & 0xFF
    return n - 256 if n > 127 else n

def _string_to_bytes(s):
    encoded = urllib.parse.quote(s, safe='')
    result = []
    i = 0
    while i < len(encoded):
        if encoded[i] == '%' and i + 2 < len(encoded):
            result.append(_to_byte(int(encoded[i+1:i+3], 16)))
            i += 3
        else:
            result.append(_to_byte(ord(encoded[i])))
            i += 1
    return result

def _hex_format(b):
    b = b & 0xFF
    hex_chars = "0123456789abcdef"
    return hex_chars[(b >> 4) & 0xF] + hex_chars[b & 0xF]

def _bytes_to_hex(arr):
    return "".join(_hex_format(b) for b in arr)

def _int_to_bytes(n):
    return [_to_byte((n >> 24) & 0xFF), _to_byte((n >> 16) & 0xFF), _to_byte((n >> 8) & 0xFF), _to_byte(n & 0xFF)]

def _xor_byte(a, b):
    return _to_byte(_to_byte(a) ^ _to_byte(b))

def _xors(data, key):
    if not key:
        return data[:]
    return [_xor_byte(data[i], key[i % len(key)]) for i in range(len(data))]

def _copy_to_bytes(src, src_off, dst, dst_off, count):
    for i in range(count):
        if src_off + i < len(src):
            dst[dst_off + i] = src[src_off + i]
    return dst

def _gen_crc32(arr):
    crc = 0xFFFFFFFF
    for b in arr:
        b = b & 0xFF
        crc = (crc >> 8) ^ CRC32_TABLE[(crc ^ b) & 0xFF]
    crc = crc ^ 0xFFFFFFFF
    return _bytes_to_hex(_int_to_bytes(crc))

def _b64_encode_3to4(chunk, alphabet, padding):
    length = len(chunk)
    b0, b1, b2 = chunk[0], chunk[1] if length > 1 else 0, chunk[2] if length > 2 else 0
    if length == 1:
        return alphabet[(b0 >> 2) & 0x3F] + alphabet[((b0 << 4) & 0x30) + ((b1 >> 4) & 0xF)] + padding + padding
    elif length == 2:
        return alphabet[(b0 >> 2) & 0x3F] + alphabet[((b0 << 4) & 0x30) + ((b1 >> 4) & 0xF)] + alphabet[((b1 << 2) & 0x3C) + ((b2 >> 6) & 0x3)] + padding
    else:
        return alphabet[(b0 >> 2) & 0x3F] + alphabet[((b0 << 4) & 0x30) + ((b1 >> 4) & 0xF)] + alphabet[((b1 << 2) & 0x3C) + ((b2 >> 6) & 0x3)] + alphabet[b2 & 0x3F]

def _base64_encode_core(arr, alphabet, padding):
    if not arr:
        return ""
    unsigned = [b & 0xFF for b in arr]
    result = []
    i = 0
    while i < len(unsigned):
        if i + 3 <= len(unsigned):
            result.append(_b64_encode_3to4(unsigned[i:i+3], alphabet, padding))
            i += 3
        else:
            result.append(_b64_encode_3to4(unsigned[i:], alphabet, padding))
            break
    return "".join(result)

def _base64_encode_private(arr):
    return _base64_encode_core(arr, PRIVATE_B64_ALPHABET, PRIVATE_B64_PADDING)

def _xor_encode(key, data):
    data_bytes = _string_to_bytes(data)
    key_bytes = _string_to_bytes(key)
    return _base64_encode_core(_xors(data_bytes, key_bytes), BASE64_ALPHABET, BASE64_PADDING)

def _sub_bytes_block(block):
    return [_to_byte(SBOX_BYTES[16 * ((b >> 4) & 0xF) + (b & 0xF)]) for b in block]

def _shift_add(a, b):
    return _to_byte(a + b)

def _shifts(data, key):
    if not key:
        return data[:]
    return [_shift_add(data[i], key[i % len(key)]) for i in range(len(data))]

def _apply_round_key(block):
    rk = __ROUND_KEY__
    i = 0
    while i < len(rk):
        op_idx = _to_byte(int(rk[i:i+2], 16))
        arg = _to_byte(int(rk[i+2:i+4], 16))
        if op_idx == 0:
            if arg + 0x100 < 0:
                return []
        elif op_idx == 1:
            block = [_xor_byte(v, arg) for v in block]
        elif op_idx == 2:
            block = [_shift_add(v, arg) for v in block]
        elif op_idx == 3:
            nb = []
            for v in block:
                nb.append(_xor_byte(v, arg))
                arg = _to_byte(arg + 1)
            block = nb
        elif op_idx == 4:
            nb = []
            for v in block:
                nb.append(_shift_add(v, arg))
                arg = _to_byte(arg + 1)
            block = nb
        elif op_idx == 5:
            nb = []
            for v in block:
                nb.append(_xor_byte(v, arg))
                arg = _to_byte(arg - 1)
            block = nb
        elif op_idx == 6:
            nb = []
            for v in block:
                nb.append(_shift_add(v, arg))
                arg = _to_byte(arg - 1)
            block = nb
        i += 4
    return block

def _expand_key(key_bytes):
    if not key_bytes:
        return [0] * 64
    if len(key_bytes) >= 64:
        return key_bytes[:64]
    return [key_bytes[i % len(key_bytes)] for i in range(64)]

def _pad_pkcs7(data):
    if not data:
        return [0] * 64
    data_len = len(data)
    padding_len = (64 - (data_len % 64) - 4) if (data_len % 64) <= 60 else (128 - (data_len % 64) - 4)
    padded = [0] * (data_len + padding_len + 4)
    _copy_to_bytes(data, 0, padded, 0, data_len)
    _copy_to_bytes(_int_to_bytes(data_len), 0, padded, data_len + padding_len, 4)
    return padded

def _generate_random_iv():
    return [_to_byte(random.randint(0, 255)) for _ in range(4)]

def _generate_iv():
    seed_bytes = _string_to_bytes(__SEED_KEY__)
    random_bytes = _generate_random_iv()
    seed_bytes = _expand_key(seed_bytes)
    seed_bytes = _xors(seed_bytes, _expand_key(random_bytes))
    seed_bytes = _expand_key(seed_bytes)
    return seed_bytes, random_bytes

def _aes(input_str):
    data_bytes = _string_to_bytes(input_str)
    iv, raw_iv = _generate_iv()
    crc_bytes = _string_to_bytes(_gen_crc32(data_bytes))
    combined = data_bytes + crc_bytes
    padded = _pad_pkcs7(combined)
    n_blocks = len(padded) // 64
    result = raw_iv[:] + [0] * (n_blocks * 64)
    prev_block = iv[:]
    for blk_idx in range(n_blocks):
        block = padded[blk_idx * 64:(blk_idx + 1) * 64]
        block = _xors(_apply_round_key(block), iv)
        block = _shifts(block, prev_block)
        block = _xors(block, prev_block)
        prev_block = _sub_bytes_block(_sub_bytes_block(block))
        _copy_to_bytes(prev_block, 0, result, 64 * blk_idx + 4, 64)
    return _base64_encode_private(result)

def _generate_uuid(length=32):
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "".join(random.choice(chars) for _ in range(length))

def _generate_cb():
    rand_str = _generate_uuid(32)
    chars = list(rand_str)
    for i, pos in enumerate(CB_POS):
        if pos < len(chars):
            chars[pos] = CB_CODE[i]
    return _aes("".join(chars))

def _sample_arr(arr, target_len):
    n = len(arr)
    if n <= target_len:
        return arr
    result = []
    for i in range(n):
        if i >= (len(result) * (n - 1)) / (target_len - 1):
            result.append(arr[i])
    return result

def _unique_2d_array(arr, col_idx=0):
    seen = set()
    result = []
    for row in arr:
        key = row[col_idx] if col_idx < len(row) else None
        if key is not None and key not in seen:
            seen.add(key)
            result.append(row)
    return result

def generate_track(distance, duration_ms=800):
    points = []
    current = 0.0
    elapsed = 0
    mid = distance * random.uniform(0.50, 0.60)
    while current < mid:
        a = 2.0 + random.uniform(0, 3.0) * (current / max(mid, 1))
        current += a + random.uniform(0, 1)
        if current >= mid:
            current = mid
        y = random.gauss(0, 0.8) + math.sin(current / 25) * 2
        elapsed += random.randint(6, 12)
        points.append((current, y, elapsed, 1))
    fast = distance * random.uniform(0.88, 0.94)
    while current < fast:
        v = 4.0 + random.uniform(0, 4) * (1 - (current - mid) / max(fast - mid, 1))
        current += v + random.uniform(-0.3, 0.8)
        if current >= fast:
            current = fast
        y = random.gauss(0, 0.6) + math.sin(current / 18) * 1.2
        elapsed += random.randint(7, 14)
        points.append((current, y, elapsed, 1))
    while current < distance - 3:
        v = max(0.3, 2.5 * (distance - current) / max(distance, 1))
        current += v + random.uniform(-0.3, 0.2)
        if current >= distance - 3:
            current = distance - 3
        y = random.gauss(0, 0.5) + math.sin(current / 12) * 1.0
        elapsed += random.randint(15, 30)
        points.append((current, y, elapsed, 1))
    while current < distance:
        current += random.uniform(0.2, 0.5)
        if current > distance:
            current = distance
        y = random.gauss(0, 0.3)
        elapsed += random.randint(20, 45)
        points.append((current, y, elapsed, 1))
    if points[-1][0] != distance:
        elapsed += random.randint(5, 10)
        points.append((distance, 0, elapsed, 1))
    return points

def build_slider_data(token, trace_points, slider_left, slider_width):
    trace_data = [_xor_encode(token, f"{round(p[0])},{round(p[1])},{p[2]},{p[3]}") for p in trace_points]
    atom_trace_data = _unique_2d_array(trace_points, 2)
    timestamps = sorted(set(p[2] for p in atom_trace_data))
    position_pct = int(slider_left) / slider_width * 100
    return {
        "d": _aes(":".join(_sample_arr(trace_data, SAMPLE_NUM))),
        "m": "",
        "p": _aes(_xor_encode(token, str(position_pct))),
        "f": _aes(_xor_encode(token, ",".join(str(t) for t in timestamps))),
        "ext": _aes(_xor_encode(token, f"1,{len(trace_data)}")),
    }

def find_gap_position(bg, front):
    front_rgb = front[:, :, :3]
    alpha = front[:, :, 3]
    _, mask = cv2.threshold(alpha, 50, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        x_ct, y_ct, w_ct, h_ct = cv2.boundingRect(contours[0])
    else:
        x_ct, y_ct, w_ct, h_ct = 0, 0, front.shape[1], front.shape[0]
    front_crop = front_rgb[y_ct:y_ct + h_ct, x_ct:x_ct + w_ct]
    mask_crop = mask[y_ct:y_ct + h_ct, x_ct:x_ct + w_ct]
    results = []
    for name, method in [("CCOEFF_normed", cv2.TM_CCOEFF_NORMED), ("CCORR_normed", cv2.TM_CCORR_NORMED)]:
        try:
            r = cv2.matchTemplate(bg, front_crop, method, mask=mask_crop)
            _, _, _, loc = cv2.minMaxLoc(r)
            results.append(loc[0] + x_ct)
        except:
            pass
    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    fg_gray = cv2.cvtColor(front_rgb, cv2.COLOR_BGR2GRAY)
    bg_canny = cv2.Canny(bg_gray, 50, 150)
    fg_canny = cv2.Canny(fg_gray, 50, 150)
    mask_eroded = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=1)
    fg_canny_masked = cv2.bitwise_and(fg_canny, fg_canny, mask=mask_eroded)
    try:
        r = cv2.matchTemplate(bg_canny, fg_canny_masked, cv2.TM_CCOEFF_NORMED)
        _, _, _, loc = cv2.minMaxLoc(r)
        results.append(loc[0])
    except:
        pass
    bg_sobel = np.abs(cv2.Sobel(bg_gray, cv2.CV_64F, 1, 0, ksize=3)).astype(np.uint8)
    fg_sobel = np.abs(cv2.Sobel(fg_gray, cv2.CV_64F, 1, 0, ksize=3)).astype(np.uint8)
    try:
        r = cv2.matchTemplate(bg_sobel, fg_sobel, cv2.TM_CCOEFF_NORMED, mask=mask)
        _, _, _, loc = cv2.minMaxLoc(r)
        results.append(loc[0])
    except:
        pass
    if not results:
        return 0.0
    median = sorted(results)[len(results) // 2]
    inliers = [x for x in results if abs(x - median) <= 15]
    if len(inliers) >= 2:
        return float(sorted(inliers)[len(inliers) // 2])
    return float(median)

DUN163_DOMAINS = [
    "https://c.dun.163.com",
    "https://c.dun.163yun.com"
]

def rotate_about_center(src, angle, scale=1.):
    try:
        w = src.shape[1]
        h = src.shape[0]
        rangle = np.deg2rad(angle)
        nw = (abs(np.sin(rangle)*h) + abs(np.cos(rangle)*w))*scale
        nh = (abs(np.cos(rangle)*h) + abs(np.sin(rangle)*w))*scale
        rot_mat = cv2.getRotationMatrix2D((nw*0.5, nh*0.5), angle, scale)
        rot_move = np.dot(rot_mat, np.array([(nw-w)*0.5, (nh-h)*0.5,0]))
        rot_mat[0,2] += rot_move[0]
        rot_mat[1,2] += rot_move[1]
        return cv2.warpAffine(src, rot_mat, (int(math.ceil(nw)), int(math.ceil(nh))), flags=cv2.INTER_LINEAR)
    except:
        return src

def parse_y_pred(ypred, anchors, class_types, islist=False, threshold=0.2, nms_threshold=0):
    try:
        if not anchors or not class_types:
            return [] if islist else None
        ceillen = 5 + len(class_types)
        sigmoid = lambda x: 1/(1+math.exp(-x))
        infos = []
        for idx in range(min(len(anchors), 3)):
            try:
                tensor_idx = 4 + idx * ceillen
                if tensor_idx >= ypred.shape[3]:
                    continue
                if USE_CUDA:
                    a = ypred[:,:,:,tensor_idx].cpu().detach().numpy()
                else:
                    a = ypred[:,:,:,tensor_idx].detach().numpy()
                for ii, i in enumerate(a[0]):
                    for jj, j in enumerate(i):
                        infos.append((ii, jj, idx, sigmoid(j)))
            except:
                continue
        if not infos:
            return [] if islist else None
        infos = sorted(infos, key=lambda i: -i[3])
        def get_xyxy_clz_con_emergency(info):
            try:
                gap = 416/ypred.shape[1]
                x, y, idx, con = info
                if idx >= len(anchors):
                    return None
                gp = idx * ceillen
                if (gp + 5 + len(class_types)) > ypred.shape[3]:
                    return None
                contain = torch.sigmoid(ypred[0, x, y, gp+4])
                pred_xy = torch.sigmoid(ypred[0, x, y, gp+0:gp+2])
                pred_wh = ypred[0, x, y, gp+2:gp+4]
                pred_clz = ypred[0, x, y, gp+5:gp+5+len(class_types)]
                if USE_CUDA:
                    pred_xy = pred_xy.cpu().detach().numpy()
                    pred_wh = pred_wh.cpu().detach().numpy()
                    pred_clz = pred_clz.cpu().detach().numpy()
                else:
                    pred_xy = pred_xy.detach().numpy()
                    pred_wh = pred_wh.detach().numpy()
                    pred_clz = pred_clz.detach().numpy()
                exp = math.exp
                cx, cy = float(pred_xy[0]), float(pred_xy[1])
                rx, ry = (cx + x)*gap, (cy + y)*gap
                rw, rh = float(pred_wh[0]), float(pred_wh[1])
                rw, rh = exp(rw)*anchors[idx][0], exp(rh)*anchors[idx][1]
                clz_ = [float(x) for x in pred_clz]
                xx = rx - rw/2
                _x = rx + rw/2
                yy = ry - rh/2
                _y = ry + rh/2
                if USE_CUDA:
                    log_cons = torch.sigmoid(ypred[:,:,:,gp+4]).cpu().detach().numpy()
                else:
                    log_cons = torch.sigmoid(ypred[:,:,:,gp+4]).detach().numpy()
                log_cons = np.transpose(log_cons, (0, 2, 1))
                clz = 'unknown'
                if clz_:
                    max_val = max(clz_)
                    max_idx = clz_.index(max_val)
                    for key, value in class_types.items():
                        if value == max_idx:
                            clz = key
                            break
                return [xx, yy, _x, _y], clz, con, log_cons
            except:
                return None
        if islist:
            limited_infos = infos[:min(50, len(infos))]
            v = []
            for i in limited_infos:
                if i[3] > threshold:
                    result = get_xyxy_clz_con_emergency(i)
                    if result is not None:
                        v.append(result)
            return v
        else:
            if infos:
                return get_xyxy_clz_con_emergency(infos[0])
            return None
    except Exception as e:
        return [] if islist else None

class Mini(nn.Module):
    class ConvBN(nn.Module):
        def __init__(self, cin, cout, kernel_size=3, stride=1, padding=None):
            super().__init__()
            padding = (kernel_size - 1) // 2 if not padding else padding
            self.conv = nn.Conv2d(cin, cout, kernel_size, stride, padding, bias=False)
            self.bn = nn.BatchNorm2d(cout, momentum=0.01)
            self.relu = nn.LeakyReLU(0.1, inplace=True)
        def forward(self, x):
            return self.relu(self.bn(self.conv(x)))

    def __init__(self, anchors, class_types, inchennel=3):
        super().__init__()
        self.oceil = len(anchors) * (5 + len(class_types))
        self.model = nn.Sequential(
            OrderedDict([
                ('ConvBN_0', self.ConvBN(inchennel, 32)),
                ('Pool_0', nn.MaxPool2d(2, 2)),
                ('ConvBN_1', self.ConvBN(32, 48)),
                ('Pool_1', nn.MaxPool2d(2, 2)),
                ('ConvBN_2', self.ConvBN(48, 64)),
                ('Pool_2', nn.MaxPool2d(2, 2)),
                ('ConvBN_3', self.ConvBN(64, 80)),
                ('Pool_3', nn.MaxPool2d(2, 2)),
                ('ConvBN_4', self.ConvBN(80, 96)),
                ('Pool_4', nn.MaxPool2d(2, 2)),
                ('ConvBN_5', self.ConvBN(96, 102)),
                ('ConvEND', nn.Conv2d(102, self.oceil, 1)),
            ])
        )

    def forward(self, x):
        return self.model(x).permute(0, 2, 3, 1)

def get_clz_rect_from_image(image_data, state, threshold=0.15):
    try:
        if not state or 'net' not in state:
            return [], None
        net = state['net']
        anchors = state.get('anchors', [])
        class_types = state.get('class_types', {})
        if not anchors or not class_types:
            return [], None
        image_array = np.frombuffer(image_data, dtype=np.uint8)
        npimg = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        if npimg is None:
            return [], None
        height, width = npimg.shape[:2]
        npimg_rgb = cv2.cvtColor(npimg, cv2.COLOR_BGR2RGB)
        npimg_resized = cv2.resize(npimg_rgb, (416, 416), interpolation=cv2.INTER_LINEAR)
        npimg_ = np.transpose(npimg_resized, (2,1,0))
        with torch.no_grad():
            input_tensor = torch.FloatTensor(npimg_).unsqueeze(0).to(DEVICE)
            if USE_CUDA:
                input_tensor = input_tensor.half()
            y_pred = net(input_tensor)
        v = parse_y_pred(y_pred, anchors, class_types, islist=True, threshold=threshold, nms_threshold=0.4)
        ret = []
        for i in v:
            if len(i) >= 4:
                rect, clz, con, log_cons = i[0], i[1], i[2], i[3]
                rw, rh = width/416, height/416
                scaled = [
                    int(rect[0] * rw),
                    int(rect[1] * rh),
                    int(rect[2] * rw),
                    int(rect[3] * rh)
                ]
                scaled[0] = clamp(scaled[0], 0, max(0, width - 1))
                scaled[1] = clamp(scaled[1], 0, max(0, height - 1))
                scaled[2] = clamp(scaled[2], 0, max(0, width - 1))
                scaled[3] = clamp(scaled[3], 0, max(0, height - 1))
                if scaled[2] > scaled[0] and scaled[3] > scaled[1]:
                    ret.append({"clz": clz, "rect": scaled, "conf": float(con)})
        ret = dedupe_rects(ret)
        if len(ret) < 3 and threshold > 0.08:
            return get_clz_rect_from_image(image_data, state, threshold=0.08)
        return ret, npimg
    except Exception as e:
        return [], None

def get_cut_img(npimg, rects):
    ret = []
    try:
        for item in rects:
            if len(item) >= 2:
                clz = item.get('clz') if isinstance(item, dict) else item[0]
                rect = item.get('rect') if isinstance(item, dict) else item[1]
                if len(rect) >= 4:
                    x1, y1, x2, y2 = rect[0], rect[1], rect[2], rect[3]
                    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(npimg.shape[1], x2), min(npimg.shape[0], y2)
                    if x2 > x1 and y2 > y1:
                        ret.append([clz, npimg[y1:y2,x1:x2,:], (x1,y1,x2,y2)])
    except:
        pass
    return ret

def get_flags_rects_from_image(image_data, state):
    try:
        if state is None:
            return None, None, None
        image_array = np.frombuffer(image_data, dtype=np.uint8)
        s = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        if s is None or s.size == 0:
            return None, None, None
        height, width = s.shape[:2]
        if height < 200 or width < 84:
            return None, None, None
        try:
            end_height = min(height, s.shape[0])
            a = s[160:end_height, 0:min(22, width), :]
            b = s[160:end_height, 28:min(50, width), :]
            c = s[160:end_height, 56:min(78, width), :]
            if a.shape[0] < 40 or a.shape[1] < 20:
                return None, None, None
            a1 = a[40:min(60, a.shape[0]), :, :] if a.shape[0] > 40 else a
            a2 = a[0:min(20, a.shape[0]), :, :] if a.shape[0] > 0 else a
            b1 = b[40:min(60, b.shape[0]), :, :] if b.shape[0] > 40 else b
            b2 = b[0:min(20, b.shape[0]), :, :] if b.shape[0] > 0 else b
            c1 = c[40:min(60, c.shape[0]), :, :] if c.shape[0] > 40 else c
            c2 = c[0:min(20, c.shape[0]), :, :] if c.shape[0] > 0 else c
        except:
            return None, None, None
        def get_match_lens_emergency(i1, i2, ratio=0.78):
            try:
                if i1.size == 0 or i2.size == 0:
                    return 0
                i1 = cv2.resize(i1, (min(i1.shape[1]*4, 800), min(i1.shape[0]*4, 600)), interpolation=cv2.INTER_LINEAR)
                i2 = cv2.resize(i2, (min(i2.shape[1]*2, 400), min(i2.shape[0]*2, 300)), interpolation=cv2.INTER_LINEAR)
                sift = get_sift_detector()
                kp1, des1 = sift.detectAndCompute(i1, None)
                kp2, des2 = sift.detectAndCompute(i2, None)
                if des1 is None or des2 is None or len(des1) == 0 or len(des2) == 0:
                    return 0
                bf = cv2.BFMatcher()
                matches = bf.knnMatch(des1, des2, k=2)
                good = 0
                for match_pair in matches:
                    if len(match_pair) >= 2:
                        m, n = match_pair[0], match_pair[1]
                        if m.distance <= ratio * n.distance:
                            good += 1
                return good
            except:
                return 0
        def get_flag_rect_emergency(k12, cut_imgs, st):
            try:
                if len(k12) < 2:
                    return []
                k1, k2 = k12[0], k12[1]
                r = []
                for item in cut_imgs:
                    if len(item) >= 3:
                        clz, npimg, rect = item[0], item[1], item[2]
                        if clz == '1':
                            r1 = get_match_lens_emergency(k1, npimg)
                            r.append([r1, rect, st])
                        elif clz == '2':
                            r2 = get_match_lens_emergency(k2, npimg)
                            r.append([r2, rect, st])
                return sorted(r, key=lambda i: i[0]) if r else []
            except:
                return []
        def try_detection(img_data):
            rects, processed_img = get_clz_rect_from_image(img_data, state)
            if not rects:
                return None, None, None
            v = get_cut_img(s, rects)
            if len(v) == 0:
                return None, None, None
            rs1 = get_flag_rect_emergency([a1, a2], v, 1)
            rs2 = get_flag_rect_emergency([b1, b2], v, 2)
            rs3 = get_flag_rect_emergency([c1, c2], v, 3)
            rs = rs1 + rs2 + rs3
            if len(rs) < 3:
                return None, None, None
            r = []
            for target_type in [1, 2, 3]:
                candidates = [x for x in rs if len(x) >= 3 and x[2] == target_type]
                if candidates:
                    best = max(candidates, key=lambda x: x[0])
                    r.append(best)
            if len(r) >= 3:
                r = sorted(r[:3], key=lambda x: x[2])
                return r[0][1], r[1][1], r[2][1]
            return None, None, None
        try:
            return try_detection(image_data)
        except:
            return None, None, None
    except Exception as e:
        return None, None, None

class Dun163:
    def __init__(self, id_, *, referer, fp_h, ua, thread_id, domain=None):
        self.fp = None
        self.resp_json2 = None
        self.domain = domain if domain else random.choice(DUN163_DOMAINS)
        self.thread_id = thread_id
        self._current_image_data = None
        self._current_rects = None
        self._current_click_points = None
        self.request_params = {
            'id': id_,
            'referer': referer,
            'fp_h': fp_h,
            'ua': ua
        }
        self.ss = self.set_session()
        self.ctx = get_compiled_js('dun163.js')

    def set_session(self):
        session = requests.Session()
        domain_host = self.domain.replace('https://', '').replace('http://', '')
        session.headers.update({
            "Accept": "*/*",
            "Accept-Language": "*",
            "Accept-Encoding": "*",
            "Accept-Post": "*/*",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": self.request_params['referer'],
            "User-Agent": self.request_params['ua'],
            "Host": domain_host,
            "X-Forwarded-For": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}",
            "X-Real-IP": f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}"
        })
        session.timeout = (1, 3)
        return session

    @staticmethod
    @lru_cache(maxsize=1000)
    def get_jsonp(text):
        try:
            jsonp_str = re.search(r"\((.*)\)", text, re.S)
            if jsonp_str:
                return json.loads(jsonp_str.group(1))
            return {}
        except:
            return {}

    @staticmethod
    def random_jsonp_str():
        s = string.ascii_lowercase + string.digits
        text = ''.join(random.choices(s, k=7))
        return "__JSONP_" + text + '_'

    def request_getconf(self):
        try:
            url = self.domain + '/api/v2/getconf'
            params = {
                "referer": self.request_params['referer'],
                "zoneId": "",
                "dt": "",
                "id": self.request_params['id'],
                "ipv6": "false",
                "runEnv": "10",
                "iv": "5",
                "loadVersion": "2.5.3",
                "lang": "en-US",
                "callback": self.random_jsonp_str() + '0'
            }
            response = self.ss.get(url, params=params)
            response.raise_for_status()
            resp_json = self.get_jsonp(response.text)
            return resp_json.get('data', {})
        except:
            return {}

    def request_get(self, dt, bid, ac_token, ir_token=None):
        try:
            url = self.domain + '/api/v3/get'
            fp = self.ctx.call('get_fp', self.request_params['fp_h'], self.request_params['ua'])
            cb = self.ctx.call('get_cb')
            self.fp = fp
            params = {
                "referer": self.request_params['referer'],
                "zoneId": "CN31",
                "dt": dt,
                "id": bid,
                "fp": fp,
                "https": "true",
                "type": "",
                "version": "2.28.5",
                "dpr": "1",
                "dev": "1",
                "cb": cb,
                "ipv6": "false",
                "runEnv": "10",
                "group": "",
                "scene": "",
                "lang": "en-US",
                "sdkVersion": "",
                "loadVersion": "2.5.3",
                "iv": "4",
                "user": "",
                "width": "320",
                "audio": "false",
                "sizeType": "10",
                "smsVersion": "v3",
                "token": "",
                "callback": self.random_jsonp_str() + '0'
            }
            if ir_token:
                params["irToken"] = ir_token
            resp_text = self.ss.get(url, params=params).text
            resp_json = self.get_jsonp(resp_text)
            return resp_json.get('data', {})
        except:
            return {}

    def request_check(self, dt, bid, *, token, captcha_type=7, click_data=None, slider_data=None):
        try:
            url = self.domain + '/api/v3/check'
            js_start_time = time.time()
            if captcha_type == 7 and click_data:
                check_data = self.ctx.call('get_click_check_data', click_data, token)
            elif captcha_type == 2 and slider_data:
                check_data = json.dumps(slider_data, separators=(',', ':'), ensure_ascii=True)
            else:
                check_data = '{"d":"","m":"","p":"","ext":""}'
            cb = self.ctx.call('get_cb')
            js_time = time.time() - js_start_time
            params = {
                "referer": self.request_params['referer'],
                "zoneId": "CN31",
                "dt": dt,
                "id": bid,
                "token": token,
                "data": check_data,
                "width": "320",
                "type": str(captcha_type),
                "version": "2.28.5",
                "cb": cb,
                "user": "",
                "extraData": "",
                "bf": "0",
                "runEnv": "10",
                "sdkVersion": "",
                "loadVersion": "2.5.3",
                "iv": "4",
                "callback": self.random_jsonp_str() + '1'
            }
            resp = self.ss.get(url, params=params)
            resp_json = self.get_jsonp(resp.text)
            return resp_json.get('data', {}), js_time
        except:
            return {}, 0.0

    def handle_click_captcha_hybrid(self, bg_url, token):
        try:
            headers = {"User-Agent": self.request_params['ua']}
            resp = requests.get(bg_url, headers=headers, timeout=3)
            resp.raise_for_status()
            image_data = resp.content
            img_start_time = time.time()
            state = get_global_model()
            if not state:
                return self.generate_emergency_clicks(), 0.0
            rects = get_flags_rects_from_image(image_data, state)
            img_time = time.time() - img_start_time
            self._current_image_data = image_data
            self._current_rects = rects
            rect1 = safe_list_access(rects, 0)
            rect2 = safe_list_access(rects, 1)
            rect3 = safe_list_access(rects, 2)
            if rect1 is not None and rect2 is not None and rect3 is not None:
                click_points = [build_click_point_from_rect(rect) for rect in [rect1, rect2, rect3] if rect and len(rect) >= 4]
                if len(click_points) >= 3:
                    self._current_click_points = click_points[:3]
                    return click_points[:3], img_time
            fallback_points = self.generate_emergency_clicks()
            partial_rects = [rect for rect in [rect1, rect2, rect3] if rect is not None and len(rect) >= 4]
            click_points = merge_detected_with_fallback(partial_rects, fallback_points) if partial_rects else fallback_points
            self._current_click_points = click_points
            return click_points, img_time
        except Exception as e:
            return self.generate_emergency_clicks(), 0.0

    def generate_emergency_clicks(self):
        try:
            patterns = [
                [(80, 70), (160, 120), (240, 90)],
                [(70, 100), (160, 95), (250, 105)],
                [(160, 60), (110, 130), (210, 140)],
                [(90, 80), (170, 110), (230, 100)],
                [(75, 95), (155, 85), (245, 110)],
                [(100, 75), (180, 130), (220, 85)],
                [(85, 110), (150, 70), (260, 120)],
                [(120, 85), (200, 105), (240, 130)],
                [(65, 80), (145, 115), (255, 95)],
                [(110, 100), (175, 65), (235, 115)],
                [(95, 65), (165, 135), (225, 105)],
                [(130, 90), (190, 75), (250, 125)],
            ]
            selected_pattern = random.choice(patterns)
            angle = math.radians(random.uniform(-3, 3))
            cx, cy = 160, 95
            click_points = []
            for x, y in selected_pattern:
                rx = cx + (x - cx) * math.cos(angle) - (y - cy) * math.sin(angle)
                ry = cy + (x - cx) * math.sin(angle) + (y - cy) * math.cos(angle)
                offset_x = int(random.gauss(0, 8))
                offset_y = int(random.gauss(0, 8))
                final_x = max(10, min(int(rx) + offset_x, 310))
                final_y = max(10, min(int(ry) + offset_y, 190))
                click_points.append({"x": final_x, "y": final_y})
            return click_points
        except:
            return [{"x": 80, "y": 70}, {"x": 160, "y": 120}, {"x": 240, "y": 90}]

    def handle_slider_captcha(self, bg_url, front_url, token):
        try:
            headers = {"User-Agent": self.request_params['ua']}
            resp_bg = requests.get(bg_url, headers=headers, timeout=3)
            resp_bg.raise_for_status()
            bg_array = np.frombuffer(resp_bg.content, dtype=np.uint8)
            bg = cv2.imdecode(bg_array, cv2.IMREAD_COLOR)
            resp_front = requests.get(front_url, headers=headers, timeout=3)
            resp_front.raise_for_status()
            front_array = np.frombuffer(resp_front.content, dtype=np.uint8)
            front = cv2.imdecode(front_array, cv2.IMREAD_UNCHANGED)
            if bg is None or front is None:
                return None
            gap_x = find_gap_position(bg, front)
            bg_width = bg.shape[1]
            slider_distance = gap_x * CONTROL_WIDTH / bg_width
            slider_distance += random.uniform(-1, 2)
            trace_points = generate_track(slider_distance, duration_ms=random.randint(600, 1200))
            slider_data = build_slider_data(token, trace_points, slider_distance, CONTROL_WIDTH)
            return slider_data
        except Exception as e:
            return None

    def save_token_locally(self, validate_token):
        try:
            line = f"{validate_token}\n"
            with file_lock:
                with open(TOKEN_OUTPUT_FILE, 'a') as f:
                    f.write(line)
            return True
        except:
            return False

    def run(self):
        try:
            get_conf_data = self.request_getconf()
            if not get_conf_data:
                return False
            dt = get_conf_data.get('dt')
            ac_data = get_conf_data.get('ac', {})
            ac_token = ac_data.get('token')
            bid = ac_data.get('bid')
            ir_data = get_conf_data.get('ir', {})
            ir_token = ir_data.get('token') if ir_data.get('enable') else None
            get_data = self.request_get(dt, bid, ac_token, ir_token)
            if not get_data:
                return False
            captcha_type = get_data.get('type', 7)
            token = get_data.get('token')
            if not token:
                return False
            bg_urls = get_data.get('bg', [])
            if not bg_urls:
                return False
            if captcha_type == 2:
                front_urls = get_data.get('front', [])
                if not front_urls:
                    return False
                slider_data = self.handle_slider_captcha(bg_urls[0], front_urls[0], token)
                if slider_data is None:
                    return False
                resp_json, js_time = self.request_check(dt, bid, token=token, captcha_type=2, slider_data=slider_data)
            elif captcha_type == 7:
                click_points, img_time = self.handle_click_captcha_hybrid(bg_urls[0], token)
                resp_json, js_time = self.request_check(dt, bid, token=token, captcha_type=7, click_data=click_points)
            else:
                return False
            self.resp_json2 = resp_json
            if resp_json.get('result') == True:
                validate_raw = resp_json.get('validate', '')
                validate_decoded = ""
                if validate_raw and self.ctx:
                    try:
                        validate_decoded = self.ctx.call('do_onVerify', validate_raw, self.fp)
                    except:
                        return False
                if validate_decoded and len(validate_decoded.strip()) > 10:
                    server_success = send_token_to_server(validate_decoded)
                    if server_success:
                        logger.success(f'T-{self.thread_id} SUCCESS: {validate_decoded[:40]}... | Sent to server')
                    else:
                        self.save_token_locally(validate_decoded)
                        logger.success(f'T-{self.thread_id} SUCCESS: {validate_decoded[:40]}... | Saved locally')
                    return True
            return False
        except:
            try:
                self.ss = self.set_session()
            except:
                pass
            return False

def worker_thread(thread_id, config):
    d = None
    cycle = 0
    while True:
        try:
            if d is None or cycle >= 3:
                d = None
                config['UA'] = UserAgent().random
                config['DOMAIN'] = random.choice(DUN163_DOMAINS)
                d = Dun163(
                    id_=config['ID_'],
                    referer=config['REFERER'],
                    fp_h=config['FP_H'],
                    ua=config['UA'],
                    thread_id=thread_id,
                    domain=config['DOMAIN']
                )
                cycle = 0
            success = d.run()
            cycle += 1
            if success:
                logger.success(f"T-{thread_id} | Success")
                cycle = 3
        except Exception as e:
            logger.error(f"T-{thread_id} | Worker crashed: {e}")
            d = None
            continue

def main():
    logger.info("Starting CN31 Solver...")
    model_state = initialize_global_model()
    if not model_state:
        logger.error("Model not available - cannot continue")
        return
    js_ctx = get_compiled_js('dun163.js')
    if not js_ctx:
        logger.error("JavaScript not available - cannot continue")
        return
    logger.success("All resources loaded")
    config = {
        'ID_': ID,
        'REFERER': REFERER,
        'FP_H': FP_H,
        'UA': UserAgent().random,
        'DOMAIN': DUN163_DOMAINS[0]
    }
    NUM_THREADS = int(os.getenv("THREADS", "10"))
    logger.info(f"Starting {NUM_THREADS} worker threads")
    logger.info(f"ID: {ID}")
    logger.info(f"REFERER: {REFERER}")
    logger.info(f"Server URL: {TOKEN_SERVER_URL}")
    logger.info("-" * 50)
    while True:
        try:
            with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
                futures = []
                for i in range(NUM_THREADS):
                    thread_config = config.copy()
                    thread_config['UA'] = UserAgent().random
                    thread_config['DOMAIN'] = DUN163_DOMAINS[i % len(DUN163_DOMAINS)]
                    future = executor.submit(worker_thread, i+1, thread_config)
                    futures.append(future)
                for future in futures:
                    future.result()
        except KeyboardInterrupt:
            logger.warning("Stopping...")
            executor.shutdown(wait=False)
            break
        except Exception as e:
            logger.error(f"Main loop crashed: {e}")
            continue

if __name__ == '__main__':
    main()
