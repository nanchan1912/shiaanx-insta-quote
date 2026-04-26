"""
run_pipeline.py
---------------
Convenience runner for the full CNC feature-to-program-sheet pipeline.

Runs all eight steps in order, passing outputs between steps automatically.

Usage
-----
    python run_pipeline.py <step_file> [options]

    python run_pipeline.py "Botlabs Hinge/MIRROR_2.3MCYH2_2.STEP"
    python run_pipeline.py part.step --material mild_steel --part-name "Bracket"

Options
-------
    --material    aluminium | mild_steel | stainless_steel  (default: aluminium)
    --part-name   "Part Name"     (default: STEP filename without extension)
    --programmer  "Name"          (default: CNC-AI)
    --revision    A               (default: A)
    --max-rpm     10000           (default: 10000)
    --coolant     through_spindle | flood | mist | dry  (default: through_spindle)
    --machine     milling | turning | both              (default: milling)
    --out-dir     path            (default: same directory as STEP file)
    --from-step   N               (resume from step N, e.g. 3 to re-run from classify)
    --dry-run     print commands without executing

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
"""

import sys
import os
import subprocess
import argparse
import logging
import time
from datetime import datetime
from pathlib import Path


def _setup_logging(log_dir: str) -> str:
    """Set up file + console logging. Returns log file path."""
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(log_dir, f'pipeline_{ts}.log')

    fmt = '%(asctime)s  %(levelname)-8s  %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(log_path, encoding='utf-8'),
            logging.StreamHandler(),   # also print to console
        ]
    )
    return log_path


# ---------------------------------------------------------------------------
# Locate script directory (all pipeline scripts live here)
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.resolve()


# ---------------------------------------------------------------------------
# Resolve the correct Python interpreter
# ---------------------------------------------------------------------------
# The pipeline requires the 'occ' conda environment (OCC + reportlab).
# If the current Python is missing OCC, fall back to the known occ env path.

_OCC_ENV_PYTHON = Path(
    r'C:\Users\Siddhant Gupta\miniconda3\envs\occ\python.exe'
)

def _find_python() -> str:
    """Return path to a Python that has OCC available."""
    # Check if current Python already has OCC
    try:
        import OCC  # noqa: F401
        return sys.executable
    except ImportError:
        pass

    # Fall back to known occ env
    if _OCC_ENV_PYTHON.exists():
        return str(_OCC_ENV_PYTHON)

    # Last resort: current Python (will fail at runtime with a clear error)
    print(
        'WARNING: OCC not found in current Python and occ env not found at:\n'
        f'  {_OCC_ENV_PYTHON}\n'
        'Run:  conda activate occ  before running this script.'
    )
    return sys.executable


_PYTHON = _find_python()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list, label: str, dry_run: bool = False) -> bool:
    """Run a command, log progress and timing, return True on success."""
    log = logging.getLogger(__name__)

    log.info(f"Starting: {label}")
    log.info(f"  CMD: {' '.join(str(c) for c in cmd)}")

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"  {' '.join(str(c) for c in cmd)}")
    print(f"{'='*60}")

    if dry_run:
        print("  [DRY RUN] -- skipping execution")
        log.info(f"[DRY RUN] Skipped: {label}")
        return True

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(SCRIPT_DIR),
            capture_output=False,  # let output stream to terminal
        )
    except Exception:
        import traceback
        log.error(f"Exception running {label}:\n{traceback.format_exc()}")
        return False

    elapsed = time.time() - t0

    if result.returncode != 0:
        print(f"\n  [FAILED] {label} exited with code {result.returncode}")
        log.error(f"FAILED: {label} — exit code {result.returncode} — {elapsed:.1f}s")
        return False

    print(f"  [OK] {label}")
    log.info(f"Done in {elapsed:.1f}s: {label}")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Run the full CNC pipeline from STEP to PDF program sheet.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('step_file',
                        help='Path to the STEP file (absolute or relative to this script)')
    parser.add_argument('--material',   default='aluminium',
                        help='Workpiece material (default: aluminium)')
    parser.add_argument('--part-name',  default=None,
                        help='Part name for program sheet header')
    parser.add_argument('--programmer', default='CNC-AI',
                        help='Programmer name for program sheet header')
    parser.add_argument('--revision',   default='A',
                        help='Revision letter (default: A)')
    parser.add_argument('--max-rpm',    default='10000',
                        help='Machine max spindle RPM (default: 10000)')
    parser.add_argument('--coolant',    default='through_spindle',
                        choices=['through_spindle', 'flood', 'mist', 'dry'],
                        help='Coolant type (default: through_spindle)')
    parser.add_argument('--machine',    default='milling',
                        choices=['milling', 'turning', 'both'],
                        help='Machine preference (default: milling)')
    parser.add_argument('--out-dir',    default=None,
                        help='Output directory (default: same as STEP file)')
    parser.add_argument('--from-step',  type=int, default=1,
                        help='Resume from step N (1-9, default: 1 = full run)')
    parser.add_argument('--dry-run',    action='store_true',
                        help='Print commands without executing')

    args = parser.parse_args()

    # Resolve STEP file path
    step_path = Path(args.step_file)
    if not step_path.is_absolute():
        step_path = SCRIPT_DIR / step_path
    step_path = step_path.resolve()

    if not args.dry_run and not step_path.exists():
        print(f"ERROR: STEP file not found: {step_path}")
        sys.exit(1)

    base = step_path.stem                           # e.g. MIRROR_2.3MCYH2_2
    out_dir = Path(args.out_dir) if args.out_dir else step_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    part_name = args.part_name or base

    # Output file paths
    f_features  = out_dir / f'{base}_features.json'
    f_clustered = out_dir / f'{base}_clustered.json'
    f_classified= out_dir / f'{base}_classified.json'
    f_processes = out_dir / f'{base}_processes.json'
    f_setups    = out_dir / f'{base}_setups.json'
    f_tools     = out_dir / f'{base}_tools.json'
    f_params    = out_dir / f'{base}_params.json'
    f_pdf       = out_dir / f'{base}_program_sheet.pdf'
    d_views     = out_dir / f'{base}_setup_views'

    py = _PYTHON

    # Step definitions: (step_number, label, command_list)
    steps = [
        (1, 'Step 1/9 -- extract_features', [
            py, str(SCRIPT_DIR / 'extract_features.py'),
            str(step_path),
            str(f_features),
        ]),
        (2, 'Step 2/9 -- cluster_features', [
            py, str(SCRIPT_DIR / 'cluster_features.py'),
            str(f_features),
            str(f_clustered),
        ]),
        (3, 'Step 3/9 -- classify_features', [
            py, str(SCRIPT_DIR / 'classify_features.py'),
            str(f_clustered),
            str(f_classified),
        ]),
        (4, 'Step 4/9 -- process_selection', [
            py, str(SCRIPT_DIR / 'process_selection.py'),
            str(f_classified),
            str(f_processes),
            '--machine', args.machine,
            '--material', args.material,
        ]),
        (5, 'Step 5/9 -- setup_planning', [
            py, str(SCRIPT_DIR / 'setup_planning.py'),
            str(f_processes),
            str(f_setups),
        ]),
        (6, 'Step 6/9 -- setup_view_renderer', [
            py, str(SCRIPT_DIR / 'setup_view_renderer.py'),
            str(step_path),
            str(f_setups),
            str(d_views),
        ]),
        (7, 'Step 7/9 -- tool_selection', [
            py, str(SCRIPT_DIR / 'tool_selection.py'),
            str(f_setups),
            str(f_tools),
            '--material', args.material,
        ]),
        (8, 'Step 8/9 -- parameter_calculation', [
            py, str(SCRIPT_DIR / 'parameter_calculation.py'),
            str(f_tools),
            str(f_params),
            '--max-rpm', args.max_rpm,
            '--coolant', args.coolant,
        ]),
        (9, 'Step 9/9 -- program_sheet', [
            py, str(SCRIPT_DIR / 'program_sheet.py'),
            str(f_params),
            str(f_pdf),
            '--part-name', part_name,
            '--programmer', args.programmer,
            '--revision', args.revision,
        ]),
    ]

    # Initialise logging
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    log_path = _setup_logging(log_dir)
    log = logging.getLogger(__name__)
    log.info(f'Pipeline log: {log_path}')

    print(f"\nCNC Pipeline -- {base}")
    print(f"  STEP       : {step_path}")
    print(f"  Output dir : {out_dir}")
    print(f"  Material   : {args.material}")
    print(f"  Starting at step {args.from_step}")
    log.info(f"Pipeline start — part: {base} | material: {args.material} | "
             f"from_step: {args.from_step}")

    pipeline_t0 = time.time()

    for step_num, label, cmd in steps:
        if step_num < args.from_step:
            print(f"  [SKIP] {label}")
            log.info(f"Skipped (--from-step {args.from_step}): {label}")
            continue

        ok = _run(cmd, label, dry_run=args.dry_run)
        if not ok:
            print(f"\nPipeline aborted at {label}.")
            print(f"Fix the issue above, then resume with:  --from-step {step_num}")
            log.error(f"Pipeline ABORTED at: {label} — total elapsed: "
                      f"{time.time()-pipeline_t0:.1f}s")
            sys.exit(1)

    total_elapsed = time.time() - pipeline_t0
    print(f"\n{'='*60}")
    print(f"  PIPELINE COMPLETE")
    print(f"  PDF: {f_pdf}")
    print(f"{'='*60}\n")
    log.info(f"Pipeline COMPLETE — PDF: {f_pdf} — total time: {total_elapsed:.1f}s")


if __name__ == '__main__':
    main()
