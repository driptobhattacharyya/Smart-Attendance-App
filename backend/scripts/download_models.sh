#!/usr/bin/env sh
# Downloads OpenCV's open-source face models (YuNet detector, SFace recogniser)
# from the official opencv_zoo repository into ./models (or $1).
set -eu

DEST="${1:-models}"
BASE="https://github.com/opencv/opencv_zoo/raw/main/models"
mkdir -p "$DEST"

fetch() {
  url="$1"; out="$DEST/$2"
  if [ -s "$out" ] && [ "$(wc -c < "$out")" -gt 100000 ]; then
    echo "already have $out"; return
  fi
  echo "downloading $2"
  curl -fL --retry 3 -o "$out" "$url"
  # A tiny file means we got a Git LFS pointer or an error page, not the model.
  if [ "$(wc -c < "$out")" -lt 100000 ]; then
    echo "error: $out looks wrong (too small)" >&2; rm -f "$out"; exit 1
  fi
}

fetch "$BASE/face_detection_yunet/face_detection_yunet_2023mar.onnx" face_detection_yunet_2023mar.onnx
fetch "$BASE/face_recognition_sface/face_recognition_sface_2021dec.onnx" face_recognition_sface_2021dec.onnx
# ---- optional: MiniFASNet passive anti-spoofing (Silent-Face-Anti-Spoofing, Apache-2.0) ----
# Opt in with FACE_ANTISPOOF=1. UNVERIFIED: these URLs and hashes were written from memory while
# GitHub was unreachable. Before enabling: download manually, confirm the files are MiniFASNetV2
# (crop scale 2.7) and MiniFASNetV1SE (crop scale 4.0) ONNX exports, run the evaluation in
# docs/face-liveness.md, then replace each REPLACE_WITH_SHA256 with the real hash. A placeholder
# hash is refused so a build can never silently ship unchecked weights.
if [ "${FACE_ANTISPOOF:-0}" = "1" ]; then
  AS_BASE="https://github.com/yakhyo/face-anti-spoofing/releases/download/weights"
  verify_sha() {
    out="$DEST/$1"; want="$2"
    case "$want" in
      REPLACE_WITH_SHA256*)
        echo "error: no sha256 pinned for $1 in download_models.sh" >&2; rm -f "$out"; exit 1;;
    esac
    got="$(sha256sum "$out" | cut -d' ' -f1)"
    [ "$got" = "$want" ] || { echo "error: sha256 mismatch for $1 ($got)" >&2; rm -f "$out"; exit 1; }
  }
  fetch "$AS_BASE/MiniFASNetV2.onnx" 2.7_80x80_MiniFASNetV2.onnx
  verify_sha 2.7_80x80_MiniFASNetV2.onnx REPLACE_WITH_SHA256_V2
  fetch "$AS_BASE/MiniFASNetV1SE.onnx" 4_0_0_80x80_MiniFASNetV1SE.onnx
  verify_sha 4_0_0_80x80_MiniFASNetV1SE.onnx REPLACE_WITH_SHA256_V1SE
fi
echo "models ready in $DEST"
