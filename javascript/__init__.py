from . import json, types

JSON = json

import os

if os.getenv('RPY_USE_EMSCRIPTEN') == 'true':
   from rpython.javascript.emscripten import *
