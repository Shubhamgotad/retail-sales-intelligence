export function isValidProductDescription(description: unknown): description is string {
  if (typeof description !== "string") return false;
  const normalized = description.replace(/\s+/gu, "");
  return normalized.toLocaleLowerCase() !== "[missingdescription]" &&
    /[\p{L}\p{N}]/u.test(normalized);
}
