# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

from sys import exit as sys_exit

import requests
from click import argument, echo, group, version_option

from platformio_api import __version__, maintenance
from platformio_api.database import sync_db
from platformio_api.web import app


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
    maintenance.process_pending_libs()


@cli.command()
def synclibs():
    maintenance.sync_libs()


@cli.command()
def rotatelibsdlstats():
    maintenance.rotate_libs_dlstats()


@cli.command("run")
def runserver():
    app.run(debug=True, reloader=True)


@cli.command()
@argument('lib_id', type=int)
def deletelib(lib_id):
    maintenance.delete_library(lib_id)


@cli.command()
@argument("keep_versions", type=int)
def cleanuplibversions(keep_versions):
    maintenance.cleanup_lib_versions(keep_versions)


@cli.command()
def optimisesyncperiod():
    maintenance.optimise_sync_period()


def main():
    # https://urllib3.readthedocs.org
    # /en/latest/security.html#insecureplatformwarning
    requests.packages.urllib3.disable_warnings()

    cli()


if __name__ == "__main__":
    sys_exit(main())
