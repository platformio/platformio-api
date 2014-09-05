# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

import atexit

from bottle import hook
from sqlalchemy import create_engine, DDL, event, exc, literal
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import Pool
from sqlalchemy.sql.expression import ClauseElement

from platformio_api import config

engine = create_engine(config['SQLALCHEMY_DATABASE_URI'],
                       pool_recycle=3600)
engine.execute("SET time_zone = '+00:00'")

db_session = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=engine)
)

Base = declarative_base()
Base.query = db_session.query_property()


atexit.register(lambda: db_session.close())


@event.listens_for(Pool, "checkout")
def ping_connection(dbapi_connection, connection_record, connection_proxy):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SELECT 1")
    except:
        raise exc.DisconnectionError()
    cursor.close()


@hook("after_request")
def enable_cors():
    db_session.remove()


class Match(ClauseElement):
    def __init__(self, columns, value):
        self.columns = columns
        self.value = literal(value)


@compiles(Match)
def _match(element, compiler, **kw):
    return "MATCH (%s) AGAINST (%s IN BOOLEAN MODE)" % (
        ", ".join(compiler.process(c, **kw) for c in element.columns),
        compiler.process(element.value))


def sync_db():
    from platformio_api.models import LibFTS

    event.listen(
        LibFTS.__table__,
        "after_create",
        DDL("ALTER TABLE %s ADD FULLTEXT(name, description, keywords)" %
            LibFTS.__tablename__)
    )

    Base.metadata.create_all(bind=engine)
