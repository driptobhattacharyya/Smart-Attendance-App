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
echo "models ready in $DEST"
