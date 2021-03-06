
function ffi_type_allocator(size, alignment, type) {
  return "allocate([" + size + ",0,0,0," + alignment + ",0," + type + ",0], 'i8', ALLOC_STATIC)"
}

var LibraryFFI = {

  ffi_type_void: ffi_type_allocator(0, 0, 0),
  ffi_type_uint8: ffi_type_allocator(1, 1, 5),
  ffi_type_sint8: ffi_type_allocator(1, 1, 6),
  ffi_type_uint16: ffi_type_allocator(2, 2, 7),
  ffi_type_sint16: ffi_type_allocator(2, 2, 8),
  ffi_type_uint32: ffi_type_allocator(4, 4, 9),
  ffi_type_sint32: ffi_type_allocator(4, 4, 10),
  ffi_type_uint64: ffi_type_allocator(0, 0, 11),
  ffi_type_sint64: ffi_type_allocator(0, 0, 12),
  ffi_type_float: ffi_type_allocator(4, 4, 2),
  ffi_type_double: ffi_type_allocator(8, 8, 3),
  ffi_type_longdouble: ffi_type_allocator(0, 0, 4),
  ffi_type_pointer: ffi_type_allocator(4, 4, 14),

  $FFI__deps: ['ffi_type_void', 'ffi_type_uint8', 'ffi_type_sint8',
               'ffi_type_uint16', 'ffi_type_sint16', 'ffi_type_uint32',
               'ffi_type_sint32', 'ffi_type_uint64', 'ffi_type_sint64',
               'ffi_type_float', 'ffi_type_double', 'ffi_type_longdouble',
               'ffi_type_pointer'],
  $FFI: {

    TYPE_VOID: 0,
    TYPE_INT: 1,
    TYPE_FLOAT: 2,
    TYPE_DOUBLE: 3,
    TYPE_LONGDOUBLE: 4,
    TYPE_UINT8: 5,
    TYPE_SINT8: 6,
    TYPE_UINT16: 7,
    TYPE_SINT16: 8,
    TYPE_UINT32: 9,
    TYPE_SINT32: 10,
    TYPE_UINT64: 11,
    TYPE_SINT64: 12,
    TYPE_STRUCT: 13,
    TYPE_POINTER: 14,

    SYSV: 1,

    OK: 0,
    BAD_TYPEDEF: 1,
    BAD_ABI: 2
    
  },

  ffi_prep_cif__deps: ['$FFI'],
  ffi_prep_cif: function(cifptr, abi, nargs, rtype, argtypes) {
    throw new Error("libffi not implemented yet");
//    if (!cifptr) { return FFI.BAD_TYPEDEF; }
//    if (abi !== FFI.SYSV ) { return FFI.BAD_ABI; }
//    {{ makeSetValue('cifptr', '0', 'abi', 'i32') }};
//    {{ makeSetValue('cifptr', '4', 'nargs', 'i32') }};
//    {{ makeSetValue('cifptr', '8', 'argtypes', 'i32') }};
//    {{ makeSetValue('cifptr', '12', 'rtype', 'i32') }};
//    return FFI.OK;
  },

  ffi_call__deps: ['$FFI'],
  ffi_call: function(cifptr, fnptr, rvalptr, argvalsptr) {
    throw new Error("libffi not implemented yet");
//    var abi = {{ makeGetValue('cifptr', '0', 'i32') }};
//    var nargs = {{ makeGetValue('cifptr', '4', 'i32') }};
//    var argtyes = {{ makeGetValue('cifptr', '8', 'i32') }};
//    var rtype = {{ makeGetValue('cifptr', '12', 'i32') }};
//    var args = [];
    // iter the argtypes, load them into the args array
    // and build up the dyncall type signature string.
    //
    // load and call the appropriate dyncall function,
    // capturing the result
    //
    // write the result into the provided pointer
    // (and ensure correct alignment for its size if < sizeof ffi_arg)
  }

};

mergeInto(LibraryManager.library, LibraryFFI);

