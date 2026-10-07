from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from addex_core.models import Base


def test_schema_compiles_for_postgres():
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        CreateTable(table).compile(dialect=dialect)


def test_no_stream_payload_columns():
    """Ground rule: never store stream URLs, magnets or hashes."""
    forbidden = ("url", "magnet", "hash", "infohash")
    cols = Base.metadata.tables["availability"].columns
    assert not [c.name for c in cols if any(f in c.name.lower() for f in forbidden)]
