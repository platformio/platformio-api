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

from sys import exit as sys_exit

import requests
from click import argument, echo, group, version_option

from platformio_api import __version__, maintenance, solr
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
@argument('lib_id', type=int)
def sync_lib(lib_id):
    maintenance.sync_lib_by_id(lib_id)


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


@cli.command()
def initialize_solr_for_libs():
    solr.delete_lib_fields()
    solr.add_lib_fields()
    solr.copy_fts_to_solr()


@cli.command()
def synchronize_libs_on_solr():
    solr.synchronize_libs_on_solr()


def main():
    # https://urllib3.readthedocs.org
    # /en/latest/security.html#insecureplatformwarning
    requests.packages.urllib3.disable_warnings()

    cli()


if __name__ == "__main__":
    sys_exit(main())
