# vim: tabstop=4 shiftwidth=4 softtabstop=4

#    Copyright (C) 2012 Yahoo! Inc. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from pkg_resources import Requirement

from anvil import shell as sh

FREEZE_CMD = ['freeze', '--local']

# Cache of whats installed - 'uncached' as needed
_installed_cache = None


def uncache():
    global _installed_cache
    _installed_cache = None


def _list_installed(pip_how):
    cmd = [pip_how] + FREEZE_CMD
    (stdout, _stderr) = sh.execute(*cmd)
    installed = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Don't take editables either...
        if line.startswith('-e'):
            continue
        # We need to adjust the == that freeze produces
        # to instead have <= so that later when we ask
        # if a version matches it will say yes it does and
        # not just for exactly the same version
        if line.find('==') != -1:
            line = line.replace('==', '<=')
        try:
            installed.append(Requirement.parse(line))
        except ValueError:
            pass
    return installed


def _whats_installed(pip_how):
    global _installed_cache
    if _installed_cache is None:
        _installed_cache = _list_installed(pip_how)
    return _installed_cache


def is_installed(pip_how, name, version=None):
    if get_installed(pip_how, name, version):
        return True
    return False


def get_installed(pip_how, name, version=None):
    name_lc = name.lower()
    whats_there = _whats_installed(pip_how)
    for req in whats_there:
        if not (name_lc == req.key):
            continue
        if not version:
            return req
        if version in req:
            return req
    return None
