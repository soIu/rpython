import sys, py

from rpython.rlib.rfloat import float_as_rbigint_ratio
from rpython.rlib.rfloat import copysign
from rpython.rlib.rfloat import round_away
from rpython.rlib.rfloat import round_double
from rpython.rlib.rfloat import erf, erfc, gamma, lgamma, isnan
from rpython.rlib.rfloat import ulps_check, acc_check
from rpython.rlib.rfloat import string_to_float
from rpython.rlib.rbigint import rbigint

def test_copysign():
    assert copysign(1, 1) == 1
    assert copysign(-1, 1) == 1
    assert copysign(-1, -1) == -1
    assert copysign(1, -1) == -1
    assert copysign(1, -0.) == -1

def test_round_away():
    assert round_away(.1) == 0.
    assert round_away(.5) == 1.
    assert round_away(.7) == 1.
    assert round_away(1.) == 1.
    assert round_away(-.5) == -1.
    assert round_away(-.1) == 0.
    assert round_away(-.7) == -1.
    assert round_away(0.) == 0.

def test_round_double():
    def almost_equal(x, y):
        assert round(abs(x-y), 7) == 0

    almost_equal(round_double(0.125, 2), 0.13)
    almost_equal(round_double(0.375, 2), 0.38)
    almost_equal(round_double(0.625, 2), 0.63)
    almost_equal(round_double(0.875, 2), 0.88)
    almost_equal(round_double(-0.125, 2), -0.13)
    almost_equal(round_double(-0.375, 2), -0.38)
    almost_equal(round_double(-0.625, 2), -0.63)
    almost_equal(round_double(-0.875, 2), -0.88)

    almost_equal(round_double(0.25, 1), 0.3)
    almost_equal(round_double(0.75, 1), 0.8)
    almost_equal(round_double(-0.25, 1), -0.3)
    almost_equal(round_double(-0.75, 1), -0.8)

    round_double(-6.5, 0) == -7.0
    round_double(-5.5, 0) == -6.0
    round_double(-1.5, 0) == -2.0
    round_double(-0.5, 0) == -1.0
    round_double(0.5, 0) == 1.0
    round_double(1.5, 0) == 2.0
    round_double(2.5, 0) == 3.0
    round_double(3.5, 0) == 4.0
    round_double(4.5, 0) == 5.0
    round_double(5.5, 0) == 6.0
    round_double(6.5, 0) == 7.0

    round_double(-25.0, -1) == -30.0
    round_double(-15.0, -1) == -20.0
    round_double(-5.0, -1) == -10.0
    round_double(5.0, -1) == 10.0
    round_double(15.0, -1) == 20.0
    round_double(25.0, -1) == 30.0
    round_double(35.0, -1) == 40.0
    round_double(45.0, -1) == 50.0
    round_double(55.0, -1) == 60.0
    round_double(65.0, -1) == 70.0
    round_double(75.0, -1) == 80.0
    round_double(85.0, -1) == 90.0
    round_double(95.0, -1) == 100.0
    round_double(12325.0, -1) == 12330.0

    round_double(350.0, -2) == 400.0
    round_double(450.0, -2) == 500.0

    almost_equal(round_double(0.5e21, -21), 1e21)
    almost_equal(round_double(1.5e21, -21), 2e21)
    almost_equal(round_double(2.5e21, -21), 3e21)
    almost_equal(round_double(5.5e21, -21), 6e21)
    almost_equal(round_double(8.5e21, -21), 9e21)

    almost_equal(round_double(-1.5e22, -22), -2e22)
    almost_equal(round_double(-0.5e22, -22), -1e22)
    almost_equal(round_double(0.5e22, -22), 1e22)
    almost_equal(round_double(1.5e22, -22), 2e22)

def test_round_half_even():
    from rpython.rlib import rfloat
    func = rfloat.round_double
    # 2.x behavior
    assert func(2.5, 0, False) == 3.0
    # 3.x behavior
    assert func(2.5, 0, True) == 2.0

def test_float_as_rbigint_ratio():
    for f, ratio in [
        (0.875, (7, 8)),
        (-0.875, (-7, 8)),
        (0.0, (0, 1)),
        (11.5, (23, 2)),
        ]:
        num, den = float_as_rbigint_ratio(f)
        assert num.eq(rbigint.fromint(ratio[0]))
        assert den.eq(rbigint.fromint(ratio[1]))

    with py.test.raises(OverflowError):
        float_as_rbigint_ratio(float('inf'))
    with py.test.raises(OverflowError):
        float_as_rbigint_ratio(float('-inf'))
    with py.test.raises(ValueError):
        float_as_rbigint_ratio(float('nan'))

def test_mtestfile():
    from rpython.rlib import rfloat
    import zipfile
    import os
    def _parse_mtestfile(fname):
        """Parse a file with test values

        -- starts a comment
        blank lines, or lines containing only a comment, are ignored
        other lines are expected to have the form
          id fn arg -> expected [flag]*

        """
        with open(fname) as fp:
            for line in fp:
                # strip comments, and skip blank lines
                if '--' in line:
                    line = line[:line.index('--')]
                if not line.strip():
                    continue

                lhs, rhs = line.split('->')
                id, fn, arg = lhs.split()
                rhs_pieces = rhs.split()
                exp = rhs_pieces[0]
                flags = rhs_pieces[1:]

                yield (id, fn, float(arg), float(exp), flags)

    ALLOWED_ERROR = 20  # permitted error, in ulps
    fail_fmt = "{}:{}({!r}): expected {!r}, got {!r}"

    failures = []
    math_testcases = os.path.join(os.path.dirname(__file__),
                                  "math_testcases.txt")
    for id, fn, arg, expected, flags in _parse_mtestfile(math_testcases):
        func = getattr(rfloat, fn)

        if 'invalid' in flags or 'divide-by-zero' in flags:
            expected = 'ValueError'
        elif 'overflow' in flags:
            expected = 'OverflowError'

        try:
            got = func(arg)
        except ValueError:
            got = 'ValueError'
        except OverflowError:
            got = 'OverflowError'

        accuracy_failure = None
        if isinstance(got, float) and isinstance(expected, float):
            if isnan(expected) and isnan(got):
                continue
            if not isnan(expected) and not isnan(got):
                if fn == 'lgamma':
                    # we use a weaker accuracy test for lgamma;
                    # lgamma only achieves an absolute error of
                    # a few multiples of the machine accuracy, in
                    # general.
                    accuracy_failure = acc_check(expected, got,
                                              rel_err = 5e-15,
                                              abs_err = 5e-15)
                elif fn == 'erfc':
                    # erfc has less-than-ideal accuracy for large
                    # arguments (x ~ 25 or so), mainly due to the
                    # error involved in computing exp(-x*x).
                    #
                    # XXX Would be better to weaken this test only
                    # for large x, instead of for all x.
                    accuracy_failure = ulps_check(expected, got, 2000)

                else:
                    accuracy_failure = ulps_check(expected, got, 20)
                if accuracy_failure is None:
                    continue

        if isinstance(got, str) and isinstance(expected, str):
            if got == expected:
                continue

        fail_msg = fail_fmt.format(id, fn, arg, expected, got)
        if accuracy_failure is not None:
            fail_msg += ' ({})'.format(accuracy_failure)
        failures.append(fail_msg)
    assert not failures


def test_gamma_overflow_translated():
    from rpython.translator.c.test.test_genc import compile
    def wrapper(arg):
        try:
            return gamma(arg)
        except OverflowError:
            return -42

    f = compile(wrapper, [float])
    assert f(10.0) == 362880.0
    assert f(1720.0) == -42
    assert f(172.0) == -42



def test_string_to_float():
    from rpython.rlib.rstring import ParseStringError
    import random
    assert string_to_float('0') == 0.0
    assert string_to_float('1') == 1.0
    assert string_to_float('-1.5') == -1.5
    assert string_to_float('1.5E2') == 150.0
    assert string_to_float('2.5E-1') == 0.25
    assert string_to_float('1e1111111111111') == float('1e1111111111111')
    assert string_to_float('1e-1111111111111') == float('1e-1111111111111')
    assert string_to_float('-1e1111111111111') == float('-1e1111111111111')
    assert string_to_float('-1e-1111111111111') == float('-1e-1111111111111')
    assert string_to_float('1e111111111111111111111') == float('1e111111111111111111111')
    assert string_to_float('1e-111111111111111111111') == float('1e-111111111111111111111')
    assert string_to_float('-1e111111111111111111111') == float('-1e111111111111111111111')
    assert string_to_float('-1e-111111111111111111111') == float('-1e-111111111111111111111')

    valid_parts = [['', '  ', ' \f\n\r\t\v'],
                   ['', '+', '-'],
                   ['00', '90', '.5', '2.4', '3.', '0.07',
                    '12.3489749871982471987198371293717398256187563298638726'
                    '2187362820947193247129871083561249818451804287437824015'
                    '013816418758104762348932657836583048761487632840726386'],
                   ['', 'e0', 'E+1', 'E-01', 'E42'],
                   ['', '  ', ' \f\n\r\t\v'],
                   ]
    invalid_parts = [['#'],
                     ['++', '+-', '-+', '--'],
                     ['', '1.2.3', '.', '5..6'],
                     ['E+', 'E-', 'e', 'e++', 'E++2'],
                     ['#'],
                     ]
    for part0 in valid_parts[0]:
        for part1 in valid_parts[1]:
            for part2 in valid_parts[2]:
                for part3 in valid_parts[3]:
                    for part4 in valid_parts[4]:
                        s = part0+part1+part2+part3+part4
                        assert (abs(string_to_float(s) - float(s)) <=
                                1E-13 * abs(float(s)))

    for j in range(len(invalid_parts)):
        for invalid in invalid_parts[j]:
            for i in range(20):
                parts = [random.choice(lst) for lst in valid_parts]
                parts[j] = invalid
                s = ''.join(parts)
                print repr(s)
                if s.strip(): # empty s raises OperationError directly
                    py.test.raises(ParseStringError, string_to_float, s)
    py.test.raises(ParseStringError, string_to_float, "")
