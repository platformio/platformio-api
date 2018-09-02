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

from sys import exit as sys_exit

import click
import requests

from platformio_api import __version__, config, maintenance
from platformio_api.database import sync_db
from platformio_api.github_terrier import GithubTerrier
from platformio_api.web import app


@click.group()
@click.version_option(__version__, prog_name="PlatformIO-API")
def cli():
    pass


@cli.command()
def syncdb():
    sync_db()
    click.echo("The database has been successfully synchronized!")


@cli.command()
def pendinglibs():
    maintenance.process_pending_libs()


@cli.command()
def synclibs():
    maintenance.sync_libs()


@cli.command()
@click.argument('lib_id', type=int)
def sync_lib(lib_id):
    maintenance.sync_lib_by_id(lib_id)


@cli.command()
def sync_arduino_libs():
    maintenance.sync_arduino_libs()


@cli.command()
def rotatelibsdlstats():
    maintenance.rotate_libs_dlstats()


@cli.command("run")
def runserver():
    app.run(debug=True, reloader=True)


@cli.command()
@click.argument('lib_ids', type=int, nargs=-1)
def deletelibs(lib_ids):
    for lib_id in lib_ids:
        maintenance.delete_library(lib_id)


@cli.command()
@click.argument('version_ids', type=int, nargs=-1)
def deletelibversion(version_ids):
    for version_id in version_ids:
        maintenance.delete_lib_version(version_id)


@cli.command()
@click.option("--keep-versions", type=int)
def cleanuplibversions(keep_versions):
    maintenance.cleanup_lib_versions(keep_versions)


@cli.command()
def optimisesyncperiod():
    maintenance.optimise_sync_period()


@cli.command()
def purge_cache():
    maintenance.purge_cache()


@cli.command()
@click.argument('search_query', type=str, nargs=1)
@click.option('--min-repo-stars', type=int, default=5)
def githubterrier(search_query, min_repo_stars):
    gh = GithubTerrier(config['GITHUB_LOGIN'], config['GITHUB_PASSWORD'],
                       search_query, min_repo_stars)
    gh.run()


def main():
    # https://urllib3.readthedocs.org
    # /en/latest/security.html#insecureplatformwarning
    requests.packages.urllib3.disable_warnings()

    cli()


if __name__ == "__main__":
    sys_exit(main())
