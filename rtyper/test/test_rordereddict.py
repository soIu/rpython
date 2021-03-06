
import py
try:
    from collections import OrderedDict
except ImportError:     # Python 2.6
    py.test.skip("requires collections.OrderedDict")
from rpython.rtyper.lltypesystem import lltype, rffi
from rpython.rtyper.lltypesystem import rordereddict, rstr
from rpython.rlib.rarithmetic import intmask
from rpython.rtyper.annlowlevel import llstr, hlstr
from rpython.rtyper.test.test_rdict import BaseTestRDict
from rpython.rlib import objectmodel


def get_indexes(ll_d):
    return ll_d.indexes._obj.container._as_ptr()

def foreach_index(ll_d):
    indexes = get_indexes(ll_d)
    for i in range(len(indexes)):
        yield rffi.cast(lltype.Signed, indexes[i])

def count_items(ll_d, ITEM):
    c = 0
    for item in foreach_index(ll_d):
        if item == ITEM:
            c += 1
    return c


class TestRDictDirect(object):
    dummykeyobj = None
    dummyvalueobj = None

    def _get_str_dict(self):
        # STR -> lltype.Signed
        DICT = rordereddict.get_ll_dict(lltype.Ptr(rstr.STR), lltype.Signed,
                                 ll_fasthash_function=rstr.LLHelpers.ll_strhash,
                                 ll_hash_function=rstr.LLHelpers.ll_strhash,
                                 ll_eq_function=rstr.LLHelpers.ll_streq,
                                 dummykeyobj=self.dummykeyobj,
                                 dummyvalueobj=self.dummyvalueobj)
        return DICT

    def test_dict_creation(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        lls = llstr("abc")
        rordereddict.ll_dict_setitem(ll_d, lls, 13)
        assert count_items(ll_d, rordereddict.FREE) == rordereddict.DICT_INITSIZE - 1
        assert rordereddict.ll_dict_getitem(ll_d, llstr("abc")) == 13
        assert rordereddict.ll_dict_getitem(ll_d, lls) == 13
        rordereddict.ll_dict_setitem(ll_d, lls, 42)
        assert rordereddict.ll_dict_getitem(ll_d, lls) == 42
        rordereddict.ll_dict_setitem(ll_d, llstr("abc"), 43)
        assert rordereddict.ll_dict_getitem(ll_d, lls) == 43

    def test_dict_creation_2(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        llab = llstr("ab")
        llb = llstr("b")
        rordereddict.ll_dict_setitem(ll_d, llab, 1)
        rordereddict.ll_dict_setitem(ll_d, llb, 2)
        assert rordereddict.ll_dict_getitem(ll_d, llb) == 2

    def test_dict_store_get(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        for i in range(20):
            for j in range(i):
                assert rordereddict.ll_dict_getitem(ll_d, llstr(str(j))) == j
            rordereddict.ll_dict_setitem(ll_d, llstr(str(i)), i)
        assert ll_d.num_live_items == 20
        for i in range(20):
            assert rordereddict.ll_dict_getitem(ll_d, llstr(str(i))) == i

    def test_dict_store_get_del(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        for i in range(20):
            for j in range(0, i, 2):
                assert rordereddict.ll_dict_getitem(ll_d, llstr(str(j))) == j
            rordereddict.ll_dict_setitem(ll_d, llstr(str(i)), i)
            if i % 2 != 0:
                rordereddict.ll_dict_delitem(ll_d, llstr(str(i)))
        assert ll_d.num_live_items == 10
        for i in range(0, 20, 2):
            assert rordereddict.ll_dict_getitem(ll_d, llstr(str(i))) == i

    def test_dict_del_lastitem(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        py.test.raises(KeyError, rordereddict.ll_dict_delitem, ll_d, llstr("abc"))
        rordereddict.ll_dict_setitem(ll_d, llstr("abc"), 13)
        py.test.raises(KeyError, rordereddict.ll_dict_delitem, ll_d, llstr("def"))
        rordereddict.ll_dict_delitem(ll_d, llstr("abc"))
        assert count_items(ll_d, rordereddict.FREE) == rordereddict.DICT_INITSIZE - 1
        assert count_items(ll_d, rordereddict.DELETED) == 1
        py.test.raises(KeyError, rordereddict.ll_dict_getitem, ll_d, llstr("abc"))

    def test_dict_del_not_lastitem(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("abc"), 13)
        rordereddict.ll_dict_setitem(ll_d, llstr("def"), 15)
        rordereddict.ll_dict_delitem(ll_d, llstr("abc"))
        assert count_items(ll_d, rordereddict.FREE) == rordereddict.DICT_INITSIZE - 2
        assert count_items(ll_d, rordereddict.DELETED) == 1

    def test_dict_resize(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("a"), 1)
        rordereddict.ll_dict_setitem(ll_d, llstr("b"), 2)
        rordereddict.ll_dict_setitem(ll_d, llstr("c"), 3)
        rordereddict.ll_dict_setitem(ll_d, llstr("d"), 4)
        rordereddict.ll_dict_setitem(ll_d, llstr("e"), 5)
        rordereddict.ll_dict_setitem(ll_d, llstr("f"), 6)
        rordereddict.ll_dict_setitem(ll_d, llstr("g"), 7)
        rordereddict.ll_dict_setitem(ll_d, llstr("h"), 8)
        rordereddict.ll_dict_setitem(ll_d, llstr("i"), 9)
        rordereddict.ll_dict_setitem(ll_d, llstr("j"), 10)
        assert len(get_indexes(ll_d)) == 16
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 11)
        rordereddict.ll_dict_setitem(ll_d, llstr("l"), 12)
        rordereddict.ll_dict_setitem(ll_d, llstr("m"), 13)
        assert len(get_indexes(ll_d)) == 64
        for item in 'abcdefghijklm':
            assert rordereddict.ll_dict_getitem(ll_d, llstr(item)) == ord(item) - ord('a') + 1

    def test_dict_grow_cleanup(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        lls = llstr("a")
        for i in range(40):
            rordereddict.ll_dict_setitem(ll_d, lls, i)
            rordereddict.ll_dict_delitem(ll_d, lls)
        assert ll_d.num_ever_used_items <= 10

    def test_dict_iteration(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 1)
        rordereddict.ll_dict_setitem(ll_d, llstr("j"), 2)
        ITER = rordereddict.get_ll_dictiter(lltype.Ptr(DICT))
        ll_iter = rordereddict.ll_dictiter(ITER, ll_d)
        ll_dictnext = rordereddict._ll_dictnext
        num = ll_dictnext(ll_iter)
        assert hlstr(ll_d.entries[num].key) == "k"
        num = ll_dictnext(ll_iter)
        assert hlstr(ll_d.entries[num].key) == "j"
        py.test.raises(StopIteration, ll_dictnext, ll_iter)

    def test_popitem(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 1)
        rordereddict.ll_dict_setitem(ll_d, llstr("j"), 2)
        TUP = lltype.Ptr(lltype.GcStruct('x', ('item0', lltype.Ptr(rstr.STR)),
                                              ('item1', lltype.Signed)))
        ll_elem = rordereddict.ll_dict_popitem(TUP, ll_d)
        assert hlstr(ll_elem.item0) == "j"
        assert ll_elem.item1 == 2
        ll_elem = rordereddict.ll_dict_popitem(TUP, ll_d)
        assert hlstr(ll_elem.item0) == "k"
        assert ll_elem.item1 == 1
        py.test.raises(KeyError, rordereddict.ll_dict_popitem, TUP, ll_d)

    def test_popitem_first(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 1)
        rordereddict.ll_dict_setitem(ll_d, llstr("j"), 2)
        rordereddict.ll_dict_setitem(ll_d, llstr("m"), 3)
        ITER = rordereddict.get_ll_dictiter(lltype.Ptr(DICT))
        for expected in ["k", "j", "m"]:
            ll_iter = rordereddict.ll_dictiter(ITER, ll_d)
            num = rordereddict._ll_dictnext(ll_iter)
            ll_key = ll_d.entries[num].key
            assert hlstr(ll_key) == expected
            rordereddict.ll_dict_delitem(ll_d, ll_key)
        ll_iter = rordereddict.ll_dictiter(ITER, ll_d)
        py.test.raises(StopIteration, rordereddict._ll_dictnext, ll_iter)

    def test_popitem_first_bug(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 1)
        rordereddict.ll_dict_setitem(ll_d, llstr("j"), 1)
        rordereddict.ll_dict_delitem(ll_d, llstr("k"))
        ITER = rordereddict.get_ll_dictiter(lltype.Ptr(DICT))
        ll_iter = rordereddict.ll_dictiter(ITER, ll_d)
        num = rordereddict._ll_dictnext(ll_iter)
        ll_key = ll_d.entries[num].key
        assert hlstr(ll_key) == "j"
        assert ll_d.lookup_function_no == 4    # 1 free item found at the start
        rordereddict.ll_dict_delitem(ll_d, llstr("j"))
        assert ll_d.num_ever_used_items == 0
        assert ll_d.lookup_function_no == 0    # reset

    def test_direct_enter_and_del(self):
        def eq(a, b):
            return a == b

        DICT = rordereddict.get_ll_dict(lltype.Signed, lltype.Signed,
                                 ll_fasthash_function=intmask,
                                 ll_hash_function=intmask,
                                 ll_eq_function=eq)
        ll_d = rordereddict.ll_newdict(DICT)
        numbers = [i * rordereddict.DICT_INITSIZE + 1 for i in range(8)]
        for num in numbers:
            rordereddict.ll_dict_setitem(ll_d, num, 1)
            rordereddict.ll_dict_delitem(ll_d, num)
            for k in foreach_index(ll_d):
                assert k < rordereddict.VALID_OFFSET

    def test_contains(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 1)
        assert rordereddict.ll_dict_contains(ll_d, llstr("k"))
        assert not rordereddict.ll_dict_contains(ll_d, llstr("j"))

    def test_clear(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 1)
        rordereddict.ll_dict_setitem(ll_d, llstr("j"), 1)
        rordereddict.ll_dict_setitem(ll_d, llstr("l"), 1)
        rordereddict.ll_dict_clear(ll_d)
        assert ll_d.num_live_items == 0

    def test_get(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 1)
        assert rordereddict.ll_dict_get(ll_d, llstr("k"), 32) == 1
        assert rordereddict.ll_dict_get(ll_d, llstr("j"), 32) == 32

    def test_setdefault(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 1)
        assert rordereddict.ll_dict_setdefault(ll_d, llstr("j"), 42) == 42
        assert rordereddict.ll_dict_getitem(ll_d, llstr("j")) == 42
        assert rordereddict.ll_dict_setdefault(ll_d, llstr("k"), 42) == 1
        assert rordereddict.ll_dict_getitem(ll_d, llstr("k")) == 1

    def test_copy(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 1)
        rordereddict.ll_dict_setitem(ll_d, llstr("j"), 2)
        ll_d2 = rordereddict.ll_dict_copy(ll_d)
        for ll_d3 in [ll_d, ll_d2]:
            assert rordereddict.ll_dict_getitem(ll_d3, llstr("k")) == 1
            assert rordereddict.ll_dict_get(ll_d3, llstr("j"), 42) == 2
            assert rordereddict.ll_dict_get(ll_d3, llstr("i"), 42) == 42

    def test_update(self):
        DICT = self._get_str_dict()
        ll_d1 = rordereddict.ll_newdict(DICT)
        ll_d2 = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d1, llstr("k"), 5)
        rordereddict.ll_dict_setitem(ll_d1, llstr("j"), 6)
        rordereddict.ll_dict_setitem(ll_d2, llstr("i"), 7)
        rordereddict.ll_dict_setitem(ll_d2, llstr("k"), 8)
        rordereddict.ll_dict_update(ll_d1, ll_d2)
        for key, value in [("k", 8), ("i", 7), ("j", 6)]:
            assert rordereddict.ll_dict_getitem(ll_d1, llstr(key)) == value

    def test_pop(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 5)
        rordereddict.ll_dict_setitem(ll_d, llstr("j"), 6)
        assert rordereddict.ll_dict_pop(ll_d, llstr("k")) == 5
        assert rordereddict.ll_dict_pop(ll_d, llstr("j")) == 6
        py.test.raises(KeyError, rordereddict.ll_dict_pop, ll_d, llstr("k"))
        py.test.raises(KeyError, rordereddict.ll_dict_pop, ll_d, llstr("j"))

    def test_pop_default(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        rordereddict.ll_dict_setitem(ll_d, llstr("k"), 5)
        rordereddict.ll_dict_setitem(ll_d, llstr("j"), 6)
        assert rordereddict.ll_dict_pop_default(ll_d, llstr("k"), 42) == 5
        assert rordereddict.ll_dict_pop_default(ll_d, llstr("j"), 41) == 6
        assert rordereddict.ll_dict_pop_default(ll_d, llstr("k"), 40) == 40
        assert rordereddict.ll_dict_pop_default(ll_d, llstr("j"), 39) == 39

    def test_bug_remove_deleted_items(self):
        DICT = self._get_str_dict()
        ll_d = rordereddict.ll_newdict(DICT)
        for i in range(15):
            rordereddict.ll_dict_setitem(ll_d, llstr(chr(i)), 5)
        for i in range(15):
            rordereddict.ll_dict_delitem(ll_d, llstr(chr(i)))
        rordereddict.ll_prepare_dict_update(ll_d, 7)
        # used to get UninitializedMemoryAccess

class TestRDictDirectDummyKey(TestRDictDirect):
    class dummykeyobj:
        ll_dummy_value = llstr("dupa")

class TestRDictDirectDummyValue(TestRDictDirect):
    class dummyvalueobj:
        ll_dummy_value = -42

class TestOrderedRDict(BaseTestRDict):
    @staticmethod
    def newdict():
        return OrderedDict()

    @staticmethod
    def newdict2():
        return OrderedDict()

    @staticmethod
    def new_r_dict(myeq, myhash):
        return objectmodel.r_ordereddict(myeq, myhash)

    def test_two_dicts_with_different_value_types(self):
        def func(i):
            d1 = OrderedDict()
            d1['hello'] = i + 1
            d2 = OrderedDict()
            d2['world'] = d1
            return d2['world']['hello']
        res = self.interpret(func, [5])
        assert res == 6


class TestStress:

    def test_stress(self):
        from rpython.annotator.dictdef import DictKey, DictValue
        from rpython.annotator import model as annmodel
        from rpython.rtyper import rint
        from rpython.rtyper.test.test_rdict import not_really_random
        rodct = rordereddict
        dictrepr = rodct.OrderedDictRepr(
                                  None, rint.signed_repr, rint.signed_repr,
                                  DictKey(None, annmodel.SomeInteger()),
                                  DictValue(None, annmodel.SomeInteger()))
        dictrepr.setup()
        l_dict = rodct.ll_newdict(dictrepr.DICT)
        referencetable = [None] * 400
        referencelength = 0
        value = 0

        def complete_check():
            for n, refvalue in zip(range(len(referencetable)), referencetable):
                try:
                    gotvalue = rodct.ll_dict_getitem(l_dict, n)
                except KeyError:
                    assert refvalue is None
                else:
                    assert gotvalue == refvalue

        for x in not_really_random():
            n = int(x*100.0)    # 0 <= x < 400
            op = repr(x)[-1]
            if op <= '2' and referencetable[n] is not None:
                rodct.ll_dict_delitem(l_dict, n)
                referencetable[n] = None
                referencelength -= 1
            elif op <= '6':
                rodct.ll_dict_setitem(l_dict, n, value)
                if referencetable[n] is None:
                    referencelength += 1
                referencetable[n] = value
                value += 1
            else:
                try:
                    gotvalue = rodct.ll_dict_getitem(l_dict, n)
                except KeyError:
                    assert referencetable[n] is None
                else:
                    assert gotvalue == referencetable[n]
            if 1.38 <= x <= 1.39:
                complete_check()
                print 'current dict length:', referencelength
            assert l_dict.num_live_items == referencelength
        complete_check()

    def test_stress_2(self):
        yield self.stress_combination, True,  False
        yield self.stress_combination, False, True
        yield self.stress_combination, False, False
        yield self.stress_combination, True,  True

    def stress_combination(self, key_can_be_none, value_can_be_none):
        from rpython.rtyper.lltypesystem.rstr import string_repr
        from rpython.annotator.dictdef import DictKey, DictValue
        from rpython.annotator import model as annmodel
        from rpython.rtyper.test.test_rdict import not_really_random
        rodct = rordereddict

        print
        print "Testing combination with can_be_None: keys %s, values %s" % (
            key_can_be_none, value_can_be_none)

        class PseudoRTyper:
            cache_dummy_values = {}
        dictrepr = rodct.OrderedDictRepr(
                       PseudoRTyper(), string_repr, string_repr,
                       DictKey(None, annmodel.SomeString(key_can_be_none)),
                       DictValue(None, annmodel.SomeString(value_can_be_none)))
        dictrepr.setup()
        print dictrepr.lowleveltype
        #for key, value in dictrepr.DICTENTRY._adtmeths.items():
        #    print '    %s = %s' % (key, value)
        l_dict = rodct.ll_newdict(dictrepr.DICT)
        referencetable = [None] * 400
        referencelength = 0
        values = not_really_random()
        keytable = [string_repr.convert_const("foo%d" % n)
                    for n in range(len(referencetable))]

        def complete_check():
            for n, refvalue in zip(range(len(referencetable)), referencetable):
                try:
                    gotvalue = rodct.ll_dict_getitem(l_dict, keytable[n])
                except KeyError:
                    assert refvalue is None
                else:
                    assert gotvalue == refvalue

        for x in not_really_random():
            n = int(x*100.0)    # 0 <= x < 400
            op = repr(x)[-1]
            if op <= '2' and referencetable[n] is not None:
                rodct.ll_dict_delitem(l_dict, keytable[n])
                referencetable[n] = None
                referencelength -= 1
            elif op <= '6':
                ll_value = string_repr.convert_const(str(values.next()))
                rodct.ll_dict_setitem(l_dict, keytable[n], ll_value)
                if referencetable[n] is None:
                    referencelength += 1
                referencetable[n] = ll_value
            else:
                try:
                    gotvalue = rodct.ll_dict_getitem(l_dict, keytable[n])
                except KeyError:
                    assert referencetable[n] is None
                else:
                    assert gotvalue == referencetable[n]
            if 1.38 <= x <= 1.39:
                complete_check()
                print 'current dict length:', referencelength
            assert l_dict.num_live_items == referencelength
        complete_check()
