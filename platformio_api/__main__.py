# Copyright (C) Ivan Kravets <me@ikravets.com>
# See LICENSE for details.

from sys import exit as sys_exit

from click import echo, group, version_option

from platformio_api import __version__
from platformio_api.crawler import (process_pending_libs, rotate_libs_dlstats,
                                    sync_libs)
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
