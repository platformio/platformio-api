# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

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


@app.route("/lib/search")
def lib_search():
    args = dict(
        query=unquote(request.query.query[:255]),
        page=int(request.query.page) if request.query.page else 0,
        # perpage=int(request.query.perpage) if request.query.perpage else 0
    )
    return finalize_json_response(api.LibSearchAPI, args)


@app.route("/lib/examples")
def lib_examples():
    args = dict(
        query=unquote(request.query.query[:255]),
        page=int(request.query.page) if request.query.page else 0,
        # perpage=int(request.query.perpage) if request.query.perpage else 0
    )
    return finalize_json_response(api.LibExamplesAPI, args)


@app.route("/lib/info/<name>")
def lib_info(name):
    return finalize_json_response(api.LibInfoAPI, dict(name=name[:50]))


@app.route("/lib/download/<name>")
def lib_download(name):
    args = dict(
        name=name,
        version=request.query.version,
        ip=request.remote_addr
    )
    return finalize_json_response(api.LibDownloadAPI, args)


@app.route("/lib/version/<names>")
def lib_version(names):
    return finalize_json_response(api.LibVersionAPI,
                                  dict(names=names.split(",")))


@app.route("/lib/register", method="POST")
def lib_register():
    return finalize_json_response(
        api.LibRegisterAPI, dict(conf_url=request.forms.get("config_url")))


@app.route("/lib/stats")
def lib_stats():
    return finalize_json_response(api.LibStatsAPI, {})
