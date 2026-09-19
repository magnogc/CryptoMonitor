from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json

def save_snapshot(result, destination: str | Path) -> None:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)

    def conv(obj):
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        raise TypeError

    path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2, default=conv),
        encoding="utf-8",
    )
