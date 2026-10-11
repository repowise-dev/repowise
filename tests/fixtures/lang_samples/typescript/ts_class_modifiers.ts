export class Service {
  public open(path: string, mode = "r"): number {
    return 0;
  }

  private close(fd: number): void {}

  protected flush(fd: number, ...chunks: Uint8Array[]): void {}

  #reset(hard?: boolean) {}

  static create(options: object): Service {
    return new Service();
  }

  async fetch(url: string): Promise<string> {
    return url;
  }

  private static async retry<T>(fn: () => Promise<T>, attempts: number): Promise<T> {
    return fn();
  }

  public override toString(): string {
    return "Service";
  }

  convert(value: string): number;
  convert(value: number): string;
  convert(value: string | number): string | number {
    return value;
  }

  constructor(private readonly root: string) {}
}

export abstract class Repository<T> {
  protected abstract load(id: string): T;

  protected cache(id: string, entity: T): void {}
}
