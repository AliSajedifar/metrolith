# -*- coding: utf-8 -*-

"""
db_analysis.py
---------------
Detects database usage and schema files inside a repository.

Searches for:
- SQL schema files (schema.sql, init.sql)
- Migration folders (Flyway, Liquibase, Django migrations, Alembic,…)
- ORM models (SQLAlchemy, Django Models, Spring JPA Entities)
- docker-compose DB services
- SQLite / local DB files

This module does not execute SQL — it only detects presence.
"""

from pathlib import Path

from modules.repository_files import iter_repository_files, read_text


# ===============================================================
# PATTERNS FOR DB DETECTION
# ===============================================================
SQL_FILE_EXTENSIONS = {".sql"}
ORM_KEYWORDS = [
    "models.Model", "sqlalchemy", "ForeignKey", "OneToOneField",
    "@Entity", "@Table", "JpaRepository", "extends CrudRepository",
    "gorm.Model", "EntityFrameworkCore", "ActiveRecord::Base",
]
DB_SERVICES = [
    "postgresql", "postgres", "mysql", "mariadb", "mongodb", "mongo",
    "sqlserver", "mssql", "oracle", "cassandra", "couchbase", "sqlite", "redis",
]


# ===============================================================
# FUNCTION: DETECT SQL SCHEMA FILES
# ===============================================================
def detect_sql_schema(repo_path, inventory=None):
    schemas = []
    repo_path = Path(repo_path)

    for path in iter_repository_files(repo_path, extensions=SQL_FILE_EXTENSIONS, inventory=inventory):
        schemas.append(path.relative_to(repo_path).as_posix())
    return sorted(set(schemas))


# ===============================================================
# FUNCTION: DETECT ORM MODEL FILES
# ===============================================================
def detect_orm_definitions(repo_path, inventory=None):
    orm_files = []
    repo_path = Path(repo_path)

    extensions = {".py", ".java", ".kt", ".go", ".cs", ".rb", ".php"}
    for path in iter_repository_files(repo_path, extensions=extensions, include_tests=False, inventory=inventory):
        content = read_text(path, inventory=inventory)
        if content and any(keyword.lower() in content.lower() for keyword in ORM_KEYWORDS):
            orm_files.append(path.relative_to(repo_path).as_posix())
    return sorted(set(orm_files))


# ===============================================================
# FUNCTION: DETECT docker-compose DB SERVICES
# ===============================================================
def detect_docker_compose_dbs(repo_path, inventory=None):
    repo_path = Path(repo_path)

    detected = set()
    compose_names = {
        "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"
    }
    for path in iter_repository_files(repo_path, extensions={".yml", ".yaml"}, inventory=inventory):
        if path.name.lower() not in compose_names:
            continue
        content = read_text(path, inventory=inventory)
        if content:
            lowered = content.lower()
            detected.update(db for db in DB_SERVICES if db in lowered)
    aliases = {"postgresql": "postgres", "mongo": "mongodb", "mssql": "sqlserver"}
    return sorted({aliases.get(db, db) for db in detected})


# ===============================================================
# FUNCTION: DETECT SQLITE FILES
# ===============================================================
def detect_sqlite_files(repo_path, inventory=None):
    repo_path = Path(repo_path)
    sqlite_files = []

    for path in iter_repository_files(repo_path, extensions={".sqlite", ".sqlite3", ".db"}, inventory=inventory):
        sqlite_files.append(path.relative_to(repo_path).as_posix())
    return sorted(sqlite_files)


# ===============================================================
# MAIN FUNCTION
# ===============================================================
def detect_db_schema(repo_path, inventory=None):
    """
    Combines several detection strategies and returns
    a structured dictionary describing the repository's DB usage.
    """

    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repository path does not exist: {repo_path}")

    sql_files = detect_sql_schema(repo_path, inventory)
    orm_files = detect_orm_definitions(repo_path, inventory)
    sqlite_files = detect_sqlite_files(repo_path, inventory)
    compose_dbs = detect_docker_compose_dbs(repo_path, inventory)

    db_type = None

    # Determine DB type by signals
    if compose_dbs:
        db_type = compose_dbs[0]
    elif sqlite_files:
        db_type = "sqlite"
    else:
        combined = " ".join(sql_files + orm_files).lower()
        for candidate in (
            "postgres", "mysql", "mariadb", "mongodb", "sqlserver",
            "oracle", "cassandra", "sqlite", "redis",
        ):
            if candidate in combined:
                db_type = candidate
                break

    return {
        "db_type": db_type,
        "sql_schema_files": sql_files,
        "orm_definition_files": orm_files,
        "sqlite_files": sqlite_files,
        "docker_compose_detected_dbs": compose_dbs,
    }
