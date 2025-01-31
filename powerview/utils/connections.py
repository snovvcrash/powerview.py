#!/usr/bin/env python3
from impacket.smbconnection import SMBConnection, SessionError
from impacket.smb3structs import FILE_READ_DATA, FILE_WRITE_DATA
from impacket.dcerpc.v5 import samr, epm, transport, rpcrt, rprn, srvs, wkst, scmr, drsuapi
from impacket.dcerpc.v5.rpcrt import DCERPCException, RPC_C_AUTHN_WINNT, RPC_C_AUTHN_LEVEL_PKT_PRIVACY, RPC_C_AUTHN_GSS_NEGOTIATE
from impacket.spnego import SPNEGO_NegTokenInit, TypesMech

from powerview.utils.helpers import (
    get_machine_name,
    host2ip,
    is_valid_fqdn,
)
from powerview.lib.resolver import (
    LDAP,
)

import ssl
import ldap3
import logging
import sys

class CONNECTION:
    def __init__(self, args):
        self.username = args.username
        self.password = args.password
        self.domain = args.domain
        self.lmhash = args.lmhash
        self.nthash = args.nthash
        self.use_kerberos = args.use_kerberos
        self.use_ldap = args.use_ldap
        self.use_ldaps = args.use_ldaps
        self.use_gc = args.use_gc
        self.use_gc_ldaps = args.use_gc_ldaps
        self.proto = None
        self.hashes = args.hashes
        self.auth_aes_key = args.auth_aes_key
        if self.auth_aes_key is not None:
            self.use_kerberos = True
        self.no_pass = args.no_pass
        self.nameserver = args.nameserver

        if is_valid_fqdn(args.ldap_address) and self.nameserver:
            _ldap_address = host2ip(args.ldap_address, nameserver=self.nameserver, dns_timeout=5)
            if not _ldap_address:
                logging.error("Couldn't resolve %s" % args.ldap_address)
                sys.exit(0)
            self.targetIp = _ldap_address
            self.ldap_address = _ldap_address
            args.ldap_address = _ldap_address
        else:
            self.targetIp = args.ldap_address
            self.ldap_address = args.ldap_address

        if args.dc_ip:
            self.dc_ip = args.dc_ip
        else:
            self.dc_ip = self.targetIp
            args.dc_ip = self.dc_ip
       
        self.kdcHost = self.dc_ip
        self.targetDomain = None

        if not self.use_ldap and not self.use_ldaps and not self.use_gc and not self.use_gc_ldaps:
            self.use_ldaps = True

        self.args = args
        self.ldap_session = None
        self.ldap_server = None

        self.rpc_conn = None
        self.samr = None
        self.TGT = {}
        self.TGS = {}

    def set_domain(self, domain):
        self.domain = domain

    def get_domain(self):
        return self.domain

    def set_targetDomain(self, targetDomain):
        self.targetDomain = targetDomain

    def get_targetDomain(self):
        return self.targetDomain

    def set_username(self, username):
        self.username = username

    def get_username(self):
        return self.username

    def set_password(self, password):
        self.password = password

    def get_password(self):
        return self.password

    def set_dc_ip(self, dc_ip):
        self.dc_ip = dc_ip

    def get_dc_ip(self):
        return self.dc_ip

    def set_ldap_address(self, ldap_address):
        self.ldap_address = ldap_address

    def get_ldap_address(self):
        return self.ldap_address

    def get_proto(self):
        return self.proto

    def set_proto(self, proto):
        self.proto = proto

    def who_am_i(self):
        whoami = self.ldap_session.extend.standard.who_am_i()
        return whoami.split(":")[-1] if whoami else "ANONYMOUS"

    def reset_connection(self):
        self.ldap_session.rebind()

    def init_ldap_session(self, ldap_address=None, use_ldap=False, use_gc_ldap=False):

        if self.targetDomain and self.targetDomain != self.domain and self.kdcHost:
            self.kdcHost = None

        if use_ldap or use_gc_ldap:
            self.use_ldaps = False
            self.use_gc_ldaps = False

        if self.use_kerberos:
            if ldap_address:
                target = get_machine_name(ldap_address)
            else:
                target = get_machine_name(self.ldap_address)
        else:
            if ldap_address:
                if is_valid_fqdn(ldap_address) and self.nameserver:
                    target = host2ip(ldap_address, nameserver=self.nameserver)
                else:
                    target = ldap_address
            elif self.ldap_address is not None:
                target = self.ldap_address
            else:
                target = self.domain

        _anonymous = False
        if not self.domain and not self.username and (not self.password or not self.nthash or not self.lmhash):
            logging.debug("No credentials supplied. Using ANONYMOUS access")
            _anonymous = True

        if self.use_ldaps is True or self.use_gc_ldaps is True:
            try:
                tls = ldap3.Tls(
                    validate=ssl.CERT_NONE,
                    version=ssl.PROTOCOL_TLSv1_2,
                    ciphers='ALL:@SECLEVEL=0',
                )
                if _anonymous:
                    self.ldap_server, self.ldap_session = self.init_ldap_anonymous(target, tls)
                else:
                    self.ldap_server, self.ldap_session = self.init_ldap_connection(target, tls, self.domain, self.username, self.password, self.lmhash, self.nthash)
                return self.ldap_server, self.ldap_session
            except (ldap3.core.exceptions.LDAPSocketOpenError, ConnectionResetError):
                try:
                    tls = ldap3.Tls(
                        validate=ssl.CERT_NONE,
                        version=ssl.PROTOCOL_TLSv1,
                        ciphers='ALL:@SECLEVEL=0',
                    )
                    if _anonymous:
                        self.ldap_server, self.ldap_session = self.init_ldap_anonymous(target, tls)
                    else:
                        self.ldap_server, self.ldap_session = self.init_ldap_connection(target, tls, self.domain, self.username, self.password, self.lmhash, self.nthash)
                    return self.ldap_server, self.ldap_session
                except:
                    if self.use_ldaps:
                        logging.debug('Error bind to LDAPS, trying LDAP')
                        self.use_ldap = True
                        self.use_ldaps = False
                    elif self.use_gc_ldaps:
                        logging.debug('Error bind to GS ssl, trying GC')
                        self.use_gc = True
                        self.use_gc_ldaps = False
                    return self.init_ldap_session()
        else:
            if _anonymous:
                self.ldap_server, self.ldap_session = self.init_ldap_anonymous(target)
            else:
                self.ldap_server, self.ldap_session = self.init_ldap_connection(target, None, self.domain, self.username, self.password, self.lmhash, self.nthash)
            return self.ldap_server, self.ldap_session

    def init_ldap_anonymous(self, target, tls=None):
        ldap_server_kwargs = {
            "host": target,
            "get_info": ldap3.ALL,
            "formatter": {
                "lastLogon": LDAP.ldap2datetime,
                "pwdLastSet": LDAP.ldap2datetime,
                "badPasswordTime": LDAP.ldap2datetime,
                "objectGUID": LDAP.bin_to_guid,
                "objectSid": LDAP.bin_to_sid,
                "securityIdentifier": LDAP.bin_to_sid,
                "mS-DS-CreatorSID": LDAP.bin_to_sid,
                "msDS-ManagedPassword": LDAP.formatGMSApass,
                "pwdProperties": LDAP.resolve_pwdProperties,
                "userAccountControl": LDAP.resolve_uac,
                "msDS-SupportedEncryptionTypes": LDAP.resolve_enc_type,
            }
        }

        if tls:
            if self.use_ldaps:
                self.proto = "LDAPS"
                ldap_server_kwargs["use_ssl"] = True
                ldap_server_kwargs["port"] = 636
            elif self.use_gc_ldaps:
                self.proto = "GCssl"
                ldap_server_kwargs["use_ssl"] = True
                ldap_server_kwargs["port"] = 3269
        else:
            if self.use_gc:
                self.proto = "GC"
                ldap_server_kwargs["use_ssl"] = False
                ldap_server_kwargs["port"] = 3268
            elif self.use_ldap:
                self.proto = "LDAP"
                ldap_server_kwargs["use_ssl"] = False
                ldap_server_kwargs["port"] = 389

        logging.debug(f"Connecting as ANONYMOUS to %s, Port: %s, SSL: %s" % (ldap_server_kwargs["host"], ldap_server_kwargs["port"], ldap_server_kwargs["use_ssl"]))
        ldap_server = ldap3.Server(**ldap_server_kwargs)
        ldap_session = ldap3.Connection(ldap_server)

        if not ldap_session.bind():
            logging.info(f"Error binding to {self.proto}")
            sys.exit(0)

        base_dn = ldap_server.info.other['defaultNamingContext'][0]
        if not ldap_session.search(base_dn,'(objectclass=*)'):
            logging.warning("ANONYMOUS access not allowed")
            sys.exit(0)
        else:
            logging.info("Server allows ANONYMOUS access!")
            return ldap_server, ldap_session

    def init_ldap_connection(self, target, tls, domain, username, password, lmhash, nthash):
        user = '%s\\%s' % (domain, username)

        ldap_server_kwargs = {
            "host": target,
            "get_info": ldap3.ALL,
            "allowed_referral_hosts": [('*', True)],
            "mode": ldap3.IP_V4_PREFERRED,
            "formatter": {
                "lastLogon": LDAP.ldap2datetime,
                "pwdLastSet": LDAP.ldap2datetime,
                "badPasswordTime": LDAP.ldap2datetime,
                "objectGUID": LDAP.bin_to_guid,
                "objectSid": LDAP.bin_to_sid,
                "securityIdentifier": LDAP.bin_to_sid,
                "mS-DS-CreatorSID": LDAP.bin_to_sid,
                "msDS-ManagedPassword": LDAP.formatGMSApass,
                "pwdProperties": LDAP.resolve_pwdProperties,
                "userAccountControl": LDAP.resolve_uac,
                "msDS-SupportedEncryptionTypes": LDAP.resolve_enc_type,
            }
        }

        if tls:
            if self.use_ldaps:
                self.proto = "LDAPS"
                ldap_server_kwargs["use_ssl"] = True
                ldap_server_kwargs["port"] = 636
            elif self.use_gc_ldaps:
                self.proto = "GCssl"
                ldap_server_kwargs["use_ssl"] = True
                ldap_server_kwargs["port"] = 3269
        else:
            if self.use_gc:
                self.proto = "GC"
                ldap_server_kwargs["use_ssl"] = False
                ldap_server_kwargs["port"] = 3268
            elif self.use_ldap:
                self.proto = "LDAP"
                ldap_server_kwargs["use_ssl"] = False
                ldap_server_kwargs["port"] = 389

        # TODO: fix target when using kerberos
        bind = False

        logging.debug(f"Connecting to %s, Port: %s, SSL: %s" % (ldap_server_kwargs["host"], ldap_server_kwargs["port"], ldap_server_kwargs["use_ssl"]))
        ldap_server = ldap3.Server(**ldap_server_kwargs)
        if self.use_kerberos:
            ldap_session = ldap3.Connection(ldap_server, auto_referrals=False)
            bind = ldap_session.bind()
            try:
                self.ldap3_kerberos_login(ldap_session, target, username, password, domain, lmhash, nthash, self.auth_aes_key, kdcHost=self.kdcHost, useCache=self.no_pass)
            except Exception as e:
                logging.error(str(e))
                sys.exit(0)
        elif self.hashes is not None:
            ldap_session = ldap3.Connection(ldap_server, user=user, password=lmhash + ":" + nthash, authentication=ldap3.NTLM)
            bind = ldap_session.bind()
        else:
            ldap_session = ldap3.Connection(ldap_server, user=user, password=password, authentication=ldap3.NTLM)
            bind = ldap_session.bind()

        # check for channel binding
        if not bind:
            # check if signing is enforced
            if "strongerAuthRequired" in str(ldap_session.result):
                logging.warning("LDAP signing is enforced!")

            error_code = ldap_session.result['message'].split(",")[2].replace("data","").strip()
            error_status = LDAP.resolve_err_status(error_code)
            if error_code and error_status:
                logging.error("Bind not successful - %s [%s]" % (ldap_session.result['description'], error_status))
                logging.debug("%s" % (ldap_session.result['message']))
            else:
                logging.error(f"Unexpected Error: {str(ldap_session.result['message'])}")

            sys.exit(0)
        else:
            logging.warning("LDAP Signing NOT Enforced!")
            logging.debug("Bind SUCCESS!")

        return ldap_server, ldap_session

    def ldap3_kerberos_login(self, connection, target, user, password, domain='', lmhash='', nthash='', aesKey='', kdcHost=None, TGT=None, TGS=None, useCache=True):
        from pyasn1.codec.ber import encoder, decoder
        from pyasn1.type.univ import noValue
        from binascii import hexlify, unhexlify
        """
        logins into the target system explicitly using Kerberos. Hashes are used if RC4_HMAC is supported.
        :param string user: username
        :param string password: password for the user
        :param string domain: domain where the account is valid for (required)
        :param string lmhash: LMHASH used to authenticate using hashes (password is not used)
        :param string nthash: NTHASH used to authenticate using hashes (password is not used)
        :param string aesKey: aes256-cts-hmac-sha1-96 or aes128-cts-hmac-sha1-96 used for Kerberos authentication
        :param string kdcHost: hostname or IP Address for the KDC. If None, the domain will be used (it needs to resolve tho)
        :param struct TGT: If there's a TGT available, send the structure here and it will be used
        :param struct TGS: same for TGS. See smb3.py for the format
        :param bool useCache: whether or not we should use the ccache for credentials lookup. If TGT or TGS are specified this is False
        :return: True, raises an Exception if error.
        """

        if lmhash != '' or nthash != '':
            if len(lmhash) % 2:
                lmhash = '0' + lmhash
            if len(nthash) % 2:
                nthash = '0' + nthash
            try:  # just in case they were converted already
                lmhash = unhexlify(lmhash)
                nthash = unhexlify(nthash)
            except TypeError:
                pass

        # Importing down here so pyasn1 is not required if kerberos is not used.
        from impacket.krb5.ccache import CCache
        from impacket.krb5.asn1 import AP_REQ, Authenticator, TGS_REP, seq_set
        from impacket.krb5.kerberosv5 import getKerberosTGT, getKerberosTGS
        from impacket.krb5 import constants
        from impacket.krb5.types import Principal, KerberosTime, Ticket
        import datetime
        import os

        if self.TGT or self.TGS:
            useCache = False

        if useCache:
            try:
                env_krb5ccname = os.getenv('KRB5CCNAME')
                if not env_krb5ccname:
                    logging.error("No KRB5CCNAME environment present.")
                    sys.exit(0)
                ccache = CCache.loadFile(env_krb5ccname)
            except Exception as e:
                # No cache present
                print(e)
                pass
            else:
                # retrieve domain information from CCache file if needed
                if domain == '':
                    domain = ccache.principal.realm['data'].decode('utf-8')
                    logging.debug('Domain retrieved from CCache: %s' % domain)

                logging.debug('Using Kerberos Cache: %s' % os.getenv('KRB5CCNAME'))
                principal = 'ldap/%s@%s' % (target.upper(), domain.upper())

                creds = ccache.getCredential(principal)
                if creds is None:
                    # Let's try for the TGT and go from there
                    principal = 'krbtgt/%s@%s' % (domain.upper(), domain.upper())
                    creds = ccache.getCredential(principal)
                    if creds is not None:
                        self.TGT = creds.toTGT()
                        logging.debug('Using TGT from cache')
                    else:
                        logging.debug('No valid credentials found in cache')
                else:
                    self.TGS = creds.toTGS(principal)
                    logging.debug('Using TGS from cache')

                # retrieve user information from CCache file if needed
                if user == '' and creds is not None:
                    user = creds['client'].prettyPrint().split(b'@')[0].decode('utf-8')
                    logging.debug('Username retrieved from CCache: %s' % user)
                elif user == '' and len(ccache.principal.components) > 0:
                    user = ccache.principal.components[0]['data'].decode('utf-8')
                    logging.debug('Username retrieved from CCache: %s' % user)

        # First of all, we need to get a TGT for the user
        userName = Principal(user, type=constants.PrincipalNameType.NT_PRINCIPAL.value)
        if not self.TGT:
            if not self.TGS:
                tgt, cipher, oldSessionKey, sessionKey = getKerberosTGT(userName, password, domain, lmhash, nthash, aesKey, kdcHost)
                self.TGT['KDC_REP'] = tgt
                self.TGT['cipher'] = cipher
                self.TGT['oldSessionKey'] = oldSessionKey
                self.TGT['sessionKey'] = sessionKey
        else:
            tgt = self.TGT['KDC_REP']
            cipher = self.TGT['cipher']
            sessionKey = self.TGT['sessionKey']

        if TGS is None:
            serverName = Principal('ldap/%s' % target, type=constants.PrincipalNameType.NT_SRV_INST.value)
            tgs, cipher, oldSessionKey, sessionKey = getKerberosTGS(serverName, domain, kdcHost, tgt, cipher, sessionKey)
            self.TGS['KDC_REP'] = tgs
            self.TGS['cipher'] = cipher
            self.TGS['oldSessionKey'] = oldSessionKey
            self.TGS['sessionKey'] = sessionKey
        else:
            tgs = self.TGS['KDC_REP']
            cipher = self.TGS['cipher']
            sessionKey = self.TGS['sessionKey']

            # Let's build a NegTokenInit with a Kerberos REQ_AP

        blob = SPNEGO_NegTokenInit()

        # Kerberos
        blob['MechTypes'] = [TypesMech['MS KRB5 - Microsoft Kerberos 5']]

        # Let's extract the ticket from the TGS
        tgs = decoder.decode(tgs, asn1Spec=TGS_REP())[0]
        ticket = Ticket()
        ticket.from_asn1(tgs['ticket'])

        # Now let's build the AP_REQ
        apReq = AP_REQ()
        apReq['pvno'] = 5
        apReq['msg-type'] = int(constants.ApplicationTagNumbers.AP_REQ.value)

        opts = []
        apReq['ap-options'] = constants.encodeFlags(opts)
        seq_set(apReq, 'ticket', ticket.to_asn1)

        authenticator = Authenticator()
        authenticator['authenticator-vno'] = 5
        authenticator['crealm'] = domain
        seq_set(authenticator, 'cname', userName.components_to_asn1)
        now = datetime.datetime.utcnow()

        authenticator['cusec'] = now.microsecond
        authenticator['ctime'] = KerberosTime.to_asn1(now)

        encodedAuthenticator = encoder.encode(authenticator)

        # Key Usage 11
        # AP-REQ Authenticator (includes application authenticator
        # subkey), encrypted with the application session key
        # (Section 5.5.1)
        encryptedEncodedAuthenticator = cipher.encrypt(sessionKey, 11, encodedAuthenticator, None)

        apReq['authenticator'] = noValue
        apReq['authenticator']['etype'] = cipher.enctype
        apReq['authenticator']['cipher'] = encryptedEncodedAuthenticator

        blob['MechToken'] = encoder.encode(apReq)

        request = ldap3.operation.bind.bind_operation(connection.version, ldap3.SASL, user, None, 'GSS-SPNEGO',
                                                      blob.getData())

        # Done with the Kerberos saga, now let's get into LDAP
        if connection.closed:  # try to open connection if closed
            connection.open(read_server_info=False)

        connection.sasl_in_progress = True
        response = connection.post_send_single_response(connection.send('bindRequest', request, None))
        connection.sasl_in_progress = False
        if response[0]['result'] != 0:
            raise Exception(response)

        connection.bound = True

        return True

    def init_smb_session(self, host, timeout=10, useCache=True):
        try:
            logging.debug("Default timeout is set to 15. Expect a delay")
            conn = SMBConnection(host, host, sess_port=445, timeout=timeout)
            if self.use_kerberos:
                if self.TGT and self.TGS:
                    useCache = False

                if useCache:
                    # only import if used
                    import os
                    from impacket.krb5.ccache import CCache
                    from impacket.krb5.kerberosv5 import KerberosError
                    from impacket.krb5 import constants

                    try:
                        ccache = CCache.loadFile(os.getenv('KRB5CCNAME'))
                    except Exception as e:
                       # No cache present
                        logging.info(str(e))
                        return
                    else:
                        # retrieve domain information from CCache file if needed
                        if self.domain == '':
                            self.domain = ccache.principal.realm['data'].decode('utf-8')
                            logging.debug('Domain retrieved from CCache: %s' % domain)

                        logging.debug('Using Kerberos Cache: %s' % os.getenv('KRB5CCNAME'))
                        principal = 'cifs/%s@%s' % (self.targetIp.upper(), self.domain.upper())

                        creds = ccache.getCredential(principal)
                        if creds is None:
                            # Let's try for the TGT and go from there
                            principal = 'krbtgt/%s@%s' % (self.domain.upper(), self.domain.upper())
                            creds = ccache.getCredential(principal)
                            if creds is not None:
                                self.TGT = creds.toTGT()
                                logging.debug('Using TGT from cache')
                            else:
                                logging.debug('No valid credentials found in cache')
                        else:
                            self.TGS = creds.toTGS(principal)
                            logging.debug('Using TGS from cache')

                        # retrieve user information from CCache file if needed
                        if self.username == '' and creds is not None:
                            self.username = creds['client'].prettyPrint().split(b'@')[0].decode('utf-8')
                            logging.debug('Username retrieved from CCache: %s' % self.username)
                        elif self.username == '' and len(ccache.principal.components) > 0:
                            self.user = ccache.principal.components[0]['data'].decode('utf-8')
                            logging.debug('Username retrieved from CCache: %s' % self.username)

                conn.kerberosLogin(self.username,self.password,self.domain, self.lmhash, self.nthash, self.auth_aes_key, self.dc_ip, self.TGT, self.TGS)
                #conn.kerberosLogin(self.username,self.password,self.domain, self.lmhash, self.nthash, self.auth_aes_key, self.dc_ip, self.TGT, self.TGS)
                # havent support kerberos authentication yet
            else:
                conn.login(self.username,self.password,self.domain, self.lmhash, self.nthash)
            return conn
        except OSError as e:
            logging.debug(str(e))
            return None
        except SessionError as e:
            logging.debug(str(e))
            return None
        except AssertionError as e:
            logging.debug(str(e))
            return None

    def init_samr_session(self):
        if not self.samr:
            self.samr = self.connectSamr()
        return self.samr

    # TODO: FIX kerberos auth
    def connectSamr(self):
        rpctransport = transport.SMBTransport(self.dc_ip, filename=r'\samr')

        if hasattr(rpctransport, 'set_credentials'):
            rpctransport.set_credentials(self.username, self.password, self.domain, lmhash=self.lmhash, nthash=self.nthash, aesKey=self.auth_aes_key)

        rpctransport.set_kerberos(self.use_kerberos, kdcHost=self.kdcHost)

        try:
            dce = rpctransport.get_dce_rpc()
            if self.use_kerberos:
                dce.set_auth_type(RPC_C_AUTHN_GSS_NEGOTIATE)
            dce.set_auth_level(RPC_C_AUTHN_LEVEL_PKT_PRIVACY)
            dce.connect()
            dce.bind(samr.MSRPC_UUID_SAMR)
            return dce
        except:
            return None

    # stole from PetitPotam.py
    # TODO: FIX kerberos auth
    def connectRPCTransport(self, host=None, stringBindings=None, auth=True):
        if not host:
            host = self.dc_ip
        if not stringBindings:
            stringBindings = epm.hept_map(host, samr.MSRPC_UUID_SAMR, protocol = 'ncacn_ip_tcp')
        if not host:
            host = self.dc_ip

        rpctransport = transport.DCERPCTransportFactory(stringBindings)
        #rpctransport.set_dport(445)

        if hasattr(rpctransport, 'set_credentials') and auth:
            rpctransport.set_credentials(self.username, self.password, self.domain, self.lmhash, self.nthash)

        if hasattr(rpctransport, 'set_kerberos') and self.use_kerberos and auth:
            rpctransport.set_kerberos(self.use_kerberos, kdcHost=self.kdcHost)

        if host:
            rpctransport.setRemoteHost(host)

        dce = rpctransport.get_dce_rpc()
        dce.set_auth_type(RPC_C_AUTHN_WINNT)
        dce.set_auth_level(RPC_C_AUTHN_LEVEL_PKT_PRIVACY)

        logging.debug("Connecting to %s" % stringBindings)

        try:
            dce.connect()
            return dce
        except Exception as e:
            return None

    # stolen from pywerview
    def create_rpc_connection(self, host, pipe):
        binding_strings = dict()
        binding_strings['srvsvc'] = srvs.MSRPC_UUID_SRVS
        binding_strings['wkssvc'] = wkst.MSRPC_UUID_WKST
        binding_strings['samr'] = samr.MSRPC_UUID_SAMR
        binding_strings['svcctl'] = scmr.MSRPC_UUID_SCMR
        binding_strings['drsuapi'] = drsuapi.MSRPC_UUID_DRSUAPI

        # TODO: try to fallback to TCP/139 if tcp/445 is closed
        if pipe == r'\drsuapi':
            string_binding = epm.hept_map(host, drsuapi.MSRPC_UUID_DRSUAPI,
                                          protocol='ncacn_ip_tcp')
            rpctransport = transport.DCERPCTransportFactory(string_binding)
            rpctransport.set_credentials(username=self.username, password=self.password,
                                         domain=self.domain, lmhash=self.lmhash,
                                         nthash=self.nthash)
        else:
            rpctransport = transport.SMBTransport(host, 445, pipe,
                                                  username=self.username, password=self.password,
                                                  domain=self.domain, lmhash=self.lmhash,
                                                  nthash=self.nthash, doKerberos=self.use_kerberos)

        rpctransport.set_connect_timeout(10)
        dce = rpctransport.get_dce_rpc()

        if pipe == r'\drsuapi':
            dce.set_auth_level(RPC_C_AUTHN_LEVEL_PKT_PRIVACY)

        try:
            dce.connect()
        except Exception as e:
            logging.critical('Error when creating RPC connection')
            logging.critical(e)
            self.rpc_conn = None
        else:
            dce.bind(binding_strings[pipe[1:]])
            self.rpc_conn = dce

        return self.rpc_conn

    def init_rpc_session(self, host, pipe=r'\srvsvc'):
        if not self.rpc_conn:
            return self.create_rpc_connection(host=host, pipe=pipe)
        else:
            return self.rpc_conn
