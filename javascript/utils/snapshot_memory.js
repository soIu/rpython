(function () {
  var copy = new Uint32Array(global.Module.wasmMemory.buffer.slice());
  return function () {
    var view = new Uint32Array(global.Module.wasmMemory.buffer);
    view.fill(0);
    copy.forEach(function (value, index) {
      view[index] = value;
    });
  }
})();
