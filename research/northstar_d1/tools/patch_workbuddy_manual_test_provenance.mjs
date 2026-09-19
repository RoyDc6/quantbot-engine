#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const DEFAULT_ASAR = "D:\\workbuddy\\resources\\app.asar";
const EXPECTED_VERSION = "5.3.8";
const ORIGINAL_ASAR_SHA256 =
  "75b9e8559217ce4e0ad2526550a17365231a8ab4eeb86ddfa7c76ec6c4297a00";
const ORIGINAL_INITIALIZE_SHA256 =
  "c8f9b0ade4dd818120812b4709e19eb86459ad6010429839c9631a21565c5461";
const PATCHED_SHAPE =
  "this.persistAndPublish();\n\t\t}\n\t\tthis.storage.persistInProgressRun({";
const ORIGINAL_SHAPE =
  "this.persistAndPublish();\n\t\t\tthis.storage.persistInProgressRun({";

function sha256(data) {
  return crypto.createHash("sha256").update(data).digest("hex");
}

function parseAsar(buffer) {
  if (buffer.length < 16) throw new Error("ASAR file is truncated");
  const headerSize = buffer.readUInt32LE(4);
  const jsonSize = buffer.readUInt32LE(12);
  const headerStart = 16;
  const headerEnd = headerStart + jsonSize;
  const dataBase = 8 + headerSize;
  if (headerEnd > buffer.length || dataBase > buffer.length) {
    throw new Error("ASAR header points outside the file");
  }
  const headerText = buffer.subarray(headerStart, headerEnd).toString("utf8");
  return {
    buffer,
    headerSize,
    jsonSize,
    headerStart,
    headerEnd,
    dataBase,
    tree: JSON.parse(headerText),
  };
}

function getEntry(parsed, entryPath) {
  let node = parsed.tree;
  for (const part of entryPath.split("/")) {
    node = node?.files?.[part];
    if (!node) throw new Error(`ASAR entry is missing: ${entryPath}`);
  }
  if (node.files || node.unpacked) {
    throw new Error(`ASAR entry is not an inline file: ${entryPath}`);
  }
  return node;
}

function readEntry(parsed, entryPath) {
  const entry = getEntry(parsed, entryPath);
  const start = parsed.dataBase + Number(entry.offset || 0);
  const end = start + Number(entry.size || 0);
  if (start < parsed.dataBase || end > parsed.buffer.length) {
    throw new Error(`ASAR entry points outside the file: ${entryPath}`);
  }
  return parsed.buffer.subarray(start, end);
}

function verifyEntryIntegrity(parsed, entryPath) {
  const entry = getEntry(parsed, entryPath);
  const data = readEntry(parsed, entryPath);
  if (!entry.integrity || entry.integrity.algorithm !== "SHA256") {
    throw new Error(`${entryPath} does not have SHA256 integrity metadata`);
  }
  const actualHash = sha256(data);
  if (actualHash !== entry.integrity.hash) {
    throw new Error(`${entryPath} SHA256 does not match its ASAR header`);
  }
  const blockSize = Number(entry.integrity.blockSize);
  const actualBlocks = [];
  for (let offset = 0; offset < data.length; offset += blockSize) {
    actualBlocks.push(sha256(data.subarray(offset, offset + blockSize)));
  }
  if (JSON.stringify(actualBlocks) !== JSON.stringify(entry.integrity.blocks)) {
    throw new Error(`${entryPath} block hashes do not match its ASAR header`);
  }
  return actualHash;
}

function readPackageVersion(parsed) {
  const packageJson = JSON.parse(readEntry(parsed, "package.json").toString("utf8"));
  return String(packageJson.version || "");
}

function inspectAsar(asarPath) {
  const buffer = fs.readFileSync(asarPath);
  const parsed = parseAsar(buffer);
  const version = readPackageVersion(parsed);
  const initialize = readEntry(parsed, "main/initialize.js");
  const initializeHash = verifyEntryIntegrity(parsed, "main/initialize.js");
  const source = initialize.toString("utf8");
  const state = source.includes(PATCHED_SHAPE)
    ? "patched"
    : source.includes(ORIGINAL_SHAPE)
      ? "original"
      : "unknown";
  return {
    buffer,
    parsed,
    version,
    initializeHash,
    asarHash: sha256(buffer),
    state,
  };
}

function patchInitializeSource(source) {
  if (source.includes(PATCHED_SHAPE)) return source;
  if (!source.includes(ORIGINAL_SHAPE)) {
    throw new Error("Expected WorkBuddy startAutomationRun shape was not found");
  }

  const functionStart = source.indexOf("\n\tstartAutomationRun(automation, options) {");
  const functionEnd = source.indexOf(
    "\n\tenqueueAutomationRun(automation, options) {",
    functionStart,
  );
  if (functionStart < 0 || functionEnd < 0) {
    throw new Error("Could not isolate startAutomationRun");
  }

  const originalRegion = source.slice(functionStart, functionEnd);
  const markerStart = originalRegion.indexOf(
    "\t\t\tthis.storage.persistInProgressRun({",
  );
  const oldClose =
    "\n\t\t}\n\t\tthis.schedulePreSessionTimeout(aggregate);";
  const closeAt = originalRegion.indexOf(oldClose, markerStart);
  if (markerStart < 0 || closeAt < 0) {
    throw new Error("Could not isolate the in-progress marker block");
  }

  const markerBlock = originalRegion.slice(markerStart, closeAt);
  const dedentedMarkerBlock = markerBlock.replace(/^\t/gm, "");
  let patchedRegion =
    originalRegion.slice(0, markerStart) +
    "\t\t}\n" +
    dedentedMarkerBlock +
    originalRegion.slice(closeAt + "\n\t\t}".length);

  const originalBytes = Buffer.byteLength(originalRegion, "utf8");
  const patchedBytes = Buffer.byteLength(patchedRegion, "utf8");
  if (patchedBytes > originalBytes) {
    throw new Error("Patched startAutomationRun is larger than its ASAR slot");
  }
  patchedRegion += " ".repeat(originalBytes - patchedBytes);
  const patchedSource =
    source.slice(0, functionStart) + patchedRegion + source.slice(functionEnd);
  if (
    Buffer.byteLength(patchedSource, "utf8") !==
    Buffer.byteLength(source, "utf8")
  ) {
    throw new Error("Patched initialize.js changed byte length");
  }
  if (!patchedSource.includes(PATCHED_SHAPE)) {
    throw new Error("Patched initialize.js does not contain the expected shape");
  }
  new vm.Script(patchedSource, { filename: "main/initialize.js" });
  return patchedSource;
}

function buildPatchedAsar(inputPath, outputPath) {
  const inspected = inspectAsar(inputPath);
  if (inspected.version !== EXPECTED_VERSION) {
    throw new Error(
      `Unsupported WorkBuddy version ${inspected.version}; expected ${EXPECTED_VERSION}`,
    );
  }
  if (inspected.state === "patched") {
    throw new Error("Input ASAR is already patched");
  }
  if (
    inspected.state !== "original" ||
    inspected.asarHash !== ORIGINAL_ASAR_SHA256 ||
    inspected.initializeHash !== ORIGINAL_INITIALIZE_SHA256
  ) {
    throw new Error("Input ASAR is not the reviewed WorkBuddy 5.3.8 build");
  }

  const source = readEntry(inspected.parsed, "main/initialize.js").toString("utf8");
  const patchedSource = patchInitializeSource(source);
  const patchedData = Buffer.from(patchedSource, "utf8");
  const entry = getEntry(inspected.parsed, "main/initialize.js");
  if (patchedData.length !== Number(entry.size)) {
    throw new Error("Patched initialize.js does not fit its ASAR entry");
  }

  entry.integrity.hash = sha256(patchedData);
  entry.integrity.blocks = [];
  const blockSize = Number(entry.integrity.blockSize);
  for (let offset = 0; offset < patchedData.length; offset += blockSize) {
    entry.integrity.blocks.push(
      sha256(patchedData.subarray(offset, offset + blockSize)),
    );
  }

  const headerText = JSON.stringify(inspected.parsed.tree);
  const headerData = Buffer.from(headerText, "utf8");
  if (headerData.length !== inspected.parsed.jsonSize) {
    throw new Error("Updated ASAR header changed byte length");
  }

  const output = Buffer.from(inspected.buffer);
  headerData.copy(output, inspected.parsed.headerStart);
  const entryStart = inspected.parsed.dataBase + Number(entry.offset || 0);
  patchedData.copy(output, entryStart);
  fs.writeFileSync(outputPath, output, { flag: "wx" });

  const verified = inspectAsar(outputPath);
  if (verified.state !== "patched" || verified.version !== EXPECTED_VERSION) {
    throw new Error("Prepared ASAR did not pass post-write verification");
  }
  return verified;
}

function installPreparedAsar(targetPath, preparedPath) {
  const target = inspectAsar(targetPath);
  const prepared = inspectAsar(preparedPath);
  if (
    target.state !== "original" ||
    target.asarHash !== ORIGINAL_ASAR_SHA256 ||
    target.initializeHash !== ORIGINAL_INITIALIZE_SHA256
  ) {
    throw new Error("Install target is not the reviewed original ASAR");
  }
  if (prepared.state !== "patched" || prepared.version !== EXPECTED_VERSION) {
    throw new Error("Prepared ASAR is not a verified patch for WorkBuddy 5.3.8");
  }

  const backupPath = `${targetPath}.northstar-original-${EXPECTED_VERSION}-${ORIGINAL_ASAR_SHA256.slice(0, 12)}`;
  if (fs.existsSync(backupPath)) {
    throw new Error(`Backup already exists; refusing to overwrite: ${backupPath}`);
  }
  fs.renameSync(targetPath, backupPath);
  try {
    fs.renameSync(preparedPath, targetPath);
    const installed = inspectAsar(targetPath);
    if (installed.state !== "patched") {
      throw new Error("Installed ASAR failed verification");
    }
    return { backupPath, installed };
  } catch (error) {
    if (fs.existsSync(targetPath)) {
      fs.renameSync(targetPath, `${targetPath}.northstar-failed-install`);
    }
    fs.renameSync(backupPath, targetPath);
    throw error;
  }
}

function printInspection(asarPath) {
  const inspected = inspectAsar(asarPath);
  console.log(
    JSON.stringify(
      {
        path: path.resolve(asarPath),
        version: inspected.version,
        state: inspected.state,
        asar_sha256: inspected.asarHash,
        initialize_sha256: inspected.initializeHash,
        integrity_verified: true,
      },
      null,
      2,
    ),
  );
}

const [mode = "--check", asarArg, extraArg] = process.argv.slice(2);
const asarPath = path.resolve(asarArg || DEFAULT_ASAR);

if (mode === "--check") {
  printInspection(asarPath);
} else if (mode === "--prepare") {
  const outputPath = path.resolve(extraArg || `${asarPath}.northstar-patched`);
  const result = buildPatchedAsar(asarPath, outputPath);
  console.log(
    JSON.stringify(
      {
        prepared_path: outputPath,
        state: result.state,
        version: result.version,
        asar_sha256: result.asarHash,
        initialize_sha256: result.initializeHash,
        integrity_verified: true,
      },
      null,
      2,
    ),
  );
} else if (mode === "--install") {
  const preparedPath = path.resolve(extraArg || `${asarPath}.northstar-patched`);
  const result = installPreparedAsar(asarPath, preparedPath);
  console.log(
    JSON.stringify(
      {
        installed_path: asarPath,
        backup_path: result.backupPath,
        state: result.installed.state,
        version: result.installed.version,
        asar_sha256: result.installed.asarHash,
        initialize_sha256: result.installed.initializeHash,
        integrity_verified: true,
      },
      null,
      2,
    ),
  );
} else {
  throw new Error(`Unknown mode: ${mode}`);
}
