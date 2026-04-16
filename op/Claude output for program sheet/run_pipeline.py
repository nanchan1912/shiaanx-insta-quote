"""
run_pipeline.py
---------------
Convenience runner for the full CNC feature-to-program-sheet-and-quote pipeline.

Usage
-----
    python run_pipeline.py <step_file> [options]

    python run_pipeline.py "Botlabs Hinge/MIRROR_2.3MCYH2_2.STEP"
    python run_pipeline.py part.step --material mild_steel --part-name "Bracket" --qty 5 --rfq example_rfq.json

Steps
-----
    1  extract_features.py       STEP -> _features.json
    2  cluster_features.py       _features.json -> _clustered.json
    3  classify_features.py      _clustered.json -> _classified.json
    4  process_selection.py      _classified.json -> _processes.json
    5  setup_planning.py         _processes.json -> _setups.json
    6  setup_view_renderer.py    STEP + _setups.json -> _setup_views/
    7  tool_selection.py         _setups.json -> _tools.json
    8  parameter_calculation.py  _tools.json -> _params.json
    9  program_sheet.py          _params.json -> _program_sheet.pdf
    10 quote_estimation.py       _params.json + _features.json -> _quote.json
"""

import os
import sys
import subprocess
import argparse
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
_OCC_ENV_PYTHON = Path(r'C:\Users\Siddhant Gupta\miniconda3\envs\occ\python.exe')


def _find_python() -> str:
    override = os.environ.get('INSTA_QUOTE_OCC_PYTHON', '').strip()
    if override and Path(override).exists():
        return override

    try:
        import OCC  # noqa: F401
        return sys.executable
    except ImportError:
        pass

    if _OCC_ENV_PYTHON.exists():
        return str(_OCC_ENV_PYTHON)

    common_candidates = [
        Path.home() / 'miniforge3' / 'envs' / 'occ' / 'python.exe',
        Path.home() / 'mambaforge' / 'envs' / 'occ' / 'python.exe',
        Path.home() / 'miniconda3' / 'envs' / 'occ' / 'python.exe',
        Path.home() / 'anaconda3' / 'envs' / 'occ' / 'python.exe',
    ]
    for candidate in common_candidates:
        if candidate.exists():
            return str(candidate)

    print(
        'WARNING: OCC not found in current Python and occ env not found at:\n'
        f'  {_OCC_ENV_PYTHON}\n'
        'Set INSTA_QUOTE_OCC_PYTHON or create a conda env named occ.'
    )
    return sys.executable


_PYTHON = _find_python()


def _run(cmd: list, label: str, dry_run: bool = False) -> bool:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"  {' '.join(str(c) for c in cmd)}")
    print(f"{'=' * 60}")

    if dry_run:
        print("  [DRY RUN] -- skipping execution")
        return True

    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR), capture_output=False)
    if result.returncode != 0:
        print(f"\n  [FAILED] {label} exited with code {result.returncode}")
        return False

    print(f"  [OK] {label}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Run the full CNC pipeline from STEP to PDF program sheet and quote.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('step_file', help='Path to the STEP file (absolute or relative to this script)')
    parser.add_argument('--material', default='aluminium', help='Workpiece material (default: aluminium)')
    parser.add_argument('--part-name', default=None, help='Part name for program sheet header')
    parser.add_argument('--programmer', default='CNC-AI', help='Programmer name for program sheet header')
    parser.add_argument('--revision', default='A', help='Revision letter (default: A)')
    parser.add_argument('--max-rpm', default='10000', help='Machine max spindle RPM (default: 10000)')
    parser.add_argument('--coolant', default='through_spindle', choices=['through_spindle', 'flood', 'mist', 'dry'], help='Coolant type (default: through_spindle)')
    parser.add_argument('--machine', default='milling', choices=['milling', 'turning', 'both'], help='Machine preference (default: milling)')
    parser.add_argument('--out-dir', default=None, help='Output directory (default: same as STEP file)')
    parser.add_argument('--from-step', type=int, default=1, help='Resume from step N (1-10, default: 1 = full run)')
    parser.add_argument('--qty', type=int, default=1, help='Quote quantity (default: 1)')
    parser.add_argument('--rfq', default=None, help='Optional RFQ JSON for finishes, inspection, shipping, etc.')
    parser.add_argument('--quote-config', default=None, help='Optional quote rules JSON override path')
    parser.add_argument('--price-book', default=None, help='Optional quote price book JSON override path')
    parser.add_argument('--skip-setup-views', action='store_true', help='Skip setup view rendering step')
    parser.add_argument('--dry-run', action='store_true', help='Print commands without executing')
    args = parser.parse_args()

    step_path = Path(args.step_file)
    if not step_path.is_absolute():
        step_path = SCRIPT_DIR / step_path
    step_path = step_path.resolve()

    if not args.dry_run and not step_path.exists():
        print(f"ERROR: STEP file not found: {step_path}")
        sys.exit(1)

    base = step_path.stem
    out_dir = Path(args.out_dir) if args.out_dir else step_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    part_name = args.part_name or base

    f_features = out_dir / f'{base}_features.json'
    f_clustered = out_dir / f'{base}_clustered.json'
    f_classified = out_dir / f'{base}_classified.json'
    f_processes = out_dir / f'{base}_processes.json'
    f_setups = out_dir / f'{base}_setups.json'
    f_tools = out_dir / f'{base}_tools.json'
    f_params = out_dir / f'{base}_params.json'
    f_pdf = out_dir / f'{base}_program_sheet.pdf'
    f_quote = out_dir / f'{base}_quote.json'
    d_views = out_dir / f'{base}_setup_views'
    py = _PYTHON

    quote_cmd = [
        py, str(SCRIPT_DIR / 'quote_estimation.py'),
        str(f_params),
        str(f_quote),
        '--features', str(f_features),
        '--qty', str(args.qty),
    ]
    if args.rfq:
        quote_cmd.extend(['--rfq', args.rfq])
    if args.quote_config:
        quote_cmd.extend(['--config', args.quote_config])
    if args.price_book:
        quote_cmd.extend(['--price-book', args.price_book])

    steps = [
        (1, 'Step 1/10 -- extract_features', [py, str(SCRIPT_DIR / 'extract_features.py'), str(step_path), str(f_features)]),
        (2, 'Step 2/10 -- cluster_features', [py, str(SCRIPT_DIR / 'cluster_features.py'), str(f_features), str(f_clustered)]),
        (3, 'Step 3/10 -- classify_features', [py, str(SCRIPT_DIR / 'classify_features.py'), str(f_clustered), str(f_classified)]),
        (4, 'Step 4/10 -- process_selection', [py, str(SCRIPT_DIR / 'process_selection.py'), str(f_classified), str(f_processes), '--machine', args.machine, '--material', args.material]),
        (5, 'Step 5/10 -- setup_planning', [py, str(SCRIPT_DIR / 'setup_planning.py'), str(f_processes), str(f_setups)]),
    ]
    if not args.skip_setup_views:
        steps.append((6, 'Step 6/10 -- setup_view_renderer', [py, str(SCRIPT_DIR / 'setup_view_renderer.py'), str(step_path), str(f_setups), str(d_views)]))
    else:
        print('  Setup views: skipped by user request')

    steps.extend([
        (7, 'Step 7/10 -- tool_selection', [py, str(SCRIPT_DIR / 'tool_selection.py'), str(f_setups), str(f_tools), '--material', args.material]),
        (8, 'Step 8/10 -- parameter_calculation', [py, str(SCRIPT_DIR / 'parameter_calculation.py'), str(f_tools), str(f_params), '--max-rpm', args.max_rpm, '--coolant', args.coolant]),
        (9, 'Step 9/10 -- program_sheet', [py, str(SCRIPT_DIR / 'program_sheet.py'), str(f_params), str(f_pdf), '--part-name', part_name, '--programmer', args.programmer, '--revision', args.revision]),
        (10, 'Step 10/10 -- quote_estimation', quote_cmd),
    ])

    print(f"\nCNC Pipeline -- {base}")
    print(f"  STEP       : {step_path}")
    print(f"  Output dir : {out_dir}")
    print(f"  Material   : {args.material}")
    print(f"  Quantity   : {args.qty}")
    print(f"  Starting at step {args.from_step}")

    for step_num, label, cmd in steps:
        if step_num < args.from_step:
            print(f"  [SKIP] {label}")
            continue

        ok = _run(cmd, label, dry_run=args.dry_run)
        if not ok:
            print(f"\nPipeline aborted at {label}.")
            print(f"Fix the issue above, then resume with:  --from-step {step_num}")
            sys.exit(1)

    print(f"\n{'=' * 60}")
    print('  PIPELINE COMPLETE')
    print(f'  PDF  : {f_pdf}')
    print(f'  Quote: {f_quote}')
    print(f"{'=' * 60}\n")


if __name__ == '__main__':
    main()
