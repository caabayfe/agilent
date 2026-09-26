export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`;
}

export function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function classes(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}

export function newId(): string {
  return crypto.randomUUID().replaceAll("-", "").slice(0, 16);
}
