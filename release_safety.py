"""Release and schema compatibility checks (Gamma G7). Standard library only:
the updater and the start of app.main() may import it.

These checks do not authenticate a release. A manifest must come through a
trusted release channel and be verified independently before activation.
"""
import re


def require_supported_schema(db, maximum_supported):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if type(maximum_supported) is not int or maximum_supported < 0:
        raise ValueError("Invalid supported schema version")
    if version > maximum_supported:
        raise RuntimeError(
            f"Database schema v{version} exceeds this release's supported v{maximum_supported}. "
            "Do not downgrade or overwrite the database automatically."
        )
    return version


def validate_release_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("Release manifest required")
    commit = manifest.get("commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Full commit SHA required")
    if manifest.get("verification") != "passed":
        raise ValueError("Release has not passed required checks")
    lock_hash = manifest.get("dependency_lock_sha256")
    if not isinstance(lock_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", lock_hash):
        raise ValueError("Dependency lock fingerprint required")
    if not isinstance(manifest.get("verification_reference"), str) or not manifest["verification_reference"]:
        raise ValueError("Verification reference required")
    return manifest


def require_readable_schema(db, supported):
    """A newer database is refused only when it says older code cannot read it.

    Migrations are additive (AGENTS.md), so a previous release, which the
    updater rolls back to when a new one fails to start, can still read a newer
    schema. A migration that is not additive must record the oldest release
    that can read it in schema_compat.min_reader; a release below that refuses
    the database instead of using or downgrading it. Returns
    (user_version, newer: bool)."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if type(supported) is not int or supported < 0:
        raise ValueError("Invalid supported schema version")
    if version <= supported:
        return version, False
    row = None
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_compat'").fetchone():
        row = db.execute("SELECT MAX(min_reader) FROM schema_compat").fetchone()
    floor = row[0] if row and type(row[0]) is int else 0
    if floor > supported:
        require_supported_schema(db, supported)       # raises with the standard message
    return version, True
