export function createRequestSequence() {
  let latest = 0;
  return {
    next: () => ++latest,
    isCurrent: (request: number) => request === latest,
    invalidate: (request: number) => {
      if (request === latest) latest += 1;
    },
  };
}
