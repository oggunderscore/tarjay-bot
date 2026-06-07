"""Generate simple PNG icons for the extension."""
import struct
import zlib
from pathlib import Path


def create_png(size, color=(204, 0, 0)):
    """Create a simple solid circle PNG icon."""
    # Create raw pixel data (RGBA)
    pixels = []
    center = size // 2
    radius = size // 2 - 2
    for y in range(size):
        row = []
        for x in range(size):
            dx = x - center
            dy = y - center
            if dx * dx + dy * dy <= radius * radius:
                row.extend([color[0], color[1], color[2], 255])
            else:
                row.extend([0, 0, 0, 0])
        pixels.append(bytes(row))

    # Build PNG
    def make_chunk(chunk_type, data):
        chunk = chunk_type + data
        crc = zlib.crc32(chunk) & 0xFFFFFFFF
        return struct.pack('>I', len(data)) + chunk + struct.pack('>I', crc)

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)
    
    raw_data = b''
    for row in pixels:
        raw_data += b'\x00' + row
    
    compressed = zlib.compress(raw_data)
    
    png = header
    png += make_chunk(b'IHDR', ihdr)
    png += make_chunk(b'IDAT', compressed)
    png += make_chunk(b'IEND', b'')
    return png


icons_dir = Path(__file__).parent
for size in [16, 48, 128]:
    png_data = create_png(size)
    (icons_dir / f'icon{size}.png').write_bytes(png_data)
    print(f'Created icon{size}.png')
