export function parse(raw: string): string;
export function parse(raw: Uint8Array): Uint8Array;
export function parse(raw: string | Uint8Array): string | Uint8Array {
  return raw;
}

export class Client {
  send(body: string): void;
  send(body: Uint8Array): void;
  send(body: string | Uint8Array): void {
    this.connect();
  }

  private connect(): void;
  private connect(retries: number): void;
  private connect(retries?: number): void {
    return;
  }
}

export interface Transport {
  open(url: string): void;
}

declare function ambient(x: string): void;

declare class Shim {
  run(x: string): void;
}
