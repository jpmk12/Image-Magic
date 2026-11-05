"""Command line: image-magic init | sync | scan | verify | status | app

The desktop app (image-magic app, or the installed Image Magic shortcut) does
the same things with buttons; both share the same settings.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, catalog, sync
from .settings import Settings


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


def _settings(args) -> Settings:
    s = Settings.load()
    if args.library:
        s.library_path = str(args.library)
    return s


def cmd_init(args) -> int:
    s = _settings(args)
    s.apple_id = args.apple_id or s.apple_id or input("Apple ID (email): ").strip()
    s.save()
    s.originals.mkdir(parents=True, exist_ok=True)
    catalog.connect(s.db).close()
    print(f"Library ready at {s.library}")
    print(f"Settings saved to {Settings.path()}")
    print("Next:  image-magic sync --test")
    return 0


def cmd_sync(args) -> int:
    s = _settings(args)
    if not s.apple_id:
        sys.exit("No Apple ID set. Run:  image-magic init")
    if args.adp:
        s.advanced_data_protection = True
    s.originals.mkdir(parents=True, exist_ok=True)
    mode = "test" if args.test else "quick" if args.quick else "full"
    cmd = sync.build_command(sync.find_icloudpd(), s, mode=mode,
                             dry_run=args.dry_run, auth_only=args.auth_only)
    print("Downloading from iCloud (copy only, nothing is deleted from iCloud).")
    print("You may be asked for your Apple ID password and the code shown on your iPhone.")
    code = sync.run_interactive(cmd)
    if code != 0:
        print(f"icloudpd exited with code {code}. The catalog was not updated.")
        return code
    if args.dry_run or args.auth_only:
        return 0
    conn = catalog.connect(s.db)
    catalog.set_meta(conn, "last_sync", catalog.now_iso())
    conn.commit()
    return _scan_and_report(conn, s.originals)


def cmd_scan(args) -> int:
    s = _settings(args)
    return _scan_and_report(catalog.connect(s.db), s.originals)


def _scan_and_report(conn, originals: Path) -> int:
    print("Cataloging files...")
    r = catalog.scan(conn, originals, progress=_progress("scanned"))
    print(f"{r.new:,} new, {r.updated:,} changed, {r.total:,} total, {r.missing:,} missing")
    print(f"Places looked up: {r.places_added:,}; trips found: {r.trips:,}")
    if r.skipped:
        print(f"Ignored {len(r.skipped)} non-media file(s), e.g. {r.skipped[0]}")
    return 1 if r.missing else 0


def cmd_verify(args) -> int:
    s = _settings(args)
    conn = catalog.connect(s.db)
    print("Checking every file against its checksum...")
    r = catalog.verify(conn, s.originals, progress=_progress("verified"))
    print(f"{r.ok:,} OK, {len(r.changed)} changed/corrupt, {len(r.missing)} missing")
    for p in r.changed[:20]:
        print(f"  CHANGED  {p}")
    for p in r.missing[:20]:
        print(f"  MISSING  {p}")
    return 0 if not (r.changed or r.missing) else 1


def cmd_status(args) -> int:
    s = _settings(args)
    st = catalog.stats(catalog.connect(s.db))
    print(f"Library:      {s.library}")
    print(f"Photos:       {st['photos']:,}")
    print(f"Videos:       {st['videos']:,}  (+{st['live_videos']:,} Live Photo clips)")
    print(f"Size:         {_human_bytes(st['bytes'])}")
    print(f"With GPS:     {st['with_gps']:,}")
    print(f"Duplicates:   {st['duplicates']:,}")
    print(f"Missing:      {st['missing']:,}")
    if st["first"]:
        print(f"Date range:   {st['first'][:10]} to {st['last'][:10]}")
    for key in ("last_sync", "last_scan", "last_verify"):
        print(f"{key.replace('_', ' ').capitalize() + ':':<14}{st[key] or 'never'}")
    return 0


def cmd_app(args) -> int:
    from .app import main as app_main
    return app_main([])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="image-magic", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--library", type=Path, default=None,
                   help="Library folder for this command (default: from settings)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init", help="Create a library and save your Apple ID")
    s.add_argument("--apple-id")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("sync", help="Download from iCloud (copy only), then catalog")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--test", action="store_true", help="Only the 20 most recent items")
    g.add_argument("--quick", action="store_true",
                   help="Stop after reaching photos you already have (fast incremental sync)")
    s.add_argument("--adp", action="store_true", help="Account uses Advanced Data Protection")
    s.add_argument("--dry-run", action="store_true", help="List what would be downloaded")
    s.add_argument("--auth-only", action="store_true", help="Just sign in and save the session")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("scan", help="Catalog files already in the library (no download)")
    s.set_defaults(func=cmd_scan)
    s = sub.add_parser("verify", help="Re-check every file against its checksum")
    s.set_defaults(func=cmd_verify)
    s = sub.add_parser("status", help="Show library totals")
    s.set_defaults(func=cmd_status)
    s = sub.add_parser("app", help="Open the Image Magic app in your browser")
    s.set_defaults(func=cmd_app)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.library:
        args.library = args.library.expanduser()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
