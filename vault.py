# -*- coding: utf-8 -*-
"""암호화 보관 파일(.enc.json) 읽기·쓰기.

페이지(index.html)의 WebCrypto 복호화와 같은 규격: PBKDF2-SHA256(310,000회) → AES-256-GCM.
salt는 파일마다 같은 값을 쓰고(키 유도 1회로 전부 복호화), iv는 SHA256(salt+평문)에서
뽑는다 — 내용이 같으면 바이트가 같아 헛커밋이 생기지 않고, 내용이 바뀌면 iv도 바뀐다.
"""
import base64, hashlib, json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITER = 310_000
_keys = {}


def _key(password: str, salt: bytes) -> bytes:
    k = (password, salt)
    if k not in _keys:
        _keys[k] = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                              iterations=ITER).derive(password.encode())
    return _keys[k]


def seal(obj, password: str, salt_b64: str) -> str:
    salt = base64.b64decode(salt_b64)
    plain = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    iv = hashlib.sha256(salt + plain).digest()[:12]
    ct = AESGCM(_key(password, salt)).encrypt(iv, plain, None)
    return json.dumps({"v": 1, "it": ITER, "salt": salt_b64,
                       "iv": base64.b64encode(iv).decode(),
                       "ct": base64.b64encode(ct).decode()})


def unseal(text: str, password: str):
    v = json.loads(text)
    salt = base64.b64decode(v["salt"])
    plain = AESGCM(_key(password, salt)).decrypt(
        base64.b64decode(v["iv"]), base64.b64decode(v["ct"]), None)
    return json.loads(plain)
