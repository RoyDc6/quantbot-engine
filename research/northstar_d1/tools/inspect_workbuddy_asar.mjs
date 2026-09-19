#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

function sha256(data) {
  return crypto.createHash("sha256").update(data).digest("hex");
}

function parseAsar(buffer) {
  const headerSize = buffer.readUInt32LE(4);
  const jsonSize = buffer.readUInt32LE(12);
  const headerStart = 16;
  const dataBase = 8 + headerSize;
  const tree = JSON.parse(
    buffer.subarray(headerStart, headerStart + jsonSize).toString("utf8"),
  );
  return { buffer, headerSize, jsonSize, headerStart, dataBase, tree };
}

function walk(node, prefix = "") {
  const rows = [];
  for (const [name, entry] of Object.entries(node.files || {})) {
    const entryPath = prefix ? `${prefix}/${name}` : name;
    if (entry.files) rows.push(...walk(entry, entryPath));
    else rows.push({ path: entryPath, entry });
  }
  return rows;
}

function readEntry(parsed, entry) {
  if (entry.unpacked) return null;
  const start = parsed.dataBase + Number(entry.offset || 0);
  const end = start + Number(entry.size || 0);
  return parsed.buffer.subarray(start, end);
}

const asarPath = path.resolve(process.argv[2] || "D:/workbuddy/resources/app.asar");
const needles = process.argv.slice(3);
if (!needles.length) {
  needles.push(
    "Read .workbuddy/automations/",
    "Write a brief high-level execution summary",
    "memory_and_skills_reminder",
    "present_files tool",
    "slice(0, 4000)",
    "substring(0, 4000)",
    "output.slice",
  );
}

const buffer = fs.readFileSync(asarPath);
const parsed = parseAsar(buffer);
const matches = [];
for (const row of walk(parsed.tree)) {
  const data = readEntry(parsed, row.entry);
  if (!data || data.length > 20 * 1024 * 1024) continue;
  const text = data.toString("utf8");
  const found = needles.filter((needle) => text.includes(needle));
  if (!found.length) continue;
  const snippets = found.map((needle) => {
    const at = text.indexOf(needle);
    return {
      needle,
      offset: at,
      snippet: text.slice(Math.max(0, at - 500), at + needle.length + 800),
    };
  });
  matches.push({
    path: row.path,
    size: data.length,
    sha256: sha256(data),
    integrity: row.entry.integrity || null,
    snippets,
  });
}

const packageEntry = walk(parsed.tree).find((row) => row.path === "package.json");
const packageJson = packageEntry
  ? JSON.parse(readEntry(parsed, packageEntry.entry).toString("utf8"))
  : {};
console.log(JSON.stringify({
  asarPath,
  asarSize: buffer.length,
  asarSha256: sha256(buffer),
  version: packageJson.version || null,
  matches,
}, null, 2));
