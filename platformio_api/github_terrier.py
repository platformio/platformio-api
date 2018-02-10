# Copyright (c) 2014-present PlatformIO <contact@platformio.org>
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

import logging
import os
import shutil
import subprocess
from tempfile import mkdtemp
from time import sleep

import requests

from github import Github
from platformio_api import util

DEBUG = False
logging.basicConfig()
logger = logging.getLogger('git-terrier')
logger.setLevel(20)


# part 1: Get n-stars library
def get_github_libs(search_request, gh_user, gh_password, gh_stars):
    g = Github(gh_user, gh_password, per_page=1000)
    search_result = g.search_code(search_request)
    result = []
    counter = 0
    for lib in search_result:
        if lib.repository.stargazers_count >= gh_stars:
            url = ("https://raw.githubusercontent.com"
                   "/%s/%s/library.properties") % (
                       lib.repository.full_name, lib.repository.default_branch)
            logger.info(url)
            result.append(url)
            if DEBUG:
                counter += 1
                if counter == 5:
                    break
    return result


# part 2: Get all PIO libs
def get_pio_libs():
    page = 1
    result = []
    while True:
        search_result = requests.get(
            'http://api.platformio.org/lib/search?page=%d' % page).json()
        for lib in search_result['items']:
            lib_url = 'http://api.platformio.org/lib/info/%d' % lib['id']
            lib_info = requests.get(lib_url).json()
            result.append(lib_info['confurl'])
            logger.info("page  = %s  id = %s" % (page, lib['id']))
            logger.info("url = %s" % lib_info['confurl'])
            sleep(0.2)
        if (search_result["perpage"] * page) >= search_result["total"]:
            break
        page += 1
        if DEBUG:
            if page == 2:
                break
    return result


# part 3: Find new libs from github search list
def find_new_libs(gh_results, pio_results):
    pio_results = [
        x.strip().replace(".json", "").replace(".properties", "")
        for x in pio_results
    ]
    gh_results = [
        x.strip().replace(".json", "").replace(".properties", "")
        for x in gh_results
    ]
    return set(gh_results).difference(pio_results)


# part 4:ensure that found libs are arduino libs
def check_libs(lib_urls):
    results = []
    for lib_url in lib_urls:
        unzip_folder = mkdtemp()
        archive_path = os.path.join(unzip_folder, "master.zip")
        url_token = lib_url.split("/")
        util.download_file("https://github.com/%s/%s/archive/master.zip" %
                           (url_token[3], url_token[4]), archive_path)
        try:
            util.extract_archive(archive_path, unzip_folder)
            logger.info("Zip ok! for url: %s" % lib_url)
            file_extensions = set()
            for _, __, files in os.walk(unzip_folder):
                for lib_file in files:
                    file_extensions.add(os.path.splitext(lib_file)[1])
            if not set(['.c', '.cpp', '.h']).isdisjoint(file_extensions):
                if ".json" in file_extensions:
                    results.append(lib_url + ".json")
                else:
                    results.append(lib_url + ".properties")
        except WindowsError as e:
            logger.info(e)
        finally:
            shutil.rmtree(unzip_folder)
    return results


# part 5: register found libraries in Platformio
def register_new_libs(new_libs):
    for new_lib in new_libs:
        logger.info(new_lib)
        if not DEBUG:
            subprocess.call(["platformio", "lib", "register", new_lib])
