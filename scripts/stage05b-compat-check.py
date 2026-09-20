import json
import os
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

info = json.loads((Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8"))
url = (
    f"postgresql+psycopg://teaching_app:{quote_plus(info['teaching_app_password'])}"
    f"@{info['host']}:{info['port']}/{info['database']}"
)
engine = create_engine(url)
with engine.connect() as connection:
    attempt = dict(connection.execute(text(
        "SELECT id, state_version, status FROM teaching_business.attempts WHERE id = :id"
    ), {"id": "ef885ba8-cd80-4946-a7a0-65c23f303039"}).mappings().one())
    intervention = dict(connection.execute(text(
        "SELECT id, status, allow_l2 FROM teaching_business.interventions WHERE id = :id"
    ), {"id": "5aa75d37-349d-4ae7-ada8-dbdbea6a5287"}).mappings().one())
    counts = dict(connection.execute(text(
        """
        SELECT
          (SELECT count(*) FROM teaching_business.attempts) AS attempts,
          (SELECT count(*) FROM teaching_business.snapshots) AS snapshots,
          (SELECT count(*) FROM teaching_business.requirement_results) AS requirement_results,
          (SELECT count(*) FROM teaching_business.teaching_events) AS teaching_events,
          (SELECT count(*) FROM teaching_business.interventions) AS interventions,
          (SELECT count(*) FROM teaching_business.submissions) AS submissions,
          (SELECT count(*) FROM teaching_business.formal_grades) AS grades
        """
    )).mappings().one())
    version = connection.execute(text("SELECT version_num FROM teaching_business.alembic_version")).scalar()
print(json.dumps({"version": version, "05a_attempt": attempt, "05a_intervention": intervention, "counts": counts}, indent=2, default=str))
engine.dispose()
