const _NUM_WORD = "One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten";

// Matches any bracket (round or square) that names a disc position, e.g.:
//   (CD Two)  [Disc 1]  (Disc One)  (CD 2 of 2)  (Disc 1 of 2)  (Disc ? of 4)
const _DISC_BRACKET_RE = new RegExp(
  `\\s*[\\[(](?:CD|Disc|Disk)\\s+(?:${_NUM_WORD}|\\?|\\d+)(?:\\s+of\\s+(?:${_NUM_WORD}|\\d+))?[\\])]\\s*$`,
  "i",
);

/**
 * Strip any disc-position suffix that a previous identify_cd confirm may have
 * appended, plus any MusicBrainz disc-naming bracket from the release title.
 *
 * Stripped forms:
 *   "(Disc N of M)"    — our canonical suffix
 *   "(Disc ? of M)"   — placeholder form
 *   "(CD Two)"        — MB medium-title word form
 *   "[Disc 1]"        — square-bracket form
 *   "(CD 2 of 2)"     — MB "N of M" form
 *
 * Unaffected: "(Remastered 2009)", "(Live 1986)", "(25th Anniversary Edition)"
 *
 * @param {string} title
 * @param {string|null} mbMediumTitle  - disc.mb_medium_title, kept for backward
 *                                       compat to strip literal medium titles that
 *                                       were saved before this logic was added
 * @returns {string}
 */
export function stripDiscSuffix(title, mbMediumTitle = null) {
  let s = (title || "").replace(_DISC_BRACKET_RE, "").trimEnd();
  if (mbMediumTitle) {
    const tag = `(${mbMediumTitle})`;
    if (s.toLowerCase().endsWith(tag.toLowerCase())) {
      s = s.slice(0, -tag.length).trimEnd();
    }
  }
  return s;
}
