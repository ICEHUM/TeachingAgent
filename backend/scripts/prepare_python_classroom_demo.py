"""Give existing DEMO acceptance learners readable, explicitly synthetic names.

Only known UUIDs whose current names still carry an acceptance prefix are updated.
Code, attempts, snapshots, checks, submissions and grades are left untouched.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import uuid

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
logging.disable(logging.CRITICAL)

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from stage06_demo import guard, local_urls

from app.business.models import User

STAGE04 = uuid.UUID("ed5996c0-d346-47ae-9f16-7a5fb5249127")
STAGE02 = uuid.UUID("8b758387-f591-4244-8108-7505be8e8de2")
RUNNER_TEMP = uuid.UUID("7892041e-7499-4259-81e5-b803339863fd")
RUNNER_OFFICIAL = uuid.UUID("c3677719-712b-42b1-911d-20633958c458")

DEMO_NAMES = {
    (STAGE04, "passing_correct"): "示例学生·林悦",
    (STAGE04, "passing_boundary"): "示例学生·陈嘉",
    (STAGE04, "passing_public"): "示例学生·周琪",
    (STAGE04, "traversal_correct"): "示例学生·何安",
    (STAGE04, "traversal_duplicate"): "示例学生·许言",
    (STAGE04, "traversal_structure"): "示例学生·郭宁",
    (STAGE02, "boundary_error"): "示例学生·孙晨",
    (STAGE02, "correct"): "示例学生·唐悦",
    (RUNNER_TEMP, "boundary_error"): "示例学生·李航",
    (RUNNER_TEMP, "correct"): "示例学生·冯欣",
    (RUNNER_OFFICIAL, "boundary_error"): "示例学生·赵一",
    (RUNNER_OFFICIAL, "correct"): "示例学生·吴雨",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-stage02b-config", action="store_true")
    args = parser.parse_args()
    if not args.local_stage02b_config:
        raise RuntimeError("Explicit --local-stage02b-config is required")
    database_url = local_urls()[0]
    guard(database_url)
    engine = create_engine(database_url)
    changed = already_curated = 0
    try:
        with Session(engine) as session:
            for (namespace, sample_key), display_name in DEMO_NAMES.items():
                learner = session.get(User, str(uuid.uuid5(namespace, f"user-{sample_key}")))
                if learner is None:
                    continue
                if learner.display_name == display_name:
                    already_curated += 1
                elif learner.display_name.startswith(("阶段04验收｜", "Runner 验收｜", "演示验收｜")):
                    learner.display_name = display_name
                    changed += 1
            session.commit()
    finally:
        engine.dispose()
    print(json.dumps({"renamed_demo_learners": changed, "already_curated": already_curated}, ensure_ascii=False))


if __name__ == "__main__":
    main()
