"""
Connection helper for the My Movies 5 SQL Server database, via FreeTDS.

Requires /etc/freetds.conf with encryption=off (this SQL Server 2014
instance doesn't support FreeTDS's default encryption negotiation) - see
DEPLOYMENT.md.

Connects via SERVERNAME=mymovies (a named freetds.conf stanza), not an
inline SERVER=/PORT= pair: tested against the real server, the FreeTDS
ODBC driver here ignores the connection string's PORT keyword and falls
back to odbcinst.ini's default (1433), so the non-default port (11598)
only takes effect when it's baked into the freetds.conf stanza itself.
The "mymovies" stanza name is fixed - see the freetds.conf example in
DEPLOYMENT.md, which must use that exact section name.
"""

import logging

import pyodbc

logger = logging.getLogger(__name__)

_FREETDS_SECTION = "mymovies"


def get_connection(cfg: dict) -> pyodbc.Connection:
    """
    Open a connection to the My Movies SQL Server database described by
    cfg["mymovies"]. Raises pyodbc.Error (or a subclass) clearly on
    failure - callers should let it propagate or catch it explicitly,
    not swallow it silently.
    """
    mm = cfg["mymovies"]
    conn_str = (
        "DRIVER={FreeTDS};"
        f"SERVERNAME={_FREETDS_SECTION};"
        f"DATABASE={mm['database']};"
        f"UID={mm['username']};"
        f"PWD={mm['password']};"
        "TDS_Version=7.3;"
    )
    try:
        return pyodbc.connect(conn_str)
    except pyodbc.Error:
        logger.exception(
            "Failed to connect to My Movies SQL Server at %s:%s/%s",
            mm["server"], mm["port"], mm["database"],
        )
        raise


def test_connection(cfg: dict) -> dict:
    """
    Quick connectivity check - opens a connection, reads SQL Server's
    version string, closes it. Returns
    {"connected": bool, "error": str|None, "server_version": str|None}.
    Never raises.
    """
    try:
        conn = get_connection(cfg)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT @@VERSION")
            row = cursor.fetchone()
            server_version = row[0] if row else None
        finally:
            conn.close()
        return {"connected": True, "error": None, "server_version": server_version}
    except Exception as exc:
        return {"connected": False, "error": str(exc), "server_version": None}
