"""SCTE-35 ``splice_info_section`` decoding.

The bit reader, the command fields and the segmentation descriptor come from
``~/Proyectos/tdt-datos/tdtdatos/tablas.py``; descriptors are kept as raw bytes, which is all the
segmentation type needs.
"""

from __future__ import annotations

from typing import Any

COMMANDS = {
    0x00: "splice_null",
    0x04: "splice_schedule",
    0x05: "splice_insert",
    0x06: "time_signal",
    0x07: "bandwidth_reservation",
    0xFF: "private_command",
}

SEGMENTATION = {
    0x10: "Program Start",
    0x11: "Program End",
    0x22: "Break Start",
    0x23: "Break End",
    0x30: "Provider Ad Start",
    0x31: "Provider Ad End",
    0x32: "Distributor Ad Start",
    0x33: "Distributor Ad End",
    0x34: "Provider Placement Opportunity Start",
    0x35: "Provider Placement Opportunity End",
    0x36: "Distributor Placement Opportunity Start",
    0x37: "Distributor Placement Opportunity End",
    0x40: "Unscheduled Event Start",
    0x41: "Unscheduled Event End",
}
BREAK_START = {0x22, 0x30, 0x32, 0x34, 0x36, 0x40}
BREAK_END = {0x23, 0x31, 0x33, 0x35, 0x37, 0x41}


class Bits:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def read(self, n: int) -> int:
        value = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            bit = (byte >> (7 - (self.pos & 7))) & 1
            value = (value << 1) | bit
            self.pos += 1
        return value

    def skip(self, n: int) -> None:
        self.pos += n


def describe_list(data: bytes) -> list[dict[str, Any]]:
    """Descriptors as ``{tag, length, raw}``: the segmentation type is read from ``raw``."""
    out = []
    i = 0
    while i + 2 <= len(data):
        tag, length = data[i], data[i + 1]
        end = i + 2 + length
        if end > len(data):
            break
        out.append({"tag": tag, "length": length, "raw": data[i + 2 : end].hex()})
        i = end
    return out


def _splice_time(bits: Bits) -> dict[str, Any]:
    if bits.read(1):
        bits.skip(6)
        pts = bits.read(33)
        return {"specified": True, "pts": pts, "seconds": pts / 90000.0}
    bits.skip(7)
    return {"specified": False}


def segmentation_info(scte: dict[str, Any]) -> dict[str, Any]:
    """The segmentation descriptor (tag 0x02): event id and segmentation type."""
    for descriptor in scte.get("descriptors", []):
        if descriptor.get("tag") != 0x02:
            continue
        try:
            raw = bytes.fromhex(descriptor.get("raw", ""))
        except ValueError:
            continue
        if raw[:4] != b"CUEI" or len(raw) < 8:
            continue
        event_id = int.from_bytes(raw[4:8], "big")
        kind = None
        for step in (3, 5):
            if len(raw) >= step and raw[-step] in SEGMENTATION:
                kind = raw[-step]
                break
        name = None
        if kind is not None:
            name = SEGMENTATION.get(kind) or f"0x{kind:02X}"
        return {
            "event_id": event_id,
            "type": kind,
            "name": name,
            "ad": kind in BREAK_START if kind is not None else None,
        }
    return {}


def parse(sec: bytes) -> dict[str, Any]:
    """Decode a ``splice_info_section`` (table_id 0xFC). Raises ``IndexError``/``ValueError`` if truncated."""
    bits = Bits(sec)
    table_id = bits.read(8)
    bits.read(1)  # section_syntax_indicator
    bits.read(1)  # private_indicator
    bits.read(2)
    bits.read(12)  # section_length
    protocol = bits.read(8)
    encrypted = bits.read(1)
    algorithm = bits.read(6)
    pts_adjustment = bits.read(33)
    cw_index = bits.read(8)
    tier = bits.read(12)
    command_length = bits.read(12)
    command = bits.read(8)
    start = bits.pos
    data: dict[str, Any] = {
        "table": "SCTE-35",
        "table_id": table_id,
        "protocol": protocol,
        "encrypted": bool(encrypted),
        "algorithm": algorithm,
        "pts_adjustment": pts_adjustment,
        "cw_index": cw_index,
        "tier": tier,
        "command": command,
        "command_name": COMMANDS.get(command, f"0x{command:02X}"),
    }
    if not encrypted:
        if command == 0x05:
            event = bits.read(32)
            cancelled = bits.read(1)
            bits.read(7)
            data["event"] = event
            data["cancelled"] = bool(cancelled)
            if not cancelled:
                out_of_network = bits.read(1)
                program = bits.read(1)
                with_duration = bits.read(1)
                immediate = bits.read(1)
                bits.read(4)
                data.update(out_of_network=bool(out_of_network), program=bool(program), immediate=bool(immediate))
                if program and not immediate:
                    data["time"] = _splice_time(bits)
                if with_duration:
                    auto = bits.read(1)
                    bits.read(6)
                    duration = bits.read(33)
                    data.update(auto_return=bool(auto), duration_pts=duration, duration_s=round(duration / 90000.0, 3))
                data["unique_program"] = bits.read(16)
                data["avail_num"] = bits.read(8)
                data["avails_expected"] = bits.read(8)
        elif command == 0x06:
            data["time"] = _splice_time(bits)
        elif command in (0x04, 0xFF):
            data["raw"] = sec[start // 8 :].hex()
    if not encrypted:
        bits.pos = start + command_length * 8
        if bits.pos + 16 <= len(sec) * 8:
            length = bits.read(16)
            if length and bits.pos + length * 8 <= len(sec) * 8:
                data["descriptors"] = describe_list(sec[bits.pos // 8 : bits.pos // 8 + length])
    return data


def from_tag(value: str | None) -> dict[str, Any] | None:
    """Decode the hexadecimal (or base64) value of a ``SCTE35-OUT``/``SCTE35-IN`` tag."""
    import base64

    if not value:
        return None
    text = str(value).strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    try:
        section = bytes.fromhex(text)
    except ValueError:
        try:
            section = base64.b64decode(text, validate=True)
        except (ValueError, TypeError):
            return None
    try:
        return parse(section)
    except (IndexError, ValueError):
        return None
