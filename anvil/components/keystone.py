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

import io

from urlparse import urlunparse

import yaml

from anvil import cfg
from anvil import colorizer
from anvil import component as comp
from anvil import importer
from anvil import log as logging
from anvil import shell as sh
from anvil import utils

from anvil.components import db

LOG = logging.getLogger(__name__)

# This db will be dropped then created
DB_NAME = "keystone"

# Subdirs of the git checkout
BIN_DIR = "bin"

# This yaml file controls keystone initialization
INIT_WHAT_FN = 'init_what.yaml'

# Simple confs
ROOT_CONF = "keystone.conf"
ROOT_SOURCE_FN = "keystone.conf.sample"
LOGGING_CONF = "logging.conf"
LOGGING_SOURCE_FN = 'logging.conf.sample'
POLICY_JSON = 'policy.json'
CONFIGS = [ROOT_CONF, LOGGING_CONF, POLICY_JSON]

# Sync db command
SYNC_DB_CMD = [sh.joinpths('%BIN_DIR%', 'keystone-manage'),
                '--config-file=%s' % (sh.joinpths('%CONFIG_DIR%', ROOT_CONF)),
                '--debug', '-v',
                # Available commands:
                # db_sync: Sync the database.
                # export_legacy_catalog: Export the service catalog from a legacy database.
                # import_legacy: Import a legacy database.
                # import_nova_auth: Import a dump of nova auth data into keystone.
                'db_sync']

# What to start
APP_NAME = 'keystone-all'
APP_OPTIONS = {
    APP_NAME: ['--config-file=%s' % (sh.joinpths('%CONFIG_DIR%', ROOT_CONF)),
                "--debug", '-v',
                '--log-config=%s' % (sh.joinpths('%CONFIG_DIR%', LOGGING_CONF))],
}


class KeystoneUninstaller(comp.PythonUninstallComponent):
    def __init__(self, *args, **kargs):
        comp.PythonUninstallComponent.__init__(self, *args, **kargs)


class KeystoneInstaller(comp.PythonInstallComponent):
    def __init__(self, *args, **kargs):
        comp.PythonInstallComponent.__init__(self, *args, **kargs)
        self.bin_dir = sh.joinpths(self.app_dir, BIN_DIR)

    def _get_download_locations(self):
        places = list()
        places.append({
            'uri': ("git", "keystone_repo"),
            'branch': ("git", "keystone_branch"),
        })
        return places

    def post_install(self):
        comp.PythonInstallComponent.post_install(self)
        self._setup_db()
        self._sync_db()

    def known_options(self):
        return set(['swift', 'quantum'])

    def _sync_db(self):
        LOG.info("Syncing keystone to database: %s", colorizer.quote(DB_NAME))
        mp = self._get_param_map(None)
        cmds = [{'cmd': SYNC_DB_CMD}]
        utils.execute_template(*cmds, cwd=self.bin_dir, params=mp)

    def _get_config_files(self):
        return list(CONFIGS)

    def _setup_db(self):
        db.drop_db(self.cfg, self.distro, DB_NAME)
        db.create_db(self.cfg, self.distro, DB_NAME, utf8=True)

    def _get_source_config(self, config_fn):
        real_fn = config_fn
        if config_fn == LOGGING_CONF:
            real_fn = LOGGING_SOURCE_FN
        elif config_fn == ROOT_CONF:
            real_fn = ROOT_SOURCE_FN
        fn = sh.joinpths(self.app_dir, 'etc', real_fn)
        return (fn, sh.load_file(fn))

    def _config_adjust_logging(self, contents, fn):
        with io.BytesIO(contents) as stream:
            config = cfg.IgnoreMissingConfigParser()
            config.readfp(stream)
            config.set('logger_root', 'level', 'DEBUG')
            config.set('logger_root', 'handlers', "devel,production")
            contents = config.stringify(fn)
        return contents

    def _config_param_replace(self, config_fn, contents, parameters):
        if config_fn in [ROOT_CONF, LOGGING_CONF]:
            # We handle these ourselves
            return contents
        else:
            return comp.PythonInstallComponent._config_param_replace(self, config_fn, contents, parameters)

    def _config_adjust_root(self, contents, fn):
        params = get_shared_params(self.cfg)
        with io.BytesIO(contents) as stream:
            config = cfg.IgnoreMissingConfigParser()
            config.readfp(stream)
            config.set('DEFAULT', 'admin_token', params['service_token'])
            config.set('DEFAULT', 'admin_port', params['endpoints']['admin']['port'])
            config.set('DEFAULT', 'public_port', params['endpoints']['public']['port'])
            config.set('DEFAULT', 'verbose', True)
            config.set('DEFAULT', 'debug', True)
            config.set('catalog', 'driver', 'keystone.catalog.backends.sql.Catalog')
            config.remove_option('DEFAULT', 'log_config')
            config.set('sql', 'connection', db.fetch_dbdsn(self.cfg, DB_NAME, utf8=True))
            config.set('ec2', 'driver', "keystone.contrib.ec2.backends.sql.Ec2")
            config.set('filter:s3_extension', 'paste.filter_factory', "keystone.contrib.s3:S3Extension.factory")
            config.set('pipeline:admin_api', 'pipeline', ('token_auth admin_token_auth xml_body '
                            'json_body debug ec2_extension s3_extension crud_extension admin_service'))
            contents = config.stringify(fn)
        return contents

    def _config_adjust(self, contents, name):
        if name == ROOT_CONF:
            return self._config_adjust_root(contents, name)
        elif name == LOGGING_CONF:
            return self._config_adjust_logging(contents, name)
        else:
            return contents

    def warm_configs(self):
        get_shared_params(self.cfg)

    def _get_param_map(self, config_fn):
        # These be used to fill in the configuration/cmds +
        # params with actual values
        mp = comp.PythonInstallComponent._get_param_map(self, config_fn)
        mp['BIN_DIR'] = self.bin_dir
        mp['CONFIG_FILE'] = sh.joinpths(self.cfg_dir, ROOT_CONF)
        return mp


class KeystoneRuntime(comp.PythonRuntime):
    def __init__(self, *args, **kargs):
        comp.PythonRuntime.__init__(self, *args, **kargs)
        self.bin_dir = sh.joinpths(self.app_dir, BIN_DIR)
        self.wait_time = max(self.cfg.getint('DEFAULT', 'service_wait_seconds'), 1)
        self.init_fn = sh.joinpths(self.trace_dir, 'was-inited')
        self.init_what = yaml.load(utils.load_template(self.component_name, INIT_WHAT_FN)[1])

    def post_start(self):
        if not sh.isfile(self.init_fn):
            LOG.info("Waiting %s seconds so that keystone can start up before running first time init." % (self.wait_time))
            sh.sleep(self.wait_time)
            LOG.info("Running client commands to initialize keystone.")
            LOG.debug("Initializing with %s", self.init_what)
            # Late load since its using a client lib that is only avail after install...
            init_cls = importer.import_entry_point('anvil.helpers.initializers:Keystone')
            initer = init_cls(self.cfg)
            initer.initialize(**self.init_what)
            # Touching this makes sure that we don't init again
            # TODO add trace
            sh.touch_file(self.init_fn)
            LOG.info("If you wish to re-run initialization, delete %s", colorizer.quote(self.init_fn))

    def _get_apps_to_start(self):
        apps = list()
        for app_name in APP_OPTIONS.keys():
            apps.append({
                'name': app_name,
                'path': sh.joinpths(self.bin_dir, app_name),
            })
        return apps

    def _get_app_options(self, app):
        return APP_OPTIONS.get(app)


def get_shared_params(cfg, service_user=None):

    mp = dict()

    # Tenants and users
    mp['tenants'] = ['admin', 'service', 'demo']
    mp['users'] = ['admin', 'demo']

    mp['demo_tenant'] = 'demo'
    mp['demo_user'] = 'demo'

    mp['admin_tenant'] = 'admin'
    mp['admin_user'] = 'admin'

    mp['service_tenant'] = 'service'
    if service_user:
        mp['users'].append(service_user)
        mp['service_user'] = service_user

    # Tokens and passwords
    mp['service_token'] = cfg.get_password(
        "service_token",
        'the service admin token',
        )
    mp['admin_password'] = cfg.get_password(
        'horizon_keystone_admin',
        'the horizon and keystone admin',
        length=20,
        )
    mp['service_password'] = cfg.get_password(
        'service_password',
        'service authentication',
        )

    host_ip = cfg.get('host', 'ip')

    # Components of the admin endpoint
    keystone_auth_host = cfg.getdefaulted('keystone', 'keystone_auth_host', host_ip)
    keystone_auth_port = cfg.getdefaulted('keystone', 'keystone_auth_port', '35357')
    keystone_auth_proto = cfg.getdefaulted('keystone', 'keystone_auth_protocol', 'http')
    keystone_auth_uri = utils.make_url(keystone_auth_proto,
                            keystone_auth_host, keystone_auth_port, path="v2.0")

    # Components of the public+internal endpoint
    keystone_service_host = cfg.getdefaulted('keystone', 'keystone_service_host', host_ip)
    keystone_service_port = cfg.getdefaulted('keystone', 'keystone_service_port', '5000')
    keystone_service_proto = cfg.getdefaulted('keystone', 'keystone_service_protocol', 'http')
    keystone_service_uri = utils.make_url(keystone_service_proto,
                            keystone_service_host, keystone_service_port, path="v2.0")

    mp['endpoints'] = {
        'admin': {
            'uri': keystone_auth_uri,
            'port': keystone_auth_port,
            'protocol': keystone_auth_proto,
            'host': keystone_auth_host,
        },
        'public': {
            'uri': keystone_service_uri,
            'port': keystone_service_port,
            'protocol': keystone_service_proto,
            'host': keystone_service_host,
        },
    }
    mp['endpoints']['internal'] = dict(mp['endpoints']['public'])

    LOG.debug("Keystone shared params: %s", mp)

    return mp