/*
 *  Minimal libffi compatability header for emscripten.
 *  Modified from the i386 libffi header; original copyright notice below.
 *
 */

/* -----------------------------------------------------------------*-C-*-
   libffi 3.0.13 - Copyright (c) 2011 Anthony Green
                    - Copyright (c) 1996-2003, 2007, 2008 Red Hat, Inc.

   Permission is hereby granted, free of charge, to any person
   obtaining a copy of this software and associated documentation
   files (the ``Software''), to deal in the Software without
   restriction, including without limitation the rights to use, copy,
   modify, merge, publish, distribute, sublicense, and/or sell copies
   of the Software, and to permit persons to whom the Software is
   furnished to do so, subject to the following conditions:

   The above copyright notice and this permission notice shall be
   included in all copies or substantial portions of the Software.

   THE SOFTWARE IS PROVIDED ``AS IS'', WITHOUT WARRANTY OF ANY KIND,
   EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
   MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
   NONINFRINGEMENT.  IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
   HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
   WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
   DEALINGS IN THE SOFTWARE.

   ----------------------------------------------------------------------- */

#ifndef LIBFFI_H
#define LIBFFI_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stddef.h>
#include <limits.h>

typedef unsigned long ffi_arg;
typedef signed long ffi_sarg;

typedef enum ffi_abi {
  FFI_FIRST_ABI = 0,
  FFI_SYSV,
  FFI_LAST_ABI,
  FFI_DEFAULT_ABI = FFI_SYSV
} ffi_abi;

typedef struct _ffi_type
{
  size_t size;
  unsigned short alignment;
  unsigned short type;
  struct _ffi_type **elements;
} ffi_type;

#define ffi_type_uchar ffi_type_uint8
#define ffi_type_schar ffi_type_sint8
#define ffi_type_ushort ffi_type_uint16
#define ffi_type_sshort ffi_type_sint16
#define ffi_type_uint ffi_type_uint32
#define ffi_type_sint ffi_type_sint32
#define ffi_type_ulong ffi_type_uint32
#define ffi_type_slong ffi_type_sint32

#define FFI_TYPE_VOID       0    
#define FFI_TYPE_INT        1
#define FFI_TYPE_FLOAT      2    
#define FFI_TYPE_DOUBLE     3
#define FFI_TYPE_LONGDOUBLE 4
#define FFI_TYPE_UINT8      5   
#define FFI_TYPE_SINT8      6
#define FFI_TYPE_UINT16     7 
#define FFI_TYPE_SINT16     8
#define FFI_TYPE_UINT32     9
#define FFI_TYPE_SINT32     10
#define FFI_TYPE_UINT64     11
#define FFI_TYPE_SINT64     12
#define FFI_TYPE_STRUCT     13
#define FFI_TYPE_POINTER    14
#define FFI_TYPE_LAST       FFI_TYPE_POINTER

extern ffi_type ffi_type_void;
extern ffi_type ffi_type_uint8;
extern ffi_type ffi_type_sint8;
extern ffi_type ffi_type_uint16;
extern ffi_type ffi_type_sint16;
extern ffi_type ffi_type_uint32;
extern ffi_type ffi_type_sint32;
extern ffi_type ffi_type_uint64;
extern ffi_type ffi_type_sint64;
extern ffi_type ffi_type_float;
extern ffi_type ffi_type_double;
extern ffi_type ffi_type_longdouble;
extern ffi_type ffi_type_pointer;

typedef enum {
  FFI_OK = 0,
  FFI_BAD_TYPEDEF = 1,
  FFI_BAD_ABI = 2
} ffi_status;

typedef unsigned FFI_TYPE;

typedef struct {
  ffi_abi abi;
  unsigned nargs;
  ffi_type **arg_types;
  ffi_type *rtype;
} ffi_cif;


ffi_status ffi_prep_cif(ffi_cif *cif,
			ffi_abi abi,
			unsigned int nargs,
			ffi_type *rtype,
			ffi_type **atypes);


void ffi_call(ffi_cif *cif,
	      void (*fn)(void),
	      void *rvalue,
	      void **avalue);


typedef struct {
  void *trampoline_table;
  void *trampoline_table_entry;
  ffi_cif   *cif;
  void     (*fun)(ffi_cif*,void*,void**,void*);
  void      *user_data;
} ffi_closure;


void *ffi_closure_alloc (size_t size, void **code);
void ffi_closure_free (void *);

ffi_status
ffi_prep_closure (ffi_closure*,
                  ffi_cif *,
                  void (*fun)(ffi_cif*,void*,void**,void*),
                  void *user_data);

ffi_status
ffi_prep_closure_loc (ffi_closure*,
                      ffi_cif *,
                      void (*fun)(ffi_cif*,void*,void**,void*),
                      void *user_data,
                      void*codeloc);

#ifdef __cplusplus
}
#endif

#endif
