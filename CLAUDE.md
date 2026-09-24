# CLAUDE.md

**The rules live in [AGENTS.md](AGENTS.md). Read it before changing anything.**

Short version: never delete listings (visibility is decided in `db.load_listings()`),
IDs must be stable (no `hash()`), one scorer (`scoring.score`) with whole-word
matching, and `python -m pytest -q` must pass.
