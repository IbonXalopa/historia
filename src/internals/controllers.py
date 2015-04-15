#!/usr/local/bin/python3
# encoding: utf-8
"""
contollers.py

This files is part of the internals package to handle controllers and other internal functions..


Created by Aaron Crosman on 2015-02-01

    This file is part of historia.

    historia is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    historia is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with historia.  If not, see <http://www.gnu.org/licenses/>.

"""

import sys, os, os.path
import datetime
import logging, logging.config
import json

from .exceptions import *

from database import system_db, user, session, user_db
from database import core_data_objects
import database.exceptions

from .web import *

class HistoriaCoreController(object):
    
    def __init__(self, config_location = '../config'):
        self.config = {}
        self.database = None
        self.interface = None
        self.active_users = {}
        self.active_user_databases = {}
        self.patterns = []
        
        
        # TODO: Move to a config file.
        self.routers = {
            'system' : {
                user.HistoriaUser.machine_type: {
                    'login':{
                        'parameters': ['name', 'password'],
                        'function'  : self.process_login
                        'type'      : 'POST'
                    },
                    'logout' :{
                        'parameters': [],
                        'function'  : self.end_session,
                        'type'      : 'GET'
                        
                    },
                    'create' : {
                        'parameters': ['name', 'password', 'email'],
                        'function'  : user.HistoriaUser, # this is probably wrong
                        'type'      : 'POST',
                        'permissions': ['admin']
                    },
                    'edit' : {
                        'parameters': ['name', 'password', 'email'],
                        'function'  : user.HistoriaUser, # this IS wrong
                        'type'      : 'POST',
                        'permissions': ['admin', 'owner']
                    },
                    'delete': {
                        'parameters': ['id'],
                        'function'  : user.HistoriaUser,
                        'type'      : 'POST',
                        'permissions': ['admin']
                    },
                    'info' : {
                        'parameters': ['id'],
                        'function'  : user.HistoriaUser,
                        'type'      : 'GET',
                        'permissions': ['admin', 'owner']
                    }
                },
                'status': {
                    'get': {
                        'parameters': [],
                        'function'  : self.system_status,
                        'type': 'GET'
                        'permissions': ['admin']
                    }
                }
            }
            'database': {
                '@dbid' : {
                    'fetch': {
                        '@oid' : {
                            'type' : 'GET',
                            'permissions' : ['admin', 'owner'],
                        }
                    },
                    'update': {
                        '@oid': {
                            'permissions': ['admin', 'owner'],
                            'type' : 'POST'
                        }
                    }
                }
            }
        }
        
        self.request_patterns(reset=True)
        
        self.load_configuration(config_location)
        
        try:
            self.connect_to_master_database()
        except database.exceptions.DataConnectionError as err:
            self.logger.error("Unable to connect to master database with current settings.")
        
    def load_configuration(self, file_location):
        """Load the configuration files for Historia"""
        default_file =  os.path.join(file_location,"default.json")
        override_file =  os.path.join(file_location,"historia.json")
        override_status = {}
        
        try:
            with open(default_file, 'rt') as f:
                base_config = json.load(f)
        except Exception as err:
            raise ConfigurationLoadError("Unable to load default.json configuration file in {0} triggered by {1}".format(file_location, err))
        
        override_config = {}
        try:
            with open(override_file, 'rt') as f:
                override_config = json.load(f)
        except Exception as err:
            # we'll log this failure later, but since it's a reasonable condition continue
            # This really should be a warning...
            override_status = { 
                'Success': False,
                'Message': "Failed to load configuration override file {0} due to {1}. This may be an expected result, conitnuing.".format(override_file, str(err))
            }
        else:
           override_status = {
                 'Success': True,
                 'Message': "Override file loaded from {0}".format(override_file)
           }
        
        self.config = self._deep_dict_merge(base_config, override_config)
        
        if self.config is None:
            raise ConfigurationLoadError("Unable to load default.json configuration file in {0}. Resulting dictionary is empty".format(file_location))
        
        logging.config.dictConfig(self.config['logging'])
        
        self.logger = logging.getLogger('historia.ctrl')
        
        
        if override_status['Success']:
            self.logger.info(override_status['Message'])
        else:
            self.logger.warn(override_status['Message'])
        
    
    def setup_web_interface(self):
        self.logger.info("Setting up user interface.")
        self.interface = HistoriaServer()
    
    def start_interface(self):
        self.logger.info("Starting interface.")
        self.interface.startup(self)
    
    def process_request(self, request_handler, session, object, request, parameters):
        """Process requests from a request_handler and send back the results"""
        if object not in self.routers:
            request_handler.send_error(404, "Requested Resource not found")
            return
            
        if session is None:
            request_handler.send_home()
            return
        
        try:
            result = self.routers[object][request](session, parameters)
            if result is None:
                request_handler.send_error(403, "Opperation failed")
            elif result is True or result is False:
                request_handler.send_record(session,{'Response':result})
            else:
                request_handler.send_record(session,result)
        except Exception as err:
            self.logger.error("Error handling request: {0}.{1} with {2} for {3}. Error: {4}".format(object, request, parameters, session.id, err))
            request_handler.send_error(500, "Error processing request")
        
    
    def process_login(self, session, parameters):
        if 'user' not in parameters or 'password' not in parameters:
            return False
        
        # If the current user is logged in, do not allow the user to re-login
        if session.userid > 0:
            return False
            
        
        user =  self.authenticate_user(parameters['user'], parameters['password'])
        
        if user:
            session.userid = user.id
            session._user = user
            session.save()
        
        return user
        
    def system_status(self, session, parameters):
        """Parameters are ignored."""
        return {
        "status"        : "Running",
        "your_session"  : session.id,
        "users"         : len(self.active_users),
        "open_databases": len(self.active_user_databases)
        
        }
    
    def request_patterns(self, reset=True):
        """Return a list with all valid URL patterns for the web interface."""
        
        if reset or self.patterns == []:
            self.patterns = []
            
            for route in self.routers:
                if route == 'system':
                    for obj in self.routers['system']:
                        for command in self.routers['system'][obj]
                            self.patterns.append("/".join([route, obj, command]) )
                else :
                    for context in self.routers[route]:
                        for obj in self.routers[route][context]:
                            for command in self.routers[route][context][obj]
                                self.patterns.append("/".join([route, context, obj, command]) )
        
        return self.patterns
        
        
    #====================================
    def create_database(self, database_name, connection_settings, db_type = "user"):
        """Create a new database with database_name. The optional parameter allows the caller to pick the type of database to be created.  Valid types are: system or user."""
        
        if db_type == 'system':
            db = system_db.HistoriaSystemDatabase(database_name)
            db.connection_settings = connection_settings
            db.createDatabase(db)
            return db
        elif db_type == 'user':
            # Check to see if there is a database record on file for that name.
            db = user_db.HistoriaUserDatabase(self.database, database_name, self.config['server']['aes_key_file'])
            db.connection_settings = connection_settings
            self.database.createDatabase(db)
            return db
        else:
            raise ValueError("Databases much be of type 'system' or 'user'")
    
    def connect_to_master_database(self):
        """Connect to the system's master database. Connection settings must be in self.config['database']"""
        
        self.database = system_db.HistoriaSystemDatabase(self.config['database']['main_database'])
        self.database.connection_settings['user'] = self.config['database']['user']
        self.database.connection_settings['password'] = self.config['database']['password']
        self.database.connection_settings['host'] = self.config['database']['host']
        
        self.database.connect()
        
    def load_database(self, database_name):
        """Setup a connection to an existing database based on database name."""
        pass
    
    def authenticate_user(self, user_name, password):
        """Check the user_name and password. If it is a validate user, return a user object."""
        if self.database is None:
            raise ControllerNotReady("The controller is not fully configured, not database object.")
        
        if not self.database.connected:
            self.database.connect()
            if not self.database.connected:
                raise DatabaseNotReady('Cannot connect to database')
        
        search = core_data_objects.HistoriaDatabaseSearch(self.database, user.HistoriaUser.machine_type)
        
        search.add_field('id')
        search.add_condition('name', user_name)
        search.add_condition('email', user_name, '=', 'OR')
        search.add_limit(1)
        test_user = user.HistoriaUser(self.database)
        try:
            results = search.execute_search()
            test_user.load(results[0]['id'])
            if(test_user.checkPassword(password)):
                return test_user
            else:
                return False
        except:
            return False
        
    
    def check_access(self, user, database):
        """Check that a user has access to a given database (object or name). Both parameters can be either the name or the object representation."""
        pass
    
    def start_session(self, ip):
        """Return a new session object"""
        sess = session.HistoriaSession(self.database)
        sess_id = sess.new_id()
        sess.ip = ip
        sess.userid = 0
        sess.save()
        
        self.logger.info("New session started with ID: {0}".format(sess_id))
        
        return sess
        
    def reload_session(self, session_id, ip):
        """Return a new session object"""
        sess = session.HistoriaSession(self.database)
        try:
            sess.load(session_id)
            if sess.ip != ip:
                sess.ip = ip
            sess.save() # reset the last_seen value in the database

            self.logger.info("Loaded session with ID: {0}".format(session_id))
            
            if sess.userid > 0 :
                try:
                    active_user = user.HistoriaUser(self.database)
                    active_user.load(sess.userid)
                    sess._user = active_user
                except database.exceptions.DataLoadError as err:
                    # If there is an error loading the user for this session then the
                    # session is corrupt and should be destoryed and a new one created.
                    self.logger.notice('Invalid user associated with session. Destorying session: {0}'.format(session_id))
                    self.end_session(session_id)
                    sess = session.HistoriaSession(self.database)
        except database.exceptions.DataLoadError as err:
            self.logger.error('Unable to load session: {0}'.format(session_id))
            raise InvalidSessionError("Invalid Session ID: {0}".format(session_id))
        except database.exceptions.DataConnectionError as err:
            self.logger.error('Unable to connect to database to load session: {0}'.format(session_id))
            raise err
        else:
            return sess

    def end_session(self, session, parameters):
        """End a given user's session, and close their database connection to free resources.
        parameters is ignored and only included for consistancy with other functions."""
        try:
            sid = session.id
            session.delete()
            self.logger.info("Ended session with ID: {0}".format(sid))
            return True
        except database.exceptions.DataLoadError as err:
            self.logger.info("Unable to end session with ID: {0}. Session not found.".format(sid))
            return False
        except database.exceptions.DataConnectionError as err:
            self.logger.error('Unable to connect to database to load session: {0}'.format(sid))
            raise err
    
    @staticmethod
    def _deep_dict_merge(base_dict, update_dict):
        """Inplace deep merge update_dict into base_dict. Intended as a deep version of update()"""
        
        for key in update_dict:
            if key in base_dict:
                if isinstance(update_dict[key], dict) and isinstance(base_dict[key], dict):
                    HistoriaCoreController._deep_dict_merge(base_dict[key], update_dict[key])
                else:
                    base_dict[key] = update_dict[key]
            else:
                base_dict[key] = update_dict[key]
        
        return base_dict
