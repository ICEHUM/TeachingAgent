from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

info = json.loads((Path(os.environ["TEMP"]) / "teachingagent-02b-db.json").read_text(encoding="utf-8"))
url = (
    f"postgresql+psycopg://langgraph_cp:{quote_plus(info['langgraph_cp_password'])}"
    f"@{info['host']}:{info['port']}/{info['database']}"
)
engine = create_engine(url)
with engine.connect() as connection:
    tables = list(connection.execute(text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='langgraph_checkpoint' ORDER BY 1"
    )).scalars())
    count = None
    if "checkpoints" in tables:
        count = connection.execute(text("SELECT count(*) FROM langgraph_checkpoint.checkpoints")).scalar()
print(json.dumps({"tables": tables, "checkpoints": count}, indent=2))
engine.dispose()
