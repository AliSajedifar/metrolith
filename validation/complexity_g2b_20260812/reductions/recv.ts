class Holder {
  other!: Holder;
  recvSelf(n: number): number { return this.recvSelf(n); }      // expected 1
  sameName(n: number): number { return this.other.sameName(n); } // other receiver: expected 0
}
export function freeSelf(n: number): number { return freeSelf(n); } // expected 1
