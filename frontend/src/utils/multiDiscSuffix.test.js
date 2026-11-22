// node --test  (Node 18+ built-in test runner)
import { test } from "node:test";
import assert from "node:assert/strict";
import { stripDiscSuffix } from "./multiDiscSuffix.js";

// ── stripDiscSuffix — no-op cases ────────────────────────────────────────────

test("title with no brackets — unchanged", () => {
  assert.equal(stripDiscSuffix("Piano Man"), "Piano Man");
});

test("other bracketed text left alone — (Remastered 2009)", () => {
  assert.equal(stripDiscSuffix("Kind of Blue (Remastered 2009)"), "Kind of Blue (Remastered 2009)");
});

test("other bracketed text left alone — (Live 1986)", () => {
  assert.equal(stripDiscSuffix("Double Fantasy (Live 1986)"), "Double Fantasy (Live 1986)");
});

test("other bracketed text left alone — (25th Anniversary Edition)", () => {
  assert.equal(stripDiscSuffix("Thriller (25th Anniversary Edition)"), "Thriller (25th Anniversary Edition)");
});

// ── stripDiscSuffix — disc-naming brackets ───────────────────────────────────

test("strips (CD Two)", () => {
  assert.equal(stripDiscSuffix("The Ultimate Collection (CD Two)"), "The Ultimate Collection");
});

test("strips [Disc 1]", () => {
  assert.equal(stripDiscSuffix("Greatest Hits [Disc 1]"), "Greatest Hits");
});

test("strips (Disc One)", () => {
  assert.equal(stripDiscSuffix("Double Album (Disc One)"), "Double Album");
});

test("strips (CD 2 of 2)", () => {
  assert.equal(stripDiscSuffix("The Wall (CD 2 of 2)"), "The Wall");
});

test("strips our canonical (Disc 1 of 2)", () => {
  assert.equal(stripDiscSuffix("Piano Man (Disc 1 of 2)"), "Piano Man");
});

test("strips (Disc 2 of 3)", () => {
  assert.equal(stripDiscSuffix("The Wall (Disc 2 of 3)"), "The Wall");
});

test("strips (Disc ? of 4) — placeholder position", () => {
  assert.equal(stripDiscSuffix("Some Album (Disc ? of 4)"), "Some Album");
});

test("strips (Disk 3) — alternate spelling", () => {
  assert.equal(stripDiscSuffix("Hits Collection (Disk 3)"), "Hits Collection");
});

test("disc bracket stripped leaving other brackets intact", () => {
  assert.equal(
    stripDiscSuffix("Kind of Blue (Remastered 2009) (CD Two)"),
    "Kind of Blue (Remastered 2009)",
  );
});

test("strip is case-insensitive", () => {
  assert.equal(stripDiscSuffix("Piano Man (disc 1 of 2)"), "Piano Man");
});

// ── stripDiscSuffix — mbMediumTitle fallback (backward compat) ───────────────

test("strips literal MB medium title when provided", () => {
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

// ── re-identify suffix round-trip ────────────────────────────────────────────
// Simulate what handleConfirm does on first and second identify (always numeric now).

function applyDiscSuffix(title, position, count) {
  const suffixTag = `(Disc ${position} of ${count})`;
  const bare = stripDiscSuffix(title);
  return bare.toLowerCase().includes(suffixTag.toLowerCase())
    ? bare
    : `${bare} ${suffixTag}`;
}

test("re-confirm with same numeric suffix — no double-append", () => {
  const afterFirst = applyDiscSuffix("Piano Man", 1, 2);
  assert.equal(afterFirst, "Piano Man (Disc 1 of 2)");
  const afterSecond = applyDiscSuffix(afterFirst, 1, 2);
  assert.equal(afterSecond, "Piano Man (Disc 1 of 2)");
});

test("re-confirm after MB bracket was in original title — cleans and re-adds numeric", () => {
  // Simulates a title that had e.g. "(CD Two)" from MusicBrainz data
  const afterFirst = applyDiscSuffix("The Ultimate Collection (CD Two)", 2, 2);
  assert.equal(afterFirst, "The Ultimate Collection (Disc 2 of 2)");
  const afterSecond = applyDiscSuffix(afterFirst, 2, 2);
  assert.equal(afterSecond, "The Ultimate Collection (Disc 2 of 2)");
});

test("re-confirm with changed disc position — replaces suffix cleanly", () => {
  const afterFirst = applyDiscSuffix("The Wall", 1, 2);
  assert.equal(afterFirst, "The Wall (Disc 1 of 2)");
  const afterSecond = applyDiscSuffix(afterFirst, 2, 2);
  assert.equal(afterSecond, "The Wall (Disc 2 of 2)");
});

test("non-disc brackets are preserved through re-confirm", () => {
  const afterFirst = applyDiscSuffix("Kind of Blue (Remastered 2009)", 1, 2);
  assert.equal(afterFirst, "Kind of Blue (Remastered 2009) (Disc 1 of 2)");
  const afterSecond = applyDiscSuffix(afterFirst, 1, 2);
  assert.equal(afterSecond, "Kind of Blue (Remastered 2009) (Disc 1 of 2)");
});
