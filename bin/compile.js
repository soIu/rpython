#!/usr/bin/env node
var os = require('os');
var fs = require('fs');
var path = require('path');
var process = require('process');
var child_process = require('child_process');
var rpython = path.join(__dirname, 'rpython');

function check_exist(command, dont_append_version) {
  try {
    child_process.execSync(command + (dont_append_version ? '' : ' --version'), {stdio: 'ignore'});
    return true;
  }
  catch (error) {
    return false;
  }
}

var python = 'pypy';
if (!check_exist('pypy')) {
  python = 'python2.7';
  if (!check_exist('python2.7')) throw new Error('PyPy (pypy not pypy3) or Python 2.7 (python2.7) must be installed and exist on PATH');
}
if (!check_exist('gcc -v', true)) throw new Error('GCC (gcc) must be installed and exist on PATH');
if (!check_exist('emcc -v', true)) throw new Error('emcc (comes with emsdk) must be installed and exist on PATH');
process.env.USER = 'current';
child_process.execSync([python, rpython, '--gc=none', '-s'].concat(process.argv.slice(2)).join(' '), {stdio: 'inherit', env: process.env});
if (process.argv[2] && process.argv[2].indexOf('.py') !== -1) {
  var file = process.argv[2].split('.py')[0];
  var directory = path.join(os.tmpdir(), 'usession-unknown-current', 'testing_1');
  var makefile = path.join(directory, 'Makefile');
  var make = fs.readFileSync(makefile).toString();
  if (process.argv.indexOf('--use-pthread') === -1) make = make.replace(/-pthread/g, '');
  make = make.replace(/-lutil/g, '');
  make = make.replace('CC = gcc', 'CC = emcc');
  make = make.replace('TARGET = ', 'TARGET = ' + file + '.js #');
  make = make.replace('DEFAULT_TARGET = ', 'DEFAULT_TARGET = ' + file + '.js #');
  fs.writeFileSync(makefile, make);
  var cores = os.cpus().length
  if (!cores) cores = child_process.execSync('nproc').toString().trim();
  child_process.execSync(['make', '-j', cores].join(' '), {env: process.env, stdio: 'inherit', cwd: directory});
  for (var filename of fs.readdirSync(directory)) {
    if (filename.startsWith(file + '.')) fs.copyFileSync(path.join(directory, filename), path.join(process.cwd(), filename));
  }
}
