#!/usr/bin/env node
var os = require('os');
var fs = require('fs');
var path = require('path');
var process = require('process');
var child_process = require('child_process');
var rpython = path.join(__dirname, 'rpython');
var rpydir = path.join(rpython, '../..')
var platform = os.platform();

process.env.RPY_USE_EMSCRIPTEN = 'true';

function check_exist(command, dont_append_version) {
  try {
    child_process.execSync(command + (dont_append_version ? '' : ' --version'), {stdio: 'ignore'});
    return true;
  }
  catch (error) {
    return false;
  }
}

function cygpath(fullpath) {
  return child_process.execSync("cygpath '" + fullpath + "'").toString().trim();
}

if (platform === 'win32') {
  if (!check_exist('cygpath')) {
    throw new Error("Currently the only possible way to compile WASM RPython programs on Windows is with Cygwin, make sure to install python2.7, gcc-core and make on the Cygwin installer. And delete /usr/bin/python on Cygwin (It interferes with emscripten's Python 3)");
  }
  rpython = cygpath(rpython);
}

function deserialize_rpython_json(object) {
  var global = typeof rpyGlobalArg !== "undefined" ? rpyGlobalArg : this;
  if (Array.isArray(object)) object.forEach(function (each, index) {
    if (Array.isArray(each)) deserialize_rpython_json(each);
    else if (each && typeof each === 'object') deserialize_rpython_json(each);
    else if (typeof each === 'string' && each.startsWith('RPYJSOBJECT:') && each.endsWith(':RPYJSOBJECT')) object[index] = global[each.slice(12, -12)];
    else if (each && typeof each === 'object') deserialize_rpython_json(each);
  });
  else if (typeof object === 'string' && object.startsWith('RPYJSOBJECT:') && object.endsWith(':RPYJSOBJECT')) object = global[object.slice(12, -12)];
  else if (object && typeof object === 'object') for (var key in object) {
    var each = object[key];
    if (Array.isArray(each)) deserialize_rpython_json(each);
    else if (typeof each === 'string' && each.startsWith('RPYJSOBJECT:') && each.endsWith(':RPYJSOBJECT')) object[key] = global[each.slice(12, -12)];
    else if (each && typeof each === 'object') deserialize_rpython_json(each);
  }
  return object;
}

function rpythonShrinkToInitial(copy) {
  var newBuffer = new ArrayBuffer(copy.byteLength);
  var newHEAP8 = new Int8Array(newBuffer);
  newHEAP8.set(copy);
  HEAP8 = new Int8Array(newBuffer);
  HEAP16 = new Int16Array(newBuffer);
  HEAP32 = new Int32Array(newBuffer);
  HEAPU8 = new Uint8Array(newBuffer);
  HEAPU16 = new Uint16Array(newBuffer);
  HEAPU32 = new Uint32Array(newBuffer);
  HEAPF32 = new Float32Array(newBuffer);
  HEAPF64 = new Float64Array(newBuffer);
  buffer = newBuffer;
  Module.wasmMemory.buffer = buffer;
  bufferView = HEAPU8;
  updateGlobalBufferAndViews(newBuffer);
}

var use_wasm = process.argv.indexOf('--wasm') !== -1;

var emcc = platform === 'win32' ? 'emcc.bat' : 'emcc';
var python = 'pypy';
if (!check_exist('pypy')) {
  python = 'python2.7';
  if (!check_exist('python2.7')) throw new Error('PyPy (pypy not pypy3) or Python 2.7 (python2.7) must be installed and exist on PATH');
}
if (!check_exist('make -v', true)) throw new Error('make (usually comes from build-essential, or just install the standalone package) must be installed and exist on PATH');
//if (!check_exist('gcc -v', true)) console.error('GCC (gcc) is somewhat needed, but not necessary');
if (!check_exist(emcc + ' -v', true)) throw new Error('emcc (comes with emsdk) must be installed and exist on PATH');
var tempdir = path.join(os.tmpdir(), 'rpython-' + (new Date()).getTime());
fs.mkdirSync(tempdir);
process.env.RPYTHON_TARGET_FILE = process.argv[2];
process.env.PYPY_USESSION_DIR = platform === 'win32' ? cygpath(tempdir) : tempdir;
process.env.USER = 'current';
child_process.execSync([python, rpython, '--gc=none', '--no-translation-jit', '-s'].concat(process.argv.slice(2)).join(' '), {stdio: 'inherit', env: process.env});
if (process.argv[2] && process.argv[2].indexOf('.py') !== -1) {
  var file = process.argv[2].split('.py')[0];
  var directory = path.join(tempdir, 'usession-unknown-0', 'testing_1');
  var makefile = path.join(directory, 'Makefile');
  var make = fs.readFileSync(makefile).toString();
  var debug_flag = process.argv.indexOf('--debug') !== -1;
  var source_flag = process.argv.indexOf('--source-map') !== -1;
  if (process.argv.indexOf('--use-pthread') === -1) make = make.replace(/-pthread/g, '');
  if (platform === 'win32') make = make.replace('RPYDIR = ', 'RPYDIR = "' + rpydir + '"#')
  make = make.replace(/-lutil/g, '');
  make = make.replace(/--export-all-symbols/g, '--export-dynamic');
  make = make.replace('CC = ', 'CC = ' + emcc + (!use_wasm ? ' -s WASM=0 ' : ' ') + '-s ALLOW_MEMORY_GROWTH=1 -s \'EXPORTED_FUNCTIONS=["_main", "_malloc", "_onresolve", "_onfunctioncall"]\' -s \'EXPORTED_RUNTIME_METHODS=["ccall", "wasmMemory"]\'' + (debug_flag ? ' -g3' : (source_flag ? ' -g4' : '')) + ' #');
  make = make.replace('TARGET = ', 'TARGET = ' + file + '.js #');
  make = make.replace('DEFAULT_TARGET = ', 'DEFAULT_TARGET = ' + file + '.js #');
  fs.writeFileSync(makefile, make);
  var cores = process.env.CORE;
  if (!cores) cores = os.cpus().filter(function(cpu) {return cpu.speed}).length;
  if (!cores) cores = child_process.execSync('nproc').toString().trim();
  if (platform === 'darwin') {
    process.env.C_INCLUDE_PATH = path.join(__dirname, '../dmidecode');
  }
  child_process.execSync(['make', '-j', cores].join(' '), {env: process.env, stdio: 'inherit', cwd: directory});
  for (var filename of fs.readdirSync(directory)) {
    if (filename.startsWith(file + '.')) {
      if (use_wasm || filename !== (file + '.js')) fs.copyFileSync(path.join(directory, filename), path.join(process.cwd(), filename));
      else fs.writeFileSync(path.join(process.cwd(), filename), fs.readFileSync(path.join(directory, filename)).toString().replace('bufferView = HEAPU8;', 'bufferView = HEAPU8;\nModule.rpythonShrinkToInitial = ' + rpythonShrinkToInitial.toString()));
    }
  }
  try {
    fs.appendFileSync(path.join(process.cwd(), file + '.js' ), '\n' + deserialize_rpython_json.toString() + '\nModule.wasmMemory = wasmMemory;\nvar rpyGlobalArg = {"Module": Module, "deserialize_rpython_json": deserialize_rpython_json, "get_dirname": function () {return __dirname;}};\nrpyGlobalArg.global = rpyGlobalArg;\n if (typeof window !== "undefined") rpyGlobalArg.window = window;\n if (typeof require !== "undefined") rpyGlobalArg.require = require;\n if (typeof self !== "undefined") rpyGlobalArg.self = self;\n if (typeof global !== "undefined") rpyGlobalArg.node = global;');
    if (source_flag) {
      var source_map = JSON.parse(require('fs').readFileSync(path.join(directory, file + '.wasm.map')));
      source_map.sources.forEach(function (filename, index) {
        var basename = path.basename(filename);
        fs.copyFileSync(path.join(directory, filename), path.join(process.cwd(), basename));
        if (basename !== filename) source_map.sources[index] = basename;
      });
      fs.writeFileSync(path.join(process.cwd(), file + '.wasm.map'), JSON.stringify(source_map));
    }
    if (process.argv.indexOf('--keep-temp') !== -1) process.exit();
    try {
      if (fs.rmSync) fs.rmSync(tempdir, {recursive: true});
      else fs.rmdirSync(tempdir, {recursive: true});
    }
    catch (error) {
      child_process.execSync('rm -rf ' + tempdir);
    }
  }
  catch (error) {
    console.warn(error);
  }
}
