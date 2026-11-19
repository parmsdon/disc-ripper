"""
Primary/secondary computation for catalog entries that share the same
normalised title + year (the same key the importer uses for matching).

A group of one entry is "sole". A group of two or more decides one primary
(the canonical library name) and the rest are secondaries (get a bracketed
suffix). Groups that cannot be resolved unambiguously are flagged
needs_review=True and appear on the Audit tab.
"""

import re
from collections import defaultdict
from typing import NamedTuple

_ALNUM_ONLY_RE = re.compile(r'[^A-Za-z0-9]')
_SUPER_TABLE = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')


def normalise_catalog_title(title: str, year: int | None) -> str:
    """
    Catalog match key: letters and digits only, superscripts → ASCII.
    "Alien3", "Alien 3", "Alien³" and "Alien: 3" all produce the same key.
    """
    t = (title or '').translate(_SUPER_TABLE)
    part = _ALNUM_ONLY_RE.sub('', t).upper()
    return f"{part}_{year}" if year else part


def fmt_region(region_codes: str | None) -> str:
    """
    Format comma-separated region string as "R2", "R1", "R2/4", etc.
    Strips "NoRegionCode" entries. Returns "" when nothing remains.
    """
    if not region_codes:
        return ""
    parts = []
    seen: set[str] = set()
    for p in region_codes.split(','):
        p = p.strip()
        if p and p != 'NoRegionCode' and p not in seen:
            parts.append(p)
            seen.add(p)
    parts.sort(key=lambda x: int(x) if x.isdigit() else 999)
    return ("R" + "/".join(parts)) if parts else ""


class DupRole(NamedTuple):
    role: str           # "sole" | "primary" | "secondary"
    link_suffix: str    # e.g. "Special Edition, R1"; "" for primaries/soles
    needs_review: bool  # group could not be resolved unambiguously


def compute_dup_roles(catalog_entries) -> dict[int, DupRole]:
    """
    Returns {catalog_id: DupRole} for every entry in catalog_entries.

    catalog_entries must be iterable of objects with:
      .id, .title, .year, .edition, .video_standard, .region_codes,
      .collection_number
    """
    groups: dict[str, list] = defaultdict(list)
    for entry in catalog_entries:
        key = normalise_catalog_title(entry.title, entry.year)
        groups[key].append(entry)

    roles: dict[int, DupRole] = {}

    for _key, group in groups.items():
        if len(group) == 1:
            roles[group[0].id] = DupRole("sole", "", False)
            continue

        # Determine primary
        pal = [e for e in group if (e.video_standard or "").upper() == "PAL"]
        needs_review = False

        if len(pal) == 1:
            primary = pal[0]
        elif len(pal) > 1:
            pal_no_edition = [e for e in pal if not e.edition]
            if len(pal_no_edition) == 1:
                primary = pal_no_edition[0]
            else:
                primary = min(group, key=lambda e: (
                    e.collection_number is None, e.collection_number or 0
                ))
                needs_review = True
        else:
            primary = min(group, key=lambda e: (
                e.collection_number is None, e.collection_number or 0
            ))
            needs_review = True

        primary_region = fmt_region(primary.region_codes)
        roles[primary.id] = DupRole("primary", "", needs_review)

        for entry in group:
            if entry.id == primary.id:
                continue
            parts = []
            if entry.edition:
                parts.append(entry.edition)
            entry_region = fmt_region(entry.region_codes)
            if entry_region and entry_region != primary_region:
                parts.append(entry_region)
            suffix = ", ".join(parts)
            suffix_needs_review = False
            if not suffix:
                suffix = str(entry.collection_number) if entry.collection_number is not None else f"id{entry.id}"
                suffix_needs_review = True
            roles[entry.id] = DupRole("secondary", suffix, needs_review or suffix_needs_review)

    return roles


def load_dup_roles(session) -> dict[int, DupRole]:
    """Load all Catalog rows from session and return compute_dup_roles result."""
    from sqlalchemy import select
    from common.models import Catalog
    entries = session.scalars(select(Catalog)).all()
    return compute_dup_roles(entries)
