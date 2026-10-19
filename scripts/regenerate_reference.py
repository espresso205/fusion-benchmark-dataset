"""Optional source reconstruction, separate from ordinary dataset maintenance.

Requires the CAD dependencies described in private/_sources/README.md.
Never overwrites frozen GT; STEP metadata can differ between regenerations.
"""
import argparse
import json
from pathlib import Path
import sys
from dataset_utils import load_verified_manifest, resolve_data_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=[f"Q{i:02d}" for i in range(1, 11)])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    load_verified_manifest(root)
    output = args.output.resolve()
    if output.is_relative_to((root / "private").resolve()) or output.is_relative_to((root / "public").resolve()):
        parser.error("output must be outside frozen public/ and private/ data")
    if output.exists():
        parser.error("output already exists")
    sys.path.insert(0, str(root / "private" / "_sources"))
    from challenge_generator import build_model
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    definition = json.loads(resolve_data_path(root, f"private/{args.case}/source_definition.json").read_text(encoding="utf-8-sig"))
    shape = build_model(definition)
    writer = STEPControl_Writer()
    if writer.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone:
        raise RuntimeError("STEP transfer failed")
    output.parent.mkdir(parents=True, exist_ok=True)
    if writer.Write(str(output)) != IFSelect_RetDone:
        raise RuntimeError("STEP write failed")
    print(f"Reconstructed source geometry for {args.case}: {output}")


if __name__ == "__main__":
    main()
