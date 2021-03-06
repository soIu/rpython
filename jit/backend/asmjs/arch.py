# This backend currently uses the JITFRAME only for transferring arguments
# and return values, not for any sort of scratch space during execution.
# XXX TODO: this probably won't suffice when we consider GC, but good for now.

WORD = 4
FRAME_FIXED_SIZE = 0
JITFRAME_FIXED_SIZE = 0

SANITYCHECK = True
