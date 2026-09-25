import configparser
import os
import sqlalchemy as db
from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy_utils import database_exists, create_database

def ConnectionString(database) -> str:
    configuration_path = os.getenv('CONFIGURATION_PATH', '.')
    configuration_file = os.path.join(configuration_path, 'wg-dashboard.ini')
    parser = configparser.ConfigParser(strict=False)
    with open(configuration_file, "r") as config_file:
        parser.read_file(config_file)

    if parser.get("Database", "type") == "postgresql":
        cn = URL.create(
            "postgresql+psycopg",
            username=parser.get("Database", "username"),
            password=parser.get("Database", "password"),
            host=parser.get("Database", "host"),
            port=parser.getint("Database", "port") if parser.get("Database", "port", fallback="") else None,
            database=database,
        )
    elif parser.get("Database", "type") == "mysql":
        cn = URL.create(
            "mysql+pymysql",
            username=parser.get("Database", "username"),
            password=parser.get("Database", "password"),
            host=parser.get("Database", "host"),
            port=parser.getint("Database", "port") if parser.get("Database", "port", fallback="") else None,
            database=database,
        )
    else:
        sqlite_path = os.path.join(configuration_path, "db")
        os.makedirs(sqlite_path, exist_ok=True)
        cn = URL.create("sqlite", database=os.path.join(sqlite_path, f"{database}.db"))
    try:
        if not database_exists(cn):
            create_database(cn)
    except Exception as exc:
        raise RuntimeError(f"Unable to initialize database '{database}'") from exc

    return cn.render_as_string(hide_password=False)


def CreateEngine(database) -> db.Engine:
    """Create a resilient SQLAlchemy engine shared by all dashboard modules."""
    connection_string = ConnectionString(database)

    if connection_string.startswith("sqlite:///"):
        engine = db.create_engine(
            connection_string,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        return engine

    return db.create_engine(connection_string, pool_pre_ping=True, pool_recycle=1800)
