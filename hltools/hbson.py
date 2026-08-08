"""Reader for HBSON, the binary format Farever's level prefabs are stored in.

`Level/World/<world>.dat/gameplayData/*.prefab` inside res.map.pak are not JSON
— they are HBSON: a tagged tree with an interned string table. A naive byte
scan for `lootTable` finds only the FIRST occurrence of each string in a file;
every later use is a 4-byte back-reference, which is why counting placements
that way silently undercounts.

Wire format (derived from the bytes, then validated — see below):

    "HBSON\\x00" <value>

    value := <tag:u8> <payload>
        0x00 zero        0x01 u8        0x02 i32       0x03 f64
        0x04 false       0x05 true      0x06/0x07 null 0x0B empty
        0x08 object: <nfields:u8> then nfields * (<string> <value>)
        0x0A string
        0x0C array: <count:u8>  then count * value
        0x0D array: <count:i32> then count * value

    string := <header:u32le>
        header & 0x40000000 -> literal of (header & 0x3FFFFFFF) bytes, INTERNED
        header & 0x80000000 -> literal of that length, NOT interned
        otherwise            -> index into the interned table, in order seen

The 0x40-vs-0x80 distinction is the trap: interning both flavours still
consumes the file exactly, so the parse *looks* correct while every index
lands one or more slots off and object keys come back as values (keys like
"Z1_Primevalley_Island" instead of "z"). Validation is therefore two-part —
exact end-of-buffer consume on all 824 world prefabs, AND object keys that are
plausible field names.
"""
import struct


class Reader:
    def __init__(self, buf):
        self.b = buf
        self.i = 0
        self.strings = []

    def u8(self):
        v = self.b[self.i]
        self.i += 1
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.b, self.i)[0]
        self.i += 4
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.i)[0]
        self.i += 4
        return v

    def f64(self):
        v = struct.unpack_from("<d", self.b, self.i)[0]
        self.i += 8
        return v

    def string(self):
        h = self.u32()
        if h & 0xC0000000:
            n = h & 0x3FFFFFFF
            s = self.b[self.i:self.i + n].decode("utf-8", "replace")
            self.i += n
            if h & 0x40000000:
                self.strings.append(s)
            return s
        return self.strings[h]

    def value(self):
        t = self.u8()
        if t in _SINGLETON:
            return _SINGLETON[t]
        if t == 0x01:
            return self.u8()
        if t == 0x02:
            return self.i32()
        if t == 0x03:
            return self.f64()
        if t == 0x08:
            n = self.u8()
            return {self.string(): self.value() for _ in range(n)}
        if t == 0x0A:
            return self.string()
        if t == 0x0C:
            return [self.value() for _ in range(self.u8())]
        if t == 0x0D:
            return [self.value() for _ in range(self.i32())]
        raise ValueError(f"unknown tag 0x{t:02x} at {self.i - 1}")


_SINGLETON = {0x00: 0, 0x04: False, 0x05: True,
              0x06: None, 0x07: None, 0x0B: None}


def load(buf):
    """Parse an HBSON buffer. Returns (value, reader); reader.i must == len."""
    if buf[:6] != b"HBSON\x00":
        raise ValueError("not HBSON")
    r = Reader(buf)
    r.i = 6
    return r.value(), r


def load_strict(buf, what="<buffer>"):
    """Parse and refuse anything that does not consume the whole buffer."""
    v, r = load(buf)
    if r.i != len(buf):
        raise ValueError(f"{what}: consumed {r.i} of {len(buf)} bytes — the "
                         "grammar does not fit this file, do not trust it")
    return v
