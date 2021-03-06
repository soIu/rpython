import py

import sys, os

from rpython.rtyper.lltypesystem.module.ll_os_path import Implementation as impl
from rpython.rtyper.test.test_llinterp import interpret
from rpython.tool.udir import udir


def test_exists():
    filename = impl.to_rstr(str(py.path.local(__file__)))
    assert impl.ll_os_path_exists(filename) == True
    assert not impl.ll_os_path_exists(impl.to_rstr(
        "strange_filename_that_looks_improbable.sde"))

def test_posixpath():
    import posixpath
    def f():
        assert posixpath.join("/foo", "bar") == "/foo/bar"
        assert posixpath.join("/foo", "spam/egg") == "/foo/spam/egg"
        assert posixpath.join("/foo", "/bar") == "/bar"
    interpret(f, [])

def test_ntpath():
    import ntpath
    def f():
        assert ntpath.join("\\foo", "bar") == "\\foo\\bar"
        assert ntpath.join("c:\\foo", "spam\\egg") == "c:\\foo\\spam\\egg"
        assert ntpath.join("c:\\foo", "d:\\bar") == "d:\\bar"
    interpret(f, [])

def test_isdir():
    if sys.platform != 'win32':
        py.test.skip("XXX cannot run os.stat() on the llinterp yet")

    s = str(udir.join('test_isdir'))
    def f():
        return os.path.isdir(s)
    res = interpret(f, [])
    assert res == os.path.isdir(s)
    os.mkdir(s)
    res = interpret(f, [])
    assert res is True

    # On Windows, the libc stat() is flawed:
    #     stat('c:/temp')  works
    # but stat('c:/temp/') does not find the directory...
    # This test passes with our own stat() implementation.
    s += os.path.sep
    def f():
        return os.path.isdir(s)
    res = interpret(f, [])
    assert res is True
