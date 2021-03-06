/*

  Abstract C API for accessing the host javascript environment.

  This file defines a C-level API for introspecting and interacting with
  the host javascript environment.  JS objects are represented to C code
  via opaque integer handles, which can be passed to functions that implement
  the primitive js operations.

  Zero is never a valid handle, and is used to indicate error conditions.
  If a call into this API returns zero, the caller should check for an
  exception by calling emjs_get_error() and, after handling it as needed, call
  emjs_set_error() to clear it.

*/

#include <stdlib.h>

#ifndef EMJS_H
#define EMJS_H

// Host JS values are represented to C code via an opaque integer "handle".
typedef int emjs_handle;

// Zero is a special value used to indicate that an exception occurred.
#define EMJS_ERROR 0
#define EMJS_OK 1

// The various JS value types are mapped to integer identifiers.
// Note that, like javascript itself, 'null' has type EMJS_TYPE_OBJECT.
typedef enum {
  EMJS_TYPE_ERROR = 0,
  EMJS_TYPE_UNDEFINED = 1,
  EMJS_TYPE_BOOLEAN = 2,
  EMJS_TYPE_NUMBER = 3,
  EMJS_TYPE_STRING = 4,
  EMJS_TYPE_OBJECT = 5,
  EMJS_TYPE_FUNCTION = 6
} emjs_type;

// Static singleton JS values have corresponding static handles.
// These can be used directly wherever an `emjs_handle` is required,
// and no other handle value will map to these primitives.
#define EMJS_UNDEFINED ((emjs_handle) 1)
#define EMJS_NULL ((emjs_handle) 2)
#define EMJS_FALSE ((emjs_handle) 3)
#define EMJS_TRUE ((emjs_handle) 4)

#define EMJS_MAX_STATIC_HANDLE ((emjs_handle) 4)

// Each handle causes its referred-to JS value to be kept alive,
// so they should be explicitly freed when no longer required.
// Freeing a static handle is safe and has no effect.
void emjs_free(emjs_handle);

// Duplicate a handle, giving another reference to its referred-to JS value.
// There may thus be multiple handles poiting to the same JS value, each of
// which is sufficient to keep it alive.  Duplicating a static handle is
// safe and returned it unmodified.
emjs_handle emjs_dup(emjs_handle);

// Get a refernce to the global scope 'this' object.
emjs_handle emjs_globals(void);

// Load/store/delete/call a JS value as a property on an object.
// The generic case accepts a handle to specify the the property, and
// there are special-case helpers for string and integer properties.
emjs_handle emjs_prop_get(emjs_handle obj, emjs_handle prop);
emjs_handle emjs_prop_set(emjs_handle obj, emjs_handle prop, emjs_handle val);
emjs_handle emjs_prop_delete(emjs_handle obj, emjs_handle prop);
emjs_handle emjs_prop_apply(emjs_handle obj, emjs_handle prop, emjs_handle args);

emjs_handle emjs_prop_get_int(emjs_handle obj, int idx);
emjs_handle emjs_prop_set_int(emjs_handle obj, int idx, emjs_handle val);
emjs_handle emjs_prop_delete_int(emjs_handle obj, int idx);
emjs_handle emjs_prop_apply_int(emjs_handle obj, int idx, emjs_handle args);

emjs_handle emjs_prop_get_str(emjs_handle obj, char* name);
emjs_handle emjs_prop_set_str(emjs_handle obj, char* name, emjs_handle val);
emjs_handle emjs_prop_delete_str(emjs_handle obj, char* name);
emjs_handle emjs_prop_apply_str(emjs_handle obj, char* name, emjs_handle args);

// Helpers to create primitive JS values of each type.
// It is not necessary to use these to create static singleton values like
// `null` or `true`, but may be convenient in some cases.
emjs_handle emjs_make_str(const char*);
emjs_handle emjs_make_strn(const char*, size_t);
emjs_handle emjs_make_int32(int);
emjs_handle emjs_make_double(double);
emjs_handle emjs_make_bool(int);
emjs_handle emjs_make_undefined(void);
emjs_handle emjs_make_null(void);
emjs_handle emjs_make_object(void);
emjs_handle emjs_make_array(int size);

// Create a JS callback that will execute a C function.
//
// The target function must accept a void* user-data pointer and a single
// emjs_handle for the arguments array, and must return an emjs_handle result.
// It may throw an error by calling emjs_set_error() and returning zero. 
// The argument and result handles will be freed after the callback completes;
// use emjs_dup to maintain references to them.
emjs_handle emjs_make_callback(emjs_handle(*fun)(void*,emjs_handle), void*);

// Deal with exceptions using an errno-like approach.
// If there was an exception then emjs_get_error() will return a handle
// to it; if there was no exception then it will return EMJS_UNDEFINED.
emjs_handle emjs_get_error(void);
void emjs_set_error(emjs_handle err);
void emjs_clear_error(void);

// Evaluate literal js code in global scope.
emjs_handle emjs_eval(const char*);

// Call JS value as a function, via fn.apply(ctx, args).
emjs_handle emjs_apply(emjs_handle fn, emjs_handle ctx, emjs_handle args);

// Call JS value as a constructor.
emjs_handle emjs_new(emjs_handle fn, emjs_handle args);

// Introspect the type of a JS value.
// Note that `typeof null` is EMJS_TYPE_OBJECT, just like it is in JS.
emjs_type emjs_typeof(emjs_handle);

// Iterate the properties of a JS value.
//
// These functions execute a callback with each property of the given
// object.  The callback should return zero to continue the loop and
// non-zero to break.  The handle to the property value will be freed at
// the end of the callback; use emjs_dup to maintain a reference to it.
//  
// XXX TODO: It would be nice to use generators to do a pull-based approach
// here, with an API closer to C++-style iterators...
emjs_handle emjs_iter_all(emjs_handle obj, void (*fun)(void*,emjs_handle), void*);
emjs_handle emjs_iter_own(emjs_handle obj, void (*fun)(void*,emjs_handle), void*);

// Check the truthinesss of a JS value.
int emjs_check(emjs_handle value);

// There's a function for each available JS operator.
// Note that there are no assignment-like operators here (e.g. `x++`)
// because there's no notion of JS-level variables in this API.

// The test operators return C-level truthiness value rather than a handle,
// so that it's easier to branch based on them.
int emjs_op_eq(emjs_handle lhs, emjs_handle rhs);
int emjs_op_neq(emjs_handle lhs, emjs_handle rhs);
int emjs_op_equiv(emjs_handle lhs, emjs_handle rhs);
int emjs_op_nequiv(emjs_handle lhs, emjs_handle rhs);
int emjs_op_gt(emjs_handle lhs, emjs_handle rhs);
int emjs_op_lt(emjs_handle lhs, emjs_handle rhs);
int emjs_op_gteq(emjs_handle lhs, emjs_handle rhs);
int emjs_op_lteq(emjs_handle lhs, emjs_handle rhs);

// Note that these can throw if you give bad types, e.g. `2 in 1`.
// The return value will be zero in this case, which can only be distinguished
// from a result of 'false' by checking emjs_get_error() == EMJS_UNDEFINED;
int emjs_op_in(emjs_handle lhs, emjs_handle rhs);
int emjs_op_instanceof(emjs_handle lhs, emjs_handle rhs);

// Arithmetic operators, which return a fresh handle to the result.
emjs_handle emjs_op_add(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_sub(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_mul(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_div(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_mod(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_uplus(emjs_handle value);
emjs_handle emjs_op_uminus(emjs_handle value);

// Bitwise operators, which return a fresh handle to the result.
emjs_handle emjs_op_bw_and(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_bw_or(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_bw_xor(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_bw_lshift(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_bw_rshift(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_bw_urshift(emjs_handle lhs, emjs_handle rhs);
emjs_handle emjs_op_bw_neg(emjs_handle value);

// Get the 'length' property as a C-level integer.
// This is a convenience for e.g. allocating buffers for a string.
// Note that it can throw if you give bad types, e.g. `undefined.length`.
// The return value will be zero in this case, which can only be distinguished
// from a result of 'false' by checking emjs_get_error() == EMJS_UNDEFINED;
int emjs_length(emjs_handle);

// Functions to convert JS values into various primitive JS types.
// These are essentially equivalent to the asmjs coercion operations.
emjs_handle emjs_to_int32(emjs_handle);
emjs_handle emjs_to_uint32(emjs_handle);
emjs_handle emjs_to_double(emjs_handle);
emjs_handle emjs_to_string(emjs_handle);

// Functions to translate primitive JS values back into primitive C values.
int emjs_read_int32(emjs_handle);
unsigned int emjs_read_uint32(emjs_handle);
double emjs_read_double(emjs_handle);
int emjs_read_str(emjs_handle, char* buffer);
int emjs_read_strn(emjs_handle, char* buffer, int maxlen);

#endif
