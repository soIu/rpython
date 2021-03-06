/************************************************************/
/***  C header subsection: operations between ints        ***/


/* Note for win64:

   'Signed' must be defined as

       __int64          in case of win64
       long             in all other cases

   'SIGNED_MIN' must be defined as

       LLONG_MIN        in case of win64
       LONG_MIN         in all other cases
 */

/*** unary operations ***/

#define OP_INT_IS_TRUE(x,r)   r = ((x) != 0)
#define OP_INT_INVERT(x,r)    r = ~(x)
#define OP_INT_NEG(x,r)       r = -(x)

#define OP_INT_NEG_OVF(x,r) \
	if ((x) == SIGNED_MIN) FAIL_OVF("integer negate"); \
	OP_INT_NEG(x,r)

#define OP_INT_ABS(x,r)    r = (x) >= 0 ? x : -(x)

#define OP_INT_ABS_OVF(x,r) \
	if ((x) == SIGNED_MIN) FAIL_OVF("integer absolute"); \
	OP_INT_ABS(x,r)

/***  binary operations ***/

#define OP_INT_EQ(x,y,r)	  r = ((x) == (y))
#define OP_INT_NE(x,y,r)	  r = ((x) != (y))
#define OP_INT_LE(x,y,r)	  r = ((x) <= (y))
#define OP_INT_GT(x,y,r)	  r = ((x) >  (y))
#define OP_INT_LT(x,y,r)	  r = ((x) <  (y))
#define OP_INT_GE(x,y,r)	  r = ((x) >= (y))

/* Implement INT_BETWEEN by optimizing for the common case where a and c
   are constants (the 2nd subtraction below is then constant-folded), or
   for the case of a == 0 (both subtractions are then constant-folded).
   Note that the following line only works if a <= c in the first place,
   which we assume is true. */
#define OP_INT_BETWEEN(a,b,c,r)   r = (((Unsigned)b - (Unsigned)a) \
                                     < ((Unsigned)c - (Unsigned)a))

#define OP_INT_FORCE_GE_ZERO(a,r)   r = (0 > a) ? 0 : (a)

/* addition, subtraction */

#define OP_INT_ADD(x,y,r)     r = (x) + (y)

/* cast to avoid undefined behaviour on overflow */
#define OP_INT_ADD_OVF(x,y,r) \
        r = (Signed)((Unsigned)x + y); \
        if ((r^x) < 0 && (r^y) < 0) FAIL_OVF("integer addition")

#define OP_INT_ADD_NONNEG_OVF(x,y,r)  /* y can be assumed >= 0 */ \
        r = (Signed)((Unsigned)x + y); \
        if ((r&~x) < 0) FAIL_OVF("integer addition")

#define OP_INT_SUB(x,y,r)     r = (x) - (y)

#define OP_INT_SUB_OVF(x,y,r) \
        r = (Signed)((Unsigned)x - y); \
        if ((r^x) < 0 && (r^~y) < 0) FAIL_OVF("integer subtraction")

#define OP_INT_MUL(x,y,r)     r = (x) * (y)

#if SIZEOF_LONG * 2 <= SIZEOF_LONG_LONG && !defined(_WIN64)
#define OP_INT_MUL_OVF(x,y,r) \
	{ \
		long long _lr = (long long)x * y; \
		r = (long)_lr; \
		if (_lr != (long long)r) FAIL_OVF("integer multiplication"); \
	}
#else
#define OP_INT_MUL_OVF(x,y,r) \
	r = op_llong_mul_ovf(x, y)   /* long == long long */
#endif

/* shifting */

/* NB. shifting has same limitations as C: the shift count must be
       >= 0 and < LONG_BITS. */
#define CHECK_SHIFT_RANGE(y, bits) RPyAssert(y >= 0 && y < bits, \
	       "The shift count is outside of the supported range")


#define OP_INT_RSHIFT(x,y,r)    CHECK_SHIFT_RANGE(y, PYPY_LONG_BIT); \
						r = Py_ARITHMETIC_RIGHT_SHIFT(Signed, x, (y))
#define OP_UINT_RSHIFT(x,y,r)   CHECK_SHIFT_RANGE(y, PYPY_LONG_BIT); \
						r = (x) >> (y)
#define OP_LLONG_RSHIFT(x,y,r)  CHECK_SHIFT_RANGE(y, PYPY_LONGLONG_BIT); \
						r = Py_ARITHMETIC_RIGHT_SHIFT(PY_LONG_LONG,x, (y))
#define OP_ULLONG_RSHIFT(x,y,r) CHECK_SHIFT_RANGE(y, PYPY_LONGLONG_BIT); \
						r = (x) >> (y)
#define OP_LLLONG_RSHIFT(x,y,r)  r = x >> y

#define OP_INT_LSHIFT(x,y,r)    CHECK_SHIFT_RANGE(y, PYPY_LONG_BIT); \
							r = (x) << (y)
#define OP_UINT_LSHIFT(x,y,r)   CHECK_SHIFT_RANGE(y, PYPY_LONG_BIT); \
							r = (x) << (y)
#define OP_LLONG_LSHIFT(x,y,r)  CHECK_SHIFT_RANGE(y, PYPY_LONGLONG_BIT); \
							r = (x) << (y)
#define OP_LLLONG_LSHIFT(x,y,r)  r = x << y
#define OP_ULLONG_LSHIFT(x,y,r) CHECK_SHIFT_RANGE(y, PYPY_LONGLONG_BIT); \
							r = (x) << (y)

#define OP_INT_LSHIFT_OVF(x,y,r) \
	OP_INT_LSHIFT(x,y,r); \
	if ((x) != Py_ARITHMETIC_RIGHT_SHIFT(Signed, r, (y))) \
		FAIL_OVF("x<<y losing bits or changing sign")

/* floor division */

#define OP_INT_FLOORDIV(x,y,r)    r = (x) / (y)
#define OP_UINT_FLOORDIV(x,y,r)   r = (x) / (y)
#define OP_LLONG_FLOORDIV(x,y,r)  r = (x) / (y)
#define OP_ULLONG_FLOORDIV(x,y,r) r = (x) / (y)
#define OP_LLLONG_FLOORDIV(x,y,r)  r = (x) / (y)

#define OP_INT_FLOORDIV_OVF(x,y,r)                      \
	if ((y) == -1 && (x) == SIGNED_MIN)               \
	    { FAIL_OVF("integer division"); r=0; }      \
	else                                            \
	    r = (x) / (y)

#define OP_INT_FLOORDIV_ZER(x,y,r)                      \
	if ((y) == 0)                                   \
	    { FAIL_ZER("integer division"); r=0; }      \
	else                                            \
	    r = (x) / (y)
#define OP_UINT_FLOORDIV_ZER(x,y,r)                             \
	if ((y) == 0)                                           \
	    { FAIL_ZER("unsigned integer division"); r=0; }     \
	else                                                    \
	    r = (x) / (y)
#define OP_LLONG_FLOORDIV_ZER(x,y,r)                    \
	if ((y) == 0)                                   \
	    { FAIL_ZER("integer division"); r=0; }      \
	else                                            \
	    r = (x) / (y)

#define OP_ULLONG_FLOORDIV_ZER(x,y,r)                           \
	if ((y) == 0)                                           \
	    { FAIL_ZER("unsigned integer division"); r=0; }     \
	else                                                    \
	    r = (x) / (y)
	    
#define OP_LLLONG_FLOORDIV_ZER(x,y,r)                    \
        if ((y) == 0)                                   \
            { FAIL_ZER("integer division"); r=0; }      \
        else                                            \
            r = (x) / (y)
            
#define OP_INT_FLOORDIV_OVF_ZER(x,y,r)                  \
	if ((y) == 0)                                   \
	    { FAIL_ZER("integer division"); r=0; }      \
	else                                            \
	    { OP_INT_FLOORDIV_OVF(x,y,r); }

/* modulus */

#define OP_INT_MOD(x,y,r)     r = (x) % (y)
#define OP_UINT_MOD(x,y,r)    r = (x) % (y)
#define OP_LLONG_MOD(x,y,r)   r = (x) % (y)
#define OP_ULLONG_MOD(x,y,r)  r = (x) % (y)
#define OP_LLLONG_MOD(x,y,r)   r = (x) % (y)

#define OP_INT_MOD_OVF(x,y,r)                           \
	if ((y) == -1 && (x) == SIGNED_MIN)               \
	    { FAIL_OVF("integer modulo"); r=0; }        \
	else                                            \
	    r = (x) % (y)
#define OP_INT_MOD_ZER(x,y,r)                           \
	if ((y) == 0)                                   \
	    { FAIL_ZER("integer modulo"); r=0; }        \
	else                                            \
	    r = (x) % (y)
#define OP_UINT_MOD_ZER(x,y,r)                                  \
	if ((y) == 0)                                           \
	    { FAIL_ZER("unsigned integer modulo"); r=0; }       \
	else                                                    \
	    r = (x) % (y)
#define OP_LLONG_MOD_ZER(x,y,r)                         \
	if ((y) == 0)                                   \
	    { FAIL_ZER("integer modulo"); r=0; }        \
	else                                            \
	    r = (x) % (y)
#define OP_ULLONG_MOD_ZER(x,y,r)                                \
	if ((y) == 0)                                           \
	    { FAIL_ZER("unsigned integer modulo"); r=0; }       \
	else                                                    \
	    r = (x) % (y)

#define OP_LLLONG_MOD_ZER(x,y,r)                         \
        if ((y) == 0)                                   \
            { FAIL_ZER("integer modulo"); r=0; }        \
        else                                            \
            r = (x) % (y)
            
#define OP_INT_MOD_OVF_ZER(x,y,r)                       \
	if ((y) == 0)                                   \
	    { FAIL_ZER("integer modulo"); r=0; }        \
	else                                            \
	    { OP_INT_MOD_OVF(x,y,r); }

/* bit operations */

#define OP_INT_AND(x,y,r)     r = (x) & (y)
#define OP_INT_OR( x,y,r)     r = (x) | (y)
#define OP_INT_XOR(x,y,r)     r = (x) ^ (y)

/*** conversions ***/

#define OP_CAST_BOOL_TO_INT(x,r)    r = (Signed)(x)
#define OP_CAST_BOOL_TO_UINT(x,r)   r = (Unsigned)(x)
#define OP_CAST_UINT_TO_INT(x,r)    r = (Signed)(x)
#define OP_CAST_INT_TO_UINT(x,r)    r = (Unsigned)(x)
#define OP_CAST_INT_TO_LONGLONG(x,r) r = (long long)(x)
#define OP_CAST_INT_TO_LONGLONGLONG(x,r) r = (__int128)(x)
#define OP_CAST_CHAR_TO_INT(x,r)    r = (Signed)((unsigned char)(x))
#define OP_CAST_INT_TO_CHAR(x,r)    r = (char)(x)
#define OP_CAST_PTR_TO_INT(x,r)     r = (Signed)(x)    /* XXX */

#define OP_TRUNCATE_LONGLONG_TO_INT(x,r) r = (Signed)(x)
#define OP_TRUNCATE_LONGLONGLONG_TO_INT(x,r) r = (Signed)(x)

#define OP_CAST_UNICHAR_TO_INT(x,r)    r = (Signed)((Unsigned)(x)) /*?*/
#define OP_CAST_INT_TO_UNICHAR(x,r)    r = (unsigned int)(x)

/* bool operations */

#define OP_BOOL_NOT(x, r) r = !(x)

#ifdef __GNUC__
#  define OP_LIKELY(x, r)    r = __builtin_expect((x), 1)
#  define OP_UNLIKELY(x, r)  r = __builtin_expect((x), 0)
#else
#  define OP_LIKELY(x, r)    r = (x)
#  define OP_UNLIKELY(x, r)  r = (x)
#endif

RPY_EXTERN long long op_llong_mul_ovf(long long a, long long b);

/* The definitions above can be used with various types */ 

#define OP_UINT_IS_TRUE OP_INT_IS_TRUE
#define OP_UINT_INVERT OP_INT_INVERT
#define OP_UINT_ADD OP_INT_ADD
#define OP_UINT_SUB OP_INT_SUB
#define OP_UINT_MUL OP_INT_MUL
#define OP_UINT_LT OP_INT_LT
#define OP_UINT_LE OP_INT_LE
#define OP_UINT_EQ OP_INT_EQ
#define OP_UINT_NE OP_INT_NE
#define OP_UINT_GT OP_INT_GT
#define OP_UINT_GE OP_INT_GE
#define OP_UINT_AND OP_INT_AND
#define OP_UINT_OR OP_INT_OR
#define OP_UINT_XOR OP_INT_XOR

#define OP_LLONG_IS_TRUE OP_INT_IS_TRUE
#define OP_LLONG_NEG     OP_INT_NEG
#define OP_LLONG_ABS     OP_INT_ABS
#define OP_LLONG_INVERT  OP_INT_INVERT

#define OP_LLLONG_IS_TRUE OP_INT_IS_TRUE
#define OP_LLLONG_NEG     OP_INT_NEG
#define OP_LLLONG_ABS     OP_INT_ABS
#define OP_LLLONG_INVERT  OP_INT_INVERT

#define OP_LLONG_ADD OP_INT_ADD
#define OP_LLONG_SUB OP_INT_SUB
#define OP_LLONG_MUL OP_INT_MUL
#define OP_LLONG_LT  OP_INT_LT
#define OP_LLONG_LE  OP_INT_LE
#define OP_LLONG_EQ  OP_INT_EQ
#define OP_LLONG_NE  OP_INT_NE
#define OP_LLONG_GT  OP_INT_GT
#define OP_LLONG_GE  OP_INT_GE
#define OP_LLONG_AND    OP_INT_AND
#define OP_LLONG_OR     OP_INT_OR
#define OP_LLONG_XOR    OP_INT_XOR

#define OP_LLLONG_ADD OP_INT_ADD
#define OP_LLLONG_SUB OP_INT_SUB
#define OP_LLLONG_MUL OP_INT_MUL
#define OP_LLLONG_LT  OP_INT_LT
#define OP_LLLONG_LE  OP_INT_LE
#define OP_LLLONG_EQ  OP_INT_EQ
#define OP_LLLONG_NE  OP_INT_NE
#define OP_LLLONG_GT  OP_INT_GT
#define OP_LLLONG_GE  OP_INT_GE
#define OP_LLLONG_AND    OP_INT_AND
#define OP_LLLONG_OR     OP_INT_OR
#define OP_LLLONG_XOR    OP_INT_XOR

#define OP_ULLONG_IS_TRUE OP_LLONG_IS_TRUE
#define OP_ULLONG_INVERT  OP_LLONG_INVERT
#define OP_ULLONG_ADD OP_LLONG_ADD
#define OP_ULLONG_SUB OP_LLONG_SUB
#define OP_ULLONG_MUL OP_LLONG_MUL
#define OP_ULLONG_LT OP_LLONG_LT
#define OP_ULLONG_LE OP_LLONG_LE
#define OP_ULLONG_EQ OP_LLONG_EQ
#define OP_ULLONG_NE OP_LLONG_NE
#define OP_ULLONG_GT OP_LLONG_GT
#define OP_ULLONG_GE OP_LLONG_GE
#define OP_ULLONG_AND OP_LLONG_AND
#define OP_ULLONG_OR OP_LLONG_OR
#define OP_ULLONG_XOR OP_LLONG_XOR
