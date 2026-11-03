import assert from 'node:assert/strict';
import { test } from 'node:test';

import { parseId3, parseSplice, samplesToCues } from '../../tdtlocal/static/js/lib/inband.js';

const hex = (text) => Uint8Array.from(text.match(/../g).map((byte) => parseInt(byte, 16)));

// Real PRIV payloads from the 448407824.ts segment of Antena 3 (the fixtures of AdMuteTest).
const INSERT = 'fc302500000000000000fff0140501321ae17fefff65c79f90fe000c5c1004d20000000025441ee9';
const INSERT_BACK = 'fc302500000000000000fff0140501321ae17f6fff65c79f90fe000c5c1004d20000000025441ee9';
const BREAK_START =
  'fc305c00000000000000fff00506ff65c79f90004602444355454901cab1617f' +
  'c000000c5c101030356137383736366539383662323830363664396364663336' +
  '356136613136356137656431613833343439336562663661220101e30a9ce0';
const BREAK_END = BREAK_START.replace('220101e30a9ce0', '230101e30a9ce0');

/** An ID3v2.3 tag with one PRIV frame per payload (what hls.js hands over as `sample.data`). */
function id3(payloads, owner = 'urn:scte:scte35:2013:bin@') {
  const frames = payloads.map((payload) => {
    const own = Uint8Array.from([...owner].map((c) => c.charCodeAt(0)));
    const body = new Uint8Array(own.length + 1 + payload.length);
    body.set(own);
    body[own.length] = 0;
    body.set(payload, own.length + 1);
    const frame = new Uint8Array(10 + body.length);
    frame.set(Uint8Array.from([0x50, 0x52, 0x49, 0x56])); // "PRIV"
    const size = body.length;
    frame[4] = (size >>> 24) & 0xff;
    frame[5] = (size >>> 16) & 0xff;
    frame[6] = (size >>> 8) & 0xff;
    frame[7] = size & 0xff;
    frame.set(body, 10);
    return frame;
  });
  const size = frames.reduce((n, frame) => n + frame.length, 0);
  const tag = new Uint8Array(10 + size);
  tag.set(Uint8Array.from([0x49, 0x44, 0x33, 3, 0, 0])); // "ID3", v2.3
  for (let i = 0; i < 4; i++) tag[6 + i] = (size >>> (21 - 7 * i)) & 0x7f;
  let at = 10;
  for (const frame of frames) {
    tag.set(frame, at);
    at += frame.length;
  }
  return tag;
}

test('splice_insert: out_of_network starts, its return ends', () => {
  const start = parseSplice(hex(INSERT));
  assert.equal(start.action, 1);
  assert.equal(start.command, 0x05);
  assert.equal(start.event, 20060897);
  assert.equal(start.outOfNetwork, true);

  const back = parseSplice(hex(INSERT_BACK));
  assert.equal(back.action, -1);
  assert.equal(back.outOfNetwork, false);
});

test('time_signal is classified by its segmentation type', () => {
  const start = parseSplice(hex(BREAK_START));
  assert.equal(start.action, 1);
  assert.equal(start.command, 0x06);
  assert.equal(start.type, 0x22);
  assert.equal(start.event, 30060897);
  assert.equal(start.duration, 9);

  const end = parseSplice(hex(BREAK_END));
  assert.equal(end.action, -1);
  assert.equal(end.type, 0x23);
});

test('a Program Start is not a break, and garbage is not a section', () => {
  const program = hex(BREAK_START);
  program[program.length - 7] = 0x10; // segmentation_type 0x10, before its message length
  assert.equal(parseSplice(program).action, 0);
  assert.equal(parseSplice(new Uint8Array([1, 2, 3, 4])), null);
  assert.equal(parseSplice(new Uint8Array()), null);
});

test('the PRIV owner must name SCTE-35', () => {
  const ok = parseId3(id3([hex(INSERT)]));
  assert.equal(ok.length, 1);
  assert.match(ok[0].owner, /scte35/);
  assert.deepEqual(Array.from(ok[0].data), Array.from(hex(INSERT)));

  assert.deepEqual(parseId3(id3([hex(INSERT)], 'com.apple.streaming.transportStreamTimestamp')), []);
  assert.deepEqual(parseId3(new Uint8Array([1, 2, 3])), []);
});

test('metadata samples become timed cues, folding the duplicate frames of one break', () => {
  const data = id3([hex(INSERT), hex(BREAK_START)]); // the same break arrives twice
  const cues = samplesToCues([{ pts: 12.5, data }]);
  assert.equal(cues.length, 1);
  assert.equal(cues[0].action, 1);
  assert.equal(cues[0].at, 12.5);

  const both = samplesToCues([{ pts: 12.5, data }, { pts: 42, data: id3([hex(BREAK_END)]) }]);
  assert.deepEqual(both.map((cue) => [cue.action, cue.at]), [[1, 12.5], [-1, 42]]);
});

test('samples without a usable timestamp are left to the playlist', () => {
  const data = id3([hex(INSERT)]);
  assert.deepEqual(samplesToCues([{ data }]), []);
  assert.deepEqual(samplesToCues([{ pts: NaN, data }]), []);
  assert.deepEqual(samplesToCues([]), []);
  assert.deepEqual(samplesToCues(null), []);
});
