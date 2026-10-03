"""Record the human gate for a generated character base."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--character-dir", type=Path, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--decision", choices=("accept", "reject"), required=True)
    parser.add_argument("--rationale", required=True)
    args = parser.parse_args()
    directory = args.character_dir.resolve()
    record_path = directory / "character.json"
    if not args.reviewer.strip() or not args.rationale.strip():
        parser.error("--reviewer and --rationale must contain non-whitespace text")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"Cannot read generated-character record: {error}", file=sys.stderr)
        return 1
    preview = directory / record.get("files", {}).get("preview_glb", "")
    expected = record.get("sha256", {}).get("preview_glb")
    if not preview.is_file() or not expected or digest(preview) != expected:
        print("Cannot record review: preview GLB is missing or no longer matches its recorded checksum.", file=sys.stderr)
        return 1
    blend = directory / record.get("files", {}).get("blend", "")
    if not blend.is_file() or digest(blend) != record.get("sha256", {}).get("blend"):
        print("Cannot record review: Blender source changed; refresh the preview before review.", file=sys.stderr)
        return 1
    profile = directory / record.get("files", {}).get("profile", "")
    if not profile.is_file() or digest(profile) != record.get("sha256", {}).get("design_profile"):
        print("Cannot record review: design profile is missing or does not match this preview.", file=sys.stderr)
        return 1
    regions = directory / record.get("files", {}).get("body_regions", "")
    if not regions.is_file() or digest(regions) != record.get("sha256", {}).get("body_regions"):
        print("Cannot record review: body-region metadata is missing or out of date.", file=sys.stderr)
        return 1
    accepted = args.decision == "accept"
    record["base_review_status"] = "accepted" if accepted else "rejected"
    record["pipeline_stage"] = "base_accepted" if accepted else "base_rejected"
    record["next_stage"] = "place_and_export_rig_markers" if accepted else "revise_design_profile"
    record["base_review"] = {
        "reviewer": args.reviewer.strip(),
        "decision": args.decision,
        "rationale": args.rationale.strip(),
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "preview_sha256": expected,
    }
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded base review: {record['base_review_status']}")
    if accepted:
        print("Rig marker placement is now unlocked for this character.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
