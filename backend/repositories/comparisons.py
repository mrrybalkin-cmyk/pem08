"""Validated comparison persistence; caller owns commit/rollback."""

import json
from sqlalchemy.orm import Session
from backend.models.analysis import ComparisonResult
from backend.models.db import Comparison


def create_comparison(db: Session, *, competitor_ids: list[str], model_id: str,
                      result: ComparisonResult) -> Comparison:
    validated = ComparisonResult.model_validate_json(result.model_dump_json(), strict=True)
    row = Comparison(
        competitor_ids_json=json.dumps(competitor_ids, ensure_ascii=False, separators=(",", ":")),
        model_id=model_id, result_json=validated.model_dump_json(),
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get_comparison(db: Session, comparison_id: str) -> Comparison | None:
    return db.get(Comparison, comparison_id)
