// Liveness capture: grab a few frames over ~1.5 s (the first one frontal, the others while the
// user performs the requested movement), shrink them and hand them to lib/faceApi.ts.
import type { CameraView } from "expo-camera";
import { ImageManipulator, SaveFormat } from "expo-image-manipulator";
import type { Frame } from "./faceApi";

export const MAX_FRAME_SIDE = 640;
export const JPEG_QUALITY = 0.8;
export const CAPTURE_DURATION_MS = 1500;

/** Scale (w, h) down so the longer side is at most `max`; never scales up. */
export function fitWithin(width: number, height: number, max: number = MAX_FRAME_SIDE) {
  const longest = Math.max(width, height);
  if (!(longest > max)) return { width: Math.round(width), height: Math.round(height) };
  const scale = max / longest;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

/** Moments (ms after the start) at which frames are taken: evenly spread, first at 0. */
export function captureOffsets(count: number, durationMs: number = CAPTURE_DURATION_MS): number[] {
  if (count <= 1) return [0];
  return Array.from({ length: count }, (_, i) => Math.round((i * durationMs) / (count - 1)));
}

/** Resize to <= 640 px on the long side and re-encode as JPEG at ~0.8. */
export async function compressFrame(uri: string, width: number, height: number): Promise<Frame> {
  const target = fitWithin(width, height);
  const context = ImageManipulator.manipulate(uri);
  if (target.width !== width || target.height !== height) context.resize(target);
  const image = await context.renderAsync();
  const result = await image.saveAsync({ format: SaveFormat.JPEG, compress: JPEG_QUALITY });
  return { uri: result.uri };
}

type Raw = { uri: string; width: number; height: number };

/**
 * Take `count` pictures at the offsets above. Pictures are taken first and compressed afterwards
 * so compression time does not stretch the capture window. `onProgress` gets 0..1.
 */
export async function captureFrames(
  camera: CameraView,
  options: { count: number; durationMs?: number; onProgress?: (progress: number) => void }
): Promise<Frame[]> {
  const durationMs = options.durationMs ?? CAPTURE_DURATION_MS;
  const offsets = captureOffsets(options.count, durationMs);
  const startedAt = Date.now();
  const ticker = setInterval(
    () => options.onProgress?.(Math.min(1, (Date.now() - startedAt) / durationMs)),
    50
  );
  const raw: Raw[] = [];
  try {
    for (const offset of offsets) {
      const wait = offset - (Date.now() - startedAt);
      if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
      const picture = await camera.takePictureAsync({ quality: 0.8, exif: false, shutterSound: false });
      if (!picture?.uri) throw new Error("The camera did not return a picture");
      raw.push({ uri: picture.uri, width: picture.width, height: picture.height });
    }
  } finally {
    clearInterval(ticker);
  }
  options.onProgress?.(1);
  return Promise.all(raw.map((r) => compressFrame(r.uri, r.width, r.height)));
}
