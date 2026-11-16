"""Bounded read-only PFSC block decoding."""
import struct
import zlib

class Reader:
    def __init__(self, read, size):
        if type(size) is not int or size < 48:
            raise ValueError("Invalid PFSC container size")
        self._source = read
        self.size = size
        fields = struct.unpack("<iiiiqqQq", self._read(0, 48))
        magic, unknown4, version, block, logical, table, data_start, length = fields
        if (
            magic != 0x43534650 or unknown4 != 0 or version != 2
            or block != 65536 or logical != 65536
            or length <= 0 or length % logical
        ):
            raise ValueError("Unsupported PFSC header")
        count = length // logical
        if not 1 <= count <= 1048575:
            raise ValueError("PFSC block count outside bounds")
        table_size = (count + 1) * 8
        if table < 48 or table + table_size > data_start or data_start > size:
            raise ValueError("PFSC block table outside bounds")
        offsets = struct.unpack(
            "<" + "Q" * (count + 1), self._read(table, table_size))
        if offsets[0] < data_start or offsets[-1] > size:
            raise ValueError("PFSC data offsets outside bounds")
        if any(
            not 1 <= end - start <= logical
            for start, end in zip(offsets, offsets[1:])
        ):
            raise ValueError("Invalid PFSC block extent")
        self.offsets = offsets
        self.block_size = logical
        self.logical_size = length
        self.block_count = count

    def _read(self, offset, size):
        if (
            type(offset) is not int or type(size) is not int
            or offset < 0 or size < 0 or size > 8 * 1024 * 1024
            or offset + size > self.size
        ):
            raise ValueError("PFSC source read outside bounds")
        data = self._source(offset, size)
        if not isinstance(data, bytes) or len(data) != size:
            raise ValueError("Truncated PFSC source")
        return data

    def block(self, index):
        if type(index) is not int or not 0 <= index < self.block_count:
            raise ValueError("PFSC block index outside bounds")
        start, end = self.offsets[index:index + 2]
        data = self._read(start, end - start)
        if len(data) == self.block_size:
            return data
        decoder = zlib.decompressobj()
        try:
            decoded = decoder.decompress(data, self.block_size + 1)
        except zlib.error as exc:
            raise ValueError("Invalid compressed PFSC block") from exc
        if (
            len(decoded) != self.block_size or not decoder.eof
            or decoder.unused_data or decoder.unconsumed_tail
        ):
            raise ValueError("PFSC decompressed block size or framing mismatch")
        return decoded

    def read(self, offset, size):
        if (
            type(offset) is not int or type(size) is not int
            or offset < 0 or size < 0 or size > 8 * 1024 * 1024
            or offset + size > self.logical_size
        ):
            raise ValueError("PFSC logical read outside bounds")
        result = bytearray()
        while size:
            index, within = divmod(offset, self.block_size)
            take = min(size, self.block_size - within)
            result.extend(self.block(index)[within:within + take])
            offset += take
            size -= take
        return bytes(result)
