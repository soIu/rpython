/*

  This is a stubbed-out version of the standard 'debug_traceback.h' file,
  which makes the traceback-collecting macros expand to empty.  This means
  that rpython-level failures cannot report nice tracebacks, but removes a
  lot of never-actually-used-in-production code from the build.

*/

#define PYPY_DEBUG_TRACEBACK_DEPTH       1

#define PYPYDTPOS_RERAISE                 ((struct pypydtpos_s *) -1)

#define PYPYDTSTORE(loc, etype)

#define OP_DEBUG_START_TRACEBACK(etype, _)
#define OP_DEBUG_RERAISE_TRACEBACK(etp, _)
#define OP_DEBUG_PRINT_TRACEBACK()

#define PYPY_DEBUG_RECORD_TRACEBACK(funcname)   {       \
  }
#define PYPY_DEBUG_CATCH_EXCEPTION(funcname, etype, is_fatal)   {       \
  }

struct pypydtpos_s {
  const char *filename;
  const char *funcname;
  int lineno;
};

struct pypydtentry_s {
  struct pypydtpos_s *location;
  void *exctype;
};

extern int pypydtcount;
extern struct pypydtentry_s pypy_debug_tracebacks[PYPY_DEBUG_TRACEBACK_DEPTH];

void pypy_debug_traceback_print(void);
void pypy_debug_catch_fatal_exception(void);
