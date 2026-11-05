"""Command line: image-magic init | sync | scan | verify | status"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, catalog, sync
from .config import Config, db_path, default_library_path, originals_dir


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"  # pragma: no cover


def _progress(label: str):
    def report(done: int, total: int) -> None:
        print(f"  {label}: {done:,}/{total:,}", flush=True)
    return report


def cmd_init(args) -> int:
    lib: Path = args.library
    cfg = Config.load(lib)
    cfg.apple_id = args.apple_id or cfg.apple_id or input("Apple ID (email): ").strip()
    cfg.save(lib)
    originals_dir(lib).mkdir(parents=True, exist_ok=True)
    catalog.connect(db_path(lib)).close()
    print(f"Library ready at {lib}")
    print("Next:  image-magic sync")
    return 0


def _require_config(lib: Path) -> Config:
    cfg = Config.load(lib)
    if not cfg.apple_id:
        sys.exit(f"No library set up at {lib}. Run:  image-magic init")
    return cfg


def cmd_sync(args) -> int:
    lib: Path = args.library
    cfg = _require_config(lib)
    originals = originals_dir(lib)
    originals.mkdir(parents=True, exist_ok=True)
    cmd = sync.build_command(
        sync.find_icloudpd(), cfg.apple_id, originals,
        recent=args.recent, until_found=args.until_found,
        adp=args.adp, dry_run=args.dry_run, auth_only=args.auth_only,
    )
    print("Downloading from iCloud. You may be asked for your password and a 2FA code.")
    code = sync.run(cmd)
    if code != 0:
        print(f"icloudpd exited with code {code}. The catalog was not updated.")
        return code
    if args.dry_run or args.auth_only:
        return 0
    conn = catalog.connect(db_path(lib))
    catalog.set_meta(conn, "last_sync", catalog.now_iso())
    conn.commit()
    return _scan_and_report(conn, originals)


def cmd_scan(args) -> int:
    lib: Path = args.library
    conn = catalog.connect(db_path(lib))
    return _scan_and_report(conn, originals_dir(lib))


def _scan_and_report(conn, originals: Path) -> int:
    print("Cataloging files...")
    r = catalog.scan(conn, originals, progress=_progress("scanned"))
    print(f"{r.new:,} new, {r.updated:,} changed, {r.total:,} total, {r.missing:,} missing")
    if r.skipped:
        print(f"Ignored {len(r.skipped)} non-media file(s), e.g. {r.skipped[0]}")
    return 1 if r.missing else 0


def cmd_verify(args) -> int:
    lib: Path = args.library
    conn = catalog.connect(db_path(lib))
    print("Checking every file against its checksum...")
    r = catalog.verify(conn, originals_dir(lib), progress=_progress("verified"))
    print(f"{r.ok:,} OK, {len(r.changed)} changed/corrupt, {len(r.missing)} missing")
    for p in r.changed[:20]:
        print(f"  CHANGED  {p}")
    for p in r.missing[:20]:
        print(f"  MISSING  {p}")
    return 0 if not (r.changed or r.missing) else 1


def cmd_status(args) -> int:
    lib: Path = args.library
    conn = catalog.connect(db_path(lib))
    s = catalog.stats(conn)
    print(f"Library:      {lib}")
    print(f"Photos:       {s['photos']:,}")
    print(f"Videos:       {s['videos']:,}  (+{s['live_videos']:,} Live Photo clips)")
    print(f"Size:         {_human_bytes(s['bytes'])}")
    print(f"With GPS:     {s['with_gps']:,}")
    print(f"Duplicates:   {s['duplicates']:,}")
    print(f"Missing:      {s['missing']:,}")
    if s["first"]:
        print(f"Date range:   {s['first'][:10]} to {s['last'][:10]}")
    for key in ("last_sync", "last_scan", "last_verify"):
        print(f"{key.replace('_', ' ').capitalize() + ':':<14}{s[key] or 'never'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="image-magic", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument(
        "--library", type=Path, default=default_library_path(),
        help="Library folder (default: %(default)s, or $IMAGE_MAGIC_LIBRARY)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="Create a library and save your Apple ID")
    s.add_argument("--apple-id")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("sync", help="Download new photos from iCloud, then catalog them")
    s.add_argument("--recent", type=int, help="Only the N most recent items (good for a test run)")
    s.add_argument("--until-found", type=int,
                   help="Stop after N already-downloaded items in a row (fast incremental sync)")
    s.add_argument("--adp", action="store_true",
                   help="Account uses Advanced Data Protection: request web access consent")
    s.add_argument("--dry-run", action="store_true", help="List what would be downloaded")
    s.add_argument("--auth-only", action="store_true", help="Just sign in and save the session")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("scan", help="Catalog files already in the library (no download)")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("verify", help="Re-check every file against its checksum")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("status", help="Show library totals")
    s.set_defaults(func=cmd_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.library = args.library.expanduser()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
