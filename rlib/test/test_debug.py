
import py
from rpython.rlib.debug import (check_annotation, make_sure_not_resized,
                             debug_print, debug_start, debug_stop,
                             have_debug_prints, debug_offset, debug_flush,
                             check_nonneg, IntegerCanBeNegative,
                             mark_dict_non_null,
                             check_list_of_chars,
                             NotAListOfChars)
from rpython.rlib import debug
from rpython.rtyper.test.test_llinterp import interpret, gengraph

def test_check_annotation():
    class Error(Exception):
        pass

    def checker(ann, bk):
        from rpython.annotator.model import SomeList, SomeInteger
        if not isinstance(ann, SomeList):
            raise Error()
        if not isinstance(ann.listdef.listitem.s_value, SomeInteger):
            raise Error()

    def f(x):
        result = [x]
        check_annotation(result, checker)
        return result

    interpret(f, [3])

    def g(x):
        check_annotation(x, checker)
        return x

    py.test.raises(Error, "interpret(g, [3])")

def test_check_nonneg():
    def f(x):
        assert x >= 5
        check_nonneg(x)
    interpret(f, [9])

    def g(x):
        check_nonneg(x-1)
    py.test.raises(IntegerCanBeNegative, interpret, g, [9])

def test_make_sure_not_resized():
    from rpython.annotator.listdef import ListChangeUnallowed
    def f():
        result = [1,2,3]
        make_sure_not_resized(result)
        result.append(4)
        return len(result)

    py.test.raises(ListChangeUnallowed, interpret, f, [],
                   list_comprehension_operations=True)

def test_make_sure_not_resized_annorder():
    def f(n):
        if n > 5:
            result = None
        else:
            result = [1,2,3]
        make_sure_not_resized(result)
    interpret(f, [10])

def test_mark_dict_non_null():
    def f():
        d = {"ac": "bx"}
        mark_dict_non_null(d)
        return d

    t, typer, graph = gengraph(f, [])
    assert sorted(graph.returnblock.inputargs[0].concretetype.TO.entries.TO.OF._flds.keys()) == ['key', 'value']


def test_check_list_of_chars():
    def f(x):
        result = []
        check_list_of_chars(result)
        result = [chr(x), 'a']
        check_list_of_chars(result)
        result = [unichr(x)]
        check_list_of_chars(result)
        return result

    interpret(f, [3])

    def g(x):
        result = ['a', 'b', 'c', '']
        check_list_of_chars(result)
        return x

    py.test.raises(NotAListOfChars, "interpret(g, [3])")


class DebugTests(object):

    def test_debug_print_start_stop(self):
        def f(x):
            debug_start("mycat")
            debug_print("foo", 2, "bar", x)
            debug_stop("mycat")
            debug_flush() # does nothing
            debug_offset() # should not explode at least
            return have_debug_prints()

        try:
            debug._log = dlog = debug.DebugLog()
            res = f(3)
            assert res == True
        finally:
            debug._log = None
        assert dlog == [
            ("mycat", [
                ('debug_print', 'foo', 2, 'bar', 3),
                ]),
            ]

        try:
            debug._log = dlog = debug.DebugLog()
            res = self.interpret(f, [3])
            assert res == True
        finally:
            debug._log = None
        assert dlog == [
            ("mycat", [
                ('debug_print', 'foo', 2, 'bar', 3),
                ]),
            ]

    def interpret(self, f, args):
        return interpret(f, args, type_system='lltype')
