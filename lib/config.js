const path = require("path");

module.exports = {
  ROOT: path.join(__dirname, ".."),
  PUBLIC_DIR: path.join(__dirname, "..", "public"),
  DATA_DIR: path.join(__dirname, "..", "data"),
  REPORTS_DIR: path.join(__dirname, "..", "public", "reports"),
  BASELINE_FILE: path.join(__dirname, "..", "data", "deepseek-baseline.json"),
  PORT: Number(process.env.PORT || 3000),
  DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY || "",
  LHM_DATA_URL: process.env.LIBRE_HARDWARE_MONITOR_URL || "http://192.168.18.154:8085/data.json",
  CPU_POWER_SENSOR_ID: process.env.CPU_POWER_SENSOR_ID || "/intelcpu/0/power/0",
  CPU_TEMP_SENSOR_ID: process.env.CPU_TEMP_SENSOR_ID || "/intelcpu/0/temperature/18",
};
