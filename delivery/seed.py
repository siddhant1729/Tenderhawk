"""Seed Ramesh's demo profile.  python -m delivery.seed  -> prints the profile id."""
import json
from pathlib import Path

from api.db import SessionLocal
from api.models import Profile

SEED = Path(__file__).parent / "seed" / "ramesh.json"


def main() -> None:
    data = json.loads(SEED.read_text())
    with SessionLocal() as db:
        p = db.query(Profile).filter_by(name=data["name"]).one_or_none()
        if p is None:
            p = Profile(**data)
            db.add(p)
        else:
            for k, v in data.items():
                setattr(p, k, v)
        db.commit()
        print(p.id)


if __name__ == "__main__":
    main()
