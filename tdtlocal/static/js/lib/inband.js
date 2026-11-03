// SCTE-35 in-band, the way the TDT Spain app's AdMute reads it: the break markers travel inside the
// picture as ID3 `PRIV` frames, and the sample they come in carries the exact instant they play on the
// player's timeline. That instant is what arms the mute and the unmute, so both land on the same second
// as the break instead of when the playlist is polled.
//
// The playlist stays as the fallback: not every channel sends the markers in-band.

const COMMANDS = { 0x00: 'splice_null', 0x05: 'splice_insert', 0x06: 'time_signal', 0x07: 'bandwidth_reservation' };

const BREAK_START = new Set([0x22, 0x24, 0x30, 0x32, 0x34, 0x36, 0x40, 0x44]);
const BREAK_END = new Set([0x23, 0x31, 0x33, 0x35, 0x37, 0x41, 0x45]);

/** Big-endian bit reader over a splice_info_section. */
class Bits {
  constructor(bytes) {
    this.bytes = bytes;
    this.pos = 0;
  }

  read(count) {
    let value = 0;
    for (let i = 0; i < count; i++) {
      const byte = this.bytes[this.pos >> 3];
      if (byte === undefined) throw new RangeError('scte35: truncated');
      value = value * 2 + ((byte >> (7 - (this.pos & 7))) & 1);
      this.pos++;
    }
    return value;
  }

  skip(count) {
    this.pos += count;
  }

  seek(bit) {
    this.pos = bit;
  }
}

const latin1 = (bytes) => String.fromCharCode(...bytes.subarray(0, 256));

function segmentation(bits, splice) {
  bits.skip(32); // the "CUEI" identifier
  const eventId = bits.read(32);
  const cancelled = bits.read(1);
  bits.skip(7);
  splice.event = eventId;
  if (cancelled) return;
  const program = bits.read(1);
  const hasDuration = bits.read(1);
  const notRestricted = bits.read(1);
  if (notRestricted) bits.skip(5);
  else {
    bits.skip(1); // web_delivery_allowed
    bits.skip(1); // no_regional_blackout
    bits.skip(1); // archive_allowed
    bits.skip(2); // device_restrictions
  }
  if (!program) {
    const components = bits.read(8);
    for (let i = 0; i < components; i++) {
      bits.skip(8); // component_tag
      bits.skip(7);
      bits.skip(33); // pts_offset
    }
  }
  if (hasDuration) splice.duration = bits.read(40) / 90000;
  bits.skip(8); // segmentation_upid_type
  const upid = bits.read(8); // segmentation_upid_length, in bytes
  bits.skip(upid * 8);
  splice.type = bits.read(8);
  const message = bits.read(8);
  bits.skip(message * 8);
}

/**
 * Decode a SCTE-35 splice_info_section (the PRIV payload). `action` is 1 for a break start, -1 for
 * its end and 0 for anything else: a Program Start (0x10), an encrypted section or garbage.
 * Returns null if it is not a section.
 */
export function parseSplice(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input || 0);
  if (bytes.length < 6 || bytes[0] !== 0xfc) return null;
  try {
    const bits = new Bits(bytes);
    bits.read(8); // table_id
    bits.read(1); // section_syntax_indicator
    bits.read(1); // private_indicator
    bits.read(2);
    bits.read(12); // section_length
    const protocol = bits.read(8);
    const encrypted = bits.read(1);
    bits.read(6); // encryption_algorithm
    const ptsAdjustment = bits.read(33);
    bits.read(8); // cw_index
    bits.read(12); // tier
    const commandLength = bits.read(12);
    const command = bits.read(8);
    const start = bits.pos;
    const out = {
      table: 'SCTE-35', protocol, encrypted: !!encrypted, ptsAdjustment, command,
      commandName: COMMANDS[command] || 'cmd 0x' + command.toString(16), action: 0, type: -1,
    };
    if (encrypted) return out;

    if (command === 0x05) {
      out.event = bits.read(32);
      const cancelled = bits.read(1);
      bits.skip(7);
      if (cancelled) {
        out.cancelled = true;
        out.action = -1;
      } else {
        const outOfNetwork = bits.read(1);
        bits.skip(3);
        bits.skip(4);
        out.outOfNetwork = !!outOfNetwork;
        out.action = outOfNetwork ? 1 : -1;
      }
      return out;
    }

    if (command === 0x06) {
      bits.seek(start + commandLength * 8);
      if (bits.pos + 16 > bytes.length * 8) return out;
      const length = bits.read(16);
      const end = bits.pos + length * 8;
      while (bits.pos + 16 <= end && bits.pos + 16 <= bytes.length * 8) {
        const tag = bits.read(8);
        const size = bits.read(8);
        const body = bits.pos;
        if (tag === 0x02) segmentation(bits, out);
        bits.seek(body + size * 8);
      }
      if (out.type >= 0) out.action = BREAK_START.has(out.type) ? 1 : BREAK_END.has(out.type) ? -1 : 0;
    }
    return out;
  } catch {
    return null; // truncated, encrypted or not SCTE-35 at all: the playlist path still decides
  }
}

const syncsafe = (bytes, at) =>
  (bytes[at] & 0x7f) * 0x200000 + (bytes[at + 1] & 0x7f) * 0x4000 + (bytes[at + 2] & 0x7f) * 0x80 + (bytes[at + 3] & 0x7f);

/** The SCTE-35 `PRIV` frames of an ID3v2 tag (the `data` of an hls.js metadata sample). */
export function parseId3(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input || 0);
  if (bytes.length < 10 || bytes[0] !== 0x49 || bytes[1] !== 0x44 || bytes[2] !== 0x33) return [];
  const version = bytes[3];
  const size = ((bytes[6] & 0x7f) << 21) | ((bytes[7] & 0x7f) << 14) | ((bytes[8] & 0x7f) << 7) | (bytes[9] & 0x7f);
  const end = Math.min(bytes.length, 10 + size);
  const frames = [];
  let at = 10;
  while (at + 10 <= end) {
    if (!bytes[at]) break; // padding
    const id = latin1(bytes.subarray(at, at + 4));
    const length = version === 4 ? syncsafe(bytes, at + 4)
      : ((bytes[at + 4] << 24) | (bytes[at + 5] << 16) | (bytes[at + 6] << 8) | bytes[at + 7]) >>> 0;
    if (!length || at + 10 + length > end) break;
    if (id === 'PRIV') {
      const content = bytes.subarray(at + 10, at + 10 + length);
      const zero = content.indexOf(0);
      if (zero > 0) {
        const owner = latin1(content.subarray(0, zero));
        if (owner.toLowerCase().includes('scte35')) frames.push({ owner, data: content.subarray(zero + 1) });
      }
    }
    at += 10 + length;
  }
  return frames;
}

/**
 * The timed cues of a batch of hls.js metadata samples: `{action, at}` where `at` is the instant the
 * sample plays, in seconds on the player's timeline (the same units as `video.currentTime`).
 * Duplicate frames for the same break (a splice_insert plus its time_signal) are folded into one.
 */
export function samplesToCues(samples) {
  const cues = [];
  const seen = new Set();
  for (const sample of samples || []) {
    const at = Number(sample && sample.pts);
    if (!Number.isFinite(at)) continue;
    for (const frame of parseId3(sample.data)) {
      const splice = parseSplice(frame.data);
      if (!splice || !splice.action) continue;
      const key = splice.action + '@' + Math.round(at * 10);
      if (seen.has(key)) continue;
      seen.add(key);
      cues.push({ action: splice.action, at, key, splice });
    }
  }
  return cues;
}
