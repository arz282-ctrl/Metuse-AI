"""Allow executing development seeds via `python -m database.seeds`."""

from database.seeds.seed_dev import main

if __name__ == "__main__":
    raise SystemExit(main())
