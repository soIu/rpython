def dummy(): pass

function = type(dummy)

NoneType = type(None)

str = str

def string(): return '""'

int = int

def integer(): return '%s' % (0)

float = float

def float(): return repl(0.0)

bool = bool

def boolean(): return 'false'

dict = dict

list = list

tuple = tuple
