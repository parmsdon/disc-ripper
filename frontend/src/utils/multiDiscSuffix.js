/**
 * Strip any disc-position suffix that a previous identify_cd confirm may have
 * appended. Handles both the numeric "(Disc N of M)" form and an arbitrary MB
 * medium-title form like "(Side A)".
 *
 * @param {string} title
 * @param {string|null} mbMediumTitle  - disc.mb_medium_title, if any
 * @returns {string}
 */
export function stripDiscSuffix(title, mbMediumTitle = null) {
  let s = (title || "").replace(/\s*\(Disc (?:\d+|\?) of \d+\)\s*$/i, "").trimEnd();
  if (mbMediumTitle) {
    const tag = `(${mbMediumTitle})`;
    if (s.toLowerCase().endsWith(tag.toLowerCase())) {
      s = s.slice(0, -tag.length).trimEnd();
    }
  }
  return s;
}
