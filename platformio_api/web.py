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
import logging
from urllib import unquote

from bottle import Bottle, request, response

from platformio_api import api, config
from platformio_api.database import db_session
from platformio_api.exception import APIBadRequest, APINotFound


app = Bottle()
logger = logging.getLogger(__name__)


@app.hook("after_request")
def db_disconnect():
    db_session.close()


def finalize_json_response(handler, kwargs):
    assert issubclass(handler, api.APIBase)
    response.set_header("Access-Control-Allow-Origin",
                        config['API_CORS_ORIGIN'])
    response.set_header("Access-Control-Allow-Methods",
                        "GET, POST, PUT, DELETE, OPTIONS")
    response.set_header("Access-Control-Allow-Headers",
                        "Content-Type, Access-Control-Allow-Headers")
    response.set_header("content-type", "application/json")

    status = 200
    error = None
    result = None
    try:
        obj = handler(**kwargs)
        result = obj.get_result()
    except APIBadRequest as error:
        status = 400
    except APINotFound as error:
        status = 404
    except Exception as e:
        status = 500
        error = "Internal server error"
        logger.exception(e)

    if error:
        item = dict(
            status=status,
            title=str(error)
        )
        result = dict(errors=[item])

    response.status = status
    return json.dumps(result)


@app.route("/", method="OPTIONS")
def cors(request):
    """ Preflighted request """
    response.set_header("Access-Control-Allow-Origin",
                        config['API_CORS_ORIGIN'])
    response.set_header("Access-Control-Allow-Methods",
                        "GET, POST, PUT, DELETE, OPTIONS")
    response.set_header("Access-Control-Allow-Headers",
                        "Content-Type, Access-Control-Allow-Headers")
    return None


@app.route("/boards")
def boards():
    return finalize_json_response(api.BoardsAPI, {})


@app.route("/frameworks")
def frameworks():
    return finalize_json_response(api.FrameworksAPI, {})


@app.route("/packages")
def packages():
    if "PlatformIO/" in request.headers.get("User-Agent", ""):
        return finalize_json_response(api.PackagesManifestAPI, {})
    else:
        return finalize_json_response(api.PackagesAPI, {})


@app.route("/packages/manifest")
def packages_manifest():
    return finalize_json_response(api.PackagesManifestAPI, {})


@app.route("/platforms")
def platforms():
    return finalize_json_response(api.PlatformsAPI, {})


@app.route("/lib/search")
def lib_search():
    args = dict(
        query=unquote(request.query.query[:255]),
        page=int(request.query.page) if request.query.page else 0,
        # perpage=int(request.query.perpage) if request.query.perpage else 0
    )
    return finalize_json_response(api.LibSearchAPI, args)


# Uncomment the line below in order to enable the Solr search
# @app.route("/lib/search_v2")
def lib_search_solr():
    strict = request.query.strict
    if strict.lower() in ['0', 'false', 'off']:
        strict = False
    args = dict(
        query=unquote(request.query.query[:255]),
        page=int(request.query.page) if request.query.page else 0,
        strict=bool(strict),
        # perpage=int(request.query.perpage) if request.query.perpage else 0
    )
    return finalize_json_response(api.LibSearchSolrAPI, args)


@app.route("/lib/examples")
def lib_examples():
    args = dict(
        query=unquote(request.query.query[:255]),
        page=int(request.query.page) if request.query.page else 0,
        # perpage=int(request.query.perpage) if request.query.perpage else 0
    )
    return finalize_json_response(api.LibExamplesAPI, args)


@app.route("/lib/info/<id_>")
def lib_info(id_):
    return finalize_json_response(api.LibInfoAPI, dict(id_=id_))


@app.route("/lib/download/<id_:int>")
def lib_download(id_):
    args = dict(
        id_=id_,
        ip=request.remote_addr,
        version=request.query.version,
        ci="CI/1" in request.headers.get("User-Agent", "")
    )
    return finalize_json_response(api.LibDownloadAPI, args)


@app.route("/lib/version/<ids:re:\d+(,\d+)*>")
def lib_version(ids):
    ids = [int(i) for i in ids.split(",")[:50]]
    return finalize_json_response(api.LibVersionAPI, dict(ids=ids))


@app.route("/lib/register", method="POST")
def lib_register():
    return finalize_json_response(
        api.LibRegisterAPI, dict(conf_url=request.forms.get("config_url")))


@app.route("/lib/stats")
def lib_stats():
    return finalize_json_response(api.LibStatsAPI, {})
