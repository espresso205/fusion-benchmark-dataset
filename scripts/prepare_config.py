"""Create an evaluator's local private-cases JSON without candidate history."""
import argparse
import json
from pathlib import Path
from dataset_utils import load_verified_manifest, resolve_data_path


def build_config(root, selected=None):
    root = Path(root).resolve()
    manifest = load_verified_manifest(root)
    selected = set(selected or [row["case"] for row in manifest["cases"]])
    available = {row["case"] for row in manifest["cases"]}
    if not selected or not selected <= available:
        raise ValueError("case selection must be a nonempty subset of Q01-Q10")
    return {
        "annotation_kind": manifest["annotation_kind"],
        "coordinate_frame": manifest["coordinate_frame"],
        "cases": [
            {"case": row["case"],
             "gt": str(resolve_data_path(root, row["gt"])),
             "annotations": str(resolve_data_path(root, row["annotations"])),
             "expected_hashes": dict(row["expected_hashes"])}
            for row in manifest["cases"] if row["case"] in selected
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", help="Optional case IDs, e.g. Q01 Q02")
    parser.add_argument("--force", action="store_true", help="Replace an existing local output")
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        output = args.output.resolve()
        if output.is_relative_to((root / "public").resolve()):
            raise ValueError("private configuration must not be written into public/")
        if output.is_relative_to((root / "private").resolve()):
            raise ValueError("configuration output must not replace private data")
        if output.is_relative_to(root) and not output.is_relative_to((root / "local").resolve()):
            raise ValueError("configuration inside the repository must be written under local/")
        config = build_config(root, args.cases)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w" if args.force else "x", encoding="utf-8", newline="\n") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        print(f"Created local private cases for {len(config['cases'])} questions: {output}")
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
