"""
Low-level implementations for the external functions of the 'time' module.
"""

import time, sys, math
from errno import EINTR
from rpython.rtyper.lltypesystem import rffi
from rpython.rtyper.tool import rffi_platform as platform
from rpython.rtyper.lltypesystem import lltype
from rpython.rtyper.extfunc import BaseLazyRegistering, registering, extdef
from rpython.rlib import rposix
from rpython.rlib.rarithmetic import intmask, UINT_MAX
from rpython.translator.tool.cbuild import ExternalCompilationInfo

if sys.platform == 'win32':
    TIME_H = 'time.h'
    FTIME = '_ftime64'
    STRUCT_TIMEB = 'struct __timeb64'
    includes = ['winsock2.h', 'windows.h',
                TIME_H, 'sys/types.h', 'sys/timeb.h']
    need_rusage = False
else:
    TIME_H = 'sys/time.h'
    FTIME = 'ftime'
    STRUCT_TIMEB = 'struct timeb'
    includes = [TIME_H, 'time.h', 'errno.h', 'sys/select.h',
                'sys/types.h', 'unistd.h',
                'sys/time.h', 'sys/resource.h']

    if not sys.platform.startswith("openbsd"):
        includes.append('sys/timeb.h')

    need_rusage = True


class CConfig:
    _compilation_info_ = ExternalCompilationInfo(
        includes=includes
    )
    TIMEVAL = platform.Struct('struct timeval', [('tv_sec', rffi.INT),
                                                 ('tv_usec', rffi.INT)])
    HAVE_GETTIMEOFDAY = platform.Has('gettimeofday')
    HAVE_FTIME = platform.Has(FTIME)
    if need_rusage:
        RUSAGE = platform.Struct('struct rusage', [('ru_utime', TIMEVAL),
                                                   ('ru_stime', TIMEVAL)])

if sys.platform.startswith('freebsd') or sys.platform.startswith('netbsd'):
    libraries = ['compat']
elif sys.platform == 'linux2':
    libraries = ['rt']
else:
    libraries = []

class CConfigForFTime:
    _compilation_info_ = ExternalCompilationInfo(
        includes=[TIME_H, 'sys/timeb.h'],
        libraries=libraries
    )
    TIMEB = platform.Struct(STRUCT_TIMEB, [('time', rffi.INT),
                                           ('millitm', rffi.INT)])

class CConfigForClockGetTime:
    _compilation_info_ = ExternalCompilationInfo(
        includes=['time.h'],
        libraries=libraries
    )
    TIMESPEC = platform.Struct('struct timespec', [('tv_sec', rffi.LONG),
                                                   ('tv_nsec', rffi.LONG)])

constant_names = ['RUSAGE_SELF', 'EINTR', 'CLOCK_PROCESS_CPUTIME_ID']
for const in constant_names:
    setattr(CConfig, const, platform.DefinedConstantInteger(const))
defs_names = ['GETTIMEOFDAY_NO_TZ']
for const in defs_names:
    setattr(CConfig, const, platform.Defined(const))

def decode_timeval(t):
    return (float(rffi.getintfield(t, 'c_tv_sec')) +
            float(rffi.getintfield(t, 'c_tv_usec')) * 0.000001)

class RegisterTime(BaseLazyRegistering):
    def __init__(self):
        self.configure(CConfig)
        self.TIMEVALP = lltype.Ptr(self.TIMEVAL)

    @registering(time.time)
    def register_time_time(self):
        # Note: time.time() is used by the framework GC during collect(),
        # which means that we have to be very careful about not allocating
        # GC memory here.  This is the reason for the _nowrapper=True.

        # AWFUL
        if self.HAVE_GETTIMEOFDAY:
            if self.GETTIMEOFDAY_NO_TZ:
                c_gettimeofday = self.llexternal('gettimeofday',
                                 [self.TIMEVALP], rffi.INT,
                                  _nowrapper=True, releasegil=False)
            else:
                c_gettimeofday = self.llexternal('gettimeofday',
                                 [self.TIMEVALP, rffi.VOIDP], rffi.INT,
                                  _nowrapper=True, releasegil=False)
            c_ftime = None # We have gettimeofday(2), so force ftime(3) OFF.
        else:
            c_gettimeofday = None

            # Only look for ftime(3) if gettimeofday(2) was not found.
            if self.HAVE_FTIME:
                self.configure(CConfigForFTime)
                c_ftime = self.llexternal(FTIME, [lltype.Ptr(self.TIMEB)],
                                          lltype.Void,
                                          _nowrapper=True, releasegil=False)
            else:
                c_ftime = None    # to not confuse the flow space

        c_time = self.llexternal('time', [rffi.VOIDP], rffi.TIME_T,
                                 _nowrapper=True, releasegil=False)

        def time_time_llimpl():
            void = lltype.nullptr(rffi.VOIDP.TO)
            result = -1.0
            if self.HAVE_GETTIMEOFDAY:
                t = lltype.malloc(self.TIMEVAL, flavor='raw')

                errcode = -1
                if self.GETTIMEOFDAY_NO_TZ:
                    errcode = c_gettimeofday(t)
                else:
                    errcode = c_gettimeofday(t, void)

                if rffi.cast(rffi.LONG, errcode) == 0:
                    result = decode_timeval(t)
                lltype.free(t, flavor='raw')
                if result != -1:
                    return result
            else: # assume using ftime(3)
                t = lltype.malloc(self.TIMEB, flavor='raw')
                c_ftime(t)
                result = (float(intmask(t.c_time)) +
                          float(intmask(t.c_millitm)) * 0.001)
                lltype.free(t, flavor='raw')
                return result
            return float(c_time(void))

        return extdef([], float, llimpl=time_time_llimpl,
                      export_name='ll_time.ll_time_time')

    @registering(time.clock)
    def register_time_clock(self):
        if sys.platform == 'win32':
            # hacking to avoid LARGE_INTEGER which is a union...
            A = lltype.FixedSizeArray(lltype.SignedLongLong, 1)
            QueryPerformanceCounter = self.llexternal(
                'QueryPerformanceCounter', [lltype.Ptr(A)], lltype.Void,
                releasegil=False)
            QueryPerformanceFrequency = self.llexternal(
                'QueryPerformanceFrequency', [lltype.Ptr(A)], rffi.INT,
                releasegil=False)
            class State(object):
                pass
            state = State()
            state.divisor = 0.0
            state.counter_start = 0
            def time_clock_llimpl():
                a = lltype.malloc(A, flavor='raw')
                if state.divisor == 0.0:
                    QueryPerformanceCounter(a)
                    state.counter_start = a[0]
                    QueryPerformanceFrequency(a)
                    state.divisor = float(a[0])
                QueryPerformanceCounter(a)
                diff = a[0] - state.counter_start
                lltype.free(a, flavor='raw')
                return float(diff) / state.divisor
        elif self.CLOCK_PROCESS_CPUTIME_ID is not None:
            # Linux and other POSIX systems with clock_gettime()
            self.configure(CConfigForClockGetTime)
            TIMESPEC = self.TIMESPEC
            CLOCK_PROCESS_CPUTIME_ID = self.CLOCK_PROCESS_CPUTIME_ID
            c_clock_gettime = self.llexternal('clock_gettime',
                [lltype.Signed, lltype.Ptr(TIMESPEC)],
                rffi.INT, releasegil=False)
            def time_clock_llimpl():
                a = lltype.malloc(TIMESPEC, flavor='raw')
                c_clock_gettime(CLOCK_PROCESS_CPUTIME_ID, a)
                result = (float(rffi.getintfield(a, 'c_tv_sec')) +
                          float(rffi.getintfield(a, 'c_tv_nsec')) * 0.000000001)
                lltype.free(a, flavor='raw')
                return result
        else:
            RUSAGE = self.RUSAGE
            RUSAGE_SELF = self.RUSAGE_SELF or 0
            c_getrusage = self.llexternal('getrusage', 
                                          [rffi.INT, lltype.Ptr(RUSAGE)],
                                          lltype.Void,
                                          releasegil=False)
            def time_clock_llimpl():
                a = lltype.malloc(RUSAGE, flavor='raw')
                c_getrusage(RUSAGE_SELF, a)
                result = (decode_timeval(a.c_ru_utime) +
                          decode_timeval(a.c_ru_stime))
                lltype.free(a, flavor='raw')
                return result

        return extdef([], float, llimpl=time_clock_llimpl,
                      export_name='ll_time.ll_time_clock')

    @registering(time.sleep)
    def register_time_sleep(self):
        if sys.platform == 'win32':
            Sleep = self.llexternal('Sleep', [rffi.ULONG], lltype.Void)
            def time_sleep_llimpl(secs):
                millisecs = secs * 1000.0
                while millisecs > UINT_MAX:
                    Sleep(UINT_MAX)
                    millisecs -= UINT_MAX
                Sleep(rffi.cast(rffi.ULONG, int(millisecs)))
        else:
            c_select = self.llexternal('select', [rffi.INT, rffi.VOIDP,
                                                  rffi.VOIDP, rffi.VOIDP,
                                                  self.TIMEVALP], rffi.INT,
                                       save_err=rffi.RFFI_SAVE_ERRNO)
            def time_sleep_llimpl(secs):
                void = lltype.nullptr(rffi.VOIDP.TO)
                t = lltype.malloc(self.TIMEVAL, flavor='raw')
                try:
                    frac = math.fmod(secs, 1.0)
                    rffi.setintfield(t, 'c_tv_sec', int(secs))
                    rffi.setintfield(t, 'c_tv_usec', int(frac*1000000.0))

                    if rffi.cast(rffi.LONG, c_select(0, void, void, void, t)) != 0:
                        errno = rposix.get_saved_errno()
                        if errno != EINTR:
                            raise OSError(errno, "Select failed")
                finally:
                    lltype.free(t, flavor='raw')

        return extdef([float], None, llimpl=time_sleep_llimpl,
                      export_name='ll_time.ll_time_sleep')
