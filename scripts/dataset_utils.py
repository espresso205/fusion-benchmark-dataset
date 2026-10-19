"""Standard-library integrity helpers; no CAD or Fusion imports."""
import hashlib
import json
from pathlib import Path, PurePosixPath


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_data_path(root, relative):
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError("data paths must be relative POSIX paths")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("data path escapes repository")
    root = Path(root).resolve()
    target = (root / Path(*path.parts)).resolve()
    if not target.is_relative_to(root):
        raise ValueError("resolved data path escapes repository")
    if not target.is_file():
        raise ValueError("missing data file: " + relative)
    return target


def load_verified_manifest(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "data_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema")
    expected_ids = [f"Q{i:02d}" for i in range(1, 11)]
    if [row["case"] for row in manifest["cases"]] != expected_ids:
        raise ValueError("expected exactly Q01 through Q10, in order")
    files = {}
    for entry in manifest["files"]:
        rel = entry["path"]
        if rel in files:
            raise ValueError("duplicate manifest path: " + rel)
        path = resolve_data_path(root, rel)
        if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            raise ValueError("data hash mismatch: " + rel)
        files[rel] = entry
    for row in manifest["cases"]:
        qid = row["case"]
        if set(row["expected_hashes"]) != {"gt", "annotations"}:
            raise ValueError("unexpected private hash keys: " + qid)
        for role in ("gt", "annotations"):
            rel = row[role]
            if not rel.startswith("private/" + qid + "/"):
                raise ValueError("private file in wrong partition: " + qid)
            if files[rel]["sha256"] != row["expected_hashes"][role]:
                raise ValueError("case hash disagrees with file manifest: " + qid)
        annotations = json.loads(resolve_data_path(root, row["annotations"]).read_text(encoding="utf-8-sig"))
        if annotations.get("gt_sha256") != row["expected_hashes"]["gt"]:
            raise ValueError("annotations belong to another GT: " + qid)
        for role, rel in row["public"].items():
            if role not in ("task", "drawing") or not rel.startswith("public/" + qid + "/") or rel not in files:
                raise ValueError("invalid public mapping: " + qid)
    return manifest
