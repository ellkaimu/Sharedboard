// ShareBoard editor — Yjs + ProseMirror over a socket.io transport.
//
// Wire protocol (matches pycrdt helpers exactly, byte-for-byte compatible
// with y-websocket/y-protocols):
//
//   message := varuint type
//             | type=0 (SYNC)
//                 varuint subtype  (0=step1, 1=step2, 2=update)
//                 varuint-prefixed payload
//             | type=1 (AWARENESS)
//                 varuint-prefixed payload
//
// The server emits bytes in this format (pycrdt's create_sync_message,
// create_update_message, create_awareness_message). On the client side we
// parse the same way y-protocols/sync and y-protocols/awareness do.

import * as Y from "yjs";
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate } from "y-protocols/awareness";
import { ySyncPlugin, yCursorPlugin, yUndoPlugin, undo, redo } from "y-prosemirror";
import { EditorState, Plugin } from "prosemirror-state";
import { EditorView } from "prosemirror-view";
import { Schema, DOMParser, DOMSerializer } from "prosemirror-model";
import { schema as basicSchema } from "prosemirror-schema-basic";
import { addListNodes } from "prosemirror-schema-list";
import { keymap } from "prosemirror-keymap";
import {
  baseKeymap,
  toggleMark,
  setBlockType,
  wrapIn,
  chainCommands,
  exitCode,
} from "prosemirror-commands";
import { io } from "socket.io-client";

const MSG_SYNC = 0;
const MSG_AWARENESS = 1;
const SYNC_STEP1 = 0;
const SYNC_STEP2 = 1;
const SYNC_UPDATE = 2;

// ---------------------------------------------------------------- y-protocol codec

function readVarUint(view, offset) {
  let num = 0;
  let mult = 1;
  let i = offset;
  while (true) {
    const b = view.getUint8(i++);
    num += (b & 0x7f) * mult;
    if ((b & 0x80) === 0) return [num, i];
    mult *= 128;
  }
}

function writeVarUint(num) {
  const out = [];
  while (num > 0x7f) {
    out.push(0x80 | (num & 0x7f));
    num >>>= 7;
  }
  out.push(num & 0x7f);
  return new Uint8Array(out);
}

function readVarUint8Array(view, offset) {
  const [len, head] = readVarUint(view, offset);
  return [new Uint8Array(view.buffer, view.byteOffset + head, len), head + len];
}

function prependByte(byte, payload) {
  const out = new Uint8Array(payload.byteLength + 1);
  out[0] = byte;
  out.set(payload, 1);
  return out;
}

function makeSyncStep1(stateVector) {
  // type=SYNC, subtype=STEP1, payload=stateVector
  return prependByte(
    MSG_SYNC,
    prependByte(SYNC_STEP1, writeVarUint(stateVector.byteLength).byteLength === 0
      ? new Uint8Array(0)
      : new Uint8Array(0)),
  );
}

// We rely on Yjs + y-protocols to build the actual sync payloads via
// Y.encodeStateVector / Y.encodeStateAsUpdate. The transport just prepends
// the right headers.

function syncStep1Message(doc) {
  const sv = Y.encodeStateVector(doc);
  const svWithLen = encodeLength(sv);
  const payload = new Uint8Array(1 + svWithLen.byteLength);
  payload[0] = SYNC_STEP1;
  payload.set(svWithLen, 1);
  return prependByte(MSG_SYNC, payload);
}

function syncStep2Message(doc, remoteStateVector) {
  const update = Y.encodeStateAsUpdate(doc, remoteStateVector);
  const updateWithLen = encodeLength(update);
  const payload = new Uint8Array(1 + updateWithLen.byteLength);
  payload[0] = SYNC_STEP2;
  payload.set(updateWithLen, 1);
  return prependByte(MSG_SYNC, payload);
}

function syncUpdateMessage(update) {
  const updateWithLen = encodeLength(update);
  const payload = new Uint8Array(1 + updateWithLen.byteLength);
  payload[0] = SYNC_UPDATE;
  payload.set(updateWithLen, 1);
  return prependByte(MSG_SYNC, payload);
}

function awarenessMessage(update) {
  const updateWithLen = encodeLength(update);
  return prependByte(MSG_AWARENESS, updateWithLen);
}

function encodeLength(bytes) {
  // varuint length prefix + bytes
  const len = writeVarUint(bytes.byteLength);
  const out = new Uint8Array(len.byteLength + bytes.byteLength);
  out.set(len, 0);
  out.set(bytes, len.byteLength);
  return out;
}

// ---------------------------------------------------------------- schema & editor

const nodes = addListNodes(basicSchema.spec.nodes, "paragraph block*", "block");
const schema = new Schema({ nodes, marks: basicSchema.spec.marks });

const boardId = new URLSearchParams(location.search).get("board");
if (!boardId) {
  document.body.innerHTML = "<p style='padding:2rem'>Missing board id.</p>";
  throw new Error("missing board id");
}

const userName = (() => {
  const stored = localStorage.getItem("shareboard:name");
  if (stored) return stored;
  const adjectives = ["Quiet", "Brisk", "Calm", "Bold", "Witty", "Swift"];
  const animals = ["Otter", "Fox", "Lynx", "Wren", "Heron", "Sable"];
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
  const name = `${pick(adjectives)} ${pick(animals)}`;
  localStorage.setItem("shareboard:name", name);
  return name;
})();

const userColor = (() => {
  const stored = localStorage.getItem("shareboard:color");
  if (stored) return stored;
  const hue = Math.floor(Math.random() * 360);
  const color = `hsl(${hue}, 70%, 45%)`;
  localStorage.setItem("shareboard:color", color);
  return color;
})();

// ---------------------------------------------------------------- yjs setup

const ydoc = new Y.Doc();
const yXmlFragment = ydoc.getXmlFragment("content");
const awareness = new Awareness(ydoc);
awareness.setLocalStateField("user", { name: userName, color: userColor });

// ---------------------------------------------------------------- socket.io transport

const socket = io({ transports: ["websocket", "polling"] });
const statusEl = document.getElementById("status");
const presenceEl = document.getElementById("presence");

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.classList.remove("connected", "disconnected");
  if (cls) statusEl.classList.add(cls);
}

socket.on("connect", () => {
  setStatus("Connected", "connected");
  // Tell the server which board we want and ask for its state.
  socket.emit("join", { board_id: boardId });
  socket.emit("sync", syncStep1Message(ydoc).buffer);
  // Push our awareness state immediately so peers see our cursor.
  const states = awareness.getStates();
  if (states.size > 0) {
    const update = encodeAwarenessUpdate(awareness, [ydoc.clientID]);
    socket.emit("awareness", awarenessMessage(update).buffer);
  }
});

socket.on("disconnect", () => setStatus("Disconnected", "disconnected"));
socket.on("connect_error", () => setStatus("Connection error", "disconnected"));

socket.on("sync", (data) => {
  const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : new Uint8Array(data);
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const [msgType, offset] = readVarUint(dv, 0);
  if (msgType !== MSG_SYNC) return;
  const [subtype, payloadStart] = readVarUint(dv, offset);
  const [payload, end] = readVarUint8Array(dv, payloadStart);

  if (subtype === SYNC_STEP1) {
    // Server is asking what we have that they don't. Reply with our state.
    socket.emit("sync", syncStep2Message(ydoc, payload).buffer);
  } else if (subtype === SYNC_STEP2 || subtype === SYNC_UPDATE) {
    Y.applyUpdate(ydoc, payload, "remote");
  }
});

socket.on("awareness", (data) => {
  const bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : new Uint8Array(data);
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const [msgType, offset] = readVarUint(dv, 0);
  if (msgType !== MSG_AWARENESS) return;
  const [payload] = readVarUint8Array(dv, offset);
  applyAwarenessUpdate(awareness, payload, "remote");
});

// Local doc updates → broadcast as SYNC_UPDATE (skip ones we just applied
// from the server).
ydoc.on("update", (update, origin) => {
  if (origin === "remote" || origin === "init") return;
  socket.emit("sync", syncUpdateMessage(update).buffer);
});

// Local awareness changes → broadcast
awareness.on("update", ({ added, updated, removed }, origin) => {
  if (origin === "remote") return;
  const changed = added.concat(updated, removed);
  if (changed.length === 0) return;
  const update = encodeAwarenessUpdate(awareness, changed);
  socket.emit("awareness", awarenessMessage(update).buffer);
});

// ---------------------------------------------------------------- presence UI

function renderPresence() {
  presenceEl.innerHTML = "";
  const states = awareness.getStates();
  for (const [clientId, state] of states) {
    if (!state || !state.user) continue;
    if (clientId === ydoc.clientID && states.size > 1) continue; // skip self if others present
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.style.background = state.user.color || "#888";
    dot.title = state.user.name || "Anonymous";
    dot.textContent = (state.user.name || "?").trim().charAt(0).toUpperCase();
    presenceEl.appendChild(dot);
  }
}
awareness.on("change", renderPresence);
renderPresence();

// ---------------------------------------------------------------- prosemirror

function toolbarButton(label, mark, attrs) {
  const btn = document.createElement("button");
  btn.className = "ghost";
  btn.textContent = label;
  btn.addEventListener("mousedown", (e) => {
    e.preventDefault();
    toggleMark(mark, attrs)(view.state, view.dispatch);
    view.focus();
  });
  return btn;
}

function blockButton(label, type, attrs) {
  const btn = document.createElement("button");
  btn.className = "ghost";
  btn.textContent = label;
  btn.addEventListener("mousedown", (e) => {
    e.preventDefault();
    setBlockType(type, attrs)(view.state, view.dispatch);
    view.focus();
  });
  return btn;
}

const editorHost = document.getElementById("editor");

const toolbar = document.createElement("div");
toolbar.style.cssText = "display:flex;gap:6px;padding:8px 0;border-bottom:1px solid var(--border);margin-bottom:12px;flex-wrap:wrap;";
toolbar.appendChild(toolbarButton("B", schema.marks.strong));
toolbar.appendChild(toolbarButton("I", schema.marks.em));
toolbar.appendChild(toolbarButton("</>", schema.marks.code));
toolbar.appendChild(blockButton("H1", schema.nodes.heading, { level: 1 }));
toolbar.appendChild(blockButton("H2", schema.nodes.heading, { level: 2 }));
toolbar.appendChild(blockButton("P", schema.nodes.paragraph));
editorHost.parentNode.insertBefore(toolbar, editorHost);

const state = EditorState.create({
  schema,
  plugins: [
    ySyncPlugin(yXmlFragment),
    yCursorPlugin(yXmlFragment, { awareness }),
    yUndoPlugin(),
    keymap({
      "Mod-z": undo,
      "Mod-y": redo,
      "Mod-Shift-z": redo,
    }),
    keymap(baseKeymap),
  ],
});

const view = new EditorView(editorHost, { state });

// ---------------------------------------------------------------- toolbar buttons

document.getElementById("share-btn").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(location.href);
    const btn = document.getElementById("share-btn");
    const orig = btn.textContent;
    btn.textContent = "Copied!";
    setTimeout(() => (btn.textContent = orig), 1500);
  } catch {
    prompt("Copy this link:", location.href);
  }
});

document.getElementById("rename-btn").addEventListener("click", async () => {
  const cur = document.querySelector(".board-name")?.textContent || "";
  const name = prompt("New board name?", cur);
  if (!name || name === cur) return;
  await fetch(`/api/boards/${boardId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  document.querySelector(".board-name").textContent = name;
});
