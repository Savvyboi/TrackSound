"""A small, read-only 7z reader on top of the standard library's ``lzma``.

Fingrid publishes its 10 Hz frequency archive as monthly ``.7z`` files and the project does not
depend on a 7z library, so this module reads the subset of the format those files use (and most
7-Zip archives of plain files): LZMA, LZMA2, BZip2, Deflate or Copy coders, one coder per
folder, solid or not, with an optional LZMA-encoded header. Encryption, filters chains (BCJ,
Delta) and multi-volume archives are not supported and raise ``ValueError``.

The format is described in 7-Zip's ``DOC/7zFormat.txt``.
"""
from __future__ import annotations

import bz2
import lzma
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Iterator

SIGNATURE = b"7z\xbc\xaf\x27\x1c"

K_END, K_HEADER, K_ARCHIVE_PROPS, K_ADDITIONAL, K_MAIN_STREAMS, K_FILES = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
K_PACK_INFO, K_UNPACK_INFO, K_SUBSTREAMS, K_SIZE, K_CRC, K_FOLDER = 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B
K_CODERS_UNPACK_SIZE, K_NUM_UNPACK_STREAM, K_EMPTY_STREAM, K_NAME, K_ENCODED_HEADER = 0x0C, 0x0D, 0x0E, 0x11, 0x17
K_DUMMY = 0x19

CODER_COPY, CODER_LZMA, CODER_LZMA2 = b"\x00", b"\x03\x01\x01", b"\x21"
CODER_BZIP2, CODER_DEFLATE = b"\x04\x02\x02", b"\x04\x01\x08"


class _Reader:
    def __init__(self, data: bytes):
        self.b, self.pos = data, 0

    def byte(self) -> int:
        v = self.b[self.pos]
        self.pos += 1
        return v

    def take(self, n: int) -> bytes:
        v = self.b[self.pos:self.pos + n]
        if len(v) != n:
            raise ValueError("truncated 7z header")
        self.pos += n
        return v

    def number(self) -> int:
        """7z variable-length UINT64."""
        first, mask, value = self.byte(), 0x80, 0
        for i in range(8):
            if first & mask == 0:
                return value | ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def bits(self, n: int) -> list[bool]:
        out, mask, cur = [], 0, 0
        for _ in range(n):
            if mask == 0:
                cur, mask = self.byte(), 0x80
            out.append(bool(cur & mask))
            mask >>= 1
        return out

    def defined(self, n: int) -> list[bool]:
        return [True] * n if self.byte() else self.bits(n)


@dataclass
class _Folder:
    coder_id: bytes
    props: bytes
    unpack_size: int
    crc: int | None = None


@dataclass
class _Streams:
    pack_pos: int = 0
    pack_sizes: list[int] = field(default_factory=list)
    folders: list[_Folder] = field(default_factory=list)
    n_substreams: list[int] = field(default_factory=list)
    sizes: list[int] = field(default_factory=list)


@dataclass
class Entry:
    name: str
    size: int
    has_stream: bool


def _read_pack_info(r: _Reader, s: _Streams) -> None:
    s.pack_pos = r.number()
    n = r.number()
    while (t := r.byte()) != K_END:
        if t == K_SIZE:
            s.pack_sizes = [r.number() for _ in range(n)]
        elif t == K_CRC:
            for d in r.defined(n):
                if d:
                    r.u32()
        else:
            raise ValueError(f"unexpected 7z pack-info property {t}")


def _read_folder(r: _Reader) -> tuple[bytes, bytes, int]:
    n_coders = r.number()
    if n_coders != 1:
        raise ValueError("7z folders with filter chains are not supported")
    flags = r.byte()
    cid = r.take(flags & 0x0F)
    if flags & 0x10:
        if r.number() != 1 or r.number() != 1:
            raise ValueError("7z coders with several streams are not supported")
    props = r.take(r.number()) if flags & 0x20 else b""
    if flags & 0x80:
        raise ValueError("7z alternative coder methods are not supported")
    return cid, props, 1


def _read_unpack_info(r: _Reader, s: _Streams) -> None:
    if r.byte() != K_FOLDER:
        raise ValueError("bad 7z unpack info")
    n = r.number()
    if r.byte() != 0:
        raise ValueError("external 7z folders are not supported")
    raw = [_read_folder(r) for _ in range(n)]
    if r.byte() != K_CODERS_UNPACK_SIZE:
        raise ValueError("bad 7z unpack info")
    s.folders = [_Folder(cid, props, r.number()) for cid, props, _ in raw]
    while (t := r.byte()) != K_END:
        if t == K_CRC:
            for f, d in zip(s.folders, r.defined(n)):
                if d:
                    f.crc = r.u32()
        else:
            raise ValueError(f"unexpected 7z unpack-info property {t}")


def _read_substreams(r: _Reader, s: _Streams) -> None:
    nums = [1] * len(s.folders)
    t = r.byte()
    if t == K_NUM_UNPACK_STREAM:
        nums = [r.number() for _ in s.folders]
        t = r.byte()
    sizes: list[int] = []
    has_sizes = t == K_SIZE
    for f, n in zip(s.folders, nums):
        if n == 0:
            continue
        used = 0
        if has_sizes:
            for _ in range(n - 1):
                v = r.number()
                sizes.append(v)
                used += v
        sizes.append(f.unpack_size - used)
    if has_sizes:
        t = r.byte()
    while t != K_END:
        if t == K_CRC:
            n_unknown = sum(n for f, n in zip(s.folders, nums) if not (n == 1 and f.crc is not None))
            for d in r.defined(n_unknown):
                if d:
                    r.u32()
        else:
            raise ValueError(f"unexpected 7z substreams property {t}")
        t = r.byte()
    s.n_substreams, s.sizes = nums, sizes


def _read_streams(r: _Reader) -> _Streams:
    s = _Streams()
    while (t := r.byte()) != K_END:
        if t == K_PACK_INFO:
            _read_pack_info(r, s)
        elif t == K_UNPACK_INFO:
            _read_unpack_info(r, s)
        elif t == K_SUBSTREAMS:
            _read_substreams(r, s)
        else:
            raise ValueError(f"unexpected 7z streams property {t}")
    if not s.n_substreams:
        s.n_substreams = [1] * len(s.folders)
        s.sizes = [f.unpack_size for f in s.folders]
    return s


def _read_files(r: _Reader) -> list[tuple[str, bool]]:
    n = r.number()
    names, empty = [""] * n, [False] * n
    while (t := r.byte()) != K_END:
        size = r.number()
        data = _Reader(r.take(size))
        if t == K_EMPTY_STREAM:
            empty = data.bits(n)
        elif t == K_NAME:
            if data.byte() != 0:
                raise ValueError("external 7z names are not supported")
            names = data.b[data.pos:].decode("utf-16-le").split("\x00")[:n]
        # times, attributes, dummies: skipped
    return [(nm, not e) for nm, e in zip(names, empty)]


def _decompressor(f: _Folder):
    if f.coder_id == CODER_LZMA:
        p = f.props[0]
        lc, p = p % 9, p // 9
        lp, pb = p % 5, p // 5
        dict_size = struct.unpack("<I", f.props[1:5])[0]
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[
            {"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}])
    if f.coder_id == CODER_LZMA2:
        b = f.props[0]
        dict_size = 0xFFFFFFFF if b >= 40 else (2 | (b & 1)) << (b // 2 + 11)
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": dict_size}])
    if f.coder_id == CODER_BZIP2:
        return bz2.BZ2Decompressor()
    if f.coder_id == CODER_DEFLATE:
        return zlib.decompressobj(-15)
    raise ValueError(f"unsupported 7z compression method {f.coder_id.hex()}")


def _unpack(fh: BinaryIO, offset: int, packed: int, f: _Folder, chunk: int = 1 << 20) -> Iterator[bytes]:
    """Decompressed bytes of one folder, in pieces."""
    fh.seek(offset)
    left, produced = packed, 0
    if f.coder_id == CODER_COPY:
        while left > 0:
            d = fh.read(min(chunk, left))
            if not d:
                break
            left -= len(d)
            yield d
        return
    dec = _decompressor(f)
    is_lzma = isinstance(dec, lzma.LZMADecompressor)
    while produced < f.unpack_size:
        if is_lzma and not dec.needs_input:
            data = b""
        else:
            data = fh.read(min(chunk, left)) if left > 0 else b""
            left -= len(data)
            if not data and (not is_lzma or dec.needs_input):
                break
        out = dec.decompress(data, 1 << 22) if is_lzma else dec.decompress(data)
        if out:
            out = out[:f.unpack_size - produced]
            produced += len(out)
            yield out
    if produced != f.unpack_size:
        raise ValueError(f"7z data ends early ({produced} of {f.unpack_size} bytes)")


class SevenZip:
    """Iterate over the files of a 7z archive: ``for entry, data in SevenZip(path).files(): ...``."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        with open(self.path, "rb") as fh:
            head = fh.read(32)
            if head[:6] != SIGNATURE:
                raise ValueError("not a 7z archive")
            _, next_off, next_size, _ = struct.unpack("<IQQI", head[8:32])
            fh.seek(32 + next_off)
            r = _Reader(fh.read(next_size))
            t = r.byte()
            while t == K_ENCODED_HEADER:
                s = _read_streams(r)
                data = b"".join(_unpack(fh, 32 + s.pack_pos, s.pack_sizes[0], s.folders[0]))
                r = _Reader(data)
                t = r.byte()
        if t != K_HEADER:
            raise ValueError("bad 7z header")
        self._streams = _Streams()
        files: list[tuple[str, bool]] = []
        while (t := r.byte()) != K_END:
            if t in (K_ARCHIVE_PROPS, K_ADDITIONAL):
                raise ValueError("7z archive properties / additional streams are not supported")
            if t == K_MAIN_STREAMS:
                self._streams = _read_streams(r)
            elif t == K_FILES:
                files = _read_files(r)
        sizes = iter(self._streams.sizes)
        self.entries = [Entry(name, next(sizes) if has else 0, has) for name, has in files]

    def files(self) -> Iterator[tuple[Entry, bytes]]:
        """(entry, contents) for every file with data, in archive order."""
        s = self._streams
        with_data = [e for e in self.entries if e.has_stream]
        pos, k = 32 + s.pack_pos, 0
        with open(self.path, "rb") as fh:
            for fi, (folder, n) in enumerate(zip(s.folders, s.n_substreams)):
                gen = _unpack(fh, pos, s.pack_sizes[fi], folder)
                buf = bytearray()
                for _ in range(n):
                    e = with_data[k]
                    k += 1
                    while len(buf) < e.size:
                        try:
                            buf += next(gen)
                        except StopIteration:
                            raise ValueError("7z data ends early")
                    yield e, bytes(buf[:e.size])
                    del buf[:e.size]
                pos += s.pack_sizes[fi]
