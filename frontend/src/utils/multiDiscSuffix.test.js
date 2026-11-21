// node --test  (Node 18+ built-in test runner)
import { test } from "node:test";
import assert from "node:assert/strict";
import { stripDiscSuffix } from "./multiDiscSuffix.js";

// ── stripDiscSuffix ──────────────────────────────────────────────────────────

test("no suffix — title unchanged", () => {
  assert.equal(stripDiscSuffix("Piano Man"), "Piano Man");
});

test("strips (Disc 1 of 2)", () => {
  assert.equal(stripDiscSuffix("Piano Man (Disc 1 of 2)"), "Piano Man");
});

test("strips (Disc 2 of 3)", () => {
  assert.equal(stripDiscSuffix("The Wall (Disc 2 of 3)"), "The Wall");
});

test("strips (Disc ? of 4) — missing position placeholder", () => {
  assert.equal(stripDiscSuffix("Some Album (Disc ? of 4)"), "Some Album");
});

test("case-insensitive strip", () => {
  assert.equal(stripDiscSuffix("Piano Man (disc 1 of 2)"), "Piano Man");
});

test("strips MB medium title when provided", () => {
  assert.equal(stripDiscSuffix("The Wall (Side A)", "Side A"), "The Wall");
});

test("medium title match is case-insensitive", () => {
  assert.equal(stripDiscSuffix("The Wall (side a)", "Side A"), "The Wall");
});

test("medium title not stripped when not provided", () => {
  assert.equal(stripDiscSuffix("The Wall (Side A)"), "The Wall (Side A)");
});

test("medium title not stripped when it does not match", () => {
  assert.equal(stripDiscSuffix("The Wall (Side A)", "Side B"), "The Wall (Side A)");
});

test("numeric suffix takes priority — leaves unrelated parens alone", () => {
  assert.equal(stripDiscSuffix("2112 (Remastered)"), "2112 (Remastered)");
});

// ── re-identify suffix round-trip ────────────────────────────────────────────
// Simulate what handleConfirm does on first and second identify

function applyDiscSuffix(title, mbMediumTitle, position, count) {
  const discSuffix = mbMediumTitle || `Disc ${position} of ${count}`;
  const suffixTag = `(${discSuffix})`;
  const bare = stripDiscSuffix(title, mbMediumTitle);
  return bare.toLowerCase().includes(suffixTag.toLowerCase())
    ? bare
    : `${bare} ${suffixTag}`;
}

test("re-confirm with same numeric suffix — no double-append", () => {
  const afterFirst = applyDiscSuffix("Piano Man", null, 1, 2);
  assert.equal(afterFirst, "Piano Man (Disc 1 of 2)");
  const afterSecond = applyDiscSuffix(afterFirst, null, 1, 2);
  assert.equal(afterSecond, "Piano Man (Disc 1 of 2)");
});

test("re-confirm switching from numeric to medium-title suffix", () => {
  const afterFirst = applyDiscSuffix("The Wall", null, 1, 2);
  assert.equal(afterFirst, "The Wall (Disc 1 of 2)");
  const afterSecond = applyDiscSuffix(afterFirst, "Side A", null, null);
  assert.equal(afterSecond, "The Wall (Side A)");
});

test("re-confirm switching from medium-title to numeric suffix", () => {
  const afterFirst = applyDiscSuffix("The Wall", "Side A", null, null);
  assert.equal(afterFirst, "The Wall (Side A)");
  // numeric strip won't remove (Side A), but medium-title strip does
  const afterSecond = applyDiscSuffix(afterFirst, null, 1, 2);
  // (Side A) is not a numeric suffix → strip doesn't remove it → appends numeric
  // This is the known limitation; test documents actual behaviour
  assert.equal(afterSecond, "The Wall (Side A) (Disc 1 of 2)");
});

test("re-confirm with same medium-title suffix — no double-append", () => {
  const afterFirst = applyDiscSuffix("The Wall", "Side A", null, null);
  assert.equal(afterFirst, "The Wall (Side A)");
  const afterSecond = applyDiscSuffix(afterFirst, "Side A", null, null);
  assert.equal(afterSecond, "The Wall (Side A)");
});
