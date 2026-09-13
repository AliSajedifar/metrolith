// Header comment.
export interface Options {
  marker: string;
}

type Alias = string;

export enum Mode { Fast, Slow }

/* block comment */
export class Service {
  private readonly marker: string;

  constructor(options: Options) {
    this.marker = options.marker;
  }

  get label(): string {
    return this.marker;
  }

  handle(request: string): string {
    return `${this.marker}${request}`; // trailing
  }
}

export function helper(): number {
  const inner = () => 1;
  return inner();
}

export declare function ambient(x: number): number;
