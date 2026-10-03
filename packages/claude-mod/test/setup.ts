// The mod runtime has Uint8Array.prototype.toBase64; Node 22 does not yet.
const proto = Uint8Array.prototype as Uint8Array & { toBase64?: () => string };
if (typeof proto.toBase64 !== "function") {
  Object.defineProperty(Uint8Array.prototype, "toBase64", {
    value(this: Uint8Array) {
      return Buffer.from(this.buffer, this.byteOffset, this.byteLength).toString("base64");
    },
  });
}
