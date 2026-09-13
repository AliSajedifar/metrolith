// Header comment.
const marker = "// not a comment";

/* block */
export class Service {
  handle(request) {
    return `${marker}${request}`; // trailing
  }
}

export function helper() {
  return 1;
}
