import assert from "node:assert/strict";
import test from "node:test";
import { glitchyPlacement } from "./glitchy-placement";

const base = {
  narrow: false,
  formFocused: false,
  shareOrDownloadInView: false,
  userOpened: false,
  viewportHeight: 800,
  obstacleTops: [] as number[],
};

test("a narrow screen starts as a small collapsed control", () => {
  const place = glitchyPlacement({ ...base, narrow: true });
  assert.equal(place.collapsed, true);
  assert.equal(place.bottom, 12);
});

test("opening Glitchy on a narrow screen shows the panel", () => {
  const place = glitchyPlacement({ ...base, narrow: true, userOpened: true });
  assert.equal(place.collapsed, false);
});

test("a form or the share step keeps Glitchy collapsed and above the button", () => {
  const typing = glitchyPlacement({
    ...base,
    narrow: true,
    userOpened: true,
    formFocused: true,
    obstacleTops: [700],
  });
  assert.equal(typing.collapsed, true);
  assert.equal(typing.bottom, 800 - 700 + 8);

  const sharing = glitchyPlacement({
    ...base,
    shareOrDownloadInView: true,
    obstacleTops: [640],
  });
  assert.equal(sharing.collapsed, true);
  assert.ok(sharing.bottom > 12);
  assert.ok(sharing.bottom <= 360);
});
