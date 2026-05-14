"""Copy original Stanford Town frontend assets and persona bootstrap memory
into ``backend/assets/`` of the stanford-town-vue project.

Layout produced under ``--target`` (default ``../backend/assets``):

    maze/the_ville/visuals/...          (PNGs, sprite sheets, .tmx, .json maps)
    maze/the_ville/matrix/...           (CSV matrices + maze_meta_info.json + special_blocks/)
    maze/the_ville/agent_history_init_n25.csv (and _n3 etc.)
    characters/*.png                    (per-character sprite sheets)
    personas/<base_set>/personas/<Name>/bootstrap_memory/...

Only the ``base_*`` persona sets are copied -- ``compressed_storage`` and other
demo runs are handled elsewhere by the M2 importer.

Usage::

    python scripts/copy_assets.py
    python scripts/copy_assets.py --source /path/to/examples/stanford_town --target backend/assets
    python scripts/copy_assets.py --dry-run
    python scripts/copy_assets.py --force

Stdlib only. Python 3.10+.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CopyStats:
    files_copied: int = 0
    files_skipped: int = 0
    bytes_copied: int = 0
    dirs_created: int = 0
    missing_sources: list[str] = field(default_factory=list)


def _same_file(src: Path, dst: Path) -> bool:
    """Return True if dst already mirrors src (size + mtime match within 1s)."""
    if not dst.exists():
        return False
    try:
        s = src.stat()
        d = dst.stat()
    except OSError:
        return False
    if s.st_size != d.st_size:
        return False
    # Filesystems differ in mtime resolution; treat within 1s as identical.
    return abs(s.st_mtime - d.st_mtime) <= 1.0


def _copy_file(src: Path, dst: Path, *, force: bool, dry_run: bool, stats: CopyStats) -> None:
    if dst.exists() and not force and _same_file(src, dst):
        stats.files_skipped += 1
        return
    if dry_run:
        print(f"  [dry-run] copy {src} -> {dst}")
        stats.files_copied += 1
        stats.bytes_copied += src.stat().st_size
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    stats.files_copied += 1
    stats.bytes_copied += src.stat().st_size


def _copy_tree(src: Path, dst: Path, *, force: bool, dry_run: bool, stats: CopyStats) -> None:
    """Recursively copy ``src`` into ``dst`` preserving structure."""
    if not src.exists():
        stats.missing_sources.append(str(src))
        print(f"  WARNING: source missing: {src}")
        return
    if not src.is_dir():
        _copy_file(src, dst, force=force, dry_run=dry_run, stats=stats)
        return
    for entry in src.rglob("*"):
        if entry.is_dir():
            continue
        rel = entry.relative_to(src)
        target = dst / rel
        _copy_file(entry, target, force=force, dry_run=dry_run, stats=stats)


def _copy_top_level_files(src_dir: Path, dst_dir: Path, pattern: str, *,
                          force: bool, dry_run: bool, stats: CopyStats) -> None:
    """Copy files in ``src_dir`` matching ``pattern`` (non-recursive)."""
    if not src_dir.exists():
        return
    for entry in src_dir.glob(pattern):
        if entry.is_file():
            _copy_file(entry, dst_dir / entry.name, force=force, dry_run=dry_run, stats=stats)


def copy_maze(source_root: Path, target_root: Path, *, force: bool, dry_run: bool,
              stats: CopyStats) -> None:
    """Copy the_ville visuals + matrix + top-level CSVs."""
    src_ville = source_root / "frontend" / "static_dirs" / "assets" / "the_ville"
    dst_ville = target_root / "maze" / "the_ville"
    print(f"[maze] {src_ville} -> {dst_ville}")
    if not src_ville.exists():
        stats.missing_sources.append(str(src_ville))
        print(f"  WARNING: source missing: {src_ville}")
        return
    _copy_tree(src_ville / "visuals", dst_ville / "visuals",
               force=force, dry_run=dry_run, stats=stats)
    _copy_tree(src_ville / "matrix", dst_ville / "matrix",
               force=force, dry_run=dry_run, stats=stats)
    _copy_top_level_files(src_ville, dst_ville, "*.csv",
                          force=force, dry_run=dry_run, stats=stats)


def copy_characters(source_root: Path, target_root: Path, *, force: bool, dry_run: bool,
                    stats: CopyStats) -> None:
    src = source_root / "frontend" / "static_dirs" / "assets" / "characters"
    dst = target_root / "characters"
    print(f"[characters] {src} -> {dst}")
    if not src.exists():
        stats.missing_sources.append(str(src))
        print(f"  WARNING: source missing: {src}")
        return
    _copy_tree(src, dst, force=force, dry_run=dry_run, stats=stats)


def copy_personas(source_root: Path, target_root: Path, *, force: bool, dry_run: bool,
                  stats: CopyStats) -> None:
    """Copy each ``base_*`` persona set's bootstrap_memory tree."""
    storage = source_root / "storage"
    dst_root = target_root / "personas"
    print(f"[personas] {storage} -> {dst_root}")
    if not storage.exists():
        stats.missing_sources.append(str(storage))
        print(f"  WARNING: source missing: {storage}")
        return
    base_sets = sorted(p for p in storage.iterdir() if p.is_dir() and p.name.startswith("base_"))
    if not base_sets:
        print("  WARNING: no base_* persona sets found under storage/")
        return
    for base in base_sets:
        personas_dir = base / "personas"
        if not personas_dir.exists():
            print(f"  WARNING: {base.name} has no personas/ subdir, skipping")
            continue
        print(f"  - {base.name}: copying bootstrap_memory for each persona")
        for persona in sorted(p for p in personas_dir.iterdir() if p.is_dir()):
            boot_src = persona / "bootstrap_memory"
            if not boot_src.exists():
                # Some sets store memory directly under persona dir; copy anything we find.
                print(f"    WARNING: {persona.name} has no bootstrap_memory/, skipping")
                continue
            boot_dst = dst_root / base.name / "personas" / persona.name / "bootstrap_memory"
            _copy_tree(boot_src, boot_dst, force=force, dry_run=dry_run, stats=stats)


def _human_bytes(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    f = float(n)
    for u in units:
        if f < 1024.0 or u == units[-1]:
            return f"{f:.1f} {u}"
        f /= 1024.0
    return f"{n} B"


def main(argv: list[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    default_source = (script_dir.parent.parent / "examples" / "stanford_town").resolve()
    default_target = (script_dir.parent / "backend" / "assets").resolve()

    parser = argparse.ArgumentParser(description="Copy Stanford Town assets into backend/assets/.")
    parser.add_argument("--source", type=Path, default=default_source,
                        help=f"Path to original examples/stanford_town (default: {default_source})")
    parser.add_argument("--target", type=Path, default=default_target,
                        help=f"Destination backend/assets dir (default: {default_target})")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite even when destination already matches.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be copied without writing.")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    target = args.target.resolve()

    print(f"source : {source}")
    print(f"target : {target}")
    print(f"force  : {args.force}")
    print(f"dry-run: {args.dry_run}")
    print()

    if not source.exists():
        print(f"ERROR: source does not exist: {source}", file=sys.stderr)
        return 2

    if not args.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    stats = CopyStats()
    copy_maze(source, target, force=args.force, dry_run=args.dry_run, stats=stats)
    copy_characters(source, target, force=args.force, dry_run=args.dry_run, stats=stats)
    copy_personas(source, target, force=args.force, dry_run=args.dry_run, stats=stats)

    print()
    print("=" * 60)
    print(f"SUMMARY: copied {stats.files_copied} files "
          f"({_human_bytes(stats.bytes_copied)}), "
          f"skipped {stats.files_skipped} up-to-date files.")
    if stats.missing_sources:
        print(f"WARNING: {len(stats.missing_sources)} expected source path(s) missing:")
        for p in stats.missing_sources:
            print(f"  - {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
