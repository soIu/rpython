import sys

from rpython.rtyper.lltypesystem import rffi, lltype
from rpython.rtyper.tool import rffi_platform
from rpython.translator.platform import platform
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.rlib.unroll import unrolling_iterable
from rpython.rlib._rsocket_rffi import SAVE_ERR


if sys.platform == 'win32' and platform.name != 'mingw32':
    libraries = ['libeay32', 'ssleay32', 'zlib',
                 'user32', 'advapi32', 'gdi32', 'msvcrt', 'ws2_32']
    includes = [
        # ssl.h includes winsock.h, which will conflict with our own
        # need of winsock2.  Remove this when separate compilation is
        # available...
        'winsock2.h',
        # wincrypt.h defines X509_NAME, include it here
        # so that openssl/ssl.h can repair this nonsense.
        'wincrypt.h']
else:
    libraries = ['z', 'ssl', 'crypto']
    includes = []

includes += [
    'openssl/ssl.h',
    'openssl/err.h',
    'openssl/rand.h',
    'openssl/evp.h',
    'openssl/ossl_typ.h',
    'openssl/x509v3.h']

eci = ExternalCompilationInfo(
    libraries = libraries,
    includes = includes,
    post_include_bits = [
        # Unnamed structures are not supported by rffi_platform.
        # So we replace an attribute access with a macro call.
        '#define pypy_GENERAL_NAME_dirn(name) (name->d.dirn)',
        '#define pypy_GENERAL_NAME_uri(name) (name->d.uniformResourceIdentifier)',
        '#define pypy_X509_OBJECT_data_x509(obj) (obj->data.x509)',
        '#define pypy_DIST_POINT_fullname(obj) (obj->distpoint->name.fullname)',
    ],
)

eci = rffi_platform.configure_external_library(
    'openssl', eci,
    [dict(prefix='openssl-',
          include_dir='inc32', library_dir='out32'),
     ])

ASN1_STRING = lltype.Ptr(lltype.ForwardReference())
ASN1_IA5STRING = ASN1_STRING
ASN1_ITEM = rffi.COpaquePtr('ASN1_ITEM')
ASN1_OBJECT = rffi.COpaquePtr('ASN1_OBJECT')
X509_NAME = rffi.COpaquePtr('X509_NAME')
X509_VERIFY_PARAM = rffi.COpaquePtr('X509_VERIFY_PARAM')
stack_st_X509_OBJECT = rffi.COpaquePtr('STACK_OF(X509_OBJECT)')
DIST_POINT = rffi.COpaquePtr('DIST_POINT')
stack_st_DIST_POINT = rffi.COpaquePtr('STACK_OF(X509_OBJECT)')
DH = rffi.COpaquePtr('DH')
EC_KEY = rffi.COpaquePtr('EC_KEY')
AUTHORITY_INFO_ACCESS = rffi.COpaquePtr('AUTHORITY_INFO_ACCESS')
GENERAL_NAME = lltype.Ptr(lltype.ForwardReference())

class CConfigBootstrap:
    _compilation_info_ = eci
    OPENSSL_EXPORT_VAR_AS_FUNCTION = rffi_platform.Defined(
            "OPENSSL_EXPORT_VAR_AS_FUNCTION")
    OPENSSL_VERSION_NUMBER = rffi_platform.ConstantInteger(
        "OPENSSL_VERSION_NUMBER")

cconfig = rffi_platform.configure(CConfigBootstrap)
if cconfig["OPENSSL_EXPORT_VAR_AS_FUNCTION"]:
    ASN1_ITEM_EXP = lltype.Ptr(lltype.FuncType([], ASN1_ITEM))
else:
    ASN1_ITEM_EXP = ASN1_ITEM
OPENSSL_VERSION_NUMBER = cconfig["OPENSSL_VERSION_NUMBER"]
HAVE_TLSv1_2 = OPENSSL_VERSION_NUMBER >= 0x10001000

class CConfig:
    _compilation_info_ = eci

    SSLEAY_VERSION = rffi_platform.DefinedConstantString(
        "SSLEAY_VERSION", "SSLeay_version(SSLEAY_VERSION)")
    OPENSSL_NO_SSL2 = rffi_platform.Defined("OPENSSL_NO_SSL2")
    OPENSSL_NO_SSL3 = rffi_platform.Defined("OPENSSL_NO_SSL3")
    OPENSSL_NO_ECDH = rffi_platform.Defined("OPENSSL_NO_ECDH")
    OPENSSL_NPN_NEGOTIATED = rffi_platform.Defined("OPENSSL_NPN_NEGOTIATED")
    SSL_FILETYPE_PEM = rffi_platform.ConstantInteger("SSL_FILETYPE_PEM")
    SSL_FILETYPE_ASN1 = rffi_platform.ConstantInteger("SSL_FILETYPE_ASN1")
    SSL_OP_ALL = rffi_platform.ConstantInteger("SSL_OP_ALL")
    SSL_OP_NO_SSLv2 = rffi_platform.ConstantInteger("SSL_OP_NO_SSLv2")
    SSL_OP_NO_SSLv3 = rffi_platform.ConstantInteger("SSL_OP_NO_SSLv3")
    SSL_OP_NO_TLSv1 = rffi_platform.ConstantInteger("SSL_OP_NO_TLSv1")
    if HAVE_TLSv1_2:
        SSL_OP_NO_TLSv1_1 = rffi_platform.ConstantInteger("SSL_OP_NO_TLSv1_1")
        SSL_OP_NO_TLSv1_2 = rffi_platform.ConstantInteger("SSL_OP_NO_TLSv1_2")
    SSL_OP_CIPHER_SERVER_PREFERENCE = rffi_platform.ConstantInteger(
        "SSL_OP_CIPHER_SERVER_PREFERENCE")
    SSL_OP_SINGLE_DH_USE = rffi_platform.ConstantInteger(
        "SSL_OP_SINGLE_DH_USE")
    SSL_OP_SINGLE_ECDH_USE = rffi_platform.ConstantInteger(
        "SSL_OP_SINGLE_ECDH_USE")
    SSL_OP_NO_COMPRESSION = rffi_platform.DefinedConstantInteger(
        "SSL_OP_NO_COMPRESSION")
    SSL_OP_DONT_INSERT_EMPTY_FRAGMENTS = rffi_platform.ConstantInteger(
        "SSL_OP_DONT_INSERT_EMPTY_FRAGMENTS")
    HAS_SNI = rffi_platform.Defined("SSL_CTRL_SET_TLSEXT_HOSTNAME")
    HAS_NPN = rffi_platform.Defined("OPENSSL_NPN_NEGOTIATED")
    SSL_VERIFY_NONE = rffi_platform.ConstantInteger("SSL_VERIFY_NONE")
    SSL_VERIFY_PEER = rffi_platform.ConstantInteger("SSL_VERIFY_PEER")
    SSL_VERIFY_FAIL_IF_NO_PEER_CERT = rffi_platform.ConstantInteger("SSL_VERIFY_FAIL_IF_NO_PEER_CERT")
    X509_V_FLAG_CRL_CHECK = rffi_platform.ConstantInteger("X509_V_FLAG_CRL_CHECK")
    X509_V_FLAG_CRL_CHECK_ALL = rffi_platform.ConstantInteger("X509_V_FLAG_CRL_CHECK_ALL")
    X509_V_FLAG_X509_STRICT = rffi_platform.ConstantInteger("X509_V_FLAG_X509_STRICT")
    SSL_ERROR_WANT_READ = rffi_platform.ConstantInteger(
        "SSL_ERROR_WANT_READ")
    SSL_ERROR_WANT_WRITE = rffi_platform.ConstantInteger(
        "SSL_ERROR_WANT_WRITE")
    SSL_ERROR_ZERO_RETURN = rffi_platform.ConstantInteger(
        "SSL_ERROR_ZERO_RETURN")
    SSL_ERROR_WANT_X509_LOOKUP = rffi_platform.ConstantInteger(
        "SSL_ERROR_WANT_X509_LOOKUP")
    SSL_ERROR_WANT_CONNECT = rffi_platform.ConstantInteger(
        "SSL_ERROR_WANT_CONNECT")
    SSL_ERROR_SYSCALL = rffi_platform.ConstantInteger("SSL_ERROR_SYSCALL")
    SSL_ERROR_SSL = rffi_platform.ConstantInteger("SSL_ERROR_SSL")
    SSL_RECEIVED_SHUTDOWN = rffi_platform.ConstantInteger(
        "SSL_RECEIVED_SHUTDOWN")
    SSL_MODE_AUTO_RETRY = rffi_platform.ConstantInteger("SSL_MODE_AUTO_RETRY")
    SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER = rffi_platform.ConstantInteger("SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER")
    SSL_TLSEXT_ERR_OK = rffi_platform.ConstantInteger("SSL_TLSEXT_ERR_OK")
    SSL_TLSEXT_ERR_ALERT_FATAL = rffi_platform.ConstantInteger("SSL_TLSEXT_ERR_ALERT_FATAL")

    SSL_AD_INTERNAL_ERROR = rffi_platform.ConstantInteger("SSL_AD_INTERNAL_ERROR")
    SSL_AD_HANDSHAKE_FAILURE = rffi_platform.ConstantInteger("SSL_AD_HANDSHAKE_FAILURE")

    TLSEXT_NAMETYPE_host_name = rffi_platform.ConstantInteger("TLSEXT_NAMETYPE_host_name")

    ERR_LIB_X509 = rffi_platform.ConstantInteger("ERR_LIB_X509")
    ERR_LIB_PEM = rffi_platform.ConstantInteger("ERR_LIB_PEM")
    ERR_LIB_ASN1 = rffi_platform.ConstantInteger("ERR_LIB_ASN1")
    PEM_R_NO_START_LINE = rffi_platform.ConstantInteger("PEM_R_NO_START_LINE")
    ASN1_R_HEADER_TOO_LONG = rffi_platform.ConstantInteger(
        "ASN1_R_HEADER_TOO_LONG")
    X509_R_CERT_ALREADY_IN_HASH_TABLE = rffi_platform.ConstantInteger(
        "X509_R_CERT_ALREADY_IN_HASH_TABLE")

    NID_undef = rffi_platform.ConstantInteger("NID_undef")
    NID_subject_alt_name = rffi_platform.ConstantInteger("NID_subject_alt_name")
    NID_ad_OCSP = rffi_platform.ConstantInteger("NID_ad_OCSP")
    NID_ad_ca_issuers = rffi_platform.ConstantInteger("NID_ad_ca_issuers")
    NID_info_access = rffi_platform.ConstantInteger("NID_info_access")
    NID_X9_62_prime256v1 = rffi_platform.ConstantInteger("NID_X9_62_prime256v1")
    NID_crl_distribution_points = rffi_platform.ConstantInteger("NID_crl_distribution_points")
    GEN_DIRNAME = rffi_platform.ConstantInteger("GEN_DIRNAME")
    GEN_EMAIL = rffi_platform.ConstantInteger("GEN_EMAIL")
    GEN_DNS = rffi_platform.ConstantInteger("GEN_DNS")
    GEN_URI = rffi_platform.ConstantInteger("GEN_URI")
    GEN_OTHERNAME = rffi_platform.ConstantInteger("GEN_OTHERNAME")
    GEN_X400 = rffi_platform.ConstantInteger("GEN_X400")
    GEN_EDIPARTY = rffi_platform.ConstantInteger("GEN_EDIPARTY")
    GEN_IPADD = rffi_platform.ConstantInteger("GEN_IPADD")
    GEN_RID = rffi_platform.ConstantInteger("GEN_RID")

    CRYPTO_LOCK = rffi_platform.ConstantInteger("CRYPTO_LOCK")

    OBJ_NAME_TYPE_MD_METH = rffi_platform.ConstantInteger(
        "OBJ_NAME_TYPE_MD_METH")

    if OPENSSL_VERSION_NUMBER >= 0x10001000:
        X509_st = rffi_platform.Struct(
            'struct x509_st',
            [('crldp', stack_st_DIST_POINT)])

    # Some structures, with only the fields used in the _ssl module
    X509_name_entry_st = rffi_platform.Struct('struct X509_name_entry_st',
                                              [('set', rffi.INT)])
    asn1_string_st = rffi_platform.Struct('struct asn1_string_st',
                                          [('length', rffi.INT),
                                           ('data', rffi.CCHARP)])
    X509_extension_st = rffi_platform.Struct(
        'struct X509_extension_st',
        [('value', ASN1_STRING)])
    x509_store_st = rffi_platform.Struct(
        'struct x509_store_st',
        [('objs', stack_st_X509_OBJECT),
         ('param', X509_VERIFY_PARAM)])
    x509_object_st = rffi_platform.Struct(
        'struct x509_object_st',
        [('type', rffi.INT)])

    X509_LU_X509 = rffi_platform.ConstantInteger("X509_LU_X509")
    X509_LU_CRL = rffi_platform.ConstantInteger("X509_LU_CRL")

    X509V3_EXT_D2I = lltype.FuncType([rffi.VOIDP, rffi.CCHARPP, rffi.LONG],
                                     rffi.VOIDP)
    v3_ext_method = rffi_platform.Struct(
        'struct v3_ext_method',
        [('it', ASN1_ITEM_EXP),
         ('d2i', lltype.Ptr(X509V3_EXT_D2I))])
    GENERAL_NAME_st = rffi_platform.Struct(
        'struct GENERAL_NAME_st',
        [('type', rffi.INT)])
    EVP_MD_st = rffi_platform.Struct(
        'EVP_MD',
        [('md_size', rffi.INT),
         ('block_size', rffi.INT)])
    EVP_MD_SIZE = rffi_platform.SizeOf('EVP_MD')
    EVP_MD_CTX_SIZE = rffi_platform.SizeOf('EVP_MD_CTX')

    OBJ_NAME_st = rffi_platform.Struct(
        'OBJ_NAME',
        [('alias', rffi.INT),
         ('name', rffi.CCHARP),
         ])

    COMP_METHOD_st = rffi_platform.Struct(
        'struct comp_method_st',
        [('type', rffi.INT),])

    ACCESS_DESCRIPTION_st = rffi_platform.Struct(
        'struct ACCESS_DESCRIPTION_st',
        [('method', ASN1_OBJECT),
         ('location', GENERAL_NAME),])

for k, v in rffi_platform.configure(CConfig).items():
    globals()[k] = v

# opaque structures
SSL_METHOD = rffi.COpaquePtr('SSL_METHOD')
SSL_CTX = rffi.COpaquePtr('SSL_CTX')
SSL_CIPHER = rffi.COpaquePtr('SSL_CIPHER')
SSL = rffi.COpaquePtr('SSL')
BIO = rffi.COpaquePtr('BIO')
if OPENSSL_VERSION_NUMBER >= 0x10001000:
    X509 = rffi.CArrayPtr(X509_st)
else:
    X509 = rffi.COpaquePtr('X509')
X509_NAME_ENTRY = rffi.CArrayPtr(X509_name_entry_st)
X509_EXTENSION = rffi.CArrayPtr(X509_extension_st)
X509_STORE = rffi.CArrayPtr(x509_store_st)
X509_OBJECT = lltype.Ptr(x509_object_st)
X509V3_EXT_METHOD = rffi.CArrayPtr(v3_ext_method)
ASN1_STRING.TO.become(asn1_string_st)
ASN1_TIME = rffi.COpaquePtr('ASN1_TIME')
ASN1_INTEGER = rffi.COpaquePtr('ASN1_INTEGER')
GENERAL_NAMES = rffi.COpaquePtr('GENERAL_NAMES')
GENERAL_NAME.TO.become(GENERAL_NAME_st)
OBJ_NAME = rffi.CArrayPtr(OBJ_NAME_st)
COMP_METHOD = rffi.CArrayPtr(COMP_METHOD_st)
ACCESS_DESCRIPTION = rffi.CArrayPtr(ACCESS_DESCRIPTION_st)

HAVE_OPENSSL_RAND = OPENSSL_VERSION_NUMBER >= 0x0090500f
HAVE_OPENSSL_FINISHED = OPENSSL_VERSION_NUMBER >= 0x0090500f
HAVE_SSL_CTX_CLEAR_OPTIONS = OPENSSL_VERSION_NUMBER >= 0x009080df and \
                             OPENSSL_VERSION_NUMBER != 0x00909000
if OPENSSL_VERSION_NUMBER < 0x0090800f and not OPENSSL_NO_ECDH:
    OPENSSL_NO_ECDH = True


def external(name, argtypes, restype, **kw):
    kw['compilation_info'] = eci
    return rffi.llexternal(
        name, argtypes, restype, **kw)

def ssl_external(name, argtypes, restype, **kw):
    globals()['libssl_' + name] = external(
        name, argtypes, restype, **kw)

ssl_external('SSL_load_error_strings', [], lltype.Void)
ssl_external('SSL_library_init', [], rffi.INT)
ssl_external('CRYPTO_num_locks', [], rffi.INT)
ssl_external('CRYPTO_set_locking_callback',
             [lltype.Ptr(lltype.FuncType(
                [rffi.INT, rffi.INT, rffi.CCHARP, rffi.INT], lltype.Void))],
             lltype.Void)
ssl_external('CRYPTO_set_id_callback',
             [lltype.Ptr(lltype.FuncType([], rffi.LONG))],
             lltype.Void)

if HAVE_OPENSSL_RAND:
    ssl_external('RAND_add', [rffi.CCHARP, rffi.INT, rffi.DOUBLE], lltype.Void)
    ssl_external('RAND_status', [], rffi.INT)
    ssl_external('RAND_egd', [rffi.CCHARP], rffi.INT)
ssl_external('SSL_CTX_new', [SSL_METHOD], SSL_CTX)
ssl_external('SSL_get_SSL_CTX', [SSL], SSL_CTX)
ssl_external('SSL_set_SSL_CTX', [SSL, SSL_CTX], SSL_CTX)
ssl_external('TLSv1_method', [], SSL_METHOD)
if HAVE_TLSv1_2:
    ssl_external('TLSv1_1_method', [], SSL_METHOD)
    ssl_external('TLSv1_2_method', [], SSL_METHOD)
ssl_external('SSLv2_method', [], SSL_METHOD)
ssl_external('SSLv3_method', [], SSL_METHOD)
ssl_external('SSLv23_method', [], SSL_METHOD)
ssl_external('SSL_CTX_use_PrivateKey_file', [SSL_CTX, rffi.CCHARP, rffi.INT], rffi.INT,
             save_err=rffi.RFFI_FULL_ERRNO_ZERO)
ssl_external('SSL_CTX_use_certificate_chain_file', [SSL_CTX, rffi.CCHARP], rffi.INT,
             save_err=rffi.RFFI_FULL_ERRNO_ZERO)
ssl_external('SSL_CTX_get_cert_store', [SSL_CTX], X509_STORE)
ssl_external('SSL_CTX_get_options', [SSL_CTX], rffi.LONG, macro=True)
ssl_external('SSL_CTX_set_options', [SSL_CTX, rffi.LONG], rffi.LONG, macro=True)
if HAVE_SSL_CTX_CLEAR_OPTIONS:
    ssl_external('SSL_CTX_clear_options', [SSL_CTX, rffi.LONG], rffi.LONG,
                 macro=True)
ssl_external('SSL_CTX_ctrl', [SSL_CTX, rffi.INT, rffi.INT, rffi.VOIDP], rffi.INT)
ssl_external('SSL_CTX_set_verify', [SSL_CTX, rffi.INT, rffi.VOIDP], lltype.Void)
ssl_external('SSL_CTX_get_verify_mode', [SSL_CTX], rffi.INT)
ssl_external('SSL_CTX_set_default_verify_paths', [SSL_CTX], rffi.INT)
ssl_external('SSL_CTX_set_cipher_list', [SSL_CTX, rffi.CCHARP], rffi.INT)
ssl_external('SSL_CTX_load_verify_locations',
             [SSL_CTX, rffi.CCHARP, rffi.CCHARP], rffi.INT,
             save_err=rffi.RFFI_FULL_ERRNO_ZERO)
ssl_external('SSL_CTX_check_private_key', [SSL_CTX], rffi.INT)
ssl_external('SSL_CTX_set_session_id_context', [SSL_CTX, rffi.CCHARP, rffi.UINT], rffi.INT)
pem_password_cb = lltype.Ptr(lltype.FuncType([rffi.CCHARP, rffi.INT, rffi.INT, rffi.VOIDP], rffi.INT))
ssl_external('SSL_CTX_set_default_passwd_cb', [SSL_CTX, pem_password_cb], lltype.Void)
ssl_external('SSL_CTX_set_default_passwd_cb_userdata', [SSL_CTX, rffi.VOIDP], lltype.Void)
servername_cb = lltype.Ptr(lltype.FuncType([SSL, rffi.INTP, rffi.VOIDP], rffi.INT))
ssl_external('SSL_CTX_set_tlsext_servername_callback', [SSL_CTX, servername_cb],
             lltype.Void, macro=True)
ssl_external('SSL_CTX_set_tlsext_servername_arg', [SSL_CTX, rffi.VOIDP], lltype.Void, macro=True)
ssl_external('SSL_CTX_set_tmp_ecdh', [SSL_CTX, EC_KEY], lltype.Void, macro=True)
if OPENSSL_VERSION_NUMBER >= 0x10002000:
    ssl_external('SSL_CTX_set_ecdh_auto', [SSL_CTX, rffi.INT], lltype.Void,
                 macro=True)
else:
    libssl_SSL_CTX_set_ecdh_auto = None

SSL_CTX_STATS_NAMES = """
    number connect connect_good connect_renegotiate accept accept_good
    accept_renegotiate hits misses timeouts cache_full""".split()
SSL_CTX_STATS = unrolling_iterable(
    (name, external('SSL_CTX_sess_' + name, [SSL_CTX], rffi.LONG, macro=True))
    for name in SSL_CTX_STATS_NAMES)

ssl_external('SSL_new', [SSL_CTX], SSL)
ssl_external('SSL_set_fd', [SSL, rffi.INT], rffi.INT)
ssl_external('SSL_set_mode', [SSL, rffi.INT], rffi.INT, macro=True)
ssl_external('SSL_ctrl', [SSL, rffi.INT, rffi.INT, rffi.VOIDP], rffi.INT)
ssl_external('BIO_ctrl', [BIO, rffi.INT, rffi.INT, rffi.VOIDP], rffi.INT)
ssl_external('SSL_get_rbio', [SSL], BIO)
ssl_external('SSL_get_wbio', [SSL], BIO)
ssl_external('SSL_set_connect_state', [SSL], lltype.Void)
ssl_external('SSL_set_accept_state', [SSL], lltype.Void)
ssl_external('SSL_connect', [SSL], rffi.INT)
ssl_external('SSL_do_handshake', [SSL], rffi.INT, save_err=SAVE_ERR)
ssl_external('SSL_shutdown', [SSL], rffi.INT, save_err=SAVE_ERR)
ssl_external('SSL_get_error', [SSL, rffi.INT], rffi.INT)
ssl_external('SSL_get_shutdown', [SSL], rffi.INT)
ssl_external('SSL_set_read_ahead', [SSL, rffi.INT], lltype.Void)
ssl_external('SSL_set_tlsext_host_name', [SSL, rffi.CCHARP], rffi.INT, macro=True)
ssl_external('SSL_session_reused', [SSL], rffi.INT, macro=True)
ssl_external('SSL_get_finished', [SSL, rffi.CCHARP, rffi.SIZE_T], rffi.SIZE_T)
ssl_external('SSL_get_peer_finished', [SSL, rffi.CCHARP, rffi.SIZE_T], rffi.SIZE_T)
ssl_external('SSL_get_current_compression', [SSL], COMP_METHOD)
ssl_external('SSL_get_version', [SSL], rffi.CCHARP)

ssl_external('SSL_get_peer_certificate', [SSL], X509)
ssl_external('SSL_get_servername', [SSL, rffi.INT], rffi.CCHARP)
ssl_external('SSL_get_app_data', [SSL], rffi.VOIDP, macro=True)
ssl_external('SSL_set_app_data', [SSL, rffi.VOIDP], lltype.Void, macro=True)
ssl_external('X509_get_subject_name', [X509], X509_NAME)
ssl_external('X509_get_issuer_name', [X509], X509_NAME)
ssl_external('X509_NAME_oneline', [X509_NAME, rffi.CCHARP, rffi.INT], rffi.CCHARP)
ssl_external('X509_NAME_entry_count', [X509_NAME], rffi.INT)
ssl_external('X509_NAME_get_entry', [X509_NAME, rffi.INT], X509_NAME_ENTRY)
ssl_external('X509_NAME_ENTRY_get_object', [X509_NAME_ENTRY], ASN1_OBJECT)
ssl_external('X509_NAME_ENTRY_get_data', [X509_NAME_ENTRY], ASN1_STRING)
ssl_external('i2d_X509', [X509, rffi.CCHARPP], rffi.INT, save_err=SAVE_ERR)
ssl_external('X509_free', [X509], lltype.Void, releasegil=False)
ssl_external('X509_check_ca', [X509], rffi.INT)
ssl_external('X509_get_notBefore', [X509], ASN1_TIME, macro=True)
ssl_external('X509_get_notAfter', [X509], ASN1_TIME, macro=True)
ssl_external('X509_get_serialNumber', [X509], ASN1_INTEGER)
ssl_external('X509_get_version', [X509], rffi.INT, macro=True)
ssl_external('X509_get_ext_by_NID', [X509, rffi.INT, rffi.INT], rffi.INT)
ssl_external('X509_get_ext', [X509, rffi.INT], X509_EXTENSION)
ssl_external('X509_get_ext_d2i', [X509, rffi.INT, rffi.VOIDP, rffi.VOIDP], rffi.VOIDP)
ssl_external('X509V3_EXT_get', [X509_EXTENSION], X509V3_EXT_METHOD)

ssl_external('X509_VERIFY_PARAM_get_flags', [X509_VERIFY_PARAM], rffi.ULONG)
ssl_external('X509_VERIFY_PARAM_set_flags', [X509_VERIFY_PARAM, rffi.ULONG], rffi.INT)
ssl_external('X509_VERIFY_PARAM_clear_flags', [X509_VERIFY_PARAM, rffi.ULONG], rffi.INT)
ssl_external('X509_STORE_add_cert', [X509_STORE, X509], rffi.INT)

ssl_external('X509_get_default_cert_file_env', [], rffi.CCHARP)
ssl_external('X509_get_default_cert_file', [], rffi.CCHARP)
ssl_external('X509_get_default_cert_dir_env', [], rffi.CCHARP)
ssl_external('X509_get_default_cert_dir', [], rffi.CCHARP)

ssl_external('OBJ_obj2txt',
             [rffi.CCHARP, rffi.INT, ASN1_OBJECT, rffi.INT], rffi.INT,
             save_err=SAVE_ERR)
ssl_external('OBJ_obj2nid', [ASN1_OBJECT], rffi.INT)
ssl_external('OBJ_nid2sn', [rffi.INT], rffi.CCHARP)
ssl_external('OBJ_sn2nid', [rffi.CCHARP], rffi.INT)
ssl_external('OBJ_nid2ln', [rffi.INT], rffi.CCHARP)
ssl_external('OBJ_txt2obj', [rffi.CCHARP, rffi.INT], ASN1_OBJECT)
ssl_external('OBJ_nid2obj', [rffi.INT], ASN1_OBJECT)
ssl_external('ASN1_OBJECT_free', [ASN1_OBJECT], lltype.Void)
ssl_external('ASN1_STRING_data', [ASN1_STRING], rffi.CCHARP)
ssl_external('ASN1_STRING_length', [ASN1_STRING], rffi.INT)
ssl_external('ASN1_STRING_to_UTF8', [rffi.CCHARPP, ASN1_STRING], rffi.INT,
             save_err=SAVE_ERR)
ssl_external('ASN1_TIME_print', [BIO, ASN1_TIME], rffi.INT)
ssl_external('i2a_ASN1_INTEGER', [BIO, ASN1_INTEGER], rffi.INT)
ssl_external('ASN1_item_d2i',
             [rffi.VOIDP, rffi.CCHARPP, rffi.LONG, ASN1_ITEM], rffi.VOIDP)
ssl_external('ASN1_ITEM_ptr', [ASN1_ITEM_EXP], ASN1_ITEM, macro=True)

ssl_external('sk_GENERAL_NAME_num', [GENERAL_NAMES], rffi.INT,
             macro=True)
ssl_external('sk_GENERAL_NAME_value', [GENERAL_NAMES, rffi.INT], GENERAL_NAME,
             macro=True)
ssl_external('sk_X509_OBJECT_num', [stack_st_X509_OBJECT], rffi.INT,
             macro=True)
ssl_external('sk_X509_OBJECT_value', [stack_st_X509_OBJECT, rffi.INT],
             X509_OBJECT, macro=True)
ssl_external('pypy_X509_OBJECT_data_x509', [X509_OBJECT], X509,
             macro=True)
ssl_external('sk_DIST_POINT_num', [stack_st_DIST_POINT], rffi.INT,
             macro=True)
ssl_external('sk_DIST_POINT_value', [stack_st_DIST_POINT, rffi.INT], DIST_POINT,
             macro=True)
ssl_external('pypy_DIST_POINT_fullname', [DIST_POINT], GENERAL_NAMES,
             macro=True)
ssl_external('sk_ACCESS_DESCRIPTION_num', [AUTHORITY_INFO_ACCESS], rffi.INT,
             macro=True)
ssl_external('sk_ACCESS_DESCRIPTION_value', [AUTHORITY_INFO_ACCESS, rffi.INT], ACCESS_DESCRIPTION,
             macro=True)
ssl_external('AUTHORITY_INFO_ACCESS_free', [AUTHORITY_INFO_ACCESS], lltype.Void)

ssl_external('GENERAL_NAME_print', [BIO, GENERAL_NAME], rffi.INT)
ssl_external('pypy_GENERAL_NAME_dirn', [GENERAL_NAME], X509_NAME,
             macro=True)
ssl_external('pypy_GENERAL_NAME_uri', [GENERAL_NAME], ASN1_IA5STRING,
             macro=True)

ssl_external('SSL_get_current_cipher', [SSL], SSL_CIPHER)
ssl_external('SSL_CIPHER_get_name', [SSL_CIPHER], rffi.CCHARP)
ssl_external('SSL_CIPHER_get_version', [SSL_CIPHER], rffi.CCHARP)
ssl_external('SSL_CIPHER_get_bits', [SSL_CIPHER, rffi.INTP], rffi.INT)

ssl_external('EC_KEY_new_by_curve_name', [rffi.INT], EC_KEY)
ssl_external('EC_KEY_free', [EC_KEY], lltype.Void)

ssl_external('ERR_get_error', [], rffi.INT)
ssl_external('ERR_peek_last_error', [], rffi.INT)
ssl_external('ERR_error_string', [rffi.ULONG, rffi.CCHARP], rffi.CCHARP)
ssl_external('ERR_reason_error_string', [rffi.ULONG], rffi.CCHARP)
ssl_external('ERR_clear_error', [], lltype.Void)
ssl_external('ERR_GET_LIB', [rffi.ULONG], rffi.INT, macro=True)
ssl_external('ERR_GET_REASON', [rffi.ULONG], rffi.INT, macro=True)

# 'releasegil=False' here indicates that this function will be called
# with the GIL held, and so is allowed to run in a RPython __del__ method.
ssl_external('SSL_free', [SSL], lltype.Void, releasegil=False)
ssl_external('SSL_CTX_free', [SSL_CTX], lltype.Void, releasegil=False)
ssl_external('CRYPTO_free', [rffi.VOIDP], lltype.Void)
libssl_OPENSSL_free = libssl_CRYPTO_free

ssl_external('SSL_write', [SSL, rffi.CCHARP, rffi.INT], rffi.INT,
             save_err=SAVE_ERR)
ssl_external('SSL_pending', [SSL], rffi.INT,
             save_err=SAVE_ERR)
ssl_external('SSL_read', [SSL, rffi.CCHARP, rffi.INT], rffi.INT,
             save_err=SAVE_ERR)

BIO_METHOD = rffi.COpaquePtr('BIO_METHOD')
ssl_external('BIO_s_mem', [], BIO_METHOD)
ssl_external('BIO_s_file', [], BIO_METHOD)
ssl_external('BIO_new', [BIO_METHOD], BIO)
ssl_external('BIO_set_nbio', [BIO, rffi.INT], rffi.INT, macro=True)
ssl_external('BIO_new_file', [rffi.CCHARP, rffi.CCHARP], BIO,
             save_err=rffi.RFFI_FULL_ERRNO_ZERO)
ssl_external('BIO_new_mem_buf', [rffi.VOIDP, rffi.INT], BIO)
ssl_external('BIO_free', [BIO], rffi.INT)
ssl_external('BIO_reset', [BIO], rffi.INT, macro=True)
ssl_external('BIO_read_filename', [BIO, rffi.CCHARP], rffi.INT, macro=True)
ssl_external('BIO_gets', [BIO, rffi.CCHARP, rffi.INT], rffi.INT,
             save_err=SAVE_ERR)
ssl_external('d2i_X509_bio', [BIO, rffi.VOIDP], X509)
ssl_external('PEM_read_bio_X509',
             [BIO, rffi.VOIDP, rffi.VOIDP, rffi.VOIDP], X509)
ssl_external('PEM_read_bio_X509_AUX',
             [BIO, rffi.VOIDP, rffi.VOIDP, rffi.VOIDP], X509)

ssl_external('PEM_read_bio_DHparams',
             [BIO, rffi.VOIDP, rffi.VOIDP, rffi.VOIDP], DH,
             save_err=rffi.RFFI_FULL_ERRNO_ZERO)
ssl_external('SSL_CTX_set_tmp_dh', [SSL_CTX, DH], rffi.INT, macro=True)
ssl_external('DH_free', [DH], lltype.Void, releasegil=False)

if HAS_NPN:
    SSL_NEXT_PROTOS_ADV_CB = lltype.Ptr(lltype.FuncType(
        [SSL, rffi.CCHARPP, rffi.UINTP, rffi.VOIDP], rffi.INT))
    ssl_external('SSL_CTX_set_next_protos_advertised_cb',
                 [SSL_CTX, SSL_NEXT_PROTOS_ADV_CB, rffi.VOIDP], lltype.Void)
    SSL_NEXT_PROTOS_SEL_CB = lltype.Ptr(lltype.FuncType(
        [SSL, rffi.CCHARPP, rffi.UCHARP, rffi.CCHARP, rffi.UINT, rffi.VOIDP],
        rffi.INT))
    ssl_external('SSL_CTX_set_next_proto_select_cb',
                 [SSL_CTX, SSL_NEXT_PROTOS_SEL_CB, rffi.VOIDP], lltype.Void)
    ssl_external(
        'SSL_select_next_proto', [rffi.CCHARPP, rffi.UCHARP,
                                  rffi.CCHARP, rffi.UINT,
                                  rffi.CCHARP, rffi.UINT], rffi.INT)
    ssl_external(
        'SSL_get0_next_proto_negotiated', [
            SSL, rffi.CCHARPP, rffi.UINTP], lltype.Void)

EVP_MD_CTX = rffi.COpaquePtr('EVP_MD_CTX', compilation_info=eci)
EVP_MD     = lltype.Ptr(EVP_MD_st)

OpenSSL_add_all_digests = external(
    'OpenSSL_add_all_digests', [], lltype.Void)
EVP_get_digestbyname = external(
    'EVP_get_digestbyname',
    [rffi.CCHARP], EVP_MD)
EVP_DigestInit = external(
    'EVP_DigestInit',
    [EVP_MD_CTX, EVP_MD], rffi.INT)
EVP_DigestUpdate = external(
    'EVP_DigestUpdate',
    [EVP_MD_CTX, rffi.CCHARP, rffi.SIZE_T], rffi.INT)
EVP_DigestFinal = external(
    'EVP_DigestFinal',
    [EVP_MD_CTX, rffi.CCHARP, rffi.VOIDP], rffi.INT)
EVP_MD_CTX_copy = external(
    'EVP_MD_CTX_copy', [EVP_MD_CTX, EVP_MD_CTX], rffi.INT)
EVP_MD_CTX_cleanup = external(
    'EVP_MD_CTX_cleanup', [EVP_MD_CTX], rffi.INT, releasegil=False)

OBJ_NAME_CALLBACK = lltype.Ptr(lltype.FuncType(
        [OBJ_NAME, rffi.VOIDP], lltype.Void))
OBJ_NAME_do_all = external(
    'OBJ_NAME_do_all', [rffi.INT, OBJ_NAME_CALLBACK, rffi.VOIDP], lltype.Void)

# HASH_MALLOC_SIZE is the size of EVP_MD, EVP_MD_CTX plus their points
# Used for adding memory pressure. Last number is an (under?)estimate of
# EVP_PKEY_CTX's size.
# XXX: Make a better estimate here
HASH_MALLOC_SIZE = EVP_MD_SIZE + EVP_MD_CTX_SIZE \
        + rffi.sizeof(EVP_MD) * 2 + 208

OBJ_NAME_CALLBACK = lltype.Ptr(lltype.FuncType(
        [OBJ_NAME, rffi.VOIDP], lltype.Void))
OBJ_NAME_do_all = external(
    'OBJ_NAME_do_all', [rffi.INT, OBJ_NAME_CALLBACK, rffi.VOIDP], lltype.Void)

def init_ssl():
    libssl_SSL_load_error_strings()
    libssl_SSL_library_init()

def init_digests():
    OpenSSL_add_all_digests()
