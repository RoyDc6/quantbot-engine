#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const DEFAULT_ASAR = "D:\\workbuddy\\resources\\app.asar";
const ORIGINAL_ASAR_SHA256 =
  "26ce177ef3d909a4071a8d507c4a24bc7667034d354c0c75925fb113859d0840";
const ORIGINAL_5_3_13_ASAR_SHA256 =
  "6a8beda11ab3482985b880ba81d8aaf80f48598dba87a4c088832a06c96fd2e3";
const SUPPORTED_ORIGINAL_ASAR_SHA256 = new Map([
  ["5.3.12", ORIGINAL_ASAR_SHA256],
  ["5.3.13", ORIGINAL_5_3_13_ASAR_SHA256],
]);
const ORIGINAL_INITIALIZE_SHA256 =
  "d381ffbb8694e308e64e2dfc726a877cce4806941b2434e4a671919a18ef20ac";
const ORIGINAL_AUTH_COORDINATOR_SHA256 =
  "bdda564d2e8200bf31ff919dfada65bb9f158f420967673fb271be811a5c0467";
const INITIALIZE_ONLY_ASAR_SHA256 =
  "2c7a6129bdbd92e9f4c717c487e22d4329d3ce2eaccdaa7a6dd675f27f556727";
const INITIALIZE_ONLY_INITIALIZE_SHA256 =
  "3f2a74b1c6a3c2a7baad2113dd310520bcc0f48c7eb3f930ebe701db0a41bfee";
const RESTRICTED_ASAR_SHA256 =
  "34fc4e95d93cfd2371c5f38b822ccc64742a6d032926e5fadb0be5e9839d851e";
const RESTRICTED_AUTH_COORDINATOR_SHA256 =
  "9daabd2f1498e095086e8efd8646c66e9c4228aefd97d6dc9d6492d6adb6b625";
const INITIALIZE_ENTRY = "main/initialize.js";
const AUTH_COORDINATOR_ENTRY = "main/workbuddy-auth-product-coordinator.js";
const ORIGINAL_OUTPUT = "var MAX_OUTPUT_LENGTH = 4e3;";
const PATCHED_OUTPUT = "var MAX_OUTPUT_LENGTH = 2e4;";
const ORIGINAL_MANUAL =
  "this.persistAndPublish();\n\t\t\tthis.storage.persistInProgressRun({";
const PATCHED_MANUAL =
  "this.persistAndPublish();\n\t\t}\n\t\tthis.storage.persistInProgressRun({";
const NO_MEMORY_MARKER = "[WORKBUDDY_NO_MEMORY_V1]";
const AUTH_MEMORY_BYPASS =
  'getAutomationSystemReminder(input.codebuddyMeta)?.includes("[WORKBUDDY_NO_MEMORY_V1]")';
const NORTHSTAR_ZERO_JITTER_MARKER = "NORTHSTAR_ZERO_JITTER_V1";
const NORTHSTAR_AUTOMATION_IDS = [
  "automation-1785736457372",
  "automation-1785736457815",
];

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
  return {
    buffer,
    jsonSize,
    headerStart,
    dataBase,
    tree: JSON.parse(buffer.subarray(headerStart, headerEnd).toString("utf8")),
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
    throw new Error(`${entryPath} has no SHA256 integrity metadata`);
  }
  const actualHash = sha256(data);
  if (actualHash !== entry.integrity.hash) {
    throw new Error(`${entryPath} hash does not match the ASAR header`);
  }
  const blockSize = Number(entry.integrity.blockSize);
  const blocks = [];
  for (let offset = 0; offset < data.length; offset += blockSize) {
    blocks.push(sha256(data.subarray(offset, offset + blockSize)));
  }
  if (JSON.stringify(blocks) !== JSON.stringify(entry.integrity.blocks)) {
    throw new Error(`${entryPath} block hashes do not match the ASAR header`);
  }
  return actualHash;
}

function sourceState(source, authSource) {
  const initializePatched = [
    source.includes(PATCHED_OUTPUT),
    source.includes(PATCHED_MANUAL),
    source.includes(NO_MEMORY_MARKER),
  ].every(Boolean);
  const initializeOriginal = [
    source.includes(ORIGINAL_OUTPUT),
    source.includes(ORIGINAL_MANUAL),
    !source.includes(NO_MEMORY_MARKER),
  ].every(Boolean);
  const authPatched = authSource.includes(AUTH_MEMORY_BYPASS);
  const northstarZeroJitter = source.includes(NORTHSTAR_ZERO_JITTER_MARKER);
  if (initializePatched && authPatched && northstarZeroJitter) {
    return "patched_zero_jitter";
  }
  if (initializePatched && authPatched) return "patched";
  if (initializePatched && !authPatched) return "initialize_only";
  if (initializeOriginal && !authPatched) return "original";
  return "partial_or_unknown";
}

function patchNorthstarJitter(source) {
  if (source.includes(NORTHSTAR_ZERO_JITTER_MARKER)) return source;
  const [hkAutomationId, usAutomationId] = NORTHSTAR_AUTOMATION_IDS;
  const replacement = `var DEFAULT_JITTER_CONFIG={peakHours:[8,9,10],peakJitterMs:600*1e3,nonPeakJitterMs:300*1e3};
var BEIJING_OFFSET_MS=480*60*1e3;
var jitterLogger={info:(msg)=>console.info(\`[automation/jitter] \${msg}\`)};
function deterministicFraction(automationId){const hex=automationId.replace(/-/g,"").slice(0,8).padEnd(8,"0");const fraction=parseInt(hex,16)/4294967296;return Number.isFinite(fraction)?fraction:0}
function getBeijingHour(timestampMs){const beijingMs=timestampMs+new Date(timestampMs).getTimezoneOffset()*60*1e3+BEIJING_OFFSET_MS;return new Date(beijingMs).getHours()}
function isPeakHour(timestampMs,config=DEFAULT_JITTER_CONFIG){const beijingHour=getBeijingHour(timestampMs);return config.peakHours.includes(beijingHour)}
function applyJitter(scheduledTimeMs,automationId,periodMs,config=DEFAULT_JITTER_CONFIG){
if(automationId==="${hkAutomationId}"||automationId==="${usAutomationId}")return scheduledTimeMs;/*${NORTHSTAR_ZERO_JITTER_MARKER}*/
const fraction=deterministicFraction(automationId),signed=(fraction-.5)*2,isPeak=isPeakHour(scheduledTimeMs,config),jitterMs=signed*(isPeak?config.peakJitterMs:config.nonPeakJitterMs),jitteredTime=scheduledTimeMs+jitterMs;
jitterLogger.info(\`id=\${automationId} scheduled=\${new Date(scheduledTimeMs).toISOString()} fraction=\${fraction.toFixed(4)} signed=\${signed.toFixed(4)} isPeak=\${isPeak} jitterMs=\${Math.round(jitterMs)} nextRunAt=\${new Date(jitteredTime).toISOString()}\`);return jitteredTime;
}`;
  const patched = replaceFixedRegion(
    source,
    "var DEFAULT_JITTER_CONFIG = {",
    "\nvar DEFAULT_RECOVERY_JITTER_CONFIG",
    replacement,
  );
  if (!patched.includes(NORTHSTAR_ZERO_JITTER_MARKER)) {
    throw new Error("Northstar zero-jitter guard is missing");
  }
  return patched;
}

function northstarJitterBehaviorValid(source) {
  try {
    const start = source.indexOf("var DEFAULT_JITTER_CONFIG");
    const end = source.indexOf("\nvar DEFAULT_RECOVERY_JITTER_CONFIG", start);
    if (start < 0 || end < 0) return false;
    const context = { console: { info() {} } };
    vm.createContext(context);
    new vm.Script(
      `${source.slice(start, end)}\nthis.__applyJitter = applyJitter;`,
      { filename: "northstar-jitter-check.js" },
    ).runInContext(context);
    const scheduledTimeMs = Date.parse("2026-08-17T16:20:00+08:00");
    const hk = context.__applyJitter(
      scheduledTimeMs,
      NORTHSTAR_AUTOMATION_IDS[0],
      24 * 60 * 60 * 1000,
    );
    const us = context.__applyJitter(
      scheduledTimeMs,
      NORTHSTAR_AUTOMATION_IDS[1],
      24 * 60 * 60 * 1000,
    );
    const unrelated = context.__applyJitter(
      scheduledTimeMs,
      "ffffffff-unrelated",
      24 * 60 * 60 * 1000,
    );
    return hk === scheduledTimeMs && us === scheduledTimeMs && unrelated !== scheduledTimeMs;
  } catch {
    return false;
  }
}

function inspectAsar(asarPath) {
  const buffer = fs.readFileSync(asarPath);
  const parsed = parseAsar(buffer);
  const version = String(
    JSON.parse(readEntry(parsed, "package.json").toString("utf8")).version || "",
  );
  const initialize = readEntry(parsed, INITIALIZE_ENTRY);
  const initializeHash = verifyEntryIntegrity(parsed, INITIALIZE_ENTRY);
  const source = initialize.toString("utf8");
  const authCoordinator = readEntry(parsed, AUTH_COORDINATOR_ENTRY);
  const authCoordinatorHash = verifyEntryIntegrity(parsed, AUTH_COORDINATOR_ENTRY);
  const authSource = authCoordinator.toString("utf8");
  return {
    buffer,
    parsed,
    version,
    initializeHash,
    authCoordinatorHash,
    asarHash: sha256(buffer),
    state: sourceState(source, authSource),
    source,
    authSource,
  };
}

function replaceFixedRegion(source, startToken, endToken, replacement) {
  const start = source.indexOf(startToken);
  const end = source.indexOf(endToken, start);
  if (start < 0 || end < 0) throw new Error(`Could not isolate ${startToken}`);
  const original = source.slice(start, end);
  const originalBytes = Buffer.byteLength(original, "utf8");
  const replacementBytes = Buffer.byteLength(replacement, "utf8");
  if (replacementBytes > originalBytes) {
    throw new Error(
      `Replacement for ${startToken} is larger than its ASAR slot: ${replacementBytes} > ${originalBytes}`,
    );
  }
  return (
    source.slice(0, start) +
    replacement +
    " ".repeat(originalBytes - replacementBytes) +
    source.slice(end)
  );
}

function patchAutomationReminder(source) {
  const replacement = `function buildAutomationExecutionSteps(request, memoryFilePath) {
\tconst prompt = \`\${request.prompt ?? ""}\`;
\tif (prompt.includes("[WORKBUDDY_NO_MEMORY_V1]")) return [
\t\t"NORTHSTAR RESTRICTED MODE: these steps override every earlier memory reminder.",
\t\t"1. Never read, create, edit, or summarize any memory file. Use only current run evidence.",
\t\t"2. Run only commands explicitly allowed by <user_query>; any other shell or file mutation is forbidden.",
\t\t"3. On success run the exact receipt command once. The receipt is the full evidence and audit-table delivery.",
\t\t"4. With [WORKBUDDY_MARKDOWN_ONLY_V1], call present_files exactly once with exactly one receipt .md; never present JSON or manifests.",
\t\t"5. Keep the normal response under 2000 characters. Any memory access or extra attachment is FAILED_CLOSED."
\t];
\tif (shouldUseLeanAutomationFlow(request)) return [
\t\t"Lean execution mode:",
\t\t"1. Prefer one direct action or answer.",
\t\t\`2. Skip \${memoryFilePath} unless prior runs are explicitly required.\`,
\t\t"3. Avoid planning/search unless the direct path fails.",
\t\t\`4. Write a one-line summary to \${memoryFilePath}.\`,
\t\t"5. Present any requested deliverable with present_files."
\t];
\treturn [
\t\t"You MUST follow these steps in order:",
\t\t\`1. Read \${memoryFilePath} for prior outcomes.\`,
\t\t"2. Execute the user query and return its output.",
\t\t"3. Present every deliverable with present_files.",
\t\t\`4. Write a brief summary to \${memoryFilePath}; never store the full deliverable there.\`
\t];
}`;
  return replaceFixedRegion(
    source,
    "function buildAutomationExecutionSteps(request, memoryFilePath) {",
    "\nfunction buildConnectorWarnings",
    replacement,
  );
}

function patchManualRunMarker(source) {
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
  const oldClose = "\n\t\t}\n\t\tthis.schedulePreSessionTimeout(aggregate);";
  const closeAt = originalRegion.indexOf(oldClose, markerStart);
  if (markerStart < 0 || closeAt < 0) {
    throw new Error("Could not isolate the in-progress marker block");
  }
  const markerBlock = originalRegion.slice(markerStart, closeAt);
  const dedentedMarkerBlock = markerBlock.replace(/^\t/gm, "");
  const replacement =
    originalRegion.slice(0, markerStart) +
    "\t\t}\n" +
    dedentedMarkerBlock +
    originalRegion.slice(closeAt + "\n\t\t}".length);
  return replaceFixedRegion(
    source,
    "\n\tstartAutomationRun(automation, options) {",
    "\n\tenqueueAutomationRun(automation, options) {",
    replacement,
  );
}

function patchInitializeSource(source) {
  const initializePatched =
    source.includes(PATCHED_OUTPUT) &&
    source.includes(PATCHED_MANUAL) &&
    source.includes(NO_MEMORY_MARKER);
  const initializeOriginal =
    source.includes(ORIGINAL_OUTPUT) &&
    source.includes(ORIGINAL_MANUAL) &&
    !source.includes(NO_MEMORY_MARKER);
  if (initializePatched) return patchNorthstarJitter(source);
  if (!initializeOriginal) {
    throw new Error("initialize.js is not in the reviewed original state");
  }
  let patched = source.replace(ORIGINAL_OUTPUT, PATCHED_OUTPUT);
  patched = patchAutomationReminder(patched);
  patched = patchManualRunMarker(patched);
  patched = patchNorthstarJitter(patched);
  if (Buffer.byteLength(patched, "utf8") !== Buffer.byteLength(source, "utf8")) {
    throw new Error("Patched initialize.js changed byte length");
  }
  if (
    !patched.includes(PATCHED_OUTPUT) ||
    !patched.includes(PATCHED_MANUAL) ||
    !patched.includes(NO_MEMORY_MARKER) ||
    !patched.includes(NORTHSTAR_ZERO_JITTER_MARKER)
  ) {
    throw new Error("Patched initialize.js is missing an expected feature");
  }
  new vm.Script(patched, { filename: "main/initialize.js" });
  return patched;
}

function patchAuthCoordinatorSource(source) {
  if (source.includes(AUTH_MEMORY_BYPASS)) return source;
  const replacement = `var WorkingMemoryReminderSection=class{stage="every_turn";shouldApply(input){if(isLocalSkillsMemoryDisabled())return false;if(getAutomationSystemReminder(input.codebuddyMeta)?.includes("[WORKBUDDY_NO_MEMORY_V1]"))return false;return!!input.cwd?.trim()}async render(input){const cwd=input.cwd?.trim();if(!cwd)return;const folder=resolveProjectMemoryFolderName();return[
"<memory_and_skills_reminder>",
"The system prompt defines \\"working_memory_files\\" and \\"agent_skills\\". You must strictly follow those rules.",
"",
"Memory:",
\`- After substantive work, first check whether today's \${cwd}/\${folder}/memory/YYYY-MM-DD.md exists; if not, create it. Then append a brief note about what was done. For long-term facts (user preferences, project conventions), write to \${cwd}/\${folder}/memory/MEMORY.md instead.\`,
\`- For cross-project user preferences or personal habits (not project-specific), write to ~/\${folder}/MEMORY.md instead.\`,
"- Skip memory for greetings, simple lookups, and short Q&A.",
"",
"Skills:",
"- After completing a larger multi-step task (15+ tool calls), fixing a tricky error, or discovering a clearly reusable workflow, consider saving the approach as a skill with SkillManage so it can be reused next time.",
"- If you notice issues in a skill (typos, garbled text, wrong tool names, outdated info, etc.), update it with SkillManage when practical and useful in the current turn; otherwise mention the follow-up clearly.",
"- If the work you just did is a repeatable workflow or multi-step process, prefer creating a skill over writing a memory note when it would be genuinely reusable. Skills are actionable; memories are informational.",
"",
"General:",
"- Memory and skills are supplemental — never use them as the primary output. The proper deliverable must be provided in your response or written to the requested file.",
"- Complete all memory/skill writes as part of your tool-call phase, before your final text reply.",
"- Do not mention this reminder to the user.",
"</memory_and_skills_reminder>"
].join("\\n")}};`;
  const patched = replaceFixedRegion(
    source,
    "var WorkingMemoryReminderSection = class {",
    "\n//#endregion",
    replacement,
  );
  if (!patched.includes(AUTH_MEMORY_BYPASS)) {
    throw new Error("Auth coordinator is missing the Northstar memory bypass");
  }
  new vm.Script(patched, { filename: AUTH_COORDINATOR_ENTRY });
  return patched;
}

function buildPatchedAsar(inputPath, outputPath) {
  const inspected = inspectAsar(inputPath);
  const expectedOriginalAsarHash = SUPPORTED_ORIGINAL_ASAR_SHA256.get(inspected.version);
  if (!expectedOriginalAsarHash) {
    throw new Error(`Unsupported WorkBuddy version ${inspected.version}`);
  }
  const reviewedOriginal =
    inspected.state === "original" &&
    inspected.asarHash === expectedOriginalAsarHash &&
    inspected.initializeHash === ORIGINAL_INITIALIZE_SHA256 &&
    inspected.authCoordinatorHash === ORIGINAL_AUTH_COORDINATOR_SHA256;
  const reviewedRestricted =
    inspected.version === "5.3.12" &&
    inspected.state === "patched" &&
    inspected.asarHash === RESTRICTED_ASAR_SHA256 &&
    inspected.initializeHash === INITIALIZE_ONLY_INITIALIZE_SHA256 &&
    inspected.authCoordinatorHash === RESTRICTED_AUTH_COORDINATOR_SHA256;
  if (!reviewedOriginal && !reviewedRestricted) {
    throw new Error("Input is not a reviewed WorkBuddy 5.3.12 source state");
  }
  const patchedEntries = [
    [INITIALIZE_ENTRY, Buffer.from(patchInitializeSource(inspected.source), "utf8")],
    [AUTH_COORDINATOR_ENTRY, Buffer.from(patchAuthCoordinatorSource(inspected.authSource), "utf8")],
  ];
  for (const [entryPath, data] of patchedEntries) {
    const entry = getEntry(inspected.parsed, entryPath);
    if (data.length !== Number(entry.size)) {
      throw new Error(`Patched ${entryPath} does not fit its ASAR entry`);
    }
    entry.integrity.hash = sha256(data);
    entry.integrity.blocks = [];
    const blockSize = Number(entry.integrity.blockSize);
    for (let offset = 0; offset < data.length; offset += blockSize) {
      entry.integrity.blocks.push(sha256(data.subarray(offset, offset + blockSize)));
    }
  }
  const headerData = Buffer.from(JSON.stringify(inspected.parsed.tree), "utf8");
  if (headerData.length !== inspected.parsed.jsonSize) {
    throw new Error("Updated ASAR header changed byte length");
  }
  const output = Buffer.from(inspected.buffer);
  headerData.copy(output, inspected.parsed.headerStart);
  for (const [entryPath, data] of patchedEntries) {
    const entry = getEntry(inspected.parsed, entryPath);
    data.copy(output, inspected.parsed.dataBase + Number(entry.offset || 0));
  }
  fs.writeFileSync(outputPath, output, { flag: "wx" });
  const verified = inspectAsar(outputPath);
  if (
    verified.state !== "patched_zero_jitter" ||
    verified.version !== inspected.version ||
    !northstarJitterBehaviorValid(verified.source)
  ) {
    throw new Error("Prepared ASAR failed verification");
  }
  return verified;
}

function installPreparedAsar(targetPath, preparedPath) {
  const target = inspectAsar(targetPath);
  const prepared = inspectAsar(preparedPath);
  const expectedOriginalAsarHash = SUPPORTED_ORIGINAL_ASAR_SHA256.get(target.version);
  const targetIsOriginal =
    Boolean(expectedOriginalAsarHash) &&
    target.state === "original" &&
    target.asarHash === expectedOriginalAsarHash &&
    target.initializeHash === ORIGINAL_INITIALIZE_SHA256 &&
    target.authCoordinatorHash === ORIGINAL_AUTH_COORDINATOR_SHA256;
  const targetIsInitializeOnly =
    target.version === "5.3.12" &&
    target.state === "initialize_only" &&
    target.asarHash === INITIALIZE_ONLY_ASAR_SHA256 &&
    target.initializeHash === INITIALIZE_ONLY_INITIALIZE_SHA256 &&
    target.authCoordinatorHash === ORIGINAL_AUTH_COORDINATOR_SHA256;
  const targetIsRestricted =
    target.version === "5.3.12" &&
    target.state === "patched" &&
    target.asarHash === RESTRICTED_ASAR_SHA256 &&
    target.initializeHash === INITIALIZE_ONLY_INITIALIZE_SHA256 &&
    target.authCoordinatorHash === RESTRICTED_AUTH_COORDINATOR_SHA256;
  if (!targetIsOriginal && !targetIsInitializeOnly && !targetIsRestricted) {
    throw new Error("Install target is not a reviewed 5.3.12 source state");
  }
  if (
    prepared.state !== "patched_zero_jitter" ||
    prepared.version !== target.version
  ) {
    throw new Error("Prepared ASAR is not a verified matching-version Northstar patch");
  }
  const backupPath = targetIsOriginal
    ? `${targetPath}.northstar-original-${target.version}-${expectedOriginalAsarHash.slice(0, 12)}`
    : targetIsInitializeOnly
      ? `${targetPath}.northstar-initialize-only-${target.version}-${INITIALIZE_ONLY_ASAR_SHA256.slice(0, 12)}`
      : `${targetPath}.northstar-restricted-${target.version}-${RESTRICTED_ASAR_SHA256.slice(0, 12)}`;
  if (fs.existsSync(backupPath)) {
    throw new Error(`Backup already exists: ${backupPath}`);
  }
  fs.renameSync(targetPath, backupPath);
  try {
    fs.renameSync(preparedPath, targetPath);
    const installed = inspectAsar(targetPath);
    if (installed.state !== "patched_zero_jitter") {
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
  console.log(JSON.stringify({
    path: path.resolve(asarPath),
    version: inspected.version,
    state: inspected.state,
    asar_sha256: inspected.asarHash,
    initialize_sha256: inspected.initializeHash,
    auth_coordinator_sha256: inspected.authCoordinatorHash,
    max_output_length: inspected.source.includes(PATCHED_OUTPUT) ? 20000 : 4000,
    northstar_no_memory_branch: inspected.source.includes(NO_MEMORY_MARKER),
    northstar_memory_reminder_omitted: inspected.authSource.includes(AUTH_MEMORY_BYPASS),
    northstar_zero_jitter: inspected.source.includes(NORTHSTAR_ZERO_JITTER_MARKER),
    northstar_zero_jitter_behavior_valid: northstarJitterBehaviorValid(inspected.source),
    manual_test_in_progress_marker: inspected.source.includes(PATCHED_MANUAL),
    integrity_verified: true,
  }, null, 2));
}

const [mode = "--check", asarArg, extraArg] = process.argv.slice(2);
const asarPath = path.resolve(asarArg || DEFAULT_ASAR);
if (mode === "--check") {
  printInspection(asarPath);
} else if (mode === "--prepare") {
  const outputPath = path.resolve(extraArg || `${asarPath}.northstar-patched`);
  const result = buildPatchedAsar(asarPath, outputPath);
  console.log(JSON.stringify({
    prepared_path: outputPath,
    state: result.state,
    version: result.version,
    asar_sha256: result.asarHash,
    initialize_sha256: result.initializeHash,
    auth_coordinator_sha256: result.authCoordinatorHash,
    integrity_verified: true,
  }, null, 2));
} else if (mode === "--install") {
  const preparedPath = path.resolve(extraArg || `${asarPath}.northstar-patched`);
  const result = installPreparedAsar(asarPath, preparedPath);
  console.log(JSON.stringify({
    installed_path: asarPath,
    backup_path: result.backupPath,
    state: result.installed.state,
    version: result.installed.version,
    asar_sha256: result.installed.asarHash,
    initialize_sha256: result.installed.initializeHash,
    auth_coordinator_sha256: result.installed.authCoordinatorHash,
    integrity_verified: true,
  }, null, 2));
} else {
  throw new Error(`Unknown mode: ${mode}`);
}
