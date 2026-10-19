"""Independent development certification, using separate rebuild and compare processes."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

CERTIFICATION_VERSION = 'dxf-only-step-reimport-v2'
EVIDENCE_FILES = ('comparison.json', 'stdout.txt', 'stderr.txt',
                  'rebuild/stdout.txt', 'rebuild/stderr.txt',
                  'rebuild/input/drawing.dxf', 'rebuild/reconstructed.step')


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def rebuild(dxf, output):
    # This stage takes no reference model or evaluator-side manifest argument.
    from semantic_dxf_rebuild import reconstruct
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    model = reconstruct(Path(dxf))
    writer = STEPControl_Writer()
    if writer.Transfer(model, STEPControl_AsIs) != IFSelect_RetDone:
        raise ValueError('reference reconstruction STEP transfer failed')
    if writer.Write(str(output)) != IFSelect_RetDone:
        raise ValueError('reference reconstruction STEP export failed')


def compare_steps(reference, candidate):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.TopAbs import TopAbs_SOLID
    from step_to_dxf import read_step, mass, bounds, items
    reference_hash, candidate_hash = _hash(reference), _hash(candidate)
    # Read exported bytes again: never compare only the in-memory rebuild.
    original, _, _ = read_step(Path(reference))
    rebuilt, _, _ = read_step(Path(candidate))
    volume = mass(original)
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError('reference must have finite positive volume')
    differences = []
    for left, right in ((original, rebuilt), (rebuilt, original)):
        operation = BRepAlgoAPI_Cut(left, right)
        if not operation.IsDone():
            raise ValueError('certification comparison boolean failed')
        differences.append(mass(operation.Shape()))
    counts = [len(items(shape, TopAbs_SOLID)) for shape in (original, rebuilt)]
    bboxes = [bounds(shape) for shape in (original, rebuilt)]
    bbox_error = max(abs(a - b) for p, q in zip(*bboxes) for a, b in zip(p, q))
    budget = max(1e-8, volume * 1e-8)
    passed = (all(math.isfinite(value) and 0 <= value <= budget for value in differences)
              and counts[0] == counts[1] == 1 and bbox_error <= 1e-6)
    if _hash(reference) != reference_hash or _hash(candidate) != candidate_hash:
        raise ValueError('comparison input hash changed')
    return {'passed': passed, 'groundTruthSha256': reference_hash,
            'reconstructedStepSha256': candidate_hash,
            'differenceVolumesMm3': differences, 'volumeToleranceMm3': budget,
            'bboxMaxErrorMm': bbox_error, 'bboxToleranceMm': 1e-6, 'solidCounts': counts,
            'referenceVolumeMm3': volume, 'reconstructedVolumeMm3': mass(rebuilt),
            'alignment': 'fixed', 'validStepReimports': True,
            'scope': 'single valid solid, bidirectional Boolean difference and bounding box; not a maximum surface-error proof'}


def certify(dxf, reference, output, *, timeout=120):
    dxf, reference, output = Path(dxf).resolve(), Path(reference).resolve(), Path(output).resolve()
    # Resolve and fingerprint both inputs before creating output artifacts.
    dxf_hash, reference_hash = _hash(dxf), _hash(reference)
    output.mkdir(parents=True, exist_ok=False)
    rebuild_dir = output / 'rebuild'
    input_dir = rebuild_dir / 'input'
    input_dir.mkdir(parents=True)
    drawing = input_dir / 'drawing.dxf'
    shutil.copyfile(dxf, drawing)
    if _hash(drawing) != dxf_hash:
        raise ValueError('drawing changed while preparing certification')
    exported = rebuild_dir / 'reconstructed.step'
    script = str(Path(__file__).resolve())
    command = [sys.executable, '-X', 'utf8', script, 'rebuild', str(drawing), str(exported)]
    report = {'certificationVersion': CERTIFICATION_VERSION, 'passed': False,
              'drawingSha256': dxf_hash, 'groundTruthSha256': reference_hash,
              'reconstructionInputs': ['drawing.dxf'], 'rebuildCommand': command,
              'stepReimportPassed': False, 'fusionCompatible': False,
              'accessIsolationVerified': False, 'stage': 'dxf_reconstruction'}
    stages = [
        ('dxf_reconstruction', command, rebuild_dir),
        ('step_reimport_comparison', [sys.executable, '-X', 'utf8', script, 'compare',
                                     str(reference), str(exported), str(output / 'comparison.json')], output),
    ]
    for stage, args, cwd in stages:
        report['stage'] = stage
        if stage == 'step_reimport_comparison':
            report['reconstructedStepSha256'] = _hash(exported)
            report['compareCommand'] = args
        try:
            proc = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout)
            (cwd / 'stdout.txt').write_bytes(proc.stdout)
            (cwd / 'stderr.txt').write_bytes(proc.stderr)
            report[stage + 'ExitCode'] = proc.returncode
            if proc.returncode:
                report['error'] = 'certification_process_failed'
                break
        except subprocess.TimeoutExpired as exc:
            (cwd / 'stdout.txt').write_bytes(exc.stdout or b'')
            (cwd / 'stderr.txt').write_bytes(exc.stderr or b'')
            report['error'] = 'certification_timeout'
            report['timeoutSeconds'] = timeout
            break
    else:
        comparison = json.loads((output / 'comparison.json').read_text(encoding='utf-8'))
        report['comparison'] = comparison
        report['stepReimportPassed'] = comparison['passed']
        report['passed'] = comparison['passed']
        report['stage'] = 'completed'
    if _hash(reference) != reference_hash or _hash(drawing) != dxf_hash:
        report.update(passed=False, stepReimportPassed=False, error='input_hash_changed')
    if 'reconstructedStepSha256' in report:
        if not exported.is_file() or _hash(exported) != report['reconstructedStepSha256']:
            report.update(passed=False, stepReimportPassed=False, error='reconstructed_step_hash_changed')
        elif 'comparison' in report and any(report['comparison'].get(key) != report[key]
                                           for key in ('groundTruthSha256', 'reconstructedStepSha256')):
            report.update(passed=False, stepReimportPassed=False, error='comparison_input_hash_mismatch')
    report['evidenceFiles'] = {name: _hash(output / name) for name in EVIDENCE_FILES
                               if (output / name).is_file()}
    _write(output / 'certificate.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    rebuild_parser = commands.add_parser('rebuild')
    rebuild_parser.add_argument('dxf', type=Path)
    rebuild_parser.add_argument('output', type=Path)
    compare_parser = commands.add_parser('compare')
    compare_parser.add_argument('reference', type=Path)
    compare_parser.add_argument('candidate', type=Path)
    compare_parser.add_argument('output', type=Path)
    run_parser = commands.add_parser('run')
    run_parser.add_argument('dxf', type=Path)
    run_parser.add_argument('reference', type=Path)
    run_parser.add_argument('output', type=Path)
    run_parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    if args.action == 'rebuild':
        rebuild(args.dxf, args.output)
    elif args.action == 'compare':
        _write(args.output, compare_steps(args.reference, args.candidate))
    else:
        result = certify(args.dxf, args.reference, args.output, timeout=args.timeout)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result['passed'] else 1)
