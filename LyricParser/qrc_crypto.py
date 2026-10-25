"""QRC 加解密：3DES-EDE（QQ 音乐私有密钥）+ zlib 压缩。

3 组 DES 密钥按 EDE 模式组合，加密数据为 zlib 压缩后按 8 字节对齐的密文。
"""
import zlib

# 3 组私有 DES 密钥
KEY_1 = b"!@#)(*$%"
KEY_2 = b"123ZXC!@"
KEY_3 = b"!@#)(NHL"

# DES 初始置换（IP）表
IP: list[int] = [58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
                 62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
                 57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
                 61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7]
# DES 末置换（FP，IP 的逆）
FP: list[int] = [40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
                 38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
                 36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
                 34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25]
# 扩展置换（E）表：32bit -> 48bit
E: list[int] = [32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13,
                12, 13, 14, 15, 16, 17, 16, 17, 18, 19, 20, 21, 20, 21, 22,
                23, 24, 25, 24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1]
# 轮函数输出置换（P）表
P: list[int] = [16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
                2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25]
# 密钥编排的 PC-1 置换表：64bit -> 56bit
PC1: list[int] = [57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18, 10, 2,
                  59, 51, 43, 35, 27, 19, 11, 3, 60, 52, 44, 36, 63, 55, 47, 39,
                  31, 23, 15, 7, 62, 54, 46, 38, 30, 22, 14, 6, 61, 53, 45, 37,
                  29, 21, 13, 5, 28, 20, 12, 4]
# 密钥编排的 PC-2 置换表：56bit -> 48bit
PC2: list[int] = [14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10, 23, 19, 12, 4,
                  26, 8, 16, 7, 27, 20, 13, 2, 41, 52, 31, 37, 47, 55, 30, 40,
                  51, 45, 33, 48, 44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32]
# 每轮密钥编排的循环左移位数
SHIFT: list[int] = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]

# 8 个 S 盒的扁平化列表（每个 S 盒 4 行 x 16 列 = 64 项）
_S_FLAT: list[list[int]] = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
     0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
     15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
     3, 13, 4, 7, 15, 2, 8, 15, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
     13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
     13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
     1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
     13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
     3, 15, 0, 6, 10, 10, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
     14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
     11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
     10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
     4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
     13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
     6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
     1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
     2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]
# 8 个 S 盒，每个为 4x16 的二维表
S_BOXES: list[list[list[int]]] = [
    [box[i * 16:(i + 1) * 16] for i in range(4)] for box in _S_FLAT
]

# 类型别名
Bits = list[int]
KeySchedule = list[Bits]


def _bytes_to_bits(b: bytes) -> Bits:
    """将字节串转换为 bit 列表（每个 bit 用 0/1 表示，MSB 在前）。"""
    return [int(c) for byte in b for c in f"{byte:08b}"]


def _bits_to_bytes(bits: Bits) -> bytes:
    """将 bit 列表转换为字节串（要求长度为 8 的倍数）。"""
    return bytes(int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8))


def _permute(bits: Bits, table: list[int]) -> Bits:
    """按置换表重排 bit。

    置换表中使用 1-based 索引（DES 规范），因此读取时需 ``t - 1``。
    """
    return [bits[t - 1] for t in table]


def _key_schedule(key: bytes) -> KeySchedule:
    """由 8 字节密钥生成 16 轮 48bit 子密钥。

    Args:
        key: 8 字节 DES 密钥。

    Returns:
        长度为 16 的子密钥列表，每个子密钥为 48bit。
    """
    bits = _permute(_bytes_to_bits(key), PC1)
    c, d = bits[:28], bits[28:]
    keys: KeySchedule = []
    for shift in SHIFT:
        c = c[shift:] + c[:shift]
        d = d[shift:] + d[:shift]
        keys.append(_permute(c + d, PC2))
    return keys


def _feistel(r_bits: Bits, subkey: Bits) -> Bits:
    """DES 轮函数 F(R, K)：扩展 -> XOR 子密钥 -> S 盒 -> P 置换。

    Args:
        r_bits: 右半部分 32bit。
        subkey: 48bit 子密钥。

    Returns:
        32bit 输出。
    """
    xored = [a ^ b for a, b in zip(_permute(r_bits, E), subkey)]
    s_out: Bits = []
    for i in range(8):
        chunk = xored[i * 6:(i + 1) * 6]
        row = (chunk[0] << 1) | chunk[5]
        col = (chunk[1] << 3) | (chunk[2] << 2) | (chunk[3] << 1) | chunk[4]
        s_out += [int(b) for b in f"{S_BOXES[i][row][col]:04b}"]
    return _permute(s_out, P)


def _des_block(block: bytes, keys: KeySchedule, decrypt: bool = False) -> bytes:
    """DES 加/解密一个 8 字节块。

    Args:
        block: 8 字节输入。
        keys: 由 :func:`_key_schedule` 生成的 16 轮子密钥。
        decrypt: True 时子密钥逆序使用。

    Returns:
        8 字节输出。
    """
    bits = _permute(_bytes_to_bits(block), IP)
    l, r = bits[:32], bits[32:]
    sched = list(reversed(keys)) if decrypt else keys
    for k in sched:
        l, r = r, [a ^ b for a, b in zip(l, _feistel(r, k))]
    return _bits_to_bytes(_permute(r + l, FP))


# 预先计算三组密钥的子密钥调度
_KEYS: list[KeySchedule] = [
    _key_schedule(KEY_1), _key_schedule(KEY_2), _key_schedule(KEY_3),
]


def _encrypt_block(block: bytes) -> bytes:
    """3DES-EDE 加密一个 8 字节块：``E_K1(D_K2(E_K3(P)))``。"""
    b = _des_block(block, _KEYS[0])
    b = _des_block(b, _KEYS[1], decrypt=True)
    return _des_block(b, _KEYS[2])


def _decrypt_block(block: bytes) -> bytes:
    """3DES-EDE 解密一个 8 字节块：``D_K3(E_K2(D_K1(C)))``。"""
    b = _des_block(block, _KEYS[2], decrypt=True)
    b = _des_block(b, _KEYS[1])
    return _des_block(b, _KEYS[0], decrypt=True)


def decrypt_qrc_hex(hex_str: str) -> str:
    """解密 QRC 加密数据（十六进制字符串）。

    Args:
        hex_str: 加密后的十六进制密文，长度须为 16 的倍数。

    Returns:
        解密后的 UTF-8 明文（已去除 BOM）。

    Raises:
        ValueError: 密文非法、长度不是 8 字节的倍数、zlib 解压失败，
            或解密结果不是合法的 UTF-8 文本。
    """
    try:
        data = bytes.fromhex(hex_str)
    except ValueError as e:
        raise ValueError(f"QRC 解密失败：无效的十六进制字符串（{e}）") from e
    if len(data) % 8 != 0:
        raise ValueError(f"QRC 解密失败：加密数据长度（{len(data)} 字节）不是 8 的倍数")
    decrypted = b"".join(_decrypt_block(data[i : i + 8]) for i in range(0, len(data), 8))
    try:
        decompressed = zlib.decompress(decrypted)
    except zlib.error as e:
        raise ValueError(f"QRC 解密失败：zlib 解压错误（{e}）") from e
    if decompressed[:3] == b"\xef\xbb\xbf":
        decompressed = decompressed[3:]
    try:
        return decompressed.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"QRC 解密失败：解密结果不是合法的 UTF-8 文本（{e}）") from e


def encrypt_qrc_hex(plaintext: str) -> str:
    """加密明文为 QRC 格式（十六进制密文）。

    Args:
        plaintext: UTF-8 明文字符串。

    Returns:
        十六进制密文字符串。
    """
    data = zlib.compress(plaintext.encode("utf-8"))
    if len(data) % 8 != 0:
        data += b"\x00" * (8 - len(data) % 8)
    encrypted = b"".join(_encrypt_block(data[i:i + 8]) for i in range(0, len(data), 8))
    return encrypted.hex()
