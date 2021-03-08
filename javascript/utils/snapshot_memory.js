(function () {
  var copy;
  return function () {
    if (!copy) copy = new Uint32Array(global.Module.wasmMemory.buffer.slice());
    var view = new Uint32Array(global.Module.wasmMemory.buffer);
    view.fill(0);
    view.set(copy);
  }
})();
