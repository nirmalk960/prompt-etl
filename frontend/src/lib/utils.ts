import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const v = parseInt(
    h.length === 3 ? h.split("").map((c) => c + c).join("") : h,
    16,
  );
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${alpha})`;
}

export function topoSort(nodeIds: string[], edges: { source: string; target: string }[]): string[] {
  const indeg = new Map<string, number>();
  const children = new Map<string, string[]>();
  for (const id of nodeIds) {
    indeg.set(id, 0);
    children.set(id, []);
  }
  for (const e of edges) {
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
    children.get(e.source)?.push(e.target);
  }
  const ready: string[] = [];
  for (const [id, d] of indeg) if (d === 0) ready.push(id);
  ready.sort();
  const out: string[] = [];
  while (ready.length) {
    const id = ready.shift()!;
    out.push(id);
    for (const c of children.get(id) ?? []) {
      indeg.set(c, (indeg.get(c) ?? 0) - 1);
      if (indeg.get(c) === 0) ready.push(c);
    }
    ready.sort();
  }
  return out;
}
