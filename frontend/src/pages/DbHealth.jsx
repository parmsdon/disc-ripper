import React, { useEffect, useState } from "react";
import { api } from "../api/client";

function Stat({ value, label, tone }) {
  return (
    <div className={`stat${tone ? ` stat-${tone}` : ""}`}>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

// Returns a tone string based on whether value is zero.
// nonzeroTone: tone when value > 0; zeroTone: tone when value === 0 (null = neutral).
function t(value, nonzeroTone, zeroTone = null) {
  return value > 0 ? nonzeroTone : zeroTone;
}

export default function DbHealth() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(e.message));
  }, []);

  if (error) {
    return <div className="panel"><h2>DB Health</h2><div className="empty-state">Error: {error}</div></div>;
  }
  if (!health) {
    return <div className="panel"><h2>DB Health</h2><div className="empty-state">Loading…</div></div>;
  }

  const { library: lib, my_movies: mm, identification: id, quality: qual, musicbrainz: mb, pipeline } = health;

  const pipelineActive =
    pipeline.currently_ripping > 0 ||
    pipeline.currently_building > 0 ||
    pipeline.currently_identifying > 0 ||
    pipeline.error_discs > 0;

  return (
    <div>
      <div className="panel">
        <h2>Library</h2>
        <div className="grid">
          <Stat value={lib.dvd_count}      label="DVDs" />
          <Stat value={lib.cd_count}       label="CDs" />
          <Stat value={lib.cd_track_count} label="CD Tracks" />
        </div>
      </div>

      <div className="panel">
        <div className="panel-heading-row">
          <h2>My Movies</h2>
          <span className="panel-subtitle">
            {mm.last_sync
              ? `Last synced: ${new Date(mm.last_sync).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}`
              : "Never synced"}
          </span>
        </div>
        <div className="grid">
          <Stat value={mm.catalog_count}    label="Catalog Entries" />
          <Stat value={mm.matched_to_ripped} label="Matched to Ripped Disc" tone={t(mm.matched_to_ripped, "good")} />
          <Stat value={mm.never_ripped}     label="Never Ripped" />
        </div>
      </div>

      <div className="panel">
        <h2>Identification</h2>
        <div className="grid">
          <Stat value={id.dvds_matched}      label="DVDs Matched"        tone={t(id.dvds_matched, "good")} />
          <Stat value={id.dvds_unmatched}    label="DVDs Unmatched"      tone={t(id.dvds_unmatched, "warn", "good")} />
          <Stat value={id.cds_identified}    label="CDs Identified"      tone={t(id.cds_identified, "good")} />
          <Stat value={id.cds_unidentified}  label="CDs Unidentified"    tone={t(id.cds_unidentified, "warn", "good")} />
          <Stat value={id.cd_tracks_titled}  label="CD Tracks Titled" />
          <Stat value={id.cd_tracks_untitled} label="CD Tracks Untitled" tone={t(id.cd_tracks_untitled, "warn", "good")} />
        </div>
      </div>

      <div className="panel">
        <h2>Quality</h2>
        <div className="grid">
          <Stat value={qual.discs_needing_rerip} label="Needs Re-rip"      tone={t(qual.discs_needing_rerip, "warn", "good")} />
          <Stat value={qual.dirty_rips}          label="Dirty Rips"        tone={t(qual.dirty_rips, "warn", "good")} />
          <Stat value={qual.imperfect_tracks}    label="Imperfect Tracks"  tone={t(qual.imperfect_tracks, "warn", "good")} />
        </div>
      </div>

      <div className="panel">
        <h2>MusicBrainz</h2>
        <div className="grid">
          <Stat value={mb.cds_mb_found}     label="MB Found"     tone={t(mb.cds_mb_found, "good")} />
          <Stat value={mb.cds_mb_not_found} label="MB Not Found" tone={t(mb.cds_mb_not_found, "warn")} />
          <Stat value={mb.cds_mb_pending}   label="MB Pending"   tone={t(mb.cds_mb_pending, "info")} />
          <Stat value={mb.cds_mb_error}     label="MB Error"     tone={t(mb.cds_mb_error, "error")} />
        </div>
      </div>

      {pipelineActive && (
        <div className="panel">
          <h2>Pipeline</h2>
          <div className="grid">
            <Stat value={pipeline.currently_ripping}     label="Ripping"     tone={t(pipeline.currently_ripping, "info")} />
            <Stat value={pipeline.currently_building}    label="Building"    tone={t(pipeline.currently_building, "info")} />
            <Stat value={pipeline.currently_identifying} label="Identifying" tone={t(pipeline.currently_identifying, "info")} />
            <Stat value={pipeline.error_discs}           label="Errors"      tone={t(pipeline.error_discs, "error")} />
          </div>
        </div>
      )}
    </div>
  );
}
