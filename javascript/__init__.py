from . import json, types
from .. import typing

JSON = json

import os

if os.getenv('RPY_USE_EMSCRIPTEN') == 'true':
   from rpython.javascript.emscripten import *
   """JSON.toString = toString
   JSON.toStr = toStr
   JSON.toInteger = toInteger
   JSON.toInt = toInt
   JSON.toFloat = toFloat
   JSON.toBoolean = toBoolean
   JSON.toBool = toBool
   JSON.toFunction = toFunction"""
   JSON.fromFunction = fromFunction
   JSON.fromMethod = fromMethod
   typing.Object.get = Object.get
