import datetime
import binascii

from impacket.uuid import bin_to_string
from ldap3.protocol.formatters.formatters import format_sid


from powerview.utils.constants import (
    UAC_DICT,
    LDAP_ERROR_STATUS,
    SUPPORTED_ENCRYPTION_TYPES,
    switcher_trustDirection,
    switcher_trustType,
    switcher_trustAttributes,
    MSDS_MANAGEDPASSWORD_BLOB,
    PWD_FLAGS,
)

class UAC:
    def parse_value(uac_value):
        uac_value = int(uac_value)
        flags = []

        for key, value in UAC_DICT.items():
            if uac_value & key:
                flags.append(value)

        return flags

class ENCRYPTION_TYPE:
    def parse_value(enc_value):
        enc_value = int(enc_value)
        flags = []

        for key, value in SUPPORTED_ENCRYPTION_TYPES.items():
            if enc_value & key:
                flags.append(value)

        return flags

class LDAP:
    def resolve_err_status(error_status):
        return LDAP_ERROR_STATUS.get(error_status)

    def resolve_enc_type(enc_type):
        if isinstance(enc_type, list):
            return ENCRYPTION_TYPE.parse_value(enc_type[0])
        elif isinstance(enc_type, bytes):
            return ENCRYPTION_TYPE.parse_value(enc_type.decode())
        else:
            return ENCRYPTION_TYPE.parse_value(enc_type)

    def resolve_uac(uac_val):
        # resolve userAccountControl
        if isinstance(uac_val, list):
            val =  UAC.parse_value(uac_val[0])
        elif isinstance(uac_val, bytes):
            val = UAC.parse_value(uac_val.decode())
        else:
            val = UAC.parse_value(uac_val)

        val[0] = f"{val[0]} [{uac_val.decode()}]"

        return val

    def ldap2datetime(ts):
        if isinstance(ts, datetime.datetime):
            return ts
        ts = int(ts)
        return datetime.datetime(1601, 1, 1) + datetime.timedelta(seconds=ts/10000000)

    def bin_to_guid(guid):
        return "{%s}" % bin_to_string(guid).lower()

    def bin_to_sid(sid):
        return format_sid(sid)

    def formatGMSApass(managedPassword):
        blob = MSDS_MANAGEDPASSWORD_BLOB(managedPassword)
        hash = MD4.new()
        hash.update(blob["CurrentPassword"][:-2])
        passwd = (
            "aad3b435b51404eeaad3b435b51404ee:" + binascii.hexlify(hash.digest()).decode()
        )
        return passwd

    def resolve_pwdProperties(flag):
        prop =  PWD_FLAGS.get(int(flag))
        return f"({flag.decode()}) {prop}" if prop else flag

class TRUST:
    def resolve_trustDirection(flag):
        return switcher_trustDirection.get(flag)

    def resolve_trustType(flag):
        return switcher_trustType.get(flag)

    def resolve_trustAttributes(flag):
        return switcher_trustAttributes.get(flag)

