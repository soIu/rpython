import gc, time
from rpython.rlib.rthread import *
from rpython.rlib.rarithmetic import r_longlong
from rpython.translator.c.test.test_boehm import AbstractGCTestClass
from rpython.rtyper.lltypesystem import lltype, rffi
import py

def setup_module(mod):
    # Hack to avoid a deadlock if the module is run after other test files :-(
    # In this module, we assume that rthread.start_new_thread() is not
    # providing us with a GIL equivalent, except in test_gc_locking
    # which installs its own aroundstate.
    rffi.aroundstate._cleanup_()

def test_lock():
    l = allocate_lock()
    ok1 = l.acquire(True)
    ok2 = l.acquire(False)
    l.release()
    ok3 = l.acquire(False)
    res = ok1 and not ok2 and ok3
    assert res == 1

def test_thread_error():
    l = allocate_lock()
    try:
        l.release()
    except error:
        pass
    else:
        py.test.fail("Did not raise")

def test_tlref_untranslated():
    class FooBar(object):
        pass
    t = ThreadLocalReference(FooBar)
    results = []
    def subthread():
        x = FooBar()
        results.append(t.get() is None)
        t.set(x)
        results.append(t.get() is x)
        time.sleep(0.2)
        results.append(t.get() is x)
    for i in range(5):
        start_new_thread(subthread, ())
    time.sleep(0.5)
    assert results == [True] * 15

def test_get_ident():
    import thread
    assert get_ident() == thread.get_ident()


def test_threadlocalref_on_llinterp():
    from rpython.rtyper.test.test_llinterp import interpret
    tlfield = ThreadLocalField(lltype.Signed, "rthread_test_")
    #
    def f():
        x = tlfield.setraw(42)
        return tlfield.getraw()
    #
    res = interpret(f, [])
    assert res == 42


class AbstractThreadTests(AbstractGCTestClass):
    use_threads = True

    def test_start_new_thread(self):
        import time

        class State:
            pass
        state = State()

        def bootstrap1():
            state.my_thread_ident1 = get_ident()
        def bootstrap2():
            state.my_thread_ident2 = get_ident()

        def f():
            state.my_thread_ident1 = get_ident()
            state.my_thread_ident2 = get_ident()
            start_new_thread(bootstrap1, ())
            start_new_thread(bootstrap2, ())
            willing_to_wait_more = 1000
            while (state.my_thread_ident1 == get_ident() or
                   state.my_thread_ident2 == get_ident()):
                willing_to_wait_more -= 1
                if not willing_to_wait_more:
                    raise Exception("thread didn't start?")
                time.sleep(0.01)
            return 42

        fn = self.getcompiled(f, [])
        res = fn()
        assert res == 42

    def test_gc_locking(self):
        import time
        from rpython.rlib.objectmodel import invoke_around_extcall
        from rpython.rlib.debug import ll_assert

        class State:
            pass
        state = State()

        class Z:
            def __init__(self, i, j):
                self.i = i
                self.j = j
            def run(self):
                j = self.j
                if self.i > 1:
                    g(self.i-1, self.j * 2)
                    ll_assert(j == self.j, "1: bad j")
                    g(self.i-2, self.j * 2 + 1)
                else:
                    if len(state.answers) % 7 == 5:
                        gc.collect()
                    state.answers.append(self.j)
                ll_assert(j == self.j, "2: bad j")
            run._dont_inline_ = True

        def before_extcall():
            release_NOAUTO(state.gil)
        before_extcall._gctransformer_hint_cannot_collect_ = True
        # ^^^ see comments in gil.py about this hint

        def after_extcall():
            acquire_NOAUTO(state.gil, True)
            gc_thread_run()
        after_extcall._gctransformer_hint_cannot_collect_ = True
        # ^^^ see comments in gil.py about this hint

        def bootstrap():
            # after_extcall() is called before we arrive here.
            # We can't just acquire and release the GIL manually here,
            # because it is unsafe: bootstrap() is called from a rffi
            # callback which checks for and reports exceptions after
            # bootstrap() returns.  The exception checking code must be
            # protected by the GIL too.
            z = state.z
            state.z = None
            state.bootstrapping.release()
            z.run()
            gc_thread_die()
            # before_extcall() is called after we leave here

        def g(i, j):
            state.bootstrapping.acquire(True)
            state.z = Z(i, j)
            start_new_thread(bootstrap, ())

        def f():
            state.gil = allocate_ll_lock()
            acquire_NOAUTO(state.gil, True)
            state.bootstrapping = allocate_lock()
            state.answers = []
            state.finished = 0
            # the next line installs before_extcall() and after_extcall()
            # to be called automatically around external function calls.
            invoke_around_extcall(before_extcall, after_extcall)

            g(10, 1)
            done = False
            willing_to_wait_more = 2000
            while not done:
                if not willing_to_wait_more:
                    break
                willing_to_wait_more -= 1
                done = len(state.answers) == expected

                time.sleep(0.01)

            time.sleep(0.1)

            return len(state.answers)

        expected = 89
        try:
            fn = self.getcompiled(f, [])
        finally:
            rffi.aroundstate._cleanup_()
        answers = fn()
        assert answers == expected

    def test_acquire_timed(self):
        import time
        def f():
            l = allocate_lock()
            l.acquire(True)
            t1 = time.time()
            ok = l.acquire_timed(1000001)
            t2 = time.time()
            delay = t2 - t1
            if ok == 0:        # RPY_LOCK_FAILURE
                return -delay
            elif ok == 2:      # RPY_LOCK_INTR
                return delay
            else:              # RPY_LOCK_ACQUIRED
                return 0.0
        fn = self.getcompiled(f, [])
        res = fn()
        assert res < -1.0

    def test_acquire_timed_huge_timeout(self):
        t = r_longlong(2 ** 61)
        def f():
            l = allocate_lock()
            return l.acquire_timed(t)
        fn = self.getcompiled(f, [])
        res = fn()
        assert res == 1       # RPY_LOCK_ACQUIRED

    def test_acquire_timed_alarm(self):
        import sys
        if not sys.platform.startswith('linux'):
            py.test.skip("skipped on non-linux")
        import time
        from rpython.rlib import rsignal
        def f():
            l = allocate_lock()
            l.acquire(True)
            #
            rsignal.pypysig_setflag(rsignal.SIGALRM)
            rsignal.c_alarm(1)
            #
            t1 = time.time()
            ok = l.acquire_timed(2500000)
            t2 = time.time()
            delay = t2 - t1
            if ok == 0:        # RPY_LOCK_FAILURE
                return -delay
            elif ok == 2:      # RPY_LOCK_INTR
                return delay
            else:              # RPY_LOCK_ACQUIRED
                return 0.0
        fn = self.getcompiled(f, [])
        res = fn()
        assert res >= 0.95

    def test_tlref(self):
        class FooBar(object):
            pass
        t = ThreadLocalReference(FooBar)
        def f():
            x1 = FooBar()
            t.set(x1)
            import gc; gc.collect()
            assert t.get() is x1
            return 42
        fn = self.getcompiled(f, [])
        res = fn()
        assert res == 42

#class TestRunDirectly(AbstractThreadTests):
#    def getcompiled(self, f, argtypes):
#        return f
# These are disabled because they crash occasionally for bad reasons
# related to the fact that ll2ctypes is not at all thread-safe

class TestUsingBoehm(AbstractThreadTests):
    gcpolicy = 'boehm'

class TestUsingFramework(AbstractThreadTests):
    gcpolicy = 'minimark'
