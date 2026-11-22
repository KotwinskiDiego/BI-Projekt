"""
Pull Finwire API data into DuckDB with structured schema.

Data structure:
- Main tables: One per dataset with flattened record data
- Metadata table: Tracks dataset origins and pull history
- Architecture: Records from API are split into two layers:
  1. Data layer: Flattened fields from each record (f_* columns)
  2. Metadata layer: Pull timestamp, dataset info, raw record JSON
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import duckdb
except ImportError:
    raise ImportError("duckdb is required. Install with: pip install duckdb")

if TYPE_CHECKING:
    import duckdb


DEFAULT_API_BASE_URL = "https://public-api.finwire.pl"
DEFAULT_DUCKDB_PATH = "finwire_data.duckdb"


@dataclass
class DuckDBConfig:
    path: str


@dataclass
class ApiDataset:
    dataset_id: str
    endpoint: str


DUCKDB_CONFIG = DuckDBConfig(
    path=DEFAULT_DUCKDB_PATH,
)

API_DATASETS: list[ApiDataset] = [
    ApiDataset("snapshot", "/v1/snapshot"),
    ApiDataset("interest-rates", "/v1/series/interest-rate"),
    ApiDataset("cpi-monthly", "/v1/series/cpi?type=monthly&limit=600"),
    ApiDataset("cpi-yearly", "/v1/series/cpi?type=yearly"),
    ApiDataset("fx-table", "/v1/series/fx"),
    ApiDataset("public-debt", "/v1/public-debt"),
]


class ApiHttpClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 90,
        max_retries: int = 5,
        retry_base_delay_seconds: float = 1.5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_base_delay_seconds = retry_base_delay_seconds

    @staticmethod
    def _retry_after_seconds(exc: HTTPError) -> int | None:
        retry_after = exc.headers.get("Retry-After")
        if not retry_after:
            return None
        try:
            value = int(retry_after)
            return value if value >= 0 else None
        except ValueError:
            return None

    @staticmethod
    def _is_retryable_http(status_code: int, detail: str) -> bool:
        if status_code in {429, 500, 502, 503, 504}:
            return True
        if status_code == 403:
            body = detail.lower()
            return "error code: 1010" in body or "access denied" in body or "cloudflare" in body
        return False

    def get_json(self, endpoint: str) -> dict[str, Any]:
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        url = self.base_url + endpoint
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "finwire-api-etl/1.0 (+python urllib duckdb)",
            },
            method="GET",
        )
        raw: str | None = None

        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                break
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                should_retry = attempt < self.max_retries and self._is_retryable_http(exc.code, detail)
                if not should_retry:
                    raise RuntimeError(f"HTTP {exc.code} for GET {endpoint}: {detail}") from exc

                retry_after = self._retry_after_seconds(exc)
                delay = retry_after if retry_after is not None else self.retry_base_delay_seconds * (
                    2**attempt
                )
                print(
                    f"Retryable HTTP {exc.code} for GET {endpoint}; "
                    f"attempt {attempt + 1}/{self.max_retries + 1}. Sleeping {delay:.1f}s."
                )
                time.sleep(delay)
            except URLError as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Network error for GET {endpoint}: {exc}") from exc
                delay = self.retry_base_delay_seconds * (2**attempt)
                print(
                    f"Network error for GET {endpoint}; "
                    f"attempt {attempt + 1}/{self.max_retries + 1}. Sleeping {delay:.1f}s."
                )
                time.sleep(delay)

        if raw is None:
            raise RuntimeError(f"No response body for GET {endpoint} after retries")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON from GET {endpoint}: {raw[:500]}") from exc

        if not isinstance(payload, dict):
            raise RuntimeError(f"Unexpected response type from GET {endpoint}: {type(payload).__name__}")

        return payload


def dataset_table_name(dataset_id: str) -> str:
    """Generate a valid DuckDB table name from dataset ID."""
    normalized = re.sub(r"[^a-z0-9]+", "_", dataset_id.lower()).strip("_")
    if not normalized:
        normalized = "dataset"
    base = f"finwire_ds_{normalized}"
    if len(base) <= 128:
        return base
    suffix = hashlib.sha1(dataset_id.encode("utf-8")).hexdigest()[:8]
    return f"{base[:120]}_{suffix}"


def sanitize_column_name(raw: str) -> str:
    """Generate a valid DuckDB column name."""
    normalized = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    if not normalized:
        normalized = "field"
    col = f"f_{normalized}"
    if len(col) <= 128:
        return col
    suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{col[:120]}_{suffix}"


def value_to_text(value: Any) -> str | None:
    """Convert any value to text representation for storage."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def flatten_record_values(
    value: Any,
    path: str,
    out: dict[str, str | None],
    used_columns: set[str],
    max_depth: int = 3,
    current_depth: int = 0,
) -> None:
    """Recursively flatten nested record structures into flat columns.
    
    Arrays deeper than max_depth are stored as JSON strings instead of flattened.
    """
    if current_depth >= max_depth and isinstance(value, (list, dict)):
        column = sanitize_column_name(path if path else "value")
        if column in used_columns:
            suffix = hashlib.sha1(path.encode("utf-8")).hexdigest()[:6]
            column = f"{column[:120]}_{suffix}"
        used_columns.add(column)
        out[column] = json.dumps(value, ensure_ascii=False) if value else None
        return
    
    if isinstance(value, dict):
        for key, nested in value.items():
            next_path = f"{path}_{key}" if path else str(key)
            flatten_record_values(nested, next_path, out, used_columns, max_depth, current_depth + 1)
        return
    
    if isinstance(value, list):
        for idx, item in enumerate(value[:10]):
            next_path = f"{path}_{idx}"
            flatten_record_values(item, next_path, out, used_columns, max_depth, current_depth + 1)
        return

    column = sanitize_column_name(path if path else "value")
    if column in used_columns:
        suffix = hashlib.sha1(path.encode("utf-8")).hexdigest()[:6]
        column = f"{column[:120]}_{suffix}"
    used_columns.add(column)
    out[column] = value_to_text(value)


def record_to_columns(record: dict[str, Any]) -> dict[str, str | None]:
    """Flatten a record dict to flat columns."""
    columns: dict[str, str | None] = {}
    used: set[str] = set()
    for key, value in record.items():
        flatten_record_values(value, str(key), columns, used)
    return columns


BASE_COLUMNS = {
    "record_index",
    "record_id",
    "record_code",
    "record_date",
    "record_period",
    "record_year",
    "record_quarter",
    "run_id",
    "pulled_at",
    "dataset_id",
    "endpoint",
    "record",
    "response_info",
}


def extract_record_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Extract common metadata fields from record."""
    metadata: dict[str, Any] = {}
    
    if "id" in record:
        metadata["record_id"] = record["id"]
    if "code" in record:
        metadata["record_code"] = record["code"]
    if "date" in record:
        metadata["record_date"] = record["date"]
    if "period" in record:
        metadata["record_period"] = record["period"]
    if "year" in record:
        metadata["record_year"] = record["year"]
    if "quarter" in record:
        metadata["record_quarter"] = record["quarter"]
    
    return metadata


def extract_public_debt_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract records from public-debt response structure."""
    records = []
    
    for key, value in data.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    record = {"_section": key, **item}
                    records.append(record)
        elif isinstance(value, dict):
            record = {"_section": key, **value}
            records.append(record)
    
    return records if records else [data]


def extract_snapshot_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract records from snapshot response structure.
    
    Snapshot contains multiple sections (interestRates, inflation, currencies, etc).
    Each section can have entries or be a single object.
    """
    records = []
    
    for section_name, section_data in data.items():
        if isinstance(section_data, dict):
            if "entries" in section_data and isinstance(section_data["entries"], list):
                for entry in section_data["entries"]:
                    record = {"_section": section_name, **entry}
                    records.append(record)
            else:
                record = {"_section": section_name, **section_data}
                records.append(record)
        elif isinstance(section_data, list):
            for idx, item in enumerate(section_data):
                if isinstance(item, dict):
                    record = {"_section": section_name, "_index": idx, **item}
                    records.append(record)
    
    return records if records else [data]
    """Extract common metadata fields from record."""
    metadata: dict[str, Any] = {}
    
    if "id" in record:
        metadata["record_id"] = record["id"]
    if "code" in record:
        metadata["record_code"] = record["code"]
    if "date" in record:
        metadata["record_date"] = record["date"]
    if "period" in record:
        metadata["record_period"] = record["period"]
    if "year" in record:
        metadata["record_year"] = record["year"]
    if "quarter" in record:
        metadata["record_quarter"] = record["quarter"]
    
    return metadata


def ensure_dataset_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create metadata tables if they don't exist."""
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS seq_error_id START 1;
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS finwire_dataset_catalog (
            dataset_id VARCHAR PRIMARY KEY,
            endpoint VARCHAR NOT NULL,
            table_name VARCHAR NOT NULL,
            record_count BIGINT DEFAULT 0,
            last_pulled_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL
        );
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS finwire_pull_errors (
            error_id INTEGER PRIMARY KEY DEFAULT nextval('seq_error_id'),
            run_id VARCHAR NOT NULL,
            dataset_id VARCHAR,
            endpoint VARCHAR,
            error_message VARCHAR NOT NULL,
            occurred_at TIMESTAMP NOT NULL
        );
    """)


def ensure_dataset_table(
    conn: duckdb.DuckDBPyConnection,
    dataset_id: str,
    table_name: str,
    sample_record: dict[str, Any],
) -> set[str]:
    """Create dataset table with appropriate columns based on sample record."""
    
    if conn.execute(f"SELECT * FROM information_schema.tables WHERE table_name = '{table_name}'").fetchall():
        result = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
        return {row[1] for row in result}
    
    flattened = record_to_columns(sample_record)
    metadata = extract_record_metadata(sample_record)
    
    columns: dict[str, str] = {
        "record_index": "BIGINT",
        "record_id": "VARCHAR",
        "record_code": "VARCHAR",
        "record_date": "VARCHAR",
        "record_period": "VARCHAR",
        "record_year": "VARCHAR",
        "record_quarter": "VARCHAR",
        "run_id": "VARCHAR",
        "pulled_at": "TIMESTAMP",
        "dataset_id": "VARCHAR",
        "endpoint": "VARCHAR",
        "record": "JSON",
        "response_info": "JSON",
    }
    
    for col_name, col_value in flattened.items():
        columns[col_name] = "VARCHAR"
    
    col_defs = ", ".join([f"{name} {dtype}" for name, dtype in columns.items()])
    create_sql = f"CREATE TABLE {table_name} ({col_defs});"
    
    conn.execute(create_sql)
    
    conn.execute(f"""
        CREATE INDEX idx_{table_name}_dataset ON {table_name}(dataset_id);
    """)
    conn.execute(f"""
        CREATE INDEX idx_{table_name}_date ON {table_name}(pulled_at);
    """)
    
    return set(columns.keys())


def insert_records(
    conn: duckdb.DuckDBPyConnection,
    dataset_id: str,
    table_name: str,
    endpoint: str,
    records: list[dict[str, Any]],
    response_metadata: dict[str, Any],
    run_id: str,
    pulled_at: datetime,
) -> int:
    """Insert records into dataset table."""
    
    if not records:
        return 0
    
    existing_columns = set(col[1] for col in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall())
    
    inserted_count = 0
    for record_index, record in enumerate(records):
        flattened = record_to_columns(record)
        metadata = extract_record_metadata(record)
        
        all_values: dict[str, Any] = {
            "record_index": record_index,
            "run_id": run_id,
            "pulled_at": pulled_at,
            "dataset_id": dataset_id,
            "endpoint": endpoint,
            "record": json.dumps(record, ensure_ascii=False),
            "response_info": json.dumps(response_metadata, ensure_ascii=False),
        }
        
        all_values.update(metadata)
        all_values.update(flattened)
        
        columns_to_insert = [col for col in all_values.keys() if col in existing_columns]
        values = [all_values.get(col) for col in columns_to_insert]
        
        col_names = ", ".join(columns_to_insert)
        placeholders = ", ".join(["?" for _ in columns_to_insert])
        
        insert_sql = f"""
            INSERT INTO {table_name} ({col_names})
            VALUES ({placeholders});
        """
        
        conn.execute(insert_sql, values)
        inserted_count += 1
    
    return inserted_count


def process_dataset(
    client: ApiHttpClient,
    conn: duckdb.DuckDBPyConnection,
    dataset: ApiDataset,
    run_id: str,
    delay_seconds: float = 0.1,
) -> None:
    """Fetch and store a single dataset."""
    
    print(f"Processing dataset: {dataset.dataset_id}")
    
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    
    try:
        payload = client.get_json(dataset.endpoint)
    except RuntimeError as exc:
        print(f"Error fetching {dataset.dataset_id}: {exc}")
        conn.execute("""
            INSERT INTO finwire_pull_errors (run_id, dataset_id, endpoint, error_message, occurred_at)
            VALUES (?, ?, ?, ?, ?);
        """, (run_id, dataset.dataset_id, dataset.endpoint, str(exc), datetime.now(timezone.utc)))
        return
    
    # Extract records from various API response formats
    records: list[dict[str, Any]] = []
    data = payload.get("data", {})
    
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        if "entries" in data and isinstance(data["entries"], list):
            records = data["entries"]
        elif dataset.dataset_id == "snapshot":
            records = extract_snapshot_records(data)
        elif dataset.dataset_id == "public-debt":
            records = extract_public_debt_records(data)
        else:
            records = [data]
    
    if not records:
        print(f"No records found in {dataset.dataset_id}")
        return
    
    table_name = dataset_table_name(dataset.dataset_id)
    pulled_at = datetime.now(timezone.utc)
    
    ensure_dataset_table(conn, dataset.dataset_id, table_name, records[0])
    
    record_count = insert_records(
        conn,
        dataset.dataset_id,
        table_name,
        dataset.endpoint,
        records,
        {
            "summary": payload.get("summary"),
            "meta": payload.get("meta"),
        },
        run_id,
        pulled_at,
    )
    
    conn.execute("""
        INSERT INTO finwire_dataset_catalog (dataset_id, endpoint, table_name, record_count, last_pulled_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (dataset_id) DO UPDATE SET
            record_count = record_count + excluded.record_count,
            last_pulled_at = excluded.last_pulled_at;
    """, (dataset.dataset_id, dataset.endpoint, table_name, record_count, pulled_at, datetime.now(timezone.utc)))
    
    print(f"Inserted {record_count} records from {dataset.dataset_id} into {table_name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull Finwire API data into DuckDB"
    )
    parser.add_argument(
        "--db-path",
        default=DEFAULT_DUCKDB_PATH,
        help=f"Path to DuckDB database file (default: {DEFAULT_DUCKDB_PATH})",
    )
    parser.add_argument(
        "--api-base-url",
        default=DEFAULT_API_BASE_URL,
        help=f"Finwire API base URL (default: {DEFAULT_API_BASE_URL})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of datasets to pull (for testing)",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.5,
        help="Delay between API calls in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=90,
        help="HTTP request timeout in seconds (default: 90)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Max retries for failed requests (default: 5)",
    )
    
    args = parser.parse_args()
    
    client = ApiHttpClient(
        base_url=args.api_base_url,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
    )
    
    conn = duckdb.connect(args.db_path)
    
    try:
        ensure_dataset_tables(conn)
        
        run_id = str(uuid.uuid4())
        datasets_to_process = API_DATASETS[:args.limit] if args.limit else API_DATASETS
        
        for dataset in datasets_to_process:
            process_dataset(client, conn, dataset, run_id, args.delay_seconds)
        
        conn.commit()
        
        print(f"\nPull completed. Database: {args.db_path}")
        print("\nDataset summary:")
        summary = conn.execute("""
            SELECT dataset_id, table_name, record_count, last_pulled_at
            FROM finwire_dataset_catalog
            ORDER BY last_pulled_at DESC;
        """).fetchall()
        for row in summary:
            print(f"  {row[0]}: {row[2]} records in {row[1]} (pulled at {row[3]})")
        
    finally:
        conn.close()


if __name__ == "__main__":
    main()
