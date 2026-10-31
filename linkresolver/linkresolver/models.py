"""Data structures: every step of one resolution (hop / extract / click / note)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Step:
    kind: str                     # request | redirect | extract | click | note | blocked | error
    url: str = ""
    status: Optional[int] = None
    detail: str = ""
    elapsed: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "kind": self.kind,
            "url": self.url,
            "status": self.status,
            "detail": self.detail,
            "elapsed": round(self.elapsed, 3),
        }
        if self.extra:
            d["extra"] = self.extra
        return d


@dataclass
class Result:
    input_url: str
    final_url: str = ""
    ok: bool = False
    method: str = ""              # http | browser | rules | none
    blocked: bool = False
    note: str = ""
    steps: List[Step] = field(default_factory=list)
    elapsed: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input": self.input_url,
            "final": self.final_url,
            "ok": self.ok,
            "method": self.method,
            "blocked": self.blocked,
            "note": self.note,
            "elapsed": round(self.elapsed, 3),
            "steps": [s.to_dict() for s in self.steps],
        }
