
var LibraryEMJS = {

  $EMJS__deps: [],

  $EMJS: {

    ERROR: 0,
    OK: 1,

    TYPE_ERROR: 0,
    TYPE_UNDEFINED: 1,
    TYPE_BOOLEAN: 2,
    TYPE_NUMBER: 3,
    TYPE_STRING: 4,
    TYPE_OBJECT: 5,
    TYPE_FUNCTION: 6,

    // An array to map integer handles onto JS values.
    // Zero is reserved, and 1-4 are for static singleton values.
    handles: [undefined, undefined, null, false, true],
    free_handles: [],

    UNDEFINED: 1,
    NULL: 2,
    FALSE: 3,
    TRUE: 4,
    MAX_STATIC_HANDLE: 4,

    last_error: undefined

  },

  emjs_make_handle__deps: ['$EMJS'],
  emjs_make_handle: function(value) {
    if (value === undefined) {
      return EMJS.UNDEFINED;
    }
    if (value === null) {
      return EMJS.NULL;
    }
    if (value === false) {
      return EMJS.FALSE;
    }
    if (value === true) {
      return EMJS.TRUE;
    }
    var h;
    if (EMJS.free_handles.length) {
      h = EMJS.free_handles.pop();
    } else {
      h = EMJS.handles.length;
    }
    EMJS.handles[h] = value;
    return h;
  },

  emjs_deref__deps: ['$EMJS'],
  emjs_deref: function(h) {
    h = h|0;
    //if (h > EMJS.MAX_STATIC_HANDLE && !EMJS.handles.hasOwnProperty(h)) {
    //  throw new Error("invalid emjs_handle: " + h);
    //}
    return EMJS.handles[h];
  },

  emjs_free__deps: ['$EMJS'],
  emjs_free: function(h) {
    h = h|0;
    //if (h > EMJS.MAX_STATIC_HANDLE && !EMJS.handles.hasOwnProperty(h)) {
    //  throw new Error("invalid emjs_handle: " + h);
    //}
    if (h > EMJS.MAX_STATIC_HANDLE) {
      EMJS.handles[h] = null;
      EMJS.free_handles.push(h);
    }
  },

  emjs_dup__deps: ['$EMJS', 'emjs_make_handle', 'emjs_deref'],
  emjs_dup: function(h) {
    h = h|0;
    if (h <= EMJS.MAX_STATIC_HANDLE) {
      return h;
    }
    return _emjs_make_handle(_emjs_deref(h));
  },

  emjs_globals__deps: ['emjs_make_handle'],
  emjs_globals: function() {
    return _emjs_make_handle(this);
  },

  emjs_prop_get__deps: ['emjs_make_handle', 'emjs_deref'],
  emjs_prop_get: function(obj_h, prop_h) {
    try {
      var obj = _emjs_deref(obj_h);
      var prop = _emjs_deref(prop_h);
      var res = obj[prop];
      return _emjs_make_handle(res);
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_prop_set__deps: ['emjs_deref'],
  emjs_prop_set: function(obj_h, prop_h, val_h) {
    try {
      var obj = _emjs_deref(obj_h);
      var prop = _emjs_deref(prop_h);
      var val = _emjs_deref(val_h);
      obj[prop] = val;
      return EMJS.OK;
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_prop_delete__deps: ['emjs_deref'],
  emjs_prop_delete: function(obj_h, prop_h) {
    try {
      var obj = _emjs_deref(obj_h);
      var prop = _emjs_deref(prop_h);
      delete obj[prop];
      return EMJS.OK;
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_prop_get_int__deps: ['emjs_make_handle', 'emjs_deref'],
  emjs_prop_get_int: function(obj_h, idx) {
    try {
      var obj = _emjs_deref(obj_h);
      var res = obj[idx];
      return _emjs_make_handle(res);
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_prop_set_int__deps: ['emjs_deref'],
  emjs_prop_set_int: function(obj_h, idx, val_h) {
    try {
      var obj = _emjs_deref(obj_h);
      var val = _emjs_deref(val_h);
      obj[idx] = val;
      return EMJS.OK;
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_prop_delete_int__deps: ['emjs_deref'],
  emjs_prop_delete_int: function(obj_h, idx) {
    try {
      var obj = _emjs_deref(obj_h);
      delete obj[idx];
      return EMJS.OK;
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_prop_get_str__deps: ['emjs_make_handle', 'emjs_deref'],
  emjs_prop_get_str: function(obj_h, nameptr) {
    try {
      var obj = _emjs_deref(obj_h);
      var name = Pointer_stringify(nameptr);
      var res = obj[name];
      return _emjs_make_handle(res);
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_prop_set_str__deps: ['emjs_deref'],
  emjs_prop_set_str: function(obj_h, nameptr, val_h) {
    try {
      var obj = _emjs_deref(obj_h);
      var name = Pointer_stringify(nameptr);
      var val = _emjs_deref(val_h);
      obj[name] = val;
      return EMJS.OK;
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_prop_delete_str__deps: ['emjs_deref'],
  emjs_prop_delete_str: function(obj_h, nameptr) {
    try {
      var obj = _emjs_deref(obj_h);
      var name = Pointer_stringify(nameptr);
      delete obj[name];
      return EMJS.OK;
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_make_str__deps: ['emjs_make_handle'],
  emjs_make_str: function(data) {
    try {
      var value = Pointer_stringify(data);
      return _emjs_make_handle(value);
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_make_strn__deps: ['emjs_make_handle'],
  emjs_make_strn: function(data, length) {
    try {
      var value = Pointer_stringify(data, length);
      return _emjs_make_handle(value);
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_make_int32__deps: ['emjs_make_handle'],
  emjs_make_int32: function(value) {
    return _emjs_make_handle(value|0);
  },

  emjs_make_double__deps: ['emjs_make_handle'],
  emjs_make_double: function(value) {
    return _emjs_make_handle(+value);
  },

  emjs_make_bool__deps: ['emjs_make_handle'],
  emjs_make_bool: function(value) {
    return _emjs_make_handle(!!value);
  },

  emjs_make_undefined__deps: ['emjs_make_handle'],
  emjs_make_undefined: function() {
    return _emjs_make_handle(void 0);
  },

  emjs_make_null__deps: ['emjs_make_handle'],
  emjs_make_null: function() {
    return _emjs_make_handle(null);
  },

  emjs_make_object__deps: ['emjs_make_handle'],
  emjs_make_object: function() {
    return _emjs_make_handle({});
  },

  emjs_make_array__deps: ['emjs_make_handle'],
  emjs_make_array: function(size) {
    try {
      return _emjs_make_handle(new Array(size));
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_make_callback__deps: ['emjs_make_handle', 'emjs_deref', 'emjs_free'],
  emjs_make_callback: function(fnptr, dataptr) {
    return _emjs_make_handle(function() {
      var args = Array.prototype.slice.call(arguments);
      args.this = this;
      var args_h = _emjs_make_handle(args);
      var res_h = Runtime.dynCall("iii", fnptr, [dataptr, args_h]);
      if (res_h == EMJS.ERROR) {
        throw EMJS.last_error;
      }
      var res = _emjs_deref(res_h);
      _emjs_free(args_h);
      _emjs_free(res_h);
      return res
    });
  },

  emjs_get_error__deps: ['emjs_make_handle'],
  emjs_get_error: function() {
    return _emjs_make_handle(EMJS.last_error);
  },

  emjs_set_error__deps: ['emjs_deref'],
  emjs_set_error: function(err_h) {
    if (err_h == EMJS.ERROR) {
      EMJS.last_error = undefined;
    } else {
      EMJS.last_error = _emjs_deref(err_h);
    }
  },

  emjs_clear_error__deps: ['emjs_set_error'],
  emjs_clear_error: function() {
    return _emjs_set_error(EMJS.UNDEFINED)
  },

  emjs_eval__deps: ['emjs_make_handle'],
  emjs_eval: function(expr_ptr) {
    try {
      var expr = Pointer_stringify(expr_ptr);
      // Force eval() to execute at global scope.
      // See http://perfectionkills.com/global-eval-what-are-the-options/
      // for discussion, and extra workaround me might like to add.
      var value = (1,eval)(expr);
      return _emjs_make_handle(value);
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_apply__deps: ['emjs_deref'],
  emjs_apply: function(fn_h, ctx_h, args_h) {
    try {
      var fn = _emjs_deref(fn_h);
      var ctx = _emjs_deref(ctx_h);
      var args = _emjs_deref(args_h);
      var res = fn.apply(ctx, args);
      return _emjs_make_handle(res);
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_new__deps: ['emjs_deref'],
  emjs_new: function(fn_h, args_h) {
    try {
      var fn = _emjs_deref(fn_h);
      var args = _emjs_deref(args_h);
      // Ugh.  How to spread args array into `new` call?
      // Can we simulate `new` by hand using lower-level object API?
      // The below is just *awful*...
      // Brython uses:
      //       var factory = self.js.bind.apply(self.js, args)
      //       var res = new factory()
      var argsrc = ""
      for (var i=0; i<args.length; i++) {
        argsrc += "a" + i + ","
      }
      argsrc += "unused"
      var newsrc = "(function(" + argsrc + ") {";
      newsrc += "return new this(" + argsrc + "); })";
      newfun = eval(newsrc);
      var res = newfun.apply(fn, args);
      return _emjs_make_handle(res);
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_typeof__deps: ['$EMJS', 'emjs_deref'],
  emjs_typeof: function(h) {
    try {
      var obj = _emjs_deref(h);
      var typstr = typeof obj;
      switch (typstr) {
        case "undefined":
          return EMJS.TYPE_UNDEFINED;
        case "boolean":
          return EMJS.TYPE_BOOLEAN;
        case "number":
          return EMJS.TYPE_NUMBER;
        case "string":
          return EMJS.TYPE_STRING;
        case "object":
          return EMJS.TYPE_OBJECT;
        case "function":
          return EMJS.TYPE_FUNCTION;
        default:
          throw new Error("unknown typeof string: " + typstr); 
      }
    } catch (err) { EMJS.last_error = err; return EMJS.TYPE_ERROR; }
  },

  emjs_iter_all__deps: ['emjs_make_handle', 'emjs_deref', 'emjs_free'],
  emjs_iter_all: function(obj_h, fnptr, dataptr) {
    try {
      var obj = _emjs_deref(obj_h);
      for (var prop in obj) {
        var prop_h = _emjs_make_handle(prop);
        Runtime.dynCall("vii", fnptr, [dataptr, prop_h]);
        _emjs_free(prop_h);
      }
      return EMJS.OK
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_iter_own__deps: ['emjs_make_handle', 'emjs_deref', 'emjs_free'],
  emjs_iter_own: function(obj_h, fnptr, dataptr) {
    try {
      var obj = _emjs_deref(obj_h);
      for (var prop in obj) {
        if (!obj.hasOwnProperty(prop)) { continue; }
        var prop_h = _emjs_make_handle(prop);
        Runtime.dynCall("vii", fnptr, [dataptr, prop_h]);
        _emjs_free(prop_h);
      }
      return EMJS.OK
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_check__deps: ['emjs_deref'],
  emjs_check: function(h) {
    var obj = _emjs_deref(h);
    return (!!obj)|0;
  },

  emjs_op_eq__deps: ['emjs_deref'],
  emjs_op_eq: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    return (!!(lhs == rhs))|0;
  },

  emjs_op_neq__deps: ['emjs_deref'],
  emjs_op_neq: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    return (!!(lhs != rhs))|0;
  },

  emjs_op_equiv__deps: ['emjs_deref'],
  emjs_op_equiv: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    return (!!(lhs === rhs))|0;
  },

  emjs_op_nequiv__deps: ['emjs_deref'],
  emjs_op_nequiv: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    return (!!(lhs !== rhs))|0;
  },

  emjs_op_gt__deps: ['emjs_deref'],
  emjs_op_gt: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    return (!!(lhs > rhs))|0;
  },

  emjs_op_lt__deps: ['emjs_deref'],
  emjs_op_lt: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    return (!!(lhs < rhs))|0;
  },

  emjs_op_gteq__deps: ['emjs_deref'],
  emjs_op_gteq: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    return (!!(lhs >= rhs))|0;
  },

  emjs_op_lteq__deps: ['emjs_deref'],
  emjs_op_lteq: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    return (!!(lhs <= rhs))|0;
  },

  emjs_op_add__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_add: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs + rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_sub__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_sub: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs - rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_mul__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_mul: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs * rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_div__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_div: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs / rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_mod__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_mod: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs % rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_uplus__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_uplus: function(h) {
    var obj = _emjs_deref(h);
    var res = +obj;
    return _emjs_make_handle(res);
  },

  emjs_op_uminus__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_uminus: function(h) {
    var obj = _emjs_deref(h);
    var res = -obj;
    return _emjs_make_handle(res);
  },

  emjs_op_bw_and__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_bw_and: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs & rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_bw_or__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_bw_or: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs | rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_bw_xor__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_bw_xor: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs ^ rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_bw_lshift__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_bw_lshift: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs << rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_bw_rshift__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_bw_rshift: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs >> rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_bw_urshift__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_bw_urshift: function(lhs_h, rhs_h) {
    var lhs = _emjs_deref(lhs_h);
    var rhs = _emjs_deref(rhs_h);
    var res = lhs >>> rhs;
    return _emjs_make_handle(res);
  },

  emjs_op_bw_neg__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_bw_neg: function(h) {
    var obj = _emjs_deref(h);
    var res = ~obj;
    return _emjs_make_handle(res);
  },

  emjs_op_in__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_in: function(lhs_h, rhs_h) {
    try {
      var lhs = _emjs_deref(lhs_h);
      var rhs = _emjs_deref(rhs_h);
      return lhs in rhs;
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_op_instanceof__deps: ['emjs_deref', 'emjs_make_handle'],
  emjs_op_instanceof: function(lhs_h, rhs_h) {
    try {
      var lhs = _emjs_deref(lhs_h);
      var rhs = _emjs_deref(rhs_h);
      return lhs instanceof rhs;
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_length__deps: ['emjs_deref'],
  emjs_length: function(h) {
    try {
      var obj = _emjs_deref(h);
      return obj.length|0;
    } catch (err) { EMJS.last_error = err; return EMJS.ERROR; }
  },

  emjs_to_int32__deps: ['emjs_deref'],
  emjs_to_int32: function(h) {
    var obj = _emjs_deref(h);
    return _emjs_make_handle(obj|0);
  },

  emjs_to_uint32__deps: ['emjs_deref'],
  emjs_to_uint32: function(h) {
    var obj = _emjs_deref(h);
    return _emjs_make_handle(obj >>> 0);
  },

  emjs_to_double__deps: ['emjs_deref'],
  emjs_to_double: function(h) {
    var obj = _emjs_deref(h);
    return _emjs_make_handle(+obj);
  },

  emjs_to_string__deps: ['emjs_deref'],
  emjs_to_string: function(h) {
    var obj = _emjs_deref(h);
    return _emjs_make_handle("" + obj);
  },

  emjs_read_int32__deps: ['emjs_deref'],
  emjs_read_int32: function(h) {
    var obj = _emjs_deref(h);
    return obj|0;
  },

  emjs_read_uint32__deps: ['emjs_deref'],
  emjs_read_uint32: function(h) {
    var obj = _emjs_deref(h);
    return obj >>> 0;
  },

  emjs_read_double__deps: ['emjs_deref'],
  emjs_read_double: function(h) {
    var obj = _emjs_deref(h);
    return +obj;
  },

  emjs_read_str__deps: ['emjs_deref'],
  emjs_read_str: function(h, bufptr) {
    var string = "" + _emjs_deref(h);
    // note: this adds a trailing null to the string.
    var array = intArrayFromString(string, false);
    var i = 0;
    while (i < array.length) {
      var chr = array[i];
      {{{ makeSetValue('bufptr', 'i', 'chr', 'i8') }}};
      i = i + 1;
    }
    return i;
  },

  emjs_read_strn__deps: ['emjs_deref'],
  emjs_read_strn: function(h, bufptr, maxlen) {
    var string = "" + _emjs_deref(h);
    // note: no trailing null added to the string.
    var array = intArrayFromString(string, true);
    var i = 0;
    while (i < array.length && i < maxlen) {
      var chr = array[i];
      {{{ makeSetValue('bufptr', 'i', 'chr', 'i8') }}};
      i = i + 1;
    }
    return i;
  }

};

mergeInto(LibraryManager.library, LibraryEMJS);

