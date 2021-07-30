(function () {
  var copy;
  return function () {
    if (!copy) copy = new Uint8Array(global.Module.wasmMemory.buffer.slice());
    var view = new Uint8Array(global.Module.wasmMemory.buffer);
    view.fill(0);
    view.set(copy);
  }
})();
