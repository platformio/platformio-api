# Copyright 2014-present Ivan Kravets <me@ikravets.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import atexit

from sqlalchemy import DDL, create_engine, event, literal
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import scoped_session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.expression import ClauseElement

from platformio_api import config


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
        DDL("ALTER TABLE %s ADD FULLTEXT(name, description, keywords, "
            "headerslist, authornames, frameworkslist, platformslist)"
            % LibFTS.__tablename__)
    )

    Base.metadata.create_all(bind=engine)


engine = create_engine(config['SQLALCHEMY_DATABASE_URI'],
                       poolclass=NullPool)
engine.execute("SET time_zone = '+00:00'")

db_session = scoped_session(
    sessionmaker(autocommit=False, autoflush=False, bind=engine)
)

Base = declarative_base()
Base.query = db_session.query_property()


atexit.register(lambda: db_session.close())
