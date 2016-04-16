# Copyright 2014-2015 Ivan Kravets <me@ikravets.com>
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

import json
from logging import getLogger

from requests import Session
from sqlalchemy import event

from platformio_api.database import db_session
from platformio_api.models import LibFTS, Libs
from platformio_api import config

logger = getLogger(__name__)


class SolrClientFactory(object):
    """Caches clients, so that queries to the same Solr instance will share
    connections.
    """
    instances = {}

    @classmethod
    def newClient(cls, base_url):
        if base_url not in cls.instances:
            cls.instances[base_url] = SolrClient(base_url)
        return cls.instances[base_url]


class SolrClient(object):
    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/') + '/'

        self.session = Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def search(self, query, params=None):
        params = params or {}
        payload = {
            'query': query,
        }
        return self.session.post(self.base_url + 'search',
                                 params=params,
                                 data=json.dumps(payload))

    def update(self, docs):
        return self.session.post(self.base_url + 'update',
                                 params={'commit': 'true'},
                                 data=json.dumps(docs))

    def schema(self, command):
        return self.session.post(self.base_url + 'schema',
                                 params={'wt': 'json'},
                                 data=json.dumps(command))


# Uncomment the line below to enable synchronization between DB and Solr
# @event.listens_for(db_session, 'after_flush')
def update_libs_on_solr(session, _):
    solr_client = SolrClientFactory.newClient(config["SOLR_LIBS_URI"])
    docs_to_update = []
    for fts in session.dirty | session.new:
        if isinstance(fts, LibFTS):
            docs_to_update.append(fts_to_dict(fts))
    if docs_to_update:
        solr_client.update(docs_to_update)

    doc_ids_to_delete = []
    for lib in session.deleted:
        if isinstance(lib, Libs):
            doc_ids_to_delete.append(str(lib.id))
    if doc_ids_to_delete:
        solr_client.update({"delete": {"id": " OR ".join(doc_ids_to_delete)}})


def synchronize_libs_on_solr():
    solr_client = SolrClientFactory.newClient(config["SOLR_LIBS_URI"])
    existing_lib_ids = [str(x) for x, in db_session.query(Libs.id).all()]
    if existing_lib_ids:
        # Delete all documents, except for those with specified ids
        solr_client.update({"delete": {
            "query": "*:* -id:(%s)" % " OR ".join(existing_lib_ids)
        }})
    else:
        logger.warn("There are no libraries in your DB.")

    copy_fts_to_solr()


def add_lib_fields():
    solr_client = SolrClientFactory.newClient(config["SOLR_LIBS_URI"])
    commands = [
        {'add-field': {
            'name': 'name',
            'type': 'string',
            'stored': True,
        }},
        {'add-field': {
            'name': 'description',
            'type': 'text_general',
            'multiValued': False,
            'stored': True,
        }},
    ]
    list_fields = ['keywords', 'examplefiles', 'authornames', 'frameworkslist',
                   'platformslist']
    for field in list_fields:
        commands.append({'add-field': {
            'name': field,
            'type': 'strings',
            'stored': True,
        }})

    for cmd in commands:
        logger.info('Added field. Response: %s'
                    % (solr_client.schema(cmd).json(),))


def delete_lib_fields():
    solr_client = SolrClientFactory.newClient(config["SOLR_LIBS_URI"])
    for field in ['keywords', 'examplefiles', 'authornames', 'frameworkslist',
                  'platformslist', 'name', 'description']:
        logger.info('Deleted field. Response: %s' % (solr_client.schema({
            'delete-field': {'name': field}
        }).json(),))


def copy_fts_to_solr():
    solr_client = SolrClientFactory.newClient(config["SOLR_LIBS_URI"])
    documents = []
    for lib_fts in db_session.query(LibFTS).order_by(LibFTS.lib_id).all():
        documents.append(fts_to_dict(lib_fts))
    return solr_client.update(documents)


def fts_to_dict(instance):
    """Convert an instance of LibFTS to a dict.

    :type instance: LibFTS
    :rtype: dict
    """
    return dict(
        id=instance.lib_id,
        name=instance.name,
        description=instance.description,
        keywords=instance.keywords.split(','),
        examplefiles=instance.examplefiles.split(','),
        authornames=instance.authornames.split(','),
        frameworkslist=instance.frameworkslist.split(','),
        platformslist=instance.platformslist.split(','),
    )
