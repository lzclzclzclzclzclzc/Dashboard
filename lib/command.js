const { execFile } = require("child_process");
const { promisify } = require("util");

const execute = promisify(execFile);
const controller = new AbortController();

function execFileAsync(file, args, options = {}) {
  if (controller.signal.aborted) {
    const error = new Error("Dashboard is shutting down");
    error.name = "AbortError";
    return Promise.reject(error);
  }
  return execute(file, args, {
    windowsHide: true,
    timeout: 10_000,
    ...options,
    signal: controller.signal,
  });
}

function stopCommands() {
  controller.abort();
}

process.on("exit", stopCommands);

module.exports = { execFileAsync, stopCommands };
