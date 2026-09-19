#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";

function parseAsar(buffer) {
  const headerSize = buffer.readUInt32LE(4);
  const jsonSize = buffer.readUInt32LE(12);
  const headerStart = 16;
  return {
    buffer,
    dataBase: 8 + headerSize,
    tree: JSON.parse(
      buffer.subarray(headerStart, headerStart + jsonSize).toString("utf8"),
    ),
  };
}

function getEntry(parsed, entryPath) {
  let node = parsed.tree;
  for (const part of entryPath.split("/")) {
    node = node?.files?.[part];
    if (!node) throw new Error(`Missing ASAR entry: ${entryPath}`);
  }
  if (node.files || node.unpacked) {
    throw new Error(`Entry is not an inline file: ${entryPath}`);
  }
  return node;
}

const [asarArg, entryArg, outputArg] = process.argv.slice(2);
if (!asarArg || !entryArg || !outputArg) {
  throw new Error("Usage: extract_workbuddy_asar_entry.mjs <asar> <entry> <output>");
}
const asarPath = path.resolve(asarArg);
const outputPath = path.resolve(outputArg);
const parsed = parseAsar(fs.readFileSync(asarPath));
const entry = getEntry(parsed, entryArg);
const start = parsed.dataBase + Number(entry.offset || 0);
const end = start + Number(entry.size || 0);
fs.writeFileSync(outputPath, parsed.buffer.subarray(start, end), { flag: "wx" });
console.log(outputPath);
