import sys, gc
import cStringIO
import traceback

# Track allocations to detect memory leaks.
# So far, this is used for lltype.malloc(flavor='raw').
TRACK_ALLOCATIONS = False
ALLOCATED = {}

class MallocMismatch(Exception):
    def __str__(self):
        dict = self.args[0]
        dict2 = {}
        for obj, traceback in dict.items():
            traceback = traceback.splitlines()
            if len(traceback) > 8:
                traceback = ['    ...'] + traceback[-6:]
            traceback = '\n'.join(traceback)
            dict2.setdefault(traceback, [])
            dict2[traceback].append(obj)
        lines = ['{']
        for traceback, objs in dict2.items():
            lines.append('')
            for obj in objs:
                lines.append('%s:' % (obj,))
            lines.append(traceback)
        lines.append('}')
        return '\n'.join(lines)

def start_tracking_allocations():
    global TRACK_ALLOCATIONS
    if TRACK_ALLOCATIONS:
        result = ALLOCATED.copy()   # nested start
    else:
        result = None
    TRACK_ALLOCATIONS = True
    ALLOCATED.clear()
    return result

def stop_tracking_allocations(check, prev=None):
    global TRACK_ALLOCATIONS
    assert TRACK_ALLOCATIONS
    for i in range(5):
        if not ALLOCATED:
            break
        gc.collect()
    result = ALLOCATED.copy()
    ALLOCATED.clear()
    if prev is None:
        TRACK_ALLOCATIONS = False
    else:
        ALLOCATED.update(prev)
    if check and result:
        raise MallocMismatch(result)
    return result

def remember_malloc(obj, framedepth=1):
    if TRACK_ALLOCATIONS:
        frame = sys._getframe(framedepth)
        sio = cStringIO.StringIO()
        traceback.print_stack(frame, limit=10, file=sio)
        tb = sio.getvalue()
        ALLOCATED[obj] = tb

def remember_free(obj):
    if TRACK_ALLOCATIONS:
        if obj not in ALLOCATED:
            # rehashing is needed because some objects' hash may change
            # e.g. when lltype objects are turned into <C object>
            items = ALLOCATED.items()
            ALLOCATED.clear()
            ALLOCATED.update(items)
        del ALLOCATED[obj]
