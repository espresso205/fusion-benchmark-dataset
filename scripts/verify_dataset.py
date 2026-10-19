"""Verify retained bytes, public input boundaries, and archive checksums."""
import argparse
import json
from pathlib import Path
import re
import sys

# Embeddable CPython may omit the script directory because of its isolated ._pth.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset_utils import load_verified_manifest, resolve_data_path, sha256


PUBLIC_METADATA = {"README.md", "INSTRUCTIONS.md", "SOURCE_LICENSE.txt", "manifest.json", "SHA256SUMS"}


def verify_checksums(root, checksum_file):
    lines = checksum_file.read_text(encoding="utf-8").splitlines()
    seen = set()
    for line in lines:
        digest, relative = line.split("  ", 1)
        if relative in seen:
            raise ValueError("duplicate checksum path: " + relative)
        seen.add(relative)
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or sha256(resolve_data_path(root, relative)) != digest:
            raise ValueError("checksum mismatch: " + relative)
    return seen


def verify(root):
    root = Path(root).resolve()
    manifest = load_verified_manifest(root)
    public = root / "public"
    expected = {f"Q{i:02d}/{name}" for i in range(1, 11) for name in ("TASK.md", "drawing.dxf")}
    expected.update(PUBLIC_METADATA)
    actual = {p.relative_to(public).as_posix() for p in public.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("unexpected/missing public files: " + repr(sorted(actual ^ expected)))
    private_hashes = {row["expected_hashes"][role] for row in manifest["cases"] for role in ("gt", "annotations")}
    forbidden = re.compile(r"ISO-10303-21|source_definition|challenge_generator|gt_sha256|(?:[A-Za-z]:[\\/])|(?m:^3DSOLID$|^BODY$|^REGION$)", re.I)
    for relative in sorted(actual):
        path = public / relative
        if path.is_symlink() or sha256(path) in private_hashes:
            raise ValueError("private bytes or symlink in public inputs: " + relative)
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if forbidden.search(text) or any(digest in text for digest in private_hashes):
            raise ValueError("private source marker/path/hash in public inputs: " + relative)
    public_checksums = verify_checksums(public, public / "SHA256SUMS")
    if public_checksums != actual - {"SHA256SUMS"}:
        raise ValueError("public checksum coverage is incomplete")
    pm = json.loads((public / "manifest.json").read_text(encoding="utf-8"))
    if [row["case"] for row in pm["cases"]] != [row["case"] for row in manifest["cases"]]:
        raise ValueError("public and private case rosters disagree")
    for row in pm["cases"]:
        if set(row) != {"case", "task", "drawing", "expected_hashes"}:
            raise ValueError("unexpected public manifest fields")
        for role in ("task", "drawing"):
            if sha256(resolve_data_path(public, row[role])) != row["expected_hashes"][role]:
                raise ValueError("public case hash mismatch")
    root_checksum = root / "SHA256SUMS"
    if root_checksum.exists():
        verify_checksums(root, root_checksum)
    return {"status": "passed", "cases": len(manifest["cases"]),
            "preserved_source_files": len(manifest["files"]), "public_files": len(actual),
            "gt_and_annotation_pairs": 10, "public_input_boundary_check": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.root), indent=2))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
