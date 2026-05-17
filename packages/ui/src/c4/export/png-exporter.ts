/**
 * Rasterize an SVG string to PNG via the browser's Image + canvas pipeline.
 * Resolves to a Blob the caller can hand to `triggerDownload`.
 */

import { triggerDownload } from "./svg-exporter";

export interface PngExportOptions {
  /** Pixel ratio multiplier for higher-resolution output. Default 2. */
  scale?: number;
  /** Background fill applied if SVG has transparency. Default null (keep). */
  background?: string | null;
}

export async function svgToPngBlob(
  svg: string,
  options: PngExportOptions = {},
): Promise<Blob> {
  const scale = options.scale ?? 2;
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const img = await loadImage(url);
    const width = img.naturalWidth || 1200;
    const height = img.naturalHeight || 800;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("2D canvas context unavailable");
    if (options.background) {
      ctx.fillStyle = options.background;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    ctx.scale(scale, scale);
    ctx.drawImage(img, 0, 0, width, height);
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (out) => (out ? resolve(out) : reject(new Error("PNG encoding failed"))),
        "image/png",
      );
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

export async function downloadPng(
  svg: string,
  filename: string,
  options?: PngExportOptions,
): Promise<void> {
  const blob = await svgToPngBlob(svg, options);
  triggerDownload(blob, filename);
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Failed to load SVG for rasterization"));
    img.src = src;
  });
}
