""" External functions accessing the win32 api.
Common types, functions from core win32 libraries, such as kernel32
"""

import os
import errno

from rpython.rtyper.module.ll_os_environ import make_env_impls
from rpython.rtyper.tool import rffi_platform
from rpython.tool.udir import udir
from rpython.translator.tool.cbuild import ExternalCompilationInfo
from rpython.translator.platform import CompilationError
from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.rlib.rarithmetic import intmask
from rpython.rlib import jit

# This module can be imported on any platform,
# but most symbols are not usable...
WIN32 = os.name == "nt"

if WIN32:
    eci = ExternalCompilationInfo(
        includes = ['windows.h', 'stdio.h', 'stdlib.h'],
        libraries = ['kernel32'],
        )
else:
    eci = ExternalCompilationInfo()

class CConfig:
    _compilation_info_ = eci

    if WIN32:
        DWORD_PTR = rffi_platform.SimpleType("DWORD_PTR", rffi.LONG)
        WORD = rffi_platform.SimpleType("WORD", rffi.UINT)
        DWORD = rffi_platform.SimpleType("DWORD", rffi.UINT)
        BOOL = rffi_platform.SimpleType("BOOL", rffi.LONG)
        BYTE = rffi_platform.SimpleType("BYTE", rffi.UCHAR)
        WCHAR = rffi_platform.SimpleType("WCHAR", rffi.UCHAR)
        INT = rffi_platform.SimpleType("INT", rffi.INT)
        LONG = rffi_platform.SimpleType("LONG", rffi.LONG)
        PLONG = rffi_platform.SimpleType("PLONG", rffi.LONGP)
        LPVOID = rffi_platform.SimpleType("LPVOID", rffi.INTP)
        LPCVOID = rffi_platform.SimpleType("LPCVOID", rffi.VOIDP)
        LPSTR = rffi_platform.SimpleType("LPSTR", rffi.CCHARP)
        LPCSTR = rffi_platform.SimpleType("LPCSTR", rffi.CCHARP)
        LPWSTR = rffi_platform.SimpleType("LPWSTR", rffi.CWCHARP)
        LPCWSTR = rffi_platform.SimpleType("LPCWSTR", rffi.CWCHARP)
        LPDWORD = rffi_platform.SimpleType("LPDWORD", rffi.UINTP)
        SIZE_T = rffi_platform.SimpleType("SIZE_T", rffi.SIZE_T)
        ULONG_PTR = rffi_platform.SimpleType("ULONG_PTR", rffi.ULONG)

        HRESULT = rffi_platform.SimpleType("HRESULT", rffi.LONG)
        HLOCAL = rffi_platform.SimpleType("HLOCAL", rffi.VOIDP)

        FILETIME = rffi_platform.Struct('FILETIME',
                                        [('dwLowDateTime', rffi.UINT),
                                         ('dwHighDateTime', rffi.UINT)])
        SYSTEMTIME = rffi_platform.Struct('SYSTEMTIME',
                                          [])

        OSVERSIONINFOEX = rffi_platform.Struct(
            'OSVERSIONINFOEX',
            [('dwOSVersionInfoSize', rffi.UINT),
             ('dwMajorVersion', rffi.UINT),
             ('dwMinorVersion', rffi.UINT),
             ('dwBuildNumber',  rffi.UINT),
             ('dwPlatformId',  rffi.UINT),
             ('szCSDVersion', rffi.CFixedArray(lltype.Char, 1)),
             ('wServicePackMajor', rffi.USHORT),
             ('wServicePackMinor', rffi.USHORT),
             ('wSuiteMask', rffi.USHORT),
             ('wProductType', rffi.UCHAR),
         ])

        LPSECURITY_ATTRIBUTES = rffi_platform.SimpleType(
            "LPSECURITY_ATTRIBUTES", rffi.CCHARP)

        DEFAULT_LANGUAGE = rffi_platform.ConstantInteger(
            "MAKELANGID(LANG_NEUTRAL, SUBLANG_DEFAULT)")

        defines = """FORMAT_MESSAGE_ALLOCATE_BUFFER FORMAT_MESSAGE_FROM_SYSTEM
                       MAX_PATH _MAX_ENV FORMAT_MESSAGE_IGNORE_INSERTS
                       WAIT_OBJECT_0 WAIT_TIMEOUT INFINITE
                       ERROR_INVALID_HANDLE
                       DELETE READ_CONTROL SYNCHRONIZE WRITE_DAC
                       WRITE_OWNER PROCESS_ALL_ACCESS
                       PROCESS_CREATE_PROCESS PROCESS_CREATE_THREAD
                       PROCESS_DUP_HANDLE PROCESS_QUERY_INFORMATION
                       PROCESS_SET_QUOTA
                       PROCESS_SUSPEND_RESUME PROCESS_TERMINATE
                       PROCESS_VM_OPERATION PROCESS_VM_READ
                       PROCESS_VM_WRITE
                       CTRL_C_EVENT CTRL_BREAK_EVENT
                       MB_ERR_INVALID_CHARS ERROR_NO_UNICODE_TRANSLATION
                       WC_NO_BEST_FIT_CHARS
                    """
        from rpython.translator.platform import host_factory
        static_platform = host_factory()
        if static_platform.name == 'msvc':
            defines += ' PROCESS_QUERY_LIMITED_INFORMATION' 
        for name in defines.split():
            locals()[name] = rffi_platform.ConstantInteger(name)

for k, v in rffi_platform.configure(CConfig).items():
    globals()[k] = v

def winexternal(name, args, result, **kwds):
    return rffi.llexternal(name, args, result, compilation_info=eci,
                           calling_conv='win', **kwds)

if WIN32:
    HANDLE = rffi.COpaquePtr(typedef='HANDLE')
    assert rffi.cast(HANDLE, -1) == rffi.cast(HANDLE, -1)

    LPHANDLE = rffi.CArrayPtr(HANDLE)
    HMODULE = HANDLE
    NULL_HANDLE = rffi.cast(HANDLE, 0)
    INVALID_HANDLE_VALUE = rffi.cast(HANDLE, -1)
    PFILETIME = rffi.CArrayPtr(FILETIME)

    _GetLastError = winexternal('GetLastError', [], DWORD,
                                _nowrapper=True, sandboxsafe=True)
    _SetLastError = winexternal('SetLastError', [DWORD], lltype.Void,
                                _nowrapper=True, sandboxsafe=True)

    def GetLastError_saved():
        """Return the value of the "saved LastError".
        The C-level GetLastError() is saved there after a call to a C
        function, if that C function was declared with the flag
        llexternal(..., save_err=rffi.RFFI_SAVE_LASTERROR).
        Functions without that flag don't change the saved LastError.
        Alternatively, if the function was declared RFFI_SAVE_WSALASTERROR,
        then the value of the C-level WSAGetLastError() is saved instead
        (into the same "saved LastError" variable).
        """
        from rpython.rlib import rthread
        return rffi.cast(lltype.Signed, rthread.tlfield_rpy_lasterror.getraw())

    def SetLastError_saved(err):
        """Set the value of the saved LastError.  This value will be used in
        a call to the C-level SetLastError() just before calling the
        following C function, provided it was declared
        llexternal(..., save_err=RFFI_READSAVED_LASTERROR).
        """
        from rpython.rlib import rthread
        rthread.tlfield_rpy_lasterror.setraw(rffi.cast(DWORD, err))

    def GetLastError_alt_saved():
        """Return the value of the "saved alt LastError".
        The C-level GetLastError() is saved there after a call to a C
        function, if that C function was declared with the flag
        llexternal(..., save_err=RFFI_SAVE_LASTERROR | RFFI_ALT_ERRNO).
        Functions without that flag don't change the saved LastError.
        Alternatively, if the function was declared 
        RFFI_SAVE_WSALASTERROR | RFFI_ALT_ERRNO,
        then the value of the C-level WSAGetLastError() is saved instead
        (into the same "saved alt LastError" variable).
        """
        from rpython.rlib import rthread
        return rffi.cast(lltype.Signed, rthread.tlfield_alt_lasterror.getraw())

    def SetLastError_alt_saved(err):
        """Set the value of the saved alt LastError.  This value will be used in
        a call to the C-level SetLastError() just before calling the
        following C function, provided it was declared
        llexternal(..., save_err=RFFI_READSAVED_LASTERROR | RFFI_ALT_ERRNO).
        """
        from rpython.rlib import rthread
        rthread.tlfield_alt_lasterror.setraw(rffi.cast(DWORD, err))

    # In tests, the first call to _GetLastError() is always wrong,
    # because error is hidden by operations in ll2ctypes.  Call it now.
    _GetLastError()

    GetModuleHandle = winexternal('GetModuleHandleA', [rffi.CCHARP], HMODULE)
    LoadLibrary = winexternal('LoadLibraryA', [rffi.CCHARP], HMODULE,
                              save_err=rffi.RFFI_SAVE_LASTERROR)
    GetProcAddress = winexternal('GetProcAddress',
                                 [HMODULE, rffi.CCHARP],
                                 rffi.VOIDP)
    FreeLibrary = winexternal('FreeLibrary', [HMODULE], BOOL, releasegil=False)

    LocalFree = winexternal('LocalFree', [HLOCAL], DWORD)
    CloseHandle = winexternal('CloseHandle', [HANDLE], BOOL, releasegil=False,
                              save_err=rffi.RFFI_SAVE_LASTERROR)
    CloseHandle_no_err = winexternal('CloseHandle', [HANDLE], BOOL,
                                     releasegil=False)

    FormatMessage = winexternal(
        'FormatMessageA',
        [DWORD, rffi.VOIDP, DWORD, DWORD, rffi.CCHARP, DWORD, rffi.VOIDP],
        DWORD)

    _get_osfhandle = rffi.llexternal('_get_osfhandle', [rffi.INT], HANDLE)

    def get_osfhandle(fd):
        from rpython.rlib.rposix import validate_fd
        validate_fd(fd)
        handle = _get_osfhandle(fd)
        if handle == INVALID_HANDLE_VALUE:
            raise WindowsError(ERROR_INVALID_HANDLE, "Invalid file handle")
        return handle

    def build_winerror_to_errno():
        """Build a dictionary mapping windows error numbers to POSIX errno.
        The function returns the dict, and the default value for codes not
        in the dict."""
        # Prior to Visual Studio 8, the MSVCRT dll doesn't export the
        # _dosmaperr() function, which is available only when compiled
        # against the static CRT library.
        from rpython.translator.platform import host_factory
        static_platform = host_factory()
        if static_platform.name == 'msvc':
            static_platform.cflags = ['/MT']  # static CRT
            static_platform.version = 0       # no manifest
        cfile = udir.join('dosmaperr.c')
        cfile.write(r'''
                #include <errno.h>
                #include <WinError.h>
                #include <stdio.h>
                #ifdef __GNUC__
                #define _dosmaperr mingw_dosmaperr
                #endif
                int main()
                {
                    int i;
                    for(i=1; i < 65000; i++) {
                        _dosmaperr(i);
                        if (errno == EINVAL) {
                            /* CPython issue #12802 */
                            if (i == ERROR_DIRECTORY)
                                errno = ENOTDIR;
                            else
                                continue;
                        }
                        printf("%d\t%d\n", i, errno);
                    }
                    return 0;
                }''')
        try:
            exename = static_platform.compile(
                [cfile], ExternalCompilationInfo(),
                outputfilename = "dosmaperr",
                standalone=True)
        except (CompilationError, WindowsError):
            # Fallback for the mingw32 compiler
            assert static_platform.name == 'mingw32'
            errors = {
                2: 2, 3: 2, 4: 24, 5: 13, 6: 9, 7: 12, 8: 12, 9: 12, 10: 7,
                11: 8, 15: 2, 16: 13, 17: 18, 18: 2, 19: 13, 20: 13, 21: 13,
                22: 13, 23: 13, 24: 13, 25: 13, 26: 13, 27: 13, 28: 13,
                29: 13, 30: 13, 31: 13, 32: 13, 33: 13, 34: 13, 35: 13,
                36: 13, 53: 2, 65: 13, 67: 2, 80: 17, 82: 13, 83: 13, 89: 11,
                108: 13, 109: 32, 112: 28, 114: 9, 128: 10, 129: 10, 130: 9,
                132: 13, 145: 41, 158: 13, 161: 2, 164: 11, 167: 13, 183: 17,
                188: 8, 189: 8, 190: 8, 191: 8, 192: 8, 193: 8, 194: 8,
                195: 8, 196: 8, 197: 8, 198: 8, 199: 8, 200: 8, 201: 8,
                202: 8, 206: 2, 215: 11, 267: 20, 1816: 12,
                }
        else:
            output = os.popen(str(exename))
            errors = dict(map(int, line.split())
                          for line in output)
        return errors, errno.EINVAL

    # A bit like strerror...
    def FormatError(code):
        return llimpl_FormatError(code)

    def llimpl_FormatError(code):
        "Return a message corresponding to the given Windows error code."
        buf = lltype.malloc(rffi.CCHARPP.TO, 1, flavor='raw')
        buf[0] = lltype.nullptr(rffi.CCHARP.TO)
        try:
            msglen = FormatMessage(FORMAT_MESSAGE_ALLOCATE_BUFFER |
                                   FORMAT_MESSAGE_FROM_SYSTEM | 
                                   FORMAT_MESSAGE_IGNORE_INSERTS,
                                   None,
                                   rffi.cast(DWORD, code),
                                   DEFAULT_LANGUAGE,
                                   rffi.cast(rffi.CCHARP, buf),
                                   0, None)
            buflen = intmask(msglen)

            # remove trailing cr/lf and dots
            s_buf = buf[0]
            while buflen > 0 and (s_buf[buflen - 1] <= ' ' or
                                  s_buf[buflen - 1] == '.'):
                buflen -= 1

            if buflen <= 0:
                result = fake_FormatError(code)
            else:
                result = rffi.charpsize2str(s_buf, buflen)
        finally:
            LocalFree(rffi.cast(rffi.VOIDP, buf[0]))
            lltype.free(buf, flavor='raw')

        return result

    def fake_FormatError(code):
        return 'Windows Error %d' % (code,)

    def lastSavedWindowsError(context="Windows Error"):
        code = GetLastError_saved()
        return WindowsError(code, context)

    def FAILED(hr):
        return rffi.cast(HRESULT, hr) < 0

    _GetModuleFileName = winexternal('GetModuleFileNameA',
                                     [HMODULE, rffi.CCHARP, DWORD],
                                     DWORD)

    def GetModuleFileName(module):
        size = MAX_PATH
        buf = lltype.malloc(rffi.CCHARP.TO, size, flavor='raw')
        try:
            res = _GetModuleFileName(module, buf, size)
            if not res:
                return ''
            else:
                return ''.join([buf[i] for i in range(res)])
        finally:
            lltype.free(buf, flavor='raw')

    _GetVersionEx = winexternal('GetVersionExA',
                                [lltype.Ptr(OSVERSIONINFOEX)],
                                DWORD,
                                save_err=rffi.RFFI_SAVE_LASTERROR)

    @jit.dont_look_inside
    def GetVersionEx():
        info = lltype.malloc(OSVERSIONINFOEX, flavor='raw')
        rffi.setintfield(info, 'c_dwOSVersionInfoSize',
                         rffi.sizeof(OSVERSIONINFOEX))
        try:
            if not _GetVersionEx(info):
                raise lastSavedWindowsError()
            return (rffi.cast(lltype.Signed, info.c_dwMajorVersion),
                    rffi.cast(lltype.Signed, info.c_dwMinorVersion),
                    rffi.cast(lltype.Signed, info.c_dwBuildNumber),
                    rffi.cast(lltype.Signed, info.c_dwPlatformId),
                    rffi.charp2str(rffi.cast(rffi.CCHARP,
                                             info.c_szCSDVersion)),
                    rffi.cast(lltype.Signed, info.c_wServicePackMajor),
                    rffi.cast(lltype.Signed, info.c_wServicePackMinor),
                    rffi.cast(lltype.Signed, info.c_wSuiteMask),
                    rffi.cast(lltype.Signed, info.c_wProductType))
        finally:
            lltype.free(info, flavor='raw')

    _WaitForSingleObject = winexternal(
        'WaitForSingleObject', [HANDLE, DWORD], DWORD,
        save_err=rffi.RFFI_SAVE_LASTERROR)

    def WaitForSingleObject(handle, timeout):
        """Return values:
        - WAIT_OBJECT_0 when the object is signaled
        - WAIT_TIMEOUT when the timeout elapsed"""
        res = _WaitForSingleObject(handle, timeout)
        if res == rffi.cast(DWORD, -1):
            raise lastSavedWindowsError("WaitForSingleObject")
        return res

    _WaitForMultipleObjects = winexternal(
        'WaitForMultipleObjects', [
            DWORD, rffi.CArrayPtr(HANDLE), BOOL, DWORD], DWORD,
            save_err=rffi.RFFI_SAVE_LASTERROR)

    def WaitForMultipleObjects(handles, waitall=False, timeout=INFINITE):
        """Return values:
        - WAIT_OBJECT_0 + index when an object is signaled
        - WAIT_TIMEOUT when the timeout elapsed"""
        nb = len(handles)
        handle_array = lltype.malloc(rffi.CArrayPtr(HANDLE).TO, nb,
                                     flavor='raw')
        try:
            for i in range(nb):
                handle_array[i] = handles[i]
            res = _WaitForMultipleObjects(nb, handle_array, waitall, timeout)
            if res == rffi.cast(DWORD, -1):
                raise lastSavedWindowsError("WaitForMultipleObjects")
            return res
        finally:
            lltype.free(handle_array, flavor='raw')

    _CreateEvent = winexternal(
        'CreateEventA', [rffi.VOIDP, BOOL, BOOL, LPCSTR], HANDLE,
        save_err=rffi.RFFI_SAVE_LASTERROR)
    def CreateEvent(*args):
        handle = _CreateEvent(*args)
        if handle == NULL_HANDLE:
            raise lastSavedWindowsError("CreateEvent")
        return handle
    SetEvent = winexternal(
        'SetEvent', [HANDLE], BOOL)
    ResetEvent = winexternal(
        'ResetEvent', [HANDLE], BOOL)
    _OpenProcess = winexternal(
        'OpenProcess', [DWORD, BOOL, DWORD], HANDLE,
        save_err=rffi.RFFI_SAVE_LASTERROR)
    def OpenProcess(*args):
        ''' OpenProcess( dwDesiredAccess, bInheritHandle, dwProcessId)
        where dwDesiredAccess is a combination of the flags:
        DELETE (0x00010000L)
        READ_CONTROL (0x00020000L)
        SYNCHRONIZE (0x00100000L)
        WRITE_DAC (0x00040000L)
        WRITE_OWNER (0x00080000L)

        PROCESS_ALL_ACCESS
        PROCESS_CREATE_PROCESS (0x0080)
        PROCESS_CREATE_THREAD (0x0002)
        PROCESS_DUP_HANDLE (0x0040)
        PROCESS_QUERY_INFORMATION (0x0400)
        PROCESS_QUERY_LIMITED_INFORMATION (0x1000)
        PROCESS_SET_QUOTA (0x0100)
        PROCESS_SUSPEND_RESUME (0x0800)
        PROCESS_TERMINATE (0x0001)
        PROCESS_VM_OPERATION (0x0008)
        PROCESS_VM_READ (0x0010)
        PROCESS_VM_WRITE (0x0020)
        SYNCHRONIZE (0x00100000L)
        '''
        handle = _OpenProcess(*args)
        if handle == NULL_HANDLE:
            raise lastSavedWindowsError("OpenProcess")
        return handle
    TerminateProcess = winexternal(
        'TerminateProcess', [HANDLE, rffi.UINT], BOOL,
        save_err=rffi.RFFI_SAVE_LASTERROR)
    GenerateConsoleCtrlEvent = winexternal(
        'GenerateConsoleCtrlEvent', [DWORD, DWORD], BOOL,
        save_err=rffi.RFFI_SAVE_LASTERROR)
    _GetCurrentProcessId = winexternal(
        'GetCurrentProcessId', [], DWORD)
    def GetCurrentProcessId():
        return rffi.cast(lltype.Signed, _GetCurrentProcessId())

    _GetConsoleCP = winexternal('GetConsoleCP', [], DWORD)
    _GetConsoleOutputCP = winexternal('GetConsoleOutputCP', [], DWORD)
    def GetConsoleCP():
        return rffi.cast(lltype.Signed, _GetConsoleCP())
    def GetConsoleOutputCP():
        return rffi.cast(lltype.Signed, _GetConsoleOutputCP())

    def os_kill(pid, sig):
        if sig == CTRL_C_EVENT or sig == CTRL_BREAK_EVENT:
            if GenerateConsoleCtrlEvent(sig, pid) == 0:
                raise lastSavedWindowsError('os_kill failed generating event')
            return
        handle = OpenProcess(PROCESS_ALL_ACCESS, False, pid)
        if handle == NULL_HANDLE:
            raise lastSavedWindowsError('os_kill failed opening process')
        try:
            if TerminateProcess(handle, sig) == 0:
                raise lastSavedWindowsError(
                    'os_kill failed to terminate process')
        finally:
            CloseHandle(handle)

    _wenviron_items, _wgetenv, _wputenv = make_env_impls(win32=True)
