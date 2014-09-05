# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

import json
from sys import exit as sys_exit
from urllib import unquote

from bottle import Bottle, request, response
from click import echo, group, version_option

from platformio_api import __version__, api
from platformio_api.crawler import (process_pending_libs, rotate_libs_dlstats,
                                    sync_libs)
from platformio_api.database import sync_db
from platformio_api.exception import APIBadRequest, APINotFound

# Web Application
app = application = Bottle()


def finalize_json_response(handler, kwargs):
    assert issubclass(handler, api.APIBase)
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
    except Exception as error:
        status = 500

    if error:
        item = dict(
            status=status,
            title=str(error)
        )
        result = dict(errors=[item])

    response.status = status
    return json.dumps(result)


@app.route("/lib/search")
def lib_search():
    args = dict(
        query=unquote(request.query.query[:100]),
        page=int(request.query.page) if request.query.page else 0,
        per_page=int(request.query.per_page) if request.query.per_page else 0
    )
    return finalize_json_response(api.LibSearchAPI, args)


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

#
# Command Line Interface
#


@group()
@version_option(__version__, prog_name="PlatformIO-API")
def cli():
    pass


@cli.command()
def syncdb():
    sync_db()
    echo("The database has been successfully synchronized!")


@cli.command()
def pendinglibs():
    process_pending_libs()


@cli.command()
def synclibs():
    sync_libs()


@cli.command()
def rotatelibsdlstats():
    rotate_libs_dlstats()


@cli.command("run")
def runserver():
    app.run(debug=True, reloader=True)


def main():
    cli()


if __name__ == "__main__":
    sys_exit(main())
