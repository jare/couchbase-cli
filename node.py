
import cluster_manager
import time
import os
import sys
import util_cli as util
import socket
import re
import urlparse
import json

from usage import command_error
from restclient import *
from _csv import reader

try:
    import pump_bfd2
    IS_ENTERPRISE= True
except ImportError:
    IS_ENTERPRISE = False

# the rest commands and associated URIs for various node operations

rest_cmds = {
    'ssl-manage'            :'/pools/default/certificate',
    'admin-role-manage'   : '/settings/rbac/users',
}

# Map of operations and the HTTP methods used against the REST interface

methods = {
    'ssl-manage'            :'GET',
    'admin-role-manage'   : 'PUT',
}

bool_to_str = lambda value: str(bool(int(value))).lower()

# Map of HTTP success code, success message and error message for
# handling HTTP response properly

class Node:
    SEP = ","
    def __init__(self):
        self.rest_cmd = None
        self.method = 'GET'
        self.debug = False
        self.server = ''
        self.port = ''
        self.user = ''
        self.password = ''
        self.ssl = False

        self.params = {}
        self.output = 'standard'
        self.sa_username = None
        self.sa_password = None
        self.index_storage_setting = None
        self.enable_email_alert = None

        #group management
        self.group_name = None
        self.server_list = []

        #SSL certificate management
        self.certificate_file = None
        self.extended = False
        self.cmd = None

        # Collect logs
        self.nodes = None
        self.all_nodes = None
        self.upload = False
        self.upload_host = None
        self.customer = None
        self.ticket = ""

        #index
        self.services = None

        #set-roles / delete-roles
        self.roles = None
        self.my_roles = False
        self.get_roles = None
        self.set_users = None
        self.set_names = None
        self.delete_users = None

    def runCmd(self, cmd, server, port,
               user, password, ssl, opts):
        self.rest_cmd = rest_cmds[cmd]
        self.method = methods[cmd]
        self.server = server
        self.port = int(port)
        self.user = user
        self.password = password
        self.ssl = ssl

        servers = self.processOpts(cmd, opts)
        if self.debug:
            print "INFO: servers %s" % servers

        elif cmd == 'ssl-manage':
            self.retrieveCert()

        elif cmd == 'admin-role-manage':
            self.alterRoles()

    # Role-Based Access Control
    def alterRoles(self):
        cm = cluster_manager.ClusterManager(self.server, self.port, self.user,
                                            self.password, self.ssl)

        # need to check arguments
        if (self.my_roles == None and self.get_roles == None and \
            self.set_users == None and self.delete_users == None):
            print "ERROR: You must specify either '--my-roles', '--get-roles', " \
                "'--set-users', or '--delete-users'"
            return

        if self.my_roles and (self.get_roles or self.set_users or self.roles or self.delete_users):
            print "ERROR: The 'my-roles' option may not be used with any other option."
            return

        if self.get_roles and (self.my_roles or self.set_users or self.roles or self.delete_users):
            print "ERROR: The 'get-roles' option may not be used with any other option."
            return

        if (self.set_users and self.roles == None) or (self.set_users == None and self.roles):
            print "ERROR: You must specify lists of both users and roles for those users.\n  --set-users=[comma delimited user list] --roles=[comma-delimited list of one or more from admin, ro_admin, cluster_admin, replication_admin, bucket_admin[bucket name or '*'], views_admin[bucket name or '*']"
            return

        # my_roles
        if self.my_roles:
            data, errors = cm.myRoles()
            if errors == None:
                print "SUCCESS: my roles:"

        # get_roles
        elif self.get_roles:
            data, errors = cm.getRoles()
            if errors == None:
                print "SUCCESS: user/role list:"

        # set_users
        elif self.set_users:
            data, errors = cm.setRoles(self.set_users,self.roles,self.set_names)
            if errors == None:
                print "SUCCESS: set roles for ",self.set_users,". New user/role list:"

        # delete_users
        else:
            data, errors = cm.deleteRoles(self.delete_users)
            if errors == None:
                print "SUCCESS: removed users ", self.delete_users, ". New user/role list:"

        _exitIfErrors(errors)
        print json.dumps(data,indent=2)

    def processOpts(self, cmd, opts):
        """ Set standard opts.
            note: use of a server key keeps optional
            args aligned with server.
            """
        servers = {
            'add': {},
            'remove': {},
            'failover': {},
            'recovery': {},
            'log': {},
        }

        # don't allow options that don't correspond to given commands

        for o, a in opts:
            command_error_msg = "option '%s' is not used with command '%s'" % (o, cmd)

        server = None
        for o, a in opts:
            if o in ("-a", "--server-add"):
                if a == "self":
                    a = socket.gethostbyname(socket.getfqdn())
                server = "%s:%d" % util.hostport(a)
                servers['add'][server] = { 'user':'', 'password':''}
                self.server_list.append(server)
            elif o == "--server-add-username":
                if server:
                    servers['add'][server]['user'] = a
                self.sa_username = a
            elif o == "--server-add-password":
                if server:
                    servers['add'][server]['password'] = a
                self.sa_password = a
            elif o in ( "-r", "--server-remove"):
                server = "%s:%d" % util.hostport(a)
                servers['remove'][server] = True
                server = None
            elif o in ( "--server-recovery"):
                server = "%s:%d" % util.hostport(a)
                servers['recovery'][server] = True
                server = None
            elif o == "--nodes":
                for server in self.normalize_servers(a):
                    servers['log'][server] = True
            elif o in ('-o', '--output'):
                if a == 'json':
                    self.output = a
                server = None
            elif o in ('-d', '--debug'):
                self.debug = True
                server = None
            elif o == '--index-storage-setting':
                self.index_storage_setting = a
            elif o == '--enable-email-alert':
                self.enable_email_alert = bool_to_str(a)
            elif o == '--create':
                self.cmd = 'create'
            elif o == '--list':
                self.cmd = 'list'
            elif o == '--delete':
                self.cmd = 'delete'
            elif o == '--set':
                self.cmd = 'set'
            elif o == '--metadata-purge-interval':
                self.purge_interval = a
            elif o == '--group-name':
                self.group_name = a
            elif o == '--add-servers':
                self.server_list = self.normalize_servers(a)
                self.cmd = 'add-servers'
            elif o == '--remove-servers':
                self.server_list = self.normalize_servers(a)
                self.cmd = 'remove-servers'
            elif o == '--move-servers':
                self.server_list = self.normalize_servers(a)
                self.cmd = 'move-servers'
            elif o == '--regenerate-cert':
                self.cmd = 'regenerate'
                self.certificate_file = a
            elif o == '--cluster-cert-info':
                self.cmd = 'cluster-cert-info'
            elif o == '--extended':
                self.extended = True
            elif o == '--node-cert-info':
                self.cmd = 'node-cert-info'
            elif o == '--upload-cluster-ca':
                self.cmd = 'upload-cluster-ca'
                self.certificate_file = a
            elif o == '--set-node-certificate':
                self.cmd = 'set-node-certificate'
            elif o == '--nodes':
                self.nodes = a
            elif o == '--all-nodes':
                self.all_nodes = True
            elif o == '--upload':
                self.upload = True
            elif o == '--upload-host':
                self.upload_host = a
            elif o == '--customer':
                self.customer = a
            elif o == '--ticket':
                self.ticket = a
            elif o == '--services':
                self.services = a
            elif o == '--roles':
                self.roles = a
            elif o == '--my-roles':
                self.my_roles = True
            elif o == '--get-roles':
                self.get_roles = True
            elif o == '--set-users':
                self.set_users = a
            elif o == '--set-names':
                self.set_names = a
            elif o == '--delete-users':
                self.delete_users = a

        return servers

    def normalize_servers(self, server_list):
        slist = []
        sep = Node.SEP
        if server_list.find(sep) < 0:
            #backward compatible with ";" as separator
            sep = ";"
        for server in server_list.split(sep):
            hostport = "%s:%d" % util.hostport(server)
            slist.append(hostport)
        return slist

    def retrieveCert(self):
        if self.cmd in ['retrieve', 'regenerate', 'upload-cluster-ca'] and self.certificate_file is None:
            command_error("please specify certificate file name for the operation")

        cm = cluster_manager.ClusterManager(self.server, self.port, self.user, self.password, self.ssl)

        if self.cmd  == 'regenerate':
            certificate, errors = cm.regenerate_cluster_certificate()
            _exitIfErrors(errors)
            _exitOnFileWriteFailure(self.certificate_file, certificate)
            print "SUCCESS: %s certificate to '%s'" % (self.cmd, self.certificate_file)
        elif self.cmd == 'cluster-cert-info':
            certificate, errors = cm.retrieve_cluster_certificate(self.extended)
            _exitIfErrors(errors)
            if isinstance(certificate, dict):
                print json.dumps(certificate, sort_keys=True, indent=2)
            else:
                print certificate
        elif self.cmd == 'node-cert-info':
            certificate, errors = cm.retrieve_node_certificate('%s:%d' % (self.server, self.port))
            _exitIfErrors(errors)
            print json.dumps(certificate, sort_keys=True, indent=2)
        elif self.cmd == 'upload-cluster-ca':
            certificate = _exitOnFileReadFailure(self.certificate_file)
            _, errors = cm.upload_cluster_certificate(certificate)
            _exitIfErrors(errors)
            print "SUCCESS: uploaded cluster certificate to %s:%d" % (self.server, self.port)
        elif self.cmd == 'set-node-certificate':
            _, errors = cm.set_node_certificate()
            _exitIfErrors(errors)
            print "SUCCESS: node certificate set"
        else:
            print "ERROR: unknown request:", self.cmd

    def getCommandSummary(self, cmd):
        """Return one-line summary info for each supported command"""
        command_summary = {
            "server-list" :"list all servers in a cluster",
            "server-info" :"show details on one server",
            "ssl-manage" : "manage cluster certificate",
            "admin-role-manage" : "set access-control roles for users"
        }
        if cmd in command_summary:
            return command_summary[cmd]
        else:
            return None

    def getCommandHelp(self, cmd):
        """ Obtain detailed parameter help for Node commands
        Returns a list of pairs (arg1, arg1-information) or None if there's
        no help or cmd is unknown.
        """


        if cmd == "ssl-manage":
            return [("--cluster-cert-info", "prints cluster certificate info"),
                    ("--node-cert-info", "prints node certificate info"),
                    ("--regenerate-cert=CERTIFICATE",
                     "regenerate cluster certificate AND save to a pem file"),
                    ("--set-node-certificate", "sets the node certificate"),
                    ("--upload-cluster-ca", "uploads a new cluster certificate")]
        elif cmd == "admin-role-manage":
            return [
            ("--my-roles", "Return a list of roles for the current user."),
            ("--get-roles", "Return list of users and roles."),
            ("--set-users", "A comma-delimited list of user ids to set acess-control roles for"),
            ("--set-names", "A optional quoted, comma-delimited list names, one for each specified user id"),
            ("--roles", "A comma-delimited list of roles to set for users, one or more from admin, ro_admin, cluster_admin, replication_admin, bucket_admin[bucket name or '*'], views_admin[bucket name or '*']"),
            ("--delete-users", "A comma-delimited list of users to remove from access control")
            ]
        else:
            return None

    def getCommandExampleHelp(self, cmd):
        """ Obtain detailed example help for command
        Returns a list of command examples to illustrate how to use command
        or None if there's no example help or cmd is unknown.
        """

        if cmd == "ssl-manage":
            return [("Download a cluster certificate",
"""
    couchbase-cli ssl-manage -c 192.168.0.1:8091 \\
        --cluster-cert-info \\
        -u Administrator -p password"""),
                ("Regenerate AND download a cluster certificate",
"""
    couchbase-cli ssl-manage -c 192.168.0.1:8091 \\
        --regenerate-cert=/tmp/test.pem \\
        -u Administrator -p password"""),
                ("Download the extended cluster certificate",
"""
    couchbase-cli ssl-manage -c 192.168.0.1:8091 \\
        --cluster-cert-info --extended \\
        -u Administrator -p password"""),
                ("Download the current node certificate",
"""
    couchbase-cli ssl-manage -c 192.168.0.1:8091 \\
        --node-cert-info \\
        -u Administrator -p password"""),
                ("Upload a new cluster certificate",
"""
    couchbase-cli ssl-manage -c 192.168.0.1:8091 \\
        --upload-cluster-ca=/tmp/test.pem \\
        -u Administrator -p password"""),
                ("Set the new node certificate",
"""
    couchbase-cli ssl-manage -c 192.168.0.1:8091 \\
        --set-node-certificate \\
        -u Administrator -p password""")]
        elif cmd == "admin-role-manage":
            return [("Show the current users roles.",
"""
    couchbase-cli admin-role-manage -c 192.168.0.1:8091 --my-roles
            """),
            ("Get a list of all users and roles",
"""
    couchbase-cli admin-role-manage -c 192.168.0.1:8091 --get-roles
            """),
                    ("Make bob and mary cluster_admins, and bucket admins for the default bucket",
"""
    couchbase-cli admin-role-manage -c 192.168.0.1:8091 \\
        --set-users=bob,mary --set-names="Bob Smith,Mary Jones" --roles=cluster_admin,bucket_admin[default]
            """),
                    ("Make jen bucket admins for all buckets",
"""
    couchbase-cli admin-role-manage -c 192.168.0.1:8091 \\
        --set-users=jen --roles=bucket_admin[*]
            """),
            ("Remove all roles for bob",
"""
    couchbase-cli admin-role-manage -c 192.168.0.1:8091 --delete-users=bob
            """)
            ]

        else:
            return None

def _warning(msg):
    print "WARNING: " + msg

def _exitIfErrors(errors):
    if errors:
        for error in errors:
            print "ERROR: " + error
        sys.exit(1)

def _exitOnFileWriteFailure(fname, bytes):
    try:
        fp = open(fname, 'w')
        fp.write(bytes)
        fp.close()
    except IOError, error:
        print "ERROR: ", error
        sys.exit(1)

def _exitOnFileReadFailure(fname):
    try:
        fp = open(fname, 'r')
        bytes = fp.read()
        fp.close()
        return bytes
    except IOError, error:
        print "ERROR: ", error
        sys.exit(1)

def isInt(s):
    if s is None:
        return False
    try:
        int(s)
        return True
    except ValueError:
        return False
