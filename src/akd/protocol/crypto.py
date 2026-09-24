"""RSA/PKCS#1 v1.5 encryption of the four SOAP login fields (protocol map §2).

Pure: bytes and strings in, base64 string out. No I/O beyond reading the
shipped public.key on request. Mirrors CATALYST.Security.Encrypter.rsaEncrypt
(Cipher "RSA" == RSA/ECB/PKCS1Padding) + commons-codec Base64.

`public.key` is written by Java's `ObjectOutputStream` as two consecutive
serialized `java.math.BigInteger` objects (modulus, then exponent) — not
PEM/DER. `_JavaObjectStreamReader` below is a minimal reader for exactly the
subset of the Java Object Serialization Stream Protocol this file uses:
TC_OBJECT/TC_CLASSDESC/TC_REFERENCE/TC_ARRAY/TC_ENDBLOCKDATA, `int` fields,
and one `byte[]` field. It is not a general-purpose deserializer.
"""
from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import padding, rsa

# Java Object Serialization Stream Protocol type codes (java.io.ObjectStreamConstants).
_TC_NULL = 0x70
_TC_REFERENCE = 0x71
_TC_OBJECT = 0x73
_TC_ARRAY = 0x75
_TC_CLASSDESC = 0x72
_TC_ENDBLOCKDATA = 0x78

# SC_WRITE_METHOD (java.io.ObjectStreamConstants): the class has a custom
# writeObject, so its serialized data is terminated by a TC_ENDBLOCKDATA.
_SC_WRITE_METHOD = 0x01

# java.math.BigInteger's declared serializable fields, in declaration order
# (as written by its classdesc) — reused for objects whose classdesc is a
# TC_REFERENCE back to an already-seen definition (the second BigInteger in
# the stream references the first's classdesc rather than redefining it).
_BIGINTEGER_FIELDS = (
    ("I", "bitCount"),
    ("I", "bitLength"),
    ("I", "firstNonzeroByteNum"),
    ("I", "lowestSetBit"),
    ("I", "signum"),
    ("[", "magnitude"),
)


class _JavaObjectStreamReader:
    """Sequential reader over a Java `ObjectOutputStream` byte stream."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def _u8(self) -> int:
        return self._bytes(1)[0]

    def _u16(self) -> int:
        return int.from_bytes(self._bytes(2), "big")

    def _u32(self) -> int:
        return int.from_bytes(self._bytes(4), "big")

    def _bytes(self, n: int) -> bytes:
        # A truncated or corrupt key must fail loudly here rather than
        # silently handing back fewer bytes than requested (a bare slice
        # does that quietly) - the downstream int.from_bytes would then
        # produce a plausible-looking but wrong value with no signal.
        v = self._data[self._pos:self._pos + n]
        if len(v) != n:
            raise ValueError(
                f"unexpected end of key data: needed {n} byte(s) at offset "
                f"{self._pos}, got {len(v)}"
            )
        self._pos += n
        return v

    def _utf(self) -> bytes:
        return self._bytes(self._u16())

    def _read_classdesc_body(self) -> tuple[tuple[tuple[str, str], ...], int]:
        """Read a classDesc's name, flags and field descriptors, then its
        (possibly nested) superclass classDesc. Returns (fields, flags) for
        *this* class only."""
        self._utf()  # class name
        self._pos += 8  # serialVersionUID
        flags = self._u8()
        n_fields = self._u16()
        fields = []
        for _ in range(n_fields):
            typecode = chr(self._u8())
            fname = self._utf().decode("ascii")
            if typecode in "[L":
                # Assumes the type-name tag is always TC_STRING (true for
                # both BigInteger fields we parse: "[B" here and "[B" in the
                # array's own classdesc). A repeated type name encoded as
                # TC_REFERENCE instead would misparse here; unreached for
                # the real key, deliberately not handled.
                self._u8()  # TC_STRING for the field's type name
                self._utf()
            fields.append((typecode, fname))
        if self._u8() != _TC_ENDBLOCKDATA:
            raise ValueError("malformed classdesc: expected TC_ENDBLOCKDATA")
        super_tc = self._u8()
        if super_tc == _TC_CLASSDESC:
            self._read_classdesc_body()  # superclass fields are unused here
        elif super_tc != _TC_NULL:
            raise ValueError(f"malformed classdesc: unexpected super tag {super_tc:#x}")
        return tuple(fields), flags

    def _read_classdesc_ref_or_def(self) -> tuple[tuple[tuple[str, str], ...], int]:
        tc = self._u8()
        if tc == _TC_CLASSDESC:
            return self._read_classdesc_body()
        if tc == _TC_REFERENCE:
            self._u32()  # handle of a previously-defined classdesc
            return _BIGINTEGER_FIELDS, _SC_WRITE_METHOD
        raise ValueError(f"expected TC_CLASSDESC or TC_REFERENCE, got {tc:#x}")

    def read_biginteger_magnitude(self) -> bytes:
        """Read one serialized `java.math.BigInteger` object and return its
        `magnitude` field (the big-endian unsigned integer bytes)."""
        if self._u8() != _TC_OBJECT:
            raise ValueError("expected TC_OBJECT")
        fields, flags = self._read_classdesc_ref_or_def()
        magnitude = b""
        for typecode, fname in fields:
            if typecode == "I":
                self._bytes(4)  # int field value, unused
            elif typecode == "[":
                if self._u8() != _TC_ARRAY:
                    raise ValueError("expected TC_ARRAY for magnitude field")
                array_classdesc_tc = self._u8()
                if array_classdesc_tc == _TC_CLASSDESC:
                    self._read_classdesc_body()
                elif array_classdesc_tc == _TC_REFERENCE:
                    self._u32()
                else:
                    raise ValueError("expected array TC_CLASSDESC or TC_REFERENCE")
                n = self._u32()
                data = self._bytes(n)
                if fname == "magnitude":
                    magnitude = data
            else:
                # A field type we don't know how to skip: reading on would
                # walk unrelated bytes as if they were the next field, and
                # could eventually be mis-read as the modulus/exponent
                # itself. Fail loudly instead of silently marching on.
                raise ValueError(f"unexpected field typecode {typecode!r} for field {fname!r}")
        if flags & _SC_WRITE_METHOD and self._u8() != _TC_ENDBLOCKDATA:
            raise ValueError("expected trailing TC_ENDBLOCKDATA")
        return magnitude

    def read_two_bigintegers(self) -> tuple[int, int]:
        """Read the stream header plus two serialized BigIntegers and return
        their values as (first, second)."""
        magic = self._u16()
        version = self._u16()
        if magic != 0xACED or version != 5:
            raise ValueError(f"not a Java ObjectOutputStream (magic={magic:#x}, version={version})")
        first = int.from_bytes(self.read_biginteger_magnitude(), "big")
        second = int.from_bytes(self.read_biginteger_magnitude(), "big")
        if self._pos != len(self._data):
            # Both reads "succeeding" while bytes remain unconsumed means we
            # parsed a differently-structured or truncated-then-padded key
            # and got lucky twice - the (n, e) we'd return could easily be
            # wrong with no other signal. Fail loudly instead.
            raise ValueError(
                f"trailing bytes after key: {len(self._data) - self._pos}"
            )
        return first, second


def load_public_key(path: Path) -> rsa.RSAPublicKey:
    """Deserialise the Java `ObjectOutputStream`-written pair of
    `BigInteger`s (modulus, then exponent) shipped as public.key into an
    `RSAPublicKey`."""
    modulus, exponent = _JavaObjectStreamReader(path.read_bytes()).read_two_bigintegers()
    return rsa.RSAPublicNumbers(exponent, modulus).public_key()


def encrypt_login_field(plaintext: str, public_key: rsa.RSAPublicKey) -> str:
    """Encrypt one SOAP login field the way `Encrypter.rsaEncrypt` does:
    RSA/ECB/PKCS1Padding (i.e. PKCS#1 v1.5, NOT OAEP) over the UTF-8 bytes of
    `plaintext`, then standard (unwrapped) base64-encode the ciphertext."""
    ciphertext = public_key.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(ciphertext).decode("ascii")
